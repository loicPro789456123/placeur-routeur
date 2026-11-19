# -*- coding: utf-8 -*-
"""Socle commun des contrôles indépendants du lot C (contrat 2.1 et avenant 2.2).

Contenu :

1. Entrées et sorties (§0.2, §0.5, §1.4) : ``charger_json`` (lecture stricte selon le format ; toute erreur donne une
   ``ErreurEntree``, code 2, dont le message ne cite que le nom du fichier), ``ecrire_json`` (écriture atomique,
   UTF-8 sans BOM, fins de ligne LF), ``analyser_arguments`` (jamais de ``sys.exit``) et ``executer`` (codes 0, 1, 2
   et 4 ; messages en français, sans trace ni chemin reçu).
2. Poses (§0.4, §2.7) : placements ``placements/2`` ou liste nue (v1) ; pièce absente ou virtuelle prise à sa
   ``pose_cible`` ; rotation quelconque ; couches d'une pièce retournée obtenues par le rang miroir (r -> N-1-r).
3. Formes exactes (§4.1), modèle « noyau + rayon » : un noyau (point, segment, ou polygone avec ses trous) élargi
   d'un rayon. Rectangle tourné, rond, oblong, rectangle arrondi, polygone, pile par couche, trous ronds, carrés ou
   oblongs, tournés et décalés.
4. Objets de cuivre posés (``poser``), avec les identifiants de §2.8 : pastilles ``<des>#<k>``, régions
   ``<des>/R<k>`` (régions de liaison comprises), vias d'empreinte ``<des>/V<k>``, cuivre fixe ``F<k>``, cuivre de
   trou ``T<k>``, pistes ``P<k>``, vias ``V<k>``, surfaces ``S<k>`` (à trous), vias de couture ``C<k>``, zones de plan
   ``Z:<couche>/<k>``.
5. Index de cases, écart de §4.7, coudes, mesures du verdict (§2.8) et termes du coût commun (§4.7).
6. Outils des contrôles (étape C3) : points les plus proches de deux formes, inclusion d'une forme dans un polygone,
   rayon circonscrit, intervalles de l'axe d'une piste contenus dans une forme (exemption de ``garde_bord``).

Conventions retenues là où le contrat laisse le choix (journal du lot C, étape C1) :

- l'écart (§4.7) ne prend pas les zones de plan pour voisines (aucune piste n'est posée sur une couche de plan dans un
  état valide) ; une région de liaison n'est pas voisine d'une piste d'un réseau qu'elle lie ;
- les clés des coudes sont les millièmes de mm arrondis au plus proche, égalité au pair (convention 12 du corpus
  du lot E) ; la longueur d'une piste est l'hypoténuse de ses écarts en mil x 0,0254 (convention 13) ;
- pile d'une pastille : la forme principale vaut pour la couche externe du côté de la pièce posée, ``opposee`` pour la
  couche externe opposée, ``interne`` pour les autres ; ``rotation`` d'une entrée de pile relative à la pièce comme
  celle de la pastille (celle de la pastille si absente), ``decalage`` dans le repère de la pastille, ``points`` dans
  le repère local de la pièce ;
- trou oblong : ``longueur`` = longueur totale de la fente (au moins le diamètre) ;
- les couches d'une pastille, d'une région ou d'un via d'empreinte sont celles de la pièce à sa pose du fichier
  (``pose_actuelle``) ; posée sur l'autre face, la pièce a ses couches miroir ;
- un trou de ``trous[]`` rattaché à une pièce (``piece`` renseigné) n'est pas un objet : le routage le traite comme la
  pastille de cette pièce (§2.5) ;
- tout nombre lu est borné à ``BORNE_NOMBRE`` (10^6) en valeur absolue, dans son unité (mil, mm ou degré : 25,4 m
  en mil, 1 km en mm), et tout entier à 2^53 : au-delà, l'entrée est refusée (code 2, chemin du champ) ; aucune
  carte réelle n'en approche (espace de travail d'Altium 2,54 m, de KiCad 2,1 m) ; les types sont stricts (un
  booléen n'est pas un nombre, un texte n'est pas un nombre, un nom de couche est un texte) ;
- l'index de cases range à part les objets dont la boîte couvre plus de ``SEUIL_CASES_OBJET`` cases (aucune
  énumération case par case d'un très grand objet) ; une requête plus grande que l'index parcourt les cases occupées.

Bibliothèque standard seulement. Aucune donnée propre à une carte, aucun fichier lu sans qu'on le désigne, aucune
variable d'environnement lue.
"""
import difflib
import json
import math
import os
import re
import sys
import time

VERSION_OUTILS = "2.0.0"

# ---------------------------------------------------------------------------------------------------------------------
# Constantes (§0.4, §4.7)
# ---------------------------------------------------------------------------------------------------------------------
MIL = 0.0254                      # mm par mil
EPS_CONTACT = 1e-4                # mm
EPS_ISOLEMENT = 1e-4              # mm
EPS_LARGEUR_MIL = 1e-6            # mil
EPS_VIA = 1e-3                    # mm
EPS_ANGLE = 1e-6                  # degré

BORNE_NOMBRE = 1.0e6              # valeur absolue maximale d'un nombre lu, dans son unité (mil, mm, degré)
ENTIER_MAX = 2 ** 53              # plus grand entier lu en valeur absolue (exact en flottant)

CASE_ECART_MM = 1.0               # côté des cases de l'écart (§4.7)
ELARGISSEMENT_ECART_MM = 0.3      # élargissement des boîtes (§4.7)
SEUIL_CASES_OBJET = 4096          # index : un objet qui couvre plus de cases est rangé à part
MARGE_ELAGAGE_MM = 1e-9           # écart : un voisin n'est écarté que si sa boîte est plus loin que le meilleur
                                  # écart (positif) de plus de cette marge (aucun arrondi ne peut le rendre meilleur)
FACTEUR_SEUIL_ECART = 3.0         # s = 3 x isolement.defaut
FACTEUR_PLAFOND_ECART = 5.0       # p = 5 x isolement.defaut
COS_COUDE = -0.999                # coude : cos > -0,999
COS_DROIT = 0.01                  # coude à 90 degrés : |cos| <= 0,01 ; angle aigu : cos > 0,01
TOLERANCE_RETRECI_MM = 1e-3       # rétréci : largeur < préférée - 1e-3 mm
MOHM_PAR_CARRE = 0.5              # résistance : 0,5 mOhm par carré
ROLES_RESISTANCE = ("puissance", "alimentation")

CODE_SUCCES = 0
CODE_NEGATIF = 1
CODE_ENTREE = 2
CODE_INTERNE = 4

# Genres d'objets posés (§4.1)
PASTILLE = "pastille"
REGION = "region"
VIA_EMPREINTE = "via_empreinte"
CUIVRE_FIXE = "cuivre_fixe"
CUIVRE_TROU = "cuivre_trou"
PISTE = "piste"
VIA = "via"
SURFACE = "surface"
VIA_COUTURE = "via_couture"
ZONE_PLAN = "zone_plan"
GENRES_NEUFS = (PISTE, VIA, SURFACE, VIA_COUTURE)


# ---------------------------------------------------------------------------------------------------------------------
# Erreurs, messages
# ---------------------------------------------------------------------------------------------------------------------
class ErreurEntree(Exception):
    """Entrée invalide (code 2, §1.4) : message en français qui ne cite que des noms de fichiers."""


class DemandeAide(Exception):
    """L'aide a été demandée (``--help``) : le texte est affiché et le code 0 rendu."""


def nom_fichier(chemin):
    """Nom seul d'un chemin : aucun message ne cite un chemin reçu (§0.2-7)."""
    texte = os.fspath(chemin) if not isinstance(chemin, str) else chemin
    morceaux = [m for m in re.split(r"[\\/]+", texte) if m]
    return morceaux[-1] if morceaux else texte


# Chemin absolu dans un texte : entre apostrophes ou guillemets, tout le contenu ; sinon les noms de dossier peuvent
# contenir des espaces (« Prénom Nom ») tant qu'un séparateur suit, le dernier élément s'arrête à la première espace.
_NOM_DOSSIER = r"[^\\/\s'\"<>|:*?][^\\/\n\r\t'\"<>|:*?]*[\\/]"
_DERNIER = r"[^\s\\/'\"<>|:*?]*"
_MOTIF_CHEMIN = re.compile(
    r"(?<=')(?:[A-Za-z]:[\\/]|\\\\|/)[^'\n]*(?=')"
    r'|(?<=")(?:[A-Za-z]:[\\/]|\\\\|/)[^"\n]*(?=")'
    r"|\b[A-Za-z]:[\\/](?:%(d)s)*%(f)s"
    r"|\\\\[^\\\s]+\\(?:%(d)s)*%(f)s"
    r"|(?<![\w.])/(?:%(d)s)+%(f)s" % {"d": _NOM_DOSSIER, "f": _DERNIER})


def anonymiser_message(texte, chemins=()):
    """Remplace dans ``texte`` les chemins reçus, et tout ce qui ressemble à un chemin absolu, par leur nom seul."""
    texte = str(texte)
    variantes = []
    for c in chemins:
        if not isinstance(c, str) or not c:
            continue
        for v in (c, os.path.abspath(c), os.path.normpath(c)):
            for w in (v, v.replace("\\", "/"), v.replace("/", "\\")):
                variantes.append((w, nom_fichier(c)))
    for v, n in sorted(set(variantes), key=lambda vn: -len(vn[0])):
        if len(v) > len(n):
            texte = texte.replace(v, n)
    return _MOTIF_CHEMIN.sub(lambda m: nom_fichier(m.group(0)), texte)


def configurer_flux():
    """Sorties standard en UTF-8 (``errors="replace"``) quand elles ne sont pas une console (§0.2-10). À appeler
    seulement depuis le bloc ``if __name__ == "__main__"`` d'un module, jamais depuis ``main``."""
    for flux in (sys.stdout, sys.stderr):
        try:
            if flux is not None and not flux.isatty():
                flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def afficher(texte, erreur=False):
    """Écrit une ligne sur la sortie standard (ou d'erreur) sans jamais échouer sur un caractère non codable."""
    flux = sys.stderr if erreur else sys.stdout
    if flux is None:
        return
    try:
        flux.write(texte + "\n")
    except UnicodeEncodeError:
        codage = getattr(flux, "encoding", None) or "ascii"
        flux.write((texte + "\n").encode(codage, errors="replace").decode(codage, errors="replace"))
    try:
        flux.flush()
    except (OSError, ValueError):
        pass


def nombre_fr(valeur, decimales=1):
    """Nombre écrit à la française (virgule décimale) pour les messages."""
    if valeur is None:
        return "-"
    return ("%.*f" % (decimales, valeur)).replace(".", ",")


# ---------------------------------------------------------------------------------------------------------------------
# Lecture et écriture des JSON (§0.2-5, §0.5, §2.1)
# ---------------------------------------------------------------------------------------------------------------------
_TRADUCTIONS_JSON = (
    ("Expecting property name enclosed in double quotes",
     "nom de clé entre guillemets doubles attendu (virgule finale ou apostrophes ?)"),
    ("Expecting ',' delimiter", "virgule attendue"),
    ("Expecting ':' delimiter", "deux-points attendu"),
    ("Expecting value", "valeur attendue"),
    ("Extra data", "données en trop après la fin du document"),
    ("Unterminated string", "chaîne non terminée"),
    ("Invalid control character", "caractère de contrôle dans une chaîne"),
    ("Invalid \\uXXXX escape", "échappement \\u invalide"),
    ("Invalid \\escape", "échappement invalide (barre oblique inverse non doublée ?)"),
)

# Formats dont la version 1 n'avait pas de champ « format » (§6.4 : lus par compatibilité).
_V1_SANS_CHAMP_FORMAT = ("routage", "vias_couture")


def _traduire_json(message):
    for anglais, francais in _TRADUCTIONS_JSON:
        if message.startswith(anglais):
            return francais
    return "syntaxe invalide"


def _refuser_constante(nom):
    raise ValueError(nom)


def format_lu(objet, formats_acceptes=()):
    """Format « nom/majeure » d'un objet lu : liste nue -> ``placements/1`` ; objet sans champ ``format`` -> version 1
    du premier format accepté qui n'en avait pas (``routage/1``, ``vias_couture/1``) ; sinon None."""
    if isinstance(objet, list):
        return "placements/1"
    if not isinstance(objet, dict):
        return None
    f = objet.get("format")
    if f is None:
        for a in formats_acceptes:
            nom, _, majeure = a.partition("/")
            if majeure == "1" and nom in _V1_SANS_CHAMP_FORMAT:
                return a
        return None
    if not isinstance(f, str) or "/" not in f:
        return f if isinstance(f, str) else None
    nom, _, version = f.partition("/")
    return "%s/%s" % (nom, version.split(".")[0])


def exiger_format(objet, format_attendu, nom):
    """Refuse un objet en mémoire qui n'est pas du format ``format_attendu`` (« nom/majeure »)."""
    if not isinstance(objet, dict):
        raise ErreurEntree("%s : objet attendu (%s)" % (nom, format_attendu))
    if "format" not in objet:
        raise ErreurEntree("%s : champ « format » absent (attendu : %s)" % (nom, format_attendu))
    if format_lu(objet) != format_attendu:
        raise ErreurEntree("%s : format %s non pris en charge (attendu : %s)"
                           % (nom, objet.get("format"), format_attendu))
    return objet


def verifier_format(objet, formats_acceptes, nom):
    """Refuse (``ErreurEntree``) un objet dont le format n'est pas accepté ; rend le format lu."""
    lu = format_lu(objet, formats_acceptes)
    if lu is None or lu not in formats_acceptes:
        attendu = ", ".join(formats_acceptes)
        if isinstance(objet, dict) and "format" not in objet:
            raise ErreurEntree("%s : champ « format » absent (attendu : %s)" % (nom, attendu))
        if not isinstance(objet, dict):
            genre = "liste nue (forme v1)" if isinstance(objet, list) else "valeur isolée"
            raise ErreurEntree("%s : %s non prise en charge (attendu : %s)" % (nom, genre, attendu))
        raise ErreurEntree("%s : format %s non pris en charge (attendu : %s)" % (nom, objet.get("format"), attendu))
    return lu


def charger_json(chemin, formats_acceptes):
    """Lit un JSON écrit par un programme et vérifie son format (§0.5).

    ``formats_acceptes`` : formats « nom/majeure » permis (``"placements/1"`` = liste nue ; ``"routage/1"`` = objet
    sans champ ``format``). Fichier absent ou illisible, texte non UTF-8, JSON invalide, NaN, format inconnu :
    ``ErreurEntree`` dont le message ne cite que le nom du fichier."""
    nom = nom_fichier(chemin)
    try:
        with open(chemin, "rb") as f:
            brut = f.read()
    except FileNotFoundError:
        raise ErreurEntree("%s : fichier introuvable" % nom) from None
    except IsADirectoryError:
        raise ErreurEntree("%s : c'est un dossier, pas un fichier" % nom) from None
    except PermissionError:
        raise ErreurEntree("%s : lecture impossible (accès refusé)" % nom) from None
    except OSError as e:
        raise ErreurEntree("%s : lecture impossible (%s)" % (nom, type(e).__name__)) from None
    try:
        texte = brut.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ErreurEntree("%s : texte non UTF-8 (octet %d)" % (nom, e.start)) from None
    try:
        objet = json.loads(texte, parse_constant=_refuser_constante)
    except json.JSONDecodeError as e:
        raise ErreurEntree("%s : JSON invalide, ligne %d, colonne %d : %s"
                           % (nom, e.lineno, e.colno, _traduire_json(e.msg))) from None
    except ValueError as e:
        raise ErreurEntree("%s : valeur non numérique %s refusée" % (nom, e)) from None
    except RecursionError:
        raise ErreurEntree("%s : JSON trop imbriqué" % nom) from None
    verifier_format(objet, tuple(formats_acceptes), nom)
    return objet


def _supprimer(chemin):
    try:
        os.remove(chemin)
    except OSError:
        pass


def ecrire_json(chemin, objet, indentation=1):
    """Écrit ``objet`` en JSON de façon atomique (§0.2-5) : ``<nom>.tmp`` puis ``os.replace``, réessayé 5 fois toutes
    les 0,2 s sur ``PermissionError`` ; UTF-8 sans BOM, fins de ligne LF, ``ensure_ascii=False``, NaN refusé."""
    nom = nom_fichier(chemin)
    texte = json.dumps(objet, ensure_ascii=False, indent=indentation, allow_nan=False) + "\n"
    octets = texte.encode("utf-8")
    temporaire = os.fspath(chemin) + ".tmp"
    try:
        with open(temporaire, "wb") as f:
            f.write(octets)
    except OSError as e:
        _supprimer(temporaire)
        raise ErreurEntree("%s : écriture impossible (%s)" % (nom, type(e).__name__)) from None
    for essai in range(5):
        try:
            os.replace(temporaire, chemin)
            return
        except PermissionError:
            if essai < 4:
                time.sleep(0.2)
        except OSError as e:
            _supprimer(temporaire)
            raise ErreurEntree("%s : écriture impossible (%s)" % (nom, type(e).__name__)) from None
    _supprimer(temporaire)
    raise ErreurEntree("%s : écriture impossible (fichier tenu par un autre programme)" % nom)


# ---------------------------------------------------------------------------------------------------------------------
# Arguments de ligne de commande (sans sys.exit) et point d'entrée commun des main(argv)
# ---------------------------------------------------------------------------------------------------------------------
class Option:
    """Option ``--nom`` : ``nargs`` = 0 pour un drapeau (booléen), sinon nombre de valeurs qui suivent."""

    __slots__ = ("nom", "dest", "nargs", "obligatoire", "defaut", "type", "aide", "meta", "choix")

    def __init__(self, nom, aide="", nargs=1, obligatoire=False, defaut=None, type=str, meta=None, choix=None):
        if not nom.startswith("--") or len(nom) < 3:
            raise ValueError("une option s'écrit --nom")
        self.nom = nom
        self.dest = nom[2:].replace("-", "_")
        self.nargs = nargs
        self.obligatoire = obligatoire
        self.defaut = defaut
        self.type = type
        self.aide = aide
        self.meta = meta if meta is not None else self.dest.upper()
        self.choix = tuple(choix) if choix is not None else None


class Positionnel:
    """Argument positionnel (``DOSSIER``, ``SORTIE``…)."""

    __slots__ = ("dest", "meta", "aide", "obligatoire")

    def __init__(self, dest, meta=None, aide="", obligatoire=True):
        self.dest = dest
        self.meta = meta if meta is not None else dest.upper()
        self.aide = aide
        self.obligatoire = obligatoire


def texte_aide(options, programme, description="", positionnels=()):
    """Texte de ``--help`` (français)."""
    morceaux = []
    for p in positionnels:
        morceaux.append(p.meta if p.obligatoire else "[%s]" % p.meta)
    for o in options:
        if o.nargs == 0:
            t = o.nom
        else:
            t = "%s %s" % (o.nom, " ".join([o.meta] * o.nargs) if o.nargs > 1 and " " not in o.meta else o.meta)
        morceaux.append(t if o.obligatoire else "[%s]" % t)
    lignes = [("usage : %s %s" % (programme, " ".join(morceaux))).rstrip()]
    if description:
        lignes += ["", description.strip()]
    if positionnels:
        lignes += ["", "arguments :"]
        for p in positionnels:
            lignes.append("  %-26s %s" % (p.meta, p.aide))
    lignes += ["", "options :"]
    for o in options:
        t = o.nom if o.nargs == 0 else "%s %s" % (o.nom, o.meta)
        aide = o.aide
        if o.choix:
            aide += " (%s)" % "|".join(str(c) for c in o.choix)
        if o.defaut is not None and o.nargs != 0:
            aide += " ; défaut : %s" % o.defaut
        if o.obligatoire:
            aide += " ; obligatoire"
        lignes.append("  %-26s %s" % (t, aide))
    lignes.append("  %-26s %s" % ("--help", "affiche cette aide"))
    return "\n".join(lignes)


_NOMBRE = re.compile(r"^[+-]?(\d+([.]\d*)?|[.]\d+)([eE][+-]?\d+)?$")


def _convertir(brut, option):
    if option.type is str:
        valeur = brut
    else:
        try:
            valeur = option.type(brut)
        except (TypeError, ValueError):
            indice = " (virgule décimale ? écrire 2.5)" if "," in brut else ""
            attendu = "un nombre entier" if option.type is int else "un nombre"
            raise ErreurEntree("%s : « %s » n'est pas %s%s" % (option.nom, brut, attendu, indice)) from None
        if isinstance(valeur, float) and not math.isfinite(valeur):
            raise ErreurEntree("%s : « %s » n'est pas un nombre fini" % (option.nom, brut))
    if option.choix is not None and valeur not in option.choix:
        raise ErreurEntree("%s : « %s » invalide (valeurs permises : %s)"
                           % (option.nom, brut, ", ".join(str(c) for c in option.choix)))
    return valeur


def analyser_arguments(argv, options, programme="", description="", positionnels=()):
    """Analyse ``argv`` sans jamais quitter le processus : rend ``{dest: valeur}``.

    ``--help`` ou ``-h`` : ``DemandeAide`` (texte d'aide) ; option inconnue, répétée, sans valeur, valeur invalide,
    option obligatoire absente, argument en trop : ``ErreurEntree`` (message en français, chemins réduits au nom)."""
    par_nom = {o.nom: o for o in options}
    valeurs = {o.dest: (False if o.nargs == 0 else o.defaut) for o in options}
    vues = set()
    libres = []
    i = 0
    argv = list(argv)
    while i < len(argv):
        a = argv[i]
        if not isinstance(a, str):
            raise ErreurEntree("argument non textuel")
        if a in ("-h", "--help", "--aide"):
            raise DemandeAide(texte_aide(options, programme, description, positionnels))
        if a == "--":
            libres.extend(argv[i + 1:])
            break
        if a.startswith("--"):
            nom, egal, valeur = a.partition("=")
            o = par_nom.get(nom)
            if o is None:
                proche = difflib.get_close_matches(nom, list(par_nom), n=1)
                raise ErreurEntree("option inconnue : %s%s" % (
                    nom, " ; vouliez-vous dire %s ?" % proche[0] if proche else ""))
            if o.dest in vues:
                raise ErreurEntree("option %s donnée deux fois" % nom)
            vues.add(o.dest)
            if o.nargs == 0:
                if egal:
                    raise ErreurEntree("l'option %s ne prend pas de valeur" % nom)
                valeurs[o.dest] = True
                i += 1
                continue
            if egal:
                if o.nargs != 1:
                    raise ErreurEntree("l'option %s attend %d valeurs séparées par des espaces" % (nom, o.nargs))
                bruts = [valeur]
                i += 1
            else:
                bruts = argv[i + 1:i + 1 + o.nargs]
                if len(bruts) < o.nargs or any(b.startswith("-") and not _NOMBRE.match(b) for b in bruts):
                    raise ErreurEntree("valeur absente après %s (%d attendue%s)" % (
                        nom, o.nargs, "s" if o.nargs > 1 else ""))
                i += 1 + o.nargs
            convertis = [_convertir(b, o) for b in bruts]
            valeurs[o.dest] = convertis[0] if o.nargs == 1 else convertis
            continue
        if a.startswith("-") and len(a) > 1 and not _NOMBRE.match(a):
            raise ErreurEntree("option inconnue : %s" % a)
        libres.append(a)
        i += 1
    if len(libres) > len(positionnels):
        raise ErreurEntree("argument en trop : %s" % nom_fichier(libres[len(positionnels)]))
    for k, p in enumerate(positionnels):
        valeurs[p.dest] = libres[k] if k < len(libres) else None
    manquants = [p.meta for k, p in enumerate(positionnels) if p.obligatoire and k >= len(libres)]
    manquants += [o.nom for o in options if o.obligatoire and o.dest not in vues]
    if manquants:
        raise ErreurEntree("%s obligatoire%s absent%s : %s (--help pour l'aide)" % (
            "argument" if len(manquants) == 1 else "arguments", "" if len(manquants) == 1 else "s",
            "" if len(manquants) == 1 else "s", ", ".join(manquants)))
    return valeurs


def executer(corps, argv, options, programme, description="", positionnels=()):
    """Point d'entrée commun des ``main(argv)`` (§5.2) : analyse des arguments puis ``corps(args)`` -> code.

    Codes (§1.4) : celui rendu par ``corps`` (0 succès ou verdict positif, 1 verdict négatif) ; 0 pour ``--help`` ;
    2 pour une ``ErreurEntree`` ; 4 pour toute autre exception (erreur interne). Aucune trace, aucun chemin reçu."""
    if argv is None:
        argv = sys.argv[1:]
    argv = [os.fspath(a) if not isinstance(a, str) else a for a in argv]
    try:
        args = analyser_arguments(argv, options, programme, description, positionnels)
    except DemandeAide as aide:
        afficher(str(aide))
        return CODE_SUCCES
    except ErreurEntree as e:
        afficher("%s : %s" % (programme, anonymiser_message(e, argv)), erreur=True)
        return CODE_ENTREE
    try:
        code = corps(args)
    except ErreurEntree as e:
        afficher("%s : %s" % (programme, anonymiser_message(e, argv)), erreur=True)
        return CODE_ENTREE
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # noqa: B036 (erreur interne : code 4, sans trace)
        afficher("%s : erreur interne (%s) : %s" % (programme, type(e).__name__, anonymiser_message(e, argv)),
                 erreur=True)
        return CODE_INTERNE
    return CODE_SUCCES if code is None else int(code)


# ---------------------------------------------------------------------------------------------------------------------
# Accès aux champs avec un message qui nomme le champ (§2.1)
# ---------------------------------------------------------------------------------------------------------------------
def objet_json(v, chemin):
    if not isinstance(v, dict):
        raise ErreurEntree("%s : objet attendu" % chemin)
    return v


def exiger(d, cle, chemin):
    if not isinstance(d, dict):
        raise ErreurEntree("%s : objet attendu" % chemin)
    if cle not in d:
        raise ErreurEntree("%s : champ « %s » absent" % (chemin, cle))
    return d[cle]


def nombre(v, chemin):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ErreurEntree("%s : nombre attendu" % chemin)
    try:
        v = float(v)
    except OverflowError:
        raise ErreurEntree("%s : nombre hors bornes (|valeur| <= %g)" % (chemin, BORNE_NOMBRE)) from None
    if not math.isfinite(v):
        raise ErreurEntree("%s : nombre fini attendu" % chemin)
    if abs(v) > BORNE_NOMBRE:
        raise ErreurEntree("%s : nombre hors bornes (|valeur| <= %g)" % (chemin, BORNE_NOMBRE))
    return v


def entier(v, chemin):
    if isinstance(v, bool) or not isinstance(v, int):
        raise ErreurEntree("%s : nombre entier attendu" % chemin)
    if abs(v) > ENTIER_MAX:
        raise ErreurEntree("%s : entier hors bornes (|valeur| <= 2^53)" % chemin)
    return v


def texte(v, chemin):
    if not isinstance(v, str):
        raise ErreurEntree("%s : texte attendu" % chemin)
    return v


def liste(v, chemin):
    if not isinstance(v, list):
        raise ErreurEntree("%s : liste attendue" % chemin)
    return v


def booleen(v, chemin):
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    raise ErreurEntree("%s : booléen attendu" % chemin)


def point(v, chemin):
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ErreurEntree("%s : point [x, y] attendu" % chemin)
    return nombre(v[0], chemin + "[0]"), nombre(v[1], chemin + "[1]")


def points(v, chemin, echelle=1.0):
    res = []
    for i, p in enumerate(liste(v, chemin)):
        x, y = point(p, "%s[%d]" % (chemin, i))
        res.append((x * echelle, y * echelle))
    return res


# ---------------------------------------------------------------------------------------------------------------------
# Angles, poses (§0.4)
# ---------------------------------------------------------------------------------------------------------------------
def cos_sin(angle):
    """Cosinus et sinus d'un angle en degrés, exacts pour les multiples de 90 degrés."""
    a = math.fmod(float(angle), 360.0)
    if a < 0.0:
        a += 360.0
    if a >= 360.0:
        a -= 360.0
    if a == 0.0:
        return 1.0, 0.0
    if a == 90.0:
        return 0.0, 1.0
    if a == 180.0:
        return -1.0, 0.0
    if a == 270.0:
        return 0.0, -1.0
    r = math.radians(a)
    return math.cos(r), math.sin(r)


def ecart_angle(a, b):
    """Écart entre deux angles, en degrés, dans [0, 180]."""
    d = math.fmod(abs(float(a) - float(b)), 360.0)
    return 360.0 - d if d > 180.0 else d


class Pose:
    """Pose d'une pièce : monde = O + R(rotation) . M(face) . local, M(dessous) = diag(1, -1) (§0.4).

    ``retournee`` : la pièce est posée sur l'autre face que dans le fichier (``pose_actuelle``) ; ses couches sont
    alors celles du rang miroir."""

    __slots__ = ("x", "y", "rotation", "dessous", "retournee", "c", "s")

    def __init__(self, x, y, rotation, dessous, retournee=False):
        self.x = float(x)
        self.y = float(y)
        self.rotation = float(rotation)
        self.dessous = bool(dessous)
        self.retournee = bool(retournee)
        self.c, self.s = cos_sin(self.rotation)

    def monde(self, lx, ly):
        if self.dessous:
            ly = -ly
        return self.x + self.c * lx - self.s * ly, self.y + self.s * lx + self.c * ly

    def vecteur(self, vx, vy):
        if self.dessous:
            vy = -vy
        return self.c * vx - self.s * vy, self.s * vx + self.c * vy

    def angle(self, relatif):
        """Angle monde d'un élément de rotation ``relatif`` à la pièce (§0.4)."""
        return self.rotation - relatif if self.dessous else self.rotation + relatif

    def local(self, wx, wy):
        """Inverse de ``monde`` : local = M(face) . R(-rotation) . (monde - O) (§0.4)."""
        dx = wx - self.x
        dy = wy - self.y
        lx = self.c * dx + self.s * dy
        ly = -self.s * dx + self.c * dy
        return lx, (-ly if self.dessous else ly)

    def egale(self, autre):
        """Même pose (position à 1e-9 mm, rotation à ``EPS_ANGLE``, même face)."""
        return (abs(self.x - autre.x) <= 1e-9 and abs(self.y - autre.y) <= 1e-9
                and ecart_angle(self.rotation, autre.rotation) <= EPS_ANGLE and self.dessous == autre.dessous)

    def __repr__(self):
        return "Pose(%r, %r, %r, %r)" % (self.x, self.y, self.rotation, self.dessous)


def _pose_vecteur(v, chemin):
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        raise ErreurEntree("%s : pose [x, y, rotation, dessous] attendue" % chemin)
    return (nombre(v[0], chemin + "[0]"), nombre(v[1], chemin + "[1]"), nombre(v[2], chemin + "[2]"),
            booleen(v[3], chemin + "[3]"))


def entrees_placement(placements):
    """Entrées d'un placement ``placements/2`` (objet à champ ``pieces``) ou v1 (liste nue) : liste de
    ``(designateur, x_mm, y_mm, rotation, dessous)``."""
    if isinstance(placements, list):
        pieces, base = placements, "placement"
    elif isinstance(placements, dict):
        if "format" not in placements:
            raise ErreurEntree("placement : champ « format » absent (attendu : placements/2 ou liste v1)")
        if format_lu(placements) != "placements/2":
            raise ErreurEntree("placement : format %s non pris en charge (attendu : placements/2 ou liste v1)"
                               % placements.get("format"))
        unite = placements.get("unite", "mil")
        if unite != "mil":
            raise ErreurEntree("placement.unite : « %s » non pris en charge (attendu : mil)" % unite)
        pieces, base = liste(exiger(placements, "pieces", "placement"), "placement.pieces"), "placement.pieces"
    else:
        raise ErreurEntree("placement : objet ou liste attendu")
    res = []
    for i, e in enumerate(pieces):
        ch = "%s[%d]" % (base, i)
        des = texte(exiger(e, "designator", ch), ch + ".designator")
        face = exiger(e, "side", ch)
        if face not in ("top", "bottom"):
            raise ErreurEntree("%s.side : « %s » invalide (top ou bottom)" % (ch, face))
        res.append((des, nombre(exiger(e, "x", ch), ch + ".x") * MIL, nombre(exiger(e, "y", ch), ch + ".y") * MIL,
                    nombre(exiger(e, "rotation", ch), ch + ".rotation"), face == "bottom"))
    return res


def poses_des_pieces(donnees, placements=None):
    """Pose de chaque pièce des données : celle du placement ; pièce absente ou virtuelle : sa ``pose_cible``
    (alias ``pose_v3``). Rend ``(poses, infos)``, ``infos`` = ``{"absentes", "inconnues", "doublons"}``."""
    comps = liste(exiger(donnees, "composants", "donnees"), "donnees.composants")
    entrees = {}
    doublons = []
    if placements is not None:
        for des, x, y, rot, dessous in entrees_placement(placements):
            if des in entrees and des not in doublons:
                doublons.append(des)
            entrees[des] = (x, y, rot, dessous)
    poses = {}
    absentes = []
    for i, c in enumerate(comps):
        ch = "donnees.composants[%d]" % i
        nom = texte(exiger(c, "nom", ch), ch + ".nom")
        if nom in poses:
            raise ErreurEntree("%s.nom : pièce %s en double" % (ch, nom))
        cible = c.get("pose_cible", c.get("pose_v3"))
        if cible is None:
            raise ErreurEntree("%s : champ « pose_cible » absent" % ch)
        cible = _pose_vecteur(cible, ch + ".pose_cible")
        actuelle = _pose_vecteur(c["pose_actuelle"], ch + ".pose_actuelle") if "pose_actuelle" in c else cible
        virtuel = booleen(c.get("virtuel", False), ch + ".virtuel")
        e = None if virtuel else entrees.get(nom)
        if e is None:
            # forme v1 (liste nue) : les pièces fixes inchangées n'y figurent pas (comportement de 9b5b5a3, lot A)
            fixe_v1 = isinstance(placements, list) and c.get("statut") == "fixe"
            if placements is not None and not virtuel and not fixe_v1:
                absentes.append(nom)
            e = cible
        poses[nom] = Pose(e[0], e[1], e[2], e[3], retournee=bool(e[3]) != bool(actuelle[3]))
    inconnues = [d for d in entrees if d not in poses]
    return poses, {"absentes": absentes, "inconnues": inconnues, "doublons": doublons}


# ---------------------------------------------------------------------------------------------------------------------
# Formes exactes : noyau (point, segment, polygone à trous) élargi d'un rayon
# ---------------------------------------------------------------------------------------------------------------------
POINT, SEGMENT, POLYGONE = 0, 1, 2


class Forme:
    """Noyau élargi d'un rayon (coordonnées monde, mm).

    POINT : (x1, y1) ; SEGMENT : (x1, y1)-(x2, y2) ; POLYGONE : ``anneaux`` = [contour, trou, …] (sens quelconque),
    ``aretes`` = toutes les arêtes (ax, ay, bx, by), ``bnoyau`` = boîte du noyau. ``boite`` = boîte de la forme."""

    __slots__ = ("genre", "x1", "y1", "x2", "y2", "anneaux", "aretes", "bnoyau", "rayon", "boite")

    def __init__(self, genre, rayon):
        self.genre = genre
        self.rayon = float(rayon)
        self.x1 = self.y1 = self.x2 = self.y2 = 0.0
        self.anneaux = None
        self.aretes = None
        self.bnoyau = None
        self.boite = None

    def __repr__(self):
        if self.genre == POINT:
            return "Forme(point (%g, %g), r=%g)" % (self.x1, self.y1, self.rayon)
        if self.genre == SEGMENT:
            return "Forme(segment (%g, %g)-(%g, %g), r=%g)" % (self.x1, self.y1, self.x2, self.y2, self.rayon)
        return "Forme(polygone %d sommets, %d trous, r=%g)" % (len(self.anneaux[0]), len(self.anneaux) - 1, self.rayon)


def forme_point(x, y, rayon):
    f = Forme(POINT, rayon)
    f.x1, f.y1 = float(x), float(y)
    r = f.rayon
    f.bnoyau = (f.x1, f.y1, f.x1, f.y1)
    f.boite = (f.x1 - r, f.y1 - r, f.x1 + r, f.y1 + r)
    return f


def forme_segment(x1, y1, x2, y2, rayon):
    f = Forme(SEGMENT, rayon)
    f.x1, f.y1, f.x2, f.y2 = float(x1), float(y1), float(x2), float(y2)
    r = f.rayon
    f.bnoyau = (min(f.x1, f.x2), min(f.y1, f.y2), max(f.x1, f.x2), max(f.y1, f.y2))
    f.boite = (f.bnoyau[0] - r, f.bnoyau[1] - r, f.bnoyau[2] + r, f.bnoyau[3] + r)
    return f


def _anneau_propre(pts):
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    propre = []
    for p in pts:
        if not propre or p != propre[-1]:
            propre.append(p)
    if len(propre) > 1 and propre[0] == propre[-1]:
        propre.pop()
    return propre


def forme_polygone(contour, trous=(), rayon=0.0, chemin="polygone"):
    """Polygone (contour non fermé, sens quelconque, trous facultatifs) élargi de ``rayon``. Un contour de moins de
    3 sommets distincts devient un segment ou un point ; ``chemin`` nomme le champ du contour dans les messages."""
    ext = _anneau_propre(contour)
    if len(ext) == 0:
        raise ErreurEntree("%s : polygone vide (aucun sommet)" % chemin)
    if len(ext) == 1:
        return forme_point(ext[0][0], ext[0][1], rayon)
    if len(ext) == 2:
        return forme_segment(ext[0][0], ext[0][1], ext[1][0], ext[1][1], rayon)
    anneaux = [ext]
    for t in trous:
        t = _anneau_propre(t)
        if len(t) >= 3:
            anneaux.append(t)
    f = Forme(POLYGONE, rayon)
    f.anneaux = anneaux
    aretes = []
    for a in anneaux:
        xj, yj = a[-1]
        for xi, yi in a:
            aretes.append((xj, yj, xi, yi))
            xj, yj = xi, yi
    f.aretes = aretes
    xs = [p[0] for p in ext]
    ys = [p[1] for p in ext]
    f.bnoyau = (min(xs), min(ys), max(xs), max(ys))
    r = f.rayon
    f.boite = (f.bnoyau[0] - r, f.bnoyau[1] - r, f.bnoyau[2] + r, f.bnoyau[3] + r)
    return f


def d_point_segment(px, py, ax, ay, bx, by):
    """Distance d'un point à un segment."""
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    t = 0.0
    if l2 != 0.0:
        t = ((px - ax) * dx + (py - ay) * dy) / l2
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _croisent(ax, ay, bx, by, cx, cy, dx, dy):
    """Croisement propre de [ab] et [cd] (les contacts et alignements sont vus par les distances aux extrémités)."""
    d1 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
    d2 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)
    d3 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d4 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
    return (d1 > 0.0) != (d2 > 0.0) and (d3 > 0.0) != (d4 > 0.0) and d1 != 0.0 and d2 != 0.0 and d3 != 0.0 \
        and d4 != 0.0


def d_segment_segment(ax, ay, bx, by, cx, cy, dx, dy):
    """Distance entre deux segments (0 s'ils se coupent)."""
    if _croisent(ax, ay, bx, by, cx, cy, dx, dy):
        return 0.0
    return min(d_point_segment(ax, ay, cx, cy, dx, dy), d_point_segment(bx, by, cx, cy, dx, dy),
               d_point_segment(cx, cy, ax, ay, bx, by), d_point_segment(dx, dy, ax, ay, bx, by))


def _dans_anneau(anneau, x, y):
    dedans = False
    xj, yj = anneau[-1]
    for xi, yi in anneau:
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            dedans = not dedans
        xj, yj = xi, yi
    return dedans


def contient(f, x, y):
    """Point dans le noyau polygonal de ``f`` (contour moins trous ; règle pair-impair)."""
    b = f.bnoyau
    if x < b[0] or x > b[2] or y < b[1] or y > b[3]:
        return False
    if not _dans_anneau(f.anneaux[0], x, y):
        return False
    for t in f.anneaux[1:]:
        if _dans_anneau(t, x, y):
            return False
    return True


def _d_point_bord(f, x, y):
    m = math.inf
    for ax, ay, bx, by in f.aretes:
        d = d_point_segment(x, y, ax, ay, bx, by)
        if d < m:
            m = d
    return m


def _d_point_polygone(f, x, y):
    if contient(f, x, y):
        return 0.0
    return _d_point_bord(f, x, y)


def _d_segment_polygone(f, ax, ay, bx, by):
    if contient(f, ax, ay) or contient(f, bx, by):
        return 0.0
    m = math.inf
    for cx, cy, dx, dy in f.aretes:
        d = d_segment_segment(ax, ay, bx, by, cx, cy, dx, dy)
        if d < m:
            m = d
            if m == 0.0:
                return 0.0
    return m


def _d_polygone_polygone(f, g):
    p = f.anneaux[0][0]
    q = g.anneaux[0][0]
    if contient(g, p[0], p[1]) or contient(f, q[0], q[1]):
        return 0.0
    m = math.inf
    for ax, ay, bx, by in f.aretes:
        for cx, cy, dx, dy in g.aretes:
            d = d_segment_segment(ax, ay, bx, by, cx, cy, dx, dy)
            if d < m:
                m = d
                if m == 0.0:
                    return 0.0
    return m


def distance_noyaux(f, g):
    """Distance (>= 0) entre les noyaux de deux formes ; 0 s'ils se recouvrent."""
    if f.genre > g.genre:
        f, g = g, f
    if f.genre == POINT:
        if g.genre == POINT:
            return math.hypot(f.x1 - g.x1, f.y1 - g.y1)
        if g.genre == SEGMENT:
            return d_point_segment(f.x1, f.y1, g.x1, g.y1, g.x2, g.y2)
        return _d_point_polygone(g, f.x1, f.y1)
    if f.genre == SEGMENT:
        if g.genre == SEGMENT:
            return d_segment_segment(f.x1, f.y1, f.x2, f.y2, g.x1, g.y1, g.x2, g.y2)
        return _d_segment_polygone(g, f.x1, f.y1, f.x2, f.y2)
    return _d_polygone_polygone(f, g)


def ecart_formes(f, g):
    """Écart bord à bord de deux formes : distance des noyaux moins les rayons (négatif en cas de recouvrement)."""
    return distance_noyaux(f, g) - f.rayon - g.rayon


def distance_signee(f, x, y):
    """Distance signée d'un point au bord de la forme : négative dedans (profondeur), positive dehors."""
    if f.genre == POINT:
        d = math.hypot(x - f.x1, y - f.y1)
    elif f.genre == SEGMENT:
        d = d_point_segment(x, y, f.x1, f.y1, f.x2, f.y2)
    else:
        d = _d_point_bord(f, x, y)
        if contient(f, x, y):
            d = -d
    return d - f.rayon


def distance_boites(a, b):
    """Distance entre deux boîtes (0 si elles se touchent) : minorant de l'écart des formes qu'elles contiennent."""
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return math.hypot(dx, dy)


def union_boites(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _rectangle(cx, cy, c, s, hx, hy):
    return [(cx + c * px - s * py, cy + s * px + c * py) for px, py in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]


def forme_parametrique(forme, largeur, hauteur, rayon_coin, cx, cy, angle, chemin="forme"):
    """Forme d'une pastille ``rect``, ``rond``, ``oblong`` ou ``rect_arrondi`` de centre (cx, cy) et d'angle monde
    ``angle`` (degrés). Un ``rond`` non circulaire est pris par excès comme l'oblong qui le contient."""
    w = float(largeur)
    h = float(hauteur)
    if w < 0.0 or h < 0.0:
        raise ErreurEntree("%s : dimensions négatives" % chemin)
    c, s = cos_sin(angle)
    if forme == "rond":
        if w == h:
            return forme_point(cx, cy, w / 2.0)
        forme = "oblong"
    if forme == "oblong":
        if w >= h:
            r = h / 2.0
            demi = w / 2.0 - r
            ax, ay, bx, by = -demi, 0.0, demi, 0.0
        else:
            r = w / 2.0
            demi = h / 2.0 - r
            ax, ay, bx, by = 0.0, -demi, 0.0, demi
        if demi <= 0.0:
            return forme_point(cx, cy, r)
        return forme_segment(cx + c * ax - s * ay, cy + s * ax + c * ay, cx + c * bx - s * by, cy + s * bx + c * by, r)
    if forme == "rect_arrondi":
        rc = min(max(float(rayon_coin or 0.0), 0.0), min(w, h) / 2.0)
        if rc > 0.0:
            hx = w / 2.0 - rc
            hy = h / 2.0 - rc
            if hx <= 0.0 and hy <= 0.0:
                return forme_point(cx, cy, rc)
            if hx <= 0.0:
                return forme_segment(cx + s * hy, cy - c * hy, cx - s * hy, cy + c * hy, rc)
            if hy <= 0.0:
                return forme_segment(cx - c * hx, cy - s * hx, cx + c * hx, cy + s * hx, rc)
            return forme_polygone(_rectangle(cx, cy, c, s, hx, hy), rayon=rc)
        forme = "rect"
    if forme == "rect":
        return forme_polygone(_rectangle(cx, cy, c, s, w / 2.0, h / 2.0))
    raise ErreurEntree("%s : forme « %s » inconnue" % (chemin, forme))


def forme_trou(trou, cx, cy, angle, chemin="trou"):
    """Paroi d'un trou ``rond``, ``carre`` ou ``oblong`` (``longueur`` = longueur totale) de centre (cx, cy), tourné
    de ``angle`` (degrés, monde)."""
    d = nombre(exiger(trou, "diametre", chemin), chemin + ".diametre")
    genre = trou.get("forme", "rond")
    if genre == "rond" or d <= 0.0:
        return forme_point(cx, cy, max(d, 0.0) / 2.0)
    c, s = cos_sin(angle)
    if genre == "carre":
        return forme_polygone(_rectangle(cx, cy, c, s, d / 2.0, d / 2.0))
    if genre == "oblong":
        longueur = max(nombre(trou.get("longueur", 0.0) or 0.0, chemin + ".longueur"), d)
        demi = (longueur - d) / 2.0
        if demi <= 0.0:
            return forme_point(cx, cy, d / 2.0)
        return forme_segment(cx - c * demi, cy - s * demi, cx + c * demi, cy + s * demi, d / 2.0)
    raise ErreurEntree("%s.forme : « %s » inconnue (rond, carre ou oblong)" % (chemin, genre))


# ---------------------------------------------------------------------------------------------------------------------
# Objets posés (§4.1) et scène
# ---------------------------------------------------------------------------------------------------------------------
class Objet:
    """Objet de cuivre posé : identifiant (§2.8), genre, réseau (``""`` = sans réseau), liaison (réseaux liés par
    une région de liaison, sinon None), masque des rangs de couche, forme (toutes couches) et ``formes`` (rang ->
    forme propre à la couche, pile d'une pastille), neuf (piste, via, surface, via de couture), boîte, pièce,
    classe d'isolement et ``info`` (champs propres au genre)."""

    __slots__ = ("id", "genre", "reseau", "liaison", "masque", "forme", "formes", "neuf", "boite", "piece", "classe",
                 "info")

    def __init__(self, ident, genre, reseau, masque, forme, formes=None, liaison=None, piece=None, classe=None,
                 info=None):
        self.id = ident
        self.genre = genre
        self.reseau = reseau or ""
        self.liaison = tuple(liaison) if liaison else None
        self.masque = masque
        self.forme = forme
        self.formes = formes or None
        self.neuf = genre in GENRES_NEUFS
        self.piece = piece
        self.classe = classe
        self.info = info if info is not None else {}
        b = forme.boite
        if self.formes:
            for g in self.formes.values():
                b = union_boites(b, g.boite)
        self.boite = b

    def forme_sur(self, rang):
        if self.formes is not None:
            f = self.formes.get(rang)
            if f is not None:
                return f
        return self.forme

    def __repr__(self):
        return "Objet(%s, %s, %r)" % (self.id, self.genre, self.reseau)


def rangs(masque):
    """Rangs des couches d'un masque, dans l'ordre."""
    r = 0
    while masque:
        if masque & 1:
            yield r
        masque >>= 1
        r += 1


def meme_reseau(o, q):
    """Deux objets du même réseau (un objet sans réseau n'est du réseau de personne ; une région de liaison est du
    réseau de chacun des réseaux qu'elle lie)."""
    if o.reseau and o.reseau == q.reseau:
        return True
    if q.liaison and o.reseau and o.reseau in q.liaison:
        return True
    if o.liaison and q.reseau and q.reseau in o.liaison:
        return True
    return False


def ecart_objets(o, q):
    """Écart bord à bord de deux objets sur leurs couches communes (None sans couche commune)."""
    commun = o.masque & q.masque
    if not commun:
        return None
    if o.formes is None and q.formes is None:
        return ecart_formes(o.forme, q.forme)
    m = None
    for r in rangs(commun):
        e = ecart_formes(o.forme_sur(r), q.forme_sur(r))
        if m is None or e < m:
            m = e
    return m


class Scene:
    """Objets posés d'un couple données + placement (+ routage, + couture).

    ``objets`` : tous les objets ; ``pistes``, ``vias``, ``surfaces``, ``couture`` : indices des objets neufs dans
    l'ordre de leurs listes ; ``parois`` : parois de trous (``{"id", "forme", "metallise", "objet"}``) des trous de
    ``trous[]`` et des pastilles percées ; ``couches`` : noms par rang ; ``rang`` : nom -> rang ; ``poses`` : nom ->
    ``Pose`` ; ``infos_placement`` : pièces absentes, inconnues, en double."""

    def __init__(self, donnees):
        exiger_format(donnees, "donnees_placeur/2", "donnees")
        self.donnees = donnees
        couches = liste(exiger(donnees, "couches", "donnees"), "donnees.couches")
        if not couches:
            raise ErreurEntree("donnees.couches : aucune couche")
        par_rang = {}
        for i, c in enumerate(couches):
            ch = "donnees.couches[%d]" % i
            nom = texte(exiger(c, "nom", ch), ch + ".nom")
            r = entier(exiger(c, "rang", ch), ch + ".rang")
            if r in par_rang:
                raise ErreurEntree("%s.rang : rang %d en double" % (ch, r))
            par_rang[r] = (nom, c)
        if sorted(par_rang) != list(range(len(couches))):
            raise ErreurEntree("donnees.couches : rangs attendus de 0 à %d" % (len(couches) - 1))
        self.n = len(couches)
        self.couches = [par_rang[r][0] for r in range(self.n)]
        self.donnees_couches = [par_rang[r][1] for r in range(self.n)]
        self.rang = {}
        for r, nom in enumerate(self.couches):
            if nom in self.rang:
                raise ErreurEntree("donnees.couches : couche %s en double" % nom)
            self.rang[nom] = r
        self.tout = (1 << self.n) - 1
        self.reseaux = {}
        self.reseaux = reseaux_des_donnees(donnees)
        self.isolement_defaut = isolement_defaut(donnees)
        self.objets = []
        self.pistes = []
        self.vias = []
        self.surfaces = []
        self.couture = []
        self.parois = []
        self.poses = {}
        self.infos_placement = {"absentes": [], "inconnues": [], "doublons": []}

    def masque(self, noms, chemin, retournee=False):
        """Masque des rangs d'une liste de noms de couches (``"toutes"`` = toutes) ; miroir si ``retournee``."""
        liste(noms, chemin)
        m = 0
        for i, nom in enumerate(noms):
            texte(nom, "%s[%d]" % (chemin, i))
            if nom == "toutes":
                return self.tout
            r = self.rang.get(nom)
            if r is None:
                raise ErreurEntree("%s[%d] : couche « %s » inconnue" % (chemin, i, nom))
            if retournee:
                r = self.n - 1 - r
            m |= 1 << r
        return m

    def ajouter(self, objet):
        self.objets.append(objet)
        return len(self.objets) - 1


def isolement_defaut(donnees):
    regles = exiger(donnees, "regles", "donnees")
    iso = exiger(regles, "isolement", "donnees.regles")
    v = nombre(exiger(iso, "defaut", "donnees.regles.isolement"), "donnees.regles.isolement.defaut")
    if v < 0.0:
        raise ErreurEntree("donnees.regles.isolement.defaut : valeur négative")
    return v


def _forme_description(desc, cx, cy, angle, pose, chemin):
    """Forme d'une description de pastille (ou d'entrée de pile) : ``polygone`` à points dans le repère local de la
    pièce, sinon forme paramétrique centrée en (cx, cy)."""
    genre = texte(exiger(desc, "forme", chemin), chemin + ".forme")
    if genre == "polygone":
        pts = [pose.monde(x, y) for x, y in points(exiger(desc, "points", chemin), chemin + ".points")]
        if len(pts) < 3:
            raise ErreurEntree("%s.points : au moins 3 sommets attendus" % chemin)
        return forme_polygone(pts, chemin=chemin + ".points")
    return forme_parametrique(genre, nombre(exiger(desc, "largeur", chemin), chemin + ".largeur"),
                              nombre(exiger(desc, "hauteur", chemin), chemin + ".hauteur"),
                              nombre(desc.get("rayon_coin", 0.0) or 0.0, chemin + ".rayon_coin"),
                              cx, cy, angle, chemin)


def _decaler(lx, ly, theta, decalage, chemin):
    """Point local décalé de ``decalage`` exprimé dans le repère d'un élément de rotation ``theta`` (relative)."""
    if not decalage:
        return lx, ly
    dx, dy = point(decalage, chemin)
    c, s = cos_sin(theta)
    return lx + c * dx - s * dy, ly + s * dx + c * dy


def _poser_composant(sc, ic, comp):
    ch0 = "donnees.composants[%d]" % ic
    nom = comp["nom"]
    pose = sc.poses[nom]
    rang_composant = sc.n - 1 if pose.dessous else 0
    rang_oppose = sc.n - 1 - rang_composant
    for k, p in enumerate(liste(exiger(comp, "pastilles", ch0), ch0 + ".pastilles")):
        ch = "%s.pastilles[%d]" % (ch0, k)
        objet_json(p, ch)
        pid = p.get("id") or "%s#%d" % (nom, k)
        lx = nombre(exiger(p, "lx", ch), ch + ".lx")
        ly = nombre(exiger(p, "ly", ch), ch + ".ly")
        theta = nombre(exiger(p, "rotation", ch), ch + ".rotation")
        cx, cy = pose.monde(lx, ly)
        forme = _forme_description(p, cx, cy, pose.angle(theta), pose, ch)
        masque = sc.masque(exiger(p, "couches", ch), ch + ".couches", pose.retournee)
        formes = None
        pile = p.get("pile")
        if pile:
            objet_json(pile, ch + ".pile")
            formes = {}
            for r in rangs(masque):
                if r == rang_composant:
                    continue
                cle = "opposee" if r == rang_oppose else "interne"
                e = pile.get(cle)
                if not e:
                    continue
                che = "%s.pile.%s" % (ch, cle)
                objet_json(e, che)
                th = nombre(e.get("rotation", theta), che + ".rotation")
                ex, ey = _decaler(lx, ly, theta, e.get("decalage"), che + ".decalage")
                ecx, ecy = pose.monde(ex, ey)
                formes[r] = _forme_description(e, ecx, ecy, pose.angle(th), pose, che)
        trou = p.get("trou")
        paroi = None
        if trou:
            cht = ch + ".trou"
            objet_json(trou, cht)
            tx, ty = _decaler(lx, ly, theta, trou.get("decalage"), cht + ".decalage")
            wx, wy = pose.monde(tx, ty)
            rot_trou = nombre(trou.get("rotation", 0.0) or 0.0, cht + ".rotation")
            paroi = forme_trou(trou, wx, wy, pose.angle(theta + rot_trou), cht)
        traversante = booleen(p.get("traversante", p.get("th", False)), ch + ".traversante")
        info = {"nom": p.get("nom", ""), "k": k, "centre": (cx, cy), "traversante": traversante, "trou": paroi,
                "metallise": bool(trou.get("metallise", True)) if trou else False,
                "via_permis": booleen(p.get("via_dans_pastille_permis", traversante), ch + ".via_dans_pastille_permis"),
                "expansion_masque": p.get("expansion_masque", 0.0), "source": p}
        reseau = texte(p.get("reseau", ""), ch + ".reseau")
        k_objet = sc.ajouter(Objet(pid, PASTILLE, reseau, masque, forme, formes=formes, piece=nom,
                                   classe=p.get("classe_isolement"), info=info))
        if paroi is not None:
            sc.parois.append({"id": pid, "forme": paroi, "metallise": info["metallise"], "objet": k_objet})
    for k, r in enumerate(liste(exiger(comp, "regions", ch0), ch0 + ".regions")):
        ch = "%s.regions[%d]" % (ch0, k)
        objet_json(r, ch)
        rid = r.get("id") or "%s/R%d" % (nom, k)
        pts = [pose.monde(x, y) for x, y in points(exiger(r, "points", ch), ch + ".points")]
        trous = [[pose.monde(x, y) for x, y in points(t, "%s.trous[%d]" % (ch, i))]
                 for i, t in enumerate(r.get("trous") or [])]
        liaison = r.get("liaison")
        if liaison is not None:
            liaison = [texte(x, ch + ".liaison") for x in liste(liaison, ch + ".liaison")]
        masque = sc.masque(exiger(r, "couches", ch), ch + ".couches", pose.retournee)
        sc.ajouter(Objet(rid, REGION, texte(r.get("reseau", "") or "", ch + ".reseau"), masque,
                         forme_polygone(pts, trous, chemin=ch + ".points"), liaison=liaison, piece=nom, classe=r.get("classe_isolement"),
                         info={"k": k, "pastille": r.get("pastille"), "source": r}))
    for k, v in enumerate(liste(exiger(comp, "vias", ch0), ch0 + ".vias")):
        ch = "%s.vias[%d]" % (ch0, k)
        objet_json(v, ch)
        vid = v.get("id") or "%s/V%d" % (nom, k)
        cx, cy = pose.monde(nombre(exiger(v, "x", ch), ch + ".x"), nombre(exiger(v, "y", ch), ch + ".y"))
        d = nombre(exiger(v, "diametre", ch), ch + ".diametre")
        t = nombre(exiger(v, "trou", ch), ch + ".trou")
        paroi = forme_point(cx, cy, t / 2.0)
        k_objet = sc.ajouter(Objet(vid, VIA_EMPREINTE, texte(v.get("reseau", "") or "", ch + ".reseau"), sc.tout,
                                   forme_point(cx, cy, d / 2.0), piece=nom, classe=v.get("classe_isolement"),
                                   info={"k": k, "diametre": d, "trou": paroi, "source": v}))
        sc.parois.append({"id": vid, "forme": paroi, "metallise": True, "objet": k_objet})


def _poser_cuivre_fixe(sc, donnees):
    for k, f in enumerate(liste(exiger(donnees, "cuivre_fixe", "donnees"), "donnees.cuivre_fixe")):
        ch = "donnees.cuivre_fixe[%d]" % k
        objet_json(f, ch)
        fid = f.get("id") or "F%d" % k
        typ = exiger(f, "type", ch)
        reseau = texte(f.get("reseau", "") or "", ch + ".reseau")
        info = {"k": k, "type": typ, "source": f}
        if typ == "piste":
            forme = forme_segment(nombre(exiger(f, "x1", ch), ch + ".x1"), nombre(exiger(f, "y1", ch), ch + ".y1"),
                                  nombre(exiger(f, "x2", ch), ch + ".x2"), nombre(exiger(f, "y2", ch), ch + ".y2"),
                                  nombre(exiger(f, "largeur", ch), ch + ".largeur") / 2.0)
            masque = sc.masque(exiger(f, "couches", ch), ch + ".couches")
        elif typ == "via":
            x = nombre(exiger(f, "x", ch), ch + ".x")
            y = nombre(exiger(f, "y", ch), ch + ".y")
            forme = forme_point(x, y, nombre(exiger(f, "diametre", ch), ch + ".diametre") / 2.0)
            masque = sc.masque(f["couches"], ch + ".couches") if f.get("couches") else sc.tout
            if f.get("trou"):
                info["trou"] = forme_point(x, y, nombre(f["trou"], ch + ".trou") / 2.0)
        elif typ == "region":
            forme = forme_polygone(points(exiger(f, "points", ch), ch + ".points"),
                                   [points(t, "%s.trous[%d]" % (ch, i)) for i, t in enumerate(f.get("trous") or [])],
                                   chemin=ch + ".points")
            masque = sc.masque(exiger(f, "couches", ch), ch + ".couches")
        else:
            raise ErreurEntree("%s.type : « %s » inconnu (piste, via ou region)" % (ch, typ))
        k_objet = sc.ajouter(Objet(fid, CUIVRE_FIXE, reseau, masque, forme, classe=f.get("classe_isolement"),
                                   info=info))
        if info.get("trou") is not None:
            sc.parois.append({"id": fid, "forme": info["trou"], "metallise": True, "objet": k_objet})


def _poser_trous(sc, donnees):
    for k, t in enumerate(liste(exiger(donnees, "trous", "donnees"), "donnees.trous")):
        ch = "donnees.trous[%d]" % k
        objet_json(t, ch)
        if t.get("piece"):
            continue
        tid = t.get("id") or "T%d" % k
        x = nombre(exiger(t, "x", ch), ch + ".x")
        y = nombre(exiger(t, "y", ch), ch + ".y")
        rot = nombre(t.get("rotation", 0.0) or 0.0, ch + ".rotation")
        trou = objet_json(exiger(t, "trou", ch), ch + ".trou")
        tx, ty = _decaler(x, y, rot, trou.get("decalage"), ch + ".trou.decalage")
        paroi = forme_trou(trou, tx, ty, rot + nombre(trou.get("rotation", 0.0) or 0.0, ch + ".trou.rotation"),
                           ch + ".trou")
        metallise = booleen(t.get("metallise", False), ch + ".metallise")
        dc = nombre(t.get("diametre_cuivre", 0.0) or 0.0, ch + ".diametre_cuivre")
        k_objet = None
        if dc > 0.0:
            k_objet = sc.ajouter(Objet(tid, CUIVRE_TROU, "", sc.tout, forme_point(x, y, dc / 2.0),
                                       classe=t.get("classe_isolement"),
                                       info={"k": k, "trou": paroi, "metallise": metallise, "source": t}))
        sc.parois.append({"id": tid, "forme": paroi, "metallise": metallise, "cuivre": dc,
                          "diametre": nombre(exiger(trou, "diametre", ch + ".trou"), ch + ".trou.diametre"),
                          "objet": k_objet})


def _poser_zones_plan(sc):
    for r, c in enumerate(sc.donnees_couches):
        ch = "donnees.couches[%d]" % r
        for k, z in enumerate(liste(c.get("zones_plan") or [], ch + ".zones_plan")):
            chz = "%s.zones_plan[%d]" % (ch, k)
            objet_json(z, chz)
            forme = forme_polygone(points(exiger(z, "contour", chz), chz + ".contour"),
                                   [points(t, "%s.trous[%d]" % (chz, i)) for i, t in enumerate(z.get("trous") or [])],
                                   chemin=chz + ".contour")
            sc.ajouter(Objet("Z:%s/%d" % (sc.couches[r], k), ZONE_PLAN,
                             texte(exiger(z, "reseau", chz), chz + ".reseau"), 1 << r, forme,
                             info={"k": k, "couche": sc.couches[r]}))


def _verifier_unite(objet, nom, attendu="mil"):
    unite = objet.get("unite", attendu)
    if unite != attendu:
        raise ErreurEntree("%s.unite : « %s » non pris en charge (attendu : %s)" % (nom, unite, attendu))


def pistes_du_routage(routage):
    """Pistes d'un routage ``routage/2`` ou v1 : liste de ``(k, x1, y1, x2, y2, largeur_mm, largeur_mil, couche,
    reseau, longueur_mm)`` en mm ; longueur = hypot des écarts en mil x 0,0254 (convention 13 du corpus, comme
    ``validite.rs``)."""
    if not isinstance(routage, dict):
        raise ErreurEntree("routage : objet attendu")
    lu = format_lu(routage, ("routage/2", "routage/1"))
    if lu not in ("routage/2", "routage/1"):
        raise ErreurEntree("routage : format %s non pris en charge (attendu : routage/2 ou v1)"
                           % routage.get("format"))
    _verifier_unite(routage, "routage")
    res = []
    for k, s in enumerate(liste(exiger(routage, "pistes", "routage"), "routage.pistes")):
        ch = "routage.pistes[%d]" % k
        w_mil = nombre(exiger(s, "width", ch), ch + ".width")
        x1 = nombre(exiger(s, "x1", ch), ch + ".x1")
        y1 = nombre(exiger(s, "y1", ch), ch + ".y1")
        x2 = nombre(exiger(s, "x2", ch), ch + ".x2")
        y2 = nombre(exiger(s, "y2", ch), ch + ".y2")
        res.append((k, x1 * MIL, y1 * MIL, x2 * MIL, y2 * MIL, w_mil * MIL, w_mil,
                    texte(exiger(s, "layer", ch), ch + ".layer"), texte(exiger(s, "net_name", ch), ch + ".net_name"),
                    math.hypot(x2 - x1, y2 - y1) * MIL))
    return res


def _masque_via(sc, v, ch):
    de, a = v.get("de"), v.get("a")
    if de is None and a is None:
        return sc.tout
    for cle, val in (("de", de), ("a", a)):
        if val is not None:
            texte(val, "%s.%s" % (ch, cle))
    r1 = sc.rang.get(de)
    r2 = sc.rang.get(a)
    if r1 is None or r2 is None:
        raise ErreurEntree("%s : couches « %s » et « %s » d'un via inconnues" % (ch, de, a))
    lo, hi = min(r1, r2), max(r1, r2)
    return ((1 << (hi + 1)) - 1) & ~((1 << lo) - 1)


def _poser_routage(sc, routage):
    for k, x1, y1, x2, y2, w, w_mil, couche, net, lg in pistes_du_routage(routage):
        r = sc.rang.get(couche)
        info = {"k": k, "a": (x1, y1), "b": (x2, y2), "largeur": w, "largeur_mil": w_mil, "couche": couche,
                "couche_connue": r is not None, "longueur": lg}
        sc.pistes.append(sc.ajouter(Objet("P%d" % k, PISTE, net, 0 if r is None else 1 << r,
                                          forme_segment(x1, y1, x2, y2, w / 2.0), info=info)))
    for k, v in enumerate(liste(exiger(routage, "vias", "routage"), "routage.vias")):
        ch = "routage.vias[%d]" % k
        x = nombre(exiger(v, "x", ch), ch + ".x") * MIL
        y = nombre(exiger(v, "y", ch), ch + ".y") * MIL
        d = nombre(exiger(v, "size", ch), ch + ".size") * MIL
        t = nombre(exiger(v, "hole_size", ch), ch + ".hole_size") * MIL
        paroi = forme_point(x, y, t / 2.0)
        ko = sc.ajouter(Objet("V%d" % k, VIA, texte(exiger(v, "net", ch), ch + ".net"), _masque_via(sc, v, ch),
                              forme_point(x, y, d / 2.0), info={"k": k, "diametre": d, "trou_mm": t, "trou": paroi}))
        sc.vias.append(ko)
        sc.parois.append({"id": "V%d" % k, "forme": paroi, "metallise": True, "objet": ko})
    for k, s in enumerate(liste(routage.get("surfaces") or [], "routage.surfaces")):
        ch = "routage.surfaces[%d]" % k
        objet_json(s, ch)
        couche = texte(exiger(s, "layer", ch), ch + ".layer")
        r = sc.rang.get(couche)
        forme = forme_polygone(points(exiger(s, "contour", ch), ch + ".contour", MIL),
                               [points(t, "%s.trous[%d]" % (ch, i), MIL) for i, t in enumerate(s.get("trous") or [])],
                               chemin=ch + ".contour")
        sc.surfaces.append(sc.ajouter(Objet("S%d" % k, SURFACE, texte(exiger(s, "net", ch), ch + ".net"),
                                            0 if r is None else 1 << r, forme,
                                            info={"k": k, "couche": couche, "couche_connue": r is not None})))


def _poser_couture(sc, couture):
    if not isinstance(couture, dict):
        raise ErreurEntree("couture : objet attendu")
    lu = format_lu(couture, ("vias_couture/2", "vias_couture/1"))
    if lu not in ("vias_couture/2", "vias_couture/1"):
        raise ErreurEntree("couture : format %s non pris en charge (attendu : vias_couture/2)" % couture.get("format"))
    _verifier_unite(couture, "couture")
    for k, v in enumerate(liste(exiger(couture, "vias", "couture"), "couture.vias")):
        ch = "couture.vias[%d]" % k
        x = nombre(exiger(v, "x", ch), ch + ".x") * MIL
        y = nombre(exiger(v, "y", ch), ch + ".y") * MIL
        d = nombre(exiger(v, "size", ch), ch + ".size") * MIL
        t = nombre(exiger(v, "hole_size", ch), ch + ".hole_size") * MIL
        paroi = forme_point(x, y, t / 2.0)
        ko = sc.ajouter(Objet("C%d" % k, VIA_COUTURE, texte(exiger(v, "net", ch), ch + ".net"), _masque_via(sc, v, ch),
                              forme_point(x, y, d / 2.0), info={"k": k, "diametre": d, "trou_mm": t, "trou": paroi}))
        sc.couture.append(ko)
        sc.parois.append({"id": "C%d" % k, "forme": paroi, "metallise": True, "objet": ko})


def poser(donnees, placements=None, routage=None, couture=None):
    """Pose tous les objets de cuivre (§4.1) : pastilles (pièces virtuelles comprises), régions, vias d'empreinte,
    cuivre fixe, cuivre de trou, zones de plan, puis pistes, vias et surfaces du routage, et vias de couture."""
    sc = Scene(donnees)
    sc.poses, sc.infos_placement = poses_des_pieces(donnees, placements)
    for ic, comp in enumerate(donnees["composants"]):
        _poser_composant(sc, ic, comp)
    _poser_cuivre_fixe(sc, donnees)
    _poser_trous(sc, donnees)
    _poser_zones_plan(sc)
    if routage is not None:
        _poser_routage(sc, routage)
    if couture is not None:
        _poser_couture(sc, couture)
    return sc


# ---------------------------------------------------------------------------------------------------------------------
# Index de cases
# ---------------------------------------------------------------------------------------------------------------------
class IndexCases:
    """Cases [i, i+1[ x [j, j+1[ (côté ``case``) du repère commun ; un objet est rangé dans chaque case que touche sa
    boîte élargie de ``marge``. Un objet dont la boîte couvre plus de ``SEUIL_CASES_OBJET`` cases est rangé à part
    avec sa plage (``grands``) : il partage une case avec une requête dès que les plages se recoupent ; une requête
    qui couvre plus de cases que l'index n'en occupe parcourt les cases occupées. Mémoire et temps restent ainsi
    proportionnels au nombre d'objets, quelle que soit la taille d'un objet."""

    __slots__ = ("case", "marge", "cases", "grands")

    def __init__(self, case=CASE_ECART_MM, marge=ELARGISSEMENT_ECART_MM):
        self.case = float(case)
        self.marge = float(marge)
        self.cases = {}
        self.grands = []

    def plage(self, boite):
        c = self.case
        m = self.marge
        return (int((boite[0] - m) // c), int((boite[1] - m) // c), int((boite[2] + m) // c), int((boite[3] + m) // c))

    def ajouter(self, k, boite):
        i0, j0, i1, j1 = p = self.plage(boite)
        if (i1 - i0 + 1) * (j1 - j0 + 1) > SEUIL_CASES_OBJET:
            self.grands.append((k, p))
            return
        cases = self.cases
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                lst = cases.get((i, j))
                if lst is None:
                    cases[(i, j)] = [k]
                else:
                    lst.append(k)

    def listes(self, boite):
        """Listes d'indices des objets qui partagent une case avec la boîte élargie de ``marge`` (un indice peut
        revenir dans plusieurs listes)."""
        i0, j0, i1, j1 = self.plage(boite)
        if self.grands:
            lst = [k for k, (a0, b0, a1, b1) in self.grands if a0 <= i1 and i0 <= a1 and b0 <= j1 and j0 <= b1]
            if lst:
                yield lst
        cases = self.cases
        if (i1 - i0 + 1) * (j1 - j0 + 1) > len(cases):
            for (i, j), lst in cases.items():
                if i0 <= i <= i1 and j0 <= j <= j1:
                    yield lst
            return
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                lst = cases.get((i, j))
                if lst:
                    yield lst


# ---------------------------------------------------------------------------------------------------------------------
# Écart, coudes, mesures du verdict, coût commun (§2.8, §4.7)
# ---------------------------------------------------------------------------------------------------------------------
def ecarts_pistes(sc):
    """Écart de chaque piste neuve (§4.7) : plus petite distance bord à bord (<= 0 en recouvrement) à un objet d'un
    autre réseau ou sans réseau, sur une couche commune, dont la boîte élargie de 0,3 mm partage une case de 1 mm
    avec celle de la piste. Rend ``{indice de la piste dans sc.objets: écart}`` ; une piste sans voisin est absente."""
    objets = sc.objets
    index = IndexCases(CASE_ECART_MM, ELARGISSEMENT_ECART_MM)
    for k, o in enumerate(objets):
        if o.genre != ZONE_PLAN and o.masque:
            index.ajouter(k, o.boite)
    marque = [-1] * len(objets)
    res = {}
    for k in sc.pistes:
        o = objets[k]
        if not o.masque:
            continue
        meilleur = None
        bo = o.boite
        for lst in index.listes(bo):
            for j in lst:
                if marque[j] == k or j == k:
                    continue
                marque[j] = k
                q = objets[j]
                if not (q.masque & o.masque) or meme_reseau(o, q):
                    continue
                # élagage exact : l'écart de deux objets est au moins la distance de leurs boîtes ; une boîte qui
                # touche (distance nulle) peut cacher un recouvrement plus profond que le meilleur, même négatif
                if meilleur is not None:
                    db = distance_boites(bo, q.boite)
                    if db > 0.0 and db >= meilleur + MARGE_ELAGAGE_MM:
                        continue
                e = ecart_objets(o, q)
                if meilleur is None or e < meilleur:
                    meilleur = e
        if meilleur is not None:
            res[k] = meilleur
    return res


def arrondi_millieme(v):
    """Entier le plus proche de 1000 x v, égalité au pair (``round`` de Python, ``round_ties_even`` de Rust :
    convention 12 du corpus du lot E, comme ``round(x, 3)`` de ``drc_maison.py`` à ``9b5b5a3``)."""
    return int(round(v * 1000.0))


def compter_coudes(segments):
    """Coudes (§4.7) d'une suite de segments ``(reseau, couche, x1, y1, x2, y2)`` (mm) : jonctions d'exactement deux
    extrémités d'un même réseau et d'une même couche au même point arrondi au millième de mm, dont le cosinus de
    l'angle entre les deux directions sortantes dépasse -0,999. Rend ``(coudes, coudes_90, angles_aigus)``."""
    bouts = {}
    for net, couche, x1, y1, x2, y2 in segments:
        for (px, py), (qx, qy) in (((x1, y1), (x2, y2)), ((x2, y2), (x1, y1))):
            cle = (net, couche, arrondi_millieme(px), arrondi_millieme(py))
            v = (qx - px, qy - py)
            lst = bouts.get(cle)
            if lst is None:
                bouts[cle] = [v]
            else:
                lst.append(v)
    coudes = droits = aigus = 0
    for vs in bouts.values():
        if len(vs) != 2:
            continue
        (ax, ay), (bx, by) = vs
        na = math.hypot(ax, ay)
        nb = math.hypot(bx, by)
        if na == 0.0 or nb == 0.0:
            continue
        cos = (ax * bx + ay * by) / (na * nb)
        if cos > COS_COUDE:
            coudes += 1
        if cos > COS_DROIT:
            aigus += 1
        elif abs(cos) <= COS_DROIT:
            droits += 1
    return coudes, droits, aigus


def _bilan(reseaux, pistes):
    """Longueur, rétréci et résistance d'une suite ``(reseau, longueur_mm, largeur_mm)``."""
    longueurs = []
    retrecis = []
    resistances = []
    for net, lg, w in pistes:
        longueurs.append(lg)
        r = reseaux.get(net)
        if r is None:
            continue
        lw = r.get("largeurs_mm")
        if isinstance(lw, list) and len(lw) >= 2 and lw[0] is not None and lw[1] is not None:
            if w < max(float(lw[1]), float(lw[0])) - TOLERANCE_RETRECI_MM:
                retrecis.append(lg)
        if r.get("role") in ROLES_RESISTANCE and w > 0.0:
            resistances.append(MOHM_PAR_CARRE * lg / w)
    return math.fsum(longueurs), math.fsum(retrecis), math.fsum(resistances)


def verifier_reseau(r, chemin):
    """Types des champs d'un réseau que lisent les mesures (§2.1) : ``nom`` texte, ``role`` texte ou null,
    ``largeurs_mm`` [min, pref, max|null] de nombres positifs ou nuls (null admis) ; rend le nom."""
    nom = texte(exiger(r, "nom", chemin), chemin + ".nom")
    role = r.get("role")
    if role is not None:
        texte(role, chemin + ".role")
    lw = r.get("largeurs_mm")
    if lw is not None:
        liste(lw, chemin + ".largeurs_mm")
        if len(lw) not in (2, 3):
            raise ErreurEntree("%s.largeurs_mm : [min, pref, max|null] attendu" % chemin)
        for i, x in enumerate(lw):
            if x is not None and nombre(x, "%s.largeurs_mm[%d]" % (chemin, i)) < 0.0:
                raise ErreurEntree("%s.largeurs_mm[%d] : valeur négative" % (chemin, i))
    return nom


def reseaux_des_donnees(donnees):
    """``{nom: réseau}`` des réseaux des données, types vérifiés (``verifier_reseau``), noms uniques."""
    res = {}
    for i, r in enumerate(liste(exiger(donnees, "reseaux", "donnees"), "donnees.reseaux")):
        ch = "donnees.reseaux[%d]" % i
        nom = verifier_reseau(r, ch)
        if nom in res:
            raise ErreurEntree("%s : réseau %s en double" % (ch, nom))
        res[nom] = r
    return res


def bilan_pistes(donnees, routage):
    """``{"longueur_mm", "retreci_mm", "resistance_puissance_mohm"}`` des pistes du routage (§4.7) : préférée =
    max(largeurs_mm[1], largeurs_mm[0]) du réseau ; résistance 0,5 x L / l sur les rôles puissance et alimentation."""
    reseaux = reseaux_des_donnees(donnees)
    lg, ret, res = _bilan(reseaux, ((net, longueur, w)
                                    for _k, _x1, _y1, _x2, _y2, w, _wm, _c, net, longueur in pistes_du_routage(routage)))
    return {"longueur_mm": lg, "retreci_mm": ret, "resistance_puissance_mohm": res}


def mesures_routage(sc, ecarts=None):
    """Mesures d'un verdict (§2.8, définitions §4.7) : ``{"ecart_moyen_mm", "ecart_min_mm", "coudes", "coudes_90",
    "angles_aigus", "longueur_pistes_mm", "resistance_puissance_mohm", "vias"}`` ; ``vias`` = vias du routage
    (couture exclue)."""
    plafond = FACTEUR_PLAFOND_ECART * sc.isolement_defaut
    if ecarts is None:
        ecarts = ecarts_pistes(sc)
    valeurs = [min(plafond, ecarts[k]) for k in sc.pistes if k in ecarts]
    objets = sc.objets
    pistes = [objets[k] for k in sc.pistes]
    coudes, droits, aigus = compter_coudes(
        (o.reseau, o.info["couche"], o.info["a"][0], o.info["a"][1], o.info["b"][0], o.info["b"][1]) for o in pistes)
    lg, _ret, res = _bilan(sc.reseaux, ((o.reseau, o.info["longueur"], o.info["largeur"]) for o in pistes))
    return {"ecart_moyen_mm": math.fsum(valeurs) / len(valeurs) if valeurs else 0.0,
            "ecart_min_mm": min(valeurs) if valeurs else None,
            "coudes": coudes, "coudes_90": droits, "angles_aigus": aigus,
            "longueur_pistes_mm": lg, "resistance_puissance_mohm": res, "vias": len(sc.vias)}


TERMES_COUT = ("longueur_mm", "vias", "retreci_mm", "resistance_puissance_mohm", "coudes", "ecart_moyen_mm")


def cout_commun(termes, defaut_isolement):
    """Coût commun ``cout_commun/2`` (§4.7) : longueur + 1,5 x vias + 2,0 x rétréci + 3,0 x résistance + 0,1 x coudes
    + 1000 x max(0 ; s - écart moyen), s = 3 x isolement.defaut."""
    s = FACTEUR_SEUIL_ECART * defaut_isolement
    return (termes["longueur_mm"] + 1.5 * termes["vias"] + 2.0 * termes["retreci_mm"]
            + 3.0 * termes["resistance_puissance_mohm"] + 0.1 * termes["coudes"]
            + 1000.0 * max(0.0, s - termes["ecart_moyen_mm"]))


# ---------------------------------------------------------------------------------------------------------------------
# Outils des contrôles (étape C3)
# ---------------------------------------------------------------------------------------------------------------------
def _proche_sur_segment(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    t = 0.0
    if l2 != 0.0:
        t = min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return ax + t * dx, ay + t * dy


def _proches_segments(s, t):
    """Points les plus proches de deux segments (intersection si ils se coupent)."""
    ax, ay, bx, by = s
    cx, cy, dx, dy = t
    if _croisent(ax, ay, bx, by, cx, cy, dx, dy):
        den = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
        u = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / den
        p = (ax + u * (bx - ax), ay + u * (by - ay))
        return p, p
    best = None
    for p, (qa, qb, qc, qd) in (((ax, ay), t), ((bx, by), t), ((cx, cy), s), ((dx, dy), s)):
        q = _proche_sur_segment(p[0], p[1], qa, qb, qc, qd)
        d = math.hypot(p[0] - q[0], p[1] - q[1])
        if best is None or d < best[0]:
            best = (d, p, q) if (qa, qb, qc, qd) == t else (d, q, p)
    return best[1], best[2]


def _segments_noyau(f):
    if f.genre == POINT:
        return [(f.x1, f.y1, f.x1, f.y1)]
    if f.genre == SEGMENT:
        return [(f.x1, f.y1, f.x2, f.y2)]
    return f.aretes


def milieu_proches(f, g):
    """Milieu des deux points les plus proches des noyaux de deux formes (convention 4 du corpus)."""
    best = None
    for s in _segments_noyau(f):
        for t in _segments_noyau(g):
            p, q = _proches_segments(s, t)
            d = math.hypot(p[0] - q[0], p[1] - q[1])
            if best is None or d < best[0]:
                best = (d, p, q)
    _d, p, q = best
    return [(p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0]


def premier_point(f):
    """Premier point du noyau d'une forme."""
    if f.genre == POLYGONE:
        return f.anneaux[0][0]
    return f.x1, f.y1


def rayon_circonscrit(f, cx, cy):
    """Rayon du plus petit disque de centre (cx, cy) qui contient la forme."""
    if f.genre == POINT:
        pts = [(f.x1, f.y1)]
    elif f.genre == SEGMENT:
        pts = [(f.x1, f.y1), (f.x2, f.y2)]
    else:
        pts = f.anneaux[0]
    return max(math.hypot(x - cx, y - cy) for x, y in pts) + f.rayon


def inclus_dans_polygone(f, z, eps=EPS_CONTACT):
    """La forme ``f`` est entièrement dans le noyau polygonal de ``z`` (à ``eps`` près)."""
    for s in _segments_noyau(f):
        for x, y in ((s[0], s[1]), (s[2], s[3])):
            if not contient(z, x, y) and _d_point_bord(z, x, y) > eps:
                return False
    m = math.inf
    for s in _segments_noyau(f):
        for a in z.aretes:
            if _croisent(s[0], s[1], s[2], s[3], a[0], a[1], a[2], a[3]):
                return False
            d = d_segment_segment(s[0], s[1], s[2], s[3], a[0], a[1], a[2], a[3])
            if d < m:
                m = d
    if m < f.rayon - eps:
        return False
    if f.genre == POLYGONE:
        for t in z.anneaux[1:]:
            if contient(f, t[0][0], t[0][1]):
                return False
    return True


def ecart_au_bord(f, contour):
    """Écart d'une forme au bord d'un polygone ``contour`` vu de l'intérieur : distance du noyau au bord moins le
    rayon, négative si une partie de la forme est dehors."""
    m = math.inf
    for s in _segments_noyau(f):
        for a in contour.aretes:
            d = d_segment_segment(s[0], s[1], s[2], s[3], a[0], a[1], a[2], a[3])
            if d < m:
                m = d
    x, y = premier_point(f)
    if not contient(contour, x, y):
        return -m - f.rayon if m > 0.0 else -f.rayon
    return m - f.rayon


def ecart_aux_aretes(f, aretes):
    """Écart d'une forme à une liste d'arêtes ``(x1, y1, x2, y2)`` : distance du noyau moins le rayon (+inf sans
    arête). Sert à la marge de bord d'une pièce ``au_bord``, dispensée des arêtes du côté demandé."""
    m = math.inf
    for s in _segments_noyau(f):
        for a in aretes:
            d = d_segment_segment(s[0], s[1], s[2], s[3], a[0], a[1], a[2], a[3])
            if d < m:
                m = d
    return m - f.rayon


def intervalles_hors(ax, ay, bx, by, formes, n=32, iterations=40):
    """Morceaux ``[(x1, y1, x2, y2)]`` de l'axe [ab] à distance signée > 0 de toutes les ``formes`` (partie d'une
    piste hors des pastilles de son réseau, exemption de ``garde_bord``). Échantillonnage de ``n`` points puis
    dichotomie sur chaque changement ; un morceau douteux est gardé (approché par excès)."""
    def dehors(t):
        x = ax + t * (bx - ax)
        y = ay + t * (by - ay)
        return all(distance_signee(g, x, y) > 0.0 for g in formes)
    ts = [i / n for i in range(n + 1)]
    etats = [dehors(t) for t in ts]
    bornes = []
    for i in range(n):
        if etats[i] != etats[i + 1]:
            lo, hi = ts[i], ts[i + 1]
            for _ in range(iterations):
                mi = (lo + hi) / 2.0
                if dehors(mi) == etats[i]:
                    lo = mi
                else:
                    hi = mi
            bornes.append((lo, hi, etats[i]))
    morceaux = []
    debut = 0.0 if etats[0] else None
    for lo, hi, etat in bornes:
        if etat:          # dehors -> dedans
            morceaux.append((debut, hi))
            debut = None
        else:             # dedans -> dehors
            debut = lo
    if debut is not None:
        morceaux.append((debut, 1.0))
    return [(ax + t0 * (bx - ax), ay + t0 * (by - ay), ax + t1 * (bx - ax), ay + t1 * (by - ay))
            for t0, t1 in morceaux]
