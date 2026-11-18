# -*- coding: utf-8 -*-
"""Paramètres par défaut du moteur (``parametres_moteur/1``, niveau ``normal``) : source unique, jamais recopiée.

Deux sources (§2.6 : « une seule source fait foi ») :

- le texte du contrat : le bloc JSON de l'annexe C de ``CONTRAT.md``, modifié par le bloc JSON de la partie D de
  ``AVENANT_2.2.md`` (chaque clé de premier niveau du bloc de l'avenant remplace celle de l'annexe : aujourd'hui la
  ligne ``conjointe``, ``essais_par_piece_mobile`` = 6,0, avenant A.1) ; aucune valeur n'est écrite dans ce script ;
- le moteur : la sortie de ``EXE parametres-defaut`` (critère E6, à l'intégration).

Puis ``--sortie F`` écrit le document (JSON UTF-8, LF, indentation 1, ordre des clés de la source ; écriture
atomique), ou ``--verifier F`` le compare au fichier F, valeurs et types JSON compris (6 et 6.0 diffèrent : l'annexe C
est « exactement » la sortie du moteur) et affiche « identique » ou les écarts.

Usage :
  python extraire_parametres.py --contrat CONTRAT.md --avenant AVENANT_2.2.md (--sortie F | --verifier F)
  python extraire_parametres.py --moteur EXE (--sortie F | --verifier F)

Codes (§1.4, outils Python) : 0 écrit ou identique ; 1 différent (écarts affichés) ; 2 entrée invalide (fichier
illisible, bloc introuvable, JSON invalide, moteur absent ou en échec) ; 4 erreur interne. Messages : noms de
fichiers seuls, jamais de dossier.
"""
import argparse
import json
import os
import subprocess
import sys
import time

TITRE_ANNEXE = "## Annexe C"
TITRE_AVENANT = "## Partie D"
DUREE_MAX_MOTEUR_S = 120


class Erreur(Exception):
    """Entrée invalide (code 2)."""


def _nom(chemin):
    return os.path.basename(os.fspath(chemin))


def _refuser(constante):
    raise ValueError(f"valeur {constante} non permise en JSON")


def _json(texte, origine):
    try:
        return json.loads(texte, parse_constant=_refuser)
    except json.JSONDecodeError as e:
        raise Erreur(f"{origine} : JSON invalide, ligne {e.lineno}, colonne {e.colno} : {e.msg}") from None
    except ValueError as e:
        raise Erreur(f"{origine} : {e}") from None


def _lire(chemin):
    try:
        with open(chemin, "rb") as f:
            return f.read().decode("utf-8-sig")
    except FileNotFoundError:
        raise Erreur(f"{_nom(chemin)} : fichier introuvable") from None
    except UnicodeDecodeError:
        raise Erreur(f"{_nom(chemin)} : encodage illisible (UTF-8 attendu)") from None
    except OSError:
        raise Erreur(f"{_nom(chemin)} : lecture impossible") from None


def bloc_json(texte, titre, origine):
    """Texte du premier bloc ```json qui suit la ligne de titre commençant par ``titre`` (titre de niveau 2 : le
    bloc doit précéder le titre de niveau 2 suivant)."""
    lignes = texte.splitlines()
    debut = next((i for i, l in enumerate(lignes) if l.startswith(titre)), None)
    if debut is None:
        raise Erreur(f"{origine} : titre « {titre} » introuvable")
    contenu, dedans = [], False
    for ligne in lignes[debut + 1:]:
        if not dedans and ligne.startswith("## "):
            break
        if not dedans and ligne.strip() == "```json":
            dedans = True
        elif dedans and ligne.strip() == "```":
            return "\n".join(contenu)
        elif dedans:
            contenu.append(ligne)
    raise Erreur(f"{origine} : bloc ```json introuvable sous « {titre} »")


def depuis_contrat(texte_contrat, texte_avenant, nom_contrat="CONTRAT.md", nom_avenant="AVENANT_2.2.md"):
    """Annexe C du contrat modifiée par la partie D de l'avenant (clés de premier niveau remplacées)."""
    annexe = _json(bloc_json(texte_contrat, TITRE_ANNEXE, nom_contrat), f"{nom_contrat} (annexe C)")
    if not isinstance(annexe, dict) or annexe.get("format") != "parametres_moteur/1":
        raise Erreur(f"{nom_contrat} (annexe C) : objet « parametres_moteur/1 » attendu")
    fragment = bloc_json(texte_avenant, TITRE_AVENANT, nom_avenant).strip().rstrip(",")
    modification = _json("{" + fragment + "}", f"{nom_avenant} (partie D)")
    for cle, valeur in modification.items():
        if cle not in annexe:
            raise Erreur(f"{nom_avenant} (partie D) : clé {cle} absente de l'annexe C")
        annexe[cle] = valeur
    return annexe


def depuis_moteur(exe):
    """Sortie JSON de ``EXE parametres-defaut``."""
    try:
        p = subprocess.run([exe, "parametres-defaut"], capture_output=True, timeout=DUREE_MAX_MOTEUR_S)
    except FileNotFoundError:
        raise Erreur(f"{_nom(exe)} : moteur introuvable") from None
    except subprocess.TimeoutExpired:
        raise Erreur(f"{_nom(exe)} parametres-defaut : pas de réponse en {DUREE_MAX_MOTEUR_S} s") from None
    except OSError:
        raise Erreur(f"{_nom(exe)} : lancement impossible") from None
    if p.returncode != 0:
        raise Erreur(f"{_nom(exe)} parametres-defaut : code {p.returncode}")
    try:
        texte = p.stdout.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Erreur(f"{_nom(exe)} parametres-defaut : sortie illisible (UTF-8 attendu)") from None
    return _json(texte, f"{_nom(exe)} parametres-defaut")


def texte_json(document):
    """Sérialisation fixe : UTF-8, indentation 1, ordre des clés de la source, LF final."""
    return json.dumps(document, ensure_ascii=False, indent=1) + "\n"


def ecarts(attendu, trouve, chemin=""):
    """Écarts entre deux documents JSON, types compris (entier, flottant, booléen distincts) ; liste de textes."""
    def nom_type(v):
        return {bool: "booléen", int: "entier", float: "flottant", str: "texte", list: "liste", dict: "objet",
                type(None): "null"}.get(type(v), type(v).__name__)

    lieu = chemin or "racine"
    if type(attendu) is not type(trouve):
        return [f"{lieu} : {nom_type(attendu)} {json.dumps(attendu)[:60]} attendu, {nom_type(trouve)} "
                f"{json.dumps(trouve)[:60]} trouvé"]
    if isinstance(attendu, dict):
        resultat = []
        for k in attendu:
            sous = f"{chemin}.{k}" if chemin else k
            if k not in trouve:
                resultat.append(f"{sous} : clé manquante")
            else:
                resultat += ecarts(attendu[k], trouve[k], sous)
        resultat += [f"{chemin + '.' if chemin else ''}{k} : clé en trop" for k in trouve if k not in attendu]
        return resultat
    if isinstance(attendu, list):
        if len(attendu) != len(trouve):
            return [f"{lieu} : liste de {len(attendu)} éléments attendue, {len(trouve)} trouvé(s)"]
        resultat = []
        for i, (a, t) in enumerate(zip(attendu, trouve)):
            resultat += ecarts(a, t, f"{chemin}[{i}]")
        return resultat
    return [] if attendu == trouve else [f"{lieu} : {json.dumps(attendu)} attendu, {json.dumps(trouve)} trouvé"]


def ecrire_atomique(chemin, texte):
    """Écriture atomique (fichier .tmp puis remplacement, 5 essais espacés de 0,2 s)."""
    temporaire = os.fspath(chemin) + ".tmp"
    with open(temporaire, "w", encoding="utf-8", newline="\n") as f:
        f.write(texte)
    for essai in range(5):
        try:
            os.replace(temporaire, chemin)
            return
        except PermissionError:
            if essai == 4:
                raise
            time.sleep(0.2)


def main(argv=None):
    for flux in (sys.stdout, sys.stderr):  # hors console (sortie redirigée) : UTF-8, caractère non codable remplacé
        try:
            if not flux.isatty() and hasattr(flux, "reconfigure"):
                flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    analyseur = argparse.ArgumentParser(prog="extraire_parametres.py", description=__doc__.splitlines()[0])
    source = analyseur.add_mutually_exclusive_group(required=True)
    source.add_argument("--contrat", help="CONTRAT.md (avec --avenant)")
    source.add_argument("--moteur", help="exécutable du moteur (placeur_routage)")
    analyseur.add_argument("--avenant", help="AVENANT_2.2.md (avec --contrat)")
    cible = analyseur.add_mutually_exclusive_group(required=True)
    cible.add_argument("--sortie", help="fichier à écrire")
    cible.add_argument("--verifier", help="fichier à comparer")
    args = analyseur.parse_args(argv)
    try:
        if args.contrat is not None:
            if args.avenant is None:
                raise Erreur("--avenant obligatoire avec --contrat")
            document = depuis_contrat(_lire(args.contrat), _lire(args.avenant), _nom(args.contrat),
                                      _nom(args.avenant))
        else:
            if args.avenant is not None:
                raise Erreur("--avenant ne va qu'avec --contrat")
            document = depuis_moteur(args.moteur)
        if args.sortie is not None:
            ecrire_atomique(args.sortie, texte_json(document))
            print(f"{_nom(args.sortie)} : écrit")
            return 0
        trouve = _json(_lire(args.verifier), _nom(args.verifier))
        differences = ecarts(document, trouve)
        if differences:
            print(f"{_nom(args.verifier)} : différent ({len(differences)} écart(s))")
            for e in differences[:200]:
                print(f"  {e}")
            return 1
        print(f"{_nom(args.verifier)} : identique")
        return 0
    except Erreur as e:
        print(e, file=sys.stderr)
        return 2
    except Exception as e:  # erreur interne : jamais de trace ni de chemin
        print(f"extraire_parametres.py : erreur interne ({type(e).__name__})", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
