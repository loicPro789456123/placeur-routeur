# -*- coding: utf-8 -*-
"""Tests du lanceur et de chemins.py (lot D ; contrat §5.2, §5.3, §5.5, §0.2-2, §0.2-5, §0.2-10, §0.3, §7.2).

    cd PCB_V2/outils/placeur_routage
    python -m unittest test_lanceur -v

Classe TestChemins : anonymisation, objet de travail, priorité, environnement des sous-processus, verrou,
sémaphore de session, écriture atomique, sorties UTF-8, modes source et figé, motifs d'anonymat, import léger.
Les processus lancés par les essais sont tous arrêtés à la fin (objets de travail, nettoyage). Aucun chemin de la
machine n'est écrit dans ce fichier : les chemins sont construits à l'exécution.
"""
from __future__ import annotations

import ast
import getpass
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import chemins  # noqa: E402

WINDOWS = os.name == "nt"
INTERDITS = {"numpy", "matplotlib", "scipy"}


def attendre(condition, delai_s: float, pas: float = 0.05) -> bool:
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(pas)
    return bool(condition())


def lire_json(chemin):
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


# Scripts des processus enfants (écrits dans un dossier temporaire, précédés de l'accès à chemins).

ENFANT_ET_PETIT_ENFANT = """
    import json, os, subprocess, sys, time
    sortie = sys.argv[1]
    petit = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(sortie + ".part", "w", encoding="utf-8") as f:
        json.dump({"enfant": os.getpid(), "petit": petit.pid}, f)
    os.replace(sortie + ".part", sortie)
    time.sleep(120)
"""

PARENT_AVEC_JOB = """
    import json, os, signal, sys, time
    import chemins
    sortie, script_enfant, sortie_enfant = sys.argv[1:4]
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(3))
    job = chemins.Job()
    enfant = chemins.lancer([sys.executable, script_enfant, sortie_enfant], job=job)
    with open(sortie + ".part", "w", encoding="utf-8") as f:
        json.dump({"parent": os.getpid()}, f)
    os.replace(sortie + ".part", sortie)
    time.sleep(120)
"""

PARENT_IMBRIQUE = """
    import json, os, sys, time
    import chemins
    sortie = sys.argv[1]
    resultat = {"parent": os.getpid(), "enfants": [], "erreur": ""}
    try:
        job = chemins.Job()
        for _ in range(2):
            p = chemins.lancer([sys.executable, "-c", "import time; time.sleep(120)"], job=job)
            resultat["enfants"].append(p.pid)
        resultat["pids"] = job.pids()
    except Exception as e:
        resultat["erreur"] = repr(e)
    with open(sortie + ".part", "w", encoding="utf-8") as f:
        json.dump(resultat, f)
    os.replace(sortie + ".part", sortie)
    time.sleep(120)
"""

TENIR_VERROU = """
    import sys, time
    import chemins
    verrou = chemins.Verrou(sys.argv[1])
    pris = verrou.acquerir()
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write("pris" if pris else "refuse")
    time.sleep(120 if pris else 0)
"""

PRENDRE_PLACE = """
    import json, sys, time
    import chemins
    nom, places, tenue_s, sortie = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
    semaphore = chemins.SemaphoreMoteurs(places, nom=nom)
    place = semaphore.prendre(delai_s=60)
    debut = time.time()
    if sortie.endswith(".marque"):
        with open(sortie, "w", encoding="utf-8") as f:
            f.write(str(place))
    time.sleep(tenue_s)
    fin = time.time()
    semaphore.rendre(place)
    semaphore.fermer()
    if not sortie.endswith(".marque"):
        with open(sortie, "w", encoding="utf-8") as f:
            json.dump({"place": place, "debut": debut, "fin": fin}, f)
"""

CODE_PRIORITE = (
    "import ctypes; from ctypes import wintypes as w; k = ctypes.WinDLL('kernel32'); "
    "k.GetCurrentProcess.restype = w.HANDLE; k.GetPriorityClass.argtypes = [w.HANDLE]; "
    "print(k.GetPriorityClass(k.GetCurrentProcess()))"
    if WINDOWS else "import os; print(os.nice(0))"
)


class TestChemins(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_chemins_")
        self.processus = []
        chemins.oublier_sorties()

    def tearDown(self):
        for p in self.processus:
            if p.poll() is None:
                chemins._tuer(p)
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        chemins.oublier_sorties()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -------------------------------------------------------------------------------------------- outils des essais

    def script(self, nom: str, corps: str) -> str:
        chemin = os.path.join(self.tmp, nom)
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(f"import sys\nsys.path.insert(0, {ICI!r})\n" + textwrap.dedent(corps))
        return chemin

    def lancer(self, cmd, **options):
        p = chemins.lancer(cmd, **options)
        self.processus.append(p)
        return p

    def tous_morts(self, pids) -> bool:
        return not any(chemins.processus_vivant(pid) for pid in pids)

    # -------------------------------------------------------------------------------------------- anonymat

    def test_anonymiser_sortie_profil_machine_json(self):
        profil = chemins.dossier_connu("profil")
        self.assertTrue(profil and os.path.isabs(profil))
        sortie = os.path.join(profil, "Documents", "cartes", "essai_resultats", "carte_20261118-101500")
        machine = platform.node() or socket.gethostname()
        lecteur_autre = os.path.splitdrive(ICI)[0] + os.sep if WINDOWS else os.sep
        autre = os.path.join(lecteur_autre, "autre dossier", "données", "fichier.txt")
        # la casse n'est ignorée que sous Windows : ailleurs, la ligne 2 répète la ligne 0
        majuscules = sortie.upper() if WINDOWS else sortie
        texte = "\n".join([
            "écriture de " + os.path.join(sortie, "technique", "rapport.json"),
            "même dossier écrit autrement : " + sortie.replace(os.sep, "/") + "/graine_3/stats.json",
            "en majuscules : " + majuscules + os.sep + "journal.txt",
            "profil : " + os.path.join(profil, "Desktop", "carte.PcbDoc"),
            "machine " + machine + " ; partage \\\\" + machine + "\\partage\\f.txt",
            "trace : File \"" + autre + "\", line 3",
            json.dumps({"p": os.path.join(sortie, "graine_3", "stats.json"),
                        "q": os.path.join(profil, "a b", "c.json"), "r": autre}),
        ])
        res = chemins.anonymiser(texte, sortie=sortie)
        lignes = res.split("\n")
        self.assertEqual(lignes[0], "écriture de " + os.path.join(".", "technique", "rapport.json"))
        self.assertEqual(lignes[1], "même dossier écrit autrement : ./graine_3/stats.json")
        self.assertEqual(lignes[2], "en majuscules : " + os.path.join(".", "journal.txt"))
        self.assertEqual(lignes[3], "profil : " + os.path.join("%USERPROFILE%", "Desktop", "carte.PcbDoc"))
        self.assertIn("<machine>", lignes[4])
        d = json.loads(lignes[6])
        self.assertEqual(d["p"], os.path.join(".", "graine_3", "stats.json"))
        self.assertEqual(d["q"], os.path.join("%USERPROFILE%", "a b", "c.json"))
        if WINDOWS:
            self.assertEqual(d["r"], "<chemin>\\fichier.txt")
            self.assertIn('File "<chemin>\\fichier.txt", line 3', lignes[5])
        # plus rien qui désigne la machine ou l'utilisateur
        self.assertNotIn(os.path.normcase(profil), os.path.normcase(res))
        self.assertNotIn(profil.replace("\\", "\\\\").lower(), res.lower())
        for nom in {machine, socket.gethostname(), getpass.getuser(), chemins.nom_utilisateur()}:
            if nom and len(nom) >= 3:
                self.assertIsNone(re.search(r"(?<!\w)" + re.escape(nom) + r"(?!\w)", res, re.IGNORECASE), nom)
        if WINDOWS:
            self.assertIsNone(chemins.MOTIF_LECTEUR.search(res))
        self.assertEqual(chemins.chercher_motifs(res, lecteur=True), [])
        self.assertEqual(chemins.anonymiser(res, sortie=sortie), res, "anonymiser deux fois ne change rien")

    def test_anonymiser_sortie_declaree_et_cas_limites(self):
        sortie = os.path.join(self.tmp, "carte_resultats", "carte_20261118-101500")
        os.makedirs(sortie)
        chemins.declarer_sortie(sortie)
        trace = "Traceback : FileNotFoundError: [Errno 2] '" + os.path.join(sortie, "graine_1", "x.json") + "'"
        res = chemins.anonymiser(trace)
        self.assertIn("'" + os.path.join(".", "graine_1", "x.json") + "'", res)
        self.assertEqual(chemins.anonymiser(None), "")
        self.assertEqual(chemins.anonymiser(""), "")
        self.assertEqual(chemins.anonymiser("rien à cacher : 3/4, a:b, http://exemple"),
                         "rien à cacher : 3/4, a:b, http://exemple")
        # un dossier voisin dont le nom commence comme la sortie n'est pas pris pour elle
        res = chemins.anonymiser(sortie + "_bis" + os.sep + "f.txt")
        self.assertFalse(res.startswith("."), res)
        self.assertIn("carte_20261118-101500_bis" + os.sep + "f.txt", res)
        if WINDOWS:   # dossiers connus du profil : leur variable (§0.3, règle de rédaction)
            local = chemins.dossier_connu("localappdata")
            itinerant = chemins.dossier_connu("appdata")
            self.assertEqual(chemins.anonymiser(os.path.join(tempfile.gettempdir(), "x.txt")), "%TEMP%\\x.txt")
            self.assertEqual(chemins.anonymiser(os.path.join(local, "PlaceurRouteur", "erreurs.txt")),
                             "%LOCALAPPDATA%\\PlaceurRouteur\\erreurs.txt")
            self.assertEqual(chemins.anonymiser(os.path.join(itinerant, "PlaceurRouteur", "parametres.ini")),
                             "%APPDATA%\\PlaceurRouteur\\parametres.ini")
            self.assertEqual(chemins.anonymiser(sortie.upper()), ".")

    # -------------------------------------------------------------------------------------------- objet de travail

    def test_job_tue_enfant_et_petit_enfant(self):
        fichier = os.path.join(self.tmp, "pids.json")
        script = self.script("enfant.py", ENFANT_ET_PETIT_ENFANT)
        with chemins.Job() as job:
            p = self.lancer([sys.executable, script, fichier], job=job)
            self.assertTrue(attendre(lambda: os.path.exists(fichier), 30), "l'enfant n'a pas démarré")
            pids = lire_json(fichier)
            self.assertTrue(chemins.processus_vivant(pids["enfant"]))
            self.assertTrue(chemins.processus_vivant(pids["petit"]))
            if WINDOWS:   # le petit-enfant appartient à l'objet (hôte de console conhost.exe en plus)
                self.assertLessEqual({pids["enfant"], pids["petit"]}, set(job.pids()))
                self.assertGreaterEqual(job.nombre_actifs(), 2)
            else:
                self.assertEqual(job.pids(), [p.pid])
            debut = time.monotonic()
            job.tout_arreter()
            morts = attendre(lambda: p.poll() is not None and self.tous_morts(pids.values()), 5, 0.02)
            duree = time.monotonic() - debut
        self.assertTrue(morts, "enfant ou petit-enfant encore vivant")
        self.assertLessEqual(duree, 2.0)

    def test_job_parent_arrete_de_force(self):
        """Orchestrateur arrêté de force (Windows : TerminateProcess ; ailleurs : SIGTERM) : les processus de son
        objet de travail, enfant et petit-enfant, meurent avec lui en moins de 2 s."""
        fichier_parent = os.path.join(self.tmp, "parent.json")
        fichier_enfant = os.path.join(self.tmp, "enfant.json")
        enfant = self.script("enfant.py", ENFANT_ET_PETIT_ENFANT)
        parent = self.script("parent.py", PARENT_AVEC_JOB)
        p = self.lancer([sys.executable, parent, fichier_parent, enfant, fichier_enfant])
        self.assertTrue(attendre(lambda: os.path.exists(fichier_enfant), 30), "l'enfant n'a pas démarré")
        pids = lire_json(fichier_enfant)
        self.assertTrue(attendre(lambda: os.path.exists(fichier_parent), 10))
        self.assertTrue(chemins.processus_vivant(pids["enfant"]) and chemins.processus_vivant(pids["petit"]))
        debut = time.monotonic()
        p.terminate()
        morts = attendre(lambda: self.tous_morts(pids.values()), 5, 0.02)
        duree = time.monotonic() - debut
        if not morts:   # nettoyage avant l'échec
            for pid in pids.values():
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
        self.assertTrue(morts, "processus restés vivants après l'arrêt du parent")
        self.assertLessEqual(duree, 2.0)

    @unittest.skipUnless(WINDOWS, "objets de travail imbriqués : Windows seulement")
    def test_job_imbrique(self):
        """La fenêtre tient l'orchestrateur dans un objet de travail ; l'orchestrateur y crée le sien pour ses
        moteurs (Windows 8 et suivants) ; tout s'arrête avec l'objet de la fenêtre."""
        fichier = os.path.join(self.tmp, "imbrique.json")
        script = self.script("imbrique.py", PARENT_IMBRIQUE)
        with chemins.Job() as job:
            self.lancer([sys.executable, script, fichier], job=job)
            self.assertTrue(attendre(lambda: os.path.exists(fichier), 30))
            d = lire_json(fichier)
            self.assertEqual(d["erreur"], "")
            self.assertEqual(len(d["enfants"]), 2)
            self.assertLessEqual(set(d["enfants"]), set(d["pids"]), "moteurs absents de l'objet imbriqué")
            pids = [d["parent"]] + d["enfants"]
            self.assertLessEqual(set(pids), set(job.pids()), "processus absents de l'objet de la fenêtre")
            debut = time.monotonic()
            job.tout_arreter()
            self.assertTrue(attendre(lambda: self.tous_morts(pids), 5, 0.02))
            self.assertLessEqual(time.monotonic() - debut, 2.0)

    # -------------------------------------------------------------------------------------------- sous-processus

    def test_priorite_inferieure_a_la_normale(self):
        r = chemins.executer([sys.executable, "-c", CODE_PRIORITE])
        self.assertEqual(r.returncode, 0, r.stderr)
        valeur = int(r.stdout.strip())
        if WINDOWS:
            self.assertEqual(valeur, chemins.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            self.assertGreaterEqual(valeur, 10)
        r = chemins.executer([sys.executable, "-c", CODE_PRIORITE], priorite_basse=False)
        valeur = int(r.stdout.strip())
        if WINDOWS and chemins.priorite_processus() == chemins.NORMAL_PRIORITY_CLASS:
            self.assertEqual(valeur, chemins.NORMAL_PRIORITY_CLASS)
        elif not WINDOWS:
            self.assertEqual(valeur, os.nice(0))

    def test_environnement_reduit(self):
        ajoutees = {"PLACEUR_ESSAI_VARIABLE": "1", "PYTHONPATH": "rien", "RAYON_NUM_THREADS": "3",
                    "RUST_MIN_STACK": "1", "PLACEUR_MOTEURS": "rien"}
        with mock.patch.dict(os.environ, ajoutees):
            r = chemins.executer([sys.executable, "-c", "import json, os; print(json.dumps(sorted(os.environ)))"])
        self.assertEqual(r.returncode, 0, r.stderr)
        cles = {c.upper() for c in json.loads(r.stdout)}
        self.assertFalse(cles & set(ajoutees), "variable de réglage transmise à un sous-processus")
        self.assertLessEqual(cles, {v.upper() for v in chemins.variables_transmises()})
        complet = chemins.environnement_sous_processus(complet=True)
        self.assertGreaterEqual(len(complet), len(cles))

    def test_sortie_accentuee_relue_en_utf8(self):
        code = ("import sys; sys.stdout.buffer.write('é à ç œ € ✓ ü'.encode('utf-8') + b'\\n' + b'\\xe9t\\xe9\\n'); "
                "sys.stdout.flush(); sys.stderr.buffer.write('erreur accentuée'.encode('utf-8'))")
        r = chemins.executer([sys.executable, "-c", code])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "é à ç œ € ✓ ü\n�t�\n")
        self.assertEqual(r.stderr, "erreur accentuée")

    def test_executer_delai_depasse(self):
        debut = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            chemins.executer([sys.executable, "-c", "import time; time.sleep(60)"], delai_s=1.0)
        self.assertLess(time.monotonic() - debut, 15)

    def test_processus_vivant(self):
        self.assertTrue(chemins.processus_vivant(os.getpid()))
        p = self.lancer([sys.executable, "-c", "pass"])
        p.wait(timeout=30)
        self.assertFalse(chemins.processus_vivant(p.pid))

    # -------------------------------------------------------------------------------------------- verrou et sémaphore

    def test_verrou_exclusif_entre_processus(self):
        chemin = os.path.join(self.tmp, ".verrou")
        script = self.script("verrou.py", TENIR_VERROU)
        marque_a = os.path.join(self.tmp, "a.txt")
        a = self.lancer([sys.executable, script, chemin, marque_a])
        self.assertTrue(attendre(lambda: os.path.exists(marque_a), 30))
        with open(marque_a, encoding="utf-8") as f:
            self.assertEqual(f.read(), "pris")
        # un second processus est refusé, ce processus-ci aussi
        marque_b = os.path.join(self.tmp, "b.txt")
        b = self.lancer([sys.executable, script, chemin, marque_b])
        b.wait(timeout=30)
        with open(marque_b, encoding="utf-8") as f:
            self.assertEqual(f.read(), "refuse")
        self.assertFalse(chemins.Verrou(chemin).acquerir())
        with self.assertRaises(chemins.VerrouOccupe):
            with chemins.Verrou(chemin):
                pass
        # le premier meurt : le système libère son verrou, un nouveau processus est admis
        a.kill()
        a.wait(timeout=30)
        marque_c = os.path.join(self.tmp, "c.txt")

        def admis():
            if os.path.exists(marque_c):
                os.remove(marque_c)
            c = self.lancer([sys.executable, script, chemin, marque_c])
            if not attendre(lambda: os.path.exists(marque_c), 30):
                return False
            with open(marque_c, encoding="utf-8") as f:
                pris = f.read() == "pris"
            if pris:
                c.kill()
                c.wait(timeout=30)
            return pris

        self.assertTrue(attendre(admis, 5, 0.2), "verrou non libéré après la mort du premier processus")
        # dans un même processus, deux instances s'excluent aussi
        v1, v2 = chemins.Verrou(chemin), chemins.Verrou(chemin)
        self.assertTrue(attendre(v1.acquerir, 5))
        self.assertFalse(v2.acquerir())
        v1.liberer(supprimer=True)
        self.assertTrue(v2.acquerir())
        v2.liberer()

    def test_semaphore_deux_places_sur_trois_processus(self):
        nom = f"PlaceurRouteur_essai_{os.getpid()}_{int(time.time() * 1000)}"
        script = self.script("place.py", PRENDRE_PLACE)
        sorties = [os.path.join(self.tmp, f"place_{i}.json") for i in range(3)]
        procs = [self.lancer([sys.executable, script, nom, "2", "2.0", s]) for s in sorties]
        for p in procs:
            p.wait(timeout=90)
            self.assertEqual(p.returncode, 0)
        intervalles = [lire_json(s) for s in sorties]
        self.assertTrue(all(i["place"] in (0, 1) for i in intervalles), intervalles)
        evenements = sorted([(i["debut"], 1) for i in intervalles] + [(i["fin"], -1) for i in intervalles],
                            key=lambda e: (e[0], e[1]))
        courant = maximum = 0
        for _, delta in evenements:
            courant += delta
            maximum = max(maximum, courant)
        self.assertEqual(maximum, 2, f"jamais plus de 2 processus sur 3 (relevé : {maximum})")
        for i, x in enumerate(intervalles):
            for y in intervalles[i + 1:]:
                if x["debut"] < y["fin"] and y["debut"] < x["fin"]:
                    self.assertNotEqual(x["place"], y["place"], "deux processus sur la même place")

    def test_semaphore_place_rendue_si_processus_tue(self):
        nom = f"PlaceurRouteur_essai_{os.getpid()}_{int(time.time() * 1000)}_tue"
        script = self.script("place.py", PRENDRE_PLACE)
        marque = os.path.join(self.tmp, "tenant.marque")
        p = self.lancer([sys.executable, script, nom, "1", "120", marque])
        self.assertTrue(attendre(lambda: os.path.exists(marque), 30))
        with chemins.SemaphoreMoteurs(1, nom=nom) as semaphore:
            self.assertIsNone(semaphore.prendre(delai_s=0.3))
            p.kill()
            p.wait(timeout=30)
            place = semaphore.prendre(delai_s=5)
            self.assertEqual(place, 0, "place non rendue après la mort de son tenant")
            self.assertIsNone(semaphore.prendre(delai_s=0), "une place ne se prend pas deux fois")
            semaphore.rendre(place)
            self.assertEqual(semaphore.tenues(), [])

    def test_veille_empechee(self):
        self.assertEqual(chemins.empecher_veille(True), WINDOWS)
        self.assertEqual(chemins.empecher_veille(False), WINDOWS)

    # -------------------------------------------------------------------------------------------- écritures

    def test_ecriture_atomique_reessayee_sur_permissionerror(self):
        cible = os.path.join(self.tmp, "rapport.json")
        vrai_remplacement = os.replace
        appels = []

        def remplacer(a, b):
            appels.append(1)
            if len(appels) <= 2:
                raise PermissionError(13, "fichier tenu par un antivirus (simulé)")
            return vrai_remplacement(a, b)

        with mock.patch.object(chemins.os, "replace", side_effect=remplacer):
            debut = time.monotonic()
            chemins.ecrire_json(cible, {"texte": "accentué"})
            duree = time.monotonic() - debut
        self.assertEqual(len(appels), 3)
        self.assertGreaterEqual(duree, 2 * chemins.PAUSE_ECRITURE_S - 0.05)
        with open(cible, "rb") as f:
            octets = f.read()
        self.assertEqual(json.loads(octets.decode("utf-8")), {"texte": "accentué"})
        self.assertFalse(octets.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", octets)
        self.assertFalse(os.path.exists(cible + ".tmp"))
        # échec permanent : 1 essai + 5 réessais, cible intacte, fichier temporaire effacé
        appels.clear()
        with mock.patch.object(chemins.os, "replace", side_effect=PermissionError(13, "tenu")) as faux:
            with self.assertRaises(PermissionError):
                chemins.ecrire_json(cible, {"texte": "nouveau"})
        self.assertEqual(faux.call_count, chemins.ESSAIS_ECRITURE)
        self.assertEqual(lire_json(cible), {"texte": "accentué"})
        self.assertFalse(os.path.exists(cible + ".tmp"))

    @unittest.skipUnless(WINDOWS, "fichier tenu sans partage de suppression : Windows seulement")
    def test_ecriture_atomique_fichier_tenu_par_un_autre(self):
        cible = os.path.join(self.tmp, "parametres_utilises.ini")
        chemins.ecrire_texte_bloc_notes(cible, "[calcul]\nqualite = normal\n")
        tenu = open(cible, "rb")
        minuterie = threading.Timer(0.5, tenu.close)
        minuterie.start()
        try:
            debut = time.monotonic()
            chemins.ecrire_texte_bloc_notes(cible, "[calcul]\nqualite = soigné\n")
            duree = time.monotonic() - debut
        finally:
            minuterie.join()
            tenu.close()
        self.assertGreaterEqual(duree, 0.3)
        with open(cible, "rb") as f:
            self.assertEqual(f.read(), "﻿[calcul]\r\nqualite = soigné\r\n".encode("utf-8"))

    # -------------------------------------------------------------------------------------------- modes et version

    def test_mode_source(self):
        self.assertFalse(chemins.est_fige())
        version = chemins.ressource("VERSION")
        self.assertEqual(os.path.normcase(version), os.path.normcase(os.path.join(chemins.OUTILS, "VERSION")))
        with open(version, "rb") as f:
            self.assertEqual(f.read(), b"2.0.0")
        self.assertRegex(chemins.version_lanceur(), r"^2\.0\.0\+(?:[0-9a-f]{7}|inconnu)$")
        self.assertEqual(chemins.commande_outil("formats", "valider", "x.json"),
                         [chemins.interpreteur(), os.path.join(ICI, "lanceur.py"), "--outil", "formats", "valider",
                          "x.json"])
        with self.assertRaises(ValueError):
            chemins.commande_outil("inconnu")
        with self.assertRaises(ValueError):
            chemins.chemin_moteur("autre")
        for nom in ("../VERSION", os.path.abspath(version), ""):
            with self.assertRaises(ValueError):
                chemins.ressource(nom)
        # --moteurs : dossier explicite, sans construction
        exe = os.path.join(self.tmp, chemins.nom_executable("placeur_routage"))
        open(exe, "wb").close()
        self.assertEqual(chemins.chemin_moteur("placeur_routage", dossier=self.tmp), exe)
        with self.assertRaises(chemins.MoteurAbsent) as cm:
            chemins.chemin_moteur("serigraphie", dossier=self.tmp)
        self.assertIn(chemins.MESSAGE_MOTEUR_ABSENT, str(cm.exception))
        self.assertNotIn(self.tmp, str(cm.exception))
        # construire=False : jamais de cargo (--essais 0, --analyser)
        with mock.patch.object(chemins, "construire_moteur", side_effect=AssertionError("cargo lancé")):
            attendu = os.path.join(chemins.OUTILS, "placeur_routage", "target", "release",
                                   chemins.nom_executable("placeur_routage"))
            if os.path.isfile(attendu):
                self.assertEqual(chemins.chemin_moteur("placeur_routage", construire=False), attendu)
            else:
                with self.assertRaises(chemins.MoteurAbsent):
                    chemins.chemin_moteur("placeur_routage", construire=False)

    def test_construction_du_moteur_simulee(self):
        """Mode source, sans lancer cargo : commande, PLACEUR_COMMIT, cargo absent, échec de compilation."""
        crate = os.path.join(self.tmp, "placeur_routage")
        os.makedirs(os.path.join(crate, "target", "release"))
        exe = os.path.join(crate, "target", "release", chemins.nom_executable("placeur_routage"))
        appels = []

        def faux_cargo(cmd, **options):
            appels.append((cmd, options))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        messages = []
        with mock.patch.object(chemins, "OUTILS", self.tmp), mock.patch.object(chemins, "executer", faux_cargo):
            with self.assertRaises(chemins.MoteurAbsent):        # cargo « réussit » mais rien n'est construit
                chemins.chemin_moteur("placeur_routage", journal=messages.append)
            open(exe, "wb").close()
            self.assertEqual(chemins.chemin_moteur("placeur_routage", journal=messages.append), exe)
        cmd, options = appels[-1]
        self.assertEqual(cmd[:4], ["cargo", "build", "--release", "--offline"])
        self.assertEqual(options["cwd"], crate)
        # sous le faux cargo, « git rev-parse » ne rend rien : commit inconnu, jamais gardé en mémoire (sinon les
        # tests suivants du même processus, dont test_placeur.TestFauxMoteur, liraient « inconnu »)
        self.assertEqual(options["environnement"]["PLACEUR_COMMIT"], chemins.COMMIT_INCONNU)
        self.assertNotIn(chemins.COMMIT_INCONNU, chemins._COMMITS.values())
        self.assertTrue(any("cargo build" in m for m in messages))
        with mock.patch.object(chemins, "OUTILS", self.tmp), \
                mock.patch.object(chemins, "executer", side_effect=FileNotFoundError("cargo")):
            messages.clear()
            self.assertEqual(chemins.chemin_moteur("placeur_routage", journal=messages.append), exe)
            self.assertTrue(any("cargo introuvable" in m for m in messages))
            os.remove(exe)
            with self.assertRaises(chemins.MoteurAbsent) as cm:
                chemins.chemin_moteur("placeur_routage")
            self.assertIn("cargo est introuvable", str(cm.exception))
        echec = subprocess.CompletedProcess([], 101, "", "error[E0425]: introuvable\n --> " + os.path.join(
            crate, "src", "main.rs") + ":3:5\n")
        with mock.patch.object(chemins, "OUTILS", self.tmp), mock.patch.object(chemins, "executer",
                                                                               return_value=echec):
            with self.assertRaises(chemins.ErreurConstruction) as cm:
                chemins.chemin_moteur("placeur_routage")
        self.assertIn("code 101", str(cm.exception))
        self.assertIn("main.rs", str(cm.exception))
        self.assertEqual(chemins.chercher_motifs(str(cm.exception), lecteur=True), [])
        if WINDOWS:   # ailleurs, seuls la sortie et le profil sont réduits (§0.3) : /tmp n'en fait pas partie
            self.assertNotIn(self.tmp, str(cm.exception), "message de construction non anonymisé")

    def test_mode_fige_simule(self):
        paquet = os.path.join(self.tmp, "PlaceurRouteur")
        interne = os.path.join(paquet, "_internal")
        os.makedirs(os.path.join(interne, "moteurs"))
        for nom in (chemins.NOM_EXE_CONSOLE, chemins.NOM_EXE_FENETRE):
            open(os.path.join(paquet, nom), "wb").close()
        moteur = os.path.join(interne, "moteurs", chemins.nom_executable("placeur_routage"))
        open(moteur, "wb").close()
        for chemin, texte in ((os.path.join(interne, "VERSION"), "2.0.0\n"),
                              (os.path.join(interne, "COMMIT"), "abc1234def0\n"),
                              (os.path.join(paquet, "MODE_EMPLOI.html"), "<html></html>")):
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(texte)
        interdit = mock.Mock(side_effect=AssertionError("aucun sous-processus en mode figé"))
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", interne, create=True), \
                mock.patch.object(sys, "executable", os.path.join(paquet, chemins.NOM_EXE_FENETRE)), \
                mock.patch.object(chemins.subprocess, "Popen", interdit):
            self.assertTrue(chemins.est_fige())
            self.assertEqual(chemins.dossier_interne(), interne)
            self.assertEqual(chemins.racine_paquet(), paquet)
            self.assertEqual(chemins.ressource("VERSION"), os.path.join(interne, "VERSION"))
            self.assertEqual(chemins.ressource("MODE_EMPLOI.html"), os.path.join(paquet, "MODE_EMPLOI.html"))
            with self.assertRaises(chemins.RessourceAbsente) as cm:
                chemins.ressource("absent.txt")
            self.assertIn("paquet incomplet", str(cm.exception))
            self.assertEqual(chemins.chemin_moteur("placeur_routage"), moteur)
            with self.assertRaises(chemins.MoteurAbsent) as cm:
                chemins.chemin_moteur("serigraphie")
            self.assertIn(chemins.MESSAGE_MOTEUR_ABSENT, str(cm.exception))
            self.assertEqual(chemins.commande_moteur("placeur_routage", "--version"), [moteur, "--version"])
            self.assertEqual(chemins.commande_outil("drc_maison", "--mode", "strict"),
                             [os.path.join(paquet, chemins.NOM_EXE_CONSOLE), "--outil", "drc_maison", "--mode",
                              "strict"])
            self.assertEqual(chemins.commande_placeur("--auto-test"),
                             [os.path.join(paquet, chemins.NOM_EXE_CONSOLE), "--auto-test"])
            self.assertEqual(chemins.version_lanceur(), "2.0.0+abc1234")
        interdit.assert_not_called()
        self.assertFalse(chemins.est_fige())

    # -------------------------------------------------------------------------------------------- système

    def test_dossiers_connus_et_noms(self):
        profil = chemins.dossier_connu("profil")
        self.assertTrue(profil and os.path.isabs(profil) and os.path.isdir(profil))
        if WINDOWS:
            self.assertEqual(os.path.normcase(profil), os.path.normcase(os.path.expanduser("~")))
            for nom in ("appdata", "localappdata"):
                d = chemins.dossier_connu(nom)
                self.assertTrue(d and os.path.isdir(d), nom)
            self.assertTrue(chemins.dossier_connu("documents"))
        with self.assertRaises(ValueError):
            chemins.dossier_connu("bureau")
        noms = [n.lower() for n in chemins.noms_machine()]
        if len(platform.node()) >= 3:
            self.assertIn(platform.node().lower(), noms)
        self.assertTrue(chemins.nom_utilisateur())

    def test_motifs_generiques_d_anonymat(self):
        bs = "\\"
        profil_windows = "C" + ":" + bs + "Users" + bs + "jdupont" + bs + "carte.PcbDoc"
        a_trouver = {
            "profil": profil_windows,
            "profil échappé en JSON": json.dumps({"c": profil_windows}),
            "profil en barres obliques": profil_windows.replace(bs, "/"),
            "profil POSIX": "/" + "home" + "/" + "jdupont/x",
            "compte de CI": "runner" + "admin",
            "temporaire": "%USERPROFILE%" + bs + "AppData" + bs + "Local" + bs + "Temp" + bs + "x",
            "PyInstaller": "_MEI" + "12345",
            "registre cargo": "/cargo/" + "." + "cargo" + "/registry/src",
        }
        for nom, texte in a_trouver.items():
            self.assertTrue(chemins.chercher_motifs(texte), nom)
            self.assertTrue(chemins.chercher_motifs(texte.encode("utf-8")), nom + " (UTF-8)")
            large = texte.encode("utf-16-le")
            self.assertTrue(chemins.chercher_motifs(b"\x20\x00" + large), nom + " (UTF-16)")
            self.assertTrue(chemins.chercher_motifs(b"\x01" + large), nom + " (UTF-16, alignement impair)")
        neutres = ["C" + ":" + bs + "Users" + bs + "<nom>" + bs + "Documents", "%USERPROFILE%" + bs + "x",
                   "C" + ":" + bs + "Users" + bs + "Public" + bs + "x", "/" + "home" + "/<nom>/x",
                   "C" + ":" + bs + "Windows" + bs + "system32", "C" + ":" + bs + "Program Files" + bs + "x",
                   "C" + ":" + bs + "<dossier>" + bs + "x", "https://exemple.org/a", "rapport : 3:4"]
        for texte in neutres:
            self.assertEqual(chemins.chercher_motifs(texte, lecteur=True), [], texte)
        lecteur = "D" + ":" + bs + "cartes" + bs + "x.PcbDoc"
        self.assertEqual(chemins.chercher_motifs(lecteur), [])
        self.assertEqual([m for m, _ in chemins.chercher_motifs(lecteur, lecteur=True)], ["lettre de lecteur"])
        self.assertTrue(chemins.chercher_motifs(json.dumps(lecteur), lecteur=True))
        accentue = "E" + ":" + bs + "Données été" + bs + "carte.kicad_pcb"
        self.assertTrue(chemins.chercher_motifs(accentue.encode("utf-8"), lecteur=True), "texte UTF-8 accentué")
        # contenu binaire : un vrai chemin est vu, des octets quelconques presque jamais
        import random
        binaire = random.Random(20261118).randbytes(2_000_000)
        self.assertTrue(chemins.chercher_motifs(binaire[:1000] + lecteur.encode("ascii") + b"\x00" + binaire[1000:2000],
                                                lecteur=True))
        hasard = [m for m, _ in chemins.chercher_motifs(binaire, lecteur=True) if m == "lettre de lecteur"]
        bruts = len(chemins.MOTIF_LECTEUR.findall(binaire.decode("latin-1")))
        self.assertLessEqual(len(hasard), 3, f"faux positifs dans 2 Mo d'octets quelconques ({bruts} sans filtre)")
        # extraits seulement sur demande (jamais dans un journal publié)
        self.assertEqual(chemins.chercher_motifs(profil_windows)[0][1], "")
        self.assertIn("jdupont", chemins.chercher_motifs(profil_windows, extraits=True)[0][1])

    def test_modules_sans_motif_d_anonymat(self):
        """chemins.py (source et code compilé, tel qu'il sera dans le paquet) et ce fichier de test ne contiennent
        aucun motif générique, ni aucune lettre de lecteur hors formes neutres."""
        import marshal
        source = os.path.join(ICI, "chemins.py")
        with open(source, "rb") as f:
            octets = f.read()
        self.assertEqual(chemins.chercher_motifs(octets, lecteur=True, extraits=True), [])
        code = compile(octets, "chemins.py", "exec")
        self.assertEqual(chemins.chercher_motifs(marshal.dumps(code), lecteur=True, extraits=True), [])
        with open(os.path.abspath(__file__), "rb") as f:
            self.assertEqual(chemins.chercher_motifs(f.read(), lecteur=True, extraits=True), [])

    def test_import_leger(self):
        """Un sous-processus neuf qui importe chemins ne charge ni numpy, ni matplotlib, ni scipy (§0.2-3)."""
        code = textwrap.dedent(f"""
            import sys
            avant = set(sys.modules)
            sys.path.insert(0, {ICI!r})
            import chemins
            apres = set(sys.modules)
            interdits = {sorted(INTERDITS)!r}
            print(repr((sorted(m for m in apres if m.split(".")[0] in interdits),
                        sorted(m for m in apres - avant if m.split(".")[0] in interdits))))
        """)
        r = chemins.executer([sys.executable, "-c", code])
        self.assertEqual(r.returncode, 0, r.stderr)
        tous, nouveaux = ast.literal_eval(r.stdout.strip())
        self.assertEqual(nouveaux, [], "modules interdits chargés par chemins")
        self.assertEqual(tous, [], "modules interdits chargés au démarrage de Python")


# ================================================================================================ lanceur (É6)

import importlib  # noqa: E402
import types  # noqa: E402

import lanceur  # noqa: E402

SERIGRAPHIE = os.path.join(chemins.OUTILS, "serigraphie")


def fichier_outil(nom: str) -> str | None:
    for d in (ICI, SERIGRAPHIE):
        c = os.path.join(d, nom + ".py")
        if os.path.isfile(c):
            return c
    return None


def a_main_argv(chemin: str) -> bool:
    with open(chemin, encoding="utf-8") as f:
        arbre = ast.parse(f.read())
    return any(isinstance(n, ast.FunctionDef) and n.name == "main" and (n.args.args or n.args.vararg)
               for n in arbre.body)


class TestAiguillage(unittest.TestCase):
    """--outil : liste blanche des 15 modules (§5.2, §5.5)."""

    def test_chaque_nom_appelle_main_et_rend_son_code(self):
        self.assertEqual(len(chemins.OUTILS_AIGUILLES), 15)
        self.assertEqual(set(lanceur.LOTS_OUTILS), set(chemins.OUTILS_AIGUILLES))
        for i, nom in enumerate(chemins.OUTILS_AIGUILLES):
            with self.subTest(nom=nom):
                recus = []
                faux = types.ModuleType(nom)
                faux.main = lambda argv, i=i: (recus.append(list(argv)), 10 + i)[1]
                with mock.patch.dict(sys.modules, {nom: faux}):
                    self.assertEqual(lanceur.main(["--outil", nom, "a", "é b"]), 10 + i)
                self.assertEqual(recus, [["a", "é b"]])

    def test_codes_none_et_systemexit(self):
        faux = types.ModuleType("formats")
        faux.main = lambda argv: None
        with mock.patch.dict(sys.modules, {"formats": faux}):
            self.assertEqual(lanceur.aiguiller("formats", []), 0)
        faux.main = lambda argv: sys.exit(3)
        with mock.patch.dict(sys.modules, {"formats": faux}):
            self.assertEqual(lanceur.aiguiller("formats", []), 3)
        sans_main = types.ModuleType("mesures")
        with mock.patch.dict(sys.modules, {"mesures": sans_main}), mock.patch("sys.stderr") as err:
            self.assertEqual(lanceur.aiguiller("mesures", []), 2)
        self.assertIn("lot C", "".join(c.args[0] for c in err.write.call_args_list))

    def test_nom_hors_liste_code_2(self):
        for nom in ("os", "placeur", "subprocess", "../formats", ""):
            with self.subTest(nom=nom), mock.patch("sys.stderr"):
                self.assertEqual(lanceur.main(["--outil", nom]), 2)
        with mock.patch("sys.stderr"):
            self.assertEqual(lanceur.main(["--outil"]), 2)


class TestOutilsReels(unittest.TestCase):
    """Chaque outil réel de la liste blanche : module livré avec main(argv) ⇒ aiguillé ; sinon sauté (nom, lot)."""

    sautes: list[str] = []

    @classmethod
    def tearDownClass(cls):
        sys.stderr.write(f"\noutils réels sautés : {len(cls.sautes)} sur {len(chemins.OUTILS_AIGUILLES)} "
                         f"({', '.join(cls.sautes)})\n")

    def verifier_outil(self, nom: str):
        chemin = fichier_outil(nom)
        lot = lanceur.LOTS_OUTILS[nom]
        if chemin is None or not a_main_argv(chemin):
            raison = "absent" if chemin is None else "sans main(argv)"
            TestOutilsReels.sautes.append(f"{nom} (lot {lot})")
            self.skipTest(f"{nom} : pas encore livré ({raison}), attendu du lot {lot}")
        lanceur.ajouter_chemins_source()
        module = importlib.import_module(nom)
        with mock.patch.object(module, "main", return_value=5) as m:
            self.assertEqual(lanceur.aiguiller(nom, ["x"]), 5)
        m.assert_called_once_with(["x"])


for _nom in chemins.OUTILS_AIGUILLES:
    setattr(TestOutilsReels, f"test_outil_{_nom}", lambda self, n=_nom: self.verifier_outil(n))


class TestLanceurModes(unittest.TestCase):
    """Mode figé simulé, UTF-8, freeze_support, attente d'Entrée (§5.5)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_lanceur_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def paquet(self):
        paquet = os.path.join(self.tmp, "PlaceurRouteur")
        interne = os.path.join(paquet, "_internal")
        os.makedirs(os.path.join(interne, "moteurs"))
        moteur = os.path.join(interne, "moteurs", chemins.nom_executable("placeur_routage"))
        open(moteur, "wb").close()
        return paquet, interne, moteur

    def test_mode_fige_simule_commande_moteurs_ini(self):
        import parametres
        paquet, interne, moteur = self.paquet()
        carte = os.path.join(self.tmp, "ma_carte.PcbDoc")
        open(carte, "wb").close()
        utilisateur = os.path.join(self.tmp, "parametres.ini")
        with open(utilisateur, "w", encoding="utf-8") as f:
            f.write("[calcul]\nqualite = rapide\n")
        with open(parametres.ini_projet(carte), "w", encoding="utf-8") as f:
            f.write("[calcul]\nessais = 3\n")
        with mock.patch.object(parametres, "ini_utilisateur", return_value=utilisateur):
            source = parametres.charger(carte=carte)            # mode source : jamais de .ini implicite
            self.assertEqual(source.fichiers_lus, [])
            for exe, mode in ((chemins.NOM_EXE_CONSOLE, "console"), (chemins.NOM_EXE_FENETRE, "fenetre")):
                with mock.patch.object(sys, "frozen", True, create=True), \
                        mock.patch.object(sys, "_MEIPASS", interne, create=True), \
                        mock.patch.object(sys, "executable", os.path.join(paquet, exe)):
                    self.assertEqual(lanceur.choisir_mode([carte]), mode)
                    self.assertEqual(lanceur.choisir_mode(["--outil", "formats"]), "outil")
                    self.assertEqual(chemins.commande_outil("formats", "valider", "x"),
                                     [os.path.join(paquet, chemins.NOM_EXE_CONSOLE), "--outil", "formats",
                                      "valider", "x"])
                    self.assertEqual(chemins.chemin_moteur("placeur_routage"), moteur)
                    fige = parametres.charger(carte=carte)
                    self.assertEqual(len(fige.fichiers_lus), 2, fige.fichiers_lus)
                    self.assertEqual((fige.valeur("calcul", "qualite"), fige.valeur("calcul", "essais")),
                                     ("rapide", 3))
        self.assertEqual(lanceur.choisir_mode([carte]), "console")
        self.assertEqual(lanceur.choisir_mode(["--fenetre"]), "fenetre")

    def test_message_accentue_redirige_relu_en_utf8(self):
        faux = os.path.join(self.tmp, "faux")
        os.makedirs(faux)
        message = "Élément créé à l'été : ½ ✓ ŒUVRE"
        with open(os.path.join(faux, "formats.py"), "w", encoding="utf-8") as f:
            f.write("import sys\ndef main(argv):\n    print(%r + ' ' + ' '.join(argv))\n"
                    "    sys.stderr.write('erreur : ' + %r + '\\n')\n    return 1\n" % (message, message))
        code = (f"import sys; sys.path.insert(0, {ICI!r}); import lanceur; sys.path.insert(0, {faux!r}); "
                f"sys.exit(lanceur.main(['--outil', 'formats', 'àé']))")
        p = chemins.lancer([sys.executable, "-c", code], stdout=chemins.PIPE, stderr=chemins.PIPE, texte=False)
        sortie, erreur = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 1, erreur)
        self.assertEqual(sortie.replace(b"\r\n", b"\n"), (message + " àé\n").encode("utf-8"))
        self.assertEqual(erreur.replace(b"\r\n", b"\n"), ("erreur : " + message + "\n").encode("utf-8"))

    def test_freeze_support_en_premier(self):
        ordre = []
        with mock.patch.object(lanceur.multiprocessing, "freeze_support", lambda: ordre.append("freeze")), \
                mock.patch.object(lanceur, "configurer_flux", lambda: ordre.append("flux")), \
                mock.patch.object(lanceur, "aiguiller", lambda n, a: ordre.append("outil") or 0):
            self.assertEqual(lanceur.main(["--outil", "formats"]), 0)
        self.assertEqual(ordre, ["freeze", "flux", "outil"])
        with open(lanceur.__file__, encoding="utf-8") as f:
            corps = [n for n in ast.parse(f.read()).body if isinstance(n, ast.FunctionDef) and n.name == "main"][0]
        premier = corps.body[0].value if isinstance(corps.body[0], ast.Expr) else None
        self.assertEqual(ast.unparse(premier), "multiprocessing.freeze_support()")

    def test_attente_entree_seulement_si_seul_dans_la_console(self):
        import placeur
        for n, attendu in ((1, True), (2, False), (3, False), (0, False)):
            with self.subTest(processus=n), mock.patch.object(lanceur, "processus_de_la_console", return_value=n), \
                    mock.patch.object(lanceur, "entree_console", return_value=True), \
                    mock.patch("builtins.input", return_value="") as entree, \
                    mock.patch.object(placeur, "main", return_value=7), mock.patch("sys.stdout"):
                self.assertEqual(lanceur.main(["carte.PcbDoc"]), 7)
                self.assertEqual(entree.called, attendu)
        with mock.patch.object(lanceur, "processus_de_la_console", return_value=1), \
                mock.patch.object(lanceur, "entree_console", return_value=False), \
                mock.patch("builtins.input") as entree:
            self.assertFalse(lanceur.attendre_entree())     # entrée redirigée ou vide : jamais d'attente
            entree.assert_not_called()
        if WINDOWS:   # vrai GetConsoleProcessList : un sous-processus sans clavier n'attend pas
            r = chemins.executer([sys.executable, "-c", f"import sys; sys.path.insert(0, {ICI!r}); import lanceur; "
                                  "print(lanceur.processus_de_la_console(), lanceur.attendre_entree())"],
                                 delai_s=30)
            self.assertEqual(r.stdout.split()[-1], "False", r.stderr)

    def test_sans_variable_d_environnement_et_import_leger(self):
        motif = re.compile(r"os\.environ|getenv")
        for nom in ("lanceur.py", "interface.py"):
            with open(os.path.join(ICI, nom), encoding="utf-8") as f:
                self.assertEqual([l for l in f.read().splitlines() if motif.search(l)], [], nom)
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {ICI!r})
            import lanceur, interface
            print(repr(sorted(m for m in sys.modules if m.split(".")[0] in {sorted(INTERDITS)!r})))
        """)
        r = chemins.executer([sys.executable, "-c", code], delai_s=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(ast.literal_eval(r.stdout.strip()), [])


# ================================================================================================ fenêtre (É6)

def _tk_disponible() -> bool:
    try:
        import tkinter
        r = tkinter.Tk()
        r.destroy()
        return True
    except Exception:  # noqa: BLE001 - pas d'affichage
        return False


TK = _tk_disponible()


@unittest.skipUnless(TK, "affichage indisponible")
class TestFenetre(unittest.TestCase):
    """La fenêtre de §5.5 avec l'orchestrateur et le faux moteur de test_placeur (processus à part)."""

    def setUp(self):
        import test_placeur
        self.tp = test_placeur
        self.tmp = tempfile.mkdtemp(prefix="essai_fenetre_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.parent = os.path.join(self.tmp, "res")
        self.journal = os.path.join(self.tmp, "vivants.txt")
        self.messages = []
        self.ouverts = []
        self.choix = "annuler"

    def commande(self, *args):
        return [chemins.interpreteur(), os.path.join(ICI, "test_placeur.py"), "--orchestrateur-faux", self.journal,
                "--"] + [str(a) for a in args]

    def fenetre(self, scenario=None, essais=3, processeurs=2):
        import tkinter
        import interface
        racine = tkinter.Tk()
        racine.withdraw()
        f = interface.Fenetre(racine, commande=self.commande, demander_fermeture=lambda: self.choix,
                              dossier_erreurs=os.path.join(self.tmp, "erreurs"), ouvrir=self.ouverts.append,
                              informer=lambda *a: self.messages.append(a))
        self.addCleanup(f.detruire)
        if scenario is not None:
            self.donnees = self.tp.donnees_completes(self.tmp, scenario)
            f.v_carte.set(self.donnees)
            f.v_sortie.set(self.parent)
            f.v_essais.set(str(essais))
            f.v_processeurs.set(str(processeurs))
            f.v_ouvrir.set(False)
        return f

    def pomper(self, f, condition, delai_s=60):
        fin = time.monotonic() + delai_s
        while time.monotonic() < fin:
            if f.ferme:
                return condition()
            f.racine.update()
            if condition():
                return True
            time.sleep(0.02)
        return False

    def lignes(self, f):
        return {f.tableau.item(i, "values")[0]: f.tableau.item(i, "values") for i in f.tableau.get_children()}

    def test_contenu_de_la_fenetre(self):
        f = self.fenetre()
        textes = " | ".join(f.textes())
        for attendu in ("Carte", "Parcourir…", "Contraintes", "Contraintes avancées…", "Pièces à garder en place",
                        "Dossier de sortie", "Qualité", "Durée maximale", "Essais", "Processeurs", "Carte produite",
                        "Sérigraphie", "Vias de couture", "Ouvrir à la fin", "Réglages avancés",
                        "S'arrêter après K graines valides", "Priorité normale", "Analyser la carte", "Lancer",
                        "Arrêter", "inir les graines en cours", "Reprendre", "Ouvrir les résultats",
                        "Enregistrer les paramètres", "Aide", "Mode d'emploi", "Vérifier l'installation",
                        "Copier le diagnostic", "Graine", "État", "Phase", "Durée", "Coût", "Liaisons ouvertes",
                        "Temps restant estimé", "Durée prévue", "%"):
            self.assertIn(attendu, textes)
        import interface
        self.assertEqual([q for q, _ in interface.QUALITES], ["Aperçu", "Rapide", "Normal", "Soigné"])
        self.assertEqual([t for _, t in interface.CHOIX_FERMETURE], ["Arrêter proprement", "Tout arrêter", "Annuler"])
        self.assertEqual(str(f.barre.cget("mode")), "determinate")
        self.assertIn("Tout arrêter", interface.TEXTE_ARRETER_2)
        f.v_carte.set(os.path.join(self.tmp, "c.kicad_pcb"))
        f.racine.update()
        self.assertIn("sans repositionnement de la sérigraphie", f.case_pcbdoc.cget("text"))
        # options et réglages changés seulement (décision 34)
        f.v_qualite.set("Soigné")
        f.v_couture.set(False)
        f.v_fixes.set("U1, J1")
        opts = f.options()
        self.assertEqual(opts[opts.index("--qualite") + 1], "soigne")
        self.assertIn("--sans-couture", opts)
        self.assertIn("--parametres", opts)
        self.assertEqual(set(f.valeurs_modifiees()), {"carte.fichier", "calcul.qualite", "pcbdoc.vias_couture",
                                                      "contraintes.fixes"})
        os.remove(opts[opts.index("--parametres") + 1])

    def test_bout_en_bout_tableau_barre_fin(self):
        f = self.fenetre({"defaut": {"duree_s": 0.3}, "2": {"cout": 900.0}})
        f.b_lancer.invoke()
        self.assertTrue(self.pomper(f, lambda: f.proc is None and f.fin is not None), f.lignes_journal)
        lignes = self.lignes(f)
        self.assertEqual(sorted(lignes), ["1", "2", "3"])
        self.assertTrue(all(v[1] == "terminee" for v in lignes.values()), lignes)
        self.assertEqual(float(f.barre.cget("value")), 100.0)
        self.assertEqual(f.code, 0)
        self.assertIn("Fin : code 0", f.l_etat.cget("text"))
        self.assertIn("meilleure graine : 2", f.l_etat.cget("text"))
        self.assertIn("Durée prévue", f.l_prevision.cget("text"))
        self.assertTrue(os.path.isdir(f.sortie_calcul))

    def demarrer_lent(self, duree_s=4.0, essais=3, processeurs=1):
        f = self.fenetre({"defaut": {"type": "lent", "duree_s": duree_s}}, essais=essais, processeurs=processeurs)
        f.b_lancer.invoke()
        self.assertTrue(self.pomper(f, lambda: f.sortie_calcul is not None and any(
            v[1] == "lancee" for v in self.lignes(f).values())), f.lignes_journal)
        return f

    def test_arreter_en_deux_temps(self):
        import interface
        f = self.demarrer_lent()
        f.b_arreter.invoke()
        self.assertTrue(os.path.exists(os.path.join(f.sortie_calcul, "arret_lancements")))
        self.assertFalse(os.path.exists(os.path.join(f.sortie_calcul, "arret_demande")))
        self.assertEqual(f.b_arreter.cget("text"), interface.TEXTE_ARRETER_2)
        f.b_arreter.invoke()
        self.assertTrue(os.path.exists(os.path.join(f.sortie_calcul, "arret_demande")))
        self.assertTrue(self.pomper(f, lambda: f.proc is None, 60), f.lignes_journal)
        self.assertEqual(f.code, 1 if not any(v[1] == "terminee" for v in self.lignes(f).values()) else 3)
        self.assertIsNotNone(f.fin)

    def test_fermeture_annuler(self):
        f = self.demarrer_lent()
        self.choix = "annuler"
        f.fermeture()
        f.racine.update()
        self.assertFalse(f.ferme)
        self.assertTrue(f.en_cours())
        f.tout_arreter()

    def test_fermeture_tout_arreter_puis_reprise_proposee(self):
        import placeur
        f = self.demarrer_lent(duree_s=30.0)
        proc = f.proc
        self.choix = "tout"
        f.fermeture()
        self.assertTrue(f.ferme)
        fin = time.monotonic() + 2.0
        while proc.poll() is None and time.monotonic() < fin:
            time.sleep(0.05)
        self.assertIsNotNone(proc.poll(), "orchestrateur encore vivant 2 s après « Tout arrêter »")
        for l in self.tp.lignes_journal(self.journal):          # moteurs du calcul tués avec lui
            if l[0] == "debut":
                self.assertTrue(attendre(lambda pid=int(l[2]): not chemins.processus_vivant(pid), 2.0), l)
        self.assertIsNotNone(placeur.calcul_inacheve(self.donnees, self.parent))
        g = self.fenetre()
        g.v_sortie.set(self.parent)
        g.v_carte.set(self.donnees)
        g.racine.update()
        self.assertEqual(str(g.b_reprendre.cget("state")), "normal")

    def test_fermeture_arreter_proprement(self):
        f = self.demarrer_lent()
        sortie = f.sortie_calcul
        self.choix = "proprement"
        f.fermeture()
        self.assertTrue(os.path.exists(os.path.join(sortie, "arret_demande")))
        self.assertFalse(f.ferme)
        self.assertTrue(self.pomper(f, lambda: f.ferme, 60), "fenêtre non fermée après l'arrêt propre")
        with open(os.path.join(sortie, "technique", "rapport.json"), encoding="utf-8") as fh:
            rapport = json.load(fh)
        self.assertEqual((rapport["fin"], rapport["code"]), ("arret_demande", 3))

    def test_plantage_avant_calcul_erreurs_txt_anonymise(self):
        import interface
        profil = os.path.expanduser("~")
        compte = chemins.nom_utilisateur()
        machine = (chemins.noms_machine() or ("MACHINE",))[0]
        message = f"échec sous {profil} pour {compte} sur {machine}"
        f = self.fenetre({"defaut": {}})
        f.commande = mock.Mock(side_effect=RuntimeError(message))
        f.b_lancer.invoke()
        chemin = os.path.join(self.tmp, "erreurs", interface.NOM_ERREURS)
        self.assertTrue(os.path.isfile(chemin))
        with open(chemin, "rb") as fh:
            octets = fh.read()
        self.assertIn("RuntimeError".encode(), octets)
        texte = octets.decode("utf-8-sig")
        for secret in (profil, compte, machine):
            self.assertNotIn(secret.lower(), texte.lower())
        self.assertEqual(chemins.chercher_motifs(octets), [])
        self.assertTrue(self.messages and self.messages[-1][2])
        # plantage à l'ouverture de la fenêtre : code 4 et erreurs.txt
        os.remove(chemin)
        code = interface.main([], dossier_erreurs=os.path.join(self.tmp, "erreurs"),
                              fabrique=mock.Mock(side_effect=RuntimeError(message)))
        self.assertEqual(code, 4)
        with open(chemin, encoding="utf-8-sig") as fh:
            self.assertNotIn(profil.lower(), fh.read().lower())


class TestFenetreSeule(unittest.TestCase):
    """`python lanceur.py --fenetre --essai-fenetre 3000` : code 0 en moins de 10 s, rien ne reste."""

    @unittest.skipUnless(TK, "affichage indisponible")
    def test_essai_fenetre(self):
        job = chemins.Job()
        t0 = time.monotonic()
        p = chemins.lancer([chemins.interpreteur(), os.path.join(ICI, "lanceur.py"), "--fenetre", "--essai-fenetre",
                            "3000"], stdout=chemins.PIPE, stderr=chemins.PIPE, job=job, priorite_basse=False)
        try:
            _, erreur = p.communicate(timeout=10)
        finally:
            job.fermer()
        self.assertEqual(p.returncode, 0, erreur)
        self.assertLess(time.monotonic() - t0, 10.0)
        self.assertFalse(chemins.processus_vivant(p.pid))


if __name__ == "__main__":
    unittest.main()
