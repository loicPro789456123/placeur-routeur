# -*- coding: utf-8 -*-
"""DRC maison independant du routeur Rust, recale sur Altium (iteration 4).

Lancer : python -I drc_maison.py <placement.json> <routage_detaille.json> [sortie.json] [--altium]
  --altium : connexion = recouvrement de cuivre (regle d'Altium, pour comparer a son chevelu) ;
  defaut (strict) : connexion seulement si l'extremite de l'axe de la piste est dans le cuivre (exigence de pose).

Pastilles : geometrie REELLE lue dans altium_rev3/pastilles.json (cuivre TopXSize/TopYSize, forme ronde /
oblongue / rectangulaire, traversante = toutes couches, pastilles libres des trous M3). On verifie que le
placement fourni redonne les memes positions (sinon on retombe sur le modele de donnees_placeur.json).

Connectivite (comme Altium) : graphe de contact reel entre primitives d'un meme reseau sur une meme couche :
  - extremite de piste dans le cuivre d'une pastille, d'un via ou d'une autre piste ;
  - centre de via dans le cuivre d'une pastille (via et pastilles traversantes : toutes couches) ;
  - GND : pastilles traversantes et vias GND relies au plan L2 (plein) ;
  - hypothese : les pastilles d'un meme reseau sur les pieces du bloc hacheur sont reliees par son cuivre.
  Liaisons ouvertes = somme sur les reseaux de (nombre de morceaux contenant une pastille - 1), comme le
  chevelu d'Altium.
Isolement : 0,124 mm seulement entre pastilles de M1/M2/U10 elles-memes, sinon max(isolement des classes)
(0,2 / 0,25 COMMUTATION, piste ou via contre pastille d'U10 compris) ; PLACEUR_BLOC_LIBRE=1 : cuivre manuel du
bloc ignore et tous ses reseaux juges ;
pastilles libres des trous M3 : 0,2 mm sur toutes les couches. Gardes 0,6 mm au bord et aux fentes.
Largeurs comparees en mils (3 decimales) a la mini de la classe. Vias 0,3-0,8 / 0,3-0,4 ; via dans une
pastille CMS interdit (sauf L1, U10 ; derogation declaree pour GND). Angles aigus. Mesures d'ecart et de
resistance.
"""
import json, math, os, sys, collections

ICI = os.path.dirname(os.path.abspath(__file__))
MIL = 0.0254
COUCHES = {'TopLayer': 0, 'BottomLayer': 1, 'MidLayer2': 2}
MODE = 'strict'


def fwd(lx, ly, rot, bot):
    vx, vy = (lx, -ly) if bot else (lx, ly)
    return {0: (vx, vy), 90: (-vy, vx), 180: (-vx, -vy)}.get(rot, (vy, -vx))


def d_pt_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def seg_inter(a, b, c, d):
    def cr(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2, d3, d4 = cr(c, d, a), cr(c, d, b), cr(a, b, c), cr(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


def d_seg_seg(a, b, c, d):
    if seg_inter(a, b, c, d):
        return 0.0
    return min(d_pt_seg(*a, *c, *d), d_pt_seg(*b, *c, *d), d_pt_seg(*c, *a, *b), d_pt_seg(*d, *a, *b))


def d_pt_rect(px, py, r):
    dx = max(r[0] - px, 0.0, px - r[2])
    dy = max(r[1] - py, 0.0, py - r[3])
    return math.hypot(dx, dy)


def d_seg_rect(a, b, r):
    if r[0] <= a[0] <= r[2] and r[1] <= a[1] <= r[3]:
        return 0.0
    if r[0] <= b[0] <= r[2] and r[1] <= b[1] <= r[3]:
        return 0.0
    cs = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    best = min(d_pt_rect(a[0], a[1], r), d_pt_rect(b[0], b[1], r))
    for k in range(4):
        best = min(best, d_seg_seg(a, b, cs[k], cs[(k + 1) % 4]))
    return best


class Forme:
    """Cuivre d'une pastille : rectangle aligne, ou capsule (rond = capsule de longueur nulle, oblong)."""

    def __init__(self, x, y, wx, wy, rond):
        self.x, self.y, self.rond = x, y, rond
        self.wx, self.wy = wx, wy
        if rond:
            r = min(wx, wy) / 2
            self.r = r
            if wx >= wy:
                self.a, self.b = (x - wx / 2 + r, y), (x + wx / 2 - r, y)
            else:
                self.a, self.b = (x, y - wy / 2 + r), (x, y + wy / 2 - r)
        self.rect = (x - wx / 2, y - wy / 2, x + wx / 2, y + wy / 2)

    def d_pt(self, px, py):
        """distance du point au cuivre (0 dedans) ; negative = profondeur a l'interieur."""
        if self.rond:
            return d_pt_seg(px, py, *self.a, *self.b) - self.r
        dx = abs(px - self.x) - self.wx / 2
        dy = abs(py - self.y) - self.wy / 2
        if dx <= 0 and dy <= 0:
            return max(dx, dy)
        return math.hypot(max(dx, 0), max(dy, 0))

    def d_seg(self, a, b):
        if self.rond:
            return d_seg_seg(a, b, self.a, self.b) - self.r
        return d_seg_rect(a, b, self.rect)

    def d_forme(self, o):
        if self.rond and o.rond:
            return d_seg_seg(self.a, self.b, o.a, o.b) - self.r - o.r
        if self.rond:
            return d_seg_rect(self.a, self.b, o.rect) - self.r
        if o.rond:
            return d_seg_rect(o.a, o.b, self.rect) - o.r
        r, s = self.rect, o.rect
        return math.hypot(max(s[0] - r[2], 0.0, r[0] - s[2]), max(s[1] - r[3], 0.0, r[1] - s[3]))


def charger_pastilles(D, pose):
    """Pastilles reelles Altium si le placement correspond, sinon modele."""
    ap = os.path.join(ICI, 'altium_rev3', 'pastilles.json')
    pads = []
    if os.path.exists(ap):
        A = json.load(open(ap, encoding='utf-8'))
        # controle : positions Altium = positions calculees avec le placement fourni
        modele = {}
        for c in D['composants']:
            ox, oy, rot, bot = pose[c['nom']]
            for p in c['pastilles']:
                dx, dy = fwd(p['lx'], p['ly'], rot, bot)
                modele.setdefault((c['nom'], p['nom']), []).append((ox + dx, oy + dy))
        ecart = 0.0
        for q in A:
            if q['Component'] in ('', 'Free'):
                continue
            pos = modele.get((q['Component'], q['Name']))
            if not pos:
                ecart = 99.0
                break
            ecart = max(ecart, min(math.hypot(float(q['X']) * MIL - x, float(q['Y']) * MIL - y) for x, y in pos))
        if ecart < 0.01:
            for q in A:
                r = int(float(q['Rotation'])) % 360
                sx, sy = float(q['TopXSize']) * MIL, float(q['TopYSize']) * MIL
                wx, wy = (sx, sy) if r in (0, 180) else (sy, sx)
                lay = (0, 1, 2) if q['Layer'] == 'MultiLayer' else ((0,) if q['Layer'] == 'TopLayer' else (1,))
                comp = q['Component'] if q['Component'] not in ('', 'Free') else 'Free-' + q['Name']
                pads.append(dict(comp=comp, nom='%s.%s' % (comp, q['Name']), net=q['Net'], lay=lay,
                                 f=Forme(float(q['X']) * MIL, float(q['Y']) * MIL, wx, wy, q['TopShape'] == '1'), th=q['Layer'] == 'MultiLayer',
                                 trou=float(q['HoleSize']) * MIL))
            return pads, 'Altium (altium_rev3/pastilles.json)'
        print('placement different de celui d Altium rev3 (ecart %.3f mm) : modele de pastilles' % ecart)
    # placement different : geometrie reelle (pastilles_reelles.json, formes et cuivre d'Altium) au nouveau placement
    pr = os.path.join(ICI, 'pastilles_reelles.json')
    PR = json.load(open(pr, encoding='utf-8')) if os.path.exists(pr) else None
    for c in D['composants']:
        ox, oy, rot, bot = pose[c['nom']]
        for k, p in enumerate(c['pastilles']):
            hw0, hh0, rond, th, trou = p['hw'], p['hh'], False, p['th'], 0.0
            if PR:
                q = PR['pieces'][c['nom']][k]
                hw0, hh0, rond, th, trou = q['hw'], q['hh'], q['forme'] != 'rect', q['th'], q['trou']
            dx, dy = fwd(p['lx'], p['ly'], rot, bot)
            hw, hh = (hh0, hw0) if rot in (90, 270) else (hw0, hh0)
            lay = (0, 1, 2) if th else ((1,) if bot else (0,))
            pads.append(dict(comp=c['nom'], nom='%s.%s' % (c['nom'], p['nom']), net=p['reseau'], lay=lay,
                             f=Forme(ox + dx, oy + dy, 2 * hw, 2 * hh, rond), th=th, trou=trou))
    if PR:
        for q in PR['libres']:
            pads.append(dict(comp='Free-' + q['nom'], nom='Free-%s.%s' % (q['nom'], q['nom']), net='', lay=(0, 1, 2),
                             f=Forme(q['x'], q['y'], q['d'], q['d'], True), th=True, trou=q['trou']))
        return pads, 'geometrie reelle (pastilles_reelles.json) au placement fourni'
    return pads, 'modele (rectangles englobants)'


def main():
    global MODE
    args = [x for x in sys.argv[1:] if not x.startswith('--')]
    MODE = 'altium' if '--altium' in sys.argv else 'strict'
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    PL = json.load(open(args[0], encoding='utf-8'))
    RT = json.load(open(args[1], encoding='utf-8'))
    out_path = args[2] if len(args) > 2 else os.path.join(os.path.dirname(os.path.abspath(args[1])), 'drc_maison.json')
    R = D['regles']
    W, H = D['contour']['w'], D['contour']['h']
    classes = D['classes']
    fins = set(R['isolement']['pieces_pas_fin'])
    via_ok = set(R['via']['via_sous_cms_permis'])
    garde = R['garde_bord_fentes_trous']
    plans = set(D['reseaux_plans'])

    def clr_net(n):
        return R['isolement']['COMMUTATION'] if classes.get(n) == 'COMMUTATION' else R['isolement']['defaut']

    def larg(n):
        return R['largeurs'].get(classes.get(n, 'defaut'), R['largeurs']['defaut'])

    pose = {c['nom']: tuple(c['pose_actuelle']) for c in D['composants']}
    for e in PL:
        pose[e['designator']] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
    libre = os.environ.get('PLACEUR_BLOC_LIBRE') == '1'
    # bloc libere : plus de cuivre manuel, plus de reseaux internes exemptes (tout est route et juge)
    bloc = set() if libre else set(c['nom'] for c in D['composants'] if c['statut'] == 'bloc')
    pads, source_pads = charger_pastilles(D, pose)
    objs = []
    for p in pads:
        objs.append(dict(t='pad', net=p['net'] or ('<sans>' + p['nom']), lay=p['lay'], f=p['f'], comp=p['comp'],
                         fin=p['comp'] in fins, th=p['th'], nom=p['nom'], vok=p['comp'] in via_ok, libre=p['comp'].startswith('Free-')))
    for key in ('pistes', 'pistes_raccord_bloc'):
        for s in RT.get(key, []):
            objs.append(dict(t='trk', net=s['net_name'], lay=(COUCHES[s['layer']],), a=(s['x1'] * MIL, s['y1'] * MIL),
                             b=(s['x2'] * MIL, s['y2'] * MIL), w=s['width'] * MIL, wmil=s['width']))
    for key in ('vias', 'vias_raccord_bloc'):
        for v in RT.get(key, []):
            objs.append(dict(t='via', net=v['net'], lay=(0, 1, 2), c=(v['x'] * MIL, v['y'] * MIL), d=v['size'] * MIL, hole=v['hole_size'] * MIL))
    cbp = os.path.join(ICI, 'cuivre_bloc_hacheur.json')
    if os.path.exists(cbp) and not libre:
        CB = json.load(open(cbp, encoding='utf-8'))
        for p in CB.get('pistes', []):
            objs.append(dict(t='trk', net=p[6], lay=(0 if p[5] == 'T' else 1,), a=(p[0] * MIL, p[1] * MIL), b=(p[2] * MIL, p[3] * MIL),
                             w=p[4] * MIL, wmil=p[4], exist=True))
        for v in CB.get('vias', []):
            objs.append(dict(t='via', net=v[4], lay=(0, 1, 2), c=(v[0] * MIL, v[1] * MIL), d=v[2] * MIL, hole=v[3] * MIL, exist=True))
        for r in CB.get('regions', []):
            x0, y0, x1, y1 = r[2] * MIL, r[3] * MIL, r[4] * MIL, r[5] * MIL
            objs.append(dict(t='pad', net=r[1], lay=(0 if r[0] == 'T' else 1,), f=Forme((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, False),
                             comp='<bloc>', fin=False, th=False, nom='surface du bloc', vok=True, exist=True, libre=False))

    def bbox(o):
        if o['t'] == 'pad':
            return o['f'].rect
        if o['t'] == 'trk':
            h = o['w'] / 2
            return (min(o['a'][0], o['b'][0]) - h, min(o['a'][1], o['b'][1]) - h, max(o['a'][0], o['b'][0]) + h, max(o['a'][1], o['b'][1]) + h)
        h = o['d'] / 2
        return (o['c'][0] - h, o['c'][1] - h, o['c'][0] + h, o['c'][1] + h)

    def gap(o, q):
        """distance cuivre-cuivre (bord a bord)"""
        order = {'pad': 0, 'trk': 1, 'via': 2}
        if order[o['t']] > order[q['t']]:
            o, q = q, o
        if o['t'] == 'pad' and q['t'] == 'pad':
            return o['f'].d_forme(q['f'])
        if o['t'] == 'pad' and q['t'] == 'trk':
            return o['f'].d_seg(q['a'], q['b']) - q['w'] / 2
        if o['t'] == 'pad' and q['t'] == 'via':
            return o['f'].d_pt(*q['c']) - q['d'] / 2
        if o['t'] == 'trk' and q['t'] == 'trk':
            return d_seg_seg(o['a'], o['b'], q['a'], q['b']) - o['w'] / 2 - q['w'] / 2
        if o['t'] == 'trk' and q['t'] == 'via':
            return d_pt_seg(q['c'][0], q['c'][1], *o['a'], *o['b']) - o['w'] / 2 - q['d'] / 2
        return math.hypot(o['c'][0] - q['c'][0], o['c'][1] - q['c'][1]) - o['d'] / 2 - q['d'] / 2

    def contact(o, q):
        """contact electrique (meme reseau, couche commune deja verifiee).
        mode 'altium' : recouvrement de cuivre ; mode 'strict' : extremite d'axe dans le cuivre."""
        # cuivre preexistant contre cuivre preexistant (pastilles, cuivre du bloc deja valide par Altium, surfaces
        # comprises) : recouvrement. Cuivre nouveau : jamais relie par une surface du bloc (forme reelle inconnue) ;
        # mode 'altium' = recouvrement, mode strict = extremite d'axe dans le cuivre.
        pre_o = o['t'] == 'pad' or o.get('exist')
        pre_q = q['t'] == 'pad' or q.get('exist')
        if pre_o and pre_q:
            return gap(o, q) <= 1e-6
        if (o['t'] == 'pad' and o.get('exist')) or (q['t'] == 'pad' and q.get('exist')):
            return False
        if MODE == 'altium':
            return gap(o, q) <= 1e-6
        if o['t'] == 'trk' or q['t'] == 'trk':
            if o['t'] != 'trk':
                o, q = q, o
            pts = (o['a'], o['b'])
            if q['t'] == 'pad':
                if any(q['f'].d_pt(*p) <= 1e-6 for p in pts):
                    return True
            elif q['t'] == 'via':
                if any(math.hypot(p[0] - q['c'][0], p[1] - q['c'][1]) <= q['d'] / 2 + 1e-6 for p in pts):
                    return True
            else:
                if any(d_pt_seg(p[0], p[1], *q['a'], *q['b']) <= q['w'] / 2 + 1e-6 for p in pts):
                    return True
                if any(d_pt_seg(p[0], p[1], *o['a'], *o['b']) <= o['w'] / 2 + 1e-6 for p in (q['a'], q['b'])):
                    return True
            return False
        if o['t'] == 'via' and q['t'] == 'pad':
            return q['f'].d_pt(*o['c']) <= 1e-6
        if q['t'] == 'via' and o['t'] == 'pad':
            return o['f'].d_pt(*q['c']) <= 1e-6
        if o['t'] == 'via' and q['t'] == 'via':
            return math.hypot(o['c'][0] - q['c'][0], o['c'][1] - q['c'][1]) <= max(o['d'], q['d']) / 2
        return o['f'].d_forme(q['f']) <= 1e-6  # pastille / surface du bloc

    CELL = 1.0
    grid = collections.defaultdict(list)
    for k, o in enumerate(objs):
        b = bbox(o)
        for gx in range(int((b[0] - 0.3) // CELL), int((b[2] + 0.3) // CELL) + 1):
            for gy in range(int((b[1] - 0.3) // CELL), int((b[3] + 0.3) // CELL) + 1):
                grid[(gx, gy)].append(k)
    seen = set()
    # champ de pastilles des pieces a pas fin (M1, M2, U10)
    champ = {}
    for o in objs:
        if o['t'] == 'pad' and o.get('fin'):
            r = o['f'].rect
            c = champ.get(o['comp'])
            champ[o['comp']] = r if c is None else (min(c[0], r[0]), min(c[1], r[1]), max(c[2], r[2]), max(c[3], r[3]))
    marge_min = [9.0, '']
    viol = collections.Counter()
    derog = collections.Counter()
    exemples = collections.defaultdict(list)
    pire = {}
    ecart = {}
    par = list(range(len(objs) + 1))

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    def u(a, b):
        a, b = f(a), f(b)
        if a != b:
            par[a] = b
    for cell, ks in grid.items():
        for ii in range(len(ks)):
            a = ks[ii]
            oa = objs[a]
            for jj in range(ii + 1, len(ks)):
                b = ks[jj]
                if (a, b) in seen:
                    continue
                ob = objs[b]
                if not set(oa['lay']) & set(ob['lay']):
                    continue
                seen.add((a, b))
                if oa['net'] == ob['net']:
                    if contact(oa, ob):
                        u(a, b)
                    continue
                pre_a = oa.get('exist') or oa['t'] == 'pad'
                pre_b = ob.get('exist') or ob['t'] == 'pad'
                g = gap(oa, ob)
                if pre_a and pre_b:
                    if oa['t'] == 'pad' and ob['t'] == 'pad' and not oa.get('exist') and not ob.get('exist') and oa['comp'] != ob['comp'] \
                            and g < R['isolement']['defaut'] - 1e-6 and not oa['libre'] and not ob['libre']:
                        viol['pastille/pastille (placement)'] += 1
                        exemples['pastille/pastille (placement)'].append('%s / %s : %.3f' % (oa['nom'], ob['nom'], g))
                    continue
                for x, y in ((a, ob), (b, oa)):
                    if objs[x]['t'] == 'trk' and not objs[x].get('exist'):
                        ecart[x] = min(ecart.get(x, 9.0), g)
                # regle Altium : 0,124 mm seulement entre objets (pastilles) de M1 / M2 / U10 eux-memes ; 0,2 mm
                # (0,25 COMMUTATION) pour tout le reste, y compris piste ou via contre une pastille d'U10
                req = max(clr_net(oa['net']), clr_net(ob['net']))
                if oa['t'] == 'pad' and ob['t'] == 'pad' and oa['fin'] and ob['fin']:
                    req = R['isolement']['pastilles_pas_fin']
                if g - req < marge_min[0]:
                    marge_min[0] = g - req
                    marge_min[1] = '%s(%s) / %s(%s) : ecart %.4f mm, regle %.3f mm' % (oa.get('nom', oa['t']), oa['net'], ob.get('nom', ob['t']), ob['net'], g, req)
                if g < req - 1e-4:
                    typ = '/'.join(sorted((oa['t'], ob['t'])))
                    if (oa['t'] == 'pad' and oa['libre']) or (ob['t'] == 'pad' and ob['libre']):
                        typ += ' (pastille libre trou M3)'
                    viol[typ] += 1
                    desc = '%s(%s) / %s(%s) : %.3f < %.3f mm' % (oa.get('nom', oa['t']), oa['net'], ob.get('nom', ob['t']), ob['net'], g, req)
                    if len(exemples[typ]) < 15:
                        exemples[typ].append(desc)
                    pire[typ] = min(pire.get(typ, 9), g - req)
    # gardes, largeurs, vias, via sous CMS
    for k, o in enumerate(objs):
        if o['t'] == 'pad' or o.get('exist'):
            continue
        if o['t'] == 'trk':
            h = o['w'] / 2
            pts = (o['a'], o['b'])
            de = min(min(p[0], p[1], W - p[0], H - p[1]) for p in pts) - h
            df = min(d_seg_rect(o['a'], o['b'], (x, y, x + w, y + hh)) for x, y, w, hh in D['fentes']) - h
            mn, pf, mx = larg(o['net'])
            mn_mil = round(mn / MIL, 3)
            if o['wmil'] < mn_mil - 1e-6 or o['w'] > mx + 1e-6:
                viol['largeur hors regle'] += 1
                if len(exemples['largeur hors regle']) < 10:
                    exemples['largeur hors regle'].append('%s : %.3f mil hors [%.3f mil ; %.3f mm]' % (o['net'], o['wmil'], mn_mil, mx))
        else:
            h = o['d'] / 2
            x, y = o['c']
            de = min(x, y, W - x, H - y) - h
            df = min(d_pt_rect(x, y, (fx, fy, fx + w, fy + hh)) for fx, fy, w, hh in D['fentes']) - h
            if not (0.3 - 1e-3 <= o['d'] <= 0.8 + 1e-3 and 0.3 - 1e-3 <= o['hole'] <= 0.4 + 1e-3):
                viol['dimension de via'] += 1
            gx, gy = int(x // CELL), int(y // CELL)
            for kk in grid[(gx, gy)]:
                q = objs[kk]
                if q['t'] == 'pad' and not q['th'] and not q['vok'] and not q.get('exist') and q['f'].d_pt(x, y) < h - 1e-6:
                    if q['net'] == o['net'] and q['net'] in plans:
                        derog['via dans pastille CMS du reseau de plan (derogation declaree)'] += 1
                        break
                    viol['via sous pastille CMS'] += 1
                    exemples['via sous pastille CMS'].append('%s sous %s' % (o['net'], q['nom']))
                    break
        for nom, dd in (('garde bord', de), ('garde fente', df)):
            if dd < garde - 1e-4:
                viol[nom] += 1
                if len(exemples[nom]) < 10:
                    exemples[nom].append('%s %s : %.3f mm' % (o['t'], o['net'], dd))
    # angles
    ends = collections.defaultdict(list)
    for k, o in enumerate(objs):
        if o['t'] == 'trk' and not o.get('exist'):
            for p, q in ((o['a'], o['b']), (o['b'], o['a'])):
                ends[(round(p[0], 3), round(p[1], 3), o['lay'][0], o['net'])].append((q[0] - p[0], q[1] - p[1]))
    aigus = angle90 = coudes = 0
    for key, vs in ends.items():
        if len(vs) == 2:
            (ax, ay), (bx, by) = vs
            na, nb = math.hypot(ax, ay), math.hypot(bx, by)
            if na == 0 or nb == 0:
                continue
            cos = (ax * bx + ay * by) / (na * nb)
            if cos > -0.999:
                coudes += 1
            if cos > 0.01:
                aigus += 1
            elif abs(cos) <= 0.01:
                angle90 += 1
    # connectivite
    PLAN = len(objs)
    pads_by_net = collections.defaultdict(list)
    cuivre_by_net = collections.defaultdict(list)   # pistes et vias poses (ilots compris, comme le chevelu d'Altium)
    for k, o in enumerate(objs):
        if o['t'] == 'pad' and not o.get('exist') and not o['net'].startswith('<sans>'):
            pads_by_net[o['net']].append(k)
        elif o['t'] in ('trk', 'via') and not o.get('exist'):
            cuivre_by_net[o['net']].append(k)
    for k, o in enumerate(objs):
        if o['net'] in plans and (o['t'] == 'via' or (o['t'] == 'pad' and o['th'])):
            u(k, PLAN)
    if '--bloc-relie' in sys.argv:   # ancienne hypothese (iterations 1 a 3) : pastilles du bloc deja reliees
        for n, ks in pads_by_net.items():
            kb = [k for k in ks if objs[k]['comp'] in bloc]
            for k in kb[1:]:
                u(kb[0], k)
    ouvertes = []
    complets = 0
    a_router = 0
    internes_bloc = []
    for n, ks in pads_by_net.items():
        # reseaux entierement internes au bloc (cuivre existant deja valide par Altium, non reroute) : comptes a part
        if '--bloc-juge' not in sys.argv and n not in plans and all(objs[k]['comp'] in bloc for k in ks) and not cuivre_by_net[n]:
            internes_bloc.append(n)
            continue
        if n in plans:
            ks2 = ks + [PLAN] + cuivre_by_net[n]
        else:
            ks2 = ks + cuivre_by_net[n]
        if len(ks) < 2 and n not in plans:
            continue
        a_router += 1
        morceaux = collections.defaultdict(list)
        for k in ks2:
            morceaux[f(k)].append(k)
        if len(morceaux) == 1:
            complets += 1
        else:
            # pour chaque morceau sans la reference, une liaison ouverte
            ref = f(PLAN) if n in plans else max(morceaux, key=lambda r: len(morceaux[r]))
            for r, mem in morceaux.items():
                if r == ref:
                    continue
                noms = [objs[k]['nom'] for k in mem if k != PLAN and objs[k]['t'] == 'pad'] or ['ilot de piste/via pres de (%.2f ; %.2f)' % (
                    (objs[mem[0]]['a'] if objs[mem[0]]['t'] == 'trk' else objs[mem[0]]['c']))]
                ouvertes.append(dict(net=n, pastilles=noms))
    total_v = sum(v for k, v in viol.items() if k != 'pastille/pastille (placement)')
    ec = [min(v, 1.0) for v in ecart.values()]
    puiss = ('ENTREE_12V', 'CANAL_2A', 'RAILS', 'PUISSANCE_8V')
    res_p = sum(math.hypot(o['b'][0] - o['a'][0], o['b'][1] - o['a'][1]) / o['w'] * 0.5 for o in objs
                if o['t'] == 'trk' and not o.get('exist') and classes.get(o['net']) in puiss)
    long_tot = sum(math.hypot(o['b'][0] - o['a'][0], o['b'][1] - o['a'][1]) for o in objs if o['t'] == 'trk' and not o.get('exist'))
    res = dict(mode_connexion=MODE, source_pastilles=source_pads, violations_routage=total_v, violations_par_type=dict(viol), derogations=dict(derog),
               pire_ecart_mm=pire, exemples={k: v[:15] for k, v in exemples.items()}, angles_aigus=aigus, coudes_90=angle90, coudes=coudes,
               marge_isolement_min_mm=marge_min[0], marge_isolement_min_paire=marge_min[1],
               liaisons_ouvertes=len(ouvertes), detail_liaisons_ouvertes=ouvertes, reseaux_internes_bloc_non_juges=internes_bloc,
               reseaux_a_router=a_router, reseaux_complets=complets, taux_completion=complets / max(1, a_router),
               ecart_moyen_mm=sum(ec) / max(1, len(ec)), ecart_min_mm=min(ec) if ec else None,
               resistance_puissance_mohm=res_p, longueur_pistes_mm=long_tot)
    json.dump(res, open(out_path, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    print('pastilles : %s' % source_pads)
    print('DRC maison : %d violation(s) de routage ; par type : %s ; derogations : %s' % (total_v, dict(viol), dict(derog)))
    print('marge d isolement minimale (ecart - regle applicable) : %.4f mm [%s]' % (marge_min[0], marge_min[1]))
    print('liaisons ouvertes (mode %s) : %d ; reseaux complets %d / %d' % (MODE, len(ouvertes), complets, a_router))
    print('angles aigus %d ; coudes a 90 degres %d ; longueur %.1f mm ; ecart moyen %.3f / min %.3f mm ; resistance puissance %.1f mOhm' % (
        aigus, angle90, long_tot, sum(ec) / max(1, len(ec)), min(ec) if ec else -1, res_p))
    for k, v in exemples.items():
        print('  ' + k + ' : ' + ' | '.join(v[:3]))
    print('-> ' + out_path)


if __name__ == '__main__':
    main()
