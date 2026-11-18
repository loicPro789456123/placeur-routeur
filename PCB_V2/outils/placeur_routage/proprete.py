# -*- coding: utf-8 -*-
"""Propreté d'un routage (lot C, §2.8, §4.7) -> ``proprete/1``. Ce n'est pas un contrôle de validité
(voir ``drc_maison.py``).

    python proprete.py --donnees D --placement P --routage R [--sortie S]

Chiffres rendus (aucun nom propre à une carte : rôles, groupes et paires des données) :

- ``segments``, ``segments_courts`` : pistes du routage, et celles de moins de 0,5 mm ;
- ``coudes_par_connexion`` : coudes de §4.7 (``geom_commune.compter_coudes``) / connexions ; ``connexions`` = somme,
  sur les réseaux d'au moins deux pastilles hors plans, de (pastilles − 1) ; plans = réseaux de rôle ``plan`` et
  réseaux qui ont une zone de plan ;
- ``vias_par_connexion`` : vias du routage hors plans / connexions ;
- ``detour`` : par réseau routé hors plans, longueur des pistes / arbre couvrant minimal octile des centres de ses
  pastilles (arbre > 0,5 mm) : médiane et 90e centile (0 si aucun réseau) ;
- ``vias_puissance`` : vias des réseaux de rôle ``puissance`` ou ``commutation`` ;
- ``entrees_excentrees`` : extrémités de piste dans une pastille de leur réseau et de leur couche, à plus de
  25 % de sa demi-petite-dimension + 0,05 mm de son centre ;
- ``hors_direction`` : par nom de couche, part de la longueur ni horizontale ni verticale dominante ;
- ``ecart_jumeaux`` : réseaux appariés par ``groupes[].correspondance_reseaux`` (couples de réseaux différents, tous
  deux routés) : écart relatif moyen de longueur (|la − lb| / max) et de vias (|va − vb| / max(1, va, vb)) ;
- ``paires`` : par paire de ``donnees.paires``, |Δ longueur| et |Δ vias| entre ``positif`` et ``negatif``.

Comportement de 9b5b5a3 (``proprete.py``) repris pour les définitions ; les expressions sur les noms de réseaux
et les classes en dur sont remplacées par les groupes et les rôles.
"""
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import geom_commune as gc  # noqa: E402
import formats  # noqa: E402

FORMAT = "proprete/1"
SEUIL_COURT_MM = 0.5
ROLES_PUISSANCE = ("puissance", "commutation")
IDEAL_MIN_MM = 0.5
EPS_DIRECTION = 1e-6


def _octile(a, b):
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return max(dx, dy) + (math.sqrt(2.0) - 1.0) * min(dx, dy)


def arbre_octile(pts):
    """Longueur de l'arbre couvrant minimal (distance octile) de points ``(x, y)``."""
    n = len(pts)
    if n < 2:
        return 0.0
    dans = [False] * n
    d = [math.inf] * n
    d[0] = 0.0
    tot = 0.0
    for _ in range(n):
        i = min((k for k in range(n) if not dans[k]), key=lambda k: d[k])
        dans[i] = True
        tot += d[i]
        for k in range(n):
            if not dans[k]:
                v = _octile(pts[i], pts[k])
                if v < d[k]:
                    d[k] = v
    return tot


def _arrondi(v, n=3):
    return round(v, n) if v is not None else None


def reseaux_plans(sc):
    plans = {n for n, r in sc.reseaux.items() if r.get("role") == "plan"}
    plans.update(o.reseau for o in sc.objets if o.genre == gc.ZONE_PLAN and o.reseau)
    return plans


def mesurer(donnees, placements, routage):
    """``proprete/1`` d'un couple placement + routage."""
    sc = gc.poser(donnees, placements, routage)
    objs = sc.objets
    plans = reseaux_plans(sc)
    pistes = [objs[k] for k in sc.pistes]
    vias = [objs[k] for k in sc.vias]
    pads_net = {}
    for o in objs:
        if o.genre == gc.PASTILLE and o.reseau:
            pads_net.setdefault(o.reseau, []).append(o)
    pistes_net = {}
    for p in pistes:
        pistes_net.setdefault(p.reseau, []).append(p)
    vias_net = {}
    for v in vias:
        vias_net[v.reseau] = vias_net.get(v.reseau, 0) + 1
    courts = sum(1 for p in pistes if p.info["longueur"] < SEUIL_COURT_MM)
    coudes = gc.compter_coudes((p.reseau, p.info["couche"], p.info["a"][0], p.info["a"][1], p.info["b"][0],
                                p.info["b"][1]) for p in pistes)[0]
    nconn = sum(len(ps) - 1 for n, ps in pads_net.items() if n not in plans and len(ps) >= 2)
    vias_hors_plans = sum(c for n, c in vias_net.items() if n not in plans)
    detours = []
    for n, ps in sorted(pads_net.items()):
        if n in plans or len(ps) < 2 or not pistes_net.get(n):
            continue
        ideal = arbre_octile([o.info["centre"] for o in ps])
        if ideal > IDEAL_MIN_MM:
            detours.append(math.fsum(p.info["longueur"] for p in pistes_net[n]) / ideal)
    detours.sort()
    vias_p = sum(c for n, c in vias_net.items() if (sc.reseaux.get(n) or {}).get("role") in ROLES_PUISSANCE)
    excentrees = 0
    for p in pistes:
        r = sc.rang.get(p.info["couche"])
        if r is None:
            continue
        for pt in (p.info["a"], p.info["b"]):
            for q in pads_net.get(p.reseau, ()):
                if not (q.masque >> r) & 1:
                    continue
                f = q.forme_sur(r)
                if gc.distance_signee(f, pt[0], pt[1]) <= 1e-6:
                    src = q.info.get("source") or {}
                    demi = min(float(src.get("largeur", 0.0)), float(src.get("hauteur", 0.0))) / 2.0
                    c = q.info["centre"]
                    if math.hypot(pt[0] - c[0], pt[1] - c[1]) > 0.25 * demi + 0.05:
                        excentrees += 1
                    break
    hors = {}
    for couche in sc.couches:
        tot = h = v = 0.0
        for p in pistes:
            if p.info["couche"] != couche:
                continue
            lg = p.info["longueur"]
            tot += lg
            (ax, ay), (bx, by) = p.info["a"], p.info["b"]
            if abs(by - ay) < EPS_DIRECTION:
                h += lg
            if abs(bx - ax) < EPS_DIRECTION:
                v += lg
        if tot > 0.0:
            hors[couche] = round(1.0 - max(h, v) / tot, 3)
    longueur = {n: math.fsum(p.info["longueur"] for p in ps) for n, ps in pistes_net.items()}
    vus = set()
    el, ev = [], []
    for g in donnees.get("groupes") or []:
        for couple in g.get("correspondance_reseaux") or []:
            if not isinstance(couple, (list, tuple)) or len(couple) != 2:
                continue
            a, b = couple
            cle = tuple(sorted((a, b)))
            if a == b or cle in vus or a not in pistes_net or b not in pistes_net:
                continue
            vus.add(cle)
            la, lb = longueur[a], longueur[b]
            if la + lb > 0.0:
                el.append(abs(la - lb) / max(la, lb))
            va, vb = vias_net.get(a, 0), vias_net.get(b, 0)
            ev.append(abs(va - vb) / max(1, va, vb))
    paires = {}
    for pr in donnees.get("paires") or []:
        a, b = pr.get("positif"), pr.get("negatif")
        paires[pr.get("nom") or "%s/%s" % (a, b)] = {
            "delta_longueur_mm": round(abs(longueur.get(a, 0.0) - longueur.get(b, 0.0)), 3),
            "delta_vias": abs(vias_net.get(a, 0) - vias_net.get(b, 0))}
    return {"format": FORMAT, "segments": len(pistes), "segments_courts": courts,
            "coudes_par_connexion": round(coudes / max(1, nconn), 3),
            "vias_par_connexion": round(vias_hors_plans / max(1, nconn), 3),
            "detour": {"mediane": _arrondi(detours[len(detours) // 2]) if detours else 0.0,
                       "p90": _arrondi(detours[int(len(detours) * 0.9)]) if detours else 0.0},
            "vias_puissance": vias_p, "entrees_excentrees": excentrees, "hors_direction": hors,
            "ecart_jumeaux": {"longueur": round(math.fsum(el) / max(1, len(el)), 3),
                              "vias": round(math.fsum(ev) / max(1, len(ev)), 3)},
            "paires": paires, "coudes": coudes, "connexions": nconn, "paires_jumelles": len(el)}


OPTIONS = (
    gc.Option("--donnees", "données du moteur (donnees_placeur/2)", obligatoire=True, meta="D"),
    gc.Option("--placement", "placement (placements/2 ou liste v1)", obligatoire=True, meta="P"),
    gc.Option("--routage", "routage (routage/2 ou v1)", obligatoire=True, meta="R"),
    gc.Option("--sortie", "fichier proprete/1 écrit", meta="S"),
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
    gc.afficher("propreté : %d segments (%d courts), %s coude et %s via par connexion, %d vias de puissance"
                % (res["segments"], res["segments_courts"], gc.nombre_fr(res["coudes_par_connexion"], 3),
                   gc.nombre_fr(res["vias_par_connexion"], 3), res["vias_puissance"]))
    return gc.CODE_SUCCES


def main(argv=None):
    """Ligne de commande ; rend le code de sortie (§1.4) sans quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, "proprete.py", "Propreté d'un routage (proprete/1).")


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
