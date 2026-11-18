# Vérification manuelle avant chaque étiquette (contrat §5.5)

Aucune étiquette `v*` sans ce compte rendu, recopié dans `NOTES_VERSION.md` (section « Compte rendu de la
vérification manuelle », ligne `COMPTE RENDU :` remplacée par `COMPTE RENDU : fait le AAAA-MM-JJ, …`). Le job de
publication de `paquet_windows.yml` le vérifie.

Deux machines propres :

- **Windows Sandbox** (Windows 11 à jour, sans aucun logiciel installé) ;
- **machine virtuelle Windows 10 22H2**.

Sur chacune, avec le zip téléchargé depuis la page de Release (pas une copie locale) :

| # | Essai | Attendu | Sandbox | Windows 10 |
|---|---|---|---|---|
| 1 | Empreinte : `Get-FileHash … -Algorithm SHA256` | égale à `PlaceurRouteur-<version>.zip.sha256` | | |
| 2 | Extraire **sans** « Débloquer », double-cliquer `PlaceurRouteur.exe` | symptôme de SmartScreen conforme à la section 7 du mode d'emploi, contournement qui marche | | |
| 3 | Extraire **avec** « Débloquer », sous `Essai é à\l'atelier (copie)\` | la fenêtre s'ouvre sans avertissement | | |
| 4 | Menu Aide → Vérifier l'installation | « tout va bien », code 0 | | |
| 5 | Exemple KiCad de `exemples\`, qualité Aperçu | fin code 0, `Ouvrir les résultats` montre le dossier | | |
| 6 | Carte `.PcbDoc` locale (METROID rev3), carte produite comprise | fin code 0, carte produite ouverte dans Altium sans erreur | | |
| 7 | Arrêter (« Finir les graines en cours », puis « Tout arrêter ») | arrêts conformes à la section 3.4 | | |
| 8 | Reprendre | graines finies reprises | | |
| 9 | Analyse Microsoft Defender du zip (si la CI ne l'a pas faite) | aucune détection | | |

Compte rendu : date, version, machines, résultat de chaque ligne, écarts constatés.
