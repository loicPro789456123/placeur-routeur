"""Fabrique de cartes .PcbDoc synthétiques et déterministes pour les tests publics (aucune carte réelle).

    racine = carte(absents=("Vias6",), vides=("Arcs6",), empilage="hors_ordre", panneau=True)
    chemin = ecrire(racine, dossier)            # fichier CFB écrit par altium_fichier.cfb
    chemin = fabriquer(dossier, **options)      # les deux à la fois

Contenu de la carte (coordonnées absolues en mil, origine affichée (1000, 1000)) :
- contour rectangulaire (1000, 1000)-(3000, 2000) dans Board6 et une région de carte (BoardRegions) identique ;
- empilage « normal » : TOP, PLANE1 (plan négatif GND, retrait 20 mil), MID1, BOTTOM (+ couches non cuivre) ;
  « hors_ordre » : TOP, MID2, MID1, BOTTOM (la numérotation ne suit pas l'ordre physique) ; « v8 » : même
  empilage que « normal » en clés LAYER_V8_* seulement ; « v6 » : sans clés V9 ni V8 (repli sur la chaîne
  LAYER<n>NEXT : TOP, MID1, BOTTOM) ;
- réseaux GND, VCC, SIG, D_P, D_N ; classe « Alim » ; paire différentielle « D » ;
- 4 composants : U1 (désignateur affiché, verrouillé, commentaire), « Ω1 » (désignateur en chaîne large),
  C7 (sans texte de désignateur : repli SOURCEDESIGNATOR ; dessous ; COMPONENTKIND 4), sans aucun désignateur
  (repli « __piece_3 », COMPONENTKIND 2) ;
- pastilles de U1 (rang = ordre du fichier) : 0 rect, 1 rond, 2 oblong, 3 rectangle arrondi 25 %, 4 octogone
  traversant, 5 trou oblong tourné de 90°, 6 trou décalé (10, -5), 7 pile haut-milieu-bas, 8 trou non métallisé,
  9 pile complète ; une pastille CMS dessous pour C7 ; une pastille libre GND ;
- pistes (dont une ligne de partage du plan et un trait de retrait), arc keep-out, vias, remplissage, régions
  (cuivre à un trou, keep-out), textes, polygones « Split Plane » (GND et VCC sur PLANE1), règles (dont une
  portée Not (... Or ...)), corps 3D, types de couches mécaniques ; panneau=True ajoute un enregistrement
  EmbeddedBoards6.
`absents` : stockages non créés ; `vides` : stockages créés avec un Data vide (Header = 0).
"""
import os
import struct
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
if RACINE not in sys.path:
    sys.path.insert(0, RACINE)

from altium_fichier import cfb  # noqa: E402
from altium_fichier import gabarits as G  # noqa: E402
from altium_fichier.proprietes import Proprietes  # noqa: E402

AUCUN = 0xFFFF
MIL = 10000  # unités internes par mil
RESEAUX = ["GND", "VCC", "SIG", "D_P", "D_N"]
GND, VCC, SIG, D_P, D_N = range(5)
MULTI, TOP, MID1, MID2, BOTTOM, PLANE1, TOPOVERLAY, KEEPOUT, MECA13 = 74, 1, 2, 3, 32, 39, 33, 56, 69
V7 = {TOP: 0x01000001, MID1: 0x01000002, MID2: 0x01000003, BOTTOM: 0x0100FFFF, PLANE1: 0x01010001,
      TOPOVERLAY: 0x01030006, KEEPOUT: 0x0103000D, MULTI: 0x0103000F, MECA13: 0x0102000D}
CONTOUR = [(1000, 1000), (3000, 1000), (3000, 2000), (1000, 2000)]
DOC_PANNEAU = "dossier\\sous-dossier\\Carte.PcbDoc"  # chemin relatif : seul le nom doit être rendu


# ---------------------------------------------------------------------------
# briques
# ---------------------------------------------------------------------------
def _i(mil):
    return int(round(mil * MIL))


def _props(paires, initial=True):
    """Octets d'une liste de propriétés texte « |CLE=VALEUR|...\\0 »."""
    elements = ([["", None]] if initial else []) + [[k, v] for k, v in paires]
    return Proprietes(elements).vers_octets()


def _enr(corps, prefixe=b""):
    return prefixe + struct.pack("<I", len(corps)) + corps


def _prim(type_, *sous):
    return bytes([type_]) + b"".join(struct.pack("<I", len(s)) + s for s in sous)


def _bloc(gabarit, longueur, **v):
    return G.encoder_bloc(G.nouveau_bloc(gabarit, longueur, **v))


def _commun(couche, reseau=AUCUN, composant=AUCUN, polygone=AUCUN, drapeaux1=0x0C, drapeaux2=0):
    return dict(couche=couche, drapeaux1=drapeaux1, drapeaux2=drapeaux2, reseau=reseau, polygone=polygone,
                composant=composant, inconnu_ffffffff=0xFFFFFFFF)


def _coords(valeurs):
    return struct.pack(f"<{len(valeurs)}i", *valeurs)


def piste(couche, x1, y1, x2, y2, largeur, reseau=AUCUN, composant=AUCUN, polygone=AUCUN, keepout=0):
    b = _bloc(G.PISTE, 49, **_commun(couche, reseau, composant, polygone, drapeaux2=2 if keepout else 0),
              x1=_i(x1), y1=_i(y1), x2=_i(x2), y2=_i(y2), largeur=_i(largeur), couche_v7=V7.get(couche, 0),
              restrictions_keepout=keepout)
    return _prim(4, b)


def arc(couche, cx, cy, rayon, a1, a2, largeur, reseau=AUCUN, composant=AUCUN, polygone=AUCUN, keepout=0):
    b = _bloc(G.ARC, 60, **_commun(couche, reseau, composant, polygone, drapeaux2=2 if keepout else 0),
              cx=_i(cx), cy=_i(cy), rayon=_i(rayon), angle_debut=float(a1), angle_fin=float(a2),
              largeur=_i(largeur), couche_v7=V7.get(couche, 0), restrictions_keepout=keepout)
    return _prim(1, b)


def via(x, y, diametre, trou, reseau=AUCUN, composant=AUCUN):
    b = _bloc(G.VIA, 321, **_commun(MULTI, reseau, composant), x=_i(x), y=_i(y), diametre=_i(diametre),
              trou=_i(trou), couche_debut=TOP, couche_fin=BOTTOM, mode_pile=0,
              diametres_couches=_coords([_i(diametre)] * 32), couche_v7=V7[MULTI])
    return _prim(3, b)


def remplissage(couche, x1, y1, x2, y2, rotation=0.0, reseau=AUCUN, composant=AUCUN):
    b = _bloc(G.REMPLI, 50, **_commun(couche, reseau, composant), x1=_i(x1), y1=_i(y1), x2=_i(x2), y2=_i(y2),
              rotation=float(rotation), couche_v7=V7.get(couche, 0))
    return _prim(6, b)


def texte(couche, x, y, chaine, composant=AUCUN, designateur=False, commentaire=False, index_large=None):
    b = _bloc(G.TEXTE, 252, **_commun(couche, AUCUN, composant), x=_i(x), y=_i(y), hauteur=_i(60),
              est_designateur=int(designateur), est_commentaire=int(commentaire),
              index_chaine_large=index_large if index_large is not None else 0xFFFFFFFF,
              couche_v7=V7.get(couche, 0))
    court = chaine.encode("latin-1", "replace")
    return _prim(5, b, bytes([len(court)]) + court)


def pastille(nom, couche, x, y, taille, forme=2, trou=0.0, composant=AUCUN, reseau=AUCUN, rotation=0.0,
             metallise=True, mode=0, milieu=None, bas=None, pile=None):
    """taille, milieu, bas : (x, y, forme) en mil ; pile : dict des champs de PASTILLE_PILE (651 octets)."""
    tx, ty = taille
    mx, my, fm = milieu if milieu is not None else (tx, ty, forme)
    bx, by, fb = bas if bas is not None else (tx, ty, forme)
    b = _bloc(G.PASTILLE, 202, **_commun(couche, reseau, composant, drapeaux1=0x04), x=_i(x), y=_i(y),
              taille_haut_x=_i(tx), taille_haut_y=_i(ty), taille_milieu_x=_i(mx), taille_milieu_y=_i(my),
              taille_bas_x=_i(bx), taille_bas_y=_i(by), trou=_i(trou), forme_haut=forme, forme_milieu=fm,
              forme_bas=fb, rotation=float(rotation), metallise=int(metallise), mode_pile=mode,
              masque_mode=1, pate_mode=1)
    s5 = b""
    if pile is not None:
        champs = {"formes_alt": bytes([1] * 32)}
        champs.update(pile)
        s5 = _bloc(G.PASTILLE_PILE, 651, **champs)
    court = nom.encode("latin-1")
    return _prim(2, bytes([len(court)]) + court, b"\x00", b"\x04|&|0", b"\x00", b, s5)


def region(couche, contour, trous=(), reseau=AUCUN, composant=AUCUN, props=(), etendu=False, keepout=False):
    """Région (types 11/12) : en-tête 18 octets, propriétés, sommets (f64 ou étendus), trous."""
    e = _bloc(G.ENTETE_REGION, 18, **_commun(couche, reseau, composant, drapeaux2=2 if keepout else 0),
              nb_trous=len(trous))
    pb = _props(props, initial=False)
    out = e + struct.pack("<I", len(pb)) + pb
    if etendu:
        out += struct.pack("<I", len(contour))
        for x, y in list(contour) + [contour[0]]:
            out += struct.pack("<B5i2d", 0, _i(x), _i(y), 0, 0, 0, 0.0, 0.0)
    else:
        out += struct.pack("<I", len(contour)) + b"".join(struct.pack("<2d", _i(x), _i(y)) for x, y in contour)
    for t in trous:
        out += struct.pack("<I", len(t)) + b"".join(struct.pack("<2d", _i(x), _i(y)) for x, y in t)
    return _prim(11, out)


def _contour_props(points):
    out = []
    for k, (x, y) in enumerate(points):
        out += [(f"KIND{k}", "0"), (f"VX{k}", f"{x}mil"), (f"VY{k}", f"{y}mil")]
    return out


# ---------------------------------------------------------------------------
# Board6
# ---------------------------------------------------------------------------
_COUCHES_NORMAL = [("Top Overlay", 0x01030006, {}), ("Top Solder", 0x0103000A, {"DIELTYPE": "3"}),
                   ("Top Layer", 0x01000001, {"COPTHICK": "1.4mil"}), ("Dielectric 1", 0x01040001, {"DIELTYPE": "1"}),
                   ("Plan GND", 0x01010001, {"COPTHICK": "1.4mil", "PULLBACKDISTANCE": "20mil"}),
                   ("Dielectric 2", 0x01040002, {"DIELTYPE": "2"}),
                   ("Signal interne", 0x01000002, {"COPTHICK": "0.7mil"}), ("Dielectric 3", 0x01040003, {"DIELTYPE": "1"}),
                   ("Bottom Layer", 0x0100FFFF, {"COPTHICK": "1.4mil"}), ("Bottom Solder", 0x0103000B, {"DIELTYPE": "3"}),
                   ("Bottom Overlay", 0x01030007, {})]
_COUCHES_HORS_ORDRE = [("Top Layer", 0x01000001, {"COPTHICK": "1.4mil"}), ("Dielectric 1", 0x01040001, {}),
                       ("Interne haut", 0x01000003, {"COPTHICK": "0.7mil"}), ("Dielectric 2", 0x01040002, {}),
                       ("Interne bas", 0x01000002, {"COPTHICK": "0.7mil"}), ("Dielectric 3", 0x01040003, {}),
                       ("Bottom Layer", 0x0100FFFF, {"COPTHICK": "1.4mil"})]
ID_EMPILAGE = "{00000000-0000-4000-8000-000000000001}"


def _board6(empilage):
    p = [("RECORD", "Board"), ("FILENAME", "synthetique.PcbDoc"), ("VERSION", "5.01"),
         ("ORIGINX", "1000mil"), ("ORIGINY", "1000mil")] + _contour_props(CONTOUR)
    p += [("PLANE1NETNAME", "GND"), ("PLANE2NETNAME", "(No Net)\r")]
    if empilage in ("normal", "hors_ordre"):
        couches_v9 = _COUCHES_NORMAL if empilage == "normal" else _COUCHES_HORS_ORDRE
        p += [("V9_MASTERSTACK_ID", "{00000000-0000-4000-8000-000000000000}"),
              ("V9_MASTERSTACK_NAME", "Master layer stack"), ("V9_MASTERSTACK_ISFLEX", "FALSE"),
              ("V9_SUBSTACK0_ID", ID_EMPILAGE), ("V9_SUBSTACK0_NAME", "Board Layer Stack"),
              ("V9_SUBSTACK0_ISFLEX", "FALSE"), ("V9_SUBSTACK0_SERVICE", "FALSE"),
              ("V9_SUBSTACK0_USEDBYPRIMS", "FALSE"), ("V9_SUBSTACK0_TYPE", "1")]
        for i, (nom, lid, extra) in enumerate(couches_v9):
            p += [(f"V9_STACK_LAYER{i}_ID", f"{{00000000-0000-4000-8000-0000000001{i:02d}}}"),
                  (f"V9_STACK_LAYER{i}_NAME", nom), (f"V9_STACK_LAYER{i}_LAYERID", str(lid))]
            p += [(f"V9_STACK_LAYER{i}_{k}", v) for k, v in extra.items()]
    elif empilage == "v8":
        p += [("LAYERSUBSTACK_V8_0ID", ID_EMPILAGE), ("LAYERSUBSTACK_V8_0NAME", "Board Layer Stack"),
              ("LAYERSUBSTACK_V8_0ISFLEX", "FALSE")]
        for i, (nom, lid, extra) in enumerate(_COUCHES_NORMAL):
            p += [(f"LAYER_V8_{i}NAME", nom), (f"LAYER_V8_{i}LAYERID", str(lid))]
            p += [(f"LAYER_V8_{i}{k}", v) for k, v in extra.items()]
    # ancien empilage V6 (chaîne LAYER<n>PREV/NEXT) : TOP -> MID1 -> BOTTOM (repli quand V9 et V8 manquent)
    p += [("LAYER1NAME", "Top Layer"), ("LAYER1PREV", "0"), ("LAYER1NEXT", "2"), ("LAYER1COPTHICK", "1.4mil"),
          ("LAYER2NAME", "Signal interne"), ("LAYER2PREV", "1"), ("LAYER2NEXT", "32"), ("LAYER2COPTHICK", "0.7mil"),
          ("LAYER3NAME", "Mid-Layer 2"), ("LAYER3PREV", "0"), ("LAYER3NEXT", "0"),
          ("LAYER32NAME", "Bottom Layer"), ("LAYER32PREV", "2"), ("LAYER32NEXT", "0"), ("LAYER32COPTHICK", "1.4mil")]
    p += [("LAYERV7_0LAYERID", str(0x01020011)), ("LAYERV7_0NAME", "Mechanical 17"),
          ("LAYERV7_0MECHKIND", "AssemblyTop")]
    return _props(p)


# ---------------------------------------------------------------------------
def contenu(empilage="normal", panneau=False, essais=False):
    """{stockage: liste des enregistrements (octets)} de la carte synthétique. `essais` (fichiers d'essai de
    l'annexe E) : 3 pièces de plus (7 en tout), dont Z0 à 3 pastilles CMS en triangle (asymétrique), pastille
    « 2 » de U1 hors de l'axe X de la pièce et règle PlaneClearance de 20 mil."""
    c = {}
    c["Board6"] = [_enr(_board6(empilage))]
    c["Nets6"] = [_enr(_props([("NAME", n), ("VISIBLE", "TRUE")])) for n in RESEAUX]
    c["Classes6"] = [_enr(_props([("NAME", "Alim"), ("KIND", "0"), ("SUPERCLASS", "FALSE"), ("M0", "GND"), ("M1", "VCC")]))]
    c["DifferentialPairs6"] = [_enr(_props([("NAME", "D"), ("POSITIVENETNAME", "D_P"), ("NEGATIVENETNAME", "D_N")]))]
    composants = [
        [("X", "1500mil"), ("Y", "1500mil"), ("ROTATION", " 0.00000000000000E+0000"), ("LAYER", "TOP"),
         ("LOCKED", "TRUE"), ("PATTERN", "ESSAI10"), ("SOURCEDESIGNATOR", "U1"), ("FOOTPRINTDESCRIPTION", "boîtier d'essai"),
         ("SOURCELIBREFERENCE", "ESSAI"), ("HEIGHT", "40mil"), ("NAMEON", "TRUE"), ("COMMENTON", "TRUE")],
        [("X", "2500mil"), ("Y", "1500mil"), ("ROTATION", " 9.00000000000000E+0001"), ("LAYER", "TOP"),
         ("LOCKED", "FALSE"), ("PATTERN", "R0603"), ("SOURCEDESIGNATOR", "X9")],
        [("X", "2000mil"), ("Y", "1800mil"), ("ROTATION", " 1.80000000000000E+0002"), ("LAYER", "BOTTOM"),
         ("LOCKED", "FALSE"), ("PATTERN", "C0805"), ("SOURCEDESIGNATOR", "C7"), ("COMPONENTKIND", "4")],
        [("X", "2800mil"), ("Y", "1900mil"), ("ROTATION", " 0.00000000000000E+0000"), ("LAYER", "TOP"),
         ("PATTERN", "LOGO"), ("COMPONENTKIND", "2")],
    ]
    if essais:
        composants += [[("X", f"{1100 + 100 * k}mil"), ("Y", "1900mil"), ("ROTATION", " 0.00000000000000E+0000"),
                        ("LAYER", "TOP"), ("PATTERN", "ESSAI"), ("SOURCEDESIGNATOR", f"Z{k}")] for k in range(3)]
    c["Components6"] = [_enr(_props(p)) for p in composants]
    # chaînes larges : désignateur Unicode de la pièce 1, textes 8 bits des autres
    larges = ["U1", "Ω1", "Commentaire U1", "Texte libre"]
    c["WideStrings6"] = [struct.pack("<II", i, 2 * len(t) + 2) + t.encode("utf-16-le") + b"\0\0" for i, t in enumerate(larges)]
    c["Texts6"] = [
        texte(TOPOVERLAY, 1500, 1600, "U1", composant=0, designateur=True, index_large=0),
        texte(TOPOVERLAY, 2500, 1600, "?1", composant=1, designateur=True, index_large=1),
        texte(TOPOVERLAY, 1500, 1400, "Commentaire U1", composant=0, commentaire=True, index_large=2),
        texte(TOPOVERLAY, 1200, 1900, "Texte libre", index_large=3),
    ]
    pile_rect_arrondi = {"formes_alt": bytes([9] * 32), "rayons_coins": bytes([25] * 32)}
    pile_oblong = {"forme_trou": 2, "longueur_oblong": _i(78.74), "rotation_oblong": 90.0}
    pile_decale = {"decalages_trou_x": _coords([_i(10)] + [0] * 31), "decalages_trou_y": _coords([_i(-5)] + [0] * 31)}
    internes = [_i(55)] * 29
    pile_complete = {"tailles_x_internes": _coords(internes), "tailles_y_internes": _coords(internes),
                     "formes_internes": bytes([3] * 29)}
    c["Pads6"] = [
        pastille("1", TOP, 1400, 1500, (60, 40), forme=2, composant=0, reseau=GND),
        pastille("2", TOP, 1450, 1530 if essais else 1500, (50, 50), forme=1, composant=0, reseau=VCC),
        pastille("3", TOP, 1500, 1500, (40, 80), forme=1, composant=0, reseau=SIG),
        pastille("4", TOP, 1550, 1500, (40, 60), forme=1, composant=0, reseau=SIG, pile=pile_rect_arrondi),
        pastille("5", MULTI, 1600, 1500, (60, 60), forme=3, trou=30, composant=0, reseau=GND),
        pastille("6", MULTI, 1650, 1500, (63, 110), forme=1, trou=39.37, composant=0, reseau=VCC, pile=pile_oblong),
        pastille("7", MULTI, 1700, 1500, (80, 80), forme=1, trou=30, composant=0, reseau=SIG, pile=pile_decale),
        pastille("8", MULTI, 1750, 1500, (60, 60), forme=2, trou=30, composant=0, reseau=GND, mode=1,
                 milieu=(50, 50, 1), bas=(70, 70, 3)),
        pastille("9", MULTI, 1800, 1500, (100, 100), forme=1, trou=100, composant=0, metallise=False),
        pastille("10", MULTI, 1850, 1500, (60, 60), forme=2, trou=30, composant=0, reseau=VCC, mode=2,
                 milieu=(45, 45, 1), bas=(65, 65, 2), pile=pile_complete),
        pastille("1", BOTTOM, 2000, 1800, (39, 57), forme=2, composant=2, reseau=VCC, rotation=180.0),
        pastille("GND", TOP, 2900, 1100, (24, 24), forme=1, reseau=GND),
    ]
    if essais:  # Z0 : boîtier à 3 pastilles CMS (asymétrique, retournable sans anomalie)
        c["Pads6"] += [pastille(n, TOP, x, y, (30, 40), forme=2, composant=4, reseau=SIG)
                       for n, x, y in (("1", 1063, 1857), ("2", 1137, 1857), ("3", 1100, 1943))]
    c["Tracks6"] = [
        piste(TOP, 1100, 1100, 1400, 1100, 10, reseau=SIG),
        piste(PLANE1, 1200, 1200, 1600, 1200, 20),                        # ligne de partage du plan
        piste(PLANE1, 1000, 1000, 3000, 1000, 40, polygone=0xFFFE),       # trait de retrait (généré)
        piste(TOPOVERLAY, 1400, 1450, 1600, 1450, 6, composant=0),
    ]
    c["Arcs6"] = [arc(KEEPOUT, 2900, 1900, 50, 0, 360, 5, keepout=0x1F),
                  arc(TOP, 1200, 1800, 30, 0, 90, 8, reseau=SIG)]
    c["Vias6"] = [via(1300, 1300, 24, 12, reseau=GND), via(1320, 1300, 24, 12, reseau=GND)]
    c["Fills6"] = [remplissage(TOP, 2100, 1100, 2200, 1150, reseau=VCC)]
    zone = [(2300, 1100), (2600, 1100), (2600, 1400), (2300, 1400)]
    trou = [(2400, 1200), (2500, 1200), (2500, 1300), (2400, 1300)]
    props_cuivre = [("V7_LAYER", "TOP"), ("NAME", " "), ("KIND", "0"), ("SUBPOLYINDEX", "-1"), ("UNIONINDEX", "0"),
                    ("ARCRESOLUTION", "0.5mil"), ("ISSHAPEBASED", "FALSE"), ("CAVITYHEIGHT", "0mil")]
    ko = [(1100, 1700), (1300, 1700), (1300, 1900), (1100, 1900)]
    props_ko = [("V7_LAYER", "KEEPOUT"), ("NAME", " "), ("KIND", "0"), ("KEEPOUT", "TRUE"), ("KEEPOUTRESTRICTIONS", "3")]
    c["Regions6"] = [region(TOP, zone, [trou], reseau=GND, props=props_cuivre),
                     region(KEEPOUT, ko, props=props_ko, keepout=True)]
    c["ShapeBasedRegions6"] = [region(TOP, zone, [trou], reseau=GND, props=props_cuivre, etendu=True),
                               region(KEEPOUT, ko, props=props_ko, etendu=True, keepout=True)]
    props_carte = [("V7_LAYER", "MULTILAYER"), ("NAME", "Layer Stack Region 1"), ("LAYER", "KEEPOUT"),
                   ("KEEPOUT", "TRUE"), ("ISBOARDCUTOUT", "TRUE"), ("KIND", "0"), ("OBJECTKIND", "BoardRegion"),
                   ("BENDINGLINECOUNT", "0"), ("LOCKED3D", "FALSE"), ("LAYERSTACKID", ID_EMPILAGE)]
    c["BoardRegions"] = [region(KEEPOUT, CONTOUR, props=props_carte)]
    corps = [("V7_LAYER", "MECHANICAL13"), ("NAME", " "), ("KIND", "0"), ("STANDOFFHEIGHT", "0mil"),
             ("OVERALLHEIGHT", "50mil"), ("BODYPROJECTION", "0"), ("MODEL.NAME", "modeles\\essai.step")]
    c["ComponentBodies6"] = [region(MECA13, [(1350, 1450), (1900, 1450), (1900, 1550), (1350, 1550)],
                                    composant=0, props=corps)]
    split_gnd = [(1010, 1010), (2990, 1010), (2990, 1990), (1010, 1990)]
    split_vcc = [(1200, 1200), (1600, 1200), (1600, 1400), (1200, 1400)]
    c["Polygons6"] = [_enr(_props([("LAYER", "PLANE1"), ("POLYGONTYPE", "Split Plane"), ("NET", str(GND)),
                                   ("NAME", "Plan GND")] + _contour_props(split_gnd))),
                      _enr(_props([("LAYER", "PLANE1"), ("POLYGONTYPE", "Split Plane"), ("NET", str(VCC)),
                                   ("NAME", "Plan VCC")] + _contour_props(split_vcc)))]
    c["Rules6"] = [
        _enr(_props([("RULEKIND", "Clearance"), ("NAME", "Clearance"), ("ENABLED", "TRUE"), ("PRIORITY", "2"),
                     ("SCOPE1EXPRESSION", "Not (InComponent('U1') Or InComponent('C7'))"),
                     ("SCOPE2EXPRESSION", "All"), ("NETSCOPE", "DifferentNets"), ("GENERICCLEARANCE", "8mil")]),
             prefixe=b"\x00\x00"),
        _enr(_props([("RULEKIND", "Width"), ("NAME", "Largeur_Alim"), ("ENABLED", "TRUE"), ("PRIORITY", "1"),
                     ("SCOPE1EXPRESSION", "InNetClass('Alim')"), ("MINLIMIT", "10mil"), ("MAXLIMIT", "80mil"),
                     ("PREFEREDWIDTH", "20mil")]), prefixe=b"\x02\x00"),
    ]
    if essais:
        c["Rules6"].append(_enr(_props([("RULEKIND", "PlaneClearance"), ("NAME", "PlaneClearance"), ("ENABLED", "TRUE"),
                                        ("PRIORITY", "1"), ("SCOPE1EXPRESSION", "All"), ("CLEARANCE", "20mil")]),
                                prefixe=b"\x0c\x00"))
    c["EmbeddedBoards6"] = []
    if panneau:
        c["EmbeddedBoards6"] = [_enr(_props([("DOCUMENTPATH", DOC_PANNEAU), ("X", "1000mil"), ("Y", "1000mil"),
                                             ("ROTATION", " 0.00000000000000E+0000"), ("ROWCOUNT", "2"),
                                             ("COLCOUNT", "3"), ("ROWSPACING", "1100mil"), ("COLSPACING", "2100mil"),
                                             ("MIRROR", "FALSE")]))]
    v = "1.0".encode("utf-16-le")
    c["LayerKindMapping"] = [struct.pack("<I", len(v)) + v + struct.pack("<II", 0, 1) + struct.pack("<II", 0x04000011, 1)]
    return c


# ---------------------------------------------------------------------------
# options : PrimitiveGuids, DrillManager, chemins absolus
# ---------------------------------------------------------------------------
FLUX_GUIDS = ["Arcs6", "Pads6", "Vias6", "Tracks6", "Texts6", "Fills6", "Regions6", "ShapeBasedRegions6",
              "ComponentBodies6", "ShapeBasedComponentBodies6"]
TYPES_GUIDS = {"Arcs6": 0x01, "Pads6": 0x02, "Vias6": 0x03, "Tracks6": 0x04, "Texts6": 0x05, "Fills6": 0x06,
               "Regions6": 0x59, "ShapeBasedRegions6": 0x59, "ComponentBodies6": 0x5A, "ShapeBasedComponentBodies6": 0x5A}
ANTI = "\\"


def absolu(lecteur, *elements, sep=ANTI):
    """Chemin absolu fictif « <lecteur>:<sep><élément><sep>... » (lecteur "" : chemin réseau
    « <sep><sep><élément>... »), construit à l'exécution : aucun chemin absolu n'est écrit dans les sources
    (contrôle d'anonymat, §0.3)."""
    return (lecteur + ":" + sep if lecteur else sep * 2) + sep.join(elements)


# chemins absolus fictifs de l'option `chemins` (test_chemins) ; aucun ne désigne un dossier réel
CHEMIN_CARTE = absolu("C", "Users", "nom", "cartes", "essai.PcbDoc")
CHEMIN_BIBLIOTHEQUE = absolu("D", "bibli", "essai.PcbLib")
CHEMIN_PANNEAU = absolu("", "serveur", "partage", "carte.PcbDoc")
TEXTE_LARGE = "voir " + absolu("C", "dossier", "note.txt")
CHEMIN_BRUT = absolu("E", "projets", "essai", "x.SchDoc")
CHEMIN_STEP = absolu("C", "Users", "nom", "modele.step", sep=ANTI * 2)  # séparateurs doublés, comme dans un STEP
CHEMIN_EMPILAGE = absolu("F", "empilages", "carte.stackup")


def _guids(c):
    """PrimitiveGuids cohérent avec les flux de primitives de `c` (GUID déterministes)."""
    import uuid
    out = []
    for k, st in enumerate(FLUX_GUIDS):
        for i in range(len(c.get(st, []))):
            g = uuid.uuid5(uuid.NAMESPACE_URL, f"fabrique/{st}/{i}").bytes_le
            out.append(struct.pack("<II16s", ((0x0D + k) << 8) | TYPES_GUIDS[st], i, g))
    return out


def _percage_pastilles():
    """DrillManager avec un seul groupe, de pastilles (trous de 30 mil : pastilles 4, 6, 7 et 9 de U1) et
    aucun groupe de via, comme la carte source."""
    p = _props([("HOLEMARK", "0"), ("LAYERPAIR", "0"), ("OBJECTID", "Pad"), ("TOOLSIZE", "30mil"),
                ("TOOLLENGTH", "0mil"), ("HOLETYPE", "0"), ("SHAPE", "ROUND"), ("PLATED", "TRUE")], initial=True)
    pads = [4, 6, 7, 9]
    return (struct.pack("<iI", -1, 1) + struct.pack("<I", len(p)) + p
            + struct.pack("<I", len(pads)) + struct.pack(f"<{len(pads)}I", *pads) + struct.pack("<I", 0))


def _ajouter_chemins(c):
    """Champs porteurs de chemins absolus (option `chemins`)."""
    import base64
    import zlib
    b = Proprietes.depuis_octets(c["Board6"][0][4:])
    b["FILENAME"] = CHEMIN_CARTE
    xml = f'<StackupDocument><Fichier Chemin="{CHEMIN_EMPILAGE}"/></StackupDocument>'.encode("utf-8")
    b["V9_STACKCUSTOMDATA"] = base64.b64encode(zlib.compress(xml, 9)).decode("ascii")
    c["Board6"] = [_enr(b.vers_octets())]
    p = Proprietes.depuis_octets(c["Components6"][0][4:])
    p["SOURCEFOOTPRINTLIBRARY"] = CHEMIN_BIBLIOTHEQUE
    c["Components6"][0] = _enr(p.vers_octets())
    c["EmbeddedBoards6"] = [_enr(_props([("DOCUMENTPATH", CHEMIN_PANNEAU), ("X", "1000mil"), ("Y", "1000mil"),
                                         ("ROWCOUNT", "1"), ("COLCOUNT", "1")]))]
    t = TEXTE_LARGE.encode("utf-16-le") + b"\0\0"
    c["WideStrings6"] = c["WideStrings6"] + [struct.pack("<II", len(c["WideStrings6"]), len(t)) + t]
    c["Models"] = [_enr(_props([("EMBED", "TRUE"), ("NAME", "modele.step"), ("ID", "{MODELE-ESSAI}"),
                                ("CHECKSUM", "0")]))]


def carte(absents=(), vides=(), empilage="normal", panneau=False, guids=False, percage=False, chemins=False,
          essais=False):
    """Racine CFB de la carte synthétique (voir le docstring du module). `guids` : PrimitiveGuids cohérent ;
    `percage` : DrillManager sans groupe de via ; `chemins` : chemins absolus fictifs (FILENAME,
    SOURCEFOOTPRINTLIBRARY, DOCUMENTPATH, V9_STACKCUSTOMDATA, chaîne large, flux brut UTF-16, modèle STEP) ;
    `essais` : base complète des fichiers d'essai de l'annexe E (voir `contenu`)."""
    import zlib
    r = cfb.Noeud("Root Entry", cfb.TYPE_RACINE)
    r.ecrire("FileHeader", struct.pack("<I", 19) + "PCB 5.0 Binary File".encode("utf-16-le")[:24])
    titre, guid = b"PCB 6.0 Binary File", b"{00000000-0000-4000-8000-0000000000FF}"
    r.ecrire("FileHeaderSix", struct.pack("<I", len(titre) + 1) + bytes([len(titre)]) + titre + struct.pack("<d", 5.01)
             + struct.pack("<I", len(guid) + 1) + bytes([len(guid)]) + guid)
    c = contenu(empilage, panneau, essais)
    if chemins:
        _ajouter_chemins(c)
    for st in list(c):
        if st in absents:
            del c[st]
        elif st in vides:
            c[st] = []
    if guids:
        c["PrimitiveGuids"] = _guids(c)
    if percage:
        c["DrillManager"] = [_percage_pastilles()]
    for st, enregistrements in c.items():
        n = 1 if st in ("LayerKindMapping", "DrillManager") and enregistrements else len(enregistrements)
        r.ecrire(st + "/Header", struct.pack("<I", n))
        r.ecrire(st + "/Data", b"".join(enregistrements))
    if chemins:
        step = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\n"
                f"FILE_NAME('{CHEMIN_STEP}','2026-01-01T00:00:00',(''),(''),'','','');\nENDSEC;\nDATA;\nENDSEC;\n"
                "END-ISO-10303-21;\n").encode("latin-1")
        r.ecrire("Models/0", zlib.compress(step, 9))
        r.ecrire("Fichiers/Liste", struct.pack("<I", 7) + CHEMIN_BRUT.encode("utf-16-le") + b"\0\0" + b"\x01\x02")
    return r


def ecrire(racine, dossier, nom="synthetique.PcbDoc"):
    chemin = os.path.join(dossier, nom)
    cfb.ecrire_cfb(racine, chemin)
    return chemin


def fabriquer(dossier, nom="synthetique.PcbDoc", **options):
    """Écrit la carte synthétique dans `dossier` et rend son chemin."""
    return ecrire(carte(**options), dossier, nom)


def retirer_stockages(racine, stockages, mode="absents"):
    """Copie modifiée d'une racine CFB (carte réelle ou synthétique) : stockages retirés (`absents`) ou vidés
    (`vides` : Data vide, Header = 0)."""
    import copy
    r = copy.deepcopy(racine)
    for st in stockages:
        if st not in r.enfants:
            continue
        if mode == "absents":
            del r.enfants[st]
        else:
            r.ecrire(st + "/Data", b"")
            if "Header" in r.enfants[st].enfants:
                r.ecrire(st + "/Header", struct.pack("<I", 0))
    return r
