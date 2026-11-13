"""Create the example LNA_2G4.hzn: a 2.4 GHz low-noise amplifier that can be built as drawn.

python examples/make_lna.py  ->  examples/LNA_2G4.hzn

One common-emitter stage with the onsemi NSVF4009SG4 (in production, AEC-Q101) on a 0.508 mm RO4350B board with
0402 parts, 5 V supply, collector-feedback bias at VCE = 3 V and IC = 10 mA. Who computes what:

- Hertzon: the schematic, the microstrip lines, junctions and vias, the lumped parts with their parasitics, and the
  reduction of the whole linear circuit to the transistor pins, the ports and the supply
- ngspice: the bias point and the small-signal admittance of the transistor (S-parameter analysis)
- circuitRF: the harmonic balance (gain compression over the drive power)

Both engines run the same transistor model, models/nsvf4009sg4.va (onsemi's Gummel-Poon parameters and package
values from the data sheet, in Verilog-A). The design "LNA_Measured" is the same board with onsemi's measured
S-parameters of the transistor (VCE = 3 V, IC = 10 mA) in place of the model: Hertzon alone, as a check of the
model.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.model import circuit as CK  # noqa: E402
from hertzon.model.circuit import Component, Wire  # noqa: E402
from hertzon.model.design import Design, Project, Report, Setup, Sweep  # noqa: E402

# the parts as built (values of the bill of materials)
VALUES = {
    "C1": "3.6pF",        # input: series resonant with L1 at 2.45 GHz, blocks DC and cuts the gain below the band
    "L1": "1.2nH",
    "Le": "0.85mm",      # emitter traces to the vias (emitter degeneration)
    "L2": "6.8nH",      # collector feed and shunt element of the output match
    "R3": "12ohm",      # in the collector feed: resistive load where C3 bypasses (stability below the band)
    "C3": "68pF",       # RF bypass of the feed
    "R1": "180ohm",     # collector resistor from 5 V: VCE = 3 V at IC = 10 mA
    "R2": "24kohm",     # collector-to-base bias resistor (the bias follows the current gain of the part)
    "C2": "3.9pF",      # output DC block and series element of the output match
}
TL_W, TL_L = "1.1mm", "5mm"           # 50 ohm feed lines on 0.508 mm RO4350B
PAD_W, PAD_L = "0.4mm", "0.8mm"       # traces at the transistor pins


def _cap(name, x, y, rot, value, esl="0.3nH", esr="0.15ohm"):
    """An 0402 C0G capacitor: the capacitance with its typical series inductance and resistance."""
    return Component(name, "SRLC", x, y, rot, {"R": esr, "L": esl, "C": value})


def _ind(name, x, y, rot, value, q=40.0, cpar="0.1pF", f=2.45e9):
    """An 0402 wire-wound inductor: the inductance, its loss as a parallel resistance for the Q at the design
    frequency and its winding capacitance."""
    from hertzon.model.expr import Evaluator
    henry = Evaluator({}).value(value)
    return Component(name, "PRLC", x, y, rot, {"R": f"{2 * 3.141592653589793 * f * henry * q:.4g}ohm", "L": value,
                                                "C": cpar})


def _line(name, x, y, rot, w, length):
    return Component(name, "MLIN", x, y, rot, {"Subst": "Sub1", "W": w, "L": length})


def _wire(*pts):
    return Wire([list(p) for p in pts])


def schematic(d: Design, values: dict, measured: bool = False) -> None:
    """The amplifier, drawn left to right: input, transistor (pins b, c, e1, e2), output, the bias network above."""
    v = values
    via = {"Subst": "Sub1", "D": "0.3mm", "N": "2", "P": "0.6mm"}
    comps = [
        Component("Sub1", "SUBST", 60, -200, 0, {**CK.TYPES["SUBST"].props, "Er": "3.66", "H": "0.508mm",
                                                  "T": "35um", "TanD": "0.0037"}),
        Component("RF_in", "PORT", 0, 40, 0, {"Num": "1", "Z0": "50ohm"}),
        _line("TL_in", 100, 0, 0, TL_W, TL_L),
        _cap("C1", 220, 0, 0, v["C1"]),
        _ind("L1", 380, 0, 0, v["L1"]),
        _line("TL_b", 540, 0, 0, PAD_W, PAD_L),
        _line("TL_e1", 600, 100, 90, PAD_W, v["Le"]),
        Component("V_e1", "MVIA", 600, 160, 0, dict(via)),
        _line("TL_e2", 760, 100, 90, PAD_W, v["Le"]),
        Component("V_e2", "MVIA", 760, 160, 0, dict(via)),
        _line("TL_c", 820, 0, 0, PAD_W, PAD_L),
        _ind("L2", 960, -40, 90, v["L2"]),
        Component("R3", "R", 960, -110, 90, {"R": v["R3"]}),
        _cap("C3", 1080, -120, 90, v["C3"]),
        Component("V_c3", "MVIA", 1080, -60, 0, dict(via)),
        Component("R1", "R", 960, -200, 90, {"R": v["R1"]}),
        Component("R2", "R", 760, -160, 0, {"R": v["R2"]}),
        _cap("C2", 1080, 0, 0, v["C2"]),
        _line("TL_out", 1200, 0, 0, TL_W, TL_L),
        Component("RF_out", "PORT", 1280, 40, 0, {"Num": "2", "Z0": "50ohm"}),
    ]
    wires = [
        _wire((0, 0), (60, 0)), _wire((140, 0), (180, 0)), _wire((260, 0), (340, 0)), _wire((420, 0), (500, 0)),
        _wire((580, 0), (620, 0)),                                   # base trace to the base pin
        _wire((620, 20), (600, 20), (600, 60)), _wire((740, 20), (760, 20), (760, 60)),     # emitter leads
        _wire((740, 0), (780, 0)), _wire((860, 0), (960, 0), (1040, 0)),
        _wire((1120, 0), (1160, 0)), _wire((1240, 0), (1280, 0)),
        _wire((730, -160), (580, -160), (580, 0)),                   # bias resistor to the base
        _wire((790, -160), (960, -160), (1080, -160)),               # feed node: R3, R1, R2, C3
        _wire((960, -140), (960, -160)), _wire((960, -170), (960, -160)), _wire((960, -230), (960, -240)),
    ]
    if measured:
        comps.append(Component("Q1", "SNP", 680, 10, 0, {"File": "models/NSVF4009SG4_3V_10mA_4pin.s4p",
                                                         "Ports": "4", "PortNames": "b,c,e1,e2"}))
        comps.append(Component("GND_vcc", "GND", 960, -230, 0, {}))     # the supply is an RF short
    else:
        comps += [
            Component("Q1", "DEVICE", 680, 10, 0, {"Kind": "Verilog-A", "Model": "", "Pins": "b c e1 e2",
                                                   "Params": "", "File": "models/nsvf4009sg4.va"}),
            _cap("C4", 1200, -200, 90, "1uF", esl="0.5nH", esr="0.01ohm"),
            Component("GND_c4", "GND", 1200, -150, 0, {}),
            Component("VCC", "VDC", 1360, -210, 0, {"V": "5V"}),
            Component("GND_vcc", "GND", 1360, -170, 0, {}),
        ]
        wires.append(_wire((960, -240), (1200, -240), (1360, -240)))
    # more room for the values under the parts of the signal path
    def sx(x):
        return x + sum(40 for edge in (180, 340, 1040, 1160) if x >= edge)
    for c in comps:
        c.x = sx(c.x)
    for w in wires:
        w.points = [[sx(x), y] for x, y in w.points]
    d.components, d.wires = comps, wires


MU = ("mu_load = (1 - mag(S(1,1))^2) / (mag(S(2,2) - conj(S(1,1)) * (S(1,1)*S(2,2) - S(1,2)*S(2,1))) "
      "+ mag(S(1,2)*S(2,1)))\n"
      "mu_source = (1 - mag(S(2,2))^2) / (mag(S(1,1) - conj(S(2,2)) * (S(1,1)*S(2,2) - S(1,2)*S(2,1))) "
      "+ mag(S(1,2)*S(2,1)))")


def _sweep(name, start, stop, count):
    sw = Sweep(name)
    sw.props.update({"SweepType": "Discrete", "Distribution": "Linear Count", "Start": start, "Stop": stop,
                     "Count": str(count)})
    return sw


def four_pin_data(folder: Path) -> Path:
    """The measured common-emitter S-parameters (2 ports, emitter on ground) as a block with the pins b, c, e1, e2
    (4 ports against ground), so that the board's emitter lines and vias come in as in the model design: the
    indefinite admittance matrix of the transistor, the emitter split into its two leads (joined by 0.2 mOhm)."""
    import numpy as np
    from hertzon.model import touchstone
    src = folder / "NSVF4009SG4_3V_10mA.s2p"
    f, S2, z0 = touchstone.read(src)
    I2 = np.eye(2)
    out = []
    for s in S2:
        y = np.linalg.solve(I2 + s, I2 - s) / z0              # Y of the two ports
        Y = np.zeros((5, 5), complex)                       # b, c, e1, e2, e (inner)
        idx = [0, 1, 4]
        Yi = np.zeros((3, 3), complex)
        Yi[:2, :2] = y
        Yi[:2, 2] = -y.sum(1)
        Yi[2, :2] = -y.sum(0)
        Yi[2, 2] = y.sum()
        Y[np.ix_(idx, idx)] += Yi
        for k in (2, 3):                                    # each lead to the inner emitter: 0.1 mOhm
            g = 1e4
            Y[np.ix_([k, 4], [k, 4])] += np.array([[g, -g], [-g, g]])
        Yr = Y[:4, :4] - np.outer(Y[:4, 4], Y[4, :4]) / Y[4, 4]
        I4 = np.eye(4)
        out.append((I4 - z0 * Yr) @ np.linalg.inv(I4 + z0 * Yr))
    dst = folder / "NSVF4009SG4_3V_10mA_4pin.s4p"
    lines = ["! onsemi NSVF4009SG4, VCE = 3 V, IC = 10 mA: the measured S-parameters of the data sheet",
             "! (NSVF4009SG4_3V_10mA.s2p, common emitter) as a 4-port with the pins b, c, e1, e2 against ground;",
             "! written by examples/make_lna.py", "# Hz S RI R 50"]
    for fk, s in zip(f, out):
        row = [f"{fk:.6g}"] + [f"{x:.9g}" for v in s.reshape(-1) for x in (v.real, v.imag)]
        lines += [" ".join(row[:9]), " ".join(row[9:17]), " ".join(row[17:25]), " ".join(row[25:])]
    dst.write_text("\n".join(lines) + "\n")
    return dst


def lna() -> Project:
    p = Project("LNA_2G4")
    p.designs.clear()
    d = Design(name="LNA", kind="circuit")
    schematic(d, VALUES)
    d.settings["OutputVariables"] = MU
    sp = Setup("SParameters_ngspice",
               sweeps=[_sweep("Band", "2GHz", "3GHz", 101), _sweep("Wide", "10MHz", "12GHz", 600)])
    sp.props["CircuitAnalysis"] = "S-Parameters"
    hb = Setup("Compression_circuitRF", sweeps=[])
    hb.props.update({"CircuitAnalysis": "Harmonic Balance", "Tone": "2.45GHz", "Harmonics": "5", "DrivePort": "RF_in",
                     "Power": "-30:1:0"})
    d.setups = [sp, hb]
    d.reports = [
        Report("Gain and match", "Rectangular", "SParameters_ngspice : Band",
               ["dB(S(2,1))", "dB(S(1,1))", "dB(S(2,2))", "dB(S(1,2))"]),
        Report("Stability (mu > 1)", "Rectangular", "SParameters_ngspice : Wide", ["mu_load", "mu_source"]),
    ]
    m = Design(name="LNA_Measured", kind="circuit")
    schematic(m, VALUES, measured=True)
    m.settings["OutputVariables"] = MU
    ms = Setup("SParameters", sweeps=[_sweep("Band", "2GHz", "3GHz", 101), _sweep("Wide", "0.5GHz", "8GHz", 301)])
    m.setups = [ms]
    m.reports = [Report("Gain and match", "Rectangular", "SParameters : Band",
                        ["dB(S(2,1))", "dB(S(1,1))", "dB(S(2,2))", "dB(S(1,2))"])]
    p.designs += [d, m]
    return p


if __name__ == "__main__":
    out = Path(__file__).resolve().parent
    four_pin_data(out / "models")
    proj = lna()
    path = out / f"{proj.name}.hzn"
    proj.save(path)
    print("wrote", path)
