"""Conteneur OLE2 / Compound File Binary (CFB, [MS-CFB]).

Lecture : s'appuie sur `olefile` (robuste, déjà installé).
Écriture : écrivain maison, version 3 (secteurs de 512 octets, mini-secteurs de 64),
avec FAT, DIFAT (si > 109 secteurs FAT), mini-FAT et arbre rouge-noir valide.

Le modèle est un arbre de `Noeud` : la racine et les storages ont des `enfants`,
les flux ont des `donnees` (bytes). Les métadonnées (CLSID, horodatages, bits d'état)
sont conservées telles que lues.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import olefile

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF
SIGNATURE = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"

TYPE_STORAGE, TYPE_FLUX, TYPE_RACINE = 1, 2, 5


@dataclass
class Noeud:
    nom: str
    type: int  # 1 storage, 2 flux, 5 racine
    donnees: bytes = b""
    enfants: dict = field(default_factory=dict)  # nom -> Noeud (ordre d'insertion conservé)
    clsid: bytes = b"\0" * 16
    etat: int = 0
    t_creation: int = 0  # FILETIME brut (u64)
    t_modif: int = 0

    # --- navigation -----------------------------------------------------
    def chemin(self, chemin: str) -> "Noeud":
        n = self
        for p in chemin.split("/"):
            n = n.enfants[p]
        return n

    def flux(self, prefixe: str = ""):
        """Itère (chemin, noeud) sur tous les flux, en profondeur."""
        for nom, e in self.enfants.items():
            c = f"{prefixe}{nom}"
            if e.type == TYPE_FLUX:
                yield c, e
            else:
                yield from e.flux(c + "/")

    def lire(self, chemin: str) -> bytes:
        return self.chemin(chemin).donnees

    def ecrire(self, chemin: str, donnees: bytes):
        """Remplace ou crée un flux (les storages intermédiaires sont créés)."""
        parties = chemin.split("/")
        n = self
        for p in parties[:-1]:
            if p not in n.enfants:
                n.enfants[p] = Noeud(p, TYPE_STORAGE)
            n = n.enfants[p]
        if parties[-1] in n.enfants:
            n.enfants[parties[-1]].donnees = bytes(donnees)
        else:
            n.enfants[parties[-1]] = Noeud(parties[-1], TYPE_FLUX, bytes(donnees))


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------
def lire_cfb(chemin_fichier) -> Noeud:
    ole = olefile.OleFileIO(chemin_fichier)
    try:
        racine_de = ole.root

        def construire(de, chemin, est_racine=False) -> Noeud:
            n = Noeud(
                nom=de.name,
                type=TYPE_RACINE if est_racine else de.entry_type,
                clsid=_clsid_txt_vers_octets(de.clsid),
                etat=de.dwUserFlags,
                t_creation=de.createTime,
                t_modif=de.modifyTime,
            )
            if de.entry_type == TYPE_FLUX and not est_racine:
                n.donnees = ole.openstream(chemin).read() if de.size else b""
                assert len(n.donnees) == de.size
            for k in de.kids:  # olefile trie les enfants par nom ; l'ordre CFB est recalculé à l'écriture
                n.enfants[k.name] = construire(k, chemin + [k.name])
            return n

        return construire(racine_de, [], True)
    finally:
        ole.close()


def _clsid_txt_vers_octets(txt: str) -> bytes:
    if not txt:
        return b"\0" * 16
    import uuid
    return uuid.UUID(txt).bytes_le


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------
def _cle_tri(nom: str):
    """Ordre CFB : longueur d'abord, puis comparaison en majuscules (unités UTF-16)."""
    return (len(nom.encode("utf-16-le")), nom.upper())


def _arbre_rn(noms_tries, indices):
    """Construit un arbre binaire équilibré rouge-noir valide.
    Renvoie (racine, {indice: (gauche, droite, couleur)}). Couleur 0=rouge, 1=noir."""
    res = {}
    n = len(noms_tries)
    if n == 0:
        return NOSTREAM, res
    # profondeur maximale de l'arbre équilibré par coupe au milieu
    prof_max = 0
    tmp = n
    while tmp > 0:
        prof_max += 1
        tmp //= 2
    prof_max -= 1  # profondeur (0 pour un seul noeud)

    def construit(lo, hi, prof):
        if lo > hi:
            return NOSTREAM
        mid = (lo + hi) // 2
        g = construit(lo, mid - 1, prof + 1)
        d = construit(mid + 1, hi, prof + 1)
        couleur = 0 if (prof == prof_max and prof > 0) else 1
        res[indices[mid]] = (g, d, couleur)
        return indices[mid]

    # vérification : avec coupe au milieu, les feuilles sont aux profondeurs prof_max ou prof_max-1
    racine = construit(0, n - 1, 0)
    return racine, res


def ecrire_cfb(racine: Noeud, chemin_fichier=None) -> bytes:
    TS = 512
    MTS = 64
    COUPURE = 4096

    # 1) aplatir l'arbre en entrées de répertoire (racine = 0)
    entrees = []  # liste de dicts

    def ajoute(n: Noeud):
        idx = len(entrees)
        entrees.append({"n": n, "g": NOSTREAM, "d": NOSTREAM, "enf": NOSTREAM, "c": 1})
        if n.type in (TYPE_STORAGE, TYPE_RACINE):
            enfants = list(n.enfants.values())
            idxs = [ajoute(e) for e in enfants]
            ordre = sorted(range(len(enfants)), key=lambda i: _cle_tri(enfants[i].nom))
            r, liens = _arbre_rn([enfants[i].nom for i in ordre], [idxs[i] for i in ordre])
            entrees[idx]["enf"] = r
            for i, (g, d, c) in liens.items():
                entrees[i]["g"], entrees[i]["d"], entrees[i]["c"] = g, d, c
        return idx

    ajoute(racine)

    # 2) répartir les flux entre mini-stream et secteurs normaux
    mini = bytearray()
    minifat = []
    gros = []  # (indice_entree, donnees)
    for e in entrees:
        n = e["n"]
        if n.type != TYPE_FLUX:
            continue
        taille = len(n.donnees)
        e["taille"] = taille
        if taille == 0:
            e["debut"] = ENDOFCHAIN
        elif taille < COUPURE:
            nb = (taille + MTS - 1) // MTS
            debut = len(minifat)
            e["debut"] = debut
            for i in range(nb):
                minifat.append(debut + i + 1 if i < nb - 1 else ENDOFCHAIN)
            mini += n.donnees + b"\0" * (nb * MTS - taille)
        else:
            gros.append(e)

    # 3) disposition des secteurs : gros flux, mini-stream, répertoire, mini-FAT, FAT, DIFAT
    fat = []
    secteurs = []  # liste de bytes (chacun TS octets)

    def alloue(donnees: bytes):
        nb = max(1, (len(donnees) + TS - 1) // TS)
        debut = len(fat)
        for i in range(nb):
            fat.append(debut + i + 1 if i < nb - 1 else ENDOFCHAIN)
            bloc = donnees[i * TS:(i + 1) * TS]
            secteurs.append(bloc + b"\0" * (TS - len(bloc)))
        return debut

    for e in gros:
        e["debut"] = alloue(e["n"].donnees)

    entrees[0]["taille"] = len(mini)
    entrees[0]["debut"] = alloue(bytes(mini)) if mini else ENDOFCHAIN

    # répertoire
    rep = bytearray()
    for e in entrees:
        rep += _entree_rep(e)
    while len(rep) % TS:
        rep += _entree_vide()
    debut_rep = alloue(bytes(rep))

    if minifat:
        mf = b"".join(struct.pack("<I", v) for v in minifat)
        mf += b"\xff" * ((-len(mf)) % TS)
        debut_minifat = alloue(mf)
        nb_minifat = len(mf) // TS
    else:
        debut_minifat, nb_minifat = ENDOFCHAIN, 0

    # FAT + DIFAT : calcul itératif du nombre de secteurs
    n_donnees = len(fat)
    nb_fat, nb_difat = 0, 0
    while True:
        total = n_donnees + nb_fat + nb_difat
        nf = (total + 127) // 128
        nd = 0 if nf <= 109 else (nf - 109 + 126) // 127
        if nf == nb_fat and nd == nb_difat:
            break
        nb_fat, nb_difat = nf, nd
    secteurs_fat = list(range(n_donnees, n_donnees + nb_fat))
    secteurs_difat = list(range(n_donnees + nb_fat, n_donnees + nb_fat + nb_difat))
    fat += [FATSECT] * nb_fat + [DIFSECT] * nb_difat
    fat += [FREESECT] * (nb_fat * 128 - len(fat))
    for i in range(nb_fat):
        secteurs.append(b"".join(struct.pack("<I", v) for v in fat[i * 128:(i + 1) * 128]))
    reste = secteurs_fat[109:]
    for i in range(nb_difat):
        lot = reste[i * 127:(i + 1) * 127]
        lot += [FREESECT] * (127 - len(lot))
        suivant = secteurs_difat[i + 1] if i + 1 < nb_difat else ENDOFCHAIN
        secteurs.append(b"".join(struct.pack("<I", v) for v in lot) + struct.pack("<I", suivant))

    # 4) en-tête
    difat_entete = secteurs_fat[:109] + [FREESECT] * (109 - min(109, nb_fat))
    entete = bytearray()
    entete += SIGNATURE + b"\0" * 16
    entete += struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
    entete += b"\0" * 6
    entete += struct.pack("<IIIIIIIII", 0, nb_fat, debut_rep, 0, COUPURE,
                          debut_minifat, nb_minifat,
                          secteurs_difat[0] if nb_difat else ENDOFCHAIN, nb_difat)
    entete += b"".join(struct.pack("<I", v) for v in difat_entete)
    assert len(entete) == 512
    sortie = bytes(entete) + b"".join(secteurs)
    if chemin_fichier:
        ecrire_atomique(chemin_fichier, sortie)
    return sortie


def ecrire_atomique(chemin, donnees: bytes):
    """Écrit `donnees` dans `chemin` de façon atomique (§0.2-5) : `<nom>.tmp`, puis `os.replace`, réessayé
    5 fois toutes les 0,2 s sur PermissionError (fichier ouvert par un autre programme) ; le `.tmp` est retiré
    en cas d'échec."""
    import os
    import time
    tmp = os.fspath(chemin) + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(donnees)
        for essai in range(6):
            try:
                os.replace(tmp, chemin)
                return
            except PermissionError:
                if essai == 5:
                    raise
                time.sleep(0.2)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _entree_rep(e) -> bytes:
    n: Noeud = e["n"]
    nom = n.nom.encode("utf-16-le")
    assert len(nom) <= 62, f"nom trop long : {n.nom}"
    b = nom + b"\0\0"
    b += b"\0" * (64 - len(b))
    b += struct.pack("<HBB", len(nom) + 2, n.type, e["c"])
    b += struct.pack("<III", e["g"], e["d"], e["enf"] if n.type != TYPE_FLUX else NOSTREAM)
    b += (n.clsid or b"\0" * 16)[:16].ljust(16, b"\0")
    b += struct.pack("<IQQ", n.etat, n.t_creation or 0, n.t_modif or 0)
    if n.type == TYPE_STORAGE:
        b += struct.pack("<IQ", 0, 0)
    else:
        b += struct.pack("<IQ", e.get("debut", ENDOFCHAIN), e.get("taille", 0))
    assert len(b) == 128
    return b


def _entree_vide() -> bytes:
    return b"\0" * 64 + struct.pack("<HBB", 0, 0, 0) + struct.pack("<III", NOSTREAM, NOSTREAM, NOSTREAM) + b"\0" * 36 + b"\0" * 12


def verifier_arbre(racine: Noeud) -> list:
    """Contrôle de cohérence du modèle (noms <= 31 caractères, pas de doublon insensible à la casse)."""
    pbs = []

    def rec(n, c):
        vus = set()
        for nom, e in n.enfants.items():
            if len(nom) > 31:
                pbs.append(f"nom > 31 caractères : {c}{nom}")
            if nom.upper() in vus:
                pbs.append(f"doublon : {c}{nom}")
            vus.add(nom.upper())
            rec(e, c + nom + "/")
    rec(racine, "")
    return pbs
