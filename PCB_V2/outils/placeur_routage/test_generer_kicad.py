"""Tests de generer_kicad.py : lecteur et écrivain S-expression, contrôle d'un gabarit KiCad (controle_gabarit/1),
génération d'une carte (étape C8) relue par l'importeur.

    python test_generer_kicad.py -v        (depuis ce dossier ; quelques secondes)

Les cartes KiCad sont écrites par les tests dans un dossier temporaire (aucune carte suivie n'est lue).
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import generer_kicad as K  # noqa: E402
import geom_commune as gc  # noqa: E402

# Clés de controle_gabarit/1 (table de §2.9 du contrat).
CLES_CONTROLE_CONTRAT = {"format", "fichier", "sha256", "utilisable", "erreurs", "avertissements",
                         "couches_ecrivibles", "non_retournables", "modeles"}

CARTE_MINIMALE = """(kicad_pcb (version %(version)s) (generator pcbnew)
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    %(internes)s
    (31 "B.Cu" signal)
    (37 "F.SilkS" user "F.Silkscreen")
    (44 "Edge.Cuts" user))
  (net 0 "")
  (net 1 "N1")
  (footprint "Essai:R" (layer "F.Cu") (at 10 10)
    (property "Reference" "R1" (at 0 -1.5 0) (layer "F.SilkS"))
    (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "N1"))
    (pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))
  (footprint "Essai:R" (layer "B.Cu") (at 20 10 90)
    (fp_text reference "R2" (at 0 1.5 90) (layer "B.SilkS"))
    (pad "1" smd rect (at -1 0) (size 1 1) (layers "B.Cu" "B.Paste" "B.Mask") (net 1 "N1"))
    (pad "2" smd rect (at 1 0) (size 1 1) (layers "B.Cu" "B.Paste" "B.Mask")))
  %(supplement)s
  (gr_rect (start 0 0) (end 30 20) (layer "Edge.Cuts") (width 0.1))
)
"""


def ecrire_carte(dossier, nom="carte.kicad_pcb", version="20221018", internes="", supplement=""):
    chemin = os.path.join(dossier, nom)
    with open(chemin, "w", encoding="utf-8", newline="\n") as f:
        f.write(CARTE_MINIMALE % {"version": version, "internes": internes, "supplement": supplement})
    return chemin


def entree_de(chemin, couches=("F.Cu", "B.Cu"), designateurs=("R1", "R2")):
    """Entrée entree_carte/2 minimale construite depuis la carte écrite : empreinte et origine."""
    with open(chemin, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    noms = {"F.Cu": "TopLayer", "B.Cu": "BottomLayer"}
    cs = [{"nom": noms.get(c, "MidLayer" + c[2:-3]), "nom_fichier": c, "rang": r, "type": "signal",
           "plans": [], "type_kicad": "signal"} for r, c in enumerate(couches)]
    return {"format": "entree_carte/2",
            "source": {"fichier": os.path.basename(chemin), "type": "kicad_pcb", "sha256": sha,
                       "version_fichier": "20221018", "origine_fichier_mil": None, "origine_fichier_mm": [0.0, 20.0],
                       "origine_cao_mil": None},
            "unite": "mm", "carte": {"couches": cs}, "composants": [{"designateur": d} for d in designateurs]}


class TestSexpr(unittest.TestCase):
    def test_aller_retour_et_echappements(self):
        texte = '(a b "c d" (e "f\\"g\\\\h" 1.5) (i "ligne\\nsuite") ())'
        arbre = K.lire_sexpr(texte)
        self.assertEqual(arbre[0], "a")
        self.assertIsInstance(arbre[2], K.Chaine)
        self.assertEqual(arbre[3][1], 'f"g\\h')
        self.assertEqual(arbre[4][1], "ligne\nsuite")
        self.assertEqual(arbre[5], [])
        relu = K.lire_sexpr(K.ecrire_sexpr(arbre))
        self.assertEqual(relu, arbre)
        self.assertEqual([type(x) for x in relu], [type(x) for x in arbre])

    def test_atome_garde_tel_quel(self):
        arbre = K.lire_sexpr("(at 1.000 -2.50 90)")
        self.assertEqual(arbre, ["at", "1.000", "-2.50", "90"])
        self.assertEqual(K.ecrire_sexpr(arbre), "(at 1.000 -2.50 90)")

    def test_chaine_vide_et_symbole_cite(self):
        arbre = ["net", "0", K.Chaine("")]
        self.assertEqual(K.ecrire_sexpr(arbre), '(net 0 "")')
        self.assertEqual(K.ecrire_sexpr(["x", "a b"]), '(x "a b")')

    def test_syntaxe_invalide(self):
        for texte, motif in (("(a (b)", "non fermée"), ("(a))", "en trop"), ("(a) (b)", "après la fin"),
                             ("", "aucune expression"), ("a", "hors de toute")):
            with self.subTest(texte=texte), self.assertRaises(gc.ErreurEntree) as ctx:
                K.lire_sexpr(texte, "x.kicad_pcb")
            self.assertIn(motif, str(ctx.exception))
            self.assertIn("x.kicad_pcb", str(ctx.exception))

    def test_ecrire_fichier_atomique(self):
        tmp = tempfile.mkdtemp(prefix="test_kicad_")
        try:
            chemin = os.path.join(tmp, "s.kicad_pcb")
            arbre = K.lire_sexpr(CARTE_MINIMALE % {"version": "20221018", "internes": "", "supplement": ""})
            K.ecrire_fichier(chemin, arbre)
            self.assertFalse(os.path.exists(chemin + ".tmp"))
            with open(chemin, "rb") as f:
                brut = f.read()
            self.assertNotIn(b"\r\n", brut)
            self.assertEqual(K.lire_sexpr(brut.decode("utf-8")), arbre)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestControleGabarit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_kicad_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_carte_minimale_utilisable(self):
        carte = ecrire_carte(self.tmp)
        r = K.controler_gabarit(carte, entree_de(carte))
        self.assertEqual(set(r), CLES_CONTROLE_CONTRAT)
        self.assertEqual(set(r["modeles"]), {"via", "piste", "region"})
        self.assertEqual(r["format"], "controle_gabarit/1")
        self.assertTrue(r["utilisable"], r["erreurs"])
        self.assertEqual(r["erreurs"], [])
        self.assertEqual(r["fichier"], "carte.kicad_pcb")
        with open(carte, "rb") as f:
            self.assertEqual(r["sha256"], hashlib.sha256(f.read()).hexdigest())
        self.assertEqual(r["couches_ecrivibles"], ["TopLayer", "BottomLayer"])
        self.assertEqual(r["non_retournables"], {})

    def test_version_ancienne_refusee(self):
        carte = ecrire_carte(self.tmp, version="20210000")
        r = K.controler_gabarit(carte, entree_de(carte))
        self.assertFalse(r["utilisable"])
        self.assertTrue(any("20210000" in e and "20211014" in e for e in r["erreurs"]), r["erreurs"])

    def test_empreinte_differente(self):
        carte = ecrire_carte(self.tmp)
        e = entree_de(carte)
        e["source"]["sha256"] = "0" * 64
        r = K.controler_gabarit(carte, e)
        self.assertFalse(r["utilisable"])
        self.assertTrue(any("carte.kicad_pcb" in x and "SHA-256" in x for x in r["erreurs"]), r["erreurs"])

    def test_couches_par_rang_et_non_retournables(self):
        supplement = """(footprint "Essai:Bobine" (layer "F.Cu") (at 5 5)
    (property "Reference" "L1")
    (pad "1" thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask"))
    (fp_poly (pts (xy -1 -1) (xy 1 -1) (xy 1 1)) (layer "In1.Cu") (width 0)))
  (footprint "Essai:Fente" (layer "F.Cu") (at 25 5)
    (property "Reference" "J1")
    (fp_line (start -1 0) (end 1 0) (layer "Edge.Cuts") (width 0.1)))"""
        carte = ecrire_carte(self.tmp, internes='(1 "In1.Cu" power)\n    (2 "In2.Cu" signal)',
                             supplement=supplement)
        e = entree_de(carte, couches=("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"), designateurs=("R1", "R2", "L1", "J1"))
        r = K.controler_gabarit(carte, e)
        self.assertTrue(r["utilisable"], r["erreurs"])
        self.assertEqual(r["couches_ecrivibles"], ["TopLayer", "MidLayer1", "MidLayer2", "BottomLayer"])
        self.assertEqual(set(r["non_retournables"]), {"L1", "J1"})
        self.assertIn("In1.Cu", r["non_retournables"]["L1"])
        self.assertIn("Edge.Cuts", r["non_retournables"]["J1"])
        # appariement par rang : un nom de fichier qui ne correspond pas au rang est une erreur
        e["carte"]["couches"][1]["nom_fichier"] = "In2.Cu"
        r = K.controler_gabarit(carte, e)
        self.assertFalse(r["utilisable"])
        self.assertTrue(any("rang 1" in x for x in r["erreurs"]), r["erreurs"])

    def test_comptes_differents_et_pieces_absentes(self):
        carte = ecrire_carte(self.tmp)
        r = K.controler_gabarit(carte, entree_de(carte, couches=("F.Cu", "In1.Cu", "B.Cu")))
        self.assertFalse(r["utilisable"])
        self.assertTrue(any("2 couches de cuivre dans le fichier, 3" in x for x in r["erreurs"]), r["erreurs"])
        r = K.controler_gabarit(carte, entree_de(carte, designateurs=("R1", "R2", "R3")))
        self.assertFalse(r["utilisable"])
        self.assertTrue(any("R3" in x for x in r["erreurs"]), r["erreurs"])

    def test_doublon_et_contour_absent(self):
        supplement = '(footprint "Essai:R" (layer "F.Cu") (at 2 2) (property "Reference" "R1"))'
        carte = ecrire_carte(self.tmp, supplement=supplement)
        r = K.controler_gabarit(carte, entree_de(carte))
        self.assertTrue(any("double" in x and "R1" in x for x in r["erreurs"]), r["erreurs"])
        chemin = os.path.join(self.tmp, "sans_contour.kicad_pcb")
        with open(chemin, "w", encoding="utf-8") as f:
            f.write('(kicad_pcb (version 20221018) (layers (0 "F.Cu" signal) (31 "B.Cu" signal)))')
        r = K.controler_gabarit(chemin, entree_de(chemin, designateurs=()))
        self.assertTrue(any("contour" in x for x in r["erreurs"]), r["erreurs"])

    def test_entrees_invalides(self):
        carte = ecrire_carte(self.tmp)
        with self.assertRaises(gc.ErreurEntree):
            K.controler_gabarit(carte, None)
        with self.assertRaises(gc.ErreurEntree):
            K.controler_gabarit(carte, {"format": "entree_carte/1"})
        with self.assertRaises(gc.ErreurEntree):
            K.controler_gabarit(os.path.join(self.tmp, "absent.kicad_pcb"), entree_de(carte))
        pas_kicad = os.path.join(self.tmp, "x.kicad_pcb")
        with open(pas_kicad, "w", encoding="utf-8") as f:
            f.write("(autre (version 1))")
        with self.assertRaises(gc.ErreurEntree):
            K.controler_gabarit(pas_kicad, entree_de(pas_kicad))


class TestLigneDeCommande(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_kicad_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def appeler(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = K.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_controle_codes(self):
        carte = ecrire_carte(self.tmp)
        e = os.path.join(self.tmp, "entree.json")
        g = os.path.join(self.tmp, "controle.json")
        with open(e, "w", encoding="utf-8") as f:
            json.dump(entree_de(carte), f)
        code, out, _ = self.appeler(["--controler-gabarit", carte, "--entree", e, "--sortie", g])
        self.assertEqual(code, 0, out)
        with open(g, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["utilisable"])
        vieille = ecrire_carte(self.tmp, nom="vieille.kicad_pcb", version="20210000")
        with open(e, "w", encoding="utf-8") as f:
            json.dump(entree_de(vieille), f)
        code, _, _ = self.appeler(["--controler-gabarit", vieille, "--entree", e, "--sortie", g])
        self.assertEqual(code, 1)
        code, _, err = self.appeler(["--controler-gabarit", vieille, "--sortie", g])
        self.assertEqual(code, 2)
        self.assertIn("--entree obligatoire", err)

    def test_generation_ligne_de_commande(self):
        carte, ent, graine = preparer_generation(self.tmp, [("R1", "top", 0.0, 0.0, 0.0)])
        e = os.path.join(self.tmp, "e.json")
        with open(e, "w", encoding="utf-8") as f:
            json.dump(ent, f)
        code, out, err = self.appeler([graine, os.path.join(self.tmp, "s.kicad_pcb"), "--gabarit", carte,
                                       "--entree", e])
        self.assertEqual(code, 0, out + err)
        code, _, err = self.appeler([graine, "s.kicad_pcb", "--gabarit", carte])
        self.assertEqual(code, 2)
        self.assertIn("--entree obligatoire", err)
        self.assertNotIn("Traceback", err)

    def test_arguments(self):
        code, _, err = self.appeler(["graine", "s.kicad_pcb"])
        self.assertEqual(code, 2)
        self.assertIn("--gabarit obligatoire", err)
        code, _, err = self.appeler(["graine", "s.kicad_pcb", "--gabarit", "g.kicad_pcb"])
        self.assertEqual(code, 2)
        self.assertIn("--entree obligatoire", err)
        self.assertEqual(self.appeler([])[0], 2)
        self.assertEqual(self.appeler(["--help"])[0], 0)

    def test_processus_sans_trace(self):
        r = subprocess.run([sys.executable, os.path.join(ICI, "generer_kicad.py"), "graine", "s.kicad_pcb"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--gabarit obligatoire", r.stderr)
        self.assertNotIn("Traceback", r.stderr + r.stdout)



def preparer_generation(dossier, poses, pistes=(), vias=()):
    """Carte minimale, son entrée (importeur du lot B) et un dossier de graine : ``poses`` = [(désignateur, side,
    dx_mil, dy_mil, rotation)] relatives à la pose importée ; pistes et vias en mil (repère commun)."""
    import import_kicad
    carte = ecrire_carte(dossier)
    ent = import_kicad.importer(carte)
    par = {c["designateur"]: c for c in ent["composants"]}
    pieces = []
    for d, side, dx, dy, rot in poses:
        c = par[d]
        pieces.append({"designator": d, "side": side, "x": c["x"] / 0.0254 + dx, "y": c["y"] / 0.0254 + dy,
                       "rotation": rot, "statut": "mobile"})
    g = os.path.join(dossier, "graine_1")
    os.makedirs(g, exist_ok=True)
    with open(os.path.join(g, "placements_routage.json"), "w", encoding="utf-8") as f:
        json.dump({"format": "placements/2", "unite": "mil", "origine": "coin", "pieces": pieces}, f)
    with open(os.path.join(g, "routage_detaille.json"), "w", encoding="utf-8") as f:
        json.dump({"format": "routage/2", "unite": "mil", "origine": "coin", "couches_routage": ["F.Cu", "B.Cu"],
                   "pistes": list(pistes), "vias": list(vias), "surfaces": []}, f)
    return carte, ent, g


def pastilles_monde(chemin):
    import import_kicad
    e = import_kicad.importer(chemin)
    ox, oy = e["source"]["origine_fichier_mm"]
    return {(c["designateur"], p["nom"]): (round(p["x"] + ox, 6), round(oy - p["y"], 6), c["face"])
            for c in e["composants"] for p in c["pastilles"]}, e


class TestGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_kicad_gen_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pose_retournement_pistes_vias(self):
        import import_kicad
        haut = [c["nom"] for c in import_kicad.importer(ecrire_carte(self.tmp))["carte"]["couches"]
                if c["rang"] == 0][0]
        pistes = [{"x1": 100, "y1": 100, "x2": 300, "y2": 100, "width": 10, "layer": haut, "net_name": "N1"}]
        vias = [{"x": 300, "y": 100, "size": 24, "hole_size": 12, "net": "N1", "de": haut, "a": haut}]
        carte, ent, g = preparer_generation(self.tmp, [("R1", "bottom", 100.0, -50.0, 45.0),
                                                       ("R2", "top", 0.0, 0.0, 180.0)], pistes, vias)
        s = os.path.join(self.tmp, "sortie.kicad_pcb")
        r = K.generer(g, s, carte, None, {"entree": ent})
        self.assertEqual(r["controles"]["erreurs"], [])
        self.assertEqual(r["format"], "rapport_pcbdoc/1")
        arbre = K.lire_arbre(K.lire_octets(s), "s")
        self.assertEqual(len(K.enfants(arbre, "segment")), 1)
        self.assertEqual(len(K.enfants(arbre, "via")), 1)
        fp = {K.designateur(f): f for f in K.empreintes(arbre)}
        self.assertEqual(K.enfant(fp["R1"], "layer")[1], "B.Cu")
        self.assertIn("B.Cu", K.enfant(K.enfants(fp["R1"], "pad")[0], "layers"))
        self.assertEqual(K.enfant(fp["R2"], "layer")[1], "F.Cu")
        # aller-retour : les pastilles reviennent à leur place d'origine (retournement et rotation réversibles)
        avant, _ = pastilles_monde(carte)
        ent2 = __import__("import_kicad").importer(s)
        par = {c["designateur"]: c for c in ent["composants"]}
        g2 = os.path.join(self.tmp, "graine_2")
        os.makedirs(g2)
        with open(os.path.join(g2, "placements_routage.json"), "w", encoding="utf-8") as f:
            json.dump({"format": "placements/2", "unite": "mil", "origine": "coin", "pieces": [
                {"designator": d, "side": "bottom" if par[d]["face"] == "dessous" else "top",
                 "x": par[d]["x"] / 0.0254, "y": par[d]["y"] / 0.0254, "rotation": par[d]["rotation"], "statut": "mobile"}
                for d in ("R1", "R2")]}, f)
        with open(os.path.join(g2, "routage_detaille.json"), "w", encoding="utf-8") as f:
            json.dump({"format": "routage/2", "unite": "mil", "origine": "coin", "pistes": [], "vias": []}, f)
        s2 = os.path.join(self.tmp, "retour.kicad_pcb")
        # le gabarit du retour est la carte produite, avec l'origine de la carte d'origine (même contour)
        ent2["source"]["origine_fichier_mm"] = ent["source"]["origine_fichier_mm"]
        r2 = K.generer(g2, s2, s, None, {"entree": ent2})
        self.assertEqual(r2["controles"]["erreurs"], [])
        apres, _ = pastilles_monde(s2)
        for k, v in avant.items():
            self.assertAlmostEqual(v[0], apres[k][0], places=5, msg=k)
            self.assertAlmostEqual(v[1], apres[k][1], places=5, msg=k)
            self.assertEqual(v[2], apres[k][2], k)

    def test_refus(self):
        carte, ent, g = preparer_generation(self.tmp, [("R1", "top", 0.0, 0.0, 0.0)],
                                            [{"x1": 0, "y1": 0, "x2": 10, "y2": 0, "width": 10, "layer": "X",
                                              "net_name": "N1"}])
        with self.assertRaises(gc.ErreurEntree):
            K.generer(g, os.path.join(self.tmp, "s.kicad_pcb"), carte, None, {"entree": ent})
        with self.assertRaises(gc.ErreurEntree):
            K.generer(g, carte, carte, None, {"entree": ent})


if __name__ == "__main__":
    unittest.main()
