"""Extraction de la référence d'une carte routée (étape 11 de §1.3, lot B) : placements, routage et vias de couture
existants d'un ``.PcbDoc``, écrits au format des sorties du moteur (§2.7, §2.8, définition de §6.1).

    python extraire_reference.py CARTE.PcbDoc --entree E.json --sortie DOSSIER

``E.json`` est l'``entree_carte/2`` de la même carte (importer_carte.py) : elle donne le repère commun (origine
dans le repère du fichier), les pièces et leurs poses. Sortie dans DOSSIER : ``placements_routage.json``
(``placements/2``), ``routage_detaille.json`` (``routage/2``) et ``vias_couture.json`` (``vias_couture/2``), en mil,
origine au coin bas-gauche de la boîte du contour.

Définition (§6.1) :
  - placements : toutes les pièces non virtuelles de l'entrée ; statut « fixe » pour une pièce verrouillée,
    « mobile » sinon (information : la référence n'a pas de statut de placement) ;
  - pistes : pistes et arcs libres (sans pièce, hors polygone, hors keep-out) sur les couches de cuivre de type
    signal ; arcs discrétisés avec une flèche de 0,005 mm ; les traits d'une couche de plan négatif (lignes de
    partage) sont ignorés ;
  - vias : vias libres (sans pièce, hors keep-out) non classés couture ;
  - surfaces : régions de cuivre et remplissages libres hors polygone (``type`` « remplissage », ``origine``
    « reference ») ;
  - couture : vias libres d'une masse (R-M2, deduire_config) dont le cuivre (disque du via) ne contient ni
    extrémité de piste ou d'arc libre de cette masse ni pastille CMS de cette masse (exclus du coût commun).

Codes de sortie (§1.4) : 0 succès ; 2 entrée invalide (fichier illisible, entrée d'une autre carte…).
Dépendances : bibliothèque standard, ``format_altium`` (lot BF), ``deduire_config`` (masses).
"""
from __future__ import annotations

import json
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(ICI), "format_altium"))
sys.path.insert(0, ICI)

from altium_fichier import PcbDoc  # noqa: E402
from altium_fichier.pcbdoc import AUCUN, ErreurPcbDoc  # noqa: E402

import deduire_config as DC  # noqa: E402
import formats  # noqa: E402
from formats import ErreurFormat  # noqa: E402

MM = 0.0254                      # mm par mil
FLECHE_MM = 0.005                # flèche de discrétisation des arcs (§6.1)
ORIGINE = "coin bas-gauche de la boite du contour"
FICHIERS = ("placements_routage.json", "routage_detaille.json", "vias_couture.json")


def _r(v):
    return round(float(v), 3)


def _segments_arc(a):
    """Arc (mil, repère du fichier) -> liste de points ; flèche ≤ FLECHE_MM."""
    r = a["rayon"]
    d0, d1 = a["angle_debut"], a["angle_fin"]
    balayage = (d1 - d0) % 360.0 or 360.0
    f = FLECHE_MM / MM
    pas = 2.0 * math.degrees(math.acos(max(-1.0, 1.0 - f / r))) if r > f else balayage
    n = max(1, int(math.ceil(balayage / max(pas, 1e-6))))
    return [(a["cx"] + r * math.cos(math.radians(d0 + balayage * k / n)),
             a["cy"] + r * math.sin(math.radians(d0 + balayage * k / n))) for k in range(n + 1)]


def _distance_rect(px, py, cx, cy, hx, hy, rot):
    """Distance du point (px, py) au rectangle de centre (cx, cy), demi-côtés (hx, hy), tourné de ``rot`` degrés."""
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    dx, dy = px - cx, py - cy
    u, v = abs(dx * c + dy * s) - hx, abs(-dx * s + dy * c) - hy
    return math.hypot(max(u, 0.0), max(v, 0.0))


def _masses(entree):
    natures, _ = DC.classer(entree)
    liste, _ = DC._masses(DC._Carte(entree, natures), entree, {}, "contraintes")
    return list(liste)


def extraire(chemin_carte, entree):
    """(placements/2, routage/2, vias_couture/2) de la carte routée ``chemin_carte`` (§6.1) ; ``entree`` est son
    ``entree_carte/2``. ``ErreurPcbDoc`` si le fichier est illisible, ``ErreurFormat`` si l'entrée ne lui
    correspond pas."""
    doc = PcbDoc.ouvrir(chemin_carte)
    if entree.get("source", {}).get("fichier") not in (None, doc.nom):
        raise ErreurFormat(f"entrée de {entree['source']['fichier']}, pas de {doc.nom}")
    x0, y0 = (entree.get("source", {}).get("origine_fichier_mil") or [0.0, 0.0])[:2]

    def W(x, y):
        return [_r(x - x0), _r(y - y0)]

    cuivre = doc.couches_cuivre()
    nom = {c["nom_altium"]: c["nom"] for c in cuivre}
    routage_couches = [c["nom"] for c in cuivre if c["type"] == "signal"]
    signal = {c["nom_altium"] for c in cuivre if c["type"] == "signal"}

    # --- placements ---
    pieces = [{"designator": c["designateur"], "side": "bottom" if c["face"] == "dessous" else "top",
               "x": _r(c["x"] / MM), "y": _r(c["y"] / MM), "rotation": float(c["rotation"]),
               "statut": "fixe" if c.get("verrouille") else "mobile"} for c in entree["composants"]]
    placements = {"format": "placements/2", "unite": "mil", "origine": ORIGINE, "pieces": pieces}

    # --- pistes et arcs libres ---
    def libre(p):
        return p["composant"] == AUCUN and p["polygone"] == AUCUN and not p["keepout"]

    pistes, extremites = [], {}
    for t in doc.pistes():
        if libre(t) and t["couche"] in signal:
            n = t["reseau"] or ""
            pistes.append({"x1": W(t["x1"], t["y1"])[0], "y1": W(t["x1"], t["y1"])[1], "x2": W(t["x2"], t["y2"])[0],
                           "y2": W(t["x2"], t["y2"])[1], "width": _r(t["largeur"]), "layer": nom[t["couche"]],
                           "net_name": n})
            extremites.setdefault(n, []).extend([(t["x1"], t["y1"]), (t["x2"], t["y2"])])
    n_pistes = len(pistes)
    n_arcs = 0
    for a in doc.arcs():
        if libre(a) and a["couche"] in signal:
            n_arcs += 1
            n = a["reseau"] or ""
            pts = _segments_arc(a)
            for p, q in zip(pts, pts[1:]):
                (xa, ya), (xb, yb) = W(*p), W(*q)
                pistes.append({"x1": xa, "y1": ya, "x2": xb, "y2": yb, "width": _r(a["largeur"]),
                               "layer": nom[a["couche"]], "net_name": n})
            extremites.setdefault(n, []).extend([pts[0], pts[-1]])

    # --- vias libres : routage ou couture ---
    masses = set(_masses(entree))
    cms = {}
    for p in doc.pastilles():
        if not p.get("traversante") and p.get("reseau") in masses:
            hx, hy = (p["taille_haut"][0] / 2.0, p["taille_haut"][1] / 2.0)
            cms.setdefault(p["reseau"], []).append((p["x"], p["y"], hx, hy, p.get("rotation") or 0.0))
    vias, couture = [], []
    for v in doc.vias():
        if v["composant"] != AUCUN or v["keepout"]:
            continue
        n = v["reseau"] or ""
        x, y = W(v["x"], v["y"])
        r = v["diametre"] / 2.0
        if n in masses and not any(math.hypot(px - v["x"], py - v["y"]) <= r for px, py in extremites.get(n, [])) \
                and not any(_distance_rect(v["x"], v["y"], *c) <= 0.0 for c in cms.get(n, [])):
            couture.append({"x": x, "y": y, "size": _r(v["diametre"]), "hole_size": _r(v["trou"]), "net": n})
        else:
            vias.append({"x": x, "y": y, "size": _r(v["diametre"]), "hole_size": _r(v["trou"]), "net": n,
                         "de": nom.get(v["de"], v["de"]), "a": nom.get(v["a"], v["a"])})

    # --- surfaces : régions et remplissages libres hors polygone ---
    surfaces = []
    for g in doc.regions():
        if libre(g) and g["couche"] in nom and not g["decoupe_carte"] and g["genre"] == 0 and len(g["contour"]) >= 3:
            surfaces.append({"net": g["reseau"] or "", "layer": nom[g["couche"]],
                             "contour": [W(x, y) for x, y in g["contour"]],
                             "trous": [[W(x, y) for x, y in t] for t in g["trous"] or [] if len(t) >= 3],
                             "type": "remplissage", "origine": "reference"})
    for f in doc.remplissages():
        if libre(f) and f["couche"] in nom:
            cx, cy = (f["x1"] + f["x2"]) / 2.0, (f["y1"] + f["y2"]) / 2.0
            hx, hy = abs(f["x2"] - f["x1"]) / 2.0, abs(f["y2"] - f["y1"]) / 2.0
            c, s = math.cos(math.radians(f["rotation"] or 0.0)), math.sin(math.radians(f["rotation"] or 0.0))
            pts = [(cx + u * c - w * s, cy + u * s + w * c) for u, w in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]
            surfaces.append({"net": f["reseau"] or "", "layer": nom[f["couche"]], "contour": [W(x, y) for x, y in pts],
                             "trous": [], "type": "remplissage", "origine": "reference"})

    routage = {"format": "routage/2", "unite": "mil", "origine": ORIGINE, "couches_routage": routage_couches,
               "pistes": pistes, "vias": vias, "surfaces": surfaces, "amorces_pas_fin": [],
               "derogations": [f"référence extraite de {doc.nom} : {n_pistes} pistes et {n_arcs} arcs libres, "
                               f"{len(vias) + len(couture)} vias libres dont {len(couture)} de couture, "
                               f"{len(surfaces)} surfaces"],
               "reseaux": [], "echecs": []}
    vc = {"format": "vias_couture/2", "unite": "mil", "reseaux": sorted({v["net"] for v in couture} | masses),
          "pas_grille_mm": 2.5, "vias": couture, "refus": {}}
    return placements, routage, vc


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------

USAGE = "usage : python extraire_reference.py CARTE.PcbDoc --entree E.json --sortie DOSSIER"


def _dire(texte, flux=None):
    flux = sys.stderr if flux is None else flux
    enc = getattr(flux, "encoding", None) or "utf-8"
    flux.write(texte.encode(enc, "replace").decode(enc, "replace") + "\n")
    flux.flush()


def _arguments(argv):
    a, i = {"carte": None}, 0
    while i < len(argv):
        x = argv[i]
        if x in ("-h", "--aide", "--help"):
            return None
        cle, egal, val = x.partition("=")
        if cle in ("--entree", "--sortie"):
            if not egal:
                if i + 1 >= len(argv):
                    raise ErreurFormat(f"option {cle} sans valeur")
                i, val = i + 1, argv[i + 1]
            a[cle[2:]] = val
        elif x.startswith("-"):
            raise ErreurFormat(f"option inconnue : {cle}")
        elif a["carte"] is None:
            a["carte"] = x
        else:
            raise ErreurFormat("une seule carte attendue")
        i += 1
    for k in ("carte", "entree", "sortie"):
        if not a.get(k):
            raise ErreurFormat(f"{'carte' if k == 'carte' else '--' + k} manquante")
    return a


def main(argv=None):
    """Rend le code de sortie (§1.4) sans quitter le processus."""
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        a = _arguments(argv)
    except ErreurFormat as e:
        _dire(f"extraire_reference : {e}")
        _dire(USAGE)
        return 2
    if a is None:
        _dire(USAGE, sys.stdout)
        return 0
    try:
        from importer_carte import ecrire_json  # noqa: PLC0415
        entree = formats.charger(a["entree"], "entree_carte/2")
        sorties = extraire(a["carte"], entree)
        os.makedirs(a["sortie"], exist_ok=True)
        for nom_f, d in zip(FICHIERS, sorties):
            ecrire_json(os.path.join(a["sortie"], nom_f), d)
    except (ErreurFormat, ErreurPcbDoc, OSError) as e:
        texte = str(e)
        for ch in (a["carte"], a["entree"], a["sortie"]):
            texte = texte.replace(os.path.abspath(ch), os.path.basename(ch)).replace(ch, os.path.basename(ch))
        _dire(f"extraire_reference : {texte}")
        return 2
    p, r, c = sorties
    _dire(f"{os.path.basename(a['carte'])} : {len(p['pieces'])} pièces, {len(r['pistes'])} pistes, "
          f"{len(r['vias'])} vias de routage, {len(c['vias'])} vias de couture, {len(r['surfaces'])} surfaces",
          sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
