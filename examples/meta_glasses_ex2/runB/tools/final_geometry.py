# Final Run 7 antenna (design "Final" in Ex2_Run7.hzn). Run inside Hertzon's run_python after
#   exec(open(r'...\work\ant_lib.py').read())
# All copper is on the FPC top layer (z = 0), 2D sheets with finite conductivity (copper, two-sided).
# Vias: copper cylinders, diameter 0.2 mm, FPC top (z = 0) to PCB bottom (z = 1.2 mm).
from shapely.geometry import LineString


def final_top(x1=0.4, lov=5.13, tip_x=12.1):
    xb = x1 + lov + 0.2
    hair = LineString([(6.6, 6.25), (6.6, 4.6), (7.2, 4.6), (7.2, 6.25)]).buffer(0.075, cap_style=3, join_style=2)
    return [
        # 2.4 GHz loop: left ground leg, outer strip, four-finger series capacitor (0.1 mm gaps),
        # strip, thin ground leg (0.15 mm) at x = 6.25
        box(-3.4, 3.6, -2.5, 6.6), box(-3.4, 6.0, x1, 6.6), box(x1 - 0.2, 5.75, x1, 6.6),
        box(x1 - 0.01, 6.35, x1 + lov, 6.6), box(x1 - 0.01, 5.75, x1 + lov, 5.95),
        box(x1 + 0.2, 6.05, xb + 0.01, 6.25), box(1.15, 5.45, xb + 0.01, 5.65), box(xb, 5.45, xb + 0.2, 6.6),
        box(xb, 6.0, 6.675, 6.6), box(6.175, 3.6, 6.325, 6.1),
        # series hairpin (0.15 mm line) between the loop and the junction of the 5-7 GHz arm
        hair,
        # 5-7 GHz arm: 0.9 mm strip along the outer edge, feed strip, run down the right side,
        # strip into the lobe, tip back under the PCB edge
        box(7.125, 6.0, 7.7, 6.6), box(7.7, 5.65, 12.3, 6.6), box(7.7, 4.3, 8.2, 6.1),
        box(11.5, 4.36, 12.3, 6.6), box(11.5, 4.36, 14.6, 4.72),
        box(14.15, 3.6, 14.6, 4.72), box(tip_x, 3.6, 14.6, 3.9)]


def build_final(**kw):
    # ground vias at x = -2.9 and 6.25 mm (y centred in the 0.33 mm overlap strip under the PCB),
    # feed via at x = 7.95 mm; the copper is clipped to 0.11 mm clearance by build()
    build(final_top(**kw), gvias=[-2.9, 6.25], feed=7.95, split=[0.2, 9.5])
