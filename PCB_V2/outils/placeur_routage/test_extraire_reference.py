"""Tests de extraire_reference.py (§6.1, critère B8 de §8.2).

    python -B test_extraire_reference.py [options de unittest]

Les tests sur METROID rev4 lisent PCB_METROID_V2_rev4.PcbDoc à la racine du dépôt (lecture seule).
"""
import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import extraire_reference as ER  # noqa: E402
import formats  # noqa: E402
import importer_carte as IC  # noqa: E402

DEPOT = os.path.dirname(os.path.dirname(os.path.dirname(ICI)))
REV4 = os.path.join(DEPOT, "PCB_METROID_V2_rev4.PcbDoc")
REV3 = os.path.join(DEPOT, "PCB_METROID_V2_rev3.PcbDoc")


def lancer(mod, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = mod.main(list(args))
    return rc, out.getvalue(), err.getvalue()


class TestGeometrie(unittest.TestCase):
    def test_arc_fleche(self):
        a = {"cx": 0.0, "cy": 0.0, "rayon": 100.0, "angle_debut": 0.0, "angle_fin": 90.0}
        pts = ER._segments_arc(a)
        self.assertEqual((round(pts[0][0], 9), round(pts[0][1], 9)), (100.0, 0.0))
        self.assertAlmostEqual(pts[-1][0], 0.0, places=9)
        self.assertAlmostEqual(pts[-1][1], 100.0, places=9)
        for p, q in zip(pts, pts[1:]):
            m = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
            self.assertLessEqual((100.0 - math.hypot(*m)) * ER.MM, ER.FLECHE_MM + 1e-12)
        self.assertGreater(len(pts), 3)

    def test_arc_complet_et_passage_par_zero(self):
        self.assertAlmostEqual(ER._segments_arc({"cx": 0, "cy": 0, "rayon": 10.0, "angle_debut": 0.0,
                                                 "angle_fin": 0.0})[-1][0], 10.0)
        pts = ER._segments_arc({"cx": 0, "cy": 0, "rayon": 10.0, "angle_debut": 270.0, "angle_fin": 90.0})
        self.assertAlmostEqual(pts[0][1], -10.0)
        self.assertAlmostEqual(pts[-1][1], 10.0)
        self.assertTrue(all(p[0] >= -1e-9 for p in pts))

    def test_distance_rect(self):
        self.assertEqual(ER._distance_rect(0, 0, 0, 0, 1, 2, 0), 0.0)
        self.assertAlmostEqual(ER._distance_rect(3, 0, 0, 0, 1, 2, 0), 2.0)
        self.assertAlmostEqual(ER._distance_rect(0, 1.5, 0, 0, 1, 2, 90), 0.5)


@unittest.skipUnless(os.path.exists(REV4), "PCB_METROID_V2_rev4.PcbDoc absent de la racine du dépôt")
class TestRev4(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.entree = os.path.join(cls.tmp.name, "e4.json")
        cls.sortie = os.path.join(cls.tmp.name, "ref4")
        rc, _, err = lancer(IC, REV4, "--entree", cls.entree)
        assert rc == 0, err
        cls.rc, cls.out, cls.err = lancer(ER, REV4, "--entree", cls.entree, "--sortie", cls.sortie)
        cls.d = {}
        for n in ER.FICHIERS:
            with open(os.path.join(cls.sortie, n), encoding="utf-8") as f:
                cls.d[n] = json.load(f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_code_et_formats(self):
        self.assertEqual(self.rc, 0, self.err)
        for n in ER.FICHIERS:
            rc, out, err = lancer(formats, "valider", os.path.join(self.sortie, n))
            self.assertEqual(rc, 0, out + err)
        self.assertNotIn(self.tmp.name, self.out + self.err)
        self.assertNotIn(self.tmp.name, json.dumps(self.d))

    def test_nombres_par_categorie(self):
        p, r, c = (self.d[n] for n in ER.FICHIERS)
        self.assertEqual(len(p["pieces"]), 359)
        self.assertEqual(len(r["pistes"]), 2452)          # rev4 : 2452 pistes libres, aucun arc libre de cuivre
        self.assertEqual(len(r["vias"]) + len(c["vias"]), 1008)
        self.assertEqual(len(c["vias"]), 512)              # publié au journal (environ 516 annoncés, revue I2)
        self.assertEqual(len(r["surfaces"]), 6)
        self.assertEqual({s["type"] for s in r["surfaces"]}, {"remplissage"})
        self.assertEqual({s["origine"] for s in r["surfaces"]}, {"reference"})
        self.assertEqual(c["reseaux"], ["GND"])
        self.assertEqual({v["net"] for v in c["vias"]}, {"GND"})
        self.assertEqual(r["couches_routage"], ["TopLayer", "MidLayer1", "MidLayer2", "BottomLayer"])
        self.assertTrue(all(t["layer"] in r["couches_routage"] for t in r["pistes"]))

    def test_couture_sans_piste_ni_cms(self):
        _, r, c = (self.d[n] for n in ER.FICHIERS)
        bouts = {(t["x1"], t["y1"]) for t in r["pistes"] if t["net_name"] == "GND"} | \
                {(t["x2"], t["y2"]) for t in r["pistes"] if t["net_name"] == "GND"}
        for v in c["vias"]:
            self.assertFalse(any(math.hypot(x - v["x"], y - v["y"]) < v["size"] / 2 - 0.01 for x, y in bouts))

    def test_placements_repere_commun(self):
        p = self.d["placements_routage.json"]
        with open(self.entree, encoding="utf-8") as f:
            e = json.load(f)
        u = next(c for c in e["composants"] if c["designateur"] == "U10")
        q = next(c for c in p["pieces"] if c["designator"] == "U10")
        self.assertAlmostEqual(q["x"] * ER.MM, u["x"], places=3)
        self.assertAlmostEqual(q["y"] * ER.MM, u["y"], places=3)
        self.assertEqual(q["side"], "top" if u["face"] == "dessus" else "bottom")


class TestLigneDeCommande(unittest.TestCase):
    def test_usage_et_erreurs(self):
        self.assertEqual(lancer(ER, "--aide")[0], 0)
        rc, _, err = lancer(ER, "carte.PcbDoc")
        self.assertEqual(rc, 2)
        self.assertIn("--entree manquante", err)
        rc, _, err = lancer(ER, "a.PcbDoc", "--entree", "e.json", "--sortie", "s", "--x")
        self.assertEqual((rc, "option inconnue : --x" in err), (2, True))
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = lancer(ER, os.path.join(tmp, "absente.PcbDoc"), "--entree", os.path.join(tmp, "e.json"),
                                "--sortie", os.path.join(tmp, "s"))
            self.assertEqual(rc, 2)
            self.assertNotIn(tmp, err)

    @unittest.skipUnless(os.path.exists(REV3) and os.path.exists(REV4), "cartes rev3 et rev4 absentes")
    def test_entree_d_une_autre_carte(self):
        with tempfile.TemporaryDirectory() as tmp:
            e3 = os.path.join(tmp, "e3.json")
            self.assertEqual(lancer(IC, REV3, "--entree", e3)[0], 0)
            rc, _, err = lancer(ER, REV4, "--entree", e3, "--sortie", os.path.join(tmp, "s"))
            self.assertEqual(rc, 2)
            self.assertIn("pas de PCB_METROID_V2_rev4.PcbDoc", err)
            self.assertNotIn(tmp, err)


if __name__ == "__main__":
    unittest.main()
