# -*- coding: utf-8 -*-
"""Lit ci_campagne.json et ecrit les sorties du workflow GitHub Actions : liste des graines et options de campagnes.py."""
import json, os

c = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ci_campagne.json'), encoding='utf-8'))
n = min(int(c['nombre']), 256)   # limite d'une matrice GitHub Actions
print('graines=' + json.dumps(list(range(int(c['graine_base']), int(c['graine_base']) + n))))
o = ['--boucle', str(int(c['boucle'])), '--budget-s', str(int(c['budget_s'])), '--mouvements', str(int(c['mouvements']))]
if c.get('bloc') == 'fige':
    o.append('--bloc-fige')
print('options=' + ' '.join(o))
