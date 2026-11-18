"""Fichiers d'essai de l'annexe E du contrat (séance dans Altium faite plus tard par une personne) et verdicts.

Fabrication :
    python -B fabriquer_essais.py --base CARTE --sortie DOSSIER
Verdicts sur les valeurs relevées dans Altium :
    python -B fabriquer_essais.py --verifier RESULTATS --attendus ATTENDUS [--json FICHIER]

Fabrication : depuis la carte de base (lue, jamais modifiée), un fichier .PcbDoc par expérience (E1 à E9, T1),
chacun portant UNE modification. Les objets modifiés sont choisis par leurs propriétés (face, forme, perçage,
nombre de pastilles, couche, place libre...), jamais par leur nom : l'outil marche sur toute carte. Une expérience
dont la base n'a pas l'objet nécessaire est sautée avec un message (code 0). Écrit aussi `attendus.json`
(objet modifié, valeurs attendues calculées par format_altium, tolérances) et `resultats_a_remplir.json` (valeurs à
relever dans Altium, toutes à null). Chaque fichier écrit est relu par olefile (tous les flux) et par la
bibliothèque (mêmes octets, modification présente et exacte) ; tout flux hors des stockages prévus doit rester
identique à la base. Même base : mêmes fichiers, octet pour octet. Protocole : EXPERIENCES_A_FAIRE.md.

Verdict par convention : « confirmée » (toutes les valeurs dans la tolérance), « contredite » (avenant au contrat
nécessaire), « non faite » (expérience sautée, non faite ou valeur manquante).

Codes : 0 succès (fichiers conformes ; aucune convention contredite) ; 1 verdict négatif (fichier non conforme ;
au moins une convention contredite) ; 2 entrée invalide ; 4 erreur interne. Messages sans chemin. Aucun fichier
n'est lu ni écrit par défaut.
"""
import copy
import hashlib
import json
import math
import os
import re
import sys
import unicodedata

import olefile

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

from altium_fichier import PcbDoc, pose  # noqa: E402
from altium_fichier import commandes as K  # noqa: E402
from altium_fichier import pcbdoc as P  # noqa: E402
from altium_fichier.flux import SousBloc, SousRegion  # noqa: E402
from altium_fichier.proprietes import formater_mil, mm_vers_interne, valeur_mil  # noqa: E402

FORMAT_ATTENDUS = "attendus_annexe_e/1"
FORMAT_RESULTATS = "resultats_annexe_e/1"
FORMAT_VERDICTS = "verdicts_annexe_e/1"
AUCUN = P.AUCUN
PRECISION = 1e-3  # mil : écart admis entre une valeur écrite et sa relecture
VIA_SANS_MODELE = (24.0, 12.0)  # diamètre et trou (mil) d'une via d'essai quand la base n'en a aucune
REGLE = ("confirmée si chaque valeur relevée est dans la tolérance de la valeur attendue ; contredite (avenant au "
         "contrat nécessaire) si une valeur en sort ; non faite si l'expérience est sautée ou une valeur manque")


class Saut(Exception):
    """Expérience impossible sur cette base (objet nécessaire absent) : sautée avec ce motif."""


# ---------------------------------------------------------------------------
# outils communs
# ---------------------------------------------------------------------------
def mm(v_mil):
    return round(v_mil * 0.0254, 4)


def affiche(doc, x, y):
    """Coordonnées telles qu'Altium les affiche : relatives à l'origine de la carte (mm et mil)."""
    ox, oy = doc.origine()
    return {"x_mm": mm(x - ox), "y_mm": mm(y - oy), "x_mil": round(x - ox, 4), "y_mil": round(y - oy, 4)}


def mesure(cle, description, type_, attendu, tolerance=None, acceptes=None):
    m = {"cle": cle, "description": description, "type": type_, "attendu": attendu}
    if tolerance is not None:
        m["tolerance"] = tolerance
    if acceptes is not None:
        m["acceptes"] = acceptes
    return m


def proche(a, b, tol=PRECISION):
    return a is not None and b is not None and abs(a - b) <= tol


def dedans(pt, poly):
    """Point dans un polygone (règle pair-impair)."""
    x, y = pt
    c = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def aire(poly):
    return abs(sum(poly[i - 1][0] * p[1] - p[0] * poly[i - 1][1] for i, p in enumerate(poly))) / 2


def polygone_carte(doc):
    """Contour de la carte (mil) : région de carte (arcs déjà en segments), sinon sommets de Board6."""
    rc = doc.regions_carte()
    if len(rc) == 1 and len(rc[0]["contour"]) >= 3:
        return rc[0]["contour"]
    pts = [(s["x"], s["y"]) for s in doc.contour_carte()]
    if len(pts) >= 3:
        return pts
    raise Saut("la carte n'a pas de contour")


def obstacles(doc):
    """Boîtes (x1, y1, x2, y2) en mil de tout le cuivre et des keep-out (sauf coulées de polygone)."""
    out = []
    for p in doc.pastilles():
        h = max(max(p["taille_haut"]), max(p["taille_milieu"]), max(p["taille_bas"]), p["trou"]) / 2
        out.append((p["x"] - h, p["y"] - h, p["x"] + h, p["y"] + h))
    for t in doc.pistes():
        if t["polygone"] == AUCUN:
            w = t["largeur"] / 2
            out.append((min(t["x1"], t["x2"]) - w, min(t["y1"], t["y2"]) - w,
                        max(t["x1"], t["x2"]) + w, max(t["y1"], t["y2"]) + w))
    for a in doc.arcs():
        if a["polygone"] == AUCUN:
            r = a["rayon"] + a["largeur"] / 2
            out.append((a["cx"] - r, a["cy"] - r, a["cx"] + r, a["cy"] + r))
    for v in doc.vias():
        r = v["diametre"] / 2
        out.append((v["x"] - r, v["y"] - r, v["x"] + r, v["y"] + r))
    for f in doc.remplissages():
        if f["polygone"] == AUCUN:
            out.append((min(f["x1"], f["x2"]), min(f["y1"], f["y2"]), max(f["x1"], f["x2"]), max(f["y1"], f["y2"])))
    for r in doc.regions():
        if r["polygone"] == AUCUN and r["contour"]:
            xs, ys = [p[0] for p in r["contour"]], [p[1] for p in r["contour"]]
            out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def zone_libre(doc, largeur, hauteur, marge=25.0):
    """Coin bas-gauche (mil, entiers) du premier rectangle `largeur` x `hauteur` (balayage de bas en haut, puis de
    gauche à droite) entièrement dans la carte, à `marge` du contour et de tout obstacle ; None s'il n'y en a pas.
    Grille d'occupation à sommes cumulées : coût proportionnel à la surface de la carte, pas au nombre d'objets."""
    poly = polygone_carte(doc)
    x0, y0 = math.floor(min(p[0] for p in poly)), math.floor(min(p[1] for p in poly))
    x1, y1 = math.ceil(max(p[0] for p in poly)), math.ceil(max(p[1] for p in poly))
    s = max(5, math.ceil(max(x1 - x0, y1 - y0) / 400))
    nx, ny = (x1 - x0) // s + 1, (y1 - y0) // s + 1
    occ = [[0] * nx for _ in range(ny)]
    for a, b, c, d in obstacles(doc):
        i0, i1 = max(0, int((a - marge - x0) // s)), min(nx - 1, int((c + marge - x0) // s))
        j0, j1 = max(0, int((b - marge - y0) // s)), min(ny - 1, int((d + marge - y0) // s))
        for j in range(j0, j1 + 1):
            ligne = occ[j]
            for i in range(i0, i1 + 1):
                ligne[i] = 1
    cumul = [[0] * (nx + 1) for _ in range(ny + 1)]
    for j in range(ny):
        acc = 0
        for i in range(nx):
            acc += occ[j][i]
            cumul[j + 1][i + 1] = cumul[j][i + 1] + acc
    cw, ch = math.ceil(largeur / s) + 1, math.ceil(hauteur / s) + 1
    for j in range(ny - ch):
        for i in range(nx - cw):
            if cumul[j + ch][i + cw] - cumul[j][i + cw] - cumul[j + ch][i] + cumul[j][i]:
                continue
            x, y = x0 + (i + 1) * s, y0 + (j + 1) * s
            coins = [(x - marge, y - marge), (x + largeur + marge, y - marge), (x - marge, y + hauteur + marge),
                     (x + largeur + marge, y + hauteur + marge), (x + largeur / 2, y + hauteur / 2)]
            if all(dedans(c, poly) for c in coins):
                return float(x), float(y)
    return None


def zone_ou_centre(doc, largeur, hauteur):
    """Zone libre ; à défaut, rectangle centré sur la carte (remarque rendue)."""
    z = zone_libre(doc, largeur, hauteur)
    if z is not None:
        return z, "zone libre de tout cuivre"
    poly = polygone_carte(doc)
    cx = round((min(p[0] for p in poly) + max(p[0] for p in poly)) / 2)
    cy = round((min(p[1] for p in poly) + max(p[1] for p in poly)) / 2)
    return (cx - largeur / 2, cy - hauteur / 2), "aucune zone libre : centre de la carte (recouvrements possibles)"


def rectangle(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def pile(pr):
    """Sous-enregistrement de pile d'une pastille (formes alternatives, trou oblong...), None s'il est vide."""
    s = pr.sous[5] if len(pr.sous) > 5 else None
    return s.bloc if isinstance(s, SousBloc) and "formes_alt" in s.bloc.v else None


def pastilles_brutes(doc):
    return list(zip(doc.pastilles(), doc.data("Pads6").primitives))


def decrire_pastille(doc, p):
    noms = doc.designateurs()
    return {"index_pads6": p["index"], "piece": noms.get(p["composant"]) if p["composant"] != AUCUN else None,
            "nom": p["nom"], "rang": p["rang"], "couche": p["couche"], "rotation": p["rotation"],
            "position": affiche(doc, p["x"], p["y"])}


def premier_reseau(doc):
    """Réseau de la première pastille qui en a un, à défaut premier réseau de Nets6 (None si aucun)."""
    r = next((p["reseau"] for p in doc.pastilles() if p["reseau"]), None)
    return r if r is not None else next(iter(doc.reseaux()), None)


def regle_valeur(doc, type_, cles):
    """Valeur (mil) de la règle active de type `type_` la plus générale (plus grand numéro de priorité)."""
    regles = [r for r in doc.regles() if r["type"] == type_ and r["active"]]
    regles.sort(key=lambda r: -(r["priorite"] if isinstance(r["priorite"], int) else 0))
    for r in regles:
        for c in cles:
            v = valeur_mil(r["valeurs"].get(c))
            if v is not None:
                return r["nom"], v
    return None, None


def tailles_pastille(b, x_mil, y_mil, forme):
    for c in ("haut", "milieu", "bas"):
        b[f"taille_{c}_x"] = mm_vers_interne(x_mil * 0.0254)
        b[f"taille_{c}_y"] = mm_vers_interne(y_mil * 0.0254)
        b[f"forme_{c}"] = forme


def verifier_forme(p, forme, taille, ecarts, pourcentage=None):
    f = p["formes"].get("TOP") or next(iter(p["formes"].values()), None)
    if f is None or f["forme"] != forme:
        ecarts.append(f"pastille {p['index']} : forme {f and f['forme']} au lieu de {forme}")
        return
    if not (proche(f["taille"][0], taille[0]) and proche(f["taille"][1], taille[1])):
        ecarts.append(f"pastille {p['index']} : taille {f['taille']} au lieu de {taille}")
    if pourcentage is not None and f["pourcentage"] != pourcentage:
        ecarts.append(f"pastille {p['index']} : pourcentage {f['pourcentage']} au lieu de {pourcentage}")


# ---------------------------------------------------------------------------
# expériences : chacune modifie `doc` et rend objet, mesures, stockages modifiés et contrôle de relecture
# ---------------------------------------------------------------------------
def e1(doc):
    """Rectangle arrondi 1 x 2 mm à 50 % sur la première pastille CMS de la face TOP qui a une pile."""
    choix = next(((p, pr) for p, pr in pastilles_brutes(doc)
                  if not p["traversante"] and p["couche"] == "TOP" and pile(pr) is not None
                  and "rayons_coins" in pile(pr).v), None)
    if choix is None:
        raise Saut("aucune pastille CMS de la face TOP avec un sous-enregistrement de pile (formes alternatives)")
    p, pr = choix
    w, h = 1 / 0.0254, 2 / 0.0254
    tailles_pastille(pr.bloc, w, h, 1)
    pl = pile(pr)
    pl["formes_alt"] = bytes([9] * len(pl["formes_alt"]))
    pl["rayons_coins"] = bytes([50] * len(pl["rayons_coins"]))
    doc.marquer("Pads6")
    taille = (pr.bloc["taille_haut_x"] / 1e4, pr.bloc["taille_haut_y"] / 1e4)

    def controle(d2):
        ecarts = []
        verifier_forme(d2.pastilles()[p["index"]], "rect_arrondi", taille, ecarts, 50)
        return ecarts
    return {"objet": dict(decrire_pastille(doc, p), modification="forme rectangle arrondi (forme 1, formes "
                          "alternatives 9), 1 x 2 mm sur toutes les couches, rayon de coin 50 %"),
            "mesures": [mesure("rayon_coin_mm", "rayon des coins (moitié du diamètre des cercles de coin de "
                               "l'ouverture Gerber de la couche haute, ou mesure)", "nombre", 0.25, 0.01),
                        mesure("pourcentage_affiche", "« Corner Radius » (%) du panneau Properties", "nombre",
                               50, 0.5)],
            "stockages": {"Pads6"}, "controle": controle}


def e2(doc):
    """Octogone 1 x 1,6 mm sur une pastille CMS de la face TOP (sans pile de préférence)."""
    cms = [(p, pr) for p, pr in pastilles_brutes(doc) if not p["traversante"] and p["couche"] == "TOP"]
    if not cms:
        raise Saut("aucune pastille CMS sur la face TOP")
    p, pr = next(((p, pr) for p, pr in cms if pile(pr) is None), cms[0])
    w, h = 1 / 0.0254, 1.6 / 0.0254
    tailles_pastille(pr.bloc, w, h, 3)
    if pile(pr) is not None:
        pile(pr)["formes_alt"] = bytes([3] * len(pile(pr)["formes_alt"]))
    doc.marquer("Pads6")
    taille = (pr.bloc["taille_haut_x"] / 1e4, pr.bloc["taille_haut_y"] / 1e4)
    c = 0.25 * 1.0
    sommets = [(-0.5 + c, 0.8), (0.5 - c, 0.8), (0.5, 0.8 - c), (0.5, -0.8 + c), (0.5 - c, -0.8), (-0.5 + c, -0.8),
               (-0.5, -0.8 + c), (-0.5, 0.8 - c)]

    def controle(d2):
        ecarts = []
        verifier_forme(d2.pastilles()[p["index"]], "octogone", taille, ecarts)
        return ecarts
    return {"objet": dict(decrire_pastille(doc, p),
                          modification="forme octogone (3), 1 x 1,6 mm sur toutes les couches",
                          sommets_attendus_mm=[[round(x, 4), round(y, 4)] for x, y in sommets],
                          repere_sommets="repère de la pastille (avant sa rotation), mm"),
            "mesures": [mesure("chanfrein_mm", "longueur coupée sur chaque côté (écart entre un sommet du Gerber "
                               "et "
                               "le coin du rectangle 1 x 1,6 mm)", "nombre", 0.25, 0.01),
                        mesure("largeur_mm", "taille X affichée (repère de la pastille)", "nombre", 1.0, 0.01),
                        mesure("hauteur_mm", "taille Y affichée (repère de la pastille)", "nombre", 1.6, 0.01)],
            "stockages": {"Pads6"}, "controle": controle}


def anomalies_pose(doc, c, rotation, dessous):
    """Anomalies que pose.poser_composant rendrait pour la pièce `c` (dict de composants()) posée sur place,
    calculées sur des copies de ses primitives (la carte n'est pas modifiée)."""
    t = pose.Transformation((c["x_mil"] * 1e4, c["y_mil"] * 1e4), c["rotation"], c["face"] == "BOTTOM",
                            (c["x_mil"] * 1e4, c["y_mil"] * 1e4), rotation, dessous)
    paires = pose.paires_mecaniques(doc)
    return [a for st, pr in doc.primitives_du_composant(c["index"])
            for a in pose.transformer_primitive(doc, st, copy.deepcopy(pr), t, paires)]


def symetrique(loc, axe):
    """True si les positions locales `loc` (mil) sont symétriques par rapport à l'axe X local (axe 0 : y -> -y)
    ou à l'axe Y local (axe 1 : x -> -x), au demi-mil près."""
    cles = {(round(2 * x), round(2 * y)) for x, y in loc}
    return all(((round(2 * x), -round(2 * y)) if axe == 0 else (-round(2 * x), round(2 * y))) in cles
               for x, y in loc)


ROTATION_RELATIVE_E3 = 60.0  # rotation relative imposée à p0 quand la sienne ne distingue pas les deux signes


def local_affiche(doc, local, origine, dessous):
    """Position affichée (mm, origine de la carte) du point local `local` d'une pièce posée en `origine`, à 30°."""
    x, y = pose.local_vers_monde(local, origine, 30.0, dessous)
    return affiche(doc, x, y)


def e3(doc):
    """Pièce de la face TOP posée sur la face BOTTOM, rotation 30°, même position. Pièce choisie, dans l'ordre :
    avec une pastille traversante à tailles haut et bas différentes, puis asymétrique par rapport à ses deux axes
    (positions des pastilles), puis de rang le plus petit ; elle doit avoir une pastille hors de ses deux axes (p0)
    et pose doit savoir la retourner sans anomalie. Si la rotation relative r de p0 ne distingue pas les deux
    signes (30 − r et 30 + r égaux modulo 180°), p0 reçoit la rotation relative 60° avant le retournement."""
    par_piece = {}
    for p in doc.pastilles():
        if p["composant"] != AUCUN:
            par_piece.setdefault(p["composant"], []).append(p)
    candidats = []
    for c in doc.composants():
        ps = par_piece.get(c["index"], [])
        if c["face"] != "TOP" or len(ps) < 2:
            continue
        origine = (c["x_mil"], c["y_mil"])
        loc = [pose.monde_vers_local((p["x"], p["y"]), origine, c["rotation"]) for p in ps]
        hors_axes = [p for p, (x, y) in zip(ps, loc) if abs(x) > 1 and abs(y) > 1]
        if not hors_axes:
            continue
        sym = symetrique(loc, 0) or symetrique(loc, 1)
        th_diff = any(p["traversante"] and p["taille_haut"] != p["taille_bas"] for p in ps)
        candidats.append(((not th_diff, sym, c["index"]), c, hors_axes, sym))
    candidats.sort(key=lambda t: t[0])
    choix = next(((c, h, sym) for _, c, h, sym in candidats if not anomalies_pose(doc, c, 30.0, True)), None)
    if choix is None:
        raise Saut("aucune pièce de la face TOP avec une pastille hors de ses deux axes, retournable sans anomalie")
    choix, hors_axes, sym = choix

    def distingue(p):
        r = pose.rotation_relative(p["rotation"], choix["rotation"])
        return abs((2 * r + 90.0) % 180.0 - 90.0) > 5.0  # 30 − r et 30 + r différents modulo 180°
    p0 = next((p for p in hors_axes if distingue(p)), hors_axes[0])
    imposee = not distingue(p0)
    if imposee:
        doc.data("Pads6").primitives[p0["index"]].bloc["rotation"] = pose.rotation_absolue(ROTATION_RELATIVE_E3,
                                                                                           choix["rotation"])
        doc.marquer("Pads6")
        p0 = doc.pastilles()[p0["index"]]
    r0 = pose.rotation_relative(p0["rotation"], choix["rotation"])
    origine = (choix["x_mil"], choix["y_mil"])
    x0, y0 = pose.monde_vers_local((p0["x"], p0["y"]), origine, choix["rotation"])
    hyp_pos = {"convention (miroir de l'axe X local, y -> -y)": local_affiche(doc, (x0, y0), origine, True),
               "contraire (miroir de l'axe Y local, x -> -x)": local_affiche(doc, (-x0, -y0), origine, True),
               "sans retournement": local_affiche(doc, (x0, y0), origine, False)}
    n, anomalies = pose.poser_composant(doc, choix["index"], choix["x_mil"], choix["y_mil"], 30.0, True)
    if anomalies:
        raise Saut(f"pose sur la face BOTTOM impossible sans anomalie ({len(anomalies)} anomalies)")
    ps = [p for p in doc.pastilles() if p["composant"] == choix["index"]]
    p0 = doc.pastilles()[p0["index"]]
    p1 = max((p for p in ps if p["index"] != p0["index"]),
             key=lambda p: (p["x"] - p0["x"]) ** 2 + (p["y"] - p0["y"]) ** 2)
    mesures = []
    for k, p in (("p0", p0), ("p1", p1)):
        a, q = affiche(doc, p["x"], p["y"]), f"la pastille {p['nom']} (rang {p['rang']})"
        mesures += [mesure(f"{k}_x_mm", f"X affiché de {q}", "nombre", a["x_mm"], 0.01),
                    mesure(f"{k}_y_mm", f"Y affiché de {q}", "nombre", a["y_mm"], 0.01)]
    mesures += [mesure("p0_rotation_deg", f"rotation affichée de la pastille {p0['nom']} (modulo 180°)", "angle180",
                       round(p0["rotation"] % 180.0, 4), 0.1),
                mesure("p0_couche", f"couche affichée de la pastille {p0['nom']}", "prefixe",
                       p0["couche"], acceptes=["multi"] if p0["traversante"] else ["bottom"]),
                mesure("piece_couche", "couche (face) affichée de la pièce", "prefixe", "BOTTOM",
                       acceptes=["bottom"]),
                mesure("piece_rotation_deg", "rotation affichée de la pièce", "angle", 30.0, 0.1)]
    th = next((p for p in ps if p["traversante"] and p["taille_haut"] != p["taille_bas"]), None)
    non_teste = []
    if th is not None:
        mesures += [mesure("th_taille_haut_x_mm", f"pastille {th['nom']} : taille X de la couche haute", "nombre",
                           mm(th["taille_haut"][0]), 0.01),
                    mesure("th_taille_bas_x_mm", f"pastille {th['nom']} : taille X de la couche basse", "nombre",
                           mm(th["taille_bas"][0]), 0.01)]
    else:
        non_teste.append("tailles haut et bas non permutées : aucune pièce retournable de la carte n'a de pastille "
                         "traversante à tailles haut et bas différentes")
    attendues = {p["index"]: (p["x"], p["y"]) for p in ps}
    i0, rot_p0 = p0["index"], p0["rotation"]

    def controle(d2):
        ecarts = []
        c2 = d2.composants()[choix["index"]]
        if c2["face"] != "BOTTOM" or not proche(c2["rotation"], 30.0):
            ecarts.append(f"pièce : face {c2['face']}, rotation {c2['rotation']}")
        q = d2.pastilles()
        for i, (x, y) in attendues.items():
            if not (proche(q[i]["x"], x) and proche(q[i]["y"], y)):
                ecarts.append(f"pastille {i} : ({q[i]['x']}, {q[i]['y']}) au lieu de ({x}, {y})")
        if not proche(q[i0]["rotation"], rot_p0):
            ecarts.append(f"pastille {i0} : rotation {q[i0]['rotation']} au lieu de {rot_p0}")
        return ecarts
    noms = doc.designateurs()
    avant = {"face": "TOP", "rotation": choix["rotation"], "position": affiche(doc, choix["x_mil"], choix["y_mil"])}
    hyp_rot = {"convention (pièce − r)": round((30.0 - r0) % 180.0, 4),
               "contraire (pièce + r)": round((30.0 + r0) % 180.0, 4)}
    objet = {"piece": noms.get(choix["index"]), "index_components6": choix["index"], "avant": avant,
             "apres": {"face": "BOTTOM", "rotation": 30.0}, "primitives_transformees": n,
             "symetrie": ("positions des pastilles symétriques par rapport à un axe de la pièce : seules les "
                          "positions des pastilles nommées (p0, p1) distinguent les miroirs") if sym
             else "pièce asymétrique par rapport à ses deux axes",
             "p0": {"index_pads6": i0, "nom": p0["nom"], "rotation_relative_avant": round(r0, 4),
                    "rotation_relative_imposee": imposee, "hypotheses_rotation_mod180": hyp_rot,
                    "hypotheses_position": hyp_pos},
             "pastilles": [decrire_pastille(doc, p) for p in ps],
             "tailles_haut_bas": "relevées" if th else "non testées : aucune pastille traversante à tailles "
                                                       "haut et bas différentes"}
    return {"objet": objet, "mesures": mesures, "non_teste": non_teste,
            "stockages": set(P.FLUX_PRIMITIVES_GEO) | {"Components6"}, "controle": controle}


ETIQUETTES_KEEPOUT = {0x01: ["via"], 0x02: ["track", "piste"], 0x04: ["copper", "cuivre"],
                      0x08: ["smd pad", "pastille cms", "smd"], 0x10: ["th pad", "pastille traversante", "th"]}


def ajouter_keepout(doc, contour, bits):
    """Région keep-out (couche KEEPOUT) de restrictions `bits`, comme les keep-out écrits par Altium
    (drapeaux2 = 2, sans réseau, propriété KEEPOUTRESTRICTIONS), dans Regions6 et son jumeau."""
    i = doc.ajouter_region("KEEPOUT", contour, None)
    for st in ("Regions6", "ShapeBasedRegions6"):
        f = doc.data(st)
        if len(f) > i:
            s0 = f.primitives[i].sous[0]
            if isinstance(s0, SousRegion):
                s0.entete["drapeaux2"] = 2
                s0.props["KEEPOUTRESTRICTIONS"] = str(bits)
    return i


def e4(doc):
    """Une zone keep-out par bit de restriction (table provisoire pcbdoc.BITS_KEEPOUT)."""
    bits = P.BITS_KEEPOUT
    cote, pas = 60.0, 100.0
    (x, y), remarque = zone_ou_centre(doc, pas * (len(bits) - 1) + cote, cote)
    zones, indices = [], []
    for k, (b, nom) in enumerate(bits):
        x0 = x + k * pas
        indices.append(ajouter_keepout(doc, rectangle(x0, y, cote, cote), b))
        zones.append({"zone": k + 1, "bit": f"0x{b:02X}", "effet_suppose": nom,
                      "centre": affiche(doc, x0 + cote / 2, y + cote / 2)})

    def controle(d2):
        r = {x["index"]: x for x in d2.regions()}
        return [f"zone {k + 1} : restrictions {r[i]['restrictions_keepout']}" for k, (i, (b, _)) in
                enumerate(zip(indices, bits)) if not (r[i]["keepout"] and r[i]["restrictions_keepout"] == b)]
    return {"objet": {"zones": zones, "couche": "KEEPOUT", "cote_mil": cote, "placement": remarque},
            "mesures": [mesure(f"zone{z['zone']}_restriction", f"zone {z['zone']} (bit {z['bit']}) : libellé de la "
                               "restriction cochée (une seule attendue)", "texte", ETIQUETTES_KEEPOUT[b][0],
                               acceptes=ETIQUETTES_KEEPOUT[b]) for z, (b, _) in zip(zones, bits)],
            "stockages": {"Regions6", "ShapeBasedRegions6", "PrimitiveGuids"}, "controle": controle}


def e5(doc):
    """Piste à 0,1 mm d'un keep-out (à la moitié de l'isolement si celui-ci ne dépasse pas 0,1 mm) et piste témoin
    à isolement + 0,1 mm, DRC."""
    regle, iso = regle_valeur(doc, "Clearance", ("GAP", "GENERICCLEARANCE"))
    if iso is None:
        raise Saut("aucune règle Clearance active")
    if iso <= 0:
        raise Saut(f"isolement de la règle {regle} nul : expérience sans objet")
    w = 10.0
    g1 = min(0.1 / 0.0254, iso / 2)
    g2 = iso + 0.1 / 0.0254
    hauteur = 100 + g1 + g2 + 2 * w
    z = zone_libre(doc, 240, hauteur)
    if z is None:
        raise Saut("aucune zone libre de 240 x %.0f mil dans la carte" % hauteur)
    x0, y0 = z[0] + 20, z[1] + g2 + w
    reseau = premier_reseau(doc)
    ik = ajouter_keepout(doc, rectangle(x0, y0, 200, 100), 0x1F)
    ya, yb = y0 + 100 + g1 + w / 2, y0 - g2 - w / 2
    ia = doc.ajouter_piste("TOP", (x0, ya), (x0 + 200, ya), w, reseau)
    ib = doc.ajouter_piste("TOP", (x0, yb), (x0 + 200, yb), w, reseau)

    def controle(d2):
        t, r = d2.pistes(), {x["index"]: x for x in d2.regions()}
        ecarts = [] if r[ik]["restrictions_keepout"] == 0x1F else ["keep-out : restrictions"]
        for i, yy in ((ia, ya), (ib, yb)):
            if not (proche(t[i]["y1"], yy) and proche(t[i]["largeur"], w) and t[i]["reseau"] == reseau):
                ecarts.append(f"piste {i} : y {t[i]['y1']} au lieu de {yy}")
        return ecarts
    return {"objet": {"keepout": {"couche": "KEEPOUT", "restrictions": "0x1F", "coin_bas_gauche": affiche(doc, x0, y0),
                                  "taille_mil": [200, 100]},
                      "piste_proche": {"couche": "TOP", "reseau": reseau, "largeur_mil": w,
                                       "ecart_au_keepout_mil": round(g1, 4),
                                       "debut": affiche(doc, x0, ya), "fin": affiche(doc, x0 + 200, ya)},
                      "piste_temoin": {"couche": "TOP", "reseau": reseau, "largeur_mil": w,
                                       "ecart_au_keepout_mil": round(g2, 4),
                                       "debut": affiche(doc, x0, yb), "fin": affiche(doc, x0 + 200, yb)},
                      "regle_isolement": regle, "isolement_mil": iso},
            "mesures": [mesure("violation_piste_proche", "le DRC signale un défaut d'isolement entre la piste "
                               "proche et le keep-out", "booleen", True),
                        mesure("violation_piste_temoin", "le DRC signale un défaut d'isolement entre la piste témoin "
                               "et le keep-out", "booleen", False),
                        mesure("ecart_mm", "écart mesuré entre le bord de la piste proche et le keep-out", "nombre",
                               round(mm(g1), 4), 0.005)],
            "stockages": {"Regions6", "ShapeBasedRegions6", "Tracks6", "PrimitiveGuids"}, "controle": controle}


GENRES_AFFICHES = {"standard": ["standard"], "mecanique": ["mechanical", "mecanique"],
                   "graphique": ["graphical", "graphique"], "liaison_reseaux": ["net tie", "liaison"],
                   "cavalier": ["jumper", "cavalier"]}


def e6(doc):
    """COMPONENTKIND k (0 à 6) sur la pièce de rang k de Components6."""
    cs = doc.composants()
    if len(cs) < 7:
        raise Saut(f"la base a {len(cs)} pièces, 7 nécessaires (une par COMPONENTKIND 0 à 6)")
    f = doc.data("Components6")
    for k in range(7):
        f[k]["COMPONENTKIND"] = str(k)
    doc.marquer("Components6")
    noms = doc.designateurs()

    def controle(d2):
        c2 = d2.composants()
        return [f"pièce {k} : COMPONENTKIND {c2[k]['genre_altium']}" for k in range(7) if c2[k]["genre_altium"] != k]
    return {"objet": {"pieces": [{"piece": noms.get(k), "index_components6": k, "componentkind": k,
                                  "genre_suppose": P.GENRES_COMPOSANT[k],
                                  "position": affiche(doc, cs[k]["x_mil"], cs[k]["y_mil"])} for k in range(7)]},
            "mesures": [mesure(f"piece{k}_type", f"pièce {noms.get(k)} (COMPONENTKIND {k}) : « Component Type » "
                               "affiché", "prefixe", GENRES_AFFICHES[P.GENRES_COMPOSANT[k]][0],
                               acceptes=GENRES_AFFICHES[P.GENRES_COMPOSANT[k]]) for k in range(7)],
            "stockages": {"Components6"}, "controle": controle}


def e7(doc):
    """Vias de part et d'autre de la plus longue ligne de partage droite d'un plan négatif, à demi-largeur de la
    ligne + rayon + PlaneClearance ± 2 mil de son axe."""
    regle, jeu = regle_valeur(doc, "PlaneClearance", ("CLEARANCE",))
    if jeu is None:
        raise Saut("aucune règle PlaneClearance active")
    vs = doc.vias()
    if vs:
        compte = {}
        for v in vs:
            compte[(v["diametre"], v["trou"])] = compte.get((v["diametre"], v["trou"]), 0) + 1
        d, t = max(sorted(compte), key=lambda k: compte[k])
    else:
        d, t = VIA_SANS_MODELE
    choix = None
    for pl in doc.plans_negatifs():
        lignes = [l for l in pl["lignes_partage"] if l["type"] == "piste"]
        lignes.sort(key=lambda l: -math.hypot(l["x2"] - l["x1"], l["y2"] - l["y1"]))
        if lignes and math.hypot(lignes[0]["x2"] - lignes[0]["x1"], lignes[0]["y2"] - lignes[0]["y1"]) >= 4 * (d + 20):
            choix = (pl, lignes[0])
            break
    if choix is None:
        raise Saut("aucun plan négatif avec une ligne de partage droite assez longue")
    pl, l = choix
    long_ = math.hypot(l["x2"] - l["x1"], l["y2"] - l["y1"])
    ux, uy = (l["x2"] - l["x1"]) / long_, (l["y2"] - l["y1"]) / long_
    nx_, ny_ = -uy, ux
    mx, my = (l["x1"] + l["x2"]) / 2, (l["y1"] + l["y2"]) / 2
    base = l["largeur"] / 2 + d / 2 + jeu

    regions = [([(s["x"], s["y"]) for s in r["contour"]], r["reseau"]) for r in pl["regions"]]

    def reseau_du_cote(px, py):
        dans = [r for r in regions if len(r[0]) >= 3 and dedans((px, py), r[0])]
        return min(dans, key=lambda r: aire(r[0]))[1] if dans else pl["reseau"]
    poses, mesures = [], []
    for cote, s in (("A", 1), ("B", -1)):
        reseau = reseau_du_cote(mx + nx_ * s * (base + 2), my + ny_ * s * (base + 2))
        if not reseau:
            continue
        for delta, decal in ((2, -(d + 20)), (-2, d + 20)):
            x = round(mx + nx_ * s * (base + delta) + ux * decal, 4)
            y = round(my + ny_ * s * (base + delta) + uy * decal, 4)
            i = doc.ajouter_via((x, y), d, t, reseau)
            cle = f"via_{cote}{'plus' if delta > 0 else 'moins'}2_connectee"
            poses.append({"index_vias6": i, "cote": cote, "reseau": reseau, "distance_axe_mil": round(base + delta, 4),
                          "position": affiche(doc, x, y), "x": x, "y": y, "cle": cle})
            mesures.append(mesure(cle, f"via {reseau} côté {cote}, axe à {round(base + delta, 3)} mil de l'axe "
                                  "de la "
                                  f"ligne : liaison au plan affichée sur {pl['nom_altium']}", "booleen", delta > 0))
    if not poses:
        raise Saut("les deux côtés de la ligne de partage sont sans réseau")

    def controle(d2):
        v2 = d2.vias()
        return [f"via {q['index_vias6']}" for q in poses
                if not (proche(v2[q["index_vias6"]]["x"], q["x"]) and proche(v2[q["index_vias6"]]["y"], q["y"])
                        and v2[q["index_vias6"]]["reseau"] == q["reseau"]
                        and proche(v2[q["index_vias6"]]["diametre"], d))]
    return {"objet": {"plan": pl["nom_altium"], "reseau_du_plan": pl["reseau"], "regle": regle,
                      "plane_clearance_mil": jeu,
                      "ligne_partage": {"debut": affiche(doc, l["x1"], l["y1"]), "fin": affiche(doc, l["x2"], l["y2"]),
                                        "largeur_mil": l["largeur"]},
                      "via": {"diametre_mil": d, "trou_mil": t,
                              "origine": "la plus fréquente de la base" if vs else "valeur d'essai"},
                      "vias": [{k: v for k, v in q.items() if k not in ("x", "y")} for q in poses]},
            "mesures": mesures, "stockages": {"Vias6", "PrimitiveGuids", "DrillManager"}, "controle": controle}


_CLE_CONTOUR = re.compile(r"(KIND|VX|VY|CX|CY|SA|EA|R)\d+$")


def arc_points(cx, cy, r, a0, a1, pas=10.0):
    """Points d'un arc de a0 à a1 (degrés, sens donné par le signe de a1 - a0), extrémités comprises."""
    n = max(1, math.ceil(abs(a1 - a0) / pas))
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / n)))
            for k in range(n + 1)]


def e8(doc):
    """Contour à arcs dans les deux sens : rectangle de la boîte du contour à coins arrondis (arcs parcourus dans
    le sens trigonométrique) et encoche en demi-cercle au milieu du bord bas (arc parcouru en sens horaire)."""
    pts = [(s["x"], s["y"]) for s in doc.contour_carte()]
    if len(pts) < 3:
        raise Saut("la carte n'a pas de contour dans Board6")
    rc = doc.regions_carte()
    if len(rc) > 1:
        raise Saut(f"{len(rc)} régions de carte (carte rigide-flexible) : contour unique attendu")
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    R = round(min(min(x1 - x0, y1 - y0) / 8, 200.0), 2)
    rho = round(min(min(x1 - x0, y1 - y0) / 8, 150.0), 2)
    xm = round((x0 + x1) / 2, 2)
    # (KIND, VX, VY, CX, CY, R, SA, EA) ; un arc va de SA à EA en sens trigonométrique ; sommet = point de départ
    # du parcours : point SA (parcours trigonométrique) ou point EA (parcours horaire, encoche)
    sommets = [(0, x0 + R, y0, 0, 0, 0, 0, 0), (1, xm - rho, y0, xm, y0, rho, 0.0, 180.0),
               (0, xm + rho, y0, 0, 0, 0, 0, 0), (1, x1 - R, y0, x1 - R, y0 + R, R, 270.0, 360.0),
               (0, x1, y0 + R, 0, 0, 0, 0, 0), (1, x1, y1 - R, x1 - R, y1 - R, R, 0.0, 90.0),
               (0, x1 - R, y1, 0, 0, 0, 0, 0), (1, x0 + R, y1, x0 + R, y1 - R, R, 90.0, 180.0),
               (0, x0, y1 - R, 0, 0, 0, 0, 0), (1, x0, y0 + R, x0 + R, y0 + R, R, 180.0, 270.0),
               (0, x0 + R, y0, 0, 0, 0, 0, 0)]
    nouveaux = []
    for k, (kind, vx, vy, cx, cy, r, sa, ea) in enumerate(sommets):
        nouveaux += [[f"KIND{k}", str(kind)], [f"VX{k}", formater_mil(vx)], [f"VY{k}", formater_mil(vy)],
                     [f"CX{k}", formater_mil(cx)], [f"CY{k}", formater_mil(cy)], [f"SA{k}", pose._fmt_rot(sa)],
                     [f"EA{k}", pose._fmt_rot(ea)], [f"R{k}", formater_mil(r)]]
    b = doc.board
    el = b.elements
    pos = next(i for i, e in enumerate(el) if _CLE_CONTOUR.match(e[0]))
    avant = sum(1 for e in el[:pos] if not _CLE_CONTOUR.match(e[0]))
    garde = [e for e in el if not _CLE_CONTOUR.match(e[0])]
    b.elements = garde[:avant] + nouveaux + garde[avant:]
    doc.marquer("Board6")
    # même forme en segments (pas de 10°) pour la région de carte
    poly = [(x0 + R, y0)] + arc_points(xm, y0, rho, 180.0, 0.0) + arc_points(x1 - R, y0 + R, R, 270.0, 360.0) \
        + arc_points(x1 - R, y1 - R, R, 0.0, 90.0) + arc_points(x0 + R, y1 - R, R, 90.0, 180.0) \
        + arc_points(x0 + R, y0 + R, R, 180.0, 270.0)
    poly = [(round(x, 4), round(y, 4)) for x, y in poly]
    poly = [p for i, p in enumerate(poly) if i == 0 or math.dist(p, poly[i - 1]) > 1e-3]
    if math.dist(poly[0], poly[-1]) <= 1e-3:
        poly.pop()
    stockages = {"Board6"}
    if rc:
        s0 = doc.data("BoardRegions").primitives[rc[0]["index"]].sous[0]
        if s0.etendu:
            s0.contour = [(0, round(x * 1e4), round(y * 1e4), 0, 0, 0, 0.0, 0.0) for x, y in poly + poly[:1]]
        else:
            s0.contour = [(round(x * 1e4) * 1.0, round(y * 1e4) * 1.0) for x, y in poly]
        doc.marquer("BoardRegions")
        stockages.add("BoardRegions")
    attendu = [(s[0], s[1], s[2]) for s in sommets]

    def controle(d2):
        c2 = d2.contour_carte()
        ecarts = [] if len(c2) == len(attendu) else [f"{len(c2)} sommets au lieu de {len(attendu)}"]
        for s, (kind, vx, vy) in zip(c2, attendu):
            if s["type"] != kind or not (proche(s["x"], vx) and proche(s["y"], vy)):
                ecarts.append(f"sommet ({s['x']}, {s['y']}) au lieu de ({vx}, {vy})")
        if rc and len(d2.regions_carte()[0]["contour"]) != len(poly):
            ecarts.append("région de carte : nombre de sommets")
        return ecarts
    return {"objet": {"boite": [affiche(doc, x0, y0), affiche(doc, x1, y1)], "rayon_coins_mil": R,
                      "rayon_encoche_mil": rho,
                      "encoche": {"centre": affiche(doc, xm, y0), "point_le_plus_profond": affiche(doc, xm, y0 + rho)},
                      "sommets_board6": [{"kind": s[0], "v": affiche(doc, s[1], s[2]), "sa": s[6], "ea": s[7]}
                                         for s in sommets],
                      "region_carte": f"{len(poly)} sommets (arcs en segments de 10°)" if rc else "aucune",
                      "aire_attendue_mm2": round(aire(poly) * 0.0254 ** 2, 3)},
            "mesures": [mesure("coins_convexes", "les quatre coins sont arrondis vers l'extérieur (forme de la "
                               "carte en 2D "
                               "et en 3D)", "booleen", True),
                        mesure("encoche_concave", "le milieu du bord bas est creusé vers l'intérieur de la carte "
                               "(demi-cercle)", "booleen", True),
                        mesure("profondeur_encoche_mm", "distance du bord bas au point le plus profond de l'encoche",
                               "nombre", mm(rho), 0.05)],
            "stockages": stockages, "controle": controle}


def e9(doc):
    """Pastille traversante à trou oblong tournée de 30° de plus (trou oblong créé s'il n'y en a aucun)."""
    th = [(p, pr) for p, pr in pastilles_brutes(doc)
          if p["traversante"] and pile(pr) is not None and "rotation_oblong" in pile(pr).v]
    if not th:
        raise Saut("aucune pastille traversante avec un sous-enregistrement de pile (trou oblong)")
    p, pr = next(((p, pr) for p, pr in th if pile(pr)["forme_trou"] == 2), th[0])
    b, pl = pr.bloc, pile(pr)
    cree = pl["forme_trou"] != 2
    if cree:
        dt = b["trou"] / 1e4
        pl["forme_trou"] = 2
        pl["longueur_oblong"] = round(2 * dt * 1e4)
        pl["rotation_oblong"] = 90.0
        tailles_pastille(b, dt + 24, 2 * dt + 24, 1)
    rot = round((b["rotation"] + 30.0) % 360.0, 6)
    b["rotation"] = rot
    doc.marquer("Pads6")
    rt = pl["rotation_oblong"]
    tx, ty = b["taille_haut_x"], b["taille_haut_y"]
    aligne = (tx < ty and round(rt) % 180 == 90) or (tx > ty and round(rt) % 180 == 0)
    mesures = [mesure("angle_axe_trou_deg", "angle (repère de la carte, modulo 180°) du grand axe du trou oblong",
                      "angle180", round((rot + rt) % 180.0, 4), 2.0),
               mesure("rotation_pastille_deg", "rotation affichée de la pastille", "angle", rot, 0.1)]
    if aligne:
        mesures.append(mesure("trou_dans_cuivre", "le trou oblong reste dans le cuivre de la pastille", "booleen",
                              True))

    def controle(d2):
        q = d2.pastilles()[p["index"]]
        return [] if (proche(q["rotation"], rot) and q["percage"]["forme"] == "oblong"
                      and proche(q["percage"]["rotation"], rt)) \
            else [f"pastille {p['index']} : rotation {q['rotation']}, trou {q['percage']}"]
    return {"objet": dict(decrire_pastille(doc, doc.pastilles()[p["index"]]), rotation_avant=p["rotation"],
                          rotation_trou=rt, trou_cree=cree,
                          hypotheses={"rotation_relative (convention)": round((rot + rt) % 180.0, 4),
                                      "rotation_absolue (contraire)": round(rt % 180.0, 4)}),
            "mesures": mesures, "stockages": {"Pads6"}, "controle": controle}


def t1(doc):
    """Carte privée de ses vias libres et de ses pistes libres (hors lignes de partage des plans), puis une piste,
    une via et une région ajoutées sans modèle (prototypes de gabarits.py)."""
    plans = {c["nom_altium"] for c in doc.couches_cuivre() if c["type"] == "plan_negatif"}
    pistes = {t["index"] for t in doc.pistes()
              if t["composant"] == AUCUN and t["polygone"] == AUCUN and t["couche"] not in plans}
    vias = {v["index"] for v in doc.vias() if v["composant"] == AUCUN}
    supprimees = pose.supprimer_primitives(doc, {"Tracks6": pistes, "Vias6": vias}) if (pistes or vias) else {}
    n_t, n_v, n_r = len(doc.pistes()), len(doc.vias()), len(doc.regions())
    d, t = VIA_SANS_MODELE
    (x, y), remarque = zone_ou_centre(doc, 400, 160)
    reseau = premier_reseau(doc)
    it = doc.ajouter_piste("TOP", (x, y + 20), (x + 200, y + 20), 10, reseau)
    iv = doc.ajouter_via((x + 300, y + 20), d, t, reseau)
    ir = doc.ajouter_region("TOP", rectangle(x, y + 60, 150, 100), reseau)

    def controle(d2):
        ecarts = []
        if (len(d2.pistes()), len(d2.vias()), len(d2.regions())) != (n_t + 1, n_v + 1, n_r + 1):
            ecarts.append("nombre de pistes, vias ou régions")
        if len([q for q in d2.vias() if q["composant"] == AUCUN]) != 1:
            ecarts.append("la carte devait n'avoir que la via ajoutée")
        if pose.verifier_guids(d2):
            ecarts.append("PrimitiveGuids incohérent")
        return ecarts
    return {"objet": {"supprimees": {k: v for k, v in sorted(supprimees.items())}, "placement": remarque,
                      "reseau": reseau,
                      "piste": {"index_tracks6": it, "couche": "TOP", "largeur_mil": 10,
                                "debut": affiche(doc, x, y + 20),
                                "fin": affiche(doc, x + 200, y + 20)},
                      "via": {"index_vias6": iv, "diametre_mil": d, "trou_mil": t,
                              "position": affiche(doc, x + 300, y + 20)},
                      "region": {"index_regions6": ir, "couche": "TOP", "coin_bas_gauche": affiche(doc, x, y + 60),
                                 "taille_mil": [150, 100]},
                      "comptes_attendus": {"pistes": n_t + 1, "vias": n_v + 1, "regions": n_r + 1}},
            "mesures": [mesure("ouverture_sans_message", "le fichier s'ouvre sans message d'erreur ni d'avertissement",
                               "booleen", True),
                        mesure("objets_visibles", "piste, via et région visibles aux positions annoncées",
                               "booleen", True),
                        mesure("via_x_mm", "X affiché de la via", "nombre", affiche(doc, x + 300, y)["x_mm"], 0.01),
                        mesure("via_y_mm", "Y affiché de la via", "nombre", affiche(doc, x, y + 20)["y_mm"], 0.01),
                        mesure("enregistrement_rouverture", "après « Enregistrer sous » dans Altium, la copie se "
                               "rouvre "
                               "sans message", "booleen", True)],
            "stockages": {"Tracks6", "Vias6", "Regions6", "ShapeBasedRegions6", "PrimitiveGuids", "DrillManager",
                          "*violations*"}, "controle": controle}


# (identifiant, fichier, titre, convention de l'annexe E, fonction)
EXPERIENCES = [
    ("E1", "E1_rect_arrondi.PcbDoc", "rectangle arrondi",
     "rectangle arrondi : rayon = pourcentage/100 × min(largeur, hauteur)/2 (annexe E-1)", e1),
    ("E2", "E2_octogone.PcbDoc", "octogone", "octogone : chanfrein de 25 % du petit côté (annexe E-2)", e2),
    ("E3", "E3_piece_dessous_30.PcbDoc", "pièce dessous à 30°",
     "pièce dessous : rotation relative = rotation de la pièce − rotation de la pastille ; tailles haut et bas non "
     "permutées au retournement (annexe E-3, §0.4)", e3),
    ("E4", "E4_keepout_bits.PcbDoc", "bits des keep-out",
     "bits de restriction des keep-out : 0x01 vias, 0x02 pistes, 0x04 remplissages (cuivre), 0x08 pastilles CMS, "
     "0x10 pastilles traversantes (annexe E-4)", e4),
    ("E5", "E5_isolement_keepout.PcbDoc", "isolement cuivre / keep-out",
     "le DRC d'Altium applique l'isolement entre cuivre et keep-out (annexe E-5, §4.4)", e5),
    ("E6", "E6_genres_composants.PcbDoc", "COMPONENTKIND",
     "COMPONENTKIND : 0 et 5 standard, 1 mécanique, 2 graphique, 3 et 4 liaison de réseaux, 6 cavalier "
     "(annexe E-6)", e6),
    ("E7", "E7_plan_negatif.PcbDoc", "plan négatif",
     "plan négatif : zone = contour − retrait − régions de partage ; via relié si son centre est dans la zone "
     "à au "
     "moins rayon + PlaneClearance du bord (annexe E-7)", e7),
    ("E8", "E8_contour_arcs.PcbDoc", "contour à arcs",
     "contour de Board6 : un arc (KIND=1) va de SA à EA en sens trigonométrique ; le sommet est son point de départ "
     "(point SA : parcours trigonométrique ; point EA : parcours horaire) (annexe E-8)", e8),
    ("E9", "E9_trou_oblong.PcbDoc", "trou oblong tourné",
     "rotation d'un trou oblong relative à la pastille : axe dans la carte = rotation de la pastille + rotation du "
     "trou (§2.2, complément de l'annexe E)", e9),
    ("T1", "T1_ajouts_sans_modele.PcbDoc", "ajouts sans modèle",
     "piste, via et région construites depuis gabarits.py sur une carte sans via ni piste libre : fichier accepté "
     "par Altium (complément de l'annexe E)", t1),
]


# ---------------------------------------------------------------------------
# fabrication
# ---------------------------------------------------------------------------
def flux_olefile(chemin):
    o = olefile.OleFileIO(chemin)
    try:
        return {"/".join(e): o.openstream(e).read() for e in o.listdir(streams=True, storages=False)}
    finally:
        o.close()


def _autorise(storage, stockages):
    return storage in stockages or ("*violations*" in stockages and bool(pose._FLUX_VIOLATIONS.match(storage)))


def controler_fichier(base_flux, chemin, stockages, controle):
    """Relecture d'un fichier d'essai : olefile lit tous les flux ; PcbDoc les relit aux mêmes octets ; flux hors
    des stockages prévus identiques à la base ; modification présente et exacte (`controle`)."""
    try:
        sortie = flux_olefile(chemin)
    except Exception as e:  # fichier illisible par olefile : défaut du fichier écrit
        return [f"olefile : {K.message_sans_chemin(e)}"]
    ecarts = [f"flux {c} modifié hors des stockages prévus" for c, o in base_flux.items()
              if not _autorise(c.split("/")[0], stockages) and sortie.get(c) != o]
    ecarts += [f"flux {c} ajouté hors des stockages prévus" for c in sortie
               if c not in base_flux and not _autorise(c.split("/")[0], stockages)]
    d2 = PcbDoc.ouvrir(chemin)
    ecarts += [f"flux {c} : relecture différente d'olefile" for c in sortie if d2.octets_flux(c) != sortie[c]]
    return ecarts + controle(d2)


def ecrire_json(chemin, donnees):
    K.ecrire_texte_atomique(chemin, json.dumps(donnees, ensure_ascii=False, indent=2) + "\n")


def fabriquer(base, sortie):
    K.verifier_fichiers([base])
    doc_base = PcbDoc.ouvrir(base)
    with open(base, "rb") as f:
        empreinte = hashlib.sha256(f.read()).hexdigest()
    base_flux = flux_olefile(base)
    os.makedirs(sortie, exist_ok=True)
    attendus = {"format": FORMAT_ATTENDUS, "base": {"nom": doc_base.nom, "sha256": empreinte,
                                                   "origine_mil": list(doc_base.origine())},
                "regle_de_conclusion": REGLE, "experiences": []}
    resultats = {"format": FORMAT_RESULTATS, "base_sha256": empreinte, "version_altium": "", "date": "",
                 "experiences": {}}
    defauts = 0
    for ident, fichier, titre, convention, fonction in EXPERIENCES:
        doc = PcbDoc.ouvrir(base)
        entree = {"id": ident, "titre": titre, "convention": convention}
        try:
            r = fonction(doc)
        except Saut as s:
            entree.update(statut="sautee", motif=str(s), fichier=None)
            attendus["experiences"].append(entree)
            print(f"{ident} {titre} : sautée : {s}")
            continue
        chemin = os.path.join(sortie, fichier)
        doc.enregistrer(chemin)
        ecarts = controler_fichier(base_flux, chemin, r["stockages"], r["controle"])
        entree.update(statut="fabriquee", fichier=fichier, objet=r["objet"], mesures=r["mesures"],
                      non_teste=r.get("non_teste", []), stockages_modifies=sorted(r["stockages"]))
        attendus["experiences"].append(entree)
        resultats["experiences"][ident] = {"faite": False, "valeurs": {m["cle"]: None for m in r["mesures"]},
                                           "remarques": ""}
        if ecarts:
            defauts += 1
            print(f"{ident} {titre} : {fichier} : NON CONFORME")
            for e in ecarts:
                print(f"  - {e}")
        else:
            print(f"{ident} {titre} : {fichier} : conforme ({len(r['mesures'])} valeurs à relever)")
    ecrire_json(os.path.join(sortie, "attendus.json"), attendus)
    ecrire_json(os.path.join(sortie, "resultats_a_remplir.json"), resultats)
    faites = sum(1 for e in attendus["experiences"] if e["statut"] == "fabriquee")
    print(f"{faites} fichiers d'essai, {len(EXPERIENCES) - faites} expériences sautées, {defauts} non conformes ; "
          "attendus.json, resultats_a_remplir.json")
    return K.CODE_NEGATIF if defauts else K.CODE_OK


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------
def normaliser(texte):
    t = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode("ascii")
    return " ".join(t.lower().replace("_", " ").split())


def conforme(m, v):
    """True si la valeur relevée `v` satisfait la mesure `m` ; K.ErreurEntree si `v` n'a pas le bon type."""
    t = m["type"]
    if t in ("nombre", "angle", "angle180"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise K.ErreurEntree(f"{m['cle']} : nombre attendu")
        ecart = v - m["attendu"]
        if t == "angle":
            ecart = (ecart + 180.0) % 360.0 - 180.0
        elif t == "angle180":
            ecart = (ecart + 90.0) % 180.0 - 90.0
        return abs(ecart) <= m["tolerance"] + 1e-9
    if t == "booleen":
        if not isinstance(v, bool):
            raise K.ErreurEntree(f"{m['cle']} : true ou false attendu")
        return v == m["attendu"]
    if not isinstance(v, str):
        raise K.ErreurEntree(f"{m['cle']} : texte attendu")
    n = normaliser(v)
    if t == "texte":
        return n in [normaliser(a) for a in m["acceptes"]]
    if t == "prefixe":
        return any(n.startswith(normaliser(a)) for a in m["acceptes"])
    raise K.ErreurEntree(f"{m['cle']} : type de mesure inconnu : {t}")


def lire_json(chemin, format_):
    K.verifier_fichiers([chemin])
    try:
        with open(chemin, encoding="utf-8") as f:
            d = json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        raise K.ErreurEntree(f"{K.nom_seul(chemin)} : JSON illisible ({type(e).__name__})") from None
    if not isinstance(d, dict) or d.get("format") != format_:
        raise K.ErreurEntree(f"{K.nom_seul(chemin)} : format {format_} attendu")
    return d


TYPES_MESURE = {"nombre": "tolerance", "angle": "tolerance", "angle180": "tolerance", "booleen": None,
                "texte": "acceptes", "prefixe": "acceptes"}


def controler_attendus(att, nom):
    """K.ErreurEntree si `att` n'a pas la structure écrite par la fabrication (fichier modifié à la main)."""
    def mesure_valide(m):
        if not (isinstance(m, dict) and isinstance(m.get("cle"), str) and m.get("type") in TYPES_MESURE
                and "attendu" in m):
            return False
        champ = TYPES_MESURE[m["type"]]
        if champ == "tolerance":
            return all(isinstance(m.get(k), (int, float)) and not isinstance(m.get(k), bool)
                       for k in ("attendu", "tolerance"))
        return champ is None or (isinstance(m.get(champ), list) and all(isinstance(a, str) for a in m[champ]))

    def experience_valide(e):
        if not (isinstance(e, dict)
                and all(isinstance(e.get(k), str) for k in ("id", "titre", "convention", "statut"))):
            return False
        return e["statut"] != "fabriquee" or (isinstance(e.get("mesures"), list)
                                              and all(mesure_valide(m) for m in e["mesures"])
                                              and isinstance(e.get("non_teste", []), list))
    base = att.get("base")
    if not (isinstance(base, dict) and isinstance(base.get("sha256"), str) and isinstance(base.get("nom"), str)
            and isinstance(att.get("experiences"), list) and all(experience_valide(e) for e in att["experiences"])):
        raise K.ErreurEntree(f"{nom} : structure invalide (fichier attendus.json écrit par la fabrication attendu)")


def controler_resultats(res, nom):
    """Relevés de `res` ({identifiant: {faite, valeurs, remarques}}) après contrôle de leur structure ;
    K.ErreurEntree (code 2) avec le chemin de la clé fautive sinon."""
    if res.get("base_sha256") is not None and not isinstance(res["base_sha256"], str):
        raise K.ErreurEntree(f"{nom} : base_sha256 : texte attendu")
    releves = res.get("experiences")
    if not isinstance(releves, dict):
        raise K.ErreurEntree(f"{nom} : experiences : objet JSON {{identifiant: relevé}} attendu")
    for ident, r in releves.items():
        if not isinstance(r, dict):
            raise K.ErreurEntree(f"{nom} : experiences.{ident} : objet JSON {{faite, valeurs, remarques}} attendu")
        if not isinstance(r.get("faite", False), bool):
            raise K.ErreurEntree(f"{nom} : experiences.{ident}.faite : true ou false attendu")
        if r.get("valeurs") is not None and not isinstance(r["valeurs"], dict):
            raise K.ErreurEntree(f"{nom} : experiences.{ident}.valeurs : objet JSON {{clé: valeur}} attendu")
        if r.get("remarques") is not None and not isinstance(r["remarques"], str):
            raise K.ErreurEntree(f"{nom} : experiences.{ident}.remarques : texte attendu")
    return releves


def verifier(chemin_resultats, chemin_attendus, chemin_json=None):
    att = lire_json(chemin_attendus, FORMAT_ATTENDUS)
    controler_attendus(att, K.nom_seul(chemin_attendus))
    res = lire_json(chemin_resultats, FORMAT_RESULTATS)
    releves = controler_resultats(res, K.nom_seul(chemin_resultats))
    if res.get("base_sha256") and res["base_sha256"] != att["base"]["sha256"]:
        raise K.ErreurEntree("les résultats ne portent pas sur la même carte de base que les attendus")
    inconnus = sorted(set(releves) - {e["id"] for e in att["experiences"]})
    if inconnus:
        print("relevés ignorés (identifiants absents de attendus.json) : " + ", ".join(map(str, inconnus)))
    verdicts = []
    for e in att["experiences"]:
        v = {"id": e["id"], "titre": e["titre"], "convention": e["convention"], "verdict": "non faite", "ecarts": [],
             "motif": "", "non_teste": list(e.get("non_teste", []))}
        r = releves.get(e["id"]) or {}
        if e["statut"] != "fabriquee":
            v["motif"] = f"expérience sautée à la fabrication : {e.get('motif', '')}"
        elif r.get("faite") is not True:
            v["motif"] = "expérience non faite"
        else:
            valeurs = r.get("valeurs") or {}
            manquantes = [m["cle"] for m in e["mesures"] if valeurs.get(m["cle"]) is None]
            if manquantes:
                v["motif"] = "valeurs manquantes : " + ", ".join(manquantes)
            else:
                v["ecarts"] = [f"{m['cle']} : relevé {valeurs[m['cle']]!r}, attendu {m['attendu']!r}"
                               + (f" ± {m['tolerance']}" if "tolerance" in m else "")
                               for m in e["mesures"] if not conforme(m, valeurs[m["cle"]])]
                v["verdict"] = "contredite" if v["ecarts"] else "confirmée"
        verdicts.append(v)
    compte = {k: sum(1 for v in verdicts if v["verdict"] == k) for k in ("confirmée", "contredite", "non faite")}
    for v in verdicts:
        suite = " (avenant au contrat nécessaire)" if v["verdict"] == "contredite" else ""
        print(f"{v['id']} {v['titre']} : {v['verdict']}{suite}" + (f" : {v['motif']}" if v["motif"] else ""))
        for e in v["ecarts"]:
            print(f"  - {e}")
        if v["verdict"] != "non faite":
            for e in v["non_teste"]:
                print(f"  - non testé (partie de la convention ni confirmée ni contredite) : {e}")
    print(f"{compte['confirmée']} confirmées, {compte['contredite']} contredites, {compte['non faite']} non faites")
    if chemin_json:
        ecrire_json(chemin_json, {"format": FORMAT_VERDICTS, "base": att["base"]["nom"], "verdicts": verdicts,
                                  "resume": compte})
    return K.CODE_NEGATIF if compte["contredite"] else K.CODE_OK


# ---------------------------------------------------------------------------
def parseur():
    p = K.Parseur(prog="fabriquer_essais.py",
                  description="Fichiers d'essai de l'annexe E (séance dans Altium) et verdicts par convention.")
    p.add_argument("--base", metavar="CARTE", help="carte .PcbDoc de base (lue, jamais modifiée)")
    p.add_argument("--sortie", metavar="DOSSIER",
                   help="dossier des fichiers d'essai, attendus.json, resultats_a_remplir.json")
    p.add_argument("--verifier", metavar="RESULTATS", help="valeurs relevées (resultats_a_remplir.json rempli)")
    p.add_argument("--attendus", metavar="ATTENDUS", help="attendus.json de la fabrication")
    p.add_argument("--json", metavar="FICHIER", help="verdicts écrits en JSON (avec --verifier)")
    return p


def main(argv=None) -> int:
    p = parseur()

    def executer(a):
        if a.verifier or a.attendus:
            if a.base or a.sortie or not (a.verifier and a.attendus):
                p.error("vérification : --verifier RESULTATS --attendus ATTENDUS, sans --base ni --sortie")
            return verifier(a.verifier, a.attendus, a.json)
        if not (a.base and a.sortie) or a.json:
            p.error("fabrication : --base CARTE --sortie DOSSIER (ou --verifier RESULTATS --attendus ATTENDUS)")
        return fabriquer(a.base, a.sortie)
    return K.executer(executer, p, argv)


if __name__ == "__main__":
    sys.exit(main())
