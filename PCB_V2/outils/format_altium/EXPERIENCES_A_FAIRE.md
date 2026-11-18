# Expériences à faire dans Altium (annexe E du contrat)

Les conventions de l'annexe E sont **provisoires** : `format_altium` et les lots qui l'utilisent les appliquent, mais
seule une séance dans Altium peut les confirmer. Ce document permet à une personne de faire cette séance **seule,
plus tard**, sur des fichiers prêts à ouvrir, puis d'obtenir un verdict par convention. Une convention contredite
donne lieu à un avenant du contrat (annexe E : « une conclusion contraire donne lieu à un avenant »).

## 1. Déroulement

1. **Fabriquer les fichiers d'essai** (sans Altium), depuis `PCB_V2/outils/format_altium` :

       python -B fabriquer_essais.py --base <dossier de la carte source>/demo.PcbDoc --sortie essais/annexe_e

   Toute carte `.PcbDoc` peut servir de base (elle n'est que lue) : les objets modifiés sont choisis par leurs
   propriétés (face, forme, perçage, nombre de pastilles, place libre...), jamais par leur nom. La carte source du
   corpus (`demo.PcbDoc`) a tout ce qu'il faut : les dix fichiers sont fabriqués. Sur une autre base, une expérience
   dont l'objet manque est **sautée** avec son motif (code 0). Le dossier `essais/` est ignoré par git : **aucun
   fichier d'essai n'est commité ni publié**.

   Le dossier reçoit un fichier par expérience (tableau du §2), `attendus.json` (pour chaque expérience :
   convention, fichier, objet modifié avec ses coordonnées **telles qu'Altium les affiche**, c'est-à-dire relatives
   à l'origine de la carte, en mm et en mil, valeurs attendues et tolérances) et `resultats_a_remplir.json` (les
   valeurs à relever, toutes à `null`). L'outil relit chaque fichier (olefile et `format_altium`) : modification
   présente et exacte, aucun autre flux changé ; deux exécutions donnent les mêmes fichiers, octet pour octet.

2. **Séance dans Altium** (une à deux heures). Pour chaque fichier : `File » Open`, faire la manipulation du §3,
   noter les valeurs. Règles :
   - travailler sur les fichiers d'`essais/annexe_e` seulement ; **ne jamais ouvrir en écriture** une carte de
     référence (`PCB_METROID_V2.PcbDoc`, `_rev2`...) ; ne pas enregistrer un fichier d'essai sous son nom (pour T1,
     « Enregistrer sous » un nouveau nom) ;
   - ne désactiver **aucune** règle de DRC (le DRC se lance tel que le fichier le définit) ;
   - unités : touche `Q` (mm / mil) ; aller à une position : `J`, `L` (Jump » Location), coordonnées de
     `attendus.json` (champ `position`, `x_mm`/`y_mm`) ; propriétés d'un objet : clic sur l'objet, panneau
     `Properties` (`View » Panels » Properties`) ;
   - noter tout message affiché à l'ouverture (dans `remarques`).

3. **Remplir les résultats** : copier `resultats_a_remplir.json` en `resultats.json`, puis pour chaque expérience
   faite : `"faite": true`, chaque valeur (nombre en mm ou en degrés avec un **point** décimal, `true`/`false`, ou
   texte tel qu'affiché), `remarques` libres, et en tête `version_altium` et `date`. Une valeur non relevée reste
   `null` (l'expérience sera « non faite »). Aucun nom de personne dans ce fichier.

4. **Verdicts** :

       python -B fabriquer_essais.py --verifier essais/annexe_e/resultats.json --attendus essais/annexe_e/attendus.json --json essais/annexe_e/verdicts.json

   Pour chaque convention : **confirmée** (chaque valeur dans la tolérance) ; **contredite** (une valeur en sort :
   avenant nécessaire ; les écarts sont listés) ; **non faite** (expérience sautée, non faite, ou valeur manquante).
   Code 0 sans contradiction, 1 si au moins une convention est contredite, 2 si les fichiers sont invalides (JSON,
   structure modifiée — `experiences` doit rester un objet `{identifiant: {faite, valeurs, remarques}}` —, type
   d'une valeur, résultats faits sur une autre base : `base_sha256`), avec la clé fautive dans le message. Les
   nombres se comparent à la tolérance, les angles modulo 360° (modulo 180° pour l'axe d'un trou oblong et la
   rotation de la pastille de E3), les textes sans casse, accents ni espaces superflus. Les parties d'une
   convention que la base ne permet pas de tester (`non_teste` de `attendus.json`) sont rappelées sous le verdict.

5. **Conclure** : rapporter les verdicts (fichier `verdicts.json`, sans chemin) au propriétaire de `format_altium` ;
   toute convention contredite ouvre un avenant (contrat, puis bibliothèque et importeurs).

## 2. Les expériences

| # | Convention provisoire (annexe E) | Fichier | Modification faite par `fabriquer_essais.py` |
|---|---|---|---|
| E1 | rectangle arrondi : rayon = pourcentage/100 × min(largeur, hauteur)/2 | `E1_rect_arrondi.PcbDoc` | première pastille CMS de la face TOP qui a une pile : 1 × 2 mm, forme 1 + formes alternatives 9, 50 % |
| E2 | octogone : chanfrein de 25 % du petit côté | `E2_octogone.PcbDoc` | pastille CMS de la face TOP (sans pile de préférence) : octogone 1 × 1,6 mm |
| E3 | pièce dessous : rotation relative = pièce − pastille ; tailles haut et bas non permutées | `E3_piece_dessous_30.PcbDoc` | pièce TOP retournable sans anomalie, ayant une pastille `p0` hors de ses deux axes (de préférence avec une pastille traversante à tailles haut et bas différentes, puis asymétrique) ; si la rotation relative de `p0` ne distingue pas les deux signes, elle reçoit 60° ; pièce posée dessous, 30°, même place |
| E4 | bits des keep-out : 0x01 vias, 0x02 pistes, 0x04 remplissages (cuivre), 0x08 pastilles CMS, 0x10 traversantes | `E4_keepout_bits.PcbDoc` | 5 zones keep-out (couche Keep-Out) de 60 mil, une par bit, dans une zone libre |
| E5 | le DRC applique l'isolement entre cuivre et keep-out (§4.4) | `E5_isolement_keepout.PcbDoc` | keep-out 200 × 100 mil (tous les bits), piste à 0,1 mm au-dessus (à la moitié de l'isolement si celui-ci ne dépasse pas 0,1 mm), piste témoin à isolement + 0,1 mm en dessous |
| E6 | `COMPONENTKIND` : 0 et 5 standard, 1 mécanique, 2 graphique, 3 et 4 liaison de réseaux, 6 cavalier | `E6_genres_composants.PcbDoc` | pièces de rang 0 à 6 de `Components6` : `COMPONENTKIND` = rang |
| E7 | plan négatif : via relié si son centre est dans la zone à au moins rayon + `PlaneClearance` du bord | `E7_plan_negatif.PcbDoc` | 4 vias de part et d'autre de la plus longue ligne de partage droite : bord de la ligne + rayon + `PlaneClearance` ± 2 mil |
| E8 | sens des arcs du contour (un arc va de SA à EA en sens trigonométrique ; le sommet est son point de départ) | `E8_contour_arcs.PcbDoc` | contour : rectangle de la boîte du contour, 4 coins arrondis (parcourus en sens trigonométrique) et encoche en demi-cercle au milieu du bord bas (parcourue en sens horaire) ; région de carte refaite en segments |
| E9 | rotation d'un trou oblong relative à la pastille (§2.2) : axe = rotation de la pastille + rotation du trou | `E9_trou_oblong.PcbDoc` | pastille traversante à trou oblong (créé s'il n'y en a pas) tournée de 30° de plus |
| T1 | ajouts sans modèle : piste, via et région depuis `gabarits.py`, sur une carte sans via ni piste libre | `T1_ajouts_sans_modele.PcbDoc` | vias libres et pistes libres (hors lignes de partage des plans) supprimées, puis une piste, une via et une région ajoutées |

E8 est aussi contrôlée automatiquement (non-régression, `tests/test_essais.py`, cartes locales) sur WiFi et SL1 :
chaque sommet d'arc est au point SA, le sommet suivant au point EA (le contour de METROID n'a aucun arc) ; la
séance vérifie en plus le parcours horaire (encoche), absent de ces cartes.

## 3. Manipulation, valeur à relever, valeur attendue, règle de conclusion

Les clés entre crochets sont celles de `resultats_a_remplir.json` ; les valeurs attendues exactes (et leurs
tolérances) sont dans `attendus.json` (champ `mesures`) : celles citées ici sont celles de la carte source.

### E1 — rectangle arrondi
1. Ouvrir `E1_rect_arrondi.PcbDoc` ; aller à la pastille de `objet.position` (pièce `objet.piece`, pastille
   `objet.nom`) ; panneau Properties : forme « Rounded Rectangle », taille 1 × 2 mm.
2. Relever le pourcentage affiché (« Corner Radius (%) ») [`pourcentage_affiche`] : attendu 50 (± 0,5).
3. Rayon : `File » Fabrication Outputs » Gerber Files`, unités mm, couche Top Layer seulement ; ouvrir le fichier
   `.GTL` dans un éditeur de texte ; repérer l'ouverture (`%ADD..*%` / `%AM...*%`) de la pastille : le rayon est la
   moitié du diamètre des cercles de coin (primitive `1,1,<diamètre>,...`) [`rayon_coin_mm`] : attendu **0,25 mm**
   (± 0,01). À défaut, mesurer dans Altium (`Reports » Measure Distance`) la distance entre un coin arrondi et le
   prolongement des deux côtés.
4. Conclusion : 0,25 mm confirme « rayon = % × min/2 » ; 0,5 mm signifierait « % × min » (contredite).

### E2 — octogone
1. Ouvrir `E2_octogone.PcbDoc` ; pastille de `objet.position` ; Properties : forme « Octagonal », relever la
   taille X [`largeur_mm`] (attendu 1,0 ± 0,01) et Y [`hauteur_mm`] (attendu 1,6 ± 0,01), repère de la pastille.
2. Exporter le Gerber de la couche Top (comme E1) ; dans le `.GTL`, l'ouverture de l'octogone donne ses 8 sommets
   (macro à contour, primitive `4,1,8,...`) : `attendus.json` donne les sommets attendus (`sommets_attendus_mm`,
   repère de la pastille). Relever la longueur coupée sur un côté = écart entre un sommet et le coin du rectangle
   1 × 1,6 mm (par exemple 0,5 − |x| du sommet situé sur le bord haut) [`chanfrein_mm`] : attendu **0,25 mm**
   (± 0,01), soit 25 % du petit côté.
3. Conclusion : 0,25 confirme ; 0,4 (25 % du grand côté) ou 0,29 (octogone régulier) contredit.

### E3 — pièce dessous à 30°
1. Ouvrir `E3_piece_dessous_30.PcbDoc` ; aller à la pièce `objet.piece` (`objet.avant.position`) ; Properties :
   relever la couche [`piece_couche`] (attendu « Bottom Layer ») et la rotation [`piece_rotation_deg`] (attendu 30 ±
   0,1).
2. Pour les deux pastilles nommées dans les descriptions des mesures (`p0`, `p1`) : clic sur la pastille (vérifier
   son nom, `objet.p0.nom`), relever X et Y affichés [`p0_x_mm`, `p0_y_mm`, `p1_x_mm`, `p1_y_mm`] (attendus dans
   `attendus.json`, ± 0,01 mm), la rotation de `p0` [`p0_rotation_deg`] (comparée modulo 180°, ± 0,1°) et sa
   couche [`p0_couche`] (« Bottom Layer » pour une CMS).
3. Si `attendus.json` le demande (`tailles_haut_bas` = « relevées ») : tailles X de la couche haute et basse d'une
   pastille traversante [`th_taille_haut_x_mm`, `th_taille_bas_x_mm`].
4. Conclusion : positions et rotation conformes confirment la convention de repère (§0.4). `objet.p0` donne les
   valeurs des autres hypothèses, qui la contredisent : `hypotheses_rotation_mod180` (« contraire (pièce + r) » :
   signe opposé de la rotation relative ; `p0` est hors des deux axes et sa rotation relative r, imposée à 60° si
   besoin — `rotation_relative_imposee` —, rend les deux signes différents) et `hypotheses_position` (miroir de
   l'autre axe, ou pièce non retournée). Sur la carte source : `p0_rotation_deg` 150 (convention) contre 90
   (contraire). Ce qui n'a pas pu être testé sur la base est listé dans `non_teste` (par exemple « tailles haut et
   bas non permutées » quand aucune pièce retournable n'a de pastille traversante à tailles différentes) et
   rappelé par `--verifier` : cette partie de la convention n'est ni confirmée ni contredite.

### E4 — bits des keep-out
1. Ouvrir `E4_keepout_bits.PcbDoc` ; les 5 zones carrées de la couche Keep-Out sont alignées (centres dans
   `objet.zones`, de gauche à droite : zone 1 à 5).
2. Pour chaque zone : clic, Properties, rubrique des restrictions de keep-out (Via, Track, Copper, SMD Pad, TH
   Pad) ; relever le libellé de la **seule** restriction active [`zone1_restriction` ... `zone5_restriction`] ; s'il
   y en a plusieurs, les écrire séparées par des virgules (la convention est alors contredite).
3. Attendus : zone 1 « Via » (bit 0x01), zone 2 « Track » (0x02), zone 3 « Copper » (0x04), zone 4 « SMD Pad »
   (0x08), zone 5 « TH Pad » (0x10) ; les libellés français (Piste, Cuivre, Pastille CMS, Pastille traversante)
   sont acceptés. Si Altium propose une restriction absente de cette liste (« placement »...), le noter dans
   `remarques` (aucun bit ne lui est attribué aujourd'hui).

### E5 — isolement entre cuivre et keep-out
1. Ouvrir `E5_isolement_keepout.PcbDoc` ; repérer le keep-out (`objet.keepout.coin_bas_gauche`, 200 × 100 mil), la
   piste proche au-dessus et la piste témoin en dessous (`objet.piste_proche`, `objet.piste_temoin`).
2. `Reports » Measure Primitives` entre la piste proche et le keep-out : relever l'écart [`ecart_mm`] (attendu
   0,1 ± 0,005 ; la moitié de l'isolement de la règle si celui-ci ne dépasse pas 0,1 mm, valeur dans
   `attendus.json`).
3. `Tools » Design Rule Check... » Run Design Rule Check` (règles telles qu'enregistrées). Dans le panneau Messages :
   y a-t-il une violation d'isolement (« Clearance Constraint ») entre la piste proche et le keep-out
   [`violation_piste_proche`] (attendu `true`, la règle `objet.regle_isolement` vaut `objet.isolement_mil`) ? et
   entre la piste témoin et le keep-out [`violation_piste_temoin`] (attendu `false`) ?
4. Conclusion : `true` / `false` confirme que le DRC applique l'isolement au keep-out (zone élargie de §4.4) ;
   `false` / `false` (seul le recouvrement compte) la contredit.

### E6 — `COMPONENTKIND`
1. Ouvrir `E6_genres_composants.PcbDoc` ; pour chaque pièce de `objet.pieces` (désignateur, position) : clic,
   Properties, relever le type (« Component Type ») [`piece0_type` ... `piece6_type`].
2. Attendus : 0 « Standard », 1 « Mechanical », 2 « Graphical », 3 « Net Tie (In BOM) », 4 « Net Tie », 5
   « Standard (No BOM) », 6 « Jumper » (le verdict compare le début du libellé au genre de la convention : standard,
   mechanical, graphical, net tie, jumper ; libellés français acceptés).

### E7 — plan négatif et ligne de partage
1. Ouvrir `E7_plan_negatif.PcbDoc` ; afficher la couche de plan `objet.plan` seule (`Shift+S`, couche active).
2. Les 4 vias (`objet.vias` : côté A ou B de la ligne `objet.ligne_partage`, réseau du côté, distance de leur centre
   à l'axe de la ligne) sont à demi-largeur de la ligne + rayon de la via + `PlaneClearance` (`objet.plane_clearance_mil`)
   **plus 2 mil** (`plus2`) ou **moins 2 mil** (`moins2`).
3. Pour chaque via : la liaison au plan est-elle dessinée (relief thermique ou connexion directe, et non un simple
   anneau d'isolement) [`via_Aplus2_connectee`, `via_Amoins2_connectee`, `via_Bplus2_connectee`,
   `via_Bmoins2_connectee`] ? Attendus : `true` pour `plus2`, `false` pour `moins2`. Lancer aussi le DRC et noter
   dans `remarques` toute violation qui cite ces vias.
4. Conclusion : les quatre valeurs attendues confirment la règle de liaison (§4, annexe E-7) ; une via `moins2`
   reliée (ou `plus2` non reliée) la contredit.

### E8 — contour à arcs dans les deux sens
1. Ouvrir `E8_contour_arcs.PcbDoc` ; vue 2D puis 3D (touches `2`, `3`) ; un message sur la forme de la carte ou la
   région de l'empilage est à noter dans `remarques`.
2. Relever : les 4 coins sont-ils arrondis vers l'extérieur [`coins_convexes`] (attendu `true`) ? le milieu du bord
   bas est-il creusé d'un demi-cercle vers l'intérieur [`encoche_concave`] (attendu `true`) ? profondeur de
   l'encoche, du bord bas au point le plus profond (`objet.encoche.point_le_plus_profond`), mesurée par
   `Reports » Measure Distance` [`profondeur_encoche_mm`] (carte source : 3,81 mm ± 0,05).
3. Conclusion : `true` / `true` confirme la lecture du sens des arcs ; une encoche bombée vers l'extérieur ou un
   cercle complet la contredit.

### E9 — trou oblong dans une pastille tournée
1. Ouvrir `E9_trou_oblong.PcbDoc` ; pastille de `objet.position` ; Properties : relever la rotation de la pastille
   [`rotation_pastille_deg`] (attendu `objet.rotation`, ± 0,1).
2. Mesurer l'angle du grand axe du trou oblong dans le repère de la carte (`Reports » Measure Distance` entre les
   centres des deux demi-cercles du trou : angle = atan2(dY, dX)) [`angle_axe_trou_deg`] : attendu
   `objet.hypotheses["rotation_relative (convention)"]` (carte source : 120°, ± 2°, modulo 180°) ; la valeur
   `rotation_absolue (contraire)` (90°) contredirait la convention.
3. Si demandé : le trou reste-t-il entièrement dans le cuivre de la pastille [`trou_dans_cuivre`] (attendu `true`) ?

### T1 — ajouts sans modèle (ouverture)
1. Ouvrir `T1_ajouts_sans_modele.PcbDoc` : aucun message d'erreur ni d'avertissement [`ouverture_sans_message`]
   (attendu `true` ; tout message est recopié dans `remarques`).
2. La piste, la via et la région sont visibles aux positions de `objet` [`objets_visibles`] ; relever X et Y de la
   via [`via_x_mm`, `via_y_mm`] (± 0,01 mm).
3. « Enregistrer sous » un nouveau nom, fermer, rouvrir la copie : aucun message [`enregistrement_rouverture`].
   La copie enregistrée par Altium peut être comparée au fichier d'essai par
   `python -B differentiel.py T1_ajouts_sans_modele.PcbDoc <copie>` (champs qu'Altium recalcule).

## 4. Historique : résultats du 15/11/2026 (Altium 26, pont eda-agent)

Séance faite sur des copies de la carte rev4 (fichiers de l'ancien outil `aller_retour.py`, retirés depuis).

| n° | résultat |
|---|---|
| T1 | `rev4_aller_retour` s'ouvre sans message ; 7365 pistes, 1008 vias, 1036 pastilles, 359 composants, 740 textes, 4 polygones, 4 couches (= original) |
| T2-T5 | les 4 variantes s'ouvrent sans message, comptes attendus (+1 piste, +1 via) ; texte à Y = 6776,25 mil |
| T6 | `rev4_modifie` sans message ; M4_ALIM2 en X = 5533,07 avec ses pastilles ; piste TOP GND 10 mil (300,300)-(500,300) ; via GND 23,622/11,811 en (500,300) ; 3 lignes de chevelu (attendu : pistes externes restées à l'ancienne place de M4) |
| T7 | cache LettersGeometry vidé : ouverture sans message, Altium le régénère à l'enregistrement → cache confirmé |
| E00 | champs volatils identifiés (voir FORMAT.md §11) |
| E01 | via verrouillée : drapeaux1 0x0C → 0x08 (bit 0x04 = déverrouillé) |
| E02 | UserRouted=False : drapeaux1 0x0C → 0x04 (bit 0x08) ; IsPreRoute : rien dans le binaire |
| E03 | keepout : drapeaux2 = 2, réseau → 0xFFFF, restrictions 0x1F ; sans « via » : 0x1E (bit0 = via) |
| E04 | points de test : propriété non inscriptible par le pont → **non fait** |
| E05 | via : « depuis le bord du trou » = octet 258 (et non 67 comme le dit KiCad) ; manuel = octet 66 valeur 2 ; bas séparé = octet 241 ; expansions 54/242 |
| E07 | tenting via : aucun changement observé (le pont n'a rien écrit ?) → **à refaire à la main** |
| E08 | pâte désactivée : expansion pâte = -99999 mil et octet 185 3 → 1 ; Jumper ID = u16 à l'octet 110 (KiCad lit une rotation de trou f64 en 106 : faux ici) |
| E09 | tolérances via 291/295 et pastille 162/166 confirmées ; masque pastille manuel 102 = 2, haut 90, bas 121, bas séparé = octet 120 ; Board6 HASHKEY mis à jour |
| E13 | rotation texte : Altium recalcule x/y |
| E14 | code-barres : 160 = type de texte (2), 43 = TrueType booléen, 137/141 = taille totale, 145/149 = marges, 153 = largeur min calculée |
| E15 | texte inversé : octets 110 et 123 |
| E22 | Rules6 préfixe : RoomDefinition = 22, Length = 3 ; nouvelles règles insérées en tête ; room = contour VX/VY + CONFINEMENTSTYLE |

(La numérotation E00-E25 de cette séance est celle des expériences différentielles ci-dessous, distincte de E1-E9
de l'annexe E.)

## Annexe — expériences différentielles restantes (identification de champs binaires)

Hors annexe E : elles servent à documenter des champs encore « supposés » (`INCONNUS.md` §2). Méthode : sur une
**copie** d'une carte, ouvrir dans Altium, faire **un seul** changement, « Enregistrer sous » un nouveau nom, puis
`python -B differentiel.py <avant> <après>`. Altium réécrit aussi des champs sans rapport (DATE/TIME de Board6,
état de vue, caches) : faire d'abord E00 (ouvrir puis enregistrer sans rien changer) et comparer chaque expérience à
la sortie de E00.

| n° | champ(s) visé(s) | changement à faire dans Altium |
|---|---|---|
| E04 | via `inconnu_58` (octets 61..64 0/1), `inconnu_68` (70: 0/2 ; 72: 0/0x10) | via : cocher Testpoint Fabrication Top / Bottom / Assembly Top / Bottom (une case par essai) |
| E06 | via `inconnu_246` (0x2A), `inconnu_299` (0x1E, 0x09) | via : changer « IPC-4761 Via Type » / Via Features (bouché, rempli...) |
| E07 | via tenting `drapeaux1` 0x20/0x40 | tenting haut seul, puis bas seul (à la main) |
| E10 | `inconnu_115` (0 / 0xFF / 0x300), `inconnu_170` (172 = 0x1A, 185 = 1/3) | pastille : changer « Jumper ID », « Pin/Pad package length », type électrique |
| E11 | sous-enregistrements 2..4 des pastilles (« », « \|&\|0 », « ») | pastille : renseigner Jumper ID = 1 ; changer le nom de la pastille |
| E12 | pastille `inconnu_596` (pile complète, 55 octets) | pastille en mode Full Stack : changer une forme interne en rectangle arrondi 30 % |
| E16 | remplissage `union` (supposé), piste `union` | créer une union (Tools > Unions) avec un remplissage et une piste |
| E17 | arc `inconnu_47`, piste `inconnu_35/40` | arc de polygone (coulée) vs arc libre : comparer (sinon constants : sans importance) |
| E18 | EmbeddedFonts6 : 3 octets inconnus | option « Embed TrueType fonts » ; police en gras + italique |
| E19 | LayerKindMapping : u32 inconnu (0x9BAAC346) | changer le type d'une couche mécanique (p. ex. Mechanical 5 -> Courtyard Top) |
| E20 | PrimitiveGuids : codes et rôle | supprimer une piste : observer si les entrées suivantes sont renumérotées |
| E21 | Connections6 : 14 octets inconnus (`inconnu_29`) | sur une copie d'une carte qui a un chevelu : défaire une piste et enregistrer |
| E23 | Dimensions6 (vide) | placer une cote linéaire et une cote angulaire |
| E24 | Board6 `V9_STACK_*` : vérifier l'empilage | changer l'épaisseur d'un diélectrique et l'Er dans le Layer Stack Manager |
| E25 | DrillManager : u32 final (0) | ajouter un trou oblong non métallisé |

Tests d'ouverture encore ouverts de l'ancienne liste : cartes produites sans Altium par `generer_pcbdoc.py` (lot C :
ouverture, recoulage des polygones, DRC) et polygone `SHELVED=TRUE` (proposé au recoulage ?) : à reprendre avec les
fichiers du lot C.
