# -*- coding: utf-8 -*-
"""Geometrie du controleur Python (independante du moteur Rust). Unites : mils.

Forme = noyau convexe (polygone ou segment) gonfle d'un rayon :
  ('poly', [(x, y), ...], r)   polygone convexe (sens quelconque) gonfle de r
  ('seg', (x1, y1), (x2, y2), r)  capsule (piste, pastille oblongue / ronde)
dist(A, B) = max(0, distance des noyaux - rA - rB).
"""
import math

MIL = 0.0254


def d_pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _cr(o, p, q):
    return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])


def segs_coupent(a, b, c, d):
    d1, d2, d3, d4 = _cr(c, d, a), _cr(c, d, b), _cr(a, b, c), _cr(a, b, d)
    return ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0))


def d_seg_seg(a, b, c, d):
    if segs_coupent(a, b, c, d):
        return 0.0
    return min(d_pt_seg(a, c, d), d_pt_seg(b, c, d), d_pt_seg(c, a, b), d_pt_seg(d, a, b))


def dans_poly(p, pts):
    """point dans polygone convexe (bord inclus)."""
    sg = 0
    n = len(pts)
    for i in range(n):
        c = _cr(pts[i], pts[(i + 1) % n], p)
        if c > 1e-12:
            s = 1
        elif c < -1e-12:
            s = -1
        else:
            continue
        if sg == 0:
            sg = s
        elif s != sg:
            return False
    return True


def aretes(noyau):
    if noyau[0] == 'seg':
        return [(noyau[1], noyau[2])]
    pts = noyau[1]
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def sommets(noyau):
    return [noyau[1], noyau[2]] if noyau[0] == 'seg' else noyau[1]


def d_noyaux(A, B):
    if A[0] == 'poly' and any(dans_poly(p, A[1]) for p in sommets(B)):
        return 0.0
    if B[0] == 'poly' and any(dans_poly(p, B[1]) for p in sommets(A)):
        return 0.0
    return min(d_seg_seg(a, b, c, d) for a, b in aretes(A) for c, d in aretes(B))


def rayon(F):
    return F[-1]


def dist(A, B):
    return max(0.0, d_noyaux(A, B) - rayon(A) - rayon(B))


def boite(F):
    pts = sommets(F)
    r = rayon(F)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)


def rot(x, y, a_deg):
    a = math.radians(a_deg)
    c, s = math.cos(a), math.sin(a)
    if abs(a_deg % 90) < 1e-9:
        k = int(round(a_deg / 90)) % 4
        c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][k]
    return x * c - y * s, x * s + y * c


def rect_tourne(cx, cy, sx, sy, a_deg, r=0.0):
    pts = [rot(u, v, a_deg) for u, v in ((-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2))]
    return ('poly', [(cx + u, cy + v) for u, v in pts], r)


def forme_pastille(p, expansion=True):
    """ouverture de masque d'une pastille Altium (dict de chaines). TopShape 1 = rond/oblong, 2 = rectangle."""
    x, y = float(p['X']), float(p['Y'])
    sx, sy = float(p['TopXSize']), float(p['TopYSize'])
    e = float(p['SolderMaskExpansion']) if expansion else 0.0
    a = float(p['Rotation'])
    if p['TopShape'] == '2':
        return rect_tourne(x, y, sx + 2 * e, sy + 2 * e, a)
    if p['TopShape'] == '1':
        r = min(sx, sy) / 2 + e
        L = abs(sx - sy) / 2
        ux, uy = rot(L, 0, a) if sx >= sy else rot(0, L, a)
        return ('seg', (x - ux, y - uy), (x + ux, y + uy), r)
    raise ValueError('TopShape inconnu %r' % p['TopShape'])


def arc_en_segments(xc, yc, R, a0, a1, sagitta=0.01):
    """arc Altium (sens trigo de a0 a a1, degres) -> liste de points. La corde est a l'interieur : on decale les
    points vers l'exterieur de la sagitta pour que la polyligne englobe l'arc (conservateur)."""
    span = (a1 - a0) % 360.0
    if span < 1e-9:
        span = 360.0
    n = max(2, int(math.ceil(span / math.degrees(2 * math.acos(max(-1, 1 - sagitta / max(R, 1e-9)))))) if R > sagitta else 2)
    Rr = R / math.cos(math.radians(span / n / 2)) if n > 1 else R
    pts = []
    for i in range(n + 1):
        t = math.radians(a0 + span * i / n)
        rr = R if i in (0, n) else Rr
        pts.append((xc + rr * math.cos(t), yc + rr * math.sin(t)))
    return pts


# --- police trait d'Altium (calibree sur les Bounds exportes, unites de h/6) ---
AVANCE = {'1': 5, '.': 5, 'I': 5}
DROITE_DERNIER = {'1': 2, 'k': 3}


def largeur_encre(texte, h):
    """largeur de l'encre (axes des traits) d'un texte en police trait, de l'axe du premier trait a celui du dernier."""
    if not texte:
        return 0.0
    u = sum(AVANCE.get(c, 6) for c in texte[:-1]) + DROITE_DERNIER.get(texte[-1], 4)
    return u * h / 6.0


def bas_encre(texte, h):
    return -h / 6.0 if any(c in '_gjpqy,' for c in texte) else 0.0


def forme_texte_trait(texte, x, y, h, w, rot_deg, miroir, gonfle=None):
    """rectangle englobant l'encre d'un texte trait : repere texte (u le long, v vers le haut), ancre (x, y) = debut
    de la ligne de base ; monde = (x, y) + R(rot) (m u, v), m = -1 si miroir. Gonfle de w/2 (bord des traits)."""
    L = largeur_encre(texte, h)
    b = bas_encre(texte, h)
    m = -1.0 if miroir else 1.0
    pts = [rot(m * u, v, rot_deg) for u, v in ((0, b), (L, b), (L, h), (0, h))]
    return ('poly', [(x + u, y + v) for u, v in pts], w / 2 if gonfle is None else gonfle)


def dans_polygone(p, pts):
    """point dans un polygone quelconque (non convexe), règle pair-impair."""
    x, y = p
    dedans = False
    j = len(pts) - 1
    for i in range(len(pts)):
        (xi, yi), (xj, yj) = pts[i], pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dedans = not dedans
        j = i
    return dedans
