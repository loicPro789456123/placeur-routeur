"""API de lecture de B1 (§8.2) : désignateurs, origine, composants, pastilles, empilage, régions de carte,
cartes imbriquées, règles, compléments (arcs, remplissages, régions, corps 3D, paires, couches mécaniques).

Publics : cartes synthétiques (fabrique.py). Locaux (--cartes-locales) : attendus codés de rev3, carte source,
WiFi, SL1, Bluetooth_Sentinel, WiFi_panel, et durées (rev4, SODIMM).
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
import fabrique  # noqa: E402
from altium_fichier import ErreurPcbDoc, PcbDoc  # noqa: E402
from altium_fichier.pcbdoc import effets_keepout  # noqa: E402

MM = 1 / 0.0254  # mil par mm


def _ouvrir_synthetique(**options):
    with commun.dossier_temporaire() as d:
        return PcbDoc.ouvrir(fabrique.fabriquer(d, **options))


class TestComposantsSynthetiques(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = _ouvrir_synthetique()
        cls.comps = cls.doc.composants()

    def test_designateurs(self):
        """Texte affiché, chaîne large, repli SOURCEDESIGNATOR, puis __piece_<rang>."""
        self.assertEqual(self.doc.designateurs(), {0: "U1", 1: "Ω1", 2: "C7", 3: "__piece_3"})
        self.assertEqual([c["designateur"] for c in self.comps], ["U1", "Ω1", "C7", "__piece_3"])
        self.assertEqual([c["designateur_source"] for c in self.comps], ["U1", "X9", "C7", ""])
        self.assertEqual(self.doc.index_composant("Ω1"), 1)

    def test_commentaires_et_origine(self):
        self.assertEqual(self.doc.commentaires(), {0: "Commentaire U1", 1: "", 2: "", 3: ""})
        self.assertEqual(self.comps[0]["commentaire"], "Commentaire U1")
        self.assertEqual(self.doc.origine(), (1000.0, 1000.0))

    def test_genre_verrouillage_face(self):
        self.assertEqual([c["genre_altium"] for c in self.comps], [0, 0, 4, 2])
        self.assertEqual([c["genre"] for c in self.comps], ["standard", "standard", "liaison_reseaux", "graphique"])
        self.assertEqual([c["verrouille"] for c in self.comps], [True, False, False, False])
        self.assertEqual([c["face"] for c in self.comps], ["TOP", "TOP", "BOTTOM", "TOP"])
        u1 = self.comps[0]
        self.assertEqual((u1["description"], u1["reference"], u1["empreinte"]), ("boîtier d'essai", "ESSAI", "ESSAI10"))
        self.assertAlmostEqual(u1["hauteur_mil"], 40.0)
        self.assertAlmostEqual(self.comps[1]["rotation"], 90.0)


class TestPastillesSynthetiques(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = _ouvrir_synthetique()
        cls.pads = cls.doc.pastilles()
        cls.u1 = [p for p in cls.pads if p["composant"] == 0]

    def test_rangs(self):
        self.assertEqual([p["rang"] for p in self.u1], list(range(10)))
        self.assertEqual([p["nom"] for p in self.u1], [str(k) for k in range(1, 11)])
        libre = self.pads[-1]
        self.assertEqual((libre["composant"], libre["rang"], libre["reseau"]), (0xFFFF, None, "GND"))
        c7 = [p for p in self.pads if p["composant"] == 2]
        self.assertEqual([p["rang"] for p in c7], [0])

    def test_chaque_forme(self):
        attendu = [("rect", (60, 40)), ("rond", (50, 50)), ("oblong", (40, 80)), ("rect_arrondi", (40, 60)),
                   ("octogone", (60, 60))]
        for p, (forme, taille) in zip(self.u1, attendu):
            f = p["formes"]["TOP"]
            self.assertEqual(f["forme"], forme, p["nom"])
            self.assertEqual(f["taille"], taille, p["nom"])
        ra = self.u1[3]["formes"]["TOP"]
        self.assertEqual(ra["pourcentage"], 25)
        self.assertAlmostEqual(ra["rayon_coin"], 0.25 * 40 / 2)
        self.assertEqual(self.u1[0]["formes"]["TOP"]["rayon_coin"], 0.0)

    def test_couches_et_traversantes(self):
        self.assertEqual(self.u1[0]["couches"], ["TOP"])
        self.assertFalse(self.u1[0]["traversante"])
        self.assertIsNone(self.u1[0]["percage"])
        # traversante : couches de signal de l'empilage (le plan négatif n'en fait pas partie), ordre physique
        self.assertEqual(self.u1[4]["couches"], ["TOP", "MID1", "BOTTOM"])
        self.assertTrue(self.u1[4]["traversante"])
        c7 = [p for p in self.pads if p["composant"] == 2][0]
        self.assertEqual(c7["couches"], ["BOTTOM"])
        self.assertEqual(c7["formes"]["BOTTOM"]["forme"], "rect")

    def test_percages(self):
        octo, oblong, decale, nm = self.u1[4], self.u1[5], self.u1[6], self.u1[8]
        self.assertEqual(octo["percage"], {"diametre": 30.0, "forme": "rond", "longueur": 30.0, "rotation": 0.0,
                                           "decalage": (0.0, 0.0), "metallise": True})
        p = oblong["percage"]
        self.assertEqual((p["forme"], p["diametre"], p["longueur"], p["rotation"]), ("oblong", 39.37, 78.74, 90.0))
        self.assertEqual(oblong["formes"]["TOP"]["forme"], "oblong")
        self.assertEqual(decale["percage"]["decalage"], (10.0, -5.0))
        self.assertEqual(decale["formes"]["BOTTOM"]["decalage"], (10.0, -5.0))
        self.assertFalse(nm["percage"]["metallise"])
        self.assertEqual(nm["percage"]["diametre"], 100.0)

    def test_pile_haut_milieu_bas(self):
        f = self.u1[7]["formes"]
        self.assertEqual(self.u1[7]["mode_pile"], 1)
        self.assertEqual((f["TOP"]["forme"], f["TOP"]["taille"]), ("rect", (60, 60)))
        self.assertEqual((f["MID1"]["forme"], f["MID1"]["taille"]), ("rond", (50, 50)))
        self.assertEqual((f["BOTTOM"]["forme"], f["BOTTOM"]["taille"]), ("octogone", (70, 70)))

    def test_pile_complete(self):
        """Mode 2 : MID1 = taille « milieu », MIDk (k >= 2) = tables de la pile ; empilage hors ordre."""
        doc = _ouvrir_synthetique(empilage="hors_ordre")
        p = [x for x in doc.pastilles() if x["composant"] == 0][9]
        self.assertEqual(p["couches"], ["TOP", "MID2", "MID1", "BOTTOM"])
        f = p["formes"]
        self.assertEqual((f["TOP"]["forme"], f["TOP"]["taille"]), ("rect", (60, 60)))
        self.assertEqual((f["MID1"]["forme"], f["MID1"]["taille"]), ("rond", (45, 45)))
        self.assertEqual((f["MID2"]["forme"], f["MID2"]["taille"]), ("octogone", (55, 55)))
        self.assertEqual((f["BOTTOM"]["forme"], f["BOTTOM"]["taille"]), ("rect", (65, 65)))

    def test_cles_historiques(self):
        p = self.u1[5]
        self.assertEqual(p["taille_haut"], (63.0, 110.0))
        self.assertEqual((p["forme_haut"], p["forme_trou"], p["trou"]), ("ronde", "oblong", 39.37))
        self.assertEqual((p["couche"], p["reseau"], p["x"], p["y"]), ("MULTILAYER", "VCC", 1650.0, 1500.0))
        self.assertEqual(p["masque"]["mode"], 1)


class TestEmpilageSynthetique(unittest.TestCase):
    def test_empilage_normal_et_plan_negatif(self):
        doc = _ouvrir_synthetique()
        cc = doc.couches_cuivre()
        self.assertEqual([(c["rang"], c["nom"], c["nom_altium"], c["type"]) for c in cc],
                         [(0, "TopLayer", "TOP", "signal"), (1, "InternalPlane1", "PLANE1", "plan_negatif"),
                          (2, "MidLayer1", "MID1", "signal"), (3, "BottomLayer", "BOTTOM", "signal")])
        plan = cc[1]
        self.assertEqual((plan["reseau"], plan["retrait_mil"], plan["nom_fichier"]), ("GND", 20.0, "Plan GND"))
        self.assertEqual(cc[0]["reseau"], "")
        self.assertAlmostEqual(cc[2]["epaisseur_cuivre_mil"], 0.7)
        pn = doc.plans_negatifs()
        self.assertEqual(len(pn), 1)
        self.assertEqual([(r["reseau"], r["reseau_du_plan"]) for r in pn[0]["regions"]], [("GND", True), ("VCC", False)])
        self.assertEqual(len(pn[0]["lignes_partage"]), 1)
        self.assertEqual(pn[0]["lignes_partage"][0]["largeur"], 20.0)
        self.assertEqual(pn[0]["traits_retrait"], 1)

    def test_numerotation_hors_ordre_physique(self):
        """TOP, MID2, MID1, BOTTOM : rangs 0..3 d'après l'ordre de l'empilage, noms d'après l'identifiant."""
        cc = _ouvrir_synthetique(empilage="hors_ordre").couches_cuivre()
        self.assertEqual([c["rang"] for c in cc], [0, 1, 2, 3])
        self.assertEqual([c["nom"] for c in cc], ["TopLayer", "MidLayer2", "MidLayer1", "BottomLayer"])
        self.assertEqual([c["nom_fichier"] for c in cc], ["Top Layer", "Interne haut", "Interne bas", "Bottom Layer"])
        self.assertTrue(all(c["type"] == "signal" for c in cc))

    def test_repli_anciens_empilages(self):
        doc = _ouvrir_synthetique(empilage="v8")
        cc = doc.couches_cuivre()
        self.assertEqual([(c["rang"], c["nom"], c["type"], c["reseau"], c["retrait_mil"], c["source"]) for c in cc],
                         [(0, "TopLayer", "signal", "", 0.0, "V8"), (1, "InternalPlane1", "plan_negatif", "GND", 20.0, "V8"),
                          (2, "MidLayer1", "signal", "", 0.0, "V8"), (3, "BottomLayer", "signal", "", 0.0, "V8")])
        self.assertEqual([s["nom"] for s in doc.sous_empilages()], ["Board Layer Stack"])
        cc = _ouvrir_synthetique(empilage="v6").couches_cuivre()
        self.assertEqual([(c["nom"], c["source"]) for c in cc],
                         [("TopLayer", "V6"), ("MidLayer1", "V6"), ("BottomLayer", "V6")])

    def test_sous_empilages_et_regions_de_carte(self):
        doc = _ouvrir_synthetique()
        se = doc.sous_empilages()
        self.assertEqual([(s["nom"], s["flex"], s["id"]) for s in se], [("Board Layer Stack", False, fabrique.ID_EMPILAGE)])
        self.assertFalse(doc.empilage_maitre()["flex"])
        rc = doc.regions_carte()
        self.assertEqual(len(rc), 1)
        self.assertEqual((rc[0]["sous_empilage"], rc[0]["lignes_pliage"]), ("Board Layer Stack", 0))
        self.assertAlmostEqual(doc.aire_region_carte(), 2000 * 1000 * 0.0254 ** 2, places=6)


class TestCarteSynthetiqueDivers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = _ouvrir_synthetique()

    def test_cartes_imbriquees(self):
        self.assertEqual(self.doc.cartes_imbriquees(), [])
        ci = _ouvrir_synthetique(panneau=True).cartes_imbriquees()
        self.assertEqual(len(ci), 1)
        self.assertEqual((ci[0]["document"], ci[0]["lignes"], ci[0]["colonnes"]), ("Carte.PcbDoc", 2, 3))
        self.assertEqual(ci[0]["proprietes"]["DOCUMENTPATH"], "Carte.PcbDoc")

    def test_regles_brutes(self):
        r = {x["nom"]: x for x in self.doc.regles()}
        c = r["Clearance"]
        self.assertEqual(c["portee1"], "Not (InComponent('U1') Or InComponent('C7'))")
        self.assertEqual((c["type"], c["code"], c["prefixe_binaire"], c["priorite"], c["active"]),
                         ("Clearance", 0, "0000", 2, True))
        self.assertEqual((c["portee_reseaux"], c["valeurs"]), ("DifferentNets", {"GENERICCLEARANCE": "8mil"}))
        w = r["Largeur_Alim"]
        self.assertEqual((w["code"], w["valeurs"]["PREFEREDWIDTH"]), (2, "20mil"))

    def test_primitives(self):
        arcs = self.doc.arcs()
        self.assertEqual((arcs[0]["couche"], arcs[0]["keepout"], arcs[0]["restrictions_keepout"]), ("KEEPOUT", True, 0x1F))
        self.assertEqual(effets_keepout(0x1F), ["vias", "pistes", "remplissages", "pastilles_cms", "pastilles_traversantes"])
        self.assertEqual((arcs[1]["reseau"], arcs[1]["rayon"], arcs[1]["angle_fin"]), ("SIG", 30.0, 90.0))
        rp = self.doc.remplissages()
        self.assertEqual((rp[0]["couche"], rp[0]["reseau"], rp[0]["x2"]), ("TOP", "VCC", 2200.0))
        reg = self.doc.regions()
        self.assertEqual((reg[0]["couche"], reg[0]["reseau"], len(reg[0]["contour"]), len(reg[0]["trous"])), ("TOP", "GND", 4, 1))
        self.assertEqual((reg[1]["keepout"], reg[1]["effets_keepout"]), (True, ["vias", "pistes"]))
        v = self.doc.vias()
        self.assertEqual((len(v), v[0]["reseau"], v[0]["diametre"], v[0]["trou"]), (2, "GND", 24.0, 12.0))
        p = self.doc.pistes()
        self.assertEqual((p[0]["couche"], p[0]["reseau"], p[0]["largeur"]), ("TOP", "SIG", 10.0))

    def test_complements(self):
        cb = self.doc.corps_3d()
        self.assertEqual((cb[0]["composant"], cb[0]["hauteur_totale_mil"], cb[0]["modele"]), (0, 50.0, "essai.step"))
        self.assertEqual(self.doc.paires_differentielles(), [{"nom": "D", "positif": "D_P", "negatif": "D_N"}])
        self.assertEqual(self.doc.types_couches_mecaniques(), {"MECHANICAL17": "AssemblyTop"})
        self.assertEqual([c["nom"] for c in self.doc.classes()], ["Alim"])
        self.assertEqual(self.doc.reseaux(), fabrique.RESEAUX)
        self.assertEqual(self.doc.index_reseau("VCC"), 1)
        self.assertEqual(self.doc.nom_reseau(4), "D_N")
        self.assertIsNone(self.doc.nom_reseau(0xFFFF))


class TestNomReseauMemorise(unittest.TestCase):
    def test_table_invalidee_par_marquer(self):
        doc = _ouvrir_synthetique()
        self.assertEqual(doc.nom_reseau(2), "SIG")
        doc.data("Nets6")[2]["NAME"] = "SIG2"
        doc.marquer("Nets6")
        self.assertEqual(doc.nom_reseau(2), "SIG2")
        self.assertEqual(doc.index_reseau("SIG2"), 2)

    def test_reseau_inconnu(self):
        doc = _ouvrir_synthetique()
        with self.assertRaises(ErreurPcbDoc) as cm:
            doc.index_reseau("INCONNU")
        self.assertIn("synthetique.PcbDoc", str(cm.exception))
        self.assertIn("INCONNU", str(cm.exception))
        self.assertEqual(doc.index_reseau(None), 0xFFFF)


# =============================================================================
# Cartes locales : attendus codés
# =============================================================================
class TestDesignateursLocaux(unittest.TestCase):
    def test_rev3_multicanal(self):
        doc = commun.doc_partage(commun.REV3)
        c = {x["designateur"]: x for x in doc.composants()}
        self.assertEqual(len(c), len(doc.composants()))
        self.assertEqual((c["M4_ALIM1"]["designateur_source"], c["M4_ALIM2"]["designateur_source"]), ("M4", "M4"))
        self.assertNotEqual(c["M4_ALIM1"]["index"], c["M4_ALIM2"]["index"])

    def test_carte_source(self):
        doc = commun.doc_partage(commun.SOURCE)
        cs = doc.composants()
        self.assertEqual(len(cs), 17)
        self.assertEqual(len(set(doc.designateurs().values())), 17)
        self.assertTrue(all(c["designateur_source"] == "" for c in cs))
        self.assertFalse(any(d.startswith("__piece_") for d in doc.designateurs().values()))
        self.assertEqual([c["designateur"] for c in cs if c["verrouille"]], ["J1"])

    def test_origine_wifi(self):
        x, y = commun.doc_partage(commun.WIFI).origine()
        self.assertAlmostEqual(x, 3937.0079, places=4)
        self.assertAlmostEqual(y, 3937.0079, places=4)

    def test_componentkind_sl1(self):
        c = {x["designateur"]: x for x in commun.doc_partage(commun.SL1).composants()}
        self.assertEqual((c["NT1"]["genre_altium"], c["NT1"]["genre"]), (4, "liaison_reseaux"))
        self.assertEqual((c["Logo5"]["genre_altium"], c["Logo5"]["genre"]), (2, "graphique"))
        self.assertEqual(c["U1"]["genre_altium"], 0)


class TestPastillesCarteSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        doc = commun.doc_partage(commun.SOURCE)
        D = doc.designateurs()
        cls.pads = {}
        for p in doc.pastilles():
            cls.pads.setdefault(D.get(p["composant"], "libre"), []).append(p)

    def test_octogones(self):
        j1 = sorted(self.pads["J1"], key=lambda p: p["nom"])
        self.assertEqual([p["formes"]["TOP"]["forme"] for p in j1], ["rect", "octogone", "octogone", "octogone"])
        self.assertTrue(all(p["traversante"] and p["percage"]["metallise"] for p in j1))

    def test_oblongues_a_trous_oblongs(self):
        """J2 : cuivre oblong de 63 x 110 mil (1,600 x 2,794 mm, « 1,6 x 2,8 mm » dans la description de la
        carte), trous oblongs de 1,0 x 2,0 mm tournés de 90°."""
        j2 = self.pads["J2"]
        self.assertEqual(len(j2), 2)
        for p in j2:
            f = p["formes"]["TOP"]
            self.assertEqual(f["forme"], "oblong")
            self.assertAlmostEqual(f["taille"][0] / MM, 1.6, delta=0.01)
            self.assertAlmostEqual(f["taille"][1] / MM, 2.8, delta=0.01)
            t = p["percage"]
            self.assertEqual(t["forme"], "oblong")
            self.assertAlmostEqual(t["diametre"] / MM, 1.0, places=3)
            self.assertAlmostEqual(t["longueur"] / MM, 2.0, places=3)
            self.assertAlmostEqual(t["rotation"], 90.0)

    def test_rectangles_arrondis(self):
        for des in ("R1", "R2", "R3", "R4", "C1", "C2", "C3", "C4", "D1"):
            for p in self.pads[des]:
                f = p["formes"]["BOTTOM" if des == "C3" else "TOP"]
                self.assertEqual((f["forme"], f["pourcentage"]), ("rect_arrondi", 25), des)
                self.assertAlmostEqual(f["rayon_coin"], 0.25 * min(f["taille"]) / 2)
        u2 = {p["nom"]: p["formes"]["TOP"]["forme"] for p in self.pads["U2"]}
        self.assertEqual(u2.pop("17"), "rect")
        self.assertEqual(set(u2.values()), {"rect_arrondi"})

    def test_trous_non_metallises(self):
        for des in ("H1", "H2"):
            (p,) = self.pads[des]
            self.assertFalse(p["percage"]["metallise"])
            self.assertAlmostEqual(p["percage"]["diametre"] / MM, 2.7, places=3)
        self.assertEqual(self.pads["C3"][0]["couches"], ["BOTTOM"])


class TestEmpilagesLocaux(unittest.TestCase):
    def test_plans_negatifs_carte_source(self):
        doc = commun.doc_partage(commun.SOURCE)
        cc = doc.couches_cuivre()
        self.assertEqual([(c["nom"], c["type"], c["reseau"]) for c in cc],
                         [("TopLayer", "signal", ""), ("InternalPlane1", "plan_negatif", "GND"),
                          ("InternalPlane2", "plan_negatif", "3V3"), ("BottomLayer", "signal", "")])
        self.assertEqual([c["retrait_mil"] for c in cc if c["type"] == "plan_negatif"], [20.0, 20.0])
        pn = {p["couche"]: p for p in doc.plans_negatifs()}
        partage = [r for r in pn["InternalPlane2"]["regions"] if not r["reseau_du_plan"]]
        self.assertEqual([r["reseau"] for r in partage], ["VIN"])
        self.assertEqual(len(pn["InternalPlane2"]["lignes_partage"]), 4)
        self.assertEqual(pn["InternalPlane1"]["lignes_partage"], [])

    def test_wifi_quatre_couches_signal(self):
        cc = commun.doc_partage(commun.WIFI).couches_cuivre()
        self.assertEqual([(c["nom"], c["type"]) for c in cc],
                         [("TopLayer", "signal"), ("MidLayer1", "signal"), ("MidLayer2", "signal"), ("BottomLayer", "signal")])

    def test_aire_region_carte_wifi(self):
        a = commun.doc_partage(commun.WIFI).aire_region_carte()
        self.assertLess(abs(a - 784.773) / 784.773, 0.001)

    def test_bluetooth_rigide_flexible(self):
        doc = commun.doc_partage(commun.BLUETOOTH)
        se = doc.sous_empilages()
        self.assertEqual(len(se), 2)
        self.assertEqual(sum(s["flex"] for s in se), 1)
        rc = doc.regions_carte()
        self.assertEqual(len(rc), 3)
        self.assertEqual(len({r["id_empilage"] for r in rc}), 2)
        self.assertEqual([r["lignes_pliage"] for r in rc if r["lignes_pliage"]], [6])

    def test_panneau(self):
        ci = commun.doc_partage(commun.WIFI_PANNEAU).cartes_imbriquees()
        self.assertEqual(len(ci), 1)
        self.assertEqual(ci[0]["document"], "WiFi.PcbDoc")

    def test_regles_rev3(self):
        r = [x for x in commun.doc_partage(commun.REV3).regles() if x["nom"] == "Clearance"]
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["portee1"], "Not (InComponent('M1') Or InComponent('M2') Or InComponent('U10'))")
        self.assertEqual((r[0]["code"], r[0]["prefixe_binaire"]), (0, "0000"))
        self.assertTrue(all(x["code"] is not None for x in commun.doc_partage(commun.REV3).regles()))


class TestDurees(unittest.TestCase):
    """Durées (critère de BF1) : pistes()+vias()+pastilles() sur rev4 < 3 s ; tous les accesseurs sur SODIMM < 5 s
    (18,6 s et 37,1 s avant la mémorisation de nom_reseau)."""

    def test_rev4(self):
        doc = PcbDoc.ouvrir(commun.carte(commun.REV4))
        t = time.perf_counter()
        n = len(doc.pistes()) + len(doc.vias()) + len(doc.pastilles())
        duree = time.perf_counter() - t
        self.assertGreater(n, 9000)
        self.assertLess(duree, 3.0)

    def test_sodimm(self):
        doc = PcbDoc.ouvrir(commun.carte(commun.SODIMM))
        t = time.perf_counter()
        commun.appeler_accesseurs(doc)
        self.assertLess(time.perf_counter() - t, 5.0)


if __name__ == "__main__":
    unittest.main(argv=commun.extraire_options(sys.argv))
