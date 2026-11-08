"""Power balance of antennas (#231): Ploss in lossy materials and on lossy boundaries, the radiation efficiency
Prad / (Prad + Ploss), the balance (Prad + Ploss - Pacc) / Pin in the Antenna Parameters and the optional
convergence criteria Max power balance / Max delta efficiency of the setup."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from hertzon.solver import power
from hertzon.solver.ffquant import (BALANCE_WARNING, antenna_parameters, antenna_values, farfield_quantities,
                                    loss_power)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class _Field:
    """A field with a fixed E and H everywhere per excitation, on one tetrahedron."""

    def __init__(self, er, f, fields, sig=0.0, ur=1.0):
        self._der = np.array([er], complex)
        self._dur = np.array([ur], complex)
        self._dsig = np.array([sig], complex)
        self.freq = f
        self.fields = fields
        self.basis = SimpleNamespace(interpolate_index=lambda x, y, z: np.zeros(len(x), np.int64))

    def interpolate(self, x, y, z, usenan=False, mapping=None):
        E, H = self.fields[self.x]
        n = len(x)
        self.Ex, self.Ey, self.Ez = (np.full(n, c, complex) for c in E)
        self.Hx, self.Hy, self.Hz = (np.full(n, c, complex) for c in H)


def _unit_tet():
    nodes = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float) * 1e-2
    mesh = SimpleNamespace(nodes=nodes, tets=np.array([[0], [1], [2], [3]]), tris=np.zeros((3, 0), int))
    return SimpleNamespace(mesh=mesh, mw=SimpleNamespace(bc=SimpleNamespace(boundary_conditions=[])))


def _excite(fld, x):
    fld.x = x


def test_dielectric_and_conduction_loss_of_a_uniform_field():
    m = _unit_tet()
    vol = 1e-6 / 6
    f = 1e9
    w = 2 * np.pi * f
    fields = {0: ((1.0, 0, 0), (0, 0, 0)), 1: ((0, 2j, 0), (0, 0, 0))}
    # e^{+jwt}: a lossy permittivity has a negative imaginary part
    fld = _Field(4 - 0.08j, f, fields, sig=0.01)
    model = power.LossModel(m, fld)
    assert not model.empty
    L = power.loss_matrix(model, fld, [0, 1], _excite)
    p1 = 0.5 * (w * power.EPS0 * 0.08 + 0.01) * vol         # 1/2 (sigma + w eps0 eps'') |E|^2 V
    assert L[0, 0].real == pytest.approx(p1, rel=1e-9)
    assert L[1, 1].real == pytest.approx(4 * p1, rel=1e-9)
    assert abs(L[0, 1]) < 1e-12 * p1                          # orthogonal fields
    assert np.allclose(L, L.conj().T)
    # superposition: a^H L a is the loss of the summed field
    a = np.array([1.0, 0.5j])
    assert float(np.real(a.conj() @ L @ a)) == pytest.approx(p1 * (1 + 1), rel=1e-9)


def test_magnetic_loss_and_lossless_material():
    m = _unit_tet()
    f = 2e9
    fields = {0: ((0, 0, 0), (0, 0, 1.0))}
    fld = _Field(4.0, f, fields, ur=2 - 0.1j)
    L = power.loss_matrix(power.LossModel(m, fld), fld, [0], _excite)
    assert L[0, 0].real == pytest.approx(0.5 * 2 * np.pi * f * power.MU0 * 0.1 / 6e6, rel=1e-9)
    assert power.LossModel(m, _Field(4.0, f, fields)).empty     # nothing lossy: no loss points at all


class SurfaceImpedance:                    # named like the EMerge boundary
    tags = [7]

    def __init__(self, zs):
        self.zs = zs

    def get_gamma(self, k0):
        return 1j * k0 * power.Z0 / self.zs


def test_surface_loss_of_an_impedance_boundary():
    """1/2 Re(1/Zs) |E_t|^2 over the face; the normal E does not count."""
    m = _unit_tet()
    m.mesh.tris = np.array([[0], [1], [2]])                      # the face z = 0, area 0.5e-4 m^2
    m.mesh.get_triangles = lambda tags: [0] if tags == [7] else []
    zs = 0.02 + 0.02j
    m.mw.bc.boundary_conditions = [SurfaceImpedance(zs), SimpleNamespace(tags=[7])]
    fld = _Field(1.0, 1e9, {0: ((3.0, 0, 5.0), (0, 0, 0))})
    model = power.LossModel(m, fld)
    assert model.vol is None and len(model.surf) == 1
    L = power.loss_matrix(model, fld, [0], _excite)
    assert L[0, 0].real == pytest.approx(0.5 * (1 / zs).real * 9 * 0.5e-4, rel=1e-9)


def test_efficiency_and_balance_in_the_antenna_parameters(tmp_path):
    th = np.linspace(0, np.pi, 19)
    ph = np.linspace(0, 2 * np.pi, 37)
    E = np.zeros((3, len(th), len(ph)), complex)
    E[2] = -np.sin(th)[:, None] * 10.0
    ff = {"theta": th, "phi": ph, "E_0_1": E, "Prad_0_1": np.array([0.45]), "freq_0": np.array([1e9])}
    q = farfield_quantities(ff, 1, 0, None, pacc=0.95, ploss=0.49)
    assert q["_eff"][0] == pytest.approx(0.45 / 0.94)
    v = antenna_values(q)
    assert v["LossPower"] == pytest.approx(0.49) and v["PowerBalance"] == pytest.approx(-0.01)
    rows = dict(antenna_parameters(q))
    assert rows["Loss Power"] == "0.49 W" and "-1.00 %" in rows["Power Balance"]
    assert BALANCE_WARNING not in rows["Power Balance"]
    rows = dict(antenna_parameters(farfield_quantities(ff, 1, 0, None, pacc=0.8, ploss=0.1)))
    assert BALANCE_WARNING in rows["Power Balance"]              # 0.45 + 0.1 vs 0.8: -25 %
    # older results without the losses: efficiency Prad / Pacc as before, no balance row
    q = farfield_quantities(ff, 1, 0, None, pacc=0.9)
    assert q["_eff"][0] == pytest.approx(0.5) and "Power Balance" not in dict(antenna_parameters(q))
    assert np.isnan(antenna_values(q)["PowerBalance"])


def test_loss_power_of_port_amplitudes(tmp_path):
    from hertzon.solver.results import ResultStore
    st = ResultStore(tmp_path)
    L = np.array([[0.4, 0.1j], [-0.1j, 0.2]])
    st.save_npz("losses_X.npz", ports=np.array([1, 2]), ok=np.array([1]), freq_0=np.array([1e9]), Ploss_0=L,
                freq_1=np.array([2e9]))
    assert loss_power(st, "X", 1e9, {1: 1.0}) == pytest.approx(0.4)
    a = np.array([1.0, 1j])
    assert loss_power(st, "X", 1e9, {1: 1.0, 2: 1j}) == pytest.approx(float(np.real(a.conj() @ L @ a)))
    assert loss_power(st, "X", 2e9, {1: 1.0}) == 0.0              # lossless at that frequency
    assert loss_power(st, "X", 3e9, {1: 1.0}) is None             # not computed
    assert loss_power(st, "Y", 1e9, {1: 1.0}) is None             # older results
    st.save_npz("losses_Y.npz", ports=np.array([1]), ok=np.array([0]))
    assert loss_power(st, "Y", 1e9, {1: 1.0}) is None             # failed


def test_lossy_patch_conserves_power(tmp_path):
    """FR4 patch: Prad + Ploss = Pacc within ~1 % of the incident power, the losses are about half; the power
    balance criterion of the setup records the balance and the efficiency change of every pass."""
    from hertzon.solver import bridge
    d = json.loads((EXAMPLES / "PatchAntenna.hzn").read_text(encoding="utf-8"))
    design = d["designs"][0]
    d["designs"] = [design]
    setup = design["setups"][0]
    setup["props"].update(MaximumPasses="4", MinimumPasses="4", UsePowerBalance="True")
    for sw in setup.get("sweeps", []):
        sw["props"]["Enabled"] = "False"
    proj = tmp_path / "patch.hzn"
    proj.write_text(json.dumps(d), encoding="utf-8")
    out = tmp_path / "out"
    assert bridge.run(str(proj), design["name"], setup["name"], str(out)) == 0
    h = np.load(out / "huygens_LastAdaptive.npz")
    lo = np.load(out / "losses_LastAdaptive.npz")
    S = np.load(out / "sparams_LastAdaptive.npz")["S"]
    prad, ploss = float(h["Prad_0_1"][0]), float(np.real(lo["Ploss_0"][0, 0]))
    pacc = 1 - abs(S.ravel()[0]) ** 2
    assert 0.3 < ploss < 0.7
    assert abs(prad + ploss - pacc) < 0.015
    conv = json.loads((out / "convergence.json").read_text())
    rows = conv["passes"]
    assert all("power_balance" in r for r in rows) and all("delta_eff" in r for r in rows[1:])
    assert rows[-1]["power_balance"] < 0.015
    assert conv["max_power_balance"] == 0.01
    vals = json.loads((out / "antenna_passes.json").read_text())
    v = next(iter(vals.values()))[-1]["values"][0]
    assert v["RadiationEfficiency"] == pytest.approx(prad / (prad + ploss), rel=1e-6)
    assert abs(v["PowerBalance"]) < 0.015


def test_conductor_solids_are_not_volume_losses():
    """A tetrahedron of a conductor solid (copper): EMerge solves no field inside, its loss is the surface
    impedance on its faces. sigma |E|^2 of the interpolated surface field gave Ploss of 100x the input power."""
    m = _unit_tet()
    fld = _Field(1.0, 1e9, {0: ((1.0, 0, 0), (0, 0, 0))}, sig=5.8e7)
    assert power.LossModel(m, fld).empty
    fld = _Field(1.0, 1e9, {0: ((1.0, 0, 0), (0, 0, 0))}, sig=100.0)       # a lossy dielectric still counts
    assert not power.LossModel(m, fld).empty


def test_patch_with_a_copper_ground_solid_conserves_power(tmp_path):
    """The FR4 patch with its ground as a 0.2 mm copper solid: Prad + Ploss = Pacc within ~2 % (it was
    Ploss = 171 W for 1 W incident)."""
    from hertzon.solver import bridge
    d = json.loads((EXAMPLES / "PatchAntenna.hzn").read_text(encoding="utf-8"))
    design = d["designs"][0]
    d["designs"] = [design]
    for o in design["objects"]:
        if o["name"] == "Ground":
            o["history"][0] = {**o["history"][0], "kind": "CreateBox",
                               "props": {"Position": "-Ws/2,-Ls/2,-0.2mm", "XSize": "Ws", "YSize": "Ls",
                                         "ZSize": "0.2mm"}}
            o["material"] = "copper"
    design["boundaries"] = [b for b in design["boundaries"] if b["name"] != "PerfE_Ground"]
    setup = design["setups"][0]
    setup["props"].update(MaximumPasses="4", MinimumPasses="4")
    for sw in setup.get("sweeps", []):
        sw["props"]["Enabled"] = "False"
    proj = tmp_path / "patch_cu.hzn"
    proj.write_text(json.dumps(d), encoding="utf-8")
    out = tmp_path / "out"
    assert bridge.run(str(proj), design["name"], setup["name"], str(out)) == 0
    h = np.load(out / "huygens_LastAdaptive.npz")
    lo = np.load(out / "losses_LastAdaptive.npz")
    S = np.load(out / "sparams_LastAdaptive.npz")["S"]
    prad, ploss = float(h["Prad_0_1"][0]), float(np.real(lo["Ploss_0"][0, 0]))
    pacc = 1 - abs(S.ravel()[0]) ** 2
    assert 0.3 < ploss < 0.7
    assert abs(prad + ploss - pacc) < 0.025


class ThinConductor(SurfaceImpedance):     # named like the EMerge boundary: a separate field on each side
    pass


def test_thin_conductor_loss_counts_both_sides():
    """A two-sided sheet (EMerge ThinConductor splits the DoFs of the sheet): each side has its own tangential
    E and dissipates 1/2 Re(1/Zs) |E_t|^2; the loss is the sum of both. One interpolation at the face saw one
    side only."""
    nodes = np.array([[0, 1, 0, 0, 0], [0, 0, 1, 0, 0], [0, 0, 0, 1, -1]], float) * 1e-2
    tets = np.array([[0, 0], [1, 1], [2, 2], [3, 4]])               # tet 0 above z = 0, tet 1 below
    mesh = SimpleNamespace(nodes=nodes, tets=tets, tris=np.array([[0], [1], [2]]),
                           tri_to_tet=np.array([[0], [1]]), get_triangles=lambda tags: [0] if tags == [7] else [])
    zs = 0.02 + 0.02j
    for kind, expect in ((ThinConductor, 9 + 16), (SurfaceImpedance, None)):
        m = SimpleNamespace(mesh=mesh, mw=SimpleNamespace(bc=SimpleNamespace(boundary_conditions=[kind(zs)])))

        class _Sided(_Field):                  # E_x = 3 above the sheet, E_y = 4 below
            def interpolate(self, x, y, z, usenan=False, mapping=None):
                super().interpolate(x, y, z, usenan, mapping)
                side = np.asarray(mapping if mapping is not None else np.zeros(len(x), int))
                self.Ex = np.where(side == 0, 3.0, 0).astype(complex)
                self.Ey = np.where(side == 1, 4.0, 0).astype(complex)

        fld = _Sided(1.0, 1e9, {0: ((0, 0, 0), (0, 0, 0))})
        model = power.LossModel(m, fld)
        L = power.loss_matrix(model, fld, [0], _excite)
        p = 0.5 * (1 / zs).real * 0.5e-4
        if expect is None:
            assert len(model.surf) == 1                     # one-sided: the field is continuous, one side
        else:
            assert len(model.surf) == 2
            assert L[0, 0].real == pytest.approx(p * expect, rel=1e-9)
            # the points are nudged into their tetrahedron
            assert np.all(model.surf[0]["pts"][2] > 0) and np.all(model.surf[1]["pts"][2] < 0)


def _strip_dipole(tmp_path, variant: str, sigma: float = 2e4):
    """Dipole of two 4 x 70 mm strips at 1 GHz, conductivity sigma: Finite Conductivity sheets ('one'- or
    'two'-sided) or 0.2 mm thick conductor solids ('solid', surface impedance on all faces). Returns
    Prad, Ploss, Pacc."""
    from hertzon.solver import bridge
    d = json.loads((EXAMPLES / "Dipole.hzn").read_text(encoding="utf-8"))
    de = d["designs"][0]
    d["designs"] = [de]
    de["variables"].update(w="4mm", t="0.2mm")
    for o in de["objects"]:
        if o["name"] in ("ArmTop", "ArmBottom"):
            z0, sz = ("g/2", "Larm") if o["name"] == "ArmTop" else ("-g/2", "-Larm")
            if variant == "solid":
                o["history"] = [{"kind": "CreateBox", "props": {"Position": f"-w/2,-t/2,{z0}", "XSize": "w",
                                                                 "YSize": "t", "ZSize": sz}}]
                o["material"] = "lossy"
            else:
                o["history"] = [{"kind": "CreateRectangle", "props": {"Position": f"-w/2,0,{z0}", "Axis": "Y",
                                                                       "XSize": "w", "YSize": "1", "ZSize": sz}}]
                o["material"] = "vacuum"
        if o["name"] == "Gap":
            o["history"][0]["props"].update(Position="-w/2,0,-g/2", XSize="w")
    d["materials"] = [{"name": "lossy", "conductivity": str(sigma)}]
    if variant != "solid":
        de["boundaries"] = [{"name": "FC", "kind": "Finite Conductivity", "faces": [], "edges": [], "enabled": True,
                             "objects": ["ArmTop", "ArmBottom"],
                             "props": {"UseMaterial": "False", "Conductivity": str(sigma),
                                       "TwoSided": "True" if variant == "two" else "False"}}]
    setup = de["setups"][0]
    setup["props"].update(MaximumPasses="5", MinimumPasses="5")
    for sw in setup["sweeps"]:
        sw["props"]["Enabled"] = "False"
    proj = tmp_path / f"strip_{variant}.hzn"
    proj.write_text(json.dumps(d), encoding="utf-8")
    out = tmp_path / f"out_{variant}"
    assert bridge.run(str(proj), de["name"], setup["name"], str(out)) == 0
    h = np.load(out / "huygens_LastAdaptive.npz")
    lo = np.load(out / "losses_LastAdaptive.npz")
    S = np.load(out / "sparams_LastAdaptive.npz")["S"].ravel()[0]
    return float(h["Prad_0_1"][0]), float(np.real(lo["Ploss_0"][0, 0])), 1 - abs(S) ** 2


def test_two_sided_sheet_matches_a_conductor_solid(tmp_path):
    """Lossy strip dipole (sigma = 2e4 S/m): the two-sided sheet dissipates what a thin conductor solid does
    (both sides carry current), and Prad + Ploss = Pacc. The one-sided sheet puts the current of both sides
    through one surface impedance: about twice the loss. Measured: one-sided eff 0.860 / Ploss 0.135, two-sided
    0.919 / 0.080 (0.040 and a balance of -4.3 % while one side was counted), solid 0.922 / 0.074."""
    p2, l2, a2 = _strip_dipole(tmp_path, "two")
    ps, ls, a_s = _strip_dipole(tmp_path, "solid")
    p1, l1, a1 = _strip_dipole(tmp_path, "one")
    for p, l, a in ((p2, l2, a2), (ps, ls, a_s), (p1, l1, a1)):
        assert abs(p + l - a) < 0.015                              # power balance
    assert l2 == pytest.approx(ls, rel=0.2)
    assert p2 / (p2 + l2) == pytest.approx(ps / (ps + ls), abs=0.01)
    assert 1.5 < l1 / l2 < 2.2
