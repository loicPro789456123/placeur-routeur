# -*- coding: utf-8 -*-
"""Export des donnees d'entree du placeur-routeur Rust vers un JSON simple et documente.

Lancer : python -I export_donnees.py
Sortie : donnees_placeur.json (dans ce dossier).

Le script NE MODIFIE RIEN : il importe placer_v3.py (dossier ../v3) en lecture seule pour reutiliser
exactement son chargement (exports Altium deja captures), sa convention de face dessous (miroir Y,
verifiee a 0,0000 mm), ses regles de face/rotation et ses obstacles de placement.

Format (unites mm, origine = coin bas-gauche de la carte, angles en degres) :
{
  "contour": {"w", "h"}, "marge_bord",
  "composants": [{
      "nom", "empreinte", "hauteur",
      "statut": "fixe" | "bloc" | "mobile",
      "lbb": [x1, y1, x2, y2]            boite locale (repere piece, sans rotation ni miroir),
      "pastilles": [{"nom", "reseau", "lx", "ly", "hw", "hh", "th"}]   geometrie locale (demi-largeurs),
      "pose_actuelle": [x, y, rot, dessous]   pose Altium d'origine,
      "pose_v3": [x, y, rot, dessous]         pose du placement v3 (placements_v3.json),
      "orientations": [[rot, dessous], ...]   (mobiles seulement) orientations permises par le plan v3,
      "regle": texte }],
  "obstacles_placement": [{"face": 0|1, "rect": [x1,y1,x2,y2], "etiquette"}]  obstacles fixes du placement,
  "bloc": {"rects_base": [{"face","rect"}] (a translation nulle), "delta_v3": [dx, dy],
           "emprise_base": [x1,y1,x2,y2], "colonne": [xmin, xmax], "broches_u25": [[x,y],...],
           "m1_centre_base": [x, y], "dist_sw_min": 30},
  "trous_m3": [[x,y],...], "fentes": [[x,y,w,h],...],
  "soft_sw": [noms], "decouplages": [{"comp", "pastille", "reseau", "broches_ci": [[comp, pastille], ...]}],
  "poids_reseaux": {reseau: poids}, "reseaux_plans": ["GND", "+8V"],
  "classes": {reseau: classe}, "classes_source": texte,
  "regles": {...}  regles Altium transmises (isolements, largeurs, vias, gardes)
}
La face 0 = dessus, 1 = dessous. Fonction de pose (identique a placer_v3.fwd) :
  dessous : (lx, ly) -> (lx, -ly) ; puis rotation rot ; monde = origine + vecteur.
"""
import json, os, sys

ICI = os.path.dirname(os.path.abspath(__file__))
V3 = os.path.join(os.path.dirname(ICI), 'v3')
sys.path.insert(0, V3)
import placer_v3 as p  # noqa: E402

MIL = p.MIL


CLASSES_ALTIUM = {   # appartenance confirmee depuis Altium (iteration 2)
    'ENTREE_12V': ['+12V'],
    'PUISSANCE_8V': ['+8V'],
    'CANAL_2A': ['NetR62_ALIM1_1', 'NetR62_ALIM2_1', 'OUT_CH1', 'OUT_CH2', 'NetD16_ALIM1_2', 'NetD16_ALIM2_2'],
    'COMMUTATION': ['NetC19_1'],
    'SENSE': ['Vsense_ALIM1', 'Vsense_ALIM2', 'NetR104_ALIM1_1', 'NetR104_ALIM2_1', 'NetR101_ALIM1_1', 'NetR101_ALIM2_1'],
    'TRIG_50R': ['NetR12_1', 'NetR3_2', 'NetQ3_3'],
    'RAILS': ['+10V', '+6.5Vref', '+5Vref', '+5V', '+2.5Vref', '-5V', '-2.5Vref'],
}


def classes_deduites(noms):
    """Classes confirmees par Altium ; tout le reste = defaut."""
    cl = {}
    for c, lst in CLASSES_ALTIUM.items():
        for n in lst:
            if n in noms:
                cl[n] = c
    return cl


REGLES = {
    "isolement": {"defaut": 0.2, "COMMUTATION": 0.25, "pastilles_pas_fin": 0.124,
                  "pieces_pas_fin": ["M1", "M2", "U10"]},
    "largeurs": {  # min, preferee, max
        "defaut": [0.254, 0.3, 3.0], "ENTREE_12V": [0.3, 2.489, 15.0], "PUISSANCE_8V": [0.3, 2.007, 15.0],
        "CANAL_2A": [0.5, 1.5, 15.0], "SENSE": [0.3, 0.3, 0.5], "TRIG_50R": [0.35, 0.35, 0.4],
        "RAILS": [0.254, 0.508, 5.0], "COMMUTATION": [0.254, 0.3, 3.0]},
    "via": {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.3, "diametre_max": 0.8, "trou_min": 0.3, "trou_max": 0.4,
            "via_sous_cms_permis": ["L1", "U10"]},
    "garde_bord_fentes_trous": 0.6,
    "rayon_trou_m3": 1.7,
}


def main():
    comps, idx, plan = p.load()
    M = p.Model(comps, idx, plan)
    pl = json.load(open(os.path.join(V3, 'placements_v3.json'), encoding='utf-8'))
    v3 = {e['designator']: (e['x'] * MIL, e['y'] * MIL, int(e['rotation']), e['side'] == 'bottom') for e in pl}
    bloc_names = set(M.block_names)
    out_c = []
    for i, c in enumerate(comps):
        n = c['name']
        st = 'bloc' if n in bloc_names else ('fixe' if i in M.fixed else 'mobile')
        if n in v3:
            pv3 = v3[n]
        else:  # fixes inchanges : pas dans placements_v3.json
            pv3 = (c['ox'], c['oy'], c['rot'], c['bottom'])
        d = dict(nom=n, empreinte=c['fp'], hauteur=c['h'], statut=st, lbb=list(c['lbb']),
                 pastilles=[dict(nom=q['name'], reseau=q['net'], lx=q['lx'], ly=q['ly'], hw=q['hw'], hh=q['hh'], th=bool(q['th']))
                            for q in c['pads']],
                 pose_actuelle=[c['ox'], c['oy'], c['rot'], c['bottom']],
                 pose_v3=[pv3[0], pv3[1], pv3[2], pv3[3]])
        if st == 'mobile':
            d['orientations'] = [[r, b] for r, b in M.allowed[i]]
            d['regle'] = M.rules[i]
        out_c.append(d)
    # bloc
    dxs = [v3[n][0] - comps[idx[n]]['ox'] for n in M.block_names]
    dys = [v3[n][1] - comps[idx[n]]['oy'] for n in M.block_names]
    assert max(dxs) - min(dxs) < 1e-3 and max(dys) - min(dys) < 1e-3
    bdx, bdy = round(dxs[0] / MIL) * MIL, round(dys[0] / MIL) * MIL   # translation exacte (-100 ; -240 mil)
    for n in M.block_names:
        c = comps[idx[n]]
        out_c[idx[n]]['pose_v3'][0] = c['ox'] + bdx
        out_c[idx[n]]['pose_v3'][1] = c['oy'] + bdy
    P = p.Placer(M, 0)
    b = comps[M.M1]['wbb']
    bloc = dict(rects_base=[dict(face=f, rect=list(r)) for f, r in M.block_rects(0.0, 0.0)],
                delta_v3=[bdx, bdy], emprise_base=list(M.emp0), colonne=list(P.col),
                broches_u25=[list(q) for q in M.u25_pins()],
                m1_centre_base=[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2], dist_sw_min=p.SW_HARD)
    obs = [dict(face=f, rect=list(r), etiquette=lab) for f, r, lab in M.static_obstacles()]
    decs = []
    for N in M.nets:
        for (i, k) in N['decs']:
            decs.append(dict(comp=comps[i]['name'], pastille=k, reseau=N['name'],
                             broches_ci=[[comps[a]['name'], kk] for a, kk in N['ics']]))
    poids = {N['name']: N['w'] for N in M.nets}
    poids['+8V'] = 3   # iteration 3 : +8V n'a plus de plan (L3 = couche de routage), reseau de puissance route
    cl = classes_deduites(M.net_names)
    data = dict(contour=plan['contour'], marge_bord=plan['marge_bord'], composants=out_c,
                obstacles_placement=obs, bloc=bloc, trous_m3=plan['trous_M3'], fentes=plan['fentes_radiateurs'],
                soft_sw=[comps[i]['name'] for i in M.soft_sw], decouplages=decs, poids_reseaux=poids,
                reseaux_plans=['GND'], classes=cl,
                classes_source="confirmees depuis Altium (iteration 2) ; le reste = defaut",
                regles=REGLES, source=dict(composants=p.F_COMP, pastilles=p.F_PADS, plan=p.F_PLAN,
                                           v3=os.path.join(V3, 'placements_v3.json')))
    json.dump(data, open(os.path.join(ICI, 'donnees_placeur.json'), 'w', encoding='utf-8'), indent=1)
    print('composants', len(out_c), 'mobiles', sum(1 for c in out_c if c['statut'] == 'mobile'),
          'bloc', len(bloc_names), 'reseaux', len(M.net_names), 'classes', len(cl), 'delta bloc v3', bloc['delta_v3'])


if __name__ == '__main__':
    main()
