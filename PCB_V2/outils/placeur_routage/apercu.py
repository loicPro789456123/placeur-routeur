"""Aperçu de la carte pour la fenêtre : formes simples dans le repère commun (mm), sans bibliothèque de dessin.

- ``depuis_entree(entree)`` : carte d'origine (``entree_carte/2`` de l'import) : contour, découpes, pastilles,
  cuivre existant, pièces (boîte posée, face, verrouillage du fichier).
- ``depuis_resultat(dossier_calcul, entree=None)`` : carte calculée (meilleure graine, sinon graine à terminer) :
  cuivre exact de ``geom_commune.poser`` (pastilles, pistes, vias, surfaces, couture) et pièces à leur nouvelle pose.

Une scène : ``contour`` (points), ``decoupes`` (listes de points), ``pieces`` (dict : nom, coins, centre, dessous,
verrouille, fixe), ``cuivre`` (tuples ``(face, genre, données)`` ; face « dessus », « dessous », « interne » ou
« toutes » ; genre « poly » (points), « seg » (x1, y1, x2, y2, largeur) ou « rond » (x, y, rayon)), ``boite``.
"""
from __future__ import annotations

import json
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import geom_commune as gc  # noqa: E402

PAS_ARC = 12


class Scene:
    def __init__(self):
        self.contour, self.decoupes, self.pieces, self.cuivre = [], [], [], []
        self.titre = ""
        self.boite = (0.0, 0.0, 1.0, 1.0)

    def finir(self):
        pts = list(self.contour) or [p for c in self.pieces for p in c["coins"]]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            self.boite = (min(xs), min(ys), max(xs), max(ys))
        return self

    def resume(self) -> dict:
        return {"pieces": len(self.pieces), "verrouillees": sum(1 for p in self.pieces if p["verrouille"]),
                "fixes": sum(1 for p in self.pieces if p["fixe"]), "cuivre": len(self.cuivre)}


def _rect_local(b):
    x0, y0, x1, y1 = b
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _piece(nom, pose, boite, verrouille, fixe):
    if not boite or (boite[2] - boite[0]) <= 0 or (boite[3] - boite[1]) <= 0:
        boite = (-0.5, -0.5, 0.5, 0.5)
    coins = [pose.monde(x, y) for x, y in _rect_local(boite)]
    cx, cy = pose.monde((boite[0] + boite[2]) / 2, (boite[1] + boite[3]) / 2)
    return {"nom": nom, "coins": coins, "centre": (cx, cy), "dessous": pose.dessous, "verrouille": bool(verrouille),
            "fixe": bool(fixe)}


def _face_couches(couches, noms):
    if not noms:
        return "dessus"
    if len(couches) > 1 and noms[0] in couches and noms[-1] in couches:
        return "toutes"
    if noms[0] in couches:
        return "dessus"
    if noms[-1] in couches:
        return "dessous"
    return "interne"


def _forme_pastille(p, pose):
    """Forme posée d'une pastille (repère local de la pièce, ou commun si ``pose`` vaut l'identité)."""
    w, h = float(p.get("largeur") or 0.1), float(p.get("hauteur") or 0.1)
    cx, cy = pose.monde(float(p.get("x", 0.0)), float(p.get("y", 0.0)))
    forme = p.get("forme")
    a = math.radians(pose.angle(float(p.get("rotation") or 0.0)))
    c, s = math.cos(a), math.sin(a)
    if forme == "rond" or (forme == "oblong" and abs(w - h) < 1e-6):
        return "rond", (cx, cy, max(w, h) / 2)
    if forme == "oblong":
        d = (max(w, h) - min(w, h)) / 2
        dx, dy = (c * d, s * d) if w >= h else (-s * d, c * d)
        return "seg", (cx - dx, cy - dy, cx + dx, cy + dy, min(w, h))
    pts = []
    for lx, ly in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        pts.append((cx + c * lx - s * ly, cy + s * lx + c * ly))
    return "poly", pts


def depuis_entree(entree: dict) -> Scene:
    sc = Scene()
    carte = entree.get("carte") or {}
    noms = carte.get("noms_couches_cuivre") or [c.get("nom") for c in carte.get("couches") or []]
    sc.contour = [tuple(p) for p in carte.get("contour") or []]
    sc.decoupes = [[tuple(p) for p in d.get("points") or []] for d in carte.get("decoupes") or []]
    ident = gc.Pose(0.0, 0.0, 0.0, False)
    for c in entree.get("composants") or []:
        pose = gc.Pose(c.get("x", 0.0), c.get("y", 0.0), c.get("rotation", 0.0), c.get("face") == "dessous")
        for p in c.get("pastilles") or []:
            face = "toutes" if p.get("traversante") else _face_couches(p.get("couches") or [], noms)
            sc.cuivre.append((face,) + _forme_pastille(p, pose))
        sc.pieces.append(_piece(c.get("designateur", "?"), pose, c.get("boite"), c.get("verrouille"),
                                c.get("verrouille")))
    for p in entree.get("pastilles_libres") or []:
        face = "toutes" if p.get("traversante") else _face_couches(p.get("couches") or [], noms)
        sc.cuivre.append((face,) + _forme_pastille(p, ident))
    for e in carte.get("cuivre_existant") or []:
        face = _face_couches(e.get("couches") or [], noms)
        t = e.get("type")
        if t == "piste":
            sc.cuivre.append((face, "seg", (e["x1"], e["y1"], e["x2"], e["y2"], e.get("largeur", 0.2))))
        elif t == "via":
            sc.cuivre.append(("toutes", "rond", (e["x"], e["y"], e.get("diametre", 0.5) / 2)))
        elif t == "region" and e.get("points"):
            sc.cuivre.append((face, "poly", [tuple(q) for q in e["points"]]))
    sc.titre = "Carte d'origine"
    return sc.finir()


def _lire(chemin):
    try:
        with open(chemin, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def graine_retenue(dossier_calcul: str):
    """(graine, dossier) de la meilleure graine du calcul, sinon de la graine à terminer ; (None, None) sinon."""
    technique = os.path.join(dossier_calcul, "technique")
    rapport = _lire(os.path.join(technique, "rapport.json")) or {}
    for cle in ("meilleure", "a_terminer"):
        g = rapport.get(cle)
        if g is not None:
            d = os.path.join(technique, f"graine_{g}")
            if os.path.isfile(os.path.join(d, "placements_routage.json")):
                return g, d
    return None, None


def depuis_resultat(dossier_calcul: str, entree: dict | None = None) -> Scene | None:
    """Scène de la carte calculée, ou None si le calcul n'a pas de graine retenue lisible."""
    g, d = graine_retenue(dossier_calcul)
    if g is None:
        return None
    donnees = _lire(os.path.join(dossier_calcul, "technique", "donnees_placeur.json"))
    placements = _lire(os.path.join(d, "placements_routage.json"))
    routage = _lire(os.path.join(d, "routage_detaille.json"))
    couture = _lire(os.path.join(d, "vias_couture.json"))
    if donnees is None or placements is None:
        return None
    s = gc.poser(donnees, placements, routage, couture)
    sc = Scene()
    n = s.n
    sc.contour = [tuple(p) for p in (donnees.get("carte") or {}).get("contour") or []]
    sc.decoupes = [[tuple(p) for p in dc.get("points") or []] for dc in (donnees.get("carte") or {}).get("decoupes")
                   or []]
    for o in s.objets:
        if o.genre == gc.ZONE_PLAN:
            continue
        haut, bas = bool(o.masque & 1), bool((o.masque >> (n - 1)) & 1)
        face = "toutes" if haut and bas else "dessus" if haut else "dessous" if bas else "interne"
        f = o.forme
        if f.genre == gc.POINT:
            sc.cuivre.append((face, "rond", (f.x1, f.y1, max(f.rayon, 0.05))))
        elif f.genre == gc.SEGMENT:
            sc.cuivre.append((face, "seg", (f.x1, f.y1, f.x2, f.y2, max(2 * f.rayon, 0.05))))
        elif f.anneaux:
            sc.cuivre.append((face, "poly", [tuple(q) for q in f.anneaux[0]]))
    verrous = {c.get("designateur"): bool(c.get("verrouille")) for c in (entree or {}).get("composants") or []}
    for c in donnees.get("composants") or []:
        if c.get("virtuel"):
            continue
        nom = c.get("nom")
        pose = s.poses.get(nom)
        if pose is None:
            continue
        if not isinstance(pose, gc.Pose):
            pose = gc.Pose(*pose[:4])
        sc.pieces.append(_piece(nom, pose, c.get("lbb"), verrous.get(nom, False), c.get("statut") == "fixe"))
    sc.titre = f"Résultat : graine {g}"
    return sc.finir()
