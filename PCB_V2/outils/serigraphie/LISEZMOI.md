# serigraphie : placement autonome de la sérigraphie (après routage)

## Commandes (depuis ce dossier)

    cargo build -j 4 --release --offline
    cargo test -j 4 --release --offline
    target/release/serigraphie --entree altium_rev4 --sortie sorties/serigraphie.json --carte altium_rev4/carte.json --threads 4
    python drc_serigraphie.py --entree altium_rev4 [--apres sorties/serigraphie.json] [--json r.json]
    python rendu_serigraphie.py --entree altium_rev4 [--apres sorties/serigraphie.json] --sortie sorties [--fenetre X0 Y0 X1 Y1]
    python -m unittest test_geom_py test_serigraphie -v

Moteur (version 2.0.0) : `--entree`, `--sortie` et `--carte` sont obligatoires ; options `--graine 1 --chaines 16
--mouvements 20000000 --threads T (0 = tous les cœurs) --reference cuivre|masque --marge 0.02
--commentaires-visibles`. Codes : 0 fait ; 2 entrée invalide (message en français, sans chemin, jamais de
panique) ; 4 erreur interne (calcul sous `catch_unwind`). Le pool de fils a toujours un nombre de fils et une pile
explicites ; le résultat ne dépend pas du nombre de fils. Le moteur ne contient aucune géométrie ni règle d'une
carte : tout vient de `carte.json`.

## `carte.json` (format `carte_serigraphie/1`, mil, repère commun)

`{"format", "unites": "mil", "contour": [[x, y], …], "decoupes": [[[x, y], …], …], "trous": [[x, y, rayon], …],
"regles": {"garde_masque_mm", "garde_bord_mm", "tailles_mm": [[hauteur, épaisseur], …], "garde_trait_mm"?
(0,05), "garde_texte_mm"? (0,1)}}`. Contour et découpes sont des polygones quelconques (arcs discrétisés à 0,5 mil
de flèche). `pcbdoc_serigraphie.geometrie_carte` le tire du `.PcbDoc` et de la section `serigraphie` de
`config_pcbdoc/2` : `garde_masque_mm` null ⇒ règle `SilkToSolderMaskClearance` du fichier (sinon 0,254 mm) ;
`tailles_mm` « auto » ⇒ médiane (hauteur, épaisseur) des désignateurs du gabarit, puis 90 % et 80 % (sans
désignateur : [[1,0, 0,15], [0,9, 0,135], [0,8, 0,12]]) ; `trous_supplementaires_mm` convertis en mil ; garde au
bord 0,5 mm (choix documenté).

## Fichiers d'entrée (jetons de couche neutres)

`pcbdoc_serigraphie.exporter(doc, dossier, config=…)` écrit `pastilles`, `composants`, `vias`, `textes`,
`pistes_serigraphie_top|bottom`, `arcs_serigraphie_top|bottom` (`.json`) et `carte.json` depuis un `.PcbDoc` lu par
`../format_altium` (désignateurs et origine : `PcbDoc.designateurs()` et `PcbDoc.origine()`). Jetons : pastilles
`dessus`, `dessous`, `traversante` ; composants `dessus`, `dessous` ; traits, arcs et textes `seri_dessus`,
`seri_dessous` (sérigraphie) ou `autre`. `appliquer(doc, resultat, index)` applique le résultat au modèle
(désignateurs en police trait, position manuelle, masqués via `NAMEON`, traits rognés supprimés, morceaux ajoutés au
composant). `altium_rev4/` est le jeu d'essai (carte d'exemple, jetons neutres, `carte.json` compris) des tests de
calibrage (`src/tests_calibrage.rs`, `test_geom_py.py`, `test_serigraphie.py`).

## Résultat du moteur (mils, 3 décimales, repère commun)

- `textes[]` : un désignateur par composant : `component, text, x, y, height, stroke_width, rotation, layer,
  mirror, hidden, font="stroke", use_ttfonts=false` (`layer` : `seri_dessus` ou `seri_dessous`), plus `ancien`
  (état d'origine), `cote`, `vertical`, `boite_encre`,
  `ambigu`, `sur_son_corps`, `vias_touches`, `auto_position_altium`.
- `pistes_supprimer[]` / `arcs_supprimer[]` : identifiés par `layer` et coordonnées exactes de l'entrée.
- `pistes_ajouter[]` : morceaux conservés (traits rognés, arcs découpés en cordes) `{x1,y1,x2,y2,width,layer,component}`.
- `stats` : déplacés, réduits, masqués (liste), ambigus (liste), traits rognés, longueur retirée par composant.

### Convention de position d'un texte trait (calibrée sur les Bounds exportés)

Ancre (X, Y) = début de la ligne de base : axe du premier trait, bas des majuscules. Un point d'encre
(u le long du texte, v vers le haut) est en (X, Y) + R(Rotation)·(m·u, v), m = −1 si MirrorFlag, +1 sinon.
Donc : dessus à 0° → (X, Y) = coin bas-gauche ; dessous miroir à 0° → (X, Y) = coin bas-DROIT vu de dessus ;
miroir à 90° → le texte descend (−y) depuis Y. Encre : v ∈ [0, h] (v ≥ −h/6 si « _ ») ; largeur des axes en
unités de h/6 = somme des avances sauf le dernier caractère (6, mais 5 pour « 1 . I ») + 4 pour le dernier
(2 pour « 1 », 3 pour « k »). Les Bounds Altium valent cette encre gonflée de w (le trait réel déborde de w/2).
Vérifié sur les 79 textes trait de l'export, toutes rotations, avec et sans miroir (écart ≤ 0,22 mil, toujours
du côté englobant).
Sens de lecture : dessus 0° / 90° ; dessous (miroir) 0° / 270°, ce qui correspond à 0° / 90° vu de dessous.

## Hypothèses

- **Référence de la règle** : la règle « Silk To Solder Mask » d'Altium se mesure jusqu'au **cuivre** de la
  pastille, pas jusqu'à l'ouverture de masque. C'est prouvé sur le rapport DRC rev3
  (`../placeur_routage/altium_rev3/drc_altium.txt`) : les 196 distances piste/pastille relevées sont égales,
  au 1/1000 mm, à la distance au cuivre, et jamais à la distance au masque. Avec cette mesure, le contrôleur
  trouve 483 violations sur rev4 (≈ 460 dans Altium). Avec le masque + 4 mil, il en trouverait 1730. Le
  moteur garde donc 0,254 + 0,02 mm au cuivre. `--reference masque` applique la consigne la plus stricte,
  mais il faudrait alors rogner presque tous les contours d'empreinte, qui sont à 6–8 mil du cuivre.
- Les commentaires des couches Overlay sont supposés **cachés**. Comptés visibles, l'état d'origine
  donnerait 816 violations, loin des ≈ 460. Seuls TopOverlay et BottomOverlay sont de la sérigraphie.
- TopShape 1 = rond ou oblong (bouts ronds), 2 = rectangle à coins vifs. Une pastille traversante est
  présente sur les deux faces avec la taille Top.
- Corps d'un composant = boîte englobant ses pastilles et ses traits de sérigraphie (pas de courtyard ni de
  3D exportés). Un texte ne doit jamais couvrir le corps d'un autre composant de la même face. Il ne couvre
  le sien qu'en repli, avec une pénalité.
- Gardes : 0,05 mm aux traits, 0,1 mm entre textes, garde au bord, aux découpes et aux trous de `carte.json`. Le
  trou (82 ; 49,5) n'a pas de pastille : on lui donne un rayon de 1,7 mm.
- Vias : leur ouverture est prise à diamètre + 4 mil. C'est un critère souple (pénalité), hors règle IsPad.

## Limites

- Les textes TrueType de l'état d'origine sont contrôlés par leur boîte englobante, ce qui est pessimiste.
- Les textes libres ne sont ni déplacés ni rognés : « +12V » est à 0,236 mm de R80_ALIM1.3. Les traits de
  P1 sortent de la garde de bord. Ces deux points sont signalés et non corrigés.
- Les remplissages (fills) et régions de sérigraphie ne sont pas exportés. Le DRC rev3 en contenait un.
- Ambiguïté : elle est mesurée de boîte à boîte, entre le texte et les corps estimés.
