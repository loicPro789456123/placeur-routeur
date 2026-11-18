"""Test d'aller-retour : lire(fichier) -> modèle -> écrire, comparaison flux par flux octet pour octet.

Usage : python aller_retour.py CARTE.PcbDoc [CARTE.PcbDoc ...] --sortie DOSSIER [--modifications]

Pour chaque carte : chaque flux est décodé puis ré-encodé ; le fichier entier est réécrit dans DOSSIER
(<nom>_aller_retour.PcbDoc), relu par olefile (lecteur indépendant) et comparé flux par flux à l'original.
--modifications écrit aussi <nom>_modifie.PcbDoc et <nom>_modifie.txt : un composant déplacé, une piste et
une via ajoutées, un texte libre déplacé (modifications simples, vérifiables à l'œil dans Altium), choisis
d'après leurs propriétés, jamais d'après un nom propre à une carte. Aucun fichier n'est lu ni écrit par défaut.
Codes de sortie : 0 tous les flux identiques ; 1 au moins un écart (ou une modification impossible) ;
2 entrée invalide ; 4 erreur interne.
"""
import os
import sys

import olefile

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

from altium_fichier import PcbDoc  # noqa: E402
from altium_fichier import commandes as K  # noqa: E402
from altium_fichier.cfb import lire_cfb  # noqa: E402
from altium_fichier.flux import decoder_flux  # noqa: E402
from altium_fichier.pcbdoc import ErreurPcbDoc  # noqa: E402


def flux_olefile(chemin):
    o = olefile.OleFileIO(chemin)
    try:
        return {"/".join(e): o.openstream(e).read() for e in o.listdir(streams=True, storages=False)}
    finally:
        o.close()


def tester(chemin, sortie):
    """(ok, détail) : réencodage de chaque flux, puis fichier réécrit et relu par olefile."""
    PcbDoc.ouvrir(chemin)  # contrôle d'entrée (ErreurPcbDoc si ce n'est pas un .PcbDoc)
    orig = flux_olefile(chemin)
    racine = lire_cfb(chemin)
    ko_flux = [c for c, n in racine.flux() if decoder_flux(c, n.donnees).encoder() != n.donnees]
    doc = PcbDoc(racine, chemin)
    doc.enregistrer(sortie)
    relu = flux_olefile(sortie)
    manquants = sorted(set(orig) - set(relu))
    en_trop = sorted(set(relu) - set(orig))
    differents = sorted(c for c in orig if c in relu and orig[c] != relu[c])
    ok = not (ko_flux or manquants or en_trop or differents)
    return ok, {"flux": len(orig), "octets": sum(len(v) for v in orig.values()), "ko_codec": ko_flux,
                "manquants": manquants, "en_trop": en_trop, "differents": differents,
                "taille_orig": os.path.getsize(chemin), "taille_ecrit": os.path.getsize(sortie)}


def fabriquer_modifie(chemin, sortie):
    """Fichier d'essai pour Altium : modifications simples choisies d'après les propriétés des objets.
    Rend (journal, nombre de modifications impossibles)."""
    doc = PcbDoc.ouvrir(chemin)
    journal, echecs = [], 0
    comps = [c for c in doc.composants() if not c["verrouille"] and c["face"] == "TOP"]
    if comps:
        c = min(comps, key=lambda c: c["designateur"])
        n = doc.deplacer_composant(c["designateur"], dx_mil=100, dy_mil=0)
        journal.append(f"composant {c['designateur']} déplacé de +100 mil en X ({n} primitives suivies), "
                       f"de ({c['x_mil']:.3f}, {c['y_mil']:.3f}) mil à ({c['x_mil'] + 100:.3f}, {c['y_mil']:.3f}) mil")
    else:
        journal.append("aucun composant déplaçable (non verrouillé, face TOP) : déplacement non fait")
        echecs += 1
    pts = [(s["x"], s["y"]) for s in doc.contour_carte()]
    x0, y0 = (min(p[0] for p in pts) + 100.0, min(p[1] for p in pts) + 100.0) if pts else (100.0, 100.0)
    reseau = next((p["reseau"] for p in doc.pastilles() if p["reseau"]), None)
    try:
        ip = doc.ajouter_piste("TOP", (x0, y0), (x0 + 200, y0), 10, reseau=reseau)
        journal.append(f"piste ajoutée (Tracks6 n°{ip}) TOP, réseau {reseau}, ({x0:.3f}, {y0:.3f}) -> "
                       f"({x0 + 200:.3f}, {y0:.3f}) mil, largeur 10 mil")
        iv = doc.ajouter_via((x0 + 200, y0), 23.622, 11.811, reseau=reseau)
        journal.append(f"via ajoutée (Vias6 n°{iv}), réseau {reseau}, en ({x0 + 200:.3f}, {y0:.3f}) mil, "
                       f"diamètre 23,622 mil, trou 11,811 mil")
    except (IndexError, ErreurPcbDoc) as e:
        journal.append(f"ajout de piste ou de via impossible sur cette carte ({K.message_sans_chemin(e)})")
        echecs += 1
    textes = [t for t in doc.textes() if t["composant"] == 0xFFFF]
    if textes:
        t = textes[0]
        doc.deplacer_texte(t["index"], dx_mil=0, dy_mil=50)
        journal.append(f"texte n°{t['index']} « {t['texte']} » ({t['couche']}) déplacé de +50 mil en Y")
    doc.enregistrer(sortie)
    relu = PcbDoc.ouvrir(sortie)
    if len(relu.pistes()) != len(doc.pistes()) or len(relu.vias()) != len(doc.vias()):
        journal.append("relecture : nombres de pistes ou de vias différents")
        echecs += 1
    flux_olefile(sortie)
    return journal, echecs


def parseur():
    p = K.Parseur(prog="aller_retour.py",
                  description="Aller-retour lecture -> écriture de fichiers .PcbDoc, comparé flux par flux.")
    p.add_argument("cartes", nargs="+", metavar="CARTE.PcbDoc", help="fichiers à éprouver")
    p.add_argument("--sortie", required=True, metavar="DOSSIER", help="dossier des fichiers réécrits (créé si besoin)")
    p.add_argument("--modifications", action="store_true",
                   help="écrire aussi <nom>_modifie.PcbDoc (fichier d'essai pour Altium)")
    return p


def _executer(a):
    K.verifier_fichiers(a.cartes)
    os.makedirs(a.sortie, exist_ok=True)
    tout_ok = True
    for f in a.cartes:
        nom = os.path.splitext(os.path.basename(f))[0]
        sortie = os.path.join(a.sortie, f"{nom}_aller_retour.PcbDoc")
        if os.path.abspath(sortie) == os.path.abspath(f):
            raise K.ErreurEntree(f"{os.path.basename(f)} : la sortie écraserait la carte lue")
        ok, r = tester(f, sortie)
        tout_ok &= ok
        print(f"{'OK ' if ok else 'ÉCHEC'} {os.path.basename(f)} : {r['flux']} flux, {r['octets']} octets ; "
              f"codec KO {len(r['ko_codec'])}, manquants {len(r['manquants'])}, en trop {len(r['en_trop'])}, "
              f"différents {len(r['differents'])} ; taille {r['taille_orig']} -> {r['taille_ecrit']} octets")
        if not ok:
            print(f"   détail : codec KO {r['ko_codec'][:10]}, manquants {r['manquants'][:10]}, "
                  f"en trop {r['en_trop'][:10]}, différents {r['differents'][:10]}")
        if a.modifications:
            sortie_m = os.path.join(a.sortie, f"{nom}_modifie.PcbDoc")
            journal, echecs = fabriquer_modifie(f, sortie_m)
            tout_ok &= not echecs
            texte = f"Modifications de {os.path.basename(sortie_m)} :\n" + "\n".join("- " + x for x in journal)
            print(texte)
            K.ecrire_texte_atomique(os.path.join(a.sortie, f"{nom}_modifie.txt"), texte + "\n")
    return K.CODE_OK if tout_ok else K.CODE_NEGATIF


def main(argv=None) -> int:
    return K.executer(_executer, parseur(), argv)


if __name__ == "__main__":
    sys.exit(main())
