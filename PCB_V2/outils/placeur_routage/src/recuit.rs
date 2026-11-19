//! Recuit simule dont le cout vient du routeur global (evaluation incrementale + reroutage complet periodique).
//! Plusieurs chaines independantes en parallele (rayon), chacune avec sa graine : resultats reproductibles.
use crate::geom::*;
use crate::globalr::*;
use std::time::Instant;

#[derive(Clone)]
pub struct Params {
    pub mouvements: usize,
    pub periode: usize,
    pub iters_pf: usize,
    pub t0_facteur: f64,
    pub pas: f64,
    pub sigma0: f64,
    /// pieces autorisees a bouger (None = toutes les mobiles) : recuit local de la boucle placement <-> routage
    pub mobiles: Option<Vec<usize>>,
}

pub struct Resultat {
    pub etat: Etat,
    pub cout: f64,
    pub cout_depart: f64,
    pub graine: u64,
    pub essais: usize,
    pub acceptes: usize,
    pub illegaux: usize,
    pub temps: f64,
    pub t0: f64,
    pub legalises: usize,
    pub ejections: usize,
    pub prof: [f64; 3],
}

struct Chaine<'a> {
    m: &'a Modele,
    etat: Etat,
    occ: Occup,
    rt: Routeur<'a>,
    rng: Rng,
    /// mouvements legaux : directs, legalises par spirale, par ejection
    stats_leg: [usize; 3],
    /// profil : secondes en legalisation, en cout annexe, en routage incremental
    prof: [f64; 3],
    /// cout annexe (decouplage + SW) courant, recalcule seulement si une piece concernee bouge
    extra: f64,
    concerne_extra: Vec<bool>,
    /// pieces deplacables par cette chaine
    movers: Vec<usize>,
    /// penalite de pression courante (somme sur les pieces)
    pression: f64,
    /// phase d'exploration des campagnes : cout = demi-perimetres ponderes (rapide), sans routeur
    hpwl_mode: bool,
    hp: Vec<f64>,
    hp_tot: f64,
    /// estimation de congestion (RUDY) utilisee en phase d'exploration
    rudy: Option<Rudy>,
}

enum Mouv {
    Pieces(Vec<(usize, Pose)>),
}

impl<'a> Chaine<'a> {
    fn cout(&self) -> f64 {
        if self.hpwl_mode {
            let r = self.rudy.as_ref().map(|r| r.lambda * r.pen).unwrap_or(0.0);
            return self.hp_tot + r + self.extra + self.pression;
        }
        self.rt.objectif() + self.extra + self.pression
    }

    fn hp_net(&self, n: usize) -> f64 {
        let net = &self.m.nets[n];
        if net.plan {
            0.0
        } else {
            net.poids.max(1.0) * self.m.hpwl_net(n, &self.etat)
        }
    }

    fn activer_hpwl(&mut self) {
        self.hpwl_mode = true;
        if let Some(r) = self.rudy.as_mut() {
            r.recalculer(self.m, &self.etat);
        }
        self.hp = (0..self.m.nets.len()).map(|n| self.hp_net(n)).collect();
        self.hp_tot = self.hp.iter().sum();
    }

    /// Essai en mode demi-perimetres (phase d'exploration).
    fn tenter_hpwl(&mut self, v: Vec<(usize, Pose)>, rects: Vec<Vec<(u8, Rect)>>, temp: f64, forcer_rejet: bool) -> Option<(f64, bool)> {
        let avant = self.cout();
        let anciens: Vec<(usize, Pose)> = v.iter().map(|(i, _)| (*i, self.etat.poses[*i])).collect();
        for (i, p) in &v {
            self.etat.poses[*i] = *p;
        }
        let mut nets: Vec<usize> = v.iter().flat_map(|(i, _)| self.m.comp_nets[*i].iter().copied()).collect();
        nets.sort();
        nets.dedup();
        let nouv: Vec<f64> = nets.iter().map(|&n| self.hp_net(n)).collect();
        let dh: f64 = nets.iter().zip(nouv.iter()).map(|(&n, &h)| h - self.hp[n]).sum();
        let mut maj_rudy: Vec<(usize, Option<[f64; 4]>, Option<[f64; 4]>)> = Vec::new();
        let mut pen0 = 0.0;
        if let Some(r) = self.rudy.as_mut() {
            maj_rudy = nets.iter().map(|&n| (n, r.bb[n], Rudy::bbox(self.m, &self.etat, n))).collect();
            pen0 = r.pen;
            r.modifier(&maj_rudy, false);
        }
        let extra0 = self.extra;
        if v.iter().any(|x| self.concerne_extra[x.0]) {
            self.extra = self.m.cout_extra(&self.etat);
        }
        let rterm = self.rudy.as_ref().map(|r| r.lambda * r.pen).unwrap_or(0.0);
        let d = self.hp_tot + dh + rterm + self.extra + self.pression - avant;
        let ok = !forcer_rejet && (d <= 0.0 || (temp > 0.0 && self.rng.f64() < (-d / temp).exp()));
        if ok {
            for (k, (i, _)) in v.iter().enumerate() {
                self.occ.reinsert(*i, &rects[k]);
            }
            for (&n, &h) in nets.iter().zip(nouv.iter()) {
                self.hp[n] = h;
            }
            self.hp_tot += dh;
            return Some((d, true));
        }
        for (i, p) in &anciens {
            self.etat.poses[*i] = *p;
        }
        if let Some(r) = self.rudy.as_mut() {
            r.modifier(&maj_rudy, true);
            r.pen = pen0;
        }
        self.extra = extra0;
        Some((d, false))
    }

    fn origine_pour_centre(&self, i: usize, rot: i32, bot: bool, cx: f64, cy: f64) -> Pose {
        let b = self.m.bb_rel(i, rot, bot);
        Pose { x: snap(cx - (b[0] + b[2]) / 2.0), y: snap(cy - (b[1] + b[3]) / 2.0), rot, bot }
    }

    /// Cible : moyenne ponderee des centres des boites des autres broches de ses reseaux (hors plans).
    fn cible(&self, i: usize) -> Option<(f64, f64)> {
        let m = self.m;
        let (mut sx, mut sy, mut sw) = (0.0, 0.0, 0.0);
        for &n in &m.comp_nets[i] {
            let net = &m.nets[n];
            if net.plan {
                continue;
            }
            let mut b = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
            let mut any = false;
            for &(a, k) in &net.pins {
                if a == i {
                    continue;
                }
                let (x, y, _, _) = m.pad_world(a, k, &self.etat.poses[a]);
                b = [b[0].min(x), b[1].min(y), b[2].max(x), b[3].max(y)];
                any = true;
            }
            if any {
                let w = net.poids.max(1.0);
                sx += w * (b[0] + b[2]) / 2.0;
                sy += w * (b[1] + b[3]) / 2.0;
                sw += w;
            }
        }
        if sw == 0.0 {
            None
        } else {
            Some((sx / sw, sy / sw))
        }
    }

    fn proposer(&mut self, frac: f64, sig0: f64) -> Option<Mouv> {
        let m = self.m;
        let sig = (sig0 * (1.0 - frac).powi(2)).max(0.25);
        // le bloc hacheur est fige a sa position v3 (iteration 2) : pas de mouvement de bloc
        let r = self.rng.f64();
        let nmov = self.movers.len();
        let k = self.rng.below(nmov);
        let i = self.movers[k];
        let p = self.etat.poses[i];
        let (cx, cy) = m.centre(i, &p);
        let ors = &m.comps[i].orients;
        if r < 0.18 {
            let k2 = self.rng.below(nmov);
            let j = self.movers[k2];
            if i == j {
                return None;
            }
            let q = self.etat.poses[j];
            let (dx, dy) = m.centre(j, &q);
            if (cx - dx).abs() + (cy - dy).abs() > 3.0 * sig + 6.0 {
                return None;
            }
            let (mut rj, mut bj) = (q.rot, q.bot);
            if self.rng.f64() < 0.3 {
                let o = m.comps[j].orients[self.rng.below(m.comps[j].orients.len())];
                rj = o.0;
                bj = o.1;
            }
            let pi = self.origine_pour_centre(i, p.rot, p.bot, dx, dy);
            let pj = self.origine_pour_centre(j, rj, bj, cx, cy);
            return Some(Mouv::Pieces(vec![(i, pi), (j, pj)]));
        }
        let (mut rot, mut bot) = (p.rot, p.bot);
        let (nx, ny);
        if r < 0.50 {
            nx = cx + self.rng.gauss() * sig;
            ny = cy + self.rng.gauss() * sig;
            if self.rng.f64() < 0.15 {
                let o = ors[self.rng.below(ors.len())];
                rot = o.0;
                bot = o.1;
            }
        } else if r < 0.68 {
            let t = self.cible(i)?;
            let s2 = (sig * 0.3).max(0.3);
            nx = t.0 + self.rng.gauss() * s2;
            ny = t.1 + self.rng.gauss() * s2;
            if self.rng.f64() < 0.3 {
                let o = ors[self.rng.below(ors.len())];
                rot = o.0;
                bot = o.1;
            }
        } else if r < 0.88 {
            let al: Vec<&(i32, bool)> = ors.iter().filter(|o| o.1 == bot && o.0 != rot).collect();
            if al.is_empty() {
                return None;
            }
            let o = *al[self.rng.below(al.len())];
            rot = o.0;
            nx = cx;
            ny = cy;
        } else {
            let al: Vec<&(i32, bool)> = ors.iter().filter(|o| o.1 != bot).collect();
            if al.is_empty() {
                return None;
            }
            let o = *al[self.rng.below(al.len())];
            rot = o.0;
            bot = o.1;
            nx = cx + self.rng.gauss() * 0.5;
            ny = cy + self.rng.gauss() * 0.5;
        }
        Some(Mouv::Pieces(vec![(i, self.origine_pour_centre(i, rot, bot, nx, ny))]))
    }

    fn legal_paire(&self, v: &[(usize, Pose)]) -> Option<Vec<Vec<(u8, Rect)>>> {
        let m = self.m;
        let excl: Vec<i32> = v.iter().map(|x| x.0 as i32).collect();
        let mut rects = Vec::new();
        for (i, p) in v {
            rects.push(self.occ.legal(m, *i, p, &excl)?);
        }
        if v.len() == 2 {
            for (fa, a) in &rects[0] {
                for (fb, b) in &rects[1] {
                    if fa == fb && ov(a, b) {
                        return None;
                    }
                }
            }
        }
        Some(rects)
    }

    /// Legalisation locale d'un mouvement illegal d'une piece : spirale autour de la cible,
    /// puis ejection d'une piece mobile genante unique vers l'ancienne place.
    fn legaliser(&mut self, v: &[(usize, Pose)]) -> Option<(Vec<(usize, Pose)>, Vec<Vec<(u8, Rect)>>, u8)> {
        if let Some(r) = self.legal_paire(v) {
            return Some((v.to_vec(), r, 0));
        }
        if v.len() != 1 {
            return None;
        }
        let m = self.m;
        let (i, p) = v[0];
        for &rad in &[0.25, 0.5, 0.75, 1.0, 1.5] {
            let k0 = self.rng.below(8);
            for k in 0..8 {
                let a = ((k + k0) % 8) as f64 * std::f64::consts::FRAC_PI_4;
                let q = Pose { x: snap(p.x + rad * a.cos()), y: snap(p.y + rad * a.sin()), ..p };
                if let Some(r) = self.occ.legal(m, i, &q, &[i as i32]) {
                    return Some((vec![(i, q)], vec![r], 1));
                }
            }
        }
        // ejection
        let rs = m.rects_of(i, &p);
        if !m.in_board(&rs[0].1) {
            return None;
        }
        let mut bl: Vec<i32> = Vec::new();
        for (f, r) in &rs {
            for o in self.occ.blockers(*f, r, &[i as i32]) {
                if !bl.contains(&o) {
                    bl.push(o);
                }
            }
        }
        if bl.len() != 1 || bl[0] < 0 || m.comps[bl[0] as usize].statut != Statut::Mobile {
            return None;
        }
        let j = bl[0] as usize;
        let (cx, cy) = m.centre(i, &self.etat.poses[i]);
        let q = self.etat.poses[j];
        let pj = self.origine_pour_centre(j, q.rot, q.bot, cx, cy);
        let w = vec![(i, p), (j, pj)];
        let r = self.legal_paire(&w)?;
        Some((w, r, 2))
    }

    /// Essaie un mouvement : None si illegal ; sinon Some((delta, accepte)).
    fn tenter(&mut self, mv: &Mouv, temp: f64, forcer_rejet: bool) -> Option<(f64, bool)> {
        let Mouv::Pieces(v0) = mv;
        let t0 = Instant::now();
        let leg = self.legaliser(v0);
        self.prof[0] += t0.elapsed().as_secs_f64();
        let (v, rects, how) = leg?;
        let t1 = Instant::now();
        self.stats_leg[how as usize] += 1;
        if self.hpwl_mode {
            return self.tenter_hpwl(v, rects, temp, forcer_rejet);
        }
        let comps: Vec<usize> = v.iter().map(|x| x.0).collect();
        let avant = self.cout();
        let mut taches: Vec<usize> = Vec::new();
        for &i in &comps {
            for &t in &self.rt.taches_de_comp[i] {
                if !taches.contains(&t) {
                    taches.push(t);
                }
            }
        }
        let mut sauve: Vec<(usize, Route)> = taches.iter().map(|&t| (t, self.rt.ripup(t))).collect();
        let anciens: Vec<(usize, Pose)> = comps.iter().map(|&i| (i, self.etat.poses[i])).collect();
        for &i in &comps {
            self.rt.retirer_pads(i);
        }
        for (i, p) in &v {
            self.etat.poses[*i] = *p;
        }
        for &i in &comps {
            let p = self.etat.poses[i];
            self.rt.poser_pads(i, &p);
        }
        // victimes : routes d'autres reseaux recouvertes par les nouvelles pastilles
        let mut vict: Vec<usize> = Vec::new();
        for &i in &comps {
            vict.extend(self.rt.victimes_pads(i));
        }
        for t in vict {
            if !sauve.iter().any(|x| x.0 == t) {
                let r = self.rt.ripup(t);
                sauve.push((t, r));
            }
        }
        sauve.sort_by_key(|x| x.0);
        for (t, _) in &sauve {
            let r = self.rt.route_tache(*t, &self.etat);
            self.rt.commit(*t, r);
        }
        let t2 = Instant::now();
        let extra0 = self.extra;
        if v.iter().any(|x| self.concerne_extra[x.0]) {
            self.extra = self.m.cout_extra(&self.etat);
        }
        let pression0 = self.pression;
        if !self.m.pression.is_empty() {
            for (k, (i, p)) in v.iter().enumerate() {
                self.pression += self.m.pression_piece(*i, p) - self.m.pression_piece(*i, &anciens[k].1);
            }
        }
        self.prof[1] += t2.elapsed().as_secs_f64();
        let apres = self.cout();
        let d = apres - avant;
        let ok = !forcer_rejet && (d <= 0.0 || (temp > 0.0 && self.rng.f64() < (-d / temp).exp()));
        if ok {
            for (k, (i, _)) in v.iter().enumerate() {
                self.occ.reinsert(*i, &rects[k]);
            }
            self.prof[2] += t1.elapsed().as_secs_f64();
            return Some((d, true));
        }
        self.extra = extra0;
        self.pression = pression0;
        for (t, _) in &sauve {
            self.rt.ripup(*t);
        }
        for &i in &comps {
            self.rt.retirer_pads(i);
        }
        for (i, p) in &anciens {
            self.etat.poses[*i] = *p;
        }
        for &i in &comps {
            let p = self.etat.poses[i];
            self.rt.poser_pads(i, &p);
        }
        for (t, r) in sauve {
            self.rt.commit(t, r);
        }
        self.prof[2] += t1.elapsed().as_secs_f64();
        Some((d, false))
    }
}

pub fn chaine(m: &Modele, depart: &Etat, graine: u64, p: &Params) -> Resultat {
    let t_start = Instant::now();
    let etat = depart.clone();
    let occ = Occup::new(m, &etat);
    let mut rt = Routeur::new(m, &etat, p.pas);
    rt.focus = true;
    rt.route_tout(&etat, p.iters_pf);
    let mut concerne_extra = vec![false; m.comps.len()];
    for (i, k, ics) in &m.decaps {
        let _ = k;
        concerne_extra[*i] = true;
        for &(a, _) in ics {
            concerne_extra[a] = true;
        }
    }
    for &i in &m.soft {
        concerne_extra[i] = true;
    }
    for &i in m.bloc.iter().chain(m.hacheur.iter()) {
        concerne_extra[i] = true;
    }
    let extra = m.cout_extra(&etat);
    let movers = p.mobiles.clone().unwrap_or_else(|| m.mobiles.clone());
    let pression: f64 = (0..m.comps.len()).map(|i| m.pression_piece(i, &etat.poses[i])).sum();
    let mut ch = Chaine { m, etat, occ, rt, rng: Rng::new(graine), stats_leg: [0; 3], prof: [0.0; 3], extra, concerne_extra, movers, pression, hpwl_mode: false, hp: Vec::new(), hp_tot: 0.0, rudy: None };
    let cout_depart = ch.cout();
    // temperature initiale : moyenne des hausses sur un echantillon de mouvements (rejetes d'office)
    let mut ups = Vec::new();
    let mut k = 0;
    while ups.len() < 60 && k < 2000 {
        k += 1;
        if let Some(mv) = ch.proposer(0.0, p.sigma0) {
            if let Some((d, _)) = ch.tenter(&mv, 0.0, true) {
                if d > 0.0 {
                    ups.push(d);
                }
            }
        }
    }
    let t0 = p.t0_facteur * ups.iter().sum::<f64>() / (ups.len().max(1) as f64);
    let tend = t0 * 1e-3;
    let mut best = (ch.cout(), ch.etat.clone());
    let (mut essais, mut acc, mut ill) = (0usize, 0usize, 0usize);
    for it in 0..p.mouvements {
        let frac = it as f64 / p.mouvements as f64;
        let temp = t0 * (tend / t0).powf(frac);
        if it > 0 && it % p.periode == 0 {
            let e = ch.etat.clone();
            ch.rt.route_tout(&e, p.iters_pf);
            let c = ch.cout();
            if c < best.0 {
                best = (c, ch.etat.clone());
            }
        }
        let mv = match ch.proposer(frac, p.sigma0) {
            Some(v) => v,
            None => continue,
        };
        essais += 1;
        match ch.tenter(&mv, temp, false) {
            None => ill += 1,
            Some((_, true)) => acc += 1,
            _ => {}
        }
    }
    let e = ch.etat.clone();
    ch.rt.route_tout(&e, p.iters_pf);
    let c = ch.cout();
    if c < best.0 {
        best = (c, ch.etat.clone());
    }
    Resultat {
        etat: best.1,
        cout: best.0,
        cout_depart,
        graine,
        essais,
        acceptes: acc,
        illegaux: ill,
        temps: t_start.elapsed().as_secs_f64(),
        t0,
        legalises: ch.stats_leg[1],
        ejections: ch.stats_leg[2],
        prof: ch.prof,
    }
}


// ---------------------------------------------------------------------------- campagnes depuis zero (iteration 7)

/// Parametres d'une campagne : recuit global a paliers, refroidissement geometrique lent, rechauffes.
#[derive(Clone)]
pub struct ParamCampagne {
    pub mouvements_max: usize,
    pub alpha: f64,
    pub palier_par_piece: usize,
    pub acc_initiale: f64,
    pub pas: f64,
    pub periode: usize,
    pub t_fin_rel: f64,
    /// capacite RUDY (mm de piste par mm2) et poids du depassement
    pub rudy_cap: f64,
    pub rudy_lambda: f64,
}

pub struct ResCampagne {
    pub etat: Etat,
    pub cout: f64,
    pub cout_depart: f64,
    pub t0: f64,
    pub acc_initiale_mesuree: f64,
    pub paliers: usize,
    pub rechauffes: usize,
    pub mouvements: usize,
    pub temps: f64,
    /// fin de l'exploration (avenant 2.2, A.5) : paliers, mouvements, HPWL, cout
    pub exploration: (usize, usize, f64, f64),
    /// longueur du noeud de commutation de chaque convertisseur estimee a la fin de l'exploration (demi-perimetre)
    pub noeuds_exploration: Vec<Option<f64>>,
}

/// Groupes fonctionnels : chaque circuit integre (U, hors bloc) avec ses passifs a 2 pastilles relies a lui par un reseau
/// de signal (au plus 8 broches, hors plans et rails), puis les deux canaux complets (_ALIM1, _ALIM2).
pub fn groupes_fonctionnels(m: &Modele) -> Vec<Vec<usize>> {
    let mut pris = vec![false; m.comps.len()];
    let mut out = Vec::new();
    for &u in &m.mobiles {
        let c = &m.comps[u];
        if !c.nom.starts_with('U') || c.pads.len() < 3 {
            continue;
        }
        let mut g = vec![u];
        for &n in &m.comp_nets[u] {
            let net = &m.nets[n];
            if net.plan || net.classe == "RAILS" || net.classe == "PUISSANCE_8V" || net.pins.len() > 8 {
                continue;
            }
            for &(j, _) in &net.pins {
                let cj = &m.comps[j];
                if j != u && !pris[j] && cj.statut == Statut::Mobile && cj.pads.len() == 2 && "RCDL".contains(&cj.nom[..1]) && !g.contains(&j) {
                    g.push(j);
                }
            }
        }
        if g.len() >= 2 {
            for &j in &g {
                pris[j] = true;
            }
            out.push(g);
        }
    }
    for suf in ["_ALIM1", "_ALIM2"] {
        let g: Vec<usize> = m.mobiles.iter().copied().filter(|&i| m.comps[i].nom.ends_with(suf)).collect();
        if g.len() >= 2 {
            out.push(g);
        }
    }
    // convertisseur libere : groupe compact (deplacement / rotation d'ensemble), avec et sans la compensation
    if m.bloc_libre && m.hacheur.len() >= 2 {
        out.push(m.hacheur.clone());
        let p: Vec<usize> = m.hacheur.iter().copied().filter(|&i| m.hacheur_puissance.contains(&i)).collect();
        if p.len() >= 2 {
            out.push(p);
        }
    }
    out
}

/// Placement initial aleatoire legal des pieces mobiles (fixes, facade, mecanique, bloc inchanges) : grandes pieces
/// d'abord, position et orientation (face comprise si permise) tirees au hasard, balayage en dernier recours.
pub fn placement_aleatoire(m: &Modele, graine: u64) -> Option<Etat> {
    let mut rng = Rng::new(graine ^ 0x5EED_0F_CAFE);
    let mut e = m.etat_v3();
    let mut occ = Occup::new_sans_mobiles(m, &e);
    let mut ordre: Vec<(f64, f64, usize)> = m
        .mobiles
        .iter()
        .map(|&i| {
            let b = m.bb_rel(i, 0, false);
            ((b[2] - b[0]) * (b[3] - b[1]), rng.f64(), i)
        })
        .collect();
    ordre.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap().then(a.1.partial_cmp(&b.1).unwrap()));
    // convertisseur libere : ses pieces d'abord, en grappe autour d'un point tire au hasard (arrangement interne
    // aleatoire, groupe compact comme demande) ; les autres pieces ensuite, n'importe ou
    let ancre = (m.w * (0.15 + 0.7 * rng.f64()), m.h * (0.15 + 0.7 * rng.f64()));
    if m.bloc_libre {
        let mut h: Vec<(f64, f64, usize)> = ordre.iter().copied().filter(|x| m.hacheur.contains(&x.2)).collect();
        h.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());
        let reste: Vec<(f64, f64, usize)> = ordre.iter().copied().filter(|x| !m.hacheur.contains(&x.2)).collect();
        ordre = h;
        ordre.extend(reste);
    }
    for &(_, _, i) in &ordre {
        let ors = m.comps[i].orients.clone();
        let mut fait = None;
        let grappe = m.bloc_libre && m.hacheur.contains(&i);
        for essai in 0..4000 {
            let o = ors[rng.below(ors.len())];
            let (cx, cy) = if grappe {
                let s = 4.0 + essai as f64 / 200.0;
                (ancre.0 + rng.gauss() * s, ancre.1 + rng.gauss() * s)
            } else {
                (rng.f64() * m.w, rng.f64() * m.h)
            };
            let b = m.bb_rel(i, o.0, o.1);
            let p = Pose { x: snap(cx - (b[0] + b[2]) / 2.0), y: snap(cy - (b[1] + b[3]) / 2.0), rot: o.0, bot: o.1 };
            if let Some(rs) = occ.legal(m, i, &p, &[i as i32]) {
                fait = Some((p, rs));
                break;
            }
        }
        if fait.is_none() {
            let mut os = ors.clone();
            for k in (1..os.len()).rev() {
                os.swap(k, rng.below(k + 1));
            }
            'balayage: for o in os {
                let b = m.bb_rel(i, o.0, o.1);
                let mut y = 0.25;
                while y < m.h {
                    let mut x = 0.25;
                    while x < m.w {
                        let p = Pose { x: snap(x - (b[0] + b[2]) / 2.0), y: snap(y - (b[1] + b[3]) / 2.0), rot: o.0, bot: o.1 };
                        if let Some(rs) = occ.legal(m, i, &p, &[i as i32]) {
                            fait = Some((p, rs));
                            break 'balayage;
                        }
                        x += 0.5;
                    }
                    y += 0.5;
                }
            }
        }
        let (p, rs) = fait?;
        e.poses[i] = p;
        occ.inserer(i, &rs);
    }
    Some(e)
}

impl<'a> Chaine<'a> {
    /// Mouvements de campagne : l'amplitude suit la temperature relative tr (1 = T0) ; grands deplacements, echanges
    /// a longue distance, translation et demi-tour (rotation de 180 degres) de groupes fonctionnels.
    fn proposer_grand(&mut self, tr: f64, groupes: &[Vec<usize>]) -> Option<Mouv> {
        let m = self.m;
        let sig = 0.4 + 40.0 * tr.min(1.0);
        let r = self.rng.f64();
        if r < 0.08 && !groupes.is_empty() {
            let g = &groupes[self.rng.below(groupes.len())];
            let (dx, dy) = (self.rng.gauss() * sig * 0.5, self.rng.gauss() * sig * 0.5);
            let v: Vec<(usize, Pose)> = g
                .iter()
                .map(|&i| {
                    let p = self.etat.poses[i];
                    (i, Pose { x: snap(p.x + dx), y: snap(p.y + dy), ..p })
                })
                .collect();
            return Some(Mouv::Pieces(v));
        }
        if r < 0.11 && !groupes.is_empty() {
            let g = &groupes[self.rng.below(groupes.len())];
            let cs: Vec<(f64, f64)> = g.iter().map(|&i| m.centre(i, &self.etat.poses[i])).collect();
            let (gx, gy) = (cs.iter().map(|c| c.0).sum::<f64>() / cs.len() as f64, cs.iter().map(|c| c.1).sum::<f64>() / cs.len() as f64);
            let mut v = Vec::new();
            for (k, &i) in g.iter().enumerate() {
                let p = self.etat.poses[i];
                let rot = (p.rot + 180).rem_euclid(360);
                if !m.comps[i].orients.contains(&(rot, p.bot)) {
                    return None;
                }
                v.push((i, self.origine_pour_centre(i, rot, p.bot, 2.0 * gx - cs[k].0, 2.0 * gy - cs[k].1)));
            }
            return Some(Mouv::Pieces(v));
        }
        let nmov = self.movers.len();
        let i = self.movers[self.rng.below(nmov)];
        let p = self.etat.poses[i];
        let (cx, cy) = m.centre(i, &p);
        let ors = m.comps[i].orients.clone();
        if r < 0.28 {
            let j = self.movers[self.rng.below(nmov)];
            if i == j {
                return None;
            }
            let q = self.etat.poses[j];
            let (dx, dy) = m.centre(j, &q);
            if (cx - dx).abs() + (cy - dy).abs() > 3.0 * sig + 6.0 {
                return None;
            }
            let o = if self.rng.f64() < 0.3 { m.comps[j].orients[self.rng.below(m.comps[j].orients.len())] } else { (q.rot, q.bot) };
            return Some(Mouv::Pieces(vec![(i, self.origine_pour_centre(i, p.rot, p.bot, dx, dy)), (j, self.origine_pour_centre(j, o.0, o.1, cx, cy))]));
        }
        let (mut rot, mut bot, nx, ny);
        if r < 0.58 {
            nx = cx + self.rng.gauss() * sig;
            ny = cy + self.rng.gauss() * sig;
            rot = p.rot;
            bot = p.bot;
            if self.rng.f64() < 0.2 {
                let o = ors[self.rng.below(ors.len())];
                rot = o.0;
                bot = o.1;
            }
        } else if r < 0.72 {
            let t = self.cible(i)?;
            nx = t.0 + self.rng.gauss() * (0.3 + 0.3 * sig);
            ny = t.1 + self.rng.gauss() * (0.3 + 0.3 * sig);
            let o = ors[self.rng.below(ors.len())];
            rot = o.0;
            bot = o.1;
        } else if r < 0.88 {
            let al: Vec<&(i32, bool)> = ors.iter().filter(|o| o.1 == p.bot && o.0 != p.rot).collect();
            if al.is_empty() {
                return None;
            }
            let o = *al[self.rng.below(al.len())];
            rot = o.0;
            bot = o.1;
            nx = cx;
            ny = cy;
        } else {
            let al: Vec<&(i32, bool)> = ors.iter().filter(|o| o.1 != p.bot).collect();
            if al.is_empty() {
                return None;
            }
            let o = *al[self.rng.below(al.len())];
            rot = o.0;
            bot = o.1;
            nx = cx + self.rng.gauss() * 0.5;
            ny = cy + self.rng.gauss() * 0.5;
        }
        Some(Mouv::Pieces(vec![(i, self.origine_pour_centre(i, rot, bot, nx, ny))]))
    }
}

/// Recuit global de campagne : T0 calibree pour un taux d'acceptation initial voulu (~85 %), paliers de
/// `palier_par_piece` x pieces mouvements a temperature constante, refroidissement geometrique (alpha),
/// rechauffe (x5, au plus 3) apres 15 paliers sans amelioration du meilleur.
pub fn chaine_campagne(m: &Modele, depart: &Etat, graine: u64, p: &ParamCampagne, groupes: &[Vec<usize>], journal: &dyn Fn(String)) -> ResCampagne {
    let t_start = Instant::now();
    let etat = depart.clone();
    let occ = Occup::new(m, &etat);
    let mut rt = Routeur::new(m, &etat, p.pas);
    rt.focus = true;
    rt.route_tout(&etat, 4);
    let mut concerne_extra = vec![false; m.comps.len()];
    for (i, _, ics) in &m.decaps {
        concerne_extra[*i] = true;
        for &(a, _) in ics {
            concerne_extra[a] = true;
        }
    }
    for &i in m.soft.iter().chain(m.bloc.iter()).chain(m.hacheur.iter()) {
        concerne_extra[i] = true;
    }
    let extra = m.cout_extra(&etat);
    let mut ch = Chaine { m, etat, occ, rt, rng: Rng::new(graine), stats_leg: [0; 3], prof: [0.0; 3], extra, concerne_extra, movers: m.mobiles.clone(), pression: 0.0, hpwl_mode: false, hp: Vec::new(), hp_tot: 0.0, rudy: None };
    // phase 1 : exploration (cout = demi-perimetres ponderes + congestion RUDY + decouplage + noeud SW), haute temperature
    ch.rudy = Some(Rudy::new(m, &ch.etat, p.rudy_cap, p.rudy_lambda));
    ch.activer_hpwl();
    let cout_depart = ch.cout();
    let (b1, s1) = recuire(&mut ch, groupes, p.mouvements_max, p.acc_initiale, p.t_fin_rel, p.alpha, p.palier_par_piece * m.mobiles.len(), 0, journal, "exploration", 1.0, m.param.recuit.rechauffes);
    ch.hpwl_mode = false;
    ch.etat = b1.1.clone();
    ch.occ = Occup::new(m, &ch.etat);
    let exploration = (s1.2, s1.4, m.hpwl(&ch.etat), b1.0);
    let noeuds_exploration: Vec<Option<f64>> =
        m.cfg.convertisseurs.iter().map(|cv| m.reseau_commutation(cv).map(|n| m.hpwl_net(n, &ch.etat))).collect();
    {
        let r = Rudy::new(m, &ch.etat, p.rudy_cap, p.rudy_lambda);
        let (mx, p95, dep) = r.stats();
        journal(format!("  fin d'exploration : cout {:.1} ; HPWL {:.0} mm ; congestion RUDY max {:.2} p95 {:.2} mm/mm2, depassement {:.0} mm", b1.0, m.hpwl(&ch.etat), mx, p95, dep));
        if m.bloc_libre {
            journal(format!("  fin d'exploration : convertisseur cout {:.0}, boucle {:?}, noeud SW {:.1} mm", m.cout_hacheur(&ch.etat), m.boucle_chaude_ap(&ch.etat),
                m.sw_net().map(|n| m.hpwl_net(n, &ch.etat)).unwrap_or(0.0)));
        }
    }
    // phase 2 : affinage avec le routeur global (capacite, vias, debordement)
    ch.hpwl_mode = false;
    ch.rt = Routeur::new(m, &ch.etat, p.pas);
    ch.rt.focus = true;
    let e = ch.etat.clone();
    ch.rt.route_tout(&e, 4);
    ch.extra = m.cout_extra(&ch.etat);
    // affinage : mouvements locaux, temperature basse (acceptation visee 3 %), sans rechauffe
    let (b2, s2) = recuire(&mut ch, groupes, p.mouvements_max / 20, 0.03, 0.01, 0.85, (p.palier_par_piece / 4).max(1) * m.mobiles.len(), p.periode, journal, "routeur global", 0.08, 0);
    let best = b2;
    let (t0, acc_init) = (s1.0, s1.1);
    let (paliers, rechauffes, mouvements) = (s1.2 + s2.2, s1.3 + s2.3, s1.4 + s2.4);
    ResCampagne { etat: best.1, cout: best.0, cout_depart, t0, acc_initiale_mesuree: acc_init, paliers, rechauffes, mouvements, temps: t_start.elapsed().as_secs_f64(), exploration, noeuds_exploration }
}

/// Recuit a paliers : calibration de T0 pour le taux d'acceptation voulu, paliers a temperature constante,
/// refroidissement geometrique, rechauffe (x5, au plus 3) apres 15 paliers sans amelioration.
/// Renvoie ((meilleur cout, meilleur etat), (T0, acceptation du 1er palier, paliers, rechauffes, mouvements)).
#[allow(clippy::too_many_arguments)]
fn recuire(ch: &mut Chaine, groupes: &[Vec<usize>], budget: usize, acc_cible: f64, t_fin_rel: f64, alpha: f64, palier: usize,
           periode: usize, journal: &dyn Fn(String), nom: &str, tr_max: f64, rech_max: usize) -> ((f64, Etat), (f64, f64, usize, usize, usize)) {
    let t_start = Instant::now();
    let mut ups = Vec::new();
    let mut k = 0;
    while ups.len() < 200 && k < 8000 {
        k += 1;
        if let Some(mv) = ch.proposer_grand(tr_max, groupes) {
            if let Some((d, _)) = ch.tenter(&mv, 0.0, true) {
                if d > 0.0 {
                    ups.push(d);
                }
            }
        }
    }
    let moy = ups.iter().sum::<f64>() / ups.len().max(1) as f64;
    let t0 = moy / (1.0 / acc_cible).ln();
    let mut temp = t0;
    let mut best = (ch.cout(), ch.etat.clone());
    let (mut mouvements, mut paliers, mut rechauffes, mut stagn) = (0usize, 0usize, 0usize, 0usize);
    let mut acc_init = -1.0;
    let mut gel = 0usize;
    while mouvements < budget && temp > t0 * t_fin_rel && gel < 5 {
        let (mut legaux, mut acc) = (0usize, 0usize);
        for _ in 0..palier {
            if periode > 0 && mouvements % periode == periode - 1 {
                let e = ch.etat.clone();
                ch.rt.route_tout(&e, 4);
            }
            mouvements += 1;
            let mv = match ch.proposer_grand(tr_max * (temp / t0).min(1.0), groupes) {
                Some(v) => v,
                None => continue,
            };
            match ch.tenter(&mv, temp, false) {
                None => {}
                Some((_, true)) => {
                    legaux += 1;
                    acc += 1;
                }
                Some((_, false)) => legaux += 1,
            }
            if ch.cout() < best.0 - 1e-6 && !ch.hpwl_mode {
                // en mode routeur, le cout incremental derive : le meilleur n'est retenu qu'en fin de palier
            }
        }
        if !ch.hpwl_mode {
            let e = ch.etat.clone();
            ch.rt.route_tout(&e, 4);
        }
        let c = ch.cout();
        let taux = acc as f64 / legaux.max(1) as f64;
        if acc_init < 0.0 {
            acc_init = taux;
        }
        if taux < 0.005 {
            gel += 1;
        } else {
            gel = 0;
        }
        if c < best.0 - 1e-6 {
            best = (c, ch.etat.clone());
            stagn = 0;
        } else {
            stagn += 1;
        }
        paliers += 1;
        if paliers % 10 == 0 || paliers == 1 {
            journal(format!("  {} palier {} : T {:.3} (T0 {:.3}) ; acceptation {:.0} % ; cout {:.1} ; meilleur {:.1} ; {} mouvements ; {:.0} s",
                nom, paliers, temp, t0, 100.0 * taux, c, best.0, mouvements, t_start.elapsed().as_secs_f64()));
        }
        // arret des que le meilleur ne progresse plus depuis recuit.arret_paliers_sans_progres paliers (150 par defaut ;
        // 40 degradait le recuit) et qu'il n'y a plus de rechauffe a faire
        if stagn >= ch.m.param.recuit.arret_paliers_sans_progres && rechauffes >= rech_max {
            journal(format!("  {} : arret, meilleur inchange depuis {} paliers ({} paliers, {:.0} s)", nom, stagn, paliers, t_start.elapsed().as_secs_f64()));
            break;
        }
        if stagn >= 15 && rechauffes < rech_max {
            temp *= 5.0;
            rechauffes += 1;
            stagn = 0;
            journal(format!("  {} : rechauffe {} (T {:.3})", nom, rechauffes, temp));
        } else {
            temp *= alpha;
        }
    }
    (best, (t0, acc_init, paliers, rechauffes, mouvements))
}

/// Estimation de congestion RUDY (Spindler) : chaque reseau repartit uniformement (W+H)/(W*H) mm de piste par mm2
/// sur sa boite englobante ; depassement = somme sur des cases de 2 mm de max(0, demande - capacite).
pub struct Rudy {
    nx: usize,
    ny: usize,
    c: f64,
    dem: Vec<f64>,
    cap: f64,
    pub lambda: f64,
    bb: Vec<Option<[f64; 4]>>,
    pen: f64,
    marque: Vec<u32>,
    tampon: u32,
}

impl Rudy {
    pub fn bbox(m: &Modele, e: &Etat, n: usize) -> Option<[f64; 4]> {
        let net = &m.nets[n];
        if net.plan || net.pins.len() < 2 {
            return None;
        }
        let mut r = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
        for &(i, k) in &net.pins {
            let (x, y, _, _) = m.pad_world(i, k, &e.poses[i]);
            r = [r[0].min(x), r[1].min(y), r[2].max(x), r[3].max(y)];
        }
        // largeur minimale 1 mm (une piste occupe une bande)
        if r[2] - r[0] < 1.0 {
            let c = (r[0] + r[2]) / 2.0;
            r[0] = c - 0.5;
            r[2] = c + 0.5;
        }
        if r[3] - r[1] < 1.0 {
            let c = (r[1] + r[3]) / 2.0;
            r[1] = c - 0.5;
            r[3] = c + 0.5;
        }
        Some(r)
    }

    pub fn new(m: &Modele, e: &Etat, cap_densite: f64, lambda: f64) -> Rudy {
        let c = 2.0;
        let nx = (m.w / c - 1e-3).ceil() as usize;
        let ny = (m.h / c - 1e-3).ceil() as usize;
        let mut r = Rudy { nx, ny, c, dem: vec![0.0; nx * ny], cap: cap_densite * c * c, lambda, bb: vec![None; m.nets.len()], pen: 0.0, marque: vec![0; nx * ny], tampon: 0 };
        r.recalculer(m, e);
        r
    }

    pub fn recalculer(&mut self, m: &Modele, e: &Etat) {
        self.dem.iter_mut().for_each(|v| *v = 0.0);
        for n in 0..m.nets.len() {
            self.bb[n] = Rudy::bbox(m, e, n);
            if let Some(b) = self.bb[n] {
                self.appliquer(b, 1.0);
            }
        }
        self.pen = self.dem.iter().map(|&v| (v - self.cap).max(0.0)).sum();
    }

    fn plage(&self, b: [f64; 4]) -> (usize, usize, usize, usize) {
        let i0 = ((b[0] / self.c).floor().max(0.0) as usize).min(self.nx - 1);
        let i1 = ((b[2] / self.c).floor().max(0.0) as usize).min(self.nx - 1);
        let j0 = ((b[1] / self.c).floor().max(0.0) as usize).min(self.ny - 1);
        let j1 = ((b[3] / self.c).floor().max(0.0) as usize).min(self.ny - 1);
        (i0, i1, j0, j1)
    }

    fn appliquer(&mut self, b: [f64; 4], signe: f64) {
        let (w, h) = (b[2] - b[0], b[3] - b[1]);
        let d = signe * (w + h) / (w * h);
        let (i0, i1, j0, j1) = self.plage(b);
        for j in j0..=j1 {
            let y0 = (j as f64 * self.c).max(b[1]);
            let y1 = ((j + 1) as f64 * self.c).min(b[3]);
            if y1 <= y0 {
                continue;
            }
            for i in i0..=i1 {
                let x0 = (i as f64 * self.c).max(b[0]);
                let x1 = ((i + 1) as f64 * self.c).min(b[2]);
                if x1 > x0 {
                    self.dem[j * self.nx + i] += d * (x1 - x0) * (y1 - y0);
                }
            }
        }
    }

    /// Remplace les boites (ancienne -> nouvelle, ou l'inverse si `inverse`) et met a jour le depassement.
    fn modifier(&mut self, maj: &[(usize, Option<[f64; 4]>, Option<[f64; 4]>)], inverse: bool) {
        self.tampon = self.tampon.wrapping_add(1);
        if self.tampon == 0 {
            self.marque.iter_mut().for_each(|v| *v = 0);
            self.tampon = 1;
        }
        let mut cases: Vec<usize> = Vec::new();
        for (_, a, b) in maj {
            for bb in [a, b].into_iter().flatten() {
                let (i0, i1, j0, j1) = self.plage(*bb);
                for j in j0..=j1 {
                    for i in i0..=i1 {
                        let k = j * self.nx + i;
                        if self.marque[k] != self.tampon {
                            self.marque[k] = self.tampon;
                            cases.push(k);
                        }
                    }
                }
            }
        }
        let avant: f64 = cases.iter().map(|&k| (self.dem[k] - self.cap).max(0.0)).sum();
        for &(n, a, b) in maj {
            let (ret, aj) = if inverse { (b, a) } else { (a, b) };
            if let Some(x) = ret {
                self.appliquer(x, -1.0);
            }
            if let Some(x) = aj {
                self.appliquer(x, 1.0);
            }
            self.bb[n] = aj;
        }
        let apres: f64 = cases.iter().map(|&k| (self.dem[k] - self.cap).max(0.0)).sum();
        self.pen += apres - avant;
    }

    /// (densite max, densite p95, depassement total) en mm/mm2 et mm
    pub fn stats(&self) -> (f64, f64, f64) {
        let a = self.c * self.c;
        let mut v: Vec<f64> = self.dem.iter().map(|x| x / a).collect();
        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
        (v[v.len() - 1], v[(v.len() as f64 * 0.95) as usize], self.pen)
    }
}
