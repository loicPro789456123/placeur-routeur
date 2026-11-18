# -*- coding: utf-8 -*-
"""Fusion d'une campagne et statistiques de l'avenant 2.2, A.7 (§7.6, §7.7 ; lot D, critère 11).

  python ci_fusion.py [DOSSIER ...] --sortie S [--reference-cout X] [--comparer REF ...] [--anciens RUN ...]
                      [--marge-cout 0,04] [--campagne-graines 8] [--niveau-ni 0,90] [--graines-min-ni 40]

DOSSIER : dossiers où chercher les sorties de `placeur.py` (`<calcul>/technique/rapport.json`, une par machine).
Elles sont fusionnées en un rapport de campagne au format `rapport/2` (S/rapport.json), avec les blocs facultatifs
`statistiques` et `comparaison` (§0.5 : champs ajoutés, ignorés des lecteurs), et S/resume.md (onglet Actions).

--anciens RUN : résultats de l'ancien code (`9b5b5a3`, branche ci-resultats-placeur, dossiers run_<n>) :
`campagnes.csv` et `c_<graine>/stats.json`. Sans DOSSIER, la campagne publiée est celle des anciens résultats
(S/anciens.json : mêmes blocs, sans paramètres effectifs que l'ancien format ne porte pas, plus la correspondance
des champs) ; plusieurs RUN sont réunis (run_4 et run_6 : 3001-3020 et 4001-4040).

--comparer REF : campagne de référence appariée par graine (dossier de sorties de placeur.py, rapport de campagne
`rapport.json`, ou dossier run_<n> de l'ancien format) ; plusieurs REF sont réunies. Règle NI(m ; d) de A.7.

Aucune variable d'environnement lue ; aucun chemin absolu écrit (dossiers relatifs aux DOSSIER donnés)."""
import csv
import json
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import chemins  # noqa: E402
import formats  # noqa: E402
import placeur  # noqa: E402

NIVEAU_NI = 0.90          # A.7-1 : deux moteurs au taux de la référence réussissent au moins 90 fois sur 100
MARGE_COUT = 0.04         # A.7-2 : d = 0,04 en vague 1
GRAINES_MIN_NI = 40       # A.7 : « au moins 40 » graines appariées
SEUIL_80 = 0.80           # A.7 : affirmation « plus de 80 % de graines valides »
ALPHA_CP = 0.05           # Clopper-Pearson unilatéral à 95 %
GRAINES_CAMPAGNE = placeur.GRAINES_CAMPAGNE   # P(8)

# Ancien format (9b5b5a3) → rapport/2 : correspondance consignée dans anciens.json et dans le journal du lot D.
CORRESPONDANCE_ANCIENNE = {
    "campagnes.csv:source=campagne": "graines[] (une ligne par graine) ; source=rev3/rev4 : références",
    "campagnes.csv:graine": "graines[].graine",
    "campagnes.csv:valide (1/0)": "graines[].valide (jugé par les contrôles de l'ancien campagnes.py)",
    "campagnes.csv:cout": "graines[].cout_commun (formule cout_commun/1 de l'ancien campagnes.py:56-58)",
    "campagnes.csv:duree_s": "graines[].duree_s",
    "campagnes.csv:cause": "graines[].cause_moteur",
    "c_<g>/stats.json:temps_s": "graines[].temps_calcul_s (= execution.temps_total_s)",
    "c_<g>/stats.json:temps_phases_s": "durées par phase : « recuit » → recuit, « routage detaille » → "
                                       "routage_detaille, « boucle k » → boucle (somme), « conjointe », « finition »",
    "absent de l'ancien format": "parametres.effectifs, import, estimation, incoherence (notée fausse)",
}
PHASES_ANCIENNES = {"recuit": "recuit", "routage detaille": "routage_detaille", "conjointe": "conjointe",
                    "finition": "finition"}


class ErreurFusion(Exception):
    """Entrées de fusion inutilisables : message, code 2."""


# ======================================================================================================= statistiques

def loi_binomiale(n: int, p: float) -> list[float]:
    """P(X = k), k = 0..n, X ~ B(n ; p) (calcul exact)."""
    return [math.comb(n, k) * p ** k * (1.0 - p) ** (n - k) for k in range(n + 1)]


def proba_ni(m: int, n: int, p: float) -> float:
    """P(X_nouveau ≥ X_réf − m) pour deux lois B(n ; p) indépendantes."""
    f = loi_binomiale(n, p)
    queue = [0.0] * (n + 2)                  # queue[k] = P(X ≥ k)
    for k in range(n, -1, -1):
        queue[k] = queue[k + 1] + f[k]
    return sum(f[r] * queue[max(0, r - m)] for r in range(n + 1))


def marge_ni(p: float, n: int = GRAINES_MIN_NI, niveau: float = NIVEAU_NI) -> int:
    """m de A.7-1 : plus petit entier tel que P(X_nouveau ≥ X_réf − m) ≥ niveau (loi binomiale exacte)."""
    for m in range(n + 1):
        if proba_ni(m, n, p) >= niveau - 1e-12:
            return m
    return n


def clopper_pearson_bas(k: int, n: int, alpha: float = ALPHA_CP) -> float:
    """Borne inférieure unilatérale de Clopper-Pearson au niveau 1 − alpha : le p tel que P(X ≥ k ; p) = alpha."""
    if n <= 0 or k <= 0:
        return 0.0

    def queue(p):
        return sum(loi_binomiale(n, p)[k:])
    bas, haut = 0.0, 1.0
    for _ in range(100):
        milieu = (bas + haut) / 2
        if queue(milieu) < alpha:
            bas = milieu
        else:
            haut = milieu
    return (bas + haut) / 2


def plus_de_80(k: int, n: int) -> bool:
    """Affirmation « plus de 80 % de graines valides » (A.7) : borne de Clopper-Pearson à 95 % ≥ 0,80."""
    return clopper_pearson_bas(k, n) >= SEUIL_80


def comparer(nouveau: list[dict], reference: list[dict], d: float = MARGE_COUT, niveau: float = NIVEAU_NI,
             graines_min: int = GRAINES_MIN_NI) -> dict:
    """Règle NI(m ; d) de A.7 sur les graines communes (appariement par graine)."""
    gn = {g["graine"]: g for g in nouveau}
    gr = {g["graine"]: g for g in reference}
    communes = sorted(set(gn) & set(gr))
    seules_n, seules_r = sorted(set(gn) - set(gr)), sorted(set(gr) - set(gn))
    n = len(communes)
    xn = sum(1 for g in communes if gn[g].get("valide"))
    xr = sum(1 for g in communes if gr[g].get("valide"))

    def couts(t):
        return [t[g]["cout_commun"] for g in communes if t[g].get("valide")
                and isinstance(t[g].get("cout_commun"), (int, float))]
    med_n, med_r = placeur.mediane(couts(gn)), placeur.mediane(couts(gr))
    m = marge_ni(xr / n, n, niveau) if n else None
    validite = bool(n) and xn >= xr - m
    cout = None if med_n is None or med_r is None else med_n <= (1.0 + d) * med_r
    appariee = not seules_n and not seules_r
    return {
        "regle": f"NI(m ; {d:g})".replace(".", ","), "graines_communes": n, "graines_min": graines_min,
        "graines_seulement_nouveau": seules_n, "graines_seulement_reference": seules_r, "appariee": appariee,
        "valides_nouveau": xn, "valides_reference": xr, "taux_reference": round(xr / n, 6) if n else None,
        "m": m, "niveau": niveau, "validite_ok": validite,
        "mediane_nouveau": round(med_n, 6) if med_n is not None else None,
        "mediane_reference": round(med_r, 6) if med_r is not None else None,
        "marge_cout": d, "cout_ok": cout,
        "accepte": bool(appariee and n >= graines_min and validite and cout),
    }


def phases_graine(stats: dict | None) -> dict:
    """Durées par phase d'une graine : stats/2 (`execution.temps_phases_s`) ou ancien format (`temps_phases_s`)."""
    if not isinstance(stats, dict):
        return {}
    ex = stats.get("execution")
    if isinstance(ex, dict) and isinstance(ex.get("temps_phases_s"), dict):
        return {k: float(v) for k, v in ex["temps_phases_s"].items() if isinstance(v, (int, float))}
    res = {}
    for k, v in (stats.get("temps_phases_s") or {}).items():
        if not isinstance(v, (int, float)):
            continue
        cle = "boucle" if k.startswith("boucle") else PHASES_ANCIENNES.get(k, k.replace(" ", "_"))
        res[cle] = res.get(cle, 0.0) + float(v)
    return res


def durees_phases(phases: list[dict]) -> dict:
    """Par phase : total, médiane par graine et part du total."""
    noms = sorted({k for p in phases for k in p})
    total = sum(sum(p.values()) for p in phases)
    res = {}
    for k in noms:
        v = [p.get(k, 0.0) for p in phases]
        res[k] = {"total_s": round(sum(v), 3), "mediane_s": round(placeur.mediane(v), 3),
                  "part": round(sum(v) / total, 6) if total else None}
    return {"graines": len(phases), "phases": res}


def bloc_statistiques(graines: list[dict], phases: list[dict], reference: float | None, ratios: list[dict],
                      k: int = GRAINES_CAMPAGNE) -> dict:
    s = placeur.statistiques(graines, reference, k)
    s["clopper_pearson_95_bas"] = round(clopper_pearson_bas(s["graines_valides"], s["graines_mesurees"]), 6)
    s["plus_de_80_pourcent"] = plus_de_80(s["graines_valides"], s["graines_mesurees"])
    s["durees_phases"] = durees_phases(phases)
    v = [r["rapport"] for r in ratios if r["rapport"] is not None]
    s["duree_prevue_sur_reelle"] = {
        "machines": ratios, "min": round(min(v), 4) if v else None, "max": round(max(v), 4) if v else None,
        "mediane": round(placeur.mediane(v), 4) if v else None,
        "dans_0_5_a_2": sum(1 for x in v if 0.5 <= x <= 2.0)}
    return s


# ============================================================================================================ lectures

def _json(chemin: str) -> dict:
    with open(chemin, encoding="utf-8-sig") as f:
        return json.load(f)


def _relatif(chemin: str, racine: str) -> str:
    r = os.path.relpath(chemin, racine)
    r = os.path.basename(os.path.abspath(chemin)) if r in (".", "") or r.startswith("..") else r
    return r.replace(os.sep, "/")


def chercher_rapports(racines: list[str]) -> list[tuple[str, str, dict]]:
    """[(machine, dossier_du_calcul, rapport)] : chaque `<calcul>/technique/rapport.json` sous les racines
    (machine : chemin du calcul relatif à sa racine)."""
    res, vus = [], set()
    for racine in racines:
        if os.path.isfile(racine):
            calcul = os.path.dirname(os.path.dirname(os.path.abspath(racine)))
            trouves = [(os.path.dirname(calcul), calcul)]
        else:
            trouves = []
            for d, sous, fichiers in os.walk(racine):
                sous.sort()
                if os.path.basename(d) == "technique" and "rapport.json" in fichiers:
                    trouves.append((racine, os.path.dirname(d)))
                    sous[:] = []
        for base, calcul in trouves:
            cle = os.path.normcase(os.path.abspath(calcul))
            if cle in vus:
                continue
            vus.add(cle)
            r = _json(os.path.join(calcul, "technique", "rapport.json"))
            if r.get("format") != placeur.FORMAT_RAPPORT:
                raise ErreurFusion(f"{_relatif(calcul, base)} : rapport.json au format {r.get('format')!r} "
                                   f"(attendu {placeur.FORMAT_RAPPORT})")
            res.append((_relatif(calcul, base), calcul, r))
    return res


def lire_anciens(run: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Résultats de l'ancien code (run_<n>) → (graines au sens de rapport/2, durées par phase, références)."""
    fcsv = os.path.join(run, "campagnes.csv")
    if not os.path.isfile(fcsv):
        raise ErreurFusion(f"{os.path.basename(os.path.normpath(run))} : campagnes.csv absent (ancien format attendu)")
    graines, phases, refs = [], [], []
    with open(fcsv, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            cout = float(r["cout"]) if (r.get("cout") or "").strip() else None
            if r.get("source") != "campagne":
                refs.append({"source": r.get("source"), "valide": r.get("valide") == "1", "cout_commun": cout})
                continue
            g = int(r["graine"])
            fstats = os.path.join(run, f"c_{g}", "stats.json")
            stats = _json(fstats) if os.path.isfile(fstats) else None
            graines.append({
                "graine": g, "dossier": f"c_{g}", "valide": r.get("valide") == "1",
                "cout_commun": cout,
                "duree_s": float(r["duree_s"]) if (r.get("duree_s") or "").strip() else 0.0,
                "temps_calcul_s": (stats or {}).get("temps_s"), "cause_moteur": r.get("cause") or "",
                "incoherence": False})
            phases.append(phases_graine(stats))
    return graines, phases, refs


def _graines_rapport(calcul: str, machine: str, r: dict) -> tuple[list[dict], list[dict]]:
    graines, phases = [], []
    for g in r.get("graines") or []:
        e = dict(g)
        e["dossier"] = f"{machine}/{g['dossier']}"
        graines.append(e)
        fstats = os.path.join(calcul, *g["dossier"].split("/"), "stats.json")
        phases.append(phases_graine(_json(fstats)) if os.path.isfile(fstats) else {})
    return graines, phases


def graines_reference(refs: list[str]) -> list[dict]:
    """Graines d'une campagne de référence : ancien format, rapport de campagne ou sorties de placeur.py."""
    res = {}
    for ref in refs:
        if os.path.isdir(ref) and os.path.isfile(os.path.join(ref, "campagnes.csv")):
            graines = lire_anciens(ref)[0]
        elif os.path.isfile(ref):
            graines = _json(ref).get("graines") or []
        elif os.path.isfile(os.path.join(ref, "rapport.json")) and not os.path.isdir(os.path.join(ref, "technique")):
            graines = _json(os.path.join(ref, "rapport.json")).get("graines") or []
        else:
            graines = [g for m, c, r in chercher_rapports([ref]) for g in _graines_rapport(c, m, r)[0]]
        for g in graines:
            res.setdefault(g["graine"], g)
    return [res[k] for k in sorted(res)]


# ============================================================================================================== fusion

def fusionner(trouves: list[tuple[str, str, dict]], reference_cout: float | None = None,
              comparaison_avec: list[dict] | None = None, d: float = MARGE_COUT, k: int = GRAINES_CAMPAGNE,
              niveau: float = NIVEAU_NI, graines_min: int = GRAINES_MIN_NI) -> dict:
    """Sorties de placeur.py (une par machine) → rapport de campagne rapport/2 avec `statistiques` et
    `comparaison`."""
    if not trouves:
        raise ErreurFusion("aucune sortie de placeur.py (technique/rapport.json) trouvée")
    base = trouves[0][2]
    cartes = sorted({r.get("carte") for _, _, r in trouves})
    if len(cartes) > 1:
        raise ErreurFusion(f"sorties de cartes différentes : {', '.join(map(str, cartes))}")
    avert, graines, phases, ratios, vus = [], [], [], [], {}
    for machine, calcul, r in trouves:
        if r.get("parametres", {}).get("niveau") != base.get("parametres", {}).get("niveau"):
            avert.append(f"{machine} : niveau {r['parametres'].get('niveau')} (base : "
                         f"{base['parametres'].get('niveau')})")
        gs, ps = _graines_rapport(calcul, machine, r)
        for g, p in zip(gs, ps):
            if g["graine"] in vus:
                avert.append(f"graine {g['graine']} présente deux fois ({vus[g['graine']]} et {machine}) : "
                             f"première gardée")
                continue
            vus[g["graine"]] = machine
            graines.append(g)
            phases.append(p)
        prevue = (r.get("estimation") or {}).get("duree_prevue_s")
        reelle = r.get("duree_totale_s")
        ratios.append({"machine": machine, "duree_prevue_s": prevue, "duree_reelle_s": reelle,
                       "rapport": round(prevue / reelle, 4) if prevue and reelle else None})
        avert += [f"{machine} : {a}" for a in r.get("avertissements") or []]
    ordre = sorted(range(len(graines)), key=lambda i: graines[i]["graine"])
    graines, phases = [graines[i] for i in ordre], [phases[i] for i in ordre]
    valides = [g for g in graines if g.get("valide") and isinstance(g.get("cout_commun"), (int, float))]
    meilleure = min(valides, key=lambda g: (g["cout_commun"], g["graine"]))["graine"] if valides else None
    fins = [r.get("fin") for _, _, r in trouves if r.get("fin") != "normale"]
    est = [r.get("estimation") or {} for _, _, r in trouves]
    rapport = {
        "format": placeur.FORMAT_RAPPORT, "version": base.get("version"), "carte": base.get("carte"),
        "mode": "campagne", "termine": all(r.get("termine") for _, _, r in trouves),
        "fin": fins[0] if fins else "normale",
        "parametres": base.get("parametres"), "import": base.get("import"), "deduction": base.get("deduction") or {},
        "estimation": {c: max(float(e.get(c) or 0.0) for e in est)
                       for c in ("memoire_mo", "duree_prevue_s", "duree_max_s")},
        "graines": graines, "meilleure": meilleure, "livrable": None, "couture": None,
        "reference_existante": None,
        "incoherences": sum(1 for g in graines if g.get("incoherence")),
        "avertissements": list(dict.fromkeys(avert)),
        "duree_totale_s": max(float(r.get("duree_totale_s") or 0.0) for _, _, r in trouves),
        "duree_cumulee_s": round(sum(float(r.get("duree_totale_s") or 0.0) for _, _, r in trouves), 3),
        "machines": [m for m, _, _ in trouves],
        "statistiques": bloc_statistiques(graines, phases, reference_cout, ratios, k),
        "comparaison": (comparer(graines, comparaison_avec, d, niveau, graines_min)
                        if comparaison_avec is not None else None),
    }
    return rapport


def campagne_ancienne(runs: list[str], reference_cout: float | None, comparaison_avec: list[dict] | None,
                      d: float = MARGE_COUT, k: int = GRAINES_CAMPAGNE, niveau: float = NIVEAU_NI,
                      graines_min: int = GRAINES_MIN_NI) -> dict:
    graines, phases, refs, vus = [], [], [], set()
    for run in runs:
        gs, ps, rs = lire_anciens(run)
        for g, p in zip(gs, ps):
            if g["graine"] not in vus:
                vus.add(g["graine"])
                g["dossier"] = f"{os.path.basename(os.path.normpath(run))}/{g['dossier']}"
                graines.append(g)
                phases.append(p)
        refs += [dict(r, run=os.path.basename(os.path.normpath(run))) for r in rs]
    ratios = []
    return {"format": "campagne_ancienne/1", "source": [os.path.basename(os.path.normpath(r)) for r in runs],
            "graines": graines, "references": refs, "correspondance": CORRESPONDANCE_ANCIENNE,
            "statistiques": bloc_statistiques(graines, phases, reference_cout, ratios, k),
            "comparaison": (comparer(graines, comparaison_avec, d, niveau, graines_min)
                            if comparaison_avec is not None else None)}


# ============================================================================================================== resume

def _fr(x, chiffres: int = 2) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "oui" if x else "non"
    if isinstance(x, int):
        return str(x)
    return f"{x:.{chiffres}f}".replace(".", ",")


def resume(rapport: dict, titre: str) -> str:
    s = rapport["statistiques"]
    w = s["wilson_95"] or [None, None]
    L = [f"## {titre}", "",
         f"- graines mesurées : {s['graines_mesurees']} ; **valides : {s['graines_valides']}** "
         f"(taux {_fr(s['taux_validite'], 3)}, Wilson 95 % [{_fr(w[0] * 100 if w[0] is not None else None, 0)} % ; "
         f"{_fr(w[1] * 100 if w[1] is not None else None, 0)} %])",
         f"- borne de Clopper-Pearson à 95 % : {_fr(s['clopper_pearson_95_bas'], 3)} ; « plus de 80 % » "
         f"affirmable : {_fr(s['plus_de_80_pourcent'])}",
         f"- coût commun des valides : meilleur {_fr(s['meilleur_cout'])}, médiane {_fr(s['mediane_cout'])}",
         f"- calcul par graine valide : {_fr(s['calcul_par_graine_valide_s'], 0)} s "
         f"(total {_fr(s['calcul_total_s'], 0)} s)",
         f"- P({s['campagne_graines']}) : probabilité qu'une campagne de {s['campagne_graines']} graines livre une "
         f"graine valide de coût ≤ {_fr(s['reference_cout'])} : {_fr(s['p_campagne'], 3)}"]
    dp = s["duree_prevue_sur_reelle"]
    if dp["machines"]:
        L.append(f"- durée prévue / durée réelle : min {_fr(dp['min'])}, médiane {_fr(dp['mediane'])}, max "
                 f"{_fr(dp['max'])} ; dans [0,5 ; 2] : {dp['dans_0_5_a_2']} sur {len(dp['machines'])}")
    c = rapport.get("comparaison")
    if c:
        L.append(f"- comparaison {c['regle']} (m = {c['m']}) sur {c['graines_communes']} graines appariées : "
                 f"valides {c['valides_nouveau']} contre {c['valides_reference']} ({_fr(c['validite_ok'])}) ; "
                 f"médianes {_fr(c['mediane_nouveau'])} contre {_fr(c['mediane_reference'])} "
                 f"({_fr(c['cout_ok'])}) ; **{'acceptée' if c['accepte'] else 'refusée'}**")
    L += ["", "| Phase | Total s | Médiane s | Part |", "|---|---:|---:|---:|"]
    for k, v in s["durees_phases"]["phases"].items():
        L.append(f"| {k} | {_fr(v['total_s'], 0)} | {_fr(v['mediane_s'], 1)} | {_fr((v['part'] or 0) * 100, 1)} % |")
    L += ["", "| Graine | Valide | Coût commun | Durée s | Dossier |", "|---:|---|---:|---:|---|"]
    for g in sorted(rapport["graines"], key=lambda g: (not g.get("valide"), g.get("cout_commun") or 0, g["graine"])):
        L.append(f"| {g['graine']} | {_fr(bool(g.get('valide')))} | {_fr(g.get('cout_commun'))} | "
                 f"{_fr(g.get('duree_s'), 0)} | {g.get('dossier', '')} |")
    return "\n".join(L) + "\n"


# ================================================================================================= ligne de commande

def _nombre(texte: str) -> float:
    return float(texte.replace(",", "."))


def main(argv: list[str] | None = None, flux=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    flux = sys.stdout if flux is None else flux
    o = {"dossiers": [], "--comparer": [], "--anciens": [], "--sortie": None, "--reference-cout": None,
         "--marge-cout": str(MARGE_COUT), "--campagne-graines": str(GRAINES_CAMPAGNE), "--niveau-ni": str(NIVEAU_NI),
         "--graines-min-ni": str(GRAINES_MIN_NI)}
    i = 0
    try:
        while i < len(argv):
            a = argv[i]
            if a in o and a.startswith("--"):
                if i + 1 >= len(argv):
                    raise ErreurFusion(f"option {a} : valeur manquante")
                if isinstance(o[a], list):
                    o[a].append(argv[i + 1])
                else:
                    o[a] = argv[i + 1]
                i += 2
            elif a.startswith("--"):
                raise ErreurFusion(f"option inconnue : {a}")
            else:
                o["dossiers"].append(a)
                i += 1
        if not o["--sortie"]:
            raise ErreurFusion("--sortie S attendu")
        if not o["dossiers"] and not o["--anciens"]:
            raise ErreurFusion("donner des DOSSIER de sorties de placeur.py ou --anciens RUN")
        ref = _nombre(o["--reference-cout"]) if o["--reference-cout"] else None
        d, k = _nombre(o["--marge-cout"]), int(o["--campagne-graines"])
        niveau, gmin = _nombre(o["--niveau-ni"]), int(o["--graines-min-ni"])
        comp = graines_reference(o["--comparer"]) if o["--comparer"] else None
        os.makedirs(o["--sortie"], exist_ok=True)
        code = 0
        if o["dossiers"]:
            rapport = fusionner(chercher_rapports(o["dossiers"]), ref, comp, d, k, niveau, gmin)
            erreurs = formats.valider_rapport(rapport)
            if erreurs:
                rapport["avertissements"].append(f"rapport de campagne refusé par formats.valider_rapport : "
                                                 f"{erreurs[:3]}")
                code = 1
            nom, titre = "rapport.json", f"Campagne {rapport['carte']} ({rapport['parametres'].get('niveau')})"
        else:
            rapport = campagne_ancienne(o["--anciens"], ref, comp, d, k, niveau, gmin)
            nom, titre = "anciens.json", f"Anciens résultats ({', '.join(rapport['source'])})"
        texte = json.dumps(rapport, ensure_ascii=False, indent=1)
        motifs = chemins.chercher_motifs(texte, lecteur=True)
        if motifs:
            raise ErreurFusion(f"chemin ou donnée personnelle dans le rapport de campagne ({len(motifs)} motif(s))")
        chemins.ecrire_json(os.path.join(o["--sortie"], nom), rapport)
        md = resume(rapport, titre)
        with open(os.path.join(o["--sortie"], "resume.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        flux.write(md)
        return code
    except (ErreurFusion, formats.ErreurFormat, OSError, ValueError, KeyError) as e:
        print(f"ci_fusion : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        _f.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
