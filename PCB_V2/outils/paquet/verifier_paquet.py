# -*- coding: utf-8 -*-
"""Vérifications du paquet portable PlaceurRouteur (contrat §5.5, « Vérifications » ; lot D, É7).

    python verifier_paquet.py DOSSIER_DU_PAQUET [--reference-python DIR] [--exemption SHA256 ...]
                              [--seulement a,b,c,d,e]

Code 0 : tout est conforme ; 1 : au moins une vérification échoue (chaque échec est écrit) ; 2 : usage.

(a) fermeture des DLL : chaque DLL importée (imports directs et différés) par un .exe, un .dll ou un .pyd du paquet
    est fournie par le paquet ou fait partie de Windows 10 (liste `DLL_WINDOWS`, ensembles d'API `api-ms-win-*`) ;
(b) les moteurs (`_internal/moteurs/*.exe`) n'importent ni VCRUNTIME*, ni MSVCP*, ni api-ms-win-crt-* ;
(c) anonymat : motifs génériques de §0.3 (`chemins.chercher_motifs`, lettre de lecteur comprise) dans chaque
    fichier, dans les membres des zip, dans le contenu décompressé des archives PYZ et des archives CArchive
    ajoutées aux exécutables ; exemptés par empreinte SHA-256 : les fichiers identiques à ceux de la distribution
    Python de référence (`--reference-python`, par défaut celle qui exécute ce script ; ils contiennent le chemin
    de leur machine de construction) et les empreintes données par `--exemption` (avis de licence) ;
(d) liste blanche du contenu : fichiers de premier niveau attendus, motifs autorisés dans `_internal`, aucune donnée
    de carte hors `exemples/` ;
(e) ressources de version des deux exécutables : ProductName « PlaceurRouteur », CompanyName vide, LegalCopyright
    « voir LICENCES.txt », ProductVersion = `_internal/VERSION`.

Bibliothèque standard seulement (lecteur PE, CArchive et PYZ écrits ici). Les motifs privés restent dans
`outils_prives/publier_public.py --verifier-zip`.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import marshal
import os
import struct
import sys
import zipfile
import zlib

ICI = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(os.path.dirname(ICI), "placeur_routage")
if SOURCES not in sys.path:
    sys.path.insert(0, SOURCES)

import chemins  # noqa: E402

# ------------------------------------------------------------------------------------------------ constantes

EXE_FENETRE = "PlaceurRouteur.exe"
EXE_CONSOLE = "placeur.exe"
PREMIER_NIVEAU_OBLIGATOIRE = (EXE_FENETRE, EXE_CONSOLE, "_internal", "0_LIRE_AVANT.txt", "MODE_EMPLOI.html",
                              "parametres_modele.ini", "LICENCES.txt")
PREMIER_NIVEAU_PERMIS = ("LICENCE.txt", "exemples")     # LICENCE.txt : licence de l'outil, décision §8.9
MOTEURS = ("moteurs/placeur_routage.exe", "moteurs/serigraphie.exe")
# motifs autorisés dans _internal (chemins relatifs en minuscules, séparateur /)
INTERNE_AUTORISES = (
    "version", "commit", "base_library.zip", "*.dll", "*.pyd", "moteurs/placeur_routage.exe",
    "moteurs/serigraphie.exe", "_tcl_data/*", "_tk_data/*", "tcl8/*", "tcl/*", "tk/*", "*.dist-info/*",
    "pyz*.pyz",
)
# extensions et noms de fichiers de données de carte (interdits hors exemples/)
EXTENSIONS_CARTE = (".pcbdoc", ".pcblib", ".schdoc", ".schlib", ".prjpcb", ".intlib", ".kicad_pcb", ".kicad_sch",
                    ".kicad_pro", ".kicad_mod", ".gbr", ".gtl", ".gbl", ".gto", ".gbo", ".drl", ".step")
PREFIXES_JSON_CARTE = ("donnees_placeur", "contraintes", "entree_carte", "config_carte", "placements",
                       "routage", "pastilles")
MOTEUR_INTERDITS = ("vcruntime", "msvcp", "api-ms-win-crt-")
VERSION_ATTENDUE = {"ProductName": "PlaceurRouteur", "CompanyName": "", "LegalCopyright": "voir LICENCES.txt"}
# DLL de Windows 10 (x64), présentes sur toute installation ; les ensembles d'API api-ms-win-* et ext-ms-win-*
# sont résolus par le chargeur (l'UCRT, api-ms-win-crt-*, fait partie de Windows 10).
DLL_WINDOWS = frozenset("""
advapi32 bcrypt bcryptprimitives cabinet cfgmgr32 comctl32 comdlg32 crypt32 cryptbase cryptsp dbghelp dnsapi dwmapi
gdi32 gdiplus glu32 imagehlp imm32 iphlpapi kernel32 kernelbase mpr msimg32 msvcrt mswsock ncrypt netapi32 normaliz
ntdll ole32 oleacc oleaut32 opengl32 powrprof propsys psapi rpcrt4 secur32 sechost setupapi shcore shell32 shlwapi
ucrtbase urlmon user32 userenv uxtheme version winhttp wininet winmm winspool wintrust ws2_32 wsock32 wtsapi32
dxgi d3d11 d2d1 dwrite usp10 msctf credui ntdsapi mscoree msi
""".split())
MAGIE_CARCHIVE = b"MEI\x0c\x0b\x0a\x0b\x0e"
MAGIE_PYZ = b"PYZ\x00"


class ErreurPE(ValueError):
    """Fichier PE illisible."""


# ------------------------------------------------------------------------------------------------ lecteur PE

class PE:
    """Lecteur minimal d'un fichier PE (PE32 et PE32+) : sections, imports, imports différés, ressources."""

    def __init__(self, donnees: bytes):
        self.d = donnees
        if donnees[:2] != b"MZ" or len(donnees) < 0x40:
            raise ErreurPE("en-tête MZ absent")
        pe = struct.unpack_from("<I", donnees, 0x3C)[0]
        if donnees[pe:pe + 4] != b"PE\x00\x00":
            raise ErreurPE("signature PE absente")
        coff = pe + 4
        self.machine, nsections = struct.unpack_from("<HH", donnees, coff)
        taille_opt = struct.unpack_from("<H", donnees, coff + 16)[0]
        opt = coff + 20
        magie = struct.unpack_from("<H", donnees, opt)[0]
        if magie == 0x10B:
            nrep, rep = struct.unpack_from("<I", donnees, opt + 92)[0], opt + 96
        elif magie == 0x20B:
            nrep, rep = struct.unpack_from("<I", donnees, opt + 108)[0], opt + 112
        else:
            raise ErreurPE("en-tête optionnel inconnu")
        self.repertoires = [struct.unpack_from("<II", donnees, rep + 8 * i) for i in range(min(nrep, 16))]
        self.sections = []
        s = opt + taille_opt
        for i in range(nsections):
            nom, vtaille, vadr, taille_brute, ptr = struct.unpack_from("<8sIIII", donnees, s + 40 * i)
            self.sections.append((vadr, max(vtaille, taille_brute), ptr, taille_brute))

    def decalage(self, rva: int) -> int:
        for vadr, vtaille, ptr, taille_brute in self.sections:
            if vadr <= rva < vadr + vtaille:
                if rva - vadr >= taille_brute:
                    raise ErreurPE("adresse hors des données de la section")
                return ptr + rva - vadr
        raise ErreurPE(f"adresse virtuelle hors section : {rva:#x}")

    def chaine(self, rva: int) -> str:
        p = self.decalage(rva)
        fin = self.d.index(b"\x00", p)
        return self.d[p:fin].decode("latin-1")

    def imports(self) -> list[str]:
        """Noms des DLL importées, directement (répertoire 1) et en différé (répertoire 13)."""
        noms = []
        if len(self.repertoires) > 1 and self.repertoires[1][0]:
            p = self.decalage(self.repertoires[1][0])
            while True:
                champs = struct.unpack_from("<IIIII", self.d, p)
                if not any(champs):
                    break
                noms.append(self.chaine(champs[3]))
                p += 20
        if len(self.repertoires) > 13 and self.repertoires[13][0]:
            p = self.decalage(self.repertoires[13][0])
            while True:
                champs = struct.unpack_from("<IIIIIIII", self.d, p)
                if not any(champs):
                    break
                noms.append(self.chaine(champs[1]))
                p += 32
        return noms

    def ressource_version(self) -> bytes | None:
        """Octets de la ressource RT_VERSION (16), première langue, ou None."""
        if len(self.repertoires) < 3 or not self.repertoires[2][0]:
            return None
        base = self.decalage(self.repertoires[2][0])

        def entrees(p):
            nnoms, nids = struct.unpack_from("<HH", self.d, p + 12)
            return [struct.unpack_from("<II", self.d, p + 16 + 8 * i) for i in range(nnoms + nids)]

        for ident, cible in entrees(base):
            if ident == 16 and cible & 0x80000000:
                p = base + (cible & 0x7FFFFFFF)
                while True:
                    liste = entrees(p)
                    if not liste:
                        return None
                    cible2 = liste[0][1]
                    if cible2 & 0x80000000:
                        p = base + (cible2 & 0x7FFFFFFF)
                        continue
                    rva, taille = struct.unpack_from("<II", self.d, base + cible2)
                    q = self.decalage(rva)
                    return self.d[q:q + taille]
        return None


def _aligner(p: int) -> int:
    return (p + 3) & ~3


def _noeud_version(d: bytes, p: int):
    longueur, lvaleur, genre = struct.unpack_from("<HHH", d, p)
    fin = p + longueur
    q = p + 6
    k = q
    while d[k:k + 2] != b"\x00\x00":
        k += 2
    cle = d[q:k].decode("utf-16-le")
    q = _aligner(k + 2)
    if genre == 1:
        valeur = d[q:q + 2 * lvaleur].decode("utf-16-le", errors="replace").split("\x00")[0]
        q += 2 * lvaleur
    else:
        valeur = d[q:q + lvaleur]
        q += lvaleur
    q = _aligner(q)
    enfants = []
    while q + 6 <= fin:
        enfant, q2 = _noeud_version(d, q)
        enfants.append(enfant)
        if q2 <= q:
            break
        q = _aligner(q2)
    return (cle, valeur, enfants), fin


def chaines_version(donnees_pe: bytes) -> dict[str, str]:
    """Chaînes de StringFileInfo de la ressource de version d'un exécutable ({} si absente)."""
    brut = PE(donnees_pe).ressource_version()
    if not brut:
        return {}
    (cle, _v, enfants), _ = _noeud_version(brut, 0)
    sortie = {}
    for nom, _valeur, tables in enfants:
        if nom == "StringFileInfo":
            for _table, _vt, chaines in tables:
                for k, v, _e in chaines:
                    sortie.setdefault(k, v if isinstance(v, str) else "")
    return sortie


# ------------------------------------------------------------------------------------------------ archives

def contenus_carchive(donnees: bytes) -> list[tuple[str, bytes]]:
    """Entrées décompressées de l'archive CArchive (PKG) ajoutée à un exécutable PyInstaller (PyInstaller 6)."""
    pos = donnees.rfind(MAGIE_CARCHIVE)
    if pos < 0 or pos + 88 > len(donnees):
        return []
    _m, taille, toc, ltoc, _pyv, _lib = struct.unpack_from("!8sIIii64s", donnees, pos)
    debut = pos + 88 - taille
    if debut < 0:
        return []
    sortie = []
    p, fin = debut + toc, debut + toc + ltoc
    while p + 18 <= fin:
        lentree, dec, lcomp, _lnon, comp, _code = struct.unpack_from("!IIIIBc", donnees, p)
        if lentree < 18:
            break
        nom = donnees[p + 18:p + lentree].rstrip(b"\x00").decode("utf-8", errors="replace")
        brut = donnees[debut + dec:debut + dec + lcomp]
        try:
            sortie.append((nom, zlib.decompress(brut) if comp else brut))
        except zlib.error:
            sortie.append((nom, brut))
        p += lentree
    return sortie


def contenus_pyz(donnees: bytes) -> list[tuple[str, bytes]]:
    """Entrées décompressées d'une archive PYZ de PyInstaller (table marshal : liste ou dictionnaire)."""
    if donnees[:4] != MAGIE_PYZ or len(donnees) < 12:
        return []
    toc_dec = struct.unpack_from("!i", donnees, 8)[0]
    try:
        table = marshal.loads(donnees[toc_dec:])
    except (ValueError, EOFError, TypeError):
        return [("<table illisible>", donnees)]
    elements = table.items() if isinstance(table, dict) else table
    sortie = []
    for nom, info in elements:
        _code, dec, longueur = info[-3:]
        brut = donnees[dec:dec + longueur]
        try:
            sortie.append((str(nom), zlib.decompress(brut)))
        except zlib.error:
            sortie.append((str(nom), brut))
    return sortie


def contenus_imbriques(nom: str, donnees: bytes, profondeur: int = 0):
    """Le fichier, puis récursivement les contenus des zip, des PYZ et des CArchive qu'il porte."""
    yield nom, donnees
    if profondeur > 3:
        return
    enfants = []
    if donnees[:4] == MAGIE_PYZ:
        enfants = contenus_pyz(donnees)
    elif donnees[:2] == b"MZ" and MAGIE_CARCHIVE in donnees:
        enfants = contenus_carchive(donnees)
    elif donnees[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(donnees)) as z:
                enfants = [(i.filename, z.read(i)) for i in z.infolist() if not i.is_dir()]
        except (zipfile.BadZipFile, OSError, RuntimeError):
            enfants = []
    for n, c in enfants:
        yield from contenus_imbriques(f"{nom}!{n}", c, profondeur + 1)


# ------------------------------------------------------------------------------------------------ vérifications

def fichiers(racine: str) -> list[str]:
    """Chemins relatifs (séparateur /) de tous les fichiers du paquet, triés."""
    sortie = []
    for dossier, _sous, noms in os.walk(racine):
        for n in noms:
            sortie.append(os.path.relpath(os.path.join(dossier, n), racine).replace(os.sep, "/"))
    return sorted(sortie)


def _lire(racine: str, rel: str) -> bytes:
    with open(os.path.join(racine, rel), "rb") as f:
        return f.read()


def _est_pe(rel: str) -> bool:
    return rel.lower().endswith((".exe", ".dll", ".pyd"))


def verifier_dll(racine: str) -> list[str]:
    """(a) Fermeture des DLL."""
    fournies = {os.path.basename(r).lower() for r in fichiers(racine)}
    erreurs = []
    for rel in fichiers(racine):
        if not _est_pe(rel):
            continue
        try:
            noms = PE(_lire(racine, rel)).imports()
        except (ErreurPE, struct.error, ValueError) as e:
            erreurs.append(f"(a) {rel} : PE illisible ({e})")
            continue
        for n in noms:
            b = n.lower()
            if b in fournies or b.startswith(("api-ms-win-", "ext-ms-win-")):
                continue
            if b.endswith(".dll") and b[:-4] in DLL_WINDOWS or b.endswith(".drv") and b[:-4] in DLL_WINDOWS:
                continue
            erreurs.append(f"(a) {rel} importe {n}, ni fournie par le paquet ni livrée avec Windows 10")
    return erreurs


def verifier_moteurs(racine: str) -> list[str]:
    """(b) Moteurs liés statiquement à la bibliothèque C (+crt-static)."""
    erreurs = []
    for m in MOTEURS:
        rel = "_internal/" + m
        chemin = os.path.join(racine, rel)
        if not os.path.isfile(chemin):
            erreurs.append(f"(b) moteur absent : {rel}")
            continue
        try:
            noms = PE(_lire(racine, rel)).imports()
        except (ErreurPE, struct.error, ValueError) as e:
            erreurs.append(f"(b) {rel} : PE illisible ({e})")
            continue
        for n in noms:
            if n.lower().startswith(MOTEUR_INTERDITS):
                erreurs.append(f"(b) {rel} importe {n} : moteur non construit avec +crt-static")
    return erreurs


def empreintes_reference(dossier: str | None) -> set[str]:
    """SHA-256 des binaires et des données Tcl/Tk de la distribution Python de référence."""
    if not dossier or not os.path.isdir(dossier):
        return set()
    sortie = set()
    candidats = [os.path.join(dossier, n) for n in os.listdir(dossier)]
    for sous in ("DLLs", "tcl"):
        base = os.path.join(dossier, sous)
        for d, _s, noms in os.walk(base):
            candidats.extend(os.path.join(d, n) for n in noms)
    for c in candidats:
        if os.path.isfile(c):
            with open(c, "rb") as f:
                sortie.add(hashlib.sha256(f.read()).hexdigest())
    return sortie


def verifier_anonymat(racine: str, exemptions: set[str]) -> tuple[list[str], int]:
    """(c) Motifs génériques de §0.3. Rend (erreurs, nombre de fichiers exemptés par empreinte).
    Les erreurs ne citent jamais l'extrait trouvé (il contiendrait ce qu'on cherche à cacher)."""
    erreurs, exemptes = [], 0
    for rel in fichiers(racine):
        donnees = _lire(racine, rel)
        if hashlib.sha256(donnees).hexdigest() in exemptions:
            exemptes += 1
            continue
        for nom, contenu in contenus_imbriques(rel, donnees):
            if nom != rel and hashlib.sha256(contenu).hexdigest() in exemptions:
                continue
            motifs = sorted({m for m, _x in chemins.chercher_motifs(contenu, lecteur=True)})
            if motifs:
                erreurs.append(f"(c) {nom} : {', '.join(motifs)}")
    return erreurs, exemptes


def est_donnee_carte(rel: str) -> bool:
    base = os.path.basename(rel).lower()
    if base.endswith(EXTENSIONS_CARTE):
        return True
    return base.endswith(".json") and base.startswith(PREFIXES_JSON_CARTE)


def verifier_liste_blanche(racine: str) -> list[str]:
    """(d) Liste blanche du contenu."""
    erreurs = []
    premiers = set(os.listdir(racine))
    for n in PREMIER_NIVEAU_OBLIGATOIRE:
        if n not in premiers:
            erreurs.append(f"(d) absent du premier niveau : {n}")
    for n in sorted(premiers - set(PREMIER_NIVEAU_OBLIGATOIRE) - set(PREMIER_NIVEAU_PERMIS)):
        erreurs.append(f"(d) fichier ou dossier inattendu au premier niveau : {n}")
    for rel in fichiers(racine):
        if rel.startswith("exemples/"):
            continue
        if est_donnee_carte(rel):
            erreurs.append(f"(d) donnée de carte hors exemples/ : {rel}")
        if rel.startswith("_internal/"):
            interne = rel[len("_internal/"):].lower()
            if not any(fnmatch.fnmatchcase(interne, m) for m in INTERNE_AUTORISES):
                erreurs.append(f"(d) fichier non autorisé dans _internal : {rel}")
    for m in MOTEURS:
        if not os.path.isfile(os.path.join(racine, "_internal", m)):
            erreurs.append(f"(d) moteur absent : _internal/{m}")
    return erreurs


def verifier_version(racine: str) -> list[str]:
    """(e) Ressources de version des deux exécutables."""
    erreurs = []
    try:
        with open(os.path.join(racine, "_internal", "VERSION"), encoding="utf-8-sig") as f:
            version = f.read().strip()
    except OSError:
        return ["(e) _internal/VERSION absent"]
    for exe in (EXE_FENETRE, EXE_CONSOLE):
        chemin = os.path.join(racine, exe)
        if not os.path.isfile(chemin):
            erreurs.append(f"(e) {exe} absent")
            continue
        try:
            chaines = chaines_version(_lire(racine, exe))
        except (ErreurPE, struct.error, ValueError, UnicodeDecodeError) as e:
            erreurs.append(f"(e) {exe} : ressource de version illisible ({e})")
            continue
        if not chaines:
            erreurs.append(f"(e) {exe} : aucune ressource de version")
            continue
        for cle, attendu in VERSION_ATTENDUE.items():
            if chaines.get(cle, "") != attendu:
                erreurs.append(f"(e) {exe} : {cle} = {chaines.get(cle, '')!r}, attendu {attendu!r}")
        if chaines.get("ProductVersion", "") != version:
            erreurs.append(f"(e) {exe} : ProductVersion = {chaines.get('ProductVersion', '')!r}, "
                           f"attendu {version!r} (VERSION)")
    return erreurs


def verifier(racine: str, reference_python: str | None, exemptions=(), seulement="abcde", flux=None) -> int:
    flux = flux or sys.stdout
    if not os.path.isdir(racine):
        flux.write("erreur : dossier du paquet introuvable\n")
        return 2
    erreurs = []
    if "a" in seulement:
        e = verifier_dll(racine)
        flux.write(f"(a) fermeture des DLL : {'OK' if not e else f'{len(e)} échec(s)'}\n")
        erreurs += e
    if "b" in seulement:
        e = verifier_moteurs(racine)
        flux.write(f"(b) moteurs sans bibliothèque C dynamique : {'OK' if not e else f'{len(e)} échec(s)'}\n")
        erreurs += e
    if "c" in seulement:
        ex = empreintes_reference(reference_python) | {x.lower() for x in exemptions}
        e, n = verifier_anonymat(racine, ex)
        flux.write(f"(c) anonymat : {'OK' if not e else f'{len(e)} échec(s)'} ({n} fichier(s) exemptés par "
                   f"empreinte)\n")
        erreurs += e
    if "d" in seulement:
        e = verifier_liste_blanche(racine)
        flux.write(f"(d) liste blanche : {'OK' if not e else f'{len(e)} échec(s)'}\n")
        erreurs += e
    if "e" in seulement:
        e = verifier_version(racine)
        flux.write(f"(e) ressources de version : {'OK' if not e else f'{len(e)} échec(s)'}\n")
        erreurs += e
    for ligne in erreurs:
        flux.write(ligne + "\n")
    flux.write("paquet conforme\n" if not erreurs else f"paquet NON conforme : {len(erreurs)} échec(s)\n")
    return 1 if erreurs else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Vérifications (a) à (e) du paquet portable (§5.5).")
    p.add_argument("paquet", help="dossier PlaceurRouteur du paquet")
    p.add_argument("--reference-python", default=sys.base_prefix,
                   help="distribution Python dont les binaires sont exemptés par empreinte (défaut : celle-ci)")
    p.add_argument("--exemption", action="append", default=[], metavar="SHA256",
                   help="empreinte SHA-256 d'un fichier exempté du contrôle d'anonymat (avis de licence)")
    p.add_argument("--seulement", default="abcde", help="sous-ensemble des vérifications (ex. « ab »)")
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return verifier(a.paquet, a.reference_python, a.exemption, a.seulement)


if __name__ == "__main__":
    sys.exit(main())
