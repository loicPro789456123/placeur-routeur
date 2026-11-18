"""Socle commun des tests de format_altium.

Cartes locales (exemples Altium, carte source du corpus, cartes METROID) : jamais publiées, jamais désignées
par un chemin écrit dans un fichier suivi. Leurs dossiers sont passés par option, répétable :

    python -B tests/lancer_tests.py --cartes-locales DOSSIER [--cartes-locales DOSSIER ...]
    python -B tests/test_lecture.py --cartes-locales DOSSIER          (un seul fichier de test)

Les cartes y sont cherchées par leur nom de fichier (sans sous-dossier). Sans option (CI publique :
`python -B -m unittest discover -s tests`), les tests qui en ont besoin sont sautés avec leur motif ; les autres
reposent sur des cartes synthétiques fabriquées par le test (fabrique.py), dans un dossier temporaire.
"""
import contextlib
import os
import shutil
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
if RACINE not in sys.path:
    sys.path.insert(0, RACINE)
if ICI not in sys.path:
    sys.path.insert(0, ICI)

# noms des cartes locales utilisées par les tests (noms de fichiers seuls)
WIFI = "WiFi.PcbDoc"
WIFI_PANNEAU = "WiFi_panel.PcbDoc"
BLUETOOTH = "Bluetooth_Sentinel.PcbDoc"
SODIMM = "PC4-SODIMM_V200_RC_A1_20141015.PcbDoc"
SL1 = "SL1 Xilinx Spartan-IIE PQ208 Rev1.02.PcbDoc"
SOURCE = "demo.PcbDoc"
METROID = "PCB_METROID_V2.PcbDoc"
REV2 = "PCB_METROID_V2_rev2.PcbDoc"
REV3 = "PCB_METROID_V2_rev3.PcbDoc"
REV4 = "PCB_METROID_V2_rev4.PcbDoc"

DOSSIERS_LOCAUX = []
_CACHE = {}


def configurer(dossiers):
    """Dossiers où chercher les cartes locales (remplace la configuration précédente)."""
    DOSSIERS_LOCAUX[:] = [os.path.abspath(d) for d in dossiers]
    _CACHE.clear()


def extraire_options(argv):
    """Retire `--cartes-locales DOSSIER` (répétable) d'une ligne de commande, configure les dossiers et rend le
    reste (pour `unittest.main(argv=...)`)."""
    reste, dossiers, i = [], [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--cartes-locales" and i + 1 < len(argv):
            dossiers.append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--cartes-locales="):
            dossiers.append(a.split("=", 1)[1])
        else:
            reste.append(a)
        i += 1
    if dossiers:
        configurer(dossiers)
    return reste


def chemin_local(relatif):
    """Premier fichier `relatif` (nom ou chemin relatif) trouvé dans les dossiers locaux, sinon None."""
    for d in DOSSIERS_LOCAUX:
        c = os.path.join(d, relatif)
        if os.path.isfile(c):
            return c
    return None


def carte(nom):
    """Chemin d'une carte locale ; saute le test (motif explicite) si elle n'est pas disponible."""
    c = chemin_local(nom)
    if c is None:
        raise unittest.SkipTest(f"carte locale {nom} absente (option --cartes-locales DOSSIER)")
    return c


def doc_partage(nom):
    """PcbDoc d'une carte locale, ouvert une fois par processus : lecture seule (ne pas le modifier)."""
    from altium_fichier import PcbDoc
    c = carte(nom)
    if c not in _CACHE:
        _CACHE[c] = PcbDoc.ouvrir(c)
    return _CACHE[c]


@contextlib.contextmanager
def dossier_temporaire():
    """Dossier de travail temporaire, supprimé à la sortie."""
    d = tempfile.mkdtemp(prefix="format_altium_")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def appeler_accesseurs(doc):
    """{accesseur: résultat} de tous les accesseurs de lecture (pcbdoc.ACCESSEURS)."""
    from altium_fichier.pcbdoc import ACCESSEURS
    return {a: getattr(doc, a)() for a in ACCESSEURS}
