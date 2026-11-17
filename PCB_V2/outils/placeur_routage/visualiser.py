# -*- coding: utf-8 -*-
"""Images PNG du routage (matplotlib).

  python -I visualiser.py <placement.json> <routage.json> <sortie.png> [x0 y0 x1 y1] [--net N]... [--pin COMP.PAD]... [--titre T] [--legende L]

Sans fenetre : vue d'ensemble, deux panneaux (dessus, dessous vu de dessus). Avec fenetre (mm) : zoom sur les
deux couches superposees, noms des reseaux sur les pastilles et les pistes. --net met un reseau en
surbrillance, --pin entoure une broche. Le cuivre existant du bloc (cuivre_bloc_hacheur.json) est dessine.
"""
import json, os, sys, math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle, Polygon, FancyBboxPatch

ICI = os.path.dirname(os.path.abspath(__file__))
MIL = 0.0254


def fwd(lx, ly, rot, bot):
    vx, vy = (lx, -ly) if bot else (lx, ly)
    return {0: (vx, vy), 90: (-vy, vx), 180: (-vx, -vy)}.get(rot, (vy, -vx))


def charger(pl_path, rt_path):
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    pose = {c['nom']: tuple(c['pose_actuelle']) for c in D['composants']}
    for e in json.load(open(pl_path, encoding='utf-8')):
        pose[e['designator']] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
    pads = []
    boxes = []
    pr = os.path.join(ICI, 'pastilles_reelles.json')
    PR = json.load(open(pr, encoding='utf-8'))['pieces'] if os.path.exists(pr) else {}
    for c in D['composants']:
        for k, p in enumerate(c['pastilles']):
            if c['nom'] in PR:
                q = PR[c['nom']][k]
                p['hw'], p['hh'], p['forme'] = q['hw'], q['hh'], q['forme']
    for c in D['composants']:
        ox, oy, rot, bot = pose[c['nom']]
        l = c['lbb']
        cs = [fwd(x, y, rot, bot) for x in (l[0], l[2]) for y in (l[1], l[3])]
        boxes.append((c['nom'], bot, ox + min(a for a, b in cs), oy + min(b for a, b in cs), ox + max(a for a, b in cs), oy + max(b for a, b in cs)))
        for p in c['pastilles']:
            dx, dy = fwd(p['lx'], p['ly'], rot, bot)
            hw, hh = (p['hh'], p['hw']) if rot in (90, 270) else (p['hw'], p['hh'])
            pads.append(dict(comp=c['nom'], nom=p['nom'], net=p['reseau'], x=ox + dx, y=oy + dy, hw=hw, hh=hh, th=p['th'], bot=bot,
                             rond=p.get('forme', 'rect') != 'rect'))
    RT = json.load(open(rt_path, encoding='utf-8')) if rt_path and os.path.exists(rt_path) else {}
    cb = os.path.join(ICI, 'cuivre_bloc_hacheur.json')
    CB = json.load(open(cb, encoding='utf-8')) if os.path.exists(cb) and os.environ.get('PLACEUR_BLOC_LIBRE') != '1' else {}
    return D, pads, boxes, RT, CB


def dessiner(ax, D, pads, boxes, RT, CB, fen, couches, nets_hl, pins_hl, zoom):
    W, H = D['contour']['w'], D['contour']['h']
    ax.add_patch(Rectangle((0, 0), W, H, fill=True, fc='#f6f8f2', ec='black', lw=1))
    for f in D['fentes']:
        ax.add_patch(Rectangle((f[0], f[1]), f[2], f[3], fc='#555', ec='black'))
    prl = os.path.join(ICI, 'pastilles_reelles.json')
    libres = json.load(open(prl, encoding='utf-8')).get('libres', []) if os.path.exists(prl) else []
    for q in libres:
        ax.add_patch(Circle((q['x'], q['y']), q['d'] / 2, fc='#a0522d', alpha=0.8))
        ax.add_patch(Circle((q['x'], q['y']), q['trou'] / 2, fc='white'))
    if not libres:
        for t in D['trous_m3']:
            ax.add_patch(Circle(t, 1.6, fc='#333'))
    for nom, bot, x0, y0, x1, y1 in boxes:
        if (1 if bot else 0) in couches:
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec='#2a6fdb' if not bot else '#1a9a4a', lw=0.4, alpha=0.6))
            if zoom and fen[0] <= (x0 + x1) / 2 <= fen[2] and fen[1] <= (y0 + y1) / 2 <= fen[3]:
                ax.text((x0 + x1) / 2, y1, nom, fontsize=6, color='#444', ha='center', va='bottom', clip_on=True)
    col = {0: '#d22', 1: '#22d', 2: '#1a1'}
    # cuivre existant du bloc
    for r in CB.get('regions', []):
        l = 0 if r[0] == 'T' else 1
        if l in couches:
            ax.add_patch(Rectangle((r[2] * MIL, r[3] * MIL), (r[4] - r[2]) * MIL, (r[5] - r[3]) * MIL, fc=col[l], alpha=0.15, ec=col[l], lw=0.3, hatch='//'))
    for p in CB.get('pistes', []):
        l = 0 if p[5] == 'T' else 1
        if l in couches:
            ax.plot([p[0] * MIL, p[2] * MIL], [p[1] * MIL, p[3] * MIL], color=col[l], lw=max(0.5, p[4] * MIL * (8 if zoom else 2)), alpha=0.35, solid_capstyle='round')
    for v in CB.get('vias', []):
        ax.add_patch(Circle((v[0] * MIL, v[1] * MIL), v[2] * MIL / 2, fc='#777', ec='black', lw=0.3))
    for p in pads:
        l = (0, 1, 2) if p['th'] else ((1,) if p['bot'] else (0,))
        if not set(l) & set(couches):
            continue
        hl = p['net'] in nets_hl
        if p['rond']:
            r = min(p['hw'], p['hh'])
            ax.add_patch(FancyBboxPatch((p['x'] - p['hw'] + r, p['y'] - p['hh'] + r), 2 * (p['hw'] - r), 2 * (p['hh'] - r),
                                        boxstyle='round,pad=%f' % r, fc='#d4a017' if not p['th'] else '#a0522d',
                                        ec='black' if hl else 'none', lw=0.8 if hl else 0, alpha=0.9))
        else:
            ax.add_patch(Rectangle((p['x'] - p['hw'], p['y'] - p['hh']), 2 * p['hw'], 2 * p['hh'], fc='#d4a017' if not p['th'] else '#a0522d',
                                   ec='black' if hl else 'none', lw=0.8 if hl else 0, alpha=0.9))
        if zoom and fen[0] <= p['x'] <= fen[2] and fen[1] <= p['y'] <= fen[3]:
            ax.text(p['x'], p['y'], (p['net'] or '-')[:14], fontsize=4.5, ha='center', va='center', color='black', clip_on=True)
    lw_scale = 10.0 if zoom else 2.2
    for key in ('pistes', 'pistes_raccord_bloc'):
        for s in RT.get(key, []):
            l = {'TopLayer': 0, 'BottomLayer': 1, 'MidLayer2': 2}[s['layer']]
            if l not in couches:
                continue
            hl = s['net_name'] in nets_hl
            x = [s['x1'] * MIL, s['x2'] * MIL]
            y = [s['y1'] * MIL, s['y2'] * MIL]
            ax.plot(x, y, color='#ff9900' if hl else col[l], lw=max(0.4, s['width'] * MIL * lw_scale), alpha=0.95 if hl else 0.6, solid_capstyle='round')
            if zoom and fen[0] <= x[0] <= fen[2] and fen[1] <= y[0] <= fen[3] and math.hypot(x[1] - x[0], y[1] - y[0]) > 0.15 * (fen[2] - fen[0]):
                ax.text((x[0] + x[1]) / 2, (y[0] + y[1]) / 2, s['net_name'][:14], fontsize=4, color=col[l], clip_on=True)
    for key in ('vias', 'vias_raccord_bloc'):
        for v in RT.get(key, []):
            ax.add_patch(Circle((v['x'] * MIL, v['y'] * MIL), v['size'] * MIL / 2, fc='#ff9900' if v['net'] in nets_hl else 'black', ec='none'))
    for a in RT.get('points_attente_bloc', []):
        ax.plot(a['x'] * MIL, a['y'] * MIL, 'x', color='purple', ms=5)
    for p in pads:
        if '%s.%s' % (p['comp'], p['nom']) in pins_hl:
            ax.add_patch(Circle((p['x'], p['y']), max(p['hw'], p['hh']) + 0.4, fill=False, ec='magenta', lw=2))
    ax.set_xlim(fen[0], fen[2])
    ax.set_ylim(fen[1], fen[3])
    ax.set_aspect('equal')


def main():
    a = sys.argv[1:]
    pl, rt, out = a[0], a[1], a[2]
    rest = a[3:]
    fen = None
    nets, pins, titre, legende = [], [], '', ''
    i = 0
    nums = []
    while i < len(rest):
        if rest[i] == '--net':
            nets.append(rest[i + 1]); i += 2
        elif rest[i] == '--pin':
            pins.append(rest[i + 1]); i += 2
        elif rest[i] == '--titre':
            titre = rest[i + 1]; i += 2
        elif rest[i] == '--legende':
            legende = rest[i + 1]; i += 2
        else:
            nums.append(float(rest[i])); i += 1
    D, pads, boxes, RT, CB = charger(pl, rt)
    W, H = D['contour']['w'], D['contour']['h']
    if len(nums) == 4:
        fen = nums
        fig, ax = plt.subplots(figsize=(10, 10 * (fen[3] - fen[1]) / (fen[2] - fen[0]) + 1.2), dpi=160)
        dessiner(ax, D, pads, boxes, RT, CB, fen, (0, 1, 2), set(nets), set(pins), True)
        ax.set_title(titre, fontsize=10)
        if legende:
            fig.text(0.02, 0.01, legende, fontsize=8, wrap=True)
        ax.text(0.01, 0.99, 'rouge = dessus (L1), vert = MidLayer2 (L3), bleu = dessous (L4), orange = reseau a relier, magenta = broches, hachures = cuivre existant du bloc',
                transform=ax.transAxes, fontsize=6, va='top')
    else:
        fig, axs = plt.subplots(3, 1, figsize=(16, 30), dpi=130)
        noms = {0: 'DESSUS (TopLayer, L1) ', 2: 'INTERNE (MidLayer2, L3) ', 1: 'DESSOUS (BottomLayer, L4), vu de dessus '}
        for ax, k in zip(axs, (0, 2, 1)):
            dessiner(ax, D, pads, boxes, RT, CB, (0, 0, W, H), (k,), set(nets), set(pins), False)
            ax.set_title(noms[k] + titre, fontsize=12)
    fig.tight_layout()
    fig.savefig(out)
    print('->', out)


if __name__ == '__main__':
    main()
