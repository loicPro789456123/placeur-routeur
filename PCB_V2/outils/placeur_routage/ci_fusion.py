# -*- coding: utf-8 -*-
"""Fusion des campagnes lancees une par machine (GitHub Actions).

  python ci_fusion.py <dossier_des_artefacts> --dossier <nom>   [--bloc-fige]

Chaque artefact contient campagnes.csv (references + une campagne) et c_<graine>/. Le script reconstruit
sorties/<nom>/ comme l'aurait fait campagnes.py sur une seule machine : campagnes.csv, confiance.md, meilleur/,
puis ecrit resume.md (tableau lisible dans l'onglet Actions)."""
import glob, os, shutil, sys
import campagnes as c

src = sys.argv[1]
os.makedirs(c.RACINE, exist_ok=True)
rows, vus = [], set()
for f in sorted(glob.glob(os.path.join(src, '*', 'campagnes.csv'))):
    for r in c.lire_csv(f):
        cle = (r['source'], r['graine'])
        if cle in vus:
            continue
        vus.add(cle)
        rows.append(r)
    d0 = os.path.dirname(f)
    for d in glob.glob(os.path.join(d0, 'c_*')) + glob.glob(os.path.join(d0, 'ref_*')):
        dst = os.path.join(c.RACINE, os.path.basename(d))
        if not os.path.exists(dst):
            shutil.copytree(d, dst)
rows.sort(key=lambda r: (r['source'] == 'campagne', int(r['graine'] or 0)))
fcsv = os.path.join(c.RACINE, 'campagnes.csv')
if os.path.exists(fcsv):
    os.remove(fcsv)
for r in rows:
    c.ajouter_csv(fcsv, r)
eps, k = c.arg('--eps', 0.01), int(c.arg('--k', 5))
conf = c.confiance(rows, eps, k)
c.ecrire_rapport(rows, None, conf, eps, c.arg('--confiance', 0.99), k, int(c.arg('--n-min', 12)))
camp = [r for r in rows if r['source'] == 'campagne']
val = sorted((r for r in camp if r['valide'] == '1'), key=lambda r: float(r['cout']))
if val:
    b = val[0]
    dc = os.path.join(c.RACINE, 'c_%s' % b['graine'])
    c.copier_meilleur(os.path.join(dc, 'placements_routage.json'), os.path.join(dc, 'routage_detaille.json'),
                      os.path.join(dc, 'vias_couture.json'), dict(source='campagne', graine=b['graine'], cout=b['cout']))
L = ['## Campagne placeur-routeur (%s)' % ('bloc fige' if c.FIGE else 'bloc libre'), '',
     '%d campagnes, **%d valides** (%.0f %%)' % (len(camp), len(val), 100.0 * len(val) / max(1, len(camp))), '',
     '| Source | Graine | Valide | Cout | Longueur mm | Vias | Retreci mm | Chute +8V c1 / c2 mV | Duree s | Cause |',
     '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
for r in [r for r in rows if r['source'] != 'campagne'] + val + [r for r in camp if r['valide'] != '1']:
    L.append('| %s | %s | %s | %s | %s | %s | %s | %s / %s | %s | %s |' % tuple(r.get(x, '') for x in (
        'source', 'graine', 'valide', 'cout', 'longueur_mm', 'vias', 'retreci_mm', 'chute_8v_c1_mv', 'chute_8v_c2_mv', 'duree_s', 'cause')))
open(os.path.join(c.RACINE, 'resume.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')
print('\n'.join(L))
