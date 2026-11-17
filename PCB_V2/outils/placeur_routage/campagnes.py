# -*- coding: utf-8 -*-
"""Multi-departs : campagnes independantes depuis zero + critere de confiance (iteration 7).

  python campagnes.py [--workers 5] [--threads 2] [--mouvements 100000000] [--budget-s 600] [--boucle 4]
                      [--eps 0.01] [--confiance 0.99] [--k 5] [--n-min 12] [--n-max 300] [--graine-base 1000] [--reprendre]
                      [--dossier campagnes]   (sous-dossier de sorties/)   [--bloc-fige]
--bloc-fige : bloc hacheur fige avec son cuivre manuel (sans --bloc-libre) ; controles sans PLACEUR_BLOC_LIBRE.

BLOC HACHEUR LIBERE : chaque campagne passe --bloc-libre (pieces du bloc mobiles, cuivre manuel
ignore, tout est route) et ses controles tournent avec PLACEUR_BLOC_LIBRE=1 ; rev3 / rev4 (bloc manuel) sont evalues
sans cette variable. Les longueurs / vias des campagnes comprennent donc le routage du bloc, pas celles de rev3 / rev4
(cuivre manuel hors compte) : la comparaison des couts est defavorable aux campagnes de ce montant.

Chaque campagne = un processus `placeur_routage --campagne --graine G --threads T` (placement aleatoire legal, recuit
global a paliers, routage detaille, phase conjointe, finition), dans sorties/campagnes/c_<G>/. A la fin de chaque
campagne : controles INDEPENDANTS (verifier_placement.py, drc_maison.py strict et --altium, vias_couture.py,
analyse_8v.py), cout commun, ligne dans campagnes.csv, mise a jour du meilleur etat valide dans meilleur/ (ecrit
au fil de l'eau : un arret ne perd rien), mise a jour de confiance.md. Arret quand la confiance empirique atteint le
seuil ET que le meilleur n'a pas bouge pendant les K dernieres campagnes valides (et au moins n-min campagnes),
ou n-max campagnes. --reprendre : repart du CSV existant (campagnes deja faites sautees).

Cout commun (le meme pour rev3, rev4 et toutes les campagnes, calcule ici depuis les JSON) :
  C = longueur (mm) + 1,5 x vias + 2 x longueur retrecie (mm) + 3 x resistance des pistes de puissance (mOhm)
      + 0,1 x coudes + 1000 x max(0 ; 0,6 - ecart moyen au cuivre d'un autre reseau (mm))
"""
import csv, json, math, os, shutil, subprocess, sys, time

ICI = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(ICI, 'target', 'release', 'placeur_routage' + ('.exe' if os.name == 'nt' else ''))
RACINE = os.path.join(ICI, 'sorties', sys.argv[sys.argv.index('--dossier') + 1] if '--dossier' in sys.argv else 'campagnes')
sys.path.insert(0, ICI)
import comparer_conjoint as cc  # noqa: E402

FIGE = '--bloc-fige' in sys.argv

CHAMPS = ['source', 'graine', 'valide', 'cout', 'longueur_mm', 'vias', 'retreci_mm', 'chute_8v_c1_mv', 'chute_8v_c2_mv',
          'ecart_moyen_mm', 'ecart_min_mm', 'coudes', 'resistance_puissance_mohm', 'ouvertes_strict', 'ouvertes_altium',
          'violations', 'verif_placement', 'vias_couture', 'boucle_chaude_mm2', 'boucle_chaude_perimetre_mm', 'sw_longueur_mm', 'sw_surface_mm2',
          'courts_demi_perimetre_mm', 'compensation_sw_min_mm', 'duree_s', 'cause']


def arg(nom, defaut, typ=float):
    if nom in sys.argv:
        return typ(sys.argv[sys.argv.index(nom) + 1])
    return defaut


def journal(s):
    print(s, flush=True)
    with open(os.path.join(RACINE, 'journal.txt'), 'a', encoding='utf-8') as f:
        f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + s + '\n')


def cout_commun(m):
    return (m['longueur'] + 1.5 * m['vias'] + 2.0 * m['retreci'] + 3.0 * m['r_puiss'] + 0.1 * m['coudes']
            + 1000.0 * max(0.0, 0.6 - m['ecart_moy']))


def evaluer(pl, rt, dossier, tag, libre=True):
    """Controles independants + mesures d'un etat (placement + routage). libre : bloc hacheur libere."""
    if libre:
        os.environ['PLACEUR_BLOC_LIBRE'] = '1'
    else:
        os.environ.pop('PLACEUR_BLOC_LIBRE', None)
    m = cc.mesures(pl, rt, dossier, tag)
    h = subprocess.run([sys.executable, '-I', os.path.join(ICI, 'mesure_hacheur.py'), pl, rt] + ([] if libre else ['--bloc-manuel']),
                       capture_output=True, text=True)
    try:
        m['hacheur'] = json.loads(h.stdout.strip().splitlines()[-1])
    except Exception:  # noqa
        m['hacheur'] = {}
    drc = json.load(open(os.path.join(dossier, 'drc_%s_strict.json' % tag), encoding='utf-8'))
    m['r_puiss'] = drc['resistance_puissance_mohm']
    v = subprocess.run([sys.executable, '-I', os.path.join(ICI, 'verifier_placement.py'), pl], capture_output=True, text=True)
    m['verif'] = v.returncode
    vc = os.path.join(dossier, 'vias_couture.json')
    subprocess.run([sys.executable, '-I', os.path.join(ICI, 'vias_couture.py'), pl, rt, vc], check=True, stdout=subprocess.DEVNULL)
    m['vias_couture'] = len(json.load(open(vc, encoding='utf-8'))['vias'])
    m['valide'] = (m['verif'] == 0 and m['violations'] == 0 and m['ouvertes_s'] == 0 and m['ouvertes_a'] == 0)
    m['cout'] = cout_commun(m)
    return m


def ligne(source, graine, m, duree, cause=''):
    if m is None:
        return dict(source=source, graine=graine, valide=0, cout='', duree_s='%.0f' % duree, cause=cause)
    return dict(source=source, graine=graine, valide=int(m['valide']), cout='%.2f' % m['cout'], longueur_mm='%.1f' % m['longueur'],
                vias=m['vias'], retreci_mm='%.1f' % m['retreci'], chute_8v_c1_mv='%.1f' % (m['chute1'] or 0), chute_8v_c2_mv='%.1f' % (m['chute2'] or 0),
                ecart_moyen_mm='%.3f' % m['ecart_moy'], ecart_min_mm='%.3f' % m['ecart_min'], coudes=m['coudes'],
                resistance_puissance_mohm='%.1f' % m['r_puiss'], ouvertes_strict=m['ouvertes_s'], ouvertes_altium=m['ouvertes_a'],
                violations=m['violations'], verif_placement=m['verif'], vias_couture=m['vias_couture'], duree_s='%.0f' % duree, cause=cause,
                **{k: ('%.2f' % m['hacheur'][k] if m['hacheur'].get(k) is not None else '') for k in
                   ('boucle_chaude_mm2', 'boucle_chaude_perimetre_mm', 'sw_longueur_mm', 'sw_surface_mm2', 'courts_demi_perimetre_mm', 'compensation_sw_min_mm')})


def lire_csv(f):
    if not os.path.exists(f):
        return []
    return list(csv.DictReader(open(f, encoding='utf-8')))


def ajouter_csv(f, row):
    neuf = not os.path.exists(f)
    with open(f, 'a', newline='', encoding='utf-8') as h:
        w = csv.DictWriter(h, fieldnames=CHAMPS)
        if neuf:
            w.writeheader()
        w.writerow({k: row.get(k, '') for k in CHAMPS})


def confiance(rows, eps, k):
    camp = [r for r in rows if r['source'] == 'campagne']
    val = [r for r in camp if r['valide'] == '1']
    cs = [float(r['cout']) for r in val]
    res = dict(n=len(camp), n_valides=len(val), n_invalides=len(camp) - len(val))
    if not cs:
        return res
    best = min(cs)
    p = sum(1 for c in cs if c <= best * (1 + eps)) / len(cs)
    res.update(best=best, p=p, conf=1 - (1 - p) ** len(cs))
    # le meilleur a-t-il bouge dans les k dernieres campagnes valides ?
    hist = []
    b = float('inf')
    for c in cs:
        b = min(b, c)
        hist.append(b)
    res['stable_k'] = len(hist) > k and hist[-1] == hist[-1 - k]
    # valeurs extremes : Weibull a 3 parametres sur les couts (minima), borne inferieure = parametre de position
    if len(cs) >= 8:
        try:
            import numpy as np
            from scipy import stats, optimize
            x = np.array(cs)
            sd = float(np.std(x)) or 1.0

            def nll(th):
                c, loc, sc = th
                if c <= 0 or sc <= 0 or loc >= x.min() - 1e-9:
                    return 1e18
                return -np.sum(stats.weibull_min.logpdf(x, c, loc=loc, scale=sc))
            meilleur = None
            for loc0 in (x.min() - 0.1 * sd, x.min() - sd, x.min() - 3 * sd):
                r = optimize.minimize(nll, [2.0, loc0, max(1e-6, float(np.mean(x)) - loc0)], method='Nelder-Mead', options=dict(maxiter=4000, xatol=1e-6, fatol=1e-9))
                if meilleur is None or r.fun < meilleur.fun:
                    meilleur = r
            c, loc, sc = meilleur.x
            res.update(weibull=dict(forme=float(c), position=float(loc), echelle=float(sc), ecart_restant=best - float(loc),
                                    ecart_restant_pct=100 * (best - float(loc)) / best,
                                    p_sous_meilleur=float(stats.weibull_min.cdf(best, c, loc=loc, scale=sc)),
                                    p_sous_eps=float(stats.weibull_min.cdf(best * (1 + eps), c, loc=loc, scale=sc))))
        except Exception as ex:  # noqa
            res['weibull_erreur'] = str(ex)
    return res


def ecrire_rapport(rows, refs, conf, eps, seuil, k, nmin):
    L = ['# Confiance des campagnes depuis zero (iteration 7)', '',
         'Campagnes independantes (graines differentes), chacune : placement aleatoire legal -> recuit global a paliers -> '
         'routage detaille complet -> phase conjointe (cout sur le routage detaille, espacement compris) -> finition. '
         'Toutes sont jugees par les memes controles independants et le meme cout commun (voir campagnes.py).', '',
         '| Grandeur | Valeur |', '|---|---:|',
         '| Campagnes terminees | %d |' % conf['n'], '| dont valides (0 liaison ouverte, 0 violation, placement 0) | %d |' % conf['n_valides'],
         '| dont invalides | %d |' % conf['n_invalides']]
    if 'best' in conf:
        L += ['| Meilleur cout (campagnes) | %.1f |' % conf['best'],
              '| p = fraction des campagnes a moins de %.0f %% du meilleur | %.3f |' % (100 * eps, conf['p']),
              '| Confiance empirique 1 - (1 - p)^N | %.4f |' % conf['conf'],
              '| Meilleur inchange depuis %d campagnes valides | %s |' % (k, 'oui' if conf.get('stable_k') else 'non')]
        w = conf.get('weibull')
        if w:
            L += ['| Weibull (minima) : borne inferieure estimee | %.1f |' % w['position'],
                  '| Ecart restant estime meilleur - borne | %.1f (%.2f %%) |' % (w['ecart_restant'], w['ecart_restant_pct']),
                  '| P(une campagne < meilleur) selon l\'ajustement | %.3f |' % w['p_sous_meilleur'],
                  '| Forme / echelle | %.2f / %.1f |' % (w['forme'], w['echelle'])]
    L += ['', '## References (meme cout, memes controles)', '', '| Etat | Cout | Longueur (mm) | Vias | Retreci (mm) | Chute +8V c1 / c2 (mV) | Ecart moyen (mm) | Valide |', '|---|---:|---:|---:|---:|---:|---:|---|']
    tous = [r for r in rows if r['valide'] == '1']
    best_c = min(tous, key=lambda r: float(r['cout'])) if tous else None
    for r in [x for x in rows if x['source'] != 'campagne'] + ([best_c] if best_c and best_c['source'] == 'campagne' else []):
        nom = r['source'] if r['source'] != 'campagne' else 'meilleure campagne (graine %s)' % r['graine']
        L.append('| %s | %s | %s | %s | %s | %s / %s | %s | %s |' % (nom, r['cout'], r.get('longueur_mm', ''), r.get('vias', ''), r.get('retreci_mm', ''),
                                                               r.get('chute_8v_c1_mv', ''), r.get('chute_8v_c2_mv', ''), r.get('ecart_moyen_mm', ''), 'oui' if r['valide'] == '1' else 'non'))
    camp = [r for r in rows if r['source'] == 'campagne' and r['valide'] == '1']
    refv = [r for r in rows if r['source'] != 'campagne' and r['valide'] == '1']
    if camp and refv:
        bc = min(float(r['cout']) for r in camp)
        br = min(refv, key=lambda r: float(r['cout']))
        if bc <= float(br['cout']):
            L.append('\nLes departs de zero atteignent ou depassent la meilleure reference (%s, %.1f) : meilleur depart de zero %.1f.' % (br['source'], float(br['cout']), bc))
        else:
            L.append('\n**Les departs de zero n\'atteignent pas la meilleure reference** (%s, %.1f) : meilleur depart de zero %.1f (%.1f %% plus cher). La reference reste candidate.' % (
                br['source'], float(br['cout']), bc, 100 * (bc - float(br['cout'])) / float(br['cout'])))
    L += ['', '## Convertisseur (bloc hacheur) : manuel (rev3 / rev4) contre automatique (campagnes, bloc libere)', '',
          '| Etat | Boucle chaude (mm2 / perimetre mm, indicateur de placement) | Noeud de commutation : longueur pistes (mm) | surface cuivre (mm2) | Reseaux courts (demi-perimetres, mm) | Compensation -> noeud SW min (mm) |',
          '|---|---:|---:|---:|---:|---:|']
    for r in [x for x in rows if x['source'] != 'campagne'] + ([best_c] if best_c and best_c['source'] == 'campagne' else []):
        nom = r['source'] if r['source'] != 'campagne' else 'meilleure campagne (graine %s)' % r['graine']
        L.append('| %s | %s / %s | %s | %s | %s | %s |' % (nom, r.get('boucle_chaude_mm2', ''), r.get('boucle_chaude_perimetre_mm', ''), r.get('sw_longueur_mm', ''), r.get('sw_surface_mm2', ''),
                                                  r.get('courts_demi_perimetre_mm', ''), r.get('compensation_sw_min_mm', '')))
    L += ['', 'Bloc manuel : surfaces de cuivre comptees par leurs rectangles englobants (surestimation) ; bloc libere : '
          'le placeur ne pose pas de surfaces/polygones, le noeud de commutation est une piste (au plus 2 mm de large).']
    L += ['', '## Interpretation (a lire)', '',
          '- La confiance 1 - (1 - p)^N est la probabilite, estimee sur l\'echantillon, qu\'au moins une des N campagnes atteigne '
          'le niveau "a moins de %.0f %% du meilleur cout observe". C\'est le meilleur atteignable PAR CET ALGORITHME ET CE COUT, '
          'pas une preuve d\'optimum global : p est estime sur les memes campagnes (biais optimiste quand N est petit), '
          'et une region de l\'espace jamais visitee par l\'algorithme ne peut pas etre vue par ce critere.' % (100 * eps),
          '- La garantie theorique du recuit (refroidissement logarithmique, temps infini) est inatteignable en pratique.',
          '- L\'ajustement de Weibull (valeurs extremes, 3 parametres) extrapole une borne inferieure de la distribution des couts '
          'de CET algorithme ; elle depend de la forme supposee et devient fragile avec peu de campagnes valides.',
          '- Critere d\'arret : confiance >= %.2f, meilleur inchange depuis %d campagnes valides, au moins %d campagnes.' % (seuil, k, nmin)]
    open(os.path.join(RACINE, 'confiance.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')


def copier_meilleur(src_pl, src_rt, src_vc, info):
    dst = os.path.join(RACINE, 'meilleur')
    os.makedirs(dst, exist_ok=True)
    for a, b in ((src_pl, 'placements_routage.json'), (src_rt, 'routage_detaille.json'), (src_vc, 'vias_couture.json')):
        shutil.copyfile(a, os.path.join(dst, b))
    json.dump(info, open(os.path.join(dst, 'origine.json'), 'w', encoding='utf-8'), indent=1)
    subprocess.run([sys.executable, os.path.join(ICI, 'visualiser.py'), os.path.join(dst, 'placements_routage.json'), os.path.join(dst, 'routage_detaille.json'),
                    os.path.join(dst, 'routage_final_3couches.png'), '--titre', '- meilleur etat (%s)' % info.get('source')],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    workers = int(arg('--workers', 5))
    threads = int(arg('--threads', 2))
    boucle = int(arg('--boucle', 4))
    mouv = int(arg('--mouvements', 100000000))
    budget = arg('--budget-s', 600)
    eps = arg('--eps', 0.01)
    seuil = arg('--confiance', 0.99)
    k = int(arg('--k', 5))
    nmin = int(arg('--n-min', 12))
    nmax = int(arg('--n-max', 300))
    base = int(arg('--graine-base', 1000))
    os.makedirs(RACINE, exist_ok=True)
    fcsv = os.path.join(RACINE, 'campagnes.csv')
    rows = lire_csv(fcsv)
    if rows and '--reprendre' not in sys.argv:
        sys.exit('%s existe deja : relancer avec --reprendre (ou deplacer le dossier)' % fcsv)
    # references : rev3 (sorties) et rev4 (sorties/conjoint), memes controles et meme cout
    for src, d in (('rev3 (sorties)', os.path.join(ICI, 'sorties')), ('rev4 (sorties/conjoint)', os.path.join(ICI, 'sorties', 'conjoint'))):
        if any(r['source'] == src for r in rows):
            continue
        dd = os.path.join(RACINE, 'ref_' + src.split()[0])
        os.makedirs(dd, exist_ok=True)
        t0 = time.time()
        m = evaluer(os.path.join(d, 'placements_routage.json'), os.path.join(d, 'routage_detaille.json'), dd, 'ref', libre=False)
        r = ligne(src, '', m, time.time() - t0)
        ajouter_csv(fcsv, r)
        rows.append({k: str(v) for k, v in r.items()})
        journal('reference %s : cout %.1f, valide %s' % (src, m['cout'], m['valide']))
    faites = set(r['graine'] for r in rows if r['source'] == 'campagne')
    graines = (g for g in range(base, base + 100000) if str(g) not in faites)
    actifs = {}

    def meilleur_actuel():
        v = [r for r in rows if r['valide'] == '1']
        return min(v, key=lambda r: float(r['cout'])) if v else None

    def lancer():
        g = next(graines)
        dc = os.path.join(RACINE, 'c_%d' % g)
        os.makedirs(dc, exist_ok=True)
        log = open(os.path.join(dc, 'console.txt'), 'w', encoding='utf-8')
        p = subprocess.Popen([EXE, '--campagne'] + ([] if FIGE else ['--bloc-libre']) + ['--graine', str(g), '--threads', str(threads), '--chaines', str(threads), '--boucle', str(boucle),
                              '--sortie', dc, '--mouvements', str(mouv), '--budget-s', str(budget), '--iters-det', '40'],
                             cwd=ICI, stdout=log, stderr=subprocess.STDOUT)
        actifs[g] = (p, time.time(), dc, log)
    arret = False
    best = meilleur_actuel()
    if best:
        info = dict(source=best['source'], graine=best['graine'], cout=best['cout'])
        if not os.path.exists(os.path.join(RACINE, 'meilleur', 'origine.json')):
            d = os.path.join(ICI, 'sorties') if best['source'].startswith('rev3') else (os.path.join(ICI, 'sorties', 'conjoint') if best['source'].startswith('rev4') else os.path.join(RACINE, 'c_%s' % best['graine']))
            vc = os.path.join(RACINE, 'ref_' + best['source'].split()[0], 'vias_couture.json') if best['source'] != 'campagne' else os.path.join(d, 'vias_couture.json')
            copier_meilleur(os.path.join(d, 'placements_routage.json'), os.path.join(d, 'routage_detaille.json'), vc, info)
    while True:
        n_camp = sum(1 for r in rows if r['source'] == 'campagne')
        while not arret and len(actifs) < workers and n_camp + len(actifs) < nmax:
            lancer()
        if not actifs:
            break
        time.sleep(10)
        for g, (p, t0, dc, log) in list(actifs.items()):
            if p.poll() is None:
                continue
            log.close()
            del actifs[g]
            dur = time.time() - t0
            st = os.path.join(dc, 'stats.json')
            stats = json.load(open(st, encoding='utf-8')) if os.path.exists(st) else dict(valide=False, cause='processus termine sans stats (code %s)' % p.returncode)
            if not stats.get('valide'):
                r = ligne('campagne', g, None, dur, '%s (%s connexions en echec)' % (stats.get('cause', 'invalide'), stats.get('connexions_echouees', '?')))
            else:
                try:
                    m = evaluer(os.path.join(dc, 'placements_routage.json'), os.path.join(dc, 'routage_detaille.json'), dc, 'camp', libre=not FIGE)
                    r = ligne('campagne', g, m, dur, '' if m['valide'] else 'controle independant non nul')
                except Exception as ex:
                    r = ligne('campagne', g, None, dur, 'evaluation : %s' % ex)
            ajouter_csv(fcsv, r)
            rows.append({k: str(v) for k, v in r.items()})
            journal('campagne %d : valide %s cout %s (%.0f s) %s' % (g, r['valide'], r['cout'], dur, r['cause']))
            b = meilleur_actuel()
            if b and b['source'] == 'campagne' and b['graine'] == str(g):
                copier_meilleur(os.path.join(dc, 'placements_routage.json'), os.path.join(dc, 'routage_detaille.json'), os.path.join(dc, 'vias_couture.json'),
                                dict(source='campagne', graine=g, cout=r['cout']))
                journal('  nouveau meilleur etat valide : campagne %d, cout %s' % (g, r['cout']))
            conf = confiance(rows, eps, k)
            ecrire_rapport(rows, None, conf, eps, seuil, k, nmin)
            if conf.get('n_valides', 0) >= nmin and conf.get('conf', 0) >= seuil and conf.get('stable_k'):
                if not arret:
                    journal('critere atteint (confiance %.4f, meilleur stable depuis %d campagnes) : fin des lancements' % (conf['conf'], k))
                arret = True
    journal('termine : %s' % json.dumps(confiance(rows, eps, k)))


if __name__ == '__main__':
    main()
