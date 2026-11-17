# -*- coding: utf-8 -*-
"""Images zoomees des connexions restees en echec (une par broche), dans <dossier>/blocages/.

  python blocages.py <placement.json> <routage_detaille.json> <dossier_sortie>
"""
import json, os, subprocess, sys

ICI = os.path.dirname(os.path.abspath(__file__))
MIL = 0.0254


def fwd(lx, ly, rot, bot):
    vx, vy = (lx, -ly) if bot else (lx, ly)
    return {0: (vx, vy), 90: (-vy, vx), 180: (-vx, -vy)}.get(rot, (vy, -vx))


def main():
    pl, rt, out = sys.argv[1], sys.argv[2], sys.argv[3]
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    pose = {c['nom']: tuple(c['pose_actuelle']) for c in D['composants']}
    for e in json.load(open(pl, encoding='utf-8')):
        pose[e['designator']] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
    comps = {c['nom']: c for c in D['composants']}
    RT = json.load(open(rt, encoding='utf-8'))
    dossier = os.path.join(out, 'blocages')
    os.makedirs(dossier, exist_ok=True)
    n = 0
    for e in RT.get('echecs', []):
        net = e['net']
        autres = []
        for c in D['composants']:
            for p in c['pastilles']:
                if p['reseau'] == net:
                    autres.append('%s.%s' % (c['nom'], p['nom']))
        for dg in e['diagnostic']:
            nom, cause = dg.split(' : ', 1)
            if '.' not in nom or nom.split('.')[0] not in comps:
                continue
            cn, pn = nom.rsplit('.', 1)
            c = comps[cn]
            p = [q for q in c['pastilles'] if q['nom'] == pn][0]
            ox, oy, rot, bot = pose[cn]
            dx, dy = fwd(p['lx'], p['ly'], rot, bot)
            x, y = ox + dx, oy + dy
            fen = [x - 7, y - 5, x + 7, y + 5]
            if 'via de plan' in cause:
                opts = "options : ecarter les pieces voisines pour liberer un emplacement de via, via dans la pastille (derogation GND), ou piste plus longue vers un via GND existant"
            else:
                opts = "options : deplacer / tourner les passifs voisins, changer de face, elargir la carte vers l'arriere, ou isolement local 0,15 mm"
            leg = '%s (%s) : %s. %s' % (nom, net, cause, opts)
            png = os.path.join(dossier, '%s_%s.png' % (net.replace('+', 'p').replace('-', 'm').replace('/', '_'), nom.replace('.', '_')))
            args = [sys.executable, os.path.join(ICI, 'visualiser.py'), pl, rt, png] + [str(v) for v in fen] + ['--net', net, '--pin', nom, '--titre', 'Blocage %s (%s)' % (nom, net), '--legende', leg]
            for a in autres:
                args += ['--pin', a]
            subprocess.run(args, check=True)
            n += 1
    print('%d image(s) de blocage dans %s' % (n, dossier))


if __name__ == '__main__':
    main()
