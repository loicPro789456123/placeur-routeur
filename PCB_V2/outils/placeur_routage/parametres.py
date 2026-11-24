# -*- coding: utf-8 -*-
"""Paramètres du placeur-routeur : fichiers .ini, niveaux de qualité, paramètres du moteur (lot D).

Contrat 2.1 : §5.4 (fichiers .ini), §2.6 (parametres_moteur/1, niveaux), §5.3 (durées), §2.9
(contraintes_effectives.json), §0.2-1 (aucun fichier lu par défaut), §0.2-9 (lecture stricte), §0.3 (anonymat) ;
avenant 2.2, A.1 (valeur du niveau normal, obtenue du moteur).

- Lecture d'un .ini (`lire_ini`, `analyser_ini`) : tolérante sur la forme (oui/non/vrai/faux/1/0, valeurs
  énumérées sans tenir compte de la casse ni des accents, virgule décimale, séparateur de milliers dans un entier,
  commentaire en fin de ligne, guillemets autour d'une valeur, BOM, repli cp1252 signalé), stricte sur le fond :
  valeur invalide ⇒ `ErreurParametres` (code 2) au message lisible ; clé ou section inconnue ⇒ avertissement.
- Priorités (`charger`) : défauts < %APPDATA%\\PlaceurRouteur\\parametres.ini < <dossier de la carte>\\<nom>.ini <
  --parametres < options. Les deux fichiers implicites ne sont lus qu'en mode figé (exécutables du paquet).
- Écriture (`mettre_a_jour_texte`, `enregistrer`) : ligne à ligne, commentaires conservés (jamais
  configparser.write), UTF-8 avec BOM, CRLF, atomique. `MODELE_INI` est le modèle commenté du paquet.
- Niveaux (`NIVEAUX`) : les deux lignes propres à l'orchestrateur (essais, durée maximale du calcul). Les clés du
  moteur de chaque niveau ne sont jamais recopiées ici : elles viennent de `placeur_routage parametres-defaut
  --qualite <niveau>` (`defauts_moteur`).
- Paramètres du moteur (`parametres_moteur`) : défauts du niveau < --parametres-moteur < [avance] < options, fusion
  profonde (objet par objet ; tableau et scalaire remplacés ; clés `_*` ignorées ; clé inconnue ⇒ code 2 avec la
  clé la plus proche).
- `[contraintes]` (`fusionner_contraintes`) : listes ajoutées, scalaires remplacés avec avertissement.
- `parametres_utilises.ini` (`texte_parametres_utilises`) : valeurs effectives, noms de fichiers seulement.
- Durées prévues (`duree_graine_prevue`, `duree_calcul_prevue_s`) : coefficients par phase.

Aucune variable d'environnement n'est lue. Bibliothèque standard et chemins.py seulement.
"""
from __future__ import annotations

import copy
import decimal
import difflib
import json
import math
import os
import re
import sys
import unicodedata

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import chemins  # noqa: E402

FORMAT_MOTEUR = "parametres_moteur/1"
FORMAT_CONTRAINTES = "contraintes/2"
QUALITES = ("apercu", "rapide", "normal", "soigne")
MODES_ROUTAGE = ("auto", "manhattan", "plus_court", "diagonale_45", "comparer")
"""Modes de routage demandés au moteur (placeur.MODE_ROUTAGE_MOTEUR) ; « comparer » : un mode par graine."""

# Lignes de la table des niveaux de §2.6 propres à l'orchestrateur (les autres viennent du moteur).
NIVEAUX = {
    "apercu": {"essais": 1, "duree_max_min": 15},
    "rapide": {"essais": 4, "duree_max_min": 60},
    "normal": {"essais": 8, "duree_max_min": 180},
    "soigne": {"essais": 16, "duree_max_min": 480},
}

NOM_DOSSIER_UTILISATEUR = "PlaceurRouteur"   # %APPDATA%\PlaceurRouteur\parametres.ini
NOM_INI_UTILISATEUR = "parametres.ini"
ORIGINE_DEFAUT = "défaut"
ORIGINE_OPTIONS = "options"


class ErreurParametres(ValueError):
    """Valeur, fichier ou clé invalide : code 2 (§1.4). `message` est le texte exact pour l'utilisateur ;
    `fichier` (nom seul) et `ligne` le situent."""

    code = 2

    def __init__(self, message: str, fichier: str | None = None, ligne: int | None = None):
        super().__init__(message)
        self.message = message
        self.fichier = fichier
        self.ligne = ligne

    def texte_complet(self) -> str:
        """Message, suivi de sa position (« (fichier x.ini, ligne 12) ») quand elle est connue."""
        lieu = []
        if self.fichier:
            lieu.append(f"fichier {self.fichier}")
        if self.ligne:
            lieu.append(f"ligne {self.ligne}")
        return self.message + (f" ({', '.join(lieu)})" if lieu else "")


# ------------------------------------------------------------------------------------------------ schéma (§5.4)

class Cle:
    """Une clé de .ini : type (« chemin », « bool », « entier », « nombre », « liste » ou tuple de valeurs
    permises), défaut (valeur prise quand la clé est absente ou vide), minimum, strictement positif."""

    __slots__ = ("type", "defaut", "minimum", "positif")

    def __init__(self, type_, defaut=None, minimum=None, positif=False):
        self.type = type_
        self.defaut = defaut
        self.minimum = minimum
        self.positif = positif


SCHEMA = {
    "carte": {
        "fichier": Cle("chemin"),
        "contraintes": Cle("chemin"),
        "config_convertisseurs": Cle("chemin"),
    },
    "sortie": {
        "dossier": Cle("chemin"),
        "ouvrir_a_la_fin": Cle("bool", True),
        "garder_graines": Cle(("meilleure", "toutes"), "meilleure"),
    },
    "calcul": {
        "qualite": Cle(QUALITES, "normal"),
        "essais": Cle("entier", 0, minimum=0),
        "duree_max_min": Cle("entier", 0, minimum=0),
        "valides_voulues": Cle("entier", 0, minimum=0),
        "graine_depart": Cle("entier", 1, minimum=0),
        "processeurs": Cle("entier", 0, minimum=0),
        "priorite_normale": Cle("bool", False),
        "mode_routage": Cle(MODES_ROUTAGE, "auto"),
    },
    "pcbdoc": {
        "produire": Cle("bool", True),
        "produire_si_invalide": Cle("bool", True),
        "serigraphie": Cle("bool", True),
        "vias_couture": Cle("bool", True),
    },
    "contraintes": {
        "fixes": Cle("liste"),
        "liberer": Cle("liste"),
        "dessus_seulement": Cle("liste"),
        "dessous_seulement": Cle("liste"),
        "faces_par_defaut": Cle(("auto", "libres", "actuelle")),
        "fixer_suggestions": Cle("bool"),
        "fixer_verrouilles": Cle("bool"),
        "hauteur_inconnue": Cle(("face_entree", "basse")),
        "marge_bord_mm": Cle("nombre", minimum=0),
        "garde_bord_mm": Cle("nombre", minimum=0),
        "marge_decoupe_mm": Cle("nombre", minimum=0),
        "garde_decoupe_mm": Cle("nombre", minimum=0),
        "pas_fin_mm": Cle("nombre", positif=True),
        "grille_placement_mm": Cle("nombre", positif=True),
    },
    "avance": {
        "parametres_moteur": Cle("chemin"),
        "conjointe_essais_par_piece": Cle("nombre", minimum=0),
        "boucle": Cle("entier", minimum=0),
        "mouvements": Cle("entier", minimum=1),
        "iters_det": Cle("entier", minimum=1),
        "duree_max_graine_min": Cle("nombre", positif=True),
        "fils_par_essai": Cle("entier", minimum=1),
    },
}

LISTES_CONTRAINTES = ("fixes", "liberer", "dessus_seulement", "dessous_seulement")

# [avance] -> (clé du moteur, facteur) (§5.4)
AVANCE_VERS_MOTEUR = {
    "conjointe_essais_par_piece": ("conjointe.essais_par_piece_mobile", None),
    "boucle": ("boucle.tours", None),
    "mouvements": ("recuit.mouvements_max", None),
    "iters_det": ("routage.iters_negociation", None),
    "duree_max_graine_min": ("campagne.duree_max_s", 60),
    "fils_par_essai": ("campagne.threads", None),
}

_BOOLEENS = {"oui": True, "non": False, "vrai": True, "faux": False, "1": True, "0": False,
             "true": True, "false": False, "yes": True, "no": False}

MODELE_INI = """\
; Paramètres du placeur-routeur
;
; Ce fichier se lit et se modifie avec le bloc-notes. Une ligne qui commence par « ; » est un commentaire.
; Chaque réglage s'écrit « nom = valeur ». Une valeur laissée vide ne change rien : le réglage vient alors d'un
; fichier plus faible (voir ci-dessous), sinon de sa valeur par défaut, indiquée entre parenthèses.
;  - oui ou non : oui, non, vrai, faux, 1 ou 0 ;
;  - nombres : 2,5 ou 2.5 ;
;  - listes : éléments séparés par des virgules (U1, J1, R1*) ;
;  - chemins : complets, ou relatifs au dossier de ce fichier ; des guillemets sont permis autour d'un chemin.
;
; Où mettre ce fichier (du plus faible au plus fort : un réglage plus fort remplace un réglage plus faible) :
;  1. %APPDATA%\\PlaceurRouteur\\parametres.ini : vos préférences, pour toutes les cartes ;
;  2. à côté de la carte, sous son nom (ma_carte.ini pour ma_carte.PcbDoc) : réglages de cette carte ;
;  3. n'importe où, désigné par l'option --parametres ;
;  4. les options de la ligne de commande et les choix de la fenêtre l'emportent sur tous les fichiers.
; Les fichiers 1 et 2 ne sont lus que par PlaceurRouteur.exe et placeur.exe (pas en mode développeur).
; Ce modèle lui-même n'est jamais lu : en copier le contenu dans l'un de ces fichiers.

[carte]
; carte à traiter : fichier .PcbDoc (Altium) ou .kicad_pcb (KiCad)
fichier =
; fichier de contraintes (facultatif ; placeur.exe --init CARTE en écrit un modèle)
contraintes =
; description des convertisseurs, si la déduction automatique ne suffit pas (facultatif)
config_convertisseurs =

[sortie]
; dossier où créer les résultats ; vide : dossier <nom>_resultats à côté de la carte
dossier =
; ouvrir le dossier des résultats à la fin du calcul (fenêtre) : oui ou non (défaut : oui)
ouvrir_a_la_fin =
; essais dont tous les fichiers sont gardés : meilleure ou toutes (défaut : meilleure)
garder_graines =

[calcul]
; niveau de qualité : apercu, rapide, normal ou soigne (défaut : normal)
;   apercu : 1 essai, 15 min au plus ; rapide : 4 essais, 1 h ; normal : 8 essais, 3 h ; soigne : 16 essais, 8 h
qualite =
; nombre d'essais (graines) ; 0 : celui du niveau (défaut : 0)
essais =
; durée maximale du calcul, en minutes ; 0 : celle du niveau (défaut : 0)
duree_max_min =
; arrêter les lancements dès que ce nombre d'essais sont réussis ; 0 : faire tous les essais (défaut : 0)
valides_voulues =
; numéro de la première graine ; les suivantes se suivent (même graine, même résultat) (défaut : 1)
graine_depart =
; processeurs utilisés ; 0 : tous sauf un (défaut : 0)
processeurs =
; oui : calcul en priorité normale (le PC répond moins vite) ; non : priorité basse, conseillée (défaut : non)
priorite_normale =
; mode de routage : auto (celui du moteur), manhattan, plus_court, diagonale_45 ou comparer (les graines
;   se partagent les modes, la meilleure est gardée) ; ignoré si le moteur ne le connaît pas (défaut : auto)
mode_routage =

[pcbdoc]
; produire la carte placée et routée (.PcbDoc ou .kicad_pcb, comme la carte d'entrée) (défaut : oui)
produire =
; sans essai réussi, produire quand même la meilleure carte, à terminer à la main (défaut : oui)
produire_si_invalide =
; placer la sérigraphie (désignateurs) (défaut : oui)
serigraphie =
; ajouter des vias de couture entre les plans de masse (défaut : oui)
vias_couture =

[contraintes]
; Complète le fichier de contraintes ; vide : rien à changer (défaut : valeurs du fichier de contraintes).
; Désignateurs séparés par des virgules.
; pièces laissées à leur place
fixes =
; pièces rendues mobiles, même si elles seraient fixées automatiquement
liberer =
; pièces permises seulement dessus, ou seulement dessous
dessus_seulement =
dessous_seulement =
; Les réglages suivants remplacent ceux du fichier de contraintes (le rapport le signale).
; faces des pièces mobiles : auto, libres ou actuelle
faces_par_defaut =
; fixer les connecteurs posés dans le contour (oui ou non)
fixer_suggestions =
; fixer les pièces verrouillées dans le fichier de la carte (oui ou non)
fixer_verrouilles =
; pièce de hauteur inconnue : face_entree (elle reste sur sa face) ou basse
hauteur_inconnue =
; marges et gardes en millimètres : bord de la carte, découpes
marge_bord_mm =
garde_bord_mm =
marge_decoupe_mm =
garde_decoupe_mm =
; seuil de pas fin et grille de placement, en millimètres
pas_fin_mm =
grille_placement_mm =

[avance]
; Réglages pour spécialistes ; vide : valeur du niveau de qualité.
; fichier de paramètres du moteur (parametres_moteur.json)
parametres_moteur =
; essais de la phase conjointe, par pièce mobile
conjointe_essais_par_piece =
; tours de la boucle placement-routage
boucle =
; mouvements maximaux du recuit
mouvements =
; itérations de négociation du routage détaillé
iters_det =
; durée maximale d'un essai, en minutes
duree_max_graine_min =
; fils de calcul par essai (1 conseillé : le résultat n'en dépend pas)
fils_par_essai =
"""


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, espaces et tirets changés en « _ » : « Soigné » → « soigne »."""
    t = unicodedata.normalize("NFKD", texte.strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[\s\-]+", "_", t)


def _proche(mot: str, choix) -> str | None:
    r = difflib.get_close_matches(mot, list(choix), n=1, cutoff=0.6)
    return r[0] if r else None


# ------------------------------------------------------------------------------------------------ analyse d'une ligne

_SECTION = re.compile(r"^\s*\[([^\]]*)\]\s*(?:[;#].*)?$")


def _decouper(ligne: str):
    """Analyse une ligne de .ini. Rend ("vide",), ("section", nom brut), ("cle", clé brute, début, fin, valeur)
    — début et fin délimitent la valeur dans la ligne, guillemets compris — ou ("illisible",)."""
    s = ligne.strip()
    if not s or s[0] in ";#":
        return ("vide",)
    m = _SECTION.match(ligne)
    if m:
        return ("section", m.group(1).strip())
    if "=" not in ligne:
        return ("illisible",)
    i = ligne.index("=")
    cle = ligne[:i].strip()
    if not cle:
        return ("illisible",)
    debut = i + 1
    while debut < len(ligne) and ligne[debut] in " \t":
        debut += 1
    reste = ligne[debut:]
    if reste.startswith('"'):
        j = reste.find('"', 1)
        if j > 0:
            apres = reste[j + 1:]
            if not apres.strip() or apres.lstrip()[0] in ";#":
                return ("cle", cle, debut, debut + j + 1, reste[1:j])
    if reste[:1] in (";", "#"):
        return ("cle", cle, debut, debut, "")
    m = re.search(r"\s[;#]", reste)
    fin = debut + (m.start() if m else len(reste))
    while fin > debut and ligne[fin - 1] in " \t":
        fin -= 1
    return ("cle", cle, debut, fin, ligne[debut:fin])


# ------------------------------------------------------------------------------------------------ conversions

def _nom(section: str, cle: str) -> str:
    return f"[{section}] {cle}"


def _verifier_bornes(section: str, cle: str, valeur, brut: str):
    c = SCHEMA[section][cle]
    if c.positif and valeur <= 0:
        raise ErreurParametres(f"{_nom(section, cle)} : {brut} doit être strictement positif")
    if c.minimum is not None and valeur < c.minimum:
        if c.minimum == 0:
            raise ErreurParametres(f"{_nom(section, cle)} : {brut} doit être positif ou nul")
        raise ErreurParametres(f"{_nom(section, cle)} : {brut} doit être au moins {c.minimum}")
    return valeur


_ENTIER = re.compile(r"[+-]?\d{1,3}(?:[   _]\d{3})+|[+-]?\d+")
_ENTIER_SCIENTIFIQUE = re.compile(r"[+-]?\d+(?:[.,]\d+)?[eE][+-]?\d{1,3}")   # 1e8, 2,5e7 (contrat : 1·10⁸)
_ENTIER_MAX = 10 ** 18


def _entier(v: str):
    """Entier écrit `100 000`, `100_000`, `1e8` ou `2,5e7` ; None si le texte n'est pas un entier exact."""
    if _ENTIER.fullmatch(v):
        return int(re.sub(r"[   _]", "", v))
    if _ENTIER_SCIENTIFIQUE.fullmatch(v):
        d = decimal.Decimal(v.replace(",", "."))
        if d == d.to_integral_value() and abs(d) <= _ENTIER_MAX:
            return int(d)
    return None
_NOMBRE = re.compile(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?")


def convertir(section: str, cle: str, brut, dossier: str | None = None):
    """Valeur typée d'une clé connue. `brut` : texte lu dans un .ini (ou donné par la fenêtre), ou valeur Python
    déjà typée (option de la ligne de commande). Vide ⇒ défaut de la clé. `dossier` : base des chemins relatifs
    (dossier du .ini) ; None : dossier courant. Valeur invalide ⇒ ErreurParametres."""
    c = SCHEMA[section][cle]
    nom = _nom(section, cle)
    if brut is None:
        return copy.deepcopy(c.defaut)
    if not isinstance(brut, str):
        return _valider_type(section, cle, brut)
    v = brut.strip()
    if v == "":
        return copy.deepcopy(c.defaut)
    if c.type == "chemin":
        if len(v) >= 2 and v[0] == v[-1] == '"':
            v = v[1:-1].strip()
        if not os.path.isabs(v):
            v = os.path.join(dossier, v) if dossier else os.path.abspath(v)
        return os.path.normpath(v)
    if c.type == "bool":
        n = normaliser(v)
        if n not in _BOOLEENS:
            raise ErreurParametres(f"{nom} : '{v}' n'est ni oui ni non")
        return _BOOLEENS[n]
    if c.type == "entier":
        n = _entier(v)
        if n is None:
            raise ErreurParametres(f"{nom} : '{v}' n'est pas un nombre entier")
        return _verifier_bornes(section, cle, n, v)
    if c.type == "nombre":
        if not _NOMBRE.fullmatch(v):
            raise ErreurParametres(f"{nom} : '{v}' n'est pas un nombre")
        x = float(v.replace(",", "."))
        if not math.isfinite(x):
            raise ErreurParametres(f"{nom} : '{v}' n'est pas un nombre")
        return _verifier_bornes(section, cle, x, v)
    if c.type == "liste":
        elements = [e.strip() for e in v.split(",")]
        elements = [e for e in elements if e]
        return elements or copy.deepcopy(c.defaut)
    n = normaliser(v)                                   # valeur énumérée
    if n not in c.type:
        raise ErreurParametres(f"{nom} : '{v}' n'est pas une valeur permise ({', '.join(c.type)})")
    return n


def _valider_type(section: str, cle: str, valeur):
    """Valeur déjà typée (option) : même contrôle que pour un texte."""
    c = SCHEMA[section][cle]
    nom = _nom(section, cle)
    if c.type == "chemin":
        if not isinstance(valeur, (str, os.PathLike)):
            raise ErreurParametres(f"{nom} : chemin attendu")
        return os.path.normpath(os.path.abspath(os.fspath(valeur)))
    if c.type == "bool":
        if not isinstance(valeur, bool):
            raise ErreurParametres(f"{nom} : '{valeur}' n'est ni oui ni non")
        return valeur
    if c.type == "entier":
        if isinstance(valeur, bool) or not isinstance(valeur, int):
            raise ErreurParametres(f"{nom} : '{valeur}' n'est pas un nombre entier")
        return _verifier_bornes(section, cle, valeur, str(valeur))
    if c.type == "nombre":
        if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur):
            raise ErreurParametres(f"{nom} : '{valeur}' n'est pas un nombre")
        return _verifier_bornes(section, cle, float(valeur), str(valeur))
    if c.type == "liste":
        if not isinstance(valeur, (list, tuple)) or not all(isinstance(e, str) for e in valeur):
            raise ErreurParametres(f"{nom} : liste de textes attendue")
        elements = [e.strip() for e in valeur if e.strip()]
        return elements or copy.deepcopy(c.defaut)
    raise ErreurParametres(f"{nom} : '{valeur}' n'est pas une valeur permise ({', '.join(c.type)})")


def formater(section: str, cle: str, valeur, dossier: str | None = None) -> str:
    """Texte d'une valeur pour un .ini (inverse de `convertir`). Chemin : relatif à `dossier` quand c'est
    possible ; `dossier` None : écrit tel quel. Guillemets quand la valeur pourrait se lire autrement."""
    c = SCHEMA[section][cle]
    if valeur is None:
        return ""
    if c.type == "bool":
        return "oui" if valeur else "non"
    if c.type == "entier":
        return str(int(valeur))
    if c.type == "nombre":
        x = float(valeur)
        return str(int(x)) if x.is_integer() and abs(x) < 1e15 else repr(x).replace(".", ",")
    if c.type == "liste":
        return ", ".join(valeur)
    texte = str(valeur)
    if c.type == "chemin" and dossier is not None and os.path.isabs(texte):
        try:
            rel = os.path.relpath(texte, dossier)
            if not rel.startswith(".."):
                texte = rel
        except ValueError:                               # autre lecteur
            pass
    if texte != texte.strip() or re.search(r"\s[;#]", texte) or texte[:1] in ('"', ";", "#"):
        texte = f'"{texte}"'
    return texte


# ------------------------------------------------------------------------------------------------ lecture

class FichierIni:
    """Résultat de la lecture d'un .ini : valeurs typées des clés présentes, avertissements, clés inconnues."""

    def __init__(self, nom: str):
        self.nom = nom
        self.valeurs: dict = {}            # (section, clé) -> valeur typée
        self.lignes: dict = {}             # (section, clé) -> numéro de ligne
        self.avertissements: list[str] = []
        self.cles_inconnues: list[str] = []
        self.encodage = "utf-8"


def decoder(octets: bytes, nom: str) -> tuple[str, str, list[str]]:
    """Texte d'un fichier : UTF-8 (BOM retiré), sinon cp1252, signalé. Rend (texte, encodage, avertissements)."""
    if octets.startswith(b"\xef\xbb\xbf"):
        octets = octets[3:]
    try:
        return octets.decode("utf-8"), "utf-8", []
    except UnicodeDecodeError:
        return (octets.decode("cp1252", errors="replace"), "cp1252",
                [f"{nom} : fichier lu en Windows-1252 (et non en UTF-8) ; l'enregistrer en UTF-8"])


def _lisible(texte: str) -> str:
    """Texte cité dans un message : caractères de contrôle remplacés par « ? »."""
    return "".join(c if c.isprintable() else "?" for c in texte)


def analyser_ini(texte: str, nom: str = "parametres.ini", dossier: str | None = None) -> FichierIni:
    """Analyse le texte d'un .ini. `nom` (nom de fichier seul) figure dans les messages ; `dossier` est la base
    des chemins relatifs. Une clé dont la valeur est vide n'est pas renseignée (absente de `valeurs`) : les
    fichiers se fusionnent clé par clé (§5.4). Valeur invalide, ligne illisible ⇒ ErreurParametres (avec fichier
    et ligne)."""
    res = FichierIni(nom)
    if "\x00" in texte:
        raise ErreurParametres(f"fichier de paramètres illisible : {nom} (ce n'est pas un fichier texte)", nom)
    section = None                       # section connue courante ; "" : section inconnue (ignorée)
    for n, ligne in enumerate(texte.splitlines(), start=1):
        if n == 1 and ligne.startswith("﻿"):
            ligne = ligne[1:]
        d = _decouper(ligne)
        if d[0] == "vide":
            continue
        if d[0] == "section":
            s = normaliser(d[1])
            if s in SCHEMA:
                section = s
            else:
                section = ""
                p = _proche(s, SCHEMA)
                res.avertissements.append(f"[{d[1]}] : section inconnue, ignorée"
                                          + (f" ; vouliez-vous dire [{p}] ?" if p else ""))
                res.cles_inconnues.append(f"[{d[1]}]")
            continue
        if d[0] == "illisible":
            raise ErreurParametres(f"ligne {n} : '{_lisible(ligne.strip()[:60])}' n'est pas de la forme "
                                   "« clé = valeur »", nom, n)
        _, cle_brute, _, _, valeur = d
        if section is None:
            raise ErreurParametres(f"ligne {n} : clé '{cle_brute}' hors de toute section", nom, n)
        if section == "":
            continue
        cle = normaliser(cle_brute)
        if cle not in SCHEMA[section]:
            p = _proche(cle, SCHEMA[section])
            res.avertissements.append(f"[{section}] {cle_brute} : clé inconnue, ignorée"
                                      + (f" ; vouliez-vous dire {p} ?" if p else ""))
            res.cles_inconnues.append(f"[{section}] {cle_brute}")
            continue
        if valeur.strip() == "":
            continue                     # valeur vide : non renseignée (un fichier plus faible ou le défaut décide)
        try:
            v = convertir(section, cle, valeur, dossier)
        except ErreurParametres as e:
            raise ErreurParametres(e.message, nom, n) from None
        if (section, cle) in res.valeurs:
            res.avertissements.append(f"[{section}] {cle} : clé répétée (lignes {res.lignes[(section, cle)]} "
                                      f"et {n}) ; la dernière valeur est gardée")
        res.valeurs[(section, cle)] = v
        res.lignes[(section, cle)] = n
    return res


def lire_ini(chemin: str) -> FichierIni:
    """Lit un .ini (BOM, CRLF, repli cp1252 signalé). Fichier absent ou illisible ⇒ ErreurParametres."""
    nom = os.path.basename(os.path.normpath(chemin))
    if os.path.isdir(chemin):
        raise ErreurParametres(f"fichier de paramètres attendu : {nom} est un dossier", nom)
    try:
        with open(chemin, "rb") as f:
            octets = f.read()
    except FileNotFoundError:
        raise ErreurParametres(f"fichier de paramètres introuvable : {nom}", nom) from None
    except PermissionError:
        raise ErreurParametres(f"fichier de paramètres illisible : {nom} (accès refusé)", nom) from None
    except OSError as e:
        raise ErreurParametres(f"fichier de paramètres illisible : {nom} (erreur de lecture n° {e.errno})",
                               nom) from None
    texte, encodage, avert = decoder(octets, nom)
    res = analyser_ini(texte, nom, os.path.dirname(os.path.abspath(chemin)))
    res.encodage = encodage
    res.avertissements[:0] = avert
    return res


# ------------------------------------------------------------------------------------------------ priorités (§5.4)

class Reglages:
    """Valeurs effectives de toutes les clés de §5.4, avec leur origine, les fichiers lus et les avertissements."""

    def __init__(self):
        self.valeurs = {s: {k: copy.deepcopy(c.defaut) for k, c in cles.items()} for s, cles in SCHEMA.items()}
        self.origines: dict = {}             # (section, clé) -> nom du fichier ou « options »
        self.fichiers_lus: list[str] = []    # noms seuls, dans l'ordre de lecture
        self.chemins_lus: list[str] = []     # chemins complets (jamais écrits dans une sortie)
        self.avertissements: list[str] = []
        self.cles_inconnues: list[str] = []

    def valeur(self, section: str, cle: str):
        return self.valeurs[section][cle]

    def origine(self, section: str, cle: str) -> str:
        return self.origines.get((section, cle), ORIGINE_DEFAUT)

    def appliquer(self, valeurs: dict, origine: str) -> None:
        for (s, k), v in valeurs.items():
            self.valeurs[s][k] = copy.deepcopy(v)
            self.origines[(s, k)] = origine

    @property
    def qualite(self) -> str:
        return self.valeurs["calcul"]["qualite"]

    def essais(self) -> int:
        """Nombre de graines : [calcul] essais, ou celui du niveau si 0."""
        return self.valeurs["calcul"]["essais"] or NIVEAUX[self.qualite]["essais"]

    def duree_max_min(self) -> int:
        """Durée maximale du calcul en minutes : [calcul] duree_max_min, ou celle du niveau si 0."""
        return self.valeurs["calcul"]["duree_max_min"] or NIVEAUX[self.qualite]["duree_max_min"]

    def processeurs(self, coeurs: int | None = None) -> int:
        """Processeurs utilisés : [calcul] processeurs, ou cœurs logiques − 1 (au moins 1) si 0."""
        n = self.valeurs["calcul"]["processeurs"]
        if n:
            return n
        return max(1, (coeurs or os.cpu_count() or 1) - 1)

    def avance_actives(self) -> dict:
        """Clés [avance] renseignées (affichées par la fenêtre et le journal, §5.4)."""
        return {k: v for k, v in self.valeurs["avance"].items() if v is not None}


def ini_utilisateur() -> str | None:
    """%APPDATA%\\PlaceurRouteur\\parametres.ini (préférences de l'utilisateur), ou None sans %APPDATA%."""
    appdata = chemins.dossier_connu("appdata")
    return os.path.join(appdata, NOM_DOSSIER_UTILISATEUR, NOM_INI_UTILISATEUR) if appdata else None


def ini_projet(carte: str) -> str:
    """<dossier de la carte>\\<nom>.ini (fichier de projet, écrit par --init et « Enregistrer les paramètres »)."""
    return os.path.splitext(os.path.abspath(carte))[0] + ".ini"


def _cle_pointee(nom: str) -> tuple[str, str]:
    if "." not in nom:
        raise ErreurParametres(f"option {nom!r} : forme « section.clé » attendue")
    s, k = nom.split(".", 1)
    s, k = normaliser(s), normaliser(k)
    if s not in SCHEMA or k not in SCHEMA[s]:
        raise ErreurParametres(f"option {nom!r} inconnue")
    return s, k


def charger(parametres: str | None = None, options: dict | None = None, carte: str | None = None,
            implicites: bool | None = None) -> Reglages:
    """Réglages effectifs (§5.4) : défauts < %APPDATA%\\PlaceurRouteur\\parametres.ini < <nom de la carte>.ini <
    `parametres` (--parametres) < `options` ({"section.clé": valeur}, ligne de commande ou fenêtre).

    `carte` : carte choisie (option CARTE) ; à défaut, [carte] fichier de `options`, puis de --parametres, puis
    du fichier des préférences. `implicites` : lire les fichiers implicites (défaut : en mode figé seulement).
    """
    r = Reglages()
    opts = {}
    for nom, v in (options or {}).items():
        s, k = _cle_pointee(nom)
        try:
            opts[(s, k)] = convertir(s, k, v)
        except ErreurParametres as e:
            raise ErreurParametres(e.message, "options") from None
    explicite = lire_ini(parametres) if parametres else None
    if implicites is None:
        implicites = chemins.est_fige()          # §0.2-1 : jamais en mode source ni en CI
    lus: list[tuple[str, FichierIni]] = []
    vus = set()
    if implicites:
        utilisateur = ini_utilisateur()
        if utilisateur and os.path.isfile(utilisateur):
            lus.append((utilisateur, lire_ini(utilisateur)))
        if carte is None:
            for source in ([opts] + ([explicite.valeurs] if explicite else []) + [f.valeurs for _, f in lus]):
                if source.get(("carte", "fichier")):
                    carte = source[("carte", "fichier")]
                    break
        if carte and os.path.isfile(ini_projet(carte)):
            lus.append((ini_projet(carte), lire_ini(ini_projet(carte))))
    if explicite is not None:
        lus.append((parametres, explicite))
    for chemin, f in lus:
        cle = os.path.normcase(os.path.abspath(chemin))
        if cle in vus:
            continue
        vus.add(cle)
        r.appliquer(f.valeurs, f.nom)
        r.fichiers_lus.append(f.nom)
        r.chemins_lus.append(os.path.abspath(chemin))
        r.avertissements.extend(f.avertissements)
        r.cles_inconnues.extend(f"{f.nom} : {c}" for c in f.cles_inconnues)
    r.appliquer(opts, ORIGINE_OPTIONS)
    return r


# ------------------------------------------------------------------------------------------------ écriture

def mettre_a_jour_texte(texte: str, valeurs: dict, dossier: str | None = None) -> str:
    """Remplace, ligne à ligne, les valeurs des clés de `valeurs` ({(section, clé): valeur} ou
    {"section.clé": valeur}) dans le texte d'un .ini, sans toucher aux commentaires ni à l'ordre ; les clés
    absentes sont ajoutées à la fin de leur section (ou d'une section ajoutée). Rend le texte (fins de ligne LF)."""
    a_ecrire = {}
    for cle, v in valeurs.items():
        s, k = cle if isinstance(cle, tuple) else _cle_pointee(cle)
        a_ecrire[(s, k)] = formater(s, k, v, dossier)
    lignes = texte.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lignes and lignes[0].startswith("﻿"):
        lignes[0] = lignes[0][1:]
    if lignes and lignes[-1] == "":
        lignes.pop()
    faites = set()
    fin_section: dict = {}               # section -> indice de la dernière ligne non vide de la section
    section = None
    for i, ligne in enumerate(lignes):
        d = _decouper(ligne)
        if d[0] == "section":
            s = normaliser(d[1])
            section = s if s in SCHEMA else ""
            if section:
                fin_section[section] = i
            continue
        if section and ligne.strip():
            fin_section[section] = i
        if d[0] != "cle" or not section:
            continue
        _, cle_brute, debut, fin, ancienne = d
        k = normaliser(cle_brute)
        if (section, k) not in a_ecrire:
            continue
        nouveau = a_ecrire[(section, k)]
        if ancienne == "" and debut == fin and nouveau:
            if debut < len(ligne):                           # commentaire collé : garder un espace
                nouveau += " "
            if ligne[debut - 1:debut] == "=":
                nouveau = " " + nouveau
        lignes[i] = ligne[:debut] + nouveau + ligne[fin:]
        if not nouveau and not ligne[fin:].strip():
            lignes[i] = lignes[i].rstrip()
        faites.add((section, k))
    ajouts: dict = {}
    for (s, k), t in a_ecrire.items():
        if (s, k) not in faites:
            ajouts.setdefault(s, []).append(f"{k} = {t}".rstrip())
    for s in sorted(ajouts, key=lambda x: -fin_section.get(x, -1)):
        if s in fin_section:
            i = fin_section[s] + 1
            lignes[i:i] = ajouts[s]
    for s in SCHEMA:                                         # sections absentes, dans l'ordre du schéma
        if s in ajouts and s not in fin_section:
            if lignes and lignes[-1].strip():
                lignes.append("")
            lignes.append(f"[{s}]")
            lignes.extend(ajouts[s])
    return "\n".join(lignes) + "\n"


def enregistrer(chemin: str, valeurs: dict) -> None:
    """Enregistre `valeurs` dans le .ini `chemin` (bouton « Enregistrer les paramètres », `--init`) : le fichier
    existant est modifié ligne à ligne, ou créé depuis `MODELE_INI`, dont toutes les valeurs sont vides : seules
    les clés de `valeurs` y sont renseignées, les autres restent aux fichiers plus faibles (§5.4). L'appelant ne
    passe que les réglages choisis par l'utilisateur. UTF-8 avec BOM, CRLF, écriture atomique."""
    texte = MODELE_INI
    if os.path.isfile(chemin):
        with open(chemin, "rb") as f:
            texte = decoder(f.read(), os.path.basename(chemin))[0]
    dossier = os.path.dirname(os.path.abspath(chemin))
    chemins.ecrire_texte_bloc_notes(chemin, mettre_a_jour_texte(texte, valeurs, dossier))


def texte_parametres_utilises(reglages: Reglages, effectifs: dict | None = None) -> str:
    """Texte de `technique/parametres_utilises.ini` (§5.4, §0.3) : toutes les valeurs effectives, sur le plan de
    `MODELE_INI` ; les chemins réduits à leur nom de fichier, `.` pour la sortie. `effectifs` ({"section.clé":
    valeur}) remplace des valeurs résolues par l'orchestrateur (essais, durée, processeurs du niveau)."""
    valeurs = {}
    for s, cles in SCHEMA.items():
        for k, c in cles.items():
            v = reglages.valeurs[s][k]
            if c.type == "chemin" and v is not None:
                v = "." if (s, k) == ("sortie", "dossier") else os.path.basename(v)
            valeurs[(s, k)] = v
    for cle, v in (effectifs or {}).items():
        valeurs[_cle_pointee(cle)] = v
    if valeurs[("sortie", "dossier")] is None:
        valeurs[("sortie", "dossier")] = "."
    entete = ("; Valeurs effectives du calcul (copie ; ce fichier n'est relu que s'il est désigné par --parametres).\n"
              f"; Fichiers de paramètres lus : {', '.join(reglages.fichiers_lus) or 'aucun'}.\n"
              "; Les fichiers sont désignés par leur nom ; « . » est le dossier du calcul.\n;\n")
    return entete + mettre_a_jour_texte(MODELE_INI, valeurs, None)


def ecrire_parametres_utilises(chemin: str, reglages: Reglages, effectifs: dict | None = None) -> None:
    chemins.ecrire_texte_bloc_notes(chemin, texte_parametres_utilises(reglages, effectifs))


# ------------------------------------------------------------------------------------------------ moteur (§2.6)

_DEFAUTS: dict = {}


def defauts_moteur(qualite: str, commande: list[str], delai_s: float = 120) -> dict:
    """Paramètres du moteur du niveau `qualite`, obtenus de `<commande> parametres-defaut --qualite <qualite>`
    (source unique, §2.6). `commande` : commande du moteur sans sous-commande (chemins.commande_moteur, ou faux
    moteur des tests). Moteur absent ou en erreur ⇒ ErreurParametres."""
    if qualite not in QUALITES:
        raise ErreurParametres(f"qualité inconnue : {qualite!r} ({', '.join(QUALITES)})")
    cle = (tuple(commande), qualite)
    if cle in _DEFAUTS:
        return copy.deepcopy(_DEFAUTS[cle])
    try:
        r = chemins.executer(list(commande) + ["parametres-defaut", "--qualite", qualite], delai_s=delai_s,
                             priorite_basse=False)
    except FileNotFoundError:
        raise ErreurParametres(chemins.MESSAGE_MOTEUR_ABSENT) from None
    except OSError as e:
        raise ErreurParametres(f"{chemins.MESSAGE_MOTEUR_ABSENT} ({e.strerror or 'lancement refusé'})") from None
    if r.returncode != 0:
        detail = chemins.anonymiser((r.stderr or r.stdout or "").strip()[-500:])
        raise ErreurParametres(f"le moteur refuse « parametres-defaut --qualite {qualite} » (code {r.returncode})"
                               + (f" : {detail}" if detail else ""))
    try:
        defauts = json.loads(r.stdout)
    except ValueError:
        raise ErreurParametres("le moteur a rendu des paramètres par défaut illisibles (JSON)") from None
    if not isinstance(defauts, dict) or defauts.get("format") != FORMAT_MOTEUR:
        raise ErreurParametres(f"le moteur n'a pas rendu le format {FORMAT_MOTEUR}")
    _DEFAUTS[cle] = defauts
    return copy.deepcopy(defauts)


def lire_json_manuel(chemin: str, quoi: str = "fichier") -> dict:
    """Lit un JSON écrit à la main (objet attendu ; UTF-8, BOM toléré). Erreur ⇒ ErreurParametres (nom seul)."""
    nom = os.path.basename(chemin)
    try:
        with open(chemin, "rb") as f:
            octets = f.read()
    except FileNotFoundError:
        raise ErreurParametres(f"{quoi} introuvable : {nom}", nom) from None
    except OSError as e:
        raise ErreurParametres(f"{quoi} illisible : {nom} ({e.strerror or 'erreur'})", nom) from None
    if octets.startswith(b"\xef\xbb\xbf"):
        octets = octets[3:]
    try:
        objet = json.loads(octets.decode("utf-8"))
    except UnicodeDecodeError:
        raise ErreurParametres(f"{nom} : fichier qui n'est pas en UTF-8", nom) from None
    except json.JSONDecodeError as e:
        raise ErreurParametres(f"{nom} : JSON illisible (ligne {e.lineno}, colonne {e.colno})", nom) from None
    if not isinstance(objet, dict):
        raise ErreurParametres(f"{nom} : un objet JSON est attendu", nom)
    return objet


def _genre(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "nombre"
    if isinstance(v, str):
        return "texte"
    if isinstance(v, list):
        return "liste"
    return "objet" if isinstance(v, dict) else "autre"


_ATTENDU = {"nombre": "un nombre est attendu", "bool": "true ou false est attendu", "texte": "un texte est attendu",
            "liste": "une liste est attendue", "objet": "un objet est attendu"}


def _null_permis(defauts: dict, parties: list[str]) -> bool:
    """null est permis là où les défauts le montrent (§2.6), y compris pour la même clé d'un autre rôle ou d'une
    autre sensibilité (`roles.commutation.couches` : null pour les autres rôles)."""
    v = defauts
    for p in parties:
        v = v.get(p) if isinstance(v, dict) else None
    if v is None:
        return True
    if len(parties) >= 3 and parties[0] in ("roles", "sensibilites") and isinstance(defauts.get(parties[0]), dict):
        return any(isinstance(x, dict) and parties[2] in x and x[parties[2]] is None
                   for x in defauts[parties[0]].values())
    return False


def _controler_valeur(defaut, valeur, chemin: str, racine: dict) -> None:
    gd, gv = _genre(defaut), _genre(valeur)
    if gv == gd or gd == "null" or (gd == "texte" and gv == "nombre"):   # « auto » ou un nombre
        return
    if gv == "null":
        if _null_permis(racine, chemin.split(".")):
            return
        raise ErreurParametres(f"{chemin} : null n'est pas permis ici")
    raise ErreurParametres(f"{chemin} : {_ATTENDU.get(gd, 'valeur refusée')}")


def fusionner_moteur(base: dict, ajout: dict, chemin: str = "", racine: dict | None = None,
                     changees: list | None = None) -> dict:
    """Fusion profonde de `ajout` sur `base` (§2.6) : objet par objet ; tableau et scalaire remplacés ; clés `_*`
    ignorées à tout niveau ; clé inconnue ⇒ ErreurParametres « chemin : clé inconnue ; vouliez-vous dire x ? ».
    `changees` reçoit les chemins des feuilles remplacées. Rend un nouvel objet ; `base` n'est pas modifié."""
    racine = base if racine is None else racine
    if not isinstance(ajout, dict):
        raise ErreurParametres(f"{chemin or 'parametres'} : un objet est attendu")
    res = copy.deepcopy(base)
    for k, v in ajout.items():
        if isinstance(k, str) and k.startswith("_"):
            continue
        p = f"{chemin}.{k}" if chemin else str(k)
        if k not in base:
            proche = _proche(str(k), [x for x in base if not str(x).startswith("_")])
            raise ErreurParametres(f"{p} : clé inconnue" + (f" ; vouliez-vous dire {proche} ?" if proche else ""))
        d = base[k]
        if not chemin and k == "format":
            if v != d:
                raise ErreurParametres(f"format {v} non pris en charge (attendu {d})")
            continue
        if isinstance(d, dict):
            if not isinstance(v, dict):
                if v is None and _null_permis(racine, p.split(".")):
                    res[k] = None
                    continue
                raise ErreurParametres(f"{p} : un objet est attendu")
            res[k] = fusionner_moteur(d, v, p, racine, changees)
            continue
        _controler_valeur(d, v, p, racine)
        res[k] = _sans_commentaires(v)
        if changees is not None:
            changees.append(p)
    return res


def _sans_commentaires(v):
    if isinstance(v, dict):
        return {k: _sans_commentaires(x) for k, x in v.items() if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(v, list):
        return [_sans_commentaires(x) for x in v]
    return copy.deepcopy(v)


def _poser(params: dict, chemin: str, valeur, racine: dict) -> None:
    parties = chemin.split(".")
    v = params
    for p in parties[:-1]:
        if not isinstance(v, dict) or p not in v or not isinstance(v[p], dict):
            raise ErreurParametres(f"{chemin} : clé inconnue")
        v = v[p]
    if parties[-1] not in v:
        proche = _proche(parties[-1], v)
        raise ErreurParametres(f"{chemin} : clé inconnue" + (f" ; vouliez-vous dire {proche} ?" if proche else ""))
    _controler_valeur(v[parties[-1]], valeur, chemin, racine)
    v[parties[-1]] = copy.deepcopy(valeur)


def _nombre_moteur(defaut, valeur):
    """Valeur numérique au type de la valeur par défaut (entier si le défaut est entier et la valeur entière)."""
    if isinstance(defaut, int) and not isinstance(defaut, bool) and float(valeur).is_integer():
        return int(valeur)
    return float(valeur) if isinstance(defaut, float) else valeur


def parametres_moteur(defauts: dict, reglages: Reglages | None = None, options_moteur: dict | None = None,
                      fichier: str | None = None) -> tuple[dict, dict]:
    """Paramètres complets passés au moteur (§5.4) : `defauts` (du niveau, `defauts_moteur`) < fichier
    --parametres-moteur ([avance] parametres_moteur, ou `fichier`) < clés [avance] < `options_moteur`
    ({"campagne.threads": 1, …}, imposées par l'orchestrateur ou la fenêtre).

    Rend (paramètres, origines) ; origines : {chemin de la clé : source} pour chaque clé changée.
    Toute clé produite existe dans `defauts` (sinon ErreurParametres)."""
    if not isinstance(defauts, dict) or defauts.get("format") != FORMAT_MOTEUR:
        raise ErreurParametres(f"paramètres par défaut du moteur au format inattendu (attendu {FORMAT_MOTEUR})")
    origines: dict = {}
    params = copy.deepcopy(defauts)
    if fichier is None and reglages is not None:
        fichier = reglages.valeur("avance", "parametres_moteur")
    if fichier:
        utilisateur = lire_json_manuel(fichier, "fichier de paramètres du moteur")
        changees: list = []
        try:
            params = fusionner_moteur(params, utilisateur, changees=changees)
        except ErreurParametres as e:
            raise ErreurParametres(e.message, os.path.basename(fichier)) from None
        origines.update({c: os.path.basename(fichier) for c in changees})
    if reglages is not None:
        for cle, v in reglages.avance_actives().items():
            if cle not in AVANCE_VERS_MOTEUR:
                continue
            chemin, facteur = AVANCE_VERS_MOTEUR[cle]
            actuel = _lire(params, chemin)
            valeur = _nombre_moteur(actuel, round(v * facteur, 6) if facteur else v)   # 0,71 min → 42,6 s
            _poser(params, chemin, valeur, defauts)
            origines[chemin] = f"[avance] {cle}"
    for chemin, v in (options_moteur or {}).items():
        _poser(params, chemin, v, defauts)
        origines[chemin] = ORIGINE_OPTIONS
    return params, origines


def _lire(params: dict, chemin: str):
    v = params
    for p in chemin.split("."):
        if not isinstance(v, dict) or p not in v:
            raise ErreurParametres(f"{chemin} : clé inconnue")
        v = v[p]
    return v


def cles_feuilles(objet: dict, prefixe: str = "") -> set[str]:
    """Chemins de toutes les clés d'un objet JSON (objets parcourus, tableaux et scalaires comme feuilles)."""
    res = set()
    for k, v in objet.items():
        p = f"{prefixe}.{k}" if prefixe else k
        res.add(p)
        if isinstance(v, dict):
            res |= cles_feuilles(v, p)
    return res


# ------------------------------------------------------------------------------------------------ [contraintes]

def fusionner_contraintes(contraintes: dict | None, reglages: Reglages, nom: str = "contraintes.json"
                          ) -> tuple[dict, list[str]]:
    """Contenu de `technique/contraintes_effectives.json` (§2.9) : `contraintes` (contraintes.json déjà lu et
    validé, format contraintes/2 ; None : aucun fichier) complété par [contraintes] du .ini. Listes ajoutées
    (sans doublon), scalaires remplacés, avec un avertissement quand la valeur du fichier change.
    Les clés en `_mm` sont des millimètres, quelle que soit l'unité du fichier (`unite` ne vaut que pour les
    coordonnées). Rend (contraintes, avertissements)."""
    res = copy.deepcopy(contraintes) if contraintes else {"format": FORMAT_CONTRAINTES}
    avert: list[str] = []
    section = reglages.valeurs["contraintes"]
    for k in LISTES_CONTRAINTES:
        ajout = section.get(k)
        if not ajout:
            continue
        existant = res.get(k, [])
        if not isinstance(existant, list):
            raise ErreurParametres(f"{nom} : {k} doit être une liste")
        res[k] = list(existant) + [x for x in ajout if x not in existant]
    for k, c in SCHEMA["contraintes"].items():
        if k in LISTES_CONTRAINTES or section.get(k) is None:
            continue
        v = section[k]
        if k in res and res[k] != v:
            avert.append(f"[contraintes] {k} = {formater('contraintes', k, v)} remplace la valeur "
                         f"{json.dumps(res[k], ensure_ascii=False)} de {nom}")
        res[k] = v
    return res, avert


# ------------------------------------------------------------------------------------------------ durées (§5.3)

# Référence : METROID, machine de la CI (4 fils), niveau normal, campagne run_4 de 9b5b5a3 (moyenne 2 722 s par
# graine ; parts par phase de l'étude, §1.4) et fumee_7 (recuit sans plafond atteint à 2,02·10⁷ mouvements).
# Taille du problème de METROID : contour de 164 × 99 mm au pas de 0,1 mm, 3 couches de routage (estimation ;
# remplacée par la sortie d'`estimer` du vrai moteur en É9). Score de référence : `etalonner` sur la CI, inconnu
# avant le jalon A1 (facteur de vitesse 1 en attendant). Valeurs à recaler sur les mesures (critère D9).
# C'est l'étalon du modèle de durée (les durées d'une carte sont proportionnelles à sa taille rapportée à celle-ci),
# jamais une valeur de repli : une estimation sans taille est refusée (EstimationInvalide).
class EstimationInvalide(ValueError):
    """Sortie d'`estimer` (estimation/1) sans les champs nécessaires à la prévision de durée."""


REFERENCE_DUREE = {
    "pieces_mobiles": 314,
    "cellules_detail": 1_623_600,
    "couches_routage": 3,
    "score": None,
    "mouvements_recuit": 2.0e7,
    "iters_negociation": 40,
}
COEFFICIENTS_DUREE = {
    "fixe_s": 1.0,                # chargement, placement initial
    "recuit_s": 348.0,            # 12,8 % de 2 722 s, pour 314 pièces mobiles
    "routage_passe_s": 290.6,     # (premier routage 6,0 % + tours 25,8 % + réparation 21,6 %) / 5 passes
    "recuit_local_tour_s": 24.5,  # recuit local de la boucle : 3,6 % / 4 tours
    "conjointe_essai_s": 0.3036,  # 21,0 % de 2 722 s pour 1 884 essais
    "finition_s": 19.0,           # 0,7 %
    "divers": 1.093,              # 8,5 % non ventilés (graine 3014 arrêtée sans temps par phase)
}


def facteur_vitesse(score: float | None) -> float:
    """Rapport score de référence / score mesuré (≥ 1 pour un PC plus lent ; 1 si l'un des deux est inconnu)."""
    ref = REFERENCE_DUREE["score"]
    if not ref or not score or score <= 0:
        return 1.0
    return ref / score


def plafond_graine_s(params: dict, score: float | None = None) -> float:
    """Plafond par graine passé au moteur (§5.3) : campagne.duree_max_s × max(1 ; score de référence / score)."""
    return params["campagne"]["duree_max_s"] * max(1.0, facteur_vitesse(score))


def duree_graine_prevue(estimation: dict, params: dict, score: float | None = None) -> dict:
    """Durée prévue d'une graine, par phase (s), d'après `estimer` (estimation/1), les paramètres du moteur et
    `etalonner` (score). Rend {"phases": {…}, "total_s", "plafond_s"} ; le total ne dépasse pas le plafond.
    `cellules_detail`, `couches_routage` et `pieces_mobiles` sont exigés (sinon EstimationInvalide) ; sans
    `essais_conjointe`, les essais sont déduits des paramètres et des pièces mobiles de la carte."""
    ref, k = REFERENCE_DUREE, COEFFICIENTS_DUREE
    for champ, minimum in (("cellules_detail", 1), ("couches_routage", 1), ("pieces_mobiles", 0)):
        v = estimation.get(champ) if isinstance(estimation, dict) else None
        if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
            raise EstimationInvalide(f"estimation du moteur incomplète : {champ} absent ou invalide ({v!r})")
    cellules = estimation["cellules_detail"] * estimation["couches_routage"]
    taille = cellules / (ref["cellules_detail"] * ref["couches_routage"])
    pieces = estimation["pieces_mobiles"]
    recuit = params["recuit"]
    tours = params["boucle"]["tours"]
    conj = params["conjointe"]
    essais = estimation.get("essais_conjointe")
    if essais is None:
        essais = conj["essais"] if conj.get("essais") is not None else \
            math.floor(conj["essais_par_piece_mobile"] * pieces + 0.5)
    vitesse = facteur_vitesse(score)
    passe = k["routage_passe_s"] * taille * params["routage"]["iters_negociation"] / ref["iters_negociation"]
    phases = {
        "chargement": k["fixe_s"],
        "recuit": k["recuit_s"] * pieces / ref["pieces_mobiles"] * min(1.0, recuit["mouvements_max"] /
                                                                       ref["mouvements_recuit"]),
        "routage_detaille": passe,
        "boucle": tours * (passe + k["recuit_local_tour_s"] * taille),
        "conjointe": min(essais * k["conjointe_essai_s"] * taille, conj["plafond_s"] or 0.0),
        "finition": k["finition_s"] * taille,
    }
    phases = {p: round(t * k["divers"] * vitesse, 1) for p, t in phases.items()}
    plafond = plafond_graine_s(params, score)
    return {"phases": phases, "total_s": round(min(sum(phases.values()), plafond), 1), "plafond_s": plafond}


def duree_calcul_prevue_s(duree_graine_s: float, essais: int, paralleles: int, duree_max_s: float) -> float:
    """Durée prévue du calcul : vagues de `paralleles` graines ; aucune graine n'est lancée qui ne peut finir
    avant l'échéance `duree_max_s` (§5.3), sauf la première vague."""
    if essais <= 0 or duree_graine_s <= 0:
        return 0.0
    vagues = math.ceil(essais / max(1, paralleles))
    possibles = max(1, math.floor(duree_max_s / duree_graine_s))
    return round(min(vagues, possibles) * duree_graine_s, 1)


# ------------------------------------------------------------------------------------------------ ligne de commande

def main(argv: list[str] | None = None) -> int:
    """`python parametres.py --verifier F.ini` : lit le fichier, affiche les fichiers lus, les valeurs et les
    avertissements ; code 0, ou 2 avec le message de l'erreur. `--modele` : écrit MODELE_INI sur la sortie."""
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv == ["--modele"]:
        sys.stdout.write(MODELE_INI)
        return 0
    if len(argv) != 2 or argv[0] != "--verifier":
        sys.stderr.write("usage : python parametres.py --verifier F.ini | --modele\n")
        return 2
    try:
        r = charger(parametres=argv[1], implicites=False)
    except ErreurParametres as e:
        sys.stderr.write(e.message + "\n")
        if e.fichier or e.ligne:
            sys.stderr.write(e.texte_complet()[len(e.message):].strip() + "\n")
        return 2
    print(f"fichiers lus : {', '.join(r.fichiers_lus)}")
    for s, cles in r.valeurs.items():
        for k, v in cles.items():
            if r.origine(s, k) != ORIGINE_DEFAUT:
                print(f"[{s}] {k} = {formater(s, k, v)}")
    for a in r.avertissements:
        print(f"avertissement : {a}")
    return 0


if __name__ == "__main__":
    for flux in (sys.stdout, sys.stderr):                # UTF-8, même redirigé (sinon cp1252 sous Windows)
        flux.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
