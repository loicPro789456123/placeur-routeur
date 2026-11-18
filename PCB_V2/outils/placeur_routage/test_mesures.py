# -*- coding: utf-8 -*-
"""Tests de geom_commune.py et de mesures.py (lot C, étape C1).

    python test_mesures.py -v [--references DOSSIER]

--references DOSSIER : dossier local des références figées ; il contient ``ei_1038/`` (placements_routage.json et
routage_detaille.json de la graine ei_1038). Sans cette option, le test ei_1038 est sauté (et compté).

Cas METROID : un adaptateur de test convertit en mémoire les données v1 de 9b5b5a3 (donnees_placeur.json v1,
pastilles réelles, régions d'empreinte, cuivre manuel du convertisseur) en donnees_placeur/2. Références :
copie figée rev4 (sorties/conjoint/, coût commun 10328,5 selon le protocole historique, cuivre du bloc présent)
et ei_1038 (12398,4). Quand ces fichiers v1 auront été retirés (lot B, jalon B2), les tests METROID de ce fichier
sont sautés et refaits sur exemples/metroid_v2/ (étape C9).
"""
import contextlib
import io
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import geom_commune as gc  # noqa: E402
import mesures  # noqa: E402
import mesure_convertisseurs as mconv  # noqa: E402
import proprete  # noqa: E402
import formats  # noqa: E402

MIL = gc.MIL
REFERENCES = None          # dossier des références figées (--references)


# ---------------------------------------------------------------------------------------------------------------------
# Fabrication de données synthétiques (donnees_placeur/2, routage/2)
# ---------------------------------------------------------------------------------------------------------------------
def reseau(nom, role="signal", largeurs=(0.2, 0.25, None)):
    return {"nom": nom, "classe": "", "role": role, "masse": False, "sensibilite": "normale",
            "largeurs_mm": list(largeurs), "descente_large": False, "isolement_mm": 0.2,
            "classes_isolement": {"piste": 0, "via": 0, "surface": 0},
            "via": {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.3, "diametre_max": None, "trou_min": 0.2,
                    "trou_max": None},
            "poids": 1.0, "priorite": None, "priorite_routage": 0, "couche_preferee": None, "couches_permises": [],
            "plan_couches": [], "remplissages": [], "alimentation_schema": False, "paire": None}


def couche(nom, rang, role="routage", zones=()):
    return {"nom": nom, "rang": rang, "role": role, "zones_plan": list(zones), "epaisseur_cuivre_mm": 0.035,
            "direction_preferee": None}


def donnees(couches=("Haut", "Bas"), defaut=0.2, reseaux=(), composants=(), cuivre_fixe=(), trous=(), zones=None):
    cs = [couche(n, i) for i, n in enumerate(couches)]
    for rang, z in (zones or {}).items():
        cs[rang]["role"] = "plan"
        cs[rang]["zones_plan"] = z
    return {"format": "donnees_placeur/2",
            "source": {"entree": "entree_carte.json", "sha256_entree": "", "contraintes": None,
                       "config": "config_carte.json", "controle_gabarit": None, "construit_par": "test"},
            "unite": "mm",
            "carte": {"contour": [[0, 0], [50, 0], [50, 40], [0, 40]], "boite": [0, 0, 50, 40], "decoupes": [],
                      "remplissages": [], "marge_bord_mm": 1.0, "marge_decoupe_mm": 1.0, "garde_bord_mm": 0.3,
                      "garde_decoupe_mm": 0.3},
            "couches": cs, "trous": list(trous), "obstacles_placement": [], "zones_imposees": [],
            "zones_routage_interdites": [], "cuivre_fixe": list(cuivre_fixe), "composants": list(composants),
            "reseaux": list(reseaux),
            "regles": {"isolement": {"defaut": defaut, "classes": ["autres"], "matrice": [[defaut]],
                                     "classe_reference": 0},
                       "via": {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.3, "diametre_max": None,
                               "trou_min": 0.2, "trou_max": None},
                       "trou_trou_mm": 0.254, "garde_trou_mm": 0.3,
                       "placement": {"grille_mm": 0.127, "marge_piece_mm": 0.25, "marge_traversante_mm": 0.6},
                       "via_dans_pastille_meme_reseau": False,
                       "connexion_plans": {"isolement": 0.5, "connexion": "relief"}},
            "convertisseurs": [], "sans_piste_dessous": [], "decouplages": [], "eloignements": [], "charges": [],
            "groupes": [], "paires": [], "au_bord": [], "masses": [], "parametres_deduction": {}}


def pastille(nom, reseau_, lx, ly, forme="rect", largeur=1.0, hauteur=1.0, rotation=0.0, couches=("Haut",),
             rayon_coin=0.0, points=(), trou=None, pile=None, ident=None):
    return {"id": ident, "nom": nom, "reseau": reseau_, "lx": lx, "ly": ly, "forme": forme, "largeur": largeur,
            "hauteur": hauteur, "rotation": rotation, "rayon_coin": rayon_coin, "points": [list(p) for p in points],
            "couches": list(couches), "face": "composant", "trou": trou, "traversante": trou is not None,
            "expansion_masque": 0.0, "pile": pile, "via_dans_pastille_permis": False, "classe_isolement": 0,
            "hw": largeur / 2, "hh": hauteur / 2, "th": trou is not None}


def piece(nom, pastilles=(), pose=(10.0, 10.0, 0.0, False), pose_fichier=None, regions=(), vias=(), virtuel=False):
    pastilles = list(pastilles)
    for k, p in enumerate(pastilles):
        if not p.get("id"):
            p["id"] = "%s#%d" % (nom, k)
    return {"nom": nom, "empreinte": "E", "nature": "autre", "genre": "standard", "valeur": "", "hauteur": 1.0,
            "statut": "mobile", "raison_statut": "libre", "virtuel": virtuel, "lbb": [-1, -1, 1, 1],
            "pastilles": pastilles, "regions": list(regions), "vias": list(vias), "zones_interdites": [],
            "decoupes": [], "pose_actuelle": list(pose_fichier or pose), "pose_cible": list(pose),
            "orientations": [[0.0, False]], "regle": "", "pas_fin": False, "matrice": False, "pas_min_mm": None}


def piste(x1, y1, x2, y2, largeur, couche_, net):
    """Piste du routage (coordonnées données en mm, écrites en mil)."""
    return {"x1": x1 / MIL, "y1": y1 / MIL, "x2": x2 / MIL, "y2": y2 / MIL, "width": largeur / MIL,
            "layer": couche_, "net_name": net}


def via(x, y, diametre, trou, net):
    return {"x": x / MIL, "y": y / MIL, "size": diametre / MIL, "hole_size": trou / MIL, "net": net}


def routage(pistes=(), vias=(), couches=("Haut", "Bas")):
    return {"format": "routage/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour",
            "couches_routage": list(couches), "pistes": list(pistes), "vias": list(vias), "surfaces": [],
            "amorces_pas_fin": [], "derogations": [], "reseaux": [], "echecs": []}


def placement_v2(*entrees):
    return {"format": "placements/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour",
            "pieces": [{"designator": d, "side": "bottom" if b else "top", "x": x / MIL, "y": y / MIL,
                        "rotation": r, "statut": "mobile"} for d, x, y, r, b in entrees]}


def cout_historique(longueur, vias, retreci, r_puiss, coudes, ecart_moy):
    """Formule de campagnes.py (9b5b5a3, lignes 56-58), écrite à l'identique."""
    return (longueur + 1.5 * vias + 2.0 * retreci + 3.0 * r_puiss + 0.1 * coudes
            + 1000.0 * max(0.0, 0.6 - ecart_moy))


# ---------------------------------------------------------------------------------------------------------------------
# Adaptateur METROID : données v1 de 9b5b5a3 -> donnees_placeur/2 (en mémoire, pour les tests seulement)
# ---------------------------------------------------------------------------------------------------------------------
FICHIERS_V1 = ("donnees_placeur.json", "pastilles_reelles.json", "cuivre_bloc_hacheur.json")
ROLES_CLASSES_V1 = {"ENTREE_12V": "puissance", "PUISSANCE_8V": "puissance", "CANAL_2A": "puissance",
                    "RAILS": "alimentation", "COMMUTATION": "commutation"}
COUCHES_V1 = ("TopLayer", "MidLayer1", "MidLayer2", "BottomLayer")


def donnees_v1_presentes():
    return all(os.path.exists(os.path.join(ICI, f)) for f in FICHIERS_V1)


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def adapter_metroid_v1(routages=(), avec_cuivre_bloc=True):
    """Données v1 de 9b5b5a3 converties en donnees_placeur/2 : pastilles réelles (formes et cuivre exacts), régions
    d'U10, trous de fixation (cuivre de 6,35 mm), cuivre manuel du convertisseur en cuivre fixe (protocole historique :
    ``campagnes.evaluer(..., libre=False)``), plan GND sur MidLayer1, rôles tirés des classes."""
    d1 = _lire(os.path.join(ICI, "donnees_placeur.json"))
    pr = _lire(os.path.join(ICI, "pastilles_reelles.json"))
    cb = _lire(os.path.join(ICI, "cuivre_bloc_hacheur.json")) if avec_cuivre_bloc else None
    larg = d1["regles"]["largeurs"]
    classes = d1["classes"]
    nets = set()
    w, h = d1["contour"]["w"], d1["contour"]["h"]
    contour = [[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]]
    tout = list(COUCHES_V1)
    composants = []
    for c in d1["composants"]:
        nom = c["nom"]
        dessous_fichier = bool(c["pose_actuelle"][3])
        face = ["BottomLayer"] if dessous_fichier else ["TopLayer"]
        pads = []
        par_nom = {}
        for k, p in enumerate(c["pastilles"]):
            q = pr["pieces"][nom][k]
            th = bool(q["th"])
            net = p["reseau"] or ""
            nets.add(net)
            par_nom.setdefault(p["nom"], (k, net))
            pads.append({"id": "%s#%d" % (nom, k), "nom": p["nom"], "reseau": net, "lx": p["lx"], "ly": p["ly"],
                         "forme": q["forme"], "largeur": 2 * q["hw"], "hauteur": 2 * q["hh"], "rotation": 0.0,
                         "rayon_coin": 0.0, "points": [], "couches": tout if th else face,
                         "face": "toutes" if th else "composant",
                         "trou": ({"diametre": q["trou"], "forme": "rond", "longueur": 0.0, "rotation": 0.0,
                                   "decalage": [0.0, 0.0], "metallise": True} if th and q["trou"] > 0 else None),
                         "traversante": th, "expansion_masque": 0.0, "pile": None,
                         "via_dans_pastille_permis": th, "classe_isolement": 0, "hw": q["hw"], "hh": q["hh"],
                         "th": th})
        regions = []
        for k, r in enumerate(c.get("regions") or []):
            kp, net = par_nom.get(r.get("pastille"), (None, r.get("reseau") or ""))
            couches_r = face if r.get("face", "composant") == "composant" else (
                (["TopLayer"] if dessous_fichier else ["BottomLayer"]) if r.get("face") == "opposee" else tout)
            regions.append({"id": "%s/R%d" % (nom, k), "couches": couches_r, "face": r.get("face", "composant"),
                            "pastille": None if kp is None else "%s#%d" % (nom, kp), "reseau": net, "liaison": None,
                            "points": r["points"], "classe_isolement": 0})
        pose = [c["pose_actuelle"][0], c["pose_actuelle"][1], float(c["pose_actuelle"][2]), dessous_fichier]
        composants.append({"nom": nom, "empreinte": c.get("empreinte", ""), "nature": "autre", "genre": "standard",
                           "valeur": "", "hauteur": c.get("hauteur", 0.0),
                           "statut": "fixe" if c["statut"] == "fixe" else "mobile", "raison_statut": c["statut"],
                           "virtuel": False, "lbb": c["lbb"], "pastilles": pads, "regions": regions, "vias": [],
                           "zones_interdites": [], "decoupes": [], "pose_actuelle": pose, "pose_cible": list(pose),
                           "orientations": [[float(o[0]), bool(o[1])] for o in c.get("orientations", [])],
                           "regle": c.get("regle", ""), "pas_fin": False, "matrice": False, "pas_min_mm": None})
    trous = [{"id": "T%d" % (k + 1), "x": q["x"], "y": q["y"], "rotation": 0.0,
              "trou": {"diametre": q["trou"], "forme": "rond", "longueur": 0.0, "rotation": 0.0,
                       "decalage": [0.0, 0.0]},
              "metallise": True, "diametre_cuivre": q["d"], "rayon_interdit_mm": q["d"] / 2 + 0.6,
              "classe_isolement": 0, "piece": None} for k, q in enumerate(pr["libres"])]
    cuivre_fixe = []
    if cb is not None:
        for x1, y1, x2, y2, lg, cou, net in cb["pistes"]:
            cuivre_fixe.append({"id": "F%d" % len(cuivre_fixe), "type": "piste", "reseau": net,
                                "couches": ["TopLayer" if cou == "T" else "BottomLayer"],
                                "x1": x1 * MIL, "y1": y1 * MIL, "x2": x2 * MIL, "y2": y2 * MIL, "largeur": lg * MIL,
                                "classe_isolement": 0})
            nets.add(net)
        for x, y, d, t, net in cb["vias"]:
            cuivre_fixe.append({"id": "F%d" % len(cuivre_fixe), "type": "via", "reseau": net, "couches": ["toutes"],
                                "x": x * MIL, "y": y * MIL, "diametre": d * MIL, "trou": t * MIL,
                                "classe_isolement": 0})
            nets.add(net)
        for cou, net, x0, y0, x1, y1 in cb["regions"]:
            cuivre_fixe.append({"id": "F%d" % len(cuivre_fixe), "type": "region", "reseau": net,
                                "couches": ["TopLayer" if cou == "T" else "BottomLayer"],
                                "points": [[x0 * MIL, y0 * MIL], [x1 * MIL, y0 * MIL], [x1 * MIL, y1 * MIL],
                                           [x0 * MIL, y1 * MIL]], "trous": [], "classe_isolement": 0})
            nets.add(net)
    for rt in routages:
        nets.update(s["net_name"] for s in rt["pistes"])
        nets.update(v["net"] for v in rt["vias"])
    nets.discard("")
    reseaux = []
    for net in sorted(nets):
        cl = classes.get(net, "defaut")
        role = "plan" if net in d1["reseaux_plans"] else ROLES_CLASSES_V1.get(cl, "signal")
        reseaux.append(dict(reseau(net, role, larg.get(cl, larg["defaut"])), classe=cl if cl != "defaut" else "",
                            masse=net in d1["reseaux_plans"]))
    d = donnees(couches=COUCHES_V1, defaut=d1["regles"]["isolement"]["defaut"], reseaux=reseaux,
                composants=composants, cuivre_fixe=cuivre_fixe, trous=trous,
                zones={1: [{"reseau": n, "contour": contour, "trous": []} for n in d1["reseaux_plans"]]})
    d["carte"]["contour"] = contour
    d["carte"]["boite"] = [0.0, 0.0, w, h]
    d["masses"] = list(d1["reseaux_plans"])
    return d


# ---------------------------------------------------------------------------------------------------------------------
# Formule
# ---------------------------------------------------------------------------------------------------------------------
class TestFormule(unittest.TestCase):
    def test_termes_connus(self):
        t = {"longueur_mm": 100.0, "vias": 10, "retreci_mm": 5.0, "resistance_puissance_mohm": 20.0, "coudes": 30,
             "ecart_moyen_mm": 0.5}
        # 100 + 15 + 10 + 60 + 3 + 1000 x (0,6 - 0,5)
        self.assertAlmostEqual(gc.cout_commun(t, 0.2), 288.0, places=9)

    def test_ecart_au_dessus_du_seuil(self):
        t = {"longueur_mm": 10.0, "vias": 0, "retreci_mm": 0.0, "resistance_puissance_mohm": 0.0, "coudes": 0,
             "ecart_moyen_mm": 0.7}
        self.assertEqual(gc.cout_commun(t, 0.2), 10.0)

    def test_seuil_proportionnel_a_l_isolement(self):
        t = {"longueur_mm": 0.0, "vias": 0, "retreci_mm": 0.0, "resistance_puissance_mohm": 0.0, "coudes": 0,
             "ecart_moyen_mm": 0.25}
        self.assertAlmostEqual(gc.cout_commun(t, 0.1), 50.0, places=9)     # s = 0,3
        self.assertAlmostEqual(gc.cout_commun(t, 0.3), 650.0, places=9)    # s = 0,9

    def test_egale_formule_historique_sur_metroid(self):
        """cout_commun/2 avec isolement.defaut = 0,2 (s = 0,6) = cout_commun/1 de campagnes.py (9b5b5a3)."""
        alea = random.Random(20261118)
        for _ in range(500):
            t = {"longueur_mm": alea.uniform(0, 9000), "vias": alea.randrange(0, 900),
                 "retreci_mm": alea.uniform(0, 600), "resistance_puissance_mohm": alea.uniform(0, 2000),
                 "coudes": alea.randrange(0, 3000), "ecart_moyen_mm": alea.uniform(0.2, 0.9)}
            attendu = cout_historique(t["longueur_mm"], t["vias"], t["retreci_mm"], t["resistance_puissance_mohm"],
                                      t["coudes"], t["ecart_moyen_mm"])
            self.assertAlmostEqual(gc.cout_commun(t, 0.2), attendu, delta=1e-9 * max(1.0, attendu))


# ---------------------------------------------------------------------------------------------------------------------
# Coudes
# ---------------------------------------------------------------------------------------------------------------------
class TestCoudes(unittest.TestCase):
    def compter(self, segments):
        return gc.compter_coudes(segments)

    def test_droit(self):
        self.assertEqual(self.compter([("A", "H", 0, 0, 5, 0), ("A", "H", 5, 0, 9, 0)]), (0, 0, 0))

    def test_angle_droit(self):
        self.assertEqual(self.compter([("A", "H", 0, 0, 5, 0), ("A", "H", 5, 0, 5, 4)]), (1, 1, 0))

    def test_45_degres(self):
        self.assertEqual(self.compter([("A", "H", 0, 0, 5, 0), ("A", "H", 5, 0, 8, 3)]), (1, 0, 0))

    def test_aigu(self):
        self.assertEqual(self.compter([("A", "H", 0, 0, 10, 0), ("A", "H", 10, 0, 1, 1)]), (1, 0, 1))

    def test_trois_extremites(self):
        segs = [("A", "H", 0, 0, 5, 0), ("A", "H", 5, 0, 5, 4), ("A", "H", 5, 0, 5, -4)]
        self.assertEqual(self.compter(segs), (0, 0, 0))

    def test_reseaux_ou_couches_differents(self):
        self.assertEqual(self.compter([("A", "H", 0, 0, 5, 0), ("B", "H", 5, 0, 5, 4)]), (0, 0, 0))
        self.assertEqual(self.compter([("A", "H", 0, 0, 5, 0), ("A", "B", 5, 0, 5, 4)]), (0, 0, 0))

    def test_point_arrondi_au_millieme(self):
        self.assertEqual(self.compter([("A", "H", 0, 0, 5, 0), ("A", "H", 5.0004, 0, 5.0004, 4)]), (1, 1, 0))
        self.assertEqual(self.compter([("A", "H", 0, 0, 5, 0), ("A", "H", 5.0006, 0, 5.0006, 4)]), (0, 0, 0))

    def test_piste_de_longueur_nulle_ignoree(self):
        self.assertEqual(self.compter([("A", "H", 5, 0, 5, 0)]), (0, 0, 0))

    def test_arrondi_egalite_au_pair(self):
        # convention 12 du corpus du lot E : égalité au pair (round de Python, round_ties_even de Rust)
        self.assertEqual(gc.arrondi_millieme(0.0025), 2)
        self.assertEqual(gc.arrondi_millieme(-0.0025), -2)
        self.assertEqual(gc.arrondi_millieme(0.0035), 4)
        self.assertEqual(gc.arrondi_millieme(0.0004999), 0)
        self.assertEqual(gc.arrondi_millieme(1.2344), 1234)
        self.assertEqual(gc.arrondi_millieme(-1.2346), -1235)

    def test_coudes_par_la_scene(self):
        d = donnees(reseaux=[reseau("A")])
        r = routage([piste(1, 1, 5, 1, 0.25, "Haut", "A"), piste(5, 1, 5, 6, 0.25, "Haut", "A")])
        m = gc.mesures_routage(gc.poser(d, placement_v2(), r))
        self.assertEqual((m["coudes"], m["coudes_90"], m["angles_aigus"]), (1, 1, 0))


# ---------------------------------------------------------------------------------------------------------------------
# Écart (§4.7)
# ---------------------------------------------------------------------------------------------------------------------
class TestEcart(unittest.TestCase):
    def ecarts(self, d, pistes, vias=(), placement=None):
        sc = gc.poser(d, placement if placement is not None else placement_v2(), routage(pistes, vias,
                                                                                          [c["nom"] for c in
                                                                                           d["couches"]]))
        e = gc.ecarts_pistes(sc)
        return [e.get(k) for k in sc.pistes], gc.mesures_routage(sc, e)

    def test_aucune_piste(self):
        e, m = self.ecarts(donnees(), [])
        self.assertEqual(e, [])
        self.assertEqual(m["ecart_moyen_mm"], 0.0)
        self.assertIsNone(m["ecart_min_mm"])

    def test_piste_sans_voisin_exclue(self):
        d = donnees(reseaux=[reseau("A")])
        e, m = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A")])
        self.assertEqual(e, [None])
        self.assertEqual(m["ecart_moyen_mm"], 0.0)

    def test_deux_pistes_paralleles(self):
        d = donnees(reseaux=[reseau("A"), reseau("B")])
        e, m = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A"), piste(2, 10.5, 8, 10.5, 0.2, "Haut", "B")])
        self.assertAlmostEqual(e[0], 0.3, places=9)
        self.assertAlmostEqual(e[1], 0.3, places=9)
        self.assertAlmostEqual(m["ecart_moyen_mm"], 0.3, places=9)
        self.assertAlmostEqual(m["ecart_min_mm"], 0.3, places=9)

    def test_meme_reseau_ou_autre_couche_pas_voisins(self):
        d = donnees(reseaux=[reseau("A"), reseau("B")])
        e, _ = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A"), piste(2, 10.5, 8, 10.5, 0.2, "Haut", "A")])
        self.assertEqual(e, [None, None])
        e, _ = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A"), piste(2, 10.5, 8, 10.5, 0.2, "Bas", "B")])
        self.assertEqual(e, [None, None])

    def test_recouvrement_negatif(self):
        d = donnees(reseaux=[reseau("A"), reseau("B")])
        e, _ = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A"), piste(5, 7, 5, 13, 0.4, "Haut", "B")])
        self.assertAlmostEqual(e[0], -0.3, places=9)
        self.assertAlmostEqual(e[1], -0.3, places=9)

    def test_plus_petit_de_plusieurs_recouvrements(self):
        # A croisée par B (l = 0,1 : -0,15) puis par C (l = 0,6 : -0,4) : l'écart de A est le plus petit, -0,4,
        # quel que soit l'ordre des objets (un premier recouvrement n'élague pas les voisins dont la boîte touche)
        d = donnees(reseaux=[reseau("A"), reseau("B"), reseau("C")])
        a = piste(2, 10, 8, 10, 0.2, "Haut", "A")
        b = piste(4, 7, 4, 13, 0.1, "Haut", "B")
        c = piste(6, 7, 6, 13, 0.6, "Haut", "C")
        for ordre, attendu in (((a, b, c), [-0.4, -0.15, -0.4]), ((a, c, b), [-0.4, -0.4, -0.15])):
            e, m = self.ecarts(d, list(ordre))
            for v, w in zip(e, attendu):
                self.assertAlmostEqual(v, w, places=9)
            self.assertAlmostEqual(m["ecart_min_mm"], -0.4, places=9)
            self.assertAlmostEqual(m["ecart_moyen_mm"], (-0.4 - 0.4 - 0.15) / 3.0, places=9)
        # pastille de B qui recouvre A de 0,05, puis piste de C qui la croise : -0,3
        p = piece("U1", [pastille("1", "B", 0.0, 0.0, largeur=1.0, hauteur=1.0)], pose=(3.0, 10.55, 0.0, False))
        d = donnees(reseaux=[reseau("A"), reseau("B"), reseau("C")], composants=[p])
        e, _ = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A"), piste(5, 7, 5, 13, 0.4, "Haut", "C")],
                           placement=placement_v2(("U1", 3.0, 10.55, 0.0, False)))
        self.assertAlmostEqual(e[0], -0.3, places=9)

    def test_plafond(self):
        # isolement.defaut 0,1 : p = 0,5 ; écart réel 0,8 (les boîtes élargies partagent la case j = 10)
        d = donnees(defaut=0.1, reseaux=[reseau("A"), reseau("B")])
        e, m = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A"), piste(2, 11, 8, 11, 0.2, "Haut", "B")])
        self.assertAlmostEqual(e[0], 0.8, places=9)
        self.assertAlmostEqual(m["ecart_moyen_mm"], 0.5, places=12)

    def test_fenetre_des_cases(self):
        d = donnees(reseaux=[reseau("A"), reseau("B")])
        # A : boîte y [10,4 ; 10,6], élargie [10,1 ; 10,9] -> case 10 ; B à y = 11,6 : [11,2 ; 12,0] -> cases 11, 12
        e, _ = self.ecarts(d, [piste(2, 10.5, 8, 10.5, 0.2, "Haut", "A"), piste(2, 11.6, 8, 11.6, 0.2, "Haut", "B")])
        self.assertEqual(e, [None, None])
        # B à y = 11,3 : [10,9 ; 11,7] -> cases 10 et 11 : voisines, écart 0,6
        e, _ = self.ecarts(d, [piste(2, 10.5, 8, 10.5, 0.2, "Haut", "A"), piste(2, 11.3, 8, 11.3, 0.2, "Haut", "B")])
        self.assertAlmostEqual(e[0], 0.6, places=9)

    def test_pastille_et_trou_sans_reseau(self):
        p = piece("U1", [pastille("1", "B", 0.0, 0.0, largeur=1.0, hauteur=1.0)], pose=(5.0, 11.0, 0.0, False))
        trou = {"id": "T1", "x": 20.0, "y": 11.0, "rotation": 0.0,
                "trou": {"diametre": 1.0, "forme": "rond", "longueur": 0.0, "rotation": 0.0, "decalage": [0, 0]},
                "metallise": False, "diametre_cuivre": 2.0, "rayon_interdit_mm": 1.6, "classe_isolement": 0,
                "piece": None}
        d = donnees(reseaux=[reseau("A"), reseau("B")], composants=[p], trous=[trou])
        # pastille 1 x 1 centrée en (5 ; 11) : bord bas y = 10,5 ; piste y = 10, largeur 0,2 -> écart 0,4
        e, _ = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A")])
        self.assertAlmostEqual(e[0], 0.4, places=9)
        # cuivre de trou (sans réseau, toutes couches) : disque de rayon 1 en (20 ; 11) -> écart 1 - 0,1 - 0 = ...
        e, _ = self.ecarts(d, [piste(18, 9.5, 22, 9.5, 0.2, "Bas", "A")])
        self.assertAlmostEqual(e[0], 1.5 - 1.0 - 0.1, places=9)

    def test_region_de_liaison_et_zone_de_plan(self):
        reg = {"id": "N1/R0", "couches": ["Haut"], "face": "composant", "pastille": None, "reseau": "",
               "liaison": ["A", "B"], "points": [[-1, -0.5], [1, -0.5], [1, 0.5], [-1, 0.5]], "classe_isolement": 0}
        p = piece("N1", [], pose=(5.0, 11.0, 0.0, False), regions=[reg])
        zone = {"reseau": "C", "contour": [[0, 0], [50, 0], [50, 40], [0, 40]], "trous": []}
        d = donnees(couches=("Haut", "Plan", "Bas"), reseaux=[reseau("A"), reseau("B"), reseau("C"), reseau("D")],
                    composants=[p], zones={1: [zone]})
        e, _ = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A")])
        self.assertEqual(e, [None])                       # la région lie A : pas voisine d'une piste de A
        e, _ = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "D")])
        self.assertAlmostEqual(e[0], 0.4, places=9)       # voisine d'une piste de D
        sc = gc.poser(d, placement_v2(), routage([], [], ["Haut", "Plan", "Bas"]))
        self.assertEqual([o.id for o in sc.objets if o.genre == gc.ZONE_PLAN], ["Z:Plan/0"])

    def test_via_voisin(self):
        d = donnees(reseaux=[reseau("A"), reseau("B")])
        e, m = self.ecarts(d, [piste(2, 10, 8, 10, 0.2, "Haut", "A")], [via(5, 10.8, 0.6, 0.3, "B")])
        self.assertAlmostEqual(e[0], 0.8 - 0.1 - 0.3, places=9)
        self.assertEqual(m["vias"], 1)


# ---------------------------------------------------------------------------------------------------------------------
# Rétréci, résistance, longueur
# ---------------------------------------------------------------------------------------------------------------------
class TestRetreciResistance(unittest.TestCase):
    def test_retreci(self):
        d = donnees(reseaux=[reseau("A", largeurs=(0.2, 0.3, None)), reseau("B", largeurs=(0.4, 0.3, None))])
        r = routage([piste(0, 0, 10, 0, 0.25, "Haut", "A"),       # 0,25 < 0,3 - 1e-3 : rétrécie (10 mm)
                     piste(0, 1, 4, 1, 0.2995, "Haut", "A"),      # 0,2995 >= 0,299 : non
                     piste(0, 2, 3, 2, 0.35, "Haut", "B"),        # préférée = max(0,3 ; 0,4) = 0,4 : rétrécie (3 mm)
                     piste(0, 3, 7, 3, 0.1, "Haut", "X")])        # réseau inconnu : pas de préférée
        b = gc.bilan_pistes(d, r)
        self.assertAlmostEqual(b["retreci_mm"], 13.0, places=9)
        self.assertAlmostEqual(b["longueur_mm"], 24.0, places=9)

    def test_resistance(self):
        d = donnees(reseaux=[reseau("P", "puissance"), reseau("Q", "alimentation"), reseau("S", "signal"),
                             reseau("C", "commutation")])
        r = routage([piste(0, 0, 10, 0, 0.5, "Haut", "P"),        # 0,5 x 10 / 0,5 = 10
                     piste(0, 1, 4, 1, 0.25, "Haut", "Q"),        # 0,5 x 4 / 0,25 = 8
                     piste(0, 2, 9, 2, 0.25, "Haut", "S"),
                     piste(0, 3, 9, 3, 0.25, "Haut", "C")])
        self.assertAlmostEqual(gc.bilan_pistes(d, r)["resistance_puissance_mohm"], 18.0, places=9)
        m = gc.mesures_routage(gc.poser(d, placement_v2(), r))
        self.assertAlmostEqual(m["resistance_puissance_mohm"], 18.0, places=9)
        self.assertAlmostEqual(m["longueur_pistes_mm"], 32.0, places=9)


# ---------------------------------------------------------------------------------------------------------------------
# Formes, poses et couches
# ---------------------------------------------------------------------------------------------------------------------
class TestFormes(unittest.TestCase):
    def test_rectangle_a_45_degres(self):
        f = gc.forme_parametrique("rect", 2.0, 1.0, 0.0, 0.0, 0.0, 45.0)
        r2 = math.sqrt(2.0)
        # (2 ; 0) dans le repère de la pastille : (r2 ; -r2)
        self.assertAlmostEqual(gc.distance_signee(f, 2.0, 0.0), math.hypot(r2 - 1.0, r2 - 0.5), places=12)
        self.assertAlmostEqual(gc.distance_signee(f, 0.0, 0.0), -0.5, places=12)
        self.assertAlmostEqual(f.boite[2], 1.5 / r2 * 1.0 + 0.0, places=12)

    def test_rectangle_arrondi(self):
        f = gc.forme_parametrique("rect_arrondi", 2.0, 1.0, 0.25, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(gc.distance_signee(f, 1.5, 1.0), math.hypot(0.75, 0.75) - 0.25, places=12)
        self.assertAlmostEqual(gc.distance_signee(f, 0.0, 0.0), -0.5, places=12)
        self.assertAlmostEqual(gc.distance_signee(f, 1.5, 0.0), 0.5, places=12)
        rect = gc.forme_parametrique("rect", 2.0, 1.0, 0.0, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(gc.distance_signee(rect, 1.5, 1.0), math.hypot(0.5, 0.5), places=12)
        oblong = gc.forme_parametrique("rect_arrondi", 2.0, 1.0, 0.5, 0.0, 0.0, 90.0)
        self.assertEqual(oblong.genre, gc.SEGMENT)

    def test_rond_et_oblong(self):
        f = gc.forme_parametrique("rond", 1.0, 1.0, 0.0, 3.0, 4.0, 30.0)
        self.assertEqual((f.genre, f.rayon), (gc.POINT, 0.5))
        o = gc.forme_parametrique("oblong", 3.0, 1.0, 0.0, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(gc.distance_signee(o, 0.0, 1.5), 1.0, places=12)
        self.assertAlmostEqual(gc.distance_signee(o, 2.5, 0.0), 1.0, places=12)
        o90 = gc.forme_parametrique("oblong", 3.0, 1.0, 0.0, 0.0, 0.0, 90.0)
        self.assertAlmostEqual(gc.distance_signee(o90, 0.0, 2.5), 1.0, places=12)

    def test_polygone_non_convexe_et_trous(self):
        en_l = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]
        f = gc.forme_polygone(en_l)
        self.assertTrue(gc.contient(f, 0.5, 3.0))
        self.assertFalse(gc.contient(f, 2.0, 2.0))
        self.assertAlmostEqual(gc.distance_signee(f, 2.0, 2.0), 1.0, places=12)
        self.assertAlmostEqual(gc.distance_signee(f, 0.5, 3.0), -0.5, places=12)
        seg = gc.forme_segment(2.0, 2.0, 3.0, 3.0, 0.1)
        self.assertAlmostEqual(gc.ecart_formes(f, seg), 0.9, places=12)
        troue = gc.forme_polygone([(0, 0), (10, 0), (10, 10), (0, 10)], [[(4, 4), (6, 4), (6, 6), (4, 6)]])
        self.assertFalse(gc.contient(troue, 5.0, 5.0))
        self.assertAlmostEqual(gc.distance_signee(troue, 5.0, 5.0), 1.0, places=12)
        petit = gc.forme_point(5.0, 5.0, 0.5)
        self.assertAlmostEqual(gc.ecart_formes(troue, petit), 0.5, places=12)
        grand = gc.forme_polygone([(4.5, 4.5), (7, 4.5), (7, 5.5), (4.5, 5.5)])
        self.assertEqual(gc.distance_noyaux(troue, grand), 0.0)

    def test_segments(self):
        self.assertEqual(gc.d_segment_segment(0, 0, 2, 2, 0, 2, 2, 0), 0.0)
        self.assertAlmostEqual(gc.d_segment_segment(0, 0, 2, 0, 0, 1, 2, 1), 1.0, places=12)
        self.assertAlmostEqual(gc.d_segment_segment(0, 0, 1, 0, 3, 0, 4, 0), 2.0, places=12)

    def test_pose_rotation_quelconque_et_dessous(self):
        p = gc.Pose(0.0, 0.0, 12.5, False)
        x, y = p.monde(1.0, 0.0)
        self.assertAlmostEqual(x, math.cos(math.radians(12.5)), places=15)
        self.assertAlmostEqual(y, math.sin(math.radians(12.5)), places=15)
        self.assertEqual(gc.Pose(0.0, 0.0, 0.0, True).monde(1.0, 2.0), (1.0, -2.0))
        self.assertEqual(gc.Pose(0.0, 0.0, 90.0, True).monde(1.0, 2.0), (2.0, 1.0))
        self.assertEqual(gc.Pose(0.0, 0.0, 90.0, True).angle(30.0), 60.0)
        self.assertEqual(gc.Pose(0.0, 0.0, 90.0, False).angle(30.0), 120.0)
        self.assertEqual(gc.cos_sin(-90.0), (0.0, -1.0))
        self.assertEqual(gc.cos_sin(450.0), (0.0, 1.0))

    def test_couches_miroir_pour_une_piece_retournee(self):
        p = piece("U1", [pastille("1", "A", 1.0, 0.5, couches=("Haut",))], pose=(10.0, 10.0, 0.0, False))
        d = donnees(couches=("Haut", "Int1", "Int2", "Bas"), reseaux=[reseau("A")], composants=[p])
        sc = gc.poser(d, placement_v2(("U1", 10.0, 10.0, 90.0, True)), None)
        o = sc.objets[0]
        self.assertEqual(o.masque, 1 << 3)
        self.assertTrue(sc.poses["U1"].retournee)
        # (1 ; 0,5) local, dessous, 90 degrés : M -> (1 ; -0,5), R(90) -> (0,5 ; 1)
        self.assertAlmostEqual(o.forme.anneaux[0][0][0] + o.forme.anneaux[0][2][0], 2 * 10.5, places=12)
        self.assertAlmostEqual(o.forme.anneaux[0][0][1] + o.forme.anneaux[0][2][1], 2 * 11.0, places=12)
        sc = gc.poser(d, placement_v2(("U1", 10.0, 10.0, 0.0, False)), None)
        self.assertEqual(sc.objets[0].masque, 1 << 0)
        self.assertFalse(sc.poses["U1"].retournee)

    def test_pile_par_couche(self):
        pile = {"opposee": {"forme": "rond", "largeur": 1.0, "hauteur": 1.0, "rotation": 0.0, "rayon_coin": 0.0,
                            "points": [], "decalage": [0.0, 0.0]},
                "interne": {"forme": "rond", "largeur": 0.6, "hauteur": 0.6, "rotation": 0.0, "rayon_coin": 0.0,
                            "points": [], "decalage": [0.0, 0.0]}}
        trou = {"diametre": 0.3, "forme": "rond", "longueur": 0.0, "rotation": 0.0, "decalage": [0, 0],
                "metallise": True}
        p = piece("J1", [pastille("1", "A", 0.0, 0.0, largeur=2.0, hauteur=2.0, couches=("toutes",), trou=trou,
                                  pile=pile)])
        d = donnees(couches=("Haut", "Int1", "Int2", "Bas"), reseaux=[reseau("A")], composants=[p])
        o = gc.poser(d, placement_v2(("J1", 10.0, 10.0, 0.0, False)), None).objets[0]
        self.assertEqual(o.forme_sur(0).genre, gc.POLYGONE)
        self.assertEqual((o.forme_sur(3).genre, o.forme_sur(3).rayon), (gc.POINT, 0.5))
        self.assertEqual((o.forme_sur(1).rayon, o.forme_sur(2).rayon), (0.3, 0.3))
        o = gc.poser(d, placement_v2(("J1", 10.0, 10.0, 0.0, True)), None).objets[0]
        self.assertEqual(o.forme_sur(3).genre, gc.POLYGONE)
        self.assertEqual(o.forme_sur(0).rayon, 0.5)

    def test_trou_tourne_et_decale(self):
        trou = {"diametre": 1.0, "forme": "oblong", "longueur": 2.0, "rotation": 0.0, "decalage": [0.5, 0.0],
                "metallise": True}
        p = piece("J1", [pastille("1", "A", 1.0, 0.0, largeur=3.0, hauteur=3.0, rotation=90.0, couches=("toutes",),
                                  trou=trou)])
        d = donnees(reseaux=[reseau("A")], composants=[p])
        sc = gc.poser(d, placement_v2(("J1", 10.0, 10.0, 0.0, False)), None)
        t = sc.objets[0].info["trou"]
        self.assertEqual(t.genre, gc.SEGMENT)
        self.assertAlmostEqual(t.rayon, 0.5, places=12)
        ys = sorted((t.y1, t.y2))
        self.assertAlmostEqual(t.x1, 11.0, places=12)
        self.assertAlmostEqual(ys[0], 10.0, places=12)
        self.assertAlmostEqual(ys[1], 11.0, places=12)
        t = gc.poser(d, placement_v2(("J1", 10.0, 10.0, 0.0, True)), None).objets[0].info["trou"]
        self.assertAlmostEqual(t.x1, 11.0, places=12)
        self.assertAlmostEqual(sorted((t.y1, t.y2))[0], 9.0, places=12)

    def test_polygone_de_pastille_dans_le_repere_local(self):
        tri = [(0, 0), (2, 0), (0, 1)]
        p = piece("U1", [pastille("1", "A", 0.0, 0.0, forme="polygone", points=tri)])
        d = donnees(reseaux=[reseau("A")], composants=[p])
        o = gc.poser(d, placement_v2(("U1", 10.0, 10.0, 90.0, False)), None).objets[0]
        attendu = [(10.0, 10.0), (10.0, 12.0), (9.0, 10.0)]
        for (x, y), (ax, ay) in zip(o.forme.anneaux[0], attendu):
            self.assertAlmostEqual(x, ax, places=12)
            self.assertAlmostEqual(y, ay, places=12)


class TestPoses(unittest.TestCase):
    def setUp(self):
        self.d = donnees(composants=[piece("A", pose=(1.0, 2.0, 0.0, False)), piece("B", pose=(3.0, 4.0, 90.0, True)),
                                     piece("V", pose=(5.0, 6.0, 0.0, False), virtuel=True)])

    def test_v1_et_v2(self):
        v2 = placement_v2(("A", 10.0, 20.0, 180.0, False))
        v1 = v2["pieces"]
        for pl in (v1, v2):
            poses, infos = gc.poses_des_pieces(self.d, pl)
            self.assertAlmostEqual(poses["A"].x, 10.0, places=9)
            self.assertEqual(poses["A"].rotation, 180.0)
            self.assertEqual((poses["B"].x, poses["B"].dessous), (3.0, True))
            self.assertEqual(infos["absentes"], ["B"])

    def test_virtuelle_inconnue_doublon(self):
        pl = placement_v2(("V", 9.0, 9.0, 0.0, False), ("Z", 1.0, 1.0, 0.0, False), ("A", 1.0, 1.0, 0.0, False),
                          ("A", 2.0, 2.0, 0.0, False))
        poses, infos = gc.poses_des_pieces(self.d, pl)
        self.assertEqual((poses["V"].x, poses["V"].y), (5.0, 6.0))
        self.assertEqual(infos["inconnues"], ["Z"])
        self.assertEqual(infos["doublons"], ["A"])
        self.assertAlmostEqual(poses["A"].x, 2.0, places=9)

    def test_placement_invalide(self):
        pl = placement_v2(("A", 1.0, 1.0, 0.0, False))
        pl["unite"] = "mm"
        with self.assertRaises(gc.ErreurEntree):
            gc.poses_des_pieces(self.d, pl)
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.poses_des_pieces(self.d, [{"designator": "A", "side": "haut", "x": 0, "y": 0, "rotation": 0}])
        self.assertIn("placement[0].side", str(ctx.exception))
        with self.assertRaises(gc.ErreurEntree):
            gc.poses_des_pieces(self.d, {"format": "placements/3", "pieces": []})


class TestObjets(unittest.TestCase):
    """Identifiants (§2.8), genres, couches et neuf/préexistant de chaque objet posé (§4.1)."""

    def test_identifiants_et_genres(self):
        reg = {"id": "U1/R0", "couches": ["Haut"], "face": "composant", "pastille": "U1#0", "reseau": "A",
               "liaison": None, "points": [[-1, -1], [1, -1], [1, 1], [-1, 1]], "classe_isolement": 0}
        vemp = {"id": "U1/V0", "x": 0.5, "y": 0.0, "diametre": 0.5, "trou": 0.25, "reseau": "A", "classe_isolement": 0}
        u1 = piece("U1", [pastille("1", "A", 0.0, 0.0)], pose=(10.0, 10.0, 0.0, False), regions=[reg], vias=[vemp])
        v = piece("__libre_0", [pastille("1", "B", 0.0, 0.0, forme="rond", largeur=0.6, hauteur=0.6)],
                  pose=(30.0, 30.0, 0.0, False), virtuel=True)
        trou_libre = {"id": "T1", "x": 40.0, "y": 5.0, "rotation": 0.0,
                      "trou": {"diametre": 3.2, "forme": "rond", "longueur": 0.0, "rotation": 0.0, "decalage": [0, 0]},
                      "metallise": True, "diametre_cuivre": 6.0, "rayon_interdit_mm": 3.6, "classe_isolement": 0,
                      "piece": None}
        trou_sans_cuivre = dict(trou_libre, id="T2", x=45.0, diametre_cuivre=0.0, metallise=False)
        trou_de_piece = dict(trou_libre, id="T3", piece="U1")
        fixes = [{"id": "F0", "type": "piste", "reseau": "A", "couches": ["Bas"], "x1": 1, "y1": 1, "x2": 5, "y2": 1,
                  "largeur": 0.3, "classe_isolement": 0},
                 {"id": "F1", "type": "via", "reseau": "", "couches": ["toutes"], "x": 3, "y": 3, "diametre": 0.6,
                  "trou": 0.3, "classe_isolement": 0},
                 {"id": "F2", "type": "region", "reseau": "B", "couches": ["Haut"],
                  "points": [[20, 20], [22, 20], [22, 22], [20, 22]], "trous": [], "classe_isolement": 0}]
        zone = {"reseau": "B", "contour": [[0, 0], [50, 0], [50, 40], [0, 40]], "trous": []}
        d = donnees(couches=("Haut", "Plan", "Int", "Bas"), reseaux=[reseau("A"), reseau("B")],
                    composants=[u1, v], cuivre_fixe=fixes, trous=[trou_libre, trou_sans_cuivre, trou_de_piece],
                    zones={1: [zone]})
        r = routage([piste(1, 2, 5, 2, 0.2, "Haut", "A"), piste(1, 3, 5, 3, 0.2, "Inconnue", "A")],
                    [via(5, 2, 0.6, 0.3, "A"), dict(via(6, 2, 0.6, 0.3, "A"), de="Haut", a="Int")],
                    ["Haut", "Int", "Bas"])
        r["surfaces"] = [{"net": "A", "layer": "Bas", "contour": [[0, 0], [400, 0], [400, 400], [0, 400]],
                          "trous": [[[100, 100], [200, 100], [200, 200], [100, 200]]], "type": "remplissage",
                          "origine": "reference"}]
        couture = {"format": "vias_couture/2", "unite": "mil", "reseaux": ["B"], "pas_grille_mm": 2.5,
                   "vias": [{"x": 1000, "y": 1000, "size": 23.6, "hole_size": 11.8, "net": "B"}], "refus": {}}
        sc = gc.poser(d, placement_v2(("U1", 10.0, 10.0, 0.0, False), ("__libre_0", 1.0, 1.0, 0.0, False)), r,
                      couture)
        par_id = {o.id: o for o in sc.objets}
        attendu = {"U1#0": gc.PASTILLE, "U1/R0": gc.REGION, "U1/V0": gc.VIA_EMPREINTE, "__libre_0#0": gc.PASTILLE,
                   "F0": gc.CUIVRE_FIXE, "F1": gc.CUIVRE_FIXE, "F2": gc.CUIVRE_FIXE, "T1": gc.CUIVRE_TROU,
                   "Z:Plan/0": gc.ZONE_PLAN, "P0": gc.PISTE, "P1": gc.PISTE, "V0": gc.VIA, "V1": gc.VIA,
                   "S0": gc.SURFACE, "C0": gc.VIA_COUTURE}
        self.assertEqual({k: o.genre for k, o in par_id.items()}, attendu)
        self.assertEqual([o.id for o in sc.objets if o.neuf], ["P0", "P1", "V0", "V1", "S0", "C0"])
        self.assertEqual((par_id["__libre_0#0"].forme.x1, par_id["__libre_0#0"].forme.y1), (30.0, 30.0))
        self.assertEqual(par_id["F0"].masque, 1 << 3)
        self.assertEqual(par_id["F1"].masque, 0b1111)
        self.assertEqual(par_id["F1"].reseau, "")
        self.assertEqual(par_id["V0"].masque, 0b1111)
        self.assertEqual(par_id["V1"].masque, 0b0111)
        self.assertEqual(par_id["P1"].masque, 0)
        self.assertFalse(par_id["P1"].info["couche_connue"])
        self.assertEqual(par_id["Z:Plan/0"].masque, 1 << 1)
        self.assertFalse(gc.contient(par_id["S0"].forme, 150 * MIL, 150 * MIL))
        self.assertTrue(gc.contient(par_id["S0"].forme, 50 * MIL, 50 * MIL))
        self.assertAlmostEqual(par_id["C0"].forme.rayon, 11.8 * MIL, places=12)
        self.assertEqual(sorted(p["id"] for p in sc.parois),
                         ["C0", "F1", "T1", "T2", "U1/V0", "V0", "V1"])
        self.assertEqual(sc.infos_placement["absentes"], [])
        # la piste de couche inconnue est mesurée (longueur) sans voisin ni coude
        m = gc.mesures_routage(sc)
        self.assertAlmostEqual(m["longueur_pistes_mm"], 8.0, places=9)
        self.assertEqual(m["vias"], 2)

    def test_couture_v1_et_refus(self):
        d = donnees(reseaux=[reseau("B")])
        v1 = {"vias": [{"x": 100, "y": 100, "size": 23.6, "hole_size": 11.8, "net": "B"}]}
        sc = gc.poser(d, None, None, v1)
        self.assertEqual([sc.objets[k].id for k in sc.couture], ["C0"])
        with self.assertRaises(gc.ErreurEntree):
            gc.poser(d, None, None, {"format": "vias_couture/3", "vias": []})
        with self.assertRaises(gc.ErreurEntree):
            gc.poser(d, None, {"format": "routage/2", "unite": "mm", "pistes": [], "vias": []})

    def test_references_croisees_et_champs(self):
        p = piece("U1", [pastille("1", "A", 0.0, 0.0, couches=("Absente",))])
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.poser(donnees(reseaux=[reseau("A")], composants=[p]))
        self.assertIn("donnees.composants[0].pastilles[0].couches[0] : couche « Absente » inconnue", str(ctx.exception))
        p = piece("U1", [pastille("1", "A", 0.0, 0.0, forme="etoile")])
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.poser(donnees(reseaux=[reseau("A")], composants=[p]))
        self.assertIn("forme « etoile » inconnue", str(ctx.exception))
        p = piece("U1", [pastille("1", "A", 0.0, 0.0)])
        del p["pastilles"][0]["lx"]
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.poser(donnees(reseaux=[reseau("A")], composants=[p]))
        self.assertIn("champ « lx » absent", str(ctx.exception))
        d = donnees()
        d["couches"][1]["rang"] = 3
        with self.assertRaises(gc.ErreurEntree):
            gc.poser(d)

    def test_trou_carre_tourne(self):
        t = gc.forme_trou({"diametre": 2.0, "forme": "carre"}, 0.0, 0.0, 45.0)
        self.assertEqual(t.genre, gc.POLYGONE)
        self.assertAlmostEqual(t.boite[2], math.sqrt(2.0), places=12)
        self.assertAlmostEqual(gc.distance_signee(t, 3.0, 0.0), 3.0 - math.sqrt(2.0), places=12)


class TestIndexForceBrute(unittest.TestCase):
    """L'écart par l'index de cases (et l'élagage par les boîtes) égale le calcul par force brute de §4.7."""

    def test_aleatoire(self):
        alea = random.Random(4242)
        nets = ["A", "B", "C", ""]
        comps = []
        for i in range(18):
            pads = [pastille(str(k), alea.choice(nets), alea.uniform(-2, 2), alea.uniform(-2, 2),
                             forme=alea.choice(["rect", "rond", "oblong", "rect_arrondi"]),
                             largeur=alea.uniform(0.2, 1.5), hauteur=alea.uniform(0.2, 1.5),
                             rotation=alea.choice([0.0, 30.0, 45.0, 90.0, 12.5]),
                             couches=alea.choice([("Haut",), ("Bas",), ("toutes",)]), rayon_coin=0.1)
                    for k in range(3)]
            for p in pads:
                if p["forme"] == "rond":
                    p["hauteur"] = p["largeur"]
            comps.append(piece("U%d" % i, pads, pose=(alea.uniform(3, 47), alea.uniform(3, 37),
                                                      alea.choice([0.0, 90.0, 33.3]), alea.random() < 0.3)))
        pistes = []
        for _ in range(80):
            x, y = alea.uniform(1, 49), alea.uniform(1, 39)
            pistes.append(piste(x, y, x + alea.uniform(-3, 3), y + alea.uniform(-3, 3), alea.uniform(0.1, 0.5),
                                alea.choice(["Haut", "Bas"]), alea.choice(["A", "B", "C"])))
        vias = [via(alea.uniform(1, 49), alea.uniform(1, 39), 0.6, 0.3, alea.choice(["A", "B", "C"]))
                for _ in range(25)]
        d = donnees(reseaux=[reseau(n) for n in "ABC"], composants=comps)
        attendu = self.comparer(gc.poser(d, None, routage(pistes, vias)))
        self.assertGreater(len(attendu), 40)

    def comparer(self, sc):
        """Compare ``ecarts_pistes`` à la force brute (plages de cases sans index) ; rend les écarts attendus."""
        obtenu = gc.ecarts_pistes(sc)
        index = gc.IndexCases()

        def recoupe(a, b):
            a0, b0, a1, b1 = index.plage(a)
            c0, d0, c1, d1 = index.plage(b)
            return a0 <= c1 and c0 <= a1 and b0 <= d1 and d0 <= b1

        attendu = {}
        for k in sc.pistes:
            o = sc.objets[k]
            meilleur = None
            for j, q in enumerate(sc.objets):
                if j == k or q.genre == gc.ZONE_PLAN or not (q.masque & o.masque) or gc.meme_reseau(o, q):
                    continue
                if not recoupe(o.boite, q.boite):
                    continue
                e = min(gc.ecart_formes(o.forme_sur(r), q.forme_sur(r)) for r in gc.rangs(o.masque & q.masque))
                meilleur = e if meilleur is None else min(meilleur, e)
            if meilleur is not None:
                attendu[k] = meilleur
        self.assertEqual(set(obtenu), set(attendu))
        for k in attendu:
            self.assertAlmostEqual(obtenu[k], attendu[k], places=12)
        return attendu

    def test_recouvrements_multiples(self):
        # scène dense : beaucoup de pistes croisent plusieurs objets d'autres réseaux (courts-circuits)
        alea = random.Random(77)
        pistes = []
        for _ in range(70):
            x, y = alea.uniform(1, 11), alea.uniform(1, 11)
            pistes.append(piste(x, y, x + alea.uniform(-4, 4), y + alea.uniform(-4, 4), alea.uniform(0.1, 0.8),
                                "Haut", alea.choice(["A", "B", "C"])))
        vias = [via(alea.uniform(1, 11), alea.uniform(1, 11), alea.uniform(0.4, 1.2), 0.3, alea.choice("ABC"))
                for _ in range(20)]
        sc = gc.poser(donnees(reseaux=[reseau(n) for n in "ABC"]), None, routage(pistes, vias))
        attendu = self.comparer(sc)
        multiples = 0
        for k in sc.pistes:
            o = sc.objets[k]
            n = sum(1 for j, q in enumerate(sc.objets) if j != k and (q.masque & o.masque)
                    and not gc.meme_reseau(o, q) and gc.ecart_objets(o, q) < 0.0)
            multiples += n >= 2
        self.assertGreater(multiples, 20)
        self.assertLess(min(attendu.values()), 0.0)

    def test_grands_objets(self):
        # pistes dont la boîte couvre plus de SEUIL_CASES_OBJET cases : rangées à part, même résultat
        alea = random.Random(5)
        pistes = [piste(0, 0, 150, 120, 0.3, "Haut", "A"), piste(0, 120, 150, 0, 2.0, "Haut", "B")]
        for _ in range(40):
            x, y = alea.uniform(0, 150), alea.uniform(0, 120)
            pistes.append(piste(x, y, x + alea.uniform(-2, 2), y + alea.uniform(-2, 2), 0.2, "Haut",
                                alea.choice("ABC")))
        sc = gc.poser(donnees(reseaux=[reseau(n) for n in "ABC"]), None, routage(pistes))
        self.comparer(sc)
        index = gc.IndexCases()
        index.ajouter(0, sc.objets[sc.pistes[0]].boite)
        self.assertEqual((len(index.cases), len(index.grands)), (0, 1))
        # une boîte immense (2 km de côté) ne fait énumérer aucune case
        index.ajouter(1, (-1.0e6, -1.0e6, 1.0e6, 1.0e6))
        index.ajouter(2, (5.0, 5.0, 5.2, 5.2))
        vus = sorted({k for lst in index.listes((-1.0e6, -1.0e6, 1.0e6, 1.0e6)) for k in lst})
        self.assertEqual(vus, [0, 1, 2])


class TestDistancesEchantillonnees(unittest.TestCase):
    """Distances exactes contre un échantillonnage fin du noyau de l'une des formes (noyaux disjoints)."""

    @staticmethod
    def echantillons(f, n=400):
        if f.genre == gc.POINT:
            return [(f.x1, f.y1)]
        if f.genre == gc.SEGMENT:
            return [(f.x1 + (f.x2 - f.x1) * i / n, f.y1 + (f.y2 - f.y1) * i / n) for i in range(n + 1)]
        pts = []
        for ax, ay, bx, by in f.aretes:
            pts += [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(n + 1)]
        return pts

    def test_formes_aleatoires(self):
        alea = random.Random(99)
        formes = ("rect", "rond", "oblong", "rect_arrondi")
        essais = 0
        while essais < 60:
            fa = gc.forme_parametrique(alea.choice(formes), alea.uniform(0.3, 2), alea.uniform(0.3, 2),
                                       alea.uniform(0, 0.4), 0.0, 0.0, alea.uniform(0, 360))
            if alea.random() < 0.3:
                # pentagone irrégulier (souvent non convexe), déplacé au hasard
                dx, dy = alea.uniform(-6, 6), alea.uniform(-6, 6)
                rayons = [alea.uniform(0.8, 2.5) for _ in range(5)]
                fb = gc.forme_polygone([(dx + r * math.cos(t), dy + r * math.sin(t))
                                        for r, t in zip(rayons, (0.0, 1.3, 2.9, 4.1, 5.5))])
            else:
                fb = gc.forme_parametrique(alea.choice(formes), alea.uniform(0.3, 2), alea.uniform(0.3, 2),
                                           alea.uniform(0, 0.4), alea.uniform(-4, 4), alea.uniform(-4, 4),
                                           alea.uniform(0, 360))
            exact = gc.ecart_formes(fa, fb)
            if gc.distance_noyaux(fa, fb) <= 1e-9:
                continue
            essais += 1
            echantillonne = min(gc.distance_signee(fb, x, y) for x, y in self.echantillons(fa)) - fa.rayon
            self.assertGreaterEqual(echantillonne, exact - 1e-12)
            self.assertLessEqual(echantillonne - exact, 5e-3)


class _DossierTemporaire(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp(prefix="test_mesures_")

    def tearDown(self):
        shutil.rmtree(self.dossier, ignore_errors=True)

    def ecrire(self, nom, contenu, binaire=False):
        chemin = os.path.join(self.dossier, nom)
        with open(chemin, "wb" if binaire else "w", encoding=None if binaire else "utf-8") as f:
            f.write(contenu)
        return chemin

    def verifier_sans_chemin(self, message):
        self.assertNotIn(self.dossier, message)
        self.assertNotIn(os.path.dirname(self.dossier), message)


class TestLecturesEtPositionnels(_DossierTemporaire):
    def test_dossier_et_texte_non_utf8(self):
        os.mkdir(os.path.join(self.dossier, "sous.json"))
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.charger_json(os.path.join(self.dossier, "sous.json"), ("mesures/2",))
        self.assertIn("sous.json : ", str(ctx.exception))
        self.verifier_sans_chemin(str(ctx.exception))
        chemin = self.ecrire("l1.json", b'{"format": "mesures/2", "t": "\xe9"}', binaire=True)
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.charger_json(chemin, ("mesures/2",))
        self.assertIn("texte non UTF-8", str(ctx.exception))

    def test_positionnels(self):
        pos = (gc.Positionnel("dossier", "DOSSIER"), gc.Positionnel("sortie", "SORTIE"))
        opts = (gc.Option("--gabarit", obligatoire=True),)
        a = gc.analyser_arguments(["g", "s.PcbDoc", "--gabarit", "c.PcbDoc"], opts, positionnels=pos)
        self.assertEqual(a, {"dossier": "g", "sortie": "s.PcbDoc", "gabarit": "c.PcbDoc"})
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.analyser_arguments(["g", "s.PcbDoc"], opts, positionnels=pos)
        self.assertIn("--gabarit", str(ctx.exception))
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.analyser_arguments(["--gabarit", "c"], opts, positionnels=pos)
        self.assertIn("DOSSIER, SORTIE", str(ctx.exception))


# ---------------------------------------------------------------------------------------------------------------------
# Entrées, sorties, arguments, codes
# ---------------------------------------------------------------------------------------------------------------------
class TestEntreesSorties(_DossierTemporaire):
    def test_lecture_stricte(self):
        ok = self.ecrire("m.json", json.dumps({"format": "mesures/2", "x": 1}))
        self.assertEqual(gc.charger_json(ok, ("mesures/2",))["x"], 1)
        for contenu, attendu in ((json.dumps({"format": "mesures/3"}), "format mesures/3 non pris en charge"),
                                 (json.dumps({"x": 1}), "champ « format » absent"),
                                 ('{"format": "mesures/2",}', "JSON invalide, ligne 1"),
                                 ('{"format": "mesures/2", "v": NaN}', "non numérique"),
                                 ("[]", "liste nue (forme v1) non prise en charge")):
            chemin = self.ecrire("f.json", contenu)
            with self.assertRaises(gc.ErreurEntree) as ctx:
                gc.charger_json(chemin, ("mesures/2",))
            self.assertIn(attendu, str(ctx.exception))
            self.assertTrue(str(ctx.exception).startswith("f.json : "))
            self.verifier_sans_chemin(str(ctx.exception))
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.charger_json(os.path.join(self.dossier, "absent.json"), ("mesures/2",))
        self.assertEqual(str(ctx.exception), "absent.json : fichier introuvable")

    def test_bom_v1_et_versions_mineures(self):
        chemin = self.ecrire("p.json", b"\xef\xbb\xbf[]", binaire=True)
        self.assertEqual(gc.charger_json(chemin, ("placements/2", "placements/1")), [])
        chemin = self.ecrire("r.json", json.dumps({"pistes": [], "vias": []}))
        self.assertEqual(gc.charger_json(chemin, ("routage/2", "routage/1"))["vias"], [])
        chemin = self.ecrire("r2.json", json.dumps({"format": "routage/2.3", "pistes": [], "vias": []}))
        self.assertEqual(gc.charger_json(chemin, ("routage/2",))["pistes"], [])

    def test_ecriture_atomique(self):
        chemin = os.path.join(self.dossier, "sortie.json")
        gc.ecrire_json(chemin, {"format": "mesures/2", "texte": "écart à 0,6 mm"})
        with open(chemin, "rb") as f:
            octets = f.read()
        self.assertFalse(octets.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", octets)
        self.assertIn("écart".encode("utf-8"), octets)
        self.assertTrue(octets.endswith(b"\n"))
        self.assertFalse(os.path.exists(chemin + ".tmp"))
        gc.ecrire_json(chemin, {"format": "mesures/2", "v": 2})
        self.assertEqual(gc.charger_json(chemin, ("mesures/2",))["v"], 2)
        with self.assertRaises(ValueError):
            gc.ecrire_json(chemin, {"v": float("nan")})
        with self.assertRaises(gc.ErreurEntree) as ctx:
            gc.ecrire_json(os.path.join(self.dossier, "absent", "x.json"), {"v": 1})
        self.verifier_sans_chemin(str(ctx.exception))

    def test_arguments(self):
        opts = (gc.Option("--donnees", obligatoire=True), gc.Option("--pas", type=float, defaut=2.5),
                gc.Option("--mode", choix=("strict", "altium"), defaut="strict"), gc.Option("--sans-grille", nargs=0),
                gc.Option("--fenetre", nargs=4, type=float))
        a = gc.analyser_arguments(["--donnees", "d.json", "--pas=1.5", "--sans-grille", "--fenetre", "-1", "0", "5",
                                   "5.5"], opts)
        self.assertEqual(a, {"donnees": "d.json", "pas": 1.5, "mode": "strict", "sans_grille": True,
                             "fenetre": [-1.0, 0.0, 5.0, 5.5]})
        for argv, attendu in ((["--pas", "2"], "argument obligatoire absent : --donnees"),
                              (["--donnees", "d", "--donees", "x"], "vouliez-vous dire --donnees ?"),
                              (["--donnees", "d", "--donnees", "e"], "donnée deux fois"),
                              (["--donnees", "d", "--pas", "2,5"], "virgule décimale"),
                              (["--donnees", "d", "--mode", "autre"], "valeurs permises : strict, altium"),
                              (["--donnees"], "valeur absente après --donnees"),
                              (["--donnees", "d", os.path.join(self.dossier, "x.json")], "argument en trop : x.json")):
            with self.assertRaises(gc.ErreurEntree) as ctx:
                gc.analyser_arguments(argv, opts)
            self.assertIn(attendu, str(ctx.exception))
            self.verifier_sans_chemin(str(ctx.exception))
        with self.assertRaises(gc.DemandeAide) as ctx:
            gc.analyser_arguments(["--help"], opts, "prog.py", "Description.")
        self.assertIn("usage : prog.py --donnees DONNEES", str(ctx.exception))

    def test_executer_codes(self):
        opts = (gc.Option("--entree", obligatoire=True),)
        absent = os.path.join(self.dossier, "absent.json")

        def lire(args):
            gc.charger_json(args["entree"], ("mesures/2",))

        def planter(args):
            raise RuntimeError("panne dans " + args["entree"])

        def negatif(args):
            return gc.CODE_NEGATIF

        for corps, argv, code in ((lire, [], 2), (lire, ["--entree", absent], 2), (planter, ["--entree", absent], 4),
                                  (negatif, ["--entree", "x"], 1), (lire, ["--help"], 0)):
            err = io.StringIO()
            sortie = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(sortie):
                self.assertEqual(gc.executer(corps, argv, opts, "prog.py"), code)
            self.assertNotIn("Traceback", err.getvalue())
            self.verifier_sans_chemin(err.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            gc.executer(planter, ["--entree", absent], opts, "prog.py")
        self.assertIn("erreur interne (RuntimeError) : panne dans absent.json", err.getvalue())

    def test_anonymiser(self):
        # chemins fictifs assemblés à l'exécution (aucun motif de chemin absolu dans le source, §0.3)
        lecteur = "C" + ":" + "\\"
        self.assertEqual(gc.anonymiser_message("erreur sur " + lecteur + "Dossier\\sous\\f.json ici"),
                         "erreur sur f.json ici")
        self.assertEqual(gc.anonymiser_message("lecture /" + "ho" + "me/qui/x/y.json"), "lecture y.json")
        self.assertEqual(gc.anonymiser_message("format routage/2 et mil/mm"), "format routage/2 et mil/mm")
        # nom de dossier à espace (« Prénom Nom »), chemin non reçu en argument
        profil = lecteur + "Users\\Jean Dupont\\outil\\f.json"
        self.assertEqual(gc.anonymiser_message("erreur dans " + profil + " ici"), "erreur dans f.json ici")
        self.assertEqual(gc.anonymiser_message("erreur dans " + profil.replace("\\", "/")), "erreur dans f.json")
        self.assertEqual(gc.anonymiser_message("lecture /" + "ho" + "me/Jean Dupont/x/y.json"), "lecture y.json")
        # entre apostrophes (repr d'une exception de Python) : tout le contenu, espaces comprises
        cite = "[Errno 2] No such file or directory: '" + lecteur + "\\Users\\\\Jean Dupont\\\\f d.json'"
        self.assertEqual(gc.anonymiser_message(cite), "[Errno 2] No such file or directory: 'f d.json'")
        for texte in (gc.anonymiser_message("erreur dans " + profil), gc.anonymiser_message(cite)):
            self.assertNotIn("Dupont", texte)


class TestMesures(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp(prefix="test_mesures_")
        p = piece("U1", [pastille("1", "B", 0.0, 0.0)], pose=(5.0, 11.0, 0.0, False))
        self.d = donnees(reseaux=[reseau("A", "puissance", (0.2, 0.3, None)), reseau("B")], composants=[p])
        self.pl = placement_v2(("U1", 5.0, 11.0, 0.0, False))
        self.r = routage([piste(2, 10, 8, 10, 0.25, "Haut", "A"), piste(8, 10, 8, 4, 0.25, "Haut", "A")],
                         [via(8, 4, 0.6, 0.3, "A")])

    def tearDown(self):
        shutil.rmtree(self.dossier, ignore_errors=True)

    def test_mesures2_et_cout(self):
        m = mesures.mesurer(self.d, self.pl, self.r)
        self.assertEqual(set(m), {"format", "formule", "cout_commun", "termes", "parametres"})
        self.assertEqual(set(m["termes"]), {"longueur_mm", "vias", "retreci_mm", "resistance_puissance_mohm", "coudes",
                                            "ecart_moyen_mm"})
        self.assertEqual(set(m["parametres"]), {"seuil_ecart_mm", "plafond_ecart_mm"})
        self.assertEqual((m["format"], m["formule"]), ("mesures/2", "cout_commun/2"))
        t = m["termes"]
        self.assertAlmostEqual(t["longueur_mm"], 12.0, places=9)
        self.assertEqual((t["vias"], t["coudes"]), (1, 1))
        self.assertAlmostEqual(t["retreci_mm"], 12.0, places=9)
        self.assertAlmostEqual(t["resistance_puissance_mohm"], 0.5 * 12.0 / 0.25, places=9)
        # seule la première piste a un voisin (pastille de B, bord bas à y = 10,5) : écart 0,375
        self.assertAlmostEqual(t["ecart_moyen_mm"], 0.375, places=9)
        attendu = 12.0 + 1.5 + 24.0 + 3.0 * 24.0 + 0.1 + 1000.0 * (0.6 - 0.375)
        self.assertAlmostEqual(m["cout_commun"], attendu, places=6)
        self.assertAlmostEqual(m["parametres"]["seuil_ecart_mm"], 0.6, places=12)
        self.assertAlmostEqual(m["parametres"]["plafond_ecart_mm"], 1.0, places=12)

    def test_termes_repris_du_verdict(self):
        verdict = {"format": "verdict/2", "mode": "strict",
                   "mesures": {"ecart_moyen_mm": 0.5, "ecart_min_mm": 0.2, "coudes": 7, "coudes_90": 1,
                               "angles_aigus": 0, "longueur_pistes_mm": 100.0, "resistance_puissance_mohm": 2.0,
                               "vias": 3}}
        m = mesures.mesurer(self.d, self.pl, self.r, verdict)
        self.assertEqual(m["termes"]["coudes"], 7)
        self.assertEqual(m["termes"]["longueur_mm"], 100.0)
        self.assertAlmostEqual(m["termes"]["retreci_mm"], 12.0, places=9)
        self.assertAlmostEqual(m["cout_commun"], 100 + 4.5 + 24.0 + 6.0 + 0.7 + 100.0, places=6)
        with self.assertRaises(gc.ErreurEntree):
            mesures.mesurer(self.d, self.pl, self.r, {"mesures": {"coudes": 1}})

    def test_carte_sans_routage(self):
        m = mesures.mesurer(self.d, self.pl, routage())
        self.assertAlmostEqual(m["cout_commun"], 600.0, places=9)
        self.assertEqual(m["termes"]["ecart_moyen_mm"], 0.0)

    def test_placement_et_routage_v1(self):
        """Compatibilité (§6.4) : placement en liste nue, routage sans champ ``format`` (références figées)."""
        r1 = {"pistes": self.r["pistes"], "vias": self.r["vias"]}
        self.assertEqual(mesures.mesurer(self.d, self.pl["pieces"], r1), mesures.mesurer(self.d, self.pl, self.r))

    def test_donnees_v1_refusees(self):
        with self.assertRaises(gc.ErreurEntree) as ctx:
            mesures.mesurer({"composants": []}, self.pl, self.r)
        self.assertIn("donnees_placeur/2", str(ctx.exception))

    def ecrire(self, nom, objet):
        chemin = os.path.join(self.dossier, nom)
        gc.ecrire_json(chemin, objet)
        return chemin

    def test_main(self):
        d = self.ecrire("donnees_placeur.json", self.d)
        p = self.ecrire("placements_routage.json", self.pl)
        r = self.ecrire("routage_detaille.json", self.r)
        s = os.path.join(self.dossier, "mesures.json")
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            code = mesures.main(["--donnees", d, "--placement", p, "--routage", r, "--sortie", s])
        self.assertEqual(code, 0)
        self.assertNotIn(self.dossier, sortie.getvalue())
        lu = gc.charger_json(s, ("mesures/2",))
        self.assertEqual(lu, json.loads(json.dumps(mesures.mesurer(self.d, self.pl, self.r))))
        for argv in ([], ["--donnees", os.path.join(self.dossier, "absent.json"), "--placement", p, "--routage", r,
                          "--sortie", s]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(mesures.main(argv), 2)
            self.assertNotIn("Traceback", err.getvalue())
            self.assertNotIn(self.dossier, err.getvalue())
            self.assertTrue(err.getvalue().startswith("mesures.py : "))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mesures.main(["--help"]), 0)

    def test_entrees_invalides_code_2(self):
        """Entrée mal typée ou absurde : code 2, message en français qui nomme le champ (jamais le code 4 ni le
        code 0 en silence), en temps borné."""
        def avec(fonction):
            d, pl, r = json.loads(json.dumps((self.d, self.pl, self.r)))
            fonction(d, pl, r)
            return d, pl, r

        def region_vide(d, _pl, _r):
            d["composants"][0]["regions"] = [{"points": [], "couches": ["Haut"], "reseau": "B"}]

        cas = [
            ("routage.pistes[0].x1", lambda d, pl, r: r["pistes"][0].update(x1=1e306)),
            ("routage.pistes[0].y2", lambda d, pl, r: r["pistes"][0].update(y2=-2.0e6)),
            ("couches[0]", lambda d, pl, r: d["composants"][0]["pastilles"][0].update(couches=[["Haut"]])),
            ("routage.vias[0].de", lambda d, pl, r: r["vias"][0].update(de=["Haut"], a="Bas")),
            ("donnees.reseaux[0].largeurs_mm[1]", lambda d, pl, r: d["reseaux"][0].update(largeurs_mm=[0.2, {}])),
            ("donnees.reseaux[0].largeurs_mm[1]", lambda d, pl, r: d["reseaux"][0].update(largeurs_mm=[0.2, "0.3"])),
            ("donnees.reseaux[0].largeurs_mm[1]", lambda d, pl, r: d["reseaux"][0].update(largeurs_mm=[0.2, True])),
            ("donnees.reseaux[0].largeurs_mm", lambda d, pl, r: d["reseaux"][0].update(largeurs_mm=[0.2])),
            ("donnees.reseaux[0].role", lambda d, pl, r: d["reseaux"][0].update(role=["puissance"])),
            ("regions[0].points", region_vide),
        ]
        for champ, modifier in cas:
            d, pl, r = avec(modifier)
            chemins = [self.ecrire(n, o) for n, o in (("d.json", d), ("p.json", pl), ("r.json", r))]
            err = io.StringIO()
            debut = time.monotonic()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = mesures.main(["--donnees", chemins[0], "--placement", chemins[1], "--routage", chemins[2],
                                     "--sortie", os.path.join(self.dossier, "m.json")])
            self.assertEqual(code, 2, "%s : %s" % (champ, err.getvalue()))
            self.assertIn(champ, err.getvalue())
            self.assertNotIn("erreur interne", err.getvalue())
            self.assertLess(time.monotonic() - debut, 5.0, champ)

    def test_ligne_de_commande_sans_argument(self):
        res = subprocess.run([sys.executable, os.path.join(ICI, "mesures.py")], capture_output=True,
                             encoding="utf-8", errors="replace", cwd=self.dossier)
        self.assertEqual(res.returncode, 2)
        self.assertNotIn("Traceback", res.stderr + res.stdout)
        self.assertIn("obligatoires absents", res.stderr)


# ---------------------------------------------------------------------------------------------------------------------
# METROID (adaptateur des données v1 de 9b5b5a3)
# ---------------------------------------------------------------------------------------------------------------------
REV4 = os.path.join(ICI, "sorties", "conjoint")
# Termes calculés par drc_maison.py et comparer_conjoint.py de 9b5b5a3 sur la copie figée rev4 (cuivre du bloc présent)
TERMES_REV4_9B5B5A3 = {"longueur_mm": 5392.969848869567, "vias": 470, "coudes": 1099,
                       "resistance_puissance_mohm": 1264.3249042207754, "ecart_moyen_mm": 0.5474865695131753}


def _afficher_termes(etiquette, m):
    t = m["termes"]
    sys.stderr.write("\n  %s : coût %.2f ; longueur %.3f mm ; vias %d ; rétréci %.3f mm ; résistance %.3f mOhm ; "
                     "coudes %d ; écart moyen %.6f mm\n" % (etiquette, m["cout_commun"], t["longueur_mm"], t["vias"],
                                                            t["retreci_mm"], t["resistance_puissance_mohm"],
                                                            t["coudes"], t["ecart_moyen_mm"]))


class TestMetroid(unittest.TestCase):
    def setUp(self):
        if not donnees_v1_presentes():
            self.skipTest("données v1 de 9b5b5a3 absentes (retirées au jalon B2 : tests refaits en C9)")

    def mesurer_rev4(self, avec_cuivre_bloc):
        pl = _lire(os.path.join(REV4, "placements_routage.json"))
        rt = _lire(os.path.join(REV4, "routage_detaille.json"))
        d = adapter_metroid_v1([rt], avec_cuivre_bloc)
        return mesures.mesurer(d, pl, rt)

    def test_rev4_protocole_historique(self):
        m = self.mesurer_rev4(True)
        _afficher_termes("rev4, cuivre du bloc présent", m)
        self.assertLessEqual(abs(m["cout_commun"] - 10328.5), 51.6)
        t = m["termes"]
        self.assertEqual(t["vias"], TERMES_REV4_9B5B5A3["vias"])
        self.assertEqual(t["coudes"], TERMES_REV4_9B5B5A3["coudes"])
        for cle in ("longueur_mm", "resistance_puissance_mohm", "ecart_moyen_mm"):
            self.assertAlmostEqual(t[cle], TERMES_REV4_9B5B5A3[cle], delta=1e-6 * max(1.0, abs(t[cle])), msg=cle)

    def test_rev4_sans_cuivre_du_bloc(self):
        m = self.mesurer_rev4(False)
        _afficher_termes("rev4, sans le cuivre du bloc", m)
        self.assertLessEqual(abs(m["cout_commun"] - 10328.5), 51.6)

    def test_ei_1038(self):
        if not REFERENCES:
            self.skipTest("--references absent : graine ei_1038 non disponible")
        dossier = os.path.join(REFERENCES, "ei_1038")
        pl = _lire(os.path.join(dossier, "placements_routage.json"))
        rt = _lire(os.path.join(dossier, "routage_detaille.json"))
        m = mesures.mesurer(adapter_metroid_v1([rt], avec_cuivre_bloc=False), pl, rt)
        _afficher_termes("ei_1038 (bloc libre)", m)
        self.assertLessEqual(abs(m["cout_commun"] - 12398.4), 62.0)


# ---------------------------------------------------------------------------------------------------------------------
# Propreté (proprete/1) et convertisseurs (mesure_convertisseurs/1), étape C5
# ---------------------------------------------------------------------------------------------------------------------
class TestProprete(unittest.TestCase):
    def test_synthetique(self):
        comps = [piece("U1", [pastille("1", "A", 0.0, 0.0)], pose=(10.0, 10.0, 0.0, False)),
                 piece("U2", [pastille("1", "A", 0.0, 0.0)], pose=(20.0, 20.0, 0.0, False)),
                 piece("U3", [pastille("1", "B", 0.0, 0.0)], pose=(30.0, 10.0, 0.0, False)),
                 piece("U4", [pastille("1", "B", 0.0, 0.0)], pose=(40.0, 20.0, 0.0, False))]
        d = donnees(reseaux=[reseau("A", "puissance"), reseau("B")], composants=comps)
        d["groupes"] = [{"nom": "g", "type": "repete", "correspondance_reseaux": [["A", "B"], ["A", "A"]]}]
        d["paires"] = [{"nom": "AB", "positif": "A", "negatif": "B", "ecart_mm": None, "largeur_mm": None,
                        "tolerance_longueur_mm": None}]
        rt = routage([piste(10, 10, 20, 10, 0.25, "Haut", "A"), piste(20, 10, 20, 20, 0.25, "Haut", "A"),
                      piste(30, 10, 30.3, 10, 0.25, "Haut", "B"), piste(30.3, 10, 40, 20, 0.25, "Haut", "B")],
                     [via(15, 10, 0.6, 0.3, "A")])
        p = proprete.mesurer(d, placement_v2(), rt)
        self.assertEqual(formats.VALIDATEURS["proprete"](p), [])
        self.assertEqual((p["segments"], p["segments_courts"], p["connexions"], p["coudes"]), (4, 1, 2, 2))
        self.assertEqual((p["coudes_par_connexion"], p["vias_par_connexion"], p["vias_puissance"]), (1.0, 0.5, 1))
        self.assertEqual(p["paires_jumelles"], 1)
        lb = 0.3 + math.hypot(9.7, 10.0)
        self.assertAlmostEqual(p["ecart_jumeaux"]["longueur"], round(abs(20.0 - lb) / max(20.0, lb), 3))
        self.assertEqual(p["ecart_jumeaux"]["vias"], 1.0)
        self.assertEqual(p["paires"]["AB"]["delta_vias"], 1)
        self.assertEqual(p["hors_direction"]["Haut"], round(1.0 - 10.3 / (20.3 + math.hypot(9.7, 10.0)), 3))

    def test_sans_routage(self):
        p = proprete.mesurer(donnees(), placement_v2(), routage())
        self.assertEqual((p["segments"], p["detour"], p["ecart_jumeaux"], p["paires"]),
                         (0, {"mediane": 0.0, "p90": 0.0}, {"longueur": 0.0, "vias": 0.0}, {}))


def _carte_convertisseur(rail="VIN", integre=False, topologie="abaisseur"):
    """Q1 (haut) et Q2 (bas) ou U2 intégré, C1 entre ``rail`` et GND, L1 (SW -> autre rail), U1 pilote (FB, SW),
    R1 charge sur VOUT. Boucle : (4,10) (9,10) (11,10) (11,5) (9,5) (4,5) = rectangle 7 x 5."""
    autre = "VOUT" if rail == "VIN" else "VIN"
    comps = [piece("C1", [pastille("1", rail, 0.0, 2.5), pastille("2", "GND", 0.0, -2.5)], pose=(4.0, 7.5, 0.0, False)),
             piece("L1", [pastille("1", "SW", -2.0, 0.0), pastille("2", autre, 2.0, 0.0)],
                   pose=(20.0, 10.0, 0.0, False)),
             piece("U1", [pastille("1", "FB", 0.0, 0.0), pastille("2", "SW", 1.0, 0.0)], pose=(30.0, 30.0, 0.0, False)),
             piece("R1", [pastille("1", "VOUT", 0.0, 0.0)], pose=(32.0, 10.0, 0.0, False))]
    if integre:
        comps.append(piece("U2", [pastille("1", rail, -1.0, 0.0), pastille("2", "SW", 1.0, 0.0),
                                  pastille("3", "GND", 0.0, -5.0)], pose=(10.0, 10.0, 0.0, False)))
        haut = bas = pilote = "U2"
    else:
        comps.append(piece("Q1", [pastille("1", rail, -1.0, 0.0), pastille("2", "SW", 1.0, 0.0)],
                           pose=(10.0, 10.0, 0.0, False)))
        comps.append(piece("Q2", [pastille("1", "SW", 1.0, 0.0), pastille("2", "GND", -1.0, 0.0)],
                           pose=(10.0, 5.0, 0.0, False)))
        haut, bas, pilote = "Q1", "Q2", "U1"
    d = donnees(reseaux=[reseau(n) for n in ("VIN", "VOUT", "SW", "GND", "FB")], composants=comps)
    d["convertisseurs"] = [{"nom": "conv", "topologie": topologie, "commutation": "integre" if integre else "synchrone",
                            "haut": haut, "bas": bas, "pilote": pilote, "inductance": "L1", "cond_entree": ["C1"],
                            "cond_sortie": [], "cond_bulk": [], "cond_pilote": [], "cond_bootstrap": [],
                            "reseau_entree": "VIN", "reseau_sortie": "VOUT", "reseau_masse": "GND",
                            "reseau_commutation": "SW", "reseaux_courts": [], "reseaux_sensibles": ["FB"],
                            "boucle_chaude": {"condensateurs": ["C1"], "reseau_rail": rail, "reseau_masse": "GND"},
                            "origine": "deduit"}]
    return d


R_10MM = 1.72e-5 * 10.0 / (1.0 * 0.035)      # piste de 10 mm, 1 mm de large, 35 µm


class TestConvertisseurs(unittest.TestCase):
    def mesurer(self, d, pistes=(), vias=()):
        m = mconv.mesurer(d, placement_v2(), routage(pistes, vias))
        self.assertEqual(formats.VALIDATEURS["mesure_convertisseurs"](m), [])
        return m["convertisseurs"]

    def test_abaisseur(self):
        d = _carte_convertisseur()
        c, = self.mesurer(d, [piste(11, 10, 18, 10, 1.0, "Haut", "SW"), piste(11, 10, 11, 5, 0.5, "Haut", "SW"),
                              piste(12, 14, 16, 14, 0.2, "Haut", "FB")], [via(14, 10, 0.6, 0.3, "SW")])
        self.assertTrue(c["boucle_chaude_evaluee"])
        self.assertAlmostEqual(c["boucle_chaude_mm2"], 35.0, places=6)
        self.assertAlmostEqual(c["boucle_chaude_perimetre_mm"], 24.0, places=6)
        self.assertAlmostEqual(c["noeud_longueur_mm"], 12.0, places=3)
        self.assertAlmostEqual(c["noeud_surface_mm2"], 9.5, places=3)
        self.assertEqual(c["noeud_vias"], 1)
        self.assertEqual(c["sensibles_distance_min_mm"], 0.0)            # U1 : FB et SW côte à côte
        self.assertAlmostEqual(c["sensibles_distance_min_hors_pilote_mm"], 3.4, places=4)
        self.assertEqual(c["chutes_mv"], {})

    def test_elevateur(self):
        c, = self.mesurer(_carte_convertisseur(rail="VOUT", topologie="elevateur"))
        self.assertTrue(c["boucle_chaude_evaluee"])
        self.assertAlmostEqual(c["boucle_chaude_mm2"], 35.0, places=6)
        self.assertEqual(c["noeud_vias"], 0)

    def test_elevateur_chute_depuis_l_interrupteur_haut(self):
        d = _carte_convertisseur(rail="VOUT", topologie="elevateur")
        d["charges"] = [{"broche": "R1.1", "pastilles": ["R1#0"], "courant_a": 1.0}]
        # source : Q1.1 (VOUT) en (9, 10) ; charge R1 en (32, 10) ; chemin de 10 + 23 + 10 = 43 mm
        c, = self.mesurer(d, [piste(9, 10, 9, 20, 1.0, "Haut", "VOUT"), piste(9, 20, 32, 20, 1.0, "Haut", "VOUT"),
                              piste(32, 20, 32, 10, 1.0, "Haut", "VOUT")])
        self.assertAlmostEqual(c["chutes_mv"]["R1.1"], 4.3 * R_10MM * 1000.0, places=3)

    def test_integre(self):
        c, = self.mesurer(_carte_convertisseur(integre=True))
        self.assertAlmostEqual(c["boucle_chaude_mm2"], 32.5, places=6)
        self.assertAlmostEqual(c["boucle_chaude_perimetre_mm"], 18.0 + math.hypot(1.0, 5.0), places=2)

    def test_piece_absente_et_sans_cuivre(self):
        d = _carte_convertisseur()
        d["convertisseurs"][0]["boucle_chaude"]["condensateurs"] = ["C99"]
        d["convertisseurs"][0]["reseaux_sensibles"] = ["INCONNU"]
        c, = self.mesurer(d)
        self.assertEqual((c["boucle_chaude_evaluee"], c["boucle_chaude_mm2"]), (False, 0.0))
        self.assertIsNone(c["sensibles_distance_min_mm"])

    def test_chutes_reseau_connu(self):
        d = _carte_convertisseur()
        d["charges"] = [{"broche": "R1.1", "pastilles": ["R1#0"], "courant_a": 1.0}]
        # une piste : I R
        c, = self.mesurer(d, [piste(22, 10, 32, 10, 1.0, "Haut", "VOUT")])
        self.assertAlmostEqual(c["chutes_mv"]["R1.1"], R_10MM * 1000.0, places=3)
        # deux pistes en parallèle : I R / 2
        c, = self.mesurer(d, [piste(22, 10, 32, 10, 1.0, "Haut", "VOUT"), piste(22, 10, 32, 10, 1.0, "Haut", "VOUT")])
        self.assertAlmostEqual(c["chutes_mv"]["R1.1"], R_10MM * 500.0, places=3)
        # dérivation en T au milieu, deux charges de 1 A : 2 A sur la première moitié
        d["composants"].append(piece("R2", [pastille("1", "VOUT", 0.0, 0.0)], pose=(27.0, 15.0, 0.0, False)))
        d["charges"].append({"broche": "R2.1", "pastilles": ["R2#0"], "courant_a": 1.0})
        c, = self.mesurer(d, [piste(22, 10, 32, 10, 1.0, "Haut", "VOUT"), piste(27, 10, 27, 15, 1.0, "Haut", "VOUT")])
        for b in ("R1.1", "R2.1"):
            self.assertAlmostEqual(c["chutes_mv"][b], 1.5 * R_10MM * 1000.0, places=3)
        # charge non reliée : non écrite
        c, = self.mesurer(d, [piste(22, 10, 32, 10, 1.0, "Haut", "VOUT")])
        self.assertEqual(sorted(c["chutes_mv"]), ["R1.1"])

    def test_sans_convertisseur(self):
        m = mconv.mesurer(donnees(), placement_v2(), routage())
        self.assertEqual(m, {"format": "mesure_convertisseurs/1", "convertisseurs": []})

    def test_ligne_de_commande(self):
        sortie = io.StringIO()
        with contextlib.redirect_stderr(sortie), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mconv.main([]), 2)
            self.assertEqual(proprete.main([]), 2)
        self.assertNotIn("Traceback", sortie.getvalue())


def convertisseur_metroid():
    """Convertisseur de config_carte.json (9b5b5a3) complété : nœud NetC19_1 (exemple de §2.4), boucle chaude sur
    les condensateurs d'entrée."""
    cfg = _lire(os.path.join(ICI, "config_carte.json"))["convertisseurs"][0]
    return dict(cfg, topologie="abaisseur", commutation="synchrone", reseau_commutation="NetC19_1",
                cond_sortie=[cfg["cond_sortie"]], cond_bulk=[cfg["cond_bulk"]], cond_pilote=[cfg["cond_pilote"]],
                cond_bootstrap=[], origine="deduit",
                boucle_chaude={"condensateurs": list(cfg["cond_entree"]), "reseau_rail": cfg["reseau_entree"],
                               "reseau_masse": cfg["reseau_masse"]})


def groupes_jumeaux_v1(reseaux):
    """Correspondance des réseaux jumeaux de METROID par la règle de nom de 9b5b5a3 (test seulement : les données
    régénérées porteront groupes[].correspondance_reseaux)."""
    import re
    jum = {}
    for n in reseaux:
        m = re.match(r'^(.*_[A-Z]+)(\d)(.*)$', n)
        if m:
            jum.setdefault((m.group(1), m.group(3)), {})[m.group(2)] = n
    return [{"nom": "jumeaux_v1", "type": "repete",
             "correspondance_reseaux": [sorted(v.values()) for v in jum.values() if len(v) == 2]}]


class TestMetroidC5(unittest.TestCase):
    def setUp(self):
        if not donnees_v1_presentes() or not os.path.isdir(REV4):
            self.skipTest("données v1 de 9b5b5a3 absentes (retirées au jalon B2 : tests refaits en C9)")

    def test_rev4_proprete_et_convertisseur(self):
        pl = _lire(os.path.join(REV4, "placements_routage.json"))
        rt = _lire(os.path.join(REV4, "routage_detaille.json"))
        d = adapter_metroid_v1([rt], True)
        d["convertisseurs"] = [convertisseur_metroid()]
        d["groupes"] = groupes_jumeaux_v1(set(s["net_name"] for s in rt["pistes"]))
        p = proprete.mesurer(d, pl, rt)
        self.assertEqual((p["segments"], p["segments_courts"], p["coudes_par_connexion"], p["vias_par_connexion"]),
                         (2421, 627, 1.875, 0.597))
        c, = mconv.mesurer(d, pl, rt)["convertisseurs"]
        sys.stderr.write("\n  rev4 propreté : %s\n  rev4 convertisseur : %s\n"
                         % (json.dumps(p, ensure_ascii=False), json.dumps(c, ensure_ascii=False)))
        sys.stderr.write("  écart des jumeaux : longueur %.3f (référence 0,34), vias %.3f (référence 0,45), %d paires\n"
                         % (p["ecart_jumeaux"]["longueur"], p["ecart_jumeaux"]["vias"], p["paires_jumelles"]))
        self.assertEqual(c["noeud_vias"], 0)
        self.assertIsNotNone(c["sensibles_distance_min_mm"])
        self.assertTrue(c["boucle_chaude_evaluee"])


def _extraire_options(argv):
    """Retire ``--references DOSSIER`` (ou ``--references=DOSSIER``) de ``argv`` ; rend le dossier."""
    dossier = None
    reste = [argv[0]]
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--references" and i + 1 < len(argv):
            dossier = argv[i + 1]
            i += 2
            continue
        if a.startswith("--references="):
            dossier = a.split("=", 1)[1]
            i += 1
            continue
        reste.append(a)
        i += 1
    argv[:] = reste
    return dossier


if __name__ == "__main__":
    REFERENCES = _extraire_options(sys.argv)
    gc.configurer_flux()
    unittest.main()
