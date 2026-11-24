# -*- coding: utf-8 -*-
"""Modes de calcul (24/11/2026) : conjoint, placement seul, sérigraphie seule."""
import json
import os
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

import formats  # noqa: E402
import parametres  # noqa: E402
import placeur  # noqa: E402


class TestModesCalcul(unittest.TestCase):
    def test_cles(self):
        self.assertEqual(parametres.MODES_CALCUL, ("conjoint", "placement", "serigraphie"))
        self.assertEqual(placeur.OPTIONS_VALEUR["--mode-calcul"], "calcul.mode_calcul")
        self.assertIn("mode_calcul", formats.CLES_CONTRAINTES)
        self.assertEqual(formats.valider_contraintes({"format": "contraintes/2", "mode_calcul": "placement"}), [])
        self.assertTrue(formats.valider_contraintes({"format": "contraintes/2", "mode_calcul": "routage"}))

    def test_controles_sans_routage(self):
        """Graine du mode placement (pas de routage_detaille.json) : placement contrôlé, DRC sans objet."""
        class C(placeur.Controles):
            def verifier_placement(self, placements, donnees):
                return {"format": "controle_placement/1", "valide": True, "violations": [], "eloignements": []}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "placements_routage.json"), "w", encoding="utf-8") as f:
                json.dump({"format": "placements/2", "origine": "x", "unite": "mil", "pieces": []}, f)
            res = C().executer(d, os.path.join(d, "donnees.json"), {})
        self.assertTrue(res["drc_strict"]["valide"] and res["drc_strict"]["sans_routage"])
        self.assertIsNone(res["mesures"])

    def test_stats_placement_seul(self):
        """stats.validite null admis pour le placement seul (fin arret_apres)."""
        d = json.load(open(os.path.join(ICI, "corpus", "formats", "exemples", "stats.json"), encoding="utf-8"))
        d.update(fin="arret_apres", valide=True, cause="", validite=None)
        self.assertFalse([e for e in formats.valider_stats(d) if "validite" in e])
        d.update(fin="normale")
        self.assertTrue([e for e in formats.valider_stats(d) if "validite" in e])


if __name__ == "__main__":
    unittest.main()
