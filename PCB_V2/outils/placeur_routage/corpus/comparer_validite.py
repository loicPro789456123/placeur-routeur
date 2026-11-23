#!/usr/bin/env python3
"""Comparaison des implémentations de la validité de référence (contrat §7.4, lot E).

    python comparer_validite.py --cas DOSSIER [--moteur EXE] [--cout-rust EXE] [--modules-python DOSSIER]
                                [--mode strict|altium|les_deux] [--rapport F]
    python comparer_validite.py --graine DOSSIER --donnees F [--moteur EXE] [--cout-rust EXE]
                                [--modules-python DOSSIER] [--rapport F]

- ``--cas`` : dossier des cas du corpus (``corpus/validite``, un sous-dossier par cas, décrit par ``cas.json``) ;
  les attendus écrits à la main sont comparés à chaque implémentation fournie.
- ``--graine`` : dossier d'une graine réelle (``placements_routage.json``, ``routage_detaille.json``, et
  ``vias_couture.json`` s'il existe) avec ``--donnees`` ; sans attendu, les implémentations sont comparées entre elles.
- ``--mode strict|altium|les_deux`` : modes comparés (défaut ``les_deux``).
- ``--moteur EXE`` : tout exécutable qui offre ``verifier`` avec les options de §5.1 (``placeur_routage`` du lot A ou
  le binaire d'essai ``validite_verifier`` du lot E) ; sa sortie porte le champ ``placement``.
- ``--cout-rust EXE`` : binaire d'essai ``validite_verifier`` (option ``--mesures``, ``mesures/2`` calculé par
  ``validite::cout_commun``).
- ``--modules-python DOSSIER`` : dossier des modules du lot C (``drc_maison.verifier``,
  ``verifier_placement.verifier``, ``mesures.mesurer``, §5.2).

Champs comparés : verdict (``valide``, ``liaisons_ouvertes``, ``violations_par_type``, ``derogations``) dans les deux
modes, placement (``valide``, ``violations_par_type``), coût commun (à 1e-6 près).

Codes de sortie (§1.4) : 0 aucun écart ; 1 écarts (rapport écrit) ; 2 entrée ou environnement invalide (dossier,
exécutable absent, module sans ``verifier``) ; 4 erreur interne. Les messages ne citent que des noms de fichiers,
jamais un dossier (§0.3).
"""

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile

FORMAT_RAPPORT = "comparaison_validite/1"
TOLERANCE = 1e-6
CHAMPS_VERDICT = ("valide", "liaisons_ouvertes", "violations_par_type", "derogations")
CHAMPS_PLACEMENT = ("valide", "violations_par_type")
MODES = ("strict", "altium")
DELAI_S = 600


class ErreurEntree(Exception):
    """Entrée ou environnement invalide (code 2)."""


# --- Fonctions pures --------------------------------------------------------------------------------------------

def egal(a, b, tol=TOLERANCE):
    """Égalité de deux valeurs JSON, les nombres (non booléens) à ``tol`` près."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=0.0, abs_tol=tol)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(egal(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(egal(x, y, tol) for x, y in zip(a, b))
    return a == b


def ecarts_champs(cas, quoi, sources, va, vb, champs):
    """Écarts entre deux sorties (``va`` de ``sources[0]``, ``vb`` de ``sources[1]``) sur ``champs``."""
    ecarts = []
    for champ in champs:
        a, b = va.get(champ), vb.get(champ)
        if not egal(a, b):
            ecarts.append({"cas": cas, "quoi": quoi, "champ": champ, "sources": list(sources), "valeurs": [a, b]})
    return ecarts


def ecart_cout(cas, sources, ca, cb):
    """Écart de coût commun (``mesures/2``) au-delà de 1e-6."""
    a, b = ca.get("cout_commun"), cb.get("cout_commun")
    if egal(a, b):
        return []
    return [{"cas": cas, "quoi": "cout", "champ": "cout_commun", "sources": list(sources), "valeurs": [a, b]}]


def comparer_sorties(cas, sorties, modes=MODES):
    """Compare deux à deux toutes les sources d'un cas.

    ``sorties`` : {source: {"strict": verdict|None, "altium": verdict|None, "placement": controle|None,
    "cout": mesures|None}} ; une valeur ``None`` (source muette sur ce point) n'est pas comparée.
    Rend (écarts, nombre de comparaisons)."""
    ecarts, n = [], 0
    noms = list(sorties)
    for i, a in enumerate(noms):
        for b in noms[i + 1:]:
            sa, sb = sorties[a], sorties[b]
            for mode in modes:
                if sa.get(mode) is not None and sb.get(mode) is not None:
                    n += 1
                    ecarts += ecarts_champs(cas, "verdict " + mode, (a, b), sa[mode], sb[mode], CHAMPS_VERDICT)
            if sa.get("placement") is not None and sb.get("placement") is not None:
                n += 1
                ecarts += ecarts_champs(cas, "placement", (a, b), sa["placement"], sb["placement"], CHAMPS_PLACEMENT)
            if sa.get("cout") is not None and sb.get("cout") is not None:
                n += 1
                ecarts += ecart_cout(cas, (a, b), sa["cout"], sb["cout"])
    return ecarts, n


def rapport(nb_cas, ecarts, comparaisons, sources):
    return {"format": FORMAT_RAPPORT, "cas": nb_cas, "sources": sorted(sources), "comparaisons": comparaisons,
            "nombre_ecarts": len(ecarts), "ecarts": ecarts}


# --- Entrées ----------------------------------------------------------------------------------------------------

def lire_json(chemin):
    try:
        with open(chemin, encoding="utf-8-sig") as f:
            return json.load(f)
    except OSError as e:
        raise ErreurEntree(f"{os.path.basename(chemin)} : lecture impossible ({e.strerror or type(e).__name__})")
    except ValueError as e:
        raise ErreurEntree(f"{os.path.basename(chemin)} : JSON invalide ({e})")


def lister_cas(dossier):
    """Cas du corpus : [(nom, {donnees, placement, routage, couture?, attendu_strict, attendu_altium,
    attendu_mesures}, grille)], par nom croissant."""
    if not os.path.isdir(dossier):
        raise ErreurEntree("--cas : dossier introuvable")
    liste = []
    for nom in sorted(os.listdir(dossier)):
        rep = os.path.join(dossier, nom)
        if not os.path.isfile(os.path.join(rep, "cas.json")):
            continue
        c = lire_json(os.path.join(rep, "cas.json"))
        fichiers = {k: os.path.join(rep, v) for k, v in c.get("fichiers", {}).items() if v}
        for cle in ("donnees", "placement", "routage"):
            if cle not in fichiers:
                raise ErreurEntree(f"cas {nom} : fichier « {cle} » absent de cas.json")
        liste.append((nom, fichiers, bool(c.get("grille", True))))
    if not liste:
        raise ErreurEntree("--cas : aucun cas (cas.json) dans le dossier")
    return liste


def _couture_v2(chemin):
    """``vias_couture.json`` lu tel quel s'il porte ``format`` ; sinon (version 1 des références figées, sans
    ``format`` ni ``reseaux``), copie temporaire en ``vias_couture/2`` (réseaux des vias, aucun refus)."""
    d = lire_json(chemin)
    if not isinstance(d, dict) or "format" in d:
        return chemin
    vias = d.get("vias") or []
    v2 = {"format": "vias_couture/2", "unite": d.get("unite", "mil"),
          "reseaux": sorted({v["net"] for v in vias}), "pas_grille_mm": d.get("pas_grille_mm", 2.5),
          "vias": [{k: v[k] for k in ("x", "y", "size", "hole_size", "net")} for v in vias], "refus": {}}
    f = tempfile.NamedTemporaryFile("w", suffix="_vias_couture.json", delete=False, encoding="utf-8")
    with f:
        json.dump(v2, f)
    return f.name


def cas_graine(dossier, donnees):
    if not os.path.isdir(dossier):
        raise ErreurEntree("--graine : dossier introuvable")
    if not os.path.isfile(donnees):
        raise ErreurEntree("--donnees : fichier introuvable")
    fichiers = {"donnees": donnees, "placement": os.path.join(dossier, "placements_routage.json"),
                "routage": os.path.join(dossier, "routage_detaille.json")}
    for cle in ("placement", "routage"):
        if not os.path.isfile(fichiers[cle]):
            raise ErreurEntree(f"--graine : {os.path.basename(fichiers[cle])} absent")
    couture = os.path.join(dossier, "vias_couture.json")
    if os.path.isfile(couture):
        fichiers["couture"] = _couture_v2(couture)
    return [(os.path.basename(os.path.normpath(dossier)) or "graine", fichiers, True)]


def sorties_attendues(fichiers):
    """Attendus d'un cas du corpus (``placement`` : champ de ``attendu_strict.json``)."""
    if "attendu_strict" not in fichiers:
        return None
    s = lire_json(fichiers["attendu_strict"])
    return {"strict": s, "altium": lire_json(fichiers["attendu_altium"]), "placement": s.get("placement"),
            "cout": lire_json(fichiers["attendu_mesures"]) if "attendu_mesures" in fichiers else None}


# --- Implémentations : exécutables et modules Python ------------------------------------------------------------

def verifier_executable(exe, option):
    if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
        raise ErreurEntree(f"{option} : exécutable introuvable")


def lancer_verifier(exe, fichiers, mode, grille, mesures=False):
    """Lance ``EXE verifier`` ; rend (verdict, mesures|None) ou lève RuntimeError (code ≠ 0)."""
    with tempfile.TemporaryDirectory() as tmp:
        sortie = os.path.join(tmp, "verdict.json")
        args = [os.path.abspath(exe), "verifier", "--donnees", fichiers["donnees"], "--placement", fichiers["placement"],
                "--routage", fichiers["routage"], "--mode", mode, "--sortie", sortie]
        if "couture" in fichiers:
            args += ["--couture", fichiers["couture"]]
        if not grille:
            args.append("--sans-grille")
        if mesures:
            args += ["--mesures", os.path.join(tmp, "mesures.json")]
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=DELAI_S)
        if p.returncode != 0:
            raise RuntimeError(f"{os.path.basename(exe)} verifier : code {p.returncode} ({p.stderr.strip()[:300]})")
        v = lire_json(sortie)
        m = lire_json(os.path.join(tmp, "mesures.json")) if mesures else None
        return v, m


def source_moteur(exe, modes=MODES):
    def calculer(fichiers, grille):
        verdicts = {mode: lancer_verifier(exe, fichiers, mode, grille)[0] for mode in modes}
        # Placement : champ du verdict strict (indépendant du mode, lu dans le premier verdict produit).
        return dict(verdicts, placement=verdicts[modes[0]].get("placement"))
    return calculer


def source_cout_rust(exe):
    def calculer(fichiers, grille):
        return {"cout": lancer_verifier(exe, fichiers, "strict", grille, mesures=True)[1]}
    return calculer


def charger_modules_python(dossier):
    """Fonctions du lot C : (drc_maison.verifier, verifier_placement.verifier, mesures.mesurer)."""
    if not os.path.isdir(dossier):
        raise ErreurEntree("--modules-python : dossier introuvable")
    voulus = (("drc_maison", "verifier"), ("verifier_placement", "verifier"), ("mesures", "mesurer"))
    fonctions = []
    sys.path.insert(0, dossier)
    try:
        for module, fonction in voulus:
            chemin = os.path.join(dossier, module + ".py")
            if not os.path.isfile(chemin):
                raise ErreurEntree(f"--modules-python : {module}.py absent (lot C, §5.2 : {module}.{fonction})")
            spec = importlib.util.spec_from_file_location("_comparer_" + module, chemin)
            m = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(m)
            except Exception as e:  # module illisible : environnement invalide
                raise ErreurEntree(f"--modules-python : import de {module}.py impossible ({type(e).__name__})")
            f = getattr(m, fonction, None)
            if not callable(f):
                raise ErreurEntree(f"--modules-python : {module}.py n'offre pas {fonction}() (API de §5.2 ; "
                                   f"module antérieur au contrat 2 ?)")
            fonctions.append(f)
    finally:
        sys.path.remove(dossier)
    return tuple(fonctions)


def source_python(drc, placement, mesurer, modes=MODES):
    def calculer(fichiers, grille):
        d = lire_json(fichiers["donnees"])
        p = lire_json(fichiers["placement"])
        r = lire_json(fichiers["routage"])
        c = lire_json(fichiers["couture"]) if "couture" in fichiers else None
        verdicts = {mode: drc(d, p, r, mode=mode, couture=c) for mode in modes}
        strict_seul = verdicts["strict"] if c is None and "strict" in verdicts else             drc(d, p, r, mode="strict", couture=None)
        return dict(verdicts, placement=placement(p, d, grille=grille),
                    cout=mesurer(d, p, r, verdict_strict=strict_seul))
    return calculer


# --- Comparaison ------------------------------------------------------------------------------------------------

def comparer(liste_cas, sources, modes=MODES):
    """``sources`` : {nom: calculer(fichiers, grille) -> sorties partielles}. Rend le rapport."""
    ecarts, comparaisons = [], 0
    for nom, fichiers, grille in liste_cas:
        sorties = {}
        attendus = sorties_attendues(fichiers)
        if attendus is not None:
            sorties["attendus"] = attendus
        for source, calculer in sources.items():
            try:
                sorties[source] = calculer(fichiers, grille)
            except ErreurEntree:
                raise
            except Exception as e:  # échec d'une implémentation sur un cas : écart, pas erreur interne
                ecarts.append({"cas": nom, "quoi": "execution", "champ": "", "sources": [source],
                               "valeurs": [f"{type(e).__name__} : {str(e)[:300]}"]})
        # Le coût Rust (binaire d'essai) complète la source « rust ».
        if "cout_rust" in sorties and "rust" in sorties:
            sorties["rust"] = dict(sorties["rust"], cout=sorties.pop("cout_rust")["cout"])
        e, n = comparer_sorties(nom, sorties, modes)
        ecarts += e
        comparaisons += n
    return rapport(len(liste_cas), ecarts, comparaisons, ["attendus"] + list(sources))


def _sortie_utf8():
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            try:
                flux.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv=None, doublures=None):
    """``doublures`` (tests) : {nom de source: calculer} qui remplace les exécutables et les modules."""
    _sortie_utf8()
    ap = argparse.ArgumentParser(description="Comparaison attendus / Rust / Python de la validité (§7.4).")
    qui = ap.add_mutually_exclusive_group(required=True)
    qui.add_argument("--cas", help="dossier des cas du corpus")
    qui.add_argument("--graine", help="dossier d'une graine (avec --donnees)")
    ap.add_argument("--donnees", help="donnees_placeur.json de la graine")
    ap.add_argument("--moteur", help="exécutable offrant « verifier » (§5.1)")
    ap.add_argument("--cout-rust", help="binaire d'essai validite_verifier (option --mesures)")
    ap.add_argument("--modules-python", help="dossier des modules du lot C")
    ap.add_argument("--mode", choices=("strict", "altium", "les_deux"), default="les_deux",
                    help="modes de vérification comparés (défaut : les deux)")
    ap.add_argument("--rapport", help="rapport JSON à écrire")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        if a.graine is not None and a.donnees is None:
            raise ErreurEntree("--graine demande --donnees")
        liste = lister_cas(a.cas) if a.cas is not None else cas_graine(a.graine, a.donnees)
        modes = MODES if a.mode == "les_deux" else (a.mode,)
        sources = {}
        if doublures is not None:
            sources.update(doublures)
        else:
            if a.moteur:
                verifier_executable(a.moteur, "--moteur")
                sources["rust"] = source_moteur(a.moteur, modes)
            if a.cout_rust:
                verifier_executable(a.cout_rust, "--cout-rust")
                sources["cout_rust"] = source_cout_rust(a.cout_rust)
            if a.modules_python:
                sources["python"] = source_python(*charger_modules_python(a.modules_python), modes)
        if not sources:
            raise ErreurEntree("rien à comparer : --moteur, --cout-rust ou --modules-python attendu")
        if a.graine is not None and len(sources) - ("cout_rust" in sources and "rust" in sources) < 2:
            raise ErreurEntree("--graine : au moins deux implémentations à comparer (sans attendu)")
        r = comparer(liste, sources, modes)
        if a.rapport:
            with open(a.rapport, "w", encoding="utf-8", newline="\n") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
                f.write("\n")
    except ErreurEntree as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"erreur : écriture du rapport impossible ({e.strerror or type(e).__name__})", file=sys.stderr)
        return 2
    except Exception as e:  # erreur interne : jamais de trace (elle nommerait des chemins)
        print(f"erreur interne : {type(e).__name__}", file=sys.stderr)
        return 4
    print(f"{r['cas']} cas, {r['comparaisons']} comparaisons, {r['nombre_ecarts']} écarts")
    for e in r["ecarts"][:20]:
        print(f"  {e['cas']} {e['quoi']} {e['champ']} : {' / '.join(e['sources'])} : {e['valeurs']}")
    return 1 if r["ecarts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
