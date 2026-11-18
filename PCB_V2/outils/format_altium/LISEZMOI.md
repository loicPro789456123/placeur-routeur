# format_altium : lire et écrire directement les fichiers Altium

Bibliothèque Python `altium_fichier` (Python 3.12, dépendance : `olefile`) pour décoder et réécrire
un `.PcbDoc` sans passer par Altium ni par le pont eda-agent, et lire `.SchDoc` / `.PrjPcb`.
L'API (jalon B1 : signatures, unités, clés rendues, erreurs, compatibilité, exemples pour les lots B et C) est
décrite dans **`API.md`**.

## Contenu

| chemin | rôle |
|---|---|
| `altium_fichier/cfb.py` | conteneur OLE2/CFB : lecture (olefile), **écrivain maison** v3 (FAT, DIFAT, mini-FAT, arbre rouge-noir) |
| `altium_fichier/proprietes.py` | listes `\|CLE=VALEUR\|` sans perte, unités (mil/mm/interne), textes Unicode |
| `altium_fichier/gabarits.py` | dispositions octet par octet des primitives binaires (piste, arc, via, pastille, texte, remplissage, région) avec statut certain/supposé/inconnu |
| `altium_fichier/flux.py` | un codec par flux (octets <-> objets) + couverture C/S/I |
| `altium_fichier/cles.py` | dictionnaire des clés de propriétés par flux |
| `altium_fichier/couches.py` | identifiants de couches V6 / V7 |
| `altium_fichier/pose.py` | pose d'un composant (translation, rotation, **changement de face**) avec toutes ses primitives ; suppression de primitives avec renumérotation de PrimitiveGuids / DrillManager / flux jumeaux ; ajout en coût constant avec GUID déterministe ; groupes de perçage de via ; contrôle des GUID ; repère local des pièces (`monde_vers_local`, `local_vers_monde`, `rotation_relative`, convention unique de §0.4) |
| `altium_fichier/pcbdoc.py` | modèle de haut niveau : lecture typée (composants, désignateurs, pastilles, pistes, vias, régions, empilage, plans négatifs, régions de carte, panneau, règles...) ; ajouts de pistes, vias et régions (prototypes de `gabarits.py`, sur n'importe quelle carte) ; effacement des chemins ; écriture atomique |
| `altium_fichier/chemins.py` | chemins absolus : détection et réduction au nom seul (textes, valeurs codées, octets bruts) |
| `altium_fichier/commandes.py` | socle des outils en ligne de commande (`main(argv) -> int`, codes de sortie, messages sans chemin) |
| `altium_fichier/schdoc.py` | lecture .SchDoc (composants, broches, paramètres, empreintes, fils, étiquettes, ports) et .PrjPcb (INI) |
| `altium_fichier/analyse.py` | inventaire et recensement des inconnus |
| `inventaire.py` | `CARTE... [--rapports DOSSIER] [--inconnus FICHIER]` : inventaire des flux et taux décodés ; écrit les rapports et le recensement des inconnus seulement si on les demande |
| `aller_retour.py` | `CARTE... --sortie DOSSIER [--modifications]` : lire -> modèle -> écrire, relu par olefile et comparé flux par flux ; fichiers d'essai modifiés en option |
| `verifier_cartes.py` | `CARTE_OU_DOSSIER... [--exclure NOM] [--travail DOSSIER] [--json FICHIER]` : pour chaque .PcbDoc (un dossier : ses .PcbDoc, sans sous-dossier), ouverture, réencodage octet pour octet de chaque flux, tous les accesseurs, aller-retour relu par olefile, ajouts (piste, via, région) écrits, relus et contrôlés ; durée de chaque phase ; rapport sans chemin ; codes 0 sans défaut, 1 défaut, 2 entrée invalide |
| `differentiel.py` | différence champ par champ entre deux .PcbDoc (expériences différentielles) ; `--apparier` : cartes entières (composants appariés par désignateur, primitives par propriétaire/couche/réseau/position) |
| `generer_format.py` | `--sortie FORMAT.md` : régénère la description du format depuis le code |
| `fabriquer_essais.py` | `--base CARTE --sortie DOSSIER` : fichiers d'essai de l'annexe E (E1 à E9, T1 ; objets choisis par leurs propriétés ; expérience sautée si la base n'a pas l'objet), `attendus.json`, `resultats_a_remplir.json`, chaque fichier relu et contrôlé ; `--verifier RESULTATS --attendus ATTENDUS [--json FICHIER]` : verdict par convention (confirmée, contredite, non faite) ; codes 0, 1 (fichier non conforme ou convention contredite), 2 ; protocole : `EXPERIENCES_A_FAIRE.md` ; fichiers dans `essais/` (ignoré par git) |
| `FORMAT.md`, `INCONNUS.md`, `EXPERIENCES_A_FAIRE.md` | documentation du format, inconnus, expériences Altium |
| `API.md` | API de la bibliothèque (jalon B1) ; `tests/test_api.py` vérifie qu'elle est complète |
| `tests/` | `python -B -m unittest discover -s tests` (cartes synthétiques, `tests/fabrique.py`) ; avec les cartes locales : `python -B tests/lancer_tests.py --cartes-locales DOSSIER ...` (dossiers passés par option, tests sautés affichés). Fichiers : `test_flux_vides`, `test_lecture`, `test_ecriture` (ajouts, GUID, perçage, déterminisme, repère local, performances), `test_chemins`, `test_api`, `test_outils`, `test_altium_fichier`, `test_cartes_locales` (`verifier_cartes.py` ; réencodage de toutes les cartes trouvées par `--cartes-locales`), `test_essais` (`fabriquer_essais.py` ; sens des arcs du contour sur WiFi et SL1) |
| `essais/` | fichiers réécrits / modifiés à faire ouvrir par Altium (non suivi) |

## Exemple

```python
from altium_fichier import PcbDoc
doc = PcbDoc.ouvrir("carte.PcbDoc")            # ErreurPcbDoc (nom seul) si ce n'est pas un .PcbDoc
print(doc.empilage(), doc.contour_carte(), doc.regles()[0])
doc.designateurs(), doc.pastilles(), doc.couches_cuivre()   # API de lecture (B1)
doc.deplacer_composant("U1", dx_mil=100)            # + pastilles, pistes, textes, régions, corps 3D
doc.ajouter_piste("TOP", (300, 300), (500, 300), 10, reseau="GND")
doc.ajouter_via((500, 300), 23.622, 11.811, reseau="GND")
doc.ajouter_region("TopLayer", [(0, 0), (100, 0), (100, 50)], "GND", trous=())
doc.effacer_chemins(tout=False)                     # FILENAME effacé (tout=True : tous les chemins)
doc.enregistrer("essais/sortie.PcbDoc")             # atomique ; refusé sur le fichier ouvert
# accès bas niveau : doc.flux["Tracks6/Data"][i].bloc["largeur"], doc.data("Board6")[0]["ORIGINX"]
```

## Principes

- Chaque flux est décodé ; tout octet non compris est **conservé brut** (`inconnu_<décalage>`) et listé
  dans `INCONNUS.md`. Un flux non modifié est réécrit **octet pour octet** (vérifié sur les 3 cartes
  METROID, 173 + 172 + 163 flux, puis sur les 11 cartes locales, MiniPC compris : `verifier_cartes.py` et
  `test_cartes_locales`). `INCONNUS.md` est régénéré par
  `python -B inventaire.py <rev4> <rev3> <original> --inconnus INCONNUS.md` (cartes METROID seules).
- Les compteurs `Header` des flux modifiés sont mis à jour à l'écriture ; une primitive ajoutée reçoit
  une entrée `PrimitiveGuids` (s'il existe) ; une via ajoutée est rattachée à son outil dans `DrillManager`
  (s'il existe ; groupe créé au besoin). Les ajouts sont construits depuis les gabarits (jamais copiés d'une
  primitive de la carte) : ils fonctionnent sur une carte sans aucune piste ni via.
- Le fichier entier réécrit n'a pas la même disposition de secteurs que celui d'Altium (même taille ;
  tous les flux identiques) : l'ouverture dans Altium reste **à vérifier** (EXPERIENCES_A_FAIRE.md, T1 ; fichier fabriqué par `fabriquer_essais.py`).
- Pose (`pose.poser_composant`) : convention monde = origine + R(rotation)·M(face)·local, M(dessous) = miroir y → −y ;
  vérifiée champ par champ sur deux cartes posées par Altium (graines ed_1009 et ed_1038 : 319 composants
  dont 95 et 107 retournés, pastilles, pistes, arcs, remplissages, corps 3D, textes mécaniques identiques).
  Changement de face refusé (anomalie) pour une primitive sur couche interne ou mécanique appairée, une
  pastille en pile complète ou à trou décalé. Utilisé par `../placeur_routage/generer_pcbdoc.py`.
- Limites : pas d'ajout de texte (WideStrings6 à compléter), de
  recoulée de polygone (le cuivre coulé n'est pas recalculé : Altium le signalera). Le cache
  `LettersGeometry` n'est pas décodé.
