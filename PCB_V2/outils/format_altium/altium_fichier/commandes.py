"""Socle des outils en ligne de commande de format_altium (inventaire, aller_retour, differentiel,
generer_format...).

- `main(argv) -> int` sans `sys.exit` : `executer(fonction, parseur, argv)` analyse les arguments et traduit
  les erreurs en codes de sortie du contrat (§1.4) : 0 succès ; 1 exécution correcte, verdict négatif ;
  2 entrée invalide (option, fichier introuvable ou qui n'est pas un .PcbDoc) ; 4 erreur interne.
- Messages en français, sans aucun chemin reçu (§0.2-7) : les chemins sont réduits au nom du fichier.
- Écritures atomiques (§0.2-5) : `<nom>.tmp` puis `os.replace`, réessayé 5 fois toutes les 0,2 s sur
  `PermissionError` (antivirus, outil de synchronisation).
Aucun fichier n'est lu ni écrit par défaut : tout fichier vient des arguments.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

CODE_OK, CODE_NEGATIF, CODE_ENTREE, CODE_INTERNE = 0, 1, 2, 4

# motifs de détection des chemins absolus (Windows avec lettre de lecteur, UNC, POSIX) dans un message
_CHEMIN = re.compile(r"[A-Za-z]:[\\/][^\s'\"]*|\\\\[^\s'\"]+|(?<![\w.])/(?:[^\s'\"/]+/)+[^\s'\"]*")

_TRADUCTIONS = [
    (re.compile(r"the following arguments are required: (.*)"), "arguments obligatoires manquants : {}"),
    (re.compile(r"unrecognized arguments: (.*)"), "arguments inconnus : {}"),
    (re.compile(r"argument (\S+): expected one argument"), "l'option {} attend une valeur"),
    (re.compile(r"argument (\S+): invalid (.*)"), "valeur invalide pour {} : {}"),
    (re.compile(r"one of the arguments (.*) is required"), "un des arguments {} est obligatoire"),
]


class ErreurEntree(Exception):
    """Entrée invalide (code 2) ; message sans chemin."""


class _Arret(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def nom_seul(chemin) -> str:
    return re.split(r"[\\/]", str(chemin or ""))[-1]


def masquer_chemins(texte: str) -> str:
    """Remplace tout chemin absolu d'un texte par le nom du fichier qu'il désigne."""
    return _CHEMIN.sub(lambda m: nom_seul(m.group(0).rstrip("\\/")), str(texte))


def message_sans_chemin(e: BaseException) -> str:
    """Description d'une exception sans chemin : type, message (chemins réduits au nom), nom du fichier."""
    if isinstance(e, OSError) and e.filename:
        return f"{type(e).__name__} : {e.strerror or ''} ({nom_seul(e.filename)})".strip()
    m = masquer_chemins(str(e))
    return f"{type(e).__name__} : {m}" if m else type(e).__name__


class Parseur(argparse.ArgumentParser):
    """ArgumentParser qui ne termine jamais le processus (erreur : code 2 rendu par `executer`) et dont les
    messages sont en français et sans chemin."""

    def __init__(self, *a, **k):
        k.setdefault("add_help", False)
        super().__init__(*a, **k)
        self._positionals.title = "arguments"
        self._optionals.title = "options"
        self.add_argument("-h", "--aide", action="help", help="afficher cette aide et quitter")

    def format_usage(self):
        return re.sub(r"^usage:", "usage :", super().format_usage())

    def format_help(self):
        return re.sub(r"^usage:", "usage :", super().format_help())

    def error(self, message):
        for motif, gabarit in _TRADUCTIONS:
            m = motif.fullmatch(message)
            if m:
                message = gabarit.format(*m.groups())
                break
        sys.stderr.write(self.format_usage())
        sys.stderr.write(f"{self.prog} : erreur : {masquer_chemins(message)}\n")
        raise _Arret(CODE_ENTREE)

    def exit(self, status=0, message=None):
        if message:
            sys.stderr.write(masquer_chemins(message))
        raise _Arret(status)


def executer(fonction, parseur: Parseur, argv) -> int:
    """Analyse `argv` (liste ; None = arguments du processus) puis rend `fonction(arguments)` (un code) ;
    ErreurPcbDoc et ErreurEntree -> 2, toute autre exception -> 4, toujours sans trace ni chemin."""
    from .pcbdoc import ErreurPcbDoc
    try:
        args = parseur.parse_args(sys.argv[1:] if argv is None else list(argv))
        return int(fonction(args) or 0)
    except _Arret as a:
        return a.code
    except (ErreurPcbDoc, ErreurEntree) as e:
        sys.stderr.write(f"{parseur.prog} : {masquer_chemins(str(e))}\n")
        return CODE_ENTREE
    except Exception as e:  # erreur interne : jamais de trace ni de chemin
        sys.stderr.write(f"{parseur.prog} : erreur interne : {message_sans_chemin(e)}\n")
        return CODE_INTERNE


def ecrire_atomique(chemin, donnees: bytes):
    """Écrit `donnees` dans `chemin` de façon atomique (`<nom>.tmp` puis `os.replace` : cfb.ecrire_atomique)."""
    from .cfb import ecrire_atomique as ecrire
    ecrire(chemin, donnees)


def ecrire_texte_atomique(chemin, texte: str):
    """Texte UTF-8 sans BOM, fins de ligne LF, écrit de façon atomique."""
    ecrire_atomique(chemin, texte.encode("utf-8"))


def verifier_fichiers(chemins):
    """ErreurEntree si un fichier désigné n'existe pas (message : nom seul)."""
    for c in chemins:
        if not os.path.isfile(c):
            raise ErreurEntree(f"{nom_seul(c)} : fichier introuvable")
