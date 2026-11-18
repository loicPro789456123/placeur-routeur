"""Importeur KiCad (``.kicad_pcb``, ``.kicad_pro``, ``.kicad_dru``) vers ``entree_carte/2`` (§2.2, annexe D).

``importer(chemin) -> dict``. Entrée invalide : ``ErreurEntree`` (code 2), message en français qui ne cite que des
noms de fichiers. Aucune variable d'environnement, aucune décision (rôles, pièces fixes, plans) : lecture fidèle.

Repères.

- Fichier : mm, y vers le bas, origine de la page. Une pièce pose ses enfants par
  monde_fichier = position + Rk(θ) · local_fichier, avec Rk(θ)(x, y) = (x cos θ + y sin θ, −x sin θ + y cos θ)
  (rotation visuelle dans le sens trigonométrique, y vers le bas). L'angle d'une pastille ``(at x y φ)`` est
  absolu ; sa position est locale.
- Commun (§0.4) : X = x − x0, Y = y0 − y, où (x0, y0) est le coin bas-gauche de la boîte du contour discrétisé,
  dans le repère du fichier (``source.origine_fichier_mm``). Les angles de KiCad sont déjà trigonométriques.
- Local d'une pièce (§0.4, M(dessous) = diag(1, −1)) : dessus, local = (lx, −ly) ; dessous, local = (lx, ly),
  rotation = θ dans les deux cas. KiCad retourne une pièce par un miroir en y et la négation des angles :
  les données d'une pièce du dessous sont déjà miroitées dans le fichier, ce qui rend à « local » la forme de la
  bibliothèque. Les vecteurs du repère d'une pastille (décalage, coins, primitives) suivent la même règle.
- Rotation relative d'une pastille : φ − θ dessus, θ − φ dessous (§0.4).
- Centre d'une pastille (``x``, ``y``) = centre du cuivre ; ``trou.decalage`` = position du trou par rapport à ce
  centre, dans le repère de la pastille (KiCad : le trou est à la position de la pastille, le cuivre est décalé
  de ``offset``).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re

import import_geometrie as G
from import_geometrie import ErreurEntree

VERSION_MIN = 20211014
TYPES_COUCHE = ("signal", "power", "mixed", "jumper")
COUCHES_BOITE = ("F.SilkS", "B.SilkS", "F.CrtYd", "B.CrtYd")
FORMES_GR = ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly", "gr_curve")
FORMES_FP = ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly", "fp_curve")
EFFETS_KEEPOUT = (("tracks", "pistes"), ("vias", "vias"), ("pads", "pastilles"), ("copperpour", "remplissages"),
                  ("footprints", "placement"))
# Valeurs par défaut de §3.8, quand le projet .kicad_pro est absent.
DEFAUT_ISOLEMENT = 0.2
DEFAUT_LARGEURS = [0.254, 0.3, 3.0]
DEFAUT_VIA = {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.5, "diametre_max": 0.8, "trou_min": 0.25,
              "trou_max": 0.4}
DEFAUT_TROU_TROU = 0.254
CAUSE_DRU = "règle personnalisée KiCad (.kicad_dru) non interprétée en vague 1"

_JETON = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')
_ECHAPPE = re.compile(r"\\(.)")
_ENTIER = re.compile(r"-?\d+")
_INTERNE = re.compile(r"In(\d+)\.Cu")


# --------------------------------------------------------------------------------------------------------------
# S-expressions
# --------------------------------------------------------------------------------------------------------------
def lire_sexpr(texte, nom):
    """Liste imbriquée de chaînes ; lève ``ErreurEntree`` si les parenthèses ne s'équilibrent pas."""
    pile = [[]]
    for m in _JETON.finditer(texte):
        t = m.group(0)
        if t == "(":
            nouveau = []
            pile[-1].append(nouveau)
            pile.append(nouveau)
        elif t == ")":
            if len(pile) == 1:
                raise ErreurEntree(f"{nom} : fichier illisible (parenthèse fermante en trop)")
            pile.pop()
        elif t[0] == '"':
            pile[-1].append(_ECHAPPE.sub(lambda c: {"n": "\n", "t": "\t"}.get(c.group(1), c.group(1)), t[1:-1]))
        else:
            pile[-1].append(t)
    if len(pile) != 1:
        raise ErreurEntree(f"{nom} : fichier illisible (parenthèse non fermée)")
    return pile[0]


def ecrire_sexpr(e):
    if isinstance(e, list):
        return "(" + " ".join(ecrire_sexpr(c) for c in e) + ")"
    if re.fullmatch(r"[^\s()\"]+", e or ""):
        return e
    return '"' + (e or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def enfants(e, nom):
    return [c for c in e[1:] if isinstance(c, list) and c and c[0] == nom]


def un(e, nom):
    for c in e[1:]:
        if isinstance(c, list) and c and c[0] == nom:
            return c
    return None


def atomes(e):
    return [c for c in e[1:] if isinstance(c, str)] if e else []


def drapeau(e, nom, debut=1):
    """Drapeau ``nom`` : atome nu, ``(nom)`` ou ``(nom yes)``."""
    if nom in [c for c in e[debut:] if isinstance(c, str)]:
        return True
    c = un(e, nom)
    if c is None:
        return False
    return len(c) < 2 or c[1] in ("yes", "true")


class _Lecteur:
    """Conversions de nombres avec le nom du fichier dans les messages."""

    def __init__(self, nom):
        self.nom = nom

    def nombre(self, v, quoi):
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise ErreurEntree(f"{self.nom} : {quoi} : nombre attendu") from None
        if not math.isfinite(x):
            raise ErreurEntree(f"{self.nom} : {quoi} : nombre invalide")
        return x

    def point(self, e, quoi):
        if e is None or len(e) < 3:
            raise ErreurEntree(f"{self.nom} : {quoi} : coordonnées manquantes")
        return (self.nombre(e[1], quoi), self.nombre(e[2], quoi))

    def valeur(self, e, nom, quoi, defaut=None):
        c = un(e, nom)
        if c is None or len(c) < 2:
            return defaut
        return self.nombre(c[1], quoi)


def _r(v):
    x = round(v, 9)
    return 0.0 if x == 0 else x


def _rp(p):
    return [_r(p[0]), _r(p[1])]


def _angle(a):
    a = math.fmod(a, 360.0)
    if a < 0:
        a += 360.0
    return 0.0 if abs(a - 360.0) < 1e-9 or abs(a) < 1e-9 else _r(a)


# --------------------------------------------------------------------------------------------------------------
# Pièce : transformations
# --------------------------------------------------------------------------------------------------------------
class _Pied:
    def __init__(self, fx, fy, theta, dessous):
        self.fx, self.fy, self.theta, self.dessous = fx, fy, theta, dessous
        t = math.radians(theta)
        self.c, self.s = math.cos(t), math.sin(t)

    def fichier(self, p):
        """Local du fichier → monde du fichier."""
        lx, ly = p
        return (self.fx + lx * self.c + ly * self.s, self.fy - lx * self.s + ly * self.c)

    def conv(self, p):
        """Vecteur local du fichier (pièce ou pastille) → repère local de §0.4."""
        return (p[0], p[1]) if self.dessous else (p[0], -p[1])

    def rotation_relative(self, phi):
        return _angle(self.theta - phi if self.dessous else phi - self.theta)


# --------------------------------------------------------------------------------------------------------------
# Importeur
# --------------------------------------------------------------------------------------------------------------
def importer(chemin):
    """``entree_carte/2`` d'un fichier ``.kicad_pcb`` (et de ses ``.kicad_pro`` et ``.kicad_dru`` voisins)."""
    return _Importeur(chemin).importer()


class _Importeur:
    def __init__(self, chemin):
        self.chemin = chemin
        self.nom = os.path.basename(chemin)
        self.L = _Lecteur(self.nom)
        self.avertissements = []

    def avertir(self, texte):
        if texte not in self.avertissements:
            self.avertissements.append(texte)

    # ----------------------------------------------------------------------------------------------------------
    def importer(self):
        try:
            with open(self.chemin, "rb") as f:
                brut = f.read()
        except OSError:
            raise ErreurEntree(f"{self.nom} : lecture impossible") from None
        self.sha256 = hashlib.sha256(brut).hexdigest()
        try:
            texte = brut.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ErreurEntree(f"{self.nom} : fichier illisible (texte UTF-8 attendu)") from None
        racines = [c for c in lire_sexpr(texte, self.nom) if isinstance(c, list)]
        if not racines or not racines[0] or racines[0][0] != "kicad_pcb":
            raise ErreurEntree(f"{self.nom} : ce n'est pas un fichier de carte KiCad (kicad_pcb)")
        self.B = racines[0]
        self._controler_version()
        self._lire_couches()
        self.pieds = enfants(self.B, "footprint")
        if enfants(self.B, "module"):
            raise ErreurEntree(f"{self.nom} : fichier antérieur à KiCad 6 (empreintes « module »)")
        self._controler_pieces()
        self._lire_contour()
        self._lire_reseaux()
        self._lire_projet()
        self.composants = [self._composant(i, fp) for i, fp in enumerate(self.pieds)]
        self._canaux()
        self._cuivre_libre()
        self._zones()
        self._couches_inutilisees()
        self._regions_et_boites()
        return self._resultat()

    # ----------------------------------------------------------------------------------------------------------
    def _controler_version(self):
        v = un(self.B, "version")
        if v is None or len(v) < 2 or not _ENTIER.fullmatch(v[1]):
            raise ErreurEntree(f"{self.nom} : version du fichier absente ou illisible")
        self.version = int(v[1])
        if self.version < VERSION_MIN:
            raise ErreurEntree(f"{self.nom} : fichier antérieur à KiCad 6 (version {self.version} < {VERSION_MIN}) ; "
                               "ouvrez-le et enregistrez-le avec KiCad 6 ou plus récent")

    def _lire_couches(self):
        lc = un(self.B, "layers")
        cuivre = {}
        for c in (lc or [])[1:]:
            if not isinstance(c, list) or len(c) < 3:
                continue
            n = c[1]
            if n == "F.Cu":
                rang = (0, 0)
            elif n == "B.Cu":
                rang = (2, 0)
            elif _INTERNE.fullmatch(n):
                rang = (1, int(_INTERNE.fullmatch(n).group(1)))
            else:
                continue
            cuivre[n] = (rang, c[2] if c[2] in TYPES_COUCHE else None)
        if "F.Cu" not in cuivre or "B.Cu" not in cuivre:
            raise ErreurEntree(f"{self.nom} : couches de cuivre F.Cu et B.Cu attendues dans (layers …)")
        self.cuivre = sorted(cuivre, key=lambda n: cuivre[n][0])
        self.type_kicad = {n: cuivre[n][1] for n in cuivre}
        self.rang = {n: i for i, n in enumerate(self.cuivre)}
        self.nom_couche = {}
        for n in self.cuivre:
            m = _INTERNE.fullmatch(n)
            self.nom_couche[n] = "TopLayer" if n == "F.Cu" else "BottomLayer" if n == "B.Cu" else f"MidLayer{m.group(1)}"
        self.epaisseur = {}
        st = un(un(self.B, "setup") or ["setup"], "stackup")
        for c in enfants(st or ["stackup"], "layer"):
            if len(c) >= 2 and c[1] in cuivre:
                self.epaisseur[c[1]] = self.L.valeur(c, "thickness", f"épaisseur de {c[1]}")

    def couches_de(self, noms):
        """Noms KiCad (jokers ``*.Cu``, ``F&B.Cu``) → couches de cuivre KiCad, dans l'ordre physique."""
        s = set()
        for n in noms:
            if n == "*.Cu":
                s.update(self.cuivre)
            elif n == "*In.Cu":
                s.update(self.cuivre[1:-1])
            elif n == "F&B.Cu":
                s.update(("F.Cu", "B.Cu"))
            elif n in self.rang:
                s.add(n)
        return [n for n in self.cuivre if n in s]

    def _controler_pieces(self):
        vus = {}
        self.refs = []
        for i, fp in enumerate(self.pieds):
            couche = (un(fp, "layer") or [None, ""])[1]
            ref = self._texte_pied(fp, "Reference", "reference")
            self.refs.append(ref)
            if couche not in ("F.Cu", "B.Cu"):
                raise ErreurEntree(f"{self.nom} : pièce {ref or i} sur une couche interne ou inconnue ({couche}) : "
                                   "refusée")
            if ref:
                vus.setdefault(ref, []).append(i)
        self.designateurs = []
        for i, ref in enumerate(self.refs):
            self.designateurs.append(ref if ref else f"__piece_{i}")
        for ref, idx in vus.items():
            if len(idx) < 2:
                continue
            avec = [i for i in idx if enfants(self.pieds[i], "pad")]
            if len(avec) >= 2:
                raise ErreurEntree(f"{self.nom} : panneau ou carte non annotée (référence {ref} répétée "
                                   f"{len(idx)} fois) : importez une seule carte, annotée")
            garde = avec[0] if avec else idx[0]
            for i in idx:
                if i != garde:
                    self.designateurs[i] = f"__piece_{i}"
            self.avertir(f"référence {ref} répétée sur des pièces sans pastille : renommées __piece_<k>")

    def _texte_pied(self, fp, propriete, fp_text):
        for p in enfants(fp, "property"):
            if len(p) >= 3 and p[1] == propriete:
                return p[2]
        for t in enfants(fp, "fp_text"):
            if len(t) >= 3 and t[1] == fp_text:
                return t[2]
        return ""

    def _pied(self, fp, quoi):
        at = un(fp, "at")
        fx, fy = self.L.point(at, quoi)
        theta = self.L.nombre(at[3], quoi) if len(at) > 3 else 0.0
        return _Pied(fx, fy, theta, (un(fp, "layer") or [None, ""])[1] == "B.Cu")

    # ----------------------------------------------------------------------------------------------------------
    # Formes graphiques
    # ----------------------------------------------------------------------------------------------------------
    def _pts(self, e, quoi):
        sortie = []
        for c in (e or [])[1:]:
            if not isinstance(c, list) or not c:
                continue
            if c[0] == "xy":
                sortie.append(self.L.point(c, quoi))
            elif c[0] == "arc":
                a = self.L.point(un(c, "start"), quoi)
                m = self.L.point(un(c, "mid"), quoi)
                b = self.L.point(un(c, "end"), quoi)
                arc = G.discretiser_arc_trois_points(a, m, b)
                if sortie and math.hypot(sortie[-1][0] - arc[0][0], sortie[-1][1] - arc[0][1]) < 1e-9:
                    arc = arc[1:]
                sortie.extend(tuple(p) for p in arc)
        return sortie

    def _forme(self, g, quoi):
        """(points, fermee) de la forme ``g`` dans son propre repère (fichier), ou None."""
        k = g[0][3:]
        if k == "line":
            return [self.L.point(un(g, "start"), quoi), self.L.point(un(g, "end"), quoi)], False
        if k == "arc":
            a, b = self.L.point(un(g, "start"), quoi), self.L.point(un(g, "end"), quoi)
            if un(g, "mid") is None:
                raise ErreurEntree(f"{self.nom} : {quoi} : arc sans point milieu (format antérieur à KiCad 6)")
            return [tuple(p) for p in G.discretiser_arc_trois_points(a, self.L.point(un(g, "mid"), quoi), b)], False
        if k == "circle":
            c, e = self.L.point(un(g, "center"), quoi), self.L.point(un(g, "end"), quoi)
            r = math.hypot(e[0] - c[0], e[1] - c[1])
            return [tuple(p) for p in G.discretiser_cercle(c, r)], True
        if k == "rect":
            a, b = self.L.point(un(g, "start"), quoi), self.L.point(un(g, "end"), quoi)
            return [(a[0], a[1]), (b[0], a[1]), (b[0], b[1]), (a[0], b[1])], True
        if k == "poly":
            return self._pts(un(g, "pts"), quoi), True
        if k == "curve":
            p = self._pts(un(g, "pts"), quoi)
            if len(p) != 4:
                raise ErreurEntree(f"{self.nom} : {quoi} : courbe de Bézier à 4 points attendue")
            return [tuple(q) for q in G.discretiser_bezier(*p)], False
        return None

    def _trait(self, g):
        s = un(g, "stroke")
        w = self.L.valeur(s, "width", "largeur de trait") if s else None
        if w is None:
            w = self.L.valeur(g, "width", "largeur de trait", 0.0)
        return max(0.0, w)

    @staticmethod
    def _rempli(g, defaut):
        f = un(g, "fill")
        if f is None or len(f) < 2:
            return defaut
        return f[1] in ("solid", "yes", "true")

    def _polygones_forme(self, g, quoi, conv=None):
        """Polygones de cuivre (repère de ``conv``) d'une forme : capsules, arc épais, polygone plein."""
        f = self._forme(g, quoi)
        if f is None:
            return []
        pts, fermee = f
        w = self._trait(g)
        conv = conv or (lambda p: p)
        k = g[0][3:]
        if k == "arc" and len(pts) >= 2:
            a, m, b = (conv(self.L.point(un(g, n), quoi)) for n in ("start", "mid", "end"))
            arc = G.arc_trois_points(a, m, b)
            if arc is not None:
                return [G.arc_epais(arc[0], arc[1], arc[2], arc[3], w)]
        pts = [conv(p) for p in pts]
        if fermee and self._rempli(g, k == "poly"):
            poly = G.orienter(G.nettoyer(pts))
            return [G.decaler_polygone(poly, w / 2.0)] if len(poly) >= 3 else []
        chaine = pts + ([pts[0]] if fermee else [])
        if w <= 0.0:
            w = 0.001
            self.avertir("trait de cuivre de largeur nulle : largeur de 0,001 mm retenue")
        return [G.orienter(c) for c in G.polyligne_epaisse(chaine, w)]

    def _rectangle_texte(self, t, quoi, conv, pied=None):
        """Rectangle (par excès) d'un texte : n caractères × hauteur, centré sur sa position."""
        at = un(t, "at")
        x, y = self.L.point(at, quoi)
        angle = self.L.nombre(at[3], quoi) if len(at) > 3 else 0.0
        texte = t[2] if t[0] == "fp_text" else t[1]
        eff = un(t, "effects")
        fo = un(eff, "font") if eff else None
        sz = un(fo, "size") if fo else None
        sx, sy = (self.L.point(sz, quoi) if sz else (1.0, 1.0))
        ep = self.L.valeur(fo, "thickness", quoi, 0.15) if fo else 0.15
        lignes = str(texte).split("\n")
        hw = max(len(s) for s in lignes) * sy / 2.0 + ep
        hh = len(lignes) * sx * 1.4 / 2.0 + ep
        a = math.radians(angle)
        coins = []
        for cx, cy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
            # angle du texte absolu, visuel trigonométrique ; repère du fichier y vers le bas
            dx = cx * math.cos(a) + cy * math.sin(a)
            dy = -cx * math.sin(a) + cy * math.cos(a)
            if pied is not None:
                # position locale de la pièce, angle absolu : on passe par le monde du fichier
                wx, wy = pied.fichier((x, y))
                coins.append(conv((wx + dx, wy + dy)))
            else:
                coins.append(conv((x + dx, y + dy)))
        return G.orienter(coins)

    # ----------------------------------------------------------------------------------------------------------
    # Contour et découpes
    # ----------------------------------------------------------------------------------------------------------
    def _lire_contour(self):
        chaines = []
        for g in self.B[1:]:
            if isinstance(g, list) and g and g[0] in FORMES_GR and (un(g, "layer") or [None, ""])[1] == "Edge.Cuts":
                f = self._forme(g, f"{g[0]} de Edge.Cuts")
                if f:
                    chaines.append((f[0], None, f[1]))
        for i, fp in enumerate(self.pieds):
            pied = self._pied(fp, f"empreinte {self.designateurs[i]}")
            for g in fp[1:]:
                if isinstance(g, list) and g and g[0] in FORMES_FP and \
                        (un(g, "layer") or [None, ""])[1] == "Edge.Cuts":
                    f = self._forme(g, f"{g[0]} de {self.designateurs[i]}")
                    if f:
                        chaines.append(([pied.fichier(p) for p in f[0]], self.designateurs[i], f[1]))
        if not chaines:
            raise ErreurEntree(f"{self.nom} : aucun contour de carte (couche Edge.Cuts vide)")
        try:
            boucles = G.assembler_boucles(chaines)
        except ErreurEntree as e:
            raise ErreurEntree(f"{self.nom} : Edge.Cuts : {e}") from None
        if not boucles:
            raise ErreurEntree(f"{self.nom} : contour de carte dégénéré (Edge.Cuts)")
        boucles.sort(key=lambda b: -G.aire(b[0]))
        ext = boucles[0][0]
        for pts, _e in boucles[1:]:
            if not G.polygone_dans_polygone(pts, ext):
                raise ErreurEntree(f"{self.nom} : panneau (plusieurs boucles extérieures disjointes dans Edge.Cuts) : "
                                   "importez une seule carte")
        self.x0 = min(p[0] for p in ext)
        self.y0 = max(p[1] for p in ext)
        self.contour = G.orienter(G.nettoyer([self.monde(p) for p in ext]))
        self.decoupes = []
        for k, (pts, etiq) in enumerate(boucles[1:], 1):
            proprio = next(iter(etiq)) if len(etiq) == 1 else None
            self.decoupes.append({"id": f"D{k}", "points": G.orienter(G.nettoyer([self.monde(p) for p in pts])),
                                  "proprietaire": proprio})
        b = G.boite(self.contour)
        self.boite = [0.0, 0.0, _r(b[2]), _r(b[3])]
        self.aire_utile = G.aire_balayage([self.contour], [d["points"] for d in self.decoupes])

    def monde(self, p):
        return [_r(p[0] - self.x0), _r(self.y0 - p[1])]

    # ----------------------------------------------------------------------------------------------------------
    # Réseaux, projet, règles
    # ----------------------------------------------------------------------------------------------------------
    def _lire_reseaux(self):
        self.num_reseau = {}
        self.reseaux = []
        for n in enfants(self.B, "net"):
            if len(n) >= 3 and _ENTIER.fullmatch(n[1]):
                self.num_reseau[int(n[1])] = n[2]
                if n[2] and n[2] not in self.reseaux:
                    self.reseaux.append(n[2])

    def reseau(self, e):
        nn = un(e, "net_name")
        if nn is not None and len(nn) >= 2:
            return self._ajouter_reseau(nn[1])
        n = un(e, "net")
        if n is None or len(n) < 2:
            return ""
        if len(n) >= 3:
            return self._ajouter_reseau(n[2])
        if _ENTIER.fullmatch(n[1]):
            return self.num_reseau.get(int(n[1]), "")
        return self._ajouter_reseau(n[1])

    def _ajouter_reseau(self, nom):
        if nom and nom not in self.reseaux:
            self.reseaux.append(nom)
        return nom

    def _lire_projet(self):
        base = os.path.splitext(self.chemin)[0]
        self.nom_pro = os.path.basename(base) + ".kicad_pro"
        self.pro = None
        if os.path.isfile(base + ".kicad_pro"):
            try:
                with open(base + ".kicad_pro", encoding="utf-8-sig") as f:
                    self.pro = json.load(f)
            except (OSError, UnicodeDecodeError, ValueError):
                raise ErreurEntree(f"{self.nom_pro} : projet KiCad illisible (JSON attendu)") from None
            if not isinstance(self.pro, dict):
                raise ErreurEntree(f"{self.nom_pro} : projet KiCad illisible (objet JSON attendu)")
        else:
            self.avertir(f"projet {self.nom_pro} absent : règles par défaut (§3.8 : isolement 0,2 mm, largeurs "
                         "[0,254 ; 0,3 ; 3,0] mm, via 0,6 / 0,3 mm)")
        self.dru = []
        self.nom_dru = os.path.basename(base) + ".kicad_dru"
        if os.path.isfile(base + ".kicad_dru"):
            try:
                with open(base + ".kicad_dru", encoding="utf-8-sig") as f:
                    texte = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))
                self.dru = enfants(["dru"] + lire_sexpr(texte, self.nom_dru), "rule")
            except (OSError, UnicodeDecodeError):
                raise ErreurEntree(f"{self.nom_dru} : règles KiCad illisibles") from None
        self._classes()

    def _nombre_pro(self, d, cle, quoi):
        v = d.get(cle) if isinstance(d, dict) else None
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ErreurEntree(f"{self.nom_pro} : {quoi}.{cle} : nombre attendu")
        return float(v)

    def _classes(self):
        """Classes de réseaux : affectations ∪ motifs ∪ classes[].nets ; classe effective de chaque réseau."""
        ns = (self.pro or {}).get("net_settings") or {}
        liste = ns.get("classes") or []
        if not isinstance(liste, list):
            raise ErreurEntree(f"{self.nom_pro} : net_settings.classes : liste attendue")
        self.def_classes = {}
        ordre = []
        for c in liste:
            if isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"] not in self.def_classes:
                self.def_classes[c["name"]] = c
                ordre.append(c["name"])
        if "Default" not in self.def_classes:
            if self.pro is not None:
                self.avertir(f"{self.nom_pro} : classe Default absente : valeurs par défaut de §3.8")
            self.def_classes["Default"] = {}
        self.noms_classes = ["Default"] + [n for n in ordre if n != "Default"]
        membres = {n: [] for n in self.noms_classes if n != "Default"}
        effective = {}
        affect = ns.get("netclass_assignments") or {}
        if isinstance(affect, dict):
            for net, cl in affect.items():
                for c in ([cl] if isinstance(cl, str) else cl if isinstance(cl, list) else []):
                    if isinstance(c, str):
                        membres.setdefault(c, [])
                        if net not in membres[c]:
                            membres[c].append(net)
                        effective.setdefault(net, c)
        for n in ordre:
            for net in self.def_classes[n].get("nets") or []:
                if isinstance(net, str) and n != "Default":
                    if net not in membres[n]:
                        membres[n].append(net)
                    effective.setdefault(net, n)
        motifs = ns.get("netclass_patterns") or []
        for net in self.reseaux:
            for m in motifs if isinstance(motifs, list) else []:
                if isinstance(m, dict) and isinstance(m.get("pattern"), str) and isinstance(m.get("netclass"), str) \
                        and fnmatch.fnmatchcase(net, m["pattern"]):
                    membres.setdefault(m["netclass"], [])
                    if net not in membres[m["netclass"]]:
                        membres[m["netclass"]].append(net)
                    effective.setdefault(net, m["netclass"])
        for c in membres:
            if c not in self.noms_classes:
                self.avertir(f"{self.nom_pro} : classe {c} affectée mais non définie : classe Default retenue")
        self.classes = {c: [n for n in self.reseaux if n in v] + [n for n in v if n not in self.reseaux]
                        for c, v in membres.items() if c != "Default"}
        self.classe_de = {n: (effective.get(n) if effective.get(n) in self.noms_classes else "Default")
                          for n in self.reseaux}

    def indice_classe(self, reseau):
        return self.noms_classes.index(self.classe_de.get(reseau, "Default")) if reseau else 0

    def _valeur_classe(self, nom, cle, defaut):
        v = self._nombre_pro(self.def_classes.get(nom, {}), cle, f"classe {nom}")
        if v is None and nom != "Default":
            v = self._nombre_pro(self.def_classes["Default"], cle, "classe Default")
        return defaut if v is None else v

    def _regles(self):
        pro = self.pro
        regles_pro = (((pro or {}).get("board") or {}).get("design_settings") or {}).get("rules") or {}
        q = "board.design_settings.rules"
        min_iso = self._nombre_pro(regles_pro, "min_clearance", q) or 0.0
        mtw = self._nombre_pro(regles_pro, "min_track_width", q)
        mvd = self._nombre_pro(regles_pro, "min_via_diameter", q)
        mth = self._nombre_pro(regles_pro, "min_through_hole_diameter", q)
        iso = [self._valeur_classe(n, "clearance", DEFAUT_ISOLEMENT) for n in self.noms_classes]
        matrice = [[_r(max(a, b, min_iso)) for b in iso] for a in iso]

        def largeurs(nom):
            if pro is None:
                return list(DEFAUT_LARGEURS)
            tw = self._valeur_classe(nom, "track_width", DEFAUT_LARGEURS[1])
            return [_r(mtw if mtw is not None else tw), _r(tw), None]

        def via(nom):
            if pro is None:
                return dict(DEFAUT_VIA)
            vd = self._valeur_classe(nom, "via_diameter", DEFAUT_VIA["diametre"])
            vt = self._valeur_classe(nom, "via_drill", DEFAUT_VIA["trou"])
            return {"diametre": _r(vd), "trou": _r(vt), "diametre_min": _r(mvd if mvd is not None else vd),
                    "diametre_max": None, "trou_min": _r(mth if mth is not None else vt), "trou_max": None}

        reseaux = {}
        for n in self.reseaux:
            cl = self.classe_de.get(n, "Default")
            k = self.noms_classes.index(cl)
            reseaux[n] = {"largeurs": largeurs(cl), "via": via(cl),
                          "classes_isolement": {"piste": k, "via": k, "surface": k}, "couches_permises": None,
                          "priorite_routage": 0, "alimentation_schema": False,
                          "regle_largeur_propre": pro is not None and cl != "Default"}
        non_interp = []
        for r in self.dru:
            nom = r[1] if len(r) > 1 and isinstance(r[1], str) else ""
            cond = un(r, "condition")
            types = [c[1] for c in enfants(r, "constraint") if len(c) > 1]
            non_interp.append({"type": "kicad_dru:" + ",".join(types), "nom": nom,
                               "portee1": cond[1] if cond and len(cond) > 1 else "", "portee2": "",
                               "cause": CAUSE_DRU})
        if self.dru:
            self.avertir(f"{self.nom_dru} : {len(self.dru)} règle(s) personnalisée(s) non interprétée(s)")
        if pro is None:
            trou_trou, garde = DEFAUT_TROU_TROU, None
        else:
            trou_trou = self._nombre_pro(regles_pro, "min_hole_to_hole", q)
            garde = self._nombre_pro(regles_pro, "min_copper_edge_clearance", q)
        return {
            "isolement": {"defaut": _r(max(min_iso, iso[0])), "classes": list(self.noms_classes), "matrice": matrice,
                          "classe_reference": 0},
            "reseaux": reseaux,
            "largeur_defaut": largeurs("Default"),
            "via": via("Default"),
            "trou_trou": None if trou_trou is None else _r(trou_trou),
            "garde_bord": None if garde is None else _r(garde),
            "isolement_pieces": None,
            "hauteurs": {"dessus": None, "dessous": None},
            "pieces": {},
            "connexion_plans": None,
            "masque_serigraphie": None,
            "salles": [],
            "tolerances_longueur": {},
            "non_interpretees": non_interp,
            "ignorees": [],
        }

    def _regles_fichier(self):
        sortie = []
        if self.pro is not None:
            for k, n in enumerate(self.noms_classes):
                valeurs = {c: v for c, v in self.def_classes.get(n, {}).items()
                           if isinstance(v, (int, float, str)) and not isinstance(v, bool) and c != "name"}
                sortie.append({"type": "netclass", "nom": n, "priorite": k, "portee1": "", "portee2": "",
                               "valeurs": valeurs})
            rp = (((self.pro.get("board") or {}).get("design_settings") or {}).get("rules") or {})
            if isinstance(rp, dict):
                sortie.append({"type": "design_settings", "nom": "rules", "priorite": 0, "portee1": "",
                               "portee2": "", "valeurs": {c: v for c, v in rp.items()
                                                          if isinstance(v, (int, float, str))}})
        for k, r in enumerate(self.dru):
            cond = un(r, "condition")
            couche = un(r, "layer")
            sortie.append({"type": "kicad_dru", "nom": r[1] if len(r) > 1 and isinstance(r[1], str) else "",
                           "priorite": k, "portee1": cond[1] if cond and len(cond) > 1 else "", "portee2": "",
                           "valeurs": {"contraintes": [ecrire_sexpr(c) for c in enfants(r, "constraint")],
                                       "couche": couche[1] if couche and len(couche) > 1 else ""}})
        return sortie

    def _paires(self):
        sortie = []
        ens = set(self.reseaux)
        for n in self.reseaux:
            for sp, sn in (("_P", "_N"), ("+", "-")):
                if n.endswith(sp) and n[:-len(sp)] + sn in ens and n[:-len(sp)]:
                    base = n[:-len(sp)]
                    cl = self.classe_de.get(n, "Default")
                    lg = ec = None
                    if self.pro is not None:
                        lg = self._valeur_classe(cl, "diff_pair_width", None)
                        ec = self._valeur_classe(cl, "diff_pair_gap", None)
                    sortie.append({"nom": base, "positif": n, "negatif": base + sn,
                                   "largeur": None if lg is None else _r(lg), "ecart": None if ec is None else _r(ec)})
        return sortie

    # ----------------------------------------------------------------------------------------------------------
    # Composants
    # ----------------------------------------------------------------------------------------------------------
    def _masque_carte(self):
        if not hasattr(self, "_mc"):
            st = un(self.B, "setup") or ["setup"]
            self._mc = self.L.valeur(st, "pad_to_mask_clearance", "pad_to_mask_clearance")
            if self._mc is None:
                self.avertir("expansion de masque absente de la carte : 0 retenu")
                self._mc = 0.0
        return self._mc

    def _composant(self, i, fp):
        des = self.designateurs[i]
        quoi = f"empreinte {des}"
        pied = self._pied(fp, quoi)
        attr = atomes(un(fp, "attr"))
        nom_fp = fp[1] if len(fp) > 1 and isinstance(fp[1], str) else ""
        chemin = un(fp, "path")
        c = {
            "designateur": des,
            "designateur_source": self.refs[i],
            "empreinte": nom_fp.split(":", 1)[-1],
            "description": (un(fp, "descr") or [None, ""])[1],
            "reference": "",
            "valeur": self._texte_pied(fp, "Value", "value"),
            "genre": "standard",
            "classes_composants": [],
            "canal": None,
            "hauteur": 0.0,
            "source_hauteur": "inconnue",
            "x": None, "y": None,
            "rotation": _angle(pied.theta),
            "face": "dessous" if pied.dessous else "dessus",
            "verrouille": drapeau(fp, "locked", 2),
            "boite": [0.0, 0.0, 0.0, 0.0],
            "pastilles": [],
            "regions": [],
            "vias": [],
            "zones_interdites": [],
            "decoupes": [d["id"] for d in self.decoupes if d["proprietaire"] == des],
        }
        c["x"], c["y"] = self.monde((pied.fx, pied.fy))
        c["_pied"] = pied
        c["_chemin"] = chemin[1] if chemin and len(chemin) > 1 else ""
        c["_fp"] = fp
        masque_fp = self.L.valeur(fp, "solder_mask_margin", quoi)
        for k, pd in enumerate(enfants(fp, "pad")):
            c["pastilles"].append(self._pastille(des, k, pd, pied, masque_fp))
        if un(fp, "net_tie_pad_groups") is not None:
            c["genre"] = "liaison_reseaux"
        elif un(fp, "jumper_pad_groups") is not None or drapeau(fp, "duplicate_pad_numbers_are_jumpers"):
            c["genre"] = "cavalier"
        c["_board_only"] = "board_only" in attr
        return c

    def _pastille(self, des, k, pd, pied, masque_fp):
        quoi = f"pastille {k} de {des}"
        if len(pd) < 4:
            raise ErreurEntree(f"{self.nom} : {quoi} : type ou forme manquant")
        nom, typ, forme_k = pd[1], pd[2], pd[3]
        at = un(pd, "at")
        px, py = self.L.point(at, quoi)
        phi = self.L.nombre(at[3], quoi) if len(at) > 3 else 0.0
        w, h = self.L.point(un(pd, "size"), quoi)
        rho = pied.rotation_relative(phi)
        drill = un(pd, "drill")
        offset = (0.0, 0.0)
        trou = None
        traversante = typ in ("thru_hole", "np_thru_hole")
        if drill is not None:
            off = un(drill, "offset")
            if off is not None:
                offset = self.L.point(off, quoi)
            vals = [a for a in drill[1:] if isinstance(a, str) and a != "oval"]
            ovale = "oval" in drill[1:]
            if traversante and vals:
                dx = self.L.nombre(vals[0], quoi)
                dy = self.L.nombre(vals[1], quoi) if ovale and len(vals) > 1 else dx
                o = pied.conv(offset)
                trou = {"diametre": _r(min(dx, dy)), "forme": "oblong" if abs(dx - dy) > 1e-9 else "rond",
                        "longueur": _r(max(dx, dy)), "rotation": 0.0 if dx >= dy else 90.0,
                        "decalage": [_r(-o[0]), _r(-o[1])], "metallise": typ == "thru_hole"}
        if traversante and trou is None:
            raise ErreurEntree(f"{self.nom} : {quoi} : pastille traversante sans perçage")
        a = pied.conv((px, py))
        o = G.tourner(pied.conv(offset), rho)
        cx, cy = a[0] + o[0], a[1] + o[1]
        rr = self.L.valeur(pd, "roundrect_rratio", quoi, 0.0)
        cr = self.L.valeur(pd, "chamfer_ratio", quoi, 0.0)
        coins = atomes(un(pd, "chamfer"))
        forme, rayon, poly = "rect", 0.0, None
        if forme_k == "circle":
            forme, h = "rond", w
        elif forme_k == "oval":
            forme = "rond" if abs(w - h) < 1e-12 else "oblong"
        elif forme_k in ("roundrect", "chamfered") and cr > 0 and coins:
            forme, poly = "polygone", self._chanfrein(w, h, rr * min(w, h), cr * min(w, h), coins)
        elif forme_k == "roundrect":
            forme, rayon = ("rect_arrondi", rr * min(w, h)) if rr > 0 else ("rect", 0.0)
        elif forme_k == "trapezoid":
            d = un(pd, "rect_delta")
            ddx, ddy = self.L.point(d, quoi) if d else (0.0, 0.0)
            if abs(ddx) > 1e-12 or abs(ddy) > 1e-12:
                hx, hy, ex, ey = w / 2.0, h / 2.0, ddx / 2.0, ddy / 2.0
                forme, poly = "polygone", [(-hx - ey, hy + ex), (-hx + ey, -hy - ex), (hx - ey, -hy + ex),
                                           (hx + ey, hy - ex)]
        elif forme_k == "custom":
            forme, poly = "polygone", self._composee(pd, w, h, quoi)
        elif forme_k != "rect":
            raise ErreurEntree(f"{self.nom} : {quoi} : forme {forme_k} inconnue")
        points = []
        if poly is not None:
            for p in poly:
                q = G.tourner(pied.conv(p), rho)
                points.append(_rp((cx + q[0], cy + q[1])))
            points = G.orienter(G.nettoyer(points))
        couches = self.couches_de(atomes(un(pd, "layers")))
        if typ == "np_thru_hole" and trou is not None and max(w, h) <= trou["longueur"] + 1e-9:
            couches = []
        if un(pd, "padstack") is not None:
            self.avertir("pile de pastille KiCad 9 (padstack) non interprétée : forme des couches extérieures "
                         "retenue")
        masque = self.L.valeur(pd, "solder_mask_margin", quoi)
        if masque is None:
            masque = masque_fp if masque_fp is not None else self._masque_carte()
        cote = "B.Cu" if pied.dessous else "F.Cu"
        if traversante or len(couches) > 1:
            face = "toutes"
        elif couches == [cote] or not couches:
            face = "composant"
        else:
            face = "opposee"
        if not traversante:
            self.avertir("via dans une pastille CMS : aucune règle KiCad, interdit (§3.8)")
        reseau = self.reseau(pd)
        return {
            "id": f"{des}#{k}", "nom": nom, "reseau": reseau, "x": _r(cx), "y": _r(cy), "forme": forme,
            "largeur": _r(w), "hauteur": _r(h), "rotation": rho, "rayon_coin": _r(rayon), "points": points,
            "couches": [self.nom_couche[n] for n in couches], "face": face, "trou": trou,
            "traversante": traversante, "expansion_masque": _r(masque), "pile": None,
            "via_dans_pastille_permis": traversante, "classe_isolement": self.indice_classe(reseau),
            "_inutilisees": traversante and drapeau(pd, "remove_unused_layers"),
            "_garder_bouts": drapeau(pd, "keep_end_layers"),
        }

    @staticmethod
    def _chanfrein(w, h, r, c, coins):
        """Rectangle chanfreiné (et arrondi aux autres coins si r > 0), repère de la pastille du fichier (y vers le
        bas : « top » = y négatif)."""
        hx, hy = w / 2.0, h / 2.0
        c, r = min(c, hx, hy), min(r, hx, hy)
        tour = [("top_left", (-hx, -hy)), ("top_right", (hx, -hy)), ("bottom_right", (hx, hy)),
                ("bottom_left", (-hx, hy))]
        pts = []
        for i, (nom, p) in enumerate(tour):
            a, b = tour[i - 1][1], tour[(i + 1) % 4][1]
            ua = ((a[0] - p[0]) / max(abs(a[0] - p[0]), 1e-300), (a[1] - p[1]) / max(abs(a[1] - p[1]), 1e-300))
            ub = ((b[0] - p[0]) / max(abs(b[0] - p[0]), 1e-300), (b[1] - p[1]) / max(abs(b[1] - p[1]), 1e-300))
            ua = (round(ua[0]), round(ua[1]))
            ub = (round(ub[0]), round(ub[1]))
            if nom in coins and c > 0:
                pts += [(p[0] + c * ua[0], p[1] + c * ua[1]), (p[0] + c * ub[0], p[1] + c * ub[1])]
            elif r > 0:
                s = (p[0] + r * ua[0], p[1] + r * ua[1])
                e = (p[0] + r * ub[0], p[1] + r * ub[1])
                ctr = (s[0] + r * ub[0], s[1] + r * ub[1])
                k = r / math.sqrt(2.0)
                m = (ctr[0] - k * (ua[0] + ub[0]), ctr[1] - k * (ua[1] + ub[1]))
                pts += [tuple(q) for q in G.discretiser_arc_trois_points(s, m, e, exces=True)]
            else:
                pts.append(p)
        return pts

    def _composee(self, pd, w, h, quoi):
        """Pastille composée : union des primitives et de l'ancre. Exacte si une primitive pleine contient tout le
        reste ; sinon enveloppe convexe (par excès, signalé)."""
        opt = un(pd, "options")
        ancre = (un(opt, "anchor") or [None, "rect"])[1] if opt else "rect"
        formes = [G.discretiser_cercle((0, 0), w / 2.0, exces=True) if ancre == "circle"
                  else [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]]]
        pleines = []
        for g in (un(pd, "primitives") or ["primitives"])[1:]:
            if not isinstance(g, list) or not g or g[0] not in FORMES_GR:
                continue
            for poly in self._polygones_forme(g, quoi):
                formes.append(poly)
                if g[0] in ("gr_poly", "gr_rect", "gr_circle") and self._trait(g) == 0.0 and self._rempli(g, True):
                    pleines.append(poly)
        for p in pleines:
            if all(all(G.point_dans_polygone(q, p) or G.distance_point_polygone(q, p) < 1e-6 for q in f)
                   for f in formes if f is not p):
                return p
        if len(formes) == 1:
            return formes[0]
        self.avertir("pastille composée approchée par l'enveloppe convexe de ses primitives (par excès)")
        return G.enveloppe_convexe([q for f in formes for q in f])

    def _canaux(self):
        groupes = {}
        for c in self.composants:
            ch = c["_chemin"]
            if ch:
                groupes.setdefault(ch.rstrip("/").split("/")[-1], []).append(c)
        for uuid, lst in groupes.items():
            if len(lst) > 1:
                for c in lst:
                    feuille = c["_chemin"].rstrip("/").rsplit("/", 1)[0]
                    c["canal"] = {"groupe": uuid, "instance": feuille or "/"}

    # ----------------------------------------------------------------------------------------------------------
    # Cuivre libre, zones
    # ----------------------------------------------------------------------------------------------------------
    def _cuivre_libre(self):
        self.cuivre_existant = []
        self.obstacles = []
        self.pistes_libres = {n: 0 for n in self.cuivre}
        self.extremites = []  # (couche KiCad, réseau, point commun) pour remove_unused_layers
        for e in self.B[1:]:
            if not isinstance(e, list) or not e:
                continue
            k = e[0]
            if k in ("segment", "arc"):
                couche = (un(e, "layer") or [None, ""])[1]
                if couche not in self.rang:
                    continue
                quoi = f"piste de {couche}"
                a, b = self.L.point(un(e, "start"), quoi), self.L.point(un(e, "end"), quoi)
                pts = [a, b] if k == "segment" else G.discretiser_arc_trois_points(a, self.L.point(un(e, "mid"), quoi), b)
                res = self.reseau(e)
                self.pistes_libres[couche] += 1
                self._existant("piste", res, [couche], drapeau(e, "locked"),
                               {"points": [self.monde(p) for p in pts],
                                "largeur": _r(self.L.valeur(e, "width", quoi, 0.0))})
                self.extremites += [(couche, res, self.monde(a)), (couche, res, self.monde(b))]
            elif k == "via":
                quoi = "via"
                x, y = self.L.point(un(e, "at"), quoi)
                lc = [n for n in atomes(un(e, "layers")) if n in self.rang]
                if len(lc) < 2:
                    lc = [self.cuivre[0], self.cuivre[-1]]
                r0, r1 = sorted((self.rang[lc[0]], self.rang[lc[-1]]))
                couches = self.cuivre[r0:r1 + 1]
                res = self.reseau(e)
                p = self.monde((x, y))
                self._existant("via", res, couches, drapeau(e, "locked"),
                               {"x": p[0], "y": p[1], "diametre": _r(self.L.valeur(e, "size", quoi, 0.0)),
                                "trou": _r(self.L.valeur(e, "drill", quoi, 0.0))})
                self.extremites += [(n, res, p) for n in couches]
            elif k in FORMES_GR:
                couche = (un(e, "layer") or [None, ""])[1]
                if couche not in self.rang:
                    continue
                res = self.reseau(e)
                for poly in self._polygones_forme(e, f"{k} de {couche}", self.monde):
                    self._existant("region", res, [couche], drapeau(e, "locked"), {"points": [_rp(q) for q in poly]})
            elif k == "gr_text":
                couche = (un(e, "layer") or [None, ""])[1]
                if couche in self.rang:
                    self.obstacles.append({"id": f"O{len(self.obstacles) + 1}", "type": "texte",
                                           "couches": [self.nom_couche[couche]],
                                           "points": self._rectangle_texte(e, "texte de cuivre", self.monde)})

    def _existant(self, typ, reseau, couches, verrou, geom):
        d = {"id": f"E{len(self.cuivre_existant) + 1}", "type": typ, "reseau": reseau,
             "couches": [self.nom_couche[n] for n in couches], "verrouille": verrou}
        d.update(geom)
        self.cuivre_existant.append(d)

    def _couches_zone(self, z):
        lst = un(z, "layers")
        noms = atomes(lst) if lst else [(un(z, "layer") or [None, ""])[1]]
        return self.couches_de(noms)

    def _zones(self):
        self.remplissages = []
        self.zones_interdites = []
        sources = [(z, None) for z in enfants(self.B, "zone")]
        for c in self.composants:
            sources += [(z, c) for z in enfants(c["_fp"], "zone")]
        for z, comp in sources:
            couches = self._couches_zone(z)
            polys = enfants(z, "polygon")
            if not couches or not polys:
                continue
            quoi = "zone" + (f" de {comp['designateur']}" if comp else "")
            contours = [G.orienter(G.nettoyer([self.monde(p) for p in self._pts(un(pg, "pts"), quoi)]))
                        for pg in polys]
            contours = [c for c in contours if len(c) >= 3]
            if not contours:
                continue
            ko = un(z, "keepout")
            if ko is not None:
                effets = [nom for cle, nom in EFFETS_KEEPOUT if (un(ko, cle) or [None, ""])[1] == "not_allowed"]
                if len(contours) > 1:
                    self.avertir("zone interdite à trous : seul son contour extérieur est retenu (par excès)")
                pts = contours[0]
                cible = self.zones_interdites
                if comp is not None:
                    pts = G.orienter([_rp(G.monde_vers_local(p, comp)) for p in pts])
                    cible = comp["zones_interdites"]
                    ident = f"{comp['designateur']}/Z{len(cible)}"
                else:
                    ident = f"Z{len(cible) + 1}"
                toutes = len(couches) == len(self.cuivre)
                cible.append({"id": ident, "couches": ["toutes"] if toutes else [self.nom_couche[n] for n in couches],
                              "forme": "polygone", "points": pts, "largeur": 0.0, "effets": effets,
                              "source": "keepout"})
                continue
            nom = (un(z, "name") or [None, ""])[1]
            res = self.reseau(z)
            exclus = contours[1:] + [d["points"] for d in self.decoupes]
            cov = G.aire_balayage([contours[0], self.contour], exclus) / self.aire_utile if self.aire_utile else 0.0
            for n in couches:
                self.remplissages.append({"nom": nom, "couche": self.nom_couche[n], "reseau": res,
                                          "contour": contours[0], "trous": contours[1:],
                                          "couverture": _r(min(1.0, max(0.0, cov)))})
                self.extremites.append((n, res, None, contours[0]))

    def _couches_inutilisees(self):
        """``remove_unused_layers`` : une couche interne n'est gardée que si une piste, un via ou une zone du même
        réseau y touche la pastille (couches extérieures gardées avec ``keep_end_layers``)."""
        for c in self.composants:
            for p in c["pastilles"]:
                if not p.pop("_inutilisees"):
                    p.pop("_garder_bouts")
                    continue
                garder_bouts = p.pop("_garder_bouts")
                centre = G.local_vers_monde([p["x"], p["y"]], c)
                rayon = max(p["largeur"], p["hauteur"]) / 2.0
                gardees = []
                for n in self.cuivre:
                    nn = self.nom_couche[n]
                    if nn not in p["couches"]:
                        continue
                    if garder_bouts and n in ("F.Cu", "B.Cu"):
                        gardees.append(nn)
                        continue
                    for ext in self.extremites:
                        if ext[0] != n or not p["reseau"] or ext[1] != p["reseau"]:
                            continue
                        if (ext[2] is not None and math.hypot(ext[2][0] - centre[0], ext[2][1] - centre[1]) <= rayon) \
                                or (ext[2] is None and G.point_dans_polygone(centre, ext[3])):
                            gardees.append(nn)
                            break
                p["couches"] = gardees

    def _regions_et_boites(self):
        for c in self.composants:
            pied, fp = c.pop("_pied"), c.pop("_fp")
            c.pop("_chemin")
            board_only = c.pop("_board_only")
            contours = [(p, G.contour_pastille(p)) for p in c["pastilles"]]
            boite = [q for _p, poly in contours for q in poly]
            cuivre = any(p["couches"] for p in c["pastilles"])
            for g in fp[1:]:
                if not isinstance(g, list) or not g or g[0] not in FORMES_FP + ("fp_text",):
                    continue
                couche = (un(g, "layer") or [None, ""])[1]
                quoi = f"{g[0]} de {c['designateur']}"
                if g[0] == "fp_text":
                    if couche in self.rang:
                        polys = [self._rectangle_texte(g, quoi, lambda q: tuple(
                            G.monde_vers_local(self.monde(q), c)), pied)]
                    else:
                        continue
                elif couche in self.rang or couche in COUCHES_BOITE:
                    polys = self._polygones_forme(g, quoi, pied.conv)
                else:
                    continue
                polys = [[_rp(q) for q in poly] for poly in polys if len(poly) >= 3]
                boite += [q for poly in polys for q in poly]
                if couche not in self.rang:
                    continue
                cuivre = True
                nc = self.nom_couche[couche]
                for poly in polys:
                    touche = [p for p, cp in contours if nc in p["couches"] and G.polygones_se_touchent(poly, cp)]
                    nets = sorted({p["reseau"] for p in touche if p["reseau"]})
                    liaison = nets if len(nets) >= 2 else None
                    reseau = nets[0] if len(nets) == 1 else ""
                    pastille = next((p["id"] for p in touche if p["reseau"] == reseau), None) if not liaison else None
                    cote = "B.Cu" if pied.dessous else "F.Cu"
                    c["regions"].append({"id": f"{c['designateur']}/R{len(c['regions'])}", "couches": [nc],
                                         "face": "composant" if couche == cote else "opposee", "pastille": pastille,
                                         "reseau": reseau, "liaison": liaison, "points": G.orienter(poly),
                                         "classe_isolement": self.indice_classe(reseau)})
            b = G.boite(boite)
            c["boite"] = [_r(v) for v in b] if b else [0.0, 0.0, 0.0, 0.0]
            if board_only and not cuivre and c["genre"] == "standard":
                c["genre"] = "graphique"

    # ----------------------------------------------------------------------------------------------------------
    def _resultat(self):
        couches = []
        for n in self.cuivre:
            ep = self.epaisseur.get(n)
            couches.append({"nom": self.nom_couche[n], "nom_fichier": n, "rang": self.rang[n], "type": "signal",
                            "plans": [], "retrait_mm": 0.0, "epaisseur_cuivre_mm": None if ep is None else _r(ep),
                            "pistes_libres": self.pistes_libres[n], "type_kicad": self.type_kicad.get(n),
                            "routage_permis": None})
        regles = self._regles()
        return {
            "format": "entree_carte/2",
            "source": {"fichier": self.nom, "type": "kicad_pcb", "sha256": self.sha256,
                       "version_fichier": str(self.version), "origine_fichier_mil": None,
                       "origine_fichier_mm": [_r(self.x0), _r(self.y0)], "origine_cao_mil": None},
            "unite": "mm",
            "carte": {
                "contour": self.contour,
                "boite": self.boite,
                "aire_reference_mm2": None,
                "decoupes": self.decoupes,
                "trous": [],
                "couches": couches,
                "remplissages": self.remplissages,
                "zones_interdites": self.zones_interdites,
                "obstacles_cuivre": self.obstacles,
                "cuivre_existant": self.cuivre_existant,
                "couches_cuivre": len(couches),
                "noms_couches_cuivre": [c["nom"] for c in couches],
            },
            "composants": self.composants,
            "pastilles_libres": [],
            "reseaux": list(self.reseaux),
            "classes": self.classes,
            "classes_composants": {},
            "paires": self._paires(),
            "regles_fichier": self._regles_fichier(),
            "regles": regles,
            "avertissements": list(self.avertissements),
        }
