"""Surfaces de cuivre propres aux empreintes (regions et remplissages de composant sur une couche de
cuivre, hors pastilles), en coordonnees locales de la piece, pour le routeur et drc_maison.

Format (champ optionnel "regions" de chaque composant de donnees_placeur.json) :
    "regions": [{"face": "composant" | "opposee" | "toutes",
                 "pastille": "11",        (facultatif : la region herite du reseau de cette pastille)
                 "reseau": "GND",         (facultatif, si aucune pastille ; aucun des deux = obstacle sans reseau)
                 "points": [[lx, ly], ...]}]   (mm, repere local, polygone simple ferme implicitement)
Repere local identique a pastilles[].lx/ly : local = M(face) . R(-rotation) . (monde - origine), M(dessous) =
diag(1, -1) (verifie sur 1010 pastilles de donnees_placeur.json). Arcs discretises a moins de 0,01 mm.

usage : python regions_empreintes.py <carte.PcbDoc> --maj donnees_placeur.json   (ajoute / remplace le champ
        "regions" des composants, rien d'autre n'est modifie)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(ICI), "format_altium"))

from altium_fichier import PcbDoc, couches  # noqa: E402
from altium_fichier.proprietes import valeur_mil  # noqa: E402

MM = 0.0254          # mm par mil
FLECHE = 0.005       # mm : ecart maximal corde / arc
FIN_CRLF, FIN_LF = chr(13) + chr(10), chr(10)
CUIVRE = {"TOP", "BOTTOM"} | {f"MID{i}" for i in range(1, 31)}


def _arc(cx, cy, r, a1, a2):
    """Points (mil) d'un arc de a1 a a2 (degres, sens direct), fleche < FLECHE."""
    if r <= 0:
        return []
    bal = (a2 - a1) % 360.0 or 360.0
    rmm = r * MM
    pas = 2 * math.degrees(math.acos(max(-1.0, 1 - FLECHE / rmm))) if rmm > FLECHE else 90.0
    n = max(1, int(math.ceil(bal / max(pas, 0.5))))
    return [(cx + r * math.cos(math.radians(a1 + bal * k / n)), cy + r * math.sin(math.radians(a1 + bal * k / n)))
            for k in range(n + 1)]


def contour_region(pr_simple, pr_forme):
    """Contour (mil, monde) d'une region : forme a arcs (ShapeBasedRegions6) discretisee si disponible,
    sinon sommets du flux Regions6."""
    if pr_forme is not None and pr_forme.sous[0].contour:
        c = pr_forme.sous[0].contour
        pts = []
        for i, (est_arc, x, y, cx, cy, ra, a1, a2) in enumerate(c[:-1] if len(c) > 1 else c):
            if est_arc:
                pts += [(px / 1e4, py / 1e4) for px, py in _arc(cx, cy, ra, a1, a2)]
            else:
                pts.append((x / 1e4, y / 1e4))
        if pts:
            return pts
    return [(x / 1e4, y / 1e4) for x, y in pr_simple.sous[0].contour]


def _local(p, o, rot, dessous):
    r = math.radians(rot)
    dx, dy = (p[0] - o[0]) * MM, (p[1] - o[1]) * MM
    lx = math.cos(r) * dx + math.sin(r) * dy
    ly = -math.sin(r) * dx + math.cos(r) * dy
    return [round(lx, 6), round(-ly if dessous else ly, 6)]


def _d_pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _dedans(p, poly):
    x, y = p
    ok = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            ok = not ok
    return ok


def _touche(pa, pb, tol=1e-3):
    """Deux polygones (memes unites) se touchent ou se chevauchent."""
    if any(_dedans(p, pb) for p in pa) or any(_dedans(p, pa) for p in pb):
        return True
    for i in range(len(pa)):
        a, b = pa[i], pa[(i + 1) % len(pa)]
        for j in range(len(pb)):
            c, d = pb[j], pb[(j + 1) % len(pb)]
            if min(_d_pt_seg(a, c, d), _d_pt_seg(b, c, d), _d_pt_seg(c, a, b), _d_pt_seg(d, a, b)) <= tol:
                return True
    return False


def _rect_pastille(p):
    w, h = p["taille_haut"]
    r = math.radians(p["rotation"])
    c, s = math.cos(r), math.sin(r)
    return [(p["x"] + c * u - s * v, p["y"] + s * u + c * v) for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def regions_par_composant(doc):
    """{designateur: [region, ...]} au format ci-dessus."""
    D = doc.designateurs()
    comp6 = doc.data("Components6")
    pads = {}
    for p in doc.pastilles():
        if p["composant"] != 0xFFFF:
            pads.setdefault(p["composant"], []).append(p)
    sb = doc.data("ShapeBasedRegions6")
    out = {}
    objets = []
    for k, pr in enumerate(doc.data("Regions6") or []):
        b = pr.bloc
        s0 = pr.sous[0]
        if b["composant"] == 0xFFFF or b["drapeaux2"] & 2 or s0.props.get("KIND", "0") != "0" or not s0.contour:
            continue
        if s0.props.get("ISBOARDCUTOUT") == "TRUE":
            continue
        objets.append((b, contour_region(pr, sb[k] if sb is not None and k < len(sb) else None)))
    for pr in doc.data("Fills6") or []:
        b = pr.bloc
        if b["composant"] == 0xFFFF or b["drapeaux2"] & 2:
            continue
        cx, cy = (b["x1"] + b["x2"]) / 2e4, (b["y1"] + b["y2"]) / 2e4
        hx, hy = abs(b["x2"] - b["x1"]) / 2e4, abs(b["y2"] - b["y1"]) / 2e4
        r = math.radians(b["rotation"])
        c, s = math.cos(r), math.sin(r)
        objets.append((b, [(cx + c * u - s * v, cy + s * u + c * v) for u, v in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]))
    for b, poly in objets:
        nom = couches.NOMS_V6.get(b["couche"], "")
        if nom not in CUIVRE and nom != "MULTILAYER":
            continue
        i = b["composant"]
        e = comp6[i]
        o = (valeur_mil(e["X"]), valeur_mil(e["Y"]))
        rot = float(e.get("ROTATION", "0").strip() or 0)
        dessous = e.get("LAYER") == "BOTTOM"
        if nom == "MULTILAYER":
            face = "toutes"
        else:
            face = "composant" if (nom == "BOTTOM") == dessous else "opposee"
        reg = {"face": face}
        net = doc.nom_reseau(b["reseau"]) if b["reseau"] != 0xFFFF else None
        if net:
            for p in pads.get(i, []):
                if p["reseau"] == net and p["couche"] in (nom, "MULTILAYER") and _touche(_rect_pastille(p), poly):
                    reg["pastille"] = p["nom"]
                    break
            if "pastille" not in reg:
                reg["reseau"] = net
        reg["points"] = [_local(p, o, rot, dessous) for p in poly]
        out.setdefault(D[i], []).append(reg)
    return out


def maj_donnees(chemin_donnees, regions):
    """Ajoute / remplace le champ 'regions' de chaque composant ; renvoie le nombre de composants touches."""
    with open(chemin_donnees, "rb") as f:
        crlf = bytes([13, 10]) in f.read(4096)
    with open(chemin_donnees, encoding="utf-8") as f:
        brut = f.read()
    d = json.loads(brut)
    n = 0
    for c in d["composants"]:
        if c["nom"] in regions:
            c["regions"] = regions[c["nom"]]
            n += 1
        elif "regions" in c:
            del c["regions"]
    with open(chemin_donnees, "w", encoding="utf-8", newline=FIN_CRLF if crlf else FIN_LF) as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
        if brut.endswith("\n"):
            f.write("\n")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("carte")
    ap.add_argument("--maj", help="donnees_placeur.json a completer")
    a = ap.parse_args(argv)
    reg = regions_par_composant(PcbDoc.ouvrir(a.carte))
    for k, v in sorted(reg.items()):
        print(k, json.dumps(v))
    if a.maj:
        print(f"{maj_donnees(a.maj, reg)} composant(s) completes dans {a.maj}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
