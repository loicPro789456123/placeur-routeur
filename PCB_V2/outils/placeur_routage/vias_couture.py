# -*- coding: utf-8 -*-
"""Vias de couture GND (iteration 5) -> sorties/vias_couture.json.

  python -I vias_couture.py [placement.json routage_detaille.json sortie.json] [--pas 2.5]
  (defaut : sorties/placements_routage.json, sorties/routage_detaille.json, sorties/vias_couture.json)

Grille reguliere ~2,5 mm sur toute la carte ; a chaque noeud, on cherche le point admissible le plus proche dans
un carre de +-0,5 mm (pas 0,25). Via 0,6 / 0,3 mm (23,623 / 11,812 mil), traversant (4 couches). Un point est
admissible si :
  - cuivre du via a >= 1 mm du bord de carte et des 4 fentes de radiateur (vraies decoupes) ;
  - isolement >= 0,2 + 0,05 mm a tout cuivre d'un autre reseau (pistes et vias routes, pastilles, cuivre et surfaces
    du bloc, pastilles libres des trous M3), sur toutes les couches (via traversant) ;
  - aucun recouvrement avec une pastille, meme GND (ecart >= 0,25 mm a toute pastille) ;
  - trou a trou >= 0,254 + 0,05 mm (parois de percage) avec tout via (tous reseaux, couture comprise), tout trou
    de pastille traversante et les trous M3 ;
  - pas sous le corps d'un composant traversant (rectangle englobant de la piece) ;
  - remplissage GND probable : disque de rayon 0,8 mm autour du centre libre de tout cuivre d'un autre reseau sur
    TopLayer et BottomLayer (sinon le via serait une antenne dans une zone sans remplissage).
Sources : altium_rev3/pastilles.json (pastilles reelles), sorties/routage_detaille.json, cuivre_bloc_hacheur.json,
donnees_placeur.json (contour, fentes, boites des pieces), sorties/placements_routage.json.
Limites : les keep-out d'Altium ne sont pas exportes (on prend le bord et les fentes a 1 mm) ; les remplissages ne
sont pas simules (critere du disque de 0,8 mm seulement) ; les surfaces du bloc sont leurs rectangles englobants.
"""
import json, math, os, sys
from collections import defaultdict

ICI = os.path.dirname(os.path.abspath(__file__))
MIL = 0.0254
R_VIA, R_TROU = 0.3, 0.15
ISOL = 0.2 + 0.05
TROU_TROU = 0.254 + 0.05
BORD = 1.0
DISQUE = 0.8
GND = 'GND'


def fwd(lx, ly, rot, bot):
    vx, vy = (lx, -ly) if bot else (lx, ly)
    return {0: (vx, vy), 90: (-vy, vx), 180: (-vx, -vy)}.get(rot, (vy, -vx))


def d_pt_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def d_pt_rect(px, py, r):
    return math.hypot(max(r[0] - px, 0.0, px - r[2]), max(r[1] - py, 0.0, py - r[3]))


def d_pt_pad(px, py, p):
    """distance du point au cuivre de la pastille (0 dedans)"""
    x, y, wx, wy, rond = p['x'], p['y'], p['wx'], p['wy'], p['rond']
    if rond:
        r = min(wx, wy) / 2
        if wx >= wy:
            return max(0.0, d_pt_seg(px, py, x - wx / 2 + r, y, x + wx / 2 - r, y) - r)
        return max(0.0, d_pt_seg(px, py, x, y - wy / 2 + r, x, y + wy / 2 - r) - r)
    return d_pt_rect(px, py, (x - wx / 2, y - wy / 2, x + wx / 2, y + wy / 2))


def main():
    args = [x for x in sys.argv[1:] if not x.startswith('--')]
    pas = 2.5
    if '--pas' in sys.argv:
        v = sys.argv[sys.argv.index('--pas') + 1]
        pas = float(v)
        args.remove(v)
    pl_path = args[0] if len(args) > 0 else os.path.join(ICI, 'sorties', 'placements_routage.json')
    rt_path = args[1] if len(args) > 1 else os.path.join(ICI, 'sorties', 'routage_detaille.json')
    out_path = args[2] if len(args) > 2 else os.path.join(ICI, 'sorties', 'vias_couture.json')
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    W, H = D['contour']['w'], D['contour']['h']
    RT = json.load(open(rt_path, encoding='utf-8'))
    CB = json.load(open(os.path.join(ICI, 'cuivre_bloc_hacheur.json'), encoding='utf-8'))
    if os.environ.get('PLACEUR_BLOC_LIBRE') == '1':   # bloc libere : cuivre manuel non repris
        CB = dict(pistes=[], vias=[], regions=[])
    PL = json.load(open(pl_path, encoding='utf-8'))
    pose = {c['nom']: tuple(c['pose_actuelle']) for c in D['composants']}
    for e in PL:
        pose[e['designator']] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
    # pastilles : meme chargeur que le DRC maison (Altium rev3 si le placement correspond, sinon geometrie reelle replacee)
    sys.path.insert(0, ICI)
    import drc_maison
    pp, source = drc_maison.charger_pastilles(D, pose)
    print('pastilles : ' + source)
    pads, trks, vias, regs = [], [], [], []
    for q in pp:
        fo = q['f']
        lay = {0, 1, 2, 3} if q['th'] else set(q['lay'])
        pads.append(dict(x=fo.x, y=fo.y, wx=fo.wx, wy=fo.wy, rond=fo.rond, lay=lay, net=q['net'], trou=q.get('trou', 0.0), nom=q['nom']))
    L = {'TopLayer': 0, 'BottomLayer': 1, 'MidLayer2': 2}
    for s in RT['pistes']:
        trks.append(((s['x1'] * MIL, s['y1'] * MIL, s['x2'] * MIL, s['y2'] * MIL), s['width'] * MIL / 2, L[s['layer']], s['net_name']))
    for v in RT['vias']:
        vias.append((v['x'] * MIL, v['y'] * MIL, v['size'] * MIL / 2, v['hole_size'] * MIL / 2, v['net']))
    for p in CB['pistes']:
        trks.append(((p[0] * MIL, p[1] * MIL, p[2] * MIL, p[3] * MIL), p[4] * MIL / 2, 0 if p[5] == 'T' else 1, p[6]))
    for v in CB['vias']:
        vias.append((v[0] * MIL, v[1] * MIL, v[2] * MIL / 2, v[3] * MIL / 2, v[4]))
    for r in CB['regions']:
        regs.append(((r[2] * MIL, r[3] * MIL, r[4] * MIL, r[5] * MIL), 0 if r[0] == 'T' else 1, r[1]))
    # corps des composants traversants
    corps = []
    for c in D['composants']:
        if not any(p['th'] for p in c['pastilles']):
            continue
        ox, oy, rot, bot = pose[c['nom']]
        l = c['lbb']
        cs = [fwd(x, y, rot, bot) for x in (l[0], l[2]) for y in (l[1], l[3])]
        corps.append((ox + min(a for a, b in cs), oy + min(b for a, b in cs), ox + max(a for a, b in cs), oy + max(b for a, b in cs)))
    # index spatial (cellules de 2 mm)
    C = 2.0
    idx = defaultdict(list)

    def poser(kind, k, bb):
        for gx in range(int((bb[0] - 1) // C), int((bb[2] + 1) // C) + 1):
            for gy in range(int((bb[1] - 1) // C), int((bb[3] + 1) // C) + 1):
                idx[(gx, gy)].append((kind, k))
    for k, p in enumerate(pads):
        poser('p', k, (p['x'] - p['wx'] / 2, p['y'] - p['wy'] / 2, p['x'] + p['wx'] / 2, p['y'] + p['wy'] / 2))
    for k, (s, h, l, n) in enumerate(trks):
        poser('t', k, (min(s[0], s[2]) - h, min(s[1], s[3]) - h, max(s[0], s[2]) + h, max(s[1], s[3]) + h))
    for k, v in enumerate(vias):
        poser('v', k, (v[0] - v[2], v[1] - v[2], v[0] + v[2], v[1] + v[2]))
    for k, (r, l, n) in enumerate(regs):
        poser('r', k, r)
    places = []

    def admissible(x, y):
        if min(x, y, W - x, H - y) - R_VIA < BORD:
            return False
        for f in D['fentes']:
            if d_pt_rect(x, y, (f[0], f[1], f[0] + f[2], f[1] + f[3])) - R_VIA < BORD:
                return False
        for b in corps:
            if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                return False
        vus = set()
        for gx in range(int(x // C) - 1, int(x // C) + 2):
            for gy in range(int(y // C) - 1, int(y // C) + 2):
                for o in idx.get((gx, gy), ()):
                    if o in vus:
                        continue
                    vus.add(o)
                    kind, k = o
                    if kind == 'p':
                        p = pads[k]
                        dd = d_pt_pad(x, y, p)
                        if dd - R_VIA < ISOL:  # aucune pastille, meme GND
                            return False
                        if p['trou'] > 0 and math.hypot(x - p['x'], y - p['y']) - R_TROU - p['trou'] / 2 < TROU_TROU:
                            return False
                        if p['net'] != GND and (p['lay'] & {0, 1}) and dd < DISQUE:
                            return False
                    elif kind == 't':
                        s, h, l, n = trks[k]
                        if n == GND:
                            continue
                        dd = d_pt_seg(x, y, *s) - h
                        if dd - R_VIA < ISOL:
                            return False
                        if l in (0, 1) and dd < DISQUE:
                            return False
                    elif kind == 'v':
                        vx, vy, rv, rt, n = vias[k]
                        dc = math.hypot(x - vx, y - vy)
                        if dc - R_TROU - rt < TROU_TROU:
                            return False
                        if n != GND and (dc - rv - R_VIA < ISOL or dc - rv < DISQUE):
                            return False
                    else:
                        r, l, n = regs[k]
                        if n == GND:
                            continue
                        dd = d_pt_rect(x, y, r)
                        if dd - R_VIA < ISOL or dd < DISQUE:
                            return False
        for (px, py) in places:
            if math.hypot(x - px, y - py) - 2 * R_TROU < TROU_TROU:
                return False
        return True
    decal = sorted({(i * 0.25, j * 0.25) for i in range(-2, 3) for j in range(-2, 3)}, key=lambda t: (math.hypot(*t), t))
    nx, ny = int(W // pas), int(H // pas)
    essais = 0
    for i in range(nx + 1):
        for j in range(ny + 1):
            x0, y0 = (i + 0.5) * pas, (j + 0.5) * pas
            if x0 > W or y0 > H:
                continue
            essais += 1
            for dx, dy in decal:
                if admissible(x0 + dx, y0 + dy):
                    places.append((x0 + dx, y0 + dy))
                    break
    r3 = lambda v: round(v / MIL, 3)
    out = dict(_doc=__doc__, unite='mil', origine='coin bas-gauche de la carte', pas_grille_mm=pas,
               vias=[dict(x=r3(x), y=r3(y), size=23.623, hole_size=11.812, net=GND) for x, y in places])
    json.dump(out, open(out_path, 'w', encoding='utf-8'), indent=1)
    print('%d vias de couture sur %d noeuds de grille (pas %.2f mm) -> %s' % (len(places), essais, pas, out_path))


if __name__ == '__main__':
    main()
