"""Différence champ par champ entre deux .PcbDoc (outil des expériences différentielles).

Usage : python differentiel.py AVANT.PcbDoc APRES.PcbDoc [--max 50] [--volatils]
        python differentiel.py CARTE_A.PcbDoc CARTE_B.PcbDoc --apparier [--detail]   (cartes entières)
Pour chaque flux modifié : enregistrements ajoutés/supprimés, et pour les enregistrements de même rang,
les champs (binaires ou clés de propriétés) dont la valeur a changé, avec leur statut (C/S/I).
Aucun fichier n'est lu par défaut ; rien n'est écrit (sortie sur la console, noms de fichiers seuls).
Codes de sortie : 0 aucune différence ; 1 différences trouvées ; 2 entrée invalide ; 4 erreur interne.
"""
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

from altium_fichier import PcbDoc  # noqa: E402
from altium_fichier import commandes as K  # noqa: E402
from altium_fichier.flux import FluxPrimitives, FluxProps, SousBloc, SousRegion, ChainePascal  # noqa: E402
from altium_fichier.proprietes import valeur_mil  # noqa: E402


def _fmt(v):
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return repr(v)


IGNORER = set()  # champs volatils ignorés (option --volatils)
VOLATILS_PROPS = {"FILENAME", "TIME", "DATE", "UNIQUEID", "VP.HX", "VP.HY", "VP.LX", "VP.LY"}


def _utf16(v):
    if isinstance(v, str):
        return v
    if isinstance(v, (bytes, bytearray)):
        for i in range(0, len(v) - 1, 2):
            if v[i] == 0 and v[i + 1] == 0:
                return bytes(v[:i]).decode("utf-16-le", "replace")
    return v


def diff_bloc(a, b, pref):
    out = []
    t = {c[1]: c for c in a.gabarit}
    for k in sorted(set(a.v) | set(b.v), key=lambda k: list(a.v).index(k) if k in a.v else 9999):
        va, vb = a.v.get(k), b.v.get(k)
        if k in t and t[k][2].startswith("utf16:"):
            va, vb = _utf16(va), _utf16(vb)  # le tampon après le NUL contient des restes de mémoire
        if k in IGNORER:
            continue
        if va != vb:
            st = t[k][4] if k in t else "I"
            extra = ""
            if isinstance(va, bytes) and isinstance(vb, bytes) and len(va) == len(vb):
                octs = [i for i in range(len(va)) if va[i] != vb[i]]
                extra = f"  (octets relatifs modifiés : {octs})"
            out.append(f"{pref}{k} [{st}] : {_fmt(va)} -> {_fmt(vb)}{extra}")
    return out


def diff_props(pa, pb, pref):
    out = []
    da, db = pa.dict(), pb.dict()
    for k in da.keys() | db.keys():
        if IGNORER and k in VOLATILS_PROPS:
            continue
        if da.get(k) != db.get(k):
            out.append(f"{pref}{k} : {da.get(k)!r} -> {db.get(k)!r}")
    return sorted(out)


def diff_prim(pa, pb, pref):
    out = []
    for i, (sa, sb) in enumerate(zip(pa.sous, pb.sous)):
        p = f"{pref}sous{i + 1}."
        if isinstance(sa, SousBloc) and isinstance(sb, SousBloc):
            out += diff_bloc(sa.bloc, sb.bloc, p)
        elif isinstance(sa, SousRegion) and isinstance(sb, SousRegion):
            out += diff_bloc(sa.entete, sb.entete, p)
            out += diff_props(sa.props, sb.props, p)
            if sa.contour != sb.contour or sa.trous != sb.trous:
                out.append(f"{p}contour/trous modifiés ({len(sa.contour or [])} -> {len(sb.contour or [])} sommets)")
        elif sa.encoder() != sb.encoder():
            ta = getattr(sa, "texte", None)
            out.append(f"{p}brut : {ta!r} / {sa.encoder().hex()[:80]} -> {getattr(sb, 'texte', None)!r} / {sb.encoder().hex()[:80]}")
    return out


def parseur():
    p = K.Parseur(prog="differentiel.py", description="Différence champ par champ entre deux fichiers .PcbDoc.")
    p.add_argument("avant", metavar="AVANT.PcbDoc")
    p.add_argument("apres", metavar="APRES.PcbDoc")
    p.add_argument("--max", type=int, default=60, help="nombre maximal d'enregistrements détaillés par flux")
    p.add_argument("--volatils", action="store_true",
                   help="ignorer les champs réécrits à chaque enregistrement (GUID de gabarit, date, vue, FileHeaderSix, PrimitiveGuids, LettersGeometry)")
    p.add_argument("--apparier", action="store_true",
                   help="cartes entières : apparier composants (désignateur) et primitives (géométrie) avant de comparer")
    p.add_argument("--detail", action="store_true", help="avec --apparier : lister les primitives non appariées")
    return p


def main(argv=None) -> int:
    return K.executer(_executer, parseur(), argv)


def _executer(a):
    K.verifier_fichiers([a.avant, a.apres])
    if a.apparier:
        lignes, ecarts = comparer_apparie_ecarts(a.avant, a.apres, resume=not a.detail)
        print("\n".join(lignes))
        return K.CODE_NEGATIF if ecarts else K.CODE_OK
    IGNORER.clear()
    if a.volatils:
        IGNORER.update({"guid_gabarit"})
    A, B = PcbDoc.ouvrir(a.avant), PcbDoc.ouvrir(a.apres)
    ecarts = 0
    for c in sorted(set(A.flux) | set(B.flux)):
        if c not in A.flux or c not in B.flux:
            print(f"== {c} : {'ajouté' if c in B.flux else 'supprimé'}")
            ecarts += 1
            continue
        fa, fb = A.flux[c], B.flux[c]
        if fa.encoder() == fb.encoder():
            continue
        if a.volatils and c.split("/")[0] in ("FileHeaderSix", "PrimitiveGuids", "LettersGeometry"):
            continue
        ecarts += 1
        print(f"== {c} ({len(fa.encoder())} -> {len(fb.encoder())} octets)")
        if isinstance(fa, FluxPrimitives) and isinstance(fb, FluxPrimitives):
            la, lb = list(fa), list(fb)
            if len(la) != len(lb):
                print(f"   enregistrements : {len(la)} -> {len(lb)}")
            n = 0
            for i, (x, y) in enumerate(zip(la, lb)):
                if x.encoder() != y.encoder():
                    lignes = diff_prim(x, y, "      ")
                    if not lignes:
                        continue
                    n += 1
                    if n <= a.max:
                        print(f"   [{i}] {x.nom_type}")
                        for l in lignes:
                            print(l)
            print(f"   {n} enregistrement(s) modifié(s)")
        elif isinstance(fa, FluxProps) and isinstance(fb, FluxProps):
            la, lb = fa.enregistrements, fb.enregistrements
            if len(la) != len(lb):
                print(f"   enregistrements : {len(la)} -> {len(lb)}")
            n = 0
            for i, (x, y) in enumerate(zip(la, lb)):
                if x.encoder() != y.encoder():
                    lignes = diff_props(x.props, y.props, "      ") if (x.props is not None and y.props is not None) else ["binaire"]
                    if not lignes and x.prefixe == y.prefixe:
                        continue
                    n += 1
                    if n <= a.max:
                        print(f"   [{i}] préfixe {x.prefixe.hex()} -> {y.prefixe.hex()}" if x.prefixe != y.prefixe else f"   [{i}]")
                        if x.props is not None and y.props is not None:
                            for l in diff_props(x.props, y.props, "      "):
                                print(l)
            print(f"   {n} enregistrement(s) modifié(s)")
        else:
            ea, eb = fa.encoder(), fb.encoder()
            octs = [i for i in range(min(len(ea), len(eb))) if ea[i] != eb[i]]
            print(f"   {type(fa).__name__} : {len(octs)} octets différents (premiers : {octs[:20]})")
    return K.CODE_NEGATIF if ecarts else K.CODE_OK


# =============================================================================
# Comparaison apres appariement (cartes entieres : rangs differents)
# =============================================================================
CHAMPS_IGNORES = {"guid_gabarit", "nom_police", "codebarre_police", "index_chaine_large"}
ANGLES = {"rotation", "angle_debut", "angle_fin", "rotation_oblong"}
COORDS = {"x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "ref_x", "ref_y"}
PROPS_IGNOREES = {"TEXTUREROTATION", "TEXTURECENTERX", "TEXTURECENTERY", "SELECTION"}


def _designateurs(doc):
    return doc.designateurs()


def _proprietaire(b, D):
    if b["composant"] != 0xFFFF:
        return "C:" + str(D.get(b["composant"], b["composant"]))
    if b["polygone"] != 0xFFFF:
        return "P:" + str(b["polygone"])
    return "libre"


def _points(pr):
    s0 = pr.sous[0]
    if s0.contour is None:
        return []
    if s0.etendu:
        return [(c[1], c[2]) for c in s0.contour]
    return [(c[0], c[1]) for c in s0.contour]


def _cle(doc, pr, D):
    b = pr.bloc
    own = _proprietaire(b, D)
    net = doc.nom_reseau(b.get("reseau")) if b.get("reseau") not in (None, 0xFFFF) else None
    if pr.type == 2:
        return (own, pr.sous[0].texte), (b["x"], b["y"])
    if pr.type == 3:
        return (own, net), (b["x"], b["y"])
    if pr.type == 4:
        a, c = (b["x1"], b["y1"]), (b["x2"], b["y2"])
        return (own, b["couche"], net, b["largeur"] // 100), (min(a, c) + max(a, c))
    if pr.type == 1:
        return (own, b["couche"], net, b["rayon"] // 100), (b["cx"], b["cy"])
    if pr.type == 5:
        return (own, b.get("est_designateur"), b.get("est_commentaire")), (b["x"], b["y"])
    if pr.type == 6:
        return (own, b["couche"]), ((b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2)
    pts = sorted(set(_points(pr)))
    c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else (0, 0)
    return (own, b["couche"], net, pr.sous[0].props.get("MODELID")), c


def _distance(p, q):
    return sum(abs(a - b) for a, b in zip(p, q))


def _apparier(la, lb, seuil):
    """la, lb : listes de (cle, position, primitive). Appariement par cle puis position la plus proche."""
    paires, seuls_a = [], []
    groupes = {}
    for e in lb:
        groupes.setdefault(e[0], []).append(e)
    for e in la:
        cands = groupes.get(e[0])
        if not cands:
            seuls_a.append(e)
            continue
        k = min(range(len(cands)), key=lambda i: _distance(e[1], cands[i][1]))
        if _distance(e[1], cands[k][1]) <= seuil:
            paires.append((e, cands.pop(k)))
        else:
            seuls_a.append(e)
    seuls_b = [e for g in groupes.values() for e in g]
    return paires, seuls_a, seuls_b


def _diff_champs(pa, pb, tol):
    out = []
    if isinstance(pa.sous[0], SousRegion):
        sa, sb = pa.sous[0], pb.sous[0]
        for k, va in sa.entete.v.items():
            if va != sb.entete.v.get(k):
                out.append((k, va, sb.entete.v.get(k)))
        da, db = sa.props.dict(), sb.props.dict()
        for k in da.keys() | db.keys():
            if k in PROPS_IGNOREES or da.get(k) == db.get(k):
                continue
            if k == "MODEL.3D.ROTZ" and da.get(k) and db.get(k) and abs((float(da[k]) - float(db[k]) + 180) % 360 - 180) < 1e-6:
                continue
            if k.startswith("MODEL.2D.") and abs((valeur_mil(da.get(k)) or 0) - (valeur_mil(db.get(k)) or 0)) <= tol / 1e4 * 10:
                continue
            out.append(("props." + k, da.get(k), db.get(k)))
        A, B = sorted(set(_points(pa))), sorted(set(_points(pb)))
        if len(A) != len(B) or any(_distance(p, min(B, key=lambda q: _distance(p, q))) > 100 * tol for p in A):
            out.append(("contour", len(A), len(B)))
        return out
    for j, (sa, sb) in enumerate(zip(pa.sous, pb.sous)):
        if not hasattr(sa, "bloc"):
            if sa.encoder() != sb.encoder():
                out.append((f"sous{j + 1}", sa.encoder()[:16].hex(), sb.encoder()[:16].hex()))
            continue
        A, B = sa.bloc.v, sb.bloc.v
        for k, va in A.items():
            vb = B.get(k)
            if k in CHAMPS_IGNORES:
                continue
            if k in ANGLES and isinstance(va, float) and isinstance(vb, float):
                eq = abs((va - vb + 180) % 360 - 180) < 1e-6
            elif k in COORDS and isinstance(va, int) and isinstance(vb, int):
                if pa.type == 4 and k in ("x1", "y1", "x2", "y2"):
                    continue  # extremites comparees sans ordre ci-dessous
                eq = abs(va - vb) <= tol
            else:
                eq = va == vb
            if not eq:
                out.append((k, va, vb))
        if pa.type == 4:
            ea = sorted([(A["x1"], A["y1"]), (A["x2"], A["y2"])])
            eb = sorted([(B["x1"], B["y1"]), (B["x2"], B["y2"])])
            if any(_distance(p, q) > tol for p, q in zip(ea, eb)):
                out.append(("extremites", ea, eb))
    return out


def comparer_apparie(chemin_a, chemin_b, resume=True, tol=2, avec_textes_auto=False):
    """Lignes de résumé de comparer_apparie_ecarts (sans le nombre d'écarts)."""
    return comparer_apparie_ecarts(chemin_a, chemin_b, resume, tol, avec_textes_auto)[0]


def comparer_apparie_ecarts(chemin_a, chemin_b, resume=True, tol=2, avec_textes_auto=False):
    """Compare deux cartes entieres : composants apparies par designateur, primitives par
    proprietaire (composant / libre / polygone), type, couche, reseau et position. Les champs
    volatils (GUID, police) sont ignores ; angles modulo 360 ; coordonnees a tol unites internes
    (1e-4 mil) ; pistes sans ordre des extremites ; cuivre coule des polygones compte seulement.
    Les designateurs et commentaires sont exclus (positionnes automatiquement par Altium) sauf
    avec_textes_auto. Renvoie (lignes de resume, nombre d'ecarts)."""
    A, B = PcbDoc.ouvrir(chemin_a), PcbDoc.ouvrir(chemin_b)
    ecarts = 0
    DA, DB = _designateurs(A), _designateurs(B)
    L = [f"A = {os.path.basename(chemin_a)} ; B = {os.path.basename(chemin_b)}"]
    ia, ib = {v: k for k, v in DA.items()}, {v: k for k, v in DB.items()}
    communs = sorted(set(ia) & set(ib))
    diff_c = {}
    for n in communs:
        pa, pb = A.data("Components6")[ia[n]].dict(), B.data("Components6")[ib[n]].dict()
        for k in pa.keys() | pb.keys():
            if k in PROPS_IGNOREES:
                continue
            va, vb = pa.get(k), pb.get(k)
            if k in ("X", "Y") and va and vb and abs(valeur_mil(va) - valeur_mil(vb)) <= tol / 1e4:
                continue
            if k == "ROTATION" and va and vb and abs((float(va) - float(vb) + 180) % 360 - 180) < 1e-6:
                continue
            if va != vb:
                diff_c.setdefault(k, []).append((n, va, vb))
    ecarts += len(set(ia) ^ set(ib)) + sum(len(v) for v in diff_c.values())
    L.append(f"Components6 : {len(communs)} apparies, {len(set(ia) - set(ib))} seulement A, {len(set(ib) - set(ia))} seulement B ; "
             f"champs differents : " + (", ".join(f"{k} x{len(v)} (ex. {v[0]})" for k, v in sorted(diff_c.items())) or "aucun"))
    for st in ["Pads6", "Tracks6", "Arcs6", "Vias6", "Fills6", "Texts6", "Regions6", "ShapeBasedRegions6",
               "ComponentBodies6", "ShapeBasedComponentBodies6"]:
        fa, fb = A.data(st), B.data(st)
        if not isinstance(fa, FluxPrimitives) or not isinstance(fb, FluxPrimitives):
            continue
        la, lb, poly = [], [], [0, 0]
        for lst, doc, D, k in ((la, A, DA, 0), (lb, B, DB, 1)):
            for pr in doc.data(st):
                b = pr.bloc
                if b["composant"] == 0xFFFF and b["polygone"] != 0xFFFF:
                    poly[k] += 1
                    continue
                if st == "Texts6" and not avec_textes_auto and (b.get("est_designateur") or b.get("est_commentaire")):
                    continue
                c, p = _cle(doc, pr, D)
                lst.append((c, p, pr))
        seuil = 10 * tol if st in ("Tracks6", "Vias6") else 2000
        if st == "Texts6":
            seuil = 10 ** 9
        paires, sa, sb = _apparier(la, lb, seuil)
        compte = {}
        for ea, eb in paires:
            for k, va, vb in _diff_champs(ea[2], eb[2], tol):
                compte.setdefault(k, []).append((ea[0][0], va, vb))
        ecarts += len(sa) + len(sb) + sum(len(v) for v in compte.values())
        txt = f"{st} : A {len(fa)}, B {len(fb)} ; apparies {len(paires)}, seulement A {len(sa)}, seulement B {len(sb)}"
        if poly != [0, 0]:
            txt += f" ; cuivre coule de polygones (non compare) A {poly[0]} / B {poly[1]}"
        if st == "Texts6" and not avec_textes_auto:
            txt += " ; designateurs et commentaires exclus"
        L.append(txt)
        for k, v in sorted(compte.items(), key=lambda x: -len(x[1]))[:12]:
            L.append(f"    {k} : {len(v)} differences (ex. {v[0]})")
        if not resume:
            for k, v in sorted(compte.items()):
                for x in v[:5]:
                    L.append(f"    detail {k} : {x}")
            for e in sa[:10]:
                L.append(f"    seulement A : {e[0]} {e[1]}")
            for e in sb[:10]:
                L.append(f"    seulement B : {e[0]} {e[1]}")
    return L, ecarts


if __name__ == "__main__":
    sys.exit(main())
