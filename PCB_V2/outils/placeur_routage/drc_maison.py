# -*- coding: utf-8 -*-
"""DRC maison : contrôle indépendant du routage (§4.1 à §4.4 du contrat 2.1, sortie ``verdict/2`` de §2.8).

Ligne de commande (§5.2) :

    python drc_maison.py --donnees D --placement P --routage R [--couture V] [--mode strict|altium] --sortie S

Codes (§1.4) : 0 routage valide, 1 routage invalide (verdict écrit), 2 entrée invalide, 4 erreur interne.

API : ``verifier(donnees, placements, routage, mode="strict", couture=None) -> dict`` (``verdict/2``).

Tout se lit dans les données (``donnees_placeur/2``) : couches par rang, matrice des classes d'isolement, gardes,
vias et largeurs de chaque réseau, zones de plan, zones interdites, découpes. Objets posés par ``geom_commune.poser``
(pièces virtuelles, piles, couches miroir, régions de liaison, cuivre fixe, cuivre de trou, vias de couture C<k>,
surfaces, zones de plan). Conventions du corpus du lot E (``corpus/LISEZMOI.md``, 1 à 16) appliquées ; choix propres
au lot C (journal, étape C3) :

- ``garde_bord`` : une violation par objet neuf ; ``garde_decoupe`` et ``zone_interdite`` : une par couple (objet,
  découpe ou zone) ; ``garde_trou`` et ``trou_trou`` : une par couple (objet, paroi) ; ``dimension_via`` : une par
  via ; ``isolement`` et ``via_sous_cms`` : une par couple d'objets ;
- paire contrôlée en isolement (marge) : boîtes à au plus m = max(0,3 ; plus grand isolement de la matrice) l'une de
  l'autre sur chaque axe ;
- l'antenne ne voit pas les zones de plan (aucun contact piste–zone, convention 8) ;
- un objet sans classe (ou de réseau inconnu) prend ``classe_reference`` ;
- ``couches_permises`` null ou vide : aucune restriction de couche propre au réseau.

Aucune variable globale modifiée, aucune variable d'environnement lue, aucun nom de carte, de couche ou de réseau.
"""
import math
import os
import sys

_ICI = os.path.dirname(os.path.abspath(__file__))
if _ICI not in sys.path:
    sys.path.insert(0, _ICI)

import formats  # noqa: E402
import geom_commune as gc  # noqa: E402

OUTIL = "drc_maison " + gc.VERSION_OUTILS
LES_MODES = ("strict", "altium")
TYPES_VIOLATIONS = ("isolement", "garde_bord", "garde_decoupe", "garde_trou", "zone_interdite", "largeur",
                    "dimension_via", "via_sous_cms", "trou_trou", "antenne", "couche_non_permise", "reseau_inconnu")
AVERTISSEMENTS = ("pistes_sous_pieces_sans_piste_dessous", "angles_aigus", "paires_desequilibrees",
                  "regles_non_couvertes", "zones_plan_isolees")
MAX_DETAILS = 1000
MAX_EXEMPLES = 15
DISTANCE_SANS_PISTE_DESSOUS_MM = 0.3
TOLERANCE_PAIRE_DEFAUT_MM = 1.0
VIAS_NEUFS = (gc.VIA, gc.VIA_COUTURE)
# ordre 1 des objets (convention 1 du corpus)
ORDRE_GENRES = {gc.PASTILLE: 0, gc.REGION: 1, gc.VIA_EMPREINTE: 2, gc.CUIVRE_FIXE: 3, gc.CUIVRE_TROU: 4,
                gc.ZONE_PLAN: 5, gc.PISTE: 6, gc.VIA: 7, gc.SURFACE: 8, gc.VIA_COUTURE: 9}


# ---------------------------------------------------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------------------------------------------------
class Union:
    """Union-find sur des clés quelconques."""

    def __init__(self):
        self.parent = {}

    def trouver(self, a):
        p = self.parent
        p.setdefault(a, a)
        r = a
        while p[r] != r:
            r = p[r]
        while p[a] != r:
            p[a], a = r, p[a]
        return r

    def unir(self, a, b):
        ra, rb = self.trouver(a), self.trouver(b)
        if ra != rb:
            self.parent[rb] = ra


def borne_mil(mm):
    """Borne de largeur convertie en mil, arrondie vers le haut au millième (convention 13)."""
    return math.ceil((mm / gc.MIL) * 1000.0 - 1e-6) / 1000.0


def reseau_commun(o, q):
    """Réseau commun de deux objets du même réseau (région de liaison comprise)."""
    if o.reseau and o.reseau == q.reseau:
        return o.reseau
    if q.liaison and o.reseau in q.liaison:
        return o.reseau
    return q.reseau


def reseaux_objet(o):
    if o.liaison:
        return list(o.liaison)
    return [o.reseau] if o.reseau else []


def centre(o):
    c = o.info.get("centre")
    if c is not None:
        return c
    return gc.premier_point(o.forme)


def _est_via(o):
    return o.genre in VIAS_NEUFS or o.genre == gc.VIA_EMPREINTE or (
        o.genre == gc.CUIVRE_FIXE and o.info.get("type") == "via")


def _arrondi(v, n=6):
    return None if v is None else round(float(v), n)


# ---------------------------------------------------------------------------------------------------------------------
# Contrôle
# ---------------------------------------------------------------------------------------------------------------------
class Controle:
    """État d'un contrôle (aucune variable globale) : scène, mode, règles, résultats."""

    def __init__(self, donnees, placements, routage, mode, couture):
        if mode not in LES_MODES:
            raise gc.ErreurEntree("mode « %s » inconnu (strict ou altium)" % mode)
        self.mode = mode
        self.d = donnees
        self.sc = gc.poser(donnees, placements, routage, couture)
        regles = gc.exiger(donnees, "regles", "donnees")
        iso = gc.exiger(regles, "isolement", "donnees.regles")
        self.matrice = [[gc.nombre(x, "donnees.regles.isolement.matrice") for x in ligne]
                        for ligne in gc.liste(gc.exiger(iso, "matrice", "donnees.regles.isolement"),
                                              "donnees.regles.isolement.matrice")]
        if not self.matrice or any(len(l) != len(self.matrice) for l in self.matrice):
            raise gc.ErreurEntree("donnees.regles.isolement.matrice : matrice carrée non vide attendue")
        self.ref = gc.entier(iso.get("classe_reference", 0), "donnees.regles.isolement.classe_reference")
        if not 0 <= self.ref < len(self.matrice):
            self.ref = 0
        self.iso_max = max(max(l) for l in self.matrice)
        self.defaut = self.sc.isolement_defaut
        self.trou_trou = gc.nombre(regles.get("trou_trou_mm", 0.0) or 0.0, "donnees.regles.trou_trou_mm")
        self.garde_trou = gc.nombre(regles.get("garde_trou_mm", 0.0) or 0.0, "donnees.regles.garde_trou_mm")
        self.derog_meme_reseau = bool(regles.get("via_dans_pastille_meme_reseau", False))
        cp = regles.get("connexion_plans") or {}
        self.iso_plans = gc.nombre(cp.get("isolement", 0.0) or 0.0, "donnees.regles.connexion_plans.isolement")
        carte = gc.exiger(donnees, "carte", "donnees")
        self.garde_bord = gc.nombre(carte.get("garde_bord_mm", 0.0) or 0.0, "donnees.carte.garde_bord_mm")
        self.garde_decoupe = gc.nombre(carte.get("garde_decoupe_mm", 0.0) or 0.0, "donnees.carte.garde_decoupe_mm")
        self.contour = gc.forme_polygone(gc.points(gc.exiger(carte, "contour", "donnees.carte"),
                                                   "donnees.carte.contour"), chemin="donnees.carte.contour")
        self.violations = {t: 0 for t in TYPES_VIOLATIONS}
        self.details = []
        self.tronque = False
        self.exemples = {}
        self.derogations = 0
        self.marge = None
        self.marge_paire = ""
        self.uf = Union()
        self.touche = {}                       # indice de piste -> [extrémité a touchée, extrémité b touchée]
        self.zones_touchees = set()
        o = self.sc.objets
        self.ordre = sorted(range(len(o)), key=lambda k: (ORDRE_GENRES[o[k].genre], k))
        self.rang_ordre = {k: i for i, k in enumerate(self.ordre)}

    # -- classes et violations -------------------------------------------------------------------------------------
    def classe(self, o):
        n = len(self.matrice)
        if o.neuf:
            r = self.sc.reseaux.get(o.reseau)
            if r is not None:
                ci = r.get("classes_isolement") or {}
                v = ci.get("via" if o.genre in VIAS_NEUFS else o.genre)
                if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n:
                    return v
            return self.ref
        c = o.classe
        if isinstance(c, int) and not isinstance(c, bool) and 0 <= c < n:
            return c
        return self.ref

    def trier(self, ks):
        return sorted(ks, key=lambda k: self.rang_ordre.get(k, k))

    def violation(self, genre, ks, position, ecart=None, requis=None, ids=None):
        self.violations[genre] += 1
        objs = self.sc.objets
        ks = self.trier([k for k in ks if k is not None])
        ident = [objs[k].id for k in ks] + list(ids or ())
        reseaux = []
        for k in ks:
            for n in reseaux_objet(objs[k]):
                if n not in reseaux:
                    reseaux.append(n)
        if len(self.details) < MAX_DETAILS:
            self.details.append({"genre": genre, "reseaux": reseaux, "objets": ident,
                                 "position_mm": [_arrondi(position[0]), _arrondi(position[1])],
                                 "ecart_mm": _arrondi(ecart), "requis_mm": _arrondi(requis)})
        else:
            self.tronque = True
        ex = self.exemples.setdefault(genre, [])
        if len(ex) < MAX_EXEMPLES:
            texte = "%s : %s" % (genre, " / ".join("%s(%s)" % (objs[k].id, ",".join(reseaux_objet(objs[k])))
                                                   for k in ks) + "".join(" / " + i for i in (ids or ())))
            if ecart is not None:
                texte += " ; écart %s mm" % gc.nombre_fr(ecart, 4)
            if requis is not None:
                texte += " ; requis %s mm" % gc.nombre_fr(requis, 4)
            texte += " ; à (%s ; %s) mm" % (gc.nombre_fr(position[0], 3), gc.nombre_fr(position[1], 3))
            ex.append(texte)

    # -- contacts (§4.2) -------------------------------------------------------------------------------------------
    def _extremite_dans(self, p, q):
        """Une extrémité d'axe de la piste ``p`` dans le cuivre de ``q`` sur la couche de la piste ; marque les
        extrémités touchées (antenne)."""
        r = next(gc.rangs(p.masque), None)
        if r is None or not (q.masque >> r) & 1:
            return False
        f = q.forme_sur(r)
        res = False
        for i, (x, y) in enumerate((p.info["a"], p.info["b"])):
            if gc.distance_signee(f, x, y) <= gc.EPS_CONTACT:
                res = True
                self.touche_ext(p, i)
        return res

    def touche_ext(self, p, i):
        k = self.sc.pistes_indice.get(id(p))
        if k is not None:
            self.touche.setdefault(k, [False, False])[i] = True

    def contact(self, o, q):
        """Contact de deux objets du même réseau (§4.2) ; marque au passage les extrémités de pistes touchées."""
        tp = False
        if o.genre == gc.PISTE:
            tp = self._extremite_dans(o, q) or tp
        if q.genre == gc.PISTE:
            tp = self._extremite_dans(q, o) or tp
        if self.mode == "altium" or not (o.neuf or q.neuf) or gc.SURFACE in (o.genre, q.genre):
            e = gc.ecart_objets(o, q)
            return e is not None and e <= gc.EPS_CONTACT
        if o.genre == gc.PISTE or q.genre == gc.PISTE:
            return tp
        # au moins un via neuf, aucun piste ni surface
        if o.genre not in VIAS_NEUFS:
            o, q = q, o
        commun = o.masque & q.masque
        if not commun:
            return False
        cx, cy = o.forme.x1, o.forme.y1
        if q.genre in VIAS_NEUFS:
            return math.hypot(cx - q.forme.x1, cy - q.forme.y1) <= max(o.forme.rayon, q.forme.rayon) + gc.EPS_CONTACT
        return any(gc.distance_signee(q.forme_sur(r), cx, cy) <= gc.EPS_CONTACT for r in gc.rangs(commun))

    def contact_zone(self, z, o):
        """Contact d'une zone de plan et d'un objet de son réseau (§4.2, convention 8)."""
        r = next(gc.rangs(z.masque))
        if not (o.masque >> r) & 1:
            return False
        f = o.forme_sur(r)
        if o.genre == gc.SURFACE:
            return gc.ecart_formes(z.forme, f) <= gc.EPS_CONTACT
        if _est_via(o) or (o.genre == gc.PASTILLE and o.info.get("traversante") and o.info.get("metallise")):
            cx, cy = centre(o)
            if not gc.contient(z.forme, cx, cy):
                return False
            if self.mode == "altium":
                return True
            rayon = f.rayon if f.genre == gc.POINT else gc.rayon_circonscrit(f, cx, cy)
            return -gc.distance_signee(z.forme, cx, cy) >= rayon + self.iso_plans - gc.EPS_CONTACT
        if o.genre == gc.PASTILLE and not o.info.get("traversante"):
            if self.mode == "altium":
                return gc.ecart_formes(z.forme, f) <= gc.EPS_CONTACT
            return gc.inclus_dans_polygone(f, z.forme)
        return False

    # -- passes ----------------------------------------------------------------------------------------------------
    def paires(self):
        """Paires d'objets proches : contacts (même réseau), isolement et marge (réseaux différents)."""
        sc = self.sc
        objs = sc.objets
        m = max(0.3, self.iso_max)
        index = gc.IndexCases(1.0, m / 2.0)
        actifs = [k for k, o in enumerate(objs) if o.genre != gc.ZONE_PLAN and o.masque]
        for k in actifs:
            index.ajouter(k, objs[k].boite)
        vu = {}
        for k in actifs:
            o = objs[k]
            bo = o.boite
            for lst in index.listes(bo):
                for j in lst:
                    if j <= k or vu.get(j) == k:
                        continue
                    vu[j] = k
                    q = objs[j]
                    bq = q.boite
                    if bo[0] - m > bq[2] or bq[0] - m > bo[2] or bo[1] - m > bq[3] or bq[1] - m > bo[3]:
                        continue
                    if not (o.masque & q.masque):
                        continue
                    if gc.meme_reseau(o, q):
                        if self.contact(o, q):
                            n = reseau_commun(o, q)
                            self.uf.unir((n, k), (n, j))
                        continue
                    if not (o.neuf or q.neuf):
                        continue
                    e = gc.ecart_objets(o, q)
                    if e is None:
                        continue
                    requis = self.matrice[self.classe(o)][self.classe(q)]
                    marge = e - requis
                    if self.marge is None or marge < self.marge:
                        a, b = self.trier([k, j])
                        self.marge = marge
                        self.marge_paire = "%s(%s) / %s(%s)" % (objs[a].id, objs[a].reseau, objs[b].id,
                                                                objs[b].reseau)
                    if e < requis - gc.EPS_ISOLEMENT:
                        self.violation("isolement", [k, j], gc.milieu_proches(o.forme, q.forme), e, requis)

    def zones(self):
        sc = self.sc
        objs = sc.objets
        par_reseau = {}
        for k, o in enumerate(objs):
            if o.genre == gc.ZONE_PLAN:
                continue
            for n in reseaux_objet(o):
                par_reseau.setdefault(n, []).append(k)
        isolees = 0
        for kz, z in enumerate(objs):
            if z.genre != gc.ZONE_PLAN:
                continue
            touchee = False
            bz = z.boite
            for k in par_reseau.get(z.reseau, ()):
                o = objs[k]
                b = o.boite
                if b[0] > bz[2] or bz[0] > b[2] or b[1] > bz[3] or bz[1] > b[3]:
                    continue
                if self.contact_zone(z, o):
                    self.uf.unir((z.reseau, kz), (z.reseau, k))
                    touchee = True
            if touchee:
                self.zones_touchees.add(kz)
            else:
                isolees += 1
        return isolees

    def connectivite(self):
        """§4.3 : liaisons ouvertes, réseaux à router et complets."""
        sc = self.sc
        objs = sc.objets
        elements = {}
        for k, o in enumerate(objs):
            if o.genre == gc.ZONE_PLAN:
                if k in self.zones_touchees:
                    elements.setdefault(o.reseau, []).append(k)
                continue
            for n in reseaux_objet(o):
                elements.setdefault(n, []).append(k)
        liaisons = 0
        details = []
        a_router = complets = 0
        for n in sorted(elements):
            ks = elements[n]
            membres = [k for k in ks if objs[k].genre in (gc.PASTILLE, gc.ZONE_PLAN)]
            if len(membres) < 2:
                continue
            a_router += 1
            classes = {}
            for k in ks:
                classes.setdefault(self.uf.trouver((n, k)), []).append(k)
            comps = []
            for lst in classes.values():
                if any(objs[k].genre in (gc.PASTILLE, gc.ZONE_PLAN, gc.PISTE, gc.VIA, gc.VIA_COUTURE, gc.SURFACE)
                       for k in lst):
                    comps.append(self.trier(lst))
            if len(comps) <= 1:
                complets += 1
                continue
            comps.sort(key=lambda c: (-sum(1 for k in c if objs[k].genre == gc.ZONE_PLAN), -len(c),
                                      self.rang_ordre[c[0]]))
            for c in comps[1:]:
                liaisons += 1
                pads = [k for k in c if objs[k].genre == gc.PASTILLE]
                if pads:
                    pos = centre(objs[pads[0]])
                else:
                    pos = centre(objs[c[0]]) if objs[c[0]].genre != gc.ZONE_PLAN else gc.premier_point(
                        objs[c[0]].forme)
                details.append({"reseau": n, "nature": "pastilles" if pads else "ilot",
                                "pastilles": [objs[k].id for k in pads],
                                "noms": ["%s.%s" % (objs[k].piece, objs[k].info.get("nom", "")) for k in pads],
                                "position_mm": [_arrondi(pos[0]), _arrondi(pos[1])]})
        return liaisons, details, a_router, complets

    def antennes(self):
        objs = self.sc.objets
        for k in self.sc.pistes:
            p = objs[k]
            t = self.touche.get(k, [False, False])
            for i in (0, 1):
                if not t[i]:
                    self.violation("antenne", [k], p.info["a"] if i == 0 else p.info["b"])
                    break

    def gardes(self):
        sc = self.sc
        objs = sc.objets
        neufs = sc.pistes + sc.vias + sc.surfaces + sc.couture
        # découpes : de la carte, et des pièces posées (repère local)
        decoupes = []
        ids_pieces = set()
        for comp in self.d.get("composants") or []:
            pose = sc.poses.get(comp.get("nom"))
            for i, dc in enumerate(comp.get("decoupes") or []):
                if isinstance(dc, dict) and dc.get("points") and pose is not None:
                    pts = [pose.monde(x, y) for x, y in gc.points(dc["points"], "decoupes")]
                    ident = dc.get("id") or "%s/D%d" % (comp.get("nom"), i)
                    ids_pieces.add(ident)
                    decoupes.append((ident, gc.forme_polygone(pts, chemin="decoupes")))
        for i, dc in enumerate((self.d.get("carte") or {}).get("decoupes") or []):
            ch = "donnees.carte.decoupes[%d]" % i
            ident = dc.get("id") or "D%d" % i
            if ident in ids_pieces:
                continue
            decoupes.append((ident, gc.forme_polygone(gc.points(gc.exiger(dc, "points", ch), ch + ".points"),
                                                      chemin=ch + ".points")))
        parois_sans_cuivre = []
        for p in sc.parois:
            if p.get("objet") is not None and objs[p["objet"]].genre in (gc.VIA, gc.VIA_COUTURE, gc.VIA_EMPREINTE):
                continue
            if p.get("objet") is not None and objs[p["objet"]].genre == gc.CUIVRE_FIXE:
                continue
            if "cuivre" in p:      # trou de trous[]
                sans = (not p["metallise"]) or p["cuivre"] <= p.get("diametre", 0.0)
            else:                  # trou de pastille
                sans = not p["metallise"]
            if sans:
                parois_sans_cuivre.append(p)
        for k in neufs:
            o = objs[k]
            f = o.forme
            # garde_bord (exemption de la partie d'une piste dans une pastille de son réseau)
            e = gc.ecart_au_bord(f, self.contour)
            if e < self.garde_bord - gc.EPS_ISOLEMENT and o.genre == gc.PISTE:
                e = self._ecart_bord_exempte(k)
            if e is not None and e < self.garde_bord - gc.EPS_ISOLEMENT:
                self.violation("garde_bord", [k], gc.premier_point(f) if o.genre != gc.PISTE else
                               ((o.info["a"][0] + o.info["b"][0]) / 2.0, (o.info["a"][1] + o.info["b"][1]) / 2.0),
                               e, self.garde_bord, ids=["contour"])
            for ident, g in decoupes:
                if gc.distance_boites(o.boite, g.boite) > self.garde_decoupe:
                    continue
                e = gc.ecart_formes(f, g)
                if e < self.garde_decoupe - gc.EPS_ISOLEMENT:
                    self.violation("garde_decoupe", [k], gc.milieu_proches(f, g), e, self.garde_decoupe,
                                   ids=[ident])
            for p in parois_sans_cuivre:
                g = p["forme"]
                if gc.distance_boites(o.boite, g.boite) > self.garde_trou:
                    continue
                e = gc.ecart_formes(f, g)
                if e < self.garde_trou - gc.EPS_ISOLEMENT:
                    ids = [] if p.get("objet") is not None else [p["id"]]
                    self.violation("garde_trou", [k, p.get("objet")], gc.milieu_proches(f, g), e, self.garde_trou,
                                   ids=ids)

    def _ecart_bord_exempte(self, k):
        """Écart au bord de la partie de la piste ``k`` hors du cuivre des pastilles de son réseau (None si toute
        la piste est dans ces pastilles)."""
        objs = self.sc.objets
        p = objs[k]
        r = next(gc.rangs(p.masque), None)
        formes = []
        for q in objs:
            if q.genre == gc.PASTILLE and q.reseau == p.reseau and r is not None and (q.masque >> r) & 1:
                if gc.distance_boites(q.boite, p.boite) <= 0.0:
                    formes.append(q.forme_sur(r))
        if not formes:
            return gc.ecart_au_bord(p.forme, self.contour)
        (ax, ay), (bx, by) = p.info["a"], p.info["b"]
        e = None
        for x1, y1, x2, y2 in gc.intervalles_hors(ax, ay, bx, by, formes):
            v = gc.ecart_au_bord(gc.forme_segment(x1, y1, x2, y2, p.forme.rayon), self.contour)
            e = v if e is None or v < e else e
        return e

    def zones_interdites(self):
        sc = self.sc
        objs = sc.objets
        zones = []

        def ajouter(z, ch, pose=None, retournee=False):
            gc.objet_json(z, ch)
            effets = z.get("effets") or []
            genres = set()
            if "pistes" in effets:
                genres.add(gc.PISTE)
            if "vias" in effets:
                genres.update(VIAS_NEUFS)
            if "remplissages" in effets:
                genres.add(gc.SURFACE)
            if not genres:
                return
            pts = gc.points(gc.exiger(z, "points", ch), ch + ".points")
            if pose is not None:
                pts = [pose.monde(x, y) for x, y in pts]
            masque = sc.masque(z.get("couches") or ["toutes"], ch + ".couches", retournee)
            if z.get("forme") == "polyligne":
                w = gc.nombre(z.get("largeur", 0.0) or 0.0, ch + ".largeur")
                formes = [gc.forme_segment(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], w / 2.0)
                          for i in range(len(pts) - 1)] or [gc.forme_point(pts[0][0], pts[0][1], w / 2.0)]
            else:
                formes = [gc.forme_polygone(pts, chemin=ch + ".points")]
            zones.append((z.get("id") or ch, masque, genres, formes))

        for i, z in enumerate(self.d.get("zones_routage_interdites") or []):
            ajouter(z, "donnees.zones_routage_interdites[%d]" % i)
        for ic, comp in enumerate(self.d.get("composants") or []):
            pose = sc.poses.get(comp.get("nom"))
            if pose is None:
                continue
            for i, z in enumerate(comp.get("zones_interdites") or []):
                ajouter(z, "donnees.composants[%d].zones_interdites[%d]" % (ic, i), pose, pose.retournee)
        for ident, masque, genres, formes in zones:
            for k in sc.pistes + sc.vias + sc.surfaces + sc.couture:
                o = objs[k]
                if o.genre not in genres or not (o.masque & masque):
                    continue
                for g in formes:
                    if gc.distance_boites(o.boite, g.boite) > self.defaut:
                        continue
                    e = gc.ecart_formes(o.forme, g)
                    if e < self.defaut - gc.EPS_ISOLEMENT:
                        self.violation("zone_interdite", [k], gc.milieu_proches(o.forme, g), e, self.defaut,
                                       ids=[str(ident)])
                        break

    def pistes_et_vias(self):
        """``largeur``, ``dimension_via``, ``couche_non_permise``, ``reseau_inconnu``, ``via_sous_cms``,
        ``trou_trou``."""
        sc = self.sc
        objs = sc.objets
        roles = {sc.couches[r]: (c.get("role") or "") for r, c in enumerate(sc.donnees_couches)}
        for k in sc.pistes + sc.surfaces:
            o = objs[k]
            res = sc.reseaux.get(o.reseau)
            if o.genre == gc.PISTE:
                mil = ((o.info["a"][0] + o.info["b"][0]) / 2.0, (o.info["a"][1] + o.info["b"][1]) / 2.0)
            else:
                mil = gc.premier_point(o.forme)
            if res is None:
                self.violation("reseau_inconnu", [k], mil)
            couche = o.info["couche"]
            permises = res.get("couches_permises") if res is not None else None
            if roles.get(couche) != "routage" or (permises and couche not in permises):
                self.violation("couche_non_permise", [k], mil)
            if o.genre == gc.PISTE and res is not None:
                lw = res.get("largeurs_mm") or []
                w = round(o.info["largeur_mil"], 3)
                if len(lw) >= 1 and lw[0] is not None and w < borne_mil(lw[0]) - gc.EPS_LARGEUR_MIL:
                    self.violation("largeur", [k], mil, o.info["largeur"], lw[0])
                elif len(lw) >= 3 and lw[2] is not None and w > borne_mil(lw[2]) + gc.EPS_LARGEUR_MIL:
                    self.violation("largeur", [k], mil, o.info["largeur"], lw[2])
        defaut_via = (self.d.get("regles") or {}).get("via") or {}
        for k in sc.vias + sc.couture:
            o = objs[k]
            res = sc.reseaux.get(o.reseau)
            c = (o.forme.x1, o.forme.y1)
            if res is None:
                self.violation("reseau_inconnu", [k], c)
            v = (res.get("via") if res is not None else None) or defaut_via
            d, t = o.info["diametre"], o.info["trou_mm"]
            faute = None
            for mesure, bmin, bmax in ((d, v.get("diametre_min"), v.get("diametre_max")),
                                       (t, v.get("trou_min"), v.get("trou_max"))):
                if bmin is not None and mesure < bmin - gc.EPS_VIA:
                    faute = (mesure, bmin)
                elif bmax is not None and mesure > bmax + gc.EPS_VIA:
                    faute = (mesure, bmax)
                if faute:
                    break
            if faute:
                self.violation("dimension_via", [k], c, faute[0], faute[1])
        # via_sous_cms
        pads = [j for j, q in enumerate(objs) if q.genre == gc.PASTILLE and not q.info.get("traversante")]
        index = gc.IndexCases(1.0, 0.0)
        for j in pads:
            index.ajouter(j, objs[j].boite)
        for k in sc.vias + sc.couture:
            o = objs[k]
            cx, cy, rv = o.forme.x1, o.forme.y1, o.forme.rayon
            vus = set()
            for lst in index.listes(o.boite):
                for j in lst:
                    if j in vus:
                        continue
                    vus.add(j)
                    q = objs[j]
                    if q.info.get("via_permis") or not (q.masque & o.masque):
                        continue
                    if not any(gc.distance_signee(q.forme_sur(r), cx, cy) < rv - gc.EPS_CONTACT
                               for r in gc.rangs(q.masque & o.masque)):
                        continue
                    if self.derog_meme_reseau and o.reseau and o.reseau == q.reseau:
                        self.derogations += 1
                    else:
                        self.violation("via_sous_cms", [k, j], (cx, cy))
        # trou_trou
        parois = sc.parois
        neuves = [i for i, p in enumerate(parois) if p.get("objet") is not None
                  and objs[p["objet"]].genre in VIAS_NEUFS]
        autres = []
        for i, p in enumerate(parois):
            ob = p.get("objet")
            g = objs[ob].genre if ob is not None else None
            if g in VIAS_NEUFS:
                continue
            if g == gc.PASTILLE and not objs[ob].info.get("traversante"):
                continue
            autres.append(i)
        idx = gc.IndexCases(1.0, self.trou_trou / 2.0)
        for i in neuves + autres:
            idx.ajouter(i, parois[i]["forme"].boite)
        for a, i in enumerate(neuves):
            p = parois[i]
            vus = set()
            for lst in idx.listes(p["forme"].boite):
                for j in lst:
                    if j == i or j in vus:
                        continue
                    vus.add(j)
                    q = parois[j]
                    qo = q.get("objet")
                    if qo is not None and objs[qo].genre in VIAS_NEUFS and j < i:
                        continue      # paire de vias neufs vue une fois
                    e = gc.ecart_formes(p["forme"], q["forme"])
                    if e < self.trou_trou - gc.EPS_ISOLEMENT:
                        self.violation("trou_trou", [p["objet"], qo], gc.milieu_proches(p["forme"], q["forme"]),
                                       e, self.trou_trou, ids=[] if qo is not None else [q["id"]])

    def avertissements(self, mesures, isolees):
        sc = self.sc
        objs = sc.objets
        sous = 0
        noms = set(self.d.get("sans_piste_dessous") or [])
        if noms:
            for comp in self.d.get("composants") or []:
                nom = comp.get("nom")
                pose = sc.poses.get(nom)
                if nom not in noms or pose is None or not comp.get("lbb"):
                    continue
                x0, y0, x1, y1 = comp["lbb"]
                boite = gc.forme_polygone([pose.monde(x0, y0), pose.monde(x1, y0), pose.monde(x1, y1),
                                           pose.monde(x0, y1)])
                rang_face = sc.n - 1 if pose.dessous else 0
                pads = [q for q in objs if q.genre == gc.PASTILLE and q.piece == nom]
                for k in sc.pistes:
                    o = objs[k]
                    if not (o.masque >> rang_face) & 1 or gc.ecart_formes(o.forme, boite) > 0.0:
                        continue
                    if all((gc.ecart_objets(o, q) is None or gc.ecart_objets(o, q) > DISTANCE_SANS_PISTE_DESSOUS_MM)
                           for q in pads):
                        sous += 1
        paires = 0
        longueur = {}
        nb_vias = {}
        for k in sc.pistes:
            o = objs[k]
            longueur[o.reseau] = longueur.get(o.reseau, 0.0) + o.info["longueur"]
        for k in sc.vias:
            nb_vias[objs[k].reseau] = nb_vias.get(objs[k].reseau, 0) + 1
        for p in self.d.get("paires") or []:
            a, b = p.get("positif"), p.get("negatif")
            tol = p.get("tolerance_longueur_mm")
            tol = TOLERANCE_PAIRE_DEFAUT_MM if tol is None else tol
            if abs(longueur.get(a, 0.0) - longueur.get(b, 0.0)) > tol or nb_vias.get(a, 0) != nb_vias.get(b, 0):
                paires += 1
        nr = (self.d.get("parametres_deduction") or {}).get("regles_non_interpretees", 0)
        return {"pistes_sous_pieces_sans_piste_dessous": sous, "angles_aigus": mesures["angles_aigus"],
                "paires_desequilibrees": paires,
                "regles_non_couvertes": nr if isinstance(nr, int) and not isinstance(nr, bool) else 0,
                "zones_plan_isolees": isolees}

    def executer(self):
        sc = self.sc
        sc.pistes_indice = {id(sc.objets[k]): k for k in sc.pistes}
        self.paires()
        isolees = self.zones()
        liaisons, det_liaisons, a_router, complets = self.connectivite()
        self.antennes()
        self.gardes()
        self.zones_interdites()
        self.pistes_et_vias()
        mesures = gc.mesures_routage(sc)
        nviol = sum(self.violations.values())
        return {"format": "verdict/2", "outil": OUTIL, "mode": self.mode,
                "valide": liaisons == 0 and nviol == 0,
                "liaisons_ouvertes": liaisons, "detail_liaisons_ouvertes": det_liaisons,
                "violations": nviol, "violations_par_type": dict(self.violations),
                "detail_violations": self.details, "detail_violations_tronque": self.tronque,
                "exemples": {t: v for t, v in self.exemples.items() if v},
                "derogations": {"via_dans_pastille_meme_reseau": self.derogations},
                "avertissements": self.avertissements(mesures, isolees),
                "reseaux_a_router": a_router, "reseaux_complets": complets,
                "marge_isolement_min_mm": self.marge, "marge_isolement_min_paire": self.marge_paire,
                "mesures": mesures}


def verifier(donnees, placements, routage, mode="strict", couture=None):
    """Verdict ``verdict/2`` du routage (§4.1 à §4.4) ; ``ErreurEntree`` si une entrée est invalide."""
    return Controle(donnees, placements, routage, mode, couture).executer()


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------
OPTIONS = (
    gc.Option("--donnees", "données du moteur (donnees_placeur/2)", obligatoire=True, meta="D"),
    gc.Option("--placement", "placement (placements/2 ou liste v1)", obligatoire=True, meta="P"),
    gc.Option("--routage", "routage (routage/2 ou v1)", obligatoire=True, meta="R"),
    gc.Option("--couture", "vias de couture (vias_couture/2)", meta="V"),
    gc.Option("--mode", "règle de contact : strict (défaut) ou altium", defaut="strict", meta="M", choix=LES_MODES),
    gc.Option("--sortie", "fichier verdict/2 écrit", obligatoire=True, meta="S"),
)


def _charger(chemin, fmt):
    try:
        return formats.charger(chemin, fmt)
    except formats.ErreurFormat as e:
        raise gc.ErreurEntree("; ".join(e.erreurs))


def _principal(args):
    donnees = _charger(args["donnees"], "donnees_placeur")
    placements = _charger(args["placement"], "placements")
    routage = _charger(args["routage"], "routage")
    couture = _charger(args["couture"], "vias_couture") if args.get("couture") else None
    v = verifier(donnees, placements, routage, args["mode"], couture)
    gc.ecrire_json(args["sortie"], v)
    vt = ", ".join("%s %d" % (t, n) for t, n in v["violations_par_type"].items() if n)
    gc.afficher("%s : %s (mode %s) : %d liaison(s) ouverte(s), %d violation(s)%s ; %d réseau(x) à router, %d complet(s)"
                % (gc.nom_fichier(args["sortie"]), "valide" if v["valide"] else "invalide", v["mode"],
                   v["liaisons_ouvertes"], v["violations"], (" (%s)" % vt) if vt else "", v["reseaux_a_router"],
                   v["reseaux_complets"]))
    return gc.CODE_SUCCES if v["valide"] else gc.CODE_NEGATIF


def main(argv=None):
    """Ligne de commande (§5.2) ; rend le code de sortie (§1.4) sans jamais quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, "drc_maison.py",
                       "Contrôle indépendant du routage (verdict/2, §4 du contrat).")


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
