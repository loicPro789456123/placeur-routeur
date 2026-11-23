# Corpus `.PcbDoc` (jalon B3)

Cartes publiables dérivées de la carte source de démonstration par `deriver.py` (bibliothèque `format_altium`) :

    python -B corpus/pcbdoc/deriver.py --source CARTE_SOURCE.PcbDoc --sortie corpus/pcbdoc

| Carte | Contenu |
|---|---|
| `demonstration.PcbDoc` | carte de démonstration du paquet (17 pièces, plans GND et 3V3 avec zone de partage VIN, J1 verrouillée, H1 et H2 mécaniques, pièce dessous C3, rectangles arrondis, trous oblongs) |
| `dessous_45.PcbDoc` | la première pièce posée dessous reposée à 45° |
| `trou_decale.PcbDoc` | une pastille traversante au cuivre décalé du trou de (5 ; -3) mil |
| `flux_vides.PcbDoc` | sans arc, région, remplissage, texte, polygone ni via (désignateurs recopiés dans `SOURCEDESIGNATOR`) |

Chemins effacés (`effacer_chemins(tout=True)`), bibliothèques sources vidées ; deux exécutions donnent les mêmes octets.
Tests : `test_importer_carte.TestCorpusPcbDoc` (chaîne complète, aller-retour des pastilles, modifications, anonymat).
La convention de rotation des pastilles d'une pièce dessous (§0.4, annexe E-3) n'est pas confirmée ici : il faut une
séance dans Altium (annexe E du lot BF).
