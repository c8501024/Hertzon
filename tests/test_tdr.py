import numpy as np
import pytest

from hertzon.solver import tdr


def line_sparams(f, z1, tau, z0=50.0):
    """S of a lossless line (impedance z1, delay tau) between two z0 ports."""
    th = 2 * np.pi * f * tau
    A, B = np.cos(th), 1j * z1 * np.sin(th)
    C, D = 1j * np.sin(th) / z1, np.cos(th)
    den = A + B / z0 + C * z0 + D
    S = np.empty((len(f), 2, 2), complex)
    S[:, 0, 0] = (A + B / z0 - C * z0 - D) / den
    S[:, 1, 1] = (-A + B / z0 - C * z0 + D) / den
    S[:, 0, 1] = S[:, 1, 0] = 2 / den
    return S


def test_tdr_impedance_and_delay_of_a_line():
    f = np.linspace(10e6, 20e9, 401)
    data = {"freq": f, "S": line_sparams(f, 75.0, 0.5e-9), "z0": np.array([50.0, 50.0])}
    t, z, info = tdr.eval_time(data, "TDRZ(1)", tdr.Options(t_max=2e-9))
    mid = (t > 0.3e-9) & (t < 0.7e-9)            # inside the line (round trip 1 ns)
    assert np.allclose(z[mid], 75.0, atol=1.0)
    late = t > 1.4e-9                             # matched port behind the line
    assert np.allclose(z[late], 50.0, atol=1.0)
    assert info["lowpass_ok"]
    t, y, _ = tdr.eval_time(data, "Step(S(2,1))", tdr.Options(t_max=2e-9))
    assert abs(t[np.argmax(y > 0.5 * y[-1])] - 0.5e-9) < 0.05e-9    # arrival after one delay


def test_sweep_starting_too_high_is_flagged():
    f = np.linspace(200e6, 20e9, 401)              # S11 turns by 72 degrees below the first point
    data = {"freq": f, "S": line_sparams(f, 75.0, 0.5e-9), "z0": np.array([50.0, 50.0])}
    assert not tdr.eval_time(data, "TDRZ(1)")[2]["lowpass_ok"]
    assert tdr.eval_time(data, "ImpulseBandPass(S(1,1))")[2]["lowpass_ok"]


def test_dc_extrapolation_is_real_and_continuous():
    f = np.linspace(0.2e9, 10e9, 200)
    s = line_sparams(f, 60.0, 0.2e-9)[:, 0, 0]
    fh, sh, gap = tdr.lowpass_data(f, s)
    assert fh[0] == 0 and sh[0].imag == 0
    exact = line_sparams(fh[1:], 60.0, 0.2e-9)[:, 0, 0]
    assert np.abs(sh[1:] - exact).max() < 1e-3
    assert gap == pytest.approx(0.02)


def test_parse_and_errors():
    assert tdr.parse("TDRZ(2)") == ("TDRZ", 1, 1)
    assert tdr.parse("Impulse(S(2,1))") == ("Impulse", 1, 0)
    with pytest.raises(ValueError):
        tdr.parse("TDRZ(S(2,1))")
    with pytest.raises(ValueError):
        tdr.parse("dB(S(1,1))")


def test_bandpass_envelope_peaks_at_the_delay():
    f = np.linspace(2e9, 6e9, 201)
    s = line_sparams(f, 50.0, 0.8e-9)[:, 1, 0]      # matched line: pure delay
    t, y = tdr.bandpass_impulse(f, s)
    assert abs(t[np.argmax(y)] - 0.8e-9) < 0.05e-9
    assert y.max() == pytest.approx(1.0, rel=0.05)


def test_time_domain_report_window(tmp_path):
    """The 'Time Domain' report draws TDRZ and step traces from a stored sweep."""
    import os
    import shutil
    from pathlib import Path
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.model.design import Project, Report
    from hertzon.gui.doc import Document, result_dir
    from hertzon.gui.reports import ReportWindow
    from hertzon.solver.results import ResultStore
    app = QApplication.instance() or QApplication([])  # noqa: F841
    src = Path(__file__).resolve().parents[1] / "examples" / "Microstrip.hzn"
    shutil.copy(src, tmp_path / "Microstrip.hzn")
    doc = Document(Project.load(tmp_path / "Microstrip.hzn"))
    f = np.linspace(10e6, 20e9, 401)
    st = ResultStore(result_dir(doc, "Setup1"))
    ports = [{"name": "1", "number": 1, "kind": "Lumped Port"}, {"name": "2", "number": 2, "kind": "Lumped Port"}]
    st.save_sparams("Sweep", f, line_sparams(f, 75.0, 0.5e-9), np.array([50.0, 50.0]), ports)
    r = Report("Time Domain 1", "Time Domain", "Setup1 : Sweep", ["TDRZ(1)", "Step(S(2,1))"],
               {"tdr_tmax_ns": "2"})
    w = ReportWindow(doc, r)
    assert "Low-pass transform" in w.info.text() and "too high" not in w.info.text()
    lines = w.fig.axes[0].get_lines()
    xs, ys = lines[0].get_data()
    assert xs.max() <= 2.0 + 1e-9 and np.interp(0.5, xs, ys) == pytest.approx(75.0, abs=1.0)
    w.cat.setCurrentText("Impulse Response")
    assert w.qty.itemText(0) == "Impulse(S(1,1))"
    xs, _, cols = w._columns()
    assert len(cols) == 2 and len(cols[0][1]) == len(xs)
