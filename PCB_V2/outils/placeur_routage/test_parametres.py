# -*- coding: utf-8 -*-
"""Tests de parametres.py (lot D ; contrat §5.4, §2.6, §2.9, §5.3 ; avenant 2.2, A.1 et partie D).

    cd PCB_V2/outils/placeur_routage
    python -m unittest test_parametres -v

- lecture et écriture des .ini : aller-retour (commentaires, BOM, CRLF, accents, chemins avec espace et
  apostrophe), tolérances de forme, erreurs de fond (code 2, message exact), clés inconnues, modèle ;
- priorités des fichiers (§5.4) : aucun .ini implicite en mode source ; ordre complet en mode figé simulé ;
- paramètres du moteur : niveaux obtenus du moteur (`parametres-defaut --qualite`) égaux à l'annexe C corrigée
  par la partie D de l'avenant et à la table des niveaux de §2.6 corrigée par A.1 ; défauts < --parametres-moteur <
  [avance] < options ; clés produites incluses dans l'annexe C pour toutes les combinaisons.
  Le moteur est ici le faux moteur de test_placeur.py (banc d'essai) ; le test est rejoué avec le vrai moteur
  quand le jalon A1 est fusionné ;
- [contraintes], parametres_utilises.ini, durées prévues.

Aucun chemin de la machine n'est écrit dans ce fichier : les chemins sont construits à l'exécution.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import chemins  # noqa: E402
import parametres as P  # noqa: E402
from test_placeur import (FAUX_MOTEUR, CONTRAT, annexe_c, annexe_c_corrigee, table_niveaux,  # noqa: E402
                          _lire)

COMMANDE_MOTEUR = FAUX_MOTEUR


def octets(chemin):
    with open(chemin, "rb") as f:
        return f.read()


class Dossier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_parametres_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def ecrire(self, nom, texte, encodage="utf-8", bom=True, crlf=True):
        chemin = os.path.join(self.tmp, nom)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        chemins.ecrire_atomique(chemin, texte, encodage=encodage, bom=bom, crlf=crlf)
        return chemin


# ------------------------------------------------------------------------------------------------ lecture, écriture

INI_ESSAI = """\
; Réglages de la carte d'essai — accents : é è à ç ô
[carte]
; carte placée dans un dossier dont le nom a une espace et une apostrophe
fichier = Mes cartes/L'essai d'été/carte.PcbDoc   ; commentaire en fin de ligne
contraintes =

[calcul]
qualite = rapide   # autre forme de commentaire
essais = 4
; ligne de commentaire au milieu d'une section

[contraintes]
fixes = J1
"""


class TestLectureEcriture(Dossier):

    def test_aller_retour_commentaires_bom_crlf_accents_chemins(self):
        ini = self.ecrire("carte.ini", INI_ESSAI)
        carte = os.path.join(self.tmp, "Mes cartes", "L'essai d'été", "carte.PcbDoc")
        sortie = os.path.join(self.tmp, "résultats ;x", "d'été")
        lu = P.lire_ini(ini)
        self.assertEqual(lu.avertissements, [])
        self.assertEqual(lu.valeurs[("carte", "fichier")], os.path.normpath(carte))
        self.assertEqual(lu.valeurs[("calcul", "qualite")], "rapide")
        valeurs = {"carte.fichier": carte, "carte.contraintes": os.path.join(self.tmp, "c d'x.json"),
                   "sortie.dossier": sortie, "calcul.essais": 12, "calcul.qualite": "soigne",
                   "contraintes.fixes": ["U1", "J1"], "contraintes.marge_bord_mm": 2.5,
                   "pcbdoc.serigraphie": False}
        P.enregistrer(ini, valeurs)
        brut = octets(ini)
        self.assertTrue(brut.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(brut.count(b"\n"), brut.count(b"\r\n"))
        texte = brut[3:].decode("utf-8")
        commentaires = [l for l in INI_ESSAI.splitlines() if l.strip().startswith(";")]
        self.assertEqual([l for l in texte.split("\r\n") if l.strip().startswith(";")], commentaires)
        self.assertIn("; commentaire en fin de ligne", texte)
        self.assertIn("# autre forme de commentaire", texte)
        relu = P.lire_ini(ini)
        self.assertEqual(relu.avertissements, [])
        attendu = {P._cle_pointee(k): v for k, v in valeurs.items()}
        for cle, v in attendu.items():
            if isinstance(v, str):
                v = os.path.normpath(v)
            self.assertEqual(relu.valeurs[cle], v, cle)
        self.assertIn('dossier = "', texte)                         # « ; » dans un chemin : guillemets
        P.enregistrer(ini, valeurs)
        self.assertEqual(octets(ini), brut)                         # écriture idempotente

    def test_creation_depuis_le_modele(self):
        ini = os.path.join(self.tmp, "nouveau.ini")
        P.enregistrer(ini, {"calcul.essais": 3})
        texte = octets(ini)[3:].decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(texte, re.sub(r"(?m)^essais =$", "essais = 3", P.MODELE_INI, count=1))
        self.assertEqual(P.lire_ini(ini).valeurs, {("calcul", "essais"): 3})    # seule clé renseignée

    def test_tolerances_de_forme(self):
        texte = ("[Calcul]\nQualité = Soigné\nessais = 100 000\nPriorite_Normale = OUI\n"
                 "[pcbdoc]\nproduire = Faux\nserigraphie = 0\nvias_couture = vrai\n"
                 "[contraintes]\nmarge_bord_mm = 2,5\ngarde_bord_mm=0.6\nhauteur_inconnue = Face entrée\n"
                 "fixes = U1 ,, J2 ,\nfaces_par_defaut = LIBRES\n")
        lu = P.analyser_ini(texte)
        self.assertEqual(lu.avertissements, [])
        v = lu.valeurs
        self.assertEqual(v[("calcul", "qualite")], "soigne")
        self.assertEqual(v[("calcul", "essais")], 100000)
        self.assertIs(v[("calcul", "priorite_normale")], True)
        self.assertEqual((v[("pcbdoc", "produire")], v[("pcbdoc", "serigraphie")], v[("pcbdoc", "vias_couture")]),
                         (False, False, True))
        self.assertEqual(v[("contraintes", "marge_bord_mm")], 2.5)
        self.assertEqual(v[("contraintes", "garde_bord_mm")], 0.6)
        self.assertEqual(v[("contraintes", "hauteur_inconnue")], "face_entree")
        self.assertEqual(v[("contraintes", "fixes")], ["U1", "J2"])
        self.assertEqual(v[("contraintes", "faces_par_defaut")], "libres")
        lu = P.analyser_ini("[avance]\nmouvements = 1e8\niters_det = 2,5e1\nboucle = 1E0\n")
        self.assertEqual((lu.valeurs[("avance", "mouvements")], lu.valeurs[("avance", "iters_det")],
                          lu.valeurs[("avance", "boucle")]), (100_000_000, 25, 1))
        for faux in ("1e-3", "2,55e1", "1e999", "1e"):
            with self.subTest(faux=faux), self.assertRaises(P.ErreurParametres) as e:
                P.analyser_ini(f"[avance]\nmouvements = {faux}\n")
            self.assertEqual(e.exception.message, f"[avance] mouvements : '{faux}' n'est pas un nombre entier")

    def test_repli_cp1252_signale(self):
        ini = self.ecrire("ancien.ini", "; réglé à la main\n[calcul]\nqualite = Soigné\n", encodage="cp1252",
                          bom=False)
        lu = P.lire_ini(ini)
        self.assertEqual(lu.encodage, "cp1252")
        self.assertEqual(lu.valeurs[("calcul", "qualite")], "soigne")
        self.assertEqual(lu.avertissements,
                         ["ancien.ini : fichier lu en Windows-1252 (et non en UTF-8) ; l'enregistrer en UTF-8"])

    def test_essais_huit_code_2_message_exact(self):
        ini = self.ecrire("mauvais.ini", "[calcul]\nessais = huit\n")
        with self.assertRaises(P.ErreurParametres) as e:
            P.lire_ini(ini)
        self.assertEqual(e.exception.message, "[calcul] essais : 'huit' n'est pas un nombre entier")
        self.assertEqual((e.exception.code, e.exception.fichier, e.exception.ligne), (2, "mauvais.ini", 2))
        r = chemins.executer([chemins.interpreteur(), os.path.join(ICI, "parametres.py"), "--verifier", ini],
                             delai_s=60)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.splitlines()[0], "[calcul] essais : 'huit' n'est pas un nombre entier")

    def test_erreurs_de_fond(self):
        cas = {
            "[pcbdoc]\nproduire = peut-être\n": "[pcbdoc] produire : 'peut-être' n'est ni oui ni non",
            "[calcul]\nqualite = excellente\n":
                "[calcul] qualite : 'excellente' n'est pas une valeur permise (apercu, rapide, normal, soigne)",
            "[calcul]\nessais = -1\n": "[calcul] essais : -1 doit être positif ou nul",
            "[avance]\niters_det = 0\n": "[avance] iters_det : 0 doit être au moins 1",
            "[contraintes]\npas_fin_mm = 0\n": "[contraintes] pas_fin_mm : 0 doit être strictement positif",
            "[contraintes]\nmarge_bord_mm = 1,2,3\n": "[contraintes] marge_bord_mm : '1,2,3' n'est pas un nombre",
            "[calcul]\nessais 8\n": "ligne 2 : 'essais 8' n'est pas de la forme « clé = valeur »",
            "essais = 8\n": "ligne 1 : clé 'essais' hors de toute section",
            "[calcul]\n\x01\x02abc\n": "ligne 2 : '??abc' n'est pas de la forme « clé = valeur »",
            "\x00\x01\x02": "fichier de paramètres illisible : parametres.ini (ce n'est pas un fichier texte)",
        }
        for texte, message in cas.items():
            with self.subTest(texte=texte), self.assertRaises(P.ErreurParametres) as e:
                P.analyser_ini(texte)
            self.assertEqual(e.exception.message, message)

    def test_cle_inconnue_avertissement_sans_erreur(self):
        lu = P.analyser_ini("[calcul]\nessai = 3\nqualite = rapide\n[calc]\nx = 1\n")
        self.assertEqual(lu.avertissements, [
            "[calcul] essai : clé inconnue, ignorée ; vouliez-vous dire essais ?",
            "[calc] : section inconnue, ignorée ; vouliez-vous dire [calcul] ?"])
        self.assertEqual(lu.cles_inconnues, ["[calcul] essai", "[calc]"])
        self.assertNotIn(("calcul", "essais"), lu.valeurs)
        self.assertEqual(lu.valeurs[("calcul", "qualite")], "rapide")

    def test_modele_relu_sans_avertissement_et_complet(self):
        lu = P.analyser_ini(P.MODELE_INI)
        self.assertEqual(lu.avertissements, [])
        self.assertEqual(lu.valeurs, {})                    # toutes les valeurs vides : aucune clé renseignée
        presentes, section = set(), None
        for ligne in P.MODELE_INI.splitlines():
            d = P._decouper(ligne)
            if d[0] == "section":
                section = d[1]
            elif d[0] == "cle":
                self.assertEqual(d[4], "", ligne)
                presentes.add((section, d[1]))
        self.assertEqual(presentes, {(s, k) for s, cles in P.SCHEMA.items() for k in cles})
        self.assertEqual(presentes, cles_table_5_4())
        for s, cles in P.SCHEMA.items():                   # défaut écrit dans le commentaire qui précède
            for k, c in cles.items():
                if c.defaut is not None and c.defaut != [] and s != "contraintes":
                    attendu = f"(défaut : {P.formater(s, k, c.defaut)})"
                    avant = P.MODELE_INI[:P.MODELE_INI.index(f"\n{k} =")].split("\n")
                    commentaire = []
                    while avant and avant[-1].startswith(";"):
                        commentaire.append(avant.pop())
                    self.assertIn(attendu, " ".join(commentaire), (s, k))

    def test_valeurs_de_la_table_5_4(self):
        """Défauts de §5.4 ; le niveau normal vaut 8 essais et 3 h (§2.6)."""
        r = P.Reglages()
        self.assertEqual((r.qualite, r.essais(), r.duree_max_min()), ("normal", 8, 180))
        self.assertEqual(r.processeurs(12), 11)
        self.assertEqual(r.processeurs(1), 1)
        self.assertEqual(r.valeur("calcul", "graine_depart"), 1)
        self.assertEqual(r.valeur("sortie", "garder_graines"), "meilleure")
        self.assertTrue(all(r.valeurs["pcbdoc"].values()))


def cles_table_5_4() -> set:
    """Clés de la table de §5.4, lues dans CONTRAT.md."""
    texte = _lire(CONTRAT)
    i = texte.index("### 5.4")
    fin = texte.index("### 5.5", i)
    cles, section = set(), None
    for ligne in texte[i:fin].splitlines():
        if not ligne.startswith("| ") or ligne.startswith("| Section") or ligne.startswith("|---"):
            continue
        cellules = [c.strip() for c in ligne.strip().strip("|").split("|")]
        if cellules[0]:
            section = cellules[0].strip("`[]")
        for k in re.findall(r"`([a-z_]+)`", cellules[1]):
            cles.add((section, k))
    return cles


# ------------------------------------------------------------------------------------------------ priorités (§5.4)

class TestPriorites(Dossier):

    def preparer(self):
        appdata = os.path.join(self.tmp, "Roaming")
        self.ecrire(os.path.join("Roaming", "PlaceurRouteur", "parametres.ini"),
                    "[calcul]\nqualite = rapide\nessais = 3\nprocesseurs = 2\n")
        carte = os.path.join(self.tmp, "cartes", "ma carte.PcbDoc")
        self.ecrire(os.path.join("cartes", "ma carte.PcbDoc"), "faux")
        self.ecrire(os.path.join("cartes", "ma carte.ini"), "[calcul]\nessais = 5\nduree_max_min = 30\n")
        explicite = self.ecrire("explicite.ini", "[carte]\nfichier = cartes/ma carte.PcbDoc\n"
                                                 "[calcul]\nessais = 7\nvalides_voulues = 1\n")
        return appdata, carte, explicite

    def test_mode_source_aucun_ini_implicite(self):
        appdata, carte, _ = self.preparer()
        with mock.patch.object(chemins, "dossier_connu", lambda nom: appdata if nom == "appdata" else None):
            self.assertFalse(chemins.est_fige())
            r = P.charger(carte=carte)
        self.assertEqual(r.fichiers_lus, [])
        self.assertEqual((r.qualite, r.essais(), r.valeur("calcul", "essais")), ("normal", 8, 0))

    def test_mode_fige_ordre_des_priorites(self):
        appdata, carte, explicite = self.preparer()
        with mock.patch.object(chemins, "dossier_connu", lambda nom: appdata if nom == "appdata" else None), \
                mock.patch.object(sys, "frozen", True, create=True):
            self.assertTrue(chemins.est_fige())
            r = P.charger(parametres=explicite, options={"calcul.essais": 9})
            r2 = P.charger(parametres=explicite)
            r3 = P.charger(parametres=os.path.join(self.tmp, "cartes", "ma carte.ini"), carte=carte)
        self.assertEqual(r.fichiers_lus, ["parametres.ini", "ma carte.ini", "explicite.ini"])
        self.assertEqual((r.valeur("calcul", "essais"), r.origine("calcul", "essais")), (9, "options"))
        self.assertEqual((r.qualite, r.origine("calcul", "qualite")), ("rapide", "parametres.ini"))
        self.assertEqual(r.valeur("calcul", "processeurs"), 2)
        self.assertEqual((r.valeur("calcul", "duree_max_min"), r.origine("calcul", "duree_max_min")),
                         (30, "ma carte.ini"))
        self.assertEqual(r.valeur("calcul", "valides_voulues"), 1)
        self.assertEqual(r2.valeur("calcul", "essais"), 7)              # --parametres > fichier de la carte
        self.assertEqual(r3.fichiers_lus, ["parametres.ini", "ma carte.ini"])   # lu une seule fois

    def test_options_et_fichier_introuvable(self):
        r = P.charger(options={"calcul.qualite": "Aperçu", "pcbdoc.produire": False, "calcul.essais": 2})
        self.assertEqual((r.qualite, r.essais(), r.duree_max_min()), ("apercu", 2, 15))
        self.assertFalse(r.valeur("pcbdoc", "produire"))
        with self.assertRaises(P.ErreurParametres) as e:
            P.charger(parametres=os.path.join(self.tmp, "absent.ini"))
        self.assertEqual(e.exception.message, "fichier de paramètres introuvable : absent.ini")
        with self.assertRaises(P.ErreurParametres) as e:
            P.charger(options={"calcul.essais": "huit"})
        self.assertEqual(e.exception.message, "[calcul] essais : 'huit' n'est pas un nombre entier")
        with self.assertRaises(P.ErreurParametres) as e:
            P.charger(parametres=self.tmp)
        self.assertEqual(e.exception.message,
                         f"fichier de paramètres attendu : {os.path.basename(self.tmp)} est un dossier")

    def test_fichier_de_projet_garde_les_preferences(self):
        """Un fichier plus fort ne remplace que les clés qu'il renseigne : le fichier de projet créé par
        « Enregistrer les paramètres » (depuis le modèle) et une valeur vide laissent les préférences."""
        appdata = os.path.join(self.tmp, "Roaming")
        prefs = os.path.join(appdata, "PlaceurRouteur", "parametres.ini")
        os.makedirs(os.path.dirname(prefs))
        P.enregistrer(prefs, {"calcul.processeurs": 4, "avance.fils_par_essai": 2, "pcbdoc.serigraphie": False,
                              "calcul.qualite": "rapide"})
        carte = self.ecrire(os.path.join("cartes", "ma_carte.PcbDoc"), "faux")
        with mock.patch.object(chemins, "dossier_connu", lambda nom: appdata if nom == "appdata" else None):
            avant = P.charger(carte=carte, implicites=True)
            P.enregistrer(P.ini_projet(carte), {"calcul.essais": 3})
            apres = P.charger(carte=carte, implicites=True)
            self.ecrire(os.path.join("cartes", "ma_carte.ini"), "[calcul]\nqualite =\nprocesseurs = ; vide\n")
            vide = P.charger(carte=carte, implicites=True)
        self.assertEqual(avant.fichiers_lus, ["parametres.ini"])
        self.assertEqual(apres.fichiers_lus, ["parametres.ini", "ma_carte.ini"])
        for r in (avant, apres, vide):
            self.assertEqual((r.valeur("calcul", "processeurs"), r.valeur("avance", "fils_par_essai"),
                              r.valeur("pcbdoc", "serigraphie"), r.qualite), (4, 2, False, "rapide"))
            self.assertEqual(r.origine("calcul", "processeurs"), "parametres.ini")
        self.assertEqual((apres.valeur("calcul", "essais"), apres.origine("calcul", "essais")), (3, "ma_carte.ini"))
        self.assertEqual(vide.fichiers_lus, ["parametres.ini", "ma_carte.ini"])
        self.assertEqual(vide.valeur("calcul", "essais"), 0)


# ------------------------------------------------------------------------------------------------ moteur (§2.6)

class TestParametresMoteur(Dossier):

    @classmethod
    def setUpClass(cls):
        cls.defauts = {q: P.defauts_moteur(q, COMMANDE_MOTEUR) for q in P.QUALITES}
        cls.annexe = annexe_c_corrigee()
        cls.table = table_niveaux()

    def test_normal_egal_annexe_c_corrigee(self):
        self.assertEqual(self.defauts["normal"], self.annexe)
        self.assertEqual(self.defauts["normal"]["conjointe"]["essais_par_piece_mobile"], 6.0)
        self.assertEqual(annexe_c()["conjointe"]["essais_par_piece_mobile"], 2.2293)   # valeur de la 2.1

    def test_niveaux_suivent_la_table_2_6(self):
        self.assertEqual(set(self.table), {"recuit.mouvements_max", "routage.iters_negociation", "boucle.tours",
                                           "conjointe.essais_par_piece_mobile", "conjointe.plafond_s",
                                           "campagne.duree_max_s"})
        for cle, valeurs in self.table.items():
            s, k = cle.split(".")
            self.assertEqual(valeurs["normal"], self.annexe[s][k], cle)
            for q in P.QUALITES:
                self.assertEqual(self.defauts[q][s][k], valeurs[q], (q, cle))
                self.assertIs(type(self.defauts[q][s][k]), type(self.annexe[s][k]), (q, cle))
        for q in P.QUALITES:                         # les autres clés ne changent pas d'un niveau à l'autre
            a, b = json.loads(json.dumps(self.defauts[q])), json.loads(json.dumps(self.annexe))
            for cle in self.table:
                s, k = cle.split(".")
                del a[s][k], b[s][k]
            self.assertEqual(a, b, q)
        self.assertEqual(self.defauts["rapide"]["boucle"]["tours"], 1)
        self.assertEqual(self.defauts["soigne"]["conjointe"]["essais_par_piece_mobile"], 6.37)

    def test_lignes_de_l_orchestrateur(self):
        texte = _lire(CONTRAT)
        bloc = texte[texte.index("**Niveaux de qualité**"):]
        essais = re.search(r"\| essais \(graines, orchestrateur\) \|([^\n]+)", bloc).group(1)
        durees = re.search(r"\| durée maximale du calcul \(orchestrateur\) \|([^\n]+)", bloc).group(1)
        essais = [int(c) for c in essais.strip(" |").split("|")]
        minutes = [int(n) * (60 if u == "h" else 1)
                   for n, u in re.findall(r"(\d+) (min|h)", durees)]
        self.assertEqual([P.NIVEAUX[q]["essais"] for q in P.QUALITES], essais)
        self.assertEqual([P.NIVEAUX[q]["duree_max_min"] for q in P.QUALITES], minutes)

    def test_priorites_defauts_fichier_avance_options(self):
        fichier = os.path.join(self.tmp, "moteur perso.json")
        chemins.ecrire_json(fichier, {
            "_commentaire": "ignoré", "format": "parametres_moteur/1",
            "boucle": {"tours": 2, "_note": "x"}, "recuit": {"mouvements_max": 5, "alpha": 0.95},
            "routage": {"espacement": {"prox": 2.0, "_note": "y"}},
            "finition": {"surfaces_puissance": {"roles": ["puissance"]}},
            "roles": {"commutation": {"couches": None, "paliers_mm": [1.0]}}, "conjointe": {"essais": 1500}})
        ini = self.ecrire("p.ini", f"[avance]\nparametres_moteur = {os.path.basename(fichier)}\nboucle = 3\n"
                                   "iters_det = 20\nduree_max_graine_min = 2,5\nconjointe_essais_par_piece = 1\n"
                                   "fils_par_essai = 1\n")
        r = P.charger(parametres=ini, options={"avance.iters_det": 33})
        params, origines = P.parametres_moteur(self.defauts["normal"], r, {"recuit.alpha": 0.9})
        self.assertEqual(params["recuit"]["mouvements_max"], 5)                 # fichier > défauts
        self.assertEqual(params["boucle"]["tours"], 3)                          # [avance] > fichier
        self.assertEqual(params["routage"]["iters_negociation"], 33)            # option > [avance]
        self.assertEqual(params["recuit"]["alpha"], 0.9)                        # option > fichier
        self.assertEqual(params["routage"]["espacement"], {"actif": True, "prox": 2.0, "coude": 0.3,
                                                           "allongement_max": 0.15})   # fusion profonde
        self.assertEqual(params["finition"]["surfaces_puissance"]["roles"], ["puissance"])   # tableau remplacé
        self.assertEqual(params["roles"]["commutation"]["paliers_mm"], [1.0])
        self.assertIsNone(params["roles"]["commutation"]["couches"])
        self.assertEqual(params["conjointe"]["essais"], 1500)
        self.assertEqual(params["campagne"]["duree_max_s"], 150)
        self.assertIs(type(params["campagne"]["duree_max_s"]), int)
        r.valeurs["avance"]["duree_max_graine_min"] = 0.71
        self.assertEqual(P.parametres_moteur(self.defauts["normal"], r)[0]["campagne"]["duree_max_s"], 42.6)
        self.assertEqual(params["conjointe"]["essais_par_piece_mobile"], 1.0)
        self.assertEqual(params["campagne"]["threads"], 1)
        self.assertNotIn("_commentaire", params)
        self.assertNotIn("_note", params["boucle"])
        self.assertTrue(P.cles_feuilles(params) <= P.cles_feuilles(self.annexe))
        self.assertEqual(origines["recuit.mouvements_max"], "moteur perso.json")
        self.assertEqual(origines["boucle.tours"], "[avance] boucle")
        self.assertEqual(origines["recuit.alpha"], "options")
        self.assertEqual(self.defauts["normal"], self.annexe)                    # défauts non modifiés

    def test_cles_inconnues_et_types(self):
        cas = {
            '{"routage": {"espacment": {"actif": false}}}': "routage.espacment : clé inconnue ; vouliez-vous dire "
                                                            "espacement ?",
            '{"boucl": {"tours": 1}}': "boucl : clé inconnue ; vouliez-vous dire boucle ?",
            '{"routage": {"espacement": 3}}': "routage.espacement : un objet est attendu",
            '{"campagne": {"chaines": "4"}}': "campagne.chaines : un nombre est attendu",
            '{"recuit": {"alpha": null}}': "recuit.alpha : null n'est pas permis ici",
            '{"format": "parametres_moteur/2"}': "format parametres_moteur/2 non pris en charge (attendu "
                                                 "parametres_moteur/1)",
            '[1, 2]': "moteur.json : un objet JSON est attendu",
            '{"a": ': "moteur.json : JSON illisible (ligne 1, colonne 7)",
        }
        fichier = os.path.join(self.tmp, "moteur.json")
        for texte, message in cas.items():
            with open(fichier, "w", encoding="utf-8") as f:
                f.write(texte)
            with self.subTest(texte=texte), self.assertRaises(P.ErreurParametres) as e:
                P.parametres_moteur(self.defauts["normal"], fichier=fichier)
            self.assertEqual(e.exception.message, message)
        with self.assertRaises(P.ErreurParametres):
            P.parametres_moteur(self.defauts["normal"], options_moteur={"campagne.fils": 1})
        params, _ = P.parametres_moteur(self.defauts["normal"], options_moteur={"routage.pas_grille_detail_mm": 0.05})
        self.assertEqual(params["routage"]["pas_grille_detail_mm"], 0.05)       # « auto » ou un nombre

    def test_cles_produites_incluses_dans_annexe_c(self):
        """Toutes les combinaisons de [avance] (64) × options (2) × niveaux (4)."""
        valeurs = {"conjointe_essais_par_piece": 0.5, "boucle": 2, "mouvements": 1000, "iters_det": 10,
                   "duree_max_graine_min": 1.5, "fils_par_essai": 2}
        reference = P.cles_feuilles(self.annexe)
        n = 0
        for q in P.QUALITES:
            for choix in itertools.product((False, True), repeat=len(valeurs)):
                r = P.Reglages()
                for (cle, v), oui in zip(valeurs.items(), choix):
                    r.valeurs["avance"][cle] = v if oui else None
                for options in (None, {"campagne.threads": 1, "campagne.duree_max_s": 60}):
                    params, _ = P.parametres_moteur(self.defauts[q], r, options)
                    self.assertTrue(P.cles_feuilles(params) <= reference)
                    self.assertEqual(P.cles_feuilles(params), reference)
                    n += 1
        self.assertEqual(n, 512)

    def test_moteur_absent_ou_en_erreur(self):
        with self.assertRaises(P.ErreurParametres) as e:
            P.defauts_moteur("normal", [os.path.join(self.tmp, "absent.exe")])
        self.assertTrue(e.exception.message.startswith(chemins.MESSAGE_MOTEUR_ABSENT))
        with self.assertRaises(P.ErreurParametres) as e:
            P.defauts_moteur("normal", FAUX_MOTEUR + ["--faux-inconnue"])
        self.assertIn("code 2", e.exception.message)


# ------------------------------------------------------------------------------------------------ [contraintes]

class TestContraintes(Dossier):

    def test_listes_ajoutees_scalaires_remplaces(self):
        contraintes = {"format": "contraintes/2", "unite": "mm", "fixes": ["J1"], "marge_bord_mm": 1.0,
                       "pas_fin_mm": 0.65}
        r = P.Reglages()
        r.appliquer(P.analyser_ini("[contraintes]\nfixes = U1, J1\ndessus_seulement = nature:connecteur\n"
                                   "marge_bord_mm = 1,5\npas_fin_mm = 0,65\nfixer_suggestions = non\n"
                                   "grille_placement_mm = 0,1\n").valeurs, "p.ini")
        res, avert = P.fusionner_contraintes(contraintes, r)
        self.assertEqual(res["fixes"], ["J1", "U1"])
        self.assertEqual(res["dessus_seulement"], ["nature:connecteur"])
        self.assertEqual((res["marge_bord_mm"], res["pas_fin_mm"], res["grille_placement_mm"]), (1.5, 0.65, 0.1))
        self.assertIs(res["fixer_suggestions"], False)
        self.assertEqual(avert, ["[contraintes] marge_bord_mm = 1,5 remplace la valeur 1.0 de contraintes.json"])
        self.assertEqual(contraintes["fixes"], ["J1"])                          # entrée non modifiée
        self.assertNotIn("liberer", res)
        vide, avert = P.fusionner_contraintes(None, P.Reglages())
        self.assertEqual((vide, avert), ({"format": "contraintes/2"}, []))


# ------------------------------------------------------------------------------------------------ parametres_utilises.ini

class TestParametresUtilises(Dossier):

    def test_noms_de_fichiers_seulement(self):
        ini = self.ecrire("projet.ini", f"[carte]\nfichier = cartes/ma carte.PcbDoc\ncontraintes = c/ma c.json\n"
                                        f"[sortie]\ndossier = {self.tmp}\n[avance]\nboucle = 2\n")
        r = P.charger(parametres=ini)
        texte = P.texte_parametres_utilises(r, {"calcul.essais": 8, "calcul.duree_max_min": 180})
        self.assertNotIn(self.tmp, texte)
        self.assertNotIn(os.path.dirname(self.tmp), texte)
        for attendu in ("fichier = ma carte.PcbDoc", "contraintes = ma c.json", "dossier = .", "essais = 8",
                        "duree_max_min = 180", "boucle = 2", "Fichiers de paramètres lus : projet.ini."):
            self.assertIn(attendu, texte)
        relu = P.analyser_ini(texte, "parametres_utilises.ini", self.tmp)
        self.assertEqual(relu.avertissements, [])
        chemin = os.path.join(self.tmp, "parametres_utilises.ini")
        P.ecrire_parametres_utilises(chemin, r)
        brut = octets(chemin)
        self.assertTrue(brut.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(brut.count(b"\n"), brut.count(b"\r\n"))


def _commande_vrai_moteur():
    """Commande du vrai moteur s'il est construit (jalon A1 fusionné), sans jamais lancer cargo ; sinon None."""
    try:
        return chemins.commande_moteur("placeur_routage", construire=False)
    except (chemins.MoteurAbsent, chemins.ErreurConstruction):
        return None


@unittest.skipIf(_commande_vrai_moteur() is None,
                 "vrai moteur non construit (cargo build --release --offline dans placeur_routage/)")
class TestParametresVraiMoteur(TestParametresMoteur):
    """Les mêmes vérifications (D4), rejouées sur le vrai moteur du lot A (jalon A1)."""

    @classmethod
    def setUpClass(cls):
        commande = _commande_vrai_moteur()
        cls.defauts = {q: P.defauts_moteur(q, commande) for q in P.QUALITES}
        cls.annexe = annexe_c_corrigee()
        cls.table = table_niveaux()


# ------------------------------------------------------------------------------------------------ durées (§5.3)

class TestDurees(unittest.TestCase):

    def test_coefficients(self):
        defauts = {q: P.defauts_moteur(q, COMMANDE_MOTEUR) for q in P.QUALITES}
        metroid = {"format": "estimation/1", "cellules_detail": P.REFERENCE_DUREE["cellules_detail"],
                   "couches_routage": 3, "pieces_mobiles": 314}
        d = {q: P.duree_graine_prevue(metroid, defauts[q]) for q in P.QUALITES}
        self.assertAlmostEqual(d["normal"]["total_s"], 2722, delta=0.02 * 2722)   # moyenne de run_4
        self.assertLess(d["apercu"]["total_s"], d["rapide"]["total_s"])
        self.assertLess(d["rapide"]["total_s"], d["normal"]["total_s"])
        self.assertLess(d["normal"]["total_s"], d["soigne"]["total_s"])
        for q in P.QUALITES:
            self.assertLessEqual(d[q]["total_s"], defauts[q]["campagne"]["duree_max_s"])
        self.assertEqual(P.plafond_graine_s(defauts["normal"]), 7200)
        self.assertEqual(P.duree_calcul_prevue_s(100, 8, 3, 1000), 300)
        self.assertEqual(P.duree_calcul_prevue_s(100, 8, 3, 150), 100)
        self.assertEqual(P.duree_calcul_prevue_s(100, 0, 3, 150), 0)
        petite = dict(metroid, cellules_detail=metroid["cellules_detail"] // 4, pieces_mobiles=20)
        self.assertLess(P.duree_graine_prevue(petite, defauts["normal"])["total_s"], d["normal"]["total_s"] / 3)
        for champ in ("cellules_detail", "couches_routage", "pieces_mobiles"):  # jamais la taille de l'étalon
            for v in (None, 0 if champ != "pieces_mobiles" else -1, "3", True):
                incomplete = {k: x for k, x in metroid.items() if k != champ}
                if v is not None:
                    incomplete[champ] = v
                with self.subTest(champ=champ, v=v), self.assertRaises(P.EstimationInvalide) as e:
                    P.duree_graine_prevue(incomplete, defauts["normal"])
                self.assertIn(champ, str(e.exception))


class TestSource(unittest.TestCase):

    def test_aucune_variable_d_environnement(self):
        with open(os.path.join(ICI, "parametres.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertIsNone(re.search(r"os\.environ|getenv", source))


if __name__ == "__main__":
    unittest.main()
