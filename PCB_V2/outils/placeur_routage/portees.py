"""Langage des portées des règles Altium et résolution des règles du fichier (contrat §3.7).

Module pur : il ne lit aucun fichier et ne dépend ni de format_altium ni d'une carte. L'importeur lui passe les
règles actives (format de `entree_carte.regles_fichier`), les profils des objets conducteurs existants et le
contexte de la carte ; il rend le bloc `regles` de `entree_carte/2` et les résultats par objet.

API :
  compiler(texte) -> Arbre        analyse une portée ; lève ErreurPortee (cause et position) si la syntaxe sort du
                                  langage, ou « prédicat X non pris en charge » pour un prédicat hors du tableau
  evaluer(arbre, profil) -> bool  vrai si l'objet décrit par le profil est dans la portée
  interpreter(regles, objets, contexte)
                                  résolution complète (bloc `regles`, résultats par objet, par couche, par paire)
  Fonctions pures de la résolution : preparer_regles, preparer_isolement, resoudre_isolement, premiere_regle,
  isolement_heuristique ; profils : index_contexte, completer_profil, profil_routeur, profil_piece,
  profil_reference ; valeurs : longueur_mm, lire_objectclearances, largeurs_regle, via_regle.

Grammaire (descente récursive ; mots-clés, noms de prédicats et arguments comparés sans tenir compte de la casse ;
espaces libres) :
  expr := ou ; ou := et (("Or" | "||") et)* ; et := non (("And" | "&&") non)* ;
  non := ("Not" | "!") non | primaire ; primaire := "(" expr ")" | predicat ;
  predicat := NOM ["(" [argument ("," argument)*] ")"] ; argument := 'chaîne' ('' = apostrophe) | nombre
Priorités : Not > And > Or.

Profil d'un objet (dict ; une clé absente vaut « vide ») :
  type             piste | arc | via | pastille_cms | pastille_traversante | region | remplissage | texte | trou,
                   ou piece (règles de pièces : Height, PermittedLayers, ComponentOrientations, RoomDefinition)
  reseau           nom du réseau ("" si aucun)
  classes_reseau   classes de réseaux du réseau            (complétées depuis le contexte si absentes)
  paires           paires différentielles du réseau        (idem)
  piece            désignateur de la pièce propriétaire    (None hors pièce)
  classes_piece    classes de composants de la pièce       (idem)
  empreinte        empreinte de la pièce                   (idem)
  pastille         nom de la pastille (pastilles seulement)
  couches          couches de cuivre de l'objet (noms communs : TopLayer, MidLayer1…, BottomLayer) ; pour une
                   pièce : la couche de sa face
  couches_fichier  noms des mêmes couches dans le fichier  (complétés depuis le contexte si absents)
  polygone         nom du polygone dont l'objet est une primitive (None hors polygone)
  masque_regle     pastille dont l'expansion de masque suit la règle SolderMaskExpansion
Couches, sémantique d'Altium : une pastille traversante, un via ou un trou est sur la couche MultiLayer
(OnLayer('MultiLayer') vrai ; OnTopLayer et OnBottomLayer faux).

Lectures choisies là où le contrat est muet (§8.0-2 c, consignées dans le journal du lot B) :
  - ordre des classes d'isolement : ordre lexicographique des signatures, « vrai » avant « faux », puis genre dans
    l'ordre de GENRES ; c'est l'ordre qui reproduit l'attendu de METROID (§3.7) ;
  - ViasUnderSMD : « sans règle : false » vaut quand le fichier n'a aucune règle ViasUnderSMD lisible ; une
    pastille qu'aucune règle existante ne couvre est permise, comme dans Altium (seule lecture compatible avec
    l'attendu de METROID : L1 et U10) ;
  - « Testpoint* » de la liste d'ignorés désigne toute règle de point de test (Testpoint, TestPointUsage et leurs
    variantes Fabrication… et Assembly…) ;
  - une règle Clearance dont LAYERKIND n'est pas « même couche » va dans non_interpretees ;
  - matrice OBJECTCLEARANCES : les couples absents valent la valeur générique ; une matrice dont toutes les
    valeurs (couples de GENRES) sont égales est uniforme, de cette valeur ;
  - PermittedLayers, ComponentOrientations, RoomDefinition : lectures provisoires (aucune carte d'exemple ne les
    porte) ; une valeur non reconnue envoie la règle dans non_interpretees.
"""
from __future__ import annotations

import fnmatch
import re

MIL_MM = 0.0254
ISOLEMENT_DEFAUT_MM = 0.2          # §3.7 : à défaut de règle, 0,2 mm

# Genres d'objets Altium de la matrice OBJECTCLEARANCES, dans l'ordre de §3.7 (ordre des classes à signature égale).
GENRES = ("Track", "Arc", "SMDPad", "THPad", "Via", "Fill", "Region", "Text")
GENRES_INFORMATION = ("Poly", "Hole")      # couples gardés en information seulement (§3.7)
GENRE_DU_TYPE = {"piste": "Track", "arc": "Arc", "pastille_cms": "SMDPad", "pastille_traversante": "THPad",
                 "trou": "THPad", "via": "Via", "remplissage": "Fill", "region": "Region", "texte": "Text"}
TYPES_PASTILLE = ("pastille_cms", "pastille_traversante", "trou")
TYPES_MULTICOUCHE = ("pastille_traversante", "via", "trou")
GENRES_ROUTEUR = ("piste", "via", "surface")   # objets que le routeur peut créer pour un réseau (§3.7)

# Liste d'ignorés documentée (§3.7), comparée sans tenir compte de la casse.
IGNORES = ("FanoutControl", "*Testpoint*", "SilkToSilkClearance", "SilkToBoardRegion*", "PasteMaskExpansion",
           "RoutingCorners", "RoutingTopology", "PolygonConnect", "ShortCircuit", "UnRoutedNet", "UnconnectedPin",
           "NetAntennae", "SMDToCorner", "SMDToPlane", "SMDNeckDown", "MinimumAnnularRing", "HoleSize",
           "LayerPairs", "AcuteAngle")

TYPES_BINAIRES = ("Clearance", "HoleToHoleClearance", "ComponentClearance")
TYPES_UNAIRES = ("Width", "RoutingVias", "ViasUnderSMD", "SolderMaskExpansion", "RoutingLayers", "RoutingPriority",
                 "SupplyNets", "Height", "PermittedLayers", "ComponentOrientations")
TYPES_AUTRES = ("BoardOutlineClearance", "PlaneClearance", "PlaneConnect", "SilkToSolderMaskClearance",
                "RoomDefinition", "DiffPairsRouting", "MatchedLengths")
TYPES_PRIS = {t.casefold(): t for t in TYPES_BINAIRES + TYPES_UNAIRES + TYPES_AUTRES}

PREDICATS_COUCHE = frozenset({"onlayer", "ontoplayer", "onbottomlayer"})
PREDICATS_POLYGONE = frozenset({"ispolygon", "inpolygon", "innamedpolygon"})


# =====================================================================================================================
# Erreur
# =====================================================================================================================
class ErreurPortee(ValueError):
    """Portée hors du langage de §3.7.

    `cause` : texte sans position ; `position` : rang du caractère fautif, à partir de 1 (None si sans objet) ;
    `predicat` : nom du prédicat non pris en charge, tel qu'écrit (None pour une erreur de syntaxe)."""

    def __init__(self, cause, position=None, predicat=None):
        self.cause = cause
        self.position = position
        self.predicat = predicat
        super().__init__(cause if position is None else f"{cause} (position {position})")


# =====================================================================================================================
# Arbre d'une portée compilée
# =====================================================================================================================
class Arbre:
    """Nœud d'une portée compilée : Ou, Et, Non ou Predicat. `repr` redonne une portée équivalente."""
    __slots__ = ()


class Ou(Arbre):
    __slots__ = ("termes",)

    def __init__(self, termes):
        self.termes = tuple(termes)

    def __repr__(self):
        return "(" + " Or ".join(map(repr, self.termes)) + ")"


class Et(Arbre):
    __slots__ = ("termes",)

    def __init__(self, termes):
        self.termes = tuple(termes)

    def __repr__(self):
        return "(" + " And ".join(map(repr, self.termes)) + ")"


class Non(Arbre):
    __slots__ = ("terme",)

    def __init__(self, terme):
        self.terme = terme

    def __repr__(self):
        return "Not " + repr(self.terme)


class Predicat(Arbre):
    """`nom` : nom canonique en minuscules ; `args` : arguments tels qu'écrits ; `args_cf` : arguments en casse
    repliée (comparaisons)."""
    __slots__ = ("nom", "args", "args_cf")

    def __init__(self, nom, args):
        self.nom = nom
        self.args = tuple(args)
        self.args_cf = tuple(a.casefold() for a in self.args)

    def __repr__(self):
        nom = _PREDICATS[self.nom][0]
        if not self.args:
            return nom
        return nom + "(" + ", ".join("'" + a.replace("'", "''") + "'" for a in self.args) + ")"


# Prédicats pris en charge (tableau de §3.7) : nom canonique -> (nom affiché, nombre d'arguments texte)
_PREDICATS = {
    "all": ("All", 0),
    "innet": ("InNet", 1), "innetclass": ("InNetClass", 1),
    "incomponent": ("InComponent", 1), "incomponentclass": ("InComponentClass", 1),
    "hasfootprint": ("HasFootprint", 1), "haspad": ("HasPad", 1),
    "ispad": ("IsPad", 0), "isvia": ("IsVia", 0), "istrack": ("IsTrack", 0), "isarc": ("IsArc", 0),
    "isfill": ("IsFill", 0), "isregion": ("IsRegion", 0), "istext": ("IsText", 0),
    "ispolygon": ("IsPolygon", 0), "inpolygon": ("InPolygon", 0), "innamedpolygon": ("InNamedPolygon", 1),
    "onlayer": ("OnLayer", 1), "ontoplayer": ("OnTopLayer", 0), "onbottomlayer": ("OnBottomLayer", 0),
    "indifferentialpair": ("InDifferentialPair", 1), "inanydifferentialpair": ("InAnyDifferentialPair", 0),
}
_MOTS_CLES = {"and": "ET", "or": "OU", "not": "NON"}


# =====================================================================================================================
# Analyse lexicale (à la demande : un prédicat inconnu est signalé avant la syntaxe qui le suit, par exemple
# « CompPinCount < 5 » ou « ObjectKind = 'Text' »)
# =====================================================================================================================
_RE_NOMBRE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_RE_NOM = re.compile(r"[^\W\d]\w*")


class _Lexique:
    """Jetons (genre, valeur, position à partir de 1). Genres : NOM, CHAINE, NOMBRE, ET, OU, NON, (, ), virgule, FIN."""

    def __init__(self, texte):
        self.texte = texte
        self.i = 0
        self.suivant = None

    def voir(self):
        if self.suivant is None:
            self.suivant = self._lire()
        return self.suivant

    def prendre(self):
        j = self.voir()
        self.suivant = None
        return j

    def _lire(self):
        t, n, i = self.texte, len(self.texte), self.i
        while i < n and t[i].isspace():
            i += 1
        if i >= n:
            self.i = n
            return ("FIN", None, n + 1)
        c, pos = t[i], i + 1
        if c in "(),":
            self.i = i + 1
            return (c, c, pos)
        for symbole, genre in (("&&", "ET"), ("||", "OU")):
            if t.startswith(symbole, i):
                self.i = i + 2
                return (genre, symbole, pos)
        if c == "!":
            self.i = i + 1
            return ("NON", c, pos)
        if c == "'":
            morceaux, j = [], i + 1
            while True:
                k = t.find("'", j)
                if k < 0:
                    raise ErreurPortee("chaîne non terminée", pos)
                morceaux.append(t[j:k])
                if t.startswith("''", k):
                    morceaux.append("'")
                    j = k + 2
                    continue
                self.i = k + 1
                return ("CHAINE", "".join(morceaux), pos)
        if c in "0123456789+-.":
            m = _RE_NOMBRE.match(t, i)
            if m:
                self.i = m.end()
                return ("NOMBRE", float(m.group(0)), pos)
        m = _RE_NOM.match(t, i)
        if m:
            self.i = m.end()
            mot = m.group(0)
            return (_MOTS_CLES.get(mot.casefold(), "NOM"), mot, pos)
        raise ErreurPortee(f"caractère « {c} » inattendu", pos)


def _decrire(j):
    genre, valeur = j[0], j[1]
    if genre == "FIN":
        return "fin de la portée"
    if genre == "CHAINE":
        return "chaîne '" + valeur + "'"
    if genre == "NOMBRE":
        return f"nombre {valeur:g}"
    return f"« {valeur} »"


# =====================================================================================================================
# Analyse syntaxique
# =====================================================================================================================
class _Analyseur:
    def __init__(self, texte):
        self.lex = _Lexique(texte)

    def attendre(self, genre, quoi):
        j = self.lex.voir()
        if j[0] != genre:
            raise ErreurPortee(f"{quoi} attendu, {_decrire(j)} trouvé", j[2])
        return self.lex.prendre()

    def ou(self):
        termes = [self.et()]
        while self.lex.voir()[0] == "OU":
            self.lex.prendre()
            termes.append(self.et())
        return termes[0] if len(termes) == 1 else Ou(termes)

    def et(self):
        termes = [self.non()]
        while self.lex.voir()[0] == "ET":
            self.lex.prendre()
            termes.append(self.non())
        return termes[0] if len(termes) == 1 else Et(termes)

    def non(self):
        if self.lex.voir()[0] == "NON":
            self.lex.prendre()
            return Non(self.non())
        return self.primaire()

    def primaire(self):
        j = self.lex.voir()
        if j[0] == "(":
            self.lex.prendre()
            arbre = self.ou()
            self.attendre(")", "« ) »")
            return arbre
        if j[0] == "NOM":
            return self.predicat()
        raise ErreurPortee(f"prédicat ou « ( » attendu, {_decrire(j)} trouvé", j[2])

    def predicat(self):
        _, mot, pos = self.lex.prendre()
        nom = mot.casefold()
        if nom not in _PREDICATS:
            raise ErreurPortee(f"prédicat {mot} non pris en charge", pos, predicat=mot)
        affiche, n_args = _PREDICATS[nom]
        args = []
        if self.lex.voir()[0] == "(":
            self.lex.prendre()
            if self.lex.voir()[0] != ")":
                while True:
                    a = self.lex.prendre()
                    if a[0] not in ("CHAINE", "NOMBRE"):
                        raise ErreurPortee("argument attendu (chaîne entre apostrophes ou nombre), "
                                           f"{_decrire(a)} trouvé", a[2])
                    args.append(a)
                    if self.lex.voir()[0] != ",":
                        break
                    self.lex.prendre()
            self.attendre(")", "« ) »")
        if len(args) != n_args:
            raise ErreurPortee(f"prédicat {affiche} : {n_args} argument(s) attendu(s), {len(args)} reçu(s)", pos)
        for genre, valeur, p in args:
            if genre != "CHAINE":
                raise ErreurPortee(f"prédicat {affiche} : argument texte attendu, {_decrire((genre, valeur, p))} "
                                   "trouvé", p)
        return Predicat(nom, [v for _, v, _ in args])


def compiler(texte) -> Arbre:
    """Analyse une portée Altium. Lève ErreurPortee (cause, position) si elle sort du langage de §3.7."""
    if texte is None or not str(texte).strip():
        raise ErreurPortee("portée vide", 1)
    a = _Analyseur(str(texte))
    arbre = a.ou()
    j = a.lex.voir()
    if j[0] != "FIN":
        raise ErreurPortee(f"{_decrire(j)} inattendu après la fin de l'expression", j[2])
    return arbre


# =====================================================================================================================
# Évaluation sur un profil
# =====================================================================================================================
def _cf(x):
    return str(x if x is not None else "").casefold()


def _norme_couche(nom):
    """Nom de couche comparable : minuscules, sans espace, tiret ni souligné ('Top Layer' == 'TopLayer')."""
    return re.sub(r"[\s_\-]+", "", str(nom)).casefold()


def _dans(valeur_cf, liste):
    return any(valeur_cf == _cf(x) for x in liste or ())


def _sur_couche(profil, nom_norme):
    if profil.get("type") in TYPES_MULTICOUCHE:
        return nom_norme == "multilayer"
    noms = list(profil.get("couches") or ()) + list(profil.get("couches_fichier") or ())
    return any(_norme_couche(c) == nom_norme for c in noms)


def _a_piece(f):
    return f.get("piece") is not None


_EVALUATION = {
    "all": lambda a, f: True,
    "innet": lambda a, f: _cf(f.get("reseau")) == a,
    "innetclass": lambda a, f: _dans(a, f.get("classes_reseau")),
    "incomponent": lambda a, f: _a_piece(f) and _cf(f["piece"]) == a,
    "incomponentclass": lambda a, f: _a_piece(f) and _dans(a, f.get("classes_piece")),
    "hasfootprint": lambda a, f: _a_piece(f) and _cf(f.get("empreinte")) == a,
    "haspad": lambda a, f: (f.get("type") in TYPES_PASTILLE and _a_piece(f) and f.get("pastille") is not None
                            and _cf(f"{f['piece']}-{f['pastille']}") == a),
    "ispad": lambda a, f: f.get("type") in TYPES_PASTILLE,
    "isvia": lambda a, f: f.get("type") == "via",
    "istrack": lambda a, f: f.get("type") == "piste",
    "isarc": lambda a, f: f.get("type") == "arc",
    "isfill": lambda a, f: f.get("type") == "remplissage",
    "isregion": lambda a, f: f.get("type") == "region",
    "istext": lambda a, f: f.get("type") == "texte",
    "ispolygon": lambda a, f: f.get("polygone") is not None,
    "inpolygon": lambda a, f: f.get("polygone") is not None,
    "innamedpolygon": lambda a, f: f.get("polygone") is not None and _cf(f["polygone"]) == a,
    "onlayer": lambda a, f: _sur_couche(f, _norme_couche(a)),
    "ontoplayer": lambda a, f: _sur_couche(f, "toplayer"),
    "onbottomlayer": lambda a, f: _sur_couche(f, "bottomlayer"),
    "indifferentialpair": lambda a, f: _dans(a, f.get("paires")),
    "inanydifferentialpair": lambda a, f: bool(f.get("paires")),
}


def evaluer(arbre, profil) -> bool:
    """Vrai si l'objet décrit par `profil` est dans la portée `arbre` (résultat de compiler)."""
    if isinstance(arbre, Predicat):
        return bool(_EVALUATION[arbre.nom](arbre.args_cf[0] if arbre.args_cf else None, profil))
    if isinstance(arbre, Et):
        return all(evaluer(t, profil) for t in arbre.termes)
    if isinstance(arbre, Ou):
        return any(evaluer(t, profil) for t in arbre.termes)
    if isinstance(arbre, Non):
        return not evaluer(arbre.terme, profil)
    raise TypeError("arbre de portée attendu (résultat de compiler)")


# =====================================================================================================================
# Analyse statique d'un arbre
# =====================================================================================================================
def predicats(arbre):
    """Noms canoniques (minuscules) des prédicats d'une portée."""
    if isinstance(arbre, Predicat):
        return {arbre.nom}
    if isinstance(arbre, Non):
        return predicats(arbre.terme)
    return set().union(*(predicats(t) for t in arbre.termes))


def _plier(arbre, constantes):
    """Logique à trois valeurs : True, False ou None (inconnu) ; `constantes` : {nom canonique: bool}."""
    if isinstance(arbre, Predicat):
        return constantes.get(arbre.nom)
    if isinstance(arbre, Non):
        v = _plier(arbre.terme, constantes)
        return None if v is None else not v
    vals = [_plier(t, constantes) for t in arbre.termes]
    if isinstance(arbre, Et):
        if any(v is False for v in vals):
            return False
        return True if all(v is True for v in vals) else None
    if any(v is True for v in vals):
        return True
    return False if all(v is False for v in vals) else None


def est_tout(arbre) -> bool:
    """Vrai si la portée couvre tout objet (All, (All), All And All…)."""
    return _plier(arbre, {"all": True}) is True


def polygones_seulement(arbre) -> bool:
    """Vrai si la portée ne peut couvrir qu'une primitive de polygone (aucun objet profilé ni objet du routeur)."""
    return _plier(arbre, {"all": True, **{p: False for p in PREDICATS_POLYGONE}}) is False


# =====================================================================================================================
# Valeurs des règles
# =====================================================================================================================
_UNITES = {"mil": MIL_MM, "mm": 1.0, "in": 25.4, "um": 0.001, "µm": 0.001}
_RE_LONGUEUR = re.compile(r"\s*([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))\s*([A-Za-zµ]*)\s*")
_RE_LARGEUR_COUCHE = re.compile(r"(TOPLAYER|BOTTOMLAYER|MIDLAYER\d+)(?:_\{[^}]*\})?_(MIN|PREF|MAX)WIDTH")


def longueur_mm(texte):
    """'9.8425mil' -> 0.2499995 ; '0.25mm' -> 0.25 ; nombre sans unité : mil. None si absent ou illisible.
    Conversion exacte, arrondie à 1e-9 mm (le fichier ne porte que 1e-4 mil)."""
    if texte is None:
        return None
    m = _RE_LONGUEUR.fullmatch(str(texte))
    if not m:
        return None
    unite = (m.group(2) or "mil").casefold()
    if unite not in _UNITES:
        return None
    return round(float(m.group(1)) * _UNITES[unite], 9)


def _longueur_exigee(texte, cle):
    v = longueur_mm(texte)
    if v is None:
        raise ValueError(f"{cle} illisible ({texte!r})")
    return v


def _booleen(texte):
    t = str(texte if texte is not None else "").strip().upper()
    return True if t == "TRUE" else (False if t == "FALSE" else None)


def _booleen_exige(texte, cle):
    b = _booleen(texte)
    if b is None:
        raise ValueError(f"{cle} illisible ({texte!r})")
    return b


def _entier_exige(texte, cle):
    try:
        return int(float(str(texte).strip()))
    except (TypeError, ValueError):
        raise ValueError(f"{cle} illisible ({texte!r})") from None


def _cle_couche(nom):
    """'TopLayer' -> 'TOPLAYER', 'MidLayer3' -> 'MIDLAYER3' (clés des valeurs par couche des règles)."""
    return re.sub(r"[\s_\-]+", "", str(nom)).upper()


def couches_signal(couches):
    """Couches de routage possibles (signal) parmi les couches de cuivre : TopLayer, MidLayer<n>, BottomLayer."""
    return [c for c in couches if re.fullmatch(r"TopLayer|BottomLayer|MidLayer\d+", str(c))]


def lire_objectclearances(texte):
    """'ClearanceObj_Track-ClearanceObj_Fill:137795;…' (valeurs en 1/10000 mil) -> ({(g1, g2): mm}, symétrique et
    limité aux couples de GENRES ; [couples gardés en information (polygone, trou, genre inconnu), en texte])."""
    objets, info = {}, []
    for e in str(texte or "").split(";"):
        m = re.fullmatch(r"\s*ClearanceObj_(\w+)-ClearanceObj_(\w+)\s*:\s*([+-]?\d+(?:\.\d*)?)\s*", e)
        if not m:
            continue
        g1, g2 = m.group(1), m.group(2)
        mm = round(float(m.group(3)) / 1e4 * MIL_MM, 9)
        if g1 in GENRES and g2 in GENRES:
            objets[(g1, g2)] = objets[(g2, g1)] = mm
        else:
            info.append(f"{g1}-{g2} {mm:g} mm")
    return objets, info


def _matrice_genres(generique, objets):
    """Matrice complète sur GENRES (couples absents = valeur générique) et sa valeur si elle est uniforme."""
    mat = {(g1, g2): objets.get((g1, g2), generique) for g1 in GENRES for g2 in GENRES}
    valeurs = {round(v, 9) for v in mat.values()}
    return mat, (valeurs.pop() if len(valeurs) == 1 else None)


def _par_couche(valeurs, couches):
    """Valeurs de largeur par couche ({COUCHE: {"MIN", "PREF", "MAX"}}) des couches de la carte (toutes si None)."""
    par = {}
    for k, v in valeurs.items():
        m = _RE_LARGEUR_COUCHE.fullmatch(str(k).strip().upper())
        x = longueur_mm(v) if m else None
        if x is not None:
            par.setdefault(m.group(1), {})[m.group(2)] = x
    if couches is not None:
        gardees = {_cle_couche(c) for c in couches}
        par = {c: d for c, d in par.items() if c in gardees}
    return dict(sorted(par.items()))


def largeurs_regle(valeurs, couches=None):
    """[min, pref, max] (mm) d'une règle Width et avertissement (None si rien à signaler).

    Valeurs générales MINLIMIT, PREFEREDWIDTH, MAXLIMIT ; valeurs par couche (`<COUCHE>_MINWIDTH`, `_PREFWIDTH`,
    `_MAXWIDTH`) des couches de la carte (`couches`, toutes si None) : si elles diffèrent des valeurs générales, on
    retient [plus grand min, pref ramenée dans l'intervalle, plus petit max] avec un avertissement (§3.7).
    Lève ValueError si les valeurs générales sont illisibles."""
    gen = [longueur_mm(valeurs.get(k)) for k in ("MINLIMIT", "PREFEREDWIDTH", "MAXLIMIT")]
    if None in gen:
        raise ValueError("MINLIMIT, PREFEREDWIDTH ou MAXLIMIT illisible")
    triplets = [[d.get("MIN", gen[0]), d.get("PREF", gen[1]), d.get("MAX", gen[2])]
                for d in _par_couche(valeurs, couches).values()]
    if all(all(abs(a - b) < 1e-9 for a, b in zip(t, gen)) for t in triplets):
        return gen, None
    mn = max([gen[0]] + [t[0] for t in triplets])
    mx = min([gen[2]] + [t[2] for t in triplets])
    if mn > mx + 1e-9:
        return gen, (f"valeurs par couche incompatibles (min {mn:g} > max {mx:g} mm) : valeurs générales "
                     f"[{gen[0]:g} ; {gen[1]:g} ; {gen[2]:g}] retenues")
    pref = min(max(gen[1], mn), mx)
    return [mn, pref, mx], (f"valeurs différentes selon les couches : [{mn:g} ; {pref:g} ; {mx:g}] retenu "
                            "(plus grand min, plus petit max)")


def largeurs_paire(valeurs, couches):
    """[min, pref, max] (mm) d'une règle DiffPairsRouting, lue seulement par couche (TOPLAYER_PREFWIDTH…) sur les
    couches de la carte, et avertissement si elles diffèrent. Lève ValueError si aucune couche n'est lisible."""
    triplets = [d for d in _par_couche(valeurs, couches).values() if len(d) == 3]
    if not triplets:
        raise ValueError("largeurs de paire par couche illisibles")
    mn, mx = max(d["MIN"] for d in triplets), min(d["MAX"] for d in triplets)
    prefs = sorted({d["PREF"] for d in triplets})
    if mn > mx + 1e-9:
        mn, mx = min(d["MIN"] for d in triplets), max(d["MAX"] for d in triplets)
        return [mn, min(max(prefs[0], mn), mx), mx], "valeurs par couche incompatibles : enveloppe retenue"
    pref = min(max(prefs[0], mn), mx)
    uniforme = len(prefs) == 1 and len({(d["MIN"], d["MAX"]) for d in triplets}) == 1
    return [mn, pref, mx], (None if uniforme else
                            f"valeurs différentes selon les couches : [{mn:g} ; {pref:g} ; {mx:g}] retenu")


def via_regle(valeurs):
    """Via d'une règle RoutingVias (mm) : {"diametre", "trou", "diametre_min", "diametre_max", "trou_min",
    "trou_max"} ; un minimum absent vaut la valeur préférée, un maximum absent vaut None. ValueError si illisible."""
    def lire(*cles):
        for c in cles:
            v = longueur_mm(valeurs.get(c))
            if v is not None:
                return v
        return None
    v = {"diametre": lire("WIDTH", "PREFEREDWIDTH"), "trou": lire("HOLEWIDTH", "PREFEREDHOLEWIDTH"),
         "diametre_min": lire("MINWIDTH"), "diametre_max": lire("MAXWIDTH"),
         "trou_min": lire("MINHOLEWIDTH"), "trou_max": lire("MAXHOLEWIDTH")}
    if v["diametre"] is None or v["trou"] is None:
        raise ValueError("diamètre (WIDTH) ou trou (HOLEWIDTH) du via illisible")
    if v["diametre_min"] is None:
        v["diametre_min"] = v["diametre"]
    if v["trou_min"] is None:
        v["trou_min"] = v["trou"]
    return v


# =====================================================================================================================
# Profils
# =====================================================================================================================
def index_contexte(contexte):
    """Index des appartenances tirées du contexte (voir interpreter) : classes et paires par réseau, classes et
    empreinte par pièce, noms de fichier des couches."""
    contexte = contexte or {}
    classes_reseau, paires_reseau, classes_piece, empreintes = {}, {}, {}, {}
    for c, membres in (contexte.get("classes") or {}).items():
        for n in membres or ():
            classes_reseau.setdefault(n, []).append(c)
    for p in contexte.get("paires") or ():
        for n in (p.get("positif"), p.get("negatif")):
            if n:
                paires_reseau.setdefault(n, []).append(p.get("nom"))
    for c, membres in (contexte.get("classes_composants") or {}).items():
        for d in membres or ():
            classes_piece.setdefault(d, []).append(c)
    for p in contexte.get("pieces") or ():
        empreintes[p.get("designateur")] = p.get("empreinte") or ""
    return {"classes_reseau": classes_reseau, "paires": paires_reseau, "classes_piece": classes_piece,
            "empreintes": empreintes, "noms_fichier": dict(contexte.get("noms_couches_fichier") or {})}


def completer_profil(profil, index=None):
    """Copie du profil avec ses clés par défaut et les appartenances déduites du contexte (`index` :
    index_contexte) ; une clé déjà présente est gardée telle quelle."""
    index = index if index is not None else index_contexte({})
    f = dict(profil)
    f.setdefault("type", "")
    f["reseau"] = f.get("reseau") or ""
    f.setdefault("piece", None)
    f.setdefault("pastille", None)
    f.setdefault("polygone", None)
    f["couches"] = list(f.get("couches") or ())
    if "classes_reseau" not in f:
        f["classes_reseau"] = list(index["classes_reseau"].get(f["reseau"], ())) if f["reseau"] else []
    if "paires" not in f:
        f["paires"] = list(index["paires"].get(f["reseau"], ())) if f["reseau"] else []
    if "classes_piece" not in f:
        f["classes_piece"] = list(index["classes_piece"].get(f["piece"], ())) if f["piece"] is not None else []
    if "empreinte" not in f:
        f["empreinte"] = index["empreintes"].get(f["piece"], "") if f["piece"] is not None else ""
    if "couches_fichier" not in f:
        f["couches_fichier"] = [index["noms_fichier"][c] for c in f["couches"] if c in index["noms_fichier"]]
    return f


def profil_routeur(reseau, genre, index=None, couches=()):
    """Objet que le routeur peut créer pour `reseau`, hors pièce et hors polygone : `genre` 'piste', 'via' ou
    'surface' (une région). Un via traverse toutes les `couches` ; une piste ou une surface n'a pas de couche fixée."""
    t = {"piste": "piste", "via": "via", "surface": "region"}[genre]
    return completer_profil({"type": t, "reseau": reseau, "piece": None,
                             "couches": list(couches) if t == "via" else []}, index)


def profil_piece(designateur, face, index=None, empreinte=None):
    """Pièce posée sur `face` ('dessus' | 'dessous') ; désignateur None = pièce quelconque (sans classe ni
    empreinte), qui sert à reconnaître les portées de face."""
    f = {"type": "piece", "piece": designateur, "couches": ["TopLayer" if face == "dessus" else "BottomLayer"]}
    if designateur is None:
        f.update(classes_piece=[], empreinte="")
    elif empreinte is not None:
        f["empreinte"] = empreinte
    return completer_profil(f, index)


def profil_reference():
    """Piste d'un réseau sans classe, hors pièce (classe de référence et largeur par défaut, §3.7)."""
    return completer_profil({"type": "piste", "reseau": "", "piece": None})


def genre_objet(profil):
    """Genre Altium d'un objet (matrice OBJECTCLEARANCES)."""
    return GENRE_DU_TYPE.get(profil.get("type"), "Track")


# =====================================================================================================================
# Préparation des règles
# =====================================================================================================================
def _est_ignore(type_regle):
    t = _cf(type_regle)
    return any(fnmatch.fnmatchcase(t, motif.casefold()) for motif in IGNORES)


def _non_interpretee(r, cause):
    return {"type": r.get("type"), "nom": r.get("nom"), "portee1": r.get("portee1"), "portee2": r.get("portee2"),
            "cause": cause}


def _cause_erreur(e, numero):
    if e.predicat is not None:
        return e.cause
    return f"portée {numero} hors du langage : {e}"


def _priorite(r):
    try:
        return int(r.get("priorite"))
    except (TypeError, ValueError):
        return 1 << 30


def preparer_regles(regles):
    """Classe les règles actives (format regles_fichier) : rend (par_type, non_interpretees, ignorees).

    par_type[type] = règles interprétables de ce type, par priorité croissante (1 = la plus forte ; égalité : ordre
    du fichier), chacune {"type", "nom", "priorite", "portee1", "portee2", "valeurs", "arbre1", "arbre2"} (arbre2
    pour les types binaires seulement). Les limites propres aux types (prédicat de couche, portée de réseau, All ×
    All, polygones) sont contrôlées ici ; non_interpretees suit l'ordre du fichier ; ignorees est triée."""
    par_type, non_int, ignorees = {}, [], set()
    for k, r in enumerate(regles or ()):
        if r.get("active") is False:
            continue
        t_brut = r.get("type")
        if _est_ignore(t_brut):
            ignorees.add(str(t_brut))
            continue
        t = TYPES_PRIS.get(_cf(t_brut))
        if t is None:
            non_int.append(_non_interpretee(r, "type non pris en charge"))
            continue
        arbres = []
        for numero in ((1, 2) if t in TYPES_BINAIRES else (1,)):
            try:
                arbres.append(compiler(r.get(f"portee{numero}")))
            except ErreurPortee as e:
                non_int.append(_non_interpretee(r, _cause_erreur(e, numero)))
                break
        else:
            a1, a2 = arbres[0], (arbres[1] if len(arbres) > 1 else None)
            cause = _cause_portee(t, r, a1, a2)
            if cause:
                non_int.append(_non_interpretee(r, cause))
                continue
            par_type.setdefault(t, []).append((_priorite(r), k, {
                "type": t, "nom": r.get("nom"), "priorite": r.get("priorite"), "portee1": r.get("portee1"),
                "portee2": r.get("portee2"), "valeurs": dict(r.get("valeurs") or {}), "arbre1": a1, "arbre2": a2}))
    par_type = {t: [x[2] for x in sorted(lst, key=lambda x: (x[0], x[1]))] for t, lst in par_type.items()}
    return par_type, non_int, sorted(ignorees)


def _cause_portee(t, r, a1, a2):
    """Cause de non-interprétation propre au type (limites de la vague 1, §3.7), sinon None."""
    arbres = [a for a in (a1, a2) if a is not None]
    if any(polygones_seulement(a) for a in arbres):
        return "polygones"
    if t in ("Clearance", "Width") and any(predicats(a) & PREDICATS_COUCHE for a in arbres):
        return f"prédicat de couche dans une règle {t}"
    v = r.get("valeurs") or {}
    if t == "Clearance":
        ns = str(v.get("NETSCOPE") or "DifferentNets").strip()
        if ns.casefold() != "differentnets":
            return f"portée de réseau « {ns} » non prise en charge (seule « réseaux différents » l'est)"
        lk = str(v.get("LAYERKIND") or "SameLayer").strip()
        if lk.casefold() != "samelayer":
            return f"portée de couches « {lk} » non prise en charge (seule « même couche » l'est)"
    if t in ("HoleToHoleClearance", "ComponentClearance") and not (est_tout(a1) and est_tout(a2)):
        return "portée autre que All × All"
    return None


def _lire_valeurs(regles_du_type, lire, non_int, avert):
    """Lit la valeur de chaque règle (`lire(valeurs) -> (valeur, avertissement | None)`) ; une règle illisible va
    dans non_interpretees. Rend les règles lisibles, chacune complétée de "valeur"."""
    ok = []
    for r in regles_du_type or ():
        try:
            v, a = lire(r["valeurs"])
        except (ValueError, TypeError, KeyError) as e:
            detail = str(e).strip()
            non_int.append(_non_interpretee(r, "valeurs non reconnues" + (f" : {detail}" if detail else "")))
            continue
        if a:
            avert.append(f"{r['type']} « {r['nom']} » : {a}")
        ok.append(dict(r, valeur=v))
    return ok


def premiere_regle(regles_du_type, profil):
    """Première règle (par priorité) dont la portée 1 couvre le profil, sinon None."""
    for r in regles_du_type or ():
        if evaluer(r["arbre1"], profil):
            return r
    return None


# =====================================================================================================================
# Isolement (règle binaire Clearance)
# =====================================================================================================================
def preparer_isolement(regles_clearance, non_int=None):
    """Règles Clearance interprétées (preparer_regles) -> (règles d'isolement, informations).

    Règle d'isolement : {"nom", "arbre1", "arbre2", "generique": mm, "matrice_genres": {(g1, g2): mm},
    "uniforme": mm | None}. Une règle dont la valeur générale est illisible va dans `non_int`."""
    out, info = [], []
    non_int = non_int if non_int is not None else []
    for r in regles_clearance or ():
        v = r["valeurs"]
        gen = longueur_mm(v.get("GENERICCLEARANCE"))
        gen = longueur_mm(v.get("GAP")) if gen is None else gen
        if gen is None:
            non_int.append(_non_interpretee(r, "valeurs non reconnues : GENERICCLEARANCE et GAP illisibles"))
            continue
        objets, inf = lire_objectclearances(v.get("OBJECTCLEARANCES"))
        if inf:
            info.append(f"Clearance « {r['nom']} » : couples non utilisés (polygone, trou) : " + ", ".join(inf))
        if _booleen(v.get("IGNOREPADTOPADCLEARANCEINFOOTPRINT")):
            info.append(f"Clearance « {r['nom']} » : Altium n'y contrôle pas l'isolement entre pastilles d'une même "
                        "empreinte (option non reprise)")
        mat, uni = _matrice_genres(gen, objets)
        out.append({"nom": r["nom"], "arbre1": r["arbre1"], "arbre2": r["arbre2"], "generique": gen,
                    "matrice_genres": mat, "uniforme": uni})
    return out, info


def _signature(regles_iso, profil, genres):
    s = tuple((evaluer(r["arbre1"], profil), evaluer(r["arbre2"], profil)) for r in regles_iso)
    return s + ((genre_objet(profil),) if genres else ())


def _cle_signature(sig, n):
    bools = tuple(0 if b else 1 for paire in sig[:n] for b in paire)   # « vrai » avant « faux »
    return bools + tuple(GENRES.index(g) for g in sig[n:])


def _cle_memo(f):
    return (f.get("type"), f.get("reseau"), tuple(f.get("classes_reseau") or ()), tuple(f.get("paires") or ()),
            f.get("piece"), tuple(f.get("classes_piece") or ()), f.get("empreinte"), f.get("pastille"),
            tuple(f.get("couches") or ()), tuple(f.get("couches_fichier") or ()), f.get("polygone"))


def _nom_classe(sig, regles_iso, genres):
    """Nom lisible d'une classe : règles dont une portée particulière (autre que All) contient l'objet."""
    parts = []
    for (b1, b2), r in zip(sig, regles_iso):
        cotes = [str(k) for k, b, a in ((1, b1, r["arbre1"]), (2, b2, r["arbre2"])) if b and not est_tout(a)]
        if cotes:
            parts.append(f"{r['nom']}:{'+'.join(cotes)}")
    nom = " ; ".join(parts) if parts else "autres objets"
    return nom + (f" [{sig[-1]}]" if genres else "")


def resoudre_isolement(regles_iso, profils, genres=None, reference=None):
    """Classes d'isolement et matrice (§3.7).

    regles_iso : règles d'isolement par priorité (preparer_isolement) ; profils : profils complétés ; genres :
    vrai si le genre d'objet complète la signature (défaut : dès qu'une matrice OBJECTCLEARANCES n'est pas
    uniforme) ; reference : profil de la classe de référence (défaut : piste d'un réseau sans classe, hors pièce).
    Rend (isolement, classes) : isolement = {"defaut", "classes", "matrice", "classe_reference"} ; classes = classe
    de chaque profil, dans l'ordre."""
    if genres is None:
        genres = any(r["uniforme"] is None for r in regles_iso)
    reference = reference or profil_reference()
    memo = {}

    def sig(f):
        k = _cle_memo(f)
        if k not in memo:
            memo[k] = _signature(regles_iso, f, genres)
        return memo[k]
    sigs = [sig(f) for f in profils]
    sig_ref = sig(reference)
    n = len(regles_iso)
    classes = sorted(set(sigs) | {sig_ref}, key=lambda s: _cle_signature(s, n))
    rang = {s: i for i, s in enumerate(classes)}
    matrice = [[ISOLEMENT_DEFAUT_MM] * len(classes) for _ in classes]
    for i, si in enumerate(classes):
        for j in range(i, len(classes)):
            sj = classes[j]
            for k, r in enumerate(regles_iso):
                if (si[k][0] and sj[k][1]) or (sj[k][0] and si[k][1]):
                    if genres:
                        v = r["matrice_genres"][(si[-1], sj[-1])]
                    else:     # matrice uniforme (ou genres écartés par l'appelant : valeur générique)
                        v = r["uniforme"] if r["uniforme"] is not None else r["generique"]
                    matrice[i][j] = matrice[j][i] = v
                    break
    ref = rang[sig_ref]
    iso = {"defaut": matrice[ref][ref], "classes": [_nom_classe(s, regles_iso, genres) for s in classes],
           "matrice": matrice, "classe_reference": ref}
    return iso, [rang[s] for s in sigs]


def isolement_heuristique(isolement, classe_piste):
    """`isolement_mm` d'un réseau (§2.5) : matrice[classe de ses pistes][classe de référence]."""
    return isolement["matrice"][classe_piste][isolement["classe_reference"]]


# =====================================================================================================================
# Règles unaires et autres règles : lectures des valeurs
# =====================================================================================================================
def _couches_permises(valeurs, couches):
    """RoutingLayers -> ({couche de signal: bool}, avertissement | None) ; une couche absente de la règle est
    considérée permise (avertissement)."""
    v = {str(k).strip().upper(): x for k, x in valeurs.items()}
    out, absentes = {}, []
    for c in couches_signal(couches):
        if c == "TopLayer":
            cles = ("TOP LAYER_V5", "TOP LAYER", "TOPLAYER")
        elif c == "BottomLayer":
            cles = ("BOTTOM LAYER_V5", "BOTTOM LAYER", "BOTTOMLAYER")
        else:
            k = c[len("MidLayer"):]
            cles = (f"MID LAYER {k}_V5", f"MID LAYER {k}", f"MIDLAYER{k}")
        b = next((x for x in (_booleen(v.get(cle)) for cle in cles) if x is not None), None)
        if b is None:
            absentes.append(c)
            b = True
        out[c] = b
    return out, (f"couche(s) {', '.join(absentes)} absente(s) de la règle : considérée(s) permise(s)"
                 if absentes else None)


def _faces_permises(valeurs):
    """PermittedLayers -> faces permises ['dessus', 'dessous'] (lecture provisoire : aucune carte d'exemple)."""
    v = {str(k).strip().upper(): x for k, x in valeurs.items()}

    def lire(*cles):
        return next((x for x in (_booleen(v.get(c)) for c in cles) if x is not None), None)
    haut = lire("TOPLAYER", "TOP LAYER", "TOP LAYER_V5", "TOP")
    bas = lire("BOTTOMLAYER", "BOTTOM LAYER", "BOTTOM LAYER_V5", "BOTTOM")
    if haut is None or bas is None:
        raise ValueError("faces permises non reconnues")
    return [f for f, ok in (("dessus", haut), ("dessous", bas)) if ok]


def _rotations_permises(valeurs):
    """ComponentOrientations -> angles permis, ou None si toutes les orientations le sont (lecture provisoire :
    masque ALLOWEDORIENTATIONS, bits 1 = 0°, 2 = 90°, 4 = 180°, 8 = 270°, 16 = toutes ; aucune carte d'exemple)."""
    try:
        masque = int(str(valeurs.get("ALLOWEDORIENTATIONS")).strip())
    except (TypeError, ValueError):
        raise ValueError("orientations permises non reconnues") from None
    if masque & 16:
        return None
    return [a for b, a in ((1, 0.0), (2, 90.0), (4, 180.0), (8, 270.0)) if masque & b]


def _style_connexion(v):
    s = str(v.get("PLANECONNECTSTYLE") or "").strip()
    if s.casefold() in ("relief", "direct"):
        return s.casefold()
    raise ValueError(f"style de connexion « {s} » non pris en charge (relief ou direct)")


# =====================================================================================================================
# Résolution complète
# =====================================================================================================================
def interpreter(regles, objets, contexte):
    """Résout les règles actives d'une carte (§3.7).

    regles   : règles actives, format de entree_carte.regles_fichier : {"type", "nom", "priorite", "portee1",
               "portee2", "valeurs": {clé: texte}} (une clé "active": false écarte la règle) ;
    objets   : profils des objets conducteurs existants (pastilles, cuivre d'empreinte, vias d'empreinte, trous,
               obstacles de cuivre, cuivre existant), sans les primitives de polygone ;
    contexte : {"reseaux": [noms], "classes": {classe: [réseaux]}, "classes_composants": {classe: [des]},
                "paires": [{"nom", "positif", "negatif"}], "couches": [couches de cuivre, de haut en bas, noms
                communs], "noms_couches_fichier": {nom commun: nom du fichier} (facultatif),
                "pieces": [{"designateur", "empreinte", "face"}], "origine_mil": [x0, y0] (salles, facultatif)}.

    Rend {"regles": bloc `regles` de entree_carte/2,
          "objets": [par objet, dans l'ordre : {"classe_isolement", "via_dans_pastille_permis" (pastilles),
                     "expansion_masque" (pastilles en mode règle)}],
          "couches": {couche: {"routage_permis": bool | None}},
          "paires": {paire: {"ecart_mm", "ecart_min_mm", "ecart_max_mm", "largeurs"}} (DiffPairsRouting),
          "avertissements": [str], "informations": [str]}."""
    contexte = contexte or {}
    index = index_contexte(contexte)
    couches = list(contexte.get("couches") or ())
    reseaux = list(contexte.get("reseaux") or ())
    pieces = list(contexte.get("pieces") or ())
    avert, info = [], []
    par_type, non_int, ignorees = preparer_regles(regles)

    # --- isolement : objets existants, objets du routeur de chaque réseau, profil de référence
    profils = [completer_profil(o, index) for o in objets or ()]
    routeur = {n: {g: profil_routeur(n, g, index, couches) for g in GENRES_ROUTEUR} for n in reseaux}
    regles_iso, info_iso = preparer_isolement(par_type.get("Clearance", []), non_int)
    info += info_iso
    a_classer = profils + [routeur[n][g] for n in reseaux for g in GENRES_ROUTEUR]
    isolement, classes = resoudre_isolement(regles_iso, a_classer)
    classes_objets = classes[:len(profils)]
    suite = iter(classes[len(profils):])
    classes_routeur = {n: {g: next(suite) for g in GENRES_ROUTEUR} for n in reseaux}

    # --- règles unaires par réseau
    ref = profil_reference()
    largeurs = _lire_valeurs(par_type.get("Width"), lambda v: largeurs_regle(v, couches), non_int, avert)
    vias = _lire_valeurs(par_type.get("RoutingVias"), lambda v: (via_regle(v), None), non_int, avert)
    priorites = _lire_valeurs(par_type.get("RoutingPriority"),
                              lambda v: (_entier_exige(v.get("ROUTINGPRIORITY"), "ROUTINGPRIORITY"), None),
                              non_int, avert)
    routage = _lire_valeurs(par_type.get("RoutingLayers"), lambda v: _couches_permises(v, couches), non_int, avert)
    alim = par_type.get("SupplyNets", [])
    r_ref = premiere_regle(largeurs, ref)
    via_ref = premiere_regle(vias, profil_routeur("", "via", None, couches))
    regles_reseaux = {}
    for n in reseaux:
        piste, via = routeur[n]["piste"], routeur[n]["via"]
        rl, rv = premiere_regle(largeurs, piste), premiere_regle(vias, via)
        rc, rp = premiere_regle(routage, piste), premiere_regle(priorites, piste)
        regles_reseaux[n] = {
            "largeurs": list(rl["valeur"]) if rl else None,
            "via": dict(rv["valeur"]) if rv else None,
            "classes_isolement": dict(classes_routeur[n]),
            "couches_permises": [c for c, ok in rc["valeur"].items() if ok] if rc else None,
            "priorite_routage": rp["valeur"] if rp else 0,
            "alimentation_schema": premiere_regle(alim, piste) is not None,
            "regle_largeur_propre": rl is not None and rl is not r_ref,
        }
    couches_res = {c: {"routage_permis": None} for c in couches}
    rc_tout = next((r for r in routage if est_tout(r["arbre1"])), None)
    if rc_tout:
        for c, ok in rc_tout["valeur"].items():
            couches_res[c]["routage_permis"] = ok

    # --- pastilles : via dans la pastille (ViasUnderSMD), expansion de masque (SolderMaskExpansion)
    vus = _lire_valeurs(par_type.get("ViasUnderSMD"), lambda v: (_booleen_exige(v.get("ALLOWED"), "ALLOWED"), None),
                        non_int, avert)
    sme = _lire_valeurs(par_type.get("SolderMaskExpansion"),
                        lambda v: (_longueur_exigee(v.get("EXPANSION"), "EXPANSION"), None), non_int, avert)
    res_objets, sans_regle_vus, sans_regle_masque = [], 0, 0
    for f, k in zip(profils, classes_objets):
        o = {"classe_isolement": k}
        if f["type"] in TYPES_PASTILLE:
            if f["type"] != "pastille_cms":
                o["via_dans_pastille_permis"] = True
            elif not vus:
                o["via_dans_pastille_permis"] = False
                sans_regle_vus += 1
            else:
                r = premiere_regle(vus, f)
                o["via_dans_pastille_permis"] = True if r is None else r["valeur"]
            if f.get("masque_regle"):
                r = premiere_regle(sme, f)
                if r is None:
                    sans_regle_masque += 1
                o["expansion_masque"] = 0.0 if r is None else r["valeur"]
        res_objets.append(o)
    if sans_regle_vus:
        avert.append(f"aucune règle ViasUnderSMD : via interdit dans les {sans_regle_vus} pastille(s) CMS")
    if sans_regle_masque:
        avert.append(f"aucune règle SolderMaskExpansion pour {sans_regle_masque} pastille(s) en mode règle : "
                     "expansion 0")

    # --- pièces, autres règles, paires
    hauteurs, regles_pieces = _regles_pieces(par_type, pieces, index, non_int, avert)
    autres = _autres_regles(par_type, ref, couches, pieces, index, contexte, non_int, avert)
    paires = _regles_paires(par_type, contexte, index, couches, reseaux, non_int, avert, autres)

    regles_out = {
        "isolement": isolement,
        "reseaux": regles_reseaux,
        "largeur_defaut": list(r_ref["valeur"]) if r_ref else None,
        "via": dict(via_ref["valeur"]) if via_ref else None,
        "trou_trou": autres["trou_trou"],
        "garde_bord": autres["garde_bord"],
        "isolement_pieces": autres["isolement_pieces"],
        "hauteurs": hauteurs,
        "pieces": regles_pieces,
        "connexion_plans": autres["connexion_plans"],
        "masque_serigraphie": autres["masque_serigraphie"],
        "salles": autres["salles"],
        "tolerances_longueur": autres["tolerances_longueur"],
        "non_interpretees": non_int,
        "ignorees": ignorees,
    }
    return {"regles": regles_out, "objets": res_objets, "couches": couches_res, "paires": paires,
            "avertissements": avert, "informations": info}


def _regles_pieces(par_type, pieces, index, non_int, avert):
    """Height (portées de face -> hauteurs, portées de pièce -> hauteur_max), PermittedLayers,
    ComponentOrientations."""
    haut = _lire_valeurs(par_type.get("Height"), lambda v: (_longueur_exigee(v.get("MAXHEIGHT"), "MAXHEIGHT"), None),
                         non_int, avert)
    faces = _lire_valeurs(par_type.get("PermittedLayers"), lambda v: (_faces_permises(v), None), non_int, avert)
    orient = _lire_valeurs(par_type.get("ComponentOrientations"), lambda v: (_rotations_permises(v), None),
                           non_int, avert)
    anonymes = {f: profil_piece(None, f, index) for f in ("dessus", "dessous")}
    r_face = {f: premiere_regle(haut, anonymes[f]) for f in anonymes}
    hauteurs = {f: (r_face[f]["valeur"] if r_face[f] else None) for f in ("dessus", "dessous")}
    out = {}
    for p in pieces:
        d = p.get("designateur")
        face = p.get("face") or "dessus"
        propres = []
        for f in ("dessus", "dessous"):
            r = premiere_regle(haut, profil_piece(d, f, index, p.get("empreinte")))
            if r is not None and r is not r_face[f]:
                propres.append(r["valeur"])
        if len(set(propres)) > 1:
            avert.append(f"Height : hauteurs maximales différentes selon la face pour {d} ; la plus petite est retenue")
        actuel = profil_piece(d, face, index, p.get("empreinte"))
        rf, ro = premiere_regle(faces, actuel), premiere_regle(orient, actuel)
        e = {"faces_permises": rf["valeur"] if rf else None, "rotations_permises": ro["valeur"] if ro else None,
             "hauteur_max": min(propres) if propres else None}
        if any(v is not None for v in e.values()):
            out[d] = e
    return hauteurs, out


_LECTEURS_GLOBAUX = {
    # type -> (genre du profil de référence, lecture des valeurs)
    "BoardOutlineClearance": ("piste", lambda v: _longueur_exigee(v.get("GENERICCLEARANCE") or v.get("GAP"),
                                                                  "GENERICCLEARANCE")),
    "PlaneClearance": ("via", lambda v: _longueur_exigee(v.get("CLEARANCE"), "CLEARANCE")),
    "PlaneConnect": ("via", _style_connexion),
    "SilkToSolderMaskClearance": ("pastille", lambda v: _longueur_exigee(v.get("MINSILKSCREENTOMASKGAP"),
                                                                         "MINSILKSCREENTOMASKGAP")),
}


def _autres_regles(par_type, ref, couches, pieces, index, contexte, non_int, avert):
    res = {"trou_trou": None, "garde_bord": None, "isolement_pieces": None, "connexion_plans": None,
           "masque_serigraphie": None, "salles": [], "tolerances_longueur": {}}
    # All × All (contrôlé dans preparer_regles) : la plus prioritaire
    for t, cle in (("HoleToHoleClearance", "trou_trou"), ("ComponentClearance", "isolement_pieces")):
        ok = _lire_valeurs(par_type.get(t), lambda v: (_longueur_exigee(v.get("GAP"), "GAP"), None), non_int, avert)
        if ok:
            res[cle] = ok[0]["valeur"]
    # types globaux : la règle qui couvre le profil de référence ; une autre portée particulière est signalée
    references = {"piste": ref, "via": profil_routeur("", "via", None, couches),
                  "pastille": completer_profil({"type": "pastille_cms", "reseau": "", "piece": None})}
    valeurs = {}
    for t, (genre, lire) in _LECTEURS_GLOBAUX.items():
        ok = _lire_valeurs(par_type.get(t), lambda v, lire=lire: (lire(v), None), non_int, avert)
        choisie = premiere_regle(ok, references[genre])
        for r in ok:
            if r is not choisie and not est_tout(r["arbre1"]):
                non_int.append(_non_interpretee(r, "portée particulière non prise en charge (une seule valeur "
                                                   "pour la carte)"))
        valeurs[t] = choisie["valeur"] if choisie else None
    res["garde_bord"] = valeurs["BoardOutlineClearance"]
    res["masque_serigraphie"] = valeurs["SilkToSolderMaskClearance"]
    if valeurs["PlaneClearance"] is not None and valeurs["PlaneConnect"] is not None:
        res["connexion_plans"] = {"isolement": valeurs["PlaneClearance"], "connexion": valeurs["PlaneConnect"]}
    elif valeurs["PlaneClearance"] is not None or valeurs["PlaneConnect"] is not None:
        manque = "PlaneConnect" if valeurs["PlaneConnect"] is None else "PlaneClearance"
        avert.append(f"aucune règle {manque} qui couvre un via quelconque : connexion_plans laissé à null")
    # salles (lecture provisoire : contour VX<i>/VY<i>, CONFINEMENTSTYLE, LAYER)
    x0, y0 = (list(contexte.get("origine_mil") or ()) + [0.0, 0.0])[:2]
    for r in par_type.get("RoomDefinition", []):
        v = r["valeurs"]
        style = str(v.get("CONFINEMENTSTYLE") or "").strip()
        if "out" in style.casefold():
            non_int.append(_non_interpretee(r, f"salle d'exclusion (« {style} ») non prise en charge"))
            continue
        poly, i = [], 0
        while f"VX{i}" in v:
            x, y = longueur_mm(v.get(f"VX{i}")), longueur_mm(v.get(f"VY{i}"))
            if x is None or y is None:
                poly = []
                break
            poly.append([round(x - x0 * MIL_MM, 6), round(y - y0 * MIL_MM, 6)])
            i += 1
        if len(poly) < 3:
            non_int.append(_non_interpretee(r, "valeurs non reconnues : contour de salle illisible"))
            continue
        couche = str(v.get("LAYER") or "").strip().upper()
        face = "dessus" if couche == "TOP" else ("dessous" if couche == "BOTTOM" else None)
        membres = [p.get("designateur") for p in pieces
                   if evaluer(r["arbre1"], profil_piece(p.get("designateur"), p.get("face") or "dessus", index,
                                                        p.get("empreinte")))]
        res["salles"].append({"nom": r["nom"], "pieces": membres, "polygone": poly, "face": face})
    return res


def _regles_paires(par_type, contexte, index, couches, reseaux, non_int, avert, autres):
    """DiffPairsRouting -> écart et largeurs par paire ; MatchedLengths -> tolerances_longueur des paires."""
    out = {}
    paires = list(contexte.get("paires") or ())

    def lire_paire(v):
        if any(k in v for k in ("MINWIDTH", "PREFWIDTH", "MAXWIDTH")):
            gen = [longueur_mm(v.get(k)) for k in ("MINWIDTH", "PREFWIDTH", "MAXWIDTH")]
            if None in gen:
                raise ValueError("MINWIDTH, PREFWIDTH ou MAXWIDTH illisible")
            return gen, None
        return largeurs_paire(v, couches)
    dpr = _lire_valeurs(par_type.get("DiffPairsRouting"), lire_paire, non_int, avert)
    for p in paires:
        r = premiere_regle(dpr, profil_routeur(p.get("positif"), "piste", index, couches))
        if r is None:
            continue
        v = r["valeurs"]
        out[p.get("nom")] = {"ecart_mm": longueur_mm(v.get("MOSTFREQGAP")), "ecart_min_mm": longueur_mm(v.get("MINLIMIT")),
                             "ecart_max_mm": longueur_mm(v.get("MAXLIMIT")), "largeurs": list(r["valeur"])}
    ml = _lire_valeurs(par_type.get("MatchedLengths"),
                       lambda v: (_longueur_exigee(v.get("TOLERANCE"), "TOLERANCE"), None), non_int, avert)
    dans_paire = {n for p in paires for n in (p.get("positif"), p.get("negatif"))}
    for r in ml:
        for p in paires:
            if all(evaluer(r["arbre1"], profil_routeur(p.get(s), "piste", index, couches))
                   for s in ("positif", "negatif")):
                autres["tolerances_longueur"].setdefault(p.get("nom"), r["valeur"])
        if any(evaluer(r["arbre1"], profil_routeur(n, "piste", index, couches)) for n in reseaux if n not in dans_paire):
            non_int.append(_non_interpretee(r, "longueurs appariées hors paires différentielles non prises en charge"))
    return out
