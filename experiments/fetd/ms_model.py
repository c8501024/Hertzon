"""Microstrip test model: lossless FR4, PEC strip and ground, two 50-ohm lumped ports, first-order ABC."""
import emerge as em

mm = 1e-3


def build(m, f1=1e9, f2=6e9, nf=21, res=0.15, abc_order=1, L=40*mm):
    W, h, H, w = 20*mm, 1.6*mm, 8*mm, 3.0*mm
    e = 4*mm                       # ports sit 4 mm inside the box, as EMerge requires
    sub = em.geo.Box(W, L + 2*e, h, position=(-W/2, -e, 0))
    air = em.geo.Box(W, L + 2*e, H, position=(-W/2, -e, h))
    sub.set_material(em.Material(er=4.4, name="fr4"))
    strip = em.geo.XYPlate(w, L, position=(-w/2, 0, h))
    p1 = em.geo.Plate((-w/2, 0, 0), (w, 0, 0), (0, 0, h))
    p2 = em.geo.Plate((-w/2, L, 0), (w, 0, 0), (0, 0, h))
    m.commit_geometry()
    m.mw.set_frequency_range(f1, f2, nf)
    m.mw.set_resolution(res)
    m.generate_mesh()
    mw = m.mw
    mw.bc.PEC(strip)
    mw.bc.PEC(sub.bottom)
    open_faces = air.top + air.left + air.right + air.front + air.back + sub.left + sub.right + sub.front + sub.back
    mw.bc.AbsorbingBoundary(open_faces, order=abc_order)
    mw.bc.LumpedPort(p1, 1, width=w, height=h, direction=(0, 0, 1), Z0=50)
    mw.bc.LumpedPort(p2, 2, width=w, height=h, direction=(0, 0, 1), Z0=50)
    return m
