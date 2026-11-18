"""Import d'une carte (Altium ``.PcbDoc`` ou KiCad ``.kicad_pcb``) vers ``entree_carte.json`` : aiguillage.

usage : python importer_carte.py CARTE --entree E.json

- ``importer(chemin) -> dict`` : ``entree_carte/2`` (§2.2) : ``import_altium.py`` (.PcbDoc, sur ``format_altium``)
  ou ``import_kicad.py`` (.kicad_pcb, annexe D).
- ``repere(doc) -> (x0, y0)`` : ``origine_fichier_mil`` d'un ``PcbDoc`` ouvert (compatibilité, lot C).
- ``main(argv) -> int`` : codes de §1.4 : 0 succès ; 2 entrée invalide (fichier absent, extension inconnue,
  fichier refusé, option) ; 4 erreur interne. Messages en français sur la sortie d'erreur, sans trace et sans
  chemin : seuls les noms de fichiers sont cités.
- ``ecrire_json(chemin, objet)`` : UTF-8 sans BOM, fins de ligne LF, ``ensure_ascii=False``, écriture atomique
  (fichier ``.tmp`` voisin puis ``os.replace``, 5 essais).

Aucune variable d'environnement n'est lue.
"""
from __future__ import annotations

import json
import os
import sys
import time

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(ICI), "format_altium"))
sys.path.insert(0, ICI)

import import_kicad  # noqa: E402
from import_geometrie import ErreurEntree  # noqa: E402

MM = 0.0254


def repere(doc):
    """Origine (mil, repère du fichier) du repère commun : ``source.origine_fichier_mil`` de l'importeur Altium
    (coin bas-gauche de la boîte du contour discrétisé). Gardée pour la compatibilité (lot C, generer_pcbdoc)."""
    import import_altium  # noqa: PLC0415
    return import_altium.origine_fichier_mil(doc)


# =============================================================================
# Aiguillage, écriture, ligne de commande
# =============================================================================
USAGE = "usage : importer_carte.py CARTE --entree E.json"
EXTENSIONS = (".pcbdoc", ".kicad_pcb")


def importer(chemin):
    """Entrée de la carte ``chemin`` (.PcbDoc ou .kicad_pcb) ; ``ErreurEntree`` si l'entrée est invalide."""
    nom = os.path.basename(chemin)
    ext = os.path.splitext(nom)[1].lower()
    if ext not in EXTENSIONS:
        raise ErreurEntree(f"{nom} : extension inconnue (attendu .PcbDoc ou .kicad_pcb)")
    if not os.path.isfile(chemin):
        raise ErreurEntree(f"{nom} : fichier introuvable")
    if ext == ".kicad_pcb":
        return import_kicad.importer(chemin)
    import import_altium  # noqa: PLC0415
    try:
        return import_altium.importer(chemin)
    except OSError:
        raise ErreurEntree(f"{nom} : lecture impossible") from None


def ecrire_json(chemin, objet):
    """Écrit ``objet`` en JSON (UTF-8 sans BOM, LF, ``ensure_ascii=False``) par un fichier ``.tmp`` remplacé
    atomiquement ; ``ErreurEntree`` si l'écriture est impossible."""
    donnees = (json.dumps(objet, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")
    tmp = chemin + ".tmp"
    nom = os.path.basename(chemin)
    try:
        with open(tmp, "wb") as f:
            f.write(donnees)
        for essai in range(5):
            try:
                os.replace(tmp, chemin)
                return
            except PermissionError:
                if essai == 4:
                    raise
                time.sleep(0.05 * (essai + 1))
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ErreurEntree(f"{nom} : écriture impossible") from None


def _arguments(argv):
    carte = entree = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--aide", "--help"):
            return None, None
        if a == "--entree":
            if i + 1 >= len(argv):
                raise ErreurEntree("option --entree sans valeur")
            entree = argv[i + 1]
            i += 2
            continue
        if a.startswith("--entree="):
            entree = a.split("=", 1)[1]
        elif a.startswith("-"):
            raise ErreurEntree(f"option inconnue : {a.split('=', 1)[0]}")
        elif carte is None:
            carte = a
        else:
            raise ErreurEntree("une seule carte attendue")
        i += 1
    if carte is None:
        raise ErreurEntree("carte manquante")
    if not entree:
        raise ErreurEntree("option --entree manquante")
    return carte, entree


def _dire(texte, flux=None):
    """Écrit une ligne sur ``flux`` (sortie d'erreur par défaut) sans échouer sur l'encodage de la console."""
    flux = sys.stderr if flux is None else flux
    enc = getattr(flux, "encoding", None) or "utf-8"
    flux.write(texte.encode(enc, "replace").decode(enc, "replace") + "\n")
    flux.flush()


def main(argv=None):
    """``CARTE --entree E.json`` ; rend le code de sortie (§1.4) sans quitter le processus."""
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        carte, entree = _arguments(argv)
    except ErreurEntree as e:
        _dire(f"importer_carte : {e}")
        _dire(USAGE)
        return 2
    if carte is None:
        _dire(USAGE, sys.stdout)
        return 0
    try:
        e = importer(carte)
        ecrire_json(entree, e)
    except ErreurEntree as ex:
        _dire(f"importer_carte : {ex}")
        return 2
    except Exception as ex:  # erreur interne : ni trace ni message système (qui peut contenir un chemin)
        _dire(f"importer_carte : erreur interne ({type(ex).__name__})")
        return 4
    nom = e["source"]["fichier"] if isinstance(e.get("source"), dict) else e.get("source", "")
    _dire(f"{nom} : {len(e['composants'])} composants, {len(e['reseaux'])} réseaux -> {os.path.basename(entree)}",
          sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
