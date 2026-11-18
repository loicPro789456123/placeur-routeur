"""Génère la description du format (FORMAT.md) à partir des gabarits (gabarits.py) et du dictionnaire de clés
(cles.py), complétés par les sections rédigées ci-dessous. Source unique de vérité : le code.

Usage : python generer_format.py --sortie FICHIER      (par exemple --sortie FORMAT.md)
Aucun fichier n'est écrit par défaut. Codes de sortie : 0 succès ; 2 entrée invalide ; 4 erreur interne.
"""
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

from altium_fichier import gabarits as G  # noqa: E402
from altium_fichier.cles import CLES  # noqa: E402
from altium_fichier import couches  # noqa: E402
from altium_fichier import commandes as K  # noqa: E402

STATUT = {"E": "**confirmé (exp.)**", "C": "certain", "S": "supposé", "I": "inconnu"}

INTRO = r"""# Format des fichiers Altium Designer (.PcbDoc, .SchDoc, .PrjPcb)

Document généré par `generer_format.py` à partir du code de `altium_fichier/` (gabarits binaires et
dictionnaire des clés) : ce qui est écrit ici est exactement ce que la bibliothèque décode.

**Statuts.** *certain* = décrit par une source tierce (importeur Altium de KiCad, AltiumSharp) **et**
cohérent avec nos trois cartes (valeurs plausibles, recoupements entre flux) ; *supposé* = déduit de
nos données, d'un nom de clé explicite ou par analogie, non confirmé ; *inconnu* = position isolée,
sens inconnu, octets conservés bruts. *confirmé (exp.)* = vérifié par une expérience différentielle dans Altium 26
(modification d'un seul objet, enregistrement, comparaison avec `differentiel.py` ; voir §11).

Fichiers étudiés : PCB_METROID_V2.PcbDoc (original), _rev3, _rev4 (Altium 2x, en-tête « PCB 6.0 Binary File »,
VERSION=5.01), Carte Metroid V2.SchDoc, PSU.SchDoc, Carte Metroid V2.PrjPcb.

## 1. Conteneur

Un .PcbDoc / .SchDoc est un fichier composé OLE2 / CFB ([MS-CFB]) version 3 : secteurs de 512 octets,
mini-secteurs de 64 octets, seuil du mini-flux 4096 octets. Le .PcbDoc contient, à la racine, deux flux
`FileHeader` et `FileHeaderSix`, puis un *storage* par type d'objet ; chaque storage contient en général
`Header` (u32 = nombre d'enregistrements de `Data`) et `Data`. Exceptions : `Models` (en plus `0`..`N-1`,
un flux par modèle 3D incorporé), `LettersGeometry` (en plus `PrimIndexes`).

Écriture : `cfb.ecrire_cfb` produit un CFB v3 conforme (FAT, DIFAT au-delà de 109 secteurs FAT, mini-FAT,
arbre rouge-noir équilibré valide trié selon la règle CFB longueur-puis-majuscules), en conservant
CLSID et horodatages de chaque entrée. La disposition des secteurs n'est pas celle d'Altium (le fichier
entier n'est donc pas identique octet pour octet) mais chaque flux l'est.

## 2. Conventions communes

- Entiers petit-boutistes. **Coordonnées binaires** : i32 en unités internes de 1/10000 mil (2,54 nm) ;
  l'origine est l'origine absolue de la feuille (Board6 ORIGINX/ORIGINY donnent l'origine utilisateur).
  Y croît vers le haut.
- **Enregistrement de propriétés** : `u32 L` puis `L` octets `|CLE=VALEUR|CLE=VALEUR...\0`. L'octet haut
  de `L` est un drapeau : 0 = texte, ≠ 0 = contenu binaire (rencontré dans le flux Storage des .SchDoc).
  Rules6 et Dimensions6 ont un préfixe de 2 octets avant `L` (Rules6 : u16 = code du type de règle,
  observés : 0 Clearance, 2 Width, 3 Length, 15 ShortCircuit, 16 UnRoutedNet, 17 ViasUnderSMD, 20 PolygonConnect, 22 RoomDefinition ; Altium insère les nouvelles règles en tête du flux). Texte en Windows-1252 ; une clé
  `%UTF8%CLE` ou `UNICODE__CLE` (codes décimaux séparés par des virgules, avec `UNICODE=EXISTS`) donne la
  version Unicode. Certaines valeurs sont toujours en codes décimaux (Polygons6 NAME, IDENTIFIER des corps 3D,
  FileVersionInfo).
- Valeurs : longueurs `12.5mil` (4 décimales max, parfois `mm`), booléens `TRUE`/`FALSE` (SchDoc : `T`),
  flottants Delphi ` 9.00000000000000E+0001` (rotations), couleurs en entier BGR Windows.
- **Primitive binaire** : `u8 type` (1 arc, 2 pastille, 3 via, 4 piste, 5 texte, 6 remplissage, 11 région,
  12 corps 3D) puis un nombre fixe de sous-enregistrements `u32 L + L octets` (1, sauf pastille 6 et texte 2).
  Les versions récentes allongent les sous-enregistrements par la fin : un lecteur doit lire selon la
  longueur réelle (c'est ce que fait `gabarits.decoder_bloc`).
- **En-tête commun des primitives** (13 octets) : couche (u8, V6), drapeaux1, drapeaux2, réseau (u16),
  polygone (u16), composant (u16), u32 toujours 0xFFFFFFFF. Les index renvoient au rang de l'enregistrement
  dans Nets6 / Polygons6 / Components6 (0xFFFF = aucun).
- **Couches** : V6 sur un octet (1 TOP, 2..31 MID1..30, 32 BOTTOM, 33/34 sérigraphie haut/bas, 35/36 pâte,
  37/38 masque, 39..54 plans, 55 guide de perçage, 56 keepout, 57..72 mécaniques 1..16, 73 dessin de perçage,
  74 multicouche) ; V7 sur 32 bits : 0x0100xxxx cuivre (1 = TOP, 0xFFFF = BOTTOM, n = MID(n-1)),
  0x0101xxxx plans, 0x0102xxxx mécaniques (n = 1..1024), 0x0103xxxx autres (6 sérigraphie haut, 7 bas,
  8/9 pâte, 10/11 masque, 13 keepout, 15 multicouche...). Vérifié sur nos fichiers : MID2 ↔ 0x01000003,
  BOTTOM ↔ 0x0100FFFF, MECHANICAL15 ↔ 0x0102000F.

## 3. Où trouver quoi (exigences de la mission)

| information | emplacement |
|---|---|
| Empilage : nombre de couches, noms, cuivre, diélectriques, matériaux, Er | Board6 `V9_STACK_LAYERn_*` (NAME, LAYERID, COPTHICK, DIELTYPE 0/1 cœur/2 préimprégné/3 masque, DIELCONST = Er, DIELHEIGHT, DIELMATERIAL, DIELLOSSTANGENT) ; anciens formats `LAYER_V8_n*`, `LAYERn*` ; masques `TOP/BOTTOM{TYPE,CONST,HEIGHT,MATERIAL}` ; sous-empilages `V9_SUBSTACKn_*` ; données brutes `V9_STACKCUSTOMDATA` (base64+zlib) — `PcbDoc.empilage()` |
| Contour de carte | Board6 `KINDn/VXn/VYn/CXn/CYn/SAn/EAn/Rn` — `PcbDoc.contour_carte()` ; BoardRegions (région de carte liée à l'empilage) |
| Découpes de carte | Regions6 / ShapeBasedRegions6 avec `ISBOARDCUTOUT=TRUE` (KIND=0, couche KEEPOUT) |
| Règles (type, portée, valeurs, priorité, activée) | Rules6 : `RULEKIND`, `NAME`, `SCOPE1EXPRESSION`/`SCOPE2EXPRESSION`, `PRIORITY`, `ENABLED`, + paramètres propres au type — `PcbDoc.regles()` |
| Rooms | Rules6 `RULEKIND=RoomDefinition` : contour `KIND/VX/VY...`, `CONFINEMENTSTYLE`, `LAYER`, portée |
| Classes de réseaux / composants | Classes6 : `NAME`, `KIND` (0 réseaux, 1 composants, 3 pastilles, 4 couches, 6 paires diff., 7 polygones), membres `M0..Mn`, `SUPERCLASS` ; xSignals : SignalClasses |
| Réseaux | Nets6 (rang = index réseau des primitives) : `NAME`, `COLOR`, `VISIBLE`, longueurs |
| Composants | Components6 (rang = index composant) : `X`,`Y`,`ROTATION`,`LAYER` (face TOP/BOTTOM), `LOCKED`, `PATTERN` (empreinte), `SOURCEDESIGNATOR`, `UNIQUEID`, `SOURCEUNIQUEID` (lien schéma : dernier segment = UniqueID du composant dans le .SchDoc, vérifié : 37/37 composants de « Carte Metroid V2 »), `NAMEON`/`COMMENTON` (désignateur / commentaire masqués) ; paramètres : PrimitiveParameters |
| Pastilles | Pads6 : sous-enr. 1 = nom, 5 = géométrie (formes haut/milieu/bas, tailles, trou, métallisé, rotation, modes masque/pâte et expansions, verrouillage dans drapeaux1), 6 = pile complète (formes et tailles par couche, trou rond/carré/oblong, longueur et rotation de l'oblong, décalages, rayons de coins) |
| Vias | Vias6 : couches de départ/arrivée, diamètre, trou, tenting (drapeaux1 0x20/0x40), expansions de masque, thermiques, diamètres par couche, tolérances |
| Pistes, arcs, remplissages, régions | Tracks6, Arcs6, Fills6, Regions6/ShapeBasedRegions6 : réseau, couche (V6+V7), keepout (drapeaux2 = 2 + restrictions), verrouillé (drapeaux1 bit 0x04 à 0), composant, polygone, sous-polygone |
| Polygones | Polygons6 : contour, `NET`, `HATCHSTYLE`, `POUROVER`, `REMOVEDEAD`, `GRIDSIZE`, `TRACKWIDTH`, `POURINDEX`, `POLYGONTYPE` ; **cuivre coulé** = primitives Regions6/Tracks6/Arcs6 dont le champ `polygone` = rang du polygone |
| Textes | Texts6 : police trait (`police_trait`) ou TrueType (`type_police`, `nom_police`, gras, italique), miroir, désignateur/commentaire, cadre ; chaîne : WideStrings6[`index_chaine_large`] (UTF-16), copie 8 bits dans le sous-enr. 2 |
| Cotes | Dimensions6 (vide dans nos cartes ; format propriétés avec préfixe 2 octets) |
| Corps 3D / modèles | ComponentBodies6 (+ ShapeBased) : région + `MODEL.*` ; Models/Data : un enregistrement par modèle (`ID`, `NAME`, `EMBED`, `CHECKSUM`) ; Models/N : STEP compressé zlib |
| Origine, grilles, affichage | Board6 `ORIGINX/Y`, `SNAPGRIDSIZE*`, `GRn_*`, `VISIBLEGRID*`, `DISPLAYUNIT`, `TOGGLELAYERS`, `VP.*`, `LAYERSET*` |
| Perçages | DrillManager : un groupe par outil (propriétés + liste des pastilles + liste des vias) |
| GUID de chaque objet | PrimitiveGuids : (u32 code = (rang du flux << 8) | type d'objet, u32 rang, GUID) |
| UniqueID des pastilles | UniqueIDPrimitiveInformation |

## 4. Tous les flux du .PcbDoc

| storage | codec | contenu |
|---|---|---|
| FileHeader | entete | u32 19 + « PCB 5.0 Binary File » UTF-16 tronqué à 24 octets |
| FileHeaderSix | entete6 | u32 + chaîne courte « PCB 6.0 Binary File », f64 5.01, u32 + chaîne courte GUID du document |
| Board6 | propriétés | un enregistrement : options de la carte, empilage, contour, grilles, vue |
| Components6, Nets6, Classes6, Polygons6, Rules6*, Dimensions6* | propriétés | un enregistrement par objet (*préfixe 2 octets) |
| Arcs6, Pads6, Vias6, Tracks6, Texts6, Fills6 | primitives | voir §5 |
| Regions6, ComponentBodies6, BoardRegions | primitives (région, sommets en f64) | voir §6 |
| ShapeBasedRegions6, ShapeBasedComponentBodies6 | primitives (région, sommets étendus) | mêmes objets que les précédents, sommets avec arcs (§6) |
| Texts | primitives | textes de la feuille / gabarits (3 dans nos cartes) |
| WideStrings6 | chaînes | (u32 index, u32 longueur octets, UTF-16LE + NUL) |
| Models | propriétés + flux N | Data : modèles 3D ; N : STEP zlib |
| ModelsNoEmbed | propriétés | modèles 3D liés non incorporés (vide) |
| EmbeddedFonts6 | polices | par police : 3 chaînes (u32+UTF-16 : famille, nom, style), 3 octets inconnus, u32 longueur, TTF zlib |
| LayerKindMapping | types de couches | u32+UTF-16 « 1.0 », u32 inconnu, u32 n, n × (u32 id de couche V7, u32 type mécanique) |
| LettersGeometry | brut | cache de la géométrie des glyphes TrueType : **confirmé recalculé par Altium** (exp. E10 : flux vidé, Altium ouvre le fichier et le régénère à l'enregistrement) ; PrimIndexes : 64 octets nuls |
| PrimitiveGuids | GUID | 24 octets par objet |
| PrimitiveParameters | propriétés | paramètres de composants (PRIMITIVEID = UNIQUEID du composant) |
| UniqueIDPrimitiveInformation | propriétés | UNIQUEID des pastilles |
| DrillManager | perçage | i32 -1, u32 nombre de groupes, groupes, u32 final 0 |
| Connections6 | blocs | chevelu (connexions non routées), 43 octets par connexion ; vide dans rev3/rev4 (recalculé) |
| ConstraintManager | chaîne UTF-16 | u32 + UTF-16 : base64 d'un zlib (« eNoDAAAAAAE= » = vide) |
| SmartUnions, UnionNames | propriétés / compteur | unions (tableaux de vias de couture...) |
| SignalClasses | propriétés | classes de xSignals |
| PadViaLibrary, PadViaLibraryCache, PadViaLibraryLinks | propriétés | bibliothèque pad/via locale (GUID référencé par les pastilles et vias) |
| Advanced Placer Options6, Design Rule Checker Options6, Pin Swap Options6 | propriétés | options |
| TSilkToSolderMaskClearanceViola, TNetAntennaeViolation, WaivedViolations | propriétés | violations DRC enregistrées / dérogations |
| FileVersionInfo | propriétés | messages de compatibilité de version (codes de caractères) |
| Coordinates6, DifferentialPairs6, EmbeddedBoards6, Embeddeds6, ExtendedPrimitiveInformation, FromTos6, PinPairsSection, Textures | propriétés (vides ici) | objets absents de nos cartes |
"""

FIN = r"""
## 11. Expériences menées dans Altium 26 (15/11/2026)

Tests d'ouverture : les 7 fichiers écrits par la bibliothèque s'ouvrent sans aucun message
(aller-retour, composant déplacé, piste, via, texte, les 4 ensemble, cache de lettres vidé) ;
comptes d'objets identiques, modifications relues par requêtes (M4 en X = 5533,07 mil avec ses
pastilles, piste et via GND en (300..500, 300), texte à Y + 50 mil, 3 lignes de chevelu attendues
vers l'ancienne position de M4).

Champs volatils (réécrits à chaque enregistrement, exp. E00) : FileHeaderSix (GUID), Board6
FILENAME/DATE/TIME/UNIQUEID/VP.*, `guid_gabarit` des pastilles (même GUID pour toutes les pastilles
d'un enregistrement), PrimitiveGuids, LettersGeometry ; Altium recalcule aussi Nets6 MANHATTANLENGTH,
les flux de violations `T*Violation` et peut **réordonner Texts6**. Les octets après le NUL des noms de
police UTF-16 contiennent des restes de mémoire (à ignorer).

Board6 `HASHKEYn` = table des formes de trous `[diamètre][?][?][1][tol+ tol-]`, `HASHVALUEn` = nombre
d'utilisations ; mise à jour quand une tolérance change (exp. E09).
"""

FIN = FIN + r"""
## 6. Régions et corps 3D (types 11 et 12)

Sous-enregistrement unique : en-tête de 18 octets (tableau ci-dessus « ENTETE_REGION »), puis
`u32 L` + propriétés texte (sans `|` initial), puis `u32 n` sommets :
- flux « normaux » (Regions6, ComponentBodies6, BoardRegions) : n × (f64 x, f64 y) en unités internes ;
- flux « ShapeBased » : n+1 sommets étendus de 37 octets : u8 est_arc, i32 x, i32 y, i32 cx, i32 cy,
  i32 rayon, f64 angle1, f64 angle2 (le dernier referme le contour) ;
puis, pour chacun des `nb_trous` trous : u32 m + m × (f64 x, f64 y).
Vérifié sur 2587 enregistrements : aucun octet restant.

## 7. .SchDoc (lecture)

CFB avec `FileHeader` (enregistrements de propriétés : en-tête `HEADER=Protel for Windows - Schematic Capture
Binary File Version 5.0`, `Weight` = nombre d'objets, puis un objet par enregistrement), `Storage` (images),
`Additional`. Les clés mélangent les casses (`Location.X`, `OwnerIndex`) : lecture insensible à la casse.
`OWNERINDEX` = rang (en-tête exclu) du parent. Coordonnées `LOCATION.X` en unités de 10 mil (+ `_FRAC`
en 1/100000). Types (`RECORD`) : 1 composant (`LIBREFERENCE`, `UNIQUEID`, `CURRENTPARTID`, `PARTCOUNT`),
2 broche (`DESIGNATOR`, `NAME`, `ELECTRICAL`, `PINLENGTH`), 4 étiquette, 6 polyligne, 13 ligne, 14 rectangle,
15/16 symbole/entrée de feuille, 17 port d'alimentation, 18 port, 25 étiquette de réseau, 27 fil
(`LOCATIONCOUNT`, `X1..`, `Y1..`), 29 jonction, 31 options de feuille, 34 désignateur, 41 paramètre
(`NAME`, `TEXT`), 44 liste d'implémentations, 45 implémentation (`MODELTYPE=PCBLIB`, `MODELNAME` = empreinte,
`ISCURRENT`), 46/47/48 correspondances broches-pastilles. Aller-retour vérifié octet pour octet sur les
flux FileHeader et Additional.

## 8. .PrjPcb (lecture)

Fichier INI UTF-8 avec BOM : `[Design]` (options du projet), `[Preferences]`, `[DocumentN]`
(`DocumentPath`, options d'annotation, de classes...), `[GeneratedDocumentN]`, `[OutputGroupN]`,
`[Configuration]`, `[ProjectVariantN]`, `[ParameterN]`, etc. Lu par `schdoc.PrjPcb` (texte conservé tel quel).
"""


def table_gabarit(nom, gab, titre):
    out = [f"### {titre} (`{nom}`)", "", "| déc. | champ | type | unité | statut | signification |", "|---:|---|---|---|---|---|"]
    for off, ch, typ, unite, st, desc in sorted(gab, key=lambda c: c[0]):
        out.append(f"| {off} | {ch} | {typ} | {unite} | {STATUT[st]} | {desc.replace('|', '¦')} |")
    out.append("")
    out.append("Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.")
    out.append("")
    return out


def texte_format():
    """Texte complet de la description du format."""
    lignes = [INTRO, "## 5. Primitives binaires : dispositions", ""]
    lignes += ["Tailles observées : piste 49, arc 60, remplissage 50, via 321, texte 252 (+ chaîne), pastille "
               "sous-enr. 1..6 = nom, 1, 5 (« |&|0 »), 1, 202, 0 ou 651 octets. Connexion (Connections6) : 43.", ""]
    for nom, titre in [("PISTE", "Piste (Tracks6, type 4)"), ("ARC", "Arc (Arcs6, type 1)"),
                       ("REMPLI", "Remplissage (Fills6, type 6)"), ("VIA", "Via (Vias6, type 3)"),
                       ("PASTILLE", "Pastille, sous-enregistrement 5 (Pads6, type 2)"),
                       ("PASTILLE_PILE", "Pastille, sous-enregistrement 6 : pile complète"),
                       ("TEXTE", "Texte, sous-enregistrement 1 (Texts6, type 5)"),
                       ("ENTETE_REGION", "En-tête de région / corps 3D (types 11, 12)"),
                       ("CONNEXION", "Connexion (Connections6)")]:
        lignes += table_gabarit(nom, getattr(G, nom), titre)
    lignes += ["Pastille, sous-enregistrements 1 à 4 : chaînes courtes (u8 longueur + octets). 1 = nom (désignateur) "
               "de la pastille ; 2 et 4 = chaîne vide ; 3 = « |&|0 » (sens inconnu). Texte, sous-enregistrement 2 : "
               "chaîne courte 8 bits (copie tronquée du texte ; la référence est WideStrings6).", ""]
    lignes.append(FIN)
    lignes += ["## 9. Dictionnaire des clés de propriétés, par flux", "",
               "Motifs = expressions régulières sur la clé (`\\d+` = indice). Les clés communes (SELECTION, LAYER, LOCKED, "
               "POLYGONOUTLINE, USERROUTED, KEEPOUT, UNIONINDEX, PRIMITIVELOCK, UNIQUEID) sont répétées dans chaque "
               "flux d'objets.", ""]
    vus = set()
    for st, motifs in CLES.items():
        cle = id(motifs)
        if cle in vus:
            lignes.append(f"### {st}\n\nMême dictionnaire que le flux équivalent ci-dessus.\n")
            continue
        vus.add(cle)
        lignes += [f"### {st}", "", "| motif de clé | statut | signification |", "|---|---|---|"]
        for m, s, d in motifs:
            lignes.append(f"| `{m.replace('|', '¦') or '(vide)'}` | {STATUT[s]} | {d.replace('|', '¦')} |")
        lignes.append("")
    lignes += ["## 10. Table des couches V6", "", ", ".join(f"{k} {v}" for k, v in sorted(couches.NOMS_V6.items())), ""]
    return "\n".join(lignes)


def parseur():
    p = K.Parseur(prog="generer_format.py", description="Génère la description du format des fichiers Altium.")
    p.add_argument("--sortie", required=True, metavar="FICHIER", help="fichier Markdown à écrire (FORMAT.md)")
    return p


def _executer(a):
    K.ecrire_texte_atomique(a.sortie, texte_format())
    print(f"{K.nom_seul(a.sortie)} écrit")
    return K.CODE_OK


def main(argv=None) -> int:
    return K.executer(_executer, parseur(), argv)


if __name__ == "__main__":
    sys.exit(main())
