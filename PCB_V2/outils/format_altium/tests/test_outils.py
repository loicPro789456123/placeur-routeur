"""Outils en ligne de commande de format_altium (§0.2-1, §0.2-7, §1.4, §5.2) : aucun fichier lu par défaut,
main(argv) -> int, codes de sortie, messages sans chemin. Cartes synthétiques seulement (tests publics)."""
import contextlib
import io
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
import fabrique  # noqa: E402
from altium_fichier import PcbDoc  # noqa: E402

sys.path.insert(0, commun.RACINE)
import aller_retour  # noqa: E402
import differentiel  # noqa: E402
import generer_format  # noqa: E402
import inventaire  # noqa: E402
import verifier_cartes  # noqa: E402
import fabriquer_essais  # noqa: E402

OUTILS = {"inventaire.py": inventaire, "aller_retour.py": aller_retour, "differentiel.py": differentiel,
          "generer_format.py": generer_format, "verifier_cartes.py": verifier_cartes,
          "fabriquer_essais.py": fabriquer_essais}


def appeler(module, argv):
    """(code, sortie, erreurs) de module.main(argv), dans le processus."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestSansArgument(unittest.TestCase):
    def test_processus_sans_argument(self):
        """Lancés sans argument dans un dossier vide : code 2, message d'usage, aucun fichier lu ni écrit."""
        for script in OUTILS:
            with self.subTest(outil=script), commun.dossier_temporaire() as d:
                r = subprocess.run([sys.executable, "-B", os.path.join(commun.RACINE, script)], cwd=d,
                                   capture_output=True, encoding="utf-8", errors="replace")
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("usage", r.stderr)
                self.assertEqual(os.listdir(d), [])

    def test_main_sans_argument_ne_lit_rien(self):
        for script, module in OUTILS.items():
            with self.subTest(outil=script), \
                    mock.patch.object(PcbDoc, "ouvrir", side_effect=AssertionError("fichier lu")), \
                    mock.patch("builtins.open", side_effect=AssertionError("fichier ouvert")):
                code, out, err = appeler(module, [])
                self.assertEqual(code, 2)
                self.assertIn("usage", err)

    def test_aucun_fichier_par_defaut_dans_le_code(self):
        """Aucune trace des anciens fichiers par défaut dans les outils et les tests."""
        motifs = ["echan" "tillons", "DEF" "AUT"]
        fichiers = [os.path.join(commun.RACINE, f) for f in ("inventaire.py", "aller_retour.py", "verifier_cartes.py",
                                                                 "fabriquer_essais.py")]
        fichiers += [os.path.join(commun.ICI, f) for f in sorted(os.listdir(commun.ICI)) if f.endswith(".py")]
        for f in fichiers:
            with open(f, encoding="utf-8") as fo:
                texte = fo.read()
            for m in motifs:
                self.assertNotIn(m, texte, os.path.basename(f))


class TestOutilsSurCarteSynthetique(unittest.TestCase):
    def test_inventaire(self):
        with commun.dossier_temporaire() as d:
            ch = fabrique.fabriquer(d)
            rapports = os.path.join(d, "rapports")
            code, out, err = appeler(inventaire, [ch, "--rapports", rapports, "--inconnus", os.path.join(d, "INCONNUS.md")])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.isfile(os.path.join(rapports, "inventaire_synthetique.md")))
            self.assertTrue(os.path.isfile(os.path.join(d, "INCONNUS.md")))
            self.assertNotIn(d, out + err)
            code, out, err = appeler(inventaire, [ch])  # sans sortie demandée : rien n'est écrit
            self.assertEqual(code, 0, err)
            self.assertEqual(sorted(os.listdir(d)), ["INCONNUS.md", "rapports", "synthetique.PcbDoc"])

    def test_aller_retour_et_differentiel(self):
        with commun.dossier_temporaire() as d:
            ch = fabrique.fabriquer(d)
            sortie = os.path.join(d, "sortie")
            code, out, err = appeler(aller_retour, [ch, "--sortie", sortie, "--modifications"])
            self.assertEqual(code, 0, out + err)
            self.assertIn("OK ", out)
            relu = os.path.join(sortie, "synthetique_aller_retour.PcbDoc")
            modifie = os.path.join(sortie, "synthetique_modifie.PcbDoc")
            self.assertTrue(os.path.isfile(relu) and os.path.isfile(modifie))
            self.assertNotIn(d, out + err)
            code, out, err = appeler(differentiel, [ch, relu])
            self.assertEqual(code, 0, out + err)
            code, out, err = appeler(differentiel, [ch, modifie])
            self.assertEqual(code, 1, err)
            self.assertIn("Tracks6", out)
            code, out, err = appeler(differentiel, [ch, relu, "--apparier"])
            self.assertEqual(code, 0, out + err)

    def test_generer_format(self):
        with commun.dossier_temporaire() as d:
            sortie = os.path.join(d, "FORMAT.md")
            code, out, err = appeler(generer_format, ["--sortie", sortie])
            self.assertEqual(code, 0, err)
            with open(sortie, encoding="utf-8") as f:
                self.assertIn("Format des fichiers Altium", f.read())
            self.assertEqual(os.listdir(d), ["FORMAT.md"])  # aucun .tmp laissé

    def test_entrees_invalides(self):
        """Fichier introuvable ou qui n'est pas un .PcbDoc : code 2, message avec le nom du fichier seul."""
        with commun.dossier_temporaire() as d:
            faux = os.path.join(d, "faux.PcbDoc")
            with open(faux, "w", encoding="utf-8") as f:
                f.write("texte")
            for module, argv in ((inventaire, [faux]), (inventaire, [os.path.join(d, "absent.PcbDoc")]),
                                 (aller_retour, [faux, "--sortie", os.path.join(d, "s")]),
                                 (differentiel, [faux, faux])):
                with self.subTest(outil=module.__name__, argv=[os.path.basename(a) for a in argv]):
                    code, out, err = appeler(module, argv)
                    self.assertEqual(code, 2, err)
                    self.assertTrue("faux.PcbDoc" in err or "absent.PcbDoc" in err, err)
                    self.assertNotIn(d, out + err)
            code, out, err = appeler(inventaire, ["--option-inconnue", faux])
            self.assertEqual(code, 2)
            self.assertNotIn(d, out + err)

    def test_lanceur_dossier_absent(self):
        import lancer_tests
        with commun.dossier_temporaire() as d:
            code, out, err = appeler(lancer_tests, ["--cartes-locales", os.path.join(d, "inexistant")])
            self.assertEqual(code, 2)
            self.assertIn("inexistant", err)
            self.assertNotIn(d, err)

    def test_aide(self):
        code, out, err = appeler(inventaire, ["--aide"])
        self.assertEqual(code, 0)
        self.assertIn("usage :", out)


if __name__ == "__main__":
    unittest.main(argv=commun.extraire_options(sys.argv))
