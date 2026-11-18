# -*- coding: utf-8 -*-
"""Tests de l'orchestrateur et faux moteur (lot D ; contrat §5.1, §1.4, §2.6, §2.7, §5.3, §7.2 ; avenant 2.2).

    cd PCB_V2/outils/placeur_routage
    python -m unittest test_placeur -v

TestFauxMoteur essaie le faux moteur ; TestOrchestrateur essaie `placeur.py --donnees` (§5.3) dans le processus,
avec le faux moteur à la place de `chemins.commande_moteur` et des doublures des contrôles de §5.2 (clés de scénario
`placement_valide`, `drc_strict_valide`, `drc_altium_valide`, `violations`). `python test_placeur.py
--orchestrateur-faux JOURNAL -- <arguments de placeur.py>` lance un orchestrateur dans un processus à part (deux
orchestrateurs simultanés).

Faux moteur : `python test_placeur.py --faux-moteur [options du faux] <ligne de commande de §5.1>`. Il respecte
la ligne de commande du moteur (`campagne`, `router`, `verifier`, `parametres-defaut [--qualite]`, `estimer`,
`etalonner`, `--version`), ses codes de sortie (§1.4) et ses formats (`stats/2` écrit en dernier et de façon
atomique, `estimation/1`, `etalonnage/1`, `verdict/2` réduit), pour développer l'orchestrateur avant le jalon A1.

- `parametres-defaut` : l'annexe C est lue dans CONTRAT.md, corrigée par la partie D de l'avenant 2.2, puis la
  table des niveaux de §2.6 (corrigée par A.1) s'applique. Rien n'est recopié à la main.
- Scénario d'une graine : clé de commentaire `_faux_scenario` des données (§0.5 : les lecteurs réels l'ignorent),
  `{"defaut": {…}, "<graine>": {…}}`. Champs : `type` ∈ valide, invalide, lent, arret, plantage, erreur_interne,
  code2, cpu ; `duree_s`, `cpu_s`, `cout`, `liaisons_ouvertes`, `valide`, `memoire_estimee_mo`, `memoire_pic_mo`,
  `code` (plantage : 101 par défaut), `message` (code2), `modele` (dossier d'une graine modèle à recopier, relatif
  aux données), `cellules_detail`, `pieces_mobiles`, `connexions`.
- Options du faux, placées avant la sous-commande : `--faux-version V` (version annoncée), `--faux-nom N` (nom
  annoncé par --version, « placeur_routage » par défaut), `--faux-score S`
  (score d'étalonnage), `--faux-journal F` (lignes horodatées « debut|fin <graine> <pid> <t> » pour compter les
  moteurs vivants).
- Le faux relève sa priorité, son pid, ses heures de début et de fin et son temps processeur dans la clé
  `_faux` de stats.json.

Aucun chemin de la machine n'est écrit dans ce fichier : les chemins sont construits à l'exécution.
"""
from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import chemins  # noqa: E402
import parametres  # noqa: E402

WINDOWS = os.name == "nt"
CONTRAT = os.path.join(chemins.OUTILS, "CONTRAT.md")
AVENANT = os.path.join(chemins.OUTILS, "AVENANT_2.2.md")
FAUX_MOTEUR = [chemins.interpreteur(), os.path.abspath(__file__), "--faux-moteur"]

PHASES = ("chargement", "placement_initial", "recuit", "routage_detaille", "boucle", "conjointe", "finition",
          "verification", "ecriture")
FICHIERS_GRAINE = ("placements_routage.json", "routage_detaille.json", "routage_detaille.svg", "journal.txt")
CLES_STATS = ("format", "version_moteur", "graine", "fin", "valide", "cause", "piece", "raison", "validite",
              "cout_commun", "connexions_echouees_routeur", "echecs", "recuit", "boucle", "conjointe", "finition",
              "convertisseurs", "mesures", "empreintes", "parametres_effectifs", "execution")
CLES_EXECUTION = ("temps_phases_s", "temps_total_s", "fils", "plafond_temps_atteint", "conjointe_plafond_atteint",
                  "memoire_estimee_mo", "memoire_pic_mo")
CLES_ESTIMATION = ("format", "memoire_mo", "cellules_detail", "pas_grille_mm", "couches_routage", "connexions",
                   "pieces_mobiles", "essais_conjointe")


# ================================================================================================ contrat et avenant

def _lire(chemin: str) -> str:
    with open(chemin, encoding="utf-8") as f:
        return f.read()


def _bloc_json(texte: str, titre: str) -> str:
    """Premier bloc ```json qui suit le titre `titre`."""
    i = texte.index(titre)
    debut = texte.index("```json", i) + len("```json")
    return texte[debut:texte.index("```", debut)]


def annexe_c() -> dict:
    """Annexe C de CONTRAT.md (parametres_moteur/1, niveau normal, version 2.1)."""
    return json.loads(_bloc_json(_lire(CONTRAT), "## Annexe C"))


def correction_avenant() -> dict:
    """Partie D de l'avenant 2.2 : lignes de l'annexe C remplacées ({"conjointe": {…}})."""
    bloc = _bloc_json(_lire(AVENANT), "## Partie D").strip().rstrip(",")
    return json.loads("{" + bloc + "}")


def annexe_c_corrigee() -> dict:
    """Annexe C corrigée par la partie D de l'avenant : `parametres-defaut` du niveau normal."""
    res = annexe_c()
    for cle, valeur in correction_avenant().items():
        if cle not in res:
            raise ValueError(f"partie D de l'avenant : clé {cle} absente de l'annexe C")
        res[cle] = valeur
    return res


_EXPOSANTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _nombre_contrat(texte: str) -> float:
    """« 2·10⁷ » → 2e7 ; « 0,48 » → 0.48."""
    s = texte.strip()
    m = re.fullmatch(r"(\d+(?:,\d+)?)·10([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", s)
    if m:
        return float(m.group(1).replace(",", ".")) * 10 ** int(m.group(2).translate(_EXPOSANTS))
    return float(s.replace(",", "."))


def table_niveaux() -> dict:
    """Clés du moteur de la table des niveaux de §2.6, corrigée par A.1 de l'avenant : {clé: {niveau: valeur}},
    valeurs au type de l'annexe C."""
    texte = _lire(CONTRAT)
    i = texte.index("**Niveaux de qualité**")
    lignes = texte[i:].split("\n\n", 2)[1].splitlines()
    entete = [c.strip().strip("`") for c in lignes[0].strip("|").split("|")]
    niveaux = entete[1:]
    if tuple(niveaux) != parametres.QUALITES:
        raise ValueError(f"table des niveaux : colonnes inattendues {niveaux}")
    ref = annexe_c()
    table = {}
    for ligne in lignes[2:]:
        cellules = [c.strip() for c in ligne.strip().strip("|").split("|")]
        if not cellules[0].startswith("`"):
            continue                                       # lignes propres à l'orchestrateur
        cle = cellules[0].strip("`")
        section, nom = cle.split(".")
        type_ref = type(ref[section][nom])
        table[cle] = {}
        for niveau, cellule in zip(niveaux, cellules[1:]):
            v = _nombre_contrat(cellule)
            table[cle][niveau] = int(v) if type_ref is int else float(v)
    m = re.search(r"Dans la table des niveaux, `([\w.]+)` de `(\w+)` passe de ([\d,]+) à \*\*([\d,]+)\*\*",
                  _lire(AVENANT))
    if not m:
        raise ValueError("avenant 2.2, A.1 : correction de la table des niveaux introuvable")
    cle, niveau, avant, apres = m.groups()
    if table[cle][niveau] != _nombre_contrat(avant):
        raise ValueError(f"avenant 2.2, A.1 : {cle} de {niveau} ne vaut pas {avant} dans la table")
    table[cle][niveau] = type(table[cle][niveau])(_nombre_contrat(apres))
    return table


def parametres_niveau(qualite: str) -> dict:
    """Paramètres du moteur attendus pour un niveau : annexe C corrigée, puis la ligne du niveau de la table."""
    res = annexe_c_corrigee()
    for cle, valeurs in table_niveaux().items():
        section, nom = cle.split(".")
        res[section][nom] = valeurs[qualite]
    return res


# ================================================================================================ faux moteur

OPTIONS = {
    "campagne": {"--donnees": 1, "--sortie": 1, "--graine": 1, "--parametres": 1, "--threads": 1,
                 "--evenements": 0, "--arret": 1},
    "router": {"--donnees": 1, "--placement": 1, "--sortie": 1, "--parametres": 1, "--threads": 1,
               "--evenements": 0, "--arret": 1},
    "verifier": {"--donnees": 1, "--placement": 1, "--routage": 1, "--couture": 1, "--mode": 1,
                 "--sans-grille": 0, "--sortie": 1},
    "parametres-defaut": {"--qualite": 1},
    "estimer": {"--donnees": 1, "--parametres": 1},
    "etalonner": {},
}
OBLIGATOIRES = {"campagne": ("--donnees", "--sortie", "--graine"), "router": ("--donnees", "--placement", "--sortie"),
                "verifier": ("--donnees", "--placement", "--routage", "--sortie"), "estimer": ("--donnees",)}
SUPPRIMEES = ("--bloc-fige", "--bloc-libre", "--cuivre-bloc", "--pastilles", "--gpu", "--conjoint",
              "--routage-depart", "--depart", "--sortie-conjoint", "--chaines-conjoint", "--mouvements", "--boucle",
              "--chaines", "--periode", "--pas", "--iters-det", "--tours-rr", "--mouv-local", "--placement-v3")


class EntreeInvalide(Exception):
    """Entrée invalide : message sur la sortie d'erreur, code 2 (§1.4)."""


def _analyser(argv: list[str]) -> tuple[dict, str | None, dict]:
    faux = {}
    while argv and argv[0] in ("--faux-version", "--faux-score", "--faux-journal", "--faux-nom"):
        if len(argv) < 2:
            raise EntreeInvalide(f"option {argv[0]} : valeur manquante")
        faux[argv[0]] = argv[1]
        argv = argv[2:]
    if not argv:
        raise EntreeInvalide("sous-commande manquante (campagne, router, verifier, parametres-defaut, estimer, "
                             "etalonner, --version)")
    if argv[0] == "--version":
        return faux, "--version", {}
    commande = argv[0]
    if commande not in OPTIONS:
        raise EntreeInvalide(f"sous-commande inconnue : {commande}")
    permises, opts, i = OPTIONS[commande], {}, 1
    while i < len(argv):
        a = argv[i]
        if a in SUPPRIMEES:
            raise EntreeInvalide(f"option supprimée : {a} (les réglages sont dans parametres_moteur.json)")
        if a not in permises:
            raise EntreeInvalide(f"option inconnue pour {commande} : {a}")
        if permises[a]:
            if i + 1 >= len(argv):
                raise EntreeInvalide(f"option {a} : valeur manquante")
            opts[a] = argv[i + 1]
            i += 2
        else:
            opts[a] = True
            i += 1
    for o in OBLIGATOIRES.get(commande, ()):
        if o not in opts:
            raise EntreeInvalide(f"option obligatoire manquante : {o}")
    return faux, commande, opts


def _lire_json(chemin: str, quoi: str) -> dict:
    try:
        with open(chemin, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except FileNotFoundError:
        raise EntreeInvalide(f"{quoi} : fichier introuvable ({os.path.basename(chemin)})") from None
    except (ValueError, UnicodeDecodeError):
        raise EntreeInvalide(f"{quoi} : JSON illisible ({os.path.basename(chemin)})") from None


def _scenario(donnees: dict, graine) -> dict:
    sc = donnees.get("_faux_scenario") or {}
    res = dict(sc.get("defaut", {}))
    if graine is not None:
        res.update(sc.get(str(graine), {}))
    res.setdefault("type", "valide")
    return res


def _parametres(opts: dict) -> dict:
    base = annexe_c_corrigee()
    if "--parametres" not in opts:
        return base
    try:
        return parametres.fusionner_moteur(base, _lire_json(opts["--parametres"], "parametres"))
    except parametres.ErreurParametres as e:
        raise EntreeInvalide(e.message) from None


def _evt(actif: bool, **champs) -> None:
    if actif:
        sys.stdout.write("EVT " + json.dumps(champs, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _journal_faux(faux: dict, quoi: str, graine) -> None:
    chemin = faux.get("--faux-journal")
    if not chemin:
        return
    ligne = f"{quoi} {graine} {os.getpid()} {time.time():.6f}\n".encode("ascii")
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, ligne)
    finally:
        os.close(fd)


def _quitter_brutalement(code: int) -> None:
    """Fin sans nettoyage, comme une panique non attrapée (101) ou un abandon (0xC0000409)."""
    sys.stdout.flush()
    sys.stderr.flush()
    if 0 <= code <= 255:
        os._exit(code)
    if WINDOWS:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.TerminateProcess(ctypes.c_void_p(-1), code & 0xFFFFFFFF)   # pseudo-poignée du processus courant
    os.abort()


def _sha256_fichier(chemin: str) -> str:
    with open(chemin, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _empreinte_parametres(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _travailler(sc: dict, opts: dict, evenements: bool, plafond_s: float, t0: float) -> str:
    """Simule les phases : durée `duree_s` (sommeil) et `cpu_s` de temps processeur (boucle de calcul) ; sonde le
    fichier d'arrêt toutes les 20 ms. Rend « normale », « arret_demande » ou « plafond_temps »."""
    duree, cpu = float(sc.get("duree_s", 0.05)), float(sc.get("cpu_s", 0.0))
    arret = opts.get("--arret")
    c0 = time.process_time()
    actives = PHASES[:7]
    faite = -1
    x = 0
    while True:
        ecoule = time.monotonic() - t0
        fraction = min(1.0, ecoule / duree) if duree > 0 else 1.0
        phase = min(int(fraction * len(actives)), len(actives) - 1)
        while faite < phase:
            if faite >= 0:
                _evt(evenements, type="phase", phase=actives[faite], etat="fin", t=round(ecoule, 3))
            faite += 1
            _evt(evenements, type="phase", phase=actives[faite], etat="debut", t=round(ecoule, 3))
        if arret and os.path.exists(arret):
            return "arret_demande"
        if ecoule >= plafond_s:
            return "plafond_temps"
        if ecoule >= duree and time.process_time() - c0 >= cpu:
            _evt(evenements, type="phase", phase=actives[faite], etat="fin", t=round(ecoule, 3))
            return "normale"
        if time.process_time() - c0 < cpu:
            fin = time.perf_counter() + 0.02
            while time.perf_counter() < fin:
                x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        else:
            time.sleep(0.02)


def _ecrire_sorties(sortie: str, sc: dict, donnees_chemin: str, placement: str | None, graine=None) -> None:
    """Sorties de la graine ; les JSON produits portent la clé de commentaire `_faux_graine` (lue par les doublures
    des contrôles)."""
    modele = sc.get("modele")
    dossier_modele = os.path.join(os.path.dirname(os.path.abspath(donnees_chemin)), modele) if modele else None
    for nom in FICHIERS_GRAINE:
        cible = os.path.join(sortie, nom)
        source = os.path.join(dossier_modele, nom) if dossier_modele else None
        if nom == "placements_routage.json" and placement:
            source = placement
        if source and os.path.isfile(source):
            with open(source, "rb") as f:
                chemins.ecrire_atomique(cible, f.read())
        elif nom.endswith(".json"):
            # Exemples valides du lot E (corpus/formats/exemples, lus seulement).
            exemple = "placements.json" if nom.startswith("placements") else "routage.json"
            with open(os.path.join(ICI, "corpus", "formats", "exemples", exemple), encoding="utf-8") as f:
                contenu = json.load(f)
            contenu["_faux_graine"] = graine
            chemins.ecrire_json(cible, contenu)
        elif nom.endswith(".svg"):
            chemins.ecrire_atomique(cible, '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>\n')
        else:
            chemins.ecrire_atomique(cible, "faux moteur : journal\n")


def exemple_lot_e(nom: str) -> dict:
    """Exemple valide d'un format, écrit par le lot E (corpus/formats/exemples, lu seulement)."""
    with open(os.path.join(ICI, "corpus", "formats", "exemples", nom), encoding="utf-8") as f:
        return json.load(f)


def verdict_faux(mode: str, valide: bool, ouvertes: int, violations: int = 0, outil: str = "faux moteur") -> dict:
    """Verdict verdict/2 complet : l'exemple du lot E dont on règle le mode, la validité, les liaisons ouvertes
    et les violations (comptes et détails cohérents)."""
    v = exemple_lot_e("verdict.json")
    lo = (v["detail_liaisons_ouvertes"] or [{}])[0]
    vi = (v["detail_violations"] or [{}])[0]
    v.update({"outil": outil, "mode": mode, "valide": valide, "liaisons_ouvertes": ouvertes,
              "detail_liaisons_ouvertes": [lo] * ouvertes, "violations": violations,
              "detail_violations": [vi] * violations, "detail_violations_tronque": False,
              "reseaux_complets": v["reseaux_a_router"] - min(ouvertes, v["reseaux_a_router"])})
    v["violations_par_type"] = {k: 0 for k in v["violations_par_type"]}
    if violations and vi:
        v["violations_par_type"][vi.get("genre", next(iter(v["violations_par_type"])))] = violations
    v["exemples"] = {k: ([] if isinstance(x, list) else x) for k, x in v["exemples"].items()} if not violations \
        else v["exemples"]
    if isinstance(v.get("placement"), dict):
        v["placement"]["valide"] = True
        for k in ("violations",):
            if isinstance(v["placement"].get(k), list):
                v["placement"][k] = []
        if isinstance(v["placement"].get("violations_par_type"), dict):
            v["placement"]["violations_par_type"] = {k: 0 for k in v["placement"]["violations_par_type"]}
    return v


def _stats(graine, sc: dict, fin: str, valide: bool, params: dict, donnees_chemin: str, version: str,
           temps_total: float, threads: int, plafond: bool, t_debut: float) -> dict:
    ouvertes = int(sc.get("liaisons_ouvertes", 0 if valide else 2))
    cause = "" if valide else ("non verifie" if fin == "erreur_interne" else "liaisons ouvertes")
    effectifs = json.loads(json.dumps(params))
    effectifs["campagne"].pop("threads", None)
    part = temps_total / len(PHASES)
    return {
        "format": "stats/2", "version_moteur": version, "graine": graine,
        "fin": fin, "valide": valide, "cause": cause, "piece": None, "raison": None,
        "validite": verdict_faux("strict", valide, ouvertes),
        "cout_commun": None if fin == "erreur_interne" else float(sc.get("cout", 10000.0 + float(graine))),
        "connexions_echouees_routeur": ouvertes, "echecs": [],
        "recuit": {"cout_depart": 0, "cout": 0, "t0": 0, "acceptation_initiale": 0, "paliers": 1, "rechauffes": 0,
                   "mouvements": 1, "hpwl_depart": 1, "hpwl": 1,
                   "exploration": {"paliers": 1, "mouvements": 0, "hpwl": 1.0, "cout": 0}},
        "routage_initial": {"echecs_routeur": ouvertes, "ko_exact": ouvertes},
        "diagnostic": {"recherches": 0, "expansions": 0, "echecs_recherche": {
            "fenetre_epuisee": 0, "plafond_expansions": 0, "aucun_site_via_plan": 0, "autre": 0}},
        "boucle": {"tours": 0, "detail": []},
        "conjointe": {"essais": 0, "acceptes": 0, "illegaux": 0, "refuses": 0, "cout_depart": 0, "cout": 0},
        "finition": {"antennes_retirees": 0, "fusions": 0, "descentes_reposees": 0, "ko_avant": ouvertes,
                     "ko_apres": ouvertes, "reparations_verification": 0, "violations_reparees": 0},
        "convertisseurs": [],
        "mesures": {"longueur_mm": 0, "vias": 0, "vias_doubles": 0, "segments": 0, "longueur_retrecie_mm": 0,
                    "resistance_puissance_mohm": 0},
        "empreintes": {"donnees_sha256": _sha256_fichier(donnees_chemin),
                       "parametres_sha256": _empreinte_parametres(effectifs)},
        "parametres_effectifs": effectifs,
        "execution": {"temps_phases_s": {p: round(part, 3) for p in PHASES}, "temps_total_s": round(temps_total, 3),
                      "fils": threads, "plafond_temps_atteint": plafond, "conjointe_plafond_atteint": False,
                      "memoire_estimee_mo": int(sc.get("memoire_estimee_mo", 50)),
                      "memoire_pic_mo": int(sc.get("memoire_pic_mo", 40))},
        "_faux": {"pid": os.getpid(), "priorite": chemins.priorite_processus(), "debut": t_debut,
                  "fin": time.time(), "cpu_s": round(time.process_time(), 3), "scenario": sc.get("type")},
    }


def _campagne(faux: dict, commande: str, opts: dict) -> int:
    t_debut, t0 = time.time(), time.monotonic()
    donnees_chemin = opts["--donnees"]
    donnees = _lire_json(donnees_chemin, "donnees")
    graine = opts.get("--graine", "0")
    if not re.fullmatch(r"\d+", str(graine)):
        raise EntreeInvalide(f"--graine : '{graine}' n'est pas un entier positif")
    graine = int(graine)
    placement = opts.get("--placement")
    if placement and not os.path.isfile(placement):
        raise EntreeInvalide(f"placement : fichier introuvable ({os.path.basename(placement)})")
    params = _parametres(opts)
    threads = int(opts.get("--threads", params["campagne"]["threads"]) or 1)
    sc = _scenario(donnees, graine)
    memoire_max = params["campagne"]["memoire_max_mo"]
    if memoire_max and int(sc.get("memoire_estimee_mo", 50)) > memoire_max:
        raise EntreeInvalide(f"mémoire estimée {int(sc.get('memoire_estimee_mo', 50))} Mo au-delà de "
                             f"campagne.memoire_max_mo ({memoire_max} Mo)")
    if sc["type"] == "code2":
        raise EntreeInvalide(sc.get("message", "donnees : entrée invalide (scénario code2 du faux moteur)"))
    sortie = opts["--sortie"]
    os.makedirs(sortie, exist_ok=True)
    for nom in FICHIERS_GRAINE + ("stats.json",):                 # §5.1 : fichiers à écrire supprimés d'abord
        for chemin in (os.path.join(sortie, nom), os.path.join(sortie, nom + ".tmp")):
            if os.path.exists(chemin):
                os.remove(chemin)
    evenements = bool(opts.get("--evenements"))
    _journal_faux(faux, "debut", graine)
    version = faux.get("--faux-version") or chemins.version_lanceur()
    fin = _travailler(sc, opts, evenements, float(params["campagne"]["duree_max_s"]), t0)
    if sc["type"] == "arret":
        fin = "arret_demande"
    if sc["type"] == "plantage":
        _ecrire_sorties(sortie, {**sc, "modele": None}, donnees_chemin, None, graine)
        os.remove(os.path.join(sortie, "routage_detaille.json"))   # sorties partielles, pas de stats.json
        _journal_faux(faux, "fin", graine)
        sys.stderr.write("thread 'main' panicked (faux moteur)\n")
        _quitter_brutalement(int(sc.get("code", 101)))
    if sc["type"] == "erreur_interne":
        fin = "erreur_interne"
    valide = bool(sc.get("valide", sc["type"] not in ("invalide", "erreur_interne")))
    if fin != "erreur_interne":
        _ecrire_sorties(sortie, sc, donnees_chemin, placement, graine)
    stats =_stats(graine, sc, fin, valide, params, donnees_chemin, version, time.monotonic() - t0, threads,
                   fin == "plafond_temps", t_debut)
    chemins.ecrire_json(os.path.join(sortie, "stats.json"), stats)   # en dernier, atomique (§2.7)
    _journal_faux(faux, "fin", graine)
    _evt(evenements, type="fin", valide=valide, fin=fin, cause=stats["cause"])
    return {"arret_demande": 3, "erreur_interne": 4}.get(fin, 0)


def _verifier(opts: dict) -> int:
    donnees = _lire_json(opts["--donnees"], "donnees")
    for o in ("--placement", "--routage", "--couture"):
        if o in opts and not os.path.isfile(opts[o]):
            raise EntreeInvalide(f"{o[2:]} : fichier introuvable ({os.path.basename(opts[o])})")
    mode = opts.get("--mode", "strict")
    if mode not in ("strict", "altium"):
        raise EntreeInvalide(f"--mode : '{mode}' (strict ou altium)")
    sc = _scenario(donnees, None)
    valide = bool(sc.get("verdict_valide", True))
    chemins.ecrire_json(opts["--sortie"], verdict_faux(mode, valide, 0))
    return 0


def _estimer(opts: dict) -> int:
    donnees = _lire_json(opts["--donnees"], "donnees")
    params = _parametres(opts)
    sc = _scenario(donnees, None)
    pieces = int(sc.get("pieces_mobiles", 10))
    conj = params["conjointe"]
    essais = conj["essais"] if conj["essais"] is not None else math.floor(conj["essais_par_piece_mobile"] * pieces + 0.5)
    print(json.dumps({"format": "estimation/1", "memoire_mo": int(sc.get("memoire_estimee_mo", 50)),
                      "cellules_detail": int(sc.get("cellules_detail", 10000)), "pas_grille_mm": 0.1,
                      "couches_routage": 2, "connexions": int(sc.get("connexions", 20)), "pieces_mobiles": pieces,
                      "essais_conjointe": essais}, ensure_ascii=False))
    return 0


def faux_moteur(argv: list[str]) -> int:
    try:
        faux, commande, opts = _analyser(list(argv))
        if commande == "--version":
            print(f"{faux.get('--faux-nom', 'placeur_routage')} {faux.get('--faux-version') or chemins.version_lanceur()}")
            return 0
        if commande == "parametres-defaut":
            qualite = opts.get("--qualite", "normal")
            if qualite not in parametres.QUALITES:
                raise EntreeInvalide(f"--qualite : '{qualite}' ({', '.join(parametres.QUALITES)})")
            print(json.dumps(parametres_niveau(qualite), ensure_ascii=False, indent=1))
            return 0
        if commande == "etalonner":
            time.sleep(0.05)
            print(json.dumps({"format": "etalonnage/1", "score": float(faux.get("--faux-score", 1000.0))}))
            return 0
        if commande == "estimer":
            return _estimer(opts)
        if commande == "verifier":
            return _verifier(opts)
        return _campagne(faux, commande, opts)
    except EntreeInvalide as e:
        sys.stderr.write(f"erreur : {e}\n")
        return 2


# ================================================================================================ outils d'essai

def lire_json(chemin):
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def ecrire_donnees(dossier: str, scenario: dict, nom: str = "donnees_placeur.json") -> str:
    """Données minimales portant un scénario du faux moteur."""
    chemin = os.path.join(dossier, nom)
    chemins.ecrire_json(chemin, {"format": "donnees_placeur/2", "_faux_scenario": scenario})
    return chemin


def lancer_faux(*args, delai_s: float = 60, prefixe=(), **kw):
    return chemins.executer(FAUX_MOTEUR + list(prefixe) + [str(a) for a in args], delai_s=delai_s, **kw)


# ================================================================================================ tests

class TestFauxMoteur(unittest.TestCase):
    """Le faux moteur respecte la ligne de commande (§5.1), les codes (§1.4) et les formats (§2.7) du moteur."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_faux_moteur_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def campagne(self, scenario: dict, graine: int = 7, *extra, **kw):
        donnees = ecrire_donnees(self.tmp, scenario)
        sortie = os.path.join(self.tmp, f"graine_{graine}")
        r = lancer_faux("campagne", "--donnees", donnees, "--sortie", sortie, "--graine", graine, *extra, **kw)
        return r, sortie

    def test_version_egale_au_lanceur(self):
        r = lancer_faux("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "placeur_routage " + chemins.version_lanceur())
        self.assertRegex(r.stdout.strip(), r"^placeur_routage 2\.0\.0\+([0-9a-f]{7}|inconnu)$")
        r = lancer_faux("--version", prefixe=["--faux-version", "1.9.9+abcdef0"])
        self.assertEqual(r.stdout.strip(), "placeur_routage 1.9.9+abcdef0")

    def test_campagne_valide_stats_en_dernier_atomique(self):
        modele = os.path.join(self.tmp, "modele")
        os.makedirs(modele)
        chemins.ecrire_json(os.path.join(modele, "placements_routage.json"),
                            {"format": "placements/2", "unite": "mil", "pieces": [{"designator": "R1"}]})
        sortie = os.path.join(self.tmp, "graine_7")
        os.makedirs(sortie)
        chemins.ecrire_json(os.path.join(sortie, "stats.json"), {"format": "stats/2", "piege": True})
        with open(os.path.join(sortie, "routage_detaille.json.tmp"), "w") as f:
            f.write("reste")
        r, sortie = self.campagne({"defaut": {"type": "valide", "modele": "modele", "cout": 9876.5}})
        self.assertEqual(r.returncode, 0, r.stderr)
        stats = lire_json(os.path.join(sortie, "stats.json"))
        self.assertNotIn("piege", stats)
        for cle in CLES_STATS:
            self.assertIn(cle, stats)
        for cle in CLES_EXECUTION:
            self.assertIn(cle, stats["execution"])
        self.assertEqual(set(stats["execution"]["temps_phases_s"]), set(PHASES))
        self.assertEqual((stats["format"], stats["graine"], stats["fin"], stats["valide"], stats["cause"]),
                         ("stats/2", 7, "normale", True, ""))
        self.assertEqual(stats["cout_commun"], 9876.5)
        self.assertEqual(stats["version_moteur"], chemins.version_lanceur())
        self.assertNotIn("threads", stats["parametres_effectifs"]["campagne"])
        self.assertEqual(lire_json(os.path.join(sortie, "placements_routage.json"))["pieces"],
                         [{"designator": "R1"}])
        dates = {n: os.stat(os.path.join(sortie, n)).st_mtime_ns for n in os.listdir(sortie)}
        self.assertEqual(sorted(dates), sorted(FICHIERS_GRAINE + ("stats.json",)))   # aucun .tmp restant
        self.assertEqual(max(dates.values()), dates["stats.json"])

    def test_stats_absent_pendant_le_calcul(self):
        donnees = ecrire_donnees(self.tmp, {"defaut": {"type": "lent", "duree_s": 30}})
        sortie = os.path.join(self.tmp, "graine_3")
        os.makedirs(sortie)
        chemins.ecrire_json(os.path.join(sortie, "stats.json"), {"format": "stats/2", "piege": True})
        arret = os.path.join(self.tmp, "arret_demande")
        p = chemins.lancer(FAUX_MOTEUR + ["campagne", "--donnees", donnees, "--sortie", sortie, "--graine", "3",
                                          "--arret", arret], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(attendre(lambda: not os.path.exists(os.path.join(sortie, "stats.json")), 15))
            time.sleep(0.3)
            self.assertFalse(os.path.exists(os.path.join(sortie, "stats.json")))
            self.assertIsNone(p.poll())
            t = time.monotonic()
            with open(arret, "w"):
                pass
            sortie_std, erreur = p.communicate(timeout=15)
            self.assertLess(time.monotonic() - t, 3.0)
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate()
        self.assertEqual(p.returncode, 3, erreur)
        stats = lire_json(os.path.join(sortie, "stats.json"))
        self.assertEqual((stats["fin"], stats["valide"], stats["cause"]), ("arret_demande", True, ""))

    def test_scenario_arret(self):
        r, sortie = self.campagne({"defaut": {"type": "arret"}})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(lire_json(os.path.join(sortie, "stats.json"))["fin"], "arret_demande")

    def test_plantage_code_101_sans_stats(self):
        r, sortie = self.campagne({"defaut": {"type": "plantage"}})
        self.assertEqual(r.returncode, 101)
        self.assertFalse(os.path.exists(os.path.join(sortie, "stats.json")))
        self.assertTrue(os.path.exists(os.path.join(sortie, "placements_routage.json")))

    @unittest.skipUnless(WINDOWS, "code d'abandon de Windows")
    def test_plantage_abandon_windows(self):
        r, sortie = self.campagne({"defaut": {"type": "plantage", "code": 0xC0000409}})
        self.assertEqual(r.returncode & 0xFFFFFFFF, 0xC0000409)
        self.assertFalse(os.path.exists(os.path.join(sortie, "stats.json")))

    def test_erreur_interne_code_4(self):
        r, sortie = self.campagne({"defaut": {"type": "erreur_interne"}})
        self.assertEqual(r.returncode, 4, r.stderr)
        stats = lire_json(os.path.join(sortie, "stats.json"))
        self.assertEqual((stats["fin"], stats["valide"], stats["cout_commun"]), ("erreur_interne", False, None))

    def test_code_2_message_sur_la_sortie_d_erreur(self):
        r, sortie = self.campagne({"defaut": {"type": "code2", "message": "contraintes.fixes[3] : pièce C99 inconnue"}})
        self.assertEqual(r.returncode, 2)
        self.assertIn("contraintes.fixes[3] : pièce C99 inconnue", r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(os.path.exists(os.path.join(sortie, "stats.json")))
        r, _ = self.campagne({}, 7, "--bloc-fige")
        self.assertEqual(r.returncode, 2)
        self.assertIn("option supprimée : --bloc-fige", r.stderr)
        p = os.path.join(self.tmp, "p.json")
        chemins.ecrire_json(p, {"routage": {"espacment": {"actif": False}}})
        r, _ = self.campagne({}, 7, "--parametres", p)
        self.assertEqual(r.returncode, 2)
        self.assertIn("routage.espacment : clé inconnue ; vouliez-vous dire espacement ?", r.stderr)
        r = lancer_faux("campagne", "--sortie", self.tmp, "--graine", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--donnees", r.stderr)

    def test_memoire_estimee_au_dela_du_plafond(self):
        p = os.path.join(self.tmp, "p.json")
        chemins.ecrire_json(p, {"campagne": {"memoire_max_mo": 100}})
        r, _ = self.campagne({"defaut": {"memoire_estimee_mo": 500}}, 7, "--parametres", p)
        self.assertEqual(r.returncode, 2)
        self.assertIn("campagne.memoire_max_mo", r.stderr)
        r, sortie = self.campagne({"defaut": {"memoire_estimee_mo": 80, "memoire_pic_mo": 95}}, 7, "--parametres", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(lire_json(os.path.join(sortie, "stats.json"))["execution"]["memoire_pic_mo"], 95)

    def test_plafond_temps(self):
        p = os.path.join(self.tmp, "p.json")
        chemins.ecrire_json(p, {"campagne": {"duree_max_s": 0.3}})
        r, sortie = self.campagne({"defaut": {"type": "lent", "duree_s": 30}}, 7, "--parametres", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        stats = lire_json(os.path.join(sortie, "stats.json"))
        self.assertEqual(stats["fin"], "plafond_temps")
        self.assertTrue(stats["execution"]["plafond_temps_atteint"])

    def test_estimer_et_etalonner(self):
        donnees = ecrire_donnees(self.tmp, {"defaut": {"memoire_estimee_mo": 321, "pieces_mobiles": 50}})
        r = lancer_faux("estimer", "--donnees", donnees)
        self.assertEqual(r.returncode, 0, r.stderr)
        est = json.loads(r.stdout)
        self.assertEqual(tuple(est), CLES_ESTIMATION)
        self.assertEqual((est["format"], est["memoire_mo"], est["essais_conjointe"]), ("estimation/1", 321, 300))
        r = lancer_faux("etalonner")
        self.assertEqual(r.returncode, 0, r.stderr)
        et = json.loads(r.stdout)
        self.assertEqual(et["format"], "etalonnage/1")
        self.assertIsInstance(et["score"], float)
        r = lancer_faux("etalonner", prefixe=["--faux-score", "250"])
        self.assertEqual(json.loads(r.stdout)["score"], 250.0)

    def test_evenements_et_priorite(self):
        r, sortie = self.campagne({"defaut": {"duree_s": 0.3}}, 5, "--evenements", priorite_basse=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        evts = [json.loads(l[4:]) for l in r.stdout.splitlines() if l.startswith("EVT ")]
        self.assertEqual(len(evts), len(r.stdout.splitlines()))
        self.assertEqual(evts[-1], {"type": "fin", "valide": True, "fin": "normale", "cause": ""})
        phases = [e["phase"] for e in evts if e["type"] == "phase" and e["etat"] == "debut"]
        self.assertEqual(phases, list(PHASES[:7]))
        faux = lire_json(os.path.join(sortie, "stats.json"))["_faux"]
        if WINDOWS:
            self.assertEqual(faux["priorite"], chemins.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            self.assertGreaterEqual(faux["priorite"], chemins.NICE_PRIORITE_BASSE)

    def test_consommation_processeur_et_journal(self):
        journal = os.path.join(self.tmp, "vivants.txt")
        r, sortie = self.campagne({"defaut": {"type": "cpu", "duree_s": 0.1, "cpu_s": 0.5}}, 9,
                                  prefixe=["--faux-journal", journal])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreaterEqual(lire_json(os.path.join(sortie, "stats.json"))["_faux"]["cpu_s"], 0.5)
        with open(journal, encoding="ascii") as f:
            lignes = [l.split() for l in f.read().splitlines()]
        self.assertEqual([l[:2] for l in lignes], [["debut", "9"], ["fin", "9"]])
        self.assertLessEqual(float(lignes[0][3]), float(lignes[1][3]))

    def test_verifier_et_parametres_defaut(self):
        donnees = ecrire_donnees(self.tmp, {})
        pl = os.path.join(self.tmp, "pl.json")
        chemins.ecrire_json(pl, {"format": "placements/2", "pieces": []})
        verdict = os.path.join(self.tmp, "verdict.json")
        r = lancer_faux("verifier", "--donnees", donnees, "--placement", pl, "--routage", pl, "--mode", "altium",
                        "--sortie", verdict)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(lire_json(verdict)["format"], "verdict/2")
        r = lancer_faux("parametres-defaut")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), annexe_c_corrigee())
        r = lancer_faux("parametres-defaut", "--qualite", "excellent")
        self.assertEqual(r.returncode, 2)


def attendre(condition, delai_s: float, pas: float = 0.05) -> bool:
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(pas)
    return bool(condition())


# ================================================================================================ orchestrateur

# Coûts et validité des 20 graines de run_4 (étude, annexe A) : 12 valides.
RUN_4 = {3001: 11539.58, 3002: 10700.05, 3003: None, 3004: None, 3005: None, 3006: 11051.22, 3007: 11717.80,
         3008: 11786.78, 3009: 11580.66, 3010: None, 3011: None, 3012: None, 3013: 12220.69, 3014: None,
         3015: 10793.85, 3016: None, 3017: 10976.93, 3018: 10090.64, 3019: 12473.45, 3020: 10334.96}

# Clés des évènements de §5.3 (en plus de « type »).
CLES_EVENEMENTS = {
    "debut": {"version", "essais", "paralleles", "fils_par_essai", "qualite", "sortie", "duree_prevue_s",
              "duree_max_s", "t"},
    "etape": {"etape", "etat", "texte"},
    "graine": {"graine", "etat", "valide", "cout", "liaisons_ouvertes", "duree_s", "texte"},
    "phase": {"graine", "phase", "etat", "fraction"},
    "avertissement": {"texte"},
    "erreur": {"texte"},
    "fin": {"code", "meilleure", "livrable", "texte"},
}


def donnees_completes(dossier: str, scenario: dict, nom: str = "donnees_placeur.json") -> str:
    """Données au format donnees_placeur/2 valides (petit cas du corpus du lot E, lu seulement) portant un scénario
    du faux moteur (champ inconnu, ignoré par les validateurs)."""
    d = lire_json(os.path.join(ICI, "corpus", "validite", "002_contacts_stricts_vias", "donnees_placeur.json"))
    d["_faux_scenario"] = scenario
    chemin = os.path.join(dossier, nom)
    chemins.ecrire_json(chemin, d)
    return chemin


def _verdict_moteur(sc: dict) -> tuple[bool, int]:
    valide = bool(sc.get("valide", sc["type"] not in ("invalide", "erreur_interne")))
    return valide, int(sc.get("liaisons_ouvertes", 0 if valide else 2))


def _doublures():
    import placeur

    class DoublureControles(placeur.Controles):
        """Doublures des contrôles de §5.2 (lot C) : elles suivent le scénario de la graine (`_faux_graine` des
        sorties du faux moteur). Champs : placement_valide, drc_strict_valide, drc_altium_valide, violations."""

        def verifier_placement(self, placements, donnees):
            sc = _scenario(donnees, placements.get("_faux_graine"))
            ok = bool(sc.get("placement_valide", True))
            d = exemple_lot_e("controle_placement.json")
            viol = (d["violations"] or [{"genre": "chevauchement", "pieces": ["R1", "R2"], "texte": "x"}])[0]
            d["valide"] = ok
            d["violations"] = [] if ok else [viol]
            d["violations_par_type"] = {k: 0 for k in d["violations_par_type"]}
            if not ok:
                d["violations_par_type"][viol["genre"]] = 1
            return d

        def drc(self, chemin_donnees, chemin_placement, chemin_routage, mode, chemin_sortie, job=None):
            sc = _scenario(lire_json(chemin_donnees), lire_json(chemin_routage).get("_faux_graine"))
            _, ouvertes = _verdict_moteur(sc)
            violations = int(sc.get("violations", 0))
            valide = bool(sc.get(f"drc_{mode}_valide", ouvertes == 0 and violations == 0))
            d = verdict_faux(mode, valide, ouvertes, violations, outil="doublure")
            chemins.ecrire_json(chemin_sortie, d)
            return d

        def mesures(self, donnees, placements, routage, verdict_strict):
            g = routage.get("_faux_graine")
            sc = _scenario(donnees, g)
            d = exemple_lot_e("mesures.json")
            cout = float(sc["cout"] if "cout" in sc else 10000.0 + g)
            # Termes choisis pour que la formule cout_commun/2 (§4.7) redonne exactement le coût du scénario.
            d["cout_commun"] = cout
            d["termes"] = {"longueur_mm": cout, "vias": 0, "retreci_mm": 0.0, "resistance_puissance_mohm": 0.0,
                           "coudes": 0, "ecart_moyen_mm": d["parametres"]["seuil_ecart_mm"]}
            return d

        def proprete(self, donnees, placements, routage):
            return exemple_lot_e("proprete.json")

        def convertisseurs(self, donnees, placements, routage):
            d = exemple_lot_e("mesure_convertisseurs.json")
            d["convertisseurs"] = []
            return d

    return DoublureControles


class Execution:
    """Résultat d'un orchestrateur lancé dans le processus des essais."""

    def __init__(self):
        self.code = None
        self.sortie = ""
        self.erreur = ""
        self.dossier = None
        self.rapport = None
        self.validations: list = []

    def evenements(self) -> list[dict]:
        return [json.loads(l[4:]) for l in self.sortie.splitlines() if l.startswith("EVT ")]

    def stats(self, g: int) -> dict:
        return lire_json(os.path.join(self.dossier, "technique", f"graine_{g}", "stats.json"))


class Environnement:
    """Faux moteur à la place de chemins.commande_moteur, et contrôle de chaque rapport.json écrit."""

    def __init__(self, prefixe=()):
        from unittest import mock
        import formats
        self.validations: list = []
        vrai = chemins.ecrire_json

        def espion(chemin, objet, indent=1):
            vrai(chemin, objet, indent)
            if os.path.basename(chemin) == "rapport.json":
                self.validations.append(formats.valider_rapport(objet))

        self._patchs = [mock.patch.object(chemins, "commande_moteur",
                                          lambda nom, *a, **kw: FAUX_MOTEUR + list(prefixe) + [str(x) for x in a]),
                        mock.patch.object(chemins, "ecrire_json", espion)]

    def __enter__(self):
        for p in self._patchs:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchs):
            p.stop()
        return False


def executer_placeur(args, controles=None) -> Execution:
    import io
    import placeur
    res = Execution()
    sortie, erreur = io.StringIO(), io.StringIO()
    res.code = placeur.main([str(a) for a in args], controles=controles or _doublures()(), flux=sortie,
                            flux_erreur=erreur)
    res.sortie, res.erreur = sortie.getvalue(), erreur.getvalue()
    return res


def dossiers_calcul(parent: str) -> list[str]:
    if not os.path.isdir(parent):
        return []
    return sorted(os.path.join(parent, n) for n in os.listdir(parent) if os.path.isdir(os.path.join(parent, n)))


def lignes_journal(chemin: str) -> list[list[str]]:
    if not os.path.exists(chemin):
        return []
    with open(chemin, encoding="ascii") as f:
        return [l.split() for l in f.read().splitlines() if l.strip()]


def max_simultanes(lignes: list[list[str]]) -> int:
    """Plus grand nombre de moteurs vivants ensemble, d'après le journal horodaté du faux moteur."""
    bornes = sorted((float(l[3]), 1 if l[0] == "debut" else -1) for l in lignes)
    n = m = 0
    for _, d in sorted(bornes, key=lambda b: (b[0], b[1])):
        n += d
        m = max(m, n)
    return m


class TestOrchestrateur(unittest.TestCase):
    """placeur.py --donnees (§5.3) avec le faux moteur et des doublures des contrôles de §5.2."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_placeur_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.parent = os.path.join(self.tmp, "res")
        self.journal = os.path.join(self.tmp, "vivants.txt")

    def lancer(self, scenario, *args, prefixe=None, controles=None) -> Execution:
        donnees = donnees_completes(self.tmp, scenario)
        prefixe = ["--faux-journal", self.journal] if prefixe is None else prefixe
        with Environnement(prefixe) as env:
            res = executer_placeur(["--donnees", donnees, "--sortie", self.parent, *args], controles)
        res.validations = env.validations
        dossiers = dossiers_calcul(self.parent)
        if dossiers:
            res.dossier = dossiers[-1]
            chemin = os.path.join(res.dossier, "technique", "rapport.json")
            if os.path.exists(chemin):
                res.rapport = lire_json(chemin)
        return res

    def graines(self, res: Execution) -> dict:
        return {e["graine"]: e for e in res.rapport["graines"]}

    def assertRapportsValides(self, res: Execution):
        self.assertTrue(res.validations)
        for erreurs in res.validations:
            self.assertEqual(erreurs, [])

    # ------------------------------------------------------------------------------------- codes et choix
    def test_code_0_choix_et_garder_graines(self):
        sc = {"1": {"cout": 12000.0}, "2": {"cout": 11000.0}, "3": {"type": "invalide", "cout": 9000.0}}
        res = self.lancer(sc, "--essais", 3, "--processeurs", 2)
        self.assertEqual(res.code, 0, res.erreur)
        self.assertRapportsValides(res)
        r = res.rapport
        self.assertEqual((r["format"], r["termine"], r["fin"], r["meilleure"], r["code"]), ("rapport/2", True,
                                                                                         "normale", 2, 0))
        g = self.graines(res)
        self.assertEqual([g[i]["valide"] for i in (1, 2, 3)], [True, True, False])
        self.assertEqual(g[2]["dossier"], "technique/graine_2")
        self.assertEqual(g[2]["drc_strict"], {"valide": True, "ouvertes": 0, "violations": 0})
        technique = os.path.join(res.dossier, "technique")
        for nom in ("journal.txt", "parametres_utilises.ini", "parametres_moteur.json", "donnees_placeur.json"):
            self.assertTrue(os.path.isfile(os.path.join(technique, nom)), nom)
        self.assertFalse(os.path.exists(os.path.join(res.dossier, ".verrou")))
        for i in (1, 3):                       # garder_graines = meilleure : fichiers lourds supprimés
            self.assertFalse(os.path.exists(os.path.join(technique, f"graine_{i}", "routage_detaille.json")))
            self.assertTrue(os.path.exists(os.path.join(technique, f"graine_{i}", "stats.json")))
            self.assertTrue(os.path.exists(os.path.join(technique, f"graine_{i}", "drc_strict.json")))
        for nom in ("routage_detaille.json", "placements_routage.json", "console.txt", "controle_placement.json",
                    "drc_altium.json", "mesures.json"):
            self.assertTrue(os.path.exists(os.path.join(technique, "graine_2", nom)), nom)
        params = lire_json(os.path.join(technique, "parametres_moteur.json"))
        self.assertEqual(params["campagne"]["threads"], 1)
        res = self.lancer(sc, "--essais", 3, "--garder-graines", "toutes")
        self.assertTrue(os.path.exists(os.path.join(res.dossier, "technique", "graine_1", "routage_detaille.json")))

    def test_code_1_graine_a_terminer(self):
        sc = {"defaut": {"type": "invalide"}, "1": {"liaisons_ouvertes": 3, "cout": 9000.0},
              "2": {"liaisons_ouvertes": 1, "violations": 1, "cout": 9000.0},
              "3": {"liaisons_ouvertes": 2, "cout": 12000.0}, "4": {"liaisons_ouvertes": 2, "cout": 11000.0}}
        res = self.lancer(sc, "--essais", 4, "--processeurs", 4)
        self.assertEqual(res.code, 1, res.erreur)
        self.assertEqual((res.rapport["meilleure"], res.rapport["a_terminer"]), (None, 4))
        self.assertTrue(os.path.exists(os.path.join(res.dossier, "technique", "graine_4", "routage_detaille.json")))
        self.assertFalse(os.path.exists(os.path.join(res.dossier, "technique", "graine_3", "routage_detaille.json")))

    def test_code_2_du_moteur_arrete_tout(self):
        res = self.lancer({"2": {"type": "code2", "message": "contraintes.fixes[3] : pièce C99 inconnue"}},
                          "--essais", 4, "--processeurs", 1)
        self.assertEqual(res.code, 2)
        self.assertIn("contraintes.fixes[3] : pièce C99 inconnue", res.erreur)
        self.assertRapportsValides(res)
        self.assertEqual(sorted(self.graines(res)), [1, 2])
        self.assertEqual(res.rapport["graines_non_lancees"], [3, 4])
        self.assertEqual((res.rapport["code"], res.rapport["erreur"] is not None), (2, True))
        self.assertEqual([l[1] for l in lignes_journal(self.journal) if l[0] == "debut"], ["1"])

    def test_plantages_et_code_4_du_moteur(self):
        sc = {"1": {"type": "plantage"}, "2": {"type": "erreur_interne"}, "3": {"cout": 10500.0}}
        if WINDOWS:
            sc["4"] = {"type": "plantage", "code": 0xC0000409}
        n = len(sc)
        res = self.lancer(sc, "--essais", n, "--processeurs", 2, "--evenements")
        self.assertEqual(res.code, 0, res.erreur)
        g = self.graines(res)
        self.assertEqual((g[1]["plantage"], g[1]["etat"], g[1]["valide"]), (True, "plantee", False))
        self.assertIn("plantage du moteur (code 101)", g[1]["texte"])
        self.assertEqual((g[2]["plantage"], g[2]["etat"]), (False, "abandonnee"))
        self.assertIn("erreur interne du moteur", g[2]["texte"])
        self.assertTrue(g[3]["valide"])
        if WINDOWS:
            self.assertEqual((g[4]["plantage"], g[4]["etat"]), (True, "plantee"))
            self.assertIn("0xC0000409", g[4]["texte"])
        etats = {e["graine"]: e["etat"] for e in res.evenements()
                 if e["type"] == "graine" and e["etat"] not in ("en_attente", "lancee")}
        self.assertEqual(etats[1], "plantee")
        self.assertEqual(etats[2], "abandonnee")
        self.assertEqual(etats[3], "terminee")
        self.assertEqual(res.rapport["meilleure"], 3)

    def test_erreur_interne_de_l_orchestrateur(self):
        from unittest import mock
        import placeur

        def panne(self):
            raise RuntimeError("panne provoquée")
        with mock.patch.object(placeur.Calcul, "choisir", panne):
            res = self.lancer({}, "--essais", 1)
        self.assertEqual(res.code, 4)
        chemin = os.path.join(res.dossier, "technique", "erreur.txt")
        with open(chemin, encoding="utf-8") as f:
            texte = f.read()
        self.assertIn("RuntimeError: panne provoquée", texte)
        self.assertNotIn(self.tmp, texte)
        self.assertNotIn(ICI, texte)
        self.assertIsNone(chemins.MOTIF_LECTEUR.search(texte))
        self.assertEqual(res.rapport["code"], 4)
        self.assertIn("erreur.txt", res.erreur)

    # ------------------------------------------------------------------------------------- arrêts, robustesse
    def test_stats_d_un_essai_precedent_jamais_relu(self):
        import placeur
        dossier = os.path.join(self.tmp, "graine_5")
        os.makedirs(dossier)
        piege = os.path.join(dossier, "stats.json")
        chemins.ecrire_json(piege, {"format": "stats/2", "graine": 5, "valide": True})
        os.utime(piege, (time.time() - 3600, time.time() - 3600))
        stats, refus = placeur.lire_stats(piege, time.time(), 5)
        self.assertIsNone(stats)
        self.assertIn("antérieur au lancement", refus)
        os.utime(piege, None)
        stats, refus = placeur.lire_stats(piege, time.time(), 5)       # récent mais incomplet : refusé
        self.assertIsNone(stats)
        self.assertIn("stats.json refusé", refus)
        placeur.vider_dossier(dossier)
        self.assertEqual(os.listdir(dossier), [])
        # le dossier de la graine est vidé avant le lancement : une graine plantée ne relit rien
        res = self.lancer({"defaut": {"type": "plantage"}}, "--essais", 1)
        self.assertEqual(res.code, 1)
        self.assertEqual(self.graines(res)[1]["etat"], "plantee")

    def test_parent_reutilise_nouveau_dossier(self):
        a = self.lancer({}, "--essais", 1)
        b = self.lancer({}, "--essais", 1)
        self.assertEqual((a.code, b.code), (0, 0))
        dossiers = dossiers_calcul(self.parent)
        self.assertEqual(len(dossiers), 2)
        for d in dossiers:
            self.assertRegex(os.path.basename(d), r"^donnees_placeur_\d{8}-\d{6}$")
            self.assertTrue(os.path.isfile(os.path.join(d, "technique", "rapport.json")))

    def _surveiller(self, n_lances: int, action, delai_s: float = 30):
        """Fil qui attend `n_lances` moteurs lancés (journal du faux), puis appelle action(dossier)."""
        etat = {}

        def corps():
            ok = attendre(lambda: dossiers_calcul(self.parent) and
                          sum(1 for l in lignes_journal(self.journal) if l[0] == "debut") >= n_lances, delai_s)
            etat["pret"] = ok
            if ok:
                etat["t"] = time.monotonic()
                action(dossiers_calcul(self.parent)[-1])
                time.sleep(2.0)
                pids = [int(l[2]) for l in lignes_journal(self.journal) if l[0] == "debut"]
                etat["vivants"] = [p for p in pids if chemins.processus_vivant(p)]
        t = threading.Thread(target=corps, daemon=True)
        t.start()
        return t, etat

    def test_arret_lancements(self):
        t, etat = self._surveiller(2, lambda d: open(os.path.join(d, "arret_lancements"), "w").close())
        res = self.lancer({"defaut": {"type": "lent", "duree_s": 1.5}}, "--essais", 4, "--processeurs", 2)
        t.join()
        self.assertTrue(etat.get("pret"))
        self.assertEqual(res.code, 3, res.erreur)
        self.assertRapportsValides(res)
        g = self.graines(res)
        self.assertEqual(sorted(g), [1, 2])
        self.assertTrue(all(e["valide"] and e["fin"] == "normale" for e in g.values()))
        self.assertEqual(res.rapport["graines_non_lancees"], [3, 4])
        self.assertEqual((res.rapport["fin"], res.rapport["termine"], res.rapport["meilleure"]),
                         ("arret_demande", False, 1))

    def test_arret_demande(self):
        t, etat = self._surveiller(2, lambda d: open(os.path.join(d, "arret_demande"), "w").close())
        res = self.lancer({"defaut": {"type": "lent", "duree_s": 30}}, "--essais", 3, "--processeurs", 2)
        t.join()
        self.assertTrue(etat.get("pret"))
        self.assertEqual(etat["vivants"], [])
        self.assertEqual(res.code, 3, res.erreur)
        self.assertRapportsValides(res)
        self.assertEqual((res.rapport["fin"], res.rapport["termine"]), ("arret_demande", False))
        self.assertEqual({e["fin"] for e in res.rapport["graines"]}, {"arret_demande"})
        self.assertEqual(res.rapport["graines_non_lancees"], [3])

    def test_ctrl_c(self):
        import _thread
        import signal
        if threading.current_thread() is not threading.main_thread():
            self.skipTest("Ctrl+C simulé dans le fil principal seulement")
        t, etat = self._surveiller(2, lambda d: _thread.interrupt_main(signal.SIGINT))
        res = self.lancer({"defaut": {"type": "lent", "duree_s": 30}}, "--essais", 3, "--processeurs", 2)
        t.join()
        self.assertTrue(etat.get("pret"))
        self.assertEqual(etat["vivants"], [])
        self.assertEqual(res.code, 3, res.erreur)
        self.assertRapportsValides(res)
        self.assertEqual((res.rapport["fin"], res.rapport["termine"]), ("arret_demande", False))
        self.assertTrue(os.path.exists(os.path.join(res.dossier, "arret_demande")))

    def test_duree_max(self):
        from unittest import mock
        import placeur
        t0 = time.time()
        with mock.patch.object(placeur, "SECONDES_PAR_MINUTE", 1):      # --duree-max-min 5 → 5 s
            res = self.lancer({"defaut": {"type": "lent", "duree_s": 1.0}}, "--essais", 10, "--processeurs", 2,
                              "--duree-max-min", 5)
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual((res.rapport["fin"], res.rapport["termine"]), ("duree_max", True))
        self.assertTrue(res.rapport["graines_non_lancees"])
        self.assertGreaterEqual(len(res.rapport["graines"]), 2)
        fins = [res.stats(g)["_faux"]["fin"] for g in self.graines(res) if res.stats(g).get("_faux")]
        self.assertLessEqual(max(fins), t0 + 5 + 1.5)     # aucune graine lancée qui ne pouvait finir à temps

    def test_valides_voulues(self):
        res = self.lancer({"3": {"type": "invalide"}}, "--essais", 6, "--processeurs", 1, "--valides-voulues", 2)
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual(sorted(self.graines(res)), [1, 2])
        self.assertEqual(res.rapport["graines_non_lancees"], [3, 4, 5, 6])
        self.assertEqual((res.rapport["fin"], res.rapport["termine"]), ("valides_voulues", True))

    def test_second_orchestrateur_meme_dossier(self):
        donnees = donnees_completes(self.tmp, {"defaut": {"type": "lent", "duree_s": 30}})
        res_a = {}
        with Environnement(["--faux-journal", self.journal]):
            a = threading.Thread(target=lambda: res_a.update(r=executer_placeur(
                ["--donnees", donnees, "--sortie", self.parent, "--essais", 1])), daemon=True)
            a.start()
            self.assertTrue(attendre(lambda: lignes_journal(self.journal), 30))
            dossier = dossiers_calcul(self.parent)[-1]
            b = executer_placeur(["--donnees", donnees, "--reprendre", dossier])
            open(os.path.join(dossier, "arret_demande"), "w").close()
            a.join(30)
        self.assertEqual(b.code, 2)
        self.assertIn("Un calcul est déjà en cours dans ce dossier.", b.erreur)
        self.assertEqual(res_a["r"].code, 3)

    # ------------------------------------------------------------------------------------- parallélisme
    def test_parallelisme_et_priorite(self):
        res = self.lancer({"defaut": {"type": "cpu", "duree_s": 0.1, "cpu_s": 2.5}}, "--essais", 2,
                          "--processeurs", 2, "--garder-graines", "toutes")
        self.assertEqual(res.code, 0, res.erreur)
        faux = [res.stats(g)["_faux"] for g in (1, 2)]
        rapport = sum(f["cpu_s"] for f in faux) / (max(f["fin"] for f in faux) - min(f["debut"] for f in faux))
        self.assertGreaterEqual(rapport, 1.8)
        for f in faux:
            if WINDOWS:
                self.assertEqual(f["priorite"], chemins.BELOW_NORMAL_PRIORITY_CLASS)
            else:
                self.assertGreaterEqual(f["priorite"], chemins.NICE_PRIORITE_BASSE)
        self.assertEqual(res.rapport["estimation"]["paralleles"], 2)
        res = self.lancer({"defaut": {"duree_s": 0.05}}, "--essais", 1, "--priorite-normale",
                          "--garder-graines", "toutes")
        if WINDOWS:
            self.assertEqual(res.stats(1)["_faux"]["priorite"], chemins.NORMAL_PRIORITY_CLASS)

    def test_deux_orchestrateurs_processeurs_2(self):
        donnees = donnees_completes(self.tmp, {"defaut": {"type": "lent", "duree_s": 1.0}})
        procs = [chemins.lancer([chemins.interpreteur(), os.path.abspath(__file__), "--orchestrateur-faux",
                                 self.journal, "--", "--donnees", donnees, "--sortie",
                                 os.path.join(self.tmp, f"res_{i}"), "--essais", 3, "--processeurs", 2],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in (1, 2)]
        for p in procs:
            _, erreur = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, erreur)
        lignes = lignes_journal(self.journal)
        self.assertEqual(sum(1 for l in lignes if l[0] == "debut"), 6)
        self.assertEqual(max_simultanes(lignes), 2)

    def test_incoherence(self):
        res = self.lancer({"1": {"drc_strict_valide": False}, "2": {"type": "invalide"}}, "--essais", 2,
                          "--evenements")
        self.assertEqual(res.code, 1, res.erreur)
        g = self.graines(res)
        self.assertEqual((g[1]["valide_moteur"], g[1]["valide"], g[1]["incoherence"]), (True, False, True))
        self.assertFalse(g[2]["incoherence"])
        self.assertEqual(res.rapport["incoherences"], 1)
        erreurs = [e for e in res.evenements() if e["type"] == "erreur"]
        self.assertEqual(len(erreurs), 1)
        self.assertIs(erreurs[0]["incoherence"], True)

    def test_controles_indisponibles_jamais_supposes_reussis(self):
        """Contrôles réels avant la livraison de C (API de §5.2 absente) : refus noté, graine jamais valide."""
        import placeur
        res = self.lancer({}, "--essais", 1, controles=placeur.Controles())
        self.assertEqual(res.code, 1, res.erreur)
        g = self.graines(res)[1]
        self.assertEqual((g["valide_moteur"], g["valide"], g["incoherence"]), (True, False, True))
        self.assertFalse(g["placement"]["valide"])
        self.assertFalse(g["drc_strict"]["valide"])
        # après intégration du lot C, le contrôle peut être réellement disponible : il doit alors refuser
        self.assertTrue("erreur" in g["drc_altium"] or g["drc_altium"].get("valide") is False, g["drc_altium"])

    # ------------------------------------------------------------------------------------- rapport et évènements
    def test_rapport_et_statistiques_run_4(self):
        import placeur
        sc = {"defaut": {"duree_s": 0.05}}
        for g, cout in RUN_4.items():
            sc[str(g)] = {"cout": cout} if cout is not None else {"type": "invalide", "cout": 13000.0}
        res = self.lancer(sc, "--graine-depart", 3001, "--essais", 20, "--processeurs", 4)
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual(len(res.validations), 20 + 2)          # après préparation, 20 graines, fin
        self.assertRapportsValides(res)
        s = res.rapport["statistiques"]
        self.assertEqual((s["graines_mesurees"], s["graines_valides"]), (20, 12))
        self.assertEqual([round(x, 3) for x in s["wilson_95"]], [0.387, 0.781])
        self.assertEqual((s["meilleur_cout"], round(s["mediane_cout"], 1)), (10090.64, 11295.4))
        temps = sum(e["temps_calcul_s"] for e in res.rapport["graines"])
        self.assertAlmostEqual(s["calcul_par_graine_valide_s"], temps / 12, places=2)
        self.assertEqual(res.rapport["meilleure"], 3018)
        s = placeur.statistiques(res.rapport["graines"], reference=10328.5)
        self.assertAlmostEqual(s["p_campagne"], 1 - (19 / 20) ** 8, places=6)
        self.assertEqual(round(s["p_campagne"], 3), 0.337)
        self.assertEqual(placeur.statistiques([])["wilson_95"], None)

    def test_evenements(self):
        res = self.lancer({"1": {"duree_s": 0.3}, "2": {"type": "invalide"}}, "--essais", 2, "--evenements")
        self.assertEqual(res.code, 0, res.erreur)
        evts = []
        for ligne in res.sortie.splitlines():
            if ligne.startswith("EVT "):
                e = json.loads(ligne[4:])
                self.assertIn(e["type"], CLES_EVENEMENTS)
                attendues = CLES_EVENEMENTS[e["type"]] | {"type"} | ({"incoherence"} if e["type"] == "erreur" else set())
                self.assertLessEqual(set(e), attendues, ligne)
                self.assertGreaterEqual(set(e), CLES_EVENEMENTS[e["type"]] | {"type"}, ligne)
                if e["type"] != "debut":
                    self.assertNotIn(self.tmp, ligne)
                    self.assertIsNone(chemins.MOTIF_LECTEUR.search(ligne), ligne)
                evts.append(e)
        types = [e["type"] for e in evts]
        self.assertEqual((types[0], types[-1]), ("etape", "fin"))
        debut = [e for e in evts if e["type"] == "debut"]
        self.assertEqual(len(debut), 1)
        self.assertTrue(os.path.isabs(debut[0]["sortie"]))
        self.assertEqual(os.path.normcase(debut[0]["sortie"]), os.path.normcase(res.dossier))
        self.assertEqual((debut[0]["essais"], debut[0]["fils_par_essai"], debut[0]["qualite"]), (2, 1, "normal"))
        self.assertIn("phase", types)
        self.assertEqual(evts[-1]["code"], 0)
        self.assertEqual(evts[-1]["meilleure"], 1)
        with open(os.path.join(res.dossier, "technique", "journal.txt"), encoding="utf-8") as f:
            journal = f.read()
        self.assertNotIn(self.tmp, journal)
        self.assertIsNone(chemins.MOTIF_LECTEUR.search(journal))

    def test_plafond_ajuste_au_score(self):
        from unittest import mock
        with mock.patch.dict(parametres.REFERENCE_DUREE, {"score": 2000.0}):
            res = self.lancer({}, "--essais", 1, prefixe=["--faux-score", "1000"])
        self.assertEqual(res.code, 0, res.erreur)
        params = lire_json(os.path.join(res.dossier, "technique", "parametres_moteur.json"))
        self.assertEqual(params["campagne"]["duree_max_s"], 2 * parametres_niveau("normal")["campagne"]["duree_max_s"])
        self.assertEqual(res.rapport["estimation"]["score"], 1000.0)
        self.assertIsNotNone(res.rapport["estimation"]["duree_prevue_s"])
        self.assertIsNotNone(res.rapport["estimation"]["duree_graine_mesuree_s"])

    def test_ligne_de_commande(self):
        import placeur
        for args, morceau in ((["--donnees"], "valeur manquante"), (["--donnees", "d.json", "--essai", "2"],
                                                                     "vouliez-vous dire --essais"),
                              (["carte.PcbDoc", "--donnees", "d.json"], "pas les deux"), ([], "carte manquante"),
                              (["--version", "--evenements"], "s'emploie seule"),
                              (["--donnees", "d.json", "--essais", "huit"], "--essais")):
            with self.assertRaises(placeur.ErreurEntree) as e:
                placeur.analyser_arguments(args)
            self.assertIn(morceau, str(e.exception))
        res = executer_placeur(["--version"])
        self.assertEqual((res.code, res.sortie.strip()), (0, "placeur " + chemins.version_lanceur()))
        for args, morceau in ((["carte.PcbDoc"], "carte introuvable"), (["--init", "carte.PcbDoc"], "introuvable"),
                              (["carte.txt"], "non prise en charge"), (["--analyser"], "CARTE")):
            res = executer_placeur(args)
            self.assertEqual(res.code, 2, args)
            self.assertIn(morceau, res.erreur)
        donnees = donnees_completes(self.tmp, {})
        res = executer_placeur(["--donnees", donnees, "--qualite", "excellente"])
        self.assertEqual(res.code, 2)
        self.assertIn("qualite", res.erreur)
        mauvais = os.path.join(self.tmp, "mauvais.json")
        chemins.ecrire_json(mauvais, {"format": "donnees_placeur/2"})
        res = executer_placeur(["--donnees", mauvais])
        self.assertEqual(res.code, 2)
        self.assertIn("mauvais.json", res.erreur)
        self.assertGreater(chemins.memoire_disponible_mo() or 1, 0)


# ================================================================================================ chaîne depuis une carte

def sha256(chemin: str) -> str:
    import hashlib
    with open(chemin, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _doublure_outils():
    import placeur

    class DoublureOutils(placeur.Outils):
        """Doublures des API de §5.2 des lots B et C : elles rendent les exemples validés du lot E
        (corpus/formats/exemples, lus seulement). Réglages (`self.reglage`) : routee, gabarit_utilisable,
        echec_carte (graines), couture_fautive, scenario (faux moteur)."""

        def __init__(self, **reglage):
            self.reglage = reglage
            self.appels: list = []

        def importer(self, chemin):
            self.appels.append(("importer", os.path.basename(chemin)))
            e = exemple_lot_e("entree_carte.json")
            e["source"]["fichier"] = os.path.basename(chemin)
            e["source"]["sha256"] = sha256(chemin)
            e["composants"][0]["verrouille"] = True
            if not self.reglage.get("routee"):
                e["carte"]["cuivre_existant"] = []
            return e

        def controler_gabarit(self, genre, gabarit, entree):
            self.appels.append(("controler_gabarit", genre))
            g = exemple_lot_e("controle_gabarit.json")
            g["utilisable"] = bool(self.reglage.get("gabarit_utilisable", True))
            g["erreurs"] = [] if g["utilisable"] else ["aucun via modèle"]
            return g

        def deduire(self, entree, surcharges=None):
            self.appels.append(("deduire", surcharges))
            c = exemple_lot_e("config_carte.json")
            c["pieces_a_fixer_suggerees"] = ["M1"]
            return c

        def construire(self, entree, contraintes, config, controle_gabarit=None):
            self.appels.append(("construire", contraintes))
            d = lire_json(os.path.join(ICI, "corpus", "validite", "002_contacts_stricts_vias", "donnees_placeur.json"))
            trou = dict(exemple_lot_e("donnees_placeur.json")["trous"][0], id="TS1", x=82.0, y=49.5,
                        classe_isolement=0)
            d["trous"] = [trou]
            d["_faux_scenario"] = self.reglage.get("scenario", {})
            return d, exemple_lot_e("deduction.json")

        def extraire_reference(self, chemin_carte, entree):
            p, r = exemple_lot_e("placements.json"), exemple_lot_e("routage.json")
            p["_faux_graine"] = r["_faux_graine"] = "reference"
            return p, r, None

        def couture(self, donnees, placements, routage):
            return exemple_lot_e("vias_couture.json")

        def drc_couture(self, chemin_donnees, cp, cr, cv, cs, job=None):
            v = lire_json(cv)
            self.appels.append(("drc_couture", len(v["vias"])))
            fautif = self.reglage.get("couture_fautive") and len(v["vias"]) == 2
            d = verdict_faux("strict", not fautif, 0, 1 if fautif else 0, outil="doublure")
            if fautif:
                via = v["vias"][0]
                d["detail_violations"][0]["position_mm"] = [via["x"] * 0.0254, via["y"] * 0.0254]
            chemins.ecrire_json(cs, d)
            return d

        def generer(self, genre, dossier_graine, sortie, gabarit, config, sans_serigraphie=False, opts=None):
            g = int(os.path.basename(dossier_graine).split("_")[1])
            self.appels.append(("generer", g, sans_serigraphie, lire_json(config)["vias_couture"]))
            r = exemple_lot_e("rapport_pcbdoc.json")
            r.update({"fichier": os.path.basename(sortie), "livrable": lire_json(config)["livrable"]})
            if g in self.reglage.get("echec_carte", ()):
                r["controles"]["erreurs"] = ["piste hors de la carte"]
            with open(sortie, "wb") as f:
                f.write(b"carte produite")
            return r

    return DoublureOutils


class TestChaine(unittest.TestCase):
    """placeur.py CARTE (§5.3) : doublures B et C, faux moteur."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_chaine_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.parent = os.path.join(self.tmp, "res")

    def carte(self, genre="kicad") -> str:
        if genre == "kicad":
            source = os.path.join(ICI, "corpus", "cartes", "deux_couches", "deux_couches.kicad_pcb")
            chemin = os.path.join(self.tmp, "deux_couches.kicad_pcb")
            shutil.copyfile(source, chemin)
        else:
            chemin = os.path.join(self.tmp, "demo.PcbDoc")
            with open(chemin, "wb") as f:
                f.write(b"\xd0\xcf\x11\xe0 carte de demonstration (doublure de l'import)")
        return chemin

    def lancer(self, carte, *args, **reglage) -> Execution:
        import io
        import placeur
        self.outils = _doublure_outils()(**reglage)
        with Environnement(["--faux-journal", os.path.join(self.tmp, "vivants.txt")]) as env:
            res = Execution()
            sortie, erreur = io.StringIO(), io.StringIO()
            res.code = placeur.main([carte, "--sortie", self.parent, *[str(a) for a in args]],
                                    controles=_doublures()(), outils=self.outils, flux=sortie, flux_erreur=erreur)
            res.sortie, res.erreur = sortie.getvalue(), erreur.getvalue()
        res.validations = env.validations
        dossiers = dossiers_calcul(self.parent)
        if dossiers:
            res.dossier = dossiers[-1]
            chemin = os.path.join(res.dossier, "technique", "rapport.json")
            if os.path.exists(chemin):
                res.rapport = lire_json(chemin)
        return res

    def rapport_txt(self, res) -> str:
        with open(os.path.join(res.dossier, "rapport.txt"), "rb") as f:
            octets = f.read()
        self.assertTrue(octets.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", octets.replace(b"\r\n", b""))
        return octets[3:].decode("utf-8")

    def test_chaine_kicad(self):
        import formats
        carte = self.carte()
        avant = sha256(carte)
        sc = {"1": {"cout": 12000.0}, "2": {"cout": 11000.0}}
        res = self.lancer(carte, "--essais", 2, "--processeurs", 2, scenario=sc)
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual(sha256(carte), avant)
        for erreurs in res.validations:
            self.assertEqual(erreurs, [])
        r = res.rapport
        self.assertEqual((r["meilleure"], r["incoherences"], r["mode"]), (2, 0, "carte"))
        self.assertEqual(r["livrable"]["fichier"], "deux_couches_g2.kicad_pcb")
        self.assertEqual(sorted(os.listdir(res.dossier)), ["deux_couches_g2.kicad_pcb", "rapport.txt", "technique"])
        technique = os.path.join(res.dossier, "technique")
        attendus = {"rapport.json", "journal.txt", "parametres_utilises.ini", "parametres_moteur.json",
                    "contraintes_effectives.json", "entree", "entree_carte.json", "controle_gabarit.json",
                    "config_carte.json", "donnees_placeur.json", "deduction.json", "config_pcbdoc.json",
                    "graine_1", "graine_2", "carte_2_rapport.json"}
        self.assertEqual(set(os.listdir(technique)), attendus)
        self.assertEqual(os.listdir(os.path.join(technique, "entree")), ["deux_couches.kicad_pcb"])
        for nom in ("vias_couture.json", "drc_couture.json"):
            self.assertTrue(os.path.isfile(os.path.join(technique, "graine_2", nom)), nom)
        cfg = lire_json(os.path.join(technique, "config_pcbdoc.json"))
        self.assertEqual(formats.valider_config_pcbdoc(cfg), [])
        self.assertEqual((cfg["livrable"], cfg["vias_couture"], cfg["serigraphie"]["threads"]), ("valide", True, 2))
        self.assertEqual(cfg["serigraphie"]["trous_supplementaires_mm"][0][:2], [82.0, 49.5])
        ce = lire_json(os.path.join(technique, "contraintes_effectives.json"))
        self.assertEqual(formats.valider_contraintes(ce), [])
        texte = self.rapport_txt(res)
        rubriques = ["AVERTISSEMENTS", "DÉDUCTIONS", "ALERTE : aucune règle de largeur propre", "GRAINES",
                     "GRAINE RETENUE : 2", "Le coût commun", "COMPARAISON", "LIVRABLE", "À FAIRE DANS KICAD"]
        positions = [texte.index(x) for x in rubriques]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("routage existant : aucun", texte)
        self.assertNotIn(self.tmp, texte)
        self.assertIn("deux_couches_g2.kicad_pcb (valide)", texte)
        self.assertIn("remplir les zones", texte)
        with open(os.path.join(technique, "parametres_utilises.ini"), encoding="utf-8-sig") as f:
            ini = f.read()
        self.assertNotIn(self.tmp, ini)

    def test_chaine_pcbdoc_reference_et_contraintes_ini(self):
        carte = self.carte("pcbdoc")
        avant = sha256(carte)
        ini = os.path.join(self.tmp, "p.ini")
        with open(ini, "w", encoding="utf-8") as f:
            f.write("[contraintes]\nfixes = U10\n")
        sc = {"1": {"cout": 12000.0}, "reference": {"cout": 15000.0}}
        res = self.lancer(carte, "--essais", 1, "--parametres", ini, routee=True, scenario=sc)
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual(sha256(carte), avant)
        self.assertTrue(os.path.isfile(os.path.join(res.dossier, "demo_g1.PcbDoc")))
        ref = os.path.join(res.dossier, "technique", "reference_existante")
        self.assertEqual(sorted(os.listdir(ref)), ["drc_strict.json", "mesures.json", "placements_routage.json",
                                                   "routage_detaille.json"])
        self.assertEqual(res.rapport["reference_existante"], {"cout_commun": 15000.0, "liaisons_ouvertes": 0,
                                                              "violations": 0})
        texte = self.rapport_txt(res)
        self.assertIn("routage existant : coût 15000 ; meilleur résultat : 12000", texte)
        self.assertIn("À FAIRE DANS ALTIUM", texte)
        self.assertIn("Recouler tout", texte)
        ce = lire_json(os.path.join(res.dossier, "technique", "contraintes_effectives.json"))
        self.assertIn("U10", ce["fixes"])
        self.assertEqual(self.outils.appels[1], ("controler_gabarit", "pcbdoc"))

    def test_a_terminer_et_apercu(self):
        carte = self.carte()
        res = self.lancer(carte, "--essais", 1, scenario={"defaut": {"type": "invalide"}})
        self.assertEqual(res.code, 1, res.erreur)
        self.assertEqual(res.rapport["livrable"]["fichier"], "deux_couches_A_TERMINER_g1.kicad_pcb")
        self.assertTrue(os.path.isfile(os.path.join(res.dossier, "deux_couches_A_TERMINER_g1.kicad_pcb")))
        texte = self.rapport_txt(res)
        self.assertIn("LIAISONS À FINIR À LA MAIN", texte)
        self.assertIn("réseau NetR25_2 : pastilles U10.8 ; position (86,8 ; 41,65) mm", texte)
        self.assertIn("(à terminer)", texte)
        res = self.lancer(carte, "--essais", 1, "--qualite", "apercu", scenario={})
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual(res.rapport["livrable"]["fichier"], "deux_couches_APERCU_g1.kicad_pcb")

    def test_repli_sur_trois_graines(self):
        carte = self.carte()
        sc = {str(g): {"cout": 10000.0 + g} for g in range(1, 5)}
        res = self.lancer(carte, "--essais", 4, "--processeurs", 2, scenario=sc, echec_carte=(1,))
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual((res.rapport["livrable"]["fichier"], res.rapport["meilleure"]),
                         ("deux_couches_g2.kicad_pcb", 2))
        self.assertFalse(os.path.exists(os.path.join(res.dossier, "deux_couches_g1.kicad_pcb")))
        res = self.lancer(carte, "--essais", 4, "--processeurs", 2, scenario=sc, echec_carte=(1, 2, 3))
        self.assertEqual(res.code, 1, res.erreur)
        self.assertIsNone(res.rapport["livrable"])
        self.assertEqual([c["graine"] for c in res.rapport["cartes_echouees"]], [1, 2, 3])
        self.assertEqual([a[1] for a in self.outils.appels if a[0] == "generer"], [1, 2, 3])
        self.assertIn("carte produite en échec", self.rapport_txt(res))

    def test_couture_fautive(self):
        res = self.lancer(self.carte(), "--essais", 1, scenario={}, couture_fautive=True)
        self.assertEqual(res.code, 0, res.erreur)
        c = res.rapport["couture"]
        self.assertEqual((c["vias"], c["retires"], c["drc"]["violations"]), (1, 1, 0))
        self.assertTrue(any("1 via(s) fautif(s) retiré(s)" in a for a in res.rapport["avertissements"]))
        v = lire_json(os.path.join(res.dossier, "technique", "graine_1", "vias_couture.json"))
        self.assertEqual(len(v["vias"]), 1)

    def test_options_sans(self):
        carte = self.carte()
        res = self.lancer(carte, "--essais", 1, "--sans-pcbdoc", scenario={})
        self.assertEqual(res.code, 0, res.erreur)
        self.assertIsNone(res.rapport["livrable"])
        self.assertEqual(sorted(os.listdir(res.dossier)), ["rapport.txt", "technique"])
        self.assertIn("carte produite non demandée", self.rapport_txt(res))
        res = self.lancer(carte, "--essais", 1, "--sans-serigraphie", "--sans-couture", scenario={})
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual([a for a in self.outils.appels if a[0] == "generer"], [("generer", 1, True, False)])
        self.assertFalse(os.path.exists(os.path.join(res.dossier, "technique", "graine_1", "vias_couture.json")))
        self.assertIsNone(res.rapport["couture"])

    def test_analyser(self):
        carte = self.carte()
        res = self.lancer(carte, "--analyser", scenario={})
        self.assertEqual(res.code, 0, res.erreur)
        self.assertEqual(res.rapport["graines"], [])
        self.assertFalse([n for n in os.listdir(os.path.join(res.dossier, "technique")) if n.startswith("graine_")])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "vivants.txt")))
        texte = self.rapport_txt(res)
        for x in ("DÉDUCTIONS", "Suggestions", "ALERTE", "PRÉVISIONS", "mémoire prévue", "durée prévue"):
            self.assertIn(x, texte)
        res = self.lancer(carte, "--analyser", gabarit_utilisable=False)
        self.assertEqual(res.code, 2)
        self.assertIn("aucun via modèle", res.erreur)

    def test_init(self):
        import formats
        import placeur
        import io
        carte = self.carte()
        outils = _doublure_outils()()
        sortie = io.StringIO()
        self.assertEqual(placeur.main(["--init", carte], outils=outils, flux=sortie, flux_erreur=io.StringIO()), 0)
        ini = os.path.join(self.tmp, "deux_couches.ini")
        cj = os.path.join(self.tmp, "deux_couches_contraintes.json")
        modele = lire_json(cj)
        self.assertEqual(formats.valider_contraintes(modele), [])
        self.assertTrue(set(formats.CLES_CONTRAINTES) <= set(modele["_aide"]))
        self.assertEqual(modele["fixer_verrouilles"], True)
        self.assertIn("U10", modele["_positions"])
        with open(ini, encoding="utf-8-sig") as f:
            texte = f.read()
        self.assertRegex(texte, r"(?m)^fixes = U10, .*M1")
        self.assertRegex(texte, r"(?m)^fichier = deux_couches\.kicad_pcb")
        def octets():
            with open(ini, "rb") as f1, open(cj, "rb") as f2:
                return f1.read(), f2.read()
        avant = octets()
        sortie = io.StringIO()
        self.assertEqual(placeur.main(["--init", carte], outils=outils, flux=sortie, flux_erreur=io.StringIO()), 0)
        self.assertEqual(octets(), avant)
        self.assertEqual(sortie.getvalue().count("rien n'est écrasé"), 2)

# ================================================================================================ É5 : reprise, environnement

def _ouvrir_sans_partage(chemin: str):
    """Ouvre `chemin` sans partage (comme Altium tient une carte ouverte) ; rend une fonction qui le referme."""
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    h = k.CreateFileW(chemin, 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)   # lecture-écriture, partage 0
    if h in (None, w.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "CreateFileW")
    return lambda: k.CloseHandle(h)


class TestRepriseEtEnvironnement(unittest.TestCase):
    """§5.3 : reprise, contrôles préalables, table des erreurs d'environnement, dossier de sortie, version."""

    setUp = TestOrchestrateur.setUp
    lancer = TestOrchestrateur.lancer
    assertRapportsValides = TestOrchestrateur.assertRapportsValides

    # ------------------------------------------------------------------------------------- reprise
    def _calcul_inacheve(self):
        """Calcul complet de 3 graines, puis rendu inachevé : graine 2 arrêtée (fin arret_demande), graine 3 sans
        stats.json, rapport termine: false."""
        res = self.lancer({}, "--essais", 3, "--processeurs", 1, "--garder-graines", "toutes")
        self.assertEqual(res.code, 0, res.erreur)
        tech = os.path.join(res.dossier, "technique")
        s2 = res.stats(2)
        s2["fin"] = "arret_demande"
        chemins.ecrire_json(os.path.join(tech, "graine_2", "stats.json"), s2)
        os.remove(os.path.join(tech, "graine_3", "stats.json"))
        rapport = dict(res.rapport, termine=False, fin="arret_demande", code=3)
        chemins.ecrire_json(os.path.join(tech, "rapport.json"), rapport)
        return res

    def reprendre(self, dossier, *args, scenario=None):
        donnees = donnees_completes(self.tmp, {} if scenario is None else scenario)
        with Environnement(["--faux-journal", self.journal]) as env:
            res = executer_placeur(["--donnees", donnees, "--reprendre", dossier, "--essais", 3, "--processeurs", 1,
                                    "--garder-graines", "toutes", *args])
        res.validations = env.validations
        res.dossier = dossier
        chemin = os.path.join(dossier, "technique", "rapport.json")
        res.rapport = lire_json(chemin) if os.path.exists(chemin) else None
        return res

    def test_reprise(self):
        res = self._calcul_inacheve()
        pid_1 = res.stats(1)["_faux"]["pid"]
        lances_avant = len(lignes_journal(self.journal))
        with open(os.path.join(res.dossier, "technique", "journal.txt"), encoding="utf-8") as f:
            journal_avant = f.read()
        b = self.reprendre(res.dossier)
        self.assertEqual(b.code, 0, b.erreur)
        self.assertRapportsValides(b)
        self.assertEqual(b.stats(1)["_faux"]["pid"], pid_1)                 # graine 1 reprise telle quelle
        relances = [int(l[1]) for l in lignes_journal(self.journal)[lances_avant:] if l[0] == "debut"]
        self.assertEqual(sorted(relances), [2, 3])                          # les autres relancées
        self.assertEqual(b.stats(2)["fin"], "normale")
        self.assertTrue(b.rapport["termine"])
        self.assertEqual(sorted(e["graine"] for e in b.rapport["graines"]), [1, 2, 3])
        with open(os.path.join(res.dossier, "technique", "journal.txt"), encoding="utf-8") as f:
            journal = f.read()
        self.assertTrue(journal.startswith(journal_avant))                  # journal ouvert en ajout
        self.assertIn("graine 1 reprise telle quelle", journal)
        self.assertEqual(len(dossiers_calcul(self.parent)), 1)              # aucun nouveau dossier

    def test_reprise_sans_dossier_dernier_inacheve(self):
        import placeur
        res = self._calcul_inacheve()
        donnees = os.path.join(self.tmp, "donnees_placeur.json")
        self.assertEqual(placeur.calcul_inacheve(donnees, self.parent), res.dossier)
        with Environnement(["--faux-journal", self.journal]):
            b = executer_placeur(["--donnees", donnees, "--sortie", self.parent, "--reprendre", "--essais", 3,
                                  "--processeurs", 1])
        self.assertEqual(b.code, 0, b.erreur)
        self.assertIsNone(placeur.calcul_inacheve(donnees, self.parent))     # achevé : plus rien à reprendre

    def test_reprise_entrees_ou_parametres_changes(self):
        res = self._calcul_inacheve()
        tech = os.path.join(res.dossier, "technique")
        avant = {n: sha256(os.path.join(tech, n)) for n in ("rapport.json", "parametres_moteur.json",
                                                           "donnees_placeur.json")}
        for args, scenario in ((["--qualite", "rapide"], None), ([], {"1": {"cout": 1.0}})):
            b = self.reprendre(res.dossier, *args, scenario=scenario)
            self.assertEqual(b.code, 2, b.erreur)
            self.assertIn("les entrées ont changé : lancer un nouveau calcul", b.erreur)
            self.assertEqual({n: sha256(os.path.join(tech, n)) for n in avant}, avant)   # rendus tels quels
            self.assertFalse(b.rapport["termine"])

    def test_reprise_refusee_sans_rapport_inacheve(self):
        vide = os.path.join(self.tmp, "vide")
        os.makedirs(os.path.join(vide, "technique"))
        b = self.reprendre(vide)
        self.assertEqual(b.code, 2)
        self.assertIn("ne contient pas de calcul inachevé", b.erreur)
        res = self.lancer({}, "--essais", 1)                               # calcul achevé : refus aussi
        b = self.reprendre(res.dossier)
        self.assertEqual(b.code, 2)
        self.assertIn("ne contient pas de calcul inachevé", b.erreur)

    # ------------------------------------------------------------------------------------- environnement
    def test_dossier_protege_erreur_5(self):
        from unittest import mock
        vrai = os.mkdir
        cible = os.path.join(self.tmp, "protege")

        def refus(chemin, *a, **kw):
            if est_sous_test(chemin, cible):
                raise PermissionError(13, "Accès refusé", chemin, 5)
            return vrai(chemin, *a, **kw)
        with mock.patch("os.mkdir", refus):
            res = executer_placeur(["--donnees", donnees_completes(self.tmp, {}), "--sortie", cible, "--essais", 0])
        self.assertEqual(res.code, 2)
        self.assertIn("Windows interdit l'écriture dans ce dossier.", res.erreur)

    def test_chemin_trop_long(self):
        from unittest import mock
        donnees = donnees_completes(self.tmp, {})
        long_ = os.path.join(self.tmp, "x" * 250)
        res = executer_placeur(["--donnees", donnees, "--sortie", long_, "--essais", 0])
        self.assertEqual(res.code, 2)
        n = len(os.path.join(long_, "donnees_placeur_AAAAMMJJ-HHMMSS"))
        self.assertIn(f"Chemin de sortie trop long ({n} caractères).", res.erreur)
        self.assertFalse(os.path.exists(long_))
        # > 200 : averti
        reste = 205 - len(os.path.join(self.tmp, "")) - len("\\donnees_placeur_AAAAMMJJ-HHMMSS")
        parent = os.path.join(self.tmp, "p" * reste)
        with Environnement(["--faux-journal", self.journal]):
            res = executer_placeur(["--donnees", donnees, "--sortie", parent, "--essais", 0])
        self.assertIn("Chemin de sortie trop long (205 caractères).", res.sortie)
        # Windows 206 en cours de création
        vrai = os.mkdir

        def trop_long(chemin, *a, **kw):
            if os.path.basename(chemin).startswith("donnees_placeur_"):
                raise OSError(errno.ENAMETOOLONG, "nom trop long", chemin, 206)
            return vrai(chemin, *a, **kw)
        with mock.patch("os.mkdir", trop_long):
            res = executer_placeur(["--donnees", donnees, "--sortie", self.parent, "--essais", 0])
        self.assertEqual(res.code, 2)
        self.assertRegex(res.erreur, r"Chemin de sortie trop long \(\d+ caractères\)\.")

    def test_disque_plein(self):
        from unittest import mock
        import placeur
        donnees = donnees_completes(self.tmp, {})
        with mock.patch.object(placeur, "espace_libre_go", lambda chemin: 0.5):
            res = executer_placeur(["--donnees", donnees, "--sortie", self.parent, "--essais", 1])
        self.assertEqual(res.code, 2)
        self.assertIn("Espace disque insuffisant (0,5 Go libres).", res.erreur)
        plein = _doublures()

        class ControlesDisquePlein(plein):
            def executer(self, *a, **kw):
                raise OSError(errno.ENOSPC, "Il n'y a pas assez d'espace sur le disque", None, 112)
        res = self.lancer({}, "--essais", 2, controles=ControlesDisquePlein())
        self.assertEqual(res.code, 2, res.erreur)
        self.assertRegex(res.erreur, r"Espace disque insuffisant \([0-9,?]+ Go libres\)\.")

    def test_carte_verrouillee_erreur_32(self):
        if os.name != "nt":
            self.skipTest("partage de fichiers Windows")
        carte = os.path.join(self.tmp, "verrouillee.kicad_pcb")
        shutil.copyfile(os.path.join(ICI, "corpus", "cartes", "deux_couches", "deux_couches.kicad_pcb"), carte)
        fermer = _ouvrir_sans_partage(carte)
        try:
            import io
            import placeur
            erreur = io.StringIO()
            with Environnement():
                code = placeur.main([carte, "--sortie", self.parent], controles=_doublures()(),
                                    outils=_doublure_outils()(), flux=io.StringIO(), flux_erreur=erreur)
        finally:
            fermer()
        self.assertEqual(code, 2)
        self.assertIn("La carte est ouverte dans Altium et verrouillée.", erreur.getvalue())

    def test_moteur_absent_ou_bloque(self):
        from unittest import mock
        donnees = donnees_completes(self.tmp, {})
        vide = os.path.join(self.tmp, "moteurs_vides")
        os.makedirs(vide)
        res = executer_placeur(["--donnees", donnees, "--sortie", self.parent, "--moteurs", vide, "--essais", 1])
        self.assertEqual(res.code, 2)
        self.assertIn("Moteur absent, peut-être mis en quarantaine par l'antivirus.", res.erreur)
        with Environnement(), mock.patch.object(chemins, "executer",
                                                side_effect=PermissionError(13, "Accès refusé", None, 5)):
            res = executer_placeur(["--donnees", donnees, "--sortie", self.parent, "--essais", 1])
        self.assertEqual(res.code, 2)
        self.assertIn("Moteur absent, peut-être mis en quarantaine par l'antivirus.", res.erreur)

    def test_memoire_insuffisante(self):
        from unittest import mock
        with mock.patch.object(chemins, "memoire_disponible_mo", lambda: 520):
            res = self.lancer({}, "--essais", 2)
        self.assertEqual(res.code, 2)
        self.assertIn("La carte demande environ 50 Mo par essai, 520 Mo sont libres.", res.erreur)

    def test_onedrive_et_batterie_avertis(self):
        from unittest import mock
        self.parent = os.path.join(self.tmp, "OneDrive - Essai", "res")
        with mock.patch.object(chemins, "sur_batterie", lambda: True):
            res = self.lancer({}, "--essais", 1)
        self.assertEqual(res.code, 0, res.erreur)
        for m in ("Ce dossier est synchronisé : les résultats seront envoyés en ligne.", "Le PC est sur batterie."):
            self.assertIn(m, res.rapport["avertissements"])
            self.assertIn(m, res.sortie)

    def test_version_du_moteur_differente(self):
        res = self.lancer({}, "--essais", 1, prefixe=["--faux-version", "0.0.1"])
        self.assertEqual(res.code, 2)
        self.assertIn("différente de celle du lanceur", res.erreur)

    def test_aucun_cargo_essais_0_ni_analyser(self):
        from unittest import mock
        import io
        import placeur
        appels = []
        with mock.patch.object(chemins, "construire_moteur", lambda *a, **kw: appels.append(a)):
            executer_placeur(["--donnees", donnees_completes(self.tmp, {}), "--sortie", self.parent, "--essais", 0])
            carte = os.path.join(self.tmp, "c.kicad_pcb")
            shutil.copyfile(os.path.join(ICI, "corpus", "cartes", "deux_couches", "deux_couches.kicad_pcb"), carte)
            placeur.main([carte, "--sortie", self.parent, "--analyser"], controles=_doublures()(),
                         outils=_doublure_outils()(), flux=io.StringIO(), flux_erreur=io.StringIO())
        self.assertEqual(appels, [])

    def test_sortie_par_defaut_et_replis(self):
        from unittest import mock
        import placeur
        donnees = donnees_completes(self.tmp, {})
        docs, local = os.path.join(self.tmp, "Documents"), os.path.join(self.tmp, "Local")
        connus = {"documents": docs, "localappdata": local}
        vrai_connu = chemins.dossier_connu
        faux_temp = os.path.join(self.tmp, "faux_temp")
        patchs = [mock.patch.object(chemins, "dossier_connu", lambda n: connus.get(n) or vrai_connu(n)),
                  mock.patch.object(chemins, "dossier_temporaire", lambda: faux_temp)]
        for p in patchs:
            p.start()
        try:
            with Environnement():
                res = executer_placeur(["--donnees", donnees, "--essais", 0])
            self.assertEqual(len(dossiers_calcul(os.path.join(self.tmp, "donnees_placeur_resultats"))), 1, res.erreur)
            vrai_mkdir = os.mkdir

            def refus(chemin, *a, **kw):
                if "donnees_placeur_resultats" in chemin or (refus.docs and est_sous_test(chemin, docs)):
                    raise PermissionError(13, "Accès refusé", chemin, 5)
                return vrai_mkdir(chemin, *a, **kw)
            refus.docs = False
            with Environnement(), mock.patch("os.mkdir", refus):
                shutil.rmtree(os.path.join(self.tmp, "donnees_placeur_resultats"))
                res = executer_placeur(["--donnees", donnees, "--essais", 0])
                self.assertEqual(len(dossiers_calcul(os.path.join(docs, "PlaceurRouteur"))), 1, res.erreur)
                refus.docs = True
                res = executer_placeur(["--donnees", donnees, "--essais", 0])
                self.assertEqual(len(dossiers_calcul(os.path.join(local, "PlaceurRouteur", "resultats"))), 1,
                                 res.erreur)
            # jamais le dossier temporaire ni le dossier du paquet
            dans_temp = donnees_completes(os.makedirs(faux_temp) or faux_temp, {})
            self.assertNotIn(os.path.join(faux_temp, "donnees_placeur_resultats"),
                             placeur.parents_par_defaut(dans_temp, "donnees_placeur"))
            with mock.patch.object(placeur, "dossier_paquet", lambda: self.tmp):
                self.assertEqual(placeur.parents_par_defaut(donnees, "donnees_placeur"), [])
                res = executer_placeur(["--donnees", donnees, "--sortie", self.parent, "--essais", 0])
                self.assertEqual(res.code, 2)
                self.assertIn("dossier du paquet", res.erreur)
        finally:
            for p in reversed(patchs):
                p.stop()

    # ------------------------------------------------------------------------------------- auto-test, diagnostic
    def _auto_test(self, absent=None):
        from unittest import mock
        import io
        import placeur

        def commande(nom, *a, **kw):
            if nom == absent:
                raise chemins.MoteurAbsent(f"{chemins.MESSAGE_MOTEUR_ABSENT} ({nom})")
            return FAUX_MOTEUR + ["--faux-nom", nom] + [str(x) for x in a]
        sortie = io.StringIO()
        with mock.patch.object(chemins, "commande_moteur", commande):
            code = placeur.main(["--auto-test"], flux=sortie, flux_erreur=io.StringIO())
        return code, sortie.getvalue()

    def test_auto_test(self):
        code, texte = self._auto_test()
        self.assertEqual(code, 0, texte)
        self.assertIn("auto-test : réussi", texte)
        code, texte = self._auto_test(absent="serigraphie")
        self.assertNotEqual(code, 0)
        self.assertIn("moteur absent, peut-être mis en quarantaine", texte.lower())

    def test_diagnostic(self):
        res = executer_placeur(["--diagnostic"])
        self.assertEqual(res.code, 0, res.erreur)
        for morceau in ("placeur ", "système : ", "python : ", "moteur placeur_routage", "moteur serigraphie",
                        "mémoire disponible", "processeurs logiques", "fichiers .ini lus"):
            self.assertIn(morceau, res.sortie)
        self.assertEqual(chemins.chercher_motifs(res.sortie, lecteur=True), [])


class TestFusionCampagne(unittest.TestCase):
    """Statistiques de l'avenant 2.2, A.7 (ci_fusion.py), fusion de sorties de placeur.py, campagne locale
    (campagnes.py) et aide de la CI (ci_graines.py)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_campagne_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    @staticmethod
    def campagne(n_valides, cout, n=40, debut=1):
        return [{"graine": debut + i, "valide": i < n_valides, "cout_commun": cout if i < n_valides else None}
                for i in range(n)]

    def test_table_de_a7(self):
        import ci_fusion
        table = {6: (0.5, 0.55, 0.6, 0.62), 5: (0.65, 0.7, 0.75, 0.8), 4: (0.82, 0.85, 0.88), 3: (0.9, 0.92),
                 2: (0.95,)}
        for m, taux in table.items():
            for p in taux:
                self.assertEqual(ci_fusion.marge_ni(p, 40, 0.90), m, p)
        # définition : m est le plus petit entier qui atteint 0,90
        self.assertGreaterEqual(ci_fusion.proba_ni(6, 40, 0.6), 0.90)
        self.assertLess(ci_fusion.proba_ni(5, 40, 0.6), 0.90)

    def test_ni_aux_bornes(self):
        import ci_fusion
        ref = self.campagne(24, 10000.0)                      # taux 0,6 : m = 6
        c = ci_fusion.comparer(self.campagne(18, 10400.0), ref, 0.04)
        self.assertEqual((c["m"], c["validite_ok"], c["cout_ok"], c["accepte"]), (6, True, True, True))
        c = ci_fusion.comparer(self.campagne(17, 10400.0), ref, 0.04)
        self.assertEqual((c["validite_ok"], c["accepte"]), (False, False))
        c = ci_fusion.comparer(self.campagne(18, 10400.02), ref, 0.04)
        self.assertEqual((c["validite_ok"], c["cout_ok"], c["accepte"]), (True, False, False))
        c = ci_fusion.comparer(self.campagne(30, 9000.0, n=39), self.campagne(24, 10000.0, n=39), 0.04)
        self.assertFalse(c["accepte"])                        # moins de 40 graines appariées
        c = ci_fusion.comparer(self.campagne(30, 9000.0, debut=2), ref, 0.04)
        self.assertEqual((c["appariee"], c["graines_seulement_nouveau"], c["accepte"]), (False, [41], False))

    def test_wilson_clopper_pearson_et_p8(self):
        import ci_fusion
        import placeur
        self.assertEqual([round(x, 3) for x in placeur.wilson(12, 20)], [0.387, 0.781])
        self.assertTrue(ci_fusion.plus_de_80(87, 100))
        self.assertFalse(ci_fusion.plus_de_80(86, 100))
        graines = self.campagne(12, 11000.0, n=20)
        graines[0]["cout_commun"] = 10090.64
        s = placeur.statistiques(graines, 10328.5, 8)
        self.assertAlmostEqual(s["p_campagne"], 1 - (19 / 20) ** 8, places=6)
        # rééchantillonnage avec remise, énuméré : 3 graines dont une bonne, campagnes de 2 graines
        import itertools
        petites = [{"graine": 1, "valide": True, "cout_commun": 1.0}, {"graine": 2, "valide": True, "cout_commun": 5.0},
                   {"graine": 3, "valide": False, "cout_commun": None}]
        bons = sum(1 for t in itertools.product(petites, repeat=2)
                   if any(g["valide"] and g["cout_commun"] <= 2.0 for g in t))
        self.assertAlmostEqual(placeur.statistiques(petites, 2.0, 2)["p_campagne"], bons / 9, places=6)

    def sortie_placeur(self, nom, graine, scenario=None, *args):
        os.makedirs(os.path.join(self.tmp, f"d_{nom}"), exist_ok=True)
        donnees = donnees_completes(os.path.join(self.tmp, f"d_{nom}"), scenario or {})
        parent = os.path.join(self.tmp, nom)
        with Environnement() as env:
            res = executer_placeur(["--donnees", donnees, "--sortie", parent, "--graine-depart", graine,
                                    "--essais", 1, *args])
        self.assertIn(res.code, (0, 1), res.erreur)
        self.assertTrue(env.validations)
        return parent, dossiers_calcul(parent)[-1], donnees

    def test_fusion_de_trois_sorties(self):
        import ci_fusion
        import formats
        import io
        parents = [self.sortie_placeur(f"m{g}", g, {str(g): {"type": "invalide"}} if g == 3 else None)[0]
                   for g in (1, 2, 3)]
        sortie = os.path.join(self.tmp, "fusion")
        flux = io.StringIO()
        code = ci_fusion.main([*parents, "--sortie", sortie, "--reference-cout", "10001,5", "--comparer", parents[0]],
                              flux)
        self.assertEqual(code, 0)
        r = lire_json(os.path.join(sortie, "rapport.json"))
        self.assertEqual(formats.valider_rapport(r), [])
        self.assertEqual([g["graine"] for g in r["graines"]], [1, 2, 3])
        self.assertEqual([g["valide"] for g in r["graines"]], [True, True, False])
        self.assertEqual((r["meilleure"], r["incoherences"], len(r["machines"])), (1, 0, 3))
        s = r["statistiques"]
        self.assertEqual((s["graines_mesurees"], s["graines_valides"], s["meilleur_cout"]), (3, 2, 10001.0))
        self.assertAlmostEqual(s["p_campagne"], 1 - (2 / 3) ** 8, places=6)
        for cle in ("wilson_95", "calcul_par_graine_valide_s", "clopper_pearson_95_bas", "durees_phases",
                    "duree_prevue_sur_reelle"):
            self.assertIn(cle, s)
        self.assertEqual(len(s["duree_prevue_sur_reelle"]["machines"]), 3)
        self.assertTrue(s["durees_phases"]["phases"])
        self.assertEqual(r["comparaison"]["graines_communes"], 1)
        self.assertIn("Wilson 95 %", flux.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(sortie, "resume.md")))
        self.assertEqual(chemins.chercher_motifs(json.dumps(r, ensure_ascii=False), lecteur=True), [])
        self.assertEqual(ci_fusion.main([os.path.join(self.tmp, "vide"), "--sortie", sortie]), 2)

    def test_anciens_resultats(self):
        import ci_fusion
        run = os.path.join(self.tmp, "run_9")
        lignes = ["source,graine,valide,cout,duree_s,cause", "rev4 (sorties/conjoint),,0,10328.52,1,",
                  "campagne,3001,1,11000.5,2000,", "campagne,3002,0,,3000,echecs apres finition"]
        os.makedirs(run)
        with open(os.path.join(run, "campagnes.csv"), "w", encoding="utf-8") as f:
            f.write("\n".join(lignes) + "\n")
        for g, t in ((3001, 2100.0), (3002, 3100.0)):
            os.makedirs(os.path.join(run, f"c_{g}"))
            chemins.ecrire_json(os.path.join(run, f"c_{g}", "stats.json"), {
                "graine": g, "temps_s": t, "temps_phases_s": {"recuit": 100.0, "routage detaille": 50.0,
                                                            "boucle 1": 10.0, "boucle 2": 20.0}})
        r = ci_fusion.campagne_ancienne([run], 10328.5, None)
        s = r["statistiques"]
        self.assertEqual((s["graines_mesurees"], s["graines_valides"], s["mediane_cout"]), (2, 1, 11000.5))
        self.assertEqual(s["calcul_par_graine_valide_s"], 5200.0)
        self.assertEqual(s["durees_phases"]["phases"]["boucle"]["total_s"], 60.0)
        self.assertEqual(s["durees_phases"]["phases"]["routage_detaille"]["total_s"], 100.0)
        self.assertEqual(r["references"][0]["cout_commun"], 10328.52)
        self.assertIn("campagnes.csv:cout", r["correspondance"])

    def test_campagne_locale_avec_reference(self):
        import campagnes
        import formats
        import io
        _, calcul, donnees = self.sortie_placeur("ref", 9, None, "--garder-graines", "toutes")
        sortie = os.path.join(self.tmp, "campagne")
        flux, erreur = io.StringIO(), io.StringIO()
        with Environnement() as env:
            code = campagnes.main(["--donnees", donnees, "--graines", "5-6", "--sortie", sortie, "--qualite", "rapide",
                                   "--reference", f"rev={os.path.join(calcul, 'technique', 'graine_9')}"],
                                  controles=_doublures()(), flux=flux, flux_erreur=erreur)
        self.assertEqual(code, 0, erreur.getvalue())
        self.assertTrue(all(e == [] for e in env.validations))
        r = lire_json(os.path.join(sortie, "campagne", "rapport.json"))
        self.assertEqual(formats.valider_rapport(r), [])
        self.assertEqual([g["graine"] for g in r["graines"]], [5, 6])
        ref = r["references_evaluees"][0]
        self.assertEqual((ref["nom"], ref["valide"], ref["cout_commun"]), ("rev", True, 10009.0))
        self.assertEqual(r["statistiques"]["reference_cout"], 10009.0)
        self.assertEqual(r["statistiques"]["p_campagne"], 1.0)
        self.assertTrue(os.path.isfile(os.path.join(sortie, "references", "rev", "drc_strict.json")))
        self.assertEqual(campagnes.suites([1, 2, 3, 7, 9, 10]), [(1, 3), (7, 1), (9, 2)])

    def test_ci_graines(self):
        import ci_graines
        import io
        flux = io.StringIO()
        self.assertEqual(ci_graines.main(["graines"], flux), 0)
        m = json.loads(flux.getvalue())["include"]
        self.assertLessEqual(len(m), 256)
        cfg = ci_graines.lire_config()
        self.assertEqual(ci_graines.lire_graines(cfg["graines"]), list(range(3001, 3021)) + list(range(4001, 4021)))
        self.assertEqual({e["jeu"] for e in m}, {"nominal", "compatibilite"})
        self.assertEqual(len(m), 80)
        trop = os.path.join(self.tmp, "trop.json")
        chemins.ecrire_json(trop, dict(cfg, graines=["1-300"]))
        self.assertEqual(ci_graines.main(["graines", "--fichier", trop], io.StringIO()), 2)
        flux = io.StringIO()
        self.assertEqual(ci_graines.main(["graines", "--nuit", "--github"], flux), 0)
        self.assertTrue(flux.getvalue().startswith("matrice="))
        cartes = ci_graines.cartes_chaine(os.path.join(ICI, "corpus"))
        self.assertTrue(cartes)
        self.assertTrue(all(c["carte"].endswith(".kicad_pcb") and c["graines_valides_min"] >= 1 for c in cartes))
        # controler : une graine valide, aucune incohérence, .kicad_pcb produit et relu
        _, calcul, _ = self.sortie_placeur("ctl", 4)
        fr = os.path.join(calcul, "technique", "rapport.json")
        r = lire_json(fr)
        r["livrable"] = {"fichier": "carte.kicad_pcb", "type": "complet", "erreurs": [], "anomalies": []}
        chemins.ecrire_json(fr, r)
        self.assertEqual(ci_graines.controler(calcul)[0], 1)              # livrable absent
        with open(os.path.join(calcul, "carte.kicad_pcb"), "w", encoding="utf-8") as f:
            f.write("(kicad_pcb (version")
        self.assertEqual(ci_graines.controler(calcul)[0], 1)              # illisible à la relecture
        self.assertEqual(ci_graines.controler(calcul, relire=lambda c: None)[0], 0)
        self.assertEqual(ci_graines.controler(calcul, 2, relire=lambda c: None)[0], 1)
        r["incoherences"] = 1
        chemins.ecrire_json(fr, r)
        self.assertEqual(ci_graines.controler(calcul, relire=lambda c: None)[0], 1)
        self.assertEqual(ci_graines.controler(os.path.join(self.tmp, "rien"))[0], 2)


class TestFluxCI(unittest.TestCase):
    """placeur.yml (§7.6, A.8) : analyse structurelle, sans bibliothèque YAML."""

    @classmethod
    def setUpClass(cls):
        racine = os.path.dirname(os.path.dirname(chemins.OUTILS))
        cls.texte = _lire(os.path.join(racine, ".github", "workflows", "placeur.yml"))

    def test_jobs_et_declencheurs(self):
        t = self.texte
        for job in ("rust", "python", "contrat", "synthetiques", "arret_posix", "liste", "campagne_metroid",
                    "campagne_corpus", "fusion"):
            self.assertRegex(t, rf"(?m)^  {job}:$", job)
        for morceau in ("push:", "'PCB_V2/outils/**'", "workflow_dispatch:", "schedule:", "ubuntu-24.04",
                        "windows-2022", "placeur_routage serigraphie", "--locked", "corpus/comparer_validite.py",
                        "--qualite rapide --essais 2", "ci_graines.py controler", "ci_graines.py graines",
                        "--qualite normal --sans-pcbdoc", "--essais 1", "ci_fusion.py", "ci-resultats-placeur",
                        "vars.CAMPAGNE_NUIT == 'oui'", "requirements.txt", "--comparer"):
            self.assertIn(morceau, t)
        for interdit in ("campagnes.py", "numpy", "matplotlib", "scipy", "--bloc-fige"):
            self.assertNotIn(interdit, t)
        # la nuit : seulement si la variable du dépôt le permet (décision §8.9-6)
        bloc = t[t.index("  campagne_metroid:"):t.index("  campagne_corpus:")]
        self.assertIn("github.event_name == 'schedule' && vars.CAMPAGNE_NUIT == 'oui'", bloc)

    def test_sources_sans_reliquat(self):
        motif = re.compile(r"bloc.fige|chute_8v|sw_longueur|numpy|scipy|matplotlib|PLACEUR_BLOC_LIBRE|os\.environ")
        for nom in ("campagnes.py", "ci_fusion.py", "ci_graines.py"):
            self.assertIsNone(motif.search(_lire(os.path.join(ICI, nom))), nom)


def est_sous_test(chemin: str, racine: str) -> bool:
    c, r = os.path.normcase(os.path.abspath(chemin)), os.path.normcase(os.path.abspath(racine))
    return c == r or c.startswith(r + os.sep)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--faux-moteur":
        for flux in (sys.stdout, sys.stderr):        # comme le moteur Rust : UTF-8, même redirigé (§0.2-10)
            flux.reconfigure(encoding="utf-8", errors="replace")
        sys.exit(faux_moteur(sys.argv[2:]))
    if len(sys.argv) > 3 and sys.argv[1] == "--orchestrateur-faux" and sys.argv[3] == "--":
        # Orchestrateur dans un processus à part, avec le faux moteur (journal sys.argv[2]) et les doublures.
        import placeur
        _prefixe = ["--faux-journal", sys.argv[2]]
        chemins.commande_moteur = lambda nom, *a, **kw: FAUX_MOTEUR + _prefixe + [str(x) for x in a]
        sys.exit(placeur.main(sys.argv[4:], controles=_doublures()()))
    unittest.main()
