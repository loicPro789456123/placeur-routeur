"""Régénère l'exemple METROID V2 (§6.1) à partir des fichiers .PcbDoc de la carte.

Usage :
    python -B regenerer.py --cartes DOSSIER [--verifier]

``DOSSIER`` contient ``PCB_METROID_V2_rev3.PcbDoc`` (carte de l'exemple) et ``PCB_METROID_V2_rev4.PcbDoc``
(référence extraite du fichier). Les fichiers produits sont écrits dans le dossier de ce script :

- ``entree_carte.json``            importer_carte (rev3) ;
- ``config_carte.json``            deduire_config (entrée + contraintes.json) ;
- ``donnees_placeur.json``         construire_donnees (entrée + config + contraintes.json) ;
- ``deduction.json``               rapport de déduction de la même construction ;
- ``donnees_placeur_9b5b5a3.json`` construire_donnees avec contraintes_9b5b5a3.json (données de compatibilité) ;
- ``references/rev4_fichier/``     extraire_reference (rev4).

Tout est calculé dans un dossier temporaire, puis copié. ``--verifier`` compare le résultat aux fichiers présents
(SHA-256) sans rien écrire : code 0 si tout est identique, 1 sinon. Code 2 : carte absente ou étape en échec.
Les contraintes (``contraintes.json``, ``contraintes_9b5b5a3.json``) et les références figées (``references/rev3``,
``rev4``, ``ei_1038``) sont des données d'entrée : ce script ne les modifie pas.
"""

import hashlib
import os
import shutil
import sys
import tempfile

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(ICI)))  # modules de placeur_routage

import construire_donnees  # noqa: E402
import deduire_config  # noqa: E402
import extraire_reference  # noqa: E402
import importer_carte  # noqa: E402

CARTE = "PCB_METROID_V2_rev3.PcbDoc"
CARTE_REV4 = "PCB_METROID_V2_rev4.PcbDoc"
PRODUITS = ("entree_carte.json", "config_carte.json", "donnees_placeur.json", "deduction.json",
            "donnees_placeur_9b5b5a3.json") + tuple("references/rev4_fichier/" + f for f in extraire_reference.FICHIERS)
USAGE = "usage : python regenerer.py --cartes DOSSIER [--verifier]"


def _dire(texte):
    print(texte, file=sys.stderr)


def _arguments(argv):
    a, i = {"cartes": None, "verifier": False}, 0
    while i < len(argv):
        x = argv[i]
        if x in ("-h", "--aide", "--help"):
            return None
        cle, egal, val = x.partition("=")
        if cle == "--cartes":
            if not egal:
                if i + 1 >= len(argv):
                    raise ValueError("option --cartes sans valeur")
                i, val = i + 1, argv[i + 1]
            a["cartes"] = val
        elif x == "--verifier":
            a["verifier"] = True
        else:
            raise ValueError(f"argument inconnu : {cle}")
        i += 1
    if not a["cartes"]:
        raise ValueError("--cartes manquant")
    return a


def _sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def produire(cartes, dossier):
    """Écrit les fichiers de ``PRODUITS`` dans ``dossier`` ; rend 0 ou le premier code d'échec."""
    t = lambda nom: os.path.join(dossier, nom)  # noqa: E731
    k = os.path.join(ICI, "contraintes.json")
    k9 = os.path.join(ICI, "contraintes_9b5b5a3.json")
    tmp4 = os.path.join(dossier, "rev4")
    os.makedirs(tmp4)
    os.makedirs(t("references/rev4_fichier"))
    etapes = [
        ("importer_carte", importer_carte.main, [os.path.join(cartes, CARTE), "--entree", t("entree_carte.json")]),
        ("deduire_config", deduire_config.main,
         [t("entree_carte.json"), "--sortie", t("config_carte.json"), "--contraintes", k]),
        ("construire_donnees", construire_donnees.main,
         [t("entree_carte.json"), "--config", t("config_carte.json"), "--contraintes", k,
          "--sortie", t("donnees_placeur.json"), "--deduction", t("deduction.json")]),
        ("construire_donnees (9b5b5a3)", construire_donnees.main,
         [t("entree_carte.json"), "--config", t("config_carte.json"), "--contraintes", k9,
          "--sortie", t("donnees_placeur_9b5b5a3.json")]),
        ("importer_carte (rev4)", importer_carte.main,
         [os.path.join(cartes, CARTE_REV4), "--entree", os.path.join(tmp4, "entree_carte.json")]),
        ("extraire_reference (rev4)", extraire_reference.main,
         [os.path.join(cartes, CARTE_REV4), "--entree", os.path.join(tmp4, "entree_carte.json"),
          "--sortie", t("references/rev4_fichier")]),
    ]
    for nom, fonction, argv in etapes:
        code = fonction(argv)
        if code != 0:
            _dire(f"regenerer : étape {nom} en échec (code {code})")
            return code
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        a = _arguments(argv)
    except ValueError as e:
        _dire(f"regenerer : {e}")
        _dire(USAGE)
        return 2
    if a is None:
        print(USAGE)
        return 0
    for nom in (CARTE, CARTE_REV4):
        if not os.path.isfile(os.path.join(a["cartes"], nom)):
            _dire(f"regenerer : {nom} absent du dossier --cartes")
            return 2
    with tempfile.TemporaryDirectory(prefix="metroid_v2_") as dossier:
        code = produire(a["cartes"], dossier)
        if code != 0:
            return 2
        differents = []
        for rel in PRODUITS:
            cible = os.path.join(ICI, *rel.split("/"))
            if not os.path.isfile(cible) or _sha256(cible) != _sha256(os.path.join(dossier, *rel.split("/"))):
                differents.append(rel)
        if a["verifier"]:
            for rel in differents:
                _dire(f"regenerer : {rel} diffère du fichier régénéré")
            print(f"vérification : {len(PRODUITS) - len(differents)}/{len(PRODUITS)} fichiers identiques")
            return 1 if differents else 0
        for rel in PRODUITS:
            cible = os.path.join(ICI, *rel.split("/"))
            os.makedirs(os.path.dirname(cible), exist_ok=True)
            shutil.copyfile(os.path.join(dossier, *rel.split("/")), cible)
        print(f"{len(PRODUITS)} fichiers écrits ({len(differents)} modifiés)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
