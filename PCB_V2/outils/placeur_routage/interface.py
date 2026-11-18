"""Fenêtre du placeur-routeur (tkinter, un écran ; §5.5, lot D).

La fenêtre ne calcule rien : elle lance `placeur … --evenements` (placeur.exe dans le paquet, `python lanceur.py`
en mode source) rattaché à un objet de travail Windows (`chemins.Job`), lit ses évènements (lignes `EVT {json}`,
§5.3) et les affiche : tableau par graine, barre d'avancement, journal, temps restant.

- Arrêter agit en deux temps : fichier `arret_lancements` (« Finir les graines en cours »), puis `arret_demande`
  (« Tout arrêter ») dans le dossier du calcul.
- Fermeture pendant un calcul : « Arrêter proprement », « Tout arrêter » ou « Annuler ».
- Reprendre : proposé quand un calcul inachevé existe pour la carte choisie (`placeur.calcul_inacheve`).
- Plantage avant le calcul : `%LOCALAPPDATA%\\PlaceurRouteur\\erreurs.txt`, anonymisé (`chemins.anonymiser`).
- `--essai-fenetre MS` : la fenêtre s'ouvre puis se ferme seule après MS millisecondes (CI).

Aucune variable d'environnement n'est lue (§0.2-2).
"""
from __future__ import annotations

import json
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

TITRE = "PlaceurRouteur"
QUALITES = (("Aperçu", "apercu"), ("Rapide", "rapide"), ("Normal", "normal"), ("Soigné", "soigne"))
COLONNES = (("graine", "Graine", 60), ("etat", "État", 90), ("phase", "Phase", 150), ("duree", "Durée (s)", 80),
            ("cout", "Coût", 90), ("ouvertes", "Liaisons ouvertes", 110))
ETATS_FINIS = ("terminee", "abandonnee", "plantee")
TEXTE_ARRETER_1 = "Arrêter : Finir les graines en cours"
TEXTE_ARRETER_2 = "Arrêter : Tout arrêter"
CHOIX_FERMETURE = (("proprement", "Arrêter proprement"), ("tout", "Tout arrêter"), ("annuler", "Annuler"))
NOTE_KICAD = ".kicad_pcb, sans repositionnement de la sérigraphie"
NOM_ERREURS = "erreurs.txt"
PAS_LECTURE_MS = 100


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


class Fenetre:
    """La fenêtre de §5.5. `commande(*args)` : commande de l'orchestrateur (par défaut `chemins.commande_placeur`) ;
    `demander_fermeture()` : « proprement », « tout » ou « annuler » ; `dossier_erreurs` : dossier de erreurs.txt ;
    `ouvrir(chemin)` : ouvre un dossier ou un fichier (Explorateur, bloc-notes)."""

    def __init__(self, racine, *, commande=None, demander_fermeture=None, dossier_erreurs=None, ouvrir=None,
                 informer=None):
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
        racine.title(TITRE)
        racine.protocol("WM_DELETE_WINDOW", self.fermeture)
        racine.report_callback_exception = self.erreur_rappel
        self._construire()
        self._apres = racine.after(PAS_LECTURE_MS, self.pomper)

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
        menu = tk.Menu(r)
        aide = tk.Menu(menu, tearoff=0)
        aide.add_command(label="Mode d'emploi", command=self.mode_emploi)
        aide.add_command(label="Vérifier l'installation", command=self.auto_test)
        aide.add_command(label="Copier le diagnostic", command=self.copier_diagnostic)
        menu.add_cascade(label="Aide", menu=aide)
        r.config(menu=menu)
        self.menu, self.menu_aide = menu, aide

        cadre = ttk.Frame(r, padding=8)
        cadre.grid(row=0, column=0, sticky="nsew")
        r.columnconfigure(0, weight=1)
        r.rowconfigure(0, weight=1)
        cadre.columnconfigure(1, weight=1)
        self.v_carte = self._var("carte.fichier")
        self.v_contraintes = self._var("carte.contraintes")
        self.v_fixes = self._var("contraintes.fixes")
        self.v_sortie = self._var("sortie.dossier")
        self.v_qualite = self._var("calcul.qualite", valeur="Normal")
        self.v_duree = self._var("calcul.duree_max_min", valeur="0")
        self.v_essais = self._var("calcul.essais", valeur="0")
        self.v_processeurs = self._var("calcul.processeurs", valeur="0")
        self.v_valides = self._var("calcul.valides_voulues", valeur="0")
        self.v_pcbdoc = self._var("pcbdoc.produire", "bool", True)
        self.v_serigraphie = self._var("pcbdoc.serigraphie", "bool", True)
        self.v_couture = self._var("pcbdoc.vias_couture", "bool", True)
        self.v_ouvrir = self._var("sortie.ouvrir_a_la_fin", "bool", True)
        self.v_priorite = self._var("calcul.priorite_normale", "bool", False)
        self.v_carte.trace_add("write", lambda *_: self.actualiser())

        ligne = 0

        def champ(texte, var, bouton=None, commande=None, autre=None):
            nonlocal ligne
            ttk.Label(cadre, text=texte).grid(row=ligne, column=0, sticky="w", pady=2)
            ttk.Entry(cadre, textvariable=var).grid(row=ligne, column=1, sticky="ew", padx=4)
            if bouton:
                ttk.Button(cadre, text=bouton, command=commande).grid(row=ligne, column=2, sticky="ew")
            if autre:
                ttk.Button(cadre, text=autre[0], command=autre[1]).grid(row=ligne, column=3, sticky="ew", padx=2)
            ligne += 1

        champ("Carte (.PcbDoc ou .kicad_pcb)", self.v_carte, "Parcourir…", self.choisir_carte)
        self.note_carte = ttk.Label(cadre, text="")
        self.note_carte.grid(row=ligne, column=1, sticky="w")
        ligne += 1
        champ("Contraintes (facultatif)", self.v_contraintes, "Parcourir…", self.choisir_contraintes,
              ("Contraintes avancées…", self.contraintes_avancees))
        champ("Pièces à garder en place", self.v_fixes)
        champ("Dossier de sortie", self.v_sortie, "Parcourir…", self.choisir_sortie)

        reglages = ttk.Frame(cadre)
        reglages.grid(row=ligne, column=0, columnspan=4, sticky="w", pady=4)
        ligne += 1
        ttk.Label(reglages, text="Qualité").pack(side="left")
        ttk.Combobox(reglages, textvariable=self.v_qualite, values=[q for q, _ in QUALITES], width=9,
                     state="readonly").pack(side="left", padx=4)
        for texte, var in (("Durée maximale (min, 0 : celle du niveau)", self.v_duree),
                           ("Essais (0 : ceux du niveau)", self.v_essais),
                           ("Processeurs (0 : tous sauf un)", self.v_processeurs)):
            ttk.Label(reglages, text=texte).pack(side="left", padx=(8, 0))
            ttk.Spinbox(reglages, textvariable=var, from_=0, to=9999, width=6).pack(side="left", padx=4)

        cases = ttk.Frame(cadre)
        cases.grid(row=ligne, column=0, columnspan=4, sticky="w")
        ligne += 1
        self.case_pcbdoc = ttk.Checkbutton(cases, text="Carte produite", variable=self.v_pcbdoc)
        self.case_pcbdoc.pack(side="left")
        for texte, var in (("Sérigraphie", self.v_serigraphie), ("Vias de couture", self.v_couture),
                           ("Ouvrir à la fin", self.v_ouvrir)):
            ttk.Checkbutton(cases, text=texte, variable=var).pack(side="left", padx=6)

        avance = ttk.LabelFrame(cadre, text="Réglages avancés", padding=4)
        avance.grid(row=ligne, column=0, columnspan=4, sticky="ew", pady=4)
        ligne += 1
        ttk.Label(avance, text="S'arrêter après K graines valides (0 : jamais)").pack(side="left")
        ttk.Spinbox(avance, textvariable=self.v_valides, from_=0, to=9999, width=6).pack(side="left", padx=4)
        ttk.Checkbutton(avance, text="Priorité normale", variable=self.v_priorite).pack(side="left", padx=8)

        boutons = ttk.Frame(cadre)
        boutons.grid(row=ligne, column=0, columnspan=4, sticky="w", pady=4)
        ligne += 1
        self.b_analyser = ttk.Button(boutons, text="Analyser la carte", command=self.analyser)
        self.b_lancer = ttk.Button(boutons, text="Lancer", command=self.lancer)
        self.b_arreter = ttk.Button(boutons, text=TEXTE_ARRETER_1, command=self.arreter, state="disabled")
        self.b_reprendre = ttk.Button(boutons, text="Reprendre", command=self.reprendre, state="disabled")
        self.b_resultats = ttk.Button(boutons, text="Ouvrir les résultats", command=self.ouvrir_resultats)
        self.b_enregistrer = ttk.Button(boutons, text="Enregistrer les paramètres", command=self.enregistrer)
        for b in (self.b_analyser, self.b_lancer, self.b_arreter, self.b_reprendre, self.b_resultats,
                  self.b_enregistrer):
            b.pack(side="left", padx=2)

        self.l_prevision = ttk.Label(cadre, text="Durée prévue : inconnue")
        self.l_prevision.grid(row=ligne, column=0, columnspan=4, sticky="w")
        ligne += 1
        self.tableau = ttk.Treeview(cadre, columns=[c for c, _, _ in COLONNES], show="headings", height=6)
        for c, texte, largeur in COLONNES:
            self.tableau.heading(c, text=texte)
            self.tableau.column(c, width=largeur, anchor="center")
        self.tableau.grid(row=ligne, column=0, columnspan=4, sticky="nsew")
        cadre.rowconfigure(ligne, weight=1)
        ligne += 1
        avancement = ttk.Frame(cadre)
        avancement.grid(row=ligne, column=0, columnspan=4, sticky="ew", pady=4)
        avancement.columnconfigure(0, weight=1)
        ligne += 1
        self.barre = ttk.Progressbar(avancement, mode="determinate", maximum=100)
        self.barre.grid(row=0, column=0, sticky="ew")
        self.l_pourcent = ttk.Label(avancement, text="0 %", width=6)
        self.l_pourcent.grid(row=0, column=1)
        self.l_restant = ttk.Label(avancement, text="Temps restant estimé : inconnu")
        self.l_restant.grid(row=0, column=2, padx=6)
        self.journal_texte = tk.Text(cadre, height=8, wrap="word", state="disabled")
        self.journal_texte.grid(row=ligne, column=0, columnspan=4, sticky="nsew")
        cadre.rowconfigure(ligne, weight=1)
        ligne += 1
        self.l_etat = ttk.Label(cadre, text="Prêt.")
        self.l_etat.grid(row=ligne, column=0, columnspan=4, sticky="w")

    # ------------------------------------------------------------------------------------------ outils
    def textes(self) -> list[str]:
        """Textes de tous les composants et du menu Aide (relevé des essais, §5.5)."""
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
            for e in w.winfo_children():
                parcourir(e)
        parcourir(self.racine)
        for m in (self.menu, self.menu_aide):
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
        """Note KiCad de la case « Carte produite » et bouton Reprendre."""
        c = self.carte()
        self.case_pcbdoc.configure(text="Carte produite (" + NOTE_KICAD + ")" if c.lower().endswith(".kicad_pcb")
                                   else "Carte produite")
        self.note_carte.configure(text=NOTE_KICAD if c.lower().endswith(".kicad_pcb") else "")
        if not self.en_cours():
            self.b_reprendre.configure(state="normal" if self.inacheve() else "disabled")

    def inacheve(self) -> str | None:
        c = self.carte()
        if not c or not os.path.isfile(c):
            return None
        try:
            import placeur
            return placeur.calcul_inacheve(c, self.v_sortie.get().strip() or None)
        except Exception:  # noqa: BLE001 - la proposition de reprise est facultative
            return None

    def options(self) -> list[str]:
        """Options de placeur.py correspondant aux champs (les choix de la fenêtre l'emportent, §5.4)."""
        a = []
        v = self.v_contraintes.get().strip()
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
        for i in self.tableau.get_children():
            self.tableau.delete(i)
        self.barre.configure(value=0)
        self.l_pourcent.configure(text="0 %")
        cmd = self.commande(*arguments, "--evenements")
        self.job = chemins.Job()
        self.proc = chemins.lancer(cmd, stdout=chemins.PIPE, stderr=chemins.STDOUT, job=self.job,
                                   priorite_basse=False)
        proc = self.proc
        threading.Thread(target=self._lire, args=(proc,), daemon=True).start()
        self.b_lancer.configure(state="disabled")
        self.b_analyser.configure(state="disabled")
        self.b_reprendre.configure(state="disabled")
        self.b_arreter.configure(state="normal", text=TEXTE_ARRETER_1)
        self.etat("Analyse en cours…" if mode == "analyse" else "Calcul en cours…")

    def _lire(self, proc) -> None:
        try:
            for ligne in proc.stdout:
                self.file.put(("ligne", ligne.rstrip("\r\n")))
        except (OSError, ValueError):
            pass
        self.file.put(("termine", proc.wait()))

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
            self.tableau.insert("", "end", iid=iid, values=(g, "", "", "", "", ""))
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
        try:
            self.racine.after_cancel(self._apres)
        except Exception:  # noqa: BLE001 - déjà exécuté
            pass
        try:
            self.racine.destroy()
        except Exception:  # noqa: BLE001 - déjà détruite
            pass


def main(argv: list[str] | None = None, *, dossier_erreurs: str | None = None, fabrique=None) -> int:
    """Ouvre la fenêtre. `--essai-fenetre MS` : fermeture automatique après MS millisecondes (code 0).
    `fabrique` (essais) : remplace la classe Fenetre."""
    argv = sys.argv[1:] if argv is None else list(argv)
    essai = None
    try:
        if "--essai-fenetre" in argv:
            i = argv.index("--essai-fenetre")
            essai = int(argv[i + 1])
            del argv[i:i + 2]
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
            f = (fabrique or Fenetre)(racine, dossier_erreurs=dossier_erreurs)
            if essai is not None:
                racine.after(essai, f.detruire)
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
