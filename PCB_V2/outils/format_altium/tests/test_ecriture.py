"""Écriture : ajouts de pistes, vias et régions sur n'importe quelle carte (prototypes construits depuis
gabarits.py, jamais copiés de la carte), PrimitiveGuids et DrillManager tenus à jour, écriture atomique,
déterminisme, repère local des pièces (§0.4) et performances."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
import fabrique  # noqa: E402
import olefile  # noqa: E402
from altium_fichier import ErreurPcbDoc, PcbDoc, pose  # noqa: E402
from altium_fichier import gabarits as G  # noqa: E402
from altium_fichier.proprietes import valeur_mil  # noqa: E402

AUCUN = 0xFFFF
SANS_CUIVRE_LIBRE = ("Tracks6", "Vias6", "Regions6", "ShapeBasedRegions6")
TROUS = (12.0, 15.748)
ZONE = [(2300.5, 1100.25), (2600.0, 1100.25), (2600.0, 1400.1234), (2300.5, 1400.1234)]
TROU_ZONE = [(2400.0, 1200.0), (2500.0, 1200.0), (2500.0, 1300.0)]
TRIANGLE = [(1100.0, 1700.0), (1300.0, 1700.0), (1200.0, 1900.0)]


def plan_ajouts(reseaux, couches=("TOP", "BOTTOM"), couche_region2="MID1"):
    """Ajouts du critère : 20 pistes sur deux couches, 10 vias de deux perçages, 2 régions dont une à trou."""
    r = [reseaux[0], reseaux[1 % len(reseaux)], None]
    pistes = [(couches[k % 2], (1000.0 + 10.1234 * k, 1100.0 + k), (1200.0 + k, 1150.5 - k), 6.0 + 0.5 * k, r[k % 3])
              for k in range(20)]
    vias = [((1500.0 + 25.0 * k, 1700.0 - 0.0001 * k), 24.0 if k % 2 == 0 else 31.496, TROUS[k % 2], r[k % 3])
            for k in range(10)]
    regions = [("TOP", ZONE, r[0], [TROU_ZONE]), (couche_region2, TRIANGLE, None, [])]
    return pistes, vias, regions


def appliquer(doc, plan):
    pistes, vias, regions = plan
    for c, a, b, l, n in pistes:
        doc.ajouter_piste(c, a, b, l, reseau=n)
    for p, d, t, n in vias:
        doc.ajouter_via(p, d, t, reseau=n)
    for c, contour, n, trous in regions:
        doc.ajouter_region(c, contour, n, trous=trous)


def arrondi(v):
    return round(v * 1e4) / 1e4


class Controles:
    """Contrôles communs après ajouts, écriture et relecture (`n0` : comptes avant les ajouts)."""

    def controler(self, chemin, plan, n0):
        pistes, vias, regions = plan
        relu = PcbDoc.ouvrir(chemin)
        ole = olefile.OleFileIO(chemin)
        try:
            for f in ole.listdir():
                ole.openstream(f).read()
        finally:
            ole.close()
        lp, lv, lr = relu.pistes(), relu.vias(), relu.regions()
        self.assertEqual(len(lp), n0["Tracks6"] + len(pistes))
        self.assertEqual(len(lv), n0["Vias6"] + len(vias))
        self.assertEqual(len(relu.data("Regions6")), n0["Regions6"] + len(regions))
        self.assertEqual(len(relu.data("Regions6")), len(relu.data("ShapeBasedRegions6")))
        for st in SANS_CUIVRE_LIBRE + ("PrimitiveGuids",):
            if relu.existe(st):
                self.assertEqual(relu.flux[st + "/Header"].valeur, len(relu.data(st)), st)
        for (c, a, b, l, n), p in zip(pistes, lp[n0["Tracks6"]:]):
            self.assertEqual((p["couche"], p["reseau"], p["composant"], p["polygone"]), (c, n, AUCUN, AUCUN))
            self.assertEqual((p["x1"], p["y1"], p["x2"], p["y2"], p["largeur"]),
                             tuple(map(arrondi, (a[0], a[1], b[0], b[1], l))))
            self.assertFalse(p["verrouille"] or p["keepout"])
        for (pos, d, t, n), v in zip(vias, lv[n0["Vias6"]:]):
            self.assertEqual((v["x"], v["y"], v["diametre"], v["trou"], v["reseau"], v["de"], v["a"]),
                             (arrondi(pos[0]), arrondi(pos[1]), arrondi(d), arrondi(t), n, "TOP", "BOTTOM"))
        for (c, contour, n, trous), r in zip(regions, lr[len(lr) - len(regions):]):
            self.assertEqual((r["couche"], r["reseau"], r["genre"], r["keepout"], r["decoupe_carte"]),
                             (c, n, 0, False, False))
            self.assertEqual(r["contour"], [(arrondi(x), arrondi(y)) for x, y in contour])
            self.assertEqual(r["trous"], [[(arrondi(x), arrondi(y)) for x, y in t] for t in trous])
        sb = relu.data("ShapeBasedRegions6")
        for k in range(len(regions)):
            s = sb[len(sb) - len(regions) + k].sous[0]
            self.assertEqual([(x, y) for _, x, y, *_ in s.contour[:-1]],
                             [(round(x * 1e4), round(y * 1e4)) for x, y in regions[k][1]])
        self.assertEqual(pose.verifier_guids(relu), [])
        if relu.existe("DrillManager"):
            dm = relu.data("DrillManager")
            rattache = {}
            for p, l in dm.groupes:
                for i in l[1]:
                    self.assertEqual(p.get("OBJECTID"), "Via")
                    rattache[i] = valeur_mil(p.get("TOOLSIZE"))
            self.assertEqual(sorted(rattache), list(range(len(lv))))
            for i, v in enumerate(lv):
                self.assertAlmostEqual(rattache[i], v["trou"], delta=1e-3)
        return relu


def comptes(doc):
    return {st: len(doc.data(st)) for st in SANS_CUIVRE_LIBRE}


class TestEcritureSynthetique(Controles, unittest.TestCase):
    def cas(self):
        for mode in ("vides", "absents"):
            for annexes in (False, True):
                yield mode, annexes

    def fabriquer(self, d, mode, annexes, nom="carte.PcbDoc"):
        options = {mode: SANS_CUIVRE_LIBRE, "guids": annexes, "percage": annexes}
        return fabrique.fabriquer(d, nom=nom, **options)

    def test_cartes_sans_piste_ni_via(self):
        plan = plan_ajouts(fabrique.RESEAUX)
        for mode, annexes in self.cas():
            with self.subTest(stockages=mode, guids_et_percage=annexes), commun.dossier_temporaire() as d:
                doc = PcbDoc.ouvrir(self.fabriquer(d, mode, annexes))
                self.assertEqual(doc.existe("PrimitiveGuids"), annexes)
                self.assertEqual(doc.existe("Tracks6"), mode == "vides")
                n0 = comptes(doc)
                self.assertEqual(n0, {st: 0 for st in SANS_CUIVRE_LIBRE})
                appliquer(doc, plan)
                sortie = os.path.join(d, "sortie.PcbDoc")
                doc.enregistrer(sortie)
                relu = self.controler(sortie, plan, n0)
                for st in SANS_CUIVRE_LIBRE:
                    self.assertTrue(relu.existe(st), st)
                if annexes:
                    vias = [p for p, l in relu.data("DrillManager").groupes if p.get("OBJECTID") == "Via"]
                    self.assertEqual(sorted(valeur_mil(p.get("TOOLSIZE")) for p in vias), list(TROUS))
                    self.assertEqual(relu.flux["DrillManager/Header"].valeur, 1)
                self.assertEqual(sorted(os.listdir(d)), ["carte.PcbDoc", "sortie.PcbDoc"])  # aucun .tmp laissé

    def test_deux_executions_identiques(self):
        plan = plan_ajouts(fabrique.RESEAUX)
        for mode, annexes in self.cas():
            with self.subTest(stockages=mode, guids_et_percage=annexes), commun.dossier_temporaire() as d:
                sorties = []
                for k in range(2):
                    doc = PcbDoc.ouvrir(self.fabriquer(d, mode, annexes, nom=f"carte{k}.PcbDoc"))
                    appliquer(doc, plan)
                    sorties.append(os.path.join(d, f"sortie{k}.PcbDoc"))
                    doc.enregistrer(sorties[-1])
                with open(sorties[0], "rb") as a, open(sorties[1], "rb") as b:
                    self.assertEqual(a.read(), b.read())

    def test_carte_avec_cuivre_libre(self):
        """Carte synthétique complète (pistes, vias, régions déjà présents) : mêmes contrôles."""
        plan = plan_ajouts(fabrique.RESEAUX)
        with commun.dossier_temporaire() as d:
            doc = PcbDoc.ouvrir(fabrique.fabriquer(d, guids=True, percage=True))
            pose.rattacher_vias_percage(doc)  # vias de la fabrique rattachées (groupe de via créé)
            n0 = comptes(doc)
            appliquer(doc, plan)
            doc.enregistrer(os.path.join(d, "sortie.PcbDoc"))
            self.controler(os.path.join(d, "sortie.PcbDoc"), plan, n0)

    def test_refus(self):
        with commun.dossier_temporaire() as d:
            ch = fabrique.fabriquer(d, empilage="hors_ordre")
            doc = PcbDoc.ouvrir(ch)
            essais = [lambda: doc.ajouter_piste("PLANE1", (0, 0), (1, 1), 5),          # plan absent de l'empilage
                      lambda: doc.ajouter_piste("MidLayer3", (0, 0), (1, 1), 5),
                      lambda: doc.ajouter_piste("COUCHE", (0, 0), (1, 1), 5),
                      lambda: doc.ajouter_piste("TOP", (0, 0), (1, 1), 5, reseau="INCONNU"),
                      lambda: doc.ajouter_piste("TOP", (0, 0), (1e7, 1), 5),
                      lambda: doc.ajouter_piste("TOP", (0, 0), (1, 1), 0),
                      lambda: doc.ajouter_via((0, 0), 10, 12),
                      lambda: doc.ajouter_region("TOP", [(0, 0), (1, 1)], None)]
            for k, essai in enumerate(essais):
                with self.subTest(essai=k), self.assertRaises(ErreurPcbDoc) as e:
                    essai()
                self.assertNotIn(d, str(e.exception))
            self.assertEqual(doc.ajouter_piste("MidLayer2", (0, 0), (1, 1), 5), len(doc.data("Tracks6")) - 1)
            with self.assertRaises(ErreurPcbDoc) as e:
                doc.enregistrer(ch)                                          # fichier ouvert
            self.assertIn("synthetique.PcbDoc", str(e.exception))
            self.assertNotIn(d, str(e.exception))
            self.assertEqual(os.listdir(d), ["synthetique.PcbDoc"])

    def test_cout_constant(self):
        """Le coût d'un ajout ne dépend pas du nombre de primitives (PrimitiveGuids présent)."""
        with commun.dossier_temporaire() as d:
            doc = PcbDoc.ouvrir(fabrique.fabriquer(d, guids=True, percage=True))
            durees = []
            for _ in range(3):
                t = time.perf_counter()
                for k in range(1000):
                    doc.ajouter_piste("TOP", (k, 0), (k, 10), 5, reseau="GND")
                durees.append(time.perf_counter() - t)
            self.assertLess(durees[2], 3 * durees[0] + 0.05, durees)
            self.assertEqual(pose.verifier_guids(doc), [])


class TestPrototypes(unittest.TestCase):
    """Chaque champ des sous-enregistrements neufs vaut la valeur documentée (gabarits.VALEURS_*), le champ
    fixé par l'appel, ou 0 ; longueur = celle des enregistrements existants du même type, sinon le gabarit."""

    def verifier_bloc(self, bloc, valeurs, fixes, longueur):
        self.assertEqual(len(G.encoder_bloc(bloc)), longueur)
        for k, v in bloc.v.items():
            if k in fixes:
                attendu = fixes[k]
            elif k in valeurs:
                attendu = valeurs[k]
            else:
                attendu = b"\0" * len(v) if isinstance(v, bytes) else 0
            self.assertEqual(v, attendu, f"{bloc.nom_gabarit}.{k}")

    def test_prototypes(self):
        for avec in (False, True):
            with self.subTest(enregistrements_existants=avec), commun.dossier_temporaire() as d:
                options = {} if avec else {"absents": SANS_CUIVRE_LIBRE}
                doc = PcbDoc.ouvrir(fabrique.fabriquer(d, **options))
                if avec:  # pistes existantes plus longues (version d'Altium plus récente) : longueur suivie
                    pr = doc.data("Tracks6")[0]
                    pr.sous[0].bloc = G.decoder_bloc(G.PISTE, G.encoder_bloc(pr.bloc) + b"\0" * 4, "piste")
                lp = 53 if avec else G.LONGUEURS_PROTOTYPES["piste"]
                i = doc.ajouter_piste("BOTTOM", (10.5, 20), (30, 40.25), 8, reseau="VCC")
                self.verifier_bloc(doc.data("Tracks6")[i].bloc, G.VALEURS_PISTE,
                                   {"couche": 32, "couche_v7": 0x0100FFFF, "reseau": 1, "x1": 105000, "y1": 200000,
                                    "x2": 300000, "y2": 402500, "largeur": 80000}, lp)
                i = doc.ajouter_via((100, 200), 24, 12, reseau="GND")
                b = doc.data("Vias6")[i].bloc
                self.verifier_bloc(b, G.VALEURS_VIA,
                                   {"x": 1000000, "y": 2000000, "diametre": 240000, "trou": 120000, "reseau": 0,
                                    "diametres_couches": G.coords_vers_octets([240000] * 32),
                                    "guid_gabarit": b["guid_gabarit"]}, G.LONGUEURS_PROTOTYPES["via"])
                self.assertNotEqual(b["guid_gabarit"], "00000000-0000-0000-0000-000000000000")
                i = doc.ajouter_region("TOP", ZONE, "SIG", trous=[TROU_ZONE])
                s = doc.data("Regions6")[i].sous[0]
                self.verifier_bloc(s.entete, G.VALEURS_ENTETE_REGION, {"couche": 1, "reseau": 2, "nb_trous": 1}, 18)
                attendu = [[k, v] for k, v in G.PROPRIETES_REGION]
                attendu[0][1] = "TOP"
                self.assertEqual(s.props.elements, attendu)
                self.assertEqual((s.drapeaux_props, s.reste), (0, b""))
                jum = doc.data("ShapeBasedRegions6")[i].sous[0]
                self.assertEqual((jum.entete.v, jum.props.elements, jum.trous), (s.entete.v, s.props.elements, s.trous))

    def test_groupe_de_via_neuf(self):
        """Groupe de via créé depuis le gabarit (carte sans aucun groupe de via) : propriétés documentées."""
        with commun.dossier_temporaire() as d:
            doc = PcbDoc.ouvrir(fabrique.fabriquer(d, percage=True))
            doc.ajouter_via((100, 200), 23.622, 11.811)
            p, l = doc.data("DrillManager").groupes[-1]
            attendu = dict(G.PROPRIETES_GROUPE_VIA, HOLEMARK="1", TOOLSIZE="11.811mil", TEMPLATENAME="v60h30")
            self.assertEqual(p.dict(), attendu)
            self.assertEqual(l, [[], [len(doc.data("Vias6")) - 1]])
            # rattacher_vias_percage sur une carte sans groupe de via : groupes créés, aucune erreur
            doc2 = PcbDoc.ouvrir(fabrique.fabriquer(d, nom="b.PcbDoc", percage=True))
            pose.rattacher_vias_percage(doc2)
            vias = [(p.get("TOOLSIZE"), l[1]) for p, l in doc2.data("DrillManager").groupes if p.get("OBJECTID") == "Via"]
            self.assertEqual(vias, [("12mil", [0, 1])])


class TestRepereLocal(unittest.TestCase):
    def test_convention(self):
        o = (100.0, 50.0)
        self.assertEqual(pose.local_vers_monde((1, 0), o, 90), (100.0, 51.0))
        self.assertEqual(pose.local_vers_monde((0, 1), o, 0, dessous=True), (100.0, 49.0))
        self.assertEqual(pose.monde_vers_local((100.0, 51.0), o, 90), (1.0, 0.0))
        self.assertEqual(pose.rotation_relative(30, 90), 300.0)
        self.assertEqual(pose.rotation_relative(30, 90, dessous=True), 60.0)
        for r in (0, 30, 90, 247.5):
            for dessous in (False, True):
                self.assertAlmostEqual(pose.rotation_absolue(pose.rotation_relative(r, 45, dessous), 45, dessous), r)

    def test_accord_avec_la_pose(self):
        """monde = origine + R(rotation)·M(face)·local : même convention que pose.Transformation."""
        t = pose.Transformation((0, 0), 0, False, (1e4, 2e4), 30, True)
        for p in ((3.0, 4.0), (-7.5, 2.25)):
            x, y = pose.local_vers_monde(p, (1e4, 2e4), 30, True)
            X, Y = t.point_f(*p)
            self.assertAlmostEqual(x, X, places=6)
            self.assertAlmostEqual(y, Y, places=6)

    def aller_retour(self, doc):
        comp = doc.composants()
        n = 0
        for p in doc.pastilles():
            if p["composant"] == AUCUN:
                continue
            c = comp[p["composant"]]
            o, r, dessous = (c["x_mil"], c["y_mil"]), c["rotation"], c["face"] == "BOTTOM"
            q = pose.local_vers_monde(pose.monde_vers_local((p["x"], p["y"]), o, r, dessous), o, r, dessous)
            self.assertLess(max(abs(q[0] - p["x"]), abs(q[1] - p["y"])) * 0.0254, 1e-3)
            n += 1
        return n

    def test_synthetique(self):
        with commun.dossier_temporaire() as d:
            self.assertEqual(self.aller_retour(PcbDoc.ouvrir(fabrique.fabriquer(d))), 11)

    def test_carte_source_et_rev3(self):
        for nom in (commun.SOURCE, commun.REV3):
            with self.subTest(carte=nom):
                doc = commun.doc_partage(nom)
                faces = {c["face"] for c in doc.composants()}
                self.assertGreater(self.aller_retour(doc), 0)
                if nom == commun.REV3:
                    self.assertEqual(faces, {"TOP", "BOTTOM"})


class TestEcritureLocale(Controles, unittest.TestCase):
    def test_carte_source(self):
        """Carte source : aucune via, aucune piste libre, DrillManager sans groupe de via."""
        doc = PcbDoc.ouvrir(commun.carte(commun.SOURCE))
        self.assertEqual(len(doc.vias()), 0)
        self.assertEqual([p for p in doc.pistes() if p["composant"] == AUCUN and p["couche"] in ("TOP", "BOTTOM")], [])
        plan = plan_ajouts(doc.reseaux(), couche_region2="BOTTOM")
        n0 = comptes(doc)
        appliquer(doc, plan)
        with commun.dossier_temporaire() as d:
            doc.enregistrer(os.path.join(d, "sortie.PcbDoc"))
            self.controler(os.path.join(d, "sortie.PcbDoc"), plan, n0)

    def test_rev3_sans_cuivre_libre(self):
        """rev3 après suppression de toutes ses pistes et vias libres (cas de generer_pcbdoc)."""
        doc = PcbDoc.ouvrir(commun.carte(commun.REV3))
        sup = {"Tracks6": {i for i, p in enumerate(doc.data("Tracks6")) if p.bloc["composant"] == AUCUN},
               "Vias6": {i for i, p in enumerate(doc.data("Vias6")) if p.bloc["composant"] == AUCUN}}
        pose.supprimer_primitives(doc, sup)
        self.assertEqual(len(doc.data("Vias6")), 0)
        plan = plan_ajouts(doc.reseaux())
        n0 = comptes(doc)
        appliquer(doc, plan)
        pose.rattacher_vias_percage(doc)
        with commun.dossier_temporaire() as d:
            doc.enregistrer(os.path.join(d, "sortie.PcbDoc"))
            self.controler(os.path.join(d, "sortie.PcbDoc"), plan, n0)

    def test_performances_rev4(self):
        """3000 ajouter_piste + 500 ajouter_via < 3 s ; enregistrer < 15 s."""
        doc = PcbDoc.ouvrir(commun.carte(commun.REV4))
        reseaux = doc.reseaux()
        t = time.perf_counter()
        for k in range(3000):
            doc.ajouter_piste("TOP" if k % 2 else "BOTTOM", (k * 0.5, 100), (k * 0.5, 200), 6, reseau=reseaux[k % 50])
        for k in range(500):
            doc.ajouter_via((k * 2.0, 300), 24, 12 if k % 2 else 11.811, reseau=reseaux[k % 50])
        duree_ajouts = time.perf_counter() - t
        with commun.dossier_temporaire() as d:
            t = time.perf_counter()
            doc.enregistrer(os.path.join(d, "sortie.PcbDoc"))
            duree_ecriture = time.perf_counter() - t
        print(f"\nrev4 : 3000 pistes + 500 vias {duree_ajouts:.2f} s ; enregistrer {duree_ecriture:.2f} s",
              file=sys.stderr)
        self.assertLess(duree_ajouts, 3.0)
        self.assertLess(duree_ecriture, 15.0)
        self.assertEqual(pose.verifier_guids(doc), [])


if __name__ == "__main__":
    unittest.main(argv=commun.extraire_options(sys.argv))
