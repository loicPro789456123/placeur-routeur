# -*- coding: utf-8 -*-
"""Vias de couture conditionnels (lot C, §4.6-6, §8.3-5) -> ``vias_couture/2``.

    python vias_couture.py --donnees D --placement P --routage R --sortie S [--pas 2.5]

Règles (aucun nom propre à une carte) :

- une masse (``donnees.masses`` et réseaux ``masse: true``) ne reçoit de vias que si elle a au moins une zone de plan
  (``couches[].zones_plan``) **et** au moins un remplissage sur une couche extérieure (``carte.remplissages`` de la
  première ou de la dernière couche) ; sinon aucun via (audit/paquet : 236 vias sans objet sur une carte sans surface
  de masse) ;
- grille régulière de pas ``--pas`` (2,5 mm) sur la boîte de la carte ; à chaque nœud, le premier point admissible
  d'un carré de ± 0,5 mm (pas 0,25 mm, du plus proche au plus lointain) ; un nœud sans point admissible compte un
  refus, à la raison du point du nœud lui-même ;
- un point est admissible si le cuivre du via est entièrement dans un remplissage extérieur de la masse
  (``hors_remplissage``), en contact strict avec une zone de plan de la masse (centre dans la zone, à au moins
  rayon + ``connexion_plans.isolement`` de son bord, règle de ``drc_maison`` en mode strict : ``hors_zone_plan``),
  à ``garde_bord_mm`` du contour (``garde_bord``), à ``garde_decoupe_mm`` des découpes de la carte
  (``garde_decoupe``), hors des zones interdites aux vias de la carte (``zone_interdite``), à l'isolement requis par
  la matrice de tout cuivre d'un autre réseau, toutes couches (``isolement``), hors de toute pastille, même de la
  masse (``pastille``), à ``trou_trou_mm`` de toute paroi de trou et des autres vias de couture (``trou_trou``) et à
  ``garde_trou_mm`` des parois sans cuivre (``garde_trou``) ;
- dimensions : ``reseaux[].via`` de la masse (à défaut ``regles.via``) ; via traversant ;
- vérification finale : ``drc_maison.verifier`` (strict, couture comprise) ; les vias ``C<k>`` cités par une
  violation sont retirés (``retirer_fautifs``, compte ``drc_<genre>``), jusqu'à trois tours.

Décisions (§8.0-2 c) : marge de 0,001 mm ajoutée à chaque distance requise (arrondi au millième de mil des
coordonnées écrites) ; les zones interdites des empreintes et les découpes des pièces ne sont vues que par la
vérification finale ; si le verdict est tronqué (plus de 1000 détails), un via fautif non cité reste (signalé par
``drc_maison``).
"""
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import geom_commune as gc  # noqa: E402
import drc_maison  # noqa: E402
import formats  # noqa: E402

FORMAT = "vias_couture/2"
PAS_DEFAUT_MM = 2.5
DEMI_CARRE_MM = 0.5
PAS_RECHERCHE_MM = 0.25
MARGE_MM = 1e-3
TOURS_VERIFICATION = 3
_DECALAGES = sorted(((i * PAS_RECHERCHE_MM, j * PAS_RECHERCHE_MM)
                     for i in range(-2, 3) for j in range(-2, 3)), key=lambda p: (math.hypot(p[0], p[1]), p))


def _masses(donnees):
    res = []
    for n in donnees.get("masses") or []:
        if isinstance(n, str) and n and n not in res:
            res.append(n)
    for r in donnees.get("reseaux") or []:
        if isinstance(r, dict) and r.get("masse") and r.get("nom") and r["nom"] not in res:
            res.append(r["nom"])
    return res


def _dimensions(donnees, reseau):
    regle = (donnees.get("regles") or {}).get("via") or {}
    v = {}
    for r in donnees.get("reseaux") or []:
        if r.get("nom") == reseau:
            v = r.get("via") or {}
    d = v.get("diametre") or regle.get("diametre")
    t = v.get("trou") or regle.get("trou")
    if not d or not t:
        raise gc.ErreurEntree("réseau %s : dimensions de via absentes (reseaux[].via et regles.via)" % reseau)
    return float(d), float(t)


def _formes_zones_interdites(donnees):
    res = []
    for i, z in enumerate(donnees.get("zones_routage_interdites") or []):
        if "vias" not in (z.get("effets") or []):
            continue
        ch = "donnees.zones_routage_interdites[%d]" % i
        pts = gc.points(gc.exiger(z, "points", ch), ch + ".points")
        if z.get("forme") == "polyligne":
            r = float(z.get("largeur") or 0.0) / 2.0
            res.extend(gc.forme_segment(a[0], a[1], b[0], b[1], r) for a, b in zip(pts, pts[1:]))
        elif len(pts) >= 3:
            res.append(gc.forme_polygone(pts, chemin=ch + ".points"))
    return res


class _Calcul:
    def __init__(self, donnees, placements, routage, pas_mm):
        self.d = donnees
        self.ctl = drc_maison.Controle(donnees, placements, routage, "strict", None)
        self.sc = self.ctl.sc
        self.pas = float(pas_mm)
        if not self.pas > 0.0:
            raise gc.ErreurEntree("--pas : valeur strictement positive attendue")
        regles = donnees.get("regles") or {}
        self.trou_trou = float(regles.get("trou_trou_mm") or 0.0)
        self.garde_trou = float(regles.get("garde_trou_mm") or 0.0)
        self.iso_plans = self.ctl.iso_plans
        carte = donnees["carte"]
        self.contour = self.ctl.contour
        self.garde_bord = self.ctl.garde_bord
        self.garde_decoupe = self.ctl.garde_decoupe
        self.decoupes = [gc.forme_polygone(gc.points(dc["points"], "donnees.carte.decoupes"), chemin="decoupes")
                         for dc in carte.get("decoupes") or [] if dc.get("points")]
        self.interdites = _formes_zones_interdites(donnees)
        objs = self.sc.objets
        self.index = gc.IndexCases(1.0, max(0.3, self.ctl.iso_max) + 1.0)
        for k, o in enumerate(objs):
            if o.genre != gc.ZONE_PLAN and o.masque:
                self.index.ajouter(k, o.boite)
        self.parois = gc.IndexCases(1.0, max(self.trou_trou, self.garde_trou) + 1.0)
        for i, p in enumerate(self.sc.parois):
            self.parois.ajouter(i, p["forme"].boite)
        self.sans_cuivre = set()
        for i, p in enumerate(self.sc.parois):
            o = objs[p["objet"]] if p.get("objet") is not None else None
            if o is not None and o.genre in (gc.VIA, gc.VIA_COUTURE, gc.VIA_EMPREINTE, gc.CUIVRE_FIXE):
                continue
            if "cuivre" in p:
                sans = (not p["metallise"]) or p["cuivre"] <= p.get("diametre", 0.0)
            else:
                sans = not p["metallise"]
            if sans:
                self.sans_cuivre.add(i)
        self.poses = []          # trous des vias de couture posés (x, y, rayon)

    def _voisins(self, boite, index):
        vu = set()
        for lst in index.listes(boite):
            for j in lst:
                if j not in vu:
                    vu.add(j)
                    yield j

    def refus(self, x, y, reseau, rv, rt, remplissages, zones, classe_via):
        """Raison du refus du point (None si admissible)."""
        via = gc.forme_point(x, y, rv)
        if not any(-gc.distance_signee(f, x, y) >= rv + MARGE_MM for f in remplissages):
            return "hors_remplissage"
        if not any(gc.contient(z, x, y) and -gc.distance_signee(z, x, y) >= rv + self.iso_plans + MARGE_MM
                   for z in zones):
            return "hors_zone_plan"
        if gc.ecart_au_bord(via, self.contour) < self.garde_bord + MARGE_MM:
            return "garde_bord"
        for g in self.decoupes:
            if gc.ecart_formes(via, g) < self.garde_decoupe + MARGE_MM:
                return "garde_decoupe"
        for g in self.interdites:
            if gc.ecart_formes(via, g) < MARGE_MM:
                return "zone_interdite"
        objs = self.sc.objets
        m = self.ctl.iso_max + MARGE_MM
        boite = (x - rv - m, y - rv - m, x + rv + m, y + rv + m)
        for j in self._voisins(boite, self.index):
            q = objs[j]
            if gc.distance_boites(via.boite, q.boite) > m:
                continue
            if q.genre == gc.PASTILLE:
                if gc.ecart_objets(_Via(via, self.sc.tout), q) < self.ctl.matrice[classe_via][self.ctl.classe(q)] \
                        + MARGE_MM:
                    return "pastille"
                continue
            if q.reseau == reseau or (q.liaison and reseau in q.liaison):
                continue
            e = gc.ecart_objets(_Via(via, self.sc.tout), q)
            if e is not None and e < self.ctl.matrice[classe_via][self.ctl.classe(q)] + MARGE_MM:
                return "isolement"
        trou = gc.forme_point(x, y, rt)
        mt = max(self.trou_trou, self.garde_trou) + MARGE_MM
        for i in self._voisins((x - rt - mt, y - rt - mt, x + rt + mt, y + rt + mt), self.parois):
            p = self.sc.parois[i]
            if gc.ecart_formes(trou, p["forme"]) < self.trou_trou + MARGE_MM:
                return "trou_trou"
            if i in self.sans_cuivre and gc.ecart_formes(via, p["forme"]) < self.garde_trou + MARGE_MM:
                return "garde_trou"
        for (px, py, pr) in self.poses:
            if math.hypot(px - x, py - y) - pr - rt < self.trou_trou + MARGE_MM:
                return "trou_trou"
        return None

    def executer(self):
        d = self.d
        couches = self.sc.couches
        exterieures = {couches[0], couches[-1]}
        objs = self.sc.objets
        res = {"format": FORMAT, "unite": "mil", "reseaux": [], "pas_grille_mm": self.pas, "vias": [], "refus": {}}
        b = self.contour.boite
        for reseau in _masses(d):
            zones = [o.forme for o in objs if o.genre == gc.ZONE_PLAN and o.reseau == reseau]
            remplissages = []
            for i, r in enumerate((d.get("carte") or {}).get("remplissages") or []):
                if r.get("reseau") != reseau or r.get("couche") not in exterieures:
                    continue
                ch = "donnees.carte.remplissages[%d]" % i
                remplissages.append(gc.forme_polygone(
                    gc.points(gc.exiger(r, "contour", ch), ch + ".contour"),
                    [gc.points(t, ch + ".trous") for t in r.get("trous") or []], chemin=ch + ".contour"))
            if not zones or not remplissages:
                continue
            res["reseaux"].append(reseau)
            diam, trou = _dimensions(d, reseau)
            rv, rt = diam / 2.0, trou / 2.0
            classe_via = self.ctl.classe(gc.Objet("c", gc.VIA_COUTURE, reseau, self.sc.tout, gc.forme_point(0, 0, rv)))
            nx = int(math.floor((b[2] - b[0]) / self.pas))
            ny = int(math.floor((b[3] - b[1]) / self.pas))
            x0 = b[0] + ((b[2] - b[0]) - nx * self.pas) / 2.0
            y0 = b[1] + ((b[3] - b[1]) - ny * self.pas) / 2.0
            for i in range(nx + 1):
                for j in range(ny + 1):
                    cx, cy = x0 + i * self.pas, y0 + j * self.pas
                    if not gc.contient(self.contour, cx, cy):
                        continue
                    raison0 = None
                    pris = False
                    for dx, dy in _DECALAGES:
                        x, y = cx + dx, cy + dy
                        raison = self.refus(x, y, reseau, rv, rt, remplissages, zones, classe_via)
                        if raison is None:
                            xm, ym = round(x / gc.MIL, 3), round(y / gc.MIL, 3)
                            res["vias"].append({"x": xm, "y": ym, "size": round(diam / gc.MIL, 3),
                                                "hole_size": round(trou / gc.MIL, 3), "net": reseau})
                            self.poses.append((x, y, rt))
                            pris = True
                            break
                        if raison0 is None:
                            raison0 = raison
                    if not pris:
                        res["refus"][raison0] = res["refus"].get(raison0, 0) + 1
        return res


class _Via:
    """Objet minimal pour ``gc.ecart_objets`` (via traversant)."""
    __slots__ = ("masque", "forme", "formes")

    def __init__(self, forme, masque):
        self.forme = forme
        self.masque = masque
        self.formes = None

    def forme_sur(self, _r):
        return self.forme


def retirer_fautifs(couture, verdict):
    """Retire de ``couture`` (``vias_couture/2``) les vias ``C<k>`` cités par une violation de ``verdict``
    (``drc_couture``). Rend ``(couture_corrigee, {genre: n})`` ; ``couture`` n'est pas modifiée."""
    fautifs = {}
    for det in verdict.get("detail_violations") or []:
        for ident in det.get("objets") or []:
            if isinstance(ident, str) and ident.startswith("C") and ident[1:].isdigit():
                fautifs.setdefault(int(ident[1:]), det.get("genre", "autre"))
    neuf = dict(couture)
    neuf["vias"] = [v for k, v in enumerate(couture.get("vias") or []) if k not in fautifs]
    comptes = {}
    for g in fautifs.values():
        comptes[g] = comptes.get(g, 0) + 1
    return neuf, comptes


def calculer(donnees, placements, routage, pas_mm=PAS_DEFAUT_MM):
    """``vias_couture/2`` d'un couple placement + routage (§4.6-6) ; la couture rendue est vérifiée par
    ``drc_maison`` (strict) et ses vias fautifs retirés."""
    res = _Calcul(donnees, placements, routage, pas_mm).executer()
    for _ in range(TOURS_VERIFICATION):
        if not res["vias"]:
            break
        v = drc_maison.verifier(donnees, placements, routage, "strict", res)
        res, comptes = retirer_fautifs(res, v)
        if not comptes:
            break
        for g, n in comptes.items():
            cle = "drc_" + g
            res["refus"][cle] = res["refus"].get(cle, 0) + n
    return res


OPTIONS = (
    gc.Option("--donnees", "données du moteur (donnees_placeur/2)", obligatoire=True, meta="D"),
    gc.Option("--placement", "placement (placements/2 ou liste v1)", obligatoire=True, meta="P"),
    gc.Option("--routage", "routage (routage/2 ou v1)", obligatoire=True, meta="R"),
    gc.Option("--sortie", "fichier vias_couture/2 écrit", obligatoire=True, meta="S"),
    gc.Option("--pas", "pas de la grille en mm (2,5)", defaut=PAS_DEFAUT_MM, type=float, meta="MM"),
)


def _charger(chemin, fmt):
    try:
        return formats.charger(chemin, fmt)
    except formats.ErreurFormat as e:
        raise gc.ErreurEntree("; ".join(e.erreurs))


def _principal(args):
    res = calculer(_charger(args["donnees"], "donnees_placeur"), _charger(args["placement"], "placements"),
                   _charger(args["routage"], "routage"), args["pas"])
    gc.ecrire_json(args["sortie"], res)
    gc.afficher("%s : %d via(s) de couture (%s) ; refus : %s"
                % (gc.nom_fichier(args["sortie"]), len(res["vias"]), ", ".join(res["reseaux"]) or "aucune masse",
                   ", ".join("%s %d" % kv for kv in sorted(res["refus"].items())) or "aucun"))
    return gc.CODE_SUCCES


def main(argv=None):
    """Ligne de commande ; rend le code de sortie (§1.4) sans quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, "vias_couture.py",
                       "Vias de couture conditionnels (vias_couture/2, §4.6-6 du contrat).")


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
