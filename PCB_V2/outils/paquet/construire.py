# -*- coding: utf-8 -*-
"""Construction du paquet portable PlaceurRouteur (contrat §5.5 ; lot D, É7).

    python construire.py [--sortie DOSSIER] [--cargo-home DIR] [--date-source SECONDES] [--sans-moteurs]
    python construire.py --controler-version [--etiquette vX.Y.Z]

Étapes (chacune s'arrête au premier échec, code ≠ 0) :
1. contrôle de version : VERSION = version des Cargo.toml des deux moteurs (et, avec --etiquette, étiquette =
   « v » + VERSION) ;
2. documents générés (generer_documents.py --avec-pyinstaller) ;
3. moteurs : `cargo build --release --locked` des deux moteurs, avec CARGO_ENCODED_RUSTFLAGS =
   `-C target-feature=+crt-static`, `--remap-path-prefix=<CARGO_HOME>=/cargo`, `--remap-path-prefix=<dépôt>=/src`,
   `-C link-arg=/Brepro` (passé au seul sous-processus cargo) ;
4. PyInstaller sur PlaceurRouteur.spec (le chargeur doit avoir été compilé depuis ses sources à l'installation de
   PyInstaller : voir paquet_windows.yml) ;
5. assemblage : documents et exemples à côté des exécutables ;
6. verifier_paquet.py (a) à (e) ;
7. zip déterministe `PlaceurRouteur-<VERSION>.zip` (entrées triées, date fixée par --date-source, c'est-à-dire
   SOURCE_DATE_EPOCH lu par le flux et passé en option), au plus 40 Mo, et son empreinte SHA-256
   (`PlaceurRouteur-<VERSION>.zip.sha256`).

Aucune variable d'environnement n'est lue (§0.2-2) : tout passe par les options.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

ICI = os.path.dirname(os.path.abspath(__file__))
OUTILS = os.path.dirname(ICI)
DEPOT = os.path.dirname(os.path.dirname(OUTILS))
SOURCES = os.path.join(OUTILS, "placeur_routage")
if SOURCES not in sys.path:
    sys.path.insert(0, SOURCES)

import chemins  # noqa: E402

TAILLE_MAX_ZIP = 40 * 1024 * 1024
MOTEURS = ("placeur_routage", "serigraphie")
# exemples livrés dans exemples\ (chemin relatif à PCB_V2/outils → nom dans le paquet) ; absents : signalés
EXEMPLES = (
    ("placeur_routage/corpus/cartes/deux_couches/deux_couches.kicad_pcb", "kicad_deux_couches/deux_couches.kicad_pcb"),
    ("placeur_routage/corpus/cartes/deux_couches/contraintes.json", "kicad_deux_couches/contraintes.json"),
    ("placeur_routage/corpus/pcbdoc/demonstration.PcbDoc", "demonstration/demonstration.PcbDoc"),
)


def version_cargo(nom: str) -> str:
    with open(os.path.join(OUTILS, nom, "Cargo.toml"), encoding="utf-8") as f:
        texte = f.read()
    m = re.search(r'^\[package\][^\[]*?^version\s*=\s*"([^"]+)"', texte, re.M | re.S)
    return m.group(1) if m else ""


def controler_version(etiquette: str | None, flux=None) -> int:
    """VERSION = Cargo.toml des deux moteurs ; étiquette = v + VERSION (§5.5)."""
    flux = flux or sys.stdout
    version = chemins.lire_version()
    erreurs = []
    for m in MOTEURS:
        v = version_cargo(m)
        if v != version:
            erreurs.append(f"{m}/Cargo.toml porte la version {v!r}, VERSION porte {version!r}")
    if etiquette is not None:
        nom = etiquette.split("/")[-1]
        if nom != "v" + version:
            erreurs.append(f"étiquette {nom!r} différente de 'v{version}'")
    for e in erreurs:
        flux.write("erreur : " + e + "\n")
    flux.write("version cohérente\n" if not erreurs else "version incohérente\n")
    return 1 if erreurs else 0


def drapeaux_rust(cargo_home: str, depot: str) -> str:
    """Valeur de CARGO_ENCODED_RUSTFLAGS (arguments séparés par 0x1F)."""
    return "\x1f".join(["-C", "target-feature=+crt-static", f"--remap-path-prefix={cargo_home}=/cargo",
                        f"--remap-path-prefix={depot}=/src", "-C", "link-arg=/Brepro"])


def construire_moteurs(destination: str, cargo_home: str, travail: str, flux) -> int:
    os.makedirs(destination, exist_ok=True)
    env = chemins.environnement_sous_processus(complet=True)
    env["CARGO_ENCODED_RUSTFLAGS"] = drapeaux_rust(cargo_home, DEPOT)
    for m in MOTEURS:
        cible = os.path.join(travail, "cible_" + m)
        cmd = ["cargo", "build", "--release", "--locked", "--manifest-path", os.path.join(OUTILS, m, "Cargo.toml"),
               "--target-dir", cible]
        flux.write(f"construction du moteur {m}\n")
        r = subprocess.run(cmd, env=env)
        if r.returncode != 0:
            flux.write(f"erreur : cargo build du moteur {m} (code {r.returncode})\n")
            return 1
        shutil.copy2(os.path.join(cible, "release", m + ".exe"), os.path.join(destination, m + ".exe"))
    return 0


def zip_deterministe(dossier: str, zip_chemin: str, date_source: int) -> None:
    date = time.gmtime(max(date_source, 315532800))[:6]
    racine = os.path.basename(os.path.normpath(dossier))
    with zipfile.ZipFile(zip_chemin, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in sorted(chemins_relatifs(dossier)):
            info = zipfile.ZipInfo(f"{racine}/{rel}", date_time=date)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(dossier, rel), "rb") as f:
                z.writestr(info, f.read())


def chemins_relatifs(dossier: str) -> list[str]:
    sortie = []
    for d, _s, noms in os.walk(dossier):
        for n in noms:
            sortie.append(os.path.relpath(os.path.join(d, n), dossier).replace(os.sep, "/"))
    return sortie


def sha256(chemin: str) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def construire(a, flux=None) -> int:
    flux = flux or sys.stdout
    if controler_version(a.etiquette, flux):
        return 1
    version = chemins.lire_version()
    sortie = os.path.abspath(a.sortie)
    travail = os.path.join(sortie, "travail")
    documents = os.path.join(travail, "documents")
    moteurs = os.path.join(travail, "moteurs")
    dist = os.path.join(sortie, "dist")
    for d in (travail, dist):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(travail)
    sys.path.insert(0, ICI)
    import generer_documents
    import verifier_paquet
    if generer_documents.generer(documents, a.registre_cargo, sys.base_prefix, True, flux):
        return 1
    if a.sans_moteurs:
        if not all(os.path.isfile(os.path.join(a.sans_moteurs, m + ".exe")) for m in MOTEURS):
            flux.write("erreur : --sans-moteurs DOSSIER doit contenir les deux moteurs déjà construits\n")
            return 1
        moteurs = os.path.abspath(a.sans_moteurs)
    elif construire_moteurs(moteurs, a.cargo_home, travail, flux):
        return 1
    commit = chemins.commit()
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", dist, "--workpath",
           os.path.join(travail, "pyinstaller"), os.path.join(ICI, "PlaceurRouteur.spec"), "--",
           "--moteurs", moteurs, "--documents", documents, "--commit", commit]
    r = subprocess.run(cmd, env=chemins.environnement_sous_processus(complet=True))
    if r.returncode != 0:
        flux.write(f"erreur : PyInstaller (code {r.returncode})\n")
        return 1
    paquet = os.path.join(dist, "PlaceurRouteur")
    for n in ("MODE_EMPLOI.html", "parametres_modele.ini", "0_LIRE_AVANT.txt", "LICENCES.txt"):
        shutil.copy2(os.path.join(documents, n), os.path.join(paquet, n))
    licence = os.path.join(DEPOT, "LICENCE.txt")
    if os.path.isfile(licence):
        shutil.copy2(licence, os.path.join(paquet, "LICENCE.txt"))
    else:
        flux.write("avertissement : LICENCE.txt absent (licence de l'outil : décision §8.9)\n")
    for source, cible in EXEMPLES:
        s = os.path.join(OUTILS, source)
        if os.path.isfile(s):
            c = os.path.join(paquet, "exemples", cible)
            os.makedirs(os.path.dirname(c), exist_ok=True)
            shutil.copy2(s, c)
        else:
            flux.write(f"avertissement : exemple absent, non livré : {source}\n")
    exemptions = [sha256(os.path.join(paquet, "LICENCES.txt"))]
    if verifier_paquet.verifier(paquet, sys.base_prefix, exemptions, "abcde", flux):
        return 1
    zip_chemin = os.path.join(sortie, f"PlaceurRouteur-{version}.zip")
    zip_deterministe(paquet, zip_chemin, a.date_source)
    taille = os.path.getsize(zip_chemin)
    if taille > TAILLE_MAX_ZIP:
        flux.write(f"erreur : zip de {taille / 1e6:.1f} Mo, plus de 40 Mo\n")
        return 1
    empreinte = sha256(zip_chemin)
    with open(zip_chemin + ".sha256", "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{empreinte}  {os.path.basename(zip_chemin)}\n")
    flux.write(f"paquet construit : {os.path.basename(zip_chemin)}, {taille / 1e6:.1f} Mo, SHA-256 {empreinte}\n")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Construction du paquet portable (§5.5).")
    p.add_argument("--sortie", default=os.path.join(ICI, "construction"), help="dossier de construction")
    p.add_argument("--cargo-home", default=os.path.join(os.path.expanduser("~"), ".cargo"),
                   help="dossier de cargo, retiré des chemins compilés (défaut : ~/.cargo)")
    p.add_argument("--registre-cargo", default=None, help="sources du registre (défaut : CARGO_HOME/registry/src)")
    p.add_argument("--date-source", type=int, default=315532800, help="date des entrées du zip (SOURCE_DATE_EPOCH)")
    p.add_argument("--sans-moteurs", metavar="DOSSIER", help="reprendre des moteurs déjà construits")
    p.add_argument("--etiquette", help="étiquette à contrôler (refs/tags/vX.Y.Z ou vX.Y.Z)")
    p.add_argument("--controler-version", action="store_true", help="seulement le contrôle de version")
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if a.controler_version:
        return controler_version(a.etiquette)
    if a.registre_cargo is None:
        a.registre_cargo = os.path.join(a.cargo_home, "registry", "src")
    return construire(a)


if __name__ == "__main__":
    sys.exit(main())
