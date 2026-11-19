"""Tests de l'importeur de carte (lot B) : aiguillage et écriture, importeur KiCad (annexe D, §7.2), importeur
Altium (import_altium.py, §2.2 : METROID rev2 à rev4 à la racine du dépôt ; cartes d'exemple avec l'option
--cartes-locales DOSSIER).

    python -B test_importer_carte.py --cartes-locales DOSSIER

Les cartes KiCad sont écrites dans un dossier temporaire ; aucun fichier n'est laissé dans le dépôt.
"""
import contextlib
import io
import json
import math
import os
import re
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import formats  # noqa: E402
import import_geometrie as G  # noqa: E402
import importer_carte as IC  # noqa: E402

DEPOT = os.path.dirname(os.path.dirname(os.path.dirname(ICI)))

COUCHES_2 = '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))'
CONTOUR_RECT = '(gr_rect (start 100 50) (end 150 80) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))'
RESEAUX = '(net 0 "") (net 1 "GND") (net 2 "VCC") (net 3 "SIG")'


def kicad(corps="", version=20221018, couches=COUCHES_2, contour=CONTOUR_RECT, reseaux=RESEAUX, entete="",
          setup="(setup (pad_to_mask_clearance 0.05))"):
    return (f'(kicad_pcb (version {version}) (generator "pcbnew") {entete}\n  (general (thickness 1.6))\n'
            f'  {couches}\n  {setup}\n  {reseaux}\n  {contour}\n{corps}\n)\n')


def kicad_monde(fp, lx, ly):
    """Position monde du fichier KiCad d'un point local d'empreinte (formule de KiCad, indépendante de
    l'importeur) : pos + Rk(θ)(lx, ly), rotation visuelle trigonométrique, y vers le bas."""
    fx, fy, t = fp
    a = math.radians(t)
    return fx + lx * math.cos(a) + ly * math.sin(a), fy - lx * math.sin(a) + ly * math.cos(a)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dossier = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def ecrire(self, nom, texte):
        chemin = os.path.join(self.dossier, nom)
        with open(chemin, "w", encoding="utf-8", newline="\n") as f:
            f.write(texte)
        return chemin

    def importer(self, texte, pro=None, dru=None, nom="carte"):
        chemin = self.ecrire(nom + ".kicad_pcb", texte)
        if pro is not None:
            self.ecrire(nom + ".kicad_pro", json.dumps(pro))
        if dru is not None:
            self.ecrire(nom + ".kicad_dru", dru)
        return IC.importer(chemin)

    def commun(self, e, p):
        """Repère du fichier → repère commun (X = x − x0, Y = y0 − y)."""
        x0, y0 = e["source"]["origine_fichier_mm"]
        return (p[0] - x0, y0 - p[1])

    @staticmethod
    def composant(e, des):
        return next(c for c in e["composants"] if c["designateur"] == des)

    def proche(self, a, b, tol=1e-6):
        self.assertLess(math.hypot(a[0] - b[0], a[1] - b[1]), tol, f"{a} contre {b}")


class TestPoses(Base):
    def test_dessus_90(self):
        """Pièce dessus à 90° : une pastille de 0,5 × 2,0 mm est rendue 2,0 × 0,5 en monde."""
        e = self.importer(kicad('''(footprint "Lib:R" (layer "F.Cu") (at 110 60 90) (property "Reference" "R1")
            (pad "1" smd rect (at 1 0 90) (size 0.5 2.0) (layers "F.Cu" "F.Mask") (net 2 "VCC")))'''))
        c = self.composant(e, "R1")
        self.assertEqual((c["face"], c["rotation"]), ("dessus", 90.0))
        p = c["pastilles"][0]
        self.assertEqual((p["largeur"], p["hauteur"], p["rotation"]), (0.5, 2.0, 0.0))
        monde = [G.local_vers_monde(q, c) for q in G.contour_pastille(p)]
        b = G.boite(monde)
        self.assertAlmostEqual(b[2] - b[0], 2.0, places=9)
        self.assertAlmostEqual(b[3] - b[1], 0.5, places=9)
        self.proche(G.local_vers_monde([p["x"], p["y"]], c), self.commun(e, kicad_monde((110, 60, 90), 1, 0)))
        self.assertEqual((p["reseau"], p["couches"], p["face"], p["trou"]), ("VCC", ["TopLayer"], "composant", None))

    def test_dessous_45(self):
        """Pièce dessous à 45° : coins de la pastille identiques à ceux de KiCad, aller-retour monde → local →
        monde à 1·10⁻⁶ mm, rotation relative = θ − φ."""
        fp = (130, 70, 45)
        e = self.importer(kicad('''(footprint "Lib:C" (layer "B.Cu") (at 130 70 45) (property "Reference" "C1")
            (pad "1" smd rect (at 1.2 -0.7 75) (size 0.6 1.0) (layers "B.Cu" "B.Mask") (net 1 "GND"))
            (pad "2" smd rect (at -1.2 0.4 45) (size 0.6 1.0) (layers "B.Cu" "B.Mask") (net 2 "VCC")))'''))
        c = self.composant(e, "C1")
        self.assertEqual((c["face"], c["rotation"]), ("dessous", 45.0))
        for p, (lx, ly, phi) in zip(c["pastilles"], ((1.2, -0.7, 75), (-1.2, 0.4, 45))):
            self.assertAlmostEqual(p["rotation"], (45 - phi) % 360, places=9)
            attendu = self.commun(e, kicad_monde(fp, lx, ly))
            monde = G.local_vers_monde([p["x"], p["y"]], c)
            self.proche(monde, attendu)
            self.proche(G.monde_vers_local(monde, c), (p["x"], p["y"]))
            self.proche(G.local_vers_monde(G.monde_vers_local(attendu, c), c), attendu)
            # coins : centre + Rk(φ)(±0,3, ±0,5) dans le fichier
            px, py = kicad_monde(fp, lx, ly)
            a = math.radians(phi)
            coins_kicad = sorted(self.commun(e, (px + cx * math.cos(a) + cy * math.sin(a),
                                                 py - cx * math.sin(a) + cy * math.cos(a)))
                                 for cx in (-0.3, 0.3) for cy in (-0.5, 0.5))
            coins = sorted(tuple(G.local_vers_monde(q, c)) for q in G.contour_pastille(p))
            for u, v in zip(coins, coins_kicad):
                self.proche(u, v)
            self.assertEqual((p["couches"], p["face"]), (["BottomLayer"], "composant"))

    def test_trou_oblong_tourne_et_decalage(self):
        """Trou oblong tourné, décalage : le trou reste à la position KiCad de la pastille, le cuivre est décalé."""
        fp = (120, 60, 30)
        e = self.importer(kicad('''(footprint "Lib:J" (layer "F.Cu") (at 120 60 30) (property "Reference" "J1")
            (pad "1" thru_hole oval (at 0 0 120) (size 3 1.5) (drill oval 2 1 (offset 0.5 0)) (layers "*.Cu" "*.Mask")
                 (net 1 "GND"))
            (pad "2" thru_hole oval (at 4 0 30) (size 1.5 3) (drill oval 1 2) (layers "*.Cu" "*.Mask")))'''))
        c = self.composant(e, "J1")
        p1, p2 = c["pastilles"]
        self.assertEqual(p1["rotation"], 90.0)
        self.assertEqual(p1["trou"], {"diametre": 1.0, "forme": "oblong", "longueur": 2.0, "rotation": 0.0,
                                      "decalage": [-0.5, 0.0], "metallise": True})
        self.assertEqual((p2["trou"]["forme"], p2["trou"]["rotation"], p2["trou"]["decalage"]), ("oblong", 90.0, [0.0, 0.0]))
        self.assertEqual((p1["traversante"], p1["face"], p1["couches"]), (True, "toutes", ["TopLayer", "BottomLayer"]))
        # trou : position KiCad de la pastille ; cuivre : + Rk(φ)(0,5, 0)
        trou_local = [p1["x"] + G.tourner(p1["trou"]["decalage"], p1["rotation"])[0],
                      p1["y"] + G.tourner(p1["trou"]["decalage"], p1["rotation"])[1]]
        self.proche(G.local_vers_monde(trou_local, c), self.commun(e, kicad_monde(fp, 0, 0)))
        a = math.radians(120)
        self.proche(G.local_vers_monde([p1["x"], p1["y"]], c),
                    self.commun(e, (120 + 0.5 * math.cos(a), 60 - 0.5 * math.sin(a))))
        self.assertTrue(p1["via_dans_pastille_permis"])

    def test_formes(self):
        """Rectangle arrondi (rayon = rratio × min) ; trapèze, chanfrein et pastille composée → polygone."""
        e = self.importer(kicad('''(footprint "Lib:U" (layer "F.Cu") (at 120 65) (property "Reference" "U1")
            (pad "1" smd roundrect (at 0 0) (size 1.0 0.6) (layers "F.Cu") (roundrect_rratio 0.25))
            (pad "2" smd trapezoid (at 3 0) (size 1.0 0.6) (rect_delta 0.2 0) (layers "F.Cu"))
            (pad "3" smd roundrect (at 6 0) (size 1.0 1.0) (layers "F.Cu") (roundrect_rratio 0)
                 (chamfer_ratio 0.2) (chamfer top_left bottom_right))
            (pad "4" smd custom (at 9 0) (size 0.5 0.5) (layers "F.Cu") (options (clearance outline) (anchor rect))
                 (primitives (gr_poly (pts (xy -1 -1) (xy 1 -1) (xy 1 1) (xy -1 1)) (width 0) (fill yes))))
            (pad "5" smd custom (at 12 0) (size 0.5 0.5) (layers "F.Cu") (options (clearance outline) (anchor circle))
                 (primitives (gr_line (start 0 0) (end 2 0) (width 0.4))))
            (pad "6" smd circle (at 15 0) (size 0.8 0.8) (layers "F.Cu"))
            (pad "7" smd oval (at 18 0) (size 0.8 1.6) (layers "F.Cu")))'''))
        c = self.composant(e, "U1")
        p = c["pastilles"]
        self.assertEqual((p[0]["forme"], p[0]["rayon_coin"]), ("rect_arrondi", 0.15))
        self.assertEqual((p[1]["forme"], len(p[1]["points"])), ("polygone", 4))
        self.assertAlmostEqual(G.aire(p[1]["points"]), 0.6, places=9)   # trapèze : aire = l × h
        ys = sorted(q[1] for q in p[1]["points"] if q[0] < 3)          # côté gauche : h + 0,2
        self.assertAlmostEqual(ys[-1] - ys[0], 0.8, places=9)
        self.assertEqual((p[2]["forme"], len(p[2]["points"])), ("polygone", 6))
        self.assertAlmostEqual(G.aire(p[2]["points"]), 1.0 - 2 * 0.2 * 0.2 / 2, places=9)
        # chanfrein « top_left » du fichier (y vers le bas) = coin haut-gauche dans le repère y vers le haut
        self.assertNotIn([5.5, 0.5], p[2]["points"])
        self.assertIn([5.5, -0.5], p[2]["points"])
        self.assertEqual(p[3]["forme"], "polygone")
        self.assertAlmostEqual(G.aire(p[3]["points"]), 4.0, places=9)  # la primitive contient l'ancre : exacte
        self.assertEqual(p[4]["forme"], "polygone")                    # enveloppe convexe, par excès
        b = G.boite(p[4]["points"])
        self.assertGreaterEqual(b[2], 12 + 2.2 - 1e-9)
        self.assertTrue(any("enveloppe convexe" in a for a in e["avertissements"]))
        self.assertEqual((p[5]["forme"], p[5]["largeur"], p[5]["hauteur"]), ("rond", 0.8, 0.8))
        self.assertEqual(p[6]["forme"], "oblong")
        for q in p:
            self.assertGreater(G.aire_signee(G.contour_pastille(q)), 0)


class TestContour(Base):
    def test_formes_du_contour(self):
        """Segments (dans les deux sens), arc, Bézier ; découpes rectangle, polygone, cercle ; sens trigonométrique,
        flèche ≤ 0,005 mm."""
        contour = '''
          (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1))
          (gr_arc (start 40 0) (mid 47.0710678 2.9289322) (end 50 10) (layer "Edge.Cuts") (width 0.1))
          (gr_line (start 50 30) (end 50 10) (layer "Edge.Cuts") (width 0.1))
          (gr_curve (pts (xy 50 30) (xy 35 40) (xy 15 20) (xy 0 30)) (layer "Edge.Cuts") (width 0.1))
          (gr_line (start 0 30) (end 0 0) (layer "Edge.Cuts") (width 0.1))
          (gr_rect (start 5 5) (end 10 8) (layer "Edge.Cuts") (width 0.1))
          (gr_poly (pts (xy 20 10) (xy 25 10) (xy 22 15)) (layer "Edge.Cuts") (width 0.1))
          (gr_circle (center 30 15) (end 32 15) (layer "Edge.Cuts") (width 0.1))'''
        e = self.importer(kicad(contour=contour))
        k = e["carte"]
        self.assertGreater(G.aire_signee(k["contour"]), 0)
        self.assertEqual(k["boite"][:2], [0.0, 0.0])
        self.assertAlmostEqual(k["boite"][2], 50.0, places=6)
        self.assertEqual(min(p[0] for p in k["contour"]), 0.0)
        self.assertEqual(min(p[1] for p in k["contour"]), 0.0)
        self.assertEqual(len(k["decoupes"]), 3)
        self.assertTrue(all(d["proprietaire"] is None for d in k["decoupes"]))
        # arc de centre (40, 10), rayon 10 (fichier) : sommets sur le cercle, milieux des cordes à ≤ 0,005 mm
        centre = self.commun(e, (40, 10))
        arc = [p for p in k["contour"] if p[0] > 40 + 1e-9 and p[1] > self.commun(e, (0, 10))[1] - 1e-9]
        self.assertGreater(len(arc), 10)
        for p in arc:
            self.assertAlmostEqual(math.hypot(p[0] - centre[0], p[1] - centre[1]), 10.0, places=5)
        for a, b in zip(arc, arc[1:]):
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            self.assertLessEqual(10.0 - math.hypot(m[0] - centre[0], m[1] - centre[1]), 0.005 + 1e-9)
        cercle = max(k["decoupes"], key=lambda d: len(d["points"]))
        self.assertAlmostEqual(G.aire(cercle["points"]), math.pi * 4, delta=2 * math.pi * 2 * 0.005)  # flèche

    def test_contour_cercle_et_polygone(self):
        """Contour extérieur d'un seul cercle, puis d'un polygone donné en sens horaire à l'écran."""
        e = self.importer(kicad(contour='(gr_circle (center 50 50) (end 60 50) (layer "Edge.Cuts") (width 0.1))'))
        k = e["carte"]
        self.assertGreater(G.aire_signee(k["contour"]), 0)
        self.assertEqual(k["boite"], [0.0, 0.0, 20.0, 20.0])
        self.assertEqual(e["source"]["origine_fichier_mm"], [40.0, 60.0])
        e = self.importer(kicad(contour='(gr_poly (pts (xy 0 0) (xy 30 0) (xy 30 20) (xy 10 20) (xy 0 10)) '
                                        '(layer "Edge.Cuts") (width 0.1))'))
        k = e["carte"]
        self.assertGreater(G.aire_signee(k["contour"]), 0)
        self.assertAlmostEqual(G.aire(k["contour"]), 600 - 50, places=9)
        self.assertEqual(k["boite"], [0.0, 0.0, 30.0, 20.0])
        self.assertEqual(k["decoupes"], [])

    def test_edge_cuts_empreinte(self):
        """Edge.Cuts d'empreinte → découpe dont la pièce est propriétaire."""
        e = self.importer(kicad('''(footprint "Lib:M" (layer "F.Cu") (at 140 60 30) (property "Reference" "M1")
            (fp_circle (center 0 0) (end 1.5 0) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))
            (fp_line (start 3 -1) (end 5 -1) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))
            (fp_line (start 5 -1) (end 5 1) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))
            (fp_line (start 5 1) (end 3 -1) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))
            (pad "1" thru_hole circle (at 0 3) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask")))
          (gr_circle (center 105 55) (end 106 55) (layer "Edge.Cuts") (width 0.1))'''))
        k = e["carte"]
        self.assertEqual(len(k["decoupes"]), 3)
        propres = [d for d in k["decoupes"] if d["proprietaire"] == "M1"]
        self.assertEqual(len(propres), 2)
        self.assertEqual(sorted(self.composant(e, "M1")["decoupes"]), sorted(d["id"] for d in propres))
        rond = max(propres, key=lambda d: len(d["points"]))
        b = G.boite(rond["points"])
        self.proche(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), self.commun(e, (140, 60)), 0.005)
        tri = min(propres, key=lambda d: len(d["points"]))
        attendu = sorted(self.commun(e, kicad_monde((140, 60, 30), x, y)) for x, y in ((3, -1), (5, -1), (5, 1)))
        for u, v in zip(sorted(map(tuple, tri["points"])), attendu):
            self.proche(u, v)


class TestKicad9EtGenres(Base):
    def test_fichier_kicad_9(self):
        """KiCad 9 : numéros de couches 0, 2, 4…, rang = ordre physique, type_kicad, épaisseur, « *.Cu »."""
        couches = '''(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (4 "In1.Cu" power) (6 "In2.Cu" mixed)
                     (25 "Edge.Cuts" user) (5 "F.SilkS" user "F.Silkscreen"))'''
        entete = '(generator_version "9.0")'
        setup = '''(setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
                     (layer "In1.Cu" (type "copper") (thickness 0.0175)) (layer "B.Cu" (type "copper") (thickness 0.07)))
                     (pad_to_mask_clearance 0))'''
        corps = '''(footprint "Lib:J" (layer "F.Cu") (uuid "a") (at 110 60) (property "Reference" "J1" (at 0 0 0) (layer "F.SilkS"))
            (property "Value" "CONN" (at 0 2 0) (layer "F.Fab"))
            (pad "1" thru_hole circle (at 0 0) (size 1.7 1.7) (drill 1.0) (layers "*.Cu" "*.Mask") (net 1 "GND")
                 (pintype "passive")))'''
        e = self.importer(kicad(corps, version=20241229, couches=couches, entete=entete, setup=setup))
        self.assertEqual(e["source"]["version_fichier"], "20241229")
        cs = e["carte"]["couches"]
        self.assertEqual([(c["nom"], c["nom_fichier"], c["rang"], c["type_kicad"]) for c in cs],
                         [("TopLayer", "F.Cu", 0, "signal"), ("MidLayer1", "In1.Cu", 1, "power"),
                          ("MidLayer2", "In2.Cu", 2, "mixed"), ("BottomLayer", "B.Cu", 3, "signal")])
        self.assertEqual([c["epaisseur_cuivre_mm"] for c in cs], [0.035, 0.0175, None, 0.07])
        self.assertEqual(e["carte"]["noms_couches_cuivre"], ["TopLayer", "MidLayer1", "MidLayer2", "BottomLayer"])
        j1 = self.composant(e, "J1")
        self.assertEqual(j1["pastilles"][0]["couches"], ["TopLayer", "MidLayer1", "MidLayer2", "BottomLayer"])
        self.assertEqual(j1["valeur"], "CONN")

    def test_liaison_reseaux_et_graphique(self):
        """net_tie_pad_groups → liaison_reseaux (région qui touche deux réseaux) ; board_only sans cuivre →
        graphique ; verrou, valeur, description, empreinte, canal."""
        corps = '''(footprint "Lib:NetTie_2" (layer "F.Cu") (at 110 60) (property "Reference" "NT1")
            (net_tie_pad_groups "1, 2")
            (fp_poly (pts (xy -0.5 -0.1) (xy 0.5 -0.1) (xy 0.5 0.1) (xy -0.5 0.1)) (layer "F.Cu") (width 0) (fill solid))
            (pad "1" smd rect (at -0.6 0) (size 0.4 0.4) (layers "F.Cu") (net 1 "GND"))
            (pad "2" smd rect (at 0.6 0) (size 0.4 0.4) (layers "F.Cu") (net 2 "VCC")))
          (footprint "Lib:Logo" (layer "F.Cu") (at 120 70) (property "Reference" "G1") (attr board_only exclude_from_bom)
            (fp_poly (pts (xy -2 -2) (xy 2 -2) (xy 2 2)) (layer "F.SilkS") (width 0.1) (fill solid)))
          (footprint "Lib:R_0603" locked (layer "F.Cu") (at 130 60) (descr "résistance 0603")
            (property "Reference" "R1") (property "Value" "10k") (path "/feuille-a/symbole-1") (attr smd)
            (pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu") (net 3 "SIG")))
          (footprint "Lib:R_0603" (layer "F.Cu") (at 135 60) (locked yes)
            (property "Reference" "R2") (property "Value" "10k") (path "/feuille-b/symbole-1") (attr smd)
            (pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu") (net 3 "SIG")))
          (footprint "Lib:R_0603" (layer "F.Cu") (at 140 60)
            (property "Reference" "R3") (path "/feuille-a/symbole-3")
            (pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu") (net 3 "SIG")))'''
        e = self.importer(kicad(corps))
        nt = self.composant(e, "NT1")
        self.assertEqual(nt["genre"], "liaison_reseaux")
        self.assertEqual(len(nt["regions"]), 1)
        r = nt["regions"][0]
        self.assertEqual((r["liaison"], r["reseau"], r["pastille"], r["couches"]), (["GND", "VCC"], "", None, ["TopLayer"]))
        self.assertAlmostEqual(G.aire(r["points"]), 0.2, places=9)
        g1 = self.composant(e, "G1")
        self.assertEqual(g1["genre"], "graphique")
        for u, v in zip(g1["boite"], [-2.05, -2.05, 2.05, 2.05]):   # trait de 0,1 mm, raccords par excès
            self.assertLessEqual(abs(u - v), 0.005)
        r1, r2, r3 = (self.composant(e, d) for d in ("R1", "R2", "R3"))
        self.assertEqual((r1["verrouille"], r2["verrouille"], r3["verrouille"]), (True, True, False))
        self.assertEqual((r1["empreinte"], r1["valeur"], r1["description"]), ("R_0603", "10k", "résistance 0603"))
        self.assertEqual(r1["canal"], {"groupe": "symbole-1", "instance": "/feuille-a"})
        self.assertEqual(r2["canal"], {"groupe": "symbole-1", "instance": "/feuille-b"})
        self.assertIsNone(r3["canal"])
        self.assertEqual((r1["genre"], r1["hauteur"], r1["source_hauteur"]), ("standard", 0.0, "inconnue"))


class TestZones(Base):
    def test_zone_multicouche(self):
        """Zone sur deux couches → une entrée par couche ; couverture = moitié de la carte (hors découpe)."""
        corps = '''(zone (net 1) (net_name "GND") (layers "F.Cu" "B.Cu") (name "plan") (hatch edge 0.5)
              (polygon (pts (xy 100 50) (xy 125 50) (xy 125 80) (xy 100 80)))
              (polygon (pts (xy 105 55) (xy 110 55) (xy 110 60) (xy 105 60))))'''
        e = self.importer(kicad(corps))
        rs = e["carte"]["remplissages"]
        self.assertEqual([(r["couche"], r["reseau"], r["nom"]) for r in rs],
                         [("TopLayer", "GND", "plan"), ("BottomLayer", "GND", "plan")])
        self.assertEqual(len(rs[0]["trous"]), 1)
        self.assertAlmostEqual(rs[0]["couverture"], (750 - 25) / 1500, delta=0.005)
        self.assertGreater(G.aire_signee(rs[0]["contour"]), 0)

    def test_keepout(self):
        """Keep-out de la carte (toutes les couches) et d'empreinte (repère local) avec leurs effets."""
        corps = '''(zone (net 0) (net_name "") (layers "*.Cu") (hatch edge 0.5)
              (keepout (tracks not_allowed) (vias not_allowed) (pads allowed) (copperpour not_allowed)
                       (footprints not_allowed))
              (polygon (pts (xy 140 70) (xy 145 70) (xy 145 75) (xy 140 75))))
          (footprint "Lib:Ant" (layer "F.Cu") (at 120 60 90) (property "Reference" "AE1")
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
            (zone (net 0) (net_name "") (layer "F.Cu") (hatch edge 0.5)
              (keepout (tracks not_allowed) (vias allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))
              (polygon (pts (xy 121 59) (xy 125 59) (xy 125 61) (xy 121 61)))))'''
        e = self.importer(kicad(corps))
        z = e["carte"]["zones_interdites"]
        self.assertEqual(len(z), 1)
        self.assertEqual((z[0]["couches"], z[0]["effets"], z[0]["forme"], z[0]["source"]),
                         (["toutes"], ["pistes", "vias", "remplissages", "placement"], "polygone", "keepout"))
        self.assertAlmostEqual(G.aire(z[0]["points"]), 25.0, places=9)
        ae = self.composant(e, "AE1")
        self.assertEqual(len(ae["zones_interdites"]), 1)
        zk = ae["zones_interdites"][0]
        self.assertEqual((zk["couches"], zk["effets"]), (["TopLayer"], ["pistes", "remplissages"]))
        # zone d'empreinte en coordonnées absolues dans le .kicad_pcb : (121..125, 59..61), à droite de la pièce
        # posée en (120, 60) et tournée de 90° ; dans le repère local, le monde +x est le local −y
        b = G.boite(zk["points"])
        self.assertEqual([round(v, 9) for v in b], [-1.0, -5.0, 1.0, -1.0])
        self.assertEqual(e["carte"]["remplissages"], [])

    def test_cuivre_libre(self):
        corps = '''(segment (start 110 60) (end 120 60) (width 0.25) (layer "F.Cu") (net 1))
          (arc (start 120 60) (mid 122.9289 61.2132) (end 124.1421 64.1421) (width 0.3) (layer "B.Cu") (net 2))
          (via (at 130 70) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1) (locked yes))
          (gr_text "TXT" (at 140 60) (layer "F.Cu") (effects (font (size 1 1) (thickness 0.15))))'''
        e = self.importer(kicad(corps))
        ce = e["carte"]["cuivre_existant"]
        self.assertEqual([(c["id"], c["type"], c["reseau"], c["couches"], c["verrouille"]) for c in ce],
                         [("E1", "piste", "GND", ["TopLayer"], False), ("E2", "piste", "VCC", ["BottomLayer"], False),
                          ("E3", "via", "GND", ["TopLayer", "BottomLayer"], True)])
        self.assertEqual(ce[0]["points"], [[10.0, 20.0], [20.0, 20.0]])
        self.assertGreater(len(ce[1]["points"]), 3)
        self.assertEqual((ce[2]["x"], ce[2]["y"], ce[2]["diametre"], ce[2]["trou"]), (30.0, 10.0, 0.8, 0.4))
        self.assertEqual([c["pistes_libres"] for c in e["carte"]["couches"]], [1, 1])
        ob = e["carte"]["obstacles_cuivre"]
        self.assertEqual((len(ob), ob[0]["type"], ob[0]["couches"]), (1, "texte", ["TopLayer"]))


PRO = {
    "board": {"design_settings": {"rules": {
        "min_clearance": 0.18, "min_track_width": 0.15, "min_via_diameter": 0.5, "min_through_hole_diameter": 0.25,
        "min_hole_to_hole": 0.25, "min_copper_edge_clearance": 0.3}}},
    "net_settings": {
        "classes": [
            {"name": "Default", "clearance": 0.2, "track_width": 0.25, "via_diameter": 0.6, "via_drill": 0.3,
             "diff_pair_width": 0.15, "diff_pair_gap": 0.2},
            {"name": "Power", "clearance": 0.3, "track_width": 0.5, "via_diameter": 0.8, "via_drill": 0.4},
            {"name": "HS", "clearance": 0.15, "track_width": 0.2, "diff_pair_width": 0.12, "diff_pair_gap": 0.1},
            {"name": "Analog", "nets": ["SIG"]},
        ],
        "netclass_assignments": {"VCC": "Power"},
        "netclass_patterns": [{"netclass": "HS", "pattern": "USB_*"}],
    },
}
RESEAUX_REGLES = '(net 0 "") (net 1 "GND") (net 2 "VCC") (net 3 "SIG") (net 4 "USB_P") (net 5 "USB_N") ' \
                 '(net 6 "D+") (net 7 "D-")'
DRU = '''(version 1)
# commentaire
(rule "isolement_hs" (condition "A.NetClass == 'HS'") (constraint clearance (min 0.3)))
'''


class TestRegles(Base):
    def test_annexe_d(self):
        e = self.importer(kicad(reseaux=RESEAUX_REGLES), pro=PRO, dru=DRU)
        self.assertEqual(e["classes"], {"Power": ["VCC"], "HS": ["USB_P", "USB_N"], "Analog": ["SIG"]})
        r = e["regles"]
        iso = r["isolement"]
        self.assertEqual((iso["classes"], iso["classe_reference"], iso["defaut"]),
                         (["Default", "Power", "HS", "Analog"], 0, 0.2))
        self.assertEqual(iso["matrice"], [[0.2, 0.3, 0.2, 0.2], [0.3, 0.3, 0.3, 0.3], [0.2, 0.3, 0.18, 0.2],
                                          [0.2, 0.3, 0.2, 0.2]])
        rr = r["reseaux"]
        self.assertEqual(rr["VCC"]["largeurs"], [0.15, 0.5, None])
        self.assertEqual(rr["SIG"]["largeurs"], [0.15, 0.25, None])      # Analog sans largeur : celle de Default
        self.assertEqual(rr["GND"]["largeurs"], [0.15, 0.25, None])
        self.assertEqual(rr["VCC"]["via"], {"diametre": 0.8, "trou": 0.4, "diametre_min": 0.5, "diametre_max": None,
                                            "trou_min": 0.25, "trou_max": None})
        self.assertEqual(rr["USB_P"]["classes_isolement"], {"piste": 2, "via": 2, "surface": 2})
        self.assertEqual((rr["VCC"]["regle_largeur_propre"], rr["GND"]["regle_largeur_propre"]), (True, False))
        self.assertEqual(r["largeur_defaut"], [0.15, 0.25, None])
        self.assertEqual((r["trou_trou"], r["garde_bord"]), (0.25, 0.3))
        self.assertEqual(len(r["non_interpretees"]), 1)
        ni = r["non_interpretees"][0]
        self.assertEqual((ni["type"], ni["nom"], ni["portee1"]), ("kicad_dru:clearance", "isolement_hs",
                                                                  "A.NetClass == 'HS'"))
        self.assertIn("kicad_dru", ni["cause"])
        self.assertEqual(sorted((p["nom"], p["positif"], p["negatif"]) for p in e["paires"]),
                         [("D", "D+", "D-"), ("USB", "USB_P", "USB_N")])
        usb = next(p for p in e["paires"] if p["nom"] == "USB")
        self.assertEqual((usb["largeur"], usb["ecart"]), (0.12, 0.1))
        self.assertEqual([x["nom"] for x in e["regles_fichier"] if x["type"] == "netclass"],
                         ["Default", "Power", "HS", "Analog"])

    def test_pastilles_et_classes(self):
        corps = '''(footprint "Lib:R" (layer "F.Cu") (at 110 60) (property "Reference" "R1") (solder_mask_margin 0.07)
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "VCC") (solder_mask_margin 0.02))
            (pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu") (net 4 "USB_P"))
            (pad "3" smd rect (at 4 0) (size 1 1) (layers "F.Cu")))'''
        e = self.importer(kicad(corps, reseaux=RESEAUX_REGLES), pro=PRO)
        p = self.composant(e, "R1")["pastilles"]
        self.assertEqual([q["classe_isolement"] for q in p], [1, 2, 0])
        self.assertEqual([q["expansion_masque"] for q in p], [0.02, 0.07, 0.07])

    def test_sans_projet(self):
        e = self.importer(kicad())
        r = e["regles"]
        self.assertEqual(r["isolement"], {"defaut": 0.2, "classes": ["Default"], "matrice": [[0.2]],
                                          "classe_reference": 0})
        self.assertEqual(r["largeur_defaut"], [0.254, 0.3, 3.0])
        self.assertEqual(r["via"], {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.5, "diametre_max": 0.8,
                                    "trou_min": 0.25, "trou_max": 0.4})
        self.assertEqual(r["trou_trou"], 0.254)
        self.assertEqual(r["reseaux"]["GND"]["largeurs"], [0.254, 0.3, 3.0])
        self.assertTrue(any("carte.kicad_pro absent" in a and "§3.8" in a for a in e["avertissements"]))
        self.assertEqual(e["classes"], {})
        self.assertEqual(e["regles"]["non_interpretees"], [])


class TestLigneDeCommande(Base):
    def lancer(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = IC.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def refus(self, chemin, attendu):
        rc, out, err = self.lancer(chemin, "--entree", os.path.join(self.dossier, "e.json"))
        self.assertEqual(rc, 2, err)
        self.assertIn(attendu, err)
        self.assertIn(os.path.basename(chemin), err)
        self.assertNotIn(self.dossier, err + out)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(os.path.join(self.dossier, "e.json")))

    def test_ecriture_et_validation(self):
        corps = '''(footprint "Lib:R" (layer "F.Cu") (at 110 60) (property "Reference" "R1") (descr "résistance Ω")
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "VCC")))'''
        chemin = self.ecrire("carte.kicad_pcb", kicad(corps))
        sortie = os.path.join(self.dossier, "e.json")
        rc, out, err = self.lancer(chemin, "--entree", sortie)
        self.assertEqual(rc, 0, err)
        with open(sortie, "rb") as f:
            brut = f.read()
        self.assertFalse(brut.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", brut)
        self.assertIn("résistance Ω".encode("utf-8"), brut)
        texte = brut.decode("utf-8")
        for t in (texte, out, err):
            self.assertNotIn(self.dossier, t)
            self.assertNotIn(self.dossier.replace("\\", "\\\\"), t)
        self.assertEqual(sorted(os.listdir(self.dossier)), ["carte.kicad_pcb", "e.json"])
        d = json.loads(texte)
        self.assertEqual(d["source"]["fichier"], "carte.kicad_pcb")
        self.assertEqual(formats.valider_entree_carte(d), [])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(formats.main(["valider", sortie]), 0)

    def test_refus_version(self):
        self.refus(self.ecrire("ancienne.kicad_pcb", kicad(version=20171130)), "fichier antérieur à KiCad 6")

    def test_refus_panneau_boucles(self):
        contour = CONTOUR_RECT + '(gr_rect (start 200 50) (end 250 80) (layer "Edge.Cuts") (width 0.1))'
        self.refus(self.ecrire("panneau.kicad_pcb", kicad(contour=contour)), "panneau")

    def test_refus_panneau_references(self):
        fp = '''(footprint "Lib:R" (layer "F.Cu") (at {x} 60) (property "Reference" "R1")
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))'''
        self.refus(self.ecrire("double.kicad_pcb", kicad(fp.format(x=110) + fp.format(x=120))), "panneau")

    def test_refus_fichier_absent_et_extension(self):
        self.refus(os.path.join(self.dossier, "absente.kicad_pcb"), "fichier introuvable")
        self.refus(self.ecrire("carte.txt", "texte"), "extension inconnue")

    def test_refus_contour_ouvert_et_options(self):
        contour = '(gr_line (start 0 0) (end 10 0) (layer "Edge.Cuts") (width 0.1))'
        self.refus(self.ecrire("ouvert.kicad_pcb", kicad(contour=contour)), "contour non fermé")
        rc, _out, err = self.lancer("carte.kicad_pcb", "--sortie", "x")
        self.assertEqual(rc, 2)
        self.assertIn("option inconnue", err)
        rc, _out, err = self.lancer("carte.kicad_pcb")
        self.assertEqual((rc, "option --entree manquante" in err), (2, True))


class TestGeometrie(unittest.TestCase):
    def test_bezier_et_boucles(self):
        pts = G.discretiser_bezier((0, 0), (0, 10), (10, 10), (10, 0))
        self.assertEqual((pts[0], pts[-1]), ([0, 0], [10, 0]))
        for t in (0.1, 0.33, 0.5, 0.77):
            x = 3 * (1 - t) * t * t * 10 + t ** 3 * 10
            y = 3 * (1 - t) ** 2 * t * 10 + 3 * (1 - t) * t * t * 10
            self.assertLess(G.distance_point_polygone((x, y), pts + [[10, -1], [0, -1]]), 0.005 + 1e-9)
        b = G.assembler_boucles([([(0, 0), (1, 0)], "a", False), ([(1, 1), (1.0004, 0.0003)], "b", False),
                                 ([(0, 1), (1, 1)], "c", False), ([(0, 0), (0, 1)], None, False)])
        self.assertEqual(len(b), 1)
        self.assertEqual(len(b[0][0]), 4)
        self.assertEqual(b[0][1], {"a", "b", "c", None})

    def test_aire_et_distances(self):
        carre = [[0, 0], [10, 0], [10, 10], [0, 10]]
        self.assertAlmostEqual(G.aire_balayage([carre], [[[2, 2], [4, 2], [4, 4], [2, 4]]]), 96.0, places=6)
        self.assertAlmostEqual(G.aire_balayage([carre, [[5, -5], [15, -5], [15, 5], [5, 5]]]), 25.0, places=6)
        self.assertTrue(G.point_dans_polygone((5, 5), carre))
        self.assertAlmostEqual(G.distance_point_polygone((13, 14), carre), 5.0)
        self.assertEqual(G.orienter(carre[::-1]), [[0, 10], [10, 10], [10, 0], [0, 0]][::-1])
        self.assertTrue(G.polygones_se_touchent(carre, [[10, 10], [12, 10], [12, 12]]))
        self.assertFalse(G.polygones_se_touchent(carre, [[11, 11], [12, 11], [12, 12]]))


# =====================================================================================================================
# Importeur Altium (import_altium.py, §2.2)
# =====================================================================================================================
CARTES_LOCALES = None
SL1 = "SL1 Xilinx Spartan-IIE PQ208 Rev1.02.PcbDoc"
WIFI = "WiFi.PcbDoc"
SODIMM = "PC4-SODIMM_V200_RC_A1_20141015.PcbDoc"
MIL = 0.0254
_CACHE = {}


def importer_altium(chemin):
    if chemin not in _CACHE:
        _CACHE[chemin] = IC.importer(chemin)
    return _CACHE[chemin]


def carte_rev(k):
    return os.path.join(DEPOT, f"PCB_METROID_V2_rev{k}.PcbDoc")


def chaines(v):
    """Toutes les chaînes (clés comprises) d'un objet JSON."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from chaines(x)
    elif isinstance(v, list):
        for x in v:
            yield from chaines(x)


def segments_se_croisent(poly):
    """Vrai si deux côtés non voisins du polygone se coupent (contour non simple)."""
    n = len(poly)
    segs = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    boites = [(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])) for a, b in segs]
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            bi, bj = boites[i], boites[j]
            if bi[2] < bj[0] or bj[2] < bi[0] or bi[3] < bj[1] or bj[3] < bi[1]:
                continue
            if G._segments_se_coupent(segs[i][0], segs[i][1], segs[j][0], segs[j][1]):
                return True
    return False


class VerifsAltium:
    def verifier_commun(self, e):
        """Schéma (formats.py), contour simple et trigonométrique, identifiants de pastilles, aucun chemin."""
        self.assertEqual(formats.valider_entree_carte(json.loads(json.dumps(e))), [])
        self.verifier_forme(e)

    def verifier_forme(self, e):
        c = e["carte"]["contour"]
        self.assertGreater(G.aire_signee(c), 0)
        self.assertFalse(segments_se_croisent(c))
        self.assertEqual(e["carte"]["boite"][:2], [0.0, 0.0])
        self.assertAlmostEqual(min(p[0] for p in c), 0.0, places=9)
        self.assertAlmostEqual(min(p[1] for p in c), 0.0, places=9)
        for comp in e["composants"]:
            for k, p in enumerate(comp["pastilles"]):
                self.assertEqual(p["id"], f"{comp['designateur']}#{k}")
        texte = json.dumps(e, ensure_ascii=False)
        for motif in ("FILENAME", "SOURCEFOOTPRINTLIBRARY", ".PcbLib", ".IntLib", ".SchLib"):
            self.assertNotIn(motif, texte)
        self.assertEqual([t for t in chaines(e) if re.search(r"[A-Za-z]:[\/]|^[\/]{2}|/(home|Users)/", t)], [])

    def verifier_fleche(self, chemin, e):
        """Arcs du contour : les sommets posés sur chaque arc du fichier ont une flèche de corde ≤ 0,005 mm."""
        from altium_fichier import PcbDoc
        x0, y0 = e["source"]["origine_fichier_mil"]
        arcs = [((s["cx"] - x0) * MIL, (s["cy"] - y0) * MIL, s["r"] * MIL) for s in PcbDoc.ouvrir(chemin).contour_carte()
                if s["type"] == 1 and s["r"]]
        c = e["carte"]["contour"]
        n = 0
        for cx, cy, r in arcs:
            sur = {i for i, p in enumerate(c) if abs(math.hypot(p[0] - cx, p[1] - cy) - r) < 1e-5}
            for i in sur:
                j = (i + 1) % len(c)
                if j in sur:
                    corde = math.hypot(c[j][0] - c[i][0], c[j][1] - c[i][1])
                    self.assertLessEqual(r - math.sqrt(max(0.0, r * r - corde * corde / 4)), 0.005 + 1e-9)
                    n += 1
        if arcs:
            self.assertGreater(n, 0)
        return len(arcs)


@unittest.skipUnless(os.path.exists(carte_rev(3)), "PCB_METROID_V2_rev3.PcbDoc absent de la racine du dépôt")
class TestAltiumMetroid(VerifsAltium, unittest.TestCase):
    def test_rev2_rev3_rev4(self):
        for k in (2, 3, 4):
            e = importer_altium(carte_rev(k))
            self.assertEqual(e["format"], "entree_carte/2")
            self.assertEqual(e["source"]["type"], "altium_pcbdoc")
            self.assertEqual(len(e["source"]["sha256"]), 64)
            self.verifier_commun(e)
            self.verifier_fleche(carte_rev(k), e)

    def test_rev3_section_2_2(self):
        from altium_fichier import PcbDoc
        e = importer_altium(carte_rev(3))
        des = [c["designateur"] for c in e["composants"]]
        self.assertEqual((len(des), len(set(des))), (359, 359))
        self.assertIn("M4_ALIM1", des)
        self.assertIn("M4_ALIM2", des)
        C = {c["designateur"]: c for c in e["composants"]}
        self.assertEqual(C["M4_ALIM1"]["designateur_source"], "M4")
        self.assertEqual(C["M4_ALIM1"]["canal"]["groupe"], "M4")
        self.assertNotEqual(C["M4_ALIM1"]["canal"]["instance"], C["M4_ALIM2"]["canal"]["instance"])
        dec = e["carte"]["decoupes"]
        self.assertEqual(sorted(d["proprietaire"] for d in dec), ["M4_ALIM1", "M4_ALIM1", "M4_ALIM2", "M4_ALIM2"])
        self.assertEqual(sorted(C["M4_ALIM1"]["decoupes"] + C["M4_ALIM2"]["decoupes"]), sorted(d["id"] for d in dec))
        self.assertEqual(len(e["carte"]["trous"]), 4)
        self.assertEqual([(c["nom"], c["rang"]) for c in e["carte"]["couches"]],
                         [("TopLayer", 0), ("MidLayer1", 1), ("MidLayer2", 2), ("BottomLayer", 3)])
        self.assertAlmostEqual(e["carte"]["boite"][2], 164.0, places=3)
        self.assertAlmostEqual(e["carte"]["boite"][3], 97.0, places=3)
        self.assertEqual(list(IC.repere(PcbDoc.ouvrir(carte_rev(3)))), e["source"]["origine_fichier_mil"])
        u = next(p for p in C["U10"]["pastilles"] if p["nom"] == "1")
        self.assertEqual(sorted([round(u["largeur"], 3), round(u["hauteur"], 3)]), [0.24, 0.6])

    def test_b4_cuivre_sans_masque(self):
        """B4, non-régression : l'importation de rev3 est identique à exemples/metroid_v2/entree_carte.json (dont les
        pastilles ont été appariées une à une, 979/979 à 0,001 mm, avec l'ancien pastilles_reelles.json en B-ET5,
        avant sa suppression en B-ET7)."""
        e = json.loads(json.dumps(importer_altium(carte_rev(3))))
        with open(os.path.join(ICI, "exemples", "metroid_v2", "entree_carte.json"), encoding="utf-8") as f:
            ref = json.load(f)
        R = {c["designateur"]: c for c in ref["composants"]}
        n = 0
        for c in e["composants"]:
            self.assertEqual(c["pastilles"], R[c["designateur"]]["pastilles"], c["designateur"])
            n += len(c["pastilles"])
        self.assertGreaterEqual(n, 979)
        self.assertEqual(e, ref)

    def test_ligne_de_commande(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = os.path.join(d, "e3.json")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(IC.main([carte_rev(3), "--entree", sortie]), 0)
            with open(sortie, encoding="utf-8") as f:
                e = json.load(f)
            self.assertEqual(formats.valider_entree_carte(e), [])
            self.assertEqual(e["source"]["fichier"], "PCB_METROID_V2_rev3.PcbDoc")

    def test_carte_sans_flux_facultatifs(self):
        """Carte fabriquée (copie de rev3 écrite dans un dossier temporaire) sans Arcs6, Fills6, Regions6, Texts6,
        Polygons6, Vias6."""
        from altium_fichier import PcbDoc
        doc = PcbDoc.ouvrir(carte_rev(3))
        for i, des in doc.designateurs().items():   # rev3 est multicanal : sans Texts6, SOURCEDESIGNATOR serait double
            doc.data("Components6")[i]["SOURCEDESIGNATOR"] = des
        doc.marquer("Components6")
        for st in ("Arcs6", "Fills6", "Regions6", "Texts6", "Polygons6", "Vias6"):
            doc.racine.enfants.pop(st, None)
            for k in [k for k in doc.flux if k.startswith(st + "/")]:
                del doc.flux[k]
            doc._compteurs.discard(st)
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "sans_flux.PcbDoc")
            doc.enregistrer(chemin)
            self.assertFalse(PcbDoc.ouvrir(chemin).existe("Texts6"))
            e = IC.importer(chemin)
        self.verifier_commun(e)
        self.assertEqual(len(e["composants"]), 359)
        self.assertEqual(e["carte"]["remplissages"], [])

    def test_empilage_non_physique(self):
        """Empilage dont la numérotation n'est pas l'ordre physique (MID2 au-dessus de MID1) : rangs physiques."""
        from altium_fichier import PcbDoc
        doc = PcbDoc.ouvrir(carte_rev(3))
        b = doc.board
        cles = {b.get(k): k for k in b.cles() if k.startswith("V9_STACK_LAYER") and k.endswith("_LAYERID")}
        k1, k2 = cles[str(0x01000002)], cles[str(0x01000003)]
        b[k1], b[k2] = str(0x01000003), str(0x01000002)
        doc.marquer("Board6")
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "empilage.PcbDoc")
            doc.enregistrer(chemin)
            e = IC.importer(chemin)
        self.assertEqual([(c["nom"], c["rang"]) for c in e["carte"]["couches"]],
                         [("TopLayer", 0), ("MidLayer2", 1), ("MidLayer1", 2), ("BottomLayer", 3)])
        self.verifier_forme(e)


class CasCartesLocales(VerifsAltium):
    def chemin(self, nom):
        c = os.path.join(CARTES_LOCALES, nom)
        self.assertTrue(os.path.exists(c), f"carte d'exemple absente : {nom}")
        return c

    def refus(self, nom):
        """Import refusé (code 2) alors que les accesseurs de géométrie lèvent une exception : refus fait avant."""
        from altium_fichier import PcbDoc
        sauves = {}

        def interdit(nom_acc):
            def f(self, *args, **kw):
                raise AssertionError(f"accesseur {nom_acc} appelé avant le refus")
            return f
        for a in ("pastilles", "pistes", "arcs", "vias", "remplissages", "regions", "textes", "polygones",
                  "corps_3d", "regles", "contour_carte", "couches_cuivre"):
            sauves[a] = getattr(PcbDoc, a)
            setattr(PcbDoc, a, interdit(a))
        err = io.StringIO()
        try:
            with self.assertRaises(G.ErreurEntree) as cm:
                IC.importer(self.chemin(nom))
            with tempfile.TemporaryDirectory() as d:
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    code = IC.main([self.chemin(nom), "--entree", os.path.join(d, "e.json")])
        finally:
            for a, f in sauves.items():
                setattr(PcbDoc, a, f)
        self.assertEqual(code, 2)
        self.assertNotIn(os.path.dirname(self.chemin(nom)), err.getvalue())
        return str(cm.exception)

    def test_refus_rigide_flexible(self):
        self.assertIn("carte rigide-flexible non prise en charge", self.refus("Bluetooth_Sentinel.PcbDoc"))

    def test_refus_panneau(self):
        self.assertIn("panneau de cartes imbriquées : importer WiFi.PcbDoc", self.refus("WiFi_panel.PcbDoc"))

    def test_imports(self):
        for nom in (SL1, WIFI, SODIMM):
            e = importer_altium(self.chemin(nom))
            self.assertEqual(e["format"], "entree_carte/2")
            self.verifier_forme(e)
            err = formats.valider_entree_carte(json.loads(json.dumps(e)))
            if nom == SODIMM:
                # SODIMM : aucune règle Width de portée All -> largeurs null (§2.2 : null = absente du fichier) ;
                # formats.py (lot E) n'admet pas encore null ici : seules ces erreurs sont admises (besoin du lot E)
                self.assertTrue(all(x.startswith(("regles.reseaux.", "regles.largeur_defaut")) and "null" in x
                                    for x in err), err[:5])
            else:
                self.assertEqual(err, [])

    def test_b5_contour(self):
        for nom in (SL1, WIFI):
            e = importer_altium(self.chemin(nom))
            self.assertGreater(self.verifier_fleche(self.chemin(nom), e), 0)
        e = importer_altium(self.chemin(WIFI))
        self.assertAlmostEqual(e["carte"]["aire_reference_mm2"], 784.773, delta=0.001)
        self.assertLess(abs(G.aire(e["carte"]["contour"]) - e["carte"]["aire_reference_mm2"]) / 784.773, 1e-3)

    def test_sl1(self):
        e = importer_altium(self.chemin(SL1))
        C = {c["designateur"]: c for c in e["composants"]}
        self.assertEqual(C["NT1"]["genre"], "liaison_reseaux")
        self.assertIn(["CLK_BRD", "CLK_BRD_1"], [r["liaison"] for r in C["NT1"]["regions"]])
        self.assertEqual(C["Logo5"]["genre"], "graphique")
        self.assertEqual(sum(c["source_hauteur"] == "corps3d" for c in e["composants"]), 110)
        self.assertIn("GND", [p["reseau"] for p in e["pastilles_libres"]])
        t = e["carte"]["trous"]
        self.assertEqual(len(t), 4)
        for x in t:
            self.assertAlmostEqual(x["trou"]["diametre"], 3.3, places=4)
            self.assertFalse(x["metallise"])
            self.assertEqual(x["diametre_cuivre"], 0.0)
        oblongs = {p["trou"]["rotation"] for p in C["J1"]["pastilles"] if p["trou"] and p["trou"]["forme"] == "oblong"}
        self.assertEqual(oblongs, {0.0, 90.0})
        self.assertEqual(len(C["U1"]["zones_interdites"]), 2)
        self.assertEqual(len(C["LCD1"]["zones_interdites"]), 4)
        self.assertTrue(all(z["couches"] == ["toutes"] for z in C["LCD1"]["zones_interdites"]))

    def test_wifi(self):
        e = importer_altium(self.chemin(WIFI))
        C = {c["designateur"]: c for c in e["composants"]}
        couches = [tuple(p["couches"]) for p in C["P1"]["pastilles"]]
        self.assertEqual((couches.count(("TopLayer",)), couches.count(("BottomLayer",))), (26, 26))
        b = C["DS2"]["boite"]
        self.assertAlmostEqual((b[2] - b[0]) * (b[3] - b[1]), 1.9, delta=0.1)
        t = e["carte"]["trous"]
        self.assertEqual(len(t), 2)
        for x in t:
            self.assertAlmostEqual(x["trou"]["diametre"], 2.6, places=4)
            self.assertEqual(x["diametre_cuivre"], 0.0)


def load_tests(loader, tests, pattern):
    """Ajoute les tests des cartes d'exemple quand --cartes-locales est donné ; sinon le dit."""
    if CARTES_LOCALES:
        classe = type("TestCartesLocales", (CasCartesLocales, unittest.TestCase), {"__module__": __name__})
        tests.addTests(loader.loadTestsFromTestCase(classe))
    else:
        sys.stderr.write("test_importer_carte : tests des cartes d'exemple non lancés (option --cartes-locales "
                         "absente)\n")
    return tests


def _lire_options(argv):
    """Retire --cartes-locales DOSSIER (ou --cartes-locales=DOSSIER) de argv ; rend (dossier, reste)."""
    dossier, reste, i = None, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--cartes-locales":
            if i + 1 >= len(argv):
                sys.stderr.write("--cartes-locales : dossier attendu\n")
                sys.exit(2)
            dossier, i = argv[i + 1], i + 2
            continue
        if a.startswith("--cartes-locales="):
            dossier = a.split("=", 1)[1]
        else:
            reste.append(a)
        i += 1
    if dossier is not None and not os.path.isdir(dossier):
        sys.stderr.write("--cartes-locales : dossier introuvable\n")
        sys.exit(2)
    return dossier, reste


if __name__ == "__main__":
    CARTES_LOCALES, _reste = _lire_options(sys.argv[1:])
    _prog = unittest.main(argv=[sys.argv[0]] + _reste, exit=False)
    _r = _prog.result
    sys.stderr.write(f"test_importer_carte : {_r.testsRun} tests, {len(_r.skipped)} sauté(s)\n")
    sys.exit(0 if _r.wasSuccessful() else 1)
