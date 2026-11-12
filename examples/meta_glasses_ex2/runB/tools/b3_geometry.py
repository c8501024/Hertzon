# Design B3_final of Run 7 (arm on the left, loop on the right). Run inside Hertzon's run_python after
#   exec(open(r'...\ant_lib.py').read())
# All copper on the FPC top layer; vias 0.2 mm; B3 uses lov=5.05, B2 uses x1=6.5, lov=5.0, foot=-1.45.
from shapely.geometry import LineString


def b3_top(x1=6.4, lov=5.10, foot=-1.6):
    xb = x1 + lov + 0.2
    hair = LineString([(3.3, 6.25), (3.3, 4.6), (3.9, 4.6), (3.9, 6.25)]).buffer(0.075, cap_style=3, join_style=2)
    return [
        # 5-7 GHz arm: outer strip (0.9 mm wide left of x = 2.2), tail down the left side, foot, feed strip
        box(-3.4, 6.0, 3.375, 6.6), box(-3.4, 5.7, 2.2, 6.6), box(-3.4, 4.3, -2.5, 6.6),
        box(-3.4, 4.3, foot, 4.8), box(2.25, 4.3, 2.75, 6.1),
        # series hairpin (0.15 mm line) between the arm junction and the loop
        hair,
        # 2.4 GHz loop: thin ground leg, strip, four-finger series capacitor (0.1 mm gaps), path to the right via
        box(4.275, 3.6, 4.425, 6.1), box(3.825, 6.0, x1, 6.6), box(x1 - 0.2, 5.75, x1, 6.6),
        box(x1 - 0.01, 6.35, x1 + lov, 6.6), box(x1 - 0.01, 5.75, x1 + lov, 5.95),
        box(x1 + 0.2, 6.05, xb + 0.01, 6.25), box(x1 + 0.2, 5.45, xb + 0.01, 5.65), box(xb, 5.45, xb + 0.2, 6.6),
        box(xb, 6.0, 12.3, 6.6),
        box(11.9, 4.36, 12.3, 6.6), box(11.9, 4.36, 14.2, 4.72), box(13.6, 3.6, 14.2, 4.72)]


def build_b3(**kw):
    # ground vias at x = 4.35 and 13.9 mm, feed via at x = 2.5 mm
    build(b3_top(**kw), gvias=[4.35, 13.9], feed=2.5, split=[5.5])
