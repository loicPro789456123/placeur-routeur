"""Flux Data vides et stockages absents (§8.2 « Fournit aux autres (B1) », §7.2, §7.5).

Publics : chaque codec de CODECS sur 0 octet ; data() d'un stockage absent ; carte synthétique privée de Arcs6,
Fills6, Regions6, Texts6, Polygons6, Vias6 (stockages absents, puis Data vides) ; fichiers qui ne sont pas des
.PcbDoc (ErreurPcbDoc, nom de fichier seul).
Locaux (--cartes-locales) : rev3 et WiFi privées des mêmes flux ; rev2 (Polygons6 vide) ; SODIMM, WiFi_panel,
Bluetooth_Sentinel lus sans exception.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
import fabrique  # noqa: E402
from altium_fichier import ErreurPcbDoc, PcbDoc, cfb  # noqa: E402
from altium_fichier.flux import CODECS, FluxPrimitives, FluxProps, decoder_flux, flux_vide  # noqa: E402
from altium_fichier.pcbdoc import ACCESSEURS  # noqa: E402

RETIRES = ("Arcs6", "Fills6", "Regions6", "Texts6", "Polygons6", "Vias6")
# accesseurs qui rendent une liste (les autres : dictionnaire, couple, nombre ou None)
LISTES = {"reseaux", "composants", "pastilles", "pistes", "vias", "arcs", "remplissages", "regions", "textes",
          "polygones", "classes", "regles", "empilage", "couches_cuivre", "plans_negatifs", "sous_empilages",
          "regions_carte", "cartes_imbriquees", "contour_carte", "corps_3d", "paires_differentielles"}


def flux_fichier(chemin):
    r = cfb.lire_cfb(chemin)
    return {c: n.donnees for c, n in r.flux()}


class TestCodecsVides(unittest.TestCase):
    def test_data_vide_de_chaque_stockage(self):
        """decoder_flux(st + '/Data', b'') : objet du type du flux plein, de longueur 0, réencodé en b''."""
        self.assertGreaterEqual(len(CODECS), 40)
        for st, (classe, _) in sorted(CODECS.items()):
            with self.subTest(stockage=st):
                for f in (decoder_flux(st + "/Data", b""), flux_vide(st)):
                    self.assertIs(type(f), classe)
                    self.assertEqual(len(f), 0)
                    self.assertEqual(f.encoder(), b"")
                    self.assertEqual(sum(f.couverture().values()), 0)

    def test_stockage_inconnu(self):
        self.assertIsNone(flux_vide("StockageInconnu"))
        self.assertEqual(decoder_flux("StockageInconnu/Data", b"").encoder(), b"")

    def test_data_dun_stockage_absent(self):
        """Carte réduite à Board6 : data() de tout autre stockage de CODECS rend l'objet vide du bon type,
        toujours le même ; rien n'est créé à l'écriture tant qu'il reste vide."""
        complet = fabrique.carte()
        r = cfb.Noeud("Root Entry", cfb.TYPE_RACINE)
        for nom in ("FileHeader", "FileHeaderSix", "Board6"):
            r.enfants[nom] = complet.enfants[nom]
        doc = PcbDoc(r, "reduite.PcbDoc")
        for st, (classe, _) in sorted(CODECS.items()):
            if st == "Board6":
                continue
            with self.subTest(stockage=st):
                self.assertFalse(doc.existe(st))
                f = doc.data(st)
                self.assertIs(type(f), classe)
                self.assertEqual(len(f), 0)
                self.assertEqual(f.encoder(), b"")
                self.assertIs(doc.data(st), f)
        self.assertIsNone(doc.data("StockageInconnu"))
        with commun.dossier_temporaire() as d:
            ch = os.path.join(d, "reduite.PcbDoc")
            doc.enregistrer(ch)
            self.assertEqual(sorted(flux_fichier(ch)), ["Board6/Data", "Board6/Header", "FileHeader", "FileHeaderSix"])
            relu = PcbDoc.ouvrir(ch)
            self.assertEqual(relu.pastilles(), [])
            self.assertEqual(relu.composants(), [])

    def test_stockage_absent_rempli_cree_a_lecriture(self):
        """Un stockage absent qui reçoit un enregistrement est créé (Header = nombre d'enregistrements)."""
        with commun.dossier_temporaire() as d:
            doc = PcbDoc.ouvrir(fabrique.fabriquer(d, absents=("Fills6",)))
            source = PcbDoc.ouvrir(fabrique.fabriquer(d, nom="source.PcbDoc"))
            f = doc.data("Fills6")
            self.assertIsInstance(f, FluxPrimitives)
            f.primitives.append(source.data("Fills6")[0])
            doc.marquer("Fills6")
            ch = os.path.join(d, "rempli.PcbDoc")
            doc.enregistrer(ch)
            flux = flux_fichier(ch)
            self.assertEqual(flux["Fills6/Header"], struct.pack("<I", 1))
            self.assertEqual(len(PcbDoc.ouvrir(ch).remplissages()), 1)


class TestCarteSynthetiqueSansFlux(unittest.TestCase):
    def verifier(self, doc):
        res = commun.appeler_accesseurs(doc)
        for a in LISTES:
            self.assertIsInstance(res[a], list, a)
        for a in ("arcs", "remplissages", "regions", "textes", "polygones", "vias"):
            self.assertEqual(res[a], [], a)
        self.assertEqual(len(res["pastilles"]), 12)
        self.assertEqual(len(res["pistes"]), 4)
        # sans Texts6, chaque désignateur vient de SOURCEDESIGNATOR, sinon __piece_<rang>
        self.assertEqual(res["designateurs"], {0: "U1", 1: "X9", 2: "C7", 3: "__piece_3"})
        self.assertEqual(res["commentaires"], {0: "", 1: "", 2: "", 3: ""})
        plan = res["plans_negatifs"][0]
        self.assertEqual(plan["regions"], [])
        self.assertEqual(len(plan["lignes_partage"]), 1)
        return res

    def test_stockages_absents_puis_vides(self):
        for mode in ("absents", "vides"):
            with self.subTest(mode=mode), commun.dossier_temporaire() as d:
                ch = fabrique.fabriquer(d, **{mode: RETIRES})
                doc = PcbDoc.ouvrir(ch)
                for st in RETIRES:
                    self.assertEqual(doc.existe(st), mode == "vides")
                self.verifier(doc)
                # aller-retour sans modification : mêmes flux, aucun stockage créé
                ch2 = os.path.join(d, "relu.PcbDoc")
                doc.enregistrer(ch2)
                self.assertEqual(flux_fichier(ch), flux_fichier(ch2))
                self.verifier(PcbDoc.ouvrir(ch2))


class TestErreurPcbDoc(unittest.TestCase):
    def verifier_message(self, chemin, e):
        nom = os.path.basename(chemin)
        msg = str(e)
        self.assertIn(nom, msg)
        self.assertNotIn(os.path.dirname(chemin), msg)
        self.assertNotIn(os.sep + nom, msg)
        self.assertNotIn("/" + nom, msg)

    def test_fichier_qui_nest_pas_un_pcbdoc(self):
        with commun.dossier_temporaire() as d:
            ch = os.path.join(d, "pas une carte.PcbDoc")
            with open(ch, "w", encoding="utf-8") as f:
                f.write("ceci n'est pas un fichier composé OLE2\n")
            with self.assertRaises(ErreurPcbDoc) as cm:
                PcbDoc.ouvrir(ch)
            self.verifier_message(ch, cm.exception)

    def test_fichier_introuvable(self):
        with commun.dossier_temporaire() as d:
            ch = os.path.join(d, "absente.PcbDoc")
            with self.assertRaises(ErreurPcbDoc) as cm:
                PcbDoc.ouvrir(ch)
            self.verifier_message(ch, cm.exception)

    def test_conteneur_ole2_sans_board6(self):
        with commun.dossier_temporaire() as d:
            r = cfb.Noeud("Root Entry", cfb.TYPE_RACINE)
            r.ecrire("Autre/Data", b"donnees")
            ch = os.path.join(d, "autre.SchDoc")
            cfb.ecrire_cfb(r, ch)
            with self.assertRaises(ErreurPcbDoc) as cm:
                PcbDoc.ouvrir(ch)
            self.verifier_message(ch, cm.exception)
            r.ecrire("Board6/Data", b"")
            cfb.ecrire_cfb(r, ch)
            with self.assertRaises(ErreurPcbDoc):
                PcbDoc.ouvrir(ch)


class TestCartesLocalesSansFlux(unittest.TestCase):
    """rev3 et WiFi privées de Arcs6, Fills6, Regions6, Texts6, Polygons6, Vias6."""

    def verifier_carte(self, nom):
        ch = commun.carte(nom)
        racine = cfb.lire_cfb(ch)
        n_pastilles = len(commun.doc_partage(nom).pastilles())
        for mode in ("absents", "vides"):
            with self.subTest(carte=nom, mode=mode), commun.dossier_temporaire() as d:
                r = fabrique.retirer_stockages(racine, RETIRES, mode)
                doc = PcbDoc.ouvrir(fabrique.ecrire(r, d, nom))
                res = commun.appeler_accesseurs(doc)
                for a in LISTES:
                    self.assertIsInstance(res[a], list, a)
                for a in ("arcs", "remplissages", "regions", "textes", "polygones", "vias"):
                    self.assertEqual(res[a], [], a)
                self.assertEqual(len(res["pastilles"]), n_pastilles)
                self.assertEqual(len(res["designateurs"]), len(res["composants"]))

    def test_rev3(self):
        self.verifier_carte(commun.REV3)

    def test_wifi(self):
        self.verifier_carte(commun.WIFI)


class TestCartesLocales(unittest.TestCase):
    def test_rev2_polygones_vides(self):
        doc = commun.doc_partage(commun.REV2)
        self.assertIsInstance(doc.data("Polygons6"), FluxProps)
        self.assertEqual(len(doc.data("Polygons6")), 0)
        self.assertEqual(doc.polygones(), [])
        commun.appeler_accesseurs(doc)

    def test_sodimm_panneau_rigide_flexible(self):
        for nom in (commun.SODIMM, commun.WIFI_PANNEAU, commun.BLUETOOTH):
            with self.subTest(carte=nom):
                doc = PcbDoc.ouvrir(commun.carte(nom))
                res = commun.appeler_accesseurs(doc)
                for a in LISTES:
                    self.assertIsInstance(res[a], list, a)

    def test_sodimm_remplissages_vides(self):
        doc = commun.doc_partage(commun.SODIMM)
        self.assertIsInstance(doc.data("Fills6"), FluxPrimitives)
        self.assertEqual(doc.remplissages(), [])

    def test_panneau_sans_composant_ni_reseau(self):
        doc = commun.doc_partage(commun.WIFI_PANNEAU)
        self.assertEqual(doc.composants(), [])
        self.assertEqual(doc.reseaux(), [])
        self.assertEqual(doc.designateurs(), {})


if __name__ == "__main__":
    unittest.main(argv=commun.extraire_options(sys.argv))
