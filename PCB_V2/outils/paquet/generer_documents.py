# -*- coding: utf-8 -*-
"""Documents générés du paquet portable (contrat §5.5 ; lot D, É7). Bibliothèque standard seulement.

    python generer_documents.py --sortie DOSSIER [--registre-cargo DIR] [--python DIR] [--avec-pyinstaller]

Écrit dans DOSSIER (jamais suivis par git ; la publication exclut les .html) :
- MODE_EMPLOI.html : placeur_routage/MODE_EMPLOI.md converti par le petit convertisseur ci-dessous (titres,
  paragraphes, listes, tableaux, blocs de code, `code`, **gras**, *italique*) ;
- parametres_modele.ini : `parametres.MODELE_INI`, UTF-8 avec BOM, fins de ligne CRLF ;
- 0_LIRE_AVANT.txt : les 5 lignes de démarrage (`LIGNES_LIRE_AVANT`), UTF-8 avec BOM, CRLF ;
- LICENCES.txt : un avis par crate des Cargo.lock des deux moteurs (fichiers LICENSE*, COPYING*, UNLICENSE*,
  NOTICE* lus hors ligne dans le registre cargo local), Python et ses composants (LICENSE.txt de la distribution),
  Tcl/Tk, olefile et, avec --avec-pyinstaller (construction du paquet), PyInstaller.

Code 0 : tout est écrit ; 1 : un avis de licence manque (chaque manque est écrit, LICENCES.txt n'est pas écrit) ;
2 : usage.
"""
from __future__ import annotations

import argparse
import html
import importlib.metadata
import os
import re
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
OUTILS = os.path.dirname(ICI)
SOURCES = os.path.join(OUTILS, "placeur_routage")
if SOURCES not in sys.path:
    sys.path.insert(0, SOURCES)

import chemins  # noqa: E402
import parametres  # noqa: E402

CARGO_LOCKS = ("placeur_routage/Cargo.lock", "serigraphie/Cargo.lock")
FICHIERS_AVIS = re.compile(r"^(LICEN[CS]E|COPYING|UNLICENSE|NOTICE)", re.IGNORECASE)


def lignes_lire_avant(version: str) -> list[str]:
    """Les 5 lignes de démarrage : 0_LIRE_AVANT.txt, notes de version, tête du mode d'emploi en ligne."""
    return [
        f"PlaceurRouteur {version} : rien à installer. Avant tout, clic droit sur le zip, Propriétés, cocher « Débloquer », OK.",
        "Clic droit sur le zip, « Extraire tout… », vers un dossier à vous (par exemple Documents\\PlaceurRouteur).",
        "Le programme ne marche pas lancé depuis l'intérieur du zip : toujours extraire d'abord.",
        "Dans le dossier extrait, double-cliquer sur PlaceurRouteur.exe : la fenêtre s'ouvre.",
        "Mode d'emploi : MODE_EMPLOI.html (blocage par Windows ou l'antivirus : section 7).",
    ]


# ------------------------------------------------------------------------------------------------ Markdown → HTML

def _en_ligne(texte: str) -> str:
    morceaux = re.split(r"(`[^`]+`)", texte)
    sortie = []
    for m in morceaux:
        if len(m) > 1 and m.startswith("`") and m.endswith("`"):
            sortie.append("<code>" + html.escape(m[1:-1]) + "</code>")
            continue
        t = html.escape(m)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
        t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
        sortie.append(t)
    return "".join(sortie)


def _cellules(ligne: str) -> list[str]:
    ligne = ligne.strip()
    if ligne.startswith("|"):
        ligne = ligne[1:]
    if ligne.endswith("|"):
        ligne = ligne[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", ligne)]


def markdown_vers_html(texte: str, titre: str) -> str:
    """Convertisseur Markdown réduit, suffisant pour MODE_EMPLOI.md."""
    lignes = texte.replace("\r\n", "\n").split("\n")
    corps, i, n = [], 0, len(lignes)
    while i < n:
        ligne = lignes[i]
        s = ligne.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            bloc, i = [], i + 1
            while i < n and not lignes[i].strip().startswith("```"):
                bloc.append(lignes[i])
                i += 1
            corps.append("<pre><code>" + html.escape("\n".join(bloc)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            niveau = len(m.group(1))
            ident = re.sub(r"[^\w]+", "-", m.group(2).lower()).strip("-")
            corps.append(f'<h{niveau} id="{html.escape(ident)}">{_en_ligne(m.group(2))}</h{niveau}>')
            i += 1
            continue
        if s.startswith("|") and i + 1 < n and re.match(r"^\s*\|?\s*:?-{2,}", lignes[i + 1]):
            entetes = _cellules(s)
            i += 2
            rangs = []
            while i < n and lignes[i].strip().startswith("|"):
                rangs.append(_cellules(lignes[i]))
                i += 1
            t = ["<table>", "<thead><tr>" + "".join(f"<th>{_en_ligne(c)}</th>" for c in entetes) + "</tr></thead>",
                 "<tbody>"]
            t += ["<tr>" + "".join(f"<td>{_en_ligne(c)}</td>" for c in r) + "</tr>" for r in rangs]
            t.append("</tbody></table>")
            corps.append("\n".join(t))
            continue
        if re.match(r"^([-*]|\d+\.)\s+", s):
            ordonnee = bool(re.match(r"^\d+\.", s))
            elements, i = [], i
            while i < n and lignes[i].strip():
                l2 = lignes[i]
                m2 = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", l2)
                if m2 and (len(l2) - len(l2.lstrip())) < 2:
                    elements.append(m2.group(2))
                elif elements:
                    elements[-1] += " " + l2.strip()
                i += 1
            balise = "ol" if ordonnee else "ul"
            corps.append(f"<{balise}>" + "".join(f"<li>{_en_ligne(e)}</li>" for e in elements) + f"</{balise}>")
            continue
        para = []
        while i < n and lignes[i].strip() and not re.match(r"^(#{1,6}\s|```|\||[-*]\s|\d+\.\s)", lignes[i].strip()):
            para.append(lignes[i].strip())
            i += 1
        if not para:
            para, i = [s], i + 1
        corps.append("<p>" + _en_ligne(" ".join(para)) + "</p>")
    style = ("body{font-family:Segoe UI,Arial,sans-serif;max-width:60em;margin:1em auto;padding:0 1em;"
             "line-height:1.45}table{border-collapse:collapse;margin:.5em 0}th,td{border:1px solid #999;"
             "padding:.25em .5em;vertical-align:top}th{background:#eee}code{background:#f3f3f3;padding:0 .2em}"
             "pre{background:#f3f3f3;padding:.5em;overflow:auto}")
    return ("<!DOCTYPE html>\n<html lang=\"fr\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{html.escape(titre)}</title>\n<style>{style}</style>\n</head>\n<body>\n"
            + "\n".join(corps) + "\n</body>\n</html>\n")


# ------------------------------------------------------------------------------------------------ licences

def crates_des_locks(outils: str = OUTILS) -> list[tuple[str, str]]:
    """(nom, version) des crates du registre dans les Cargo.lock des deux moteurs, sans doublon, triés."""
    vus = set()
    for rel in CARGO_LOCKS:
        with open(os.path.join(outils, rel), encoding="utf-8") as f:
            texte = f.read()
        for bloc in texte.split("[[package]]")[1:]:
            nom = re.search(r'^name = "([^"]+)"', bloc, re.M)
            ver = re.search(r'^version = "([^"]+)"', bloc, re.M)
            src = re.search(r'^source = "([^"]+)"', bloc, re.M)
            if nom and ver and src and src.group(1).startswith("registry+"):
                vus.add((nom.group(1), ver.group(1)))
    return sorted(vus)


def _lire_texte(chemin: str) -> str:
    with open(chemin, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n").strip()


def avis_crate(registre: str, nom: str, version: str) -> list[tuple[str, str]]:
    """Fichiers d'avis de la crate dans le registre cargo local (tous les index), ou []."""
    if not os.path.isdir(registre):
        return []
    for index in sorted(os.listdir(registre)):
        dossier = os.path.join(registre, index, f"{nom}-{version}")
        if os.path.isdir(dossier):
            return [(n, _lire_texte(os.path.join(dossier, n))) for n in sorted(os.listdir(dossier))
                    if FICHIERS_AVIS.match(n) and os.path.isfile(os.path.join(dossier, n))]
    return []


def avis_distribution(nom: str) -> list[tuple[str, str]]:
    """Avis de licence d'un paquet Python installé (fichiers LICENSE*/COPYING* de sa distribution)."""
    try:
        dist = importlib.metadata.distribution(nom)
    except importlib.metadata.PackageNotFoundError:
        return []
    sortie = []
    for f in dist.files or ():
        base = os.path.basename(str(f))
        if FICHIERS_AVIS.match(base) and ".dist-info" in str(f) and base.lower().endswith((".txt", "", ".md")):
            try:
                sortie.append((str(f).replace("\\", "/"), str(f.locate().read_text(encoding="utf-8")).strip()))
            except OSError:
                pass
    return sortie


def construire_licences(registre: str, python: str, avec_pyinstaller: bool, version: str):
    """Texte de LICENCES.txt et liste des avis manquants."""
    manques, parties = [], []
    entete = (f"PlaceurRouteur {version} : avis de licence des composants livrés dans ce paquet.\n"
              "La licence de l'outil lui-même est dans LICENCE.txt (si présent).\n")
    parties.append(entete)
    parties.append("=" * 78 + "\nMoteurs (Rust) : crates des Cargo.lock de placeur_routage et serigraphie\n" + "=" * 78)
    for nom, ver in crates_des_locks():
        avis = avis_crate(registre, nom, ver)
        if not avis:
            manques.append(f"crate {nom} {ver} : aucun fichier d'avis dans le registre cargo local")
            continue
        for fichier, texte in avis:
            parties.append(f"--- crate {nom} {ver} : {fichier} ---\n{texte}\n")
    lic_py = os.path.join(python, "LICENSE.txt")
    if os.path.isfile(lic_py):
        texte = _lire_texte(lic_py)
        parties.append("=" * 78 + "\nPython (et les composants qu'il fournit : OpenSSL, libffi, zlib, Tcl/Tk…)\n"
                       + "=" * 78 + f"\n{texte}\n")
        if "Tcl/Tk" not in texte:
            manques.append("Tcl : avis absent de LICENSE.txt de Python")
    else:
        manques.append("Python : LICENSE.txt absent de la distribution")
    tk = os.path.join(python, "tcl", "tk8.6", "license.terms")
    if os.path.isfile(tk):
        parties.append("=" * 78 + "\nTk 8.6 : license.terms\n" + "=" * 78 + f"\n{_lire_texte(tk)}\n")
    else:
        manques.append("Tk : tcl/tk8.6/license.terms absent de la distribution")
    paquets = ["olefile"] + (["pyinstaller"] if avec_pyinstaller else [])
    for p in paquets:
        avis = avis_distribution(p)
        if not avis:
            manques.append(f"paquet Python {p} : aucun avis de licence trouvé dans sa distribution installée")
        for fichier, texte in avis:
            parties.append("=" * 78 + f"\n{p} : {fichier}\n" + "=" * 78 + f"\n{texte}\n")
    return "\n".join(parties), manques


# ------------------------------------------------------------------------------------------------ principal

def generer(sortie: str, registre: str, python: str, avec_pyinstaller: bool, flux=None) -> int:
    flux = flux or sys.stdout
    version = chemins.lire_version()
    os.makedirs(sortie, exist_ok=True)
    licences, manques = construire_licences(registre, python, avec_pyinstaller, version)
    if manques:
        for m in manques:
            flux.write(f"avis manquant : {m}\n")
        flux.write(f"LICENCES.txt non écrit : {len(manques)} avis manquant(s)\n")
        return 1
    with open(os.path.join(SOURCES, "MODE_EMPLOI.md"), encoding="utf-8-sig") as f:
        md = f.read()
    chemins.ecrire_atomique(os.path.join(sortie, "MODE_EMPLOI.html"),
                            markdown_vers_html(md, f"PlaceurRouteur {version} : mode d'emploi"))
    chemins.ecrire_atomique(os.path.join(sortie, "parametres_modele.ini"), parametres.MODELE_INI, bom=True, crlf=True)
    chemins.ecrire_atomique(os.path.join(sortie, "0_LIRE_AVANT.txt"), "\n".join(lignes_lire_avant(version)) + "\n",
                            bom=True, crlf=True)
    chemins.ecrire_atomique(os.path.join(sortie, "LICENCES.txt"), licences + "\n", bom=True, crlf=True)
    flux.write(f"{len(crates_des_locks())} crates, documents écrits : MODE_EMPLOI.html, parametres_modele.ini, "
               "0_LIRE_AVANT.txt, LICENCES.txt\n")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Documents générés du paquet portable (§5.5).")
    p.add_argument("--sortie", required=True, help="dossier où écrire les documents")
    p.add_argument("--registre-cargo", default=os.path.join(os.path.expanduser("~"), ".cargo", "registry", "src"),
                   help="sources du registre cargo local (défaut : ~/.cargo/registry/src)")
    p.add_argument("--python", default=sys.base_prefix, help="distribution Python embarquée (défaut : celle-ci)")
    p.add_argument("--avec-pyinstaller", action="store_true", help="exiger aussi l'avis de PyInstaller")
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return generer(a.sortie, a.registre_cargo, a.python, a.avec_pyinstaller)


if __name__ == "__main__":
    sys.exit(main())
