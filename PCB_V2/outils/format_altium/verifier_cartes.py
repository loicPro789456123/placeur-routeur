"""Vérification de la bibliothèque altium_fichier sur des cartes .PcbDoc réelles.

Usage : python -B verifier_cartes.py CARTE_OU_DOSSIER [...] [--exclure NOM ...] [--travail DOSSIER] [--json FICHIER]

Pour chaque .PcbDoc désigné (un dossier : ses fichiers .PcbDoc, sans sous-dossier), phase par phase et chronométré :
1. ouverture (PcbDoc.ouvrir) ;
2. réencodage : chaque flux décodé puis réencodé redonne ses octets, octet pour octet ;
3. accesseurs : tous ceux de pcbdoc.ACCESSEURS, sans exception (durée de chacun ; pistes+vias+polygones) ;
4. aller-retour : le modèle lu est écrit puis relu par olefile (lecteur indépendant) : mêmes flux, mêmes octets ;
5. ajouts : une piste, une via et une région (couche TOP, premier réseau de la carte) ajoutées, fichier écrit,
   relu par PcbDoc et par olefile : comptes, valeurs, GUID de primitives, groupe de perçage de la via, et tous
   les flux hors des stockages touchés identiques à l'original (durées à part : appels d'ajout, écriture).
Le rapport ne donne que des noms de fichiers, jamais de chemin. Les fichiers écrits vont dans DOSSIER (--travail,
gardés) ou dans un dossier temporaire supprimé à la fin. Aucun fichier n'est lu ni écrit par défaut.
Codes de sortie : 0 toutes les cartes sans défaut ; 1 au moins un défaut (une carte qui ne s'ouvre pas en est
un) ; 2 entrée invalide (fichier ou dossier introuvable, aucune carte, option) ; 4 erreur interne.
"""
import json
import os
import shutil
import sys
import tempfile
import time

import olefile

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

from altium_fichier import PcbDoc, pose  # noqa: E402
from altium_fichier import commandes as K  # noqa: E402
from altium_fichier.pcbdoc import ACCESSEURS  # noqa: E402

# stockages que les ajouts ont le droit de modifier (tous les autres flux doivent rester identiques)
STOCKAGES_AJOUTS = ("Tracks6", "Vias6", "Regions6", "ShapeBasedRegions6", "PrimitiveGuids", "DrillManager")
PHASES = ("ouverture", "reencodage", "accesseurs", "aller_retour", "ajouts")
PRECISION = 1e-4  # mil : résolution des coordonnées binaires


def flux_olefile(chemin):
    """{chemin du flux: octets} lus par olefile."""
    o = olefile.OleFileIO(chemin)
    try:
        return {"/".join(e): o.openstream(e).read() for e in o.listdir(streams=True, storages=False)}
    finally:
        o.close()


def lister_cartes(entrees, exclure=()):
    """Chemins des .PcbDoc désignés (dossier : ses .PcbDoc triés, sans sous-dossier), sans doublon ni exclu.
    ErreurEntree : entrée introuvable, ou aucune carte."""
    exclus = {e.lower() for e in exclure}
    vus, out = set(), []
    for e in entrees:
        if os.path.isdir(e):
            noms = sorted(n for n in os.listdir(e) if n.lower().endswith(".pcbdoc")
                          and os.path.isfile(os.path.join(e, n)))
            chemins = [os.path.join(e, n) for n in noms]
        elif os.path.isfile(e):
            chemins = [e]
        else:
            raise K.ErreurEntree(f"{K.nom_seul(os.path.normpath(e))} : fichier ou dossier introuvable")
        for c in chemins:
            cle = os.path.normcase(os.path.abspath(c))
            if cle in vus or os.path.basename(c).lower() in exclus:
                continue
            vus.add(cle)
            out.append(c)
    if not out:
        raise K.ErreurEntree("aucune carte .PcbDoc à vérifier")
    return out


def _proche(a, b):
    return a is not None and b is not None and abs(a - b) <= PRECISION


def _point_ajout(doc):
    """Point d'ancrage des ajouts : coin bas-gauche de la boîte du contour + 100 mil (sinon des pastilles,
    sinon l'origine du fichier)."""
    pts = [(s["x"], s["y"]) for s in doc.contour_carte()]
    if not pts:
        pts = [(p["x"], p["y"]) for p in doc.pastilles() if p.get("x") is not None]
    if not pts:
        return 1000.0, 1000.0
    return min(p[0] for p in pts) + 100.0, min(p[1] for p in pts) + 100.0


def _ajouts(doc, orig, sortie, defauts, durees):
    """Phase 5 : ajouts, écriture, relecture et contrôles (défauts ajoutés à `defauts` ; durées des appels
    d'ajout et de l'écriture dans `durees`)."""
    x0, y0 = _point_ajout(doc)
    reseau = next((r for r in doc.reseaux() if r), None)
    couche = "TOP"
    n0 = {"pistes": len(doc.pistes()), "vias": len(doc.vias()), "regions": len(doc.regions()),
          "jumeaux": len(doc.data("ShapeBasedRegions6"))}
    carre = [(x0, y0 + 60.0), (x0 + 50.0, y0 + 60.0), (x0 + 50.0, y0 + 110.0), (x0, y0 + 110.0)]
    t = time.perf_counter()
    ip = doc.ajouter_piste(couche, (x0, y0), (x0 + 100.0, y0), 10.0, reseau=reseau)
    iv = doc.ajouter_via((x0 + 100.0, y0), 24.0, 12.0, reseau=reseau)
    ir = doc.ajouter_region(couche, carre, reseau)
    durees["appels_ajout"] = round(time.perf_counter() - t, 3)
    jumeau = len(doc.data("ShapeBasedRegions6")) == n0["jumeaux"] + 1
    t = time.perf_counter()
    doc.enregistrer(sortie)
    durees["ecriture"] = round(time.perf_counter() - t, 3)
    relu = PcbDoc.ouvrir(sortie)
    pistes, vias, regions = relu.pistes(), relu.vias(), relu.regions()
    if (len(pistes), len(vias), len(regions)) != (n0["pistes"] + 1, n0["vias"] + 1, n0["regions"] + 1):
        defauts.append(f"ajouts : comptes relus {len(pistes)}, {len(vias)}, {len(regions)} au lieu de "
                       f"{n0['pistes'] + 1}, {n0['vias'] + 1}, {n0['regions'] + 1}")
        return
    p, v, r = pistes[ip], vias[iv], regions[ir]
    if not (p["couche"] == couche and p["reseau"] == reseau and _proche(p["x1"], x0) and _proche(p["y1"], y0)
            and _proche(p["x2"], x0 + 100.0) and _proche(p["y2"], y0) and _proche(p["largeur"], 10.0)):
        defauts.append("ajouts : piste relue différente de la piste ajoutée")
    if not (v["reseau"] == reseau and _proche(v["x"], x0 + 100.0) and _proche(v["y"], y0)
            and _proche(v["diametre"], 24.0) and _proche(v["trou"], 12.0) and (v["de"], v["a"]) == ("TOP", "BOTTOM")):
        defauts.append("ajouts : via relue différente de la via ajoutée")
    if not (r["couche"] == couche and r["reseau"] == reseau and len(r["contour"]) == len(carre)
            and all(_proche(a[0], b[0]) and _proche(a[1], b[1]) for a, b in zip(r["contour"], carre))):
        defauts.append("ajouts : région relue différente de la région ajoutée")
    if jumeau and len(relu.data("ShapeBasedRegions6")) != len(relu.data("Regions6")):
        defauts.append("ajouts : Regions6 et ShapeBasedRegions6 de longueurs différentes")
    defauts.extend(f"ajouts : PrimitiveGuids : {e}" for e in pose.verifier_guids(relu))
    if relu.existe("DrillManager"):
        g = pose.groupe_via(relu.data("DrillManager"), 12.0)
        if g is None or iv not in g[1][1]:
            defauts.append("ajouts : via absente du groupe de perçage de 12 mil (DrillManager)")
    for c, n in relu.racine.flux():
        if relu.flux[c].encoder() != n.donnees:
            defauts.append(f"ajouts : flux {c} relu non réencodé à l'identique")
    ecrit = flux_olefile(sortie)
    touche = [c for c in set(orig) | set(ecrit) if c.split("/")[0] in STOCKAGES_AJOUTS]
    autres = sorted(c for c in set(orig) | set(ecrit) if c not in touche and orig.get(c) != ecrit.get(c))
    if autres:
        defauts.append(f"ajouts : {len(autres)} flux hors des stockages touchés modifiés : {autres[:5]}")
    if any(c not in ecrit for c in orig):
        defauts.append("ajouts : flux disparus : " + str(sorted(c for c in orig if c not in ecrit)[:5]))


def verifier(chemin, travail):
    """Vérifie une carte ; rend le résultat (dict sans chemin) : carte, defauts, durees, accesseurs, flux,
    octets. Les fichiers écrits vont dans `travail`."""
    nom = os.path.basename(chemin)
    base = os.path.splitext(nom)[0]
    res = {"carte": nom, "defauts": [], "durees": {}, "accesseurs": {}, "flux": 0, "octets": 0}
    d, du = res["defauts"], res["durees"]

    def phase(cle, fonction):
        t = time.perf_counter()
        try:
            return fonction()
        except Exception as e:  # tout défaut est rapporté, sans trace ni chemin
            d.append(f"{cle} : {K.message_sans_chemin(e)}")
            return None
        finally:
            du[cle] = round(time.perf_counter() - t, 3)

    doc = phase("ouverture", lambda: PcbDoc.ouvrir(chemin))
    if doc is None:
        return res

    def reencoder():
        for c, n in doc.racine.flux():
            res["flux"] += 1
            res["octets"] += len(n.donnees)
            if doc.flux[c].encoder() != n.donnees:
                d.append(f"réencodage : flux {c} différent")
    phase("reencodage", reencoder)

    def accesseurs():
        for a in ACCESSEURS:
            t = time.perf_counter()
            try:
                v = getattr(doc, a)()
                n = len(v) if hasattr(v, "__len__") else None
            except Exception as e:
                d.append(f"accesseur {a} : {K.message_sans_chemin(e)}")
                n = None
            res["accesseurs"][a] = {"duree": round(time.perf_counter() - t, 3), "nombre": n}
    phase("accesseurs", accesseurs)
    du["pistes_vias_polygones"] = round(sum(res["accesseurs"][a]["duree"] for a in ("pistes", "vias", "polygones")), 3)

    orig = {}

    def aller_retour():
        sortie = os.path.join(travail, f"{base}_aller_retour.PcbDoc")
        doc.enregistrer(sortie)
        orig.update(flux_olefile(chemin))
        ecrit = flux_olefile(sortie)
        manquants = sorted(set(orig) - set(ecrit))
        en_trop = sorted(set(ecrit) - set(orig))
        differents = sorted(c for c in orig if c in ecrit and orig[c] != ecrit[c])
        for quoi, liste in (("manquants", manquants), ("en trop", en_trop), ("différents", differents)):
            if liste:
                d.append(f"aller-retour : {len(liste)} flux {quoi} : {liste[:5]}")
    phase("aller_retour", aller_retour)

    if orig:
        phase("ajouts", lambda: _ajouts(doc, orig, os.path.join(travail, f"{base}_ajouts.PcbDoc"), d, du))
    return res


def _s(v):
    return f"{v:.2f}".replace(".", ",") + " s" if v is not None else "—"


def ligne(res):
    du = res["durees"]
    etat = "OK   " if not res["defauts"] else "ÉCHEC"
    texte = (f"{etat} {res['carte']} : {res['flux']} flux, {res['octets']} octets ; "
             + " ; ".join(f"{p.replace('_', '-')} {_s(du.get(p))}" for p in PHASES)
             + f" ; pistes+vias+polygones {_s(du.get('pistes_vias_polygones'))} ; appels d'ajout "
               f"{_s(du.get('appels_ajout'))} ; écriture {_s(du.get('ecriture'))}")
    for x in res["defauts"]:
        texte += f"\n      défaut : {K.masquer_chemins(x)}"
    return texte


def parseur():
    p = K.Parseur(prog="verifier_cartes.py",
                  description="Vérifie altium_fichier sur des cartes .PcbDoc : ouverture, réencodage, accesseurs, "
                              "aller-retour, ajouts ; durée de chaque phase.")
    p.add_argument("entrees", nargs="+", metavar="CARTE_OU_DOSSIER", help="fichier .PcbDoc ou dossier de .PcbDoc")
    p.add_argument("--exclure", action="append", default=[], metavar="NOM",
                   help="nom de fichier à ne pas vérifier (répétable)")
    p.add_argument("--travail", metavar="DOSSIER", help="dossier des fichiers écrits (gardés) ; sinon temporaire")
    p.add_argument("--json", metavar="FICHIER", help="rapport détaillé (JSON, sans chemin)")
    return p


def _executer(a):
    cartes = lister_cartes(a.entrees, a.exclure)
    travail = a.travail or tempfile.mkdtemp(prefix="verifier_cartes_")
    os.makedirs(travail, exist_ok=True)
    resultats = []
    try:
        for c in cartes:
            r = verifier(c, travail)
            resultats.append(r)
            print(ligne(r), flush=True)
            if not a.travail:
                for f in os.listdir(travail):
                    os.remove(os.path.join(travail, f))
    finally:
        if not a.travail:
            shutil.rmtree(travail, ignore_errors=True)
    ko = [r for r in resultats if r["defauts"]]
    print(f"{len(resultats)} cartes, {len(resultats) - len(ko)} sans défaut, {len(ko)} avec défauts")
    if a.json:
        K.ecrire_texte_atomique(a.json, json.dumps({"cartes": resultats}, ensure_ascii=False, indent=1) + "\n")
    return K.CODE_NEGATIF if ko else K.CODE_OK


def main(argv=None) -> int:
    return K.executer(_executer, parseur(), argv)


if __name__ == "__main__":
    sys.exit(main())
