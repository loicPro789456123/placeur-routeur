//! Post-traitement du routage final (lot G, demande du 24/11/2026) :
//! - lissage des marches et des détours (`finition.lissage_marches`) : chaque chaîne de pistes entre deux points
//!   d'ancrage (pastille, via, jonction, changement de largeur, cuivre fixe du réseau) est raccourcie par des
//!   raccourcis au style des pistes (octogonal : un segment à 0/45/90°, ou deux ; manhattan : un segment
//!   orthogonal, ou un L ; libre : un segment de direction quelconque) ;
//! - accord des longueurs (paires différentielles, `longueurs_appariees`, `longueurs` minimales) par méandres
//!   rectangulaires posés sur les plus longs segments du réseau trop court.
//! Chaque nouveau segment est contrôlé sur les cartes d'occupation du routeur (disque d'isolement de la piste ; marge
//! d'une demi-diagonale de cellule hors grille) ; aucun angle aigu n'est créé ; `moteur.rs` revient au routage
//! d'origine si la vérification finale refuse le résultat.

use super::*;
use std::collections::BTreeMap;

type Pt = (f64, f64);

/// Entrées recentrées dans les pastilles (bout libre prolongé jusqu'au centre) : désactivé. Mesure du 24/11/2026 sur
/// METROID 4005 : entrées excentrées 1255 -> 1115 seulement, longueur +138 mm, coût commun +193 (la piste entre dans
/// la pastille sans changer le cuivre fabriqué) ; à reprendre avec une cible de routage au centre.
const RECENTRER: bool = false;

/// Marge ajoutée au disque d'isolement des raccourcis et des méandres (mm).
const MARGE_LISSAGE: f64 = P;

fn cle(p: Pt, l: u8) -> (i64, i64, u8) {
    ((p.0 * 1000.0).round() as i64, (p.1 * 1000.0).round() as i64, l)
}

fn lg(a: Pt, b: Pt) -> f64 {
    ((b.0 - a.0).powi(2) + (b.1 - a.1).powi(2)).sqrt()
}

fn unit(a: Pt, b: Pt) -> Pt {
    let l = lg(a, b);
    if l < 1e-12 {
        (0.0, 0.0)
    } else {
        ((b.0 - a.0) / l, (b.1 - a.1) / l)
    }
}

fn dot(a: Pt, b: Pt) -> f64 {
    a.0 * b.0 + a.1 * b.1
}

/// Centre de cellule de la grille du routeur.
fn sur_grille(p: Pt) -> bool {
    let f = |v: f64| {
        let q = v / P - 0.5;
        (q - q.round()).abs() < 1e-4
    };
    f(p.0) && f(p.1)
}

fn octilineaire(a: Pt, b: Pt) -> bool {
    let (dx, dy) = ((b.0 - a.0).abs(), (b.1 - a.1).abs());
    dx < 1e-6 || dy < 1e-6 || (dx - dy).abs() < 1e-6
}

/// Chaîne de segments entre deux ancrages (même couche, même largeur).
struct Chaine {
    /// vrai si le premier (dernier) point est le centre d'une pastille ajouté au bout libre
    centre_debut: bool,
    centre_fin: bool,
    pts: Vec<Pt>,
    segs: Vec<usize>,
    couche: u8,
    w: f64,
}

impl<'a> Detail<'a> {
    /// Le point touche-t-il du cuivre du réseau autre que ses pistes (pastille, via, amorce, cuivre du bloc,
    /// région d'empreinte) ? Un tel point reste fixe.
    fn ancre_cuivre(&self, n: usize, p: Pt, l: u8, w: f64) -> bool {
        let m = self.m;
        let tol = w / 2.0 + 1e-3;
        for v in &self.nets[n].vias {
            if lg(p, (v.x, v.y)) <= m.regles.via.diametre / 2.0 + tol {
                return true;
            }
        }
        for i in 0..m.comps.len() {
            let pose = self.e.poses[i];
            for k in 0..m.comps[i].pads.len() {
                let pd = &m.comps[i].pads[k];
                if pd.net != Some(n) {
                    continue;
                }
                let couche_ok = pd.th || (if pose.bot { l == 1 } else { l == 0 });
                if !couche_ok {
                    continue;
                }
                let (x, y, hw, hh) = m.pad_world(i, k, &pose);
                if (x - p.0).abs() > hw + hh + 2.0 && (y - p.1).abs() > hw + hh + 2.0 {
                    continue;
                }
                if dist_pad(p.0, p.1, x, y, hw, hh, pd.forme) <= tol {
                    return true;
                }
            }
            for r in &m.comps[i].regions {
                if r.1 != Some(n) {
                    continue;
                }
                let (pts, ls) = m.region_monde(i, r, &pose);
                if ls.contains(&(l as usize)) && dist_poly(p.0, p.1, &pts) <= tol {
                    return true;
                }
            }
        }
        for (n2, segs) in &self.amorces {
            if *n2 != n {
                continue;
            }
            for o in segs {
                if o.couche == l && dist_seg(p.0, p.1, o) <= o.w / 2.0 + tol {
                    return true;
                }
            }
        }
        for cu in &m.cuivre_bloc {
            if cu.net != Some(n) {
                continue;
            }
            let touche = match cu.obj {
                CuivreObj::Seg { x1, y1, x2, y2, w: wo, couche } => {
                    couche == l && dist_seg(p.0, p.1, &SegD { x1, y1, x2, y2, w: wo, couche }) <= wo / 2.0 + tol
                }
                CuivreObj::Via { x, y, d } => lg(p, (x, y)) <= d / 2.0 + tol,
                CuivreObj::Region { couche, r } => couche == l && dist_rect(p.0, p.1, &r) <= tol,
            };
            if touche {
                return true;
            }
        }
        false
    }

    /// Centre de la pastille du réseau qui contient `p` (couche `l`), si `p` n'en est pas déjà proche et ne touche
    /// aucun autre cuivre fixe du réseau (via, amorce, cuivre du bloc).
    fn centre_pastille(&self, n: usize, p: Pt, l: u8) -> Option<Pt> {
        let m = self.m;
        for v in &self.nets[n].vias {
            if lg(p, (v.x, v.y)) <= m.regles.via.diametre {
                return None;
            }
        }
        if self.amorces.iter().any(|(n2, segs)| *n2 == n && segs.iter().any(|o| o.couche == l && dist_seg(p.0, p.1, o) <= o.w)) {
            return None;
        }
        for i in 0..m.comps.len() {
            let pose = self.e.poses[i];
            for k in 0..m.comps[i].pads.len() {
                let pd = &m.comps[i].pads[k];
                if pd.net != Some(n) || !(pd.th || (if pose.bot { l == 1 } else { l == 0 })) {
                    continue;
                }
                let (x, y, hw, hh) = m.pad_world(i, k, &pose);
                if (x - p.0).abs() > hw + 0.01 || (y - p.1).abs() > hh + 0.01 {
                    continue;
                }
                if dist_pad(p.0, p.1, x, y, hw, hh, pd.forme) <= 1e-6 {
                    return if lg(p, (x, y)) > 0.25 * hw.min(hh) + 0.05 - 1e-3 { Some((x, y)) } else { None };
                }
            }
        }
        None
    }

    /// Disques d'isolement d'une piste de largeur `w` du réseau `n` : (sur la grille, hors grille).
    fn disques(&self, n: usize, w: f64) -> (Vec<(i32, i32)>, Vec<(i32, i32)>) {
        let clr_x = (self.clr_net(n) - MARGE_ISOL - self.m.regles.isolement.defaut).max(0.0);
        let r = w / 2.0 - H_DEF + clr_x;
        // une cellule de plus que le routeur : un raccourci ne vient pas coller ses voisins (terme d'écart du coût)
        (offsets(r + MARGE_LISSAGE), offsets(r + MARGE_LISSAGE + DIAG))
    }

    /// Segment libre pour le réseau `n` (cartes d'occupation, état strict).
    fn seg_libre(&self, n: usize, a: Pt, b: Pt, l: u8, offs: &(Vec<(i32, i32)>, Vec<(i32, i32)>)) -> bool {
        let key = n as u32 + 1;
        let o = if sur_grille(a) && sur_grille(b) && octilineaire(a, b) { &offs.0 } else { &offs.1 };
        let len = lg(a, b);
        let k = ((len / (P / 2.0)).ceil() as usize).max(1);
        for t in 0..=k {
            let u = t as f64 / k as f64;
            let (x, y) = (a.0 + u * (b.0 - a.0), a.1 + u * (b.1 - a.1));
            if x < 0.0 || y < 0.0 {
                return false;
            }
            let nd = self.node_at(x, y, l) as usize;
            if self.disk_state(nd, key, o, false) != 1 {
                return false;
            }
        }
        true
    }

    /// Segment libre pour le réseau `n`, le cuivre du partenaire `j` (paire différentielle) étant ignoré ; l'écart au
    /// partenaire est contrôlé à part (`ecart_partenaire`).
    fn seg_libre_paire(&self, n: usize, j: usize, a: Pt, b: Pt, l: u8, o: &[(i32, i32)]) -> bool {
        let (key, kj) = (n as u32 + 1, j as u32 + 1);
        let len = lg(a, b);
        let k = ((len / (P / 2.0)).ceil() as usize).max(1);
        for t in 0..=k {
            let u = t as f64 / k as f64;
            let (x, y) = (a.0 + u * (b.0 - a.0), a.1 + u * (b.1 - a.1));
            if x < 0.0 || y < 0.0 {
                return false;
            }
            let nd = self.node_at(x, y, l) as usize;
            let (c0, l0) = (nd % self.nc, nd / self.nc);
            let (cx, cy) = ((c0 % self.nx) as i32, (c0 / self.nx) as i32);
            for &(dx, dy) in o {
                let (uu, vv) = (cx + dx, cy + dy);
                if uu < 0 || vv < 0 || uu >= self.nx as i32 || vv >= self.ny as i32 {
                    return false;
                }
                let c = vv as usize * self.nx + uu as usize;
                let m = l0 * self.nc + c;
                if self.hard[c] || (self.zone[c] && m < self.nc) || !self.kp.ok(m, key) || !self.kt.ok2(m, key, kj) {
                    return false;
                }
            }
        }
        true
    }

    /// Plus petite distance (axe à axe, mm) entre la polyligne et les pistes du réseau `j` sur la couche `l`.
    fn ecart_partenaire(&self, j: usize, pts: &[Pt], l: u8) -> f64 {
        let mut d = f64::MAX;
        for o in self.nets[j].segs.iter().filter(|o| o.couche == l) {
            for w in pts.windows(2) {
                let s = SegD { x1: w[0].0, y1: w[0].1, x2: w[1].0, y2: w[1].1, w: 0.0, couche: l };
                let k = ((lg(w[0], w[1]) / 0.02).ceil() as usize).max(1);
                for t in 0..=k {
                    let u = t as f64 / k as f64;
                    d = d.min(dist_seg(w[0].0 + u * (w[1].0 - w[0].0), w[0].1 + u * (w[1].1 - w[0].1), o));
                    d = d.min(dist_seg(o.x1 + u * (o.x2 - o.x1), o.y1 + u * (o.y2 - o.y1), &s));
                }
            }
        }
        d
    }

    fn polyligne_libre(&self, n: usize, pts: &[Pt], l: u8, offs: &(Vec<(i32, i32)>, Vec<(i32, i32)>)) -> bool {
        pts.windows(2).all(|w| self.seg_libre(n, w[0], w[1], l, offs))
    }

    /// Chaînes de pistes du réseau entre ancrages.
    fn chaines(&self, n: usize) -> Vec<Chaine> {
        let segs = &self.nets[n].segs;
        let mut ends: BTreeMap<(i64, i64, u8), Vec<(usize, bool)>> = BTreeMap::new();
        for (k, s) in segs.iter().enumerate() {
            if lg((s.x1, s.y1), (s.x2, s.y2)) < 1e-9 {
                continue;
            }
            ends.entry(cle((s.x1, s.y1), s.couche)).or_default().push((k, true));
            ends.entry(cle((s.x2, s.y2), s.couche)).or_default().push((k, false));
        }
        let mut ancre: BTreeMap<(i64, i64, u8), bool> = BTreeMap::new();
        for (c, v) in &ends {
            let p = (c.0 as f64 / 1000.0, c.1 as f64 / 1000.0);
            let a = v.len() != 2
                || (segs[v[0].0].w - segs[v[1].0].w).abs() > 1e-9
                || v[0].0 == v[1].0
                || self.ancre_cuivre(n, p, c.2, segs[v[0].0].w);
            ancre.insert(*c, a);
        }
        let mut vu = vec![false; segs.len()];
        let mut out = Vec::new();
        for (c, v) in &ends {
            if !ancre[c] {
                continue;
            }
            for &(k0, debut0) in v {
                if vu[k0] {
                    continue;
                }
                let s0 = &segs[k0];
                let mut pts = vec![(c.0 as f64 / 1000.0, c.1 as f64 / 1000.0)];
                let mut idx = Vec::new();
                let (mut k, mut debut) = (k0, debut0);
                loop {
                    vu[k] = true;
                    idx.push(k);
                    let s = &segs[k];
                    let suiv = if debut { (s.x2, s.y2) } else { (s.x1, s.y1) };
                    pts.push(suiv);
                    let ck = cle(suiv, s.couche);
                    if ancre.get(&ck).copied().unwrap_or(true) {
                        break;
                    }
                    let lst = &ends[&ck];
                    let autre = if lst[0].0 == k { lst[1] } else { lst[0] };
                    if vu[autre.0] {
                        break;
                    }
                    k = autre.0;
                    debut = autre.1;
                }
                // bouts libres dans une pastille du réseau : la chaîne vise le centre de la pastille (entrée centrée)
                let l = s0.couche;
                let (mut centre_debut, mut centre_fin) = (false, false);
                let dernier = *pts.last().unwrap();
                if RECENTRER && ends.get(&cle(dernier, l)).map_or(false, |v| v.len() == 1) {
                    if let Some(cp) = self.centre_pastille(n, dernier, l) {
                        let m = pts.len();
                        if dot(unit(pts[m - 2], pts[m - 1]), unit(dernier, cp)) >= -1e-9 {
                            pts.push(cp);
                            centre_fin = true;
                        }
                    }
                }
                let premier = pts[0];
                if RECENTRER && v.len() == 1 {
                    if let Some(cp) = self.centre_pastille(n, premier, l) {
                        if dot(unit(cp, premier), unit(pts[0], pts[1])) >= -1e-9 {
                            pts.insert(0, cp);
                            centre_debut = true;
                        }
                    }
                }
                out.push(Chaine { centre_debut, centre_fin, pts, segs: idx, couche: s0.couche, w: s0.w });
            }
        }
        out
    }

    /// Directions sortantes des autres pistes du réseau au point `p` (même couche), hors segments `exclus`.
    fn directions_au_point(&self, n: usize, p: Pt, l: u8, exclus: &[usize]) -> Vec<Pt> {
        let ck = cle(p, l);
        let mut v = Vec::new();
        for (k, s) in self.nets[n].segs.iter().enumerate() {
            if s.couche != l || exclus.contains(&k) {
                continue;
            }
            if cle((s.x1, s.y1), l) == ck {
                v.push(unit((s.x1, s.y1), (s.x2, s.y2)));
            } else if cle((s.x2, s.y2), l) == ck {
                v.push(unit((s.x2, s.y2), (s.x1, s.y1)));
            }
        }
        v
    }

    /// Raccourcis candidats de `a` à `b` au style `mode`.
    fn raccourcis(a: Pt, b: Pt, mode: u8) -> Vec<Vec<Pt>> {
        let (dx, dy) = (b.0 - a.0, b.1 - a.1);
        match mode {
            MODE_LIBRE => vec![vec![a, b]],
            MODE_MANHATTAN => {
                if dx.abs() < 1e-6 || dy.abs() < 1e-6 {
                    vec![vec![a, b]]
                } else {
                    vec![vec![a, (b.0, a.1), b], vec![a, (a.0, b.1), b]]
                }
            }
            _ => {
                if octilineaire(a, b) {
                    vec![vec![a, b]]
                } else {
                    let d = dx.abs().min(dy.abs());
                    let (sx, sy) = (dx.signum(), dy.signum());
                    vec![vec![a, (a.0 + sx * d, a.1 + sy * d), b], vec![a, (b.0 - sx * d, b.1 - sy * d), b]]
                }
            }
        }
    }

    /// Lissage des marches et des détours de tous les réseaux ; rend (chaînes raccourcies, longueur gagnée en mm,
    /// segments en moins).
    pub fn lisser(&mut self, mode: u8) -> (usize, f64, i64) {
        let (mut nb, mut gain, mut dseg) = (0usize, 0.0f64, 0i64);
        for n in 0..self.nets.len() {
            if self.nets[n].segs.len() < 2 {
                continue;
            }
            let chaines = self.chaines(n);
            let mut remplace: Vec<(Vec<usize>, Vec<SegD>)> = Vec::new();
            for ch in chaines {
                if ch.pts.len() < 3 {
                    continue;
                }
                let offs = self.disques(n, ch.w);
                let k = ch.pts.len() - 1;
                let pts = &ch.pts;
                // angle aux ancrages : jamais plus aigu qu'avant (ni aigu s'il ne l'était pas)
                let dirs0 = self.directions_au_point(n, pts[0], ch.couche, &ch.segs);
                let dirsk = self.directions_au_point(n, pts[k], ch.couche, &ch.segs);
                let pire = |dirs: &[Pt], f: Pt| dirs.iter().map(|&o| dot(o, f)).fold(f64::MIN, f64::max);
                let old0 = pire(&dirs0, unit(pts[0], pts[1])).max(1e-9);
                let oldk = pire(&dirsk, unit(pts[k], pts[k - 1])).max(1e-9);
                let mut out: Vec<Pt> = vec![pts[0]];
                let mut i = 0;
                while i < k {
                    let mut fait = false;
                    for j in (i + 2..=k).rev() {
                        let old: f64 = (i..j).map(|t| lg(pts[t], pts[t + 1])).sum();
                        for cand in Self::raccourcis(pts[i], pts[j], mode) {
                            let cand: Vec<Pt> = cand.into_iter().fold(Vec::new(), |mut v, p| {
                                if v.last().map_or(true, |&q| lg(q, p) > 1e-6) {
                                    v.push(p);
                                }
                                v
                            });
                            if cand.len() < 2 {
                                continue;
                            }
                            let new: f64 = cand.windows(2).map(|w| lg(w[0], w[1])).sum();
                            if new > old + 1e-6 || (cand.len() - 1 >= j - i && new >= old - 1e-6) {
                                continue;
                            }
                            let f = unit(cand[0], cand[1]);
                            let ok_avant = if out.len() >= 2 {
                                dot(unit(out[out.len() - 2], out[out.len() - 1]), f) >= -1e-9
                            } else {
                                pire(&dirs0, f) <= old0 + 1e-9
                            };
                            if !ok_avant {
                                continue;
                            }
                            let dl = unit(cand[cand.len() - 2], cand[cand.len() - 1]);
                            let ok_apres = if j < k {
                                dot(dl, unit(pts[j], pts[j + 1])) >= -1e-9
                            } else {
                                pire(&dirsk, (-dl.0, -dl.1)) <= oldk + 1e-9
                            };
                            if !ok_apres || !self.polyligne_libre(n, &cand, ch.couche, &offs) {
                                continue;
                            }
                            out.extend_from_slice(&cand[1..]);
                            i = j;
                            fait = true;
                            break;
                        }
                        if fait {
                            break;
                        }
                    }
                    if !fait {
                        out.push(pts[i + 1]);
                        i += 1;
                    }
                }
                // entrée au centre gardée seulement si l'ancien bout a disparu (sinon : crochet inutile dans la pastille)
                let (p0, pk) = (ch.pts[0], ch.pts[k]);
                if ch.centre_fin && out.len() >= 2 && lg(out[out.len() - 2], pts[k - 1]) < 1e-9 && lg(out[out.len() - 1], pk) < 1e-9 {
                    out.pop();
                }
                if ch.centre_debut && out.len() >= 2 && lg(out[0], p0) < 1e-9 && lg(out[1], pts[1]) < 1e-9 {
                    out.remove(0);
                }
                let pts_ref: Vec<Pt> = {
                    let mut v = ch.pts.clone();
                    if ch.centre_fin {
                        v.pop();
                    }
                    if ch.centre_debut {
                        v.remove(0);
                    }
                    v
                };
                let l_ref: f64 = pts_ref.windows(2).map(|w| lg(w[0], w[1])).sum();
                let l_out: f64 = out.windows(2).map(|w| lg(w[0], w[1])).sum();
                let change = out.len() != pts_ref.len() || out.iter().zip(pts_ref.iter()).any(|(a, b)| lg(*a, *b) > 1e-9);
                if change && (out.len() < pts_ref.len() || l_out < l_ref - 1e-6 || out.first() != pts_ref.first() || out.last() != pts_ref.last()) {
                    gain += l_ref - l_out;
                    dseg += ch.segs.len() as i64 - (out.len() as i64 - 1);
                    nb += 1;
                    let segs = out
                        .windows(2)
                        .map(|w| SegD { x1: w[0].0, y1: w[0].1, x2: w[1].0, y2: w[1].1, w: ch.w, couche: ch.couche })
                        .collect();
                    remplace.push((ch.segs, segs));
                }
            }
            if !remplace.is_empty() {
                self.remplacer_segments(n, remplace);
            }
        }
        (nb, gain, dseg)
    }

    /// Remplace des segments du réseau (indices -> nouveaux segments) et retrame le réseau.
    fn remplacer_segments(&mut self, n: usize, remplace: Vec<(Vec<usize>, Vec<SegD>)>) {
        let retires: std::collections::BTreeSet<usize> = remplace.iter().flat_map(|r| r.0.iter().copied()).collect();
        let mut segs: Vec<SegD> =
            self.nets[n].segs.iter().enumerate().filter(|(k, _)| !retires.contains(k)).map(|(_, s)| s.clone()).collect();
        for (_, s) in remplace {
            segs.extend(s);
        }
        let old = self.nets[n].clone();
        self.arracher(n);
        let mut base = old.clone();
        base.segs.clear();
        base.vias.clear();
        base.noeuds.clear();
        base.kt.clear();
        base.kprox.clear();
        base.carres = 0.0;
        self.nets[n] = base;
        self.commit_geom(n, segs, old.vias.clone(), old.noeuds.clone());
    }

    /// Longueur des pistes d'un réseau (mm), comme la vérification (`validite.rs`).
    pub fn longueur_pistes(&self, n: usize) -> f64 {
        self.nets[n].segs.iter().map(|s| lg((s.x1, s.y1), (s.x2, s.y2))).sum()
    }

    /// Méandres sur les segments du réseau `n` pour ajouter `besoin` mm (au plus `besoin + marge`) ; rend la longueur
    /// ajoutée.
    fn meandrer(&mut self, n: usize, besoin: f64, marge: f64, partenaire: Option<usize>) -> f64 {
        let mut ajoute = 0.0;
        let mut ordre: Vec<usize> = (0..self.nets[n].segs.len()).collect();
        ordre.sort_by(|&a, &b| {
            let la = lg((self.nets[n].segs[a].x1, self.nets[n].segs[a].y1), (self.nets[n].segs[a].x2, self.nets[n].segs[a].y2));
            let lb = lg((self.nets[n].segs[b].x1, self.nets[n].segs[b].y1), (self.nets[n].segs[b].x2, self.nets[n].segs[b].y2));
            lb.partial_cmp(&la).unwrap_or(Ordering::Equal)
        });
        let clr = self.clr_net(n);
        let mut remplace: Vec<(Vec<usize>, Vec<SegD>)> = Vec::new();
        for k in ordre {
            let reste = besoin - ajoute;
            if reste <= 1e-6 {
                break;
            }
            let s = self.nets[n].segs[k].clone();
            let (a, b) = ((s.x1, s.y1), (s.x2, s.y2));
            let longueur = lg(a, b);
            // pas des méandres : au moins 3 largeurs et l'isolement de part et d'autre (règle usuelle, au pas de grille)
            let pas = ((3.0 * s.w).max(s.w + 2.0 * clr) / P).ceil() * P;
            let bord = pas.max(2.0 * s.w);
            if longueur < 2.0 * bord + pas {
                continue;
            }
            let kmax = ((longueur - 2.0 * bord + pas) / (2.0 * pas)).floor() as usize;
            if kmax == 0 {
                continue;
            }
            let u = unit(a, b);
            let offs = self.disques(n, s.w);
            // paire : écart actuel au partenaire le long du segment (les méandres ne s'en rapprochent pas)
            let ecart_p = partenaire.map(|j| self.ecart_partenaire(j, &[a, b], s.couche));
            let amax = 3.0f64;
            let amin = s.w.max(P);
            let mut pose = None;
            'essais: for nb in 1..=kmax {
                let mut amp = reste / (2.0 * nb as f64);
                if amp > amax {
                    if nb < kmax {
                        continue;
                    }
                    amp = amax;
                }
                if amp < amin {
                    if 2.0 * amin * nb as f64 > reste + marge {
                        continue;
                    }
                    amp = amin;
                }
                for cote in [1.0, -1.0] {
                    let v = (-u.1 * cote, u.0 * cote);
                    let t0 = (longueur - (2 * nb - 1) as f64 * pas) / 2.0;
                    let mut pts = vec![a];
                    for q in 0..nb {
                        let t = t0 + (2 * q) as f64 * pas;
                        let p0 = (a.0 + u.0 * t, a.1 + u.1 * t);
                        let p3 = (p0.0 + u.0 * pas, p0.1 + u.1 * pas);
                        pts.push(p0);
                        pts.push((p0.0 + v.0 * amp, p0.1 + v.1 * amp));
                        pts.push((p3.0 + v.0 * amp, p3.1 + v.1 * amp));
                        pts.push(p3);
                    }
                    pts.push(b);
                    let libre = match (partenaire, ecart_p) {
                        (Some(j), Some(e)) => {
                            pts.windows(2).all(|w| self.seg_libre_paire(n, j, w[0], w[1], s.couche, &offs.1))
                                && self.ecart_partenaire(j, &pts, s.couche) >= e.min(10.0) - 1e-3
                        }
                        _ => self.polyligne_libre(n, &pts, s.couche, &offs),
                    };
                    if libre {
                        pose = Some((pts, 2.0 * amp * nb as f64));
                        break 'essais;
                    }
                }
            }
            if let Some((pts, gain)) = pose {
                ajoute += gain;
                let segs = pts
                    .windows(2)
                    .map(|w| SegD { x1: w[0].0, y1: w[0].1, x2: w[1].0, y2: w[1].1, w: s.w, couche: s.couche })
                    .collect();
                remplace.push((vec![k], segs));
            }
        }
        if !remplace.is_empty() {
            self.remplacer_segments(n, remplace);
        }
        ajoute
    }

    /// Accord des longueurs : groupes appariés (paires, longueurs_appariees) puis longueurs minimales. Rend
    /// (réseaux allongés, groupes ou bornes encore hors tolérance).
    pub fn accorder(&mut self) -> (usize, usize) {
        let (mut allonges, mut restants) = (0usize, 0usize);
        let groupes = self.m.groupes_longueur.clone();
        for (nets, tol, paire) in groupes {
            let ls: Vec<f64> = nets.iter().map(|&n| self.longueur_pistes(n)).collect();
            let lmax = ls.iter().cloned().fold(0.0, f64::max);
            let mut ok = true;
            for (i, &n) in nets.iter().enumerate() {
                if lmax - ls[i] <= tol || self.nets[n].segs.is_empty() {
                    continue;
                }
                let besoin = lmax - tol / 2.0 - ls[i];
                let partenaire = if paire { nets.iter().copied().find(|&x| x != n) } else { None };
                let a = self.meandrer(n, besoin, tol / 2.0, partenaire);
                if a > 0.0 {
                    allonges += 1;
                }
                if lmax - (ls[i] + a) > tol + 1e-9 {
                    ok = false;
                }
            }
            if !ok {
                restants += 1;
            }
        }
        for (n, mn, mx) in self.m.longueurs_bornees.clone() {
            let l = self.longueur_pistes(n);
            if l < mn && !self.nets[n].segs.is_empty() {
                let marge = if mx.is_finite() { (mx - mn).max(0.0) } else { 1.0 };
                let a = self.meandrer(n, mn - l + marge.min(1.0) / 2.0, marge / 2.0, None);
                if a > 0.0 {
                    allonges += 1;
                }
                if l + a < mn {
                    restants += 1;
                }
            } else if l > mx {
                restants += 1;
            }
        }
        (allonges, restants)
    }
}
