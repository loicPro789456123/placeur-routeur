"""Fenêtre du placeur-routeur (tkinter et ttk, un écran ; §5.5, lot D).

La fenêtre ne calcule rien : elle lance `placeur … --evenements` (placeur.exe dans le paquet, `python lanceur.py`
en mode source) rattaché à un objet de travail Windows (`chemins.Job`), lit ses évènements (lignes `EVT {json}`,
§5.3) et les affiche : tableau par graine, barre d'avancement, journal, temps restant.

- Colonne de gauche : carte, contraintes, calcul (qualité, essais, essais simultanés), mode de routage, sorties,
  boutons. À droite, trois onglets : Aperçu (carte d'origine puis résultat, pièces verrouillées marquées d'un
  cadenas, molette : zoom, glisser : déplacement), Règles (règles lues dans la carte, modifiables, transmises au
  calcul par un fichier de contraintes temporaire ; le fichier d'origine n'est jamais modifié), Calcul.
- Thème clair ou sombre (bouton en haut à droite) ; aucune image ni bibliothèque ajoutée.
- Arrêter agit en deux temps : fichier `arret_lancements` (« Finir les graines en cours »), puis `arret_demande`
  (« Tout arrêter ») dans le dossier du calcul.
- Fermeture pendant un calcul : « Arrêter proprement », « Tout arrêter » ou « Annuler ».
- Reprendre : proposé quand un calcul inachevé existe pour la carte choisie (`placeur.calcul_inacheve`).
- Plantage avant le calcul : `%LOCALAPPDATA%\\PlaceurRouteur\\erreurs.txt`, anonymisé (`chemins.anonymiser`).
- `--essai-fenetre MS` : la fenêtre s'ouvre puis se ferme seule après MS millisecondes (CI) ; `--carte FICHIER`
  choisit la carte à l'ouverture ; `--rapport R.json` écrit, à la fermeture, ce que la fenêtre a lu (essais de
  fumée).

Aucune variable d'environnement n'est lue (§0.2-2).
"""
from __future__ import annotations

import json
import math
import os
import queue
import sys
import threading
import time
import traceback

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import chemins  # noqa: E402
import parametres  # noqa: E402
import regles_utilisateur as RU  # noqa: E402

TITRE = "PlaceurRouteur"
QUALITES = (("Aperçu", "apercu"), ("Rapide", "rapide"), ("Normal", "normal"), ("Soigné", "soigne"))
MODES_ROUTAGE = (
    ("Automatique", "auto", "Le moteur garde son réglage habituel."),
    ("Manhattan", "manhattan", "Segments horizontaux et verticaux seulement : tracé régulier, un peu plus long."),
    ("Plus court", "plus_court", "Chemin le plus court dans toutes les directions : moins de longueur, tracé moins "
                                 "régulier."),
    ("Diagonales 45°", "diagonale_45", "Horizontal, vertical et 45° : le compromis usuel des cartes."),
    ("Comparer les modes", "comparer", "Les essais se partagent les trois modes ; la meilleure carte valide est "
                                       "gardée."),
)
TEXTE_ESSAIS = ("Chaque essai part d'une graine différente. Plusieurs essais tournent en même temps (un par "
                "processeur) ; le meilleur résultat valide est gardé.")
COLONNES = (("graine", "Graine", 60), ("etat", "État", 90), ("phase", "Phase", 150), ("duree", "Durée (s)", 80),
            ("cout", "Coût", 90), ("ouvertes", "Liaisons ouvertes", 110), ("mode", "Mode", 100))
COLONNES_REGLES = (("valeur", "Valeur (mm)", 110), ("origine", "Fichier (mm)", 110), ("aide", "Source", 260))
ETATS_FINIS = ("terminee", "abandonnee", "plantee")
TEXTE_ARRETER_1 = "Arrêter : Finir les graines en cours"
TEXTE_ARRETER_2 = "Arrêter : Tout arrêter"
CHOIX_FERMETURE = (("proprement", "Arrêter proprement"), ("tout", "Tout arrêter"), ("annuler", "Annuler"))
NOTE_KICAD = ".kicad_pcb, sans repositionnement de la sérigraphie"
NOM_ERREURS = "erreurs.txt"
PAS_LECTURE_MS = 100
EXTENSIONS_CARTE = (".pcbdoc", ".kicad_pcb")

# Palettes (thème clair, thème sombre) : interface sobre, une seule couleur d'accent.
THEMES = {
    "clair": {"fond": "#eef0f3", "surface": "#ffffff", "bord": "#cfd5dc", "texte": "#1c232b", "discret": "#5d6875",
              "accent": "#2a62a8", "accent_actif": "#214f88", "texte_accent": "#ffffff", "selection": "#dbe6f4",
              "erreur": "#b3261e", "modifie": "#8a5a00", "entete": "#f7f8fa",
              "toile": "#e6e9ed", "carte": "#d9e3d4", "contour": "#3c4a3a", "dessus": "#c0392b", "dessous": "#2f6db4",
              "interne": "#b4bec6", "piece": "#56606c", "verrou": "#c27c00", "texte_toile": "#2b333c"},
    "sombre": {"fond": "#1c1f24", "surface": "#25292f", "bord": "#3a4049", "texte": "#e2e5e9", "discret": "#9aa3ad",
               "accent": "#4f8fd6", "accent_actif": "#3f7cc0", "texte_accent": "#ffffff", "selection": "#2f4560",
               "erreur": "#ef6b62", "modifie": "#e2b04a", "entete": "#2b3037",
               "toile": "#14171b", "carte": "#1c2a22", "contour": "#c9d4b8", "dessus": "#d0533f",
               "dessous": "#4a8ad6", "interne": "#3b4650", "piece": "#aab3bd", "verrou": "#f0b232",
               "texte_toile": "#d6dbe0"},
}


def dossier_erreurs_defaut() -> str | None:
    base = chemins.dossier_connu("localappdata")
    return os.path.join(base, "PlaceurRouteur") if base else None


def ecrire_erreur(texte: str, dossier: str | None = None) -> str | None:
    """Écrit `erreurs.txt` (anonymisé) ; rend son chemin, ou None si impossible."""
    dossier = dossier or dossier_erreurs_defaut()
    if not dossier:
        return None
    try:
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, NOM_ERREURS)
        entete = f"placeur {chemins.version_lanceur()} : erreur de la fenêtre ({time.strftime('%Y-%m-%d %H:%M:%S')})\n"
        chemins.ecrire_texte_bloc_notes(chemin, chemins.anonymiser(entete + texte))
        return chemin
    except OSError:
        return None


def regler_dpi() -> bool:
    """SetProcessDpiAwareness(1) : texte net sur les écrans agrandis (Windows 8.1 et suivants)."""
    if not chemins.WINDOWS:
        return False
    import ctypes
    try:
        return ctypes.windll.shcore.SetProcessDpiAwareness(1) == 0
    except (AttributeError, OSError):
        return False


def _nombre(texte: str) -> int:
    try:
        return max(0, int(str(texte).strip() or 0))
    except ValueError:
        return 0


def _lire_json(chemin: str):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def pieces_a_garder(dossier: str) -> list[str]:
    """Pièces verrouillées et suggestions de fixation d'une analyse (`technique/entree_carte.json`,
    `technique/config_carte.json`), pour préremplir « Pièces à garder en place »."""
    technique = os.path.join(dossier, "technique")
    entree = _lire_json(os.path.join(technique, "entree_carte.json")) or {}
    config = _lire_json(os.path.join(technique, "config_carte.json")) or {}
    noms = [c.get("designateur") for c in entree.get("composants") or [] if isinstance(c, dict) and c.get("verrouille")]
    for s in config.get("pieces_a_fixer_suggerees") or []:
        noms.append(s if isinstance(s, str) else (s.get("piece") if isinstance(s, dict) else None))
    return [n for n in dict.fromkeys(noms) if n]


def _duree(s) -> str:
    if not isinstance(s, (int, float)):
        return "inconnue"
    s = int(round(s))
    return f"{s // 3600} h {s % 3600 // 60:02d} min" if s >= 3600 else f"{s // 60} min {s % 60:02d} s"




def barre_titre_sombre(racine, sombre: bool) -> None:
    """Barre de titre Windows assortie au thème (DWMWA_USE_IMMERSIVE_DARK_MODE) ; sans effet ailleurs."""
    if not chemins.WINDOWS:
        return
    try:
        import ctypes
        racine.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(racine.winfo_id()) or racine.winfo_id()
        v = ctypes.c_int(1 if sombre else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
    except (AttributeError, OSError):
        pass


def _mm(v) -> str:
    """Valeur en mm à la française (« 0,254 ») ; « — » : aucune."""
    if v is None:
        return "—"
    return (f"{v:.4f}".rstrip("0").rstrip(".") or "0").replace(".", ",")


def charger_carte(chemin: str) -> dict:
    """Carte importée (entree_carte/2) ; ValueError avec un message court si elle est refusée."""
    import importer_carte
    try:
        return importer_carte.importer(chemin)
    except Exception as e:  # noqa: BLE001 - toute erreur d'import devient un message de la fenêtre
        raise ValueError(chemins.anonymiser(str(e)) or type(e).__name__) from None


_VERROU_GC = threading.Lock()
_FILS_SANS_GC = [0]


class _SansRamasseMiettes:
    """Le ramasse-miettes cyclique ne doit pas tourner dans un fil de lecture : il pourrait y libérer des objets
    tkinter (Tcl_AsyncDelete, arrêt brutal). Il est suspendu pendant la lecture, puis rétabli."""

    def __enter__(self):
        import gc
        with _VERROU_GC:
            if _FILS_SANS_GC[0] == 0:
                gc.disable()
            _FILS_SANS_GC[0] += 1

    def __exit__(self, *_):
        import gc
        with _VERROU_GC:
            _FILS_SANS_GC[0] -= 1
            if _FILS_SANS_GC[0] == 0:
                gc.enable()


def _lire_carte(c: str, file) -> None:
    """Fil de lecture d'une carte (ne touche à aucun objet tkinter) : (« carte », (chemin, entrée, scène, erreur))."""
    with _SansRamasseMiettes():
        _lire_carte_fil(c, file)


def _lire_carte_fil(c: str, file) -> None:
    try:
        import apercu
        e = charger_carte(c)
        file.put(("carte", (c, e, apercu.depuis_entree(e), None)))
    except Exception as x:  # noqa: BLE001 - message montré dans la fenêtre
        file.put(("carte", (c, None, None, str(x) or type(x).__name__)))


def _lire_sortie(proc, file) -> None:
    """Fil de lecture de la sortie de l'orchestrateur (aucune référence à la fenêtre : voir _SansRamasseMiettes)."""
    try:
        for ligne in proc.stdout:
            file.put(("ligne", ligne.rstrip("\r\n")))
    except (OSError, ValueError):
        pass
    file.put(("termine", proc.wait()))


def _lire_resultat(dossier: str, entree, file) -> None:
    with _SansRamasseMiettes():
        try:
            import apercu
            s = apercu.depuis_resultat(dossier, entree)
        except Exception:  # noqa: BLE001 - l'aperçu du résultat est facultatif
            s = None
        file.put(("resultat", (dossier, s)))


class Apercu:
    """Toile de l'aperçu : contour, cuivre (dessus, dessous), pièces ; pièces verrouillées : cadenas et couleur
    propre. Molette : zoom autour du pointeur ; glisser : déplacement ; double-clic : vue entière."""

    def __init__(self, parent, tk, palette):
        self.tk = tk
        self.c = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
        self.p = palette
        self.scene = None
        self.echelle, self.ox, self.oy = 1.0, 0.0, 0.0
        self.message = "Choisir une carte pour voir son aperçu."
        self._prevu = None
        self.c.bind("<Configure>", lambda e: self._replanifier(ajuster=self.scene is not None and not self._vu))
        self.c.bind("<MouseWheel>", self._molette)
        self.c.bind("<Button-4>", lambda e: self._zoom(e.x, e.y, 1.2))
        self.c.bind("<Button-5>", lambda e: self._zoom(e.x, e.y, 1 / 1.2))
        self.c.bind("<ButtonPress-1>", self._prise)
        self.c.bind("<B1-Motion>", self._glisse)
        self.c.bind("<Double-Button-1>", lambda e: self.ajuster())
        self._vu = False
        self._dernier = None

    def theme(self, palette):
        self.p = palette
        self.dessiner()

    def montrer(self, scene, message=""):
        self.scene, self.message = scene, message
        self._vu = False
        self.ajuster()

    def ajuster(self):
        if self.scene is None:
            self.dessiner()
            return
        w, h = max(self.c.winfo_width(), 50), max(self.c.winfo_height(), 50)
        x0, y0, x1, y1 = self.scene.boite
        lw, lh = max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)
        self.echelle = 0.9 * min(w / lw, (h - 30) / lh)
        self.ox = w / 2 - self.echelle * (x0 + x1) / 2
        self.oy = (h - 30) / 2 + self.echelle * (y0 + y1) / 2
        self._vu = self.c.winfo_width() > 50
        self.dessiner()

    def _replanifier(self, ajuster=False):
        if self._prevu is not None:
            self.c.after_cancel(self._prevu)
        self._prevu = self.c.after(40, lambda: (setattr(self, "_prevu", None),
                                                self.ajuster() if ajuster else self.dessiner()))

    def ecran(self, x, y):
        return self.ox + self.echelle * x, self.oy - self.echelle * y

    def _molette(self, e):
        self._zoom(e.x, e.y, 1.2 if e.delta > 0 else 1 / 1.2)

    def _zoom(self, x, y, f):
        if self.scene is None:
            return
        self.ox = x - f * (x - self.ox)
        self.oy = y - f * (y - self.oy)
        self.echelle *= f
        self._replanifier()

    def _prise(self, e):
        self._dernier = (e.x, e.y)

    def _glisse(self, e):
        if self._dernier is None or self.scene is None:
            return
        dx, dy = e.x - self._dernier[0], e.y - self._dernier[1]
        self._dernier = (e.x, e.y)
        self.ox += dx
        self.oy += dy
        self.c.move("all", dx, dy)

    def _cadenas(self, x, y, t):
        """Cadenas dessiné à la toile (corps et anse), coin haut gauche en (x, y), taille t pixels."""
        p, c = self.p, self.c
        c.create_arc(x + 0.2 * t, y, x + 0.8 * t, y + 0.7 * t, start=0, extent=180, style="arc",
                     outline=p["verrou"], width=max(1, t / 7))
        c.create_rectangle(x, y + 0.38 * t, x + t, y + t, fill=p["verrou"], outline=p["verrou"])
        c.create_rectangle(x + 0.45 * t, y + 0.55 * t, x + 0.55 * t, y + 0.8 * t, fill=p["toile"], outline="")

    def dessiner(self):
        c, p = self.c, self.p
        c.delete("all")
        c.configure(background=p["toile"])
        if self.scene is None:
            c.create_text(max(c.winfo_width(), 200) / 2, max(c.winfo_height(), 100) / 2, text=self.message,
                          fill=p["discret"], font=("Segoe UI", 10))
            return
        s, E = self.scene, self.ecran
        if len(s.contour) >= 3:
            c.create_polygon(*[v for q in s.contour for v in E(*q)], fill=p["carte"], outline=p["contour"], width=2)
        for d in s.decoupes:
            if len(d) >= 3:
                c.create_polygon(*[v for q in d for v in E(*q)], fill=p["toile"], outline=p["contour"])
        ordre = {"dessous": 0, "interne": 1, "toutes": 2, "dessus": 3}
        for face, genre, d in sorted(s.cuivre, key=lambda o: ordre.get(o[0], 1)):
            coul = p["dessus"] if face in ("dessus", "toutes") else p["dessous"] if face == "dessous" else p["interne"]
            if genre == "poly":
                if len(d) >= 3:
                    c.create_polygon(*[v for q in d for v in E(*q)], fill=coul, outline="")
            elif genre == "seg":
                a, b = E(d[0], d[1]), E(d[2], d[3])
                c.create_line(a[0], a[1], b[0], b[1], fill=coul, width=max(1.0, d[4] * self.echelle),
                              capstyle="round")
            else:
                (x, y), r = E(d[0], d[1]), max(1.0, d[2] * self.echelle)
                c.create_oval(x - r, y - r, x + r, y + r, fill=coul, outline="")
        police = max(7, min(11, int(self.echelle * 0.9)))
        for q in s.pieces:
            pts = [v for k in q["coins"] for v in E(*k)]
            verrou = q["verrouille"]
            coul = p["verrou"] if verrou else p["piece"]
            c.create_polygon(*pts, fill="", outline=coul, width=2 if verrou else 1,
                             dash=() if not q["dessous"] else (4, 2))
            cx, cy = E(*q["centre"])
            xs, ys = pts[0::2], pts[1::2]
            if max(max(xs) - min(xs), max(ys) - min(ys)) >= 6.5 * len(q["nom"]) and self.echelle > 2:
                c.create_text(cx, cy, text=q["nom"], fill=coul if verrou else p["texte_toile"],
                              font=("Segoe UI", police))
            if verrou:
                t = max(9, min(16, self.echelle * 1.2))
                self._cadenas(max(xs) - t * 0.4, min(ys) - t * 0.6, t)
        # légende
        h = c.winfo_height()
        x = 10
        for texte, coul, forme in (("Cuivre dessus", p["dessus"], "r"), ("Cuivre dessous", p["dessous"], "r"),
                                   ("Pièce (tirets : dessous)", p["piece"], "o"),
                                   ("Pièce verrouillée", p["verrou"], "v")):
            if forme == "v":
                self._cadenas(x, h - 22, 12)
            elif forme == "o":
                c.create_rectangle(x, h - 21, x + 12, h - 11, outline=coul, width=1)
            else:
                c.create_rectangle(x, h - 21, x + 12, h - 11, fill=coul, outline="")
            t = c.create_text(x + 18, h - 16, text=texte, anchor="w", fill=p["texte_toile"], font=("Segoe UI", 9))
            x = c.bbox(t)[2] + 16
        r = s.resume()
        c.create_text(c.winfo_width() - 10, h - 16, anchor="e", fill=p["texte_toile"], font=("Segoe UI", 9),
                      text=f"{s.titre} : {r['pieces']} pièces, {r['verrouillees']} verrouillée(s)")


class Fenetre:
    """La fenêtre de §5.5. `commande(*args)` : commande de l'orchestrateur (par défaut `chemins.commande_placeur`) ;
    `demander_fermeture()` : « proprement », « tout » ou « annuler » ; `dossier_erreurs` : dossier de erreurs.txt ;
    `ouvrir(chemin)` : ouvre un dossier ou un fichier (Explorateur, bloc-notes)."""

    def __init__(self, racine, *, commande=None, demander_fermeture=None, dossier_erreurs=None, ouvrir=None,
                 informer=None, theme="clair"):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.racine = racine
        self.commande = commande or chemins.commande_placeur
        self.demander_fermeture = demander_fermeture or self._dialogue_fermeture
        self.dossier_erreurs = dossier_erreurs
        self.ouvrir = ouvrir or self._ouvrir_systeme
        self.informer = informer or self._message
        self.proc = None
        self.job = None
        self.mode = None                # « calcul », « analyse »
        self.file = queue.Queue()
        self.sortie_calcul = None
        self.essais = 0
        self.arret = 0                  # 0, 1 (arret_lancements), 2 (arret_demande)
        self.fin = None
        self.code = None
        self.t0 = None
        self.duree_max_s = None
        self.fermer_a_la_fin = False
        self.ferme = False
        self.modifies: set[str] = set()
        self._programme = False
        self.lignes_journal: list[str] = []
        self.entree = None              # carte importée (entree_carte/2) pour les règles et l'aperçu
        self.carte_lue = None
        self.regles: list = []
        self.valeurs_regles: dict = {}
        self.erreurs_regles: dict = {}
        self.scene_origine = None
        self.scene_resultat = None
        self.mode_ignore = False
        self.fichiers_temporaires: list[str] = []
        self.nom_theme = theme if theme in THEMES else "clair"
        racine.title(TITRE)
        racine.protocol("WM_DELETE_WINDOW", self.fermeture)
        racine.report_callback_exception = self.erreur_rappel
        racine.minsize(1080, 680)
        self._polices()
        self._construire()
        self.appliquer_theme()
        self._apres = racine.after(PAS_LECTURE_MS, self.pomper)

    # ------------------------------------------------------------------------------------------ apparence
    def _polices(self) -> None:
        from tkinter import font
        familles = set(font.families(self.racine))
        texte = next((f for f in ("Segoe UI", "Noto Sans", "DejaVu Sans", "Helvetica") if f in familles), None)
        mono = next((f for f in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New") if f in familles),
                    None)
        for nom in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                f = font.nametofont(nom)
                if texte:
                    f.configure(family=texte, size=9)
            except Exception:  # noqa: BLE001 - police absente
                pass
        self.police = (texte or "TkDefaultFont", 9)
        self.police_titre = (texte or "TkDefaultFont", 12, "bold")
        self.police_section = (texte or "TkDefaultFont", 9, "bold")
        self.police_mono = (mono or "TkFixedFont", 9)

    def appliquer_theme(self) -> None:
        p = THEMES[self.nom_theme]
        s = self.ttk.Style(self.racine)
        try:
            s.theme_use("clam")
        except Exception:  # noqa: BLE001 - thème absent : celui par défaut
            pass
        r = self.racine
        r.configure(background=p["fond"])
        s.configure(".", background=p["fond"], foreground=p["texte"], fieldbackground=p["surface"],
                    bordercolor=p["bord"], lightcolor=p["fond"], darkcolor=p["fond"], troughcolor=p["entete"],
                    selectbackground=p["selection"], selectforeground=p["texte"], insertcolor=p["texte"],
                    focuscolor=p["accent"], font=self.police)
        s.configure("TFrame", background=p["fond"])
        s.configure("Panneau.TFrame", background=p["surface"])
        s.configure("Entete.TFrame", background=p["surface"])
        s.configure("TLabel", background=p["fond"], foreground=p["texte"])
        s.configure("Panneau.TLabel", background=p["surface"])
        s.configure("Discret.TLabel", background=p["surface"], foreground=p["discret"])
        s.configure("Aide.TLabel", background=p["surface"], foreground=p["discret"])
        s.configure("Section.TLabel", background=p["surface"], foreground=p["discret"], font=self.police_section)
        s.configure("Titre.TLabel", background=p["surface"], foreground=p["texte"], font=self.police_titre)
        s.configure("Etat.TLabel", background=p["entete"], foreground=p["discret"], padding=(10, 4))
        s.configure("Erreur.TLabel", background=p["surface"], foreground=p["erreur"])
        s.configure("TCheckbutton", background=p["surface"], foreground=p["texte"], indicatorbackground=p["surface"],
                    indicatorforeground=p["accent"])
        s.map("TCheckbutton", background=[("active", p["surface"])],
              indicatorbackground=[("selected", p["surface"]), ("active", p["surface"])])
        s.configure("TButton", background=p["entete"], foreground=p["texte"], bordercolor=p["bord"],
                    lightcolor=p["entete"], darkcolor=p["entete"], padding=(10, 4), relief="flat")
        s.map("TButton", background=[("disabled", p["fond"]), ("pressed", p["selection"]), ("active", p["selection"])],
              foreground=[("disabled", p["discret"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["texte_accent"], bordercolor=p["accent"],
                    lightcolor=p["accent"], darkcolor=p["accent"], font=(self.police[0], 9, "bold"))
        s.map("Accent.TButton", background=[("disabled", p["bord"]), ("pressed", p["accent_actif"]),
                                            ("active", p["accent_actif"])],
              foreground=[("disabled", p["discret"])])
        s.configure("TMenubutton", background=p["entete"], foreground=p["texte"], bordercolor=p["bord"],
                    lightcolor=p["entete"], darkcolor=p["entete"], arrowcolor=p["discret"], padding=(10, 4),
                    relief="flat")
        s.map("TMenubutton", background=[("active", p["selection"])])
        s.configure("Choisi.TButton", background=p["selection"], foreground=p["texte"], bordercolor=p["accent"],
                    lightcolor=p["selection"], darkcolor=p["selection"], padding=(10, 4))
        s.map("Choisi.TButton", background=[("active", p["selection"])])
        s.configure("TEntry", fieldbackground=p["surface"], foreground=p["texte"], bordercolor=p["bord"],
                    lightcolor=p["surface"], darkcolor=p["surface"], padding=3)
        s.map("TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])])
        for w in ("TCombobox", "TSpinbox"):
            s.configure(w, fieldbackground=p["surface"], foreground=p["texte"], background=p["entete"],
                        bordercolor=p["bord"], arrowcolor=p["discret"], lightcolor=p["surface"],
                        darkcolor=p["surface"], padding=2)
            s.map(w, fieldbackground=[("readonly", p["surface"])], foreground=[("readonly", p["texte"])],
                  bordercolor=[("focus", p["accent"])], selectbackground=[("readonly", p["surface"])],
                  selectforeground=[("readonly", p["texte"])])
        r.option_add("*TCombobox*Listbox.background", p["surface"])
        r.option_add("*TCombobox*Listbox.foreground", p["texte"])
        r.option_add("*TCombobox*Listbox.selectBackground", p["selection"])
        r.option_add("*TCombobox*Listbox.selectForeground", p["texte"])
        s.configure("TNotebook", background=p["fond"], bordercolor=p["bord"], tabmargins=(0, 4, 0, 0))
        s.configure("TNotebook.Tab", background=p["fond"], foreground=p["discret"], padding=(14, 6),
                    bordercolor=p["bord"], lightcolor=p["fond"])
        s.map("TNotebook.Tab", background=[("selected", p["surface"])], foreground=[("selected", p["texte"])],
              lightcolor=[("selected", p["accent"])])
        s.configure("Treeview", background=p["surface"], fieldbackground=p["surface"], foreground=p["texte"],
                    bordercolor=p["bord"], rowheight=22)
        s.map("Treeview", background=[("selected", p["selection"])], foreground=[("selected", p["texte"])])
        s.configure("Treeview.Heading", background=p["entete"], foreground=p["discret"], bordercolor=p["bord"],
                    relief="flat", padding=(6, 4), font=self.police_section)
        s.map("Treeview.Heading", background=[("active", p["selection"])])
        s.configure("Horizontal.TProgressbar", background=p["accent"], troughcolor=p["entete"],
                    bordercolor=p["bord"], lightcolor=p["accent"], darkcolor=p["accent"])
        s.configure("TSeparator", background=p["bord"])
        s.configure("TPanedwindow", background=p["bord"])
        s.configure("TLabelframe", background=p["surface"], bordercolor=p["bord"])
        s.configure("TLabelframe.Label", background=p["surface"], foreground=p["discret"], font=self.police_section)
        for t in (self.journal_texte,):
            t.configure(background=p["surface"], foreground=p["texte"], insertbackground=p["texte"],
                        selectbackground=p["selection"], highlightthickness=1, highlightbackground=p["bord"],
                        highlightcolor=p["bord"], relief="flat", borderwidth=0)
        self.regles_arbre.tag_configure("groupe", font=(self.police[0], 9, "bold"), foreground=p["texte"],
                                        background=p["entete"])
        self.regles_arbre.tag_configure("modifie", foreground=p["modifie"])
        self.regles_arbre.tag_configure("erreur", foreground=p["erreur"])
        self.regles_arbre.tag_configure("info", foreground=p["discret"])
        self.b_theme.configure(text="Thème clair" if self.nom_theme == "sombre" else "Thème sombre")
        self.menu_aide.configure(background=p["surface"], foreground=p["texte"], activebackground=p["selection"],
                                 activeforeground=p["texte"], relief="flat", borderwidth=1)
        barre_titre_sombre(self.racine, self.nom_theme == "sombre")
        self.apercu.theme(p)

    def basculer_theme(self) -> None:
        self.nom_theme = "sombre" if self.nom_theme == "clair" else "clair"
        self.appliquer_theme()

    # ------------------------------------------------------------------------------------------ construction
    def _var(self, cle: str | None, genre="texte", valeur=""):
        tk = self.tk
        v = {"texte": tk.StringVar, "bool": tk.BooleanVar}[genre](value=valeur)
        if cle:
            v.trace_add("write", lambda *_: None if self._programme else self.modifies.add(cle))
        return v

    def _construire(self) -> None:
        tk, ttk = self.tk, self.ttk
        r = self.racine
        aide = tk.Menu(r, tearoff=0)
        aide.add_command(label="Mode d'emploi", command=self.mode_emploi)
        aide.add_command(label="Vérifier l'installation", command=self.auto_test)
        aide.add_command(label="Copier le diagnostic", command=self.copier_diagnostic)
        self.menu = self.menu_aide = aide
        r.columnconfigure(0, weight=1)
        r.rowconfigure(1, weight=1)

        self.v_carte = self._var("carte.fichier")
        self.v_contraintes = self._var("carte.contraintes")
        self.v_fixes = self._var("contraintes.fixes")
        self.v_sortie = self._var("sortie.dossier")
        self.v_qualite = self._var("calcul.qualite", valeur="Normal")
        self.v_duree = self._var("calcul.duree_max_min", valeur="0")
        self.v_essais = self._var("calcul.essais", valeur="0")
        self.v_processeurs = self._var("calcul.processeurs", valeur="0")
        self.v_valides = self._var("calcul.valides_voulues", valeur="0")
        self.v_mode_routage = self._var("calcul.mode_routage", valeur=MODES_ROUTAGE[0][0])
        self.v_pcbdoc = self._var("pcbdoc.produire", "bool", True)
        self.v_serigraphie = self._var("pcbdoc.serigraphie", "bool", True)
        self.v_couture = self._var("pcbdoc.vias_couture", "bool", True)
        self.v_ouvrir = self._var("sortie.ouvrir_a_la_fin", "bool", True)
        self.v_priorite = self._var("calcul.priorite_normale", "bool", False)
        self.v_carte.trace_add("write", lambda *_: self.actualiser())
        self.v_mode_routage.trace_add("write", lambda *_: self._explication_mode())

        # bandeau
        entete = ttk.Frame(r, style="Entete.TFrame", padding=(14, 8))
        entete.grid(row=0, column=0, sticky="ew")
        entete.columnconfigure(2, weight=1)
        ttk.Label(entete, text=TITRE, style="Titre.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(entete, text="placement et routage automatiques", style="Discret.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 24), pady=(3, 0))
        boutons = ttk.Frame(entete, style="Entete.TFrame")
        boutons.grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.b_lancer = ttk.Button(boutons, text="Lancer", style="Accent.TButton", command=self.lancer)
        self.b_analyser = ttk.Button(boutons, text="Analyser la carte", command=self.analyser)
        self.b_arreter = ttk.Button(boutons, text=TEXTE_ARRETER_1, command=self.arreter, state="disabled")
        self.b_reprendre = ttk.Button(boutons, text="Reprendre", command=self.reprendre, state="disabled")
        self.b_resultats = ttk.Button(boutons, text="Ouvrir les résultats", command=self.ouvrir_resultats)
        self.b_enregistrer = ttk.Button(boutons, text="Enregistrer les paramètres", command=self.enregistrer)
        for i, b in enumerate((self.b_lancer, self.b_analyser, self.b_arreter, self.b_reprendre, self.b_resultats,
                               self.b_enregistrer)):
            b.grid(row=0, column=i, padx=(0 if i == 0 else 12 if i == 3 else 4, 0))
        self.b_theme = ttk.Button(entete, text="Thème sombre", command=self.basculer_theme)
        self.b_theme.grid(row=0, column=3, sticky="e", padx=(12, 0))
        self.b_aide = ttk.Menubutton(entete, text="Aide", menu=aide)
        self.b_aide.grid(row=0, column=4, sticky="e", padx=(4, 0))
        ttk.Separator(r, orient="horizontal").grid(row=0, column=0, sticky="sew")

        corps = ttk.Panedwindow(r, orient="horizontal")
        corps.grid(row=1, column=0, sticky="nsew")
        gauche = ttk.Frame(corps, style="Panneau.TFrame", padding=(14, 10, 12, 10))
        droite = ttk.Frame(corps, padding=(8, 8, 10, 6))
        corps.add(gauche, weight=0)
        corps.add(droite, weight=1)
        gauche.columnconfigure(0, weight=1)
        gauche.columnconfigure(1, weight=1)
        self._ligne = 0

        def section(texte):
            ttk.Label(gauche, text=texte, style="Section.TLabel").grid(
                row=self._ligne, column=0, columnspan=3, sticky="w", pady=(10 if self._ligne else 0, 3))
            self._ligne += 1

        def champ(texte, var, bouton=None, commande=None):
            ttk.Label(gauche, text=texte, style="Panneau.TLabel").grid(row=self._ligne, column=0, columnspan=3,
                                                                     sticky="w")
            self._ligne += 1
            e = ttk.Entry(gauche, textvariable=var, width=34)
            e.grid(row=self._ligne, column=0, columnspan=2 if bouton else 3, sticky="ew", pady=(1, 4))
            if bouton:
                ttk.Button(gauche, text=bouton, command=commande).grid(row=self._ligne, column=2, sticky="ew",
                                                                       padx=(4, 0), pady=(1, 4))
            self._ligne += 1
            return e

        def nombre(texte, var, colonne):
            cadre = ttk.Frame(gauche, style="Panneau.TFrame")
            cadre.grid(row=self._ligne, column=colonne, sticky="ew", padx=(0, 6), pady=(0, 4))
            ttk.Label(cadre, text=texte, style="Panneau.TLabel").pack(anchor="w")
            ttk.Spinbox(cadre, textvariable=var, from_=0, to=9999, width=8).pack(anchor="w", fill="x")

        section("Carte")
        champ("Carte (.PcbDoc ou .kicad_pcb)", self.v_carte, "Parcourir…", self.choisir_carte)
        self.note_carte = ttk.Label(gauche, text="", style="Aide.TLabel", wraplength=320)
        self.note_carte.grid(row=self._ligne, column=0, columnspan=3, sticky="w")
        self.note_carte.grid_remove()
        self._ligne += 1
        champ("Contraintes (facultatif)", self.v_contraintes, "Parcourir…", self.choisir_contraintes)
        ttk.Button(gauche, text="Contraintes avancées…", command=self.contraintes_avancees).grid(
            row=self._ligne, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self._ligne += 1
        champ("Pièces à garder en place", self.v_fixes)
        champ("Dossier de sortie", self.v_sortie, "Parcourir…", self.choisir_sortie)

        section("Calcul")
        ttk.Label(gauche, text="Qualité", style="Panneau.TLabel").grid(row=self._ligne, column=0, sticky="w")
        ttk.Label(gauche, text="Durée maximale (min, 0 : celle du niveau)", style="Panneau.TLabel").grid(
            row=self._ligne, column=1, columnspan=2, sticky="w")
        self._ligne += 1
        ttk.Combobox(gauche, textvariable=self.v_qualite, values=[q for q, _ in QUALITES], width=10,
                     state="readonly").grid(row=self._ligne, column=0, sticky="ew", padx=(0, 6), pady=(0, 4))
        ttk.Spinbox(gauche, textvariable=self.v_duree, from_=0, to=9999, width=8).grid(
            row=self._ligne, column=1, columnspan=2, sticky="ew", pady=(0, 4))
        self._ligne += 1
        nombre("Essais (0 : ceux du niveau)", self.v_essais, 0)
        nombre("Processeurs (essais simultanés, 0 : tous sauf un)", self.v_processeurs, 1)
        self._ligne += 1
        ttk.Label(gauche, text=TEXTE_ESSAIS, style="Aide.TLabel", wraplength=320, justify="left").grid(
            row=self._ligne, column=0, columnspan=3, sticky="w")
        self._ligne += 1

        section("Routage")
        ttk.Label(gauche, text="Mode de routage", style="Panneau.TLabel").grid(row=self._ligne, column=0,
                                                                             columnspan=3, sticky="w")
        self._ligne += 1
        self.choix_mode = ttk.Combobox(gauche, textvariable=self.v_mode_routage, values=[m for m, _, _ in MODES_ROUTAGE],
                                       state="readonly", width=22)
        self.choix_mode.grid(row=self._ligne, column=0, columnspan=3, sticky="ew", pady=(1, 2))
        self._ligne += 1
        self.l_mode = ttk.Label(gauche, text="", style="Aide.TLabel", wraplength=320, justify="left")
        self.l_mode.grid(row=self._ligne, column=0, columnspan=3, sticky="w")
        self._ligne += 1

        section("Sorties")
        cases = ttk.Frame(gauche, style="Panneau.TFrame")
        cases.grid(row=self._ligne, column=0, columnspan=3, sticky="w")
        self._ligne += 1
        self.case_pcbdoc = ttk.Checkbutton(cases, text="Carte produite", variable=self.v_pcbdoc)
        self.case_pcbdoc.grid(row=0, column=0, columnspan=2, sticky="w")
        for i, (texte, var) in enumerate((("Sérigraphie", self.v_serigraphie), ("Vias de couture", self.v_couture),
                                          ("Ouvrir à la fin", self.v_ouvrir))):
            ttk.Checkbutton(cases, text=texte, variable=var).grid(row=1 + i // 2, column=i % 2, sticky="w",
                                                                   padx=(0, 12))

        section("Réglages avancés")
        ttk.Label(gauche, text="S'arrêter après K graines valides (0 : jamais)", style="Panneau.TLabel").grid(
            row=self._ligne, column=0, columnspan=2, sticky="w")
        ttk.Spinbox(gauche, textvariable=self.v_valides, from_=0, to=9999, width=6).grid(
            row=self._ligne, column=2, sticky="e")
        self._ligne += 1
        ttk.Checkbutton(gauche, text="Priorité normale", variable=self.v_priorite).grid(
            row=self._ligne, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self._ligne += 1


        # onglets
        droite.columnconfigure(0, weight=1)
        droite.rowconfigure(0, weight=1)
        self.onglets = ttk.Notebook(droite)
        self.onglets.grid(row=0, column=0, sticky="nsew")
        o_apercu = ttk.Frame(self.onglets, style="Panneau.TFrame", padding=6)
        o_regles = ttk.Frame(self.onglets, style="Panneau.TFrame", padding=8)
        o_calcul = ttk.Frame(self.onglets, style="Panneau.TFrame", padding=8)
        self.onglets.add(o_apercu, text="Aperçu")
        self.onglets.add(o_regles, text="Règles")
        self.onglets.add(o_calcul, text="Calcul")
        self.onglet_apercu, self.onglet_regles, self.onglet_calcul = o_apercu, o_regles, o_calcul

        # aperçu
        o_apercu.columnconfigure(0, weight=1)
        o_apercu.rowconfigure(1, weight=1)
        barre_a = ttk.Frame(o_apercu, style="Panneau.TFrame")
        barre_a.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.v_vue = tk.StringVar(value="origine")
        self.b_vue_origine = ttk.Button(barre_a, text="Carte d'origine", command=lambda: self.vue("origine"))
        self.b_vue_resultat = ttk.Button(barre_a, text="Résultat", command=lambda: self.vue("resultat"),
                                         state="disabled")
        self.b_vue_origine.pack(side="left")
        self.b_vue_resultat.pack(side="left", padx=(4, 0))
        ttk.Button(barre_a, text="Vue entière", command=lambda: self.apercu.ajuster()).pack(side="left", padx=(12, 0))
        ttk.Label(barre_a, text="Molette : zoom. Glisser : déplacer. Double-clic : vue entière.",
                  style="Discret.TLabel").pack(side="right")
        self.apercu = Apercu(o_apercu, tk, THEMES[self.nom_theme])
        self.apercu.c.grid(row=1, column=0, sticky="nsew")

        # règles
        o_regles.columnconfigure(0, weight=1)
        o_regles.rowconfigure(1, weight=1)
        barre_r = ttk.Frame(o_regles, style="Panneau.TFrame")
        barre_r.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(barre_r, text="Filtrer", style="Panneau.TLabel").pack(side="left")
        self.v_filtre = tk.StringVar()
        self.v_filtre.trace_add("write", lambda *_: self.remplir_regles())
        ttk.Entry(barre_r, textvariable=self.v_filtre, width=24).pack(side="left", padx=(6, 12))
        self.b_regle_defaut = ttk.Button(barre_r, text="Valeur du fichier", command=self.regle_du_fichier)
        self.b_regles_defaut = ttk.Button(barre_r, text="Tout réinitialiser", command=self.reinitialiser_regles)
        self.b_regle_defaut.pack(side="left")
        self.b_regles_defaut.pack(side="left", padx=(4, 0))
        self.l_regles = ttk.Label(barre_r, text="", style="Discret.TLabel")
        self.l_regles.pack(side="right")
        self.regles_arbre = ttk.Treeview(o_regles, columns=[c for c, _, _ in COLONNES_REGLES], show="tree headings",
                                         selectmode="browse")
        self.regles_arbre.heading("#0", text="Règle")
        self.regles_arbre.column("#0", width=280, stretch=True)
        for c, texte, largeur in COLONNES_REGLES:
            self.regles_arbre.heading(c, text=texte)
            self.regles_arbre.column(c, width=largeur, anchor="e" if c != "aide" else "w", stretch=c == "aide")
        self.regles_arbre.grid(row=1, column=0, sticky="nsew")
        defil = ttk.Scrollbar(o_regles, orient="vertical", command=self.regles_arbre.yview)
        defil.grid(row=1, column=1, sticky="ns")
        self.regles_arbre.configure(yscrollcommand=defil.set)
        self.regles_arbre.bind("<Double-1>", self.editer_regle)
        self.regles_arbre.bind("<Return>", self.editer_regle)
        self.regles_arbre.bind("<F2>", self.editer_regle)
        self.l_regles_note = ttk.Label(o_regles, text="Choisir une carte .PcbDoc ou .kicad_pcb : ses règles "
                                                      "s'affichent ici. Double-clic sur une valeur pour la modifier ; "
                                                      "le fichier d'origine n'est jamais modifié.",
                                       style="Aide.TLabel", wraplength=700, justify="left")
        self.l_regles_note.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.editeur = None

        # calcul
        o_calcul.columnconfigure(0, weight=1)
        o_calcul.rowconfigure(1, weight=3)
        o_calcul.rowconfigure(4, weight=2)
        self.l_prevision = ttk.Label(o_calcul, text="Durée prévue : inconnue", style="Panneau.TLabel")
        self.l_prevision.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.tableau = ttk.Treeview(o_calcul, columns=[c for c, _, _ in COLONNES], show="headings", height=6)
        for c, texte, largeur in COLONNES:
            self.tableau.heading(c, text=texte)
            self.tableau.column(c, width=largeur, anchor="center")
        self.tableau.grid(row=1, column=0, sticky="nsew")
        avancement = ttk.Frame(o_calcul, style="Panneau.TFrame")
        avancement.grid(row=2, column=0, sticky="ew", pady=8)
        avancement.columnconfigure(0, weight=1)
        self.barre = ttk.Progressbar(avancement, mode="determinate", maximum=100)
        self.barre.grid(row=0, column=0, sticky="ew")
        self.l_pourcent = ttk.Label(avancement, text="0 %", width=6, anchor="e", style="Panneau.TLabel")
        self.l_pourcent.grid(row=0, column=1)
        self.l_restant = ttk.Label(avancement, text="Temps restant estimé : inconnu", style="Discret.TLabel")
        self.l_restant.grid(row=0, column=2, padx=(10, 0))
        ttk.Label(o_calcul, text="Journal", style="Section.TLabel").grid(row=3, column=0, sticky="w")
        self.journal_texte = tk.Text(o_calcul, height=8, wrap="word", state="disabled", font=self.police_mono,
                                     padx=6, pady=4)
        self.journal_texte.grid(row=4, column=0, sticky="nsew")

        self.l_etat = ttk.Label(r, text="Prêt.", style="Etat.TLabel")
        self.l_etat.grid(row=2, column=0, sticky="ew")
        self._explication_mode()

    # ------------------------------------------------------------------------------------------ outils
    def textes(self) -> list[str]:
        """Textes de tous les composants, des onglets et du menu Aide (relevé des essais, §5.5)."""
        vus = []

        def parcourir(w):
            for opt in (() if isinstance(w, (self.ttk.Entry, self.tk.Entry)) else ("text",)):
                try:
                    t = w.cget(opt)
                except Exception:  # noqa: BLE001 - composant sans texte
                    t = ""
                if t:
                    vus.append(str(t))
            if isinstance(w, self.ttk.Treeview):
                vus.extend(w.heading(c)["text"] for c in w["columns"])
            if isinstance(w, self.ttk.Notebook):
                vus.extend(w.tab(i, "text") for i in w.tabs())
            for e in w.winfo_children():
                parcourir(e)
        parcourir(self.racine)
        for m in dict.fromkeys((self.menu, self.menu_aide)):
            for i in range((m.index("end") or 0) + 1):
                try:
                    vus.append(m.entrycget(i, "label"))
                except Exception:  # noqa: BLE001 - séparateur
                    pass
        return vus

    def journal(self, texte: str) -> None:
        self.lignes_journal.append(texte)
        w = self.journal_texte
        w.configure(state="normal")
        w.insert("end", texte + "\n")
        w.see("end")
        w.configure(state="disabled")

    def etat(self, texte: str) -> None:
        self.l_etat.configure(text=texte)

    def _message(self, titre: str, texte: str, erreur: bool = False) -> None:
        from tkinter import messagebox
        (messagebox.showerror if erreur else messagebox.showinfo)(titre, texte, parent=self.racine)

    @staticmethod
    def _ouvrir_systeme(chemin: str) -> None:
        if chemins.WINDOWS:
            os.startfile(chemin)  # noqa: S606 - ouverture d'un dossier ou d'un fichier par l'Explorateur

    def en_cours(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def erreur_rappel(self, genre, valeur, trace) -> None:
        """Exception dans un rappel tkinter : erreurs.txt (anonymisé) si aucun calcul n'a commencé."""
        texte = "".join(traceback.format_exception(genre, valeur, trace))
        chemin = None
        if self.sortie_calcul is None:
            chemin = ecrire_erreur(texte, self.dossier_erreurs)
        self.journal("erreur de la fenêtre" + (f" : voir {NOM_ERREURS}" if chemin else ""))
        self.informer(TITRE, "Erreur de la fenêtre." + (f"\nDétails : {chemins.anonymiser(chemin)}"
                                                         if chemin else ""), True)

    def _explication_mode(self) -> None:
        nom = self.v_mode_routage.get()
        self.l_mode.configure(text=next((t for m, _, t in MODES_ROUTAGE if m == nom), ""))

    def mode_routage(self) -> str:
        nom = self.v_mode_routage.get()
        return next((c for m, c, _ in MODES_ROUTAGE if m == nom), "auto")

    # ------------------------------------------------------------------------------------------ champs
    def choisir_carte(self) -> None:
        from tkinter import filedialog
        c = filedialog.askopenfilename(parent=self.racine, title="Carte", filetypes=[
            ("Carte Altium", "*.PcbDoc"), ("Carte KiCad", "*.kicad_pcb"), ("Données du placeur", "*.json"),
            ("Tous les fichiers", "*.*")])
        if c:
            self.v_carte.set(os.path.normpath(c))

    def choisir_contraintes(self) -> None:
        from tkinter import filedialog
        c = filedialog.askopenfilename(parent=self.racine, title="Contraintes", filetypes=[
            ("Contraintes", "*.json"), ("Tous les fichiers", "*.*")])
        if c:
            self.v_contraintes.set(os.path.normpath(c))

    def choisir_sortie(self) -> None:
        from tkinter import filedialog
        d = filedialog.askdirectory(parent=self.racine, title="Dossier de sortie")
        if d:
            self.v_sortie.set(os.path.normpath(d))

    def carte(self) -> str:
        return self.v_carte.get().strip().strip('"')

    def actualiser(self) -> None:
        """Note KiCad de la case « Carte produite », bouton Reprendre, lecture de la carte (règles, aperçu)."""
        c = self.carte()
        kicad = c.lower().endswith(".kicad_pcb")
        self.case_pcbdoc.configure(text="Carte produite (" + NOTE_KICAD + ")" if kicad else "Carte produite")
        self.note_carte.configure(text=NOTE_KICAD if kicad else "")
        (self.note_carte.grid if kicad else self.note_carte.grid_remove)()
        if not self.en_cours():
            self.b_reprendre.configure(state="normal" if self.inacheve() else "disabled")
        self.lire_carte()

    def inacheve(self) -> str | None:
        c = self.carte()
        if not c or not os.path.isfile(c):
            return None
        try:
            import placeur
            return placeur.calcul_inacheve(c, self.v_sortie.get().strip() or None)
        except Exception:  # noqa: BLE001 - la proposition de reprise est facultative
            return None

    # ------------------------------------------------------------------------------------------ carte : lecture
    def lire_carte(self) -> None:
        """Lecture de la carte choisie dans un fil (import seul, rien n'est écrit) ; règles et aperçu à la fin."""
        c = self.carte()
        if c == self.carte_lue:
            return
        self.carte_lue = c
        if not c or not c.lower().endswith(EXTENSIONS_CARTE) or not os.path.isfile(c):
            self.entree = None
            self.regles, self.valeurs_regles, self.erreurs_regles = [], {}, {}
            self.remplir_regles()
            self.scene_origine = self.scene_resultat = None
            self.b_vue_resultat.configure(state="disabled")
            self.apercu.montrer(None, "Choisir une carte pour voir son aperçu." if not c else
                                "Aperçu et règles : pour une carte .PcbDoc ou .kicad_pcb."
                                if not c.lower().endswith(EXTENSIONS_CARTE) else "Carte introuvable.")
            return
        self.apercu.montrer(None, "Lecture de la carte…")
        self.etat(f"Lecture de {os.path.basename(c)}…")
        import gc
        gc.collect()                    # objets tkinter orphelins libérés ici, dans le fil principal
        threading.Thread(target=_lire_carte, args=(c, self.file), daemon=True).start()

    def carte_lue_ok(self, valeur) -> None:
        c, entree, scene, erreur = valeur
        if c != self.carte_lue:
            return                      # une autre carte a été choisie entre-temps
        if erreur:
            self.entree = None
            self.regles = []
            self.remplir_regles()
            self.apercu.montrer(None, "Carte illisible : " + erreur)
            self.etat("Carte illisible : " + erreur)
            return
        self.entree = entree
        self.regles = RU.lignes(entree)
        self.valeurs_regles = RU.valeurs_origine(self.regles)
        self.erreurs_regles = {}
        self.remplir_regles()
        self.scene_origine, self.scene_resultat = scene, None
        self.b_vue_resultat.configure(state="disabled")
        self.vue("origine")
        r = scene.resume()
        self.etat(f"{os.path.basename(c)} : {r['pieces']} pièces dont {r['verrouillees']} verrouillée(s), "
                  f"{len(self.regles)} règle(s) lue(s).")

    def vue(self, quelle: str) -> None:
        if quelle == "resultat" and self.scene_resultat is not None:
            self.apercu.montrer(self.scene_resultat)
        else:
            self.apercu.montrer(self.scene_origine, "Choisir une carte pour voir son aperçu.")
        self.v_vue.set(quelle)
        self.b_vue_origine.configure(style="Choisi.TButton" if quelle != "resultat" else "TButton")
        self.b_vue_resultat.configure(style="Choisi.TButton" if quelle == "resultat" else "TButton")

    # ------------------------------------------------------------------------------------------ règles
    def remplir_regles(self) -> None:
        a = self.regles_arbre
        ouverts = {a.item(i, "text") for i in a.get_children() if a.item(i, "open")} or None
        a.delete(*a.get_children())
        filtre = self.v_filtre.get().strip().lower()
        groupes = {}
        for k, l in enumerate(self.regles):
            if filtre and filtre not in (l.libelle + " " + l.groupe + " " + l.aide).lower():
                continue
            g = groupes.get(l.groupe)
            if g is None:
                g = groupes[l.groupe] = a.insert("", "end", text=l.groupe, open=True if ouverts is None
                                                 else l.groupe in ouverts or bool(filtre), tags=("groupe",))
            v = self.valeurs_regles.get(l.cle)
            tags = ("erreur",) if l.cle in self.erreurs_regles else ("modifie",) if not RU._egal(v, l.origine) else ()
            a.insert(g, "end", iid=f"r{k}", text=l.libelle, values=(_mm(v), _mm(l.origine), l.aide), tags=tags)
        if self.entree is not None:
            classes = self.entree.get("classes") or {}
            if classes and (not filtre or "classe" in filtre):
                g = a.insert("", "end", text=f"Classes de réseaux ({len(classes)})", open=False, tags=("groupe",))
                for n, membres in sorted(classes.items()):
                    a.insert(g, "end", text=n, values=("", "", f"{len(membres)} réseau(x)"), tags=("info",))
            rg = self.entree.get("regles") or {}
            ni = rg.get("non_interpretees") or []
            if ni and (not filtre or "interpr" in filtre):
                g = a.insert("", "end", text=f"Règles du fichier non interprétées ({len(ni)})", open=False,
                             tags=("groupe",))
                for x in ni:
                    a.insert(g, "end", text=f"{x.get('type', '')} {x.get('nom', '')}".strip(),
                             values=("", "", x.get("cause", "")), tags=("info",))
        n = len(RU.modifiees(self.regles, self.valeurs_regles))
        if self.erreurs_regles:
            self.l_regles.configure(text=next(iter(self.erreurs_regles.values())), style="Erreur.TLabel")
        elif self.regles:
            self.l_regles.configure(text=f"{len(self.regles)} règle(s) lue(s), {n} modifiée(s)",
                                    style="Discret.TLabel")
        else:
            self.l_regles.configure(text="", style="Discret.TLabel")

    def _ligne_regle(self, iid):
        if not iid or not iid.startswith("r"):
            return None
        try:
            return self.regles[int(iid[1:])]
        except (ValueError, IndexError):
            return None

    def editer_regle(self, evenement=None) -> str | None:
        """Éditeur en place de la colonne Valeur (Entrée : valider, Échap : annuler)."""
        a = self.regles_arbre
        iid = a.focus() if evenement is None or evenement.type != "4" else a.identify_row(evenement.y)
        l = self._ligne_regle(iid)
        if l is None:
            return None
        self.fermer_editeur()
        a.see(iid)
        a.update_idletasks()
        boite = a.bbox(iid, "valeur")
        if not boite:
            return None
        x, y, w, h = boite
        e = self.ttk.Entry(a, justify="right")
        e.insert(0, _mm(self.valeurs_regles.get(l.cle)))
        e.select_range(0, "end")
        e.place(x=x, y=y, width=w, height=h)
        e.focus_set()
        self.editeur = (e, iid)
        e.bind("<Return>", lambda _e: self.valider_editeur())
        e.bind("<KP_Enter>", lambda _e: self.valider_editeur())
        e.bind("<Escape>", lambda _e: self.fermer_editeur())
        e.bind("<FocusOut>", lambda _e: self.valider_editeur())
        return "break"

    def fermer_editeur(self) -> None:
        if self.editeur is not None:
            e, _ = self.editeur
            self.editeur = None
            e.destroy()

    def valider_editeur(self) -> None:
        if self.editeur is None:
            return
        e, iid = self.editeur
        self.modifier_regle(self._ligne_regle(iid).cle, e.get())
        self.fermer_editeur()
        if self.regles_arbre.exists(iid):
            self.regles_arbre.selection_set(iid)
            self.regles_arbre.focus(iid)
        self.regles_arbre.focus_set()

    def modifier_regle(self, cle: str, texte: str) -> bool:
        """Valeur saisie pour une règle : contrôlée, puis cohérence de l'ensemble. Rend True si acceptée."""
        l = next((x for x in self.regles if x.cle == cle), None)
        if l is None:
            return False
        v, err = RU.lire_valeur(l, texte)
        self.erreurs_regles.pop(cle, None)
        if err:
            self.erreurs_regles[cle] = err
            self.remplir_regles()
            return False
        self.valeurs_regles[cle] = v
        self.erreurs_regles = {k: m for k, m in RU.verifier(self.regles, self.valeurs_regles)}
        self.remplir_regles()
        return cle not in self.erreurs_regles

    def regle_du_fichier(self) -> None:
        l = self._ligne_regle(self.regles_arbre.focus())
        if l is not None:
            self.modifier_regle(l.cle, _mm(l.origine))

    def reinitialiser_regles(self) -> None:
        self.valeurs_regles = RU.valeurs_origine(self.regles)
        self.erreurs_regles = {}
        self.remplir_regles()

    def contraintes_fenetre(self) -> str | None:
        """Fichier de contraintes temporaire (contraintes de l'utilisateur + règles modifiées) ; None si aucune
        règle n'est modifiée. Le fichier de la carte et celui de l'utilisateur ne sont jamais modifiés."""
        if self.entree is None:
            return None
        morceau = RU.surcharges(self.entree, self.regles, self.valeurs_regles)
        if not morceau:
            return None
        base = None
        cj = self.v_contraintes.get().strip()
        if cj:
            import formats
            base, _ = formats.lire_contraintes(cj)
        ct = RU.fusionner(base, morceau)
        tmp = chemins.dossier_temporaire()
        if not tmp:
            return None
        chemin = os.path.join(tmp, f"placeur_fenetre_{os.getpid()}_contraintes.json")
        chemins.ecrire_json(chemin, ct)
        self.fichiers_temporaires.append(chemin)
        return chemin

    def options(self) -> list[str]:
        """Options de placeur.py correspondant aux champs (les choix de la fenêtre l'emportent, §5.4)."""
        a = []
        v = self.contraintes_fenetre() or self.v_contraintes.get().strip()
        if v:
            a += ["--contraintes", v]
        v = self.v_sortie.get().strip()
        if v:
            a += ["--sortie", v]
        qualite = dict(QUALITES).get(self.v_qualite.get(), "normal")
        a += ["--qualite", qualite]
        for var, opt in ((self.v_duree, "--duree-max-min"), (self.v_essais, "--essais"),
                         (self.v_processeurs, "--processeurs"), (self.v_valides, "--valides-voulues")):
            if _nombre(var.get()):
                a += [opt, str(_nombre(var.get()))]
        if self.mode_routage() != "auto":
            a += ["--mode-routage", self.mode_routage()]
        for var, opt in ((self.v_pcbdoc, "--sans-pcbdoc"), (self.v_serigraphie, "--sans-serigraphie"),
                         (self.v_couture, "--sans-couture")):
            if not var.get():
                a.append(opt)
        if self.v_priorite.get():
            a.append("--priorite-normale")
        fixes = [p.strip() for p in self.v_fixes.get().split(",") if p.strip()]
        if fixes:
            tmp = chemins.dossier_temporaire()
            if tmp:
                ini = os.path.join(tmp, f"placeur_fenetre_{os.getpid()}.ini")
                parametres.enregistrer(ini, {"contraintes.fixes": fixes})
                a += ["--parametres", ini]
        return a

    def valeurs_modifiees(self) -> dict:
        """Réglages changés par l'utilisateur, pour « Enregistrer les paramètres » (décision 34)."""
        lecture = {
            "carte.fichier": lambda: self.carte(), "carte.contraintes": lambda: self.v_contraintes.get().strip(),
            "contraintes.fixes": lambda: [p.strip() for p in self.v_fixes.get().split(",") if p.strip()],
            "sortie.dossier": lambda: self.v_sortie.get().strip(),
            "calcul.qualite": lambda: dict(QUALITES).get(self.v_qualite.get(), "normal"),
            "calcul.duree_max_min": lambda: _nombre(self.v_duree.get()),
            "calcul.essais": lambda: _nombre(self.v_essais.get()),
            "calcul.processeurs": lambda: _nombre(self.v_processeurs.get()),
            "calcul.valides_voulues": lambda: _nombre(self.v_valides.get()),
            "calcul.mode_routage": self.mode_routage,
            "pcbdoc.produire": self.v_pcbdoc.get, "pcbdoc.serigraphie": self.v_serigraphie.get,
            "pcbdoc.vias_couture": self.v_couture.get, "sortie.ouvrir_a_la_fin": self.v_ouvrir.get,
            "calcul.priorite_normale": self.v_priorite.get,
        }
        return {k: lecture[k]() for k in sorted(self.modifies) if k in lecture}

    # ------------------------------------------------------------------------------------------ boutons
    def _verifier_carte(self) -> str | None:
        c = self.carte()
        if not c:
            self.informer(TITRE, "Choisir d'abord une carte (.PcbDoc ou .kicad_pcb).", True)
            return None
        if self.erreurs_regles:
            self.onglets.select(self.onglet_regles)
            self.informer(TITRE, "Règles à corriger : " + "; ".join(self.erreurs_regles.values()), True)
            return None
        return c

    def _arguments_source(self, c: str) -> list[str]:
        return ["--donnees", c] if c.lower().endswith(".json") else [c]

    def analyser(self) -> None:
        c = self._verifier_carte()
        if c and not self.en_cours():
            self.demarrer(self._arguments_source(c) + ["--analyser"] + self.options(), "analyse")

    def lancer(self) -> None:
        c = self._verifier_carte()
        if not c or self.en_cours():
            return
        d = self.inacheve()
        if d:
            from tkinter import messagebox
            if messagebox.askyesno(TITRE, "Un calcul inachevé existe pour cette carte. Le reprendre ?",
                                   parent=self.racine):
                self.demarrer(["--reprendre", d], "calcul")
                return
        self.demarrer(self._arguments_source(c) + self.options(), "calcul")

    def reprendre(self) -> None:
        d = self.inacheve()
        if d and not self.en_cours():
            self.demarrer(["--reprendre", d], "calcul")

    def demarrer(self, arguments: list[str], mode: str) -> None:
        """Lance l'orchestrateur avec --evenements dans un objet de travail et lit sa sortie dans un fil."""
        self.mode, self.sortie_calcul, self.fin, self.code, self.arret = mode, None, None, None, 0
        self.essais, self.t0, self.duree_max_s = 0, time.monotonic(), None
        self.mode_ignore = False
        for i in self.tableau.get_children():
            self.tableau.delete(i)
        self.barre.configure(value=0)
        self.l_pourcent.configure(text="0 %")
        cmd = self.commande(*arguments, "--evenements")
        self.job = chemins.Job()
        self.proc = chemins.lancer(cmd, stdout=chemins.PIPE, stderr=chemins.STDOUT, job=self.job,
                                   priorite_basse=False)
        proc = self.proc
        threading.Thread(target=_lire_sortie, args=(proc, self.file), daemon=True).start()
        self.b_lancer.configure(state="disabled")
        self.b_analyser.configure(state="disabled")
        self.b_reprendre.configure(state="disabled")
        self.b_arreter.configure(state="normal", text=TEXTE_ARRETER_1)
        self.onglets.select(self.onglet_calcul)
        self.etat("Analyse en cours…" if mode == "analyse" else "Calcul en cours…")

    def mode_de_graine(self, g) -> str:
        m = self.mode_routage()
        if self.mode_ignore or m == "auto":
            return "moteur"
        if m == "comparer":
            import placeur
            try:
                return placeur.MODES_COMPARES[(int(g) - 1) % len(placeur.MODES_COMPARES)]
            except (TypeError, ValueError):
                return ""
        return m

    def resultat_pret(self, valeur) -> None:
        dossier, scene = valeur
        if dossier != self.sortie_calcul or scene is None:
            return
        self.scene_resultat = scene
        self.b_vue_resultat.configure(state="normal")
        self.vue("resultat")
        self.onglets.select(self.onglet_apercu)

    def charger_resultat(self) -> None:
        import gc
        gc.collect()
        threading.Thread(target=_lire_resultat, args=(self.sortie_calcul, self.entree, self.file),
                         daemon=True).start()

    def resume(self) -> dict:
        """Ce que la fenêtre a lu (essais de fumée : --rapport)."""
        s = self.scene_origine.resume() if self.scene_origine is not None else {}
        return {"carte": os.path.basename(self.carte()) if self.carte() else None,
                "regles": len(self.regles), "pieces": s.get("pieces", 0), "verrouillees": s.get("verrouillees", 0),
                "cuivre": s.get("cuivre", 0), "textes": len(self.textes())}

    def arreter(self) -> None:
        """Premier clic : `arret_lancements` ; second clic : `arret_demande` ; sans dossier connu : arrêt forcé."""
        if not self.en_cours():
            return
        if self.sortie_calcul is None:
            self.tout_arreter()
            return
        nom = "arret_lancements" if self.arret == 0 else "arret_demande"
        try:
            open(os.path.join(self.sortie_calcul, nom), "a").close()
        except OSError as e:
            self.journal(f"arrêt impossible ({e.strerror or e}) : arrêt forcé")
            self.tout_arreter()
            return
        self.arret = 1 if nom == "arret_lancements" else 2
        if self.arret == 1:
            self.journal("Arrêt demandé : plus de nouvelle graine, les graines en cours finissent.")
            self.b_arreter.configure(text=TEXTE_ARRETER_2)
        else:
            self.journal("Arrêt de toutes les graines demandé.")
            self.b_arreter.configure(state="disabled")

    def tout_arreter(self) -> None:
        if self.job is not None:
            self.job.tout_arreter()

    def ouvrir_resultats(self) -> None:
        d = self.sortie_calcul or self.v_sortie.get().strip()
        if d and os.path.isdir(d):
            self.ouvrir(d)
        else:
            self.informer(TITRE, "Aucun dossier de résultats pour l'instant.")

    def enregistrer(self) -> None:
        from tkinter import filedialog
        c = self.carte()
        initial = parametres.ini_projet(c) if c and not c.lower().endswith(".json") else "parametres.ini"
        chemin = filedialog.asksaveasfilename(parent=self.racine, title="Enregistrer les paramètres",
                                              initialdir=os.path.dirname(initial) or None,
                                              initialfile=os.path.basename(initial), defaultextension=".ini",
                                              filetypes=[("Paramètres", "*.ini")])
        if chemin:
            parametres.enregistrer(chemin, self.valeurs_modifiees())
            self.journal(f"paramètres enregistrés : {os.path.basename(chemin)}")

    def contraintes_avancees(self) -> None:
        """Crée le modèle de contraintes (`--init`) s'il manque, puis l'ouvre dans le bloc-notes."""
        c = self._verifier_carte()
        if not c:
            return
        cj = self.v_contraintes.get().strip() or os.path.splitext(c)[0] + "_contraintes.json"
        if not os.path.isfile(cj):
            r = chemins.executer(self.commande("--init", c), delai_s=600, priorite_basse=False)
            self.journal((r.stdout or "").strip() or (r.stderr or "").strip())
        if os.path.isfile(cj):
            self.v_contraintes.set(cj)
            if chemins.WINDOWS:
                chemins.lancer(["notepad.exe", cj], priorite_basse=False)
            else:
                self.ouvrir(cj)

    def _outil_aide(self, option: str, titre: str, copier: bool = False) -> None:
        r = chemins.executer(self.commande(option), delai_s=300, priorite_basse=False)
        texte = ((r.stdout or "") + (r.stderr or "")).strip()
        if copier:
            self.racine.clipboard_clear()
            self.racine.clipboard_append(texte)
            self.informer(titre, "Diagnostic copié dans le presse-papiers.")
        else:
            self.informer(titre, texte or f"code {r.returncode}", r.returncode != 0)

    def auto_test(self) -> None:
        self._outil_aide("--auto-test", "Vérifier l'installation")

    def copier_diagnostic(self) -> None:
        self._outil_aide("--diagnostic", "Copier le diagnostic", copier=True)

    def mode_emploi(self) -> None:
        for nom in ("MODE_EMPLOI.html", "MODE_EMPLOI.md", os.path.join("placeur_routage", "MODE_EMPLOI.md")):
            try:
                chemin = chemins.ressource(nom)
            except (chemins.RessourceAbsente, ValueError):
                continue
            self.ouvrir(chemin)
            return
        self.informer(TITRE, "Mode d'emploi introuvable.", True)

    # ------------------------------------------------------------------------------------------ évènements
    def pomper(self) -> None:
        """Lit les lignes reçues de l'orchestrateur (appelé toutes les 100 ms)."""
        if self.ferme:
            return
        try:
            while True:
                genre, valeur = self.file.get_nowait()
                if genre == "ligne":
                    self.traiter_ligne(valeur)
                elif genre == "carte":
                    self.carte_lue_ok(valeur)
                elif genre == "resultat":
                    self.resultat_pret(valeur)
                else:
                    self.processus_termine(valeur)
        except queue.Empty:
            pass
        self.restant()
        if not self.ferme:
            self._apres = self.racine.after(PAS_LECTURE_MS, self.pomper)

    def traiter_ligne(self, ligne: str) -> None:
        if not ligne.startswith("EVT "):
            if ligne.strip():
                self.journal(ligne)
            return
        try:
            e = json.loads(ligne[4:])
        except ValueError:
            self.journal(ligne)
            return
        self.evenement(e)

    def _ligne_graine(self, g) -> str:
        iid = f"g{g}"
        if not self.tableau.exists(iid):
            self.tableau.insert("", "end", iid=iid, values=(g, "", "", "", "", "", self.mode_de_graine(g)))
        return iid

    def evenement(self, e: dict) -> None:
        t = e.get("type")
        if t == "debut":
            self.sortie_calcul = e.get("sortie")
            self.essais = int(e.get("essais") or 0)
            self.duree_max_s = e.get("duree_max_s")
            self.l_prevision.configure(text=f"Durée prévue : {_duree(e.get('duree_prevue_s'))} ; au plus : "
                                            f"{_duree(e.get('duree_max_s'))}")
            self.journal(f"{self.essais} graine(s), {e.get('paralleles')} en parallèle, niveau {e.get('qualite')}")
        elif t == "graine":
            iid = self._ligne_graine(e.get("graine"))
            v = list(self.tableau.item(iid, "values"))
            v[1] = e.get("etat", v[1])
            if e.get("duree_s") is not None:
                v[3] = f"{float(e['duree_s']):.1f}"
            if e.get("cout") is not None:
                v[4] = f"{float(e['cout']):.1f}"
            if e.get("liaisons_ouvertes") is not None:
                v[5] = e["liaisons_ouvertes"]
            self.tableau.item(iid, values=v)
            if e.get("texte"):
                self.journal(f"graine {e.get('graine')} : {e['texte']}")
            self.avancement()
        elif t == "phase":
            iid = self._ligne_graine(e.get("graine"))
            v = list(self.tableau.item(iid, "values"))
            f = e.get("fraction")
            v[2] = f"{e.get('phase', '')} {e.get('etat', '')}" + (f" {round(100 * f)} %" if isinstance(
                f, (int, float)) else "")
            self.tableau.item(iid, values=v)
        elif t == "etape":
            self.etat(f"Étape : {e.get('etape')} ({e.get('etat')})")
            if e.get("texte"):
                self.journal(f"{e.get('etape')} : {e['texte']}")
        elif t in ("avertissement", "erreur"):
            self.journal(f"{t} : {e.get('texte', '')}")
            if "mode de routage" in str(e.get("texte", "")) and "ignoré" in str(e.get("texte", "")):
                self.mode_ignore = True
                for i in self.tableau.get_children():
                    v = list(self.tableau.item(i, "values"))
                    self.tableau.item(i, values=v[:6] + ["moteur"])
        elif t == "fin":
            self.fin = e
            self.code = e.get("code")
            self.barre.configure(value=100)
            self.l_pourcent.configure(text="100 %")
            self.etat(f"Fin : code {e.get('code')} ; {e.get('texte', '')}" + (
                f" ; meilleure graine : {e['meilleure']}" if e.get("meilleure") is not None else ""))

    def finies(self) -> int:
        return sum(1 for i in self.tableau.get_children() if self.tableau.item(i, "values")[1] in ETATS_FINIS)

    def avancement(self) -> None:
        if self.essais > 0:
            p = min(100.0, 100.0 * self.finies() / self.essais)
            self.barre.configure(value=p)
            self.l_pourcent.configure(text=f"{round(p)} %")

    def restant(self) -> None:
        if not self.en_cours() or self.t0 is None:
            return
        ecoule = time.monotonic() - self.t0
        f = self.finies() / self.essais if self.essais else 0.0
        reste = ecoule * (1 - f) / f if f > 0 else None
        if isinstance(self.duree_max_s, (int, float)):
            borne = max(0.0, self.duree_max_s - ecoule)
            reste = borne if reste is None else min(reste, borne)
        self.l_restant.configure(text=f"Temps restant estimé : {_duree(reste)}")

    def processus_termine(self, code: int) -> None:
        self.code = code if self.code is None else self.code
        self.proc = None
        self.job = None
        self.b_lancer.configure(state="normal")
        self.b_analyser.configure(state="normal")
        self.b_arreter.configure(state="disabled", text=TEXTE_ARRETER_1)
        self.l_restant.configure(text="Temps restant estimé : 0 s")
        if self.fin is None:
            self.etat(f"Fin : code {code}")
        if self.mode == "analyse" and self.sortie_calcul:
            fixes = pieces_a_garder(self.sortie_calcul)
            if fixes and not self.v_fixes.get().strip():
                self.v_fixes.set(", ".join(fixes))
            rapport = _lire_json(os.path.join(self.sortie_calcul, "technique", "rapport.json")) or {}
            est = rapport.get("estimation") or {}
            self.journal(f"analyse : durée prévue d'une graine {_duree(est.get('duree_graine_prevue_s'))}, "
                         f"au plus {_duree(est.get('duree_max_s'))}")
        elif self.mode == "calcul" and self.sortie_calcul and self.v_ouvrir.get() and code in (0, 1, 3):
            try:
                self.ouvrir(self.sortie_calcul)
            except OSError:
                pass
        if self.mode == "calcul" and self.sortie_calcul:
            self.charger_resultat()
        self.actualiser()
        if self.fermer_a_la_fin:
            self.detruire()

    # ------------------------------------------------------------------------------------------ fermeture
    def _dialogue_fermeture(self) -> str:
        tk, ttk = self.tk, self.ttk
        choix = {"v": "annuler"}
        d = tk.Toplevel(self.racine)
        d.title(TITRE)
        d.transient(self.racine)
        d.configure(background=THEMES[self.nom_theme]["fond"])
        ttk.Label(d, text="Un calcul est en cours.", padding=10).pack()
        cadre = ttk.Frame(d, padding=6)
        cadre.pack()
        for cle, texte in CHOIX_FERMETURE:
            ttk.Button(cadre, text=texte, command=lambda c=cle: (choix.update(v=c), d.destroy())).pack(
                side="left", padx=4)
        d.protocol("WM_DELETE_WINDOW", d.destroy)
        d.grab_set()
        self.racine.wait_window(d)
        return choix["v"]

    def fermeture(self) -> None:
        if not self.en_cours():
            self.detruire()
            return
        choix = self.demander_fermeture()
        if choix == "annuler":
            return
        if choix == "tout":
            self.tout_arreter()
            self.detruire()
            return
        self.fermer_a_la_fin = True             # « Arrêter proprement » : arret_demande, fermeture à la fin
        if self.sortie_calcul:
            self.arret = 1
            self.arreter()
        else:
            self.tout_arreter()

    def detruire(self) -> None:
        if self.ferme:
            return
        self.ferme = True
        if self.job is not None:
            self.job.tout_arreter()
        for chemin in self.fichiers_temporaires:
            try:
                os.remove(chemin)
            except OSError:
                pass
        try:
            self.racine.after_cancel(self._apres)
        except Exception:  # noqa: BLE001 - déjà exécuté
            pass
        try:
            self.racine.destroy()
        except Exception:  # noqa: BLE001 - déjà détruite
            pass


def main(argv: list[str] | None = None, *, dossier_erreurs: str | None = None, fabrique=None) -> int:
    """Ouvre la fenêtre. `--essai-fenetre MS` : fermeture automatique après MS millisecondes (code 0) ;
    `--carte FICHIER` : carte choisie à l'ouverture ; `--theme clair|sombre` ; `--rapport R.json` (avec
    --essai-fenetre) : ce que la fenêtre a lu, écrit juste avant la fermeture. `fabrique` (essais) : remplace la
    classe Fenetre."""
    argv = sys.argv[1:] if argv is None else list(argv)
    essai = None
    valeurs = {}
    try:
        if "--essai-fenetre" in argv:
            i = argv.index("--essai-fenetre")
            essai = int(argv[i + 1])
            del argv[i:i + 2]
        for opt in ("--carte", "--rapport", "--theme"):
            if opt in argv:
                i = argv.index(opt)
                valeurs[opt] = argv[i + 1]
                del argv[i:i + 2]
        if valeurs.get("--theme", "clair") not in THEMES:
            raise ValueError("--theme : clair ou sombre")
        if argv:
            raise ValueError(f"option inconnue de la fenêtre : {' '.join(argv)}")
    except (ValueError, IndexError) as e:
        sys.stderr.write(f"erreur : {e if str(e) else '--essai-fenetre MS'}\n")
        return 2
    try:
        regler_dpi()
        import tkinter as tk
        racine = tk.Tk()
        try:
            if fabrique is None:
                f = Fenetre(racine, dossier_erreurs=dossier_erreurs, theme=valeurs.get("--theme", "clair"))
            else:
                f = fabrique(racine, dossier_erreurs=dossier_erreurs)
            if "--carte" in valeurs:
                f.v_carte.set(os.path.abspath(valeurs["--carte"]))
            if essai is not None:
                def finir():
                    if "--rapport" in valeurs:
                        chemins.ecrire_json(os.path.abspath(valeurs["--rapport"]), f.resume())
                    f.detruire()
                racine.after(essai, finir)
            racine.mainloop()
        finally:
            try:
                racine.destroy()
            except Exception:  # noqa: BLE001 - déjà détruite
                pass
        return 0
    except Exception:  # noqa: BLE001 - plantage avant le calcul : erreurs.txt (§5.5)
        chemin = ecrire_erreur(traceback.format_exc(), dossier_erreurs)
        sys.stderr.write("erreur de la fenêtre" + (f" : voir {chemins.anonymiser(chemin)}\n" if chemin else "\n"))
        return 4


if __name__ == "__main__":
    sys.exit(main())
