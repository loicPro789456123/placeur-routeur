"""Tests de construire_donnees.py (donnees_placeur/2, §2.5 ; deduction/1, §2.9 ; règles de §3.1 à §3.3, §3.5, §3.6,
§3.8 ; contraintes de §2.3).

    python -B -m unittest test_construire_donnees -v

Entrées entree_carte/2 synthétiques (noms sans rapport avec une carte réelle) et cartes KiCad écrites dans un dossier
temporaire (2 couches sans plan ; 4 couches à plans de type power), importées par importer_carte.
"""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import construire_donnees as CD  # noqa: E402
import deduire_config as DC  # noqa: E402
import formats  # noqa: E402
import importer_carte as IC  # noqa: E402
from formats import ErreurFormat  # noqa: E402

MIL = 0.0254
VIA = {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.5, "diametre_max": 0.8, "trou_min": 0.25, "trou_max": 0.4}


# ---------------------------------------------------------------------------------------------------------------------
# Entrées synthétiques
# ---------------------------------------------------------------------------------------------------------------------

def pastille(des, k, reseau, x=0.0, y=0.0, l=0.6, h=0.6, forme="rect", couches=("TopLayer",), trou=None,
             classe=0, via=False, nom=None):
    tr = trou is not None
    return {"id": f"{des}#{k}", "nom": nom or str(k + 1), "reseau": reseau, "x": x, "y": y, "forme": forme,
            "largeur": l, "hauteur": h, "rotation": 0.0, "rayon_coin": 0.0, "points": [],
            "couches": list(couches) if not tr else ["TopLayer", "BottomLayer"], "face": "composant",
            "trou": None if not tr else {"diametre": trou, "forme": "rond", "longueur": 0.0, "rotation": 0.0,
                                         "decalage": [0.0, 0.0], "metallise": True},
            "traversante": tr, "expansion_masque": 0.0, "pile": None, "via_dans_pastille_permis": via or tr,
            "classe_isolement": classe}


def piece(des, desc, reseaux, x=20.0, y=20.0, pas=1.0, face="dessus", hauteur=1.0, valeur="", genre="standard",
          trou=None, l=0.6, verrouille=False, canal=None, empreinte="", classe=0):
    """Composant : une pastille par réseau, alignées au pas ``pas`` le long de x."""
    pads = [pastille(des, k, r, x=pas * k, l=l, h=l, trou=trou, classe=classe,
                     couches=("BottomLayer",) if face == "dessous" else ("TopLayer",))
            for k, r in enumerate(reseaux)]
    n = max(1, len(reseaux))
    return {"designateur": des, "designateur_source": des, "empreinte": empreinte, "description": desc,
            "reference": "", "valeur": valeur, "genre": genre, "classes_composants": [], "canal": canal,
            "hauteur": hauteur, "source_hauteur": "hauteur" if hauteur else "inconnue", "x": x, "y": y,
            "rotation": 0.0, "face": face, "verrouille": verrouille,
            "boite": [-0.5, -0.5, pas * (n - 1) + 0.5, 0.5], "pastilles": pads, "regions": [], "vias": [],
            "zones_interdites": [], "decoupes": []}


def couche(nom, rang, nom_fichier=None, pistes=0, type_="signal", plans=None, kicad=None, permis=None):
    return {"nom": nom, "nom_fichier": nom_fichier or nom, "rang": rang, "type": type_, "plans": plans or [],
            "retrait_mm": 0.0, "epaisseur_cuivre_mm": None, "pistes_libres": pistes, "type_kicad": kicad,
            "routage_permis": permis}


CONTOUR = [[0.0, 0.0], [50.0, 0.0], [50.0, 40.0], [0.0, 40.0]]


def remplissage(couche_, reseau, couverture=1.0, contour=None):
    return {"nom": f"P_{reseau}", "couche": couche_, "reseau": reseau, "contour": contour or CONTOUR, "trous": [],
            "couverture": couverture}


def regles(reseaux, largeurs=None, defaut=(0.254, 0.3, 3.0), **autres):
    """Bloc ``regles`` de entree_carte/2 ; ``largeurs`` : {réseau: [min, pref, max]} (règle de largeur propre)."""
    largeurs = largeurs or {}
    r = {"isolement": {"defaut": 0.2, "classes": ["toutes"], "matrice": [[0.2]], "classe_reference": 0},
         "reseaux": {n: {"largeurs": list(largeurs.get(n, defaut)), "via": dict(VIA),
                         "classes_isolement": {"piste": 0, "via": 0, "surface": 0}, "couches_permises": None,
                         "priorite_routage": 0, "alimentation_schema": False, "regle_largeur_propre": n in largeurs}
                     for n in reseaux},
         "largeur_defaut": list(defaut), "via": dict(VIA), "trou_trou": 0.254, "garde_bord": None,
         "isolement_pieces": None, "hauteurs": {"dessus": None, "dessous": None}, "pieces": {},
         "connexion_plans": None, "masque_serigraphie": None, "salles": [], "tolerances_longueur": {},
         "non_interpretees": [], "ignorees": []}
    r.update(autres)
    return r


def entree(pieces, couches=None, remplissages=None, largeurs=None, type_="kicad_pcb", libres=None, **carte):
    couches = couches or [couche("TopLayer", 0), couche("BottomLayer", 1)]
    nets = sorted({p["reseau"] for c in pieces for p in c["pastilles"] if p["reseau"]}
                  | {p["reseau"] for p in libres or []})
    e = {"format": "entree_carte/2",
         "source": {"fichier": "synthetique", "type": type_, "sha256": "0" * 64, "version_fichier": "1",
                    "origine_fichier_mil": None if type_ == "kicad_pcb" else [1000.0, 2000.0],
                    "origine_fichier_mm": [100.0, 80.0] if type_ == "kicad_pcb" else None,
                    "origine_cao_mil": None if type_ == "kicad_pcb" else [1500.0, 2500.0]},
         "unite": "mm",
         "carte": {"contour": [list(p) for p in CONTOUR], "boite": [0.0, 0.0, 50.0, 40.0], "aire_reference_mm2": None,
                   "decoupes": [], "trous": [], "couches": couches, "remplissages": remplissages or [],
                   "zones_interdites": [], "obstacles_cuivre": [], "cuivre_existant": [],
                   "couches_cuivre": len(couches), "noms_couches_cuivre": [c["nom"] for c in couches]},
         "composants": pieces, "pastilles_libres": libres or [], "reseaux": nets, "classes": {},
         "classes_composants": {}, "paires": [], "regles_fichier": [], "regles": regles(nets, largeurs),
         "avertissements": []}
    e["carte"].update(carte)
    return e


def base(n=6):
    """Petite carte : un circuit, ses passifs, des condensateurs vers GND, un connecteur."""
    p = [piece("U1", "Microcontroller IC", ["GND", "VCC", "A", "B", "C", "D"], x=20, y=20),
         piece("R1", "Chip Resistor", ["A", "VCC"], x=10, y=10),
         piece("R2", "Chip Resistor", ["B", "GND"], x=12, y=10),
         piece("C1", "Chip Capacitor", ["VCC", "GND"], x=14, y=10),
         piece("C2", "Chip Capacitor", ["VCC", "GND"], x=16, y=10)]
    p += [piece(f"RX{k}", "Chip Resistor", ["GND", f"N{k}"], x=5 + 2 * k, y=30) for k in range(n)]
    return p


def construire(e, ct=None, gabarit=None, config=None):
    cfg = config if config is not None else DC.deduire(e)
    return CD.construire(e, ct, cfg, gabarit, noms={"contraintes": "k.json"} if ct else None)


def comp(d, nom):
    return next(c for c in d["composants"] if c["nom"] == nom)


def res(d, nom):
    return next(r for r in d["reseaux"] if r["nom"] == nom)


def valides(test, d, x):
    test.assertEqual(formats.valider_donnees_placeur(d), [])
    test.assertEqual(formats.valider_deduction(x), [])
    json.dumps(d), json.dumps(x)


class Base(unittest.TestCase):
    def refus(self, attendu, e, ct=None, **kw):
        with self.assertRaises(ErreurFormat) as cm:
            construire(e, ct, **kw)
        self.assertIn(attendu, str(cm.exception))
        return str(cm.exception)


# ---------------------------------------------------------------------------------------------------------------------
# Contraintes : repère, unité, motifs, références
# ---------------------------------------------------------------------------------------------------------------------

class TestContraintes(Base):
    def test_repere_fichier_kicad(self):
        """KiCad : coordonnées de la page en mm, y vers le bas ; X = x − x0, Y = y0 − y."""
        e = entree(base())
        ct = {"format": "contraintes/2", "trous_supplementaires": [{"x": 125.0, "y": 50.0, "diametre": 3.2}],
              "fixes_deplaces": {"R1": [130.0, 55.0]}}
        d, x = construire(e, ct)
        t = d["trous"][-1]
        self.assertEqual((t["x"], t["y"], t["trou"]["diametre"], t["rayon_interdit_mm"]), (25.0, 30.0, 3.2, 2.2))
        self.assertEqual(comp(d, "R1")["pose_cible"][:2], [30.0, 25.0])
        self.assertEqual(x["positions_imposees"], [{"piece": "R1", "repere_fichier": [130.0, 55.0],
                                                    "repere_carte": [30.0, 25.0]}])
        valides(self, d, x)

    def test_repere_fichier_altium_et_mil(self):
        """Altium : coordonnées affichées, relatives à ORIGIN, y vers le haut ; unité mil."""
        e = entree(base(), type_="altium_pcbdoc")
        # origine commune à (1000, 2000) mil dans le fichier, origine affichée à (1500, 2500) : décalage +500 mil
        ct = {"format": "contraintes/2", "unite": "mil",
              "trous_supplementaires": [{"x": 100.0, "y": -200.0, "diametre": 125.98}],
              "zones_interdites": [{"rect": [0, 0, 100, 100]}]}
        d, _ = construire(e, ct)
        t = d["trous"][-1]
        self.assertAlmostEqual(t["x"], 600 * MIL, places=9)
        self.assertAlmostEqual(t["y"], 300 * MIL, places=9)
        self.assertAlmostEqual(t["trou"]["diametre"], 125.98 * MIL, places=9)
        self.assertEqual(d["obstacles_placement"][0]["rect"], [round(500 * MIL, 9), round(500 * MIL, 9),
                                                               round(600 * MIL, 9), round(600 * MIL, 9)])
        # repère « carte » : seule l'unité change
        d, _ = construire(e, {"format": "contraintes/2", "repere": "carte", "unite": "mil",
                              "trous_supplementaires": [{"x": 100.0, "y": 200.0, "diametre": 100}]})
        self.assertEqual((d["trous"][-1]["x"], d["trous"][-1]["y"]), (2.54, 5.08))

    def test_motif_sans_effet_et_reference_inconnue(self):
        e = entree(base())
        d, x = construire(e, {"format": "contraintes/2", "fixes": ["R*", "Q*"]})
        self.assertIn("contraintes.fixes[1] : motif « Q* » sans effet", x["avertissements"])
        self.assertEqual(comp(d, "R1")["raison_statut"], "contrainte")
        msg = self.refus("contraintes.fixes[3] : pièce C99 inconnue", e,
                         {"format": "contraintes/2", "fixes": ["R1", "R2", "C1", "C99"]})
        self.assertTrue(msg.startswith("k.json : "))
        self.refus("contraintes.reseaux.XYZ : réseau XYZ inconnu", e, {"reseaux": {"XYZ": {"poids": 2}}})
        self.refus("contraintes.couches.routage[0] : couche In9 inconnue", e, {"couches": {"routage": ["In9"]}})
        self.refus("contraintes.au_bord[0].cote : 'nord' inconnu", e, {"au_bord": [{"piece": "R1", "cote": "nord"}]})
        self.refus("contraintes.reseaux.VCC.pods : clé inconnue ; vouliez-vous dire 'poids' ?", e,
                   {"reseaux": {"VCC": {"pods": 2}}})

    def test_ligne_de_commande_reference_inconnue(self):
        with tempfile.TemporaryDirectory() as tmp:
            e = entree(base())
            chemins = {}
            for nom, doc in (("e.json", e), ("c.json", DC.deduire(e)),
                             ("k.json", {"format": "contraintes/2", "fixes": ["R1", "R2", "C1", "C99"]})):
                chemins[nom] = os.path.join(tmp, nom)
                IC.ecrire_json(chemins[nom], doc)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = CD.main([chemins["e.json"], "--config", chemins["c.json"], "--contraintes", chemins["k.json"],
                              "--sortie", os.path.join(tmp, "d.json")])
            self.assertEqual(rc, 2)
            self.assertIn("k.json : contraintes.fixes[3] : pièce C99 inconnue", err.getvalue())
            self.assertNotIn(tmp, err.getvalue())
            self.assertFalse(os.path.exists(os.path.join(tmp, "d.json")))


# ---------------------------------------------------------------------------------------------------------------------
# Couches (§3.1)
# ---------------------------------------------------------------------------------------------------------------------

def quatre_couches(mid1="In1", mid2="In2", pistes2=0, kicad=None, fills=("GND", "GND")):
    cs = [couche("TopLayer", 0), couche("MidLayer1", 1, mid1, kicad=kicad), couche("MidLayer2", 2, mid2, pistes2),
          couche("BottomLayer", 3)]
    rs = [remplissage("MidLayer1", fills[0]), remplissage("MidLayer2", fills[1])]
    return cs, rs


class TestCouches(Base):
    def roles(self, d):
        return [c["role"] for c in d["couches"]]

    def test_deux_couches(self):
        d, x = construire(entree(base()))
        self.assertEqual(self.roles(d), ["routage", "routage"])
        self.assertEqual([e["regle"] for e in x["elements"] if e["objet"].startswith("couche:")], ["R-C4", "R-C4"])

    def test_r_c3_plan_par_le_nom_et_plan_probable(self):
        cs, rs = quatre_couches("L2_GND", "In2")
        d, x = construire(entree(base(), cs, rs))
        self.assertEqual(self.roles(d), ["routage", "plan", "routage", "routage"])
        self.assertEqual(d["couches"][1]["zones_plan"], [{"reseau": "GND", "contour": CONTOUR, "trous": []}])
        self.assertEqual(res(d, "GND")["role"], "plan")
        self.assertEqual(res(d, "GND")["plan_couches"], ["MidLayer1"])
        self.assertEqual(res(d, "GND")["poids"], 0.0)
        ac = [a for a in x["a_confirmer"] if a["objet"] == "couche:MidLayer2"]
        self.assertEqual(len(ac), 1)
        self.assertIn("plan probable", ac[0]["raison"])
        el = next(e for e in x["elements"] if e["objet"] == "couche:MidLayer1")
        self.assertEqual((el["regle"], el["preuve"]), ("R-C3", "couverture 1,00, 0 piste libre, « GND » dans le nom"))
        # le remplissage d'une couche de plan n'est pas répété dans carte.remplissages (source unique)
        self.assertEqual([f["couche"] for f in d["carte"]["remplissages"]], ["MidLayer2"])
        self.assertEqual(res(d, "GND")["remplissages"], ["MidLayer2"])

    def test_r_c3_pistes_et_type_power(self):
        cs, rs = quatre_couches("GND", "PWR", pistes2=3)
        d, _ = construire(entree(base(), cs, rs))
        self.assertEqual(self.roles(d), ["routage", "plan", "routage", "routage"])
        # R-C5 : couche interne peu couverte ; R-C3 : zone coupée au contour de la carte
        cs, rs = quatre_couches("GND", "In2")
        rs[0]["contour"] = [[-5.0, -5.0], [55.0, -5.0], [55.0, 45.0], [-5.0, 45.0]]
        rs[1]["couverture"] = 0.3
        d, x = construire(entree(base(), cs, rs))
        self.assertEqual(self.roles(d), ["routage", "plan", "routage", "routage"])
        self.assertEqual(sorted(map(tuple, d["couches"][1]["zones_plan"][0]["contour"])), sorted(map(tuple, CONTOUR)))
        self.assertEqual(next(e for e in x["elements"] if e["objet"] == "couche:MidLayer2")["regle"], "R-C5")
        cs, rs = quatre_couches("In1", "In2", kicad="power", fills=("GND", "VCC"))
        cs[2]["type_kicad"] = "power"
        d, _ = construire(entree(base(), cs, rs))
        self.assertEqual(self.roles(d), ["routage", "plan", "plan", "routage"])
        self.assertEqual(res(d, "VCC")["role"], "plan")

    def test_r_c1_r_c2_r_c2c(self):
        cs = [couche("TopLayer", 0), couche("InternalPlane1", 1, "P1", type_="plan_negatif",
                                            plans=[{"reseau": "GND", "contour": CONTOUR, "trous": []}]),
              couche("InternalPlane2", 2, "P2", type_="plan_negatif"), couche("MidLayer1", 3, "Sig", permis=False),
              couche("BottomLayer", 4)]
        d, x = construire(entree(base(), cs))
        self.assertEqual(self.roles(d), ["routage", "plan", "inutilisee", "inutilisee", "routage"])
        self.assertIn("couche InternalPlane2 : plan négatif sans réseau, couche inutilisée", x["avertissements"])
        self.assertEqual([e["regle"] for e in x["elements"] if e["objet"].startswith("couche:")],
                         ["R-C4", "R-C2", "R-C2", "R-C2c", "R-C4"])
        # R-C1 : plan déclaré sans géométrie = contour moins garde_bord_mm ; couche inutilisée
        ct = {"couches": {"plans": {"MidLayer1": "VCC"}, "inutilisees": ["InternalPlane1"]}, "garde_bord_mm": 0.5}
        d, x = construire(entree(base(), cs), ct)
        self.assertEqual(self.roles(d), ["routage", "inutilisee", "inutilisee", "plan", "routage"])
        self.assertEqual(d["couches"][3]["zones_plan"][0]["contour"],
                         [[0.5, 0.5], [49.5, 0.5], [49.5, 39.5], [0.5, 39.5]])
        self.assertEqual(next(e for e in x["elements"] if e["objet"] == "couche:MidLayer1")["source"], "contrainte")

    def test_aucune_couche_de_routage(self):
        self.refus("aucune couche de routage", entree(base()),
                   {"couches": {"inutilisees": ["TopLayer"], "plans": {"BottomLayer": "GND"}}})

    def test_garde_fou_face_sans_routage(self):
        p = base()
        p[1] = piece("R1", "Chip Resistor", ["A", "VCC"], x=10, y=10, verrouille=True)
        self.refus("pièce R1 : pastille R1#0 (réseau A) sur une face sans couche de routage", entree(p),
                   {"couches": {"plans": {"TopLayer": "GND"}}, "faces_par_defaut": "libres"})
        self.refus("pièce U1 : pastille U1#1 (réseau VCC) : aucune face avec une couche de routage", entree(p),
                   {"couches": {"plans": {"TopLayer": "GND"}}})
        # mobile : la face sans routage est retirée de ses orientations
        d, _ = construire(entree(base()), {"couches": {"plans": {"TopLayer": "GND"}}, "faces_par_defaut": "libres"})
        self.assertTrue(all(o[1] for o in comp(d, "R1")["orientations"]))

    def test_r_c6_suggestion_et_r_c7(self):
        cs, rs = quatre_couches("L2_GND", "L3_+5V", pistes2=10)
        p = base() + [piece("R9", "Chip Resistor", ["+5V", "A"], x=30, y=30)]
        d, x = construire(entree(p, cs, rs))
        self.assertIsNone(res(d, "+5V")["couche_preferee"])
        sg = [s for s in x["suggestions"] if s["objet"] == "reseau:+5V"]
        self.assertEqual(len(sg), 1)
        self.assertIn("MidLayer2", sg[0]["proposition"])
        d, _ = construire(entree(p, cs, rs), {"couche_preferee_par_nom": True})
        self.assertEqual(res(d, "+5V")["couche_preferee"], "MidLayer2")
        d, _ = construire(entree(p, cs, rs), {"couche_preferee_par_nom": True,
                                              "couches": {"preferees": {"+5V": "TopLayer"}}})
        self.assertEqual(res(d, "+5V")["couche_preferee"], "TopLayer")
        self.refus("couche MidLayer1 : pas une couche de routage", entree(p, cs, rs),
                   {"couches": {"preferees": {"+5V": "MidLayer1"}}})


# ---------------------------------------------------------------------------------------------------------------------
# Masses (§3.2) et rôles (§3.3)
# ---------------------------------------------------------------------------------------------------------------------

class TestMassesEtRoles(Base):
    def test_r_m2_matrice_de_led(self):
        """Le rail +5V porte plus de pastilles que la masse ; les condensateurs départagent (R-M2)."""
        p = [piece(f"D{k}", "LED", ["+5V", f"K{k}"], x=2 + k, y=5) for k in range(30)]
        p += [piece(f"C{k}", "Chip Capacitor", ["+5V", "GND"], x=2 + k, y=15) for k in range(4)]
        p += [piece(f"C{k}", "Chip Capacitor", ["VCC", "GND"], x=2 + k, y=20) for k in range(4, 7)]
        p += [piece("U1", "LED driver IC", ["GND"] + [f"K{k}" for k in range(30)], x=10, y=30, pas=0.5)]
        d, x = construire(entree(p))
        self.assertEqual(d["masses"], ["GND"])
        self.assertTrue(res(d, "GND")["masse"])
        self.assertFalse(res(d, "+5V")["masse"])
        el = next(e for e in x["elements"] if e["objet"] == "reseau:GND" and e["propriete"] == "masse")
        self.assertEqual(el["regle"], "R-M2")
        self.assertIn("7 condensateur(s)", el["preuve"])
        # R-M1 : contrainte
        d, _ = construire(entree(p), {"roles": {"masse": ["+5V", "GND"]}})
        self.assertEqual(d["masses"], ["+5V", "GND"])
        self.refus("contraintes.roles.masse : réseau AGND inconnu", entree(p), {"roles": {"masse": "AGND"}})

    def test_roles_par_les_regles_de_largeur(self):
        """R-R5 (≥ 3 × défaut, gamme large), R-R6 (1,5 à 3), classe 50 Ω [0,3 ; 0,3 ; 0,3] → signal sensible."""
        p = base() + [piece("R7", "Chip Resistor", ["PW", "AL"], x=30, y=10),
                      piece("R8", "Chip Resistor", ["RF", "GND"], x=33, y=10)]
        e = entree(p, largeurs={"PW": [0.3, 1.0, 10.0], "AL": [0.2, 0.2, None], "RF": [0.3, 0.3, 0.3]})
        e["regles"]["largeur_defaut"] = [0.1, 0.1, 5.0]
        d, x = construire(e)
        self.assertEqual((res(d, "PW")["role"], res(d, "PW")["poids"], res(d, "PW")["largeurs_mm"]),
                         ("puissance", 3.0, [0.3, 1.0, 10.0]))
        self.assertEqual(res(d, "AL")["role"], "alimentation")
        self.assertEqual((res(d, "RF")["role"], res(d, "RF")["sensibilite"]), ("signal", "sensible"))
        self.assertEqual(res(d, "A")["largeurs_mm"], [0.254, 0.3, 3.0])          # règle Width du réseau (fichier)
        el = next(e for e in x["elements"] if e["objet"] == "reseau:PW" and e["propriete"] == "role")
        self.assertEqual((el["regle"], el["preuve"], el["source"]),
                         ("R-R5", "largeur préférée 1 = 10,00 × défaut 0,1, maximum 10", "fichier"))
        self.assertEqual(next(e for e in x["elements"] if e["objet"] == "reseau:RF" and
                              e["propriete"] == "sensibilite")["regle"], "R-S3")

    def test_r_r5b_r_r6b_et_surcharges(self):
        e = entree(base())
        cfg = DC.deduire(e)
        cfg["convertisseurs"] = [{
            "nom": "abaisseur VCC -> A", "topologie": "abaisseur", "commutation": "integre", "haut": "U1",
            "bas": "U1", "pilote": "U1", "inductance": "R1", "cond_entree": ["C1"], "cond_sortie": [], "cond_bulk": [],
            "cond_pilote": [], "cond_bootstrap": [], "reseau_entree": "VCC", "reseau_sortie": "A",
            "reseau_masse": "GND", "reseau_commutation": "B", "reseaux_courts": [], "reseaux_sensibles": ["C"],
            "boucle_chaude": {"condensateurs": ["C1"], "reseau_rail": "VCC", "reseau_masse": "GND"},
            "origine": "impose"}]
        d, _ = CD.construire(e, None, cfg)
        self.assertEqual((res(d, "VCC")["role"], res(d, "VCC")["largeurs_mm"]), ("puissance", [0.254, 0.9, 3.0]))
        self.assertEqual((res(d, "B")["role"], res(d, "B")["poids"]), ("commutation", 3.0))
        self.assertEqual(d["convertisseurs"], cfg["convertisseurs"])
        # R-R6b : SupplyNets sur une carte sans règle de largeur propre
        e["regles"]["reseaux"]["D"]["alimentation_schema"] = True
        d, _ = construire(e)
        self.assertEqual(res(d, "D")["role"], "alimentation")
        e2 = copy.deepcopy(e)
        e2["regles"]["reseaux"]["N0"]["regle_largeur_propre"] = True
        d, _ = construire(e2)
        self.assertEqual(res(d, "D")["role"], "signal")
        # R-R1, R-R2, R-S2, poids et priorité
        ct = {"roles": {"puissance": ["N*"], "sensible": ["A"], "peu_sensible": ["Z*"]},
              "reseaux": {"N0": {"role": "signal", "poids": 7, "priorite": 0, "largeurs_mm": [0.2, 0.4, None]}}}
        d, x = construire(entree(base()), ct)
        self.assertEqual((res(d, "N0")["role"], res(d, "N0")["poids"], res(d, "N0")["priorite"],
                          res(d, "N0")["largeurs_mm"]), ("signal", 7.0, 0, [0.2, 0.4, None]))
        self.assertEqual((res(d, "N1")["role"], res(d, "A")["sensibilite"]), ("puissance", "sensible"))
        self.assertIn("contraintes.roles.peu_sensible[0] : motif « Z* » sans effet", x["avertissements"])

    def test_isolement_classes_et_via(self):
        e = entree(base())
        e["regles"]["isolement"] = {"defaut": 0.2, "classes": ["c0", "c1"], "matrice": [[0.25, 0.25], [0.25, 0.2]],
                                    "classe_reference": 1}
        for n, r in e["regles"]["reseaux"].items():
            k = 0 if n == "A" else 1
            r["classes_isolement"] = {"piste": k, "via": k, "surface": k}
        e["regles"]["reseaux"]["A"]["via"] = {"diametre": 0.8, "trou": 0.4, "diametre_min": 0.7, "diametre_max": None,
                                              "trou_min": 0.3, "trou_max": None}
        e["regles"]["reseaux"]["A"]["couches_permises"] = ["BottomLayer"]
        d, _ = construire(e)
        self.assertEqual((res(d, "A")["isolement_mm"], res(d, "B")["isolement_mm"]), (0.25, 0.2))
        self.assertEqual(res(d, "A")["via"]["diametre"], 0.8)
        self.assertEqual(res(d, "A")["couches_permises"], ["BottomLayer"])
        self.assertEqual(d["regles"]["isolement"]["classe_reference"], 1)


# ---------------------------------------------------------------------------------------------------------------------
# Pièces (§3.5)
# ---------------------------------------------------------------------------------------------------------------------

def bga(des, n=4, pas=0.8, bille=0.4):
    pads = []
    for i in range(n):
        for j in range(n):
            k = i * n + j
            pads.append(pastille(des, k, "GND" if (i + j) % 2 else f"S{k}", x=i * pas, y=j * pas, l=bille, h=bille,
                                 forme="rond"))
    c = piece(des, "FPGA IC", [], x=25, y=20)
    c["pastilles"], c["boite"] = pads, [-0.5, -0.5, (n - 1) * pas + 0.5, (n - 1) * pas + 0.5]
    return c


class TestPieces(Base):
    def test_r_p2_pas_fin(self):
        p = base() + [piece("U2", "Op amp IC", ["A", "B", "C"], x=30, y=30, pas=0.5),
                      piece("C9", "Chip Capacitor", ["A", "GND"], x=35, y=30, pas=0.3)]
        d, x = construire(entree(p))
        self.assertEqual((comp(d, "U2")["pas_fin"], comp(d, "U2")["pas_min_mm"]), (True, 0.5))
        self.assertEqual((comp(d, "C9")["pas_fin"], comp(d, "C9")["pas_min_mm"]), (False, 0.3))   # deux pastilles
        self.assertFalse(comp(d, "U1")["pas_fin"])
        # isolement relâché entre les pastilles d'une pièce (règle de pièce)
        e = entree(p)
        e["regles"]["isolement"] = {"defaut": 0.2, "classes": ["a", "b"], "matrice": [[0.2, 0.2], [0.2, 0.1]],
                                    "classe_reference": 0}
        for q in next(c for c in e["composants"] if c["designateur"] == "U1")["pastilles"]:
            q["classe_isolement"] = 1
        d, x = construire(e)
        self.assertTrue(comp(d, "U1")["pas_fin"])
        self.assertIn("isolement relâché", next(e for e in x["elements"] if e["objet"] == "piece:U1"
                                                 and e["propriete"] == "pas_fin")["preuve"])

    def test_r_p2b_matrice(self):
        d, x = construire(entree(base() + [bga("U5", pas=1.0)]))
        self.assertEqual((comp(d, "U5")["matrice"], comp(d, "U5")["pas_fin"]), (True, True))
        self.assertTrue(any("matrice de billes U5 : éventail en os de chien" in a for a in x["avertissements"]))
        self.refus("matrice de billes U6 : via trop gros pour l'éventail (pas 0,5, bille 0,3, via 0,5, isolement 0,2)",
                   entree(base() + [bga("U6", pas=0.5, bille=0.3)]))

    def test_r_p3_raisons(self):
        p = base()
        p += [piece("J1", "Connector header", ["A", "B"], x=25, y=35, pas=2.54),
              piece("J2", "Connector header", ["C", "D"], x=60, y=60, pas=2.54),
              piece("H1", "Mounting hole", ["GND"], x=45, y=35, trou=3.2, l=6.0, genre="mecanique"),
              piece("Q1", "MOSFET", ["A", "B", "C"], x=30, y=15, verrouille=True),
              piece("R5", "Chip Resistor", ["C", "D"], x=49.4, y=20),
              piece("LOGO", "", [], x=40, y=5, genre="graphique"),
              piece("K1", "Relay", ["A", "D"], x=8, y=20)]
        p[-1]["decoupes"] = ["D1"]
        libres = [{"id": "__libre_1", "nom": "GND", "reseau": "GND", "x": 3.0, "y": 37.0, "forme": "rond",
                   "largeur": 0.6, "hauteur": 0.6, "rotation": 0.0, "rayon_coin": 0.0, "points": [],
                   "couches": ["TopLayer"], "face": "dessus", "trou": None, "traversante": False,
                   "expansion_masque": 0.0, "pile": None, "via_dans_pastille_permis": False, "classe_isolement": 0}]
        e = entree(p, libres=libres, decoupes=[{"id": "D1", "points": [[5, 18], [7, 18], [7, 22], [5, 22]],
                                                "proprietaire": "K1"}])
        d, x = construire(e, {"fixes": ["R2"]})
        raisons = {c["nom"]: c["raison_statut"] for c in d["composants"]}
        self.assertEqual({k: raisons[k] for k in ("R2", "LOGO", "__libre_1", "H1", "K1", "Q1", "R5", "J1", "J2",
                                                  "R1")},
                         {"R2": "contrainte", "LOGO": "sans pastille", "__libre_1": "virtuel", "H1": "mecanique",
                          "K1": "decoupe", "Q1": "verrouillee", "R5": "bord", "J1": "suggestion", "J2": "libre",
                          "R1": "libre"})
        self.assertEqual(comp(d, "J1")["orientations"], [])
        self.assertTrue(comp(d, "__libre_1")["virtuel"])
        self.assertEqual(comp(d, "__libre_1")["pastilles"][0]["id"], "__libre_1#0")
        self.assertEqual(comp(d, "__libre_1")["pose_cible"], [3.0, 37.0, 0.0, False])
        self.assertTrue(any(t["piece"] == "H1" and t["trou"]["diametre"] == 3.2 for t in d["trous"]))
        valides(self, d, x)
        # liberer annule mecanique, verrouillee, bord et suggestion, pas decoupe
        d, _ = construire(e, {"liberer": ["H1", "Q1", "R5", "J1", "K1"]})
        self.assertEqual([comp(d, k)["raison_statut"] for k in ("H1", "Q1", "R5", "J1", "K1")],
                         ["libre", "libre", "libre", "libre", "decoupe"])
        d, _ = construire(e, {"fixer_suggestions": False, "fixer_verrouilles": False})
        self.assertEqual((comp(d, "J1")["statut"], comp(d, "Q1")["statut"]), ("mobile", "mobile"))
        # refus
        self.refus("contraintes.fixes[0] : pièce J2 entièrement hors du contour, fixée à sa pose d'entrée", e,
                   {"fixes": ["J2"]})
        self.refus("contraintes.fixes_deplaces.K1 : pièce K1 propriétaire de la découpe D1", e,
                   {"fixes_deplaces": {"K1": [10.0, 10.0]}})

    def test_conflit_fixes_deplaces(self):
        p = base() + [piece("J1", "Connector header", ["A", "B"], x=25, y=35, pas=2.54)]
        e = entree(p)
        msg = self.refus("contraintes.fixes_deplaces.R1 : conflit entre R1 et la pièce fixe J1", e,
                         {"repere": "carte", "fixes_deplaces": {"R1": [26.0, 35.0]}})
        self.assertIn("boîtes", msg)
        self.refus("pastilles R1#1 et J1#1", e, {"repere": "carte",
                                                "fixes_deplaces": {"R1": {"x": 26.54, "y": 35.7, "rotation": 0}},
                                                "boites": {"R1": [0, 0, 0.1, 0.1]}})
        self.refus("boîte hors du contour", e, {"repere": "carte", "fixes_deplaces": {"R1": [49.9, 20.0]}})
        d, _ = construire(e, {"repere": "carte",
                              "fixes_deplaces": {"R1": {"x": 20.0, "y": 30.0, "rotation": 90, "face": "dessous"}}})
        self.assertEqual(comp(d, "R1")["pose_cible"], [20.0, 30.0, 90.0, True])
        self.assertEqual(comp(d, "R1")["pose_actuelle"], [10.0, 10.0, 0.0, False])

    def test_r_p5_faces(self):
        p = base()
        p[3]["face"] = "dessous"                                     # C1 dessous : la carte n'est pas simple face
        for q in p[3]["pastilles"]:
            q["couches"] = ["BottomLayer"]
        p += [piece("U3", "Regulator IC", ["VCC", "GND", "A"], x=30, y=20, hauteur=0.0),
              piece("U4", "Regulator IC", ["VCC", "GND", "B"], x=35, y=20, hauteur=5.0),
              piece("C5", "Aluminum electrolytic capacitor", ["A", "B"], x=30, y=30, trou=1.0, l=1.6)]
        e = entree(p)
        d, _ = construire(e, gabarit={"non_retournables": {"U1": "couche mécanique non appairée"}})
        self.assertEqual(comp(d, "U3")["orientations"], [[0, False], [90, False], [180, False], [270, False]])
        self.assertIn("hauteur inconnue", comp(d, "U3")["regle"])
        self.assertEqual({o[1] for o in comp(d, "U4")["orientations"]}, {False})    # 5 mm > 3 mm dessous
        self.assertEqual({o[1] for o in comp(d, "C5")["orientations"]}, {False})    # traversante : face d'entrée
        self.assertEqual({o[1] for o in comp(d, "U1")["orientations"]}, {False})
        self.assertIn("gabarit : couche mécanique non appairée", comp(d, "U1")["regle"])
        self.assertEqual(len(comp(d, "R1")["orientations"]), 8)
        d, _ = construire(e, {"hauteur_inconnue": "basse", "rotations_dessous_multibroches": [0, 180],
                              "orientations": {"R2": [[45, False], [135.5, True]]},
                              "dessous_seulement": ["nature:condensateur"]})
        self.assertEqual(comp(d, "U3")["orientations"], [[0, False], [90, False], [180, False], [270, False],
                                                         [0, True], [180, True]])
        self.assertEqual(comp(d, "R2")["orientations"], [[45, False], [135.5, True]])
        self.assertEqual({o[1] for o in comp(d, "C2")["orientations"]}, {True})
        # faces « auto » : toutes les CMS sur une face → face d'entrée seulement
        d, x = construire(entree(base()))
        self.assertEqual({o[1] for o in comp(d, "R1")["orientations"]}, {False})
        self.assertEqual(d["parametres_deduction"]["faces_par_defaut_effectif"], "actuelle")
        self.refus("pièce R1 à la fois dans dessus_seulement et dessous_seulement", e,
                   {"dessus_seulement": ["R1"], "dessous_seulement": ["R*"]})

    def test_r_p5b_r_p6_r_p7(self):
        e = entree(base(), largeurs=None)
        cfg = DC.deduire(e)
        cfg["convertisseurs"] = [{
            "nom": "x", "topologie": "abaisseur", "commutation": "integre", "haut": "U1", "bas": "U1",
            "pilote": "U1", "inductance": "R1", "cond_entree": ["C1"], "cond_sortie": [], "cond_bulk": [],
            "cond_pilote": [], "cond_bootstrap": [], "reseau_entree": "VCC", "reseau_sortie": "A",
            "reseau_masse": "GND", "reseau_commutation": "B", "reseaux_courts": ["D"], "reseaux_sensibles": [],
            "boucle_chaude": {"condensateurs": ["C1"], "reseau_rail": "VCC", "reseau_masse": "GND"},
            "origine": "impose"}]
        e["composants"].append(piece("R6", "Chip Resistor", ["D", "GND"], x=40, y=10))
        d, x = CD.construire(e, {"faces_par_defaut": "libres", "via_sous_cms_permis": ["C2"]}, cfg)
        for k in ("U1", "R1", "C1", "R6"):
            self.assertEqual(comp(d, k)["orientations"], [[0, False], [90, False], [180, False], [270, False]], k)
            self.assertEqual(comp(d, k)["regle"], "convertisseur : face d'entree")
        self.assertEqual(len(comp(d, "C2")["orientations"]), 8)
        self.assertTrue(all(p["via_dans_pastille_permis"] for p in comp(d, "C2")["pastilles"]))
        self.assertFalse(any(p["via_dans_pastille_permis"] for p in comp(d, "C1")["pastilles"]))
        dec = {x["comp"]: x for x in d["decouplages"]}
        self.assertEqual(dec["C1"], {"comp": "C1", "pastille": "C1#0", "reseau": "VCC", "broches_ci": ["U1#1"]})
        self.assertNotIn("R2", dec)
        p = comp(d, "C1")["pastilles"][0]
        self.assertEqual((p["lx"], p["ly"], p["hw"], p["hh"], p["th"]), (0.0, 0.0, 0.3, 0.3, False))


# ---------------------------------------------------------------------------------------------------------------------
# Groupes (§3.6) et liens
# ---------------------------------------------------------------------------------------------------------------------

def canaux(mode):
    """Deux canaux identiques ; ``mode`` = « hierarchie » (champ canal) ou « motif » (suffixes _CH1, _CH2)."""
    p = []
    for k in (1, 2):
        sfx = f"_CH{k}"
        for base_, desc, nets in (("U1", "Op amp IC", [f"IN{sfx}", f"OUT{sfx}", "GND"]),
                                  ("R1", "Chip Resistor", [f"IN{sfx}", f"OUT{sfx}"]),
                                  ("C1", "Chip Capacitor", [f"OUT{sfx}", "GND"])):
            c = piece(base_ + (sfx if mode == "motif" else f"_{k}"), desc, nets, x=10 * k, y=10, empreinte=base_)
            if mode == "hierarchie":
                c["canal"] = {"groupe": base_, "instance": f"/feuille/canal{k}/"}
            p.append(c)
    return p + [piece(f"CX{k}", "Chip Capacitor", ["VCC", "GND"], x=40, y=5 + k) for k in range(3)]


class TestGroupes(Base):
    def test_fonctionnel(self):
        d, x = construire(entree(base()))
        g = [g for g in d["groupes"] if g["type"] == "fonctionnel"]
        self.assertEqual(g, [{"type": "fonctionnel", "nom": "fonctionnel_U1", "pieces": ["U1", "R1", "R2", "C1", "C2"],
                              "rigide": False}])

    def test_repete_hierarchie_et_motif(self):
        for mode in ("hierarchie", "motif"):
            d, _ = construire(entree(canaux(mode)))
            g = [g for g in d["groupes"] if g["type"] == "repete"]
            self.assertEqual(len(g), 1, mode)
            g = g[0]
            s = "_CH" if mode == "motif" else "_"
            self.assertEqual((g["source"], len(g["exemplaires"])), (mode, 2))
            self.assertEqual(g["correspondance_pieces"], [[f"U1{s}1", f"U1{s}2"], [f"R1{s}1", f"R1{s}2"],
                                                          [f"C1{s}1", f"C1{s}2"]])
            self.assertEqual(g["correspondance_reseaux"], [["IN_CH1", "IN_CH2"], ["OUT_CH1", "OUT_CH2"],
                                                           ["GND", "GND"]])
            if mode == "motif":
                self.assertEqual((g["nom"], g["famille"], [e["nom"] for e in g["exemplaires"]]),
                                 ("_CH", "_CH", ["CH1", "CH2"]))
            else:
                self.assertEqual((g["nom"], g["famille"], [e["nom"] for e in g["exemplaires"]]),
                                 ("_canal", "_canal", ["canal1", "canal2"]))

    def test_impose_et_exclus(self):
        ct = {"groupes": [{"nom": "entree", "pieces": ["R1", "C1"], "rigide": True}],
              "groupes_exclus": ["fonctionnel_U1", "absent"]}
        d, x = construire(entree(base()), ct)
        self.assertEqual(d["groupes"], [{"type": "impose", "nom": "entree", "pieces": ["R1", "C1"], "rigide": True}])
        self.assertIn("contraintes.groupes_exclus[1] : groupe absent inconnu (sans effet)", x["avertissements"])
        self.refus("contraintes.groupes[0].pieces[1] : pièce C99 inconnue", entree(base()),
                   {"groupes": [{"nom": "g", "pieces": ["R1", "C99"]}]})

    def test_eloignements_charges_au_bord(self):
        p = base()
        p[0]["pastilles"][2]["nom"] = p[0]["pastilles"][3]["nom"] = "IO"
        ct = {"eloignements": [{"type": "dure", "objets": ["U1.IO", "R1"], "reference": ["C1"], "distance_mm": 30},
                               {"type": "souple", "objets": ["R2#1"], "reference": ["C1", "C2"], "distance_mm": 10,
                                "poids_par_mm": 2}],
              "charges": [{"broche": "U1.2", "courant_a": 1.5}],
              "au_bord": [{"piece": "R1", "cote": "gauche"}]}
        d, x = construire(entree(p), ct)
        self.assertEqual(d["eloignements"], [
            {"type": "dure", "objets": ["U1#2", "U1#3", "R1"], "reference": ["C1"], "distance_mm": 30.0,
             "poids_par_mm": 300.0, "marge_mm": 1.0},
            {"type": "souple", "objets": ["R2#1"], "reference": ["C1", "C2"], "distance_mm": 10.0,
             "poids_par_mm": 2.0, "marge_mm": 0.0}])
        self.assertTrue(any("U1.IO désigne 2 pastilles" in a for a in x["avertissements"]))
        self.assertEqual(d["charges"], [{"broche": "U1.2", "pastilles": ["U1#1"], "courant_a": 1.5}])
        self.assertEqual(d["au_bord"], [{"piece": "R1", "cote": "gauche", "debord_mm": 0.0}])
        self.refus("contraintes.eloignements[0].objets[0] : broche 99 inconnue sur la pièce U1", entree(p),
                   {"eloignements": [{"type": "dure", "objets": ["U1.99"], "reference": ["C1"], "distance_mm": 3}]})
        # éloignement d'un config_carte v1 (champ ajouté par deduire_config)
        e = entree(p)
        cfg = DC.deduire(e)
        cfg["eloignements"] = [{"type": "dure", "objets": ["U1.IO"], "reference": ["C2"], "distance_mm": 5.0}]
        d, _ = CD.construire(e, None, cfg)
        self.assertEqual(d["eloignements"][0]["objets"], ["U1#2", "U1#3"])

    def test_zones_et_cuivre(self):
        e = entree(base())
        e["carte"]["zones_interdites"] = [
            {"id": "Z1", "couches": ["toutes"], "forme": "polygone", "points": [[1, 1], [3, 1], [3, 3], [1, 3]],
             "largeur": 0.0, "effets": ["pistes", "placement"], "source": "keepout"}]
        e["carte"]["cuivre_existant"] = [
            {"id": "E1", "type": "piste", "reseau": "GND", "couches": ["TopLayer"], "verrouille": True,
             "x1": 1, "y1": 1, "x2": 5, "y2": 1, "largeur": 0.3},
            {"id": "E3", "type": "piste", "reseau": "GND", "couches": ["TopLayer"], "verrouille": True,
             "x1": 5, "y1": 1, "x2": 5, "y2": 5, "largeur": 0.3},
            {"id": "E2", "type": "via", "reseau": "VCC", "couches": ["TopLayer", "BottomLayer"], "verrouille": False,
             "x": 8.0, "y": 8.0, "diametre": 0.6, "trou": 0.3}]
        e["carte"]["obstacles_cuivre"] = [{"id": "O1", "type": "texte", "couches": ["TopLayer"],
                                           "points": [[30, 1], [32, 1], [32, 2], [30, 2]]}]
        ct = {"repere": "carte", "cuivre_a_garder": [{"reseaux": ["GND"], "verrouilles_seulement": True}],
              "zones_imposees": [{"pieces": ["R*"], "rect": [0, 0, 20, 20]}],
              "zones_interdites": [{"polygone": [[40, 30], [45, 30], [45, 35]], "effets": ["vias"],
                                    "etiquette": "antenne"}],
              "zones_autour": [{"designateurs": ["R2"], "face": "dessus", "demi_cote_mm": 2}], "fixes": ["R2"]}
        d, x = construire(e, ct)
        self.assertEqual([(f["id"], f["type"], f["reseau"]) for f in d["cuivre_fixe"]],
                         [("F1", "region", ""), ("F2", "piste", "GND"), ("F3", "piste", "GND")])
        self.assertEqual((d["cuivre_fixe"][1]["x1"], d["cuivre_fixe"][2]["y2"]), (1, 5))
        self.assertEqual([z["etiquette"] for z in d["zones_routage_interdites"]], ["antenne", "keep-out Z1"])
        self.assertEqual(d["zones_routage_interdites"][0]["effets"], ["vias"])
        self.assertEqual([o["etiquette"] for o in d["obstacles_placement"]], ["zone autour de R2", "keep-out Z1"])
        self.assertEqual(d["zones_imposees"][0]["pieces"], ["R1", "R2"] + [f"RX{k}" for k in range(6)])
        valides(self, d, x)

    def test_zone_autour_reduite_par_piece_fixe_deplacee(self):
        # R1 figée (fixes_deplaces) dans la zone autour de R2 : la zone est réduite, sinon toute graine est invalide
        ct = {"repere": "carte", "fixes": ["R2"], "fixes_deplaces": {"R1": [12.0, 16.0]},
              "zones_autour": [{"designateurs": ["R2"], "face": "dessus", "demi_cote_mm": 5}]}
        d, x = construire(entree(base()), ct)
        z = [o for o in d["obstacles_placement"] if o["etiquette"] == "zone autour de R2"][0]["rect"]
        r1 = [c for c in d["composants"] if c["nom"] == "R1"][0]
        haut_r1 = min(r1["pose_cible"][1] + v for v in (r1["lbb"][1], -r1["lbb"][3]))
        self.assertLess(z[3], haut_r1 - 2 * d["regles"]["placement"]["marge_piece_mm"])
        self.assertEqual((z[0], z[1], z[2]), (7.5, 5.0, 17.5))
        self.assertTrue(any("réduite" in a for a in x["avertissements"]))
        valides(self, d, x)


# ---------------------------------------------------------------------------------------------------------------------
# Cartes KiCad d'essai (importées par importer_carte) et ligne de commande
# ---------------------------------------------------------------------------------------------------------------------

RESEAUX_KICAD = '(net 0 "") (net 1 "GND") (net 2 "VCC") (net 3 "SIG") (net 4 "OUT")'
COUCHES_KICAD_2 = '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))'
COUCHES_KICAD_4 = ('(layers (0 "F.Cu" signal) (1 "In1.Cu" power) (2 "In2.Cu" power) (31 "B.Cu" signal) '
                   '(44 "Edge.Cuts" user))')
PIECES_KICAD = '''
  (footprint "Lib:SOIC-8" (layer "F.Cu") (at 115 60) (property "Reference" "U1") (descr "Op amp IC")
    (pad "1" smd rect (at -2.7 -1.905) (size 1.5 0.6) (layers "F.Cu") (net 3 "SIG"))
    (pad "2" smd rect (at -2.7 -0.635) (size 1.5 0.6) (layers "F.Cu") (net 1 "GND"))
    (pad "3" smd rect (at -2.7 0.635) (size 1.5 0.6) (layers "F.Cu") (net 4 "OUT"))
    (pad "4" smd rect (at -2.7 1.905) (size 1.5 0.6) (layers "F.Cu") (net 2 "VCC")))
  (footprint "Lib:R_0603" (layer "F.Cu") (at 125 60) (property "Reference" "R1") (descr "Chip Resistor")
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 3 "SIG"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 4 "OUT")))
  (footprint "Lib:C_0603" (layer "F.Cu") (at 125 65) (property "Reference" "C1") (descr "Chip Capacitor")
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 2 "VCC"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 1 "GND")))
  (footprint "Lib:Conn" (layer "F.Cu") (at 140 70) (property "Reference" "J1") (descr "Connector header")
    (pad "1" thru_hole rect (at 0 0) (size 1.7 1.7) (drill 1.0) (layers "*.Cu") (net 2 "VCC"))
    (pad "2" thru_hole circle (at 2.54 0) (size 1.7 1.7) (drill 1.0) (layers "*.Cu") (net 1 "GND")))
'''
ZONES_KICAD_4 = '''
  (zone (net 1) (net_name "GND") (layer "In1.Cu") (hatch edge 0.5)
    (polygon (pts (xy 100 50) (xy 150 50) (xy 150 80) (xy 100 80))))
  (zone (net 2) (net_name "VCC") (layer "In2.Cu") (hatch edge 0.5)
    (polygon (pts (xy 100 50) (xy 150 50) (xy 150 80) (xy 100 80))))
'''


def kicad(couches, corps):
    return (f'(kicad_pcb (version 20221018) (generator "pcbnew")\n  (general (thickness 1.6))\n  {couches}\n'
            f'  (setup (pad_to_mask_clearance 0.05))\n  {RESEAUX_KICAD}\n'
            f'  (gr_rect (start 100 50) (end 150 80) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))\n'
            f'{corps}\n)\n')


class TestCartesKicad(unittest.TestCase):
    def lancer(self, mod, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def chaine(self, couches, corps):
        with tempfile.TemporaryDirectory() as tmp:
            j = lambda n: os.path.join(tmp, n)  # noqa: E731
            with open(j("carte.kicad_pcb"), "w", encoding="utf-8", newline="\n") as f:
                f.write(kicad(couches, corps))
            for mod, args in ((IC, [j("carte.kicad_pcb"), "--entree", j("e.json")]),
                              (DC, [j("e.json"), "--sortie", j("c.json")]),
                              (CD, [j("e.json"), "--config", j("c.json"), "--sortie", j("d.json"), "--deduction",
                                    j("x.json")]),
                              (formats, ["valider", j("d.json")]), (formats, ["valider", j("x.json")])):
                rc, out, err = self.lancer(mod, *args)
                self.assertEqual(rc, 0, f"{mod.__name__} : {err}")
                self.assertNotIn(tmp, out + err)
            with open(j("d.json"), encoding="utf-8") as f:
                d = json.load(f)
            with open(j("x.json"), encoding="utf-8") as f:
                x = json.load(f)
            self.assertNotIn(tmp, json.dumps(d) + json.dumps(x))
            return d, x

    def test_deux_couches(self):
        d, x = self.chaine(COUCHES_KICAD_2, PIECES_KICAD)
        self.assertEqual([c["role"] for c in d["couches"]], ["routage", "routage"])
        self.assertEqual(d["masses"], ["GND"])
        self.assertEqual(d["regles"]["placement"]["grille_mm"], 0.05)
        self.assertEqual(comp(d, "J1")["raison_statut"], "suggestion")
        self.assertEqual(d["source"]["entree"], "e.json")

    def test_quatre_couches_plans_power(self):
        d, x = self.chaine(COUCHES_KICAD_4, PIECES_KICAD + ZONES_KICAD_4)
        self.assertEqual([c["role"] for c in d["couches"]], ["routage", "plan", "plan", "routage"])
        self.assertEqual([[z["reseau"] for z in c["zones_plan"]] for c in d["couches"]], [[], ["GND"], ["VCC"], []])
        self.assertEqual((res(d, "GND")["role"], res(d, "VCC")["role"], res(d, "SIG")["role"]),
                         ("plan", "plan", "signal"))
        el = next(e for e in x["elements"] if e["objet"] == "couche:MidLayer1")
        self.assertEqual((el["regle"], el["preuve"]), ("R-C3", "couverture 1,00, 0 piste libre, type KiCad power"))


class TestCorrections(Base):
    """Corrections de B-ET4 : coupe exacte des zones de plan, contrôle du schéma de l'entrée, unité mil."""

    def test_zone_coupee_carte_non_convexe_avec_trou(self):
        # carte en L : le remplissage qui déborde est coupé exactement, son trou est gardé
        L = [[0.0, 0.0], [50.0, 0.0], [50.0, 20.0], [20.0, 20.0], [20.0, 40.0], [0.0, 40.0]]
        cs, rs = quatre_couches("GND", "In2")
        rs[0]["contour"] = [[-5.0, -5.0], [55.0, -5.0], [55.0, 45.0], [-5.0, 45.0]]
        rs[0]["trous"] = [[[5.0, 5.0], [5.0, 8.0], [8.0, 8.0], [8.0, 5.0]]]
        e = entree(base(), cs, rs, contour=L)
        d, x = construire(e)
        zones = d["couches"][1]["zones_plan"]
        self.assertEqual(len(zones), 1)
        self.assertEqual(sorted(map(tuple, zones[0]["contour"])), sorted(map(tuple, L)))
        self.assertEqual(len(zones[0]["trous"]), 1)
        self.assertEqual(sorted(map(tuple, zones[0]["trous"][0])), [(5.0, 5.0), (5.0, 8.0), (8.0, 5.0), (8.0, 8.0)])
        self.assertFalse([a for a in x["avertissements"] if "non convexe" in a])
        valides(self, d, x)

    def test_zone_coupee_en_deux_morceaux(self):
        # carte en U : un remplissage horizontal qui traverse l'échancrure donne deux zones
        U = [[0.0, 0.0], [50.0, 0.0], [50.0, 40.0], [30.0, 40.0], [30.0, 10.0], [20.0, 10.0], [20.0, 40.0],
             [0.0, 40.0]]
        cs, rs = quatre_couches("GND", "In2")
        rs[0]["contour"] = [[-5.0, 20.0], [55.0, 20.0], [55.0, 30.0], [-5.0, 30.0]]
        d, _ = construire(entree(base(), cs, rs, contour=U))
        zones = d["couches"][1]["zones_plan"]
        self.assertEqual(sorted(sorted(map(tuple, z["contour"])) for z in zones),
                         [[(0.0, 20.0), (0.0, 30.0), (20.0, 20.0), (20.0, 30.0)],
                          [(30.0, 20.0), (30.0, 30.0), (50.0, 20.0), (50.0, 30.0)]])

    def test_schema_entree_champ_manquant(self):
        e = entree(base())
        e["carte"]["trous"] = [{"id": "T1", "x": 1.0, "y": 1.0, "rotation": 0.0, "metallise": False,
                                "trou": {"diametre": 3.2, "forme": "rond", "longueur": 0.0, "rotation": 0.0,
                                         "decalage": [0.0, 0.0], "metallise": False}, "couches": []}]
        self.refus("carte.trous[0].diametre_cuivre : champ manquant", e)
        e = entree(base())
        e["composants"][0]["pastilles"][0]["couches"] = ["Inconnue"]
        self.refus("composants[0].pastilles[0].couches[0] : couche Inconnue inconnue", e)

    def test_mil_et_cles_suffixees_mm(self):
        _, x = construire(entree(base()), {"format": "contraintes/2", "unite": "mil", "garde_bord_mm": 0.5})
        self.assertTrue(any("clés suffixées _mm (garde_bord_mm)" in a for a in x["avertissements"]))


# ---------------------------------------------------------------------------------------------------------------------
# Cartes réelles (§3.7, §3.9) et corpus du lot E (cartes « déduction »), par la chaîne complète en ligne de commande
# ---------------------------------------------------------------------------------------------------------------------

DEPOT = os.path.dirname(os.path.dirname(os.path.dirname(ICI)))
REV3 = os.path.join(DEPOT, "PCB_METROID_V2_rev3.PcbDoc")
CONTRAINTES_METROID = os.path.join(ICI, "exemples", "metroid_v2", "contraintes.json")
CORPUS_CARTES = os.path.join(ICI, "corpus", "cartes")
CARTES_LOCALES = None          # option --cartes-locales DOSSIER (lancement en script)
SL1 = "SL1 Xilinx Spartan-IIE PQ208 Rev1.02.PcbDoc"
WIFI = "WiFi.PcbDoc"


def _lancer(mod, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = mod.main(list(args))
    return rc, out.getvalue(), err.getvalue()


def chaine_fichier(test, carte, contraintes=None):
    """import, deduire_config, construire_donnees et formats.py valider (code 0 partout) ; rend (e, c, d, x)."""
    with tempfile.TemporaryDirectory() as tmp:
        j = lambda n: os.path.join(tmp, n)  # noqa: E731
        k = ["--contraintes", contraintes] if contraintes else []
        for mod, args in ((IC, [carte, "--entree", j("e.json")]), (DC, [j("e.json"), "--sortie", j("c.json")] + k),
                          (CD, [j("e.json"), "--config", j("c.json"), "--sortie", j("d.json"), "--deduction",
                                j("x.json")] + k),
                          (formats, ["valider", j("e.json")]), (formats, ["valider", j("c.json")]),
                          (formats, ["valider", j("d.json")]), (formats, ["valider", j("x.json")])):
            rc, out, err = _lancer(mod, *args)
            test.assertEqual(rc, 0, f"{mod.__name__} {os.path.basename(args[0])} : {err[-2000:]}")
        r = []
        for n in ("e.json", "c.json", "d.json", "x.json"):
            with open(j(n), encoding="utf-8") as f:
                r.append(json.load(f))
        return r


def _iso(d, a, b, genre="piste"):
    m = d["regles"]["isolement"]["matrice"]
    return m[res(d, a)["classes_isolement"][genre]][res(d, b)["classes_isolement"][genre]]


def _fixes(d):
    return {c["nom"]: c["raison_statut"] for c in d["composants"] if c["statut"] == "fixe"}


@unittest.skipUnless(os.path.exists(REV3), "PCB_METROID_V2_rev3.PcbDoc absent de la racine du dépôt")
class TestMetroid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e, cls.c, cls.d, cls.x = chaine_fichier(cls(), REV3, CONTRAINTES_METROID)

    def test_regles_3_7(self):
        d, iso = self.d, self.d["regles"]["isolement"]
        self.assertEqual(len(iso["classes"]), 3)
        self.assertEqual(len(iso["matrice"]), 3)
        self.assertAlmostEqual(iso["defaut"], 0.2, places=5)
        for r in d["reseaux"]:
            self.assertAlmostEqual(r["isolement_mm"], 0.25 if r["nom"] == "NetC19_1" else 0.2, places=5, msg=r["nom"])
        permis = {p["id"].split("#")[0] for c in d["composants"] for p in c["pastilles"]
                  if p["via_dans_pastille_permis"] and not p["traversante"]}
        self.assertEqual(permis, {"L1", "U10"})
        largeurs = {"ENTREE_12V": 2.4892, "PUISSANCE_8V": 2.0066, "CANAL_2A": 1.5, "RAILS": 0.508}
        for r in d["reseaux"]:
            if r["classe"] in largeurs:
                self.assertAlmostEqual(r["largeurs_mm"][1], largeurs[r["classe"]], places=4, msg=r["nom"])

    def test_deduction_3_9(self):
        d = self.d
        self.assertEqual((len(d["couches"]), len(d["composants"]), len(d["reseaux"])), (4, 359, 259))
        self.assertEqual([(c["nom"], c["role"], [z["reseau"] for z in c["zones_plan"]]) for c in d["couches"]],
                         [("TopLayer", "routage", []), ("MidLayer1", "plan", ["GND"]), ("MidLayer2", "routage", []),
                          ("BottomLayer", "routage", [])])
        self.assertEqual(d["masses"], ["GND"])
        self.assertTrue(res(d, "GND")["masse"])
        attendu = {"ENTREE_12V": ("puissance", None), "PUISSANCE_8V": ("puissance", None),
                   "CANAL_2A": ("puissance", None), "RAILS": ("alimentation", None),
                   "COMMUTATION": ("commutation", None), "SENSE": ("signal", "sensible"),
                   "TRIG_50R": ("signal", "sensible")}
        for r in d["reseaux"]:
            if r["classe"] in attendu:
                role, sens = attendu[r["classe"]]
                self.assertEqual(r["role"], role, r["nom"])
                if sens:
                    self.assertEqual((r["sensibilite"], r["poids"]), (sens, 3.0), r["nom"])
            if r["nom"].endswith("Vref"):
                self.assertEqual((r["role"], r["sensibilite"]), ("alimentation", "sensible"), r["nom"])
            if r["nom"].startswith("SEG"):
                self.assertEqual(r["sensibilite"], "peu_sensible", r["nom"])
        self.assertEqual(res(d, "NetC19_1")["role"], "commutation")
        self.assertEqual(res(d, "+8V")["couche_preferee"], "MidLayer2")
        self.assertEqual(sorted(c["nom"] for c in d["composants"] if c["pas_fin"]), ["M1", "M2", "U10"])
        cs = {c["nom"]: c for c in d["composants"]}
        faces = {c["designateur"]: c["face"] == "dessous" for c in self.e["composants"]}
        for n in ("U10", "M2", "M1", "L1", "C20", "C19", "C18", "C17", "C16", "C15", "C7"):   # ancien bloc
            self.assertEqual(cs[n]["orientations"], [[r, faces[n]] for r in (0, 90, 180, 270)], n)
        self.assertEqual((cs["M4_ALIM1"]["statut"], cs["M4_ALIM2"]["statut"]), ("fixe", "fixe"))
        self.assertEqual(len(d["trous"]), 5)
        rep = {g["famille"]: [len(x["pieces"]) for x in g["exemplaires"]] for g in d["groupes"]
               if g["type"] == "repete"}
        self.assertEqual(rep, {"_ALIM": [119, 119], "_VOLT": [18, 18]})
        st = [c["statut"] for c in d["composants"]]
        self.assertEqual((st.count("fixe"), st.count("mobile")), (55, 304))   # 45 + les 10 trimmers 3224W figés (18/11/2026)
        for n in ("R70_ALIM1", "R95_VOLT2", "D1", "D2", "D3"):
            self.assertEqual(cs[n]["statut"], "fixe", n)
        self.assertEqual([e["type"] for e in d["eloignements"]], ["souple", "dure"])
        self.assertEqual([c["courant_a"] for c in d["charges"]], [2.0, 2.0])

    def test_convertisseur_ecrit_a_la_main(self):
        conv = self.c["convertisseurs"][0]
        for k, v in CONVERTISSEUR_ECRIT_A_LA_MAIN.items():
            if k in ("eloignement", "nom") or k.startswith("_") or k not in conv:     # nom : libellé libre
                continue
            if k.startswith("cond_") and isinstance(v, str):        # lecture v1 : chaîne -> liste (§2.4)
                v = [v] if v else []
            if k == "reseau_commutation" and v == "":               # v1 : réseau non écrit (déduit : NetC19_1)
                continue
            self.assertEqual(sorted(v) if isinstance(v, list) else v,
                             sorted(conv[k]) if isinstance(conv[k], list) else conv[k], k)


EXEMPLE = os.path.join(ICI, "exemples", "metroid_v2")

# Convertisseur de l'ancien config_carte.json écrit à la main (v1, supprimé en B-ET7), figé ici.
CONVERTISSEUR_ECRIT_A_LA_MAIN = {
    "nom": "hacheur 12 V -> 8 V", "haut": "M1", "bas": "M2", "pilote": "U10", "inductance": "L1",
    "cond_entree": ["C7", "C15"], "cond_bulk": "C17", "cond_sortie": "C20", "cond_pilote": "C15",
    "reseau_entree": "+12V", "reseau_masse": "GND", "reseau_sortie": "+8V", "reseau_commutation": "",
    "reseaux_courts": ["NetC19_2", "NetC16_2", "NetC18_2", "NetM1_4", "NetM2_4"],
    "reseaux_sensibles": ["NetC21_1", "NetC22_2", "NetR25_2"]}


def _lire(nom):
    with open(os.path.join(EXEMPLE, nom), encoding="utf-8") as f:
        return json.load(f)


class TestExempleMetroid(unittest.TestCase):
    """Non-régression contre exemples/metroid_v2/ (jalon B2) ; sans carte .PcbDoc."""

    def test_donnees_nominales(self):
        d = _lire("donnees_placeur.json")
        st = [c["statut"] for c in d["composants"]]
        self.assertEqual((len(st), st.count("fixe"), st.count("mobile")), (359, 55, 304))
        self.assertEqual(sorted(g["nom"] for g in d["groupes"] if g["type"] == "repete"), ["_ALIM", "_VOLT"])
        self.assertEqual(formats.valider_donnees_placeur(d), [])
        self.assertEqual(formats.valider_donnees_placeur(_lire("donnees_placeur_9b5b5a3.json")), [])
        self.assertEqual(formats.valider_deduction(_lire("deduction.json")), [])

    def test_compatibilite_9b5b5a3(self):
        """Critère A7 : boîtes de l'ancien donnees_placeur.json (figées dans contraintes_9b5b5a3.json, vérifiées
        à 1e-6 mm contre l'original avant sa suppression), priorités 2 et 5, groupe _ALIM seul."""
        d = _lire("donnees_placeur_9b5b5a3.json")
        boites = _lire("contraintes_9b5b5a3.json")["boites"]
        self.assertEqual(len(boites), 359)
        cs = {c["nom"]: c for c in d["composants"]}
        self.assertEqual(set(cs), set(boites))
        for n, b in boites.items():
            for x, y in zip(cs[n]["lbb"], b):
                self.assertAlmostEqual(x, y, delta=1e-6, msg=n)
        prio = {r["nom"]: r["priorite"] for r in d["reseaux"]}
        self.assertEqual({n for n, p in prio.items() if p == 2},
                         {"+12V", "NetR62_ALIM1_1", "NetR62_ALIM2_1", "OUT_CH1", "OUT_CH2", "NetD16_ALIM1_2",
                          "NetD16_ALIM2_2"})
        self.assertEqual({n for n, p in prio.items() if p == 5}, {"NetR12_1", "NetR3_2", "NetQ3_3"})
        self.assertEqual([g["nom"] for g in d["groupes"] if g["type"] == "repete"], ["_ALIM"])

    def test_reconstruction_identique(self):
        """construire_donnees sur les fichiers de l'exemple redonne donnees_placeur.json octet pour octet."""
        with tempfile.TemporaryDirectory() as t:
            sortie = os.path.join(t, "donnees_placeur.json")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = CD.main([os.path.join(EXEMPLE, "entree_carte.json"), "--config",
                                os.path.join(EXEMPLE, "config_carte.json"), "--contraintes",
                                os.path.join(EXEMPLE, "contraintes.json"), "--sortie", sortie])
            self.assertEqual(code, 0)
            with open(sortie, "rb") as f, open(os.path.join(EXEMPLE, "donnees_placeur.json"), "rb") as g:
                self.assertEqual(f.read().replace(b"\r\n", b"\n"), g.read().replace(b"\r\n", b"\n"))

    def test_lecture_des_contraintes(self):
        for nom in ("contraintes.json", "contraintes_9b5b5a3.json"):
            self.assertEqual(formats.lire_contraintes(os.path.join(EXEMPLE, nom))[1], [], nom)


class _Locales(unittest.TestCase):
    NOM = None

    @classmethod
    def setUpClass(cls):
        cls.e, cls.c, cls.d, cls.x = chaine_fichier(cls(), os.path.join(CARTES_LOCALES, cls.NOM))


class TestSL1(_Locales):
    NOM = SL1

    def test_regles_3_7(self):
        d, e = self.d, self.e
        for n in ("5V", "VCCINT", "GND", "VCCO"):
            self.assertEqual([round(v, 4) for v in res(d, n)["largeurs_mm"]], [0.3, 0.3, 2.0], n)
            self.assertEqual(res(d, n)["role"], "alimentation", n)
            self.assertEqual((round(res(d, n)["via"]["diametre_max"], 4), round(res(d, n)["via"]["trou_max"], 4)),
                             (1.5, 0.8))
        self.assertEqual([round(v, 4) for v in e["regles"]["largeur_defaut"]], [0.2, 0.2, 2.0])
        self.assertIn(("Clearance_Power Polygon", "polygones"),
                      [(r["nom"], r["cause"]) for r in e["regles"]["non_interpretees"]])

    def test_deduction_3_9(self):
        d = self.d
        self.assertEqual([(c["nom"], c["role"]) for c in d["couches"]], [("TopLayer", "routage"),
                                                                       ("BottomLayer", "routage")])
        self.assertEqual(d["masses"], ["GND"])
        self.assertEqual(self.c["convertisseurs"], [])
        self.assertEqual(sorted(c["nom"] for c in d["composants"] if c["pas_fin"]), ["U1", "U5"])
        f = _fixes(d)
        for n in ("HDR2", "HDR3", "JP2", "LCD1", "U1"):
            self.assertEqual(f.get(n), "verrouillee", n)
        for n in ("J1", "HDR1", "S1", "S2", "S3"):
            self.assertIn(f.get(n), ("suggestion", "bord"), n)
        self.assertEqual([(t["trou"]["metallise"], t["diametre_cuivre"], round(t["trou"]["diametre"], 2))
                          for t in d["trous"]], [(False, 0.0, 3.3)] * 4)
        self.assertEqual((round(d["regles"]["via"]["diametre"], 4), round(d["regles"]["via"]["trou"], 4)), (0.9, 0.6))
        kz = {c["nom"]: len(c["zones_interdites"]) for c in d["composants"] if c["zones_interdites"]}
        self.assertEqual(kz, {"U1": 2, "LCD1": 4})
        self.assertEqual([c["nom"] for c in d["composants"] if c["virtuel"]], ["__libre_0"])


class TestWiFi(_Locales):
    NOM = WIFI

    def test_regles_3_7(self):
        d, e = self.d, self.e
        self.assertEqual(d["paires"][0]["nom"], "PA12_USB_D")
        self.assertAlmostEqual(_iso(d, "PA12_USB_D_P", "PA12_USB_D_N"), 0.1, places=5)
        self.assertEqual(e["regles"]["hauteurs"], {"dessus": 2.40000028, "dessous": 1.34999984})
        self.assertEqual(e["regles"]["isolement_pieces"], 0.254)
        self.assertAlmostEqual(e["regles"]["garde_bord"], 0.2, places=5)

    def test_deduction_3_9(self):
        d = self.d
        self.assertEqual([(c["nom"], c["role"], [z["reseau"] for z in c["zones_plan"]]) for c in d["couches"]],
                         [("TopLayer", "routage", []), ("MidLayer1", "plan", ["3V3"]),
                          ("MidLayer2", "plan", ["GND"]), ("BottomLayer", "routage", [])])
        self.assertEqual(d["masses"], ["GND"])
        self.assertEqual({r["nom"] for r in d["reseaux"] if r["role"] != "signal"}, {"GND", "3V3"})
        self.assertEqual(_fixes(d), {"P1": "verrouillee"})
        self.assertAlmostEqual(d["carte"]["marge_bord_mm"], 0.2, places=5)
        self.assertAlmostEqual(d["carte"]["garde_bord_mm"], 0.2, places=5)
        self.assertEqual(d["regles"]["placement"]["marge_piece_mm"], 0.25)
        self.assertEqual((d["parametres_deduction"]["hauteur_max_dessus_mm"],
                          d["parametres_deduction"]["hauteur_max_dessous_mm"]), (2.40000028, 1.34999984))
        self.assertEqual([(t["trou"]["metallise"], t["diametre_cuivre"], round(t["trou"]["diametre"], 2))
                          for t in d["trous"]], [(False, 0.0, 2.6)] * 2)
        self.assertAlmostEqual(self.e["carte"]["aire_reference_mm2"], 784.773, places=2)


def ecarts_attendu(att, c, d):
    """Écarts entre une déduction (config c, données d) et un ``attendu_deduction/1`` du corpus ; liste de textes."""
    ec = []
    R = {r["nom"]: r for r in d["reseaux"]}
    cou = [{"nom": x["nom"], "role": x["role"], "plans": [z["reseau"] for z in x["zones_plan"]]} for x in d["couches"]]
    if cou != att["couches"]:
        ec.append(f"couches : {cou} au lieu de {att['couches']}")
    if d["masses"] != att["masses"]:
        ec.append(f"masses : {d['masses']} au lieu de {att['masses']}")
    for role, nets in att["roles"].items():
        for n in nets:
            if R.get(n, {}).get("role") != role:
                ec.append(f"rôle de {n} : {R.get(n, {}).get('role')} au lieu de {role}")
    for n, l in att["largeurs"].items():
        got = R[n]["largeurs_mm"]
        if any(v is not None and abs(v - g) > 1e-4 for v, g in zip(l, got)):
            ec.append(f"largeurs de {n} : {got} au lieu de {l}")
    if sorted(d["sans_piste_dessous"]) != sorted(att["sans_piste_dessous"]):
        ec.append(f"sans_piste_dessous : {d['sans_piste_dessous']} au lieu de {att['sans_piste_dessous']}")
    pf = sorted(x["nom"] for x in d["composants"] if x["pas_fin"])
    if pf != sorted(att["pas_fin"]):
        ec.append(f"pas_fin : {pf} au lieu de {att['pas_fin']}")
    mx = sorted(x["nom"] for x in d["composants"] if x.get("matrice"))
    if mx != sorted(att["matrice"]):
        ec.append(f"matrice : {mx} au lieu de {att['matrice']}")
    if abs(d["regles"]["isolement"]["defaut"] - att["isolement"]["defaut"]) > 1e-4:
        ec.append(f"isolement.defaut : {d['regles']['isolement']['defaut']}")
    for q in att["isolement"]["requis"]:
        v = _iso(d, *q["reseaux"])
        if abs(v - q["mm"]) > 1e-4:
            ec.append(f"isolement {q['reseaux']} : {v} au lieu de {q['mm']}")
    f = {k: v for k, v in _fixes(d).items() if k in att["pieces_fixes"] or v != "virtuel"}
    if f != att["pieces_fixes"]:
        ec.append(f"pieces_fixes : {f} au lieu de {att['pieces_fixes']}")
    conv = {x["reseau_commutation"]: x for x in c["convertisseurs"]}
    if sorted(conv) != sorted(x["reseau_commutation"] for x in att["convertisseurs"]):
        ec.append(f"convertisseurs : {sorted(conv)}")
    for a in att["convertisseurs"]:
        g = conv.get(a["reseau_commutation"], {})
        for k, v in a.items():
            gv = g.get(k)
            if isinstance(v, list):
                v, gv = sorted(v), sorted(gv or [])
            elif isinstance(v, dict):
                v = {kk: sorted(vv) if isinstance(vv, list) else vv for kk, vv in v.items()}
                gv = {kk: sorted(vv) if isinstance(vv, list) else vv for kk, vv in (gv or {}).items() if kk in v}
            if v != gv:
                ec.append(f"convertisseur {a['reseau_commutation']}.{k} : {gv} au lieu de {v}")
    groupes = [(g["type"], sorted(g.get("pieces") or [])) for g in d["groupes"]]
    for g in att["groupes"]:
        if (g["type"], sorted(g["pieces"])) not in groupes:
            ec.append(f"groupe {g['type']} {sorted(g['pieces'])} absent")
    return ec


# Écarts connus entre la déduction et les attendus du lot E, signalés au lot E (journal du lot B, B-ET6) : l'exemple
# de §2.4 (METROID : cond_entree [C7, C15] sans le bulk C17, cond_sortie [C20] seul, boucle chaude C7, C15, §3.9)
# exclut le condensateur bulk de cond_entree et de la boucle chaude et ne garde que le plus gros condensateur de
# sortie ; les attendus du corpus les y mettent. cond_pilote C4 (pilote alimenté hors de l'entrée) : à trancher.
ECARTS_SIGNALES_LOT_E = {
    "deux_convertisseurs": [
        "convertisseur SW1.cond_entree : ['C2'] au lieu de ['C1', 'C2']",
        "convertisseur SW1.cond_sortie : ['C5'] au lieu de ['C5', 'C8']",
        "convertisseur SW1.cond_pilote : [] au lieu de ['C4']",
        "convertisseur SW1.boucle_chaude : {'condensateurs': ['C2'], 'reseau_rail': '+12V', 'reseau_masse': 'GND'} "
        "au lieu de {'condensateurs': ['C1', 'C2'], 'reseau_rail': '+12V', 'reseau_masse': 'GND'}",
        "convertisseur SW2.cond_entree : ['C8'] au lieu de ['C5', 'C8']",
        "convertisseur SW2.boucle_chaude : {'condensateurs': ['C8'], 'reseau_rail': '+5V', 'reseau_masse': 'GND'} "
        "au lieu de {'condensateurs': ['C5', 'C8'], 'reseau_rail': '+5V', 'reseau_masse': 'GND'}"],
    "sans_classes": [
        "convertisseur SW.cond_entree : ['C2'] au lieu de ['C1', 'C2']",
        "convertisseur SW.cond_sortie : ['C4'] au lieu de ['C4', 'C5']",
        "convertisseur SW.boucle_chaude : {'condensateurs': ['C2'], 'reseau_rail': '+12V', 'reseau_masse': 'GND'} "
        "au lieu de {'condensateurs': ['C1', 'C2'], 'reseau_rail': '+12V', 'reseau_masse': 'GND'}"]}


class TestCorpusCartes(unittest.TestCase):
    def test_cartes_du_corpus(self):
        noms = sorted(os.listdir(CORPUS_CARTES))
        self.assertTrue(noms)
        for nom in noms:
            with self.subTest(carte=nom):
                rep = os.path.join(CORPUS_CARTES, nom)
                with open(os.path.join(rep, "attendu_deduction.json"), encoding="utf-8") as f:
                    att = json.load(f)
                _, c, d, _ = chaine_fichier(self, os.path.join(rep, nom + ".kicad_pcb"),
                                            os.path.join(rep, "contraintes.json"))
                self.assertEqual(ecarts_attendu(att, c, d), ECARTS_SIGNALES_LOT_E.get(nom, []))


def load_tests(loader, standard, motif):
    """Les tests SL1 et WiFi ne sont ajoutés qu'avec --cartes-locales ; sinon une ligne le dit."""
    suite = unittest.TestSuite()
    for cl in [v for v in globals().values() if isinstance(v, type) and issubclass(v, unittest.TestCase)]:
        if cl is _Locales or (issubclass(cl, _Locales) and not CARTES_LOCALES):
            continue
        suite.addTests(loader.loadTestsFromTestCase(cl))
    if not CARTES_LOCALES:
        sys.stderr.write("test_construire_donnees : tests SL1 et WiFi non lancés (option --cartes-locales absente)\n")
    return suite


def _lire_options(argv):
    """Retire --cartes-locales DOSSIER (ou --cartes-locales=DOSSIER) de argv ; rend (dossier, reste)."""
    dossier, reste, i = None, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--cartes-locales" and i + 1 < len(argv):
            dossier, i = argv[i + 1], i + 2
            continue
        if a.startswith("--cartes-locales="):
            dossier = a.split("=", 1)[1]
        else:
            reste.append(a)
        i += 1
    if dossier is not None and not os.path.isdir(dossier):
        sys.stderr.write("--cartes-locales : dossier introuvable\n")
        sys.exit(2)
    return dossier, reste


if __name__ == "__main__":
    CARTES_LOCALES, _reste = _lire_options(sys.argv[1:])
    unittest.main(argv=[sys.argv[0]] + _reste)
