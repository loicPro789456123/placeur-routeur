"""Fichiers d'essai de l'annexe E (étape BF4) : fabriquer_essais.py sur des cartes synthétiques (public) et sur la
carte source du corpus (local, --cartes-locales DOSSIER) ; verdicts de --verifier."""
import contextlib
import io
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
import fabrique  # noqa: E402
from altium_fichier import PcbDoc  # noqa: E402

sys.path.insert(0, commun.RACINE)
import fabriquer_essais  # noqa: E402
import verifier_cartes  # noqa: E402

TOUTES = [e[0] for e in fabriquer_essais.EXPERIENCES]


def appeler(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fabriquer_essais.main(argv)
    return code, out.getvalue(), err.getvalue()


def lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def contenus(dossier):
    out = {}
    for n in sorted(os.listdir(dossier)):
        with open(os.path.join(dossier, n), "rb") as f:
            out[n] = f.read()
    return out


def valeur_conforme(m):
    """Valeur relevée qui satisfait la mesure `m` (attendu, ou premier libellé accepté)."""
    return m["acceptes"][0] if m["type"] in ("texte", "prefixe") else m["attendu"]


class TestFabricationSynthetique(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = commun.dossier_temporaire()
        cls.d = cls._ctx.__enter__()
        cls.base = fabrique.fabriquer(cls.d, nom="base.PcbDoc", essais=True)
        cls.sortie = os.path.join(cls.d, "essais")
        cls.code, cls.out, cls.err = appeler(["--base", cls.base, "--sortie", cls.sortie])
        cls.att = lire(os.path.join(cls.sortie, "attendus.json"))
        cls.exp = {e["id"]: e for e in cls.att["experiences"]}

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def test_code_et_fichiers(self):
        self.assertEqual(self.code, 0, self.out + self.err)
        attendus = {e[1] for e in fabriquer_essais.EXPERIENCES} | {"attendus.json", "resultats_a_remplir.json"}
        self.assertEqual(set(os.listdir(self.sortie)), attendus)
        self.assertEqual([e["statut"] for e in self.att["experiences"]], ["fabriquee"] * len(TOUTES))
        self.assertNotIn("NON CONFORME", self.out)
        res = lire(os.path.join(self.sortie, "resultats_a_remplir.json"))
        self.assertEqual(list(res["experiences"]), TOUTES)
        for e in self.att["experiences"]:
            self.assertEqual(res["experiences"][e["id"]], {"faite": False, "remarques": "",
                                                           "valeurs": {m["cle"]: None for m in e["mesures"]}})
            self.assertTrue(e["mesures"] and e["convention"] and e["objet"])

    def test_sans_chemin(self):
        textes = [self.out, self.err]
        for n in ("attendus.json", "resultats_a_remplir.json"):
            with open(os.path.join(self.sortie, n), encoding="utf-8") as f:
                textes.append(f.read())
        for t in textes:
            self.assertNotIn(self.d, t)
            self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", t))
        self.assertEqual(self.att["base"]["nom"], "base.PcbDoc")

    def test_relecture_olefile_et_flux_hors_stockages(self):
        """Chaque fichier : olefile relit tous les flux, PcbDoc les relit aux mêmes octets, et tout flux hors des
        stockages déclarés est identique à la base."""
        base = verifier_cartes.flux_olefile(self.base)
        for e in self.att["experiences"]:
            with self.subTest(e["id"]):
                chemin = os.path.join(self.sortie, e["fichier"])
                sortie = verifier_cartes.flux_olefile(chemin)
                doc = PcbDoc.ouvrir(chemin)
                for c, o in sortie.items():
                    self.assertEqual(doc.octets_flux(c), o, c)
                modifies = {c.split("/")[0] for c in set(base) | set(sortie) if base.get(c) != sortie.get(c)}
                autorises = set(e["stockages_modifies"])
                self.assertTrue(modifies, "aucune modification")
                self.assertFalse({s for s in modifies if s not in autorises and not re.match(r".*Violation", s)},
                                 modifies)

    def test_modifications_prevues(self):
        doc = lambda i: PcbDoc.ouvrir(os.path.join(self.sortie, self.exp[i]["fichier"]))  # noqa: E731
        p = doc("E1").pastilles()[self.exp["E1"]["objet"]["index_pads6"]]
        f = p["formes"]["TOP"]
        self.assertEqual((f["forme"], f["pourcentage"]), ("rect_arrondi", 50))
        self.assertAlmostEqual(f["rayon_coin"] * 0.0254, 0.25, places=4)
        p = doc("E2").pastilles()[self.exp["E2"]["objet"]["index_pads6"]]
        self.assertEqual(p["formes"]["TOP"]["forme"], "octogone")
        self.assertAlmostEqual(p["formes"]["TOP"]["taille"][1] * 0.0254, 1.6, places=4)
        c = doc("E3").composants()[self.exp["E3"]["objet"]["index_components6"]]
        self.assertEqual((c["face"], c["rotation"]), ("BOTTOM", 30.0))
        r = doc("E4").regions()[-5:]
        self.assertEqual([x["restrictions_keepout"] for x in r], [0x01, 0x02, 0x04, 0x08, 0x10])
        self.assertTrue(all(x["keepout"] and x["couche"] == "KEEPOUT" for x in r))
        self.assertEqual([c["genre_altium"] for c in doc("E6").composants()], list(range(7)))
        vias = doc("E7").vias()[-4:]
        self.assertEqual([v["reseau"] for v in vias], ["VCC", "VCC", "GND", "GND"])
        c8 = doc("E8").contour_carte()
        self.assertEqual([s["type"] for s in c8], [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0])
        self.assertEqual((c8[1]["sa"], c8[1]["ea"]), (0.0, 180.0))
        p = doc("E9").pastilles()[self.exp["E9"]["objet"]["index_pads6"]]
        self.assertEqual((p["rotation"], p["percage"]["forme"], p["percage"]["rotation"]), (30.0, "oblong", 90.0))
        angle = {m["cle"]: m["attendu"] for m in self.exp["E9"]["mesures"]}["angle_axe_trou_deg"]
        self.assertEqual(angle, 120.0)  # convention : rotation de la pastille + rotation du trou
        d = doc("T1")
        self.assertEqual(len([v for v in d.vias() if v["composant"] == 0xFFFF]), 1)
        self.assertEqual(self.exp["T1"]["objet"]["supprimees"], {"Tracks6": 1, "Vias6": 2})

    def test_deux_executions_identiques(self):
        autre = os.path.join(self.d, "essais_2")
        code, out, err = appeler(["--base", self.base, "--sortie", autre])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(contenus(autre), contenus(self.sortie))

    def test_base_non_modifiee(self):
        with open(self.base, "rb") as f:
            import hashlib
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), self.att["base"]["sha256"])

    def test_verdicts(self):
        """Une convention confirmée (E1), une contredite (E2), les autres non faites."""
        res = lire(os.path.join(self.sortie, "resultats_a_remplir.json"))
        for i in ("E1", "E2"):
            res["experiences"][i]["faite"] = True
            res["experiences"][i]["valeurs"] = {m["cle"]: valeur_conforme(m) for m in self.exp[i]["mesures"]}
        res["experiences"]["E2"]["valeurs"]["chanfrein_mm"] = 0.4
        res["experiences"]["E3"]["faite"] = True  # faite, mais valeurs manquantes : non faite
        chemin = os.path.join(self.d, "resultats.json")
        verdicts = os.path.join(self.d, "verdicts.json")
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(res, f)
        code, out, err = appeler(["--verifier", chemin, "--attendus", os.path.join(self.sortie, "attendus.json"),
                                  "--json", verdicts])
        self.assertEqual(code, 1, out + err)
        v = {x["id"]: x for x in lire(verdicts)["verdicts"]}
        self.assertEqual(v["E1"]["verdict"], "confirmée")
        self.assertEqual(v["E2"]["verdict"], "contredite")
        self.assertIn("chanfrein_mm", v["E2"]["ecarts"][0])
        self.assertTrue(all(v[i]["verdict"] == "non faite" for i in TOUTES if i not in ("E1", "E2")))
        self.assertIn("valeurs manquantes", v["E3"]["motif"])
        self.assertIn("avenant", out)
        self.assertEqual(lire(verdicts)["resume"], {"confirmée": 1, "contredite": 1, "non faite": len(TOUTES) - 2})
        # toutes confirmées (tolérances, angles modulo, libellés sans casse ni accents) : code 0
        for e in self.att["experiences"]:
            r = res["experiences"][e["id"]]
            r["faite"] = True
            r["valeurs"] = {m["cle"]: valeur_conforme(m) for m in e["mesures"]}
        r = res["experiences"]
        r["E9"]["valeurs"]["angle_axe_trou_deg"] = 120.0 + 180.0 + 1.5
        r["E3"]["valeurs"]["piece_rotation_deg"] = 30.0 - 360.0
        r["E6"]["valeurs"]["piece5_type"] = "Standard (No BOM)"
        r["E4"]["valeurs"]["zone4_restriction"] = "SMD  Pad"
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(res, f)
        code, out, err = appeler(["--verifier", chemin, "--attendus", os.path.join(self.sortie, "attendus.json")])
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"{len(TOUTES)} confirmées, 0 contredites, 0 non faites", out)
        # valeur du mauvais type, autre base : entrée invalide
        r["E1"]["valeurs"]["rayon_coin_mm"] = "0,25"
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(res, f)
        self.assertEqual(appeler(["--verifier", chemin, "--attendus", os.path.join(self.sortie, "attendus.json")])[0], 2)
        res["base_sha256"] = "0" * 64
        r["E1"]["valeurs"]["rayon_coin_mm"] = 0.25
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(res, f)
        self.assertEqual(appeler(["--verifier", chemin, "--attendus", os.path.join(self.sortie, "attendus.json")])[0], 2)

    def test_e3_distingue_les_deux_conventions(self):
        """E3 : p0 hors des deux axes de la pièce, hypothèses de rotation (pièce − r, pièce + r) et de miroir
        différentes ; un relevé conforme à l'hypothèse contraire donne « contredite »."""
        e3 = self.exp["E3"]
        p0 = e3["objet"]["p0"]
        rot = list(p0["hypotheses_rotation_mod180"].values())
        self.assertGreater(abs((rot[0] - rot[1] + 90.0) % 180.0 - 90.0), 5.0)
        pos = [(h["x_mm"], h["y_mm"]) for h in p0["hypotheses_position"].values()]
        self.assertEqual(len(set(pos)), 3)
        m = {x["cle"]: x for x in e3["mesures"]}
        self.assertEqual(m["p0_rotation_deg"]["attendu"], rot[0])
        self.assertEqual((m["p0_x_mm"]["attendu"], m["p0_y_mm"]["attendu"]), pos[0])
        q = PcbDoc.ouvrir(os.path.join(self.sortie, e3["fichier"])).pastilles()[p0["index_pads6"]]
        self.assertAlmostEqual(q["rotation"] % 180.0, rot[0], places=3)
        self.assertTrue(e3["non_teste"] and "tailles haut et bas" in e3["non_teste"][0])
        res = lire(os.path.join(self.sortie, "resultats_a_remplir.json"))
        conformes = {k: valeur_conforme(x) for k, x in m.items()}
        chemin, verdicts = os.path.join(self.d, "r_e3.json"), os.path.join(self.d, "v_e3.json")
        attendus = os.path.join(self.sortie, "attendus.json")
        for valeurs, verdict in (({}, "confirmée"), ({"p0_rotation_deg": rot[1]}, "contredite"),
                                 ({"p0_x_mm": pos[1][0], "p0_y_mm": pos[1][1]}, "contredite")):
            with self.subTest(verdict=verdict, valeurs=valeurs):
                res["experiences"]["E3"] = {"faite": True, "remarques": "", "valeurs": dict(conformes, **valeurs)}
                with open(chemin, "w", encoding="utf-8") as f:
                    json.dump(res, f)
                code, out, err = appeler(["--verifier", chemin, "--attendus", attendus, "--json", verdicts])
                v = {x["id"]: x for x in lire(verdicts)["verdicts"]}["E3"]
                self.assertEqual(v["verdict"], verdict, out + err)
                self.assertIn("non testé", out)

    def test_resultats_mal_formes(self):
        """Structure de resultats_a_remplir.json cassée à la main : code 2 et message clair, jamais code 4."""
        attendus = os.path.join(self.sortie, "attendus.json")
        res = lire(os.path.join(self.sortie, "resultats_a_remplir.json"))
        chemin = os.path.join(self.d, "r_casse.json")
        cas = {"relevé texte": {"E1": "oui"}, "valeurs liste": {"E1": {"faite": True, "valeurs": [1, 2]}},
               "faite texte": {"E1": {"faite": "oui", "valeurs": {}}}, "experiences texte": "x",
               "experiences liste": [], "remarques nombre": {"E1": {"faite": False, "remarques": 3}}}
        for nom, experiences in cas.items():
            with self.subTest(cas=nom):
                with open(chemin, "w", encoding="utf-8") as f:
                    json.dump(dict(res, experiences=experiences), f)
                code, out, err = appeler(["--verifier", chemin, "--attendus", attendus])
                self.assertEqual(code, 2, out + err)
                self.assertIn("experiences", err)
                self.assertNotIn("erreur interne", err)
        att = lire(attendus)
        att["experiences"][0]["mesures"] = "x"
        casse = os.path.join(self.d, "a_casse.json")
        with open(casse, "w", encoding="utf-8") as f:
            json.dump(att, f)
        code, out, err = appeler(["--verifier", os.path.join(self.sortie, "resultats_a_remplir.json"),
                                  "--attendus", casse])
        self.assertEqual(code, 2, out + err)
        self.assertIn("structure invalide", err)

    def test_e5_isolement_faible(self):
        """Isolement de la règle au plus 0,1 mm : piste proche à la moitié de l'isolement, pas de saut."""
        doc = PcbDoc.ouvrir(self.base)
        with mock.patch.object(fabriquer_essais, "regle_valeur", return_value=("Clearance", 3.937)):
            r = fabriquer_essais.e5(doc)
        m = {x["cle"]: x for x in r["mesures"]}
        self.assertAlmostEqual(m["ecart_mm"]["attendu"], round(3.937 / 2 * 0.0254, 4))
        self.assertIs(m["violation_piste_proche"]["attendu"], True)
        self.assertAlmostEqual(r["objet"]["piste_proche"]["ecart_au_keepout_mil"], 3.937 / 2, places=3)


class TestSautsEtEntrees(unittest.TestCase):
    def test_base_privee_d_objets(self):
        """Carte synthétique ordinaire : 4 pièces (E6), pièce à pastilles alignées (E3), pas de règle
        PlaneClearance (E7) : expériences sautées avec leur motif, code 0, les autres fabriquées."""
        with commun.dossier_temporaire() as d:
            base = fabrique.fabriquer(d, nom="base.PcbDoc")
            code, out, err = appeler(["--base", base, "--sortie", os.path.join(d, "e")])
            self.assertEqual(code, 0, out + err)
            att = {e["id"]: e for e in lire(os.path.join(d, "e", "attendus.json"))["experiences"]}
            sautees = {i for i, e in att.items() if e["statut"] == "sautee"}
            self.assertEqual(sautees, {"E3", "E6", "E7"})
            self.assertIn("7 nécessaires", att["E6"]["motif"])
            self.assertIn("PlaneClearance", att["E7"]["motif"])
            for i in sautees:
                self.assertIn(f"{i} {att[i]['titre']} : sautée", out)
                self.assertNotIn(i, lire(os.path.join(d, "e", "resultats_a_remplir.json"))["experiences"])

    def test_sans_cuivre_libre_ni_plan(self):
        """Empilage sans plan, pistes, vias et régions absents : E7 sauté, T1 fabriqué sans modèle."""
        with commun.dossier_temporaire() as d:
            base = fabrique.fabriquer(d, nom="nue.PcbDoc", empilage="hors_ordre", essais=True,
                                      absents=("Tracks6", "Vias6", "Regions6", "ShapeBasedRegions6"))
            code, out, err = appeler(["--base", base, "--sortie", os.path.join(d, "e")])
            self.assertEqual(code, 0, out + err)
            att = {e["id"]: e for e in lire(os.path.join(d, "e", "attendus.json"))["experiences"]}
            self.assertEqual(att["E7"]["statut"], "sautee")
            self.assertEqual(att["T1"]["statut"], "fabriquee")
            t1 = PcbDoc.ouvrir(os.path.join(d, "e", att["T1"]["fichier"]))
            self.assertEqual((len(t1.pistes()), len(t1.vias()), len(t1.regions())), (1, 1, 1))

    def test_entrees_invalides(self):
        with commun.dossier_temporaire() as d:
            base = fabrique.fabriquer(d, nom="base.PcbDoc")
            for argv in ([], ["--sortie", d], ["--base", base], ["--verifier", base],
                         ["--base", base, "--sortie", d, "--verifier", base, "--attendus", base],
                         ["--base", os.path.join(d, "absente.PcbDoc"), "--sortie", d]):
                with self.subTest(argv=len(argv)):
                    code, out, err = appeler(argv)
                    self.assertEqual(code, 2, err)
                    self.assertNotIn(d, err)
            texte = os.path.join(d, "texte.PcbDoc")
            with open(texte, "w", encoding="utf-8") as f:
                f.write("pas un PcbDoc")
            self.assertEqual(appeler(["--base", texte, "--sortie", os.path.join(d, "e")])[0], 2)
            self.assertEqual(appeler(["--verifier", texte, "--attendus", texte])[0], 2)

    def test_aucun_nom_propre_a_une_carte(self):
        """Les objets sont choisis par leurs propriétés : aucun désignateur ni nom de carte dans l'outil."""
        with open(os.path.join(commun.RACINE, "fabriquer_essais.py"), encoding="utf-8") as f:
            texte = f.read()
        self.assertIsNone(re.search("J1|J2|U2|C3|" + "de" + "mo", texte))


class TestSensDesArcsDuContour(unittest.TestCase):
    """Annexe E-8, non-régression sur des cartes dessinées dans Altium : chaque sommet d'arc (KIND=1) est au
    point SA de l'arc et le sommet suivant au point EA (arc parcouru de SA à EA en sens trigonométrique). Le
    contour de METROID n'a aucun arc : il n'apporte rien ici."""

    def verifier(self, nom):
        import math
        c = commun.doc_partage(nom).contour_carte()
        arcs = [i for i, s in enumerate(c) if s["type"] == 1]
        self.assertTrue(arcs, f"{nom} : aucun arc dans le contour")
        for i in arcs:
            s, n = c[i], c[(i + 1) % len(c)]
            pa = (s["cx"] + s["r"] * math.cos(math.radians(s["sa"])), s["cy"] + s["r"] * math.sin(math.radians(s["sa"])))
            pb = (s["cx"] + s["r"] * math.cos(math.radians(s["ea"])), s["cy"] + s["r"] * math.sin(math.radians(s["ea"])))
            self.assertLess(math.dist(pa, (s["x"], s["y"])), 0.5, f"{nom} : sommet {i}")
            self.assertLess(math.dist(pb, (n["x"], n["y"])), 0.5, f"{nom} : sommet {i + 1}")

    def test_wifi(self):
        self.verifier(commun.WIFI)

    def test_sl1(self):
        self.verifier(commun.SL1)


class TestCarteSource(unittest.TestCase):
    def test_toutes_les_experiences(self):
        base = commun.carte(commun.SOURCE)
        with commun.dossier_temporaire() as d:
            code, out, err = appeler(["--base", base, "--sortie", d])
            self.assertEqual(code, 0, out + err)
            att = lire(os.path.join(d, "attendus.json"))
            self.assertEqual([e["statut"] for e in att["experiences"]], ["fabriquee"] * len(TOUTES))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] + commun.extraire_options(sys.argv[1:]))
