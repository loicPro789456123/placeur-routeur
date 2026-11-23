# Exemple METROID V2

Carte réelle à 4 couches (164 × 97 mm, 359 pièces, 259 réseaux, plan GND sur MidLayer1), deux canaux
d'alimentation répétés (`_ALIM1`, `_ALIM2`), deux canaux de mesure (`_VOLT1`, `_VOLT2`) et un hacheur
12 V → 8 V. Cet exemple sert à la CI (`campagne_metroid`, §7.7 du contrat) et aux tests de non-régression du lot B.

## Provenance

- `entree_carte.json`, `config_carte.json`, `donnees_placeur.json`, `deduction.json` et
  `donnees_placeur_9b5b5a3.json` sont **produits** par `regenerer.py` à partir de `PCB_METROID_V2_rev3.PcbDoc`
  (carte de base, sans routage) ; `references/rev4_fichier/` est extrait de `PCB_METROID_V2_rev4.PcbDoc`.
  Aucun `.PcbDoc` n'est publié ici ; `source` ne contient que des noms de fichiers.
- `contraintes.json` (contraintes/2, repère « carte ») : conversion de l'ancien `contraintes_carte.json` (v1) par
  `formats.lire_contraintes`, complétée des choix de l'utilisateur (§6.1) : trou central, éloignements, `roles`
  (`*Vref` sensible, `SEG*` peu sensible), charges R62_ALIM1.2 et R62_ALIM2.2 à 2 A, `couches.routage`,
  `couches.preferees` (+8V → MidLayer2), `fixer_suggestions: false`, `hauteur_inconnue: "basse"`.
- `contraintes_9b5b5a3.json` : `contraintes.json`, plus `boites` des 359 pièces (les `lbb` de l'ancien
  `donnees_placeur.json` de `9b5b5a3`, recopiées une fois avant sa suppression ; écart constaté 3,7·10⁻¹⁴ mm),
  `reseaux.<n>.priorite` 2 pour +12V et les 6 réseaux de CANAL_2A, 5 pour les 3 réseaux de TRIG_50R, et
  `groupes_exclus: ["_VOLT"]`. Fichier figé : il n'est pas régénéré.
- `references/rev3/`, `references/rev4/`, `references/ei_1038/` : **copies figées**, octet pour octet, de
  `sorties/` (rev3), `sorties/conjoint/` (rev4) et de l'essai `ei_1038` (`placements_routage.json`,
  `routage_detaille.json`, `vias_couture.json`, format 1, lu par compatibilité).

## Commande

Depuis `PCB_V2/outils/placeur_routage`, avec les cartes à la racine du dépôt :

    python -B exemples/metroid_v2/regenerer.py --cartes ../../..
    python -B exemples/metroid_v2/regenerer.py --cartes ../../.. --verifier

La première écrit les fichiers produits (calculés dans un dossier temporaire, puis copiés) ; deux exécutions
donnent des fichiers identiques (SHA-256). `--verifier` compare sans rien écrire : 0 si tout est identique, 1 sinon ;
2 si une carte manque ou si une étape échoue. Étapes : `importer_carte` (rev3) → `deduire_config` (avec
`contraintes.json`) → `construire_donnees` (nominal, avec `deduction.json`) → `construire_donnees` (avec
`contraintes_9b5b5a3.json`) → `importer_carte` (rev4, en temporaire) → `extraire_reference` (rev4).

## Contenu

| Fichier | Format | Contenu |
|---|---|---|
| `entree_carte.json` | entree_carte/2 | carte rev3 importée (§2.2) |
| `config_carte.json` | config_carte/2 | déduite : 1 convertisseur (M1, M2, U10, L1), L1 sans piste dessous |
| `donnees_placeur.json` | donnees_placeur/2 | 359 pièces, 45 fixes, 314 mobiles ; groupes répétés `_ALIM` (2 × 119) et `_VOLT` (2 × 18) |
| `deduction.json` | deduction/1 | rapport de déduction de la construction nominale |
| `donnees_placeur_9b5b5a3.json` | donnees_placeur/2 | données de compatibilité : boîtes de `9b5b5a3`, priorités 2 et 5, `_ALIM` seul |
| `references/rev4_fichier/` | placements/2, routage/2, vias_couture | extraction du fichier rev4 : 2452 pistes, 496 vias de routage, 512 vias de couture, 6 surfaces |

## Références et coûts connus

| Référence | Origine | Coût commun |
|---|---|---|
| `references/rev4/` | `sorties/conjoint/` (2421 pistes, 5393,0 mm, 470 vias, sans le cuivre du bloc) | **10328,5** (référence de qualité, citée par l'utilisateur) |
| `references/ei_1038/` | essai ei_1038 | **12398,4** |
| `references/rev3/` | `sorties/` | non publié (référence historique) |
| `references/rev4_fichier/` | `PCB_METROID_V2_rev4.PcbDoc` (cuivre manuel du hacheur compris) | **10622,6** en information (`mesures.py`, 23/11/2026 : 5440,6 mm, 496 vias, rétréci 237,6 mm) ; mêmes mesures : rev4 = 10494,1 et ei_1038 = 12577,8 (+1,6 % et +1,45 % sur les valeurs de 9b5b5a3, terme rétréci) |

La référence de qualité reste `references/rev4/` : le fichier rev4 contient aussi 6 régions de cuivre manuelles et
les vias de couture, que la copie de `sorties/conjoint/` n'a pas.

## Données de compatibilité (critère A7)

`donnees_placeur_9b5b5a3.json` pose au moteur **le même problème** que `9b5b5a3` : mêmes boîtes de placement, mêmes
priorités, mêmes groupes. Le critère A7 compare le moteur réécrit à `9b5b5a3` sur ces données (taux de validité et
coût commun médian, protocole de l'avenant 2.2) ; les données nominales sont mesurées et publiées à part.

## Écarts assumés avec `9b5b5a3` (§2.6, avenant 2.2 A.2)

Les données nominales diffèrent volontairement de `9b5b5a3` : (1) priorités ENTREE_12V et CANAL_2A de 2 à 1,
TRIG_50R de 5 à 4 ; (2) groupe répété `_VOLT` ajouté ; (3) boîtes des pièces recalculées par l'importeur (§2.2) ;
(4) éloignement `dure` devenu contrainte de légalité ; (5) isolements selon la priorité des règles Altium (piste +12V
contre pastille NetC19_1 d'U10 : 0,2 au lieu de 0,25). L'avenant ajoute (6) le compte d'échecs d'un réseau de plan et
(7) la règle d'acceptation d'un essai de la phase conjointe (comportement du moteur, pas des données). Les écarts
(1) à (3) sont neutralisés par les données de compatibilité ; (4) à (7) ne le sont pas.
