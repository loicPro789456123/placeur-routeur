"""Importeur Altium ``.PcbDoc`` vers ``entree_carte/2`` (contrat §2.2), sur l'API ``format_altium`` (jalon B1).

- ``importer(chemin) -> dict`` : entrée fidèle de la carte ; ``ErreurEntree`` (code 2) si le fichier est refusé.
- ``controler_refus(doc, nom)`` : refus de R-P9 (§3.5), faits avant tout accesseur de géométrie : panneau,
  rigide-flexible, pièce sur couche interne, désignateurs en double.
- ``origine_fichier_mil(doc)`` : position (mil, repère du fichier) de l'origine du repère commun = coin bas-gauche
  de la boîte du contour discrétisé.

Repère commun (§0.4) : mm, X = (x − x0)·0,0254, Y = (y − y0)·0,0254 ; repère local d'une pièce :
local = M(face)·R(−rotation)·(monde − origine). Aucune décision n'est prise ici (rôles, pièces fixes, plans) ; les
règles sont interprétées par ``portees.py`` (§3.7). Aucun chemin n'est recopié (FILENAME, SOURCEFOOTPRINTLIBRARY,
DOCUMENTPATH ne sont jamais lus) ; aucune variable d'environnement n'est lue.

Lectures choisies là où le contrat est muet (§8.0-2 c, journal du lot B, D-B60 à D-B66) :
  - forme principale d'une pastille = couche de sa face (CMS : sa couche ; traversante : TopLayer pour une pièce
    dessus ou libre, BottomLayer pour une pièce dessous) ; forme Altium inconnue -> rect (par excès) ;
  - keep-out sans bit de restriction -> effets pistes, vias, remplissages, pastilles ;
  - texte de cuivre -> rectangle hauteur × (hauteur · nombre de caractères) depuis son point d'ancrage (par excès) ;
  - canal : instance = première classe d'exemplaire (ordre alphabétique ; classe où aucun désignateur source
    répété n'apparaît deux fois) qui contient la pièce et aucune autre pièce du même groupe, sinon le suffixe du
    désignateur après le désignateur source, sinon le désignateur ; une pièce au désignateur source unique qui
    finit par le nom de sa seule classe d'exemplaire prend le groupe « désignateur moins ce suffixe » ;
  - boîte d'une pièce : pastilles, cuivre, corps 3D, sérigraphie (pistes, arcs, remplissages, régions ; textes
    exclus) et couches mécaniques de type Courtyard.
"""
from __future__ import annotations

import collections
import hashlib
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(ICI), "format_altium"))
sys.path.insert(0, ICI)

from altium_fichier import PcbDoc, pose  # noqa: E402
from altium_fichier.pcbdoc import AUCUN, ErreurPcbDoc, effets_keepout  # noqa: E402

import import_geometrie as G  # noqa: E402
import portees as P  # noqa: E402
import regions_empreintes as RE  # noqa: E402
from import_geometrie import ErreurEntree  # noqa: E402

MM = 0.0254
TOL_ARC_MIL = 0.01                 # §2.2 : extrémités d'un arc du contour à 0,01 mil près
EFFETS_TOUS = ["pistes", "vias", "remplissages", "pastilles"]
SERIGRAPHIE = ("TOPOVERLAY", "BOTTOMOVERLAY")


def _r(v):
    return round(float(v), 9)


def _rp(p):
    return [_r(p[0]), _r(p[1])]


def importer(chemin):
    return _Importeur(chemin).importer()


# =====================================================================================================================
# Refus (R-P9) et contour
# =====================================================================================================================
def controler_refus(doc, nom):
    """Refus de R-P9 (code 2) avant tout accesseur de géométrie (pastilles, pistes, régions...)."""
    imb = doc.cartes_imbriquees()
    if imb:
        docs = sorted({c["document"] for c in imb if c["document"]}) or ["la carte d'origine"]
        raise ErreurEntree(f"{nom} : panneau de cartes imbriquées : importer {', '.join(docs)}")
    if any(s["flex"] for s in doc.sous_empilages()) or doc.empilage_maitre()["flex"] or len(doc.regions_carte()) > 1:
        raise ErreurEntree(f"{nom} : carte rigide-flexible non prise en charge")
    comps = doc.data("Components6")
    D = doc.designateurs()
    for i, e in enumerate(comps):
        face = (e.props.get("LAYER") if e.props is not None else None) or "TOP"
        if face not in ("TOP", "BOTTOM"):
            raise ErreurEntree(f"{nom} : pièce {D[i]} sur une couche interne ({face}) : non prise en charge")
    doubles = sorted(d for d, n in collections.Counter(D.values()).items() if n > 1)
    if doubles:
        raise ErreurEntree(f"{nom} : désignateurs en double : {', '.join(doubles[:10])}")


def _direction_arc(s, a, b, tol):
    """(angle de départ, balayage) en degrés de l'arc du sommet ``s`` parcouru de ``a`` vers ``b`` (mil), choisi
    par les extrémités ; None si aucun sens ne fait coïncider les extrémités à ``tol`` près."""
    cx, cy, r = s["cx"], s["cy"], s["r"]
    pa = (cx + r * math.cos(math.radians(s["sa"])), cy + r * math.sin(math.radians(s["sa"])))
    pe = (cx + r * math.cos(math.radians(s["ea"])), cy + r * math.sin(math.radians(s["ea"])))
    bal = (s["ea"] - s["sa"]) % 360.0 or 360.0
    d = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1])  # noqa: E731
    if d(pa, a) <= tol and d(pe, b) <= tol:
        return s["sa"], bal
    if d(pe, a) <= tol and d(pa, b) <= tol:
        return s["ea"], -bal
    return None


def discretiser_sommets(sommets, quoi=None, nom="", avertir=None):
    """Points (mm, repère du fichier) d'un contour à sommets Altium (``KIND`` 1 = arc du sommet i vers i+1).
    ``quoi`` donné (contour de carte) : arc dont les extrémités ne coïncident pas -> ErreurEntree ; sinon corde
    et avertissement."""
    pts = [(s["x"], s["y"]) for s in sommets]
    if len(pts) >= 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= TOL_ARC_MIL:
        sommets, pts = sommets[:-1], pts[:-1]
    n = len(pts)
    out = []
    for i in range(n):
        s, a, b = sommets[i], pts[i], pts[(i + 1) % n]
        out.append([a[0] * MM, a[1] * MM])
        if s.get("type") != 1 or not s.get("r"):
            continue
        sens = _direction_arc(s, a, b, TOL_ARC_MIL)
        if sens is None:
            if quoi:
                raise ErreurEntree(f"{nom} : {quoi} : arc du sommet {i} dont les extrémités ne coïncident pas avec "
                                   f"les sommets à {TOL_ARC_MIL} mil près")
            if avertir:
                avertir("arc de contour aux extrémités incohérentes remplacé par sa corde")
            continue
        a0, bal = sens
        arc = G.discretiser_arc((s["cx"] * MM, s["cy"] * MM), s["r"] * MM, math.radians(a0), math.radians(bal))
        out.extend(arc[1:-1])
    return G.nettoyer(out)


def contour_fichier_mm(doc, nom=""):
    pts = discretiser_sommets(doc.contour_carte(), "contour de la carte", nom)
    if len(pts) < 3:
        raise ErreurEntree(f"{nom} : contour de la carte absent ou dégénéré")
    return pts


def origine_fichier_mil(doc):
    """Origine du repère commun (mil, repère du fichier) : coin bas-gauche de la boîte du contour discrétisé."""
    pts = contour_fichier_mm(doc, doc.nom)
    return min(p[0] for p in pts) / MM, min(p[1] for p in pts) / MM


# =====================================================================================================================
# Importeur
# =====================================================================================================================
class _Importeur:
    def __init__(self, chemin):
        self.chemin = chemin
        self.nom = os.path.basename(chemin)
        self.avertissements = []

    def avertir(self, texte):
        if texte not in self.avertissements:
            self.avertissements.append(texte)

    # ------------------------------------------------------------------------------------------------------------
    def importer(self):
        try:
            with open(self.chemin, "rb") as f:
                self.sha256 = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            raise ErreurEntree(f"{self.nom} : lecture impossible") from None
        try:
            doc = PcbDoc.ouvrir(self.chemin)
        except ErreurPcbDoc as e:
            raise ErreurEntree(str(e)) from None
        self.doc = doc
        controler_refus(doc, self.nom)
        self._repere()
        self._couches()
        self._reseaux_et_classes()
        self._pastilles_brutes()
        self._composants()
        self._libres()
        self._cuivre_pieces()
        self._keepouts()
        self._decoupes()
        self._remplissages()
        self._regles()
        self._boites()
        return self._resultat()

    # ------------------------------------------------------------------------------------------------------------
    def _repere(self):
        pts = contour_fichier_mm(self.doc, self.nom)
        self.x0, self.y0 = min(p[0] for p in pts) / MM, min(p[1] for p in pts) / MM
        self.contour = G.orienter([_rp((x - self.x0 * MM, y - self.y0 * MM)) for x, y in pts])
        self.boite = [_r(v) for v in G.boite(self.contour)]
        self.boite[0] = self.boite[1] = 0.0

    def W(self, x, y):
        """Point du fichier (mil) -> repère commun (mm)."""
        return [_r((x - self.x0) * MM), _r((y - self.y0) * MM)]

    def Wpts(self, pts_mm_fichier):
        return [_rp((x - self.x0 * MM, y - self.y0 * MM)) for x, y in pts_mm_fichier]

    def _couches(self):
        self.cu = self.doc.couches_cuivre()
        self.nom_v6 = {c["nom_altium"]: c["nom"] for c in self.cu}
        self.rang = {c["nom"]: c["rang"] for c in self.cu}
        self.noms = [c["nom"] for c in self.cu]

    def couches_de(self, v6):
        """Couches de cuivre (noms communs) d'un objet de la couche V6 ``v6`` ; None hors cuivre."""
        if v6 == "MULTILAYER":
            return list(self.noms)
        n = self.nom_v6.get(v6)
        return [n] if n else None

    def net(self, n):
        n = (n or "").strip()
        return "" if n == "(No Net)" else n

    def _reseaux_et_classes(self):
        self.reseaux = []
        for n in self.doc.reseaux():
            n = self.net(n)
            if n and n not in self.reseaux:
                self.reseaux.append(n)
        cl = self.doc.classes()
        self.classes = {c["nom"]: [self.net(m) for m in c["membres"]] for c in cl
                        if c["type"] == 0 and not c["superclasse"]}
        self.classes_composants = {c["nom"]: list(c["membres"]) for c in cl if c["type"] == 1 and not c["superclasse"]}
        self.paires = [{"nom": p["nom"], "positif": self.net(p["positif"]), "negatif": self.net(p["negatif"])}
                       for p in self.doc.paires_differentielles()]

    # ------------------------------------------------------------------------------------------------------------
    def _pastilles_brutes(self):
        self.pads = self.doc.pastilles()
        self.pads_piece = collections.defaultdict(list)
        for p in self.pads:
            if p["composant"] != AUCUN:
                self.pads_piece[p["composant"]].append(p)

    def _composants(self):
        doc = self.doc
        props6 = [e.props for e in doc.data("Components6")]
        corps = collections.defaultdict(list)
        for b in doc.corps_3d():
            corps[b["composant"]].append(b)
        self.corps = corps
        tous = doc.composants()
        sources = collections.Counter(c["designateur_source"] for c in tous if c["designateur_source"])
        # D-B71 : classe d'exemplaire = classe de composants où aucun désignateur source répété n'apparaît deux fois
        # (écarte les classes automatiques d'Altium, « Top Side Components »…, qui mêlent les exemplaires)
        ds_de = {c["designateur"]: c["designateur_source"] for c in tous}
        instances = set()
        for k, m in self.classes_composants.items():
            vus = [ds_de[x] for x in set(m) if ds_de.get(x) and sources[ds_de[x]] > 1]
            if vus and len(vus) == len(set(vus)):
                instances.add(k)
        self.composants, self.poses = [], {}
        for c in tous:
            i, des = c["index"], c["designateur"]
            props = props6[i]
            dessous = c["face"] == "BOTTOM"
            rot = (c["rotation"] or 0.0) % 360.0
            self.poses[i] = ((c["x_mil"], c["y_mil"]), rot, dessous)
            hauteur, source = 0.0, "inconnue"
            if c["hauteur_mil"] and c["hauteur_mil"] > 0:
                hauteur, source = c["hauteur_mil"] * MM, "hauteur"
            else:
                h3 = max((b["hauteur_totale_mil"] or 0.0 for b in corps.get(i, [])), default=0.0)
                if h3 > 0:
                    hauteur, source = h3 * MM, "corps3d"
            classes = sorted(k for k, m in self.classes_composants.items() if des in m)
            canal = None
            ds = c["designateur_source"]
            if ds and sources[ds] > 1:
                freres = {d["designateur"] for d in tous if d["designateur_source"] == ds} - {des}
                propres = [k for k in classes if k in instances and not freres & set(self.classes_composants[k])]
                inst = propres[0] if propres else (des[len(ds):].lstrip("_") if des.startswith(ds) else "") or des
                canal = {"groupe": ds, "instance": inst}
            else:                                   # D-B71 : désignateur source propre à l'exemplaire (renommé)
                ks = [k for k in classes if k in instances and des.endswith(k) and len(des) > len(k)]
                if len(ks) == 1 and des[:-len(ks[0])].rstrip("_"):
                    canal = {"groupe": des[:-len(ks[0])].rstrip("_"), "instance": ks[0]}
            x, y = self.W(c["x_mil"], c["y_mil"])
            comp = {"designateur": des, "designateur_source": ds, "empreinte": c["empreinte"] or "",
                    "description": c["description"], "description_source": (props.texte("SOURCEDESCRIPTION") if props is not None else "") or "",
                    "reference": c["reference"], "valeur": c["commentaire"] or "",
                    "genre": c["genre"] or "standard", "classes_composants": classes, "canal": canal,
                    "hauteur": _r(hauteur), "source_hauteur": source, "x": x, "y": y, "rotation": _r(rot),
                    "face": "dessous" if dessous else "dessus", "verrouille": bool(c["verrouille"]),
                    "boite": [0.0, 0.0, 0.0, 0.0], "pastilles": [], "regions": [], "vias": [],
                    "zones_interdites": [], "decoupes": []}
            for p in self.pads_piece.get(i, []):
                comp["pastilles"].append(self._pastille(p, f"{des}#{p['rang']}", i))
            self.composants.append(comp)
        self.par_index = {i: self.composants[k] for k, i in enumerate(sorted(self.poses))}

    def L(self, i, x, y):
        """Point du fichier (mil) -> repère local (mm) de la pièce de rang ``i``."""
        o, rot, dessous = self.poses[i]
        lx, ly = pose.monde_vers_local((x, y), o, rot, dessous)
        return [_r(lx * MM), _r(ly * MM)]

    # ------------------------------------------------------------------------------------------------------------
    def _forme(self, f, rot_rel, centre, miroir):
        """Champs de forme d'une pastille (forme, largeur, hauteur, rayon_coin, points) ; ``centre`` en mm, repère
        de sortie ; ``rot_rel`` = rotation de la pastille dans ce repère."""
        w, h = f["taille"][0] * MM, f["taille"][1] * MM
        forme, points = f["forme"], []
        if forme == "octogone":
            c = 0.25 * min(w, h)
            hw, hh = w / 2, h / 2
            base = [(-hw + c, -hh), (hw - c, -hh), (hw, -hh + c), (hw, hh - c), (hw - c, hh), (-hw + c, hh),
                    (-hw, hh - c), (-hw, -hh + c)]
            for q in base:
                t = G.tourner(q, rot_rel)
                points.append(_rp((centre[0] + t[0], centre[1] + t[1])))
            forme, points = "polygone", G.orienter(points)
        elif forme not in ("rect", "rond", "oblong", "rect_arrondi"):
            self.avertir("forme de pastille Altium inconnue approchée par un rectangle")
            forme = "rect"
        dx, dy = f["decalage"]
        return {"forme": forme, "largeur": _r(w), "hauteur": _r(h), "rayon_coin": _r(f["rayon_coin"] * MM),
                "points": points, "decalage": [_r(-dx * MM), _r((dy if miroir else -dy) * MM)]}

    def _pastille(self, p, ident, i=None):
        """Pastille de ``entree_carte/2`` ; ``i`` = rang de la pièce (repère local) ou None (repère commun)."""
        formes = p["formes"]
        couches = [self.nom_v6[n] for n in p["couches"] if n in self.nom_v6]
        if p["traversante"] and p.get("metallise", True):
            # pastille traversante métallisée : cuivre sur toutes les couches, plans négatifs compris (le lecteur ne
            # cite que les couches de signal de la pile ; la liaison au plan se fait par la pastille, §4.2)
            couches = list(self.noms)
        dessous = self.poses[i][2] if i is not None else False
        if p["traversante"]:
            cote = "BOTTOM" if dessous else "TOP"
        else:
            cote = p["couche"]
        cle = cote if cote in formes else (next(iter(formes)) if formes else None)
        f = formes.get(cle) if cle else None
        if f is None:
            f = {"forme": "rond", "taille": (0.0, 0.0), "rayon_coin": 0.0, "decalage": (0.0, 0.0)}
        # centre du cuivre = trou + R(rotation absolue)·décalage du cuivre (repère de la pastille)
        ox, oy = f["decalage"]
        t = G.tourner((ox, oy), p["rotation"])
        cx, cy = p["x"] + t[0], p["y"] + t[1]
        if i is not None:
            o, rot_p, _d = self.poses[i]
            centre = self.L(i, cx, cy)
            rot = pose.rotation_relative(p["rotation"], rot_p, dessous)
        else:
            centre = self.W(cx, cy)
            rot = p["rotation"] % 360.0
        champs = self._forme(f, rot, centre, dessous)
        perc = p["percage"]
        trou = None
        if perc is not None:
            trou = {"diametre": _r(perc["diametre"] * MM), "forme": perc["forme"], "longueur": _r(perc["longueur"] * MM),
                    "rotation": _r(perc["rotation"] or 0.0), "decalage": champs["decalage"],
                    "metallise": bool(perc["metallise"])}
        m = p["masque"]
        exp = 0.0
        if m["mode"] == 2:
            v = m["expansion_bas"] if cote == "BOTTOM" else m["expansion_haut"]
            exp = (v or 0.0) * MM
        if p["traversante"] or len(couches) > 1:
            face = "toutes"
        else:
            bas = "BottomLayer" if (dessous if i is not None else cote == "BOTTOM") else "TopLayer"
            face = "composant" if (not couches or couches == [bas]) else "opposee"
            if i is None:
                face = "dessous" if couches == ["BottomLayer"] else ("dessus" if couches == ["TopLayer"] else "toutes")
        pile = None
        if p["mode_pile"]:
            autre = "TOP" if cote == "BOTTOM" else "BOTTOM"
            fo = formes.get(autre, f)
            internes = [formes[k] for k in formes if k not in ("TOP", "BOTTOM")]
            if internes:
                if len({(q["forme"], q["taille"]) for q in internes}) > 1:
                    self.avertir("pile de pastille : formes des couches internes différentes, enveloppe retenue")
                fi = dict(max(internes, key=lambda q: q["taille"][0] * q["taille"][1]))
                fi["taille"] = (max(q["taille"][0] for q in internes), max(q["taille"][1] for q in internes))
            else:
                fi = f
            pile = {}
            for k, fx in (("opposee", fo), ("interne", fi)):
                ch = self._forme(fx, rot, centre, dessous)
                pile[k] = {"forme": ch["forme"], "largeur": ch["largeur"], "hauteur": ch["hauteur"], "rotation": _r(rot),
                           "rayon_coin": ch["rayon_coin"], "points": ch["points"], "decalage": ch["decalage"]}
        d = {"id": ident, "nom": p["nom"], "reseau": self.net(p["reseau"]), "x": centre[0], "y": centre[1],
             "forme": champs["forme"], "largeur": champs["largeur"], "hauteur": champs["hauteur"], "rotation": _r(rot),
             "rayon_coin": champs["rayon_coin"], "points": champs["points"], "couches": couches, "face": face,
             "trou": trou, "traversante": bool(p["traversante"]), "expansion_masque": _r(exp), "pile": pile,
             "via_dans_pastille_permis": bool(p["traversante"]), "classe_isolement": 0}
        d["_masque_regle"] = m["mode"] == 1
        return d

    # ------------------------------------------------------------------------------------------------------------
    def _libres(self):
        """Pastilles libres : trous (sans réseau, percés), pastilles avec réseau, obstacles (CMS sans réseau)."""
        self.trous, self.pastilles_libres, self.obstacles = [], [], []
        for p in self.pads:
            if p["composant"] != AUCUN:
                continue
            reseau = self.net(p["reseau"])
            if not reseau and p["trou"] > 0:
                q = self._pastille(p, f"T{len(self.trous) + 1}")
                cuivre = max([max(f["taille"]) for f in p["formes"].values()] or [0.0]) * MM
                dc = cuivre if cuivre > q["trou"]["diametre"] + 1e-9 else 0.0
                self.trous.append({"id": q["id"], "x": q["x"], "y": q["y"], "rotation": q["rotation"], "trou": q["trou"],
                                   "metallise": q["trou"]["metallise"], "diametre_cuivre": _r(dc),
                                   "couches": q["couches"] if dc > 0 else [], "classe_isolement": 0,
                                   "nom": p["nom"], "_masque_regle": False})
            elif reseau:
                self.pastilles_libres.append(self._pastille(p, f"__libre_{len(self.pastilles_libres)}"))
            else:
                q = self._pastille(p, f"O{len(self.obstacles) + 1}")
                if q["couches"]:
                    self.obstacles.append({"id": q["id"], "type": "pastille", "couches": q["couches"],
                                           "points": G.contour_pastille(q), "classe_isolement": 0})

    # ------------------------------------------------------------------------------------------------------------
    def _formes_primitives(self, composant=None):
        """Formes (mil, repère du fichier) des primitives de cuivre et de sérigraphie hors polygone, hors keep-out :
        [(type, couche V6, réseau, composant, verrouille, polygone mm fichier | None, géométrie brute)]."""
        out = []
        doc = self.doc
        for t in doc.pistes():
            if t["polygone"] == AUCUN and not t["keepout"]:
                poly = G.capsule((t["x1"] * MM, t["y1"] * MM), (t["x2"] * MM, t["y2"] * MM), t["largeur"] * MM)
                out.append(("piste", t["couche"], t, poly))
        for a in doc.arcs():
            if a["polygone"] == AUCUN and not a["keepout"]:
                bal = (a["angle_fin"] - a["angle_debut"]) % 360.0 or 360.0
                poly = G.arc_epais((a["cx"] * MM, a["cy"] * MM), a["rayon"] * MM, math.radians(a["angle_debut"]),
                                   math.radians(bal), a["largeur"] * MM)
                out.append(("arc", a["couche"], a, poly))
        for f in doc.remplissages():
            if f["polygone"] == AUCUN and not f["keepout"]:
                out.append(("remplissage", f["couche"], f, self._rect_remplissage(f)))
        regions6 = list(doc.data("Regions6") or [])
        sb = doc.data("ShapeBasedRegions6")
        sb = list(sb) if sb is not None else []
        for r in doc.regions():
            if r["polygone"] != AUCUN or r["keepout"] or r["decoupe_carte"] or r["genre"] != 0 or len(r["contour"]) < 3:
                continue
            k = r["index"]
            try:
                c = RE.contour_region(regions6[k], sb[k] if k < len(sb) else None)
            except (IndexError, AttributeError, TypeError, ValueError):
                c = r["contour"]
            out.append(("region", r["couche"], r, [[x * MM, y * MM] for x, y in c]))
        for t in doc.textes():
            if (t["designateur"] or t["commentaire"]) or not t["texte"]:
                continue
            if self.couches_de(t["couche"]) is None:
                continue
            out.append(("texte", t["couche"], t, self._rect_texte(t)))
        return out

    @staticmethod
    def _rect_remplissage(f):
        cx, cy = (f["x1"] + f["x2"]) / 2, (f["y1"] + f["y2"]) / 2
        hx, hy = abs(f["x2"] - f["x1"]) / 2, abs(f["y2"] - f["y1"]) / 2
        out = []
        for u, v in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)):
            q = G.tourner((u, v), f["rotation"] or 0.0)
            out.append([(cx + q[0]) * MM, (cy + q[1]) * MM])
        return out

    @staticmethod
    def _rect_texte(t):
        h = t["hauteur"] or 0.0
        w = h * len(t["texte"])
        s = -1.0 if t["miroir"] else 1.0
        out = []
        for u, v in ((0, 0), (s * w, 0), (s * w, h), (0, h)):
            q = G.tourner((u, v), t["rotation"] or 0.0)
            out.append([(t["x"] + q[0]) * MM, (t["y"] + q[1]) * MM])
        return out

    def _local_poly(self, i, poly_mm):
        return [self.L(i, x / MM, y / MM) for x, y in poly_mm]

    def _cuivre_pieces(self):
        """Cuivre d'empreinte hors pastilles (régions des pièces), cuivre libre (existant, obstacles de texte),
        vias, boîtes (points accumulés) et pistes libres par couche."""
        self.cuivre_existant = []
        self.pistes_libres = {n: 0 for n in self.noms}
        self.points_boite = collections.defaultdict(list)
        types_mec = self.doc.types_couches_mecaniques()
        courtyard = {k for k, v in types_mec.items() if "courtyard" in (v or "").lower()}
        self.prims = self._formes_primitives()
        contours_pads = {}
        for i, comp in self.par_index.items():
            contours_pads[i] = [(q, G.contour_pastille(q)) for q in comp["pastilles"]]
            for _q, poly in contours_pads[i]:
                self.points_boite[i] += poly
        for typ, v6, prim, poly in self.prims:
            i = prim["composant"]
            couches = self.couches_de(v6)
            if i != AUCUN and i in self.poses:
                if couches is None:
                    if v6 in SERIGRAPHIE or v6 in courtyard:
                        self.points_boite[i] += self._local_poly(i, poly)
                    continue
                comp = self.par_index[i]
                lp = G.orienter(G.nettoyer(self._local_poly(i, poly)))
                if len(lp) < 3:
                    continue
                self.points_boite[i] += lp
                touche = [q for q, cp in contours_pads[i] if set(q["couches"]) & set(couches)
                          and G.polygones_se_touchent(lp, cp)]
                nets = sorted({q["reseau"] for q in touche if q["reseau"]})
                liaison = nets if len(nets) >= 2 else None
                reseau = "" if liaison else (nets[0] if nets else self.net(prim.get("reseau")))
                pastille = None if liaison else next((q["id"] for q in touche if q["reseau"] == reseau), None)
                dessous = self.poses[i][2]
                cote = "BottomLayer" if dessous else "TopLayer"
                face = "toutes" if len(couches) > 1 else ("composant" if couches == [cote] else "opposee")
                comp["regions"].append({"id": f"{comp['designateur']}/R{len(comp['regions'])}", "couches": couches,
                                        "face": face, "pastille": pastille, "reseau": reseau, "liaison": liaison,
                                        "points": lp, "classe_isolement": 0,
                                        "_type": "texte" if typ == "texte" else "region"})
                continue
            if couches is None:
                continue
            if typ in ("piste", "arc") and len(couches) == 1:
                self.pistes_libres[couches[0]] += 1
            reseau = self.net(prim.get("reseau"))
            verrou = bool(prim.get("verrouille"))
            if typ == "texte":
                self.obstacles.append({"id": f"O{len(self.obstacles) + 1}", "type": "texte", "couches": couches,
                                       "points": G.orienter(self.Wpts(poly)), "classe_isolement": 0})
            elif typ == "piste":
                self._existant("piste", reseau, couches, verrou,
                               dict(zip(("x1", "y1"), self.W(prim["x1"], prim["y1"])),
                                    **dict(zip(("x2", "y2"), self.W(prim["x2"], prim["y2"]))),
                                    largeur=_r(prim["largeur"] * MM)))
            elif typ == "arc":
                bal = (prim["angle_fin"] - prim["angle_debut"]) % 360.0 or 360.0
                pts = G.discretiser_arc((prim["cx"] * MM, prim["cy"] * MM), prim["rayon"] * MM,
                                        math.radians(prim["angle_debut"]), math.radians(bal))
                pts = self.Wpts(pts)
                for a, b in zip(pts, pts[1:]):
                    self._existant("piste", reseau, couches, verrou, {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1],
                                                                      "largeur": _r(prim["largeur"] * MM)})
            else:
                trous = [G.orienter(self.Wpts([[x * MM, y * MM] for x, y in t]), False)
                         for t in prim.get("trous") or [] if len(t) >= 3] if typ == "region" else []
                self._existant("region", reseau, couches, verrou, {"points": G.orienter(self.Wpts(poly)),
                                                                   "trous": trous})
        for i, corps in self.corps.items():
            if i in self.poses:
                for b in corps:
                    self.points_boite[i] += [self.L(i, x, y) for x, y in b["contour"]]
        for v in self.doc.vias():
            if v["keepout"]:
                continue
            reseau = self.net(v["reseau"])
            i = v["composant"]
            if i != AUCUN and i in self.poses:
                comp = self.par_index[i]
                x, y = self.L(i, v["x"], v["y"])
                comp["vias"].append({"id": f"{comp['designateur']}/V{len(comp['vias'])}", "x": x, "y": y,
                                     "diametre": _r(v["diametre"] * MM), "trou": _r(v["trou"] * MM),
                                     "reseau": reseau, "classe_isolement": 0})
                r = v["diametre"] / 2
                self.points_boite[i] += [self.L(i, v["x"] + dx, v["y"] + dy) for dx in (-r, r) for dy in (-r, r)]
                continue
            de, a = self.nom_v6.get(v["de"]), self.nom_v6.get(v["a"])
            r0, r1 = sorted((self.rang.get(de, 0), self.rang.get(a, len(self.noms) - 1)))
            x, y = self.W(v["x"], v["y"])
            self._existant("via", reseau, self.noms[r0:r1 + 1], bool(v["verrouille"]),
                           {"x": x, "y": y, "diametre": _r(v["diametre"] * MM), "trou": _r(v["trou"] * MM)})

    def _existant(self, typ, reseau, couches, verrou, geom):
        d = {"id": f"E{len(self.cuivre_existant) + 1}", "type": typ, "reseau": reseau, "couches": list(couches),
             "verrouille": verrou, "classe_isolement": 0}
        d.update(geom)
        self.cuivre_existant.append(d)

    # ------------------------------------------------------------------------------------------------------------
    def _keepouts(self):
        self.zones_interdites = []
        doc = self.doc

        def couches_k(v6):
            if v6 == "KEEPOUT":
                return ["toutes"]
            return self.couches_de(v6)

        def effets(bits):
            e = []
            for nom in effets_keepout(bits):
                nom = "pastilles" if nom.startswith("pastilles") else nom
                if nom not in e:
                    e.append(nom)
            return e or list(EFFETS_TOUS)

        def ajouter(prim, forme, pts_mm, largeur=None):
            couches = couches_k(prim["couche"])
            if not couches:
                return
            i = prim["composant"]
            local = i != AUCUN and i in self.poses
            pts = self._local_poly(i, pts_mm) if local else self.Wpts(pts_mm)
            cible = self.par_index[i]["zones_interdites"] if local else self.zones_interdites
            pre = f"{self.par_index[i]['designateur']}/K" if local else "K"
            z = {"id": f"{pre}{len(cible) + 1}", "couches": couches, "forme": forme,
                 "points": G.orienter(pts) if forme == "polygone" else pts,
                 "effets": effets(prim.get("restrictions_keepout")), "source": "keepout"}
            if largeur is not None:
                z["largeur"] = _r(largeur * MM)
            cible.append(z)
        for t in doc.pistes():
            if t["keepout"]:
                ajouter(t, "polyligne", [[t["x1"] * MM, t["y1"] * MM], [t["x2"] * MM, t["y2"] * MM]], t["largeur"])
        for a in doc.arcs():
            if a["keepout"]:
                bal = (a["angle_fin"] - a["angle_debut"]) % 360.0 or 360.0
                pts = G.discretiser_arc((a["cx"] * MM, a["cy"] * MM), a["rayon"] * MM, math.radians(a["angle_debut"]),
                                        math.radians(bal))
                ajouter(a, "polyligne", pts, a["largeur"])
        for f in doc.remplissages():
            if f["keepout"]:
                ajouter(f, "polygone", self._rect_remplissage(f))
        for r in doc.regions():
            if r["keepout"] and len(r["contour"]) >= 3:
                ajouter(r, "polygone", [[x * MM, y * MM] for x, y in r["contour"]])

    def _decoupes(self):
        self.decoupes = []
        regions6 = list(self.doc.data("Regions6") or [])
        sb = self.doc.data("ShapeBasedRegions6")
        sb = list(sb) if sb is not None else []
        self.cutouts_poly = []
        for r in self.doc.regions():
            if r["genre"] == 1 and r["polygone"] == AUCUN and r["composant"] == AUCUN and len(r["contour"]) >= 3:
                self.cutouts_poly.append((r["couche"], G.orienter(self.Wpts([[x * MM, y * MM] for x, y in r["contour"]]))))
            if not r["decoupe_carte"] or len(r["contour"]) < 3:
                continue
            k = r["index"]
            try:
                c = RE.contour_region(regions6[k], sb[k] if k < len(sb) else None)
            except (IndexError, AttributeError, TypeError, ValueError):
                c = r["contour"]
            ident = f"D{len(self.decoupes) + 1}"
            prop = None
            if r["composant"] != AUCUN and r["composant"] in self.poses:
                comp = self.par_index[r["composant"]]
                prop = comp["designateur"]
                comp["decoupes"].append(ident)
            self.decoupes.append({"id": ident, "points": G.orienter(self.Wpts([[x * MM, y * MM] for x, y in c])),
                                  "proprietaire": prop})

    def _remplissages(self):
        self.remplissages = []
        carte = self.contour
        decs = [d["points"] for d in self.decoupes]
        aire_carte = G.aire_balayage([carte], decs) or 1.0
        self.zones_split = collections.defaultdict(list)
        for q in self.doc.polygones():
            couche = self.nom_v6.get(q["couche"])
            if couche is None:
                continue
            pts = discretiser_sommets(q["contour"], avertir=self.avertir)
            if len(pts) < 3:
                continue
            contour = G.orienter(self.Wpts(pts))
            if (q["type"] or "").replace(" ", "").lower() == "splitplane":
                self.zones_split[couche].append((self.net(q["reseau"]), contour))
                continue
            trous = [p for c, p in self.cutouts_poly if self.nom_v6.get(c) == couche and G.polygone_dans_polygone(p, contour)]
            couv = G.aire_balayage([contour, carte], trous + decs) / aire_carte
            self.remplissages.append({"nom": q["nom"] or "", "couche": couche, "reseau": self.net(q["reseau"]),
                                      "contour": contour, "trous": [G.orienter(t, False) for t in trous],
                                      "couverture": _r(min(1.0, max(0.0, couv)))})

    # ------------------------------------------------------------------------------------------------------------
    def _regles(self):
        self.regles_fichier = []
        for r in self.doc.regles():
            if not r["active"] or r["binaire"] or not r["type"]:
                continue
            self.regles_fichier.append({"type": r["type"], "nom": r["nom"] or "", "priorite": int(r["priorite"] or 0),
                                        "portee1": r["portee1"] or "", "portee2": r["portee2"] or "",
                                        "valeurs": {k: (v if v is not None else "") for k, v in r["valeurs"].items()}})
        objets, cibles = [], []

        def ajouter(obj, profil):
            objets.append(profil)
            cibles.append(obj)
        for c in self.composants:
            des = c["designateur"]
            for q in c["pastilles"]:
                t = "pastille_traversante" if q["traversante"] else "pastille_cms"
                ajouter(q, {"type": t, "reseau": q["reseau"], "piece": des, "pastille": q["nom"],
                            "couches": list(self.noms) if q["traversante"] else q["couches"],
                            "masque_regle": q["_masque_regle"]})
            for g in c["regions"]:
                ajouter(g, {"type": g.pop("_type"), "reseau": g["reseau"], "piece": des, "couches": g["couches"]})
            for v in c["vias"]:
                ajouter(v, {"type": "via", "reseau": v["reseau"], "piece": des, "couches": list(self.noms)})
        for q in self.pastilles_libres:
            t = "pastille_traversante" if q["traversante"] else "pastille_cms"
            ajouter(q, {"type": t, "reseau": q["reseau"], "piece": None, "pastille": q["nom"],
                        "couches": list(self.noms) if q["traversante"] else q["couches"],
                        "masque_regle": q["_masque_regle"]})
        for t in self.trous:
            ajouter(t, {"type": "trou", "reseau": "", "piece": None, "pastille": t.pop("nom"),
                        "couches": list(self.noms), "masque_regle": t.pop("_masque_regle")})
        for o in self.obstacles:
            ajouter(o, {"type": "pastille_cms" if o["type"] == "pastille" else "texte", "reseau": "", "piece": None,
                        "couches": o["couches"]})
        for e in self.cuivre_existant:
            ajouter(e, {"type": e["type"], "reseau": e["reseau"], "piece": None, "couches": e["couches"]})
        contexte = {"reseaux": self.reseaux, "classes": self.classes, "classes_composants": self.classes_composants,
                    "paires": self.paires, "couches": self.noms,
                    "noms_couches_fichier": {c["nom"]: c["nom_fichier"] for c in self.cu},
                    "pieces": [{"designateur": c["designateur"], "empreinte": c["empreinte"], "face": c["face"]}
                               for c in self.composants],
                    "origine_mil": [self.x0, self.y0]}
        res = P.interpreter(self.regles_fichier, objets, contexte)
        for obj, o in zip(cibles, res["objets"]):
            obj["classe_isolement"] = o["classe_isolement"]
            if "via_dans_pastille_permis" in o and "via_dans_pastille_permis" in obj:
                obj["via_dans_pastille_permis"] = o["via_dans_pastille_permis"]
            if obj.pop("_masque_regle", False):
                obj["expansion_masque"] = _r(o.get("expansion_masque", 0.0))
        self.regles = res["regles"]
        self.routage_permis = {c: v["routage_permis"] for c, v in res["couches"].items()}
        for p in self.paires:
            r = res["paires"].get(p["nom"]) or {}
            p["ecart"] = r.get("ecart_mm")
            lg = r.get("largeurs")
            p["largeur"] = lg[1] if lg else None
        for a in res["avertissements"]:
            self.avertir(a)

    def _boites(self):
        for i, comp in self.par_index.items():
            b = G.boite(self.points_boite.get(i) or [])
            comp["boite"] = [_r(v) for v in b] if b else [0.0, 0.0, 0.0, 0.0]

    # ------------------------------------------------------------------------------------------------------------
    def _couches_sortie(self):
        out = []
        plans = {p["couche"]: p for p in self.doc.plans_negatifs()}
        for c in self.cu:
            nom = c["nom"]
            liste = []
            retrait = (c["retrait_mil"] or 0.0) * MM
            if c["type"] == "plan_negatif":
                pl = plans.get(nom, {})
                zones = [(self.net(r["reseau"]), G.orienter(self.Wpts(discretiser_sommets(r["contour"],
                                                                                         avertir=self.avertir))))
                         for r in pl.get("regions", []) if not r["reseau_du_plan"]]
                zones = [z for z in zones if len(z[1]) >= 3]
                principal = G.retrait_interieur(self.contour, retrait)
                liste.append({"reseau": self.net(c["reseau"]), "contour": principal,
                              "trous": [G.orienter(z, False) for _n, z in zones]})
                for n, z in zones:
                    liste.append({"reseau": n, "contour": z, "trous": []})
            ep = c["epaisseur_cuivre_mil"]
            out.append({"nom": nom, "nom_fichier": (c["nom_fichier"] or "").strip(), "rang": c["rang"],
                        "type": c["type"], "plans": liste, "retrait_mm": _r(retrait),
                        "epaisseur_cuivre_mm": _r(ep * MM) if ep else None,
                        "pistes_libres": self.pistes_libres.get(nom, 0), "type_kicad": None,
                        "routage_permis": self.routage_permis.get(nom)})
        return out

    def _resultat(self):
        couches = self._couches_sortie()
        version = (self.doc.board.texte("VERSION") or "").strip()
        ox, oy = self.doc.origine()
        aire = self.doc.aire_region_carte()
        return {
            "format": "entree_carte/2",
            "source": {"fichier": self.nom, "type": "altium_pcbdoc", "sha256": self.sha256, "version_fichier": version,
                       "origine_fichier_mil": [_r(self.x0), _r(self.y0)], "origine_fichier_mm": None,
                       "origine_cao_mil": [_r(ox), _r(oy)]},
            "unite": "mm",
            "carte": {
                "contour": self.contour,
                "boite": self.boite,
                "aire_reference_mm2": _r(aire) if aire is not None else None,
                "decoupes": self.decoupes,
                "trous": self.trous,
                "couches": couches,
                "remplissages": self.remplissages,
                "zones_interdites": self.zones_interdites,
                "obstacles_cuivre": self.obstacles,
                "cuivre_existant": self.cuivre_existant,
                "couches_cuivre": len(couches),
                "noms_couches_cuivre": [c["nom"] for c in couches],
            },
            "composants": self.composants,
            "pastilles_libres": self.pastilles_libres,
            "reseaux": list(self.reseaux),
            "classes": self.classes,
            "classes_composants": self.classes_composants,
            "paires": self.paires,
            "regles_fichier": self.regles_fichier,
            "regles": self.regles,
            "avertissements": list(self.avertissements),
        }
