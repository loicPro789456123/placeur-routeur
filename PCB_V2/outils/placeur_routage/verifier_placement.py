# -*- coding: utf-8 -*-
"""Controle independant d'un placement produit par placeur_routage (code Python distinct du Rust).

Lancer : python -I verifier_placement.py <placements_routage.json>

Reutilise placer_v3.independent_check (comparaisons deux a deux sans grille : boites + 0,25 mm,
pastilles traversantes + 0,6 mm, fentes + 1 mm, trous M3, marge de bord, zones SMA, corps des fixes,
emprise du bloc sur deux faces, regles de face et de rotation, noeud SW >= 30 mm, colonne centrale)
et ajoute : pieces fixes non deplacees, bloc translate d'un seul tenant (sans rotation ni changement de face),
origines sur la grille de 5 mil.
Bloc libere (variable d'environnement PLACEUR_BLOC_LIBRE=1, campagnes de l'iteration 7 bis) : les pieces du bloc sont
controlees comme des pieces mobiles ; les controles du bloc rigide (translation d'un seul tenant, emprise, colonne
centrale) sont sans objet et retires ; le noeud SW >= 30 mm des broches 27-36 des U25 reste controle (centre de M1).
"""
import copy, json, os, sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(ICI), 'v3'))
import placer_v3 as p  # noqa: E402

MIL = p.MIL


def main():
    pl = json.load(open(sys.argv[1], encoding='utf-8'))
    comps, idx, plan = p.load()
    byname = {c['name']: c for c in comps}
    ent = {e['designator']: e for e in pl}
    viol = []
    fixed_same = set(plan['fixes_inchanges'])
    for n in fixed_same:
        if n in ent:
            c = byname[n]
            e = ent[n]
            if abs(e['x'] * MIL - c['ox']) > 1e-3 or abs(e['y'] * MIL - c['oy']) > 1e-3 or e['rotation'] != c['rot'] or (e['side'] == 'bottom') != c['bottom']:
                viol.append('piece fixe %s deplacee' % n)
    for n, v in plan['fixes_deplaces'].items():
        e = ent.get(n)
        c = byname[n]
        if e is None:
            viol.append('%s absent' % n)
            continue
        if abs(e['x'] * MIL - v['origine'][0]) > 1e-3 or abs(e['y'] * MIL - v['origine'][1]) > 1e-3 or e['rotation'] != c['rot'] or (e['side'] == 'bottom') != c['bottom']:
            viol.append('piece fixe deplacee %s pas a la position du plan' % n)
    libre = os.environ.get('PLACEUR_BLOC_LIBRE') == '1'
    if libre:
        plan = copy.deepcopy(plan)
        plan['bloc_rigide_hacheur']['dessus'] = []
        plan['bloc_rigide_hacheur']['dessous'] = []
    bl = plan['bloc_rigide_hacheur']
    blk = bl['dessus'] + bl['dessous']
    deltas = set() if blk else {(0.0, 0.0)}
    for n in blk:
        e = ent[n]
        c = byname[n]
        if e['rotation'] != c['rot'] or (e['side'] == 'bottom') != c['bottom']:
            viol.append('bloc : %s tourne ou change de face' % n)
        deltas.add((e['x'] - c['ox'] / MIL, e['y'] - c['oy'] / MIL))
    dx = [a for a, b in deltas]
    dy = [b for a, b in deltas]
    if max(dx) - min(dx) > 0.05 or max(dy) - min(dy) > 0.05:  # tolerance 0,05 mil (sorties arrondies a 0,01 mil)
        viol.append('bloc non translate d\'un seul tenant : %s' % deltas)
    d = (round(sum(dx) / len(dx), 1), round(sum(dy) / len(dy), 1))
    delta = (d[0] * MIL, d[1] * MIL)
    poses = {}
    for n, e in ent.items():
        if n in fixed_same or n in plan['fixes_deplaces'] or n in blk:
            continue
        poses[n] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
        for v in (e['x'], e['y']):
            if abs(v / 5 - round(v / 5)) > 1e-3:
                viol.append('%s hors grille 5 mil (%s)' % (n, v))
    manquants = [c['name'] for c in comps if c['name'] not in fixed_same and c['name'] not in ent]
    if manquants:
        viol.append('pieces absentes du placement : %s' % manquants)
    v2, chk = p.independent_check(comps, idx, plan, poses, delta)
    if libre:
        v2 = [v for v in v2 if 'emprise' not in v and 'colonne' not in v]
    viol += v2
    print('Controle independant : %d violation(s)' % len(viol))
    for v in viol[:60]:
        print('  - ' + v)
    print('Bloc : translation (%.2f ; %.2f) mm ; noeud SW a %.1f mm des broches 27-36 des U25' % (delta[0], delta[1], chk['sw_u25']))
    for n, dd in chk['soft'].items():
        print('  %s a %.1f mm du noeud SW' % (n, dd))
    faces = {'top': 0, 'bottom': 0}
    for e in pl:
        faces[e['side']] += 1
    print('Pieces listees : %d (dessus %d, dessous %d)' % (len(pl), faces['top'], faces['bottom']))
    sys.exit(1 if viol else 0)


if __name__ == '__main__':
    main()
