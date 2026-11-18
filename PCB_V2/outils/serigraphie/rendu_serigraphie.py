# -*- coding: utf-8 -*-
"""Images SVG de la sérigraphie (Python pur, sans bibliothèque de dessin) : une vue par face.

  python rendu_serigraphie.py --entree D [--apres RESULTAT.json] --sortie DOSSIER [--fenetre X0 Y0 X1 Y1]

--entree : dossier des fichiers d'entrée du moteur, carte.json compris (contour, découpes, trous). Écrit
``serigraphie_dessus.svg`` et ``serigraphie_dessous.svg`` ; --fenetre (mm, repère commun) limite la vue.
Face inférieure dessinée VUE DE DESSOUS (x miroir) pour que les textes miroir se lisent à l'endroit.
Gris : cuivre des pastilles de la face ; noir : traits de sérigraphie conservés ; bleu : morceaux rognés ;
vert : désignateurs placés (boîte d'encre) ; orange : ambigus ; rouge : désignateurs masqués (croix au composant).
Codes : 0 fait ; 2 entrée invalide.
"""
import argparse
import os
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drc_serigraphie as M  # noqa: E402
from geom_py import MIL, forme_pastille, arc_en_segments, forme_texte_trait, boite  # noqa: E402

FACES = (("top", "dessus"), ("bottom", "dessous"))


def _chemin(pts, ferme=True):
    return "M" + " L".join("%.4f %.4f" % p for p in pts) + (" Z" if ferme else "")


class Vue:
    """Transformation mil -> coordonnées SVG (mm, y vers le bas), miroir pour la face inférieure."""

    def __init__(self, carte, face, fenetre=None):
        xs = [p[0] for p in carte["contour"]]
        ys = [p[1] for p in carte["contour"]]
        self.bx = (min(xs) * MIL, min(ys) * MIL, max(xs) * MIL, max(ys) * MIL)
        self.f = fenetre or self.bx
        self.face = face

    def p(self, x, y):
        X, Y = x * MIL, y * MIL
        if self.face == "bottom":
            X = self.bx[0] + self.bx[2] - X
        return X, self.bx[1] + self.bx[3] - Y

    def boite_vue(self):
        a = self.p(self.f[0] / MIL, self.f[1] / MIL)
        b = self.p(self.f[2] / MIL, self.f[3] / MIL)
        return min(a[0], b[0]), min(a[1], b[1]), abs(b[0] - a[0]), abs(b[1] - a[1])


def _forme(v, F, couleur, opacite=1.0):
    """Forme de geom_py (seg/poly + rayon) en éléments SVG exacts."""
    r = F[-1] * MIL
    if F[0] == "seg":
        a, b = v.p(*F[1]), v.p(*F[2])
        return ('<line x1="%.4f" y1="%.4f" x2="%.4f" y2="%.4f" stroke="%s" stroke-width="%.4f" '
                'stroke-linecap="round" opacity="%.2f"/>' % (a[0], a[1], b[0], b[1], couleur, max(2 * r, 0.01), opacite))
    pts = [v.p(*q) for q in F[1]]
    trait = (' stroke="%s" stroke-width="%.4f" stroke-linejoin="round"' % (couleur, 2 * r)) if r > 0 else ""
    return '<path d="%s" fill="%s"%s opacity="%.2f"/>' % (_chemin(pts), couleur, trait, opacite)


def dessiner_face(D, apres, face, fenetre=None):
    """Document SVG (texte) d'une face."""
    carte = D["carte"]
    v = Vue(carte, face, fenetre)
    x, y, w, h = v.boite_vue()
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="%.4f %.4f %.4f %.4f" width="%.1fmm" height="%.1fmm">'
           % (x, y, w, h, w, h), '<rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" fill="#0b3d1e"/>' % (x, y, w, h)]
    out.append('<path d="%s" fill="none" stroke="#e8d44d" stroke-width="0.15"/>' % _chemin([v.p(*q) for q in carte["contour"]]))
    for d in carte["decoupes"]:
        out.append('<path d="%s" fill="#000" stroke="#e8d44d" stroke-width="0.1"/>' % _chemin([v.p(*q) for q in d]))
    for tx, ty, tr in carte["trous"]:
        c = v.p(tx, ty)
        out.append('<circle cx="%.4f" cy="%.4f" r="%.4f" fill="#000" stroke="#e8d44d" stroke-width="0.08"/>' % (c[0], c[1], tr * MIL))
    for p in D["pastilles"]:
        if face in M.faces_pastille(p):
            out.append(_forme(v, forme_pastille(p, expansion=False), "#b8b8b8"))
    for s in M.primitives(D, apres, argparse.Namespace(commentaires="caches", texte_ttf="bounds", texte_trait="encre")):
        if s["face"] != face or s.get("nouveau"):
            continue
        out.append(_forme(v, s["forme"], "#4da3ff" if s["type"] == "piste+" else "#f0f0f0"))
    pos = {c["Designator"]: (float(c["X"]), float(c["Y"])) for c in D["composants"]}
    for t in (apres or {}).get("textes", []):
        if M.FACE.get(t["layer"]) != face:
            continue
        if t["hidden"]:
            q = pos.get(t["component"])
            if q:
                a = v.p(*q)
                out.append('<path d="M%.3f %.3f l0.8 0.8 M%.3f %.3f l-0.8 0.8" stroke="#ff4040" stroke-width="0.12"/>'
                           % (a[0] - 0.4, a[1] - 0.4, a[0] + 0.4, a[1] - 0.4))
            continue
        F = forme_texte_trait(t["text"], t["x"], t["y"], t["height"], t["stroke_width"], t["rotation"], t["mirror"])
        coul = "#ffa500" if t.get("ambigu") else "#40e040"
        out.append(_forme(v, F, coul, 0.6))
        b = boite(F)
        c = v.p((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        vertical = (b[3] - b[1]) > (b[2] - b[0])
        out.append('<text x="%.3f" y="%.3f" font-size="%.3f" font-family="monospace" fill="#fff" text-anchor="middle" '
                   'dominant-baseline="middle"%s>%s</text>' % (
                       c[0], c[1], t["height"] * MIL * 0.8,
                       ' transform="rotate(-90 %.3f %.3f)"' % (c[0], c[1]) if vertical else "", escape(t["text"])))
    out.append("</svg>")
    return "\n".join(out)


def main(argv=None):
    ap = M._Analyseur(prog="rendu_serigraphie", add_help=False)
    ap.add_argument("--entree", required=True)
    ap.add_argument("--apres", default=None)
    ap.add_argument("--sortie", required=True)
    ap.add_argument("--fenetre", type=float, nargs=4, default=None)
    try:
        o = ap.parse_args(sys.argv[1:] if argv is None else argv)
        D = M.charger(o.entree)
        apres = M.lire_json(o.apres) if o.apres else None
        os.makedirs(o.sortie, exist_ok=True)
        for face, nom in FACES:
            with open(os.path.join(o.sortie, "serigraphie_%s.svg" % nom), "w", encoding="utf-8") as f:
                f.write(dessiner_face(D, apres, face, o.fenetre))
        return 0
    except M.ErreurEntree as e:
        print("rendu_serigraphie : erreur d'entrée : %s" % e, file=sys.stderr)
        return 2
    except (KeyError, TypeError, ValueError):
        print("rendu_serigraphie : erreur d'entrée : fichiers incomplets ou invalides", file=sys.stderr)
        return 2
    except OSError:
        print("rendu_serigraphie : erreur d'entrée : écriture impossible", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
