# -*- coding: utf-8 -*-
"""Chemins, sous-processus et services du système pour le placeur-routeur (lot D).

Contrat 2.1 : §5.2 (API), §5.3 (verrou, arrêts, ressources de la machine), §5.5 (mode figé, version), §0.2-2,
§0.2-5, §0.2-10 (sous-processus, mode figé), §0.3 (anonymat).

Tout ce qui dépend du système d'exploitation est ici, pour que les autres modules n'en dépendent pas :

- emplacements : `ressource`, `chemin_moteur`, `commande_moteur`, `commande_outil`, `commande_placeur`, identiques
  en mode source (dépôt) et en mode figé (paquet PyInstaller « onedir ») ;
- sous-processus : `lancer` et `executer` (sorties en UTF-8, sans fenêtre de console, priorité « inférieure à la
  normale », environnement réduit), objet de travail `Job` (« tuer à la fermeture » sous Windows, groupes de
  processus ailleurs), `processus_vivant` ;
- synchronisation : `Verrou` (verrou exclusif d'un dossier de sortie), `SemaphoreMoteurs` (nombre total de moteurs
  de la session), `empecher_veille` ;
- fichiers : `ecrire_atomique`, `ecrire_json`, `ecrire_texte_bloc_notes` (§0.2-5) ;
- version : `version_lanceur()` = contenu de VERSION + « + » + 7 caractères hexadécimaux du commit ;
- système : `dossier_connu` (SHGetKnownFolderPath), `dossier_temporaire`, `noms_machine`, `nom_utilisateur`,
  `priorite_processus` ;
- anonymat : `anonymiser` (sorties, §0.3) et `chercher_motifs` (motifs génériques de §0.3, source unique pour
  test_anonymat et paquet/verifier_paquet.py).

Mode source : modules dans PCB_V2/outils/placeur_routage ; version dans PCB_V2/outils/VERSION ; moteurs dans
<crate>/target/release, construits par `cargo build --release --offline` s'ils sont absents ou périmés ; outils
Python lancés par `python lanceur.py --outil <nom>`.
Mode figé (`sys.frozen`) : ressources dans `sys._MEIPASS` (dossier `_internal` du paquet), puis dans le dossier des
exécutables ; moteurs dans `_internal/moteurs` ; version dans `_internal/VERSION` et commit dans `_internal/COMMIT`
(écrits à la construction du paquet) ; outils Python lancés par `placeur.exe --outil <nom>`.

Variables d'environnement (§0.2-2) : aucune lecture ne change un comportement. Le profil, `%APPDATA%`,
`%LOCALAPPDATA%` et Documents sont lus par SHGetKnownFolderPath ; le nom de la machine et celui de l'utilisateur par
les fonctions du système ; le dossier temporaire est celui que donne Windows (GetTempPathW) et ne sert qu'à
l'anonymisation. La seule lecture de l'environnement est `environnement_sous_processus()`, qui construit
l'environnement des sous-processus (liste blanche de variables du système ; environnement complet pour cargo).

Bibliothèque standard seulement ; ctypes n'est chargé qu'à la première fonction qui en a besoin.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time

# ------------------------------------------------------------------------------------------------ constantes

NOM_EXE_CONSOLE = "placeur.exe"        # exécutable console du paquet (ligne de commande, --outil)
NOM_EXE_FENETRE = "PlaceurRouteur.exe"  # exécutable fenêtre du paquet (double-clic)

# moteurs Rust : nom -> dossier de la crate, relatif à PCB_V2/outils (mode source)
MOTEURS = {"placeur_routage": "placeur_routage", "serigraphie": "serigraphie"}

# liste blanche de l'aiguillage --outil (§5.2)
OUTILS_AIGUILLES = (
    "importer_carte", "deduire_config", "construire_donnees", "extraire_reference", "verifier_placement",
    "drc_maison", "vias_couture", "mesures", "proprete", "mesure_convertisseurs", "visualiser", "generer_pcbdoc",
    "generer_kicad", "drc_serigraphie", "formats",
)

MESSAGE_MOTEUR_ABSENT = "Moteur absent, peut-être mis en quarantaine par l'antivirus."   # §5.3
NOM_SEMAPHORE = "PlaceurRouteur_moteurs"   # §5.3 : Local\PlaceurRouteur_moteurs
COMMIT_INCONNU = "inconnu"

ESSAIS_ECRITURE = 6         # écriture atomique : 1 essai puis 5 réessais (§0.2-5)
PAUSE_ECRITURE_S = 0.2      # entre deux essais

NICE_PRIORITE_BASSE = 10    # hors Windows : « inférieure à la normale »
PLACES_MAX = 64             # MAXIMUM_WAIT_OBJECTS de WaitForMultipleObjects

# drapeaux de CreateProcess (Windows)
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020

# Sous-processus : les autres modules passent par ce module seulement (§5.2 ; placeur.py n'importe pas subprocess).
PIPE = subprocess.PIPE
STDOUT = subprocess.STDOUT
DEVNULL = subprocess.DEVNULL
DelaiDepasse = subprocess.TimeoutExpired          # délai de `executer` dépassé (processus tué)
ErreurSousProcessus = subprocess.SubprocessError  # classe de base des erreurs de sous-processus

ICI = os.path.dirname(os.path.abspath(__file__))   # PCB_V2/outils/placeur_routage en mode source
OUTILS = os.path.dirname(ICI)                       # PCB_V2/outils en mode source

WINDOWS = os.name == "nt"


class RessourceAbsente(FileNotFoundError):
    """Ressource du programme introuvable (paquet incomplet, dépôt partiel)."""


class MoteurAbsent(FileNotFoundError):
    """Exécutable d'un moteur introuvable ou impossible à construire."""


class ErreurConstruction(RuntimeError):
    """`cargo build` a échoué (mode source)."""


class VerrouOccupe(RuntimeError):
    """Le verrou est tenu par un autre processus."""


# ------------------------------------------------------------------------------------------------ mode et emplacements

def est_fige() -> bool:
    """Vrai dans le paquet PyInstaller (exécutables figés), faux en mode source."""
    return bool(getattr(sys, "frozen", False))


def dossier_interne() -> str:
    """Dossier des modules et ressources internes : `_internal` du paquet (mode figé), placeur_routage (source)."""
    if est_fige():
        meipass = getattr(sys, "_MEIPASS", None)
        return os.path.abspath(meipass) if meipass else os.path.dirname(os.path.abspath(sys.executable))
    return ICI


def racine_paquet() -> str:
    """Racine des fichiers livrés : dossier des exécutables (mode figé), PCB_V2/outils (mode source)."""
    if est_fige():
        return os.path.dirname(os.path.abspath(sys.executable))
    return OUTILS


def nom_executable(nom: str) -> str:
    """Nom de fichier d'un exécutable (« .exe » sous Windows)."""
    return nom + ".exe" if WINDOWS else nom


def _relatif_sur(nom: str) -> str:
    """Chemin relatif sans « .. » ni lecteur, pour qu'une ressource ne sorte jamais du programme."""
    if not isinstance(nom, str) or not nom.strip():
        raise ValueError("nom de ressource vide")
    if os.path.isabs(nom) or re.match(r"^[A-Za-z]:", nom) or nom[:1] in ("\\", "/"):
        raise ValueError(f"nom de ressource invalide (chemin absolu) : {os.path.basename(nom)}")
    parties = [p for p in re.split(r"[\\/]+", nom) if p not in ("", ".")]
    if not parties or any(p == ".." for p in parties):
        raise ValueError(f"nom de ressource invalide : {nom}")
    return os.path.join(*parties)


def ressource(nom: str, doit_exister: bool = True) -> str:
    """Chemin absolu de la ressource `nom` (chemin relatif, séparateur / ou \\).

    Mode figé : `_internal`, puis le dossier des exécutables. Mode source : placeur_routage, puis PCB_V2/outils.
    `doit_exister` : sinon `RessourceAbsente` (message sans chemin) ; avec False, rend le premier emplacement.
    """
    rel = _relatif_sur(nom)
    bases = (dossier_interne(), racine_paquet()) if est_fige() else (ICI, OUTILS)
    for base in bases:
        chemin = os.path.join(base, rel)
        if os.path.exists(chemin):
            return chemin
    if doit_exister:
        conseil = " : paquet incomplet, extraire de nouveau tout le zip" if est_fige() else ""
        raise RessourceAbsente(f"ressource absente : {nom}{conseil}")
    return os.path.join(bases[0], rel)


def interpreteur() -> str:
    """Interpréteur Python des sous-processus en mode source (python.exe plutôt que pythonw.exe)."""
    exe = sys.executable
    dossier, base = os.path.split(exe)
    if base.lower() == "pythonw.exe":
        console = os.path.join(dossier, "python.exe")
        if os.path.isfile(console):
            return console
    return exe


def commande_outil(nom: str, *args) -> list[str]:
    """Commande d'un outil Python de la liste blanche, en processus séparé (§5.2, aiguillage de lanceur.py).

    Mode figé : [placeur.exe, --outil, nom, …] ; mode source : [python, lanceur.py, --outil, nom, …].
    """
    if nom not in OUTILS_AIGUILLES:
        raise ValueError(f"outil inconnu : {nom!r} (liste blanche : {', '.join(OUTILS_AIGUILLES)})")
    return commande_placeur("--outil", nom, *args)


def commande_placeur(*args) -> list[str]:
    """Commande de l'orchestrateur (fenêtre, essais) : placeur.exe (figé) ou python lanceur.py (source)."""
    args = [str(a) for a in args]
    if est_fige():
        return [os.path.join(racine_paquet(), NOM_EXE_CONSOLE)] + args
    return [interpreteur(), os.path.join(ICI, "lanceur.py")] + args


def chemin_moteur(nom: str, dossier: str | None = None, construire: bool = True, journal=None) -> str:
    """Chemin absolu de l'exécutable du moteur `nom` (« placeur_routage » ou « serigraphie »).

    - `dossier` (option --moteurs) : `<dossier>/<nom>(.exe)`, sans construction ;
    - mode figé : `_internal/moteurs/<nom>.exe`, jamais de cargo ;
    - mode source : `<crate>/target/release/<nom>(.exe)`, construit par cargo si `construire` (cargo décide
      lui-même si l'exécutable est périmé). `construire=False` : aucun cargo lancé (--essais 0, --analyser).
    `journal` : fonction qui reçoit les messages de construction.
    """
    if nom not in MOTEURS:
        raise ValueError(f"moteur inconnu : {nom!r} (attendu : {', '.join(MOTEURS)})")
    exe = nom_executable(nom)
    if dossier is not None:
        chemin = os.path.abspath(os.path.join(dossier, exe))
        if not os.path.isfile(chemin):
            raise MoteurAbsent(f"{MESSAGE_MOTEUR_ABSENT} ({exe} introuvable dans le dossier --moteurs)")
        return chemin
    if est_fige():
        chemin = os.path.join(dossier_interne(), "moteurs", exe)
        if not os.path.isfile(chemin):
            raise MoteurAbsent(f"{MESSAGE_MOTEUR_ABSENT} ({exe})")
        return chemin
    crate = os.path.join(OUTILS, MOTEURS[nom])
    chemin = os.path.join(crate, "target", "release", exe)
    if construire:
        construire_moteur(nom, journal=journal)
    if not os.path.isfile(chemin):
        raise MoteurAbsent(f"Moteur absent : {exe} n'est pas construit (cargo build --release --offline dans "
                           f"{MOTEURS[nom]}).")
    return chemin


def construire_moteur(nom: str, journal=None) -> None:
    """Mode source : `cargo build --release --offline` de la crate `nom`, avec PLACEUR_COMMIT (§0.2-2, variable de
    construction). Cargo absent : l'exécutable existant est gardé (avec un message), sinon `MoteurAbsent`."""
    if est_fige():
        return
    crate = os.path.join(OUTILS, MOTEURS[nom])
    exe = os.path.join(crate, "target", "release", nom_executable(nom))
    env = environnement_sous_processus(complet=True)
    env["PLACEUR_COMMIT"] = commit()
    cmd = ["cargo", "build", "--release", "--offline", "--target-dir", os.path.join(crate, "target")]
    if journal:
        journal(f"construction du moteur {nom} (cargo build --release --offline)")
    try:
        r = executer(cmd, cwd=crate, environnement=env, priorite_basse=False)
    except FileNotFoundError:
        if os.path.isfile(exe):
            if journal:
                journal(f"cargo introuvable : moteur {nom} existant utilisé sans reconstruction")
            return
        raise MoteurAbsent(f"Moteur absent : {nom_executable(nom)} n'est pas construit et cargo est introuvable "
                           f"(installer Rust : mode d'emploi, section 9).") from None
    if r.returncode != 0:
        fin = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-25:])
        raise ErreurConstruction(f"construction du moteur {nom} impossible (cargo, code {r.returncode}) :\n"
                                 + anonymiser(fin))


def commande_moteur(nom: str, *args, dossier: str | None = None, construire: bool = True, journal=None) -> list[str]:
    """Commande d'un moteur : [exécutable, *args]. Point unique : les tests le remplacent par un faux moteur."""
    return [chemin_moteur(nom, dossier=dossier, construire=construire, journal=journal)] + [str(a) for a in args]


# ------------------------------------------------------------------------------------------------ version

_COMMITS: dict = {}


def lire_version() -> str:
    """Version du programme, lue dans VERSION (source unique, §5.5) : « X.Y.Z »."""
    with open(ressource("VERSION"), encoding="utf-8-sig") as f:
        texte = f.read().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", texte):
        raise ValueError(f"VERSION invalide : {texte[:40]!r} (attendu X.Y.Z)")
    return texte


def commit() -> str:
    """7 premiers caractères hexadécimaux du commit : `_internal/COMMIT` en mode figé (écrit à la construction du
    paquet), `git rev-parse HEAD` en mode source ; « inconnu » à défaut.
    Seul un commit lu est gardé en mémoire, par mode et par dossier (« inconnu » n'est jamais mis en cache : un
    échec passager, ou un test qui remplace `executer` ou `OUTILS`, ne fige pas la valeur du processus)."""
    cle = (est_fige(), dossier_interne(), OUTILS)
    if cle in _COMMITS:
        return _COMMITS[cle]
    valeur = ""
    if est_fige():
        try:
            with open(ressource("COMMIT"), encoding="utf-8-sig") as f:
                valeur = f.read().strip()
        except OSError:
            valeur = ""
    else:
        try:
            r = executer(["git", "rev-parse", "HEAD"], cwd=OUTILS, delai_s=20, priorite_basse=False)
            if r.returncode == 0:
                valeur = r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            valeur = ""
    valeur = valeur.lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", valeur):
        return COMMIT_INCONNU
    _COMMITS[cle] = valeur[:7]
    return valeur[:7]


def version_lanceur() -> str:
    """« 2.0.0+abc1234 » : la version que le moteur doit écrire à l'identique après « placeur_routage » (§5.5)."""
    return f"{lire_version()}+{commit()}"


# ------------------------------------------------------------------------------------------------ Windows (ctypes)

_K32 = None
_TYPES = {}


def _kernel32():
    """kernel32 avec les prototypes utilisés ici (poignées 64 bits : restype et argtypes explicites)."""
    global _K32
    if _K32 is not None:
        return _K32
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)

    def proto(nom, restype, *argtypes):
        f = getattr(k, nom)
        f.restype = restype
        f.argtypes = list(argtypes)

    proto("CreateJobObjectW", w.HANDLE, ctypes.c_void_p, w.LPCWSTR)
    proto("SetInformationJobObject", w.BOOL, w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
    proto("QueryInformationJobObject", w.BOOL, w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD,
          ctypes.POINTER(w.DWORD))
    proto("AssignProcessToJobObject", w.BOOL, w.HANDLE, w.HANDLE)
    proto("TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)
    proto("OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    proto("TerminateProcess", w.BOOL, w.HANDLE, w.UINT)
    proto("CloseHandle", w.BOOL, w.HANDLE)
    proto("WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
    proto("WaitForMultipleObjects", w.DWORD, w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD)
    proto("CreateMutexW", w.HANDLE, ctypes.c_void_p, w.BOOL, w.LPCWSTR)
    proto("ReleaseMutex", w.BOOL, w.HANDLE)
    proto("CreateToolhelp32Snapshot", w.HANDLE, w.DWORD, w.DWORD)
    proto("Thread32First", w.BOOL, w.HANDLE, ctypes.c_void_p)
    proto("Thread32Next", w.BOOL, w.HANDLE, ctypes.c_void_p)
    proto("OpenThread", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    proto("ResumeThread", w.DWORD, w.HANDLE)
    proto("SetThreadExecutionState", w.DWORD, w.DWORD)
    proto("GetCurrentProcess", w.HANDLE)
    proto("GetPriorityClass", w.DWORD, w.HANDLE)
    proto("GetComputerNameExW", w.BOOL, ctypes.c_int, w.LPWSTR, ctypes.POINTER(w.DWORD))
    proto("GetShortPathNameW", w.DWORD, w.LPCWSTR, w.LPWSTR, w.DWORD)
    proto("GetLongPathNameW", w.DWORD, w.LPCWSTR, w.LPWSTR, w.DWORD)
    proto("GetTempPathW", w.DWORD, w.DWORD, w.LPWSTR)
    _K32 = k
    return k


def _structures():
    """Structures Win32 (créées une seule fois)."""
    if _TYPES:
        return _TYPES
    import ctypes
    from ctypes import wintypes as w

    class LimiteBase(ctypes.Structure):          # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

    class CompteursES(ctypes.Structure):         # IO_COUNTERS
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                  "OtherOperationCount", "ReadTransferCount",
                                                  "WriteTransferCount", "OtherTransferCount")]

    class LimiteEtendue(ctypes.Structure):       # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [("BasicLimitInformation", LimiteBase), ("IoInfo", CompteursES),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class Comptabilite(ctypes.Structure):        # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", w.DWORD), ("TotalProcesses", w.DWORD),
                    ("ActiveProcesses", w.DWORD), ("TotalTerminatedProcesses", w.DWORD)]

    class EntreeFil(ctypes.Structure):           # THREADENTRY32
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                    ("th32OwnerProcessID", w.DWORD), ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", w.DWORD)]

    class Guid(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    _TYPES.update(LimiteEtendue=LimiteEtendue, Comptabilite=Comptabilite, EntreeFil=EntreeFil, Guid=Guid)
    return _TYPES


def _erreur_windows(fonction: str) -> OSError:
    import ctypes
    code = ctypes.get_last_error()
    return OSError(code, f"{fonction} : erreur Windows {code}")


def _reprendre_processus(pid: int) -> int:
    """Relance les fils d'un processus créé suspendu ; rend le nombre de fils relancés."""
    import ctypes
    k = _kernel32()
    entree_t = _structures()["EntreeFil"]
    instantane = k.CreateToolhelp32Snapshot(0x00000004, 0)   # TH32CS_SNAPTHREAD
    if not instantane or instantane == ctypes.c_void_p(-1).value:
        raise _erreur_windows("CreateToolhelp32Snapshot")
    relances = 0
    try:
        e = entree_t()
        e.dwSize = ctypes.sizeof(entree_t)
        ok = k.Thread32First(instantane, ctypes.byref(e))
        while ok:
            if e.th32OwnerProcessID == pid:
                h = k.OpenThread(0x0002, False, e.th32ThreadID)          # THREAD_SUSPEND_RESUME
                if h:
                    try:
                        if k.ResumeThread(h) != 0xFFFFFFFF:
                            relances += 1
                    finally:
                        k.CloseHandle(h)
            ok = k.Thread32Next(instantane, ctypes.byref(e))
    finally:
        k.CloseHandle(instantane)
    return relances


# ------------------------------------------------------------------------------------------------ sous-processus

_ENV_WINDOWS = (
    "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ALLUSERSPROFILE", "PUBLIC",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "OS",
    "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN",
)
_ENV_POSIX = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN")


def variables_transmises() -> tuple[str, ...]:
    """Liste blanche des variables d'environnement transmises aux sous-processus."""
    return _ENV_WINDOWS if WINDOWS else _ENV_POSIX


def environnement_sous_processus(complet: bool = False) -> dict:
    """Environnement des sous-processus (seule lecture de l'environnement de ce module, §0.2-2).

    Par défaut : les seules variables du système de `variables_transmises()` (emplacements du système, du profil
    pour l'interpréteur Python et cargo, chemin de recherche). Aucune variable de réglage n'est transmise : ni
    PYTHON*, ni RAYON_NUM_THREADS, ni RUST_MIN_STACK, ni les variables de l'annexe A. Les programmes lancés ne
    reçoivent leurs réglages que par leurs options et leurs fichiers.
    `complet=True` : copie de tout l'environnement, réservée à `cargo build` en mode source (chaîne d'outils du
    développeur : CARGO_HOME, RUSTUP_TOOLCHAIN, compilateur C…) ; le moteur construit n'en lit rien à l'exécution.
    """
    source = os.environ
    if complet:
        return dict(source)
    garder = {n.upper() for n in variables_transmises()}
    return {cle: valeur for cle, valeur in source.items() if cle.upper() in garder}


def _baisser_priorite():  # pragma: no cover - exécuté dans l'enfant, entre fork et exec (hors Windows)
    os.nice(NICE_PRIORITE_BASSE)


def lancer(cmd, *, cwd=None, stdin=subprocess.DEVNULL, stdout=None, stderr=None, texte: bool = True,
           priorite_basse: bool = True, job: "Job | None" = None, environnement: dict | None = None):
    """Lance un sous-processus (subprocess.Popen) selon §0.2-10 et §5.3.

    - sorties texte en UTF-8, caractères invalides remplacés (`texte=True`) ;
    - sans fenêtre de console (CREATE_NO_WINDOW) ; entrée standard vide par défaut ;
    - priorité « inférieure à la normale » (Windows : BELOW_NORMAL_PRIORITY_CLASS ; ailleurs : nice +10), sauf
      `priorite_basse=False` (--priorite-normale) ;
    - environnement réduit (`environnement_sous_processus()`), sauf `environnement` explicite ;
    - `job` : sous Windows, le processus est créé suspendu, rattaché à l'objet de travail, puis relancé ; ses
      descendants appartiennent donc tous à l'objet de travail, sans fenêtre de course. Ailleurs, le processus
      ouvre sa propre session (groupe de processus) pour être arrêté avec ses descendants.
    """
    cmd = [str(c) for c in cmd]
    env = environnement_sous_processus() if environnement is None else environnement
    options = dict(cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr, env=env, close_fds=True)
    if texte:
        options.update(encoding="utf-8", errors="replace")
    if WINDOWS:
        drapeaux = CREATE_NO_WINDOW
        if priorite_basse:
            drapeaux |= BELOW_NORMAL_PRIORITY_CLASS
        if job is not None:
            drapeaux |= CREATE_SUSPENDED
        options["creationflags"] = drapeaux
        p = subprocess.Popen(cmd, **options)
        if job is not None:
            try:
                if not job.rattacher(p):
                    raise _erreur_windows("AssignProcessToJobObject")
                if _reprendre_processus(p.pid) < 1:
                    raise OSError("processus suspendu impossible à relancer")
            except BaseException:
                p.kill()
                p.wait()
                raise
        return p
    options["start_new_session"] = True
    if priorite_basse:
        options["preexec_fn"] = _baisser_priorite
    p = subprocess.Popen(cmd, **options)
    if job is not None:
        job.rattacher(p)
    return p


def executer(cmd, *, delai_s: float | None = None, cwd=None, entree: str | None = None, priorite_basse: bool = True,
             environnement: dict | None = None, job: "Job | None" = None) -> subprocess.CompletedProcess:
    """Lance `cmd`, attend sa fin et rend un CompletedProcess (sorties texte UTF-8).
    Délai dépassé : le processus est tué (avec son groupe hors Windows ; jamais tout l'objet de travail, qui peut
    être partagé), puis subprocess.TimeoutExpired ; si des descendants tiennent encore ses sorties, elles sont
    abandonnées au bout de 5 s."""
    p = lancer(cmd, cwd=cwd, stdin=subprocess.PIPE if entree is not None else subprocess.DEVNULL,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, priorite_basse=priorite_basse,
               environnement=environnement, job=job)
    try:
        sortie, erreur = p.communicate(entree, timeout=delai_s)
    except subprocess.TimeoutExpired:
        _tuer(p)
        try:
            sortie, erreur = p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            sortie, erreur = "", ""
            p.wait()
        raise subprocess.TimeoutExpired(p.args, delai_s, sortie, erreur) from None
    return subprocess.CompletedProcess(p.args, p.returncode, sortie, erreur)


def _tuer(p) -> None:
    """Tue un processus lancé par `lancer` (avec son groupe hors Windows)."""
    if not WINDOWS:
        import signal
        try:
            if os.getpgid(p.pid) == p.pid:
                os.killpg(p.pid, signal.SIGKILL)
                return
        except (ProcessLookupError, PermissionError):
            pass
    try:
        p.kill()
    except OSError:
        pass


def processus_vivant(pid: int) -> bool:
    """Vrai si le processus `pid` existe et n'est pas terminé."""
    if WINDOWS:
        import ctypes
        k = _kernel32()
        h = k.OpenProcess(0x00100000 | 0x1000, False, int(pid))   # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5                     # accès refusé : il existe
        try:
            return k.WaitForSingleObject(h, 0) == 0x00000102         # WAIT_TIMEOUT : toujours en cours
        finally:
            k.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="ascii", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"     # processus zombie : terminé
    except (OSError, IndexError):
        return True


def priorite_processus() -> int:
    """Priorité du processus courant : classe Windows (0x4000 = inférieure à la normale) ou valeur nice."""
    if WINDOWS:
        k = _kernel32()
        return int(k.GetPriorityClass(k.GetCurrentProcess()))
    return os.nice(0)


class Job:
    """Ensemble de processus arrêtés ensemble (§5.3 « arrêt forcé »).

    Windows : objet de travail « tuer à la fermeture » (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) : quand sa dernière
    poignée se ferme (fermeture normale, plantage ou arrêt forcé du processus qui le tient), tous ses processus et
    leurs descendants sont tués. Les plantages des processus rattachés ne montrent pas de boîte de dialogue
    (JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION). Les objets de travail imbriqués (Windows 8 et suivants)
    permettent à un processus déjà rattaché (l'orchestrateur lancé par la fenêtre) d'en créer un pour ses moteurs.
    Ailleurs : chaque processus lancé par `lancer(job=…)` ouvre sa session ; `tout_arreter` et `fermer` tuent ces
    groupes (killpg), et `fermer` est appelé à la sortie normale de l'interpréteur. Un arrêt brutal (SIGKILL) du
    processus qui tient l'objet n'est pas couvert hors Windows.
    """

    def __init__(self):
        self._poignee = None
        self._groupes: set[int] = set()
        self._ferme = False
        if WINDOWS:
            import ctypes
            k = _kernel32()
            h = k.CreateJobObjectW(None, None)
            if not h:
                raise _erreur_windows("CreateJobObjectW")
            info = _structures()["LimiteEtendue"]()
            info.BasicLimitInformation.LimitFlags = 0x00002000 | 0x00000400   # KILL_ON_JOB_CLOSE | DIE_ON_UNHANDLED
            if not k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                erreur = _erreur_windows("SetInformationJobObject")
                k.CloseHandle(h)
                raise erreur
            self._poignee = h
        else:
            import atexit
            atexit.register(self.fermer)

    def rattacher(self, popen) -> bool:
        """Rattache un processus déjà lancé. Préférer `lancer(…, job=self)`, sans fenêtre de course."""
        if self._ferme:
            return False
        if WINDOWS:
            k = _kernel32()
            hp = k.OpenProcess(0x0100 | 0x0001, False, popen.pid)   # PROCESS_SET_QUOTA | PROCESS_TERMINATE
            if not hp:
                return False
            try:
                return bool(k.AssignProcessToJobObject(self._poignee, hp))
            finally:
                k.CloseHandle(hp)
        try:
            if os.getpgid(popen.pid) != popen.pid:
                return False
        except ProcessLookupError:
            return False
        self._groupes.add(popen.pid)
        return True

    def nombre_actifs(self) -> int:
        """Nombre de processus vivants dans l'objet. Windows : compte exact, qui inclut les hôtes de console
        (conhost.exe) des programmes en mode console ; ailleurs : nombre de groupes non vides."""
        if self._ferme:
            return 0
        if WINDOWS:
            import ctypes
            k = _kernel32()
            info = _structures()["Comptabilite"]()
            if not k.QueryInformationJobObject(self._poignee, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise _erreur_windows("QueryInformationJobObject")
            return int(info.ActiveProcesses)
        return len(self.pids())

    def pids(self) -> list[int]:
        """Identifiants des processus vivants de l'objet. Windows : tous, descendants et hôtes de console compris ;
        ailleurs : les meneurs des groupes rattachés dont le groupe a encore un membre."""
        if self._ferme:
            return []
        if WINDOWS:
            import ctypes
            from ctypes import wintypes as w
            k = _kernel32()
            capacite = 64
            while True:
                class ListeIdentifiants(ctypes.Structure):   # JOBOBJECT_BASIC_PROCESS_ID_LIST
                    _fields_ = [("NumberOfAssignedProcesses", w.DWORD), ("NumberOfProcessIdsInList", w.DWORD),
                                ("ProcessIdList", ctypes.c_size_t * capacite)]
                liste = ListeIdentifiants()
                ok = k.QueryInformationJobObject(self._poignee, 3, ctypes.byref(liste), ctypes.sizeof(liste), None)
                if ok or ctypes.get_last_error() != 234:                 # ERROR_MORE_DATA : agrandir
                    if not ok:
                        raise _erreur_windows("QueryInformationJobObject")
                    return [int(liste.ProcessIdList[i]) for i in range(liste.NumberOfProcessIdsInList)]
                capacite = max(2 * capacite, int(liste.NumberOfAssignedProcesses) + 16)
        vivants = []
        for groupe in sorted(self._groupes):
            try:
                os.killpg(groupe, 0)
                vivants.append(groupe)
            except (ProcessLookupError, PermissionError):
                self._groupes.discard(groupe)
        return vivants

    def tout_arreter(self, code: int = 1) -> None:
        """Tue immédiatement tous les processus rattachés et leurs descendants (code de sortie `code` sous Windows)."""
        if self._ferme:
            return
        if WINDOWS:
            _kernel32().TerminateJobObject(self._poignee, int(code))
            return
        import signal
        for groupe in list(self._groupes):
            try:
                os.killpg(groupe, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def fermer(self) -> None:
        """Ferme l'objet : les processus encore vivants sont tués (« tuer à la fermeture »)."""
        if self._ferme:
            return
        if WINDOWS:
            _kernel32().CloseHandle(self._poignee)
            self._poignee = None
        else:
            self.tout_arreter()
            self._groupes.clear()
        self._ferme = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fermer()
        return False


# ------------------------------------------------------------------------------------------------ verrou et sémaphore

class Verrou:
    """Verrou exclusif d'un fichier (§5.3 : `<sortie>/.verrou`), libéré par le système si le processus meurt.

    Windows : msvcrt.locking sur le premier octet ; ailleurs : fcntl.flock. Le descripteur n'est pas hérité par les
    sous-processus (PEP 446). Deux instances, même dans un seul processus, s'excluent.
    """

    def __init__(self, chemin: str):
        self.chemin = os.path.abspath(chemin)
        self._fd = None

    @property
    def tenu(self) -> bool:
        return self._fd is not None

    def acquerir(self) -> bool:
        """Prend le verrou sans attendre : vrai si pris, faux s'il est tenu ailleurs."""
        if self._fd is not None:
            return True
        fd = os.open(self.chemin, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if WINDOWS:
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if isinstance(e, (PermissionError, BlockingIOError)) or e.errno in (11, 13, 35, 36):
                return False
            raise
        self._fd = fd
        return True

    def liberer(self, supprimer: bool = False) -> None:
        """Rend le verrou ; `supprimer` efface ensuite le fichier (sans erreur s'il est tenu ailleurs)."""
        if self._fd is None:
            return
        try:
            if WINDOWS:
                import msvcrt
                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
        if supprimer:
            try:
                os.remove(self.chemin)
            except OSError:
                pass

    def __enter__(self):
        if not self.acquerir():
            raise VerrouOccupe("Un calcul est déjà en cours dans ce dossier.")
        return self

    def __exit__(self, *exc):
        self.liberer()
        return False


class SemaphoreMoteurs:
    """Nombre total de moteurs lancés dans la session (§5.3 : « Local\\PlaceurRouteur_moteurs »).

    Sémaphore à `places` places, chaque place étant un mutex nommé `Local\\<nom>_<k>` (Windows) ou un fichier
    verrouillé par flock (ailleurs, dans /tmp, un dossier par utilisateur). Une place tenue par un processus tué est
    rendue par le système (mutex abandonné, verrou de fichier libéré), ce que ne fait pas un sémaphore Win32 : un
    orchestrateur arrêté de force (« Tout arrêter ») ne bloque jamais les autres.
    Chaque orchestrateur prend ses places parmi les `places` premières, `places` venant de son --processeurs : deux
    orchestrateurs de même --processeurs P n'ont jamais plus de P moteurs vivants ensemble ; en général, le total
    de la session ne dépasse pas le plus grand P demandé. Au plus 64 places.
    Windows : une place se rend depuis le fil qui l'a prise (propriété d'un mutex).
    """

    def __init__(self, places: int, nom: str = NOM_SEMAPHORE):
        if not re.fullmatch(r"[A-Za-z0-9_]{1,100}", nom or ""):
            raise ValueError(f"nom de sémaphore invalide : {nom!r}")
        self.places = max(1, min(int(places), PLACES_MAX))
        self.nom = nom
        self._mutex: list = []
        self._tenues: dict[int, object] = {}
        self._dossier = None
        if WINDOWS:
            k = _kernel32()
            for i in range(self.places):
                h = k.CreateMutexW(None, False, f"Local\\{nom}_{i}")
                if not h:
                    erreur = _erreur_windows("CreateMutexW")
                    self.fermer()
                    raise erreur
                self._mutex.append(h)
        else:
            self._dossier = os.path.join("/tmp", f"{nom}-{os.getuid()}")
            os.makedirs(self._dossier, mode=0o700, exist_ok=True)

    def prendre(self, delai_s: float | None = None) -> int | None:
        """Prend une place libre ; rend son numéro, ou None si `delai_s` expire (None : attente sans fin)."""
        fin = None if delai_s is None else time.monotonic() + max(0.0, float(delai_s))
        if WINDOWS:
            import ctypes
            from ctypes import wintypes as w
            k = _kernel32()
            libres = [i for i in range(self.places) if i not in self._tenues]
            if not libres:
                return None
            poignees = (w.HANDLE * len(libres))(*[self._mutex[i] for i in libres])
            while True:
                if fin is None:
                    attente = 0xFFFFFFFF                                    # INFINITE
                else:
                    attente = max(0, int((fin - time.monotonic()) * 1000))
                r = k.WaitForMultipleObjects(len(libres), poignees, False, attente)
                if r < len(libres):                                         # WAIT_OBJECT_0 + i
                    i = libres[r]
                elif 0x80 <= r < 0x80 + len(libres):                        # WAIT_ABANDONED_0 + i : tenant mort
                    i = libres[r - 0x80]
                elif r == 0x00000102:                                       # WAIT_TIMEOUT
                    return None
                else:
                    raise _erreur_windows("WaitForMultipleObjects")
                self._tenues[i] = threading.get_ident()
                return i
        import fcntl
        while True:
            for i in range(self.places):
                if i in self._tenues:
                    continue
                fd = os.open(os.path.join(self._dossier, f"{i}.verrou"), os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)
                    continue
                self._tenues[i] = fd
                return i
            if fin is not None and time.monotonic() >= fin:
                return None
            time.sleep(0.1)

    def rendre(self, place: int) -> None:
        """Rend la place `place`, prise par `prendre`."""
        if place not in self._tenues:
            raise ValueError(f"place {place} non tenue")
        tenant = self._tenues[place]
        if WINDOWS:
            if tenant != threading.get_ident():
                raise RuntimeError("une place se rend depuis le fil qui l'a prise")
            if not _kernel32().ReleaseMutex(self._mutex[place]):
                raise _erreur_windows("ReleaseMutex")
        else:
            import fcntl
            try:
                fcntl.flock(tenant, fcntl.LOCK_UN)
            finally:
                os.close(tenant)
        del self._tenues[place]

    def tenues(self) -> list[int]:
        return sorted(self._tenues)

    def fermer(self) -> None:
        """Rend les places tenues (depuis ce fil) et ferme les poignées."""
        for place in list(self._tenues):
            try:
                self.rendre(place)
            except (OSError, RuntimeError):
                self._tenues.pop(place, None)
        if WINDOWS:
            k = _kernel32()
            for h in self._mutex:
                k.CloseHandle(h)
        self._mutex = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fermer()
        return False


def empecher_veille(oui: bool = True) -> bool:
    """Empêche (ou autorise de nouveau) la mise en veille pendant un calcul (SetThreadExecutionState, §5.3).

    L'état est attaché au fil appelant : appeler depuis le fil principal, qui vit tout le calcul. Rend vrai si
    l'appel a réussi ; toujours faux hors Windows (sans effet).
    """
    if not WINDOWS:
        return False
    es_continuous, es_system_required = 0x80000000, 0x00000001
    etat = es_continuous | (es_system_required if oui else 0)
    return _kernel32().SetThreadExecutionState(etat) != 0


def sur_batterie() -> bool | None:
    """Vrai si le PC fonctionne sur batterie (GetSystemPowerStatus, ACLineStatus = 0) ; None si l'état est inconnu
    (hors Windows, ou alimentation non signalée). Sert à l'avertissement « Le PC est sur batterie. » de §5.3."""
    if not WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes as w

    class EtatAlimentation(ctypes.Structure):   # SYSTEM_POWER_STATUS
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", w.DWORD), ("BatteryFullLifeTime", w.DWORD)]
    etat = EtatAlimentation()
    fonction = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemPowerStatus
    fonction.argtypes = [ctypes.POINTER(EtatAlimentation)]
    fonction.restype = w.BOOL
    if not fonction(ctypes.byref(etat)):
        return None
    return {0: True, 1: False}.get(etat.ACLineStatus)


# ------------------------------------------------------------------------------------------------ écritures atomiques

def ecrire_atomique(chemin: str, contenu, *, encodage: str = "utf-8", bom: bool = False, crlf: bool = False) -> None:
    """Écrit `contenu` (bytes ou str) dans `<chemin>.tmp`, puis le renomme en `chemin` (os.replace).

    Chaque étape est réessayée 5 fois toutes les 0,2 s sur PermissionError (antivirus, outil de synchronisation
    qui tient le fichier, §0.2-5). En cas d'échec final, `<chemin>.tmp` est effacé, `chemin` est intact et la
    dernière PermissionError est levée. Texte : `bom` ajoute l'indicateur d'ordre des octets, `crlf` écrit les fins
    de ligne CRLF (fichiers destinés au bloc-notes).
    """
    if isinstance(contenu, str):
        if crlf:
            contenu = contenu.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        donnees = (("\ufeff" if bom else "") + contenu).encode(encodage)
    else:
        donnees = bytes(contenu)
    temporaire = chemin + ".tmp"

    def ecrire_temporaire():
        with open(temporaire, "wb") as f:
            f.write(donnees)
            f.flush()
            os.fsync(f.fileno())

    try:
        _reessayer(ecrire_temporaire)
        _reessayer(lambda: os.replace(temporaire, chemin))
    except BaseException:
        try:
            os.remove(temporaire)
        except OSError:
            pass
        raise


def _reessayer(action):
    for essai in range(ESSAIS_ECRITURE):
        try:
            return action()
        except PermissionError:
            if essai + 1 >= ESSAIS_ECRITURE:
                raise
            time.sleep(PAUSE_ECRITURE_S)


def ecrire_json(chemin: str, objet, indent: int | None = 1) -> None:
    """JSON en UTF-8 sans BOM, fins de ligne LF, ensure_ascii=False, écriture atomique (§0.2-5)."""
    import json
    ecrire_atomique(chemin, json.dumps(objet, ensure_ascii=False, indent=indent) + "\n")


def ecrire_texte_bloc_notes(chemin: str, texte: str) -> None:
    """Texte destiné au bloc-notes (.ini, rapport.txt, 0_LIRE_AVANT.txt) : UTF-8 avec BOM, CRLF, atomique."""
    ecrire_atomique(chemin, texte, bom=True, crlf=True)


# ------------------------------------------------------------------------------------------------ système

_FOLDERID = {
    "profil": "5E6C858F-0E22-4760-9AFE-EA3317B67173",        # FOLDERID_Profile
    "appdata": "3EB685DB-65F9-4CF6-A03A-E3EF65729F3D",       # FOLDERID_RoamingAppData (%APPDATA%)
    "localappdata": "F1B32785-6FBA-4FCF-9D55-7B8E7F157091",  # FOLDERID_LocalAppData (%LOCALAPPDATA%)
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",     # FOLDERID_Documents
    "onedrive": "A52BBA46-E9E1-435F-B3D9-28DAA648C0F6",      # FOLDERID_SkyDrive
}
_DOSSIERS: dict = {}


def dossier_connu(nom: str) -> str | None:
    """Dossier connu du système, sans lire de variable d'environnement (§0.2-2).

    `nom` : « profil », « appdata », « localappdata », « documents », « onedrive ». Windows : SHGetKnownFolderPath ;
    ailleurs : dossier personnel de la base des comptes (pwd), puis ~/.config, ~/.local/share, ~/Documents.
    Rend None si le dossier n'est pas défini (OneDrive non configuré, par exemple).
    """
    if nom not in _FOLDERID:
        raise ValueError(f"dossier connu inconnu : {nom!r}")
    if nom in _DOSSIERS:
        return _DOSSIERS[nom]
    resultat = _dossier_connu_windows(nom) if WINDOWS else _dossier_connu_posix(nom)
    _DOSSIERS[nom] = resultat
    return resultat


def _dossier_connu_windows(nom: str) -> str | None:
    import ctypes
    from ctypes import wintypes as w
    guid_t = _structures()["Guid"]
    texte = _FOLDERID[nom].replace("-", "")
    guid = guid_t(int(texte[0:8], 16), int(texte[8:12], 16), int(texte[12:16], 16),
                  (ctypes.c_ubyte * 8)(*bytes.fromhex(texte[16:32])))
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32")
    fonction = shell32.SHGetKnownFolderPath
    fonction.argtypes = [ctypes.POINTER(guid_t), w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    fonction.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    pointeur = ctypes.c_void_p()
    hr = fonction(ctypes.byref(guid), 0x00004000, None, ctypes.byref(pointeur))   # KF_FLAG_DONT_VERIFY
    try:
        if hr != 0 or not pointeur.value:
            return None
        return ctypes.wstring_at(pointeur.value) or None
    finally:
        if pointeur.value:
            ole32.CoTaskMemFree(pointeur)


def dossier_temporaire() -> str | None:
    """Dossier temporaire du système, tel que Windows le donne (GetTempPathW) ; None hors Windows."""
    if not WINDOWS:
        return None
    if "temporaire" not in _DOSSIERS:
        import ctypes
        tampon = ctypes.create_unicode_buffer(32768)
        n = _kernel32().GetTempPathW(len(tampon), tampon)
        _DOSSIERS["temporaire"] = tampon.value.rstrip("\\/") if 0 < n < len(tampon) else None
    return _DOSSIERS["temporaire"]


def memoire_disponible_mo() -> int | None:
    """Mémoire physique disponible en Mo (§5.3, nombre d'essais en parallèle) : GlobalMemoryStatusEx sous Windows,
    MemAvailable de /proc/meminfo ailleurs ; None si elle ne peut pas être lue."""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes as w

        class EtatMemoire(ctypes.Structure):   # MEMORYSTATUSEX
            _fields_ = [("dwLength", w.DWORD), ("dwMemoryLoad", w.DWORD)] + \
                       [(n, ctypes.c_ulonglong) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                                                          "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual",
                                                          "ullAvailExtendedVirtual")]
        etat = EtatMemoire()
        etat.dwLength = ctypes.sizeof(etat)
        fonction = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
        fonction.argtypes = [ctypes.POINTER(EtatMemoire)]
        fonction.restype = w.BOOL
        if not fonction(ctypes.byref(etat)):
            return None
        return int(etat.ullAvailPhys // (1024 * 1024))
    try:
        with open("/proc/meminfo", encoding="ascii", errors="replace") as f:
            for ligne in f:
                if ligne.startswith("MemAvailable:"):
                    return int(ligne.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _dossier_connu_posix(nom: str) -> str | None:
    try:
        import pwd
        maison = pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError):
        return None
    if not maison:
        return None
    relatif = {"profil": "", "appdata": ".config", "localappdata": os.path.join(".local", "share"),
               "documents": "Documents", "onedrive": None}[nom]
    if relatif is None:
        return None
    return os.path.join(maison, relatif) if relatif else maison


_NOMS: dict = {}


def noms_machine() -> tuple[str, ...]:
    """Noms de la machine (nom d'hôte, NetBIOS et DNS sous Windows), du plus long au plus court."""
    if "machine" in _NOMS:
        return _NOMS["machine"]
    noms = set()
    try:
        import socket
        noms.add(socket.gethostname())
    except OSError:
        pass
    try:
        import platform
        noms.add(platform.node())
    except OSError:
        pass
    if WINDOWS:
        import ctypes
        from ctypes import wintypes as w
        k = _kernel32()
        for format_nom in (0, 1, 4, 5):   # NetBIOS, DnsHostname, PhysicalNetBIOS, PhysicalDnsHostname
            taille = w.DWORD(0)
            k.GetComputerNameExW(format_nom, None, ctypes.byref(taille))
            tampon = ctypes.create_unicode_buffer(max(taille.value, 1) + 1)
            taille = w.DWORD(len(tampon))
            if k.GetComputerNameExW(format_nom, tampon, ctypes.byref(taille)):
                noms.add(tampon.value)
    propres = set()
    for nom in noms:
        nom = (nom or "").strip()
        for variante in (nom, nom.split(".")[0]):
            if len(variante) >= 3 and variante.lower() not in ("localhost", "machine", "utilisateur"):
                propres.add(variante)
    _NOMS["machine"] = tuple(sorted(propres, key=lambda n: (-len(n), n)))
    return _NOMS["machine"]


def nom_utilisateur() -> str:
    """Nom du compte de l'utilisateur, par le système (sans variable d'environnement) ; « » s'il est inconnu."""
    if "utilisateur" in _NOMS:
        return _NOMS["utilisateur"]
    nom = ""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes as w
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.GetUserNameW.argtypes = [w.LPWSTR, ctypes.POINTER(w.DWORD)]
        advapi32.GetUserNameW.restype = w.BOOL
        tampon = ctypes.create_unicode_buffer(257)
        taille = w.DWORD(len(tampon))
        if advapi32.GetUserNameW(tampon, ctypes.byref(taille)):
            nom = tampon.value
    else:
        try:
            import pwd
            nom = pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError, OSError):
            nom = ""
    _NOMS["utilisateur"] = nom or ""
    return _NOMS["utilisateur"]


# ------------------------------------------------------------------------------------------------ anonymat des sorties

_SEP = r"(?:\\\\|\\|/)"                                      # séparateur : \, / ou \\ (chemin échappé en JSON)
_PREFIXE_UNC = r"(?:\\\\\\\\|\\\\|//)"                        # \\ ou \\\\ (JSON) ou //
_MORCEAU = r"[^\\/:*?\"<>|\r\n\t]+"                          # élément d'un chemin Windows
_FIN_RACINE = r"(?=$|[\\/\s\"'`)\]},;:>|]|\.(?!\w))"         # la racine se termine là
_PREFIXE_LONG = re.compile(r"(?:\\\\\\\\|\\\\)[?.](?:\\\\|\\)")          # préfixes \\?\ et \\.\
_CHEMIN_UNC = re.compile(r"(?<![^\s\"'(=\[,;<>])" + _PREFIXE_UNC + r"[^\\/\s\"'<>|*?:]+" + _SEP
                         + r"(?:" + _MORCEAU + _SEP + r")*")
_CHEMIN_LECTEUR = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:" + _SEP + r"(?:" + _MORCEAU + _SEP + r")*")
SUBSTITUT_CHEMIN = "<chemin>"

_SORTIES_DECLAREES: list[str] = []
_MOTIFS_COMPILES: dict = {}


def declarer_sortie(dossier: str) -> None:
    """Déclare un dossier de sortie, remplacé par « . » dans tout texte anonymisé ensuite."""
    d = os.path.abspath(dossier)
    if d not in _SORTIES_DECLAREES:
        _SORTIES_DECLAREES.append(d)


def oublier_sorties() -> None:
    _SORTIES_DECLAREES.clear()


def _variantes_chemin(chemin: str) -> set[str]:
    """Écritures possibles d'un dossier : normalisée, réelle (liens, lecteurs substitués), courte (8.3) et longue."""
    if not chemin:
        return set()
    variantes = {os.path.normpath(os.path.abspath(chemin))}
    try:
        variantes.add(os.path.realpath(chemin))
    except OSError:
        pass
    if WINDOWS:
        import ctypes
        k = _kernel32()
        for fonction in (k.GetShortPathNameW, k.GetLongPathNameW):
            tampon = ctypes.create_unicode_buffer(32768)
            n = fonction(chemin, tampon, len(tampon))
            if 0 < n < len(tampon):
                variantes.add(tampon.value)
    return {v.rstrip("\\/") for v in variantes if v and len(v.rstrip("\\/")) >= 3}


def _motif_racine(racine: str):
    cle = ("racine", racine)
    if cle in _MOTIFS_COMPILES:
        return _MOTIFS_COMPILES[cle]
    texte = racine.rstrip("\\/")
    debut = ""
    if re.match(r"^[\\/]{2}", texte):
        debut = _PREFIXE_UNC
    elif texte.startswith("/"):
        debut = "/"
    morceaux = [p for p in re.split(r"[\\/]+", texte) if p]
    motif = None
    if morceaux:
        motif = re.compile(debut + _SEP.join(re.escape(p) for p in morceaux) + _FIN_RACINE,
                           re.IGNORECASE if WINDOWS else 0)
    _MOTIFS_COMPILES[cle] = motif
    return motif


def _motif_nom(nom: str):
    """Nom isolé : ni précédé ni suivi d'une lettre, d'un chiffre ou de « _ » (le tiret et le point séparent)."""
    cle = ("nom", nom)
    if cle not in _MOTIFS_COMPILES:
        _MOTIFS_COMPILES[cle] = re.compile(r"(?<!\w)" + re.escape(nom) + r"(?!\w)", re.IGNORECASE)
    return _MOTIFS_COMPILES[cle]


def _garder_separateur(m) -> str:
    trouve = m.group(0)
    separateur = "\\\\" if trouve.endswith("\\\\") else trouve[-1]
    return SUBSTITUT_CHEMIN + separateur


def anonymiser(texte, sortie: str | None = None) -> str:
    """Rend `texte` sans information sur la machine ni sur l'utilisateur (§0.3), pour toute sortie écrite.

    Dans l'ordre :
    1. dossier de sortie (`sortie` et dossiers de `declarer_sortie`) → « . » ;
    2. Windows : dossier temporaire → « %TEMP% », `%LOCALAPPDATA%` et `%APPDATA%` → leur variable (règle de
       rédaction de §0.3) ;
    3. profil de l'utilisateur → « %USERPROFILE% » ;
    4. nom de la machine → « <machine> » ; nom du compte de l'utilisateur → « <utilisateur> » ;
    5. tout autre chemin absolu Windows (lettre de lecteur, partage réseau) → « <chemin> » suivi de son dernier
       élément : il ne reste aucune lettre de lecteur. Un chemin non délimité (ni guillemets ni fin de ligne) peut
       emporter le texte qui le suit jusqu'à son dernier séparateur : l'anonymat passe avant la lisibilité, et les
       messages du programme n'écrivent que des noms de fichiers (§0.2-7).
    Les chemins sont reconnus avec \\, / ou \\\\ (chaînes échappées en JSON), sans tenir compte de la casse sous
    Windows ; le dossier le plus long est remplacé d'abord. Le résultat est stable : anonymiser deux fois ne change
    plus rien.
    """
    if texte is None:
        return ""
    texte = str(texte)
    if not texte:
        return texte
    racines = []
    for dossier in ([sortie] if sortie else []) + list(_SORTIES_DECLAREES):
        racines.extend((v, ".") for v in _variantes_chemin(dossier))
    if WINDOWS:
        for dossier, variable in ((dossier_temporaire(), "%TEMP%"),
                                  (dossier_connu("localappdata"), "%LOCALAPPDATA%"),
                                  (dossier_connu("appdata"), "%APPDATA%")):
            if dossier:
                racines.extend((v, variable) for v in _variantes_chemin(dossier))
    profil = dossier_connu("profil")
    if profil:
        racines.extend((v, "%USERPROFILE%") for v in _variantes_chemin(profil))
    for racine, substitut in sorted(set(racines), key=lambda r: (-len(r[0]), r[0])):
        motif = _motif_racine(racine)
        if motif is not None:
            texte = motif.sub(lambda m, s=substitut: s, texte)
    for nom in noms_machine():
        texte = _motif_nom(nom).sub("<machine>", texte)
    utilisateur = nom_utilisateur()
    if len(utilisateur) >= 3 and utilisateur.lower() not in ("machine", "utilisateur", "chemin"):
        texte = _motif_nom(utilisateur).sub("<utilisateur>", texte)
    texte = _PREFIXE_LONG.sub("", texte)
    texte = _CHEMIN_UNC.sub(_garder_separateur, texte)
    texte = _CHEMIN_LECTEUR.sub(_garder_separateur, texte)
    return texte


# ------------------------------------------------------------------------------------------------ motifs génériques

# Motifs génériques de §0.3, publiables (les motifs propres à une personne restent dans outils_prives/). Ils sont
# écrits pour ne pas se reconnaître eux-mêmes (classes de caractères) : un module qui les contient, une fois compilé
# dans le paquet, passe le contrôle.
_NEUTRES_PROFIL = r"(?:<[^<>\r\n]{1,40}>|Public|Default|Default User|All Users)"
_FIN_NOM = r"(?=[\\/\s\"'`,;:)\]}]|$)"
MOTIFS_ANONYMAT = (
    ("profil Windows", re.compile(r"[A-Za-z]:" + _SEP + r"+Users" + _SEP + r"+(?!" + _NEUTRES_PROFIL + _FIN_NOM
                                  + r")[^\\/\s\"'<>%|*?:]+", re.IGNORECASE)),
    ("dossier personnel POSIX", re.compile(r"[/]home[/](?!<)[A-Za-z0-9._-]+")),
    ("profil macOS", re.compile(r"(?<![:\\/])[/]Users[/](?!<|Shared(?![\w.-]))[A-Za-z0-9._-]+")),
    ("compte d'une machine de CI", re.compile(r"runneradm[i]n", re.IGNORECASE)),
    ("dossier temporaire", re.compile(r"AppData" + _SEP + r"+Local" + _SEP + r"+Temp(?![A-Za-z])|_MEI[0-9]{3,}"
                                      r"|[/]tmp[/](?:pip-|tmp)[A-Za-z0-9_-]{4,}", re.IGNORECASE)),
    ("dossier d'outil de développement", re.compile(r"(?<![~][\\/])[.]cargo" + _SEP + r"+registry"
                                                    + r"|(?<![~][\\/])[.]rustup" + _SEP
                                                    + r"|hostedtoolcach[e]|" + _SEP + r"a" + _SEP
                                                    + r"+_temp(?![A-Za-z])", re.IGNORECASE)),
)
# Lettre de lecteur suivie de « :\ » ou « :/ » : dans un texte, toujours signalée (hors formes neutres) ; dans un
# contenu binaire (image, exécutable, code compilé), seulement si un premier dossier au nom ASCII plausible suit,
# terminé par un séparateur : des octets quelconques ne forment presque jamais cette suite par hasard (essai sur
# 2 Mo pseudo-aléatoires), alors qu'un chemin de la machine de construction écrit dans un binaire la forme toujours.
MOTIF_LECTEUR = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)")
MOTIF_LECTEUR_BINAIRE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)"
                                   r"(?=[A-Za-z0-9 ._()$%~<>-]{1,80}(?:\\\\|\\|/))")
FORMES_NEUTRES = (
    re.compile(r"[A-Za-z]:" + _SEP + r"+Users" + _SEP + r"+" + _NEUTRES_PROFIL + _FIN_NOM, re.IGNORECASE),
    re.compile(r"[A-Za-z]:" + _SEP + r"+(?:Windows|Program Files|Program Files \(x86\)|ProgramData)(?![\w.-])",
               re.IGNORECASE),
    re.compile(r"[A-Za-z]:" + _SEP + r"+<[^<>\r\n]{1,40}>"),
)


def _vues_texte(donnees) -> list[tuple[str, bool]]:
    """Vues texte d'un contenu, chacune avec un indicateur « binaire ».

    Texte (str, ou octets en UTF-8 valide sans octet nul) : le texte lui-même. Binaire : les octets lus en Latin-1
    (chaînes ASCII et UTF-8) et, s'ils contiennent des octets nuls, en UTF-16-LE aux deux alignements (chaînes
    larges des binaires Windows)."""
    if isinstance(donnees, str):
        return [(donnees, False)]
    donnees = bytes(donnees)
    if b"\x00" not in donnees:
        try:
            return [(donnees.decode("utf-8"), False)]
        except UnicodeDecodeError:
            pass
    vues = [(donnees.decode("latin-1"), True)]
    if b"\x00" in donnees:
        for decalage in (0, 1):
            partie = donnees[decalage:]
            partie = partie[:len(partie) - (len(partie) % 2)]
            vues.append((partie.decode("utf-16-le", errors="ignore"), True))
    return vues


def chercher_motifs(donnees, lecteur: bool = False, extraits: bool = False) -> list[tuple[str, str]]:
    """Motifs génériques d'anonymat (§0.3) trouvés dans `donnees` (str ou bytes).

    `lecteur=True` (fichiers propres au projet : modules, documents, exemples, moteurs, sorties) : toute lettre de
    lecteur suivie de « :\\ » ou « :/ » est aussi signalée, hors formes neutres (`C:\\Users\\<nom>`, `C:\\Windows`,
    `C:\\Program Files`, `C:\\<dossier>`) ; dans un contenu binaire, seulement si un élément de chemin plausible suit
    (`MOTIF_LECTEUR_BINAIRE`). Rend des couples (motif, extrait) ; l'extrait est vide sauf avec `extraits=True`, à ne
    jamais afficher dans un journal publié (il contiendrait ce qu'on cherche à cacher).
    """
    trouves = []
    for vue, binaire in _vues_texte(donnees):
        for nom, motif in MOTIFS_ANONYMAT:
            for m in motif.finditer(vue):
                trouves.append((nom, m.group(0) if extraits else ""))
        if lecteur:
            for m in (MOTIF_LECTEUR_BINAIRE if binaire else MOTIF_LECTEUR).finditer(vue):
                if not any(n.match(vue, m.start()) for n in FORMES_NEUTRES):
                    trouves.append(("lettre de lecteur", vue[m.start():m.start() + 40] if extraits else ""))
    return trouves
