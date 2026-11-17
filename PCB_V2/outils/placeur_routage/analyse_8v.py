# -*- coding: utf-8 -*-
"""Resistance et chute de tension du reseau +8V route (analyse nodale).

  python analyse_8v.py <placement.json> <routage_detaille.json>

Modele : pistes = resistances (longueur / largeur x 0,5 mOhm par carre, cuivre 35 um) ; via traversant
0,6 / 0,3 mm = 1,2 mOhm (cuivre de fut 25 um, 1,6 mm) ; une pastille relie sans resistance les extremites de
pistes et les vias qui la touchent. Source (0 V) : cuivre +8V du bloc hacheur (pastille L1.2, C20.2, C8, C9 s'ils
sont dans le bloc, vias +8V existants). Charges : 2 A sur chaque pastille +8V de R62_ALIM1 et R62_ALIM2 (les deux
canaux, 4 A au total). Resultat : tension de chaque charge et resistance equivalente vue par chaque canal.
Limites : surfaces de cuivre et pistes du bloc non comptees (supposees ideales), resistance des pastilles nulle,
courants des autres consommateurs +8V (U7, etc.) negliges.
"""
import json, math, os, sys
import numpy as np

ICI = os.path.dirname(os.path.abspath(__file__))
MIL = 0.0254
R_CARRE = 0.5e-3
R_VIA = 1.2e-3


def fwd(lx, ly, rot, bot):
    vx, vy = (lx, -ly) if bot else (lx, ly)
    return {0: (vx, vy), 90: (-vy, vx), 180: (-vx, -vy)}.get(rot, (vy, -vx))


def main():
    pl, rt = sys.argv[1], sys.argv[2]
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    pose = {c['nom']: tuple(c['pose_actuelle']) for c in D['composants']}
    for e in json.load(open(pl, encoding='utf-8')):
        pose[e['designator']] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
    RT = json.load(open(rt, encoding='utf-8'))
    NET = '+8V'
    bloc = set(c['nom'] for c in D['composants'] if c['statut'] == 'bloc')
    pads = []
    for c in D['composants']:
        ox, oy, rot, bot = pose[c['nom']]
        for p in c['pastilles']:
            if p['reseau'] != NET:
                continue
            dx, dy = fwd(p['lx'], p['ly'], rot, bot)
            hw, hh = (p['hh'], p['hw']) if rot in (90, 270) else (p['hw'], p['hh'])
            lay = {0, 1, 2} if p['th'] else ({1} if bot else {0})
            pads.append(dict(nom='%s.%s' % (c['nom'], p['nom']), comp=c['nom'], x=ox + dx, y=oy + dy, hw=hw, hh=hh, lay=lay))
    L = {'TopLayer': 0, 'BottomLayer': 1, 'MidLayer2': 2}
    segs = [((s['x1'] * MIL, s['y1'] * MIL), (s['x2'] * MIL, s['y2'] * MIL), s['width'] * MIL, L[s['layer']]) for s in RT['pistes'] if s['net_name'] == NET]
    vias = [(v['x'] * MIL, v['y'] * MIL) for v in RT['vias'] if v['net'] == NET]
    CB = json.load(open(os.path.join(ICI, 'cuivre_bloc_hacheur.json'), encoding='utf-8'))
    vias_bloc = [] if os.environ.get('PLACEUR_BLOC_LIBRE') == '1' else [(v[0] * MIL, v[1] * MIL) for v in CB['vias'] if v[4] == NET]
    # noeuds : (x, y, couche) arrondis ; on decoupe les segments aux points de jonction interieurs
    key = lambda x, y, l: (round(x, 3), round(y, 3), l)
    pts = set()
    for a, b, w, l in segs:
        pts.add(key(*a, l))
        pts.add(key(*b, l))
    for x, y in vias + vias_bloc:
        for l in range(3):
            pts.add(key(x, y, l))
    edges = []  # (k1, k2, R)

    def on_seg(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        if l2 == 0:
            return None
        t = ((x - x1) * dx + (y - y1) * dy) / l2
        if t <= 1e-6 or t >= 1 - 1e-6:
            return None
        if math.hypot(x1 + t * dx - x, y1 + t * dy - y) < 2e-3:
            return t
        return None
    for a, b, w, l in segs:
        cuts = [(0.0, key(*a, l)), (1.0, key(*b, l))]
        for p in pts:
            if p[2] != l:
                continue
            t = on_seg((p[0], p[1]), a, b)
            if t is not None:
                cuts.append((t, p))
        cuts.sort()
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        for (t1, k1), (t2, k2) in zip(cuts, cuts[1:]):
            if k1 != k2:
                edges.append((k1, k2, max(1e-9, (t2 - t1) * length / w * R_CARRE)))
    for x, y in vias:
        for l in (1, 2):
            edges.append((key(x, y, 0), key(x, y, l), R_VIA / 2))
    for x, y in vias_bloc:
        for l in (1, 2):
            edges.append((key(x, y, 0), key(x, y, l), R_VIA / 2))
    # pastilles : super-noeuds
    nodes = sorted(pts)
    for p in pads:
        pk = ('PAD', p['nom'])
        nodes.append(pk)
        for q in pts:
            if q[2] in p['lay'] and abs(q[0] - p['x']) <= p['hw'] + 1e-3 and abs(q[1] - p['y']) <= p['hh'] + 1e-3:
                edges.append((pk, q, 1e-6))
    SRC = ('SOURCE',)
    nodes.append(SRC)
    for p in pads:
        if p['comp'] in bloc:
            edges.append((SRC, ('PAD', p['nom']), 1e-6))
    for x, y in vias_bloc:
        edges.append((SRC, key(x, y, 0), 1e-6))
        # extremites de pistes posees dans le fut d'un via +8V existant du bloc
        for q in pts:
            if math.hypot(q[0] - x, q[1] - y) <= 0.4 + 1e-3:
                edges.append((SRC, q, 1e-6))
    idx = {n: i for i, n in enumerate(nodes)}
    # connexite : chaque charge doit etre reliee a la source
    par = {n: n for n in nodes}

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for a_, b_, _ in edges:
        par[f(a_)] = f(b_)
    for nom in ('R62_ALIM1.2', 'R62_ALIM2.2'):
        k = ('PAD', nom)
        if k in par and f(k) != f(SRC):
            print('ATTENTION : %s non relie a la source dans ce modele' % nom)
    N = len(nodes)
    G = np.zeros((N, N))
    for a, b, r in edges:
        i, j = idx[a], idx[b]
        g = 1.0 / r
        G[i, i] += g
        G[j, j] += g
        G[i, j] -= g
        G[j, i] -= g
    charges = {'R62_ALIM1.2': 2.0, 'R62_ALIM2.2': 2.0}
    I = np.zeros(N)
    for nom, a in charges.items():
        k = ('PAD', nom)
        if k not in idx:
            print('charge %s absente' % nom)
            continue
        I[idx[k]] = -a  # courant sortant
    s = idx[SRC]
    keep = [i for i in range(N) if i != s]
    Gr = G[np.ix_(keep, keep)] + np.eye(len(keep)) * 1e-12
    V = np.zeros(N)
    V[keep] = np.linalg.solve(Gr, I[keep])
    print('Reseau +8V : %d pistes, %d vias routes ; source = cuivre +8V du bloc ; 2 A par canal' % (len(segs), len(vias)))
    out = {}
    for nom in charges:
        k = ('PAD', nom)
        if k in idx:
            dv = -V[idx[k]]
            out[nom] = dv
            print('  %s : chute %.1f mV' % (nom, dv * 1000))
    # resistance equivalente de chaque canal seul
    for nom in charges:
        k = ('PAD', nom)
        if k not in idx:
            continue
        I1 = np.zeros(N)
        I1[idx[k]] = -1.0
        V1 = np.zeros(N)
        V1[keep] = np.linalg.solve(Gr, I1[keep])
        print('  resistance equivalente source -> %s : %.1f mOhm' % (nom, -V1[idx[k]] * 1000))
    composantes = None
    print(json.dumps({k: round(v * 1000, 2) for k, v in out.items()}))


if __name__ == '__main__':
    main()
