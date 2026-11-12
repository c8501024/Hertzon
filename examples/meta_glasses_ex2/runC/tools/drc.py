"""Design rule check of the final antenna polygon against the FPC outline and holes (all in mm)."""
import math

V = dict(xL=-3.1, xR=12, yT=6.55, wT=0.4, xf=8.5, wf=1.0, yfb=4.3, yr=4.3, wr=0.4, xe=6.8, wv=0.4,
         xs=7.5, xk=1.1, ym=5.2, wm=0.6, xtab=14.2, ytb=3.65, wtb=1.8, wT2=0.4, wv2=0.4, xsv=6.3,
         wTw=0.65, xw=-0.9)
PTS = ("xsv-0.25,3.65;xs+0.2,3.65;xs+0.2,yT-wT;xf-wf/2,yT-wT;xf-wf/2,yfb;xf+wf/2,yfb;xf+wf/2,yT-wT2;"
       "xR-wv2,yT-wT2;xR-wv2,4.35;xtab-wtb,4.35;xtab-wtb,ytb;xtab,ytb;xtab,4.7;xR,4.7;xR,yT;xL,yT;xL,yr;"
       "xe,yr;xe,ym+wm;xk,ym+wm;xk,ym;xe-wv,ym;xe-wv,yr+wr;xL+wv,yr+wr;xL+wv,yT-wTw;xw,yT-wTw;xw,yT-wT;"
       "xs-0.2,yT-wT;xs-0.2,3.95;xsv-0.25,3.95")
ant = [tuple(eval(c, {}, V) for c in p.split(",")) for p in PTS.split(";")]

fpc = [(-3.4, 3.5), (10.23, 3.5), (10.71, 3.26), (11.3, 2.94), (12, 2.94), (12.64, 3.26), (13.12, 3.5), (14.4, 3.5),
       (14.63, 3.73), (14.63, 4.6), (14.4, 4.83), (12.88, 4.85), (12.45, 5.03), (12.29, 5.46), (12.3, 6.42),
       (12.07, 6.655), (-3.17, 6.655), (-3.41, 6.42), (-3.647, 3.774)]
slot = [(11.441, 3.49), (11.839, 3.444), (11.926, 4.188), (11.529, 4.235)]
hole_c, hole_r = (0.525, 5.315), 0.372


def segs(poly):
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def d_pt_seg(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def d_seg_seg(s1, s2):
    return min(d_pt_seg(s1[0], *s2), d_pt_seg(s1[1], *s2), d_pt_seg(s2[0], *s1), d_pt_seg(s2[1], *s1))


def inside(p, poly):
    x, y = p; c = False
    for (x1, y1), (x2, y2) in segs(poly):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


print("all antenna vertices inside FPC outline:", all(inside(p, fpc) for p in ant))
print("min distance copper - FPC edge   : %.3f mm" % min(d_seg_seg(a, f) for a in segs(ant) for f in segs(fpc)))
print("min distance copper - round hole : %.3f mm" % (min(d_pt_seg(hole_c, *a) for a in segs(ant)) - hole_r))
print("min distance copper - slot hole  : %.3f mm" % min(d_seg_seg(a, f) for a in segs(ant) for f in segs(slot)))
print("round hole centre inside copper:", inside(hole_c, ant), " slot corners inside copper:", any(inside(p, ant) for p in slot))
# vias: centre, radius; PCB upper edge y = 3.93 + 0.0055866*(x+3.5)
for name, (x, y) in (("ground via", (V["xsv"], 3.8)), ("feed via", (V["xf"], 4.65))):
    edge = 3.93 + 0.0055866 * (x + 3.5)
    print("%s at (%.2f, %.2f) r 0.125: inside copper %s, top of via to PCB edge %.3f mm (%s PCB)" % (
        name, x, y, inside((x, y), ant) and min(d_pt_seg((x, y), *a) for a in segs(ant)) >= 0.125 - 1e-9,
        edge - (y + 0.125), "under" if y + 0.125 < edge else "outside"))
# smallest gap between non-adjacent copper edges (trace spacing)
s = segs(ant); n = len(s)
gap = min(d_seg_seg(s[i], s[j]) for i in range(n) for j in range(i + 2, n) if not (i == 0 and j == n - 1))
print("smallest copper-copper gap / neck: %.3f mm" % gap)
