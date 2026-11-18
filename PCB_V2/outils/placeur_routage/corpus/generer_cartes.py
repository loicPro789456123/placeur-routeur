#!/usr/bin/env python3
"""Cartes synthétiques KiCad du corpus (contrat §7.3, lot E) : générateur déterministe.

    python generer_cartes.py --sortie DOSSIER

Écrit ``DOSSIER/<nom>/`` pour chaque carte : ``<nom>.kicad_pcb`` (KiCad 8, ``(version 20240108)``),
``<nom>.kicad_pro`` (classes et règles, annexe D), ``contraintes.json`` (contraintes/2) et
``attendu_deduction.json`` (attendu_deduction/1, décrit dans ``LISEZMOI.md``). Deux exécutions donnent des fichiers
identiques octet pour octet (UTF-8, LF ; identifiants uuid5 tirés du nom de la carte et de l'objet).

Les attendus sont ÉCRITS À LA MAIN dans ``CARTES`` ci-dessous d'après les règles de déduction du contrat (§3) ; le
commentaire de chaque carte donne la règle appliquée. Les empreintes sont paramétriques (``EMPREINTES``), dans le
repère local de KiCad (mm, y vers le bas). Pièces posées dessous : couches ``B.*``, coordonnées locales des
pastilles en miroir (y → −y, convention de retournement de KiCad), textes ``(justify mirror)`` ; seules des pièces à
deux pastilles symétriques y sont posées à ce jalon.

Cartes de ce jalon (E1) : ``deux_couches``, ``quatre_couches`` (chaîne), ``deux_convertisseurs``, ``sans_classes``
(déduction) ; les autres cartes de §7.3 viennent à l'étape 8.
"""

import argparse
import json
import os
import sys
import uuid

VERSION_KICAD = 20240108
ESPACE_UUID = uuid.UUID("6d1f1c7e-3b51-5c1e-9a3e-0c0a9b1e4e21")
FORMAT_ATTENDU = "attendu_deduction/1"
NOMS_COUCHES = {"F.Cu": "TopLayer", "B.Cu": "BottomLayer"}


def nom_couche(kicad):
    """Nom de §0.4 d'une couche de cuivre KiCad (F.Cu → TopLayer, In<n>.Cu → MidLayer<n>, B.Cu → BottomLayer)."""
    return NOMS_COUCHES.get(kicad) or "MidLayer" + kicad[2:-3]


def n(v):
    """Nombre écrit à la façon de KiCad : au plus 6 décimales, sans zéro final ni « -0 »."""
    t = f"{round(float(v), 6):.6f}".rstrip("0").rstrip(".")
    return "0" if t in ("-0", "") else t


def q(t):
    return '"' + str(t).replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- Empreintes paramétriques (repère local KiCad, mm) ---------------------------------------------------------------
# pastille : (nom, type, forme, x, y, largeur, hauteur, perçage) ; type smd | thru_hole | np_thru_hole.

def _deux(px, w, h, forme="roundrect"):
    return [("1", "smd", forme, -px, 0.0, w, h, None), ("2", "smd", forme, px, 0.0, w, h, None)]


def _sot23_6():
    xs = (-1.1375, -1.1375, -1.1375, 1.1375, 1.1375, 1.1375)
    ys = (-0.95, 0.0, 0.95, 0.95, 0.0, -0.95)
    return [(str(i + 1), "smd", "roundrect", xs[i], ys[i], 1.325, 0.6, None) for i in range(6)]


def _soic8():
    ys = (-1.905, -0.635, 0.635, 1.905)
    g = [(str(i + 1), "smd", "roundrect", -2.475, ys[i], 1.95, 0.6, None) for i in range(4)]
    return g + [(str(i + 5), "smd", "roundrect", 2.475, ys[3 - i], 1.95, 0.6, None) for i in range(4)]


def _qfn16():
    p, c = (-0.75, -0.25, 0.25, 0.75), 1.45
    L = [(str(i + 1), "smd", "roundrect", -c, p[i], 0.8, 0.25, None) for i in range(4)]
    L += [(str(i + 5), "smd", "roundrect", p[i], c, 0.25, 0.8, None) for i in range(4)]
    L += [(str(i + 9), "smd", "roundrect", c, p[3 - i], 0.8, 0.25, None) for i in range(4)]
    L += [(str(i + 13), "smd", "roundrect", p[3 - i], -c, 0.25, 0.8, None) for i in range(4)]
    return L + [("17", "smd", "rect", 0.0, 0.0, 1.7, 1.7, None)]


def _barrette(k):
    return [(str(i + 1), "thru_hole", "rect" if i == 0 else "circle", 0.0, round(2.54 * i, 6), 1.7, 1.7, 1.0)
            for i in range(k)]


EMPREINTES = {
    # nom : (description, attribut, pastilles, demi-côtés de la zone d'encombrement (x, y) ou boîte complète)
    "R_0402_1005Metric": ("Resistance CMS 0402", "smd", _deux(0.51, 0.54, 0.64), (0.93, 0.47)),
    "C_0402_1005Metric": ("Condensateur CMS 0402", "smd", _deux(0.48, 0.56, 0.62), (0.91, 0.46)),
    "R_0603_1608Metric": ("Resistance CMS 0603", "smd", _deux(0.825, 0.8, 0.95), (1.48, 0.73)),
    "C_0603_1608Metric": ("Condensateur CMS 0603", "smd", _deux(0.775, 0.9, 0.95), (1.48, 0.73)),
    "C_0805_2012Metric": ("Condensateur CMS 0805", "smd", _deux(0.95, 1.0, 1.45), (1.7, 0.98)),
    "C_1210_3225Metric": ("Condensateur CMS 1210", "smd", _deux(1.475, 1.15, 2.7), (2.28, 1.6)),
    "LED_0603_1608Metric": ("LED CMS 0603", "smd", _deux(0.7875, 0.875, 0.95), (1.48, 0.73)),
    "L_4x4": ("Inductance CMS 4 x 4 mm", "smd", _deux(1.7, 1.4, 3.4), (2.6, 2.25)),
    "SOT-23": ("Boitier SOT-23, 3 broches", "smd",
               [("1", "smd", "roundrect", -0.9375, -0.95, 1.475, 0.6, None),
                ("2", "smd", "roundrect", -0.9375, 0.95, 1.475, 0.6, None),
                ("3", "smd", "roundrect", 0.9375, 0.0, 1.475, 0.6, None)], (2.05, 1.6)),
    "SOT-23-6": ("Boitier SOT-23, 6 broches, pas 0,95 mm", "smd", _sot23_6(), (2.05, 1.75)),
    "SOIC-8_3.9x4.9mm_P1.27mm": ("Boitier SOIC-8, pas 1,27 mm", "smd", _soic8(), (3.7, 2.7)),
    "QFN-16_3x3mm_P0.5mm": ("Boitier QFN-16 3 x 3 mm, pas 0,5 mm, pastille exposee", "smd", _qfn16(), (2.1, 2.1)),
    "PinHeader_1x02_P2.54mm": ("Barrette 1 x 2, pas 2,54 mm", "through_hole", _barrette(2),
                               (-1.8, -1.8, 1.8, 4.34)),
    "PinHeader_1x03_P2.54mm": ("Barrette 1 x 3, pas 2,54 mm", "through_hole", _barrette(3),
                               (-1.8, -1.8, 1.8, 6.88)),
    "PinHeader_1x04_P2.54mm": ("Barrette 1 x 4, pas 2,54 mm", "through_hole", _barrette(4),
                               (-1.8, -1.8, 1.8, 9.42)),
    "PinHeader_1x05_P2.54mm": ("Barrette 1 x 5, pas 2,54 mm", "through_hole", _barrette(5),
                               (-1.8, -1.8, 1.8, 11.96)),
    "PinHeader_1x07_P2.54mm": ("Barrette 1 x 7, pas 2,54 mm", "through_hole", _barrette(7),
                               (-1.8, -1.8, 1.8, 17.04)),
    "MountingHole_3.2mm_M3": ("Trou de fixation M3, non metallise", "exclude_from_pos_files exclude_from_bom",
                              [("", "np_thru_hole", "circle", 0.0, 0.0, 3.2, 3.2, 3.2)], (3.45, 3.45)),
}


# --- Cartes : pièces, classes, attendus écrits à la main --------------------------------------------------------------
# pièce : (désignateur, empreinte, valeur, x, y, rotation, face, {pastille: réseau}) ; coordonnées de la page KiCad
# (mm, y vers le bas) ; la carte est le rectangle ``contour`` de la page.

def _p(des, fp, val, x, y, rot=0, face="dessus", **nets):
    return (des, fp, val, x, y, rot, face, {k.lstrip("_"): v for k, v in nets.items()})


def _r(des, x, y, a, b, rot=0, val="10k", fp="R_0603_1608Metric", face="dessus"):
    return _p(des, fp, val, x, y, rot, face, _1=a, _2=b)


def _c(des, x, y, a, b, rot=0, val="100n", fp="C_0603_1608Metric", face="dessus"):
    return _p(des, fp, val, x, y, rot, face, _1=a, _2=b)


def _h(des, x, y):
    return _p(des, "MountingHole_3.2mm_M3", "Trou M3", x, y)


def _barre(des, k, x, y, nets, val="Barrette"):
    return _p(des, f"PinHeader_1x{k:02d}_P2.54mm", val, x, y, **{f"_{i + 1}": r for i, r in enumerate(nets)})


CLASSE_DEFAUT = {"name": "Default", "clearance": 0.2, "track_width": 0.25, "via_diameter": 0.6, "via_drill": 0.3}
REGLES_KICAD = {"min_clearance": 0.15, "min_track_width": 0.2, "min_via_diameter": 0.5,
                "min_through_hole_diameter": 0.3, "min_hole_to_hole": 0.25, "min_copper_edge_clearance": 0.5}

CARTES = []

# deux_couches (chaîne) — 2 couches, 30 pièces CMS et traversantes, sans plan. Classe « Alim » (piste 0,5 mm,
# isolement 0,25) pour +5V, +3V3 et GND par motifs exacts. Attendus : R-C4 (deux couches externes en routage) ;
# R-M2 (aucun plan ; GND touche 8 condensateurs, +3V3 5) ; R-R6 (r = 0,5 / 0,25 = 2, gamme large : max null) pour
# GND, +5V, +3V3 ; R-R7 sinon ; aucun convertisseur (U2 linéaire, aucune inductance) ; aucun pas fin (pas minimal
# 0,95 mm sur 3 pastilles et plus) ; R-P3 : J1 contrainte (« fixes »), J2 suggestion (pastilles à plus de
# 2 × garde_bord du bord), H1 à H4 mécaniques ; groupes fonctionnels (§3.6) de U1 et de U3 (U2 n'a que des réseaux
# d'alimentation) ; isolement : défaut max(0,15 ; 0,2) = 0,2, matrice max(c_i, c_j, 0,15).
CARTES.append({
    "nom": "deux_couches", "famille": "chaine", "contour": (100.0, 100.0, 150.0, 140.0),
    "couches": ("F.Cu", "B.Cu"), "types": {}, "zones": [],
    "classes": [CLASSE_DEFAUT, {"name": "Alim", "clearance": 0.25, "track_width": 0.5, "via_diameter": 0.8,
                                "via_drill": 0.4}],
    "motifs": [("Alim", "+5V"), ("Alim", "+3V3"), ("Alim", "GND")], "affectations": None,
    "pieces": [
        _h("H1", 103.5, 103.5), _h("H2", 146.5, 103.5), _h("H3", 103.5, 136.5), _h("H4", 146.5, 136.5),
        _barre("J1", 2, 106.0, 118.0, ["+5V", "GND"]),
        _barre("J2", 7, 144.0, 113.0, ["SCL_J", "SDA_J", "TX", "RX_J", "IN_J", "SENS_J", "LOAD"]),
        _p("U2", "SOT-23", "Regulateur 3V3", 113.0, 112.0, _1="GND", _2="+3V3", _3="+5V"),
        _c("C2", 113.0, 117.0, "+5V", "GND", val="1u", fp="C_0805_2012Metric"),
        _c("C5", 113.0, 107.0, "+3V3", "GND", val="1u", fp="C_0805_2012Metric"),
        _p("U1", "SOIC-8_3.9x4.9mm_P1.27mm", "Microcontroleur", 126.0, 113.0, _1="+3V3", _2="RST", _3="LED1",
           _4="GND", _5="SCL", _6="SDA", _7="TX", _8="RX"),
        _c("C1", 126.0, 108.5, "+3V3", "GND"),
        _r("R1", 119.0, 112.0, "+3V3", "RST", rot=90),
        _c("C3", 119.0, 116.0, "RST", "GND", rot=90),
        _r("R3", 122.0, 120.0, "LED1", "D1_A", val="330"),
        _p("D1", "LED_0603_1608Metric", "Rouge", 122.0, 123.0, _1="GND", _2="D1_A"),
        _r("R2", 130.5, 108.5, "+3V3", "SCL", val="4k7"),
        _r("R5", 134.0, 111.0, "SCL", "SCL_J", val="33"),
        _r("R6", 134.0, 114.0, "SDA", "SDA_J", val="33"),
        _r("R7", 134.0, 117.0, "RX", "RX_J", val="33"),
        _p("U3", "SOIC-8_3.9x4.9mm_P1.27mm", "Comparateur", 127.0, 130.0, _1="+3V3", _2="IN_A", _3="OUT_B",
           _4="GND", _6="SENS", _7="+3V3", _8="GND"),
        _c("C4", 127.0, 125.5, "+3V3", "GND"),
        _r("R8", 136.0, 125.0, "IN_J", "IN_A", val="1k"),
        _r("R10", 136.0, 128.0, "SENS_J", "SENS", val="1k"),
        _r("R9", 135.0, 132.0, "OUT_B", "Q_G", val="100"),
        _p("Q1", "SOT-23", "MOSFET N", 139.0, 132.0, _1="Q_G", _2="GND", _3="LOAD"),
        _c("C6", 120.0, 130.0, "SENS", "GND", rot=90, val="10n"),
        _r("R4", 108.0, 126.0, "+5V", "D2_A", val="1k"),
        _p("D2", "LED_0603_1608Metric", "Verte", 108.0, 129.0, _1="GND", _2="D2_A"),
        _c("C7", 126.0, 113.0, "+3V3", "GND", face="dessous"),
        _c("C8", 127.0, 130.0, "+3V3", "GND", face="dessous"),
    ],
    "contraintes": {"format": "contraintes/2",
                    "_doc": "Carte synthétique deux_couches : J1 fixé par contrainte, le reste déduit.",
                    "fixes": ["J1"]},
    "attendu": {
        "description": "2 couches de routage, sans plan, 30 pièces ; classe Alim pour +5V, +3V3 et GND.",
        "regles": ["R-C4", "R-M2", "R-R6", "R-R7", "R-P2", "R-P3", "§3.6 fonctionnel", "annexe D"],
        "couches": [{"nom": "TopLayer", "role": "routage", "plans": []},
                    {"nom": "BottomLayer", "role": "routage", "plans": []}],
        "masses": ["GND"],
        "roles": {"alimentation": ["+3V3", "+5V", "GND"],
                  "signal": ["D1_A", "D2_A", "IN_A", "IN_J", "LED1", "LOAD", "OUT_B", "Q_G", "RST", "RX", "RX_J",
                             "SCL", "SCL_J", "SDA", "SDA_J", "SENS", "SENS_J", "TX"]},
        "largeurs": {"+5V": [0.2, 0.5, None], "RST": [0.2, 0.25, None]},
        "convertisseurs": [], "sans_piste_dessous": [], "pas_fin": [], "matrice": [],
        "isolement": {"defaut": 0.2, "requis": [{"reseaux": ["RST", "SCL"], "mm": 0.2},
                                                {"reseaux": ["+5V", "GND"], "mm": 0.25},
                                                {"reseaux": ["+3V3", "RST"], "mm": 0.25}]},
        "pieces_fixes": {"H1": "mecanique", "H2": "mecanique", "H3": "mecanique", "H4": "mecanique",
                         "J1": "contrainte", "J2": "suggestion"},
        "groupes": [{"type": "fonctionnel", "pieces": ["C3", "R1", "R2", "R3", "R5", "R6", "R7", "U1"]},
                    {"type": "fonctionnel", "pieces": ["C6", "R10", "R8", "R9", "U3"]}],
        "chaine": {"niveau": "rapide", "graines": 10, "graines_valides_min": 1,
                   "couches_pistes": ["TopLayer", "BottomLayer"],
                   "couches_sans_piste": [], "descentes_par_vias": []}},
})

# quatre_couches (chaîne) — In1.Cu (type power) couvert par une zone GND, In2.Cu (type power) par une zone +3V3
# (contour moins 0,5 mm : couverture 0,9425 ≥ 0,8 ; aucune piste libre). Attendus : R-C3 (indice d'intention : type
# power) ⇒ MidLayer1 plan GND, MidLayer2 plan +3V3 ; R-C4 ; R-R3 (GND et +3V3 en plan) ; R-M2 (deux plans ; GND
# touche 5 condensateurs, +3V3 3) ; R-P2 : U1 (QFN, pas 0,5) ; R-P3 : J1, J2, J3 suggestions, H1 et H2 mécaniques ;
# groupes fonctionnels de U1 et de U2 (GND et +3V3, plans, exclus des liens).
CARTES.append({
    "nom": "quatre_couches", "famille": "chaine", "contour": (100.0, 100.0, 140.0, 130.0),
    "couches": ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"), "types": {"In1.Cu": "power", "In2.Cu": "power"},
    "zones": [("GND", "In1.Cu"), ("+3V3", "In2.Cu")],
    "classes": [CLASSE_DEFAUT], "motifs": [], "affectations": None,
    "pieces": [
        _h("H1", 103.5, 103.5), _h("H2", 136.5, 126.5),
        _barre("J1", 2, 104.0, 113.0, ["+3V3", "GND"]),
        _barre("J2", 4, 136.0, 106.0, ["SCL", "SDA", "INT", "SENS_O"]),
        _barre("J3", 2, 104.0, 121.0, ["SENS_IN", "GND"]),
        _p("U1", "QFN-16_3x3mm_P0.5mm", "Microcontroleur", 120.0, 110.0, _1="SCL", _2="SDA", _3="INT", _4="GND",
           _5="+3V3", _6="RST", _7="GND", _8="LED", _10="GND", _11="+3V3", _15="GND", _16="+3V3", _17="GND"),
        _c("C1", 115.5, 110.0, "+3V3", "GND", rot=90, fp="C_0402_1005Metric"),
        _c("C2", 120.0, 105.5, "+3V3", "GND", fp="C_0402_1005Metric"),
        _r("R1", 125.0, 106.5, "+3V3", "SCL", val="4k7", fp="R_0402_1005Metric"),
        _r("R2", 125.0, 108.0, "+3V3", "SDA", val="4k7", fp="R_0402_1005Metric"),
        _r("R7", 125.0, 109.5, "+3V3", "INT", val="47k", fp="R_0402_1005Metric"),
        _c("C3", 116.0, 114.5, "RST", "GND", fp="C_0402_1005Metric"),
        _r("R8", 116.0, 116.0, "+3V3", "RST", fp="R_0402_1005Metric"),
        _r("R9", 121.0, 115.5, "LED", "LED_A", val="330", fp="R_0402_1005Metric"),
        _p("D1", "LED_0603_1608Metric", "Verte", 121.0, 118.0, _1="GND", _2="LED_A"),
        _p("U2", "SOIC-8_3.9x4.9mm_P1.27mm", "Amplificateur", 122.0, 124.0, _1="SENS_O", _2="FB_A", _3="SENS_P",
           _4="GND", _8="+3V3"),
        _c("C4", 127.5, 122.0, "+3V3", "GND", fp="C_0402_1005Metric"),
        _r("R3", 128.0, 124.5, "FB_A", "SENS_O", val="22k", fp="R_0402_1005Metric"),
        _r("R4", 128.0, 126.0, "FB_A", "GND", val="10k", fp="R_0402_1005Metric"),
        _c("C5", 112.0, 124.0, "SENS_P", "GND", val="10n", fp="C_0402_1005Metric"),
        _r("R5", 109.0, 122.0, "SENS_IN", "SENS_P", val="1k", fp="R_0402_1005Metric"),
    ],
    "contraintes": {"format": "contraintes/2",
                    "_doc": "Carte synthétique quatre_couches : tout est déduit (seuil de pas fin par défaut).",
                    "pas_fin_mm": 0.65},
    "attendu": {
        "description": "4 couches : plans GND (In1) et +3V3 (In2) couvrants de type power, pastilles de plan.",
        "regles": ["R-C3", "R-C4", "R-M2", "R-R3", "R-R7", "R-P2", "R-P3", "§3.6 fonctionnel"],
        "couches": [{"nom": "TopLayer", "role": "routage", "plans": []},
                    {"nom": "MidLayer1", "role": "plan", "plans": ["GND"]},
                    {"nom": "MidLayer2", "role": "plan", "plans": ["+3V3"]},
                    {"nom": "BottomLayer", "role": "routage", "plans": []}],
        "masses": ["GND"],
        "roles": {"plan": ["+3V3", "GND"],
                  "signal": ["FB_A", "INT", "LED", "LED_A", "RST", "SCL", "SDA", "SENS_IN", "SENS_O", "SENS_P"]},
        "largeurs": {"SCL": [0.2, 0.25, None]},
        "convertisseurs": [], "sans_piste_dessous": [], "pas_fin": ["U1"], "matrice": [],
        "isolement": {"defaut": 0.2, "requis": [{"reseaux": ["SCL", "SDA"], "mm": 0.2}]},
        "pieces_fixes": {"H1": "mecanique", "H2": "mecanique", "J1": "suggestion", "J2": "suggestion",
                         "J3": "suggestion"},
        "groupes": [{"type": "fonctionnel", "pieces": ["C3", "R1", "R2", "R7", "R8", "R9", "U1"]},
                    {"type": "fonctionnel", "pieces": ["C5", "R3", "R4", "R5", "U2"]}],
        "chaine": {"niveau": "rapide", "graines": 10, "graines_valides_min": 1,
                   "couches_pistes": ["TopLayer", "BottomLayer"],
                   "couches_sans_piste": ["MidLayer1", "MidLayer2"], "descentes_par_vias": ["+3V3", "GND"]}},
})

# deux_convertisseurs (déduction) — demi-pont synchrone discret (R-V1 : Q1 haut vers +12V, Q2 bas vers GND, nœud
# SW1, L1 vers +5V, grilles commandées par U1, HO à travers R1 ; abaisseur : +5V n'alimente pas U1, alimenté par
# VDRV) et abaisseur à interrupteur intégré (R-V3 : U2, nœud SW2, L2 vers +3V3 ; rail = réseau de U2 qui porte le
# plus de condensateurs vers la masse = +5V (C5, C8) ; C6 amorçage). Piège : la broche HS de U1 touche aussi L1 et
# satisferait R-V3 ; le nœud SW1 est celui d'un demi-pont (R-V1), une seule entrée par reseau_commutation.
# Classes : Puissance (piste 1,0, isolement 0,25 ; +12V, +5V, GND par affectation, SW1 et SW2 par le motif « SW* »),
# Alim (piste 0,5 ; +3V3). Rôles : R-R4 (SW1, SW2 en commutation, avant R-R5), R-R5 (r = 4) puissance, R-R6 (r = 2)
# alimentation, R-R7 signal. R-V5 : L1 et L2 (2 réseaux, pastilles de 4,76 mm² chacune). Groupes fonctionnels :
# SW1 et SW2 (commutation) ne sont pas exclus des liens (§3.6), d'où L1 dans le groupe de U1 et L2 dans celui de U2.
CARTES.append({
    "nom": "deux_convertisseurs", "famille": "deduction", "contour": (100.0, 100.0, 150.0, 140.0),
    "couches": ("F.Cu", "B.Cu"), "types": {}, "zones": [],
    "classes": [CLASSE_DEFAUT, {"name": "Puissance", "clearance": 0.25, "track_width": 1.0, "via_diameter": 0.8,
                                "via_drill": 0.4},
                {"name": "Alim", "clearance": 0.2, "track_width": 0.5, "via_diameter": 0.6, "via_drill": 0.3}],
    "motifs": [("Puissance", "SW*"), ("Alim", "+3V3")],
    "affectations": {"+12V": "Puissance", "+5V": "Puissance", "GND": "Puissance"},
    "pieces": [
        _barre("J1", 5, 105.0, 110.0, ["+12V", "GND", "VDRV", "PWMH", "PWML"]),
        _p("U1", "SOIC-8_3.9x4.9mm_P1.27mm", "Pilote demi-pont", 118.0, 112.0, _1="VDRV", _2="PWMH", _3="PWML",
           _4="GND", _5="GL", _6="SW1", _7="GH_U", _8="HB"),
        _c("C4", 113.0, 108.0, "VDRV", "GND", val="1u"),
        _c("C3", 118.0, 107.0, "HB", "SW1", val="100n"),
        _r("R1", 124.0, 108.0, "GH_U", "GH", val="4R7"),
        _p("Q1", "SOT-23", "MOSFET N", 129.0, 110.0, _1="GH", _2="SW1", _3="+12V"),
        _p("Q2", "SOT-23", "MOSFET N", 129.0, 116.0, _1="GL", _2="GND", _3="SW1"),
        _p("L1", "L_4x4", "4.7uH", 136.0, 113.0, 90, _1="SW1", _2="+5V"),
        _c("C1", 122.0, 104.0, "+12V", "GND", val="22u", fp="C_1210_3225Metric"),
        _c("C2", 128.0, 104.0, "+12V", "GND", val="1u", fp="C_0805_2012Metric"),
        _c("C5", 142.0, 120.0, "+5V", "GND", rot=90, val="22u", fp="C_1210_3225Metric"),
        _p("U2", "SOT-23-6", "Abaisseur integre", 128.0, 128.0, _1="BST", _2="GND", _3="FB", _4="EN", _5="+5V",
           _6="SW2"),
        _c("C6", 124.0, 126.0, "BST", "SW2", val="100n"),
        _c("C8", 132.0, 124.0, "+5V", "GND", val="4u7", fp="C_0805_2012Metric"),
        _r("R4", 124.0, 131.0, "+5V", "EN", val="100k"),
        _p("L2", "L_4x4", "2.2uH", 136.0, 130.0, 90, _1="SW2", _2="+3V3"),
        _c("C7", 142.0, 131.0, "+3V3", "GND", rot=90, val="10u", fp="C_0805_2012Metric"),
        _r("R2", 130.0, 134.0, "+3V3", "FB", val="31k6"),
        _r("R3", 126.0, 134.0, "FB", "GND", val="10k"),
        _barre("J2", 3, 146.0, 124.0, ["+5V", "+3V3", "GND"]),
    ],
    "contraintes": {"format": "contraintes/2", "_doc": "Carte synthétique deux_convertisseurs : tout est déduit."},
    "attendu": {
        "description": "Demi-pont synchrone discret (+12V → +5V) et abaisseur intégré (+5V → +3V3).",
        "regles": ["R-C4", "R-M2", "R-M3", "R-R4", "R-R5", "R-R6", "R-R7", "R-V1", "R-V3", "R-V5", "R-P3",
                   "§3.6 fonctionnel", "annexe D (motif joker)"],
        "couches": [{"nom": "TopLayer", "role": "routage", "plans": []},
                    {"nom": "BottomLayer", "role": "routage", "plans": []}],
        "masses": ["GND"],
        "roles": {"puissance": ["+12V", "+5V", "GND"], "commutation": ["SW1", "SW2"], "alimentation": ["+3V3"],
                  "signal": ["BST", "EN", "FB", "GH", "GH_U", "GL", "HB", "PWMH", "PWML", "VDRV"]},
        "largeurs": {"+12V": [0.2, 1.0, None], "+3V3": [0.2, 0.5, None], "FB": [0.2, 0.25, None]},
        "convertisseurs": [
            {"reseau_commutation": "SW1", "topologie": "abaisseur", "commutation": "synchrone", "haut": "Q1",
             "bas": "Q2", "pilote": "U1", "inductance": "L1", "reseau_entree": "+12V", "reseau_sortie": "+5V",
             "reseau_masse": "GND", "cond_entree": ["C1", "C2"], "cond_sortie": ["C5", "C8"], "cond_bulk": ["C1"],
             "cond_pilote": ["C4"], "cond_bootstrap": ["C3"],
             "boucle_chaude": {"condensateurs": ["C1", "C2"], "reseau_rail": "+12V", "reseau_masse": "GND"}},
            {"reseau_commutation": "SW2", "topologie": "abaisseur", "commutation": "integre", "haut": "U2",
             "bas": "U2", "pilote": "U2", "inductance": "L2", "reseau_entree": "+5V", "reseau_sortie": "+3V3",
             "reseau_masse": "GND", "cond_entree": ["C5", "C8"], "cond_sortie": ["C7"], "cond_bulk": ["C5"],
             "cond_bootstrap": ["C6"],
             "boucle_chaude": {"condensateurs": ["C5", "C8"], "reseau_rail": "+5V", "reseau_masse": "GND"}}],
        "sans_piste_dessous": ["L1", "L2"], "pas_fin": [], "matrice": [],
        "isolement": {"defaut": 0.2, "requis": [{"reseaux": ["GND", "SW1"], "mm": 0.25},
                                                {"reseaux": ["EN", "FB"], "mm": 0.2},
                                                {"reseaux": ["+3V3", "FB"], "mm": 0.2},
                                                {"reseaux": ["+12V", "+3V3"], "mm": 0.25}]},
        "pieces_fixes": {"J1": "suggestion", "J2": "suggestion"},
        "groupes": [{"type": "fonctionnel", "pieces": ["C3", "C4", "L1", "R1", "U1"]},
                    {"type": "fonctionnel", "pieces": ["C6", "L2", "R2", "R3", "R4", "U2"]}],
        "chaine": None},
})

# sans_classes (déduction) — seule la classe Default (piste 0,25 ; min_track_width 0,2), un abaisseur intégré
# (R-V3 : U1, nœud SW, L1 vers +5V, rail +12V = réseau de U1 qui porte le plus de condensateurs vers la masse).
# Attendus : R-R5b (réseaux sans règle de largeur propre, entrée et sortie d'un convertisseur) ⇒ +12V et +5V en
# puissance, largeurs [0,2 ; min(3 × 0,25 ; sans maximum) = 0,75 ; null] ; R-R4 SW ; R-R7 sinon (GND compris :
# aucune règle ne le classe, et R-R6b demande SupplyNets, absent de KiCad). Groupes : GND (signal) porte 9 broches,
# plus de 8 : il ne lie pas.
CARTES.append({
    "nom": "sans_classes", "famille": "deduction", "contour": (100.0, 100.0, 140.0, 130.0),
    "couches": ("F.Cu", "B.Cu"), "types": {}, "zones": [],
    "classes": [CLASSE_DEFAUT], "motifs": [], "affectations": None,
    "pieces": [
        _barre("J1", 2, 104.0, 110.0, ["+12V", "GND"]),
        _p("U1", "SOT-23-6", "Abaisseur integre", 116.0, 112.0, _1="BST", _2="GND", _3="FB", _4="EN", _5="+12V",
           _6="SW"),
        _c("C1", 110.0, 106.0, "+12V", "GND", val="10u", fp="C_1210_3225Metric"),
        _c("C2", 110.0, 112.0, "+12V", "GND", val="100n", fp="C_0805_2012Metric"),
        _c("C3", 116.0, 107.5, "BST", "SW", val="100n"),
        _r("R3", 112.0, 117.0, "+12V", "EN", val="100k"),
        _p("L1", "L_4x4", "10uH", 122.0, 112.0, 90, _1="SW", _2="+5V"),
        _c("C4", 128.0, 110.0, "+5V", "GND", rot=90, val="22u", fp="C_1210_3225Metric"),
        _r("R1", 120.0, 118.0, "+5V", "FB", val="52k3"),
        _r("R2", 116.0, 118.0, "FB", "GND", val="10k"),
        _p("U2", "SOIC-8_3.9x4.9mm_P1.27mm", "Charge logique", 128.0, 121.0, _1="+5V", _2="IN", _3="OUT", _4="GND"),
        _c("C5", 128.0, 116.0, "+5V", "GND"),
        _r("R4", 122.0, 124.0, "+5V", "IN", val="10k"),
        _barre("J2", 3, 136.0, 112.0, ["+5V", "GND", "OUT"]),
    ],
    "contraintes": {"format": "contraintes/2", "_doc": "Carte synthétique sans_classes : tout est déduit."},
    "attendu": {
        "description": "Seule la classe Default ; un abaisseur intégré +12V → +5V.",
        "regles": ["R-C4", "R-M2", "R-R4", "R-R5b", "R-R7", "R-V3", "R-V5", "R-P3", "§3.6 fonctionnel"],
        "couches": [{"nom": "TopLayer", "role": "routage", "plans": []},
                    {"nom": "BottomLayer", "role": "routage", "plans": []}],
        "masses": ["GND"],
        "roles": {"puissance": ["+12V", "+5V"], "commutation": ["SW"],
                  "signal": ["BST", "EN", "FB", "GND", "IN", "OUT"]},
        "largeurs": {"+12V": [0.2, 0.75, None], "+5V": [0.2, 0.75, None], "FB": [0.2, 0.25, None]},
        "convertisseurs": [
            {"reseau_commutation": "SW", "topologie": "abaisseur", "commutation": "integre", "haut": "U1",
             "bas": "U1", "pilote": "U1", "inductance": "L1", "reseau_entree": "+12V", "reseau_sortie": "+5V",
             "reseau_masse": "GND", "cond_entree": ["C1", "C2"], "cond_sortie": ["C4", "C5"], "cond_bulk": ["C1"],
             "cond_bootstrap": ["C3"],
             "boucle_chaude": {"condensateurs": ["C1", "C2"], "reseau_rail": "+12V", "reseau_masse": "GND"}}],
        "sans_piste_dessous": ["L1"], "pas_fin": [], "matrice": [],
        "isolement": {"defaut": 0.2, "requis": [{"reseaux": ["+12V", "SW"], "mm": 0.2}]},
        "pieces_fixes": {"J1": "suggestion", "J2": "suggestion"},
        "groupes": [{"type": "fonctionnel", "pieces": ["C3", "L1", "R1", "R2", "R3", "U1"]},
                    {"type": "fonctionnel", "pieces": ["R4", "U2"]}],
        "chaine": None},
})


# --- Écriture KiCad 8 -------------------------------------------------------------------------------------------------

class Sexpr:
    def __init__(self, carte):
        self.carte = carte

    def uuid(self, quoi):
        return q(uuid.uuid5(ESPACE_UUID, f"{self.carte}/{quoi}"))


def _effets(miroir=False):
    j = " (justify mirror)" if miroir else ""
    return f"(effects (font (size 1 1) (thickness 0.15)){j})"


def _couches_pastille(type_, dessous):
    if type_ in ("thru_hole", "np_thru_hole"):
        return '"*.Cu" "*.Mask"'
    f = "B" if dessous else "F"
    return f'"{f}.Cu" "{f}.Paste" "{f}.Mask"'


def _empreinte(S, piece, codes):
    des, fp, val, x, y, rot, face, nets = piece
    descr, attr, pastilles, cy = EMPREINTES[fp]
    dessous = face == "dessous"
    f = "B" if dessous else "F"
    a = f" {n(rot)}" if rot else ""
    L = [f'\t(footprint "Corpus:{fp}"', f'\t\t(layer "{f}.Cu")', f"\t\t(uuid {S.uuid(des)})",
         f"\t\t(at {n(x)} {n(y)}{a})"]
    boite = cy if len(cy) == 4 else (-cy[0], -cy[1], cy[0], cy[1])
    if dessous:
        boite = (boite[0], -boite[3], boite[2], -boite[1])
    L.append(f'\t\t(property "Reference" {q(des)} (at 0 {n(boite[1] - 1.0)}{a}) (layer "{f}.SilkS") '
             f"(uuid {S.uuid(des + '/ref')}) {_effets(dessous)})")
    L.append(f'\t\t(property "Value" {q(val)} (at 0 {n(boite[3] + 1.0)}{a}) (layer "{f}.Fab") '
             f"(uuid {S.uuid(des + '/val')}) {_effets(dessous)})")
    L.append(f'\t\t(property "Footprint" "Corpus:{fp}" (at 0 0{a}) (layer "{f}.Fab") (hide yes) '
             f"(uuid {S.uuid(des + '/fp')}) {_effets(dessous)})")
    L.append(f"\t\t(descr {q(descr)})")
    L.append(f'\t\t(path "/{uuid.uuid5(ESPACE_UUID, S.carte + "/symbole/" + des)}")')
    L.append(f"\t\t(attr {attr})")
    L.append(f"\t\t(fp_rect (start {n(boite[0])} {n(boite[1])}) (end {n(boite[2])} {n(boite[3])}) "
             f'(stroke (width 0.05) (type solid)) (fill none) (layer "{f}.CrtYd") (uuid {S.uuid(des + "/cy")}))')
    for nom, type_, forme, px, py, w, h, perc in pastilles:
        py = -py if dessous else py
        pa = f" {n(rot)}" if rot else ""
        t = [f"\t\t(pad {q(nom)} {type_} {forme} (at {n(px)} {n(py)}{pa}) (size {n(w)} {n(h)})"]
        if perc is not None:
            t.append(f"(drill {n(perc)})")
        t.append(f"(layers {_couches_pastille(type_, dessous)})")
        if type_ == "thru_hole":
            t.append("(remove_unused_layers no)")
        if forme == "roundrect":
            t.append("(roundrect_rratio 0.25)")
        if nets.get(nom):
            t.append(f'(net {codes[nets[nom]]} {q(nets[nom])}) (pintype "passive")')
        t.append(f'(uuid {S.uuid(des + "/pad/" + (nom or "trou"))}))')
        L.append(" ".join(t))
    L.append("\t)")
    return L


def _empilage(couches):
    L = ['\t\t(stackup', '\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))',
         '\t\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))']
    epais = round((1.6 - 0.035 * len(couches) - 0.02) / (len(couches) - 1), 4)
    for i, c in enumerate(couches):
        L.append(f'\t\t\t(layer "{c}" (type "copper") (thickness 0.035))')
        if i < len(couches) - 1:
            L.append(f'\t\t\t(layer "dielectric {i + 1}" (type "{"core" if i % 2 == 0 else "prepreg"}") '
                     f'(thickness {n(epais)}) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))')
    L += ['\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))',
          '\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))',
          '\t\t\t(copper_finish "None")', '\t\t\t(dielectric_constraints no)', '\t\t)']
    return L


def texte_kicad_pcb(c):
    S = Sexpr(c["nom"])
    nets = sorted({r for p in c["pieces"] for r in p[7].values()} | {z[0] for z in c["zones"]})
    codes = {r: i + 1 for i, r in enumerate(nets)}
    L = ["(kicad_pcb", f"\t(version {VERSION_KICAD})", '\t(generator "corpus_lot_e")',
         '\t(generator_version "8.0")', "\t(general", "\t\t(thickness 1.6)", "\t\t(legacy_teardrops no)", "\t)",
         '\t(paper "A4")', "\t(layers"]
    for k, cu in enumerate(c["couches"]):
        num = 31 if cu == "B.Cu" else k
        L.append(f'\t\t({num} "{cu}" {c["types"].get(cu, "signal")})')
    L += ['\t\t(32 "B.Adhes" user "B.Adhesive")', '\t\t(33 "F.Adhes" user "F.Adhesive")',
          '\t\t(34 "B.Paste" user)', '\t\t(35 "F.Paste" user)', '\t\t(36 "B.SilkS" user "B.Silkscreen")',
          '\t\t(37 "F.SilkS" user "F.Silkscreen")', '\t\t(38 "B.Mask" user)', '\t\t(39 "F.Mask" user)',
          '\t\t(44 "Edge.Cuts" user)', '\t\t(45 "Margin" user)', '\t\t(46 "B.CrtYd" user "B.Courtyard")',
          '\t\t(47 "F.CrtYd" user "F.Courtyard")', '\t\t(48 "B.Fab" user)', '\t\t(49 "F.Fab" user)', "\t)",
          "\t(setup"]
    L += _empilage(c["couches"])
    L += ["\t\t(pad_to_mask_clearance 0)", "\t\t(allow_soldermask_bridges_in_footprints no)", "\t)",
          '\t(net 0 "")']
    L += [f"\t(net {codes[r]} {q(r)})" for r in nets]
    for p in c["pieces"]:
        L += _empreinte(S, p, codes)
    x0, y0, x1, y1 = c["contour"]
    L.append(f"\t(gr_rect (start {n(x0)} {n(y0)}) (end {n(x1)} {n(y1)}) (stroke (width 0.05) (type default)) "
             f'(fill none) (layer "Edge.Cuts") (uuid {S.uuid("contour")}))')
    for reseau_, couche in c["zones"]:
        pts = " ".join(f"(xy {n(a)} {n(b)})" for a, b in ((x0 + 0.5, y0 + 0.5), (x1 - 0.5, y0 + 0.5),
                                                          (x1 - 0.5, y1 - 0.5), (x0 + 0.5, y1 - 0.5)))
        L += ["\t(zone", f"\t\t(net {codes[reseau_]})", f"\t\t(net_name {q(reseau_)})", f'\t\t(layer "{couche}")',
              f"\t\t(uuid {S.uuid('zone/' + reseau_)})", "\t\t(hatch edge 0.5)", "\t\t(connect_pads (clearance 0.5))",
              "\t\t(min_thickness 0.25)", "\t\t(filled_areas_thickness no)",
              "\t\t(fill (thermal_gap 0.5) (thermal_bridge_width 0.5))", f"\t\t(polygon (pts {pts}))", "\t)"]
    L.append(")")
    return "\n".join(L) + "\n"


def doc_kicad_pro(c):
    classes = []
    for k in c["classes"]:
        d = {"bus_width": 12, "clearance": k["clearance"], "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
             "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
             "name": k["name"], "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
             "track_width": k["track_width"], "via_diameter": k["via_diameter"], "via_drill": k["via_drill"],
             "wire_width": 6}
        classes.append(d)
    return {
        "board": {"design_settings": {"defaults": {"board_outline_line_width": 0.05},
                                      "rules": dict(REGLES_KICAD), "track_widths": [0.0],
                                      "via_dimensions": [{"diameter": 0.0, "drill": 0.0}]},
                  "layer_presets": [], "viewports": []},
        "meta": {"filename": c["nom"] + ".kicad_pro", "version": 1},
        "net_settings": {"classes": classes, "meta": {"version": 3}, "net_colors": None,
                         "netclass_assignments": c["affectations"],
                         "netclass_patterns": [{"netclass": k, "pattern": m} for k, m in c["motifs"]]},
        "sheets": [], "text_variables": {}}


def doc_attendu(c):
    a = dict(c["attendu"])
    return dict({"format": FORMAT_ATTENDU, "carte": c["nom"], "famille": c["famille"]}, **a)


def texte_json(d):
    return json.dumps(d, ensure_ascii=False, indent=1) + "\n"


# --- Validation d'un attendu (format attendu_deduction/1) --------------------------------------------------------------

ROLES = ("signal", "alimentation", "puissance", "commutation", "plan")
CHAMPS_CONVERTISSEUR = {"reseau_commutation": "reseau", "topologie": "texte", "commutation": "texte",
                        "haut": "piece", "bas": "piece", "pilote": "piece", "inductance": "piece",
                        "reseau_entree": "reseau", "reseau_sortie": "reseau", "reseau_masse": "reseau",
                        "cond_entree": "pieces", "cond_sortie": "pieces", "cond_bulk": "pieces",
                        "cond_pilote": "pieces", "cond_bootstrap": "pieces", "boucle_chaude": "boucle"}
RAISONS_FIXES = ("contrainte", "sans pastille", "virtuel", "mecanique", "decoupe", "verrouillee", "bord", "suggestion")


def valider_attendu_deduction(d, pieces, reseaux, couches):
    """Erreurs (liste de textes) d'un ``attendu_deduction/1`` ; ``pieces``, ``reseaux``, ``couches`` (noms de §0.4)
    sont ceux de la carte : l'attendu ne doit citer qu'eux, et ``roles`` doit classer chaque réseau une fois."""
    E = []

    def piece(v, ou):
        if v not in pieces:
            E.append(f"{ou} : pièce {v!r} absente de la carte")

    def reseau_(v, ou):
        if v not in reseaux:
            E.append(f"{ou} : réseau {v!r} absent de la carte")

    attendues = ("format", "carte", "famille", "description", "regles", "couches", "masses", "roles", "largeurs",
                 "convertisseurs", "sans_piste_dessous", "pas_fin", "matrice", "isolement", "pieces_fixes",
                 "groupes", "chaine")
    for k in attendues:
        if k not in d:
            E.append(f"{k} : champ manquant")
    if E:
        return E
    if d["format"] != FORMAT_ATTENDU:
        E.append(f"format : {FORMAT_ATTENDU} attendu")
    if d["famille"] not in ("chaine", "deduction"):
        E.append("famille : chaine ou deduction attendu")
    if [c["nom"] for c in d["couches"]] != list(couches):
        E.append(f"couches : {list(couches)} attendu (toutes les couches de cuivre, de haut en bas)")
    for i, c in enumerate(d["couches"]):
        if c.get("role") not in ("routage", "plan", "inutilisee"):
            E.append(f"couches[{i}].role : routage, plan ou inutilisee attendu")
        if bool(c.get("plans")) != (c.get("role") == "plan"):
            E.append(f"couches[{i}].plans : non vide si et seulement si le rôle est plan")
        for r in c.get("plans", []):
            reseau_(r, f"couches[{i}].plans")
    if not d["masses"]:
        E.append("masses : au moins une masse attendue")
    for r in d["masses"]:
        reseau_(r, "masses")
    vus = []
    for role, noms in d["roles"].items():
        if role not in ROLES:
            E.append(f"roles.{role} : rôle inconnu")
        for r in noms:
            reseau_(r, f"roles.{role}")
        vus += noms
    if sorted(vus) != sorted(reseaux):
        E.append("roles : chaque réseau de la carte doit être classé exactement une fois")
    for r, l in d["largeurs"].items():
        reseau_(r, "largeurs")
        if len(l) != 3 or not (l[2] is None or l[2] >= l[1] >= l[0] > 0):
            E.append(f"largeurs.{r} : [min, préférée, max|null] croissants attendus")
    for i, cv in enumerate(d["convertisseurs"]):
        for k, genre in CHAMPS_CONVERTISSEUR.items():
            if k not in cv:
                if k not in ("cond_pilote",):
                    E.append(f"convertisseurs[{i}].{k} : champ manquant")
                continue
            v, ou = cv[k], f"convertisseurs[{i}].{k}"
            if genre == "reseau":
                reseau_(v, ou)
            elif genre == "piece":
                piece(v, ou)
            elif genre == "pieces":
                for p in v:
                    piece(p, ou)
            elif genre == "boucle":
                for p in v["condensateurs"]:
                    piece(p, ou + ".condensateurs")
                reseau_(v["reseau_rail"], ou + ".reseau_rail")
                reseau_(v["reseau_masse"], ou + ".reseau_masse")
    if len({cv.get("reseau_commutation") for cv in d["convertisseurs"]}) != len(d["convertisseurs"]):
        E.append("convertisseurs : reseau_commutation en double")
    for k in ("sans_piste_dessous", "pas_fin", "matrice"):
        for p in d[k]:
            piece(p, k)
    if not set(d["matrice"]) <= set(d["pas_fin"]):
        E.append("matrice : une pièce matrice est aussi pas_fin (R-P2b)")
    for i, e in enumerate(d["isolement"]["requis"]):
        for r in e["reseaux"]:
            reseau_(r, f"isolement.requis[{i}]")
        if len(e["reseaux"]) != 2 or not e["mm"] > 0:
            E.append(f"isolement.requis[{i}] : deux réseaux et un isolement > 0 attendus")
    for p, raison in d["pieces_fixes"].items():
        piece(p, "pieces_fixes")
        if raison not in RAISONS_FIXES:
            E.append(f"pieces_fixes.{p} : raison inconnue {raison!r}")
    for i, g in enumerate(d["groupes"]):
        if g.get("type") not in ("fonctionnel", "repete", "impose"):
            E.append(f"groupes[{i}].type : fonctionnel, repete ou impose attendu")
        for p in g.get("pieces", []):
            piece(p, f"groupes[{i}].pieces")
    ch = d["chaine"]
    if (ch is None) != (d["famille"] == "deduction"):
        E.append("chaine : null si et seulement si la famille est deduction")
    cles_chaine = ("niveau", "graines", "graines_valides_min", "couches_pistes", "couches_sans_piste",
                   "descentes_par_vias")
    if ch is not None and sorted(ch) != sorted(cles_chaine):
        E.append(f"chaine : champs {', '.join(cles_chaine)} attendus, exactement")
    elif ch is not None:
        for k in ("couches_pistes", "couches_sans_piste"):
            for c in ch[k]:
                if c not in couches:
                    E.append(f"chaine.{k} : couche {c!r} absente de la carte")
        for r in ch["descentes_par_vias"]:
            reseau_(r, "chaine.descentes_par_vias")
        g, gmin = ch["graines"], ch["graines_valides_min"]
        if (ch["niveau"] not in ("rapide", "normal", "fin") or not isinstance(g, int) or not isinstance(gmin, int)
                or isinstance(g, bool) or isinstance(gmin, bool) or not 1 <= gmin <= g):
            E.append("chaine : niveau (rapide|normal|fin) et entiers 1 <= graines_valides_min <= graines attendus")
    return E


def ecrire(dossier):
    noms = []
    for c in CARTES:
        rep = os.path.join(dossier, c["nom"])
        os.makedirs(rep, exist_ok=True)
        fichiers = {c["nom"] + ".kicad_pcb": texte_kicad_pcb(c), c["nom"] + ".kicad_pro": texte_json(doc_kicad_pro(c)),
                    "contraintes.json": texte_json(c["contraintes"]), "attendu_deduction.json": texte_json(doc_attendu(c))}
        for f, t in fichiers.items():
            with open(os.path.join(rep, f), "wb") as fh:
                fh.write(t.encode("utf-8"))
        noms.append(c["nom"])
    return noms


def _sortie_utf8():
    for flux in (sys.stdout, sys.stderr):
        if not flux.isatty() and hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    _sortie_utf8()
    ap = argparse.ArgumentParser(description="Cartes synthétiques KiCad du corpus (§7.3), écrites de façon "
                                             "déterministe.")
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
    print(f"{len(noms)} cartes écrites : {', '.join(noms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
