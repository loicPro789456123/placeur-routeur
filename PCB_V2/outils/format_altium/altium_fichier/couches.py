"""Identifiants de couches Altium.

- V6 : un octet (champ `couche` des primitives binaires, et noms texte `LAYER=TOP`...).
- V7 : 32 bits (champ `couche_v7`) : 0x0100xxxx cuivre, 0x0101xxxx plans internes,
  0x0102xxxx mécaniques, 0x0103xxxx couches « autres » (sérigraphie, masque, pâte...).
"""

NOMS_V6 = {0: "UNKNOWN", 1: "TOP"}
for _i in range(1, 31):
    NOMS_V6[1 + _i] = f"MID{_i}"
NOMS_V6.update({32: "BOTTOM", 33: "TOPOVERLAY", 34: "BOTTOMOVERLAY", 35: "TOPPASTE",
                36: "BOTTOMPASTE", 37: "TOPSOLDER", 38: "BOTTOMSOLDER"})
for _i in range(1, 17):
    NOMS_V6[38 + _i] = f"PLANE{_i}"
NOMS_V6.update({55: "DRILLGUIDE", 56: "KEEPOUT"})
for _i in range(1, 17):
    NOMS_V6[56 + _i] = f"MECHANICAL{_i}"
NOMS_V6.update({73: "DRILLDRAWING", 74: "MULTILAYER", 75: "CONNECTIONS", 76: "BACKGROUND",
                77: "DRCERRORMARKERS", 78: "SELECTIONS", 79: "VISIBLEGRID1", 80: "VISIBLEGRID2",
                81: "PADHOLES", 82: "VIAHOLES"})
IDS_V6 = {v: k for k, v in NOMS_V6.items()}

# Couches « autres » V7 : 0x01030000 + n (d'après KiCad, cohérent avec nos fichiers)
_AUTRES_V7 = {6: "TOPOVERLAY", 7: "BOTTOMOVERLAY", 8: "TOPPASTE", 9: "BOTTOMPASTE",
              10: "TOPSOLDER", 11: "BOTTOMSOLDER", 12: "DRILLGUIDE", 13: "KEEPOUT",
              14: "DRILLDRAWING", 15: "MULTILAYER", 16: "CONNECTIONS", 17: "BACKGROUND",
              18: "DRCERRORMARKERS", 19: "SELECTIONS", 20: "VISIBLEGRID1", 21: "VISIBLEGRID2",
              22: "PADHOLES", 23: "VIAHOLES", 24: "TOPPADMASTER", 25: "BOTTOMPADMASTER",
              26: "DRCDETAILMARKERS"}


def nom_v7(v: int) -> str:
    fam, n = v >> 16, v & 0xFFFF
    if v == 0:
        return "AUCUNE"
    if fam == 0x0100:
        if n == 1:
            return "TOP"
        if n == 0xFFFF:
            return "BOTTOM"
        return f"MID{n - 1}"  # supposé : 0x01000002 = MID1
    if fam == 0x0101:
        return f"PLANE{n}"
    if fam == 0x0102:
        return f"MECHANICAL{n}"
    if fam == 0x0103:
        return _AUTRES_V7.get(n, f"AUTRE_{n}")
    return f"V7_{v:08X}"


def v7_depuis_v6(v6: int) -> int:
    """Correspondance V6 -> V7 (utilisée pour créer de nouvelles primitives)."""
    if v6 == 1:
        return 0x01000001
    if v6 == 32:
        return 0x0100FFFF
    if 2 <= v6 <= 31:
        return 0x01000000 + v6  # MIDn -> n+1 (supposé)
    if 39 <= v6 <= 54:
        return 0x01010000 + (v6 - 38)
    if 57 <= v6 <= 72:
        return 0x01020000 + (v6 - 56)
    nom = NOMS_V6.get(v6)
    for n, s in _AUTRES_V7.items():
        if s == nom:
            return 0x01030000 + n
    return 0


def nom_couche(v6: int, v7: int | None = None) -> str:
    if v7 and (v7 >> 16) == 0x0102 and (v7 & 0xFFFF) > 16:
        return nom_v7(v7)  # mécaniques 17+ n'existent qu'en V7
    return NOMS_V6.get(v6, f"COUCHE_{v6}")


# ---------------------------------------------------------------------------
# Couches de cuivre : V7 <-> V6 <-> noms du contrat (§0.4 : TopLayer, MidLayer<n>, InternalPlane<n>,
# BottomLayer). La numérotation ne suit pas forcément l'ordre physique : le rang vient de l'empilage.
# ---------------------------------------------------------------------------
def est_cuivre_v7(v7: int) -> bool:
    """Couche de cuivre (signal 0x0100xxxx ou plan négatif 0x0101xxxx)."""
    fam, n = v7 >> 16, v7 & 0xFFFF
    return (fam == 0x0100 and (n == 0xFFFF or 1 <= n <= 31)) or (fam == 0x0101 and 1 <= n <= 16)


def est_plan_v7(v7: int) -> bool:
    return v7 >> 16 == 0x0101 and 1 <= (v7 & 0xFFFF) <= 16


def v6_depuis_v7(v7: int) -> int:
    """Correspondance V7 -> V6 (0 si la couche n'a pas d'identifiant V6)."""
    fam, n = v7 >> 16, v7 & 0xFFFF
    if fam == 0x0100:
        if n == 1:
            return 1
        if n == 0xFFFF:
            return 32
        if 2 <= n <= 31:
            return n  # MID(n-1) : V6 = 1 + (n - 1)
        return 0
    if fam == 0x0101 and 1 <= n <= 16:
        return 38 + n
    if fam == 0x0102 and 1 <= n <= 16:
        return 56 + n
    if fam == 0x0103:
        return IDS_V6.get(_AUTRES_V7.get(n, ""), 0)
    return 0


def nom_contrat_v6(v6: int) -> str | None:
    """Nom de couche de cuivre du contrat d'après l'identifiant V6 ; None hors cuivre."""
    if v6 == 1:
        return "TopLayer"
    if v6 == 32:
        return "BottomLayer"
    if 2 <= v6 <= 31:
        return f"MidLayer{v6 - 1}"
    if 39 <= v6 <= 54:
        return f"InternalPlane{v6 - 38}"
    return None


def nom_contrat_v7(v7: int) -> str | None:
    return nom_contrat_v6(v6_depuis_v7(v7)) if est_cuivre_v7(v7) else None


def v6_depuis_nom(nom: str) -> int | None:
    """Identifiant V6 d'une couche donnée par son nom Altium V6 (« TOP », « MID2 », « PLANE1 ») ou par
    son nom du contrat (« TopLayer », « MidLayer2 », « InternalPlane1 », « BottomLayer ») ; None si inconnu."""
    if nom in IDS_V6:
        return IDS_V6[nom]
    if nom == "TopLayer":
        return 1
    if nom == "BottomLayer":
        return 32
    for prefixe, base, maxi in (("MidLayer", 1, 30), ("InternalPlane", 38, 16)):
        if nom.startswith(prefixe) and nom[len(prefixe):].isdigit():
            k = int(nom[len(prefixe):])
            if 1 <= k <= maxi:
                return base + k
    return None
