# -*- coding: utf-8 -*-
"""Cartes ``.PcbDoc`` publiables du corpus (§7.3, jalon B3), dérivées d'une carte source par ``format_altium``.

    python -B corpus/pcbdoc/deriver.py --source CARTE.PcbDoc --sortie corpus/pcbdoc

Cartes écrites (chemins effacés par ``effacer_chemins(tout=True)``, §0.3) :

- ``demonstration.PcbDoc`` : la carte source, sans modification (carte de démonstration du paquet) ;
- ``dessous_45.PcbDoc`` : la première pièce posée dessous (ordre du fichier) qui a au moins deux pastilles, reposée
  à 45° à sa place (``pose.poser_composant``) ;
- ``trou_decale.PcbDoc`` : la première pastille traversante qui porte sa pile de tailles reçoit un décalage du cuivre
  par rapport au trou (« Offset From Hole Center ») de (5 ; -3) mil sur toutes les couches ;
- ``flux_vides.PcbDoc`` : tous les arcs, régions, remplissages, textes et vias supprimés, et aucun polygone (les
  désignateurs affichés sont d'abord recopiés dans ``SOURCEDESIGNATOR``).

Les objets modifiés sont choisis par leurs propriétés, jamais par leur nom. Deux exécutions donnent les mêmes octets
(GUID déterministes de la bibliothèque). Codes : 0 ; 2 entrée invalide (message sans chemin).
"""
import argparse
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ICI, "..", "..", "..", "format_altium"))

from altium_fichier import PcbDoc, ErreurPcbDoc, pose  # noqa: E402
from altium_fichier import gabarits as G  # noqa: E402
from altium_fichier.flux import SousBloc  # noqa: E402
from altium_fichier.proprietes import mil_vers_interne  # noqa: E402

DECALAGE_MIL = (5.0, -3.0)


def _sans_modification(doc):
    return "carte source"


def _dessous_45(doc):
    nb = {}
    for p in doc.pastilles():
        nb[p["composant"]] = nb.get(p["composant"], 0) + 1
    for c in doc.composants():
        if c["face"] == "BOTTOM" and nb.get(c["index"], 0) >= 2:
            n, anomalies = pose.poser_composant(doc, c["index"], c["x_mil"], c["y_mil"], 45.0, True)
            if anomalies:
                raise ErreurPcbDoc(f"pose à 45° : {len(anomalies)} anomalie(s)")
            return f"pièce dessous de rang {c['index']} à 45° ({n} primitives)"
    return None


def _trou_decale(doc):
    f = doc.data("Pads6")
    for i, p in enumerate(f.primitives):
        pile = p.sous[5].bloc if len(p.sous) > 5 and isinstance(p.sous[5], SousBloc) else None
        if pile is None or not p.bloc.get("trou"):
            continue
        n = len(pile["decalages_trou_x"]) // 4
        pile["decalages_trou_x"] = G.coords_vers_octets([mil_vers_interne(DECALAGE_MIL[0])] * n)
        pile["decalages_trou_y"] = G.coords_vers_octets([mil_vers_interne(DECALAGE_MIL[1])] * n)
        doc.marquer("Pads6")
        return f"pastille traversante de rang {i} décalée de {DECALAGE_MIL} mil"
    return None


def _flux_vides(doc):
    # les désignateurs affichés sont des textes : ils passent dans SOURCEDESIGNATOR avant la suppression des textes
    for c in doc.composants():
        doc.composant(c["designateur"])["SOURCEDESIGNATOR"] = c["designateur"]
    doc.marquer("Components6")
    a_supprimer = {}
    for storage in ("Arcs6", "Regions6", "Fills6", "Texts6", "Vias6"):
        d = doc.data(storage)
        if d is not None and getattr(d, "primitives", None):
            a_supprimer[storage] = set(range(len(d.primitives)))
    # pistes des polygones (traits de remplissage) : retirées avec eux
    a_supprimer["Tracks6"] = {t["index"] for t in doc.pistes() if t["polygone"] != 0xFFFF}
    a_supprimer = {k: v for k, v in a_supprimer.items() if v}
    bilan = pose.supprimer_primitives(doc, a_supprimer) if a_supprimer else {}
    poly = doc.data("Polygons6")
    if poly is not None and len(poly):
        poly.enregistrements.clear()
        doc.marquer("Polygons6")
    return "supprimés : " + ", ".join(f"{k} {v}" for k, v in sorted(bilan.items()))


CARTES = [("demonstration", _sans_modification), ("dessous_45", _dessous_45), ("trou_decale", _trou_decale),
          ("flux_vides", _flux_vides)]


def deriver(source, sortie):
    """Écrit les cartes de ``CARTES`` dans ``sortie`` ; rend [(nom, compte rendu)] (``None`` : cas absent de la
    source, carte non écrite)."""
    os.makedirs(sortie, exist_ok=True)
    res = []
    for nom, fonction in CARTES:
        doc = PcbDoc.ouvrir(source)
        quoi = fonction(doc)
        if quoi is None:
            res.append((nom, None))
            continue
        if pose.verifier_guids(doc):
            raise ErreurPcbDoc(f"{nom} : PrimitiveGuids incohérents")
        doc.effacer_chemins(tout=True)
        # bibliothèques sources vidées (B-ET8, critère 4 : même un nom de fichier seul n'est pas publié)
        for c in doc.composants():
            pr = doc.composant(c["designateur"])
            for k in ("SOURCEFOOTPRINTLIBRARY", "SOURCECOMPONENTLIBRARY"):
                if pr.get(k):
                    pr[k] = ""
        doc.marquer("Components6")
        doc.enregistrer(os.path.join(sortie, nom + ".PcbDoc"))
        res.append((nom, quoi))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cartes .PcbDoc du corpus dérivées d'une carte source (§7.3, B3).")
    ap.add_argument("--source", required=True, help="carte source .PcbDoc")
    ap.add_argument("--sortie", required=True, help="dossier de sortie")
    a = ap.parse_args(argv)
    try:
        for nom, quoi in deriver(a.source, a.sortie):
            print(f"{nom} : {quoi}" if quoi else f"{nom} : cas absent de la source, carte non écrite")
    except (ErreurPcbDoc, pose.ErreurPose, OSError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
