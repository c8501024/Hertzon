# Builder for Run 7 antenna variants. Executed inside Hertzon's run_python
# (needs add_object, operation, design, Assignment in the namespace).
from shapely.geometry import Polygon, box, Point
from shapely.ops import unary_union

FPC_PTS = [(-3.4, 3.5), (10.23, 3.5), (10.71, 3.26), (11.3, 2.94), (12, 2.94), (12.64, 3.26),
           (13.12, 3.5), (14.4, 3.5), (14.63, 3.73), (14.63, 4.6), (14.4, 4.83), (12.88, 4.85),
           (12.45, 5.03), (12.29, 5.46), (12.3, 6.42), (12.07, 6.655), (-3.17, 6.655),
           (-3.41, 6.42), (-3.647, 3.774)]
HOLE_R = Point(0.525, 5.315).buffer(0.372, 64)
HOLE_L = Polygon([(11.441, 3.49), (11.839, 3.444), (11.926, 4.188), (11.529, 4.235)])
FPC = Polygon(FPC_PTS)
CLEAR = 0.11            # copper clearance to FPC edges and holes (rule: >= 0.1)
ALLOWED = FPC.buffer(-CLEAR, join_style=2).difference(HOLE_R.buffer(CLEAR, 64)).difference(
    HOLE_L.buffer(CLEAR, join_style=2))
Z_TOP, Z_BOT, Z_PCB = 0.0, -0.265, 1.2
VIA_D = 0.2
GAP = 0.3               # port gap between PCB edge and signal pad
PAD_W, PAD_D = 0.6, 0.6


def ye(x):
    """y of the upper PCB edge, (-3.5, 3.93) .. (16.19, 4.04)"""
    return 3.93 + (x + 3.5) * (4.04 - 3.93) / (16.19 + 3.5)


def gvia_y(x):
    return (3.5 + CLEAR + ye(x)) / 2


def feed_y(x):
    return ye(x) + GAP + PAD_D / 2


def _pts(coords, z):
    return ';'.join('%.4f,%.4f,%g' % (x, y, z) for x, y in coords)


def _sheets(base, geom, z, color):
    names = []
    polys = [geom] if geom.geom_type == 'Polygon' else list(geom.geoms)
    for i, p in enumerate(polys):
        p = p.simplify(1e-4)
        if p.area < 1e-4:
            continue
        name = '%s%d' % (base, i + 1)
        holes = '|'.join(_pts(list(r.coords)[:-1], z) for r in p.interiors)
        o = add_object('CreatePolyline', name=name, material='vacuum',
                       Points=_pts(list(p.exterior.coords)[:-1], z), Closed='True', Covered='True',
                       Holes=holes)
        o.color = color
        names.append(name)
    return names


def clean():
    dead = set(o.name for o in design.objects if o.name.startswith('A_'))
    design.objects[:] = [o for o in design.objects if o.name not in dead]
    for lst in (design.boundaries, design.excitations, design.mesh_ops):
        lst[:] = [a for a in lst if not (set(a.objects) & dead or a.name.startswith('A_'))]
    fpc = design.obj('FPC')
    fpc.history[:] = [h for h in fpc.history
                      if not (h.kind == 'Subtract' and 'A_' in h.props.get('Tools', ''))]


def _cut(geom, split):
    if not split:
        return geom
    xs = [-50] + sorted(split) + [50]
    parts = [geom.intersection(box(a, -50, b, 50)) for a, b in zip(xs[:-1], xs[1:])]
    out = []
    for g in parts:
        out += [g] if g.geom_type == 'Polygon' else [q for q in getattr(g, 'geoms', []) if q.geom_type == 'Polygon']
    from shapely.geometry import MultiPolygon
    return MultiPolygon(out)


def build(top, bot=(), gvias=(), feed=None, ivias=(), keep_raw=False, split=(), tvias=(), antipad=0.3):
    """top/bot: lists of shapely geometries (design units mm); gvias: x positions of ground vias
    (or (x, y)); feed: x of the feed via; ivias: (x, y) of FPC inter-layer vias."""
    clean()
    top = list(top)
    gv = [(g, gvia_y(g)) if not isinstance(g, tuple) else g for g in gvias]
    for x, y in gv:      # landing pad under each ground via
        top.append(box(x - 0.3, 3.5 + CLEAR, x + 0.3, y + 0.25))
    if feed is not None:
        fy = feed_y(feed)
        top.append(box(feed - 0.3, fy - 0.3, feed + 0.3, fy + 0.3))
    for x, y in ivias:
        top.append(Point(x, y).buffer(0.3, 16))
    tg = unary_union(top)
    for x, y in tvias:   # through vias (bottom layer to PCB) pass the top layer in an antipad
        tg = tg.difference(Point(x, y).buffer(antipad, 32))
    bot = list(bot) + [box(x - 0.3, 3.5 + CLEAR, x + 0.3, y + 0.25) for x, y in tvias]
    bg = unary_union(list(bot) + [Point(x, y).buffer(0.3, 16) for x, y in ivias]) if (bot or ivias) else None
    if not keep_raw:
        tg = tg.intersection(ALLOWED)
        if bg is not None:
            bg = bg.intersection(ALLOWED)
    cu = _sheets('A_top', _cut(tg, split), Z_TOP, (200, 120, 40))
    if bg is not None and not bg.is_empty:
        cu += _sheets('A_bot', _cut(bg, split), Z_BOT, (40, 140, 200))
    vias = []
    for i, (x, y) in enumerate(gv):
        n = 'A_gvia%d' % (i + 1)
        add_object('CreateCylinder', name=n, material='copper', CenterPosition='%.4f,%.4f,0' % (x, y),
                   Axis='Z', Radius='%g' % (VIA_D / 2), Height='%g' % Z_PCB)
        vias.append(n)
    tv = []
    for i, (x, y) in enumerate(tvias):
        n = 'A_tvia%d' % (i + 1)
        add_object('CreateCylinder', name=n, material='copper', CenterPosition='%.4f,%.4f,%g' % (x, y, Z_BOT),
                   Axis='Z', Radius='%g' % (VIA_D / 2), Height='%g' % (Z_PCB - Z_BOT))
        tv.append(n)
        v = Point(x, y).buffer(VIA_D / 2, 32)
        print('tvia (%.2f, %.3f): on bottom copper %s, top clearance %.3f, under PCB %s' % (
            x, y, bg.buffer(1e-6).contains(v), tg.distance(v), y + VIA_D / 2 < ye(x)))
    if tv:
        operation('FPC', 'Subtract', Tools=','.join(tv), KeepOriginals='True')
    vias += tv
    if feed is not None:
        fy = feed_y(feed)
        add_object('CreateCylinder', name='A_fvia', material='copper',
                   CenterPosition='%.4f,%.4f,0' % (feed, fy), Axis='Z', Radius='%g' % (VIA_D / 2),
                   Height='%g' % Z_PCB)
        x0, x1 = feed - PAD_W / 2, feed + PAD_W / 2
        pad = [(x0, ye(x0) + GAP), (x1, ye(x1) + GAP), (x1, ye(x1) + GAP + PAD_D), (x0, ye(x0) + GAP + PAD_D)]
        add_object('CreatePolyline', name='A_pad', material='vacuum', Points=_pts(pad, Z_PCB),
                   Closed='True', Covered='True')
        cu.append('A_pad')
        prt = [(x0, ye(x0)), (x1, ye(x1)), (x1, ye(x1) + GAP), (x0, ye(x0) + GAP)]
        add_object('CreatePolyline', name='A_port', material='vacuum', Points=_pts(prt, Z_PCB),
                   Closed='True', Covered='True')
        design.excitations.append(Assignment(
            name='A_P1', kind='Lumped Port',
            props={'PortNumber': '1', 'Impedance': '50ohm',
                   'IntegrationLine': '%.4f,%.4f,%g;%.4f,%.4f,%g' % (feed, ye(feed), Z_PCB, feed, ye(feed) + GAP, Z_PCB)},
            objects=['A_port']))
    design.boundaries.append(Assignment(name='A_Cu', kind='Finite Conductivity',
                                        props={'UseMaterial': 'True', 'Material': 'copper', 'TwoSided': 'True'}, objects=cu))
    # design-rule numbers
    allcu = tg if bg is None else unary_union([tg, bg])
    print('top area %.2f mm2' % tg.area, '| bottom area %.2f' % (bg.area if bg is not None else 0))
    print('min dist to FPC outline %.3f' % allcu.distance(FPC.exterior),
          '| round hole %.3f' % allcu.distance(HOLE_R), '| slot hole %.3f' % allcu.distance(HOLE_L))
    for x, y in gv:
        v = Point(x, y).buffer(VIA_D / 2, 32)
        print('gvia (%.2f, %.3f): on top copper %s, under PCB %s (edge y %.3f)' % (
            x, y, tg.buffer(1e-6).contains(v), y + VIA_D / 2 < ye(x), ye(x)))
    if feed is not None:
        v = Point(feed, feed_y(feed)).buffer(VIA_D / 2, 32)
        print('fvia (%.2f, %.3f): on top copper %s' % (feed, feed_y(feed), tg.buffer(1e-6).contains(v)))
    print('objects:', cu, vias)
