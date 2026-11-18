"""Pont entre un .PcbDoc (bibliothèque format_altium/altium_fichier) et le moteur de sérigraphie.

- exporter(doc, dossier, config=...) : écrit les 8 fichiers d'entrée du moteur (jetons de couche neutres :
  dessus, dessous, traversante, seri_dessus, seri_dessous, autre) et carte.json (format carte_serigraphie/1 :
  contour et découpes polygonaux, trous, règles tirées du fichier et de config_pcbdoc/2), sans Altium ;
- appliquer(doc, resultat, index) : applique le résultat du moteur au modèle (désignateurs en police trait
  placés à la main, traits rognés supprimés, morceaux conservés ajoutés au composant).

Désignateurs et origine : ``PcbDoc.designateurs()`` et ``PcbDoc.origine()`` de format_altium (jalon B1).
"""
from __future__ import annotations

import copy
import json
import math
import os
import statistics
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ICI, "..", "format_altium"))

from altium_fichier import couches  # noqa: E402
from altium_fichier.flux import FluxPrimitives  # noqa: E402
from altium_fichier.proprietes import valeur_mil  # noqa: E402

MIL_MM = 0.0254

# jetons de couche neutres des fichiers d'entree du moteur (voir src/donnees.rs)
JETONS = {"TOP": "dessus", "BOTTOM": "dessous", "MULTILAYER": "traversante",
          "TOPOVERLAY": "seri_dessus", "BOTTOMOVERLAY": "seri_dessous"}
SERI = {"TOPOVERLAY": 33, "BOTTOMOVERLAY": 34}
FACE_DU_JETON = {"seri_dessus": "TOPOVERLAY", "seri_dessous": "BOTTOMOVERLAY"}
FORMAT_CARTE = "carte_serigraphie/1"
GARDE_MASQUE_DEFAUT_MM = 0.254     # §2.9 : sans règle SilkToSolderMaskClearance dans le fichier
GARDE_BORD_MM = 0.5                # garde des désignateurs au bord, aux découpes et aux trous (choix §8.0-2 c)
TAILLES_DEFAUT_MM = [[1.0, 0.15], [0.9, 0.135], [0.8, 0.12]]   # §2.9 : sans désignateur dans le gabarit
FLECHE_ARC_MIL = 0.5               # flèche de la discrétisation des arcs du contour


def jeton(nom):
    """Jeton neutre d'une couche (nom de format_altium) ; « autre » hors cuivre extérieur et sérigraphie."""
    return JETONS.get(nom, "autre")


def f4(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def b(v):
    return "true" if v else "false"


def origine(doc):
    """Origine du fichier (mil) : ``PcbDoc.origine()`` de format_altium (alias conservé pour les appelants)."""
    return doc.origine()


def designateurs(doc):
    """{rang Components6: désignateur} : ``PcbDoc.designateurs()`` de format_altium (alias conservé)."""
    return doc.designateurs()


def _texte(doc, pr):
    larges = doc.chaines_larges()
    idx = pr.bloc.get("index_chaine_large")
    return larges.get(idx, pr.sous[1].texte if len(pr.sous) > 1 else "")


def _expansion_masque_regle(doc):
    """Expansion de masque de la regle SolderMaskExpansion de portee 'All' la plus prioritaire."""
    best = None
    for r in doc.regles():
        if r["type"] == "SolderMaskExpansion" and r["active"] and (r["portee1"] or "All").strip() == "All":
            if best is None or r["priorite"] < best["priorite"]:
                best = r
    if best is None:
        return 0.0
    return valeur_mil(best["proprietes"].get("EXPANSION"), 0.0)


def _boite_texte(bl, ox, oy):
    """Boite englobante d'un texte a partir de son rectangle (rect_largeur x rect_hauteur) :
    ancre (x, y), point d'encre (u, v) -> (x, y) + R(rot).(m.u, v)."""
    x, y = bl["x"] / 1e4 - ox, bl["y"] / 1e4 - oy
    w, h = bl["rect_largeur"] / 1e4, bl["rect_hauteur"] / 1e4
    fx = fy = 0.0
    if bl.get("justification_valide") and bl.get("ref_x") is not None:
        # justification 1..9 : gauche-haut, gauche-milieu, gauche-bas, centre-haut ... droite-bas,
        # rectangle place autour du point de reference (cale sur l'export Altium de rev4)
        j = max(1, min(9, bl.get("justification") or 3)) - 1
        fx, fy = (j // 3) / 2.0, 1.0 - (j % 3) / 2.0
        x, y = bl["ref_x"] / 1e4 - ox, bl["ref_y"] / 1e4 - oy
    m = -1 if bl["miroir"] else 1
    r = math.radians(bl["rotation"])
    c, s = math.cos(r), math.sin(r)
    pts = [(x + c * m * u - s * v, y + s * m * u + c * v) for u in (-fx * w, (1 - fx) * w) for v in (-fy * h, (1 - fy) * h)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def exporter(doc, dossier, trous_supplementaires=(), sans_caches=False, config=None):
    """Ecrit les fichiers d'entree du moteur. Renvoie (index, carte) : index {('piste'|'arc', face,
    i_export): rang de la primitive} pour appliquer ensuite pistes_supprimer / arcs_supprimer.
    config : section ``serigraphie`` de config_pcbdoc/2 (tailles, garde au masque, trous supplémentaires en mm) ;
    trous_supplementaires : trous [x, y, rayon] en mil ajoutés à ceux de la configuration.
    sans_caches : omettre les designateurs (NAMEON=FALSE) et commentaires (COMMENTON=FALSE) caches
    (controle du fichier produit)."""
    os.makedirs(dossier, exist_ok=True)
    ox, oy = doc.origine()
    D = doc.designateurs()
    comp6 = doc.data("Components6")
    nom_c = lambda i: D.get(i, "") if i != 0xFFFF else ""
    textes = []
    commentaires = {}
    for pr in doc.data("Texts6"):
        if pr.bloc.get("est_commentaire") and pr.bloc["composant"] != 0xFFFF:
            commentaires[pr.bloc["composant"]] = _texte(doc, pr)
    for pr in doc.data("Texts6"):
        bl = pr.bloc
        if sans_caches and bl["composant"] != 0xFFFF:
            p = comp6[bl["composant"]]
            if (bl.get("est_designateur") and p.get("NAMEON") == "FALSE") or (
                    bl.get("est_commentaire") and p.get("COMMENTON") == "FALSE"):
                continue
        nom = couches.nom_couche(bl["couche"], bl.get("couche_v7"))
        t = _texte(doc, pr)
        # chaines speciales (comme l'export Altium : valeur convertie)
        if t.lower() == ".designator" and bl["composant"] != 0xFFFF:
            t = D.get(bl["composant"], t)
        elif t.lower() == ".comment" and bl["composant"] != 0xFFFF:
            t = commentaires.get(bl["composant"], t)
        bx = _boite_texte(bl, ox, oy)
        textes.append({"Text": t, "X": f4(bl["x"] / 1e4 - ox), "Y": f4(bl["y"] / 1e4 - oy),
                       "Height": f4(bl["hauteur"] / 1e4), "StrokeWidth": f4(bl["epaisseur_trait"] / 1e4),
                       "Rotation": f4(bl["rotation"]), "Layer": jeton(nom),
                       "IsDesignator": b(bl.get("est_designateur")), "IsComment": b(bl.get("est_commentaire")),
                       "Component": nom_c(bl["composant"]), "MirrorFlag": b(bl["miroir"]),
                       "InComponent": b(bl["composant"] != 0xFFFF), "UseTTFonts": b(bl.get("type_police") == 1),
                       "FontName": "", "BoundsLeft": f4(bx[0]), "BoundsBottom": f4(bx[1]),
                       "BoundsRight": f4(bx[2]), "BoundsTop": f4(bx[3])})
    exp_regle = _expansion_masque_regle(doc)
    pastilles = []
    for pr in doc.data("Pads6"):
        bl = pr.bloc
        nom = couches.NOMS_V6.get(bl["couche"], "")
        forme = {1: "1", 2: "2", 3: "2"}.get(bl["forme_haut"], "2")  # octogone : rectangle englobant
        mode = bl.get("masque_mode")
        exp = bl["masque_expansion"] / 1e4 if mode == 2 else (exp_regle if mode == 1 else 0.0)
        pastilles.append({"Component": nom_c(bl["composant"]), "Name": pr.sous[0].texte or "",
                          "X": f4(bl["x"] / 1e4 - ox), "Y": f4(bl["y"] / 1e4 - oy), "Layer": jeton(nom),
                          "Net": doc.nom_reseau(bl["reseau"]) or "", "HoleSize": f4(bl["trou"] / 1e4),
                          "TopXSize": f4(bl["taille_haut_x"] / 1e4), "TopYSize": f4(bl["taille_haut_y"] / 1e4),
                          "Rotation": f4(bl["rotation"]), "TopShape": forme, "SolderMaskExpansion": f4(exp)})
    composants = []
    for i, e in enumerate(comp6):
        p = e.props
        composants.append({"Designator": D[i], "Comment": commentaires.get(i, ""), "Pattern": p.texte("PATTERN"),
                           "Rotation": f4(float(p.get("ROTATION", "0").strip() or 0)),
                           "X": f4(valeur_mil(p["X"]) - ox), "Y": f4(valeur_mil(p["Y"]) - oy),
                           "Layer": "dessous" if p.get("LAYER") == "BOTTOM" else "dessus",
                           "NameAutoPosition": p.get("NAMEAUTOPOSITION", "0"),
                           "CommentAutoPosition": p.get("COMMENTAUTOPOSITION", "0"), "OverallHeight": ""})
    vias = [{"X": f4(pr.bloc["x"] / 1e4 - ox), "Y": f4(pr.bloc["y"] / 1e4 - oy), "Size": f4(pr.bloc["diametre"] / 1e4),
             "HoleSize": f4(pr.bloc["trou"] / 1e4), "Net": doc.nom_reseau(pr.bloc["reseau"]) or "",
             "IsTenting_Top": b(pr.bloc["drapeaux1"] & 0x20), "IsTenting_Bottom": b(pr.bloc["drapeaux1"] & 0x40)}
            for pr in doc.data("Vias6")]
    index = {}
    for face, nomf in (("TOPOVERLAY", "top"), ("BOTTOMOVERLAY", "bottom")):
        lst = []
        for i, pr in enumerate(doc.data("Tracks6")):
            bl = pr.bloc
            if bl["couche"] == SERI[face]:
                index[("piste", face, len(lst))] = i
                lst.append({"X1": f4(bl["x1"] / 1e4 - ox), "Y1": f4(bl["y1"] / 1e4 - oy), "X2": f4(bl["x2"] / 1e4 - ox),
                            "Y2": f4(bl["y2"] / 1e4 - oy), "Width": f4(bl["largeur"] / 1e4),
                            "Layer": JETONS[face], "Component": nom_c(bl["composant"]),
                            "InComponent": b(bl["composant"] != 0xFFFF)})
        _ecrire(dossier, f"pistes_serigraphie_{nomf}", lst)
        lst = []
        for i, pr in enumerate(doc.data("Arcs6")):
            bl = pr.bloc
            if bl["couche"] == SERI[face]:
                index[("arc", face, len(lst))] = i
                lst.append({"XCenter": f4(bl["cx"] / 1e4 - ox), "YCenter": f4(bl["cy"] / 1e4 - oy),
                            "Radius": f4(bl["rayon"] / 1e4), "StartAngle": repr(float(bl["angle_debut"])),
                            "EndAngle": repr(float(bl["angle_fin"])), "Width": f4(bl["largeur"] / 1e4),
                            "Layer": JETONS[face], "Component": nom_c(bl["composant"]),
                            "InComponent": b(bl["composant"] != 0xFFFF)})
        _ecrire(dossier, f"arcs_serigraphie_{nomf}", lst)
    _ecrire(dossier, "textes", textes)
    _ecrire(dossier, "pastilles", pastilles)
    _ecrire(dossier, "composants", composants)
    _ecrire(dossier, "vias", vias)
    carte = geometrie_carte(doc, list(trous_supplementaires) + trous_config_mil(config), config)
    with open(os.path.join(dossier, "carte.json"), "w", encoding="utf-8") as f:
        json.dump(carte, f, indent=1)
    return index, carte


def _ecrire(dossier, nom, objets):
    with open(os.path.join(dossier, nom + ".json"), "w", encoding="utf-8") as f:
        json.dump(objets, f)


def _arc(cx, cy, r, sa, ea, depart):
    """Points d'un arc du contour (degrés, sens trigonométrique de sa à ea), orientés depuis ``depart``."""
    if ea <= sa:
        ea += 360.0
    pas = 2.0 * math.degrees(math.acos(max(-1.0, 1.0 - FLECHE_ARC_MIL / r))) if r > FLECHE_ARC_MIL else 90.0
    n = max(1, int(math.ceil((ea - sa) / max(pas, 1e-3))))
    pts = [(cx + r * math.cos(math.radians(sa + (ea - sa) * k / n)), cy + r * math.sin(math.radians(sa + (ea - sa) * k / n)))
           for k in range(n + 1)]
    if math.hypot(pts[-1][0] - depart[0], pts[-1][1] - depart[1]) < math.hypot(pts[0][0] - depart[0], pts[0][1] - depart[1]):
        pts.reverse()
    return pts


def contour_polygonal(sommets, ox, oy):
    """Contour Board6 (sommets, arcs KIND=1) en polygone (mil, repère commun), arcs discrétisés."""
    pts = []
    for s in sommets:
        if s["type"] == 1 and s["r"] and s["r"] > 0:
            pts.extend(_arc(s["cx"], s["cy"], s["r"], s["sa"], s["ea"], (s["x"], s["y"])))
        else:
            pts.append((s["x"], s["y"]))
    out = []
    for x, y in pts:
        q = [round(x - ox, 4), round(y - oy, 4)]
        if not out or q != out[-1]:
            out.append(q)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def garde_masque_mm(doc, config=None):
    """Garde sérigraphie-masque (mm) : ``config.garde_masque_mm`` si donnée, sinon la règle active
    SilkToSolderMaskClearance la plus prioritaire du fichier, sinon 0,254 mm (§2.9)."""
    g = (config or {}).get("garde_masque_mm")
    if g is not None:
        return float(g)
    best = None
    for r in doc.regles():
        if r["type"] == "SilkToSolderMaskClearance" and r["active"]:
            if best is None or (r["priorite"] or 0) < (best["priorite"] or 0):
                best = r
    if best is not None:
        v = best["valeurs"].get("MINSILKSCREENTOMASKGAP")
        if v:
            return round(valeur_mil(v, 0.0) * MIL_MM, 6)
    return GARDE_MASQUE_DEFAUT_MM


def tailles_mm(doc, config=None):
    """Tailles des désignateurs (mm) : liste de ``config.tailles_mm``, ou « auto » = médiane (hauteur, épaisseur)
    des désignateurs du gabarit puis 90 % et 80 % ; sans désignateur : tailles par défaut de §2.9."""
    t = (config or {}).get("tailles_mm", "auto")
    if t != "auto":
        return [[float(h), float(w)] for h, w in t]
    hs, ws = [], []
    for pr in doc.data("Texts6"):
        bl = pr.bloc
        if bl.get("est_designateur") and bl["composant"] != 0xFFFF and bl["hauteur"] > 0 and bl["epaisseur_trait"] > 0:
            hs.append(bl["hauteur"] / 1e4 * MIL_MM)
            ws.append(bl["epaisseur_trait"] / 1e4 * MIL_MM)
    if not hs:
        return [list(x) for x in TAILLES_DEFAUT_MM]
    h, w = statistics.median(hs), statistics.median(ws)
    if w >= h:
        w = 0.15 * h
    return [[round(h * k, 4), round(w * k, 4)] for k in (1.0, 0.9, 0.8)]


def geometrie_carte(doc, trous_supplementaires=(), config=None):
    """carte.json (format carte_serigraphie/1, mil, repère commun) : contour polygonal (arcs discrétisés),
    découpes polygonales (régions ISBOARDCUTOUT), trous des pastilles libres percées (rayon = trou / 2) et trous
    supplémentaires [x, y, rayon] (mil), règles (garde au masque, garde au bord, tailles)."""
    ox, oy = doc.origine()
    contour = contour_polygonal(doc.contour_carte(), ox, oy)
    if len(contour) < 3:
        raise ValueError("contour de carte absent de Board6")
    decoupes = []
    f = doc.data("Regions6")
    if isinstance(f, FluxPrimitives):
        for pr in f:
            s0 = pr.sous[0]
            if getattr(s0, "props", None) is not None and s0.props.get("ISBOARDCUTOUT") == "TRUE" and s0.contour:
                pts = [[round(c[-7] / 1e4 - ox, 4), round(c[-6] / 1e4 - oy, 4)] if len(c) == 8 else
                       [round(c[0] / 1e4 - ox, 4), round(c[1] / 1e4 - oy, 4)] for c in s0.contour]
                if len(pts) >= 3:
                    decoupes.append(pts)
    trous = []
    for pr in doc.data("Pads6"):
        bl = pr.bloc
        if bl["composant"] == 0xFFFF and bl["trou"] > 0:
            trous.append([round(bl["x"] / 1e4 - ox, 4), round(bl["y"] / 1e4 - oy, 4), round(bl["trou"] / 2e4, 4)])
    trous += [[float(v) for v in t] for t in trous_supplementaires]
    regles = {"garde_masque_mm": garde_masque_mm(doc, config), "garde_bord_mm": GARDE_BORD_MM,
              "tailles_mm": tailles_mm(doc, config)}
    return {"format": FORMAT_CARTE, "unites": "mil", "contour": contour, "decoupes": decoupes, "trous": trous,
            "regles": regles}


def trous_config_mil(config):
    """``trous_supplementaires_mm`` de config_pcbdoc/2 ([x, y, rayon] mm, repère commun) convertis en mil."""
    return [[x / MIL_MM, y / MIL_MM, r / MIL_MM] for x, y, r in (config or {}).get("trous_supplementaires_mm", [])]


# ---------------------------------------------------------------------------
def _face(j):
    if j not in FACE_DU_JETON:
        raise ValueError("couche de sérigraphie inconnue dans le résultat du moteur")
    return FACE_DU_JETON[j]


def appliquer(doc, res, index, tolerance=0.02):
    """Applique le resultat du moteur (dict de serigraphie.json) au modele. Renvoie un bilan."""
    ox, oy = doc.origine()
    D = doc.designateurs()
    par_nom = {v: k for k, v in D.items()}
    comp6 = doc.data("Components6")
    bilan = {"textes_places": 0, "textes_masques": 0, "pistes_supprimees": 0, "arcs_supprimes": 0,
             "pistes_ajoutees": 0, "anomalies": []}
    designateur_de = {}
    for i, pr in enumerate(doc.data("Texts6")):
        if pr.bloc.get("est_designateur") and pr.bloc["composant"] != 0xFFFF:
            designateur_de[pr.bloc["composant"]] = pr
    for t in res["textes"]:
        ic = par_nom.get(t["component"])
        if ic is None or ic not in designateur_de:
            bilan["anomalies"].append(f"designateur introuvable : {t['component']}")
            continue
        p = comp6[ic]
        p.supprimer("NAMEAUTOPOSITION")  # positionnement manuel (cle absente = 0)
        if t["hidden"]:
            p["NAMEON"] = "FALSE"
            bilan["textes_masques"] += 1
            continue
        p["NAMEON"] = "TRUE"
        bl = designateur_de[ic].bloc
        face = _face(t["layer"])
        bl["couche"] = couches.IDS_V6[face]
        if "couche_v7" in bl.v:
            bl["couche_v7"] = couches.v7_depuis_v6(bl["couche"])
        x, y = int(round((t["x"] + ox) * 1e4)), int(round((t["y"] + oy) * 1e4))
        h, w = int(round(t["height"] * 1e4)), int(round(t["stroke_width"] * 1e4))
        bl["x"], bl["y"] = x, y
        bl["hauteur"], bl["epaisseur_trait"] = h, w
        bl["rotation"] = float(t["rotation"]) % 360.0
        bl["miroir"] = 1 if t["mirror"] else 0
        # police trait par defaut (comme les designateurs trait des cartes d'origine)
        bl["type_police"] = 0
        if "type_texte" in bl.v:
            bl["type_texte"] = 0
        bl["police_trait"] = 1
        if "justification_valide" in bl.v:
            bl["justification_valide"] = 0
        if "ref_x" in bl.v:
            bl["ref_x"], bl["ref_y"] = x, y  # textes trait non justifies : reference = ancre
        enc = t.get("boite_encre")
        if enc and "rect_largeur" in bl.v:
            long_u = (enc[2] - enc[0]) if round(bl["rotation"]) % 180 == 0 else (enc[3] - enc[1])
            bl["rect_largeur"] = int(round((long_u + t["stroke_width"]) * 1e4))
            bl["rect_hauteur"] = h + w
        doc.marquer("Texts6")
        bilan["textes_places"] += 1
    doc.marquer("Components6")
    # traits supprimes : retrouves par l'index d'export (couche + coordonnees exactes)
    a_sup = {"Tracks6": set(), "Arcs6": set()}
    for genre, cle_liste, storage, champs in (("piste", "pistes_supprimer", "Tracks6", ("x1", "y1", "x2", "y2")),
                                             ("arc", "arcs_supprimer", "Arcs6", ("xcenter", "ycenter", "radius"))):
        f = doc.data(storage)
        for s in res.get(cle_liste, []):
            face = _face(s["layer"])
            trouve = None
            for (g, fc, k), i in index.items():
                if g != genre or fc != face or i in a_sup[storage]:
                    continue
                bl = f[i].bloc
                if genre == "piste":
                    v = (bl["x1"] / 1e4 - ox, bl["y1"] / 1e4 - oy, bl["x2"] / 1e4 - ox, bl["y2"] / 1e4 - oy)
                else:
                    v = (bl["cx"] / 1e4 - ox, bl["cy"] / 1e4 - oy, bl["rayon"] / 1e4)
                if all(abs(a - s[c]) <= tolerance for a, c in zip(v, champs)):
                    trouve = i
                    break
            if trouve is None:
                bilan["anomalies"].append(f"{genre} a supprimer introuvable : {s}")
            else:
                a_sup[storage].add(trouve)
    # morceaux conserves : ajoutes au composant d'origine (ils suivent ainsi le composant)
    modele = {}
    for pr in doc.data("Tracks6"):
        if pr.bloc["couche"] in SERI.values() and pr.bloc["couche"] not in modele:
            modele[pr.bloc["couche"]] = pr
    nouveaux = []
    for m in res.get("pistes_ajouter", []):
        face = _face(m["layer"])
        v6 = couches.IDS_V6[face]
        if v6 not in modele:
            bilan["anomalies"].append(f"pas de modele de piste sur {face}")
            continue
        pr = copy.deepcopy(modele[v6])
        bl = pr.bloc
        bl["couche"] = v6
        bl["couche_v7"] = couches.v7_depuis_v6(v6)
        bl["reseau"], bl["polygone"] = 0xFFFF, 0xFFFF
        bl["composant"] = par_nom.get(m.get("component") or "", 0xFFFF)
        bl["x1"], bl["y1"] = int(round((m["x1"] + ox) * 1e4)), int(round((m["y1"] + oy) * 1e4))
        bl["x2"], bl["y2"] = int(round((m["x2"] + ox) * 1e4)), int(round((m["y2"] + oy) * 1e4))
        bl["largeur"] = int(round(m["width"] * 1e4))
        nouveaux.append(pr)
    return bilan, a_sup, nouveaux
