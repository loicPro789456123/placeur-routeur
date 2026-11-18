"""Tests de ``comparer_validite.py`` (lot E) par doublures injectées, sans les lots A et C.

    cd placeur_routage && python -m unittest discover -s corpus -p "test_*.py" -v
"""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

import comparer_validite as cv  # noqa: E402

CAS = os.path.join(ICI, "validite")
BINAIRE = os.path.join(ICI, "validite_rs", "target", "release",
                       "validite_verifier" + (".exe" if os.name == "nt" else ""))


def lire(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def doublure_exacte(fichiers, grille):
    """Implémentation parfaite : rend les attendus du cas."""
    rep = os.path.dirname(fichiers["donnees"])
    s = lire(os.path.join(rep, "attendu_strict.json"))
    return {"strict": s, "altium": lire(os.path.join(rep, "attendu_altium.json")), "placement": s["placement"],
            "cout": lire(os.path.join(rep, "attendu_mesures.json"))}


def doublure_decalee(nom_cas, retouche):
    def calculer(fichiers, grille):
        sorties = copy.deepcopy(doublure_exacte(fichiers, grille))
        if os.path.basename(os.path.dirname(fichiers["donnees"])) == nom_cas:
            retouche(sorties)
        return sorties
    return calculer


def lancer(argv, doublures=None):
    err, out = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        code = cv.main(argv, doublures=doublures)
    return code, out.getvalue(), err.getvalue()


class TestComparer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rapport = os.path.join(self.tmp.name, "r.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fonctions_pures(self):
        self.assertTrue(cv.egal({"a": [1, 2.0000001]}, {"a": [1.0, 2.0]}))
        self.assertFalse(cv.egal(1.0, 1.00001))
        self.assertFalse(cv.egal(True, 1))
        e, n = cv.comparer_sorties("x", {"a": {"cout": {"cout_commun": 1.0}}, "b": {"cout": {"cout_commun": 1.0}}})
        self.assertEqual((e, n), ([], 1))

    def test_doublures_exactes_zero_ecart(self):
        code, out, err = lancer(["--cas", CAS, "--rapport", self.rapport], {"rust": doublure_exacte,
                                                                             "python": doublure_exacte})
        self.assertEqual(code, 0, err)
        r = lire(self.rapport)
        self.assertEqual(r["format"], cv.FORMAT_RAPPORT)
        self.assertEqual(r["nombre_ecarts"], 0)
        self.assertGreaterEqual(r["cas"], 60)
        self.assertEqual(r["comparaisons"], r["cas"] * 4 * 3)   # 3 paires de sources × (2 modes, placement, coût)

    def test_cle_de_violation_decalee(self):
        def retouche(s):
            s["strict"]["violations_par_type"]["isolement"] += 1
        code, out, _ = lancer(["--cas", CAS, "--rapport", self.rapport],
                              {"rust": doublure_decalee("011_isolement_piste_pastille", retouche)})
        self.assertEqual(code, 1)
        r = lire(self.rapport)
        self.assertEqual(r["nombre_ecarts"], 1)
        e = r["ecarts"][0]
        self.assertEqual((e["cas"], e["quoi"], e["champ"]), ("011_isolement_piste_pastille", "verdict strict",
                                                             "violations_par_type"))

    def test_cle_de_placement_decalee(self):
        def retouche(s):
            s["placement"]["violations_par_type"]["obstacle"] -= 1
        code, _, _ = lancer(["--cas", CAS, "--rapport", self.rapport],
                            {"rust": doublure_decalee("052_obstacle", retouche)})
        self.assertEqual(code, 1)
        self.assertEqual([(e["quoi"], e["champ"]) for e in lire(self.rapport)["ecarts"]],
                         [("placement", "violations_par_type")])

    def test_cout_decale_de_1e5(self):
        def retouche(s):
            s["cout"]["cout_commun"] += 1e-5
        code, _, _ = lancer(["--cas", CAS, "--rapport", self.rapport],
                            {"python": doublure_decalee("014_coudes_90", retouche)})
        self.assertEqual(code, 1)
        e = lire(self.rapport)["ecarts"]
        self.assertEqual([(x["cas"], x["champ"]) for x in e], [("014_coudes_90", "cout_commun")])

    def test_cout_decale_de_1e7_tolere(self):
        def retouche(s):
            s["cout"]["cout_commun"] += 1e-7
        code, _, _ = lancer(["--cas", CAS], {"python": doublure_decalee("014_coudes_90", retouche)})
        self.assertEqual(code, 0)

    def test_modules_9b5b5a3_sans_verifier(self):
        """Modules Python du commit 9b5b5a3 (``drc_maison`` sans ``verifier``, pas de ``mesures``) : code 2."""
        mods = os.path.join(self.tmp.name, "mods")
        os.makedirs(mods)
        racine = subprocess.run(["git", "-C", ICI, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        for nom in ("drc_maison", "verifier_placement"):
            p = subprocess.run(["git", "-C", ICI, "show", f"9b5b5a3:PCB_V2/outils/placeur_routage/{nom}.py"],
                               capture_output=True) if racine.returncode == 0 else None
            texte = p.stdout if p is not None and p.returncode == 0 else b"def main():\n    pass\n"
            with open(os.path.join(mods, nom + ".py"), "wb") as f:
                f.write(texte)
        code, _, err = lancer(["--cas", CAS, "--modules-python", mods])
        self.assertEqual(code, 2)
        self.assertIn("drc_maison.py n'offre pas verifier()", err)
        self.assertNotIn(self.tmp.name, err)

    def test_entrees_invalides(self):
        for argv in (["--cas", os.path.join(self.tmp.name, "absent"), "--moteur", BINAIRE],
                     ["--cas", CAS, "--moteur", os.path.join(self.tmp.name, "absent.exe")],
                     ["--cas", CAS],
                     ["--graine", CAS, "--moteur", BINAIRE],
                     ["--cas", CAS, "--option-inconnue"]):
            with self.subTest(argv=argv[2:]):
                code, _, err = lancer(argv)
                self.assertEqual(code, 2)
                self.assertNotIn(self.tmp.name, err)
                self.assertNotIn(ICI, err)

    def test_echec_d_une_implementation_est_un_ecart(self):
        def casse(fichiers, grille):
            raise RuntimeError("échec simulé")
        code, _, _ = lancer(["--cas", CAS, "--rapport", self.rapport], {"rust": casse})
        self.assertEqual(code, 1)
        self.assertTrue(all(e["quoi"] == "execution" for e in lire(self.rapport)["ecarts"]))

    @unittest.skipUnless(os.path.isfile(BINAIRE), "binaire d'essai non construit (cargo build --release --offline)")
    def test_binaire_d_essai_zero_ecart(self):
        code, out, err = lancer(["--cas", CAS, "--moteur", BINAIRE, "--cout-rust", BINAIRE,
                                 "--rapport", self.rapport])
        self.assertEqual(code, 0, out + err)
        r = lire(self.rapport)
        self.assertEqual(r["nombre_ecarts"], 0)
        self.assertEqual(r["comparaisons"], r["cas"] * 4)

    @unittest.skipUnless(os.path.isfile(BINAIRE), "binaire d'essai non construit (cargo build --release --offline)")
    def test_binaire_un_seul_mode(self):
        code, out, err = lancer(["--cas", CAS, "--moteur", BINAIRE, "--mode", "altium", "--rapport", self.rapport])
        self.assertEqual(code, 0, out + err)
        r = lire(self.rapport)
        self.assertEqual(r["comparaisons"], r["cas"] * 2)   # verdict altium et placement

    @unittest.skipUnless(os.path.isfile(BINAIRE), "binaire d'essai non construit (cargo build --release --offline)")
    def test_graine_deux_implementations(self):
        graine = os.path.join(self.tmp.name, "graine_1")
        os.makedirs(graine)
        rep = os.path.join(CAS, "047_amorce_coupee")
        for f in ("placements_routage.json", "routage_detaille.json"):
            with open(os.path.join(rep, f), "rb") as a, open(os.path.join(graine, f), "wb") as b:
                b.write(a.read())
        code, out, err = lancer(["--graine", graine, "--donnees", os.path.join(rep, "donnees_placeur.json"),
                                 "--moteur", BINAIRE, "--rapport", self.rapport], None)
        self.assertEqual(code, 2)   # une seule implémentation : rien à comparer sans attendu
        code, out, err = lancer(["--graine", graine, "--donnees", os.path.join(rep, "donnees_placeur.json"),
                                 "--rapport", self.rapport], {"rust": cv.source_moteur(BINAIRE),
                                                              "python": doublure_exacte_graine(rep)})
        self.assertEqual(code, 0, out + err)


def doublure_exacte_graine(rep):
    def calculer(fichiers, grille):
        s = lire(os.path.join(rep, "attendu_strict.json"))
        return {"strict": s, "altium": lire(os.path.join(rep, "attendu_altium.json")), "placement": s["placement"]}
    return calculer


if __name__ == "__main__":
    unittest.main()
