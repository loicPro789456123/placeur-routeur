# -*- coding: utf-8 -*-
"""Validateurs des formats du contrat (§2, §5.1) et lecture stricte des fichiers écrits à la main (lot E).

API (§5.2), fonctions pures, sans lecture de la ligne de commande ni de l'environnement, sans état global :

  charger(chemin, format_attendu) -> dict      lecture, contrôle de la version, validation ; ErreurFormat sinon
                                               (une liste pour un placement de forme v1)
  valider_<nom>(d) -> list[str]                un validateur par format (21) : liste des erreurs, vide si conforme
  lire_contraintes(chemin) -> (dict, list)     lecture stricte ; une version 1 est convertie en contraintes/2
                                               (options config=…, plans_deduits=… : voir la fonction)
  placements_en_liste(p, donnees) -> list      poses de toutes les pièces, placement v1 ou v2
  ErreurFormat                                 entrée invalide (code 2)
Ajouts du lot E (hors §5.2) : valider_config_utilisateur(d) (fichier --config partiel, ou v1),
lire_parametres_moteur(chemin), lire_config_utilisateur(chemin) -> (dict, avertissements).

Ligne de commande : ``python formats.py valider FICHIER [--config-utilisateur]`` ; codes (§1.4) : 0 conforme,
2 non conforme ou illisible (ou usage incorrect), 4 erreur interne. Messages : « <fichier> : <chemin du champ> :
<cause> », nom de fichier seul, jamais de dossier (§0.2-7) ; une entrée, quelle qu'elle soit, ne provoque jamais
d'erreur interne. Formes reconnues sans « format » : placement v1 (liste nue), routage v1 (pistes et vias),
configuration v1 (convertisseurs), contraintes v1 (clés de contraintes en majorité).

Lecture JSON (toutes les fonctions qui lisent un fichier) au moins aussi stricte que celle du moteur et de
validite.rs (serde_json), pour que formats.py ne déclare jamais conforme un fichier que le lecteur Rust refuse :
NaN et Infinity refusés, nombres de valeur absolue supérieure à BORNE_NOMBRES (1e308) refusés, demi-caractères de
substitution isolés (« \\ud800 ») refusés, imbrication limitée à PROFONDEUR_MAX (127) niveaux de listes et d'objets.
Refusées en plus (serde_json garde la dernière valeur) : les clés en double d'un même objet, sauf les commentaires
« _* » (§0.5).

État : étape 3 du plan du lot E. Fait :
  - lecture UTF-8, BOM accepté ; JSON comme ci-dessus, erreur traduite avec sa ligne, sa colonne et sa cause
    probable (virgule finale, apostrophes, barre oblique inverse non doublée, virgule décimale, commentaire, clé
    sans guillemets, True/False/None) ; fichiers écrits à la main : repli cp1252 signalé par un avertissement
    (charger, lui, n'accepte que l'UTF-8) ;
  - champ « format » : nom attendu et version majeure prise en charge (« format X/N non pris en charge », §0.5) ;
  - formats écrits par un programme (19, dont placements et routage v1) : schéma déclaratif complet (langage
    ci-dessous) parcouru avec le chemin du champ : champs obligatoires (§2.1), types (entier ≠ flottant, nombre
    entre guillemets signalé), énumérations, null seulement où la table le permet, champs supprimés refusés
    (§2.5, §2.7), champs facultatifs de l'avenant 2.2 (A.5, A.6) acceptés et typés ; références croisées internes
    (§2.1) ; cohérence interne (copies de §2.5, sommes et détails d'un verdict, formule du coût commun, noms
    uniques, identifiants de §0.4) ;
  - fichiers écrits à la main (contraintes/2 et contraintes_effectives, contraintes v1, fichier --config partiel
    ou v1, parametres_moteur/1 partiel ou complet) : schéma strict à tout niveau, clé inconnue refusée avec la clé
    la plus proche (§0.5), clés « _* » ignorées, types, énumérations, null seulement où le contrat le permet,
    longueurs de listes avec l'indice « virgule décimale ? », nombre entre guillemets refusé ;
  - formes v1 : placement en liste nue, routage sans « format » ; contraintes v1 converties selon toutes les règles
    de §2.3 (soft_sw et eloignement d'une configuration v1 avec l'option config, plans en contrôle de cohérence).
Les désignateurs, réseaux et couches d'un fichier écrit à la main ne sont pas résolus ici (le fichier n'en porte
pas la liste) : construire_donnees (lot B) les résout contre la carte.
"""
import argparse
import difflib
import json
import os
import re
import sys

VERSION = "0.3-e3"

MIL = 0.0254
"""1 mil en mm (§0.4)."""

PROFONDEUR_MAX = 127
"""Imbrication maximale des listes et des objets d'un document JSON : celle que lit serde_json (lecteur du moteur et
de validite.rs : 127 niveaux lus, 128 refusés)."""

BORNE_NOMBRES = 1e308
"""Plus grande valeur absolue acceptée pour un nombre JSON. serde_json refuse un nombre au-delà du plus grand
flottant (≈ 1,8e308, et parfois un peu en deçà : son arrondi n'est pas exact près de cette limite) ; avec 1e308,
formats.py est toujours au moins aussi strict."""

FORMATS = {
    "entree_carte": 2,
    "contraintes": 2,
    "config_carte": 2,
    "donnees_placeur": 2,
    "parametres_moteur": 1,
    "placements": 2,
    "routage": 2,
    "stats": 2,
    "verdict": 2,
    "controle_placement": 1,
    "mesures": 2,
    "proprete": 1,
    "mesure_convertisseurs": 1,
    "vias_couture": 2,
    "controle_gabarit": 1,
    "config_pcbdoc": 2,
    "rapport_pcbdoc": 1,
    "deduction": 1,
    "rapport": 2,
    "estimation": 1,
    "etalonnage": 1,
}
"""Formats pris en charge : nom → version majeure (§2 et §5.1)."""

CLES_CONTRAINTES = (
    "format", "repere", "unite", "fixes", "fixes_deplaces", "liberer", "fixer_suggestions", "fixer_verrouilles",
    "dessus_seulement", "dessous_seulement", "orientations", "faces_par_defaut", "rotations_dessous_multibroches",
    "hauteur_max_dessus_mm", "hauteur_max_dessous_mm", "hauteur_inconnue", "exceptions_hauteur", "face_encombrants",
    "boites", "au_bord", "zones_imposees", "salles_comme_zones", "groupes", "groupes_exclus", "motif_canaux",
    "pas_fin_mm", "via_sous_cms_permis", "via_dans_pastille_meme_reseau", "sans_piste_dessous", "zones_interdites",
    "zones_autour", "marge_bord_mm", "marge_decoupe_mm", "garde_bord_mm", "garde_decoupe_mm", "garde_trou_mm",
    "marge_piece_mm", "marge_traversante_mm", "grille_placement_mm", "rayon_interdit_trou_mm",
    "trous_supplementaires", "cuivre_a_garder", "couches", "couche_preferee_par_nom", "roles", "reseaux", "paires",
    "eloignements", "charges", "convertisseurs",
)
"""Clés de premier niveau de contraintes/2 (§2.3)."""

RENOMMAGES_CONTRAINTES_V1 = {
    "hauteur_max_dessous": ("hauteur_max_dessous_mm",),
    "marge_bord": ("marge_bord_mm",),
    "marge_decoupe": ("marge_decoupe_mm",),
    "rayon_interdit_trou": ("rayon_interdit_trou_mm",),
    "garde_bord_fentes_trous": ("garde_bord_mm", "garde_decoupe_mm"),
}
"""Clés de la version 1 des contraintes renommées en version 2 (§2.3)."""

SOFT_SW_DISTANCE_MM = 30.0
SOFT_SW_POIDS_PAR_MM = 5.0
"""Conversion de ``soft_sw`` (contraintes v1) en éloignement souple : distance et poids de 9b5b5a3 (``SW_DIST``,
``SW_SOFT_W``, ``geom.rs:14-17``), repris par §2.3."""

CLES_PARAMETRES_MOTEUR = ("format", "campagne", "placement", "recuit", "boucle", "routage", "finition", "conjointe",
                          "couts", "roles", "sensibilites", "couche_preferee", "journal")
"""Clés de premier niveau de parametres_moteur/1 (annexe C)."""

_MESSAGES_JSON = {
    "Expecting value": "valeur attendue",
    "Expecting ',' delimiter": "virgule attendue",
    "Expecting ':' delimiter": "deux-points attendus",
    "Expecting property name enclosed in double quotes": "nom de clé entre guillemets doubles attendu",
    "Unterminated string starting at": "chaîne non terminée",
    "Invalid control character at": "caractère de contrôle dans une chaîne",
    "Invalid \\escape": "séquence d'échappement invalide",
    "Invalid \\uXXXX escape": "séquence \\uXXXX invalide",
    "Extra data": "données en trop après la fin du document",
    "Illegal trailing comma before end of object": "virgule finale avant la fin de l'objet",
    "Illegal trailing comma before end of array": "virgule finale avant la fin de la liste",
}


class ErreurFormat(ValueError):
    """Entrée invalide (code 2). ``erreurs`` : messages « <fichier> : <chemin> : <cause> », sans dossier."""

    def __init__(self, erreurs):
        if isinstance(erreurs, str):
            erreurs = [erreurs]
        self.erreurs = list(erreurs)
        super().__init__("\n".join(self.erreurs))


# ---------------------------------------------------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------------------------------------------------

def _nom_fichier(chemin):
    return os.path.basename(os.fspath(chemin))


def _lire_octets(chemin):
    nom = _nom_fichier(chemin)
    try:
        with open(chemin, "rb") as f:
            return f.read()
    except FileNotFoundError:
        raise ErreurFormat(f"{nom} : fichier introuvable") from None
    except (IsADirectoryError, PermissionError):
        raise ErreurFormat(f"{nom} : lecture refusée (dossier, ou accès refusé)") from None
    except OSError:
        raise ErreurFormat(f"{nom} : lecture impossible") from None


def _decoder_texte(octets, nom, repli=False):
    """(texte, avertissements) : UTF-8, BOM accepté (bloc-notes) ; avec ``repli`` (fichiers écrits à la main, §2.1),
    un fichier qui n'est pas en UTF-8 est lu en cp1252 avec un avertissement ; ErreurFormat sinon."""
    try:
        return octets.decode("utf-8-sig"), []
    except UnicodeDecodeError as e:
        debut = e.start
    if repli:
        try:
            texte = octets.decode("cp1252")
        except UnicodeDecodeError:
            raise ErreurFormat(f"{nom} : encodage illisible à l'octet {debut} (UTF-8 ou cp1252 attendu)") from None
        return texte, [f"{nom} : pas en UTF-8 (octet {debut}) : lu en cp1252 (Windows-1252) ; "
                       f"l'enregistrer en UTF-8"]
    raise ErreurFormat(f"{nom} : encodage illisible à l'octet {debut} (UTF-8 attendu)")


def _lire_texte(chemin):
    """Texte UTF-8 du fichier (BOM accepté) ; ErreurFormat qui ne nomme que le fichier."""
    return _decoder_texte(_lire_octets(chemin), _nom_fichier(chemin))[0]


def _refuser_constante(nom):
    raise ValueError(f"valeur {nom} non permise en JSON")


def _message_json(msg):
    return _MESSAGES_JSON.get(msg, msg)


def _cause_probable(texte, e):
    """Cause probable d'une erreur JSON (§2.1 : virgule finale, apostrophes, barre oblique inverse non doublée…),
    déduite du caractère fautif et de celui qui le précède ; "" si aucune."""
    pos = min(max(e.pos, 0), len(texte))
    c = texte[pos:pos + 1]
    avant = texte[:pos].rstrip()
    prec = avant[-1:]
    if e.msg.startswith("Invalid \\escape") or e.msg.startswith("Invalid \\u"):
        return "barre oblique inverse non doublée ? écrire \\\\ ou /"
    if "trailing comma" in e.msg or (c in ("}", "]") and prec == ","):
        return "virgule finale ?"
    if c == "'":
        return "apostrophes au lieu de guillemets doubles ?"
    if texte[pos:pos + 2] in ("//", "/*") or c == "#":
        return "commentaire ? JSON n'en permet pas : utiliser une clé « _… »"
    if prec == "," and c.isdigit() and avant[-2:-1].isdigit():
        return "virgule décimale ? écrire 1.5 et non 1,5"
    mot = re.match(r"[A-Za-z_]\w*", texte[pos:])
    if mot and mot.group() in ("True", "False", "None", "TRUE", "FALSE", "NULL", "Null"):
        return "true, false et null s'écrivent en minuscules en JSON"
    if mot and e.msg.startswith("Expecting property name"):
        return "nom de clé sans guillemets doubles ?"
    if e.msg == "Expecting ',' delimiter":
        return "virgule manquante ?"
    return ""


def _court(texte, n=60):
    """Texte recopié d'un fichier dans un message, abrégé au-delà de ``n`` caractères."""
    texte = str(texte)
    return texte if len(texte) <= n else texte[:n - 1] + "…"


def _joindre(chemin, cle):
    """Chemin d'un champ : « a.b[3].c » (« [3] » pour un élément de liste ; clé longue abrégée)."""
    if isinstance(cle, int):
        return f"{chemin}[{cle}]"
    cle = _court(cle)
    return f"{chemin}.{cle}" if chemin else cle


_HORS_BORNES = float("inf")
_BORNE_ENTIERS = 10 ** 308
_SUBSTITUT_ISOLE = re.compile("[%s-%s]" % (chr(0xD800), chr(0xDFFF)))
_TROP_PROFOND = f"imbrication trop profonde (plus de {PROFONDEUR_MAX} niveaux de listes et d'objets)"
_SUBSTITUT = "demi-caractère de substitution isolé (\\ud800 à \\udfff sans sa paire)"


def _lire_entier(texte):
    """Entier JSON ; au-delà de BORNE_NOMBRES, l'infini, que _controler_document refuse avec le chemin du champ (un
    entier de plus de 310 caractères n'est pas converti : Python ≥ 3.11 refuserait au-delà de 4 300 chiffres)."""
    if len(texte) > 310:
        return _HORS_BORNES
    n = int(texte)
    return n if -_BORNE_ENTIERS <= n <= _BORNE_ENTIERS else _HORS_BORNES


def _controler_document(doc, nom, doublons):
    """Contrôles du document décodé (parcours sans récursion) : imbrication, nombres, demi-caractères de
    substitution isolés, clés en double (``doublons`` : identifiant d'un objet → première clé en double)."""
    def scalaire(x, chemin):
        t = type(x)
        if t is float and not -BORNE_NOMBRES <= x <= BORNE_NOMBRES:
            raise ErreurFormat(f"{nom} : {chemin} : nombre hors des bornes (valeur absolue supérieure à 1e308)")
        if t is str and _SUBSTITUT_ISOLE.search(x):
            raise ErreurFormat(f"{nom} : {chemin} : texte contenant un {_SUBSTITUT}")

    if not isinstance(doc, (dict, list)):
        scalaire(doc, "racine")
        return
    pile = [(doc, "", 1)]
    while pile:
        valeur, chemin, niveau = pile.pop()
        if niveau > PROFONDEUR_MAX:
            raise ErreurFormat(f"{nom} : {_TROP_PROFOND}")
        if isinstance(valeur, dict):
            if id(valeur) in doublons:
                raise ErreurFormat(f"{nom} : {_joindre(chemin, doublons[id(valeur)])} : clé en double")
            elements = valeur.items()
        else:
            elements = enumerate(valeur)
        for cle, x in elements:
            if type(cle) is str and _SUBSTITUT_ISOLE.search(cle):
                raise ErreurFormat(f"{nom} : {chemin or 'racine'} : clé contenant un {_SUBSTITUT}")
            if isinstance(x, (dict, list)):
                pile.append((x, _joindre(chemin, cle), niveau + 1))
            else:
                scalaire(x, _joindre(chemin, cle))


def _decoder_json(texte, nom):
    """Document JSON de ``texte`` (fichier ``nom``) ; règles de lecture : voir la docstring du module."""
    doublons = {}

    def objet(paires):
        d = dict(paires)
        if len(d) != len(paires):
            vues = set()
            for cle, _ in paires:
                if cle in vues and not cle.startswith("_"):
                    doublons[id(d)] = cle
                    break
                vues.add(cle)
        return d

    try:
        doc = json.loads(texte, parse_constant=_refuser_constante, parse_int=_lire_entier, object_pairs_hook=objet)
    except json.JSONDecodeError as e:
        cause = _cause_probable(texte, e)
        raise ErreurFormat(f"{nom} : JSON invalide, ligne {e.lineno}, colonne {e.colno} : {_message_json(e.msg)}"
                           + (f" ({cause})" if cause else "")) from None
    except RecursionError:
        raise ErreurFormat(f"{nom} : {_TROP_PROFOND}") from None
    except ValueError as e:
        raise ErreurFormat(f"{nom} : {e}") from None
    _controler_document(doc, nom, doublons)
    return doc


def _lire_json(chemin):
    return _decoder_json(_lire_texte(chemin), _nom_fichier(chemin))


def _lire_json_main(chemin):
    """(document, avertissements) d'un fichier écrit à la main : UTF-8 (BOM accepté), repli cp1252 signalé."""
    nom = _nom_fichier(chemin)
    texte, avertissements = _decoder_texte(_lire_octets(chemin), nom, repli=True)
    return _decoder_json(texte, nom), avertissements


_VERSION_FORMAT = re.compile("[0-9]{1,9}")


def _decouper_format(texte):
    """(nom, version) de « nom/N » : version entière ; None sans « /N » ; -1 (jamais prise en charge) si N n'est pas
    un entier de 1 à 9 chiffres ASCII (« verdict/² » est refusé, jamais une erreur interne)."""
    nom, sep, version = str(texte).partition("/")
    if not sep:
        return nom, None
    return nom, (int(version) if _VERSION_FORMAT.fullmatch(version) else -1)


def _format_du_document(d):
    """Nom du format d'un document, formes v1 comprises ; None si indéterminé."""
    if isinstance(d, list):
        return "placements"
    if isinstance(d, dict):
        f = d.get("format")
        if isinstance(f, str):
            return _decouper_format(f)[0]
        if "format" in d:
            return None
        if "pistes" in d and "vias" in d:
            return "routage"
        cles = [c for c in d if not c.startswith("_")]
        if isinstance(d.get("convertisseurs"), list) and set(cles) <= {"convertisseurs", "sans_piste_dessous"}:
            return "config_utilisateur"
        connues = set(CLES_CONTRAINTES) | set(CLES_CONTRAINTES_V1)
        n_contraintes = sum(1 for c in cles if c in connues)
        n_parametres = sum(1 for c in cles if c in CLES_PARAMETRES_MOTEUR)
        if n_parametres > n_contraintes and 2 * n_parametres > len(cles):
            return "parametres_moteur"  # « format » facultatif dans un fichier de paramètres (§2.6)
        if cles and 2 * n_contraintes > len(cles):
            return "contraintes"
    return None


ECRITS_A_LA_MAIN = ("contraintes", "parametres_moteur", "config_utilisateur")
"""Formats des fichiers écrits à la main (§0.5) : lecture stricte, repli cp1252 signalé."""


def charger(chemin, format_attendu):
    """Lit et valide un fichier du format attendu (« donnees_placeur » ou « donnees_placeur/2 »).

    Rend le document (un dict ; une liste pour un placement de forme v1). ErreurFormat si le fichier est illisible,
    d'un autre format, d'une version non prise en charge ou non conforme (messages avec le nom du fichier seul).
    """
    nom_fichier = _nom_fichier(chemin)
    nom, version = _decouper_format(format_attendu)
    if nom not in FORMATS:
        raise ErreurFormat(f"{nom_fichier} : format attendu « {_court(format_attendu)} » inconnu")
    if version is not None and version != FORMATS[nom]:
        raise ErreurFormat(f"{nom_fichier} : format {_court(format_attendu)} non pris en charge")
    d = _lire_json(chemin)
    erreurs = VALIDATEURS[nom](d)
    if erreurs:
        raise ErreurFormat([f"{nom_fichier} : {e}" for e in erreurs])
    return d


# ---------------------------------------------------------------------------------------------------------------------
# Contrôles communs
# ---------------------------------------------------------------------------------------------------------------------

def _erreurs_format(d, nom):
    """Erreurs du champ « format » d'un objet pour le format ``nom`` ; [] s'il convient."""
    attendu = f"{nom}/{FORMATS[nom]}"
    f = d.get("format")
    if f is None:
        return ["format : champ manquant"]
    if not isinstance(f, str):
        return ["format : texte attendu"]
    if f == attendu:
        return []
    if _decouper_format(f)[0] == nom:
        return [f"format {_court(f)} non pris en charge"]
    return [f"format : « {_court(f)} » au lieu de « {attendu} »"]


def _plus_proche(cle, connues):
    proches = difflib.get_close_matches(cle, list(connues), n=1, cutoff=0.6)
    return proches[0] if proches else None



# ---------------------------------------------------------------------------------------------------------------------
# Langage de schéma déclaratif (formats écrits par un programme, §2.1)
#
# Un schéma est un arbre de nœuds (Txt, Ent, Nb, Bool, Enum, Motif, Nul, Liste, Tuple, Dico, Obj, Ref, Ou, Tout) ;
# Obj déclare ses champs, tous obligatoires sauf Fac(…), ses champs supprimés (refusés), ses groupes de champs
# exclusifs (un et un seul présent) et ses variantes selon la valeur d'un champ. Le parcours donne à chaque erreur
# le chemin du champ (« convertisseurs[0].cond_bulk[0] »). Les champs inconnus sont ignorés (§0.5), comme les clés
# « _* ». Ref contrôle une référence croisée interne (§2.1) quand le fichier contient la liste de référence.
# ---------------------------------------------------------------------------------------------------------------------

MAX_ERREURS = 200
"""Nombre maximal d'erreurs rendues par un validateur ; les suivantes sont comptées sur une dernière ligne."""

_NATURES = {
    "piece": ("pièce", "inconnue"),
    "reseau": ("réseau", "inconnu"),
    "couche": ("couche", "inconnue"),
    "pastille": ("pastille", "inconnue"),
    "decoupe": ("découpe", "inconnue"),
    "classe": ("classe d'isolement", "inconnue"),
    "paire": ("paire", "inconnue"),
    "objet": ("objet (pièce ou pastille)", "inconnu"),
}
"""Nature d'une référence croisée → (libellé, accord de « inconnu »)."""


class _Contexte:
    """Erreurs d'une validation et ensembles des noms que citent les références (nature → ensemble). Une nature
    absente n'est pas contrôlée (fichier sans liste de référence, ou liste de référence elle-même mal formée)."""

    def __init__(self, ensembles=None, main=False):
        self.erreurs = []
        self.ensembles = ensembles or {}
        self.main = main  # fichier écrit à la main : indice « virgule décimale ? » sur les longueurs de listes

    def erreur(self, chemin, cause):
        self.erreurs.append(f"{chemin or 'racine'} : {cause}")

    def longueur(self, chemin, n, trouve):
        indice = " (virgule décimale ?)" if self.main else ""
        self.erreur(chemin, f"liste de {n} éléments attendue, {trouve} trouvé(s){indice}")


def _trouve(v):
    """Description courte de la valeur trouvée à la place de celle attendue."""
    if v is None:
        return "null trouvé"
    if type(v) is bool:
        return "booléen trouvé"
    if type(v) in (int, float):
        return f"{v!r} trouvé"
    if type(v) is str:
        return f"texte « {_court(v)} » trouvé"
    return "liste trouvée" if type(v) is list else "objet trouvé"


class _Schema:
    attendu = "valeur attendue"
    nombre = False

    def verifier(self, v, chemin, ctx):
        """Contrôle ``v`` (champ ``chemin``), erreurs dans ``ctx`` ; rend True si le type de ``v`` convient."""
        raise NotImplementedError

    def _faux(self, v, chemin, ctx):
        cause = f"{self.attendu}, {_trouve(v)}"
        if self.nombre and type(v) is str:
            cause += " (nombre écrit entre guillemets ?)"
        ctx.erreur(chemin, cause)
        return False


class Tout(_Schema):
    """Valeur quelconque (contenu non spécifié par le contrat)."""

    def verifier(self, v, chemin, ctx):
        return True


class Txt(_Schema):
    attendu = "texte attendu"

    def verifier(self, v, chemin, ctx):
        return True if type(v) is str else self._faux(v, chemin, ctx)


class Bool(_Schema):
    attendu = "booléen attendu"

    def verifier(self, v, chemin, ctx):
        return True if type(v) is bool else self._faux(v, chemin, ctx)


class Nb(_Schema):
    """Nombre JSON (entier ou flottant, jamais un booléen), borné si ``mini`` / ``maxi``."""
    attendu = "nombre attendu"
    nombre = True

    def __init__(self, mini=None, maxi=None):
        self.mini, self.maxi = mini, maxi

    def _type(self, v):
        return type(v) in (int, float)

    def verifier(self, v, chemin, ctx):
        if not self._type(v):
            return self._faux(v, chemin, ctx)
        if not -_BORNE_ENTIERS <= v <= _BORNE_ENTIERS:  # infini, NaN ou entier géant d'un dict construit en mémoire
            ctx.erreur(chemin, "nombre hors des bornes")
            return False
        if (self.mini is not None and v < self.mini) or (self.maxi is not None and v > self.maxi):
            bornes = " et ".join(b for b in (f"≥ {self.mini}" if self.mini is not None else "",
                                             f"≤ {self.maxi}" if self.maxi is not None else "") if b)
            ctx.erreur(chemin, f"valeur {bornes} attendue, {_trouve(v)}")
        return True


class Ent(Nb):
    """Entier JSON écrit sans partie décimale (comme serde : 4.0 n'est pas un entier)."""
    attendu = "entier attendu"

    def _type(self, v):
        return type(v) is int


class Enum(_Schema):
    def __init__(self, *valeurs):
        self.valeurs = valeurs

    def verifier(self, v, chemin, ctx):
        if any(type(v) is type(x) and v == x for x in self.valeurs):
            return True
        permis = ", ".join(f"« {x} »" for x in self.valeurs)
        ctx.erreur(chemin, f"valeur non permise ({_trouve(v)} ; attendu : {permis})")
        return False


class Motif(Txt):
    """Texte qui suit une expression régulière (``description`` : forme attendue, pour le message)."""

    def __init__(self, expression, description):
        self.expression, self.description = re.compile(expression), description

    def verifier(self, v, chemin, ctx):
        if not super().verifier(v, chemin, ctx):
            return False
        if not self.expression.fullmatch(v):
            ctx.erreur(chemin, f"{self.description} attendu, {_trouve(v)}")
        return True


class Nul(_Schema):
    """``null`` permis (seulement là où la table du contrat le dit, §2.1)."""

    def __init__(self, schema):
        self.schema = schema

    def verifier(self, v, chemin, ctx):
        return True if v is None else self.schema.verifier(v, chemin, ctx)


class Fac:
    """Marque un champ facultatif d'un Obj : absent permis ; présent, contrôlé par ``schema``."""

    def __init__(self, schema):
        self.schema = schema


class Liste(_Schema):
    attendu = "liste attendue"

    def __init__(self, schema, n=None, mini=0, maxi=None):
        self.schema, self.n, self.mini, self.maxi = schema, n, mini, maxi

    def verifier(self, v, chemin, ctx):
        if type(v) is not list:
            return self._faux(v, chemin, ctx)
        if self.n is not None and len(v) != self.n:
            ctx.longueur(chemin, self.n, len(v))
        elif len(v) < self.mini:
            ctx.erreur(chemin, f"liste d'au moins {self.mini} élément(s) attendue, {len(v)} trouvé(s)")
        elif self.maxi is not None and len(v) > self.maxi:
            ctx.erreur(chemin, f"liste d'au plus {self.maxi} éléments attendue, {len(v)} trouvés")
        for i, x in enumerate(v):
            self.schema.verifier(x, _joindre(chemin, i), ctx)
        return True


class Tuple(_Schema):
    """Liste de longueur fixe dont chaque élément a son propre schéma (pose ``[x, y, rot, dessous]``)."""
    attendu = "liste attendue"

    def __init__(self, *schemas):
        self.schemas = schemas

    def verifier(self, v, chemin, ctx):
        if type(v) is not list:
            return self._faux(v, chemin, ctx)
        if len(v) != len(self.schemas):
            ctx.longueur(chemin, len(self.schemas), len(v))
            return True
        for i, (s, x) in enumerate(zip(self.schemas, v)):
            s.verifier(x, _joindre(chemin, i), ctx)
        return True


class Dico(_Schema):
    """Objet à clés libres (``{réseau: …}``) ; ``cle`` : schéma des clés (référence croisée, énumération)."""
    attendu = "objet attendu"

    def __init__(self, valeur, cle=None):
        self.valeur, self.cle = valeur, cle

    def verifier(self, v, chemin, ctx):
        if type(v) is not dict:
            return self._faux(v, chemin, ctx)
        for k, x in v.items():
            if k.startswith("_"):
                continue
            c = _joindre(chemin, k)
            if self.cle is not None:
                self.cle.verifier(k, c, ctx)
            self.valeur.verifier(x, c, ctx)
        return True


class Obj(_Schema):
    """Objet à champs déclarés. ``champs`` : nom → schéma (ou Fac(schéma)), obligatoires sauf Fac ;
    ``supprimes`` : champs refusés (supprimés par le contrat) ; ``exclusifs`` : groupes dont un et un seul champ est
    présent ; ``selon`` : (champ, {valeur: champs ajoutés}) pour les variantes (géométrie selon le type…) ;
    ``strict`` (fichiers écrits à la main, §0.5) : clé inconnue refusée avec la clé la plus proche, au style des
    contraintes (« 'fixe' inconnue ; vouliez-vous dire 'fixes' ? ») ou des paramètres du moteur
    (« routage.espacment : clé inconnue ; vouliez-vous dire espacement ? »)."""
    attendu = "objet attendu"

    def __init__(self, champs, supprimes=(), exclusifs=(), selon=None, strict=None):
        self.champs, self.supprimes, self.exclusifs, self.selon = champs, supprimes, exclusifs, selon
        self.strict = strict

    def _inconnues(self, v, champs, chemin, ctx):
        connues = list(champs)
        for cle in v:
            if cle.startswith("_") or cle in connues or cle in self.supprimes:
                continue
            proche = _plus_proche(cle, connues)
            if self.strict == "parametres":
                ctx.erreur(_joindre(chemin, cle), "clé inconnue" + (f" ; vouliez-vous dire {proche} ?" if proche
                                                                    else ""))
            else:
                texte = f"'{_court(cle)}' inconnue" + (f" ; vouliez-vous dire '{proche}' ?" if proche else "")
                ctx.erreurs.append(f"{chemin} : {texte}" if chemin else texte)

    def verifier(self, v, chemin, ctx):
        if type(v) is not dict:
            return self._faux(v, chemin, ctx)
        champs = self.champs
        if self.selon is not None:
            cle, variantes = self.selon
            valeur = v.get(cle)
            if type(valeur) is str and valeur in variantes:
                champs = dict(champs, **variantes[valeur])
        if self.strict:
            self._inconnues(v, champs, chemin, ctx)
        exclusifs = {nom for groupe in self.exclusifs for nom in groupe}
        for nom, s in champs.items():
            facultatif = isinstance(s, Fac) or nom in exclusifs
            s = s.schema if isinstance(s, Fac) else s
            if nom in v:
                s.verifier(v[nom], _joindre(chemin, nom), ctx)
            elif not facultatif:
                ctx.erreur(_joindre(chemin, nom), "champ manquant")
        for groupe in self.exclusifs:
            n = sum(1 for nom in groupe if nom in v)
            if n != 1:
                noms = " ou ".join(f"« {nom} »" for nom in groupe)
                ctx.erreur(chemin, f"un et un seul champ parmi {noms} attendu, {n} présent(s)")
        for nom in self.supprimes:
            if nom in v:
                ctx.erreur(_joindre(chemin, nom), "champ supprimé par le contrat")
        return True


class Ref(_Schema):
    """Référence croisée interne (§2.1) : valeur de type ``base`` qui doit exister dans l'ensemble ``nature`` du
    contexte (s'il est connu) ; ``vide`` : "" permis (absence) ; ``toutes`` : "toutes" permis (couches)."""

    def __init__(self, nature, base=None, vide=False, toutes=False):
        self.nature, self.base, self.vide, self.toutes = nature, base or Txt(), vide, toutes

    def verifier(self, v, chemin, ctx):
        if not self.base.verifier(v, chemin, ctx):
            return False
        if (self.vide and v == "") or (self.toutes and v == "toutes"):
            return True
        ensemble = ctx.ensembles.get(self.nature)
        if ensemble is not None and v not in ensemble:
            libelle, accord = _NATURES[self.nature]
            ctx.erreur(chemin, f"{libelle} {_court(v)} {accord}")
        return True


class Ou(_Schema):
    """Une des formes ``schemas`` (``description`` : formes attendues, pour le message)."""

    def __init__(self, description, *schemas):
        self.description, self.schemas = description, schemas

    def verifier(self, v, chemin, ctx):
        for s in self.schemas:
            essai = _Contexte(ctx.ensembles, ctx.main)
            if s.verifier(v, chemin, essai) and not essai.erreurs:
                return True
        indice = " (nombre écrit entre guillemets ?)" if type(v) is str and any(
            getattr(s, "nombre", False) for s in self.schemas) else ""
        ctx.erreur(chemin, f"{self.description} attendu, {_trouve(v)}{indice}")
        return False


class ParType(_Schema):
    """Schéma choisi selon le type JSON de la valeur (``{list: s1, dict: s2}``), pour garder les messages précis de
    chaque forme (« [x, y] ou {x, y, rotation, face} ») ; ``description`` : formes attendues."""

    def __init__(self, description, formes):
        self.description, self.formes = description, formes

    def verifier(self, v, chemin, ctx):
        s = self.formes.get(type(v))
        if s is None:
            ctx.erreur(chemin, f"{self.description} attendu, {_trouve(v)}")
            return False
        return s.verifier(v, chemin, ctx)


class RectOuPolygone(_Schema):
    """Rectangle ``[x0, y0, x1, y1]`` ou polygone ``[[x, y], …]`` (au moins 3 points) d'un fichier écrit à la main :
    la forme se lit sur le premier élément (une liste : polygone)."""
    attendu = "rectangle [x0, y0, x1, y1] ou polygone [[x, y], …] attendu"

    def verifier(self, v, chemin, ctx):
        if type(v) is not list:
            return self._faux(v, chemin, ctx)
        if v and type(v[0]) is list:
            return Liste(Liste(Nb(), n=2), mini=3).verifier(v, chemin, ctx)
        return Liste(Nb(), n=4).verifier(v, chemin, ctx)


class Regex(Txt):
    """Expression régulière (``motif_canaux``) : texte que ``re`` compile."""

    def verifier(self, v, chemin, ctx):
        if not super().verifier(v, chemin, ctx):
            return False
        try:
            re.compile(v)
        except re.error as e:
            ctx.erreur(chemin, f"expression régulière invalide ({e.msg})")
        return True


# --- Briques communes -------------------------------------------------------------------------------------------------

TXT, BOOL, NB, ENT, TOUT = Txt(), Bool(), Nb(), Ent(), Tout()
N = Ent(mini=0)                         # compte
NBP = Nb(mini=0)                        # dimension, durée, longueur (≥ 0)
PART = Nb(mini=0, maxi=1)               # proportion
POINT = Liste(NB, n=2)
POLY = Liste(POINT)
TROUS = Liste(POLY)
RECT = Liste(NB, n=4)
POSE = Tuple(NB, NB, NB, BOOL)
FACE = Enum("dessus", "dessous")
FACES = Liste(FACE)
PIECE, PIECE_OU_VIDE = Ref("piece"), Ref("piece", vide=True)
RESEAU, RESEAU_OU_VIDE = Ref("reseau"), Ref("reseau", vide=True)
COUCHE, COUCHE_OU_TOUTES = Ref("couche"), Ref("couche", toutes=True)
PASTILLE, DECOUPE, PAIRE = Ref("pastille"), Ref("decoupe"), Ref("paire")
CLASSE = Ref("classe", Ent(mini=0))
ROLE = Enum("signal", "alimentation", "puissance", "commutation", "plan")
LARGEURS = Tuple(NBP, NBP, Nul(NBP))
VIA_REGLE = Obj({"diametre": NBP, "trou": NBP, "diametre_min": NBP, "diametre_max": Nul(NBP), "trou_min": NBP,
                 "trou_max": Nul(NBP)})
CLASSES_ISOLEMENT = Obj({"piste": CLASSE, "via": CLASSE, "surface": CLASSE})
ISOLEMENT = Obj({"defaut": NBP, "classes": Liste(TXT, mini=1), "matrice": Liste(Liste(NBP)),
                 "classe_reference": CLASSE}, supprimes=("COMMUTATION", "intra_pieces", "pieces_pas_fin"))
FORME_PASTILLE = Enum("rect", "rond", "oblong", "rect_arrondi", "polygone")
FACE_CUIVRE = Enum("composant", "opposee", "toutes")
TROU = Obj({"diametre": NBP, "forme": Enum("rond", "carre", "oblong"), "longueur": NBP, "rotation": NB,
            "decalage": POINT, "metallise": BOOL})
PILE_FACE = Obj({"forme": FORME_PASTILLE, "largeur": NBP, "hauteur": NBP, "rotation": NB, "rayon_coin": NBP,
                 "points": POLY, "decalage": POINT})
_CHAMPS_PASTILLE = {
    "id": TXT, "nom": TXT, "reseau": RESEAU_OU_VIDE, "forme": FORME_PASTILLE, "largeur": NBP, "hauteur": NBP,
    "rotation": NB, "rayon_coin": NBP, "points": POLY, "couches": Liste(COUCHE), "face": FACE_CUIVRE,
    "trou": Nul(TROU), "traversante": BOOL, "expansion_masque": NB,
    "pile": Nul(Obj({"opposee": PILE_FACE, "interne": PILE_FACE})), "via_dans_pastille_permis": BOOL,
    "classe_isolement": CLASSE}
PASTILLE_ENTREE = Obj(dict(_CHAMPS_PASTILLE, x=NB, y=NB))
PASTILLE_LIBRE = Obj(dict(_CHAMPS_PASTILLE, x=NB, y=NB, face=Enum("dessus", "dessous", "toutes")))
PASTILLE_DONNEES = Obj(dict(_CHAMPS_PASTILLE, lx=NB, ly=NB, hw=NBP, hh=NBP, th=BOOL))
REGION = Obj({"id": TXT, "couches": Liste(COUCHE), "face": FACE_CUIVRE, "pastille": Nul(PASTILLE),
              "reseau": RESEAU_OU_VIDE, "liaison": Nul(Liste(RESEAU)), "points": POLY, "classe_isolement": CLASSE})
VIA_EMPREINTE = Obj({"id": TXT, "x": NB, "y": NB, "diametre": NBP, "trou": NBP, "reseau": RESEAU_OU_VIDE,
                     "classe_isolement": CLASSE})
ZONE_INTERDITE = Obj({"id": TXT, "couches": Liste(COUCHE_OU_TOUTES), "forme": Enum("polygone", "polyligne"),
                      "points": POLY, "largeur": Fac(NBP),
                      "effets": Liste(Enum("pistes", "vias", "remplissages", "pastilles", "placement")),
                      "source": Enum("keepout", "zone_regle")}, selon=("forme", {"polyligne": {"largeur": NBP}}))
GEOMETRIES_CUIVRE = {"piste": {"x1": NB, "y1": NB, "x2": NB, "y2": NB, "largeur": NBP},
                     "via": {"x": NB, "y": NB, "diametre": NBP, "trou": NBP},
                     "region": {"points": POLY, "trous": TROUS}}
TYPE_CUIVRE = Enum("piste", "via", "region")
CONVERTISSEUR = Obj({
    "nom": TXT, "topologie": Enum("abaisseur", "elevateur", "abaisseur_elevateur", "inverseur", "autre"),
    "commutation": Enum("synchrone", "asynchrone", "integre"), "haut": PIECE, "bas": PIECE, "pilote": PIECE_OU_VIDE,
    "inductance": PIECE, "cond_entree": Liste(PIECE), "cond_sortie": Liste(PIECE), "cond_bulk": Liste(PIECE),
    "cond_pilote": Liste(PIECE), "cond_bootstrap": Liste(PIECE), "reseau_entree": RESEAU, "reseau_sortie": RESEAU,
    "reseau_masse": RESEAU, "reseau_commutation": RESEAU, "reseaux_courts": Liste(RESEAU),
    "reseaux_sensibles": Liste(RESEAU),
    "boucle_chaude": Obj({"condensateurs": Liste(PIECE), "reseau_rail": RESEAU, "reseau_masse": RESEAU}),
    "origine": Enum("deduit", "impose", "modifie")})

GENRES_ROUTAGE = ("isolement", "garde_bord", "garde_decoupe", "garde_trou", "zone_interdite", "largeur",
                  "dimension_via", "via_sous_cms", "trou_trou", "antenne", "couche_non_permise", "reseau_inconnu")
"""Les 12 clés de ``violations_par_type`` d'un verdict (§4.4)."""

GENRES_PLACEMENT = ("absente", "inconnue", "fixe_deplacee", "orientation", "grille", "chevauchement", "obstacle",
                    "marge_bord", "marge_decoupe", "eloignement", "pastille_pastille", "zone_imposee", "au_bord",
                    "groupe_rigide")
"""Les 14 clés de ``violations_par_type`` d'un contrôle de placement (§2.8, §4.5)."""

PHASES = ("chargement", "placement_initial", "recuit", "routage_detaille", "boucle", "conjointe", "finition",
          "verification", "ecriture")
"""Clés de ``stats.execution.temps_phases_s``, toujours toutes présentes (§2.7)."""

LIVRABLE = Enum("valide", "a_terminer", "apercu")

# --- entree_carte/2 (§2.2) --------------------------------------------------------------------------------------------

ENTREE_CARTE = Obj({
    "format": TXT,
    "source": Obj({"fichier": TXT, "type": Enum("altium_pcbdoc", "kicad_pcb"), "sha256": TXT, "version_fichier": TXT,
                   "origine_fichier_mil": Nul(POINT), "origine_fichier_mm": Nul(POINT),
                   "origine_cao_mil": Nul(POINT)}),
    "unite": Enum("mm"),
    "carte": Obj({
        "contour": Liste(POINT, mini=3), "boite": RECT, "aire_reference_mm2": Nul(NBP),
        "decoupes": Liste(Obj({"id": TXT, "points": POLY, "proprietaire": Nul(PIECE)})),
        "trous": Liste(Obj({"id": TXT, "x": NB, "y": NB, "rotation": NB, "trou": TROU, "metallise": BOOL,
                            "diametre_cuivre": NBP, "couches": Liste(COUCHE)})),
        "couches": Liste(Obj({
            "nom": TXT, "nom_fichier": TXT, "rang": N, "type": Enum("signal", "plan_negatif"),
            "plans": Liste(Obj({"reseau": RESEAU_OU_VIDE, "contour": POLY, "trous": TROUS})), "retrait_mm": NB,
            "epaisseur_cuivre_mm": Nul(NBP), "pistes_libres": N,
            "type_kicad": Nul(Enum("signal", "power", "mixed", "jumper")), "routage_permis": Nul(BOOL)}), mini=1),
        "remplissages": Liste(Obj({"nom": TXT, "couche": COUCHE, "reseau": RESEAU_OU_VIDE, "contour": POLY,
                                   "trous": TROUS, "couverture": PART})),
        "zones_interdites": Liste(ZONE_INTERDITE),
        "obstacles_cuivre": Liste(Obj({"id": TXT, "type": TXT, "couches": Liste(COUCHE_OU_TOUTES), "points": POLY})),
        "cuivre_existant": Liste(Obj({"id": TXT, "type": TYPE_CUIVRE, "reseau": RESEAU_OU_VIDE,
                                      "couches": Liste(COUCHE), "verrouille": BOOL},
                                     selon=("type", GEOMETRIES_CUIVRE))),
        "couches_cuivre": N, "noms_couches_cuivre": Liste(COUCHE)}),
    "composants": Liste(Obj({
        "designateur": TXT, "designateur_source": TXT, "empreinte": TXT, "description": TXT, "reference": TXT,
        "valeur": TXT, "genre": Enum("standard", "mecanique", "graphique", "liaison_reseaux", "cavalier"),
        "classes_composants": Liste(TXT), "canal": Nul(Obj({"groupe": TXT, "instance": TXT})), "hauteur": NBP,
        "source_hauteur": Enum("hauteur", "corps3d", "inconnue"), "x": NB, "y": NB, "rotation": NB, "face": FACE,
        "verrouille": BOOL, "boite": RECT, "pastilles": Liste(PASTILLE_ENTREE), "regions": Liste(REGION),
        "vias": Liste(VIA_EMPREINTE), "zones_interdites": Liste(ZONE_INTERDITE), "decoupes": Liste(DECOUPE)})),
    "pastilles_libres": Liste(PASTILLE_LIBRE),
    "reseaux": Liste(TXT),
    "classes": Dico(Liste(RESEAU)),
    "classes_composants": Dico(Liste(PIECE)),
    "paires": Liste(Obj({"nom": TXT, "positif": RESEAU, "negatif": RESEAU})),
    "regles_fichier": Liste(Obj({"type": TXT, "nom": TXT, "priorite": ENT, "portee1": TXT, "portee2": TXT,
                                 "valeurs": Dico(TOUT)})),
    "regles": Obj({
        "isolement": ISOLEMENT,
        "reseaux": Dico(Obj({"largeurs": LARGEURS, "via": VIA_REGLE, "classes_isolement": CLASSES_ISOLEMENT,
                             "couches_permises": Nul(Liste(COUCHE)), "priorite_routage": ENT,
                             "alimentation_schema": BOOL, "regle_largeur_propre": BOOL}), cle=RESEAU),
        "largeur_defaut": LARGEURS, "via": VIA_REGLE, "trou_trou": Nul(NBP), "garde_bord": Nul(NBP),
        "isolement_pieces": Nul(NBP), "hauteurs": Obj({"dessus": Nul(NBP), "dessous": Nul(NBP)}),
        "pieces": Dico(Obj({"faces_permises": Nul(FACES), "rotations_permises": Nul(Liste(NB)),
                            "hauteur_max": Nul(NBP)}), cle=PIECE),
        "connexion_plans": Nul(Obj({"isolement": NBP, "connexion": Enum("relief", "direct")})),
        "masque_serigraphie": Nul(NBP),
        "salles": Liste(Obj({"nom": TXT, "pieces": Liste(PIECE), "polygone": POLY,
                             "face": Enum("dessus", "dessous", "toutes")})),
        "tolerances_longueur": Dico(NBP, cle=PAIRE),
        "non_interpretees": Liste(Obj({"type": TXT, "nom": TXT, "portee1": TXT, "portee2": TXT, "cause": TXT})),
        "ignorees": Liste(TXT)}),
    "avertissements": Liste(TXT)})

# --- config_carte/2 (§2.4) --------------------------------------------------------------------------------------------

CONFIG_CARTE = Obj({"format": TXT, "convertisseurs": Liste(CONVERTISSEUR), "sans_piste_dessous": Liste(TXT),
                    "pieces_a_fixer_suggerees": Liste(TXT), "surcharges_appliquees": Liste(TXT),
                    "avertissements": Liste(TXT)})

# --- donnees_placeur/2 (§2.5) -----------------------------------------------------------------------------------------

DONNEES_PLACEUR = Obj({
    "format": TXT,
    "source": Obj({"entree": TXT, "sha256_entree": TXT, "contraintes": Nul(TXT), "config": TXT,
                   "controle_gabarit": Nul(TXT), "construit_par": Motif(r"construire_donnees .+",
                                                                        "« construire_donnees <version> »")}),
    "unite": Enum("mm"),
    "carte": Obj({"contour": Liste(POINT, mini=3), "boite": RECT,
                  "decoupes": Liste(Obj({"id": TXT, "points": POLY, "proprietaire": Nul(PIECE)})),
                  "remplissages": Liste(Obj({"couche": COUCHE, "reseau": RESEAU, "contour": POLY, "trous": TROUS})),
                  "marge_bord_mm": NBP, "marge_decoupe_mm": NBP, "garde_bord_mm": NBP, "garde_decoupe_mm": NBP},
                 supprimes=("fentes",)),
    "couches": Liste(Obj({"nom": TXT, "rang": N, "role": Enum("routage", "plan", "inutilisee"),
                          "zones_plan": Liste(Obj({"reseau": RESEAU, "contour": POLY, "trous": TROUS})),
                          "epaisseur_cuivre_mm": NBP, "direction_preferee": Nul(Enum("H", "V"))}), mini=1),
    "trous": Liste(Obj({"id": TXT, "x": NB, "y": NB, "rotation": NB, "trou": TROU, "metallise": BOOL,
                        "diametre_cuivre": NBP, "rayon_interdit_mm": NBP, "classe_isolement": CLASSE,
                        "piece": Nul(PIECE)})),
    "obstacles_placement": Liste(Obj({"faces": FACES, "rect": Fac(RECT), "polygone": Fac(POLY), "etiquette": TXT},
                                     exclusifs=(("rect", "polygone"),))),
    "zones_imposees": Liste(Obj({"pieces": Liste(PIECE), "faces": FACES, "polygone": POLY, "etiquette": TXT,
                                 "source": Enum("contrainte", "salle")})),
    "zones_routage_interdites": Liste(Obj({"id": TXT, "couches": Liste(COUCHE_OU_TOUTES),
                                           "forme": Enum("polygone", "polyligne"), "points": POLY, "largeur": NBP,
                                           "effets": Liste(Enum("pistes", "vias", "remplissages")),
                                           "etiquette": TXT})),
    "cuivre_fixe": Liste(Obj({"id": TXT, "type": TYPE_CUIVRE, "reseau": RESEAU_OU_VIDE, "couches": Liste(COUCHE),
                              "classe_isolement": CLASSE}, selon=("type", GEOMETRIES_CUIVRE))),
    "composants": Liste(Obj({
        "nom": TXT, "empreinte": TXT,
        "nature": Enum("condensateur", "resistance", "inductance", "ferrite", "transformateur", "transistor",
                       "diode", "circuit", "connecteur", "quartz", "interface", "mecanique", "graphique", "liaison",
                       "autre"),
        "genre": Enum("standard", "mecanique", "graphique", "liaison_reseaux", "cavalier"), "valeur": TXT,
        "hauteur": NBP, "statut": Enum("mobile", "fixe"), "raison_statut": TXT, "virtuel": BOOL, "lbb": RECT,
        "pastilles": Liste(PASTILLE_DONNEES), "regions": Liste(REGION), "vias": Liste(VIA_EMPREINTE),
        "zones_interdites": Liste(ZONE_INTERDITE), "decoupes": Liste(DECOUPE), "pose_actuelle": POSE,
        "pose_cible": POSE, "orientations": Liste(Tuple(NB, BOOL)), "regle": TXT, "pas_fin": BOOL, "matrice": BOOL,
        "pas_min_mm": Nul(NBP)}, supprimes=("pose_v3", "via_sous_pastilles_permis"))),
    "reseaux": Liste(Obj({
        "nom": TXT, "classe": TXT, "role": ROLE, "masse": BOOL,
        "sensibilite": Enum("normale", "sensible", "peu_sensible"), "largeurs_mm": LARGEURS, "descente_large": BOOL,
        "isolement_mm": NBP, "classes_isolement": CLASSES_ISOLEMENT, "via": VIA_REGLE, "poids": NBP,
        "priorite": Nul(ENT), "priorite_routage": ENT, "couche_preferee": Nul(COUCHE),
        "couches_permises": Liste(COUCHE), "plan_couches": Liste(COUCHE), "remplissages": Liste(COUCHE),
        "alimentation_schema": BOOL, "paire": Nul(PAIRE)})),
    "regles": Obj({"isolement": ISOLEMENT, "via": VIA_REGLE, "trou_trou_mm": NBP, "garde_trou_mm": NBP,
                   "placement": Obj({"grille_mm": NB, "marge_piece_mm": NBP, "marge_traversante_mm": NBP}),
                   "via_dans_pastille_meme_reseau": BOOL,
                   "connexion_plans": Obj({"isolement": NBP, "connexion": Enum("relief", "direct")})},
                  supprimes=("rayon_trou_m3", "largeurs", "via_sous_cms_permis")),
    "convertisseurs": Liste(CONVERTISSEUR),
    "sans_piste_dessous": Liste(PIECE),
    "decouplages": Liste(Obj({"comp": PIECE, "pastille": PASTILLE, "reseau": RESEAU, "broches_ci": Liste(PASTILLE)})),
    "eloignements": Liste(Obj({"type": Enum("dure", "souple"), "objets": Liste(Ref("objet"), mini=1),
                               "reference": Liste(PIECE, mini=1), "distance_mm": NBP, "poids_par_mm": NBP,
                               "marge_mm": NBP})),
    "charges": Liste(Obj({"broche": TXT, "pastilles": Liste(PASTILLE, mini=1), "courant_a": NB})),
    "groupes": Liste(Obj({"type": Enum("fonctionnel", "impose", "repete"), "nom": TXT, "rigide": BOOL},
                         selon=("type", {
                             "fonctionnel": {"pieces": Liste(PIECE)},
                             "impose": {"pieces": Liste(PIECE)},
                             "repete": {"famille": TXT, "source": Enum("hierarchie", "motif"),
                                        "exemplaires": Liste(Obj({"nom": TXT, "pieces": Liste(PIECE)})),
                                        "correspondance_pieces": Liste(Liste(PIECE)),
                                        "correspondance_reseaux": Liste(Liste(RESEAU))}}))),
    "paires": Liste(Obj({"nom": TXT, "positif": RESEAU, "negatif": RESEAU, "ecart_mm": Nul(NBP),
                         "largeur_mm": Nul(NBP), "tolerance_longueur_mm": Nul(NBP)})),
    "au_bord": Liste(Obj({"piece": PIECE, "cote": Enum("gauche", "droite", "haut", "bas", "quelconque"),
                          "debord_mm": NBP})),
    "masses": Liste(RESEAU),
    "parametres_deduction": Obj({"pas_fin_mm": Fac(NBP), "regles_non_interpretees": Fac(N)}),
}, supprimes=("bloc", "trous_m3", "fentes", "soft_sw", "poids_reseaux", "reseaux_plans", "classes", "pieces_pas_fin",
              "couches_cuivre", "via_sous_cms_permis"))

# --- Sorties du moteur (§2.7, avenant A.5) ----------------------------------------------------------------------------

PLACEMENT_V1 = Obj({"designator": TXT, "side": Enum("top", "bottom"), "x": NB, "y": NB, "rotation": NB})
PLACEMENTS = Obj({"format": TXT, "unite": Enum("mil"), "origine": TXT,
                  "pieces": Liste(Obj({"designator": TXT, "side": Enum("top", "bottom"), "x": NB, "y": NB,
                                       "rotation": NB, "statut": Enum("mobile", "fixe"),
                                       "rotation_si_convention_miroir_x": Fac(NB)}))})

PISTE = Obj({"x1": NB, "y1": NB, "x2": NB, "y2": NB, "width": NBP, "layer": COUCHE, "net_name": TXT})
SURFACE = Obj({"net": TXT, "layer": TXT, "contour": POLY, "trous": TROUS,
               "type": Enum("zone_puissance", "remplissage"), "origine": Enum("moteur", "reference")})
ROUTAGE = Obj({
    "format": TXT, "unite": Enum("mil"), "origine": TXT, "couches_routage": Liste(TXT, mini=1),
    "pistes": Liste(PISTE),
    "vias": Liste(Obj({"x": NB, "y": NB, "size": NBP, "hole_size": NBP, "net": TXT, "de": TXT, "a": TXT})),
    "surfaces": Liste(SURFACE), "amorces_pas_fin": Liste(TXT), "derogations": Liste(TXT),
    "reseaux": Liste(Obj({"net": TXT, "role": ROLE, "longueur_mm": NBP, "resistance_mohm": NBP,
                          "longueur_retrecie_mm": NBP, "longueur_sous_mini_regle_mm": NBP, "vias": N,
                          "vias_doubles": N})),
    "echecs": Liste(Obj({"net": TXT, "role": ROLE, "connexions_echouees": N, "connexions_reussies": N,
                         "diagnostic": Liste(TXT)}))},
    supprimes=("note", "raccords_bloc", "segments_touchant_emprise", "stats"))
CLES_ROUTAGE_V2_SEULEMENT = ("couches_routage",)
"""Clés de routage/2 absentes des sorties v1 de 9b5b5a3 (qui portent déjà origine, reseaux, echecs, note, stats…) :
un routage sans « format » qui en porte une est une forme v2 privée de son format, refusée."""
ROUTAGE_V1 = Obj({"pistes": Liste(PISTE),
                  "vias": Liste(Obj({"x": NB, "y": NB, "size": NBP, "hole_size": NBP, "net": TXT})),
                  "unite": Fac(Enum("mil")), "surfaces": Fac(Liste(SURFACE))})

_CHAMPS_CONTROLE_PLACEMENT = {
    "valide": BOOL,
    "violations": Liste(Obj({"genre": Enum(*GENRES_PLACEMENT), "pieces": Liste(TXT), "texte": TXT})),
    "violations_par_type": Obj({g: N for g in GENRES_PLACEMENT}),
    "infos": Obj({"eloignements": Liste(Obj({"objet": TXT, "reference": TXT, "distance_mm": NB,
                                             "minimum_mm": NB}))})}
CONTROLE_PLACEMENT = Obj(dict({"format": TXT}, **_CHAMPS_CONTROLE_PLACEMENT))


def _schema_verdict(dans_stats):
    """verdict/2 (§2.8). Dans ``stats.validite`` (§2.7) : « format » facultatif et ``placement`` obligatoire."""
    placement = Obj(dict({"format": Fac(Enum("controle_placement/1"))}, **_CHAMPS_CONTROLE_PLACEMENT))
    return Obj({
        "format": Fac(Enum("verdict/2")) if dans_stats else TXT,
        "outil": TXT, "mode": Enum("strict", "altium"), "valide": BOOL, "liaisons_ouvertes": N,
        "detail_liaisons_ouvertes": Liste(Obj({"reseau": TXT, "nature": Enum("pastilles", "ilot"),
                                               "pastilles": Liste(TXT), "noms": Liste(TXT),
                                               "position_mm": POINT})),
        "violations": N, "violations_par_type": Obj({g: N for g in GENRES_ROUTAGE}),
        "detail_violations": Liste(Obj({"genre": Enum(*GENRES_ROUTAGE), "reseaux": Liste(TXT),
                                        "objets": Liste(TXT), "position_mm": POINT, "ecart_mm": Nul(NB),
                                        "requis_mm": Nul(NB)}), maxi=1000),
        "detail_violations_tronque": BOOL,
        "exemples": Dico(Liste(TXT, maxi=15), cle=Enum(*GENRES_ROUTAGE)),
        "derogations": Obj({"via_dans_pastille_meme_reseau": N}),
        "avertissements": Obj({"pistes_sous_pieces_sans_piste_dessous": N, "angles_aigus": N,
                               "paires_desequilibrees": N, "regles_non_couvertes": N,
                               "zones_plan_isolees": Fac(N)}),
        "reseaux_a_router": N, "reseaux_complets": N, "marge_isolement_min_mm": Nul(NB),
        "marge_isolement_min_paire": TXT,
        "mesures": Obj({"ecart_moyen_mm": NB, "ecart_min_mm": Nul(NB), "coudes": N, "coudes_90": N,
                        "angles_aigus": N, "longueur_pistes_mm": NBP, "resistance_puissance_mohm": NBP, "vias": N}),
        "placement": placement if dans_stats else Fac(placement)})


VERDICT = _schema_verdict(False)

def _schema_parametres(complet):
    """parametres_moteur/1 (§2.6, annexe C modifiée par l'avenant 2.2, partie D) : clés, imbrication et types ;
    ``null`` seulement pour les clés où l'annexe C le montre. ``complet`` (paramètres effectifs écrits par un
    programme : ``stats``, ``rapport``) : toutes les clés obligatoires sauf ``campagne.threads``, clés inconnues
    ignorées (§0.5) ; sinon (fichier de l'utilisateur, partiel) : toutes facultatives, clé inconnue refusée avec la
    clé la plus proche. Les valeurs par défaut ne sont pas ici : elles viennent du moteur (« parametres-defaut ») et
    de corpus/schemas/parametres_moteur.defaut.json, extrait du contrat (corpus/schemas/extraire_parametres.py)."""
    def S(champs, facultatifs=()):
        return Obj({k: (Fac(s) if not complet or k in facultatifs else s) for k, s in champs.items()},
                   strict=None if complet else "parametres")

    auto_ou_mm = Ou("« auto » ou nombre ≥ 0", Enum("auto"), NBP)
    couches = Nul(Liste(Obj({"si": Enum("preferee_puissance", "adjacente_plan", "face_convertisseur", "externe",
                                         "interne"), "facteur": NBP}, strict=None if complet else "parametres")))
    commun_role = {"facteur_via": NBP, "vias_doubles": BOOL, "couches": couches, "resistance_conjointe": BOOL,
                   "sorties_longues": BOOL}
    role = S(dict({"priorite": N, "largeur_preferee_mm": Nul(NBP), "largeur_preferee_pastille": BOOL,
                   "paliers_mm": Liste(NBP)}, **commun_role))
    role_plan = S(dict({"priorite": N, "descentes_mm": Liste(NBP), "descente_puissance_mm": Liste(NBP),
                        "rayon_vias_doubles_mm": NBP}, **commun_role))
    sensibilite = S({"couches": couches, "proximite": NBP, "priorite_signal": Nul(N)})
    return S({
        "format": TXT,
        "campagne": S({"threads": N, "chaines": Ent(mini=1), "duree_max_s": NBP,
                       "arret_apres": Enum("recuit", "routage", "complet"), "seuil_abandon_echecs": N,
                       "seuil_abandon_part": PART, "memoire_max_mo": NBP}, facultatifs=("threads",)),
        "placement": S({"degagement_pas_fin_mm": NBP, "copier_motifs": BOOL}),
        "recuit": S({"mouvements_max": N, "alpha": PART, "palier_par_piece": N, "acceptation_initiale": PART,
                     "t_fin_relative": NBP, "rudy_capacite": NBP, "rudy_lambda": NBP, "rechauffes": N,
                     "arret_paliers_sans_progres": N, "periode_reroutage": N, "pas_grille_globale_mm": NBP,
                     "affinage": S({"fraction_mouvements": PART, "acceptation": PART, "t_fin_relative": NBP,
                                    "alpha": PART, "palier_par_piece": N})}),
        "boucle": S({"tours": N, "mouvements_locaux": N, "rayon_zone_mm": NBP, "poids_initial": NBP,
                     "rayon_pieces_mm": NBP, "t0_facteur": NBP, "sigma_mm": NBP, "reroutage_local": BOOL}),
        "routage": S({"iters_negociation": N, "tours_arrachage": N, "arret_si_aggravation": BOOL,
                      "ordre_pas_fin_d_abord": BOOL, "pas_grille_detail_mm": auto_ou_mm, "largeur_plancher_mm": NBP,
                      "eventail_auto": BOOL, "eventail_pas_sortie_mm": auto_ou_mm, "eventail_longueur_mm": NBP,
                      "amorce_eventail_paires": BOOL, "amorce_moyenne": BOOL, "ponts_plans": BOOL,
                      "rayon_via_plan_mm": NBP, "fenetre_mm": NBP,
                      "espacement": S({"actif": BOOL, "prox": NBP, "coude": NBP, "allongement_max": NBP}),
                      "sens_par_couche": S({"actif": BOOL, "penalite": NBP}), "copier_motifs": BOOL,
                      "global": S({"cout_via": NBP, "penalite_via": NBP, "penalite_debordement": NBP,
                                   "penalite_echec": NBP, "max_expansions": N, "rayon_echappement_cellules": N})}),
        "finition": S({"reparer_tours": N, "reparer_si_echecs_max": Nul(N), "routage_complet": BOOL,
                       "routage_complet_iters": N, "routage_complet_tours": N, "verification_reparer_tours": N,
                       "lissage_marches": BOOL,
                       "surfaces_puissance": S({"actif": BOOL, "roles": Liste(ROLE), "largeur_min_mm": NBP})}),
        "conjointe": S({"essais": Nul(N), "essais_par_piece_mobile": NBP, "plafond_s": NBP, "t0": NBP, "t_fin": NBP,
                        "sigma_mm": NBP, "poids_proximite": NBP, "periode_zone": N, "max_expansions": N}),
        "couts": S({"conjointe": S({"longueur": NBP, "via": NBP, "retreci": NBP, "resistance_carre": NBP,
                                    "segment": NBP, "echec": NBP}),
                    "convertisseur": S({"boucle_mm2": NBP, "perimetre": NBP, "noeud": NBP, "courts": NBP,
                                        "decouplage_pilote": NBP, "bulk": NBP, "sensible": NBP,
                                        "distance_sensible_mm": NBP}),
                    "decouplage": S({"limite_mm": NBP, "poids": NBP})}),
        "roles": S({"signal": role, "alimentation": role, "puissance": role, "commutation": role,
                    "plan": role_plan}),
        "sensibilites": S({"normale": sensibilite, "sensible": sensibilite, "peu_sensible": sensibilite}),
        "couche_preferee": S({"penalite_ailleurs": NBP}),
        "journal": S({"niveau": TXT, "debug": Liste(Enum("marge", "amorces", "conjointe"))}),
    }, facultatifs=("format",))


PARAMETRES_COMPLETS = _schema_parametres(True)
"""parametres_moteur complets écrits par un programme (``stats.parametres_effectifs``, ``rapport.parametres``)."""

PARAMETRES_PARTIELS = _schema_parametres(False)
"""parametres_moteur/1 de l'utilisateur (``--parametres-moteur``), partiel ou complet, lecture stricte."""

STATS = Obj({
    "format": TXT, "version_moteur": TXT, "graine": N,
    "fin": Enum("normale", "arret_apres", "abandon", "plafond_temps", "arret_demande", "erreur_interne"),
    "valide": BOOL,
    "cause": Enum("", "placement impossible", "routage detaille incomplet", "liaisons ouvertes", "violations",
                  "placement invalide", "non route", "non verifie"),
    "piece": Nul(TXT), "raison": Nul(TXT), "validite": Nul(_schema_verdict(True)), "cout_commun": Nul(NB),
    "connexions_echouees_routeur": N, "echecs": Liste(TXT),
    "recuit": Obj({"cout_depart": NB, "cout": NB, "t0": NBP, "acceptation_initiale": PART, "paliers": N,
                   "rechauffes": N, "mouvements": N, "hpwl_depart": NBP, "hpwl": NBP,
                   "exploration": Fac(Obj({"paliers": N, "mouvements": N, "hpwl": NBP, "cout": NB}))}),
    "boucle": Obj({"tours": N, "detail": Liste(Obj({"tour": N, "zones": N, "pieces": N, "ko_avant": N,
                                                    "ko_apres": N, "retenu": BOOL}))}),
    "conjointe": Obj({"essais": N, "acceptes": N, "illegaux": N, "refuses": N, "cout_depart": NB, "cout": NB}),
    "finition": Obj({"antennes_retirees": N, "fusions": N, "descentes_reposees": N, "ko_avant": N, "ko_apres": N,
                     "reparations_verification": N, "violations_reparees": N}),
    "convertisseurs": Liste(Obj({"nom": TXT, "boucle_chaude_evaluee": BOOL, "noeud_estime_mm": Fac(NBP)})),
    "mesures": Obj({"longueur_mm": NBP, "vias": N, "vias_doubles": N, "segments": N, "longueur_retrecie_mm": NBP,
                    "resistance_puissance_mohm": NBP}),
    "empreintes": Obj({"donnees_sha256": TXT, "parametres_sha256": TXT}),
    "parametres_effectifs": PARAMETRES_COMPLETS,
    "execution": Obj({"temps_phases_s": Obj({p: NBP for p in PHASES}), "temps_total_s": NBP, "fils": N,
                      "plafond_temps_atteint": BOOL, "conjointe_plafond_atteint": BOOL, "memoire_estimee_mo": NBP,
                      "memoire_pic_mo": NBP}),
    "routage_initial": Fac(Obj({"echecs_routeur": N, "ko_exact": N})),
    "diagnostic": Fac(Obj({"recherches": N, "expansions": N,
                           "echecs_recherche": Obj({"fenetre_epuisee": N, "plafond_expansions": N,
                                                    "aucun_site_via_plan": N, "autre": N})}))})

# --- Sorties des contrôles (§2.8, avenant A.6) ------------------------------------------------------------------------

MESURES = Obj({"format": TXT, "formule": Enum("cout_commun/2"), "cout_commun": NB,
               "termes": Obj({"longueur_mm": NBP, "vias": N, "retreci_mm": NBP, "resistance_puissance_mohm": NBP,
                              "coudes": N, "ecart_moyen_mm": NB}),
               "parametres": Obj({"seuil_ecart_mm": NBP, "plafond_ecart_mm": NBP})})

PROPRETE = Obj({"format": TXT, "segments": N, "segments_courts": N, "coudes_par_connexion": NBP,
                "vias_par_connexion": NBP, "detour": Obj({"mediane": NB, "p90": NB}), "vias_puissance": N,
                "entrees_excentrees": N, "hors_direction": Dico(PART),
                "ecart_jumeaux": Obj({"longueur": NB, "vias": NB}),
                "paires": Dico(Obj({"delta_longueur_mm": NB, "delta_vias": ENT}))})

MESURE_CONVERTISSEURS = Obj({"format": TXT, "convertisseurs": Liste(Obj({
    "nom": TXT, "boucle_chaude_evaluee": BOOL, "boucle_chaude_mm2": NBP, "boucle_chaude_perimetre_mm": NBP,
    "noeud_longueur_mm": NBP, "noeud_surface_mm2": NBP, "chutes_mv": Dico(NB), "noeud_vias": Fac(N),
    "sensibles_distance_min_mm": Fac(Nul(NBP))}))})

VIAS_COUTURE = Obj({"format": TXT, "unite": Enum("mil"), "reseaux": Liste(TXT), "pas_grille_mm": NBP,
                    "vias": Liste(Obj({"x": NB, "y": NB, "size": NBP, "hole_size": NBP, "net": RESEAU})),
                    "refus": Dico(N)})

# --- Orchestrateur et générateurs de carte (§2.9, §5.1) ---------------------------------------------------------------

CONTROLE_GABARIT = Obj({"format": TXT, "fichier": TXT, "sha256": TXT, "utilisable": BOOL, "erreurs": Liste(TXT),
                        "avertissements": Liste(TXT), "couches_ecrivibles": Liste(TXT),
                        "non_retournables": Dico(TXT),
                        "modeles": Obj({"via": BOOL, "piste": BOOL, "region": BOOL})})

CONFIG_PCBDOC = Obj({"format": TXT, "livrable": LIVRABLE, "serigraphie": Obj({
    "graine": N, "chaines": N, "mouvements": N, "threads": N,
    "tailles_mm": Ou("« auto » ou liste de [hauteur, épaisseur]", Enum("auto"), Liste(Tuple(NBP, NBP), mini=1)),
    "garde_masque_mm": Nul(NBP), "trous_supplementaires_mm": Liste(Liste(NB, n=3)), "options": Liste(TXT)}),
    "vias_couture": BOOL})

RAPPORT_PCBDOC = Obj({"format": TXT, "fichier": TXT, "livrable": LIVRABLE,
                      "controles": Obj({"erreurs": Liste(TXT), "anomalies": Liste(TXT)}), "conflits_isolement": N,
                      "serigraphie": Obj({"etat": Enum("coherent", "incoherent", "non_faite"), "masques": N}),
                      "polygones_a_recouler": N, "chemins_effaces": BOOL, "duree_s": NBP})

_PROPOSITION = Obj({"objet": TXT, "proposition": TXT, "raison": TXT})
DEDUCTION = Obj({"format": TXT, "elements": Liste(Obj({
    "objet": Motif(r"(couche|reseau|piece|convertisseur|groupe):.+",
                   "« couche:<nom> », « reseau:<nom> », « piece:<nom> », « convertisseur:<nom> » ou « groupe:<nom> »"),
    "propriete": TXT, "valeur": TOUT, "regle": TXT, "preuve": TXT,
    "source": Enum("deduit", "contrainte", "fichier")})),
    "suggestions": Liste(_PROPOSITION), "a_confirmer": Liste(_PROPOSITION), "avertissements": Liste(TXT),
    "positions_imposees": Liste(Obj({"piece": TXT, "repere_fichier": TOUT, "repere_carte": TOUT}))})

_COMPTE_OU_TEXTES = Ou("entier ou liste de textes", N, Liste(TXT))
RAPPORT = Obj({
    "format": TXT, "version": TXT, "carte": TXT, "termine": BOOL,
    "fin": Enum("normale", "arret_demande", "duree_max", "valides_voulues"),
    "parametres": Obj({"effectifs": PARAMETRES_COMPLETS, "niveau": TXT, "fichiers_ini": Liste(TXT)}),
    "import": Obj({"composants": N, "reseaux": N, "couches": N}), "deduction": Dico(TOUT),
    "estimation": Obj({"memoire_mo": NBP, "duree_prevue_s": NBP, "duree_max_s": NBP}),
    "graines": Liste(Obj({"graine": N, "dossier": TXT, "code": ENT, "fin": TXT, "plantage": BOOL, "duree_s": NBP,
                          "valide_moteur": Nul(BOOL), "placement": Nul(Dico(TOUT)), "drc_strict": Nul(Dico(TOUT)),
                          "drc_altium": Nul(Dico(TOUT)), "valide": BOOL, "cout_commun": Nul(NB),
                          "incoherence": BOOL})),
    "meilleure": Nul(N),
    "livrable": Nul(Obj({"fichier": TXT, "type": TXT, "erreurs": _COMPTE_OU_TEXTES,
                         "anomalies": _COMPTE_OU_TEXTES})),
    "couture": Nul(Obj({"vias": N, "drc": TOUT})),
    "reference_existante": Nul(Obj({"cout_commun": Nul(NB), "liaisons_ouvertes": N, "violations": N})),
    "incoherences": N, "avertissements": Liste(TXT), "duree_totale_s": NBP})

ESTIMATION = Obj({"format": TXT, "memoire_mo": NBP, "cellules_detail": N, "pas_grille_mm": NBP,
                  "couches_routage": N, "connexions": N, "pieces_mobiles": N, "essais_conjointe": N})

ETALONNAGE = Obj({"format": TXT, "score": NBP})

# --- Fichiers écrits à la main (§2.1, §2.3, §2.4 partiel ; lecture stricte) -------------------------------------------
#
# Toutes les clés de premier niveau sont facultatives ; une clé inconnue est refusée avec la clé la plus proche ; les
# champs des éléments de listes sont obligatoires sauf ceux qui ont un défaut dans le contrat (Fac). Les désignateurs,
# réseaux et couches cités ne sont pas résolus ici (le fichier n'en porte pas la liste) : construire_donnees (B) les
# résout contre la carte et refuse une référence inconnue (§2.3).


def _M(champs, **options):
    """Objet d'un fichier écrit à la main (strict, au style des contraintes)."""
    return Obj(champs, strict="contraintes", **options)


DES = TXT                                    # désignateur ou motif fnmatch
LISTE_DES = Liste(DES)
FACE_V2 = Enum("dessus", "dessous")
_CHAMPS_CONVERTISSEUR = {
    "nom": TXT, "topologie": Enum("abaisseur", "elevateur", "abaisseur_elevateur", "inverseur", "autre"),
    "commutation": Enum("synchrone", "asynchrone", "integre"), "haut": DES, "bas": DES, "pilote": TXT,
    "inductance": DES, "cond_entree": LISTE_DES, "cond_sortie": LISTE_DES, "cond_bulk": LISTE_DES,
    "cond_pilote": LISTE_DES, "cond_bootstrap": LISTE_DES, "reseau_entree": TXT, "reseau_sortie": TXT,
    "reseau_masse": TXT, "reseau_commutation": TXT, "reseaux_courts": Liste(TXT), "reseaux_sensibles": Liste(TXT),
    "boucle_chaude": _M({"condensateurs": LISTE_DES, "reseau_rail": TXT, "reseau_masse": TXT}),
    "origine": Enum("deduit", "impose", "modifie")}
_OBLIGATOIRES_AJOUT = ("nom", "topologie", "commutation", "haut", "bas", "inductance", "reseau_entree",
                       "reseau_sortie", "reseau_masse", "reseau_commutation")
CONVERTISSEUR_AJOUTE = _M({k: (s if k in _OBLIGATOIRES_AJOUT else Fac(s)) for k, s in _CHAMPS_CONVERTISSEUR.items()})
"""Convertisseur ajouté par l'utilisateur : champs d'identité obligatoires ; listes, pilote, boucle chaude et origine
facultatifs (B les complète, calcule la boucle chaude et note l'origine « impose »)."""
CONVERTISSEUR_MODIFIE = _M({k: (s if k == "reseau_commutation" else Fac(s)) for k, s in _CHAMPS_CONVERTISSEUR.items()})
"""Surcharge d'un convertisseur déduit, désigné par son réseau de commutation (§2.4, §3.4 R-V4)."""

_CHAMPS_CONTRAINTES = {
    "format": TXT, "repere": Enum("fichier", "carte"), "unite": Enum("mm", "mil"),
    "fixes": LISTE_DES,
    "fixes_deplaces": Dico(ParType("[x, y] ou {x, y, rotation, face}", {
        list: Liste(NB, n=2), dict: _M({"x": NB, "y": NB, "rotation": Fac(NB), "face": Fac(FACE_V2)})})),
    "liberer": LISTE_DES, "fixer_suggestions": BOOL, "fixer_verrouilles": BOOL,
    "dessus_seulement": LISTE_DES, "dessous_seulement": LISTE_DES,
    "orientations": Dico(Liste(Tuple(NB, BOOL))),
    "faces_par_defaut": Enum("auto", "libres", "actuelle"),
    "rotations_dessous_multibroches": Liste(NB),
    "hauteur_max_dessus_mm": NBP, "hauteur_max_dessous_mm": NBP,
    "hauteur_inconnue": Enum("face_entree", "basse"), "exceptions_hauteur": LISTE_DES,
    "face_encombrants": Enum("actuelle", "dessus"),
    "boites": Dico(Liste(NB, n=4)),
    "au_bord": Liste(_M({"piece": DES, "cote": Enum("gauche", "droite", "haut", "bas", "quelconque"),
                         "debord_mm": Fac(NBP)})),
    "zones_imposees": Liste(_M({"pieces": Liste(DES, mini=1), "faces": Fac(Liste(FACE_V2, mini=1)),
                                "rect": RECT, "polygone": Liste(POINT, mini=3), "etiquette": Fac(TXT)},
                               exclusifs=(("rect", "polygone"),))),
    "salles_comme_zones": BOOL,
    "groupes": Liste(_M({"nom": TXT, "pieces": Liste(DES, mini=1), "rigide": Fac(BOOL)})),
    "groupes_exclus": Liste(TXT),
    "motif_canaux": Regex(),
    "pas_fin_mm": NBP,
    "via_sous_cms_permis": LISTE_DES, "via_dans_pastille_meme_reseau": BOOL,
    "sans_piste_dessous": _M({"ajouter": Fac(LISTE_DES), "retirer": Fac(LISTE_DES)}),
    "zones_interdites": Liste(_M({"faces": Fac(Liste(FACE_V2, mini=1)), "rect": RECT,
                                  "polygone": Liste(POINT, mini=3),
                                  "effets": Fac(Liste(Enum("placement", "pistes", "vias"), mini=1)),
                                  "couches": Fac(Liste(TXT, mini=1)), "etiquette": Fac(TXT)},
                                 exclusifs=(("rect", "polygone"),))),
    "zones_autour": Liste(_M({"empreinte_prefixe": TXT, "designateurs": Liste(DES, mini=1),
                              "face": Enum("dessus", "dessous", "toutes"), "demi_cote_mm": NBP},
                             exclusifs=(("empreinte_prefixe", "designateurs"),))),
    "marge_bord_mm": NBP, "marge_decoupe_mm": NBP, "garde_bord_mm": NBP, "garde_decoupe_mm": NBP,
    "garde_trou_mm": NBP, "marge_piece_mm": NBP, "marge_traversante_mm": NBP, "grille_placement_mm": NBP,
    "rayon_interdit_trou_mm": Nul(NBP),
    "trous_supplementaires": Liste(_M({"x": NB, "y": NB, "diametre": NBP, "diametre_cuivre": NBP,
                                       "metallise": BOOL, "rayon_interdit_mm": Fac(Nul(NBP))})),
    "cuivre_a_garder": Liste(_M({"couches": Liste(TXT, mini=1), "reseaux": Liste(TXT, mini=1),
                                 "zone": Fac(Nul(RectOuPolygone())), "verrouilles_seulement": Fac(BOOL)})),
    "couches": _M({"routage": Fac(Liste(TXT, mini=1)),
                   "plans": Fac(Dico(Ou("réseau ou liste de réseaux", TXT, Liste(TXT, mini=1)))),
                   "inutilisees": Fac(Liste(TXT)), "preferees": Fac(Dico(TXT))}),
    "couche_preferee_par_nom": BOOL,
    "roles": _M({r: Fac(Liste(TXT)) for r in ("puissance", "alimentation", "signal", "commutation", "sensible",
                                               "peu_sensible", "normale")}
                | {"masse": Fac(Ou("réseau ou liste de réseaux", TXT, Liste(TXT, mini=1)))}),
    "reseaux": Dico(_M({"role": Fac(ROLE), "sensibilite": Fac(Enum("normale", "sensible", "peu_sensible")),
                        "poids": Fac(NBP), "priorite": Fac(N), "largeurs_mm": Fac(Tuple(NBP, NBP, Nul(NBP))),
                        "couche_preferee": Fac(TXT), "couches_permises": Fac(Liste(TXT, mini=1)),
                        "descente_large": Fac(BOOL)})),
    "paires": Liste(_M({"nom": TXT, "positif": TXT, "negatif": TXT, "ecart_mm": Fac(NBP),
                        "largeur_mm": Fac(NBP)})),
    "eloignements": Liste(_M({"type": Enum("dure", "souple"), "objets": Liste(TXT, mini=1),
                              "reference": Liste(DES, mini=1), "distance_mm": NBP, "poids_par_mm": Fac(NBP),
                              "marge_mm": Fac(NBP)})),
    "charges": Liste(_M({"broche": Motif(r"[^.]+\..+", "« PIECE.BROCHE »"), "courant_a": NBP})),
    "convertisseurs": _M({"ajouter": Fac(Liste(CONVERTISSEUR_AJOUTE)), "modifier": Fac(Liste(CONVERTISSEUR_MODIFIE)),
                          "supprimer": Fac(Liste(_M({"reseau_commutation": TXT})))}),
}
CONTRAINTES = Obj({k: Fac(s) for k, s in _CHAMPS_CONTRAINTES.items()}, strict="contraintes")
"""contraintes/2 (§2.3), et contraintes_effectives.json (même format, §2.9) : toutes les clés facultatives."""

_TEXTE_OU_LISTE = Ou("texte ou liste de textes", TXT, Liste(TXT))
CONTRAINTES_V1 = Obj({k: Fac(s) for k, s in {
    "fixes": LISTE_DES, "fixes_deplaces": Dico(Liste(NB, n=2)), "dessus_seulement": LISTE_DES,
    "dessous_seulement": LISTE_DES, "orientations": Dico(Liste(Tuple(NB, BOOL))),
    "zones_interdites": Liste(_M({"face": Fac(Enum(0, 1, "toutes")), "rect": RECT, "etiquette": Fac(TXT)})),
    "zones_autour": Liste(_M({"empreinte_prefixe": TXT, "designateurs": LISTE_DES, "face": Fac(Enum(0, 1, "toutes")),
                              "demi_cote": NBP}, exclusifs=(("empreinte_prefixe", "designateurs"),))),
    "marge_bord": NBP, "marge_decoupe": NBP, "rayon_interdit_trou": NBP, "hauteur_max_dessous": NBP,
    "exceptions_hauteur": LISTE_DES, "rotations_dessous_multibroches": Liste(NB), "poids_reseaux": Dico(NBP),
    "plans": Liste(TXT), "via_sous_cms_permis": LISTE_DES, "soft_sw": LISTE_DES, "garde_bord_fentes_trous": NBP,
    "face_encombrants": Enum("actuelle", "dessus")}.items()}, strict="contraintes")
"""Contraintes de la version 1 (sans « format ») : les 19 clés que lit 9b5b5a3 (``importer_carte.py``)."""
CLES_CONTRAINTES_V1 = tuple(CONTRAINTES_V1.champs)

CONFIG_UTILISATEUR = Obj({"format": TXT, "convertisseurs": Fac(Liste(CONVERTISSEUR_MODIFIE)),
                          "sans_piste_dessous": Fac(LISTE_DES), "pieces_a_fixer_suggerees": Fac(LISTE_DES),
                          "surcharges_appliquees": Fac(Liste(TXT)), "avertissements": Fac(Liste(TXT))},
                         strict="contraintes")
"""Fichier ``--config`` de l'utilisateur : config_carte/2 partiel (§3.4 R-V4) ; chaque convertisseur est désigné par
son ``reseau_commutation`` : B le modifie s'il est déduit, l'ajoute sinon (il doit alors être complet)."""

CONFIG_UTILISATEUR_V1 = Obj({"convertisseurs": Fac(Liste(_M({
    "nom": Fac(TXT), "haut": DES, "bas": DES, "pilote": Fac(TXT), "inductance": Fac(TXT),
    "cond_entree": Fac(_TEXTE_OU_LISTE), "cond_bulk": Fac(_TEXTE_OU_LISTE), "cond_sortie": Fac(_TEXTE_OU_LISTE),
    "cond_pilote": Fac(_TEXTE_OU_LISTE), "cond_bootstrap": Fac(_TEXTE_OU_LISTE), "reseau_entree": TXT,
    "reseau_masse": TXT, "reseau_sortie": Fac(TXT), "reseau_commutation": Fac(TXT),
    "reseaux_courts": Fac(Liste(TXT)), "reseaux_sensibles": Fac(Liste(TXT)),
    "topologie": Fac(_CHAMPS_CONVERTISSEUR["topologie"]), "commutation": Fac(_CHAMPS_CONVERTISSEUR["commutation"]),
    "eloignement": Fac(Liste(_M({"broches": Liste(TXT, mini=1), "distance_mm": NBP})))}))),
    "sans_piste_dessous": Fac(LISTE_DES)}, strict="contraintes")
"""config_carte.json de la version 1 (sans « format », écrit à la main pour 9b5b5a3) : champs de ``geom.rs``
(``haut``, ``bas``, ``reseau_entree``, ``reseau_masse`` obligatoires ; condensateurs en texte ou en liste)."""


# --- Ensembles des références croisées et cohérence interne -----------------------------------------------------------

def _champ(d, *cles):
    """``d[c1][c2]…`` si chaque niveau est un objet qui porte la clé, sinon None (lecture tolérante)."""
    for c in cles:
        if type(d) is not dict or c not in d:
            return None
        d = d[c]
    return d


def _noms(liste, cle):
    """Valeurs textuelles de ``cle`` des objets de ``liste`` ; None si ``liste`` n'est pas une liste."""
    if type(liste) is not list:
        return None
    return {e[cle] for e in liste if type(e) is dict and type(e.get(cle)) is str}


def _ids_pastilles(composants):
    if type(composants) is not list:
        return None
    return {p["id"] for c in composants if type(c) is dict and type(c.get("pastilles")) is list
            for p in c["pastilles"] if type(p) is dict and type(p.get("id")) is str}


def _classes(isolement):
    classes = _champ(isolement, "classes")
    return set(range(len(classes))) if type(classes) is list else None


def _sans_inconnus(ensembles):
    return {nature: e for nature, e in ensembles.items() if e is not None}


def _ensembles_entree(d):
    reseaux = d.get("reseaux")
    pastilles = _ids_pastilles(d.get("composants"))
    libres = _noms(d.get("pastilles_libres"), "id")
    return _sans_inconnus({
        "piece": _noms(d.get("composants"), "designateur"),
        "reseau": {r for r in reseaux if type(r) is str} if type(reseaux) is list else None,
        "couche": _noms(_champ(d, "carte", "couches"), "nom"),
        "pastille": pastilles | libres if pastilles is not None and libres is not None else None,
        "decoupe": _noms(_champ(d, "carte", "decoupes"), "id"),
        "classe": _classes(_champ(d, "regles", "isolement")),
        "paire": _noms(d.get("paires"), "nom")})


def _ensembles_donnees(d):
    pieces = _noms(d.get("composants"), "nom")
    pastilles = _ids_pastilles(d.get("composants"))
    return _sans_inconnus({
        "piece": pieces,
        "reseau": _noms(d.get("reseaux"), "nom"),
        "couche": _noms(d.get("couches"), "nom"),
        "pastille": pastilles,
        "decoupe": _noms(_champ(d, "carte", "decoupes"), "id"),
        "classe": _classes(_champ(d, "regles", "isolement")),
        "paire": _noms(d.get("paires"), "nom"),
        "objet": pieces | pastilles if pieces is not None and pastilles is not None else None})


def _doublons(ctx, liste, cle, chemin):
    """Valeurs de ``cle`` en double dans ``liste`` (``cle`` None : éléments eux-mêmes)."""
    vues = set()
    for i, e in enumerate(liste):
        v = e if cle is None else e[cle]
        if v in vues:
            ctx.erreur(_joindre(_joindre(chemin, i), cle) if cle is not None else _joindre(chemin, i),
                       f"« {_court(v)} » en double")
        vues.add(v)


def _rangs(ctx, couches, chemin):
    """Couches de haut en bas : ``rang`` = position dans la liste."""
    for i, c in enumerate(couches):
        if c["rang"] != i:
            ctx.erreur(f"{chemin}[{i}].rang", f"{i} attendu (couches de haut en bas), {c['rang']} trouvé")


def _matrice(ctx, isolement, chemin):
    """Matrice d'isolement carrée (une ligne et une colonne par classe) et symétrique ; rend True si elle l'est."""
    n, m = len(isolement["classes"]), isolement["matrice"]
    if len(m) != n or any(len(ligne) != n for ligne in m):
        ctx.erreur(f"{chemin}.matrice", f"matrice {n} × {n} attendue (une ligne et une colonne par classe)")
        return False
    for i in range(n):
        for j in range(i):
            if m[i][j] != m[j][i]:
                ctx.erreur(f"{chemin}.matrice[{i}][{j}]", f"matrice symétrique attendue ({m[i][j]!r} ≠ "
                                                          f"matrice[{j}][{i}] = {m[j][i]!r})")
    return True


def _identifiants_piece(ctx, composants, cle_nom, chemin, proprietaires):
    """Identifiants des pastilles (« <des>#<k> », §0.4), régions (« <des>/R<k> ») et vias d'empreinte
    (« <des>/V<k> ») ; découpes d'une pièce dont elle est propriétaire (``proprietaires`` : id → désignateur)."""
    for i, c in enumerate(composants):
        nom = c[cle_nom]
        for liste, forme in (("pastilles", "{}#{}"), ("regions", "{}/R{}"), ("vias", "{}/V{}")):
            for k, e in enumerate(c[liste]):
                attendu = forme.format(nom, k)
                if e["id"] != attendu:
                    ctx.erreur(f"{chemin}[{i}].{liste}[{k}].id", f"« {_court(attendu)} » attendu, "
                                                                f"« {_court(e['id'])} » trouvé")
        for k, dc in enumerate(c["decoupes"]):
            if dc in proprietaires and proprietaires[dc] != nom:
                ctx.erreur(f"{chemin}[{i}].decoupes[{k}]", f"découpe {_court(dc)} dont la pièce n'est pas "
                                                          f"propriétaire (carte.decoupes)")


def _decoupes_proprietaires(ctx, decoupes, cites_par, chemin):
    """Une découpe qui a un propriétaire est citée dans les ``decoupes`` de cette pièce."""
    for i, dc in enumerate(decoupes):
        p = dc["proprietaire"]
        if p is not None and dc["id"] not in cites_par.get(p, ()):
            ctx.erreur(f"{chemin}[{i}].proprietaire", f"la pièce {_court(p)} ne cite pas la découpe "
                                                     f"{_court(dc['id'])} dans ses decoupes")


def _boucle_chaude(ctx, cv, chemin):
    """Bloc ``boucle_chaude`` calculé selon la topologie (§2.4) ; circuit intégré : haut = bas = pilote."""
    if cv["topologie"] == "elevateur":
        cond, rail, nom = cv["cond_sortie"], cv["reseau_sortie"], "sortie"
    else:
        cond, rail, nom = cv["cond_entree"], cv["reseau_entree"], "entree"
    b = cv["boucle_chaude"]
    if sorted(b["condensateurs"]) != sorted(cond):
        ctx.erreur(f"{chemin}.boucle_chaude.condensateurs", f"copie de cond_{nom} attendue (topologie "
                                                            f"{cv['topologie']}, §2.4)")
    if b["reseau_rail"] != rail:
        ctx.erreur(f"{chemin}.boucle_chaude.reseau_rail", f"reseau_{nom} attendu ({_court(rail)}, topologie "
                                                          f"{cv['topologie']}, §2.4)")
    if b["reseau_masse"] != cv["reseau_masse"]:
        ctx.erreur(f"{chemin}.boucle_chaude.reseau_masse", f"reseau_masse attendu ({_court(cv['reseau_masse'])})")
    if cv["commutation"] == "integre" and not cv["haut"] == cv["bas"] == cv["pilote"]:
        ctx.erreur(f"{chemin}.commutation", "« integre » : haut, bas et pilote désignent le même circuit")


def _convertisseurs(ctx, convertisseurs, chemin):
    _doublons(ctx, convertisseurs, "reseau_commutation", chemin)
    for k, cv in enumerate(convertisseurs):
        _boucle_chaude(ctx, cv, f"{chemin}[{k}]")


def _copie(ctx, chemin, trouve, attendu, ordre, source):
    """Copie d'une donnée redondante (§2.5) : mêmes éléments que la source, sans doublon."""
    if len(trouve) != len(set(trouve)) or set(trouve) != attendu:
        liste = [x for x in ordre if x in attendu]
        ctx.erreur(chemin, f"copie incohérente avec {source} ({liste} attendu)")


def _coherence_entree(d, ctx):
    carte = d["carte"]
    _doublons(ctx, d["composants"], "designateur", "composants")
    _doublons(ctx, d["reseaux"], None, "reseaux")
    _doublons(ctx, carte["couches"], "nom", "carte.couches")
    _doublons(ctx, carte["decoupes"], "id", "carte.decoupes")
    _doublons(ctx, carte["trous"], "id", "carte.trous")
    _doublons(ctx, d["paires"], "nom", "paires")
    _rangs(ctx, carte["couches"], "carte.couches")
    _matrice(ctx, d["regles"]["isolement"], "regles.isolement")
    proprietaires = {dc["id"]: dc["proprietaire"] for dc in carte["decoupes"]}
    _identifiants_piece(ctx, d["composants"], "designateur", "composants", proprietaires)
    _decoupes_proprietaires(ctx, carte["decoupes"], {c["designateur"]: c["decoupes"] for c in d["composants"]},
                            "carte.decoupes")
    for k, p in enumerate(d["pastilles_libres"]):
        if p["id"] != f"__libre_{k}":
            ctx.erreur(f"pastilles_libres[{k}].id", f"« __libre_{k} » attendu, « {_court(p['id'])} » trouvé")
    noms = [c["nom"] for c in carte["couches"]]
    if carte["noms_couches_cuivre"] != noms or carte["couches_cuivre"] != len(noms):
        ctx.erreur("carte.noms_couches_cuivre", "copie incohérente avec carte.couches (noms et nombre)")


def _coherence_config(d, ctx):
    _convertisseurs(ctx, d["convertisseurs"], "convertisseurs")


def _coherence_donnees(d, ctx):
    carte, couches, reseaux = d["carte"], d["couches"], d["reseaux"]
    _doublons(ctx, d["composants"], "nom", "composants")
    _doublons(ctx, reseaux, "nom", "reseaux")
    _doublons(ctx, couches, "nom", "couches")
    _doublons(ctx, carte["decoupes"], "id", "carte.decoupes")
    _doublons(ctx, d["trous"], "id", "trous")
    _doublons(ctx, d["paires"], "nom", "paires")
    _rangs(ctx, couches, "couches")
    for i, c in enumerate(couches):
        if (c["role"] == "plan") != bool(c["zones_plan"]):
            ctx.erreur(f"couches[{i}].zones_plan", "non vide si et seulement si role = « plan » (§2.5)")
    matrice_ok = _matrice(ctx, d["regles"]["isolement"], "regles.isolement")
    proprietaires = {dc["id"]: dc["proprietaire"] for dc in carte["decoupes"]}
    _identifiants_piece(ctx, d["composants"], "nom", "composants", proprietaires)
    _decoupes_proprietaires(ctx, carte["decoupes"], {c["nom"]: c["decoupes"] for c in d["composants"]},
                            "carte.decoupes")
    for i, c in enumerate(d["composants"]):
        if (c["statut"] == "mobile") != bool(c["orientations"]):
            ctx.erreur(f"composants[{i}].orientations", "non vide pour une pièce mobile, [] pour une fixe (§2.5)")
    ordre = [c["nom"] for c in couches]
    plans, remplissages = {}, {}
    for c in couches:
        for z in c["zones_plan"]:
            plans.setdefault(z["reseau"], set()).add(c["nom"])
    for r in carte["remplissages"]:
        remplissages.setdefault(r["reseau"], set()).add(r["couche"])
    masses = set(d["masses"])
    paires = {p["nom"]: p for p in d["paires"]}
    isolement = d["regles"]["isolement"]
    for i, r in enumerate(reseaux):
        nom = r["nom"]
        _copie(ctx, f"reseaux[{i}].plan_couches", r["plan_couches"], plans.get(nom, set()), ordre,
               "couches[].zones_plan")
        _copie(ctx, f"reseaux[{i}].remplissages", r["remplissages"], remplissages.get(nom, set()), ordre,
               "carte.remplissages")
        if r["masse"] != (nom in masses):
            ctx.erreur(f"reseaux[{i}].masse", "copie incohérente avec masses")
        if r["paire"] is not None and nom not in (paires[r["paire"]]["positif"], paires[r["paire"]]["negatif"]):
            ctx.erreur(f"reseaux[{i}].paire", f"la paire {_court(r['paire'])} ne contient pas ce réseau")
        if matrice_ok:
            attendu = isolement["matrice"][r["classes_isolement"]["piste"]][isolement["classe_reference"]]
            if abs(r["isolement_mm"] - attendu) > 1e-9:
                ctx.erreur(f"reseaux[{i}].isolement_mm", f"{attendu!r} attendu (matrice[classes_isolement.piste]"
                                                         f"[classe_reference], §2.5), {r['isolement_mm']!r} trouvé")
    _convertisseurs(ctx, d["convertisseurs"], "convertisseurs")


def _coherence_placement(v, ctx, chemin):
    total = sum(v["violations_par_type"][g] for g in GENRES_PLACEMENT)
    if v["valide"] != (total == 0):
        ctx.erreur(_joindre(chemin, "valide"), f"{str(total == 0).lower()} attendu ({total} violation(s))")


def _coherence_verdict(v, ctx, chemin=""):
    total = sum(v["violations_par_type"][g] for g in GENRES_ROUTAGE)
    if v["violations"] != total:
        ctx.erreur(_joindre(chemin, "violations"), f"{total} attendu (somme de violations_par_type), "
                                                   f"{v['violations']} trouvé")
    valide = v["liaisons_ouvertes"] == 0 and v["violations"] == 0
    if v["valide"] != valide:
        ctx.erreur(_joindre(chemin, "valide"), f"{str(valide).lower()} attendu (liaisons_ouvertes = 0 et "
                                               f"violations = 0)")
    n = len(v["detail_violations"])
    if v["detail_violations_tronque"]:
        if n != 1000 or v["violations"] <= 1000:
            ctx.erreur(_joindre(chemin, "detail_violations_tronque"), "vrai seulement pour plus de 1000 "
                                                                      "violations, avec 1000 détails")
    elif n != v["violations"]:
        ctx.erreur(_joindre(chemin, "detail_violations"), f"un détail par violation attendu ({v['violations']}), "
                                                          f"{n} trouvé(s)")
    else:
        for g in GENRES_ROUTAGE:
            k = sum(1 for e in v["detail_violations"] if e["genre"] == g)
            if k != v["violations_par_type"][g]:
                ctx.erreur(_joindre(chemin, "detail_violations"), f"{k} détail(s) de genre {g} pour "
                                                                  f"violations_par_type.{g} = "
                                                                  f"{v['violations_par_type'][g]}")
    if (v["marge_isolement_min_mm"] is None) != (v["marge_isolement_min_paire"] == ""):
        ctx.erreur(_joindre(chemin, "marge_isolement_min_paire"), "\"\" si et seulement si "
                                                                  "marge_isolement_min_mm est null")
    if v["reseaux_complets"] > v["reseaux_a_router"]:
        ctx.erreur(_joindre(chemin, "reseaux_complets"), "au plus reseaux_a_router attendu")
    if "placement" in v:
        _coherence_placement(v["placement"], ctx, _joindre(chemin, "placement"))


def _coherence_stats(d, ctx):
    if d["valide"] != (d["cause"] == ""):
        ctx.erreur("cause", "\"\" si et seulement si valide (§2.7)")
    if d["cause"] == "placement impossible":
        for champ in ("piece", "raison"):
            if d[champ] is None:
                ctx.erreur(champ, "texte attendu avec la cause « placement impossible » (§2.7), null trouvé")
    v = d["validite"]
    if v is None:
        if d["cause"] != "non verifie":
            ctx.erreur("validite", "null seulement pour la cause « non verifie »")
    else:
        _coherence_verdict(v, ctx, "validite")
        if d["valide"] and not (v["valide"] and v["placement"]["valide"]):
            ctx.erreur("valide", "vrai seulement si la vérification finale (validite) est valide, placement "
                                 "compris (§4.6)")
    if type(_champ(d, "parametres_effectifs", "campagne")) is dict and "threads" in d["parametres_effectifs"][
            "campagne"]:
        ctx.erreur("parametres_effectifs.campagne.threads", "champ exclu des paramètres effectifs (§2.7)")


def _coherence_mesures(d, ctx):
    t, s = d["termes"], d["parametres"]["seuil_ecart_mm"]
    cout = (t["longueur_mm"] + 1.5 * t["vias"] + 2.0 * t["retreci_mm"] + 3.0 * t["resistance_puissance_mohm"]
            + 0.1 * t["coudes"] + 1000.0 * max(0.0, s - t["ecart_moyen_mm"]))
    if abs(d["cout_commun"] - cout) > 1e-6 * max(1.0, abs(cout)):
        ctx.erreur("cout_commun", f"{cout!r} attendu (formule cout_commun/2 appliquée aux termes, §4.7), "
                                  f"{d['cout_commun']!r} trouvé")


def _coherence_placements(d, ctx):
    pieces = d if type(d) is list else d["pieces"]
    _doublons(ctx, pieces, "designator", "" if type(d) is list else "pieces")


def _tronquer(erreurs):
    if len(erreurs) <= MAX_ERREURS:
        return erreurs
    return erreurs[:MAX_ERREURS] + [f"… et {len(erreurs) - MAX_ERREURS} autre(s) erreur(s)"]


def _valider(d, nom, schema, ensembles=None, coherence=None):
    """Validation d'un document d'un format écrit par un programme : champ « format », schéma (types, champs,
    références croisées), puis, si le schéma est respecté, cohérence interne (copies, sommes, identifiants)."""
    if not isinstance(d, dict):
        return ["racine : objet JSON attendu"]
    erreurs = _erreurs_format(d, nom)
    if erreurs:
        return erreurs
    ctx = _Contexte(ensembles(d) if ensembles is not None else None)
    schema.verifier(d, "", ctx)
    if coherence is not None and not ctx.erreurs:
        coherence(d, ctx)
    return _tronquer(ctx.erreurs)


# ---------------------------------------------------------------------------------------------------------------------
# Validateurs (un par format)
# ---------------------------------------------------------------------------------------------------------------------

def valider_entree_carte(d):
    """``entree_carte/2`` (§2.2) : schéma complet ; références aux pièces, réseaux, couches, pastilles, découpes,
    classes d'isolement et paires du fichier ; noms uniques, rangs, matrice carrée et symétrique, identifiants."""
    return _valider(d, "entree_carte", ENTREE_CARTE, _ensembles_entree, _coherence_entree)


def _valider_main(d, schema):
    """Validation d'un fichier écrit à la main (§2.1) : schéma strict, indice « virgule décimale ? »."""
    ctx = _Contexte(main=True)
    schema.verifier(d, "", ctx)
    return _tronquer(ctx.erreurs)


def valider_contraintes(d):
    """``contraintes/2`` (§2.3) et ``contraintes_effectives.json``, fichier écrit à la main : toutes les clés sont
    facultatives ; clé inconnue refusée avec la clé la plus proche (« 'fixe' inconnue ; vouliez-vous dire
    'fixes' ? »), à tout niveau ; types, énumérations, longueurs (point = 2, rectangle = 4, « virgule décimale ? »),
    nombre entre guillemets, null seulement où la table le permet. Sans « format » : version 1 (les 19 clés lues par
    9b5b5a3 ; une clé de la version 2 y est refusée avec l'indice d'ajouter le format)."""
    if not isinstance(d, dict):
        return ["racine : objet JSON attendu"]
    if "format" in d:
        return _erreurs_format(d, "contraintes") or _valider_main(d, CONTRAINTES)
    v2_seulement = [c for c in d if c in CLES_CONTRAINTES and c not in CLES_CONTRAINTES_V1]
    erreurs = [f"'{c}' : clé de contraintes/2 dans un fichier sans « format » (lu comme la version 1) ; ajouter "
               f"\"format\": \"contraintes/2\"" for c in v2_seulement]
    return erreurs + _valider_main({k: v for k, v in d.items() if k not in v2_seulement}, CONTRAINTES_V1)


def valider_config_utilisateur(d):
    """Fichier ``--config`` de l'utilisateur (§2.4, §3.4 R-V4), écrit à la main : ``config_carte/2`` partiel (toutes
    les clés facultatives, chaque convertisseur désigné par ``reseau_commutation``), lecture stricte ; sans
    « format » : config_carte.json de la version 1 (champs de ``geom.rs``, ``eloignement`` compris)."""
    if not isinstance(d, dict):
        return ["racine : objet JSON attendu"]
    if "format" in d:
        return _erreurs_format(d, "config_carte") or _valider_main(d, CONFIG_UTILISATEUR)
    return _valider_main(d, CONFIG_UTILISATEUR_V1)


def valider_config_carte(d):
    """``config_carte/2`` (§2.4) : schéma complet ; réseaux de commutation uniques ; boucle chaude selon la
    topologie. Les pièces et réseaux cités ne sont pas contrôlés (le fichier n'en porte pas la liste)."""
    return _valider(d, "config_carte", CONFIG_CARTE, None, _coherence_config)


def valider_donnees_placeur(d):
    """``donnees_placeur/2`` (§2.5) : schéma complet, champs supprimés refusés ; références croisées internes
    (§2.1) ; cohérence des copies (``plan_couches``, ``remplissages``, ``masse``, ``isolement_mm``), noms uniques,
    rangs, matrice, identifiants, orientations selon le statut, boucle chaude."""
    return _valider(d, "donnees_placeur", DONNEES_PLACEUR, _ensembles_donnees, _coherence_donnees)


def valider_parametres_moteur(d):
    """``parametres_moteur/1`` (§2.6, annexe C), fichier partiel de l'utilisateur ou complet : « format » facultatif,
    clés ⊆ annexe C à tout niveau (« routage.espacment : clé inconnue ; vouliez-vous dire espacement ? »), clés
    « _* » ignorées, clés réservées acceptées ; types et énumérations ; null seulement où l'annexe C le montre ;
    longueurs des listes ; nombre entre guillemets refusé."""
    if not isinstance(d, dict):
        return ["racine : objet JSON attendu"]
    if "format" in d:
        erreurs = _erreurs_format(d, "parametres_moteur")
        if erreurs:
            return erreurs
    return _valider_main(d, PARAMETRES_PARTIELS)


def valider_placements(d):
    """``placements/2`` (§2.7), ou forme v1 : liste nue d'objets ``designator, side, x, y, rotation`` ;
    désignateurs uniques."""
    if isinstance(d, list):
        ctx = _Contexte()
        Liste(PLACEMENT_V1).verifier(d, "", ctx)
        if not ctx.erreurs:
            _coherence_placements(d, ctx)
        return _tronquer(ctx.erreurs)
    return _valider(d, "placements", PLACEMENTS, None, _coherence_placements)


def valider_routage(d):
    """``routage/2`` (§2.7 ; ``layer`` des pistes ∈ ``couches_routage`` ; ``note``, ``raccords_bloc``,
    ``segments_touchant_emprise`` et ``stats`` refusés), ou forme v1 (sans « format » : ``pistes`` et ``vias`` ;
    ``unite`` facultative, « mil » si présente ; ``surfaces`` facultatives)."""
    if isinstance(d, dict) and "format" not in d:
        if any(c in d for c in CLES_ROUTAGE_V2_SEULEMENT):
            presentes = ", ".join(c for c in CLES_ROUTAGE_V2_SEULEMENT if c in d)
            return [f"format : champ manquant ({presentes} présent : forme routage/2, pas v1)"]
        ctx = _Contexte()
        ROUTAGE_V1.verifier(d, "", ctx)
        return _tronquer(ctx.erreurs)

    def ensembles(r):
        couches = r.get("couches_routage")
        return {"couche": {c for c in couches if type(c) is str}} if type(couches) is list else {}

    return _valider(d, "routage", ROUTAGE, ensembles)


def valider_stats(d):
    """``stats/2`` (§2.7) : ``fin``, ``cause``, toutes les clés de ``temps_phases_s``, ``validite`` (verdict/2 avec
    ``placement``) ; champs facultatifs de l'avenant 2.2, A.5 (``recuit.exploration``, ``routage_initial``,
    ``diagnostic``, ``convertisseurs[].noeud_estime_mm``) acceptés et typés ; cause vide si et seulement si valide."""
    return _valider(d, "stats", STATS, None, _coherence_stats)


def valider_verdict(d):
    """``verdict/2`` (§2.8 ; ``placement`` facultatif, ``avertissements.zones_plan_isolees`` facultatif) :
    les 12 clés de §4.4 ; somme des violations, ``valide``, un détail par violation (au plus 1000)."""
    return _valider(d, "verdict", VERDICT, None, lambda v, ctx: _coherence_verdict(v, ctx))


def valider_controle_placement(d):
    """``controle_placement/1`` (§2.8) : les 14 clés de §4.5 ; ``valide`` si et seulement si aucune violation."""
    return _valider(d, "controle_placement", CONTROLE_PLACEMENT, None,
                    lambda v, ctx: _coherence_placement(v, ctx, ""))


def valider_mesures(d):
    """``mesures/2`` (§2.8, §4.7) : ``cout_commun`` égal à la formule appliquée aux termes."""
    return _valider(d, "mesures", MESURES, None, _coherence_mesures)


def valider_proprete(d):
    """``proprete/1`` (§2.8)."""
    return _valider(d, "proprete", PROPRETE)


def valider_mesure_convertisseurs(d):
    """``mesure_convertisseurs/1`` (§2.8 ; champs facultatifs de l'avenant 2.2, A.6 : ``noeud_vias``,
    ``sensibles_distance_min_mm``) : ``{"format", "convertisseurs": [...]}`` (choix du lot E, le contrat décrit le
    contenu « par convertisseur »)."""
    return _valider(d, "mesure_convertisseurs", MESURE_CONVERTISSEURS)


def valider_vias_couture(d):
    """``vias_couture/2`` (§2.8) : réseau de chaque via ∈ ``reseaux``."""
    def ensembles(v):
        reseaux = v.get("reseaux")
        return {"reseau": {r for r in reseaux if type(r) is str}} if type(reseaux) is list else {}

    return _valider(d, "vias_couture", VIAS_COUTURE, ensembles)


def valider_controle_gabarit(d):
    """``controle_gabarit/1`` (§2.9)."""
    return _valider(d, "controle_gabarit", CONTROLE_GABARIT)


def valider_config_pcbdoc(d):
    """``config_pcbdoc/2`` (§2.9)."""
    return _valider(d, "config_pcbdoc", CONFIG_PCBDOC)


def valider_rapport_pcbdoc(d):
    """``rapport_pcbdoc/1`` (§2.9)."""
    return _valider(d, "rapport_pcbdoc", RAPPORT_PCBDOC)


def valider_deduction(d):
    """``deduction/1`` (§2.9)."""
    return _valider(d, "deduction", DEDUCTION)


def valider_rapport(d):
    """``rapport/2`` (§2.9)."""
    return _valider(d, "rapport", RAPPORT)


def valider_estimation(d):
    """``estimation/1`` (§5.1, ``placeur_routage estimer``)."""
    return _valider(d, "estimation", ESTIMATION)


def valider_etalonnage(d):
    """``etalonnage/1`` (§5.1, ``placeur_routage etalonner``)."""
    return _valider(d, "etalonnage", ETALONNAGE)


VALIDATEURS = {nom: globals()["valider_" + nom] for nom in FORMATS}
"""Nom du format → validateur."""


# ---------------------------------------------------------------------------------------------------------------------
# Contraintes de l'utilisateur
# ---------------------------------------------------------------------------------------------------------------------

_FACES_V1 = {0: "dessus", 1: "dessous"}


def _sans_commentaires(x):
    """Copie profonde sans les clés « _* » (commentaires, §0.5)."""
    if isinstance(x, dict):
        return {k: _sans_commentaires(v) for k, v in x.items() if not k.startswith("_")}
    if isinstance(x, list):
        return [_sans_commentaires(v) for v in x]
    return x


def _convertisseurs_config(config, nom):
    """Convertisseurs d'une configuration passée à lire_contraintes (config_carte v1 ou v2, partielle ou complète) :
    liste de dicts dont ``haut`` et ``bas`` sont des textes ; ErreurFormat sinon (chemin du champ)."""
    if not isinstance(config, dict):
        raise ErreurFormat(f"{nom} : config : objet attendu")
    convertisseurs = config.get("convertisseurs", [])
    if not isinstance(convertisseurs, list):
        raise ErreurFormat(f"{nom} : config.convertisseurs : liste attendue")
    for i, cv in enumerate(convertisseurs):
        for cle in ("haut", "bas"):
            if not isinstance(cv, dict) or not isinstance(cv.get(cle), str):
                raise ErreurFormat(f"{nom} : config.convertisseurs[{i}].{cle} : texte attendu")
    return convertisseurs


def _eloignements_config_v1(config, nom):
    """Clé ``eloignement`` d'un config_carte.json v1 → éloignements ``dure`` de référence ``[haut]`` (§2.3)."""
    if not isinstance(config, dict) or "format" in config:
        return [], []
    eloignements = []
    for i, cv in enumerate(_convertisseurs_config(config, nom)):
        for j, e in enumerate(cv.get("eloignement", [])):
            try:
                eloignements.append({"type": "dure", "objets": list(e["broches"]), "reference": [cv["haut"]],
                                     "distance_mm": e["distance_mm"]})
            except (KeyError, TypeError):
                raise ErreurFormat(f"{nom} : config.convertisseurs[{i}].eloignement[{j}] : « broches » et "
                                   f"« distance_mm » attendus") from None
    avertissements = [f"{nom} : config (version 1) : {len(eloignements)} éloignement(s) « eloignement » converti(s) "
                      f"en éloignements dure de référence [haut]"] if eloignements else []
    return eloignements, avertissements


def _convertir_contraintes_v1(d, nom, config=None, plans_deduits=None):
    """Contraintes v1 → contraintes/2 (§2.3), sur un document déjà validé (valider_contraintes) ; ne modifie pas
    son entrée. Rend (contraintes/2, avertissements)."""
    avertissements = [f"{nom} : contraintes de la version 1 (sans « format ») converties en contraintes/2"]
    d = _sans_commentaires(d)
    v2 = {"format": "contraintes/2", "repere": "carte", "unite": "mm"}
    for cle, valeur in d.items():
        if cle in RENOMMAGES_CONTRAINTES_V1:
            for nouvelle in RENOMMAGES_CONTRAINTES_V1[cle]:
                v2[nouvelle] = valeur
        elif cle == "poids_reseaux":
            for reseau, poids in valeur.items():
                v2.setdefault("reseaux", {})[reseau] = {"poids": poids}
        elif cle == "zones_interdites":
            v2[cle] = [dict({"faces": ["dessus", "dessous"] if z.get("face") == "toutes"
                             else [_FACES_V1[z.get("face", 0)]]},
                            **{k: x for k, x in z.items() if k != "face"}) for z in valeur]
        elif cle == "zones_autour":
            v2[cle] = [{("demi_cote_mm" if k == "demi_cote" else k): x for k, x in z.items() if k != "face"}
                       | {"face": _FACES_V1.get(z.get("face", 0), "toutes")} for z in valeur]
        elif cle == "soft_sw":
            if not valeur:
                continue
            if config is None:
                avertissements.append(f"{nom} : soft_sw : non converti (la référence, interrupteurs haut et bas du "
                                      f"premier convertisseur, demande la configuration : lire_contraintes(chemin, "
                                      f"config=…)) ; clé ignorée")
                continue
            convertisseurs = _convertisseurs_config(config, nom)
            if not convertisseurs:
                avertissements.append(f"{nom} : soft_sw : aucun convertisseur dans la configuration ; clé ignorée")
                continue
            reference = list(dict.fromkeys((convertisseurs[0]["haut"], convertisseurs[0]["bas"])))
            v2.setdefault("eloignements", []).append({
                "type": "souple", "objets": list(valeur), "reference": reference,
                "distance_mm": SOFT_SW_DISTANCE_MM, "poids_par_mm": SOFT_SW_POIDS_PAR_MM})
        elif cle == "plans":
            plans = ", ".join(valeur) or "aucun"
            if plans_deduits is None:
                avertissements.append(f"{nom} : plans (version 1) : {plans} : contrôle de cohérence seulement, "
                                      f"jamais une surcharge ; non comparé (plans déduits non fournis)")
            elif set(valeur) != set(plans_deduits):
                deduits = ", ".join(sorted(plans_deduits)) or "aucun"
                avertissements.append(f"{nom} : plans (version 1) : {plans} ; plans déduits : {deduits} : écart "
                                      f"(la déduction l'emporte)")
        else:
            v2[cle] = valeur
    return v2, avertissements


def lire_contraintes(chemin, config=None, plans_deduits=None):
    """Lecture stricte d'un fichier de contraintes (§2.3), ``contraintes_effectives.json`` compris ; rend
    (contraintes/2 sans les commentaires « _* », avertissements).

    UTF-8 (BOM accepté), repli cp1252 signalé. Un fichier sans « format » est lu comme la version 1 et converti
    (avertissement) selon toutes les règles de §2.3. Options (choix du lot E, la signature de §5.2 reste valable) :
    ``config`` (dict config_carte v1 ou v2) donne la référence de ``soft_sw`` (interrupteurs haut et bas de son
    premier convertisseur) et, pour une configuration v1, ses ``eloignement`` (éloignements dure de référence
    ``[haut]``, ajoutés aux contraintes) ; ``plans_deduits`` (réseaux de plan déduits, §3.1) sert au contrôle de
    cohérence de ``plans`` v1 (avertissement en cas d'écart, jamais une surcharge). ErreurFormat si le fichier est
    illisible ou non conforme (clé inconnue avec la clé la plus proche, version non prise en charge…).
    """
    nom = _nom_fichier(chemin)
    d, avertissements = _lire_json_main(chemin)
    erreurs = valider_contraintes(d)
    if erreurs:
        raise ErreurFormat([f"{nom} : {e}" for e in erreurs])
    if "format" not in d:
        v2, conversion = _convertir_contraintes_v1(d, nom, config, plans_deduits)
    else:
        v2, conversion = _sans_commentaires(d), []
    eloignements, venus = _eloignements_config_v1(config, nom)
    if eloignements:
        v2["eloignements"] = list(v2.get("eloignements", [])) + eloignements
    return v2, avertissements + conversion + venus


def lire_parametres_moteur(chemin):
    """Lecture stricte d'un fichier ``parametres_moteur/1`` de l'utilisateur (§2.6), partiel ou complet ; rend
    (document sans les commentaires « _* », avertissements) ; UTF-8 (BOM accepté), repli cp1252 signalé ;
    ErreurFormat si le fichier est illisible ou non conforme."""
    nom = _nom_fichier(chemin)
    d, avertissements = _lire_json_main(chemin)
    erreurs = valider_parametres_moteur(d)
    if erreurs:
        raise ErreurFormat([f"{nom} : {e}" for e in erreurs])
    return _sans_commentaires(d), avertissements


def _liste_v1(valeur):
    """Condensateurs v1 : texte → liste d'un élément, "" → [] (§2.4)."""
    if isinstance(valeur, str):
        return [valeur] if valeur else []
    return list(valeur)


def lire_config_utilisateur(chemin):
    """Lecture stricte du fichier ``--config`` de l'utilisateur (§3.4 R-V4) ; rend (config_carte/2 partiel sans les
    commentaires, avertissements). Une configuration v1 (sans « format ») est convertie : condensateurs en listes
    (texte → liste d'un élément, "" → []), ``reseau_commutation`` vide retiré (B le déduit), ``eloignement`` retiré
    (il devient un éloignement des contraintes : lire_contraintes(…, config=document v1)), avec un avertissement."""
    nom = _nom_fichier(chemin)
    d, avertissements = _lire_json_main(chemin)
    erreurs = valider_config_utilisateur(d)
    if erreurs:
        raise ErreurFormat([f"{nom} : {e}" for e in erreurs])
    d = _sans_commentaires(d)
    if "format" in d:
        return d, avertissements
    avertissements.append(f"{nom} : configuration de la version 1 (sans « format ») convertie en config_carte/2 "
                          f"partiel")
    convertisseurs = []
    for cv in d.get("convertisseurs", []):
        cv = {k: (_liste_v1(x) if k.startswith("cond_") else x) for k, x in cv.items() if k != "eloignement"}
        if cv.get("reseau_commutation") == "":
            del cv["reseau_commutation"]
        convertisseurs.append(cv)
    if any("eloignement" in cv for cv in d.get("convertisseurs", [])):
        avertissements.append(f"{nom} : eloignement : retiré de la configuration ; il devient un éloignement dure "
                              f"des contraintes (lire_contraintes(…, config=…))")
    v2 = {"format": "config_carte/2"}
    if "convertisseurs" in d:
        v2["convertisseurs"] = convertisseurs
    if "sans_piste_dessous" in d:
        v2["sans_piste_dessous"] = d["sans_piste_dessous"]
    return v2, avertissements


# ---------------------------------------------------------------------------------------------------------------------
# Placements
# ---------------------------------------------------------------------------------------------------------------------

def placements_en_liste(p, donnees):
    """Poses de toutes les pièces de ``donnees`` (ordre de ``donnees.composants``), pour un placement v1 (liste nue)
    ou v2 (``placements/2``).

    Chaque élément : ``{"designator", "side": "top"|"bottom", "x", "y" (mil), "rotation" (degrés), "statut",
    "virtuel": bool, "absente": bool}``. Une pièce virtuelle, ou absente du placement, est prise à sa ``pose_cible``
    (obligatoire, sans alias : le nom de la version 1 est supprimé par §2.5, comme dans validite.rs), convertie en
    mil arrondis au millième (précision des fichiers de placement, §0.4), et marquée comme telle (§2.7) :
    ``absente`` vaut true pour une pièce non virtuelle manquante, sauf, dans un placement v1 (qui ne porte que les
    pièces mobiles), pour une pièce fixe (comportement de 9b5b5a3, comme validite.rs). Les désignateurs inconnus des
    données sont ignorés : le contrôle de placement les signale (``inconnue``). ErreurFormat si le placement ou les
    données sont mal formés.
    """
    try:
        v1 = isinstance(p, list)
        pieces = p if v1 else p["pieces"]
        par_nom = {}
        for e in pieces:
            par_nom.setdefault(e["designator"], e)
        liste = []
        for c in donnees["composants"]:
            nom = c["nom"]
            virtuel = bool(c.get("virtuel", False))
            e = None if virtuel else par_nom.get(nom)
            if e is not None:
                liste.append({"designator": nom, "side": e["side"], "x": float(e["x"]), "y": float(e["y"]),
                              "rotation": float(e["rotation"]), "statut": c.get("statut"), "virtuel": False,
                              "absente": False})
                continue
            x, y, rotation, dessous = c["pose_cible"]
            absente = not virtuel and not (v1 and c.get("statut") == "fixe")
            liste.append({"designator": nom, "side": "bottom" if dessous else "top", "x": round(float(x) / MIL, 3),
                          "y": round(float(y) / MIL, 3), "rotation": float(rotation), "statut": c.get("statut"),
                          "virtuel": virtuel, "absente": absente})
        return liste
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as e:
        raise ErreurFormat(f"placement ou données mal formés ({type(e).__name__} : {_court(e, 120)})") from None


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------

USAGE = ("usage : formats.py valider FICHIER [--config-utilisateur]\n"
         "  --config-utilisateur : un fichier config_carte/2 est le fichier --config partiel de l'utilisateur")


def _arguments(argv):
    """Arguments de la ligne de commande : ``argv``, ou ceux du processus quand ``argv`` est None."""
    analyseur = argparse.ArgumentParser(prog="formats.py", add_help=False, allow_abbrev=False)
    return list(analyseur.parse_known_args(argv)[1])


def _ecrire(texte, flux):
    """Écrit une ligne ; un caractère que le flux ne sait pas encoder (console, sortie redirigée en cp1252) est
    remplacé, jamais une erreur interne."""
    try:
        print(texte, file=flux)
    except UnicodeEncodeError:
        codage = getattr(flux, "encoding", None) or "utf-8"
        print(str(texte).encode(codage, "replace").decode(codage, "replace"), file=flux)


def _valider_fichier(chemin, sortie, erreur, config_utilisateur=False):
    nom = _nom_fichier(chemin)
    try:  # repli cp1252 permis, puis accepté seulement pour un fichier écrit à la main (avertissement)
        d, avertissements = _lire_json_main(chemin)
    except ErreurFormat as e:
        _ecrire(e, erreur)
        return 2
    if isinstance(d, dict) and "format" in d and not isinstance(d["format"], str):
        _ecrire(f"{nom} : format : texte attendu", erreur)
        return 2
    nom_format = _format_du_document(d)
    if nom_format == "config_carte" and config_utilisateur:
        nom_format = "config_utilisateur"
    if nom_format is None:
        _ecrire(f"{nom} : format : champ manquant (format du document indéterminé)", erreur)
        return 2
    if nom_format not in FORMATS and nom_format != "config_utilisateur":
        _ecrire(f"{nom} : format « {_court(d.get('format'))} » inconnu", erreur)
        return 2
    if avertissements and nom_format not in ECRITS_A_LA_MAIN:
        _ecrire(f"{nom} : encodage illisible (UTF-8 attendu pour un fichier écrit par un programme)", erreur)
        return 2
    for a in avertissements:
        _ecrire(a, erreur)
    validateur = valider_config_utilisateur if nom_format == "config_utilisateur" else VALIDATEURS[nom_format]
    erreurs = validateur(d)
    if erreurs:
        for e in erreurs:
            _ecrire(f"{nom} : {e}", erreur)
        return 2
    forme = d.get("format") if isinstance(d, dict) and "format" in d else f"{nom_format}, forme v1"
    if nom_format == "config_utilisateur":
        forme += ", fichier --config partiel de l'utilisateur"
    _ecrire(f"{nom} : conforme ({forme})", sortie)
    return 0


def main(argv=None):
    """``formats.py valider FICHIER`` : 0 conforme, 2 non conforme, illisible ou usage incorrect, 4 erreur interne."""
    sortie, erreur = sys.stdout, sys.stderr
    for flux in (sortie, erreur):  # hors console (sortie redirigée) : UTF-8, caractère non codable remplacé (§0.2-10)
        try:
            if not flux.isatty() and hasattr(flux, "reconfigure"):
                flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    try:
        mots = _arguments(argv)
        if mots and mots[0] in ("-h", "--help", "aide"):
            _ecrire(USAGE, sortie)
            return 0
        config_utilisateur = "--config-utilisateur" in mots[2:]
        options = [m for m in mots[2:] if m != "--config-utilisateur"]
        if len(mots) < 2 or mots[0] != "valider" or options or mots[1].startswith("--"):
            _ecrire(USAGE, erreur)
            return 2
        return _valider_fichier(mots[1], sortie, erreur, config_utilisateur)
    except Exception as e:  # erreur interne : jamais de trace ni de chemin
        _ecrire(f"formats.py : erreur interne ({type(e).__name__})", erreur)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
