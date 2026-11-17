# -*- coding: utf-8 -*-
"""Mesures electriques du convertisseur (bloc hacheur) d'un etat place + route (iteration 7 bis).

  python -I mesure_hacheur.py <placements_routage.json> <routage_detaille.json> [--bloc-manuel]

--bloc-manuel : ajoute le cuivre manuel du bloc (cuivre_bloc_hacheur.json, etats rev3 / rev4 a bloc fige).
Sortie JSON sur stdout :
  boucle_chaude_mm2 : surface du polygone condensateur d'entree (+12V) -> M1 (+12V) -> M1 (commutation) ->
      M2 (commutation) -> M2 (GND) -> condensateur d'entree (GND), centres des pastilles par reseau, minimum sur C7 / C15
      (meme definition que le cout du placeur ; indicateur de placement, le trajet reel du cuivre n'est pas suivi) ;
      boucle_chaude_perimetre_mm : perimetre du meme polygone (une boucle aplatie mais longue a une petite surface).
  sw_longueur_mm, sw_surface_mm2 : longueur des pistes du noeud de commutation et surface de cuivre
      (pistes longueur x largeur + surfaces du bloc manuel ; pastilles non comptees).
  sw_largeur_min_mm : piste la plus etroite du noeud.
  courts_demi_perimetre_mm : somme des demi-perimetres des reseaux a garder courts (bootstrap, grilles, decouplages
      du pilote ; liste dans config_carte.json).
  compensation_sw_min_mm : plus petite distance d'une pastille du reseau de compensation (hors U10) au noeud de
      commutation (ses pastilles et le centre de l'inductance).
"""
import json, math, os, sys

ICI = os.path.dirname(os.path.abspath(__file__))
MIL = 0.0254
sys.path.insert(0, ICI)
import drc_maison as dm  # noqa: E402

CFG = json.load(open(os.environ.get('CONFIG_CARTE', os.path.join(ICI, 'config_carte.json')), encoding='utf-8'))
CV = CFG['convertisseurs'][0]   # roles des pieces et reseaux : configuration de carte, rien en dur
COMP = tuple(CV.get('reseaux_sensibles', []))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    D = json.load(open(os.path.join(ICI, 'donnees_placeur.json'), encoding='utf-8'))
    pose = {c['nom']: tuple(c['pose_actuelle']) for c in D['composants']}
    for e in json.load(open(args[0], encoding='utf-8')):
        pose[e['designator']] = (e['x'] * MIL, e['y'] * MIL, int(e['rotation']) % 360, e['side'] == 'bottom')
    RT = json.load(open(args[1], encoding='utf-8'))
    pads, _ = dm.charger_pastilles(D, pose)

    def centre(comp, net):
        pts = [(p['f'].x, p['f'].y) for p in pads if p['comp'] == comp and p['net'] == net]
        if not pts:
            return None
        return (sum(x for x, y in pts) / len(pts), sum(y for x, y in pts) / len(pts))

    def demi_perimetre(net):
        pts = [(p['f'].x, p['f'].y) for p in pads if p['net'] == net]
        if not pts:
            return 0.0
        return max(x for x, y in pts) - min(x for x, y in pts) + max(y for x, y in pts) - min(y for x, y in pts)

    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1]) if a and b else None
    H, B, E, G = CV['haut'], CV['bas'], CV['reseau_entree'], CV['reseau_masse']
    SW = CV.get('reseau_commutation') or next((n for n in set(p['net'] for p in pads if p['comp'] == H) if D['classes'].get(n) == 'COMMUTATION'), None)
    a, b = centre(H, E), centre(H, SW)
    c, d = centre(B, SW), centre(B, G)
    best = None
    for cap in CV.get('cond_entree', []):
        p, g = centre(cap, E), centre(cap, G)
        if None in (p, g, a, b, c, d):
            continue
        poly = [p, a, b, c, d, g]
        s = sum(poly[k][0] * poly[(k + 1) % 6][1] - poly[(k + 1) % 6][0] * poly[k][1] for k in range(6))
        ar = abs(s) / 2
        per = sum(math.hypot(poly[(k + 1) % 6][0] - poly[k][0], poly[(k + 1) % 6][1] - poly[k][1]) for k in range(6))
        if best is None or ar + per < best[0] + best[1]:
            best = (ar, per)
    segs = [((s['x1'] * MIL, s['y1'] * MIL), (s['x2'] * MIL, s['y2'] * MIL), s['width'] * MIL) for s in RT.get('pistes', []) if s['net_name'] == SW]
    surf_reg = 0.0
    if '--bloc-manuel' in sys.argv:
        CB = json.load(open(os.path.join(ICI, 'cuivre_bloc_hacheur.json'), encoding='utf-8'))
        segs += [((p[0] * MIL, p[1] * MIL), (p[2] * MIL, p[3] * MIL), p[4] * MIL) for p in CB['pistes'] if p[6] == SW]
        surf_reg = sum((r[4] - r[2]) * (r[5] - r[3]) * MIL * MIL for r in CB['regions'] if r[1] == SW)
    lg = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q, w in segs)
    surf = sum(math.hypot(q[0] - p[0], q[1] - p[1]) * w for p, q, w in segs) + surf_reg
    sw_pts = [(p['f'].x, p['f'].y) for p in pads if p['net'] == SW]
    l1 = [(p['f'].x, p['f'].y) for p in pads if p['comp'] == CV.get('inductance')]
    if l1:
        sw_pts.append((sum(x for x, y in l1) / len(l1), sum(y for x, y in l1) / len(l1)))
    comp_min = min((math.hypot(p['f'].x - x, p['f'].y - y) for p in pads if p['net'] in COMP and p['comp'] != CV.get('pilote') for x, y in sw_pts), default=None)
    out = dict(boucle_chaude_mm2=best[0] if best else None, boucle_chaude_perimetre_mm=best[1] if best else None, sw_longueur_mm=lg, sw_surface_mm2=surf, sw_surfaces_bloc_mm2=surf_reg,
               sw_largeur_min_mm=min((w for p, q, w in segs), default=None),
               courts_demi_perimetre_mm=sum(demi_perimetre(n) for n in CV.get('reseaux_courts', [])),
               compensation_sw_min_mm=comp_min)
    print(json.dumps(out))


if __name__ == '__main__':
    main()
