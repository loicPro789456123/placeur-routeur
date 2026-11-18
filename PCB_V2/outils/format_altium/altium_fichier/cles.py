"""Dictionnaire des clés de propriétés texte, par flux.

Chaque entrée : (expression régulière sur la clé, statut, description)
  statut "C" certain (source tierce + cohérence), "S" supposé (nom explicite / déduit), "I" inconnu.
Une clé non reconnue par aucun motif est comptée « I » (inconnue) dans la couverture.
`#` dans les descriptions = indice numérique.
"""
from __future__ import annotations

import re

COMMUNS = [
    (r"SELECTION", "C", "objet sélectionné à l'enregistrement"),
    (r"LAYER", "C", "couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...)"),
    (r"LAYER_V7|V7_LAYER", "C", "couche (nom V7, pour MECHANICAL17+)"),
    (r"LOCKED", "C", "verrouillé"),
    (r"POLYGONOUTLINE", "C", "fait partie d'un contour de polygone"),
    (r"USERROUTED", "C", "routé par l'utilisateur (vs autorouteur)"),
    (r"KEEPOUT", "C", "objet keepout"),
    (r"UNIONINDEX", "C", "index d'union (SmartUnions)"),
    (r"PRIMITIVELOCK", "C", "primitives verrouillées (composant/polygone)"),
    (r"UNIQUEID", "C", "identifiant unique 8 lettres (lien schéma pour les composants)"),
    (r"", "S", "élément vide (séparateur initial '|')"),
]

# Géométrie de contour partagée (Board6, Polygons6, Rules6/rooms, SmartUnions)
CONTOUR = [
    (r"KIND\d+", "C", "type du sommet # : 0 segment, 1 arc"),
    (r"VX\d+|VY\d+", "C", "sommet # (X, Y) avec unité"),
    (r"CX\d+|CY\d+", "C", "centre de l'arc du sommet #"),
    (r"SA\d+|EA\d+", "C", "angles de début / fin de l'arc du sommet #"),
    (r"R\d+", "C", "rayon de l'arc du sommet #"),
]

CLES = {
    "Board6": COMMUNS + CONTOUR + [
        (r"RECORD|KIND|VERSION|FILENAME|DATE|TIME", "C", "en-tête : type d'enregistrement, version 5.01, chemin, date/heure d'enregistrement"),
        (r"ORIGINX|ORIGINY", "C", "origine de la carte"),
        (r"SHEETX|SHEETY|SHEETWIDTH|SHEETHEIGHT|SHOWSHEET|LOCKSHEET", "C", "feuille (position, taille, affichage, verrou)"),
        (r"(BIG)?VISIBLEGRID(SIZE|MULTFACTOR)|DOTGRID|GRIDSIZE|GRIDSNAPENABLED", "C", "grilles visibles / pas d'accrochage"),
        (r"(SNAP|COMPONENT|TRACK|VIA)GRIDSIZE[XY]?", "C", "pas de grilles (unité interne 1e-4 mil, flottant)"),
        (r"ELECTRICALGRID\w*|EG\w+", "C", "grille électrique (activée, portée, accrochages)"),
        (r"GR\d+_\w+", "C", "gestionnaire de grilles : grille # (nom, type, pas, origine, rotation, couleurs, priorité)"),
        (r"(MGSNAP|OGSNAP|POINTGUIDE)ENABLED", "S", "accrochages (guides manuels, objets, points)"),
        (r"DISPLAYUNIT", "C", "unité d'affichage (0 impérial, 1 métrique)"),
        (r"DESIGNATORDISPLAYMODE", "S", "mode d'affichage des désignateurs (physique/logique)"),
        (r"LAYER\d+(NAME|PREV|NEXT|MECHENABLED|COPTHICK|DIELTYPE|DIELCONST|DIELHEIGHT|DIELMATERIAL)", "C",
         "empilage V6 : couche # (nom, précédente, suivante, méca activée, épaisseur cuivre, type/Er/épaisseur/matériau diélectrique)"),
        (r"(LAYER_V8|V9_STACK_LAYER|V9_CACHE_LAYER)\d+_\w*\{.*\}\w+", "S", "empilage : contexte par sous-empilage (GUID) de la couche #"),
        (r"LAYERV7_\d+\w+", "C", "couches mécaniques V7 (17+) : identifiant, nom, type mécanique, activée"),
        (r"LAYER_V8_\d+\w+|V9_STACK_LAYER\d+_\w+", "C",
         "empilage V8/V9 : couche # (ID GUID, LAYERID, nom, épaisseur cuivre COPTHICK, diélectrique DIELTYPE 0 aucun/1 cœur/2 préimprégné/3 masque, "
         "DIELCONST = Er, DIELHEIGHT, DIELMATERIAL, DIELLOSSTANGENT, orientation cuivre, placement composants, utilisée)"),
        (r"V9_CACHE_LAYER\d+_\w+", "S", "cache de l'empilage V9 (copie, inclut les couches non cuivre)"),
        (r"V9_STACKCUSTOMDATA", "S", "données personnalisées de l'empilage (base64 + zlib)"),
        (r"(V9_)?(LAYER)?MASTERSTACK(_V8)?_?\w+", "C", "empilage maître (ID, nom, style, flex, diélectriques extérieurs)"),
        (r"(V9_SUBSTACK\d+_|LAYERSUBSTACK_V8_\d+)\w+", "C", "sous-empilage # (ID, nom, type, flex, service, utilisé)"),
        (r"LAYERSTACKSTYLE|SHOWTOPDIELECTRIC|SHOWBOTTOMDIELECTRIC|LAYERCOUNT", "S", "options d'empilage"),
        (r"(TOP|BOTTOM)(TYPE|CONST|HEIGHT|MATERIAL)", "C", "masque de soudure haut/bas : type, Er, épaisseur, matériau"),
        (r"LAYERPAIR\d+\w*", "C", "paire de perçage # (couche basse/haute, guide, dessin, sous-empilage)"),
        (r"LAYERSET\w*|SHOWDEFAULTSETS", "C", "jeux de couches (nom, couches, couche active, retournement)"),
        (r"MECHPAIR\d+L\d", "C", "paires de couches mécaniques"),
        (r"PLANE\d+NETNAME", "C", "réseau affecté au plan interne #"),
        (r"(TOP|BOTTOM)LAYER_MRLASTWIDTH|MIDLAYER\d+_MRLASTWIDTH|MRLASTVIA(SIZE|HOLE)|TRACKWIDTH|LASTTARGETLENGTH", "S", "dernières largeurs / via utilisées en routage interactif"),
        (r"ROUTINGDIRECTION.*", "C", "direction de routage préférée par couche"),
        (r"TOGGLELAYERS|FAROBJECTS?(SET|ENABLED)|NEAROBJECTS?(SET|ENABLED)|NEARDISTANCE|ZOOMMULT", "S", "affichage : couches basculées, objets proches/lointains"),
        (r"VP\.[LH][XY]|VIEWSIZE\.[XY]|LOOKAT\.[XYZ]|EYEROTATION\.[XYZ]|CURRENT2D3DVIEWSTATE|2DCONFIG\w*|3DCONFIG\w*|VIEWPORTSAREVISIBLE|BOARDINSIGHTVIEWCONFIGURATIONNAME", "S", "état de la vue 2D/3D enregistrée"),
        (r"[23]DCONFIG\w*", "S", "configuration de vue 2D/3D (sérialisée)"),
        (r"SELECTIONMEMORYLOCK\d+", "S", "mémoires de sélection verrouillées"),
        (r"PLACEMARKER[XY]\d+", "S", "marqueurs de placement"),
        (r"FN#?\d+|FV#?\d+|VALUECOUNT", "S", "paramètres nommés (nom / valeur) : options de méandres, etc."),
        (r"HASHKEY#?\d+|HASHVALUE#?\d+|HOLESHAPEHASHSIZE", "S", "table de hachage des formes de trous"),
        (r"DRILLSYMBOL(SIZE|ASENUM)", "C", "symboles de perçage"),
        (r"(SURFACEMICROSTRIP|SYMMETRICSTRIPLINE)_[IW]", "C", "formules d'impédance (calculateur)"),
        (r"TEARDROPPARAM_\w+", "C", "paramètres des gouttes (teardrops)"),
        (r"SILKSCREENCLIPPER_\w+\s*", "S", "découpe de la sérigraphie au contour"),
        (r"NAME|HATCHSTYLE|GRIDSIZE|POURINDEX|POUROVER(STYLE)?|REMOVEDEAD|REMOVEISLANDSBYAREA|AREATHRESHOLD|REMOVENECKS|NECKWIDTHTHRESHOLD|ARCRESOLUTION|MINPRIMLENGTH|USEOCTAGONS|SHELVED|RESTORELAYER|RESTORENET|POLYGONTYPE|IGNOREVIOLATIONS", "S",
         "paramètres de polygone attachés au contour de carte (format commun aux polygones)"),
        (r"OUTLINEMODEL(NAME|CRC)", "S", "modèle de contour importé"),
        (r"SPLITLINECOUNT|PINPAIRCOUNT", "S", "compteurs (lignes de séparation de plans, paires de broches)"),
        (r"SHOWSIGNALLAYERSONLY", "S", "affichage restreint aux couches signal"),
    ],
    "Components6": COMMUNS + [
        (r"X|Y", "C", "position de l'origine du composant"),
        (r"ROTATION", "C", "rotation (degrés, format flottant Delphi)"),
        (r"HEIGHT", "C", "hauteur du composant"),
        (r"PATTERN", "C", "nom de l'empreinte"),
        (r"SOURCEDESIGNATOR", "C", "désignateur (repère)"),
        (r"SOURCEUNIQUEID", "C", "chemin d'identifiants uniques du composant schéma (\\FEUILLE\\COMPOSANT) : lien schéma"),
        (r"SOURCEHIERARCHICALPATH", "C", "chemin hiérarchique de la feuille source"),
        (r"SOURCE(LIBREFERENCE|COMPONENTLIBRARY|FOOTPRINTLIBRARY|DESCRIPTION|COMPLIBRARYIDENTIFIER|COMPLIBIDENTIFIERKIND)", "C", "référence et bibliothèques source"),
        (r"FOOTPRINTDESCRIPTION|UNICODE__FOOTPRINTDESCRIPTION", "C", "description de l'empreinte (UNICODE__ = codes de caractères séparés par virgules)"),
        (r"UNICODE", "C", "indique la présence de clés UNICODE__*"),
        (r"UNICODE__\w+", "C", "version Unicode d'un champ texte (codes décimaux séparés par des virgules)"),
        (r"NAMEON|COMMENTON", "C", "désignateur / commentaire visibles"),
        (r"NAMEAUTOPOSITION|COMMENTAUTOPOSITION", "C", "positionnement automatique du désignateur / commentaire (0 manuel, 1..9)"),
        (r"GROUPNUM|COUNT|CHANNELOFFSET", "S", "groupe, compteur, décalage de canal (multicanal)"),
        (r"ITEMGUID|ITEMREVISIONGUID|VAULTGUID|REVISIONGUID", "C", "liens coffre (vault) du composant"),
        (r"JUMPERSVISIBLE", "C", "cavaliers visibles"),
        (r"AREA", "S", "aire de l'empreinte (unité interne²)"),
        (r"COMPONENTKIND|FLIPPEDONLAYER|ENABLEPINSWAPPING|ENABLEPARTSWAPPING|DEFAULTPCB3DMODEL|LOCKSTRINGS|SWAPPEDPADSTRING|COMPONENTSIDE", "S", "options diverses"),
    ],
    "Nets6": COMMUNS + [
        (r"NAME", "C", "nom du réseau"),
        (r"COLOR|OVERRIDECOLORFORDRAW|VISIBLE", "C", "couleur et visibilité"),
        (r"(TOP|BOTTOM)LAYER_MRWIDTH|MIDLAYER\d+_MRWIDTH", "S", "dernière largeur de routage utilisée par couche"),
        (r"LOOPREMOVAL|JUMPERSVISIBLE", "C", "suppression des boucles, cavaliers"),
        (r"ROUTEDLENGTH|MANHATTANLENGTH", "S", "longueur routée / Manhattan (unité interne)"),
        (r"CURRENTTOTAL|DELAYTOTAL|RESISTANCETOTAL", "S", "analyses : courant, délai, résistance cumulés"),
    ],
    "Classes6": COMMUNS + [
        (r"NAME", "C", "nom de la classe"),
        (r"KIND", "C", "type : 0 réseaux, 1 composants, 2 from-to, 3 pastilles, 4 couches, 6 paires diff., 7 polygones"),
        (r"M\d+", "C", "membre #"),
        (r"SUPERCLASS", "C", "classe « tous » (superclasse)"),
        (r"SELECTED", "S", "classe sélectionnée"),
        (r"AUTOGENERATEDCLASS(KIND)?|SCHAUTOGENERATEDCLUSTER", "S", "classe générée automatiquement (et son type)"),
    ],
    "SignalClasses": COMMUNS + [
        (r"NAME|KIND|SUPERCLASS|SELECTED|SCHAUTOGENERATEDCLUSTER|M\d+", "S", "classes de xSignals (même format que Classes6)"),
    ],
    "Rules6": COMMUNS + CONTOUR + [
        (r"RULEKIND", "C", "type de règle (Clearance, Width, RoutingVias, PlaneClearance, PolygonConnect, Height, RoomDefinition...)"),
        (r"NAME|COMMENT|UNICODE__COMMENT|UNICODE", "C", "nom et commentaire"),
        (r"ENABLED", "C", "règle activée"),
        (r"PRIORITY", "C", "priorité (1 = la plus forte)"),
        (r"SCOPE\d+EXPRESSION|SCOPE\d+_\w+", "C", "requête de portée # (1 = premier objet, 2 = second)"),
        (r"NETSCOPE|LAYERKIND|SCOPECOUNT", "C", "portée réseaux (DifferentNets, SameNet, AnyNet) / couches"),
        (r"DEFINEDBYLOGICALDOCUMENT", "C", "règle définie depuis le schéma (directive)"),
        (r"UNIQUEID", "C", "identifiant de la règle"),
        (r"GAP|GENERICCLEARANCE|OBJECTCLEARANCES|IGNOREPADTOPADCLEARANCEINFOOTPRINT|MOSTFREQGAP|ABSOLUTEVALUES", "C", "dégagement : valeur générique, matrice par types d'objets"),
        (r"(MIN|MAX|PREFERED)WIDTH|(TOP|BOTTOM)LAYER_(MIN|MAX|PREF)WIDTH|MIDLAYER\d+_(MIN|MAX|PREF)WIDTH|(TOP|BOTTOM) LAYER_V\d|MID LAYER \d+_V\d", "C", "largeur : min/préférée/max, par couche"),
        (r"(MIN|MAX|PREFERED)(HOLE)?(SIZE|WIDTH)|HOLEWIDTH|VIASTYLE", "C", "vias / trous : tailles min/préf/max"),
        (r"(MIN|MAX|PREF)HEIGHT", "C", "hauteur de composant"),
        (r"EXPANSION|ISTENTINGTOP|ISTENTINGBOTTOM", "C", "expansion masque (soudure/pâte), tenting"),
        (r"CLEARANCE|RELIEF(AIRGAP|CONDUCTORWIDTH|ENTRIES|EXPANSION)|PLANECONNECTSTYLE", "C", "plans : dégagement, style de connexion, thermique"),
        (r"(SMDPAD\.|THPAD\.|VIA\.)?(CONNECTSTYLE|AIRGAPWIDTH|RELIEFCONDUCTORWIDTH|RELIEFENTRIES|POLYGONRELIEFANGLE)", "C", "connexion polygone : style, entrefer, largeur, branches, angle (par type d'objet)"),
        (r"(MIN|MAX)LIMIT|MINSOLDERMASKWIDTH|MINSILKSCREENTOMASKGAP|SILKTOSILKCLEARANCE|CLEARANCETOEXPOSEDCOPPER|NETANTENNAETOLERANCE", "C", "limites diverses (trous, masque, sérigraphie, antennes)"),
        (r"CONFINEMENTSTYLE|LOCKCOMPONENTS|SIDE|REGIONKIND|BOUNDARYLAYER|LOCKED", "C", "room : confinement (dedans/dehors), verrou, face"),
        (r"TOPOLOGY|ROUTINGPRIORITY|CORNERSTYLE|(MIN|MAX)SETBACK|ALLOWED|LAYER\d+\w*", "C", "topologie, priorité, coins, couches de routage"),
        (r"FANOUT\w+|BGA\w+", "C", "options de fanout"),
        (r"(MIN|MAX)PERCENT|POLYGONREMOVAL\w*|DEADCOPPER\w*", "S", "pourcentages / cuivre mort"),
        (r"TESTPOINT\w+|USEGRID|GRIDTOLERANCE|VIAGRID|ALLOWSIDE(TOP|BOTTOM)|ALLOWMULTIPLE|TESTPOINTUNDERCOMPONENT", "C", "points de test"),
        (r"ALLOWSTACKEDMICROVIAS|MAXUNCOUPLEDLENGTH|VERTICALGAP|COLLISIONCHECKMODE|SHOWDISTANCES|FORMATCOPY|ENFORCE|VALID|VOLTAGE|MAXHOLESIZE|MINHOLESIZE|MAXSIZE|MINSIZE|PREFEREDSIZE|PREFEREDHOLESIZE", "S", "paramètres divers de règle"),
        (r"[A-Z0-9_.]+", "S", "autre paramètre de règle (nom explicite, valeur avec unité)"),
    ],
    "Polygons6": COMMUNS + CONTOUR + [
        (r"NAME|AUTONAME", "C", "nom (UNICODE codes séparés par virgules possible), nommage auto"),
        (r"NET", "C", "index du réseau (Nets6)"),
        (r"POLYGONTYPE", "C", "Polygon / Split plane / Signal layer"),
        (r"HATCHSTYLE", "C", "Solid, 45Degree, 90Degree, Horizontal, Vertical, None"),
        (r"POUROVER(STYLE)?", "C", "couler par-dessus les objets du même réseau"),
        (r"REMOVEDEAD|REMOVEISLANDSBYAREA|AREATHRESHOLD|REMOVENECKS|NECKWIDTHTHRESHOLD|NECKWIDTHFROMRULE", "C", "suppression des îlots / cuivre mort / cols"),
        (r"GRIDSIZE|TRACKWIDTH|MINPRIMLENGTH|ARCRESOLUTION|USEOCTAGONS", "C", "hachures : grille, largeur, longueur min, résolution d'arc"),
        (r"POURINDEX", "C", "ordre de coulée"),
        (r"IGNOREVIOLATIONS|OBEYPOLYGONCUTOUT|OPTIMALVOIDROTATION|SHELVED|RESTORELAYER|RESTORENET", "C", "options (ignorer violations, respecter découpes, mis de côté)"),
    ],
    "Dimensions6": COMMUNS + [
        (r"[A-Z0-9_.]+", "S", "paramètres de cote (DIMENSIONKIND, X1/Y1, REFERENCE#X/Y, TEXTX/Y, hauteurs, flèches, unités)"),
    ],
    "Models": [
        (r"ID", "C", "GUID du modèle (référencé par MODELID des corps 3D)"),
        (r"NAME", "C", "nom de fichier du modèle"),
        (r"EMBED", "C", "modèle incorporé (flux Models/N, N = rang de l'enregistrement)"),
        (r"MODELSOURCE", "C", "origine"),
        (r"ROTX|ROTY|ROTZ|DZ", "C", "rotation / décalage Z par défaut"),
        (r"CHECKSUM", "C", "somme de contrôle du modèle"),
        (r"", "S", "élément vide"),
    ],
    "ComponentBodies6": COMMUNS + [
        (r"MODEL\.\w+(\.\w+)*", "C", "modèle 3D : nom, incorporé, position 2D (X, Y), rotation, décalage DZ, rotations X/Y/Z, type, extrusion, somme"),
        (r"MODELID", "C", "GUID du modèle (Models)"),
        (r"BODYCOLOR3D|BODYOPACITY3D|BODYPROJECTION", "C", "couleur, opacité, face de projection"),
        (r"IDENTIFIER", "C", "identifiant (codes de caractères séparés par virgules)"),
        (r"STANDOFFHEIGHT|OVERALLHEIGHT|CAVITYHEIGHT", "C", "hauteurs"),
        (r"TEXTURE\w*", "C", "texture"),
        (r"NAME|KIND|SUBPOLYINDEX|ARCRESOLUTION|ISSHAPEBASED", "C", "communs aux régions"),
    ],
    "Regions6": COMMUNS + [
        (r"NAME|KIND|SUBPOLYINDEX|ARCRESOLUTION|ISSHAPEBASED|ISBOARDCUTOUT|CAVITYHEIGHT", "C",
         "région : nom, type (0 cuivre, 1 découpe de polygone, 2 contour pointillé, 4 cavité), sous-polygone, découpe de carte"),
        (r"KEEPOUTRESTRICTIONS", "C", "restrictions keepout"),
        (r"OBJECTKIND|LAYERSTACKID|BENDINGLINECOUNT|LOCKED3D", "C", "région de carte : type, empilage associé, lignes de pliage"),
        (r"UNIONINDEX|UNIQUEID", "C", ""),
    ],
    "PrimitiveParameters": [
        (r"PRIMITIVEID", "C", "UNIQUEID de la primitive porteuse"),
        (r"ID|APPURTENANCE|VARIANTGUID|COUNT|NAME|VALUE|ISIMPORTED", "C", "paramètre (nom, valeur) d'un composant / d'une empreinte"),
        (r"", "S", "élément vide"),
    ],
    "UniqueIDPrimitiveInformation": [
        (r"PRIMITIVEINDEX|PRIMITIVEOBJECTID|UNIQUEID", "C", "UNIQUEID d'une primitive (type + index dans son flux)"),
        (r"", "S", "élément vide"),
    ],
    "SmartUnions": COMMUNS + CONTOUR + [
        (r"[A-Z0-9_.]+", "S", "union intelligente (tableau de vias de couture, etc.) : paramètres explicites"),
    ],
    "DrillManager": [
        (r"[A-Z0-9_.]+", "C", "outil de perçage : objet, taille, forme, métallisé, gabarit, tolérances, type"),
        (r"", "S", "élément vide"),
    ],
    "FileVersionInfo": [
        (r"COUNT|VER\d+|FWDMSG\d+|BKMSG\d+|GUID\d+|ISDYNAMIC\d+", "C", "historique de versions du format (messages de compatibilité, codes de caractères)"),
        (r"", "S", "élément vide"),
    ],
}

for _nom in ("Advanced Placer Options6", "Design Rule Checker Options6", "Pin Swap Options6",
             "PadViaLibrary", "PadViaLibraryCache", "PadViaLibraryLinks"):
    CLES[_nom] = [(r"RECORD", "C", "type d'enregistrement"),
                  (r"[A-Z0-9_.]+", "C", "option nommée explicitement (valeur booléenne/numérique)"),
                  (r"", "S", "élément vide")]
for _nom in ("TSilkToSolderMaskClearanceViola", "TNetAntennaeViolation", "WaivedViolations"):
    CLES[_nom] = COMMUNS + [(r"[A-Z0-9_.]+", "C", "violation DRC enregistrée : description, règle, emplacement, primitives impliquées")]
CLES["ShapeBasedComponentBodies6"] = CLES["ComponentBodies6"]
CLES["ShapeBasedRegions6"] = CLES["Regions6"]
CLES["BoardRegions"] = CLES["Regions6"]


class DictionnaireCles:
    def __init__(self, motifs):
        self.motifs = [(re.compile(m + r"\Z"), s, d) for m, s, d in motifs]
        self._cache = {}

    def chercher(self, cle):
        r = self._cache.get(cle)
        if r is None:
            r = ("I", "")
            for rx, s, d in self.motifs:
                if rx.match(cle):
                    r = (s, d)
                    break
            self._cache[cle] = r
        return r

    def statut(self, cle):
        return self.chercher(cle)[0]


_DICOS = {}


def dictionnaire(storage):
    if storage not in CLES:
        return None
    if storage not in _DICOS:
        _DICOS[storage] = DictionnaireCles(CLES[storage])
    return _DICOS[storage]
