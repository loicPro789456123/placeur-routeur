# Format des fichiers Altium Designer (.PcbDoc, .SchDoc, .PrjPcb)

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

## 5. Primitives binaires : dispositions

Tailles observées : piste 49, arc 60, remplissage 50, via 321, texte 252 (+ chaîne), pastille sous-enr. 1..6 = nom, 1, 5 (« |&|0 »), 1, 202, 0 ou 651 octets. Connexion (Connections6) : 43.

### Piste (Tracks6, type 4) (`PISTE`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | couche | u8 | id V6 | certain | couche (identifiant V6, voir couches.py) |
| 1 | drapeaux1 | u8 | bits | **confirmé (exp.)** | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| 2 | drapeaux2 | u8 | bits | **confirmé (exp.)** | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| 3 | reseau | u16 | index Nets6 | certain | index du réseau dans Nets6 (0xFFFF = aucun) |
| 5 | polygone | u16 | index Polygons6 | certain | index du polygone propriétaire (0xFFFF = aucun) |
| 7 | composant | u16 | index Components6 | certain | index du composant propriétaire (0xFFFF = libre) |
| 9 | inconnu_ffffffff | u32 |  | inconnu | toujours 0xFFFFFFFF dans nos fichiers |
| 13 | x1 | coord | 1e-4 mil | certain | début X |
| 17 | y1 | coord | 1e-4 mil | certain | début Y |
| 21 | x2 | coord | 1e-4 mil | certain | fin X |
| 25 | y2 | coord | 1e-4 mil | certain | fin Y |
| 29 | largeur | coord | 1e-4 mil | certain | largeur |
| 33 | sous_poly | u16 |  | certain | index de sous-polygone (coulée) |
| 35 | inconnu_35 | u8 |  | inconnu | toujours 0 |
| 36 | union | u32 |  | certain | index d'union (SmartUnions/UnionNames) |
| 40 | inconnu_40 | u8 |  | inconnu | toujours 0 |
| 41 | couche_v7 | u32 | id V7 | certain | couche V7 (32 bits) |
| 45 | restrictions_keepout | u8 | bits | **confirmé (exp.)** | restrictions keepout : 0x1F = toutes, bit0 = vias (exp. E03) ; autres bits supposés (pistes, cuivre, CMS, traversantes) ; 0 si non keepout |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

### Arc (Arcs6, type 1) (`ARC`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | couche | u8 | id V6 | certain | couche (identifiant V6, voir couches.py) |
| 1 | drapeaux1 | u8 | bits | **confirmé (exp.)** | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| 2 | drapeaux2 | u8 | bits | **confirmé (exp.)** | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| 3 | reseau | u16 | index Nets6 | certain | index du réseau dans Nets6 (0xFFFF = aucun) |
| 5 | polygone | u16 | index Polygons6 | certain | index du polygone propriétaire (0xFFFF = aucun) |
| 7 | composant | u16 | index Components6 | certain | index du composant propriétaire (0xFFFF = libre) |
| 9 | inconnu_ffffffff | u32 |  | inconnu | toujours 0xFFFFFFFF dans nos fichiers |
| 13 | cx | coord | 1e-4 mil | certain | centre X |
| 17 | cy | coord | 1e-4 mil | certain | centre Y |
| 21 | rayon | coord | 1e-4 mil | certain | rayon |
| 25 | angle_debut | f64 | degré | certain | angle de départ |
| 33 | angle_fin | f64 | degré | certain | angle de fin |
| 41 | largeur | coord | 1e-4 mil | certain | largeur du trait |
| 45 | sous_poly | u16 |  | certain | index de sous-polygone |
| 47 | inconnu_47 | u8 |  | inconnu | toujours 0 |
| 48 | union | u32 |  | certain | index d'union |
| 52 | couche_v7 | u32 | id V7 | certain | couche V7 |
| 56 | restrictions_keepout | u8 | bits | certain | restrictions keepout |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

### Remplissage (Fills6, type 6) (`REMPLI`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | couche | u8 | id V6 | certain | couche (identifiant V6, voir couches.py) |
| 1 | drapeaux1 | u8 | bits | **confirmé (exp.)** | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| 2 | drapeaux2 | u8 | bits | **confirmé (exp.)** | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| 3 | reseau | u16 | index Nets6 | certain | index du réseau dans Nets6 (0xFFFF = aucun) |
| 5 | polygone | u16 | index Polygons6 | certain | index du polygone propriétaire (0xFFFF = aucun) |
| 7 | composant | u16 | index Components6 | certain | index du composant propriétaire (0xFFFF = libre) |
| 9 | inconnu_ffffffff | u32 |  | inconnu | toujours 0xFFFFFFFF dans nos fichiers |
| 13 | x1 | coord | 1e-4 mil | certain | coin 1 X |
| 17 | y1 | coord | 1e-4 mil | certain | coin 1 Y |
| 21 | x2 | coord | 1e-4 mil | certain | coin 2 X |
| 25 | y2 | coord | 1e-4 mil | certain | coin 2 Y |
| 29 | rotation | f64 | degré | certain | rotation |
| 37 | inconnu_37 | u8 |  | inconnu | toujours 0 |
| 38 | union | u32 |  | supposé | index d'union (par analogie avec les pistes) |
| 42 | couche_v7 | u32 | id V7 | certain | couche V7 |
| 46 | restrictions_keepout | u8 | bits | certain | restrictions keepout |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

### Via (Vias6, type 3) (`VIA`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | couche | u8 | id V6 | supposé | toujours 74 (MULTILAYER) |
| 1 | drapeaux1 | u8 | bits | **confirmé (exp.)** | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| 2 | drapeaux2 | u8 | bits | **confirmé (exp.)** | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| 3 | reseau | u16 | index Nets6 | certain | index du réseau dans Nets6 (0xFFFF = aucun) |
| 5 | polygone | u16 | index Polygons6 | certain | index du polygone propriétaire (0xFFFF = aucun) |
| 7 | composant | u16 | index Components6 | certain | index du composant propriétaire (0xFFFF = libre) |
| 9 | inconnu_ffffffff | u32 |  | inconnu | toujours 0xFFFFFFFF dans nos fichiers |
| 13 | x | coord | 1e-4 mil | certain | position X |
| 17 | y | coord | 1e-4 mil | certain | position Y |
| 21 | diametre | coord | 1e-4 mil | certain | diamètre (mode simple) |
| 25 | trou | coord | 1e-4 mil | certain | diamètre de perçage |
| 29 | couche_debut | u8 | id V6 | certain | couche de départ (1 = TOP) |
| 30 | couche_fin | u8 | id V6 | certain | couche d'arrivée (32 = BOTTOM) |
| 31 | inconnu_31 | u8 |  | inconnu | toujours 0 |
| 32 | relief_entrefer | coord | 1e-4 mil | certain | thermique : entrefer (air gap) |
| 36 | relief_branches | u8 |  | certain | thermique : nombre de branches |
| 37 | inconnu_37 | u8 |  | inconnu | toujours 0 |
| 38 | relief_largeur | coord | 1e-4 mil | certain | thermique : largeur des branches |
| 42 | plan_expansion_relief | coord | 1e-4 mil | supposé | expansion de relief sur plan (20 mil par défaut) |
| 46 | plan_degagement | coord | 1e-4 mil | supposé | dégagement sur plan (20 mil par défaut) |
| 50 | inconnu_50 | u32 |  | inconnu | toujours 0 |
| 54 | masque_expansion_haut | coord | 1e-4 mil | **confirmé (exp.)** | expansion masque de soudure (haut) (exp. E05) |
| 58 | inconnu_58 | octets:8 |  | inconnu | varie 0/1 octet par octet (61..64) : drapeaux ? |
| 66 | masque_mode | u8 | enum | **confirmé (exp.)** | mode d'expansion du masque : 1 règle, 2 manuelle (exp. E05) |
| 67 | inconnu_67 | u8 |  | inconnu | toujours 0 ; KiCad y lit « depuis le bord du trou » : NON confirmé (exp. E05 : c'est l'octet 258) |
| 68 | inconnu_68 | octets:6 |  | inconnu | 70: 0/2 ; 72: 0/0x10 |
| 74 | mode_pile | u8 |  | certain | 0 simple, 1 haut-milieu-bas, 2 pile complète |
| 75 | diametres_couches | octets:128 | 32 x coord | certain | diamètre par couche cuivre (32 x i32) |
| 203 | couche_v7 | u32 | id V7 | supposé | couche V7 (toujours 0x0103000F = MULTILAYER) |
| 207 | inconnu_207 | octets:34 |  | inconnu | zéros sauf 242..245 = 4 mil |
| 241 | masque_bas_separe | u8 | bool | **confirmé (exp.)** | 1 = expansion bas distincte de l'expansion haut (exp. E05) |
| 242 | masque_expansion_bas | coord | 1e-4 mil | **confirmé (exp.)** | expansion masque de soudure (bas) (exp. E05) |
| 246 | inconnu_246 | octets:12 |  | inconnu | 254 = 0x2A |
| 258 | masque_depuis_bord_trou | u8 | bool | **confirmé (exp.)** | expansion mesurée depuis le bord du trou (exp. E05) |
| 259 | guid_gabarit | guid |  | supposé | GUID du gabarit pad/via (propre à chaque via) |
| 275 | guid_bibliotheque | guid |  | supposé | GUID de la bibliothèque pad/via (= PadViaLibraryCache LIBRARYID) |
| 291 | tolerance_plus | coord | 1e-4 mil | **confirmé (exp.)** | tolérance de perçage + (0x7FFFFFFF = non définie) |
| 295 | tolerance_moins | coord | 1e-4 mil | **confirmé (exp.)** | tolérance de perçage - (0x7FFFFFFF = non définie) |
| 299 | inconnu_299 | octets:22 |  | inconnu | 304 = 0x1E, 308 = 0x09, 320 = 0x01 |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

### Pastille, sous-enregistrement 5 (Pads6, type 2) (`PASTILLE`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | couche | u8 | id V6 | certain | couche (identifiant V6, voir couches.py) |
| 1 | drapeaux1 | u8 | bits | **confirmé (exp.)** | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| 2 | drapeaux2 | u8 | bits | **confirmé (exp.)** | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| 3 | reseau | u16 | index Nets6 | certain | index du réseau dans Nets6 (0xFFFF = aucun) |
| 5 | polygone | u16 | index Polygons6 | certain | index du polygone propriétaire (0xFFFF = aucun) |
| 7 | composant | u16 | index Components6 | certain | index du composant propriétaire (0xFFFF = libre) |
| 9 | inconnu_ffffffff | u32 |  | inconnu | toujours 0xFFFFFFFF dans nos fichiers |
| 13 | x | coord | 1e-4 mil | certain | position X |
| 17 | y | coord | 1e-4 mil | certain | position Y |
| 21 | taille_haut_x | coord | 1e-4 mil | certain | taille X couche haute |
| 25 | taille_haut_y | coord | 1e-4 mil | certain | taille Y couche haute |
| 29 | taille_milieu_x | coord | 1e-4 mil | certain | taille X couches internes |
| 33 | taille_milieu_y | coord | 1e-4 mil | certain | taille Y couches internes |
| 37 | taille_bas_x | coord | 1e-4 mil | certain | taille X couche basse |
| 41 | taille_bas_y | coord | 1e-4 mil | certain | taille Y couche basse |
| 45 | trou | coord | 1e-4 mil | certain | diamètre de trou (0 = CMS) |
| 49 | forme_haut | u8 | enum | certain | 1 rond, 2 rectangle, 3 octogone (rect. arrondi : voir pile) |
| 50 | forme_milieu | u8 | enum | certain | idem couches internes |
| 51 | forme_bas | u8 | enum | certain | idem couche basse |
| 52 | rotation | f64 | degré | certain | rotation de la pastille |
| 60 | metallise | u8 | bool | certain | trou métallisé |
| 61 | inconnu_61 | u8 |  | inconnu | toujours 0 |
| 62 | mode_pile | u8 |  | certain | 0 simple, 1 haut-milieu-bas, 2 pile complète (sous-enr. 6) |
| 63 | inconnu_63 | u32 |  | inconnu | toujours 0 |
| 67 | inconnu_67 | u8 |  | inconnu | 0/1 |
| 68 | relief_entrefer | coord | 1e-4 mil | supposé | thermique : entrefer (analogie via) |
| 72 | relief_branches | u8 |  | supposé | thermique : nombre de branches |
| 73 | inconnu_73 | u8 |  | inconnu | toujours 0 |
| 74 | relief_largeur | coord | 1e-4 mil | supposé | thermique : largeur des branches |
| 78 | plan_expansion_relief | coord | 1e-4 mil | supposé | expansion relief plan |
| 82 | plan_degagement | coord | 1e-4 mil | supposé | dégagement plan |
| 86 | pate_expansion | coord | 1e-4 mil | **confirmé (exp.)** | expansion masque de pâte (manuelle) ; -99999 mil quand la pâte est désactivée (exp. E08) |
| 90 | masque_expansion | coord | 1e-4 mil | **confirmé (exp.)** | expansion masque de soudure (manuelle, haut) (exp. E09) |
| 94 | inconnu_94 | octets:7 |  | inconnu | 96: 0/1 ; 97..100 = 1 |
| 101 | pate_mode | u8 | enum | certain | 0 aucune, 1 règle, 2 manuelle |
| 102 | masque_mode | u8 | enum | **confirmé (exp.)** | 0 aucune, 1 règle, 2 manuelle (exp. E09) |
| 103 | inconnu_103 | octets:3 |  | inconnu | 103 = 1, 104: 0/1 |
| 106 | inconnu_106 | u32 |  | inconnu | toujours 0 (KiCad y lit une rotation de trou f64 : contredit par l'exp. E08b) |
| 110 | jumper_id | u16 |  | **confirmé (exp.)** | identifiant de cavalier (Jumper ID) (exp. E08b) |
| 112 | inconnu_112 | u16 |  | inconnu | toujours 0 |
| 114 | vers_couche | u8 | id V6 | supposé | octet 0 de la couche V7 de la pastille (114..117 = u32 V7 : 0x01000001 TOP, 0x0100FFFF BOTTOM, 0x0103000F MULTI ; vérifié sur 3060 pastilles et 95 retournements Altium de rev5_auto) ; KiCad : couche d'arrivée |
| 115 | inconnu_115 | u16 |  | supposé | octets 1-2 de la couche V7 (0 TOP, 0x00FF BOTTOM, 0x0300 MULTI) |
| 117 | depuis_couche | u8 | id V6 | supposé | octet 3 de la couche V7 (famille, toujours 1) ; KiCad : couche de départ |
| 118 | inconnu_118 | octets:2 |  | inconnu | toujours 0 |
| 120 | masque_bas_separe | u8 | bool | **confirmé (exp.)** | 1 = expansion bas distincte (exp. E09) |
| 121 | masque_expansion_bas | coord | 1e-4 mil | **confirmé (exp.)** | expansion masque de soudure (bas) (exp. E09) |
| 125 | inconnu_125 | u8 |  | inconnu | toujours 0 |
| 126 | guid_gabarit | guid |  | supposé | GUID du gabarit pad/via |
| 142 | guid_bibliotheque | guid |  | supposé | GUID de la bibliothèque pad/via (= PadViaLibraryCache) |
| 158 | longueur_pastille_puce | coord | 1e-4 mil | certain | pad-to-die length |
| 162 | tolerance_plus | coord | 1e-4 mil | **confirmé (exp.)** | tolérance de perçage + (0x7FFFFFFF = non définie) (exp. E09) |
| 166 | tolerance_moins | coord | 1e-4 mil | **confirmé (exp.)** | tolérance de perçage - (exp. E09) |
| 170 | inconnu_170 | octets:15 |  | inconnu | 171 = 1, 172 = 0x1A, 184 = 1 |
| 185 | pate_active | u8 | enum | **confirmé (exp.)** | 3 = masque de pâte actif, 1 = désactivé (exp. E08a) |
| 186 | inconnu_186 | octets:8 |  | inconnu | toujours 0 |
| 194 | delai_pastille_puce | f64 | s | certain | pad-to-die delay (secondes) |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

### Pastille, sous-enregistrement 6 : pile complète (`PASTILLE_PILE`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | tailles_x_internes | octets:116 | 29 x coord | certain | taille X pour MID2..MID30 (MID1 : taille_milieu du sous-enr. 5 ; vérifié sur les 6208 couches de la table par couche des pastilles en pile complète de Bluetooth_Sentinel) |
| 116 | tailles_y_internes | octets:116 | 29 x coord | certain | taille Y pour MID2..MID30 |
| 232 | formes_internes | octets:29 | 29 x u8 | certain | forme pour MID2..MID30 (MID1 : forme_milieu) |
| 261 | inconnu_261 | u8 |  | inconnu | toujours 0 |
| 262 | forme_trou | u8 | enum | certain | 0 rond, 1 carré, 2 oblong (slot) |
| 263 | longueur_oblong | coord | 1e-4 mil | certain | longueur du trou oblong |
| 267 | rotation_oblong | f64 | degré | certain | rotation du trou oblong |
| 275 | decalages_trou_x | octets:128 | 32 x coord | certain | décalage X par couche (0 TOP, k MIDk, 31 BOTTOM) ; « Offset From Hole Center » d'Altium : décalage du cuivre par rapport au centre du trou, repère de la pastille (sens supposé : annexe E) |
| 403 | decalages_trou_y | octets:128 | 32 x coord | certain | décalage Y par couche (même convention) |
| 531 | inconnu_531 | u8 |  | inconnu | toujours 0 |
| 532 | formes_alt | octets:32 | 32 x u8 | certain | forme alternative par couche (0 TOP, k MIDk, 31 BOTTOM) : 9 = rectangle arrondi, quelle que soit la forme de base (comme l'importeur KiCad ; observé avec une forme de base ronde) ; 1 sinon (même sur une pastille rectangulaire : sans effet) |
| 564 | rayons_coins | octets:32 | 32 x u8 | certain | rayon de coin en % par couche (même indexation) |
| 596 | inconnu_596 | octets:55 |  | inconnu | fin de la pile (651 octets) : 628=1, 632=0x0F, 636=4... ; les versions récentes ajoutent ensuite une table de 32 x 15 octets (u32 couche V7, u8 forme, coord x, coord y, u16 = 50), conservée brute |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

### Texte, sous-enregistrement 1 (Texts6, type 5) (`TEXTE`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | couche | u8 | id V6 | certain | couche |
| 1 | drapeaux1 | u8 | bits | supposé | comme les autres primitives (0x04 = non verrouillé) |
| 2 | drapeaux2 | u8 | bits | supposé | 0x02 keepout |
| 3 | reseau | u16 |  | supposé | index réseau (0xFFFF) |
| 5 | polygone | u16 |  | supposé | index polygone |
| 7 | composant | u16 | index Components6 | certain | composant propriétaire |
| 9 | inconnu_ffffffff | u32 |  | inconnu | 0xFFFFFFFF |
| 13 | x | coord | 1e-4 mil | certain | position X (coin d'ancrage) |
| 17 | y | coord | 1e-4 mil | certain | position Y |
| 21 | hauteur | coord | 1e-4 mil | certain | hauteur du texte |
| 25 | police_trait | u16 | enum | certain | police trait : 1 défaut, 2 sans serif, 3 serif |
| 27 | rotation | f64 | degré | **confirmé (exp.)** | rotation (exp. E13 : Altium recalcule x/y autour du point de rotation) |
| 35 | miroir | u8 | bool | certain | texte en miroir |
| 36 | epaisseur_trait | coord | 1e-4 mil | certain | épaisseur de trait (police trait) |
| 40 | est_commentaire | u8 | bool | certain | texte = commentaire du composant |
| 41 | est_designateur | u8 | bool | certain | texte = désignateur du composant |
| 42 | inconnu_42 | u8 |  | inconnu | 0/1 |
| 43 | type_police | u8 | enum | **confirmé (exp.)** | 1 = TrueType, 0 sinon (trait ou code-barres) (exp. E14) |
| 44 | gras | u8 | bool | certain | gras (TrueType) |
| 45 | italique | u8 | bool | certain | italique (TrueType) |
| 46 | nom_police | utf16:64 |  | certain | nom de la police TrueType |
| 110 | inverse | u8 | bool | **confirmé (exp.)** | texte inversé (exp. E15) |
| 111 | marge_inversion | coord | 1e-4 mil | certain | largeur de bordure d'inversion |
| 115 | index_chaine_large | u32 | index WideStrings6 | certain | index dans WideStrings6 (texte Unicode) |
| 119 | inconnu_119 | u32 |  | inconnu |  |
| 123 | rect_inverse | u8 | bool | **confirmé (exp.)** | inversion par rectangle (exp. E15) |
| 124 | rect_largeur | coord | 1e-4 mil | certain | largeur du cadre |
| 128 | rect_hauteur | coord | 1e-4 mil | certain | hauteur du cadre |
| 132 | justification | u8 | enum | certain | 1..9 : gauche-haut ... droite-bas |
| 133 | decalage_texte | coord | 1e-4 mil | certain | décalage du texte dans le cadre |
| 137 | codebarre_largeur | coord | 1e-4 mil | **confirmé (exp.)** | largeur totale du code-barres (exp. E14 ; 1050 mil par défaut) |
| 141 | codebarre_hauteur | coord | 1e-4 mil | **confirmé (exp.)** | hauteur totale du code-barres (exp. E14 ; 210 mil par défaut) |
| 145 | codebarre_marge_x | coord | 1e-4 mil | **confirmé (exp.)** | marge code-barres X (exp. E14) |
| 149 | codebarre_marge_y | coord | 1e-4 mil | **confirmé (exp.)** | marge code-barres Y (exp. E14) |
| 153 | codebarre_largeur_min | coord | 1e-4 mil | supposé | largeur minimale de barre (calculée par Altium : 4,63 mil à l'exp. E14 ; 0 sinon) |
| 157 | codebarre_type | u8 | enum | certain | 0 Code39, 1 Code128 |
| 158 | inconnu_158 | u8 |  | inconnu |  |
| 159 | codebarre_inverse | u8 | bool | certain |  |
| 160 | type_texte | u8 | enum | **confirmé (exp.)** | 0 trait, 1 TrueType, 2 code-barres (exp. E14) |
| 161 | codebarre_police | utf16:64 |  | certain | police du code-barres |
| 225 | inconnu_225 | u8 |  | inconnu |  |
| 226 | couche_v7 | u32 | id V7 | certain | couche V7 |
| 230 | est_cadre | u8 | bool | certain | texte dans un cadre (frame) |
| 231 | bordure_decalage | u8 | bool | certain | utilise la bordure « offset » au lieu de « margin » |
| 232 | inconnu_232 | octets:8 |  | inconnu |  |
| 240 | justification_valide | u8 | bool | certain | la justification est significative |
| 241 | inconnu_241 | octets:3 |  | inconnu | toujours 0 |
| 244 | ref_x | coord | 1e-4 mil | supposé | point de référence X (proche de x ; ancrage avant décalage de justification ?) ; 0 sur les textes anciens |
| 248 | ref_y | coord | 1e-4 mil | supposé | point de référence Y |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

### En-tête de région / corps 3D (types 11, 12) (`ENTETE_REGION`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | couche | u8 | id V6 | certain | couche (identifiant V6, voir couches.py) |
| 1 | drapeaux1 | u8 | bits | **confirmé (exp.)** | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| 2 | drapeaux2 | u8 | bits | **confirmé (exp.)** | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| 3 | reseau | u16 | index Nets6 | certain | index du réseau dans Nets6 (0xFFFF = aucun) |
| 5 | polygone | u16 | index Polygons6 | certain | index du polygone propriétaire (0xFFFF = aucun) |
| 7 | composant | u16 | index Components6 | certain | index du composant propriétaire (0xFFFF = libre) |
| 9 | inconnu_ffffffff | u32 |  | inconnu | toujours 0xFFFFFFFF dans nos fichiers |
| 13 | inconnu_13 | u8 |  | inconnu | toujours 0 |
| 14 | nb_trous | u16 |  | certain | nombre de trous (contours intérieurs) |
| 16 | inconnu_16 | u16 |  | inconnu | toujours 0 |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

### Connexion (Connections6) (`CONNEXION`)

| déc. | champ | type | unité | statut | signification |
|---:|---|---|---|---|---|
| 0 | couche | u8 | id V6 | certain | couche (identifiant V6, voir couches.py) |
| 1 | drapeaux1 | u8 | bits | **confirmé (exp.)** | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| 2 | drapeaux2 | u8 | bits | **confirmé (exp.)** | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| 3 | reseau | u16 | index Nets6 | certain | index du réseau dans Nets6 (0xFFFF = aucun) |
| 5 | polygone | u16 | index Polygons6 | certain | index du polygone propriétaire (0xFFFF = aucun) |
| 7 | composant | u16 | index Components6 | certain | index du composant propriétaire (0xFFFF = libre) |
| 9 | inconnu_ffffffff | u32 |  | inconnu | toujours 0xFFFFFFFF dans nos fichiers |
| 13 | x1 | coord | 1e-4 mil | supposé | extrémité 1 X |
| 17 | y1 | coord | 1e-4 mil | supposé | extrémité 1 Y |
| 21 | x2 | coord | 1e-4 mil | supposé | extrémité 2 X |
| 25 | y2 | coord | 1e-4 mil | supposé | extrémité 2 Y |

Les octets non listés sont conservés bruts sous le nom `inconnu_<décalage>`.

Pastille, sous-enregistrements 1 à 4 : chaînes courtes (u8 longueur + octets). 1 = nom (désignateur) de la pastille ; 2 et 4 = chaîne vide ; 3 = « |&|0 » (sens inconnu). Texte, sous-enregistrement 2 : chaîne courte 8 bits (copie tronquée du texte ; la référence est WideStrings6).


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

## 9. Dictionnaire des clés de propriétés, par flux

Motifs = expressions régulières sur la clé (`\d+` = indice). Les clés communes (SELECTION, LAYER, LOCKED, POLYGONOUTLINE, USERROUTED, KEEPOUT, UNIONINDEX, PRIMITIVELOCK, UNIQUEID) sont répétées dans chaque flux d'objets.

### Board6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `KIND\d+` | certain | type du sommet # : 0 segment, 1 arc |
| `VX\d+¦VY\d+` | certain | sommet # (X, Y) avec unité |
| `CX\d+¦CY\d+` | certain | centre de l'arc du sommet # |
| `SA\d+¦EA\d+` | certain | angles de début / fin de l'arc du sommet # |
| `R\d+` | certain | rayon de l'arc du sommet # |
| `RECORD¦KIND¦VERSION¦FILENAME¦DATE¦TIME` | certain | en-tête : type d'enregistrement, version 5.01, chemin, date/heure d'enregistrement |
| `ORIGINX¦ORIGINY` | certain | origine de la carte |
| `SHEETX¦SHEETY¦SHEETWIDTH¦SHEETHEIGHT¦SHOWSHEET¦LOCKSHEET` | certain | feuille (position, taille, affichage, verrou) |
| `(BIG)?VISIBLEGRID(SIZE¦MULTFACTOR)¦DOTGRID¦GRIDSIZE¦GRIDSNAPENABLED` | certain | grilles visibles / pas d'accrochage |
| `(SNAP¦COMPONENT¦TRACK¦VIA)GRIDSIZE[XY]?` | certain | pas de grilles (unité interne 1e-4 mil, flottant) |
| `ELECTRICALGRID\w*¦EG\w+` | certain | grille électrique (activée, portée, accrochages) |
| `GR\d+_\w+` | certain | gestionnaire de grilles : grille # (nom, type, pas, origine, rotation, couleurs, priorité) |
| `(MGSNAP¦OGSNAP¦POINTGUIDE)ENABLED` | supposé | accrochages (guides manuels, objets, points) |
| `DISPLAYUNIT` | certain | unité d'affichage (0 impérial, 1 métrique) |
| `DESIGNATORDISPLAYMODE` | supposé | mode d'affichage des désignateurs (physique/logique) |
| `LAYER\d+(NAME¦PREV¦NEXT¦MECHENABLED¦COPTHICK¦DIELTYPE¦DIELCONST¦DIELHEIGHT¦DIELMATERIAL)` | certain | empilage V6 : couche # (nom, précédente, suivante, méca activée, épaisseur cuivre, type/Er/épaisseur/matériau diélectrique) |
| `(LAYER_V8¦V9_STACK_LAYER¦V9_CACHE_LAYER)\d+_\w*\{.*\}\w+` | supposé | empilage : contexte par sous-empilage (GUID) de la couche # |
| `LAYERV7_\d+\w+` | certain | couches mécaniques V7 (17+) : identifiant, nom, type mécanique, activée |
| `LAYER_V8_\d+\w+¦V9_STACK_LAYER\d+_\w+` | certain | empilage V8/V9 : couche # (ID GUID, LAYERID, nom, épaisseur cuivre COPTHICK, diélectrique DIELTYPE 0 aucun/1 cœur/2 préimprégné/3 masque, DIELCONST = Er, DIELHEIGHT, DIELMATERIAL, DIELLOSSTANGENT, orientation cuivre, placement composants, utilisée) |
| `V9_CACHE_LAYER\d+_\w+` | supposé | cache de l'empilage V9 (copie, inclut les couches non cuivre) |
| `V9_STACKCUSTOMDATA` | supposé | données personnalisées de l'empilage (base64 + zlib) |
| `(V9_)?(LAYER)?MASTERSTACK(_V8)?_?\w+` | certain | empilage maître (ID, nom, style, flex, diélectriques extérieurs) |
| `(V9_SUBSTACK\d+_¦LAYERSUBSTACK_V8_\d+)\w+` | certain | sous-empilage # (ID, nom, type, flex, service, utilisé) |
| `LAYERSTACKSTYLE¦SHOWTOPDIELECTRIC¦SHOWBOTTOMDIELECTRIC¦LAYERCOUNT` | supposé | options d'empilage |
| `(TOP¦BOTTOM)(TYPE¦CONST¦HEIGHT¦MATERIAL)` | certain | masque de soudure haut/bas : type, Er, épaisseur, matériau |
| `LAYERPAIR\d+\w*` | certain | paire de perçage # (couche basse/haute, guide, dessin, sous-empilage) |
| `LAYERSET\w*¦SHOWDEFAULTSETS` | certain | jeux de couches (nom, couches, couche active, retournement) |
| `MECHPAIR\d+L\d` | certain | paires de couches mécaniques |
| `PLANE\d+NETNAME` | certain | réseau affecté au plan interne # |
| `(TOP¦BOTTOM)LAYER_MRLASTWIDTH¦MIDLAYER\d+_MRLASTWIDTH¦MRLASTVIA(SIZE¦HOLE)¦TRACKWIDTH¦LASTTARGETLENGTH` | supposé | dernières largeurs / via utilisées en routage interactif |
| `ROUTINGDIRECTION.*` | certain | direction de routage préférée par couche |
| `TOGGLELAYERS¦FAROBJECTS?(SET¦ENABLED)¦NEAROBJECTS?(SET¦ENABLED)¦NEARDISTANCE¦ZOOMMULT` | supposé | affichage : couches basculées, objets proches/lointains |
| `VP\.[LH][XY]¦VIEWSIZE\.[XY]¦LOOKAT\.[XYZ]¦EYEROTATION\.[XYZ]¦CURRENT2D3DVIEWSTATE¦2DCONFIG\w*¦3DCONFIG\w*¦VIEWPORTSAREVISIBLE¦BOARDINSIGHTVIEWCONFIGURATIONNAME` | supposé | état de la vue 2D/3D enregistrée |
| `[23]DCONFIG\w*` | supposé | configuration de vue 2D/3D (sérialisée) |
| `SELECTIONMEMORYLOCK\d+` | supposé | mémoires de sélection verrouillées |
| `PLACEMARKER[XY]\d+` | supposé | marqueurs de placement |
| `FN#?\d+¦FV#?\d+¦VALUECOUNT` | supposé | paramètres nommés (nom / valeur) : options de méandres, etc. |
| `HASHKEY#?\d+¦HASHVALUE#?\d+¦HOLESHAPEHASHSIZE` | supposé | table de hachage des formes de trous |
| `DRILLSYMBOL(SIZE¦ASENUM)` | certain | symboles de perçage |
| `(SURFACEMICROSTRIP¦SYMMETRICSTRIPLINE)_[IW]` | certain | formules d'impédance (calculateur) |
| `TEARDROPPARAM_\w+` | certain | paramètres des gouttes (teardrops) |
| `SILKSCREENCLIPPER_\w+\s*` | supposé | découpe de la sérigraphie au contour |
| `NAME¦HATCHSTYLE¦GRIDSIZE¦POURINDEX¦POUROVER(STYLE)?¦REMOVEDEAD¦REMOVEISLANDSBYAREA¦AREATHRESHOLD¦REMOVENECKS¦NECKWIDTHTHRESHOLD¦ARCRESOLUTION¦MINPRIMLENGTH¦USEOCTAGONS¦SHELVED¦RESTORELAYER¦RESTORENET¦POLYGONTYPE¦IGNOREVIOLATIONS` | supposé | paramètres de polygone attachés au contour de carte (format commun aux polygones) |
| `OUTLINEMODEL(NAME¦CRC)` | supposé | modèle de contour importé |
| `SPLITLINECOUNT¦PINPAIRCOUNT` | supposé | compteurs (lignes de séparation de plans, paires de broches) |
| `SHOWSIGNALLAYERSONLY` | supposé | affichage restreint aux couches signal |

### Components6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `X¦Y` | certain | position de l'origine du composant |
| `ROTATION` | certain | rotation (degrés, format flottant Delphi) |
| `HEIGHT` | certain | hauteur du composant |
| `PATTERN` | certain | nom de l'empreinte |
| `SOURCEDESIGNATOR` | certain | désignateur (repère) |
| `SOURCEUNIQUEID` | certain | chemin d'identifiants uniques du composant schéma (\FEUILLE\COMPOSANT) : lien schéma |
| `SOURCEHIERARCHICALPATH` | certain | chemin hiérarchique de la feuille source |
| `SOURCE(LIBREFERENCE¦COMPONENTLIBRARY¦FOOTPRINTLIBRARY¦DESCRIPTION¦COMPLIBRARYIDENTIFIER¦COMPLIBIDENTIFIERKIND)` | certain | référence et bibliothèques source |
| `FOOTPRINTDESCRIPTION¦UNICODE__FOOTPRINTDESCRIPTION` | certain | description de l'empreinte (UNICODE__ = codes de caractères séparés par virgules) |
| `UNICODE` | certain | indique la présence de clés UNICODE__* |
| `UNICODE__\w+` | certain | version Unicode d'un champ texte (codes décimaux séparés par des virgules) |
| `NAMEON¦COMMENTON` | certain | désignateur / commentaire visibles |
| `NAMEAUTOPOSITION¦COMMENTAUTOPOSITION` | certain | positionnement automatique du désignateur / commentaire (0 manuel, 1..9) |
| `GROUPNUM¦COUNT¦CHANNELOFFSET` | supposé | groupe, compteur, décalage de canal (multicanal) |
| `ITEMGUID¦ITEMREVISIONGUID¦VAULTGUID¦REVISIONGUID` | certain | liens coffre (vault) du composant |
| `JUMPERSVISIBLE` | certain | cavaliers visibles |
| `AREA` | supposé | aire de l'empreinte (unité interne²) |
| `COMPONENTKIND¦FLIPPEDONLAYER¦ENABLEPINSWAPPING¦ENABLEPARTSWAPPING¦DEFAULTPCB3DMODEL¦LOCKSTRINGS¦SWAPPEDPADSTRING¦COMPONENTSIDE` | supposé | options diverses |

### Nets6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `NAME` | certain | nom du réseau |
| `COLOR¦OVERRIDECOLORFORDRAW¦VISIBLE` | certain | couleur et visibilité |
| `(TOP¦BOTTOM)LAYER_MRWIDTH¦MIDLAYER\d+_MRWIDTH` | supposé | dernière largeur de routage utilisée par couche |
| `LOOPREMOVAL¦JUMPERSVISIBLE` | certain | suppression des boucles, cavaliers |
| `ROUTEDLENGTH¦MANHATTANLENGTH` | supposé | longueur routée / Manhattan (unité interne) |
| `CURRENTTOTAL¦DELAYTOTAL¦RESISTANCETOTAL` | supposé | analyses : courant, délai, résistance cumulés |

### Classes6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `NAME` | certain | nom de la classe |
| `KIND` | certain | type : 0 réseaux, 1 composants, 2 from-to, 3 pastilles, 4 couches, 6 paires diff., 7 polygones |
| `M\d+` | certain | membre # |
| `SUPERCLASS` | certain | classe « tous » (superclasse) |
| `SELECTED` | supposé | classe sélectionnée |
| `AUTOGENERATEDCLASS(KIND)?¦SCHAUTOGENERATEDCLUSTER` | supposé | classe générée automatiquement (et son type) |

### SignalClasses

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `NAME¦KIND¦SUPERCLASS¦SELECTED¦SCHAUTOGENERATEDCLUSTER¦M\d+` | supposé | classes de xSignals (même format que Classes6) |

### Rules6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `KIND\d+` | certain | type du sommet # : 0 segment, 1 arc |
| `VX\d+¦VY\d+` | certain | sommet # (X, Y) avec unité |
| `CX\d+¦CY\d+` | certain | centre de l'arc du sommet # |
| `SA\d+¦EA\d+` | certain | angles de début / fin de l'arc du sommet # |
| `R\d+` | certain | rayon de l'arc du sommet # |
| `RULEKIND` | certain | type de règle (Clearance, Width, RoutingVias, PlaneClearance, PolygonConnect, Height, RoomDefinition...) |
| `NAME¦COMMENT¦UNICODE__COMMENT¦UNICODE` | certain | nom et commentaire |
| `ENABLED` | certain | règle activée |
| `PRIORITY` | certain | priorité (1 = la plus forte) |
| `SCOPE\d+EXPRESSION¦SCOPE\d+_\w+` | certain | requête de portée # (1 = premier objet, 2 = second) |
| `NETSCOPE¦LAYERKIND¦SCOPECOUNT` | certain | portée réseaux (DifferentNets, SameNet, AnyNet) / couches |
| `DEFINEDBYLOGICALDOCUMENT` | certain | règle définie depuis le schéma (directive) |
| `UNIQUEID` | certain | identifiant de la règle |
| `GAP¦GENERICCLEARANCE¦OBJECTCLEARANCES¦IGNOREPADTOPADCLEARANCEINFOOTPRINT¦MOSTFREQGAP¦ABSOLUTEVALUES` | certain | dégagement : valeur générique, matrice par types d'objets |
| `(MIN¦MAX¦PREFERED)WIDTH¦(TOP¦BOTTOM)LAYER_(MIN¦MAX¦PREF)WIDTH¦MIDLAYER\d+_(MIN¦MAX¦PREF)WIDTH¦(TOP¦BOTTOM) LAYER_V\d¦MID LAYER \d+_V\d` | certain | largeur : min/préférée/max, par couche |
| `(MIN¦MAX¦PREFERED)(HOLE)?(SIZE¦WIDTH)¦HOLEWIDTH¦VIASTYLE` | certain | vias / trous : tailles min/préf/max |
| `(MIN¦MAX¦PREF)HEIGHT` | certain | hauteur de composant |
| `EXPANSION¦ISTENTINGTOP¦ISTENTINGBOTTOM` | certain | expansion masque (soudure/pâte), tenting |
| `CLEARANCE¦RELIEF(AIRGAP¦CONDUCTORWIDTH¦ENTRIES¦EXPANSION)¦PLANECONNECTSTYLE` | certain | plans : dégagement, style de connexion, thermique |
| `(SMDPAD\.¦THPAD\.¦VIA\.)?(CONNECTSTYLE¦AIRGAPWIDTH¦RELIEFCONDUCTORWIDTH¦RELIEFENTRIES¦POLYGONRELIEFANGLE)` | certain | connexion polygone : style, entrefer, largeur, branches, angle (par type d'objet) |
| `(MIN¦MAX)LIMIT¦MINSOLDERMASKWIDTH¦MINSILKSCREENTOMASKGAP¦SILKTOSILKCLEARANCE¦CLEARANCETOEXPOSEDCOPPER¦NETANTENNAETOLERANCE` | certain | limites diverses (trous, masque, sérigraphie, antennes) |
| `CONFINEMENTSTYLE¦LOCKCOMPONENTS¦SIDE¦REGIONKIND¦BOUNDARYLAYER¦LOCKED` | certain | room : confinement (dedans/dehors), verrou, face |
| `TOPOLOGY¦ROUTINGPRIORITY¦CORNERSTYLE¦(MIN¦MAX)SETBACK¦ALLOWED¦LAYER\d+\w*` | certain | topologie, priorité, coins, couches de routage |
| `FANOUT\w+¦BGA\w+` | certain | options de fanout |
| `(MIN¦MAX)PERCENT¦POLYGONREMOVAL\w*¦DEADCOPPER\w*` | supposé | pourcentages / cuivre mort |
| `TESTPOINT\w+¦USEGRID¦GRIDTOLERANCE¦VIAGRID¦ALLOWSIDE(TOP¦BOTTOM)¦ALLOWMULTIPLE¦TESTPOINTUNDERCOMPONENT` | certain | points de test |
| `ALLOWSTACKEDMICROVIAS¦MAXUNCOUPLEDLENGTH¦VERTICALGAP¦COLLISIONCHECKMODE¦SHOWDISTANCES¦FORMATCOPY¦ENFORCE¦VALID¦VOLTAGE¦MAXHOLESIZE¦MINHOLESIZE¦MAXSIZE¦MINSIZE¦PREFEREDSIZE¦PREFEREDHOLESIZE` | supposé | paramètres divers de règle |
| `[A-Z0-9_.]+` | supposé | autre paramètre de règle (nom explicite, valeur avec unité) |

### Polygons6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `KIND\d+` | certain | type du sommet # : 0 segment, 1 arc |
| `VX\d+¦VY\d+` | certain | sommet # (X, Y) avec unité |
| `CX\d+¦CY\d+` | certain | centre de l'arc du sommet # |
| `SA\d+¦EA\d+` | certain | angles de début / fin de l'arc du sommet # |
| `R\d+` | certain | rayon de l'arc du sommet # |
| `NAME¦AUTONAME` | certain | nom (UNICODE codes séparés par virgules possible), nommage auto |
| `NET` | certain | index du réseau (Nets6) |
| `POLYGONTYPE` | certain | Polygon / Split plane / Signal layer |
| `HATCHSTYLE` | certain | Solid, 45Degree, 90Degree, Horizontal, Vertical, None |
| `POUROVER(STYLE)?` | certain | couler par-dessus les objets du même réseau |
| `REMOVEDEAD¦REMOVEISLANDSBYAREA¦AREATHRESHOLD¦REMOVENECKS¦NECKWIDTHTHRESHOLD¦NECKWIDTHFROMRULE` | certain | suppression des îlots / cuivre mort / cols |
| `GRIDSIZE¦TRACKWIDTH¦MINPRIMLENGTH¦ARCRESOLUTION¦USEOCTAGONS` | certain | hachures : grille, largeur, longueur min, résolution d'arc |
| `POURINDEX` | certain | ordre de coulée |
| `IGNOREVIOLATIONS¦OBEYPOLYGONCUTOUT¦OPTIMALVOIDROTATION¦SHELVED¦RESTORELAYER¦RESTORENET` | certain | options (ignorer violations, respecter découpes, mis de côté) |

### Dimensions6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `[A-Z0-9_.]+` | supposé | paramètres de cote (DIMENSIONKIND, X1/Y1, REFERENCE#X/Y, TEXTX/Y, hauteurs, flèches, unités) |

### Models

| motif de clé | statut | signification |
|---|---|---|
| `ID` | certain | GUID du modèle (référencé par MODELID des corps 3D) |
| `NAME` | certain | nom de fichier du modèle |
| `EMBED` | certain | modèle incorporé (flux Models/N, N = rang de l'enregistrement) |
| `MODELSOURCE` | certain | origine |
| `ROTX¦ROTY¦ROTZ¦DZ` | certain | rotation / décalage Z par défaut |
| `CHECKSUM` | certain | somme de contrôle du modèle |
| `(vide)` | supposé | élément vide |

### ComponentBodies6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `MODEL\.\w+(\.\w+)*` | certain | modèle 3D : nom, incorporé, position 2D (X, Y), rotation, décalage DZ, rotations X/Y/Z, type, extrusion, somme |
| `MODELID` | certain | GUID du modèle (Models) |
| `BODYCOLOR3D¦BODYOPACITY3D¦BODYPROJECTION` | certain | couleur, opacité, face de projection |
| `IDENTIFIER` | certain | identifiant (codes de caractères séparés par virgules) |
| `STANDOFFHEIGHT¦OVERALLHEIGHT¦CAVITYHEIGHT` | certain | hauteurs |
| `TEXTURE\w*` | certain | texture |
| `NAME¦KIND¦SUBPOLYINDEX¦ARCRESOLUTION¦ISSHAPEBASED` | certain | communs aux régions |

### Regions6

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `NAME¦KIND¦SUBPOLYINDEX¦ARCRESOLUTION¦ISSHAPEBASED¦ISBOARDCUTOUT¦CAVITYHEIGHT` | certain | région : nom, type (0 cuivre, 1 découpe de polygone, 2 contour pointillé, 4 cavité), sous-polygone, découpe de carte |
| `KEEPOUTRESTRICTIONS` | certain | restrictions keepout |
| `OBJECTKIND¦LAYERSTACKID¦BENDINGLINECOUNT¦LOCKED3D` | certain | région de carte : type, empilage associé, lignes de pliage |
| `UNIONINDEX¦UNIQUEID` | certain |  |

### PrimitiveParameters

| motif de clé | statut | signification |
|---|---|---|
| `PRIMITIVEID` | certain | UNIQUEID de la primitive porteuse |
| `ID¦APPURTENANCE¦VARIANTGUID¦COUNT¦NAME¦VALUE¦ISIMPORTED` | certain | paramètre (nom, valeur) d'un composant / d'une empreinte |
| `(vide)` | supposé | élément vide |

### UniqueIDPrimitiveInformation

| motif de clé | statut | signification |
|---|---|---|
| `PRIMITIVEINDEX¦PRIMITIVEOBJECTID¦UNIQUEID` | certain | UNIQUEID d'une primitive (type + index dans son flux) |
| `(vide)` | supposé | élément vide |

### SmartUnions

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `KIND\d+` | certain | type du sommet # : 0 segment, 1 arc |
| `VX\d+¦VY\d+` | certain | sommet # (X, Y) avec unité |
| `CX\d+¦CY\d+` | certain | centre de l'arc du sommet # |
| `SA\d+¦EA\d+` | certain | angles de début / fin de l'arc du sommet # |
| `R\d+` | certain | rayon de l'arc du sommet # |
| `[A-Z0-9_.]+` | supposé | union intelligente (tableau de vias de couture, etc.) : paramètres explicites |

### DrillManager

| motif de clé | statut | signification |
|---|---|---|
| `[A-Z0-9_.]+` | certain | outil de perçage : objet, taille, forme, métallisé, gabarit, tolérances, type |
| `(vide)` | supposé | élément vide |

### FileVersionInfo

| motif de clé | statut | signification |
|---|---|---|
| `COUNT¦VER\d+¦FWDMSG\d+¦BKMSG\d+¦GUID\d+¦ISDYNAMIC\d+` | certain | historique de versions du format (messages de compatibilité, codes de caractères) |
| `(vide)` | supposé | élément vide |

### Advanced Placer Options6

| motif de clé | statut | signification |
|---|---|---|
| `RECORD` | certain | type d'enregistrement |
| `[A-Z0-9_.]+` | certain | option nommée explicitement (valeur booléenne/numérique) |
| `(vide)` | supposé | élément vide |

### Design Rule Checker Options6

| motif de clé | statut | signification |
|---|---|---|
| `RECORD` | certain | type d'enregistrement |
| `[A-Z0-9_.]+` | certain | option nommée explicitement (valeur booléenne/numérique) |
| `(vide)` | supposé | élément vide |

### Pin Swap Options6

| motif de clé | statut | signification |
|---|---|---|
| `RECORD` | certain | type d'enregistrement |
| `[A-Z0-9_.]+` | certain | option nommée explicitement (valeur booléenne/numérique) |
| `(vide)` | supposé | élément vide |

### PadViaLibrary

| motif de clé | statut | signification |
|---|---|---|
| `RECORD` | certain | type d'enregistrement |
| `[A-Z0-9_.]+` | certain | option nommée explicitement (valeur booléenne/numérique) |
| `(vide)` | supposé | élément vide |

### PadViaLibraryCache

| motif de clé | statut | signification |
|---|---|---|
| `RECORD` | certain | type d'enregistrement |
| `[A-Z0-9_.]+` | certain | option nommée explicitement (valeur booléenne/numérique) |
| `(vide)` | supposé | élément vide |

### PadViaLibraryLinks

| motif de clé | statut | signification |
|---|---|---|
| `RECORD` | certain | type d'enregistrement |
| `[A-Z0-9_.]+` | certain | option nommée explicitement (valeur booléenne/numérique) |
| `(vide)` | supposé | élément vide |

### TSilkToSolderMaskClearanceViola

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `[A-Z0-9_.]+` | certain | violation DRC enregistrée : description, règle, emplacement, primitives impliquées |

### TNetAntennaeViolation

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `[A-Z0-9_.]+` | certain | violation DRC enregistrée : description, règle, emplacement, primitives impliquées |

### WaivedViolations

| motif de clé | statut | signification |
|---|---|---|
| `SELECTION` | certain | objet sélectionné à l'enregistrement |
| `LAYER` | certain | couche (nom V6 : TOP, MID1, BOTTOM, MULTILAYER, MECHANICAL13...) |
| `LAYER_V7¦V7_LAYER` | certain | couche (nom V7, pour MECHANICAL17+) |
| `LOCKED` | certain | verrouillé |
| `POLYGONOUTLINE` | certain | fait partie d'un contour de polygone |
| `USERROUTED` | certain | routé par l'utilisateur (vs autorouteur) |
| `KEEPOUT` | certain | objet keepout |
| `UNIONINDEX` | certain | index d'union (SmartUnions) |
| `PRIMITIVELOCK` | certain | primitives verrouillées (composant/polygone) |
| `UNIQUEID` | certain | identifiant unique 8 lettres (lien schéma pour les composants) |
| `(vide)` | supposé | élément vide (séparateur initial '¦') |
| `[A-Z0-9_.]+` | certain | violation DRC enregistrée : description, règle, emplacement, primitives impliquées |

### ShapeBasedComponentBodies6

Même dictionnaire que le flux équivalent ci-dessus.

### ShapeBasedRegions6

Même dictionnaire que le flux équivalent ci-dessus.

### BoardRegions

Même dictionnaire que le flux équivalent ci-dessus.

## 10. Table des couches V6

0 UNKNOWN, 1 TOP, 2 MID1, 3 MID2, 4 MID3, 5 MID4, 6 MID5, 7 MID6, 8 MID7, 9 MID8, 10 MID9, 11 MID10, 12 MID11, 13 MID12, 14 MID13, 15 MID14, 16 MID15, 17 MID16, 18 MID17, 19 MID18, 20 MID19, 21 MID20, 22 MID21, 23 MID22, 24 MID23, 25 MID24, 26 MID25, 27 MID26, 28 MID27, 29 MID28, 30 MID29, 31 MID30, 32 BOTTOM, 33 TOPOVERLAY, 34 BOTTOMOVERLAY, 35 TOPPASTE, 36 BOTTOMPASTE, 37 TOPSOLDER, 38 BOTTOMSOLDER, 39 PLANE1, 40 PLANE2, 41 PLANE3, 42 PLANE4, 43 PLANE5, 44 PLANE6, 45 PLANE7, 46 PLANE8, 47 PLANE9, 48 PLANE10, 49 PLANE11, 50 PLANE12, 51 PLANE13, 52 PLANE14, 53 PLANE15, 54 PLANE16, 55 DRILLGUIDE, 56 KEEPOUT, 57 MECHANICAL1, 58 MECHANICAL2, 59 MECHANICAL3, 60 MECHANICAL4, 61 MECHANICAL5, 62 MECHANICAL6, 63 MECHANICAL7, 64 MECHANICAL8, 65 MECHANICAL9, 66 MECHANICAL10, 67 MECHANICAL11, 68 MECHANICAL12, 69 MECHANICAL13, 70 MECHANICAL14, 71 MECHANICAL15, 72 MECHANICAL16, 73 DRILLDRAWING, 74 MULTILAYER, 75 CONNECTIONS, 76 BACKGROUND, 77 DRCERRORMARKERS, 78 SELECTIONS, 79 VISIBLEGRID1, 80 VISIBLEGRID2, 81 PADHOLES, 82 VIAHOLES
