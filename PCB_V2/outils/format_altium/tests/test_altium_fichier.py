"""Tests de base de la bibliothèque altium_fichier : propriétés texte, conteneur CFB, aller-retour octet pour
octet des flux, modifications simples, schémas.

    python -B -m unittest discover -s tests                         (public : cartes synthétiques)
    python -B tests/lancer_tests.py --cartes-locales DOSSIER ...     (avec les cartes locales)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
import fabrique  # noqa: E402
import olefile  # noqa: E402

from altium_fichier import PcbDoc  # noqa: E402
from altium_fichier import cfb  # noqa: E402
from altium_fichier.flux import decoder_flux  # noqa: E402
from altium_fichier.proprietes import Proprietes, valeur_mil, formater_mil, codes_vers_texte  # noqa: E402
from altium_fichier.schdoc import SchDoc, PrjPcb  # noqa: E402

CARTES_LOCALES = (commun.METROID, commun.REV2, commun.REV3, commun.REV4, commun.SL1, commun.WIFI,
                  commun.WIFI_PANNEAU, commun.BLUETOOTH, commun.SODIMM, commun.SOURCE)


def flux_ole(chemin):
    o = olefile.OleFileIO(chemin)
    try:
        return {"/".join(e): o.openstream(e).read() for e in o.listdir()}
    finally:
        o.close()


class TestProprietes(unittest.TestCase):
    def test_aller_retour_cas_limites(self):
        for b in (b"|A=1|B=2\0", b"A=1|B=\0", b"|A=1|B|C=x=y|\0", b"", b"|\0", b"|A=\xe9\xff\0", b"|A=1|A=2\0\0"):
            self.assertEqual(Proprietes.depuis_octets(b).vers_octets(), b)

    def test_acces(self):
        p = Proprietes.depuis_octets(b"|X=10mil|%UTF8%NAME=\xc3\xa9t\xc3\xa9|NAME=?t?\0")
        self.assertEqual(valeur_mil(p["X"]), 10.0)
        self.assertEqual(p.texte("NAME"), "été")
        p["X"] = formater_mil(12.5)
        self.assertEqual(p["X"], "12.5mil")
        self.assertEqual(codes_vers_texte("76,50,95,71"), "L2_G")

    def test_unites(self):
        self.assertAlmostEqual(valeur_mil("1mm"), 39.37007874, places=6)
        self.assertEqual(formater_mil(0), "0mil")


class TestCFB(unittest.TestCase):
    def test_ecriture_lecture_synthetique(self):
        r = cfb.Noeud("Root Entry", cfb.TYPE_RACINE)
        r.ecrire("A/Data", b"x" * 5000)        # secteurs normaux
        r.ecrire("A/Header", b"\1\0\0\0")      # mini-stream
        r.ecrire("Vide", b"")
        for i in range(300):                    # beaucoup d'entrées -> arbre RN profond
            r.ecrire(f"S{i}/Data", bytes([i % 256]) * (i * 37))
        with commun.dossier_temporaire() as d:
            ch = os.path.join(d, "t.cfb")
            cfb.ecrire_cfb(r, ch)
            f = flux_ole(ch)
            self.assertEqual(f["A/Data"], b"x" * 5000)
            self.assertEqual(f["A/Header"], b"\1\0\0\0")
            self.assertEqual(f["S299/Data"], bytes([299 % 256]) * (299 * 37))
            self.assertEqual(len(f), 3 + 300)


def verifier_flux(test, chemin):
    """Chaque flux décodé puis ré-encodé redonne ses octets ; la couverture compte tous les octets."""
    r = cfb.lire_cfb(chemin)
    for c, n in r.flux():
        o = decoder_flux(c, n.donnees)
        test.assertEqual(o.encoder(), n.donnees, f"{os.path.basename(chemin)} : {c}")
        test.assertEqual(sum(o.couverture().values()), len(n.donnees), f"couverture {c}")


def verifier_modifications(test, chemin):
    doc = PcbDoc.ouvrir(chemin)
    avec_pastilles = {p["composant"] for p in doc.pastilles()}
    c = min((c for c in doc.composants() if not c["verrouille"] and c["index"] in avec_pastilles), key=lambda c: c["index"])
    idx = c["index"]
    avant = [(pr.bloc.get("x"), pr.bloc.get("y")) for st, pr in doc.primitives_du_composant(idx) if st == "Pads6"]
    nt, nv = len(doc.pistes()), len(doc.vias())
    reseau = doc.reseaux()[0]
    doc.deplacer_composant(c["designateur"], dx_mil=100)
    doc.ajouter_piste("TOP", (300, 300), (500, 300), 10, reseau=reseau)
    doc.ajouter_via((500, 300), 23.622, 11.811, reseau=reseau)
    with commun.dossier_temporaire() as d:
        s = os.path.join(d, "mod.PcbDoc")
        doc.enregistrer(s)
        relu = PcbDoc.ouvrir(s)
        test.assertEqual(len(relu.pistes()), nt + 1)
        test.assertEqual(len(relu.vias()), nv + 1)
        test.assertEqual(relu.flux["Tracks6/Header"].valeur, nt + 1)
        test.assertEqual(relu.flux["Vias6/Header"].valeur, nv + 1)
        p = relu.pistes()[-1]
        test.assertEqual((p["x1"], p["y1"], p["x2"], p["largeur"], p["reseau"], p["couche"]), (300, 300, 500, 10, reseau, "TOP"))
        c2 = relu.composants()[idx]
        test.assertAlmostEqual(c2["x_mil"], c["x_mil"] + 100, places=3)
        apres = [(pr.bloc.get("x"), pr.bloc.get("y")) for st, pr in relu.primitives_du_composant(idx) if st == "Pads6"]
        test.assertEqual(len(apres), len(avant))
        for (xa, ya), (xb, yb) in zip(avant, apres):
            test.assertEqual((xb - xa, yb - ya), (1000000, 0))


class TestPcbDocSynthetique(unittest.TestCase):
    def test_flux_octet_pour_octet(self):
        with commun.dossier_temporaire() as d:
            verifier_flux(self, fabrique.fabriquer(d))

    def test_fichier_reecrit(self):
        with commun.dossier_temporaire() as d:
            ch = fabrique.fabriquer(d)
            s = os.path.join(d, "ar.PcbDoc")
            PcbDoc.ouvrir(ch).enregistrer(s)
            self.assertEqual(flux_ole(ch), flux_ole(s))

    def test_fabrique_deterministe(self):
        with commun.dossier_temporaire() as d:
            a = fabrique.fabriquer(d, nom="a.PcbDoc")
            b = fabrique.fabriquer(d, nom="b.PcbDoc")
            with open(a, "rb") as fa, open(b, "rb") as fb:
                self.assertEqual(fa.read(), fb.read())

    def test_modifications(self):
        with commun.dossier_temporaire() as d:
            verifier_modifications(self, fabrique.fabriquer(d))

    def test_acces_haut_niveau(self):
        with commun.dossier_temporaire() as d:
            doc = PcbDoc.ouvrir(fabrique.fabriquer(d))
        self.assertTrue(doc.composants())
        self.assertTrue(doc.empilage())
        self.assertTrue(doc.contour_carte())
        self.assertTrue(doc.regles())
        self.assertIn("GND", doc.reseaux())


class TestPcbDocLocales(unittest.TestCase):
    def test_flux_octet_pour_octet(self):
        trouvees = [n for n in CARTES_LOCALES if commun.chemin_local(n)]
        if not trouvees:
            self.skipTest("aucune carte locale (option --cartes-locales DOSSIER)")
        for nom in trouvees:
            with self.subTest(carte=nom):
                verifier_flux(self, commun.chemin_local(nom))

    def test_fichier_reecrit_rev4(self):
        ch = commun.carte(commun.REV4)
        with commun.dossier_temporaire() as d:
            s = os.path.join(d, "ar.PcbDoc")
            PcbDoc.ouvrir(ch).enregistrer(s)
            self.assertEqual(flux_ole(ch), flux_ole(s))

    def test_modifications_rev4(self):
        verifier_modifications(self, commun.carte(commun.REV4))


class TestSchema(unittest.TestCase):
    def test_schdoc(self):
        s = SchDoc.ouvrir(commun.carte("Carte Metroid V2.SchDoc"))
        self.assertTrue(s.aller_retour_ok())
        cs = s.composants()
        self.assertTrue(any(c["designateur"] and c["broches"] for c in cs))

    def test_prjpcb(self):
        p = PrjPcb.ouvrir(commun.carte("Carte Metroid V2.PrjPcb"))
        self.assertIn("Carte Metroid V2.SchDoc", p.chemins_documents())


if __name__ == "__main__":
    unittest.main(argv=commun.extraire_options(sys.argv))
