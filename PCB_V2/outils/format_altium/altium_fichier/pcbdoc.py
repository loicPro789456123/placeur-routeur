"""Modèle de haut niveau d'un .PcbDoc : lecture, accès typé, modifications, écriture.

    from altium_fichier import PcbDoc, ErreurPcbDoc
    doc = PcbDoc.ouvrir("carte.PcbDoc")      # ErreurPcbDoc (nom de fichier seul) si ce n'est pas un .PcbDoc
    doc.designateurs()                       # {rang Components6: désignateur} (règle du contrat, §2.2)
    doc.pastilles()                          # formes par couche, perçage, masque...
    doc.couches_cuivre()                     # couches de cuivre dans l'ordre physique
    doc.deplacer_composant("U1", dx_mil=50)  # déplace le composant et toutes ses primitives
    doc.enregistrer("sortie.PcbDoc")

Principe : chaque flux est décodé en objets (flux.py) ; à l'écriture, chaque flux est ré-encodé. Un flux non
modifié redonne exactement ses octets d'origine. Un stockage connu absent du fichier se lit comme un flux vide
(`data()` rend l'objet vide du bon type) ; il n'est créé à l'écriture que s'il a reçu des enregistrements.

Unités : mil et repère du fichier (coordonnées absolues de la feuille) ; noms de couches Altium V6 (TOP, MID1,
PLANE1, BOTTOM, MULTILAYER...) ; `couches_cuivre()` donne en plus les noms du contrat (TopLayer, MidLayer<n>,
InternalPlane<n>, BottomLayer) et le rang physique. Aucune décision n'est prise ici (refus, rôles, conversion de
l'octogone en polygone) : c'est le rôle de l'importeur.
"""
from __future__ import annotations

import math
import os
import re
import struct
import uuid

import olefile

from . import cfb, chemins, couches, pose
from . import gabarits as G
from .cles import dictionnaire
from .flux import (ChainePascal, FluxBrut, FluxChaineUtf16, FluxChainesLarges, FluxCompteur,
                   FluxGestionnairePercage, FluxModele3D, FluxNomsUnions, FluxPrimitives, FluxProps, Primitive,
                   SousBloc, SousRegion, decoder_flux, flux_vide)
from .proprietes import (Proprietes, booleen, codes_vers_texte, formater_mil, interne_vers_mil, mil_vers_interne,
                         valeur_mil)

_MAX_COORD = 2 ** 31 - 1  # coordonnées binaires : i32 en 1/10000 mil

# flux de primitives -> code PrimitiveGuids (octet bas = type d'objet, octet haut = rang du flux)
FLUX_PRIMITIVES_GEO = ["Arcs6", "Pads6", "Vias6", "Tracks6", "Texts6", "Fills6", "Regions6",
                       "ShapeBasedRegions6", "ComponentBodies6", "ShapeBasedComponentBodies6"]

AUCUN = 0xFFFF  # index « aucun » (réseau, polygone, composant) des primitives binaires
POLYGONE_RETRAIT = 0xFFFE  # pistes et arcs du contour de retrait d'un plan négatif (générés par Altium)

# Genre d'une pièce d'après COMPONENTKIND (annexe E-6 du contrat, convention provisoire) ; absent = 0.
GENRES_COMPOSANT = {0: "standard", 1: "mecanique", 2: "graphique", 3: "liaison_reseaux", 4: "liaison_reseaux",
                    5: "standard", 6: "cavalier"}

# Bits de restriction des keep-out (annexe E-4, convention provisoire ; même table que l'importeur KiCad).
BITS_KEEPOUT = ((0x01, "vias"), (0x02, "pistes"), (0x04, "remplissages"), (0x08, "pastilles_cms"),
                (0x10, "pastilles_traversantes"))

# Accesseurs de lecture (sans argument) : liste unique, utilisée par les tests et les outils.
ACCESSEURS = ("reseaux", "composants", "designateurs", "commentaires", "origine", "pastilles", "pistes", "vias",
              "arcs", "remplissages", "regions", "textes", "polygones", "classes", "regles", "empilage",
              "couches_cuivre", "plans_negatifs", "sous_empilages", "empilage_maitre", "regions_carte",
              "aire_region_carte", "cartes_imbriquees", "contour_carte", "corps_3d", "paires_differentielles",
              "types_couches_mecaniques", "chaines_larges")


class ErreurPcbDoc(Exception):
    """Erreur d'entrée : fichier introuvable ou qui n'est pas un .PcbDoc, référence inconnue (couche, réseau).
    Le message est en français et ne contient que le nom du fichier, jamais son chemin (§0.2-7)."""


nom_seul = chemins.nom_seul


def effets_keepout(bits) -> list:
    """Bits de restriction d'un keep-out -> effets (table provisoire de l'annexe E-4)."""
    return [nom for masque, nom in BITS_KEEPOUT if (bits or 0) & masque]


def _entier(txt, defaut=None):
    try:
        return int(str(txt).strip())
    except (TypeError, ValueError):
        return defaut


def _flottant(txt, defaut=None):
    try:
        return float(str(txt).strip())
    except (TypeError, ValueError):
        return defaut


def _aire_mm2(points_mil) -> float:
    """Aire (valeur absolue, formule du lacet) d'un polygone en mil, en mm²."""
    s = 0.0
    n = len(points_mil)
    for i in range(n):
        x1, y1 = points_mil[i]
        x2, y2 = points_mil[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2 * 0.0254 ** 2


class PcbDoc:
    def __init__(self, racine: cfb.Noeud, chemin=None):
        self.racine = racine
        self.chemin = chemin
        self.nom = os.path.basename(os.fspath(chemin)) if chemin is not None else "carte"
        self.flux = {}
        for c, n in racine.flux():
            self.flux[c] = decoder_flux(c, n.donnees, dictionnaire)
        self._modifies = set()
        self._absents = {}  # stockage connu absent du fichier -> objet vide (créé à l'écriture s'il est rempli)
        self._cache_reseaux = None
        self._cache_cuivre = None   # identifiants V6 des couches de cuivre de l'empilage (invalidé : Board6)
        self._longueurs = {}        # longueur des sous-enregistrements neufs, par flux
        self._n_guids = 0           # compteur des GUID neufs (nouveau_guid)
        self._espace_guid = None
        # stockages dont le Header compte exactement les enregistrements de Data (mis à jour à l'écriture) ;
        # les autres Header (options : 0, DrillManager : 1, PrimitiveParameters...) ne sont jamais touchés
        self._compteurs = set()
        for c, h in self.flux.items():
            parties = c.split("/")
            if len(parties) == 2 and parties[1] == "Header" and isinstance(h, FluxCompteur) and h.valeur is not None \
                    and parties[0] != "DrillManager":
                d = self.flux.get(parties[0] + "/Data")
                try:
                    if d is not None and len(d) == h.valeur:
                        self._compteurs.add(parties[0])
                except TypeError:
                    pass

    # ------------------------------------------------------------------ E/S
    @classmethod
    def ouvrir(cls, chemin):
        """Lit un .PcbDoc. ErreurPcbDoc (nom seul) : fichier introuvable, pas un conteneur OLE2, conteneur
        illisible, ou Board6 absent ou vide (pas une carte Altium)."""
        nom = os.path.basename(os.fspath(chemin))
        if not os.path.isfile(chemin):
            raise ErreurPcbDoc(f"{nom} : fichier introuvable")
        try:
            ole = olefile.isOleFile(chemin)
        except OSError:
            ole = False
        if not ole:
            raise ErreurPcbDoc(f"{nom} : ce n'est pas un fichier .PcbDoc (conteneur OLE2 attendu)")
        try:
            racine = cfb.lire_cfb(chemin)
        except Exception as e:  # olefile lève des exceptions variées sur un conteneur abîmé
            raise ErreurPcbDoc(f"{nom} : conteneur OLE2 illisible ({type(e).__name__})") from None
        doc = cls(racine, chemin)
        b = doc.flux.get("Board6/Data")
        if not isinstance(b, FluxProps) or not len(b) or b.enregistrements[0].props is None:
            raise ErreurPcbDoc(f"{nom} : flux Board6 absent ou vide (ce n'est pas une carte Altium)")
        return doc

    def octets_flux(self, chemin) -> bytes:
        return self.flux[chemin].encoder()

    def enregistrer(self, chemin):
        """Ré-encode tous les flux, met à jour les compteurs Header des stockages modifiés, crée les stockages
        absents qui ont reçu des enregistrements (Header + Data), écrit le CFB de façon atomique (`<nom>.tmp`
        puis `os.replace`, réessayé 5 fois toutes les 0,2 s : cfb.ecrire_atomique). ErreurPcbDoc si `chemin`
        désigne le fichier ouvert (le fichier lu n'est jamais remplacé)."""
        if self.chemin is not None and _meme_fichier(chemin, self.chemin):
            raise ErreurPcbDoc(f"{self.nom} : écriture sur le fichier ouvert refusée (choisir un autre fichier)")
        for st, f in list(self._absents.items()):
            if len(f):
                self.racine.ecrire(st + "/Header", struct.pack("<I", len(f)))
                self.racine.ecrire(st + "/Data", b"")
                self.flux[st + "/Header"] = FluxCompteur(struct.pack("<I", len(f)))
                self.flux[st + "/Data"] = f
                self._compteurs.add(st)
                self._modifies.add(st)
                del self._absents[st]
        for st in self._modifies:
            if st not in self._compteurs:
                continue
            d = self.flux.get(st + "/Data")
            h = self.flux.get(st + "/Header")
            if isinstance(h, FluxCompteur) and h.valeur is not None and d is not None:
                h.valeur = len(d)
        # DrillManager : Header = 1 (une table) dès qu'il a un groupe, quel que soit leur nombre (rev3 : 17)
        h, d = self.flux.get("DrillManager/Header"), self.flux.get("DrillManager/Data")
        if "DrillManager" in self._modifies and isinstance(h, FluxCompteur) and h.valeur == 0 and d is not None \
                and len(d):
            h.valeur = 1
        for c, n in self.racine.flux():
            n.donnees = self.flux[c].encoder()
        cfb.ecrire_atomique(chemin, cfb.ecrire_cfb(self.racine))

    def marquer(self, storage):
        """Signale un stockage modifié (son Header sera recompté à l'écriture). Obligatoire après une
        modification de Nets6 (table des réseaux mémorisée) ou de l'empilage dans Board6."""
        self._modifies.add(storage)
        if storage == "Nets6":
            self._cache_reseaux = None
        elif storage == "Board6":
            self._cache_cuivre = None

    def nouveau_guid(self, objet="") -> uuid.UUID:
        """GUID neuf et déterministe (UUID version 5) : dérivé du GUID du document (FileHeaderSix ; à défaut, du
        nom du fichier), de `objet` et d'un compteur d'appels. Même carte et mêmes ajouts dans le même ordre :
        mêmes GUID, donc fichier identique octet pour octet ; deux appels ne rendent jamais le même GUID."""
        if self._espace_guid is None:
            g = getattr(self.flux.get("FileHeaderSix"), "guid", None)
            try:
                self._espace_guid = uuid.UUID(str(g).strip("{}"))
            except ValueError:
                self._espace_guid = uuid.uuid5(uuid.NAMESPACE_URL, "altium_fichier/" + self.nom)
        self._n_guids += 1
        return uuid.uuid5(self._espace_guid, f"{objet}/{self._n_guids}")

    # ------------------------------------------------------------ raccourcis
    def existe(self, storage) -> bool:
        """Le stockage (ou flux de premier niveau) est présent dans le fichier lu."""
        return storage in self.racine.enfants

    def data(self, storage):
        """Objet décodé du flux `<storage>/Data`. Stockage connu absent du fichier : objet vide du type du flux
        plein (toujours le même objet, créé à l'écriture s'il reçoit des enregistrements) ; stockage inconnu
        absent, ou présent sans flux Data : None."""
        f = self.flux.get(storage + "/Data")
        if f is not None:
            return f
        f = self._absents.get(storage)
        if f is not None or self.existe(storage):
            return f
        f = flux_vide(storage, dictionnaire)
        if f is not None:
            self._absents[storage] = f
        return f

    @property
    def board(self) -> Proprietes:
        return self.data("Board6")[0]

    def chaines_larges(self):
        f = self.data("WideStrings6")
        return f.dict() if f is not None and hasattr(f, "dict") else {}

    def _proprietes(self, storage):
        """Enregistrements de propriétés texte d'un flux (les enregistrements binaires sont sautés)."""
        f = self.data(storage)
        if not isinstance(f, FluxProps):
            return []
        return [e.props for e in f if e.props is not None]

    def _prims(self, storage):
        f = self.data(storage)
        return f.primitives if isinstance(f, FluxPrimitives) else []

    # ------------------------------------------------------------ réseaux
    def _table_reseaux(self):
        """(clé de validité, noms, {nom: premier index}) mémorisés : invalidés par marquer("Nets6"), ou si le
        flux Nets6 est remplacé ou change de longueur."""
        f = self.data("Nets6")
        cle = (id(f), len(f) if f is not None else 0)
        if self._cache_reseaux is None or self._cache_reseaux[0] != cle:
            noms = [p.texte("NAME") if p is not None else None for p in (e.props for e in f)] if f is not None else []
            index = {}
            for i, n in enumerate(noms):
                index.setdefault(n, i)
            self._cache_reseaux = (cle, noms, index)
        return self._cache_reseaux

    def reseaux(self):
        return list(self._table_reseaux()[1])

    def index_reseau(self, nom):
        """Index Nets6 d'un réseau (0xFFFF pour None) ; ErreurPcbDoc si le réseau est inconnu."""
        if nom is None:
            return AUCUN
        i = self._table_reseaux()[2].get(nom)
        if i is None:
            raise ErreurPcbDoc(f"{self.nom} : réseau inconnu : {nom}")
        return i

    def nom_reseau(self, idx):
        r = self._table_reseaux()[1]
        return r[idx] if idx is not None and 0 <= idx < len(r) else None

    # ------------------------------------------------------------ composants
    def _textes_composants(self, drapeau):
        """{rang Components6: texte} des textes de composant marqués `drapeau` (est_designateur, est_commentaire) ;
        texte de WideStrings6 (index_chaine_large) s'il existe, sinon la copie 8 bits du sous-enregistrement 2 ;
        un composant à plusieurs textes garde le dernier (comme pcbdoc_serigraphie.designateurs)."""
        larges = self.chaines_larges()
        n = len(self.data("Components6"))
        out = {}
        for pr in self._prims("Texts6"):
            b = pr.bloc
            c = b.get("composant", AUCUN)
            if not b.get(drapeau) or c == AUCUN or c >= n:
                continue
            idx = b.get("index_chaine_large")
            t = larges.get(idx) if idx is not None else None
            if t is None and len(pr.sous) > 1:
                t = getattr(pr.sous[1], "texte", None)
            out[c] = t
        return out

    def designateurs(self):
        """{rang Components6: désignateur} selon le contrat (§2.2) : texte du désignateur affiché (Texts6
        est_designateur, chaîne large comprise), sinon SOURCEDESIGNATOR, sinon "__piece_<rang>". Un texte vide
        ou fait d'espaces compte comme absent. Les doublons sont rendus tels quels (refus : importeur)."""
        f = self.data("Components6")
        textes = self._textes_composants("est_designateur")
        out = {}
        for i, e in enumerate(f):
            t = textes.get(i)
            if not (t and t.strip()):
                t = e.props.texte("SOURCEDESIGNATOR") if e.props is not None else None
            out[i] = t if (t and t.strip()) else f"__piece_{i}"
        return out

    def commentaires(self):
        """{rang Components6: texte du commentaire affiché (Texts6 est_commentaire), "" s'il n'y en a pas}."""
        textes = self._textes_composants("est_commentaire")
        return {i: textes.get(i) or "" for i in range(len(self.data("Components6")))}

    def origine(self):
        """Origine de la carte affichée par Altium (Board6 ORIGINX, ORIGINY), en mil ; (0, 0) si absente."""
        p = self.board
        return valeur_mil(p.get("ORIGINX"), 0.0), valeur_mil(p.get("ORIGINY"), 0.0)

    def composants(self):
        f = self.data("Components6")
        D = self.designateurs()
        C = self.commentaires()
        out = []
        for i, e in enumerate(f):
            p = e.props if e.props is not None else Proprietes()
            genre = _entier(p.get("COMPONENTKIND"), 0)
            out.append({
                "index": i,
                "designateur": D[i],
                "designateur_source": p.texte("SOURCEDESIGNATOR") or "",
                "empreinte": p.texte("PATTERN"),
                "x_mil": valeur_mil(p.get("X")),
                "y_mil": valeur_mil(p.get("Y")),
                "rotation": _flottant(p.get("ROTATION"), 0.0),
                "face": p.get("LAYER"),
                "verrouille": booleen(p.get("LOCKED"), False),
                "unique_id": p.get("UNIQUEID"),
                "lien_schema": p.get("SOURCEUNIQUEID"),
                "bibliotheque": p.texte("SOURCEFOOTPRINTLIBRARY"),
                "hauteur_mil": valeur_mil(p.get("HEIGHT")),
                "genre_altium": genre,
                "genre": GENRES_COMPOSANT.get(genre),
                "description": p.texte("FOOTPRINTDESCRIPTION") or "",
                "reference": p.texte("SOURCELIBREFERENCE") or "",
                "commentaire": C.get(i, ""),
                "designateur_visible": booleen(p.get("NAMEON"), True),
                "commentaire_visible": booleen(p.get("COMMENTON"), False),
            })
        return out

    def index_composant(self, designateur):
        for i, d in self.designateurs().items():
            if d == designateur:
                return i
        raise KeyError(f"composant inconnu : {designateur}")

    def composant(self, designateur) -> Proprietes:
        return self.data("Components6")[self.index_composant(designateur)]

    def primitives_du_composant(self, idx):
        for st in FLUX_PRIMITIVES_GEO:
            for pr in self._prims(st):
                if pr.bloc.get("composant") == idx:
                    yield st, pr

    # ------------------------------------------------------------ primitives
    def pistes(self):
        out = []
        for i, p in enumerate(self._prims("Tracks6")):
            b = p.bloc
            out.append({"index": i, "couche": couches.nom_couche(b["couche"], b.get("couche_v7")),
                        "x1": interne_vers_mil(b["x1"]), "y1": interne_vers_mil(b["y1"]),
                        "x2": interne_vers_mil(b["x2"]), "y2": interne_vers_mil(b["y2"]),
                        "largeur": interne_vers_mil(b["largeur"]), "reseau": self.nom_reseau(b["reseau"]),
                        "composant": b["composant"], "polygone": b["polygone"],
                        "verrouille": not (b["drapeaux1"] & 0x04), "keepout": b["drapeaux2"] == 2,
                        "restrictions_keepout": b.get("restrictions_keepout", 0) if b["drapeaux2"] & 2 else 0})
        return out

    def arcs(self):
        out = []
        for i, p in enumerate(self._prims("Arcs6")):
            b = p.bloc
            out.append({"index": i, "couche": couches.nom_couche(b["couche"], b.get("couche_v7")),
                        "cx": interne_vers_mil(b["cx"]), "cy": interne_vers_mil(b["cy"]),
                        "rayon": interne_vers_mil(b["rayon"]), "angle_debut": b["angle_debut"],
                        "angle_fin": b["angle_fin"], "largeur": interne_vers_mil(b["largeur"]),
                        "reseau": self.nom_reseau(b["reseau"]), "composant": b["composant"],
                        "polygone": b["polygone"], "verrouille": not (b["drapeaux1"] & 0x04),
                        "keepout": bool(b["drapeaux2"] & 2),
                        "restrictions_keepout": b.get("restrictions_keepout", 0) if b["drapeaux2"] & 2 else 0})
        return out

    def remplissages(self):
        out = []
        for i, p in enumerate(self._prims("Fills6")):
            b = p.bloc
            out.append({"index": i, "couche": couches.nom_couche(b["couche"], b.get("couche_v7")),
                        "x1": interne_vers_mil(b["x1"]), "y1": interne_vers_mil(b["y1"]),
                        "x2": interne_vers_mil(b["x2"]), "y2": interne_vers_mil(b["y2"]),
                        "rotation": b["rotation"], "reseau": self.nom_reseau(b["reseau"]),
                        "composant": b["composant"], "polygone": b["polygone"],
                        "verrouille": not (b["drapeaux1"] & 0x04), "keepout": bool(b["drapeaux2"] & 2),
                        "restrictions_keepout": b.get("restrictions_keepout", 0) if b["drapeaux2"] & 2 else 0})
        return out

    def vias(self):
        out = []
        for i, p in enumerate(self._prims("Vias6")):
            b = p.bloc
            out.append({"index": i, "x": interne_vers_mil(b["x"]), "y": interne_vers_mil(b["y"]),
                        "diametre": interne_vers_mil(b["diametre"]), "trou": interne_vers_mil(b["trou"]),
                        "de": couches.NOMS_V6.get(b["couche_debut"]), "a": couches.NOMS_V6.get(b["couche_fin"]),
                        "reseau": self.nom_reseau(b["reseau"]),
                        "tenting_haut": bool(b["drapeaux1"] & 0x20), "tenting_bas": bool(b["drapeaux1"] & 0x40),
                        "verrouille": not (b["drapeaux1"] & 0x04), "composant": b["composant"],
                        "polygone": b["polygone"], "keepout": bool(b["drapeaux2"] & 2),
                        "mode_pile": b.get("mode_pile", 0)})
        return out

    def pastilles(self):
        """Pastilles (Pads6), coordonnées absolues en mil. Clés historiques conservées (`taille_haut`,
        `forme_haut`, `trou` = diamètre en mil, `forme_trou`...) ; ajouts : `rang` dans la pièce (ordre du
        fichier), `traversante`, `couches` (cuivre de la pastille, ordre physique), `formes` par couche,
        `percage` (None pour une CMS), `masque`, `pate`, `verrouille`."""
        signal = [c["v6"] for c in self.couches_cuivre() if c["type"] == "signal"]
        rangs = {}
        out = []
        for i, p in enumerate(self._prims("Pads6")):
            b = p.bloc
            pile = p.sous[5].bloc if len(p.sous) > 5 and isinstance(p.sous[5], SousBloc) else None
            comp = b["composant"]
            rang = None
            if comp != AUCUN:
                rang = rangs.get(comp, 0)
                rangs[comp] = rang + 1
            v6 = b["couche"]
            mode = b["mode_pile"]
            if v6 == couches.IDS_V6["MULTILAYER"]:
                candidates = signal
            elif couches.nom_contrat_v6(v6) is not None:
                candidates = [v6]
            else:
                candidates = []
            formes = {}
            for c in candidates:
                fc = _forme_couche(b, pile, c, mode)
                if fc is not None:
                    formes[couches.NOMS_V6[c]] = fc
            out.append({
                "index": i, "nom": getattr(p.sous[0], "texte", None) or "", "composant": comp, "rang": rang,
                "x": interne_vers_mil(b["x"]), "y": interne_vers_mil(b["y"]),
                "couche": couches.nom_couche(v6),
                "taille_haut": (interne_vers_mil(b["taille_haut_x"]), interne_vers_mil(b["taille_haut_y"])),
                "taille_milieu": (interne_vers_mil(b["taille_milieu_x"]), interne_vers_mil(b["taille_milieu_y"])),
                "taille_bas": (interne_vers_mil(b["taille_bas_x"]), interne_vers_mil(b["taille_bas_y"])),
                "forme_haut": G.FORMES_PASTILLE.get(b["forme_haut"]),
                "forme_milieu": G.FORMES_PASTILLE.get(b["forme_milieu"]),
                "forme_bas": G.FORMES_PASTILLE.get(b["forme_bas"]),
                "trou": interne_vers_mil(b["trou"]), "metallise": bool(b["metallise"]),
                "forme_trou": G.FORMES_TROU.get(pile.get("forme_trou")) if pile else "rond",
                "rotation": b["rotation"], "reseau": self.nom_reseau(b["reseau"]),
                "mode_pile": mode,
                "masque_mode": b.get("masque_mode"), "pate_mode": b.get("pate_mode"),
                "traversante": v6 == couches.IDS_V6["MULTILAYER"],
                "couches": list(formes),
                "formes": formes,
                "percage": _percage(b, pile),
                "masque": _masque(b),
                "pate": {"mode": b.get("pate_mode"),
                         "expansion": interne_vers_mil(b["pate_expansion"]) if "pate_expansion" in b.v else None},
                "verrouille": not (b["drapeaux1"] & 0x04),
            })
        return out

    def textes(self):
        larges = self.chaines_larges()
        out = []
        for i, p in enumerate(self._prims("Texts6")):
            b = p.bloc
            idx = b.get("index_chaine_large")
            t = larges.get(idx, getattr(p.sous[1], "texte", None) if len(p.sous) > 1 else None)
            out.append({"index": i, "texte": t, "couche": couches.nom_couche(b["couche"], b.get("couche_v7")),
                        "x": interne_vers_mil(b["x"]), "y": interne_vers_mil(b["y"]),
                        "hauteur": interne_vers_mil(b["hauteur"]), "rotation": b["rotation"],
                        "miroir": bool(b["miroir"]), "composant": b["composant"],
                        "designateur": bool(b.get("est_designateur")), "commentaire": bool(b.get("est_commentaire")),
                        "police": "TrueType" if b.get("type_police") == 1 else ("code-barres" if b.get("type_police") == 2 else "trait"),
                        "nom_police": b.get("nom_police")})
        return out

    def regions(self):
        """Régions (Regions6) : cuivre, découpes de carte, keep-out, découpes de polygone. Contour et trous en mil
        (sommets du flux Regions6 ; le jumeau de même rang de ShapeBasedRegions6 porte les arcs)."""
        out = []
        for i, pr in enumerate(self._prims("Regions6")):
            s0 = pr.sous[0]
            if not isinstance(s0, SousRegion):
                continue
            e, p = s0.entete, s0.props
            keepout = bool(e["drapeaux2"] & 2) or booleen(p.get("KEEPOUT"), False)
            bits = _entier(p.get("KEEPOUTRESTRICTIONS"), 0) if keepout else 0
            out.append({"index": i, "couche": p.get("V7_LAYER") or couches.nom_couche(e["couche"]),
                        "reseau": self.nom_reseau(e["reseau"]), "composant": e["composant"],
                        "polygone": e["polygone"], "genre": _entier(p.get("KIND"), 0),
                        "decoupe_carte": booleen(p.get("ISBOARDCUTOUT"), False), "keepout": keepout,
                        "restrictions_keepout": bits, "effets_keepout": effets_keepout(bits),
                        "verrouille": not (e["drapeaux1"] & 0x04), "nom": p.texte("NAME"),
                        "sous_poly": _entier(p.get("SUBPOLYINDEX"), -1),
                        "contour": _points_region(s0), "trous": _trous_region(s0), "proprietes": p.dict()})
        return out

    def corps_3d(self):
        """Corps 3D (ComponentBodies6) : composant, couche, hauteurs (mil), modèle (nom seul), contour (mil)."""
        out = []
        for i, pr in enumerate(self._prims("ComponentBodies6")):
            s0 = pr.sous[0]
            if not isinstance(s0, SousRegion):
                continue
            e, p = s0.entete, s0.props
            out.append({"index": i, "composant": e["composant"],
                        "couche": p.get("V7_LAYER") or couches.nom_couche(e["couche"]),
                        "hauteur_totale_mil": valeur_mil(p.get("OVERALLHEIGHT")),
                        "hauteur_appui_mil": valeur_mil(p.get("STANDOFFHEIGHT")),
                        "projection": p.get("BODYPROJECTION"), "modele": nom_seul(p.texte("MODEL.NAME") or ""),
                        "identifiant_modele": p.get("MODELID"), "contour": _points_region(s0)})
        return out

    # ------------------------------------------------------------ carte
    def empilage(self):
        """Couches de l'empilage V9 (ordre du fichier) : nom, id, cuivre, diélectrique."""
        p = self.board.dict()
        idx = sorted({int(m.group(1)) for k in p if (m := re.match(r"V9_STACK_LAYER(\d+)_NAME$", k))})
        out = []
        for i in idx:
            g = lambda s: p.get(f"V9_STACK_LAYER{i}_{s}")
            lid = _entier(g("LAYERID"), 0)
            out.append({"rang": i, "nom": g("NAME"), "layerid": lid, "couche": couches.nom_v7(lid) if lid else None,
                        "cuivre_mil": valeur_mil(g("COPTHICK")), "type_dielectrique": g("DIELTYPE"),
                        "er": _flottant(g("DIELCONST")),
                        "epaisseur_dielectrique_mil": valeur_mil(g("DIELHEIGHT")),
                        "materiau": g("DIELMATERIAL"), "tangente_pertes": g("DIELLOSSTANGENT")})
        return out

    def couches_cuivre(self):
        """Couches de cuivre dans l'ordre physique, de haut en bas (rang 0 à N-1 d'après l'ordre de l'empilage
        V9_STACK_LAYER*, à défaut LAYER_V8_*, à défaut la chaîne LAYER<n>PREV/NEXT ; jamais d'après le numéro du
        nom). `nom` = nom du contrat (TopLayer, MidLayer<n>, InternalPlane<n>, BottomLayer, d'après
        l'identifiant de la couche), `nom_altium` = nom V6, `nom_fichier` = nom donné dans l'empilage, `type` =
        "signal" ou "plan_negatif", `reseau` = réseau d'un plan ("" sans réseau ou pour une couche de signal),
        `retrait_mil` = PULLBACKDISTANCE d'un plan (None si absent ; 0 pour une couche de signal)."""
        p = self.board.dict()
        entrees, source = _couches_v9(p), "V9"
        if not entrees:
            entrees, source = _couches_v8(p), "V8"
        if not entrees:
            entrees, source = _couches_v6(p), "V6"
        out = []
        for rang, (idx, v7, prefixe) in enumerate(entrees):
            v6 = couches.v6_depuis_v7(v7)
            plan = couches.est_plan_v7(v7)
            reseau = ""
            if plan:
                r = (self.board.texte(f"PLANE{v7 & 0xFFFF}NETNAME") or "").strip()
                reseau = "" if r in ("", "(No Net)") else r
            retrait = valeur_mil(p.get(prefixe + "PULLBACKDISTANCE")) if plan else 0.0
            out.append({"rang": rang, "nom": couches.nom_contrat_v7(v7), "nom_altium": couches.NOMS_V6.get(v6),
                        "nom_fichier": self.board.texte(prefixe + "NAME") or "",
                        "type": "plan_negatif" if plan else "signal", "reseau": reseau, "retrait_mil": retrait,
                        "epaisseur_cuivre_mil": valeur_mil(p.get(prefixe + "COPTHICK")),
                        "id_v7": v7, "v6": v6, "source": source, "index_empilage": idx})
        return out

    def plans_negatifs(self):
        """Par couche de plan négatif : réseau, retrait, régions de partage (polygones « Split Plane » de la
        couche : contour en mil, réseau) et lignes de partage (pistes et arcs libres de la couche, hors polygone
        et hors contour de retrait généré par Altium)."""
        polys = self.polygones()
        out = []
        for c in self.couches_cuivre():
            if c["type"] != "plan_negatif":
                continue
            nom, v6 = c["nom_altium"], c["v6"]
            regions = [{"index": q["index"], "nom": q["nom"], "reseau": q["reseau"] or "", "contour": q["contour"],
                        "reseau_du_plan": (q["reseau"] or "") == c["reseau"]}
                       for q in polys if q["couche"] == nom and (q["type"] or "").replace(" ", "").lower() == "splitplane"]
            lignes, retrait = [], 0
            for pr in self._prims("Tracks6"):
                b = pr.bloc
                if b["couche"] != v6 or b["composant"] != AUCUN or b["drapeaux2"] & 2:
                    continue
                if b["polygone"] == POLYGONE_RETRAIT:
                    retrait += 1
                elif b["polygone"] == AUCUN:
                    lignes.append({"type": "piste", "x1": interne_vers_mil(b["x1"]), "y1": interne_vers_mil(b["y1"]),
                                   "x2": interne_vers_mil(b["x2"]), "y2": interne_vers_mil(b["y2"]),
                                   "largeur": interne_vers_mil(b["largeur"])})
            for pr in self._prims("Arcs6"):
                b = pr.bloc
                if b["couche"] != v6 or b["composant"] != AUCUN or b["drapeaux2"] & 2:
                    continue
                if b["polygone"] == POLYGONE_RETRAIT:
                    retrait += 1
                elif b["polygone"] == AUCUN:
                    lignes.append({"type": "arc", "cx": interne_vers_mil(b["cx"]), "cy": interne_vers_mil(b["cy"]),
                                   "rayon": interne_vers_mil(b["rayon"]), "angle_debut": b["angle_debut"],
                                   "angle_fin": b["angle_fin"], "largeur": interne_vers_mil(b["largeur"])})
            out.append({"couche": c["nom"], "nom_altium": nom, "rang": c["rang"], "reseau": c["reseau"],
                        "retrait_mil": c["retrait_mil"], "regions": regions, "lignes_partage": lignes,
                        "traits_retrait": retrait})
        return out

    def sous_empilages(self):
        """Sous-empilages (V9_SUBSTACK<n>_*, à défaut LAYERSUBSTACK_V8_<n>*) : id, nom, flex (ISFLEX), type,
        utilise (USEDBYPRIMS), service."""
        p = self.board.dict()
        out = []
        for motif, pre in ((r"V9_SUBSTACK(\d+)_ID$", "V9_SUBSTACK{}_"), (r"LAYERSUBSTACK_V8_(\d+)ID$", "LAYERSUBSTACK_V8_{}")):
            idx = sorted({int(m.group(1)) for k in p if (m := re.match(motif, k))})
            for n in idx:
                g = lambda s, n=n: p.get(pre.format(n) + s)
                out.append({"rang": n, "id": g("ID"), "nom": self.board.texte(pre.format(n) + "NAME") or "",
                            "flex": booleen(g("ISFLEX"), False), "type": g("TYPE"),
                            "utilise": booleen(g("USEDBYPRIMS"), False), "service": booleen(g("SERVICE"), False)})
            if out:
                break
        return out

    def empilage_maitre(self):
        """Empilage maître (V9_MASTERSTACK_*, à défaut LAYERMASTERSTACK_V8*) : id, nom, flex, style."""
        p = self.board.dict()
        for pre in ("V9_MASTERSTACK_", "LAYERMASTERSTACK_V8"):
            if any(k.startswith(pre) for k in p):
                return {"id": p.get(pre + "ID"), "nom": self.board.texte(pre + "NAME") or "",
                        "flex": booleen(p.get(pre + "ISFLEX"), False), "style": p.get(pre + "STYLE")}
        return {"id": None, "nom": "", "flex": False, "style": None}

    def regions_carte(self):
        """Régions de carte (BoardRegions) : nom, id_empilage (LAYERSTACKID), sous_empilage (son nom),
        lignes_pliage (BENDINGLINECOUNT), contour et trous en mil, aire en mm² (contour moins trous)."""
        noms = {s["id"]: s["nom"] for s in self.sous_empilages()}
        out = []
        for i, pr in enumerate(self._prims("BoardRegions")):
            s0 = pr.sous[0]
            if not isinstance(s0, SousRegion):
                continue
            p = s0.props
            contour, trous = _points_region(s0), _trous_region(s0)
            aire = _aire_mm2(contour) - sum(_aire_mm2(t) for t in trous) if len(contour) >= 3 else 0.0
            n = _entier(p.get("BENDINGLINECOUNT"), 0)
            out.append({"index": i, "nom": p.texte("NAME") or "", "id_empilage": p.get("LAYERSTACKID"),
                        "sous_empilage": noms.get(p.get("LAYERSTACKID")), "lignes_pliage": n,
                        "lignes": [p.get(f"BENDINGLINE{k}") for k in range(n) if p.get(f"BENDINGLINE{k}") is not None],
                        "contour": contour, "trous": trous, "aire_mm2": aire,
                        "verrouille_3d": booleen(p.get("LOCKED3D"), False), "proprietes": p.dict()})
        return out

    def aire_region_carte(self):
        """Aire (mm²) de la région de carte écrite par Altium (BoardRegions) ; somme des régions s'il y en a
        plusieurs (carte rigide-flexible, que l'importeur refuse) ; None s'il n'y en a aucune."""
        r = self.regions_carte()
        return sum(x["aire_mm2"] for x in r) if r else None

    def cartes_imbriquees(self):
        """Enregistrements d'EmbeddedBoards6 (panneau) : document (nom seul du fichier de DOCUMENTPATH), lignes,
        colonnes, position, rotation, miroir, espacements (mil), propriétés brutes (DOCUMENTPATH réduit au
        nom du fichier : aucun chemin rendu)."""
        out = []
        for i, e in enumerate(self.data("EmbeddedBoards6")):
            p = e.props if e.props is not None else Proprietes()
            document = nom_seul(p.texte("DOCUMENTPATH") or "")
            props = p.dict()
            for k in list(props):
                if k.upper().endswith("DOCUMENTPATH"):
                    props[k] = document
            out.append({"index": i, "document": document, "lignes": _entier(p.get("ROWCOUNT"), 1),
                        "colonnes": _entier(p.get("COLCOUNT"), 1), "x_mil": valeur_mil(p.get("X")),
                        "y_mil": valeur_mil(p.get("Y")), "rotation": _flottant(p.get("ROTATION"), 0.0),
                        "miroir": booleen(p.get("MIRROR"), False),
                        "espacement_lignes_mil": valeur_mil(p.get("ROWSPACING")),
                        "espacement_colonnes_mil": valeur_mil(p.get("COLSPACING")), "proprietes": props})
        return out

    def contour_carte(self):
        return _contour(self.board)

    # clés de Rules6 communes à toutes les règles (les autres sont les valeurs propres au type)
    _CLES_REGLE = {"RULEKIND", "NAME", "ENABLED", "PRIORITY", "SCOPE1EXPRESSION", "SCOPE2EXPRESSION", "COMMENT",
                   "UNIQUEID", "NETSCOPE", "LAYERKIND", "SELECTION", "LAYER", "LOCKED", "POLYGONOUTLINE",
                   "USERROUTED", "KEEPOUT", "UNIONINDEX", "DEFINEDBYLOGICALDOCUMENT", "UNICODE", "SCOPECOUNT", ""}

    def regles(self):
        """Règles (Rules6) brutes : nom, type (RULEKIND), code (u16 du préfixe binaire), active, priorité,
        portées (texte exact), portée réseaux et couches, commentaire, identifiant, valeurs propres au type
        (texte décodé), contour (RoomDefinition), propriétés brutes."""
        f = self.data("Rules6")
        out = []
        for e in (f if isinstance(f, FluxProps) else []):
            code = struct.unpack("<H", e.prefixe)[0] if len(e.prefixe) == 2 else None
            p = e.props
            if p is None:
                out.append({"nom": None, "type": None, "code": code, "active": False, "priorite": None,
                            "portee1": None, "portee2": None, "prefixe_binaire": e.prefixe.hex(),
                            "portee_reseaux": None, "portee_couches": None, "commentaire": "", "identifiant": None,
                            "valeurs": {}, "contour": None, "proprietes": {}, "binaire": True})
                continue
            valeurs = {k: p.texte(k) for k in p.cles()
                       if k not in self._CLES_REGLE and not k.startswith(("UNICODE__", "%UTF8%"))
                       and not re.fullmatch(r"(KIND|VX|VY|CX|CY|SA|EA|R)\d+", k)}
            out.append({"nom": p.texte("NAME"), "type": p.get("RULEKIND"), "code": code,
                        "active": booleen(p.get("ENABLED")), "priorite": _entier(p.get("PRIORITY"), 0),
                        "portee1": p.texte("SCOPE1EXPRESSION"), "portee2": p.texte("SCOPE2EXPRESSION"),
                        "prefixe_binaire": e.prefixe.hex(), "portee_reseaux": p.get("NETSCOPE"),
                        "portee_couches": p.get("LAYERKIND"), "commentaire": p.texte("COMMENT") or "",
                        "identifiant": p.get("UNIQUEID"), "valeurs": valeurs,
                        "contour": _contour(p) if p.get("RULEKIND") == "RoomDefinition" else None,
                        "proprietes": p.dict(), "binaire": False})
        return out

    def classes(self):
        out = []
        for p in self._proprietes("Classes6"):
            membres = [p.texte(k) for k in p.cles() if re.fullmatch(r"M\d+", k)]
            out.append({"nom": p.texte("NAME"), "type": _entier(p.get("KIND"), -1),
                        "superclasse": booleen(p.get("SUPERCLASS")), "membres": membres})
        return out

    def polygones(self):
        out = []
        for i, p in enumerate(self._proprietes("Polygons6")):
            nom = codes_vers_texte(p.get("NAME")) or p.texte("NAME")
            out.append({"index": i, "nom": nom, "couche": p.get("LAYER") or "",
                        "reseau": self.nom_reseau(_entier(p.get("NET"), AUCUN)),
                        "type": p.get("POLYGONTYPE"), "hachures": p.get("HATCHSTYLE"), "coulee_dessus": p.get("POUROVER"),
                        "contour": _contour(p), "ordre": p.get("POURINDEX")})
        return out

    def paires_differentielles(self):
        """Paires différentielles (DifferentialPairs6) : nom, réseau positif, réseau négatif."""
        return [{"nom": p.texte("NAME") or "", "positif": p.texte("POSITIVENETNAME") or "",
                 "negatif": p.texte("NEGATIVENETNAME") or ""} for p in self._proprietes("DifferentialPairs6")]

    def types_couches_mecaniques(self):
        """{couche mécanique (nom V7) : type} : type texte de Board6 (clés *MECHKIND : « Courtyard »,
        « AssemblyTop »...) ; à défaut, code de LayerKindMapping sous la forme « code_<n> » (identifiants
        0x0400xxxx = mécanique xxxx, observé)."""
        p = self.board.dict()
        out = {}
        for k, v in p.items():
            m = re.fullmatch(r"(V9_STACK_LAYER|V9_CACHE_LAYER)(\d+)_MECHKIND|(LAYERV7_|LAYER_V8_)(\d+)MECHKIND", k)
            if not m:
                continue
            pre = f"{m.group(1)}{m.group(2)}_" if m.group(1) else f"{m.group(3)}{m.group(4)}"
            lid = _entier(p.get(pre + "LAYERID"), 0)
            if lid:
                out.setdefault(couches.nom_v7(lid), (v or "").strip())
        lk = self.data("LayerKindMapping")
        for ident, code in getattr(lk, "paires", None) or []:
            if ident >> 16 == 0x0400:
                out.setdefault(f"MECHANICAL{ident & 0xFFFF}", f"code_{code}")
        return out

    # ============================================================ MODIFICATIONS
    def deplacer_composant(self, designateur, dx_mil=0.0, dy_mil=0.0):
        """Translation d'un composant et de toutes ses primitives (pastilles, pistes, arcs,
        textes, remplissages, régions, corps 3D)."""
        idx = self.index_composant(designateur)
        dx, dy = mil_vers_interne(dx_mil), mil_vers_interne(dy_mil)
        p = self.data("Components6")[idx]
        p["X"] = formater_mil(valeur_mil(p["X"]) + dx_mil)
        p["Y"] = formater_mil(valeur_mil(p["Y"]) + dy_mil)
        self.marquer("Components6")
        n = 0
        for st, pr in list(self.primitives_du_composant(idx)):
            _translater(pr, dx, dy, dx_mil, dy_mil)
            self.marquer(st)
            n += 1
        return n

    def deplacer_texte(self, index, dx_mil=0.0, dy_mil=0.0):
        pr = self.data("Texts6")[index]
        dx, dy = mil_vers_interne(dx_mil), mil_vers_interne(dy_mil)
        _translater(pr, dx, dy, dx_mil, dy_mil)  # position + point de référence (ref_x/ref_y)
        self.marquer("Texts6")

    # ------------------------------------------------------------ ajouts de primitives
    def _v6_cuivre(self):
        if self._cache_cuivre is None:
            self._cache_cuivre = {c["v6"] for c in self.couches_cuivre()}
        return self._cache_cuivre

    def _couche_ajout(self, couche):
        """Identifiant V6 d'une couche donnée par son nom Altium (« TOP », « MID2 », « PLANE1 », « TOPOVERLAY »),
        son nom du contrat (« TopLayer », « MidLayer2 », « InternalPlane1 ») ou son identifiant V6. ErreurPcbDoc
        si elle est inconnue, ou si c'est une couche de cuivre absente de l'empilage de la carte."""
        v6 = couche if isinstance(couche, int) and not isinstance(couche, bool) else couches.v6_depuis_nom(str(couche))
        if not v6 or v6 not in couches.NOMS_V6:
            raise ErreurPcbDoc(f"{self.nom} : couche inconnue : {couche}")
        if couches.nom_contrat_v6(v6) is not None and v6 not in self._v6_cuivre():
            raise ErreurPcbDoc(f"{self.nom} : couche absente de l'empilage de la carte : {couche}")
        return v6

    def _interne(self, v_mil, quoi, positif=False):
        """mil -> unités internes (1/10000 mil, arrondi au plus proche) ; ErreurPcbDoc hors du domaine i32."""
        try:
            v = float(v_mil)
        except (TypeError, ValueError):
            raise ErreurPcbDoc(f"{self.nom} : {quoi} : nombre attendu") from None
        if not math.isfinite(v) or abs(v) * 1e4 > _MAX_COORD or (positif and v <= 0):
            raise ErreurPcbDoc(f"{self.nom} : {quoi} hors du domaine : {v_mil}")
        return mil_vers_interne(v)

    def _point(self, p, quoi):
        try:
            x, y = p
        except (TypeError, ValueError):
            raise ErreurPcbDoc(f"{self.nom} : {quoi} : couple (x, y) en mil attendu") from None
        return self._interne(x, quoi), self._interne(y, quoi)

    def _longueur_proto(self, storage, nom):
        """Longueur du sous-enregistrement neuf : celle du premier enregistrement existant du flux (même version
        d'Altium), sinon gabarits.LONGUEURS_PROTOTYPES."""
        n = self._longueurs.get(storage)
        if n is None:
            f = self._prims(storage)
            n = len(f[0].sous[0].encoder()) if f else G.LONGUEURS_PROTOTYPES[nom]
            self._longueurs[storage] = n
        return n

    def ajouter_piste(self, couche, debut_mil, fin_mil, largeur_mil, reseau=None):
        """Ajoute une piste libre (Tracks6) de `debut_mil` à `fin_mil` ((x, y) en mil, repère du fichier),
        de largeur `largeur_mil`, sur `couche` (nom Altium ou du contrat), au réseau `reseau` (nom ; None :
        aucun). Prototype construit depuis gabarits.VALEURS_PISTE (non verrouillée, routée par l'utilisateur),
        même sur une carte sans aucune piste. Rend son rang dans Tracks6. ErreurPcbDoc : couche ou réseau
        inconnu, valeur hors domaine."""
        v6 = self._couche_ajout(couche)
        x1, y1 = self._point(debut_mil, "début de piste")
        x2, y2 = self._point(fin_mil, "fin de piste")
        b = G.prototype(G.PISTE, G.VALEURS_PISTE, self._longueur_proto("Tracks6", "piste"), "piste",
                        couche=v6, couche_v7=couches.v7_depuis_v6(v6), reseau=self.index_reseau(reseau),
                        x1=x1, y1=y1, x2=x2, y2=y2, largeur=self._interne(largeur_mil, "largeur", True))
        return pose.ajouter_primitive(self, "Tracks6", Primitive(4, [SousBloc(b)]))

    def ajouter_via(self, pos_mil, diametre_mil, trou_mil, reseau=None):
        """Ajoute une via libre (Vias6) traversante (TOP à BOTTOM), en `pos_mil` ((x, y) en mil), de diamètre
        `diametre_mil` (toutes les couches) et de perçage `trou_mil`, au réseau `reseau` (nom ou None).
        Prototype construit depuis gabarits.VALEURS_VIA, même sur une carte sans via. Si DrillManager existe
        dans le fichier, la via est rattachée au groupe de via de même TOOLSIZE, créé s'il n'existe pas (copie du
        groupe de via le plus proche, sinon gabarit). Rend son rang dans Vias6. ErreurPcbDoc : réseau inconnu,
        valeur hors domaine, trou plus grand que le diamètre."""
        x, y = self._point(pos_mil, "position de via")
        d = self._interne(diametre_mil, "diamètre de via", True)
        t = self._interne(trou_mil, "trou de via", True)
        if t >= d:
            raise ErreurPcbDoc(f"{self.nom} : trou de via ({trou_mil} mil) >= diamètre ({diametre_mil} mil)")
        b = G.prototype(G.VIA, G.VALEURS_VIA, self._longueur_proto("Vias6", "via"), "via",
                        x=x, y=y, diametre=d, trou=t, reseau=self.index_reseau(reseau),
                        diametres_couches=G.coords_vers_octets([d] * 32),
                        guid_gabarit=str(self.nouveau_guid("via/gabarit")).upper())
        i = pose.ajouter_primitive(self, "Vias6", Primitive(3, [SousBloc(b)]))
        if self.existe("DrillManager"):
            dm = self.data("DrillManager")
            g = pose.groupe_via(dm, t / 1e4)
            if g is None:
                existants = [x for x in dm.groupes if x[0].get("OBJECTID") == "Via"]
                mod = min(existants, key=lambda x: abs((valeur_mil(x[0].get("TOOLSIZE")) or 0) - t / 1e4),
                          default=None)
                g = pose.nouveau_groupe_via(dm, t / 1e4, d / 1e4, mod[0] if mod else None)
            g[1][1].append(i)
            self.marquer("DrillManager")
        return i

    def ajouter_region(self, couche, contour_mil, reseau, trous=()):
        """Ajoute une région de cuivre libre sur `couche`, de contour `contour_mil` ([(x, y)] en mil, au moins
        3 sommets, non refermé) et de trous `trous` (liste de contours), au réseau `reseau` (nom ou None) :
        enregistrement Regions6 (sommets f64) et son jumeau de même rang dans ShapeBasedRegions6 (sommets
        étendus, sommet de fermeture), propriétés gabarits.PROPRIETES_REGION. Le jumeau n'est omis que si le
        fichier a des régions sans aucun ShapeBasedRegions6 (ancien format). Rend le rang dans Regions6.
        ErreurPcbDoc : couche ou réseau inconnu, contour de moins de 3 sommets, flux jumeaux de longueurs
        différentes."""
        v6 = self._couche_ajout(couche)
        idx = self.index_reseau(reseau)
        contour = [self._point(p, "sommet de région") for p in contour_mil]
        lt = [[self._point(p, "sommet de trou de région") for p in t] for t in trous]
        if len(contour) < 3 or any(len(t) < 3 for t in lt):
            raise ErreurPcbDoc(f"{self.nom} : région : contour et trous d'au moins 3 sommets attendus")
        r6, sb = self.data("Regions6"), self.data("ShapeBasedRegions6")
        jumeau = len(sb) == len(r6)
        if not jumeau and (self.existe("ShapeBasedRegions6") or len(sb)):
            raise ErreurPcbDoc(f"{self.nom} : Regions6 ({len(r6)}) et ShapeBasedRegions6 ({len(sb)}) de longueurs "
                               "différentes : ajout de région refusé")
        entete = G.prototype(G.ENTETE_REGION, G.VALEURS_ENTETE_REGION, 18, "entete_region",
                             couche=v6, reseau=idx, nb_trous=len(lt))
        props = Proprietes([[k, v] for k, v in G.PROPRIETES_REGION])
        props["V7_LAYER"] = couches.NOMS_V6[v6]
        tete = G.encoder_bloc(entete)
        pb = props.vers_octets()
        tete += struct.pack("<I", len(pb)) + pb
        fins = b"".join(struct.pack("<I", len(t)) + b"".join(struct.pack("<2d", x, y) for x, y in t) for t in lt)
        simple = tete + struct.pack("<I", len(contour)) + b"".join(struct.pack("<2d", x, y) for x, y in contour)
        i = pose.ajouter_primitive(self, "Regions6", Primitive(11, [SousRegion(simple + fins, False)]))
        if jumeau:
            etendu = tete + struct.pack("<I", len(contour)) + b"".join(
                struct.pack("<B5i2d", 0, x, y, 0, 0, 0, 0.0, 0.0) for x, y in contour + contour[:1])
            pose.ajouter_primitive(self, "ShapeBasedRegions6", Primitive(11, [SousRegion(etendu + fins, True)]))
        return i

    # ------------------------------------------------------------ chemins
    def effacer_chemins(self, tout=False):
        """Efface les chemins du fichier (§0.3). `tout=False` : FILENAME de Board6 vidé (il désigne le fichier
        d'entrée ; cartes produites par generer_pcbdoc). `tout=True` (fichiers publiés) : de plus, tout chemin
        absolu ramené à son nom seul, dans toutes les propriétés texte (SOURCEFOOTPRINTLIBRARY, DOCUMENTPATH,
        modèles 3D...), valeurs UNICODE__ et %UTF8%, données base64 + zlib (V9_STACKCUSTOMDATA,
        ConstraintManager), chaînes larges, chaînes des primitives, modèles STEP compressés, et flux bruts de
        structure inconnue (latin-1 et UTF-16, à longueur égale : nom seul suivi d'espaces). Rend le bilan des
        champs touchés : [{"flux", "champ", "nombre"}] (aucun chemin rendu)."""
        if not tout:
            bilan = {}
            f = self.data("Board6")
            for e in (f.enregistrements if isinstance(f, FluxProps) else []):
                for el in (e.props.elements if e.props is not None else []):
                    if _base_cle(el[0]) == "FILENAME" and el[1]:
                        el[1] = ""
                        bilan[("Board6/Data", el[0])] = bilan.get(("Board6/Data", el[0]), 0) + 1
            if bilan:
                self.marquer("Board6")
            return [{"flux": c, "champ": k, "nombre": n} for (c, k), n in sorted(bilan.items())]
        return self._parcourir_chemins(True)

    def chemins_absolus(self):
        """Champs qui contiennent encore un chemin absolu (même parcours que effacer_chemins(tout=True), sans
        rien modifier) : [{"flux", "champ", "nombre"}] ; [] si aucun. Les flux binaires (polices, GUID, cache
        des lettres) ne sont pas lus ; les flux bruts inconnus le sont avec le motif strict."""
        return self._parcourir_chemins(False)

    def _parcourir_chemins(self, modifier):
        bilan = {}

        def noter(flux, champ, n):
            bilan[(flux, champ)] = bilan.get((flux, champ), 0) + n

        for c, f in list(self.flux.items()):
            st = c.split("/")[0]
            touche = False
            if isinstance(f, FluxProps):
                fichier = modifier and c == "Board6/Data"
                for e in f.enregistrements:
                    if e.props is not None:
                        touche |= _chemins_props(e.props, modifier, lambda k, n: noter(c, k, n), fichier)
            elif isinstance(f, FluxGestionnairePercage):
                for p, _ in f.groupes:
                    touche |= _chemins_props(p, modifier, lambda k, n: noter(c, k, n))
            elif isinstance(f, FluxPrimitives):
                for pr in f.primitives:
                    for k, s in enumerate(pr.sous):
                        if isinstance(s, SousRegion):
                            touche |= _chemins_props(s.props, modifier, lambda kk, n: noter(c, kk, n))
                        elif isinstance(s, ChainePascal) and s.texte:
                            t, n = chemins.reduire(s.texte)
                            if n:
                                noter(c, f"{pr.nom_type}[{k}]", n)
                                if modifier:
                                    s.texte, touche = t, True
            elif isinstance(f, FluxChainesLarges):
                for ch in f.chaines:
                    if ch[1]:
                        t, n = chemins.reduire(ch[1])
                        if n:
                            noter(c, "chaine", n)
                            if modifier:
                                ch[1], touche = t, True
            elif isinstance(f, FluxNomsUnions):
                for nom in f.noms:
                    t, n = chemins.reduire(nom[1])
                    if n:
                        noter(c, "nom", n)
                        if modifier:
                            nom[1], touche = t, True
            elif isinstance(f, FluxModele3D):
                if f.valide:
                    t, n = chemins.reduire(f.step().decode("latin-1"))
                    if n:
                        noter(c, "STEP", n)
                        if modifier:
                            f.remplacer_step(t.encode("latin-1"))
                            touche = True
            elif isinstance(f, FluxChaineUtf16):
                if f.ok and f.texte:
                    coeur = f.texte.rstrip("\0")
                    t, n = chemins.reduire_valeur(coeur)
                    if n:
                        noter(c, "texte", n)
                        if modifier:
                            b = (t + f.texte[len(coeur):]).encode("utf-16-le")
                            self.flux[c] = FluxChaineUtf16(struct.pack("<I", len(b)) + b)
                            touche = True
            elif type(f) is FluxBrut:
                n = len(chemins.trouver_octets(f.octets))
                if n:
                    noter(c, "octets", n)
                    if modifier:
                        f.octets = chemins.reduire_octets(f.octets)[0]
                        touche = True
            if touche:
                self.marquer(st)
        return [{"flux": c, "champ": k, "nombre": n} for (c, k), n in sorted(bilan.items())]


# ---------------------------------------------------------------------------
# Chemins et fichiers
# ---------------------------------------------------------------------------
def _base_cle(cle):
    """Clé sans préfixe UNICODE__ ni %UTF8% (en majuscules)."""
    c = cle.upper()
    for pre in ("UNICODE__", "%UTF8%"):
        if c.startswith(pre):
            return c[len(pre):]
    return c


def _chemins_props(props, modifier, noter, effacer_fichier=False):
    """Chemins des valeurs d'une liste de propriétés (réduits au nom seul si `modifier`) ; FILENAME vidé si
    `effacer_fichier`. Rend True si une valeur a changé."""
    touche = False
    for el in props.elements:
        k, v = el
        if not v:
            continue
        if k.upper().startswith("UNICODE__"):
            nv, n = chemins.reduire_codes(v)
        else:
            nv, n = chemins.reduire_valeur(v)
        if effacer_fichier and _base_cle(k) == "FILENAME":
            nv, n = "", max(n, 1)
        if n:
            noter(k, n)
            if modifier:
                el[1] = nv
                touche = True
    return touche


def _meme_fichier(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ---------------------------------------------------------------------------
# Empilage : couches de cuivre dans l'ordre physique
# ---------------------------------------------------------------------------
def _couches_v9(p):
    """[(index, id V7, préfixe des clés)] des couches de cuivre de V9_STACK_LAYER<i>_*, dans l'ordre des i."""
    idx = sorted({int(m.group(1)) for k in p if (m := re.match(r"V9_STACK_LAYER(\d+)_LAYERID$", k))})
    out = []
    for i in idx:
        v7 = _entier(p.get(f"V9_STACK_LAYER{i}_LAYERID"), 0)
        if couches.est_cuivre_v7(v7):
            out.append((i, v7, f"V9_STACK_LAYER{i}_"))
    return out


def _couches_v8(p):
    """Repli : LAYER_V8_<i>* (ordre des i, supposé physique comme en V9 : vérifié sur nos cartes)."""
    idx = sorted({int(m.group(1)) for k in p if (m := re.match(r"LAYER_V8_(\d+)LAYERID$", k))})
    out = []
    for i in idx:
        v7 = _entier(p.get(f"LAYER_V8_{i}LAYERID"), 0)
        if couches.est_cuivre_v7(v7):
            out.append((i, v7, f"LAYER_V8_{i}"))
    return out


def _couches_v6(p):
    """Repli des anciens fichiers : chaîne LAYER<n>NEXT à partir de TOP (n = identifiant V6)."""
    out, n, vus = [], 1, set()
    while n and n not in vus and len(out) < 64:
        vus.add(n)
        if f"LAYER{n}NAME" not in p:
            break
        v7 = couches.v7_depuis_v6(n)
        if couches.est_cuivre_v7(v7):
            out.append((n, v7, f"LAYER{n}"))
        if n == 32:
            break
        n = _entier(p.get(f"LAYER{n}NEXT"), 0)
    return out if out and out[-1][1] == couches.v7_depuis_v6(32) else []


# ---------------------------------------------------------------------------
# Pastilles : forme par couche, perçage, masque
# ---------------------------------------------------------------------------
_RECT_ARRONDI = G.FORME_ALT_RECT_ARRONDI


def _forme_couche(b, pile, v6, mode):
    """Forme du cuivre d'une pastille sur la couche de cuivre `v6` (None si la taille est nulle : pas de cuivre).
    Mode 0 (simple) : valeurs « haut » partout ; 1 (haut-milieu-bas) : haut, milieu (couches internes), bas ;
    2 (pile complète) : haut, MID1 = milieu, MIDk (k >= 2) = tables de la pile [k-2], bas. Tables par couche de
    la pile (forme alternative, rayon de coin, décalage) : indice 0 = haut, k = MIDk, 31 = bas (indice 0 en
    mode simple)."""
    k = v6 - 1  # MIDk
    if mode == 0 or v6 == 1:
        fx, fy, f, idx = b["taille_haut_x"], b["taille_haut_y"], b["forme_haut"], 0
    elif v6 == 32:
        fx, fy, f, idx = b["taille_bas_x"], b["taille_bas_y"], b["forme_bas"], 31
    elif mode == 2 and k >= 2 and pile is not None and isinstance(pile.get("tailles_x_internes"), bytes):
        fx = G.tableau_coords(pile["tailles_x_internes"])[k - 2]
        fy = G.tableau_coords(pile["tailles_y_internes"])[k - 2]
        f, idx = pile["formes_internes"][k - 2], k
    else:
        fx, fy, f, idx = b["taille_milieu_x"], b["taille_milieu_y"], b["forme_milieu"], k
    if mode == 0:
        idx = 0
    if fx <= 0 or fy <= 0:
        return None
    alt = _octet(pile, "formes_alt", idx)
    pct = _octet(pile, "rayons_coins", idx)
    dx = _coord(pile, "decalages_trou_x", idx)
    dy = _coord(pile, "decalages_trou_y", idx)
    x, y = interne_vers_mil(fx), interne_vers_mil(fy)
    rayon = 0.0
    if alt == _RECT_ARRONDI:
        forme = "rect_arrondi"
        rayon = pct / 100.0 * min(x, y) / 2  # annexe E-1 (convention provisoire)
    elif f == 1:
        forme = "rond" if fx == fy else "oblong"
    elif f == 2:
        forme = "rect"
    elif f == 3:
        forme = "octogone"
    else:
        forme = "inconnue"
    return {"forme": forme, "taille": (x, y), "pourcentage": pct if alt == _RECT_ARRONDI else 0,
            "rayon_coin": rayon, "decalage": (interne_vers_mil(dx), interne_vers_mil(dy)),
            "code_forme": f, "code_forme_alt": alt}


def _octet(pile, champ, idx):
    if pile is None:
        return 0
    v = pile.get(champ)
    return v[idx] if isinstance(v, (bytes, bytearray)) and idx < len(v) else 0


def _coord(pile, champ, idx):
    if pile is None:
        return 0
    v = pile.get(champ)
    if not isinstance(v, (bytes, bytearray)) or 4 * idx + 4 > len(v):
        return 0
    return struct.unpack_from("<i", v, 4 * idx)[0]


def _percage(b, pile):
    """Trou d'une pastille (None pour une CMS) : diamètre, forme (rond, carre, oblong), longueur (= diamètre
    sauf oblong), rotation du trou telle qu'écrite (relative à la pastille, convention provisoire), décalage
    du cuivre de la couche haute par rapport au centre du trou (repère de la pastille), métallisation ; mil."""
    if b["trou"] <= 0:
        return None
    ft = pile.get("forme_trou", 0) if pile is not None else 0
    oblong = ft == 2 and pile is not None and "longueur_oblong" in pile.v
    return {"diametre": interne_vers_mil(b["trou"]), "forme": G.FORMES_TROU_CONTRAT.get(ft, "rond"),
            "longueur": interne_vers_mil(pile["longueur_oblong"]) if oblong else interne_vers_mil(b["trou"]),
            "rotation": pile.get("rotation_oblong", 0.0) if pile is not None and ft in (1, 2) else 0.0,
            "decalage": (interne_vers_mil(_coord(pile, "decalages_trou_x", 0)),
                         interne_vers_mil(_coord(pile, "decalages_trou_y", 0))),
            "metallise": bool(b["metallise"])}


def _masque(b):
    """Masque de soudure : mode (0 aucun, 1 règle, 2 manuel), expansions haut et bas (mil ; bas = haut sauf
    expansion bas distincte)."""
    haut = interne_vers_mil(b["masque_expansion"]) if "masque_expansion" in b.v else None
    separe = bool(b.get("masque_bas_separe"))
    bas = interne_vers_mil(b["masque_expansion_bas"]) if separe and "masque_expansion_bas" in b.v else haut
    return {"mode": b.get("masque_mode"), "expansion_haut": haut, "expansion_bas": bas, "bas_separe": separe}


# ---------------------------------------------------------------------------
def _points_region(s0):
    """Contour d'une région (mil) : sommets du flux (f64 en unités internes, ou sommets étendus)."""
    if not s0.contour:
        return []
    if s0.etendu:
        return [(c[1] / 1e4, c[2] / 1e4) for c in s0.contour]
    return [(x / 1e4, y / 1e4) for x, y in s0.contour]


def _trous_region(s0):
    return [[(x / 1e4, y / 1e4) for x, y in t] for t in (s0.trous or [])]


def _contour(p: Proprietes):
    d = p.dict()
    out = []
    i = 0
    while f"VX{i}" in d:
        out.append({"type": _entier(d.get(f"KIND{i}"), 0), "x": valeur_mil(d[f"VX{i}"]), "y": valeur_mil(d[f"VY{i}"]),
                    "cx": valeur_mil(d.get(f"CX{i}")), "cy": valeur_mil(d.get(f"CY{i}")), "r": valeur_mil(d.get(f"R{i}")),
                    "sa": _flottant(d.get(f"SA{i}"), 0.0), "ea": _flottant(d.get(f"EA{i}"), 0.0)})
        i += 1
    return out


def _translater(pr, dx, dy, dx_mil, dy_mil):
    s0 = pr.sous[0]
    if isinstance(s0, SousRegion):
        if s0.contour is not None:
            if s0.etendu:
                s0.contour = [(r, x + dx, y + dy, cx + dx, cy + dy, ra, a1, a2) for (r, x, y, cx, cy, ra, a1, a2) in s0.contour]
            else:
                s0.contour = [(x + dx, y + dy) for x, y in s0.contour]
            s0.trous = [[(x + dx, y + dy) for x, y in t] for t in s0.trous]
        for k, d in (("MODEL.2D.X", dx_mil), ("MODEL.2D.Y", dy_mil)):
            v = s0.props.get(k)
            if v is not None:
                s0.props[k] = formater_mil(valeur_mil(v) + d)
        return
    b = pr.bloc
    for kx, ky in (("x", "y"), ("x1", "y1"), ("x2", "y2"), ("cx", "cy"), ("ref_x", "ref_y")):
        if kx in b.v and not isinstance(b.v[kx], bytes):
            if kx == "ref_x" and b[kx] == 0 and b[ky] == 0:
                continue  # point de référence absent (textes anciens)
            b[kx] += dx
            b[ky] += dy
