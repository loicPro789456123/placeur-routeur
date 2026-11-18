"""Inventaire exhaustif des flux d'un ou plusieurs .PcbDoc, avec taux d'octets décodés.

Usage : python inventaire.py CARTE.PcbDoc [CARTE.PcbDoc ...] [--rapports DOSSIER] [--inconnus FICHIER]

Affiche le résumé de chaque carte. --rapports écrit inventaire_<nom>.md (table de tous les flux) dans DOSSIER ;
--inconnus écrit le recensement des champs inconnus ou supposés (INCONNUS.md) dans FICHIER. Aucun fichier n'est
lu ni écrit par défaut.
Codes de sortie : 0 succès ; 2 entrée invalide (option, fichier introuvable ou qui n'est pas un .PcbDoc) ;
4 erreur interne.
"""
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

from altium_fichier import PcbDoc  # noqa: E402
from altium_fichier import commandes as K  # noqa: E402
from altium_fichier import gabarits as G  # noqa: E402
from altium_fichier.analyse import inventaire, recenser_inconnus  # noqa: E402


def _pct(a, b):
    return 100.0 * a / b if b else 100.0


def tableau_inventaire(nom, lignes):
    tot = {k: sum(l[k] for l in lignes) for k in ("taille", "C", "S", "I")}
    sans_modeles = [l for l in lignes if not l["flux"].startswith("Models/") or not l["flux"][7:].isdigit()]
    t2 = {k: sum(l[k] for l in sans_modeles) for k in ("taille", "C", "S", "I")}
    out = [f"# Inventaire : {nom}", "",
           f"- {len(lignes)} flux, {tot['taille']} octets de données.",
           f"- Octets structurés (certain + supposé) : {_pct(tot['C'] + tot['S'], tot['taille']):.2f} % ; "
           f"certains : {_pct(tot['C'], tot['taille']):.2f} % ; inconnus : {tot['I']} octets "
           f"({_pct(tot['I'], tot['taille']) if tot['taille'] else 0.0:.2f} %).",
           f"- Hors modèles 3D STEP (Models/N) : {t2['taille']} octets, structurés {_pct(t2['C'] + t2['S'], t2['taille']):.2f} %, "
           f"certains {_pct(t2['C'], t2['taille']):.2f} %, inconnus {t2['I']} octets.", "",
           "Légende : C = certain, S = supposé, I = inconnu (conservé brut). Pour les flux de propriétés texte, "
           "la structure est entièrement décodée ; C/S/I se rapporte au sens des clés (dictionnaire cles.py).", "",
           "| flux | octets | nature | enr. | C | S | I | % structuré | % certain |",
           "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
    modeles = [l for l in lignes if l["flux"].startswith("Models/") and l["flux"][7:].isdigit()]
    for l in lignes:
        if l in modeles:
            continue
        out.append(f"| {l['flux']} | {l['taille']} | {l['nature']} | {'' if l['nb'] is None else l['nb']} | "
                   f"{l['C']} | {l['S']} | {l['I']} | {l['taux_structure']:.1f} | {l['taux_certain']:.1f} |")
    if modeles:
        tm = sum(l["taille"] for l in modeles)
        ok = sum(1 for l in modeles if l["C"] == l["taille"])
        out.append(f"| Models/0..{len(modeles) - 1} ({len(modeles)} flux) | {tm} | modele3d (STEP zlib) | | {sum(l['C'] for l in modeles)} | 0 | "
                   f"{sum(l['I'] for l in modeles)} | {_pct(sum(l['C'] for l in modeles), tm):.1f} | {ok}/{len(modeles)} décompressibles |")
    return "\n".join(out) + "\n"


def rapport_inconnus(res, noms):
    R = res["resume"]
    out = ["# Champs inconnus ou supposés (régénéré par inventaire.py)", "",
           f"Fichiers analysés : {', '.join(noms)}.", "",
           "Tout octet listé ici est **conservé brut** à la lecture et réécrit à l'identique. "
           "Les valeurs observées (valeur×occurrences) servent à choisir les expériences différentielles "
           "(voir EXPERIENCES_A_FAIRE.md).", "",
           "## 1. Champs binaires inconnus (gabarits)", "",
           "| sous-enregistrement | champ | taille | valeurs observées |", "|---|---|---:|---|"]
    for (gab, ch), cnt in sorted(res["champs"].items()):
        tl = "/".join(str(t) for t in sorted(res["tailles"][(gab, ch)]))
        out.append(f"| {gab} | {ch} | {tl} | {R(cnt).replace('|', '¦')} |")
    out += ["", "## 2. Champs binaires « supposés » (sens déduit, non confirmé)", "",
            "| gabarit | décalage | champ | description |", "|---|---:|---|---|"]
    for nomg in ("PISTE", "ARC", "REMPLI", "VIA", "PASTILLE", "PASTILLE_PILE", "TEXTE", "ENTETE_REGION", "CONNEXION"):
        for off, ch, typ, unite, st, desc in getattr(G, nomg):
            if st == "S":
                out.append(f"| {nomg} | {off} | {ch} | {desc} |")
    out += ["", "## 2 bis. Champs confirmés par expérience dans Altium", "",
            "| gabarit | décalage | champ | description |", "|---|---:|---|---|"]
    for nomg in ("PISTE", "ARC", "REMPLI", "VIA", "PASTILLE", "PASTILLE_PILE", "TEXTE", "ENTETE_REGION", "CONNEXION"):
        for off, ch, typ, unite, st, desc in getattr(G, nomg):
            if st == "E":
                out.append(f"| {nomg} | {off} | {ch} | {desc} |")
    out += ["", "## 3. Sous-enregistrements 2 à 4 des pastilles (chaînes courtes, sens inconnu)", ""]
    for k, cnt in res["pascal"].items():
        out.append(f"- {k} : {R(cnt)}")
    out += ["", "## 4. Clés de propriétés non reconnues par le dictionnaire", ""]
    if not res["cles"]:
        out.append("Aucune : toutes les clés rencontrées sont rattachées à une description (statut C ou S).")
    for st, cnt in sorted(res["cles"].items()):
        out.append(f"- **{st}** : " + ", ".join(f"`{k}`×{n} (ex. `{res['exemples'][(st, k)]}`)" for k, n in cnt.most_common()))
    out += ["", "## 5. Flux conservés bruts (structure interne non décodée)", ""]
    for c, n in sorted(res["bruts"].items()):
        out.append(f"- `{c}` : {n} octets")
    out += ["", "## 6. Restes non structurés dans des flux décodés", ""]
    for c, n in sorted(res["restes"].items()):
        out.append(f"- `{c}` : {n} octets")
    return "\n".join(out) + "\n"


def parseur():
    p = K.Parseur(prog="inventaire.py",
                  description="Inventaire des flux de fichiers .PcbDoc (taux d'octets décodés, champs inconnus).")
    p.add_argument("cartes", nargs="+", metavar="CARTE.PcbDoc", help="fichiers à analyser")
    p.add_argument("--rapports", metavar="DOSSIER", help="dossier où écrire inventaire_<nom>.md (créé si besoin)")
    p.add_argument("--inconnus", metavar="FICHIER", help="fichier où écrire le recensement des champs inconnus")
    return p


def _executer(a):
    K.verifier_fichiers(a.cartes)
    if a.rapports:
        os.makedirs(a.rapports, exist_ok=True)
    docs, noms = [], []
    for f in a.cartes:
        doc = PcbDoc.ouvrir(f)
        docs.append(doc)
        nom = os.path.basename(f)
        noms.append(nom)
        md = tableau_inventaire(nom, inventaire(doc))
        if a.rapports:
            K.ecrire_texte_atomique(os.path.join(a.rapports, f"inventaire_{os.path.splitext(nom)[0]}.md"), md)
        print(md.split("\n\n")[1])
    if a.inconnus:
        K.ecrire_texte_atomique(a.inconnus, rapport_inconnus(recenser_inconnus(docs), noms))
        print(f"{K.nom_seul(a.inconnus)} écrit.")
    return K.CODE_OK


def main(argv=None) -> int:
    return K.executer(_executer, parseur(), argv)


if __name__ == "__main__":
    sys.exit(main())
