# -*- coding: utf-8 -*-
"""Tests des outils Python de la sérigraphie et de la ligne de commande du moteur.

  python -m unittest test_serigraphie -v      (depuis PCB_V2/outils/serigraphie)
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import drc_serigraphie as drc  # noqa: E402
import pcbdoc_serigraphie as ps  # noqa: E402
import rendu_serigraphie as rendu  # noqa: E402

REV4 = os.path.join(ICI, "altium_rev4")
GABARIT_REV4 = os.path.join(ICI, "..", "..", "..", "PCB_METROID_V2_rev4.PcbDoc")
MOTEUR = os.path.join(ICI, "target", "release", "serigraphie" + (".exe" if os.name == "nt" else ""))


def muet(f, *a):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as e:
        code = f(*a)
    return code, e.getvalue()


def carte_simple(dossier, contour=None, decoupes=()):
    """Entrée minimale : deux composants, une pastille chacun, un trait ; contour en mil."""
    os.makedirs(dossier, exist_ok=True)
    pad = lambda c, x: {"Component": c, "Name": "1", "X": str(x), "Y": "500", "Layer": "dessus", "TopXSize": "40",
                        "TopYSize": "40", "Rotation": "0", "TopShape": "2", "SolderMaskExpansion": "4"}
    f = {"pastilles": [pad("R1", 300), pad("R2", 700)],
         "composants": [{"Designator": c, "X": str(x), "Y": "500", "Layer": "dessus", "Rotation": "0",
                         "NameAutoPosition": "0"} for c, x in (("R1", 300), ("R2", 700))],
         "vias": [], "textes": [], "pistes_serigraphie_top": [
             {"X1": "260", "Y1": "528", "X2": "340", "Y2": "528", "Width": "5", "Layer": "seri_dessus", "Component": "R1"}],
         "pistes_serigraphie_bottom": [], "arcs_serigraphie_top": [], "arcs_serigraphie_bottom": []}
    for n, v in f.items():
        with open(os.path.join(dossier, n + ".json"), "w", encoding="utf-8") as h:
            json.dump(v, h)
    carte = {"format": "carte_serigraphie/1", "unites": "mil",
             "contour": contour or [[0, 0], [1000, 0], [1000, 1000], [0, 1000]], "decoupes": list(decoupes),
             "trous": [[900, 900, 30]], "regles": {"garde_masque_mm": 0.254, "garde_bord_mm": 0.5,
                                                    "tailles_mm": [[1.0, 0.15], [0.8, 0.12]]}}
    with open(os.path.join(dossier, "carte.json"), "w", encoding="utf-8") as h:
        json.dump(carte, h)
    return carte


class TestDrcSerigraphie(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sans_carte_json_code_2(self):
        code, err = muet(drc.main, ["--entree", self.tmp, "--json", os.path.join(self.tmp, "x.json")])
        self.assertEqual(code, 2)
        self.assertIn("carte.json", err)
        self.assertNotIn(self.tmp, err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x.json")))

    def test_sans_entree_code_2(self):
        self.assertEqual(muet(drc.main, [])[0], 2)
        self.assertEqual(muet(drc.main, ["--entree", os.path.join(self.tmp, "absent")])[0], 2)

    def test_json_tronque_code_2(self):
        carte_simple(self.tmp)
        with open(os.path.join(self.tmp, "pastilles.json"), "w", encoding="utf-8") as h:
            h.write('[{"X": ')
        self.assertEqual(muet(drc.main, ["--entree", self.tmp])[0], 2)

    def test_carte_simple(self):
        carte_simple(self.tmp)
        js = os.path.join(self.tmp, "r.json")
        self.assertEqual(muet(drc.main, ["--entree", self.tmp, "--json", js])[0], 0)
        with open(js, encoding="utf-8") as h:
            r = json.load(h)
        self.assertEqual(r["n"], 1)            # trait à 5,5 mil du cuivre de R1 (< 10 mil)
        self.assertEqual(r["hors_carte"], 0)

    def test_contour_polygonal_et_decoupes(self):
        L = [[0, 0], [1000, 0], [1000, 500], [500, 500], [500, 1000], [0, 1000]]
        carte_simple(self.tmp, contour=L, decoupes=[[[100, 100], [200, 100], [150, 200]]])
        carte = drc.charger_carte(self.tmp)
        self.assertTrue(drc.boite_dans_carte((300, 600, 400, 650), carte))
        self.assertFalse(drc.boite_dans_carte((600, 600, 700, 650), carte))   # angle retiré du L
        self.assertFalse(drc.boite_dans_carte((120, 120, 160, 150), carte))   # dans la découpe
        self.assertFalse(drc.boite_dans_carte((5, 300, 50, 320), carte))      # garde au bord

    def test_rev4_avant(self):
        """Jeu d'essai : 483 violations sérigraphie / pastille à l'état d'entrée (référence du LISEZMOI)."""
        js = os.path.join(self.tmp, "r.json")
        self.assertEqual(muet(drc.main, ["--entree", REV4, "--json", js])[0], 0)
        with open(js, encoding="utf-8") as h:
            self.assertEqual(json.load(h)["n"], 483)


class TestRendu(unittest.TestCase):
    def test_svg_lisible(self):
        tmp = tempfile.mkdtemp()
        try:
            carte_simple(os.path.join(tmp, "e"))
            apres = {"textes": [{"component": "R1", "text": "R1", "x": 280.0, "y": 600.0, "height": 39.37,
                                 "stroke_width": 5.9, "rotation": 0, "layer": "seri_dessus", "mirror": False,
                                 "hidden": False},
                                {"component": "R2", "text": "R2", "x": 0, "y": 0, "height": 39.37, "stroke_width": 5.9,
                                 "rotation": 0, "layer": "seri_dessus", "mirror": False, "hidden": True}],
                     "pistes_supprimer": [], "arcs_supprimer": [], "pistes_ajouter": []}
            with open(os.path.join(tmp, "a.json"), "w", encoding="utf-8") as h:
                json.dump(apres, h)
            code, _ = muet(rendu.main, ["--entree", os.path.join(tmp, "e"), "--apres", os.path.join(tmp, "a.json"),
                                        "--sortie", os.path.join(tmp, "s")])
            self.assertEqual(code, 0)
            for nom in ("serigraphie_dessus.svg", "serigraphie_dessous.svg"):
                racine = ET.parse(os.path.join(tmp, "s", nom)).getroot()
                self.assertTrue(racine.tag.endswith("svg"))
            self.assertEqual(muet(rendu.main, ["--entree", os.path.join(tmp, "absent"), "--sortie", tmp])[0], 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DocFactice:
    """Document minimal pour les règles de carte.json."""

    class _T:
        def __init__(self, h, w):
            self.bloc = {"est_designateur": True, "composant": 0, "hauteur": h, "epaisseur_trait": w}

    def __init__(self, textes=(), regles=()):
        self._t = [self._T(h, w) for h, w in textes]
        self._r = list(regles)

    def data(self, nom):
        return self._t if nom == "Texts6" else []

    def regles(self):
        return self._r


class TestCarteJson(unittest.TestCase):
    def test_tailles_auto(self):
        # hauteurs 40, 50, 60 mil (médiane 50 mil = 1,27 mm) ; épaisseurs 5, 6, 7 mil
        doc = DocFactice([(400000, 50000), (500000, 60000), (600000, 70000)])
        t = ps.tailles_mm(doc, {"tailles_mm": "auto"})
        self.assertAlmostEqual(t[0][0], 1.27, 4)
        self.assertAlmostEqual(t[0][1], 0.1524, 4)
        self.assertAlmostEqual(t[1][0], 1.143, 4)
        self.assertAlmostEqual(t[2][0], 1.016, 4)
        self.assertEqual(ps.tailles_mm(DocFactice(), None), ps.TAILLES_DEFAUT_MM)
        self.assertEqual(ps.tailles_mm(doc, {"tailles_mm": [[2, 0.2]]}), [[2.0, 0.2]])

    def test_garde_masque(self):
        r = {"type": "SilkToSolderMaskClearance", "active": True, "priorite": 1,
             "valeurs": {"MINSILKSCREENTOMASKGAP": "8mil"}}
        self.assertAlmostEqual(ps.garde_masque_mm(DocFactice(regles=[r]), {"garde_masque_mm": None}), 0.2032, 6)
        self.assertEqual(ps.garde_masque_mm(DocFactice(), {}), 0.254)
        self.assertEqual(ps.garde_masque_mm(DocFactice(regles=[r]), {"garde_masque_mm": 0.1}), 0.1)

    def test_contour_avec_arc(self):
        # carré de 100 mil dont le coin haut-droit est un quart de cercle de rayon 20
        s = [{"type": 0, "x": 0, "y": 0, "cx": 0, "cy": 0, "r": 0, "sa": 0, "ea": 0},
             {"type": 0, "x": 100, "y": 0, "cx": 0, "cy": 0, "r": 0, "sa": 0, "ea": 0},
             {"type": 1, "x": 100, "y": 80, "cx": 80, "cy": 80, "r": 20, "sa": 0, "ea": 90},
             {"type": 0, "x": 80, "y": 100, "cx": 0, "cy": 0, "r": 0, "sa": 0, "ea": 0},
             {"type": 0, "x": 0, "y": 100, "cx": 0, "cy": 0, "r": 0, "sa": 0, "ea": 0}]
        c = ps.contour_polygonal(s, 0, 0)
        self.assertGreater(len(c), 6)
        for x, y in c:
            if x > 80 and y > 80:
                self.assertAlmostEqual(((x - 80) ** 2 + (y - 80) ** 2) ** 0.5, 20, 3)
        self.assertEqual(ps.trous_config_mil({"trous_supplementaires_mm": [[2.54, 0, 0.254]]}), [[100.0, 0.0, 10.0]])

    def test_exporter_rev4(self):
        """Le .PcbDoc rev4 (lecture seule) exporté : mêmes pastilles, composants et vias que le jeu d'essai, jetons neutres, carte.json complet."""
        if not os.path.isfile(GABARIT_REV4):
            self.skipTest("PCB_METROID_V2_rev4.PcbDoc absent de la copie de travail")
        sys.path.insert(0, os.path.join(ICI, "..", "format_altium"))
        from altium_fichier.pcbdoc import PcbDoc
        tmp = tempfile.mkdtemp()
        try:
            _, carte = ps.exporter(PcbDoc.ouvrir(GABARIT_REV4), tmp, config={"tailles_mm": "auto", "garde_masque_mm": None})
            for n in drc.FICHIERS:
                with open(os.path.join(tmp, n + ".json"), encoding="utf-8") as h, \
                        open(os.path.join(REV4, n + ".json"), encoding="utf-8") as g:
                    a, b = json.load(h), json.load(g)
                if n in ("pastilles", "composants", "vias"):
                    self.assertEqual(len(a), len(b), n)
                self.assertEqual(sorted(set(e.get("Layer", "") for e in a)), sorted(set(e.get("Layer", "") for e in b)), n)
            self.assertEqual(carte["regles"]["garde_masque_mm"], 0.254)
            self.assertEqual(len(carte["decoupes"]), 4)
            self.assertGreaterEqual(len(carte["contour"]), 4)
            self.assertEqual(muet(drc.main, ["--entree", tmp])[0], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(os.path.isfile(MOTEUR), "moteur non compilé (cargo build --release --offline)")
class TestMoteur(unittest.TestCase):
    def lancer(self, *args):
        r = subprocess.run([MOTEUR] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout + r.stderr

    def test_sans_option(self):
        code, sortie = self.lancer()
        self.assertEqual(code, 2)
        self.assertIn("obligatoire", sortie)
        self.assertNotIn("panicked", sortie)

    def test_carte_absente_et_tronquee(self):
        tmp = tempfile.mkdtemp()
        try:
            code, sortie = self.lancer("--entree", REV4, "--sortie", os.path.join(tmp, "s.json"),
                                       "--carte", os.path.join(tmp, "absent.json"))
            self.assertEqual(code, 2)
            self.assertNotIn(tmp, sortie)
            with open(os.path.join(REV4, "carte.json"), encoding="utf-8") as h:
                texte = h.read()
            with open(os.path.join(tmp, "t.json"), "w", encoding="utf-8") as h:
                h.write(texte[:len(texte) // 2])
            code, sortie = self.lancer("--entree", REV4, "--sortie", os.path.join(tmp, "s.json"),
                                       "--carte", os.path.join(tmp, "t.json"))
            self.assertEqual(code, 2)
            self.assertNotIn("panicked", sortie)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_carte_simple(self):
        tmp = tempfile.mkdtemp()
        try:
            carte_simple(os.path.join(tmp, "e"))
            s = os.path.join(tmp, "s.json")
            code, sortie = self.lancer("--entree", os.path.join(tmp, "e"), "--sortie", s, "--carte",
                                       os.path.join(tmp, "e", "carte.json"), "--chaines", "2", "--mouvements", "2000",
                                       "--threads", "2")
            self.assertEqual(code, 0, sortie)
            with open(s, encoding="utf-8") as h:
                r = json.load(h)
            self.assertEqual(len(r["textes"]), 2)
            self.assertTrue(all(t["layer"] == "seri_dessus" for t in r["textes"]))
            self.assertEqual(muet(drc.main, ["--entree", os.path.join(tmp, "e"), "--apres", s])[0], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
