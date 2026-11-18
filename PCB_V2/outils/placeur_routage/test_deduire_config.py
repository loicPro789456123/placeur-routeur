"""Tests de deduire_config.py (config_carte/2, §2.4, §3.4, R-P1) et de regions_empreintes.py.

    python -B -m unittest test_deduire_config -v

Entrées entree_carte/2 synthétiques (familles « déduction » de §7.3, noms sans rapport avec une carte réelle) ;
METROID rev3 par l'importeur Altium v2 (import_altium.py, B-ET5).
"""
import io
import json
import math
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
sys.path.insert(0, os.path.join(os.path.dirname(ICI), "format_altium"))
sys.path.insert(0, os.path.join(os.path.dirname(ICI), "serigraphie"))
import deduire_config as DC  # noqa: E402
import formats  # noqa: E402
import importer_carte as IC  # noqa: E402
import regions_empreintes as RE  # noqa: E402
from altium_fichier import PcbDoc  # noqa: E402

DEPOT = os.path.dirname(os.path.dirname(os.path.dirname(ICI)))
GABARIT = os.path.join(DEPOT, "PCB_METROID_V2_rev3.PcbDoc")
MM = 0.0254


# ---------------------------------------------------------------------------------------------------------------------
# Entrées v2 synthétiques
# ---------------------------------------------------------------------------------------------------------------------

def piece(des, desc, reseaux, surface=1.0, valeur="", genre="standard", traversante=False, trou=None):
    """Composant entree_carte/2 : une pastille carrée par réseau, d'aire totale ``surface`` mm²."""
    cote = math.sqrt(surface / max(1, len(reseaux)))
    pads = [{"id": f"{des}#{k}", "nom": str(k + 1), "reseau": r, "x": 1.0 * k, "y": 0.0, "forme": "rect",
             "largeur": cote, "hauteur": cote, "rotation": 0.0, "rayon_coin": 0.0, "points": [],
             "couches": ["TopLayer"], "face": "composant", "trou": trou, "traversante": traversante,
             "expansion_masque": 0.0, "pile": None, "via_dans_pastille_permis": False, "classe_isolement": 0}
            for k, r in enumerate(reseaux)]
    return {"designateur": des, "designateur_source": des, "empreinte": "", "description": desc, "reference": "",
            "valeur": valeur, "genre": genre, "classes_composants": [], "canal": None, "hauteur": 1.0,
            "source_hauteur": "hauteur", "x": 5.0, "y": 5.0, "rotation": 0.0, "face": "dessus", "verrouille": False,
            "boite": [-1.0, -1.0, 1.0, 1.0], "pastilles": pads, "regions": [], "vias": [], "zones_interdites": [],
            "decoupes": []}


def couche(nom, rang):
    """Couche de signal complète de carte.couches (entree_carte/2), sans plan."""
    return {"nom": nom, "nom_fichier": nom, "rang": rang, "type": "signal", "plans": [], "retrait_mm": 0.0,
            "epaisseur_cuivre_mm": None, "pistes_libres": 0, "type_kicad": None, "routage_permis": None}


def regles(reseaux):
    """regles complètes d'entree_carte/2 : une classe d'isolement, mêmes largeurs et via pour tous les réseaux."""
    via = {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.5, "diametre_max": 0.8, "trou_min": 0.25, "trou_max": 0.4}
    return {"isolement": {"defaut": 0.2, "classes": ["toutes"], "matrice": [[0.2]], "classe_reference": 0},
            "reseaux": {n: {"largeurs": [0.254, 0.3, 3.0], "via": dict(via),
                            "classes_isolement": {"piste": 0, "via": 0, "surface": 0}, "couches_permises": None,
                            "priorite_routage": 0, "alimentation_schema": False, "regle_largeur_propre": False}
                        for n in reseaux},
            "largeur_defaut": [0.254, 0.3, 3.0], "via": via, "trou_trou": 0.254, "garde_bord": None,
            "isolement_pieces": None, "hauteurs": {"dessus": None, "dessous": None}, "pieces": {},
            "connexion_plans": None, "masque_serigraphie": None, "salles": [], "tolerances_longueur": {},
            "non_interpretees": [], "ignorees": []}


def entree(pieces, couches=None, remplissages=None):
    """entree_carte/2 complète (contour 50 × 40 mm, deux couches de signal par défaut), sans classes."""
    contour = [[0.0, 0.0], [50.0, 0.0], [50.0, 40.0], [0.0, 40.0]]
    couches = couches or [couche("TopLayer", 0), couche("BottomLayer", 1)]
    reseaux = sorted({p["reseau"] for c in pieces for p in c["pastilles"] if p["reseau"]})
    return {"format": "entree_carte/2",
            "source": {"fichier": "synthetique.kicad_pcb", "type": "kicad_pcb", "sha256": "0" * 64,
                       "version_fichier": "20240108", "origine_fichier_mil": None, "origine_fichier_mm": [0.0, 0.0],
                       "origine_cao_mil": None},
            "unite": "mm",
            "carte": {"contour": contour, "boite": [0.0, 0.0, 50.0, 40.0], "aire_reference_mm2": None,
                      "decoupes": [], "trous": [], "couches": couches, "remplissages": remplissages or [],
                      "zones_interdites": [], "obstacles_cuivre": [], "cuivre_existant": [],
                      "couches_cuivre": len(couches), "noms_couches_cuivre": [c["nom"] for c in couches]},
            "composants": pieces, "pastilles_libres": [], "reseaux": reseaux, "classes": {}, "classes_composants": {},
            "paires": [], "regles_fichier": [], "regles": regles(reseaux), "avertissements": []}


def masse_partagee(n=10, masse="MASSE"):
    return [piece(f"RX{k}", "Chip Resistor", [masse, f"N{k}"]) for k in range(n)]


def demi_pont(elevateur=False, des=("QH", "QB"), desc="MOSFET N", grilles_resistances=False):
    """Demi-pont synchrone : interrupteurs, pilote CI1, inductance L9, condensateurs."""
    vin, vout = ("VBAT", "VHV") if elevateur else ("VIN", "VOUT")
    rail = vout if elevateur else vin
    gh, gl = ("GHD", "GLD") if grilles_resistances else ("GH", "GL")
    p = [piece(des[0], desc, ["SW", "GH", rail]), piece(des[1], desc, ["MASSE", "GL", "SW"]),
         piece("CI1", "Controller IC", [vin, gh, gl, "SW", "BST", "FB", "SS", "MASSE"]),
         piece("L9", "Power Inductor", ["SW", vin if elevateur else vout], 100.0),
         piece("CB", "Chip Capacitor", ["BST", "SW"]), piece("CSS", "Chip Capacitor", ["SS", "MASSE"]),
         piece("CE1", "Chip Capacitor", [vin, "MASSE"], 8.0), piece("CE2", "Chip Capacitor", [vin, "MASSE"], 3.0),
         piece("CE3", "Aluminum Electrolytic Capacitor", [vin, "MASSE"], 70.0),
         piece("CS1", "Chip Capacitor", [vout, "MASSE"], 8.0), piece("CS2", "Chip Capacitor", [vout, "MASSE"], 4.0),
         piece("RF1", "Chip Resistor", [vout, "FB"]), piece("RF2", "Chip Resistor", ["FB", "MASSE"])]
    if grilles_resistances:
        p += [piece("RG1", "Chip Resistor", ["GHD", "GH"]), piece("RG2", "Chip Resistor", ["GLD", "GL"])]
    return entree(p + masse_partagee())


def integre_sot23():
    return entree([piece("U1", "SOT-23-6 buck converter", ["BST", "MASSE", "FB", "EN", "VIN", "SW"]),
                   piece("L1", "Inductor", ["SW", "VOUT"], 1.5, valeur="4.7uH"),
                   piece("CB", "Chip Capacitor", ["BST", "SW"]), piece("CIN", "Chip Capacitor", ["VIN", "MASSE"]),
                   piece("CO1", "Chip Capacitor", ["VOUT", "MASSE"]), piece("CO2", "Chip Capacitor", ["VOUT", "MASSE"]),
                   piece("RE1", "Chip Resistor", ["VIN", "EN"]), piece("RF1", "Chip Resistor", ["VOUT", "FB"]),
                   piece("RF2", "Chip Resistor", ["FB", "MASSE"])] + masse_partagee())


def un(cfg):
    assert len(cfg["convertisseurs"]) == 1, cfg["convertisseurs"]
    return cfg["convertisseurs"][0]


class TestConvertisseurs(unittest.TestCase):
    def test_demi_pont_synchrone(self):
        cfg = DC.deduire(demi_pont())
        c = un(cfg)
        self.assertEqual((c["haut"], c["bas"], c["pilote"], c["inductance"]), ("QH", "QB", "CI1", "L9"))
        self.assertEqual((c["reseau_entree"], c["reseau_sortie"], c["reseau_masse"], c["reseau_commutation"]),
                         ("VIN", "VOUT", "MASSE", "SW"))
        self.assertEqual((c["topologie"], c["commutation"], c["origine"]), ("abaisseur", "synchrone", "deduit"))
        self.assertEqual((c["cond_entree"], c["cond_bulk"], c["cond_pilote"]), (["CE1", "CE2"], ["CE3"], ["CE2"]))
        self.assertEqual((c["cond_sortie"], c["cond_bootstrap"]), (["CS1"], ["CB"]))
        self.assertEqual(c["reseaux_courts"], ["BST", "GH", "GL", "SS"])
        self.assertEqual(c["reseaux_sensibles"], ["FB"])
        self.assertEqual(c["boucle_chaude"], {"condensateurs": ["CE1", "CE2"], "reseau_rail": "VIN",
                                              "reseau_masse": "MASSE"})
        self.assertEqual(c["nom"], "abaisseur VIN -> VOUT")
        self.assertEqual(list(c), list(DC.CHAMPS))
        self.assertEqual(cfg["sans_piste_dessous"], ["L9"])
        self.assertEqual((cfg["format"], cfg["surcharges_appliquees"], cfg["avertissements"], cfg["a_confirmer"]),
                         ("config_carte/2", [], [], []))
        self.assertEqual(formats.valider_config_carte(cfg), [])

    def test_prefixe_m_par_la_topologie(self):
        """M → autre (§6.2) ; un interrupteur sans mot se reconnaît à la topologie."""
        e = demi_pont(des=("M7", "M8"), desc="SON, 8-Leads, Body 3.30x3.30mm")
        self.assertEqual(DC.nature(e["composants"][0]), "autre")
        c = un(DC.deduire(e))
        self.assertEqual((c["haut"], c["bas"], c["reseau_commutation"]), ("M7", "M8", "SW"))

    def test_elevateur(self):
        c = un(DC.deduire(demi_pont(elevateur=True)))
        self.assertEqual((c["topologie"], c["reseau_entree"], c["reseau_sortie"]), ("elevateur", "VBAT", "VHV"))
        self.assertEqual(c["cond_sortie"], ["CS1", "CS2"])
        self.assertEqual(c["boucle_chaude"], {"condensateurs": ["CS1", "CS2"], "reseau_rail": "VHV",
                                              "reseau_masse": "MASSE"})

    def test_asynchrone(self):
        e = entree([piece("QH", "P-channel MOSFET", ["VIN", "GH", "SW"]),
                    piece("D1", "Schottky Diode", ["SW", "MASSE"]),
                    piece("CI1", "Controller IC", ["VIN", "GH", "FB", "MASSE"]),
                    piece("L2", "Power Inductor", ["SW", "VOUT"], 30.0),
                    piece("CE1", "Chip Capacitor", ["VIN", "MASSE"]), piece("CS1", "Chip Capacitor", ["VOUT", "MASSE"]),
                    piece("RF1", "Chip Resistor", ["VOUT", "FB"])] + masse_partagee())
        c = un(DC.deduire(e))
        self.assertEqual((c["haut"], c["bas"], c["pilote"], c["commutation"]), ("QH", "D1", "CI1", "asynchrone"))
        self.assertEqual((c["topologie"], c["reseau_entree"], c["reseau_sortie"]), ("abaisseur", "VIN", "VOUT"))

    def test_integre_sot23(self):
        cfg = DC.deduire(integre_sot23())
        c = un(cfg)
        self.assertEqual((c["haut"], c["bas"], c["pilote"], c["inductance"]), ("U1", "U1", "U1", "L1"))
        self.assertEqual((c["commutation"], c["topologie"]), ("integre", "abaisseur"))
        self.assertEqual((c["reseau_entree"], c["reseau_sortie"], c["reseau_commutation"]), ("VIN", "VOUT", "SW"))
        self.assertEqual(c["cond_bootstrap"], ["CB"])
        self.assertEqual(len(cfg["a_confirmer"]), 1)
        self.assertIn("topologie supposée", cfg["a_confirmer"][0])
        self.assertEqual(cfg["sans_piste_dessous"], [])          # 1,5 mm² < 4 mm²

    def test_integre_trop_petit(self):
        """R-V3 : inductance de moins de 2 mm² et de moins de 0,47 µH : pas un convertisseur."""
        e = integre_sot23()
        e["composants"][1]["valeur"] = "100nH"
        self.assertEqual(DC.deduire(e)["convertisseurs"], [])

    def test_resistances_de_grille(self):
        c = un(DC.deduire(demi_pont(grilles_resistances=True)))
        self.assertEqual((c["haut"], c["bas"], c["pilote"]), ("QH", "QB", "CI1"))
        self.assertEqual(c["reseaux_courts"], ["BST", "GH", "GHD", "GL", "GLD", "SS"])
        self.assertEqual(c["reseaux_sensibles"], ["FB"])

    def test_pilotes_commandes_par_un_controleur(self):
        """Deux pilotes séparés (haut, bas), eux-mêmes commandés par un même contrôleur."""
        e = entree([piece("QH", "MOSFET", ["VIN", "GH", "SW"]), piece("QB", "MOSFET", ["SW", "GL", "MASSE"]),
                    piece("DRH", "Gate driver", ["VIN", "PWMH", "GH", "MASSE"]),
                    piece("DRB", "Gate driver", ["VIN", "PWML", "GL", "MASSE"]),
                    piece("CTL", "PWM controller", ["VIN", "PWMH", "PWML", "FB", "MASSE"]),
                    piece("L1", "Inductor", ["SW", "VOUT"], 30.0), piece("CE", "Chip Capacitor", ["VIN", "MASSE"]),
                    piece("CS", "Chip Capacitor", ["VOUT", "MASSE"])] + masse_partagee())
        c = un(DC.deduire(e))
        self.assertEqual((c["haut"], c["bas"], c["pilote"]), ("QH", "QB", "CTL"))
        self.assertEqual({"GH", "GL", "PWMH", "PWML"} - set(c["reseaux_courts"]), set())

    def test_boitier_double(self):
        """Boîtier double à 5 réseaux : haut = bas = le boîtier."""
        e = entree([piece("Q5", "Dual N-channel MOSFET", ["VIN", "G1", "SW", "G2", "MASSE"], 20.0),
                    piece("CI1", "Controller IC", ["VIN", "G1", "G2", "SW", "MASSE"]),
                    piece("L1", "Inductor", ["SW", "VOUT"], 30.0), piece("CE", "Chip Capacitor", ["VIN", "MASSE"]),
                    piece("CS", "Chip Capacitor", ["VOUT", "MASSE"])] + masse_partagee())
        c = un(DC.deduire(e))
        self.assertEqual((c["haut"], c["bas"], c["pilote"], c["reseau_entree"]), ("Q5", "Q5", "CI1", "VIN"))

    def test_multiphase_filtre_entree(self):
        e = entree([piece("U1", "Multiphase controller", ["VIN", "GH1", "GL1", "SW1", "BST1", "GH2", "GL2", "SW2",
                                                         "BST2", "FB", "MASSE"]),
                    piece("Q1", "MOSFET", ["VIN", "GH1", "SW1"]), piece("Q2", "MOSFET", ["SW1", "GL1", "MASSE"]),
                    piece("Q3", "MOSFET", ["VIN", "GH2", "SW2"]), piece("Q4", "MOSFET", ["SW2", "GL2", "MASSE"]),
                    piece("L1", "Inductor", ["SW1", "VOUT"], 30.0), piece("L2", "Inductor", ["SW2", "VOUT"], 30.0),
                    piece("LF", "Inductor", ["VBAT", "VIN"], 30.0),
                    piece("CF", "Chip Capacitor", ["VBAT", "MASSE"]), piece("CI1", "Chip Capacitor", ["VIN", "MASSE"]),
                    piece("CI2", "Chip Capacitor", ["VIN", "MASSE"]), piece("CO1", "Chip Capacitor", ["VOUT", "MASSE"]),
                    piece("CB1", "Chip Capacitor", ["BST1", "SW1"]), piece("CB2", "Chip Capacitor", ["BST2", "SW2"]),
                    piece("RF", "Chip Resistor", ["VOUT", "FB"])] + masse_partagee())
        cfg = DC.deduire(e)
        noeuds = [c["reseau_commutation"] for c in cfg["convertisseurs"]]
        self.assertEqual(noeuds, ["SW1", "SW2"])
        self.assertEqual([(c["haut"], c["bas"]) for c in cfg["convertisseurs"]], [("Q1", "Q2"), ("Q3", "Q4")])
        self.assertTrue(all(c["reseau_entree"] == "VIN" for c in cfg["convertisseurs"]))
        self.assertEqual(cfg["convertisseurs"][0]["cond_bootstrap"], ["CB1"])

    def test_double_abaisseur_integre(self):
        e = entree([piece("U1", "Dual buck regulator", ["VIN", "SW1", "SW2", "FB1", "FB2", "VOS2", "MASSE"]),
                    piece("L1", "Inductor", ["SW1", "VOUT1"], 6.0), piece("L2", "Inductor", ["SW2", "VOS2"], 6.0),
                    piece("CI1", "Chip Capacitor", ["VIN", "MASSE"]), piece("CI2", "Chip Capacitor", ["VIN", "MASSE"])]
                   + [piece(f"CO{k}", "Chip Capacitor", ["VOUT1" if k < 3 else "VOS2", "MASSE"]) for k in range(6)]
                   + [piece("RF1", "Chip Resistor", ["VOUT1", "FB1"]), piece("RF2", "Chip Resistor", ["VOS2", "FB2"])]
                   + masse_partagee())
        cfg = DC.deduire(e)
        self.assertEqual([c["reseau_commutation"] for c in cfg["convertisseurs"]], ["SW1", "SW2"])
        self.assertEqual([(c["reseau_entree"], c["reseau_sortie"]) for c in cfg["convertisseurs"]],
                         [("VIN", "VOUT1"), ("VIN", "VOS2")])
        self.assertEqual(len(cfg["a_confirmer"]), 2)

    def test_filtre_pi_et_adaptation_rf(self):
        e = entree([piece("FB1", "Ferrite bead 600R", ["VIN", "VA"], 2.0),
                    piece("L5", "Chip bead", ["VIN", "VB"], 6.0),
                    piece("L6", "Chip inductor", ["VIN", "VC"], 1.0, valeur="10uH"),
                    piece("C1", "Chip Capacitor", ["VIN", "MASSE"]), piece("C2", "Chip Capacitor", ["VA", "MASSE"]),
                    piece("C3", "Chip Capacitor", ["VB", "MASSE"]), piece("C4", "Chip Capacitor", ["VC", "MASSE"]),
                    piece("U1", "Op amp", ["VA", "IN", "OUT", "MASSE"]),
                    piece("U2", "RF transceiver IC", ["VB", "RFO", "MASSE"]),
                    piece("L3", "Chip inductor", ["RFO", "ANT"], 0.5, valeur="2.2nH"),
                    piece("C5", "Chip Capacitor", ["ANT", "MASSE"]), piece("C6", "Chip Capacitor", ["RFO", "MASSE"]),
                    piece("E1", "Antenna", ["ANT"])] + masse_partagee())
        cfg = DC.deduire(e)
        self.assertEqual(cfg["convertisseurs"], [])
        nat = {c["designateur"]: DC.nature(c) for c in e["composants"]}
        self.assertEqual((nat["FB1"], nat["L5"], nat["L6"], nat["L3"]), ("ferrite", "ferrite", "inductance",
                                                                         "inductance"))
        self.assertEqual(cfg["sans_piste_dessous"], [])   # R-V5 : ferrite exclue, selfs de filtrage < 4 mm²

    def test_sans_piste_dessous(self):
        e = entree([piece("T1", "Transformer", ["P1", "P2", "S1", "S2"], 20.0),
                    piece("L7", "Power inductor", ["A", "B"], 12.0), piece("L8", "Power inductor", ["A", "A"], 12.0),
                    piece("FB3", "Ferrite", ["A", "C"], 12.0)])
        self.assertEqual(DC.deduire(e)["sans_piste_dessous"], ["T1", "L7"])

    def test_sans_convertisseur_et_sans_classes(self):
        e = demi_pont()
        e["composants"] = [c for c in e["composants"] if c["designateur"] != "L9"]
        del e["classes"]
        cfg = DC.deduire(e)
        self.assertEqual(cfg["convertisseurs"], [])
        self.assertNotIn("classes", cfg)
        self.assertEqual(DC.deduire(entree([]))["convertisseurs"], [])

    def test_masses_multiples(self):
        """R-M1 : liste de masses ; le convertisseur de puissance a sa masse PGND."""
        e = demi_pont()
        for c in e["composants"]:
            for p in c["pastilles"]:
                if c["designateur"] in ("QB", "CE1", "CE2", "CE3", "CS1", "CS2") and p["reseau"] == "MASSE":
                    p["reseau"] = "PGND"
        e["reseaux"].append("PGND")
        c = un(DC.deduire(e, {"contraintes": {"roles": {"masse": ["MASSE", "PGND"]}}}))
        self.assertEqual((c["reseau_masse"], c["cond_entree"], c["bas"]), ("PGND", ["CE1", "CE2"], "QB"))
        self.assertEqual(DC.masses(e, {"roles": {"masse": "PGND"}}), (["PGND"], []))
        with self.assertRaises(formats.ErreurFormat) as cm:
            DC.masses(e, {"roles": {"masse": ["MASSE", "AGND"]}})
        self.assertIn("roles.masse[1] : réseau AGND inconnu", str(cm.exception))


class TestMassesEtNatures(unittest.TestCase):
    def masse_contre(self, n_a, n_b):
        p = [piece(f"CA{k}", "Chip Capacitor", ["A", f"X{k}"]) for k in range(n_a)]
        p += [piece(f"CB{k}", "Chip Capacitor", ["B", f"Y{k}"]) for k in range(n_b)]
        return DC.masses(entree(p))

    def test_r_m2_avertissement_20_pourcent(self):
        m, av = self.masse_contre(10, 9)
        self.assertEqual(m, ["A"])
        self.assertEqual(len(av), 1)
        self.assertIn("moins de 20 %", av[0])
        self.assertEqual(self.masse_contre(10, 8), (["A"], []))        # 20 % juste : pas d'avertissement
        self.assertEqual(self.masse_contre(10, 5), (["A"], []))

    def test_r_m2_ordre(self):
        """Zone de plan d'abord, puis condensateurs, puis pastilles ; égalité : ordre alphabétique."""
        p = [piece(f"CA{k}", "Chip Capacitor", ["A", f"X{k}"]) for k in range(5)]
        p += [piece("RB", "Resistor", ["B"] * 2)]
        couches = [{"nom": "TopLayer", "type": "signal", "pistes_libres": 3},
                   {"nom": "MidLayer1", "nom_fichier": "L2_masse", "type": "signal", "pistes_libres": 0},
                   {"nom": "BottomLayer", "type": "signal", "pistes_libres": 3}]
        rempl = [{"nom": "P", "couche": "MidLayer1", "reseau": "B", "couverture": 0.97}]
        e = entree(p, couches, rempl)
        self.assertEqual(DC.couches_plans(e), {"MidLayer1": ["B"]})
        self.assertEqual(DC.masses(e)[0], ["B"])
        self.assertEqual(DC.masses(entree(p))[0], ["A"])
        m, av = DC.masses(entree([piece("C1", "Capacitor", ["Z", "Y"])]))
        self.assertEqual(m, ["Y"])
        self.assertIn("ordre alphabétique", av[0])
        couches[1]["nom_fichier"] = "L2"                               # sans indice d'intention : routage
        self.assertEqual(DC.couches_plans(entree(p, couches, rempl)), {})

    def test_r_p1_ordre(self):
        n = lambda *a, **k: DC.nature_et_source(piece(*a, **k))      # noqa: E731
        self.assertEqual(n("R5", "Ceramic Capacitor", ["A", "B"]), ("condensateur", "mots"))  # mots avant préfixe
        self.assertEqual(n("C1", "Capacitor", ["A"], genre="graphique"), ("graphique", "genre"))
        self.assertEqual(n("NT1", "", ["A", "B"], genre="liaison_reseaux"), ("liaison", "genre"))
        self.assertEqual(n("L4", "Ferrite core power inductor", ["A", "B"]), ("ferrite", "mots"))  # ferrite d'abord
        self.assertEqual(n("X1", "", ["A", "B"], valeur="100nF"), ("condensateur", "mots"))
        self.assertEqual(n("X2", "", ["A", "B"], valeur="4k7"), ("resistance", "mots"))
        self.assertEqual(n("Z1", "Kondensator 100n", ["A", "B"]), ("condensateur", "mots"))
        self.assertEqual(n("Z2", "Widerstand", ["A", "B"]), ("resistance", "mots"))
        self.assertEqual(n("Z3", "Resistencia SMD", ["A", "B"]), ("resistance", "mots"))
        self.assertEqual(n("Z4", "Induttore di potenza", ["A", "B"]), ("inductance", "mots"))
        self.assertEqual(n("Z5", "Résistance 1 %", ["A", "B"]), ("resistance", "mots"))
        self.assertEqual(n("Q9", "MOSFET, low on-resistance", ["A", "B", "C"]), ("transistor", "mots"))
        self.assertEqual(n("U3", "LED driver", ["A", "B", "C"]), ("circuit", "mots"))
        self.assertEqual(n("J9", "Through hole", ["A", "B"]), ("connecteur", "prefixe"))  # « through hole » ne suffit pas
        trou = {"diametre": 3.2, "forme": "rond", "longueur": 3.2, "rotation": 0.0, "decalage": [0.0, 0.0],
                "metallise": True}
        self.assertEqual(n("Z6", "", ["GND"] * 4, traversante=True, trou=trou), ("mecanique", "geometrie"))
        self.assertEqual(n("Z7", "", ["A", "B"], traversante=True, trou=trou), ("autre", "defaut"))  # 2 réseaux
        for des, nat in (("M3", "autre"), ("FB2", "ferrite"), ("LED1", "diode"), ("HDR1", "connecteur"),
                         ("Y1", "quartz"), ("SW2", "interface"), ("MH1", "mecanique"), ("IC4", "circuit"),
                         ("Q2", "transistor"), ("T1", "transformateur")):
            self.assertEqual(n(des, "", ["A", "B"]), (nat, "prefixe"), des)
        e = entree([piece("M3", "", ["A", "B"]), piece("C1", "Capacitor", ["A", "B"])])
        self.assertEqual(DC.classer(e)[1], ["M3"])
        self.assertEqual(DC.deduire(e)["pieces_par_prefixe"], ["M3"])

    def test_r_p1_descriptions_reelles(self):
        """Descriptions de pièces réelles (catalogues) : mots ambigus, coupure à « with », passes, exclusions."""
        cas = [("FB1", "Ferrite bead 600 ohm @ 100MHz 0603", {}, "ferrite"),
               ("L2", "Chip Bead for signal line, 1500 ohm at 100 MHz", {}, "ferrite"),
               ("L3", "Chip inductor", {"description_source": "SMD EMI suppression ferrite bead, Z = 30 ohm"},
                "ferrite"),
               ("FB2", "Ferrite bead", {"empreinte": "RESC1608X55N"}, "ferrite"),     # description d'abord
               ("S2", "DIP Switch Side actuator long liver Piano Key", {"empreinte": "DIP-16-KEY"}, "interface"),
               ("SW2", "Small compact SMD DIP switch with gull wing", {}, "interface"),
               ("D1", "", {"empreinte": "SMA"}, "diode"),                           # SMA : boîtier DO-214AC
               ("U1", "Single-Chip USB to UART Bridge", {}, "circuit"),
               ("U2", "Adjustable precision shunt regulator", {}, "circuit"),
               ("U3", "Switched capacitor voltage converter (inverter)", {}, "circuit"),
               ("U4", "3 1/2 digit A/D converter, LED display driver", {}, "circuit"),
               ("U5", "Half-bridge gate driver with integrated bootstrap diode", {}, "circuit"),
               ("U6", "Buck controller, current shunt monitor", {}, "circuit"),
               ("U7", "Adjustable current-limit switch", {}, "circuit"),
               ("U8", "Load switch, 5.5 V, 2 A", {}, "circuit"),
               ("U9", "Highly integrated synchronous DC-DC buck with integrated inductor", {}, "circuit"),
               ("U10", "DDR4 DIMM temperature sensor with EEPROM", {}, "circuit"),
               ("DS1", "7-segment LED display, 10-pin DIP", {}, "interface"),
               ("D2", "SMD chip LED, red", {}, "diode"),
               ("J9", "RJ45 LAN transformer, modular jack", {}, "connecteur"),
               ("J10", "USB 2.0 Type-C receptacle", {}, "connecteur"),
               ("X1", "", {"empreinte": "USB_Micro-B"}, "connecteur"),              # « usb » : indice
               ("R7", "Current sense shunt 2512", {}, "resistance"),                # « shunt » : indice
               ("R8", "", {"valeur": "10 mOhm"}, "resistance"),
               ("U11", "", {"empreinte": "SOIC-8_3.9x4.9mm_P1.27mm"}, "circuit")]   # boîtier : indice
        for des, desc, autres, attendu in cas:
            c = piece(des, desc, ["A", "B"])
            c.update(autres)
            self.assertEqual(DC.nature(c), attendu, (des, desc, autres))
        # pilote décrit avec un mot d'une autre nature : le demi-pont est toujours trouvé (R-V1)
        for desc in ("Half-bridge gate driver with integrated bootstrap diode", "Buck controller, current shunt monitor"):
            e = demi_pont()
            next(c for c in e["composants"] if c["designateur"] == "CI1")["description"] = desc
            c = un(DC.deduire(e))
            self.assertEqual((c["haut"], c["bas"], c["pilote"]), ("QH", "QB", "CI1"), desc)

    def test_r_p4_suggestions(self):
        e = entree([piece("J1", "Connector", ["A", "B"]), piece("SW1", "Push button", ["A", "B"]),
                    piece("J2", "Connector", ["A", "B"]), piece("R1", "Resistor", ["A", "B"])])
        e["composants"][2]["x"] = 80.0                                     # hors du contour
        self.assertEqual(DC.deduire(e)["pieces_a_fixer_suggerees"], ["J1", "SW1"])


class TestSurcharges(unittest.TestCase):
    def test_ajouter_modifier_supprimer(self):
        e = demi_pont()
        k = {"convertisseurs": {
            "modifier": [{"reseau_commutation": "SW", "cond_sortie": ["CS1", "CS2"]}],
            "ajouter": [{"reseau_commutation": "BST", "haut": "CI1", "bas": "CI1", "inductance": "L9",
                         "reseau_entree": "VIN", "reseau_sortie": "VOUT", "reseau_masse": "MASSE"}]}}
        cfg = DC.deduire(e, {"contraintes": k})
        a, b = cfg["convertisseurs"]
        self.assertEqual((a["origine"], a["cond_sortie"]), ("modifie", ["CS1", "CS2"]))
        self.assertEqual((b["origine"], b["commutation"], b["topologie"]), ("impose", "integre", "abaisseur"))
        self.assertEqual(b["boucle_chaude"]["reseau_rail"], "VIN")
        self.assertEqual(len(cfg["surcharges_appliquees"]), 2)
        self.assertEqual(formats.valider_config_carte(cfg), [])
        cfg = DC.deduire(e, {"contraintes": {"convertisseurs": {"supprimer": [{"reseau_commutation": "SW"}]}}})
        self.assertEqual(cfg["convertisseurs"], [])
        self.assertIn("supprimé", cfg["surcharges_appliquees"][0])
        # puis --config (même sémantique, forme liste) : modifier par le nœud, après les contraintes
        cfg = DC.deduire(e, {"contraintes": k, "config": {"format": "config_carte/2", "convertisseurs": [
            {"reseau_commutation": "BST", "supprimer": True}, {"reseau_commutation": "SW", "pilote": ""}]}})
        self.assertEqual([c["reseau_commutation"] for c in cfg["convertisseurs"]], ["SW"])
        self.assertEqual(cfg["convertisseurs"][0]["pilote"], "")

    def test_topologie_imposee_retire_a_confirmer(self):
        k = {"convertisseurs": {"modifier": [{"reseau_commutation": "SW", "topologie": "elevateur",
                                              "reseau_entree": "VOUT", "reseau_sortie": "VIN"}]}}
        cfg = DC.deduire(integre_sot23(), {"contraintes": k})
        c = un(cfg)
        self.assertEqual((c["topologie"], c["origine"], cfg["a_confirmer"]), ("elevateur", "modifie", []))
        self.assertEqual(c["boucle_chaude"]["reseau_rail"], "VIN")
        self.assertEqual(c["nom"], "elevateur VOUT -> VIN")

    def test_sans_piste_dessous_et_identique(self):
        e = demi_pont()
        cfg = DC.deduire(e, {"contraintes": {"sans_piste_dessous": {"ajouter": ["CE3"], "retirer": ["L9"]}}})
        self.assertEqual(cfg["sans_piste_dessous"], ["CE3"])
        # une configuration complète relue sans changement ne modifie rien
        cfg = DC.deduire(e)
        cfg2 = DC.deduire(e, {"config": json.loads(json.dumps(cfg))})
        self.assertEqual(cfg2["convertisseurs"][0]["origine"], "deduit")
        self.assertEqual(DC.comparer(cfg2, cfg), [])

    def test_config_v1(self):
        """config_carte v1 : une chaîne devient une liste ; « eloignement » → éloignement dure de référence [haut]
        (§2.3) ; nœud vide."""
        v1 = {"_doc": "v1", "convertisseurs": [
            {"nom": "hacheur", "haut": "QH", "bas": "QB", "pilote": "CI1", "inductance": "L9", "cond_bulk": "CE3",
             "cond_sortie": "CS2", "cond_pilote": "", "reseau_commutation": "",
             "eloignement": [{"broches": ["CI1.6"], "distance_mm": 30.0}]}]}
        cfg = DC.deduire(demi_pont(), {"config": v1, "noms": {"config": "u.json"}})
        c = un(cfg)
        self.assertEqual((c["cond_bulk"], c["cond_sortie"], c["cond_pilote"]), (["CE3"], ["CS2"], []))
        self.assertEqual((c["origine"], c["nom"]), ("modifie", "hacheur"))
        self.assertEqual(cfg["eloignements"], [{"type": "dure", "objets": ["CI1.6"], "reference": ["QH"],
                                                "distance_mm": 30.0}])
        self.assertEqual(formats.valider_config_carte(cfg), [])
        # relu en v2 (champ eloignements) : transmis tel quel
        cfg2 = DC.deduire(demi_pont(), {"config": json.loads(json.dumps(cfg))})
        self.assertEqual(cfg2["eloignements"], cfg["eloignements"])
        for broches, attendu in ((["CI1.99"], "broche 99 absente de CI1"), (["Q99.1"], "pièce Q99 inconnue")):
            v1["convertisseurs"][0]["eloignement"] = [{"broches": broches, "distance_mm": 30.0}]
            with self.assertRaises(formats.ErreurFormat) as cm:
                DC.deduire(demi_pont(), {"config": v1, "noms": {"config": "u.json"}})
            self.assertIn(f"u.json : convertisseurs[0].eloignement[0].broches[0] : {attendu}", str(cm.exception))

    def test_entree_mal_formee(self):
        """Entrée entree_carte/2 mal formée au-delà du premier niveau : ErreurFormat avec le chemin (code 2)."""
        def casse(f):
            e = demi_pont()
            f(e)
            return e
        qh = lambda e: e["composants"][0]                                       # noqa: E731
        cas = [(lambda e: qh(e).pop("designateur"), "composants[0].designateur : champ manquant"),
               (lambda e: qh(e)["pastilles"][0].update(largeur="2"), "composants[0].pastilles[0].largeur : nombre"),
               (lambda e: e.update(composants="abc"), "composants : liste attendue"),
               (lambda e: e["composants"].append(qh(e)), "composants[23].designateur : QH en double"),
               (lambda e: qh(e).update(genre="pièce"), "composants[0].genre : 'pièce' inconnu"),
               (lambda e: qh(e)["pastilles"][1].pop("trou"), "composants[0].pastilles[1].trou : champ manquant"),
               (lambda e: qh(e)["pastilles"][0].update(forme="ovale"), "pastilles[0].forme : 'ovale' inconnue"),
               (lambda e: e["carte"].update(contour=[[0, 0], [1]]), "carte.contour[1] : point [x, y] attendu"),
               (lambda e: e["carte"]["remplissages"].append({"couche": "L1", "reseau": "MASSE"}),
                "carte.remplissages[0].couverture : champ manquant"),
               (lambda e: e.update(reseaux=["VIN", 3]), "reseaux[1] : texte attendu"),
               (lambda e: e.pop("pastilles_libres"), "pastilles_libres : champ manquant")]
        for f, attendu in cas:
            with self.assertRaises(formats.ErreurFormat, msg=attendu) as cm:
                DC.deduire(casse(f), {"noms": {"entree": "e.json"}})
            self.assertIn(attendu, str(cm.exception))
            self.assertTrue(str(cm.exception).startswith("e.json : "), str(cm.exception))
        # composant standard sans pastille : accepté (§2.2), avec un avertissement
        cfg = DC.deduire(casse(lambda e: qh(e).update(pastilles=[])))
        self.assertIn("QH : composant standard sans pastille", cfg["avertissements"])

    def test_erreurs(self):
        e = demi_pont()
        cas = [({"convertisseurs": [{"reseau_commutation": "SW", "haut": "Q99"}]}, "convertisseurs[0].haut : pièce Q99"),
               ({"convertisseurs": [{"reseau_commutation": "SW", "cond_entree": ["CE1", "C99"]}]},
                "convertisseurs[0].cond_entree[1] : pièce C99 inconnue"),
               ({"convertisseurs": [{"reseau_commutation": "SW", "reseau_entre": "VIN"}]},
                "vouliez-vous dire 'reseau_entree'"),
               ({"convertisseurs": [{"reseau_commutation": "SW", "reseau_masse": "GND9"}]}, "réseau GND9 inconnu"),
               ({"convertisseurs": [{"reseau_commutation": "SW", "cond_sortie": "CS1"}]}, "liste de textes attendue"),
               ({"convertisseurs": [{"reseau_commutation": "BST", "haut": "QH"}]}, "champ(s) obligatoire(s)"),
               ({"format": "config_carte/3"}, "config_carte/3 non pris en charge"),
               ({"convertisseur": []}, "clé inconnue")]
        for u, attendu in cas:
            with self.assertRaises(formats.ErreurFormat, msg=attendu) as cm:
                DC.deduire(e, {"config": dict(u, format=u.get("format", "config_carte/2")), "noms": {"config": "u.json"}})
            self.assertIn(attendu, str(cm.exception))
            self.assertTrue(str(cm.exception).startswith("u.json : "), str(cm.exception))
        with self.assertRaises(formats.ErreurFormat) as cm:
            DC.deduire(e, {"contraintes": {"convertisseurs": {"modifier": [{"reseau_commutation": "BST"}]}}})
        self.assertIn("convertisseurs.modifier[0].reseau_commutation : convertisseur BST introuvable",
                      str(cm.exception))


class TestComparerEtLigneDeCommande(unittest.TestCase):
    def test_comparer(self):
        cfg = DC.deduire(demi_pont())
        ref = json.loads(json.dumps(cfg))
        conv = ref["convertisseurs"][0]
        conv.update(nom="libellé libre", eloignement=[{"broches": ["CI1.6"], "distance_mm": 30.0}],
                    reseau_commutation="", _commentaire="ignoré", cond_bulk="CE3")
        self.assertEqual(DC.comparer(cfg, ref), [])
        conv["reseau_commutation"] = "AUTRE"           # un nœud explicite doit être retrouvé
        self.assertEqual(len(DC.comparer(cfg, ref)), 1)
        conv["reseau_commutation"] = "SW"
        conv["cond_sortie"] = "CE1"                    # toute autre clé est comparée
        diff = DC.comparer(cfg, ref)
        self.assertEqual(len(diff), 1, diff)
        self.assertIn("cond_sortie", diff[0])

    def lancer(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = DC.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_ligne_de_commande(self):
        with tempfile.TemporaryDirectory() as tmp:
            e, c = os.path.join(tmp, "e.json"), os.path.join(tmp, "c.json")
            with open(e, "w", encoding="utf-8") as f:
                json.dump(demi_pont(), f)
            code, out, err = self.lancer(e, "--sortie", c)
            self.assertEqual(code, 0, err)
            self.assertNotIn(tmp, out + err)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(formats.main(["valider", c]), 0)
            with open(c, encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(un(cfg)["reseau_commutation"], "SW")
            r = os.path.join(tmp, "r.json")
            cfg["convertisseurs"][0]["cond_sortie"] = ["CS2"]
            with open(r, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            code, out, _ = self.lancer(e, "--sortie", c, "--comparer", r)
            self.assertEqual(code, 1)
            self.assertIn("cond_sortie", out)
            with open(r, "w", encoding="utf-8") as f:                       # référence sans nom ni nœud
                json.dump({"convertisseurs": [{"haut": "QH", "bas": "QB", "cond_sortie": ["CS2"]}]}, f)
            code, out, _ = self.lancer(e, "--sortie", c, "--comparer", r)
            self.assertEqual(code, 1)
            self.assertIn("abaisseur VIN -> VOUT / cond_sortie : référence ['CS2'], déduit ['CS1']", out)
            self.assertNotIn("None", out)
            x = os.path.join(tmp, "x.json")
            mauvaise = demi_pont()
            mauvaise["composants"][0]["pastilles"][0]["largeur"] = "2"
            with open(x, "w", encoding="utf-8") as f:
                json.dump(mauvaise, f)
            code, _, err = self.lancer(x, "--sortie", c)
            self.assertEqual(code, 2, err)
            self.assertIn("x.json : composants[0].pastilles[0].largeur : nombre attendu", err)
            u = os.path.join(tmp, "u.json")
            with open(u, "w", encoding="utf-8") as f:
                f.write('{"format": "config_carte/2", "convertisseurs": [],}')
            code, _, err = self.lancer(e, "--sortie", c, "--config", u)
            self.assertEqual(code, 2)
            self.assertIn("u.json : JSON invalide, ligne 1", err)
            with open(u, "w", encoding="utf-8") as f:
                json.dump({"format": "config_carte/2", "convertisseurs": [{"reseau_commutation": "SW",
                                                                            "inductance": "L99"}]}, f)
            code, _, err = self.lancer(e, "--sortie", c, "--config", u)
            self.assertEqual(code, 2)
            self.assertIn("u.json : convertisseurs[0].inductance : pièce L99 inconnue", err)
            self.assertNotIn(tmp, err)
            k = os.path.join(tmp, "k.json")
            with open(k, "w", encoding="utf-8") as f:
                json.dump({"format": "contraintes/2", "convertisseurs": {
                    "supprimer": [{"reseau_commutation": "SW"}]}}, f)
            code, out, err = self.lancer(e, "--sortie", c, "--contraintes", k)
            self.assertEqual(code, 0, err)
            self.assertIn("0 convertisseur", out)
            self.assertEqual(self.lancer(e)[0], 2)                            # --sortie manquante
            self.assertEqual(self.lancer(e, "--sortie", c, "--inconnue", "x")[0], 2)
            self.assertEqual(self.lancer(os.path.join(tmp, "absent.json"), "--sortie", c)[0], 2)


# ---------------------------------------------------------------------------------------------------------------------
# METROID rev3 (importeur Altium v2)
# ---------------------------------------------------------------------------------------------------------------------

# config_carte.json écrit à la main (v1), sans « eloignement » (contrainte de placement, migrée vers les contraintes)
CONFIG_METROID = {"convertisseurs": [{
    "nom": "hacheur 12 V -> 8 V", "haut": "M1", "bas": "M2", "pilote": "U10", "inductance": "L1",
    "cond_entree": ["C7", "C15"], "cond_bulk": "C17", "cond_sortie": "C20", "cond_pilote": "C15",
    "reseau_entree": "+12V", "reseau_masse": "GND", "reseau_sortie": "+8V", "reseau_commutation": "",
    "reseaux_courts": ["NetC19_2", "NetC16_2", "NetC18_2", "NetM1_4", "NetM2_4"],
    "reseaux_sensibles": ["NetC21_1", "NetC22_2", "NetR25_2"]}], "sans_piste_dessous": ["L1"]}


@unittest.skipUnless(os.path.exists(GABARIT), "carte rev3 absente de la racine du dépôt")
class TestMetroid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entree = IC.importer(GABARIT)          # importeur Altium v2 (B-ET5)
        cls.cfg = DC.deduire(cls.entree)

    def test_natures_des_circuits(self):
        """Les circuits décrits « shunt regulator », « switched capacitor », « LED display driver » restent des
        circuits ; les afficheurs à LED sont des interfaces."""
        natures, _ = DC.classer(self.entree)
        self.assertEqual({d: n for d, n in natures.items() if d.startswith("U") and n != "circuit"}, {})
        self.assertEqual({natures[d] for d in natures if d.startswith("DS")}, {"interface"})

    def test_convertisseur(self):
        c = un(self.cfg)
        self.assertEqual((c["haut"], c["bas"], c["pilote"], c["inductance"]), ("M1", "M2", "U10", "L1"))
        self.assertEqual((c["topologie"], c["commutation"], c["reseau_entree"], c["reseau_sortie"]),
                         ("abaisseur", "synchrone", "+12V", "+8V"))
        self.assertEqual((c["reseau_commutation"], c["reseau_masse"]), ("NetC19_1", "GND"))
        self.assertEqual(c["boucle_chaude"], {"condensateurs": ["C15", "C7"], "reseau_rail": "+12V",
                                              "reseau_masse": "GND"})
        self.assertEqual(DC.masses(self.entree), (["GND"], []))
        self.assertEqual(formats.valider_config_carte(self.cfg), [])

    def test_comparer_config_ecrite_a_la_main(self):
        self.assertEqual(DC.comparer(self.cfg, CONFIG_METROID), [])
        self.assertEqual(self.cfg["sans_piste_dessous"], ["L1"])

    def test_config_v1_relue(self):
        """Le fichier v1 écrit à la main (avec « eloignement ») relu par --config ne change que le libellé."""
        v1 = json.loads(json.dumps(CONFIG_METROID))
        v1["convertisseurs"][0]["eloignement"] = [{"broches": ["U25_VOLT1.27"], "distance_mm": 30.0}]
        cfg = DC.deduire(self.entree, {"config": v1})
        self.assertEqual(DC.comparer(cfg, CONFIG_METROID), [])
        self.assertEqual(un(cfg)["origine"], "modifie")
        self.assertIn("(nom)", cfg["surcharges_appliquees"][0])
        self.assertEqual(cfg["eloignements"], [{"type": "dure", "objets": ["U25_VOLT1.27"], "reference": ["M1"],
                                                "distance_mm": 30.0}])


@unittest.skipUnless(os.path.exists(GABARIT), "carte rev3 absente de la racine du dépôt")
class TestRegions(unittest.TestCase):
    def test_regions_u10(self):
        reg = RE.regions_par_composant(PcbDoc.ouvrir(GABARIT))
        self.assertEqual(list(reg), ["U10"])
        self.assertEqual(len(reg["U10"]), 4)
        for r in reg["U10"]:
            self.assertEqual(r["face"], "composant")
            self.assertEqual(r["pastille"], "11")
            self.assertNotIn("reseau", r)
            xs = sorted(abs(p[0]) for p in r["points"])
            ys = sorted(abs(p[1]) for p in r["points"])
            self.assertAlmostEqual(xs[0], 0.25, places=3)
            self.assertAlmostEqual(xs[-1], 0.5, places=3)
            self.assertAlmostEqual(ys[0], 1.15, places=3)
            self.assertAlmostEqual(ys[-1], 1.7, places=3)

    def test_arc_discretise(self):
        pts = RE._arc(0, 0, 1000 / 0.0254 * 1, 0, 90)  # grand rayon
        self.assertGreater(len(pts), 10)
        r = 2.0 / 0.0254  # 2 mm
        pts = RE._arc(0, 0, r, 0, 180)
        for a, b in zip(pts, pts[1:]):
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            self.assertLess((r - math.hypot(*m)) * 0.0254, 0.01)


if __name__ == "__main__":
    unittest.main()
