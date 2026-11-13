# Device models of the examples

| File | What it is |
|---|---|
| `gummel_poon.va` | The SPICE Gummel-Poon bipolar transistor in Verilog-A (terminals c, b, e, s), for any vendor's model card. Written for Hertzon |
| `gummel_poon_core.vams` | Its parameters and equations, shared by the modules that include it |
| `nsvf4009sg4.va` | onsemi NSVF4009SG4 RF transistor: onsemi's Gummel-Poon parameters and package network (data sheet) on the core above. Terminals b, c, e1, e2 |
| `NSVF4009SG4_3V_*mA.s2p` | onsemi's measured S-parameters of the NSVF4009SG4 at VCE = 3 V and 5, 10, 20 mA, as printed in the data sheet |
| `NSVF4009SG4_3V_10mA_4pin.s4p` | The 10 mA data as a block with the pins b, c, e1, e2 (written by `../make_lna.py`) |

ngspice and circuitRF both load a compiled Verilog-A model (OSDI). Hertzon compiles a `.va` with OpenVAF-Reloaded
(`openvaf-r`) the first time it is used and keeps the result in `.hertzon-osdi/` next to it (in the temporary
folder where that is not writable).

## Gummel-Poon in Verilog-A

The module follows the SPICE bipolar model of ngspice (level 1): transport current with Early effect and high
injection, leakage currents, base resistance with RB, RBM and IRB, emitter and collector resistances, depletion
charges with FC and the XCJC split, transit times with XTF, VTF and ITF, substrate junction, thermal and shot
noise. The parameters are evaluated at TEMP (no temperature scaling). Excess phase (PTF) is a second-order Bessel
delay of the forward current, as in an ngspice transient analysis; ngspice's AC analysis rotates the phase
instead.

Checked against ngspice's own bipolar model with the same parameters (ngspice 47, VCE = 3 V, 0.1 to 15 GHz):

| Quantity | Largest relative difference |
|---|---|
| DC collector and base current, VBE 0.6 to 1.0 V | 5e-5, 8e-5 |
| Small-signal Y21, Y11, PTF = 0 | 7e-6 |
| Small-signal Y21, Y11, PTF = 32° | 4e-3 (the excess phase, see above) |
| Input-referred noise of a common-emitter stage, 1 to 3 GHz | 1e-6 |

## NSVF4009SG4

The data sheet (NSVF4009SG4/D, Rev. 1, March 2024) lists the Gummel-Poon parameters and the package inductances
(bond wire Lw and lead Ll at each terminal) and capacitances, and draws their connection only as a figure. The
connection in `nsvf4009sg4.va` was chosen among all placements of the capacitors (at the pins, between lead and
wire, at the chip) as the one closest to onsemi's measured S-parameters; the best four placements are within 7 %
of each other. CJS is set to 0 because the printed VJS (140 fV) and MJS (150) cannot be evaluated.

Model against the measured data at VCE = 3 V, IC = 10 mA, 2.5 GHz:

| | S11 | S21 | S12 | S22 |
|---|---|---|---|---|
| data sheet | 0.384 ∠ −152.5° | 5.52 ∠ 75.6° | 0.072 ∠ 44.9° | 0.263 ∠ −75.7° |
| model | 0.371 ∠ −167.3° | 5.25 ∠ 73.0° | 0.078 ∠ 39.4° | 0.239 ∠ −69.2° |

|S21| of the model is 0.4 dB below the measurement at 2.5 GHz, the input reflection turns about 15° further. The
example LNA_2G4 has a second design, *LNA_Measured*, that uses the measured data instead of the model on the same
board, so the difference can be seen on the amplifier.
