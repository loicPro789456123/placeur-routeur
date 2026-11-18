# PlaceurRouteur 2.0.0 : notes de version

## Démarrer (lisible avant l'extraction)

1. PlaceurRouteur 2.0.0 : rien à installer. Avant tout, clic droit sur le zip, Propriétés, cocher « Débloquer », OK.
2. Clic droit sur le zip, « Extraire tout… », vers un dossier à vous (par exemple Documents\PlaceurRouteur).
3. Le programme ne marche pas lancé depuis l'intérieur du zip : toujours extraire d'abord.
4. Dans le dossier extrait, double-cliquer sur PlaceurRouteur.exe : la fenêtre s'ouvre.
5. Mode d'emploi : MODE_EMPLOI.html (blocage par Windows ou l'antivirus : section 7).

Empreinte SHA-256 du zip : publiée à côté du zip (`PlaceurRouteur-2.0.0.zip.sha256`) ; la comparer avec
`Get-FileHash .\PlaceurRouteur-2.0.0.zip -Algorithm SHA256` (mode d'emploi, section 8).

## Systèmes

Essayés : Windows 11 23H2 et suivants, Windows 10 22H2, x64. Tout le reste est **non vérifié** (ARM64 par
émulation compris).

## Nouveautés

*(à rédiger à l'étiquette)*

## Limites connues

Polygones à recouler dans Altium et zones à remplir dans KiCad ; KiCad : sérigraphie non repositionnée ; matrices
de billes : éventail en os de chien non fait ; paires différentielles routées à la suite, sans routage couplé ;
carte 2 couches sans plan : masse routée en pistes ; règles `Clearance`/`Width` à portée par couche ou par salle et
`ComponentClearance` par paire : non interprétées (signalées) ; découpes appartenant à une pièce : la pièce reste
fixe ; cartes rigides-flexibles et panneaux : refusés.

## Compte rendu de la vérification manuelle

Exigé avant toute étiquette (`VERIFICATION_MANUELLE.md`) ; le job de publication refuse ces notes tant que la ligne
ci-dessous n'est pas remplacée par le compte rendu daté.

COMPTE RENDU : à faire
