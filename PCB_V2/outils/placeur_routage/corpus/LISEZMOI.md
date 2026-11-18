# Corpus du contrat (lot E)

Le corpus rend le contrat (`PCB_V2/outils/CONTRAT.md` 2.1 et son avenant 2.2) exécutable : exemples de chaque format,
cas de validité qui départagent `validite.rs` (Rust) et `drc_maison.py` (Python), cartes KiCad synthétiques pour la
déduction et la chaîne complète. Tout ce qui est ici est public, déterministe et régénérable ; aucun dossier `essais`,
aucun `.PcbDoc` (le chemin `.PcbDoc` est dans `corpus/pcbdoc/`, lot B).

État au jalon E1 : 14 cas de validité, 4 cartes (`deux_couches`, `quatre_couches`, `deux_convertisseurs`,
`sans_classes`) ; les 60 cas et les 24 cartes de §7.3 sont complétés aux étapes 7 et 8 du lot E.

## Arborescence

```
LISEZMOI.md              ce fichier : formats propres au corpus, conventions, commandes
.gitattributes           * text eol=lf (fichiers extraits en LF malgré core.autocrlf=true)
generer_cas.py           cas de validité (§7.4) : --sortie DOSSIER ; attendus écrits à la main dans le source
generer_cartes.py        cartes KiCad 8 (§7.3) : --sortie DOSSIER ; attendus écrits à la main dans le source
test_corpus.py           déterminisme, corpus suivi = sortie des générateurs, formats, attendus, syntaxe KiCad
validite/<NNN_nom>/      cas.json, donnees_placeur.json, placements_routage.json, routage_detaille.json,
                         attendu_strict.json, attendu_altium.json, attendu_mesures.json (+ vias_couture.json)
cartes/<nom>/            <nom>.kicad_pcb, <nom>.kicad_pro, contraintes.json, attendu_deduction.json
formats/exemples/        un exemple complet par format de §2 (+ formes v1, conversion v1 attendue)
schemas/                 parametres_moteur.defaut.json (annexe C + avenant, partie D), extraire_parametres.py
validite_rs/             caisse d'essai Rust de src/validite.rs (Cargo.toml, lib.rs, verifier.rs, tests/)
```

`comparer_validite.py` (étape 7) compare attendus, Rust et Python ; `test_comparer.py` le teste par doublures injectées
et, s'il est construit, sur le binaire d'essai.

## Commandes

Depuis `placeur_routage/` :

```
python corpus/generer_cas.py --sortie corpus/validite         # régénère les cas (identiques octet pour octet)
python corpus/generer_cartes.py --sortie corpus/cartes        # régénère les cartes
python -m unittest discover -s corpus -p "test_*.py" -v       # tests du corpus (le dossier n'est pas un paquet)
python -m unittest -v test_formats                            # validateurs de formats
python formats.py valider corpus/validite/011_isolement_piste_pastille/attendu_strict.json
cargo test --release --offline -j 4 --manifest-path corpus/validite_rs/Cargo.toml
```

Codes des deux générateurs (§1.4, outils Python) : 0 écrit ; 2 option manquante ou `--sortie` inutilisable (fichier
existant, dossier impossible à créer ou à écrire), message sans trace ni chemin absolu.

Après une modification d'un générateur, régénérer puis relancer les tests : `test_corpus.py` échoue si le corpus suivi
diffère de la sortie du générateur.

### `comparer_validite.py` (ligne de commande figée pour la CI du lot D ; livré à l'étape 7)

```
python corpus/comparer_validite.py --cas DOSSIER --moteur EXE [--cout-rust EXE] [--modules-python DOSSIER]
                                   [--mode strict|altium|les_deux] --rapport F
python corpus/comparer_validite.py --graine DOSSIER --donnees F --moteur EXE [--cout-rust EXE]
                                   [--modules-python DOSSIER] [--mode strict|altium|les_deux] --rapport F
```

- `--cas DOSSIER` : tous les cas `DOSSIER/<NNN_nom>/` (lus par leur `cas.json`) ; `--graine DOSSIER` : une graine
  réelle (`placements_routage.json`, `routage_detaille.json`, `vias_couture.json` facultatif) avec `--donnees F`.
- `--moteur EXE` : tout exécutable qui offre `verifier` (§5.1 : `placeur_routage` du lot A ou `validite_verifier` de
  la caisse d'essai) ; `--cout-rust EXE` : exécutable qui écrit `mesures/2` (`validite_verifier verifier … --mesures
  F`) ; `--modules-python DOSSIER` : dossier qui contient `drc_maison.py`, `verifier_placement.py` et `mesures.py`
  du lot C (`verifier`, `verifier`, `mesurer`, §5.2) ; sans lui, seule la comparaison attendus / Rust est faite.
  Au moins une implémentation est demandée (`--moteur` peut être omis si `--modules-python` est donné) ; en
  `--graine`, au moins deux (pas d'attendu). Un échec d'exécution d'une implémentation sur un cas est un écart.
- `--mode` : défaut `les_deux`.
- Champs comparés : `valide`, `liaisons_ouvertes`, `violations_par_type`, `derogations` (chaque mode), placement
  (`valide`, `violations_par_type`) et `cout_commun` à 1·10⁻⁶ près ; attendus, Rust et Python deux à deux.
  Placement : attendu = champ `placement` de `attendu_strict.json` (voir `cas_validite/1`) ; Rust = champ
  `placement` du `verdict/2` écrit par `verifier --mode strict` ; Python = `verifier_placement.verifier(placements,
  donnees, grille=cas.grille)`. Comparé une fois par cas (indépendant du mode) ; en `--graine`, Rust et Python seuls.
- `--rapport F` : JSON `comparaison_validite/1` (`cas`, `sources`, `comparaisons`, `nombre_ecarts`, `ecarts[]` :
  `{cas, quoi, champ, sources, valeurs}`), écrit dès que la comparaison a eu lieu (codes 0 et 1).
- Codes : 0 aucun écart ; 1 écarts (rapport écrit) ; 2 entrée ou environnement invalide (exécutable absent, module
  sans `verifier`, option inconnue) ; 4 erreur interne. Messages sans chemin absolu.

Commande du job `contrat` (lot D), depuis `placeur_routage/` :

```
python corpus/comparer_validite.py --cas corpus/validite --moteur target/release/placeur_routage --cout-rust corpus/validite_rs/target/release/validite_verifier --modules-python . --rapport comparaison_validite.json
```

## Format `cas_validite/1` (`cas.json` de chaque cas)

| Champ | Contenu |
|---|---|
| `format` | `"cas_validite/1"` |
| `nom` | nom du dossier, `NNN_mots` (trois chiffres, minuscules) |
| `description` | texte |
| `themes` | thèmes de §7.4 couverts, pris dans `generer_cas.THEMES` (`contact:piste-pastille`, `contact:piste-piste`, `contact:piste-via`, `contact:via-pastille`, `contact:via-via`, `contact:zone-via`, `zone_plan`, `modes_differents`, `audit:D3.2`, `violation:<clé de §4.4>`, `arrondi:largeur`, `mesure:coudes`, `mesure:ecart` ; la liste s'allonge aux étapes suivantes) |
| `grille` | bool : contrôle de la grille de placement (`false` ⇒ `verifier … --sans-grille`) |
| `couture` | bool : `vias_couture.json` présent (passé par `--couture`) |
| `modes_differents` | bool : les attendus strict et altium diffèrent sur un champ comparé |
| `fichiers` | `{"donnees", "placement", "routage", "attendu_strict", "attendu_altium", "attendu_mesures", "couture": nom\|null}` |

Les attendus sont des `verdict/2` complets (champ `outil` : « attendu ecrit a la main (corpus du lot E) ») et un
`mesures/2`. **Attendu de placement** : le champ facultatif `placement` de `verdict/2` (contenu `controle_placement/1`
avec son `format`), présent dans **chaque** `attendu_strict.json` et jamais dans `attendu_altium.json` (le contrôle de
placement ne dépend pas du mode) ; `cas_validite/1` ne porte donc aucun fichier ni champ de plus. Les cas de §4.5
(étape 7) y écrivent leurs violations de placement ; le banc Rust (`tests/cas.rs`) compare `valide`,
`violations_par_type`, la suite des (genre, pièces) et `infos` (à 1·10⁻⁶) ; `comparer_validite.py` seulement `valide`
et `violations_par_type` ; les textes ne sont jamais comparés. Leurs valeurs sont écrites à la main dans `generer_cas.py`, calcul en commentaire ; elles ne sont
jamais produites par `validite.rs` ni par `drc_maison.py`. `test_corpus.py` recompte indépendamment la longueur et
les vias sur le routage et contrôle que strict est au moins aussi exigeant qu'altium.

Repère commun des cas : carte de 800 × 400 mil, origine au coin bas-gauche (mil et mm ne diffèrent que du facteur
0,0254) ; pastilles CMS carrées de 40 mil sur TopLayer ; pistes de 10 mil ; vias 0,6 / 0,3 mm ; isolement 0,2 mm
(s = 0,6, p = 1,0) ; largeurs [0,2 ; 0,254 ; 1,0] ; pièces fixes à leur pose (placement valide).

## Format `attendu_deduction/1` (`attendu_deduction.json` de chaque carte)

Attendus de la déduction (§3) sur la carte, écrits à la main d'après le contrat (règle citée dans le commentaire de
la carte, `generer_cartes.py`). Le test de B (`test_deduire_config`) compare sa déduction champ par champ.

| Champ | Contenu | Comparaison |
|---|---|---|
| `format`, `carte`, `famille` | `"attendu_deduction/1"`, nom, `"chaine"` ou `"deduction"` | — |
| `description`, `regles` | texte ; règles du contrat exercées | information |
| `couches` | `[{"nom", "role", "plans": [réseaux]}]`, toutes les couches de cuivre de haut en bas | exacte, dans l'ordre (`plans` = réseaux de `zones_plan`) |
| `masses` | réseaux de masse, la principale en premier | exacte, dans l'ordre |
| `roles` | `{rôle: [réseaux]}` ; chaque réseau de la carte y figure une fois | exacte |
| `largeurs` | `{réseau: [min, préférée, max\|null]}` | pour les réseaux cités |
| `convertisseurs` | liste ; chacun désigné par `reseau_commutation` | même nombre ; champs cités exacts (listes de pièces comparées comme ensembles) ; champs absents non comparés |
| `sans_piste_dessous`, `pas_fin`, `matrice` | listes de pièces | ensembles exacts |
| `isolement` | `{"defaut", "requis": [{"reseaux": [a, b], "mm"}]}` | `regles.isolement.defaut` ; `matrice[classe piste de a][classe piste de b]` |
| `pieces_fixes` | `{pièce: raison_statut}` | exacte ; toute autre pièce est mobile |
| `groupes` | `[{"type", "pieces"}]` | même multiensemble de (type, ensemble de pièces) ; noms non comparés |
| `chaine` | `null` (déduction) ou `{"niveau", "graines", "graines_valides_min", "couches_pistes", "couches_sans_piste", "descentes_par_vias"}` | attendus de la CI : au moins `graines_valides_min` graines valides (§4.6-4) parmi `graines` graines lancées au `niveau` donné (critère symétrique de §7.7 : 1 sur 10 en `rapide`), couches seules permises aux pistes, couches sans aucune piste, réseaux de plan reliés par des vias dans leurs zones. Le job `synthetiques` de §7.6 (`--essais 2`) exige le même minimum (au moins une graine valide) sur ses 2 graines ; un protocole qui lance moins de `graines` graines garde ce minimum. Toute carte « chaîne » a `graines_valides_min` ≥ 1 |

`generer_cartes.valider_attendu_deduction(d, pieces, reseaux, couches)` contrôle la forme et que l'attendu ne cite que
des pièces, réseaux et couches de la carte.

Cartes KiCad : version 20240108 (KiCad 8), numéros de couches de KiCad 8 (`F.Cu` 0, `In<n>.Cu` n, `B.Cu` 31),
empreintes paramétriques, identifiants uuid5 déterministes ; pièces dessous selon la convention de retournement de
KiCad (couches `B.*`, y local des pastilles en miroir, textes `(justify mirror)`) ; classes dans `.kicad_pro`
(`netclass_patterns`, et `netclass_assignments` sur `deux_convertisseurs`, motif joker `SW*` compris) ; règles
`min_clearance` 0,15, `min_track_width` 0,2, `min_via_diameter` 0,5, `min_through_hole_diameter` 0,3,
`min_hole_to_hole` 0,25, `min_copper_edge_clearance` 0,5. Piège voulu de `deux_convertisseurs` : la broche HS du pilote
U1 touche aussi L1 et satisferait R-V3 ; le nœud SW1 relève de R-V1 (un seul convertisseur par `reseau_commutation`).

## Identifiants des verdicts (§2.8)

Pistes `P<k>` (rang dans `routage.pistes`), vias `V<k>`, surfaces `S<k>`, vias de couture `C<k>`, pastilles
`<des>#<k>`, régions `<des>/R<k>`, vias d'empreinte `<des>/V<k>`, trous `T<k>`, cuivre fixe `F<k>`, zones
`Z:<id>`, contour `contour`, découpes `D<k>`. Noms des liaisons ouvertes : `<des>.<nom de pastille>`. Les entrées
de `couches[].zones_plan` n'ayant pas d'identifiant, `<id>` = `<nom de la couche>/<k>` (k = rang dans la liste) :
`Z:MidLayer1/0`.

## Conventions retenues là où le contrat laisse un choix (§8.0-2 c)

Elles font foi pour `validite.rs` et pour les contrôles du lot C : les attendus du corpus les appliquent.

### Validité (§4)

1. **Ordre des objets** (égalités, ordre des `objets` d'une violation, paire de la marge) : pastilles (ordre des
   pièces puis des pastilles dans les données), régions, vias d'empreinte, cuivre fixe, zones de plan, `P<k>`,
   `V<k>`, `S<k>`, `C<k>`. `reseaux` d'une violation : dans l'ordre de ses `objets`.
2. **Composante de référence** (§4.3) : celle qui contient le plus de zones de plan, puis le plus d'objets (membres et
   conducteurs, comme `drc_maison.py` de `9b5b5a3`), puis celle qui contient le plus petit objet (ordre 1).
3. **Liaison ouverte** : `nature` `pastilles` si la composante contient une pastille, sinon `ilot` (`pastilles` et
   `noms` vides) ; `position_mm` = centre de sa première pastille, sinon premier point du premier objet.
4. **`position_mm` d'une violation** : milieu des deux points les plus proches (isolement, gardes, trou_trou) ;
   extrémité fautive (antenne) ; milieu de la piste (`largeur`, `couche_non_permise`, `reseau_inconnu` d'une piste) ;
   centre du via (`dimension_via`, `via_sous_cms`) ; premier point du contour d'une surface.
5. **`ecart_mm` / `requis_mm`** : écart mesuré et requis (isolement, gardes, trou_trou) ; largeur mesurée et borne
   violée (`largeur`), dimension mesurée et borne violée (`dimension_via`), en mm ; null sinon (antenne,
   `couche_non_permise`, `reseau_inconnu`, `via_sous_cms`).
6. **Antenne** : jugée au sens strict dans les deux modes ; une violation par piste (comme `9b5b5a3`), à sa première
   extrémité fautive dans l'ordre (x1, y1), (x2, y2).
7. **`trou_trou`** : quel que soit le réseau des deux objets (deux vias d'un même réseau compris), paires
   préexistantes exclues (comme `9b5b5a3`).
8. **Zones de plan** : jamais objet d'une violation d'isolement (dégagement du plan) ; aucun contact piste–zone ;
   contact strict avec une pastille traversante non ronde : « rayon de l'objet » = rayon circonscrit de son cuivre
   sur la couche.
9. **`garde_bord`** : la partie de l'axe d'une piste à distance signée ≤ 0 du cuivre d'une pastille de son réseau
   n'est pas comptée ; la garde s'applique au reste. **`via_sous_cms`** : distance du centre du via au cuivre de la
   pastille < rayon du via − ε.
10. **Écart moyen** (§4.7) : aucune piste avec voisin ⇒ `ecart_moyen_mm` = 0 (somme / max(1, n), comme
    `drc_maison.py` de `9b5b5a3`) et `ecart_min_mm` = null ; le coût commun compte alors 1000 × s.
11. **Marge d'isolement** : paires contrôlées = au moins un objet neuf, réseaux différents ou sans réseau, couche
    commune, boîtes élargies de max(0,3 ; plus grand isolement de la matrice) qui se recouvrent ; aucune ⇒ null et
    `""` ; paire écrite « objet(réseau) / objet(réseau) » dans l'ordre 1.
12. **Coudes** : extrémités regroupées par x_mm × 1000 et y_mm × 1000 arrondis au plus proche, égalité au pair
    (`round` de Python, `round_ties_even` en Rust), x_mm = x_mil × 0,0254.
13. **Largeurs** : bornes converties en mil par `ceil((mm / 0,0254) × 1000 − 1e-6) / 1000` (0,2 mm → 7,875 mil,
    0,254 mm → 10,000 mil) ; longueur d'une piste = hypot en mil × 0,0254 ; résistance nulle pour une largeur nulle.
14. **`paires_desequilibrees`** : longueurs et nombres de vias du routage (couture exclue) ; `tolerance_longueur_mm`
    null ⇒ 1 mm.
15. **Vias de couture** : objets `C<k>`, contrôlés, jamais comptés dans `mesures.vias` ni dans le coût.
16. **`regles_non_couvertes`** = `donnees.parametres_deduction.regles_non_interpretees` (entier ≥ 0), 0 s'il est
    absent ; **`zones_plan_isolees`** : cinquième clé de `avertissements` ; `derogations` porte toujours
    `via_dans_pastille_meme_reseau` ; `exemples` ne porte que les types qui ont au moins une violation (textes libres,
    jamais comparés).
17. **Placement** : forme v1 (liste nue) : une pièce fixe absente n'est pas `absente` ; v2 : toute pièce non
    virtuelle absente l'est ; pièce virtuelle prise à sa pose cible ; désignateur en double, pièce mobile sans
    orientation, réseau ou couche non déclarés : entrée invalide ; `grille_mm` ≤ 0 : aucun contrôle de grille.
18. **`outil`** : « validite <version> » pour `validite.rs` ; le lot A peut le remplacer dans `stats.validite`.
19. **`pose_v3`** : jamais lu (ni Rust ni Python) ; refusé dans les données (champ supprimé).
20. **Routage v1** = sans `format` et sans `couches_routage` ; `unite` facultative mais `mil` si présente ;
    `surfaces` facultatives ; `de` / `a` des vias non lus.
21. **Formes** (étape 5) : `rond` de largeur ≠ hauteur et `oblong` = capsule selon le grand côté ; `rect_arrondi`
    = rectangle réduit du rayon de coin, épaissi de ce rayon ; piste de cuivre fixe = capsule (bouts ronds) ;
    `polygone` : `points` dans le repère local de la pièce (rotation et centre de la pastille non appliqués).
22. **Pile** (§2.2) : forme `principale` sur la couche extérieure de la face de la pièce posée, `opposee` sur l'autre
    couche extérieure, `interne` sur les couches internes (absente : forme principale) ; centre d'une entrée de pile
    = centre de la pastille + `decalage` (repère local de la pièce) ; rotation relative à la pièce.
23. **Couches d'une pièce retournée** : les `couches` des pastilles et des régions sont celles de la face de
    `pose_cible` ; une pièce posée sur l'autre face voit le rang k devenir N − 1 − k. Une pièce absente du placement
    (pièce virtuelle, forme v1) est à `pose_cible`.
24. **Contacts stricts** (§4.2) : tolérance ε_contact ajoutée partout, y compris via – via (entraxe ≤ max(rayons) +
    ε) et zone – via (distance au bord ≥ rayon + isolement − ε) ; une pastille traversante compte pour la ligne 8 si
    son trou est métallisé et qu'elle a du cuivre sur la couche du plan ; un via d'empreinte et un cuivre fixe de type
    `via` sont des « vias » pour la ligne 8 et des objets préexistants pour les lignes 2, 4 et 7.
25. **Composantes** : une classe sans membre ni objet neuf (région, via d'empreinte, cuivre fixe seuls) est ignorée ;
    `position_mm` d'une liaison sans pastille = centre du premier objet (milieu d'une piste, centre d'un via, centre
    de la boîte sinon) ; liaisons dans l'ordre de `donnees.reseaux`, puis de leur plus petit objet.
26. **Région de liaison** : jamais voisine (écart moyen, marge d'isolement) ni en violation des réseaux qu'elle lie
    (cas 028).
27. **Écart moyen** (étape 6) : voisins = objets d'un autre réseau ou sans réseau ayant une couche commune avec la
    piste, zones de plan exclues (dégagement du plan, comme la convention 8) ; `ecart_min_mm` = minimum des écarts
    des pistes, **non plafonné** ; écart d'un recouvrement : valeur ≤ 0 de `Forme::ecart` (minoration de la
    pénétration).
28. **Isolement** : requis = `matrice[classe(p)][classe(q)]`, p et q dans l'ordre 1 ; classe d'un objet neuf =
    `classes_isolement` (piste, via, surface ; via de couture = via) de son réseau, réseau inconnu : `classe_reference` ;
    cuivre de trou (`trous[]` à `diametre_cuivre` > 0, hors trous de pièce) : objet sans réseau `T<k>` de sa classe,
    sur toutes les couches, rangé après le cuivre fixe.
29. **Gardes** : violation si écart < garde − ε_isolement (bord, découpe, trou) ; via et surface : aussi hors du
    contour si leur cuivre n'y est pas entièrement contenu ; une violation par objet (bord), par couple objet –
    découpe ou objet – trou. Découpes : celles de `carte.decoupes`, sauf celles d'une pièce qui porte ses propres
    `decoupes` (posées avec elle). Trous de `trous[]` à `piece` renseigné : ignorés par le routage (les pastilles de
    la pièce les portent). `garde_trou` : trous de `trous[]` non métallisés ou à `diametre_cuivre` ≤ diamètre, trous
    non métallisés des pastilles.
30. **`zone_interdite`** : écart du cuivre à la zone < `isolement.defaut` − ε_isolement, sur une couche de la zone
    (via : toutes) ; effet `pistes` (piste), `vias` (via, via de couture), `remplissages` (surface) ; zone d'empreinte
    posée avec la pièce (couches retournées comme la convention 23) ; une violation par couple objet – zone.
31. **Antenne** : une piste de réseau inconnu n'est jugée que par `reseau_inconnu`.
32. **`pistes_sous_pieces_sans_piste_dessous`** : un compte par couple (piste, pièce) : piste sur la couche
    extérieure de la face posée de la pièce, dont le cuivre coupe le rectangle `lbb` posé, à plus de 0,3 mm de chaque
    pastille de la pièce sur cette couche.
33. **`via_sous_cms`** : une violation (ou une dérogation) par couple via neuf – pastille CMS sans trou dont
    `via_dans_pastille_permis` est faux ; `objets` = [pastille, via].
34. **`position_mm`** des violations entre deux objets (isolement, garde_trou, trou_trou) : milieu des deux centres
    (provisoire : la convention 4 demande le milieu des points les plus proches ; jamais comparé par le banc).

35. **Contrôle de placement : tolérance** : recouvrements et inclusions à 1·10⁻⁶ mm (un contact bord à bord n'est
    pas un recouvrement ; une boîte posée sur le bord d'une zone imposée y est contenue) ; distances aux marges
    comparées à `marge − 1·10⁻⁶` ; poses (fixe déplacée, `au_bord`, groupe rigide) à 1·10⁻³ mm et ε_angle.
36. **Pièce à sa pose d'entrée** : pièce fixe posée à sa `pose_cible`, elle-même égale à sa `pose_actuelle` (une pièce
    de `fixes_deplaces` a une `pose_cible` différente de sa pose du fichier). `obstacle`, `marge_bord` et
    `marge_decoupe` ne portent que sur les pièces qui ne sont pas à leur pose d'entrée ; `chevauchement` et
    `pastille_pastille` sur les paires dont l'une au moins ne l'est pas ; `zone_imposee`, `au_bord`, `eloignement` et
    `groupe_rigide` sur toutes les pièces citées. Les pièces virtuelles ne comptent que par leurs pastilles
    (`pastille_pastille`).
37. **Boîte d'une pièce** : boîte des quatre coins de `lbb` posés. `chevauchement` et `marge_bord` l'élargissent de
    `marge_piece_mm` ; `obstacle`, `marge_decoupe`, `zone_imposee`, `au_bord` et l'éloignement d'une pièce
    (centre) la prennent sans marge.
38. **`chevauchement` par la face opposée** : pour deux pièces de faces différentes A et B, toute pastille de B dont
    le cuivre posé est sur la couche extérieure de la face de A (traversante, ou CMS de la face opposée de B) ;
    boîte de ce cuivre élargie de max(0, `expansion_masque`) + `marge_traversante_mm`, contre la boîte de A élargie
    de `marge_piece_mm` ; une violation [A, B] par sens. Deux pièces de même face : une violation par paire.
39. **`obstacle`** : une violation par (pièce, obstacle) : obstacle de placement d'une face de la pièce ; zone
    interdite à effet `placement` d'une autre pièce posée (faces = couches extérieures de la zone posée, ou face de
    sa pièce si elle n'en a pas ; `pieces` = [pièce, propriétaire]) ; disque `rayon_interdit_mm` d'un trou qui n'est
    pas celui de la pièce.
40. **`marge_bord`** : une violation par pièce (boîte élargie hors du contour exact, ou à moins de `marge_bord_mm`
    d'une arête). Une pièce `au_bord` est dispensée de chaque côté demandé (pour `quelconque`, des côtés où elle
    colle) : sa boîte élargie y est ramenée au côté de la boîte de la carte et les arêtes du contour portées par ce
    côté sont ignorées.
41. **`marge_decoupe`** : une violation par (pièce, découpe) ; découpes de la carte (sauf celles dont le propriétaire
    a ses propres découpes, déjà posées avec lui) et découpes des pièces posées, jamais celles de la pièce elle-même.
42. **`eloignement`** : point de référence = moyenne des centres des boîtes des pièces de `reference` ; objet
    `PIECE#k` = centre de la pastille posée, `PIECE` = centre de sa boîte. `infos.eloignements` : une entrée par
    (éloignement, objet), `dure` et `souple`, `reference` = noms joints par « + » ; violation (`dure` seulement) si
    distance < `distance_mm` − 1·10⁻⁶, `pieces` = [pièce de l'objet].
43. **`pastille_pastille`** : une violation par couple de pastilles (sans exemption de réseau), première couche
    commune fautive ; requis = `matrice[classe(a)][classe(b)]`, comparé à l'écart − ε_isolement.
44. **`zone_imposee`** : violation si la face de la pièce n'est pas dans `faces` de la zone, ou si sa boîte n'y est
    pas contenue. **`au_bord`** : `gauche` = x min, `bas` = y min, `droite` = x max, `haut` = y max de la boîte de la
    carte, décalés vers l'extérieur de `debord_mm`. **`groupe_rigide`** : pose relative de chaque pièce dans le repère
    de la première pièce du groupe (position locale, rotation relative, face relative) comparée à l'entrée
    (`pose_cible`) ; une violation par groupe, `pieces` = pièces du groupe ; un groupe `rigide` sans `pieces` vaut par
    exemplaire.
45. **Ordre des violations de placement** : par pièce dans l'ordre des données (`absente`, `fixe_deplacee`,
    `orientation`, `grille`), `inconnue` dans l'ordre du placement, puis clé par clé dans l'ordre de §4.5.

### Formats (`formats.py`)

21. `charger(chemin, format_attendu)` : nom (`donnees_placeur`) ou nom et version (`donnees_placeur/2`) ;
    `ErreurFormat.erreurs` = « <fichier> : <chemin> : <cause> », nom de fichier seul ; version inconnue :
    « <fichier> : format X/N non pris en charge ». Ligne de commande `formats.py valider F [--config-utilisateur]` :
    0 conforme, 2 non conforme ou illisible, 4 erreur interne.
22. Champs inconnus ignorés à tout niveau des fichiers écrits par un programme ; refusés (avec la clé la plus proche)
    dans les fichiers écrits à la main ; clés `_*` ignorées partout. Références croisées contrôlées seulement si la
    liste de référence est lisible, cohérence interne seulement si le schéma est respecté.
23. Entiers sans partie décimale (`4.0` refusé) ; comptes, rangs, classes ≥ 0 ; dimensions, longueurs, durées,
    poids ≥ 0 ; coordonnées et coûts de signe quelconque ; parts dans [0, 1]. JSON jamais moins strict que serde_json
    (bornes ±1e308, imbrication 127, substituts isolés) ; clés en double refusées.
24. `null` accepté en plus de la table, dans le sens permissif : `paires[].tolerance_longueur_mm`, `stats.validite`
    (cause « non verifie »), `mesure_convertisseurs.convertisseurs[].sensibles_distance_min_mm`, règles facultatives
    de `entree_carte.regles`, champs d'une graine plantée de `rapport.json`.
25. `mesure_convertisseurs/1` : racine `{"format", "convertisseurs": [...]}`. `rapport.parametres.effectifs` :
    paramètres complets (`campagne.threads` permis) ; `stats.parametres_effectifs` sans `campagne.threads`.
26. Contraintes : sans `format` ⇒ version 1 convertie (avertissement) ; `lire_contraintes(chemin, config=None,
    plans_deduits=None)` : `config` sert à `soft_sw` (sans elle, ignoré avec un avertissement) et à `eloignement` d'un
    config_carte v1 ; `plans` v1 seulement comparé à `plans_deduits`, jamais une surcharge. Repli cp1252 accepté
    (avertissement) pour les seuls fichiers écrits à la main.
27. Paramètres du moteur : `schemas/parametres_moteur.defaut.json` = annexe C modifiée par la partie D de l'avenant
    (`conjointe.essais_par_piece_mobile` = 6.0), types JSON compris (6 ≠ 6.0) ; la sortie de
    `placeur_routage parametres-defaut` doit lui être égale.

### Cartes KiCad

28. KiCad 8 (`(version 20240108)`) ; pièces dessous selon la convention de retournement de KiCad ; `.kicad_pro` à
    `net_settings.classes` (Default comprise), `netclass_patterns`, `netclass_assignments`.

## Limites connues

- KiCad n'est pas installé : les cartes ne sont vérifiées que par des contrôles syntaxiques (`test_corpus.py`), par
  le lecteur KiCad de `9b5b5a3` (lecture seule, sans exception) et, à l'intégration, par l'importeur du lot B.
- `validite.rs` est complet depuis l'étape 7 ; seule la position des violations entre deux objets reste provisoire
  (convention 34, jamais comparée).
