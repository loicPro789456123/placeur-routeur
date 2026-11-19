# -*- coding: utf-8 -*-
"""Tests des contrôles indépendants du lot C : ``drc_maison`` (§4.1 à §4.4, ``verdict/2``) et ``verifier_placement``
(§4.5, ``controle_placement/1`` : un test par type, exclusion des poses d'entrée, ligne de commande, attendus de
placement du corpus, METROID sans grille).

    python test_controles.py -v [--references DOSSIER]

--references DOSSIER : dossier local des références figées (ei_1038/) pour la couture de METROID (C5).

Un test par ligne de §4.2 dans les deux modes, un test par type de §4.4, la dérogation, les avertissements, une
carte 2 couches sans plan, sans découpe et sans convertisseur, la ligne de commande, la boucle sur les cas de validité
du corpus du lot E (``corpus/validite/``, champs comparés : valide, liaisons_ouvertes, violations_par_type,
derogations) et, si les données v1 de 9b5b5a3 sont présentes, METROID (copie figée rev4, mode strict) : mesures du
verdict égales à ``mesures.mesurer``.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import drc_maison  # noqa: E402
import formats  # noqa: E402
import geom_commune as gc  # noqa: E402
import mesures  # noqa: E402
import test_mesures as tm  # noqa: E402  (fabriques de données synthétiques et adaptateur METROID)
import verifier_placement as vp  # noqa: E402
import mesure_convertisseurs as mconv  # noqa: E402
import proprete  # noqa: E402
import vias_couture as vc  # noqa: E402

CORPUS = os.path.join(ICI, "corpus", "validite")
CHAMPS_CORPUS = ("valide", "liaisons_ouvertes", "violations_par_type", "derogations")
W = 0.25    # largeur des pistes des tests (mm)
REFERENCES = None          # dossier des références figées (--references, contient ei_1038/)


def carte(reseaux=("A", "B"), composants=(), **k):
    return tm.donnees(reseaux=[tm.reseau(n) for n in reseaux], composants=list(composants), **k)


def deux_pastilles(net="A", p1=(10.0, 10.0), p2=(20.0, 10.0), couches=("Haut",)):
    """Deux pièces d'une pastille 1 x 1 mm du réseau ``net``."""
    return [tm.piece("U1", [tm.pastille("1", net, 0.0, 0.0, couches=couches)], pose=(p1[0], p1[1], 0.0, False)),
            tm.piece("U2", [tm.pastille("1", net, 0.0, 0.0, couches=couches)], pose=(p2[0], p2[1], 0.0, False))]


def verdicts(d, pistes=(), vias=(), surfaces=(), couture=None):
    rt = tm.routage(pistes, vias)
    rt["surfaces"] = list(surfaces)
    return {m: drc_maison.verifier(d, tm.placement_v2(), rt, m, couture) for m in drc_maison.LES_MODES}


def surface(points_mm, couche, net):
    return {"layer": couche, "net": net, "contour": [[x / tm.MIL, y / tm.MIL] for x, y in points_mm], "trous": []}


# ---------------------------------------------------------------------------------------------------------------------
# §4.2 : contacts, une ligne par test, deux modes
# ---------------------------------------------------------------------------------------------------------------------
class TestContacts(unittest.TestCase):
    def liaisons(self, v):
        return v["strict"]["liaisons_ouvertes"], v["altium"]["liaisons_ouvertes"]

    def test_piste_piste(self):
        # extrémité de P0 à 0,2 mm de l'axe de P1 (demi-largeur 0,125) : recouvrement sans contact strict
        d = carte(composants=deux_pastilles(p2=(15.2, 15.0)))
        v = verdicts(d, [tm.piste(10, 10, 15, 10, W, "Haut", "A"), tm.piste(15.2, 9, 15.2, 15, W, "Haut", "A")])
        self.assertEqual(self.liaisons(v), (1, 0))
        v = verdicts(d, [tm.piste(10, 10, 15.1, 10, W, "Haut", "A"), tm.piste(15.2, 9, 15.2, 15, W, "Haut", "A")])
        self.assertEqual(self.liaisons(v), (0, 0))

    def test_piste_pastille(self):
        d = carte(composants=deux_pastilles())
        # extrémité à 0,1 mm du bord de la pastille de U2 (bord à x = 19,5)
        v = verdicts(d, [tm.piste(10, 10, 19.4, 10, W, "Haut", "A")])
        self.assertEqual(self.liaisons(v), (1, 0))
        v = verdicts(d, [tm.piste(10, 10, 19.6, 10, W, "Haut", "A")])
        self.assertEqual(self.liaisons(v), (0, 0))

    def test_piste_via(self):
        d = carte(composants=deux_pastilles(couches=("Haut", "Bas")))
        d["composants"][1]["pastilles"][0]["couches"] = ["Bas"]
        pistes = [tm.piste(10, 10, 14.65, 10, W, "Haut", "A"), tm.piste(15, 10, 20, 10, W, "Bas", "A")]
        v = verdicts(d, pistes, [tm.via(15, 10, 0.6, 0.3, "A")])           # 0,35 > rayon 0,3
        self.assertEqual(self.liaisons(v), (1, 0))
        pistes[0] = tm.piste(10, 10, 14.75, 10, W, "Haut", "A")
        self.assertEqual(self.liaisons(verdicts(d, pistes, [tm.via(15, 10, 0.6, 0.3, "A")])), (0, 0))

    def test_via_pastille(self):
        d = carte(composants=deux_pastilles())
        d["composants"][1]["pastilles"][0]["couches"] = ["Bas"]
        pistes = [tm.piste(20, 10, 20, 12, W, "Bas", "A"), tm.piste(10.6, 12, 20, 12, W, "Bas", "A")]
        v = verdicts(d, pistes + [tm.piste(10.6, 10, 10.6, 12, W, "Bas", "A")], [tm.via(10.6, 10, 0.6, 0.3, "A")])
        self.assertEqual(self.liaisons(v), (1, 0))                           # centre à 0,1 mm hors du cuivre
        v = verdicts(d, pistes + [tm.piste(10.4, 10, 10.6, 12, W, "Bas", "A")], [tm.via(10.4, 10, 0.6, 0.3, "A")])
        self.assertEqual(self.liaisons(v), (0, 0))

    def test_via_via(self):
        d = carte(composants=deux_pastilles())
        d["composants"][1]["pastilles"][0]["couches"] = ["Bas"]
        pistes = [tm.piste(10, 10, 15, 10, W, "Haut", "A"), tm.piste(15.5, 10, 20, 10, W, "Bas", "A")]
        vias = [tm.via(15, 10, 0.6, 0.3, "A"), tm.via(15.5, 10, 0.6, 0.3, "A")]
        self.assertEqual(self.liaisons(verdicts(d, pistes, vias)), (1, 0))   # centres à 0,5 > max(rayons) 0,3
        pistes[1] = tm.piste(15.25, 10, 20, 10, W, "Bas", "A")
        vias[1] = tm.via(15.25, 10, 0.6, 0.3, "A")
        self.assertEqual(self.liaisons(verdicts(d, pistes, vias)), (0, 0))

    def test_surface_recouvrement(self):
        d = carte(composants=deux_pastilles())
        s = surface([(10.4, 9.8), (19.6, 9.8), (19.6, 10.2), (10.4, 10.2)], "Haut", "A")
        self.assertEqual(self.liaisons(verdicts(d, surfaces=[s])), (0, 0))
        s = surface([(10.6, 9.8), (19.6, 9.8), (19.6, 10.2), (10.6, 10.2)], "Haut", "A")
        self.assertEqual(self.liaisons(verdicts(d, surfaces=[s])), (1, 1))

    def test_preexistants_recouvrement(self):
        # région de la pièce U1 qui recouvre ses deux pastilles : connectées dans les deux modes sans routage
        reg = {"id": None, "couches": ["Haut"], "face": "composant", "pastille": None, "reseau": "A",
               "liaison": None, "points": [[0.4, -0.1], [2.6, -0.1], [2.6, 0.1], [0.4, 0.1]],
               "classe_isolement": 0}
        u1 = tm.piece("U1", [tm.pastille("1", "A", 0, 0), tm.pastille("2", "A", 3, 0)], regions=[reg])
        self.assertEqual(self.liaisons(verdicts(carte(composants=[u1]))), (0, 0))
        reg["points"] = [[0.6, -0.1], [2.6, -0.1], [2.6, 0.1], [0.6, 0.1]]
        self.assertEqual(self.liaisons(verdicts(carte(composants=[u1]))), (1, 1))

    def _plan(self, x_via2):
        zone = {"reseau": "A", "contour": [[5, 5], [30, 5], [30, 20], [5, 20]], "trous": []}
        d = tm.donnees(couches=("Haut", "Plan", "Bas"), reseaux=[tm.reseau("A")], composants=deux_pastilles(),
                       zones={1: [zone]})
        pistes = [tm.piste(10, 10, 10, 12, W, "Haut", "A"), tm.piste(20, 10, x_via2, 12, W, "Haut", "A")]
        return d, pistes, [tm.via(10, 12, 0.6, 0.3, "A"), tm.via(x_via2, 12, 0.6, 0.3, "A")]

    def test_zone_via(self):
        d, pistes, vias = self._plan(29.5)      # à 0,5 du bord < rayon 0,3 + isolement de connexion 0,5
        self.assertEqual(self.liaisons(verdicts(d, pistes, vias)), (1, 0))
        d, pistes, vias = self._plan(29.0)      # à 1,0 du bord
        self.assertEqual(self.liaisons(verdicts(d, pistes, vias)), (0, 0))

    def test_zone_via_hors_zone(self):
        d, pistes, vias = self._plan(31.0)
        self.assertEqual(self.liaisons(verdicts(d, pistes, vias)), (1, 1))

    def test_zone_pastille_cms(self):
        zone = {"reseau": "A", "contour": [[5, 5], [20.2, 5], [20.2, 20], [5, 20]], "trous": []}
        d = tm.donnees(couches=("Haut", "Bas"), reseaux=[tm.reseau("A")],
                       composants=deux_pastilles(couches=("Bas",)), zones={1: [zone]})
        v = verdicts(d)         # U2 dépasse de la zone : recouvrement seulement
        self.assertEqual(self.liaisons(v), (1, 0))
        zone["contour"] = [[5, 5], [21, 5], [21, 20], [5, 20]]
        self.assertEqual(self.liaisons(verdicts(d)), (0, 0))

    def test_zone_autre_reseau_ni_contact_ni_violation(self):
        zone = {"reseau": "B", "contour": [[5, 5], [30, 5], [30, 20], [5, 20]], "trous": []}
        d = tm.donnees(couches=("Haut", "Plan", "Bas"), reseaux=[tm.reseau("A"), tm.reseau("B")],
                       composants=deux_pastilles(), zones={1: [zone]})
        v = verdicts(d, [tm.piste(10, 10, 15, 10, W, "Haut", "A"), tm.piste(15, 10, 18, 10, W, "Bas", "A"),
                         tm.piste(18, 10, 20, 10, W, "Haut", "A")],
                     [tm.via(15, 10, 0.6, 0.3, "A"), tm.via(18, 10, 0.6, 0.3, "A")])
        for m in drc_maison.LES_MODES:
            self.assertEqual(v[m]["violations"], 0, m)
            self.assertEqual(v[m]["liaisons_ouvertes"], 0, m)
            self.assertEqual(v[m]["avertissements"]["zones_plan_isolees"], 1, m)


# ---------------------------------------------------------------------------------------------------------------------
# §4.4 : un test par type de violation
# ---------------------------------------------------------------------------------------------------------------------
class TestViolations(unittest.TestCase):
    def compte(self, v, genre):
        a, b = v["strict"]["violations_par_type"][genre], v["altium"]["violations_par_type"][genre]
        self.assertEqual(a, b, genre)
        return a

    def base(self, **k):
        return carte(composants=deux_pastilles(), **k)

    def relie(self):
        return [tm.piste(10, 10, 20, 10, W, "Haut", "A")]

    def test_isolement(self):
        v = verdicts(self.base(), self.relie() + [tm.piste(10, 10.8, 20, 10.8, W, "Haut", "B"),
                                                 tm.piste(10, 10.8, 10, 15, W, "Haut", "B")])
        # 0,8 - 0,5 - 0,125 = 0,175 < 0,2 : P1 contre les deux pastilles, P2 contre celle de U1
        self.assertEqual(self.compte(v, "isolement"), 3)
        self.assertAlmostEqual(v["strict"]["marge_isolement_min_mm"], -0.025, places=9)
        self.assertIn("U1#0(A)", v["strict"]["marge_isolement_min_paire"])

    def test_isolement_classes(self):
        d = self.base()
        d["regles"]["isolement"].update({"classes": ["a", "b"], "matrice": [[0.2, 0.1], [0.1, 0.1]]})
        d["reseaux"][1]["classes_isolement"] = {"piste": 1, "via": 1, "surface": 1}
        d["composants"][0]["pastilles"][0]["classe_isolement"] = 1
        d["composants"][1]["pastilles"][0]["classe_isolement"] = 1
        v = verdicts(d, self.relie() + [tm.piste(10, 10.8, 20, 10.8, W, "Haut", "B")])
        self.assertEqual(self.compte(v, "isolement"), 0)   # requis 0,1 entre classes 1 et 1

    def test_garde_bord(self):
        v = verdicts(self.base(), self.relie() + [tm.piste(10, 10, 10, 0.3, W, "Haut", "A")])
        self.assertEqual(self.compte(v, "garde_bord"), 1)

    def test_garde_bord_exemption_pastille(self):
        u = [tm.piece("U1", [tm.pastille("1", "A", 0, 0)], pose=(10.0, 0.5, 0.0, False)),
             tm.piece("U2", [tm.pastille("1", "A", 0, 0)], pose=(20.0, 10.0, 0.0, False))]
        d = carte(composants=u)
        pistes = [tm.piste(10, 0.3, 10, 10, W, "Haut", "A"), tm.piste(10, 10, 20, 10, W, "Haut", "A")]
        self.assertEqual(self.compte(verdicts(d, pistes), "garde_bord"), 0)
        pistes[0] = tm.piste(11, 0.3, 11, 10, W, "Haut", "A")
        pistes[1] = tm.piste(11, 10, 20, 10, W, "Haut", "A")
        self.assertEqual(self.compte(verdicts(d, pistes), "garde_bord"), 1)

    def test_garde_decoupe(self):
        d = self.base()
        d["carte"]["decoupes"] = [{"id": "D0", "points": [[14, 10.3], [16, 10.3], [16, 12], [14, 12]],
                                   "proprietaire": None}]
        self.assertEqual(self.compte(verdicts(d, self.relie()), "garde_decoupe"), 1)

    def test_garde_trou(self):
        trou = {"id": "T0", "x": 15.0, "y": 10.5, "rotation": 0.0,
                "trou": {"diametre": 0.5, "forme": "rond", "longueur": 0.0, "rotation": 0.0, "decalage": [0, 0]},
                "metallise": False, "diametre_cuivre": 0.0, "rayon_interdit_mm": 0.5, "classe_isolement": 0,
                "piece": None}
        self.assertEqual(self.compte(verdicts(self.base(trous=[trou]), self.relie()), "garde_trou"), 1)

    def test_zone_interdite_elargie(self):
        d = self.base()
        d["zones_routage_interdites"] = [{"id": "K0", "couches": ["toutes"], "forme": "polygone",
                                          "points": [[14, 10.3], [16, 10.3], [16, 12], [14, 12]], "largeur": 0.0,
                                          "effets": ["pistes"], "etiquette": ""}]
        # écart 0,3 - 0,125 = 0,175 < isolement.defaut 0,2
        self.assertEqual(self.compte(verdicts(d, self.relie()), "zone_interdite"), 1)
        d["zones_routage_interdites"][0]["effets"] = ["vias"]
        self.assertEqual(self.compte(verdicts(d, self.relie()), "zone_interdite"), 0)

    def test_largeur_millieme_par_exces(self):
        self.assertEqual(drc_maison.borne_mil(0.2), 7.875)
        self.assertEqual(drc_maison.borne_mil(0.254), 10.0)
        v = verdicts(self.base(), [tm.piste(10, 10, 20, 10, 0.15, "Haut", "A")])
        self.assertEqual(self.compte(v, "largeur"), 1)
        v = verdicts(self.base(), [dict(tm.piste(10, 10, 20, 10, 0.2, "Haut", "A"), width=7.875)])
        self.assertEqual(self.compte(v, "largeur"), 0)
        v = verdicts(self.base(), [dict(tm.piste(10, 10, 20, 10, 0.2, "Haut", "A"), width=7.874)])
        self.assertEqual(self.compte(v, "largeur"), 1)

    def test_dimension_via_du_reseau(self):
        d = carte(composants=deux_pastilles())
        d["composants"][1]["pastilles"][0]["couches"] = ["Bas"]
        pistes = [tm.piste(10, 10, 15, 10, W, "Haut", "A"), tm.piste(15, 10, 20, 10, W, "Bas", "A")]
        self.assertEqual(self.compte(verdicts(d, pistes, [tm.via(15, 10, 0.6, 0.3, "A")]), "dimension_via"), 0)
        d["reseaux"][0]["via"]["diametre_min"] = 0.7
        self.assertEqual(self.compte(verdicts(d, pistes, [tm.via(15, 10, 0.6, 0.3, "A")]), "dimension_via"), 1)

    def test_via_sous_cms_et_derogation(self):
        d = carte(composants=deux_pastilles())
        d["composants"][1]["pastilles"][0]["couches"] = ["Bas"]
        pistes = [tm.piste(10, 10, 20, 10, W, "Bas", "A")]
        v = verdicts(d, pistes, [tm.via(10, 10, 0.6, 0.3, "A")])
        self.assertEqual(self.compte(v, "via_sous_cms"), 1)
        d["regles"]["via_dans_pastille_meme_reseau"] = True
        v = verdicts(d, pistes, [tm.via(10, 10, 0.6, 0.3, "A")])
        self.assertEqual(self.compte(v, "via_sous_cms"), 0)
        self.assertEqual(v["strict"]["derogations"]["via_dans_pastille_meme_reseau"], 1)
        d["composants"][0]["pastilles"][0]["via_dans_pastille_permis"] = True
        v = verdicts(d, pistes, [tm.via(10, 10, 0.6, 0.3, "A")])
        self.assertEqual(v["strict"]["derogations"]["via_dans_pastille_meme_reseau"], 0)

    def test_trou_trou(self):
        v = verdicts(self.base(), self.relie(), [tm.via(30, 30, 0.6, 0.3, "A"), tm.via(30.5, 30, 0.6, 0.3, "A")])
        self.assertEqual(self.compte(v, "trou_trou"), 1)    # parois à 0,2 < 0,254, même réseau compris

    def test_antenne(self):
        v = verdicts(self.base(), self.relie() + [tm.piste(15, 10, 15, 14, W, "Haut", "A")])
        self.assertEqual(self.compte(v, "antenne"), 1)
        self.assertEqual(v["strict"]["liaisons_ouvertes"], 0)

    def test_couche_non_permise(self):
        zone = {"reseau": "B", "contour": [[1, 1], [2, 1], [2, 2], [1, 2]], "trous": []}
        d = tm.donnees(couches=("Haut", "Plan", "Bas"), reseaux=[tm.reseau("A"), tm.reseau("B")],
                       composants=deux_pastilles(couches=("Haut", "Plan", "Bas")), zones={1: [zone]})
        self.assertEqual(self.compte(verdicts(d, [tm.piste(10, 10, 20, 10, W, "Plan", "A")]),
                                     "couche_non_permise"), 1)
        d["reseaux"][0]["couches_permises"] = ["Bas"]
        self.assertEqual(self.compte(verdicts(d, [tm.piste(10, 10, 20, 10, W, "Haut", "A")]),
                                     "couche_non_permise"), 1)

    def test_reseau_inconnu(self):
        v = verdicts(self.base(), self.relie() + [tm.piste(30, 30, 35, 30, W, "Haut", "X")],
                     [tm.via(40, 30, 0.6, 0.3, "Y")])
        self.assertEqual(self.compte(v, "reseau_inconnu"), 2)

    def test_details_et_exemples_bornes(self):
        pistes = [tm.piste(10, 10, 20, 10, 0.1, "Haut", "A") for _ in range(1100)]
        v = drc_maison.verifier(self.base(), tm.placement_v2(), tm.routage(pistes))
        self.assertEqual(v["violations_par_type"]["largeur"], 1100)
        self.assertEqual(len(v["detail_violations"]), 1000)
        self.assertTrue(v["detail_violations_tronque"])
        self.assertTrue(all(len(x) <= 15 for x in v["exemples"].values()))


class TestAvertissementsEtCarteSimple(unittest.TestCase):
    def test_carte_deux_couches_sans_plan(self):
        d = carte(composants=deux_pastilles())
        v = verdicts(d, [tm.piste(10, 10, 15, 10, W, "Haut", "A"), tm.piste(15, 10, 20, 10, W, "Bas", "A")],
                     [tm.via(15, 10, 0.6, 0.3, "A")])
        d["composants"][1]["pastilles"][0]["couches"] = ["Bas"]
        v = verdicts(d, [tm.piste(10, 10, 15, 10, W, "Haut", "A"), tm.piste(15, 10, 20, 10, W, "Bas", "A")],
                     [tm.via(15, 10, 0.6, 0.3, "A")])
        for m in drc_maison.LES_MODES:
            self.assertTrue(v[m]["valide"], (m, v[m]["violations_par_type"], v[m]["detail_liaisons_ouvertes"]))
            self.assertEqual((v[m]["reseaux_a_router"], v[m]["reseaux_complets"]), (1, 1))
            self.assertEqual(sorted(v[m]["violations_par_type"]), sorted(drc_maison.TYPES_VIOLATIONS))
            self.assertEqual(sorted(v[m]["avertissements"]), sorted(drc_maison.AVERTISSEMENTS))
        self.assertEqual(formats.VALIDATEURS["verdict"](v["strict"]), [])

    def test_liaison_ouverte_detail(self):
        v = verdicts(carte(composants=deux_pastilles()))
        dl = v["strict"]["detail_liaisons_ouvertes"]
        self.assertEqual(len(dl), 1)
        self.assertEqual((dl[0]["nature"], dl[0]["noms"]), ("pastilles", ["U2.1"]))

    def test_ilot(self):
        v = verdicts(carte(composants=deux_pastilles()), [tm.piste(10, 10, 20, 10, W, "Haut", "A"),
                                                          tm.piste(30, 30, 35, 30, W, "Haut", "A")])
        self.assertEqual(v["strict"]["liaisons_ouvertes"], 1)
        self.assertEqual(v["strict"]["detail_liaisons_ouvertes"][0]["nature"], "ilot")

    def test_reseau_d_un_membre_ignore(self):
        d = carte(composants=[tm.piece("U1", [tm.pastille("1", "A", 0, 0)])])
        v = verdicts(d)
        self.assertEqual((v["strict"]["liaisons_ouvertes"], v["strict"]["reseaux_a_router"]), (0, 0))

    def test_paires_et_regles_non_couvertes(self):
        d = carte(composants=deux_pastilles() + [
            tm.piece("U3", [tm.pastille("1", "B", 0, 0)], pose=(10.0, 20.0, 0.0, False)),
            tm.piece("U4", [tm.pastille("1", "B", 0, 0)], pose=(25.0, 20.0, 0.0, False))])
        d["paires"] = [{"nom": "P", "positif": "A", "negatif": "B", "ecart_mm": None, "largeur_mm": None,
                        "tolerance_longueur_mm": None}]
        d["parametres_deduction"] = {"regles_non_interpretees": 3}
        v = verdicts(d, [tm.piste(10, 10, 20, 10, W, "Haut", "A"), tm.piste(10, 20, 25, 20, W, "Haut", "B")])
        self.assertEqual(v["strict"]["avertissements"]["paires_desequilibrees"], 1)
        self.assertEqual(v["strict"]["avertissements"]["regles_non_couvertes"], 3)

    def test_sans_piste_dessous(self):
        u = tm.piece("L1", [tm.pastille("1", "A", -3, 0), tm.pastille("2", "B", 3, 0)], pose=(25, 25, 0, False))
        u["lbb"] = [-4, -2, 4, 2]
        d = carte(composants=[u])
        d["sans_piste_dessous"] = ["L1"]
        v = verdicts(d, [tm.piste(20, 26.5, 30, 26.5, W, "Haut", "C")])
        self.assertEqual(v["strict"]["avertissements"]["pistes_sous_pieces_sans_piste_dessous"], 1)

    def test_aucune_variable_globale(self):
        with open(os.path.join(ICI, "drc_maison.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("os.environ", src)
        self.assertNotIn("global ", src)


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------
class TestLigneDeCommande(unittest.TestCase):
    def lancer(self, *args):
        p = subprocess.run([sys.executable, "-I", os.path.join(ICI, "drc_maison.py")] + list(args),
                           capture_output=True, text=True, encoding="utf-8", cwd=ICI)
        return p.returncode, p.stdout + p.stderr

    def test_sans_argument(self):
        code, sortie = self.lancer()
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", sortie)

    def test_cas_du_corpus(self):
        cas = os.path.join(CORPUS, "013_antenne")
        if not os.path.isdir(cas):
            self.skipTest("corpus absent")
        with tempfile.TemporaryDirectory() as t:
            s = os.path.join(t, "v.json")
            code, sortie = self.lancer("--donnees", os.path.join(cas, "donnees_placeur.json"),
                                       "--placement", os.path.join(cas, "placements_routage.json"),
                                       "--routage", os.path.join(cas, "routage_detaille.json"),
                                       "--mode", "strict", "--sortie", s)
            with open(os.path.join(cas, "attendu_strict.json"), encoding="utf-8") as f:
                attendu = json.load(f)
            self.assertEqual(code, 0 if attendu["valide"] else 1, sortie)
            formats.charger(s, "verdict/2")
            self.assertNotIn(t, sortie)


# ---------------------------------------------------------------------------------------------------------------------
# Corpus du lot E
# ---------------------------------------------------------------------------------------------------------------------
class TestCorpus(unittest.TestCase):
    def test_cas_de_validite(self):
        if not os.path.isdir(CORPUS):
            self.skipTest("corpus/validite absent")
        ecarts = {m: [] for m in drc_maison.LES_MODES}
        cas = sorted(c for c in os.listdir(CORPUS) if os.path.isfile(os.path.join(CORPUS, c, "cas.json")))
        for nom in cas:
            dossier = os.path.join(CORPUS, nom)
            def lire(f, dossier=dossier):
                with open(os.path.join(dossier, f), encoding="utf-8") as fl:
                    return json.load(fl)
            fic = lire("cas.json")["fichiers"]
            couture = lire(fic["couture"]) if fic.get("couture") else None
            for m in drc_maison.LES_MODES:
                v = drc_maison.verifier(lire(fic["donnees"]), lire(fic["placement"]), lire(fic["routage"]), m,
                                        couture)
                a = lire(fic["attendu_" + m])
                for champ in CHAMPS_CORPUS:
                    if v[champ] != a[champ]:
                        ecarts[m].append("%s %s : %r au lieu de %r" % (nom, champ, v[champ], a[champ]))
        for m in drc_maison.LES_MODES:
            sys.stderr.write("\n  corpus (%s) : %d cas, %d écart\n" % (m, len(cas), len(ecarts[m])))
        self.assertEqual(ecarts, {m: [] for m in drc_maison.LES_MODES})


# ---------------------------------------------------------------------------------------------------------------------
# METROID (adaptateur des données v1 de 9b5b5a3, copie figée rev4)
# ---------------------------------------------------------------------------------------------------------------------
class TestMetroid(unittest.TestCase):
    def test_rev4_strict_mesures_egales_a_mesurer(self):
        if not tm.donnees_v1_presentes() or not os.path.isdir(tm.REV4):
            self.skipTest("données v1 de 9b5b5a3 absentes (retirées au jalon B2 : tests refaits en C9)")
        pl = tm._lire(os.path.join(tm.REV4, "placements_routage.json"))
        rt = tm._lire(os.path.join(tm.REV4, "routage_detaille.json"))
        d = tm.adapter_metroid_v1([rt], True)
        t0 = time.time()
        v = drc_maison.verifier(d, pl, rt, "strict")
        duree = time.time() - t0
        sys.stderr.write("\n  METROID rev4 strict : %.2f s ; liaisons %d ; %s\n"
                         % (duree, v["liaisons_ouvertes"],
                            {k: n for k, n in v["violations_par_type"].items() if n}))
        self.assertLess(duree, 120.0)
        m = mesures.mesurer(d, pl, rt)
        m_v = mesures.mesurer(d, pl, rt, verdict_strict=v)
        self.assertEqual(m, m_v)
        self.assertEqual(formats.VALIDATEURS["verdict"](v), [])

    def test_rev4_placement_sans_grille(self):
        if not tm.donnees_v1_presentes() or not os.path.isdir(tm.REV4):
            self.skipTest("données v1 de 9b5b5a3 absentes (retirées au jalon B2 : tests refaits en C9)")
        pl = tm._lire(os.path.join(tm.REV4, "placements_routage.json"))
        rt = tm._lire(os.path.join(tm.REV4, "routage_detaille.json"))
        v = vp.verifier(pl, tm.adapter_metroid_v1([rt], True), grille=False)
        sys.stderr.write("\n  METROID rev4 placement (sans grille) : %s\n"
                         % {k: n for k, n in v["violations_par_type"].items() if n})
        self.assertEqual(formats.VALIDATEURS["controle_placement"](v), [])


# ---------------------------------------------------------------------------------------------------------------------
# §4.5 : contrôle du placement, un test par type
# ---------------------------------------------------------------------------------------------------------------------
def pp(*entrees):
    """Placement placements/2 : entrées (des, x_mm, y_mm[, rot[, dessous]])."""
    return tm.placement_v2(*[(e[0], e[1], e[2], e[3] if len(e) > 3 else 0.0, e[4] if len(e) > 4 else False)
                             for e in entrees])


def pieces_pl(n=2, nets=("A", "B")):
    """Pièces U1..Un (boîte 2 x 2 mm, une pastille 1 x 1 mm au centre)."""
    return [tm.piece("U%d" % (i + 1), [tm.pastille("1", nets[i % len(nets)], 0.0, 0.0)], pose=(10.0 + 5 * i, 10.0, 0.0, False))
            for i in range(n)]


class TestPlacement(unittest.TestCase):
    def ctrl(self, d, pl, grille=False):
        v = vp.verifier(pl, d, grille=grille)
        self.assertEqual(formats.VALIDATEURS["controle_placement"](v), [])
        return v["violations_par_type"]

    def test_valide(self):
        d = carte(composants=pieces_pl())
        v = vp.verifier(pp(("U1", 10.0, 10.0), ("U2", 15.0, 10.0)), d, grille=False)
        self.assertTrue(v["valide"], v["violations"])

    def test_absente_inconnue(self):
        d = carte(composants=pieces_pl())
        t = self.ctrl(d, pp(("U1", 10.0, 10.0), ("X9", 30.0, 30.0)))
        self.assertEqual((t["absente"], t["inconnue"]), (1, 1))

    def test_fixe_deplacee(self):
        cs = pieces_pl()
        cs[0]["statut"] = "fixe"
        d = carte(composants=cs)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 15.0, 10.0)))["fixe_deplacee"], 0)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.01, 10.0), ("U2", 15.0, 10.0)))["fixe_deplacee"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0, 90.0), ("U2", 15.0, 10.0)))["fixe_deplacee"], 1)

    def test_orientation(self):
        d = carte(composants=pieces_pl())
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0, 90.0), ("U2", 15.0, 10.0)))["orientation"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0, 0.0, True), ("U2", 15.0, 10.0)))["orientation"], 1)

    def test_grille(self):
        d = carte(composants=pieces_pl())
        self.assertEqual(self.ctrl(d, pp(("U1", 10.16, 10.16), ("U2", 15.24, 10.16)), grille=True)["grille"], 0)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.16), ("U2", 15.24, 10.16)), grille=True)["grille"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.16), ("U2", 15.24, 10.16)), grille=False)["grille"], 0)

    def test_chevauchement_boites(self):
        d = carte(composants=pieces_pl())
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 11.5, 10.0)))["chevauchement"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 12.6, 10.0)))["chevauchement"], 0)
        # Boîte tournée de 45° : le coin s'approche à moins de 2 x 0,25 mm.
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 12.6, 10.0, 45.0)))["chevauchement"], 1)

    def test_chevauchement_pastille_face_opposee(self):
        cs = pieces_pl()
        cs[1]["pastilles"][0]["couches"] = ["Haut", "Bas"]
        cs[1]["pastilles"][0]["lx"] = -2.0
        cs[1]["orientations"] = [[0.0, True]]
        d = carte(composants=cs)
        t = self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 13.0, 10.0, 0.0, True)))
        self.assertEqual(t["chevauchement"], 1)
        t = self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 15.0, 10.0, 0.0, True)))
        self.assertEqual(t["chevauchement"], 0)

    def test_poses_d_entree_exclues(self):
        cs = pieces_pl()
        for c in cs:
            c["statut"] = "fixe"
        cs[1]["pose_cible"] = cs[1]["pose_actuelle"] = [11.0, 10.0, 0.0, False]
        d = carte(composants=cs)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 11.0, 10.0)))["chevauchement"], 0)
        cs[1]["pose_actuelle"] = [30.0, 10.0, 0.0, False]     # fixes_deplaces : contrôlée
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 11.0, 10.0)))["chevauchement"], 1)

    def test_obstacle(self):
        d = carte(composants=pieces_pl())
        d["obstacles_placement"] = [{"faces": ["dessus"], "rect": [11.0, 9.0, 12.0, 11.0], "etiquette": "zone"}]
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 15.0, 10.0)))["obstacle"], 1)
        d["obstacles_placement"][0]["faces"] = ["dessous"]
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 15.0, 10.0)))["obstacle"], 0)
        # Zone interdite d'empreinte à effet placement (suit sa pièce).
        d = carte(composants=pieces_pl())
        d["composants"][1]["zones_interdites"] = [{"id": "K1", "couches": ["toutes"], "forme": "polygone",
                                                   "points": [[-4, -1], [-2, -1], [-2, 1], [-4, 1]],
                                                   "largeur": 0, "effets": ["placement"], "source": "keepout"}]
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 15.0, 10.0)))["obstacle"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 20.0, 10.0)))["obstacle"], 0)

    def test_marge_bord_et_au_bord(self):
        d = carte(composants=pieces_pl())
        self.assertEqual(self.ctrl(d, pp(("U1", 1.5, 20.0), ("U2", 15.0, 10.0)))["marge_bord"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 3.0, 20.0), ("U2", 15.0, 10.0)))["marge_bord"], 0)
        d["au_bord"] = [{"piece": "U1", "cote": "gauche", "debord_mm": 0.0}]
        t = self.ctrl(d, pp(("U1", 1.0, 20.0), ("U2", 15.0, 10.0)))
        self.assertEqual((t["marge_bord"], t["au_bord"]), (0, 0))
        t = self.ctrl(d, pp(("U1", 2.0, 20.0), ("U2", 15.0, 10.0)))
        self.assertEqual(t["au_bord"], 1)
        t = self.ctrl(d, pp(("U1", 1.0, 1.5), ("U2", 15.0, 10.0)))       # trop près du bas : non dispensé
        self.assertEqual(t["marge_bord"], 1)
        d["au_bord"][0]["cote"] = "quelconque"
        self.assertEqual(self.ctrl(d, pp(("U1", 20.0, 39.0), ("U2", 15.0, 10.0)))["au_bord"], 0)

    def test_marge_decoupe(self):
        d = carte(composants=pieces_pl())
        d["carte"]["decoupes"] = [{"id": "D1", "points": [[20, 15], [25, 15], [25, 25], [20, 25]],
                                   "proprietaire": None}]
        self.assertEqual(self.ctrl(d, pp(("U1", 18.5, 20.0), ("U2", 15.0, 10.0)))["marge_decoupe"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 22.0, 20.0), ("U2", 15.0, 10.0)))["marge_decoupe"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 17.5, 20.0), ("U2", 15.0, 10.0)))["marge_decoupe"], 0)

    def test_eloignement(self):
        d = carte(composants=pieces_pl())
        d["eloignements"] = [{"type": "dure", "objets": ["U1", "U1#0"], "reference": ["U2"], "distance_mm": 15.0,
                              "poids_par_mm": 300, "marge_mm": 1.0}]
        v = vp.verifier(pp(("U1", 10.0, 10.0), ("U2", 20.0, 10.0)), d, grille=False)
        self.assertEqual(v["violations_par_type"]["eloignement"], 2)
        self.assertEqual(v["infos"]["eloignements"][0],
                         {"objet": "U1", "reference": "U2", "distance_mm": 10.0, "minimum_mm": 15.0})
        d["eloignements"][0]["type"] = "souple"
        v = vp.verifier(pp(("U1", 10.0, 10.0), ("U2", 20.0, 10.0)), d, grille=False)
        self.assertEqual(v["violations_par_type"]["eloignement"], 0)
        self.assertEqual(len(v["infos"]["eloignements"]), 2)

    def test_pastille_pastille(self):
        cs = pieces_pl()
        for c in cs:
            c["lbb"] = [-0.5, -0.5, 0.5, 0.5]
        d = carte(composants=cs)
        d["regles"]["placement"]["marge_piece_mm"] = 0.0
        t = self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 11.1, 10.0)))
        self.assertEqual((t["pastille_pastille"], t["chevauchement"]), (1, 0))
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 11.3, 10.0)))["pastille_pastille"], 0)
        cs[1]["pastilles"][0]["reseau"] = "A"          # même réseau : pas un défaut d'isolement
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 11.1, 10.0)))["pastille_pastille"], 0)

    def test_zone_imposee(self):
        d = carte(composants=pieces_pl())
        d["zones_imposees"] = [{"pieces": ["U1"], "faces": [], "polygone": [[0, 0], [12, 0], [12, 12], [0, 12]],
                                "etiquette": "z", "source": "contrainte"}]
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 15.0, 10.0)))["zone_imposee"], 0)
        self.assertEqual(self.ctrl(d, pp(("U1", 11.5, 10.0), ("U2", 15.0, 10.0)))["zone_imposee"], 1)
        d["zones_imposees"][0]["faces"] = ["dessous"]
        self.assertEqual(self.ctrl(d, pp(("U1", 10.0, 10.0), ("U2", 15.0, 10.0)))["zone_imposee"], 1)

    def test_groupe_rigide(self):
        cs = pieces_pl()
        for c in cs:
            c["orientations"] = [[0.0, False], [180.0, False]]
        d = carte(composants=cs)
        d["groupes"] = [{"type": "impose", "nom": "G", "pieces": ["U1", "U2"], "rigide": True}]
        self.assertEqual(self.ctrl(d, pp(("U1", 20.0, 20.0), ("U2", 25.0, 20.0)))["groupe_rigide"], 0)
        t = self.ctrl(d, pp(("U1", 25.0, 20.0, 180.0), ("U2", 20.0, 20.0, 180.0)))   # demi-tour d'ensemble
        self.assertEqual(t["groupe_rigide"], 0)
        self.assertEqual(self.ctrl(d, pp(("U1", 20.0, 20.0), ("U2", 26.0, 20.0)))["groupe_rigide"], 1)
        self.assertEqual(self.ctrl(d, pp(("U1", 20.0, 20.0), ("U2", 25.0, 20.0, 180.0)))["groupe_rigide"], 1)
        d["groupes"][0]["rigide"] = False
        self.assertEqual(self.ctrl(d, pp(("U1", 20.0, 20.0), ("U2", 26.0, 20.0)))["groupe_rigide"], 0)

    def test_parametres_lus_dans_les_donnees(self):
        d = carte(composants=pieces_pl())
        pl = pp(("U1", 10.0, 10.0), ("U2", 12.6, 10.0))
        self.assertEqual(self.ctrl(d, pl)["chevauchement"], 0)
        d["regles"]["placement"]["marge_piece_mm"] = 0.4
        self.assertEqual(self.ctrl(d, pl)["chevauchement"], 1)

    def test_doublon_code_2(self):
        d = carte(composants=pieces_pl())
        with self.assertRaises(gc.ErreurEntree):
            vp.verifier(pp(("U1", 10.0, 10.0), ("U1", 10.0, 10.0), ("U2", 15.0, 10.0)), d)


class TestPlacementLigneDeCommande(unittest.TestCase):
    def lancer(self, *args):
        p = subprocess.run([sys.executable, "-I", os.path.join(ICI, "verifier_placement.py")] + list(args),
                           capture_output=True, text=True, encoding="utf-8", cwd=ICI)
        return p.returncode, p.stdout + p.stderr

    def test_sans_argument(self):
        code, sortie = self.lancer()
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", sortie)

    def test_cas_du_corpus_et_negatif(self):
        cas = os.path.join(CORPUS, "013_antenne")
        if not os.path.isdir(cas):
            self.skipTest("corpus absent")
        with tempfile.TemporaryDirectory() as t:
            s = os.path.join(t, "c.json")
            code, sortie = self.lancer("--donnees", os.path.join(cas, "donnees_placeur.json"),
                                       "--placement", os.path.join(cas, "placements_routage.json"), "--sortie", s)
            with open(os.path.join(cas, "attendu_strict.json"), encoding="utf-8") as f:
                attendu = json.load(f)["placement"]
            self.assertEqual(code, 0 if attendu["valide"] else 1, sortie)
            formats.charger(s, "controle_placement/1")
            self.assertNotIn(t, sortie)
            d = os.path.join(t, "d.json")
            p = os.path.join(t, "p.json")
            dn = carte(composants=pieces_pl())
            dn["source"]["construit_par"] = "construire_donnees 2.0.0"
            gc.ecrire_json(d, dn)
            gc.ecrire_json(p, pp(("U1", 10.0, 10.0), ("U2", 11.0, 10.0)))
            code, sortie = self.lancer("--donnees", d, "--placement", p, "--sans-grille")
            self.assertEqual(code, 1, sortie)


class TestCorpusPlacement(unittest.TestCase):
    def test_attendus_de_placement(self):
        if not os.path.isdir(CORPUS):
            self.skipTest("corpus/validite absent")
        ecarts = []
        n = 0
        for nom in sorted(os.listdir(CORPUS)):
            dossier = os.path.join(CORPUS, nom)
            if not os.path.isfile(os.path.join(dossier, "cas.json")):
                continue
            def lire(f, dossier=dossier):
                with open(os.path.join(dossier, f), encoding="utf-8") as fl:
                    return json.load(fl)
            cas = lire("cas.json")
            fic = cas["fichiers"]
            attendu = lire(fic["attendu_strict"]).get("placement")
            if attendu is None:
                continue
            n += 1
            v = vp.verifier(lire(fic["placement"]), lire(fic["donnees"]), grille=cas.get("grille", True))
            for champ in ("valide", "violations_par_type", "infos"):
                if v[champ] != attendu[champ]:
                    ecarts.append("%s %s : %r au lieu de %r" % (nom, champ, v[champ], attendu[champ]))
        sys.stderr.write("\n  corpus placement : %d cas, %d écart\n" % (n, len(ecarts)))
        self.assertEqual(ecarts, [])


# ---------------------------------------------------------------------------------------------------------------------
# Couture conditionnelle (vias_couture/2, §4.6-6), étape C5
# ---------------------------------------------------------------------------------------------------------------------
CONTOUR = [[0, 0], [50, 0], [50, 40], [0, 40]]
REMPLI = [[5, 5], [25, 5], [25, 20], [5, 20]]


def carte_masse(plan=True, remplissage="Haut"):
    """Carte 3 couches (Haut, Plan, Bas), masse GND, plan GND sur la couche du milieu, remplissage GND sur la
    couche ``remplissage`` (rectangle REMPLI), deux pastilles A reliées par une piste en y = 10."""
    reseaux = [tm.reseau("A"), dict(tm.reseau("GND", "plan"), masse=True)]
    d = tm.donnees(couches=("Haut", "Plan", "Bas"), reseaux=reseaux, composants=deux_pastilles(),
                   zones={1: [{"reseau": "GND", "contour": CONTOUR, "trous": []}]} if plan else None)
    d["masses"] = ["GND"]
    if remplissage:
        d["carte"]["remplissages"] = [{"couche": remplissage, "reseau": "GND", "contour": REMPLI, "trous": []}]
    return d, tm.placement_v2(), tm.routage([tm.piste(10, 10, 20, 10, W, "Haut", "A")], (), ("Haut", "Plan", "Bas"))


class TestCouture(unittest.TestCase):
    def test_sans_plan_aucun_via(self):
        d, pl, rt = carte_masse(plan=False)
        c = vc.calculer(d, pl, rt)
        self.assertEqual((c["vias"], c["reseaux"], c["refus"]), ([], [], {}))
        self.assertEqual(formats.VALIDATEURS["vias_couture"](c), [])

    def test_sans_remplissage_exterieur_aucun_via(self):
        for rempl in (None, "Plan"):
            d, pl, rt = carte_masse(remplissage=rempl)
            self.assertEqual(vc.calculer(d, pl, rt)["vias"], [], rempl)

    def test_vias_dans_le_remplissage_et_drc_valide(self):
        d, pl, rt = carte_masse()
        c = vc.calculer(d, pl, rt)
        self.assertEqual(formats.VALIDATEURS["vias_couture"](c), [])
        self.assertEqual(c["reseaux"], ["GND"])
        self.assertGreater(len(c["vias"]), 10)
        for v in c["vias"]:
            x, y, r = v["x"] * tm.MIL, v["y"] * tm.MIL, v["size"] * tm.MIL / 2
            self.assertEqual((v["net"], round(v["size"] * tm.MIL, 3), round(v["hole_size"] * tm.MIL, 3)),
                             ("GND", 0.6, 0.3))
            self.assertTrue(5 + r <= x <= 25 - r and 5 + r <= y <= 20 - r, (x, y))
            self.assertGreaterEqual(gc.d_point_segment(x, y, 10, 10, 20, 10) - r - W / 2, 0.2)   # piste A
        self.assertGreater(c["refus"].get("isolement", 0) + c["refus"].get("pastille", 0), 0)
        for m in drc_maison.LES_MODES:
            v = drc_maison.verifier(d, pl, rt, m, c)
            self.assertTrue(v["valide"], (m, v["violations_par_type"], v["detail_liaisons_ouvertes"]))

    def test_dimensions_du_reseau(self):
        d, pl, rt = carte_masse()
        d["reseaux"][1]["via"] = dict(d["reseaux"][1]["via"], diametre=0.8, trou=0.4)
        c = vc.calculer(d, pl, rt)
        self.assertTrue(c["vias"])
        self.assertEqual({(round(v["size"] * tm.MIL, 3), round(v["hole_size"] * tm.MIL, 3)) for v in c["vias"]},
                         {(0.8, 0.4)})

    def test_retirer_fautifs(self):
        c = {"format": "vias_couture/2", "unite": "mil", "reseaux": ["GND"], "pas_grille_mm": 2.5, "refus": {},
             "vias": [{"x": 1, "y": 1, "size": 1, "hole_size": 0.5, "net": "GND"} for _ in range(3)]}
        v = {"detail_violations": [{"genre": "isolement", "objets": ["P0", "C1"]},
                                   {"genre": "trou_trou", "objets": ["C1", "C2"]}]}
        neuf, comptes = vc.retirer_fautifs(c, v)
        self.assertEqual((len(neuf["vias"]), len(c["vias"]), sum(comptes.values())), (1, 3, 2))

    def test_ligne_de_commande(self):
        d, pl, rt = carte_masse()
        d["source"]["construit_par"] = "construire_donnees 2.0.0"
        d["reseaux"][1].update(plan_couches=["Plan"], remplissages=["Haut"])     # copies dérivées (§2.5)
        with tempfile.TemporaryDirectory() as tmp:
            chemins = {}
            for nom, obj in (("d", d), ("p", pl), ("r", rt)):
                chemins[nom] = os.path.join(tmp, nom + ".json")
                with open(chemins[nom], "w", encoding="utf-8") as f:
                    json.dump(obj, f)
            s = os.path.join(tmp, "c.json")
            r = subprocess.run([sys.executable, "-I", os.path.join(ICI, "vias_couture.py"), "--donnees", chemins["d"],
                                "--placement", chemins["p"], "--routage", chemins["r"], "--sortie", s],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(s, encoding="utf-8") as f:
                self.assertTrue(json.load(f)["vias"])
            r = subprocess.run([sys.executable, "-I", os.path.join(ICI, "vias_couture.py")], capture_output=True,
                               text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 2)
            self.assertNotIn("Traceback", r.stderr + r.stdout)


class TestCarteSimpleTousControles(unittest.TestCase):
    """Critère 3 de §8.3 : carte 2 couches sans découpe, sans convertisseur, sans plan : tous les contrôles."""

    def test_tous_les_controles(self):
        d = carte(composants=deux_pastilles())
        pl = tm.placement_v2()
        rt = tm.routage([tm.piste(10, 10, 20, 10, W, "Haut", "A")])
        self.assertEqual(formats.VALIDATEURS["controle_placement"](vp.verifier(pl, d, grille=False)), [])
        for m in drc_maison.LES_MODES:
            self.assertTrue(drc_maison.verifier(d, pl, rt, m)["valide"], m)
        self.assertEqual(formats.VALIDATEURS["mesures"](mesures.mesurer(d, pl, rt)), [])
        self.assertEqual(formats.VALIDATEURS["proprete"](proprete.mesurer(d, pl, rt)), [])
        self.assertEqual(mconv.mesurer(d, pl, rt)["convertisseurs"], [])
        c = vc.calculer(d, pl, rt)
        self.assertEqual((c["vias"], c["reseaux"]), ([], []))


class TestVisualiser(unittest.TestCase):
    """visualiser.py : SVG lisible par xml.etree (vue d'ensemble, fenêtre, réseau, couture), code 2 sinon."""

    def test_svg(self):
        import tempfile
        import xml.etree.ElementTree as ET
        import visualiser
        d, pl, rt = carte_masse()
        d["source"]["construit_par"] = "construire_donnees 2.0.0"
        d["reseaux"][1]["plan_couches"] = ["Plan"]      # copies attendues par formats.charger
        d["reseaux"][1]["remplissages"] = ["Haut"]
        c = vc.calculer(d, pl, rt)
        self.assertTrue(c["vias"])
        tmp = tempfile.mkdtemp()
        try:
            ch = {}
            for nom, obj in (("d", d), ("p", pl), ("r", rt), ("c", c)):
                ch[nom] = os.path.join(tmp, nom + ".json")
                with open(ch[nom], "w", encoding="utf-8") as f:
                    json.dump(obj, f)
            base = ["--donnees", ch["d"], "--placement", ch["p"], "--routage", ch["r"]]
            for extra in ([], ["--couture", ch["c"], "--reseau", "A,GND"], ["--fenetre", "5", "5", "25", "15"]):
                s = os.path.join(tmp, "a.svg")
                self.assertEqual(visualiser.main(base + ["--sortie", s] + extra), 0, extra)
                racine = ET.parse(s).getroot()
                self.assertTrue(racine.tag.endswith("svg"))
                ids = {e.get("id") for e in racine.iter() if e.get("id")}
                self.assertTrue({"couche_0", "couche_1", "couche_2", "couture", "legende"} <= ids, ids)
            self.assertEqual(visualiser.main(base + ["--sortie", s, "--couture", ch["c"]]), 0)
            racine = ET.parse(s).getroot()
            g = [e for e in racine.iter() if e.get("id") == "couture"][0]
            self.assertEqual(len(list(g)), len(c["vias"]))
            self.assertEqual(visualiser.main(base[:2] + ["--sortie", s]), 2)
            self.assertEqual(visualiser.main(base + ["--sortie", s, "--fenetre", "5", "5", "1", "1"]), 2)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestMetroidCouture(unittest.TestCase):
    """ei_1038 (adaptateur, ``--references``) : couture calculée puis drc_maison strict avec la couture."""

    def test_ei_1038_couture_puis_drc_strict(self):
        if not REFERENCES:
            self.skipTest("--references absent : graine ei_1038 non disponible")
        if not tm.donnees_v1_presentes():
            self.skipTest("données v1 de 9b5b5a3 absentes")
        dossier = os.path.join(REFERENCES, "ei_1038")
        pl = tm._lire(os.path.join(dossier, "placements_routage.json"))
        rt = tm._lire(os.path.join(dossier, "routage_detaille.json"))
        d = tm.adapter_metroid_v1([rt], avec_cuivre_bloc=False)
        # adaptateur : remplissages GND sur les couches extérieures (polygones de la carte), vias d'un réseau dans
        # une pastille CMS du même réseau acceptés (comportement de 9b5b5a3 : 7 cas dans ei_1038)
        d["carte"]["remplissages"] = [{"couche": n, "reseau": "GND", "contour": d["carte"]["contour"], "trous": []}
                                      for n in ("TopLayer", "BottomLayer")]
        d["regles"]["via_dans_pastille_meme_reseau"] = True
        t0 = time.time()
        c = vc.calculer(d, pl, rt)
        duree = time.time() - t0
        v = drc_maison.verifier(d, pl, rt, "strict", c)
        sys.stderr.write("\n  ei_1038 couture : %d vias en %.1f s ; refus %s ; drc strict avec couture : %s, %s\n"
                         % (len(c["vias"]), duree, c["refus"], v["valide"],
                            {k: n for k, n in v["violations_par_type"].items() if n}))
        self.assertEqual(formats.VALIDATEURS["vias_couture"](c), [])
        self.assertTrue(v["valide"], (v["liaisons_ouvertes"], v["violations_par_type"]))


if __name__ == "__main__":
    REFERENCES = tm._extraire_options(sys.argv)
    gc.configurer_flux()
    unittest.main()
