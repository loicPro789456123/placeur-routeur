# -*- coding: utf-8 -*-
"""Aperçu SVG d'une carte placée et routée (Python pur, sans bibliothèque de dessin).

  python visualiser.py --donnees D --placement P --routage R --sortie S.svg
                       [--couture C] [--fenetre X0 Y0 X1 Y1] [--reseau N1,N2] [--titre T]

Formes exactes de ``geom_commune.poser`` (pastilles, régions, cuivre fixe, zones de plan, pistes, vias, surfaces,
vias de couture), une couleur par rang de couche (palette fixe, couche du dessus en premier plan), légende des
couches, contour et découpes. ``--fenetre`` (mm, repère commun) zoome sur une zone ; ``--reseau`` met un ou
plusieurs réseaux (séparés par des virgules) en surbrillance (les autres sont estompés). Codes : 0 fait ; 2 entrée invalide ; 4 erreur interne.
"""
import os
import sys
from xml.sax.saxutils import escape

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import formats  # noqa: E402
import geom_commune as gc  # noqa: E402

# palette par rang (rang 0 = dessus) ; au-delà, la palette recommence
PALETTE = ("#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22",
           "#7f7f7f")
COULEUR_COUTURE = "#ffd700"
COULEUR_FOND = "#101418"
COULEUR_CONTOUR = "#f0e68c"
OPACITE_ZONE = 0.12
OPACITE_ESTOMPE = 0.15
MARGE_MM = 1.0


def couleur_rang(r):
    return PALETTE[r % len(PALETTE)]


def _pt(x, y):
    return "%.4f %.4f" % (x, -y)


def _chemin_anneaux(anneaux):
    return " ".join("M" + " L".join(_pt(x, y) for x, y in a) + " Z" for a in anneaux if len(a) >= 2)


def forme_svg(f, couleur, opacite=1.0, remplir=True):
    """Élément SVG exact d'une forme de geom_commune (noyau élargi de son rayon)."""
    r = f.rayon
    if f.genre == gc.POINT:
        return '<circle cx="%.4f" cy="%.4f" r="%.4f" fill="%s" opacity="%.3f"/>' % (f.x1, -f.y1, max(r, 0.01), couleur, opacite)
    if f.genre == gc.SEGMENT:
        return ('<line x1="%.4f" y1="%.4f" x2="%.4f" y2="%.4f" stroke="%s" stroke-width="%.4f" stroke-linecap="round" '
                'opacity="%.3f"/>' % (f.x1, -f.y1, f.x2, -f.y2, couleur, max(2 * r, 0.01), opacite))
    trait = (' stroke="%s" stroke-width="%.4f" stroke-linejoin="round"' % (couleur, 2 * r)) if r > 0 else ""
    return '<path d="%s" fill="%s" fill-rule="evenodd"%s opacity="%.3f"/>' % (
        _chemin_anneaux(f.anneaux), couleur if remplir else "none", trait, opacite)


def _boite_carte(donnees):
    pts = gc.points(gc.exiger(donnees["carte"], "contour", "donnees.carte"), "donnees.carte.contour")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return pts, (min(xs), min(ys), max(xs), max(ys))


def dessiner_svg(donnees, placements, routage, couture=None, fenetre=None, reseaux=(), titre=""):
    """Document SVG (texte) de la carte : un groupe par rang (dernier rang dessous), légende, couture."""
    sc = gc.poser(donnees, placements, routage, couture)
    contour, bx = _boite_carte(donnees)
    f = fenetre or (bx[0] - MARGE_MM, bx[1] - MARGE_MM, bx[2] + MARGE_MM, bx[3] + MARGE_MM)
    if f[2] <= f[0] or f[3] <= f[1]:
        raise gc.ErreurEntree("--fenetre : x1 > x0 et y1 > y0 attendus")
    lw, lh = f[2] - f[0], f[3] - f[1]
    leg = 4.0 + 3.0 * (sc.n + 1)
    vb = (f[0], -f[3], lw, lh + leg)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="%.4f %.4f %.4f %.4f" width="%.1fmm" height="%.1fmm">'
           % (vb + (vb[2], vb[3])),
           '<title>%s</title>' % escape(titre or "aperçu"),
           '<rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" fill="%s"/>' % (vb + (COULEUR_FOND,)),
           '<path d="%s" fill="none" stroke="%s" stroke-width="0.15"/>' % (_chemin_anneaux([contour]), COULEUR_CONTOUR)]
    for i, dc in enumerate((donnees["carte"].get("decoupes") or [])):
        pts = gc.points(gc.exiger(dc, "points", "donnees.carte.decoupes[%d]" % i), "donnees.carte.decoupes")
        out.append('<path d="%s" fill="#000" stroke="%s" stroke-width="0.1"/>' % (_chemin_anneaux([pts]), COULEUR_CONTOUR))
    choisis = set(reseaux)
    fen = (f[0], f[1], f[2], f[3])

    def visible(o):
        b = o.boite
        return not (b[2] < fen[0] or b[0] > fen[2] or b[3] < fen[1] or b[1] > fen[3])

    def opacite(o, base=0.85):
        if choisis and o.reseau not in choisis and not (o.liaison and choisis & set(o.liaison)):
            return OPACITE_ESTOMPE
        return base

    couture_ids = set(sc.couture)
    for r in reversed(range(sc.n)):
        coul = couleur_rang(r)
        out.append('<g id="couche_%d">' % r)
        for k, o in enumerate(sc.objets):
            if not (o.masque >> r) & 1 or not visible(o) or k in couture_ids:
                continue
            if o.genre == gc.ZONE_PLAN:
                out.append(forme_svg(o.forme_sur(r), coul, OPACITE_ZONE if not choisis else OPACITE_ESTOMPE))
                continue
            out.append(forme_svg(o.forme_sur(r), coul, opacite(o)))
        out.append('</g>')
    out.append('<g id="couture">')
    for k in sc.couture:
        o = sc.objets[k]
        if visible(o):
            out.append(forme_svg(o.forme, COULEUR_COUTURE, opacite(o, 0.9)))
    out.append('</g>')
    # légende : une ligne par couche (rang, nom), puis la couture
    y0 = -f[1] + 3.0
    fs = 2.2
    out.append('<g id="legende" font-family="sans-serif" font-size="%.2f" fill="#ddd">' % fs)
    if titre:
        out.append('<text x="%.3f" y="%.3f">%s</text>' % (f[0] + 1, y0, escape(titre)))
    for r, nom in enumerate(sc.couches):
        y = y0 + 3.0 * (r + 1)
        out.append('<rect x="%.3f" y="%.3f" width="2" height="2" fill="%s"/>' % (f[0] + 1, y - 1.8, couleur_rang(r)))
        out.append('<text x="%.3f" y="%.3f">%d %s</text>' % (f[0] + 4, y, r, escape(nom)))
    y = y0 + 3.0 * (sc.n + 1)
    out.append('<rect x="%.3f" y="%.3f" width="2" height="2" fill="%s"/>' % (f[0] + 1, y - 1.8, COULEUR_COUTURE))
    out.append('<text x="%.3f" y="%.3f">vias de couture (%d)%s</text>' % (
        f[0] + 4, y, len(sc.couture), (" ; réseaux : " + escape(", ".join(sorted(choisis)))) if choisis else ""))
    out.append('</g></svg>')
    return "\n".join(out)


OPTIONS = (
    gc.Option("--donnees", "données du moteur (donnees_placeur/2)", obligatoire=True, meta="D"),
    gc.Option("--placement", "placement (placements/2 ou liste v1)", obligatoire=True, meta="P"),
    gc.Option("--routage", "routage (routage/2 ou v1)", obligatoire=True, meta="R"),
    gc.Option("--sortie", "fichier SVG écrit", obligatoire=True, meta="S"),
    gc.Option("--couture", "vias de couture (vias_couture/1)", meta="C"),
    gc.Option("--fenetre", "zoom : x0 y0 x1 y1 en mm (repère commun)", nargs=4, type=float, meta="X"),
    gc.Option("--reseau", "réseaux en surbrillance, séparés par des virgules", meta="N"),
    gc.Option("--titre", "titre de l'aperçu", meta="T"),
)


def _charger(chemin, fmt):
    try:
        return formats.charger(chemin, fmt)
    except formats.ErreurFormat as e:
        raise gc.ErreurEntree("; ".join(e.erreurs))


def _principal(args):
    couture = _charger(args["couture"], "vias_couture") if args.get("couture") else None
    reseaux = [n for n in (args.get("reseau") or "").split(",") if n]
    svg = dessiner_svg(_charger(args["donnees"], "donnees_placeur"), _charger(args["placement"], "placements"),
                       _charger(args["routage"], "routage"), couture, args.get("fenetre"), reseaux,
                       args.get("titre") or "")
    tmp = args["sortie"] + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)
        os.replace(tmp, args["sortie"])
    except OSError:
        raise gc.ErreurEntree("%s : écriture impossible" % gc.nom_fichier(args["sortie"])) from None
    gc.afficher("aperçu écrit : %s" % gc.nom_fichier(args["sortie"]))
    return gc.CODE_SUCCES


def main(argv=None):
    """Ligne de commande ; rend le code de sortie (§1.4) sans quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, "visualiser.py", "Aperçu SVG d'une carte placée et routée.")


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
