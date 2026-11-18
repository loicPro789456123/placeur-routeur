"""Chemins absolus dans un .PcbDoc : détection et réduction au nom seul (§0.3 du contrat).

Un chemin absolu commence par une lettre de lecteur suivie de deux-points et d'une barre oblique (inverse ou
non), par un chemin réseau (deux barres obliques inverses, serveur, partage) ou par un dossier de profil
(dossier « Users » ou « home » suivi d'un nom) ; il se termine au premier délimiteur (guillemet,
barre verticale, chevron, fin de ligne, NUL, `*`, `?`, `;`, `,`) ou à la fin du texte. Le réduire, c'est le
remplacer par son dernier élément (nom du fichier ; vide pour un dossier).

Deux motifs :
- `MOTIF_CHEMIN` (textes décodés : propriétés, chaînes, STEP, XML) ;
- `MOTIF_STRICT` (octets bruts de structure inconnue, et contrôle d'un fichier entier) : au moins deux
  éléments de chemin après la lettre de lecteur ou le serveur, pour ne pas prendre des octets binaires (polices,
  modèles compressés) pour un chemin.
"""
from __future__ import annotations

import base64
import binascii
import re
import struct
import zlib

_FIN = r"[^'\"|<>\r\n\x00*?;,]*"
_DEBUT = (r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
          r"|(?<![\\\w])\\\\[\w.$-]+\\"
          r"|(?<![\w.])[\\/](?i:Users|home|Documents and Settings)[\\/][^\\/|\"'<>\r\n\x00]+")
MOTIF_CHEMIN = re.compile(f"(?:{_DEBUT}){_FIN}")
_ELT = r"[\w .()$~+-]+"
_SUITE = r"(?:[\\/][\w .()$~+-]*)*"  # éléments suivants (le chemin entier est réduit)
MOTIF_STRICT = re.compile(
    rf"(?<![A-Za-z0-9])[A-Za-z]:[\\/]{_ELT}[\\/]{_ELT}{_SUITE}"
    rf"|(?<![\\\w])\\\\[\w.$-]+\\{_ELT}{_SUITE}"
    rf"|(?<![\w.])[\\/](?i:Users|home|Documents and Settings)[\\/]{_ELT}{_SUITE}")
_SEP = re.compile(r"[\\/]")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def nom_seul(chemin) -> str:
    """Dernier élément d'un chemin écrit avec des « \\ » ou des « / » (quel que soit le système)."""
    return _SEP.split(str(chemin or ""))[-1]


def contient(texte: str) -> bool:
    """Le texte contient un chemin absolu (motif des textes décodés)."""
    return bool(texte) and MOTIF_CHEMIN.search(texte) is not None


def reduire(texte: str) -> tuple:
    """(texte où chaque chemin absolu est ramené à son nom seul, nombre de chemins réduits)."""
    if not texte:
        return texte, 0
    return MOTIF_CHEMIN.subn(lambda m: nom_seul(m.group(0)), texte)


def _zlib_base64(valeur: str):
    """Octets décompressés d'une valeur « base64 d'un flux zlib » (V9_STACKCUSTOMDATA, ConstraintManager),
    sinon None."""
    if not valeur or not _BASE64.fullmatch(valeur):
        return None
    try:
        return zlib.decompress(base64.b64decode(valeur, validate=True))
    except (binascii.Error, ValueError, zlib.error):
        return None


def reduire_valeur(valeur: str) -> tuple:
    """Réduction d'une valeur de propriété (texte latin-1 du fichier) : texte direct (Windows-1252 ou UTF-8 :
    les séparateurs sont ASCII), ou base64 d'un flux zlib (recompressé seulement s'il change).
    Rend (nouvelle valeur, nombre de chemins)."""
    if valeur is None:
        return valeur, 0
    brut = _zlib_base64(valeur)
    if brut is not None:
        t, n = reduire(brut.decode("latin-1"))
        if not n:
            return valeur, 0
        return base64.b64encode(zlib.compress(t.encode("latin-1"), 9)).decode("ascii"), n
    return reduire(valeur)


def reduire_codes(valeur: str) -> tuple:
    """Valeur « UNICODE__ » (codes décimaux séparés par des virgules) : (nouvelle valeur, nombre)."""
    if not valeur or not re.fullmatch(r"\d+(,\d+)*", valeur):
        return valeur, 0
    try:
        t = "".join(chr(int(x)) for x in valeur.split(","))
    except (ValueError, OverflowError):
        return valeur, 0
    t2, n = reduire(t)
    return (",".join(str(ord(c)) for c in t2), n) if n else (valeur, 0)


# ---------------------------------------------------------------------------
# octets bruts : même longueur (structure inconnue), latin-1 et UTF-16LE (deux alignements)
# ---------------------------------------------------------------------------
def _unites(b: bytes, align: int):
    n = (len(b) - align) // 2
    return "".join(map(chr, struct.unpack_from(f"<{n}H", b, align))) if n > 0 else ""


def trouver_octets(b: bytes, strict=True) -> list:
    """[(codage, décalage en octets)] des chemins absolus trouvés dans des octets bruts, en latin-1 (« latin-1 »)
    et en UTF-16LE (« utf-16 », deux alignements)."""
    motif = MOTIF_STRICT if strict else MOTIF_CHEMIN
    out = [("latin-1", m.start()) for m in motif.finditer(b.decode("latin-1"))]
    for align in (0, 1):
        out += [("utf-16", align + 2 * m.start()) for m in motif.finditer(_unites(b, align))]
    return out


def reduire_octets(b: bytes) -> tuple:
    """Octets bruts de structure inconnue : chaque chemin (motif strict) est remplacé, à longueur égale, par son
    nom seul suivi d'espaces (latin-1, puis UTF-16LE). Rend (octets, nombre de chemins)."""
    def rempl(m):
        t = m.group(0)
        return nom_seul(t).ljust(len(t))

    s, n = MOTIF_STRICT.subn(rempl, b.decode("latin-1"))
    out = s.encode("latin-1")
    for align in (0, 1):
        u = _unites(out, align)
        u2, k = MOTIF_STRICT.subn(rempl, u)
        if k:
            n += k
            out = out[:align] + struct.pack(f"<{len(u2)}H", *map(ord, u2)) + out[align + 2 * len(u2):]
    return out, n
