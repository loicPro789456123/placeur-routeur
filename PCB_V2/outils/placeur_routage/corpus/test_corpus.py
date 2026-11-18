"""Tests du corpus (lot E) : déterminisme des générateurs, corpus suivi égal à leur sortie, formats, attendus,
contrôles syntaxiques des cartes KiCad.

    cd placeur_routage && python -m unittest discover -s corpus -p "test_*.py" -v
"""

import contextlib
import io
import json
import math
import os
import re
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
sys.path.insert(1, os.path.dirname(ICI))

import formats  # noqa: E402
import generer_cartes  # noqa: E402
import generer_cas  # noqa: E402

MIL = 0.0254
FORMATS_CAS = {"donnees_placeur.json": "donnees_placeur/2", "placements_routage.json": "placements/2",
               "routage_detaille.json": "routage/2", "attendu_strict.json": "verdict/2",
               "attendu_altium.json": "verdict/2", "attendu_mesures.json": "mesures/2"}
THEMES_E1 = ("contact:piste-pastille", "contact:piste-piste", "contact:piste-via", "contact:via-pastille",
             "contact:via-via", "contact:zone-via", "zone_plan", "audit:D3.2", "violation:isolement",
             "violation:largeur", "violation:antenne")
CONTACTS = THEMES_E1[:5]
# Lignes de la table de §4.2 → thèmes de cas.json qui les exercent (étape 5).
LIGNES_42 = {
    1: {"contact:piste-piste"},
    2: {"contact:piste-pastille", "contact:piste-region", "contact:piste-via-empreinte", "contact:piste-cuivre-fixe"},
    3: {"contact:piste-via"},
    4: {"contact:via-pastille", "contact:via-region"},
    5: {"contact:via-via"},
    6: {"contact:surface"},
    7: {"contact:preexistants"},
    8: {"contact:zone-via"},
    9: {"contact:zone-cms"},
}
LIGNES_STRICT_DIFFERENT = (1, 2, 3, 4, 5, 8, 9)
THEMES_E5 = ("contact:piste-region", "contact:piste-via-empreinte", "contact:piste-cuivre-fixe", "contact:via-region",
             "contact:surface", "contact:preexistants", "contact:zone-cms", "objet:piece_virtuelle",
             "objet:region_liaison", "objet:cuivre_fixe", "objet:surface", "zone:ilot_autre_reseau",
             "zone:plan_partiel", "zone:bande_retrait", "zone:bord", "zone:plan_externe",
             "geometrie:pastille_tournee", "geometrie:trou_decale")
THEMES_E6 = ("violation:garde_bord", "violation:garde_decoupe", "violation:garde_trou", "violation:zone_interdite",
             "violation:dimension_via", "violation:via_sous_cms", "violation:trou_trou",
             "violation:couche_non_permise", "violation:reseau_inconnu", "derogation:via_dans_pastille",
             "geometrie:contour_polygonal", "objet:trou", "regles:matrice", "arrondi:pastille",
             "audit:amorce_coupee", "avertissement:sous_piece", "zone_interdite:empreinte",
             "zone_interdite:elargie")
# Étape 7 : chaque clé de §4.5 et les cas particuliers du placement (face opposée, pose d'entrée, v1, sans grille).
THEMES_E7 = tuple("placement:" + k for k in generer_cas.GENRES_PLACEMENT) + (
    "placement:valide", "placement:face_opposee", "placement:pose_entree", "placement:v1", "placement:sans_grille")


def arbre(dossier):
    """{chemin relatif (séparateur /) : octets} de tous les fichiers sous ``dossier``."""
    t = {}
    for racine, _, fichiers in os.walk(dossier):
        for f in fichiers:
            p = os.path.join(racine, f)
            with open(p, "rb") as fh:
                t[os.path.relpath(p, dossier).replace(os.sep, "/")] = fh.read()
    return t


def lire(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class TestCas(unittest.TestCase):
    suivi = os.path.join(ICI, "validite")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.c1, cls.c2 = os.path.join(cls.tmp.name, "c1"), os.path.join(cls.tmp.name, "c2")
        generer_cas.ecrire(cls.c1)
        generer_cas.ecrire(cls.c2)
        cls.noms = sorted(os.listdir(cls.c1))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_deterministe_et_suivi(self):
        a1 = arbre(self.c1)
        self.assertEqual(a1, arbre(self.c2), "deux exécutions différentes")
        self.assertEqual(sorted(a1), sorted(arbre(self.suivi)), "fichiers du corpus suivi ≠ sortie du générateur")
        self.assertEqual(a1, arbre(self.suivi), "corpus suivi ≠ sortie du générateur (régénérer)")
        self.assertTrue(all(b"\r" not in v for v in a1.values()), "fin de ligne CRLF")

    def test_au_moins_60_cas_et_fichiers_acceptes(self):
        self.assertGreaterEqual(len(self.noms), 60)
        for nom in self.noms:
            for f, fmt in FORMATS_CAS.items():
                with self.subTest(cas=nom, fichier=f):
                    d = formats.charger(os.path.join(self.c1, nom, f), fmt)
                    if isinstance(d, dict):   # placement v1 : liste nue, acceptée par placements/2
                        self.assertEqual(d["format"], fmt)
            with contextlib.redirect_stdout(io.StringIO()) as sortie:
                code = formats.main(["valider", os.path.join(self.c1, nom, "attendu_strict.json")])
            self.assertEqual(code, 0, sortie.getvalue())

    def test_cas_json_et_couverture(self):
        couverts, differents = set(), set()
        for nom in self.noms:
            c = lire(os.path.join(self.c1, nom, "cas.json"))
            self.assertEqual(c["format"], "cas_validite/1")
            self.assertEqual(c["nom"], nom)
            self.assertTrue(re.fullmatch(r"\d{3}_[a-z0-9_]+", nom), nom)
            self.assertTrue(set(c["themes"]) <= set(generer_cas.THEMES), c["themes"])
            self.assertEqual(sorted(c["fichiers"]), sorted(["donnees", "placement", "routage", "attendu_strict",
                                                            "attendu_altium", "attendu_mesures", "couture"]))
            self.assertIsInstance(c["grille"], bool)
            self.assertEqual(c["couture"], c["fichiers"]["couture"] is not None)
            s = lire(os.path.join(self.c1, nom, "attendu_strict.json"))
            a = lire(os.path.join(self.c1, nom, "attendu_altium.json"))
            diff = (s["valide"], s["liaisons_ouvertes"], s["violations_par_type"]) != \
                   (a["valide"], a["liaisons_ouvertes"], a["violations_par_type"])
            self.assertEqual(c["modes_differents"], diff, nom)
            self.assertEqual(diff, "modes_differents" in c["themes"], nom)
            couverts |= set(c["themes"])
            if diff:
                differents |= set(c["themes"])
            # Attendu de placement : champ « placement » (controle_placement/1) de attendu_strict.json seul.
            self.assertNotIn("placement", a, nom)
            pl = s["placement"]
            self.assertEqual(pl["format"], "controle_placement/1", nom)
            self.assertEqual(sorted(pl["violations_par_type"]), sorted(generer_cas.GENRES_PLACEMENT))
            self.assertEqual(sorted(pl["violations_par_type"]), sorted(formats.GENRES_PLACEMENT))
            self.assertEqual(pl["valide"], not pl["violations"], nom)
            self.assertEqual(sum(pl["violations_par_type"].values()), len(pl["violations"]), nom)
        manquants = (set(THEMES_E1) | set(THEMES_E5)) - couverts
        # Chaque ligne de §4.2 a un attendu dans chaque mode (chaque cas a les deux attendus) ; lignes 1 à 5, 8 et 9 :
        # au moins un cas où strict ≠ altium.
        for ligne, themes in LIGNES_42.items():
            self.assertTrue(themes & couverts, f"ligne {ligne} de §4.2 sans cas")
            if ligne in LIGNES_STRICT_DIFFERENT:
                self.assertTrue(themes & differents, f"ligne {ligne} de §4.2 sans cas strict ≠ altium")
        self.assertFalse(manquants, f"thèmes de E1 non couverts : {sorted(manquants)}")
        sans_ecart = set(CONTACTS) - differents
        self.assertFalse(sans_ecart, f"contacts sans cas strict ≠ altium : {sorted(sans_ecart)}")

    def test_couverture_des_violations(self):
        """Étape 6 : chaque clé de §4.4 (et la dérogation) non nulle dans au moins un attendu ; chaque thème de
        l'étape 6 couvert."""
        vues = set()
        for nom in self.noms:
            for f in ("attendu_strict.json", "attendu_altium.json"):
                v = lire(os.path.join(self.c1, nom, f))
                vues |= {k for k, n in v["violations_par_type"].items() if n}
                vues |= {"derogation:" + k for k, n in v["derogations"].items() if n}
                vues |= {"avertissement:" + k for k, n in v["avertissements"].items() if n}
        attendues = set(generer_cas.GENRES_ROUTAGE) | {"derogation:via_dans_pastille_meme_reseau",
                                                       "avertissement:pistes_sous_pieces_sans_piste_dessous"}
        self.assertFalse(attendues - vues, f"clés jamais non nulles : {sorted(attendues - vues)}")
        themes = set()
        for nom in self.noms:
            themes |= set(lire(os.path.join(self.c1, nom, "cas.json"))["themes"])
        self.assertFalse(set(THEMES_E6) - themes, f"thèmes de l'étape 6 non couverts : {sorted(set(THEMES_E6) - themes)}")

    def test_couverture_du_placement(self):
        """Étape 7 : chaque clé de §4.5 non nulle dans au moins un attendu ; thèmes de l'étape 7 couverts ; un cas
        sans grille et un placement v1 (liste nue)."""
        vues, themes, sans_grille, v1 = set(), set(), False, False
        for nom in self.noms:
            pl = lire(os.path.join(self.c1, nom, "attendu_strict.json"))["placement"]
            vues |= {k for k, n in pl["violations_par_type"].items() if n}
            c = lire(os.path.join(self.c1, nom, "cas.json"))
            themes |= set(c["themes"])
            sans_grille |= not c["grille"]
            v1 |= isinstance(lire(os.path.join(self.c1, nom, "placements_routage.json")), list)
        self.assertFalse(set(generer_cas.GENRES_PLACEMENT) - vues,
                         f"clés de §4.5 jamais non nulles : {sorted(set(generer_cas.GENRES_PLACEMENT) - vues)}")
        self.assertFalse(set(THEMES_E7) - themes, f"thèmes de l'étape 7 non couverts : {sorted(set(THEMES_E7) - themes)}")
        self.assertTrue(sans_grille and v1)

    def test_attendus_coherents(self):
        """Contrôles indépendants des valeurs écrites à la main : longueur et vias recomptés sur le routage, mesures
        du verdict = termes de mesures/2, strict plus exigeant qu'altium (§4.2), mode et outil."""
        for nom in self.noms:
            with self.subTest(cas=nom):
                r = lire(os.path.join(self.c1, nom, "routage_detaille.json"))
                s = lire(os.path.join(self.c1, nom, "attendu_strict.json"))
                a = lire(os.path.join(self.c1, nom, "attendu_altium.json"))
                m = lire(os.path.join(self.c1, nom, "attendu_mesures.json"))
                longueur = sum(math.hypot(p["x2"] - p["x1"], p["y2"] - p["y1"]) for p in r["pistes"]) * MIL
                self.assertAlmostEqual(m["termes"]["longueur_mm"], longueur, places=9)
                self.assertEqual(m["termes"]["vias"], len(r["vias"]))
                for v, mode in ((s, "strict"), (a, "altium")):
                    self.assertEqual(v["mode"], mode)
                    self.assertAlmostEqual(v["mesures"]["longueur_pistes_mm"], longueur, places=9)
                    self.assertEqual(v["mesures"]["vias"], len(r["vias"]))
                    self.assertEqual(v["mesures"]["coudes"], m["termes"]["coudes"])
                    self.assertEqual(v["mesures"]["ecart_moyen_mm"], m["termes"]["ecart_moyen_mm"])
                    self.assertEqual(v["mesures"]["ecart_min_mm"] is None, v["mesures"]["ecart_moyen_mm"] == 0)
                self.assertGreaterEqual(s["liaisons_ouvertes"], a["liaisons_ouvertes"])
                if s["valide"]:
                    self.assertTrue(a["valide"], "valide en strict ⇒ valide en altium")
                self.assertEqual(s["mesures"], a["mesures"])
                for d in s["detail_liaisons_ouvertes"] + a["detail_liaisons_ouvertes"]:
                    self.assertEqual(d["nature"] == "ilot", not d["pastilles"])


def jetons(texte):
    return re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', texte)


def lire_sexpr(texte):
    """Arbre de listes d'un texte S-expression ; ValueError si les parenthèses sont déséquilibrées."""
    pile, profondeur = [[]], 0
    for j in jetons(texte):
        if j == "(":
            pile.append([])
            profondeur += 1
        elif j == ")":
            profondeur -= 1
            if profondeur < 0:
                raise ValueError("parenthèse fermante en trop")
            e = pile.pop()
            pile[-1].append(e)
        else:
            pile[-1].append(j[1:-1] if j.startswith('"') else j)
    if profondeur or len(pile[0]) != 1:
        raise ValueError("parenthèses déséquilibrées ou plusieurs formes")
    return pile[0][0]


def enfants(e, nom):
    return [x for x in e[1:] if isinstance(x, list) and x and x[0] == nom]


def couche_contrat(k):
    return {"F.Cu": "TopLayer", "B.Cu": "BottomLayer"}.get(k) or "MidLayer" + re.fullmatch(r"In(\d+)\.Cu", k)[1]


class TestCartes(unittest.TestCase):
    suivi = os.path.join(ICI, "cartes")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.k1, cls.k2 = os.path.join(cls.tmp.name, "k1"), os.path.join(cls.tmp.name, "k2")
        generer_cartes.ecrire(cls.k1)
        generer_cartes.ecrire(cls.k2)
        cls.noms = sorted(os.listdir(cls.k1))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_deterministe_et_suivi(self):
        a1 = arbre(self.k1)
        self.assertEqual(a1, arbre(self.k2), "deux exécutions différentes")
        self.assertEqual(sorted(a1), sorted(arbre(self.suivi)), "fichiers du corpus suivi ≠ sortie du générateur")
        self.assertEqual(a1, arbre(self.suivi), "corpus suivi ≠ sortie du générateur (régénérer)")
        self.assertTrue(all(b"\r" not in v for v in a1.values()), "fin de ligne CRLF")

    def test_quatre_cartes(self):
        self.assertEqual(self.noms, ["deux_convertisseurs", "deux_couches", "quatre_couches", "sans_classes"])

    def test_syntaxe_kicad_et_attendus(self):
        for nom in self.noms:
            with self.subTest(carte=nom):
                rep = os.path.join(self.k1, nom)
                self.assertEqual(sorted(os.listdir(rep)), sorted([nom + ".kicad_pcb", nom + ".kicad_pro",
                                                                  "contraintes.json", "attendu_deduction.json"]))
                with open(os.path.join(rep, nom + ".kicad_pcb"), encoding="utf-8") as f:
                    B = lire_sexpr(f.read())
                self.assertEqual(B[0], "kicad_pcb")
                self.assertGreaterEqual(int(enfants(B, "version")[0][1]), 20211014)
                declares = {e[2] for e in enfants(B, "net") if e[2]}
                self.assertEqual([e[1] for e in enfants(B, "net")], [str(i) for i in range(len(declares) + 1)])
                refs, reseaux_pastilles = [], set()
                for fp in enfants(B, "footprint"):
                    refs += [p[2] for p in enfants(fp, "property") if p[1] == "Reference"]
                    for pad in enfants(fp, "pad"):
                        for net in enfants(pad, "net"):
                            self.assertIn(net[2], declares)
                            self.assertEqual(enfants(B, "net")[int(net[1])][2], net[2], "code et nom du réseau")
                            reseaux_pastilles.add(net[2])
                for z in enfants(B, "zone"):
                    self.assertIn(enfants(z, "net_name")[0][1], declares)
                self.assertEqual(len(refs), len(set(refs)), "références en double")
                self.assertEqual(len(refs), len(enfants(B, "footprint")), "empreinte sans référence")
                self.assertTrue(declares <= reseaux_pastilles | {enfants(z, "net_name")[0][1]
                                                                  for z in enfants(B, "zone")})
                cuivre = [couche_contrat(e[1]) for e in enfants(B, "layers")[0][1:] if e[1].endswith(".Cu")]
                self.assertTrue(any(e for e in B[1:] if isinstance(e, list) and e[0] == "gr_rect"
                                    and enfants(e, "layer")[0][1] == "Edge.Cuts"))
                pro = lire(os.path.join(rep, nom + ".kicad_pro"))
                self.assertIn("Default", [c["name"] for c in pro["net_settings"]["classes"]])
                contraintes, avert = formats.lire_contraintes(os.path.join(rep, "contraintes.json"))
                self.assertEqual(contraintes["format"], "contraintes/2")
                self.assertEqual(avert, [])
                att = lire(os.path.join(rep, "attendu_deduction.json"))
                self.assertEqual(att["carte"], nom)
                erreurs = generer_cartes.valider_attendu_deduction(att, set(refs), declares, cuivre)
                self.assertEqual(erreurs, [])

    def test_validateur_attendu_refuse(self):
        rep = os.path.join(self.k1, "deux_convertisseurs")
        att = lire(os.path.join(rep, "attendu_deduction.json"))
        bon = generer_cartes.valider_attendu_deduction
        pieces = {p[0] for c in generer_cartes.CARTES if c["nom"] == "deux_convertisseurs" for p in c["pieces"]}
        reseaux = {r for c in generer_cartes.CARTES if c["nom"] == "deux_convertisseurs" for p in c["pieces"]
                   for r in p[7].values()}
        couches = ["TopLayer", "BottomLayer"]
        self.assertEqual(bon(att, pieces, reseaux, couches), [])
        faux = json.loads(json.dumps(att))
        faux["convertisseurs"][0]["haut"] = "Q9"
        self.assertIn("convertisseurs[0].haut : pièce 'Q9' absente de la carte", bon(faux, pieces, reseaux, couches))
        faux = json.loads(json.dumps(att))
        faux["roles"]["signal"].remove("FB")
        self.assertTrue(bon(faux, pieces, reseaux, couches))
        faux = json.loads(json.dumps(att))
        faux["couches"][0]["nom"] = "MidLayer1"
        self.assertTrue(bon(faux, pieces, reseaux, couches))
        faux = json.loads(json.dumps(att))
        del faux["groupes"]
        self.assertEqual(bon(faux, pieces, reseaux, couches), ["groupes : champ manquant"])


    def test_chaine_au_moins_une_graine_valide(self):
        # §7.6 (job synthetiques, --essais 2) et §7.7 (1 valide sur 10 en rapide) : minimum ≥ 1 sur chaque carte chaîne.
        chaines = []
        for nom in self.noms:
            att = lire(os.path.join(self.k1, nom, "attendu_deduction.json"))
            if att["famille"] == "chaine":
                chaines.append(nom)
                ch = att["chaine"]
                self.assertEqual((ch["niveau"], ch["graines"]), ("rapide", 10), nom)
                self.assertGreaterEqual(ch["graines_valides_min"], 1, nom)
        self.assertEqual(chaines, ["deux_couches", "quatre_couches"])
        att = lire(os.path.join(self.k1, "deux_couches", "attendu_deduction.json"))
        pieces, reseaux, couches = set(), set(), ["TopLayer", "BottomLayer"]
        for k, v in (("graines_valides_min", 0), ("graines", 0), ("graines", True)):
            faux = json.loads(json.dumps(att))
            faux["chaine"][k] = v
            self.assertTrue([e for e in generer_cartes.valider_attendu_deduction(faux, pieces, reseaux, couches)
                             if e.startswith("chaine")], (k, v))
        faux = json.loads(json.dumps(att))
        del faux["chaine"]["graines"]
        self.assertTrue([e for e in generer_cartes.valider_attendu_deduction(faux, pieces, reseaux, couches)
                         if e.startswith("chaine")])


class TestLigneDeCommande(unittest.TestCase):
    def test_sortie_fichier_existant_code_2_sans_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            fichier = os.path.join(tmp, "f.txt")
            with open(fichier, "w", encoding="utf-8") as f:
                f.write("x")
            for module in (generer_cas, generer_cartes):
                for sortie in (fichier, os.path.join(fichier, "sous")):
                    with self.subTest(module=module.__name__, sortie=os.path.basename(sortie)):
                        err = io.StringIO()
                        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                            code = module.main(["--sortie", sortie])
                        self.assertEqual(code, 2)
                        self.assertTrue(err.getvalue().startswith("erreur : "), err.getvalue())
                        self.assertNotIn(tmp, err.getvalue())
                        self.assertNotIn("Traceback", err.getvalue())


class TestDepot(unittest.TestCase):
    def test_gitattributes_et_contenu(self):
        with open(os.path.join(ICI, ".gitattributes"), encoding="utf-8") as f:
            self.assertIn("* text eol=lf", f.read().splitlines())
        for racine, dossiers, fichiers in os.walk(ICI):
            dossiers[:] = [d for d in dossiers if d not in ("target", "__pycache__")]
            self.assertFalse([d for d in dossiers if "essais" in d.lower()], racine)
            self.assertFalse([f for f in fichiers if f.lower().endswith(".pcbdoc")], racine)


if __name__ == "__main__":
    unittest.main()
