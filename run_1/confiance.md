# Confiance des campagnes depuis zero (iteration 7)

Campagnes independantes (graines differentes), chacune : placement aleatoire legal -> recuit global a paliers -> routage detaille complet -> phase conjointe (cout sur le routage detaille, espacement compris) -> finition. Toutes sont jugees par les memes controles independants et le meme cout commun (voir campagnes.py).

| Grandeur | Valeur |
|---|---:|
| Campagnes terminees | 24 |
| dont valides (0 liaison ouverte, 0 violation, placement 0) | 0 |
| dont invalides | 24 |

## References (meme cout, memes controles)

| Etat | Cout | Longueur (mm) | Vias | Retreci (mm) | Chute +8V c1 / c2 (mV) | Ecart moyen (mm) | Valide |
|---|---:|---:|---:|---:|---:|---:|---|
| rev3 (sorties) | 10596.00 | 5381.4 | 473 | 242.9 | 69.2 / 51.8 | 0.509 | non |
| rev4 (sorties/conjoint) | 10328.52 | 5393.0 | 470 | 137.6 | 70.9 / 52.9 | 0.547 | non |

## Convertisseur (bloc hacheur) : manuel (rev3 / rev4) contre automatique (campagnes, bloc libere)

| Etat | Boucle chaude (mm2 / perimetre mm, indicateur de placement) | Noeud de commutation : longueur pistes (mm) | surface cuivre (mm2) | Reseaux courts (demi-perimetres, mm) | Compensation -> noeud SW min (mm) |
|---|---:|---:|---:|---:|---:|
| rev3 (sorties) | 3.55 / 26.64 | 8.32 | 15.08 | 41.72 | 8.71 |
| rev4 (sorties/conjoint) | 3.55 / 26.64 | 8.32 | 15.08 | 42.23 | 8.71 |

Bloc manuel : surfaces de cuivre comptees par leurs rectangles englobants (surestimation) ; bloc libere : le placeur ne pose pas de surfaces/polygones, le noeud de commutation est une piste (au plus 2 mm de large).

## Interpretation (a lire)

- La confiance 1 - (1 - p)^N est la probabilite, estimee sur l'echantillon, qu'au moins une des N campagnes atteigne le niveau "a moins de 1 % du meilleur cout observe". C'est le meilleur atteignable PAR CET ALGORITHME ET CE COUT, pas une preuve d'optimum global : p est estime sur les memes campagnes (biais optimiste quand N est petit), et une region de l'espace jamais visitee par l'algorithme ne peut pas etre vue par ce critere.
- La garantie theorique du recuit (refroidissement logarithmique, temps infini) est inatteignable en pratique.
- L'ajustement de Weibull (valeurs extremes, 3 parametres) extrapole une borne inferieure de la distribution des couts de CET algorithme ; elle depend de la forme supposee et devient fragile avec peu de campagnes valides.
- Critere d'arret : confiance >= 0.99, meilleur inchange depuis 5 campagnes valides, au moins 12 campagnes.
