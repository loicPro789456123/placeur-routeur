"""Carte KiCad (.kicad_pcb) d'une graine du placeur-routeur, et contrôle d'un gabarit KiCad.

Contrôle du gabarit (``controle_gabarit/1``, §2.9) :

    python generer_kicad.py --controler-gabarit CARTE.kicad_pcb --entree E.json --sortie G.json

    codes : 0 gabarit utilisable, 1 inutilisable (erreurs listées), 2 entrée invalide, 4 erreur interne.

Génération (§5.2, ``DOSSIER SORTIE.kicad_pcb --gabarit CARTE.kicad_pcb --entree E.json [--config C.json]``) :
**écrite à l'étape C8 du lot C** ; d'ici là, ``generer`` est refusée explicitement (code 4).

Le module contient le lecteur et l'écrivain S-expression du format KiCad (chaînes entre guillemets, échappements
``\\"``, ``\\\\``, ``\\n``) : ``lire_sexpr(texte)``, ``ecrire_sexpr(arbre)``, ``ecrire_fichier(chemin, arbre)``.
Un nœud est une liste Python ; un atome non cité (symbole ou nombre, gardé tel qu'écrit) est un ``str`` ; une
chaîne citée est un ``Chaine`` (sous-classe de ``str``), réécrite entre guillemets.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import geom_commune as gc  # noqa: E402

PROGRAMME = "generer_kicad.py"
FORMAT_ENTREE = "entree_carte/2"
FORMAT_CONTROLE = "controle_gabarit/1"
TYPE_SOURCE = "kicad_pcb"
VERSION_MIN = 20211014          # KiCad 6 (§2.2 : version de fichier KiCad < 20211014 refusée)
COUCHE_CONTOUR = "Edge.Cuts"
CLES_CONTROLE = ("format", "fichier", "sha256", "utilisable", "erreurs", "avertissements", "couches_ecrivibles",
                 "non_retournables", "modeles")
_ETAPE_GENERATION = "C8"


# ---------------------------------------------------------------------------------------------------------------------
# S-expressions
# ---------------------------------------------------------------------------------------------------------------------
class Chaine(str):
    """Chaîne citée d'un fichier KiCad (réécrite entre guillemets)."""

    __slots__ = ()


_JETON = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_ECHAPPEMENTS_LUS = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}
_A_ECHAPPER = re.compile(r'[\\"\n\r\t]')
_ECHAPPEMENTS_ECRITS = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_ATOME_SUR = re.compile(r'^[^\s()"\\]+$')


def _desechapper(texte):
    if "\\" not in texte:
        return texte
    return re.sub(r"\\(.)", lambda m: _ECHAPPEMENTS_LUS.get(m.group(1), m.group(1)), texte, flags=re.S)


def lire_sexpr(texte, nom="fichier"):
    """Arbre de la première expression de ``texte`` (une seule expression permise, blancs autour acceptés).
    Syntaxe invalide : ``ErreurEntree`` qui cite ``nom`` et la position."""
    if texte.startswith("﻿"):
        texte = texte[1:]
    pile, racine, pos, n = [], None, 0, len(texte)
    while pos < n:
        m = _JETON.match(texte, pos)
        if m is None:
            if texte[pos:].strip() == "":
                break
            raise gc.ErreurEntree("%s : S-expression invalide (caractère %d)" % (nom, pos))
        pos = m.end()
        if m.group(1):
            if racine is not None and not pile:
                raise gc.ErreurEntree("%s : texte après la fin de l'expression (caractère %d)" % (nom, m.start(1)))
            nouveau = []
            if pile:
                pile[-1].append(nouveau)
            else:
                racine = nouveau
            pile.append(nouveau)
        elif m.group(2):
            if not pile:
                raise gc.ErreurEntree("%s : parenthèse fermante en trop (caractère %d)" % (nom, m.start(2)))
            pile.pop()
        else:
            if not pile:
                raise gc.ErreurEntree("%s : atome hors de toute expression (caractère %d)" % (nom, m.start()))
            if m.group(3) is not None:
                pile[-1].append(Chaine(_desechapper(m.group(3))))
            else:
                pile[-1].append(m.group(4))
    if pile:
        raise gc.ErreurEntree("%s : expression non fermée (%d parenthèse(s) manquante(s))" % (nom, len(pile)))
    if racine is None:
        raise gc.ErreurEntree("%s : aucune expression" % nom)
    return racine


def _atome(a):
    if isinstance(a, Chaine) or not _ATOME_SUR.match(a):
        return '"%s"' % _A_ECHAPPER.sub(lambda m: _ECHAPPEMENTS_ECRITS[m.group(0)], a)
    return a


def ecrire_sexpr(noeud, indentation=0):
    """Texte d'un arbre : un nœud dont tous les enfants sont des atomes tient sur une ligne ; sinon chaque sous-nœud
    sur sa ligne, indenté de deux espaces (comme KiCad)."""
    if not isinstance(noeud, list):
        return _atome(noeud)
    if all(not isinstance(e, list) for e in noeud):
        return "(" + " ".join(_atome(e) for e in noeud) + ")"
    tete, morceaux = [], []
    i = 0
    while i < len(noeud) and not isinstance(noeud[i], list):
        tete.append(_atome(noeud[i]))
        i += 1
    marge = "  " * (indentation + 1)
    for e in noeud[i:]:
        morceaux.append(marge + ecrire_sexpr(e, indentation + 1))
    return "(" + " ".join(tete) + "\n" + "\n".join(morceaux) + "\n" + "  " * indentation + ")"


def ecrire_fichier(chemin, arbre):
    """Écrit l'arbre (UTF-8 sans BOM, LF) de façon atomique (``.tmp`` puis ``os.replace``)."""
    octets = (ecrire_sexpr(arbre) + "\n").encode("utf-8")
    temporaire = os.fspath(chemin) + ".tmp"
    nom = gc.nom_fichier(chemin)
    try:
        with open(temporaire, "wb") as f:
            f.write(octets)
        for essai in range(5):
            try:
                os.replace(temporaire, chemin)
                return
            except PermissionError:
                if essai == 4:
                    raise
                time.sleep(0.2)
    except OSError as e:
        try:
            os.remove(temporaire)
        except OSError:
            pass
        raise gc.ErreurEntree("%s : écriture impossible (%s)" % (nom, type(e).__name__)) from None


def enfants(noeud, tete):
    """Sous-nœuds directs de ``noeud`` dont le premier atome est ``tete``."""
    return [e for e in noeud if isinstance(e, list) and e and e[0] == tete]


def enfant(noeud, tete):
    lst = enfants(noeud, tete)
    return lst[0] if lst else None


def _parcourir(noeud):
    yield noeud
    for e in noeud:
        if isinstance(e, list):
            yield from _parcourir(e)


# ---------------------------------------------------------------------------------------------------------------------
# Couches et empreintes
# ---------------------------------------------------------------------------------------------------------------------
def _rang_cuivre(nom):
    """Rang de tri d'une couche de cuivre KiCad (``F.Cu`` 0, ``In<n>.Cu`` n, ``B.Cu`` en dernier) ou None."""
    if not isinstance(nom, str) or not nom.endswith(".Cu"):
        return None
    if nom == "F.Cu":
        return 0
    if nom == "B.Cu":
        return 10 ** 6
    m = re.fullmatch(r"In(\d+)\.Cu", nom)
    return int(m.group(1)) if m else None


def est_interne(nom):
    r = _rang_cuivre(nom)
    return r is not None and 0 < r < 10 ** 6


def couches_cuivre(arbre):
    """Couches de cuivre du fichier, de haut en bas : ``[{"nom_fichier", "type_kicad"}]``."""
    lst = enfant(arbre, "layers")
    out = []
    for e in (lst[1:] if lst else []):
        if isinstance(e, list) and len(e) >= 2 and _rang_cuivre(e[1]) is not None:
            out.append({"nom_fichier": str(e[1]), "type_kicad": str(e[2]) if len(e) > 2 else None})
    out.sort(key=lambda c: _rang_cuivre(c["nom_fichier"]))
    return out


def designateur(empreinte):
    """Référence d'une empreinte : ``(property "Reference" …)`` (KiCad 8) ou ``(fp_text reference …)``."""
    for p in enfants(empreinte, "property"):
        if len(p) >= 3 and p[1] == "Reference":
            return str(p[2])
    for t in enfants(empreinte, "fp_text"):
        if len(t) >= 3 and t[1] == "reference":
            return str(t[2])
    return None


def empreintes(arbre):
    return enfants(arbre, "footprint") + enfants(arbre, "module")


def _couches_citees(noeud):
    """Noms de couches cités par les nœuds ``(layer …)`` et ``(layers …)`` d'un sous-arbre."""
    out = []
    for n in _parcourir(noeud):
        if n and n[0] in ("layer", "layers"):
            out += [str(x) for x in n[1:] if not isinstance(x, list)]
    return out


def raison_non_retournable(empreinte):
    """Raison pour laquelle le retournement d'une empreinte n'est pas sûr (plan du lot C, §4-14), sinon None : cuivre
    sur une couche interne, contour ``Edge.Cuts`` dans l'empreinte."""
    raisons = []
    internes = sorted({c for c in _couches_citees(empreinte[1:]) if est_interne(c)})
    if internes:
        raisons.append("cuivre sur couche interne %s" % ", ".join(internes))
    if COUCHE_CONTOUR in _couches_citees(empreinte[1:]):
        raisons.append("contour %s dans l'empreinte" % COUCHE_CONTOUR)
    return "; ".join(raisons) or None


def _contour_present(arbre):
    for e in arbre[1:]:
        if isinstance(e, list) and e and str(e[0]).startswith("gr_") and COUCHE_CONTOUR in _couches_citees(e):
            return True
    return any(COUCHE_CONTOUR in _couches_citees(f) for f in empreintes(arbre))


# ---------------------------------------------------------------------------------------------------------------------
# Contrôle du gabarit
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


def lire_arbre(brut, nom):
    try:
        texte = brut.decode("utf-8")
    except UnicodeDecodeError as e:
        raise gc.ErreurEntree("%s : texte non UTF-8 (octet %d)" % (nom, e.start)) from None
    arbre = lire_sexpr(texte, nom)
    if not arbre or arbre[0] != "kicad_pcb":
        raise gc.ErreurEntree("%s : ce n'est pas une carte KiCad (racine « kicad_pcb » attendue)" % nom)
    return arbre


def charger_entree(entree):
    if entree is None:
        raise gc.ErreurEntree("--entree obligatoire : entrée entree_carte/2 de la carte")
    if isinstance(entree, (str, os.PathLike)):
        return gc.charger_json(entree, (FORMAT_ENTREE,))
    return gc.exiger_format(entree, FORMAT_ENTREE, "entree")


def _couches_entree(entree):
    carte = gc.objet_json(gc.exiger(entree, "carte", "entree"), "entree.carte")
    res = []
    for k, c in enumerate(gc.liste(gc.exiger(carte, "couches", "entree.carte"), "entree.carte.couches")):
        ch = "entree.carte.couches[%d]" % k
        gc.objet_json(c, ch)
        res.append({"nom": gc.texte(gc.exiger(c, "nom", ch), ch + ".nom"),
                    "rang": gc.entier(gc.exiger(c, "rang", ch), ch + ".rang"), "nom_fichier": c.get("nom_fichier")})
    res.sort(key=lambda c: c["rang"])
    if [c["rang"] for c in res] != list(range(len(res))):
        raise gc.ErreurEntree("entree.carte.couches : rangs non consécutifs depuis 0")
    return res


def controler_gabarit(gabarit, entree):
    """Contrôle d'un gabarit KiCad (``controle_gabarit/1``, §2.9) : rend l'objet, n'écrit rien.

    Version de fichier ≥ 20211014 ; couches de cuivre (``F.Cu``, ``In<n>.Cu``, ``B.Cu``) appariées par rang à celles de
    l'entrée ; empreintes non retournables : cuivre sur couche interne, contour ``Edge.Cuts`` d'empreinte. ``modeles`` :
    tous vrais (le générateur KiCad crée pistes, vias et zones sans modèle)."""
    ent = charger_entree(entree)
    fichier = gc.nom_fichier(gabarit)
    brut = lire_octets(gabarit)
    sha = hashlib.sha256(brut).hexdigest()
    src = gc.objet_json(gc.exiger(ent, "source", "entree"), "entree.source")
    erreurs, avert = [], []
    if src.get("type") not in (None, TYPE_SOURCE):
        erreurs.append("entree.source.type : « %s » ; ce générateur n'écrit que des cartes %s"
                       % (src.get("type"), TYPE_SOURCE))
    if str(src.get("sha256") or "").lower() != sha:
        erreurs.append("%s : empreinte SHA-256 différente de celle de l'entrée (%s) : ce n'est pas la carte importée"
                       % (fichier, gc.nom_fichier(str(src.get("fichier") or "?"))))
    if src.get("origine_fichier_mm") is None:
        erreurs.append("entree.source.origine_fichier_mm absente : origine du repère commun inconnue")
    arbre = lire_arbre(brut, fichier)
    v = enfant(arbre, "version")
    version = None
    if v is not None and len(v) >= 2:
        try:
            version = int(str(v[1]))
        except ValueError:
            version = None
    if version is None:
        erreurs.append("%s : version de fichier absente ou illisible" % fichier)
    elif version < VERSION_MIN:
        erreurs.append("%s : version de fichier %d antérieure à %d (KiCad 6) : à réenregistrer avec KiCad 6 ou plus "
                       "récent" % (fichier, version, VERSION_MIN))
    gab = couches_cuivre(arbre)
    ent_couches = _couches_entree(ent)
    ecrivibles = []
    if not gab:
        erreurs.append("%s : aucune couche de cuivre" % fichier)
    elif len(gab) != len(ent_couches):
        erreurs.append("%s : %d couches de cuivre dans le fichier, %d dans l'entrée" % (fichier, len(gab),
                                                                                      len(ent_couches)))
    else:
        for r, (g, e) in enumerate(zip(gab, ent_couches)):
            if e["nom_fichier"] not in (None, g["nom_fichier"]):
                erreurs.append("couche de rang %d : « %s » dans le fichier, « %s » dans l'entrée"
                               % (r, g["nom_fichier"], e["nom_fichier"]))
            else:
                ecrivibles.append(e["nom"])
    if not _contour_present(arbre):
        erreurs.append("%s : contour de carte (%s) absent" % (fichier, COUCHE_CONTOUR))
    vus, doubles, non_retournables = set(), [], {}
    for k, f in enumerate(empreintes(arbre)):
        d = designateur(f) or "__piece_%d" % k
        if d in vus:
            doubles.append(d)
        vus.add(d)
        raison = raison_non_retournable(f)
        if raison:
            non_retournables[d] = raison
    if doubles:
        erreurs.append("%s : désignateurs en double : %s" % (fichier, ", ".join(sorted(set(doubles))[:20])))
    noms_entree = []
    for k, c in enumerate(gc.liste(ent.get("composants", []), "entree.composants")):
        ch = "entree.composants[%d]" % k
        noms_entree.append(gc.texte(gc.exiger(gc.objet_json(c, ch), "designateur", ch), ch + ".designateur"))
    absents = sorted(set(noms_entree) - vus)
    if absents:
        erreurs.append("%s : %d pièces de l'entrée absentes du gabarit : %s"
                       % (fichier, len(absents), ", ".join(absents[:20])))
    en_trop = sorted(vus - set(noms_entree))
    if en_trop:
        avert.append("%d pièces du gabarit absentes de l'entrée : %s" % (len(en_trop), ", ".join(en_trop[:20])))
    return {"format": FORMAT_CONTROLE, "fichier": fichier, "sha256": sha, "utilisable": not erreurs,
            "erreurs": erreurs, "avertissements": avert, "couches_ecrivibles": ecrivibles,
            "non_retournables": non_retournables, "modeles": {"via": True, "piste": True, "region": True}}


# ---------------------------------------------------------------------------------------------------------------------
# Génération (étape C8)
# ---------------------------------------------------------------------------------------------------------------------
class GenerationNonDisponible(NotImplementedError):
    """La génération KiCad n'est pas encore écrite (étape C8 du lot C)."""


def generer(dossier_graine, sortie, gabarit, config, opts=None):
    """Carte KiCad d'une graine (``rapport_pcbdoc/1``, §5.2). **Écrite à l'étape C8** : refusée explicitement d'ici
    là (``GenerationNonDisponible``, code 4 en ligne de commande)."""
    raise GenerationNonDisponible("generer_kicad.generer : génération KiCad écrite à l'étape %s du lot C"
                                  % _ETAPE_GENERATION)


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------
POSITIONNELS = (gc.Positionnel("dossier", "DOSSIER", "dossier de la graine", False),
                gc.Positionnel("sortie_carte", "SORTIE.kicad_pcb", "carte produite", False))
OPTIONS = (
    gc.Option("--gabarit", "copie de la carte importée (lue seulement)", meta="CARTE.kicad_pcb"),
    gc.Option("--entree", "entrée entree_carte/2 de la carte (obligatoire)", meta="E.json"),
    gc.Option("--config", "configuration config_pcbdoc/2", meta="C.json"),
    gc.Option("--controler-gabarit", "contrôle seul du gabarit (controle_gabarit/1), avec --entree et --sortie",
              meta="CARTE.kicad_pcb"),
    gc.Option("--sortie", "fichier controle_gabarit/1 écrit par --controler-gabarit", meta="G.json"),
)
DESCRIPTION = "Carte .kicad_pcb d'une graine (étape C8) ou contrôle d'un gabarit KiCad (controle_gabarit/1)."


def _principal(a):
    if a["controler_gabarit"]:
        if a["dossier"] or a["sortie_carte"] or a["gabarit"]:
            raise gc.ErreurEntree("--controler-gabarit : ni DOSSIER, ni SORTIE.kicad_pcb, ni --gabarit")
        if not a["entree"]:
            raise gc.ErreurEntree("--entree obligatoire avec --controler-gabarit")
        if not a["sortie"]:
            raise gc.ErreurEntree("--sortie obligatoire avec --controler-gabarit")
        r = controler_gabarit(a["controler_gabarit"], a["entree"])
        gc.ecrire_json(a["sortie"], r)
        gc.afficher("%s : %s ; %d erreur(s) ; couches écrivibles : %s ; %d empreinte(s) non retournable(s)"
                    % (r["fichier"], "utilisable" if r["utilisable"] else "INUTILISABLE", len(r["erreurs"]),
                       ", ".join(r["couches_ecrivibles"]), len(r["non_retournables"])))
        for e in r["erreurs"]:
            gc.afficher("  ! " + e)
        return gc.CODE_SUCCES if r["utilisable"] else gc.CODE_NEGATIF
    if not a["dossier"] or not a["sortie_carte"]:
        raise gc.ErreurEntree("arguments obligatoires absents : DOSSIER SORTIE.kicad_pcb (--help pour l'aide)")
    if not a["gabarit"]:
        raise gc.ErreurEntree("--gabarit obligatoire : copie de la carte importée (lue seulement)")
    if not a["entree"]:
        raise gc.ErreurEntree("--entree obligatoire : entrée entree_carte/2 de la carte")
    try:
        generer(a["dossier"], a["sortie_carte"], a["gabarit"], a["config"], a)
    except GenerationNonDisponible as e:
        gc.afficher("%s : %s" % (PROGRAMME, e), erreur=True)
        return gc.CODE_INTERNE
    return gc.CODE_SUCCES


def main(argv=None):
    """Ligne de commande (§5.2) ; rend le code de sortie (§1.4) sans jamais quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, PROGRAMME, DESCRIPTION, POSITIONNELS)


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
