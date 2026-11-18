# Placeur-routeur : mode d'emploi

**Version 2.0.0, en cours de construction.** Ce document suit le plan imposé par le contrat du projet (§8.4-7).
Chaque section est rédigée quand les fonctions qu'elle décrit sont livrées et essayées ; une section marquée
« à rédiger » ne décrit encore rien d'utilisable. L'ancien mode d'emploi, qui décrivait l'outil précédent, est
retiré : plusieurs de ses affirmations étaient inexactes.

But de la version 2.0.0 : placer les composants d'un circuit imprimé et router ses pistes à partir d'une carte
Altium (`.PcbDoc`) ou KiCad (`.kicad_pcb`), sans Altium ni aucun autre logiciel de CAO ouvert, et produire une
carte du même format.

## 1 Démarrer en 5 minutes

1. Télécharger le zip `PlaceurRouteur-<version>.zip`, clic droit sur le zip, **Propriétés**, cocher
   **Débloquer**, **OK** (sans cela, Windows peut bloquer le programme ; section 7).
2. Clic droit, **Extraire tout…**, dans un dossier à vous (par exemple `Documents\PlaceurRouteur`) : le programme ne
   marche pas lancé depuis l'intérieur du zip. Lire `0_LIRE_AVANT.txt`.
3. Double-cliquer sur **`PlaceurRouteur.exe`** : la fenêtre s'ouvre (section 3.0).
4. **Essayer la carte d'exemple** : bouton **Parcourir…** de la ligne « Carte », choisir la carte du dossier
   `exemples\`, qualité **Aperçu**, puis **Lancer**. Le tableau se remplit, la barre arrive à 100 % et la ligne
   d'état affiche « Fin : code 0 ». **Ouvrir les résultats** montre le dossier créé (section 5).
5. **Sa propre carte** : la préparer (section 2), la fermer dans Altium, la choisir, cliquer **Analyser la carte**
   pour voir ce que le programme en comprend et préremplir « Pièces à garder en place », puis **Lancer**.

En ligne de commande, la même chose s'écrit `placeur.exe ma_carte.PcbDoc` (section 3.3) ; déposer une carte sur
l'icône de `placeur.exe` la lance aussi, et la fenêtre de console attend Entrée à la fin pour être lue.
Le paquet est construit et vérifié par l'intégration continue publique du projet (flux `paquet_windows`), sur
Windows Server 2022 et 2025 ; chaque version est essayée à la main dans Windows 11 et Windows 10 22H2 avant d'être
publiée. Systèmes annoncés : Windows 11 23H2 et suivants, Windows 10 22H2, x64 ; tout le reste est non vérifié.
*(à venir)* Ces étapes seront essayées à la lettre sur le premier paquet construit par l'intégration continue.

## 2 Préparer sa carte dans Altium ou KiCad

La carte d'entrée n'est jamais modifiée : le placeur-routeur en travaille une copie (`technique/entree/`) et écrit
une nouvelle carte à côté du rapport. Avant de lancer, vérifiez dans Altium ou KiCad :

| À vérifier | Si c'est absent |
|---|---|
| Un **contour** de carte fermé (Altium : forme de la carte ; KiCad : couche `Edge.Cuts`) | entrée refusée (code 2) |
| L'**empilage** : nombre de couches de cuivre ; les couches de plan déclarées comme telles (Altium : plan négatif `InternalPlane` ; KiCad : couche de type `power`) | une couche intérieure couverte de cuivre sans nom évocateur est routée comme une couche de signal et signalée « plan probable » à confirmer dans le rapport |
| Des **classes de réseaux avec une règle de largeur propre** pour les alimentations et la puissance | aucun réseau n'est traité en puissance ; le rapport l'écrit en alerte (« aucune règle de largeur propre ») |
| Les règles d'**isolement**, de **largeur** et de **vias** (diamètres, trous) | les valeurs par défaut du fichier servent ; une règle non interprétée est citée dans les avertissements du rapport, et le verdict ne la couvre pas |
| Les **connecteurs**, trous de fixation et pièces imposées **à leur place**, idéalement **verrouillés** | une pièce non verrouillée et non fixée par contrainte peut être déplacée ; les connecteurs posés dans le contour sont fixés d'office (réglage `fixer_suggestions`) |
| Au moins un **via** dans la carte (modèle de via) | la carte ne peut pas servir de gabarit : entrée refusée avant tout calcul (code 2) |
| La carte **fermée** dans Altium | « La carte est ouverte dans Altium et verrouillée. » (code 2) : fermez-la ou choisissez-en une copie |

Pour voir ce que le placeur-routeur comprend de la carte **sans lancer de calcul** :

```
placeur.exe ma_carte.PcbDoc --analyser
```

Il écrit `rapport.txt` dans un nouveau dossier de résultats : déductions (couches, masses, réseaux de puissance,
convertisseurs, pièces fixées), suggestions, alertes, mémoire et durée prévues. Aucun essai n'est lancé ; le code
de sortie vaut 0, ou 2 si la carte est refusée (le message dit pourquoi).

## 3 Lancer

Le calcul se lance depuis la fenêtre (section 3.0) ou en ligne de commande (section 3.3) ; les deux font la même
chose : la fenêtre ne calcule rien elle-même, elle lance `placeur.exe` et affiche ce qu'il annonce. Le calcul depuis
une carte demande les modules de lecture, de déduction et de production de carte des autres lots : tant qu'ils ne
sont pas livrés, il s'arrête avec le code 2 et un message « … impossible : … absent ». Les phrases marquées
*(à venir)* décrivent un comportement prévu, pas encore essayé.

### 3.0 La fenêtre

`PlaceurRouteur.exe` (paquet) ou, en mode développeur, `python lanceur.py --fenetre` ouvre un seul écran :

| Élément | Rôle |
|---|---|
| **Carte** (Parcourir…) | la carte `.PcbDoc` (filtre par défaut) ou `.kicad_pcb` ; un fichier `donnees_placeur.json` est aussi accepté (calcul sur données déjà construites, section 3.3). Pour une carte KiCad, la case « Carte produite » rappelle « .kicad_pcb, sans repositionnement de la sérigraphie » |
| **Contraintes** (Parcourir…, **Contraintes avancées…**) | fichier de contraintes facultatif (section 4) ; « Contraintes avancées… » crée le modèle `<carte>_contraintes.json` s'il manque (`--init`) et l'ouvre dans le bloc-notes |
| **Pièces à garder en place** | désignateurs séparés par des virgules, laissés à leur place ; prérempli par « Analyser la carte » (pièces verrouillées et suggestions) quand le champ est vide |
| **Dossier de sortie** | dossier parent des résultats ; vide : `<nom>_resultats` à côté de la carte |
| **Qualité** | Aperçu, Rapide, Normal ou Soigné (section 3.1) |
| **Durée maximale**, **Essais**, **Processeurs** | 0 : valeur du niveau (processeurs : tous sauf un) |
| Cases **Carte produite**, **Sérigraphie**, **Vias de couture**, **Ouvrir à la fin** | décocher pour ne pas produire la carte, ne pas placer la sérigraphie, ne pas ajouter de vias de couture, ne pas ouvrir le dossier à la fin |
| **Réglages avancés** | « S'arrêter après K graines valides » (0 : jamais) ; « Priorité normale » (le PC répond moins vite) |

Boutons :

- **Analyser la carte** : `--analyser` (section 2) ; le journal donne la durée prévue d'un essai et la durée
  maximale, sans rien calculer.
- **Lancer** : démarre le calcul. La ligne « Durée prévue » affiche, avant les essais, la durée prévue et sa borne
  (la durée maximale). Si un calcul inachevé existe pour la carte, la fenêtre propose d'abord de le reprendre.
- **Arrêter** agit en deux temps : premier clic « Finir les graines en cours » (plus de nouvel essai, les essais en
  cours finissent), second clic « Tout arrêter » (les essais s'arrêtent en une seconde environ). Le rapport est écrit
  dans les deux cas (section 3.4).
- **Reprendre** : actif quand un calcul inachevé et libre existe pour la carte et le dossier de sortie choisis.
- **Ouvrir les résultats** : ouvre le dossier du calcul dans l'Explorateur.
- **Enregistrer les paramètres** : écrit dans un `.ini` (par défaut `<nom de la carte>.ini`, à côté de la carte, lu
  automatiquement ensuite par le paquet) **seulement les réglages que vous avez changés** (section 3.2).
- Menu **Aide** : **Mode d'emploi** (ce document), **Vérifier l'installation** (`--auto-test`, section 8),
  **Copier le diagnostic** (`--diagnostic`, dans le presse-papiers, à joindre à une demande d'aide).

Pendant le calcul, un tableau donne par graine l'état (en_attente, lancee, terminee, abandonnee, plantee), la phase
en cours, la durée, le coût et les liaisons ouvertes ; la barre d'avancement indique la part des essais finis (en %),
le journal défile et « Temps restant estimé » est recalculé. À la fin, la ligne d'état affiche « Fin : code N »
(codes de la section 3.3) et la meilleure graine.

**Fermer la fenêtre pendant un calcul** demande : « Arrêter proprement » (les essais s'arrêtent, le rapport est
écrit, puis la fenêtre se ferme), « Tout arrêter » (tous les programmes du calcul sont arrêtés aussitôt et la
fenêtre se ferme ; le calcul reste reprenable) ou « Annuler » (rien ne change).

Si la fenêtre elle-même rencontre une erreur avant qu'un calcul ait commencé, elle en écrit le détail, sans chemin
ni nom personnel, dans `%LOCALAPPDATA%\PlaceurRouteur\erreurs.txt` : joindre ce fichier à une demande d'aide.

### 3.1 Niveaux de qualité

Le niveau fixe le nombre d'essais (graines) et la durée maximale du calcul. Chaque essai part d'un placement
différent ; le meilleur essai réussi est livré. Le niveau règle aussi l'effort de chaque essai (recuit, boucle,
phase conjointe), selon des valeurs que donne le moteur lui-même.

| Niveau | Essais | Durée maximale du calcul | Usage |
|---|---:|---:|---|
| `apercu` | 1 | 15 min | vérifier l'import et voir un premier résultat ; *(à venir)* la carte produite portera toujours la mention APERCU |
| `rapide` | 4 | 1 h | premier résultat utilisable |
| `normal` | 8 | 3 h | niveau par défaut |
| `soigne` | 16 | 8 h | carte difficile, calcul de nuit |

- `essais` et `duree_max_min` (section `[calcul]`) remplacent ces valeurs ; 0 garde celles du niveau.
- À la durée maximale, aucun nouvel essai n'est lancé ; les essais en cours finissent (section 3.4).
- Les noms se tapent sans accent ni majuscule obligatoires : `Soigné`, `soigne` et `SOIGNE` sont acceptés.

### 3.2 Fichier de paramètres `.ini`

Un fichier `.ini` se lit et se modifie avec le bloc-notes. Un modèle commenté, où toutes les valeurs sont vides
et où chaque commentaire donne la valeur par défaut, s'obtient par `python parametres.py --modele` (section 9) ;
*(à venir)* le paquet le contiendra sous le nom `parametres_modele.ini`, qui n'est jamais lu. En copier le contenu,
puis ne remplir que les réglages à changer.

**Où le mettre.** Du plus faible au plus fort, un réglage plus fort remplaçant un réglage plus faible :

1. les valeurs par défaut ;
2. `%APPDATA%\PlaceurRouteur\parametres.ini` : vos préférences, pour toutes les cartes ;
3. à côté de la carte, sous son nom (`ma_carte.ini` pour `ma_carte.PcbDoc`) : les réglages de cette carte ;
4. le fichier désigné par `--parametres F.ini` ;
5. les options de la ligne de commande et les choix de la fenêtre.

Les fichiers 2 et 3 ne sont lus que par `PlaceurRouteur.exe` et `placeur.exe`. Depuis le dépôt (mode
développeur, section 9) et dans l'intégration continue, aucun `.ini` n'est lu sans `--parametres`.

**Écriture.** Une ligne qui commence par `;` ou `#` est un commentaire ; un commentaire peut aussi finir une ligne
(` ; …` ou ` # …`, précédé d'une espace). Chaque réglage s'écrit `nom = valeur`, dans sa section (`[calcul]`…). Une
valeur vide ne change rien : le réglage vient alors d'un fichier plus faible, sinon de sa valeur par défaut. Ainsi,
un fichier de carte où seul `essais = 3` est rempli garde toutes vos préférences. Pour revenir à la valeur par
défaut (ou à celle du niveau) d'un réglage rempli dans un fichier plus faible, effacer la ligne de ce fichier-là.
Les formes suivantes sont acceptées :

| Type | Formes acceptées |
|---|---|
| oui ou non | `oui`, `non`, `vrai`, `faux`, `1`, `0` (majuscules permises) |
| entier | `8`, `100 000`, `1e8` (100 000 000), `2,5e7` |
| nombre | `2,5` ou `2.5` |
| liste | éléments séparés par des virgules : `U1, J1, R1*` |
| chemin | complet, ou relatif au dossier du `.ini` ; espaces et apostrophes permises ; guillemets permis autour |

Le fichier peut être enregistré en UTF-8 (avec ou sans BOM) ; un fichier en Windows-1252 est lu, et le rapport le
signale. Le programme écrit ses `.ini` en UTF-8 avec BOM et fins de ligne Windows, en gardant les commentaires.

**Erreurs.** Une valeur invalide arrête le programme avant tout calcul (code 2), avec un message qui nomme la
section, la clé et la valeur, suivi du fichier et de la ligne :

```
[calcul] essais : 'huit' n'est pas un nombre entier
(fichier ma_carte.ini, ligne 12)
```

Une clé ou une section inconnue n'arrête rien : elle est ignorée, et un avertissement propose le nom le plus proche
(« [calcul] essai : clé inconnue, ignorée ; vouliez-vous dire essais ? »). Un dossier, un fichier qui n'est pas du
texte, ou un fichier illisible arrête aussi le programme (code 2), avec un message en français.

**Réglages.**

| Section | Clé | Valeur | Défaut | Équivalent en ligne de commande |
|---|---|---|---|---|
| `[carte]` | `fichier` | chemin de la carte (`.PcbDoc` ou `.kicad_pcb`) | vide | `CARTE` |
| | `contraintes` | chemin du fichier de contraintes | vide | `--contraintes` |
| | `config_convertisseurs` | chemin de la description des convertisseurs | vide | `--config` |
| `[sortie]` | `dossier` | dossier où créer les résultats | vide : `<nom>_resultats` à côté de la carte | `--sortie` |
| | `ouvrir_a_la_fin` | oui ou non | oui | fenêtre |
| | `garder_graines` | `meilleure` ou `toutes` | `meilleure` | `--garder-graines` |
| `[calcul]` | `qualite` | `apercu`, `rapide`, `normal`, `soigne` | `normal` | `--qualite` |
| | `essais` | entier ; 0 : celui du niveau | 0 | `--essais` |
| | `duree_max_min` | minutes ; 0 : celle du niveau | 0 | `--duree-max-min` |
| | `valides_voulues` | arrêt des lancements après ce nombre d'essais réussis ; 0 : tous | 0 | `--valides-voulues` |
| | `graine_depart` | numéro de la première graine | 1 | `--graine-depart` |
| | `processeurs` | entier ; 0 : tous sauf un | 0 | `--processeurs` |
| | `priorite_normale` | oui ou non | non (priorité basse) | `--priorite-normale` |
| `[pcbdoc]` | `produire` | produire la carte (`.PcbDoc` ou `.kicad_pcb`) | oui | `--sans-pcbdoc` |
| | `produire_si_invalide` | sans essai réussi, produire la meilleure carte « à terminer » | oui | |
| | `serigraphie` | placer la sérigraphie | oui | `--sans-serigraphie` |
| | `vias_couture` | ajouter des vias de couture | oui | `--sans-couture` |
| `[contraintes]` | `fixes`, `liberer`, `dessus_seulement`, `dessous_seulement` | listes | vide | ajoutées aux listes du fichier de contraintes |
| | `faces_par_defaut` | `auto`, `libres`, `actuelle` | celui du fichier de contraintes | remplace sa valeur |
| | `fixer_suggestions`, `fixer_verrouilles` | oui ou non | idem | idem |
| | `hauteur_inconnue` | `face_entree` ou `basse` | idem | idem |
| | `marge_bord_mm`, `garde_bord_mm`, `marge_decoupe_mm`, `garde_decoupe_mm`, `pas_fin_mm`, `grille_placement_mm` | millimètres | idem | idem |
| `[avance]` | `parametres_moteur` | chemin d'un fichier `parametres_moteur.json` | vide | `--parametres-moteur` |
| | `conjointe_essais_par_piece`, `boucle`, `mouvements`, `iters_det`, `duree_max_graine_min`, `fils_par_essai` | nombres ; vide : valeur du niveau | vide | réglages du moteur |

Une valeur de `[contraintes]` qui remplace celle du fichier de contraintes est signalée dans le rapport. Les
longueurs en `_mm` sont toujours en millimètres, même si le fichier de contraintes est écrit en mils.

**Réglages du moteur** (`[avance]`, pour spécialistes). Du plus faible au plus fort : valeurs du niveau, fichier
`parametres_moteur.json`, clés de `[avance]`, choix de la fenêtre. Le fichier `parametres_moteur.json` ne contient
que les clés à changer, rangées par section (`{"boucle": {"tours": 2}}`) ; une clé inconnue arrête le programme
avec le nom le plus proche (« routage.espacment : clé inconnue ; vouliez-vous dire espacement ? ») ; une clé qui
commence par `_` est un commentaire. Correspondances : `conjointe_essais_par_piece` →
`conjointe.essais_par_piece_mobile`, `boucle` → `boucle.tours`, `mouvements` → `recuit.mouvements_max`, `iters_det`
→ `routage.iters_negociation`, `duree_max_graine_min` → `campagne.duree_max_s` (en minutes), `fils_par_essai` →
fils de calcul de chaque essai (1 conseillé : le résultat n'en dépend pas).

**Ce qui a servi.** Chaque calcul écrit `technique/parametres_utilises.ini` : toutes les valeurs effectives, les
fichiers désignés par leur seul nom et `.` pour le dossier du calcul.

### 3.3 Ligne de commande

```
python placeur.py --donnees donnees_placeur.json [options]
python placeur.py --version
```

`--donnees` lance le calcul sur des données déjà construites (`donnees_placeur.json`, format
`donnees_placeur/2`) : c'est le mode de l'intégration continue. Il n'importe aucune carte et ne produit aucune carte ;
il calcule les essais, les contrôle et écrit le rapport. `placeur.exe CARTE` (ou `python lanceur.py CARTE` en mode
développeur) fait la chaîne complète depuis une carte ; `--init` et `--analyser` sont décrits en section 2,
`--reprendre` en section 3.4, `--auto-test` et `--diagnostic` en section 8. Les fichiers `.ini` implicites
(préférences, `<nom de la carte>.ini`) ne sont lus que par `placeur.exe` et `PlaceurRouteur.exe`, jamais en mode
développeur.

| Option | Effet |
|---|---|
| `--sortie PARENT` | dossier parent des résultats (défaut : `<nom des données>_resultats` à côté des données) |
| `--parametres F.ini` | fichier de paramètres (section 3.2) |
| `--qualite N` | niveau : `apercu`, `rapide`, `normal` (défaut) ou `soigne` |
| `--essais N` | nombre d'essais ; `--essais 0` : aucun essai (préparation et estimations seulement, sans construire le moteur) |
| `--graine-depart G` | premier numéro d'essai (défaut 1) ; les essais sont G, G+1, … |
| `--processeurs P` | essais calculés en même temps, au plus (défaut : processeurs logiques − 1) |
| `--duree-max-min M` | durée maximale du calcul en minutes (défaut : celle du niveau) |
| `--valides-voulues K` | plus de nouvel essai après K essais réussis (défaut 0 : tous les essais) |
| `--parametres-moteur M.json` | réglages du moteur (fichier partiel, section 3.2) |
| `--moteurs DOSSIER` | dossier qui contient `placeur_routage(.exe)` |
| `--priorite-normale` | moteurs en priorité normale (défaut : « inférieure à la normale », le PC reste utilisable) |
| `--garder-graines toutes` | garder tous les fichiers de tous les essais (défaut `meilleure`, section 5) |
| `--evenements` | lignes `EVT {json}` sur la sortie standard, pour un programme qui suit le calcul |

Chaque calcul crée un **nouveau** dossier `<parent>/<nom>_<AAAAMMJJ-HHMMSS>/` ; un dossier existant n'est jamais
réutilisé (deux calculs lancés dans la même seconde : le second prend la seconde suivante). Le dossier est verrouillé
(`.verrou`) pendant le calcul : un second programme qui veut y écrire s'arrête avec « Un calcul est déjà en cours
dans ce dossier. » (code 2).

**Codes de sortie** : 0 au moins un essai réussi ; 1 aucun ; 2 entrée ou environnement invalide (message sur la
sortie d'erreur) ; 3 calcul arrêté à la demande ; 4 erreur interne du programme (détail dans
`technique/erreur.txt`, à joindre à une demande d'aide).

### 3.4 Arrêter un calcul

- **Arrêter les lancements** : créer un fichier vide `arret_lancements` dans le dossier du calcul. Aucun nouvel essai
  n'est lancé, les essais en cours finissent, puis le rapport est écrit. Code 3.
- **Arrêter tout de suite** : créer un fichier vide `arret_demande` dans le dossier du calcul, ou taper Ctrl+C. Les
  moteurs écrivent leur état et s'arrêtent en une seconde environ ; les essais arrêtés sont contrôlés et le rapport
  est écrit. Code 3. Un second Ctrl+C tue aussitôt tous les moteurs.
- **Durée maximale** (`--duree-max-min`, ou celle du niveau) : passé ce délai, plus aucun essai n'est lancé ; un essai
  n'est pas lancé non plus s'il ne peut pas finir avant l'échéance, d'après la durée moyenne des essais déjà finis.
  Le calcul se termine normalement (code 0 ou 1).
- **Essais réussis voulus** (`--valides-voulues K`) : après K essais réussis, plus aucun essai n'est lancé ; le calcul
  se termine normalement.
- Les fichiers d'arrêt d'un calcul précédent sont effacés au démarrage (et notés au journal).
- Si le programme lui-même est arrêté de force, Windows arrête aussi ses moteurs.
- **Reprendre** un calcul interrompu (arrêt, fermeture, PC éteint) : `placeur.exe --reprendre DOSSIER`, ou
  `--reprendre` seul (dernier calcul inachevé de la carte), ou le bouton **Reprendre** de la fenêtre. Les essais
  finis sont gardés, les autres relancés ; si la carte, les contraintes ou les paramètres ont changé, la reprise est
  refusée : « les entrées ont changé : lancer un nouveau calcul ».

### 3.5 Durées et ressources de la machine

- Avant les essais, le programme mesure la vitesse du PC (`etalonner`, environ 10 s) et la taille du problème
  (`estimer`), puis écrit au journal la durée prévue et la durée maximale. Après chaque essai, la prévision est
  corrigée par la durée mesurée ; les deux sont dans `technique/rapport.json` (section 5). Une durée qui ne peut pas
  être prévue est annoncée « inconnue ».
- Chaque essai utilise un seul fil de calcul (le résultat n'en dépend pas) ; plusieurs essais tournent en même
  temps : au plus `--processeurs`, et au plus ce que la mémoire libre permet (500 Mo gardés pour le système ; la
  mémoire d'un essai est d'abord estimée, puis remplacée par celle mesurée sur le premier essai fini). Si un seul
  essai ne tient pas en mémoire, le calcul refuse de démarrer : « La carte demande environ n Mo par essai, m Mo sont
  libres. »
- Plusieurs calculs lancés en même temps sur le même PC se partagent les places : le nombre total de moteurs ne
  dépasse pas le plus grand `--processeurs` demandé.
- Les moteurs tournent en priorité « inférieure à la normale », et la mise en veille du PC est empêchée pendant le
  calcul.
- Chaque essai a un plafond de durée (réglage du moteur `campagne.duree_max_s`), allongé sur un PC plus lent que le
  PC de référence ; un essai qui l'atteint est signalé dans les avertissements du rapport.
- *(à venir)* Durées mesurées sur des PC de 4 et 8 fils, configuration minimale et systèmes essayés.

## 4 Écrire des contraintes par l'exemple

**Point de départ** : `placeur.exe --init ma_carte.PcbDoc` écrit, à côté de la carte, `ma_carte.ini` (réglages,
section `[contraintes]` préremplie avec les pièces verrouillées et les connecteurs suggérés) et
`ma_carte_contraintes.json` (toutes les clés avec leur valeur par défaut ; `_aide` explique chaque clé ; `_positions`
donne la position actuelle de chaque pièce, sans effet). Un fichier qui existe déjà n'est jamais écrasé : relancer
`--init` ne change rien.

**Repère et unité** : par défaut (`"repere": "fichier"`, `"unite": "mm"`), les coordonnées sont celles qu'affiche
votre logiciel : Altium, coordonnées relatives à l'origine de la carte, y vers le haut (passez Altium en mm, ou
écrivez `"unite": "mil"`) ; KiCad, coordonnées de la page en mm, y vers le **bas**.

Dix cas types (à mettre dans `ma_carte_contraintes.json`, en gardant `"format": "contraintes/2"`) :

| Besoin | Contrainte |
|---|---|
| garder des pièces à leur place | `"fixes": ["J1", "J2", "H*"]` (ou, dans le `.ini` : `fixes = J1, J2, H*`) |
| rendre mobile un connecteur fixé d'office | `"liberer": ["J3"]` |
| poser une pièce fixe ailleurs | `"fixes_deplaces": {"J4": {"x": 12.5, "y": 30.0, "rotation": 90, "face": "dessus"}}` |
| interdire le dessous à des pièces | `"dessus_seulement": ["U1", "nature:connecteur"]` |
| garder un bloc dans une zone | `"zones_imposees": [{"pieces": ["U5", "C5*"], "faces": ["dessus"], "rect": [10, 10, 40, 30], "etiquette": "alim"}]` |
| interdire une zone aux pistes et aux vias | `"zones_interdites": [{"faces": ["dessus", "dessous"], "rect": [0, 0, 8, 8], "effets": ["pistes", "vias"], "couches": ["toutes"], "etiquette": "antenne"}]` |
| imposer le rôle puissance à des réseaux | `"roles": {"puissance": ["+12V", "VBAT*"]}` |
| déclarer un plan | `"couches": {"plans": {"MidLayer1": "GND"}}` |
| ajouter un trou absent du fichier | `"trous_supplementaires": [{"x": 82.0, "y": 49.5, "diametre": 3.2, "diametre_cuivre": 6.35, "metallise": true}]` |
| éloigner des pièces d'une autre | `"eloignements": [{"type": "dure", "objets": ["U25"], "reference": ["M1"], "distance_mm": 30.0}]` |

Le fichier est lu strictement : une clé mal écrite est refusée avec la clé la plus proche. Les valeurs de la section
`[contraintes]` du `.ini` complètent ce fichier (listes ajoutées ; une valeur simple remplace celle du fichier, et le
rapport le signale) ; le résultat de la fusion est écrit dans `technique/contraintes_effectives.json`.

## 5 Lire le rapport

**`rapport.txt`** (à la racine du dossier de résultats ; s'ouvre avec le bloc-notes), dans cet ordre :

1. **Avertissements** : motifs sans effet, clés `.ini` inconnues, pièces de hauteur inconnue, règles non
   interprétées (le verdict ne les couvre pas), déductions à confirmer, vias de couture retirés, carte produite en
   échec.
2. **Déductions** : couches et plans, masses, réseaux de puissance et d'alimentation, convertisseurs, pièces fixées
   et pourquoi (règle et preuve chiffrée), puis les suggestions.
3. **Alerte** « aucune règle de largeur propre : aucun réseau traité en puissance », quand c'est le cas.
4. **Graines** : une ligne par essai (réussi ou non, coût commun, liaisons ouvertes, durée).
5. **Graine retenue**, et une ligne qui explique le coût commun.
6. **Comparaison** : « routage existant : coût X ; meilleur résultat : Y » si la carte d'entrée était déjà routée.
7. **Livrable** : nom de la carte produite et son type.
8. **Liaisons à finir à la main** (livrable « à terminer » ou « aperçu ») : réseau, pastilles, position en mm.
9. **À faire dans Altium** ou **dans KiCad** (section 6).

**Livrables** (à la racine) :

| Fichier | Quand | Code |
|---|---|---|
| `<nom>_g<G>.PcbDoc` ou `<nom>_g<G>.kicad_pcb` | au moins un essai réussi ; la carte produite passe ses contrôles | 0 |
| `<nom>_A_TERMINER_g<G>.*` | aucun essai réussi (réglage `produire_si_invalide = oui`, défaut) : l'essai sans violation qui a le moins de liaisons ouvertes ; les liaisons à finir sont listées | 1 |
| `<nom>_APERCU_g<G>.*` | niveau `apercu`, même pour un essai réussi | 0 ou 1 |
| aucun | `--sans-pcbdoc`, ou carte produite en échec pour 3 essais réussis de suite (l'essai réussi suivant est essayé à chaque échec) | 0 ou 1 |

`--sans-serigraphie` produit la carte sans replacer les désignateurs ; `--sans-couture` sans vias de couture. Les
vias de couture qui enfreignent une règle sont retirés avant la production de la carte, et le rapport le signale.

**Dossier d'un calcul** :

```
<nom>_<AAAAMMJJ-HHMMSS>/
  <nom>_g<G>.PcbDoc | .kicad_pcb    carte produite (variantes _A_TERMINER_, _APERCU_)
  rapport.txt                  rapport pour l'utilisateur (ci-dessus)
  .verrou                      présent pendant le calcul seulement
  arret_lancements, arret_demande   fichiers d'arrêt (section 3.4), s'ils ont servi
  technique/
    rapport.json               état du calcul, réécrit après chaque essai
    journal.txt                journal horodaté
    parametres_utilises.ini    réglages effectifs (section 3.2)
    parametres_moteur.json     réglages complets passés au moteur
    contraintes_effectives.json   contraintes du fichier et du .ini réunies (section 4)
    entree/<carte>             copie de la carte d'entrée (l'original n'est jamais modifié)
    entree_carte.json, controle_gabarit.json, config_carte.json, deduction.json   lecture et déductions
    donnees_placeur.json       données calculées
    config_pcbdoc.json, carte_<G>_rapport.json   réglages et compte rendu de la carte produite
    reference_existante/       routage de la carte d'entrée et ses mesures (si elle était routée)
    erreur.txt                 seulement après une erreur interne (code 4)
    graine_<G>/                un dossier par essai : sorties du moteur (stats.json, placement, routage, aperçu
                               .svg, console.txt) et contrôles indépendants (controle_placement.json,
                               drc_strict.json, drc_altium.json, mesures.json, proprete.json, convertisseurs.json) ;
                               pour l'essai de la carte produite : vias_couture.json, drc_couture.json
```

Avec `garder_graines = meilleure` (défaut), à la fin d'un calcul terminé, les fichiers lourds des essais autres que
l'essai retenu et l'essai « à terminer » sont supprimés (placement, routage, aperçu, console) ; leur `stats.json` et
leurs contrôles restent. Un calcul arrêté garde tout.

**`technique/rapport.json`** (format `rapport/2`). Il est réécrit après chaque essai : après un arrêt ou une panne,
il décrit les essais déjà faits.

| Champ | Contenu |
|---|---|
| `termine`, `fin`, `code` | calcul terminé ; raison de la fin (`normale`, `arret_demande`, `duree_max`, `valides_voulues`) ; code de sortie |
| `erreur` | message d'une entrée refusée (code 2) ou d'une erreur interne (code 4), sinon vide |
| `parametres` | `effectifs` : paramètres complets passés au moteur ; `utilisateur` : vos réglages (`.ini` et options) ; niveau, fichiers `.ini` lus, clés `[avance]` actives |
| `estimation` | mémoire estimée et mesurée, mémoire libre, durée prévue, durée maximale, durée mesurée d'un essai, prévision corrigée, essais en parallèle, plafond par essai, vitesse du PC |
| `graines` | une ligne par essai lancé (voir ci-dessous) |
| `graines_non_lancees` | essais prévus mais pas lancés (arrêt, durée maximale, essais réussis voulus) |
| `meilleure` | essai retenu : l'essai réussi de plus petit coût commun |
| `a_terminer` | sans essai réussi : l'essai sans violation qui a le moins de liaisons ouvertes (puis le plus petit coût) |
| `incoherences` | nombre d'essais que le moteur déclare réussis et que les contrôles indépendants refusent |
| `statistiques` | essais mesurés et réussis, taux de réussite et son intervalle de confiance à 95 % (Wilson), meilleur coût et coût médian des essais réussis, temps de calcul total et par essai réussi |
| `avertissements`, `duree_totale_s` | avertissements du calcul ; durée réelle |

Une ligne de `graines` : `graine`, `dossier`, `code` du moteur, `fin` (raison de la fin donnée par le moteur),
`etat` (`terminee`, `abandonnee`, `plantee`), `texte`, `plantage`, `duree_s`, `valide_moteur` (verdict du moteur),
`placement`, `drc_strict`, `drc_altium` (verdicts des contrôles indépendants : `valide`, liaisons `ouvertes`,
`violations`), `valide` (verdict final), `cout_commun`, `incoherence`, `temps_calcul_s`, `memoire_pic_mo`,
`plafond_temps_atteint`.

Un essai est **réussi** (`valide`) seulement si le moteur s'est terminé normalement ou sur un arrêt demandé, si son
`stats.json` vient bien de ce lancement, et si le moteur et les trois contrôles indépendants (placement, règles
strictes, règles d'Altium) le déclarent tous valide. Un moteur qui plante (`plantee`) ou qui signale une erreur
interne (`abandonnee`) n'arrête pas les autres essais ; un moteur qui refuse les données (code 2) arrête tout le
calcul, puisque tous les essais seraient refusés de même.

**Glossaire**

- **Essai, graine** : un calcul complet de placement et de routage, depuis un placement de départ tiré au hasard ; le
  numéro de la graine fixe ce tirage, et le même numéro redonne le même résultat.
- **Coût commun** : note d'un routage, la même pour tous les programmes du projet (plus elle est basse, mieux c'est) :
  longueur des pistes, vias, pistes rétrécies, résistance des pistes de puissance, coudes, écartement.
- **Liaison ouverte** : deux pastilles d'un même réseau qui ne sont pas reliées par du cuivre.
- **Violation** : une règle de la carte non respectée (isolement, largeur, trou…) ; un essai qui en a une n'est pas
  réussi.
- **Incohérence** : le moteur déclare un essai réussi, et un contrôle indépendant le refuse ; elle est signalée et
  l'essai n'est pas retenu.
- **Vias de couture** : vias ajoutés entre les surfaces d'une masse, sur les différentes couches.
- **Plan** : couche intérieure de cuivre plein dédiée à une masse ou à une alimentation.

## 6 Finir dans Altium ou KiCad

La carte produite garde le contour, les polygones, les textes et les empreintes de la carte d'entrée ; les pistes et
vias libres de l'entrée sont remplacés par le nouveau routage. Les polygones et zones doivent être recoulés.

**Altium** :

1. Ajouter `<nom>_g<G>.PcbDoc` au projet (*clic droit sur le projet > Ajouter un fichier existant*), à la place de
   l'ancienne carte si vous la retenez.
2. *Outils > Polygones > Recouler tout*.
3. *Outils > Contrôle des règles de conception (DRC)* et lire le compte rendu.
4. Pour une carte « à terminer » : router à la main les liaisons listées dans `rapport.txt` (réseau, pastilles,
   position), puis refaire 2 et 3.

**KiCad** :

1. Ouvrir `<nom>_g<G>.kicad_pcb` dans l'éditeur de circuits imprimés.
2. Remplir les zones (touche **B**).
3. *Inspecter > Contrôle des règles de conception (DRC)*.
4. Carte « à terminer » : comme pour Altium.

*(à venir)* Versions d'Altium et de KiCad essayées : elles seront listées ici après les essais de la carte de
démonstration (étape É9).

## 7 Problèmes et solutions

Avant tout calcul, le placeur contrôle la carte, le dossier de sortie, le moteur et la mémoire. Un refus rend le
code 2 avec l'un des messages ci-dessous (dans la fenêtre, sur la sortie d'erreur et dans `technique/journal.txt`) ;
les deux derniers ne sont que des avertissements : le calcul continue.

| Message ou symptôme | Cause | Que faire |
|---|---|---|
| « La carte est ouverte dans Altium et verrouillée. » | Altium (ou un autre programme) tient la carte ouverte sans partage | fermer la carte dans Altium, ou choisir une copie de la carte |
| « Espace disque insuffisant (n Go libres). » | moins de 1 Go libre au lancement, ou disque plein pendant le calcul | libérer de la place, ou choisir un dossier de sortie sur un autre disque |
| « Windows interdit l'écriture dans ce dossier. » | droits insuffisants, ou « Accès contrôlé aux dossiers » de la Sécurité Windows | choisir un autre dossier de sortie, ou autoriser `placeur.exe` dans « Accès contrôlé aux dossiers » |
| « Chemin de sortie trop long (n caractères). » | plus de 240 caractères : refus ; plus de 200 : avertissement | choisir un dossier plus court (par exemple `Documents\PlaceurRouteur`) |
| « Moteur absent, peut-être mis en quarantaine par l'antivirus. » | `_internal\moteurs\placeur_routage.exe` ou `serigraphie.exe` manque ou ne peut pas être lancé | restaurer le fichier depuis la quarantaine de l'antivirus (ou extraire le zip de nouveau), puis signaler le faux positif à l'éditeur de l'antivirus ; `placeur.exe --auto-test` doit ensuite réussir |
| « La carte demande environ n Mo par essai, m Mo sont libres. » | mémoire libre insuffisante pour un seul essai | fermer des programmes ; réduire `processeurs` ne suffit pas si un seul essai ne tient pas |
| « Un calcul est déjà en cours dans ce dossier. » | un autre calcul tient le verrou du dossier (`.verrou`) | attendre la fin de ce calcul, ou choisir un autre dossier |
| « les entrées ont changé : lancer un nouveau calcul » | reprise (`--reprendre`) d'un calcul dont la carte, les contraintes ou les paramètres ont changé depuis | lancer un nouveau calcul (le dossier repris est laissé tel quel) |
| « Version du moteur … différente de celle du lanceur … » | fichiers de deux versions du paquet mélangés | extraire de nouveau le zip complet dans un dossier vide |
| « Ce dossier est synchronisé : les résultats seront envoyés en ligne. » (avertissement) | dossier de sortie sous OneDrive | choisir un dossier local si les cartes ne doivent pas quitter le PC |
| « Le PC est sur batterie. » (avertissement) | PC portable débranché : le calcul sera plus lent et videra la batterie | brancher le PC |
| Windows affiche « Windows a protégé votre ordinateur » (SmartScreen) au premier lancement | le paquet n'est pas signé par un éditeur connu | vérifier l'empreinte SHA-256 du zip (section 8), puis « Informations complémentaires » → « Exécuter quand même » |
| « Contrôle intelligent des applications » bloque `placeur.exe` | ce mode de Windows 11 bloque les programmes non signés, sans bouton pour passer outre | lancer le mode source (section 9), ou désactiver le Contrôle intelligent des applications (Sécurité Windows → Contrôle des applications et du navigateur) ; il ne peut pas être réactivé ensuite sans réinitialiser Windows |
| `placeur.exe` ne démarre pas, ou « fichier introuvable » dans `_internal` | le programme a été lancé depuis le zip sans l'extraire | clic droit sur le zip → « Extraire tout… », puis lancer `placeur.exe` dans le dossier extrait |
| Un calcul a été interrompu (PC éteint, fenêtre fermée) | le dossier du calcul porte un `technique\rapport.json` inachevé | relancer avec `--reprendre DOSSIER` (ou `--reprendre` seul : dernier calcul inachevé de la carte) ; la fenêtre le propose d'elle-même ; les graines finies sont gardées, les autres relancées |

**Antivirus et faux positif.** Le paquet n'est pas signé ; un antivirus peut le prendre à tort pour un logiciel
malveillant (« faux positif »). Symptômes : `placeur.exe` ou un moteur disparaît après l'extraction, ou
`--auto-test` écrit « Moteur absent, peut-être mis en quarantaine par l'antivirus. ». Que faire :

1. vérifier l'empreinte SHA-256 du zip (section 8) : si elle est égale à celle publiée, le fichier est bien celui
   construit par l'intégration continue publique du projet ;
2. restaurer le fichier depuis la quarantaine (Sécurité Windows → Protection contre les virus et menaces →
   Historique de protection → Actions → Restaurer), ou extraire de nouveau le zip dans un dossier vide ;
3. signaler le faux positif à l'éditeur de l'antivirus (pour Microsoft Defender : page « Submit a file for malware
   analysis » du site de sécurité de Microsoft, choix « Incorrectly detected as malware ») en joignant le zip ;
4. relancer `placeur.exe --auto-test`, qui doit réussir.

Si Microsoft Defender détecte le paquet et que le signalement n'aboutit pas sous deux semaines, le projet change de
forme de paquet (Python embarqué officiel, signé par la Python Software Foundation, avec un petit lanceur) ; en
attendant, le mode source (section 9) marche sans aucun exécutable du paquet.

Le dossier de sortie par défaut est `<dossier de la carte>\<nom>_resultats\` ; s'il n'est pas inscriptible, le
placeur prend `Documents\PlaceurRouteur\`, puis `%LOCALAPPDATA%\PlaceurRouteur\resultats\`. Il n'écrit jamais
de résultats dans le dossier du paquet ni dans le dossier temporaire de Windows.

## 8 Mettre à jour, déplacer, désinstaller

- **Vérifier le zip** avant de l'extraire : l'empreinte SHA-256 est publiée à côté de chaque version, dans le
  fichier `PlaceurRouteur-<version>.zip.sha256`. Dans PowerShell, dans le dossier du téléchargement :
  `Get-FileHash .\PlaceurRouteur-<version>.zip -Algorithm SHA256` (remplacer `<version>`, par exemple `2.0.0`).
  Les deux empreintes doivent être identiques ; sinon, supprimer le zip et le télécharger de nouveau.
- **Mettre à jour** : extraire le nouveau zip dans un dossier vide, puis supprimer l'ancien dossier du paquet. Ne
  jamais copier une nouvelle version par-dessus une ancienne (le moteur et le lanceur doivent venir du même paquet).
  Les résultats déjà calculés ne sont pas dans le dossier du paquet : ils sont gardés.
- **Déplacer** : le dossier du paquet peut être déplacé ou renommé tel quel ; il ne dépend d'aucun chemin fixe.
- **Désinstaller** : supprimer le dossier du paquet. Le placeur n'écrit rien d'autre que les dossiers de résultats
  et, s'ils existent, `%APPDATA%\PlaceurRouteur` (réglages de la fenêtre) et `%LOCALAPPDATA%\PlaceurRouteur`
  (résultats de repli) ; les supprimer aussi pour tout effacer. Rien n'est inscrit dans le registre.
- **Vérifier l'installation** : `placeur.exe --auto-test` contrôle les moteurs (présents, version égale à celle du
  lanceur), les outils et un dossier inscriptible ; code 0 si tout va bien, sinon chaque échec est écrit.
- **Demander de l'aide** : joindre la sortie de `placeur.exe --diagnostic` (version, système, Python, moteurs,
  mémoire, processeurs, fichiers `.ini` lus, espace libre). Elle est anonymisée : ni nom d'utilisateur, ni nom de la
  machine, ni chemin complet. Joindre aussi `rapport.txt` et `technique\journal.txt` du calcul en cause, eux aussi
  anonymisés (le dossier du calcul y est écrit « . », le profil `%USERPROFILE%`). En mode source :
  `python placeur.py --diagnostic`.

## 9 Mode développeur

Cette section décrit l'usage depuis le dépôt (« mode source ») : tests, intégration continue et campagnes de
mesure.

- **Prérequis** : Python 3.10 ou plus récent (essayé avec 3.12) et le module `olefile`, installé par
  `python -m pip install -r requirements.txt` depuis `PCB_V2/outils/placeur_routage`. La version 2 ne demande
  aucune autre bibliothèque Python. Rust (`cargo`) sert à construire les deux moteurs.
- **Version** : le fichier `PCB_V2/outils/VERSION` est la source unique du numéro de version ; la version complète
  ajoute les 7 premiers caractères du commit (`2.0.0+<commit>`).
- **Variables d'environnement** : `chemins.py` n'en lit aucune pour régler son comportement, et les programmes
  qu'il lance ne reçoivent qu'une liste réduite de variables du système (emplacements, chemin de recherche).
- **Moteurs et chaîne en mode source** : `placeur.py CARTE` lance lui-même `cargo build --release --offline`
  dans `placeur_routage/` (moteur `placeur_routage`) avant les graines ; `--auto-test` et `--version` ne lancent
  jamais cargo. Pour construire d'avance les deux moteurs puis essayer la chaîne sur une carte du corpus :

  ```
  cd PCB_V2/outils/placeur_routage
  cargo build --release --offline
  cd ../serigraphie
  cargo build --release --offline
  cd ../placeur_routage
  python placeur.py --auto-test
  python placeur.py corpus/cartes/deux_couches/deux_couches.kicad_pcb --qualite rapide --essais 2 --processeurs 2
  ```

  Le résultat est écrit dans `corpus/cartes/deux_couches/deux_couches_resultats/<nom>_<date>/` (section 5) ;
  si le dépôt est dans le dossier temporaire, dans `Documents\PlaceurRouteur\` (section 3, `--sortie`).
  Dans un dépôt git, des moteurs construits à la main affichent `2.0.0+inconnu` alors que le lanceur affiche
  `2.0.0+<commit>` : `--auto-test` signale la différence, et `placeur.py CARTE` les reconstruit avec la bonne
  version. Hors dépôt git (copie par `git archive`), les deux affichent `2.0.0+inconnu`.
- **Tests de `chemins.py`** (sous-processus, objet de travail, verrou, sémaphore, écritures atomiques, anonymat) :

  ```
  cd PCB_V2/outils/placeur_routage
  python -m unittest test_lanceur -v
  ```
- **Vérifier un fichier `.ini`** sans rien calculer : `python parametres.py --verifier F.ini` affiche les valeurs
  lues et les avertissements (code 0), ou le message de l'erreur (code 2). `python parametres.py --modele` écrit le
  modèle commenté sur la sortie standard.
- **Tests des paramètres** (lecture et écriture des `.ini`, priorités, niveaux et paramètres du moteur,
  `[contraintes]`, durées prévues) :

  ```
  cd PCB_V2/outils/placeur_routage
  python -m unittest test_parametres -v
  ```

- **Faux moteur** : `test_placeur.py` contient un faux moteur, qui suit la ligne de commande et les formats du
  vrai (`python test_placeur.py --faux-moteur campagne --donnees F --sortie D --graine N …`), pour essayer
  l'orchestrateur sans attendre de vraies graines. Le scénario de chaque graine (valide, invalide, lent, arrêt,
  plantage, erreur interne, entrée invalide, mémoire, calcul) est lu dans la clé `_faux_scenario` des données. Ses
  essais : `python -m unittest test_placeur.TestFauxMoteur -v`.
- **Tests de l'orchestrateur** (`placeur.py --donnees`, avec le faux moteur et des doublures des contrôles
  indépendants ; environ 1 min) : codes de sortie, plantages, arrêts et Ctrl+C, verrou, durée maximale, essais en
  parallèle, priorité, sémaphore entre deux calculs, incohérence, choix de l'essai, `rapport.json` et ses
  statistiques, évènements :

  ```
  cd PCB_V2/outils/placeur_routage
  python -m unittest test_placeur -v
  ```
- **Contrôles indépendants** : tant que les modules de contrôle n'exposent pas leurs fonctions (lot C), un vrai
  calcul note chaque contrôle « refusé » (jamais réussi par défaut) : aucun essai n'est alors réussi.
- **Tous les tests** (comme le job `python` de l'intégration continue) :

  ```
  cd PCB_V2/outils/placeur_routage
  python -m unittest discover -s . -p "test_*.py" -v
  ```
- **Intégration continue** (`.github/workflows/placeur.yml`, sur le dépôt public) :
  - à chaque poussée sur `PCB_V2/outils/**` : `rust` (Ubuntu 24.04 et Windows Server 2022, `cargo test` et
    `cargo build --release --locked` des deux moteurs), `python` (tous les tests, avec olefile), `contrat`
    (`corpus/comparer_validite.py`), `synthetiques` (`placeur.py` sur chaque carte « chaîne » du corpus,
    `--qualite rapide --essais 2`, puis `python ci_graines.py controler SORTIE` : au moins une graine valide, aucune
    incohérence, `.kicad_pcb` produit et relu) et `arret_posix` (arrêt du groupe de processus sous Linux) ;
  - en lancement manuel (onglet *Actions* > *Placeur-routeur* > *Run workflow* ; choix des campagnes et des graines
    « acceptation » ou « nuit ») et chaque nuit : `campagne_metroid` (une graine par machine, jeux nominal et
    compatibilité, `placeur.py --donnees … --qualite normal --sans-pcbdoc`), `campagne_corpus` (10 graines `normal`
    par carte « chaîne »), puis `fusion`, publiée dans le dossier `run_<numéro>` de la branche `ci-resultats-placeur`
    et dans le résumé de l'exécution ;
  - la campagne de nuit ne tourne que si la variable du dépôt `CAMPAGNE_NUIT` vaut `oui` (*Settings* >
    *Secrets and variables* > *Actions* > *Variables*) ;
  - les graines et les jeux sont dans `ci_campagne.json` : `graines` (mesure d'acceptation, 3001-3020 et
    4001-4020), `graines_nuit`, `jeux`, `reference_cout` (rev4 : 10328,5), `marge_cout` (d = 0,04), `comparer`
    (campagnes de référence de l'ancien code `9b5b5a3` : `run_4` et `run_6`). `python ci_graines.py graines`
    affiche la matrice (jeux × graines, au plus 256 entrées) ; `python ci_graines.py cartes corpus` les cartes
    « chaîne ».
- **Rapport de campagne** : `python ci_fusion.py DOSSIER… --sortie S [--reference-cout 10328,5] [--comparer REF…]`
  réunit les sorties de `placeur.py` (une par machine) en `S/rapport.json` (format `rapport/2`, avec les blocs
  `statistiques` et `comparaison`) et `S/resume.md`. Il publie : le taux de validité et son intervalle de Wilson à
  95 % ; la borne de Clopper-Pearson à 95 % (« plus de 80 % de graines valides » n'est affirmé qu'à partir de 87
  valides sur 100) ; le meilleur coût commun et la médiane des valides ; le calcul par graine valide (somme des
  temps de calcul des graines divisée par le nombre de valides) ; la probabilité qu'une campagne de 8 graines livre
  au moins une graine valide de coût ≤ référence ; les durées par phase ; le rapport durée prévue / durée réelle de
  chaque machine. `--comparer` applique la règle de non-infériorité NI(m ; d) sur les graines communes : nouveau
  nombre de valides ≥ celui de la référence − m (m tiré de la loi binomiale exacte : 6 pour un taux de 0,5 à 0,62,
  5 de 0,65 à 0,8, 4 de 0,82 à 0,88, 3 à 0,9 et 0,92, 2 à 0,95, sur 40 graines) et médiane des coûts ≤ (1 + d) ×
  celle de la référence ; la comparaison n'est acceptée que sur au moins 40 graines appariées.
- **Anciens résultats** (`9b5b5a3`, branche `ci-resultats-placeur`, lue par un clone) :
  `python ci_fusion.py --anciens <clone>/run_4 --reference-cout 10328,5 --sortie S` écrit `S/anciens.json` (mêmes
  blocs, plus la correspondance des champs de l'ancien format) et `S/resume.md` ; run_4 donne 12 graines valides
  sur 20 (Wilson [39 % ; 78 %]), meilleur 10090,64, médiane 11295,4, P(8) ≈ 0,34.
- **Campagne locale** : `python campagnes.py --donnees D.json --graines 3001-3020,4001-4020 --sortie S
  [--qualite normal] [--processeurs P] [--reference rev4=exemples/metroid_v2/references/rev4]
  [--reference-cout X] [--comparer REF]` lance `placeur.py` sur chaque suite de graines consécutives (dans
  `S/calculs/`), juge chaque référence par les mêmes contrôles que les graines (copie dans `S/references/`), puis
  écrit `S/campagne/rapport.json` et `S/campagne/resume.md`. Une campagne normale de METROID dure de 20 à 50 min par
  graine sur 4 fils : la mesure sur 40 graines se fait par l'intégration continue.
- **Essais de ces outils** : `python -m unittest test_placeur.TestFusionCampagne test_placeur.TestFluxCI -v`.
