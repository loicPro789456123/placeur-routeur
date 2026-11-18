# -*- mode: python ; coding: utf-8 -*-
# Fichier spec PyInstaller du paquet portable PlaceurRouteur (contrat §5.5 ; lot D, É7).
#
# Lancé par construire.py, jamais à la main :
#     pyinstaller --noconfirm --clean --distpath DIST --workpath TRAVAIL PlaceurRouteur.spec -- \
#                 --moteurs DOSSIER_DES_MOTEURS --documents DOSSIER_DES_DOCUMENTS --commit SHA
#
# Une Analysis sur lanceur.py ; deux EXE sur le même _internal (onedir) : PlaceurRouteur.exe (fenêtre,
# console=False) et placeur.exe (console) ; moteurs dans _internal\moteurs ; excludes numpy, matplotlib, scipy,
# PIL ; optimize=0 ; upx=False ; uac_admin=False ; ressources de version lues dans VERSION (source unique).
import argparse
import os

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

_p = argparse.ArgumentParser()
_p.add_argument("--moteurs", required=True)
_p.add_argument("--documents", required=True)
_p.add_argument("--commit", required=True)
_a = _p.parse_args()

PAQUET = os.path.abspath(SPECPATH)                       # noqa: F821 (défini par PyInstaller)
OUTILS = os.path.dirname(PAQUET)
SOURCES = os.path.join(OUTILS, "placeur_routage")
with open(os.path.join(OUTILS, "VERSION"), encoding="utf-8-sig") as _f:
    VERSION = _f.read().strip()
_x, _y, _z = (int(v) for v in VERSION.split("."))

# fichier COMMIT lu par chemins.commit() en mode figé
_travail = os.path.join(os.path.abspath(workpath), "ressources")   # noqa: F821
os.makedirs(_travail, exist_ok=True)
with open(os.path.join(_travail, "COMMIT"), "w", encoding="utf-8", newline="\n") as _f:
    _f.write(_a.commit.strip() + "\n")

OUTILS_AIGUILLES = ("importer_carte", "deduire_config", "construire_donnees", "extraire_reference",
                    "verifier_placement", "drc_maison", "vias_couture", "mesures", "proprete",
                    "mesure_convertisseurs", "visualiser", "generer_pcbdoc", "generer_kicad", "drc_serigraphie",
                    "formats")


def _modules_presents():
    """Modules de la liste blanche --outil présents dans les sources (les autres sont signalés par --auto-test)."""
    presents = []
    for nom in OUTILS_AIGUILLES:
        for base in (SOURCES, os.path.join(OUTILS, "serigraphie")):
            if os.path.isfile(os.path.join(base, nom + ".py")):
                presents.append(nom)
                break
    return presents


def _version(nom_original, description):
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=(_x, _y, _z, 0), prodvers=(_x, _y, _z, 0), mask=0x3F, flags=0x0, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("040C04B0", [
                StringStruct("CompanyName", ""),
                StringStruct("FileDescription", description),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", nom_original[:-4]),
                StringStruct("LegalCopyright", "voir LICENCES.txt"),
                StringStruct("OriginalFilename", nom_original),
                StringStruct("ProductName", "PlaceurRouteur"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
        ])


import sys  # noqa: E402
sys.path.insert(0, os.path.join(OUTILS, "format_altium"))   # collect_submodules importe altium_fichier ici

a = Analysis(                                            # noqa: F821
    [os.path.join(SOURCES, "lanceur.py")],
    pathex=[SOURCES, os.path.join(OUTILS, "serigraphie"), os.path.join(OUTILS, "format_altium"), OUTILS],
    binaries=[(os.path.join(_a.moteurs, "placeur_routage.exe"), "moteurs"),
              (os.path.join(_a.moteurs, "serigraphie.exe"), "moteurs")],
    datas=[(os.path.join(OUTILS, "VERSION"), "."), (os.path.join(_travail, "COMMIT"), ".")],
    hiddenimports=["placeur", "interface", "parametres", "chemins", "olefile", "altium_fichier"]
                  + collect_submodules("altium_fichier")
                  + _modules_presents(),
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "matplotlib", "scipy", "PIL"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)                                        # noqa: F821

exe_fenetre = EXE(                                       # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PlaceurRouteur",
    console=False,
    debug=False,
    strip=False,
    upx=False,
    uac_admin=False,
    version=_version("PlaceurRouteur.exe", "PlaceurRouteur (fenêtre)"),
)
exe_console = EXE(                                       # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="placeur",
    console=True,
    debug=False,
    strip=False,
    upx=False,
    uac_admin=False,
    version=_version("placeur.exe", "PlaceurRouteur (ligne de commande)"),
)
coll = COLLECT(                                          # noqa: F821
    exe_fenetre, exe_console,
    a.binaries, a.datas,
    strip=False,
    upx=False,
    name="PlaceurRouteur",
)
