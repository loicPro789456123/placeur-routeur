# -*- coding: utf-8 -*-
"""Coût commun d'un placement routé (format ``mesures/2``, formule ``cout_commun/2`` du contrat, §2.8 et §4.7).

    python mesures.py --donnees D --placement P --routage R --sortie S

cout_commun = longueur_mm + 1,5 x vias + 2,0 x retreci_mm + 3,0 x resistance_puissance_mohm + 0,1 x coudes
              + 1000 x max(0 ; s - ecart_moyen_mm),     s = 3 x isolement.defaut (plafond de l'écart p = 5 x défaut)

Mêmes définitions que ``validite::cout_commun`` (lot E) : longueur et vias du routage (couture exclue), rétréci sous
max(préférée, minimale) - 1e-3 mm, résistance 0,5 x L / l des rôles puissance et alimentation, coudes et écart moyen
calculés par ``geom_commune``. Codes : 0 (mesures écrites), 2 (entrée invalide), 4 (erreur interne).
"""
import sys

import geom_commune as gc

FORMAT = "mesures/2"
FORMULE = "cout_commun/2"

_CLES_VERDICT = ("longueur_pistes_mm", "vias", "resistance_puissance_mohm", "coudes", "ecart_moyen_mm")


def _termes_du_verdict(verdict):
    """Termes repris de ``verdict_strict.mesures`` (§2.8)."""
    m = gc.exiger(gc.objet_json(verdict, "verdict_strict"), "mesures", "verdict_strict")
    gc.objet_json(m, "verdict_strict.mesures")
    for cle in _CLES_VERDICT:
        gc.exiger(m, cle, "verdict_strict.mesures")
    return {"longueur_mm": gc.nombre(m["longueur_pistes_mm"], "verdict_strict.mesures.longueur_pistes_mm"),
            "vias": gc.entier(m["vias"], "verdict_strict.mesures.vias"),
            "resistance_puissance_mohm": gc.nombre(m["resistance_puissance_mohm"],
                                                   "verdict_strict.mesures.resistance_puissance_mohm"),
            "coudes": gc.entier(m["coudes"], "verdict_strict.mesures.coudes"),
            "ecart_moyen_mm": gc.nombre(m["ecart_moyen_mm"], "verdict_strict.mesures.ecart_moyen_mm")}


def mesurer(donnees, placements, routage, verdict_strict=None):
    """Rend ``mesures/2`` pour un placement et un routage (références manuelles comprises).

    Si ``verdict_strict`` (``verdict/2``) est fourni, la longueur, les vias, la résistance, les coudes et l'écart moyen
    sont repris de ses ``mesures`` ; sinon ils sont calculés ici. Le rétréci est toujours calculé sur le routage."""
    gc.exiger_format(donnees, "donnees_placeur/2", "donnees")
    defaut = gc.isolement_defaut(donnees)
    bilan = gc.bilan_pistes(donnees, routage)
    if verdict_strict is None:
        scene = gc.poser(donnees, placements, routage)
        m = gc.mesures_routage(scene)
        termes = {"longueur_mm": m["longueur_pistes_mm"], "vias": m["vias"],
                  "resistance_puissance_mohm": m["resistance_puissance_mohm"], "coudes": m["coudes"],
                  "ecart_moyen_mm": m["ecart_moyen_mm"]}
    else:
        termes = _termes_du_verdict(verdict_strict)
    termes["retreci_mm"] = bilan["retreci_mm"]
    termes = {cle: termes[cle] for cle in gc.TERMES_COUT}
    return {"format": FORMAT, "formule": FORMULE, "cout_commun": gc.cout_commun(termes, defaut), "termes": termes,
            "parametres": {"seuil_ecart_mm": gc.FACTEUR_SEUIL_ECART * defaut,
                           "plafond_ecart_mm": gc.FACTEUR_PLAFOND_ECART * defaut}}


OPTIONS = (
    gc.Option("--donnees", "données du moteur (donnees_placeur/2)", obligatoire=True, meta="D"),
    gc.Option("--placement", "placement (placements/2 ou liste v1)", obligatoire=True, meta="P"),
    gc.Option("--routage", "routage (routage/2 ou v1)", obligatoire=True, meta="R"),
    gc.Option("--sortie", "fichier mesures/2 écrit", obligatoire=True, meta="S"),
)


def _principal(args):
    donnees = gc.charger_json(args["donnees"], ("donnees_placeur/2",))
    placements = gc.charger_json(args["placement"], ("placements/2", "placements/1"))
    routage = gc.charger_json(args["routage"], ("routage/2", "routage/1"))
    res = mesurer(donnees, placements, routage)
    gc.ecrire_json(args["sortie"], res)
    t = res["termes"]
    gc.afficher("%s : coût commun %s (longueur %s mm, %d vias, rétréci %s mm, résistance %s mOhm, %d coudes, "
                "écart moyen %s mm)" % (gc.nom_fichier(args["sortie"]), gc.nombre_fr(res["cout_commun"]),
                                        gc.nombre_fr(t["longueur_mm"]), t["vias"], gc.nombre_fr(t["retreci_mm"]),
                                        gc.nombre_fr(t["resistance_puissance_mohm"]), t["coudes"],
                                        gc.nombre_fr(t["ecart_moyen_mm"], 3)))
    return gc.CODE_SUCCES


def main(argv=None):
    """Ligne de commande (§5.2) ; rend le code de sortie (§1.4) sans jamais quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, "mesures.py",
                       "Coût commun (mesures/2, formule cout_commun/2) d'un placement routé.")


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
