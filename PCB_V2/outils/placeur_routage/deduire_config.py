"""Déduction de la configuration de carte (``config_carte/2``, contrat §2.4) depuis une entrée ``entree_carte/2``.

usage : python deduire_config.py E.json --sortie C.json [--contraintes K.json] [--config U.json] [--comparer R.json]

API :

- ``deduire(entree, surcharges=None) -> dict`` : ``config_carte/2``. ``surcharges`` (facultatif) :
  ``{"contraintes": contraintes/2, "config": config_carte partiel (v1 ou v2), "noms": {"contraintes": nom,
  "config": nom}}`` ; ``noms`` ne sert qu'aux messages. Une surcharge invalide (clé inconnue, pièce ou réseau
  inconnu, type) lève ``formats.ErreurFormat`` avec le chemin du champ (code 2).
- ``nature(composant) -> str`` : règle R-P1 (§3.5) avec la table ``natures.json`` ; ``classer(entree)`` rend en plus
  la liste des pièces classées par leur seul préfixe.
- ``comparer(deduit, ref) -> list[str]`` : différences avec une configuration de référence (v1 ou v2).
- ``masses(entree, contraintes=None) -> (masses, avertissements)`` : masses de §3.2 (``contraintes.roles.masse``,
  sinon masse principale déduite, en premier).
- ``couches_plans(entree, contraintes=None) -> {couche: [réseaux]}`` : couches de plan de §3.1 (R-C1 à R-C3), sans
  le calcul des zones, d'après ``analyser_couches(entree, contraintes=None)`` (rôle, règle, preuve et « plan
  probable » de chaque couche, partagé avec ``construire_donnees``).

Règles appliquées (§3.4) : R-V1 demi-pont discret (boîtier double à 5 réseaux, grille commandée directement ou à
travers une pièce à deux pastilles, pilotes commandés par un même contrôleur, nœud sans condensateur vers une masse),
R-V2 asynchrone, R-V3 interrupteur intégré, listes de condensateurs et boucle chaude selon la topologie, R-V5 pièces
sans piste dessous, R-P4 pièces à fixer suggérées, R-V4 surcharges (``contraintes.convertisseurs`` puis ``--config``,
désignées par ``reseau_commutation``), avec ``origine`` ``deduit``, ``impose`` ou ``modifie``.

- ``controler_entree(entree, nom)`` : contrôle des champs de ``entree_carte/2`` que lit la déduction (présents, du
  bon type, désignateurs uniques) ; appelé par ``deduire`` ; ``ErreurFormat`` avec le chemin du champ (code 2).

Champs ajoutés à ``config_carte/2`` (ignorés par les lecteurs, §0.5) : ``a_confirmer`` (déductions incertaines,
reprises par ``construire_donnees``), ``pieces_par_prefixe`` (pièces classées par leur seul préfixe) et
``eloignements`` (clé ``eloignement`` d'un config_carte v1 lu par ``--config``, convertie en éloignements ``dure`` de
référence [haut] selon §2.3, à fusionner par ``construire_donnees`` avec ``contraintes.eloignements``).

Codes (§1.4) : 0 succès ; 1 différence trouvée par ``--comparer`` (configuration écrite) ; 2 entrée invalide ;
4 erreur interne. Messages sans chemin de dossier. Aucune variable d'environnement n'est lue.
"""
from __future__ import annotations

import collections
import difflib
import json
import math
import os
import re
import sys
import unicodedata

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import formats  # noqa: E402
from formats import ErreurFormat  # noqa: E402
from import_geometrie import ErreurEntree, point_dans_polygone  # noqa: E402

FORMAT = "config_carte/2"
TOPOLOGIES = ("abaisseur", "elevateur", "abaisseur_elevateur", "inverseur", "autre")
COMMUTATIONS = ("synchrone", "asynchrone", "integre")
ORIGINES = ("deduit", "impose", "modifie")
CHAMPS_PIECE = ("haut", "bas", "pilote", "inductance")
CHAMPS_COND = ("cond_entree", "cond_sortie", "cond_bulk", "cond_pilote", "cond_bootstrap")
CHAMPS_RESEAU = ("reseau_entree", "reseau_sortie", "reseau_masse", "reseau_commutation")
CHAMPS_RESEAUX = ("reseaux_courts", "reseaux_sensibles")
CHAMPS = (("nom", "topologie", "commutation") + CHAMPS_PIECE + CHAMPS_COND + CHAMPS_RESEAU + CHAMPS_RESEAUX
          + ("boucle_chaude", "origine"))
DERIVES = CHAMPS_COND + CHAMPS_RESEAUX + ("boucle_chaude",)
OBLIGATOIRES_AJOUT = ("reseau_commutation", "haut", "bas", "inductance", "reseau_entree", "reseau_sortie",
                      "reseau_masse")
RACINE_CONFIG = ("format", "convertisseurs", "sans_piste_dessous", "pieces_a_fixer_suggerees",
                 "surcharges_appliquees", "avertissements", "a_confirmer", "pieces_par_prefixe", "eloignements")
# Clés d'un convertisseur de référence qui ne se déduisent pas : « nom » est un libellé ; « eloignement »
# (config_carte v1) est une contrainte de placement, migrée vers contraintes.eloignements (§2.3, §3.9).
CLES_NON_DEDUITES = ("nom", "eloignement")
TEXTES = ("description", "description_source", "reference", "empreinte", "valeur")
MOTS_PLAN = ("GND", "PWR", "POWER", "PLANE", "PLAN", "VCC", "VDD", "VSS", "VEE", "MASSE", "ALIM", "SPLIT")
SURFACE_INTEGRE_MM2 = 2.0       # R-V3
VALEUR_INTEGRE_UH = 0.47        # R-V3
SURFACE_SANS_PISTE_MM2 = 4.0    # R-V5
TAILLE_COMMANDE = 6             # réseau de commande d'un pilote par un contrôleur : au plus 6 pastilles
EPS = 1e-9


# ---------------------------------------------------------------------------------------------------------------------
# Natures (R-P1)
# ---------------------------------------------------------------------------------------------------------------------

_TABLE = None


def _chemin_natures():
    """Table des natures : ressource du module (décision 5 du plan), par ``chemins.ressource`` (lot D) s'il existe."""
    try:
        import chemins  # noqa: PLC0415
        return chemins.ressource("natures.json")
    except (ImportError, AttributeError):
        return os.path.join(ICI, "natures.json")


def table_natures():
    global _TABLE
    if _TABLE is None:
        with open(_chemin_natures(), encoding="utf-8") as f:
            t = json.load(f)
        if t.get("format") != "natures/1":
            raise ValueError("natures.json : format natures/1 attendu")
        def motifs(cle, n):
            return [re.compile(m, re.I) for m in t[cle].get(n, [])]
        _TABLE = {cle: [(n, motifs(cle, n), motifs("sauf", n)) for n in t["ordre"]] for cle in ("mots", "indices")}
        _TABLE.update(coupures=[re.compile(m, re.I) for m in t["coupures"]],
                      gros=[re.compile(m, re.I) for m in t["gros_condensateurs"]],
                      prefixes={k.upper(): v for k, v in t["prefixes"].items()})
    return _TABLE


def _texte(s):
    """Texte comparé aux motifs : sans accents, en minuscules, « µ » devenu « u », « _ » devenu une espace."""
    s = unicodedata.normalize("NFKD", str(s or "")).replace("μ", "u").replace("µ", "u").replace("_", " ")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()


def _textes(c, champs=TEXTES, coupures=()):
    """Textes non vides des ``champs`` d'un composant, chacun coupé au premier motif de ``coupures``."""
    out = []
    for k in champs:
        t = _texte(c.get(k))
        for m in coupures:
            t = m.sub("", t, count=1).strip()
        if t:
            out.append(t)
    return out


# Passes de R-P1 (décision D-B41) : mots des descriptions, mots des autres textes, puis indices dans tous les textes.
PASSES_NATURE = (("mots", ("description", "description_source")), ("mots", ("reference", "empreinte", "valeur")),
                 ("indices", TEXTES))


def _nature_par_mots(c, table):
    for cle, champs in PASSES_NATURE:
        textes = _textes(c, champs, table["coupures"])
        for n, motifs, sauf in table[cle]:
            if any(m.search(t) and not any(s.search(t) for s in sauf) for m in motifs for t in textes):
                return n
    return None


def _trou_mecanique(p):
    t = p.get("trou")
    return bool(p.get("traversante")) and t is not None and (
        (t.get("diametre") or 0) >= 2.0 - EPS or not t.get("metallise", True))


def nature_et_source(c):
    """(nature, source) d'un composant de ``entree_carte/2`` ; source ∈ genre, mots, geometrie, prefixe, defaut."""
    genre = c.get("genre", "standard")
    if genre == "graphique":
        return "graphique", "genre"
    if genre in ("liaison_reseaux", "cavalier"):
        return "liaison", "genre"
    if genre == "mecanique":
        return "mecanique", "genre"
    table = table_natures()
    n = _nature_par_mots(c, table)
    if n:
        return n, "mots"
    pads = c.get("pastilles") or []
    if pads and all(_trou_mecanique(p) for p in pads) and len({p["reseau"] for p in pads if p.get("reseau")}) <= 1:
        return "mecanique", "geometrie"
    m = re.match(r"[A-Za-z]+", c.get("designateur") or "")
    if m and m.group(0).upper() in table["prefixes"]:
        return table["prefixes"][m.group(0).upper()], "prefixe"
    return "autre", "defaut"


def nature(composant):
    """Nature d'un composant (R-P1, §3.5)."""
    return nature_et_source(composant)[0]


# ---------------------------------------------------------------------------------------------------------------------
# Contrôle de l'entrée (§2.1 : aucun défaut appliqué par un lecteur ; §2.2)
# ---------------------------------------------------------------------------------------------------------------------

GENRES = ("standard", "mecanique", "graphique", "liaison_reseaux", "cavalier")
FORMES = ("rect", "rond", "oblong", "rect_arrondi", "polygone")


def _nombre(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


_TYPES = {"texte": (lambda v: isinstance(v, str), "texte attendu"),
          "nombre": (_nombre, "nombre attendu"),
          "booleen": (lambda v: isinstance(v, bool), "true ou false attendu"),
          "booleen_ou_null": (lambda v: v is None or isinstance(v, bool), "true, false ou null attendu"),
          "liste": (lambda v: isinstance(v, list), "liste attendue"),
          "objet": (lambda v: isinstance(v, dict), "objet attendu"),
          "point": (lambda v: isinstance(v, list) and len(v) == 2 and all(_nombre(x) for x in v), "point [x, y] attendu")}


def controler_entree(entree, nom="entree"):
    """Contrôle des champs de ``entree_carte/2`` que lit la déduction : présents et du bon type, désignateurs uniques.
    Lève ``ErreurFormat`` (« nom : chemin : message », code 2) ; ne complète rien (§2.1)."""
    def valeur(v, ch, typ):
        test, msg = _TYPES[typ]
        if not test(v):
            raise ErreurFormat(f"{nom} : {ch} : {msg}")
        return v

    def champ(o, k, ch, typ, facultatif=False):
        chk = f"{ch}.{k}" if ch else k
        if k not in o:
            if facultatif:
                return None
            raise ErreurFormat(f"{nom} : {chk} : champ manquant")
        return valeur(o[k], chk, typ)

    def objets(o, k, ch):
        L = champ(o, k, ch, "liste")
        for i, x in enumerate(L):
            valeur(x, f"{ch}.{k}[{i}]" if ch else f"{k}[{i}]", "objet")
        return L

    def pastille(p, ch):
        champ(p, "reseau", ch, "texte")
        forme = champ(p, "forme", ch, "texte")
        if forme not in FORMES:
            raise ErreurFormat(f"{nom} : {ch}.forme : {forme!r} inconnue (permis : {', '.join(FORMES)})")
        for k in ("largeur", "hauteur"):
            if champ(p, k, ch, "nombre") < 0:
                raise ErreurFormat(f"{nom} : {ch}.{k} : nombre positif ou nul attendu")
        if forme == "rect_arrondi":
            champ(p, "rayon_coin", ch, "nombre")
        if forme == "polygone":
            for i, q in enumerate(champ(p, "points", ch, "liste")):
                valeur(q, f"{ch}.points[{i}]", "point")

    racine = ""
    if not isinstance(entree, dict):
        raise ErreurFormat(f"{nom} : racine : objet JSON attendu")
    vus = set()
    for i, c in enumerate(objets(entree, "composants", racine)):
        ch = f"composants[{i}]"
        d = champ(c, "designateur", ch, "texte")
        if not d:
            raise ErreurFormat(f"{nom} : {ch}.designateur : texte non vide attendu")
        if d in vus:
            raise ErreurFormat(f"{nom} : {ch}.designateur : {d} en double (désignateur unique, §2.2)")
        vus.add(d)
        g = champ(c, "genre", ch, "texte")
        if g not in GENRES:
            raise ErreurFormat(f"{nom} : {ch}.genre : {g!r} inconnu (permis : {', '.join(GENRES)})")
        for k in ("description", "reference", "empreinte", "valeur"):
            champ(c, k, ch, "texte")
        champ(c, "description_source", ch, "texte", facultatif=True)       # champ ajouté (D-B28)
        champ(c, "x", ch, "nombre")
        champ(c, "y", ch, "nombre")
        for j, p in enumerate(objets(c, "pastilles", ch)):
            chp = f"{ch}.pastilles[{j}]"
            champ(p, "id", chp, "texte")
            champ(p, "nom", chp, "texte")
            pastille(p, chp)
            champ(p, "traversante", chp, "booleen")
            if p.get("trou") is not None or "trou" not in p:
                t = champ(p, "trou", chp, "objet")
                champ(t, "diametre", f"{chp}.trou", "nombre")
                champ(t, "metallise", f"{chp}.trou", "booleen")
    for j, p in enumerate(objets(entree, "pastilles_libres", racine)):
        champ(p, "reseau", f"pastilles_libres[{j}]", "texte")
    for i, r in enumerate(champ(entree, "reseaux", racine, "liste")):
        valeur(r, f"reseaux[{i}]", "texte")
    carte = champ(entree, "carte", racine, "objet")
    for i, q in enumerate(champ(carte, "contour", "carte", "liste")):
        valeur(q, f"carte.contour[{i}]", "point")
    for i, c in enumerate(objets(carte, "couches", "carte")):
        ch = f"carte.couches[{i}]"
        champ(c, "nom", ch, "texte")
        champ(c, "type", ch, "texte")
        for j, p in enumerate(objets(c, "plans", ch)):
            champ(p, "reseau", f"{ch}.plans[{j}]", "texte")
        champ(c, "nom_fichier", ch, "texte", facultatif=True)
        champ(c, "pistes_libres", ch, "nombre", facultatif=True)
        champ(c, "routage_permis", ch, "booleen_ou_null", facultatif=True)      # null : règle absente (§2.2)
    for i, f in enumerate(objets(carte, "remplissages", "carte")):
        ch = f"carte.remplissages[{i}]"
        champ(f, "couche", ch, "texte")
        champ(f, "reseau", ch, "texte")
        champ(f, "couverture", ch, "nombre")


def classer(entree):
    """({désignateur: nature}, [désignateurs classés par leur seul préfixe]) dans l'ordre des composants."""
    natures, prefixe = {}, []
    for c in entree.get("composants") or []:
        n, src = nature_et_source(c)
        natures[c["designateur"]] = n
        if src == "prefixe":
            prefixe.append(c["designateur"])
    return natures, prefixe


# ---------------------------------------------------------------------------------------------------------------------
# Index de la carte
# ---------------------------------------------------------------------------------------------------------------------

def aire_pastille(p):
    """Aire du cuivre d'une pastille (mm²)."""
    forme, l, h = p.get("forme"), p.get("largeur") or 0.0, p.get("hauteur") or 0.0
    if forme == "rond":
        return math.pi * l * h / 4
    if forme == "oblong":
        m = min(l, h)
        return l * h - (1 - math.pi / 4) * m * m
    if forme == "rect_arrondi":
        r = p.get("rayon_coin") or 0.0
        return l * h - (4 - math.pi) * r * r
    if forme == "polygone" and p.get("points"):
        pts = p["points"]
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2
    return l * h


class _Carte:
    """Netlist d'une entrée : réseaux de chaque pièce, pièces de chaque réseau, pastilles par réseau."""

    def __init__(self, entree, natures):
        comps = entree.get("composants") or []
        self.comps = {c["designateur"]: c for c in comps}
        self.ordre = [c["designateur"] for c in comps]
        self.rang = {d: i for i, d in enumerate(self.ordre)}
        self.nat = natures
        self.nets, self.npads, self.surface, self.sur_piece = {}, {}, {}, {}
        self.pieces = collections.defaultdict(set)
        self.taille = collections.Counter()
        for c in comps:
            d, pads = c["designateur"], c.get("pastilles") or []
            cnt = collections.Counter(p["reseau"] for p in pads if p.get("reseau"))
            self.sur_piece[d], self.nets[d], self.npads[d] = cnt, set(cnt), len(pads)
            self.surface[d] = sum(aire_pastille(p) for p in pads)
            for r, k in cnt.items():
                self.pieces[r].add(d)
                self.taille[r] += k
        for p in entree.get("pastilles_libres") or []:
            if p.get("reseau"):
                self.taille[p["reseau"]] += 1
        self.reseaux = set(entree.get("reseaux") or []) | set(self.taille)

    def tries(self, pieces):
        return sorted(pieces, key=self.rang.get)

    def gros(self, d):
        """Clé du « plus gros » condensateur : mots de natures.json, puis surface des pastilles, puis rang."""
        textes = _textes(self.comps[d])
        return (any(m.search(t) for m in table_natures()["gros"] for t in textes), self.surface[d], -self.rang[d])

    def caps_entre(self, a, masses):
        """Condensateurs à deux réseaux entre ``a`` et une des ``masses``."""
        return self.tries(d for d in self.pieces[a] if self.nat[d] == "condensateur" and len(self.nets[d]) == 2
                          and (self.nets[d] - {a}) <= masses and a not in masses)


def _mot_delimite(mot, nom):
    return re.search(r"(?:^|[_\-\s.\d])" + re.escape(mot) + r"(?:$|[_\-\s.\d])", nom, re.I) is not None


def _fr(x, n=2):
    """Nombre écrit à la française (virgule décimale) pour les preuves chiffrées."""
    return f"{x:.{n}f}".replace(".", ",")


def analyser_couches(entree, contraintes=None):
    """Rôle de chaque couche de cuivre (§3.1, R-C1 à R-C5), de haut en bas : liste de ``{"nom", "rang", "role":
    "routage"|"plan"|"inutilisee", "reseaux": [réseaux des zones de plan], "regle", "preuve", "source",
    "plan_probable": bool, "avertissement": str|None}``. Les zones elles-mêmes sont calculées par
    ``construire_donnees``. L'union des remplissages d'une couche est approchée par la somme de leurs couvertures,
    bornée à 1. Les couches inconnues de ``contraintes.couches`` sont contrôlées par ``construire_donnees``."""
    carte = entree.get("carte") or {}
    couches = carte.get("couches") or []
    cc = (contraintes or {}).get("couches") or {}
    imposes = cc.get("plans") or {}
    routage_impose = set(cc.get("routage") or [])
    inutilisees = set(cc.get("inutilisees") or [])
    remplissages = carte.get("remplissages") or []
    res = []
    for i, c in enumerate(couches):
        nom = c.get("nom")
        x = {"nom": nom, "rang": c.get("rang", i), "role": "routage", "reseaux": [], "regle": "R-C5", "preuve": "",
             "source": "deduit", "plan_probable": False, "avertissement": None}
        res.append(x)
        externe = i in (0, len(couches) - 1)
        if nom in imposes or nom in routage_impose or nom in inutilisees:
            v = imposes.get(nom)
            x.update(source="contrainte", regle="R-C1")
            if nom in imposes:
                x.update(role="plan", reseaux=[v] if isinstance(v, str) else list(v),
                         preuve="contraintes.couches.plans")
            elif nom in inutilisees:
                x.update(role="inutilisee", preuve="contraintes.couches.inutilisees")
            else:
                x.update(preuve="contraintes.couches.routage")
            continue
        if c.get("type") == "plan_negatif":
            r = sorted({p["reseau"] for p in c.get("plans") or [] if p.get("reseau")})
            x.update(regle="R-C2", source="fichier")
            if r:
                x.update(role="plan", reseaux=r, preuve=f"plan négatif du fichier ({', '.join(r)})")
            else:
                x.update(role="inutilisee", preuve="plan négatif sans réseau",
                         avertissement=f"couche {nom} : plan négatif sans réseau, couche inutilisée")
            continue
        desactivee = c.get("routage_permis") is False
        fills = [f for f in remplissages if f.get("couche") == nom and f.get("reseau")]
        couverture = min(1.0, sum(f.get("couverture") or 0.0 for f in fills))
        par_reseau = collections.Counter()
        for f in fills:
            par_reseau[f["reseau"]] += f.get("couverture") or 0.0
        zones = sorted(r for r, v in par_reseau.items() if v >= 0.05 - EPS)
        if desactivee:
            x.update(regle="R-C2c", source="fichier")
            if couverture >= 0.8 - EPS and zones:
                x.update(role="plan", reseaux=zones, preuve=f"RoutingLayers la désactive ; couverture "
                                                            f"{_fr(couverture)}")
            else:
                x.update(role="inutilisee", preuve=f"RoutingLayers la désactive ; couverture {_fr(couverture)} < 0,8")
            continue
        if externe:
            x.update(regle="R-C4", preuve="couche externe")
            continue
        pistes = c.get("pistes_libres", 0) or 0
        if couverture < 0.8 - EPS or not zones:
            x.update(preuve=f"couverture {_fr(couverture)} < 0,8" if couverture < 0.8 - EPS
                     else "aucun remplissage de 0,05 au moins")
            continue
        if pistes != 0:
            x.update(regle="R-C3", preuve=f"couverture {_fr(couverture)}, mais {pistes} piste(s) libre(s)")
            continue
        noms = [v for v in (c.get("nom_fichier"), nom) if v]
        mots = MOTS_PLAN + tuple(sorted({f["reseau"] for f in fills}))
        indice = next((f"« {m} » dans le nom" for m in mots for v in noms if _mot_delimite(m, v)), None)
        if indice is None and c.get("type_kicad") == "power":
            indice = "type KiCad power"
        if indice is None:
            x.update(regle="R-C3", plan_probable=True,
                     preuve=f"couverture {_fr(couverture)}, 0 piste libre, aucun indice d'intention")
            continue
        x.update(role="plan", reseaux=zones, regle="R-C3",
                 preuve=f"couverture {_fr(couverture)}, 0 piste libre, {indice}")
    return res


def couches_plans(entree, contraintes=None):
    """Couches de plan (§3.1) : {couche: [réseaux]} par R-C1 (``contraintes.couches``), R-C2 (plan négatif), R-C2c et
    R-C3 (couche interne couverte à 0,8 au moins par ses remplissages, sans piste libre, avec un indice d'intention ;
    zones des réseaux couvrant 0,05 au moins), d'après ``analyser_couches``."""
    return {x["nom"]: x["reseaux"] for x in analyser_couches(entree, contraintes) if x["role"] == "plan"}


# ---------------------------------------------------------------------------------------------------------------------
# Masses (§3.2 : contrainte, sinon masse principale déduite)
# ---------------------------------------------------------------------------------------------------------------------

def _masses(K, entree, contraintes, nom_k="contraintes"):
    roles = (contraintes or {}).get("roles") or {}
    if roles.get("masse"):
        v = roles["masse"]
        liste = [v] if isinstance(v, str) else list(v)
        for i, r in enumerate(liste):
            if r not in K.reseaux:
                chemin = "roles.masse" if isinstance(v, str) else f"roles.masse[{i}]"
                raise ErreurFormat(f"{nom_k} : {chemin} : réseau {r} inconnu")
        return liste, []
    plans = {r for v in couches_plans(entree, contraintes).values() for r in v}
    caps = collections.Counter()
    for d in K.ordre:
        if K.nat[d] == "condensateur" and K.npads[d] == 2 and len(K.nets[d]) == 2:
            caps.update(K.nets[d])
    cand = sorted((r for r in K.taille if K.taille[r] > 0),
                  key=lambda r: (-(r in plans), -caps[r], -K.taille[r], r))
    if not cand:
        return [], ["aucune masse : la carte n'a aucun réseau relié"]
    avert = []
    if len(cand) > 1:
        a, b = cand[0], cand[1]
        for libelle, va, vb in (("zone de plan", int(a in plans), int(b in plans)),
                                ("condensateurs", caps[a], caps[b]), ("pastilles", K.taille[a], K.taille[b])):
            if va != vb:
                if libelle != "zone de plan" and va - vb < 0.2 * va:
                    avert.append(f"masse principale {a} choisie devant {b} à moins de 20 % ({libelle} : {va} contre "
                                 f"{vb}) ; à confirmer par contraintes.roles.masse")
                break
        else:
            avert.append(f"masse principale {a} choisie devant {b} par l'ordre alphabétique (égalité) ; à confirmer "
                         f"par contraintes.roles.masse")
    return [cand[0]], avert


def masses(entree, contraintes=None):
    """(masses, avertissements) de §3.2 : ``contraintes.roles.masse``, sinon la masse principale déduite."""
    natures, _ = classer(entree)
    return _masses(_Carte(entree, natures), entree, contraintes)


# ---------------------------------------------------------------------------------------------------------------------
# Convertisseurs (R-V1, R-V2, R-V3) et listes
# ---------------------------------------------------------------------------------------------------------------------

def _commandes(K, net, exclus):
    """{circuit: réseau de sa broche} des circuits qui commandent ``net`` directement ou à travers une pièce à deux
    pastilles (résistance ou ferrite)."""
    res = {}
    for d in K.tries(K.pieces[net]):
        if K.nat[d] == "circuit" and d not in exclus:
            res.setdefault(d, net)
    for p in K.tries(K.pieces[net]):
        if K.nat[p] in ("resistance", "ferrite") and K.npads[p] == 2 and len(K.nets[p]) == 2 and p not in exclus:
            autre = next(iter(K.nets[p] - {net}))
            for d in K.tries(K.pieces[autre]):
                if K.nat[d] == "circuit" and d not in exclus:
                    res.setdefault(d, autre)
    return res


def _cle_grille(K, d, g):
    return (not _commandes(K, g, {d}), K.sur_piece[d][g], K.taille[g], g)


def _grille(K, d, restes, ms):
    """(grille, réseau de puissance) d'un interrupteur parmi ses deux réseaux hors nœud ; une masse n'est jamais une
    grille ; préférence : réseau commandé par un circuit, puis moins de pastilles sur la pièce, puis sur la carte."""
    a, b = sorted(restes)
    options = [(g, p) for g, p in ((a, b), (b, a)) if g not in ms]
    return min(options, key=lambda gp: _cle_grille(K, d, gp[0])) if options else None


def _controleurs(K, pilote, ms, exclus):
    res = set()
    for n in K.nets[pilote]:
        if n not in ms and K.taille[n] <= TAILLE_COMMANDE:
            res |= set(_commandes(K, n, exclus | {pilote}))
    return res


def _pilote(K, ga, gb, exclus, ms):
    """(pilote, pièces pilotes, réseaux de commande) des grilles ``ga`` et ``gb`` : un même circuit, sinon deux
    pilotes commandés par un même contrôleur (le pilote retenu est alors le contrôleur) ; None sinon."""
    ca, cb = _commandes(K, ga, exclus), _commandes(K, gb, exclus)
    communs = set(ca) & set(cb)
    if communs:
        p = min(communs, key=lambda d: (not (ca[d] == ga and cb[d] == gb), K.rang[d]))
        return p, {p}, {ca[p], cb[p]} - {ga, gb}
    for pa in K.tries(ca):
        for pb in K.tries(cb):
            if pa != pb:
                k = (_controleurs(K, pa, ms, exclus) & _controleurs(K, pb, ms, exclus)) - {pa, pb}
                if k:
                    p = min(k, key=K.rang.get)
                    liaisons = {n for n in K.nets[p] & (K.nets[pa] | K.nets[pb])
                                if n not in ms and K.taille[n] <= TAILLE_COMMANDE}
                    return p, {p, pa, pb}, ({ca[pa], cb[pb]} | liaisons) - {ga, gb}
    return None


def _valeur_uh(c):
    """Valeur d'une inductance en µH lue dans « valeur » (10uH, 4u7, 470nH), sinon None."""
    for k in ("valeur", "description_source", "description"):
        t = _texte(c.get(k)).replace(" ", "")
        m = re.search(r"(\d+(?:[.,]\d+)?)([pnum])h", t)
        if m:
            v, u = float(m.group(1).replace(",", ".")), m.group(2)
        else:
            m = re.fullmatch(r"(\d+)([pnum])(\d+)h?", t)
            if not m:
                continue
            v, u = float(f"{m.group(1)}.{m.group(3)}"), m.group(2)
        return v * {"p": 1e-6, "n": 1e-3, "u": 1.0, "m": 1e3}[u]
    return None


def _nom(conv):
    return f"{conv['topologie']} {conv['reseau_entree']} -> {conv['reseau_sortie']}"


def _listes(K, conv, ms, pilotes, grilles=(), commandes=(), autres=(), garder=()):
    """Listes de condensateurs, réseaux courts et sensibles, boucle chaude (§2.4, §3.4) ; les champs de ``garder``
    (donnés par une surcharge) ne sont pas recalculés."""
    m = conv["reseau_masse"]
    msc = set(ms) | ({m} if m else set())
    e, s, sw = conv["reseau_entree"], conv["reseau_sortie"], conv["reseau_commutation"]
    pil_nets = set().union(*(K.nets.get(p, set()) for p in pilotes)) if pilotes else set()
    ce = K.caps_entre(e, msc) if e else []
    bulk = [max(ce, key=K.gros)] if len(ce) > 1 else []
    ceram = [d for d in ce if d not in bulk]
    cpil = [min(ceram, key=lambda d: (K.surface[d], K.rang[d]))] if ceram and e in pil_nets else []
    cs = K.caps_entre(s, msc) if s else []
    csort = cs if conv["topologie"] == "elevateur" else ([max(cs, key=K.gros)] if cs else [])
    boots = K.tries(d for d in K.pieces[sw] if K.nat[d] == "condensateur" and len(K.nets[d]) == 2
                    and next(iter(K.nets[d] - {sw})) in pil_nets - msc - {e, s})
    r_boot = {next(iter(K.nets[d] - {sw})) for d in boots}
    exclus = {e, s, sw} | msc | set(grilles) | r_boot | set(autres)
    courts, sensibles = set(grilles) | r_boot | (set(commandes) - msc - {e, s, sw}), []
    for n in sorted(pil_nets - exclus - set(commandes)):
        (courts.add(n) if K.caps_entre(n, msc) else sensibles.append(n))
    valeurs = {"cond_entree": ceram, "cond_sortie": csort, "cond_bulk": bulk, "cond_pilote": cpil,
               "cond_bootstrap": boots, "reseaux_courts": sorted(courts), "reseaux_sensibles": sensibles}
    for k, v in valeurs.items():
        if k not in garder:
            conv[k] = v
    if "boucle_chaude" not in garder:
        cote = conv["topologie"] == "elevateur"
        conv["boucle_chaude"] = {"condensateurs": list(conv["cond_sortie"] if cote else conv["cond_entree"]),
                                 "reseau_rail": s if cote else e, "reseau_masse": m}


def _convertisseur(K, ms, *, haut, bas, pilote, pilotes, ind, sw, rail, masse, commutation, grilles=(),
                   commandes=(), topologie=None, autres=()):
    autre_l = next(iter(K.nets[ind] - {sw}))
    if topologie is None:
        alim = set().union(*(K.nets[p] for p in pilotes))
        topologie = "elevateur" if autre_l in alim and rail not in alim else "abaisseur"
    entree, sortie = (autre_l, rail) if topologie == "elevateur" else (rail, autre_l)
    conv = {"nom": "", "topologie": topologie, "commutation": commutation, "haut": haut, "bas": bas,
            "pilote": pilote, "inductance": ind, "reseau_entree": entree, "reseau_sortie": sortie,
            "reseau_masse": masse, "reseau_commutation": sw}
    _listes(K, conv, ms, pilotes, grilles, commandes, autres)
    conv["nom"] = _nom(conv)
    conv["origine"] = "deduit"
    return {k: conv[k] for k in CHAMPS}


def _convertisseurs(K, masses_):
    """(convertisseurs, a_confirmer, masses des convertisseurs) de R-V1, R-V2 et R-V3."""
    ms = set(masses_)
    convs, a_conf, utilises, noeuds = [], [], set(), set()

    def inductances(sw, integre=False):
        return [d for d in K.tries(K.pieces[sw]) if K.nat[d] == "inductance" and len(K.nets[d]) == 2
                and d not in utilises and not (K.nets[d] - {sw}) <= ms
                and (not integre or K.surface[d] >= SURFACE_INTEGRE_MM2 - EPS
                     or (_valeur_uh(K.comps[d]) or 0.0) >= VALEUR_INTEGRE_UH - EPS)]

    def noeud_libre(sw):
        return sw not in ms and sw not in noeuds and not any(
            K.nat[d] == "condensateur" and len(K.nets[d]) == 2 and (K.nets[d] - {sw}) <= ms for d in K.pieces[sw])

    def retenir(conv, pieces):
        convs.append(conv)
        utilises.update(pieces)
        noeuds.add(conv["reseau_commutation"])

    simples = [d for d in K.ordre if K.nat[d] in ("transistor", "autre") and len(K.nets[d]) == 3]
    doubles = [d for d in K.ordre if K.nat[d] == "transistor" and len(K.nets[d]) == 5]
    # R-V1 : demi-pont de deux interrupteurs
    for i, a in enumerate(simples):
        for b in simples[i + 1:]:
            if a in utilises:
                break
            if b in utilises:
                continue
            for sw in sorted(K.nets[a] & K.nets[b]):
                inds = inductances(sw) if noeud_libre(sw) else []
                ga, gb = _grille(K, a, K.nets[a] - {sw}, ms), _grille(K, b, K.nets[b] - {sw}, ms)
                if not inds or not ga or not gb or ga[1] == gb[1] or (ga[1] in ms and gb[1] in ms):
                    continue
                if gb[1] in ms or (ga[1] not in ms and (K.taille[gb[1]], ga[1]) > (K.taille[ga[1]], gb[1])):
                    (haut, (gh, rail)), (bas, (gl, masse)) = (a, ga), (b, gb)
                else:
                    (haut, (gh, rail)), (bas, (gl, masse)) = (b, gb), (a, ga)
                pil = _pilote(K, gh, gl, {a, b}, ms)
                if pil is None:
                    continue
                retenir(_convertisseur(K, ms | {masse}, haut=haut, bas=bas, pilote=pil[0], pilotes=pil[1],
                                       ind=inds[0], sw=sw, rail=rail, masse=masse, commutation="synchrone",
                                       grilles=(gh, gl), commandes=pil[2]), (a, b, inds[0]))
                break
    # R-V1 : boîtier double à 5 réseaux (nœud, rail, masse, deux grilles)
    for d in doubles:
        for sw in sorted(K.nets[d]):
            inds = inductances(sw) if noeud_libre(sw) and d not in utilises else []
            reste = K.nets[d] - {sw}
            if not inds or len(reste & ms) != 1:
                continue
            masse = next(iter(reste & ms))
            g1, g2, rail = sorted(reste - {masse}, key=lambda g: _cle_grille(K, d, g))
            pil = _pilote(K, g1, g2, {d}, ms)
            if pil is not None:
                retenir(_convertisseur(K, ms, haut=d, bas=d, pilote=pil[0], pilotes=pil[1], ind=inds[0], sw=sw,
                                       rail=rail, masse=masse, commutation="synchrone", grilles=(g1, g2),
                                       commandes=pil[2]), (d, inds[0]))
                break
    # R-V2 : asynchrone (un transistor et une diode)
    diodes = [d for d in K.ordre if K.nat[d] == "diode" and len(K.nets[d]) == 2]
    for t in simples:
        for dd in diodes:
            if t in utilises:
                break
            if dd in utilises:
                continue
            for sw in sorted(K.nets[t] & K.nets[dd]):
                inds = inductances(sw) if noeud_libre(sw) else []
                gp = _grille(K, t, K.nets[t] - {sw}, ms)
                if not inds or not gp:
                    continue
                g, p = gp
                x = next(iter(K.nets[dd] - {sw}))
                if p in ms and x not in ms:
                    haut, bas, rail, masse = dd, t, x, p
                elif x in ms and p not in ms:
                    haut, bas, rail, masse = t, dd, p, x
                else:
                    continue
                cmd = _commandes(K, g, {t})
                if not cmd:
                    continue
                pil = min(cmd, key=K.rang.get)
                retenir(_convertisseur(K, ms, haut=haut, bas=bas, pilote=pil, pilotes={pil}, ind=inds[0], sw=sw,
                                       rail=rail, masse=masse, commutation="asynchrone", grilles=(g,),
                                       commandes={cmd[pil]} - {g}), (t, dd, inds[0]))
                break
    # R-V3 : interrupteur intégré
    candidats = []
    for u in K.ordre:
        if K.nat[u] != "circuit":
            continue
        for sw in sorted(K.nets[u]):
            if not noeud_libre(sw):
                continue
            for ind in inductances(sw, integre=True):
                autre = next(iter(K.nets[ind] - {sw}))
                caps_u = [c for c in K.pieces[sw] if K.nat[c] == "condensateur" and len(K.nets[c]) == 2
                          and (K.nets[c] - {sw}) <= K.nets[u]]
                diodes_m = [c for c in K.pieces[sw] if K.nat[c] == "diode" and len(K.nets[c]) == 2
                            and (K.nets[c] - {sw}) <= ms]
                if K.caps_entre(autre, ms) and len(caps_u) <= 1 and len(diodes_m) <= 1:
                    candidats.append((u, sw, ind, autre))
                    break
    for u, sw, ind, autre in candidats:
        if ind in utilises or sw in noeuds:
            continue
        propres = {x for v, s, _, o in candidats if v == u for x in (s, o)}
        boots = {next(iter(K.nets[c] - {sw})) for c in K.pieces[sw]
                 if K.nat[c] == "condensateur" and len(K.nets[c]) == 2 and (K.nets[c] - {sw}) <= K.nets[u]}
        rails = sorted((n for n in K.nets[u] - propres - ms - boots if K.caps_entre(n, ms)),
                       key=lambda n: (-len(K.caps_entre(n, ms)), -K.taille[n], n))
        masses_u = [m for m in masses_ if m in K.nets[u]] or list(masses_)
        if not rails or not masses_u:
            continue
        conv = _convertisseur(K, ms, haut=u, bas=u, pilote=u, pilotes={u}, ind=ind, sw=sw, rail=rails[0],
                              masse=masses_u[0], commutation="integre", topologie="abaisseur",
                              autres=propres - {sw, autre})
        retenir(conv, (ind,))
        a_conf.append((sw, f"convertisseur {conv['nom']} ({sw}) : topologie supposée (abaisseur, interrupteur "
                           f"intégré à {u}) ; à préciser par contraintes.convertisseurs.modifier pour un élévateur"))
    return convs, a_conf, [c["reseau_masse"] for c in convs]


# ---------------------------------------------------------------------------------------------------------------------
# Surcharges (R-V4)
# ---------------------------------------------------------------------------------------------------------------------

def _suggestion(cle, connues):
    p = difflib.get_close_matches(cle, list(connues), n=1, cutoff=0.6)
    return f" ; vouliez-vous dire '{p[0]}' ?" if p else ""


def _liste_textes(v, chemin, nom, v1):
    if isinstance(v, str) and v1:
        return [v] if v else []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ErreurFormat(f"{nom} : {chemin} : liste de textes attendue")
    return list(v)


def _broche(K, b, ch, nom):
    """Contrôle d'un objet d'éloignement : ``PIECE``, ``PIECE.BROCHE`` ou ``PIECE#k`` (§2.3)."""
    if "#" in b:
        d = b.partition("#")[0]
        if d not in K.comps or not any(p["id"] == b for p in K.comps[d]["pastilles"]):
            raise ErreurFormat(f"{nom} : {ch} : pastille {b} inconnue")
        return
    d, _, broche = b.partition(".")
    if d not in K.comps:
        raise ErreurFormat(f"{nom} : {ch} : pièce {d} inconnue")
    if broche and not any(p["nom"] == broche for p in K.comps[d]["pastilles"]):
        raise ErreurFormat(f"{nom} : {ch} : broche {broche} absente de {d}")


def _objets_controles(v, chemin, nom, connues, obligatoires):
    if not isinstance(v, list):
        raise ErreurFormat(f"{nom} : {chemin} : liste attendue")
    for i, e in enumerate(v):
        ch = f"{chemin}[{i}]"
        if not isinstance(e, dict):
            raise ErreurFormat(f"{nom} : {ch} : objet attendu")
        for k in e:
            if not k.startswith("_") and k not in connues:
                raise ErreurFormat(f"{nom} : {ch}.{k} : clé inconnue{_suggestion(k, connues)}")
        for k in obligatoires:
            if k not in e:
                raise ErreurFormat(f"{nom} : {ch}.{k} : champ manquant")
        for k in ("distance_mm", "poids_par_mm", "marge_mm"):
            if k in e and (not _nombre(e[k]) or e[k] < 0):
                raise ErreurFormat(f"{nom} : {ch}.{k} : nombre positif ou nul attendu")
        yield ch, e


def _eloignement_v1(K, v, chemin, nom):
    """Clé ``eloignement`` d'un convertisseur de config_carte v1 : ``[{"broches", "distance_mm"}]``, contrôlée."""
    out = []
    for ch, e in _objets_controles(v, chemin, nom, ("broches", "distance_mm"), ("broches", "distance_mm")):
        broches = _liste_textes(e["broches"], f"{ch}.broches", nom, False)
        for j, b in enumerate(broches):
            _broche(K, b, f"{ch}.broches[{j}]", nom)
        out.append({"broches": broches, "distance_mm": float(e["distance_mm"])})
    return out


def _eloignements_v2(K, v, chemin, nom):
    """Champ ``eloignements`` d'un config_carte/2 relu (forme de contraintes.eloignements, §2.3), contrôlé."""
    out = []
    for ch, e in _objets_controles(v, chemin, nom, ("type", "objets", "reference", "distance_mm", "poids_par_mm",
                                                    "marge_mm"), ("type", "objets", "reference", "distance_mm")):
        if e["type"] not in ("dure", "souple"):
            raise ErreurFormat(f"{nom} : {ch}.type : {e['type']!r} inconnu (permis : dure, souple)")
        for k in ("objets", "reference"):
            for j, b in enumerate(_liste_textes(e[k], f"{ch}.{k}", nom, False)):
                _broche(K, b, f"{ch}.{k}[{j}]", nom)
        out.append({k: x for k, x in e.items() if not k.startswith("_")})
    return out


def _controler_conv(K, x, chemin, nom, v1, avert, supprimer_permis=False):
    """Convertisseur d'une surcharge, contrôlé (clés, types, pièces et réseaux existants) et lu en v2."""
    if not isinstance(x, dict):
        raise ErreurFormat(f"{nom} : {chemin} : objet attendu")
    out = {}
    for k, v in x.items():
        ch = f"{chemin}.{k}"
        if k.startswith("_"):
            continue
        if k == "eloignement" and v1:
            out["_eloignement"] = _eloignement_v1(K, v, ch, nom)
            continue
        if k == "supprimer" and supprimer_permis:
            if not isinstance(v, bool):
                raise ErreurFormat(f"{nom} : {ch} : true ou false attendu")
            out[k] = v
            continue
        if k not in CHAMPS:
            raise ErreurFormat(f"{nom} : {ch} : clé inconnue{_suggestion(k, CHAMPS)}")
        if k in CHAMPS_COND + CHAMPS_RESEAUX:
            v = _liste_textes(v, ch, nom, v1)
        elif k == "boucle_chaude":
            if not isinstance(v, dict):
                raise ErreurFormat(f"{nom} : {ch} : objet attendu")
            for kk in v:
                if not kk.startswith("_") and kk not in ("condensateurs", "reseau_rail", "reseau_masse"):
                    raise ErreurFormat(f"{nom} : {ch}.{kk} : clé inconnue")
            for kk in ("reseau_rail", "reseau_masse"):
                if not isinstance(v.get(kk), str):
                    raise ErreurFormat(f"{nom} : {ch}.{kk} : texte attendu")
            v = {"condensateurs": _liste_textes(v.get("condensateurs", []), f"{ch}.condensateurs", nom, False),
                 "reseau_rail": v["reseau_rail"], "reseau_masse": v["reseau_masse"]}
        elif not isinstance(v, str):
            raise ErreurFormat(f"{nom} : {ch} : texte attendu")
        out[k] = v
    for k, permis in (("topologie", TOPOLOGIES), ("commutation", COMMUTATIONS), ("origine", ORIGINES)):
        if k in out and out[k] not in permis:
            raise ErreurFormat(f"{nom} : {chemin}.{k} : {out[k]!r} inconnu (permis : {', '.join(permis)})")

    def piece(d, ch):
        if d not in K.comps:
            raise ErreurFormat(f"{nom} : {ch} : pièce {d} inconnue")

    def reseau(r, ch):
        if r not in K.reseaux:
            raise ErreurFormat(f"{nom} : {ch} : réseau {r} inconnu")

    for k in CHAMPS_PIECE:
        if out.get(k):
            piece(out[k], f"{chemin}.{k}")
    for k in CHAMPS_COND:
        for i, d in enumerate(out.get(k, [])):
            piece(d, f"{chemin}.{k}[{i}]")
    for k in CHAMPS_RESEAU:
        if out.get(k):
            reseau(out[k], f"{chemin}.{k}")
    for k in CHAMPS_RESEAUX:
        for i, r in enumerate(out.get(k, [])):
            reseau(r, f"{chemin}.{k}[{i}]")
    if "boucle_chaude" in out:
        b = out["boucle_chaude"]
        for i, d in enumerate(b["condensateurs"]):
            piece(d, f"{chemin}.boucle_chaude.condensateurs[{i}]")
        for kk in ("reseau_rail", "reseau_masse"):
            if b[kk]:
                reseau(b[kk], f"{chemin}.boucle_chaude.{kk}")
    return out


def _trouver(convs, x, par_paire):
    sw = x.get("reseau_commutation")
    for i, c in enumerate(convs):
        if (sw and c["reseau_commutation"] == sw) or (
                not sw and par_paire and x.get("haut") and c["haut"] == x["haut"] and c["bas"] == x.get("bas")):
            return i
    return None


class _Surcharges:
    def __init__(self, K, convs, a_conf, ms):
        self.K, self.convs, self.a_conf, self.ms = K, convs, dict(a_conf), set(ms)
        self.appliquees, self.avert, self.eloignements = [], [], []

    def _recalculer(self, conv, donnes):
        pilotes = {conv["pilote"]} if conv["pilote"] else set()
        if conv["commutation"] == "integre":
            pilotes.add(conv["haut"])
        _listes(self.K, conv, self.ms, pilotes, garder=donnes)

    def ajouter(self, x, chemin, nom):
        manque = [k for k in OBLIGATOIRES_AJOUT if not x.get(k)]
        if manque:
            raise ErreurFormat(f"{nom} : {chemin} : champ(s) obligatoire(s) pour un convertisseur ajouté : "
                               f"{', '.join(manque)}")
        if _trouver(self.convs, x, False) is not None:
            raise ErreurFormat(f"{nom} : {chemin}.reseau_commutation : convertisseur {x['reseau_commutation']} déjà "
                               f"déduit (utiliser modifier)")
        commutation = x.get("commutation", "integre" if x["haut"] == x["bas"] else "synchrone")
        # §2.3 : « integre » signifie haut = bas = pilote = le circuit ; le pilote omis est donc le circuit.
        conv = {"nom": "", "topologie": x.get("topologie", "abaisseur"), "commutation": commutation,
                "pilote": x.get("pilote", x["haut"] if commutation == "integre" else "")}
        conv.update({k: v for k, v in x.items() if k in CHAMPS})
        self._recalculer(conv, {k for k in DERIVES if k in x})
        conv["nom"] = x.get("nom") or _nom(conv)
        conv["origine"] = "impose"
        self.convs.append({k: conv[k] for k in CHAMPS})
        self.appliquees.append(f"{nom} : {chemin} : convertisseur {conv['reseau_commutation']} ajouté")

    def modifier(self, i, x, chemin, nom):
        conv = self.convs[i]
        changes = sorted(k for k, v in x.items() if k in CHAMPS and k not in ("reseau_commutation", "origine")
                         and _normal(k, conv.get(k)) != _normal(k, v))
        if not changes:
            return
        for k in changes:
            conv[k] = x[k]
        if set(changes) & ({"topologie", "commutation", "pilote", "haut"} | set(CHAMPS_RESEAU)):
            self._recalculer(conv, {k for k in DERIVES if k in x})
            if "nom" not in x:
                conv["nom"] = _nom(conv)
        if "topologie" in changes:
            self.a_conf.pop(conv["reseau_commutation"], None)
        if conv["origine"] == "deduit":
            conv["origine"] = "modifie"
        self.appliquees.append(f"{nom} : {chemin} : convertisseur {conv['reseau_commutation']} modifié "
                               f"({', '.join(changes)})")

    def eloigner(self, elo, conv, chemin, nom):
        """config_carte v1 : ``eloignement`` → éloignements ``dure`` de référence [haut] (§2.3, décision D-B42)."""
        for e in elo:
            self.eloignements.append({"type": "dure", "objets": e["broches"], "reference": [conv["haut"]],
                                      "distance_mm": e["distance_mm"]})
        self.appliquees.append(f"{nom} : {chemin}.eloignement : {len(elo)} éloignement(s) dure(s) de référence "
                               f"[{conv['haut']}] (champ eloignements)")

    def supprimer(self, i, chemin, nom):
        conv = self.convs.pop(i)
        self.a_conf.pop(conv["reseau_commutation"], None)
        self.appliquees.append(f"{nom} : {chemin} : convertisseur {conv['reseau_commutation']} supprimé")

    def bloc(self, bloc, chemin, nom, v1=False):
        """``{"ajouter", "modifier", "supprimer"}`` (contraintes, --config) ou liste (--config, v1 compris)."""
        K = self.K
        if isinstance(bloc, list):
            for j, x in enumerate(bloc):
                ch = f"{chemin}[{j}]"
                x = _controler_conv(K, x, ch, nom, v1, self.avert, supprimer_permis=True)
                i = _trouver(self.convs, x, True)
                elo = x.pop("_eloignement", None)
                if x.pop("supprimer", False):
                    if i is None:
                        raise ErreurFormat(f"{nom} : {ch} : convertisseur à supprimer introuvable")
                    self.supprimer(i, ch, nom)
                    continue
                if i is None:
                    self.ajouter(x, ch, nom)
                    i = len(self.convs) - 1
                else:
                    self.modifier(i, x, ch, nom)
                if elo:
                    self.eloigner(elo, self.convs[i], ch, nom)
            return
        if not isinstance(bloc, dict):
            raise ErreurFormat(f"{nom} : {chemin} : objet ou liste attendu")
        for k in bloc:
            if not k.startswith("_") and k not in ("ajouter", "modifier", "supprimer"):
                raise ErreurFormat(f"{nom} : {chemin}.{k} : clé inconnue"
                                   f"{_suggestion(k, ('ajouter', 'modifier', 'supprimer'))}")
        for op in ("supprimer", "modifier", "ajouter"):
            liste = bloc.get(op, [])
            if not isinstance(liste, list):
                raise ErreurFormat(f"{nom} : {chemin}.{op} : liste attendue")
            for j, x in enumerate(liste):
                ch = f"{chemin}.{op}[{j}]"
                x = _controler_conv(K, x, ch, nom, v1, self.avert)
                if op == "ajouter":
                    self.ajouter(x, ch, nom)
                    continue
                if not x.get("reseau_commutation"):
                    raise ErreurFormat(f"{nom} : {ch}.reseau_commutation : champ obligatoire")
                i = _trouver(self.convs, x, False)
                if i is None:
                    raise ErreurFormat(f"{nom} : {ch}.reseau_commutation : convertisseur "
                                       f"{x['reseau_commutation']} introuvable")
                if op == "supprimer":
                    self.supprimer(i, ch, nom)
                else:
                    self.modifier(i, x, ch, nom)


def _pieces_liste(K, v, chemin, nom):
    v = _liste_textes(v, chemin, nom, False)
    for i, d in enumerate(v):
        if d not in K.comps:
            raise ErreurFormat(f"{nom} : {chemin}[{i}] : pièce {d} inconnue")
    return v


def _lire_config(U, nom):
    """(config partielle, v1) d'un fichier --config : v2 (``config_carte/2``) ou v1 (sans « format »)."""
    if not isinstance(U, dict):
        raise ErreurFormat(f"{nom} : racine : objet JSON attendu")
    if "format" in U:
        n, _, v = str(U["format"]).partition("/")
        if n != "config_carte":
            raise ErreurFormat(f"{nom} : format : config_carte attendu")
        if v != "2":
            raise ErreurFormat(f"{nom} : format config_carte/{v} non pris en charge")
        connues = RACINE_CONFIG
        v1 = False
    else:
        connues, v1 = ("convertisseurs", "sans_piste_dessous", "classes"), True
    for k in U:
        if not k.startswith("_") and k not in connues:
            raise ErreurFormat(f"{nom} : {k} : clé inconnue{_suggestion(k, connues)}")
    return U, v1


# ---------------------------------------------------------------------------------------------------------------------
# Déduction
# ---------------------------------------------------------------------------------------------------------------------

def _dans_contour(c, contour):
    if not contour or len(contour) < 3:
        return True
    return point_dans_polygone((c.get("x", 0.0), c.get("y", 0.0)), contour)


def deduire(entree, surcharges=None):
    """Configuration ``config_carte/2`` de l'entrée ``entree_carte/2`` (§3.4), surcharges appliquées (R-V4)."""
    s = surcharges or {}
    contraintes, U = s.get("contraintes") or {}, s.get("config")
    noms = s.get("noms") or {}
    nom_k, nom_u = noms.get("contraintes", "contraintes"), noms.get("config", "config")
    controler_entree(entree, noms.get("entree", "entree"))
    natures, par_prefixe = classer(entree)
    K = _Carte(entree, natures)
    liste_masses, avert = _masses(K, entree, contraintes, nom_k)
    avert += [f"{d} : composant standard sans pastille" for d in K.ordre
              if K.comps[d]["genre"] == "standard" and not K.comps[d]["pastilles"]]
    convs, a_conf, masses_conv = _convertisseurs(K, liste_masses)
    sans = [d for d in K.ordre if natures[d] in ("inductance", "transformateur") and len(K.nets[d]) >= 2
            and K.surface[d] >= SURFACE_SANS_PISTE_MM2 - EPS]
    contour = (entree.get("carte") or {}).get("contour")
    suggerees = [c["designateur"] for c in entree.get("composants") or []
                 if natures[c["designateur"]] in ("connecteur", "interface") and _dans_contour(c, contour)]
    S = _Surcharges(K, convs, a_conf, set(liste_masses) | set(masses_conv))
    if contraintes.get("convertisseurs"):
        S.bloc(contraintes["convertisseurs"], "convertisseurs", nom_k)
    sp = contraintes.get("sans_piste_dessous") or {}
    if sp:
        if not isinstance(sp, dict):
            raise ErreurFormat(f"{nom_k} : sans_piste_dessous : objet attendu")
        for k in sp:
            if not k.startswith("_") and k not in ("ajouter", "retirer"):
                raise ErreurFormat(f"{nom_k} : sans_piste_dessous.{k} : clé inconnue")
        aj = _pieces_liste(K, sp.get("ajouter", []), "sans_piste_dessous.ajouter", nom_k)
        re_ = set(_pieces_liste(K, sp.get("retirer", []), "sans_piste_dessous.retirer", nom_k))
        sans = K.tries((set(sans) | set(aj)) - re_)
        S.appliquees.append(f"{nom_k} : sans_piste_dessous : {len(aj)} ajoutée(s), {len(re_)} retirée(s)")
    if U is not None:
        U, v1 = _lire_config(U, nom_u)
        if "convertisseurs" in U:
            S.bloc(U["convertisseurs"], "convertisseurs", nom_u, v1)
        for k, ch in (("sans_piste_dessous", "sans"), ("pieces_a_fixer_suggerees", "sugg")):
            if k in U:
                v = _pieces_liste(K, U[k], k, nom_u)
                if ch == "sans":
                    sans = v
                else:
                    suggerees = v
                S.appliquees.append(f"{nom_u} : {k} : imposé ({len(v)} pièce(s))")
        if not v1 and "eloignements" in U:
            S.eloignements = _eloignements_v2(K, U["eloignements"], "eloignements", nom_u)
            S.appliquees.append(f"{nom_u} : eloignements : imposé ({len(S.eloignements)} éloignement(s))")
        if v1 and "classes" in U:
            S.avert.append(f"{nom_u} : classes : ignoré (les classes viennent du fichier de la carte)")
    return {"format": FORMAT, "convertisseurs": S.convs, "sans_piste_dessous": sans,
            "pieces_a_fixer_suggerees": suggerees, "surcharges_appliquees": S.appliquees,
            "avertissements": avert + S.avert, "a_confirmer": list(S.a_conf.values()),
            "pieces_par_prefixe": par_prefixe, "eloignements": S.eloignements}


# ---------------------------------------------------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------------------------------------------------

def _normal(k, v):
    if k in CHAMPS_COND + CHAMPS_RESEAUX:
        return sorted([v] if isinstance(v, str) and v else ([] if isinstance(v, str) else v))
    if k == "boucle_chaude" and isinstance(v, dict):
        return {**v, "condensateurs": sorted(v.get("condensateurs", []))}
    return v


def comparer(deduit, ref):
    """Différences entre une configuration déduite et une référence (v1 ou v2). Ignorés : ``nom``, ``eloignement``,
    les commentaires « _… » et les clés absentes de la référence ; un ``reseau_commutation`` vide de la référence
    signifie « à déduire » (le convertisseur est alors apparié par ``haut`` et ``bas``)."""
    L = []
    cr, cd = ref.get("convertisseurs", []), deduit.get("convertisseurs", [])
    if len(cr) != len(cd):
        L.append(f"convertisseurs : {len(cd)} déduit(s), {len(cr)} dans la référence")
    for r in cr:
        i = _trouver(cd, r, True)
        nom = r.get("nom") or r.get("reseau_commutation")
        if i is None:
            L.append(f"convertisseur {nom + ' ' if nom else ''}({r.get('haut')}/{r.get('bas')}) non retrouvé")
            continue
        nom = nom or cd[i]["nom"]
        for k, v in r.items():
            if k in CLES_NON_DEDUITES or k.startswith("_") or (k == "reseau_commutation" and not v):
                continue
            if _normal(k, v) != _normal(k, cd[i].get(k)):
                L.append(f"{nom} / {k} : référence {v!r}, déduit {cd[i].get(k)!r}")
    for k in ("sans_piste_dessous", "pieces_a_fixer_suggerees"):
        if k in ref and sorted(ref[k]) != sorted(deduit.get(k, [])):
            L.append(f"{k} : référence {sorted(ref[k])}, déduit {sorted(deduit.get(k, []))}")
    return L


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------

USAGE = ("usage : python deduire_config.py E.json --sortie C.json [--contraintes K.json] [--config U.json] "
         "[--comparer R.json]")
OPTIONS = ("--sortie", "--contraintes", "--config", "--comparer")


def _arguments(argv):
    a, i = {"entree": None}, 0
    while i < len(argv):
        x = argv[i]
        if x in ("-h", "--aide", "--help"):
            return None
        cle, egal, val = x.partition("=")
        if cle in OPTIONS:
            if not egal:
                if i + 1 >= len(argv):
                    raise ErreurFormat(f"option {cle} sans valeur")
                i, val = i + 1, argv[i + 1]
            a[cle[2:]] = val
        elif x.startswith("-"):
            raise ErreurFormat(f"option inconnue : {cle}")
        elif a["entree"] is None:
            a["entree"] = x
        else:
            raise ErreurFormat("une seule entrée attendue")
        i += 1
    if a["entree"] is None:
        raise ErreurFormat("entrée manquante")
    if not a.get("sortie"):
        raise ErreurFormat("option --sortie manquante")
    return a


def _dire(texte, flux=None):
    flux = sys.stderr if flux is None else flux
    enc = getattr(flux, "encoding", None) or "utf-8"
    flux.write(texte.encode(enc, "replace").decode(enc, "replace") + "\n")
    flux.flush()


def main(argv=None):
    """Rend le code de sortie (§1.4) sans quitter le processus."""
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        a = _arguments(argv)
    except ErreurFormat as e:
        _dire(f"deduire_config : {e}")
        _dire(USAGE)
        return 2
    if a is None:
        _dire(USAGE, sys.stdout)
        return 0
    try:
        from importer_carte import ecrire_json  # noqa: PLC0415
        entree = formats.charger(a["entree"], "entree_carte/2")
        s, lecture = {"noms": {"entree": os.path.basename(a["entree"])}}, []
        if a.get("contraintes"):
            s["contraintes"], lecture = formats.lire_contraintes(a["contraintes"])
            s["noms"]["contraintes"] = os.path.basename(a["contraintes"])
        if a.get("config"):
            s["config"] = formats._lire_json(a["config"])
            s["noms"]["config"] = os.path.basename(a["config"])
        ref = formats._lire_json(a["comparer"]) if a.get("comparer") else None
        if ref is not None and not isinstance(ref, dict):
            raise ErreurFormat(f"{os.path.basename(a['comparer'])} : racine : objet JSON attendu")
        cfg = deduire(entree, s)
        cfg["avertissements"] = list(lecture) + cfg["avertissements"]
        ecrire_json(a["sortie"], cfg)
    except (ErreurFormat, ErreurEntree) as e:
        _dire(f"deduire_config : {e}")
        return 2
    except Exception as e:  # erreur interne : ni trace ni message système (qui peut contenir un chemin)
        _dire(f"deduire_config : erreur interne ({type(e).__name__})")
        return 4
    _dire(f"{os.path.basename(a['entree'])} : {len(cfg['convertisseurs'])} convertisseur(s), "
          f"{len(cfg['sans_piste_dessous'])} pièce(s) sans piste dessous -> {os.path.basename(a['sortie'])}",
          sys.stdout)
    for x in cfg["a_confirmer"]:
        _dire(f"à confirmer : {x}", sys.stdout)
    if ref is not None:
        diff = comparer(cfg, ref)
        if diff:
            _dire("différences avec la référence :\n  " + "\n  ".join(diff), sys.stdout)
            return 1
        _dire("identique à la référence", sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
