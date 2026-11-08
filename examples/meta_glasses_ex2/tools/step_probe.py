"""Parse the supplied STEP: per body bounding box, volume-ish info and largest planar face outlines."""
import re, sys, json, math
t = open(sys.argv[1], errors='ignore').read()
ent = {}
for m in re.finditer(r"#(\d+)\s*=\s*(.*?);\s*(?=#\d+\s*=|ENDSEC)", t, re.S):
    ent[int(m[1])] = ' '.join(m[2].split())
refs = lambda s: [int(x) for x in re.findall(r"#(\d+)", s)]
def pt(i):
    return tuple(float(x) for x in re.search(r"\(\s*'[^']*'\s*,\s*\(([^)]*)\)", ent[i])[1].split(','))
def vtx(i):
    return pt(refs(ent[i])[0])
out = {}
for i, s in ent.items():
    if not s.startswith('MANIFOLD_SOLID_BREP'): continue
    name = re.search(r"'([^']*)'", s)[1]
    shell = refs(s)[0]
    faces = refs(ent[shell])
    pts, fl = [], []
    for f in faces:
        fs = ent[f]
        fr = refs(fs); bounds, srf = fr[:-1], fr[-1]
        loops = []
        for b in bounds:
            lp = refs(ent[b])[0]
            edges = refs(ent[lp])
            poly, kinds = [], []
            for oe in edges:
                oes = ent[oe]
                ec = refs(oes)[-1]
                rs = refs(ent[ec])
                v1, v2 = vtx(rs[0]), vtx(rs[1])
                fwd = '.T.' in oes.split(',')[-1]
                ks = ent[rs[2]].split('(')[0]
                sg = '.T.' in ent[ec].split(',')[-1]
                poly.append((v1, v2) if fwd == sg else (v2, v1))
                kinds.append(ks)
            loops.append({'pts': [p[0] for p in poly], 'kinds': kinds})
            pts += [p[0] for p in poly]
        fl.append({'srf': ent[srf].split('(')[0], 'loops': loops})
    xs = list(zip(*pts))
    bb = [(round(min(a),3), round(max(a),3)) for a in xs]
    out[name] = {'bbox': bb, 'nfaces': len(faces), 'faces': fl}
    print(f"{name:32s} bbox x{bb[0]} y{bb[1]} z{bb[2]}  faces {len(faces)}")
json.dump(out, open(sys.argv[2], 'w'))
