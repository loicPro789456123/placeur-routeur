# Champs inconnus ou supposés (régénéré par inventaire.py)

Fichiers analysés : PCB_METROID_V2_rev4.PcbDoc, PCB_METROID_V2_rev3.PcbDoc, PCB_METROID_V2.PcbDoc.

Tout octet listé ici est **conservé brut** à la lecture et réécrit à l'identique. Les valeurs observées (valeur×occurrences) servent à choisir les expériences différentielles (voir EXPERIENCES_A_FAIRE.md).

## 1. Champs binaires inconnus (gabarits)

| sous-enregistrement | champ | taille | valeurs observées |
|---|---|---:|---|
| arc | inconnu_47 | 1 | 0×294 |
| arc | inconnu_57 | 3 | 000000×294 |
| arc | inconnu_ffffffff | 4 | 4294967295×294 |
| connexion | inconnu_29 | 14 | 0101100003010100000101000001×18, 0002100003010000030102000001×5, 0202100003010200000102000001×1 |
| connexion | inconnu_ffffffff | 4 | 4294967295×24 |
| entete_region | inconnu_13 | 1 | 0×2587 |
| entete_region | inconnu_16 | 2 | 0×2587 |
| entete_region | inconnu_ffffffff | 4 | 4294967295×2587 |
| pastille | inconnu_103 | 3 | 010000×2474, 010100×586 |
| pastille | inconnu_106 | 4 | 0×3060 |
| pastille | inconnu_112 | 2 | 0×3060 |
| pastille | inconnu_118 | 2 | 0000×3060 |
| pastille | inconnu_125 | 1 | 0×3060 |
| pastille | inconnu_170 | 15 | 00011a000000000000000000000001×3060 |
| pastille | inconnu_186 | 8 | 0000000000000000×3060 |
| pastille | inconnu_61 | 1 | 0×3060 |
| pastille | inconnu_63 | 4 | 0×3060 |
| pastille | inconnu_67 | 1 | 0×2462, 1×598 |
| pastille | inconnu_73 | 1 | 0×3060 |
| pastille | inconnu_94 | 7 | 00000001010101×2474, 00000101010101×586 |
| pastille | inconnu_ffffffff | 4 | 4294967295×3060 |
| pastille_pile | inconnu_261 | 1 | 0×8 |
| pastille_pile | inconnu_531 | 1 | 0×8 |
| pastille_pile | inconnu_596 | 55 | 00000000000000000000000000000000…×4, 00000000000000000000000000000000…×2, 00000000000000000000000000000000…×2 |
| piste | inconnu_35 | 1 | 0×21294 |
| piste | inconnu_40 | 1 | 0×21294 |
| piste | inconnu_46 | 3 | 000000×21294 |
| piste | inconnu_ffffffff | 4 | 4294967295×21294 |
| remplissage | inconnu_37 | 1 | 0×66 |
| remplissage | inconnu_47 | 3 | 000000×66 |
| remplissage | inconnu_ffffffff | 4 | 4294967295×66 |
| texte | inconnu_119 | 4 | 0×2172 |
| texte | inconnu_158 | 1 | 1×2172 |
| texte | inconnu_225 | 1 | 1×2172 |
| texte | inconnu_232 | 8 | 0000008000000080×2172 |
| texte | inconnu_241 | 3 | 000000×2172 |
| texte | inconnu_42 | 1 | 0×2172 |
| texte | inconnu_ffffffff | 4 | 4294967295×2172 |
| via | inconnu_207 | 34 | 00000000000000000000000000000000…×3851 |
| via | inconnu_246 | 12 | 00000000000000002a000000×3851 |
| via | inconnu_299 | 22 | 00000000001e00000009000000000000…×3851 |
| via | inconnu_31 | 1 | 0×3851 |
| via | inconnu_37 | 1 | 0×3851 |
| via | inconnu_50 | 4 | 0×3851 |
| via | inconnu_58 | 8 | 0000000101010100×3341, 0000000000000000×510 |
| via | inconnu_67 | 1 | 1×3341, 0×510 |
| via | inconnu_68 | 6 | 000000000000×2183, 000002001000×1668 |
| via | inconnu_ffffffff | 4 | 4294967295×3851 |

## 2. Champs binaires « supposés » (sens déduit, non confirmé)

| gabarit | décalage | champ | description |
|---|---:|---|---|
| REMPLI | 38 | union | index d'union (par analogie avec les pistes) |
| VIA | 0 | couche | toujours 74 (MULTILAYER) |
| VIA | 42 | plan_expansion_relief | expansion de relief sur plan (20 mil par défaut) |
| VIA | 46 | plan_degagement | dégagement sur plan (20 mil par défaut) |
| VIA | 203 | couche_v7 | couche V7 (toujours 0x0103000F = MULTILAYER) |
| VIA | 259 | guid_gabarit | GUID du gabarit pad/via (propre à chaque via) |
| VIA | 275 | guid_bibliotheque | GUID de la bibliothèque pad/via (= PadViaLibraryCache LIBRARYID) |
| PASTILLE | 68 | relief_entrefer | thermique : entrefer (analogie via) |
| PASTILLE | 72 | relief_branches | thermique : nombre de branches |
| PASTILLE | 74 | relief_largeur | thermique : largeur des branches |
| PASTILLE | 78 | plan_expansion_relief | expansion relief plan |
| PASTILLE | 82 | plan_degagement | dégagement plan |
| PASTILLE | 114 | vers_couche | octet 0 de la couche V7 de la pastille (114..117 = u32 V7 : 0x01000001 TOP, 0x0100FFFF BOTTOM, 0x0103000F MULTI ; vérifié sur 3060 pastilles et 95 retournements Altium de rev5_auto) ; KiCad : couche d'arrivée |
| PASTILLE | 115 | inconnu_115 | octets 1-2 de la couche V7 (0 TOP, 0x00FF BOTTOM, 0x0300 MULTI) |
| PASTILLE | 117 | depuis_couche | octet 3 de la couche V7 (famille, toujours 1) ; KiCad : couche de départ |
| PASTILLE | 126 | guid_gabarit | GUID du gabarit pad/via |
| PASTILLE | 142 | guid_bibliotheque | GUID de la bibliothèque pad/via (= PadViaLibraryCache) |
| TEXTE | 1 | drapeaux1 | comme les autres primitives (0x04 = non verrouillé) |
| TEXTE | 2 | drapeaux2 | 0x02 keepout |
| TEXTE | 3 | reseau | index réseau (0xFFFF) |
| TEXTE | 5 | polygone | index polygone |
| TEXTE | 153 | codebarre_largeur_min | largeur minimale de barre (calculée par Altium : 4,63 mil à l'exp. E14 ; 0 sinon) |
| TEXTE | 244 | ref_x | point de référence X (proche de x ; ancrage avant décalage de justification ?) ; 0 sur les textes anciens |
| TEXTE | 248 | ref_y | point de référence Y |
| CONNEXION | 13 | x1 | extrémité 1 X |
| CONNEXION | 17 | y1 | extrémité 1 Y |
| CONNEXION | 21 | x2 | extrémité 2 X |
| CONNEXION | 25 | y2 | extrémité 2 Y |

## 2 bis. Champs confirmés par expérience dans Altium

| gabarit | décalage | champ | description |
|---|---:|---|---|
| PISTE | 1 | drapeaux1 | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| PISTE | 2 | drapeaux2 | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| PISTE | 45 | restrictions_keepout | restrictions keepout : 0x1F = toutes, bit0 = vias (exp. E03) ; autres bits supposés (pistes, cuivre, CMS, traversantes) ; 0 si non keepout |
| ARC | 1 | drapeaux1 | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| ARC | 2 | drapeaux2 | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| REMPLI | 1 | drapeaux1 | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| REMPLI | 2 | drapeaux2 | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| VIA | 1 | drapeaux1 | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| VIA | 2 | drapeaux2 | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| VIA | 54 | masque_expansion_haut | expansion masque de soudure (haut) (exp. E05) |
| VIA | 66 | masque_mode | mode d'expansion du masque : 1 règle, 2 manuelle (exp. E05) |
| VIA | 241 | masque_bas_separe | 1 = expansion bas distincte de l'expansion haut (exp. E05) |
| VIA | 242 | masque_expansion_bas | expansion masque de soudure (bas) (exp. E05) |
| VIA | 258 | masque_depuis_bord_trou | expansion mesurée depuis le bord du trou (exp. E05) |
| VIA | 291 | tolerance_plus | tolérance de perçage + (0x7FFFFFFF = non définie) |
| VIA | 295 | tolerance_moins | tolérance de perçage - (0x7FFFFFFF = non définie) |
| PASTILLE | 1 | drapeaux1 | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| PASTILLE | 2 | drapeaux2 | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| PASTILLE | 86 | pate_expansion | expansion masque de pâte (manuelle) ; -99999 mil quand la pâte est désactivée (exp. E08) |
| PASTILLE | 90 | masque_expansion | expansion masque de soudure (manuelle, haut) (exp. E09) |
| PASTILLE | 102 | masque_mode | 0 aucune, 1 règle, 2 manuelle (exp. E09) |
| PASTILLE | 110 | jumper_id | identifiant de cavalier (Jumper ID) (exp. E08b) |
| PASTILLE | 120 | masque_bas_separe | 1 = expansion bas distincte (exp. E09) |
| PASTILLE | 121 | masque_expansion_bas | expansion masque de soudure (bas) (exp. E09) |
| PASTILLE | 162 | tolerance_plus | tolérance de perçage + (0x7FFFFFFF = non définie) (exp. E09) |
| PASTILLE | 166 | tolerance_moins | tolérance de perçage - (exp. E09) |
| PASTILLE | 185 | pate_active | 3 = masque de pâte actif, 1 = désactivé (exp. E08a) |
| TEXTE | 27 | rotation | rotation (exp. E13 : Altium recalcule x/y autour du point de rotation) |
| TEXTE | 43 | type_police | 1 = TrueType, 0 sinon (trait ou code-barres) (exp. E14) |
| TEXTE | 110 | inverse | texte inversé (exp. E15) |
| TEXTE | 123 | rect_inverse | inversion par rectangle (exp. E15) |
| TEXTE | 137 | codebarre_largeur | largeur totale du code-barres (exp. E14 ; 1050 mil par défaut) |
| TEXTE | 141 | codebarre_hauteur | hauteur totale du code-barres (exp. E14 ; 210 mil par défaut) |
| TEXTE | 145 | codebarre_marge_x | marge code-barres X (exp. E14) |
| TEXTE | 149 | codebarre_marge_y | marge code-barres Y (exp. E14) |
| TEXTE | 160 | type_texte | 0 trait, 1 TrueType, 2 code-barres (exp. E14) |
| ENTETE_REGION | 1 | drapeaux1 | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| ENTETE_REGION | 2 | drapeaux2 | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |
| CONNEXION | 1 | drapeaux1 | bit2 (0x04)=0 -> verrouillé (exp. E01) ; bit3 (0x08)=routé par l'utilisateur (exp. E02) ; bit1 (0x02)=contour de polygone ; pastille/via : 0x20 tenting haut, 0x40 tenting bas, 0x80 point de test fab haut |
| CONNEXION | 2 | drapeaux2 | 0x02 = keepout (exp. E03 ; le réseau passe alors à 0xFFFF) ; pastille/via : 0x01 point de test fab bas (non vérifié) |

## 3. Sous-enregistrements 2 à 4 des pastilles (chaînes courtes, sens inconnu)

- pastille sous-enr. 2 : 00×3060
- pastille sous-enr. 3 : 047c267c30×3060
- pastille sous-enr. 4 : 00×3060

## 4. Clés de propriétés non reconnues par le dictionnaire

Aucune : toutes les clés rencontrées sont rattachées à une description (statut C ou S).

## 5. Flux conservés bruts (structure interne non décodée)

- `LettersGeometry/PrimIndexes` : 80 octets

## 6. Restes non structurés dans des flux décodés

- `DrillManager/Data` : 12 octets
- `EmbeddedFonts6/Data (FluxPolices)` : 9 octets
- `LayerKindMapping/Data (FluxCorrespondanceCouches)` : 12 octets
- `LettersGeometry/Data (FluxGeometrieLettres)` : 1722770 octets
