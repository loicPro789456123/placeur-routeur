# -*- coding: utf-8 -*-
"""Contrôleur indépendant de la sérigraphie (géométrie Python recalculée, sans le code Rust).

  python drc_serigraphie.py --entree D [--apres RESULTAT.json] [--json SORTIE.json]
                            [--commentaires visibles|caches] [--texte-ttf bounds|aucun] [--texte-trait encre|bounds]
                            [--regle MIL] [--reference cuivre|masque]

--entree : dossier des fichiers d'entrée du moteur, ``carte.json`` compris (obligatoire : contour, découpes, trous et
règles de la carte). AVANT (sans --apres) : état d'entrée. APRÈS : état d'entrée + modifications du moteur.

Règle « Silk To Solder Mask » : toute primitive de sérigraphie (piste, arc, texte) à moins de la garde
(``regles.garde_masque_mm`` de carte.json, ou --regle) d'une pastille de la même face ; pastille traversante = deux
faces. Une violation = un couple (primitive, pastille). Contrôles complémentaires (APRÈS) : désignateurs (taille,
sens, chevauchements, hors carte, ambiguïté), sérigraphie sur via (souple), garde au bord et aux découpes.
Codes : 0 fait ; 2 entrée invalide (message sans chemin) ; 4 erreur interne.
"""
import json, os, sys, math, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geom_py import (MIL, dist, boite, forme_pastille, arc_en_segments, forme_texte_trait, largeur_encre, rot,
                     d_pt_seg, d_seg_seg, dans_polygone)

FACE = {'seri_dessus': 'top', 'seri_dessous': 'bottom'}
FACES_PASTILLE = {'dessus': ('top',), 'dessous': ('bottom',), 'traversante': ('top', 'bottom')}
FICHIERS = ('textes', 'pastilles', 'composants', 'vias', 'pistes_serigraphie_top', 'pistes_serigraphie_bottom',
            'arcs_serigraphie_top', 'arcs_serigraphie_bottom')


class ErreurEntree(Exception):
    """Entrée invalide : message en français, sans chemin."""


def lire_json(chemin):
    nom = os.path.basename(chemin)
    try:
        with open(chemin, encoding='utf-8') as f:
            return json.load(f)
    except OSError:
        raise ErreurEntree('%s : fichier absent ou illisible' % nom) from None
    except ValueError as e:
        raise ErreurEntree('%s : JSON invalide (%s)' % (nom, type(e).__name__)) from None


def charger_carte(dossier):
    """carte.json (carte_serigraphie/1) : contour, découpes (polygones, mil), trous, règles (mm)."""
    c = lire_json(os.path.join(dossier, 'carte.json'))
    if not isinstance(c, dict) or not str(c.get('format', '')).startswith('carte_serigraphie/1'):
        raise ErreurEntree('carte.json : format carte_serigraphie/1 attendu')
    try:
        contour = [(float(x), float(y)) for x, y in c['contour']]
        decoupes = [[(float(x), float(y)) for x, y in d] for d in c.get('decoupes', [])]
        trous = [(float(x), float(y), float(rr)) for x, y, rr in c.get('trous', [])]
        R = c['regles']
        regles = dict(garde_masque_mm=float(R['garde_masque_mm']), garde_bord_mm=float(R['garde_bord_mm']),
                      tailles_mm=[(float(h), float(w)) for h, w in R['tailles_mm']])
    except (KeyError, TypeError, ValueError):
        raise ErreurEntree('carte.json : contour, découpes, trous ou règles invalides') from None
    if len(contour) < 3 or not regles['tailles_mm']:
        raise ErreurEntree('carte.json : contour ou tailles absents')
    return dict(contour=contour, decoupes=decoupes, trous=trous, regles=regles)


def charger(dossier):
    if not os.path.isdir(dossier):
        raise ErreurEntree('--entree : dossier absent')
    carte = charger_carte(dossier)
    L = {n: lire_json(os.path.join(dossier, n + '.json')) for n in FICHIERS}
    for n, v in L.items():
        if not isinstance(v, list) or not all(isinstance(e, dict) for e in v):
            raise ErreurEntree('%s.json : liste d\'objets attendue' % n)
    return dict(textes=L['textes'], pastilles=L['pastilles'], composants=L['composants'], vias=L['vias'],
                pistes=L['pistes_serigraphie_top'] + L['pistes_serigraphie_bottom'],
                arcs=L['arcs_serigraphie_top'] + L['arcs_serigraphie_bottom'], carte=carte)


class Grille:
    def __init__(self, pas=100.0):
        self.pas = pas
        self.c = collections.defaultdict(list)

    def cles(self, b):
        p = self.pas
        for i in range(int(math.floor(b[0] / p)), int(math.floor(b[2] / p)) + 1):
            for j in range(int(math.floor(b[1] / p)), int(math.floor(b[3] / p)) + 1):
                yield (i, j)

    def ajoute(self, b, obj):
        for k in self.cles(b):
            self.c[k].append(obj)

    def proches(self, b, marge):
        bb = (b[0] - marge, b[1] - marge, b[2] + marge, b[3] + marge)
        vus = set()
        for k in self.cles(bb):
            for o in self.c.get(k, ()):
                if id(o) not in vus:
                    vus.add(id(o))
                    yield o


def k3(v):
    return '%.3f' % float(v)


def cle_piste(t):
    return (t['Layer'], k3(t['X1']), k3(t['Y1']), k3(t['X2']), k3(t['Y2']))


def cle_arc(a):
    return (a['Layer'], k3(a['XCenter']), k3(a['YCenter']), k3(a['Radius']), k3(a['StartAngle']), k3(a['EndAngle']))


def faces_pastille(p):
    return FACES_PASTILLE[p['Layer']]


def primitives(D, apres, opt):
    """liste de primitives de serigraphie : dict(face, forme, type, comp, nom)."""
    prims = []
    sup_p = set((t['layer'], k3(t['x1']), k3(t['y1']), k3(t['x2']), k3(t['y2'])) for t in apres.get('pistes_supprimer', [])) if apres else set()
    sup_a = set((a['layer'], k3(a['xcenter']), k3(a['ycenter']), k3(a['radius']), k3(a['start_angle']), k3(a['end_angle']))
                for a in apres.get('arcs_supprimer', [])) if apres else set()
    trouve_p = sum(1 for t in D['pistes'] if cle_piste(t) in sup_p)
    trouve_a = sum(1 for a in D['arcs'] if cle_arc(a) in sup_a)
    if apres and (trouve_p != len(sup_p) or trouve_a != len(sup_a)):
        print('ATTENTION : objets a supprimer introuvables dans l\'entree : pistes %d/%d, arcs %d/%d' % (trouve_p, len(sup_p), trouve_a, len(sup_a)))
    for t in D['pistes']:
        if cle_piste(t) in sup_p:
            continue
        w = float(t['Width'])
        prims.append(dict(face=FACE[t['Layer']], type='piste', comp=t['Component'], ref=cle_piste(t),
                          forme=('seg', (float(t['X1']), float(t['Y1'])), (float(t['X2']), float(t['Y2'])), w / 2)))
    for a in D['arcs']:
        if cle_arc(a) in sup_a:
            continue
        w = float(a['Width'])
        pts = arc_en_segments(float(a['XCenter']), float(a['YCenter']), float(a['Radius']), float(a['StartAngle']),
                              float(a['EndAngle']))
        for i in range(len(pts) - 1):
            prims.append(dict(face=FACE[a['Layer']], type='arc', comp=a['Component'], ref=cle_arc(a),
                              forme=('seg', pts[i], pts[i + 1], w / 2)))
    if apres:
        for t in apres.get('pistes_ajouter', []):
            prims.append(dict(face=FACE[t['layer']], type='piste+', comp=t.get('component', ''),
                              ref=('ajout', t['x1'], t['y1'], t['x2'], t['y2']),
                              forme=('seg', (t['x1'], t['y1']), (t['x2'], t['y2']), t['width'] / 2)))
    remplaces = set(x['component'] for x in apres.get('textes', [])) if apres else set()
    for t in D['textes']:
        if t['Layer'] not in FACE:
            continue
        if t['IsComment'] == 'true' and opt.commentaires == 'caches':
            continue
        if t['IsDesignator'] == 'true' and t['Component'] in remplaces:
            continue
        if t['IsComment'] == 'true' and apres and apres.get('commentaires_caches'):
            continue
        h, w = float(t['Height']), float(t['StrokeWidth'])
        if t['UseTTFonts'] == 'true':
            if opt.texte_ttf == 'aucun':
                continue
            b = [float(t[k]) for k in ('BoundsLeft', 'BoundsBottom', 'BoundsRight', 'BoundsTop')]
            F = ('poly', [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])], 0.0)
        else:
            F = forme_texte_trait(t['Text'], float(t['X']), float(t['Y']), h, w, float(t['Rotation']),
                                  t['MirrorFlag'] == 'true', gonfle=(w if opt.texte_trait == 'bounds' else None))
        prims.append(dict(face=FACE[t['Layer']], type='texte', comp=t['Component'], ref=('texte', t['Component'], t['Text']),
                          forme=F, des=t['IsDesignator'] == 'true'))
    if apres:
        for t in apres.get('textes', []):
            if t['hidden']:
                continue
            F = forme_texte_trait(t['text'], t['x'], t['y'], t['height'], t['stroke_width'], t['rotation'], t['mirror'])
            prims.append(dict(face=FACE[t['layer']], type='texte', comp=t['component'], ref=('texte', t['component'], t['text']),
                              forme=F, des=True, nouveau=t))
    for p in prims:
        p['boite'] = boite(p['forme'])
    return prims


def violations_masque(D, prims, regle, reference='cuivre'):
    G = {'top': Grille(), 'bottom': Grille()}
    for p in D['pastilles']:
        F = forme_pastille(p, expansion=(reference == 'masque'))
        q = dict(p=p, forme=F, boite=boite(F))
        for f in faces_pastille(p):
            G[f].ajoute(q['boite'], q)
    V = []
    for s in prims:
        for q in G[s['face']].proches(s['boite'], regle + 1):
            d = dist(s['forme'], q['forme'])
            if d < regle - 1e-3:
                V.append(dict(type=s['type'], comp=s['comp'], ref=s['ref'], pastille='%s.%s' % (q['p']['Component'], q['p']['Name']),
                              face=s['face'], d_mm=round(d * MIL, 4)))
    # un arc decoupe en segments compte une fois par pastille
    vus = set()
    out = []
    for v in V:
        k = (v['ref'], v['pastille'], v['face'])
        if k not in vus:
            vus.add(k)
            out.append(v)
    return out


def violations_vias(D, prims, regle):
    G = Grille()
    for v in D['vias']:
        r = float(v['Size']) / 2 + 4.0
        F = ('seg', (float(v['X']), float(v['Y'])), (float(v['X']), float(v['Y'])), r)
        G.ajoute(boite(F), dict(forme=F, boite=boite(F)))
    n = 0
    for s in prims:
        for q in G.proches(s['boite'], regle):
            if dist(s['forme'], q['forme']) < regle - 1e-3:
                n += 1
    return n


def _d_boite_polygone(b, pts):
    """plus petite distance entre les arêtes de la boîte b et celles du polygone pts (0 si elles se coupent)."""
    c = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
    return min(d_seg_seg(c[i], c[(i + 1) % 4], pts[k], pts[(k + 1) % len(pts)]) for i in range(4) for k in range(len(pts)))


def boite_dans_carte(b, carte):
    """boîte (mil) dans le contour, hors des découpes et des trous, à la garde au bord près."""
    g = carte['regles']['garde_bord_mm'] / MIL
    c = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
    if not all(dans_polygone(q, carte['contour']) for q in c) or _d_boite_polygone(b, carte['contour']) < g:
        return False
    for d in carte['decoupes']:
        if any(dans_polygone(q, d) for q in c) or any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for x, y in d):
            return False
        if _d_boite_polygone(b, d) < g:
            return False
    for x, y, rr in carte['trous']:
        dx = max(b[0] - x, x - b[2], 0.0)
        dy = max(b[1] - y, y - b[3], 0.0)
        if math.hypot(dx, dy) < rr + g:
            return False
    return True


def hors_carte(prims, carte):
    return [s for s in prims if not boite_dans_carte(s['boite'], carte)]


def corps(D):
    """corps estime de chaque composant : boite des pastilles (cuivre) et des traits de serigraphie de l'empreinte."""
    B = {}
    def ajoute(c, b):
        if not c:
            return
        o = B.get(c)
        B[c] = b if o is None else (min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3]))
    for p in D['pastilles']:
        ajoute(p['Component'], boite(forme_pastille(p, expansion=False)))
    for t in D['pistes']:
        ajoute(t['Component'], (min(float(t['X1']), float(t['X2'])), min(float(t['Y1']), float(t['Y2'])),
                                max(float(t['X1']), float(t['X2'])), max(float(t['Y1']), float(t['Y2']))))
    return B


def d_boites(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def controles_designateurs(D, prims, apres):
    """verifications des nouveaux designateurs (APRES)."""
    R = collections.Counter()
    details = []
    B = corps(D)
    face_comp = {c['Designator']: ('bottom' if c['Layer'] == 'dessous' else 'top') for c in D['composants']}
    hmin, wmin = min(D['carte']['regles']['tailles_mm'])
    nouveaux = [s for s in prims if s.get('nouveau')]
    for s in nouveaux:
        t = s['nouveau']
        c = t['component']
        if t['height'] < hmin / MIL - 0.01 or t['stroke_width'] < wmin / MIL - 0.01:
            R['taille sous le minimum'] += 1
        if int(round(t['rotation'])) % 360 not in ((0, 90) if t['layer'] == 'seri_dessus' else (0, 270)):
            R['sens de lecture interdit'] += 1
        if (t['layer'] == 'seri_dessous') != bool(t['mirror']):
            R['miroir incorrect'] += 1
        if FACE.get(t['layer']) != face_comp.get(c):
            R['mauvaise face'] += 1
        b = s['boite']
        # ambiguite : distance boite texte -> corps du composant vs autres composants de la meme face
        if c not in B:
            continue
        dp = d_boites(b, B[c])
        for o, bo in B.items():
            if o != c and face_comp.get(o) == face_comp[c] and d_boites(b, bo) < dp - 1e-6:
                R['ambigu (plus proche d\'un autre corps)'] += 1
                details.append(('ambigu', c, o))
                break
        for o, bo in B.items():
            if o != c and face_comp.get(o) == face_comp[c]:
                if b[0] < bo[2] and b[2] > bo[0] and b[1] < bo[3] and b[3] > bo[1]:
                    R['sur le corps d\'un autre composant (boite)'] += 1
                    details.append(('sur corps', c, o))
                    break
    # chevauchements texte / serigraphie
    G = {'top': Grille(), 'bottom': Grille()}
    for s in prims:
        G[s['face']].ajoute(s['boite'], s)
    for s in nouveaux:
        for o in G[s['face']].proches(s['boite'], 1):
            if o is s:
                continue
            if o['type'] == 'texte' and o.get('nouveau') is not None and id(o) < id(s):
                continue
            if dist(s['forme'], o['forme']) <= 0.0:
                k = 'chevauche un autre texte' if o['type'] == 'texte' else 'chevauche un trait'
                R[k] += 1
                details.append((k, s['comp'], o['comp']))
    R['hors carte / garde bord-decoupes'] = len(hors_carte(nouveaux, D['carte']))
    return R, details


class _Analyseur(argparse.ArgumentParser):
    def error(self, message):
        raise ErreurEntree('options : ' + message)


def analyser(argv):
    ap = _Analyseur(prog='drc_serigraphie', add_help=False)
    ap.add_argument('--entree', required=True)
    ap.add_argument('--apres', default=None)
    ap.add_argument('--json', default=None)
    ap.add_argument('--commentaires', default='caches', choices=['visibles', 'caches'])
    ap.add_argument('--texte-ttf', dest='texte_ttf', default='bounds', choices=['bounds', 'aucun'])
    ap.add_argument('--texte-trait', dest='texte_trait', default='encre', choices=['encre', 'bounds'])
    ap.add_argument('--regle', type=float, default=None, help='garde en mil (défaut : carte.json)')
    ap.add_argument('--reference', default='cuivre', choices=['cuivre', 'masque'])
    return ap.parse_args(argv)


def controler(opt):
    """Contrôle complet : rend (dict résultat, lignes du rapport)."""
    D = charger(opt.entree)
    apres = lire_json(opt.apres) if opt.apres else None
    regle = opt.regle if opt.regle is not None else D['carte']['regles']['garde_masque_mm'] / MIL
    lignes = []
    try:
        prims = primitives(D, apres, opt)
        V = violations_masque(D, prims, regle, opt.reference)
    except (KeyError, TypeError, ValueError):
        raise ErreurEntree('fichiers d\'entrée incomplets ou invalides') from None
    lignes.append('=== %s : serigraphie / pastilles (%s, < %.3f mm) : %d violations' % (
        'APRES' if apres else 'AVANT', opt.reference, regle * MIL, len(V)))
    c = collections.Counter((v['type'], v['face']) for v in V)
    for k, n in sorted(c.items()):
        lignes.append('   %-8s %-7s %d' % (k[0], k[1], n))
    lignes.append('   textes designateurs en violation : %d, autres textes (libres) : %d' % (
        sum(1 for v in V if v['type'] == 'texte' and v['ref'][2] == v['comp']),
        sum(1 for v in V if v['type'] == 'texte' and v['ref'][2] != v['comp'])))
    nv = violations_vias(D, prims, regle)
    lignes.append('   serigraphie trop pres d\'un via (souple) : %d' % nv)
    hc = hors_carte([s for s in prims if s['type'] != 'texte' or s.get('des')], D['carte'])
    lignes.append('   primitives hors carte / dans la garde au bord ou aux decoupes : %d' % len(hc))
    res = dict(violations=V, n=len(V), vias=nv, hors_carte=len(hc))
    if apres:
        R, det = controles_designateurs(D, prims, apres)
        nt = apres.get('textes', [])
        hmax = max(D['carte']['regles']['tailles_mm'])[0]
        lignes.append('   designateurs : %d, masques %d, reduits %d' % (
            len(nt), sum(1 for t in nt if t['hidden']), sum(1 for t in nt if not t['hidden'] and t['height'] < hmax / MIL - 0.01)))
        for k, n in sorted(R.items()):
            lignes.append('   %-45s %d' % (k, n))
        res['designateurs'] = dict(R)
        res['details'] = det
    return res, lignes


def main(argv=None):
    """Point d'entrée : rend le code de sortie (0, 2 ou 4)."""
    try:
        opt = analyser(sys.argv[1:] if argv is None else argv)
        res, lignes = controler(opt)
        print('\n'.join(lignes))
        if opt.json:
            tmp = opt.json + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(res, f, indent=1, ensure_ascii=False)
            os.replace(tmp, opt.json)
        return 0
    except ErreurEntree as e:
        print('drc_serigraphie : erreur d\'entrée : %s' % e, file=sys.stderr)
        return 2
    except OSError:
        print('drc_serigraphie : erreur d\'entrée : écriture impossible', file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 : code 4, sans trace ni chemin
        print('drc_serigraphie : erreur interne (%s)' % type(e).__name__, file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
