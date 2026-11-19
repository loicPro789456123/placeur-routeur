# -*- coding: utf-8 -*-
"""Aide aux flux de la CI (§7.6, placeur.yml) : listes de graines, cartes « chaîne », contrôle d'une sortie.

  python ci_graines.py graines [--nuit] [--jeux nominal,compatibilite] [--fichier ci_campagne.json] [--github]
      matrice JSON {"include": [{"jeu", "donnees", "graine"}, …]} (jeux × graines, au plus 256 entrées, limite d'une
      matrice GitHub Actions ; au-delà : code 2). --github : ligne « matrice=… » pour $GITHUB_OUTPUT.
  python ci_graines.py cartes CORPUS [--github]
      cartes « chaîne » du corpus (attendu_deduction.json, champ « chaine » non nul) : [{"nom", "carte", "niveau",
      "graines", "graines_valides_min"}] ; --github : ligne « cartes=… ».
  python ci_graines.py controler SORTIE [--valides-min N]
      code 0 seulement si la dernière sortie de placeur.py sous SORTIE a un rapport.json valide, au moins N graines
      valides (défaut 1), aucune incohérence, et un livrable .kicad_pcb produit et relu ; code 1 sinon, 2 si SORTIE
      ne contient aucune sortie.

Aucune variable d'environnement lue."""
import json
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
MATRICE_MAX = 256
FICHIER = os.path.join(ICI, "ci_campagne.json")


class ErreurCI(Exception):
    """Entrée inutilisable : message, code 2."""


def lire_graines(liste) -> list[int]:
    """« 3001-3020 », 4005, ou une liste de ces éléments (texte séparé par des virgules accepté) → graines triées,
    sans doublon."""
    if isinstance(liste, (int, str)):
        liste = [liste]
    res = set()
    for e in liste:
        for morceau in (str(e).split(",") if isinstance(e, str) else [e]):
            m = str(morceau).strip()
            if not m:
                continue
            if "-" in m[1:]:
                a, b = (int(x) for x in m.split("-", 1))
                if b < a:
                    raise ErreurCI(f"intervalle de graines vide : {m}")
                res.update(range(a, b + 1))
            else:
                res.add(int(m))
    if any(g < 0 for g in res):
        raise ErreurCI("graine négative")
    return sorted(res)


def lire_config(chemin: str = FICHIER) -> dict:
    with open(chemin, encoding="utf-8-sig") as f:
        c = json.load(f)
    for cle in ("graines", "jeux", "qualite"):
        if cle not in c:
            raise ErreurCI(f"{os.path.basename(chemin)} : clé « {cle} » manquante")
    return c


def matrice(config: dict, nuit: bool = False, jeux: list[str] | None = None) -> dict:
    graines = lire_graines(config.get("graines_nuit", config["graines"]) if nuit else config["graines"])
    noms = jeux or list(config["jeux"])
    inconnus = [j for j in noms if j not in config["jeux"]]
    if inconnus:
        raise ErreurCI(f"jeu inconnu : {', '.join(inconnus)} (connus : {', '.join(config['jeux'])})")
    include = [{"jeu": j, "donnees": config["jeux"][j], "graine": g} for j in noms for g in graines]
    if len(include) > MATRICE_MAX:
        raise ErreurCI(f"{len(include)} entrées : une matrice GitHub Actions en accepte au plus {MATRICE_MAX}")
    return {"include": include}


def cartes_chaine(corpus: str) -> list[dict]:
    racine = os.path.join(corpus, "cartes") if os.path.isdir(os.path.join(corpus, "cartes")) else corpus
    res = []
    for nom in sorted(os.listdir(racine)):
        f = os.path.join(racine, nom, "attendu_deduction.json")
        if not os.path.isfile(f):
            continue
        with open(f, encoding="utf-8-sig") as fl:
            chaine = json.load(fl).get("chaine")
        if not chaine:
            continue
        carte = os.path.join(racine, nom, f"{nom}.kicad_pcb")
        if not os.path.isfile(carte):
            raise ErreurCI(f"{nom} : {nom}.kicad_pcb absent")
        res.append({"nom": nom, "carte": carte.replace(os.sep, "/"), "niveau": chaine.get("niveau"),
                    "graines": chaine.get("graines"), "graines_valides_min": chaine.get("graines_valides_min", 1)})
    return res


def relire_kicad(chemin: str) -> None:
    """Relit le .kicad_pcb produit par l'importeur (lot B) ; exception si illisible."""
    import importer_carte
    e = importer_carte.importer(chemin)
    if not e:
        raise ValueError("lecture vide")


def controler(sortie: str, valides_min: int = 1, relire=relire_kicad) -> tuple[int, list[str]]:
    import formats
    calculs = []
    for d, sous, fichiers in os.walk(sortie):
        if os.path.basename(d) == "technique" and "rapport.json" in fichiers:
            calculs.append(os.path.dirname(d))
            sous[:] = []
    if not calculs:
        return 2, ["aucune sortie de placeur.py (technique/rapport.json) sous ce dossier"]
    calcul = sorted(calculs)[-1]
    with open(os.path.join(calcul, "technique", "rapport.json"), encoding="utf-8-sig") as f:
        r = json.load(f)
    L, ok = [], True
    erreurs = formats.valider_rapport(r)
    L.append(f"rapport.json : {'valide' if not erreurs else 'refusé : ' + '; '.join(map(str, erreurs[:3]))}")
    ok &= not erreurs
    valides = [g["graine"] for g in r.get("graines") or [] if g.get("valide")]
    L.append(f"graines valides : {len(valides)} sur {len(r.get('graines') or [])} (minimum {valides_min})")
    ok &= len(valides) >= valides_min
    inc = int(r.get("incoherences") or 0)
    L.append(f"incohérences : {inc}")
    ok &= inc == 0
    liv = r.get("livrable") or {}
    fichier = os.path.join(calcul, liv["fichier"]) if liv.get("fichier") else None
    if not fichier or not fichier.endswith(".kicad_pcb") or not os.path.isfile(fichier):
        L.append("carte produite .kicad_pcb : absente")
        ok = False
    else:
        try:
            relire(fichier)
            L.append(f"carte produite {os.path.basename(fichier)} : relue")
        except Exception as e:  # noqa: BLE001 - toute erreur de relecture est un échec du contrôle
            L.append(f"carte produite {os.path.basename(fichier)} : illisible ({type(e).__name__})")
            ok = False
    return (0 if ok else 1), L


def main(argv: list[str] | None = None, flux=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    flux = sys.stdout if flux is None else flux
    try:
        if not argv or argv[0] not in ("graines", "cartes", "controler"):
            raise ErreurCI("sous-commande attendue : graines, cartes ou controler")
        cmd, reste = argv[0], argv[1:]
        github = "--github" in reste
        reste = [a for a in reste if a != "--github"]

        def option(nom, defaut=None):
            if nom in reste:
                i = reste.index(nom)
                if i + 1 >= len(reste):
                    raise ErreurCI(f"option {nom} : valeur manquante")
                v = reste[i + 1]
                del reste[i:i + 2]
                return v
            return defaut
        if cmd == "graines":
            nuit = "--nuit" in reste
            reste = [a for a in reste if a != "--nuit"]
            fichier = option("--fichier", FICHIER)
            jeux = option("--jeux")
            if reste:
                raise ErreurCI(f"argument inattendu : {reste[0]}")
            m = matrice(lire_config(fichier), nuit, jeux.split(",") if jeux else None)
            texte = json.dumps(m, ensure_ascii=False, separators=(",", ":"))
            flux.write(("matrice=" + texte if github else texte) + "\n")
            return 0
        if cmd == "cartes":
            if len(reste) != 1:
                raise ErreurCI("usage : ci_graines.py cartes CORPUS [--github]")
            texte = json.dumps(cartes_chaine(reste[0]), ensure_ascii=False, separators=(",", ":"))
            flux.write(("cartes=" + texte if github else texte) + "\n")
            return 0
        vmin = int(option("--valides-min", "1"))
        if len(reste) != 1:
            raise ErreurCI("usage : ci_graines.py controler SORTIE [--valides-min N]")
        code, lignes = controler(reste[0], vmin)
        flux.write("\n".join(lignes) + f"\ncontrôle : {'réussi' if code == 0 else 'échoué'}\n")
        return code
    except (ErreurCI, OSError, ValueError, KeyError) as e:
        print(f"ci_graines : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        _f.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
