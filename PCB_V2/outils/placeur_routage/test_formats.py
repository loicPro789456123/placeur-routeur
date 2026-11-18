# -*- coding: utf-8 -*-
"""Tests de formats.py (lot E) — jalon E0 : API de §5.2, lecture (BOM ; JSON au moins aussi strict que serde_json ;
aucune erreur interne sur une entrée fautive), version majeure, champs de premier niveau, fichiers écrits à la main
(clés inconnues), formes v1, ligne de commande ; étape 2 : formats écrits par un programme ; étape 3 : fichiers
écrits à la main (lecture stricte, variantes fautives, BOM, cp1252, causes des erreurs JSON), conversion v1 des
contraintes, placements_en_liste, paramètres par défaut extraits du contrat (corpus/schemas).

Lancer depuis le dossier de ce fichier : ``python -m unittest -v test_formats``. Les fichiers d'essai sont écrits
dans un dossier temporaire, effacé à la fin de chaque test.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import formats

ICI = os.path.dirname(os.path.abspath(__file__))

EXEMPLES = os.path.join(ICI, "corpus", "formats", "exemples")

PROGRAMME = ("entree_carte", "config_carte", "donnees_placeur", "placements", "routage", "stats", "verdict",
             "controle_placement", "mesures", "proprete", "mesure_convertisseurs", "vias_couture", "controle_gabarit",
             "config_pcbdoc", "rapport_pcbdoc", "deduction", "rapport", "estimation", "etalonnage")
"""Les 19 formats écrits par un programme (§2.2 à §2.9, §5.1)."""

FORMES_V1 = {"placements_v1.json": "placements", "routage_v1.json": "routage"}


def lire_exemple(nom_fichier):
    with open(os.path.join(EXEMPLES, nom_fichier), encoding="utf-8") as f:
        return json.load(f)


# Un document par format : l'exemple complet du corpus (corpus/formats/exemples/<format>.json) pour les formats
# écrits par un programme ; pour les fichiers écrits à la main, toutes les clés sont facultatives.
DOCUMENTS = {nom: lire_exemple(nom + ".json") for nom in PROGRAMME}
DOCUMENTS["contraintes"] = {"format": "contraintes/2", "fixes": ["P1"], "_note": "commentaire ignoré"}
DOCUMENTS["parametres_moteur"] = {"campagne": {"chaines": 4}, "_note": "commentaire ignoré"}

ECRITS_A_LA_MAIN = ("contraintes", "parametres_moteur")


class Dossier:
    """Dossier temporaire où écrire des fichiers d'essai."""

    def __init__(self, test):
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        self.chemin = self._tmp.name

    def ecrire(self, nom, contenu, bom=False):
        chemin = os.path.join(self.chemin, nom)
        texte = contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False)
        with open(chemin, "wb") as f:
            f.write((b"\xef\xbb\xbf" if bom else b"") + texte.encode("utf-8"))
        return chemin


def lancer_main(argv):
    """(code, sortie, erreur) de formats.main(argv)."""
    sortie, erreur = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreur):
        code = formats.main(argv)
    return code, sortie.getvalue(), erreur.getvalue()


class TestJalonE0(unittest.TestCase):

    def test_21_validateurs(self):
        """Un validateur valider_<nom>(d) -> list[str] par format de §2 et de §5.1 (21)."""
        noms = sorted(DOCUMENTS)
        self.assertEqual(len(noms), 21)
        self.assertEqual(sorted(formats.FORMATS), noms)
        for nom in noms:
            fonction = getattr(formats, "valider_" + nom, None)
            self.assertTrue(callable(fonction), nom)
            self.assertIs(formats.VALIDATEURS[nom], fonction)
            self.assertIsInstance(fonction({}), list, nom)
        for nom in ("charger", "lire_contraintes", "placements_en_liste", "main"):
            self.assertTrue(callable(getattr(formats, nom)), nom)
        self.assertTrue(issubclass(formats.ErreurFormat, Exception))

    def test_documents_conformes(self):
        """Chaque document minimal est accepté par son validateur et par charger (nom, ou nom et version)."""
        d = Dossier(self)
        for nom, doc in DOCUMENTS.items():
            with self.subTest(nom):
                self.assertEqual(formats.VALIDATEURS[nom](doc), [])
                chemin = d.ecrire(nom + ".json", doc)
                self.assertEqual(formats.charger(chemin, nom), doc)
                self.assertEqual(formats.charger(chemin, f"{nom}/{formats.FORMATS[nom]}"), doc)

    def test_bom_accepte(self):
        """Un fichier UTF-8 avec BOM (bloc-notes) est lu par charger et lire_contraintes."""
        d = Dossier(self)
        chemin = d.ecrire("etalonnage.json", DOCUMENTS["etalonnage"], bom=True)
        self.assertEqual(formats.charger(chemin, "etalonnage"), DOCUMENTS["etalonnage"])
        chemin = d.ecrire("contraintes.json", {"format": "contraintes/2", "fixes": ["Pièce é"]}, bom=True)
        contraintes, avertissements = formats.lire_contraintes(chemin)
        self.assertEqual(contraintes, {"format": "contraintes/2", "fixes": ["Pièce é"]})
        self.assertEqual(avertissements, [])
        code, sortie, _ = lancer_main(["valider", chemin])
        self.assertEqual(code, 0)

    def test_version_majeure_inconnue(self):
        """Version majeure inconnue refusée : « format X/N non pris en charge » (§0.5)."""
        d = Dossier(self)
        for nom, version in formats.FORMATS.items():
            with self.subTest(nom):
                doc = dict(DOCUMENTS[nom], format=f"{nom}/{version + 1}")
                self.assertEqual(formats.VALIDATEURS[nom](doc), [f"format {nom}/{version + 1} non pris en charge"])
        chemin = d.ecrire("verdict.json", dict(DOCUMENTS["verdict"], format="verdict/3"))
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.charger(chemin, "verdict")
        self.assertEqual(str(e.exception), "verdict.json : format verdict/3 non pris en charge")
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.charger(d.ecrire("v.json", DOCUMENTS["verdict"]), "verdict/1")
        self.assertEqual(str(e.exception), "v.json : format verdict/1 non pris en charge")
        # Autre format que celui attendu.
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.charger(d.ecrire("x.json", DOCUMENTS["mesures"]), "verdict")
        self.assertIn("« mesures/2 » au lieu de « verdict/2 »", str(e.exception))

    def test_champ_premier_niveau_manquant(self):
        """Chaque champ de premier niveau d'un format écrit par un programme est obligatoire (sauf ceux que le
        contrat dit facultatifs) : son absence est refusée avec le nom du champ et, par charger, le nom du fichier
        (sans dossier)."""
        d = Dossier(self)
        for nom, doc in DOCUMENTS.items():
            if nom in ECRITS_A_LA_MAIN:
                continue
            for champ in doc:
                if (nom, champ) in FACULTATIFS:
                    continue
                with self.subTest(format=nom, champ=champ):
                    incomplet = {k: v for k, v in doc.items() if k != champ}
                    attendu = [f"{champ} : champ manquant"]
                    if (nom, champ) == ("routage", "format"):  # forme v2 privée de son format : pas une v1
                        attendu = ["format : champ manquant (couches_routage présent : forme routage/2, pas v1)"]
                    self.assertEqual(formats.VALIDATEURS[nom](incomplet), attendu)
        incomplet = {k: v for k, v in DOCUMENTS["donnees_placeur"].items() if k != "regles"}
        chemin = d.ecrire("donnees_placeur.json", incomplet)
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.charger(chemin, "donnees_placeur")
        self.assertEqual(str(e.exception), "donnees_placeur.json : regles : champ manquant")
        self.assertNotIn(d.chemin, str(e.exception))
        self.assertEqual(formats.valider_verdict([]), ["racine : objet JSON attendu"])

    def test_ligne_de_commande(self):
        """``formats.py valider FICHIER`` : 0 conforme, 2 non conforme ou illisible ; messages sans dossier."""
        d = Dossier(self)
        conforme = d.ecrire("donnees_placeur.json", DOCUMENTS["donnees_placeur"])
        code, sortie, erreur = lancer_main(["valider", conforme])
        self.assertEqual((code, erreur), (0, ""))
        self.assertEqual(sortie.strip(), "donnees_placeur.json : conforme (donnees_placeur/2)")
        incomplet = {k: v for k, v in DOCUMENTS["stats"].items() if k != "execution"}
        cas = {
            "stats.json": (incomplet, "stats.json : execution : champ manquant"),
            "v3.json": ({"format": "verdict/3"}, "v3.json : format verdict/3 non pris en charge"),
            "casse.json": ('{"format": "etalonnage/1", "score": 1,}', "casse.json : JSON invalide, ligne 1"),
            "inconnu.json": ({"format": "inconnu/1"}, "inconnu.json : format « inconnu/1 » inconnu"),
            "sans_format.json": ({"a": 1}, "sans_format.json : format : champ manquant"),
            "nan.json": ('{"format": "etalonnage/1", "score": NaN}', "nan.json : valeur NaN non permise"),
        }
        for nom, (contenu, debut) in cas.items():
            with self.subTest(nom):
                code, sortie, erreur = lancer_main(["valider", d.ecrire(nom, contenu)])
                self.assertEqual(code, 2)
                self.assertEqual(sortie, "")
                self.assertTrue(erreur.startswith(debut), erreur)
                self.assertNotIn(d.chemin, erreur)
        for argv in (["valider", os.path.join(d.chemin, "absent.json")], ["valider", d.chemin]):
            code, sortie, erreur = lancer_main(argv)
            self.assertEqual(code, 2, argv)
            self.assertNotIn(d.chemin, erreur)
        for argv in ([], ["valider"], ["autre", conforme], ["valider", conforme, conforme]):
            code, sortie, erreur = lancer_main(argv)
            self.assertEqual(code, 2, argv)
            self.assertTrue(erreur.startswith("usage : formats.py valider FICHIER"), erreur)
        # Exécution réelle (argv lu par argparse dans main) : mêmes codes.
        script = os.path.join(ICI, "formats.py")
        for chemin, attendu in ((conforme, 0), (d.ecrire("x.json", incomplet), 2)):
            p = subprocess.run([sys.executable, "-B", script, "valider", chemin], capture_output=True,
                               encoding="utf-8", errors="replace")
            self.assertEqual(p.returncode, attendu, p.stderr)
            self.assertNotIn(d.chemin, p.stdout + p.stderr)

    def test_fichiers_ecrits_a_la_main(self):
        """Clé inconnue refusée avec la clé la plus proche (§0.5, §2.6) ; clés « _* » ignorées."""
        self.assertEqual(formats.valider_contraintes({"format": "contraintes/2", "fixe": []}),
                         ["'fixe' inconnue ; vouliez-vous dire 'fixes' ?"])
        self.assertEqual(formats.valider_contraintes({"format": "contraintes/2", "zzzz": 1}), ["'zzzz' inconnue"])
        self.assertEqual(formats.valider_parametres_moteur({"campagn": {}}),
                         ["campagn : clé inconnue ; vouliez-vous dire campagne ?"])
        self.assertEqual(formats.valider_parametres_moteur({"format": "parametres_moteur/2"}),
                         ["format parametres_moteur/2 non pris en charge"])
        d = Dossier(self)
        chemin = d.ecrire("contraintes.json", {"format": "contraintes/2", "fixe": ["P1"]})
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.lire_contraintes(chemin)
        self.assertEqual(str(e.exception), "contraintes.json : 'fixe' inconnue ; vouliez-vous dire 'fixes' ?")
        code, _, erreur = lancer_main(["valider", chemin])
        self.assertEqual(code, 2)
        self.assertEqual(erreur.strip(), "contraintes.json : 'fixe' inconnue ; vouliez-vous dire 'fixes' ?")

    def test_contraintes_v1(self):
        """Sans « format » : version 1, convertie avec un avertissement (renommages de §2.3, repère « carte »)."""
        d = Dossier(self)
        v1 = {"_doc": "commentaire", "fixes": ["P1"], "marge_bord": 1.5, "garde_bord_fentes_trous": 0.6,
              "hauteur_max_dessous": 3.0, "poids_reseaux": {"SENSE": 3},
              "zones_autour": [{"empreinte_prefixe": "SMA", "face": 0, "demi_cote": 6.0}],
              "zones_interdites": [{"rect": [0, 0, 1, 1], "face": "toutes"}]}
        contraintes, avertissements = formats.lire_contraintes(d.ecrire("contraintes_v1.json", v1))
        self.assertEqual(contraintes["format"], "contraintes/2")
        self.assertEqual((contraintes["repere"], contraintes["unite"]), ("carte", "mm"))
        self.assertEqual(contraintes["marge_bord_mm"], 1.5)
        self.assertEqual((contraintes["garde_bord_mm"], contraintes["garde_decoupe_mm"]), (0.6, 0.6))
        self.assertEqual(contraintes["hauteur_max_dessous_mm"], 3.0)
        self.assertEqual(contraintes["reseaux"], {"SENSE": {"poids": 3}})
        self.assertEqual(contraintes["zones_autour"][0]["face"], "dessus")
        self.assertEqual(contraintes["zones_interdites"][0]["faces"], ["dessus", "dessous"])
        self.assertNotIn("_doc", contraintes)
        self.assertTrue(avertissements and avertissements[0].startswith("contraintes_v1.json : "))
        copie = json.loads(json.dumps(v1))
        formats._convertir_contraintes_v1(copie, "contraintes_v1.json")
        self.assertEqual(copie, v1, "la conversion ne modifie pas son entrée")
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.lire_contraintes(d.ecrire("c.json", {"marge_bords": 1.0}))
        self.assertIn("'marge_bords' inconnue ; vouliez-vous dire", str(e.exception))

    def test_formes_v1_et_placements_en_liste(self):
        """Placement v1 (liste nue) et routage v1 (sans « format ») acceptés ; placements_en_liste donne la même
        liste pour les deux formes d'un même placement, pièces absentes et virtuelles à leur pose cible."""
        v1 = [{"designator": "U1", "side": "top", "x": 100.0, "y": 50.0, "rotation": 90}]
        self.assertEqual(formats.valider_placements(v1), [])
        self.assertEqual(formats.valider_placements([{"designator": "U1"}]),
                         ["[0].side : champ manquant", "[0].x : champ manquant", "[0].y : champ manquant",
                          "[0].rotation : champ manquant"])
        self.assertEqual(formats.valider_routage({"pistes": [], "vias": []}), [])
        self.assertEqual(formats.valider_routage({"pistes": []}), ["vias : champ manquant"])
        donnees = {"composants": [
            {"nom": "U1", "statut": "mobile", "virtuel": False, "pose_cible": [0.0, 0.0, 0.0, False]},
            {"nom": "P1", "statut": "fixe", "virtuel": False, "pose_cible": [2.54, 1.27, 180.0, True]},
            {"nom": "__libre_0", "statut": "fixe", "virtuel": True, "pose_cible": [0.254, 0.254, 0.0, False]}]}
        # Même placement sous les deux formes : la v1 ne porte que les pièces mobiles, la v2 porte aussi la pièce
        # fixe P1 à sa pose (celle de pose_cible, en mil au millième).
        p1 = {"designator": "P1", "side": "bottom", "x": 100.0, "y": 50.0, "rotation": 180.0, "statut": "fixe"}
        v2 = dict(DOCUMENTS["placements"], pieces=[dict(v1[0], statut="mobile"), p1])
        liste = formats.placements_en_liste(v1, donnees)
        self.assertEqual(liste, formats.placements_en_liste(v2, donnees))
        self.assertEqual([e["designator"] for e in liste], ["U1", "P1", "__libre_0"])
        self.assertEqual((liste[0]["x"], liste[0]["rotation"], liste[0]["absente"]), (100.0, 90.0, False))
        self.assertEqual((liste[1]["side"], liste[1]["x"], liste[1]["y"], liste[1]["absente"], liste[1]["virtuel"]),
                         ("bottom", 100.0, 50.0, False, False))
        self.assertEqual((liste[2]["x"], liste[2]["y"], liste[2]["absente"], liste[2]["virtuel"]),
                         (10.0, 10.0, False, True))
        # En v2, une pièce non virtuelle manquante est absente (à sa pose cible) ; en v1, seulement une mobile.
        v2_sans_p1 = dict(v2, pieces=v2["pieces"][:1])
        self.assertEqual(formats.placements_en_liste(v2_sans_p1, donnees)[1],
                         dict(liste[1], absente=True))
        self.assertTrue(formats.placements_en_liste([], donnees)[0]["absente"])
        with self.assertRaises(formats.ErreurFormat):
            formats.placements_en_liste({"format": "placements/2"}, donnees)
        # Aucun alias de pose_cible (nom de la version 1 supprimé par §2.5, non lu par validite.rs non plus).
        sans_pose = {"composants": [{"nom": "P1", "statut": "fixe", "virtuel": False,
                                     "pose_v3": [2.54, 1.27, 180.0, True]}]}
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.placements_en_liste([], sans_pose)
        self.assertIn("pose_cible", str(e.exception))

    def test_stats_avenant_a5(self):
        """stats/2 avec les champs facultatifs de l'avenant 2.2 (A.5) : accepté ; sans eux : accepté aussi."""
        stats = DOCUMENTS["stats"]
        self.assertEqual(stats["routage_initial"], {"echecs_routeur": 9, "ko_exact": 9})
        self.assertEqual(sorted(stats["recuit"]["exploration"]), ["cout", "hpwl", "mouvements", "paliers"])
        self.assertIn("noeud_estime_mm", stats["convertisseurs"][0])
        self.assertEqual(formats.valider_stats(stats), [])
        sans = copie(stats)
        for cle in ("routage_initial", "diagnostic"):
            del sans[cle]
        del sans["recuit"]["exploration"]
        del sans["convertisseurs"][0]["noeud_estime_mm"]
        self.assertEqual(formats.valider_stats(sans), [])


def copie(doc):
    return json.loads(json.dumps(doc))


FACULTATIFS = {
    ("verdict", "placement"), ("verdict", "avertissements.zones_plan_isolees"), ("verdict", "placement.format"),
    ("stats", "routage_initial"), ("stats", "diagnostic"), ("stats", "recuit.exploration"),
    ("stats", "convertisseurs[0].noeud_estime_mm"), ("stats", "validite.format"),
    ("mesure_convertisseurs", "convertisseurs[0].noeud_vias"),
    ("mesure_convertisseurs", "convertisseurs[0].sensibles_distance_min_mm"),
    ("donnees_placeur", "parametres_deduction.pas_fin_mm"),
    ("donnees_placeur", "parametres_deduction.regles_non_interpretees"),
    ("routage_v1", "unite"), ("placements_v1", "[0].rotation_si_convention_miroir_x"),
}
"""Champs que le contrat dit facultatifs (« facultatif », avenant 2.2 A.5 et A.6, choix du lot E notés au journal),
présents dans les exemples ; tout autre champ de premier et de second niveau est obligatoire (§2.1)."""

DICOS = {("entree_carte", "classes"), ("entree_carte", "classes_composants"),
         ("rapport", "deduction"), ("proprete", "hors_direction"), ("proprete", "paires"), ("vias_couture", "refus"),
         ("controle_gabarit", "non_retournables"), ("verdict", "exemples")}
"""Objets à clés libres (nom de réseau, de classe, de couche…) : leurs clés ne sont pas des champs obligatoires."""

EXCLUSIFS = {("donnees_placeur", "obstacles_placement[0].rect"): "obstacles_placement[0] : un et un seul champ parmi"}
"""Champs d'un groupe « un et un seul parmi » : leur absence est signalée sur l'objet qui les porte."""


def _parties(chemin):
    return [int(p) if p.isdigit() else p for p in re.findall(r"[^.\[\]]+", chemin)]


def mettre(doc, chemin, valeur, supprimer=False):
    """Copie de ``doc`` où le champ ``chemin`` (« a.b[0].c ») vaut ``valeur`` (``supprimer`` : champ retiré)."""
    doc = copie(doc)
    parties = _parties(chemin)
    x = doc
    for p in parties[:-1]:
        x = x[p]
    if supprimer:
        del x[parties[-1]]
    else:
        x[parties[-1]] = valeur
    return doc


def premier_et_second_niveau(doc):
    """Chemins des champs de premier niveau (hors « format ») et de second niveau : clés des objets de premier
    niveau et clés du premier élément des listes de premier niveau (racine liste : clés de son premier élément)."""
    if isinstance(doc, list):
        return [f"[0].{k}" for k in doc[0] if not k.startswith("_")]
    chemins = []
    for k, v in doc.items():
        if k == "format" or k.startswith("_"):
            continue
        chemins.append(k)
        if isinstance(v, dict):
            chemins += [f"{k}.{s}" for s in v if not s.startswith("_")]
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            chemins += [f"{k}[0].{s}" for s in v[0] if not s.startswith("_")]
    return chemins


class TestEtape2(unittest.TestCase):
    """Validateurs complets des 19 formats écrits par un programme (§2.1 à §2.9, §5.1 ; avenant 2.2 A.5, A.6) et
    exemples complets du corpus."""

    def refus(self, nom, doc, attendu, nom_fichier=None):
        """Le validateur de ``nom`` refuse ``doc`` avec le message ``attendu`` (parmi ses erreurs) ; avec
        ``nom_fichier``, charger le refuse avec ce message précédé du nom du fichier, sans dossier."""
        erreurs = formats.VALIDATEURS[nom](doc)
        self.assertIn(attendu, erreurs)
        if nom_fichier:
            d = Dossier(self)
            with self.assertRaises(formats.ErreurFormat) as e:
                formats.charger(d.ecrire(nom_fichier, doc), nom)
            self.assertIn(f"{nom_fichier} : {attendu}", e.exception.erreurs)
            self.assertNotIn(d.chemin, str(e.exception))

    def test_exemples_acceptes(self):
        """Chaque fichier de corpus/formats/exemples/ (au moins 21 : 19 formats et 2 formes v1) est accepté par
        son validateur, par charger et par la ligne de commande (code 0)."""
        fichiers = sorted(f for f in os.listdir(EXEMPLES) if f.endswith(".json"))
        self.assertGreaterEqual(len(fichiers), 21)
        for nom in PROGRAMME:
            self.assertIn(nom + ".json", fichiers)
        for f in FORMES_V1:
            self.assertIn(f, fichiers)
        for f in fichiers:
            with self.subTest(f):
                chemin = os.path.join(EXEMPLES, f)
                options = ["--config-utilisateur"] if f.startswith("config_utilisateur") else []
                code, sortie, erreur = lancer_main(["valider", chemin] + options)
                self.assertEqual((code, erreur), (0, ""))
                self.assertTrue(sortie.startswith(f"{f} : conforme ("), sortie)
                nom = FORMES_V1.get(f, f[:-5])
                if nom in formats.FORMATS:
                    self.assertEqual(formats.charger(chemin, nom), lire_exemple(f))
        self.assertIsInstance(lire_exemple("placements_v1.json"), list)
        self.assertNotIn("format", lire_exemple("routage_v1.json"))

    def test_suppression_premier_et_second_niveau(self):
        """Suppression systématique de chaque champ obligatoire de premier et de second niveau de chaque exemple :
        refusée avec son chemin et, par charger, le nom du fichier ; les champs facultatifs peuvent manquer."""
        d = Dossier(self)
        cas = [(nom, nom, DOCUMENTS[nom]) for nom in PROGRAMME]
        cas += [(f[:-5], format_nom, lire_exemple(f)) for f, format_nom in FORMES_V1.items()]
        n = 0
        for nom, format_nom, doc in cas:
            for chemin in premier_et_second_niveau(doc):
                if (nom, re.split(r"[.\[]", chemin)[0]) in DICOS and "." in chemin:
                    continue
                with self.subTest(format=nom, champ=chemin):
                    variante = mettre(doc, chemin, None, supprimer=True)
                    erreurs = formats.VALIDATEURS[format_nom](variante)
                    if (nom, chemin) in FACULTATIFS:
                        self.assertEqual(erreurs, [])
                        continue
                    n += 1
                    if (nom, chemin) in EXCLUSIFS:
                        self.assertTrue(any(e.startswith(EXCLUSIFS[nom, chemin]) for e in erreurs), erreurs)
                        continue
                    attendu = f"{chemin} : champ manquant"
                    self.assertIn(attendu, erreurs)
                    fichier = nom + ".json"
                    with self.assertRaises(formats.ErreurFormat) as e:
                        formats.charger(d.ecrire(fichier, variante), format_nom)
                    self.assertIn(f"{fichier} : {attendu}", e.exception.erreurs)
                    self.assertNotIn(d.chemin, str(e.exception))
        self.assertGreater(n, 400)

    def test_references_croisees(self):
        """Références croisées internes (§2.1) : désignateurs, réseaux, couches, pastilles, découpes, classes
        d'isolement et paires inconnus refusés avec le chemin du champ."""
        donnees = DOCUMENTS["donnees_placeur"]
        self.refus("donnees_placeur", mettre(donnees, "convertisseurs[0].cond_bulk[0]", "C99"),
                   "convertisseurs[0].cond_bulk[0] : pièce C99 inconnue", "donnees_placeur.json")
        cas = {
            "groupes[1].exemplaires[0].pieces[0]": ("X1", "pièce X1 inconnue"),
            "eloignements[0].objets[0]": ("U25_VOLT1#99", "objet (pièce ou pastille) U25_VOLT1#99 inconnu"),
            "eloignements[0].reference[0]": ("M9", "pièce M9 inconnue"),
            "charges[0].pastilles[0]": ("R62_ALIM1#5", "pastille R62_ALIM1#5 inconnue"),
            "decouplages[0].broches_ci[0]": ("U10#99", "pastille U10#99 inconnue"),
            "decouplages[0].reseau": ("+15V", "réseau +15V inconnu"),
            "sans_piste_dessous[0]": ("L9", "pièce L9 inconnue"),
            "zones_imposees[0].pieces[0]": ("U99", "pièce U99 inconnue"),
            "reseaux[2].couche_preferee": ("MidLayer9", "couche MidLayer9 inconnue"),
            "reseaux[0].couches_permises[0]": ("Top", "couche Top inconnue"),
            "reseaux[0].paire": ("USB", "paire USB inconnue"),
            "reseaux[3].classes_isolement.via": (3, "classe d'isolement 3 inconnue"),
            "couches[1].zones_plan[0].reseau": ("AGND", "réseau AGND inconnu"),
            "carte.remplissages[0].couche": ("MidLayer3", "couche MidLayer3 inconnue"),
            "carte.decoupes[0].proprietaire": ("M5", "pièce M5 inconnue"),
            "composants[0].pastilles[0].reseau": ("Net9", "réseau Net9 inconnu"),
            "composants[0].regions[0].pastille": ("U10#40", "pastille U10#40 inconnue"),
            "composants[15].decoupes[0]": ("D7", "découpe D7 inconnue"),
            "cuivre_fixe[1].couches[0]": ("Bottom", "couche Bottom inconnue"),
            "zones_routage_interdites[0].couches[0]": ("tout", "couche tout inconnue"),
            "trous[0].piece": ("M7", "pièce M7 inconnue"),
            "masses[0]": ("GROUND", "réseau GROUND inconnu"),
            "convertisseurs[0].boucle_chaude.reseau_rail": ("+13V", "réseau +13V inconnu"),
        }
        for chemin, (valeur, cause) in cas.items():
            with self.subTest(chemin):
                self.refus("donnees_placeur", mettre(donnees, chemin, valeur), f"{chemin} : {cause}")
        entree = DOCUMENTS["entree_carte"]
        for chemin, (valeur, cause) in {
                "classes.COMMUTATION[0]": ("NetX", "réseau NetX inconnu"),
                "classes_composants.CONVERTISSEUR[0]": ("Q1", "pièce Q1 inconnue"),
                "composants[0].pastilles[0].couches[0]": ("F.Cu", "couche F.Cu inconnue"),
                "carte.trous[0].couches[0]": ("Inner", "couche Inner inconnue")}.items():
            with self.subTest(chemin):
                self.refus("entree_carte", mettre(entree, chemin, valeur), f"{chemin} : {cause}")
        self.refus("entree_carte", mettre(entree, "regles.reseaux.GND.couches_permises", ["Top"]),
                   "regles.reseaux.GND.couches_permises[0] : couche Top inconnue")
        regles = copie(entree)
        regles["regles"]["reseaux"]["Inconnu"] = regles["regles"]["reseaux"]["GND"]
        self.refus("entree_carte", regles, "regles.reseaux.Inconnu : réseau Inconnu inconnu")
        self.refus("routage", mettre(DOCUMENTS["routage"], "pistes[0].layer", "MidLayer1"),
                   "pistes[0].layer : couche MidLayer1 inconnue", "routage.json")
        self.refus("vias_couture", mettre(DOCUMENTS["vias_couture"], "vias[1].net", "+8V"),
                   "vias[1].net : réseau +8V inconnu")
        # config_carte ne porte pas la liste des pièces : ses désignateurs ne sont pas contrôlés.
        self.assertEqual(formats.valider_config_carte(
            mettre(DOCUMENTS["config_carte"], "convertisseurs[0].cond_bulk[0]", "C99")), [])

    def test_copies_et_coherence(self):
        """Cohérence des copies de §2.5 et cohérence interne (noms uniques, rangs, matrice, identifiants, boucle
        chaude, verdict, coût commun)."""
        donnees = DOCUMENTS["donnees_placeur"]
        cas = [
            ("reseaux[0].plan_couches", ["MidLayer2"],
             "reseaux[0].plan_couches : copie incohérente avec couches[].zones_plan (['MidLayer1'] attendu)"),
            ("reseaux[1].plan_couches", ["MidLayer1"],
             "reseaux[1].plan_couches : copie incohérente avec couches[].zones_plan ([] attendu)"),
            ("reseaux[0].remplissages", ["TopLayer"],
             "reseaux[0].remplissages : copie incohérente avec carte.remplissages (['TopLayer', 'MidLayer2', "
             "'BottomLayer'] attendu)"),
            ("reseaux[1].masse", True, "reseaux[1].masse : copie incohérente avec masses"),
            ("reseaux[3].isolement_mm", 0.2, "reseaux[3].isolement_mm : 0.25 attendu (matrice[classes_isolement."
                                             "piste][classe_reference], §2.5), 0.2 trouvé"),
            ("couches[2].role", "plan", "couches[2].zones_plan : non vide si et seulement si role = « plan » (§2.5)"),
            ("couches[1].role", "routage",
             "couches[1].zones_plan : non vide si et seulement si role = « plan » (§2.5)"),
            ("couches[3].rang", 4, "couches[3].rang : 3 attendu (couches de haut en bas), 4 trouvé"),
            ("composants[1].pastilles[0].id", "M1#7",
             "composants[1].pastilles[0].id : « M1#0 » attendu, « M1#7 » trouvé"),
            ("composants[0].orientations", [],
             "composants[0].orientations : non vide pour une pièce mobile, [] pour une fixe (§2.5)"),
            ("regles.isolement.matrice", [[0.25, 0.2, 0.25], [0.25, 0.12446, 0.2], [0.25, 0.2, 0.2]],
             "regles.isolement.matrice[1][0] : matrice symétrique attendue (0.25 ≠ matrice[0][1] = 0.2)"),
            ("regles.isolement.matrice", [[0.25, 0.25], [0.25, 0.2]],
             "regles.isolement.matrice : matrice 3 × 3 attendue (une ligne et une colonne par classe)"),
            ("convertisseurs[0].boucle_chaude.condensateurs", ["C20"],
             "convertisseurs[0].boucle_chaude.condensateurs : copie de cond_entree attendue (topologie abaisseur, "
             "§2.4)"),
            ("carte.decoupes[0].proprietaire", None,
             "carte.decoupes[0].proprietaire : la pièce M4_ALIM1 ne cite pas la découpe D1 dans ses decoupes"),
        ]
        for chemin, valeur, attendu in cas:
            with self.subTest(chemin):
                if valeur is None:  # découpe sans propriétaire, mais citée par M4_ALIM1
                    doc = mettre(donnees, chemin, None)
                    self.assertIn("composants[15].decoupes[0] : découpe D1 dont la pièce n'est pas propriétaire "
                                  "(carte.decoupes)", formats.valider_donnees_placeur(doc))
                    doc = mettre(donnees, "composants[15].decoupes", [])
                    self.refus("donnees_placeur", doc, attendu)
                    continue
                self.refus("donnees_placeur", mettre(donnees, chemin, valeur), attendu,
                           "donnees_placeur.json" if chemin.endswith("plan_couches") else None)
        double = copie(donnees)
        double["composants"].append(copie(donnees["composants"][2]))
        self.refus("donnees_placeur", double, "composants[17].nom : « M2 » en double")
        config = DOCUMENTS["config_carte"]
        self.refus("config_carte", mettre(config, "convertisseurs[0].topologie", "elevateur"),
                   "convertisseurs[0].boucle_chaude.condensateurs : copie de cond_sortie attendue (topologie "
                   "elevateur, §2.4)")
        self.refus("config_carte", mettre(config, "convertisseurs[0].commutation", "integre"),
                   "convertisseurs[0].commutation : « integre » : haut, bas et pilote désignent le même circuit")
        double = copie(config)
        double["convertisseurs"].append(copie(config["convertisseurs"][0]))
        self.refus("config_carte", double, "convertisseurs[1].reseau_commutation : « NetC19_1 » en double")
        entree = DOCUMENTS["entree_carte"]
        self.refus("entree_carte", mettre(entree, "carte.noms_couches_cuivre", ["TopLayer"]),
                   "carte.noms_couches_cuivre : copie incohérente avec carte.couches (noms et nombre)")
        self.refus("entree_carte", dict(entree, reseaux=entree["reseaux"] + ["GND"]), "reseaux[14] : « GND » en double")
        self.refus("entree_carte", mettre(entree, "pastilles_libres[0].id", "__libre_3"),
                   "pastilles_libres[0].id : « __libre_0 » attendu, « __libre_3 » trouvé")
        verdict = DOCUMENTS["verdict"]
        self.refus("verdict", mettre(verdict, "violations", 2),
                   "violations : 1 attendu (somme de violations_par_type), 2 trouvé")
        self.refus("verdict", mettre(verdict, "valide", True),
                   "valide : false attendu (liaisons_ouvertes = 0 et violations = 0)")
        self.refus("verdict", mettre(verdict, "detail_violations", []),
                   "detail_violations : un détail par violation attendu (1), 0 trouvé(s)")
        self.refus("verdict", mettre(verdict, "marge_isolement_min_paire", ""),
                   "marge_isolement_min_paire : \"\" si et seulement si marge_isolement_min_mm est null")
        self.refus("verdict", mettre(verdict, "exemples", {"court": ["x"]}),
                   "exemples.court : valeur non permise (texte « court » trouvé ; attendu : « isolement », "
                   "« garde_bord », « garde_decoupe », « garde_trou », « zone_interdite », « largeur », "
                   "« dimension_via », « via_sous_cms », « trou_trou », « antenne », « couche_non_permise », "
                   "« reseau_inconnu »)")
        self.refus("verdict", mettre(verdict, "exemples.largeur", ["x"] * 16),
                   "exemples.largeur : liste d'au plus 15 éléments attendue, 16 trouvés")
        self.refus("verdict", mettre(verdict, "placement.valide", True),
                   "placement.valide : false attendu (1 violation(s))")
        self.refus("controle_placement", mettre(DOCUMENTS["controle_placement"], "valide", True),
                   "valide : false attendu (1 violation(s))")
        mesures = DOCUMENTS["mesures"]
        faux = mesures["cout_commun"] + 1.0
        self.refus("mesures", mettre(mesures, "cout_commun", faux),
                   f"cout_commun : {mesures['cout_commun']!r} attendu (formule cout_commun/2 appliquée aux termes, "
                   f"§4.7), {faux!r} trouvé")
        stats = DOCUMENTS["stats"]
        self.refus("stats", mettre(stats, "cause", ""), "cause : \"\" si et seulement si valide (§2.7)")
        self.refus("stats", mettre(stats, "validite", None),
                   "validite : null seulement pour la cause « non verifie »")
        self.assertEqual(formats.valider_stats(mettre(mettre(stats, "validite", None), "cause", "non verifie")), [])
        self.refus("stats", mettre(stats, "parametres_effectifs.campagne.threads", 0),
                   "parametres_effectifs.campagne.threads : champ exclu des paramètres effectifs (§2.7)")
        self.refus("stats", mettre(stats, "validite.violations", 2),
                   "validite.violations : 0 attendu (somme de violations_par_type), 2 trouvé")
        self.refus("placements", mettre(DOCUMENTS["placements"], "pieces[1].designator", "U10"),
                   "pieces[1].designator : « U10 » en double")
        v1 = lire_exemple("placements_v1.json")
        self.refus("placements", mettre(v1, "[1].designator", "U10"), "[1].designator : « U10 » en double")

    def test_champs_supprimes(self):
        """Champs supprimés par §2.5 et §2.7 refusés : bloc, trous_m3, pose_v3… (données), note, stats… (routage)."""
        donnees = DOCUMENTS["donnees_placeur"]
        for cle in ("bloc", "trous_m3", "fentes", "soft_sw", "poids_reseaux", "reseaux_plans", "classes",
                    "pieces_pas_fin", "couches_cuivre"):
            with self.subTest(cle):
                self.refus("donnees_placeur", dict(donnees, **{cle: []}), f"{cle} : champ supprimé par le contrat",
                           "donnees_placeur.json")
        for chemin in ("composants[0].pose_v3", "composants[0].via_sous_pastilles_permis", "regles.rayon_trou_m3",
                       "regles.largeurs", "regles.via_sous_cms_permis", "regles.isolement.COMMUTATION",
                       "regles.isolement.intra_pieces", "carte.fentes"):
            with self.subTest(chemin):
                self.refus("donnees_placeur", mettre(donnees, chemin, 1), f"{chemin} : champ supprimé par le contrat",
                           "donnees_placeur.json" if chemin.endswith("pose_v3") else None)
        self.refus("donnees_placeur", mettre(donnees, "composants[0].statut", "bloc"),
                   "composants[0].statut : valeur non permise (texte « bloc » trouvé ; attendu : « mobile », "
                   "« fixe »)")
        for cle in ("note", "raccords_bloc", "segments_touchant_emprise", "stats"):
            with self.subTest(cle):
                self.refus("routage", dict(DOCUMENTS["routage"], **{cle: {}}),
                           f"{cle} : champ supprimé par le contrat", "routage.json")
        # Un champ inconnu (évolution mineure, §0.5) est ignoré ; les sorties v1 du moteur restent lisibles.
        self.assertEqual(formats.valider_routage(dict(DOCUMENTS["routage"], nouveau=1)), [])
        self.assertEqual(formats.valider_routage(dict(lire_exemple("routage_v1.json"), note="", stats={})), [])

    def test_types_et_null(self):
        """Types (entier ≠ flottant, nombre entre guillemets signalé), énumérations, null seulement où la table le
        permet, longueurs des points et des poses, champs exclusifs, nombre d'erreurs borné."""
        donnees = DOCUMENTS["donnees_placeur"]
        cas = [
            ("donnees_placeur", "couches[0].rang", 0.0, "couches[0].rang : entier attendu, 0.0 trouvé"),
            ("donnees_placeur", "composants[0].pose_cible", [86.3, 40.2, 0.0],
             "composants[0].pose_cible : liste de 4 éléments attendue, 3 trouvé(s)"),
            ("donnees_placeur", "composants[0].pose_cible[3]", 0,
             "composants[0].pose_cible[3] : booléen attendu, 0 trouvé"),
            ("donnees_placeur", "carte.contour[1]", [164, 0, 0],
             "carte.contour[1] : liste de 2 éléments attendue, 3 trouvé(s)"),
            ("donnees_placeur", "reseaux[0].poids", -1, "reseaux[0].poids : valeur ≥ 0 attendue, -1 trouvé"),
            ("donnees_placeur", "reseaux[0].priorite", "1",
             "reseaux[0].priorite : entier attendu, texte « 1 » trouvé (nombre écrit entre guillemets ?)"),
            ("donnees_placeur", "reseaux[0].role", "masse",
             "reseaux[0].role : valeur non permise (texte « masse » trouvé ; attendu : « signal », « alimentation », "
             "« puissance », « commutation », « plan »)"),
            ("donnees_placeur", "reseaux[0].classe", None, "reseaux[0].classe : texte attendu, null trouvé"),
            ("donnees_placeur", "couches[0].epaisseur_cuivre_mm", None,
             "couches[0].epaisseur_cuivre_mm : nombre attendu, null trouvé"),
            ("donnees_placeur", "source.construit_par", "B",
             "source.construit_par : « construire_donnees <version> » attendu, texte « B » trouvé"),
            ("verdict", "marge_isolement_min_mm", "0.1",
             "marge_isolement_min_mm : nombre attendu, texte « 0.1 » trouvé (nombre écrit entre guillemets ?)"),
            ("stats", "fin", "fini", "fin : valeur non permise (texte « fini » trouvé ; attendu : « normale », "
                                     "« arret_apres », « abandon », « plafond_temps », « arret_demande », "
                                     "« erreur_interne »)"),
            ("config_pcbdoc", "serigraphie.tailles_mm", "petit",
             "serigraphie.tailles_mm : « auto » ou liste de [hauteur, épaisseur] attendu, texte « petit » trouvé"),
            ("rapport", "graines[0].code", None, "graines[0].code : entier attendu, null trouvé"),
            ("deduction", "elements[0].objet", "MidLayer1",
             "elements[0].objet : « couche:<nom> », « reseau:<nom> », « piece:<nom> », « convertisseur:<nom> » ou "
             "« groupe:<nom> » attendu, texte « MidLayer1 » trouvé"),
            ("placements", "pieces[0].side", "dessus",
             "pieces[0].side : valeur non permise (texte « dessus » trouvé ; attendu : « top », « bottom »)"),
            ("etalonnage", "score", True, "score : nombre attendu, booléen trouvé"),
            ("routage", "pistes[0]", [], "pistes[0] : objet attendu, liste trouvée"),
        ]
        for nom, chemin, valeur, attendu in cas:
            with self.subTest(format=nom, champ=chemin):
                self.refus(nom, mettre(DOCUMENTS[nom], chemin, valeur), attendu)
        # null permis là où la table le dit.
        for nom, chemin in (("donnees_placeur", "reseaux[0].couche_preferee"), ("donnees_placeur", "trous[0].piece"),
                            ("donnees_placeur", "source.contraintes"), ("stats", "cout_commun"),
                            ("rapport", "meilleure"), ("rapport", "reference_existante"),
                            ("config_pcbdoc", "serigraphie.garde_masque_mm"),
                            ("entree_carte", "carte.aire_reference_mm2"), ("verdict", "mesures.ecart_min_mm")):
            with self.subTest(null=chemin):
                self.assertEqual(formats.VALIDATEURS[nom](mettre(DOCUMENTS[nom], chemin, None)), [])
        # Tailles de sérigraphie en liste ; obstacle en polygone ou en rectangle (un et un seul).
        self.assertEqual(formats.valider_config_pcbdoc(
            mettre(DOCUMENTS["config_pcbdoc"], "serigraphie.tailles_mm", [[1.0, 0.15], [0.9, 0.135]])), [])
        deux = mettre(donnees, "obstacles_placement[0].polygone", [[0, 0], [1, 0], [1, 1]])
        self.assertIn("obstacles_placement[0] : un et un seul champ parmi « rect » ou « polygone » attendu, "
                      "2 présent(s)", formats.valider_donnees_placeur(deux))
        # Racine d'un autre type ; nombre d'erreurs borné.
        self.assertEqual(formats.valider_donnees_placeur([]), ["racine : objet JSON attendu"])
        erreurs = formats.valider_routage(mettre(DOCUMENTS["routage"], "pistes", [{}] * 100))
        self.assertEqual(len(erreurs), formats.MAX_ERREURS + 1)
        self.assertEqual(erreurs[-1], "… et 500 autre(s) erreur(s)")

    def test_stats_ko_exact_et_phases(self):
        """stats/2 : ``routage_initial.ko_exact`` écrit "9" refusé avec son chemin ; ``temps_phases_s.boucle``
        absent refusé ; champs de A.5 typés."""
        stats = DOCUMENTS["stats"]
        self.refus("stats", mettre(stats, "routage_initial.ko_exact", "9"),
                   "routage_initial.ko_exact : entier attendu, texte « 9 » trouvé (nombre écrit entre guillemets ?)",
                   "stats.json")
        self.refus("stats", mettre(stats, "execution.temps_phases_s.boucle", None, supprimer=True),
                   "execution.temps_phases_s.boucle : champ manquant", "stats.json")
        for chemin, valeur, attendu in (
                ("recuit.exploration.paliers", 1.5, "recuit.exploration.paliers : entier attendu, 1.5 trouvé"),
                ("recuit.exploration", {"paliers": 1}, "recuit.exploration.mouvements : champ manquant"),
                ("convertisseurs[0].noeud_estime_mm", "22.3",
                 "convertisseurs[0].noeud_estime_mm : nombre attendu, texte « 22.3 » trouvé (nombre écrit entre "
                 "guillemets ?)"),
                ("diagnostic.echecs_recherche", {"fenetre_epuisee": 0, "plafond_expansions": 0,
                                                 "aucun_site_via_plan": 0},
                 "diagnostic.echecs_recherche.autre : champ manquant"),
                ("routage_initial.echecs_routeur", -1,
                 "routage_initial.echecs_routeur : valeur ≥ 0 attendue, -1 trouvé"),
                ("validite.placement", None, "validite.placement : objet attendu, null trouvé")):
            with self.subTest(chemin):
                self.refus("stats", mettre(stats, chemin, valeur), attendu)
        self.refus("stats", mettre(stats, "validite.placement", None, supprimer=True),
                   "validite.placement : champ manquant")

    def test_mesure_convertisseurs_avenant_a6(self):
        """mesure_convertisseurs/1 avec les champs de l'avenant 2.2 (A.6) : accepté et typé ; sans eux : accepté."""
        doc = DOCUMENTS["mesure_convertisseurs"]
        mesure = doc["convertisseurs"][0]
        self.assertEqual((mesure["noeud_vias"], mesure["sensibles_distance_min_mm"]), (0, 8.7))
        self.assertEqual(formats.valider_mesure_convertisseurs(doc), [])
        self.assertEqual(formats.valider_mesure_convertisseurs(
            mettre(doc, "convertisseurs[0].sensibles_distance_min_mm", None)), [])
        self.refus("mesure_convertisseurs", mettre(doc, "convertisseurs[0].noeud_vias", 1.0),
                   "convertisseurs[0].noeud_vias : entier attendu, 1.0 trouvé")
        self.refus("mesure_convertisseurs", mettre(doc, "convertisseurs[0].sensibles_distance_min_mm", "8"),
                   "convertisseurs[0].sensibles_distance_min_mm : nombre attendu, texte « 8 » trouvé (nombre écrit "
                   "entre guillemets ?)")


BS = chr(92)  # barre oblique inverse, pour écrire des échappements JSON (« \ud800 ») sans ambiguïté
ETALONNAGE = '{"format": "etalonnage/1", "score": %s}'


class TestCorrectionsEtape2(unittest.TestCase):
    """Corrections demandées par le vérificateur indépendant (étape 2, tour 1)."""

    def test_parametres_effectifs_complets(self):
        """stats.parametres_effectifs et rapport.parametres.effectifs : parametres_moteur complets (§2.7, §2.9) ;
        chaque section de l'annexe C obligatoire et objet ; campagne.threads exclu de stats seulement."""
        stats, rapport = DOCUMENTS["stats"], DOCUMENTS["rapport"]
        sections = [s for s in formats.CLES_PARAMETRES_MOTEUR if s != "format"]
        self.assertEqual(len(sections), 12)
        for s in sections:
            with self.subTest(s):
                self.assertIn(f"parametres_effectifs.{s} : champ manquant",
                              formats.valider_stats(mettre(stats, f"parametres_effectifs.{s}", None, True)))
                self.assertIn(f"parametres.effectifs.{s} : champ manquant",
                              formats.valider_rapport(mettre(rapport, f"parametres.effectifs.{s}", None, True)))
        self.assertEqual(len(formats.valider_stats(mettre(stats, "parametres_effectifs", {}))), 12)
        erreurs = formats.valider_stats(mettre(stats, "parametres_effectifs.recuit", "x"))
        self.assertTrue(any(e.startswith("parametres_effectifs.recuit : objet attendu") for e in erreurs), erreurs)
        erreurs = formats.valider_rapport(mettre(rapport, "parametres.effectifs.recuit", 3))
        self.assertTrue(any(e.startswith("parametres.effectifs.recuit : objet attendu") for e in erreurs), erreurs)
        self.assertEqual(formats.valider_rapport(mettre(rapport, "parametres.effectifs.campagne.threads", 4)), [])

    def test_routage_v2_sans_format(self):
        """Un routage sans « format » qui porte couches_routage (clé propre à routage/2) n'est pas une forme v1."""
        routage = DOCUMENTS["routage"]
        attendu = ["format : champ manquant (couches_routage présent : forme routage/2, pas v1)"]
        sans = mettre(routage, "format", None, True)
        self.assertEqual(formats.valider_routage(dict(sans, note="x", stats={})), attendu)
        self.assertEqual(formats.valider_routage(mettre(routage, "format", None)), ["format : champ manquant"])
        self.assertEqual(formats.valider_routage(lire_exemple("routage_v1.json")), [])

    def test_placement_impossible_piece_et_raison(self):
        """cause « placement impossible » : piece et raison obligatoirement renseignées (§2.7)."""
        stats = dict(DOCUMENTS["stats"], valide=False, cause="placement impossible", piece="U10",
                     raison="aucune position légale")
        self.assertEqual(formats.valider_stats(stats), [])
        for champ in ("piece", "raison"):
            with self.subTest(champ):
                self.assertEqual(formats.valider_stats(dict(stats, **{champ: None})),
                                 [f"{champ} : texte attendu avec la cause « placement impossible » (§2.7), null "
                                  f"trouvé"])

    def test_nombres_hors_bornes_en_memoire(self):
        """Un dict construit en mémoire avec un entier géant, l'infini ou NaN : liste d'erreurs, jamais d'exception."""
        mesures = DOCUMENTS["mesures"]
        for valeur in (10 ** 400, -10 ** 309, float("inf"), float("nan")):
            with self.subTest(valeur=repr(valeur)[:12]):
                self.assertEqual(formats.valider_mesures(mettre(mesures, "termes.vias" if isinstance(valeur, int)
                                                                else "termes.longueur_mm", valeur)),
                                 ["termes.vias : nombre hors des bornes" if isinstance(valeur, int)
                                  else "termes.longueur_mm : nombre hors des bornes"])

    def test_largeur_zone_interdite_polyligne_seulement(self):
        """carte.zones_interdites[].largeur : exigée pour une polyligne seulement (§2.2, « largeur (polyligne) »)."""
        entree = DOCUMENTS["entree_carte"]
        self.assertEqual(formats.valider_entree_carte(mettre(entree, "carte.zones_interdites[0].largeur", None,
                                                             True)), [])
        polyligne = mettre(entree, "carte.zones_interdites[0].forme", "polyligne")
        self.assertEqual(formats.valider_entree_carte(polyligne), [])
        self.assertEqual(formats.valider_entree_carte(mettre(polyligne, "carte.zones_interdites[0].largeur", None,
                                                             True)),
                         ["carte.zones_interdites[0].largeur : champ manquant"])

    def test_sortie_redirigee_utf8(self):
        """Sortie d'erreur redirigée (pas une console) : messages en UTF-8 (§0.2-10)."""
        d = Dossier(self)
        chemin = d.ecrire("neg.json", dict(DOCUMENTS["etalonnage"], score=-1))
        r = subprocess.run([sys.executable, "-B", os.path.join(ICI, "formats.py"), "valider", chemin],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.decode("utf-8").strip(), "neg.json : score : valeur ≥ 0 attendue, -1 trouvé")


class TestLectureStricte(unittest.TestCase):
    """Lecture JSON au moins aussi stricte que serde_json (lecteur du moteur et de validite.rs) ; une entrée, quelle
    qu'elle soit, donne le code 2 et un message clair, jamais une erreur interne (§0.2-7)."""

    def verifier_refus(self, d, nom, contenu, debut, format_attendu="etalonnage"):
        """charger et la ligne de commande refusent le fichier : ErreurFormat, code 2, message sans dossier."""
        chemin = d.ecrire(nom, contenu)
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.charger(chemin, format_attendu)
        self.assertTrue(str(e.exception).startswith(debut), str(e.exception))
        code, sortie, erreur = lancer_main(["valider", chemin])
        self.assertEqual((code, sortie), (2, ""), erreur)
        self.assertTrue(erreur.startswith(debut), erreur)
        self.assertNotIn(d.chemin, erreur)

    def test_nombres_hors_bornes(self):
        """Nombre de valeur absolue supérieure à 1e308 (serde_json refuse au-delà du plus grand flottant) : refusé
        avec le chemin du champ ; 1e308 et 1e-400 (arrondi à 0) acceptés."""
        d = Dossier(self)
        refuses = {
            "grand.json": ETALONNAGE % "1e400",
            "moins_grand.json": ETALONNAGE % "-1e400",
            "entier_309.json": ETALONNAGE % ("9" * 309),
            "entier_5000.json": ETALONNAGE % ("1" * 5000),
            "imbrique.json": '{"format": "etalonnage/1", "score": 1, "_x": [0, {"y": 2e308}]}',
        }
        for nom, contenu in refuses.items():
            with self.subTest(nom):
                champ = "_x[1].y" if nom == "imbrique.json" else "score"
                self.verifier_refus(d, nom, contenu, f"{nom} : {champ} : nombre hors des bornes")
        racine = d.ecrire("racine.json", "1e999")
        code, _, erreur = lancer_main(["valider", racine])
        self.assertEqual(code, 2)
        self.assertTrue(erreur.startswith("racine.json : racine : nombre hors des bornes"), erreur)
        for nom, texte, valeur in (("borne.json", "1e308", 1e308), ("moins_borne.json", "-1e308", -1e308),
                                   ("petit.json", "1e-400", 0.0), ("entier_308.json", "1" + "0" * 308, 10 ** 308)):
            with self.subTest(nom):
                # Valeur dans un commentaire « _v » : le schéma d'etalonnage/1 veut un score ≥ 0.
                chemin = d.ecrire(nom, '{"format": "etalonnage/1", "score": 1, "_v": %s}' % texte)
                self.assertEqual(formats.charger(chemin, "etalonnage")["_v"], valeur)
                self.assertEqual(lancer_main(["valider", chemin])[0], 0)

    def test_cles_en_double(self):
        """Clé en double d'un même objet refusée avec son chemin (serde_json garderait la dernière valeur en
        silence) ; commentaires « _* » répétés acceptés (§0.5)."""
        d = Dossier(self)
        self.verifier_refus(d, "double.json", '{"format": "etalonnage/1", "score": 1, "score": 2}',
                            "double.json : score : clé en double")
        self.verifier_refus(d, "double_profond.json",
                            '{"format": "etalonnage/1", "score": 1, "_x": [{"a": 1}, {"b": 1, "c": 0, "b": 2}]}',
                            "double_profond.json : _x[1].b : clé en double")
        chemin = d.ecrire("commentaires.json", '{"_": "a", "format": "etalonnage/1", "_": "b", "score": 1}')
        self.assertEqual(formats.charger(chemin, "etalonnage")["score"], 1)
        contraintes = d.ecrire("contraintes.json", '{"format": "contraintes/2", "fixes": [], "fixes": ["P1"]}')
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.lire_contraintes(contraintes)
        self.assertEqual(str(e.exception), "contraintes.json : fixes : clé en double")

    def test_substituts_isoles(self):
        """Demi-caractère de substitution isolé (« \\ud800 », refusé par serde_json) : refusé, dans une valeur comme
        dans une clé ; une paire (« \\ud83d\\ude00 ») est acceptée."""
        d = Dossier(self)
        self.verifier_refus(d, "valeur.json", ETALONNAGE % ('"' + BS + 'ud800"'),
                            "valeur.json : score : texte contenant un demi-caractère de substitution isolé")
        self.verifier_refus(d, "cle.json", '{"format": "etalonnage/1", "score": 1, "' + BS + 'udc00": 1}',
                            "cle.json : racine : clé contenant un demi-caractère de substitution isolé")
        chemin = d.ecrire("paire.json", '{"format": "etalonnage/1", "score": 1, "_t": "' + BS + 'ud83d' + BS
                          + 'ude00"}')
        self.assertEqual(formats.charger(chemin, "etalonnage")["_t"], "\U0001F600")

    def test_imbrication(self):
        """127 niveaux de listes et d'objets lus, 128 refusés (comme serde_json) ; 100 000 niveaux refusés de même,
        par la fonction et par la ligne de commande réelle (code 2, et non 4)."""
        d = Dossier(self)
        chemin = d.ecrire("p127.json", '{"format": "etalonnage/1", "score": 1, "_x": ' + "[" * 126 + "1" + "]" * 126
                          + "}")
        self.assertEqual(lancer_main(["valider", chemin])[0], 0)
        # La racine et 126 objets imbriqués : 127 niveaux.
        objets = d.ecrire("p127o.json", '{"format": "etalonnage/1", "score": 1, "_": '
                          + '{"a": ' * 126 + "1" + "}" * 126 + "}")
        self.assertEqual(lancer_main(["valider", objets])[0], 0)
        self.verifier_refus(d, "p128o.json", '{"format": "etalonnage/1", "score": 1, "_": '
                            + '{"a": ' * 127 + "1" + "}" * 127 + "}", "p128o.json : imbrication trop profonde")
        self.verifier_refus(d, "p128.json", ETALONNAGE % ("[" * 127 + "1" + "]" * 127),
                            "p128.json : imbrication trop profonde (plus de 127 niveaux")
        profond = "[" * 100000 + "]" * 100000
        self.verifier_refus(d, "profond.json", profond, "profond.json : imbrication trop profonde")
        with self.assertRaises(formats.ErreurFormat):
            formats.lire_contraintes(d.ecrire("contraintes.json", profond))
        p = subprocess.run([sys.executable, "-B", os.path.join(ICI, "formats.py"), "valider",
                            os.path.join(d.chemin, "profond.json")], capture_output=True, encoding="utf-8",
                           errors="replace")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertTrue(p.stderr.startswith("profond.json : imbrication trop profonde"), p.stderr)

    def test_version_non_ascii_ou_mal_formee(self):
        """Version écrite autrement qu'en chiffres ASCII (« verdict/² », chiffres arabes, 5 000 chiffres) ou champ
        « format » qui n'est pas un texte : refusés (code 2), jamais une erreur interne."""
        d = Dossier(self)
        self.verifier_refus(d, "sup.json", '{"format": "verdict/²"}', "sup.json : format verdict/² non pris en charge",
                            "verdict")
        self.verifier_refus(d, "arabe.json", '{"format": "verdict/٢"}',
                            "arabe.json : format verdict/٢ non pris en charge", "verdict")
        self.verifier_refus(d, "long.json", '{"format": "verdict/' + "1" * 5000 + '"}',
                            "long.json : format verdict/111", "verdict")
        code, _, erreur = lancer_main(["valider", d.ecrire("nombre.json", '{"format": 5}')])
        self.assertEqual((code, erreur.strip()), (2, "nombre.json : format : texte attendu"))
        chemin = d.ecrire("e.json", DOCUMENTS["etalonnage"])
        for attendu in ("etalonnage/²", "etalonnage/", "etalonnage/1x"):
            with self.subTest(attendu):
                with self.assertRaises(formats.ErreurFormat) as e:
                    formats.charger(chemin, attendu)
                self.assertEqual(str(e.exception), f"e.json : format {attendu} non pris en charge")

    def test_ecriture_sans_erreur_interne(self):
        """Un caractère que le flux de sortie ne sait pas encoder est remplacé, sans exception."""
        flux = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="\n")
        formats._ecrire("clé « x » " + chr(0xD800), flux)
        flux.flush()
        self.assertEqual(flux.buffer.getvalue(), b"cl? ? x ? ?\n")


# --- Étape 3 : fichiers écrits à la main -------------------------------------------------------------------------------

SCHEMAS = os.path.join(ICI, "corpus", "schemas")
DEFAUT = os.path.join(SCHEMAS, "parametres_moteur.defaut.json")
CONTRAT = os.path.join(ICI, "..", "CONTRAT.md")
AVENANT = os.path.join(ICI, "..", "AVENANT_2.2.md")
C2 = {"format": "contraintes/2"}


def _avec(nom, chemin, valeur, supprimer=False):
    return mettre(DOCUMENTS[nom], chemin, valeur, supprimer)


# (fichier, contenu : dict ou texte JSON, lecteur, début attendu du message après « <fichier> : ») ; le message
# commence par le chemin du champ fautif (ou le nomme : « 'fixe' inconnue… », « format … », « JSON invalide, ligne »).
VARIANTES = [
    # contraintes/2 (écrit à la main)
    ("contraintes.json", dict(C2, fixe=[]), "contraintes", "'fixe' inconnue ; vouliez-vous dire 'fixes' ?"),
    ("contraintes.json", dict(C2, au_bord=[{"piece": "P1", "cote": "bas", "cotee": 1}]), "contraintes",
     "au_bord[0] : 'cotee' inconnue ; vouliez-vous dire 'cote' ?"),
    ("contraintes.json", dict(C2, au_bord=[{"piece": "P1"}]), "contraintes", "au_bord[0].cote : champ manquant"),
    ("contraintes.json", '{"format": "contraintes/2", "boites": {"U1": [1,5, 2, 3, 4]}}', "contraintes",
     "boites.U1 : liste de 4 éléments attendue, 5 trouvé(s) (virgule décimale ?)"),
    ("contraintes.json", '{"format": "contraintes/2", "fixes_deplaces": {"P1": [82,0, 97,0]}}', "contraintes",
     "fixes_deplaces.P1 : liste de 2 éléments attendue, 4 trouvé(s) (virgule décimale ?)"),
    ("contraintes.json", dict(C2, orientations={"C1": [[90]]}), "contraintes",
     "orientations.C1[0] : liste de 2 éléments attendue, 1 trouvé(s) (virgule décimale ?)"),
    ("contraintes.json", dict(C2, marge_bord_mm="1.5"), "contraintes",
     "marge_bord_mm : nombre attendu, texte « 1.5 » trouvé (nombre écrit entre guillemets ?)"),
    ("contraintes.json", dict(C2, faces_par_defaut="toutes"), "contraintes", "faces_par_defaut : valeur non permise"),
    ("contraintes.json", dict(C2, pas_fin_mm=None), "contraintes", "pas_fin_mm : nombre attendu, null trouvé"),
    ("contraintes.json", {"format": "contraintes/3"}, "contraintes", "format contraintes/3 non pris en charge"),
    ("contraintes.json", dict(C2, zones_interdites=[{"rect": [0, 0, 1, 1], "polygone": [[0, 0], [1, 0], [0, 1]]}]),
     "contraintes", "zones_interdites[0] : un et un seul champ parmi « rect » ou « polygone » attendu"),
    ("contraintes.json", dict(C2, motif_canaux="(_CH"), "contraintes", "motif_canaux : expression régulière invalide"),
    ("contraintes.json", dict(C2, garde_bord_mm=-1), "contraintes", "garde_bord_mm : valeur ≥ 0 attendue"),
    ("contraintes.json", dict(C2, fixer_suggestions="oui"), "contraintes", "fixer_suggestions : booléen attendu"),
    ("contraintes.json", dict(C2, convertisseurs={"modifier": [{"cond_bulk": ["C17"]}]}), "contraintes",
     "convertisseurs.modifier[0].reseau_commutation : champ manquant"),
    ("contraintes.json", dict(C2, charges=[{"broche": "P1", "courant_a": 1.0}]), "contraintes",
     "charges[0].broche : « PIECE.BROCHE » attendu"),
    ("contraintes.json", dict(C2, eloignements=[{"type": "ferme", "objets": ["U1"], "reference": ["M1"],
                                                 "distance_mm": 3.0}]), "contraintes",
     "eloignements[0].type : valeur non permise"),
    # contraintes v1 (sans « format »)
    ("contraintes_v1.json", {"fixes": [], "pas_fin_mm": 0.5}, "contraintes",
     "'pas_fin_mm' : clé de contraintes/2 dans un fichier sans « format »"),
    ("contraintes_v1.json", {"zones_autour": [{"empreinte_prefixe": "X", "face": 2, "demi_cote": 1.0}]},
     "contraintes", "zones_autour[0].face : valeur non permise"),
    ("contraintes_v1.json", {"marge_bords": 1.0}, "contraintes",
     "'marge_bords' inconnue ; vouliez-vous dire 'marge_bord' ?"),
    # parametres_moteur/1 (écrit à la main)
    ("parametres.json", {"routage": {"espacment": {}}}, "parametres",
     "routage.espacment : clé inconnue ; vouliez-vous dire espacement ?"),
    ("parametres.json", {"campagne": {"chaines": "4"}}, "parametres",
     "campagne.chaines : entier attendu, texte « 4 » trouvé (nombre écrit entre guillemets ?)"),
    ("parametres.json", {"conjointe": {"essais_par_piece_mobile": None}}, "parametres",
     "conjointe.essais_par_piece_mobile : nombre attendu, null trouvé"),
    ("parametres.json", {"campagne": {"arret_apres": "tout"}}, "parametres", "campagne.arret_apres : valeur non permise"),
    ("parametres.json", '{"boucle": {"tours": 4.0}}', "parametres", "boucle.tours : entier attendu, 4.0 trouvé"),
    ("parametres.json", {"format": "parametres_moteur/2"}, "parametres", "format parametres_moteur/2 non pris en charge"),
    ("parametres.json", {"recuit": 5}, "parametres", "recuit : objet attendu"),
    ("parametres.json", {"journal": {"debug": ["tout"]}}, "parametres", "journal.debug[0] : valeur non permise"),
    ("parametres.json", {"roles": {"signal": {"couches": [{"si": "dehors", "facteur": 1.0}]}}}, "parametres",
     "roles.signal.couches[0].si : valeur non permise"),
    # fichier --config de l'utilisateur
    ("config.json", {"format": "config_carte/2", "convertisseurs": [{"reseau_comutation": "SW"}]}, "config",
     "convertisseurs[0] : 'reseau_comutation' inconnue ; vouliez-vous dire 'reseau_commutation' ?"),
    ("config.json", {"convertisseurs": [{"bas": "M2", "reseau_entree": "+12V", "reseau_masse": "GND"}]}, "config",
     "convertisseurs[0].haut : champ manquant"),
    # JSON invalide (fichier écrit à la main) : ligne, colonne et cause probable
    ("contraintes.json", '{\n "fixes": ["P1"],\n}', "contraintes",
     "JSON invalide, ligne 3, colonne 1 : nom de clé entre guillemets doubles attendu (virgule finale ?)"),
    ("contraintes.json", "{'fixes': []}", "contraintes", "JSON invalide, ligne 1, colonne 2 :"),
    ("contraintes.json", '{"fixes": ["C:' + BS + 'x"]}', "contraintes", "JSON invalide, ligne 1, colonne 15 :"),
    # formats écrits par un programme
    ("donnees_placeur.json", _avec("donnees_placeur", "carte", None, supprimer=True), "donnees_placeur",
     "carte : champ manquant"),
    ("donnees_placeur.json", _avec("donnees_placeur", "convertisseurs[0].cond_bulk[0]", "C99"), "donnees_placeur",
     "convertisseurs[0].cond_bulk[0] : pièce C99 inconnue"),
    ("verdict.json", _avec("verdict", "format", "verdict/3"), "verdict", "format verdict/3 non pris en charge"),
    ("verdict.json", _avec("verdict", "outil", None), "verdict", "outil : texte attendu, null trouvé"),
    ("verdict.json", _avec("verdict", "mode", "souple"), "verdict", "mode : valeur non permise"),
    ("stats.json", _avec("stats", "routage_initial.ko_exact", "9"), "stats", "routage_initial.ko_exact : entier attendu"),
]

LECTEURS = {"contraintes": formats.lire_contraintes, "parametres": formats.lire_parametres_moteur,
            "config": formats.lire_config_utilisateur}


class TestEtape3(unittest.TestCase):
    """Fichiers écrits à la main (§0.5, §2.1, §2.3, §2.6, §3.4 R-V4), conversion v1, paramètres par défaut."""

    def test_variantes_fautives(self):
        """Au moins 30 variantes fautives, chacune refusée (ErreurFormat) avec un message qui commence par le nom du
        fichier puis nomme le champ fautif, sans dossier."""
        d = Dossier(self)
        for fichier, contenu, lecteur, attendu in VARIANTES:
            with self.subTest(attendu=attendu):
                chemin = d.ecrire(fichier, contenu)
                with self.assertRaises(formats.ErreurFormat) as e:
                    LECTEURS.get(lecteur, lambda c: formats.charger(c, lecteur))(chemin)
                messages = e.exception.erreurs
                self.assertTrue(any(m.startswith(f"{fichier} : {attendu}") for m in messages), messages)
                self.assertNotIn(d.chemin, str(e.exception))
        self.assertGreaterEqual(len(VARIANTES), 30)
        print(f"\n  {len(VARIANTES)} variantes fautives refusées ; chaque message nomme le champ fautif",
              file=sys.stderr)

    def test_cles_fixe_et_espacment(self):
        """« contraintes.json : 'fixe' inconnue ; vouliez-vous dire 'fixes' ? » (code 2 par la ligne de commande) ;
        « routage.espacment : clé inconnue ; vouliez-vous dire espacement ? »."""
        d = Dossier(self)
        chemin = d.ecrire("contraintes.json", dict(C2, fixe=["P1"]))
        attendu = "contraintes.json : 'fixe' inconnue ; vouliez-vous dire 'fixes' ?"
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.lire_contraintes(chemin)
        self.assertEqual(str(e.exception), attendu)
        self.assertEqual(lancer_main(["valider", chemin]), (2, "", attendu + "\n"))
        script = os.path.join(ICI, "formats.py")
        p = subprocess.run([sys.executable, "-B", script, "valider", chemin], capture_output=True, encoding="utf-8")
        self.assertEqual((p.returncode, p.stderr.strip()), (2, attendu))
        self.assertEqual(formats.valider_parametres_moteur({"routage": {"espacment": {"actif": True}}}),
                         ["routage.espacment : clé inconnue ; vouliez-vous dire espacement ?"])
        chemin = d.ecrire("parametres.json", {"routage": {"espacment": {}}})
        self.assertEqual(lancer_main(["valider", chemin]),
                         (2, "", "parametres.json : routage.espacment : clé inconnue ; vouliez-vous dire espacement ?\n"))

    def test_virgule_finale_bom_cp1252(self):
        """JSON à virgule finale : ligne, colonne et « virgule finale ? » ; BOM accepté ; cp1252 lu avec un
        avertissement (fichiers écrits à la main seulement)."""
        d = Dossier(self)
        for texte, ligne, colonne in (('{"format": "contraintes/2", "fixes": ["P1",]}', 1, 44),
                                      ('{\n "format": "contraintes/2",\n "fixes": ["P1"],\n}', 4, 1)):
            with self.assertRaises(formats.ErreurFormat) as e:
                formats.lire_contraintes(d.ecrire("virgule.json", texte))
            self.assertRegex(str(e.exception), rf"^virgule\.json : JSON invalide, ligne {ligne}, colonne {colonne} : "
                                               r".* \(virgule finale \?\)$")
        contenu = {"format": "contraintes/2", "fixes": ["ÉTAGE_1"], "_note": "créé au bloc-notes"}
        self.assertEqual(formats.lire_contraintes(d.ecrire("bom.json", contenu, bom=True)),
                         ({"format": "contraintes/2", "fixes": ["ÉTAGE_1"]}, []))
        self.assertEqual(formats.lire_parametres_moteur(d.ecrire("p.json", {"campagne": {"chaines": 2}}, bom=True)),
                         ({"campagne": {"chaines": 2}}, []))
        chemin = os.path.join(d.chemin, "cp1252.json")
        with open(chemin, "wb") as f:
            f.write(json.dumps(contenu, ensure_ascii=False).encode("cp1252"))
        contraintes, avertissements = formats.lire_contraintes(chemin)
        self.assertEqual(contraintes["fixes"], ["ÉTAGE_1"])
        self.assertEqual(len(avertissements), 1)
        self.assertTrue(avertissements[0].startswith("cp1252.json : pas en UTF-8 (octet "), avertissements)
        self.assertIn("lu en cp1252", avertissements[0])
        code, sortie, erreur = lancer_main(["valider", chemin])
        self.assertEqual(code, 0)
        self.assertIn("lu en cp1252", erreur)
        # Un fichier écrit par un programme doit être en UTF-8 : pas de repli.
        verdict = os.path.join(d.chemin, "verdict.json")
        with open(verdict, "wb") as f:
            f.write(json.dumps(dict(DOCUMENTS["verdict"], outil="vérif"), ensure_ascii=False).encode("cp1252"))
        self.assertEqual(lancer_main(["valider", verdict])[0], 2)
        with self.assertRaises(formats.ErreurFormat):
            formats.charger(verdict, "verdict")
        # Octet indéfini en cp1252 : illisible.
        with open(chemin, "wb") as f:
            f.write(b'{"fixes": ["\x81"]}')
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.lire_contraintes(chemin)
        self.assertIn("UTF-8 ou cp1252 attendu", str(e.exception))

    def test_causes_json(self):
        """Causes probables des erreurs JSON (§2.1)."""
        d = Dossier(self)
        for texte, cause in (("{'fixes': []}", "apostrophes au lieu de guillemets doubles ?"),
                             ('{"fixes": ["C:' + BS + 'x"]}', "barre oblique inverse non doublée ?"),
                             ('{"marge_bord_mm": 1,5}', "virgule décimale ?"),
                             ('{"fixes": True}', "true, false et null s'écrivent en minuscules"),
                             ('{fixes: []}', "nom de clé sans guillemets doubles ?"),
                             ('{"fixes": [] // pièces\n}', "commentaire ?"),
                             ('{"fixes": ["P1" "P2"]}', "virgule manquante ?")):
            with self.subTest(cause=cause):
                with self.assertRaises(formats.ErreurFormat) as e:
                    formats.lire_contraintes(d.ecrire("c.json", texte))
                self.assertIn(f"({cause}", str(e.exception))
                self.assertRegex(str(e.exception), r"^c\.json : JSON invalide, ligne \d+, colonne \d+ : ")

    def test_conversion_v1(self):
        """lire_contraintes(contraintes_v1.json) = contraintes_v1_converti.json (attendu écrit à la main), avec la
        configuration de l'exemple (référence de soft_sw) ; sans configuration, soft_sw est signalé et ignoré ;
        plans en contrôle de cohérence ; eloignement d'une configuration v1 ; le résultat est un contraintes/2."""
        v1 = os.path.join(EXEMPLES, "contraintes_v1.json")
        attendu = formats._sans_commentaires(lire_exemple("contraintes_v1_converti.json"))
        config = formats.charger(os.path.join(EXEMPLES, "config_carte.json"), "config_carte")
        contraintes, avertissements = formats.lire_contraintes(v1, config=config)
        self.assertEqual(contraintes, attendu)
        self.assertEqual(formats.valider_contraintes(contraintes), [])
        self.assertEqual(len(avertissements), 2)
        self.assertIn("converties en contraintes/2", avertissements[0])
        self.assertIn("plans (version 1) : GND", avertissements[1])
        self.assertEqual(sorted(lire_exemple("contraintes_v1.json")), sorted(formats.CLES_CONTRAINTES_V1 + ("_doc",)))
        # Sans configuration : tout sauf l'éloignement de soft_sw, signalé.
        sans, avertissements = formats.lire_contraintes(v1)
        self.assertEqual(sans, {k: v for k, v in attendu.items() if k != "eloignements"})
        self.assertTrue(any("soft_sw : non converti" in a for a in avertissements), avertissements)
        # plans : comparé aux plans déduits s'ils sont fournis ; jamais une surcharge.
        _, avertissements = formats.lire_contraintes(v1, config=config, plans_deduits=["GND"])
        self.assertFalse(any("plans" in a for a in avertissements), avertissements)
        _, avertissements = formats.lire_contraintes(v1, config=config, plans_deduits=["GND", "+8V"])
        self.assertTrue(any("plans déduits : +8V, GND : écart" in a for a in avertissements), avertissements)
        self.assertNotIn("couches", contraintes)
        # Configuration v1 (geom.rs) : eloignement → éloignement dure de référence [haut] ; soft_sw : haut et bas.
        config_v1 = {"convertisseurs": [{"haut": "Q1", "bas": "Q2", "reseau_entree": "+12V", "reseau_masse": "GND",
                                         "eloignement": [{"broches": ["U9.3", "U9.4"], "distance_mm": 25.0}]}]}
        contraintes, avertissements = formats.lire_contraintes(v1, config=config_v1)
        self.assertEqual(contraintes["eloignements"], [
            {"type": "souple", "objets": ["U10", "C19"], "reference": ["Q1", "Q2"], "distance_mm": 30.0,
             "poids_par_mm": 5.0},
            {"type": "dure", "objets": ["U9.3", "U9.4"], "reference": ["Q1"], "distance_mm": 25.0}])
        self.assertTrue(any("éloignements dure de référence [haut]" in a for a in avertissements))
        # Convertisseur intégré (haut = bas) : référence d'une seule pièce.
        integre = {"format": "config_carte/2", "convertisseurs": [{"haut": "U3", "bas": "U3"}]}
        self.assertEqual(formats.lire_contraintes(v1, config=integre)[0]["eloignements"][0]["reference"], ["U3"])
        with self.assertRaises(formats.ErreurFormat) as e:
            formats.lire_contraintes(v1, config={"convertisseurs": [{"haut": 3}]})
        self.assertIn("config.convertisseurs[0].haut : texte attendu", str(e.exception))
        # contraintes/2 : rendues telles quelles, sans les commentaires.
        self.assertEqual(formats.lire_contraintes(os.path.join(EXEMPLES, "contraintes.json")),
                         (formats._sans_commentaires(lire_exemple("contraintes.json")), []))

    def test_exemples_ecrits_a_la_main(self):
        """contraintes.json porte toutes les clés de §2.3 ; parametres_moteur_partiel.json et
        config_utilisateur_partielle.json sont acceptés ; la liste des clés de contraintes est celle du schéma."""
        self.assertEqual(formats.CLES_CONTRAINTES, tuple(formats._CHAMPS_CONTRAINTES))
        self.assertEqual(sorted(k for k in lire_exemple("contraintes.json") if not k.startswith("_")),
                         sorted(formats.CLES_CONTRAINTES))
        self.assertEqual(formats.valider_contraintes(lire_exemple("contraintes.json")), [])
        self.assertEqual(formats.valider_parametres_moteur(lire_exemple("parametres_moteur_partiel.json")), [])
        partielle = os.path.join(EXEMPLES, "config_utilisateur_partielle.json")
        self.assertEqual(formats.lire_config_utilisateur(partielle),
                         (formats._sans_commentaires(lire_exemple("config_utilisateur_partielle.json")), []))
        self.assertEqual(lancer_main(["valider", partielle])[0], 2, "sans l'option : config_carte/2 complet")
        self.assertEqual(lancer_main(["valider", partielle, "--config-utilisateur"])[0], 0)
        self.assertEqual(lancer_main(["valider", partielle, "--autre"])[0], 2)

    def test_config_utilisateur_v1(self):
        """config_carte.json v1 (écrit à la main pour 9b5b5a3) : reconnu sans « format », converti en
        config_carte/2 partiel (condensateurs en listes, eloignement retiré et signalé)."""
        d = Dossier(self)
        v1 = {"_doc": "v1", "convertisseurs": [{"nom": "hacheur", "haut": "M1", "bas": "M2", "pilote": "U10",
                                                "cond_entree": ["C7"], "cond_bulk": "C17", "cond_sortie": "",
                                                "reseau_entree": "+12V", "reseau_masse": "GND",
                                                "reseau_commutation": "",
                                                "eloignement": [{"broches": ["U25_VOLT1.27"], "distance_mm": 30.0}]}],
              "sans_piste_dessous": ["L1"]}
        chemin = d.ecrire("config_carte.json", v1)
        code, sortie, _ = lancer_main(["valider", chemin])
        self.assertEqual((code, sortie.strip()),
                         (0, "config_carte.json : conforme (config_utilisateur, forme v1, fichier --config partiel de "
                             "l'utilisateur)"))
        config, avertissements = formats.lire_config_utilisateur(chemin)
        self.assertEqual(config, {"format": "config_carte/2", "sans_piste_dessous": ["L1"], "convertisseurs": [
            {"nom": "hacheur", "haut": "M1", "bas": "M2", "pilote": "U10", "cond_entree": ["C7"],
             "cond_bulk": ["C17"], "cond_sortie": [], "reseau_entree": "+12V", "reseau_masse": "GND"}]})
        self.assertEqual(len(avertissements), 2)
        self.assertIn("eloignement : retiré", avertissements[1])

    def test_parametres_par_defaut(self):
        """parametres_moteur.defaut.json = annexe C modifiée par la partie D de l'avenant (« identique », code 0),
        conjointe.essais_par_piece_mobile = 6.0 ; accepté par valider_parametres_moteur (partiel) et par le schéma
        complet ; ses clés sont exactement celles du schéma ; un écart est signalé (code 1)."""
        defaut = json.load(open(DEFAUT, encoding="utf-8"))
        self.assertEqual(defaut["conjointe"]["essais_par_piece_mobile"], 6.0)
        self.assertIs(type(defaut["conjointe"]["essais_par_piece_mobile"]), float)
        self.assertEqual(formats.valider_parametres_moteur(defaut), [])
        self.assertEqual(formats._valider_main(defaut, formats.PARAMETRES_COMPLETS), [])

        def cles(schema, doc, chemin):
            schema = schema.schema if isinstance(schema, (formats.Fac, formats.Nul)) else schema
            schema = schema.schema if isinstance(schema, formats.Nul) else schema
            if isinstance(schema, formats.Obj) and isinstance(doc, dict):
                self.assertEqual(sorted(schema.champs), sorted(doc), chemin or "racine")
                for k, v in doc.items():
                    cles(schema.champs[k], v, f"{chemin}.{k}")
            elif isinstance(schema, formats.Liste) and isinstance(doc, list):
                for x in doc:
                    cles(schema.schema, x, chemin + "[]")

        cles(formats.PARAMETRES_PARTIELS, defaut, "")
        incomplet = copie(defaut)
        del incomplet["routage"]["espacement"]["coude"]
        self.assertEqual(formats._valider_main(incomplet, formats.PARAMETRES_COMPLETS),
                         ["routage.espacement.coude : champ manquant"])
        self.assertEqual(formats.valider_parametres_moteur(incomplet), [])
        sans_threads = copie(defaut)
        del sans_threads["campagne"]["threads"]
        self.assertEqual(formats._valider_main(sans_threads, formats.PARAMETRES_COMPLETS), [])
        if not (os.path.exists(CONTRAT) and os.path.exists(AVENANT)):
            self.skipTest("CONTRAT.md ou AVENANT_2.2.md absent (dépôt public) : égalité avec l'annexe C non testée")
        script = os.path.join(SCHEMAS, "extraire_parametres.py")
        source = ["--contrat", CONTRAT, "--avenant", AVENANT]
        p = subprocess.run([sys.executable, "-B", script] + source + ["--verifier", DEFAUT], capture_output=True,
                           encoding="utf-8")
        self.assertEqual((p.returncode, p.stdout.strip(), p.stderr), (0, "parametres_moteur.defaut.json : identique",
                                                                     ""))
        d = Dossier(self)
        sortie = os.path.join(d.chemin, "defaut.json")
        p = subprocess.run([sys.executable, "-B", script] + source + ["--sortie", sortie], capture_output=True,
                           encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(sortie, "rb") as f, open(DEFAUT, "rb") as g:
            self.assertEqual(f.read(), g.read(), "fichier suivi = sortie du script, octet pour octet")
        modifie = copie(defaut)
        modifie["conjointe"]["essais_par_piece_mobile"] = 6
        p = subprocess.run([sys.executable, "-B", script] + source + ["--verifier", d.ecrire("m.json", modifie)],
                           capture_output=True, encoding="utf-8")
        self.assertEqual(p.returncode, 1)
        self.assertIn("conjointe.essais_par_piece_mobile : flottant 6.0 attendu, entier 6 trouvé", p.stdout)
        p = subprocess.run([sys.executable, "-B", script, "--contrat", CONTRAT, "--verifier", DEFAUT],
                           capture_output=True, encoding="utf-8")
        self.assertEqual((p.returncode, p.stderr.strip()), (2, "--avenant obligatoire avec --contrat"))
        p = subprocess.run([sys.executable, "-B", script, "--moteur", os.path.join(d.chemin, "absent.exe"),
                            "--verifier", DEFAUT], capture_output=True, encoding="utf-8")
        self.assertEqual((p.returncode, p.stderr.strip()), (2, "absent.exe : moteur introuvable"))


if __name__ == "__main__":
    unittest.main()
