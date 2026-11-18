"""Données du moteur (``donnees_placeur/2``, contrat §2.5) et explication des déductions (``deduction/1``, §2.9).

usage : python construire_donnees.py E.json --config C.json [--contraintes K.json] [--gabarit-controle G.json]
        --sortie D.json [--deduction X.json]

API : ``construire(entree, contraintes, config, controle_gabarit=None, *, noms=None) -> (donnees, deduction)``.

- ``entree`` : ``entree_carte/2`` ; ``config`` : ``config_carte/2`` (``deduire_config``) ; ``contraintes`` :
  ``contraintes/2`` lu par ``formats.lire_contraintes`` (ou None) ; ``controle_gabarit`` : ``controle_gabarit/1``
  (ou None).
- ``noms`` (facultatif) : ``{"entree", "contraintes", "config", "controle_gabarit", "sha256_entree"}``, pour les
  messages et le bloc ``source``.
- Toute incohérence lève ``formats.ErreurFormat`` avec le chemin du champ (code 2), par exemple
  « k.json : contraintes.fixes[3] : pièce C99 inconnue ».

Règles appliquées (§3) :

- contraintes (§2.3) : repère « fichier » (Altium : origine affichée ``ORIGIN`` ; KiCad : y vers le bas) ou
  « carte » ; unité mil ou mm des coordonnées et des longueurs sans suffixe (une clé suffixée ``_mm`` est en mm) ;
  motifs ``fnmatch`` (avertissement pour un motif sans effet) ; références croisées (code 2) ;
- couches R-C1 à R-C7 et garde-fous ; masses (§3.2, trois règles) ; rôles R-R1 à R-R7, sensibilités R-S1 à R-S4, poids,
  largeurs, vias et valeurs de §3.8 ;
- pièces R-P2, R-P2b, R-P3 (contrôle de ``fixes_deplaces`` contre les autres pièces fixes), R-P5, R-P5b, R-P6, R-P7,
  R-P8 ; groupes de §3.6 ; éloignements, charges, paires, ``au_bord`` ; sources uniques de §2.5.

Codes (§1.4) : 0 succès ; 2 entrée invalide ; 4 erreur interne. Messages sans chemin de dossier. Aucune variable
d'environnement n'est lue.
"""
from __future__ import annotations

import collections
import difflib
import fnmatch
import hashlib
import json
import math
import os
import re
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import deduire_config as DC  # noqa: E402
import formats  # noqa: E402
import import_geometrie as G  # noqa: E402
from formats import ErreurFormat  # noqa: E402
from import_geometrie import ErreurEntree  # noqa: E402

VERSION = "2.0.0"
FORMAT = "donnees_placeur/2"
FORMAT_DEDUCTION = "deduction/1"
MIL = 0.0254
EPS = 1e-9
ROTATIONS = (0, 90, 180, 270)
ROLES = ("signal", "alimentation", "puissance", "commutation", "plan")
ROLES_MOTIFS = ("puissance", "alimentation", "signal", "commutation")
SENSIBILITES = ("sensible", "peu_sensible", "normale")
NATURES = ("condensateur", "resistance", "inductance", "ferrite", "transformateur", "transistor", "diode", "circuit",
           "connecteur", "quartz", "interface", "mecanique", "graphique", "liaison", "autre")
NATURES_GROUPE = ("resistance", "condensateur", "diode", "inductance", "ferrite")
ROLES_HORS_GROUPE = ("plan", "alimentation", "puissance")
BROCHES_GROUPE = 8               # §3.6 : réseau d'au plus 8 broches
PIECES_EXEMPLAIRE = 3            # §3.6 : exemplaire d'au moins 3 pièces
LARGEURS_DEFAUT = [0.254, 0.3, 3.0]
VIA_DEFAUT = {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.5, "diametre_max": 0.8, "trou_min": 0.25,
              "trou_max": 0.4}
ISOLEMENT_DEFAUT = 0.2
TROU_TROU_DEFAUT = 0.254
GARDE_DEFAUT = 0.6
MARGE_BORD_DEFAUT = 1.0
MARGE_DECOUPE_DEFAUT = 1.0
MARGE_TRAVERSANTE_DEFAUT = 0.6
MARGE_TROU = 0.6                 # rayon interdit = max(diamètre, diamètre du cuivre) / 2 + 0,6
GRILLE_DEFAUT = {"altium_pcbdoc": 0.127, "kicad_pcb": 0.05}
PAS_FIN_DEFAUT = 0.65
HAUTEUR_DESSOUS_DEFAUT = 3.0
TROU_PIECE_MIN = 2.0             # trous d'au moins 2 mm des pièces mécaniques fixes et virtuelles (§2.5)
TOL_MATRICE = 0.02               # R-P2b : voisines à ± pas, à 2 % près
MOTIF_CANAUX = r"^(.+?)(_[A-Za-z]+\d+)$"
EPAISSEUR_CUIVRE = 0.035
COTES = ("gauche", "droite", "haut", "bas", "quelconque")
FLECHE_CONTROLE = 0.02           # discrétisation des pastilles pour les contrôles de distance (par excès)
_JOKERS = re.compile(r"[*?\[]")
REGLE_MASSE = {k: f"R-M{k}" for k in (1, 2, 3)}   # identifiants des règles de §3.2


def _r(x):
    return round(float(x), 9)


def _num(x):
    """Nombre lisible à la française (« 2,0066 »)."""
    return f"{x:.6g}".replace(".", ",")


def _angle(a):
    """Angle d'orientation : entier s'il l'est (exemple de §2.5), sinon flottant."""
    a = float(a)
    return int(a) if a == int(a) else a


def _rect_poly(r):
    x0, y0, x1, y1 = r
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _convexe(poly):
    n, signe = len(poly), 0
    for i in range(n):
        a, b, c = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
        z = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(z) > EPS:
            s = 1 if z > 0 else -1
            if signe and s != signe:
                return False
            signe = s
    return True


_intersection_carte = G.intersection_carte      # déplacées dans import_geometrie (B-ET5)
_retrait = G.retrait_interieur


def _distance_polygones(a, b):
    if G.polygones_se_touchent(a, b):
        return 0.0
    na, nb = len(a), len(b)
    d = min(G.distance_point_segment(p, b[i], b[(i + 1) % nb]) for p in a for i in range(nb))
    return min(d, min(G.distance_point_segment(p, a[i], a[(i + 1) % na]) for p in b for i in range(na)))


def _demi_boite(p):
    """(hw, hh) : demi-dimensions de la boîte du cuivre d'une pastille dans le repère de la pièce (§2.5)."""
    if p["forme"] == "polygone" and p.get("points"):
        b = G.boite(p["points"])
        return (b[2] - b[0]) / 2, (b[3] - b[1]) / 2
    w, h = p["largeur"] / 2, p["hauteur"] / 2
    rc = {"rond": w, "oblong": min(w, h), "rect_arrondi": p.get("rayon_coin") or 0.0}.get(p["forme"], 0.0)
    rc = min(rc, w, h)
    a = math.radians(p.get("rotation") or 0.0)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return (w - rc) * c + (h - rc) * s + rc, (w - rc) * s + (h - rc) * c + rc


def _pose_dict(pose):
    return {"x": pose[0], "y": pose[1], "rotation": pose[2], "face": "dessous" if pose[3] else "dessus"}


def _boite_monde(lbb, pose):
    pd = _pose_dict(pose)
    pts = [G.local_vers_monde(q, pd) for q in _rect_poly(lbb)]
    return G.orienter(pts)


def _cle_naturelle(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _cles_suffixees_mm(v):
    """Noms des clés suffixées ``_mm`` présentes à toute profondeur d'un objet JSON."""
    noms = set()
    if isinstance(v, dict):
        for k, w in v.items():
            if isinstance(k, str) and k.endswith("_mm"):
                noms.add(k)
            noms |= _cles_suffixees_mm(w)
    elif isinstance(v, list):
        for w in v:
            noms |= _cles_suffixees_mm(w)
    return noms


# ---------------------------------------------------------------------------------------------------------------------
# Lecture des contraintes (§2.1, §2.3)
# ---------------------------------------------------------------------------------------------------------------------

class _Contraintes:
    """Contraintes/2 : repère et unité, motifs, références croisées. Erreur : « <nom> : contraintes.<chemin> : … »."""

    def __init__(self, ct, entree, nom, ordre, comps, natures, reseaux, couches):
        self.ct = ct or {}
        self.prefixe = f"{nom} : " if nom else ""
        self.ordre, self.comps, self.natures = ordre, comps, natures
        self.reseaux, self.couches = reseaux, couches
        self.avert = []
        src = entree.get("source") or {}
        self.type = src.get("type")
        self.repere = self.choix("repere", self.ct.get("repere", "fichier"), ("fichier", "carte"))
        self.unite = self.choix("unite", self.ct.get("unite", "mm"), ("mm", "mil"))
        self.f = MIL if self.unite == "mil" else 1.0
        if self.unite == "mil":             # D-B43 : choix rendu visible (§0.4 et §2.3 ne s'accordent pas sur ce point)
            suffixees = sorted(_cles_suffixees_mm(self.ct))
            if suffixees:
                self.avert.append(f"contraintes.unite = mil : les clés suffixées _mm ({', '.join(suffixees)}) sont "
                                  f"lues en mm, comme leur nom l'indique ; seules les coordonnées et les longueurs sans "
                                  f"suffixe sont converties")
        self.kicad = self.type == "kicad_pcb"
        if self.kicad:
            self.o = src.get("origine_fichier_mm") or [0.0, 0.0]
        else:
            o, c = src.get("origine_fichier_mil") or [0.0, 0.0], src.get("origine_cao_mil") or [0.0, 0.0]
            self.d = ((c[0] - o[0]) * MIL, (c[1] - o[1]) * MIL)

    # --- erreurs et types ---
    def erreur(self, chemin, msg):
        raise ErreurFormat(f"{self.prefixe}contraintes.{chemin} : {msg}")

    def cles(self, chemin, obj, permises, obligatoires=()):
        if not isinstance(obj, dict):
            self.erreur(chemin, "objet attendu")
        for k in obj:
            if k.startswith("_") or k in permises:
                continue
            proche = difflib.get_close_matches(k, list(permises), n=1, cutoff=0.6)
            self.erreur(f"{chemin}.{k}", "clé inconnue" + (f" ; vouliez-vous dire '{proche[0]}' ?" if proche else ""))
        for k in obligatoires:
            if k not in obj:
                self.erreur(f"{chemin}.{k}", "champ manquant")
        return obj

    def liste(self, chemin, v):
        if not isinstance(v, list):
            self.erreur(chemin, "liste attendue")
        return v

    def objet(self, chemin, v):
        if not isinstance(v, dict):
            self.erreur(chemin, "objet attendu")
        return v

    def nombre(self, chemin, v, mini=None, strict=False):
        if isinstance(v, str):
            self.erreur(chemin, "nombre écrit entre guillemets")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            self.erreur(chemin, "nombre attendu")
        if mini is not None and (v < mini or (strict and v <= mini)):
            self.erreur(chemin, f"nombre {'>' if strict else '≥'} {_num(mini)} attendu")
        return float(v)

    def booleen(self, chemin, v):
        if not isinstance(v, bool):
            self.erreur(chemin, "true ou false attendu")
        return v

    def texte(self, chemin, v):
        if not isinstance(v, str):
            self.erreur(chemin, "texte attendu")
        return v

    def choix(self, chemin, v, permis):
        if v not in permis:
            self.erreur(chemin, f"{v!r} inconnu (permis : {', '.join(permis)})")
        return v

    def valeur(self, cle, defaut, lire=None):
        """Valeur d'une clé de premier niveau, sinon ``defaut``."""
        if cle not in self.ct or self.ct[cle] is None:
            return defaut
        return (lire or (lambda c, v: v))(cle, self.ct[cle])

    def mm(self, cle, defaut, strict=False):
        return self.valeur(cle, defaut, lambda c, v: self.nombre(c, v, 0.0, strict))

    # --- repère et unité ---
    def _xy(self, x, y):
        f = self.f
        if self.repere == "carte":
            return [_r(x * f), _r(y * f)]
        if self.kicad:
            return [_r(x * f - self.o[0]), _r(self.o[1] - y * f)]
        return [_r(x * f + self.d[0]), _r(y * f + self.d[1])]

    def point(self, chemin, v):
        if not isinstance(v, list) or len(v) != 2:
            self.erreur(chemin, "point [x, y] attendu" + (" (virgule décimale ?)" if isinstance(v, list) else ""))
        return self._xy(self.nombre(f"{chemin}[0]", v[0]), self.nombre(f"{chemin}[1]", v[1]))

    def longueur(self, chemin, v, mini=0.0):
        return _r(self.nombre(chemin, v, mini) * self.f)

    def rect(self, chemin, v):
        if not isinstance(v, list) or len(v) != 4:
            self.erreur(chemin, "rectangle [x0, y0, x1, y1] attendu" + (" (virgule décimale ?)"
                                                                         if isinstance(v, list) else ""))
        a = self._xy(self.nombre(f"{chemin}[0]", v[0]), self.nombre(f"{chemin}[1]", v[1]))
        b = self._xy(self.nombre(f"{chemin}[2]", v[2]), self.nombre(f"{chemin}[3]", v[3]))
        return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]

    def polygone(self, chemin, v):
        if not isinstance(v, list) or len(v) < 3:
            self.erreur(chemin, "polygone d'au moins 3 points attendu")
        return G.orienter([self.point(f"{chemin}[{i}]", p) for i, p in enumerate(v)])

    def forme(self, chemin, z):
        """(« rect »|« polygone », valeur convertie, polygone) d'une zone qui porte ``rect`` ou ``polygone``."""
        if ("rect" in z) == ("polygone" in z):
            self.erreur(chemin, "« rect » ou « polygone » attendu (un seul des deux)")
        if "rect" in z:
            r = self.rect(f"{chemin}.rect", z["rect"])
            return "rect", r, _rect_poly(r)
        p = self.polygone(f"{chemin}.polygone", z["polygone"])
        return "polygone", p, p

    def faces(self, chemin, v):
        L = self.liste(chemin, v)
        for i, f in enumerate(L):
            self.choix(f"{chemin}[{i}]", f, ("dessus", "dessous"))
        return list(dict.fromkeys(L))

    # --- références ---
    def piece(self, chemin, d):
        if self.texte(chemin, d) not in self.comps:
            self.erreur(chemin, f"pièce {d} inconnue")
        return d

    def reseau(self, chemin, n):
        if self.texte(chemin, n) not in self.reseaux:
            self.erreur(chemin, f"réseau {n} inconnu")
        return n

    def couche(self, chemin, n):
        if self.texte(chemin, n) not in self.couches:
            self.erreur(chemin, f"couche {n} inconnue (couches : {', '.join(self.couches)})")
        return n

    def couches_liste(self, chemin, v):
        L = self.liste(chemin, v)
        if L == ["toutes"]:
            return ["toutes"]
        return [self.couche(f"{chemin}[{i}]", n) for i, n in enumerate(L)]

    def pieces(self, chemin, v, selecteurs=False):
        """(désignateurs dans l'ordre des composants, {désignateur: chemin de l'élément qui le cite})."""
        L = self.liste(chemin, v)
        origine = {}
        for i, x in enumerate(L):
            ch = f"{chemin}[{i}]"
            x = self.texte(ch, x)
            if selecteurs and x.startswith("empreinte:"):
                m = x[len("empreinte:"):]
                t = [d for d in self.ordre if fnmatch.fnmatchcase(self.comps[d].get("empreinte", ""), m)]
            elif selecteurs and x.startswith("nature:"):
                n = self.choix(ch, x[len("nature:"):], NATURES)
                t = [d for d in self.ordre if self.natures.get(d) == n]
            elif _JOKERS.search(x):
                t = [d for d in self.ordre if fnmatch.fnmatchcase(d, x)]
            else:
                t = [self.piece(ch, x)]
            if not t:
                self.avert.append(f"contraintes.{ch} : motif « {x} » sans effet")
            for d in t:
                origine.setdefault(d, ch)
        return [d for d in self.ordre if d in origine], origine

    def reseaux_motifs(self, chemin, v):
        L = [v] if isinstance(v, str) else self.liste(chemin, v)
        res = set()
        for i, x in enumerate(L):
            ch = f"{chemin}[{i}]" if not isinstance(v, str) else chemin
            x = self.texte(ch, x)
            if _JOKERS.search(x):
                t = {n for n in self.reseaux if fnmatch.fnmatchcase(n, x)}
                if not t:
                    self.avert.append(f"contraintes.{ch} : motif « {x} » sans effet")
            else:
                t = {self.reseau(ch, x)}
            res |= t
        return res


# ---------------------------------------------------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------------------------------------------------

class _Construction:
    def __init__(self, entree, contraintes, config, gabarit, noms):
        self.e, self.cfg, self.gab, self.noms = entree, config, gabarit, noms
        self.nom_e = noms.get("entree", "entree")
        self.carte = entree["carte"]
        self.rg = entree.get("regles") or {}
        self.elements, self.suggestions, self.a_confirmer, self.avert, self.positions = [], [], [], [], []
        self.natures, self.par_prefixe = DC.classer(entree)
        self.sources_nature = {c["designateur"]: DC.nature_et_source(c)[1] for c in entree["composants"]}
        self.comps = {c["designateur"]: c for c in entree["composants"]}
        self.ordre = [c["designateur"] for c in entree["composants"]]
        self.K = DC._Carte(entree, self.natures)
        self.couches_e = self.carte.get("couches") or []
        self.noms_couches = [c["nom"] for c in self.couches_e]
        self.reseaux_tous = self._reseaux_tous()
        self.C = _Contraintes(contraintes, entree, noms.get("contraintes"), self.ordre, self.comps, self.natures,
                              set(self.reseaux_tous), self.noms_couches)
        self.contour = self.carte.get("contour") or []

    # --- outils ---
    def el(self, objet, propriete, valeur, regle, preuve="", source="deduit"):
        self.elements.append({"objet": objet, "propriete": propriete, "valeur": valeur, "regle": regle,
                              "preuve": preuve, "source": source})

    def _reseaux_tous(self):
        vus = list(self.e.get("reseaux") or [])
        ens = set(vus)

        def ajouter(n):
            if n and n not in ens:
                ens.add(n)
                extra.append(n)
        extra = []
        for c in self.e["composants"]:
            for p in c.get("pastilles") or []:
                ajouter(p.get("reseau"))
            for x in (c.get("regions") or []) + (c.get("vias") or []):
                ajouter(x.get("reseau"))
                for n in x.get("liaison") or []:
                    ajouter(n)
        for x in (self.e.get("pastilles_libres") or []) + (self.carte.get("cuivre_existant") or []) + \
                (self.carte.get("remplissages") or []):
            ajouter(x.get("reseau"))
        for c in self.couches_e:
            for p in c.get("plans") or []:
                ajouter(p.get("reseau"))
        return [n for n in vus if n] + sorted(extra)

    def _erreur_entree(self, msg):
        raise ErreurFormat(f"{self.nom_e} : {msg}")

    # --- config_carte ---
    def _config(self):
        cfg, nom = self.cfg, self.noms.get("config", "config")

        def err(ch, msg):
            raise ErreurFormat(f"{nom} : {ch} : {msg}")
        if not isinstance(cfg, dict):
            err("racine", "objet JSON attendu")
        for k in ("convertisseurs", "sans_piste_dessous", "pieces_a_fixer_suggerees"):
            if not isinstance(cfg.get(k), list):
                err(k, "liste attendue")
        res = set(self.reseaux_tous)
        self.convs = []
        for i, cv in enumerate(cfg["convertisseurs"]):
            ch = f"convertisseurs[{i}]"
            if not isinstance(cv, dict):
                err(ch, "objet attendu")
            for k in DC.CHAMPS:
                if k not in cv:
                    err(f"{ch}.{k}", "champ manquant")
            for k in DC.CHAMPS_PIECE:
                if cv[k] and cv[k] not in self.comps:
                    err(f"{ch}.{k}", f"pièce {cv[k]} inconnue")
            for k in DC.CHAMPS_COND:
                for j, d in enumerate(cv[k]):
                    if d not in self.comps:
                        err(f"{ch}.{k}[{j}]", f"pièce {d} inconnue")
            for k in DC.CHAMPS_RESEAU:
                if cv[k] and cv[k] not in res:
                    err(f"{ch}.{k}", f"réseau {cv[k]} inconnu")
            for k in DC.CHAMPS_RESEAUX:
                for j, n in enumerate(cv[k]):
                    if n not in res:
                        err(f"{ch}.{k}[{j}]", f"réseau {n} inconnu")
            bc = cv["boucle_chaude"]
            for j, d in enumerate(bc.get("condensateurs") or []):
                if d not in self.comps:
                    err(f"{ch}.boucle_chaude.condensateurs[{j}]", f"pièce {d} inconnue")
            self.convs.append({k: cv[k] for k in DC.CHAMPS})
        for k in ("sans_piste_dessous", "pieces_a_fixer_suggerees"):
            for j, d in enumerate(cfg[k]):
                if d not in self.comps:
                    err(f"{k}[{j}]", f"pièce {d} inconnue")
        self.sans_piste = list(cfg["sans_piste_dessous"])
        self.suggerees = set(cfg["pieces_a_fixer_suggerees"])
        for x in cfg.get("a_confirmer") or []:
            self.a_confirmer.append({"objet": "config_carte", "proposition": str(x), "raison": "deduire_config (R-V3)"})
        self.avert += [str(x) for x in cfg.get("avertissements") or []]
        for cv in self.convs:
            regle = {"synchrone": "R-V1", "asynchrone": "R-V2", "integre": "R-V3"}.get(cv["commutation"], "R-V4")
            src = "deduit" if cv["origine"] == "deduit" else "contrainte"
            self.el(f"convertisseur:{cv['nom']}", "topologie", cv["topologie"], regle if src == "deduit" else "R-V4",
                    f"{cv['haut']} / {cv['bas']} / {cv['pilote'] or '-'} / {cv['inductance']}, nœud "
                    f"{cv['reseau_commutation']}, boucle chaude {', '.join(cv['boucle_chaude']['condensateurs'])} "
                    f"sur {cv['boucle_chaude']['reseau_rail']}", src)
        for d in self.sans_piste:
            self.el(f"piece:{d}", "sans_piste_dessous", True, "R-V5",
                    f"config_carte.sans_piste_dessous ({len(self.sans_piste)} pièce(s))", "deduit")

    # --- paramètres (§3.8) ---
    def _parametres(self):
        C, rg = self.C, self.rg
        gb = rg.get("garde_bord")
        self.garde_bord = C.mm("garde_bord_mm", gb if gb is not None else GARDE_DEFAUT)
        self.garde_decoupe = C.mm("garde_decoupe_mm", self.garde_bord)
        self.garde_trou = C.mm("garde_trou_mm", self.garde_decoupe)
        self.marge_bord = C.mm("marge_bord_mm", gb if gb is not None else MARGE_BORD_DEFAUT)
        self.marge_decoupe = C.mm("marge_decoupe_mm", MARGE_DECOUPE_DEFAUT)
        ip = rg.get("isolement_pieces")
        self.marge_piece = C.mm("marge_piece_mm", max(0.25, ip / 2) if ip is not None else 0.25)
        self.marge_traversante = C.mm("marge_traversante_mm", MARGE_TRAVERSANTE_DEFAUT)
        self.grille = C.mm("grille_placement_mm", GRILLE_DEFAUT.get(C.type, 0.127), strict=True)
        self.pas_fin = C.mm("pas_fin_mm", PAS_FIN_DEFAUT)
        self.rayon_trou = C.mm("rayon_interdit_trou_mm", None)
        iso = rg.get("isolement") or {}
        self.iso = {"defaut": iso.get("defaut", ISOLEMENT_DEFAUT), "classes": list(iso.get("classes") or ["toutes"]),
                    "matrice": iso.get("matrice") or [[ISOLEMENT_DEFAUT]],
                    "classe_reference": iso.get("classe_reference", 0)}
        self.cref = self.iso["classe_reference"]
        h = rg.get("hauteurs") or {}
        hd = h.get("dessous")
        self.hmax = {False: C.mm("hauteur_max_dessus_mm", h.get("dessus")),
                     True: C.mm("hauteur_max_dessous_mm", hd if hd is not None else HAUTEUR_DESSOUS_DEFAUT)}
        b = C.booleen
        self.fixer_suggestions = C.valeur("fixer_suggestions", True, b)
        self.fixer_verrouilles = C.valeur("fixer_verrouilles", True, b)
        self.salles_zones = C.valeur("salles_comme_zones", True, b)
        self.r_c6 = C.valeur("couche_preferee_par_nom", False, b)
        self.via_meme_reseau = C.valeur("via_dans_pastille_meme_reseau", False, b)
        self.faces_defaut = C.valeur("faces_par_defaut", "auto",
                                     lambda c, v: C.choix(c, v, ("auto", "libres", "actuelle")))
        self.hauteur_inconnue = C.valeur("hauteur_inconnue", "face_entree",
                                         lambda c, v: C.choix(c, v, ("face_entree", "basse")))
        self.face_encombrants = C.valeur("face_encombrants", "actuelle",
                                         lambda c, v: C.choix(c, v, ("actuelle", "dessus")))
        rdm = C.valeur("rotations_dessous_multibroches", list(ROTATIONS), C.liste)
        self.rot_dessous = [_angle(C.nombre(f"rotations_dessous_multibroches[{i}]", a)) for i, a in enumerate(rdm)]
        motif = C.valeur("motif_canaux", MOTIF_CANAUX, C.texte)
        try:
            self.motif_canaux = re.compile(motif)
        except re.error as x:
            C.erreur("motif_canaux", f"expression régulière invalide ({x})")
        self.motif_texte = motif

    # --- couches (§3.1) ---
    def _couches(self):
        C = self.C
        cc = C.ct.get("couches")
        if cc is not None:
            C.cles("couches", cc, ("routage", "plans", "inutilisees", "preferees"))
            vus = {}
            for k in ("routage", "inutilisees"):
                for i, n in enumerate(C.liste(f"couches.{k}", cc.get(k, []))):
                    C.couche(f"couches.{k}[{i}]", n)
                    if n in vus:
                        C.erreur(f"couches.{k}[{i}]", f"couche {n} déjà citée dans couches.{vus[n]}")
                    vus[n] = k
            for n, v in C.objet("couches.plans", cc.get("plans", {})).items():
                C.couche(f"couches.plans.{n}", n)
                if n in vus:
                    C.erreur(f"couches.plans.{n}", f"couche {n} déjà citée dans couches.{vus[n]}")
                if isinstance(v, str):
                    C.reseau(f"couches.plans.{n}", v)
                else:
                    for j, r in enumerate(C.liste(f"couches.plans.{n}", v)):
                        C.reseau(f"couches.plans.{n}[{j}]", r)
        analyse = DC.analyser_couches(self.e, C.ct)
        self.couches = []
        for x, ce in zip(analyse, self.couches_e):
            zones = self._zones_plan(x, ce) if x["role"] == "plan" else []
            ep = ce.get("epaisseur_cuivre_mm")
            self.couches.append({"nom": x["nom"], "rang": ce.get("rang", len(self.couches)), "role": x["role"],
                                 "zones_plan": zones, "epaisseur_cuivre_mm": ep if ep else EPAISSEUR_CUIVRE,
                                 "direction_preferee": None})
            valeur = x["role"] + (f" {', '.join(x['reseaux'])}" if x["reseaux"] else "")
            self.el(f"couche:{x['nom']}", "role", valeur, x["regle"], x["preuve"], x["source"])
            if x["avertissement"]:
                self.avert.append(x["avertissement"])
            if x["plan_probable"]:
                self.a_confirmer.append({"objet": f"couche:{x['nom']}", "proposition": "plan",
                                         "raison": f"plan probable (R-C3) : {x['preuve']} ; à déclarer dans "
                                                   f"contraintes.couches.plans"})
        self.role_couche = {c["nom"]: c["role"] for c in self.couches}
        self.routage = [c["nom"] for c in self.couches if c["role"] == "routage"]
        if not self.routage:
            self._erreur_entree("aucune couche de routage (§3.1) : " + ", ".join(
                f"{c['nom']} ({c['role']})" for c in self.couches))
        self.plan_reseaux = collections.defaultdict(list)
        self.plans_couche = collections.defaultdict(set)
        for c in self.couches:
            for z in c["zones_plan"]:
                if c["nom"] not in self.plan_reseaux[z["reseau"]]:
                    self.plan_reseaux[z["reseau"]].append(c["nom"])
                self.plans_couche[c["nom"]].add(z["reseau"])
        self.externes = {False: self.couches[0]["nom"], True: self.couches[-1]["nom"]} if self.couches else {}

    def _couper(self, poly, trous):
        """Zones de plan (§3.1 R-C3, R-C2c) : contour du remplissage moins ses trous, intersecté avec la carte ;
        [(contour, trous)], plusieurs morceaux si la carte coupe le remplissage en plusieurs parties."""
        trous = [t for t in trous or [] if len(t) >= 3]
        if not self.contour or len(poly) < 3:
            return [([list(p) for p in poly], [[list(q) for q in t] for t in trous])]
        if not trous and _convexe(self.contour) and all(G.point_dans_polygone(p, self.contour)
                                                        or G.distance_point_polygone(p, self.contour) < 1e-6
                                                        for p in poly):
            return [([list(p) for p in poly], [])]          # carte convexe qui contient tous les sommets
        return _intersection_carte(poly, trous, self.contour)

    def _zones_plan(self, x, ce):
        nets = x["reseaux"]
        fills = [f for f in self.carte.get("remplissages") or [] if f["couche"] == x["nom"] and f.get("reseau")]
        neg = [p for p in ce.get("plans") or [] if p.get("reseau")]
        zones = []
        for n in nets:
            pn = [p for p in neg if p["reseau"] == n]
            fn = [f for f in fills if f["reseau"] == n]
            if pn:
                zones += [{"reseau": n, "contour": p["contour"], "trous": p.get("trous") or []} for p in pn]
            elif fn:
                morceaux = [m for f in fn for m in self._couper(f["contour"], f.get("trous"))]
                zones += [{"reseau": n, "contour": c, "trous": t} for c, t in morceaux]
                if not morceaux:            # aucun remplissage dans la carte : plan sans géométrie (R-C1)
                    self.avert.append(f"couche {x['nom']} : remplissage(s) du réseau {n} hors de la carte ; zone de "
                                      f"plan = contour moins garde_bord_mm")
                    zones.append({"reseau": n, "contour": _retrait(self.contour, self._garde_plan()), "trous": []})
            else:
                zones.append({"reseau": n, "contour": _retrait(self.contour, self._garde_plan()), "trous": []})
        return zones

    def _garde_plan(self):
        return self.garde_bord

    # --- masses (§3.2) ---
    def _masses(self):
        C = self.C
        roles = C.ct.get("roles")
        if roles is not None:
            C.cles("roles", roles, ROLES_MOTIFS + SENSIBILITES + ("masse",))
            if "masse" in roles:
                v = roles["masse"]
                if isinstance(v, str):
                    C.reseau("roles.masse", v)
                else:
                    for i, r in enumerate(C.liste("roles.masse", v)):
                        C.reseau(f"roles.masse[{i}]", r)
        liste, avert = DC._masses(self.K, self.e, C.ct)
        if roles and roles.get("masse"):
            for m in liste:
                self.el(f"reseau:{m}", "masse", True, REGLE_MASSE[1], "contraintes.roles.masse", "contrainte")
        elif liste:
            m = liste[0]
            caps = sum(1 for d in self.ordre if self.natures[d] == "condensateur" and self.K.npads[d] == 2
                       and len(self.K.nets[d]) == 2 and m in self.K.nets[d])
            plan = "zone de plan" if m in self.plan_reseaux else "sans zone de plan"
            self.el(f"reseau:{m}", "masse", True, REGLE_MASSE[2],
                    f"{plan}, {caps} condensateur(s) à deux pastilles, {self.K.taille[m]} pastilles")
        for a in avert:
            self.avert.append(a)
            if liste:
                self.a_confirmer.append({"objet": f"reseau:{liste[0]}", "proposition": "masse principale",
                                         "raison": a})
        for cv in self.convs:
            m = cv["reseau_masse"]
            if m and m not in liste:
                liste.append(m)
                self.el(f"reseau:{m}", "masse", True, REGLE_MASSE[3], f"reseau_masse du convertisseur {cv['nom']}")
        self.masses = liste

    # --- paires ---
    def _paires(self):
        C = self.C
        par_nom = {}
        for p in self.e.get("paires") or []:
            par_nom[p["nom"]] = {"nom": p["nom"], "positif": p["positif"], "negatif": p["negatif"],
                                 "ecart_mm": p.get("ecart"), "largeur_mm": p.get("largeur")}
        for i, p in enumerate(C.liste("paires", C.ct.get("paires", []))):
            ch = f"paires[{i}]"
            C.cles(ch, p, ("nom", "positif", "negatif", "ecart_mm", "largeur_mm"), ("nom",))
            nom = C.texte(f"{ch}.nom", p["nom"])
            x = par_nom.get(nom)
            if x is None:
                C.cles(ch, p, ("nom", "positif", "negatif", "ecart_mm", "largeur_mm"), ("nom", "positif", "negatif"))
                x = par_nom[nom] = {"nom": nom, "positif": "", "negatif": "", "ecart_mm": None, "largeur_mm": None}
            for k in ("positif", "negatif"):
                if k in p:
                    x[k] = C.reseau(f"{ch}.{k}", p[k])
            for k in ("ecart_mm", "largeur_mm"):
                if k in p:
                    x[k] = C.nombre(f"{ch}.{k}", p[k], 0.0, strict=True)
        tol = self.rg.get("tolerances_longueur") or {}
        self.paires = [dict(x, tolerance_longueur_mm=tol.get(x["nom"])) for x in par_nom.values()]
        self.paire_de = {}
        for x in self.paires:
            self.paire_de.setdefault(x["positif"], x["nom"])
            self.paire_de.setdefault(x["negatif"], x["nom"])

    # --- rôles (§3.3) et valeurs des réseaux (§3.8) ---
    def _reseaux(self):
        C, rg = self.C, self.rg
        roles = C.ct.get("roles") or {}
        motifs_role = {r: C.reseaux_motifs(f"roles.{r}", roles[r]) for r in ROLES_MOTIFS if r in roles}
        motifs_sens = {s: C.reseaux_motifs(f"roles.{s}", roles[s]) for s in SENSIBILITES if s in roles}
        par = C.objet("reseaux", C.ct.get("reseaux", {}))
        champs = ("role", "sensibilite", "poids", "priorite", "largeurs_mm", "couche_preferee", "couches_permises",
                  "descente_large")
        for n, v in par.items():
            C.reseau(f"reseaux.{n}", n)
            C.cles(f"reseaux.{n}", v, champs)
        pref = {}
        cc = C.ct.get("couches") or {}
        for n, couche in C.objet("couches.preferees", cc.get("preferees", {})).items():
            C.reseau(f"couches.preferees.{n}", n)
            pref[n] = self._couche_routage(f"couches.preferees.{n}", couche)
        rr = rg.get("reseaux") or {}
        ld = rg.get("largeur_defaut") or LARGEURS_DEFAUT
        d = ld[1]
        propre_carte = any(v.get("regle_largeur_propre") for v in rr.values())
        commut = {cv["reseau_commutation"]: cv["nom"] for cv in self.convs if cv["reseau_commutation"]}
        es = {}
        for cv in self.convs:
            for k in ("reseau_entree", "reseau_sortie"):
                if cv[k]:
                    es.setdefault(cv[k], cv["nom"])
        classes = sorted(((k, v) for k, v in (self.e.get("classes") or {}).items()), key=lambda kv: len(kv[1]))
        classe_de = {}
        for k, v in classes:
            for n in v:
                classe_de.setdefault(n, k)
        r6 = {}
        for c in self.couches:
            if c["role"] != "routage":
                continue
            ce = self.couches_e[self.noms_couches.index(c["nom"])]
            nf = ce.get("nom_fichier") or c["nom"]
            cand = [n for n in self.reseaux_tous if len(n) >= 2 and DC._mot_delimite(n, nf)]
            if cand:
                r6.setdefault(max(cand, key=len), (c["nom"], nf))

        def gamme(n):
            v = rr.get(n) or {}
            if not v.get("regle_largeur_propre"):
                return None
            mn, pf, mx = v["largeurs"]
            r = pf / d if d > 0 else math.inf
            return r, mx is None or mx > 2 * pf + EPS, (mn, pf, mx)

        def preuve_largeur(g):
            mn, pf, mx = g[2]
            return (f"largeur préférée {_num(pf)} = {DC._fr(g[0])} × défaut {_num(d)}, maximum "
                    f"{'aucun' if mx is None else _num(mx)}")

        mat = self.iso["matrice"]
        self.reseaux = []
        for n in self.reseaux_tous:
            pr, g, v = par.get(n) or {}, gamme(n), rr.get(n) or {}
            ch = f"reseaux.{n}"
            if "role" in pr:
                role, regle, src, preuve = C.choix(f"{ch}.role", pr["role"], ROLES), "R-R1", "contrainte", ch
            elif any(n in m for m in motifs_role.values()):
                role = next(r for r in ROLES_MOTIFS if n in motifs_role.get(r, ()))
                regle, src, preuve = "R-R2", "contrainte", f"contraintes.roles.{role}"
            elif n in self.plan_reseaux:
                role, regle, src = "plan", "R-R3", "deduit"
                preuve = f"zone de plan sur {', '.join(self.plan_reseaux[n])}"
            elif n in commut:
                role, regle, src, preuve = "commutation", "R-R4", "deduit", f"nœud du convertisseur {commut[n]}"
            elif g and g[0] >= 3 - 1e-3 and g[1]:
                role, regle, src, preuve = "puissance", "R-R5", "fichier", preuve_largeur(g)
            elif g and 1.5 - 1e-3 <= g[0] < 3 - 1e-3 and g[1]:
                role, regle, src, preuve = "alimentation", "R-R6", "fichier", preuve_largeur(g)
            elif g is None and n in es:
                role, regle, src = "puissance", "R-R5b", "deduit"
                preuve = f"entrée ou sortie du convertisseur {es[n]}, sans règle de largeur propre"
            elif g is None and v.get("alimentation_schema") and not propre_carte:
                role, regle, src = "alimentation", "R-R6b", "fichier"
                preuve = "SupplyNets, aucune règle de largeur propre"
            else:
                role, regle, src = "signal", "R-R7", "deduit"
                preuve = (f"{preuve_largeur(g)} : ni R-R5 (≥ 3) ni R-R6 (≥ 1,5) avec maximum > 2 × préférée"
                          if g else f"{self.K.taille.get(n, 0)} pastille(s), aucune règle de largeur propre, ni "
                                    f"contrainte, ni plan, ni convertisseur")
            self.el(f"reseau:{n}", "role", role, regle, preuve, src)
            if "sensibilite" in pr:
                sens = C.choix(f"{ch}.sensibilite", pr["sensibilite"], SENSIBILITES)
                rs, ss, ps = "R-S1", "contrainte", ch
            elif any(n in m for m in motifs_sens.values()):
                sens = next(s for s in SENSIBILITES if n in motifs_sens.get(s, ()))
                rs, ss, ps = "R-S2", "contrainte", f"contraintes.roles.{sens}"
            elif role == "signal" and g and g[2][2] is not None and g[2][2] <= 2 * g[2][1] + EPS:
                sens, rs, ss = "sensible", "R-S3", "fichier"
                ps = f"gamme étroite : maximum {_num(g[2][2])} ≤ 2 × préférée {_num(g[2][1])}"
            else:
                sens, rs, ss, ps = "normale", "R-S4", "deduit", ""
            if sens != "normale" or rs != "R-S4":
                self.el(f"reseau:{n}", "sensibilite", sens, rs, ps, ss)
            if "largeurs_mm" in pr:
                L = pr["largeurs_mm"]
                if not isinstance(L, list) or len(L) != 3:
                    C.erreur(f"{ch}.largeurs_mm", "[min, préférée, max|null] attendu")
                L = [C.nombre(f"{ch}.largeurs_mm[0]", L[0], 0.0, True), C.nombre(f"{ch}.largeurs_mm[1]", L[1], 0.0,
                                                                                    True),
                     None if L[2] is None else C.nombre(f"{ch}.largeurs_mm[2]", L[2], 0.0, True)]
                if L[0] > L[1] + EPS or (L[2] is not None and L[1] > L[2] + EPS):
                    C.erreur(f"{ch}.largeurs_mm", "min ≤ préférée ≤ max attendu")
            elif g:
                L = list(g[2])
            elif regle == "R-R5b":
                L = [ld[0], min(3 * d, ld[2]) if ld[2] is not None else 3 * d, ld[2]]
            elif v.get("largeurs"):
                L = list(v["largeurs"])
            else:
                L = list(ld)
            if "poids" in pr:
                poids = C.nombre(f"{ch}.poids", pr["poids"], 0.0)
                self.el(f"reseau:{n}", "poids", poids, "§3.3", ch, "contrainte")
            else:
                poids = 0.0 if role == "plan" else 3.0 if role in ("puissance", "commutation") else 1.0
            prio = None
            if pr.get("priorite") is not None:
                prio = pr["priorite"]
                if isinstance(prio, bool) or not isinstance(prio, int):
                    C.erreur(f"{ch}.priorite", "entier ou null attendu")
            if "descente_large" in pr:
                dl = C.booleen(f"{ch}.descente_large", pr["descente_large"])
            else:
                dl = role == "plan" and bool(g) and g[0] >= 3 - 1e-3 and g[1]
            cp = None
            if pr.get("couche_preferee") is not None:
                cp = self._couche_routage(f"{ch}.couche_preferee", pr["couche_preferee"])
                self.el(f"reseau:{n}", "couche_preferee", cp, "R-C7", ch, "contrainte")
            elif n in pref:
                cp = pref[n]
                self.el(f"reseau:{n}", "couche_preferee", cp, "R-C7", "contraintes.couches.preferees", "contrainte")
            elif n in r6 and role != "plan":
                if self.r_c6:
                    cp = r6[n][0]
                    self.el(f"reseau:{n}", "couche_preferee", cp, "R-C6", f"le nom de la couche {r6[n][1]} contient "
                                                                          f"« {n} »")
                else:
                    self.suggestions.append({"objet": f"reseau:{n}", "proposition": f"couche préférée {r6[n][0]} "
                                             f"(contraintes.couches.preferees)", "raison": f"R-C6 : le nom de la "
                                             f"couche {r6[n][1]} contient « {n} » ; règle désactivée par défaut "
                                             f"(contraintes.couche_preferee_par_nom)"})
            if "couches_permises" in pr:
                perm = [self._couche_routage(f"{ch}.couches_permises[{i}]", x)
                        for i, x in enumerate(C.liste(f"{ch}.couches_permises", pr["couches_permises"]))]
            elif v.get("couches_permises") is not None:
                perm = [x for x in self.routage if x in v["couches_permises"]]
            else:
                perm = list(self.routage)
            if not perm and role != "plan":
                self._erreur_entree(f"réseau {n} : aucune couche de routage permise (règle RoutingLayers ou "
                                    f"contraintes)")
            ci = v.get("classes_isolement") or {"piste": self.cref, "via": self.cref, "surface": self.cref}
            self.reseaux.append({
                "nom": n, "classe": classe_de.get(n, ""), "role": role, "masse": n in self.masses,
                "sensibilite": sens, "largeurs_mm": [None if x is None else _r(x) for x in L],
                "descente_large": dl, "isolement_mm": mat[ci["piste"]][self.cref], "classes_isolement": dict(ci),
                "via": dict(v.get("via") or rg.get("via") or VIA_DEFAUT), "poids": poids, "priorite": prio,
                "priorite_routage": v.get("priorite_routage") or 0, "couche_preferee": cp, "couches_permises": perm,
                "plan_couches": list(self.plan_reseaux.get(n, [])), "remplissages": [],
                "alimentation_schema": bool(v.get("alimentation_schema")), "paire": self.paire_de.get(n)})
        self.reseau = {x["nom"]: x for x in self.reseaux}

    def _couche_routage(self, chemin, couche):
        self.C.couche(chemin, couche)
        if self.role_couche.get(couche) != "routage":
            self.C.erreur(chemin, f"couche {couche} : pas une couche de routage ({self.role_couche.get(couche)})")
        return couche

    # --- pièces (§3.5) ---
    def _virtuels(self):
        """R-P8 : une pièce virtuelle fixe par pastille libre avec réseau."""
        res = []
        for p in self.e.get("pastilles_libres") or []:
            if not p.get("reseau"):
                continue
            des = p["id"]
            dessous = p.get("face") == "dessous"
            q = {k: v for k, v in p.items() if k not in ("id", "x", "y")}
            q["id"], q["x"], q["y"] = f"{des}#0", 0.0, 0.0
            q["rotation"] = -(p.get("rotation") or 0.0) if dessous else (p.get("rotation") or 0.0)
            if p.get("forme") == "polygone" and p.get("points"):
                q["points"] = [[a - p["x"], (p["y"] - b) if dessous else (b - p["y"])] for a, b in p["points"]]
            q["face"] = "toutes" if p.get("face") == "toutes" else "composant"
            hw, hh = _demi_boite(q)
            res.append({"designateur": des, "designateur_source": "", "empreinte": "", "description": "",
                        "reference": "", "valeur": "", "genre": "standard", "classes_composants": [], "canal": None,
                        "hauteur": 0.0, "source_hauteur": "inconnue", "x": p["x"], "y": p["y"], "rotation": 0.0,
                        "face": "dessous" if dessous else "dessus", "verrouille": True,
                        "boite": [-hw, -hh, hw, hh], "pastilles": [q], "regions": [], "vias": [],
                        "zones_interdites": [], "decoupes": [], "_virtuel": True})
            self.el(f"piece:{des}", "virtuel", True, "R-P8", f"pastille libre {p.get('nom', '')} du réseau "
                                                             f"{p['reseau']}")
        return res

    def _pads_monde(self, c, pose):
        pd = _pose_dict(pose)
        return [(p, [G.local_vers_monde(q, pd) for q in G.contour_pastille(p, FLECHE_CONTROLE)])
                for p in c["pastilles"]]

    def _hors_contour(self, c, pose):
        if not self.contour:
            return False
        return not G.polygones_se_touchent(_boite_monde(self._lbb(c), pose), self.contour)

    def _au_bord(self, c, pose):
        """R-P3 « bord » : une pastille à moins de 2 × garde_bord du contour, ou la boîte qui chevauche le contour."""
        if not self.contour:
            return None
        if any(not G.point_dans_polygone(q, self.contour) for q in _boite_monde(self._lbb(c), pose)):
            return "boîte qui chevauche le contour"
        lim = 2 * self.garde_bord
        n = len(self.contour)
        segs = [(self.contour[i], self.contour[(i + 1) % n]) for i in range(n)]
        for p, poly in self._pads_monde(c, pose):
            for q in poly:
                dmin = min(G.distance_point_segment(q, a, b) for a, b in segs)
                if dmin < lim - EPS:
                    return f"pastille {p['id']} à {_num(round(dmin, 4))} mm du contour (< 2 × {_num(self.garde_bord)})"
        return None

    def _lbb(self, c):
        return self.boites.get(c["designateur"], c["boite"])

    def _composants(self):
        C = self.C
        ct = C.ct
        self.virtuels = self._virtuels()
        tous = self.e["composants"] + self.virtuels
        self.tous = {c["designateur"]: c for c in tous}
        fixes, orig_fixes = C.pieces("fixes", ct.get("fixes", []))
        fixes = set(fixes)
        fd = {}
        for des, v in C.objet("fixes_deplaces", ct.get("fixes_deplaces", {})).items():
            ch = f"fixes_deplaces.{des}"
            c = self.comps.get(des) or C.piece(ch, des)
            if isinstance(v, list):
                xy, rot, face = C.point(ch, v), c["rotation"], c["face"]
            else:
                C.cles(ch, v, ("x", "y", "rotation", "face"), ("x", "y"))
                xy = C.point(ch, [v["x"], v["y"]])
                rot = C.nombre(f"{ch}.rotation", v["rotation"]) if "rotation" in v else c["rotation"]
                face = C.choix(f"{ch}.face", v["face"], ("dessus", "dessous")) if "face" in v else c["face"]
            deplacee = (math.hypot(xy[0] - c["x"], xy[1] - c["y"]) > 1e-3 or face != c["face"]
                        or abs((float(rot) - c["rotation"] + 180.0) % 360.0 - 180.0) > 1e-6)
            if c.get("decoupes") and deplacee:      # D-B70 : pose d'entrée redite à 0,001 mm près = pas un déplacement
                C.erreur(ch, f"pièce {des} propriétaire de la découpe {', '.join(c['decoupes'])} : le moteur ne sait "
                             f"pas déplacer une découpe (§3.5 R-P3)")
            self.positions.append({"piece": des, "repere_fichier": v, "repere_carte": [xy[0], xy[1]]})
            if not deplacee:                        # D-B70 : simple pièce fixe, à sa pose d'entrée
                fixes.add(des)
                continue
            fd[des] = [xy[0], xy[1], float(rot), face == "dessous"]
        liberer, _ = C.pieces("liberer", ct.get("liberer", []))
        liberer = set(liberer)
        dessus_s, _ = C.pieces("dessus_seulement", ct.get("dessus_seulement", []), selecteurs=True)
        dessous_s, o_dessous = C.pieces("dessous_seulement", ct.get("dessous_seulement", []), selecteurs=True)
        for d in set(dessus_s) & set(dessous_s):
            C.erreur(o_dessous[d], f"pièce {d} à la fois dans dessus_seulement et dessous_seulement")
        dessus_s, dessous_s = set(dessus_s), set(dessous_s)
        orients = {}
        for des, v in C.objet("orientations", ct.get("orientations", {})).items():
            ch = f"orientations.{des}"
            C.piece(ch, des)
            L = []
            for i, o in enumerate(C.liste(ch, v)):
                if not isinstance(o, list) or len(o) != 2:
                    C.erreur(f"{ch}[{i}]", "[rotation, dessous] attendu")
                L.append([_angle(C.nombre(f"{ch}[{i}][0]", o[0])), C.booleen(f"{ch}[{i}][1]", o[1])])
            if not L:
                C.erreur(ch, "liste non vide attendue")
            orients[des] = L
        self.boites = {}
        for des, v in C.objet("boites", ct.get("boites", {})).items():
            ch = f"boites.{des}"
            C.piece(ch, des)
            if not isinstance(v, list) or len(v) != 4:
                C.erreur(ch, "rectangle [x0, y0, x1, y1] attendu")
            b = [C.longueur(f"{ch}[{i}]", x, None) for i, x in enumerate(v)]
            self.boites[des] = [min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])]
        exceptions = set(C.pieces("exceptions_hauteur", ct.get("exceptions_hauteur", []))[0])
        via_ok = set(C.pieces("via_sous_cms_permis", ct.get("via_sous_cms_permis", []))[0])
        non_ret = {}
        for d, raison in ((self.gab or {}).get("non_retournables") or {}).items():
            if d in self.comps:
                non_ret[d] = raison
            else:
                self.avert.append(f"controle_gabarit : non_retournables : pièce {d} inconnue, ignorée")
        conv_pieces = set()
        courts = set()
        for cv in self.convs:
            conv_pieces |= {cv[k] for k in DC.CHAMPS_PIECE if cv[k]}
            for k in DC.CHAMPS_COND:
                conv_pieces |= set(cv[k])
            courts |= set(cv["reseaux_courts"])
        for d in self.ordre:
            if self.K.npads[d] == 2 and self.K.nets[d] & courts:
                conv_pieces.add(d)
        faces_cms = {c["face"] for c in self.e["composants"]
                     if any(not p.get("traversante") for p in c["pastilles"])}
        fpd = self.faces_defaut if self.faces_defaut != "auto" else ("actuelle" if len(faces_cms) <= 1 else "libres")
        self.faces_effectif = fpd
        rpieces = self.rg.get("pieces") or {}
        # statuts (R-P3)
        statut = {}
        for c in tous:
            des = c["designateur"]
            pose = [c["x"], c["y"], float(c["rotation"]), c["face"] == "dessous"]
            if des in fixes or des in fd:
                statut[des] = ("contrainte", "contraintes." + ("fixes_deplaces" if des in fd else "fixes"))
                if des in fixes and des not in fd and self._hors_contour(c, pose):
                    C.erreur(orig_fixes[des], f"pièce {des} entièrement hors du contour, fixée à sa pose d'entrée "
                                              f"(§3.5 R-P3)")
                continue
            if c.get("_virtuel"):
                statut[des] = ("virtuel", "pastille libre avec réseau (R-P8)")
                continue
            if not c["pastilles"]:
                statut[des] = ("sans pastille", f"genre {c['genre']}")
                continue
            hors = self._hors_contour(c, pose)
            raisons = []
            if self.natures[des] == "mecanique":
                raisons.append(("mecanique", f"nature mécanique ({self.sources_nature[des]})"))
            if c.get("decoupes"):
                raisons.append(("decoupe", f"propriétaire de {', '.join(c['decoupes'])}"))
            if c.get("verrouille") and self.fixer_verrouilles:
                raisons.append(("verrouillee", "verrouillée dans le fichier"))
            if not hors:
                b = self._au_bord(c, pose)
                if b:
                    raisons.append(("bord", b))
                if des in self.suggerees and self.fixer_suggestions:
                    raisons.append(("suggestion", f"nature {self.natures[des]} dans le contour (R-P4)"))
            if des in liberer:
                raisons = [r for r in raisons if r[0] == "decoupe"]
            if hors:
                if raisons and raisons[0][0] != "decoupe":
                    self.el(f"piece:{des}", "statut", "mobile", "R-P3", "entièrement hors du contour : jamais fixée "
                                                                        "automatiquement")
                raisons = [r for r in raisons if r[0] == "decoupe"]
            statut[des] = raisons[0] if raisons else ("libre", "contraintes.liberer" if des in liberer else "")
        self.statut = statut
        # composants
        self.composants = []
        for c in tous:
            des = c["designateur"]
            raison, preuve = statut[des]
            fixe = raison != "libre"
            pose = [c["x"], c["y"], float(c["rotation"]), c["face"] == "dessous"]
            cible = fd.get(des, pose)
            virt = bool(c.get("_virtuel"))
            nature = "autre" if virt else self.natures[des]
            self.el(f"piece:{des}", "statut", "fixe" if fixe else "mobile", "R-P3", f"{raison} : {preuve}"
                    if preuve else raison, "contrainte" if raison == "contrainte" else "deduit")
            if not virt:
                self.el(f"piece:{des}", "nature", nature, "R-P1", f"source : {self.sources_nature[des]}")
            pas = self._pas_min(c)
            pas_fin, matrice, preuve_pf = self._pas_fin(c, pas)
            if pas_fin:
                self.el(f"piece:{des}", "pas_fin", True, "R-P2b" if matrice else "R-P2", preuve_pf)
            if matrice:
                self._controle_billes(c, pas)
            if fixe:
                orientations, regle = [], f"fixe : {raison}"
                self._garde_face_fixe(c)
            else:
                orientations, regle = self._orientations(c, orients, dessus_s, dessous_s, non_ret, exceptions,
                                                         conv_pieces, fpd, rpieces.get(des) or {})
                self.el(f"piece:{des}", "orientations", len(orientations),
                        "R-P5b" if regle.startswith("convertisseur") else "R-P5", regle,
                        "contrainte" if "contrainte" in regle else "deduit")
            pads = [self._pastille(p, des in via_ok) for p in c["pastilles"]]
            if des in via_ok:
                self.el(f"piece:{des}", "via_dans_pastille_permis", True, "R-P6", "contraintes.via_sous_cms_permis",
                        "contrainte")
            elif any(p["via_dans_pastille_permis"] and not p["traversante"] for p in pads):
                self.el(f"piece:{des}", "via_dans_pastille_permis", True, "R-P6", "règle ViasUnderSMD", "fichier")
            self.composants.append({
                "nom": des, "empreinte": c["empreinte"], "nature": nature, "genre": c["genre"], "valeur": c["valeur"],
                "hauteur": c.get("hauteur") or 0.0, "statut": "fixe" if fixe else "mobile",
                "raison_statut": raison, "virtuel": virt, "lbb": list(self._lbb(c)), "pastilles": pads,
                "regions": c.get("regions") or [], "vias": c.get("vias") or [],
                "zones_interdites": c.get("zones_interdites") or [], "decoupes": c.get("decoupes") or [],
                "pose_actuelle": pose, "pose_cible": list(cible), "orientations": orientations, "regle": regle,
                "pas_fin": pas_fin, "matrice": matrice, "pas_min_mm": None if pas is None else _r(pas)})
        self.composant = {x["nom"]: x for x in self.composants}
        for des in fd:
            self._controle_deplace(des)
        if self.par_prefixe:
            self.avert.append("pièces classées par leur seul préfixe (R-P1) : " + ", ".join(self.par_prefixe))

    def _pastille(self, p, via_ok):
        q = {"id": p["id"], "nom": p["nom"], "reseau": p["reseau"], "lx": p["x"], "ly": p["y"]}
        for k in ("forme", "largeur", "hauteur", "rotation", "rayon_coin", "points", "couches", "face", "trou",
                  "traversante", "expansion_masque", "pile", "via_dans_pastille_permis", "classe_isolement"):
            q[k] = p.get(k)
        q["rayon_coin"] = q["rayon_coin"] or 0.0
        q["points"] = q["points"] or []
        q["couches"] = q["couches"] or []
        q["expansion_masque"] = q["expansion_masque"] or 0.0
        q["rotation"] = q["rotation"] or 0.0
        q["classe_isolement"] = self.cref if q["classe_isolement"] is None else q["classe_isolement"]
        q["traversante"] = bool(q["traversante"])
        q["via_dans_pastille_permis"] = True if (q["traversante"] or via_ok) else bool(q["via_dans_pastille_permis"])
        hw, hh = _demi_boite(p)
        q["hw"], q["hh"], q["th"] = _r(hw), _r(hh), q["traversante"]
        return q

    def _pas_min(self, c):
        pts = sorted((p["x"], p["y"]) for p in c["pastilles"])
        best = math.inf
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                if pts[j][0] - pts[i][0] >= best:
                    break
                d = math.hypot(pts[j][0] - pts[i][0], pts[j][1] - pts[i][1])
                if 1e-6 < d < best:
                    best = d
        return None if best == math.inf else best

    def _pas_fin(self, c, pas):
        """R-P2 et R-P2b : (pas_fin, matrice, preuve)."""
        pads = c["pastilles"]
        if pas is not None and len(pads) >= 5 and self._est_matrice(pads, pas):
            return True, True, f"matrice de billes, pas {_num(round(pas, 4))} mm"
        if pas is not None and len(pads) >= 3 and pas <= self.pas_fin + EPS:
            return True, False, f"pas minimal {_num(round(pas, 4))} mm ≤ {_num(self.pas_fin)} ({len(pads)} pastilles)"
        cls = collections.Counter(self.cref if p.get("classe_isolement") is None else p["classe_isolement"]
                                  for p in pads)
        mat, defaut = self.iso["matrice"], self.iso["defaut"]
        for a in cls:
            for b in cls:
                if (a != b or cls[a] >= 2) and mat[a][b] < defaut - EPS:
                    return True, False, (f"isolement relâché entre ses pastilles {_num(mat[a][b])} < défaut "
                                         f"{_num(defaut)}")
        return False, False, ""

    @staticmethod
    def _est_matrice(pads, pas):
        tol = TOL_MATRICE * pas
        grille = collections.defaultdict(list)
        for p in pads:
            grille[(round(p["x"] / pas), round(p["y"] / pas))].append((p["x"], p["y"]))

        def existe(x, y):
            i, j = round(x / pas), round(y / pas)
            return any(abs(a - x) <= tol and abs(b - y) <= tol
                       for di in (-1, 0, 1) for dj in (-1, 0, 1) for a, b in grille.get((i + di, j + dj), ()))
        return any(all(existe(p["x"] + dx, p["y"] + dy) for dx, dy in ((pas, 0), (-pas, 0), (0, pas), (0, -pas)))
                   for p in pads)

    def _controle_billes(self, c, pas):
        """R-P2b : refus si aucun diamètre de via permis ne tient au centre de quatre billes."""
        des = c["designateur"]
        bille = max(max(p["largeur"], p["hauteur"]) for p in c["pastilles"])
        mat = self.iso["matrice"]
        cls_pads = {self.cref if p.get("classe_isolement") is None else p["classe_isolement"] for p in c["pastilles"]}
        for n in sorted({p["reseau"] for p in c["pastilles"] if p["reseau"]}):
            r = self.reseau[n]
            via = r["via"]
            dv = via.get("diametre_min") or via["diametre"]
            requis = max(mat[r["classes_isolement"]["via"]][k] for k in cls_pads)
            if pas / math.sqrt(2) - bille / 2 - dv / 2 < requis - EPS:
                self._erreur_entree(f"matrice de billes {des} : via trop gros pour l'éventail (pas "
                                    f"{_num(round(pas, 4))}, bille {_num(round(bille, 4))}, via {_num(dv)}, "
                                    f"isolement {_num(requis)})")
        self.avert.append(f"matrice de billes {des} : éventail en os de chien non fait en vague 1 (§5.5, lot J)")

    def _faces_routables(self, c):
        """Faces où chaque pastille CMS d'un réseau qui n'est pas un plan de la face a une couche de routage."""
        nets = {p["reseau"] for p in c["pastilles"] if p["reseau"] and not p.get("traversante")}
        res = set()
        for dessous, couche in self.externes.items():
            if self.role_couche.get(couche) == "routage" or nets <= self.plans_couche.get(couche, set()):
                res.add(dessous)
        return res

    def _garde_face_fixe(self, c):
        for p in c["pastilles"]:
            if not p["reseau"] or p.get("traversante"):
                continue
            couches = p.get("couches") or []
            if couches and not any(self.role_couche.get(x) == "routage" or p["reseau"] in self.plans_couche.get(x, ())
                                   for x in couches):
                self._erreur_entree(f"pièce {c['designateur']} : pastille {p['id']} (réseau {p['reseau']}) sur une "
                                    f"face sans couche de routage ({', '.join(couches)}) (§3.1)")

    def _orientations(self, c, orients, dessus_s, dessous_s, non_ret, exceptions, conv_pieces, fpd, rp):
        """R-P5 et R-P5b : ([[rotation, dessous], …], règle)."""
        des = c["designateur"]
        if des in orients:
            return orients[des], "contrainte (orientations)"
        entree = c["face"] == "dessous"
        faces, raisons = {False, True}, []
        if des in dessous_s:
            faces, raisons = {True}, ["dessous seulement (contrainte)"]
        elif des in dessus_s:
            faces, raisons = {False}, ["dessus seulement (contrainte)"]
        elif des in conv_pieces:
            faces, raisons = {entree}, ["convertisseur : face d'entree"]
        else:
            if des in non_ret:
                faces &= {entree}
                raisons.append(f"face d'entree (gabarit : {non_ret[des]})")
            if rp.get("faces_permises"):
                fp = {f == "dessous" for f in rp["faces_permises"]}
                faces &= fp
                raisons.append("PermittedLayers")
            th = any(p.get("traversante") for p in c["pastilles"])
            h, inconnue = c.get("hauteur") or 0.0, c.get("source_hauteur") == "inconnue" or not c.get("hauteur")
            haute = False
            if not inconnue and des not in exceptions:
                for f in (False, True):
                    if self.hmax[f] is not None and h > self.hmax[f] + EPS and f in faces and len(faces) > 1:
                        faces.discard(f)
                        haute = True
                        raisons.append(f"hauteur {_num(h)} > {_num(self.hmax[f])} ({'dessous' if f else 'dessus'})")
            if th or haute:
                f = entree if self.face_encombrants == "actuelle" else False
                if f in faces:
                    faces = {f}
                    raisons.append(("traversante" if th else "haute") + f", face {'dessous' if f else 'dessus'}")
            if inconnue and self.hauteur_inconnue == "face_entree" and entree in faces:
                faces = {entree}
                raisons.append("hauteur inconnue : face d'entree")
            if fpd == "actuelle" and entree in faces:
                faces = {entree}
                raisons.append("faces par défaut : actuelle")
            if not faces:
                faces = {entree}
                self.avert.append(f"pièce {des} : aucune face permise par les règles ; face d'entrée gardée")
        routables = self._faces_routables(c)
        if not faces <= routables:
            faces &= routables
            raisons.append("face sans couche de routage exclue")
            if not faces:
                plans = self.plans_couche.get(self.externes[entree], set())
                p = next(p for p in c["pastilles"] if p["reseau"] and not p.get("traversante")
                         and p["reseau"] not in plans)
                self._erreur_entree(f"pièce {des} : pastille {p['id']} (réseau {p['reseau']}) : aucune face avec "
                                    f"une couche de routage (§3.1)")
        rots = list(ROTATIONS)
        rots_dessous = list(ROTATIONS) if len(c["pastilles"]) <= 2 or des in conv_pieces else list(self.rot_dessous)
        if rp.get("rotations_permises"):
            rp_ = [_angle(a) for a in rp["rotations_permises"]]
            rots = [a for a in rots if a in rp_] or rp_
            rots_dessous = [a for a in rots_dessous if a in rp_] or rp_
            raisons.append("ComponentOrientations")
        res = [[a, False] for a in rots if False in faces] + [[a, True] for a in rots_dessous if True in faces]
        return res, "; ".join(raisons) if raisons else "libre"

    def _couches_posees(self, c, p, dessous):
        """Couches de la pastille ``p`` de la pièce ``c`` posée sur la face ``dessous`` : une pièce retournée par
        rapport à l'entrée échange ses deux couches externes (les couches internes ne changent pas)."""
        couches = set(p.get("couches") or [])
        if bool(dessous) == (c.get("face") == "dessous") or not self.externes:
            return couches
        h, b = self.externes[False], self.externes[True]
        return {b if x == h else h if x == b else x for x in couches}

    def _controle_deplace(self, des):
        """§3.5 : une pièce de fixes_deplaces contre les autres pièces fixes (chevauchement, pastilles, contour)."""
        m = self.composant[des]
        c = self.tous[des]
        ch = f"fixes_deplaces.{des}"
        boite = _boite_monde(m["lbb"], m["pose_cible"])
        if self.contour and not all(G.point_dans_polygone(q, self.contour) for q in boite):
            self.C.erreur(ch, f"pièce {des} : boîte hors du contour à la pose imposée")
        th_m = any(p["traversante"] for p in m["pastilles"])
        pads_m = self._pads_monde(c, m["pose_cible"])
        bm = G.boite([q for _, poly in pads_m for q in poly] or boite)
        marge = max(max(r) for r in self.iso["matrice"])
        for o in self.composants:
            if o["nom"] == des or o["statut"] != "fixe":
                continue
            th_o = any(p["traversante"] for p in o["pastilles"])
            if o["pose_cible"][3] == m["pose_cible"][3] or th_m or th_o:
                bo = _boite_monde(o["lbb"], o["pose_cible"])
                if _chevauchement(boite, bo):
                    self.C.erreur(ch, f"conflit entre {des} et la pièce fixe {o['nom']} (boîtes qui se chevauchent)")
            pads_o = self._pads_monde(self.tous[o["nom"]], o["pose_cible"])
            bb = G.boite([q for _, poly in pads_o for q in poly])
            if bb is None or (bb[0] > bm[2] + marge or bm[0] > bb[2] + marge or bb[1] > bm[3] + marge
                              or bm[1] > bb[3] + marge):
                continue
            for p, pm in pads_m:
                for q, po in pads_o:
                    if not (self._couches_posees(c, p, m["pose_cible"][3])
                            & self._couches_posees(self.tous[o["nom"]], q, o["pose_cible"][3])):
                        continue
                    if p["reseau"] and p["reseau"] == q["reseau"]:
                        continue
                    ci = self.cref if p.get("classe_isolement") is None else p["classe_isolement"]
                    cj = self.cref if q.get("classe_isolement") is None else q["classe_isolement"]
                    requis = self.iso["matrice"][ci][cj]
                    dist = _distance_polygones(pm, po)
                    if dist < requis - 1e-4:
                        self.C.erreur(ch, f"conflit entre {des} et la pièce fixe {o['nom']} (pastilles {p['id']} et "
                                          f"{q['id']} à {_num(round(dist, 4))} mm < {_num(requis)})")

    # --- données de la carte ---
    def _carte(self):
        C, ct, carte = self.C, self.C.ct, self.carte
        fills = [f for f in carte.get("remplissages") or [] if f.get("reseau")
                 and self.role_couche.get(f["couche"]) == "routage"]
        self.carte_d = {"contour": self.contour, "boite": carte["boite"],
                        "decoupes": [{"id": d["id"], "points": d["points"], "proprietaire": d.get("proprietaire")}
                                     for d in carte.get("decoupes") or []],
                        "remplissages": [{"couche": f["couche"], "reseau": f["reseau"], "contour": f["contour"],
                                          "trous": f.get("trous") or []} for f in fills],
                        "marge_bord_mm": self.marge_bord, "marge_decoupe_mm": self.marge_decoupe,
                        "garde_bord_mm": self.garde_bord, "garde_decoupe_mm": self.garde_decoupe}
        for f in fills:
            r = self.reseau[f["reseau"]]
            if f["couche"] not in r["remplissages"]:
                r["remplissages"].append(f["couche"])
        # trous
        self.trous = []

        def rayon(d, dc, propre=None):
            if propre is not None:
                return propre
            if self.rayon_trou is not None:
                return self.rayon_trou
            return _r(max(d, dc) / 2 + MARGE_TROU)
        for t in carte.get("trous") or []:
            self.trous.append({"id": t["id"], "x": t["x"], "y": t["y"], "rotation": t.get("rotation", 0.0),
                               "trou": t["trou"], "metallise": t.get("metallise", False),
                               "diametre_cuivre": t.get("diametre_cuivre", 0.0),
                               "rayon_interdit_mm": rayon(t["trou"]["diametre"], t.get("diametre_cuivre", 0.0)),
                               "classe_isolement": t.get("classe_isolement", self.cref), "piece": None})
        k = len(self.trous)
        for i, s in enumerate(C.liste("trous_supplementaires", ct.get("trous_supplementaires", []))):
            ch = f"trous_supplementaires[{i}]"
            C.cles(ch, s, ("x", "y", "diametre", "diametre_cuivre", "metallise", "rayon_interdit_mm"),
                   ("x", "y", "diametre"))
            xy = C.point(ch, [s["x"], s["y"]])
            d = C.longueur(f"{ch}.diametre", s["diametre"])
            dc = C.longueur(f"{ch}.diametre_cuivre", s["diametre_cuivre"]) if "diametre_cuivre" in s else 0.0
            met = C.booleen(f"{ch}.metallise", s["metallise"]) if "metallise" in s else False
            ri = C.nombre(f"{ch}.rayon_interdit_mm", s["rayon_interdit_mm"], 0.0) if "rayon_interdit_mm" in s else None
            k += 1
            self.trous.append({"id": f"T{k}", "x": xy[0], "y": xy[1], "rotation": 0.0,
                               "trou": {"diametre": d, "forme": "rond", "longueur": 0.0, "rotation": 0.0,
                                        "decalage": [0.0, 0.0], "metallise": met},
                               "metallise": met, "diametre_cuivre": dc, "rayon_interdit_mm": rayon(d, dc, ri),
                               "classe_isolement": self.cref, "piece": None})
        for m in self.composants:
            c = self.tous[m["nom"]]
            if m["statut"] != "fixe" or not (c["genre"] == "mecanique" or m["virtuel"]):
                continue
            pd = _pose_dict(m["pose_cible"])
            for p in c["pastilles"]:
                t = p.get("trou")
                if not t or t.get("diametre", 0.0) < TROU_PIECE_MIN - EPS:
                    continue
                dx, dy = G.tourner(t.get("decalage") or [0.0, 0.0], p.get("rotation") or 0.0)
                x, y = G.local_vers_monde([p["x"] + dx, p["y"] + dy], pd)
                sens = -1 if pd["face"] == "dessous" else 1
                rot = (pd["rotation"] + sens * (p.get("rotation") or 0.0)) % 360
                dc = max(p["largeur"], p["hauteur"])
                dc = dc if dc > t["diametre"] + EPS else 0.0
                k += 1
                self.trous.append({"id": f"T{k}", "x": _r(x), "y": _r(y), "rotation": _r(rot), "trou": t,
                                   "metallise": bool(t.get("metallise")), "diametre_cuivre": dc,
                                   "rayon_interdit_mm": rayon(t["diametre"], dc),
                                   "classe_isolement": self.cref if p.get("classe_isolement") is None
                                   else p["classe_isolement"], "piece": m["nom"]})
        # obstacles de placement
        self.obstacles = []
        self.zri = []
        for i, z in enumerate(C.liste("zones_interdites", ct.get("zones_interdites", []))):
            ch = f"zones_interdites[{i}]"
            C.cles(ch, z, ("faces", "rect", "polygone", "effets", "couches", "etiquette"))
            genre, val, poly = C.forme(ch, z)
            faces = C.faces(f"{ch}.faces", z["faces"]) if "faces" in z else ["dessus", "dessous"]
            effets = C.liste(f"{ch}.effets", z.get("effets", ["placement"]))
            for j, e in enumerate(effets):
                C.choix(f"{ch}.effets[{j}]", e, ("placement", "pistes", "vias"))
            etiq = C.texte(f"{ch}.etiquette", z["etiquette"]) if "etiquette" in z else f"zone interdite {i + 1}"
            if "placement" in effets:
                self.obstacles.append({"faces": faces, genre: val, "etiquette": etiq})
            routage = [e for e in effets if e in ("pistes", "vias")]
            if routage:
                couches = C.couches_liste(f"{ch}.couches", z["couches"]) if "couches" in z else ["toutes"]
                self.zri.append({"id": f"U{i + 1}", "couches": couches, "forme": "polygone", "points": poly,
                                 "largeur": 0.0, "effets": routage, "etiquette": etiq})
        for i, z in enumerate(C.liste("zones_autour", ct.get("zones_autour", []))):
            ch = f"zones_autour[{i}]"
            C.cles(ch, z, ("empreinte_prefixe", "designateurs", "face", "demi_cote_mm"), ("face", "demi_cote_mm"))
            if ("empreinte_prefixe" in z) == ("designateurs" in z):
                C.erreur(ch, "« empreinte_prefixe » ou « designateurs » attendu (un seul des deux)")
            face = C.choix(f"{ch}.face", z["face"], ("dessus", "dessous"))
            s = C.nombre(f"{ch}.demi_cote_mm", z["demi_cote_mm"], 0.0, True)
            if "designateurs" in z:
                cibles = set(C.pieces(f"{ch}.designateurs", z["designateurs"])[0])
            else:
                pre = C.texte(f"{ch}.empreinte_prefixe", z["empreinte_prefixe"])
                cibles = {d for d in self.ordre if self.comps[d]["empreinte"].startswith(pre)}
            n = 0
            for m in self.composants:
                if m["statut"] != "fixe" or m["nom"] not in cibles:
                    continue
                b = G.boite(_boite_monde(m["lbb"], m["pose_cible"]))
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                self.obstacles.append({"faces": [face], "rect": [_r(cx - s), _r(cy - s), _r(cx + s), _r(cy + s)],
                                       "etiquette": f"zone autour de {m['nom']}"})
                n += 1
            if not n:
                self.avert.append(f"contraintes.{ch} : aucune pièce fixe concernée (sans effet)")
        for z in carte.get("zones_interdites") or []:
            couches = z.get("couches") or ["toutes"]
            if "placement" in (z.get("effets") or []):
                if couches == ["toutes"]:
                    faces = ["dessus", "dessous"]
                else:
                    faces = [f for f, n in (("dessus", self.externes.get(False)), ("dessous", self.externes.get(True)))
                             if n in couches]
                if faces:
                    if z.get("forme") == "polyligne":
                        for poly in G.polyligne_epaisse(z["points"], z.get("largeur") or 0.0):
                            self.obstacles.append({"faces": faces, "polygone": poly,
                                                   "etiquette": f"keep-out {z['id']}"})
                    else:
                        self.obstacles.append({"faces": faces, "polygone": z["points"],
                                               "etiquette": f"keep-out {z['id']}"})
            routage = [e for e in z.get("effets") or [] if e in ("pistes", "vias", "remplissages")]
            if routage:
                self.zri.append({"id": z["id"], "couches": couches, "forme": z.get("forme", "polygone"),
                                 "points": z["points"], "largeur": z.get("largeur") or 0.0, "effets": routage,
                                 "etiquette": f"keep-out {z['id']}"})
        # zones imposées et salles
        self.zones_imposees = []
        for i, z in enumerate(C.liste("zones_imposees", ct.get("zones_imposees", []))):
            ch = f"zones_imposees[{i}]"
            C.cles(ch, z, ("pieces", "faces", "rect", "polygone", "etiquette"), ("pieces",))
            _, _, poly = C.forme(ch, z)
            pieces = C.pieces(f"{ch}.pieces", z["pieces"])[0]
            faces = C.faces(f"{ch}.faces", z["faces"]) if "faces" in z else ["dessus", "dessous"]
            etiq = C.texte(f"{ch}.etiquette", z["etiquette"]) if "etiquette" in z else f"zone imposée {i + 1}"
            self.zones_imposees.append({"pieces": pieces, "faces": faces, "polygone": poly, "etiquette": etiq,
                                        "source": "contrainte"})
        if self.salles_zones:
            for s in self.rg.get("salles") or []:
                poly = s.get("polygone") or []
                if len(poly) < 3 or not all(G.point_dans_polygone(q, self.contour) or
                                            G.distance_point_polygone(q, self.contour) < 1e-6 for q in poly):
                    self.avert.append(f"salle {s.get('nom')} : pas entièrement dans le contour, non retenue comme zone")
                    continue
                pieces = [d for d in s.get("pieces") or [] if d in self.comps]
                face = s.get("face")
                faces = [face] if face in ("dessus", "dessous") else ["dessus", "dessous"]
                self.zones_imposees.append({"pieces": pieces, "faces": faces, "polygone": G.orienter(poly),
                                            "etiquette": s.get("nom", ""), "source": "salle"})
        # cuivre fixe
        self.cuivre = []
        k = 0

        def classe(n, genre, o):
            if o.get("classe_isolement") is not None:
                return o["classe_isolement"]
            r = self.reseau.get(n)
            return r["classes_isolement"][genre] if r else self.cref
        for o in carte.get("obstacles_cuivre") or []:
            k += 1
            self.cuivre.append({"id": f"F{k}", "type": "region", "reseau": "", "couches": o.get("couches") or [],
                                "points": o["points"], "trous": [], "classe_isolement": classe("", "surface", o)})
        retenus = set()
        for i, g in enumerate(C.liste("cuivre_a_garder", ct.get("cuivre_a_garder", []))):
            ch = f"cuivre_a_garder[{i}]"
            C.cles(ch, g, ("couches", "reseaux", "zone", "verrouilles_seulement"))
            couches = C.couches_liste(f"{ch}.couches", g["couches"]) if "couches" in g else ["toutes"]
            nets = C.reseaux_motifs(f"{ch}.reseaux", g["reseaux"]) if "reseaux" in g else None
            zone = None
            if g.get("zone") is not None:
                z = g["zone"]
                if isinstance(z, dict):
                    C.cles(f"{ch}.zone", z, ("rect", "polygone"))
                    zone = C.forme(f"{ch}.zone", z)[2]
                elif isinstance(z, list) and len(z) == 4 and not isinstance(z[0], list):
                    zone = _rect_poly(C.rect(f"{ch}.zone", z))
                else:
                    zone = C.polygone(f"{ch}.zone", z)
            verr = C.booleen(f"{ch}.verrouilles_seulement", g["verrouilles_seulement"]) \
                if "verrouilles_seulement" in g else False
            n_avant = len(retenus)
            for e in carte.get("cuivre_existant") or []:
                if e["id"] in retenus or (verr and not e.get("verrouille")):
                    continue
                if nets is not None and e.get("reseau") not in nets:
                    continue
                if couches != ["toutes"] and not set(e.get("couches") or []) & set(couches):
                    continue
                pts = e.get("points") or ([[e["x1"], e["y1"]], [e["x2"], e["y2"]]] if "x1" in e
                                         else [[e["x"], e["y"]]] if "x" in e else [])
                if zone is not None and not all(G.point_dans_polygone(q, zone) for q in pts):
                    continue
                retenus.add(e["id"])
                n = e.get("reseau") or ""
                if e["type"] == "piste":
                    k += 1
                    self.cuivre.append({"id": f"F{k}", "type": "piste", "reseau": n, "couches": e["couches"],
                                        "x1": e["x1"], "y1": e["y1"], "x2": e["x2"], "y2": e["y2"],
                                        "largeur": e["largeur"], "classe_isolement": classe(n, "piste", e)})
                elif e["type"] == "via":
                    k += 1
                    self.cuivre.append({"id": f"F{k}", "type": "via", "reseau": n, "couches": e["couches"],
                                        "x": e["x"], "y": e["y"], "diametre": e["diametre"], "trou": e["trou"],
                                        "classe_isolement": classe(n, "via", e)})
                else:
                    k += 1
                    self.cuivre.append({"id": f"F{k}", "type": "region", "reseau": n, "couches": e["couches"],
                                        "points": e["points"], "trous": e.get("trous") or [],
                                        "classe_isolement": classe(n, "surface", e)})
            if len(retenus) == n_avant:
                self.avert.append(f"contraintes.{ch} : aucun cuivre existant retenu (sans effet)")
        # découplages (R-P7)
        ms = set(self.masses)
        self.decouplages = []
        for m in self.composants:
            if m["virtuel"] or m["nature"] != "condensateur" or len(m["pastilles"]) != 2:
                continue
            a, b = m["pastilles"]
            if a["reseau"] in ms and b["reseau"] and b["reseau"] not in ms:
                p = b
            elif b["reseau"] in ms and a["reseau"] and a["reseau"] not in ms:
                p = a
            else:
                continue
            ci = [q["id"] for o in self.composants if not o["virtuel"] and o["nom"] != m["nom"]
                  and len(o["pastilles"]) >= 4 for q in o["pastilles"] if q["reseau"] == p["reseau"]]
            if ci:
                self.decouplages.append({"comp": m["nom"], "pastille": p["id"], "reseau": p["reseau"],
                                         "broches_ci": ci})
                self.el(f"piece:{m['nom']}", "decouplage", p["reseau"], "R-P7", f"{len(ci)} broche(s) de circuit")

    # --- groupes (§3.6) ---
    def _groupes(self):
        C, ct = self.C, self.C.ct
        mobile = {m["nom"] for m in self.composants if m["statut"] == "mobile"}
        deduits = []
        # fonctionnels
        pris = set()
        for d in self.ordre:
            if d not in mobile or self.natures[d] != "circuit" or self.K.npads[d] < 3:
                continue
            nets = {n for n in self.K.nets[d] if self.reseau[n]["role"] not in ROLES_HORS_GROUPE
                    and self.K.taille[n] <= BROCHES_GROUPE}
            membres = [x for x in self.ordre if x != d and x in mobile and x not in pris and self.K.npads[x] == 2
                       and self.natures[x] in NATURES_GROUPE and self.K.nets[x] & nets]
            if membres:
                pris |= set(membres)
                deduits.append({"type": "fonctionnel", "nom": f"fonctionnel_{d}", "pieces": [d] + membres,
                                "rigide": False})
        # répétés : hiérarchie
        canal = {d: self.comps[d]["canal"] for d in self.ordre if self.comps[d].get("canal")}
        inst_de_groupe = collections.defaultdict(set)
        for d, cn in canal.items():
            inst_de_groupe[cn["groupe"]].add(cn["instance"])
        parent = {}

        def trouver(x):
            while parent.setdefault(x, x) != x:
                x = parent[x]
            return x
        for insts in inst_de_groupe.values():
            insts = sorted(insts)
            for x in insts[1:]:
                parent[trouver(x)] = trouver(insts[0])
        familles = collections.defaultdict(set)
        for d, cn in canal.items():
            familles[trouver(cn["instance"])].add(cn["instance"])
        en_hierarchie = set()
        for k, insts in enumerate(sorted(familles.values(), key=lambda s: _cle_naturelle(min(s)))):
            insts = sorted(insts, key=_cle_naturelle)
            pieces = {i: [d for d in self.ordre if d in canal and canal[d]["instance"] == i] for i in insts}
            if len(insts) < 2 or any(len(p) < PIECES_EXEMPLAIRE for p in pieces.values()):
                continue
            noms_ex = [_nom_instance(i) for i in insts]
            base = os.path.commonprefix(noms_ex).rstrip("0123456789_-/ .") or f"repete_{k + 1}"
            lignes = collections.OrderedDict()
            for d in self.ordre:
                if d in canal and canal[d]["instance"] in pieces:
                    lignes.setdefault(canal[d]["groupe"], {}).setdefault(canal[d]["instance"], []).append(d)
            corr = [[v[i][0] for i in insts] for v in lignes.values()
                    if len(v) == len(insts) and all(len(x) == 1 for x in v.values())]
            groupe = {"type": "repete", "nom": "_" + base, "famille": "_" + base, "source": "hierarchie", "rigide": False,
                      "exemplaires": [{"nom": n, "pieces": pieces[i]} for n, i in zip(noms_ex, insts)],
                      "correspondance_pieces": corr, "correspondance_reseaux": self._corr_reseaux(corr)}
            deduits.append(groupe)
            for p in pieces.values():
                en_hierarchie |= set(p)
        # répétés : motif des désignateurs
        par_famille = collections.defaultdict(lambda: collections.defaultdict(list))
        for d in self.ordre:
            if d in en_hierarchie or d.startswith("__libre_"):
                continue
            mt = self.motif_canaux.match(d)
            if not mt or len(mt.groups()) < 2:
                continue
            base, suffixe = mt.group(1), mt.group(2)
            par_famille[re.sub(r"\d+$", "", suffixe)][suffixe].append((base, d))
        for fam in sorted(par_famille):
            sfx = par_famille[fam]
            signatures = collections.defaultdict(list)
            for s, membres in sfx.items():
                cle = tuple(sorted(collections.Counter((b, self.comps[d]["empreinte"]) for b, d in membres).items()))
                signatures[cle].append(s)
            cle, suff = max(signatures.items(), key=lambda kv: (len(kv[1]), len(kv[0])))
            suff = sorted(suff, key=_cle_naturelle)
            if len(suff) < 2 or any(len(sfx[s]) < PIECES_EXEMPLAIRE for s in suff):
                continue
            nom = fam  # nom = famille (D-B72) : groupes_exclus agit par le nom
            par_base = [dict(sfx[s]) for s in suff]
            corr = [[pb[b] for pb in par_base] for b, _ in sfx[suff[0]] if all(b in pb for pb in par_base)
                    and all(list(x[0] for x in sfx[s]).count(b) == 1 for s in suff)]
            deduits.append({"type": "repete", "nom": nom, "famille": fam, "source": "motif", "rigide": False,
                            "exemplaires": [{"nom": s.lstrip("_"), "pieces": [d for _, d in sfx[s]]} for s in suff],
                            "correspondance_pieces": corr, "correspondance_reseaux": self._corr_reseaux(corr)})
        # exclusions et groupes imposés
        exclus = C.liste("groupes_exclus", ct.get("groupes_exclus", []))
        noms = {g["nom"] for g in deduits}
        for i, n in enumerate(exclus):
            if C.texte(f"groupes_exclus[{i}]", n) not in noms:
                C.avert.append(f"contraintes.groupes_exclus[{i}] : groupe {n} inconnu (sans effet)")
        self.groupes = [g for g in deduits if g["nom"] not in set(exclus)]
        for g in self.groupes:
            if g["type"] == "repete":
                tailles = ", ".join(str(len(e["pieces"])) for e in g["exemplaires"])
                self.el(f"groupe:{g['nom']}", "type", "repete", "§3.6",
                        f"{len(g['exemplaires'])} exemplaires ({tailles} pièces), source {g['source']}")
            else:
                self.el(f"groupe:{g['nom']}", "type", g["type"], "§3.6", f"{len(g['pieces'])} pièces")
        noms = {g["nom"] for g in self.groupes}
        for i, g in enumerate(C.liste("groupes", ct.get("groupes", []))):
            ch = f"groupes[{i}]"
            C.cles(ch, g, ("nom", "pieces", "rigide"), ("nom", "pieces"))
            nom = C.texte(f"{ch}.nom", g["nom"])
            if nom in noms:
                C.erreur(f"{ch}.nom", f"nom {nom} déjà pris (groupe déduit à exclure par groupes_exclus, ou en double)")
            noms.add(nom)
            pieces = [C.piece(f"{ch}.pieces[{j}]", d) for j, d in enumerate(C.liste(f"{ch}.pieces", g["pieces"]))]
            if not pieces:
                C.erreur(f"{ch}.pieces", "liste non vide attendue")
            rig = C.booleen(f"{ch}.rigide", g["rigide"]) if "rigide" in g else False
            self.groupes.append({"type": "impose", "nom": nom, "pieces": pieces, "rigide": rig})
            self.el(f"groupe:{nom}", "type", "impose", "§3.6", f"{len(pieces)} pièces", "contrainte")

    def _corr_reseaux(self, corr):
        """Pour chaque réseau de l'exemplaire 1, ses réseaux dans les autres (paires cohérentes seulement)."""
        if not corr:
            return []
        k = len(corr[0])
        vus = collections.OrderedDict()
        for ligne in corr:
            pads = [{p["id"].split("#")[-1]: p["reseau"] for p in self.comps[d]["pastilles"]} for d in ligne]
            for j, n1 in pads[0].items():
                if not n1:
                    continue
                vus.setdefault(n1, [set() for _ in range(k)])
                for m in range(k):
                    vus[n1][m].add(pads[m].get(j, ""))
        res = []
        for n1, ens in vus.items():
            if all(len(e) == 1 and "" not in e for e in ens):
                res.append([next(iter(e)) for e in ens])
        cibles = collections.Counter(tuple(x[1:]) for x in res)
        return [x for x in res if cibles[tuple(x[1:])] == 1]

    # --- éloignements, charges, au_bord ---
    def _objet(self, err, ch, s):
        if not isinstance(s, str):
            err(ch, "texte attendu")
        if s in self.tous:
            return [s]
        if "#" in s:
            d, _, k = s.rpartition("#")
            if d in self.tous and any(p["id"] == s for p in self.tous[d]["pastilles"]):
                return [s]
            err(ch, f"pastille {s} inconnue")
        d, _, b = s.rpartition(".")
        if d not in self.tous:
            err(ch, f"pièce {d or s} inconnue")
        ids = [p["id"] for p in self.tous[d]["pastilles"] if p["nom"] == b]
        if not ids:
            err(ch, f"broche {b} inconnue sur la pièce {d}")
        if len(ids) > 1:
            self.avert.append(f"{ch} : {s} désigne {len(ids)} pastilles ({', '.join(ids)}) ; toutes sont retenues")
        return ids

    def _liens(self):
        C, ct = self.C, self.C.ct
        self.eloignements = []
        nom_cfg = self.noms.get("config", "config")
        def err_config(ch, msg):
            raise ErreurFormat(f"{nom_cfg} : {ch} : {msg}")
        sources = [(self.cfg.get("eloignements") or [], err_config),
                   (C.liste("eloignements", ct.get("eloignements", [])), None)]
        for liste, err_cfg in sources:
            for i, x in enumerate(liste):
                ch = f"eloignements[{i}]"
                err = err_cfg or C.erreur
                if not isinstance(x, dict):
                    err(ch, "objet attendu")
                if err_cfg is None:
                    C.cles(ch, x, ("type", "objets", "reference", "distance_mm", "poids_par_mm", "marge_mm"),
                           ("type", "objets", "reference", "distance_mm"))
                t = x.get("type")
                if t not in ("dure", "souple"):
                    err(f"{ch}.type", f"{t!r} inconnu (permis : dure, souple)")
                objets = []
                if not isinstance(x.get("objets"), list) or not x["objets"]:
                    err(f"{ch}.objets", "liste non vide attendue")
                for j, s in enumerate(x["objets"]):
                    objets += [o for o in self._objet(err, f"{ch}.objets[{j}]", s) if o not in objets]
                ref = x.get("reference")
                if not isinstance(ref, list) or not ref:
                    err(f"{ch}.reference", "liste non vide attendue")
                for j, d in enumerate(ref):
                    if d not in self.tous:
                        err(f"{ch}.reference[{j}]", f"pièce {d} inconnue")
                dist = C.nombre(f"{ch}.distance_mm", x.get("distance_mm"), 0.0, True)
                poids = C.nombre(f"{ch}.poids_par_mm", x["poids_par_mm"], 0.0) if "poids_par_mm" in x \
                    else (300.0 if t == "dure" else 5.0)
                marge = C.nombre(f"{ch}.marge_mm", x["marge_mm"], 0.0) if "marge_mm" in x \
                    else (1.0 if t == "dure" else 0.0)
                self.eloignements.append({"type": t, "objets": objets, "reference": list(ref), "distance_mm": dist,
                                          "poids_par_mm": poids, "marge_mm": marge})
        self.charges = []
        for i, x in enumerate(C.liste("charges", ct.get("charges", []))):
            ch = f"charges[{i}]"
            C.cles(ch, x, ("broche", "courant_a"), ("broche", "courant_a"))
            s = C.texte(f"{ch}.broche", x["broche"])
            ids = self._objet(C.erreur, f"{ch}.broche", s)
            if ids == [s] and "#" not in s:
                C.erreur(f"{ch}.broche", f"« PIECE.BROCHE » attendu ({s} est une pièce)")
            self.charges.append({"broche": s, "pastilles": ids,
                                 "courant_a": C.nombre(f"{ch}.courant_a", x["courant_a"], 0.0)})
        self.au_bord = []
        for i, x in enumerate(C.liste("au_bord", ct.get("au_bord", []))):
            ch = f"au_bord[{i}]"
            C.cles(ch, x, ("piece", "cote", "debord_mm"), ("piece", "cote"))
            self.au_bord.append({"piece": C.piece(f"{ch}.piece", x["piece"]),
                                 "cote": C.choix(f"{ch}.cote", x["cote"], COTES),
                                 "debord_mm": C.nombre(f"{ch}.debord_mm", x["debord_mm"], 0.0)
                                 if "debord_mm" in x else 0.0})

    # --- assemblage ---
    def executer(self):
        self._config()
        self._parametres()
        self._couches()
        self._masses()
        self._paires()
        self._reseaux()
        self._composants()
        self._carte()
        self._groupes()
        self._liens()
        return self._donnees(), self._deduction()

    def _donnees(self):
        rg, n = self.rg, self.noms
        sha = n.get("sha256_entree") or hashlib.sha256(
            json.dumps(self.e, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        cp = rg.get("connexion_plans") or {"isolement": self.iso["defaut"], "connexion": "relief"}
        return {
            "format": FORMAT,
            "source": {"entree": n.get("entree", "entree_carte.json"), "sha256_entree": sha,
                       "contraintes": n.get("contraintes", "contraintes.json") if self.C.ct else None,
                       "config": n.get("config", "config_carte.json"),
                       "controle_gabarit": n.get("controle_gabarit", "controle_gabarit.json") if self.gab else None,
                       "construit_par": f"construire_donnees {VERSION}"},
            "unite": "mm", "carte": self.carte_d, "couches": self.couches, "trous": self.trous,
            "obstacles_placement": self.obstacles, "zones_imposees": self.zones_imposees,
            "zones_routage_interdites": self.zri, "cuivre_fixe": self.cuivre, "composants": self.composants,
            "reseaux": self.reseaux,
            "regles": {"isolement": self.iso, "via": dict(rg.get("via") or VIA_DEFAUT),
                       "trou_trou_mm": rg.get("trou_trou") if rg.get("trou_trou") is not None else TROU_TROU_DEFAUT,
                       "garde_trou_mm": self.garde_trou,
                       "placement": {"grille_mm": self.grille, "marge_piece_mm": self.marge_piece,
                                     "marge_traversante_mm": self.marge_traversante},
                       "via_dans_pastille_meme_reseau": self.via_meme_reseau,
                       "connexion_plans": {"isolement": cp.get("isolement"), "connexion": cp.get("connexion")}},
            "convertisseurs": self.convs, "sans_piste_dessous": self.sans_piste, "decouplages": self.decouplages,
            "eloignements": self.eloignements, "charges": self.charges, "groupes": self.groupes,
            "paires": self.paires, "au_bord": self.au_bord, "masses": self.masses,
            "parametres_deduction": {
                "pas_fin_mm": self.pas_fin, "faces_par_defaut": self.faces_defaut,
                "faces_par_defaut_effectif": self.faces_effectif, "hauteur_max_dessus_mm": self.hmax[False],
                "hauteur_max_dessous_mm": self.hmax[True], "hauteur_inconnue": self.hauteur_inconnue,
                "face_encombrants": self.face_encombrants, "fixer_suggestions": self.fixer_suggestions,
                "fixer_verrouilles": self.fixer_verrouilles, "salles_comme_zones": self.salles_zones,
                "couche_preferee_par_nom": self.r_c6, "motif_canaux": self.motif_texte,
                "rotations_dessous_multibroches": self.rot_dessous, "repere": self.C.repere, "unite": self.C.unite}}

    def _deduction(self):
        return {"format": FORMAT_DEDUCTION, "elements": self.elements, "suggestions": self.suggestions,
                "a_confirmer": self.a_confirmer, "avertissements": list(dict.fromkeys(self.C.avert + self.avert)),
                "positions_imposees": self.positions}


def _nom_instance(i):
    """Nom d'un exemplaire : dernier élément non vide d'un chemin de feuille KiCad, sinon l'instance."""
    parties = [x for x in re.split(r"[/\\]", str(i)) if x]
    return parties[-1] if parties else str(i)


def _chevauchement(a, b):
    """Vrai si les deux boîtes (polygones convexes) se recouvrent sur une aire non nulle (contact exclu)."""
    def reduire(p):
        cx, cy = sum(q[0] for q in p) / len(p), sum(q[1] for q in p) / len(p)
        return [[cx + (q[0] - cx) * (1 - 1e-7), cy + (q[1] - cy) * (1 - 1e-7)] for q in p]
    if G.aire(a) < EPS or G.aire(b) < EPS:
        return False
    return G.polygones_se_touchent(reduire(a), reduire(b))


# Schéma des champs de ``entree_carte/2`` (§2.2) que lit ``construire_donnees`` : tous obligatoires (§2.1), sauf les
# clés préfixées « ~ » (champs que l'importeur n'écrit que lorsqu'il les connaît, D-B55, ou hors des tables de §2.2).
# Types : "texte", "nombre", "positif" (≥ 0), "entier" (≥ 0), "booleen", "point", "boite" (4 nombres), "objet",
# "largeurs" ([min, préférée, max|null]) ; ("?", s) : s ou null ; ("liste", s[, n]) : liste d'au moins n éléments s ;
# ("dict", s) : objet dont chaque valeur est s ; ("choix", valeurs) ; ("selon", clé, {valeur: champs}, communs) :
# champs propres à chaque valeur de la clé ; un objet {clé: s} (clés supplémentaires permises, §0.5).
_POLY = ("liste", "point", 3)
_TROU = {"diametre": "positif", "forme": ("choix", ("rond", "carre", "oblong")), "longueur": "nombre",
         "rotation": "nombre", "decalage": "point", "metallise": "booleen"}
_CLASSE = ("?", "entier")
_PASTILLE = {"id": "texte", "nom": "texte", "reseau": "texte", "x": "nombre", "y": "nombre",
             "forme": ("choix", DC.FORMES), "largeur": "positif", "hauteur": "positif", "rotation": "nombre",
             "rayon_coin": "positif", "points": ("liste", "point"), "couches": ("liste", "texte"),
             "face": ("choix", ("composant", "opposee", "toutes")), "trou": ("?", _TROU), "traversante": "booleen",
             "expansion_masque": "nombre", "pile": ("?", "objet"), "via_dans_pastille_permis": "booleen",
             "classe_isolement": _CLASSE}
_PASTILLE_LIBRE = dict(_PASTILLE, face=("choix", ("dessus", "dessous", "toutes")))
_ZONE = {"id": "texte", "couches": ("liste", "texte", 1), "forme": ("choix", ("polygone", "polyligne")),
         "points": ("liste", "point", 2), "~largeur": "positif",
         "effets": ("liste", ("choix", ("pistes", "vias", "remplissages", "pastilles", "placement"))),
         "~source": "texte"}
_COMPOSANT = {
    "designateur": "texte", "designateur_source": "texte", "empreinte": "texte", "description": "texte",
    "reference": "texte", "valeur": "texte", "genre": ("choix", DC.GENRES), "classes_composants": ("liste", "texte"),
    "canal": ("?", {"groupe": "texte", "instance": "texte"}), "hauteur": "positif", "source_hauteur": "texte",
    "x": "nombre", "y": "nombre", "rotation": "nombre", "face": ("choix", ("dessus", "dessous")),
    "verrouille": "booleen", "boite": "boite", "pastilles": ("liste", _PASTILLE),
    "regions": ("liste", {"id": "texte", "couches": ("liste", "texte"), "pastille": ("?", "texte"),
                          "reseau": "texte", "liaison": ("?", ("liste", "texte")), "points": _POLY,
                          "classe_isolement": _CLASSE}),
    "vias": ("liste", {"id": "texte", "x": "nombre", "y": "nombre", "diametre": "positif", "trou": "positif",
                       "reseau": "texte", "classe_isolement": _CLASSE}),
    "zones_interdites": ("liste", _ZONE), "decoupes": ("liste", "texte")}
_VIA_REGLE = {"diametre": "positif", "trou": "positif", "diametre_min": "positif", "diametre_max": ("?", "positif"),
              "trou_min": "positif", "trou_max": ("?", "positif")}
_REGLES = {
    "isolement": {"defaut": "positif", "classes": ("liste", "texte", 1), "matrice": ("liste", ("liste", "positif")),
                  "classe_reference": "entier"},
    "reseaux": ("dict", {"largeurs": "largeurs", "via": _VIA_REGLE,
                         "classes_isolement": {"piste": "entier", "via": "entier", "surface": "entier"},
                         "couches_permises": ("?", ("liste", "texte")), "priorite_routage": "nombre",
                         "alimentation_schema": "booleen", "regle_largeur_propre": "booleen"}),
    "largeur_defaut": "largeurs", "via": _VIA_REGLE, "trou_trou": ("?", "positif"), "garde_bord": ("?", "positif"),
    "isolement_pieces": ("?", "positif"), "hauteurs": {"dessus": ("?", "positif"), "dessous": ("?", "positif")},
    "pieces": ("dict", {"~faces_permises": ("?", ("liste", ("choix", ("dessus", "dessous")))),
                        "~rotations_permises": ("?", ("liste", "nombre")), "~hauteur_max": ("?", "positif")}),
    "connexion_plans": ("?", {"isolement": "positif", "connexion": ("choix", ("relief", "direct"))}),
    "salles": ("liste", {"nom": "texte", "pieces": ("liste", "texte"), "polygone": ("liste", "point"),
                         "face": ("?", "texte")}),
    "tolerances_longueur": ("dict", "positif")}
_ENTREE = {
    "source": {"type": ("choix", ("altium_pcbdoc", "kicad_pcb")), "origine_fichier_mil": ("?", "point"),
               "origine_fichier_mm": ("?", "point"), "origine_cao_mil": ("?", "point")},
    "unite": ("choix", ("mm",)),
    "carte": {
        "contour": _POLY, "boite": "boite",
        "decoupes": ("liste", {"id": "texte", "points": _POLY, "proprietaire": ("?", "texte")}),
        "trous": ("liste", {"id": "texte", "x": "nombre", "y": "nombre", "rotation": "nombre", "trou": _TROU,
                            "metallise": "booleen", "diametre_cuivre": "positif", "couches": ("liste", "texte"),
                            "~classe_isolement": _CLASSE}),
        "couches": ("liste", {"nom": "texte", "nom_fichier": "texte", "rang": "entier",
                              "type": ("choix", ("signal", "plan_negatif")),
                              "plans": ("liste", {"reseau": "texte", "contour": _POLY, "trous": ("liste", _POLY)}),
                              "epaisseur_cuivre_mm": ("?", "positif"), "pistes_libres": "entier",
                              "type_kicad": ("?", "texte"), "routage_permis": ("?", "booleen")}, 1),
        "remplissages": ("liste", {"nom": "texte", "couche": "texte", "reseau": "texte", "contour": _POLY,
                                   "trous": ("liste", _POLY), "couverture": "positif"}),
        "zones_interdites": ("liste", _ZONE),
        "obstacles_cuivre": ("liste", {"id": "texte", "type": "texte", "couches": ("liste", "texte"),
                                       "points": _POLY, "~classe_isolement": _CLASSE}),
        "cuivre_existant": ("liste", ("selon", "type", {
            "piste": {"x1": "nombre", "y1": "nombre", "x2": "nombre", "y2": "nombre", "largeur": "positif"},
            "via": {"x": "nombre", "y": "nombre", "diametre": "positif", "trou": "positif"},
            "region": {"points": _POLY, "~trous": ("liste", _POLY)}},
            {"id": "texte", "reseau": "texte", "couches": ("liste", "texte"), "verrouille": "booleen",
             "~classe_isolement": _CLASSE}))},
    "composants": ("liste", _COMPOSANT), "pastilles_libres": ("liste", _PASTILLE_LIBRE),
    "reseaux": ("liste", "texte"), "classes": ("dict", ("liste", "texte")),
    "paires": ("liste", {"nom": "texte", "positif": "texte", "negatif": "texte"}), "regles": _REGLES}

_TYPES_SIMPLES = {
    "texte": (lambda v: isinstance(v, str), "texte attendu"),
    "nombre": (DC._nombre, "nombre attendu"),
    "positif": (lambda v: DC._nombre(v) and v >= 0, "nombre positif ou nul attendu"),
    "entier": (lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0, "entier positif ou nul attendu"),
    "booleen": (lambda v: isinstance(v, bool), "true ou false attendu"),
    "point": (lambda v: isinstance(v, list) and len(v) == 2 and all(DC._nombre(x) for x in v), "point [x, y] attendu"),
    "boite": (lambda v: isinstance(v, list) and len(v) == 4 and all(DC._nombre(x) for x in v),
              "[x0, y0, x1, y1] attendu"),
    "objet": (lambda v: isinstance(v, dict), "objet attendu"),
    "largeurs": (lambda v: isinstance(v, list) and len(v) == 3 and DC._nombre(v[0]) and DC._nombre(v[1])
                 and (v[2] is None or DC._nombre(v[2])), "[min, préférée, max|null] attendu")}


def _schema(v, s, ch, err):
    """Contrôle de ``v`` contre le schéma ``s`` ; ``err(chemin, message)`` lève l'erreur."""
    if isinstance(s, str):
        test, msg = _TYPES_SIMPLES[s]
        if not test(v):
            err(ch, msg)
    elif isinstance(s, dict):
        if not isinstance(v, dict):
            err(ch, "objet attendu")
        for k, sk in s.items():
            facultatif = k.startswith("~")
            k = k.lstrip("~")
            chk = f"{ch}.{k}" if ch else k
            if k in v:
                _schema(v[k], sk, chk, err)
            elif not facultatif:
                err(chk, "champ manquant")
    elif s[0] == "?":
        if v is not None:
            _schema(v, s[1], ch, err)
    elif s[0] == "liste":
        if not isinstance(v, list):
            err(ch, "liste attendue")
        if len(s) > 2 and len(v) < s[2]:
            err(ch, f"au moins {s[2]} élément(s) attendu(s), {len(v)} trouvé(s)")
        for i, x in enumerate(v):
            _schema(x, s[1], f"{ch}[{i}]", err)
    elif s[0] == "dict":
        if not isinstance(v, dict):
            err(ch, "objet attendu")
        for k, x in v.items():
            _schema(x, s[1], f"{ch}.{k}", err)
    elif s[0] == "choix":
        if not isinstance(v, str) or v not in s[1]:
            err(ch, f"{v!r} inconnu (permis : {', '.join(s[1])})")
    else:                                   # ("selon", clé, {valeur: champs propres}, champs communs)
        _, cle, propres, communs = s
        if not isinstance(v, dict):
            err(ch, "objet attendu")
        if cle not in v:
            err(f"{ch}.{cle}", "champ manquant")
        _schema(v[cle], ("choix", tuple(propres)), f"{ch}.{cle}", err)
        _schema(v, dict(communs, **propres[v[cle]]), ch, err)


def _controler_suite(e, nom):
    """Champs de ``entree_carte/2`` que lit ``construire_donnees``, en plus de ceux de
    ``deduire_config.controler_entree`` : schéma ``_ENTREE`` (présence, type, bornes), contour non dégénéré et
    références internes (couches, réseaux, classes d'isolement, découpes, pastilles, identifiants uniques).
    ``ErreurFormat`` (code 2) qui nomme le champ ; rien n'est complété (§2.1)."""
    def err(ch, msg):
        raise ErreurFormat(f"{nom} : {ch} : {msg}")

    _schema(e, _ENTREE, "", err)
    carte, rg = e["carte"], e["regles"]
    contour = G.nettoyer(carte["contour"])
    if len(contour) < 3 or G.aire(contour) <= 1e-9:
        err("carte.contour", f"contour dégénéré ({len(carte['contour'])} sommet(s), aire nulle) : au moins 3 sommets "
                             f"non alignés attendus")
    iso = rg["isolement"]
    n = len(iso["classes"])
    if len(iso["matrice"]) != n or any(len(ligne) != n for ligne in iso["matrice"]):
        err("regles.isolement.matrice", f"matrice {n} × {n} attendue ({n} classe(s) dans regles.isolement.classes)")

    def classe(v, ch):
        if v is not None and v >= n:
            err(ch, f"classe d'isolement {v} inconnue ({n} classe(s) dans regles.isolement.classes)")

    classe(iso["classe_reference"], "regles.isolement.classe_reference")
    noms_couches = [c["nom"] for c in carte["couches"]]
    connues, reseaux = set(noms_couches), set(e["reseaux"])
    if len(connues) != len(noms_couches):
        err("carte.couches", "nom de couche en double")

    def couches(L, ch, toutes=False):
        if toutes and L == ["toutes"]:
            return
        for i, x in enumerate(L):
            if x not in connues:
                err(f"{ch}[{i}]", f"couche {x} inconnue (couches : {', '.join(noms_couches)})")

    def reseau(r, ch):
        if r and r not in reseaux:
            err(ch, f"réseau {r} absent de « reseaux »")

    def cuivre(o, ch, toutes=False):
        couches(o["couches"], f"{ch}.couches", toutes)
        if "reseau" in o:
            reseau(o["reseau"], f"{ch}.reseau")
        classe(o.get("classe_isolement"), f"{ch}.classe_isolement")

    decoupes = {d["id"] for d in carte["decoupes"]}
    designateurs = {c["designateur"] for c in e["composants"]}
    ids = set()
    for i, c in enumerate(e["composants"]):
        ch = f"composants[{i}]"
        pads = set()
        for j, p in enumerate(c["pastilles"]):
            chp = f"{ch}.pastilles[{j}]"
            if p["id"] in ids:
                err(f"{chp}.id", f"{p['id']} en double")
            ids.add(p["id"])
            pads.add(p["id"])
            cuivre(p, chp)
        for j, r in enumerate(c["regions"]):
            chr_ = f"{ch}.regions[{j}]"
            cuivre(r, chr_)
            if r["pastille"] is not None and r["pastille"] not in pads:
                err(f"{chr_}.pastille", f"pastille {r['pastille']} absente de la pièce {c['designateur']}")
            for k, x in enumerate(r["liaison"] or []):
                reseau(x, f"{chr_}.liaison[{k}]")
        for j, v in enumerate(c["vias"]):
            reseau(v["reseau"], f"{ch}.vias[{j}].reseau")
            classe(v["classe_isolement"], f"{ch}.vias[{j}].classe_isolement")
        for j, z in enumerate(c["zones_interdites"]):
            couches(z["couches"], f"{ch}.zones_interdites[{j}].couches", True)
        for j, d in enumerate(c["decoupes"]):
            if d not in decoupes:
                err(f"{ch}.decoupes[{j}]", f"découpe {d} absente de carte.decoupes")
    for j, p in enumerate(e["pastilles_libres"]):
        ch = f"pastilles_libres[{j}]"
        if p["id"] in ids:
            err(f"{ch}.id", f"{p['id']} en double")
        ids.add(p["id"])
        cuivre(p, ch)
    for i, d in enumerate(carte["decoupes"]):
        if d["proprietaire"] is not None and d["proprietaire"] not in designateurs:
            err(f"carte.decoupes[{i}].proprietaire", f"pièce {d['proprietaire']} inconnue")
    for i, c in enumerate(carte["couches"]):
        for j, p in enumerate(c["plans"]):
            reseau(p["reseau"], f"carte.couches[{i}].plans[{j}].reseau")
    for cle in ("trous", "remplissages", "zones_interdites", "obstacles_cuivre", "cuivre_existant"):
        for i, o in enumerate(carte[cle]):
            ch = f"carte.{cle}[{i}]"
            if cle == "remplissages":
                couches([o["couche"]], f"{ch}.couche")
                reseau(o["reseau"], f"{ch}.reseau")
            else:
                cuivre(o, ch, cle == "zones_interdites")
    for nr, r in rg["reseaux"].items():
        ch = f"regles.reseaux.{nr}"
        reseau(nr, ch)
        for k, v in r["classes_isolement"].items():
            classe(v, f"{ch}.classes_isolement.{k}")
        couches(r["couches_permises"] or [], f"{ch}.couches_permises")
    for i, p in enumerate(e["paires"]):
        for k in ("positif", "negatif"):
            reseau(p[k], f"paires[{i}].{k}")


def construire(entree, contraintes, config, controle_gabarit=None, *, noms=None):
    """(donnees_placeur/2, deduction/1) ; ``formats.ErreurFormat`` (code 2) pour toute incohérence."""
    noms = dict(noms or {})
    DC.controler_entree(entree, noms.get("entree", "entree"))
    _controler_suite(entree, noms.get("entree", "entree"))
    if not isinstance(entree.get("carte"), dict) or not isinstance(entree["carte"].get("boite"), list):
        raise ErreurFormat(f"{noms.get('entree', 'entree')} : carte.boite : liste attendue")
    return _Construction(entree, contraintes or {}, config, controle_gabarit, noms).executer()


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------

USAGE = ("usage : python construire_donnees.py E.json --config C.json [--contraintes K.json] "
         "[--gabarit-controle G.json] --sortie D.json [--deduction X.json]")
OPTIONS = ("--config", "--contraintes", "--gabarit-controle", "--sortie", "--deduction")


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
    for k in ("config", "sortie"):
        if not a.get(k):
            raise ErreurFormat(f"option --{k} manquante")
    return a


def _dire(texte, flux=None):
    flux = sys.stderr if flux is None else flux
    enc = getattr(flux, "encoding", None) or "utf-8"
    flux.write(texte.encode(enc, "replace").decode(enc, "replace") + "\n")
    flux.flush()


def _sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def main(argv=None):
    """Rend le code de sortie (§1.4) sans quitter le processus."""
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        a = _arguments(argv)
    except ErreurFormat as e:
        _dire(f"construire_donnees : {e}")
        _dire(USAGE)
        return 2
    if a is None:
        _dire(USAGE, sys.stdout)
        return 0
    try:
        from importer_carte import ecrire_json  # noqa: PLC0415
        nom = os.path.basename
        entree = formats.charger(a["entree"], "entree_carte/2")
        noms = {"entree": nom(a["entree"]), "config": nom(a["config"]), "sha256_entree": _sha256(a["entree"])}
        config = formats.charger(a["config"], "config_carte/2")
        contraintes, lecture = None, []
        if a.get("contraintes"):
            contraintes, lecture = formats.lire_contraintes(a["contraintes"])
            noms["contraintes"] = nom(a["contraintes"])
        gabarit = None
        if a.get("gabarit-controle"):
            gabarit = formats.charger(a["gabarit-controle"], "controle_gabarit/1")
            noms["controle_gabarit"] = nom(a["gabarit-controle"])
        donnees, deduction = construire(entree, contraintes, config, gabarit, noms=noms)
        deduction["avertissements"] = list(lecture) + deduction["avertissements"]
        ecrire_json(a["sortie"], donnees)
        if a.get("deduction"):
            ecrire_json(a["deduction"], deduction)
    except (ErreurFormat, ErreurEntree) as e:
        _dire(f"construire_donnees : {e}")
        return 2
    except Exception as e:  # erreur interne : ni trace ni message système (qui peut contenir un chemin)
        _dire(f"construire_donnees : erreur interne ({type(e).__name__})")
        return 4
    fixes = sum(1 for c in donnees["composants"] if c["statut"] == "fixe")
    _dire(f"{nom(a['entree'])} : {len(donnees['composants'])} pièces ({fixes} fixes, "
          f"{len(donnees['composants']) - fixes} mobiles), {len(donnees['reseaux'])} réseaux, couches "
          f"{[c['role'] for c in donnees['couches']]} -> {nom(a['sortie'])}", sys.stdout)
    for x in deduction["a_confirmer"]:
        _dire(f"à confirmer : {x['objet']} : {x['proposition']} ({x['raison']})", sys.stdout)
    for x in deduction["avertissements"]:
        _dire(f"avertissement : {x}", sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
