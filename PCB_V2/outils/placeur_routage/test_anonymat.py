# -*- coding: utf-8 -*-
"""Anonymat des sorties de l'orchestrateur (contrat §0.3 ; plan du lot D, É5, critère 5).

    python -m unittest test_anonymat -v

Aucune occurrence, dans les sorties suivantes, du profil de l'utilisateur, de son nom de compte, du nom de la
machine, d'une lettre de lecteur suivie de « :\\ » (ou « :/ », ou « :\\\\ » échappé en JSON), ni d'un chemin
échappé en JSON :
- toutes les sorties d'une exécution complète (mode --donnees et chaîne depuis une carte) : fichiers du dossier de
  calcul (technique/, rapport.txt, journaux, console.txt, stats.json…), sortie standard et sortie d'erreur ;
- technique/erreur.txt d'un plantage provoqué de l'orchestrateur (message qui porte exprès ces informations) ;
- la sortie de --diagnostic.
Seule exception (§0.3) : l'évènement `debut` sur la sortie standard, qui porte le chemin absolu du dossier de sortie.
Les essais utilisent le faux moteur et les doublures de test_placeur (aucun moteur réel, aucun cargo).
"""
from __future__ import annotations

import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import chemins  # noqa: E402
import test_placeur as tp  # noqa: E402

LECTEUR = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)")
ECHAPPE = re.compile(r"(?:\\\\){1,2}(?:Users|AppData|Temp|home)(?:\\\\)", re.IGNORECASE)


def interdits() -> list[tuple[str, re.Pattern]]:
    """Motifs propres à cette machine (profil, compte, machine) et motifs génériques, construits à l'exécution."""
    motifs = []
    profil = chemins.dossier_connu("profil")
    if profil:
        morceaux = [re.escape(p) for p in re.split(r"[\\/]+", profil) if p]
        motifs.append(("profil", re.compile(r"(?:\\\\|\\|/)+".join(morceaux), re.IGNORECASE)))
    nom = chemins.nom_utilisateur()
    if len(nom) >= 3:
        motifs.append(("nom d'utilisateur", re.compile(r"(?<![\w])" + re.escape(nom) + r"(?![\w])", re.IGNORECASE)))
    for machine in chemins.noms_machine():
        motifs.append(("nom de machine", re.compile(r"(?<![\w])" + re.escape(machine) + r"(?![\w])", re.IGNORECASE)))
    motifs.append(("lettre de lecteur", LECTEUR))
    motifs.append(("chemin échappé en JSON", ECHAPPE))
    motifs.extend(chemins.MOTIFS_ANONYMAT)
    return motifs


class TestAnonymat(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_anonymat_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.parent = os.path.join(self.tmp, "res")
        self.motifs = interdits()

    def problemes(self, texte: str, ou: str) -> list[str]:
        return [f"{ou} : {nom}" for nom, motif in self.motifs if motif.search(texte)]

    def verifier_sorties(self, dossier: str, sortie: str, erreur: str) -> None:
        trouves = []
        n = 0
        for d, _, fichiers in os.walk(dossier):
            for f in fichiers:
                chemin = os.path.join(d, f)
                with open(chemin, "rb") as fl:
                    texte = fl.read().decode("utf-8", errors="replace")
                trouves += self.problemes(texte, os.path.relpath(chemin, dossier))
                n += 1
        self.assertGreater(n, 5)
        lignes = [l for l in sortie.splitlines() if not l.startswith('EVT {"type": "debut"')]
        self.assertTrue(any(l.startswith('EVT {"type": "debut"') for l in sortie.splitlines()))
        trouves += self.problemes("\n".join(lignes), "sortie standard")
        trouves += self.problemes(erreur, "sortie d'erreur")
        self.assertEqual(trouves, [])

    def test_execution_complete_donnees(self):
        donnees = tp.donnees_completes(self.tmp, {"2": {"type": "invalide"}, "3": {"type": "plantage"}})
        with tp.Environnement(["--faux-journal", os.path.join(self.tmp, "vivants.txt")]):
            res = tp.executer_placeur(["--donnees", donnees, "--sortie", self.parent, "--essais", 3,
                                       "--processeurs", 2, "--evenements", "--garder-graines", "toutes"])
        self.assertEqual(res.code, 0, res.erreur)
        self.verifier_sorties(tp.dossiers_calcul(self.parent)[-1], res.sortie, res.erreur)

    def test_execution_complete_carte(self):
        source = os.path.join(ICI, "corpus", "cartes", "deux_couches", "deux_couches.kicad_pcb")
        carte = os.path.join(self.tmp, "deux_couches.kicad_pcb")
        shutil.copyfile(source, carte)
        import placeur
        sortie, erreur = io.StringIO(), io.StringIO()
        with tp.Environnement(["--faux-journal", os.path.join(self.tmp, "vivants.txt")]):
            code = placeur.main([carte, "--sortie", self.parent, "--essais", "2", "--evenements"],
                                controles=tp._doublures()(), outils=tp._doublure_outils()(), flux=sortie,
                                flux_erreur=erreur)
        self.assertIn(code, (0, 1), erreur.getvalue())
        self.verifier_sorties(tp.dossiers_calcul(self.parent)[-1], sortie.getvalue(), erreur.getvalue())

    def test_plantage_provoque(self):
        import placeur
        profil = chemins.dossier_connu("profil") or self.tmp
        machine = (chemins.noms_machine() or ("MACHINE-ESSAI",))[0]

        def planter(calcul):
            raise RuntimeError(f"échec provoqué dans {calcul.sortie} ; profil {profil} ; "
                               f"échappé {calcul.sortie.replace(os.sep, os.sep * 2)} ; compte "
                               f"{chemins.nom_utilisateur()} ; machine {machine} ; fichier {os.path.abspath(__file__)}")
        donnees = tp.donnees_completes(self.tmp, {})
        with tp.Environnement(), mock.patch.object(placeur.Calcul, "choisir", planter):
            res = tp.executer_placeur(["--donnees", donnees, "--sortie", self.parent, "--essais", 1,
                                       "--evenements"])
        self.assertEqual(res.code, 4, res.erreur)
        dossier = tp.dossiers_calcul(self.parent)[-1]
        with open(os.path.join(dossier, "technique", "erreur.txt"), encoding="utf-8") as f:
            texte = f.read()
        self.assertIn("échec provoqué", texte)
        self.assertEqual(self.problemes(texte, "erreur.txt"), [])
        self.verifier_sorties(dossier, res.sortie, res.erreur)

    def test_diagnostic(self):
        res = tp.executer_placeur(["--diagnostic"])
        self.assertEqual(res.code, 0, res.erreur)
        self.assertIn("système", res.sortie)
        self.assertEqual(self.problemes(res.sortie + res.erreur, "--diagnostic"), [])


if __name__ == "__main__":
    unittest.main()
