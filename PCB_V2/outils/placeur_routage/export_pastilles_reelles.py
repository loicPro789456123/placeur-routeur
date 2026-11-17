# -*- coding: utf-8 -*-
"""Geometrie reelle des pastilles (verite Altium, altium_rev3/pastilles.json) -> pastilles_reelles.json.

  python -I export_pastilles_reelles.py [placement.json]

Pour chaque piece de donnees_placeur.json et chaque pastille (meme ordre), on retrouve la pastille Altium de meme
piece et de meme nom la plus proche de la position calculee avec le placement donne (defaut :
depart_iteration2.json, placement pose dans Altium rev3), et on en tire :
  forme : "rect" (TopShape 2), "rond" (TopShape 1, X = Y), "oblong" (TopShape 1, X != Y : extremites rondes) ;
  hw, hh : demi-dimensions du CUIVRE (TopXSize / TopYSize, pas les Bounds qui incluent 4 mil de masque),
           dans le repere local de la piece (comme les autres donnees locales) ;
  th : traversante (Layer = MultiLayer), trou : diametre de percage.
Les pastilles libres (trous M3, MH1..MH5, sans piece) sont listees dans "libres" (mm).
Un controle signale l'ecart de position maximal entre le modele et Altium.
"""
import json, os, sys, math

ICI = os.path.dirname(os.path.abspath(__file__))
MIL = 0.0254


def fwd(lx, ly, rot, bot):
    vx, vy = (lx, -ly) if bot else (lx, ly)
    return {0: (vx, vy), 90: (-vy, vx), 180: (-vx, -vy)}.get(rot, (vy, -vx))


def main():
    pl_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ICI, 'depart_iteration2.json')
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    A = json.load(open(os.path.join(ICI, 'altium_rev3', 'pastilles.json'), encoding='utf-8'))
    pose = {c['nom']: tuple(c['pose_actuelle']) for c in D['composants']}
    for e in json.load(open(pl_path, encoding='utf-8')):
        pose[e['designator']] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
    par_comp = {}
    for p in A:
        par_comp.setdefault(p['Component'], []).append(p)
    out = {}
    pire = (0.0, '')
    pire_taille = (0.0, '')
    utilises = set()
    for c in D['composants']:
        ox, oy, rot, bot = pose[c['nom']]
        lst = []
        for p in c['pastilles']:
            dx, dy = fwd(p['lx'], p['ly'], rot, bot)
            x, y = ox + dx, oy + dy
            cands = [q for q in par_comp.get(c['nom'], []) if q['Name'] == p['nom'] and id(q) not in utilises]
            if not cands:
                raise SystemExit('pastille Altium introuvable : %s.%s' % (c['nom'], p['nom']))
            q = min(cands, key=lambda q: math.hypot(float(q['X']) * MIL - x, float(q['Y']) * MIL - y))
            utilises.add(id(q))
            d = math.hypot(float(q['X']) * MIL - x, float(q['Y']) * MIL - y)
            if d > pire[0]:
                pire = (d, '%s.%s' % (c['nom'], p['nom']))
            r = int(float(q['Rotation'])) % 360
            sx, sy = float(q['TopXSize']) * MIL, float(q['TopYSize']) * MIL
            wx, wy = (sx, sy) if r in (0, 180) else (sy, sx)          # dimensions monde
            hw, hh = ((wx / 2, wy / 2) if rot in (0, 180) else (wy / 2, wx / 2))  # repere local de la piece
            forme = 'rect' if q['TopShape'] == '2' else ('rond' if abs(sx - sy) < 1e-6 else 'oblong')
            if q['TopShape'] not in ('1', '2'):
                raise SystemExit('forme inconnue %s pour %s.%s' % (q['TopShape'], c['nom'], p['nom']))
            dt = max(abs(hw - p['hw']), abs(hh - p['hh']))
            if dt > pire_taille[0]:
                pire_taille = (dt, '%s.%s' % (c['nom'], p['nom']))
            lst.append(dict(forme=forme, hw=hw, hh=hh, th=q['Layer'] == 'MultiLayer', trou=float(q['HoleSize']) * MIL,
                            reseau_altium=q['Net']))
            if (q['Net'] or '') != (p['reseau'] or ''):
                print('ATTENTION reseau different %s.%s : modele %r, Altium %r' % (c['nom'], p['nom'], p['reseau'], q['Net']))
        out[c['nom']] = lst
    libres = [dict(nom=q['Name'], x=float(q['X']) * MIL, y=float(q['Y']) * MIL, d=float(q['TopXSize']) * MIL, trou=float(q['HoleSize']) * MIL)
              for q in A if q['Component'] in ('', 'Free')]
    json.dump(dict(_doc=__doc__, placement=os.path.basename(pl_path), pieces=out, libres=libres),
              open(os.path.join(ICI, 'pastilles_reelles.json'), 'w', encoding='utf-8'), indent=1)
    print('pastilles : %d pieces ; ecart de position max modele/Altium %.4f mm (%s) ; ecart de demi-taille max ancien/reel %.3f mm (%s) ; %d pastilles libres'
          % (len(out), pire[0], pire[1], pire_taille[0], pire_taille[1], len(libres)))
    nb = sum(len(v) for v in out.values())
    print('pastilles appariees : %d sur %d dans Altium' % (nb, len(A)))


if __name__ == '__main__':
    main()
