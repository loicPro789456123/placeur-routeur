"""Tests du langage des portées des règles Altium et de la résolution des règles (portees.py, contrat §3.7).

    python -B -m unittest test_portees -v
    python -B test_portees.py --cartes-locales DOSSIER [options de unittest]

- Grammaire, évaluation et résolution sur des règles synthétiques : toujours lancés.
- METROID : règles et profils lus dans PCB_METROID_V2_rev3.PcbDoc, à la racine du dépôt, par l'API actuelle de
  format_altium (désignateurs : fonction existante de la sérigraphie, remplacée par celle de format_altium à
  l'étape B-ET5 du lot B). Sautés, et comptés, si la carte est absente.
- Cartes d'exemple Altium (SL1, WiFi, SODIMM, Bluetooth_Sentinel ; jamais publiées) : collectés seulement avec
  --cartes-locales, qui désigne leur dossier. Sans l'option, ils ne sont pas lancés et une ligne le dit sur la
  sortie d'erreur ; avec l'option, une carte absente fait échouer son test.
"""
import math
import os
import sys
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
DEPOT = os.path.dirname(os.path.dirname(os.path.dirname(ICI)))
sys.path.insert(0, ICI)
sys.path.insert(0, os.path.join(os.path.dirname(ICI), "format_altium"))
sys.path.insert(0, os.path.join(os.path.dirname(ICI), "serigraphie"))
import portees as P  # noqa: E402

REV3 = os.path.join(DEPOT, "PCB_METROID_V2_rev3.PcbDoc")
CARTES_LOCALES = None          # dossier des cartes d'exemple (option --cartes-locales du lancement en script)
SL1 = "SL1 Xilinx Spartan-IIE PQ208 Rev1.02.PcbDoc"
WIFI = "WiFi.PcbDoc"
SODIMM = "PC4-SODIMM_V200_RC_A1_20141015.PcbDoc"
BLUETOOTH = "Bluetooth_Sentinel.PcbDoc"
MIL = 0.0254


# =====================================================================================================================
# Outils communs
# =====================================================================================================================
def regle(type_, nom, priorite, portee1, portee2="All", **valeurs):
    """Règle au format entree_carte.regles_fichier."""
    return {"type": type_, "nom": nom, "priorite": priorite, "portee1": portee1, "portee2": portee2,
            "valeurs": dict(valeurs)}


def clearance(nom, priorite, portee1, portee2, gap_mil, objets="", **autres):
    v = {"GAP": f"{gap_mil}mil", "GENERICCLEARANCE": f"{gap_mil}mil", "OBJECTCLEARANCES": objets,
         "NETSCOPE": "DifferentNets", "LAYERKIND": "SameLayer", **autres}
    return regle("Clearance", nom, priorite, portee1, portee2, **v)


def pastille(piece, nom, reseau, cms=True, couches=("TopLayer",), **autres):
    return {"type": "pastille_cms" if cms else "pastille_traversante", "piece": piece, "pastille": nom,
            "reseau": reseau, "couches": list(couches), **autres}


def nom_commun(v6):
    """Nom V6 de format_altium ('TOP', 'MID2', 'PLANE1', 'BOTTOM') -> nom commun (§0.4), None hors cuivre."""
    if v6 == "TOP":
        return "TopLayer"
    if v6 == "BOTTOM":
        return "BottomLayer"
    for prefixe, commun in (("MID", "MidLayer"), ("PLANE", "InternalPlane")):
        if v6 and v6.startswith(prefixe) and v6[len(prefixe):].isdigit():
            return commun + v6[len(prefixe):]
    return None


def ouvrir(chemin):
    """Ouvre une carte par l'API actuelle de format_altium. nom_reseau n'y est pas encore mémorisé (livré par B1) :
    la liste des réseaux est mémorisée sur l'instance ouverte (21 s -> 1 s pour les profils de rev3)."""
    from altium_fichier import PcbDoc
    doc = PcbDoc.ouvrir(chemin)
    noms = doc.reseaux()
    doc.reseaux = lambda: noms
    return doc


def regles_altium(doc, toutes=False):
    """Règles de l'API actuelle de format_altium -> format regles_fichier (actives seulement, sauf `toutes`)."""
    return [dict(regle(r["type"], r["nom"], r["priorite"], r["portee1"], r["portee2"], **r["proprietes"]),
                 active=r["active"]) for r in doc.regles() if toutes or r["active"]]


def contexte_altium(doc):
    """Contexte de interpreter tiré d'une carte par l'API actuelle de format_altium (sans les pièces)."""
    classes = doc.classes()
    couches, noms_fichier = [], {}
    for e in doc.empilage():
        c = nom_commun(e["couche"])
        if c:
            couches.append(c)
            noms_fichier[c] = e["nom"]
    paires = []
    flux = doc.data("DifferentialPairs6")
    try:
        entrees = list(flux or [])
    except TypeError:            # flux vide décodé en FluxBrut par l'API actuelle (défaut traité par B1)
        entrees = []
    for e in entrees:
        p = getattr(e, "props", e)
        paires.append({"nom": p.texte("NAME"), "positif": p.texte("POSITIVENETNAME"),
                       "negatif": p.texte("NEGATIVENETNAME")})
    return {"reseaux": [n for n in doc.reseaux() if n],
            "classes": {c["nom"]: c["membres"] for c in classes if c["type"] == 0 and not c["superclasse"]},
            "classes_composants": {c["nom"]: c["membres"] for c in classes if c["type"] == 1 and not c["superclasse"]},
            "paires": paires, "couches": couches, "noms_couches_fichier": noms_fichier, "pieces": []}


class AssertionsProches:
    def assertProche(self, a, b, tol=1e-6, msg=None):
        self.assertIsNotNone(a, msg)
        self.assertLessEqual(abs(a - b), tol, msg or f"{a!r} != {b!r} à {tol:g} près")

    def assertListeProche(self, a, b, tol=1e-6, msg=None):
        self.assertIsNotNone(a, msg)
        self.assertEqual(len(a), len(b), msg)
        for x, y in zip(a, b):
            if isinstance(y, (list, tuple)):
                self.assertListeProche(x, y, tol, msg)
            else:
                self.assertProche(x, y, tol, msg or f"{a!r} != {b!r} à {tol:g} près")


# =====================================================================================================================
# Grammaire
# =====================================================================================================================
class TestGrammaire(unittest.TestCase):
    def test_operateurs_et_priorites(self):
        attendu = "(InNet('a') Or (InNet('b') And Not InNet('c')))"
        self.assertEqual(repr(P.compiler("InNet('a') Or InNet('b') And Not InNet('c')")), attendu)
        self.assertEqual(repr(P.compiler("InNet('a') || InNet('b') && !InNet('c')")), attendu)
        self.assertEqual(repr(P.compiler("InNet('a')||InNet('b')&&!InNet('c')")), attendu)
        # Not > And : « Not a And b » = (Not a) And b
        self.assertEqual(repr(P.compiler("Not InNet('a') And InNet('b')")), "(Not InNet('a') And InNet('b'))")
        # parenthèses
        self.assertEqual(repr(P.compiler("(InNet('a') Or InNet('b')) And Not (InNet('c') Or InNet('d'))")),
                         "((InNet('a') Or InNet('b')) And Not (InNet('c') Or InNet('d')))")
        self.assertEqual(repr(P.compiler("((All))")), "All")
        self.assertEqual(repr(P.compiler("Not Not IsVia")), "Not Not IsVia")
        self.assertEqual(repr(P.compiler("!!IsVia")), "Not Not IsVia")
        # suites d'opérateurs de même niveau
        self.assertEqual(repr(P.compiler("IsVia Or IsPad Or IsTrack")), "(IsVia Or IsPad Or IsTrack)")
        self.assertEqual(repr(P.compiler("IsVia && IsPad And IsTrack")), "(IsVia And IsPad And IsTrack)")
        # opérateur collé à une parenthèse (portée réelle de SODIMM : « …('min0')or WithinRoom… »)
        self.assertEqual(repr(P.compiler("InNet('a')or InNet('b')")), "(InNet('a') Or InNet('b'))")

    def test_evaluation_des_priorites(self):
        a = P.compiler("InNet('a') Or InNet('b') And Not IsVia")
        self.assertTrue(P.evaluer(a, P.completer_profil({"type": "via", "reseau": "a"})))
        self.assertFalse(P.evaluer(a, P.completer_profil({"type": "via", "reseau": "b"})))
        self.assertTrue(P.evaluer(a, P.completer_profil({"type": "piste", "reseau": "b"})))
        b = P.compiler("(InNet('a') Or InNet('b')) And Not IsVia")
        self.assertFalse(P.evaluer(b, P.completer_profil({"type": "via", "reseau": "a"})))

    def test_casse_ignoree(self):
        a = P.compiler("INNET('gnd') aNd nOt isvia OR innetclass('PUISSANCE')")
        self.assertEqual(repr(a), "((InNet('gnd') And Not IsVia) Or InNetClass('PUISSANCE'))")
        index = P.index_contexte({"classes": {"Puissance": ["+12V"]}})
        self.assertTrue(P.evaluer(a, P.completer_profil({"type": "piste", "reseau": "GND"}, index)))
        self.assertFalse(P.evaluer(a, P.completer_profil({"type": "via", "reseau": "Gnd"}, index)))
        self.assertTrue(P.evaluer(a, P.completer_profil({"type": "via", "reseau": "+12V"}, index)))
        self.assertTrue(P.evaluer(P.compiler("InComponent('u1')"), P.completer_profil({"piece": "U1"})))

    def test_apostrophe_doublee(self):
        a = P.compiler("InNet('it''s') Or InNet('''')")
        self.assertEqual(a.termes[0].args, ("it's",))
        self.assertEqual(a.termes[1].args, ("'",))
        self.assertEqual(repr(a), "(InNet('it''s') Or InNet(''''))")
        self.assertTrue(P.evaluer(a, P.completer_profil({"reseau": "it's"})))
        self.assertTrue(P.evaluer(a, P.completer_profil({"reseau": "'"})))
        self.assertFalse(P.evaluer(a, P.completer_profil({"reseau": "its"})))
        # chaîne avec opérateurs et parenthèses (portée réelle de SODIMM)
        b = P.compiler("InNetClass('Address and Comand (group1)') or InNetClass('Control')")
        self.assertEqual(b.termes[0].args, ("Address and Comand (group1)",))

    def test_nombres(self):
        # la grammaire lit les nombres ; les prédicats pris en charge n'acceptent que du texte
        for texte, pos in (("InNet(5)", 7), ("InNet(-2.5)", 7), ("InNet(.5)", 7)):
            with self.assertRaises(P.ErreurPortee) as ctx:
                P.compiler(texte)
            self.assertEqual(ctx.exception.position, pos, texte)
            self.assertIn("argument texte attendu", ctx.exception.cause)
            self.assertIsNone(ctx.exception.predicat)

    def test_erreur_de_syntaxe_avec_position(self):
        cas = [
            ("InNet('a'", 10, "« ) » attendu"),
            ("InNet('a) Or All", 7, "chaîne non terminée"),
            ("InNet('a') Or", 14, "prédicat ou « ( » attendu"),
            ("(InNet('a')", 12, "« ) » attendu"),
            ("InNet('a') InNet('b')", 12, "inattendu après la fin"),
            ("InNet('a') & InNet('b')", 12, "caractère « & » inattendu"),
            ("InNet('a'))", 11, "inattendu après la fin"),
            ("All(", 5, "argument attendu"),
            ("InNet(,'a')", 7, "argument attendu"),
            ("And IsVia", 1, "prédicat ou « ( » attendu"),
            ("   ", 1, "portée vide"),
            ("", 1, "portée vide"),
            (None, 1, "portée vide"),
        ]
        for texte, pos, debut in cas:
            with self.assertRaises(P.ErreurPortee, msg=repr(texte)) as ctx:
                P.compiler(texte)
            e = ctx.exception
            self.assertEqual(e.position, pos, repr(texte))
            self.assertIn(debut, e.cause, repr(texte))
            self.assertIn(f"(position {pos})", str(e), repr(texte))
            self.assertIsNone(e.predicat, repr(texte))

    def test_predicat_non_pris_en_charge(self):
        cas = [("WithinRoom('R1')", "WithinRoom", 1), ("InPadClass('Polygon')", "InPadClass", 1),
               ("IsPad And InAnyComponent", "InAnyComponent", 11), ("(CompPinCount < 5)", "CompPinCount", 2),
               ("(ObjectKind = 'Text') And (StringText = 'x')", "ObjectKind", 2),
               ("WithinRoom('min0')or WithinRoom('min1')", "WithinRoom", 1), ("Été", "Été", 1)]
        for texte, nom, pos in cas:
            with self.assertRaises(P.ErreurPortee, msg=texte) as ctx:
                P.compiler(texte)
            e = ctx.exception
            self.assertEqual(e.cause, f"prédicat {nom} non pris en charge", texte)
            self.assertEqual(e.predicat, nom, texte)
            self.assertEqual(e.position, pos, texte)

    def test_nombre_d_arguments(self):
        for texte in ("InNet()", "InNet", "IsPad('x')", "InNet('a', 'b')", "All('x')"):
            with self.assertRaises(P.ErreurPortee, msg=texte) as ctx:
                P.compiler(texte)
            self.assertIn("argument(s) attendu(s)", ctx.exception.cause, texte)
        self.assertEqual(repr(P.compiler("All()")), "All")
        self.assertEqual(repr(P.compiler("IsVia ( )")), "IsVia")


# =====================================================================================================================
# Évaluation des prédicats
# =====================================================================================================================
class TestPredicats(unittest.TestCase):
    def setUp(self):
        self.index = P.index_contexte({
            "classes": {"PUISSANCE": ["+12V", "+8V"]}, "classes_composants": {"ALIM": ["U1", "C1"]},
            "paires": [{"nom": "USB", "positif": "D_P", "negatif": "D_N"}],
            "pieces": [{"designateur": "U1", "empreinte": "SOIC-8", "face": "dessus"}],
            "noms_couches_fichier": {"TopLayer": "Signal 1", "MidLayer1": "L2_GND", "BottomLayer": "Signal 2"}})

    def ev(self, texte, **profil):
        return P.evaluer(P.compiler(texte), P.completer_profil(profil, self.index))

    def test_reseaux_classes_paires(self):
        self.assertTrue(self.ev("All"))
        self.assertTrue(self.ev("InNet('+12V')", type="piste", reseau="+12V"))
        self.assertFalse(self.ev("InNet('+12V')", type="piste", reseau="+8V"))
        self.assertTrue(self.ev("InNetClass('PUISSANCE')", type="via", reseau="+8V"))
        self.assertFalse(self.ev("InNetClass('PUISSANCE')", type="via", reseau="GND"))
        self.assertFalse(self.ev("InNetClass('PUISSANCE')", type="via", reseau=""))
        self.assertTrue(self.ev("InDifferentialPair('USB')", type="piste", reseau="D_N"))
        self.assertTrue(self.ev("InAnyDifferentialPair", type="piste", reseau="D_P"))
        self.assertFalse(self.ev("InAnyDifferentialPair", type="piste", reseau="GND"))

    def test_pieces_et_pastilles(self):
        self.assertTrue(self.ev("InComponent('U1')", type="pastille_cms", piece="U1", pastille="3"))
        self.assertFalse(self.ev("InComponent('U1')", type="piste", reseau="X"))
        self.assertTrue(self.ev("InComponentClass('ALIM')", type="region", piece="C1"))
        self.assertFalse(self.ev("InComponentClass('ALIM')", type="region", piece="R1"))
        self.assertTrue(self.ev("HasFootprint('soic-8')", type="pastille_cms", piece="U1"))
        self.assertFalse(self.ev("HasFootprint('SOIC-8')", type="pastille_cms", piece="C1"))
        self.assertTrue(self.ev("HasPad('U1-3')", type="pastille_cms", piece="U1", pastille="3"))
        self.assertFalse(self.ev("HasPad('U1-3')", type="pastille_cms", piece="U1", pastille="4"))
        self.assertFalse(self.ev("HasPad('U1-3')", type="region", piece="U1", pastille="3"))

    def test_genres_d_objets(self):
        types = ["piste", "arc", "via", "pastille_cms", "pastille_traversante", "trou", "region", "remplissage",
                 "texte"]
        attendu = {"IsPad": {"pastille_cms", "pastille_traversante", "trou"}, "IsVia": {"via"},
                   "IsTrack": {"piste"}, "IsArc": {"arc"}, "IsFill": {"remplissage"}, "IsRegion": {"region"},
                   "IsText": {"texte"}}
        for pred, vrais in attendu.items():
            for t in types:
                self.assertEqual(self.ev(pred, type=t), t in vrais, (pred, t))

    def test_polygones(self):
        for pred in ("IsPolygon", "InPolygon", "InNamedPolygon('GND_TOP')"):
            self.assertTrue(self.ev(pred, type="region", reseau="GND", polygone="gnd_top"), pred)
            self.assertFalse(self.ev(pred, type="region", reseau="GND"), pred)
        self.assertFalse(self.ev("InNamedPolygon('autre')", type="region", polygone="GND_TOP"))
        # aucun objet du routeur n'est dans un polygone
        for g in P.GENRES_ROUTEUR:
            self.assertFalse(P.evaluer(P.compiler("InPolygon"), P.profil_routeur("GND", g, self.index)))

    def test_couches(self):
        self.assertTrue(self.ev("OnLayer('Top Layer')", type="pastille_cms", couches=["TopLayer"]))
        self.assertTrue(self.ev("OnTopLayer", type="piste", couches=["TopLayer"]))
        self.assertFalse(self.ev("OnBottomLayer", type="piste", couches=["TopLayer"]))
        # nom de couche du fichier (contexte) et normalisation (espaces, tirets, soulignés)
        self.assertTrue(self.ev("OnLayer('L2_GND')", type="piste", couches=["MidLayer1"]))
        self.assertTrue(self.ev("OnLayer('Mid-Layer 1')", type="piste", couches=["MidLayer1"]))
        # traversante, via, trou : couche MultiLayer
        for t in ("pastille_traversante", "via", "trou"):
            self.assertTrue(self.ev("OnLayer('MultiLayer')", type=t, couches=["TopLayer", "BottomLayer"]), t)
            self.assertFalse(self.ev("OnTopLayer", type=t, couches=["TopLayer", "BottomLayer"]), t)
        # pièce : face
        self.assertTrue(P.evaluer(P.compiler("OnBottomLayer"), P.profil_piece("U1", "dessous", self.index)))
        self.assertFalse(P.evaluer(P.compiler("OnBottomLayer"), P.profil_piece("U1", "dessus", self.index)))

    def test_analyse_statique(self):
        self.assertTrue(P.est_tout(P.compiler("All")))
        self.assertTrue(P.est_tout(P.compiler("(All) And All")))
        self.assertFalse(P.est_tout(P.compiler("InNet('a')")))
        self.assertTrue(P.polygones_seulement(P.compiler("inpolygon and innetclass('Power')")))
        self.assertTrue(P.polygones_seulement(P.compiler("IsPolygon")))
        self.assertFalse(P.polygones_seulement(P.compiler("innetclass('Power') and not inpolygon")))
        self.assertFalse(P.polygones_seulement(P.compiler("InPolygon Or InNet('a')")))
        self.assertEqual(P.predicats(P.compiler("OnLayer('x') And Not (IsVia Or All)")), {"onlayer", "isvia", "all"})


# =====================================================================================================================
# Valeurs des règles
# =====================================================================================================================
class TestValeurs(AssertionsProches, unittest.TestCase):
    def test_longueurs(self):
        self.assertProche(P.longueur_mm("9.8425mil"), 0.2499995, 1e-12)
        self.assertProche(P.longueur_mm(" 4.9mil "), 0.12446, 1e-12)
        self.assertProche(P.longueur_mm("0.25mm"), 0.25, 1e-12)
        self.assertProche(P.longueur_mm("10"), 0.254, 1e-12)
        for x in (None, "", "abc", "3furlong"):
            self.assertIsNone(P.longueur_mm(x), x)

    def test_objectclearances(self):
        objets, info = P.lire_objectclearances(
            "ClearanceObj_Arc-ClearanceObj_Fill:137795;ClearanceObj_Track-ClearanceObj_Poly:137795;"
            "ClearanceObj_SMDPad-ClearanceObj_Hole:0")
        self.assertProche(objets[("Arc", "Fill")], 0.35, 1e-6)
        self.assertProche(objets[("Fill", "Arc")], 0.35, 1e-6)
        self.assertEqual(len(objets), 2)
        self.assertEqual(len(info), 2)

    def test_largeurs_par_couche(self):
        v = {"MINLIMIT": "10mil", "PREFEREDWIDTH": "12mil", "MAXLIMIT": "100mil"}
        self.assertEqual(P.largeurs_regle(v, ["TopLayer", "BottomLayer"]), ([0.254, 0.3048, 2.54], None))
        v2 = dict(v, TOPLAYER_MINWIDTH="11mil", TOPLAYER_PREFWIDTH="12mil", TOPLAYER_MAXWIDTH="50mil",
                  MIDLAYER9_MINWIDTH="40mil", MIDLAYER9_PREFWIDTH="40mil", MIDLAYER9_MAXWIDTH="40mil")
        larg, avert = P.largeurs_regle(v2, ["TopLayer", "BottomLayer"])     # MidLayer9 : absente de la carte
        self.assertListeProche(larg, [0.2794, 0.3048, 1.27], 1e-9)
        self.assertIn("différentes selon les couches", avert)
        with self.assertRaises(ValueError):
            P.largeurs_regle({"MINLIMIT": "10mil"}, [])

    def test_via(self):
        v = P.via_regle({"WIDTH": "23.622mil", "HOLEWIDTH": "11.811mil", "MAXWIDTH": "31.496mil"})
        self.assertProche(v["diametre"], 23.622 * MIL, 1e-9)
        self.assertProche(v["diametre_min"], 23.622 * MIL, 1e-9)
        self.assertProche(v["diametre_max"], 31.496 * MIL, 1e-9)
        self.assertProche(v["trou"], 0.3, 1e-5)
        self.assertIsNone(v["trou_max"])
        with self.assertRaises(ValueError):
            P.via_regle({"WIDTH": "20mil"})


# =====================================================================================================================
# Résolution sur des règles synthétiques
# =====================================================================================================================
class TestResolution(AssertionsProches, unittest.TestCase):
    def test_regle_prioritaire_qui_relache(self):
        # sous U1, l'isolement est relâché à 0,1 mm ; ailleurs 0,2 mm
        regles = [clearance("sous_U1", 1, "InComponent('U1')", "InComponent('U1')", 3.937),
                  clearance("defaut", 2, "All", "All", 7.874)]
        objets = [pastille("U1", "1", "A"), pastille("U1", "2", "B"), pastille("R1", "1", "C")]
        res = P.interpreter(regles, objets, {"reseaux": ["A", "B", "C"]})
        iso = res["regles"]["isolement"]
        self.assertEqual(len(iso["classes"]), 2)
        self.assertEqual([o["classe_isolement"] for o in res["objets"]], [0, 0, 1])
        self.assertListeProche(iso["matrice"], [[3.937 * MIL, 7.874 * MIL], [7.874 * MIL, 7.874 * MIL]], 1e-9)
        self.assertEqual(iso["classe_reference"], 1)
        self.assertProche(iso["defaut"], 0.2, 1e-6)
        for n in ("A", "B", "C"):
            self.assertEqual(res["regles"]["reseaux"][n]["classes_isolement"], {"piste": 1, "via": 1, "surface": 1})
        self.assertEqual(iso["classes"], ["sous_U1:1+2", "autres objets"])

    def test_regle_par_paire_de_classes(self):
        # haute tension contre basse tension : 1,016 mm ; entre deux réseaux d'une même classe : 0,2 mm
        regles = [clearance("HT_BT", 1, "InNetClass('HT')", "InNetClass('BT')", 40),
                  clearance("defaut", 2, "All", "All", 7.874)]
        ctx = {"reseaux": ["HV", "LV", "SIG"], "classes": {"HT": ["HV"], "BT": ["LV"]}}
        res = P.interpreter(regles, [], ctx)
        iso, rr = res["regles"]["isolement"], res["regles"]["reseaux"]
        k = {n: rr[n]["classes_isolement"]["piste"] for n in ctx["reseaux"]}
        self.assertEqual(k, {"HV": 0, "LV": 1, "SIG": 2})
        d, h = 7.874 * MIL, 40 * MIL
        self.assertListeProche(iso["matrice"], [[d, h, d], [h, d, d], [d, d, d]], 1e-9)
        # la paire est couverte dans un sens ou dans l'autre : matrice symétrique
        self.assertEqual(iso["matrice"][0][1], iso["matrice"][1][0])
        self.assertProche(P.isolement_heuristique(iso, k["HV"]), d, 1e-9)   # HV contre la référence

    def test_priorites_inversees(self):
        larges = [clearance("defaut", 1, "All", "All", 7.874),
                  clearance("sous_U1", 2, "InComponent('U1')", "InComponent('U1')", 3.937)]
        objets = [pastille("U1", "1", "A"), pastille("R1", "1", "C")]
        res = P.interpreter(larges, objets, {"reseaux": ["A", "C"]})
        # la règle générale, plus prioritaire, couvre toutes les paires : le relâchement ne s'applique jamais
        for ligne in res["regles"]["isolement"]["matrice"]:
            for v in ligne:
                self.assertProche(v, 7.874 * MIL, 1e-9)
        # l'ordre de la liste ne compte pas, seule la priorité : mêmes règles dans l'autre ordre de liste
        a = P.interpreter([clearance("defaut", 2, "All", "All", 7.874),
                           clearance("sous_U1", 1, "InComponent('U1')", "InComponent('U1')", 3.937)],
                          objets, {"reseaux": ["A", "C"]})
        b = P.interpreter([clearance("sous_U1", 1, "InComponent('U1')", "InComponent('U1')", 3.937),
                           clearance("defaut", 2, "All", "All", 7.874)], objets, {"reseaux": ["A", "C"]})
        self.assertEqual(a["regles"]["isolement"], b["regles"]["isolement"])
        self.assertProche(a["regles"]["isolement"]["matrice"][0][0], 3.937 * MIL, 1e-9)

    def test_objectclearances_non_uniforme(self):
        oc = ("ClearanceObj_Track-ClearanceObj_Fill:137795;ClearanceObj_SMDPad-ClearanceObj_Fill:118110;"
              "ClearanceObj_Track-ClearanceObj_Hole:0;ClearanceObj_Track-ClearanceObj_Poly:137795")
        regles = [clearance("defaut", 1, "All", "All", 7.874, oc)]
        objets = [pastille("U1", "1", "A"), {"type": "remplissage", "reseau": "B", "couches": ["TopLayer"]}]
        res = P.interpreter(regles, objets, {"reseaux": ["A"]})
        iso = res["regles"]["isolement"]
        # classes = signatures complétées du genre, dans l'ordre des genres : Track, SMDPad, Via, Fill, Region
        self.assertEqual(iso["classes"], ["autres objets [Track]", "autres objets [SMDPad]", "autres objets [Via]",
                                          "autres objets [Fill]", "autres objets [Region]"])
        g = {nom: i for i, nom in enumerate(("Track", "SMDPad", "Via", "Fill", "Region"))}
        m = iso["matrice"]
        self.assertProche(m[g["Track"]][g["Fill"]], 0.35, 1e-6)
        self.assertProche(m[g["Fill"]][g["Track"]], 0.35, 1e-6)
        self.assertProche(m[g["SMDPad"]][g["Fill"]], 0.3, 1e-6)
        self.assertProche(m[g["Track"]][g["Track"]], 0.2, 1e-6)
        self.assertProche(m[g["Via"]][g["Region"]], 0.2, 1e-6)
        self.assertEqual(iso["classe_reference"], g["Track"])
        self.assertEqual([o["classe_isolement"] for o in res["objets"]], [g["SMDPad"], g["Fill"]])
        self.assertEqual(res["regles"]["reseaux"]["A"]["classes_isolement"],
                         {"piste": g["Track"], "via": g["Via"], "surface": g["Region"]})
        # couples d'un polygone ou d'un trou : information seulement
        self.assertTrue(any("Track-Hole" in i and "Track-Poly" in i for i in res["informations"]), res["informations"])

    def test_objectclearances_uniforme(self):
        # couples tous égaux à la valeur générique : pas de genre dans la signature
        uni = clearance("defaut", 1, "All", "All", 7.874, "ClearanceObj_Track-ClearanceObj_Fill:78740")
        res = P.interpreter([uni], [pastille("U1", "1", "A")], {"reseaux": ["A"]})
        self.assertEqual(len(res["regles"]["isolement"]["classes"]), 1)
        # matrice complète d'une seule valeur, différente de la valeur générique : c'est elle qui vaut
        complete = ";".join(f"ClearanceObj_{a}-ClearanceObj_{b}:100000" for a in P.GENRES for b in P.GENRES)
        res = P.interpreter([clearance("defaut", 1, "All", "All", 7.874, complete)], [], {"reseaux": ["A"]})
        self.assertListeProche(res["regles"]["isolement"]["matrice"], [[0.254]], 1e-9)

    def test_sans_regle_d_isolement(self):
        res = P.interpreter([], [pastille("U1", "1", "A")], {"reseaux": ["A"]})
        iso = res["regles"]["isolement"]
        self.assertEqual((iso["matrice"], iso["classe_reference"], iso["defaut"]), ([[0.2]], 0, 0.2))

    def test_limites_non_interpretees_et_ignorees(self):
        regles = [
            clearance("couche", 1, "OnLayer('TopLayer')", "All", 10),
            regle("Width", "largeur_couche", 1, "OnTopLayer", MINLIMIT="10mil", PREFEREDWIDTH="10mil",
                  MAXLIMIT="10mil"),
            clearance("meme_reseau", 2, "All", "All", 10, NETSCOPE="SameNet"),
            clearance("couches_adjacentes", 3, "All", "All", 10, LAYERKIND="AdjacentLayers"),
            regle("ComponentClearance", "piece_U1", 1, "InComponent('U1')", "All", GAP="10mil"),
            regle("HoleToHoleClearance", "vias", 1, "IsVia", "All", GAP="10mil"),
            clearance("polygones", 4, "inpolygon and innetclass('Power')", "All", 12),
            clearance("salle", 5, "WithinRoom('R1')", "All", 12),
            clearance("syntaxe", 6, "All", "InNet('a' Or", 12),
            regle("MinimumSolderMaskSliver", "sliver", 1, "All"),
            regle("FanoutControl", "fanout", 1, "(CompPinCount < 5)"),
            regle("AssemblyTestpoint", "tp1", 1, "All"), regle("FabricationTestPointUsage", "tp2", 1, "All"),
            regle("SilkToBoardRegionClearance", "silk", 1, "All"), regle("PasteMaskExpansion", "pate", 1, "All"),
            regle("PolygonConnect", "poly", 1, "InPadClass('x')"),
            dict(clearance("inactive", 1, "WithinRoom('R2')", "All", 1), active=False),
            clearance("defaut", 7, "All", "All", 7.874),
            regle("ComponentClearance", "pieces", 2, "All", "All", GAP="10mil"),
            regle("HoleToHoleClearance", "trous", 2, "All", "All", GAP="10mil"),
        ]
        res = P.interpreter(regles, [], {"reseaux": ["A"]})
        ni = {(r["type"], r["nom"]): r for r in res["regles"]["non_interpretees"]}
        attendu = {
            ("Clearance", "couche"): "prédicat de couche dans une règle Clearance",
            ("Width", "largeur_couche"): "prédicat de couche dans une règle Width",
            ("Clearance", "meme_reseau"): "portée de réseau « SameNet » non prise en charge (seule « réseaux "
                                          "différents » l'est)",
            ("Clearance", "couches_adjacentes"): "portée de couches « AdjacentLayers » non prise en charge (seule "
                                                 "« même couche » l'est)",
            ("ComponentClearance", "piece_U1"): "portée autre que All × All",
            ("HoleToHoleClearance", "vias"): "portée autre que All × All",
            ("Clearance", "polygones"): "polygones",
            ("Clearance", "salle"): "prédicat WithinRoom non pris en charge",
            ("MinimumSolderMaskSliver", "sliver"): "type non pris en charge",
        }
        for cle, cause in attendu.items():
            self.assertIn(cle, ni)
            self.assertEqual(ni[cle]["cause"], cause, cle)
        syntaxe = ni[("Clearance", "syntaxe")]
        self.assertTrue(syntaxe["cause"].startswith("portée 2 hors du langage : "), syntaxe)
        self.assertIn("(position", syntaxe["cause"])
        self.assertEqual(set(ni), set(attendu) | {("Clearance", "syntaxe")})
        self.assertEqual(ni[("Clearance", "polygones")]["portee1"], "inpolygon and innetclass('Power')")
        self.assertEqual(ni[("Clearance", "polygones")]["portee2"], "All")
        self.assertEqual(res["regles"]["ignorees"],
                         ["AssemblyTestpoint", "FabricationTestPointUsage", "FanoutControl", "PasteMaskExpansion",
                          "PolygonConnect", "SilkToBoardRegionClearance"])
        # les règles interprétables restent appliquées
        self.assertEqual(len(res["regles"]["isolement"]["classes"]), 1)
        self.assertProche(res["regles"]["isolement"]["defaut"], 0.2, 1e-6)
        self.assertProche(res["regles"]["isolement_pieces"], 0.254, 1e-9)
        self.assertProche(res["regles"]["trou_trou"], 0.254, 1e-9)

    def test_regles_unaires(self):
        regles = [
            regle("Width", "W_PUISSANCE", 1, "InNetClass('PUISSANCE')", MINLIMIT="20mil", PREFEREDWIDTH="40mil",
                  MAXLIMIT="400mil"),
            regle("Width", "Width", 2, "All", MINLIMIT="8mil", PREFEREDWIDTH="10mil", MAXLIMIT="100mil"),
            regle("RoutingVias", "V_PUISSANCE", 1, "InNetClass('PUISSANCE')", WIDTH="40mil", HOLEWIDTH="20mil"),
            regle("RoutingVias", "RoutingVias", 2, "All", WIDTH="24mil", HOLEWIDTH="12mil", MAXWIDTH="30mil"),
            regle("RoutingPriority", "prio", 1, "InNet('CLK')", ROUTINGPRIORITY="5"),
            regle("RoutingLayers", "couches_puissance", 1, "InNetClass('PUISSANCE')",
                  **{"TOP LAYER_V5": "TRUE", "MID LAYER 1_V5": "FALSE", "BOTTOM LAYER_V5": "FALSE"}),
            regle("RoutingLayers", "couches", 2, "All",
                  **{"TOP LAYER_V5": "TRUE", "MID LAYER 1_V5": "FALSE", "BOTTOM LAYER_V5": "TRUE"}),
            regle("SupplyNets", "alim", 1, "InNet('+12V')"),
            regle("ViasUnderSMD", "pas_sous_cms", 1, "Not InComponent('U1')", ALLOWED="FALSE"),
            regle("SolderMaskExpansion", "vias", 1, "IsVia", EXPANSION="0mil"),
            regle("SolderMaskExpansion", "masque", 2, "All", EXPANSION="4mil"),
            regle("Height", "J1", 1, "InComponent('J1')", MAXHEIGHT="196.8504mil"),
            regle("Height", "dessous", 2, "OnBottomLayer", MAXHEIGHT="53.1496mil"),
            regle("Height", "dessus", 3, "OnTopLayer", MAXHEIGHT="94.4882mil"),
            regle("BoardOutlineClearance", "bord", 1, "All", GAP="7.874mil", GENERICCLEARANCE="7.874mil"),
            regle("PlaneClearance", "plan", 1, "All", CLEARANCE="20mil"),
            regle("PlaneConnect", "connexion", 1, "All", PLANECONNECTSTYLE="Relief"),
            regle("SilkToSolderMaskClearance", "silk", 1, "IsPad", MINSILKSCREENTOMASKGAP="10mil"),
        ]
        ctx = {"reseaux": ["+12V", "CLK", "SIG"], "classes": {"PUISSANCE": ["+12V"]},
               "couches": ["TopLayer", "MidLayer1", "BottomLayer"],
               "pieces": [{"designateur": "J1", "empreinte": "", "face": "dessus"},
                          {"designateur": "U1", "empreinte": "", "face": "dessus"}]}
        objets = [pastille("U1", "1", "SIG", masque_regle=True), pastille("R1", "1", "SIG", masque_regle=True),
                  pastille("J1", "1", "+12V", cms=False, couches=("TopLayer", "MidLayer1", "BottomLayer"))]
        res = P.interpreter(regles, objets, ctx)
        rg, rr = res["regles"], res["regles"]["reseaux"]
        self.assertListeProche(rr["+12V"]["largeurs"], [0.508, 1.016, 10.16], 1e-9)
        self.assertTrue(rr["+12V"]["regle_largeur_propre"])
        self.assertListeProche(rr["SIG"]["largeurs"], [0.2032, 0.254, 2.54], 1e-9)
        self.assertFalse(rr["SIG"]["regle_largeur_propre"])
        self.assertListeProche(rg["largeur_defaut"], [0.2032, 0.254, 2.54], 1e-9)
        self.assertProche(rr["+12V"]["via"]["diametre"], 1.016, 1e-9)
        self.assertIsNone(rr["+12V"]["via"]["diametre_max"])
        self.assertProche(rg["via"]["diametre_max"], 0.762, 1e-9)
        self.assertEqual([rr[n]["priorite_routage"] for n in ("+12V", "CLK", "SIG")], [0, 5, 0])
        self.assertEqual(rr["+12V"]["couches_permises"], ["TopLayer"])
        self.assertEqual(rr["SIG"]["couches_permises"], ["TopLayer", "BottomLayer"])
        self.assertEqual(res["couches"], {"TopLayer": {"routage_permis": True}, "MidLayer1": {"routage_permis": False},
                                          "BottomLayer": {"routage_permis": True}})
        self.assertEqual([rr[n]["alimentation_schema"] for n in ("+12V", "CLK", "SIG")], [True, False, False])
        # ViasUnderSMD : U1 n'est couverte par aucune règle -> permis ; R1 -> interdit ; traversante -> permis
        self.assertEqual([o["via_dans_pastille_permis"] for o in res["objets"]], [True, False, True])
        self.assertEqual([o.get("expansion_masque") for o in res["objets"]], [0.1016, 0.1016, None])
        # hauteurs de face et hauteur propre à une pièce
        self.assertListeProche([rg["hauteurs"]["dessus"], rg["hauteurs"]["dessous"]], [2.4, 1.35], 1e-5)
        self.assertEqual(set(rg["pieces"]), {"J1"})
        self.assertProche(rg["pieces"]["J1"]["hauteur_max"], 5.0, 1e-5)
        self.assertProche(rg["garde_bord"], 0.2, 1e-6)
        self.assertEqual(rg["connexion_plans"], {"isolement": 0.508, "connexion": "relief"})
        self.assertProche(rg["masque_serigraphie"], 0.254, 1e-9)
        self.assertEqual(rg["non_interpretees"], [])

    def test_sans_regle_vias_sous_cms(self):
        res = P.interpreter([], [pastille("U1", "1", "A"), pastille("J1", "1", "A", cms=False)], {"reseaux": ["A"]})
        self.assertEqual([o["via_dans_pastille_permis"] for o in res["objets"]], [False, True])
        self.assertTrue(any("ViasUnderSMD" in a for a in res["avertissements"]), res["avertissements"])

    def test_valeurs_illisibles(self):
        regles = [regle("Width", "w", 1, "All", MINLIMIT="x"), regle("PlaneConnect", "pc", 1, "All",
                                                                     PLANECONNECTSTYLE="NoConnect")]
        res = P.interpreter(regles, [], {"reseaux": ["A"]})
        causes = {r["nom"]: r["cause"] for r in res["regles"]["non_interpretees"]}
        self.assertTrue(causes["w"].startswith("valeurs non reconnues"), causes)
        self.assertIn("NoConnect", causes["pc"])
        self.assertIsNone(res["regles"]["largeur_defaut"])


# =====================================================================================================================
# METROID rev3 (règles et profils lus par l'API actuelle de format_altium)
# =====================================================================================================================
def profils_altium(doc, designateurs, couches):
    """Profils des objets conducteurs d'une carte, par l'API actuelle de format_altium : pastilles (et trous),
    cuivre d'empreinte (régions), pistes libres de cuivre hors polygone, vias."""
    import regions_empreintes as RE
    objets = []
    pads_par_piece = {}
    for p in doc.pastilles():
        piece = designateurs[p["composant"]] if p["composant"] != 0xFFFF else None
        th = p["couche"] == "MULTILAYER"
        if th:
            t = "trou" if piece is None and not p["reseau"] and p["trou"] > 0 else "pastille_traversante"
        else:
            t = "pastille_cms"
        f = {"type": t, "reseau": p["reseau"] or "", "piece": piece, "pastille": p["nom"],
             "couches": list(couches) if th else [nom_commun(p["couche"])], "masque_regle": p["masque_mode"] == 1}
        objets.append(f)
        if piece is not None:
            pads_par_piece.setdefault(piece, {})[p["nom"]] = p["reseau"] or ""
    for des, regions in RE.regions_par_composant(doc).items():
        for r in regions:
            reseau = pads_par_piece.get(des, {}).get(r.get("pastille"), r.get("reseau", ""))
            objets.append({"type": "region", "reseau": reseau or "", "piece": des, "couches": []})
    for t in doc.pistes():
        c = nom_commun(t["couche"])
        if c and t["polygone"] == 0xFFFF and not t["keepout"]:
            piece = designateurs[t["composant"]] if t["composant"] != 0xFFFF else None
            objets.append({"type": "piste", "reseau": t["reseau"] or "", "piece": piece, "couches": [c]})
    for v in doc.vias():
        objets.append({"type": "via", "reseau": v["reseau"] or "", "piece": None, "couches": list(couches)})
    return objets


@unittest.skipUnless(os.path.exists(REV3), "PCB_METROID_V2_rev3.PcbDoc absent de la racine du dépôt")
class TestMetroidRev3(AssertionsProches, unittest.TestCase):
    MATRICE = [[0.25, 0.25, 0.25], [0.25, 0.12446, 0.2], [0.25, 0.2, 0.2]]
    PAS_FIN = {"M1", "M2", "U10"}
    LARGEURS_MIL = {"CANAL_2A": (19.685, 59.0551, 590.551), "RAILS": (10, 20, 196.85),
                    "PUISSANCE_8V": (19.685, 79, 590.551), "ENTREE_12V": (11.811, 98, 590.551),
                    "TRIG_50R": (13.78, 14, 15.748), "SENSE": (11.811, 12, 19.685),
                    "COMMUTATION": (10, 11.811, 118.1102)}          # COMMUTATION : règle Width par défaut (All)
    LARGEURS_MM = {"CANAL_2A": (0.5, 1.5, 15.0), "RAILS": (0.254, 0.508, 5.0), "PUISSANCE_8V": (0.5, 2.0066, 15.0),
                   "ENTREE_12V": (0.3, 2.4892, 15.0), "TRIG_50R": (0.35, 0.3556, 0.4), "SENSE": (0.3, 0.3048, 0.5),
                   "COMMUTATION": (0.254, 0.3, 3.0)}   # §3.9 et valeurs saisies en mm (le fichier les garde en mil,
    #                                                      arrondies à 0,01 mil près : 13,78 mil = 0,350012 mm)

    @classmethod
    def setUpClass(cls):
        import pcbdoc_serigraphie as S        # désignateurs : fonction existante (remplacée en B-ET5)
        doc = ouvrir(REV3)
        cls.ctx = contexte_altium(doc)
        cls.objets = profils_altium(doc, S.designateurs(doc), cls.ctx["couches"])
        cls.res = P.interpreter(regles_altium(doc), cls.objets, cls.ctx)
        cls.iso = cls.res["regles"]["isolement"]

    def test_classes_et_matrice(self):
        iso, rr = self.iso, self.res["regles"]["reseaux"]
        self.assertEqual(self.ctx["classes"]["COMMUTATION"], ["NetC19_1"])
        self.assertEqual(len(iso["classes"]), 3)
        self.assertListeProche(iso["matrice"], self.MATRICE, 1e-6)
        self.assertProche(iso["defaut"], 0.2, 1e-6)
        self.assertEqual(iso["classe_reference"], 2)
        # ordre : commutation hors M1, M2, U10 ; objets de M1, M2, U10 ; autres objets
        self.assertEqual(rr["NetC19_1"]["classes_isolement"], {"piste": 0, "via": 0, "surface": 0})
        for n in self.ctx["reseaux"]:
            if n != "NetC19_1":
                self.assertEqual(rr[n]["classes_isolement"], {"piste": 2, "via": 2, "surface": 2}, n)
        par_classe = {0: 0, 1: 0, 2: 0}
        for f, o in zip(self.objets, self.res["objets"]):
            if f["piece"] in self.PAS_FIN:
                attendu = 1
            elif f["reseau"] == "NetC19_1":
                attendu = 0
            else:
                attendu = 2
            self.assertEqual(o["classe_isolement"], attendu, f)
            par_classe[attendu] += 1
        self.assertTrue(all(par_classe.values()), par_classe)     # chaque classe a des objets existants

    def test_isolement_heuristique(self):
        rr = self.res["regles"]["reseaux"]
        self.assertEqual(len(rr), 259)
        for n, r in rr.items():
            self.assertProche(P.isolement_heuristique(self.iso, r["classes_isolement"]["piste"]),
                              0.25 if n == "NetC19_1" else 0.2, 1e-6, n)

    def test_vias_sous_cms(self):
        permis_cms = set()
        n_cms = 0
        for f, o in zip(self.objets, self.res["objets"]):
            if f["type"] == "pastille_cms":
                n_cms += 1
                self.assertEqual(o["via_dans_pastille_permis"], f["piece"] in ("L1", "U10"), f)
                if o["via_dans_pastille_permis"]:
                    permis_cms.add(f["piece"])
            elif f["type"] in P.TYPES_PASTILLE:
                self.assertTrue(o["via_dans_pastille_permis"], f)
        self.assertEqual(permis_cms, {"L1", "U10"})
        self.assertGreater(n_cms, 500)

    def test_largeurs(self):
        rg = self.res["regles"]
        self.assertListeProche(rg["largeur_defaut"], [0.254, 0.3, 3.0], 1e-6)
        self.assertEqual(len(self.LARGEURS_MIL), len(self.ctx["classes"]))      # les 7 classes de la carte
        for classe, membres in self.ctx["classes"].items():
            for n in membres:
                r = rg["reseaux"][n]
                self.assertListeProche(r["largeurs"], [x * MIL for x in self.LARGEURS_MIL[classe]], 1e-9, n)
                self.assertListeProche(r["largeurs"], self.LARGEURS_MM[classe], 1e-4, n)
                self.assertEqual(r["regle_largeur_propre"], classe != "COMMUTATION", n)

    def test_autres_regles(self):
        rg = self.res["regles"]
        self.assertProche(rg["trou_trou"], 0.254, 1e-9)
        self.assertProche(rg["isolement_pieces"], 0.254, 1e-9)
        self.assertIsNone(rg["garde_bord"])
        via = [rg["via"][k] for k in ("diametre", "trou", "diametre_min", "diametre_max", "trou_min", "trou_max")]
        self.assertListeProche(via, [x * MIL for x in (23.622, 11.811, 23.622, 31.496, 11.811, 15.748)], 1e-9)
        self.assertListeProche(via, [0.6, 0.3, 0.6, 0.8, 0.3, 0.4], 2e-6)
        self.assertEqual(rg["connexion_plans"], {"isolement": 0.508, "connexion": "relief"})
        alim = sorted(n for n, r in rg["reseaux"].items() if r["alimentation_schema"])
        self.assertEqual(alim, sorted(["+5V", "+6.5Vref", "GND", "+10V", "ALIM_OFF", "-2.5Vref", "+8V", "+2.5Vref",
                                       "+5Vref", "+12V", "-5V"]))
        self.assertEqual({c: v["routage_permis"] for c, v in self.res["couches"].items()},
                         {"TopLayer": True, "MidLayer1": True, "MidLayer2": True, "BottomLayer": True})
        self.assertIn("FanoutControl", rg["ignorees"])
        self.assertIn("AssemblyTestpoint", rg["ignorees"])
        types_ni = sorted({r["type"] for r in rg["non_interpretees"]})
        self.assertEqual(types_ni, ["MinimumSolderMaskSliver", "UnpouredPolygon"], rg["non_interpretees"])
        self.assertTrue(all(r["cause"] == "type non pris en charge" for r in rg["non_interpretees"]))


# =====================================================================================================================
# Cartes d'exemple Altium (collectées seulement avec --cartes-locales)
# =====================================================================================================================
class CasCartesLocales(AssertionsProches):
    CARTES = (SL1, WIFI, SODIMM, BLUETOOTH)
    _docs = {}

    def doc(self, nom):
        chemin = os.path.join(CARTES_LOCALES, nom)
        self.assertTrue(os.path.exists(chemin), f"carte d'exemple absente : {nom}")
        if nom not in self._docs:
            self._docs[nom] = ouvrir(chemin)
        return self._docs[nom]

    def test_portees_actives_compilees_ou_erreur_portee(self):
        for nom in self.CARTES:
            doc = self.doc(nom)
            compilees, erreurs = 0, 0
            for r in doc.regles():
                if not r["active"]:
                    continue
                for portee in (r["portee1"], r["portee2"]):
                    try:
                        P.compiler(portee)
                        compilees += 1
                    except P.ErreurPortee:
                        erreurs += 1
            self.assertGreater(compilees, 0, nom)
            # et la résolution complète passe sans exception
            res = P.interpreter(regles_altium(doc), [], contexte_altium(doc))
            self.assertEqual(set(res["regles"]), {
                "isolement", "reseaux", "largeur_defaut", "via", "trou_trou", "garde_bord", "isolement_pieces",
                "hauteurs", "pieces", "connexion_plans", "masque_serigraphie", "salles", "tolerances_longueur",
                "non_interpretees", "ignorees"}, nom)

    def test_sl1_portees_lues(self):
        doc = self.doc(SL1)
        portees = {p for r in doc.regles() for p in (r["portee1"], r["portee2"])}   # règles actives et inactives
        largeur, pieces = "innetclass('Power') and not inpolygon", "Not InComponent('NT1') && Not InComponent('LED8')"
        self.assertIn(largeur, portees)
        self.assertIn(pieces, portees)
        index = P.index_contexte(contexte_altium(doc))
        a = P.compiler(largeur)
        self.assertTrue(P.evaluer(a, P.profil_routeur("5V", "piste", index)))
        self.assertFalse(P.evaluer(a, P.profil_routeur("SW0", "piste", index)))
        self.assertFalse(P.evaluer(a, P.completer_profil({"type": "region", "reseau": "5V", "polygone": "P"}, index)))
        b = P.compiler(pieces)
        for des, attendu in (("NT1", False), ("LED8", False), ("U1", True), (None, True)):
            self.assertEqual(P.evaluer(b, P.completer_profil({"type": "pastille_cms", "piece": des}, index)), attendu)

    def test_sl1_resolution(self):
        doc = self.doc(SL1)
        ctx = contexte_altium(doc)
        res = P.interpreter(regles_altium(doc), [], ctx)
        rg = res["regles"]
        ni = {r["nom"]: r for r in rg["non_interpretees"]}
        self.assertEqual(ni["Clearance_Power Polygon"]["cause"], "polygones")
        self.assertEqual(ni["Clearance_Power Polygon"]["portee1"], "inpolygon and innetclass('Power')")
        self.assertListeProche(rg["largeur_defaut"], [0.2, 0.2, 2.0], 1e-5)
        power = ctx["classes"]["Power"]
        self.assertEqual(sorted(power), sorted(["5V", "VCCINT", "GND", "VCCO"]))
        for n in power:
            self.assertListeProche(rg["reseaux"][n]["largeurs"], [0.3, 0.3, 2.0], 1e-5, n)
            self.assertTrue(rg["reseaux"][n]["regle_largeur_propre"], n)
            self.assertListeProche([rg["reseaux"][n]["via"][k] for k in ("diametre", "trou", "diametre_max",
                                                                        "trou_max")], [0.9, 0.6, 1.5, 0.8], 1e-5, n)
        self.assertListeProche([rg["via"][k] for k in ("diametre", "trou")], [0.9, 0.6], 1e-5)
        self.assertProche(rg["isolement_pieces"], 0.127, 1e-9)
        self.assertEqual(len(rg["isolement"]["classes"]), 1)            # une seule règle Clearance interprétée
        self.assertProche(rg["isolement"]["defaut"], 0.2, 1e-6)

    def test_wifi_resolution(self):
        doc = self.doc(WIFI)
        ctx = contexte_altium(doc)
        self.assertEqual(ctx["paires"], [{"nom": "PA12_USB_D", "positif": "PA12_USB_D_P",
                                          "negatif": "PA12_USB_D_N"}])
        res = P.interpreter(regles_altium(doc), [], ctx)
        rg = res["regles"]
        iso, rr = rg["isolement"], rg["reseaux"]
        k = {n: rr[n]["classes_isolement"] for n in ctx["reseaux"]}
        # une classe pour les pistes des réseaux de la paire, à 0,1 mm entre elles
        kp = k["PA12_USB_D_P"]["piste"]
        self.assertEqual(k["PA12_USB_D_N"]["piste"], kp)
        self.assertProche(iso["matrice"][kp][kp], 0.1, 1e-6)
        autres = [n for n in ctx["reseaux"] if n not in ("PA12_USB_D_P", "PA12_USB_D_N")]
        self.assertTrue(all(k[n]["piste"] != kp for n in autres))
        # matrice OBJECTCLEARANCES non uniforme (règle Clearance) : piste contre surface 0,35, piste contre piste 0,2
        a, b = autres[0], autres[1]
        self.assertProche(iso["matrice"][k[a]["piste"]][k[b]["surface"]], 0.35, 1e-6)
        self.assertProche(iso["matrice"][k[a]["piste"]][k[b]["piste"]], 0.2, 1e-6)
        self.assertListeProche([rg["hauteurs"]["dessus"], rg["hauteurs"]["dessous"]], [2.4, 1.35], 1e-5)
        self.assertProche(rg["isolement_pieces"], 0.254, 1e-9)
        self.assertProche(rg["garde_bord"], 0.2, 1e-6)
        paire = res["paires"]["PA12_USB_D"]
        self.assertProche(paire["ecart_mm"], 0.12, 1e-6)
        self.assertListeProche(paire["largeurs"], [0.25, 0.25, 0.25], 1e-6)


def load_tests(loader, tests, pattern):
    """Ajoute les tests des cartes d'exemple quand --cartes-locales est donné ; sinon le dit."""
    if CARTES_LOCALES:
        classe = type("TestCartesLocales", (CasCartesLocales, unittest.TestCase), {"__module__": __name__})
        tests.addTests(loader.loadTestsFromTestCase(classe))
    else:
        sys.stderr.write("test_portees : tests des cartes d'exemple non lancés (option --cartes-locales absente)\n")
    return tests


def _lire_options(argv):
    """Retire --cartes-locales DOSSIER (ou --cartes-locales=DOSSIER) de argv ; rend (dossier, reste)."""
    dossier, reste, i = None, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--cartes-locales":
            if i + 1 >= len(argv):
                sys.stderr.write("--cartes-locales : dossier attendu\n")
                sys.exit(2)
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
