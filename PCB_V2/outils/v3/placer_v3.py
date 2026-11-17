# -*- coding: utf-8 -*-
"""Optimiseur de placement V3 (hors ligne).  Lancer : python -I placer_v3.py [nb_moves] [nb_graines]

Etapes : chargement + derivation geometrie locale (verifiee), cout du placement actuel,
placement constructif, recuit simule (deplacement / cible / echange / rotation / face / bloc)
avec cout incremental et grille spatiale, compactage glouton, plusieurs graines en parallele,
controle independant des contraintes, ecriture des sorties.
"""
import json, math, random, time, sys, os, collections

MIL = 0.0254
OUT = os.path.dirname(os.path.abspath(__file__))
# donnees d'entree (copies dans le depot : le controle doit tourner sur n'importe quelle machine)
F_COMP = os.path.join(OUT, 'donnees', 'composants.json')
F_PADS = os.path.join(OUT, 'donnees', 'pastilles.json')
F_PLAN = os.path.join(OUT, 'donnees', 'plan_placement_v3.json')

PIECE_MARGIN = 0.25      # mm par cote
TH_MARGIN = 0.6          # mm autour des pastilles traversantes
SMA_TOP = 6.0
VIA_PEN = 2.0
DEC_LIM = 4.0
DEC_W = 25.0             # mm de cout par mm au-dela de la limite
DEC_PULL = 1.0           # attraction douce
SW_SOFT_W = 5.0
SW_HARD = 30.0
SNAP = 5 * MIL           # grille d'origine 5 mil
GRID = 2.0

W3 = ['+12V', 'NetC19_1', 'NetR62_ALIM1_1', 'NetR62_ALIM2_1', 'NetD16_ALIM1_2', 'NetD16_ALIM2_2',
      'OUT_CH1', 'OUT_CH2', 'Vsense_ALIM1', 'Vsense_ALIM2', 'NetR101_ALIM1_1', 'NetR101_ALIM2_1',
      'NetR104_ALIM1_1', 'NetR104_ALIM2_1', 'NetR3_2', 'NetQ3_3', 'NetR12_1']
W0 = ['GND', '+8V']


def is_rail(n):
    return n in ('+5V', '-5V', '+10V', '+8V', '+12V') or n.endswith('Vref')


# Convention dessous : la consigne donnait monde = O + R(rot)(-lx, ly) (miroir X).  Les donnees
# Altium la contredisent (erreur 3 mm sur les empreintes presentes des deux cotes) et suivent
# exactement monde = O + R(rot)(lx, -ly) (miroir Y), soit la consigne avec rot + 180.
# CONV = 'Y' (verifiee sur les donnees, utilisee) ou 'X' (consigne, pour comparaison).
CONV = 'Y'


def fwd(lx, ly, rot, bottom):
    if bottom and CONV == 'X':
        vx, vy = -lx, ly
    elif bottom:
        vx, vy = lx, -ly
    else:
        vx, vy = lx, ly
    if rot == 0:
        return vx, vy
    if rot == 90:
        return -vy, vx
    if rot == 180:
        return -vx, -vy
    return vy, -vx


def inv(dx, dy, rot, bottom):
    if rot == 0:
        u, v = dx, dy
    elif rot == 90:
        u, v = dy, -dx
    elif rot == 180:
        u, v = -dx, -dy
    else:
        u, v = -dy, dx
    if bottom and CONV == 'X':
        return -u, v
    if bottom:
        return u, -v
    return u, v


def snap(v):
    return round(v / SNAP) * SNAP


# ----------------------------------------------------------------------------- chargement
def load():
    C = json.load(open(F_COMP, encoding='utf-8'))['components']
    P = json.load(open(F_PADS, encoding='utf-8'))['objects']
    plan = json.load(open(F_PLAN, encoding='utf-8'))
    comps, idx = [], {}
    for c in C:
        d = dict(name=c['designator'], fp=c['footprint'], bottom=(c['layer'] == 'BottomLayer'),
                 rot=int(round(c['rotation'])) % 360, ox=c['x'] * MIL, oy=c['y'] * MIL,
                 h=c['height_mils'] * MIL, pads=[])
        b = c['bbox_no_name_comment']
        cs = [inv(X * MIL - d['ox'], Y * MIL - d['oy'], d['rot'], d['bottom'])
              for X in (b['x1'], b['x2']) for Y in (b['y1'], b['y2'])]
        d['lbb'] = (min(p[0] for p in cs), min(p[1] for p in cs), max(p[0] for p in cs), max(p[1] for p in cs))
        d['wbb'] = (b['x1'] * MIL, b['y1'] * MIL, b['x2'] * MIL, b['y2'] * MIL)
        idx[d['name']] = len(comps)
        comps.append(d)
    for q in P:
        c = comps[idx[q['Component']]]
        X, Y = float(q['X']) * MIL, float(q['Y']) * MIL
        lx, ly = inv(X - c['ox'], Y - c['oy'], c['rot'], c['bottom'])
        w = (float(q['BoundsRight']) - float(q['BoundsLeft'])) * MIL
        h = (float(q['BoundsTop']) - float(q['BoundsBottom'])) * MIL
        if c['rot'] in (90, 270):
            w, h = h, w
        c['pads'].append(dict(name=q['Name'], net=q['Net'], lx=lx, ly=ly, hw=w / 2, hh=h / 2,
                              th=float(q['HoleSize']) > 0, wx=X, wy=Y))
    return comps, idx, plan


def verify_geometry(comps):
    """Coherence par empreinte : on reconstruit les pastilles de chaque instance avec la geometrie
    locale de l'instance de reference de la meme empreinte (dessus et dessous melanges)."""
    ref = {}
    for c in comps:
        ref.setdefault(c['fp'], c)
    worst, worst_name, nmix = 0.0, None, 0
    fps_mixed = set()
    byfp = collections.defaultdict(set)
    for c in comps:
        byfp[c['fp']].add(c['bottom'])
    for c in comps:
        r = ref[c['fp']]
        if len(r['pads']) != len(c['pads']):
            return 99.0, c['name'] + ' (nb pastilles)', 0
        for p in c['pads']:
            best = 1e9
            for q in r['pads']:
                if q['name'] != p['name']:
                    continue
                dx, dy = fwd(q['lx'], q['ly'], c['rot'], c['bottom'])
                e = math.hypot(c['ox'] + dx - p['wx'], c['oy'] + dy - p['wy'])
                best = min(best, e)
            if best > worst:
                worst, worst_name = best, c['name'] + '.' + p['name']
    mixed = [fp for fp, s in byfp.items() if len(s) == 2]
    return worst, worst_name, mixed


# ----------------------------------------------------------------------------- modele
class Model:
    def __init__(self, comps, idx, plan):
        self.comps, self.idx, self.plan = comps, idx, plan
        self.W, self.H = plan['contour']['w'], plan['contour']['h']
        self.EM = plan['marge_bord']
        n = len(comps)
        fixed_same = set(plan['fixes_inchanges'])
        fixed_moved = plan['fixes_deplaces']
        bl = plan['bloc_rigide_hacheur']
        self.block_names = bl['dessus'] + bl['dessous']
        self.block = [idx[x] for x in self.block_names]
        self.fixed = set(idx[x] for x in fixed_same) | set(idx[x] for x in fixed_moved)
        self.fixed_moved = {idx[k]: v for k, v in fixed_moved.items()}
        self.mobile = [i for i in range(n) if i not in self.fixed and i not in self.block]
        self.is_mobile = [False] * n
        for i in self.mobile:
            self.is_mobile[i] = True
        e = bl['cuivre_emprise_mils_actuelle']
        self.emp0 = (e[0] * MIL, e[1] * MIL, e[2] * MIL, e[3] * MIL)
        self.M1 = idx['M1']
        self.U25 = [idx['U25_VOLT1'], idx['U25_VOLT2']]
        self.soft_sw = [idx[x] for x in ('U23_ALIM1', 'U23_ALIM2', 'U11_ALIM1', 'U11_ALIM2') if x in idx]
        # radiateurs : colonne centrale
        m4 = [idx['M4_ALIM1'], idx['M4_ALIM2']]
        # pose de depart (actuelle)
        self.ox = [c['ox'] for c in comps]
        self.oy = [c['oy'] for c in comps]
        self.rot = [c['rot'] for c in comps]
        self.bot = [c['bottom'] for c in comps]
        self.orient_cache = {}
        # regles de face / rotation
        top_only_fp = ['BOURNS TRIMPOT 3224W', 'ONSC-TO-92-3-29-11', 'TO-220', 'CAP ALSMD 6.6MM H7.7MM']
        self.allowed = {}
        self.rules = {}
        for i in self.mobile:
            c = comps[i]
            th = any(p['th'] for p in c['pads'])
            if c['name'] in ('C32_ALIM1', 'C32_ALIM2'):
                al = [(90, True), (270, True)]
                why = 'dessous seulement'
            elif c['fp'] in top_only_fp or th or (c['h'] > 3.0 and c['name'] not in ('C32_ALIM1', 'C32_ALIM2', 'C17', 'C20')):
                al = [(r, False) for r in (0, 90, 180, 270)]
                why = 'dessus seulement (' + ('liste' if c['fp'] in top_only_fp else ('traversant' if th else 'hauteur>3mm')) + ')'
            else:
                al = [(r, False) for r in (0, 90, 180, 270)]
                if len(c['pads']) > 2:
                    al += [(0, True), (180, True)]
                else:
                    al += [(r, True) for r in (0, 90, 180, 270)]
                why = 'libre'
            self.allowed[i] = al
            self.rules[i] = why
        self.has_th = [any(p['th'] for p in c['pads']) for c in comps]
        # nouvelles poses des fixes deplaces
        for i, v in self.fixed_moved.items():
            self.ox[i] = v['origine'][0]
            self.oy[i] = v['origine'][1]
        self.bdx = 0.0
        self.bdy = 0.0
        self.block_base = {i: (comps[i]['ox'], comps[i]['oy']) for i in self.block}
        self.PX = [None] * n
        self.PY = [None] * n
        for i in range(n):
            self.update_pads(i)
        self.build_nets()

    # -- geometrie
    def orient(self, i, rot, bottom):
        key = (i, rot, bottom)
        r = self.orient_cache.get(key)
        if r is None:
            c = self.comps[i]
            pads = []
            for p in c['pads']:
                dx, dy = fwd(p['lx'], p['ly'], rot, bottom)
                hw, hh = (p['hh'], p['hw']) if rot in (90, 270) else (p['hw'], p['hh'])
                pads.append((dx, dy, hw, hh, p['th']))
            x1, y1, x2, y2 = c['lbb']
            cs = [fwd(x, y, rot, bottom) for x in (x1, x2) for y in (y1, y2)]
            bb = (min(p[0] for p in cs), min(p[1] for p in cs), max(p[0] for p in cs), max(p[1] for p in cs))
            r = (pads, bb)
            self.orient_cache[key] = r
        return r

    def update_pads(self, i):
        pads, _ = self.orient(i, self.rot[i], self.bot[i])
        ox, oy = self.ox[i], self.oy[i]
        self.PX[i] = [ox + p[0] for p in pads]
        self.PY[i] = [oy + p[1] for p in pads]

    def rects_of(self, i, rot, bottom, ox, oy):
        """(face, rect) occupes par la piece : boite gonflee sur sa face, pastilles traversantes +0.6 sur l'autre."""
        pads, bb = self.orient(i, rot, bottom)
        m = PIECE_MARGIN
        f = 1 if bottom else 0
        out = [(f, (ox + bb[0] - m, oy + bb[1] - m, ox + bb[2] + m, oy + bb[3] + m))]
        if self.has_th[i]:
            for dx, dy, hw, hh, th in pads:
                if th:
                    out.append((1 - f, (ox + dx - hw - TH_MARGIN, oy + dy - hh - TH_MARGIN,
                                        ox + dx + hw + TH_MARGIN, oy + dy + hh + TH_MARGIN)))
        return out

    def block_rects(self, dx, dy):
        e = self.emp0
        out = [(0, (e[0] + dx, e[1] + dy, e[2] + dx, e[3] + dy)), (1, (e[0] + dx, e[1] + dy, e[2] + dx, e[3] + dy))]
        m = PIECE_MARGIN
        for i in self.block:
            c = self.comps[i]
            b = c['wbb']
            out.append((1 if c['bottom'] else 0, (b[0] + dx - m, b[1] + dy - m, b[2] + dx + m, b[3] + dy + m)))
        return out

    def set_block(self, dx, dy):
        self.bdx, self.bdy = dx, dy
        for i in self.block:
            bx, by = self.block_base[i]
            self.ox[i] = bx + dx
            self.oy[i] = by + dy
            self.update_pads(i)

    def sw_node(self):
        b = self.comps[self.M1]['wbb']
        return ((b[0] + b[2]) / 2 + self.bdx, (b[1] + b[3]) / 2 + self.bdy)

    def static_obstacles(self):
        """liste de (face, rect, etiquette) des obstacles fixes"""
        pl = self.plan
        obs = []
        mf = pl['marge_fente']
        for k, (x, y, w, h) in enumerate(pl['fentes_radiateurs']):
            for f in (0, 1):
                obs.append((f, (x - mf, y - mf, x + w + mf, y + h + mf), 'fente%d' % (k + 1)))
        r = pl['rayon_interdit_trou_M3']
        for k, (x, y) in enumerate(pl['trous_M3']):
            for f in (0, 1):
                obs.append((f, (x - r, y - r, x + r, y + r), 'trou%d' % (k + 1)))
        for i in sorted(self.fixed):
            c = self.comps[i]
            pads, bb = self.orient(i, self.rot[i], self.bot[i])
            ox, oy = self.ox[i], self.oy[i]
            f = 1 if self.bot[i] else 0
            obs.append((f, (ox + bb[0], oy + bb[1], ox + bb[2], oy + bb[3]), c['name']))
            for dx, dy, hw, hh, th in pads:
                if th:
                    obs.append((1 - f, (ox + dx - hw - TH_MARGIN, oy + dy - hh - TH_MARGIN,
                                        ox + dx + hw + TH_MARGIN, oy + dy + hh + TH_MARGIN), c['name'] + '(pastille TH)'))
            if c['fp'].startswith('ADAMTECH'):
                cx, cy = ox + (bb[0] + bb[2]) / 2, oy + (bb[1] + bb[3]) / 2
                obs.append((0, (cx - SMA_TOP, cy - SMA_TOP, cx + SMA_TOP, cy + SMA_TOP), c['name'] + '(zone SMA)'))
        return obs

    def u25_pins(self):
        out = []
        for i in self.U25:
            for k, p in enumerate(self.comps[i]['pads']):
                try:
                    num = int(p['name'])
                except ValueError:
                    continue
                if 27 <= num <= 36:
                    out.append((self.PX[i][k], self.PY[i][k]))
        return out

    # -- reseaux
    def build_nets(self):
        comps = self.comps
        refs = collections.defaultdict(list)
        for i, c in enumerate(comps):
            for k, p in enumerate(c['pads']):
                if p['net']:
                    refs[p['net']].append((i, k))
        self.net_names = sorted(refs)
        self.nets = []
        part_nets = collections.defaultdict(set)
        self.decaps = []
        for n in self.net_names:
            R = refs[n]
            w = 0 if n in W0 else (3 if n in W3 else 1)
            mode = 'rail' if (is_rail(n) and len(R) > 8) else 'hpwl'
            passive = [(i, k) for i, k in R if comps[i]['name'][0] in 'RCLD']
            active = [(i, k) for i, k in R if comps[i]['name'][0] not in 'RCLD']
            if mode == 'rail' and (not active or not passive):
                mode = 'hpwl'
            ics = [(i, k) for i, k in R if comps[i]['name'][0] == 'U']
            decs = []
            if is_rail(n) and ics:
                for i, k in R:
                    c = comps[i]
                    if len(c['pads']) == 2 and c['name'][0] in 'C' and i not in self.block:
                        other = c['pads'][1 - k]['net']
                        if other == 'GND':
                            decs.append((i, k))
            smd = [(i, k) for i, k in R if not comps[i]['pads'][k]['th']]
            nd = dict(name=n, w=w, refs=R, mode=mode, passive=passive, active=active, ics=ics, decs=decs, smd=smd)
            self.nets.append(nd)
            if w > 0 or decs:
                for i, k in R:
                    part_nets[i].add(len(self.nets) - 1)
            for i, k in decs:
                self.decaps.append((i, n))
        self.part_nets = {i: sorted(v) for i, v in part_nets.items()}
        for i in range(len(comps)):
            self.part_nets.setdefault(i, [])
        self.net_cost_cache = [self.net_cost(j) for j in range(len(self.nets))]

    def net_cost(self, j):
        N = self.nets[j]
        PX, PY = self.PX, self.PY
        cost = 0.0
        w = N['w']
        if w > 0:
            if N['mode'] == 'hpwl':
                R = N['refs']
                i, k = R[0]
                x1 = x2 = PX[i][k]
                y1 = y2 = PY[i][k]
                for i, k in R:
                    x = PX[i][k]
                    y = PY[i][k]
                    if x < x1: x1 = x
                    elif x > x2: x2 = x
                    if y < y1: y1 = y
                    elif y > y2: y2 = y
                base = x2 - x1 + y2 - y1
            else:
                A = [(PX[i][k], PY[i][k]) for i, k in N['active']]
                base = 0.0
                for i, k in N['passive']:
                    x = PX[i][k]
                    y = PY[i][k]
                    best = 1e9
                    for ax, ay in A:
                        d = abs(ax - x) + abs(ay - y)
                        if d < best:
                            best = d
                    base += best
                xs = [a[0] for a in A]
                ys = [a[1] for a in A]
                base += max(xs) - min(xs) + max(ys) - min(ys)
            cost += w * base
            t = b = False
            bot = self.bot
            for i, k in N['smd']:
                if bot[i]:
                    b = True
                else:
                    t = True
                if t and b:
                    cost += VIA_PEN
                    break
        if N['decs']:
            I = [(PX[i][k], PY[i][k]) for i, k in N['ics']]
            for i, k in N['decs']:
                x = PX[i][k]
                y = PY[i][k]
                best = min((ax - x) ** 2 + (ay - y) ** 2 for ax, ay in I) ** 0.5
                cost += DEC_PULL * best
                if best > DEC_LIM - 0.2:
                    cost += DEC_W * (best - DEC_LIM + 0.2)
        return cost

    def sw_cost(self):
        sx, sy = self.sw_node()
        c = 0.0
        for i in self.soft_sw:
            b = self.orient(i, self.rot[i], self.bot[i])[1]
            cx = self.ox[i] + (b[0] + b[2]) / 2
            cy = self.oy[i] + (b[1] + b[3]) / 2
            d = math.hypot(cx - sx, cy - sy)
            if d < SW_HARD:
                c += SW_SOFT_W * (SW_HARD - d)
        return c

    def total_cost(self):
        return sum(self.net_cost(j) for j in range(len(self.nets))) + self.sw_cost()

    # -- mesures pour le rapport
    def metrics(self):
        PX, PY = self.PX, self.PY
        plain = {}
        bif = 0
        for N in self.nets:
            if N['name'] in W0:
                continue
            xs = [PX[i][k] for i, k in N['refs']]
            ys = [PY[i][k] for i, k in N['refs']]
            plain[N['name']] = max(xs) - min(xs) + max(ys) - min(ys)
            faces = set(self.bot[i] for i, k in N['smd'])
            if len(faces) == 2:
                bif += 1
        weighted = sum(N['w'] * plain.get(N['name'], 0.0) for N in self.nets)
        dec_bad = []
        for N in self.nets:
            if not N['decs']:
                continue
            I = [(PX[i][k], PY[i][k]) for i, k in N['ics']]
            for i, k in N['decs']:
                d = min(math.hypot(a - PX[i][k], b - PY[i][k]) for a, b in I)
                if d > DEC_LIM:
                    dec_bad.append((self.comps[i]['name'], N['name'], round(d, 2)))
        return dict(hpwl=sum(plain.values()), hpwl_w=weighted, objective=self.total_cost(), bifaces=bif,
                    nets=plain, n_decaps=len(self.decaps), dec_bad=dec_bad)


# ----------------------------------------------------------------------------- grille spatiale
class Grid:
    def __init__(self, W, H):
        self.NX = int(math.ceil(W / GRID)) + 1
        self.NY = int(math.ceil(H / GRID)) + 1
        self.cells = [[set() for _ in range(self.NX * self.NY)] for _ in (0, 1)]
        self.items = []   # [face, rect, owner]

    def _cells(self, r):
        NX, NY = self.NX, self.NY
        cx1 = max(0, int(r[0] / GRID)); cx2 = min(NX - 1, int(r[2] / GRID))
        cy1 = max(0, int(r[1] / GRID)); cy2 = min(NY - 1, int(r[3] / GRID))
        return [cx * NY + cy for cx in range(cx1, cx2 + 1) for cy in range(cy1, cy2 + 1)]

    def add(self, face, r, owner):
        it = len(self.items)
        self.items.append([face, r, owner])
        cl = self.cells[face]
        for c in self._cells(r):
            cl[c].add(it)
        return it

    def move(self, it, r, nface=None):
        face, old, owner = self.items[it]
        cl = self.cells[face]
        for c in self._cells(old):
            cl[c].discard(it)
        if nface is not None:
            face = nface
            self.items[it][0] = face
            cl = self.cells[face]
        self.items[it][1] = r
        for c in self._cells(r):
            cl[c].add(it)

    def free(self, face, r, excl):
        items = self.items
        cl = self.cells[face]
        x1, y1, x2, y2 = r
        seen = set()
        for c in self._cells(r):
            for it in cl[c]:
                if it in seen:
                    continue
                seen.add(it)
                f, q, o = items[it]
                if o in excl:
                    continue
                if x1 < q[2] and q[0] < x2 and y1 < q[3] and q[1] < y2:
                    return False
        return True

    def blockers(self, face, r, excl):
        items = self.items
        cl = self.cells[face]
        x1, y1, x2, y2 = r
        out = set()
        for c in self._cells(r):
            for it in cl[c]:
                f, q, o = items[it]
                if o in excl:
                    continue
                if x1 < q[2] and q[0] < x2 and y1 < q[3] and q[1] < y2:
                    out.add(o)
        return out


def ov(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# ----------------------------------------------------------------------------- optimiseur
class Placer:
    BLOCK = -2
    STATIC = -1

    def __init__(self, M, seed):
        self.M = M
        self.rng = random.Random(seed)
        self.grid = Grid(M.W, M.H)
        for f, r, lab in M.static_obstacles():
            self.grid.add(f, r, self.STATIC)
        self.items_of = {}
        self.placed = set()
        lo = M.EM
        self.lims = (lo, lo, M.W - lo, M.H - lo)
        # colonne centrale = entre les boites des radiateurs
        m4a = M.idx['M4_ALIM1']; m4b = M.idx['M4_ALIM2']
        ba = M.orient(m4a, M.rot[m4a], M.bot[m4a])[1]
        bb = M.orient(m4b, M.rot[m4b], M.bot[m4b])[1]
        self.col = (M.ox[m4a] + ba[2], M.ox[m4b] + bb[0])
        self.u25p = M.u25_pins()

    def in_board(self, r):
        L = self.lims
        return r[0] >= L[0] and r[1] >= L[1] and r[2] <= L[2] and r[3] <= L[3]

    def legal(self, i, rot, bot, ox, oy, excl):
        rs = self.M.rects_of(i, rot, bot, ox, oy)
        if not self.in_board(rs[0][1]):
            return None
        g = self.grid
        for f, r in rs:
            if not g.free(f, r, excl):
                return None
        return rs

    def insert(self, i, rs):
        self.items_of[i] = [self.grid.add(f, r, i) for f, r in rs]
        self.placed.add(i)

    def reinsert(self, i, rs):
        its = self.items_of[i]
        g = self.grid
        assert len(its) == len(rs)
        for it, (f, r) in zip(its, rs):
            g.move(it, r, f)

    # -- bloc
    def block_ok(self, dx, dy):
        M = self.M
        rs = M.block_rects(dx, dy)
        e = rs[0][1]
        if e[0] < self.col[0] or e[2] > self.col[1]:
            return None
        for f, r in rs:
            if not self.in_board(r):
                return None
            if not self.grid.free(f, r, (self.BLOCK,)):
                return None
        # SW >= 30 mm des broches 27..36 des U25
        b = M.comps[M.M1]['wbb']
        sx, sy = (b[0] + b[2]) / 2 + dx, (b[1] + b[3]) / 2 + dy
        for px, py in self.u25p:
            if math.hypot(px - sx, py - sy) < SW_HARD:
                return None
        return rs

    def place_block_initial(self):
        M = self.M
        # chercher la translation legale la plus proche de P1 (cuivre +12V)
        p1 = M.idx['P1']
        P1pads = [(M.PX[p1][k], M.PY[p1][k]) for k, p in enumerate(M.comps[p1]['pads']) if p['net'] == '+12V']
        best = None
        for ix in range(-60, 61):
            for iy in range(-80, 21):
                dx, dy = ix * 0.5, iy * 0.5
                rs = self.block_ok(dx, dy)
                if rs is None:
                    continue
                e = rs[0][1]
                cx, cy = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
                d = min(math.hypot(cx - x, cy - y) for x, y in P1pads)
                if best is None or d < best[0]:
                    best = (d, dx, dy, rs)
        d, dx, dy, rs = best
        M.set_block(dx, dy)
        self.block_items = [self.grid.add(f, r, self.BLOCK) for f, r in rs]

    # -- placement constructif
    def target(self, i, placed_set):
        M = self.M
        sx = sy = sw = 0.0
        for j in M.part_nets[i]:
            N = M.nets[j]
            if N['decs'] and any(a == i for a, k in N['decs']):
                pts = [(M.PX[a][k], M.PY[a][k]) for a, k in N['ics'] if a in placed_set]
                if pts:
                    # broche de CI la plus proche de la cible actuelle : on prend la premiere libre
                    x, y = pts[self.rng.randrange(len(pts))]
                    sx += 10 * x; sy += 10 * y; sw += 10
                continue
            if N['w'] == 0:
                continue
            if N['mode'] == 'rail':
                if comps_is_passive(M, i):
                    pts = [(M.PX[a][k], M.PY[a][k]) for a, k in N['active'] if a in placed_set]
                else:
                    continue
            else:
                pts = [(M.PX[a][k], M.PY[a][k]) for a, k in N['refs'] if a in placed_set and a != i]
            if not pts:
                continue
            x = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
            y = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
            sx += N['w'] * x; sy += N['w'] * y; sw += N['w']
        if sw == 0:
            return None
        return sx / sw, sy / sw

    def find_spot(self, i, tx, ty, maxr=200.0):
        M = self.M
        al = self.M.allowed[i]
        step = 0.5
        for ring in range(0, int(maxr / step)):
            pts = []
            if ring == 0:
                pts = [(0, 0)]
            else:
                for a in range(-ring, ring + 1):
                    pts += [(a, -ring), (a, ring)]
                for b in range(-ring + 1, ring):
                    pts += [(-ring, b), (ring, b)]
            self.rng.shuffle(pts)
            pts.sort(key=lambda p: p[0] * p[0] + p[1] * p[1])
            for a, b in pts:
                cx, cy = tx + a * step, ty + b * step
                if cx < 0 or cx > M.W or cy < 0 or cy > M.H:
                    continue
                ors = list(al)
                self.rng.shuffle(ors)
                for rot, bot in ors:
                    bb = M.orient(i, rot, bot)[1]
                    ox = snap(cx - (bb[0] + bb[2]) / 2)
                    oy = snap(cy - (bb[1] + bb[3]) / 2)
                    rs = self.legal(i, rot, bot, ox, oy, (i,))
                    if rs is not None:
                        return rot, bot, ox, oy, rs
        return None

    def construct(self):
        M = self.M
        self.place_block_initial()
        placed_set = set(M.fixed) | set(M.block)
        todo = set(M.mobile)
        area = {}
        for i in todo:
            bb = M.orient(i, 0, False)[1]
            area[i] = (bb[2] - bb[0]) * (bb[3] - bb[1])
        conn = collections.defaultdict(float)

        def score(i):
            s = 0.0
            for j in M.part_nets[i]:
                N = M.nets[j]
                wt = max(N['w'], 1 if N['decs'] else 0)
                if N['mode'] == 'rail' and not N['decs']:
                    wt *= 0.2
                s += wt * sum(1 for a, k in N['refs'] if a in placed_set and a != i)
            return s
        big = [i for i in todo if area[i] > 40 or M.rules[i] != 'libre']
        order_phase = [big, None]
        while todo:
            pool = [i for i in todo if i in big] or list(todo)
            best = max(pool, key=lambda i: (score(i), area[i], -i))
            t = self.target(best, placed_set)
            if t is None:
                t = (M.W / 2, M.H * 0.6)
            r = self.find_spot(best, t[0], t[1])
            if r is None:
                raise RuntimeError('pas de place pour ' + M.comps[best]['name'])
            rot, bot, ox, oy, rs = r
            M.rot[best], M.bot[best], M.ox[best], M.oy[best] = rot, bot, ox, oy
            M.update_pads(best)
            self.insert(best, rs)
            placed_set.add(best)
            todo.discard(best)
            if best in big:
                big.remove(best)
        M.net_cost_cache = [M.net_cost(j) for j in range(len(M.nets))]

    # -- reparation des decouplages (recherche exhaustive autour des broches, avec ejection)
    def _apply(self, i, rot, bot, ox, oy, rs):
        M = self.M
        M.ox[i], M.oy[i], M.rot[i], M.bot[i] = ox, oy, rot, bot
        M.update_pads(i)
        self.reinsert(i, rs)

    def _lift(self, i):
        for it in self.items_of[i]:
            self.grid.move(it, (-100.0, -100.0, -99.0, -99.0))

    def repair_decaps(self, log=None):
        M = self.M
        fixed_ok, failed = [], []
        for (i, netname) in M.decaps:
            if not M.is_mobile[i]:
                continue
            j = M.net_names.index(netname)
            N = M.nets[j]
            k = [kk for a, kk in N['decs'] if a == i][0]

            def dist():
                return min(math.hypot(M.PX[a][b] - M.PX[i][k], M.PY[a][b] - M.PY[i][k]) for a, b in N['ics'])
            if dist() <= DEC_LIM - 0.2:
                continue
            base = M.total_cost()
            base_other = base - sum(M.net_cost(jj) for jj in M.part_nets[i])
            pins = [(M.PX[a][b], M.PY[a][b]) for a, b in N['ics']]
            sv = (M.ox[i], M.oy[i], M.rot[i], M.bot[i])
            best = None
            cand_blocked = []
            for (px, py) in pins:
                for rot, bot in M.allowed[i]:
                    pads, bb = M.orient(i, rot, bot)
                    for ix in range(-15, 16):
                        for iy in range(-15, 16):
                            dx, dy = ix * 0.25, iy * 0.25
                            if dx * dx + dy * dy > 3.6 ** 2:
                                continue
                            ox = snap(px + dx - pads[k][0]); oy = snap(py + dy - pads[k][1])
                            rs = self.legal(i, rot, bot, ox, oy, (i,))
                            if rs is None:
                                if len(cand_blocked) < 4000 and (ix + iy) % 2 == 0:
                                    cand_blocked.append((rot, bot, ox, oy))
                                continue
                            M.ox[i], M.oy[i], M.rot[i], M.bot[i] = ox, oy, rot, bot
                            M.update_pads(i)
                            c = sum(M.net_cost(jj) for jj in M.part_nets[i]) + base_other
                            if best is None or c < best[0]:
                                best = (c, rot, bot, ox, oy, rs)
                            M.ox[i], M.oy[i], M.rot[i], M.bot[i] = sv
                            M.update_pads(i)
            if best is not None and best[0] < base:
                self._apply(i, *best[1:])
                fixed_ok.append(M.comps[i]['name'])
                continue
            # ejection : jusqu'a 3 pieces mobiles genantes, replacees au plus pres
            done = False
            self.rng.shuffle(cand_blocked)
            for rot, bot, ox, oy in cand_blocked[:400]:
                rs2 = M.rects_of(i, rot, bot, ox, oy)
                if not self.in_board(rs2[0][1]):
                    continue
                bl = set()
                for f, rr in rs2:
                    bl |= self.grid.blockers(f, rr, (i,))
                if not bl or len(bl) > 3 or any((b is None or b < 0 or not M.is_mobile[b]) for b in bl):
                    continue
                bl = list(bl)
                old = {b: (M.ox[b], M.oy[b], M.rot[b], M.bot[b]) for b in bl}
                for b in bl:
                    self._lift(b)
                rs = self.legal(i, rot, bot, ox, oy, (i,))
                if rs is None:
                    for b in bl:
                        self.reinsert(b, M.rects_of(b, *old[b][2:], old[b][0], old[b][1]))
                    continue
                svi = (M.ox[i], M.oy[i], M.rot[i], M.bot[i], self.grid.items[self.items_of[i][0]][1])
                old_rs_i = M.rects_of(i, svi[2], svi[3], svi[0], svi[1])
                self._apply(i, rot, bot, ox, oy, rs)
                ok = True
                for b in bl:
                    bbx = M.orient(b, M.rot[b], M.bot[b])[1]
                    cx = M.ox[b] + (bbx[0] + bbx[2]) / 2
                    cy = M.oy[b] + (bbx[1] + bbx[3]) / 2
                    r = self.find_spot(b, cx, cy, maxr=12.0)
                    if r is None:
                        ok = False
                        break
                    self._apply(b, *r)
                if ok and M.total_cost() < base:
                    done = True
                    fixed_ok.append(M.comps[i]['name'] + ' (ejection de ' + ', '.join(M.comps[b]['name'] for b in bl) + ')')
                    break
                # annuler
                for b in bl:
                    self._lift(b)
                self._apply(i, svi[2], svi[3], svi[0], svi[1], old_rs_i)
                for b in bl:
                    o = old[b]
                    self._apply(b, o[2], o[3], o[0], o[1], M.rects_of(b, o[2], o[3], o[0], o[1]))
            if not done:
                failed.append(M.comps[i]['name'])
        M.net_cost_cache = [M.net_cost(j) for j in range(len(M.nets))]
        return fixed_ok, failed

    # -- recuit
    def anneal(self, nmoves, t0=None, tend_ratio=1e-3, greedy=False, log=None):
        M, rng, grid = self.M, self.rng, self.grid
        mob = M.mobile
        cache = M.net_cost_cache
        swc = [M.sw_cost()]
        cost = sum(cache) + swc[0]
        part_nets = M.part_nets
        allowed = M.allowed
        soft = set(M.soft_sw)
        block_nets = sorted(set(j for i in M.block for j in part_nets[i]))

        def center(i):
            bb = M.orient(i, M.rot[i], M.bot[i])[1]
            return M.ox[i] + (bb[0] + bb[2]) / 2, M.oy[i] + (bb[1] + bb[3]) / 2

        def eval_nets(nets):
            return [M.net_cost(j) for j in nets]

        def opt_target(i):
            sx = sy = sw = 0.0
            for j in part_nets[i]:
                N = M.nets[j]
                if N['decs'] and any(a == i for a, k in N['decs']):
                    k0 = [k for a, k in N['decs'] if a == i][0]
                    x, y = M.PX[i][k0], M.PY[i][k0]
                    best = min(N['ics'], key=lambda ak: (M.PX[ak[0]][ak[1]] - x) ** 2 + (M.PY[ak[0]][ak[1]] - y) ** 2)
                    sx += 8 * M.PX[best[0]][best[1]]; sy += 8 * M.PY[best[0]][best[1]]; sw += 8
                    continue
                if N['w'] == 0:
                    continue
                if N['mode'] == 'rail':
                    if comps_is_passive(M, i):
                        pts = [(M.PX[a][k], M.PY[a][k]) for a, k in N['active']]
                        mx, my = center(i)
                        p = min(pts, key=lambda q: abs(q[0] - mx) + abs(q[1] - my))
                        sx += N['w'] * p[0]; sy += N['w'] * p[1]; sw += N['w']
                    continue
                xs = [M.PX[a][k] for a, k in N['refs'] if a != i]
                ys = [M.PY[a][k] for a, k in N['refs'] if a != i]
                if not xs:
                    continue
                sx += N['w'] * (min(xs) + max(xs)) / 2
                sy += N['w'] * (min(ys) + max(ys)) / 2
                sw += N['w']
            if sw == 0:
                return None
            return sx / sw, sy / sw

        def try_pair(i, roti, boti, ci_new, j, rotj, botj, cj_new, T):
            bi = M.orient(i, roti, boti)[1]
            bj = M.orient(j, rotj, botj)[1]
            oxi, oyi = snap(ci_new[0] - (bi[0] + bi[2]) / 2), snap(ci_new[1] - (bi[1] + bi[3]) / 2)
            oxj, oyj = snap(cj_new[0] - (bj[0] + bj[2]) / 2), snap(cj_new[1] - (bj[1] + bj[3]) / 2)
            ri = self.legal(i, roti, boti, oxi, oyi, (i, j))
            if ri is None:
                return None
            rj = self.legal(j, rotj, botj, oxj, oyj, (i, j))
            if rj is None:
                return None
            for fa, a in ri:
                for fb, b in rj:
                    if fa == fb and ov(a, b):
                        return None
            nets = sorted(set(part_nets[i]) | set(part_nets[j]))
            sv = (M.ox[i], M.oy[i], M.rot[i], M.bot[i], M.ox[j], M.oy[j], M.rot[j], M.bot[j])
            M.ox[i], M.oy[i], M.rot[i], M.bot[i] = oxi, oyi, roti, boti
            M.ox[j], M.oy[j], M.rot[j], M.bot[j] = oxj, oyj, rotj, botj
            M.update_pads(i); M.update_pads(j)
            new = eval_nets(nets)
            nsw = M.sw_cost() if (i in soft or j in soft) else swc[0]
            d = sum(new) - sum(cache[k] for k in nets) + nsw - swc[0]
            if d <= 0 or (T > 0 and rng.random() < math.exp(-d / T)):
                for k, v in zip(nets, new):
                    cache[k] = v
                swc[0] = nsw
                self.reinsert(i, ri); self.reinsert(j, rj)
                return d
            M.ox[i], M.oy[i], M.rot[i], M.bot[i], M.ox[j], M.oy[j], M.rot[j], M.bot[j] = sv
            M.update_pads(i); M.update_pads(j)
            return None

        if t0 is None:
            ds = []
            for _ in range(400):
                i = rng.choice(mob)
                cx, cy = center(i)
                nx, ny = cx + rng.gauss(0, 5), cy + rng.gauss(0, 5)
                bb = M.orient(i, M.rot[i], M.bot[i])[1]
                ox, oy = snap(nx - (bb[0] + bb[2]) / 2), snap(ny - (bb[1] + bb[3]) / 2)
                if self.legal(i, M.rot[i], M.bot[i], ox, oy, (i,)) is None:
                    continue
                so = (M.ox[i], M.oy[i])
                M.ox[i], M.oy[i] = ox, oy
                M.update_pads(i)
                d = sum(eval_nets(part_nets[i])) - sum(cache[j] for j in part_nets[i])
                M.ox[i], M.oy[i] = so
                M.update_pads(i)
                if d > 0:
                    ds.append(d)
            t0 = (sum(ds) / max(1, len(ds))) * 2.0  # 0.7 -> 6500, 2.0 -> 5971 (retenu), 4.0 -> 6023
        tend = t0 * tend_ratio
        acc = 0
        stats = collections.Counter()
        t_start = time.time()
        for it in range(nmoves):
            frac = it / nmoves
            T = 0.0 if greedy else t0 * (tend / t0) ** frac
            sig = max(0.25, 12.0 * (1 - frac) ** 2) if not greedy else 0.4
            r = rng.random()
            if r < 0.02 and not greedy or (greedy and r < 0.01):
                # ---- deplacement du bloc
                s = max(0.25, 3.0 * (1 - frac))
                ndx = snap(M.bdx + rng.gauss(0, s)); ndy = snap(M.bdy + rng.gauss(0, s))
                excl_old = (Placer.BLOCK,)
                rs = self.block_ok(ndx, ndy)
                stats['bloc_try'] += 1
                if rs is None:
                    continue
                odx, ody = M.bdx, M.bdy
                M.set_block(ndx, ndy)
                new = eval_nets(block_nets)
                nsw = M.sw_cost()
                d = sum(new) - sum(cache[j] for j in block_nets) + nsw - swc[0]
                if d <= 0 or (T > 0 and rng.random() < math.exp(-d / T)):
                    for j, v in zip(block_nets, new):
                        cache[j] = v
                    swc[0] = nsw
                    cost += d
                    for itm, (f, rr) in zip(self.block_items, rs):
                        grid.move(itm, rr)
                    stats['bloc_ok'] += 1
                else:
                    M.set_block(odx, ody)
                continue
            if r < 0.20:
                # ---- echange
                i = rng.choice(mob); j = rng.choice(mob)
                if i == j:
                    continue
                ci = center(i); cj = center(j)
                if abs(ci[0] - cj[0]) + abs(ci[1] - cj[1]) > 3 * sig + 6:
                    continue
                stats['swap_try'] += 1
                rj_ = M.rot[j]; bj_ = M.bot[j]
                if rng.random() < 0.3:
                    rj_, bj_ = rng.choice(allowed[j])
                res = try_pair(i, M.rot[i], M.bot[i], cj, j, rj_, bj_, ci, T)
                if res is not None:
                    cost += res
                    stats['swap_ok'] += 1
                continue
            # ---- deplacement simple / cible / rotation / face
            i = rng.choice(mob)
            rot, bot = M.rot[i], M.bot[i]
            cx, cy = center(i)
            if r < 0.50:
                kind = 'shift'
                nx, ny = cx + rng.gauss(0, sig), cy + rng.gauss(0, sig)
                if rng.random() < 0.15:
                    rot, bot = rng.choice(allowed[i])
            elif r < 0.78:
                kind = 'cible'
                t = opt_target(i)
                if t is None:
                    continue
                s2 = max(0.3, sig * 0.3)
                nx, ny = t[0] + rng.gauss(0, s2) + (cx - _padc(M, i)[0]), t[1] + rng.gauss(0, s2) + (cy - _padc(M, i)[1])
                if rng.random() < 0.4:
                    rot, bot = rng.choice(allowed[i])
            elif r < 0.90:
                kind = 'rot'
                al = [a for a in allowed[i] if a[1] == bot and a[0] != rot]
                if not al:
                    continue
                rot, bot = rng.choice(al)
                nx, ny = cx, cy
            else:
                kind = 'face'
                al = [a for a in allowed[i] if a[1] != bot]
                if not al:
                    continue
                pref = [a for a in al if a[0] == rot]
                rot, bot = rng.choice(pref) if pref and rng.random() < 0.6 else rng.choice(al)
                nx, ny = cx + rng.gauss(0, 0.5), cy + rng.gauss(0, 0.5)
            stats[kind + '_try'] += 1
            bb = M.orient(i, rot, bot)[1]
            ox, oy = snap(nx - (bb[0] + bb[2]) / 2), snap(ny - (bb[1] + bb[3]) / 2)
            rs = self.legal(i, rot, bot, ox, oy, (i,))
            if rs is None:
                if kind in ('cible', 'shift', 'rot') and rng.random() < 0.6:
                    # ejection : si une seule piece mobile gene, elle prend l'ancienne place de i
                    rs2 = M.rects_of(i, rot, bot, ox, oy)
                    if not self.in_board(rs2[0][1]):
                        continue
                    bl = set()
                    for f, rr in rs2:
                        bl |= grid.blockers(f, rr, (i,))
                        if len(bl) > 1:
                            break
                    if len(bl) == 1:
                        j = bl.pop()
                        if j is not None and j >= 0 and M.is_mobile[j]:
                            stats['eject_try'] += 1
                            rj_, bj_ = M.rot[j], M.bot[j]
                            if rng.random() < 0.3:
                                rj_, bj_ = rng.choice(allowed[j])
                            res = try_pair(i, rot, bot, (nx, ny), j, rj_, bj_, (cx, cy), T)
                            if res is not None:
                                cost += res
                                stats['eject_ok'] += 1
                continue
            nets = part_nets[i]
            sv = (M.ox[i], M.oy[i], M.rot[i], M.bot[i])
            M.ox[i], M.oy[i], M.rot[i], M.bot[i] = ox, oy, rot, bot
            M.update_pads(i)
            new = eval_nets(nets)
            nsw = M.sw_cost() if i in soft else swc[0]
            d = sum(new) - sum(cache[k] for k in nets) + nsw - swc[0]
            if d <= 0 or (T > 0 and rng.random() < math.exp(-d / T)):
                for k, v in zip(nets, new):
                    cache[k] = v
                swc[0] = nsw
                cost += d
                self.reinsert(i, rs)
                stats[kind + '_ok'] += 1
            else:
                M.ox[i], M.oy[i], M.rot[i], M.bot[i] = sv
                M.update_pads(i)
            if log and it % 100000 == 0:
                log('  it=%d T=%.3f cout=%.1f (%.0f s)' % (it, T, cost, time.time() - t_start))
        return cost, stats, t0


def _padc(M, i):
    xs, ys = M.PX[i], M.PY[i]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def comps_is_passive(M, i):
    return M.comps[i]['name'][0] in 'RCLD'


# ----------------------------------------------------------------------------- une graine
def run_seed(args):
    seed, nmoves = args
    comps, idx, plan = load()
    M = Model(comps, idx, plan)
    P = Placer(M, seed)
    t = time.time()
    P.construct()
    c0 = M.total_cost()
    logs = []
    cost, st, t0 = P.anneal(nmoves, log=None)
    # reparation des decouplages puis compactage glouton (T=0), deux tours
    rep = []
    for _ in range(2):
        ok, failed = P.repair_decaps()
        rep += ok
        cost2, st2, _ = P.anneal(max(50000, nmoves // 8), t0=t0, greedy=True)
    cfinal = M.total_cost()
    res = dict(seed=seed, c_construct=c0, c_sa=cost, c_final=cfinal, t0=t0, time=time.time() - t,
               repaired=rep, dec_failed=failed,
               stats=dict(st), stats_greedy=dict(st2),
               pose={M.comps[i]['name']: (M.ox[i], M.oy[i], M.rot[i], M.bot[i]) for i in M.mobile},
               block=(M.bdx, M.bdy))
    return res


# ----------------------------------------------------------------------------- controle independant
def independent_check(comps, idx, plan, poses, block_delta):
    """Recalcule tout depuis les poses de sortie, sans grille, par comparaison deux a deux."""
    W, H = plan['contour']['w'], plan['contour']['h']
    em = plan['marge_bord']
    fixed_same = set(plan['fixes_inchanges'])
    blk = plan['bloc_rigide_hacheur']
    blk_names = blk['dessus'] + blk['dessous']
    pose = {}
    for c in comps:
        n = c['name']
        if n in fixed_same:
            pose[n] = (c['ox'], c['oy'], c['rot'], c['bottom'])
        elif n in plan['fixes_deplaces']:
            o = plan['fixes_deplaces'][n]['origine']
            pose[n] = (o[0], o[1], c['rot'], c['bottom'])
        elif n in blk_names:
            pose[n] = (c['ox'] + block_delta[0], c['oy'] + block_delta[1], c['rot'], c['bottom'])
        else:
            pose[n] = poses[n]
    viol = []

    def geo(c):
        ox, oy, rot, bot = pose[c['name']]
        pads = []
        for p in c['pads']:
            dx, dy = fwd(p['lx'], p['ly'], rot, bot)
            hw, hh = (p['hh'], p['hw']) if rot in (90, 270) else (p['hw'], p['hh'])
            pads.append((ox + dx, oy + dy, hw, hh, p['th'], p['name'], p['net']))
        x1, y1, x2, y2 = c['lbb']
        cs = [fwd(x, y, rot, bot) for x in (x1, x2) for y in (y1, y2)]
        box = (ox + min(a for a, b in cs), oy + min(b for a, b in cs), ox + max(a for a, b in cs), oy + max(b for a, b in cs))
        return pads, box, bot
    G = {c['name']: geo(c) for c in comps}
    mobile = [c['name'] for c in comps if c['name'] not in fixed_same and c['name'] not in plan['fixes_deplaces'] and c['name'] not in blk_names]
    fixed = [c['name'] for c in comps if c['name'] in fixed_same or c['name'] in plan['fixes_deplaces']]
    byname = {c['name']: c for c in comps}

    def infl(r, m):
        return (r[0] - m, r[1] - m, r[2] + m, r[3] + m)

    def ovl(a, b):
        return a[0] < b[2] - 1e-6 and b[0] < a[2] - 1e-6 and a[1] < b[3] - 1e-6 and b[1] < a[3] - 1e-6
    # obstacles (face, rect, nom)
    obs = []
    mf = plan['marge_fente']
    for k, (x, y, w, h) in enumerate(plan['fentes_radiateurs']):
        obs += [(f, (x - mf, y - mf, x + w + mf, y + h + mf), 'fente%d' % (k + 1)) for f in (0, 1)]
    rr = plan['rayon_interdit_trou_M3']
    for k, (x, y) in enumerate(plan['trous_M3']):
        obs += [(f, (x - rr, y - rr, x + rr, y + rr), 'trou M3 #%d' % (k + 1)) for f in (0, 1)]
    for n in fixed:
        pads, box, bot = G[n]
        obs.append((1 if bot else 0, box, n))
        for x, y, hw, hh, th, pn, net in pads:
            if th:
                obs.append((0 if bot else 1, (x - hw - 0.6, y - hh - 0.6, x + hw + 0.6, y + hh + 0.6), n + ' pastille ' + pn))
        if byname[n]['fp'].startswith('ADAMTECH'):
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            obs.append((0, (cx - 6, cy - 6, cx + 6, cy + 6), n + ' zone SMA'))
    e = blk['cuivre_emprise_mils_actuelle']
    emp = (e[0] * MIL + block_delta[0], e[1] * MIL + block_delta[1], e[2] * MIL + block_delta[0], e[3] * MIL + block_delta[1])
    obs += [(0, emp, 'emprise bloc'), (1, emp, 'emprise bloc')]
    for n in blk_names:
        pads, box, bot = G[n]
        obs.append((1 if bot else 0, infl(box, 0.25), 'bloc:' + n))
    # occupation des pieces mobiles
    occ = []
    for n in mobile:
        pads, box, bot = G[n]
        f = 1 if bot else 0
        occ.append((f, infl(box, 0.25), n, 'boite'))
        for x, y, hw, hh, th, pn, net in pads:
            if th:
                occ.append((1 - f, (x - hw - 0.6, y - hh - 0.6, x + hw + 0.6, y + hh + 0.6), n, 'pastille TH ' + pn))
        b = infl(box, 0.25)
        if b[0] < em - 1e-6 or b[1] < em - 1e-6 or b[2] > W - em + 1e-6 or b[3] > H - em + 1e-6:
            viol.append('%s : sort de la marge de bord %s' % (n, tuple(round(v, 2) for v in b)))
    for a in range(len(occ)):
        fa, ra, na, ka = occ[a]
        for f, r, lab in obs:
            if f == fa and ovl(ra, r):
                viol.append('%s (%s, face %s) chevauche %s' % (na, ka, 'dessous' if fa else 'dessus', lab))
        for b in range(a + 1, len(occ)):
            fb, rb, nb, kb = occ[b]
            if fa == fb and na != nb and ovl(ra, rb):
                if ka != 'boite' and kb != 'boite':
                    pass  # deux zones de pastilles TH sur la face opposee de leurs pieces : pieces sur la meme face -> deja couvert
                viol.append('%s (%s) chevauche %s (%s) face %s' % (na, ka, nb, kb, 'dessous' if fa else 'dessus'))
    # bloc vs obstacles statiques
    for n in blk_names + ['emprise']:
        rects = [(0, emp), (1, emp)] if n == 'emprise' else [((1 if G[n][2] else 0), infl(G[n][1], 0.25))]
        for f, r in rects:
            if r[0] < em - 1e-6 or r[1] < em - 1e-6 or r[2] > W - em + 1e-6 or r[3] > H - em + 1e-6:
                viol.append('bloc %s sort de la marge de bord' % n)
            for fo, ro, lab in obs:
                if fo == f and ovl(r, ro) and not lab.startswith('bloc:') and lab != 'emprise bloc':
                    viol.append('bloc %s chevauche %s' % (n, lab))
    # regles de face / rotation
    top_only_fp = ['BOURNS TRIMPOT 3224W', 'ONSC-TO-92-3-29-11', 'TO-220', 'CAP ALSMD 6.6MM H7.7MM']
    for n in mobile:
        c = byname[n]
        ox, oy, rot, bot = pose[n]
        th = any(p['th'] for p in c['pads'])
        if rot not in (0, 90, 180, 270):
            viol.append('%s rotation %s' % (n, rot))
        if n in ('C32_ALIM1', 'C32_ALIM2'):
            if not bot or rot not in (90, 270):
                viol.append('%s doit etre dessous a 90/270' % n)
            continue
        if (c['fp'] in top_only_fp or th or (c['h'] > 3.0 and n not in ('C17', 'C20'))) and bot:
            viol.append('%s doit etre dessus' % n)
        if bot and len(c['pads']) > 2 and rot not in (0, 180):
            viol.append('%s dessous rotation %d interdite' % (n, rot))
    # noeud SW
    m1 = G['M1'][1]
    sx, sy = (m1[0] + m1[2]) / 2, (m1[1] + m1[3]) / 2
    dmin = 1e9
    for u in ('U25_VOLT1', 'U25_VOLT2'):
        for x, y, hw, hh, th, pn, net in G[u][0]:
            if pn.isdigit() and 27 <= int(pn) <= 36:
                dmin = min(dmin, math.hypot(x - sx, y - sy))
    if dmin < 30:
        viol.append('noeud SW a %.2f mm des broches 27-36 des U25' % dmin)
    # colonne centrale
    m4a, m4b = G['M4_ALIM1'][1], G['M4_ALIM2'][1]
    if emp[0] < m4a[2] - 1e-6 or emp[2] > m4b[0] + 1e-6:
        viol.append('bloc hors colonne centrale')
    soft = {}
    for n in ('U23_ALIM1', 'U23_ALIM2', 'U11_ALIM1', 'U11_ALIM2'):
        b = G[n][1]
        soft[n] = math.hypot((b[0] + b[2]) / 2 - sx, (b[1] + b[3]) / 2 - sy)
    return viol, dict(sw=(sx, sy), sw_u25=dmin, soft=soft, G=G, emp=emp, pose=pose)


# ----------------------------------------------------------------------------- dessin SVG
def draw_svg(path, plan, comps, chk, mobile_names, title):
    G, emp = chk['G'], chk['emp']
    W, H = plan['contour']['w'], plan['contour']['h']
    S = 6.0
    pad = 10
    ph = H * S
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="Arial">' % (int(W * S + 2 * pad), int(2 * ph + 4 * pad + 30))]
    out.append('<rect x="0" y="0" width="100%" height="100%" fill="white"/>')
    out.append('<text x="%d" y="16" font-size="14">%s</text>' % (pad, title))
    byname = {c['name']: c for c in comps}
    for face in (0, 1):
        oyp = 30 + pad + face * (ph + 2 * pad)
        mirror = False

        def X(x):
            return pad + x * S

        def Y(y):
            return oyp + (H - y) * S

        def rect(r, fill, stroke, op=1.0, sw=0.5):
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" stroke="%s" fill-opacity="%.2f" stroke-width="%.1f"/>' % (
                X(r[0]), Y(r[3]), (r[2] - r[0]) * S, (r[3] - r[1]) * S, fill, stroke, op, sw))
        out.append('<text x="%d" y="%d" font-size="13" font-weight="bold">%s</text>' % (pad, oyp - 3, 'FACE DESSUS (vue de dessus)' if face == 0 else 'FACE DESSOUS (vue de dessus, par transparence)'))
        rect((0, 0, W, H), '#f4f8f0', 'black', 1, 1.2)
        em = plan['marge_bord']
        rect((em, em, W - em, H - em), 'none', '#999', 1, 0.4)
        for (x, y, w, h) in plan['fentes_radiateurs']:
            rect((x, y, x + w, y + h), '#555', '#000', 1)
        for (x, y) in plan['trous_M3']:
            out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#333"/>' % (X(x), Y(y), 1.6 * S))
            rect((x - 3.6, y - 3.6, x + 3.6, y + 3.6), 'none', '#333', 1, 0.4)
        rect(emp, '#ffd0d0', '#c00', 0.5, 1.0)
        for c in comps:
            n = c['name']
            pads, box, bot = G[n]
            onface = (1 if bot else 0) == face
            fixed = n not in mobile_names
            blk = n in plan['bloc_rigide_hacheur']['dessus'] + plan['bloc_rigide_hacheur']['dessous']
            if onface:
                col = '#c00' if blk else ('#888' if fixed else ('#2a6fdb' if face == 0 else '#1a9a4a'))
                rect(box, col, col, 0.25 if not fixed else 0.15, 0.6)
                for x, y, hw, hh, th, pn, net in pads:
                    rect((x - hw, y - hh, x + hw, y + hh), '#d4a017' if not th else '#a0522d', 'none', 0.8, 0)
                big = (box[2] - box[0]) * (box[3] - box[1]) > 30 or n in ('U7',)
                if big or n.startswith('U') or n.startswith('M') or n.startswith('Q'):
                    fs = 7 if not big else 9
                    out.append('<text x="%.1f" y="%.1f" font-size="%d" text-anchor="middle" fill="black">%s</text>' % (
                        X((box[0] + box[2]) / 2), Y((box[1] + box[3]) / 2) + 3, fs, n))
            else:
                for x, y, hw, hh, th, pn, net in pads:
                    if th:
                        rect((x - hw, y - hh, x + hw, y + hh), '#a0522d', 'none', 0.5, 0)
        sx, sy = chk['sw']
        out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="#c00" stroke-dasharray="4,3"/>' % (X(sx), Y(sy), 30 * S))
    out.append('</svg>')
    open(path, 'w', encoding='utf-8').write('\n'.join(out))


# ----------------------------------------------------------------------------- principal
def main():
    nmoves = int(sys.argv[1]) if len(sys.argv) > 1 else 6000000
    nseeds = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    os.makedirs(OUT, exist_ok=True)
    logf = open(os.path.join(OUT, 'log_v3.txt'), 'w', encoding='utf-8')

    def log(s):
        print(s)
        logf.write(s + '\n')
        logf.flush()
    global CONV
    CONV = 'X'
    cx_, _, _ = load()
    errx, wherex, _ = verify_geometry(cx_)
    CONV = 'Y'
    log('Convention de la consigne (miroir X) : erreur max %.3f mm (%s) -> rejetee' % (errx, wherex))
    comps, idx, plan = load()
    err, where, mixed = verify_geometry(comps)
    where = '%s ; consigne miroir X : %.3f mm sur %s' % (where, errx, wherex)
    log('Geometrie locale : erreur max de reconstruction %.4f mm (%s) ; empreintes presentes sur les deux faces : %s' % (err, where, mixed))
    if err > 0.05:
        log('ATTENTION : erreur de geometrie > 0,05 mm')
    # -- reference : placement actuel
    Mref = Model(comps, idx, plan)
    for i in Mref.fixed_moved:   # remettre les fixes deplaces a leur place actuelle
        Mref.ox[i], Mref.oy[i] = comps[i]['ox'], comps[i]['oy']
        Mref.update_pads(i)
    Mref.net_cost_cache = [Mref.net_cost(j) for j in range(len(Mref.nets))]
    ref = Mref.metrics()
    log('Reference (placement actuel) : HPWL non pondere hors GND/+8V = %.1f mm ; HPWL pondere = %.1f ; objectif = %.1f ; reseaux bifaces = %d ; decouplages > 4 mm : %d/%d' % (
        ref['hpwl'], ref['hpwl_w'], ref['objective'], ref['bifaces'], len(ref['dec_bad']), ref['n_decaps']))
    # -- graines en parallele
    import multiprocessing as mp
    seeds = [(1000 + s, nmoves) for s in range(nseeds)]
    t = time.time()
    with mp.Pool(min(nseeds, max(1, (os.cpu_count() or 2) - 2))) as pool:
        results = pool.map(run_seed, seeds)
    for r in results:
        log('graine %d : constructif %.1f -> recuit %.1f -> final %.1f  (T0=%.2f, %.0f s) %s' % (
            r['seed'], r['c_construct'], r['c_sa'], r['c_final'], r['t0'], r['time'],
            {k: v for k, v in r['stats'].items() if k.endswith('_ok')}))
    best = min(results, key=lambda r: r['c_final'])
    log('Meilleure graine : %d (%.1f) ; duree totale %.0f s' % (best['seed'], best['c_final'], time.time() - t))
    # -- reconstruire le modele final
    M = Model(comps, idx, plan)
    for n, (x, y, r, b) in best['pose'].items():
        i = idx[n]
        M.ox[i], M.oy[i], M.rot[i], M.bot[i] = x, y, r, b
        M.update_pads(i)
    M.set_block(*best['block'])
    M.net_cost_cache = [M.net_cost(j) for j in range(len(M.nets))]
    fin = M.metrics()
    log('Final : HPWL non pondere = %.1f mm ; pondere = %.1f ; objectif = %.1f ; bifaces = %d ; decouplages > 4 mm : %d/%d' % (
        fin['hpwl'], fin['hpwl_w'], fin['objective'], fin['bifaces'], len(fin['dec_bad']), fin['n_decaps']))
    poses = {n: (M.ox[idx[n]], M.oy[idx[n]], M.rot[idx[n]], M.bot[idx[n]]) for n in best['pose']}
    viol, chk = independent_check(comps, idx, plan, poses, best['block'])
    log('Controle independant : %d violation(s)' % len(viol))
    for v in viol[:50]:
        log('  - ' + v)
    # -- sorties
    fixed_same = set(plan['fixes_inchanges'])
    place = []
    pads_out = {}
    for c in comps:
        n = c['name']
        if n in fixed_same:
            continue
        ox, oy, rot, bot = chk['pose'][n]
        e = dict(designator=n, side='bottom' if bot else 'top', x=round(ox / MIL, 2), y=round(oy / MIL, 2), rotation=int(rot))
        if bot:
            e['rotation_si_convention_miroir_x'] = int((rot + 180) % 360)
        place.append(e)
        dd = collections.defaultdict(list)
        for x, y, hw, hh, th, pn, net in chk['G'][n][0]:
            dd[pn].append([round(x / MIL, 2), round(y / MIL, 2)])
        pads_out[n] = {k: (v[0] if len(v) == 1 else v) for k, v in dd.items()}
    json.dump(place, open(os.path.join(OUT, 'placements_v3.json'), 'w', encoding='utf-8'), indent=1)
    json.dump(pads_out, open(os.path.join(OUT, 'pads_v3.json'), 'w', encoding='utf-8'), indent=1)
    bdx, bdy = best['block']
    json.dump({'dx_mil': round(bdx / MIL, 2), 'dy_mil': round(bdy / MIL, 2)}, open(os.path.join(OUT, 'bloc_delta.json'), 'w', encoding='utf-8'))
    mobile_names = set(best['pose'])
    draw_svg(os.path.join(OUT, 'placement_v3.svg'), plan, comps, chk, mobile_names,
             'Placement V3 - graine %d - HPWL %.0f mm (actuel %.0f mm)' % (best['seed'], fin['hpwl'], ref['hpwl']))
    # -- rapport
    write_report(os.path.join(OUT, 'rapport_v3.md'), M, Mref, ref, fin, viol, chk, results, best, plan, comps, idx, err, where, mixed, nmoves)
    log('Sorties ecrites dans ' + OUT)


def write_report(path, M, Mref, ref, fin, viol, chk, results, best, plan, comps, idx, gerr, gwhere, mixed, nmoves):
    L = []
    A = L.append
    A('# Placement V3 - rapport de l\'optimiseur\n')
    A('Script : `placer_v3.py` (relancable : `python -I placer_v3.py [mouvements] [graines]`), %d mouvements de recuit par graine + passe gloutonne, %d graines.\n' % (nmoves, len(results)))
    A('## Geometrie locale\n')
    A('- Erreur max de reconstruction des pastilles (geometrie de l\'instance de reference de chaque empreinte, appliquee a toutes les instances, dessus et dessous) : **%.4f mm** (%s).' % (gerr, gwhere))
    A('- Empreintes presentes a la fois dessus et dessous (valident la convention dessous) : %s\n' % (', '.join(mixed) or 'aucune'))
    A('## Couts avant / apres\n')
    A('| Mesure | Actuel | V3 |')
    A('|---|---:|---:|')
    A('| HPWL non pondere hors GND/+8V (mm) | %.1f | %.1f |' % (ref['hpwl'], fin['hpwl']))
    A('| HPWL pondere (poids 0/1/3) (mm) | %.1f | %.1f |' % (ref['hpwl_w'], fin['hpwl_w']))
    A('| Objectif complet (rails, vias, decouplage, SW) | %.1f | %.1f |' % (ref['objective'], fin['objective']))
    A('| Reseaux avec pastilles CMS sur les deux faces (hors GND/+8V) | %d | %d |' % (ref['bifaces'], fin['bifaces']))
    A('| Condensateurs de decouplage a > 4 mm de la broche de CI | %d / %d | %d / %d |' % (len(ref['dec_bad']), ref['n_decaps'], len(fin['dec_bad']), fin['n_decaps']))
    A('')
    A('Le placement actuel est mesure sur la carte actuelle (fixes deplaces a leur ancienne position) : il ne respecte pas le nouveau contour 164 x 97.\n')
    A('### Graines\n')
    A('| Graine | Constructif | Recuit | Final | Duree (s) |')
    A('|---|---:|---:|---:|---:|')
    for r in results:
        A('| %d%s | %.1f | %.1f | %.1f | %.0f |' % (r['seed'], ' (retenue)' if r is best else '', r['c_construct'], r['c_sa'], r['c_final'], r['time']))
    A('')
    A('### 20 reseaux les plus longs (HPWL, mm)\n')
    A('| Avant | mm | Apres | mm |')
    A('|---|---:|---|---:|')
    a = sorted(ref['nets'].items(), key=lambda t: -t[1])[:20]
    b = sorted(fin['nets'].items(), key=lambda t: -t[1])[:20]
    for (n1, v1), (n2, v2) in zip(a, b):
        A('| %s | %.1f | %s | %.1f |' % (n1, v1, n2, v2))
    A('')
    # occupation
    A('## Occupation\n')
    W, H = M.W, M.H
    for face, nm in ((0, 'dessus'), (1, 'dessous')):
        parts = [i for i in range(len(comps)) if (1 if M.bot[i] else 0) == face]
        mob = [i for i in parts if M.is_mobile[i]]
        area = 0.0
        for i in parts:
            bb = M.orient(i, M.rot[i], M.bot[i])[1]
            area += (bb[2] - bb[0]) * (bb[3] - bb[1])
        A('- Face %s : %d pieces (%d mobiles), somme des boites %.0f mm^2 = %.0f %% de la carte (%.0f mm^2).' % (nm, len(parts), len(mob), area, 100 * area / (W * H), W * H))
    A('')
    A('## Bloc hacheur et pieces notables\n')
    bdx, bdy = best['block']
    e = chk['emp']
    A('- Translation du bloc : dx = %.2f mm (%.1f mil), dy = %.2f mm (%.1f mil) ; emprise cuivre : x %.1f-%.1f, y %.1f-%.1f mm.' % (bdx, bdx / MIL, bdy, bdy / MIL, e[0], e[2], e[1], e[3]))
    A('- Noeud SW (centre de M1) : (%.1f ; %.1f) mm ; distance mini aux broches 27-36 des U25 : %.1f mm (>= 30).' % (chk['sw'][0], chk['sw'][1], chk['sw_u25']))
    for n, d in chk['soft'].items():
        A('- %s a %.1f mm du noeud SW%s.' % (n, d, '' if d >= 30 else ' (**< 30 mm, penalite douce**)'))
    p1 = idx['P1']
    for n in ('U7', 'C32_ALIM1', 'C32_ALIM2', 'U1'):
        if n in chk['pose']:
            ox, oy, rot, bot = chk['pose'][n]
            A('- %s : origine (%.2f ; %.2f) mm, rot %d, %s.' % (n, ox, oy, rot, 'dessous' if bot else 'dessus'))
    A('')
    A('## Controle independant des contraintes dures\n')
    A('Recalcul a partir des poses de sortie et de la geometrie locale, comparaisons deux a deux sans grille (boites + 0,25 mm, pastilles TH + 0,6 mm, fentes + 1 mm, trous M3 +-3,6 mm, marge de bord 1,5 mm, zones SMA +-6 mm dessus, corps des fixes dessus, emprise du bloc deux faces, regles de face et de rotation, noeud SW, colonne centrale).\n')
    if viol:
        A('**%d violation(s)** :\n' % len(viol))
        for v in viol:
            A('- ' + v)
    else:
        A('**0 violation.**')
    A('')
    if fin['dec_bad']:
        A('### Condensateurs de decouplage encore a plus de 4 mm\n')
        for n, net, d in fin['dec_bad']:
            A('- %s (%s) : %.2f mm' % (n, net, d))
        A('')
    A('## Regles de face appliquees\n')
    cnt = collections.Counter(M.rules[i] for i in M.mobile)
    for k, v in cnt.items():
        A('- %s : %d pieces' % (k, v))
    A('')
    A(HYP)
    open(path, 'w', encoding='utf-8').write('\n'.join(L))


HYP = """## Hypotheses

- Unites internes mm ; origines arrondies a une grille de 5 mil.
- Fentes : [x, y, largeur, hauteur] depuis le coin bas-gauche (coherent avec la boite du radiateur M4 deplace, dont le haut tombe sur le haut des fentes).
- Boite d'une piece = `bbox_no_name_comment` ramenee dans le repere local ; marge 0,25 mm de chaque cote. Les pastilles traversantes (bornes avec masque) bloquent la face opposee avec 0,6 mm de marge ; ce sont aussi les seuls obstacles dessous des pieces fixes traversantes (afficheurs, PDIP40, radiateurs, P1, SMA).
- Les pieces fixes CMS (LED, boutons) ne bloquent que le dessus.
- Bloc hacheur : emprise cuivre (deux faces, sans marge supplementaire) + boites gonflees de ses 11 pieces sur leur face (L1 deborde de l'emprise de ~3,7 mm a droite). Translation seule, emprise entre les boites des radiateurs.
- Objectif : HPWL pondere (GND/+8V poids 0, liste de puissance poids 3), +2 mm par reseau ayant des pastilles CMS sur les deux faces (reseaux de poids > 0), rails de plus de 8 pastilles (+5V, +10V, +2.5Vref, -5V, +12V, +8V) : somme des distances Manhattan de chaque pastille passive (R, C, L, D) a la pastille active (U, M, Q, P, connecteurs) la plus proche + HPWL des seules pastilles actives (ce qui garde le lien P1 -> bloc pour +12V).
- Decouplage : piece a 2 pastilles C, une a GND, l'autre sur un rail ; distance euclidienne de la pastille rail a la broche de CI (U*) la plus proche du meme reseau ; cout 1 x d + 25 x (d - 3,8) au-dela de 3,8 mm.
- Les condensateurs du bloc hacheur (C7, C15, C17, C20) sont exclus du terme de decouplage : ils sont rigides et decouplent M1/M2, pas un CI.
- Penalite douce : 5 par mm en dessous de 30 mm entre le centre de U23_ALIMx / U11_ALIMx et le noeud SW.
- Pastilles de nom vide / reseau vide : ignorees pour le cout, gardees pour la geometrie.

## Limites connues

- HPWL et distance a la broche ne voient ni le routage reel ni la densite de vias ; aucune regle d'orientation fine (sens d'entree/sortie des CI) n'est modelisee.
- La convention de rotation dessous (monde = O + R(rot)(-lx, ly)) est appliquee telle quelle ; a verifier dans Altium apres application.
- Les boites englobantes incluent la serigraphie / le corps 3D : le placement est un peu plus lache que le strict cuivre.
- Hauteur des pieces sous la carte non verifiee vis-a-vis du boitier.
"""

if __name__ == '__main__':
    main()
