# `altium_fichier` : API (jalon B1)

Bibliothèque de lecture et d'écriture directe des `.PcbDoc` d'Altium Designer, sans Altium (contrat 2.1 §8.2,
« Fournit aux autres (B1) »). Python 3.10+, bibliothèque standard et `olefile`. Ce document décrit chaque fonction
publique des modules de l'API ; `tests/test_api.py` vérifie que chacune y figure et que chaque nom cité existe.

```python
import sys; sys.path.insert(0, "PCB_V2/outils/format_altium")   # chemin relatif à la racine du dépôt
from altium_fichier import PcbDoc, ErreurPcbDoc, pose
```

## 1. Conventions communes

- **Unités** : mil (1 mil = 0,0254 mm) et **repère du fichier** (coordonnées absolues de la feuille ; l'origine
  affichée par Altium est `PcbDoc.origine()`). En interne, les primitives binaires stockent des entiers en 1/10000
  de mil (`proprietes.mil_vers_interne`, arrondi au plus proche) : une valeur relue vaut l'arrondi au 1/10000 mil de
  la valeur écrite. Angles en degrés, sens trigonométrique.
- **Couches** : noms Altium V6 (`TOP`, `MID1`…`MID30`, `PLANE1`…`PLANE16`, `BOTTOM`, `TOPOVERLAY`, `MULTILAYER`,
  `MECHANICAL<n>`…) dans les accesseurs ; `PcbDoc.couches_cuivre()` donne aussi les noms du contrat (`TopLayer`,
  `MidLayer<n>`, `InternalPlane<n>`, `BottomLayer`) et le **rang physique**. Les ajouts acceptent les deux noms.
- **Index** : `0xFFFF` (`pcbdoc.AUCUN`) = aucun réseau, polygone ou composant. `0xFFFE`
  (`pcbdoc.POLYGONE_RETRAIT`) = trait du contour de retrait d'un plan négatif (généré par Altium).
- **Erreurs** : `pcbdoc.ErreurPcbDoc` (exception) pour une entrée invalide : fichier introuvable, pas un conteneur
  OLE2, conteneur illisible, `Board6` absent ou vide, réseau ou couche inconnus, valeur hors domaine, écriture sur
  le fichier ouvert. Message en français, **nom du fichier seul, jamais de chemin** (§0.2-7) ; l'importeur la
  traduit en code 2. `pose.ErreurPose` : pose ou suppression impossible de façon sûre.
- **Aucune décision** n'est prise ici (refus d'un panneau, d'une carte rigide-flexible, rôles, octogone converti
  en polygone, mm, repère commun) : c'est le rôle de l'importeur (lot B) et des générateurs (lot C).
- **Compatibilité** (garantie pour la vague 1) : les clés et le sens des accesseurs du commit de base sont gardés ;
  on n'ajoute que des clés. Seule exception, voulue par le contrat (§2.2) : `composants()["designateur"]` suit la
  règle de `PcbDoc.designateurs()` ; l'ancien sens (`SOURCEDESIGNATOR`) est sous `designateur_source`.
- **Déterminisme** : même fichier d'entrée et mêmes appels dans le même ordre -> fichier écrit identique octet pour
  octet (GUID neufs déterministes : `PcbDoc.nouveau_guid`).

## 2. `PcbDoc` : ouverture, écriture, flux

| Fonction | Rôle |
|---|---|
| `PcbDoc.ouvrir(chemin)` | Lit un `.PcbDoc` et rend un `PcbDoc`. Attributs : `racine` (arbre CFB, `cfb.Noeud`), `chemin`, `nom` (nom du fichier seul), `flux` ({chemin du flux : objet décodé}). `ErreurPcbDoc` : introuvable, pas OLE2, illisible, `Board6` absent ou vide. Un flux `Data` vide d'un stockage connu se lit comme un flux plein sans enregistrement. |
| `PcbDoc.enregistrer(chemin)` | Ré-encode tous les flux (un flux non modifié redonne ses octets d'origine), recompte les `Header` des stockages modifiés dont le `Header` comptait les enregistrements (jamais ceux des options, de `UnionNames`, de `LayerKindMapping` ; `DrillManager` : 1 dès qu'il a un groupe), crée les stockages connus absents qui ont reçu des enregistrements (`Header` + `Data`), et écrit de façon **atomique** (`<nom>.tmp` puis `os.replace`, réessayé 5 fois toutes les 0,2 s sur `PermissionError` ; aucun `.tmp` laissé). `ErreurPcbDoc` si `chemin` désigne le fichier ouvert (le fichier lu n'est jamais remplacé). |
| `PcbDoc.octets_flux(chemin)` | Octets ré-encodés d'un flux (`"Tracks6/Data"`…). |
| `PcbDoc.existe(storage)` | `True` si le stockage (ou flux de premier niveau) est présent dans le fichier **lu**. |
| `PcbDoc.data(storage)` | Objet décodé de `<storage>/Data` : `flux.FluxProps` (liste de `proprietes.Proprietes` ; `f[i]`, `len(f)`, `f.ajouter(props)`), `flux.FluxPrimitives` (`f.primitives`, liste de `flux.Primitive` ; `pr.bloc` = `gabarits.Bloc` des champs binaires), etc. Stockage connu **absent** : objet vide du type du flux plein (toujours le même objet ; créé à l'écriture s'il reçoit des enregistrements). Stockage inconnu absent : `None`. |
| `PcbDoc.marquer(storage)` | Signale une modification faite directement sur `data(storage)` (Header recompté à l'écriture). Obligatoire après une modification de `Nets6` (table des réseaux mémorisée) ou de l'empilage de `Board6`. Les fonctions d'ajout et de `pose` marquent elles-mêmes. |
| `PcbDoc.board` | Propriété : `proprietes.Proprietes` du premier enregistrement de `Board6`. |
| `PcbDoc.nouveau_guid(objet="")` | `uuid.UUID` neuf et déterministe (version 5) dérivé du GUID du document (`FileHeaderSix`, à défaut du nom du fichier), de `objet` et d'un compteur d'appels ; jamais deux fois le même. |
| `PcbDoc.chaines_larges()` | {index : texte} de `WideStrings6`. |

## 3. `PcbDoc` : lecture (liste `pcbdoc.ACCESSEURS` : accesseurs sans argument)

Chaque accesseur rend des types simples (listes, dicts, nombres, textes) ; aucun ne lève d'exception sur une carte
lisible par `PcbDoc.ouvrir` (flux vides, stockages absents, panneau, rigide-flexible compris).

| Fonction | Rendu (mil, repère du fichier) |
|---|---|
| `PcbDoc.reseaux()` | Noms des réseaux, dans l'ordre de `Nets6` (index = rang). |
| `PcbDoc.index_reseau(nom)` | Rang du réseau dans `Nets6` (`0xFFFF` pour `None`) ; `ErreurPcbDoc` s'il est inconnu. Table mémorisée (coût constant). |
| `PcbDoc.nom_reseau(idx)` | Nom du réseau d'index `idx` ; `None` pour `0xFFFF` ou hors table. Mémorisé. |
| `PcbDoc.designateurs()` | {rang `Components6` : désignateur} selon §2.2 : texte du désignateur affiché (`Texts6` marqué désignateur, chaîne large comprise ; le dernier s'il y en a plusieurs ; vide ou espaces = absent), sinon `SOURCEDESIGNATOR`, sinon `"__piece_<rang>"`. Doublons rendus tels quels. Remplace `serigraphie/pcbdoc_serigraphie.designateurs` (lot C). |
| `PcbDoc.commentaires()` | {rang : texte du commentaire affiché, `""` sinon}. |
| `PcbDoc.origine()` | `(ORIGINX, ORIGINY)` de `Board6` en mil ; `(0, 0)` si absents. Remplace `pcbdoc_serigraphie.origine`. |
| `PcbDoc.composants()` | Par pièce : `index`, `designateur` (règle de `designateurs()`), `designateur_source` (`SOURCEDESIGNATOR` ou `""`), `empreinte`, `x_mil`, `y_mil`, `rotation`, `face` (`"TOP"`/`"BOTTOM"`), `verrouille` (`LOCKED`, `False` si absent), `unique_id`, `lien_schema`, `bibliotheque` (`SOURCEFOOTPRINTLIBRARY`, chemin possible : ne jamais le recopier dans un JSON, §0.3), `hauteur_mil`, `genre_altium` (`COMPONENTKIND`, 0 si absent), `genre` (`standard`, `mecanique`, `graphique`, `liaison_reseaux`, `cavalier` d'après `pcbdoc.GENRES_COMPOSANT`, annexe E-6 provisoire ; `None` hors table), `description`, `reference`, `commentaire`, `designateur_visible`, `commentaire_visible`. |
| `PcbDoc.index_composant(designateur)` | Rang de la pièce (`KeyError` si inconnue). |
| `PcbDoc.composant(designateur)` | `Proprietes` de la pièce dans `Components6`. |
| `PcbDoc.primitives_du_composant(idx)` | Itère `(flux, primitive)` de toutes les primitives de la pièce de rang `idx`. |
| `PcbDoc.pistes()` | `index`, `couche`, `x1`, `y1`, `x2`, `y2`, `largeur`, `reseau`, `composant`, `polygone`, `verrouille`, `keepout`, `restrictions_keepout`. |
| `PcbDoc.arcs()` | `index`, `couche`, `cx`, `cy`, `rayon`, `angle_debut`, `angle_fin`, `largeur`, `reseau`, `composant`, `polygone`, `verrouille`, `keepout`, `restrictions_keepout`. |
| `PcbDoc.remplissages()` | `index`, `couche`, `x1`, `y1`, `x2`, `y2`, `rotation`, `reseau`, `composant`, `polygone`, `verrouille`, `keepout`, `restrictions_keepout`. |
| `PcbDoc.vias()` | `index`, `x`, `y`, `diametre`, `trou`, `de`, `a` (couches V6), `reseau`, `tenting_haut`, `tenting_bas`, `verrouille`, `composant`, `polygone`, `keepout`, `mode_pile`. |
| `PcbDoc.pastilles()` | Clés historiques : `index`, `nom`, `composant`, `x`, `y`, `couche`, `taille_haut`, `taille_milieu`, `taille_bas` ((x, y)), `forme_haut`/`_milieu`/`_bas` (`ronde`, `rectangle`, `octogone`), `trou` (diamètre en mil), `metallise`, `forme_trou`, `rotation`, `reseau`, `mode_pile`, `masque_mode`, `pate_mode`. Ajouts : `rang` (rang dans la pièce, ordre du fichier : identifiant `"<désignateur>#<rang>"` de §0.4), `traversante`, `couches` (cuivre de la pastille, ordre physique), `formes` ({couche V6 : `forme` (`rond`, `oblong`, `rect`, `rect_arrondi`, `octogone`), `taille`, `pourcentage`, `rayon_coin` (= pourcentage/100 × min/2, annexe E-1), `decalage`, `code_forme`, `code_forme_alt`}), `percage` (`None` pour une CMS ; sinon `diametre`, `forme` (`rond`, `carre`, `oblong`), `longueur`, `rotation` (telle qu'écrite, relative à la pastille, à confirmer par E9), `decalage`, `metallise`), `masque` (`mode`, `expansion_haut`, `expansion_bas`, `bas_separe`), `pate` (`mode`, `expansion`), `verrouille`. |
| `PcbDoc.textes()` | `index`, `texte`, `couche`, `x`, `y`, `hauteur`, `rotation`, `miroir`, `composant`, `designateur`, `commentaire`, `police`, `nom_police`. |
| `PcbDoc.regions()` | `Regions6` : `index`, `couche`, `reseau`, `composant`, `polygone`, `genre` (`KIND`), `decoupe_carte`, `keepout`, `restrictions_keepout`, `effets_keepout`, `verrouille`, `nom`, `sous_poly`, `contour` ([(x, y)]), `trous` ([[(x, y)]]), `proprietes`. |
| `PcbDoc.corps_3d()` | `index`, `composant`, `couche`, `hauteur_totale_mil`, `hauteur_appui_mil`, `projection`, `modele` (nom seul), `identifiant_modele`, `contour`. |
| `PcbDoc.empilage()` | Couches de `V9_STACK_LAYER*` (ordre du fichier) : `rang`, `nom`, `layerid`, `couche`, `cuivre_mil`, `type_dielectrique`, `er`, `epaisseur_dielectrique_mil`, `materiau`, `tangente_pertes`. |
| `PcbDoc.couches_cuivre()` | Couches de cuivre dans l'**ordre physique** (V9, à défaut V8, à défaut chaîne V6 `LAYER<n>NEXT`) : `rang` (0 = dessus), `nom` (contrat), `nom_altium` (V6), `nom_fichier`, `type` (`signal`, `plan_negatif`), `reseau` (plan), `retrait_mil` (`PULLBACKDISTANCE` d'un plan, `None` si absent ; 0 pour un signal), `epaisseur_cuivre_mil`, `id_v7`, `v6`, `source`, `index_empilage`. |
| `PcbDoc.plans_negatifs()` | Par plan : `couche`, `nom_altium`, `rang`, `reseau`, `retrait_mil`, `regions` (polygones « Split Plane » : `index`, `nom`, `reseau`, `contour`, `reseau_du_plan`), `lignes_partage` (pistes et arcs libres du plan), `traits_retrait`. |
| `PcbDoc.sous_empilages()` | `rang`, `id`, `nom`, `flex` (`ISFLEX`), `type`, `utilise`, `service`. |
| `PcbDoc.empilage_maitre()` | `id`, `nom`, `flex`, `style`. |
| `PcbDoc.regions_carte()` | `BoardRegions` : `index`, `nom`, `id_empilage` (`LAYERSTACKID`), `sous_empilage`, `lignes_pliage` (`BENDINGLINECOUNT`), `lignes`, `contour`, `trous`, `aire_mm2`, `verrouille_3d`, `proprietes`. |
| `PcbDoc.aire_region_carte()` | Aire (mm²) des régions de carte (somme s'il y en a plusieurs) ; `None` s'il n'y en a aucune. |
| `PcbDoc.cartes_imbriquees()` | `EmbeddedBoards6` (panneau) : `index`, `document` (nom seul), `lignes`, `colonnes`, `x_mil`, `y_mil`, `rotation`, `miroir`, `espacement_lignes_mil`, `espacement_colonnes_mil`, `proprietes` (`DOCUMENTPATH` réduit au nom seul). |
| `PcbDoc.contour_carte()` | Sommets du contour de `Board6` : `type` (0 segment, 1 arc), `x`, `y`, `cx`, `cy`, `r`, `sa`, `ea`. |
| `PcbDoc.regles()` | `Rules6` brutes : `nom`, `type` (`RULEKIND`), `code`, `active`, `priorite`, `portee1`, `portee2` (texte exact), `prefixe_binaire`, `portee_reseaux`, `portee_couches`, `commentaire`, `identifiant`, `valeurs` (propres au type, texte décodé), `contour` (`RoomDefinition`), `proprietes`, `binaire`. |
| `PcbDoc.classes()` | `nom`, `type`, `superclasse`, `membres`. |
| `PcbDoc.polygones()` | `index`, `nom`, `couche`, `reseau`, `type`, `hachures`, `coulee_dessus`, `contour`, `ordre`. |
| `PcbDoc.paires_differentielles()` | `nom`, `positif`, `negatif`. |
| `PcbDoc.types_couches_mecaniques()` | {couche mécanique : type (`Courtyard`, `AssemblyTop`… ou `code_<n>`)}. |

Module `pcbdoc` : classes `pcbdoc.PcbDoc` (sections 2 à 4) et `pcbdoc.ErreurPcbDoc` ; `pcbdoc.effets_keepout(bits)` (bits de restriction -> effets, table provisoire
`pcbdoc.BITS_KEEPOUT`, annexe E-4) ; `pcbdoc.nom_seul(chemin)` (= `chemins.nom_seul`) ; constantes
`pcbdoc.ACCESSEURS`, `pcbdoc.FLUX_PRIMITIVES_GEO`, `pcbdoc.GENRES_COMPOSANT`.

## 4. `PcbDoc` : modifications

| Fonction | Rôle |
|---|---|
| `PcbDoc.ajouter_piste(couche, debut_mil, fin_mil, largeur_mil, reseau=None)` | Piste libre (`Tracks6`) de `debut_mil` à `fin_mil` ((x, y) en mil), largeur `largeur_mil` > 0, réseau par son nom (`None` : aucun). Rend son rang dans `Tracks6`. |
| `PcbDoc.ajouter_via(pos_mil, diametre_mil, trou_mil, reseau=None)` | Via libre traversante (`TOP` à `BOTTOM`, même diamètre sur toutes les couches). Si `DrillManager` existe dans le fichier, la via est rattachée au groupe de via de même `TOOLSIZE`, créé s'il n'existe pas (`pose.nouveau_groupe_via`). Rend son rang dans `Vias6`. |
| `PcbDoc.ajouter_region(couche, contour_mil, reseau, trous=())` | Région de cuivre libre (contour : au moins 3 sommets (x, y) en mil, non refermé ; trous : liste de contours), enregistrée dans `Regions6` et, au même rang, dans `ShapeBasedRegions6` (omis seulement pour un fichier qui a des régions sans aucun `ShapeBasedRegions6`). Rend le rang dans `Regions6`. |
| `PcbDoc.deplacer_composant(designateur, dx_mil=0.0, dy_mil=0.0)` | Translation d'une pièce et de toutes ses primitives ; rend le nombre de primitives déplacées. |
| `PcbDoc.deplacer_texte(index, dx_mil=0.0, dy_mil=0.0)` | Translation d'un texte (`Texts6`). |
| `PcbDoc.effacer_chemins(tout=False)` | §0.3. `tout=False` : `FILENAME` de `Board6` vidé (cartes produites par `generer_pcbdoc`). `tout=True` (fichiers publiés) : de plus, tout chemin absolu ramené au nom seul dans toutes les propriétés (`SOURCEFOOTPRINTLIBRARY`, `SOURCECOMPONENTLIBRARY`, `DOCUMENTPATH`, `MODEL.NAME`…, valeurs `UNICODE__` et `%UTF8%`, données base64 + zlib : `V9_STACKCUSTOMDATA`, `ConstraintManager`), chaînes larges, chaînes des primitives, groupes de `DrillManager`, modèles STEP compressés (recompressés seulement s'ils changent) et flux bruts inconnus (latin-1 et UTF-16, à longueur égale : nom seul suivi d'espaces). Rend le bilan `[{"flux", "champ", "nombre"}]`, sans aucun chemin. |
| `PcbDoc.chemins_absolus()` | Même parcours, sans rien modifier : champs qui contiennent encore un chemin absolu (`[]` si aucun). Les flux binaires (polices, GUID, cache des lettres) ne sont pas lus ; les flux bruts inconnus le sont avec `chemins.MOTIF_STRICT`. |

**Couches des ajouts** : nom Altium (`TOP`, `MID2`, `PLANE1`, `TOPOVERLAY`…), nom du contrat (`TopLayer`,
`MidLayer2`, `InternalPlane1`, `BottomLayer`) ou identifiant V6 ; une couche de cuivre absente de l'empilage de la
carte est refusée (`ErreurPcbDoc`), de même qu'un réseau inconnu, une coordonnée hors du domaine i32 (±214 748 mil),
une largeur ou un diamètre nul, un trou au moins égal au diamètre, une région de moins de 3 sommets.

**Prototypes** : chaque sous-enregistrement neuf est construit par `gabarits.prototype` depuis les tables
documentées `gabarits.VALEURS_PISTE`, `gabarits.VALEURS_VIA`, `gabarits.VALEURS_ENTETE_REGION` (tout autre champ
à 0), les propriétés `gabarits.PROPRIETES_REGION` et, pour un groupe de perçage neuf,
`gabarits.PROPRIETES_GROUPE_VIA` ; jamais copié d'une primitive de la carte : les ajouts fonctionnent sur une carte
**sans aucune piste ni via**. Longueur : celle du premier enregistrement existant du même flux, sinon
`gabarits.LONGUEURS_PROTOTYPES` (49 octets pour une piste, 321 pour une via). Valeurs : non verrouillé, routé par
l'utilisateur (`drapeaux1 = 0x0C`), sans keep-out ; via : relief thermique 10 mil, 4 branches, dégagement de plan
20 mil, masque selon la règle (4 mil), aucune bibliothèque pad/via (GUID nul), `guid_gabarit` neuf déterministe.

**Index croisés** : `PrimitiveGuids` (s'il existe dans le fichier) reçoit une entrée par primitive ajoutée, au code
du flux, en coût constant (`pose.ajouter_primitive`) ; un stockage absent (`Tracks6`, `Vias6`, `Regions6`,
`ShapeBasedRegions6`…) est créé à l'écriture. `PrimitiveGuids` et `DrillManager` absents (exemples d'Altium) ne
sont pas créés.

## 5. `pose` : pose des pièces, index croisés, repère local

| Fonction | Rôle |
|---|---|
| `pose.monde_vers_local(point, origine, rotation, dessous=False)` | Convention unique de §0.4 : local = M(face) · R(−rotation) · (monde − origine), M(dessous) = diag(1, −1). Unités de `point` et `origine` (mil ou mm). |
| `pose.local_vers_monde(point, origine, rotation, dessous=False)` | Inverse : monde = origine + R(rotation) · M(face) · local (aller-retour exact à 1e-9 près ; valeurs exactes aux multiples de 90°). |
| `pose.rotation_relative(rotation_pastille, rotation_piece, dessous=False)` | §0.4 : pastille − pièce (dessus), pièce − pastille (dessous), dans [0, 360). |
| `pose.rotation_absolue(rotation_rel, rotation_piece, dessous=False)` | Inverse de `pose.rotation_relative`. |
| `pose.Transformation` | Classe : p -> o1 + L (p − o0) entre deux poses (o, rotation, dessous), en unités internes. |
| `pose.poser_composant(doc, idx, x_mil, y_mil, rotation, dessous)` | Pose la pièce de rang `idx` et transforme toutes ses primitives ; rend `(nombre de primitives, anomalies)`. |
| `pose.transformer_primitive(doc, storage, pr, t, paires_mec)` | Applique une `pose.Transformation` à une primitive de pièce ; rend ses anomalies. |
| `pose.paires_mecaniques(doc)` | {couche mécanique : couche associée} (`MECHPAIR*`). |
| `pose.supprimer_primitives(doc, a_supprimer)` | `{flux : ensemble d'index}` : supprime (et leurs jumelles de `ShapeBased*`), renumérote `PrimitiveGuids` et `DrillManager`, vide les violations DRC ; refuse les pastilles (`pose.ErreurPose`). Rend {flux : nombre}. |
| `pose.vider_violations(doc)` | Vide les violations DRC enregistrées (Altium les recalcule). |
| `pose.ajouter_primitive(doc, storage, pr)` | Ajoute une primitive déjà construite en fin de flux et, si `PrimitiveGuids` existe, son entrée (GUID `PcbDoc.nouveau_guid`), en coût constant. Rend son rang. |
| `pose.code_guid(doc, storage)` | Code `PrimitiveGuids` d'un flux (`(0x0D + rang) << 8 \| type`). |
| `pose.verifier_guids(doc)` | Écarts entre `PrimitiveGuids` et les flux de primitives ; `[]` si cohérent **ou si le fichier n'a pas de `PrimitiveGuids`**. |
| `pose.rattacher_vias_percage(doc)` | Reconstruit les listes de vias de `DrillManager` (s'il existe) d'après le trou de chaque via ; groupe manquant copié du groupe de via le plus proche, ou construit depuis le gabarit si la carte n'en a aucun ; groupes de via vides retirés. |
| `pose.groupe_via(dm, trou_mil)` | Groupe de via de `DrillManager` de `TOOLSIZE` égal (à 0,001 mil), sinon `None`. |
| `pose.nouveau_groupe_via(dm, trou_mil, diametre_mil=None, modele=None)` | Ajoute un groupe de via (copie de `modele`, sinon gabarit ; `HOLEMARK` = max + 1 ; `TEMPLATENAME` « v<diamètre>h<trou> » en centièmes de mm) et le rend `(propriétés, [pastilles, vias])`. |
| `pose.ErreurPose` | Exception : pose ou suppression non sûre (le fichier ne doit pas être écrit). |

## 6. `chemins` : chemins absolus

| Fonction | Rôle |
|---|---|
| `chemins.nom_seul(chemin)` | Dernier élément d'un chemin (« \\ » ou « / »). |
| `chemins.contient(texte)` | Le texte contient un chemin absolu (`chemins.MOTIF_CHEMIN` : lettre de lecteur, chemin réseau, dossier de profil « Users » ou « home » suivi d'un nom). |
| `chemins.reduire(texte)` | `(texte, n)` : chaque chemin absolu ramené à son nom seul. |
| `chemins.reduire_valeur(valeur)` | Idem pour une valeur de propriété, y compris base64 d'un flux zlib. |
| `chemins.reduire_codes(valeur)` | Idem pour une valeur `UNICODE__` (codes décimaux). |
| `chemins.trouver_octets(b, strict=True)` | `[(codage, décalage)]` des chemins dans des octets bruts (latin-1 et UTF-16LE, deux alignements) ; `strict` : `chemins.MOTIF_STRICT` (au moins deux éléments après le lecteur : pas de faux positif sur des octets compressés). Sert aussi à contrôler un fichier écrit. |
| `chemins.reduire_octets(b)` | Octets bruts : chaque chemin (motif strict) remplacé à longueur égale par son nom seul suivi d'espaces. |

## 7. Modules de bas niveau

`gabarits` (dispositions octet par octet des sous-enregistrements binaires : `gabarits.PISTE`, `gabarits.ARC`,
`gabarits.VIA`, `gabarits.PASTILLE`, `gabarits.PASTILLE_PILE`, `gabarits.TEXTE`, `gabarits.ENTETE_REGION`… ; statut
E/C/S/I de chaque champ) :

| Fonction | Rôle |
|---|---|
| `gabarits.Bloc` | Classe : sous-enregistrement décodé ; `b["champ"]`, `b.get`, `b.v` (dict ordonné). |
| `gabarits.decoder_bloc(gabarit, b, nom="")` | Octets -> `Bloc` (zones hors gabarit gardées brutes sous `inconnu_<décalage>`). |
| `gabarits.encoder_bloc(bloc)` | `Bloc` -> octets (aller-retour exact). |
| `gabarits.couverture_bloc(bloc)` | {statut : octets}. |
| `gabarits.taille_type(t)` | Taille en octets d'un type de champ. |
| `gabarits.longueur_gabarit(gabarit)` | Fin du dernier champ du gabarit. |
| `gabarits.nouveau_bloc(gabarit, longueur=None, nom="", **valeurs)` | Bloc d'octets nuls, champs nommés fixés (`KeyError` si absent). |
| `gabarits.prototype(gabarit, valeurs, longueur, nom="", **champs)` | Bloc d'octets nuls, puis table `valeurs` et `champs` (champ absent à cette longueur : ignoré). |
| `gabarits.tableau_coords(b)` | Octets -> liste d'i32. |
| `gabarits.coords_vers_octets(lst)` | Liste d'i32 -> octets. |

`couches` (identifiants V6 sur un octet, V7 sur 32 bits) : `couches.nom_v7(v)`, `couches.v7_depuis_v6(v6)`,
`couches.v6_depuis_v7(v7)`, `couches.nom_couche(v6, v7=None)` (nom V6, ou V7 si le V6 est inconnu),
`couches.est_cuivre_v7(v7)`, `couches.est_plan_v7(v7)`, `couches.nom_contrat_v6(v6)` et `couches.nom_contrat_v7(v7)`
(noms du contrat, `None` hors cuivre), `couches.v6_depuis_nom(nom)` (nom Altium ou du contrat -> V6, `None` si
inconnu) ; tables `couches.NOMS_V6`, `couches.IDS_V6`.

`cfb` (conteneur OLE2) : `cfb.lire_cfb(chemin_fichier)` -> `cfb.Noeud` (arbre : `enfants`, `donnees`,
`flux()`, `lire`, `ecrire`) ; `cfb.ecrire_cfb(racine, chemin_fichier=None)` -> octets (écrits de façon atomique si
`chemin_fichier`) ; `cfb.ecrire_atomique(chemin, donnees)` ; `cfb.verifier_arbre(racine)` (écarts de l'arbre
rouge-noir).

`flux` (codecs des flux) : `flux.codec(storage)` ((classe, options) du flux `Data`, `None` si inconnu),
`flux.decoder_data(storage, octets, cles=None)`, `flux.flux_vide(storage, cles=None)` (objet vide du type plein,
réencodé en 0 octet), `flux.decoder_flux(chemin, octets, cles=None)` ; table `flux.CODECS` ; classes
`flux.FluxProps`, `flux.FluxPrimitives`, `flux.Primitive`, `flux.SousBloc`, `flux.SousRegion`,
`flux.ChainePascal`, `flux.FluxGuids`, `flux.FluxGestionnairePercage`, `flux.FluxChainesLarges`,
`flux.FluxModele3D`, `flux.FluxChaineUtf16`, `flux.FluxBrut`.

`proprietes` (listes `|CLE=VALEUR|…`) : `proprietes.Proprietes` (`get`, `texte` (valeur affichable : `UNICODE__`,
`%UTF8%` ou Windows-1252), `dict`, `cles`, `p["CLE"] = valeur`, `supprimer`, `vers_octets`) ;
`proprietes.valeur_mil(txt, defaut=None)` (« 12.5mil », « 0.3mm »… -> mil), `proprietes.valeur_mm(txt, defaut=None)`,
`proprietes.formater_mil(v_mil)` (« 12.5mil »), `proprietes.booleen(txt, defaut=None)`,
`proprietes.interne_vers_mil(v)`, `proprietes.mil_vers_interne(v)`, `proprietes.mm_vers_interne(v)`,
`proprietes.interne_vers_mm(v)`, `proprietes.codes_vers_texte(v)`, `proprietes.texte_vers_codes(t)`.

## 8. Exemples

Importeur (lot B) : lire une carte quelconque, sans exception.

```python
try:
    doc = PcbDoc.ouvrir(chemin)
except ErreurPcbDoc as e:          # message sans chemin
    return erreur_entree(str(e))   # code 2
if doc.cartes_imbriquees():        # panneau : refus de l'importeur (décision du lot B)
    ...
noms = doc.designateurs()
pieces = doc.composants()          # une fois : chaque appel relit Components6
for p in doc.pastilles():
    c = pieces[p["composant"]] if p["composant"] != 0xFFFF else None
    if c is not None:
        local = pose.monde_vers_local((p["x"], p["y"]), (c["x_mil"], c["y_mil"]), c["rotation"], c["face"] == "BOTTOM")
        ident = f'{noms[p["composant"]]}#{p["rang"]}'
couches = [(c["rang"], c["nom"], c["type"]) for c in doc.couches_cuivre()]
```

Générateur (lot C) : carte produite depuis un gabarit, même sans via ni piste libre.

```python
doc = PcbDoc.ouvrir(gabarit)
pose.supprimer_primitives(doc, {"Tracks6": pistes_libres, "Vias6": vias_libres})
for p in placements:
    pose.poser_composant(doc, doc.index_composant(p["designator"]), x, y, rot, dessous)
for t in pistes:
    doc.ajouter_piste(t["layer"], (x1, y1), (x2, y2), t["width"], reseau=t.get("net_name"))
for v in vias:
    doc.ajouter_via((v["x"], v["y"]), v["size"], v["hole_size"], reseau=v.get("net"))
doc.ajouter_region("TopLayer", contour, "GND", trous=[trou])
assert pose.verifier_guids(doc) == []
doc.effacer_chemins(tout=False)    # FILENAME effacé (§0.3)
doc.enregistrer(sortie)            # atomique ; refus si sortie == gabarit
```
