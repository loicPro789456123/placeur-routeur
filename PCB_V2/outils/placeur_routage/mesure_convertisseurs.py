# -*- coding: utf-8 -*-
"""Mesures des convertisseurs à découpage (lot C, §2.8, avenant 2.2 A.6) -> ``mesure_convertisseurs/1``.

    python mesure_convertisseurs.py --donnees D --placement P --routage R [--sortie S]

Un élément par convertisseur de ``donnees.convertisseurs`` (liste vide sans convertisseur) :

- ``boucle_chaude_mm2``, ``boucle_chaude_perimetre_mm`` : polygone condensateur (rail) -> haut (rail) -> haut (nœud)
  -> bas (nœud) -> bas (masse) -> condensateur (masse), sommets aux centres des pastilles de chaque pièce sur chaque
  réseau (``boucle_chaude.reseau_rail``, ``reseau_commutation``, ``boucle_chaude.reseau_masse``), minimum (surface +
  périmètre) sur les ``boucle_chaude.condensateurs`` (définition de ``mesure_hacheur.py``, 9b5b5a3) ;
  ``boucle_chaude_evaluee`` faux (surface et périmètre 0) si un sommet manque pour tous les condensateurs ;
- ``noeud_longueur_mm``, ``noeud_surface_mm2`` : pistes du ``reseau_commutation`` (routage et cuivre fixe) :
  somme des longueurs ; surface = Σ longueur × largeur + surfaces du routage + régions du cuivre fixe (pastilles non
  comptées, comme 9b5b5a3) ;
- ``noeud_vias`` (A.6) : vias du routage sur ``reseau_commutation`` (couture exclue : elle n'est pas lue) ;
- ``sensibles_distance_min_mm`` (A.6) : plus petite distance, en projection sur le plan de la carte, entre le cuivre
  posé (pistes, vias, pastilles ; routage, cuivre fixe et empreintes) des ``reseaux_sensibles`` et celui du
  ``reseau_commutation`` ; null si l'un des deux n'a pas de cuivre ; recouvrement en projection = 0 ;
- ``sensibles_distance_min_hors_pilote_mm`` (informatif) : la même sans les pastilles du ``pilote`` (ses broches
  sensibles sont par construction voisines de sa broche de commutation) ;
- ``chutes_mv`` : pour chaque charge de ``donnees.charges`` dont les pastilles sont sur ``reseau_sortie`` : chute de
  tension (mV, moyenne sur ses pastilles) entre la source et la charge, toutes les charges du réseau tirant leur
  courant en même temps (analyse nodale, Python pur, gradient conjugué). Source : pastilles sur ``reseau_sortie`` de
  ``haut`` pour un élévateur, de l'``inductance`` sinon (à défaut, des ``cond_sortie``). Modèle : pistes =
  résistances ρ L / (l × e) (ρ = 1,72·10⁻⁵ Ω·mm, e = ``couches[].epaisseur_cuivre_mm``, 0,035 à défaut) ;
  pastilles, vias, surfaces et régions = nœuds de résistance nulle ; contacts : extrémité de piste dans un tel objet
  ou sur l'axe d'une autre piste (à sa demi-largeur près, la piste est alors coupée en ce point). Une charge non
  reliée à la source n'est pas écrite (décision §8.0-2 c).
"""
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import geom_commune as gc  # noqa: E402
import formats  # noqa: E402

FORMAT = "mesure_convertisseurs/1"
RHO_CUIVRE_OHM_MM = 1.72e-5
EPAISSEUR_DEFAUT_MM = 0.035
EPS_CONTACT = 1e-6
GENRES_NOEUDS = (gc.PASTILLE, gc.VIA, gc.VIA_EMPREINTE, gc.SURFACE, gc.REGION)


def _aire_polygone(f):
    def shoelace(a):
        return abs(math.fsum(a[i][0] * a[(i + 1) % len(a)][1] - a[(i + 1) % len(a)][0] * a[i][1]
                             for i in range(len(a)))) / 2.0
    return max(0.0, shoelace(f.anneaux[0]) - math.fsum(shoelace(t) for t in f.anneaux[1:]))


def _est_piste(o):
    return o.genre == gc.PISTE or (o.genre == gc.CUIVRE_FIXE and o.info.get("type") == "piste")


def _est_cuivre_pose(o):
    """Pistes, vias, pastilles (A.6)."""
    if o.genre in (gc.PISTE, gc.VIA, gc.VIA_EMPREINTE, gc.PASTILLE):
        return True
    return o.genre == gc.CUIVRE_FIXE and o.info.get("type") in ("piste", "via")


def _formes(o):
    fs = [o.forme]
    if o.formes:
        fs.extend(o.formes.values())
    return fs


def _distance_projection(a, b):
    m = None
    for f in _formes(a):
        for g in _formes(b):
            e = gc.ecart_formes(f, g)
            if m is None or e < m:
                m = e
    return max(0.0, m)


def distance_min(objs_a, objs_b):
    """Plus petite distance en projection entre deux listes d'objets (None si l'une est vide)."""
    if not objs_a or not objs_b:
        return None
    index = gc.IndexCases(1.0, 0.0)
    for k, o in enumerate(objs_b):
        index.ajouter(k, o.boite)
    best = None
    for o in objs_a:
        if best is not None:
            b = o.boite
            requete = (b[0] - best, b[1] - best, b[2] + best, b[3] + best)
            candidats = set()
            for lst in index.listes(requete):
                candidats.update(lst)
            if not candidats:
                continue
        else:
            candidats = range(len(objs_b))
        for j in candidats:
            q = objs_b[j]
            if best is not None and gc.distance_boites(o.boite, q.boite) >= best:
                continue
            e = _distance_projection(o, q)
            if best is None or e < best:
                best = e
    return best


def _centre_pieces(sc, piece, reseau):
    pts = [o.info["centre"] for o in sc.objets if o.genre == gc.PASTILLE and o.piece == piece and o.reseau == reseau]
    if not pts:
        return None
    return (math.fsum(p[0] for p in pts) / len(pts), math.fsum(p[1] for p in pts) / len(pts))


def boucle_chaude(sc, cv):
    bc = cv.get("boucle_chaude") or {}
    rail, masse, sw = bc.get("reseau_rail"), bc.get("reseau_masse"), cv.get("reseau_commutation")
    h, b = cv.get("haut"), cv.get("bas")
    a1, a2 = _centre_pieces(sc, h, rail), _centre_pieces(sc, h, sw)
    b1, b2 = _centre_pieces(sc, b, sw), _centre_pieces(sc, b, masse)
    best = None
    for cap in bc.get("condensateurs") or []:
        p, g = _centre_pieces(sc, cap, rail), _centre_pieces(sc, cap, masse)
        poly = [p, a1, a2, b1, b2, g]
        if any(x is None for x in poly):
            continue
        n = len(poly)
        aire = abs(math.fsum(poly[k][0] * poly[(k + 1) % n][1] - poly[(k + 1) % n][0] * poly[k][1]
                             for k in range(n))) / 2.0
        per = math.fsum(math.hypot(poly[(k + 1) % n][0] - poly[k][0], poly[(k + 1) % n][1] - poly[k][1])
                        for k in range(n))
        if best is None or aire + per < best[0] + best[1]:
            best = (aire, per)
    return best


# ---------------------------------------------------------------------------------------------------------------------
# Analyse nodale
# ---------------------------------------------------------------------------------------------------------------------
class _Union:
    def __init__(self):
        self.p = {}

    def trouver(self, a):
        p = self.p
        p.setdefault(a, a)
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def unir(self, a, b):
        ra, rb = self.trouver(a), self.trouver(b)
        if ra != rb:
            self.p[ra] = rb


def _gradient_conjugue(adj, b, iterations=20000, tol=1e-14):
    """Résout L x = b (L : laplacien réduit défini positif, ``adj[i]`` = {j: conductance}, ``diag`` inclus sous la
    clé ``i``)."""
    n = len(b)
    x = [0.0] * n
    r = list(b)
    p = list(r)
    rr = math.fsum(v * v for v in r)
    if rr == 0.0:
        return x
    seuil = tol * max(1.0, rr)
    for _ in range(iterations):
        ap = [math.fsum(g * p[j] for j, g in adj[i].items()) for i in range(n)]
        pap = math.fsum(p[i] * ap[i] for i in range(n))
        if pap <= 0.0:
            break
        alpha = rr / pap
        for i in range(n):
            x[i] += alpha * p[i]
            r[i] -= alpha * ap[i]
        rr2 = math.fsum(v * v for v in r)
        if rr2 < seuil:
            break
        beta = rr2 / rr
        rr = rr2
        p = [r[i] + beta * p[i] for i in range(n)]
    return x


def chutes_reseau(sc, reseau, sources, charges):
    """Chutes (V) aux charges ``[(nom, [indices de pastilles], courant_a)]`` d'un réseau, la source (indices de
    pastilles) à 0 V. Rend ``{nom: chute_v}`` (charges reliées seulement)."""
    objs = sc.objets
    epaisseur = [float(c.get("epaisseur_cuivre_mm") or EPAISSEUR_DEFAUT_MM) for c in sc.donnees_couches]
    pistes = [k for k, o in enumerate(objs) if o.reseau == reseau and _est_piste(o) and o.masque]
    blocs = [k for k, o in enumerate(objs) if o.reseau == reseau and o.masque and (
        o.genre in GENRES_NOEUDS or (o.genre == gc.CUIVRE_FIXE and o.info.get("type") in ("via", "region")))]
    uf = _Union()
    for k in blocs:
        uf.trouver(("b", k))
    for i, ka in enumerate(blocs):
        a = objs[ka]
        for kb in blocs[i + 1:]:
            b = objs[kb]
            if a.masque & b.masque and gc.distance_boites(a.boite, b.boite) <= gc.EPS_CONTACT:
                e = gc.ecart_objets(a, b)
                if e is not None and e <= gc.EPS_CONTACT:
                    uf.unir(("b", ka), ("b", kb))
    coupes = {k: {0.0: ("p", k, 0.0), 1.0: ("p", k, 1.0)} for k in pistes}
    for k in pistes:
        o = objs[k]
        f = o.forme
        for t, (x, y) in ((0.0, (f.x1, f.y1)), (1.0, (f.x2, f.y2))):
            nd = ("p", k, t)
            uf.trouver(nd)
            for kb in blocs:
                b = objs[kb]
                if (o.masque & b.masque) and any(gc.distance_signee(b.forme_sur(r), x, y) <= EPS_CONTACT
                                                 for r in gc.rangs(o.masque & b.masque)):
                    uf.unir(nd, ("b", kb))
            for j in pistes:
                if j == k or not (o.masque & objs[j].masque):
                    continue
                g = objs[j].forme
                dx, dy = g.x2 - g.x1, g.y2 - g.y1
                l2 = dx * dx + dy * dy
                u = 0.0 if l2 == 0.0 else max(0.0, min(1.0, ((x - g.x1) * dx + (y - g.y1) * dy) / l2))
                if math.hypot(x - g.x1 - u * dx, y - g.y1 - u * dy) <= g.rayon + EPS_CONTACT:
                    u = round(u, 9)
                    cible = coupes[j].setdefault(u, ("p", j, u))
                    uf.unir(nd, cible)
    aretes = []
    for k in pistes:
        o = objs[k]
        f = o.forme
        lg = math.hypot(f.x2 - f.x1, f.y2 - f.y1)
        w = 2.0 * f.rayon
        e = max(epaisseur[r] for r in gc.rangs(o.masque))
        ts = sorted(coupes[k])
        for t0, t1 in zip(ts, ts[1:]):
            a, b = uf.trouver(coupes[k][t0]), uf.trouver(coupes[k][t1])
            if a == b or w <= 0.0:
                continue
            r = RHO_CUIVRE_OHM_MM * (t1 - t0) * lg / (w * e)
            aretes.append((a, b, 1.0 / r if r > 0.0 else 1e12))
    src = {uf.trouver(("b", k)) for k in sources}
    if not src:
        return {}
    # composante de la source
    voisins = {}
    for a, b, g in aretes:
        voisins.setdefault(a, []).append(b)
        voisins.setdefault(b, []).append(a)
    vus = set(src)
    pile = list(src)
    while pile:
        n = pile.pop()
        for m in voisins.get(n, ()):
            if m not in vus:
                vus.add(m)
                pile.append(m)
    libres = sorted((n for n in vus if n not in src), key=repr)
    rang = {n: i for i, n in enumerate(libres)}
    adj = [dict() for _ in libres]
    for a, b, g in aretes:
        ia, ib = rang.get(a), rang.get(b)
        if ia is not None:
            adj[ia][ia] = adj[ia].get(ia, 0.0) + g
        if ib is not None:
            adj[ib][ib] = adj[ib].get(ib, 0.0) + g
        if ia is not None and ib is not None:
            adj[ia][ib] = adj[ia].get(ib, 0.0) - g
            adj[ib][ia] = adj[ib].get(ia, 0.0) - g
    b = [0.0] * len(libres)
    for _nom, pads, courant in charges:
        if not pads:
            continue
        part = float(courant) / len(pads)
        for k in pads:
            i = rang.get(uf.trouver(("b", k)))
            if i is not None:
                b[i] += part
    x = _gradient_conjugue(adj, b) if libres else []
    res = {}
    for nom, pads, _courant in charges:
        vals = []
        for k in pads:
            n = uf.trouver(("b", k))
            if n in src:
                vals.append(0.0)
            elif n in rang:
                vals.append(x[rang[n]])
        if pads and len(vals) == len(pads):
            res[nom] = math.fsum(vals) / len(vals)
    return res


# ---------------------------------------------------------------------------------------------------------------------
def _mesurer_un(sc, donnees, cv):
    objs = sc.objets
    sw = cv.get("reseau_commutation") or ""
    best = boucle_chaude(sc, cv)
    lg = surf = 0.0
    for o in objs:
        if o.reseau != sw or not sw:
            continue
        if _est_piste(o):
            f = o.forme
            l = math.hypot(f.x2 - f.x1, f.y2 - f.y1)
            lg += l
            surf += l * 2.0 * f.rayon
        elif o.genre == gc.SURFACE or (o.genre == gc.CUIVRE_FIXE and o.info.get("type") == "region"):
            surf += _aire_polygone(o.forme)
    sens = set(cv.get("reseaux_sensibles") or [])
    pilote = cv.get("pilote") or None
    cote_sw = [o for o in objs if sw and o.reseau == sw and _est_cuivre_pose(o)]
    cote_sens = [o for o in objs if o.reseau in sens and _est_cuivre_pose(o)]
    d_min = distance_min(cote_sens, cote_sw)
    hors = distance_min([o for o in cote_sens if not (o.genre == gc.PASTILLE and pilote and o.piece == pilote)],
                        [o for o in cote_sw if not (o.genre == gc.PASTILLE and pilote and o.piece == pilote)])
    chutes = {}
    sortie = cv.get("reseau_sortie")
    charges = []
    par_id = {o.id: k for k, o in enumerate(objs) if o.genre == gc.PASTILLE}
    for ch in donnees.get("charges") or []:
        pads = [par_id[i] for i in ch.get("pastilles") or [] if i in par_id]
        if pads and all(objs[k].reseau == sortie for k in pads):
            charges.append((ch.get("broche") or objs[pads[0]].id, pads, float(ch.get("courant_a") or 0.0)))
    if charges and sortie:
        ordre = [cv.get("haut")] if cv.get("topologie") == "elevateur" else [cv.get("inductance")]
        ordre.append(list(cv.get("cond_sortie") or []))
        sources = []
        for choix in ordre:
            noms = choix if isinstance(choix, list) else [choix]
            sources = [k for k, o in enumerate(objs)
                       if o.genre == gc.PASTILLE and o.piece in noms and o.reseau == sortie]
            if sources:
                break
        for nom, v in chutes_reseau(sc, sortie, sources, charges).items():
            chutes[nom] = round(v * 1000.0, 3)
    return {"nom": cv.get("nom", ""), "boucle_chaude_evaluee": best is not None,
            "boucle_chaude_mm2": round(best[0], 3) if best else 0.0,
            "boucle_chaude_perimetre_mm": round(best[1], 3) if best else 0.0,
            "noeud_longueur_mm": round(lg, 3), "noeud_surface_mm2": round(surf, 3), "chutes_mv": chutes,
            "noeud_vias": sum(1 for k in sc.vias if sw and objs[k].reseau == sw),
            "sensibles_distance_min_mm": None if d_min is None else round(d_min, 4),
            "sensibles_distance_min_hors_pilote_mm": None if hors is None else round(hors, 4)}


def mesurer(donnees, placements, routage):
    """``mesure_convertisseurs/1`` d'un couple placement + routage (``convertisseurs: []`` sans convertisseur)."""
    sc = gc.poser(donnees, placements, routage)
    return {"format": FORMAT,
            "convertisseurs": [_mesurer_un(sc, donnees, cv) for cv in donnees.get("convertisseurs") or []]}


OPTIONS = (
    gc.Option("--donnees", "données du moteur (donnees_placeur/2)", obligatoire=True, meta="D"),
    gc.Option("--placement", "placement (placements/2 ou liste v1)", obligatoire=True, meta="P"),
    gc.Option("--routage", "routage (routage/2 ou v1)", obligatoire=True, meta="R"),
    gc.Option("--sortie", "fichier mesure_convertisseurs/1 écrit", meta="S"),
)


def _charger(chemin, fmt):
    try:
        return formats.charger(chemin, fmt)
    except formats.ErreurFormat as e:
        raise gc.ErreurEntree("; ".join(e.erreurs))


def _principal(args):
    res = mesurer(_charger(args["donnees"], "donnees_placeur"), _charger(args["placement"], "placements"),
                  _charger(args["routage"], "routage"))
    if args.get("sortie"):
        gc.ecrire_json(args["sortie"], res)
    if not res["convertisseurs"]:
        gc.afficher("aucun convertisseur")
    for c in res["convertisseurs"]:
        gc.afficher("%s : boucle chaude %s mm², nœud %s mm / %s mm², %d via(s), distance sensible %s mm"
                    % (c["nom"], gc.nombre_fr(c["boucle_chaude_mm2"], 2), gc.nombre_fr(c["noeud_longueur_mm"], 2),
                       gc.nombre_fr(c["noeud_surface_mm2"], 2), c["noeud_vias"],
                       "—" if c["sensibles_distance_min_mm"] is None
                       else gc.nombre_fr(c["sensibles_distance_min_mm"], 3)))
    return gc.CODE_SUCCES


def main(argv=None):
    """Ligne de commande ; rend le code de sortie (§1.4) sans quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, "mesure_convertisseurs.py",
                       "Mesures des convertisseurs à découpage (mesure_convertisseurs/1).")


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
