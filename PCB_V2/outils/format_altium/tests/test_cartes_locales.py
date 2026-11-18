"""Robustesse sur toutes les cartes (étape BF3) : outil verifier_cartes.py sur des cartes synthétiques (public),
et réencodage octet pour octet de chaque flux de toutes les cartes .PcbDoc trouvées dans les dossiers passés par
--cartes-locales (local)."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
import fabrique  # noqa: E402
from altium_fichier import PcbDoc, cfb  # noqa: E402
from altium_fichier.flux import decoder_flux  # noqa: E402

sys.path.insert(0, commun.RACINE)
import verifier_cartes  # noqa: E402

SANS_CUIVRE_LIBRE = ("Tracks6", "Vias6", "Regions6", "ShapeBasedRegions6")


def appeler(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = verifier_cartes.main(argv)
    return code, out.getvalue(), err.getvalue()


def fabriquer_jeu(dossier):
    """Cartes synthétiques variées : normale, sans cuivre libre (stockages absents, avec PrimitiveGuids et
    DrillManager ; stockages vides), empilage hors ordre, panneau, flux de pistes et d'arcs vides."""
    os.makedirs(dossier, exist_ok=True)
    fabrique.fabriquer(dossier, nom="normale.PcbDoc")
    fabrique.fabriquer(dossier, nom="absents.PcbDoc", absents=SANS_CUIVRE_LIBRE, guids=True, percage=True)
    fabrique.fabriquer(dossier, nom="vides.PcbDoc", vides=SANS_CUIVRE_LIBRE)
    fabrique.fabriquer(dossier, nom="hors_ordre.PcbDoc", empilage="hors_ordre")
    fabrique.fabriquer(dossier, nom="panneau.PcbDoc", panneau=True, vides=("Arcs6",))
    return 5


class TestVerifierCartesSynthetiques(unittest.TestCase):
    def test_cartes_sans_defaut(self):
        with commun.dossier_temporaire() as d:
            cartes = os.path.join(d, "cartes")
            n = fabriquer_jeu(cartes)
            rapport = os.path.join(d, "rapport.json")
            code, out, err = appeler([cartes, "--json", rapport])
            self.assertEqual(code, 0, out + err)
            self.assertIn(f"{n} cartes, {n} sans défaut, 0 avec défauts", out)
            with open(rapport, encoding="utf-8") as f:
                texte = f.read()
            for t in (out, err, texte):
                self.assertNotIn(d, t)
                self.assertNotIn(os.path.dirname(d), t)
            r = json.loads(texte)["cartes"]
            self.assertEqual(sorted(x["carte"] for x in r), sorted(os.listdir(cartes)))
            for x in r:
                self.assertEqual(x["defauts"], [], x["carte"])
                for p in verifier_cartes.PHASES + ("pistes_vias_polygones", "appels_ajout", "ecriture"):
                    self.assertIn(p, x["durees"], (x["carte"], p))
                self.assertEqual(set(x["accesseurs"]), set(verifier_cartes.ACCESSEURS))
            self.assertEqual(sorted(os.listdir(d)), ["cartes", "rapport.json"])  # temporaire supprimé

    def test_exclure_et_travail(self):
        with commun.dossier_temporaire() as d:
            cartes = os.path.join(d, "cartes")
            fabriquer_jeu(cartes)
            travail = os.path.join(d, "travail")
            code, out, err = appeler([cartes, os.path.join(cartes, "normale.PcbDoc"), "--exclure", "PANNEAU.PcbDoc",
                                      "--exclure", "vides.PcbDoc", "--travail", travail])
            self.assertEqual(code, 0, out + err)
            self.assertIn("3 cartes, 3 sans défaut", out)  # doublon compté une fois
            self.assertNotIn("panneau.PcbDoc", out)
            self.assertEqual(sorted(os.listdir(travail)),
                             sorted(f"{b}_{s}.PcbDoc" for b in ("absents", "hors_ordre", "normale")
                                    for s in ("aller_retour", "ajouts")))
            relu = PcbDoc.ouvrir(os.path.join(travail, "absents_ajouts.PcbDoc"))
            self.assertEqual((len(relu.pistes()), len(relu.vias()), len(relu.regions())), (1, 1, 1))

    def test_defauts_signales(self):
        """Une carte illisible et un accesseur en erreur sont des défauts : code 1, nom du fichier seul."""
        with commun.dossier_temporaire() as d:
            cartes = os.path.join(d, "cartes")
            os.makedirs(cartes)
            fabrique.fabriquer(cartes, nom="normale.PcbDoc")
            with open(os.path.join(cartes, "faux.PcbDoc"), "w", encoding="utf-8") as f:
                f.write("texte")
            code, out, err = appeler([cartes])
            self.assertEqual(code, 1, out + err)
            self.assertIn("ÉCHEC faux.PcbDoc", out)
            self.assertIn("ouverture : ErreurPcbDoc : faux.PcbDoc : ce n'est pas un fichier .PcbDoc", out)
            self.assertIn("OK    normale.PcbDoc", out)
            self.assertIn("2 cartes, 1 sans défaut, 1 avec défauts", out)
            self.assertNotIn(d, out + err)
            with mock.patch.object(PcbDoc, "polygones", side_effect=ValueError("panne simulée")):
                code, out, err = appeler([os.path.join(cartes, "normale.PcbDoc")])
            self.assertEqual(code, 1, out + err)
            self.assertIn("accesseur polygones : ValueError : panne simulée", out)

    def test_ecart_de_reencodage_detecte(self):
        with commun.dossier_temporaire() as d:
            ch = fabrique.fabriquer(d)
            vrai = verifier_cartes.PcbDoc.ouvrir

            def ouvrir_abime(chemin):
                doc = vrai(chemin)
                doc.racine.enfants["Board6"].enfants["Data"].donnees += b"\0"
                return doc
            with mock.patch.object(verifier_cartes.PcbDoc, "ouvrir", side_effect=ouvrir_abime):
                code, out, err = appeler([ch])
            self.assertEqual(code, 1, out + err)
            self.assertIn("réencodage : flux Board6/Data différent", out)

    def test_entrees_invalides(self):
        with commun.dossier_temporaire() as d:
            vide = os.path.join(d, "vide")
            os.makedirs(vide)
            for argv, motif in (([os.path.join(d, "absent.PcbDoc")], "absent.PcbDoc : fichier ou dossier introuvable"),
                                ([vide], "aucune carte .PcbDoc"), ([], "usage"), (["--inconnue", vide], "usage")):
                with self.subTest(argv=[os.path.basename(a) for a in argv]):
                    code, out, err = appeler(argv)
                    self.assertEqual(code, 2, out + err)
                    self.assertIn(motif, err)
                    self.assertNotIn(d, out + err)
            fabrique.fabriquer(vide, nom="seule.PcbDoc")
            code, out, err = appeler([vide, "--exclure", "seule.PcbDoc"])
            self.assertEqual(code, 2)
            self.assertIn("aucune carte .PcbDoc", err)


def cartes_locales_trouvees():
    """Toutes les cartes .PcbDoc des dossiers --cartes-locales (sans sous-dossier), sans doublon."""
    vus, out = set(), []
    for dossier in commun.DOSSIERS_LOCAUX:
        for n in sorted(os.listdir(dossier)):
            c = os.path.join(dossier, n)
            if n.lower().endswith(".pcbdoc") and os.path.isfile(c) and os.path.normcase(c) not in vus:
                vus.add(os.path.normcase(c))
                out.append(c)
    return out


class TestReencodageToutesCartesLocales(unittest.TestCase):
    def test_chaque_flux_octet_pour_octet(self):
        """Chaque flux de chaque carte trouvée, décodé puis réencodé, redonne ses octets ; la couverture du
        décodage compte tous les octets."""
        trouvees = cartes_locales_trouvees()
        if not trouvees:
            self.skipTest("aucune carte locale (option --cartes-locales DOSSIER)")
        for chemin in trouvees:
            nom = os.path.basename(chemin)
            with self.subTest(carte=nom):
                r = cfb.lire_cfb(chemin)
                ecarts = []
                for c, n in r.flux():
                    o = decoder_flux(c, n.donnees)
                    if o.encoder() != n.donnees or sum(o.couverture().values()) != len(n.donnees):
                        ecarts.append(c)
                    n.donnees = None  # libère la mémoire au fil de l'eau (grosses cartes)
                self.assertEqual(ecarts, [], nom)


if __name__ == "__main__":
    unittest.main(argv=commun.extraire_options(sys.argv))
