"""Chemins absolus (§0.3) : PcbDoc.effacer_chemins(tout) et PcbDoc.chemins_absolus(), module chemins."""
import base64
import os
import sys
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
import fabrique  # noqa: E402
from altium_fichier import PcbDoc, chemins  # noqa: E402

A = fabrique.absolu
S = fabrique.ANTI
# fragments des chemins fictifs de la fabrique, cherchés dans le fichier écrit (latin-1 et UTF-16)
FRAGMENTS = ("Users" + S + "nom", "bibli" + S, S * 2 + "serveur", "dossier" + S, "projets" + S, "empilages" + S,
             "Users" + S * 2 + "nom")


def champs(bilan):
    return {(b["flux"], b["champ"]) for b in bilan}


class TestModule(unittest.TestCase):
    def test_reduire(self):
        cas = {A("C", "Users", "nom", "a.PcbLib"): "a.PcbLib", A("d", "x", "y", "z.step", sep="/"): "z.step",
               A("", "serveur", "p", "c.PcbDoc"): "c.PcbDoc", "voir " + A("C", "d", "n.txt") + "|suite": "voir n.txt|suite",
               "/home" + "/nom/f.txt": "f.txt",
               "FILE_NAME('" + A("C", "Users", "nom", "m.step", sep=S * 2) + "','x')": "FILE_NAME('m.step','x')",
               "Carte Metroid V2" + S + "ALIM2": "Carte Metroid V2" + S + "ALIM2",
               S + "WCFSFLDU" + S + "FSJVRTAI": S + "WCFSFLDU" + S + "FSJVRTAI",
               "modeles" + S + "essai.step": "modeles" + S + "essai.step", A("C"): ""}
        for avant, apres in cas.items():
            with self.subTest(texte=avant):
                self.assertEqual(chemins.reduire(avant)[0], apres)
                self.assertEqual(chemins.contient(avant), avant != apres)

    def test_valeurs_codees(self):
        codes = ",".join(str(ord(c)) for c in A("C", "Ω", "b.PcbLib"))
        self.assertEqual(chemins.reduire_codes(codes), (",".join(str(ord(c)) for c in "b.PcbLib"), 1))
        v = base64.b64encode(zlib.compress(('<a Chemin="' + A("D", "x", "y.z") + '"/>').encode())).decode()
        nv, n = chemins.reduire_valeur(v)
        self.assertEqual((zlib.decompress(base64.b64decode(nv)), n), (b'<a Chemin="y.z"/>', 1))
        self.assertEqual(chemins.reduire_valeur("eNoDAAAAAAE="), ("eNoDAAAAAAE=", 0))

    def test_octets(self):
        b = b"\x00\x01" + A("E", "p", "q", "r.txt").encode("utf-16-le") + b"\xff" + A("X", "u", "v.w").encode() + b"\x00"
        self.assertEqual(len(chemins.trouver_octets(b)), 2)
        r, n = chemins.reduire_octets(b)
        self.assertEqual((len(r), n, chemins.trouver_octets(r)), (len(b), 2, []))
        self.assertIn("r.txt".encode("utf-16-le"), r)


class TestCarteSynthetique(unittest.TestCase):
    def fichier_sans_chemin(self, ch):
        with open(ch, "rb") as f:
            b = f.read()
        self.assertEqual(chemins.trouver_octets(b), [])
        for frag in FRAGMENTS:
            self.assertNotIn(frag.encode("latin-1"), b)
            self.assertNotIn(frag.encode("utf-16-le"), b)

    def test_tout_faux(self):
        with commun.dossier_temporaire() as d:
            doc = PcbDoc.ouvrir(fabrique.fabriquer(d, chemins=True, panneau=True))
            avant = champs(doc.chemins_absolus())
            self.assertIn(("Board6/Data", "FILENAME"), avant)
            self.assertEqual(doc.effacer_chemins(tout=False), [{"flux": "Board6/Data", "champ": "FILENAME", "nombre": 1}])
            doc.enregistrer(os.path.join(d, "sortie.PcbDoc"))
            relu = PcbDoc.ouvrir(os.path.join(d, "sortie.PcbDoc"))
            self.assertEqual(relu.board.get("FILENAME"), "")
            self.assertEqual(champs(relu.chemins_absolus()), avant - {("Board6/Data", "FILENAME")})
            self.assertEqual(relu.composants()[0]["bibliotheque"], fabrique.CHEMIN_BIBLIOTHEQUE)

    def test_tout_vrai(self):
        with commun.dossier_temporaire() as d:
            doc = PcbDoc.ouvrir(fabrique.fabriquer(d, chemins=True, panneau=True))
            attendus = {("Board6/Data", "FILENAME"), ("Board6/Data", "V9_STACKCUSTOMDATA"),
                        ("Components6/Data", "SOURCEFOOTPRINTLIBRARY"), ("EmbeddedBoards6/Data", "DOCUMENTPATH"),
                        ("WideStrings6/Data", "chaine"), ("Models/0", "STEP"), ("Fichiers/Liste", "octets")}
            self.assertEqual(champs(doc.chemins_absolus()), attendus)
            self.assertEqual(champs(doc.effacer_chemins(tout=True)), attendus)
            self.assertEqual(doc.chemins_absolus(), [])
            ch = os.path.join(d, "sortie.PcbDoc")
            doc.enregistrer(ch)
            relu = PcbDoc.ouvrir(ch)
            self.assertEqual(relu.chemins_absolus(), [])
            self.fichier_sans_chemin(ch)
            self.assertEqual(relu.board.get("FILENAME"), "")
            self.assertEqual(relu.composants()[0]["bibliotheque"], "essai.PcbLib")
            self.assertEqual(relu.cartes_imbriquees()[0]["proprietes"]["DOCUMENTPATH"], "carte.PcbDoc")
            self.assertEqual(relu.data("EmbeddedBoards6")[0].get("DOCUMENTPATH"), "carte.PcbDoc")
            self.assertEqual(relu.chaines_larges()[4], "voir note.txt")
            self.assertIn(b"FILE_NAME('modele.step'", zlib.decompress(relu.flux["Models/0"].encoder()))
            brut = relu.flux["Fichiers/Liste"].encoder()
            self.assertEqual(brut[4:4 + 2 * len(fabrique.CHEMIN_BRUT)].decode("utf-16-le"),
                             "x.SchDoc".ljust(len(fabrique.CHEMIN_BRUT)))
            xml = zlib.decompress(base64.b64decode(relu.board.get("V9_STACKCUSTOMDATA")))
            self.assertIn(b'Chemin="carte.stackup"', xml)
            # le reste de la carte est intact
            self.assertEqual(relu.designateurs(), doc.designateurs())
            self.assertEqual(len(relu.pastilles()), 12)

    def test_carte_sans_chemin(self):
        """Carte sans chemin absolu : seul FILENAME (nom seul) est vidé ; tous les autres flux sont inchangés."""
        with commun.dossier_temporaire() as d:
            doc = PcbDoc.ouvrir(fabrique.fabriquer(d))
            avant = {c: f.encoder() for c, f in doc.flux.items()}
            self.assertEqual(doc.chemins_absolus(), [])
            self.assertEqual(doc.effacer_chemins(tout=True), [{"flux": "Board6/Data", "champ": "FILENAME", "nombre": 1}])
            doc.enregistrer(os.path.join(d, "sortie.PcbDoc"))
            relu = PcbDoc.ouvrir(os.path.join(d, "sortie.PcbDoc"))
            apres = {c: f.encoder() for c, f in relu.flux.items()}
            self.assertEqual(set(apres), set(avant))
            self.assertEqual([c for c in avant if avant[c] != apres[c]], ["Board6/Data"])


class TestCarteSource(unittest.TestCase):
    def test_carte_source(self):
        ch = commun.carte(commun.SOURCE)
        for tout in (False, True):
            with self.subTest(tout=tout), commun.dossier_temporaire() as d:
                doc = PcbDoc.ouvrir(ch)
                avant = champs(doc.chemins_absolus())
                self.assertIn(("Board6/Data", "FILENAME"), avant)
                self.assertIn(("Components6/Data", "SOURCEFOOTPRINTLIBRARY"), avant)
                bilan = doc.effacer_chemins(tout=tout)
                sortie = os.path.join(d, "sortie.PcbDoc")
                doc.enregistrer(sortie)
                relu = PcbDoc.ouvrir(sortie)
                if tout:
                    self.assertEqual(champs(bilan), avant)
                    self.assertEqual(relu.chemins_absolus(), [])
                    with open(sortie, "rb") as f:
                        self.assertEqual(chemins.trouver_octets(f.read()), [])
                else:
                    self.assertEqual(champs(bilan), {("Board6/Data", "FILENAME")})
                    self.assertEqual(champs(relu.chemins_absolus()), avant - {("Board6/Data", "FILENAME")})
                self.assertEqual(relu.designateurs(), doc.designateurs())


if __name__ == "__main__":
    unittest.main(argv=commun.extraire_options(sys.argv))
