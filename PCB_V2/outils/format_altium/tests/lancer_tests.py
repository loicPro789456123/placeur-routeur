"""Lanceur des tests de format_altium.

    python -B tests/lancer_tests.py [--cartes-locales DOSSIER ...] [-v] [-k MOTIF ...] [--fichiers MOTIF]

Découvre les tests du dossier (test_*.py), les lance, affiche chaque test sauté avec son motif, puis, en
dernière ligne, le nombre de tests, d'échecs, d'erreurs et de tests sautés. Les dossiers de cartes locales
sont passés par option (jamais écrits dans un fichier suivi) ; sans eux, les tests locaux sont sautés.
Codes de sortie : 0 tous les tests réussis ; 1 au moins un échec ou une erreur ; 2 option invalide.
"""
import os
import sys
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

import commun  # noqa: E402
from altium_fichier import commandes as K  # noqa: E402


def parseur():
    p = K.Parseur(prog="lancer_tests.py", description="Tests de format_altium (cartes synthétiques et locales).")
    p.add_argument("--cartes-locales", action="append", default=[], metavar="DOSSIER",
                   help="dossier de cartes .PcbDoc locales (répétable ; cartes cherchées par leur nom)")
    p.add_argument("-v", "--verbeux", action="store_true", help="un test par ligne")
    p.add_argument("-k", dest="motifs", action="append", default=[], metavar="MOTIF",
                   help="ne lancer que les tests dont le nom contient MOTIF (répétable)")
    p.add_argument("--fichiers", default="test_*.py", metavar="MOTIF", help="fichiers de test (défaut : test_*.py)")
    return p


def _executer(a):
    for d in a.cartes_locales:
        if not os.path.isdir(d):
            raise K.ErreurEntree(f"{K.nom_seul(os.path.normpath(d))} : dossier introuvable")
    commun.configurer(a.cartes_locales)
    chargeur = unittest.TestLoader()
    if a.motifs:
        chargeur.testNamePatterns = [f"*{m}*" for m in a.motifs]
    suite = chargeur.discover(ICI, pattern=a.fichiers, top_level_dir=ICI)
    res = unittest.TextTestRunner(verbosity=2 if a.verbeux else 1).run(suite)
    sys.stderr.flush()
    for test, motif in res.skipped:
        print(f"sauté : {test.id()} : {motif}")
    print(f"{res.testsRun} tests, {len(res.failures)} échecs, {len(res.errors)} erreurs, "
          f"{len(res.skipped)} sautés", flush=True)
    return K.CODE_OK if res.wasSuccessful() else K.CODE_NEGATIF


def main(argv=None) -> int:
    return K.executer(_executer, parseur(), argv)


if __name__ == "__main__":
    sys.exit(main())
