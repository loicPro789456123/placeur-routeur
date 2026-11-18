"""Gabarits (dispositions octet par octet) des sous-enregistrements binaires du .PcbDoc.

Chaque champ : (décalage, nom, type, unité, statut, description)
  statut : "E" = confirmé par expérience dans Altium (voir EXPERIENCES_A_FAIRE.md),
           "C" = certain (source tierce KiCad/AltiumSharp ET cohérent avec nos 3 fichiers),
           "S" = supposé (déduit de nos données ou par analogie, non confirmé),
           "I" = inconnu (position isolée mais sens inconnu ; conservé brut).
Les octets non couverts par un champ sont conservés bruts sous le nom `inconnu_<décalage>`.
Un champ qui dépasse la longueur réelle du sous-enregistrement est simplement absent
(les versions d'Altium allongent les enregistrements par la fin).

Types : u8 i8 u16 i16 u32 i32 f64, coord (i32, 1/10000 mil), guid (16 octets),
        utf16:N (N octets UTF-16LE, complété de NUL), octets:N.
"""
from __future__ import annotations

import struct
import uuid

TAILLES = {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "f64": 8, "coord": 4, "guid": 16}
_FMT = {"u8": "<B", "i8": "<b", "u16": "<H", "i16": "<h", "u32": "<I", "i32": "<i", "f64": "<d", "coord": "<i"}


def taille_type(t: str) -> int:
    if t in TAILLES:
        return TAILLES[t]
    return int(t.split(":")[1])


# --- en-tête commun des primitives (13 octets) ---------------------------------
def _entete(couche=True):
    c = []
    if couche:
        c.append((0, "couche", "u8", "id V6", "C", "couche (identifiant V6, voir couches.py)"))
    c += [
        (1, "drapeaux1", "u8", "bits", "E",
         "bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; "
         "pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut"),
        (2, "drapeaux2", "u8", "bits", "E", "0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié)"),
        (3, "reseau", "u16", "index Nets6", "C", "index du réseau dans Nets6 (0xFFFF = aucun)"),
        (5, "polygone", "u16", "index Polygons6", "C", "index du polygone propriétaire (0xFFFF = aucun)"),
        (7, "composant", "u16", "index Components6", "C", "index du composant propriétaire (0xFFFF = libre)"),
        (9, "inconnu_ffffffff", "u32", "", "I", "toujours 0xFFFFFFFF dans nos fichiers"),
    ]
    return c


PISTE = _entete() + [
    (13, "x1", "coord", "1e-4 mil", "C", "début X"),
    (17, "y1", "coord", "1e-4 mil", "C", "début Y"),
    (21, "x2", "coord", "1e-4 mil", "C", "fin X"),
    (25, "y2", "coord", "1e-4 mil", "C", "fin Y"),
    (29, "largeur", "coord", "1e-4 mil", "C", "largeur"),
    (33, "sous_poly", "u16", "", "C", "index de sous-polygone (coulée)"),
    (35, "inconnu_35", "u8", "", "I", "toujours 0"),
    (36, "union", "u32", "", "C", "index d'union (SmartUnions/UnionNames)"),
    (40, "inconnu_40", "u8", "", "I", "toujours 0"),
    (41, "couche_v7", "u32", "id V7", "C", "couche V7 (32 bits)"),
    (45, "restrictions_keepout", "u8", "bits", "E", "restrictions keepout : 0x1F = toutes, bit0 = vias (exp. E03) ; autres bits supposés (pistes, cuivre, CMS, traversantes) ; 0 si non keepout"),
]

ARC = _entete() + [
    (13, "cx", "coord", "1e-4 mil", "C", "centre X"),
    (17, "cy", "coord", "1e-4 mil", "C", "centre Y"),
    (21, "rayon", "coord", "1e-4 mil", "C", "rayon"),
    (25, "angle_debut", "f64", "degré", "C", "angle de départ"),
    (33, "angle_fin", "f64", "degré", "C", "angle de fin"),
    (41, "largeur", "coord", "1e-4 mil", "C", "largeur du trait"),
    (45, "sous_poly", "u16", "", "C", "index de sous-polygone"),
    (47, "inconnu_47", "u8", "", "I", "toujours 0"),
    (48, "union", "u32", "", "C", "index d'union"),
    (52, "couche_v7", "u32", "id V7", "C", "couche V7"),
    (56, "restrictions_keepout", "u8", "bits", "C", "restrictions keepout"),
]

REMPLI = _entete() + [
    (13, "x1", "coord", "1e-4 mil", "C", "coin 1 X"),
    (17, "y1", "coord", "1e-4 mil", "C", "coin 1 Y"),
    (21, "x2", "coord", "1e-4 mil", "C", "coin 2 X"),
    (25, "y2", "coord", "1e-4 mil", "C", "coin 2 Y"),
    (29, "rotation", "f64", "degré", "C", "rotation"),
    (37, "inconnu_37", "u8", "", "I", "toujours 0"),
    (38, "union", "u32", "", "S", "index d'union (par analogie avec les pistes)"),
    (42, "couche_v7", "u32", "id V7", "C", "couche V7"),
    (46, "restrictions_keepout", "u8", "bits", "C", "restrictions keepout"),
]

VIA = [
    (0, "couche", "u8", "id V6", "S", "toujours 74 (MULTILAYER)"),
] + _entete(couche=False)[0:] + [
    (13, "x", "coord", "1e-4 mil", "C", "position X"),
    (17, "y", "coord", "1e-4 mil", "C", "position Y"),
    (21, "diametre", "coord", "1e-4 mil", "C", "diamètre (mode simple)"),
    (25, "trou", "coord", "1e-4 mil", "C", "diamètre de perçage"),
    (29, "couche_debut", "u8", "id V6", "C", "couche de départ (1 = TOP)"),
    (30, "couche_fin", "u8", "id V6", "C", "couche d'arrivée (32 = BOTTOM)"),
    (31, "inconnu_31", "u8", "", "I", "toujours 0"),
    (32, "relief_entrefer", "coord", "1e-4 mil", "C", "thermique : entrefer (air gap)"),
    (36, "relief_branches", "u8", "", "C", "thermique : nombre de branches"),
    (37, "inconnu_37", "u8", "", "I", "toujours 0"),
    (38, "relief_largeur", "coord", "1e-4 mil", "C", "thermique : largeur des branches"),
    (42, "plan_expansion_relief", "coord", "1e-4 mil", "S", "expansion de relief sur plan (20 mil par défaut)"),
    (46, "plan_degagement", "coord", "1e-4 mil", "S", "dégagement sur plan (20 mil par défaut)"),
    (50, "inconnu_50", "u32", "", "I", "toujours 0"),
    (54, "masque_expansion_haut", "coord", "1e-4 mil", "E", "expansion masque de soudure (haut) (exp. E05)"),
    (58, "inconnu_58", "octets:8", "", "I", "varie 0/1 octet par octet (61..64) : drapeaux ?"),
    (66, "masque_mode", "u8", "enum", "E", "mode d'expansion du masque : 1 règle, 2 manuelle (exp. E05)"),
    (67, "inconnu_67", "u8", "", "I", "toujours 0 ; KiCad y lit « depuis le bord du trou » : NON confirmé (exp. E05 : c'est l'octet 258)"),
    (68, "inconnu_68", "octets:6", "", "I", "70: 0/2 ; 72: 0/0x10"),
    (74, "mode_pile", "u8", "", "C", "0 simple, 1 haut-milieu-bas, 2 pile complète"),
    (75, "diametres_couches", "octets:128", "32 x coord", "C", "diamètre par couche cuivre (32 x i32)"),
    (203, "couche_v7", "u32", "id V7", "S", "couche V7 (toujours 0x0103000F = MULTILAYER)"),
    (207, "inconnu_207", "octets:34", "", "I", "zéros sauf 242..245 = 4 mil"),
    (241, "masque_bas_separe", "u8", "bool", "E", "1 = expansion bas distincte de l'expansion haut (exp. E05)"),
    (242, "masque_expansion_bas", "coord", "1e-4 mil", "E", "expansion masque de soudure (bas) (exp. E05)"),
    (246, "inconnu_246", "octets:12", "", "I", "254 = 0x2A"),
    (258, "masque_depuis_bord_trou", "u8", "bool", "E", "expansion mesurée depuis le bord du trou (exp. E05)"),
    (259, "guid_gabarit", "guid", "", "S", "GUID du gabarit pad/via (propre à chaque via)"),
    (275, "guid_bibliotheque", "guid", "", "S", "GUID de la bibliothèque pad/via (= PadViaLibraryCache LIBRARYID)"),
    (291, "tolerance_plus", "coord", "1e-4 mil", "E", "tolérance de perçage + (0x7FFFFFFF = non définie)"),
    (295, "tolerance_moins", "coord", "1e-4 mil", "E", "tolérance de perçage - (0x7FFFFFFF = non définie)"),
    (299, "inconnu_299", "octets:22", "", "I", "304 = 0x1E, 308 = 0x09, 320 = 0x01"),
]

PASTILLE = _entete() + [
    (13, "x", "coord", "1e-4 mil", "C", "position X"),
    (17, "y", "coord", "1e-4 mil", "C", "position Y"),
    (21, "taille_haut_x", "coord", "1e-4 mil", "C", "taille X couche haute"),
    (25, "taille_haut_y", "coord", "1e-4 mil", "C", "taille Y couche haute"),
    (29, "taille_milieu_x", "coord", "1e-4 mil", "C", "taille X couches internes"),
    (33, "taille_milieu_y", "coord", "1e-4 mil", "C", "taille Y couches internes"),
    (37, "taille_bas_x", "coord", "1e-4 mil", "C", "taille X couche basse"),
    (41, "taille_bas_y", "coord", "1e-4 mil", "C", "taille Y couche basse"),
    (45, "trou", "coord", "1e-4 mil", "C", "diamètre de trou (0 = CMS)"),
    (49, "forme_haut", "u8", "enum", "C", "1 rond, 2 rectangle, 3 octogone (rect. arrondi : voir pile)"),
    (50, "forme_milieu", "u8", "enum", "C", "idem couches internes"),
    (51, "forme_bas", "u8", "enum", "C", "idem couche basse"),
    (52, "rotation", "f64", "degré", "C", "rotation de la pastille"),
    (60, "metallise", "u8", "bool", "C", "trou métallisé"),
    (61, "inconnu_61", "u8", "", "I", "toujours 0"),
    (62, "mode_pile", "u8", "", "C", "0 simple, 1 haut-milieu-bas, 2 pile complète (sous-enr. 6)"),
    (63, "inconnu_63", "u32", "", "I", "toujours 0"),
    (67, "inconnu_67", "u8", "", "I", "0/1"),
    (68, "relief_entrefer", "coord", "1e-4 mil", "S", "thermique : entrefer (analogie via)"),
    (72, "relief_branches", "u8", "", "S", "thermique : nombre de branches"),
    (73, "inconnu_73", "u8", "", "I", "toujours 0"),
    (74, "relief_largeur", "coord", "1e-4 mil", "S", "thermique : largeur des branches"),
    (78, "plan_expansion_relief", "coord", "1e-4 mil", "S", "expansion relief plan"),
    (82, "plan_degagement", "coord", "1e-4 mil", "S", "dégagement plan"),
    (86, "pate_expansion", "coord", "1e-4 mil", "E", "expansion masque de pâte (manuelle) ; -99999 mil quand la pâte est désactivée (exp. E08)"),
    (90, "masque_expansion", "coord", "1e-4 mil", "E", "expansion masque de soudure (manuelle, haut) (exp. E09)"),
    (94, "inconnu_94", "octets:7", "", "I", "96: 0/1 ; 97..100 = 1"),
    (101, "pate_mode", "u8", "enum", "C", "0 aucune, 1 règle, 2 manuelle"),
    (102, "masque_mode", "u8", "enum", "E", "0 aucune, 1 règle, 2 manuelle (exp. E09)"),
    (103, "inconnu_103", "octets:3", "", "I", "103 = 1, 104: 0/1"),
    (106, "inconnu_106", "u32", "", "I", "toujours 0 (KiCad y lit une rotation de trou f64 : contredit par l'exp. E08b)"),
    (110, "jumper_id", "u16", "", "E", "identifiant de cavalier (Jumper ID) (exp. E08b)"),
    (112, "inconnu_112", "u16", "", "I", "toujours 0"),
    (114, "vers_couche", "u8", "id V6", "S", "octet 0 de la couche V7 de la pastille (114..117 = u32 V7 : 0x01000001 TOP, 0x0100FFFF BOTTOM, 0x0103000F MULTI ; vérifié sur 3060 pastilles et 95 retournements Altium de rev5_auto) ; KiCad : couche d'arrivée"),
    (115, "inconnu_115", "u16", "", "S", "octets 1-2 de la couche V7 (0 TOP, 0x00FF BOTTOM, 0x0300 MULTI)"),
    (117, "depuis_couche", "u8", "id V6", "S", "octet 3 de la couche V7 (famille, toujours 1) ; KiCad : couche de départ"),
    (118, "inconnu_118", "octets:2", "", "I", "toujours 0"),
    (120, "masque_bas_separe", "u8", "bool", "E", "1 = expansion bas distincte (exp. E09)"),
    (121, "masque_expansion_bas", "coord", "1e-4 mil", "E", "expansion masque de soudure (bas) (exp. E09)"),
    (125, "inconnu_125", "u8", "", "I", "toujours 0"),
    (126, "guid_gabarit", "guid", "", "S", "GUID du gabarit pad/via"),
    (142, "guid_bibliotheque", "guid", "", "S", "GUID de la bibliothèque pad/via (= PadViaLibraryCache)"),
    (158, "longueur_pastille_puce", "coord", "1e-4 mil", "C", "pad-to-die length"),
    (162, "tolerance_plus", "coord", "1e-4 mil", "E", "tolérance de perçage + (0x7FFFFFFF = non définie) (exp. E09)"),
    (166, "tolerance_moins", "coord", "1e-4 mil", "E", "tolérance de perçage - (exp. E09)"),
    (170, "inconnu_170", "octets:15", "", "I", "171 = 1, 172 = 0x1A, 184 = 1"),
    (185, "pate_active", "u8", "enum", "E", "3 = masque de pâte actif, 1 = désactivé (exp. E08a)"),
    (186, "inconnu_186", "octets:8", "", "I", "toujours 0"),
    (194, "delai_pastille_puce", "f64", "s", "C", "pad-to-die delay (secondes)"),
]

PASTILLE_PILE = [
    (0, "tailles_x_internes", "octets:116", "29 x coord", "C",
     "taille X pour MID2..MID30 (MID1 : taille_milieu du sous-enr. 5 ; vérifié sur les 6208 couches de la table "
     "par couche des pastilles en pile complète de Bluetooth_Sentinel)"),
    (116, "tailles_y_internes", "octets:116", "29 x coord", "C", "taille Y pour MID2..MID30"),
    (232, "formes_internes", "octets:29", "29 x u8", "C", "forme pour MID2..MID30 (MID1 : forme_milieu)"),
    (261, "inconnu_261", "u8", "", "I", "toujours 0"),
    (262, "forme_trou", "u8", "enum", "C", "0 rond, 1 carré, 2 oblong (slot)"),
    (263, "longueur_oblong", "coord", "1e-4 mil", "C", "longueur du trou oblong"),
    (267, "rotation_oblong", "f64", "degré", "C", "rotation du trou oblong"),
    (275, "decalages_trou_x", "octets:128", "32 x coord", "C",
     "décalage X par couche (0 TOP, k MIDk, 31 BOTTOM) ; « Offset From Hole Center » d'Altium : décalage du cuivre "
     "par rapport au centre du trou, repère de la pastille (sens supposé : annexe E)"),
    (403, "decalages_trou_y", "octets:128", "32 x coord", "C", "décalage Y par couche (même convention)"),
    (531, "inconnu_531", "u8", "", "I", "toujours 0"),
    (532, "formes_alt", "octets:32", "32 x u8", "C",
     "forme alternative par couche (0 TOP, k MIDk, 31 BOTTOM) : 9 = rectangle arrondi, quelle que soit la forme "
     "de base (comme l'importeur KiCad ; observé avec une forme de base ronde) ; 1 sinon (même sur une pastille "
     "rectangulaire : sans effet)"),
    (564, "rayons_coins", "octets:32", "32 x u8", "C", "rayon de coin en % par couche (même indexation)"),
    (596, "inconnu_596", "octets:55", "", "I",
     "fin de la pile (651 octets) : 628=1, 632=0x0F, 636=4... ; les versions récentes ajoutent ensuite une table "
     "de 32 x 15 octets (u32 couche V7, u8 forme, coord x, coord y, u16 = 50), conservée brute"),
]

TEXTE = [
    (0, "couche", "u8", "id V6", "C", "couche"),
    (1, "drapeaux1", "u8", "bits", "S", "comme les autres primitives (0x04 = non verrouillé)"),
    (2, "drapeaux2", "u8", "bits", "S", "0x02 keepout"),
    (3, "reseau", "u16", "", "S", "index réseau (0xFFFF)"),
    (5, "polygone", "u16", "", "S", "index polygone"),
    (7, "composant", "u16", "index Components6", "C", "composant propriétaire"),
    (9, "inconnu_ffffffff", "u32", "", "I", "0xFFFFFFFF"),
    (13, "x", "coord", "1e-4 mil", "C", "position X (coin d'ancrage)"),
    (17, "y", "coord", "1e-4 mil", "C", "position Y"),
    (21, "hauteur", "coord", "1e-4 mil", "C", "hauteur du texte"),
    (25, "police_trait", "u16", "enum", "C", "police trait : 1 défaut, 2 sans serif, 3 serif"),
    (27, "rotation", "f64", "degré", "E", "rotation (exp. E13 : Altium recalcule x/y autour du point de rotation)"),
    (35, "miroir", "u8", "bool", "C", "texte en miroir"),
    (36, "epaisseur_trait", "coord", "1e-4 mil", "C", "épaisseur de trait (police trait)"),
    (40, "est_commentaire", "u8", "bool", "C", "texte = commentaire du composant"),
    (41, "est_designateur", "u8", "bool", "C", "texte = désignateur du composant"),
    (42, "inconnu_42", "u8", "", "I", "0/1"),
    (43, "type_police", "u8", "enum", "E", "1 = TrueType, 0 sinon (trait ou code-barres) (exp. E14)"),
    (44, "gras", "u8", "bool", "C", "gras (TrueType)"),
    (45, "italique", "u8", "bool", "C", "italique (TrueType)"),
    (46, "nom_police", "utf16:64", "", "C", "nom de la police TrueType"),
    (110, "inverse", "u8", "bool", "E", "texte inversé (exp. E15)"),
    (111, "marge_inversion", "coord", "1e-4 mil", "C", "largeur de bordure d'inversion"),
    (115, "index_chaine_large", "u32", "index WideStrings6", "C", "index dans WideStrings6 (texte Unicode)"),
    (119, "inconnu_119", "u32", "", "I", ""),
    (123, "rect_inverse", "u8", "bool", "E", "inversion par rectangle (exp. E15)"),
    (124, "rect_largeur", "coord", "1e-4 mil", "C", "largeur du cadre"),
    (128, "rect_hauteur", "coord", "1e-4 mil", "C", "hauteur du cadre"),
    (132, "justification", "u8", "enum", "C", "1..9 : gauche-haut ... droite-bas"),
    (133, "decalage_texte", "coord", "1e-4 mil", "C", "décalage du texte dans le cadre"),
    (137, "codebarre_largeur", "coord", "1e-4 mil", "E", "largeur totale du code-barres (exp. E14 ; 1050 mil par défaut)"),
    (141, "codebarre_hauteur", "coord", "1e-4 mil", "E", "hauteur totale du code-barres (exp. E14 ; 210 mil par défaut)"),
    (145, "codebarre_marge_x", "coord", "1e-4 mil", "E", "marge code-barres X (exp. E14)"),
    (149, "codebarre_marge_y", "coord", "1e-4 mil", "E", "marge code-barres Y (exp. E14)"),
    (153, "codebarre_largeur_min", "coord", "1e-4 mil", "S", "largeur minimale de barre (calculée par Altium : 4,63 mil à l'exp. E14 ; 0 sinon)"),
    (157, "codebarre_type", "u8", "enum", "C", "0 Code39, 1 Code128"),
    (158, "inconnu_158", "u8", "", "I", ""),
    (159, "codebarre_inverse", "u8", "bool", "C", ""),
    (160, "type_texte", "u8", "enum", "E", "0 trait, 1 TrueType, 2 code-barres (exp. E14)"),
    (161, "codebarre_police", "utf16:64", "", "C", "police du code-barres"),
    (225, "inconnu_225", "u8", "", "I", ""),
    (226, "couche_v7", "u32", "id V7", "C", "couche V7"),
    (230, "est_cadre", "u8", "bool", "C", "texte dans un cadre (frame)"),
    (231, "bordure_decalage", "u8", "bool", "C", "utilise la bordure « offset » au lieu de « margin »"),
    (232, "inconnu_232", "octets:8", "", "I", ""),
    (240, "justification_valide", "u8", "bool", "C", "la justification est significative"),
    (241, "inconnu_241", "octets:3", "", "I", "toujours 0"),
    (244, "ref_x", "coord", "1e-4 mil", "S", "point de référence X (proche de x ; ancrage avant décalage de justification ?) ; 0 sur les textes anciens"),
    (248, "ref_y", "coord", "1e-4 mil", "S", "point de référence Y"),
]

# En-tête des régions / corps 3D (18 octets) avant la liste de propriétés
ENTETE_REGION = _entete() + [
    (13, "inconnu_13", "u8", "", "I", "toujours 0"),
    (14, "nb_trous", "u16", "", "C", "nombre de trous (contours intérieurs)"),
    (16, "inconnu_16", "u16", "", "I", "toujours 0"),
]

# Connections6 (chevelu / connexions non routées, recalculé par Altium) : blocs de 43 octets
CONNEXION = _entete() + [
    (13, "x1", "coord", "1e-4 mil", "S", "extrémité 1 X"),
    (17, "y1", "coord", "1e-4 mil", "S", "extrémité 1 Y"),
    (21, "x2", "coord", "1e-4 mil", "S", "extrémité 2 X"),
    (25, "y2", "coord", "1e-4 mil", "S", "extrémité 2 Y"),
]

FORMES_PASTILLE = {0: "inconnue", 1: "ronde", 2: "rectangle", 3: "octogone"}
FORMES_ALT = {0: "-", 1: "ronde", 2: "rectangle", 3: "octogone", 9: "rectangle arrondi"}
FORMES_TROU = {0: "rond", 1: "carré", 2: "oblong"}
# formes du cuivre d'une pastille rendues par PcbDoc.pastilles() (vocabulaire du contrat, §2.2)
FORMES_TROU_CONTRAT = {0: "rond", 1: "carre", 2: "oblong"}
FORME_ALT_RECT_ARRONDI = 9


# -----------------------------------------------------------------------------
class Bloc:
    """Sous-enregistrement décodé selon un gabarit. `v` : dict ordonné nom -> valeur.
    Les zones hors gabarit sont des `bytes` sous `inconnu_<off>`."""
    __slots__ = ("gabarit", "v", "nom_gabarit")

    def __init__(self, gabarit, v, nom_gabarit=""):
        self.gabarit = gabarit
        self.v = v
        self.nom_gabarit = nom_gabarit

    def __getitem__(self, k):
        return self.v[k]

    def __setitem__(self, k, val):
        if k not in self.v:
            raise KeyError(f"champ absent de ce sous-enregistrement : {k}")
        self.v[k] = val

    def get(self, k, d=None):
        return self.v.get(k, d)


_INDEX_GABARITS = {}


def _types(gabarit):
    t = _INDEX_GABARITS.get(id(gabarit))
    if t is None:
        t = {c[1]: c for c in gabarit}
        _INDEX_GABARITS[id(gabarit)] = t
    return t


def decoder_bloc(gabarit, b: bytes, nom="") -> Bloc:
    v = {}
    pos = 0
    for off, nomc, typ, *_ in sorted(gabarit, key=lambda c: c[0]):
        n = taille_type(typ)
        if off + n > len(b):
            break
        if off > pos:
            v[f"inconnu_{pos}"] = b[pos:off]
        brut = b[off:off + n]
        v[nomc] = _lire(typ, brut)
        pos = off + n
    if pos < len(b):
        v[f"inconnu_{pos}"] = b[pos:]
    return Bloc(gabarit, v, nom)


def _lire(typ, brut):
    if typ in _FMT:
        return struct.unpack(_FMT[typ], brut)[0]
    if typ == "guid":
        return str(uuid.UUID(bytes_le=brut)).upper()
    if typ.startswith("utf16:"):
        try:
            s = brut.decode("utf-16-le")
        except UnicodeDecodeError:
            return brut  # octets non UTF-16 après le NUL : conservé brut
        t = s.split("\0", 1)[0]
        if t.encode("utf-16-le").ljust(len(brut), b"\0") == brut:
            return t
        return brut  # contenu après NUL non nul : conservé brut
    return brut


def encoder_bloc(bloc: Bloc) -> bytes:
    t = _types(bloc.gabarit)
    out = bytearray()
    for k, val in bloc.v.items():
        c = t.get(k)
        if c is None or isinstance(val, (bytes, bytearray)):
            out += val
            continue
        typ = c[2]
        if typ in _FMT:
            out += struct.pack(_FMT[typ], val)
        elif typ == "guid":
            out += uuid.UUID(val).bytes_le
        elif typ.startswith("utf16:"):
            n = taille_type(typ)
            e = val.encode("utf-16-le")[:n]
            out += e.ljust(n, b"\0")
        else:
            raise TypeError(f"{k}: type {typ} attend des octets")
    return bytes(out)


def couverture_bloc(bloc: Bloc):
    """Renvoie {statut: nb_octets} ; statut 'I' pour les zones hors gabarit."""
    t = _types(bloc.gabarit)
    res = {"C": 0, "S": 0, "I": 0, "E": 0}
    for k, val in bloc.v.items():
        c = t.get(k)
        if c is None:
            res["I"] += len(val)
        else:
            res[c[4]] += taille_type(c[2])
    res["C"] += res.pop("E")  # « confirmé » compte comme certain
    return res


def longueur_gabarit(gabarit) -> int:
    """Longueur couverte par le gabarit (fin du dernier champ)."""
    return max(off + taille_type(typ) for off, _, typ, *_ in gabarit)


def nouveau_bloc(gabarit, longueur=None, nom="", **valeurs) -> Bloc:
    """Bloc neuf : `longueur` octets nuls (défaut : longueur du gabarit) décodés selon le gabarit, puis
    champs nommés fixés par `valeurs` (un champ absent de cette longueur lève KeyError)."""
    b = decoder_bloc(gabarit, b"\0" * (longueur_gabarit(gabarit) if longueur is None else longueur), nom)
    for k, v in valeurs.items():
        b[k] = v
    return b


# -----------------------------------------------------------------------------
# Prototypes des primitives ajoutées (PcbDoc.ajouter_piste, ajouter_via, ajouter_region)
# -----------------------------------------------------------------------------
# Valeur de chaque champ du prototype ; les champs non cités valent 0 (octets nuls), comme dans les
# primitives libres observées (rev3, carte source, WiFi, SL1 : pistes et vias libres non verrouillées,
# routées par l'utilisateur, sans keep-out). Les champs propres à chaque ajout (couche, réseau,
# coordonnées, largeur, diamètres) sont fixés par PcbDoc. Longueur du prototype : celle des enregistrements
# existants du même type (versions d'Altium), sinon LONGUEURS_PROTOTYPES.
AUCUN_INDEX = 0xFFFF
VALEURS_COMMUNES = {"drapeaux1": 0x0C, "drapeaux2": 0, "reseau": AUCUN_INDEX, "polygone": AUCUN_INDEX,
                    "composant": AUCUN_INDEX, "inconnu_ffffffff": 0xFFFFFFFF}
VALEURS_PISTE = dict(VALEURS_COMMUNES, sous_poly=0, union=0, restrictions_keepout=0)
VALEURS_VIA = dict(VALEURS_COMMUNES, couche=74, couche_debut=1, couche_fin=32,
                   relief_entrefer=100000, relief_branches=4, relief_largeur=100000,
                   plan_expansion_relief=200000, plan_degagement=200000,
                   masque_expansion_haut=40000, masque_mode=1, mode_pile=0, couche_v7=0x0103000F,
                   masque_bas_separe=0, masque_expansion_bas=40000,
                   inconnu_246=bytes.fromhex("00000000000000002a000000"), masque_depuis_bord_trou=0,
                   guid_bibliotheque="00000000-0000-0000-0000-000000000000",
                   tolerance_plus=0x7FFFFFFF, tolerance_moins=0x7FFFFFFF,
                   inconnu_299=bytes.fromhex("00000000001e00000009000000000000000000000001"))
VALEURS_ENTETE_REGION = dict(VALEURS_COMMUNES, inconnu_13=0, nb_trous=0, inconnu_16=0)
# propriétés texte d'une région de cuivre libre (ordre observé ; V7_LAYER = nom V6 de la couche)
PROPRIETES_REGION = (("V7_LAYER", None), ("NAME", " "), ("KIND", "0"), ("SUBPOLYINDEX", "-1"),
                     ("UNIONINDEX", "0"), ("ARCRESOLUTION", "0.5mil"), ("ISSHAPEBASED", "FALSE"),
                     ("CAVITYHEIGHT", "0mil"))
# propriétés d'un groupe d'outil de perçage de via (DrillManager), ordre observé sur rev3 ;
# HOLEMARK, TOOLSIZE et TEMPLATENAME sont fixés à la création du groupe
PROPRIETES_GROUPE_VIA = (("HOLEMARK", None), ("LAYERPAIR", "0"), ("OBJECTID", "Via"), ("TOOLSIZE", None),
                         ("TOOLLENGTH", "0mil"), ("HOLETYPE", "0"), ("SHAPE", "ROUND"), ("PLATED", "TRUE"),
                         ("TEMPLATENAME", None), ("TEMPLATEDESCRIPTION", ""),
                         ("HOLEPOSITIVETOLERANCE", "214748.3647mil"), ("HOLENEGATIVETOLERANCE", "214748.3647mil"),
                         ("DRILLTYPE", "0"), ("VIASTRUCTURETYPE", "0"), ("VIASTRUCTUREDESCRIPTION", ""),
                         ("VIAFEATUREDESCRIPTION", ""), ("COUNTERHOLEDEPTH", "0mil"),
                         ("COUNTERHOLEANGLE", " 0.00000000000000E+0000"),
                         ("FIELDS", "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15"))
# longueur des sous-enregistrements neufs quand la carte n'en a aucun du même type (longueur observée dans
# tous nos fichiers : rev3, carte source, WiFi, SL1 ; le gabarit PISTE ne couvre que 46 octets)
LONGUEURS_PROTOTYPES = {"piste": 49, "via": 321}


def prototype(gabarit, valeurs, longueur, nom="", **champs) -> Bloc:
    """Bloc neuf de `longueur` octets : octets nuls, puis `valeurs` (table ci-dessus) et `champs`. Un champ
    absent à cette longueur (enregistrement plus court d'une ancienne version) est ignoré."""
    b = decoder_bloc(gabarit, b"\0" * longueur, nom)
    for k, v in list(valeurs.items()) + list(champs.items()):
        if k in b.v and not (isinstance(v, (bytes, bytearray)) and len(v) != len(b.v[k])):
            b.v[k] = v
    return b


def tableau_coords(b: bytes):
    """octets -> liste d'i32 (tableaux par couche des pastilles/vias)."""
    return list(struct.unpack(f"<{len(b) // 4}i", b))


def coords_vers_octets(lst):
    return struct.pack(f"<{len(lst)}i", *lst)
