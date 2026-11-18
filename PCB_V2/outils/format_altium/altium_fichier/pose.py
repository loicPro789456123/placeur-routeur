"""Pose d'un composant (translation, rotation, changement de face) et suppression de primitives
avec mise a jour des index croises (PrimitiveGuids, DrillManager, flux paralleles, violations).

Convention de pose (verifiee sur 1032 pastilles de 319 composants poses par Altium, carte
PCB_METROID_V2_rev5_auto, dont 26 composants retournes) :

    monde = origine + R(rotation) . M(face) . local       M(dessus) = I, M(dessous) = diag(1, -1)

Un composant sur la face inferieure est donc le miroir de son empreinte par rapport a l'axe X
local (y -> -y), puis tourne de ROTATION. Le passage d'une pose (o0, r0, f0) a une pose (o1, r1, f1)
est l'application affine p -> o1 + L (p - o0), L = R(r1) M1 M0 R(-r0) ; det L = -1 si la face change.
"""
from __future__ import annotations

import math
import re

from . import couches
from .flux import FluxPrimitives, SousRegion
from .proprietes import formater_mil, valeur_mil

# couches echangees lors d'un changement de face (V6) ; les MIDn sont refusees (voir poser_composant)
_PAIRES_V6 = [("TOP", "BOTTOM"), ("TOPOVERLAY", "BOTTOMOVERLAY"), ("TOPPASTE", "BOTTOMPASTE"),
              ("TOPSOLDER", "BOTTOMSOLDER")]
_PAIRES_V7 = [(0x01000001, 0x0100FFFF), (0x01030006, 0x01030007), (0x01030008, 0x01030009),
              (0x0103000A, 0x0103000B)]


class ErreurPose(Exception):
    """Pose impossible de facon sure (le fichier ne doit pas etre ecrit)."""


def _norm(a):
    a = math.fmod(a, 360.0)
    if a < 0:
        a += 360.0
    if abs(a - 360.0) < 1e-9:
        a = 0.0
    return a


class Transformation:
    """p -> o1 + L (p - o0), en unites internes (1e-4 mil) ; angles en degres."""

    def __init__(self, o0, r0, dessous0, o1, r1, dessous1):
        def rot(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            # valeurs exactes pour les multiples de 90 degres
            c, s = (round(c) if abs(c - round(c)) < 1e-12 else c), (round(s) if abs(s - round(s)) < 1e-12 else s)
            return ((c, -s), (s, c))

        def mul(a, b):
            return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(2)) for j in range(2)) for i in range(2))

        m0 = ((1, 0), (0, -1)) if dessous0 else ((1, 0), (0, 1))
        m1 = ((1, 0), (0, -1)) if dessous1 else ((1, 0), (0, 1))
        self.L = mul(mul(rot(r1), m1), mul(m0, rot(-r0)))
        self.o0, self.o1 = o0, o1
        self.miroir = bool(dessous0) != bool(dessous1)
        self.r0, self.r1 = r0, r1
        self.dessous0, self.dessous1 = bool(dessous0), bool(dessous1)
        # angle de la rotation propre (L = R(theta) ou R(theta).diag(1,-1))
        self.theta = math.degrees(math.atan2(self.L[1][0], self.L[0][0]))

    def identite(self):
        return (self.L == ((1, 0), (0, 1)) and abs(self.o0[0] - self.o1[0]) < 0.5
                and abs(self.o0[1] - self.o1[1]) < 0.5)

    def point_f(self, x, y):
        dx, dy = x - self.o0[0], y - self.o0[1]
        L = self.L
        return self.o1[0] + L[0][0] * dx + L[0][1] * dy, self.o1[1] + L[1][0] * dx + L[1][1] * dy

    def point(self, x, y):
        X, Y = self.point_f(x, y)
        return int(round(X)), int(round(Y))

    def angle(self, a):
        """Direction d'angle a -> angle de L.dir(a), dans [0, 360)."""
        r = math.radians(a)
        c, s = math.cos(r), math.sin(r)
        L = self.L
        return _norm(math.degrees(math.atan2(L[1][0] * c + L[1][1] * s, L[0][0] * c + L[0][1] * s)))

    def arc(self, a_debut, a_fin):
        """Arc parcouru dans le sens direct de a_debut a a_fin -> (debut, fin) transformes."""
        if not self.miroir:
            return self.angle(a_debut), self.angle(a_fin)
        return self.angle(a_fin), self.angle(a_debut)


# ---------------------------------------------------------------------------
def _echange_v6(v6):
    n = couches.NOMS_V6.get(v6)
    for a, b in _PAIRES_V6:
        if n == a:
            return couches.IDS_V6[b]
        if n == b:
            return couches.IDS_V6[a]
    return v6


def _echange_v7(v7):
    for a, b in _PAIRES_V7:
        if v7 == a:
            return b
        if v7 == b:
            return a
    return v7


def _echange_nom(nom):
    for a, b in _PAIRES_V6:
        if nom == a:
            return b
        if nom == b:
            return a
    return nom


def paires_mecaniques(doc):
    """Paires de couches mecaniques (Board6 MECHPAIRnL1/L2) : {nom: nom_associe}."""
    p = doc.board.dict()
    out = {}
    for k, v in p.items():
        m = re.fullmatch(r"MECHPAIR(\d+)L1", k)
        if m:
            w = p.get(f"MECHPAIR{m.group(1)}L2")
            if w:
                out[v] = w
                out[w] = v
    return out


def _couche_primitive(b):
    return couches.nom_couche(b["couche"], b.get("couche_v7"))


def _fmt_rot(a):
    """Format flottant Delphi des rotations (' 2.70000000000000E+0002')."""
    if a == 0:
        return " 0.00000000000000E+0000"
    e = int(math.floor(math.log10(abs(a))))
    m = a / 10 ** e
    if abs(m) >= 10 - 5e-15:
        m /= 10
        e += 1
    signe = "-" if a < 0 else " "
    return f"{signe}{abs(m):.14f}E{'+' if e >= 0 else '-'}{abs(e):04d}"


def _rotation_pastille(t: Transformation, a):
    """Rotation d'une pastille (forme symetrique par rapport a ses axes) : Altium note la
    rotation r1 + s.(a - r0) (s = -1 si la face change), equivalente modulo 180 degres a
    l'image geometrique (verifie sur rev5_auto)."""
    if not t.miroir:
        return _norm(a + t.theta)
    s0 = -1 if t.dessous0 else 1
    s1 = -1 if t.dessous1 else 1
    return _norm(t.r1 + s1 * s0 * (a - t.r0))


# ---------------------------------------------------------------------------
def transformer_primitive(doc, storage, pr, t: Transformation, paires_mec):
    """Applique la transformation a une primitive de composant. Renvoie une liste d'anomalies."""
    anomalies = []
    s0 = pr.sous[0]
    if isinstance(s0, SousRegion):
        _transformer_region(s0, t, paires_mec, anomalies, corps=storage in ("ComponentBodies6", "ShapeBasedComponentBodies6"))
        return anomalies
    b = pr.bloc
    if t.miroir and pr.type != 2:  # les pastilles sont traitees a part
        nom = _couche_primitive(b)
        if nom.startswith("MID") or nom.startswith("PLANE"):
            anomalies.append(f"{storage} : primitive sur couche interne {nom} (changement de face non gere)")
        elif nom in paires_mec:
            anomalies.append(f"{storage} : couche mecanique appairee {nom} (non geree)")
        b["couche"] = _echange_v6(b["couche"])
        if "couche_v7" in b.v:
            b["couche_v7"] = _echange_v7(b["couche_v7"])
    if pr.type == 4:  # piste
        b["x1"], b["y1"] = t.point(b["x1"], b["y1"])
        b["x2"], b["y2"] = t.point(b["x2"], b["y2"])
    elif pr.type == 1:  # arc
        b["cx"], b["cy"] = t.point(b["cx"], b["cy"])
        b["angle_debut"], b["angle_fin"] = _angles_arc(t, b["angle_debut"], b["angle_fin"])
    elif pr.type == 6:  # remplissage : rectangle (x1,y1)-(x2,y2) tourne autour de son centre
        cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
        hx, hy = abs(b["x2"] - b["x1"]) / 2, abs(b["y2"] - b["y1"]) / 2
        X, Y = t.point_f(cx, cy)
        b["rotation"] = _rotation_pastille(t, b["rotation"])
        b["x1"], b["y1"] = int(round(X - hx)), int(round(Y - hy))
        b["x2"], b["y2"] = int(round(X + hx)), int(round(Y + hy))
    elif pr.type == 2:  # pastille
        _transformer_pastille(pr, t, anomalies)
    elif pr.type == 5:  # texte
        _transformer_texte(b, t)
    elif pr.type == 3:
        b["x"], b["y"] = t.point(b["x"], b["y"])
    else:
        anomalies.append(f"{storage} : type de primitive {pr.type} non gere")
    return anomalies


def _angles_arc(t, a, b):
    plein = abs((b - a) % 360.0) < 1e-9 and abs(b - a) > 1e-9
    if plein:
        return 0.0, 360.0  # cercle complet : Altium note 0 -> 360
    na, nb = t.arc(a, b)
    if nb == 0.0 and na > 0:
        nb = 360.0  # Altium note 360 plutot que 0 en fin d'arc
    return na, nb


def _transformer_pastille(pr, t, anomalies):
    b = pr.bloc
    b["x"], b["y"] = t.point(b["x"], b["y"])
    b["rotation"] = _rotation_pastille(t, b["rotation"])
    pile = pr.sous[5].bloc if len(pr.sous) > 5 and hasattr(pr.sous[5], "bloc") else None
    if pile is not None and pile.get("forme_trou") == 2:
        pile["rotation_oblong"] = _rotation_pastille(t, pile["rotation_oblong"])
    if pile is not None:
        dx = pile.get("decalages_trou_x")
        dy = pile.get("decalages_trou_y")
        if (isinstance(dx, bytes) and any(dx)) or (isinstance(dy, bytes) and any(dy)):
            anomalies.append("pastille avec trou decale : decalage non transforme")
    if not t.miroir:
        return
    b["couche"] = _echange_v6(b["couche"])
    # octets 114..117 = couche V7 de la pastille (u8 114, u16 115, u8 117)
    if all(k in b.v for k in ("vers_couche", "inconnu_115", "depuis_couche")):
        v7 = b["vers_couche"] | (b["inconnu_115"] << 8) | (b["depuis_couche"] << 24)
        v7 = _echange_v7(v7)
        b["vers_couche"], b["inconnu_115"], b["depuis_couche"] = v7 & 0xFF, (v7 >> 8) & 0xFFFF, v7 >> 24
    if b["mode_pile"] != 0:
        # pile haut-milieu-bas / complete : echanger haut et bas
        b["taille_haut_x"], b["taille_bas_x"] = b["taille_bas_x"], b["taille_haut_x"]
        b["taille_haut_y"], b["taille_bas_y"] = b["taille_bas_y"], b["taille_haut_y"]
        b["forme_haut"], b["forme_bas"] = b["forme_bas"], b["forme_haut"]
        if b["mode_pile"] == 2:
            anomalies.append("pastille en pile complete : couches internes non inversees")
    if b.get("masque_bas_separe"):
        b["masque_expansion"], b["masque_expansion_bas"] = b["masque_expansion_bas"], b["masque_expansion"]


def _transformer_texte(b, t):
    """Texte : ancre (x, y) et point de reference transformes ; miroir bascule ; la rotation suit
    la direction de lecture u : monde = (x, y) + R(rot).(m.u, v), m = -1 si miroir."""
    m0 = -1 if b["miroir"] else 1
    # direction (m0.u) du texte dans le monde : R(rot).(m0, 0)
    r = math.radians(b["rotation"])
    ux, uy = m0 * math.cos(r), m0 * math.sin(r)
    vx, vy = -math.sin(r), math.cos(r)
    L = t.L
    nux, nuy = L[0][0] * ux + L[0][1] * uy, L[1][0] * ux + L[1][1] * uy
    nvx, nvy = L[0][0] * vx + L[0][1] * vy, L[1][0] * vx + L[1][1] * vy
    # nouvelle rotation : direction de v (le haut du texte) tournee de -90
    rot = _norm(math.degrees(math.atan2(nvy, nvx)) - 90.0)
    m1 = m0 * (-1 if t.miroir else 1)
    b["rotation"] = rot
    b["miroir"] = 1 if m1 < 0 else 0
    b["x"], b["y"] = t.point(b["x"], b["y"])
    if "ref_x" in b.v and not (b["ref_x"] == 0 and b["ref_y"] == 0):
        b["ref_x"], b["ref_y"] = t.point(b["ref_x"], b["ref_y"])
    del nux, nuy


def _transformer_region(s0: SousRegion, t, paires_mec, anomalies, corps):
    e = s0.entete
    if t.miroir and not corps:
        nom = couches.NOMS_V6.get(e["couche"], "")
        v7 = s0.props.get("V7_LAYER")
        if nom.startswith("MID") or nom.startswith("PLANE") or (v7 and v7 in paires_mec):
            anomalies.append(f"region sur couche {v7 or nom} : changement de face non gere")
        e["couche"] = _echange_v6(e["couche"])
        if v7 is not None:
            s0.props["V7_LAYER"] = _echange_nom(v7)
        if s0.props.get("LAYER") is not None:
            s0.props["LAYER"] = _echange_nom(s0.props.get("LAYER"))
    if s0.contour is not None:
        if s0.etendu:
            nc = []
            for (est_arc, x, y, cx, cy, ra, a1, a2) in s0.contour:
                X, Y = t.point(x, y)
                if est_arc:
                    CX, CY = t.point(cx, cy)
                    n1, n2 = t.angle(a1), t.angle(a2)
                    nc.append((est_arc, X, Y, CX, CY, ra, n1, n2))
                else:
                    nc.append((est_arc, X, Y, cx, cy, ra, a1, a2))
            s0.contour = nc
        else:
            s0.contour = [t.point_f(x, y) for x, y in s0.contour]
        s0.trous = [[t.point_f(x, y) for x, y in tr] for tr in s0.trous]
    p = s0.props
    if p.get("MODEL.2D.X") is not None and p.get("MODEL.2D.Y") is not None:
        X, Y = t.point_f(valeur_mil(p["MODEL.2D.X"]) * 1e4, valeur_mil(p["MODEL.2D.Y"]) * 1e4)
        p["MODEL.2D.X"] = formater_mil(X / 1e4)
        p["MODEL.2D.Y"] = formater_mil(Y / 1e4)
    if corps:
        _transformer_corps3d(p, t)


def _transformer_corps3d(p, t):
    """Corps 3D : la rotation du modele est relative au composant (ROTZ - s.ROTATION invariant,
    s = -1 sur la face inferieure : verifie sur les 391 corps de rev5_auto) ; le miroir passe par
    BODYPROJECTION (0 dessus, 1 dessous). TEXTUREROTATION suit la rotation du composant."""
    s0 = -1 if t.dessous0 else 1
    s1 = -1 if t.dessous1 else 1
    # corps extrude (MODEL.MODELTYPE = 0) : ROTZ laisse tel quel par Altium (17 cas observes)
    if p.get("MODEL.3D.ROTZ") is not None and p.get("MODEL.MODELTYPE") != "0":
        rz = float(p["MODEL.3D.ROTZ"])
        p["MODEL.3D.ROTZ"] = f"{_norm(rz - s0 * t.r0 + s1 * t.r1):.3f}"
    if p.get("TEXTUREROTATION") is not None:
        tr = float(p["TEXTUREROTATION"])
        p["TEXTUREROTATION"] = _fmt_rot(_norm(tr - t.r0 + t.r1))
    if t.miroir and p.get("BODYPROJECTION") is not None:
        p["BODYPROJECTION"] = "1" if t.dessous1 else "0"


# ---------------------------------------------------------------------------
def poser_composant(doc, idx, x_mil, y_mil, rotation, dessous):
    """Pose le composant de rang idx (Components6) en (x, y) mil, rotation (degres), face.
    Transforme toutes ses primitives. Renvoie (nb_primitives, anomalies)."""
    from .pcbdoc import FLUX_PRIMITIVES_GEO
    c = doc.data("Components6")[idx]
    x0, y0 = valeur_mil(c["X"]), valeur_mil(c["Y"])
    r0 = float(c.get("ROTATION", "0").strip() or 0)
    d0 = c.get("LAYER") == "BOTTOM"
    t = Transformation((x0 * 1e4, y0 * 1e4), r0, d0, (x_mil * 1e4, y_mil * 1e4), rotation, bool(dessous))
    if t.identite():
        return 0, []
    paires_mec = paires_mecaniques(doc)
    anomalies = []
    n = 0
    for st in FLUX_PRIMITIVES_GEO:
        f = doc.data(st)
        if not isinstance(f, FluxPrimitives):
            continue
        for pr in f:
            if pr.bloc.get("composant") == idx:
                anomalies += transformer_primitive(doc, st, pr, t, paires_mec)
                doc.marquer(st)
                n += 1
    c["X"] = formater_mil(x_mil)
    c["Y"] = formater_mil(y_mil)
    c["ROTATION"] = _fmt_rot(_norm(rotation))
    if t.miroir:
        c["LAYER"] = "BOTTOM" if dessous else "TOP"
    doc.marquer("Components6")
    return n, anomalies


# ---------------------------------------------------------------------------
# Suppression de primitives
# ---------------------------------------------------------------------------
# flux dont les enregistrements sont paralleles (meme rang = meme objet)
FLUX_PARALLELES = {"Regions6": "ShapeBasedRegions6", "ShapeBasedRegions6": "Regions6",
                   "ComponentBodies6": "ShapeBasedComponentBodies6",
                   "ShapeBasedComponentBodies6": "ComponentBodies6"}
# code PrimitiveGuids (octet bas) par flux
CODES_GUID = {"Arcs6": 0x01, "Pads6": 0x02, "Vias6": 0x03, "Tracks6": 0x04, "Texts6": 0x05, "Fills6": 0x06,
              "Regions6": 0x59, "ShapeBasedRegions6": 0x59, "ComponentBodies6": 0x5A,
              "ShapeBasedComponentBodies6": 0x5A}
_FLUX_VIOLATIONS = re.compile(r"^T\w*Viola(tion)?$")


def code_guid(doc, storage):
    """Code complet (rang << 8 | type) des entrees PrimitiveGuids d'un flux : le rang du flux
    dans l'ordre FLUX_PRIMITIVES_GEO suit les codes observes (0x0d01 Arcs6 ... 0x165a)."""
    from .pcbdoc import FLUX_PRIMITIVES_GEO
    if storage not in FLUX_PRIMITIVES_GEO:
        return None
    return ((0x0D + FLUX_PRIMITIVES_GEO.index(storage)) << 8) | CODES_GUID[storage]


def supprimer_primitives(doc, a_supprimer: dict):
    """a_supprimer : {storage: ensemble d'index}. Supprime les primitives, leurs jumelles des flux
    paralleles, renumerote PrimitiveGuids et DrillManager, vide les violations DRC enregistrees.
    Refuse de supprimer des pastilles (UniqueIDPrimitiveInformation non renumerote)."""
    sup = {k: set(v) for k, v in a_supprimer.items() if v}
    for st in list(sup):
        jum = FLUX_PARALLELES.get(st)
        if jum and (doc.existe(jum) or len(doc.data(jum) or ())):  # jumeau lu, ou ajouté (créé à l'écriture)
            sup.setdefault(jum, set()).update(sup[st])
    if "Pads6" in sup:
        raise ErreurPose("suppression de pastilles non geree")
    correspondances = {}
    for st, idx in sup.items():
        f = doc.data(st)
        if not isinstance(f, FluxPrimitives):
            raise ErreurPose(f"flux {st} : pas un flux de primitives")
        nouv, corr = [], {}
        for i, pr in enumerate(f.primitives):
            if i in idx:
                continue
            corr[i] = len(nouv)
            nouv.append(pr)
        f.primitives = nouv
        correspondances[st] = corr
        doc.marquer(st)
    # PrimitiveGuids (seulement s'il existe dans le fichier)
    g = doc.data("PrimitiveGuids") if doc.existe("PrimitiveGuids") else None
    if g is not None and hasattr(g, "entrees"):
        codes = {code_guid(doc, st): st for st in correspondances}
        ent = []
        for code, i, guid in g.entrees:
            st = codes.get(code)
            if st is None:
                ent.append((code, i, guid))
            elif i in correspondances[st]:
                ent.append((code, correspondances[st][i], guid))
        g.entrees = ent
        doc.marquer("PrimitiveGuids")
    # DrillManager
    if "Vias6" in correspondances and doc.existe("DrillManager"):
        dm = doc.data("DrillManager")
        if dm is not None and hasattr(dm, "groupes"):
            corr = correspondances["Vias6"]
            for props, listes in dm.groupes:
                listes[1][:] = [corr[i] for i in listes[1] if i in corr]
            doc.marquer("DrillManager")
    vider_violations(doc)
    return {st: len(v) for st, v in sup.items()}


def vider_violations(doc):
    """Les violations DRC enregistrees designent des primitives par leur rang : on les vide
    (Altium les recalcule au prochain DRC)."""
    n = 0
    for c, f in doc.flux.items():
        st = c.split("/")[0]
        if c.endswith("/Data") and _FLUX_VIOLATIONS.match(st) and hasattr(f, "enregistrements") and len(f):
            n += len(f.enregistrements)
            f.enregistrements = []
            doc.marquer(st)
    return n


# ---------------------------------------------------------------------------
# DrillManager : groupes d'outils de perçage des vias
# ---------------------------------------------------------------------------
def _centiemes_mm(v_mil):
    return f"{v_mil * 0.0254 * 100:.4g}"


def nouveau_groupe_via(dm, trou_mil, diametre_mil=None, modele=None):
    """Ajoute à `dm` (DrillManager) un groupe d'outil de perçage de via de TOOLSIZE `trou_mil` et le rend
    (propriétés, [pastilles, vias]). Propriétés : copie de `modele` (groupe de via existant) s'il est donné,
    sinon gabarit PROPRIETES_GROUPE_VIA (TEMPLATENAME « v<diamètre>h<trou> » en centièmes de mm, comme
    Altium) ; HOLEMARK = plus grand HOLEMARK existant + 1."""
    import copy
    from . import gabarits as G
    from .proprietes import Proprietes
    marque = max((int(p.get("HOLEMARK")) for p, _ in dm.groupes
                  if (p.get("HOLEMARK") or "").strip().lstrip("-").isdigit()), default=-1) + 1
    if modele is not None:
        p = copy.deepcopy(modele)
    else:
        p = Proprietes([["", None]] + [[k, v] for k, v in G.PROPRIETES_GROUPE_VIA])
        d = diametre_mil if diametre_mil is not None else 2 * trou_mil
        p["TEMPLATENAME"] = f"v{_centiemes_mm(d)}h{_centiemes_mm(trou_mil)}"
    p["HOLEMARK"] = str(marque)
    p["TOOLSIZE"] = formater_mil(trou_mil)
    groupe = (p, [[], []])
    dm.groupes.append(groupe)
    return groupe


def groupe_via(dm, trou_mil):
    """Groupe de via de `dm` dont le TOOLSIZE vaut `trou_mil` (à 0,001 mil), sinon None."""
    for g in dm.groupes:
        p = g[0]
        if p.get("OBJECTID") == "Via":
            ts = valeur_mil(p.get("TOOLSIZE"))
            if ts is not None and abs(ts - trou_mil) < 1e-3:
                return g
    return None


def rattacher_vias_percage(doc):
    """Reconstruit les listes de vias de DrillManager (s'il existe dans le fichier) d'après le diamètre de trou
    de chaque via. Les groupes de vias devenus vides sont retirés ; un trou sans groupe reçoit un groupe copié
    du groupe de via le plus proche (TOOLSIZE mis à jour), ou construit depuis le gabarit si la carte n'a aucun
    groupe de via (nouveau_groupe_via)."""
    dm = doc.data("DrillManager") if doc.existe("DrillManager") else None
    if dm is None or not hasattr(dm, "groupes"):
        return
    vias = doc.data("Vias6")
    groupes_via = [g for g in dm.groupes if g[0].get("OBJECTID") == "Via"]
    for _, l in groupes_via:
        l[1][:] = []
    for i, pr in enumerate(vias or []):
        trou = pr.bloc["trou"] / 1e4
        g = groupe_via(dm, trou)
        if g is None:
            mod = min(groupes_via, key=lambda x: abs((valeur_mil(x[0].get("TOOLSIZE")) or 0) - trou),
                      default=None)
            g = nouveau_groupe_via(dm, trou, pr.bloc["diametre"] / 1e4, mod[0] if mod else None)
            groupes_via.append(g)
        g[1][1].append(i)
    dm.groupes[:] = [(p, l) for p, l in dm.groupes if p.get("OBJECTID") != "Via" or l[1]]
    doc.marquer("DrillManager")


# ---------------------------------------------------------------------------
# PrimitiveGuids : ajout en coût constant, GUID déterministes
# ---------------------------------------------------------------------------
def _plages_guids(doc, g):
    """{code: [début, fin)} des entrées PrimitiveGuids (première et dernière entrée de chaque code),
    mémorisées sur le document tant que la liste des entrées n'est pas remplacée ni modifiée ailleurs."""
    m = getattr(doc, "_plages_guids", None)
    if m is not None and m[0] is g.entrees and m[1] == len(g.entrees):
        return m[2]
    plages = {}
    for k, e in enumerate(g.entrees):
        p = plages.get(e[0])
        if p is None:
            plages[e[0]] = [k, k + 1]
        else:
            p[1] = k + 1
    doc._plages_guids = [g.entrees, len(g.entrees), plages]
    return plages


def ajouter_primitive(doc, storage, pr):
    """Ajoute une primitive en fin de flux et, si PrimitiveGuids existe dans le fichier, lui donne une entrée
    (code du flux calculé par code_guid, GUID déterministe `doc.nouveau_guid`) placée après la dernière
    entrée du même code (ou avant le premier groupe de rang de flux supérieur). Coût constant par ajout
    (plages des codes mémorisées). Rend le rang de la primitive."""
    f = doc.data(storage)
    f.primitives.append(pr)
    i = len(f.primitives) - 1
    doc.marquer(storage)
    g = doc.data("PrimitiveGuids") if doc.existe("PrimitiveGuids") else None
    code = code_guid(doc, storage)
    if g is not None and hasattr(g, "entrees") and code is not None:
        plages = _plages_guids(doc, g)
        p = plages.get(code)
        if p is not None:
            pos = p[1]
        else:
            pos = min((q[0] for c, q in plages.items() if (c >> 8) > (code >> 8)), default=len(g.entrees))
        for q in plages.values():
            if q[0] >= pos:
                q[0] += 1
            if q[1] > pos:
                q[1] += 1
        if p is None:
            plages[code] = [pos, pos + 1]
        else:
            p[1] = pos + 1
        g.entrees.insert(pos, (code, i, doc.nouveau_guid(storage).bytes_le))
        doc._plages_guids[1] = len(g.entrees)
        doc.marquer("PrimitiveGuids")
    return i


def verifier_guids(doc):
    """Contrôle : pour chaque flux de primitives, les entrées PrimitiveGuids sont exactement 0..n-1. Rend la
    liste des écarts (vide si cohérent, ou si le fichier n'a pas de PrimitiveGuids : les exemples d'Altium
    n'en ont pas, Altium le reconstruit)."""
    from .pcbdoc import FLUX_PRIMITIVES_GEO
    g = doc.data("PrimitiveGuids") if doc.existe("PrimitiveGuids") else None
    if g is None or not hasattr(g, "entrees"):
        return []
    par_code = {}
    for e in g.entrees:
        par_code.setdefault(e[0], []).append(e[1])
    ecarts = []
    for st in FLUX_PRIMITIVES_GEO:
        f = doc.data(st)
        if not isinstance(f, FluxPrimitives):
            continue
        idx = par_code.get(code_guid(doc, st), [])
        if idx != list(range(len(f))):
            ecarts.append(f"{st} : {len(idx)} GUID pour {len(f)} primitives")
    return ecarts


# ---------------------------------------------------------------------------
# Repère local d'une pièce (convention unique du contrat, §0.4)
# ---------------------------------------------------------------------------
def _cos_sin(a):
    r = math.radians(a)
    c, s = math.cos(r), math.sin(r)
    c = round(c) if abs(c - round(c)) < 1e-12 else c  # valeurs exactes pour les multiples de 90 degrés
    s = round(s) if abs(s - round(s)) < 1e-12 else s
    return c, s


def monde_vers_local(point, origine, rotation, dessous=False):
    """Point du repère monde (carte) -> repère local d'une pièce d'origine `origine`, de rotation `rotation`
    (degrés, sens trigonométrique), posée dessous ou non : local = M(face) · R(−rotation) · (monde − origine),
    M(dessous) = diag(1, −1) (§0.4). Unités quelconques (celles de `point` et `origine`)."""
    c, s = _cos_sin(rotation)
    dx, dy = point[0] - origine[0], point[1] - origine[1]
    x, y = c * dx + s * dy, -s * dx + c * dy
    return (x, -y) if dessous else (x, y)


def local_vers_monde(point, origine, rotation, dessous=False):
    """Inverse de monde_vers_local : monde = origine + R(rotation) · M(face) · local."""
    c, s = _cos_sin(rotation)
    x, y = point[0], (-point[1] if dessous else point[1])
    return origine[0] + c * x - s * y, origine[1] + s * x + c * y


def rotation_relative(rotation_pastille, rotation_piece, dessous=False):
    """Rotation d'une pastille relative à sa pièce (§0.4), dans [0, 360) : rotation de la pastille − rotation
    de la pièce pour une pièce dessus, rotation de la pièce − rotation de la pastille pour une pièce dessous."""
    if dessous:
        return _norm(rotation_piece - rotation_pastille)
    return _norm(rotation_pastille - rotation_piece)


def rotation_absolue(rotation_rel, rotation_piece, dessous=False):
    """Inverse de rotation_relative : rotation de la pastille dans le repère monde, dans [0, 360)."""
    if dessous:
        return _norm(rotation_piece - rotation_rel)
    return _norm(rotation_rel + rotation_piece)


