"""Géométrie commune des importeurs de carte (lot B) : discrétisation, boucles, polygones, distances.

Fonctions pures, sans lecture de fichier ni variable d'environnement. Unités : mm. Un polygone est une liste de
points [x, y], non fermée (le dernier point n'est pas répété).

- Discrétisation des arcs, cercles et courbes de Bézier cubiques avec une flèche ≤ ``FLECHE`` (0,005 mm, §2.2).
  Avec ``exces=True``, les sommets sont posés sur le cercle de rayon r / cos(δ/2) : chaque corde est tangente au
  cercle vrai et le polygone contient la forme (approximation « par excès » des pastilles, §2.2).
- ``assembler_boucles`` : assemblage de chaînes ouvertes en boucles, extrémités confondues à ``TOL_BOUCLE``
  (0,001 mm, annexe D).
- Orientation, aire, point dans un polygone, distances, enveloppe convexe, intersection de deux polygones.
- ``aire_balayage`` : aire de (∩ inclus) − (∪ exclus) par balayage horizontal (règle pair-impair), qui sert à la
  couverture des remplissages.
- ``contour_pastille`` : contour du cuivre d'une pastille de ``entree_carte/2`` dans le repère local de la pièce.
- ``intersection_carte(sujet, trous, carte)`` : (sujet − ∪ trous) ∩ carte, exacte pour des polygones simples
  quelconques ; ``retrait_interieur(poly, d)`` : polygone rentré de d (plans négatifs, zones de plan). Déplacées
  de ``construire_donnees.py`` (B-ET5), qui les appelle ici.

``ErreurEntree`` est l'erreur commune des importeurs : entrée invalide (code 2, §1.4). Son message est en français
et ne contient jamais de chemin.
"""
from __future__ import annotations

import bisect
import collections
import math

FLECHE = 0.005
TOL_BOUCLE = 0.001
_EPS = 1e-9


class ErreurEntree(Exception):
    """Entrée invalide (code 2) ; le message ne cite que des noms de fichiers, jamais de chemin."""


# --------------------------------------------------------------------------------------------------------------
# Discrétisation
# --------------------------------------------------------------------------------------------------------------
def pas_angulaire(rayon, fleche=FLECHE):
    """Plus grand pas angulaire (radians) dont la flèche reste ≤ ``fleche`` sur un cercle de ce rayon."""
    if rayon <= fleche:
        return math.pi / 2
    return 2.0 * math.acos(1.0 - fleche / rayon)


def discretiser_arc(centre, rayon, a0, balayage, fleche=FLECHE, exces=False):
    """Points d'un arc de centre ``centre``, de l'angle ``a0`` (radians) sur ``balayage`` radians (signé, sens
    trigonométrique si positif), extrémités comprises."""
    cx, cy = centre
    if rayon <= 0.0:
        return [[cx, cy]]
    pas = pas_angulaire(rayon, fleche)
    n = max(1, int(math.ceil(abs(balayage) / pas - 1e-12)))
    d = balayage / n
    r = rayon / math.cos(abs(d) / 2.0) if exces else rayon
    return [[cx + r * math.cos(a0 + k * d), cy + r * math.sin(a0 + k * d)] for k in range(n + 1)]


def discretiser_cercle(centre, rayon, fleche=FLECHE, exces=False):
    """Polygone (non fermé, sens trigonométrique) d'un cercle ; au moins 8 sommets."""
    pas = min(pas_angulaire(rayon, fleche), math.pi / 4)
    n = max(8, int(math.ceil(2.0 * math.pi / pas - 1e-12)))
    d = 2.0 * math.pi / n
    r = rayon / math.cos(d / 2.0) if exces else rayon
    return [[centre[0] + r * math.cos(k * d), centre[1] + r * math.sin(k * d)] for k in range(n)]


def cercle_par_trois_points(a, m, b):
    """(centre, rayon) du cercle passant par a, m, b ; None si les points sont alignés."""
    ax, ay = a
    bx, by = m
    cx, cy = b
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def arc_trois_points(a, m, b):
    """(centre, rayon, a0, balayage) de l'arc qui va de a à b en passant par m ; None si alignés."""
    c = cercle_par_trois_points(a, m, b)
    if c is None:
        return None
    (ux, uy), r = c
    t0 = math.atan2(a[1] - uy, a[0] - ux)
    tm = math.atan2(m[1] - uy, m[0] - ux)
    t1 = math.atan2(b[1] - uy, b[0] - ux)
    dm = (tm - t0) % (2.0 * math.pi)
    d1 = (t1 - t0) % (2.0 * math.pi)
    balayage = d1 if dm <= d1 else d1 - 2.0 * math.pi
    return (ux, uy), r, t0, balayage


def discretiser_arc_trois_points(a, m, b, fleche=FLECHE, exces=False):
    """Points de l'arc a → m → b ; un segment si les trois points sont alignés. Extrémités exactes."""
    arc = arc_trois_points(a, m, b)
    if arc is None:
        return [list(a), list(b)]
    c, r, t0, bal = arc
    pts = discretiser_arc(c, r, t0, bal, fleche, exces)
    if not exces:
        pts[0], pts[-1] = list(a), list(b)
    return pts


def discretiser_bezier(p0, p1, p2, p3, fleche=FLECHE):
    """Points d'une courbe de Bézier cubique, extrémités comprises, par subdivision : l'écart à la corde d'un
    morceau est borné par 3/4 de la plus grande distance de ses points de contrôle à la corde."""
    sortie = [list(p0)]

    def plat(a, b, c, d):
        return max(distance_point_droite(b, a, d), distance_point_droite(c, a, d)) * 0.75 <= fleche

    pile = [(p0, p1, p2, p3, 0)]
    while pile:
        a, b, c, d, prof = pile.pop()
        if prof >= 24 or plat(a, b, c, d):
            sortie.append(list(d))
            continue
        ab = _milieu(a, b)
        bc = _milieu(b, c)
        cd = _milieu(c, d)
        abc = _milieu(ab, bc)
        bcd = _milieu(bc, cd)
        m = _milieu(abc, bcd)
        pile.append((m, bcd, cd, d, prof + 1))
        pile.append((a, ab, abc, m, prof + 1))
    return sortie


def _milieu(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


# --------------------------------------------------------------------------------------------------------------
# Formes épaisses
# --------------------------------------------------------------------------------------------------------------
def capsule(p, q, largeur, fleche=FLECHE, exces=True):
    """Polygone (sens trigonométrique) d'un segment épais à bouts ronds."""
    r = largeur / 2.0
    if r <= 0.0:
        return [list(p), list(q)]
    a = math.atan2(q[1] - p[1], q[0] - p[0]) if (p[0], p[1]) != (q[0], q[1]) else 0.0
    if math.hypot(q[0] - p[0], q[1] - p[1]) < 1e-12:
        return discretiser_cercle(p, r, fleche, exces)
    fin = discretiser_arc(q, r, a - math.pi / 2, math.pi, fleche, exces)
    debut = discretiser_arc(p, r, a + math.pi / 2, math.pi, fleche, exces)
    return nettoyer(fin + debut)


def arc_epais(centre, rayon, a0, balayage, largeur, fleche=FLECHE, exces=True):
    """Polygone d'un arc épais à bouts ronds (piste en arc, trait d'empreinte)."""
    w = largeur / 2.0
    axe = discretiser_arc(centre, rayon, a0, balayage, fleche, False)
    if w <= 0.0:
        return axe
    s = 1.0 if balayage >= 0 else -1.0
    ext, inte = [], []
    for x, y in axe:
        nx, ny = x - centre[0], y - centre[1]
        n = math.hypot(nx, ny) or 1.0
        nx, ny = nx / n, ny / n
        k = (1.0 / math.cos(pas_angulaire(rayon + w, fleche) / 2.0)) if exces else 1.0
        ext.append([x + nx * w * k, y + ny * w * k])
        inte.append([x - nx * w, y - ny * w])
    t1 = a0 + balayage
    cap_fin = discretiser_arc(axe[-1], w, t1, s * math.pi, fleche, exces)
    cap_debut = discretiser_arc(axe[0], w, a0 + math.pi, s * math.pi, fleche, exces)
    return orienter(nettoyer(ext + cap_fin[1:-1] + inte[::-1] + cap_debut[1:-1]))


def polyligne_epaisse(points, largeur, fleche=FLECHE):
    """Liste de capsules d'une polyligne épaisse (une par segment)."""
    return [capsule(points[i], points[i + 1], largeur, fleche) for i in range(len(points) - 1)]


def decaler_polygone(poly, d, fleche=FLECHE):
    """Polygone décalé vers l'extérieur de ``d`` ≥ 0 (contour d'un trait de largeur 2·d) : raccord arrondi (par
    excès) aux sommets convexes, intersection exacte des côtés décalés aux sommets rentrants."""
    if d <= 0.0 or len(poly) < 3:
        return [list(p) for p in poly]
    p = orienter(poly)
    n = len(p)
    sortie = []
    for i in range(n):
        a, b, c = p[i - 1], p[i], p[(i + 1) % n]
        n1 = _normale_ext(a, b)
        n2 = _normale_ext(b, c)
        croix = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if croix > 1e-15:
            a1 = math.atan2(n1[1], n1[0])
            bal = (math.atan2(n2[1], n2[0]) - a1) % (2.0 * math.pi)
            sortie.extend(discretiser_arc(b, d, a1, bal, fleche, exces=True))
            continue
        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        lb = math.hypot(bx, by)
        if lb < 1e-12:
            sortie.append([b[0] + n1[0] * d, b[1] + n1[1] * d])
            continue
        bx, by = bx / lb, by / lb
        k = d / max(bx * n1[0] + by * n1[1], 0.25)
        sortie.append([b[0] + bx * k, b[1] + by * k])
    return nettoyer(sortie)


def _normale_ext(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return (dy / n, -dx / n)


# --------------------------------------------------------------------------------------------------------------
# Boucles
# --------------------------------------------------------------------------------------------------------------
def assembler_boucles(chaines, tol=TOL_BOUCLE):
    """Assemble des chaînes en boucles fermées.

    ``chaines`` : liste de (points, etiquette, fermee). Une chaîne fermée est une boucle à elle seule. Les chaînes
    ouvertes sont mises bout à bout (sens inversé au besoin) quand leurs extrémités sont à ``tol`` près.
    Rend une liste de (points, {etiquettes}). Lève ``ErreurEntree`` si une chaîne ne se referme pas.
    """
    boucles = []
    ouvertes = []
    for pts, etiq, fermee in chaines:
        pts = [list(p) for p in pts]
        if len(pts) < 2:
            continue
        if fermee or (len(pts) > 2 and _proche(pts[0], pts[-1], tol)):
            if _proche(pts[0], pts[-1], tol):
                pts = pts[:-1]
            if len(pts) >= 3:
                boucles.append((pts, {etiq}))
            continue
        ouvertes.append((pts, etiq))
    cellule = max(tol * 10.0, 1e-6)
    grille = {}

    def cle(p):
        return (int(math.floor(p[0] / cellule)), int(math.floor(p[1] / cellule)))

    for i, (pts, _e) in enumerate(ouvertes):
        for bout in (0, 1):
            grille.setdefault(cle(pts[0] if bout == 0 else pts[-1]), []).append((i, bout))
    utilisee = [False] * len(ouvertes)

    def chercher(p):
        cx, cy = cle(p)
        meilleur = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i, bout in grille.get((cx + dx, cy + dy), ()):
                    if utilisee[i]:
                        continue
                    q = ouvertes[i][0][0] if bout == 0 else ouvertes[i][0][-1]
                    d = math.hypot(q[0] - p[0], q[1] - p[1])
                    if d <= tol and (meilleur is None or d < meilleur[0]):
                        meilleur = (d, i, bout)
        return meilleur

    for i0 in range(len(ouvertes)):
        if utilisee[i0]:
            continue
        utilisee[i0] = True
        pts, etiq = ouvertes[i0]
        courant = list(pts)
        etiquettes = {etiq}
        while True:
            if len(courant) > 2 and _proche(courant[0], courant[-1], tol):
                break
            t = chercher(courant[-1])
            if t is None:
                x, y = courant[-1]
                raise ErreurEntree(f"contour non fermé : extrémité libre près de ({x:.3f} ; {y:.3f}) mm")
            _d, i, bout = t
            utilisee[i] = True
            suite = ouvertes[i][0] if bout == 0 else ouvertes[i][0][::-1]
            courant.extend(suite[1:])
            etiquettes.add(ouvertes[i][1])
        courant = courant[:-1]
        if len(courant) >= 3:
            boucles.append((courant, etiquettes))
    return boucles


def _proche(a, b, tol):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def nettoyer(poly, eps=1e-9):
    """Retire les sommets consécutifs confondus (et le dernier s'il répète le premier)."""
    sortie = []
    for p in poly:
        if not sortie or abs(p[0] - sortie[-1][0]) > eps or abs(p[1] - sortie[-1][1]) > eps:
            sortie.append([p[0], p[1]])
    while len(sortie) > 1 and abs(sortie[0][0] - sortie[-1][0]) <= eps and abs(sortie[0][1] - sortie[-1][1]) <= eps:
        sortie.pop()
    return sortie


# --------------------------------------------------------------------------------------------------------------
# Polygones
# --------------------------------------------------------------------------------------------------------------
def aire_signee(poly):
    """Aire signée (positive en sens trigonométrique)."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def aire(poly):
    return abs(aire_signee(poly))


def orienter(poly, trigo=True):
    """Copie du polygone dans le sens demandé (trigonométrique par défaut)."""
    p = [list(q) for q in poly]
    if (aire_signee(p) > 0) != trigo:
        p.reverse()
    return p


def boite(points):
    """[x0, y0, x1, y1] des points ; None si vide."""
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def point_dans_polygone(p, poly):
    """Règle pair-impair ; un point sur le bord peut tomber d'un côté ou de l'autre."""
    x, y = p
    dedans = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xc = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < xc:
                dedans = not dedans
        j = i
    return dedans


def polygone_dans_polygone(a, b):
    """Vrai si la majorité des sommets de ``a`` est dans ``b`` (robuste aux sommets posés sur le bord)."""
    if not a:
        return False
    n = sum(1 for p in a if point_dans_polygone(p, b))
    return n * 2 > len(a)


def distance_point_segment(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 <= 0.0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def distance_point_droite(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n <= 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / n


def distance_point_polygone(p, poly):
    """Distance au bord ; 0 si le point est à l'intérieur."""
    if point_dans_polygone(p, poly):
        return 0.0
    n = len(poly)
    return min(distance_point_segment(p, poly[i], poly[(i + 1) % n]) for i in range(n))


def _segments_se_coupent(a, b, c, d):
    def o(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-15 else (1 if v > 0 else -1)

    o1, o2, o3, o4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and _sur(a, c, b)) or (o2 == 0 and _sur(a, d, b)) or (o3 == 0 and _sur(c, a, d)) \
        or (o4 == 0 and _sur(c, b, d))


def _sur(p, q, r):
    return min(p[0], r[0]) - 1e-12 <= q[0] <= max(p[0], r[0]) + 1e-12 and \
        min(p[1], r[1]) - 1e-12 <= q[1] <= max(p[1], r[1]) + 1e-12


def polygones_se_touchent(a, b):
    """Vrai si les deux polygones (pleins) ont un point commun, bords compris."""
    ba, bb = boite(a), boite(b)
    if ba is None or bb is None or ba[2] < bb[0] or bb[2] < ba[0] or ba[3] < bb[1] or bb[3] < ba[1]:
        return False
    if point_dans_polygone(a[0], b) or point_dans_polygone(b[0], a):
        return True
    na, nb = len(a), len(b)
    for i in range(na):
        p, q = a[i], a[(i + 1) % na]
        for j in range(nb):
            if _segments_se_coupent(p, q, b[j], b[(j + 1) % nb]):
                return True
    return False


def enveloppe_convexe(points):
    """Enveloppe convexe (chaîne monotone d'Andrew), sens trigonométrique."""
    pts = sorted({(p[0], p[1]) for p in points})
    if len(pts) <= 2:
        return [list(p) for p in pts]

    def croix(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    bas, haut = [], []
    for p in pts:
        while len(bas) >= 2 and croix(bas[-2], bas[-1], p) <= 0:
            bas.pop()
        bas.append(p)
    for p in reversed(pts):
        while len(haut) >= 2 and croix(haut[-2], haut[-1], p) <= 0:
            haut.pop()
        haut.append(p)
    return [list(p) for p in bas[:-1] + haut[:-1]]


def _intervalles(poly, y):
    xs = []
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    return [(xs[k], xs[k + 1]) for k in range(0, len(xs) - 1, 2)]


def _intersection_iv(a, b):
    sortie = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if lo < hi:
            sortie.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return sortie


def _union_iv(listes):
    tous = sorted(iv for lst in listes for iv in lst)
    sortie = []
    for lo, hi in tous:
        if sortie and lo <= sortie[-1][1]:
            sortie[-1] = (sortie[-1][0], max(sortie[-1][1], hi))
        else:
            sortie.append((lo, hi))
    return sortie


def _longueur_moins(a, u):
    total = 0.0
    for lo, hi in a:
        total += hi - lo
        for ulo, uhi in u:
            c = min(hi, uhi) - max(lo, ulo)
            if c > 0:
                total -= c
    return total


def aire_balayage(inclus, exclus=(), lignes=400):
    """Aire de (∩ des polygones ``inclus``) − (∪ des polygones ``exclus``), par ``lignes`` lignes de balayage
    horizontales (règle du point milieu ; exacte en x). Précision relative de l'ordre de 1/lignes sur les bords
    obliques."""
    if not inclus:
        return 0.0
    b = boite([p for poly in inclus for p in poly])
    for poly in inclus[1:]:
        bb = boite(poly)
        b = [max(b[0], bb[0]), max(b[1], bb[1]), min(b[2], bb[2]), min(b[3], bb[3])]
    if b[2] <= b[0] or b[3] <= b[1]:
        return 0.0
    h = (b[3] - b[1]) / lignes
    total = 0.0
    for k in range(lignes):
        y = b[1] + (k + 0.5) * h
        iv = _intervalles(inclus[0], y)
        for poly in inclus[1:]:
            iv = _intersection_iv(iv, _intervalles(poly, y))
            if not iv:
                break
        if not iv:
            continue
        u = _union_iv([_intervalles(poly, y) for poly in exclus]) if exclus else []
        total += _longueur_moins(iv, u) * h
    return total


# --------------------------------------------------------------------------------------------------------------
# Repères et pastilles
# --------------------------------------------------------------------------------------------------------------
def tourner(p, angle_deg):
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return [p[0] * c - p[1] * s, p[0] * s + p[1] * c]


def local_vers_monde(p, composant):
    """monde = origine + R(rotation) · M(face) · local, M(dessous) = diag(1, −1) (§0.4)."""
    x, y = p
    if composant["face"] == "dessous":
        y = -y
    q = tourner([x, y], composant["rotation"])
    return [composant["x"] + q[0], composant["y"] + q[1]]


def monde_vers_local(p, composant):
    """local = M(face) · R(−rotation) · (monde − origine) (§0.4)."""
    q = tourner([p[0] - composant["x"], p[1] - composant["y"]], -composant["rotation"])
    if composant["face"] == "dessous":
        q[1] = -q[1]
    return q


def rectangle_arrondi(largeur, hauteur, rayon, fleche=FLECHE, exces=True):
    """Polygone centré d'un rectangle à coins arrondis (repère de la pastille)."""
    hw, hh = largeur / 2.0, hauteur / 2.0
    r = max(0.0, min(rayon, hw, hh))
    if r <= 0.0:
        return [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]]
    pts = []
    for cx, cy, a in ((hw - r, -hh + r, -math.pi / 2), (hw - r, hh - r, 0.0), (-hw + r, hh - r, math.pi / 2),
                      (-hw + r, -hh + r, math.pi)):
        pts.extend(discretiser_arc((cx, cy), r, a, math.pi / 2, fleche, exces))
    return nettoyer(pts)


def forme_pastille(forme, largeur, hauteur, rayon_coin=0.0, fleche=FLECHE):
    """Polygone centré (repère propre de la pastille, avant rotation) d'une forme simple, par excès."""
    if forme == "rond":
        return discretiser_cercle((0.0, 0.0), largeur / 2.0, fleche, exces=True)
    if forme == "oblong":
        if largeur >= hauteur:
            d = (largeur - hauteur) / 2.0
            return capsule((-d, 0.0), (d, 0.0), hauteur, fleche)
        d = (hauteur - largeur) / 2.0
        return capsule((0.0, -d), (0.0, d), largeur, fleche)
    if forme == "rect_arrondi":
        return rectangle_arrondi(largeur, hauteur, rayon_coin, fleche)
    hw, hh = largeur / 2.0, hauteur / 2.0
    return [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]]


def contour_pastille(pastille, fleche=FLECHE):
    """Contour du cuivre d'une pastille de ``entree_carte/2`` dans le repère local de la pièce (sens
    trigonométrique) : ``points`` pour un polygone, sinon la forme tournée de ``rotation`` et posée en (x, y)."""
    if pastille["forme"] == "polygone" and pastille.get("points"):
        return orienter(pastille["points"])
    base = forme_pastille(pastille["forme"], pastille["largeur"], pastille["hauteur"],
                          pastille.get("rayon_coin", 0.0), fleche)
    pts = []
    for p in base:
        q = tourner(p, pastille["rotation"])
        pts.append([pastille["x"] + q[0], pastille["y"] + q[1]])
    return orienter(pts)


# --------------------------------------------------------------------------------------------------------------
# Intersection avec la carte et retrait intérieur (exacts, polygones simples quelconques)
# --------------------------------------------------------------------------------------------------------------
def _r9(x):
    return round(float(x), 9)


def _abscisses_communes(a, b, c, d):
    """Abscisse du point commun des segments [a, b] et [c, d] non parallèles (croisement ou contact), sinon []. Les
    extrémités d'un recouvrement de segments parallèles sont des sommets : elles sont déjà retenues."""
    rx, ry, sx, sy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return []
    qx, qy = c[0] - a[0], c[1] - a[1]
    t, u = (qx * sy - qy * sx) / den, (qx * ry - qy * rx) / den
    return [a[0] + t * rx] if -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12 else []


def _aligne(a, b, c):
    """``b`` sur la droite (a, c), à 1e-9 près en sinus de l'angle."""
    ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1]
    return abs(ux * vy - uy * vx) <= 1e-9 * max(1e-12, math.hypot(ux, uy) * math.hypot(vx, vy))


def _moins_intervalles(a, b):
    """Intervalles de ``a`` hors de ``b`` (listes triées d'intervalles disjoints)."""
    sortie = []
    for lo, hi in a:
        cur = lo
        for blo, bhi in b:
            if bhi <= cur or blo >= hi:
                continue
            if blo > cur:
                sortie.append((cur, blo))
            cur = max(cur, bhi)
        if cur < hi:
            sortie.append((cur, hi))
    return sortie


def intersection_carte(sujet, trous, carte):
    """(``sujet`` − ∪ ``trous``) ∩ ``carte`` pour des polygones simples quelconques (convexes ou non, côtés confondus
    permis) : [(contour, [trous])], contours en sens trigonométrique, trous en sens horaire.

    Méthode exacte par bandes verticales : les abscisses des sommets et des points communs des côtés découpent le plan
    en bandes où aucun côté n'en croise un autre ; dans chaque bande, les côtés sont ordonnés en y et l'appartenance
    (pair-impair pour chaque polygone) est connue cellule par cellule. Les côtés où elle change, et les morceaux
    verticaux où elle diffère de part et d'autre d'une abscisse, forment le bord, orienté la région à gauche ; il est
    chaîné en boucles (au sommet commun à deux boucles, le virage le plus à gauche), puis les sommets alignés sont
    retirés."""
    polys = [nettoyer(sujet), nettoyer(carte)] + [nettoyer(t) for t in trous]
    if len(polys[0]) < 3 or len(polys[1]) < 3:
        return []
    polys = [p for k, p in enumerate(polys) if k < 2 or len(p) >= 3]
    segs, xs = [], set()
    for k, p in enumerate(polys):
        n = len(p)
        for i in range(n):
            a, b = p[i], p[(i + 1) % n]
            xs.add(a[0])
            if abs(a[0] - b[0]) > 1e-12:
                segs.append((a, b, k) if a[0] < b[0] else (b, a, k))
    segs.sort(key=lambda s: (s[0][0], s[1][0]))          # balayage : côtés triés par abscisse de gauche
    for i, (a, b, k) in enumerate(segs):
        ya0, ya1 = min(a[1], b[1]), max(a[1], b[1])
        for j in range(i + 1, len(segs)):
            c, d, l = segs[j]
            if c[0] > b[0]:
                break
            if l != k and min(c[1], d[1]) <= ya1 and ya0 <= max(c[1], d[1]):
                xs.update(_abscisses_communes(a, b, c, d))
    xs_tries = []
    for x in sorted(xs):
        if not xs_tries or x - xs_tries[-1] > 1e-9:
            xs_tries.append(x)
    xs = xs_tries
    nb = len(polys)

    def dedans(par):
        return par[0] and par[1] and not any(par[2:])

    bandes = []                            # par bande : [(yg, yd, dedans_dessous, dedans_dessus)], côtés groupés
    ys = [[] for _ in xs]                  # ordonnées connues à chaque abscisse
    actifs, suivant = [], 0
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        while suivant < len(segs) and segs[suivant][0][0] <= x0 + 1e-9:
            actifs.append(segs[suivant])
            suivant += 1
        actifs = [s for s in actifs if s[1][0] >= x1 - 1e-9]
        lignes = []
        for a, b, k in actifs:
            pente = (b[1] - a[1]) / (b[0] - a[0])
            yg, yd = a[1] + (x0 - a[0]) * pente, a[1] + (x1 - a[0]) * pente
            lignes.append(((yg + yd) / 2, yg, yd, k))
        lignes.sort()
        par, bande, j = [False] * nb, [], 0
        while j < len(lignes):
            ym, yg, yd, _ = lignes[j]
            avant = dedans(par)
            while j < len(lignes) and lignes[j][0] - ym <= 1e-9:      # côtés confondus : un seul bord
                par[lignes[j][3]] = not par[lignes[j][3]]
                j += 1
            apres = dedans(par)
            if avant != apres:
                bande.append((yg, yd, avant, apres))
                ys[i].append(yg)
                ys[i + 1].append(yd)
        bandes.append(bande)
    canon = []
    for liste in ys:
        grappe = []
        for y in sorted(liste):
            if not grappe or y - grappe[-1] > 1e-7:
                grappe.append(y)
        canon.append(grappe)

    def cy(i, y):
        liste = canon[i]
        j = bisect.bisect_left(liste, y)
        return min(liste[max(0, j - 1):j + 1], key=lambda v: abs(v - y))

    aretes = []
    gauche = [[] for _ in xs]              # intervalles intérieurs vus de la bande de gauche, à chaque abscisse
    droite = [[] for _ in xs]
    for i, bande in enumerate(bandes):
        x0, x1 = xs[i], xs[i + 1]
        ouvert_g = ouvert_d = None
        for yg, yd, avant, apres in bande:
            g, d = cy(i, yg), cy(i + 1, yd)
            aretes.append(((x0, g), (x1, d)) if apres else ((x1, d), (x0, g)))
            if apres:
                ouvert_g, ouvert_d = g, d
            else:
                droite[i].append((ouvert_g, g))
                gauche[i + 1].append((ouvert_d, d))
    for i, x in enumerate(xs):
        L = [iv for iv in gauche[i] if iv[1] > iv[0]]
        R = [iv for iv in droite[i] if iv[1] > iv[0]]
        for lo, hi, monte in ([(lo, hi, True) for lo, hi in _moins_intervalles(L, R)]
                              + [(lo, hi, False) for lo, hi in _moins_intervalles(R, L)]):
            liste = canon[i]
            arrets = [lo] + liste[bisect.bisect_right(liste, lo):bisect.bisect_left(liste, hi)] + [hi]
            morceaux = list(zip(arrets, arrets[1:]))
            for y0, y1 in morceaux if monte else reversed(morceaux):
                aretes.append(((x, y0), (x, y1)) if monte else ((x, y1), (x, y0)))
    sortants = collections.defaultdict(list)
    for k, (p, q) in enumerate(aretes):
        sortants[p].append(k)
    libre = [True] * len(aretes)
    boucles = []
    for k0 in range(len(aretes)):
        if not libre[k0]:
            continue
        boucle, k = [], k0
        while True:
            libre[k] = False
            p, q = aretes[k]
            boucle.append(p)
            cand = [j for j in sortants[q] if libre[j] or j == k0]
            if not cand:
                break
            dx, dy = q[0] - p[0], q[1] - p[1]

            def virage(j):
                r = aretes[j][1]
                ex, ey = r[0] - q[0], r[1] - q[1]
                return math.atan2(dx * ey - dy * ex, dx * ex + dy * ey)

            k = max(cand, key=virage)
            if k == k0:
                break
        net = []
        for p in boucle:                   # sommets alignés retirés
            net.append(p)
            while len(net) >= 3 and _aligne(net[-3], net[-2], net[-1]):
                del net[-2]
        while len(net) >= 3:
            if _aligne(net[-2], net[-1], net[0]):
                net.pop()
            elif _aligne(net[-1], net[0], net[1]):
                net.pop(0)
            else:
                break
        if len(net) >= 3 and abs(aire_signee(net)) > 1e-12:
            boucles.append([[_r9(x), _r9(y)] for x, y in net])
    exterieurs = [b for b in boucles if aire_signee(b) > 0]
    resultat = [(b, []) for b in sorted(exterieurs, key=lambda b: (min(b), -aire(b)))]
    for t in boucles:
        if aire_signee(t) > 0:
            continue
        a, b = t[0], t[1]
        lg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        m = [(a[0] + b[0]) / 2 - (b[1] - a[1]) / lg * 1e-6, (a[1] + b[1]) / 2 + (b[0] - a[0]) / lg * 1e-6]
        hotes = [r for r in resultat if point_dans_polygone(m, r[0])]
        if hotes:
            min(hotes, key=lambda r: aire(r[0]))[1].append(t)
    return resultat


def retrait_interieur(poly, d):
    """Polygone rentré de ``d`` (côtés décalés vers l'intérieur, sommets à l'intersection des côtés décalés)."""
    p = orienter(poly)
    n = len(p)
    if d <= 0 or n < 3:
        return [list(q) for q in p]
    lignes = []
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        lg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        nx, ny = -(b[1] - a[1]) / lg, (b[0] - a[0]) / lg            # normale intérieure (sens trigonométrique)
        lignes.append(((a[0] + nx * d, a[1] + ny * d), (b[0] - a[0], b[1] - a[1])))
    sortie = []
    for i in range(n):
        (p1, d1), (p2, d2) = lignes[i - 1], lignes[i]
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < _EPS:
            sortie.append([p2[0], p2[1]])
            continue
        t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
        sortie.append([_r9(p1[0] + t * d1[0]), _r9(p1[1] + t * d1[1])])
    return sortie
