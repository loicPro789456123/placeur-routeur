"""Listes de propriétés texte Altium : `|CLE=VALEUR|CLE=VALEUR...\\0`.

Décodage sans perte : les octets sont décodés en latin-1 (bijection octet <-> caractère),
l'ordre des clés, les doublons, les éléments vides et le terminateur sont conservés.
`texte()` donne la vraie valeur affichable (Windows-1252, ou UTF-8 pour les clés `%UTF8%...`).
"""
from __future__ import annotations

import re

UNITE_INTERNE_PAR_MIL = 10000  # 1 unité interne Altium = 1/10000 mil = 2,54 nm


class Proprietes:
    __slots__ = ("elements", "terminateur")

    def __init__(self, elements=None, terminateur=b"\0"):
        # elements : liste de [cle, valeur] (valeur None = élément sans '=')
        self.elements = elements if elements is not None else []
        self.terminateur = terminateur

    # --- (dé)sérialisation ------------------------------------------------
    @classmethod
    def depuis_octets(cls, b: bytes) -> "Proprietes":
        term = b""
        if b.endswith(b"\0"):
            # un seul NUL terminal ; d'éventuels NUL supplémentaires restent dans le corps
            term = b"\0"
            b = b[:-1]
        s = b.decode("latin-1")
        elements = []
        for item in s.split("|"):
            if "=" in item:
                k, v = item.split("=", 1)
                elements.append([k, v])
            else:
                elements.append([item, None])
        return cls(elements, term)

    def vers_octets(self) -> bytes:
        s = "|".join(k if v is None else f"{k}={v}" for k, v in self.elements)
        return s.encode("latin-1") + self.terminateur

    # --- accès de type dictionnaire (première occurrence) -------------------
    def get(self, cle, defaut=None):
        for k, v in self.elements:
            if k == cle:
                return v
        return defaut

    def geti(self, cle, defaut=None):
        """Comme get, mais insensible à la casse (les .SchDoc mélangent 'Location.X' et 'LOCATION.X')."""
        v = self.get(cle, KeyError)
        if v is not KeyError:
            return v
        c = cle.upper()
        for k, v in self.elements:
            if k.upper() == c:
                return v
        return defaut

    def __getitem__(self, cle):
        v = self.get(cle, KeyError)
        if v is KeyError:
            raise KeyError(cle)
        return v

    def __contains__(self, cle):
        return any(k == cle for k, _ in self.elements)

    def __setitem__(self, cle, valeur):
        """Modifie la première occurrence ; sinon ajoute en fin (avant un éventuel élément vide final)."""
        for e in self.elements:
            if e[0] == cle:
                e[1] = valeur
                return
        pos = len(self.elements)
        if self.elements and self.elements[-1] == ["", None] and pos > 1:
            pos -= 1
        self.elements.insert(pos, [cle, valeur])

    def supprimer(self, cle):
        self.elements = [e for e in self.elements if e[0] != cle]

    def cles(self):
        return [k for k, v in self.elements if not (k == "" and v is None)]

    def dict(self):
        d = {}
        for k, v in self.elements:
            if k == "" and v is None:
                continue
            d.setdefault(k, v)
        return d

    def texte(self, cle, defaut=None):
        """Valeur texte réelle : `UNICODE__CLE` (codes décimaux) ou `%UTF8%CLE` prioritaires,
        sinon Windows-1252."""
        c = self.geti("UNICODE__" + cle)
        if c is not None:
            t = codes_vers_texte(c)
            if t is not None:
                return t
        u = self.geti("%UTF8%" + cle)
        if u is not None:
            return u.encode("latin-1").decode("utf-8", "replace")
        v = self.geti(cle)
        if v is None:
            return defaut
        return v.encode("latin-1").decode("cp1252", "replace")

    def __repr__(self):
        return f"Proprietes({len(self.elements)} éléments)"


def codes_vers_texte(v):
    """'76,50,95,71' -> 'L2_G' (format Altium des chaînes Unicode : codes décimaux séparés par des virgules)."""
    if v is None or not re.fullmatch(r"\d+(,\d+)*", v):
        return None
    return "".join(chr(int(x)) for x in v.split(","))


def texte_vers_codes(t: str) -> str:
    return ",".join(str(ord(c)) for c in t)


# --- conversions d'unités ----------------------------------------------------
_RE_VAL = re.compile(r"^\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(mil|mm|in|cm|um|nm)?\s*$")
_FACTEUR_MIL = {None: 1.0, "mil": 1.0, "mm": 1 / 0.0254, "cm": 10 / 0.0254, "in": 1000.0,
                "um": 1 / 25.4, "nm": 1 / 25400.0}


def valeur_mil(txt, defaut=None):
    """'12.5mil' / '0.3mm' -> float en mil."""
    if txt is None:
        return defaut
    m = _RE_VAL.match(txt)
    if not m:
        return defaut
    return float(m.group(1)) * _FACTEUR_MIL[m.group(2)]


def valeur_mm(txt, defaut=None):
    v = valeur_mil(txt)
    return defaut if v is None else v * 0.0254


def formater_mil(v_mil: float) -> str:
    """Format Altium courant : '12.5mil', '0mil', '3.937mil'."""
    s = f"{v_mil:.4f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s + "mil"


def booleen(txt, defaut=None):
    if txt is None:
        return defaut
    return txt.upper() == "TRUE"


def interne_vers_mil(v: int) -> float:
    return v / UNITE_INTERNE_PAR_MIL


def mil_vers_interne(v: float) -> int:
    return int(round(v * UNITE_INTERNE_PAR_MIL))


def mm_vers_interne(v: float) -> int:
    return int(round(v / 0.0254 * UNITE_INTERNE_PAR_MIL))


def interne_vers_mm(v: int) -> float:
    return v / UNITE_INTERNE_PAR_MIL * 0.0254
