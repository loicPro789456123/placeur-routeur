"""Analyses : inventaire des flux, couverture, recensement des champs inconnus."""
from __future__ import annotations

import collections
import re

from . import gabarits as G
from .flux import (FluxBrut, FluxPrimitives, FluxProps, FluxBlocsLongueur, SousBloc, SousRegion,
                   ChainePascal, FluxGestionnairePercage)
from .cles import dictionnaire


def inventaire(doc):
    """Liste de dicts : chemin, taille, nature, octets C/S/I, taux décodé, nombre d'enregistrements."""
    lignes = []
    for c, n in doc.racine.flux():
        f = doc.flux[c]
        cv = f.couverture()
        tot = len(n.donnees)
        nb = None
        for attr in ("primitives", "enregistrements", "chaines", "entrees", "blocs", "groupes", "polices"):
            if hasattr(f, attr):
                nb = len(getattr(f, attr))
                break
        lignes.append({"flux": c, "taille": tot, "nature": getattr(f, "nature", "?"), "nb": nb,
                       "C": cv["C"], "S": cv["S"], "I": cv["I"],
                       "taux_structure": 100.0 if tot == 0 else 100.0 * (cv["C"] + cv["S"]) / tot,
                       "taux_certain": 100.0 if tot == 0 else 100.0 * cv["C"] / tot})
    return lignes


def _resume_valeurs(cnt: collections.Counter, n=6):
    def fmt(v):
        if isinstance(v, (bytes, bytearray)):
            return v.hex() if len(v) <= 16 else v[:16].hex() + "…"
        return repr(v)
    items = cnt.most_common(n)
    s = ", ".join(f"{fmt(v)}×{k}" for v, k in items)
    if len(cnt) > n:
        s += f", … ({len(cnt)} valeurs distinctes)"
    return s


def recenser_inconnus(docs):
    """docs : liste de PcbDoc. Renvoie un dict de sections pour INCONNUS.md."""
    champs = collections.defaultdict(collections.Counter)   # (gabarit, champ) -> valeurs
    tailles = collections.defaultdict(collections.Counter)  # (gabarit, champ) -> longueurs
    cles_inconnues = collections.defaultdict(collections.Counter)  # storage -> clé -> nb
    exemples_cles = {}
    pascal = collections.defaultdict(collections.Counter)
    bruts = {}
    restes = collections.Counter()

    def bloc(b: G.Bloc):
        t = {c[1]: c for c in b.gabarit}
        for k, v in b.v.items():
            c = t.get(k)
            if c is None or c[4] == "I":
                champs[(b.nom_gabarit, k)][v if not isinstance(v, bytearray) else bytes(v)] += 1
                tailles[(b.nom_gabarit, k)][len(v) if isinstance(v, bytes) else G.taille_type(c[2])] += 1

    def props(storage, p):
        d = dictionnaire(storage)
        for k, v in p.elements:
            if d is None or d.statut(k) == "I":
                kk = re.sub(r"\d+", "#", k)
                cles_inconnues[storage][kk] += 1
                exemples_cles.setdefault((storage, kk), (v or "")[:60])

    for doc in docs:
        for c, f in doc.flux.items():
            st = c.split("/")[0]
            if isinstance(f, FluxPrimitives):
                for pr in f:
                    for i, s in enumerate(pr.sous):
                        if isinstance(s, SousBloc):
                            bloc(s.bloc)
                        elif isinstance(s, SousRegion):
                            bloc(s.entete)
                            props(st, s.props)
                            if s.reste:
                                restes[c] += len(s.reste)
                        elif isinstance(s, ChainePascal) and pr.type == 2 and i in (1, 2, 3):
                            pascal[f"pastille sous-enr. {i + 1}"][s.encoder()] += 1
                if f.reste:
                    restes[c] += len(f.reste)
            elif isinstance(f, FluxProps):
                for e in f:
                    if e.props is not None:
                        props(st, e.props)
                    else:
                        restes[c + " (enregistrement binaire)"] += len(e.binaire)
                if f.reste:
                    restes[c] += len(f.reste)
            elif isinstance(f, FluxBlocsLongueur):
                for b in f.blocs:
                    bloc(b)
            elif isinstance(f, FluxGestionnairePercage):
                for pr, _ in f.groupes:
                    props(st, pr)
                if f.reste:
                    restes[c] += len(f.reste)
            elif isinstance(f, FluxBrut) and f.statut == "I" and f.octets:
                bruts.setdefault(c, len(f.octets))
            else:
                cv = f.couverture()
                if cv["I"] and not c.startswith("Models/"):
                    restes[c + f" ({type(f).__name__})"] += cv["I"]
    return {"champs": champs, "tailles": tailles, "cles": cles_inconnues, "exemples": exemples_cles,
            "pascal": pascal, "bruts": bruts, "restes": restes, "resume": _resume_valeurs}
