//! Optimisation conjointe placement + routage detaille (iteration 6).
//!
//! Etat = placement + routage detaille complet et valide (0 connexion en echec, aucun recouvrement).
//! Mouvement local : deplacer, tourner (90 degres), changer de face (si permis), echanger deux pieces de meme
//! empreinte. Les pieces sont tirees de preference dans les zones chargees (troncons retrecis, vias, detours).
//! Apres le mouvement, seuls les reseaux de la (des) piece(s) deplacee(s) et ceux dont le cuivre gene les nouvelles
//! pastilles sont arraches et reroutes (mode strict + arrachage cible limite). Un mouvement qui laisse une connexion
//! non routee est refuse et annule exactement : un etat invalide n'est jamais accepte ni retenu.
//! Cout (mesure sur le routage detaille reel) = longueur + 1,5 x vias + 2 x longueur retrecie
//! + 3 x resistance des reseaux de puissance (+8V, +12V, CANAL_2A, mOhm) + 0,1 x segments (coudes).
//! Recuit (temperature geometrique) ; plusieurs chaines independantes en parallele (clones de l'etat).
use crate::detail::{Detail, NetR};
use crate::geom::*;
use std::time::Instant;

#[derive(Clone)]
pub struct ParamConj {
    pub budget_s: f64,
    pub t0: f64,
    pub t_fin: f64,
    pub sigma: f64,
    /// poids du terme d'espacement (proximite des cuivres d'autres reseaux), 0 = ignore
    pub prox_poids: f64,
    /// tous les combien d'essais un reroutage complet d'une zone (20 x 20 mm) est tente (0 = jamais)
    pub zone_periode: usize,
}

pub struct ResConj {
    pub cout_depart: f64,
    pub cout: f64,
    pub etat: Etat,
    pub nets: Vec<NetR>,
    pub essais: usize,
    pub illegaux: usize,
    pub refuses_routage: usize,
    pub acceptes: usize,
    pub graine: u64,
}

pub fn cout_de(m: [f64; 6]) -> f64 {
    m[0] + 1.5 * m[1] + 2.0 * m[2] + 3.0 * 0.5 * m[3] + 0.1 * m[4] + 1e5 * m[5]
}

fn signature(m: &Modele, i: usize) -> Vec<i64> {
    let c = &m.comps[i];
    let mut v: Vec<i64> = c.lbb.iter().map(|x| (x * 100.0).round() as i64).collect();
    for p in &c.pads {
        v.push((p.hw * 1000.0).round() as i64);
        v.push((p.hh * 1000.0).round() as i64);
        v.push((p.lx * 1000.0).round() as i64);
        v.push((p.ly * 1000.0).round() as i64);
    }
    v
}

pub fn chaine(base: &Detail, graine: u64, p: &ParamConj, journal: &(dyn Fn(String) + Sync)) -> ResConj {
    let t_start = Instant::now();
    let m = base.m;
    let mut d = base.clone();
    // essais rapides : une recherche qui ne trouve pas vite est un mouvement refuse
    d.rapide = true;
    d.max_exp = 200_000;
    if p.prox_poids > 0.0 {
        d.activer_prox();
    }
    let mut occ = Occup::new(m, &d.e);
    let mut rng = Rng::new(graine);
    let nn = m.nets.len();
    let mut met: Vec<[f64; 6]> = (0..nn).map(|n| d.metrique(n)).collect();
    let mut prox: Vec<f64> = (0..nn).map(|n| d.prox_net(n)).collect();
    let total = |met: &Vec<[f64; 6]>, prox: &Vec<f64>| -> f64 { met.iter().zip(prox.iter()).map(|(x, q)| cout_de(*x) + p.prox_poids * q).sum() };
    let mut cout = total(&met, &prox);
    let mut zones_tentees = 0usize;
    let mut zones_acceptees = 0usize;
    let cout_depart = cout;
    let mut best = (cout, d.e.clone(), d.nets.clone());
    let sigs: Vec<Vec<i64>> = (0..m.comps.len()).map(|i| signature(m, i)).collect();
    let (mut essais, mut ill, mut refus, mut acc) = (0usize, 0usize, 0usize, 0usize);
    // pieces portant une amorce de sortie (convertisseur libere) : figees dans cette phase
    let fig = d.pieces_amorcees();
    // pieces de puissance des convertisseurs (configuration de carte) : figees aussi, le cout de cette phase ne voit pas
    // la boucle chaude (elle a ete optimisee par le recuit)
    let mobiles: Vec<usize> = m.mobiles.iter().copied().filter(|i| !fig.contains(i) && !m.hacheur_puissance.contains(i)).collect();
    let mut dernier_journal = 0.0;
    loop {
        let el = t_start.elapsed().as_secs_f64();
        if el > p.budget_s {
            break;
        }
        let frac = el / p.budget_s;
        let temp = p.t0 * (p.t_fin / p.t0).powf(frac);
        // resynchronisation periodique (la proximite d'un reseau depend de ses voisins)
        if essais % 300 == 299 {
            for n in 0..nn {
                met[n] = d.metrique(n);
                prox[n] = d.prox_net(n);
            }
            cout = total(&met, &prox);
            if cout < best.0 - 1e-9 {
                best = (cout, d.e.clone(), d.nets.clone());
            }
        }
        // reroutage complet d'une zone de 20 x 20 mm autour d'une piece tiree au hasard
        if p.zone_periode > 0 && essais % p.zone_periode == p.zone_periode - 1 {
            essais += 1;
            let i = mobiles[rng.below(mobiles.len())];
            let (zx, zy) = m.centre(i, &d.e.poses[i]);
            let mut nets: Vec<usize> = (0..nn)
                .filter(|&n| {
                    !m.nets[n].plan
                        && d.est_routable(n)
                        && m.nets[n].pins.iter().any(|&(a, k)| {
                            let (x, y, _, _) = m.pad_world(a, k, &d.e.poses[a]);
                            (x - zx).abs() < 10.0 && (y - zy).abs() < 10.0
                        })
                })
                .collect();
            nets.truncate(30);
            zones_tentees += 1;
            let (ok, olds) = d.rerouter_opt(&nets, false);
            let mut delta = 0.0;
            let mut nouveaux = Vec::new();
            for (n, _) in &olds {
                let nm = d.metrique(*n);
                let np = d.prox_net(*n);
                delta += cout_de(nm) + p.prox_poids * np - cout_de(met[*n]) - p.prox_poids * prox[*n];
                nouveaux.push((*n, nm, np));
            }
            if ok && (delta <= 0.0 || rng.f64() < (-delta / temp).exp()) {
                for (n, nm, np) in nouveaux {
                    met[n] = nm;
                    prox[n] = np;
                }
                cout += delta;
                zones_acceptees += 1;
                if cout < best.0 - 1e-9 {
                    best = (cout, d.e.clone(), d.nets.clone());
                }
            } else {
                d.annuler(olds);
            }
            continue;
        }
        // poids des pieces : charge des reseaux (retreci, vias, detours = longueur / demi-perimetre)
        let poids: Vec<f64> = mobiles
            .iter()
            .map(|&i| {
                1.0 + m.comp_nets[i]
                    .iter()
                    .map(|&n| {
                        let h = m.hpwl_net(n, &d.e).max(1.0);
                        // connexions en echec : les pieces de ces reseaux sont tirees en priorite
                        met[n][2] + 0.5 * met[n][1] + ((met[n][0] / h) - 1.0).max(0.0).min(5.0) + 200.0 * met[n][5]
                    })
                    .sum::<f64>()
            })
            .collect();
        let tot: f64 = poids.iter().sum();
        let mut r = rng.f64() * tot;
        let mut k = 0;
        while k + 1 < poids.len() && r > poids[k] {
            r -= poids[k];
            k += 1;
        }
        let i = mobiles[k];
        let pi = d.e.poses[i];
        let ors = &m.comps[i].orients;
        let typ = rng.f64();
        let mut mv: Vec<(usize, Pose)> = Vec::new();
        let centre = |e: &Etat, j: usize, rot: i32, bot: bool, cx: f64, cy: f64| -> Pose {
            let b = m.bb_rel(j, rot, bot);
            let _ = e;
            Pose { x: snap(cx - (b[0] + b[2]) / 2.0), y: snap(cy - (b[1] + b[3]) / 2.0), rot, bot }
        };
        let (cx, cy) = m.centre(i, &pi);
        let sig = (p.sigma * (1.0 - frac)).max(0.3);
        if typ < 0.55 {
            mv.push((i, centre(&d.e, i, pi.rot, pi.bot, cx + rng.gauss() * sig, cy + rng.gauss() * sig)));
        } else if typ < 0.75 {
            let al: Vec<&(i32, bool)> = ors.iter().filter(|o| o.1 == pi.bot && o.0 != pi.rot).collect();
            if al.is_empty() {
                continue;
            }
            let o = *al[rng.below(al.len())];
            mv.push((i, centre(&d.e, i, o.0, o.1, cx, cy)));
        } else if typ < 0.88 {
            // echange avec une piece de meme empreinte proche
            let cands: Vec<usize> = mobiles
                .iter()
                .copied()
                .filter(|&j| j != i && sigs[j] == sigs[i] && {
                    let (x, y) = m.centre(j, &d.e.poses[j]);
                    (x - cx).abs() + (y - cy).abs() < 25.0
                })
                .collect();
            if cands.is_empty() {
                continue;
            }
            let j = cands[rng.below(cands.len())];
            let pj = d.e.poses[j];
            let (dx, dy) = m.centre(j, &pj);
            mv.push((i, centre(&d.e, i, pj.rot, pj.bot, dx, dy)));
            mv.push((j, centre(&d.e, j, pi.rot, pi.bot, cx, cy)));
        } else {
            let al: Vec<&(i32, bool)> = ors.iter().filter(|o| o.1 != pi.bot).collect();
            if al.is_empty() {
                continue;
            }
            let o = *al[rng.below(al.len())];
            mv.push((i, centre(&d.e, i, o.0, o.1, cx, cy)));
        }
        essais += 1;
        // legalite du placement (memes regles que le recuit v3 : boites + 0,25 mm, traversantes, obstacles, bloc)
        let excl: Vec<i32> = mv.iter().map(|x| x.0 as i32).collect();
        let mut rects = Vec::new();
        let mut legal = true;
        for (j, q) in &mv {
            match occ.legal(m, *j, q, &excl) {
                Some(r) => rects.push(r),
                None => {
                    legal = false;
                    break;
                }
            }
        }
        if legal && mv.len() == 2 {
            for (fa, a) in &rects[0] {
                for (fb, b) in &rects[1] {
                    if fa == fb && ov(a, b) {
                        legal = false;
                    }
                }
            }
        }
        if !legal {
            ill += 1;
            continue;
        }
        let anciens: Vec<(usize, Pose)> = mv.iter().map(|(j, _)| (*j, d.e.poses[*j])).collect();
        for (j, q) in &mv {
            d.deplacer(*j, *q);
        }
        let mut nets: Vec<usize> = Vec::new();
        for (j, _) in &mv {
            nets.extend(m.comp_nets[*j].iter().copied());
            nets.extend(d.reseaux_genes(*j));
        }
        nets.sort();
        nets.dedup();
        // reseaux de plan (GND) : reroutage partiel des seules descentes concernees
        let plans: Vec<usize> = nets.iter().copied().filter(|&n| m.nets[n].plan).collect();
        nets.retain(|&n| !m.nets[n].plan);
        let tr = Instant::now();
        let mut olds: Vec<(usize, NetR)> = Vec::new();
        let mut ok = true;
        for g in plans {
            let mut pads = Vec::new();
            let mut zones = Vec::new();
            for (j, q) in &mv {
                for (k, pd) in m.comps[*j].pads.iter().enumerate() {
                    if pd.net == Some(g) && !pd.th {
                        pads.push((*j, k));
                    }
                    let (x, y, hw, hh) = m.pad_world(*j, k, q);
                    zones.push((x, y, hw.max(hh) + 0.8));
                }
            }
            let (okg, old) = d.rerouter_plan_partiel(g, &pads, &zones);
            olds.push((g, old));
            ok &= okg;
        }
        let (ok2, olds2) = d.rerouter_opt(&nets, false);
        ok &= ok2;
        olds.extend(olds2);
        if std::env::var("CONJ_DEBUG").is_ok() {
            journal(format!("    essai {} : piece {} ; reseaux {:?} ; touches {} ; ok {} ; {:.2} s",
                essais, m.comps[i].nom, nets.iter().map(|&n| m.nets[n].nom.clone()).collect::<Vec<_>>(), olds.len(), ok, tr.elapsed().as_secs_f64()));
        }
        let mut delta = 0.0;
        let mut nouveaux = Vec::new();
        for (n, _) in &olds {
            let nm = d.metrique(*n);
            let np = d.prox_net(*n);
            delta += cout_de(nm) + p.prox_poids * np - cout_de(met[*n]) - p.prox_poids * prox[*n];
            nouveaux.push((*n, nm, np));
        }
        let accepte = ok && (delta <= 0.0 || (temp > 0.0 && rng.f64() < (-delta / temp).exp()));
        if accepte {
            for (k2, (j, _)) in mv.iter().enumerate() {
                occ.reinsert(*j, &rects[k2]);
            }
            for (n, nm, np) in nouveaux {
                met[n] = nm;
                prox[n] = np;
            }
            cout += delta;
            acc += 1;
            if cout < best.0 - 1e-9 {
                best = (cout, d.e.clone(), d.nets.clone());
            }
        } else {
            if !ok {
                refus += 1;
            }
            d.annuler(olds);
            for (j, q) in anciens.iter().rev() {
                d.deplacer(*j, *q);
            }
        }
        if el - dernier_journal > 300.0 {
            dernier_journal = el;
            journal(format!(
                "  chaine {} : {:.0} s, essais {}, acceptes {}, illegaux {}, refuses (routage) {} ; cout {:.1} (meilleur {:.1}, depart {:.1})",
                graine, el, essais, acc, ill, refus, cout, best.0, cout_depart
            ));
        }
    }
    journal(format!("  zones rerouties : {} acceptees sur {}", zones_acceptees, zones_tentees));
    ResConj { cout_depart, cout: best.0, etat: best.1, nets: best.2, essais, illegaux: ill, refuses_routage: refus, acceptes: acc, graine }
}
