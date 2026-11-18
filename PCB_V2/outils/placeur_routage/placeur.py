# -*- coding: utf-8 -*-
"""Orchestrateur du placeur-routeur (lot D ; contrat §5.3, §1.3, §1.4, §2.9, §4.6 ; avenant 2.2, A.7).

    placeur.py CARTE [options]                            chaîne depuis une carte (import, graines, carte produite)
    placeur.py --donnees donnees_placeur.json [options]   données déjà construites (CI, données publiques)
    placeur.py --init CARTE | --version | --auto-test | --diagnostic

Toutes les options de §5.3 sont analysées. Contrôles préalables et table des erreurs d'environnement de §5.3
(carte verrouillée, disque plein, dossier protégé, chemin trop long, moteur absent, mémoire, calcul en cours ;
avertissements OneDrive et batterie) ; `--reprendre [DOSSIER]` (§5.3 « Reprise ») ; `--auto-test` et
`--diagnostic` (texte anonymisé). Mode --donnees : un dossier `<parent>/<nom>_<AAAAMMJJ-HHMMSS>/` est créé (§1.3) et verrouillé (`.verrou`) ;
les graines sont lancées en parallèle (moteur, puis contrôles indépendants), la graine retenue est choisie, et
`technique/rapport.json` (rapport/2) est réécrit après chaque graine.

Arrêts (§5.3) : fichier `arret_lancements` du dossier (plus de nouvelle graine, les graines en cours finissent),
fichier `arret_demande` ou Ctrl+C (passé aux moteurs par --arret) ; un second Ctrl+C tue tous les moteurs.

Codes de sortie (§1.4) : 0 au moins une graine valide ; 1 aucune ; 2 entrée ou environnement invalide (dont le
code 2 du moteur) ; 3 arrêt demandé ; 4 erreur interne de l'orchestrateur (`technique/erreur.txt`).
"""
from __future__ import annotations

import difflib
import errno
import hashlib
import importlib
import json
import platform
import math
import os
import queue
import re
import shutil
import signal
import sys
import threading
import time
import traceback

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import chemins  # noqa: E402
import formats  # noqa: E402
import parametres  # noqa: E402

NOM_MOTEUR = "placeur_routage"
FORMAT_RAPPORT = "rapport/2"
SECONDES_PAR_MINUTE = 60          # --duree-max-min est en minutes
MARGE_MEMOIRE_MO = 500            # §5.3 : ⌊(mémoire disponible − 500 Mo) / mémoire estimée⌋
TOLERANCE_DATE_S = 2.0            # stats.json « postérieur au lancement » (granularité des dates de fichier)
PAUSE_S = 0.1                     # période de sondage du fil principal (arrêts, résultats)
DELAI_ESTIMER_S = 600
DELAI_ETALONNER_S = 300
DELAI_DRC_S = 3600
FICHIERS_LOURDS = ("placements_routage.json", "routage_detaille.json", "routage_detaille.svg", "console.txt")
Z_95 = 1.959963984540054          # quantile 0,975 de la loi normale (intervalle de Wilson à 95 %)
GRAINES_CAMPAGNE = 8              # A.7 : campagne de 8 graines
ARRET_LANCEMENTS = "arret_lancements"
ARRET_DEMANDE = "arret_demande"
LONGUEUR_REFUSEE = 240            # §5.3 : chemin de sortie > 240 caractères refusé, > 200 averti
LONGUEUR_AVERTIE = 200
DISQUE_MIN_GO = 1.0               # §5.3 : espace libre < 1 Go au lancement
MESSAGE_EN_COURS = "Un calcul est déjà en cours dans ce dossier."
MESSAGE_PROTEGE = "Windows interdit l'écriture dans ce dossier."
MESSAGE_VERROUILLEE = "La carte est ouverte dans Altium et verrouillée."
MESSAGE_ONEDRIVE = "Ce dossier est synchronisé : les résultats seront envoyés en ligne."
MESSAGE_BATTERIE = "Le PC est sur batterie."
MESSAGE_ENTREES_CHANGEES = "les entrées ont changé : lancer un nouveau calcul"
MESSAGE_PAQUET = "Le dossier du paquet ne reçoit pas de résultats : choisir un autre dossier de sortie."
MODULES_AUTO_TEST = ("formats", "parametres", "drc_maison", "verifier_placement", "proprete", "importer_carte",
                     "deduire_config", "vias_couture", "generer_pcbdoc", "visualiser")

USAGE = """\
usage : placeur.py CARTE [options]
        placeur.py --donnees donnees_placeur.json [options]
        placeur.py --init CARTE | --version | --auto-test | --diagnostic
options : --sortie PARENT  --parametres F.ini  --qualite apercu|rapide|normal|soigne  --essais N
          --graine-depart G  --processeurs P  --duree-max-min M  --valides-voulues K  --contraintes C.json
          --config U.json  --parametres-moteur M.json  --moteurs DOSSIER  --sans-pcbdoc  --sans-serigraphie
          --sans-couture  --reprendre [DOSSIER]  --analyser  --priorite-normale  --garder-graines meilleure|toutes
          --evenements
(mode d'emploi, section 3)"""

# option -> clé du .ini (§5.4) ; None : traitée à part
OPTIONS_VALEUR = {
    "--sortie": "sortie.dossier", "--parametres": None, "--qualite": "calcul.qualite", "--essais": None,
    "--graine-depart": "calcul.graine_depart", "--processeurs": "calcul.processeurs",
    "--duree-max-min": "calcul.duree_max_min", "--valides-voulues": "calcul.valides_voulues",
    "--contraintes": "carte.contraintes", "--config": "carte.config_convertisseurs",
    "--parametres-moteur": "avance.parametres_moteur", "--moteurs": None, "--garder-graines": "sortie.garder_graines",
    "--donnees": None, "--init": None,
}
OPTIONS_DRAPEAU = {
    "--sans-pcbdoc": ("pcbdoc.produire", "non"), "--sans-serigraphie": ("pcbdoc.serigraphie", "non"),
    "--sans-couture": ("pcbdoc.vias_couture", "non"), "--priorite-normale": ("calcul.priorite_normale", "oui"),
    "--analyser": None, "--evenements": None, "--version": None, "--auto-test": None, "--diagnostic": None,
}
OPTIONS_CHEMIN = ("--sortie", "--parametres", "--contraintes", "--config", "--parametres-moteur", "--moteurs",
                  "--donnees", "--init")
OPTIONS_SEULES = ("--version", "--auto-test", "--diagnostic")


class ErreurEntree(Exception):
    """Entrée ou environnement invalide : message pour l'utilisateur, code 2 (§1.4)."""


class ErreurInterne(RuntimeError):
    """Erreur interne survenue dans un fil de graine ; porte la trace complète."""


class ControleIndisponible(RuntimeError):
    """Le module de contrôle (lot C) n'expose pas l'API de §5.2."""


class _NonInscriptible(Exception):
    """Dossier de sortie par défaut non inscriptible : le dossier suivant est essayé (§5.3)."""


# ================================================================================================ ligne de commande

def analyser_arguments(argv: list[str]) -> dict:
    """Analyse la ligne de commande de §5.3. Rend {"carte", "valeurs": {option: texte}, "drapeaux": set,
    "reprendre": None | True | dossier}. Erreur ⇒ ErreurEntree.

    `--reprendre` prend l'argument suivant comme dossier s'il ne commence pas par « - » et désigne un dossier."""
    o = {"carte": None, "valeurs": {}, "drapeaux": set(), "reprendre": None}
    connues = list(OPTIONS_VALEUR) + list(OPTIONS_DRAPEAU) + ["--reprendre"]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in OPTIONS_VALEUR:
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                raise ErreurEntree(f"option {a} : valeur manquante")
            if a in o["valeurs"]:
                raise ErreurEntree(f"option {a} donnée deux fois")
            o["valeurs"][a] = argv[i + 1]
            i += 2
        elif a in OPTIONS_DRAPEAU:
            o["drapeaux"].add(a)
            i += 1
        elif a == "--reprendre":
            if i + 1 < len(argv) and not argv[i + 1].startswith("-") and os.path.isdir(argv[i + 1]):
                o["reprendre"] = os.path.abspath(argv[i + 1])
                i += 2
            else:
                o["reprendre"] = True
                i += 1
        elif a.startswith("-") and len(a) > 1:
            proche = difflib.get_close_matches(a, connues, n=1, cutoff=0.6)
            raise ErreurEntree(f"option inconnue : {a}" + (f" ; vouliez-vous dire {proche[0]} ?" if proche else ""))
        else:
            if o["carte"] is not None:
                raise ErreurEntree(f"une seule carte attendue (reçu {os.path.basename(o['carte'])} et "
                                   f"{os.path.basename(a)})")
            o["carte"] = a
            i += 1
    seules = [d for d in OPTIONS_SEULES if d in o["drapeaux"]]
    if seules and (len(seules) > 1 or len(o["drapeaux"]) > 1 or o["valeurs"] or o["carte"] or o["reprendre"]):
        raise ErreurEntree(f"l'option {seules[0]} s'emploie seule")
    if "--init" in o["valeurs"] and (o["carte"] or "--donnees" in o["valeurs"]):
        raise ErreurEntree("--init CARTE s'emploie sans autre carte ni --donnees")
    if "--donnees" in o["valeurs"] and o["carte"]:
        raise ErreurEntree("donner soit une CARTE, soit --donnees, pas les deux")
    if (not seules and not o["carte"] and "--donnees" not in o["valeurs"] and "--init" not in o["valeurs"]
            and not isinstance(o["reprendre"], str)):          # --reprendre DOSSIER : la carte est celle du calcul
        raise ErreurEntree("carte manquante : placeur.py CARTE, ou --donnees donnees_placeur.json")
    if "--essais" in o["valeurs"]:
        v = o["valeurs"]["--essais"].strip()
        if not v.isdigit():
            raise ErreurEntree(f"--essais : '{v}' n'est pas un nombre entier positif ou nul")
    return o


def options_ini(o: dict) -> dict:
    """Options de la ligne de commande vers les clés du .ini (§5.4), chemins rendus absolus."""
    res = {}
    for option, cle in OPTIONS_VALEUR.items():
        if cle and option in o["valeurs"]:
            v = o["valeurs"][option]
            res[cle] = os.path.abspath(v) if option in OPTIONS_CHEMIN else v
    if o["valeurs"].get("--essais", "0").strip() not in ("", "0"):
        res["calcul.essais"] = o["valeurs"]["--essais"]
    for option, cle in OPTIONS_DRAPEAU.items():
        if cle and option in o["drapeaux"]:
            res[cle[0]] = cle[1]
    if o["carte"]:
        res["carte.fichier"] = os.path.abspath(o["carte"])
    return res


# ================================================================================================ statistiques (A.7)

def wilson(succes: int, n: int, z: float = Z_95) -> list[float] | None:
    """Intervalle de Wilson [bas, haut] d'une proportion succes/n ; None si n = 0."""
    if n <= 0:
        return None
    p = succes / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    demi = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, centre - demi), min(1.0, centre + demi)]


def mediane(valeurs: list[float]) -> float | None:
    v = sorted(valeurs)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def statistiques(graines: list[dict], reference: float | None = None, k: int = GRAINES_CAMPAGNE) -> dict:
    """Quantités publiées de l'avenant 2.2, A.7, sur les graines mesurées (entrées `graines` de rapport/2) :
    taux de validité et intervalle de Wilson à 95 % ; meilleur coût et médiane des valides ; calcul par graine
    valide (somme des `execution.temps_total_s` — à défaut, durée mesurée par l'orchestrateur pour une graine sans
    stats.json — divisée par le nombre de graines valides) ; probabilité qu'une campagne de `k` graines tirées avec
    remise parmi les graines mesurées livre au moins une graine valide de coût ≤ `reference` : calcul exact du
    rééchantillonnage, 1 − (1 − q)^k, q étant la part des graines mesurées valides et de coût ≤ référence."""
    n = len(graines)
    valides = [g for g in graines if g.get("valide")]
    couts = [g["cout_commun"] for g in valides if isinstance(g.get("cout_commun"), (int, float))]
    temps = 0.0
    for g in graines:
        t = g.get("temps_calcul_s")
        temps += float(t if isinstance(t, (int, float)) else (g.get("duree_s") or 0.0))
    ic = wilson(len(valides), n)
    res = {
        "graines_mesurees": n,
        "graines_valides": len(valides),
        "taux_validite": round(len(valides) / n, 6) if n else None,
        "wilson_95": [round(x, 6) for x in ic] if ic else None,
        "meilleur_cout": min(couts) if couts else None,
        "mediane_cout": round(mediane(couts), 6) if couts else None,
        "calcul_total_s": round(temps, 3),
        "calcul_par_graine_valide_s": round(temps / len(valides), 3) if valides else None,
        "reference_cout": reference,
        "campagne_graines": k,
        "q_reference": None,
        "p_campagne": None,
    }
    if reference is not None and n:
        q = sum(1 for c in couts if c <= reference) / n
        res["q_reference"] = round(q, 6)
        res["p_campagne"] = round(1.0 - (1.0 - q) ** k, 6)
    return res


# ================================================================================================ contrôles (§5.2)

class Controles:
    """Contrôles indépendants d'une graine (§4.6-4), par les API Python de §5.2 (lot C).

    Les quatre fonctions de mesure et le contrôle du placement sont appelés dans le processus ; `drc_maison`
    (strict et altium) en processus séparé par `chemins.commande_outil`. Une fonction absente (module pas encore
    livré au format de §5.2) lève ControleIndisponible : le contrôle est alors noté refusé, jamais supposé réussi.
    Les essais remplacent les méthodes élémentaires par des doublures ; `executer` reste celui-ci."""

    def verifier_placement(self, placements, donnees) -> dict:
        return self._fonction("verifier_placement", "verifier")(placements, donnees, grille=True)

    def drc(self, chemin_donnees: str, chemin_placement: str, chemin_routage: str, mode: str, chemin_sortie: str,
            job=None) -> dict:
        cmd = chemins.commande_outil("drc_maison", "--donnees", chemin_donnees, "--placement", chemin_placement,
                                     "--routage", chemin_routage, "--mode", mode, "--sortie", chemin_sortie)
        r = chemins.executer(cmd, delai_s=DELAI_DRC_S, job=job)
        if r.returncode not in (0, 1):
            fin = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-5:])
            raise RuntimeError(f"drc_maison --mode {mode} : code {r.returncode}" + (f" : {fin}" if fin else ""))
        return formats.charger(chemin_sortie, "verdict")

    def mesures(self, donnees, placements, routage, verdict_strict) -> dict:
        return self._fonction("mesures", "mesurer")(donnees, placements, routage, verdict_strict=verdict_strict)

    def proprete(self, donnees, placements, routage) -> dict:
        return self._fonction("proprete", "mesurer")(donnees, placements, routage)

    def convertisseurs(self, donnees, placements, routage) -> dict:
        return self._fonction("mesure_convertisseurs", "mesurer")(donnees, placements, routage)

    @staticmethod
    def _fonction(module: str, nom: str):
        try:
            mod = importlib.import_module(module)
        except ImportError:
            raise ControleIndisponible(f"module {module} absent") from None
        f = getattr(mod, nom, None)
        if not callable(f):
            raise ControleIndisponible(f"{module}.{nom} absent (API de §5.2 non livrée)")
        return f

    def executer(self, dossier: str, chemin_donnees: str, donnees: dict, job=None, sortie: str | None = None) -> dict:
        """Contrôle la graine de `dossier` et y écrit controle_placement.json, drc_strict.json, drc_altium.json,
        mesures.json, proprete.json, convertisseurs.json. Rend {"placement", "drc_strict", "drc_altium": dict
        (refus : {"valide": False, "erreur": texte}), "mesures", "proprete", "convertisseurs": dict | None,
        "avertissements": [texte]}."""
        res = {"avertissements": []}
        cp = os.path.join(dossier, "placements_routage.json")
        cr = os.path.join(dossier, "routage_detaille.json")
        try:
            placements = formats.charger(cp, "placements")
            routage = formats.charger(cr, "routage")
        except (formats.ErreurFormat, OSError) as e:
            texte = chemins.anonymiser(f"sorties du moteur illisibles : {_texte_erreur(e)}", sortie)
            for cle in ("placement", "drc_strict", "drc_altium"):
                res[cle] = {"valide": False, "erreur": texte}
            for cle in ("mesures", "proprete", "convertisseurs"):
                res[cle] = None
            return res

        def un(cle, fichier, fmt, fonction, obligatoire=True):
            try:
                d = fonction()
                erreurs = formats.VALIDATEURS[fmt](d)
                if erreurs:
                    raise formats.ErreurFormat(erreurs)
                chemins.ecrire_json(os.path.join(dossier, fichier), d)
                return d
            except Exception as e:  # noqa: BLE001 - tout refus d'un contrôle est noté, jamais supposé réussi
                texte = chemins.anonymiser(f"{fichier} : {_texte_erreur(e)}", sortie)
                if obligatoire:
                    return {"valide": False, "erreur": texte}
                res["avertissements"].append(texte)
                return None

        res["placement"] = un("placement", "controle_placement.json", "controle_placement",
                              lambda: self.verifier_placement(placements, donnees))
        for mode in ("strict", "altium"):
            fichier = f"drc_{mode}.json"
            res[f"drc_{mode}"] = un(f"drc_{mode}", fichier, "verdict",
                                    lambda m=mode, f=fichier: self.drc(chemin_donnees, cp, cr, m,
                                                                       os.path.join(dossier, f), job))
        strict = res["drc_strict"] if "erreur" not in res["drc_strict"] else None
        res["mesures"] = un("mesures", "mesures.json", "mesures",
                            lambda: self.mesures(donnees, placements, routage, strict), obligatoire=False)
        res["proprete"] = un("proprete", "proprete.json", "proprete",
                             lambda: self.proprete(donnees, placements, routage), obligatoire=False)
        res["convertisseurs"] = un("convertisseurs", "convertisseurs.json", "mesure_convertisseurs",
                                   lambda: self.convertisseurs(donnees, placements, routage), obligatoire=False)
        return res


def _texte_erreur(e: BaseException) -> str:
    if isinstance(e, formats.ErreurFormat):
        return str(e)
    if isinstance(e, OSError):
        return f"{e.strerror or type(e).__name__} ({os.path.basename(e.filename or '') or '?'})"
    return f"{type(e).__name__} : {e}"


def _nombre(v) -> int:
    """Nombre d'éléments d'un champ de verdict/2 (entier) ou de Verdict::en_json (liste)."""
    if isinstance(v, list):
        return len(v)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _resume_verdict(v: dict | None) -> dict | None:
    if v is None:
        return None
    if "erreur" in v:
        return {"valide": False, "ouvertes": None, "violations": None, "erreur": v["erreur"]}
    return {"valide": bool(v.get("valide")), "ouvertes": _nombre(v.get("liaisons_ouvertes")),
            "violations": _nombre(v.get("violations"))}


def _resume_placement(p: dict | None) -> dict | None:
    if p is None:
        return None
    if "erreur" in p:
        return {"valide": False, "violations": None, "erreur": p["erreur"]}
    return {"valide": bool(p.get("valide")), "violations": _nombre(p.get("violations"))}


def code_lisible(code: int) -> str:
    """Code de sortie lisible : décimal, ou hexadécimal pour un code d'exception Windows (0xC0000409)."""
    if code is None:
        return "?"
    if code < 0 or code > 255:
        return f"0x{code & 0xFFFFFFFF:08X}"
    return str(code)


# ================================================================================================ environnement

def _nombre_fr(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def espace_libre_go(chemin: str) -> float | None:
    """Espace libre (Go) du volume de `chemin` ou de son plus proche parent existant ; None s'il est illisible."""
    d = os.path.abspath(chemin)
    while d and not os.path.exists(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    try:
        return shutil.disk_usage(d).free / 1024 ** 3
    except OSError:
        return None


def message_disque(chemin: str) -> str:
    libre = espace_libre_go(chemin)
    return f"Espace disque insuffisant ({_nombre_fr(libre) if libre is not None else '?'} Go libres)."


def message_long(n: int) -> str:
    return f"Chemin de sortie trop long ({n} caractères)."


def message_environnement(e: BaseException, chemin: str | None = None) -> str | None:
    """Message de la table de §5.3 pour une erreur du système (Windows 112, 206, 5 ; ENOSPC, ENAMETOOLONG), ou None."""
    if not isinstance(e, OSError):
        return None
    w = getattr(e, "winerror", None)
    if w == 112 or e.errno == errno.ENOSPC:
        return message_disque(chemin or e.filename or ".")
    if w == 206 or e.errno == errno.ENAMETOOLONG:
        return message_long(len(str(e.filename or chemin or "")))
    if w == 5 or (isinstance(e, PermissionError) and w is None):
        return MESSAGE_PROTEGE
    return None


def carte_verrouillee(chemin: str, e: OSError) -> bool:
    """Vrai si la lecture de la carte est refusée par un autre programme qui la tient ouverte (Windows 32 ou 33).
    `open()` rend EACCES sans le code Windows : celui-ci est relu par CreateFileW en lecture partagée."""
    if getattr(e, "winerror", None) in (32, 33):
        return True
    if os.name != "nt" or not isinstance(e, PermissionError):
        return False
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    h = k.CreateFileW(chemin, 0x80000000, 0x7, None, 3, 0x80, None)    # lecture ; partage lecture-écriture-effacement
    if h not in (None, w.HANDLE(-1).value):
        k.CloseHandle(h)
        return False
    return ctypes.get_last_error() in (32, 33)


def est_sous(chemin: str | None, racine: str | None) -> bool:
    if not chemin or not racine:
        return False
    c = os.path.normcase(os.path.abspath(chemin)).rstrip("\\/")
    r = os.path.normcase(os.path.abspath(racine)).rstrip("\\/")
    return c == r or c.startswith(r + os.sep)


def dossier_paquet() -> str | None:
    """Dossier du paquet (mode figé), qui ne reçoit jamais de résultats ; None en mode source (le dépôt)."""
    return chemins.racine_paquet() if chemins.est_fige() else None


def dossiers_onedrive() -> list[str]:
    d = chemins.dossier_connu("onedrive")
    return [d] if d else []


def synchronise(chemin: str) -> bool:
    """Vrai si `chemin` est sous un dossier OneDrive (dossier connu du système, ou élément « OneDrive… »)."""
    if any(est_sous(chemin, d) for d in dossiers_onedrive()):
        return True
    return any(p.lower().startswith("onedrive") for p in os.path.abspath(chemin).replace("\\", "/").split("/"))


def interdit_par_defaut(dossier: str) -> bool:
    """Jamais de résultats par défaut dans le dossier du paquet ni dans le dossier temporaire (§5.3)."""
    return est_sous(dossier, dossier_paquet()) or est_sous(dossier, chemins.dossier_temporaire())


def parents_par_defaut(source: str, nom: str) -> list[str]:
    """Défaut de --sortie (§5.3) : `<dossier de la carte>/<nom>_resultats/`, puis Documents\\PlaceurRouteur, puis
    %LOCALAPPDATA%\\PlaceurRouteur\\resultats ; les dossiers interdits (paquet, temporaire) sont sautés."""
    candidats = [os.path.join(os.path.dirname(os.path.abspath(source)), f"{nom}_resultats")]
    documents = chemins.dossier_connu("documents")
    if documents:
        candidats.append(os.path.join(documents, "PlaceurRouteur"))
    local = chemins.dossier_connu("localappdata")
    if local:
        candidats.append(os.path.join(local, "PlaceurRouteur", "resultats"))
    return [c for c in candidats if not interdit_par_defaut(c)]


def lire_rapport_inacheve(dossier: str) -> dict | None:
    """rapport.json d'un calcul inachevé (`rapport/2`, `termine: false`) du dossier, ou None."""
    try:
        with open(os.path.join(dossier, "technique", "rapport.json"), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(d, dict) and d.get("format") == FORMAT_RAPPORT and d.get("termine") is False:
        return d
    return None


def calcul_inacheve(source: str, parent: str | None = None) -> str | None:
    """Dernier calcul inachevé de la carte (ou des données) `source`, pour --reprendre sans dossier et pour la
    fenêtre (proposition de reprise) : dossier `<nom>_<AAAAMMJJ-HHMMSS>` d'un parent (`parent`, sinon les dossiers
    par défaut de --sortie) dont le rapport est inachevé et dont le verrou est libre. Rend son chemin ou None."""
    nom = os.path.splitext(os.path.basename(source))[0]
    trouves = []
    for p in ([parent] if parent else parents_par_defaut(source, nom)):
        try:
            noms = os.listdir(p)
        except OSError:
            continue
        for n in noms:
            d = os.path.join(p, n)
            if re.fullmatch(re.escape(nom) + r"_\d{8}-\d{6}", n) and os.path.isdir(d) and lire_rapport_inacheve(d):
                v = chemins.Verrou(os.path.join(d, ".verrou"))
                try:
                    libre = v.acquerir()
                except OSError:
                    continue
                if libre:
                    v.liberer()
                    trouves.append((n[len(nom) + 1:], d))
    return max(trouves)[1] if trouves else None


def empreinte_parametres(params: dict) -> str:
    """SHA-256 du JSON canonique des paramètres effectifs sans `campagne.threads` (clés triées, UTF-8)."""
    p = json.loads(json.dumps(params))
    (p.get("campagne") or {}).pop("threads", None)
    return hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _sans_fils(params) -> dict | None:
    if not isinstance(params, dict):
        return None
    p = json.loads(json.dumps(params))
    (p.get("campagne") or {}).pop("threads", None)
    return p


# ================================================================================================ calcul

class Calcul:
    """Un calcul en mode --donnees : dossier, verrou, estimations, graines en parallèle, choix et rapport."""

    def __init__(self, o: dict, reglages, chemin_donnees: str, donnees: dict, controles: Controles,
                 flux, flux_erreur):
        self.o = o
        self.reglages = reglages
        self.source_donnees = chemin_donnees
        self.donnees = donnees
        self.controles = controles
        self.flux = flux
        self.flux_erreur = flux_erreur
        self.evenements = "--evenements" in o["drapeaux"]
        self.nom = os.path.splitext(os.path.basename(chemin_donnees))[0]
        self.version = chemins.version_lanceur()
        self.t0 = time.monotonic()
        self.debut_epoch = time.time()
        self._verrou_etat = threading.RLock()
        self._verrou_sortie = threading.Lock()
        self._resultats: queue.Queue = queue.Queue()
        self.sortie = None
        self.technique = None
        self.verrou = None
        self.job = None
        self.semaphore = None
        self.graines: dict[int, dict] = {}
        self.non_lancees: list[int] = []
        self.avertissements: list[str] = list(reglages.avertissements)
        self.incoherences = 0
        self.fin = "normale"
        self.raison_arret = None          # « lancements », « demande », « duree_max », « valides_voulues »
        self.arret_demande = False
        self.arret_lancements = False
        self.signaux = 0
        self.force = False
        self.erreur_moteur = None
        self.termine = False
        self.code = None
        self.erreur = None
        self.meilleure = None
        self.a_terminer = None
        self.estimation = {"memoire_mo": None, "memoire_corrigee_mo": None, "memoire_disponible_mo": None,
                           "duree_prevue_s": None, "duree_max_s": None, "duree_graine_prevue_s": None,
                           "duree_graine_mesuree_s": None, "duree_prevue_corrigee_s": None, "paralleles": None,
                           "plafond_graine_s": None, "score": None}
        self.params = None
        self.origines_moteur = {}
        self.durees_finies: list[float] = []
        self.pics_memoire: list[int] = []
        self.valides = 0
        self.essais = 0
        self.fils = 1
        self.processeurs = 1
        self.echeance = None
        self.mode = "donnees"
        self.sans_graine = False          # --analyser : aucune graine, aucun cargo
        self.livrable = None
        self.couture = None
        self.reference = None
        self.reprise = o.get("reprendre")    # None, True (dernier calcul inachevé) ou dossier
        self.ancien_rapport = None
        self.instantane: dict[str, bytes] = {}
        self.reprise_validee = False
        self.reprises: list[int] = []

    # ------------------------------------------------------------------------------------------ sorties
    def temps(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def journal(self, texte: str) -> None:
        ligne = chemins.anonymiser(texte, self.sortie)
        horodate = time.strftime("%H:%M:%S") + " " + ligne
        with self._verrou_sortie:
            if self.technique:
                with open(os.path.join(self.technique, "journal.txt"), "a", encoding="utf-8", newline="\n") as f:
                    f.write(horodate + "\n")
            try:
                self.flux.write(ligne + "\n")
                self.flux.flush()
            except (OSError, ValueError):
                pass

    def evt(self, type_: str, **champs) -> None:
        if not self.evenements:
            return
        for cle in ("texte",):
            if isinstance(champs.get(cle), str):
                champs[cle] = chemins.anonymiser(champs[cle], self.sortie)
        ligne = "EVT " + json.dumps({"type": type_, **champs}, ensure_ascii=False)
        with self._verrou_sortie:
            try:
                self.flux.write(ligne + "\n")
                self.flux.flush()
            except (OSError, ValueError):
                pass

    def avertir(self, texte: str) -> None:
        texte = chemins.anonymiser(texte, self.sortie)
        self.avertissements.append(texte)
        self.journal("avertissement : " + texte)
        self.evt("avertissement", texte=texte)

    # ------------------------------------------------------------------------------------------ préparation
    def creer_dossier(self) -> None:
        """`<parent>/<nom>_<AAAAMMJJ-HHMMSS>/` : jamais un dossier existant (dans la même seconde : la suivante).
        Parent : --sortie (`[sortie] dossier`), sinon le premier dossier par défaut inscriptible (§5.3)."""
        if self.reprise is not None:
            self.ouvrir_reprise()
        else:
            explicite = self.reglages.valeur("sortie", "dossier")
            if explicite and est_sous(explicite, dossier_paquet()):
                raise ErreurEntree(MESSAGE_PAQUET)
            candidats = [explicite] if explicite else parents_par_defaut(self.source_donnees, self.nom)
            if not candidats:
                raise ErreurEntree("aucun dossier de sortie possible : donner --sortie")
            for i, parent in enumerate(candidats):
                try:
                    self.creer_dans(parent)
                    break
                except _NonInscriptible as e:
                    if explicite or i == len(candidats) - 1:
                        raise ErreurEntree(str(e)) from None
                    self.avertissements.append(f"dossier par défaut non inscriptible ({e}) : dossier suivant essayé")
        self.technique = os.path.join(self.sortie, "technique")
        os.makedirs(self.technique, exist_ok=True)
        n = len(self.sortie)
        if n > LONGUEUR_AVERTIE:
            self.avertir(message_long(n) + " : un dossier plus court est conseillé")
        if synchronise(self.sortie):
            self.avertir(MESSAGE_ONEDRIVE)
        if chemins.sur_batterie():
            self.avertir(MESSAGE_BATTERIE)

    def creer_dans(self, parent: str) -> None:
        parent = os.path.abspath(parent)
        n = len(os.path.join(parent, f"{self.nom}_{time.strftime('%Y%m%d-%H%M%S')}"))
        if n > LONGUEUR_REFUSEE:
            raise ErreurEntree(message_long(n))
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            m = message_environnement(e, parent)
            if m and m != MESSAGE_PROTEGE:
                raise ErreurEntree(m) from None
            raise _NonInscriptible(m or f"Dossier de sortie impossible à créer ({e.strerror or e}).") from None
        libre = espace_libre_go(parent)
        if libre is not None and libre < DISQUE_MIN_GO:
            raise ErreurEntree(message_disque(parent))
        for _ in range(10):
            dossier = os.path.join(parent, f"{self.nom}_{time.strftime('%Y%m%d-%H%M%S')}")
            try:
                os.mkdir(dossier)
            except FileExistsError:
                time.sleep(1.0 - (time.time() % 1.0) + 0.01)
                continue
            except OSError as e:
                m = message_environnement(e, dossier)
                if m == MESSAGE_PROTEGE or not m:
                    raise _NonInscriptible(m or f"Dossier de sortie impossible à créer ({e.strerror or e}).") \
                        from None
                raise ErreurEntree(m) from None
            self.sortie = os.path.abspath(dossier)
            break
        else:
            raise ErreurEntree("Impossible de créer un nouveau dossier de calcul (noms horodatés déjà pris).")
        chemins.declarer_sortie(self.sortie)
        self.verrou = chemins.Verrou(os.path.join(self.sortie, ".verrou"))
        if not self.verrou.acquerir():
            raise ErreurEntree(MESSAGE_EN_COURS)

    # ------------------------------------------------------------------------------------------ reprise
    def ouvrir_reprise(self) -> None:
        """--reprendre (§5.3) : verrou, puis rapport inachevé exigé ; instantané des fichiers de technique/ (rendus
        tels quels si les entrées ont changé)."""
        dossier = self.reprise
        if dossier is True:
            parent = self.reglages.valeur("sortie", "dossier")
            dossier = calcul_inacheve(self.source_donnees, parent)
            if dossier is None:
                raise ErreurEntree("--reprendre : aucun calcul inachevé de cette carte dans le dossier de sortie")
        dossier = os.path.abspath(dossier)
        chemins.declarer_sortie(dossier)
        verrou = chemins.Verrou(os.path.join(dossier, ".verrou"))
        try:
            libre = verrou.acquerir()
        except OSError as e:
            raise ErreurEntree(message_environnement(e, dossier) or
                               f"dossier de reprise inaccessible ({e.strerror or e})") from None
        if not libre:
            raise ErreurEntree(MESSAGE_EN_COURS)
        self.verrou = verrou
        self.ancien_rapport = lire_rapport_inacheve(dossier)
        if self.ancien_rapport is None:
            raise ErreurEntree("--reprendre : ce dossier ne contient pas de calcul inachevé (technique/rapport.json "
                               "au format rapport/2 avec termine: false)")
        self.sortie = dossier
        technique = os.path.join(dossier, "technique")
        for sous in ("", "entree"):
            d = os.path.join(technique, sous)
            for n in (os.listdir(d) if os.path.isdir(d) else []):
                c = os.path.join(d, n)
                if os.path.isfile(c) and n != "journal.txt":
                    with open(c, "rb") as f:
                        self.instantane[os.path.join(sous, n)] = f.read()
        self.technique = technique
        self.journal(f"reprise du calcul {os.path.basename(dossier)}")

    def verifier_reprise(self) -> None:
        """Entrées et paramètres effectifs refaits, comparés à ceux du calcul repris ; différents : refus, et les
        fichiers du calcul repris sont rendus tels quels."""
        if self.reprise is None or self.reprise_validee:
            return
        for nom in ("donnees_placeur.json", "parametres_moteur.json"):
            with open(os.path.join(self.technique, nom), "rb") as f:
                nouveau = f.read()
            if self.instantane.get(nom) != nouveau:
                for rel, octets in self.instantane.items():
                    with open(os.path.join(self.technique, rel), "wb") as f:
                        f.write(octets)
                self.journal(f"reprise refusée : {nom} diffère du calcul repris")
                raise ErreurEntree(MESSAGE_ENTREES_CHANGEES)
        self.reprise_validee = True
        self.empreinte_donnees = hashlib.sha256(self.instantane["donnees_placeur.json"]).hexdigest()
        self.journal("reprise : entrées et paramètres effectifs identiques au calcul repris")

    def reprendre_graine(self, g: int, r: dict) -> bool:
        """Graine reprise telle quelle : stats.json présent, fin ≠ arret_demande, empreintes égales (§5.3) ; ses
        contrôles indépendants sont refaits. Rend faux si la graine doit être relancée."""
        dossier = os.path.join(self.technique, f"graine_{g}")
        chemin = os.path.join(dossier, "stats.json")
        if not os.path.isfile(chemin):
            return False
        stats, refus = lire_stats(chemin, 0.0, g)
        if refus or stats.get("fin") in (ARRET_DEMANDE, "erreur_interne"):
            return False
        emp = stats.get("empreintes") or {}
        if emp.get("donnees_sha256") != self.empreinte_donnees:
            return False
        if emp.get("parametres_sha256") != empreinte_parametres(self.params) and \
                _sans_fils(stats.get("parametres_effectifs")) != _sans_fils(self.params):
            return False
        duree = (stats.get("execution") or {}).get("temps_total_s")
        r.update(code=0, duree_s=float(duree) if isinstance(duree, (int, float)) else 0.0, reprise=True)
        self.journal(f"graine {g} reprise telle quelle (calcul précédent)")
        self.verdict_moteur(g, r, 0, dossier, 0.0, [])
        return True

    def effacer_arrets(self) -> None:
        for nom in (ARRET_LANCEMENTS, ARRET_DEMANDE):
            chemin = os.path.join(self.sortie, nom)
            if os.path.exists(chemin):
                try:
                    os.remove(chemin)
                    self.journal(f"fichier d'arrêt {nom} d'un calcul précédent effacé")
                except OSError as e:
                    raise ErreurEntree(f"Fichier d'arrêt {nom} impossible à effacer ({e.strerror}).") from None

    def moteur(self, args: list[str], delai_s: float):
        return chemins.executer(self.base + list(args), delai_s=delai_s, priorite_basse=self.priorite_basse,
                                job=self.job)

    def preparer(self) -> None:
        """Moteur, version, paramètres, estimations ; écrit les fichiers de technique/."""
        r = self.reglages
        self.essais = 0 if self.sans_graine or self.o["valeurs"].get("--essais", "").strip() == "0" else r.essais()
        self.processeurs = r.processeurs()
        self.fils = int(r.valeur("avance", "fils_par_essai") or 1)
        self.priorite_basse = not r.valeur("calcul", "priorite_normale")
        duree_max_min = r.duree_max_min()
        self.estimation["duree_max_s"] = duree_max_min * SECONDES_PAR_MINUTE
        self.echeance = self.t0 + self.estimation["duree_max_s"]
        try:
            self.base = chemins.commande_moteur(NOM_MOTEUR, dossier=self.o["valeurs"].get("--moteurs"),
                                                construire=self.essais > 0, journal=self.journal)
        except (chemins.MoteurAbsent, chemins.ErreurConstruction) as e:
            raise ErreurEntree(str(e)) from None
        try:
            v = chemins.executer(self.base + ["--version"], delai_s=60, priorite_basse=False)
        except OSError as e:
            raise ErreurEntree(f"{chemins.MESSAGE_MOTEUR_ABSENT} ({e.strerror or 'lancement refusé'})") from None
        attendue = f"{NOM_MOTEUR} {self.version}"
        if v.returncode != 0 or v.stdout.strip() != attendue:
            raise ErreurEntree(f"Version du moteur « {chemins.anonymiser(v.stdout.strip()[:80])} » différente de "
                               f"celle du lanceur « {attendue} » : moteur et lanceur doivent venir du même paquet.")
        self.journal(f"moteur : {attendue}")
        try:
            defauts = parametres.defauts_moteur(r.qualite, self.base)
        except parametres.ErreurParametres as e:
            raise ErreurEntree(e.texte_complet()) from None
        score = None
        if self.ancien_rapport is not None:      # reprise : même score, donc même plafond par graine
            ancien = (self.ancien_rapport.get("estimation") or {}).get("score")
            score = float(ancien) if isinstance(ancien, (int, float)) else None
            self.journal(f"reprise : score d'étalonnage du calcul repris ({score})")
        elif self.essais > 0:
            score = self.etalonner()
        self.estimation["score"] = score
        try:
            self.params, self.origines_moteur = parametres.parametres_moteur(
                defauts, r, options_moteur={"campagne.threads": self.fils})
            plafond = parametres.plafond_graine_s(self.params, score)
            plafond = int(plafond) if float(plafond).is_integer() else round(plafond, 3)
            self.params["campagne"]["duree_max_s"] = plafond
        except parametres.ErreurParametres as e:
            raise ErreurEntree(e.texte_complet()) from None
        self.estimation["plafond_graine_s"] = plafond
        self.chemin_params = os.path.join(self.technique, "parametres_moteur.json")
        chemins.ecrire_json(self.chemin_params, self.params)
        parametres.ecrire_parametres_utilises(
            os.path.join(self.technique, "parametres_utilises.ini"), r,
            {"calcul.essais": self.essais, "calcul.duree_max_min": duree_max_min,
             "calcul.processeurs": self.processeurs})
        self.chemin_donnees = os.path.join(self.technique, "donnees_placeur.json")
        if os.path.normcase(os.path.abspath(self.source_donnees)) != os.path.normcase(self.chemin_donnees):
            shutil.copyfile(self.source_donnees, self.chemin_donnees)
        self.journal(f"fichiers de paramètres lus : {', '.join(r.fichiers_lus) or 'aucun'}")
        if r.avance_actives():
            self.journal("clés [avance] actives : " + ", ".join(f"{k} = {v}" for k, v in r.avance_actives().items()))
        self.estimer()

    def etalonner(self):
        try:
            e = self.moteur(["etalonner"], DELAI_ETALONNER_S)
            d = json.loads(e.stdout) if e.returncode == 0 else None
            if not d or formats.valider_etalonnage(d):
                raise ValueError(f"code {e.returncode}")
            self.journal(f"étalonnage de la machine : score {d['score']}")
            return float(d["score"])
        except (ValueError, OSError, chemins.DelaiDepasse) as ex:
            self.avertir(f"étalonnage du moteur impossible ({_texte_erreur(ex)}) : vitesse de référence supposée")
            return None

    def estimer(self) -> None:
        try:
            e = self.moteur(["estimer", "--donnees", self.chemin_donnees, "--parametres", self.chemin_params],
                            DELAI_ESTIMER_S)
        except (OSError, chemins.DelaiDepasse) as ex:
            self.avertir(f"estimation du moteur impossible ({_texte_erreur(ex)}) : mémoire et durée inconnues")
            return
        if e.returncode == 2:
            raise ErreurEntree(chemins.anonymiser((e.stderr or e.stdout or "").strip()[-2000:])
                               or "données refusées par le moteur (code 2)")
        try:
            est = json.loads(e.stdout) if e.returncode == 0 else None
        except ValueError:
            est = None
        if not isinstance(est, dict) or formats.valider_estimation(est):
            self.avertir(f"estimation du moteur illisible (code {code_lisible(e.returncode)}) : mémoire et durée "
                         "inconnues")
            return
        self.estimation["memoire_mo"] = est["memoire_mo"]
        dispo = chemins.memoire_disponible_mo()
        self.estimation["memoire_disponible_mo"] = dispo
        if self.essais > 0 and dispo is not None and isinstance(est["memoire_mo"], (int, float)) \
                and est["memoire_mo"] > 0 and dispo - MARGE_MEMOIRE_MO < est["memoire_mo"]:
            raise ErreurEntree(f"La carte demande environ {int(est['memoire_mo'])} Mo par essai, {dispo} Mo sont "
                               "libres.")
        try:
            graine = parametres.duree_graine_prevue(est, self.params, self.estimation["score"])
            self.estimation["duree_graine_prevue_s"] = graine["total_s"]
        except parametres.EstimationInvalide as ex:
            self.avertir(f"durée inconnue : {ex}")

    # ------------------------------------------------------------------------------------------ ressources
    def paralleles(self) -> int:
        """§5.3 : max(1 ; min(processeurs ÷ fils_par_essai ; ⌊(mémoire disponible − 500 Mo) / mémoire estimée⌋)),
        la mémoire estimée étant remplacée par le plus grand pic mesuré dès la première graine finie."""
        n = max(1, self.processeurs // max(1, self.fils))
        memoire = max(self.pics_memoire) if self.pics_memoire else self.estimation["memoire_mo"]
        dispo = self.estimation["memoire_disponible_mo"]
        if dispo is not None and isinstance(memoire, (int, float)) and memoire > 0:
            n = min(n, math.floor((dispo - MARGE_MEMOIRE_MO) / memoire))
        return max(1, n)

    def refus_lancement(self) -> str | None:
        """Raison de ne plus lancer de graine (définitive), ou None. Appelé juste avant chaque lancement."""
        with self._verrou_etat:
            if self.erreur_moteur is not None:
                return "entree_invalide"
            if self.arret_demande:
                return "demande"
            if self.arret_lancements:
                return "lancements"
            if self.raison_arret in ("duree_max", "valides_voulues"):
                return self.raison_arret
            voulues = self.reglages.valeur("calcul", "valides_voulues")
            if voulues and self.valides >= voulues:
                self.raison_arret = "valides_voulues"
                return self.raison_arret
            maintenant = time.monotonic()
            moyenne = sum(self.durees_finies) / len(self.durees_finies) if self.durees_finies else 0.0
            if maintenant >= self.echeance or maintenant + moyenne > self.echeance:
                self.raison_arret = "duree_max"
                return self.raison_arret
            return None

    # ------------------------------------------------------------------------------------------ arrêts
    def _signal(self, signum, frame):  # noqa: ARG002 - signature imposée par signal
        """Ctrl+C : premier → arret_demande (passé aux moteurs) ; second → arrêt forcé de tous les moteurs."""
        self.signaux += 1
        if self.signaux == 1:
            self.arret_demande = True
            try:
                open(os.path.join(self.sortie, ARRET_DEMANDE), "a").close()
            except (OSError, TypeError):
                pass
        else:
            self.force = True
            if self.job is not None:
                self.job.tout_arreter()

    def sonder_arrets(self) -> None:
        if self.sortie is None:
            return
        if not self.arret_demande and os.path.exists(os.path.join(self.sortie, ARRET_DEMANDE)):
            self.arret_demande = True
        if not self.arret_lancements and os.path.exists(os.path.join(self.sortie, ARRET_LANCEMENTS)):
            self.arret_lancements = True
            self.journal("arrêt des lancements demandé : les graines en cours finissent")
        if self.arret_demande and not getattr(self, "_arret_note", False):
            self._arret_note = True
            try:
                open(os.path.join(self.sortie, ARRET_DEMANDE), "a").close()
            except OSError:
                pass
            self.journal("arrêt demandé : les moteurs écrivent leur état et s'arrêtent")

    # ------------------------------------------------------------------------------------------ une graine
    def graine(self, g: int) -> None:
        """Fil d'une graine : place du sémaphore, moteur, verdict du moteur, contrôles ; résultat dans la file."""
        r = {"graine": g}
        try:
            self._graine(g, r)
        except OSError as e:
            m = message_environnement(e, self.sortie)
            if m:
                r["environnement"] = m
            else:
                r["exception"] = traceback.format_exc()
        except BaseException:  # noqa: BLE001 - remontée au fil principal (code 4)
            r["exception"] = traceback.format_exc()
        self._resultats.put(r)

    def _graine(self, g: int, r: dict) -> None:
        if self.reprise_validee and self.reprendre_graine(g, r):
            return
        r.pop("reprise", None)
        dossier = os.path.join(self.technique, f"graine_{g}")
        place = None
        code = None
        try:
            while place is None:
                if self.refus_lancement():
                    r["etat"] = "non_lancee"
                    return
                place = self.semaphore.prendre(delai_s=0.2)
            if self.refus_lancement():
                r["etat"] = "non_lancee"
                return
            vider_dossier(dossier)
            cmd = self.base + ["campagne", "--donnees", self.chemin_donnees, "--sortie", dossier, "--graine", str(g),
                               "--parametres", self.chemin_params, "--threads", str(self.fils), "--evenements",
                               "--arret", os.path.join(self.sortie, ARRET_DEMANDE)]
            t_lancement = time.time()
            t0 = time.monotonic()
            try:
                p = chemins.lancer(cmd, stdout=chemins.PIPE, stderr=chemins.STDOUT,
                                   priorite_basse=self.priorite_basse, job=self.job)
            except OSError as e:
                r.update(etat="plantee", plantage=True, code=None, duree_s=0.0,
                         texte=f"lancement du moteur refusé ({e.strerror or e})")
                return
            self.journal(f"graine {g} lancée")
            self.evt("graine", graine=g, etat="lancee", valide=None, cout=None, liaisons_ouvertes=None,
                     duree_s=None, texte=f"graine {g} lancée")
            derniers: list[str] = []
            with open(os.path.join(dossier, "console.txt"), "w", encoding="utf-8", newline="\n") as console:
                for ligne in p.stdout:
                    if ligne.startswith("EVT "):
                        self.relayer(g, ligne[4:])
                        continue
                    console.write(chemins.anonymiser(ligne.rstrip("\r\n"), self.sortie) + "\n")
                    derniers = (derniers + [ligne.rstrip("\r\n")])[-30:]
            p.stdout.close()
            code = p.wait()
        finally:
            if place is not None:
                self.semaphore.rendre(place)
        r["duree_s"] = round(time.monotonic() - t0, 3)
        r["code"] = code
        self.verdict_moteur(g, r, code, dossier, t_lancement, derniers)

    def relayer(self, g: int, texte: str) -> None:
        try:
            e = json.loads(texte)
        except ValueError:
            return
        if not isinstance(e, dict):
            return
        if e.get("type") == "phase":
            self.evt("phase", graine=g, phase=e.get("phase"), etat=e.get("etat"), fraction=None)
        elif e.get("type") == "progression":
            self.evt("phase", graine=g, phase=e.get("phase"), etat="en_cours", fraction=e.get("fraction"))

    def verdict_moteur(self, g: int, r: dict, code: int, dossier: str, t_lancement: float, derniers: list) -> None:
        """Traitement du code du moteur (§1.4), puis contrôles indépendants des graines candidates."""
        r.update(plantage=False, stats=None, etat="abandonnee", texte="")
        if code == 2:
            r["etat"] = "entree_invalide"
            r["message"] = chemins.anonymiser("\n".join(derniers).strip(), self.sortie) or \
                "entrée refusée par le moteur (code 2)"
            return
        if code not in (0, 3):
            if self.force:
                r["texte"] = f"arrêt forcé (code {code_lisible(code)})"
            elif self.erreur_moteur is not None:
                r["texte"] = "arrêtée : entrée invalide pour une autre graine"
            elif code == 4:
                r["texte"] = "erreur interne du moteur (code 4)"
            else:
                r.update(etat="plantee", plantage=True, texte=f"plantage du moteur (code {code_lisible(code)})")
            return
        stats, refus = lire_stats(os.path.join(dossier, "stats.json"), t_lancement, g)
        if refus:
            r["texte"] = refus
            return
        r["stats"] = stats
        if stats["fin"] == "erreur_interne":
            r["texte"] = "erreur interne du moteur (stats.fin = erreur_interne)"
            return
        if self.force:
            r["texte"] = "arrêt forcé : contrôles non faits"
            return
        r["controles"] = self.controles.executer(dossier, self.chemin_donnees, self.donnees, self.job, self.sortie)
        r["etat"] = "terminee"

    # ------------------------------------------------------------------------------------------ résultats
    def enregistrer(self, r: dict) -> None:
        """Fil principal : note le résultat d'une graine, met à jour le rapport et émet les évènements."""
        g = r["graine"]
        if "exception" in r:
            raise ErreurInterne(r["exception"])
        if "environnement" in r:
            raise ErreurEntree(r["environnement"])
        if r.get("reprise"):
            self.reprises.append(g)
        if r.get("etat") == "non_lancee":
            self.non_lancees.append(g)
            return
        if r["etat"] == "entree_invalide":
            if self.erreur_moteur is None:
                self.erreur_moteur = r["message"]
                self.journal(f"graine {g} : entrée refusée par le moteur (code 2) ; plus aucune graine lancée")
                if self.job is not None:
                    self.job.tout_arreter()
            r = {**r, "etat": "abandonnee", "texte": "entrée refusée par le moteur (code 2)"}
        e = entree_graine(r)
        self.graines[g] = e
        stats = r.get("stats")
        if stats:
            ex = stats.get("execution") or {}
            if isinstance(ex.get("memoire_pic_mo"), (int, float)) and ex["memoire_pic_mo"] > 0:
                self.pics_memoire.append(int(ex["memoire_pic_mo"]))
                self.estimation["memoire_corrigee_mo"] = max(self.pics_memoire)
            if ex.get("plafond_temps_atteint"):
                self.avertir(f"graine {g} : plafond de temps par graine atteint "
                             f"({self.estimation['plafond_graine_s']} s)")
        if r.get("code") in (0, 3) and r.get("duree_s") is not None:
            with self._verrou_etat:
                self.durees_finies.append(r["duree_s"])
        if e["valide"]:
            with self._verrou_etat:
                self.valides += 1
        for texte in (r.get("controles") or {}).get("avertissements", []):
            self.avertir(f"graine {g} : {texte}")
        if e["incoherence"]:
            self.incoherences += 1
            texte = (f"incohérence de validité, graine {g} : le moteur la déclare valide, les contrôles "
                     "indépendants la refusent")
            self.journal(texte)
            self.evt("erreur", texte=texte, incoherence=True)
        etat_evt = {"terminee": "terminee", "plantee": "plantee"}.get(e["etat"], "abandonnee")
        resume = (f"graine {g} : " + ("valide" if e["valide"] else "non valide") +
                  (f", coût {e['cout_commun']}" if e["cout_commun"] is not None else "") +
                  (f", {e['drc_strict']['ouvertes']} liaisons ouvertes"
                   if e.get("drc_strict") and e["drc_strict"].get("ouvertes") is not None else "") +
                  f", {e['duree_s']} s" + (f" ({e['texte']})" if e["texte"] else ""))
        self.journal(resume)
        self.evt("graine", graine=g, etat=etat_evt, valide=e["valide"], cout=e["cout_commun"],
                 liaisons_ouvertes=(e.get("drc_strict") or {}).get("ouvertes"), duree_s=e["duree_s"], texte=resume)
        self.corriger_durees()
        self.ecrire_rapport()

    def corriger_durees(self) -> None:
        if not self.durees_finies:
            return
        moyenne = sum(self.durees_finies) / len(self.durees_finies)
        self.estimation["duree_graine_mesuree_s"] = round(moyenne, 3)
        restantes = max(0, self.essais - len(self.graines) - len(self.non_lancees))
        ecoule = time.monotonic() - self.t0
        reste = parametres.duree_calcul_prevue_s(moyenne, restantes, self.paralleles(),
                                                 max(0.0, self.estimation["duree_max_s"] - ecoule))
        self.estimation["duree_prevue_corrigee_s"] = round(ecoule + reste, 1)

    def choisir(self) -> None:
        """Graine retenue : valide de plus petit coût commun ; sinon, graine « à terminer » : placement valide et
        aucune violation stricte, le moins de liaisons ouvertes, puis le plus petit coût (§5.3 « Échecs »)."""
        def cout(e):
            return e["cout_commun"] if isinstance(e["cout_commun"], (int, float)) else math.inf
        valides = [e for e in self.graines.values() if e["valide"]]
        self.meilleure = min(valides, key=lambda e: (cout(e), e["graine"]))["graine"] if valides else None
        self.a_terminer = None
        if self.meilleure is None:
            possibles = [e for e in self.graines.values()
                         if e.get("drc_strict") and e["drc_strict"].get("violations") == 0
                         and e["drc_strict"].get("ouvertes") is not None
                         and e.get("placement") and e["placement"].get("valide")]
            if possibles:
                self.a_terminer = min(possibles, key=lambda e: (e["drc_strict"]["ouvertes"], cout(e),
                                                                e["graine"]))["graine"]

    def garder_graines(self) -> None:
        """`garder_graines = meilleure` : fichiers lourds supprimés pour les graines autres que la retenue et la
        graine « à terminer » (§1.3) ; stats.json et contrôles gardés. Un calcul inachevé garde tout (reprise)."""
        if self.reglages.valeur("sortie", "garder_graines") != "meilleure" or not self.termine:
            return
        for g in self.graines:
            if g in (self.meilleure, self.a_terminer):
                continue
            for nom in FICHIERS_LOURDS:
                try:
                    os.remove(os.path.join(self.technique, f"graine_{g}", nom))
                except FileNotFoundError:
                    pass
                except OSError as e:
                    self.avertir(f"graine {g} : {nom} non supprimé ({e.strerror})")

    def rapport(self) -> dict:
        r = self.reglages
        effectifs = {}
        for s, cles in parametres.SCHEMA.items():
            for k, c in cles.items():
                v = r.valeurs[s][k]
                if c.type == "chemin" and v is not None:
                    v = "." if (s, k) == ("sortie", "dossier") else os.path.basename(v)
                effectifs[f"{s}.{k}"] = v
        effectifs.update({"calcul.essais": self.essais, "calcul.duree_max_min": r.duree_max_min(),
                          "calcul.processeurs": self.processeurs, "avance.fils_par_essai": self.fils})
        d = self.donnees
        graines = [self.graines[g] for g in sorted(self.graines)]
        return {
            "format": FORMAT_RAPPORT, "version": self.version, "carte": self.nom, "mode": self.mode,
            "termine": self.termine, "fin": self.fin, "code": self.code, "erreur": self.erreur,
            # `effectifs` = paramètres du moteur complets (convention du validateur du lot E, décision 48) ;
            # les réglages de l'utilisateur (.ini et options) sont dans `utilisateur`.
            "parametres": {"effectifs": self.params, "niveau": r.qualite,
                           "fichiers_ini": list(r.fichiers_lus), "utilisateur": effectifs,
                           "avance": r.avance_actives(), "moteur_origines": self.origines_moteur},
            "import": {"composants": len(d.get("composants") or []), "reseaux": len(d.get("reseaux") or []),
                       "couches": len(d.get("couches") or [])},
            "deduction": self.resume_deduction(),
            "estimation": dict(self.estimation),
            "graines": graines,
            "graines_non_lancees": sorted(self.non_lancees),
            "meilleure": self.meilleure, "a_terminer": self.a_terminer,
            "livrable": self.livrable, "couture": self.couture, "reference_existante": self.reference,
            "incoherences": self.incoherences,
            "avertissements": list(self.avertissements),
            "statistiques": statistiques(graines, (self.reference or {}).get("cout_commun")),
            "duree_totale_s": self.temps(),
        }

    def resume_deduction(self) -> dict:
        return {}

    def apres_choix(self) -> None:
        """Étapes « couture » à « référence existante » (chaîne depuis une carte, CalculCarte)."""

    def ecrire_rapport_txt(self) -> None:
        """rapport.txt (chaîne depuis une carte, CalculCarte)."""

    def ecrire_rapport(self) -> None:
        # Écrit dès que les paramètres du moteur sont connus (`parametres.effectifs` les exige, décision 48), puis
        # après chaque graine et à la fin ; une erreur antérieure laisse technique/erreur.txt seul.
        if self.reprise is not None and not self.reprise_validee:
            return                       # le rapport du calcul repris reste tel quel tant que la reprise n'est pas admise
        if self.technique and self.params is not None:
            chemins.ecrire_json(os.path.join(self.technique, "rapport.json"), self.rapport())

    # ------------------------------------------------------------------------------------------ déroulement
    def graines_en_parallele(self) -> None:
        depart = self.reglages.valeur("calcul", "graine_depart")
        attente = list(range(depart, depart + self.essais))
        for g in attente:
            self.evt("graine", graine=g, etat="en_attente", valide=None, cout=None, liaisons_ouvertes=None,
                     duree_s=None, texte=f"graine {g} en attente")
        actifs: dict[int, threading.Thread] = {}
        while attente or actifs:
            self.sonder_arrets()
            if attente and self.refus_lancement():
                self.non_lancees.extend(attente)
                attente = []
            while attente and len(actifs) < self.paralleles():
                g = attente.pop(0)
                t = threading.Thread(target=self.graine, args=(g,), name=f"graine_{g}", daemon=True)
                actifs[g] = t
                t.start()
            try:
                r = self._resultats.get(timeout=PAUSE_S)
            except queue.Empty:
                continue
            actifs.pop(r["graine"]).join()
            self.enregistrer(r)

    def executer(self) -> int:
        self.creer_dossier()
        self.effacer_arrets()
        self.job = chemins.Job()
        self.journal(f"placeur {self.version} : calcul {os.path.basename(self.sortie)} (données "
                     f"{os.path.basename(self.source_donnees)})")
        self.evt("etape", etape="donnees", etat="debut", texte="paramètres, moteur et estimations")
        self.ecrire_rapport()
        self.preparer()
        self.verifier_reprise()
        self.evt("etape", etape="donnees", etat="fin", texte="paramètres, moteur et estimations")
        paralleles = max(1, min(self.essais, self.paralleles())) if self.essais else 0
        self.estimation["paralleles"] = paralleles
        if self.estimation["duree_graine_prevue_s"] is not None:
            self.estimation["duree_prevue_s"] = parametres.duree_calcul_prevue_s(
                self.estimation["duree_graine_prevue_s"], self.essais, max(1, paralleles),
                self.estimation["duree_max_s"])
        self.journal(f"{self.essais} graine(s), {paralleles} en parallèle, {self.fils} fil(s) par essai, niveau "
                     f"{self.reglages.qualite} ; durée prévue "
                     + (f"{self.estimation['duree_prevue_s']} s" if self.estimation["duree_prevue_s"] is not None
                        else "inconnue") + f", au plus {self.estimation['duree_max_s']} s")
        self.evt("debut", version=self.version, essais=self.essais, paralleles=paralleles, fils_par_essai=self.fils,
                 qualite=self.reglages.qualite, sortie=self.sortie, duree_prevue_s=self.estimation["duree_prevue_s"],
                 duree_max_s=self.estimation["duree_max_s"], t=self.temps())
        self.ecrire_rapport()
        self.semaphore = chemins.SemaphoreMoteurs(max(1, self.processeurs // max(1, self.fils)))
        veille = chemins.empecher_veille(True)
        try:
            self.evt("etape", etape="graines", etat="debut", texte=f"{self.essais} graine(s)")
            self.sonder_arrets()
            self.graines_en_parallele()
            self.evt("etape", etape="graines", etat="fin", texte=f"{len(self.graines)} graine(s) calculée(s)")
        finally:
            if veille:
                chemins.empecher_veille(False)
        return self.conclure()

    def conclure(self) -> int:
        self.evt("etape", etape="rapport", etat="debut", texte="choix de la graine et rapport")
        self.sonder_arrets()                      # un arrêt vu par les moteurs avant le dernier sondage
        if self.erreur_moteur is not None:
            self.code, self.termine, self.erreur = 2, True, self.erreur_moteur
        elif self.arret_demande or self.arret_lancements:
            self.code, self.fin, self.termine = 3, "arret_demande", False
        else:
            self.termine = True
            if self.raison_arret in ("duree_max", "valides_voulues"):
                self.fin = self.raison_arret
        self.choisir()
        if self.code is None:
            self.code = 0 if self.meilleure is not None else 1
        if self.code != 2:
            self.apres_choix()
        self.garder_graines()
        self.ecrire_rapport()
        self.ecrire_rapport_txt()
        if self.code == 2:
            texte = f"entrée refusée par le moteur : {self.erreur_moteur}"
            self.flux_erreur.write(f"erreur : {texte}\n")
        elif self.meilleure is not None:
            texte = f"graine retenue : {self.meilleure} (coût {self.graines[self.meilleure]['cout_commun']})"
        elif self.a_terminer is not None:
            texte = f"aucune graine valide ; graine à terminer : {self.a_terminer}"
        else:
            texte = "aucune graine valide"
        if self.code == 3:
            texte += " ; calcul arrêté à la demande"
        self.journal(texte + f" ; code {self.code} ; {self.temps()} s")
        self.evt("etape", etape="rapport", etat="fin", texte="rapport écrit")
        self.evt("fin", code=self.code, meilleure=self.meilleure,
                 livrable=self.livrable["fichier"] if self.livrable else None, texte=texte)
        return self.code

    def fermer(self) -> None:
        if self.job is not None:
            self.job.fermer()
        if self.semaphore is not None:
            self.semaphore.fermer()
        if self.verrou is not None:
            self.verrou.liberer(supprimer=True)


def entree_graine(r: dict) -> dict:
    """Entrée `graines[]` de rapport/2 (§2.9) d'après le résultat d'un fil de graine ; verdict de §4.6-4 et 5."""
    stats = r.get("stats") or {}
    ctl = r.get("controles") or {}
    ex = stats.get("execution") or {}
    placement = _resume_placement(ctl.get("placement"))
    strict = _resume_verdict(ctl.get("drc_strict"))
    altium = _resume_verdict(ctl.get("drc_altium"))
    candidate = r.get("etat") == "terminee"
    valide_moteur = bool(stats.get("valide")) if stats else None
    valide = bool(candidate and valide_moteur and placement and placement["valide"] and strict and strict["valide"]
                  and altium and altium["valide"])
    mesures = ctl.get("mesures")
    cout = mesures.get("cout_commun") if isinstance(mesures, dict) else None
    if not isinstance(cout, (int, float)):
        cout = stats.get("cout_commun")
    return {
        "graine": r["graine"], "dossier": f"technique/graine_{r['graine']}", "code": r.get("code"),
        "fin": stats.get("fin") or "", "plantage": bool(r.get("plantage")), "duree_s": r.get("duree_s"),
        "valide_moteur": valide_moteur, "placement": placement, "drc_strict": strict, "drc_altium": altium,
        "valide": valide, "cout_commun": cout if isinstance(cout, (int, float)) else None,
        "incoherence": bool(candidate and valide_moteur and not valide),
        "etat": r.get("etat"), "texte": r.get("texte") or "",
        "cause_moteur": stats.get("cause"), "cout_moteur": stats.get("cout_commun"),
        "temps_calcul_s": ex.get("temps_total_s"), "memoire_pic_mo": ex.get("memoire_pic_mo"),
        "plafond_temps_atteint": bool(ex.get("plafond_temps_atteint")),
    }


def lire_stats(chemin: str, t_lancement: float, graine: int) -> tuple[dict | None, str | None]:
    """stats.json d'une graine, s'il est recevable (§1.4) : présent, postérieur au lancement, au format stats/2 et
    de la bonne graine. Rend (stats, None) ou (None, raison du refus)."""
    try:
        date = os.stat(chemin).st_mtime
    except FileNotFoundError:
        return None, "stats.json absent"
    if date < t_lancement - TOLERANCE_DATE_S:
        return None, "stats.json antérieur au lancement (essai précédent) : ignoré"
    try:
        stats = formats.charger(chemin, "stats")
    except formats.ErreurFormat as e:
        return None, f"stats.json refusé : {e}"
    except OSError as e:
        return None, f"stats.json illisible ({e.strerror})"
    if stats.get("graine") != graine:
        return None, f"stats.json d'une autre graine ({stats.get('graine')!r})"
    return stats, None


def vider_dossier(dossier: str) -> None:
    """Vide `graine_<G>/` avant le lancement (§1.3) : rien d'un essai précédent n'est relu."""
    for essai in range(chemins.ESSAIS_ECRITURE):
        try:
            if os.path.isdir(dossier):
                shutil.rmtree(dossier)
            os.makedirs(dossier)
            return
        except PermissionError:
            if essai + 1 >= chemins.ESSAIS_ECRITURE:
                raise
            time.sleep(chemins.PAUSE_ECRITURE_S)


# ================================================================================================ chaîne depuis une carte

GENRES = {".pcbdoc": "pcbdoc", ".kicad_pcb": "kicad"}
GENERATEURS = {"pcbdoc": "generer_pcbdoc", "kicad": "generer_kicad"}
EXTENSIONS = {"pcbdoc": ".PcbDoc", "kicad": ".kicad_pcb"}
LOGICIEL = {"pcbdoc": "Altium", "kicad": "KiCad"}
MAX_CARTES = 3                    # §5.3 « Échecs » : au plus 3 graines essayées pour la carte produite
SUFFIXES = {"valide": "", "a_terminer": "_A_TERMINER", "apercu": "_APERCU"}
NOMS_LIVRABLE = {"valide": "valide", "a_terminer": "à terminer", "apercu": "aperçu"}
TAILLE_TRANCHE = 1 << 20

# Défauts de contraintes/2 (§2.3) pour le modèle de --init ; les clés dont le défaut dépend des règles du fichier
# (MODELE_DEPENDANTS) ne sont pas écrites (une valeur figée remplacerait la règle) : leur défaut est dans `_aide`.
MODELE_CONTRAINTES = {
    "repere": ("fichier", "repère des coordonnées : « fichier » (coordonnées affichées par Altium ou KiCad) ou "
                          "« carte »"),
    "unite": ("mm", "unité des longueurs : « mm » ou « mil »"),
    "fixes": ([], "pièces laissées à leur pose d'entrée (désignateurs ou motifs)"),
    "fixes_deplaces": ({}, "pièces fixes posées ailleurs : {\"U1\": [x, y]} ou {\"U1\": {\"x\", \"y\", \"rotation\", "
                           "\"face\"}}"),
    "liberer": ([], "pièces rendues mobiles malgré une fixation automatique"),
    "fixer_suggestions": (True, "fixe les connecteurs et interfaces posés dans le contour (R-P3)"),
    "fixer_verrouilles": (True, "fixe les pièces verrouillées dans le fichier"),
    "dessus_seulement": ([], "pièces permises seulement dessus (désignateurs, « empreinte:<motif> », "
                             "« nature:<nature> »)"),
    "dessous_seulement": ([], "pièces permises seulement dessous"),
    "orientations": ({}, "orientations permises : {\"U1\": [[0, false], [90, false]]}"),
    "faces_par_defaut": ("auto", "« auto », « libres » ou « actuelle »"),
    "rotations_dessous_multibroches": ([0, 90, 180, 270], "rotations permises dessous aux pièces de plus de 2 "
                                                          "pastilles"),
    "hauteur_inconnue": ("face_entree", "pièce de hauteur inconnue : « face_entree » ou « basse »"),
    "exceptions_hauteur": ([], "pièces dispensées de la règle de hauteur"),
    "face_encombrants": ("actuelle", "face des traversantes et des pièces hautes : « actuelle » ou « dessus »"),
    "boites": ({}, "boîte de placement d'une pièce : {\"U1\": [x0, y0, x1, y1]} (repère local)"),
    "au_bord": ([], "pièces contre un côté : [{\"piece\", \"cote\", \"debord_mm\"}]"),
    "zones_imposees": ([], "zones imposées : [{\"pieces\", \"faces\", \"rect\" ou \"polygone\", \"etiquette\"}]"),
    "salles_comme_zones": (True, "une salle Altium dans le contour devient une zone imposée"),
    "groupes": ([], "groupes imposés : [{\"nom\", \"pieces\", \"rigide\"}]"),
    "groupes_exclus": ([], "groupes déduits à ne pas utiliser"),
    "motif_canaux": ("^(.+?)(_[A-Za-z]+\\d+)$", "repli de la détection des canaux répétés"),
    "pas_fin_mm": (0.65, "seuil de pas fin (mm)"),
    "via_dans_pastille_meme_reseau": (False, "via permis dans une pastille CMS de son réseau"),
    "sans_piste_dessous": ({}, "{\"ajouter\": [pièces], \"retirer\": [pièces]}"),
    "zones_interdites": ([], "zones interdites : [{\"faces\", \"rect\" ou \"polygone\", \"effets\", \"couches\", "
                             "\"etiquette\"}]"),
    "zones_autour": ([], "zones carrées autour de pièces fixes"),
    "marge_decoupe_mm": (1.0, "marge de placement aux découpes (mm)"),
    "marge_traversante_mm": (0.6, "marge des pastilles de la face opposée (mm)"),
    "rayon_interdit_trou_mm": (None, "exclusion de placement autour de chaque trou ; null : calculé par trou"),
    "trous_supplementaires": ([], "trous absents du fichier : [{\"x\", \"y\", \"diametre\", \"diametre_cuivre\", "
                                  "\"metallise\"}]"),
    "cuivre_a_garder": ([], "cuivre existant conservé dans la carte produite"),
    "couches": ({}, "surcharges des couches : {\"routage\", \"plans\", \"inutilisees\", \"preferees\"}"),
    "couche_preferee_par_nom": (False, "active la règle R-C6"),
    "roles": ({}, "surcharges des rôles des réseaux (motifs)"),
    "reseaux": ({}, "surcharges par réseau (role, largeurs_mm, couche_preferee…)"),
    "paires": ([], "paires différentielles"),
    "eloignements": ([], "distances minimales à un point de référence"),
    "charges": ([], "charges des chutes de tension : [{\"broche\", \"courant_a\"}]"),
    "convertisseurs": ({}, "surcharges des convertisseurs : {\"ajouter\", \"modifier\", \"supprimer\"}"),
}
MODELE_DEPENDANTS = {
    "hauteur_max_dessus_mm": "règle Height du fichier, sinon aucune",
    "hauteur_max_dessous_mm": "règle Height du fichier, sinon 3,0",
    "via_sous_cms_permis": "règle ViasUnderSMD du fichier",
    "marge_bord_mm": "règle garde_bord du fichier, sinon 1,0",
    "garde_bord_mm": "règle garde_bord du fichier, sinon 0,6",
    "garde_decoupe_mm": "= garde_bord_mm",
    "garde_trou_mm": "= garde_decoupe_mm",
    "marge_piece_mm": "max(0,25 ; isolement_pieces / 2)",
    "grille_placement_mm": "0,127 (Altium), 0,05 (KiCad)",
}


def sha256_fichier(chemin: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(TAILLE_TRANCHE), b""):
            h.update(bloc)
    return h.hexdigest()


def genre_carte(chemin: str) -> str:
    ext = os.path.splitext(chemin)[1].lower()
    if ext not in GENRES:
        raise ErreurEntree(f"carte {os.path.basename(chemin)} : extension {ext or '(aucune)'} non prise en charge "
                           "(.PcbDoc ou .kicad_pcb)")
    return GENRES[ext]


class Outils:
    """API de §5.2 des lots B et C pour la chaîne depuis une carte. Fonctions pures appelées dans le processus
    (import, contrôle du gabarit, déduction, construction, référence, couture, génération : choix (c) pour les
    générateurs, permis par §5.2, décision 49) ; `drc_maison` avec couture en processus séparé
    (`chemins.commande_outil`). Une fonction absente lève ControleIndisponible. Les essais remplacent ces méthodes
    par des doublures."""

    _fonction = staticmethod(Controles._fonction)

    def importer(self, chemin):
        return self._fonction("importer_carte", "importer")(chemin)

    def controler_gabarit(self, genre, gabarit, entree):
        return self._fonction(GENERATEURS[genre], "controler_gabarit")(gabarit, entree)

    def deduire(self, entree, surcharges=None):
        return self._fonction("deduire_config", "deduire")(entree, surcharges)

    def construire(self, entree, contraintes, config, controle_gabarit=None):
        return self._fonction("construire_donnees", "construire")(entree, contraintes, config, controle_gabarit)

    def extraire_reference(self, chemin_carte, entree):
        return self._fonction("extraire_reference", "extraire")(chemin_carte, entree)

    def couture(self, donnees, placements, routage):
        return self._fonction("vias_couture", "calculer")(donnees, placements, routage)

    def drc_couture(self, chemin_donnees, chemin_placement, chemin_routage, chemin_couture, chemin_sortie, job=None):
        cmd = chemins.commande_outil("drc_maison", "--donnees", chemin_donnees, "--placement", chemin_placement,
                                     "--routage", chemin_routage, "--couture", chemin_couture, "--mode", "strict",
                                     "--sortie", chemin_sortie)
        r = chemins.executer(cmd, delai_s=DELAI_DRC_S, job=job)
        if r.returncode not in (0, 1):
            raise RuntimeError(f"drc_maison --couture : code {r.returncode}")
        return formats.charger(chemin_sortie, "verdict")

    def generer(self, genre, dossier_graine, sortie, gabarit, config, sans_serigraphie=False, opts=None):
        f = self._fonction(GENERATEURS[genre], "generer")
        if genre == "pcbdoc":
            return f(dossier_graine, sortie, gabarit, config, sans_serigraphie=sans_serigraphie, opts=opts)
        return f(dossier_graine, sortie, gabarit, config)


def _appel_api(etape: str, fmt: str | None, fonction):
    """Appel d'une fonction de B ou C pendant la préparation : toute erreur est une entrée refusée (code 2), le
    résultat est validé au format de §2 (choix (c), décision 49)."""
    try:
        d = fonction()
    except ErreurEntree:
        raise
    except ControleIndisponible as e:
        raise ErreurEntree(f"{etape} impossible : {e}") from None
    except Exception as e:  # noqa: BLE001 - un refus de B ou C arrête la chaîne avec son message
        raise ErreurEntree(f"{etape} : {_texte_erreur(e)}") from None
    if fmt is not None:
        erreurs = formats.VALIDATEURS[fmt](d)
        if erreurs:
            raise ErreurEntree(f"{etape} : résultat refusé ({fmt}) : " + " ; ".join(erreurs[:3]))
    return d


def _nb(v) -> str:
    """Nombre lisible à la française (virgule décimale)."""
    if v is None:
        return "?"
    if isinstance(v, float):
        v = round(v, 3)
        if v.is_integer():
            v = int(v)
    return str(v).replace(".", ",")


def contraintes_utilisateur(reglages) -> tuple:
    chemin = reglages.valeur("carte", "contraintes")
    if not chemin:
        return None, []
    try:
        return formats.lire_contraintes(chemin)
    except formats.ErreurFormat as e:
        raise ErreurEntree(str(e)) from None
    except OSError as e:
        raise ErreurEntree(f"fichier de contraintes illisible ({e.strerror})") from None


class CalculCarte(Calcul):
    """Chaîne complète de §5.3 depuis une carte .PcbDoc ou .kicad_pcb (et --analyser)."""

    def __init__(self, o, reglages, carte, outils: Outils, controles, flux, flux_erreur, analyser=False):
        self.genre = genre_carte(carte)
        if not os.path.isfile(carte):
            raise ErreurEntree(f"carte introuvable : {os.path.basename(carte)}")
        super().__init__(o, reglages, os.path.abspath(carte), {}, controles, flux, flux_erreur)
        self.carte = os.path.abspath(carte)
        self.outils = outils
        self.mode = "analyse" if analyser else "carte"
        self.sans_graine = analyser
        self.entree = self.config = self.deduction = self.gabarit = None
        self.copie = None
        try:
            self.sha_entree = sha256_fichier(self.carte)
        except OSError as e:
            if carte_verrouillee(self.carte, e):
                raise ErreurEntree(MESSAGE_VERROUILLEE) from None
            raise ErreurEntree(f"carte illisible ({e.strerror or e})") from None
        self.cartes_echouees: list[dict] = []
        self.liaisons_a_finir: list[dict] = []

    # ------------------------------------------------------------------------------------------ préparation
    def ecrire(self, nom: str, d: dict) -> str:
        chemin = os.path.join(self.technique, nom)
        chemins.ecrire_json(chemin, d)
        return chemin

    def preparer(self) -> None:
        self.preparer_carte()
        super().preparer()

    def preparer_carte(self) -> None:
        """Copie, import, contrôle du gabarit, déduction, construction (§5.3, déroulement)."""
        self.evt("etape", etape="import", etat="debut", texte="lecture de la carte")
        dossier = os.path.join(self.technique, "entree")
        os.makedirs(dossier, exist_ok=True)
        self.copie = os.path.join(dossier, os.path.basename(self.carte))
        try:
            if os.path.normcase(os.path.abspath(self.carte)) != os.path.normcase(os.path.abspath(self.copie)):
                shutil.copyfile(self.carte, self.copie)   # reprise : la carte est déjà la copie du calcul
        except OSError as e:
            if carte_verrouillee(self.carte, e):
                raise ErreurEntree(MESSAGE_VERROUILLEE) from None
            raise ErreurEntree(message_environnement(e, self.sortie) or f"carte illisible ({e.strerror})") from None
        if self.carte.lower().endswith(".kicad_pcb"):   # §2.1, étape 1 : `.kicad_pro` (et `.kicad_dru`) voisins
            base = os.path.splitext(self.carte)[0]
            for ext in (".kicad_pro", ".kicad_dru"):
                if os.path.isfile(base + ext):
                    try:
                        shutil.copyfile(base + ext, os.path.splitext(self.copie)[0] + ext)
                    except OSError as e:
                        raise ErreurEntree(f"projet KiCad illisible : {os.path.basename(base + ext)} "
                                           f"({e.strerror})") from None
        self.entree = _appel_api("import de la carte", "entree_carte", lambda: self.outils.importer(self.copie))
        self.ecrire("entree_carte.json", self.entree)
        for a in self.entree.get("avertissements") or []:
            self.avertir(f"import : {a}")
        self.journal(f"import : {len(self.entree['composants'])} composant(s), {len(self.entree['reseaux'])} "
                     f"réseau(x), {len(self.entree['carte']['couches'])} couche(s)")
        self.gabarit = _appel_api("contrôle du gabarit", "controle_gabarit",
                                  lambda: self.outils.controler_gabarit(self.genre, self.copie, self.entree))
        self.ecrire("controle_gabarit.json", self.gabarit)
        for a in self.gabarit.get("avertissements") or []:
            self.avertir(f"gabarit : {a}")
        if not self.gabarit["utilisable"]:
            raise ErreurEntree("carte inutilisable comme gabarit : " + " ; ".join(self.gabarit["erreurs"] or ["?"]))
        self.evt("etape", etape="import", etat="fin", texte="carte lue")
        self.evt("etape", etape="deduction", etat="debut", texte="déduction et construction des données")
        contraintes, avert = contraintes_utilisateur(self.reglages)
        for a in avert:
            self.avertir(f"contraintes : {a}")
        nom_c = os.path.basename(self.reglages.valeur("carte", "contraintes") or "contraintes.json")
        effectives, avert = parametres.fusionner_contraintes(contraintes, self.reglages, nom_c)
        for a in avert:
            self.avertir(a)
        erreurs = formats.valider_contraintes(effectives)
        if erreurs:
            raise ErreurEntree("contraintes effectives refusées : " + " ; ".join(erreurs[:3]))
        self.ecrire("contraintes_effectives.json", effectives)
        surcharges = None
        if self.reglages.valeur("carte", "config_convertisseurs"):
            try:
                surcharges = formats.lire_config_utilisateur(self.reglages.valeur("carte", "config_convertisseurs"))
            except formats.ErreurFormat as e:
                raise ErreurEntree(str(e)) from None
            if isinstance(surcharges, tuple):
                surcharges = surcharges[0]
        self.config = _appel_api("déduction", "config_carte", lambda: self.outils.deduire(self.entree, surcharges))
        self.ecrire("config_carte.json", self.config)
        res = _appel_api("construction des données", None,
                         lambda: self.outils.construire(self.entree, effectives, self.config, self.gabarit))
        if not (isinstance(res, tuple) and len(res) == 2):
            raise ErreurEntree("construction des données : (donnees, deduction) attendu")
        donnees, deduction = res
        for fmt, d in (("donnees_placeur", donnees), ("deduction", deduction)):
            erreurs = formats.VALIDATEURS[fmt](d)
            if erreurs:
                raise ErreurEntree(f"construction des données : résultat refusé ({fmt}) : " + " ; ".join(erreurs[:3]))
        self.donnees, self.deduction = donnees, deduction
        self.source_donnees = self.ecrire("donnees_placeur.json", donnees)
        self.ecrire("deduction.json", deduction)
        for a in deduction.get("avertissements") or []:
            self.avertir(f"déduction : {a}")
        for a in deduction.get("a_confirmer") or []:
            self.avertir(f"à confirmer : {a['objet']} : {a['proposition']} ({a['raison']})")
        self.evt("etape", etape="deduction", etat="fin", texte="données construites")

    def resume_deduction(self) -> dict:
        d = self.deduction
        if not d:
            return {}
        return {"elements": len(d["elements"]), "suggestions": len(d["suggestions"]),
                "a_confirmer": len(d["a_confirmer"]), "avertissements": len(d["avertissements"]),
                "puissance": self.reseaux_puissance()}

    def reseaux_puissance(self) -> list[str]:
        return sorted(str(r.get("nom", "?")) for r in (self.donnees or {}).get("reseaux") or []
                      if isinstance(r, dict) and r.get("role") == "puissance")

    def rapport(self) -> dict:
        d = super().rapport()
        if self.entree:
            d["import"] = {"composants": len(self.entree["composants"]), "reseaux": len(self.entree["reseaux"]),
                           "couches": len(self.entree["carte"]["couches"])}
        d["cartes_echouees"] = list(self.cartes_echouees)
        return d

    # ------------------------------------------------------------------------------------------ analyse
    def executer(self) -> int:
        if not self.sans_graine:
            return super().executer()
        self.creer_dossier()
        self.effacer_arrets()
        self.job = chemins.Job()
        self.journal(f"placeur {self.version} : analyse de {os.path.basename(self.carte)}")
        self.preparer()
        # évènement `debut` de l'analyse (fenêtre, §5.5) : dossier, prévisions ; aucune graine n'est lancée
        e = self.estimation
        prevue = None
        if e.get("duree_graine_prevue_s") is not None and e.get("duree_max_s") is not None:
            n = max(1, self.reglages.essais())
            prevue = parametres.duree_calcul_prevue_s(e["duree_graine_prevue_s"], n, max(1, min(
                n, int(getattr(self, "processeurs", 1) or 1) // max(1, int(getattr(self, "fils", 1) or 1)))),
                e["duree_max_s"])
        self.evt("debut", version=self.version, essais=0, paralleles=0, fils_par_essai=getattr(self, "fils", 1),
                 qualite=self.reglages.qualite, sortie=self.sortie, duree_prevue_s=prevue,
                 duree_max_s=e.get("duree_max_s"), t=self.temps())
        self.termine, self.code = True, 0
        self.ecrire_rapport()
        self.ecrire_rapport_txt()
        self.journal(f"analyse terminée ; code 0 ; {self.temps()} s")
        self.evt("fin", code=0, meilleure=None, livrable=None, texte="analyse terminée")
        return 0

    # ------------------------------------------------------------------------------------------ après le choix
    def apres_choix(self) -> None:
        """Référence existante, couture, carte produite (repli sur 3 graines au plus) (§5.3)."""
        self.reference_existante()
        r = self.reglages
        if not r.valeur("pcbdoc", "produire"):
            self.journal("carte produite : non demandée ([pcbdoc] produire = non)")
            return
        apercu = r.qualite == "apercu"

        def cout(e):
            return e["cout_commun"] if isinstance(e["cout_commun"], (int, float)) else math.inf
        valides = sorted((e for e in self.graines.values() if e["valide"]), key=lambda e: (cout(e), e["graine"]))
        if valides:
            type_, candidats = ("apercu" if apercu else "valide"), [e["graine"] for e in valides][:MAX_CARTES]
        elif self.a_terminer is not None and (apercu or r.valeur("pcbdoc", "produire_si_invalide")):
            type_, candidats = ("apercu" if apercu else "a_terminer"), [self.a_terminer]
        else:
            self.journal("carte produite : aucune graine utilisable")
            return
        self.evt("etape", etape="carte", etat="debut", texte="couture et carte produite")
        for g in candidats:
            if self.produire_carte(g, type_):
                if valides and g != self.meilleure:
                    self.avertir(f"carte produite avec la graine valide suivante {g} (graine {self.meilleure} en "
                                 "échec)")
                    self.meilleure = g
                break
        else:
            self.avertir(f"carte produite en échec pour {len(candidats)} graine(s) : aucun livrable")
            if self.code == 0:
                self.code = 1
        self.evt("etape", etape="carte", etat="fin", texte="carte produite" if self.livrable else "aucune carte")

    def coudre(self, g: int, dossier: str, placements: dict, routage: dict) -> None:
        """Vias de couture, puis drc strict avec couture ; vias fautifs retirés et signalés (§5.3)."""
        cp = os.path.join(dossier, "placements_routage.json")
        cr = os.path.join(dossier, "routage_detaille.json")
        cv = os.path.join(dossier, "vias_couture.json")
        cd = os.path.join(dossier, "drc_couture.json")
        try:
            v = self.outils.couture(self.donnees, placements, routage)
            erreurs = formats.valider_vias_couture(v)
            if erreurs:
                raise formats.ErreurFormat(erreurs)
            chemins.ecrire_json(cv, v)
            verdict = self.outils.drc_couture(self.chemin_donnees, cp, cr, cv, cd, self.job)
            retires = 0
            if _nombre(verdict.get("violations")):
                positions = [p["position_mm"] for p in verdict.get("detail_violations") or []
                             if isinstance(p, dict) and p.get("position_mm")]
                garde = []
                for via in v["vias"]:
                    x, y, rayon = via["x"] * formats.MIL, via["y"] * formats.MIL, via["size"] * formats.MIL / 2
                    if any(math.hypot(x - px, y - py) <= rayon + 1e-3 for px, py in positions):
                        retires += 1
                    else:
                        garde.append(via)
                v["vias"] = garde
                chemins.ecrire_json(cv, v)
                verdict = self.outils.drc_couture(self.chemin_donnees, cp, cr, cv, cd, self.job)
                if _nombre(verdict.get("violations")):
                    retires += len(garde)
                    v["vias"] = []
                    chemins.ecrire_json(cv, v)
                    verdict = self.outils.drc_couture(self.chemin_donnees, cp, cr, cv, cd, self.job)
                self.avertir(f"couture de la graine {g} : {retires} via(s) fautif(s) retiré(s)")
            self.couture = {"vias": len(v["vias"]), "drc": _resume_verdict(verdict), "retires": retires,
                            "graine": g}
            self.journal(f"couture : {len(v['vias'])} via(s), {retires} retiré(s)")
        except Exception as e:  # noqa: BLE001 - la couture manquante n'empêche pas la carte
            texte = chemins.anonymiser(f"couture impossible ({_texte_erreur(e)}) : carte sans vias de couture",
                                       self.sortie)
            self.avertir(texte)
            for chemin in (cv, cd):
                try:
                    os.remove(chemin)
                except OSError:
                    pass
            self.couture = {"vias": 0, "drc": None, "retires": 0, "graine": g, "erreur": texte}

    def config_pcbdoc(self, type_: str, couture: bool) -> dict:
        """config_pcbdoc/2 (§2.9) ; trous supplémentaires = trous de donnees.trous absents de la carte."""
        connus = {t.get("id") for t in self.entree["carte"].get("trous") or []}
        trous = []
        for t in self.donnees.get("trous") or []:
            if t.get("id") in connus:
                continue
            d = max(float(t.get("diametre_cuivre") or 0.0), float((t.get("trou") or {}).get("diametre") or 0.0))
            trous.append([t["x"], t["y"], round(d / 2, 6)])
        return {"format": "config_pcbdoc/2", "livrable": type_,
                "serigraphie": {"graine": 1, "chaines": 16, "mouvements": 20000000, "threads": self.processeurs,
                                "tailles_mm": "auto", "garde_masque_mm": None, "trous_supplementaires_mm": trous,
                                "options": []},
                "vias_couture": couture}

    def produire_carte(self, g: int, type_: str) -> bool:
        r = self.reglages
        dossier = os.path.join(self.technique, f"graine_{g}")
        nom = f"{self.nom}{SUFFIXES[type_]}_g{g}{EXTENSIONS[self.genre]}"
        sortie = os.path.join(self.sortie, nom)
        couture = bool(r.valeur("pcbdoc", "vias_couture"))
        try:
            placements = formats.charger(os.path.join(dossier, "placements_routage.json"), "placements")
            routage = formats.charger(os.path.join(dossier, "routage_detaille.json"), "routage")
        except (formats.ErreurFormat, OSError) as e:
            self.cartes_echouees.append({"graine": g, "raison": _texte_erreur(e)})
            return False
        if couture:
            self.coudre(g, dossier, placements, routage)
            couture = self.couture is not None and "erreur" not in self.couture
        config = self.config_pcbdoc(type_, couture)
        chemin_config = self.ecrire("config_pcbdoc.json", config)
        rapport, raison = None, None
        try:
            rapport = self.outils.generer(self.genre, dossier, sortie, self.copie, chemin_config,
                                          sans_serigraphie=not r.valeur("pcbdoc", "serigraphie"),
                                          opts={"entree": self.entree})
            erreurs = formats.valider_rapport_pcbdoc(rapport)
            if erreurs:
                raise formats.ErreurFormat(erreurs)
            self.ecrire(f"carte_{g}_rapport.json", rapport)
            if rapport["controles"]["erreurs"]:
                raison = "erreurs de contrôle : " + " ; ".join(rapport["controles"]["erreurs"][:3])
            elif rapport["conflits_isolement"]:
                raison = f"{rapport['conflits_isolement']} conflit(s) d'isolement"
            elif not os.path.isfile(sortie):
                raison = "fichier de carte absent"
        except Exception as e:  # noqa: BLE001 - échec de la carte produite : graine suivante (§5.3)
            raison = _texte_erreur(e)
        if sha256_fichier(self.carte) != self.sha_entree:
            raise ErreurInterne("la carte d'entrée a changé pendant le calcul (SHA-256)")
        if raison:
            raison = chemins.anonymiser(raison, self.sortie)
            self.avertir(f"carte produite en échec pour la graine {g} : {raison}")
            self.cartes_echouees.append({"graine": g, "raison": raison})
            try:
                os.remove(sortie)
            except OSError:
                pass
            return False
        self.livrable = {"fichier": nom, "type": type_, "graine": g, "erreurs": list(rapport["controles"]["erreurs"]),
                         "anomalies": list(rapport["controles"]["anomalies"]),
                         "serigraphie": rapport["serigraphie"]["etat"],
                         "polygones_a_recouler": rapport["polygones_a_recouler"]}
        if type_ != "valide":
            self.liaisons_a_finir = self.liaisons_ouvertes(g)
        self.journal(f"carte produite : {nom}")
        return True

    def liaisons_ouvertes(self, g: int) -> list[dict]:
        try:
            v = formats.charger(os.path.join(self.technique, f"graine_{g}", "drc_strict.json"), "verdict")
        except (formats.ErreurFormat, OSError):
            return []
        return [x for x in v.get("detail_liaisons_ouvertes") or [] if isinstance(x, dict)]

    def reference_existante(self) -> None:
        """Carte d'entrée routée (cuivre libre) : extraire_reference, drc strict, mesures (§5.3)."""
        if not (self.entree["carte"].get("cuivre_existant") or []):
            return
        dossier = os.path.join(self.technique, "reference_existante")
        try:
            res = self.outils.extraire_reference(self.copie, self.entree)
            placements, routage = res[0], res[1]
            for fmt, d in (("placements", placements), ("routage", routage)):
                erreurs = formats.VALIDATEURS[fmt](d)
                if erreurs:
                    raise formats.ErreurFormat(erreurs)
            os.makedirs(dossier, exist_ok=True)
            cp = os.path.join(dossier, "placements_routage.json")
            cr = os.path.join(dossier, "routage_detaille.json")
            chemins.ecrire_json(cp, placements)
            chemins.ecrire_json(cr, routage)
            verdict = self.controles.drc(self.chemin_donnees, cp, cr, "strict",
                                         os.path.join(dossier, "drc_strict.json"), self.job)
            mesures = self.controles.mesures(self.donnees, placements, routage, verdict)
            erreurs = formats.valider_mesures(mesures)
            if erreurs:
                raise formats.ErreurFormat(erreurs)
            chemins.ecrire_json(os.path.join(dossier, "mesures.json"), mesures)
            self.reference = {"cout_commun": mesures.get("cout_commun"),
                              "liaisons_ouvertes": _nombre(verdict.get("liaisons_ouvertes")),
                              "violations": _nombre(verdict.get("violations"))}
            self.journal(f"routage existant : coût {_nb(self.reference['cout_commun'])}")
        except Exception as e:  # noqa: BLE001 - la comparaison manquante n'empêche pas le livrable
            self.avertir(f"comparaison au routage existant impossible ({_texte_erreur(e)})")

    # ------------------------------------------------------------------------------------------ rapport.txt
    def lignes_deduction(self) -> list[str]:
        d = self.deduction or {"elements": [], "suggestions": []}
        titres = (("couche:", "Couches et plans"), ("reseau:", "Réseaux (masses, puissance, alimentation)"),
                  ("convertisseur:", "Convertisseurs"), ("piece:", "Pièces fixées ou contraintes"),
                  ("groupe:", "Groupes"))
        lignes = []
        for prefixe, titre in titres:
            elems = [e for e in d["elements"] if str(e.get("objet", "")).startswith(prefixe)]
            if elems:
                lignes.append(f"  {titre} :")
                for e in elems:
                    lignes.append(f"    - {e['objet'][len(prefixe):]} : {e['propriete']} = {_nb(e['valeur'])} "
                                  f"({e['regle']} ; {e['preuve']} ; source {e['source']})")
        if not lignes:
            lignes.append("  aucune déduction")
        lignes.append("  Suggestions :")
        lignes += [f"    - {s['objet']} : {s['proposition']} ({s['raison']})" for s in d["suggestions"]] \
            or ["    aucune"]
        return lignes

    def ecrire_rapport_txt(self) -> None:
        """rapport.txt (§2.9) : UTF-8 avec BOM, CRLF ; rubriques dans l'ordre du contrat."""
        if not self.sortie:
            return
        L = [f"Placeur-routeur {self.version} — carte {os.path.basename(self.carte)}", ""]
        L.append("AVERTISSEMENTS")
        L += [f"  - {a}" for a in self.avertissements] or ["  aucun"]
        if self.avertissements:
            L.append("  (une règle non interprétée n'est pas couverte par le verdict)")
        L += ["", "DÉDUCTIONS"] + self.lignes_deduction()
        if not self.reseaux_puissance():
            L += ["", "ALERTE : aucune règle de largeur propre : aucun réseau traité en puissance"]
        e = self.estimation
        if self.sans_graine:
            L += ["", "PRÉVISIONS",
                  f"  mémoire prévue : {_nb(e['memoire_mo'])} Mo par essai ; mémoire libre : "
                  f"{_nb(e['memoire_disponible_mo'])} Mo",
                  f"  durée prévue d'une graine : {_nb(e['duree_graine_prevue_s'])} s ; durée maximale du calcul : "
                  f"{_nb(e['duree_max_s'])} s",
                  "", "Analyse seule : aucune graine lancée."]
            chemins.ecrire_texte_bloc_notes(os.path.join(self.sortie, "rapport.txt"), "\n".join(L) + "\n")
            return
        L += ["", "GRAINES"]
        for g in sorted(self.graines):
            x = self.graines[g]
            etat = "valide" if x["valide"] else ("plantage du moteur" if x["plantage"] else "non valide")
            ouvertes = (x.get("drc_strict") or {}).get("ouvertes")
            L.append(f"  graine {g} : {etat} ; coût commun {_nb(x['cout_commun'])} ; liaisons ouvertes "
                     f"{_nb(ouvertes)} ; durée {_nb(x['duree_s'])} s")
        if not self.graines:
            L.append("  aucune graine calculée")
        retenue = self.meilleure if self.meilleure is not None else "aucune"
        if self.meilleure is None and self.a_terminer is not None:
            retenue = f"aucune (graine à terminer : {self.a_terminer})"
        L += ["", f"GRAINE RETENUE : {retenue}",
              "  Le coût commun (cout_commun/2) additionne la longueur des pistes et des pénalités pour les vias, "
              "les rétrécissements, la résistance des réseaux de puissance et les coudes ; plus petit est meilleur, "
              "et il se compare entre graines et avec le routage existant."]
        meilleur = self.graines[self.meilleure]["cout_commun"] if self.meilleure is not None else None
        L += ["", "COMPARAISON"]
        if self.reference:
            L.append(f"  routage existant : coût {_nb(self.reference['cout_commun'])} ; meilleur résultat : "
                     f"{_nb(meilleur)}")
        else:
            L.append("  routage existant : aucun (carte d'entrée sans cuivre libre, ou comparaison impossible)")
        L += ["", "LIVRABLE"]
        if self.livrable:
            L.append(f"  {self.livrable['fichier']} ({NOMS_LIVRABLE[self.livrable['type']]})")
            if self.livrable.get("serigraphie"):
                L.append(f"  sérigraphie (serigraphie) : {self.livrable['serigraphie']}")
            L += [f"  anomalie : {a}" for a in self.livrable["anomalies"]]
        elif not self.reglages.valeur("pcbdoc", "produire"):
            L.append("  aucun : carte produite non demandée")
        else:
            L.append("  aucun" + (" : carte produite en échec" if self.cartes_echouees else ""))
        if self.liaisons_a_finir:
            L += ["", "LIAISONS À FINIR À LA MAIN"]
            for x in self.liaisons_a_finir:
                pos = x.get("position_mm") or [None, None]
                L.append(f"  - réseau {x.get('reseau', '?')} : pastilles {', '.join(x.get('noms') or [])} ; "
                         f"position ({_nb(pos[0])} ; {_nb(pos[1])}) mm")
        L += ["", f"À FAIRE DANS {LOGICIEL[self.genre].upper()}"]
        if self.genre == "pcbdoc":
            L += ["  - ajouter la carte produite au projet Altium", "  - Outils > Polygones > Recouler tout",
                  "  - lancer la DRC"]
        else:
            L += ["  - remplir les zones (touche B)", "  - lancer la DRC"]
        chemins.ecrire_texte_bloc_notes(os.path.join(self.sortie, "rapport.txt"), "\n".join(L) + "\n")


# ================================================================================================ --init

def initialiser(carte: str, outils: Outils, flux) -> int:
    """--init CARTE : <nom>.ini et <nom>_contraintes.json à côté de la carte, sans jamais écraser (§5.3)."""
    genre = genre_carte(carte)
    if not os.path.isfile(carte):
        raise ErreurEntree(f"carte introuvable : {os.path.basename(carte)}")
    carte = os.path.abspath(carte)
    ini = parametres.ini_projet(carte)
    cj = os.path.splitext(carte)[0] + "_contraintes.json"
    a_ecrire = [c for c in (ini, cj) if not os.path.exists(c)]
    for c in (ini, cj):
        if c not in a_ecrire:
            flux.write(f"{os.path.basename(c)} existe déjà : rien n'est écrasé\n")
    if not a_ecrire:
        return 0
    entree = _appel_api("import de la carte", "entree_carte", lambda: outils.importer(carte))
    deduction, suggerees = None, []
    try:
        config = outils.deduire(entree, None)
        suggerees = list(config.get("pieces_a_fixer_suggerees") or [])
        res = outils.construire(entree, {"format": "contraintes/2"}, config, None)
        deduction = res[1] if isinstance(res, tuple) and len(res) == 2 else None
    except Exception as e:  # noqa: BLE001 - le modèle s'écrit sans les déductions
        flux.write(f"avertissement : déduction impossible ({_texte_erreur(e)}) : modèle sans suggestions\n")
    verrouillees = [c["designateur"] for c in entree["composants"] if c.get("verrouille")]
    plans = [f"{a['objet']} : {a['proposition']}" for a in (deduction or {}).get("a_confirmer") or []
             if "plan" in f"{a.get('proposition', '')} {a.get('raison', '')}".lower()]
    fixes = list(dict.fromkeys(verrouillees + [s if isinstance(s, str) else str(s.get("piece", ""))
                                               for s in suggerees]))
    if ini in a_ecrire:
        parametres.enregistrer(ini, {"carte.fichier": carte, "carte.contraintes": cj, "contraintes.fixes": fixes})
        flux.write(f"{os.path.basename(ini)} écrit ([contraintes] fixes : {', '.join(fixes) or 'aucune'})\n")
    if cj in a_ecrire:
        modele = {"format": "contraintes/2"}
        aide = {"format": "format du fichier (ne pas changer)"}
        for k, (defaut, texte) in MODELE_CONTRAINTES.items():
            modele[k] = defaut
            aide[k] = f"{texte} ; défaut : {json.dumps(defaut, ensure_ascii=False)}"
        for k, texte in MODELE_DEPENDANTS.items():
            aide[k] = f"clé absente du modèle : défaut = {texte} ; l'ajouter pour l'imposer"
        if plans:
            aide["couches"] += " ; plans probables à confirmer : " + " ; ".join(plans)
        aide["_positions"] = "positions actuelles des pièces, repère du fichier, mm (information, sans effet)"
        modele["_aide"] = aide
        modele["_positions"] = positions_fichier(entree, genre)
        erreurs = formats.valider_contraintes(modele)
        if erreurs:
            raise ErreurInterne("modèle de contraintes refusé par formats : " + " ; ".join(erreurs[:3]))
        chemins.ecrire_json(cj, modele)
        flux.write(f"{os.path.basename(cj)} écrit (modèle, positions dans le repère du fichier)\n")
    return 0


def positions_fichier(entree: dict, genre: str) -> dict:
    """Positions actuelles des pièces dans le repère du fichier (§0.4), en mm : Altium, origine commune décalée de
    `source.origine_fichier_mil` ; KiCad, de `source.origine_fichier_mm`, y vers le bas (choix (c), décision 50)."""
    s = entree["source"]
    if genre == "pcbdoc":
        o = s.get("origine_fichier_mil") or [0.0, 0.0]
        ox, oy, sens = o[0] * formats.MIL, o[1] * formats.MIL, 1.0
    else:
        o = s.get("origine_fichier_mm") or [0.0, 0.0]
        ox, oy, sens = o[0], o[1], -1.0
    return {c["designateur"]: {"x": round(ox + c["x"], 4), "y": round(oy + sens * c["y"], 4),
                               "rotation": c["rotation"], "face": c["face"], "verrouille": bool(c.get("verrouille"))}
            for c in entree["composants"]}


# ================================================================================================ entrée



def _version_moteur(nom: str, version: str) -> tuple[bool, str]:
    """(présent et à la bonne version, texte) pour le moteur `nom`, sans jamais lancer cargo."""
    try:
        cmd = chemins.commande_moteur(nom, construire=False)
    except (chemins.MoteurAbsent, chemins.ErreurConstruction) as e:
        return False, str(e)
    try:
        v = chemins.executer(cmd + ["--version"], delai_s=60, priorite_basse=False)
    except (OSError, chemins.DelaiDepasse) as e:
        return False, f"{chemins.MESSAGE_MOTEUR_ABSENT} (lancement refusé : {_texte_erreur(e)})"
    texte = (v.stdout or "").strip()[:80]
    attendue = f"{nom} {version}"
    if v.returncode != 0 or texte != attendue:
        return False, f"version « {texte} » (code {code_lisible(v.returncode)}) différente de « {attendue} »"
    return True, texte


def auto_test(flux) -> int:
    """--auto-test (§5.5) : moteurs présents et à la version du lanceur, outils importables, dossier de résultats
    inscriptible. Code 0 si tout va bien, 2 sinon ; aucun cargo lancé."""
    version = chemins.version_lanceur()
    echecs = 0

    def ligne(ok, texte):
        nonlocal echecs
        echecs += 0 if ok else 1
        flux.write(chemins.anonymiser(("ok      " if ok else "ÉCHEC   ") + texte) + "\n")

    flux.write(f"placeur {version} : auto-test\n")
    for nom in chemins.MOTEURS:
        ok, texte = _version_moteur(nom, version)
        ligne(ok, f"moteur {nom} : {texte}")
    for module in MODULES_AUTO_TEST:
        try:
            importlib.import_module(module)
            ligne(True, f"outil {module} importable")
        except Exception as e:  # noqa: BLE001 - tout échec d'import est signalé
            ligne(False, f"outil {module} : import impossible ({type(e).__name__} : {_texte_erreur(e)})")
    try:
        import tkinter  # noqa: F401
        ligne(True, "fenêtre (tkinter) disponible")
    except ImportError as e:
        ligne(False, f"fenêtre (tkinter) indisponible ({_texte_erreur(e)})")
    dossier = chemins.dossier_connu("localappdata") or chemins.dossier_connu("profil")
    try:
        if not dossier:
            raise OSError("dossier de l'utilisateur inconnu")
        essai = os.path.join(dossier, f"PlaceurRouteur_essai_{os.getpid()}.tmp")
        with open(essai, "w", encoding="ascii") as f:
            f.write("essai\n")
        os.remove(essai)
        ligne(True, "dossier de l'utilisateur inscriptible")
    except OSError as e:
        ligne(False, f"dossier de l'utilisateur non inscriptible : {message_environnement(e) or _texte_erreur(e)}")
    flux.write(f"auto-test : {'réussi' if echecs == 0 else f'{echecs} échec(s)'}\n")
    return 0 if echecs == 0 else 2


def diagnostic(flux) -> int:
    """--diagnostic (§5.3, mode d'emploi section 8) : texte anonymisé à joindre à une demande d'aide."""
    version = chemins.version_lanceur()
    lignes = [f"placeur {version}",
              f"système : {platform.system()} {platform.release()} ({platform.version()}), {platform.machine()}",
              f"python : {platform.python_version()} ({'paquet figé' if chemins.est_fige() else 'mode source'})"]
    for nom in chemins.MOTEURS:
        ok, texte = _version_moteur(nom, version)
        lignes.append(f"moteur {nom} : {'présent' if ok else 'PROBLÈME'} : {texte}")
    memoire = chemins.memoire_disponible_mo()
    lignes.append(f"mémoire disponible : {memoire if memoire is not None else '?'} Mo")
    lignes.append(f"processeurs logiques : {os.cpu_count() or '?'}")
    batterie = chemins.sur_batterie()
    lignes.append("alimentation : " + {True: "batterie", False: "secteur", None: "inconnue"}[batterie])
    try:
        r = parametres.charger(None, {})
        lignes.append("fichiers .ini lus : " + (", ".join(os.path.basename(f) for f in r.fichiers_lus) or "aucun"))
    except parametres.ErreurParametres as e:
        lignes.append(f"fichiers .ini : erreur : {e.texte_complet()}")
    for nom in ("documents", "localappdata"):
        d = chemins.dossier_connu(nom)
        libre = espace_libre_go(d) if d else None
        lignes.append(f"espace libre ({nom}) : {_nombre_fr(libre) + ' Go' if libre is not None else '?'}"
                      + (" ; synchronisé OneDrive" if d and synchronise(d) else ""))
    for l in lignes:
        flux.write(chemins.anonymiser(l) + "\n")
    return 0


def main(argv: list[str] | None = None, *, controles: Controles | None = None, outils: Outils | None = None,
         flux=None, flux_erreur=None) -> int:
    """Point d'entrée (§5.3). `controles`, `outils`, `flux` et `flux_erreur` : contrôles indépendants, API des lots
    B et C et sorties (par défaut Controles(), Outils(), sys.stdout et sys.stderr)."""
    argv = sys.argv[1:] if argv is None else list(argv)
    flux = sys.stdout if flux is None else flux
    flux_erreur = sys.stderr if flux_erreur is None else flux_erreur
    calcul = None
    ancien = {}
    try:
        o = analyser_arguments(argv)
        if "--version" in o["drapeaux"]:
            flux.write(f"placeur {chemins.version_lanceur()}\n")
            return 0
        if "--auto-test" in o["drapeaux"]:
            return auto_test(flux)
        if "--diagnostic" in o["drapeaux"]:
            return diagnostic(flux)
        if "--init" in o["valeurs"]:
            return initialiser(o["valeurs"]["--init"], outils or Outils(), flux)
        if "--analyser" in o["drapeaux"] and not o["carte"]:
            raise ErreurEntree("--analyser s'emploie avec une CARTE")
        if "--analyser" in o["drapeaux"] and o["reprendre"] is not None:
            raise ErreurEntree("--analyser et --reprendre ne s'emploient pas ensemble")
        if isinstance(o["reprendre"], str) and not o["carte"] and "--donnees" not in o["valeurs"]:
            # « --reprendre DOSSIER » seul : la carte est la copie gardée par le calcul (technique/entree/)
            entree = os.path.join(o["reprendre"], "technique", "entree")
            cartes = sorted(f for f in (os.listdir(entree) if os.path.isdir(entree) else [])
                            if os.path.splitext(f)[1].lower() in (".pcbdoc", ".kicad_pcb"))
            if not cartes:
                donnees = os.path.join(o["reprendre"], "technique", "donnees_placeur.json")
                if not os.path.isfile(donnees):
                    raise ErreurEntree("--reprendre : dossier sans calcul à reprendre (carte d'entrée introuvable)")
                o["valeurs"]["--donnees"] = donnees
            else:
                o["carte"] = os.path.join(entree, cartes[0])
        reglages = parametres.charger(o["valeurs"].get("--parametres") and
                                      os.path.abspath(o["valeurs"]["--parametres"]), options_ini(o),
                                      carte=o["carte"] and os.path.abspath(o["carte"]))
        if o["carte"]:
            calcul = CalculCarte(o, reglages, o["carte"], outils or Outils(), controles or Controles(), flux,
                                 flux_erreur, analyser="--analyser" in o["drapeaux"])
            if threading.current_thread() is threading.main_thread():
                for nom in ("SIGINT", "SIGBREAK"):
                    if hasattr(signal, nom):
                        ancien[nom] = signal.signal(getattr(signal, nom), calcul._signal)
            return calcul.executer()
        chemin_donnees = os.path.abspath(o["valeurs"]["--donnees"])
        try:
            donnees = formats.charger(chemin_donnees, "donnees_placeur")
        except formats.ErreurFormat as e:
            raise ErreurEntree(str(e)) from None
        calcul = Calcul(o, reglages, chemin_donnees, donnees, controles or Controles(), flux, flux_erreur)
        if threading.current_thread() is threading.main_thread():
            for nom in ("SIGINT", "SIGBREAK"):
                if hasattr(signal, nom):
                    ancien[nom] = signal.signal(getattr(signal, nom), calcul._signal)
        return calcul.executer()
    except (ErreurEntree, parametres.ErreurParametres, chemins.VerrouOccupe) as e:
        texte = e.texte_complet() if isinstance(e, parametres.ErreurParametres) else str(e)
        texte = chemins.anonymiser(texte, calcul.sortie if calcul else None)
        flux_erreur.write(f"erreur : {texte}\n")
        if calcul is not None and calcul.job is not None:
            calcul.job.tout_arreter()
        if calcul is not None and calcul.technique:
            calcul.code, calcul.erreur = 2, texte
            try:
                calcul.journal(f"erreur : {texte}")
                calcul.ecrire_rapport()
                calcul.evt("fin", code=2, meilleure=None, livrable=None, texte=texte)
            except OSError:
                pass
        return 2
    except BaseException as e:  # noqa: BLE001 - erreur interne de l'orchestrateur : code 4 (§1.4)
        if isinstance(e, SystemExit):
            raise
        m = message_environnement(e, calcul.sortie if calcul else None)
        if m:                                     # table de §5.3 (disque plein, chemin trop long, refus d'accès)
            flux_erreur.write(f"erreur : {m}\n")
            if calcul is not None and calcul.job is not None:
                calcul.job.tout_arreter()
            if calcul is not None and calcul.technique:
                calcul.code, calcul.erreur = 2, m
                try:
                    calcul.journal(f"erreur : {m}")
                    calcul.ecrire_rapport()
                except OSError:
                    pass
            return 2
        trace =e.args[0] if isinstance(e, ErreurInterne) else traceback.format_exc()
        texte = chemins.anonymiser(f"placeur {chemins.version_lanceur()} : erreur interne\n{trace}",
                                   calcul.sortie if calcul else None)
        if calcul is not None and calcul.job is not None:
            calcul.job.tout_arreter()
        if calcul is not None and calcul.technique:
            try:
                chemins.ecrire_atomique(os.path.join(calcul.technique, "erreur.txt"), texte)
                calcul.code, calcul.erreur = 4, "erreur interne de l'orchestrateur (technique/erreur.txt)"
                calcul.ecrire_rapport()
                calcul.evt("fin", code=4, meilleure=None, livrable=None, texte=calcul.erreur)
            except OSError:
                pass
            flux_erreur.write("erreur interne de l'orchestrateur : voir technique/erreur.txt\n")
        else:
            flux_erreur.write(texte + "\n")
        return 4
    finally:
        for nom, h in ancien.items():
            signal.signal(getattr(signal, nom), h)
        if calcul is not None:
            calcul.fermer()


if __name__ == "__main__":
    for _flux in (sys.stdout, sys.stderr):           # UTF-8 même redirigé (§0.2-10)
        try:
            _flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
