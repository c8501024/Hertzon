"""Near Field Line: the report functions mag, ang, re, im (re/im of X e^(jφ), the phase slider), the phase
slider only for quantities with a phase reference, old reports with envelope/inst, and the skin depth fit."""
import os
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from hertzon.gui.reports import near_values, skin_depth_fit
from hertzon.post.quantities import (NF_FUNCS, functions_for, migrate_near_report, migrate_near_trace,
                                     phasor_value, quantity_meta)

EX = Path(__file__).resolve().parents[1] / "examples"


def _nf(c):
    return {"NearEX": c[:, 0], "NearEY": c[:, 1], "NearEZ": c[:, 2],
            "NearETotal": np.sqrt((np.abs(c) ** 2).sum(axis=1))}


def test_functions_are_mag_ang_re_im():
    assert "envelope" not in NF_FUNCS and "inst" not in NF_FUNCS
    assert set(functions_for("NearEX")) - {"dB"} == {"mag", "ang_deg", "re", "im"}
    assert functions_for("NearHZ") == functions_for("NearEX")
    assert functions_for("NearETotal") == ["mag", "dB"]          # a magnitude: no re, im, ang
    assert functions_for("NearHTotal") == ["mag", "dB"]


def test_re_im_follow_the_phase():
    nf = {"NearEX": np.array([1.0 + 0j])}
    re_ = [near_values(nf, "re", "NearEX", p)[0] for p in (0, 90, 180)]
    im_ = [near_values(nf, "im", "NearEX", p)[0] for p in (0, 90, 180)]
    assert re_ == pytest.approx([1, 0, -1], abs=1e-12)
    assert im_ == pytest.approx([0, 1, 0], abs=1e-12)


def test_mag_and_ang_do_not_depend_on_the_phase():
    c = np.array([[1.0 + 1j, 0, 0], [0, 2j, 0.5]])
    nf = _nf(c)
    for f in ("mag", "ang_deg", "dB"):
        for q in ("NearEX", "NearEY"):
            ref = near_values(nf, f, q, 0)
            for p in (37, 90, 180, 300):
                assert np.allclose(near_values(nf, f, q, p), ref, atol=1e-12)
    assert near_values(nf, "mag", "NearETotal", 77) == pytest.approx(near_values(nf, "mag", "NearETotal", 0))
    with pytest.raises(ValueError):
        near_values(nf, "re", "NearETotal", 0)


def test_re_is_the_instantaneous_value():
    c = np.array([[1.0 + 1j, 0, 0], [0, 2j, 0]])
    nf = _nf(c)
    for p in (0, 45, 90, 200):
        assert near_values(nf, "re", "NearEX", p) == pytest.approx(np.real(c[:, 0] * np.exp(1j * np.radians(p))))


def test_quantity_metadata():
    for q in ("NearEX", "NearEY", "NearEZ", "NearHX", "NearHY", "NearHZ"):
        assert quantity_meta(q).has_phase_reference and not quantity_meta(q).is_magnitude
    for q in ("NearETotal", "NearHTotal", "VSWR(1)"):
        assert quantity_meta(q).is_magnitude and not quantity_meta(q).has_phase_reference
    for q in ("S(1,1)", "S(2,1)", "Z(1,1)", "Y(1,2)", "Zo(1)", "Gamma(1)"):
        assert not quantity_meta(q).has_phase_reference and not quantity_meta(q).is_magnitude


def test_network_quantities_ignore_the_phase():
    s11 = np.array([0.3 + 0.4j, -0.1 + 0.2j])
    meta = quantity_meta("S(1,1)")
    for f in ("re", "im", "mag", "ang_deg"):
        ref = phasor_value(f, s11, 0, meta)
        for p in (30, 90, 180):
            assert np.array_equal(phasor_value(f, s11, p, meta), ref)
    assert phasor_value("re", s11, 90, meta) == pytest.approx(np.real(s11))


def test_old_traces_are_migrated():
    assert migrate_near_trace("envelope(NearEX)")[0] == "mag(NearEX)"
    assert migrate_near_trace("inst(NearHY)")[0] == "re(NearHY)"
    assert migrate_near_trace("inst(NearETotal)")[0] == "mag(NearETotal)"
    assert migrate_near_trace("envelope(NearETotal)")[0] == "mag(NearETotal)"
    assert migrate_near_trace("re(NearETotal)")[0] == "mag(NearETotal)"
    assert migrate_near_trace("re(NearEX)") == ("re(NearEX)", None)
    assert migrate_near_trace("mag(NearETotal)") == ("mag(NearETotal)", None)
    assert migrate_near_trace("envelope(NearEX)")[1]


def test_old_report_loads_with_warnings(caplog):
    from hertzon.model.design import Project, Report, design_from_dict
    d = Project.load(EX / "PatchAntenna.hzn").to_dict()["designs"][0]
    r = Report("NF", "Near Field Line", "Setup1 : LastAdaptive",
               ["envelope(NearETotal)", "inst(NearEX)", "envelope(NearEY)", "mag(NearEY)", "dB(NearHZ)"],
               {"phase": "45", "skin": "envelope(NearEY)|1,2"})
    d["reports"] = [asdict(r), asdict(Report("S", "Rectangular", "Setup1 : Sweep", ["re(S(1,1))"]))]
    with caplog.at_level("WARNING"):
        des = design_from_dict(d)
    nf, s = des.reports
    assert nf.traces == ["mag(NearETotal)", "re(NearEX)", "mag(NearEY)", "dB(NearHZ)"]
    assert nf.props["skin"] == "mag(NearEY)|1,2"
    assert len(nf.migration_warnings) == 3
    assert "envelope(NearETotal)" in caplog.text
    assert "migration_warnings" not in asdict(nf)           # not saved with the project
    assert s.traces == ["re(S(1,1))"]                      # other reports unchanged
    assert migrate_near_report(s) == []


def _report_window(tmp_path, report):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.doc import Document
    from hertzon.gui.reports import ReportWindow
    from hertzon.model.design import Project
    app = QApplication.instance() or QApplication([])
    proj = Project.load(EX / "PatchAntenna.hzn")
    proj.save(tmp_path / "p.hzn")
    doc = Document(proj)
    w = ReportWindow(doc, report)
    w.show()
    app.processEvents()
    return w, app


def test_phase_slider_only_with_a_phase_reference(tmp_path):
    from hertzon.model.design import Report
    # E Total: no slider, only mag (and dB)
    w, app = _report_window(tmp_path, Report("NF", "Near Field Line", "Setup1 : LastAdaptive", ["mag(NearETotal)"]))
    w.qty.setCurrentText("NearETotal")
    app.processEvents()
    assert not w.nf_phase_row.isVisible() and not w.nf_phase_label.isVisible()
    assert [w.func.itemText(i) for i in range(w.func.count())] == ["mag", "dB"]
    # changing the quantity shows the slider at once, with re/im/ang
    w.qty.setCurrentText("NearEX")
    app.processEvents()
    assert w.nf_phase_row.isVisible()
    assert {"mag", "re", "im", "ang_deg"} <= {w.func.itemText(i) for i in range(w.func.count())}
    w.qty.setCurrentText("NearETotal")
    app.processEvents()
    assert not w.nf_phase_row.isVisible()
    # a mixed report: the slider is shown while one trace has a phase reference, and acts on that one only
    w.report.traces = ["mag(NearETotal)", "re(NearEX)"]
    w.refresh()
    app.processEvents()
    assert w.nf_phase_row.isVisible()
    assert w._near_label("re(NearEX)").startswith("re(NearEX) @ ")
    assert w._near_label("mag(NearETotal)") == "mag(NearETotal)"


def test_old_near_report_opens_with_a_warning(tmp_path):
    from hertzon.model.design import Report
    w, app = _report_window(tmp_path, Report("NF", "Near Field Line", "Setup1 : LastAdaptive",
                                             ["envelope(NearEX)", "inst(NearEX)"]))
    assert w.report.traces == ["mag(NearEX)", "re(NearEX)"]
    assert "envelope(NearEX)" in w.info.text()


def test_s_parameter_report_has_no_phase_slider(tmp_path):
    from hertzon.model.design import Report
    w, app = _report_window(tmp_path, Report("S11", "Rectangular", "Setup1 : Sweep", ["re(S(1,1))"],
                                             {"phase": "90"}))
    assert getattr(w, "nf_phase_row", None) is None
    assert getattr(w, "nf_phase", None) is None


def test_skin_depth_fit():
    x = np.linspace(0, 50, 401)
    delta = 3.7
    # free space with a standing wave up to x = 20, then exponential decay in the wall
    y = np.where(x < 20, 1.0 + 0.3 * np.cos(x), (1.0 + 0.3 * np.cos(20)) * np.exp(-(x - 20) / delta))
    fit = skin_depth_fit(x, y, 20, 50)
    assert fit["delta"] == pytest.approx(delta, rel=1e-3)
    assert fit["r2"] > 0.999
    assert fit["start"] == pytest.approx(20, abs=0.2)
    # the floor 60 dB down is not fitted
    y2 = np.maximum(y, 1e-7)
    assert skin_depth_fit(x, y2, 20, 50)["delta"] == pytest.approx(delta, rel=1e-3)
    # decay towards smaller x
    fit = skin_depth_fit(-x, y, -50, -20)
    assert fit["delta"] == pytest.approx(delta, rel=1e-3)
    with pytest.raises(ValueError):
        skin_depth_fit(x, np.ones_like(x))
