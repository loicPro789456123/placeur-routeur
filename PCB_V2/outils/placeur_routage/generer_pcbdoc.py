"""Fabrique le .PcbDoc d'une graine du placeur-routeur, sans Altium (Windows ou Linux), et contrôle un gabarit.

Contrôle du gabarit (``controle_gabarit/1``, §2.9) :

    python generer_pcbdoc.py --controler-gabarit CARTE.PcbDoc --entree E.json --sortie G.json

    codes : 0 gabarit utilisable, 1 inutilisable (erreurs listées), 2 entrée invalide, 4 erreur interne.

Génération (``rapport_pcbdoc/1``, §2.9, §5.2) :

    python generer_pcbdoc.py DOSSIER SORTIE.PcbDoc --gabarit CARTE.PcbDoc --entree E.json [--config C.json]
        [--sans-serigraphie] [--travail DOSSIER] [--rapport R.json] [--moteur-serigraphie EXE]
        [--serigraphie-json F] [--donnees D.json] [--garder-coulee] [--reference REF.PcbDoc] [--forcer]

    codes : 0 carte produite, 1 carte produite mais contrôles en échec, 2 entrée invalide, 4 erreur interne.

Entrées : le dossier de graine (``placements_routage.json``, ``routage_detaille.json``, ``vias_couture.json``
facultatif ; mil, repère commun), le gabarit (copie de la carte importée, lue seulement : son SHA-256 doit égaler
``entree.source.sha256``), l'entrée ``entree_carte/2`` (origine du repère commun dans le fichier,
``source.origine_fichier_mil`` ; couches de cuivre nommées par rang) et la configuration ``config_pcbdoc/2``
(défauts du plan du lot C, §4-12, si elle est absente).

Étapes : retrait du cuivre libre routé (pistes, arcs, remplissages, régions, vias hors composant et hors keep-out) ;
pose des pièces ; ajout des pistes, vias et vias de couture ; sérigraphie par le moteur (repli : carte écrite sans
repositionner les désignateurs) ; chemins du fichier effacés ; écriture, relecture et contrôles.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import math
import os
import platform
import subprocess
import sys
import time

ICI = os.path.dirname(os.path.abspath(__file__))
OUTILS = os.path.dirname(ICI)
for _d in (ICI, os.path.join(OUTILS, "format_altium"), os.path.join(OUTILS, "serigraphie")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import geom_commune as gc  # noqa: E402
from altium_fichier import PcbDoc, cfb, couches  # noqa: E402
from altium_fichier import pose  # noqa: E402
from altium_fichier.flux import FluxPrimitives, SousRegion  # noqa: E402
from altium_fichier.pcbdoc import FLUX_PRIMITIVES_GEO  # noqa: E402
from altium_fichier.proprietes import valeur_mil  # noqa: E402
import pcbdoc_serigraphie as seri  # noqa: E402
import drc_serigraphie  # noqa: E402

PROGRAMME = "generer_pcbdoc.py"
DOSSIER_SERI = os.path.join(OUTILS, "serigraphie")
FORMAT_ENTREE = "entree_carte/2"
FORMAT_CONTROLE = "controle_gabarit/1"
FORMAT_RAPPORT = "rapport_pcbdoc/1"
FORMAT_CONFIG = "config_pcbdoc/2"
TYPE_SOURCE = "altium_pcbdoc"
# Clés des formats de §2.9 (comparées par les tests aux tables du contrat).
CLES_CONTROLE = ("format", "fichier", "sha256", "utilisable", "erreurs", "avertissements", "couches_ecrivibles",
                 "non_retournables", "modeles")
CLES_MODELES = ("via", "piste", "region")
CLES_RAPPORT = ("format", "fichier", "livrable", "controles", "conflits_isolement", "serigraphie",
                "polygones_a_recouler", "chemins_effaces", "duree_s")
LIVRABLES = ("valide", "a_terminer", "apercu")
FLUX_CUIVRE_LIBRE = ["Tracks6", "Arcs6", "Fills6", "Regions6", "ShapeBasedRegions6"]
SANS_OBJET = 0xFFFF
TOLERANCE_MIL = 0.002


class ErreurGeneration(gc.ErreurEntree):
    """Génération impossible pour une raison d'entrée (code 2)."""


# ---------------------------------------------------------------------------------------------------------------------
# Couches de cuivre : genre par l'identifiant du format, appariement par rang avec l'entrée (§0.4)
# ---------------------------------------------------------------------------------------------------------------------
def genre_couche(nom):
    """Genre d'une couche d'après son identifiant ``format_altium`` : ``signal``, ``plan_negatif`` ou None."""
    nom = nom or ""
    if nom in ("TOP", "BOTTOM") or (nom.startswith("MID") and nom[3:].isdigit()):
        return "signal"
    if nom.startswith("PLANE") and nom[5:].isdigit():
        return "plan_negatif"
    return None


def est_cuivre_signal(v6):
    return genre_couche(couches.NOMS_V6.get(v6, "")) == "signal"


def couches_cuivre(doc):
    """Couches de cuivre de l'empilage, de haut en bas : ``[{"v6", "genre", "nom_empilage"}]``."""
    out = []
    for e in doc.empilage():
        g = genre_couche(e.get("couche"))
        if g is not None and e.get("couche") in couches.IDS_V6:
            out.append({"v6": couches.IDS_V6[e["couche"]], "genre": g, "nom_empilage": e.get("nom")})
    return out


def _couches_entree(entree):
    carte = gc.objet_json(gc.exiger(entree, "carte", "entree"), "entree.carte")
    lst = gc.liste(gc.exiger(carte, "couches", "entree.carte"), "entree.carte.couches")
    res = []
    for k, c in enumerate(lst):
        ch = "entree.carte.couches[%d]" % k
        gc.objet_json(c, ch)
        res.append({"nom": gc.texte(gc.exiger(c, "nom", ch), ch + ".nom"),
                    "rang": gc.entier(gc.exiger(c, "rang", ch), ch + ".rang"),
                    "type": gc.texte(gc.exiger(c, "type", ch), ch + ".type"),
                    "nom_fichier": c.get("nom_fichier")})
    res.sort(key=lambda c: c["rang"])
    if [c["rang"] for c in res] != list(range(len(res))):
        raise gc.ErreurEntree("entree.carte.couches : rangs non consécutifs depuis 0")
    return res


def apparier_couches(doc, entree, fichier):
    """Apparie par rang les couches de cuivre du gabarit et celles de l'entrée.

    Rend ``(v6_par_nom, ecrivibles, erreurs, avertissements)`` : ``v6_par_nom`` {nom de l'entrée : identifiant V6},
    ``ecrivibles`` = noms (de l'entrée) des couches de signal de l'empilage, de haut en bas."""
    gab = couches_cuivre(doc)
    ent = _couches_entree(entree)
    erreurs, avert = [], []
    if not gab:
        return {}, [], ["%s : aucune couche de cuivre dans l'empilage" % fichier], []
    if len(gab) != len(ent):
        return {}, [], ["%s : %d couches de cuivre dans l'empilage, %d dans l'entrée" % (fichier, len(gab), len(ent))], []
    v6_par_nom, ecrivibles = {}, []
    for g, e in zip(gab, ent):
        if g["genre"] != e["type"]:
            erreurs.append("%s : couche de rang %d de type %s dans l'empilage, %s dans l'entrée (%s)"
                           % (fichier, e["rang"], g["genre"], e["type"], e["nom"]))
            continue
        if e["nom_fichier"] not in (None, "", g["nom_empilage"]):
            avert.append("couche de rang %d : nommée « %s » dans l'entrée, « %s » dans l'empilage"
                         % (e["rang"], e["nom_fichier"], g["nom_empilage"]))
        v6_par_nom[e["nom"]] = g["v6"]
        if g["genre"] == "signal":
            ecrivibles.append(e["nom"])
    return v6_par_nom, ecrivibles, erreurs, avert


# ---------------------------------------------------------------------------------------------------------------------
# Lecture du gabarit et de l'entrée
# ---------------------------------------------------------------------------------------------------------------------
def lire_octets(chemin):
    nom = gc.nom_fichier(chemin)
    try:
        with open(chemin, "rb") as f:
            return f.read()
    except FileNotFoundError:
        raise gc.ErreurEntree("%s : fichier introuvable" % nom) from None
    except IsADirectoryError:
        raise gc.ErreurEntree("%s : c'est un dossier, pas un fichier" % nom) from None
    except OSError as e:
        raise gc.ErreurEntree("%s : lecture impossible (%s)" % (nom, type(e).__name__)) from None


def ouvrir_octets(brut, nom):
    """Lecture en mémoire d'un .PcbDoc (le fichier n'est jamais réécrit)."""
    try:
        return PcbDoc(cfb.lire_cfb(io.BytesIO(brut)), nom)
    except gc.ErreurEntree:
        raise
    except Exception as e:  # fichier corrompu ou d'un autre type : entrée invalide
        raise gc.ErreurEntree("%s : fichier .PcbDoc illisible (%s)" % (nom, type(e).__name__)) from None


def charger_entree(entree):
    """``entree_carte/2`` en mémoire (objet) ou par son chemin."""
    if entree is None:
        raise gc.ErreurEntree("--entree obligatoire : entrée entree_carte/2 de la carte (son SHA-256 et l'origine "
                              "du repère commun)")
    if isinstance(entree, (str, os.PathLike)):
        return gc.charger_json(entree, (FORMAT_ENTREE,))
    return gc.exiger_format(entree, FORMAT_ENTREE, "entree")


def _source(entree):
    return gc.objet_json(gc.exiger(entree, "source", "entree"), "entree.source")


def origine_absolue_mil(entree):
    """Origine du repère commun dans le fichier (mil), lue dans ``entree.source.origine_fichier_mil`` (§0.4)."""
    src = _source(entree)
    v = src.get("origine_fichier_mil")
    if v is None:
        raise gc.ErreurEntree("entree.source.origine_fichier_mil absente : origine du repère commun inconnue")
    x, y = gc.point(v, "entree.source.origine_fichier_mil")
    return x, y


def designateurs(doc):
    """{rang Components6 : désignateur} (§2.2 : texte affiché, sinon SOURCEDESIGNATOR, sinon ``__piece_<k>``)."""
    D = dict(doc.designateurs())
    for i in range(len(doc.data("Components6") or [])):
        if not D.get(i):
            D[i] = "__piece_%d" % i
    return D


def _dedoublonner(anomalies):
    vus = {}
    for a in anomalies:
        vus[a] = vus.get(a, 0) + 1
    return [a if n == 1 else "%s (x%d)" % (a, n) for a, n in vus.items()]


def _pose_fichier(c):
    return (valeur_mil(c["X"]), valeur_mil(c["Y"]), float(c.get("ROTATION", "0").strip() or 0),
            c.get("LAYER") == "BOTTOM")


def pose_inchangee(c, x, y, r, dessous, tol_mil=0.001, tol_deg=1e-6):
    """Vrai si la pose demandée est celle du fichier (0,001 mil et 1e-6 degré près) : la pièce n'est pas reposée."""
    fx, fy, fr, fd = _pose_fichier(c)
    dr = abs((fr - r + 180.0) % 360.0 - 180.0)
    return fd == dessous and abs(fx - x) <= tol_mil and abs(fy - y) <= tol_mil and dr <= tol_deg


def essai_retournement(doc, D):
    """Retourne chaque pièce sur place (``pose.poser_composant``, même position et rotation, autre face) dans une
    lecture en mémoire ``doc`` qui ne sert qu'à cela : {désignateur : raison} des pièces dont le retournement n'est
    pas sûr (couche interne, couche mécanique appairée, pile complète, trou décalé…)."""
    out = {}
    comp = doc.data("Components6")
    for i in range(len(comp or [])):
        try:
            x, y, r, dessous = _pose_fichier(comp[i])
            _, anomalies = pose.poser_composant(doc, i, x, y, r, not dessous)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # noqa: BLE001 (une pièce qu'on ne sait pas retourner n'arrête pas le contrôle)
            anomalies = ["essai de retournement en échec (%s)" % type(e).__name__]
        if anomalies:
            out[D[i]] = "; ".join(_dedoublonner(anomalies))
    return out


def modeles(doc):
    """Primitives libres pouvant servir de modèle : via, piste (cuivre de signal), région (cuivre de signal)."""
    via = any(pr.bloc["composant"] == SANS_OBJET and not pr.bloc["drapeaux2"] & 2 and pr.bloc["mode_pile"] == 0
              for pr in doc.data("Vias6") or [])
    piste = any(pr.bloc["composant"] == SANS_OBJET and pr.bloc["polygone"] == SANS_OBJET
                and not pr.bloc["drapeaux2"] & 2 and est_cuivre_signal(pr.bloc["couche"])
                for pr in doc.data("Tracks6") or [])
    region = False
    for pr in doc.data("Regions6") or []:
        b, s0 = pr.bloc, pr.sous[0]
        if (b["composant"] == SANS_OBJET and b["polygone"] == SANS_OBJET and not b["drapeaux2"] & 2
                and est_cuivre_signal(b["couche"]) and isinstance(s0, SousRegion)
                and s0.props.get("ISBOARDCUTOUT") != "TRUE" and s0.props.get("KEEPOUT") != "TRUE"):
            region = True
            break
    return {"via": via, "piste": piste, "region": region}


def controler_gabarit(gabarit, entree):
    """Contrôle d'un gabarit Altium (``controle_gabarit/1``, §2.9) : rend l'objet, n'écrit rien.

    ``gabarit`` : chemin du .PcbDoc ; ``entree`` : ``entree_carte/2`` (objet ou chemin). Gabarit illisible ou entrée
    invalide : ``ErreurEntree`` (code 2)."""
    ent = charger_entree(entree)
    fichier = gc.nom_fichier(gabarit)
    brut = lire_octets(gabarit)
    sha = hashlib.sha256(brut).hexdigest()
    src = _source(ent)
    erreurs, avert = [], []
    if src.get("type") not in (None, TYPE_SOURCE):
        erreurs.append("entree.source.type : « %s » ; ce générateur n'écrit que des cartes %s"
                       % (src.get("type"), TYPE_SOURCE))
    if str(src.get("sha256") or "").lower() != sha:
        erreurs.append("%s : empreinte SHA-256 différente de celle de l'entrée (%s) : ce n'est pas la carte importée"
                       % (fichier, gc.nom_fichier(str(src.get("fichier") or "?"))))
    if src.get("origine_fichier_mil") is None:
        erreurs.append("entree.source.origine_fichier_mil absente : origine du repère commun inconnue")
    doc = ouvrir_octets(brut, fichier)
    _, ecrivibles, e_couches, a_couches = apparier_couches(doc, ent, fichier)
    erreurs += e_couches
    avert += a_couches
    if not doc.contour_carte():
        erreurs.append("%s : contour de carte absent" % fichier)
    D = designateurs(doc)
    vus, doubles = set(), []
    for i in sorted(D):
        if D[i] in vus:
            doubles.append(D[i])
        vus.add(D[i])
    if doubles:
        erreurs.append("%s : désignateurs en double : %s" % (fichier, ", ".join(sorted(set(doubles))[:20])))
    composants = gc.liste(ent.get("composants", []), "entree.composants")
    noms_entree = []
    for k, c in enumerate(composants):
        ch = "entree.composants[%d]" % k
        noms_entree.append(gc.texte(gc.exiger(gc.objet_json(c, ch), "designateur", ch), ch + ".designateur"))
    absents = sorted(set(noms_entree) - vus)
    if absents:
        erreurs.append("%s : %d pièces de l'entrée absentes du gabarit : %s"
                       % (fichier, len(absents), ", ".join(absents[:20])))
    en_trop = sorted(vus - set(noms_entree))
    if en_trop:
        avert.append("%d pièces du gabarit absentes de l'entrée : %s" % (len(en_trop), ", ".join(en_trop[:20])))
    non_retournables = essai_retournement(ouvrir_octets(brut, fichier), D)
    return {"format": FORMAT_CONTROLE, "fichier": fichier, "sha256": sha, "utilisable": not erreurs,
            "erreurs": erreurs, "avertissements": avert, "couches_ecrivibles": ecrivibles,
            "non_retournables": non_retournables, "modeles": modeles(doc)}


# ---------------------------------------------------------------------------------------------------------------------
# Configuration config_pcbdoc/2 (§2.9) ; défauts : plan du lot C, §4-12
# ---------------------------------------------------------------------------------------------------------------------
def config_defaut():
    return {"format": FORMAT_CONFIG, "livrable": "valide",
            "serigraphie": {"graine": 1, "chaines": 16, "mouvements": 20000000, "threads": os.cpu_count() or 1,
                            "tailles_mm": "auto", "garde_masque_mm": None, "trous_supplementaires_mm": [],
                            "options": []},
            "vias_couture": True}


def charger_config(config):
    """``config_pcbdoc/2`` (objet, chemin ou None = défauts) complétée par les défauts et vérifiée. Les clés
    inconnues (évolutions mineures, §0.5) et celles qui commencent par ``_`` sont ignorées."""
    cfg = config_defaut()
    if config is None:
        return cfg
    if isinstance(config, (str, os.PathLike)):
        lu = gc.charger_json(config, (FORMAT_CONFIG,))
    else:
        lu = gc.exiger_format(config, FORMAT_CONFIG, "config")
    if "livrable" in lu:
        v = gc.texte(lu["livrable"], "config.livrable")
        if v not in LIVRABLES:
            raise gc.ErreurEntree("config.livrable : « %s » invalide (%s)" % (v, ", ".join(LIVRABLES)))
        cfg["livrable"] = v
    if "vias_couture" in lu:
        cfg["vias_couture"] = gc.booleen(lu["vias_couture"], "config.vias_couture")
    s = gc.objet_json(lu.get("serigraphie", {}), "config.serigraphie")
    cs = cfg["serigraphie"]
    for cle, mini in (("graine", None), ("chaines", 1), ("mouvements", 0), ("threads", 1)):
        if cle in s:
            v = gc.entier(s[cle], "config.serigraphie." + cle)
            if mini is not None and v < mini:
                raise gc.ErreurEntree("config.serigraphie.%s : %d < %d" % (cle, v, mini))
            cs[cle] = v
    if "tailles_mm" in s:
        t = s["tailles_mm"]
        if t != "auto":
            t = [[gc.nombre(x, "config.serigraphie.tailles_mm[%d][%d]" % (i, j)) for j, x in enumerate(
                gc.liste(e, "config.serigraphie.tailles_mm[%d]" % i))] for i, e in enumerate(
                gc.liste(t, "config.serigraphie.tailles_mm"))]
            if not t or any(len(e) != 2 or min(e) <= 0 for e in t):
                raise gc.ErreurEntree("config.serigraphie.tailles_mm : « auto » ou liste de [hauteur, épaisseur] > 0")
        cs["tailles_mm"] = t
    if "garde_masque_mm" in s:
        g = s["garde_masque_mm"]
        if g is not None:
            g = gc.nombre(g, "config.serigraphie.garde_masque_mm")
            if g < 0:
                raise gc.ErreurEntree("config.serigraphie.garde_masque_mm : valeur négative")
        cs["garde_masque_mm"] = g
    if "trous_supplementaires_mm" in s:
        trous = []
        for i, e in enumerate(gc.liste(s["trous_supplementaires_mm"], "config.serigraphie.trous_supplementaires_mm")):
            ch = "config.serigraphie.trous_supplementaires_mm[%d]" % i
            e = gc.liste(e, ch)
            if len(e) != 3:
                raise gc.ErreurEntree("%s : [x, y, rayon] attendu" % ch)
            trous.append([gc.nombre(x, "%s[%d]" % (ch, j)) for j, x in enumerate(e)])
        cs["trous_supplementaires_mm"] = trous
    if "options" in s:
        cs["options"] = [gc.texte(o, "config.serigraphie.options[%d]" % i)
                         for i, o in enumerate(gc.liste(s["options"], "config.serigraphie.options"))]
    return cfg


def _options(opts):
    """``opts`` (dictionnaire, espace de noms ou None) en dictionnaire (plan du lot C, §4-1)."""
    if opts is None:
        return {}
    if isinstance(opts, dict):
        return dict(opts)
    return {k: v for k, v in vars(opts).items()}


# ---------------------------------------------------------------------------------------------------------------------
# Cuivre libre, modèles, ajouts
# ---------------------------------------------------------------------------------------------------------------------
def cuivre_libre(doc, garder_coulee=False, garder=None):
    """Index des primitives de cuivre libre à retirer : {storage: set} ; ``garder(storage, primitive)`` vrai : la
    primitive est du cuivre conservé (``cuivre_fixe`` à réseau), laissée en place."""
    out = {}
    for st in FLUX_CUIVRE_LIBRE:
        f = doc.data(st)
        if not isinstance(f, FluxPrimitives):
            continue
        s = set()
        for i, pr in enumerate(f):
            b = pr.bloc
            if b["composant"] != SANS_OBJET:
                continue
            if b["polygone"] != SANS_OBJET and garder_coulee:
                continue
            if b["drapeaux2"] & 0x02:  # keep-out
                continue
            if not est_cuivre_signal(b["couche"]):
                continue
            if isinstance(pr.sous[0], SousRegion):
                p = pr.sous[0].props
                if p.get("ISBOARDCUTOUT") == "TRUE" or p.get("KEEPOUT") == "TRUE":
                    continue
            if garder is not None and garder(st, pr):
                continue
            s.add(i)
        out[st] = s
    f = doc.data("Vias6")
    out["Vias6"] = {i for i, pr in enumerate(f or []) if pr.bloc["composant"] == SANS_OBJET
                    and not pr.bloc["drapeaux2"] & 0x02 and not (garder is not None and garder("Vias6", pr))}
    return out


def guids_bibliotheque_vias(doc):
    """{(diamètre, trou) en unités internes : guid_bibliotheque} des vias libres du fichier (bibliothèque pad/via
    de la carte, PadViaLibraryCache) ; une taille portée par plusieurs GUID n'est pas retenue."""
    out, vus = {}, set()
    for pr in doc.data("Vias6") or []:
        b = pr.bloc
        if b["composant"] != SANS_OBJET or "guid_bibliotheque" not in b.v:
            continue
        g, k = b["guid_bibliotheque"], (b["diametre"], b["trou"])
        if not g or set(str(g)) <= set("0-{}"):
            continue
        if k in out and out[k] != g:
            vus.add(k)
        out.setdefault(k, g)
    return {k: g for k, g in out.items() if k not in vus}


TOLERANCE_CONSERVE_MIL = 0.5   # cuivre_fixe en mm arrondis (§2.3) : rapprochement avec le fichier à 0,5 mil


def _donnees(o):
    d = o.get("donnees")
    if d is None or isinstance(d, dict):
        return d
    return gc.charger_json(d, ("donnees_placeur/2",))


def cuivre_conserve(donnees, ox, oy):
    """Prédicat ``garder(storage, primitive)`` du cuivre conservé : éléments de ``cuivre_fixe`` à réseau non vide
    (``cuivre_a_garder``) de ``donnees_placeur/2`` (mm, repère commun), retrouvés dans le fichier par leur
    géométrie (mil absolus) à TOLERANCE_CONSERVE_MIL près. None si rien n'est à conserver."""
    k = 1.0 / 0.0254
    pistes, vias, regions = [], [], []
    for c in (donnees or {}).get("cuivre_fixe") or []:
        if not c.get("reseau"):
            continue
        v6s = {couches.v6_depuis_nom(str(n)) for n in c.get("couches") or []}
        if c.get("type") == "piste":
            pistes.append((v6s, (c["x1"] * k + ox, c["y1"] * k + oy), (c["x2"] * k + ox, c["y2"] * k + oy)))
        elif c.get("type") == "via":
            vias.append((c["x"] * k + ox, c["y"] * k + oy))
        elif c.get("type") == "region" and c.get("points"):
            pts = [(x * k + ox, y * k + oy) for x, y in c["points"]]
            regions.append((v6s, sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts)))
    if not (pistes or vias or regions):
        return None
    t = TOLERANCE_CONSERVE_MIL

    def proche(a, b):
        return abs(a[0] - b[0]) <= t and abs(a[1] - b[1]) <= t

    def garder(st, pr):
        b = pr.bloc
        if st == "Tracks6":
            a, z = (b["x1"] / 1e4, b["y1"] / 1e4), (b["x2"] / 1e4, b["y2"] / 1e4)
            return any(b["couche"] in v and ((proche(a, p) and proche(z, q)) or (proche(a, q) and proche(z, p)))
                       for v, p, q in pistes)
        if st == "Vias6":
            return any(proche((b["x"] / 1e4, b["y"] / 1e4), p) for p in vias)
        if st in ("Regions6", "ShapeBasedRegions6") and regions:
            s0 = pr.sous[0]
            pts = [((c[1], c[2]) if s0.etendu else c) for c in (s0.contour or [])]
            if s0.etendu and len(pts) > 1:
                pts = pts[:-1]
            if not pts:
                return False
            g = (sum(q[0] for q in pts) / len(pts) / 1e4, sum(q[1] for q in pts) / len(pts) / 1e4)
            return any(b["couche"] in v and proche(g, (x, y)) for v, x, y in regions)
        return False
    return garder


def effacer_chemins(doc):
    """Efface les chemins de la machine portés par le fichier (``PcbDoc.effacer_chemins(tout=False)`` : FILENAME de
    Board6). Rend True si le fichier n'en porte plus."""
    doc.effacer_chemins(tout=False)
    return (doc.board.get("FILENAME") or "") == ""


# ---------------------------------------------------------------------------------------------------------------------
# Sérigraphie
# ---------------------------------------------------------------------------------------------------------------------
def chemin_moteur(o):
    """Moteur de sérigraphie : ``opts.moteur_serigraphie``, sinon ``chemins.chemin_moteur('serigraphie')`` du lot D
    s'il existe, sinon ``serigraphie/target/release``."""
    if o.get("moteur_serigraphie"):
        return o["moteur_serigraphie"]
    try:
        import chemins  # lot D (facultatif)
        ch = chemins.chemin_moteur("serigraphie")
        if ch:
            return str(ch)
    except (ImportError, AttributeError, OSError, ValueError, TypeError):
        pass
    return os.path.join(DOSSIER_SERI, "target", "release",
                        "serigraphie" + (".exe" if platform.system() == "Windows" else ""))


def _lancer(cmd):
    drapeaux = getattr(subprocess, "CREATE_NO_WINDOW", 0) if platform.system() == "Windows" else 0
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=drapeaux)


def calculer_serigraphie(doc, travail, cfg_seri, trous_mil, o, journal):
    """Fichiers d'entrée du moteur, puis moteur (ou résultat fourni) : rend (index, résultat, carte) ; résultat None
    si le moteur manque ou échoue (repli : carte sans repositionner les désignateurs)."""
    # trous supplémentaires déjà convertis en mil (repère du fichier) : retirés de la configuration transmise
    cfg_regles = {k: v for k, v in cfg_seri.items() if k != "trous_supplementaires_mm"}
    index, carte = seri.exporter(doc, os.path.join(travail, "entree"), trous_supplementaires=trous_mil,
                                 config=cfg_regles)
    sortie = os.path.join(travail, "serigraphie.json")
    if o.get("serigraphie_json"):
        res = _lire_resultat(o["serigraphie_json"])
        gc.ecrire_json(sortie, res)
        journal.append("sérigraphie : résultat fourni (moteur non relancé)")
        return index, res, carte
    exe = chemin_moteur(o)
    if not os.path.isfile(exe):
        return index, None, carte
    cmd = [exe, "--entree", os.path.join(travail, "entree"), "--sortie", sortie,
           "--carte", os.path.join(travail, "entree", "carte.json"),
           "--graine", str(cfg_seri["graine"]), "--chaines", str(cfg_seri["chaines"]),
           "--mouvements", str(cfg_seri["mouvements"]), "--threads", str(cfg_seri["threads"])]
    cmd += [str(x) for x in cfg_seri.get("options", [])]
    t0 = time.time()
    r = _lancer(cmd)
    if r.returncode != 0 or not os.path.isfile(sortie):
        journal.append("sérigraphie : moteur en échec (code %s)" % r.returncode)
        return index, None, carte
    with open(os.path.join(travail, "console.txt"), "w", encoding="utf-8") as f:
        f.write(gc.anonymiser_message(r.stdout or ""))
    journal.append("sérigraphie : moteur %.0f s" % (time.time() - t0))
    return index, _lire_resultat(sortie), carte


def _lire_resultat(chemin):
    import json
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise gc.ErreurEntree("%s : résultat de sérigraphie illisible (%s)" % (gc.nom_fichier(chemin),
                                                                                type(e).__name__)) from None


# ---------------------------------------------------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------------------------------------------------
def _pieces(placements):
    if isinstance(placements, list):
        return placements
    return gc.liste(gc.exiger(placements, "pieces", "placement"), "placement.pieces")


def est_dessous(side):
    return str(side or "top").lower().startswith("b")


def generer(dossier_graine, sortie, gabarit, config, sans_serigraphie=False, opts=None):
    """Fabrique le .PcbDoc ``sortie`` ; rend ``rapport_pcbdoc/1`` (§2.9) et l'écrit si ``opts["rapport"]`` est donné.

    ``opts`` (dictionnaire ou espace de noms ; plan du lot C, §4-1) : ``entree`` (obligatoire : objet ou chemin
    ``entree_carte/2``), ``donnees`` (donnees_placeur/2, objet ou chemin : cuivre conservé), ``travail``, ``rapport``, ``moteur_serigraphie``, ``serigraphie_json``, ``garder_coulee``,
    ``forcer``, ``reference``."""
    return generer_detaille(dossier_graine, sortie, gabarit, config, sans_serigraphie, opts)[0]


def generer_detaille(dossier_graine, sortie, gabarit, config, sans_serigraphie=False, opts=None):
    """Comme ``generer`` ; rend ``(rapport_pcbdoc/1, détail)``, le détail portant les comptes et le journal."""
    t0 = time.time()
    o = _options(opts)
    if not gabarit:
        raise ErreurGeneration("--gabarit obligatoire : copie de la carte importée (lue seulement)")
    ent = charger_entree(o.get("entree"))
    cfg = charger_config(config)
    if os.path.abspath(gabarit) == os.path.abspath(sortie):
        raise ErreurGeneration("la sortie ne peut pas être le gabarit (lecture seule)")
    fichier_gab = gc.nom_fichier(gabarit)
    brut = lire_octets(gabarit)
    sha = hashlib.sha256(brut).hexdigest()
    if str(_source(ent).get("sha256") or "").lower() != sha:
        raise ErreurGeneration("%s : empreinte SHA-256 différente de celle de l'entrée (%s) : ce n'est pas la carte "
                               "importée" % (fichier_gab, gc.nom_fichier(str(_source(ent).get("fichier") or "?"))))
    ox, oy = origine_absolue_mil(ent)
    an, journal = [], []
    detail = {"graine": gc.nom_fichier(os.path.abspath(dossier_graine)), "gabarit": fichier_gab,
              "journal": journal}

    placements = gc.charger_json(os.path.join(dossier_graine, "placements_routage.json"),
                                 ("placements/1", "placements/2"))
    pieces = _pieces(placements)
    ch_routage = os.path.join(dossier_graine, "routage_detaille.json")
    if os.path.exists(ch_routage):
        routage = gc.charger_json(ch_routage, ("routage/1", "routage/2"))
    else:
        # mode « placement seul » : carte placée, non routée (cuivre libre retiré, aucune piste posée)
        routage = {"format": "routage/2", "unite": "mil", "pistes": [], "vias": [], "surfaces": []}
        journal.append("routage absent : carte placée, non routée")
    ch_couture = os.path.join(dossier_graine, "vias_couture.json")
    couture = []
    if cfg["vias_couture"] and os.path.exists(ch_couture):
        couture = list(gc.charger_json(ch_couture, ("vias_couture/1", "vias_couture/2")).get("vias", []))

    doc = ouvrir_octets(brut, fichier_gab)
    v6_par_nom, ecrivibles, e_couches, _ = apparier_couches(doc, ent, fichier_gab)
    if e_couches:
        raise ErreurGeneration("; ".join(e_couches))
    D = designateurs(doc)
    par_nom = {}
    for i, n in D.items():
        if n in par_nom:
            raise ErreurGeneration("désignateur en double dans le gabarit : %s" % n)
        par_nom[n] = i

    # --- contrôles d'entrée
    inconnus = [p["designator"] for p in pieces if p["designator"] not in par_nom]
    if inconnus:
        raise ErreurGeneration("%d composants des placements absents du gabarit : %s" % (len(inconnus), inconnus[:10]))
    places = {p["designator"] for p in pieces}
    non_places = sorted(n for n in par_nom if n not in places)
    if non_places:
        an.append("%d composants du gabarit absents des placements (laissés en place) : %s"
                  % (len(non_places), non_places[:20]))
    faces = []
    for p in pieces:
        c = doc.data("Components6")[par_nom[p["designator"]]]
        if (c.get("LAYER") == "BOTTOM") != est_dessous(p.get("side")):
            faces.append("%s:%s" % (p["designator"], "dessous" if est_dessous(p.get("side")) else "dessus"))
    detail["changements_de_face"] = faces
    reseaux = {n: i for i, n in enumerate(doc.reseaux())}
    manquants = sorted({t["net_name"] for t in routage["pistes"] if t.get("net_name") and t["net_name"] not in reseaux}
                       | {v["net"] for v in list(routage["vias"]) + couture if v.get("net") and v["net"] not in reseaux})
    if manquants:
        raise ErreurGeneration("réseaux absents du gabarit : %s" % manquants[:20])
    hors = sorted({t["layer"] for t in routage["pistes"] if t["layer"] not in ecrivibles})
    if hors:
        raise ErreurGeneration("pistes sur des couches qui ne sont pas des couches de signal du gabarit : %s "
                               "(couches de signal : %s)" % (hors, ecrivibles))

    # --- 1. retrait du cuivre libre, sauf le cuivre conservé (cuivre_fixe à réseau des données) ; le GUID de
    # bibliothèque pad/via des vias libres du gabarit est noté par (diamètre, trou) pour les vias neuves de même taille
    guid_vias = guids_bibliotheque_vias(doc)
    a_sup = cuivre_libre(doc, garder_coulee=o.get("garder_coulee", False),
                         garder=cuivre_conserve(_donnees(o), ox, oy))
    n_sup = pose.supprimer_primitives(doc, a_sup)
    detail["retire"] = {st: len(s) for st, s in a_sup.items()}
    journal.append("cuivre libre retiré : " + ", ".join("%s %s" % (st, n) for st, n in n_sup.items()))

    # --- 2. pose des composants
    n_prim, anomalies_pose, inchangees = 0, [], 0
    for p in pieces:
        idx = par_nom[p["designator"]]
        x, y, r = float(p["x"]) + ox, float(p["y"]) + oy, float(p["rotation"]) % 360.0
        if pose_inchangee(doc.data("Components6")[idx], x, y, r, est_dessous(p.get("side"))):
            inchangees += 1
            continue
        n, a = pose.poser_composant(doc, idx, x, y, r, est_dessous(p.get("side")))
        n_prim += n
        anomalies_pose += ["%s : %s" % (p["designator"], x) for x in a]
    if anomalies_pose:
        msg = "changement de face / pose non sûr : " + "; ".join(anomalies_pose[:20])
        if not o.get("forcer"):
            raise ErreurGeneration(msg + " (fichier non écrit ; --forcer pour passer outre)")
        an.append(msg)
    detail["composants_poses"] = len(pieces) - inchangees
    detail["composants_inchanges"] = inchangees
    journal.append("%d composants posés (%d primitives transformées), %d inchangés"
                   % (len(pieces) - inchangees, n_prim, inchangees))

    # --- 3. pistes, vias, vias de couture et surfaces (PcbDoc.ajouter_* : sans prototype, carte neuve acceptée)
    for t in routage["pistes"]:
        doc.ajouter_piste(v6_par_nom[t["layer"]], (float(t["x1"]) + ox, float(t["y1"]) + oy),
                          (float(t["x2"]) + ox, float(t["y2"]) + oy), float(t["width"]), t.get("net_name") or None)
    vias6 = doc.data("Vias6")
    for v in list(routage["vias"]) + couture:
        i = doc.ajouter_via((float(v["x"]) + ox, float(v["y"]) + oy), float(v["size"]), float(v["hole_size"]),
                            v.get("net") or None)
        b = vias6[i].bloc
        g = guid_vias.get((b["diametre"], b["trou"]))
        if g and "guid_bibliotheque" in b.v:
            b["guid_bibliotheque"] = g
    pose.rattacher_vias_percage(doc)
    surfaces = list(routage.get("surfaces") or [])
    for z in surfaces:
        doc.ajouter_region(v6_par_nom[z["layer"]], [(float(x) + ox, float(y) + oy) for x, y in z["contour"]],
                           z.get("net") or None,
                           [[(float(x) + ox, float(y) + oy) for x, y in t] for t in z.get("trous") or []])
    detail.update(pistes_ajoutees=len(routage["pistes"]), vias_ajoutees=len(routage["vias"]),
                  vias_couture_ajoutees=len(couture), surfaces_ajoutees=len(surfaces))

    # --- 4. sérigraphie (repli : désignateurs non repositionnés)
    res = travail = None
    etat, masques = "non_faite", 0
    sox, soy = seri.origine(doc)
    trous_mil = [[x / gc.MIL + ox - sox, y / gc.MIL + oy - soy, r / gc.MIL]
                 for x, y, r in cfg["serigraphie"]["trous_supplementaires_mm"]]
    if not sans_serigraphie:
        travail = o.get("travail") or os.path.splitext(sortie)[0] + "_serigraphie"
        index, res, carte = calculer_serigraphie(doc, travail, cfg["serigraphie"], trous_mil, o, journal)
        if res is None:
            an.append("sérigraphie non faite : moteur absent ou en échec ; désignateurs laissés à leur place")
        else:
            bilan, a_sup_seri, nouveaux = seri.appliquer(doc, res, index)
            pose.supprimer_primitives(doc, a_sup_seri)
            for pr in nouveaux:
                pose.ajouter_primitive(doc, "Tracks6", pr)
            an += ["sérigraphie : %s" % x for x in bilan.pop("anomalies")]
            masques = int(bilan.get("textes_masques", 0))
            detail["serigraphie"] = bilan
    else:
        journal.append("sérigraphie non calculée (--sans-serigraphie)")

    # --- 5. polygones, chemins
    polys = doc.polygones()
    if polys:
        an.append("%d polygone(s) à recouler dans Altium (Outils > Polygones > Recouler tout)" % len(polys))
    chemins = effacer_chemins(doc)

    # --- 6. écriture, relecture, contrôles
    ecarts = pose.verifier_guids(doc)
    if ecarts:
        raise RuntimeError("PrimitiveGuids incohérent : " + "; ".join(ecarts))
    dossier_sortie = os.path.dirname(os.path.abspath(sortie))
    os.makedirs(dossier_sortie, exist_ok=True)
    temporaire = os.path.abspath(sortie) + ".tmp"
    doc.enregistrer(temporaire)
    os.replace(temporaire, sortie)
    ctl = verifier_sortie(sortie, pieces, routage, couture, ox, oy, v6_par_nom, res)
    detail["controles"] = ctl
    if res is not None:
        cs = controle_serigraphie(sortie, travail, trous_mil)
        detail["controle_serigraphie"] = cs
        etat = "coherent" if cs.get("coherent") else "incoherent"
    if o.get("reference"):
        detail["comparaison_reference"] = comparer_reference(sortie, o["reference"])
    conflits = ctl["isolement_cuivre_empreintes"]["conflits"]
    if conflits:
        an.append("%d conflit(s) d'isolement entre le routage et des surfaces de cuivre d'empreinte (régions hors "
                  "pastilles) : la graine est en défaut" % len(conflits))
    rapport = {"format": FORMAT_RAPPORT, "fichier": gc.nom_fichier(sortie), "livrable": cfg["livrable"],
               "controles": {"erreurs": list(ctl["erreurs"]), "anomalies": an},
               "conflits_isolement": len(conflits), "serigraphie": {"etat": etat, "masques": masques},
               "polygones_a_recouler": len(polys), "chemins_effaces": bool(chemins and ctl.get("chemins_effaces")),
               "duree_s": round(time.time() - t0, 1)}
    if o.get("rapport"):
        gc.ecrire_json(o["rapport"], rapport)
    return rapport, detail


# ---------------------------------------------------------------------------------------------------------------------
def verifier_sortie(chemin, pieces, routage, couture, ox, oy, v6_par_nom, seri_res=None, tol=TOLERANCE_MIL):
    """Relit le fichier écrit et contrôle composants, pistes, vias, désignateurs, GUID, perçages et chemins."""
    doc = PcbDoc.ouvrir(chemin)
    res = {"relu": True, "erreurs": []}
    err = res["erreurs"]
    D = {n: i for i, n in designateurs(doc).items()}
    comp = doc.data("Components6")
    if seri_res is not None:
        sox, soy = seri.origine(doc)
        txt = {pr.bloc["composant"]: pr.bloc for pr in doc.data("Texts6")
               if pr.bloc.get("est_designateur") and pr.bloc["composant"] != SANS_OBJET}
        n_ok = n_tot = 0
        for t in seri_res["textes"]:
            i = D.get(t["component"])
            if i is None:
                continue
            n_tot += 1
            c = comp[i]
            if t["hidden"]:
                ok = c.get("NAMEON") == "FALSE"
            else:
                b = txt[i]
                ok = (c.get("NAMEON") == "TRUE" and c.get("NAMEAUTOPOSITION") in (None, "0")
                      and abs(b["x"] / 1e4 - sox - t["x"]) < tol and abs(b["y"] / 1e4 - soy - t["y"]) < tol
                      and abs(b["hauteur"] / 1e4 - t["height"]) < tol
                      and abs(b["epaisseur_trait"] / 1e4 - t["stroke_width"]) < tol
                      and abs((b["rotation"] - t["rotation"] + 180) % 360 - 180) < 1e-6
                      and bool(b["miroir"]) == bool(t["mirror"])
                      and couches.NOMS_V6.get(b["couche"]) == seri.FACE_DU_JETON.get(t["layer"]) and b["type_police"] == 0)
            n_ok += ok
            if not ok:
                err.append("désignateur %s non conforme" % t["component"])
        res["designateurs_conformes"] = "%d/%d" % (n_ok, n_tot)
    n_ok = 0
    for p in pieces:
        c = comp[D[p["designator"]]]
        ok = (abs(valeur_mil(c["X"]) - ox - float(p["x"])) < tol and abs(valeur_mil(c["Y"]) - oy - float(p["y"])) < tol
              and abs(((float(c["ROTATION"]) - float(p["rotation"])) + 180) % 360 - 180) < 1e-6
              and (c.get("LAYER") == "BOTTOM") == est_dessous(p.get("side")))
        n_ok += ok
        if not ok:
            err.append("composant %s mal posé" % p["designator"])
    res["composants_conformes"] = "%d/%d" % (n_ok, len(pieces))

    def interne(v, o_=0.0):  # même conversion que l'écriture (mil du repère commun -> unités internes)
        return int(round((float(v) + o_) * 1e4))

    def cle_piste(v6, a, b, w, net):
        return (v6, min(a, b), max(a, b), w, net)
    lues = {}
    for pr in doc.data("Tracks6"):
        b = pr.bloc
        if b["composant"] == SANS_OBJET and b["polygone"] == SANS_OBJET and est_cuivre_signal(b["couche"]):
            k = cle_piste(b["couche"], (b["x1"], b["y1"]), (b["x2"], b["y2"]), b["largeur"], doc.nom_reseau(b["reseau"]))
            lues[k] = lues.get(k, 0) + 1
    attendues = {}
    for t in routage["pistes"]:
        k = cle_piste(v6_par_nom[t["layer"]], (interne(t["x1"], ox), interne(t["y1"], oy)),
                      (interne(t["x2"], ox), interne(t["y2"], oy)), interne(t["width"]), t.get("net_name"))
        attendues[k] = attendues.get(k, 0) + 1
    manque = sum(max(0, n - lues.get(k, 0)) for k, n in attendues.items())
    trop = sum(max(0, n - attendues.get(k, 0)) for k, n in lues.items())
    res["pistes"] = {"attendues": len(routage["pistes"]), "lues": sum(lues.values()), "manquantes": manque,
                     "en_trop": trop}
    if manque or trop:
        err.append("pistes : %d manquantes, %d en trop" % (manque, trop))

    lues = {}
    for pr in doc.data("Vias6"):
        b = pr.bloc
        k = (b["x"], b["y"], b["diametre"], b["trou"], doc.nom_reseau(b["reseau"]))
        lues[k] = lues.get(k, 0) + 1
    attendues = {}
    for v in list(routage["vias"]) + list(couture):
        k = (interne(v["x"], ox), interne(v["y"], oy), interne(v["size"]), interne(v["hole_size"]), v.get("net"))
        attendues[k] = attendues.get(k, 0) + 1
    manque = sum(max(0, n - lues.get(k, 0)) for k, n in attendues.items())
    trop = sum(max(0, n - attendues.get(k, 0)) for k, n in lues.items())
    res["vias"] = {"attendues": sum(attendues.values()), "lues": sum(lues.values()), "manquantes": manque,
                   "en_trop": trop}
    if manque or trop:
        err.append("vias : %d manquantes, %d en trop" % (manque, trop))
    g = pose.verifier_guids(doc)
    res["guids"] = "coherents" if not g else g
    err += g
    dm = doc.data("DrillManager")
    lst = sorted(i for p, l in dm.groupes for i in l[1])
    res["percage_vias"] = "coherent" if lst == list(range(len(doc.data("Vias6")))) else "INCOHERENT"
    if lst != list(range(len(doc.data("Vias6")))):
        err.append("DrillManager : liste des vias incohérente")
    res["comptes"] = {st: len(doc.data(st)) for st in FLUX_PRIMITIVES_GEO if isinstance(doc.data(st), FluxPrimitives)}
    res["chemins_effaces"] = doc.board.get("FILENAME") in (None, "")
    if not res["chemins_effaces"]:
        err.append("FILENAME de Board6 non effacé")
    iso = isolement_defaut(doc)
    res["isolement_cuivre_empreintes"] = {"isolement_mil": iso, "conflits": controle_cuivre_empreintes(doc, ox, oy, iso)}
    res["aller_retour"] = all(doc.flux[c].encoder() == n.donnees for c, n in doc.racine.flux())
    if not res["aller_retour"]:
        err.append("aller-retour du fichier écrit non identique")
    return res


# ---------------------------------------------------------------------------------------------------------------------
def isolement_defaut(doc):
    """Isolement de la règle Clearance la moins prioritaire (règle par défaut), en mil."""
    regles = [r for r in doc.regles() if r["type"] == "Clearance" and r["active"]]
    if not regles:
        return None
    r = max(regles, key=lambda r: r["priorite"])
    return valeur_mil(r["proprietes"].get("GAP") or r["proprietes"].get("GENERICCLEARANCE"))


def _d_pt_seg(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _seg_coupe(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def _dedans(p, poly):
    x, y = p
    n, ok = len(poly), False
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            ok = not ok
    return ok


def _d_seg_poly(a, b, poly):
    if _dedans(a, poly) or _dedans(b, poly):
        return 0.0
    m = float("inf")
    n = len(poly)
    for i in range(n):
        c, d = poly[i], poly[(i + 1) % n]
        if _seg_coupe(a, b, c, d):
            return 0.0
        m = min(m, _d_pt_seg(a, c, d), _d_pt_seg(b, c, d), _d_pt_seg(c, a, b), _d_pt_seg(d, a, b))
    return m


def controle_cuivre_empreintes(doc, ox, oy, isolement):
    """Isolement entre les surfaces de cuivre propres à une empreinte (régions de composant sur une couche de cuivre,
    avec un réseau, que le routeur ne voit pas) et le cuivre libre (pistes, vias) d'un autre réseau ; rend les
    conflits (mil, repère commun)."""
    regions = []
    for pr in doc.data("Regions6"):
        b = pr.bloc
        s0 = pr.sous[0]
        if b["composant"] == SANS_OBJET or not est_cuivre_signal(b["couche"]) or not s0.contour or b["drapeaux2"] & 2:
            continue
        if s0.props.get("KIND", "0") != "0":
            continue
        poly = [(x / 1e4 - ox, y / 1e4 - oy) for x, y in s0.contour]
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        regions.append((b["couche"], b["reseau"], b["composant"], poly, (min(xs), min(ys), max(xs), max(ys))))
    if not regions or isolement is None:
        return []
    D = designateurs(doc)
    out = []

    def proche(bb, x0, y0, x1, y1, m):
        return not (x1 < bb[0] - m or x0 > bb[2] + m or y1 < bb[1] - m or y0 > bb[3] + m)
    for pr in doc.data("Tracks6"):
        b = pr.bloc
        if b["composant"] != SANS_OBJET or b["polygone"] != SANS_OBJET:
            continue
        a = (b["x1"] / 1e4 - ox, b["y1"] / 1e4 - oy)
        c = (b["x2"] / 1e4 - ox, b["y2"] / 1e4 - oy)
        w = b["largeur"] / 1e4
        for (lay, net, comp, poly, bb) in regions:
            if lay != b["couche"] or net == b["reseau"]:
                continue
            if not proche(bb, min(a[0], c[0]), min(a[1], c[1]), max(a[0], c[0]), max(a[1], c[1]), w / 2 + isolement):
                continue
            g = _d_seg_poly(a, c, poly) - w / 2
            if g < isolement - 1e-6:
                out.append({"region": "%s %s %s" % (D.get(comp), doc.nom_reseau(net), couches.NOMS_V6[lay]),
                            "objet": "piste %s %g mil %s-%s" % (doc.nom_reseau(b["reseau"]), w, a, c),
                            "isolement_mil": round(g, 3)})
    for pr in doc.data("Vias6"):
        b = pr.bloc
        if b["composant"] != SANS_OBJET:
            continue
        p = (b["x"] / 1e4 - ox, b["y"] / 1e4 - oy)
        r = b["diametre"] / 2e4
        for (lay, net, comp, poly, bb) in regions:
            if net == b["reseau"] or not proche(bb, p[0], p[1], p[0], p[1], r + isolement):
                continue
            g = _d_seg_poly(p, p, poly) - r
            if g < isolement - 1e-6:
                out.append({"region": "%s %s %s" % (D.get(comp), doc.nom_reseau(net), couches.NOMS_V6[lay]),
                            "objet": "via %s %s" % (doc.nom_reseau(b["reseau"]), p), "isolement_mil": round(g, 3)})
    return out


def controle_serigraphie(sortie, travail, trous_mil):
    """Contrôle indépendant (``drc_serigraphie.main`` dans le processus, géométrie Python) : la sérigraphie du fichier relu (désignateurs
    cachés omis) doit avoir autant de violations sérigraphie / pastille que le résultat du moteur appliqué à son
    entrée."""
    doc = PcbDoc.ouvrir(sortie)
    relu = os.path.join(travail, "relu")
    seri.exporter(doc, relu, trous_supplementaires=trous_mil, sans_caches=True)
    out = {}
    for nom, args in (("moteur", ["--entree", os.path.join(travail, "entree"), "--apres",
                                  os.path.join(travail, "serigraphie.json")]),
                      ("fichier_relu", ["--entree", relu])):
        js = os.path.join(travail, "drc_%s.json" % nom)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = drc_serigraphie.main(args + ["--json", js])
        if code != 0 or not os.path.exists(js):
            out[nom] = "drc_serigraphie en échec (code %s)" % code
            continue
        v = _lire_resultat(js).get("violations", [])
        compte = {}
        for x in v:
            compte[x.get("type", "?")] = compte.get(x.get("type", "?"), 0) + 1
        out[nom] = {"violations_serigraphie_pastille": len(v), "par_type": compte}
    a, b = out.get("moteur"), out.get("fichier_relu")
    out["coherent"] = isinstance(a, dict) and isinstance(b, dict) and a["par_type"] == b["par_type"]
    return out


def comparer_reference(sortie, reference):
    import differentiel
    return differentiel.comparer_apparie(sortie, reference, resume=True)


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------
POSITIONNELS = (gc.Positionnel("dossier", "DOSSIER", "dossier de la graine (placements, routage, couture)", False),
                gc.Positionnel("sortie_carte", "SORTIE.PcbDoc", "carte produite", False))
OPTIONS = (
    gc.Option("--gabarit", "copie de la carte importée (lue seulement ; son SHA-256 = entree.source.sha256)",
              meta="CARTE.PcbDoc"),
    gc.Option("--entree", "entrée entree_carte/2 de la carte (obligatoire)", meta="E.json"),
    gc.Option("--config", "configuration config_pcbdoc/2 (défauts si absente)", meta="C.json"),
    gc.Option("--controler-gabarit", "contrôle seul du gabarit (controle_gabarit/1), avec --entree et --sortie",
              meta="CARTE.PcbDoc"),
    gc.Option("--sortie", "fichier controle_gabarit/1 écrit par --controler-gabarit", meta="G.json"),
    gc.Option("--sans-serigraphie", "ne pas repositionner les désignateurs", nargs=0),
    gc.Option("--travail", "dossier de travail de la sérigraphie (défaut : <SORTIE>_serigraphie)", meta="DOSSIER"),
    gc.Option("--rapport", "fichier rapport_pcbdoc/1 à écrire", meta="R.json"),
    gc.Option("--moteur-serigraphie", "exécutable du moteur de sérigraphie", meta="EXE"),
    gc.Option("--donnees", "donnees_placeur/2 : cuivre_fixe à réseau conservé dans la carte", meta="D.json"),
    gc.Option("--serigraphie-json", "résultat du moteur déjà calculé (moteur non relancé)", meta="F.json"),
    gc.Option("--garder-coulee", "garder le cuivre coulé (périmé) des polygones", nargs=0),
    gc.Option("--reference", ".PcbDoc de référence à comparer champ par champ", meta="REF.PcbDoc"),
    gc.Option("--forcer", "écrire malgré une pose non sûre (déconseillé)", nargs=0),
)
DESCRIPTION = "Carte .PcbDoc d'une graine (rapport_pcbdoc/1) ou contrôle d'un gabarit (controle_gabarit/1)."


def _principal(a):
    if a["controler_gabarit"]:
        if a["dossier"] or a["sortie_carte"] or a["gabarit"]:
            raise gc.ErreurEntree("--controler-gabarit : ni DOSSIER, ni SORTIE.PcbDoc, ni --gabarit")
        if not a["entree"]:
            raise gc.ErreurEntree("--entree obligatoire avec --controler-gabarit")
        if not a["sortie"]:
            raise gc.ErreurEntree("--sortie obligatoire avec --controler-gabarit")
        r = controler_gabarit(a["controler_gabarit"], a["entree"])
        gc.ecrire_json(a["sortie"], r)
        gc.afficher("%s : %s ; %d erreur(s), %d avertissement(s) ; couches écrivibles : %s ; %d pièce(s) non "
                    "retournable(s)" % (r["fichier"], "utilisable" if r["utilisable"] else "INUTILISABLE",
                                       len(r["erreurs"]), len(r["avertissements"]), ", ".join(r["couches_ecrivibles"]),
                                       len(r["non_retournables"])))
        for e in r["erreurs"]:
            gc.afficher("  ! " + e)
        return gc.CODE_SUCCES if r["utilisable"] else gc.CODE_NEGATIF
    if not a["dossier"] or not a["sortie_carte"]:
        raise gc.ErreurEntree("arguments obligatoires absents : DOSSIER SORTIE.PcbDoc (--help pour l'aide)")
    if not a["gabarit"]:
        raise gc.ErreurEntree("--gabarit obligatoire : copie de la carte importée (lue seulement)")
    if not a["entree"]:
        raise gc.ErreurEntree("--entree obligatoire : entrée entree_carte/2 de la carte")
    if a["sortie"]:
        raise gc.ErreurEntree("--sortie : seulement avec --controler-gabarit (la carte produite est SORTIE.PcbDoc)")
    r = generer(a["dossier"], a["sortie_carte"], a["gabarit"], a["config"], a["sans_serigraphie"], a)
    c = r["controles"]
    gc.afficher("%s : %d erreur(s) de contrôle, %d anomalie(s) ; sérigraphie %s ; %d polygone(s) à recouler ; %s s"
                % (r["fichier"], len(c["erreurs"]), len(c["anomalies"]), r["serigraphie"]["etat"],
                   r["polygones_a_recouler"], gc.nombre_fr(r["duree_s"])))
    for e in c["erreurs"]:
        gc.afficher("  ! " + e)
    for e in c["anomalies"]:
        gc.afficher("  - " + e)
    return gc.CODE_NEGATIF if c["erreurs"] else gc.CODE_SUCCES


def main(argv=None):
    """Ligne de commande (§5.2) ; rend le code de sortie (§1.4) sans jamais quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, PROGRAMME, DESCRIPTION, POSITIONNELS)


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
