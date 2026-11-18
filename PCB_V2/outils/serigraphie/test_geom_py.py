# -*- coding: utf-8 -*-
"""Tests de la geometrie du controleur Python :  python -m unittest test_geom_py"""
import json, os, unittest
from geom_py import (dist, forme_pastille, forme_texte_trait, boite, largeur_encre, arc_en_segments, rect_tourne)

ICI = os.path.dirname(os.path.abspath(__file__))


def pad(x, y, sx, sy, r, forme, e=0):
    return dict(X=str(x), Y=str(y), TopXSize=str(sx), TopYSize=str(sy), Rotation=str(r), TopShape=forme,
                SolderMaskExpansion=str(e))


class T(unittest.TestCase):
    def test_capsule_rect(self):
        t = ('seg', (-50, 20), (50, 20), 3)
        self.assertAlmostEqual(dist(t, forme_pastille(pad(0, 0, 20, 10, 0, '2'))), 12)
        self.assertAlmostEqual(dist(t, forme_pastille(pad(0, 0, 20, 10, 90, '2'))), 7)
        self.assertAlmostEqual(dist(t, forme_pastille(pad(0, 0, 20, 10, 90, '2', 4))), 3)

    def test_oblong(self):
        P = forme_pastille(pad(0, 0, 30, 10, 0, '1'))
        self.assertAlmostEqual(dist(P, ('seg', (20, 0), (20, 0), 0)), 5)
        self.assertAlmostEqual(dist(P, ('seg', (10, 8), (10, 8), 0)), 3)

    def test_recouvrement(self):
        self.assertEqual(dist(rect_tourne(0, 0, 10, 10, 0), rect_tourne(1, 1, 2, 2, 0)), 0)

    def test_arc_englobant(self):
        pts = arc_en_segments(0, 0, 10, 0, 90)
        self.assertAlmostEqual(pts[0][0], 10)
        self.assertAlmostEqual(pts[-1][1], 10)

    def test_police_contre_bounds_altium(self):
        """modele trait gonfle de w = Bounds exportes (0,3 mil pres, modele toujours englobant) pour tous les textes trait de l'export."""
        with open(os.path.join(ICI, 'altium_rev4', 'textes.json'), encoding='utf-8') as f:
            T = json.load(f)
        n = 0
        for t in T:
            if t['UseTTFonts'] != 'false':
                continue
            w = float(t['StrokeWidth'])
            F = forme_texte_trait(t['Text'], float(t['X']), float(t['Y']), float(t['Height']), w, float(t['Rotation']),
                                  t['MirrorFlag'] == 'true', gonfle=w)
            b = boite(F)
            B = [float(t[k]) for k in ('BoundsLeft', 'BoundsBottom', 'BoundsRight', 'BoundsTop')]
            for k, (u, v) in enumerate(zip(b, B)):
                self.assertLess(abs(u - v), 0.3, (t['Text'], b, B))
                # le modele n'est jamais plus petit que la boite Altium (cote sur), a 0,05 mil pres
                self.assertTrue((u <= v + 0.05) if k < 2 else (u >= v - 0.05), (t['Text'], b, B))
            n += 1
        self.assertGreater(n, 70)


if __name__ == '__main__':
    unittest.main()
