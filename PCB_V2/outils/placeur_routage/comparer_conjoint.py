# -*- coding: utf-8 -*-
"""Tableau avant / apres de l'optimisation conjointe (iteration 6) -> sorties/conjoint/comparatif_conjoint.md.

  python comparer_conjoint.py

Avant = etat pose sur rev3 (sorties/placements_routage.json + sorties/routage_detaille.json) ; apres =
sorties/conjoint/. Toutes les grandeurs sont recalculees ici ou par drc_maison.py / analyse_8v.py (independants du
routeur) : longueur, vias, longueur retrecie sous la largeur preferee de la classe, chute +8V par canal (2 A),
ecart minimal et moyen au cuivre d'un autre reseau, coudes, liaisons ouvertes, violations, pieces deplacees.
"""
import json, math, os, subprocess, sys

ICI = os.path.dirname(os.path.abspath(__file__))
MIL = 0.0254


def fwd(lx, ly, rot, bot):
    vx, vy = (lx, -ly) if bot else (lx, ly)
    return {0: (vx, vy), 90: (-vy, vx), 180: (-vx, -vy)}.get(rot, (vy, -vx))


def mesures(pl, rt, dossier, tag):
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    R = json.load(open(rt, encoding='utf-8'))
    cl = D['classes']
    larg = D['regles']['largeurs']
    long = sum(math.hypot(s['x2'] - s['x1'], s['y2'] - s['y1']) * MIL for s in R['pistes'])
    retreci = 0.0
    for s in R['pistes']:
        pref = max(larg.get(cl.get(s['net_name'], 'defaut'), larg['defaut'])[1], larg.get(cl.get(s['net_name'], 'defaut'), larg['defaut'])[0])
        if s['net_name'] == 'GND':
            pref = 0.3
        if s['width'] * MIL < pref - 1e-3:
            retreci += math.hypot(s['x2'] - s['x1'], s['y2'] - s['y1']) * MIL
    out = {}
    for mode in ('strict', 'altium'):
        js = os.path.join(dossier, 'drc_%s_%s.json' % (tag, mode))
        args = [sys.executable, '-I', os.path.join(ICI, 'drc_maison.py'), pl, rt, js] + (['--altium'] if mode == 'altium' else [])
        subprocess.run(args, check=True, stdout=subprocess.DEVNULL)
        out[mode] = json.load(open(js, encoding='utf-8'))
    a8 = subprocess.run([sys.executable, os.path.join(ICI, 'analyse_8v.py'), pl, rt], capture_output=True, text=True, check=True).stdout
    chute = json.loads(a8.strip().splitlines()[-1])
    return dict(longueur=long, vias=len(R['vias']), retreci=retreci, chute1=chute.get('R62_ALIM1.2'), chute2=chute.get('R62_ALIM2.2'),
                ecart_min=out['strict']['ecart_min_mm'], ecart_moy=out['strict']['ecart_moyen_mm'], coudes=out['strict']['coudes'],
                ouvertes_s=out['strict']['liaisons_ouvertes'], ouvertes_a=out['altium']['liaisons_ouvertes'],
                violations=out['strict']['violations_routage'], marge=out['strict']['marge_isolement_min_mm'])


def centres(pl):
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    pose = {c['nom']: tuple(c['pose_actuelle']) for c in D['composants']}
    for e in json.load(open(pl, encoding='utf-8')):
        pose[e['designator']] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
    out = {}
    for c in D['composants']:
        ox, oy, rot, bot = pose[c['nom']]
        l = c['lbb']
        cs = [fwd(x, y, rot, bot) for x in (l[0], l[2]) for y in (l[1], l[3])]
        out[c['nom']] = (ox + (min(a for a, b in cs) + max(a for a, b in cs)) / 2, oy + (min(b for a, b in cs) + max(b for a, b in cs)) / 2, rot, bot)
    return out


def main():
    dc = os.path.join(ICI, 'sorties', 'conjoint')
    av = mesures(os.path.join(ICI, 'sorties', 'placements_routage.json'), os.path.join(ICI, 'sorties', 'routage_detaille.json'), dc, 'avant')
    ap = mesures(os.path.join(dc, 'placements_routage.json'), os.path.join(dc, 'routage_detaille.json'), dc, 'apres')
    c0 = centres(os.path.join(ICI, 'sorties', 'placements_routage.json'))
    c1 = centres(os.path.join(dc, 'placements_routage.json'))
    dep = [(n, math.hypot(c1[n][0] - c0[n][0], c1[n][1] - c0[n][1]), c0[n][2] != c1[n][2], c0[n][3] != c1[n][3]) for n in c0 if c0[n] != c1[n]]
    L = ['# Optimisation conjointe placement + routage detaille : avant / apres', '',
         '| Mesure | Avant (pose rev3) | Apres (conjoint) |', '|---|---:|---:|']
    f = lambda v, p=1: ('%.' + str(p) + 'f') % v if isinstance(v, float) else str(v)
    for lab, k, p in [('Longueur de piste (mm)', 'longueur', 1), ('Vias (signal + plan)', 'vias', 0), ('Longueur retrecie sous la largeur preferee (mm)', 'retreci', 1),
                      ('Chute +8V canal 1, 2 A (mV)', 'chute1', 1), ('Chute +8V canal 2, 2 A (mV)', 'chute2', 1),
                      ('Ecart minimal au cuivre d un autre reseau (mm)', 'ecart_min', 3), ('Ecart moyen (mm, plafond 1 mm)', 'ecart_moy', 3),
                      ('Marge d isolement minimale (mm)', 'marge', 4), ('Coudes', 'coudes', 0),
                      ('Liaisons ouvertes (strict / mode Altium)', None, 0), ('Violations DRC maison', 'violations', 0)]:
        if k is None:
            L.append('| %s | %d / %d | %d / %d |' % (lab, av['ouvertes_s'], av['ouvertes_a'], ap['ouvertes_s'], ap['ouvertes_a']))
        else:
            L.append('| %s | %s | %s |' % (lab, f(av[k], p), f(ap[k], p)))
    moy = sum(d for _, d, _, _ in dep) / max(1, len(dep))
    L.append('| Pieces deplacees (dont tournees / changees de face) | - | %d (%d / %d) |' % (len(dep), sum(1 for x in dep if x[2]), sum(1 for x in dep if x[3])))
    L.append('| Deplacement moyen des pieces deplacees (mm) | - | %.2f |' % moy)
    L.append('')
    L.append('Pieces deplacees : ' + ', '.join('%s (%.2f mm%s%s)' % (n, d, ', rot' if r else '', ', face' if fc else '') for n, d, r, fc in sorted(dep)))
    open(os.path.join(dc, 'comparatif_conjoint.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
