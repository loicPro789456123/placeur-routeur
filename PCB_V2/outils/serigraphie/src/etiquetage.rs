//! Placement des designateurs : etiquetage (label placement) par recuit simule.
//!
//! 1. Candidats par composant : 2 sens de lecture x 3 tailles x positions autour du corps (4 cotes, glissement
//!    continu echantillonne au pas fin, coins compris, ecarts de 0 a 3 mm) + positions dans le corps (repli).
//! 2. Filtre dur (statique) : garde aux pastilles (meme face, traversantes = 2 faces), aux traits de
//!    serigraphie (apres rognage), aux textes libres, pas sur le corps d'un AUTRE composant, dans la carte
//!    (garde au bord du contour polygonal, aux decoupes et aux trous).
//! 3. Cout statique : distance au corps, decentrage, reduction de taille, sens vertical, sur son propre corps,
//!    vias touches (souple), ambiguite (plus proche d'un autre corps).
//! 4. Recuit simule global (chaines paralleles rayon, deterministes par graine) avec couts dynamiques :
//!    chevauchement texte/texte (interdit), desalignement de voisins alignes ; puis descente gloutonne.
//!    Dernier recours : designateur masque.

use crate::donnees::*;
use crate::geom::*;
use rayon::prelude::*;

pub const MASQUE: usize = usize::MAX;

#[derive(Clone, Debug)]
pub struct Params {
    pub garde_pastille: f64,
    pub masque: bool,
    pub garde_trait: f64,
    pub garde_texte: f64,
    pub garde_bord: f64,
    pub niveaux: Vec<(f64, f64)>,
    pub ecarts: Vec<f64>,
    pub pas: f64,
    pub k_max: usize,
    pub chaines: usize,
    pub mouvements: usize,
    pub graine: u64,
    pub cout_masque: f64,
    pub cout_conflit: f64,
    pub cout_align: f64,
    pub cout_ambigu: f64,
    pub cout_corps: f64,
    pub cout_niveau: f64,
    pub cout_vertical: f64,
    pub cout_via: f64,
    pub marge_ambigu: f64,
}

#[derive(Clone, Debug)]
pub struct Cand {
    pub x: f64,
    pub y: f64,
    pub rot: f64,
    pub vertical: bool,
    pub niv: usize,
    pub h: f64,
    pub w: f64,
    /// boite de la forme (encre gonflee de w/2)
    pub bb: [f64; 4],
    pub cout: f64,
    pub ambigu: bool,
    pub dans_corps: bool,
    pub vias: u32,
    pub cote: &'static str,
}

impl Cand {
    pub fn cx(&self) -> f64 {
        (self.bb[0] + self.bb[2]) / 2.0
    }
    pub fn cy(&self) -> f64 {
        (self.bb[1] + self.bb[3]) / 2.0
    }
}

/// geometrie de la carte (mils, repere commun) : contour et decoupes polygonaux (quelconques), trous ronds
#[derive(Debug)]
pub struct Carte {
    pub contour: Vec<P>,
    pub decoupes: Vec<Vec<P>>,
    /// boites des decoupes (pre-filtre)
    pub boites_decoupes: Vec<[f64; 4]>,
    pub trous: Vec<(P, f64)>,
}

impl Carte {
    pub fn new(contour: Vec<P>, decoupes: Vec<Vec<P>>, trous: Vec<(P, f64)>) -> Carte {
        let boites_decoupes = decoupes.iter().map(|d| boite_points(d)).collect();
        Carte { contour, decoupes, boites_decoupes, trous }
    }

    /// vrai si la boite bb est dans la carte avec la garde gb : dans le contour, hors des decoupes, loin des trous
    pub fn accepte(&self, bb: &[f64; 4], gb: f64) -> bool {
        if !boite_dans_polygone(bb, &self.contour, gb) {
            return false;
        }
        for (d, b) in self.decoupes.iter().zip(&self.boites_decoupes) {
            if d_boites(bb, b) < gb && !boite_hors_polygone(bb, d, gb) {
                return false;
            }
        }
        for (t, r) in &self.trous {
            if d_boites(bb, &[t.x, t.y, t.x, t.y]) < r + gb {
                return false;
            }
        }
        true
    }
}

pub struct Monde<'a> {
    pub d: &'a Donnees,
    pub carte: &'a Carte,
    /// formes des traits de serigraphie finaux par face
    pub traits: [Vec<Forme>; 2],
    /// textes visibles conserves (libres) par face
    pub textes_fixes: [Vec<Forme>; 2],
    pub g_pastilles: [Grille; 2],
    pub g_traits: [Grille; 2],
    pub g_textes: [Grille; 2],
    pub g_corps: [Grille; 2],
    pub vias: Vec<Forme>,
    pub g_vias: Grille,
}

fn rotations(face: Face) -> [(f64, bool); 2] {
    // (Rotation Altium, vertical). Face inferieure (miroir) : 0 = lisible vue de dessous de gauche a droite,
    // 270 = lisible vue de dessous de bas en haut (equivalent du 90 de la face superieure).
    match face {
        Face::Dessus => [(0.0, false), (90.0, true)],
        Face::Dessous => [(0.0, false), (270.0, true)],
    }
}

fn union(a: &mut [f64; 4], b: &[f64; 4]) {
    a[0] = a[0].min(b[0]);
    a[1] = a[1].min(b[1]);
    a[2] = a[2].max(b[2]);
    a[3] = a[3].max(b[3]);
}

/// echantillons de [a, b] au pas donne (au plus nmax), extremites et centre compris
fn echantillons(a: f64, b: f64, pas: f64, nmax: usize, centre: f64) -> Vec<f64> {
    if b <= a {
        return vec![a];
    }
    let n = (((b - a) / pas).ceil() as usize).clamp(1, nmax);
    let mut v: Vec<f64> = (0..=n).map(|i| a + (b - a) * i as f64 / n as f64).collect();
    if centre > a && centre < b {
        v.push(centre);
    }
    v
}

struct Local {
    pastilles: Vec<usize>,
    traits: Vec<usize>,
    textes: Vec<usize>,
    corps: Vec<usize>,
    vias: Vec<usize>,
}

/// candidats legaux d'un composant, tries par cout statique (au plus k_max)
pub fn candidats(m: &Monde, ic: usize, pr: &Params) -> Vec<Cand> {
    let c = &m.d.composants[ic];
    let f = c.face;
    let fi = f.idx();
    let b = c.corps;
    let (bcx, bcy) = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0);
    let emax = *pr.ecarts.last().unwrap();
    // obstacles locaux
    let lmax = largeur_encre(&c.nom, pr.niveaux[0].0) + pr.niveaux[0].1 + 2.0 * pr.niveaux[0].0;
    let zone = [b[0] - emax - lmax, b[1] - emax - lmax, b[2] + emax + lmax, b[3] + emax + lmax];
    let loc = Local {
        pastilles: m.g_pastilles[fi].requete(&zone, pr.garde_pastille + 1.0),
        traits: m.g_traits[fi].requete(&zone, pr.garde_trait + 1.0),
        textes: m.g_textes[fi].requete(&zone, pr.garde_texte + 1.0),
        corps: m.g_corps[fi].requete(&zone, emax + lmax),
        vias: m.g_vias.requete(&zone, pr.garde_pastille + 1.0),
    };
    let mut out: Vec<Cand> = Vec::new();
    let miroir = f == Face::Dessous;
    for (rot_deg, vertical) in rotations(f) {
        for (niv, &(h, w)) in pr.niveaux.iter().enumerate() {
            let e0 = boite_encre(&c.nom, 0.0, 0.0, h, rot_deg, miroir);
            let (bw, bh) = (e0[2] - e0[0] + w, e0[3] - e0[1] + w); // dimensions de la forme
            let mut poses: Vec<([f64; 2], &'static str)> = Vec::new();
            for &g in &pr.ecarts {
                // dessus / dessous : glissement en x de (corps gauche - largeur) a corps droit
                for sx in echantillons(b[0] - bw * 0.85, b[2] - bw * 0.15, pr.pas, 40, bcx - bw / 2.0) {
                    poses.push(([sx, b[3] + g], "haut"));
                    poses.push(([sx, b[1] - g - bh], "bas"));
                }
                for sy in echantillons(b[1] - bh * 0.85, b[3] - bh * 0.15, pr.pas, 40, bcy - bh / 2.0) {
                    poses.push(([b[0] - g - bw, sy], "gauche"));
                    poses.push(([b[2] + g, sy], "droite"));
                }
            }
            // repli : positions chevauchant son propre corps (entre ses pastilles, sur l'empreinte)
            for sx in echantillons(b[0] - bw, b[2], pr.pas * 1.5, 30, bcx - bw / 2.0) {
                for sy in echantillons(b[1] - bh, b[3], pr.pas * 1.5, 30, bcy - bh / 2.0) {
                    if boites_se_coupent(&[sx, sy, sx + bw, sy + bh], &b) {
                        poses.push(([sx, sy], "corps"));
                    }
                }
            }
            for (q, cote) in poses {
                let bb = [q[0], q[1], q[0] + bw, q[1] + bh];
                if let Some(cd) = evaluer(m, ic, &loc, bb, w, h, rot_deg, vertical, niv, miroir, cote, pr) {
                    out.push(cd);
                }
            }
        }
    }
    out.sort_by(|a, b| a.cout.partial_cmp(&b.cout).unwrap().then(a.bb[0].partial_cmp(&b.bb[0]).unwrap()).then(a.bb[1].partial_cmp(&b.bb[1]).unwrap()));
    // dedoublonnage (meme boite, meme taille)
    let mut res: Vec<Cand> = Vec::new();
    for cd in out {
        if res.iter().any(|r| r.niv == cd.niv && r.rot == cd.rot && (r.bb[0] - cd.bb[0]).abs() < 0.5 && (r.bb[1] - cd.bb[1]).abs() < 0.5) {
            continue;
        }
        res.push(cd);
        if res.len() >= pr.k_max {
            break;
        }
    }
    res
}

#[allow(clippy::too_many_arguments)]
fn evaluer(m: &Monde, ic: usize, loc: &Local, bb: [f64; 4], w: f64, h: f64, rot_deg: f64, vertical: bool, niv: usize,
           miroir: bool, cote: &'static str, pr: &Params) -> Option<Cand> {
    let c = &m.d.composants[ic];
    let fi = c.face.idx();
    // carte
    if !m.carte.accepte(&bb, pr.garde_bord) {
        return None;
    }
    // autres corps (meme face) : interdit ; le sien : penalise
    let mut dans_corps = false;
    for &j in &loc.corps {
        let o = &m.d.composants[j];
        if o.face != c.face {
            continue;
        }
        if boites_se_coupent(&bb, &o.corps) {
            if j == ic {
                dans_corps = true;
            } else {
                return None;
            }
        }
    }
    let forme = Forme::rect([bb[0] + w / 2.0, bb[1] + w / 2.0, bb[2] - w / 2.0, bb[3] - w / 2.0], w / 2.0);
    for &i in &loc.pastilles {
        let q = &m.d.pastilles[i];
        let s = if pr.masque { &q.masque } else { &q.cuivre };
        if plus_pres_que(&forme, s, pr.garde_pastille) {
            return None;
        }
    }
    for &i in &loc.traits {
        if plus_pres_que(&forme, &m.traits[fi][i], pr.garde_trait) {
            return None;
        }
    }
    for &i in &loc.textes {
        if plus_pres_que(&forme, &m.textes_fixes[fi][i], pr.garde_texte) {
            return None;
        }
    }
    let mut vias = 0u32;
    for &i in &loc.vias {
        if plus_pres_que(&forme, &m.vias[i], pr.garde_pastille) {
            vias += 1;
        }
    }
    // ambiguite : un autre corps (meme face) aussi proche que le sien
    let d_own = d_boites(&bb, &c.corps);
    let mut ambigu = false;
    for &j in &loc.corps {
        let o = &m.d.composants[j];
        if j != ic && o.face == c.face && d_boites(&bb, &o.corps) <= d_own + pr.marge_ambigu {
            ambigu = true;
            break;
        }
    }
    let (bcx, bcy) = ((c.corps[0] + c.corps[2]) / 2.0, (c.corps[1] + c.corps[3]) / 2.0);
    let (cx, cy) = ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0);
    let decentrage = match cote {
        "haut" | "bas" => (cx - bcx).abs(),
        "gauche" | "droite" => (cy - bcy).abs(),
        _ => ((cx - bcx).powi(2) + (cy - bcy).powi(2)).sqrt(),
    };
    let haut_corps = (c.corps[3] - c.corps[1]) > 1.3 * (c.corps[2] - c.corps[0]);
    let mut cout = d_own * MIL_MM + 0.3 * decentrage * MIL_MM + pr.cout_niveau * niv as f64;
    if vertical {
        cout += if haut_corps { pr.cout_vertical * 0.4 } else { pr.cout_vertical };
    }
    if dans_corps {
        cout += pr.cout_corps;
    }
    if ambigu {
        cout += pr.cout_ambigu;
    }
    cout += pr.cout_via * vias as f64;
    let ink = [bb[0] + w / 2.0, bb[1] + w / 2.0];
    let (x, y) = ancre_pour_boite(&c.nom, ink[0], ink[1], h, rot_deg, miroir);
    Some(Cand { x, y, rot: rot_deg, vertical, niv, h, w, bb, cout, ambigu, dans_corps, vias, cote })
}

// ------------------------------------------------------------------------------------------------
// Recuit simule
// ------------------------------------------------------------------------------------------------

pub struct Rng(u64);
impl Rng {
    pub fn new(s: u64) -> Rng {
        Rng(s ^ 0x9E37_79B9_7F4A_7C15)
    }
    pub fn next(&mut self) -> u64 {
        // splitmix64
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    pub fn unif(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
    pub fn idx(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
}

#[derive(Clone, Copy, Debug)]
pub enum Align {
    Ligne,
    Colonne,
}

pub struct Probleme<'a> {
    pub cands: &'a [Vec<Cand>],
    pub voisins: Vec<Vec<usize>>,
    pub align: Vec<Vec<(usize, Align)>>,
    pub pr: &'a Params,
}

impl<'a> Probleme<'a> {
    pub fn new(cands: &'a [Vec<Cand>], composants: &[Composant], pr: &'a Params) -> Probleme<'a> {
        let n = cands.len();
        let mut ub: Vec<Option<[f64; 4]>> = vec![None; n];
        for i in 0..n {
            for cd in &cands[i] {
                match &mut ub[i] {
                    None => ub[i] = Some(cd.bb),
                    Some(u) => union(u, &cd.bb),
                }
            }
        }
        let mut voisins = vec![Vec::new(); n];
        for i in 0..n {
            for j in 0..n {
                if i == j || composants[i].face != composants[j].face {
                    continue;
                }
                if let (Some(a), Some(b)) = (ub[i], ub[j]) {
                    if d_boites(&a, &b) < pr.garde_texte {
                        voisins[i].push(j);
                    }
                }
            }
        }
        // voisins alignes : meme face, corps de memes dimensions, centres alignes, proches
        let mut align = vec![Vec::new(); n];
        for i in 0..n {
            for j in 0..n {
                if i == j || composants[i].face != composants[j].face {
                    continue;
                }
                let (a, b) = (&composants[i].corps, &composants[j].corps);
                let (wa, ha, wb, hb) = (a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]);
                if (wa - wb).abs() > 0.1 * wa.max(wb) + 1.0 || (ha - hb).abs() > 0.1 * ha.max(hb) + 1.0 {
                    continue;
                }
                let (dx, dy) = (((a[0] + a[2]) - (b[0] + b[2])) / 2.0, ((a[1] + a[3]) - (b[1] + b[3])) / 2.0);
                let lim = 8.0 / MIL_MM;
                if dy.abs() < 2.0 && dx.abs() < lim {
                    align[i].push((j, Align::Ligne));
                } else if dx.abs() < 2.0 && dy.abs() < lim {
                    align[i].push((j, Align::Colonne));
                }
            }
        }
        Probleme { cands, voisins, align, pr }
    }

    fn cand(&self, i: usize, k: usize) -> Option<&Cand> {
        if k == MASQUE {
            None
        } else {
            Some(&self.cands[i][k])
        }
    }

    pub fn statique(&self, i: usize, k: usize) -> f64 {
        match self.cand(i, k) {
            None => self.pr.cout_masque,
            Some(c) => c.cout,
        }
    }

    pub fn conflit(&self, a: Option<&Cand>, b: Option<&Cand>) -> bool {
        match (a, b) {
            (Some(x), Some(y)) => d_boites(&x.bb, &y.bb) < self.pr.garde_texte,
            _ => false,
        }
    }

    fn desaligne(&self, a: Option<&Cand>, b: Option<&Cand>, t: Align) -> bool {
        match (a, b) {
            (Some(x), Some(y)) => {
                if x.rot != y.rot || x.niv != y.niv || x.cote != y.cote {
                    return true;
                }
                match t {
                    Align::Ligne => (x.cy() - y.cy()).abs() > 0.5,
                    Align::Colonne => (x.cx() - y.cx()).abs() > 0.5,
                }
            }
            _ => false,
        }
    }

    /// cout local de i (statique + interactions avec ses voisins)
    pub fn local(&self, s: &[usize], i: usize, k: usize) -> f64 {
        let a = self.cand(i, k);
        let mut c = self.statique(i, k);
        for &j in &self.voisins[i] {
            if self.conflit(a, self.cand(j, s[j])) {
                c += self.pr.cout_conflit;
            }
        }
        for &(j, t) in &self.align[i] {
            if self.desaligne(a, self.cand(j, s[j]), t) {
                c += self.pr.cout_align;
            }
        }
        c
    }

    pub fn total(&self, s: &[usize]) -> f64 {
        let mut c = 0.0;
        for i in 0..s.len() {
            c += self.statique(i, s[i]);
            for &j in &self.voisins[i] {
                if j > i && self.conflit(self.cand(i, s[i]), self.cand(j, s[j])) {
                    c += self.pr.cout_conflit;
                }
            }
            for &(j, t) in &self.align[i] {
                if j > i && self.desaligne(self.cand(i, s[i]), self.cand(j, s[j]), t) {
                    c += self.pr.cout_align;
                }
            }
        }
        c
    }

    pub fn depart(&self) -> Vec<usize> {
        (0..self.cands.len()).map(|i| if self.cands[i].is_empty() { MASQUE } else { 0 }).collect()
    }

    pub fn recuit(&self, graine: u64) -> (f64, Vec<usize>) {
        let mut rng = Rng::new(graine);
        let n = self.cands.len();
        let mut s = self.depart();
        let mut cout = self.total(&s);
        let mut best = (cout, s.clone());
        let (t0, t1) = (4.0f64, 0.01f64);
        let mv = self.pr.mouvements.max(1);
        for it in 0..mv {
            let t = t0 * (t1 / t0).powf(it as f64 / mv as f64);
            let i = rng.idx(n);
            let nc = self.cands[i].len();
            if nc == 0 {
                continue;
            }
            let k = if rng.unif() < 0.01 {
                MASQUE
            } else if rng.unif() < 0.6 {
                rng.idx(nc.min(20))
            } else {
                rng.idx(nc)
            };
            if k == s[i] {
                continue;
            }
            let d = self.local(&s, i, k) - self.local(&s, i, s[i]);
            if d <= 0.0 || rng.unif() < (-d / t).exp() {
                s[i] = k;
                cout += d;
                if cout < best.0 - 1e-9 {
                    best = (cout, s.clone());
                }
            }
        }
        let mut s = best.1;
        self.glouton(&mut s);
        self.ejections(&mut s);
        (self.total(&s), s)
    }

    /// descente : meilleur candidat de chaque composant, les autres fixes, jusqu'a stabilite
    pub fn glouton(&self, s: &mut [usize]) {
        for _ in 0..50 {
            let mut change = false;
            for i in 0..s.len() {
                let mut bk = s[i];
                let mut bc = self.local(s, i, s[i]);
                for k in (0..self.cands[i].len()).chain(std::iter::once(MASQUE)) {
                    let c = self.local(s, i, k);
                    if c < bc - 1e-9 {
                        bc = c;
                        bk = k;
                    }
                }
                if bk != s[i] {
                    s[i] = bk;
                    change = true;
                }
            }
            if !change {
                break;
            }
        }
    }

    /// reparation par chaines d'ejection : pour un designateur masque (ou couteux), essayer chaque candidat
    /// et reloger les (au plus 3) designateurs qu'il gene vers leur meilleure place libre.
    pub fn ejections(&self, s: &mut [usize]) -> usize {
        let mut gains = 0;
        for _ in 0..20 {
            let mut ameliore = false;
            let mut ordre: Vec<usize> = (0..s.len()).collect();
            ordre.sort_by(|&a, &b| self.local(s, b, s[b]).partial_cmp(&self.local(s, a, s[a])).unwrap().then(a.cmp(&b)));
            for &i in &ordre {
                if self.local(s, i, s[i]) < 3.0 {
                    continue;
                }
                let mut meilleur: Option<(f64, Vec<(usize, usize)>)> = None;
                for k in 0..self.cands[i].len() {
                    let a = Some(&self.cands[i][k]);
                    let genes: Vec<usize> =
                        self.voisins[i].iter().copied().filter(|&j| self.conflit(a, self.cand(j, s[j]))).collect();
                    if genes.len() > 3 {
                        continue;
                    }
                    let mut t = s.to_vec();
                    let mut delta = self.local(&t, i, k) - self.local(&t, i, t[i]);
                    t[i] = k;
                    let mut chg = vec![(i, k)];
                    for &j in &genes {
                        let mut bk = MASQUE;
                        let mut bc = f64::MAX;
                        for kk in (0..self.cands[j].len()).chain(std::iter::once(MASQUE)) {
                            let c = self.local(&t, j, kk);
                            if c < bc {
                                bc = c;
                                bk = kk;
                            }
                        }
                        delta += bc - self.local(&t, j, t[j]);
                        t[j] = bk;
                        chg.push((j, bk));
                    }
                    let c = delta;
                    if c < -1e-6 && meilleur.as_ref().map_or(true, |m| c < m.0 - 1e-9) {
                        meilleur = Some((c, chg));
                    }
                }
                if let Some((_, chg)) = meilleur {
                    for (j, k) in chg {
                        s[j] = k;
                    }
                    gains += 1;
                    ameliore = true;
                }
            }
            self.glouton(s);
            if !ameliore {
                break;
            }
        }
        gains
    }

    pub fn resoudre(&self) -> (f64, Vec<usize>, Vec<f64>) {
        let res: Vec<(f64, Vec<usize>)> =
            (0..self.pr.chaines).into_par_iter().map(|k| self.recuit(self.pr.graine * 1000 + k as u64)).collect();
        let couts: Vec<f64> = res.iter().map(|r| r.0).collect();
        let mut ib = 0;
        for (k, r) in res.iter().enumerate() {
            if r.0 < res[ib].0 - 1e-9 {
                ib = k;
            }
        }
        let (c, s) = res[ib].clone();
        (c, s, couts)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn echantillonnage() {
        let v = echantillons(0.0, 10.0, 2.5, 40, 5.0);
        assert!(v.contains(&0.0) && v.contains(&10.0) && v.contains(&5.0));
        assert_eq!(echantillons(3.0, 1.0, 1.0, 10, 0.0), vec![3.0]);
    }

    #[test]
    fn rng_deterministe() {
        let mut a = Rng::new(7);
        let mut b = Rng::new(7);
        for _ in 0..100 {
            assert_eq!(a.next(), b.next());
        }
    }
}
