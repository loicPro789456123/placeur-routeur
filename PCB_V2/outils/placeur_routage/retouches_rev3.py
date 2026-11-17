# -*- coding: utf-8 -*-
"""Report dans les JSON des retouches faites a la main dans Altium apres la pose rev3 (iteration 5).

  python -I retouches_rev3.py

  - vias en double au meme point : supprimes ;
  - paires de vias d'un meme reseau trop proches (trou a trou < 0,254 mm) : ALIM_OFF (2860.236;1383.858) fusionne dans
    (2868.11;1387.795), NetC2_1 (3242.126;895.669) fusionne dans (3242.126;899.606) ; les extremites de pistes qui
    arrivaient au via supprime sont reportees sur le via garde ;
  - NetR25_2 : bout pendant de la piste du bloc rogne (debut x 2701.7712 -> 2738.189) et notre raccord recale sur
    son axe (y 3025.591 -> 3023.78) ;
  - piste NetC19_2 manquante ajoutee a cuivre_bloc_hacheur.json (si absente).
Modifie sorties/routage_detaille.json et cuivre_bloc_hacheur.json (idempotent).
"""
import json, os

ICI = os.path.dirname(os.path.abspath(__file__))


def main():
    f = os.path.join(ICI, 'sorties', 'routage_detaille.json')
    r = json.load(open(f, encoding='utf-8'))
    vu, garde = set(), []
    for v in r['vias']:
        k = (v['net'], v['x'], v['y'])
        if k not in vu:
            vu.add(k)
            garde.append(v)
    r['vias'] = garde
    fusions = [('ALIM_OFF', (2860.236, 1383.858), (2868.11, 1387.795)), ('NetC2_1', (3242.126, 895.669), (3242.126, 899.606))]
    for net, a, b in fusions:
        r['vias'] = [v for v in r['vias'] if not (v['net'] == net and (v['x'], v['y']) == a)]
        for s in r['pistes']:
            if s['net_name'] != net:
                continue
            if (s['x1'], s['y1']) == a:
                s['x1'], s['y1'] = b
            if (s['x2'], s['y2']) == a:
                s['x2'], s['y2'] = b
        r['pistes'] = [s for s in r['pistes'] if (s['x1'], s['y1']) != (s['x2'], s['y2'])]
    for s in r['pistes']:
        if s['net_name'] == 'NetR25_2':
            if (s['x1'], s['y1']) == (2738.189, 3025.591):
                s['y1'] = 3023.78
            if (s['x2'], s['y2']) == (2738.189, 3025.591):
                s['y2'] = 3023.78
    r['retouches_iteration5'] = ['retouches manuelles Altium reportees par retouches_rev3.py (voir son en-tete)']
    json.dump(r, open(f, 'w', encoding='utf-8'), indent=1)
    fc = os.path.join(ICI, 'cuivre_bloc_hacheur.json')
    c = json.load(open(fc, encoding='utf-8'))
    for p in c['pistes']:
        if p[6] == 'NetR25_2' and abs(p[0] - 2701.7712) < 1e-3:
            p[0] = 2738.189
    t = [3257, 2928.504, 3298, 2928.5, 15, 'T', 'NetC19_2']
    if t not in c['pistes']:
        c['pistes'].append(t)
    json.dump(c, open(fc, 'w', encoding='utf-8'), ensure_ascii=False)
    print('retouches reportees : %d vias, %d pistes' % (len(r['vias']), len(r['pistes'])))


if __name__ == '__main__':
    main()
