#!/usr/bin/env python3
"""Cas de validité du corpus (contrat §7.4, lot E) : générateur déterministe.

    python generer_cas.py --sortie DOSSIER

Écrit un dossier par cas (``DOSSIER/<NNN_nom>/``) : ``donnees_placeur.json`` (donnees_placeur/2),
``placements_routage.json`` (placements/2), ``routage_detaille.json`` (routage/2), ``attendu_strict.json`` et
``attendu_altium.json`` (verdict/2 complets), ``attendu_mesures.json`` (mesures/2) et ``cas.json`` (cas_validite/1,
décrit dans ``LISEZMOI.md``). Deux exécutions donnent des fichiers identiques octet pour octet (UTF-8, LF).

Les attendus sont ÉCRITS À LA MAIN ci-dessous, avec le calcul en commentaire, d'après la définition de §4 et les
conventions de ``LISEZMOI.md`` ; ils ne sont jamais produits par ``validite.rs`` ni par ``drc_maison.py`` : ce sont
eux qui départagent les deux implémentations. Le générateur ne calcule que la forme des fichiers (champs par défaut,
somme ``violations`` des ``violations_par_type``, conversion mil → mm des données).

Repère commun de tous les cas : carte rectangulaire de 800 × 400 mil (20,32 × 10,16 mm), origine au coin bas-gauche,
donc mil et mm ne diffèrent que du facteur 0,0254. Coordonnées des cas en mil (entiers) ; pastilles CMS carrées de
40 mil (1,016 mm) sur TopLayer ; pistes de 10 mil (0,254 mm) sauf mention ; vias 0,6 / 0,3 mm (23,622 / 11,811 mil).
Isolement 0,2 mm partout (une classe), donc s = 3 × 0,2 = 0,6 mm et p = 1,0 mm (§4.7) ; largeurs [0,2 ; 0,254 ; 1,0].
"""

import argparse
import json
import math
import os
import sys

MIL = 0.0254
FORMAT_CAS = "cas_validite/1"
OUTIL = "attendu ecrit a la main (corpus du lot E)"

THEMES = (
    "contact:piste-pastille", "contact:piste-piste", "contact:piste-via", "contact:via-pastille", "contact:via-via",
    "contact:zone-via", "zone_plan", "modes_differents", "audit:D3.2", "violation:isolement", "violation:largeur",
    "violation:antenne", "arrondi:largeur", "mesure:coudes", "mesure:ecart",
    "contact:piste-region", "contact:piste-via-empreinte", "contact:piste-cuivre-fixe", "contact:via-region",
    "contact:surface", "contact:preexistants", "contact:zone-cms", "objet:region", "objet:via_empreinte",
    "objet:cuivre_fixe", "objet:surface", "objet:piece_virtuelle", "objet:region_liaison",
    "objet:pastille_traversante", "zone:ilot_autre_reseau", "zone:plan_partiel", "zone:bande_retrait", "zone:bord",
    "zone:plan_externe", "geometrie:pastille_tournee", "geometrie:trou_decale",
    "violation:garde_bord", "violation:garde_decoupe", "violation:garde_trou", "violation:zone_interdite",
    "violation:dimension_via", "violation:via_sous_cms", "violation:trou_trou", "violation:couche_non_permise",
    "violation:reseau_inconnu", "derogation:via_dans_pastille", "geometrie:contour_polygonal", "objet:trou",
    "regles:matrice", "arrondi:pastille", "audit:amorce_coupee", "avertissement:sous_piece",
    "zone_interdite:empreinte", "zone_interdite:elargie",
    "placement:valide", "placement:absente", "placement:inconnue", "placement:fixe_deplacee", "placement:orientation",
    "placement:grille", "placement:chevauchement", "placement:obstacle", "placement:marge_bord",
    "placement:marge_decoupe", "placement:eloignement", "placement:pastille_pastille", "placement:zone_imposee",
    "placement:au_bord", "placement:groupe_rigide", "placement:face_opposee", "placement:pose_entree",
    "placement:v1", "placement:sans_grille",
)
"""Vocabulaire des thèmes de ``cas.json`` (couverture de §7.4 contrôlée par ``test_corpus.py``)."""

GENRES_ROUTAGE = ("isolement", "garde_bord", "garde_decoupe", "garde_trou", "zone_interdite", "largeur",
                  "dimension_via", "via_sous_cms", "trou_trou", "antenne", "couche_non_permise", "reseau_inconnu")

COUCHES_2 = ("TopLayer", "BottomLayer")
COUCHES_4 = ("TopLayer", "MidLayer1", "MidLayer2", "BottomLayer")
ROUTAGE_4 = ("TopLayer", "MidLayer2", "BottomLayer")
VIA_REGLE = {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.5, "diametre_max": 0.8, "trou_min": 0.25,
             "trou_max": 0.4}
VIA_06 = (0.6, 0.3)
VIA_08 = (0.8, 0.25)
ZONE_GND = [[20, 20], [780, 20], [780, 380], [20, 380]]     # mil : contour de la carte moins 20 mil


def mm(v):
    """mil → mm, arrondi au nanomètre (évite 2.5399999999999996)."""
    return round(v * MIL, 9)


def pts_mm(points):
    return [[mm(x), mm(y)] for x, y in points]


def mil_ecrit(v_mm):
    """Dimension écrite en mil (3 décimales, au plus proche), comme une sortie du moteur (§0.4)."""
    return round(v_mm / MIL, 3)


# --- Données (donnees_placeur/2) ---------------------------------------------------------------------------------------

def pastille(des, k, nom, reseau, lx_mil=0, ly_mil=0, cote_mil=40, forme="rect", via_permis=False):
    c = mm(cote_mil)
    return {"id": f"{des}#{k}", "nom": nom, "reseau": reseau, "lx": mm(lx_mil), "ly": mm(ly_mil), "forme": forme,
            "largeur": c, "hauteur": c, "rotation": 0.0, "rayon_coin": 0.0, "points": [], "couches": ["TopLayer"],
            "face": "composant", "trou": None, "traversante": False, "expansion_masque": 0.0, "pile": None,
            "via_dans_pastille_permis": via_permis, "classe_isolement": 0, "hw": round(c / 2, 9),
            "hh": round(c / 2, 9), "th": False}


def piece(nom, x_mil, y_mil, pastilles, nature="connecteur"):
    xs = [p["lx"] - p["hw"] for p in pastilles] + [p["lx"] + p["hw"] for p in pastilles]
    ys = [p["ly"] - p["hh"] for p in pastilles] + [p["ly"] + p["hh"] for p in pastilles]
    pose = [mm(x_mil), mm(y_mil), 0.0, False]
    return {"nom": nom, "empreinte": "CORPUS_PASTILLES", "nature": nature, "genre": "standard", "valeur": "",
            "hauteur": 0.0, "statut": "fixe", "raison_statut": "contrainte", "virtuel": False,
            "lbb": [round(min(xs), 9), round(min(ys), 9), round(max(xs), 9), round(max(ys), 9)],
            "pastilles": pastilles, "regions": [], "vias": [], "zones_interdites": [], "decoupes": [],
            "pose_actuelle": list(pose), "pose_cible": list(pose), "orientations": [], "regle": "contrainte",
            "pas_fin": False, "matrice": False, "pas_min_mm": None}


def j(nom, x_mil, y_mil, reseau, **options):
    """Pièce à une pastille (connecteur de test) posée en (x, y) mil."""
    return piece(nom, x_mil, y_mil, [pastille(nom, 0, "1", reseau, **options)])


def reseau(nom, routage, role="signal", plans=()):
    return {"nom": nom, "classe": "", "role": role, "masse": role == "plan", "sensibilite": "normale",
            "largeurs_mm": [0.2, 0.254, 1.0], "descente_large": False, "isolement_mm": 0.2,
            "classes_isolement": {"piste": 0, "via": 0, "surface": 0}, "via": dict(VIA_REGLE),
            "poids": 0.0 if role == "plan" else 1.0, "priorite": None, "priorite_routage": 0, "couche_preferee": None,
            "couches_permises": list(routage), "plan_couches": list(plans), "remplissages": [],
            "alimentation_schema": False, "paire": None}


def donnees(nom, pieces, reseaux, quatre_couches=False, zone_trous=(), regles=None):
    noms = COUCHES_4 if quatre_couches else COUCHES_2
    couches = []
    for rang, n in enumerate(noms):
        plan = quatre_couches and n == "MidLayer1"
        zones = [{"reseau": "GND", "contour": pts_mm(ZONE_GND), "trous": [pts_mm(t) for t in zone_trous]}] if plan else []
        couches.append({"nom": n, "rang": rang, "role": "plan" if plan else "routage", "zones_plan": zones,
                        "epaisseur_cuivre_mm": 0.035, "direction_preferee": None})
    r = {"isolement": {"defaut": 0.2, "classes": ["defaut"], "matrice": [[0.2]], "classe_reference": 0},
         "via": dict(VIA_REGLE), "trou_trou_mm": 0.254, "garde_trou_mm": 0.6,
         "placement": {"grille_mm": 0.127, "marge_piece_mm": 0.25, "marge_traversante_mm": 0.6},
         "via_dans_pastille_meme_reseau": False, "connexion_plans": {"isolement": 0.5, "connexion": "relief"}}
    r.update(regles or {})
    contour = pts_mm([[0, 0], [800, 0], [800, 400], [0, 400]])
    return {
        "format": "donnees_placeur/2",
        "source": {"entree": "entree_carte.json", "sha256_entree": "cas " + nom, "contraintes": None,
                   "config": "config_carte.json", "controle_gabarit": None,
                   "construit_par": "construire_donnees corpus-e1 (cas ecrit a la main)"},
        "unite": "mm",
        "carte": {"contour": contour, "boite": [0.0, 0.0, mm(800), mm(400)], "decoupes": [], "remplissages": [],
                  "marge_bord_mm": 1.0, "marge_decoupe_mm": 1.0, "garde_bord_mm": 0.6, "garde_decoupe_mm": 0.6},
        "couches": couches, "trous": [], "obstacles_placement": [], "zones_imposees": [],
        "zones_routage_interdites": [], "cuivre_fixe": [], "composants": pieces, "reseaux": reseaux, "regles": r,
        "convertisseurs": [], "sans_piste_dessous": [], "decouplages": [], "eloignements": [], "charges": [],
        "groupes": [], "paires": [], "au_bord": [], "masses": ["GND"] if quatre_couches else [],
        "parametres_deduction": {"pas_fin_mm": 0.65, "regles_non_interpretees": 0}}


def placements(pieces):
    return {"format": "placements/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour",
            "pieces": [{"designator": p["nom"], "side": "top", "x": round(p["pose_cible"][0] / MIL, 3),
                        "y": round(p["pose_cible"][1] / MIL, 3), "rotation": 0.0, "statut": "fixe"}
                       for p in pieces]}


def routage(pistes, vias, quatre_couches=False):
    """pistes : (x1, y1, x2, y2, largeur_mil, couche, réseau) ; vias : (x, y, (diamètre, trou) mm, réseau)."""
    couches = ROUTAGE_4 if quatre_couches else COUCHES_2
    return {"format": "routage/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour",
            "couches_routage": list(couches),
            "pistes": [{"x1": float(a), "y1": float(b), "x2": float(c), "y2": float(d), "width": float(w),
                        "layer": l, "net_name": n} for a, b, c, d, w, l, n in pistes],
            "vias": [{"x": float(x), "y": float(y), "size": mil_ecrit(t[0]), "hole_size": mil_ecrit(t[1]), "net": n,
                      "de": couches[0], "a": couches[-1]} for x, y, t, n in vias],
            "surfaces": [], "amorces_pas_fin": [], "derogations": [], "reseaux": [], "echecs": []}


# --- Attendus (verdict/2, mesures/2) : valeurs écrites à la main ----------------------------------------------------

def ouverte(reseau_, pastilles, noms, position, nature="pastilles"):
    return {"reseau": reseau_, "nature": nature, "pastilles": pastilles, "noms": noms, "position_mm": position}


def violation(genre, reseaux, objets, position, ecart=None, requis=None):
    return {"genre": genre, "reseaux": reseaux, "objets": objets, "position_mm": position, "ecart_mm": ecart,
            "requis_mm": requis}


def verdict(mode, valide, a_router, complets, longueur, ouvertes=(), violations=(), exemples=None, vias=0,
            coudes=0, coudes_90=0, ecart_moyen=0.0, ecart_min=None, marge=None, paire="", isolees=0):
    par_type = {g: 0 for g in GENRES_ROUTAGE}
    for v in violations:
        par_type[v["genre"]] += 1
    return {
        "format": "verdict/2", "outil": OUTIL, "mode": mode, "valide": valide,
        "liaisons_ouvertes": len(ouvertes), "detail_liaisons_ouvertes": list(ouvertes),
        "violations": sum(par_type.values()), "violations_par_type": par_type,
        "detail_violations": list(violations), "detail_violations_tronque": False, "exemples": exemples or {},
        "derogations": {"via_dans_pastille_meme_reseau": 0},
        "avertissements": {"pistes_sous_pieces_sans_piste_dessous": 0, "angles_aigus": 0,
                           "paires_desequilibrees": 0, "regles_non_couvertes": 0, "zones_plan_isolees": isolees},
        "reseaux_a_router": a_router, "reseaux_complets": complets,
        "marge_isolement_min_mm": marge, "marge_isolement_min_paire": paire,
        "mesures": {"ecart_moyen_mm": ecart_moyen, "ecart_min_mm": ecart_min, "coudes": coudes,
                    "coudes_90": coudes_90, "angles_aigus": 0, "longueur_pistes_mm": longueur,
                    "resistance_puissance_mohm": 0.0, "vias": vias}}


def mesures(cout, longueur, vias=0, retreci=0.0, coudes=0, ecart_moyen=0.0):
    return {"format": "mesures/2", "formule": "cout_commun/2", "cout_commun": cout,
            "termes": {"longueur_mm": longueur, "vias": vias, "retreci_mm": retreci, "resistance_puissance_mohm": 0.0,
                       "coudes": coudes, "ecart_moyen_mm": ecart_moyen},
            "parametres": {"seuil_ecart_mm": 0.6, "plafond_ecart_mm": 1.0}}


def _compare(v):
    return v["valide"], v["liaisons_ouvertes"], v["violations_par_type"], v["derogations"]


GENRES_PLACEMENT = ("absente", "inconnue", "fixe_deplacee", "orientation", "grille", "chevauchement", "obstacle",
                    "marge_bord", "marge_decoupe", "eloignement", "pastille_pastille", "zone_imposee", "au_bord",
                    "groupe_rigide")


def controle_placement(violations=()):
    """Attendu de placement (controle_placement/1, §2.8) : ``violations`` = [(genre, [pièces], texte)]."""
    par_type = {g: 0 for g in GENRES_PLACEMENT}
    for genre, _, _ in violations:
        par_type[genre] += 1
    return {"format": "controle_placement/1", "valide": not violations,
            "violations": [{"genre": g, "pieces": list(pc), "texte": t} for g, pc, t in violations],
            "violations_par_type": par_type, "infos": {"eloignements": []}}


def cas(nom, description, themes, d, p, r, strict, altium, m, placement=None):
    # L'attendu de placement (indépendant du mode) est le champ facultatif « placement » de verdict/2, porté par
    # attendu_strict.json seul (LISEZMOI, format cas_validite/1) ; défaut : pièces à leur pose, placement valide.
    strict = dict(strict, placement=placement if placement is not None else controle_placement())
    return nom, {"format": FORMAT_CAS, "nom": nom, "description": description, "themes": list(themes),
                 "grille": True, "couture": False, "modes_differents": _compare(strict) != _compare(altium),
                 "fichiers": {"donnees": "donnees_placeur.json", "placement": "placements_routage.json",
                              "routage": "routage_detaille.json", "attendu_strict": "attendu_strict.json",
                              "attendu_altium": "attendu_altium.json", "attendu_mesures": "attendu_mesures.json",
                              "couture": None}}, d, p, r, strict, altium, m


T = "TopLayer"
B = "BottomLayer"
N1 = ["N1"]


def deux(nom, *pieces_, nets=("N1",)):
    pieces = list(pieces_)
    return pieces, donnees(nom, pieces, [reseau(n, COUCHES_2) for n in nets])


def liste_cas():
    L = []

    # 001 — contacts stricts piste–pastille et piste–piste (T). J1 (400,300), J2 (100,100), J3 (700,100), tous N1.
    # P0 (400,300)→(400,100) : départ au centre de J1 ; arrivée sur l'axe de P1 (distance 0 ≤ 0,127 + ε) : contact
    # strict piste–piste. P1 (100,100)→(700,100) : extrémités aux centres de J2 et J3. Aucune jonction de deux
    # extrémités (l'arrivée de P0 est au milieu de P1) : 0 coude. Longueur 200 + 600 = 800 mil = 20,32 mm.
    # Aucun objet d'un autre réseau : écart moyen 0 (aucune piste n'a de voisin), marge null.
    # Coût = 20,32 + 1000 × (0,6 − 0) = 620,32.
    nom = "001_contacts_stricts_pistes"
    pieces, d = deux(nom, j("J1", 400, 300, "N1"), j("J2", 100, 100, "N1"), j("J3", 700, 100, "N1"))
    r = routage([(400, 300, 400, 100, 10, T, "N1"), (100, 100, 700, 100, 10, T, "N1")], [])
    ok = dict(valide=True, a_router=1, complets=1, longueur=20.32)
    L.append(cas(nom, "Contacts stricts piste–pastille et piste–piste (jonction en T) : valide dans les deux modes.",
                 ["contact:piste-pastille", "contact:piste-piste"], d, placements(pieces), r,
                 verdict("strict", **ok), verdict("altium", **ok), mesures(620.32, 20.32)))

    # 002 — contacts stricts piste–via, via–via et via–pastille. J1 (100,200), J2 (700,200), N1 ; trou_trou 0,1 mm.
    # P0 (100,200)→(300,200) Top, arrivée au centre de V0 (300,200), via 0,8 / 0,25 (31,496 / 9,843 mil).
    # V1 (315,200), via 0,6 / 0,25 : entraxe 15 mil = 0,381 mm ≤ max(rayons) = 0,4 : contact strict via–via ;
    # parois : 0,381 − 0,25 = 0,131 ≥ 0,1 (pas de trou_trou). P1 (315,200)→(700,200) Bottom, de V1 à V2 (700,200),
    # via 0,6 / 0,3 dont le centre est dans J2 (via_dans_pastille_permis vrai : ni via_sous_cms ni dérogation).
    # Longueur 200 + 385 = 585 mil = 14,859 mm ; 3 vias. Coût = 14,859 + 1,5 × 3 + 600 = 619,359.
    nom = "002_contacts_stricts_vias"
    pieces = [j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1", via_permis=True)]
    d = donnees(nom, pieces, [reseau("N1", COUCHES_2)], regles={"trou_trou_mm": 0.1})
    r = routage([(100, 200, 300, 200, 10, T, "N1"), (315, 200, 700, 200, 10, B, "N1")],
                [(300, 200, VIA_08, "N1"), (315, 200, (0.6, 0.25), "N1"), (700, 200, VIA_06, "N1")])
    ok = dict(valide=True, a_router=1, complets=1, longueur=14.859, vias=3)
    L.append(cas(nom, "Contacts stricts piste–via, via–via (entraxe ≤ plus grand rayon) et via–pastille (centre "
                      "dans le cuivre) : valide dans les deux modes.",
                 ["contact:piste-via", "contact:via-via", "contact:via-pastille"], d, placements(pieces), r,
                 verdict("strict", **ok), verdict("altium", **ok), mesures(619.359, 14.859, vias=3)))

    # 003 — piste–pastille par recouvrement seulement. J1 (100,200), J2 (700,200), N1.
    # P0 (124,200)→(700,200) : départ à 4 mil = 0,1016 mm du bord de J1 (x = 120 mil) ; demi-largeur 0,127 :
    # recouvrement de 0,0254 mm (contact altium), distance signée 0,1016 > ε (pas de contact strict).
    # Strict : composantes {J1} et {P0, J2} ; référence = la plus grande ⇒ liaison ouverte J1 (centre (2,54 ; 5,08)).
    # Antenne (au sens strict, dans les deux modes) : départ de P0 (124 mil = 3,1496 mm ; 5,08) ne touche rien.
    # Longueur 576 mil = 14,6304 mm. Coût = 14,6304 + 600 = 614,6304.
    nom = "003_piste_pastille_recouvrement"
    pieces, d = deux(nom, j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1"))
    r = routage([(124, 200, 700, 200, 10, T, "N1")], [])
    ant = violation("antenne", N1, ["P0"], [3.1496, 5.08])
    ex = {"antenne": ["P0 (N1) : extrémité (3.150 ; 5.080) mm sans contact strict"]}
    L.append(cas(nom, "Piste dont l'extrémité est hors du cuivre de la pastille mais dont le cuivre la recouvre : "
                      "liaison ouverte en strict, reliée en altium ; antenne dans les deux modes.",
                 ["contact:piste-pastille", "modes_differents", "violation:antenne"], d, placements(pieces), r,
                 verdict("strict", False, 1, 0, 14.6304, [ouverte("N1", ["J1#0"], ["J1.1"], [2.54, 5.08])], [ant], ex),
                 verdict("altium", False, 1, 1, 14.6304, [], [ant], ex), mesures(614.6304, 14.6304)))

    # 004 — piste–piste par recouvrement seulement. J1 (100,200), J2 (700,200), N1.
    # P0 (100,200)→(400,200) ; P1 (380,208)→(700,208) (arrivée dans J2 : |208 − 200| = 8 ≤ 20 mil).
    # Cuivres : P0 y ∈ [195 ; 205], P1 y ∈ [203 ; 213] sur x ∈ [380 ; 400] : recouvrement (contact altium).
    # Strict : arrivée de P0 (400,200) à 8 mil = 0,2032 mm de l'axe de P1 > 0,127 ; départ de P1 (380,208) à
    # 0,2032 mm de l'axe de P0 > 0,127 : aucun contact. Composantes {J1, P0} et {P1, J2} (2 objets chacune) ;
    # égalité ⇒ référence = celle du plus petit identifiant (J1#0) ⇒ liaison ouverte J2 (17,78 ; 5,08).
    # Antennes (une par piste) : P0 en (10,16 ; 5,08), P1 en (9,652 ; 5,2832).
    # Longueur 300 + 320 = 620 mil = 15,748 mm. Coût = 15,748 + 600 = 615,748.
    nom = "004_piste_piste_recouvrement"
    pieces, d = deux(nom, j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1"))
    r = routage([(100, 200, 400, 200, 10, T, "N1"), (380, 208, 700, 208, 10, T, "N1")], [])
    ants = [violation("antenne", N1, ["P0"], [10.16, 5.08]), violation("antenne", N1, ["P1"], [9.652, 5.2832])]
    ex = {"antenne": ["P0 (N1) : extrémité (10.160 ; 5.080) mm sans contact strict",
                      "P1 (N1) : extrémité (9.652 ; 5.283) mm sans contact strict"]}
    L.append(cas(nom, "Deux pistes qui se recouvrent sans qu'une extrémité d'axe soit dans la demi-largeur de "
                      "l'autre : liaison ouverte en strict, reliées en altium ; deux antennes.",
                 ["contact:piste-piste", "modes_differents", "violation:antenne"], d, placements(pieces), r,
                 verdict("strict", False, 1, 0, 15.748, [ouverte("N1", ["J2#0"], ["J2.1"], [17.78, 5.08])], ants, ex),
                 verdict("altium", False, 1, 1, 15.748, [], ants, ex), mesures(615.748, 15.748)))

    # 005 — piste–via par recouvrement seulement. J1 (100,200), J2 (700,200), N1.
    # P0 (100,200)→(384,200) ; V0 (400,200) 0,6 / 0,3 ; P1 (400,200)→(700,200) part du centre de V0.
    # Arrivée de P0 à 16 mil = 0,4064 mm du centre de V0 > rayon 0,3 + ε (pas de contact strict) ;
    # 0,4064 < 0,3 + 0,127 : recouvrement (contact altium). P0 et P1 : 16 − 2 × 5 = 6 mil d'écart, aucun contact.
    # Strict : {J1, P0} (2 objets) et {V0, P1, J2} (3) ⇒ liaison ouverte J1 (2,54 ; 5,08).
    # Antenne : arrivée de P0 (384 mil = 9,7536 mm ; 5,08). Longueur 284 + 300 = 584 mil = 14,8336 mm ; 1 via.
    # Coût = 14,8336 + 1,5 + 600 = 616,3336.
    nom = "005_piste_via_recouvrement"
    pieces, d = deux(nom, j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1"))
    r = routage([(100, 200, 384, 200, 10, T, "N1"), (400, 200, 700, 200, 10, T, "N1")], [(400, 200, VIA_06, "N1")])
    ant = violation("antenne", N1, ["P0"], [9.7536, 5.08])
    ex = {"antenne": ["P0 (N1) : extrémité (9.754 ; 5.080) mm sans contact strict"]}
    L.append(cas(nom, "Piste dont l'extrémité est hors du rayon du via mais dont le cuivre le recouvre : liaison "
                      "ouverte en strict, reliée en altium ; antenne dans les deux modes.",
                 ["contact:piste-via", "modes_differents", "violation:antenne"], d, placements(pieces), r,
                 verdict("strict", False, 1, 0, 14.8336, [ouverte("N1", ["J1#0"], ["J1.1"], [2.54, 5.08])], [ant], ex,
                         vias=1),
                 verdict("altium", False, 1, 1, 14.8336, [], [ant], ex, vias=1), mesures(616.3336, 14.8336, vias=1)))

    # 006 — via–pastille par recouvrement seulement. J1 (100,200), J2 (700,200), N1, via permis dans les deux.
    # V0 (125,200) : centre à 5 mil = 0,127 mm du bord de J1 (pas de contact strict), rayon 0,3 : recouvrement
    # (contact altium). P0 (125,200)→(700,200) sur BottomLayer (aucun contact avec J1, CMS de TopLayer), de V0 à
    # V1 (700,200) dont le centre est dans J2 (contact strict). Strict : {J1} et {V0, P0, V1, J2} ⇒ liaison
    # ouverte J1 (2,54 ; 5,08). Longueur 575 mil = 14,605 mm ; 2 vias. Coût = 14,605 + 3 + 600 = 617,605.
    nom = "006_via_pastille_recouvrement"
    pieces = [j("J1", 100, 200, "N1", via_permis=True), j("J2", 700, 200, "N1", via_permis=True)]
    d = donnees(nom, pieces, [reseau("N1", COUCHES_2)])
    r = routage([(125, 200, 700, 200, 10, B, "N1")], [(125, 200, VIA_06, "N1"), (700, 200, VIA_06, "N1")])
    L.append(cas(nom, "Via dont le centre est hors de la pastille mais dont le cuivre la recouvre : liaison ouverte "
                      "en strict, valide en altium.",
                 ["contact:via-pastille", "modes_differents"], d, placements(pieces), r,
                 verdict("strict", False, 1, 0, 14.605, [ouverte("N1", ["J1#0"], ["J1.1"], [2.54, 5.08])], vias=2),
                 verdict("altium", True, 1, 1, 14.605, vias=2), mesures(617.605, 14.605, vias=2)))

    # 007 — via–via par recouvrement seulement. J1 (100,200), J2 (700,200), N1 ; trou_trou 0,1 mm.
    # P0 (100,200)→(300,200) jusqu'au centre de V0 (300,200) ; V1 (320,200) ; P1 (320,200)→(700,200).
    # Entraxe 20 mil = 0,508 mm > max(rayons) 0,3 (pas de contact strict) et < 0,6 (recouvrement, contact
    # altium) ; parois 0,508 − 0,3 = 0,208 ≥ 0,1. Les pistes ne touchent pas l'autre via (écart ≈ 0,081 mm).
    # Strict : {J1, P0, V0} et {V1, P1, J2} (3 objets chacune) ; égalité ⇒ référence = celle de J1#0 ⇒ liaison
    # ouverte J2 (17,78 ; 5,08). Longueur 200 + 380 = 580 mil = 14,732 mm ; 2 vias. Coût = 617,732.
    nom = "007_via_via_recouvrement"
    pieces = [j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1")]
    d = donnees(nom, pieces, [reseau("N1", COUCHES_2)], regles={"trou_trou_mm": 0.1})
    r = routage([(100, 200, 300, 200, 10, T, "N1"), (320, 200, 700, 200, 10, T, "N1")],
                [(300, 200, VIA_06, "N1"), (320, 200, VIA_06, "N1")])
    L.append(cas(nom, "Deux vias qui se recouvrent, entraxe supérieur au plus grand rayon : liaison ouverte en "
                      "strict, valide en altium.",
                 ["contact:via-via", "modes_differents"], d, placements(pieces), r,
                 verdict("strict", False, 1, 0, 14.732, [ouverte("N1", ["J2#0"], ["J2.1"], [17.78, 5.08])], vias=2),
                 verdict("altium", True, 1, 1, 14.732, vias=2), mesures(617.732, 14.732, vias=2)))

    # Cas à quatre couches : MidLayer1 = plan GND, zone = contour moins 20 mil ([20 ; 780] × [20 ; 380] mil).
    # GND : rôle plan, masse ; membres = pastilles GND et zone touchée. Contact strict zone–via : centre dans la
    # zone à au moins rayon + connexion_plans.isolement = 0,3 + 0,5 = 0,8 mm de son bord (contour et trous).
    gnd = reseau("GND", ROUTAGE_4, role="plan", plans=["MidLayer1"])
    base = [(100, 200, 200, 200, 10, T, "GND"), (700, 200, 600, 200, 10, T, "GND")]
    vias_gnd = [(200, 200, VIA_06, "GND"), (600, 200, VIA_06, "GND")]

    # 008 — descentes par vias dans le plan. J1 (100,200), J2 (700,200) GND ; P0 J1→V0 (200,200), P1 J2→V1
    # (600,200) ; V0 et V1 à 4,572 mm du bord de la zone (≥ 0,8) : contact strict. Une composante (J1, J2, zone).
    # Longueur 100 + 100 = 200 mil = 5,08 mm ; 2 vias ; GND n'est ni puissance ni alimentation (résistance 0) ;
    # préférée 0,254 : rien de rétréci. Coût = 5,08 + 3 + 600 = 608,08.
    nom = "008_plan_vias_relies"
    pieces = [j("J1", 100, 200, "GND"), j("J2", 700, 200, "GND")]
    d = donnees(nom, pieces, [gnd], quatre_couches=True)
    r = routage(base, vias_gnd, quatre_couches=True)
    ok = dict(valide=True, a_router=1, complets=1, longueur=5.08, vias=2)
    L.append(cas(nom, "Deux pastilles GND reliées chacune au plan interne par une piste et un via : valide.",
                 ["contact:zone-via", "zone_plan"], d, placements(pieces), r,
                 verdict("strict", **ok), verdict("altium", **ok), mesures(608.08, 5.08, vias=2)))

    # 009 — descente GND manquante (audit D3.2, en petit). Cas 008 plus D3 (400,300) : D3#0 « 1 » (LED_A) à
    # (370,300), D3#1 « 2 » (GND) à (430,300) mil, sans via ni piste. D3#1 (CMS de TopLayer) ne touche pas la zone
    # (MidLayer1) : composante isolée ⇒ 1 liaison ouverte dans les deux modes, D3.2 au centre de D3#1
    # (430 mil = 10,922 mm ; 300 mil = 7,62 mm). LED_A n'a qu'un membre : non compté. D3 est à plus de 1,6 mm de
    # toute piste : aucune case de 1 mm commune (aucun voisin, écart 0), aucune paire contrôlée (marge null).
    nom = "009_plan_descente_gnd_manquante"
    d3 = piece("D3", 400, 300, [pastille("D3", 0, "1", "LED_A", -30, 0), pastille("D3", 1, "2", "GND", 30, 0)],
               nature="diode")
    pieces = [j("J1", 100, 200, "GND"), j("J2", 700, 200, "GND"), d3]
    d = donnees(nom, pieces, [gnd, reseau("LED_A", ROUTAGE_4)], quatre_couches=True)
    r = routage(base, vias_gnd, quatre_couches=True)
    lo = [ouverte("GND", ["D3#1"], ["D3.2"], [10.922, 7.62])]
    L.append(cas(nom, "Pastille GND d'une pièce CMS sans descente vers le plan (cas D3.2 de l'audit) : une liaison "
                      "ouverte dans les deux modes.",
                 ["zone_plan", "audit:D3.2", "contact:zone-via"], d, placements(pieces), r,
                 verdict("strict", False, 1, 0, 5.08, lo, vias=2), verdict("altium", False, 1, 0, 5.08, lo, vias=2),
                 mesures(608.08, 5.08, vias=2)))

    # 010 — via trop près d'un trou de la zone. Cas 008 dont la zone a un trou [616 ; 700] × [150 ; 250] mil.
    # V1 (600,200) : centre dans la zone, à 16 mil = 0,4064 mm du trou < 0,8 ⇒ pas de contact strict ; contact
    # altium (centre dans la zone). Strict : {J1, P0, V0, zone} (la seule avec une zone, référence) et
    # {V1, P1, J2} ⇒ liaison ouverte J2 (17,78 ; 5,08). Mesures du cas 008.
    nom = "010_plan_via_pres_du_trou"
    pieces = [j("J1", 100, 200, "GND"), j("J2", 700, 200, "GND")]
    d = donnees(nom, pieces, [gnd], quatre_couches=True, zone_trous=[[[616, 150], [700, 150], [700, 250], [616, 250]]])
    r = routage(base, vias_gnd, quatre_couches=True)
    L.append(cas(nom, "Via dans la zone de plan mais à moins de rayon + isolement de connexion d'un trou de la "
                      "zone : liaison ouverte en strict, valide en altium.",
                 ["contact:zone-via", "zone_plan", "modes_differents"], d, placements(pieces), r,
                 verdict("strict", False, 1, 0, 5.08, [ouverte("GND", ["J2#0"], ["J2.1"], [17.78, 5.08])], vias=2),
                 verdict("altium", True, 1, 1, 5.08, vias=2), mesures(608.08, 5.08, vias=2)))

    # 011 — isolement. N1 : J1 (100,200), J2 (700,200), P0 (100,200)→(700,200) ; N2 : J3, pastille ronde de
    # 40 mil en (400,230). Bord haut de P0 : 205 mil ; bas de J3 : 210 mil ⇒ écart 5 mil = 0,127 mm < 0,2 :
    # violation entre J3#0 et P0, points les plus proches (400,205) et (400,210) ⇒ milieu (10,16 ; 5,2705).
    # Écart de P0 (seul voisin J3) = 0,127 ⇒ écart moyen et minimal 0,127 ; seule paire contrôlée (J3#0, P0) :
    # marge 0,127 − 0,2 = −0,073. N2 n'a qu'un membre : non compté. Longueur 600 mil = 15,24 mm.
    # Coût = 15,24 + 1000 × (0,6 − 0,127) = 488,24.
    nom = "011_isolement_piste_pastille"
    pieces = [j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1"), j("J3", 400, 230, "N2", forme="rond")]
    d = donnees(nom, pieces, [reseau("N1", COUCHES_2), reseau("N2", COUCHES_2)])
    r = routage([(100, 200, 700, 200, 10, T, "N1")], [])
    iso = violation("isolement", ["N2", "N1"], ["J3#0", "P0"], [10.16, 5.2705], 0.127, 0.2)
    ex = {"isolement": ["J3#0 (N2) / P0 (N1) : 0.127 < 0.200 mm"]}
    m = dict(ecart_moyen=0.127, ecart_min=0.127, marge=-0.073, paire="J3#0(N2) / P0(N1)")
    L.append(cas(nom, "Piste qui passe à 0,127 mm d'une pastille d'un autre réseau (isolement 0,2) : violation "
                      "dans les deux modes ; écart moyen et marge d'isolement.",
                 ["violation:isolement", "mesure:ecart"], d, placements(pieces), r,
                 verdict("strict", False, 1, 1, 15.24, [], [iso], ex, **m),
                 verdict("altium", False, 1, 1, 15.24, [], [iso], ex, **m),
                 mesures(488.24, 15.24, ecart_moyen=0.127)))

    # 012 — largeur à l'arrondi du millième de mil. Minimum 0,2 mm = 7,874015… mil, borne arrondie vers le haut :
    # 7,875 mil. J1 (100,200), J2 (400,200), J3 (700,200) ; P0 J1→J2 à 7,875 mil (conforme), P1 J2→J3 à
    # 7,874 mil < 7,875 − 1e-6 : violation (mesurée 7,874 × 0,0254 = 0,1999996 mm, borne 0,2 mm), au milieu de P1
    # (550 mil = 13,97 mm ; 5,08). Jonction en J2 : directions sortantes opposées (cos = −1) : pas un coude.
    # Rétréci : les deux pistes < 0,254 − 0,001 ⇒ 600 mil = 15,24 mm. Coût = 15,24 + 2 × 15,24 + 600 = 645,72.
    nom = "012_largeur_arrondi_millieme"
    pieces, d = deux(nom, j("J1", 100, 200, "N1"), j("J2", 400, 200, "N1"), j("J3", 700, 200, "N1"))
    r = routage([(100, 200, 400, 200, 7.875, T, "N1"), (400, 200, 700, 200, 7.874, T, "N1")], [])
    lar = violation("largeur", N1, ["P1"], [13.97, 5.08], 0.1999996, 0.2)
    ex = {"largeur": ["P1 (N1) : 7.874 mil < 7.875 mil"]}
    L.append(cas(nom, "Largeur minimale 0,2 mm arrondie vers le haut au millième de mil (7,875) : une piste à "
                      "7,875 mil conforme, une à 7,874 mil en violation.",
                 ["violation:largeur", "arrondi:largeur"], d, placements(pieces), r,
                 verdict("strict", False, 1, 1, 15.24, [], [lar], ex),
                 verdict("altium", False, 1, 1, 15.24, [], [lar], ex), mesures(645.72, 15.24, retreci=15.24)))

    # 013 — antenne. J1 (100,200), J2 (700,200), N1 ; P0 J1→J2 ; P1 (400,200)→(400,300) part de l'axe de P0
    # (contact strict) et finit dans le vide : une antenne en (10,16 ; 7,62), aucune liaison ouverte.
    # Longueur 600 + 100 = 700 mil = 17,78 mm. Coût = 17,78 + 600 = 617,78.
    nom = "013_antenne"
    pieces, d = deux(nom, j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1"))
    r = routage([(100, 200, 700, 200, 10, T, "N1"), (400, 200, 400, 300, 10, T, "N1")], [])
    ant = violation("antenne", N1, ["P1"], [10.16, 7.62])
    ex = {"antenne": ["P1 (N1) : extrémité (10.160 ; 7.620) mm sans contact strict"]}
    L.append(cas(nom, "Tronçon de piste dont une extrémité ne touche aucun cuivre de son réseau : antenne dans les "
                      "deux modes, sans liaison ouverte.",
                 ["violation:antenne", "contact:piste-piste"], d, placements(pieces), r,
                 verdict("strict", False, 1, 1, 17.78, [], [ant], ex),
                 verdict("altium", False, 1, 1, 17.78, [], [ant], ex), mesures(617.78, 17.78)))

    # 014 — coudes à 90°. J1 (100,100), J2 (700,300), N1 ; P0 (100,100)→(400,100), P1 (400,100)→(400,300),
    # P2 (400,300)→(700,300). Jonctions de deux extrémités en (400,100) : directions sortantes (−1, 0) et (0, 1),
    # cos 0 ; en (400,300) : (0, −1) et (1, 0), cos 0 ⇒ 2 coudes, 2 coudes à 90°, 0 angle aigu.
    # Longueur 300 + 200 + 300 = 800 mil = 20,32 mm. Coût = 20,32 + 0,1 × 2 + 600 = 620,52.
    nom = "014_coudes_90"
    pieces, d = deux(nom, j("J1", 100, 100, "N1"), j("J2", 700, 300, "N1"))
    r = routage([(100, 100, 400, 100, 10, T, "N1"), (400, 100, 400, 300, 10, T, "N1"),
                 (400, 300, 700, 300, 10, T, "N1")], [])
    ok = dict(valide=True, a_router=1, complets=1, longueur=20.32, coudes=2, coudes_90=2)
    L.append(cas(nom, "Deux coudes à angle droit : valide ; compte des coudes de §4.7.",
                 ["mesure:coudes", "contact:piste-piste"], d, placements(pieces), r,
                 verdict("strict", **ok), verdict("altium", **ok), mesures(620.52, 20.32, coudes=2)))
    return L + cas_etape5() + cas_etape6() + cas_etape7()


# --- Étape 5 : objets de §4.1 (formes, trous, régions, vias d'empreinte, cuivre fixe, zones, surfaces) -------------


def pastille_g(des, k, nom, reseau, lx_mil=0, ly_mil=0, l_mil=40, h_mil=40, forme="rect", rotation=0.0,
               couches=("TopLayer",), trou=None, via_permis=False):
    """Pastille générale : ``hw``/``hh`` = demi-côtés de la boîte du cuivre tourné (repère de la pièce)."""
    l, h = mm(l_mil), mm(h_mil)
    c, s_ = abs(math.cos(math.radians(rotation))), abs(math.sin(math.radians(rotation)))
    traversante = trou is not None
    return {"id": f"{des}#{k}", "nom": nom, "reseau": reseau, "lx": mm(lx_mil), "ly": mm(ly_mil), "forme": forme,
            "largeur": l, "hauteur": h, "rotation": float(rotation), "rayon_coin": 0.0, "points": [],
            "couches": list(couches), "face": "toutes" if traversante else "composant", "trou": trou,
            "traversante": traversante, "expansion_masque": 0.0, "pile": None,
            "via_dans_pastille_permis": via_permis or traversante, "classe_isolement": 0,
            "hw": round((c * l + s_ * h) / 2, 9), "hh": round((s_ * l + c * h) / 2, 9), "th": traversante}


def trou_g(diametre_mm, decalage_mil=(0, 0)):
    """Trou rond métallisé de diamètre ``diametre_mm``, décalé de ``decalage_mil`` (repère de la pastille)."""
    return {"diametre": diametre_mm, "forme": "rond", "longueur": diametre_mm, "rotation": 0.0,
            "decalage": [mm(decalage_mil[0]), mm(decalage_mil[1])], "metallise": True}


def region_g(des, k, reseau_, points_mil, pastille_=None, liaison=None):
    return {"id": f"{des}/R{k}", "couches": ["TopLayer"], "face": "composant", "pastille": pastille_,
            "reseau": reseau_, "liaison": liaison, "points": pts_mm(points_mil), "classe_isolement": 0}


def via_e(des, k, lx_mil, ly_mil, dim, reseau_):
    return {"id": f"{des}/V{k}", "x": mm(lx_mil), "y": mm(ly_mil), "diametre": dim[0], "trou": dim[1],
            "reseau": reseau_, "classe_isolement": 0}


def piece_g(nom, x_mil, y_mil, pastilles, rotation=0.0, dessous=False, regions=(), vias=(), virtuel=False,
            nature="connecteur"):
    """Pièce fixe générale ; ``lbb`` = boîte locale des pastilles, des régions et des vias d'empreinte."""
    xs = [p["lx"] - p["hw"] for p in pastilles] + [p["lx"] + p["hw"] for p in pastilles]
    ys = [p["ly"] - p["hh"] for p in pastilles] + [p["ly"] + p["hh"] for p in pastilles]
    for r in regions:
        xs += [q[0] for q in r["points"]]
        ys += [q[1] for q in r["points"]]
    for v in vias:
        xs += [v["x"] - v["diametre"] / 2, v["x"] + v["diametre"] / 2]
        ys += [v["y"] - v["diametre"] / 2, v["y"] + v["diametre"] / 2]
    pose = [mm(x_mil), mm(y_mil), float(rotation), dessous]
    return {"nom": nom, "empreinte": "CORPUS_LIBRE" if virtuel else "CORPUS_PASTILLES", "nature": nature,
            "genre": "standard", "valeur": "", "hauteur": 0.0, "statut": "fixe",
            "raison_statut": "virtuel" if virtuel else "contrainte", "virtuel": virtuel,
            "lbb": [round(min(xs), 9), round(min(ys), 9), round(max(xs), 9), round(max(ys), 9)],
            "pastilles": pastilles, "regions": list(regions), "vias": list(vias), "zones_interdites": [],
            "decoupes": [], "pose_actuelle": list(pose), "pose_cible": list(pose), "orientations": [],
            "regle": "contrainte", "pas_fin": False, "matrice": False, "pas_min_mm": None}


def placements_g(pieces):
    """placements/2 : pièces non virtuelles à leur pose (face et rotation comprises)."""
    return {"format": "placements/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour",
            "pieces": [{"designator": p["nom"], "side": "bottom" if p["pose_cible"][3] else "top",
                        "x": round(p["pose_cible"][0] / MIL, 3), "y": round(p["pose_cible"][1] / MIL, 3),
                        "rotation": p["pose_cible"][2], "statut": "fixe"} for p in pieces if not p["virtuel"]]}


def zone_g(reseau_, contour_mil, trous_mil=()):
    return {"reseau": reseau_, "contour": pts_mm(contour_mil), "trous": [pts_mm(t) for t in trous_mil]}


def plans(d, zones_par_couche):
    """Remplace les zones de plan : {couche: [zones]} ; rôle plan si la couche a des zones, routage sinon."""
    for c in d["couches"]:
        zs = zones_par_couche.get(c["nom"], [])
        c["zones_plan"] = zs
        c["role"] = "plan" if zs else "routage"
    return d


def surface(reseau_, couche, contour_mil):
    return {"net": reseau_, "layer": couche, "contour": [[float(x), float(y)] for x, y in contour_mil], "trous": [],
            "type": "remplissage", "origine": "moteur"}


def antenne(piste, x_mil, y_mil, reseau_="N1"):
    """Antenne (§4.4) à l'extrémité (x, y) mil d'une piste : violation et texte d'exemple."""
    x, y = round(x_mil * MIL, 6), round(y_mil * MIL, 6)
    return (violation("antenne", [reseau_], [piste], [x, y]),
            {"antenne": [f"{piste} ({reseau_}) : extrémité ({x:.3f} ; {y:.3f}) mm sans contact strict"]})


def cout(longueur_mm, vias=0):
    """Coût commun sans voisin d'un autre réseau (écart moyen 0) : L + 1,5 × vias + 1000 × 0,6."""
    return round(longueur_mm + 1.5 * vias + 600, 9)


def cas_etape5():
    L = []
    O17 = [17.78, 5.08]                       # centre d'une pastille en (700, 200) mil
    lo_j2 = [ouverte("N1", ["J2#0"], ["J2.1"], O17)]
    lo_j2_gnd = [ouverte("GND", ["J2#0"], ["J2.1"], O17)]
    N1_2 = [reseau("N1", COUCHES_2)]

    # 015 — piste – région (strict : extrémité dans le cuivre) et région – pastille (préexistants : recouvrement).
    # J2 en (600,200) : J2#0 en local (100,0) = (700,200) ; J2/R0 local [−50 ; 110] × [−20 ; 20] = monde
    # [550 ; 710] × [180 ; 220], qui recouvre J2#0 ([680 ; 720]). P0 (100,200)→(560,200) : extrémité à 10 mil dans
    # la région (distance signée −0,254 mm). Une composante {J1#0, J2#0, J2/R0, P0} : valide dans les deux modes.
    # Longueur 460 mil = 11,684 mm ; coût 611,684.
    nom = "015_piste_region"
    j2 = piece_g("J2", 600, 200, [pastille_g("J2", 0, "1", "N1", 100, 0)],
                 regions=[region_g("J2", 0, "N1", [[-50, -20], [110, -20], [110, 20], [-50, 20]], "J2#0")])
    pieces = [j("J1", 100, 200, "N1"), j2]
    d = donnees(nom, pieces, N1_2)
    r = routage([(100, 200, 560, 200, 10, T, "N1")], [])
    ok = dict(valide=True, a_router=1, complets=1, longueur=11.684)
    L.append(cas(nom, "Piste dont l'extrémité est dans une région d'empreinte qui recouvre la pastille : valide "
                      "dans les deux modes.",
                 ["contact:piste-region", "contact:preexistants", "objet:region"], d, placements_g(pieces), r,
                 verdict("strict", **ok), verdict("altium", **ok), mesures(cout(11.684), 11.684)))

    # 016 — piste – via d'empreinte par recouvrement seulement. J2 (700,200) : J2#0 (via permis) et J2/V0 en local
    # (−30,0) = (670,200), Ø 0,6 : bord à 681,811 mil, recouvre J2#0 (bord 680). P0 (100,200)→(656,200) : extrémité à
    # 14 mil = 0,3556 mm du centre du via (> 0,3 : pas de contact strict ; < 0,3 + 0,127 : recouvrement).
    # Strict : {J1#0, P0} et {J2#0, J2/V0} (2 objets chacune) ⇒ référence J1#0 ⇒ liaison ouverte J2. Antenne
    # en (656,200) dans les deux modes. Longueur 556 mil = 14,1224 mm ; coût 614,1224.
    nom = "016_piste_via_empreinte"
    j2 = piece_g("J2", 700, 200, [pastille_g("J2", 0, "1", "N1", via_permis=True)],
                 vias=[via_e("J2", 0, -30, 0, VIA_06, "N1")])
    pieces = [j("J1", 100, 200, "N1"), j2]
    d = donnees(nom, pieces, N1_2)
    r = routage([(100, 200, 656, 200, 10, T, "N1")], [])
    ant, ex = antenne("P0", 656, 200)
    L.append(cas(nom, "Piste dont l'extrémité est hors du cuivre d'un via d'empreinte mais le recouvre : liaison "
                      "ouverte en strict, reliée en altium ; antenne dans les deux modes.",
                 ["contact:piste-via-empreinte", "contact:preexistants", "objet:via_empreinte", "modes_differents",
                  "violation:antenne"], d, placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 14.1224, lo_j2, [ant], ex),
                 verdict("altium", False, 1, 1, 14.1224, [], [ant], ex), mesures(cout(14.1224), 14.1224)))

    # 017 — piste – cuivre fixe par recouvrement seulement. F0 : piste fixe N1 (500,200)→(700,200), 20 mil, qui
    # finit au centre de J2. P0 (100,200)→(486,200) : extrémité à 14 mil de l'extrémité de F0, distance signée
    # 14 − 10 = 4 mil = 0,1016 mm (pas de contact strict), écart 14 − 10 − 5 = −1 mil (recouvrement).
    # Strict : {J1#0, P0} et {J2#0, F0} ⇒ liaison ouverte J2. Antenne en (486,200). Longueur 386 mil = 9,8044 mm.
    nom = "017_piste_cuivre_fixe"
    pieces = [j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1")]
    d = donnees(nom, pieces, N1_2)
    d["cuivre_fixe"] = [{"id": "F0", "type": "piste", "reseau": "N1", "couches": [T], "x1": mm(500), "y1": mm(200),
                         "x2": mm(700), "y2": mm(200), "largeur": mm(20), "classe_isolement": 0}]
    r = routage([(100, 200, 486, 200, 10, T, "N1")], [])
    ant, ex = antenne("P0", 486, 200)
    L.append(cas(nom, "Piste dont l'extrémité est hors d'une piste de cuivre fixe mais la recouvre : liaison ouverte "
                      "en strict, reliée en altium ; antenne dans les deux modes.",
                 ["contact:piste-cuivre-fixe", "contact:preexistants", "objet:cuivre_fixe", "modes_differents",
                  "violation:antenne"], d, placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 9.8044, lo_j2, [ant], ex),
                 verdict("altium", False, 1, 1, 9.8044, [], [ant], ex), mesures(cout(9.8044), 9.8044)))

    # 018 — via – région par recouvrement seulement. J2 (600,200) : J2#0 (700,200), J2/R0 monde [540 ; 710] ×
    # [180 ; 220]. V0 (530,200) Ø 0,6 : centre à 10 mil = 0,254 mm hors de la région (pas de contact strict),
    # rayon 0,3 > 0,254 (recouvrement). P0 (100,200)→(530,200) : bout de cuivre à 535 < 540 (pas de contact avec la
    # région). Strict : {J1#0, P0, V0} et {J2#0, J2/R0} ⇒ liaison ouverte J2 ; altium : valide.
    # Longueur 430 mil = 10,922 mm ; 1 via ; coût 612,422.
    nom = "018_via_region"
    j2 = piece_g("J2", 600, 200, [pastille_g("J2", 0, "1", "N1", 100, 0)],
                 regions=[region_g("J2", 0, "N1", [[-60, -20], [110, -20], [110, 20], [-60, 20]], "J2#0")])
    pieces = [j("J1", 100, 200, "N1"), j2]
    d = donnees(nom, pieces, N1_2)
    r = routage([(100, 200, 530, 200, 10, T, "N1")], [(530, 200, VIA_06, "N1")])
    L.append(cas(nom, "Via dont le centre est hors d'une région d'empreinte mais dont le cuivre la recouvre : liaison "
                      "ouverte en strict, valide en altium.",
                 ["contact:via-region", "contact:preexistants", "objet:region", "modes_differents"], d,
                 placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 10.922, lo_j2, vias=1),
                 verdict("altium", True, 1, 1, 10.922, vias=1), mesures(cout(10.922, 1), 10.922, vias=1)))

    # 019 — surface (recouvrement dans les deux modes). S0 : N1, TopLayer, [300 ; 500] × [170 ; 230] mil.
    # P0 (100,200)→(310,200) et P1 (490,200)→(700,200) finissent dans S0 : une composante, valide.
    # Longueur 420 mil = 10,668 mm ; coût 610,668.
    nom = "019_surface"
    pieces = [j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1")]
    d = donnees(nom, pieces, N1_2)
    r = routage([(100, 200, 310, 200, 10, T, "N1"), (490, 200, 700, 200, 10, T, "N1")], [])
    r["surfaces"] = [surface("N1", T, [[300, 170], [500, 170], [500, 230], [300, 230]])]
    ok = dict(valide=True, a_router=1, complets=1, longueur=10.668)
    L.append(cas(nom, "Deux pistes reliées par une surface de cuivre neuve (recouvrement) : valide dans les deux "
                      "modes.",
                 ["contact:surface", "objet:surface"], d, placements_g(pieces), r,
                 verdict("strict", **ok), verdict("altium", **ok), mesures(cout(10.668), 10.668)))

    # 020 — objets préexistants entre eux. J1 (100,200), J2 (300,200) ; F0 : région fixe N1 [110 ; 290] ×
    # [190 ; 210] mil qui recouvre les deux pastilles ; aucun routage : valide. Aucune piste : écart 0, coût 600.
    nom = "020_preexistants"
    pieces = [j("J1", 100, 200, "N1"), j("J2", 300, 200, "N1")]
    d = donnees(nom, pieces, N1_2)
    d["cuivre_fixe"] = [{"id": "F0", "type": "region", "reseau": "N1", "couches": [T],
                         "points": pts_mm([[110, 190], [290, 190], [290, 210], [110, 210]]), "trous": [],
                         "classe_isolement": 0}]
    r = routage([], [])
    ok = dict(valide=True, a_router=1, complets=1, longueur=0.0)
    L.append(cas(nom, "Deux pastilles reliées par une région de cuivre fixe, sans routage : valide dans les deux "
                      "modes.",
                 ["contact:preexistants", "objet:cuivre_fixe"], d, placements_g(pieces), r,
                 verdict("strict", **ok), verdict("altium", **ok), mesures(600.0, 0.0)))

    # Cas à quatre couches (MidLayer1 = plan GND) ; connexion strict : rayon + 0,5 mm du bord de la zone.
    gnd = reseau("GND", ROUTAGE_4, role="plan", plans=["MidLayer1"])
    descentes = [(100, 200, 200, 200, 10, T, "GND")]

    # 021 — zone – pastille traversante. J1 (100,200), J2 (700,200) : rondes de 60 mil (r 0,762 mm), trou Ø 0,8
    # métallisé, sur les 4 couches. Zone GND = contour moins 20 mil, trou [730 ; 760] × [170 ; 230]. J1 à 80 mil =
    # 2,032 mm du bord ≥ 0,762 + 0,5 : contact strict ; J2 à 30 mil = 0,762 mm du trou < 1,262 : pas de contact
    # strict, centre dans la zone : contact altium. Strict : {J1#0, zone} (référence) et {J2#0} ⇒ liaison ouverte J2.
    # Aucun routage : coût 600.
    nom = "021_zone_pastille_traversante"
    th = dict(l_mil=60, h_mil=60, forme="rond", couches=COUCHES_4, trou=trou_g(0.8))
    pieces = [piece_g("J1", 100, 200, [pastille_g("J1", 0, "1", "GND", **th)]),
              piece_g("J2", 700, 200, [pastille_g("J2", 0, "1", "GND", **th)])]
    d = plans(donnees(nom, pieces, [gnd], quatre_couches=True),
              {"MidLayer1": [zone_g("GND", ZONE_GND, [[[730, 170], [760, 170], [760, 230], [730, 230]]])]})
    r = routage([], [], quatre_couches=True)
    L.append(cas(nom, "Pastille traversante métallisée dans la zone de plan mais à moins de rayon + isolement de "
                      "connexion d'un trou de la zone : liaison ouverte en strict, valide en altium.",
                 ["contact:zone-via", "zone_plan", "objet:pastille_traversante", "modes_differents"], d,
                 placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 0.0, lo_j2_gnd), verdict("altium", True, 1, 1, 0.0),
                 mesures(600.0, 0.0)))

    # 022 — plan externe : BottomLayer = plan GND (zone = contour moins 20 mil, trou [715 ; 760] × [150 ; 250]).
    # J1 (100,200) et J2 (700,200) posées dessous, pastilles CMS sur BottomLayer. J1 entièrement dans la zone :
    # contact strict ; J2 ([680 ; 720]) mord le trou : pas de contact strict, recouvrement (altium). Strict :
    # {J1#0, zone} et {J2#0} ⇒ liaison ouverte J2. Aucun routage : coût 600.
    nom = "022_plan_externe_cms"
    pieces = [piece_g("J1", 100, 200, [pastille_g("J1", 0, "1", "GND", couches=(B,))], dessous=True),
              piece_g("J2", 700, 200, [pastille_g("J2", 0, "1", "GND", couches=(B,))], dessous=True)]
    d = donnees(nom, pieces, [reseau("GND", [T], role="plan", plans=[B])])
    d["masses"] = ["GND"]
    plans(d, {B: [zone_g("GND", ZONE_GND, [[[715, 150], [760, 150], [760, 250], [715, 250]]])]})
    r = routage([], [])
    L.append(cas(nom, "Pastilles CMS posées sur un plan externe : l'une entièrement dans la zone (contact strict), "
                      "l'autre qui mord un trou de la zone (recouvrement seulement) : liaison ouverte en strict, "
                      "valide en altium.",
                 ["contact:zone-cms", "zone_plan", "zone:plan_externe", "modes_differents"], d,
                 placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 0.0, lo_j2_gnd), verdict("altium", True, 1, 1, 0.0),
                 mesures(600.0, 0.0)))

    # 023 — via du réseau principal dans l'îlot d'un autre réseau. MidLayer1 : zone GND (trou [590 ; 770] ×
    # [90 ; 310]) et zone P3V3 [600 ; 760] × [100 ; 300]. V0 (200,200) relié au plan GND ; V1 (650,200), relié à J2
    # par P1 (700,200)→(650,200), est dans l'îlot P3V3 : ni contact ni violation (dégagement), dans les deux modes ⇒
    # liaison ouverte J2. La zone P3V3 ne touche aucun objet de son réseau : zones_plan_isolees = 1 ; P3V3 n'a aucun
    # membre (non compté). Longueur 100 + 50 = 150 mil = 3,81 mm ; 2 vias ; coût 606,81.
    nom = "023_zone_ilot_autre_reseau"
    pieces = [j("J1", 100, 200, "GND"), j("J2", 700, 200, "GND")]
    ilot = [[600, 100], [760, 100], [760, 300], [600, 300]]
    d = plans(donnees(nom, pieces, [gnd, reseau("P3V3", ROUTAGE_4, role="alimentation", plans=["MidLayer1"])],
                      quatre_couches=True),
              {"MidLayer1": [zone_g("GND", ZONE_GND, [[[590, 90], [770, 90], [770, 310], [590, 310]]]),
                             zone_g("P3V3", ilot)]})
    r = routage(descentes + [(700, 200, 650, 200, 10, T, "GND")],
                [(200, 200, VIA_06, "GND"), (650, 200, VIA_06, "GND")], quatre_couches=True)
    m = dict(vias=2, isolees=1)
    L.append(cas(nom, "Via GND posé dans l'îlot d'un autre réseau du plan partagé : ni contact ni violation ; "
                      "liaison ouverte dans les deux modes.",
                 ["contact:zone-via", "zone_plan", "zone:ilot_autre_reseau"], d, placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 3.81, lo_j2_gnd, **m),
                 verdict("altium", False, 1, 0, 3.81, lo_j2_gnd, **m), mesures(cout(3.81, 2), 3.81, vias=2)))

    # 024 — plan partiel : zone GND [20 ; 400] × [20 ; 380]. V1 (600,200) hors de la zone : liaison ouverte J2 dans
    # les deux modes. Longueur 100 + 100 = 200 mil = 5,08 mm ; 2 vias ; coût 608,08.
    nom = "024_zone_plan_partiel"
    pieces = [j("J1", 100, 200, "GND"), j("J2", 700, 200, "GND")]
    d = plans(donnees(nom, pieces, [gnd], quatre_couches=True),
              {"MidLayer1": [zone_g("GND", [[20, 20], [400, 20], [400, 380], [20, 380]])]})
    r = routage(descentes + [(700, 200, 600, 200, 10, T, "GND")],
                [(200, 200, VIA_06, "GND"), (600, 200, VIA_06, "GND")], quatre_couches=True)
    L.append(cas(nom, "Via posé hors de la partie couverte d'un plan partiel : liaison ouverte dans les deux modes.",
                 ["contact:zone-via", "zone_plan", "zone:plan_partiel"], d, placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 5.08, lo_j2_gnd, vias=2),
                 verdict("altium", False, 1, 0, 5.08, lo_j2_gnd, vias=2), mesures(cout(5.08, 2), 5.08, vias=2)))

    # 025 — bande de retrait : zone GND = contour moins 60 mil ([60 ; 740] × [60 ; 340]). V1 (750,200) dans la bande
    # (hors de la zone ; bord du cuivre à 38,19 mil = 0,970 mm du contour ≥ garde 0,6) : liaison ouverte J2 dans les
    # deux modes. Longueur 100 + 50 = 150 mil = 3,81 mm ; 2 vias ; coût 606,81.
    nom = "025_zone_bande_retrait"
    pieces = [j("J1", 100, 200, "GND"), j("J2", 700, 200, "GND")]
    d = plans(donnees(nom, pieces, [gnd], quatre_couches=True),
              {"MidLayer1": [zone_g("GND", [[60, 60], [740, 60], [740, 340], [60, 340]])]})
    r = routage(descentes + [(700, 200, 750, 200, 10, T, "GND")],
                [(200, 200, VIA_06, "GND"), (750, 200, VIA_06, "GND")], quatre_couches=True)
    L.append(cas(nom, "Via posé dans la bande de retrait du plan (entre le contour de la carte et la zone) : liaison "
                      "ouverte dans les deux modes.",
                 ["contact:zone-via", "zone_plan", "zone:bande_retrait"], d, placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 3.81, lo_j2_gnd, vias=2),
                 verdict("altium", False, 1, 0, 3.81, lo_j2_gnd, vias=2), mesures(cout(3.81, 2), 3.81, vias=2)))

    # 026 — via près du bord de la zone (contour) : zone GND = contour moins 20 mil ; V1 (760,200) à 20 mil =
    # 0,508 mm du bord x = 780 < 0,3 + 0,5 : pas de contact strict ; centre dans la zone : contact altium. Bord du
    # cuivre à 28,19 mil = 0,716 mm du contour (≥ 0,6). Strict : liaison ouverte J2 ; altium : valide.
    # Longueur 100 + 60 = 160 mil = 4,064 mm ; 2 vias ; coût 607,064.
    nom = "026_zone_bord"
    pieces = [j("J1", 100, 200, "GND"), j("J2", 700, 200, "GND")]
    d = plans(donnees(nom, pieces, [gnd], quatre_couches=True), {"MidLayer1": [zone_g("GND", ZONE_GND)]})
    r = routage(descentes + [(700, 200, 760, 200, 10, T, "GND")],
                [(200, 200, VIA_06, "GND"), (760, 200, VIA_06, "GND")], quatre_couches=True)
    L.append(cas(nom, "Via dans la zone de plan mais à moins de rayon + isolement de connexion de son contour : "
                      "liaison ouverte en strict, valide en altium.",
                 ["contact:zone-via", "zone_plan", "zone:bord", "modes_differents"], d, placements_g(pieces), r,
                 verdict("strict", False, 1, 0, 4.064, lo_j2_gnd, vias=2),
                 verdict("altium", True, 1, 1, 4.064, vias=2), mesures(cout(4.064, 2), 4.064, vias=2)))

    # 027 — pièce virtuelle : __libre_0 (pastille libre N1 ronde de 24 mil en (700,200), absente du placement,
    # posée à pose_cible) est un membre : N1 a 2 membres (compté) ; P0 (100,200)→(700,200) : valide.
    # Longueur 600 mil = 15,24 mm ; coût 615,24.
    nom = "027_piece_virtuelle"
    libre = piece_g("__libre_0", 700, 200, [pastille_g("__libre_0", 0, "TP", "N1", l_mil=24, h_mil=24, forme="rond")],
                    virtuel=True, nature="autre")
    pieces = [j("J1", 100, 200, "N1"), libre]
    d = donnees(nom, pieces, N1_2)
    r = routage([(100, 200, 700, 200, 10, T, "N1")], [])
    ok = dict(valide=True, a_router=1, complets=1, longueur=15.24)
    L.append(cas(nom, "Pastille libre (pièce virtuelle, absente du placement) reliée par une piste : membre de son "
                      "réseau, valide dans les deux modes.",
                 ["objet:piece_virtuelle", "contact:piste-pastille"], d, placements_g(pieces), r,
                 verdict("strict", **ok), verdict("altium", **ok), mesures(cout(15.24), 15.24)))

    # 028 — région de liaison (NT1 en (400,200)) : NT1#0 N1 en (300,200), NT1#1 N2 en (500,200), NT1/R0 sans
    # réseau, liaison [N1, N2], monde [290 ; 510] × [190 ; 210], recouvre les deux pastilles. P0 (N1)
    # (400,330)→(400,200) part de J1 et finit dans la région (contact strict) ; P1 (N2) (500,200)→(700,200) de
    # NT1#1 à J2. N1 relié par la région seule ; N2 par P1 : valide, 2 réseaux complets. Écart : la région ne compte
    # pas comme voisine des réseaux qu'elle lie (convention, LISEZMOI) et aucun autre objet ne partage de case de
    # 1 mm avec les pistes : écart 0, marge null. Longueur 130 + 200 = 330 mil = 8,382 mm ; coût 608,382.
    nom = "028_region_liaison"
    nt1 = piece_g("NT1", 400, 200, [pastille_g("NT1", 0, "1", "N1", -100, 0), pastille_g("NT1", 1, "2", "N2", 100, 0)],
                  regions=[region_g("NT1", 0, "", [[-110, -10], [110, -10], [110, 10], [-110, 10]],
                                    liaison=["N1", "N2"])], nature="liaison")
    pieces = [j("J1", 400, 330, "N1"), j("J2", 700, 200, "N2"), nt1]
    d = donnees(nom, pieces, [reseau("N1", COUCHES_2), reseau("N2", COUCHES_2)])
    r = routage([(400, 330, 400, 200, 10, T, "N1"), (500, 200, 700, 200, 10, T, "N2")], [])
    ok = dict(valide=True, a_router=2, complets=2, longueur=8.382)
    L.append(cas(nom, "Région de liaison de deux réseaux : conductrice pour chacun ; une piste qui y finit relie sa "
                      "pastille : valide dans les deux modes.",
                 ["objet:region_liaison", "objet:region", "contact:piste-region", "contact:preexistants"], d,
                 placements_g(pieces), r, verdict("strict", **ok), verdict("altium", **ok),
                 mesures(cout(8.382), 8.382)))

    # 029 — pastille tournée de 30° sur une pièce à 45° : U1 en (400,200), U1#0 rect 80 × 20 mil, axe long à 75°,
    # u = (cos 75° ; sin 75°). P0 de S = U1 + 120 u (dans J1, posée en (430,315)) à E = U1 + 44 u : E à 4 mil =
    # 0,1016 mm du bout de la pastille (pas de contact strict) et le bout rond de P0 (5 mil) la recouvre (altium).
    # Si la rotation était 45° seulement, E serait à 12 mil du bord : aucun contact. Strict : {J1#0, P0} et {U1#0}
    # ⇒ liaison ouverte U1 en (10,16 ; 5,08). Antenne en E. Longueur 76 mil = 1,9304 mm ; coût 601,9304.
    nom = "029_pastille_tournee"
    ux, uy = math.cos(math.radians(75)), math.sin(math.radians(75))
    sx, sy = round(400 + 120 * ux, 6), round(200 + 120 * uy, 6)
    ex_, ey_ = round(400 + 44 * ux, 6), round(200 + 44 * uy, 6)
    u1 = piece_g("U1", 400, 200, [pastille_g("U1", 0, "1", "N1", l_mil=80, h_mil=20, rotation=30.0)], rotation=45.0,
                 nature="circuit")
    pieces = [j("J1", 430, 315, "N1"), u1]
    d = donnees(nom, pieces, N1_2)
    r = routage([(sx, sy, ex_, ey_, 10, T, "N1")], [])
    longueur = round(math.hypot(sx - ex_, sy - ey_) * MIL, 9)
    ant, ex = antenne("P0", ex_, ey_)
    L.append(cas(nom, "Pastille rectangulaire tournée de 30° sur une pièce à 45° : extrémité de piste à 0,1 mm du "
                      "bout de la pastille sur son axe à 75° : liaison ouverte en strict, reliée en altium ; antenne.",
                 ["geometrie:pastille_tournee", "contact:piste-pastille", "modes_differents", "violation:antenne"],
                 d, placements_g(pieces), r,
                 verdict("strict", False, 1, 0, longueur, [ouverte("N1", ["U1#0"], ["U1.1"], [10.16, 5.08])],
                         [ant], ex),
                 verdict("altium", False, 1, 1, longueur, [], [ant], ex), mesures(cout(longueur), longueur)))

    # 030 — trou décalé : J1 (400,200) traversante rect 80 × 40 mil, trou rond Ø 0,6 métallisé décalé de (20 ; 0)
    # mil : centre (420,200). V0 (380,200) Ø 0,6 / 0,3 dans J1 : contact strict ; parois : 40 mil = 1,016 mm −
    # 0,3 − 0,15 = 0,566 ≥ 0,254 (sans le décalage : 0,508 − 0,45 = 0,058, trou_trou). P0 (100,200)→(380,200) de J2
    # au via. Valide. Longueur 280 mil = 7,112 mm ; 1 via ; coût 608,612.
    nom = "030_trou_decale"
    pieces = [j("J2", 100, 200, "N1"),
              piece_g("J1", 400, 200, [pastille_g("J1", 0, "1", "N1", l_mil=80, h_mil=40, couches=COUCHES_2,
                                                  trou=trou_g(0.6, (20, 0)))])]
    d = donnees(nom, pieces, N1_2)
    r = routage([(100, 200, 380, 200, 10, T, "N1")], [(380, 200, VIA_06, "N1")])
    ok = dict(valide=True, a_router=1, complets=1, longueur=7.112, vias=1)
    L.append(cas(nom, "Via dans une pastille traversante dont le trou est décalé : contact strict, parois à 0,566 mm "
                      "(pas de trou_trou) : valide dans les deux modes.",
                 ["geometrie:trou_decale", "objet:pastille_traversante", "contact:via-pastille"], d,
                 placements_g(pieces), r, verdict("strict", **ok), verdict("altium", **ok),
                 mesures(cout(7.112, 1), 7.112, vias=1)))
    return L


def exemples(*violations):
    """Textes d'exemple (libres, jamais comparés) : un par violation, groupés par type."""
    ex = {}
    for v in violations:
        ex.setdefault(v["genre"], []).append(f"{' / '.join(v['objets'])} ({' / '.join(v['reseaux'])})")
    return ex


def trou_carte(id_, x_mil, y_mil, diametre_mm, metallise, diametre_cuivre_mm=0.0):
    return {"id": id_, "x": mm(x_mil), "y": mm(y_mil), "rotation": 0.0,
            "trou": {"diametre": diametre_mm, "forme": "rond", "longueur": diametre_mm, "rotation": 0.0,
                     "decalage": [0.0, 0.0], "metallise": metallise},
            "metallise": metallise, "diametre_cuivre": diametre_cuivre_mm,
            "rayon_interdit_mm": round(max(diametre_mm, diametre_cuivre_mm) / 2 + 0.6, 9), "classe_isolement": 0,
            "piece": None}


def zone_interdite(id_, couches, points_mm, effets, forme="polygone", largeur=0.0):
    return {"id": id_, "couches": list(couches), "forme": forme, "points": points_mm, "largeur": largeur,
            "effets": list(effets), "etiquette": "corpus"}


def rect_mil(x1, y1, x2, y2):
    return pts_mm([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])


def cas_etape6():
    L = []
    L_DROITE = 15.24                          # piste (100,200)→(700,200) : 600 mil
    P_DROITE = (100, 200, 700, 200, 10, T, "N1")
    N1_2 = [reseau("N1", COUCHES_2)]

    def base(nom, *autres, reseaux=None, quatre=False, regles=None):
        pieces = [j("J1", 100, 200, "N1"), j("J2", 700, 200, "N1")] + list(autres)
        return pieces, donnees(nom, pieces, reseaux or N1_2, quatre_couches=quatre, regles=regles)

    def deux_modes(valide, longueur, violations=(), **options):
        ex = exemples(*violations)
        return (verdict("strict", valide, 1, 1, longueur, violations=violations, exemples=ex, **options),
                verdict("altium", valide, 1, 1, longueur, violations=violations, exemples=ex, **options))

    # 031 — garde_bord. J1 (100,25), J2 (700,25) (pastilles y ∈ [5 ; 45] mil), P0 (100,25)→(700,25) 10 mil : cuivre
    # y ∈ [20 ; 30] ; hors des pastilles (x ∈ [120 ; 680]) le cuivre est à 20 mil = 0,508 mm du bord y = 0 < 0,6 :
    # une violation. Longueur 15,24 ; coût 615,24.
    nom = "031_garde_bord"
    pieces = [j("J1", 100, 25, "N1"), j("J2", 700, 25, "N1")]
    d = donnees(nom, pieces, N1_2)
    r = routage([(100, 25, 700, 25, 10, T, "N1")], [])
    v = violation("garde_bord", N1, ["P0"], [3.048, 0.635], 0.508, 0.6)
    s_, a_ = deux_modes(False, L_DROITE, [v])
    L.append(cas(nom, "Piste à 0,508 mm du bord (garde 0,6) hors de ses pastilles : garde_bord dans les deux modes.",
                 ["violation:garde_bord"], d, placements(pieces), r, s_, a_, mesures(cout(L_DROITE), L_DROITE)))

    # 032 — garde_bord : partie d'axe dans une pastille du réseau non comptée. J1 (20,200) : pastille x ∈ [0 ; 40]
    # (au bord), P0 (20,200)→(700,200). L'axe dans J1 (x ≤ 40) est exclu ; au-delà, cuivre à 40 − 5 = 35 mil =
    # 0,889 mm du bord x = 0 ≥ 0,6. Sans l'exclusion, le bout (x = 15 mil = 0,381 mm) serait fautif. Valide.
    # Longueur 680 mil = 17,272 ; coût 617,272.
    nom = "032_garde_bord_pastille"
    pieces = [j("J1", 20, 200, "N1"), j("J2", 700, 200, "N1")]
    d = donnees(nom, pieces, N1_2)
    r = routage([(20, 200, 700, 200, 10, T, "N1")], [])
    s_, a_ = deux_modes(True, 17.272)
    L.append(cas(nom, "Piste qui part d'une pastille au bord : la partie de l'axe dans la pastille n'est pas soumise "
                      "à la garde de bord ; le reste est à 0,889 mm : valide.",
                 ["violation:garde_bord"], d, placements(pieces), r, s_, a_, mesures(cout(17.272), 17.272)))

    # 033 — contour polygonal : carte en L, [0 ; 800] × [0 ; 400] privée de [0 ; 400[ × ]250 ; 400] (contour
    # (0,0) (800,0) (800,400) (400,400) (400,250) (0,250)). J1 (100,225), J2 (700,225), P0 (100,225)→(700,225) :
    # cuivre y ≤ 230, à 20 mil = 0,508 mm de l'arête y = 250 (x ≤ 400) < 0,6 : garde_bord. Avec la seule boîte de
    # la carte, aucune violation. Longueur 15,24 ; coût 615,24.
    nom = "033_contour_polygonal"
    pieces = [j("J1", 100, 225, "N1"), j("J2", 700, 225, "N1")]
    d = donnees(nom, pieces, N1_2)
    d["carte"]["contour"] = pts_mm([[0, 0], [800, 0], [800, 400], [400, 400], [400, 250], [0, 250]])
    r = routage([(100, 225, 700, 225, 10, T, "N1")], [])
    v = violation("garde_bord", N1, ["P0"], [3.048, 5.842], 0.508, 0.6)
    s_, a_ = deux_modes(False, L_DROITE, [v])
    L.append(cas(nom, "Carte en L : piste à 0,508 mm de l'arête rentrante du contour, loin de la boîte : "
                      "garde_bord (contour polygonal exact).",
                 ["violation:garde_bord", "geometrie:contour_polygonal"], d, placements(pieces), r, s_, a_,
                 mesures(cout(L_DROITE), L_DROITE)))

    # 034 — garde_decoupe : découpe D1 [380 ; 420] × [225 ; 300] mil ; cuivre de P0 y ≤ 205 : 20 mil = 0,508 mm
    # < 0,6. Une violation. Coût 615,24.
    nom = "034_garde_decoupe"
    pieces, d = base(nom)
    d["carte"]["decoupes"] = [{"id": "D1", "points": rect_mil(380, 225, 420, 300), "proprietaire": None}]
    r = routage([P_DROITE], [])
    v = violation("garde_decoupe", N1, ["P0", "D1"], [10.16, 5.461], 0.508, 0.6)
    s_, a_ = deux_modes(False, L_DROITE, [v])
    L.append(cas(nom, "Piste à 0,508 mm d'une découpe (garde 0,6) : garde_decoupe.", ["violation:garde_decoupe"],
                 d, placements(pieces), r, s_, a_, mesures(cout(L_DROITE), L_DROITE)))

    # 035 — trou non métallisé traversé : T1 (400,200) Ø 1,0 non métallisé, sans cuivre. P0 le traverse : écart
    # 0 − 0,127 − 0,5 < 0,6 : garde_trou (pas d'isolement : un trou sans cuivre n'est pas un objet de cuivre).
    # Coût 615,24.
    nom = "035_trou_non_metallise_traverse"
    pieces, d = base(nom)
    d["trous"] = [trou_carte("T1", 400, 200, 1.0, False)]
    r = routage([P_DROITE], [])
    v = violation("garde_trou", ["N1", ""], ["P0", "T1"], [10.16, 5.08], -0.627, 0.6)
    s_, a_ = deux_modes(False, L_DROITE, [v])
    L.append(cas(nom, "Piste qui traverse un trou non métallisé sans cuivre : garde_trou, sans isolement.",
                 ["violation:garde_trou", "objet:trou"], d, placements(pieces), r, s_, a_,
                 mesures(cout(L_DROITE), L_DROITE)))

    # 036 — cuivre de trou : T1 (400,260) Ø 1,0 métallisé, cuivre Ø 2,0 (obstacle sans réseau, pas de garde_trou).
    # Écart à P0 : 60 mil = 1,524 − 1,0 − 0,127 = 0,397 ≥ 0,2 : valide. Voisin (cases de 1 mm partagées) : écart
    # moyen = min(1,0 ; 0,397) = 0,397 = écart min ; marge 0,197, paire T1() / P0(N1).
    # Coût = 15,24 + 1000 × (0,6 − 0,397) = 218,24.
    nom = "036_cuivre_de_trou"
    pieces, d = base(nom)
    d["trous"] = [trou_carte("T1", 400, 260, 1.0, True, 2.0)]
    r = routage([P_DROITE], [])
    s_, a_ = deux_modes(True, L_DROITE, ecart_moyen=0.397, ecart_min=0.397, marge=0.197, paire="T1() / P0(N1)")
    L.append(cas(nom, "Trou métallisé à cuivre de 2 mm à 0,397 mm d'une piste : obstacle d'isolement respecté, "
                      "voisin de l'écart moyen ; valide.",
                 ["objet:trou", "mesure:ecart"], d, placements(pieces), r, s_, a_,
                 mesures(218.24, L_DROITE, ecart_moyen=0.397)))

    # 037 — zone interdite élargie de isolement.defaut (0,2). K1 [380 ; 420] × [210 ; 260] mil sur TopLayer, effets
    # pistes et vias : 5 mil = 0,127 mm du cuivre de P0 (sans la couper) < 0,2 : zone_interdite. K2 coupe P0 mais
    # sur BottomLayer ; K3 coupe P0 sur TopLayer mais n'interdit que les vias : sans effet. Coût 615,24.
    nom = "037_zone_interdite_elargie"
    pieces, d = base(nom)
    d["zones_routage_interdites"] = [
        zone_interdite("K1", [T], rect_mil(380, 210, 420, 260), ["pistes", "vias"]),
        zone_interdite("K2", [B], rect_mil(200, 150, 240, 250), ["pistes"]),
        zone_interdite("K3", [T], rect_mil(550, 150, 590, 250), ["vias"])]
    r = routage([P_DROITE], [])
    v = violation("zone_interdite", N1, ["P0", "K1"], [10.16, 5.08], 0.127, 0.2)
    s_, a_ = deux_modes(False, L_DROITE, [v])
    L.append(cas(nom, "Piste à 0,127 mm d'une zone interdite (élargie de 0,2) : violation ; zones d'une autre couche "
                      "ou d'un autre effet : sans effet.",
                 ["violation:zone_interdite", "zone_interdite:elargie"], d, placements(pieces), r, s_, a_,
                 mesures(cout(L_DROITE), L_DROITE)))

    # 038 — zone interdite d'empreinte posée : J3 (400,300) tourné de 180°, pastille N2 ; keep-out polyligne
    # (1,0 ; 2,2) → (−1,0 ; 2,2) mm en local, largeur 0,2, toutes couches, effet pistes : posé en y = 7,62 − 2,2 =
    # 5,42 mm, bas à 5,32 ; cuivre de P0 jusqu'à 5,207 : 0,113 < 0,2 : zone_interdite. J3 (N2, seule pastille : réseau
    # non compté) à 75 mil de P0 : ni isolement ni voisin (cases non partagées). Coût 615,24.
    nom = "038_zone_interdite_empreinte"
    j3 = piece_g("J3", 400, 300, [pastille_g("J3", 0, "1", "N2")], rotation=180.0)
    k0 = zone_interdite("J3/K0", ["toutes"], [[1.0, 2.2], [-1.0, 2.2]], ["pistes"], forme="polyligne", largeur=0.2)
    del k0["etiquette"]
    k0["source"] = "keepout"
    j3["zones_interdites"] = [k0]
    pieces, d = base(nom, j3, reseaux=[reseau("N1", COUCHES_2), reseau("N2", COUCHES_2)])
    r = routage([P_DROITE], [])
    v = violation("zone_interdite", N1, ["P0", "J3/K0"], [10.16, 5.08], 0.113, 0.2)
    s_, a_ = deux_modes(False, L_DROITE, [v])
    L.append(cas(nom, "Keep-out polyligne d'une empreinte tournée de 180° : posé avec la pièce, il passe à 0,113 mm "
                      "de la piste : zone_interdite.",
                 ["violation:zone_interdite", "zone_interdite:empreinte"], d, placements_g(pieces), r, s_, a_,
                 mesures(cout(L_DROITE), L_DROITE)))

    # 039 — arrondi au millième de mil en limite de pastille. P0 : cuivre y ≤ 205 mil. J3 (300 ; 232,874) N2 : bas à
    # 212,874, écart 7,874 mil = 0,1999996 mm (0,2 − 4e-7 : dans ε_isolement) ; J4 (500 ; 232,870) N3 : écart
    # 7,870 mil = 0,199898 mm (0,2 − 1,02e-4 : hors ε) : un isolement. Écart moyen = écart min = 0,199898 ;
    # marge −0,000102, paire J4#0(N3) / P0(N1). Coût = 15,24 + 1000 × (0,6 − 0,199898) = 415,342.
    nom = "039_arrondi_limite_pastille"
    pieces, d = base(nom, j("J3", 300, 232.874, "N2"), j("J4", 500, 232.870, "N3"),
                     reseaux=[reseau(n, COUCHES_2) for n in ("N1", "N2", "N3")])
    r = routage([P_DROITE], [])
    v = violation("isolement", ["N3", "N1"], ["J4#0", "P0"], [11.43, 5.3069], 0.199898, 0.2)
    s_, a_ = deux_modes(False, L_DROITE, [v], ecart_moyen=0.199898, ecart_min=0.199898, marge=-0.000102,
                        paire="J4#0(N3) / P0(N1)")
    L.append(cas(nom, "Pastilles à 7,874 et 7,870 mil d'une piste (isolement 0,2 mm = 7,874 mil) : la première est "
                      "dans ε_isolement, la seconde non : un isolement.",
                 ["violation:isolement", "arrondi:pastille", "mesure:ecart"], d, placements(pieces), r, s_, a_,
                 mesures(415.342, L_DROITE, ecart_moyen=0.199898)))

    # 040 — largeur maximale arrondie au millième vers le haut : largeurs [0,2 ; 0,254 ; 0,3], max 0,3 mm =
    # 11,811 02 mil → 11,812. N1 : P0 (100,100)→(700,100) 11,812 mil : conforme ; N2 : P1 (100,300)→(700,300)
    # 11,813 mil : largeur. Pistes à 5 mm l'une de l'autre : aucun voisin. Longueur 30,48 ; coût 630,48.
    nom = "040_largeur_max_millieme"
    pieces = [j("J1", 100, 100, "N1"), j("J2", 700, 100, "N1"), j("J3", 100, 300, "N2"), j("J4", 700, 300, "N2")]
    res = [reseau("N1", COUCHES_2), reseau("N2", COUCHES_2)]
    for x in res:
        x["largeurs_mm"] = [0.2, 0.254, 0.3]
    d = donnees(nom, pieces, res)
    r = routage([(100, 100, 700, 100, 11.812, T, "N1"), (100, 300, 700, 300, 11.813, T, "N2")], [])
    v = violation("largeur", ["N2"], ["P1"], [10.16, 7.62], 0.3000502, 0.3)
    ex = exemples(v)
    L.append(cas(nom, "Largeur maximale 0,3 mm arrondie à 11,812 mil : 11,812 conforme, 11,813 en violation.",
                 ["violation:largeur", "arrondi:largeur"], d, placements(pieces), r,
                 verdict("strict", False, 2, 2, 30.48, violations=[v], exemples=ex),
                 verdict("altium", False, 2, 2, 30.48, violations=[v], exemples=ex), mesures(630.48, 30.48)))

    # 041 — dimension_via : P0..P3 (100→250→400→550→700, y 200) ; V0 (250,200) Ø 0,8005 (31,516 mil = 0,800 506 :
    # dans ε_via) ; V1 (400,200) Ø 0,9 (35,433 mil = 0,899 998 > 0,8 + 1e-3) ; V2 (550,200) trou 0,24 (9,449 mil =
    # 0,240 005 < 0,25 − 1e-3) : deux violations. Alignements droits : aucun coude. Coût 15,24 + 4,5 + 600 = 619,74.
    nom = "041_dimension_via"
    pieces, d = base(nom)
    r = routage([(100, 200, 250, 200, 10, T, "N1"), (250, 200, 400, 200, 10, T, "N1"),
                 (400, 200, 550, 200, 10, T, "N1"), (550, 200, 700, 200, 10, T, "N1")],
                [(250, 200, (0.8005, 0.3), "N1"), (400, 200, (0.9, 0.3), "N1"), (550, 200, (0.6, 0.24), "N1")])
    vs = [violation("dimension_via", N1, ["V1"], [10.16, 5.08], 0.8999982, 0.8),
          violation("dimension_via", N1, ["V2"], [13.97, 5.08], 0.2400046, 0.25)]
    s_, a_ = deux_modes(False, L_DROITE, vs, vias=3)
    L.append(cas(nom, "Vias hors des bornes de leur réseau (diamètre 0,9, trou 0,24) : deux dimension_via ; "
                      "Ø 0,8005 dans ε_via.",
                 ["violation:dimension_via"], d, placements(pieces), r, s_, a_,
                 mesures(619.74, L_DROITE, vias=3)))

    # 042 — via_sous_cms : V0 (700,200) au centre de J2 (CMS, via non permis), même réseau, dérogation désactivée :
    # violation. Coût 15,24 + 1,5 + 600 = 616,74.
    nom = "042_via_sous_cms"
    pieces, d = base(nom)
    r = routage([P_DROITE], [(700, 200, VIA_06, "N1")])
    v = violation("via_sous_cms", ["N1", "N1"], ["J2#0", "V0"], [17.78, 5.08])
    s_, a_ = deux_modes(False, L_DROITE, [v], vias=1)
    L.append(cas(nom, "Via dans une pastille CMS dont le via n'est pas permis (même réseau, sans dérogation) : "
                      "via_sous_cms.",
                 ["violation:via_sous_cms"], d, placements(pieces), r, s_, a_, mesures(616.74, L_DROITE, vias=1)))

    # 043 — dérogation : même géométrie, via_dans_pastille_meme_reseau vrai : V0 dans J2 compte une dérogation ;
    # V1 (100,200) dans J1 dont le via est permis : rien. Valide. Coût 15,24 + 3 + 600 = 618,24.
    nom = "043_via_sous_cms_derogation"
    pieces = [j("J1", 100, 200, "N1", via_permis=True), j("J2", 700, 200, "N1")]
    d = donnees(nom, pieces, N1_2, regles={"via_dans_pastille_meme_reseau": True})
    r = routage([P_DROITE], [(700, 200, VIA_06, "N1"), (100, 200, VIA_06, "N1")])
    s_, a_ = deux_modes(True, L_DROITE, vias=2)
    for x in (s_, a_):
        x["derogations"]["via_dans_pastille_meme_reseau"] = 1
    L.append(cas(nom, "Via dans une pastille CMS du même réseau avec via_dans_pastille_meme_reseau : une dérogation "
                      "comptée, pas de violation.",
                 ["violation:via_sous_cms", "derogation:via_dans_pastille"], d, placements(pieces), r, s_, a_,
                 mesures(618.24, L_DROITE, vias=2)))

    # 044 — trou_trou : V0 (400,200) (trou 0,3) et J3 (400,225) traversante ronde 60 mil, trou 0,6, N1 : entraxe
    # 25 mil = 0,635 − 0,15 − 0,3 = 0,185 < 0,254 : trou_trou (même réseau). Le centre de V0 est dans J3 (0,635 <
    # 0,762) : contact strict. Coût 15,24 + 1,5 + 600 = 616,74.
    nom = "044_trou_trou"
    j3 = piece_g("J3", 400, 225, [pastille_g("J3", 0, "1", "N1", l_mil=60, h_mil=60, forme="rond",
                                             couches=COUCHES_2, trou=trou_g(0.6))])
    pieces, d = base(nom, j3)
    r = routage([(100, 200, 400, 200, 10, T, "N1"), (400, 200, 700, 200, 10, T, "N1")], [(400, 200, VIA_06, "N1")])
    v = violation("trou_trou", ["N1", "N1"], ["J3#0", "V0"], [10.16, 5.3975], 0.185, 0.254)
    s_, a_ = deux_modes(False, L_DROITE, [v], vias=1)
    for x in (s_, a_):
        x["reseaux_a_router"] = x["reseaux_complets"] = 1
    L.append(cas(nom, "Via à 0,185 mm (paroi à paroi) du trou d'une pastille traversante du même réseau : "
                      "trou_trou.",
                 ["violation:trou_trou", "objet:pastille_traversante"], d, placements_g(pieces), r, s_, a_,
                 mesures(616.74, L_DROITE, vias=1)))

    # 045 — couche_non_permise et reseau_inconnu. N1 permis sur TopLayer seul ; J1, J2 traversantes. P0 TopLayer,
    # P1 même trajet sur BottomLayer : couche_non_permise. P2 (100,350)→(200,350) réseau « N9 » inconnu :
    # reseau_inconnu (ni antenne ni isolement : aucun cuivre à moins de 3 mm). Longueur 15,24 × 2 + 2,54 = 33,02 ;
    # aucun voisin ; coût 633,02.
    nom = "045_couche_et_reseau_inconnu"
    pieces = [piece_g(n, x, 200, [pastille_g(n, 0, "1", "N1", couches=COUCHES_2, trou=trou_g(0.6))])
              for n, x in (("J1", 100), ("J2", 700))]
    d = donnees(nom, pieces, [reseau("N1", [T])])
    r = routage([P_DROITE, (100, 200, 700, 200, 10, B, "N1"), (100, 350, 200, 350, 10, T, "N9")], [])
    vs = [violation("couche_non_permise", N1, ["P1"], [10.16, 5.08]),
          violation("reseau_inconnu", ["N9"], ["P2"], [3.81, 8.89])]
    s_, a_ = deux_modes(False, 33.02, vs)
    L.append(cas(nom, "Piste sur une couche hors des couches permises de son réseau, piste d'un réseau inconnu.",
                 ["violation:couche_non_permise", "violation:reseau_inconnu"], d, placements_g(pieces), r, s_, a_,
                 mesures(633.02, 33.02)))

    # 046 — matrice des classes : classes [defaut, fin, large], matrice [[0,2 0,1 0,3] [0,1 0,1 0,3] [0,3 0,3 0,3]].
    # Pistes de N1 en classe 1. J3 (300,230) N2 classe 1 : écart 5 mil = 0,127 ≥ m[1][1] = 0,1 (la règle relâche
    # l'isolement par défaut 0,2) ; J4 (500,235) N3 classe 2 : écart 0,254 < m[2][1] = 0,3 (règle de paire plus
    # stricte) : un isolement. Marge min(0,027 ; −0,046) = −0,046, paire J4#0(N3) / P0(N1) ; écart moyen = écart
    # min = 0,127. Coût = 15,24 + 1000 × (0,6 − 0,127) = 488,24.
    nom = "046_matrice_classes"
    j3, j4 = j("J3", 300, 230, "N2"), j("J4", 500, 235, "N3")
    j3["pastilles"][0]["classe_isolement"] = 1
    j4["pastilles"][0]["classe_isolement"] = 2
    res = [reseau(n, COUCHES_2) for n in ("N1", "N2", "N3")]
    res[0]["classes_isolement"] = {"piste": 1, "via": 1, "surface": 1}
    res[0]["isolement_mm"] = 0.1             # heuristique des routeurs : matrice[1][classe_reference 0]
    pieces, d = base(nom, j3, j4, reseaux=res, regles={"isolement": {
        "defaut": 0.2, "classes": ["defaut", "fin", "large"],
        "matrice": [[0.2, 0.1, 0.3], [0.1, 0.1, 0.3], [0.3, 0.3, 0.3]], "classe_reference": 0}})
    r = routage([P_DROITE], [])
    v = violation("isolement", ["N3", "N1"], ["J4#0", "P0"], [12.7, 5.334], 0.254, 0.3)
    s_, a_ = deux_modes(False, L_DROITE, [v], ecart_moyen=0.127, ecart_min=0.127, marge=-0.046,
                        paire="J4#0(N3) / P0(N1)")
    L.append(cas(nom, "Matrice des classes : une règle relâche l'isolement d'une paire (0,1), une autre le durcit "
                      "(0,3) : un seul isolement, requis lu dans la matrice.",
                 ["violation:isolement", "regles:matrice", "mesure:ecart"], d, placements(pieces), r, s_, a_,
                 mesures(488.24, L_DROITE, ecart_moyen=0.127)))

    # 047 — amorce coupée (audit) : amorce P0 (100,200)→(150,200) depuis J1 ; reste du trajet P1 (160,200)→
    # (700,200). Cuivres bout à bout (écart 0) : reliés en altium ; strict : extrémités à 10 mil = 0,254 > 0,127 de
    # l'axe de l'autre : liaison ouverte (référence {J1#0, P0}, égalité : plus petit identifiant ⇒ J2 ouverte) et
    # deux antennes (dans les deux modes). Longueur 590 mil = 14,986 ; coût 614,986.
    nom = "047_amorce_coupee"
    pieces, d = base(nom)
    r = routage([(100, 200, 150, 200, 10, T, "N1"), (160, 200, 700, 200, 10, T, "N1")], [])
    vs = [violation("antenne", N1, ["P0"], [3.81, 5.08]), violation("antenne", N1, ["P1"], [4.064, 5.08])]
    ex = exemples(*vs)
    L.append(cas(nom, "Amorce de sortie coupée à 0,254 mm de la suite de la piste : cuivres bout à bout (reliés en "
                      "altium), liaison ouverte en strict ; deux antennes.",
                 ["audit:amorce_coupee", "violation:antenne", "contact:piste-piste", "modes_differents"], d,
                 placements(pieces), r,
                 verdict("strict", False, 1, 0, 14.986, [ouverte("N1", ["J2#0"], ["J2.1"], [17.78, 5.08])], vs, ex),
                 verdict("altium", False, 1, 1, 14.986, [], vs, ex), mesures(614.986, 14.986)))

    # 048 — avertissement pistes_sous_pieces_sans_piste_dessous : L1 (400,300) (sans_piste_dessous), pastilles
    # (0,−60) N2 et (0,60) N3 en local, lbb [−20 ; 20] × [−80 ; 80] mil. P0 (100,300)→(700,300) coupe la boîte, à
    # 35 mil = 0,889 mm des deux pastilles (> 0,3) : un avertissement. Voisin : L1#1 (cases y = 8 partagées) ⇒ écart
    # moyen = écart min = 0,889 ; marge : boîtes élargies de 0,3 disjointes ⇒ null. Coût = 15,24 (0,889 ≥ 0,6).
    nom = "048_piste_sous_piece"
    l1 = piece_g("L1", 400, 300, [pastille_g("L1", 0, "1", "N2", 0, -60), pastille_g("L1", 1, "2", "N3", 0, 60)],
                 nature="inductance")
    pieces = [j("J1", 100, 300, "N1"), j("J2", 700, 300, "N1"), l1]
    d = donnees(nom, pieces, [reseau(n, COUCHES_2) for n in ("N1", "N2", "N3")])
    d["sans_piste_dessous"] = ["L1"]
    r = routage([(100, 300, 700, 300, 10, T, "N1")], [])
    s_, a_ = deux_modes(True, L_DROITE, ecart_moyen=0.889, ecart_min=0.889)
    for x in (s_, a_):
        x["avertissements"]["pistes_sous_pieces_sans_piste_dessous"] = 1
    L.append(cas(nom, "Piste sous une pièce sans_piste_dessous, loin de ses pastilles : avertissement non bloquant.",
                 ["avertissement:sous_piece", "mesure:ecart"], d, placements_g(pieces), r, s_, a_,
                 mesures(15.24, L_DROITE, ecart_moyen=0.889)))
    return L

# --- Étape 7 : contrôle de placement (§4.5) ------------------------------------------------------------------------
#
# Pièces mobiles à une pastille CMS carrée de 40 mil sans réseau (aucun réseau à router : verdict de routage valide,
# 0 réseau, longueur 0, écart moyen 0 ⇒ coût = 1000 × 0,6 = 600). Boîte locale = pastille : ±20 mil (±0,508 mm),
# sauf mention. Règles : marge_piece 0,25 mm (9,843 mil), marge_traversante 0,6 mm (23,622 mil), marge_bord et
# marge_decoupe 1,0 mm (39,370 mil), grille 0,127 mm = 5 mil (toutes les positions sont des multiples de 5 mil,
# sauf mention). Deux boîtes de pièces doivent donc laisser 2 × 0,25 = 0,5 mm (19,685 mil) entre elles.

ORIENTATIONS = [[0.0, False], [90.0, False], [180.0, False], [270.0, False], [0.0, True], [180.0, True]]


def mob(nom, x_mil, y_mil, lbb_mil=None, classe=0, couches=("TopLayer",), trou=None, fixe=False):
    """Pièce à une pastille sans réseau ; mobile (orientations de ORIENTATIONS) sauf ``fixe``."""
    pa = pastille_g(nom, 0, "1", "", couches=couches, trou=trou)
    pa["classe_isolement"] = classe
    c = piece_g(nom, x_mil, y_mil, [pa])
    if lbb_mil is not None:
        c["lbb"] = [mm(v) for v in lbb_mil]
    if not fixe:
        c.update(statut="mobile", raison_statut="libre", orientations=[list(o) for o in ORIENTATIONS],
                 regle="corpus")
    return c


def pl(nom, x_mil, y_mil, rotation=0.0, dessous=False, statut="mobile"):
    return {"designator": nom, "side": "bottom" if dessous else "top", "x": float(x_mil), "y": float(y_mil),
            "rotation": float(rotation), "statut": statut}


def placements_p(entrees):
    return {"format": "placements/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour",
            "pieces": list(entrees)}


def cas_etape7():
    L = []
    r_vide = routage([], [])

    def ajouter(nom, description, themes, pieces, p, violations=(), infos=(), modif=None, regles=None):
        d = donnees(nom, pieces, [], regles=regles)
        if modif:
            modif(d)
        cp = controle_placement(violations)
        cp["infos"]["eloignements"] = [{"objet": o, "reference": r, "distance_mm": x, "minimum_mm": m}
                                       for o, r, x, m in infos]
        L.append(cas(nom, description, themes, d, p, r_vide, verdict("strict", True, 0, 0, 0.0),
                     verdict("altium", True, 0, 0, 0.0), mesures(600.0, 0.0), placement=cp))

    # 049 — placement valide. U1 (200,200), U2 (300,200) : boîtes à 60 mil, au-delà de 19,685 ; U3 (560,200) : à
    # 60 − 40 = 20 mil = 0,508 mm de U4 (500,200), juste au-dessus de 0,5 mm (aucun chevauchement). U5 posée dessous
    # en (200,300) (retournée : sa pastille passe sur BottomLayer). Éloignement souple U1 → U2 de 2 mm : information
    # seulement, distance des centres 100 mil = 2,54 mm.
    pieces = [mob("U1", 100, 100), mob("U2", 100, 300), mob("U3", 700, 100), mob("U4", 700, 300),
              mob("U5", 400, 300)]
    p = placements_p([pl("U1", 200, 200), pl("U2", 300, 200), pl("U3", 560, 200), pl("U4", 500, 200),
                      pl("U5", 200, 300, 0.0, True)])

    def souple(d):
        d["eloignements"] = [{"type": "souple", "objets": ["U1"], "reference": ["U2"], "distance_mm": 2.0,
                              "poids_par_mm": 5.0, "marge_mm": 0.0}]
    ajouter("049_placement_valide", "Pièces mobiles déplacées, boîtes à 0,508 mm (marge 2 × 0,25), une pièce "
            "retournée, éloignement souple (information) : placement valide.",
            ["placement:valide", "placement:eloignement"], pieces, p, infos=[("U1", "U2", 2.54, 2.0)], modif=souple)

    # 050 — chevauchement : U1 (200,200), U2 (250,200) : boîtes élargies [170,157 ; 229,843] et [220,157 ; ...] qui
    # se recouvrent (les pastilles, à 10 mil = 0,254 mm, respectent 0,2 : pas de pastille_pastille). U3 (500,200),
    # U4 (555,200) : boîtes à 15 mil = 0,381 mm < 0,5 : chevauchement.
    pieces = [mob("U1", 100, 100), mob("U2", 100, 300), mob("U3", 700, 100), mob("U4", 700, 300)]
    p = placements_p([pl("U1", 200, 200), pl("U2", 250, 200), pl("U3", 500, 200), pl("U4", 555, 200)])
    ajouter("050_chevauchement", "Deux paires de pièces mobiles dont les boîtes élargies de 0,25 mm se recouvrent.",
            ["placement:chevauchement"], pieces, p,
            [("chevauchement", ["U1", "U2"], ""), ("chevauchement", ["U3", "U4"], "")])

    # 051 — chevauchement par la face opposée. U1 dessus (200,200), U2 dessous (200,200) : pastille CMS de U2 sur
    # BottomLayer seulement : aucune violation. U3 dessus (500,200) ; T1 dessous (560,200), pastille traversante
    # (TopLayer et BottomLayer, trou 0,5 mm) : sa boîte de cuivre [540 ; 580] élargie de 23,622 mil commence à
    # 516,378 < 529,843 (bord droit de U3 élargi) : chevauchement [U3, T1] (paire (pièce de la face, pièce de la
    # pastille)). Pastilles U3 / T1 sur TopLayer à 20 mil = 0,508 mm : pas de pastille_pastille.
    pieces = [mob("U1", 100, 100), mob("U2", 100, 300), mob("U3", 700, 100),
              mob("T1", 700, 300, couches=(T, B), trou=trou_g(0.5))]
    p = placements_p([pl("U1", 200, 200), pl("U2", 200, 200, 0.0, True), pl("U3", 500, 200),
                      pl("T1", 560, 200, 0.0, True)])
    ajouter("051_chevauchement_face_opposee", "CMS de la face opposée sous une pièce : permis ; pastille "
            "traversante de la face opposée à moins de 0,6 mm de la boîte d'une pièce : chevauchement.",
            ["placement:chevauchement", "placement:face_opposee"], pieces, p,
            [("chevauchement", ["U3", "T1"], "")])

    # 052 — obstacle. Obstacle de placement dessus [100 ; 200] × [100 ; 300] mil : U1 dessus (180,200) le recouvre ;
    # U2 dessous (150,200) non (face). Trou T1 (500,200) Ø 1,0 non métallisé, rayon interdit 1,1 mm = 43,307 mil :
    # U3 (560,200), bord gauche à 40 mil = 1,016 mm < 1,1 : obstacle. F1 fixe (pose d'entrée) en (350,320), zone
    # interdite « placement » locale [−30 ; 30] × [−100 ; −40] mil (monde [320 ; 380] × [220 ; 280]) : U5 (350,215),
    # boîte y ∈ [195 ; 235], la recouvre : obstacle [U5, F1] (sans chevauchement : 300 − 9,843 > 235 + 9,843).
    f1 = mob("F1", 350, 320, fixe=True)
    f1["zones_interdites"] = [dict(zone_interdite("F1/K0", [T], rect_mil(-30, -100, 30, -40), ["placement"]),
                                   source="keepout")]
    pieces = [mob("U1", 100, 330), mob("U2", 700, 330), mob("U3", 700, 100), mob("U5", 600, 330), f1]
    p = placements_p([pl("U1", 180, 200), pl("U2", 150, 200, 0.0, True), pl("U3", 560, 200), pl("U5", 350, 215),
                      pl("F1", 350, 320, statut="fixe")])

    def obstacles(d):
        d["obstacles_placement"] = [{"faces": ["dessus"], "rect": [mm(100), mm(100), mm(200), mm(300)],
                                     "etiquette": "corpus"}]
        d["trous"] = [trou_carte("T1", 500, 200, 1.0, False)]
    ajouter("052_obstacle", "Obstacle de placement de la face, rayon interdit d'un trou et zone interdite « placement » "
            "d'une empreinte fixe : trois obstacles ; l'obstacle de l'autre face est sans effet.",
            ["placement:obstacle"], pieces, p,
            [("obstacle", ["U1"], ""), ("obstacle", ["U3"], ""), ("obstacle", ["U5", "F1"], "")], modif=obstacles)

    # 053 — marge_bord, carte en L (contour (0,0) (800,0) (800,400) (400,400) (400,250) (0,250)). U1 (65,100) :
    # bord gauche de la boîte élargie à 45 − 9,843 = 35,157 mil = 0,893 mm < 1,0. U2 (70,170) : 40,157 mil =
    # 1,020 mm, valide. U3 (300,215) : haut élargi 244,843, à 5,157 mil de l'arête rentrante y = 250 (hors de la
    # boîte de la carte : seul le contour exact le voit). U4 (480,330) dans la partie haute : 50,157 mil de x = 400,
    # 40,157 mil de y = 400 : valide. U5 (200,330) : dans l'encoche, hors du contour.
    pieces = [mob(n, 700, y) for n, y in (("U1", 60), ("U2", 120), ("U3", 180))] + \
             [mob("U4", 600, 120), mob("U5", 600, 180)]
    p = placements_p([pl("U1", 65, 100), pl("U2", 70, 170), pl("U3", 300, 215), pl("U4", 480, 330),
                      pl("U5", 200, 330)])

    def carte_l(d):
        d["carte"]["contour"] = pts_mm([[0, 0], [800, 0], [800, 400], [400, 400], [400, 250], [0, 250]])
    ajouter("053_marge_bord_contour", "Carte en L : marge au contour exact (bord, arête rentrante, pièce hors du "
            "contour) ; deux pièces juste au-delà de la marge.",
            ["placement:marge_bord", "geometrie:contour_polygonal"], pieces, p,
            [("marge_bord", ["U1"], ""), ("marge_bord", ["U3"], ""), ("marge_bord", ["U5"], "")], modif=carte_l)

    # 054 — marge_decoupe au polygone : découpe triangulaire D1 (380,150) (420,150) (380,250). U1 (330,200) : bord
    # droit 350, à 30 mil = 0,762 mm de l'arête x = 380 : violation. U2 (455,250) : coin (435,230) à
    # (15 × 100 + 80 × 40) / √(40² + 100²) = 4700 / 107,703 = 43,638 mil = 1,108 mm de l'hypoténuse : valide (la
    # boîte de la découpe, x ≤ 420, serait à 15 mil).
    pieces = [mob("U1", 100, 100), mob("U2", 100, 300)]
    p = placements_p([pl("U1", 330, 200), pl("U2", 455, 250)])

    def decoupe(d):
        d["carte"]["decoupes"] = [{"id": "D1", "points": pts_mm([[380, 150], [420, 150], [380, 250]]),
                                   "proprietaire": None}]
    ajouter("054_marge_decoupe_polygone", "Marge de placement au polygone exact d'une découpe triangulaire : une "
            "pièce trop proche, une pièce valide près de l'hypoténuse (fautive avec la seule boîte).",
            ["placement:marge_decoupe"], pieces, p, [("marge_decoupe", ["U1"], "")], modif=decoupe)

    # 055 — éloignement dur : objets U1#0 (pastille) et U2 (centre de boîte), référence M1 (fixe, (100,200)),
    # 10 mm. U1 (400,200) : 300 mil = 7,62 mm < 10 : violation [U1] ; U2 (600,200) : 500 mil = 12,7 mm.
    pieces = [mob("M1", 100, 200, fixe=True), mob("U1", 700, 100), mob("U2", 700, 300)]
    p = placements_p([pl("M1", 100, 200, statut="fixe"), pl("U1", 400, 200), pl("U2", 600, 200)])

    def dur(d):
        d["eloignements"] = [{"type": "dure", "objets": ["U1#0", "U2"], "reference": ["M1"], "distance_mm": 10.0,
                              "poids_par_mm": 300, "marge_mm": 1.0}]
    ajouter("055_eloignement_dur", "Éloignement dur d'une pastille (7,62 mm < 10) et d'une pièce (12,7 mm) à une "
            "pièce de référence : une violation, deux informations.",
            ["placement:eloignement"], pieces, p, [("eloignement", ["U1"], "")],
            [("U1#0", "M1", 7.62, 10.0), ("U2", "M1", 12.7, 10.0)], modif=dur)

    # 056 — pastille_pastille, matrice [[0,2 ; 0,1] ; [0,1 ; 0,1]]. Boîtes locales réduites à ±10 mil (sous la
    # pastille) : aucune boîte ne se recouvre. U1 (200,200), U2 (245,200) classe 0 : pastilles à 5 mil = 0,127 mm
    # < 0,2 : violation. U3 (500,200), U4 (545,200) classe 1 : 0,127 ≥ 0,1 (règle qui relâche) : valide. V1 (700,100)
    # et V2 (745,100), fixes à leur pose d'entrée, à 0,127 mm : paire exclue (dessin d'origine).
    b10 = [-10, -10, 10, 10]
    pieces = [mob("U1", 100, 300, b10), mob("U2", 200, 300, b10), mob("U3", 300, 300, b10, classe=1),
              mob("U4", 400, 300, b10, classe=1), mob("V1", 700, 100, b10, fixe=True),
              mob("V2", 745, 100, b10, fixe=True)]
    p = placements_p([pl("U1", 200, 200), pl("U2", 245, 200), pl("U3", 500, 200), pl("U4", 545, 200),
                      pl("V1", 700, 100, statut="fixe"), pl("V2", 745, 100, statut="fixe")])
    regles = {"isolement": {"defaut": 0.2, "classes": ["defaut", "fin"], "matrice": [[0.2, 0.1], [0.1, 0.1]],
                            "classe_reference": 0}}
    ajouter("056_pastille_pastille", "Écart entre pastilles de pièces différentes par la matrice des classes : "
            "0,127 < 0,2 fautif, 0,127 ≥ 0,1 permis, paire de deux pièces fixes à leur pose d'entrée exclue.",
            ["placement:pastille_pastille", "regles:matrice", "placement:pose_entree"], pieces, p,
            [("pastille_pastille", ["U1", "U2"], "")], regles=regles)

    # 057 — zone_imposee [100 ; 400] × [100 ; 300] mil, face dessus, pièces U1, U2, U3. U1 (200,200) dedans ; U2
    # (390,200) déborde (410 > 400) ; U3 dessous (200,250) : face hors de la zone.
    pieces = [mob("U1", 600, 100), mob("U2", 600, 300), mob("U3", 700, 200)]
    p = placements_p([pl("U1", 200, 200), pl("U2", 390, 200), pl("U3", 200, 250, 0.0, True)])

    def imposee(d):
        d["zones_imposees"] = [{"pieces": ["U1", "U2", "U3"], "faces": ["dessus"],
                                "polygone": rect_mil(100, 100, 400, 300), "etiquette": "corpus",
                                "source": "contrainte"}]
    ajouter("057_zone_imposee", "Zone imposée (face dessus) : une pièce dedans, une qui déborde, une sur l'autre "
            "face.", ["placement:zone_imposee"], pieces, p,
            [("zone_imposee", ["U2"], ""), ("zone_imposee", ["U3"], "")], modif=imposee)

    # 058 — au_bord. U1 gauche (20,200) : bord gauche 0 = côté de la carte ; dispensée de marge_bord à gauche.
    # U2 haut, débord 0,254 mm (10 mil) : (400,390), haut 410 = 400 + 10. U3 quelconque (780,100) : droite 800.
    # U4 droite (700,300) : bord droit 720 ≠ 800 : au_bord. U5 (20,300) sans au_bord : marge_bord.
    pieces = [mob("U1", 200, 100), mob("U2", 300, 100), mob("U3", 400, 100), mob("U4", 500, 100),
              mob("U5", 600, 100)]
    p = placements_p([pl("U1", 20, 200), pl("U2", 400, 390), pl("U3", 780, 100), pl("U4", 700, 300),
                      pl("U5", 20, 300)])

    def bord(d):
        d["au_bord"] = [{"piece": "U1", "cote": "gauche", "debord_mm": 0.0},
                        {"piece": "U2", "cote": "haut", "debord_mm": 0.254},
                        {"piece": "U3", "cote": "quelconque", "debord_mm": 0.0},
                        {"piece": "U4", "cote": "droite", "debord_mm": 0.0}]
    ajouter("058_au_bord", "Pièces au bord (côté imposé, débord, côté quelconque) dispensées de marge de ce côté ; "
            "une pièce loin de son côté ; une pièce au bord sans contrainte.",
            ["placement:au_bord", "placement:marge_bord"], pieces, p,
            [("marge_bord", ["U5"], ""), ("au_bord", ["U4"], "")], modif=bord)

    # 059 — groupe_rigide. G1 (U1, U2) à l'entrée (200,100), (260,100) ; posé en (400,200) et (340,200), tous deux
    # tournés de 180° : U2 vue de U1 = R(−180°)(−60, 0) = (60, 0), comme à l'entrée : valide. G2 (U3, U4) :
    # (200,300), (260,300) → (200,300), (270,300) : (70, 0) ≠ (60, 0) : violation. G3 (U5, U6), non rigide : libre.
    pieces = [mob("U1", 200, 100), mob("U2", 260, 100), mob("U3", 200, 300), mob("U4", 260, 300),
              mob("U5", 500, 100), mob("U6", 560, 100)]
    p = placements_p([pl("U1", 400, 200, 180.0), pl("U2", 340, 200, 180.0), pl("U3", 200, 300), pl("U4", 270, 300),
                      pl("U5", 500, 100), pl("U6", 600, 150)])

    def groupes(d):
        d["groupes"] = [{"type": "impose", "nom": "G1", "pieces": ["U1", "U2"], "rigide": True},
                        {"type": "impose", "nom": "G2", "pieces": ["U3", "U4"], "rigide": True},
                        {"type": "impose", "nom": "G3", "pieces": ["U5", "U6"], "rigide": False}]
    ajouter("059_groupe_rigide", "Groupe rigide déplacé d'un bloc (translation et demi-tour) : valide ; groupe "
            "rigide déformé : violation ; groupe non rigide : libre.",
            ["placement:groupe_rigide"], pieces, p, [("groupe_rigide", ["U3", "U4"], "")], modif=groupes)

    # 060 — placement v1 (liste nue, sans les pièces fixes inchangées) : F1 fixe absente (permis en v1), U1 posée,
    # U2 mobile absente : absente ; X9 inconnue des données : inconnue.
    pieces = [mob("F1", 600, 300, fixe=True), mob("U1", 100, 100), mob("U2", 300, 300)]
    p = [{"designator": "U1", "side": "top", "x": 200.0, "y": 200.0, "rotation": 0.0},
         {"designator": "X9", "side": "top", "x": 400.0, "y": 100.0, "rotation": 0.0}]
    ajouter("060_placement_v1", "Placement v1 en liste nue : pièce fixe absente permise, pièce mobile absente, "
            "pièce inconnue.", ["placement:v1", "placement:absente", "placement:inconnue"], pieces, p,
            [("absente", ["U2"], ""), ("inconnue", ["X9"], "")])

    # 061 et 062 — contrôles de pose. F1 fixe (100,100) posée en (105,100) : fixe_deplacee (5 mil = 0,127 mm). U1
    # tournée de 45° : orientation. U2 en (302,200) : 302 mil n'est pas un multiple de 5 mil : grille (061 ; en 062,
    # contrôle sans grille, aucune). U3 absente du placement v2 : absente (laissée à sa pose cible (500,300)). X9 :
    # inconnue.
    pieces = [mob("F1", 100, 100, fixe=True), mob("U1", 600, 100), mob("U2", 600, 300), mob("U3", 500, 300)]
    p = placements_p([pl("F1", 105, 100, statut="fixe"), pl("U1", 200, 300, 45.0), pl("U2", 302, 200),
                      pl("X9", 700, 200)])
    ajouter("061_controles_de_pose", "Pièce fixe déplacée, orientation non permise, origine hors grille, pièce "
            "absente, pièce inconnue.",
            ["placement:fixe_deplacee", "placement:orientation", "placement:grille", "placement:absente",
             "placement:inconnue"], pieces, p,
            [("fixe_deplacee", ["F1"], ""), ("orientation", ["U1"], ""), ("grille", ["U2"], ""),
             ("absente", ["U3"], ""), ("inconnue", ["X9"], "")])
    ajouter("062_controles_sans_grille", "Mêmes poses que 061, contrôle sans grille (--sans-grille, référence "
            "manuelle) : pas de violation de grille.",
            ["placement:fixe_deplacee", "placement:sans_grille"], pieces, p,
            [("fixe_deplacee", ["F1"], ""), ("orientation", ["U1"], ""), ("absente", ["U3"], ""),
             ("inconnue", ["X9"], "")])
    nom, c, *reste = L[-1]
    c["grille"] = False
    return L


FICHIERS = ("cas.json", "donnees_placeur.json", "placements_routage.json", "routage_detaille.json",
            "attendu_strict.json", "attendu_altium.json", "attendu_mesures.json")


def texte_json(d):
    return json.dumps(d, ensure_ascii=False, indent=1) + "\n"


def ecrire(dossier):
    """Écrit tous les cas sous ``dossier`` ; rend la liste des noms de cas."""
    noms = []
    for nom, *docs in liste_cas():
        rep = os.path.join(dossier, nom)
        os.makedirs(rep, exist_ok=True)
        for f, d in zip(FICHIERS, docs):
            with open(os.path.join(rep, f), "wb") as fh:
                fh.write(texte_json(d).encode("utf-8"))
        noms.append(nom)
    return noms


def _sortie_utf8():
    for flux in (sys.stdout, sys.stderr):
        if not flux.isatty() and hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    _sortie_utf8()
    ap = argparse.ArgumentParser(description="Cas de validité du corpus (§7.4), écrits de façon déterministe.")
    ap.add_argument("--sortie", required=True, help="dossier de sortie (créé au besoin)")
    a = ap.parse_args(argv)
    if os.path.exists(a.sortie) and not os.path.isdir(a.sortie):
        print("erreur : --sortie désigne un fichier existant, un dossier est attendu", file=sys.stderr)
        return 2
    try:
        noms = ecrire(a.sortie)
    except OSError as e:
        # Entrée invalide (§1.4, code 2) : dossier impossible à créer ou à écrire ; message sans chemin absolu.
        print(f"erreur : écriture impossible sous --sortie ({e.strerror or type(e).__name__})", file=sys.stderr)
        return 2
    print(f"{len(noms)} cas écrits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
