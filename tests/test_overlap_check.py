"""The overlap check finds every overlapping pair and remembers what it computed."""
import gmsh
from hertzon.kernel import occ as K


def _overlap_model():
    """Nested cylinders of alternating materials and blocks through them: many pairs overlap, several completely."""
    from hertzon.model.design import Design, Project
    des = Design(units="mm")
    proj = Project(designs=[des])
    mats = ["copper", "aluminum", "air", "vacuum"]
    for i in range(6):
        des.add_object("CreateCylinder", name=f"Cyl{i}", material=mats[i % 4], CenterPosition=f"0,0,{-20 - (i % 3)}mm", Axis="Z",
                       Radius=f"{3 + i * 0.6}mm", Height=f"{40 + i * 3}mm")
    for i in range(3):
        des.add_object("CreateBox", name=f"Block{i}", material=mats[(i + 1) % 4], Position=f"{-10 + i}mm,{-4 + i}mm,{-10 + 3 * i}mm",
                       XSize="14mm", YSize="8mm", ZSize="6mm")
    return proj, des


def test_the_overlap_check_finds_every_overlapping_pair_whatever_the_order_of_the_pairs():
    """The intersection of a solid that lies completely inside another is the solid itself; removing that result removed the solid
    from the scratch model and every later pair with it found nothing (72 of 273 pairs in a model of 24 solids)."""
    import itertools
    import gmsh
    import hertzon.kernel.occ as K
    proj, des = _overlap_model()
    ev = des.evaluator(proj)
    names = [o.name for o in des.objects]
    got = {(a, b) for a, b, *_ in K.solid_overlaps(des, ev, names)}
    K.ensure_gmsh("overlap_test")
    ref = set()
    for a, b in itertools.combinations(names, 2):                  # every pair in a model of its own: nothing can disturb it
        cur = gmsh.model.getCurrent()
        gmsh.model.add("pair")
        try:
            ctx = K.build_design(des, ev, names=[a, b])
            occ = K._occ()
            sa, sb = [d for d in ctx.built[a] if d[0] == 3], [d for d in ctx.built[b] if d[0] == 3]
            r, _ = occ.intersect(sa, sb, removeObject=False, removeTool=False)
            v = sum(occ.getMass(3, t) for d, t in r if d == 3)
            if v > 1e-6 * min(sum(occ.getMass(3, t) for _, t in sa), sum(occ.getMass(3, t) for _, t in sb)):
                ref.add((a, b))
        finally:
            gmsh.model.remove()
            gmsh.model.setCurrent(cur)
    assert len(ref) > 20 and got == ref, (len(got), len(ref))


def test_the_overlap_check_skips_pairs_of_one_material_and_remembers_what_it_computed():
    import hertzon.kernel.occ as K
    proj, des = _overlap_model()
    ev = des.evaluator(proj)
    names = [o.name for o in des.objects]
    mat = {n: des.obj(n).material for n in names}
    keys = {n: K.overlap_key(des, des.obj(n), ev) for n in names}
    boxes = None
    calls = []
    import gmsh
    real = gmsh.model.occ.intersect
    gmsh.model.occ.intersect = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    try:
        K._OVERLAP_VOLUMES.clear()
        K._SOLID_VOLUMES.clear()
        every = K.solid_overlaps(des, ev, names)
        n_all = len(calls)
        calls.clear()
        first = K.solid_overlaps(des, ev, names, skip=lambda a, b: mat[a] == mat[b], keys=keys, boxes=boxes)
        n_first = len(calls)
        calls.clear()
        second = K.solid_overlaps(des, ev, names, skip=lambda a, b: mat[a] == mat[b], keys=keys, boxes=boxes)
        assert len(calls) == 0                                          # nothing changed: no boolean at all
        des.obj("Block1").history[0].props["XSize"] = "15mm"
        keys["Block1"] = K.overlap_key(des, des.obj("Block1"), ev)
        calls.clear()
        K.solid_overlaps(des, ev, names, skip=lambda a, b: mat[a] == mat[b], keys=keys, boxes=boxes)
        n_changed = len(calls)
    finally:
        gmsh.model.occ.intersect = real
    assert n_first < n_all                                              # the pairs of one material did not get a boolean
    assert sorted(second) == sorted(first) and {p[:2] for p in first} == {p[:2] for p in every if mat[p[0]] != mat[p[1]]}
    assert 0 < n_changed < n_first                                      # only the pairs of the changed solid
