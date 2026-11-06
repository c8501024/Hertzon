"""Coplanar waveguide closed form and the full-wave line model of the circuit simulator (solver/xsection.py)."""
import time

import numpy as np
import pytest

from hertzon.model import circuit as CK
from hertzon.model.circuit import TYPES, Wire
from hertzon.model.circuit import Component as C
from hertzon.model.design import Design, Project, circuit_setup
from hertzon.solver import tlines as TL
from hertzon.solver import xsection as XS
from hertzon.solver.circuit import analyze, cross_sections


def test_coplanar_closed_form():
    z_air, ee_air = TL.cpw_static(1e-3, 0.2e-3, 0.508e-3, 0.0, 3.66, False)
    z_gnd, ee_gnd = TL.cpw_static(1e-3, 0.2e-3, 0.508e-3, 0.0, 3.66, True)
    z_t, _ = TL.cpw_static(1e-3, 0.2e-3, 0.508e-3, 35e-6, 3.66, False)
    assert 1 < ee_air < (3.66 + 1) / 2 < ee_gnd < 3.66      # a ground under the substrate pulls the field in
    assert z_gnd < z_air and z_t < z_air                   # ... and lowers the impedance, like thick metal
    z1, g1 = TL.cpw(1e9, 1e-3, 0.2e-3, 0.508e-3, 35e-6, 3.66, 0.0037, 5.8e7)
    z2, g2 = TL.cpw(20e9, 1e-3, 0.2e-3, 0.508e-3, 35e-6, 3.66, 0.0037, 5.8e7)
    assert (g2.imag / 20) ** 2 > (g1.imag / 1) ** 2         # dispersion: eeff rises with frequency
    assert 0 < g1.real < g2.real


def test_grid_and_interpolation():
    assert XS.grid_fmax(6e9) == 1e10 and XS.grid_fmax(10e9) == 1e10 and XS.grid_fmax(1.1e9) == 2e9
    xs = XS.XSec("MS", 1.1e-3, 0.0, 0.508e-3, 35e-6, 3.66, 0.0037)
    fr = XS.grid(1e10)
    beta = 2 * np.pi * fr * np.sqrt(np.linspace(2.80, 2.90, len(fr))) / TL.C0
    tb = XS.Table(xs, fr, 1e-11 * fr + 1j * beta, np.full(len(fr), 50.0))
    z0, g = tb.at(fr[3], 0.0)
    assert z0 == 50 and g == pytest.approx(complex(1e-11 * fr[3], beta[3]))
    z0, g = tb.at(0.0, 5.8e7)                               # below the grid: held, no loss at DC
    assert g == 0 and z0 == 50
    assert xs.key(1e10) != XS.XSec("MS", 1.2e-3, 0.0, 0.508e-3, 35e-6, 3.66, 0.0037).key(1e10)


def _through(tmp_path, line):
    p = Project()
    p.path = str(tmp_path / "lines.hzn")
    d = Design(name="Net", kind="circuit")
    d.components = [C("Sub1", "SUBST", -200, 200, props=dict(TYPES["SUBST"].props)),
                    C("Port1", "PORT", 0, 40, 0, {"Z0": "50ohm"}), line, C("Port2", "PORT", 160, 40, 0, {"Z0": "50ohm"})]
    d.wires = [Wire([[0, 0], [40, 0]]), Wire([[120, 0], [160, 0]])]
    st = circuit_setup()
    st.sweeps[0].props.update(Distribution="Linear Count", Start="1GHz", Stop="10GHz", Count="10")
    d.setups = [st]
    p.designs.append(d)
    return p, d


def test_full_wave_line_model_against_the_closed_form(tmp_path):
    line = C("MS1", "MLIN", 80, 0, 0, {"Subst": "Sub1", "W": "1.1mm", "L": "20mm"})
    p, d = _through(tmp_path, line)
    f = np.linspace(1e9, 10e9, 10)
    cf = analyze(p, d, f).S
    d.settings[CK.LINE_MODEL] = "Full wave"
    t = time.time()
    fw = analyze(p, d, f).S
    first = time.time() - t
    assert len(list(XS.cache_dir(p.path).glob("*.npz"))) == 1
    # the same line: phase within 2 percent, matched near 50 ohm, only the line loss
    ph_cf, ph_fw = np.unwrap(np.angle(cf[:, 1, 0])), np.unwrap(np.angle(fw[:, 1, 0]))
    assert np.allclose(ph_fw, ph_cf, rtol=0.02)
    assert np.all(np.abs(fw[:, 0, 0]) < 0.1) and np.all(np.abs(fw[:, 1, 0]) > 0.97)
    # the length is applied in the circuit: a longer line needs no new solve
    line.props["L"] = "30mm"
    t = time.time()
    analyze(p, d, f)
    assert time.time() - t < first / 3 and len(list(XS.cache_dir(p.path).glob("*.npz"))) == 1


def test_full_wave_coplanar_line(tmp_path):
    line = C("CPW1", "CPWLIN", 80, 0, 0, {"Subst": "Sub1", "W": "1mm", "S": "0.2mm", "L": "15mm", "Backed": "True"})
    p, d = _through(tmp_path, line)
    assert cross_sections(p, d)["CPW1"].kind == "GCPW"
    d.settings[CK.LINE_MODEL] = "Full wave"
    r = analyze(p, d, np.linspace(1e9, 10e9, 10))
    assert np.all(np.isfinite(r.S)) and np.allclose(r.S, r.S.transpose(0, 2, 1))
    tb = XS.load(cross_sections(p, d)["CPW1"], 1e10, XS.cache_dir(p.path))
    zcf, gcf = TL.cpw(5e9, 1e-3, 0.2e-3, 0.508e-3, 35e-6, 3.66, 0.0037, 5.8e7, True)
    z, g = tb.at(5e9, 5.8e7)
    assert abs(g.imag / gcf.imag - 1) < 0.05 and abs(z / zcf - 1) < 0.2
