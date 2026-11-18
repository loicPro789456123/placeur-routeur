"""Tests de generer_pcbdoc.py (controle_gabarit/1, rapport_pcbdoc/1, génération) et des briques de pose
(format_altium/altium_fichier/pose.py) et de sérigraphie (../serigraphie/pcbdoc_serigraphie.py).

    python test_generer_pcbdoc.py -v [--depot-local DOSSIER]      (depuis ce dossier ; environ 1 min)

Les tests n'utilisent que des fichiers suivis par git (cartes PCB_METROID_V2_rev3.PcbDoc et _rev4) : l'entrée
entree_carte/2 est construite depuis le gabarit (empreinte, origine, couches, désignateurs) et une graine
synthétique est fabriquée à partir du gabarit.

--depot-local DOSSIER : copie de travail locale du dépôt qui contient la carte posée par Altium
(PCB_METROID_V2_rev5_auto.PcbDoc, à sa racine) et la graine ed_1009 (PCB_V2/outils/placeur_routage/sorties/essais/) ;
sans elle, test_identique_a_altium est sauté (compté), sauf si ces fichiers sont dans ce dépôt-ci.
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
import generer_pcbdoc as G  # noqa: E402
from altium_fichier import PcbDoc, couches, pose  # noqa: E402
from altium_fichier.proprietes import valeur_mil  # noqa: E402
import pcbdoc_serigraphie as S  # noqa: E402

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(ICI)))
GABARIT = os.path.join(RACINE, "PCB_METROID_V2_rev3.PcbDoc")
REV4 = os.path.join(RACINE, "PCB_METROID_V2_rev4.PcbDoc")
DEPOT_LOCAL = None           # --depot-local


def chemins_1009():
    base = DEPOT_LOCAL or RACINE
    return (os.path.join(base, "PCB_METROID_V2_rev5_auto.PcbDoc"),
            os.path.join(base, "PCB_V2", "outils", "placeur_routage", "sorties", "essais", "ed_1009"))


# Clés des formats (tables de §2.9 du contrat), écrites ici indépendamment du module.
CLES_CONTROLE_CONTRAT = {"format", "fichier", "sha256", "utilisable", "erreurs", "avertissements",
                         "couches_ecrivibles", "non_retournables", "modeles"}
CLES_RAPPORT_CONTRAT = {"format", "fichier", "livrable", "controles", "conflits_isolement", "serigraphie",
                        "polygones_a_recouler", "chemins_effaces", "duree_s"}

_NOMS_TEST = {"TOP": "TopLayer", "BOTTOM": "BottomLayer"}


def nom_couche_test(v6):
    n = couches.NOMS_V6[v6]
    if n in _NOMS_TEST:
        return _NOMS_TEST[n]
    return ("MidLayer" + n[3:]) if n.startswith("MID") else ("InternalPlane" + n[5:])


def entree_depuis_gabarit(chemin):
    """Entrée entree_carte/2 minimale construite depuis le gabarit : empreinte, origine du repère commun (coin
    bas-gauche de la boîte du contour, mil absolus), couches de cuivre par rang, désignateurs."""
    with open(chemin, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    doc = PcbDoc.ouvrir(chemin)
    pts = [(s["x"], s["y"]) for s in doc.contour_carte()]
    b = doc.board
    cs = [{"nom": nom_couche_test(c["v6"]), "nom_fichier": c["nom_empilage"], "rang": r, "type": c["genre"],
           "plans": []} for r, c in enumerate(G.couches_cuivre(doc))]
    return {"format": "entree_carte/2",
            "source": {"fichier": os.path.basename(chemin), "type": "altium_pcbdoc", "sha256": sha,
                       "version_fichier": "", "origine_fichier_mil": [min(p[0] for p in pts), min(p[1] for p in pts)],
                       "origine_fichier_mm": None,
                       "origine_cao_mil": [valeur_mil(b.get("ORIGINX")), valeur_mil(b.get("ORIGINY"))]},
            "unite": "mm", "carte": {"couches": cs},
            "composants": [{"designateur": d} for d in G.designateurs(doc).values()]}


_ENTREES = {}


def entree(chemin=GABARIT):
    if chemin not in _ENTREES:
        _ENTREES[chemin] = entree_depuis_gabarit(chemin)
    return json.loads(json.dumps(_ENTREES[chemin]))



def empreinte(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


class TestTransformation(unittest.TestCase):
    def test_dessus_vers_dessous(self):
        # composant en (0,0) rot 0 dessus -> (100,0) rot 0 dessous : y local -> -y
        t = pose.Transformation((0, 0), 0, False, (100, 0), 0, True)
        self.assertEqual(t.point(10, 5), (110, -5))
        self.assertTrue(t.miroir)

    def test_rotation_meme_face(self):
        t = pose.Transformation((0, 0), 0, False, (0, 0), 90, False)
        self.assertEqual(t.point(10, 0), (0, 10))
        self.assertAlmostEqual(t.angle(0), 90)

    def test_dessous_rotation(self):
        # dessous : monde = o + R(r).diag(1,-1).local ; local (10,0) a 90 deg -> (0,10)
        t = pose.Transformation((0, 0), 0, False, (0, 0), 90, True)
        self.assertEqual(t.point(10, 0), (0, 10))
        self.assertEqual(t.point(0, 10), (10, 0))

    def test_aller_retour(self):
        t1 = pose.Transformation((5, 7), 30, False, (100, 50), 210, True)
        t2 = pose.Transformation((100, 50), 210, True, (5, 7), 30, False)
        x, y = t1.point_f(123.0, -45.0)
        X, Y = t2.point_f(x, y)
        self.assertAlmostEqual(X, 123.0)
        self.assertAlmostEqual(Y, -45.0)

    def test_arc_miroir(self):
        t = pose.Transformation((0, 0), 0, False, (0, 0), 0, True)
        # arc 0 -> 90 (quart haut-droit) devient 270 -> 360 (quart bas-droit)
        self.assertEqual(G.pose._angles_arc(t, 0.0, 90.0), (270.0, 360.0))
        self.assertEqual(G.pose._angles_arc(t, 0.0, 360.0), (0.0, 360.0))

    def test_format_rotation_delphi(self):
        self.assertEqual(pose._fmt_rot(270.0), " 2.70000000000000E+0002")
        self.assertEqual(pose._fmt_rot(0.0), " 0.00000000000000E+0000")
        self.assertEqual(pose._fmt_rot(90.0), " 9.00000000000000E+0001")


class TestGabarit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = PcbDoc.ouvrir(GABARIT)

    def setUp(self):
        self.d = PcbDoc.ouvrir(GABARIT)

    def test_pose_puis_retour(self):
        """Poser un composant ailleurs (autre face, autre rotation) puis le reposer a sa place
        redonne les memes primitives (a l'arrondi pres)."""
        d = self.d
        D = {n: i for i, n in G.designateurs(d).items()}
        i = D["U9"]
        c = d.data("Components6")[i]
        x0, y0, r0 = valeur_mil(c["X"]), valeur_mil(c["Y"]), float(c["ROTATION"])
        bas0 = c["LAYER"] == "BOTTOM"
        n, an = pose.poser_composant(d, i, 1234.5, 987.25, 90, not bas0)
        self.assertGreater(n, 5)
        self.assertEqual(an, [])
        self.assertEqual(d.data("Components6")[i]["LAYER"], "TOP" if bas0 else "BOTTOM")
        pose.poser_composant(d, i, x0, y0, r0, bas0)
        # comparaison champ a champ des pastilles, pistes et arcs (tolerance 2 unites internes)
        ref = self.doc
        for st in ("Pads6", "Tracks6", "Arcs6"):
            A = [pr.bloc.v for pr in ref.data(st) if pr.bloc["composant"] == i]
            B = [pr.bloc.v for pr in d.data(st) if pr.bloc["composant"] == i]
            for a, b in zip(A, B):
                for k in ("x", "y", "x1", "y1", "x2", "y2", "cx", "cy"):
                    if k in a:
                        self.assertLessEqual(abs(a[k] - b[k]), 2, (st, k))
                for k in ("couche", "couche_v7", "vers_couche", "inconnu_115", "depuis_couche"):
                    if k in a:
                        self.assertEqual(a[k], b[k], (st, k))

    def test_retournement_couches(self):
        d = self.d
        D = {n: i for i, n in G.designateurs(d).items()}
        i = D["U9"]
        bas = d.data("Components6")[i]["LAYER"] == "BOTTOM"
        couches_avant = sorted(couches.NOMS_V6[pr.bloc["couche"]] for st, pr in d.primitives_du_composant(i) if st == "Pads6")
        pose.poser_composant(d, i, 1000, 1000, 0, not bas)
        couches_apres = sorted(couches.NOMS_V6[pr.bloc["couche"]] for st, pr in d.primitives_du_composant(i) if st == "Pads6")
        echange = {"TOP": "BOTTOM", "BOTTOM": "TOP"}
        self.assertEqual(sorted(echange.get(c, c) for c in couches_avant), couches_apres)
        for st, pr in d.primitives_du_composant(i):
            if st == "Pads6":
                b = pr.bloc
                v7 = b["vers_couche"] | (b["inconnu_115"] << 8) | (b["depuis_couche"] << 24)
                self.assertEqual(couches.nom_v7(v7), couches.NOMS_V6[b["couche"]])

    def test_retournement_non_gere_signale(self):
        """Une primitive de composant sur une couche interne : changement de face refuse."""
        d = self.d
        D = {n: i for i, n in G.designateurs(d).items()}
        i = D["R1"]
        for st, pr in d.primitives_du_composant(i):
            if st == "Tracks6":
                pr.bloc["couche"] = couches.IDS_V6["MID1"]
                break
        bas = d.data("Components6")[i]["LAYER"] == "BOTTOM"
        _, an = pose.poser_composant(d, i, 1000, 1000, 0, not bas)
        self.assertTrue(any("couche interne" in a for a in an))

    def test_suppression_index_croises(self):
        d = self.d
        sel = G.cuivre_libre(d)
        self.assertGreater(len(sel["Tracks6"]), 0)
        self.assertGreater(len(sel["Vias6"]), 0)
        # rien d'un composant, d'un keepout ou d'une decoupe
        for st, idx in sel.items():
            for i in idx:
                b = d.data(st)[i].bloc
                self.assertEqual(b["composant"], 0xFFFF)
                self.assertFalse(b["drapeaux2"] & 2)
        n_avant = {st: len(d.data(st)) for st in sel}
        pose.supprimer_primitives(d, sel)
        for st, idx in sel.items():
            self.assertEqual(len(d.data(st)), n_avant[st] - len(idx))
        self.assertEqual(pose.verifier_guids(d), [])
        self.assertEqual(len(d.data("Vias6")), 0)
        # keepout et decoupes conserves
        self.assertTrue(any(pr.bloc["drapeaux2"] & 2 for pr in d.data("Tracks6")))
        self.assertTrue(any(pr.sous[0].props.get("ISBOARDCUTOUT") == "TRUE" for pr in d.data("Regions6")))
        self.assertEqual(sum(len(l[1]) for p, l in d.data("DrillManager").groupes), 0)

    def test_ajout_guid_et_percage(self):
        d = self.d
        pose.supprimer_primitives(d, G.cuivre_libre(d))
        d.ajouter_via((100, 200), 23.623, 11.812, "GND")
        d.ajouter_via((110, 200), 30, 15, "GND")
        d.ajouter_piste(1, (0, 0), (1, 0), 10, "GND")
        pose.rattacher_vias_percage(d)
        self.assertEqual(pose.verifier_guids(d), [])
        groupes = [(valeur_mil(p.get("TOOLSIZE")), l[1]) for p, l in d.data("DrillManager").groupes if p.get("OBJECTID") == "Via"]
        self.assertEqual(sorted(i for _, l in groupes for i in l), [0, 1])
        self.assertIn(15.0, [round(t, 3) for t, _ in groupes])  # groupe cree pour le nouveau trou

    def test_isolement_cuivre_empreinte(self):
        """Surfaces de cuivre d'empreinte hors pastilles (U10 : 4 regions GND) : une piste +12V a
        moins de l'isolement est signalee, une piste eloignee ou du meme reseau ne l'est pas
        (cas gh_2004 : 3 defauts Altium retrouves)."""
        d = self.d
        pose.supprimer_primitives(d, G.cuivre_libre(d))
        iso = G.isolement_defaut(d)
        self.assertAlmostEqual(iso, 7.874, places=3)
        self.assertEqual(G.controle_cuivre_empreintes(d, 0, 0, iso), [])
        # region U10 (gabarit) : x 2951.68..2961.52, y 3069.06..3090.71 ; piste 10 mil a 2 mil du bord
        d.ajouter_piste(1, (2956, 3097.7), (2956, 3130), 10, "+12V")
        d.ajouter_piste(1, (2956, 3092), (2956, 3130), 10, "GND")
        d.ajouter_piste(1, (2956, 3110), (2956, 3130), 10, "+12V")
        d.ajouter_via((2940, 3080), 23.622, 11.811, "+12V")
        c = G.controle_cuivre_empreintes(d, 0, 0, iso)
        self.assertEqual(len(c), 2, c)
        self.assertTrue(all(x["region"].startswith("U10 GND") for x in c))
        self.assertAlmostEqual(min(x["isolement_mil"] for x in c if x["objet"].startswith("piste")), 3097.7 - 5 - 3090.71, places=1)

    def test_geometrie_carte(self):
        c = S.geometrie_carte(self.d, [[1, 2, 3]], {"tailles_mm": "auto", "garde_masque_mm": None})
        self.assertEqual(c["format"], "carte_serigraphie/1")
        xs, ys = [p[0] for p in c["contour"]], [p[1] for p in c["contour"]]
        self.assertAlmostEqual(max(xs), 6456.693, places=2)
        self.assertAlmostEqual(max(ys), 3818.898, places=2)
        self.assertEqual(len(c["decoupes"]), 4)
        self.assertEqual(len(c["trous"]), 5)
        self.assertEqual(c["trous"][-1], [1, 2, 3])
        self.assertEqual(c["regles"]["garde_masque_mm"], 0.254)
        self.assertEqual(len(c["regles"]["tailles_mm"]), 3)

    def test_appariement_par_rang(self):
        """Les couches du routage sont nommées par l'entrée et appariées par rang à l'empilage, jamais par leur nom."""
        e = entree()
        noms = ["A", "B", "C", "D"]
        for c, n in zip(e["carte"]["couches"], noms):
            c["nom"] = n
        v6, ecrivibles, erreurs, _ = G.apparier_couches(self.d, e, "gabarit")
        self.assertEqual(erreurs, [])
        self.assertEqual([v6[n] for n in noms], [c["v6"] for c in G.couches_cuivre(self.d)])
        self.assertEqual(ecrivibles, noms)
        e["carte"]["couches"].pop()
        _, _, erreurs, _ = G.apparier_couches(self.d, e, "gabarit")
        self.assertTrue(any("4 couches de cuivre dans l'empilage, 3" in x for x in erreurs), erreurs)


def graine_synthetique(dossier, doc):
    """Graine fabriquee a partir du gabarit : 6 composants deplaces (dont 3 retournes), 3 pistes,
    2 vias, 1 via de couture."""
    os.makedirs(dossier, exist_ok=True)
    D = G.designateurs(doc)
    pl = []
    choisis = ["U9", "R1", "C1", "Q1", "R88", "C23"]
    for k, (i, n) in enumerate(sorted(D.items())):
        c = doc.data("Components6")[i]
        p = {"designator": n, "x": valeur_mil(c["X"]), "y": valeur_mil(c["Y"]),
             "rotation": round(float(c["ROTATION"])) % 360, "side": "bottom" if c["LAYER"] == "BOTTOM" else "top"}
        if n in choisis:
            j = choisis.index(n)
            p["x"] = 600 + 300 * j
            p["y"] = 600
            p["rotation"] = (90 * j) % 360
            if j % 2 == 0:
                p["side"] = "top" if p["side"] == "bottom" else "bottom"
        pl.append(p)
    pistes = [{"layer": "TopLayer", "net_name": "GND", "width": 10, "x1": 100, "y1": 100, "x2": 300, "y2": 100},
              {"layer": "MidLayer2", "net_name": "GND", "width": 12.5, "x1": 300, "y1": 100, "x2": 300, "y2": 400},
              {"layer": "BottomLayer", "net_name": "+12V", "width": 20, "x1": 500, "y1": 500, "x2": 700, "y2": 700}]
    vias = [{"x": 300, "y": 100, "size": 23.623, "hole_size": 11.812, "net": "GND"},
            {"x": 700, "y": 700, "size": 30, "hole_size": 15, "net": "+12V"}]
    with open(os.path.join(dossier, "placements_routage.json"), "w") as f:
        json.dump(pl, f)
    with open(os.path.join(dossier, "routage_detaille.json"), "w") as f:
        json.dump({"pistes": pistes, "vias": vias}, f)
    with open(os.path.join(dossier, "vias_couture.json"), "w") as f:
        json.dump({"vias": [{"x": 59.055, "y": 344.488, "size": 23.623, "hole_size": 11.812, "net": "GND"}]}, f)
    return pl, pistes, vias


def ecrire_graine_json(dossier, nom, objet):
    with open(os.path.join(dossier, nom), "w", encoding="utf-8") as f:
        json.dump(objet, f)


class TestControleGabarit(unittest.TestCase):
    """controle_gabarit/1 sur les cartes METROID suivies (rev3, rev4)."""

    def verifier_carte(self, chemin):
        e = entree(chemin)
        r = G.controler_gabarit(chemin, e)
        self.assertEqual(set(r), CLES_CONTROLE_CONTRAT)
        self.assertEqual(set(r["modeles"]), {"via", "piste", "region"})
        self.assertEqual(r["format"], "controle_gabarit/1")
        self.assertTrue(r["utilisable"], r["erreurs"])
        self.assertEqual(r["erreurs"], [])
        self.assertEqual(r["fichier"], os.path.basename(chemin))
        self.assertEqual(r["sha256"], empreinte(chemin))
        signal = [c["nom"] for c in e["carte"]["couches"] if c["type"] == "signal"]
        self.assertEqual(r["couches_ecrivibles"], signal)
        self.assertIsInstance(r["non_retournables"], dict)
        for raison in r["non_retournables"].values():
            self.assertIsInstance(raison, str)
            self.assertTrue(raison)
        print("\n  %s : couches écrivibles %s ; non retournables %s" % (
            r["fichier"], r["couches_ecrivibles"], json.dumps(r["non_retournables"], ensure_ascii=False)))
        return r

    def test_rev3(self):
        h0 = empreinte(GABARIT)
        self.verifier_carte(GABARIT)
        self.assertEqual(empreinte(GABARIT), h0, "le gabarit ne doit jamais être modifié")

    def test_rev4(self):
        self.verifier_carte(REV4)

    def test_empreinte_differente(self):
        e = entree(GABARIT)
        e["source"]["sha256"] = empreinte(REV4)
        r = G.controler_gabarit(GABARIT, e)
        self.assertFalse(r["utilisable"])
        self.assertTrue(any(os.path.basename(GABARIT) in x and "SHA-256" in x for x in r["erreurs"]), r["erreurs"])

    def test_erreurs_de_l_entree(self):
        e = entree(GABARIT)
        e["composants"].append({"designateur": "X999"})
        e["carte"]["couches"][1]["type"] = "plan_negatif"
        del e["source"]["origine_fichier_mil"]
        r = G.controler_gabarit(GABARIT, e)
        self.assertFalse(r["utilisable"])
        texte = " | ".join(r["erreurs"])
        self.assertIn("X999", texte)
        self.assertIn("rang 1", texte)
        self.assertIn("origine_fichier_mil", texte)
        for mauvais in (None, {"format": "entree_carte/1"}, [1, 2]):
            with self.assertRaises(G.gc.ErreurEntree):
                G.controler_gabarit(GABARIT, mauvais)

    def test_ligne_de_commande(self):
        tmp = tempfile.mkdtemp(prefix="test_pcbdoc_")
        try:
            ch_e, ch_g = os.path.join(tmp, "entree.json"), os.path.join(tmp, "controle.json")
            ecrire_graine_json(tmp, "entree.json", entree(GABARIT))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = G.main(["--controler-gabarit", GABARIT, "--entree", ch_e, "--sortie", ch_g])
            self.assertEqual(code, 0, out.getvalue())
            with open(ch_g, encoding="utf-8") as f:
                self.assertTrue(json.load(f)["utilisable"])
            ecrire_graine_json(tmp, "entree.json", entree(REV4))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(G.main(["--controler-gabarit", GABARIT, "--entree", ch_e, "--sortie", ch_g]), 1)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(G.main(["--controler-gabarit", GABARIT, "--sortie", ch_g]), 2)
            self.assertIn("--entree obligatoire", err.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_pcbdoc_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def graine(self):
        g = os.path.join(self.tmp, "graine")
        pl, _, _ = graine_synthetique(g, PcbDoc.ouvrir(GABARIT))
        return g, pl

    def test_graine_synthetique_sans_serigraphie(self):
        h0 = empreinte(GABARIT)
        g, pl = self.graine()
        sortie = os.path.join(self.tmp, "sortie.PcbDoc")
        ch_rapport = os.path.join(self.tmp, "carte_rapport.json")
        r, det = G.generer_detaille(g, sortie, GABARIT, None, sans_serigraphie=True,
                                    opts={"entree": entree(), "rapport": ch_rapport})
        self.assertEqual(empreinte(GABARIT), h0, "le gabarit ne doit jamais être modifié")
        self.assertEqual(r["controles"]["erreurs"], [])
        c = det["controles"]
        self.assertEqual(c["pistes"]["lues"], 3)
        self.assertEqual(c["vias"]["lues"], 3)
        self.assertEqual(len(det["changements_de_face"]), 3)
        with open(ch_rapport, encoding="utf-8") as f:
            self.assertEqual(json.load(f), r)
        # relecture indépendante
        d = PcbDoc.ouvrir(sortie)
        D = {n: i for i, n in G.designateurs(d).items()}
        for p in pl:
            comp = d.data("Components6")[D[p["designator"]]]
            self.assertAlmostEqual(valeur_mil(comp["X"]), p["x"], places=3)
            self.assertEqual(comp["LAYER"] == "BOTTOM", p["side"] == "bottom")
        libres = [pr for pr in d.data("Tracks6") if pr.bloc["composant"] == 0xFFFF
                  and G.est_cuivre_signal(pr.bloc["couche"])]
        self.assertEqual(len(libres), 3)
        self.assertEqual(len([pr for pr in d.data("Regions6") if pr.bloc["polygone"] != 0xFFFF]), 0)
        self.assertEqual(d.board.get("FILENAME") or "", "")
        self.assertTrue(r["chemins_effaces"])

    def test_cles_du_rapport(self):
        g, _ = self.graine()
        r = G.generer(g, os.path.join(self.tmp, "s.PcbDoc"), GABARIT, None, sans_serigraphie=True,
                      opts={"entree": entree()})
        self.assertEqual(set(r), CLES_RAPPORT_CONTRAT)
        self.assertEqual(r["format"], "rapport_pcbdoc/1")
        self.assertEqual(r["fichier"], "s.PcbDoc")
        self.assertEqual(r["livrable"], "valide")
        self.assertEqual(set(r["controles"]), {"erreurs", "anomalies"})
        self.assertEqual(set(r["serigraphie"]), {"etat", "masques"})
        self.assertEqual(r["serigraphie"]["etat"], "non_faite")
        self.assertIsInstance(r["conflits_isolement"], int)
        self.assertIsInstance(r["polygones_a_recouler"], int)
        self.assertIsInstance(r["chemins_effaces"], bool)
        self.assertIsInstance(r["duree_s"], float)
        json.dumps(r, allow_nan=False)

    def test_config_pcbdoc(self):
        g, _ = self.graine()
        cfg = {"format": "config_pcbdoc/2", "livrable": "apercu", "vias_couture": False, "_note": "commentaire",
               "serigraphie": {"chaines": 2}}
        r, det = G.generer_detaille(g, os.path.join(self.tmp, "s.PcbDoc"), GABARIT, cfg, sans_serigraphie=True,
                                    opts={"entree": entree()})
        self.assertEqual(r["livrable"], "apercu")
        self.assertEqual(det["vias_couture_ajoutees"], 0)
        self.assertEqual(det["controles"]["vias"]["lues"], 2)
        for mauvais in ({"format": "config_pcbdoc/1"}, {"format": "config_pcbdoc/2", "livrable": "fini"},
                        {"format": "config_pcbdoc/2", "serigraphie": {"chaines": 0}},
                        {"format": "config_pcbdoc/2", "serigraphie": {"tailles_mm": [[1.0]]}}):
            with self.subTest(config=mauvais), self.assertRaises(G.gc.ErreurEntree):
                G.charger_config(mauvais)
        self.assertEqual(G.charger_config(None)["serigraphie"]["mouvements"], 20000000)

    def test_entree_obligatoire_et_empreinte(self):
        g, _ = self.graine()
        sortie = os.path.join(self.tmp, "s.PcbDoc")
        with self.assertRaises(G.gc.ErreurEntree) as ctx:
            G.generer(g, sortie, GABARIT, None, sans_serigraphie=True)
        self.assertIn("--entree obligatoire", str(ctx.exception))
        with self.assertRaises(G.gc.ErreurEntree) as ctx:
            G.generer(g, sortie, None, None, sans_serigraphie=True, opts={"entree": entree()})
        self.assertIn("--gabarit obligatoire", str(ctx.exception))
        with self.assertRaises(G.ErreurGeneration) as ctx:
            G.generer(g, sortie, GABARIT, None, sans_serigraphie=True, opts={"entree": entree(REV4)})
        self.assertIn("SHA-256", str(ctx.exception))
        self.assertIn(os.path.basename(GABARIT), str(ctx.exception))
        self.assertFalse(os.path.exists(sortie))

    def test_origine_lue_dans_l_entree(self):
        """L'origine du repère commun vient de entree.source.origine_fichier_mil, jamais recalculée."""
        g, pl = self.graine()
        e = entree()
        e["source"]["origine_fichier_mil"] = [100.0, -50.0]
        sortie = os.path.join(self.tmp, "s.PcbDoc")
        r = G.generer(g, sortie, GABARIT, None, sans_serigraphie=True, opts={"entree": e})
        self.assertEqual(r["controles"]["erreurs"], [])
        d = PcbDoc.ouvrir(sortie)
        D = {n: i for i, n in G.designateurs(d).items()}
        p = next(p for p in pl if p["designator"] == "U9")
        comp = d.data("Components6")[D["U9"]]
        self.assertAlmostEqual(valeur_mil(comp["X"]), p["x"] + 100.0, places=3)
        self.assertAlmostEqual(valeur_mil(comp["Y"]), p["y"] - 50.0, places=3)

    def test_sortie_egale_gabarit_refusee(self):
        with self.assertRaises(G.ErreurGeneration):
            G.generer(self.tmp, GABARIT, GABARIT, None, sans_serigraphie=True, opts={"entree": entree()})

    def test_designateur_inconnu(self):
        g, _ = self.graine()
        with open(os.path.join(g, "placements_routage.json"), encoding="utf-8") as f:
            pl = json.load(f)
        pl.append({"designator": "X999", "x": 0, "y": 0, "rotation": 0, "side": "top"})
        ecrire_graine_json(g, "placements_routage.json", pl)
        with self.assertRaises(G.ErreurGeneration):
            G.generer(g, os.path.join(self.tmp, "s.PcbDoc"), GABARIT, None, sans_serigraphie=True,
                      opts={"entree": entree()})

    def test_carte_sans_via_ni_piste_libre(self):
        """Gabarit rev3 privé de tout son cuivre libre (dossier temporaire) : PcbDoc.ajouter_* sans modèle."""
        d = PcbDoc.ouvrir(GABARIT)
        pose.supprimer_primitives(d, G.cuivre_libre(d))
        self.assertEqual(len(d.data("Vias6")), 0)
        self.assertFalse(any(pr.bloc["composant"] == 0xFFFF and G.est_cuivre_signal(pr.bloc["couche"])
                             and not pr.bloc["drapeaux2"] & 2 for pr in d.data("Tracks6")))
        nu = os.path.join(self.tmp, "nu.PcbDoc")
        d.enregistrer(nu)
        e = entree(GABARIT)
        e["source"]["sha256"] = empreinte(nu)
        e["source"]["fichier"] = "nu.PcbDoc"
        g, _ = self.graine()
        sortie = os.path.join(self.tmp, "sortie.PcbDoc")
        r, det = G.generer_detaille(g, sortie, nu, None, sans_serigraphie=True, opts={"entree": e})
        self.assertEqual(r["controles"]["erreurs"], [])
        self.assertTrue(r["chemins_effaces"])
        self.assertEqual(det["controles"]["pistes"]["lues"], 3)
        self.assertEqual(det["controles"]["vias"]["lues"], 3)
        relu = PcbDoc.ouvrir(sortie)
        self.assertEqual(relu.board.get("FILENAME") or "", "")
        self.assertEqual(len(relu.data("Vias6")), 3)

    def test_filename_vide(self):
        self.assertNotEqual(PcbDoc.ouvrir(GABARIT).board.get("FILENAME") or "", "", "gabarit sans FILENAME")
        g, _ = self.graine()
        sortie = os.path.join(self.tmp, "s.PcbDoc")
        r = G.generer(g, sortie, GABARIT, None, sans_serigraphie=True, opts={"entree": entree()})
        self.assertTrue(r["chemins_effaces"])
        self.assertEqual(PcbDoc.ouvrir(sortie).board.get("FILENAME") or "", "")

    def test_piece_non_reposee(self):
        g, pl = self.graine()
        sortie = os.path.join(self.tmp, "s.PcbDoc")
        appels = []
        orig = pose.poser_composant

        def espion(doc, idx, *a, **k):
            appels.append(idx)
            return orig(doc, idx, *a, **k)
        pose.poser_composant = espion
        try:
            r, det = G.generer_detaille(g, sortie, GABARIT, None, sans_serigraphie=True, opts={"entree": entree()})
        finally:
            pose.poser_composant = orig
        self.assertEqual(r["controles"]["erreurs"], [])
        self.assertEqual(len(appels), det["composants_poses"])
        self.assertGreaterEqual(det["composants_poses"], 6)
        self.assertGreater(det["composants_inchanges"], 0)
        self.assertEqual(det["composants_poses"] + det["composants_inchanges"], len(pl))

    def test_pose_inchangee(self):
        c = {"X": "100mil", "Y": "200mil", "ROTATION": " 9.00000000000000E+0001", "LAYER": "TOP"}
        self.assertTrue(G.pose_inchangee(c, 100.0005, 200, 90.0000005, False))
        self.assertTrue(G.pose_inchangee(c, 100, 200, 450, False))
        self.assertFalse(G.pose_inchangee(c, 100.002, 200, 90, False))
        self.assertFalse(G.pose_inchangee(c, 100, 200, 90.00001, False))
        self.assertFalse(G.pose_inchangee(c, 100, 200, 90, True))

    def test_cuivre_conserve(self):
        d = PcbDoc.ouvrir(GABARIT)
        sel = G.cuivre_libre(d)
        i = sorted(sel["Tracks6"])[0]
        v = sorted(sel["Vias6"])[0]
        b, bv = d.data("Tracks6")[i].bloc, d.data("Vias6")[v].bloc
        mm = 0.0254
        don = {"cuivre_fixe": [
            {"id": "F0", "type": "piste", "reseau": "X", "couches": [couches.NOMS_V6[b["couche"]]],
             "x1": (b["x2"] / 1e4 - 10) * mm, "y1": (b["y2"] / 1e4 - 20) * mm,
             "x2": (b["x1"] / 1e4 - 10) * mm, "y2": (b["y1"] / 1e4 - 20) * mm, "largeur": 0.25},
            {"id": "F1", "type": "via", "reseau": "X", "couches": [], "x": (bv["x"] / 1e4 - 10) * mm,
             "y": (bv["y"] / 1e4 - 20) * mm, "diametre": 0.6, "trou": 0.3},
            {"id": "F2", "type": "via", "reseau": "", "couches": [], "x": 0, "y": 0}]}
        sel2 = G.cuivre_libre(d, garder=G.cuivre_conserve(don, 10, 20))
        self.assertEqual(sel["Tracks6"] - sel2["Tracks6"], {i})
        self.assertEqual(sel["Vias6"] - sel2["Vias6"], {v})
        self.assertIsNone(G.cuivre_conserve({"cuivre_fixe": [don["cuivre_fixe"][2]]}, 0, 0))

    def test_repli_sans_moteur(self):
        g, _ = self.graine()
        sortie = os.path.join(self.tmp, "s.PcbDoc")
        r = G.generer(g, sortie, GABARIT, None, opts={"entree": entree(),
                                                      "moteur_serigraphie": os.path.join(self.tmp, "absent.exe")})
        self.assertEqual(r["serigraphie"]["etat"], "non_faite")
        self.assertEqual(r["controles"]["erreurs"], [])
        self.assertTrue(any("sérigraphie non faite" in a for a in r["controles"]["anomalies"]))
        PcbDoc.ouvrir(sortie)
        ch = os.path.join(self.tmp, "entree.json")
        with open(ch, "w", encoding="utf-8") as f:
            json.dump(entree(), f)
        err = io.StringIO()
        with contextlib.redirect_stdout(err), contextlib.redirect_stderr(err):
            code = G.main([g, os.path.join(self.tmp, "s2.PcbDoc"), "--gabarit", GABARIT, "--entree", ch,
                           "--moteur-serigraphie", os.path.join(self.tmp, "absent.exe")])
        self.assertEqual(code, 0, err.getvalue())
        PcbDoc.ouvrir(os.path.join(self.tmp, "s2.PcbDoc"))

    @unittest.skipUnless(os.path.isfile(G.chemin_moteur({})), "moteur de sérigraphie non construit")
    def test_graine_synthetique_avec_serigraphie(self):
        g, _ = self.graine()
        sortie = os.path.join(self.tmp, "sortie.PcbDoc")
        cfg = {"format": "config_pcbdoc/2", "serigraphie": {"graine": 1, "chaines": 2, "mouvements": 20000,
                                                            "threads": 2}}
        r, det = G.generer_detaille(g, sortie, GABARIT, cfg, opts={"entree": entree()})
        self.assertEqual(r["controles"]["erreurs"], [])
        n, tot = map(int, det["controles"]["designateurs_conformes"].split("/"))
        self.assertEqual(n, tot)
        self.assertTrue(det["controle_serigraphie"]["coherent"])
        self.assertEqual(r["serigraphie"]["etat"], "coherent")


class TestLigneDeCommande(unittest.TestCase):
    def appeler(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = G.main(argv)
        return code, err.getvalue()

    def test_gabarit_et_entree_obligatoires(self):
        code, err = self.appeler(["graine", "s.PcbDoc"])
        self.assertEqual(code, 2)
        self.assertIn("--gabarit obligatoire", err)
        code, err = self.appeler(["graine", "s.PcbDoc", "--gabarit", "g.PcbDoc"])
        self.assertEqual(code, 2)
        self.assertIn("--entree obligatoire", err)
        self.assertEqual(self.appeler([])[0], 2)
        self.assertEqual(self.appeler(["--help"])[0], 0)
        self.assertEqual(self.appeler(["graine", "s.PcbDoc", "--mouvements", "3"])[0], 2)

    def test_processus_sans_trace(self):
        tmp = tempfile.mkdtemp(prefix="test_pcbdoc_")
        try:
            for args in (["graine", "s.PcbDoc"], ["graine", "s.PcbDoc", "--gabarit", "g.PcbDoc"]):
                r = subprocess.run([sys.executable, os.path.join(ICI, "generer_pcbdoc.py")] + args,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tmp)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertNotIn("Traceback", r.stderr + r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestReferenceAltium(unittest.TestCase):
    """La graine ed_1009 posée sans Altium doit être identique, champ par champ, à la même graine posée par Altium
    (PCB_METROID_V2_rev5_auto.PcbDoc) : composants, pastilles, pistes, vias, arcs, corps 3D (polygones, désignateurs
    et commentaires exclus)."""

    def test_identique_a_altium(self):
        ref, graine = chemins_1009()
        if not (os.path.exists(ref) and os.path.isdir(graine)):
            self.skipTest("référence Altium ed_1009 absente (--depot-local)")
        import differentiel
        tmp = tempfile.mkdtemp(prefix="test_pcbdoc_")
        try:
            sortie = os.path.join(tmp, "auto.PcbDoc")
            r = G.generer(graine, sortie, GABARIT, None, sans_serigraphie=True, opts={"entree": entree()})
            self.assertEqual(r["controles"]["erreurs"], [])
            L = differentiel.comparer_apparie(sortie, ref)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        corps = "\n".join(L)
        self.assertIn("champs differents : aucun", L[1])
        for st in ("Pads6", "Tracks6", "Arcs6", "Vias6", "Fills6", "ComponentBodies6", "ShapeBasedComponentBodies6"):
            ligne = next(l for l in L if l.startswith(st + " :"))
            self.assertIn("seulement A 0, seulement B 0", ligne, corps)
            i = L.index(ligne)
            self.assertFalse(i + 1 < len(L) and L[i + 1].startswith("    "), corps)


def _extraire_options(argv):
    """Retire ``--depot-local DOSSIER`` (ou ``--depot-local=DOSSIER``) de ``argv`` ; rend le dossier."""
    dossier, reste, i = None, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--depot-local" and i + 1 < len(argv):
            dossier = argv[i + 1]
            i += 2
            continue
        if a.startswith("--depot-local="):
            dossier = a.split("=", 1)[1]
        else:
            reste.append(a)
        i += 1
    argv[:] = reste
    return dossier


if __name__ == "__main__":
    DEPOT_LOCAL = _extraire_options(sys.argv)
    unittest.main()
