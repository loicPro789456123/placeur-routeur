# -*- coding: utf-8 -*-
"""Campagne locale du placeur-routeur sur une liste de graines (§7.7, avenant 2.2, A.7), par placeur.py.

  python campagnes.py --donnees D.json --graines 3001-3020,4001-4020 --sortie S
                      [--qualite normal] [--processeurs P] [--moteurs DOSSIER]
                      [--reference NOM=DOSSIER ...] [--reference-cout X] [--comparer REF ...] [--marge-cout 0,04]

- Chaque suite de graines consécutives est un lancement de `placeur.py --donnees D --graine-depart G --essais N
  --qualite Q --sans-pcbdoc` dans S/calculs/ : c'est ce qui est livré qui est mesuré (§1.1), avec ses contrôles
  indépendants (§4.6-4), jamais les étapes une à une.
- --reference NOM=DOSSIER : routage de référence (placements_routage.json et routage_detaille.json, par exemple
  exemples/metroid_v2/references/rev4/) recopié dans S/references/NOM/ puis évalué par les mêmes contrôles que les
  graines ; son coût commun sert de référence de coût si --reference-cout n'est pas donné.
- --comparer REF : campagne de référence appariée par graine (règle NI(m ; d), voir ci_fusion.py).
- Résultat : S/campagne/rapport.json (rapport/2 avec `statistiques`, `comparaison` et `references_evaluees`) et
  S/campagne/resume.md ; codes : 0 rapport écrit, 1 rapport refusé par formats.valider_rapport, 2 entrée invalide
  ou code 2 de placeur.py.

Aucune variable d'environnement lue ; bibliothèque standard seulement (§0.2-3)."""
import json
import os
import shutil
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import chemins  # noqa: E402
import ci_fusion  # noqa: E402
import ci_graines  # noqa: E402
import formats  # noqa: E402
import placeur  # noqa: E402

FICHIERS_REFERENCE = ("placements_routage.json", "routage_detaille.json")


class ErreurCampagne(Exception):
    """Entrée invalide : message, code 2."""


def suites(graines: list[int]) -> list[tuple[int, int]]:
    """Graines triées → [(première, nombre)] de suites consécutives."""
    res = []
    for g in graines:
        if res and res[-1][0] + res[-1][1] == g:
            res[-1] = (res[-1][0], res[-1][1] + 1)
        else:
            res.append((g, 1))
    return res


def evaluer_reference(nom: str, dossier: str, sortie: str, chemin_donnees: str, donnees: dict,
                      controles: placeur.Controles) -> dict:
    """Recopie la référence (jamais écrite sur place) et la juge par les contrôles de §4.6-4."""
    cible = os.path.join(sortie, "references", nom)
    os.makedirs(cible, exist_ok=True)
    for f in FICHIERS_REFERENCE:
        source = os.path.join(dossier, f)
        if not os.path.isfile(source):
            raise ErreurCampagne(f"référence {nom} : {f} absent")
        shutil.copyfile(source, os.path.join(cible, f))
    res = controles.executer(cible, chemin_donnees, donnees, sortie=sortie)
    e = placeur.entree_graine({"graine": 0, "etat": "terminee", "stats": {"valide": True}, "controles": res})
    return {"nom": nom, "dossier": f"references/{nom}", "valide": e["valide"], "cout_commun": e["cout_commun"],
            "placement": e["placement"], "drc_strict": e["drc_strict"], "drc_altium": e["drc_altium"],
            "avertissements": res.get("avertissements", [])}


def analyser(argv: list[str]) -> dict:
    o = {"--donnees": None, "--graines": None, "--sortie": None, "--qualite": "normal", "--processeurs": None,
         "--moteurs": None, "--reference": [], "--reference-cout": None, "--comparer": [],
         "--marge-cout": str(ci_fusion.MARGE_COUT)}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a not in o:
            raise ErreurCampagne(f"option inconnue : {a}")
        if i + 1 >= len(argv):
            raise ErreurCampagne(f"option {a} : valeur manquante")
        if isinstance(o[a], list):
            o[a].append(argv[i + 1])
        else:
            o[a] = argv[i + 1]
        i += 2
    for cle in ("--donnees", "--graines", "--sortie"):
        if not o[cle]:
            raise ErreurCampagne(f"{cle} attendu")
    return o


def main(argv: list[str] | None = None, *, controles: placeur.Controles | None = None, flux=None,
         flux_erreur=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    flux = sys.stdout if flux is None else flux
    flux_erreur = sys.stderr if flux_erreur is None else flux_erreur
    controles = controles or placeur.Controles()
    try:
        o = analyser(argv)
        graines = ci_graines.lire_graines(o["--graines"])
        if not graines:
            raise ErreurCampagne("aucune graine")
        sortie = os.path.abspath(o["--sortie"])
        calculs = os.path.join(sortie, "calculs")
        os.makedirs(calculs, exist_ok=True)
        chemin_donnees = os.path.abspath(o["--donnees"])
        donnees = formats.charger(chemin_donnees, "donnees_placeur")
        references = []
        for r in o["--reference"]:
            nom, _, dossier = r.partition("=")
            if not nom or not dossier:
                raise ErreurCampagne(f"--reference {r} : NOM=DOSSIER attendu")
            references.append(evaluer_reference(nom, dossier, sortie, chemin_donnees, donnees, controles))
            flux.write(f"référence {nom} : valide {references[-1]['valide']}, coût commun "
                       f"{references[-1]['cout_commun']}\n")
        for debut, n in suites(graines):
            args = ["--donnees", chemin_donnees, "--sortie", calculs, "--graine-depart", str(debut), "--essais",
                    str(n), "--qualite", o["--qualite"], "--sans-pcbdoc"]
            if o["--processeurs"]:
                args += ["--processeurs", o["--processeurs"]]
            if o["--moteurs"]:
                args += ["--moteurs", o["--moteurs"]]
            code = placeur.main(args, controles=controles, flux=flux, flux_erreur=flux_erreur)
            flux.write(f"graines {debut} à {debut + n - 1} : code {code}\n")
            if code == 2:
                return 2
        ref = ci_fusion._nombre(o["--reference-cout"]) if o["--reference-cout"] else next(
            (r["cout_commun"] for r in references if r["cout_commun"] is not None), None)
        comp = ci_fusion.graines_reference(o["--comparer"]) if o["--comparer"] else None
        rapport = ci_fusion.fusionner(ci_fusion.chercher_rapports([calculs]), ref, comp,
                                      ci_fusion._nombre(o["--marge-cout"]))
        rapport["references_evaluees"] = references
        erreurs = formats.valider_rapport(rapport)
        dossier = os.path.join(sortie, "campagne")
        os.makedirs(dossier, exist_ok=True)
        chemins.ecrire_json(os.path.join(dossier, "rapport.json"), rapport)
        md = ci_fusion.resume(rapport, f"Campagne locale {rapport['carte']} ({o['--qualite']})")
        md += "".join(f"\nRéférence {r['nom']} : valide {ci_fusion._fr(bool(r['valide']))}, coût commun "
                      f"{ci_fusion._fr(r['cout_commun'])}\n" for r in references)
        with open(os.path.join(dossier, "resume.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        flux.write(md)
        if erreurs:
            flux_erreur.write(f"campagnes : rapport refusé par formats.valider_rapport : {erreurs[:3]}\n")
            return 1
        return 0
    except (ErreurCampagne, ci_graines.ErreurCI, ci_fusion.ErreurFusion, formats.ErreurFormat, OSError,
            ValueError) as e:
        flux_erreur.write(f"campagnes : {e}\n")
        return 2


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        _f.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
