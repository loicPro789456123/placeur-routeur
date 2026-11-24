"""Règles de la carte vues et modifiées dans la fenêtre, puis transmises au calcul sans toucher au fichier d'origine.

- ``lignes(entree)`` : règles interprétées par l'import (``entree_carte/2``, clé « regles » et « paires ») mises à
  plat, une ligne par valeur modifiable : largeurs, dégagements (matrice des classes), vias, carte, plans, paires
  différentielles, longueurs, réseaux à règle propre.
- ``lire_valeur(ligne, texte)`` : valeur saisie (mm, virgule acceptée, vide = aucune si permis) ou message d'erreur.
- ``verifier(lignes, valeurs)`` : contrôles de cohérence (min ≤ préférée ≤ max, trou < diamètre…).
- ``surcharges(entree, lignes, valeurs)`` : morceau de ``contraintes/2`` (clés « regles » et « paires ») qui ne
  porte que les valeurs changées ; ``fusionner(contraintes, morceau)`` l'ajoute à un fichier de contraintes existant.
- ``appliquer(regles, surcharge)`` : règles effectives (appelé par construire_donnees) ; une largeur ou un via par
  défaut changé suit sur les réseaux qui n'ont pas de règle propre.

Toutes les longueurs sont en mm (même avec « unite »: « mil » dans les contraintes). Aucune variable
d'environnement n'est lue.
"""
from __future__ import annotations

import copy

MAXI_MM = 100.0
CHAMPS_VIA = (("diametre", "diamètre"), ("trou", "trou"), ("diametre_min", "diamètre min"),
              ("diametre_max", "diamètre max"), ("trou_min", "trou min"), ("trou_max", "trou max"))
NOMS_LARGEUR = ("min", "préférée", "max")


class ErreurRegles(ValueError):
    """Surcharge de règles impossible à appliquer (réseau inconnu, matrice de mauvaise taille…)."""


class Ligne:
    __slots__ = ("cle", "groupe", "libelle", "origine", "nul_permis", "aide")

    def __init__(self, cle, groupe, libelle, origine, nul_permis=False, aide=""):
        self.cle, self.groupe, self.libelle, self.origine = cle, groupe, libelle, origine
        self.nul_permis, self.aide = nul_permis, aide

    def __repr__(self):
        return f"Ligne({self.cle!r}, {self.origine!r})"


def _f(v):
    return None if v is None else float(v)


def lignes(entree: dict) -> list[Ligne]:
    """Règles modifiables de la carte importée, dans l'ordre d'affichage."""
    rg = entree.get("regles") or {}
    out = []
    ld = rg.get("largeur_defaut") or [None, None, None]
    for i, nom in enumerate(NOMS_LARGEUR):
        out.append(Ligne(f"largeur_defaut.{i}", "Pistes", f"Largeur {nom}", _f(ld[i]), i == 2,
                         "règle Width de portée la plus large"))
    iso = rg.get("isolement") or {}
    out.append(Ligne("isolement.defaut", "Dégagements", "Dégagement par défaut", _f(iso.get("defaut")), False,
                     "règle Clearance de portée la plus large"))
    classes, mat = iso.get("classes") or [], iso.get("matrice") or []
    if len(classes) > 1:
        for i, a in enumerate(classes):
            for j in range(i, len(classes)):
                if i < len(mat) and j < len(mat[i]):
                    out.append(Ligne(f"isolement.matrice.{i}.{j}", "Dégagements", f"{a} / {classes[j]}",
                                     _f(mat[i][j]), False, "dégagement entre deux classes d'objets"))
    via = rg.get("via") or {}
    for k, nom in CHAMPS_VIA:
        if k in via:
            out.append(Ligne(f"via.{k}", "Vias", f"Via : {nom}", _f(via[k]), k.endswith("_max"), "règle RoutingVias"))
    for k, nom, aide in (("trou_trou", "Trou à trou", "règle HoleToHoleClearance"),
                         ("garde_bord", "Garde au bord de carte", "règle de dégagement au contour"),
                         ("isolement_pieces", "Écart entre pièces", "règle ComponentClearance"),
                         ("masque_serigraphie", "Sérigraphie / masque", "règle SilkToSolderMaskClearance")):
        if k in rg:
            out.append(Ligne(k, "Carte", nom, _f(rg[k]), True, aide))
    h = rg.get("hauteurs") or {}
    for k in ("dessus", "dessous"):
        if k in h:
            out.append(Ligne(f"hauteurs.{k}", "Carte", f"Hauteur max {k}", _f(h[k]), True, "règle Height"))
    cp = rg.get("connexion_plans")
    if cp:
        out.append(Ligne("connexion_plans.isolement", "Plans", "Dégagement des plans", _f(cp.get("isolement")), False,
                         f"règle PlaneClearance ; connexion {cp.get('connexion', '')}"))
    for p in entree.get("paires") or []:
        n = p["nom"]
        out.append(Ligne(f"paire:{n}.ecart", "Paires différentielles", f"{n} : écart", _f(p.get("ecart")), True,
                         f"{p.get('positif', '')} / {p.get('negatif', '')}"))
        out.append(Ligne(f"paire:{n}.largeur", "Paires différentielles", f"{n} : largeur", _f(p.get("largeur")), True,
                         f"{p.get('positif', '')} / {p.get('negatif', '')}"))
    tol = rg.get("tolerances_longueur") or {}
    noms_paires = [p["nom"] for p in entree.get("paires") or []]
    for n in sorted(set(tol) | set(noms_paires)):
        out.append(Ligne(f"longueur:{n}", "Longueurs", f"{n} : tolérance d'appariement", _f(tol.get(n)), True,
                         "règle MatchedLengths"))
    classe_de = {}
    for k, membres in sorted((entree.get("classes") or {}).items(), key=lambda kv: len(kv[1])):
        for m in membres:
            classe_de.setdefault(m, k)
    for n, v in sorted((rg.get("reseaux") or {}).items()):
        if not v.get("regle_largeur_propre"):
            continue
        lg = v.get("largeurs") or [None, None, None]
        cl = classe_de.get(n)
        for i, nom in enumerate(NOMS_LARGEUR):
            out.append(Ligne(f"reseau:{n}.largeurs.{i}", "Réseaux et classes", f"{n} : largeur {nom}", _f(lg[i]),
                             i == 2, f"classe {cl}" if cl else "règle propre au réseau"))
    return out


def valeurs_origine(ls: list[Ligne]) -> dict:
    return {l.cle: l.origine for l in ls}


def lire_valeur(ligne: Ligne, texte: str):
    """(valeur, None) ou (None, message). Vide : aucune valeur (si permis)."""
    t = (texte or "").strip().replace(",", ".").replace(" ", "")
    if t.lower().endswith("mm"):
        t = t[:-2]
    if t in ("", "-", "—", "aucune"):
        if ligne.nul_permis:
            return None, None
        return None, f"{ligne.libelle} : valeur obligatoire"
    try:
        v = float(t)
    except ValueError:
        return None, f"{ligne.libelle} : nombre attendu (mm), « {texte.strip()} » reçu"
    if v != v or v <= 0:
        return None, f"{ligne.libelle} : valeur strictement positive attendue"
    if v > MAXI_MM:
        return None, f"{ligne.libelle} : {v:g} mm dépasse {MAXI_MM:g} mm"
    return v, None


def _pres(a, b):
    return a is not None and b is not None and a > b + 1e-9


def verifier(ls: list[Ligne], valeurs: dict) -> list[tuple[str, str]]:
    """Incohérences entre valeurs : [(clé, message)]."""
    err = []
    par = {l.cle: l for l in ls}

    def triplet(base, nom):
        v = [valeurs.get(f"{base}.{i}") for i in range(3)]
        if _pres(v[0], v[1]):
            err.append((f"{base}.0", f"{nom} : largeur min supérieure à la préférée"))
        if _pres(v[1], v[2]):
            err.append((f"{base}.2", f"{nom} : largeur préférée supérieure à la max"))
    triplet("largeur_defaut", "Pistes")
    for c in par:
        if c.startswith("reseau:") and c.endswith(".largeurs.0"):
            triplet(c[:-2], c[len("reseau:"):-len(".largeurs.0")])
    g = {k: valeurs.get(f"via.{k}") for k, _ in CHAMPS_VIA}
    if _pres(g["trou"], g["diametre"]) or (g["trou"] is not None and g["diametre"] is not None
                                           and g["trou"] >= g["diametre"] - 1e-9):
        err.append(("via.trou", "Via : le trou doit être plus petit que le diamètre"))
    for a, b, quoi in (("diametre_min", "diametre", "diamètre"), ("diametre", "diametre_max", "diamètre"),
                       ("trou_min", "trou", "trou"), ("trou", "trou_max", "trou")):
        if _pres(g[a], g[b]):
            err.append((f"via.{b}", f"Via : {quoi} hors de ses bornes min / max"))
    return err


def _egal(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-9


def modifiees(ls: list[Ligne], valeurs: dict) -> list[str]:
    return [l.cle for l in ls if l.cle in valeurs and not _egal(valeurs[l.cle], l.origine)]


def surcharges(entree: dict, ls: list[Ligne], valeurs: dict) -> dict:
    """Morceau de contraintes/2 (« regles », « paires ») portant seulement les valeurs changées."""
    rg = entree.get("regles") or {}
    ch = set(modifiees(ls, valeurs))
    v = valeurs
    out, paires = {}, {}
    if any(c.startswith("largeur_defaut.") for c in ch):
        out["largeur_defaut"] = [v.get(f"largeur_defaut.{i}") for i in range(3)]
    if "isolement.defaut" in ch or any(c.startswith("isolement.matrice.") for c in ch):
        iso = rg.get("isolement") or {}
        mat = [list(r) for r in iso.get("matrice") or []]
        ancien = iso.get("defaut")
        neuf = v.get("isolement.defaut", ancien)
        if "isolement.defaut" in ch:              # les cases égales à l'ancien défaut le suivent
            mat = [[neuf if _egal(x, ancien) else x for x in r] for r in mat]
        for c in ch:
            if c.startswith("isolement.matrice."):
                i, j = (int(x) for x in c.split(".")[2:4])
                mat[i][j] = mat[j][i] = v[c]
        out["isolement"] = {"defaut": neuf, "matrice": mat}
    if any(c.startswith("via.") for c in ch):
        out["via"] = {c[4:]: v.get(c) for c in (l.cle for l in ls) if c.startswith("via.")}
    for k in ("trou_trou", "garde_bord", "isolement_pieces", "masque_serigraphie"):
        if k in ch:
            out[k] = v.get(k)
    for k in ("dessus", "dessous"):
        if f"hauteurs.{k}" in ch:
            out.setdefault("hauteurs", {})[k] = v.get(f"hauteurs.{k}")
    if "connexion_plans.isolement" in ch:
        out["connexion_plans"] = {"isolement": v["connexion_plans.isolement"]}
    for c in sorted(ch):
        if c.startswith("longueur:") and v.get(c) is not None:
            out.setdefault("tolerances_longueur", {})[c[len("longueur:"):]] = v[c]
        elif c.startswith("reseau:"):
            n = c[len("reseau:"):c.rindex(".largeurs.")]
            out.setdefault("reseaux", {})[n] = {"largeurs": [v.get(f"reseau:{n}.largeurs.{i}") for i in range(3)]}
        elif c.startswith("paire:") and v.get(c) is not None:
            n, quoi = c[len("paire:"):].rsplit(".", 1)
            paires.setdefault(n, {"nom": n})[f"{quoi}_mm"] = v[c]
    res = {}
    if out:
        res["regles"] = out
    if paires:
        res["paires"] = [paires[n] for n in sorted(paires)]
    return res


def fusionner(contraintes: dict | None, morceau: dict) -> dict:
    """Contraintes de l'utilisateur complétées par le morceau de la fenêtre (la fenêtre l'emporte)."""
    ct = copy.deepcopy(contraintes) if contraintes else {}
    ct.setdefault("format", "contraintes/2")
    if "regles" in morceau:
        r = ct.setdefault("regles", {})
        for k, val in morceau["regles"].items():
            if isinstance(val, dict) and isinstance(r.get(k), dict) and k != "via":
                r[k] = {**r[k], **val}
            else:
                r[k] = copy.deepcopy(val)
    if "paires" in morceau:
        liste = ct.setdefault("paires", [])
        par = {p.get("nom"): p for p in liste}
        for p in morceau["paires"]:
            if p["nom"] in par:
                par[p["nom"]].update(p)
            else:
                liste.append(dict(p))
    return ct


def appliquer(regles: dict, surcharge: dict | None) -> dict:
    """Règles effectives : ``regles`` (entree_carte/2) corrigées par ``surcharge`` (contraintes « regles »)."""
    rg = copy.deepcopy(regles or {})
    if not surcharge:
        return rg
    s = surcharge
    rr = rg.setdefault("reseaux", {})
    if "largeur_defaut" in s:
        ancien = rg.get("largeur_defaut")
        neuf = list(s["largeur_defaut"])
        for v in rr.values():
            if not v.get("regle_largeur_propre") and (ancien is None or v.get("largeurs") == ancien):
                v["largeurs"] = list(neuf)
        rg["largeur_defaut"] = neuf
    if "via" in s:
        ancien = dict(rg.get("via") or {})
        neuf = {**ancien, **s["via"]}
        for v in rr.values():
            if v.get("via") is None or v.get("via") == ancien:
                v["via"] = dict(neuf)
        rg["via"] = neuf
    if "isolement" in s:
        iso = rg.setdefault("isolement", {})
        n = len(iso.get("classes") or [None])
        if "matrice" in s["isolement"]:
            m = s["isolement"]["matrice"]
            if len(m) != n or any(len(r) != n for r in m):
                raise ErreurRegles(f"regles.isolement.matrice : {n} × {n} valeurs attendues (une par classe)")
            iso["matrice"] = [list(r) for r in m]
        if "defaut" in s["isolement"]:
            iso["defaut"] = s["isolement"]["defaut"]
    for k in ("trou_trou", "garde_bord", "isolement_pieces", "masque_serigraphie"):
        if k in s:
            rg[k] = s[k]
    if "hauteurs" in s:
        rg["hauteurs"] = {**(rg.get("hauteurs") or {}), **s["hauteurs"]}
    if "connexion_plans" in s:
        rg["connexion_plans"] = {**(rg.get("connexion_plans") or {"connexion": "relief"}), **s["connexion_plans"]}
    if "tolerances_longueur" in s:
        rg["tolerances_longueur"] = {**(rg.get("tolerances_longueur") or {}), **s["tolerances_longueur"]}
    for n, val in (s.get("reseaux") or {}).items():
        if n not in rr:
            raise ErreurRegles(f"regles.reseaux.{n} : réseau inconnu dans la carte")
        if "largeurs" in val:
            rr[n]["largeurs"] = list(val["largeurs"])
            rr[n]["regle_largeur_propre"] = True
        if "via" in val:
            rr[n]["via"] = {**(rr[n].get("via") or rg.get("via") or {}), **val["via"]}
    return rg
