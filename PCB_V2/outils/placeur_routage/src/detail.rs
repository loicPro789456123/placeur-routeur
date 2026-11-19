//! Routage detaille final sur le placement retenu.
//!
//! Grille fine 0,1 mm, 2 couches (TopLayer / BottomLayer), 8 directions (coudes a 45 degres ; 90 degres
//! penalises ; 135 degres = angle aigu interdit), A* avec croissance d'arbre par reseau, vias 0,6 / 0,3.
//!
//! Isolements : carte des zones interdites par objet de cuivre (pastille, piste, via), gonflee de
//! (isolement de l'objet + demi-largeur de reference 0,127 + marge 0,02). Chaque cellule garde
//! (nombre, somme des cles, somme des carres des cles) : elle est libre pour le reseau n si elle est vide
//! ou si TOUTES les contributions viennent de n (somme = c.n et somme des carres = c.n^2) ; on peut donc
//! ajouter / retirer exactement les objets. Pistes plus larges que 0,254 mm, isolement 0,25 (COMMUTATION) et
//! vias : test sur un disque de cellules de rayon (surlargeur + 2 x demi-diagonale), ce qui couvre l'erreur
//! d'echantillonnage.
//!
//! Largeur : chaque noeud est pris a la largeur preferee de la classe si elle passe, sinon a la largeur
//! de repli (mini de la classe, >= 0,254), avec un petit surcout. Pour CANAL_2A (mini 1,5 mm) le repli
//! (0,5 mm) n'est permis qu'a moins de 2 mm d'une pastille du reseau (retrecissement signale).
//!
//! Negociation (PathFinder) : les reseaux sont d'abord routes en se recouvrant si besoin (cout present +
//! historique par noeud), puis seuls les reseaux en conflit sont reroutes, avec un cout present croissant
//! et un historique qui s'accumule sur les noeuds disputes. Ensuite passe stricte (aucun recouvrement) pour
//! les reseaux encore en conflit, puis arrachage-reroutage cible.
//!
//! Bloc hacheur fige : son cuivre existant (cuivre_bloc_hacheur.json) est un obstacle pour les autres reseaux et
//! une cible pour le sien ; un reseau qui touche le bloc est relie a n'importe quel cuivre du bloc du meme reseau.
//! Couche dessus interdite sous le corps de L1 (regle du noeud SW).
use crate::geom::*;
use crate::globalr::{couche_mult, NCOUCHES};
use std::cmp::Ordering;
use std::collections::BinaryHeap;

pub const P: f64 = 0.1;
pub const H_DEF: f64 = 0.127;
pub const DELTA: f64 = 0.02;
/// marge ajoutee a tous les isolements (iteration 5 : un ecart de 0,199 mm a ete refuse par Altium)
pub const MARGE_ISOL: f64 = 0.01;
/// ecart mini entre bords de trous (regle Altium Hole To Hole Clearance)
pub const TROU_TROU: f64 = 0.254;
/// trou a trou : 0,254 mm entre parois de percage, + 0,05 de marge, trous de 0,3 mm -> entraxe mini
pub const ENTRAXE_VIAS: f64 = 0.254 + 0.05 + 0.3;
const DIAG: f64 = 0.0711;
const NONE: u32 = u32::MAX;
const VIA_D: f32 = 1.5;
pub const R_VIA_PLAN: f64 = 6.0;
const RIPS_MAX: u32 = 6;
const HINC: f32 = 0.5;
const DIRS: [(i32, i32); 8] = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)];
const TURN: [f32; 5] = [0.0, 0.02, 0.4, 1e6, 1e9];

#[derive(Clone, Copy)]
struct HI {
    f: f32,
    n: u32,
}
impl PartialEq for HI {
    fn eq(&self, o: &Self) -> bool {
        self.f == o.f && self.n == o.n
    }
}
impl Eq for HI {}
impl Ord for HI {
    fn cmp(&self, o: &Self) -> Ordering {
        o.f.partial_cmp(&self.f).unwrap_or(Ordering::Equal).then_with(|| o.n.cmp(&self.n))
    }
}
impl PartialOrd for HI {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}

#[derive(Clone, Debug)]
pub struct SegD {
    pub x1: f64,
    pub y1: f64,
    pub x2: f64,
    pub y2: f64,
    pub w: f64,
    pub couche: u8,
}

#[derive(Clone, Debug)]
pub struct ViaD {
    pub x: f64,
    pub y: f64,
}

#[derive(Default, Clone)]
pub struct NetR {
    pub segs: Vec<SegD>,
    pub vias: Vec<ViaD>,
    kt: Vec<u32>,
    kprox: Vec<(u32, u8)>,
    /// noeuds d'axe poses (bits 2.. : indice de largeur, bit 1 : via a cet endroit)
    noeuds: Vec<(u32, u8)>,
    pub ok: usize,
    pub ko: usize,
    pub largeur_reduite: usize,
    pub neck_mm: f64,
    pub derog_mm: f64,
    pub carres: f64,
    pub vias_doubles: usize,
    pub diag: Vec<String>,
}

#[derive(Clone, Copy, PartialEq)]
enum Mode {
    Strict,
    Negocie,
    Sonde,
}

#[derive(Clone)]
struct KMap {
    cnt: Vec<u16>,
    s1: Vec<u32>,
    s2: Vec<u64>,
}

impl KMap {
    fn new(n: usize) -> KMap {
        KMap { cnt: vec![0; n], s1: vec![0; n], s2: vec![0; n] }
    }
    #[inline]
    fn add(&mut self, n: usize, k: u32) {
        self.cnt[n] += 1;
        self.s1[n] += k;
        self.s2[n] += (k as u64) * (k as u64);
    }
    #[inline]
    fn sub(&mut self, n: usize, k: u32) {
        self.cnt[n] -= 1;
        self.s1[n] -= k;
        self.s2[n] -= (k as u64) * (k as u64);
    }
    #[inline]
    fn ok(&self, n: usize, k: u32) -> bool {
        let c = self.cnt[n] as u32;
        c == 0 || (self.s1[n] == c * k && self.s2[n] == (c as u64) * (k as u64) * (k as u64))
    }
    #[inline]
    fn owner(&self, n: usize) -> Option<u32> {
        let c = self.cnt[n] as u32;
        if c == 0 || self.s1[n] % c != 0 {
            return None;
        }
        let k = self.s1[n] / c;
        if self.s2[n] == (c as u64) * (k as u64) * (k as u64) {
            Some(k)
        } else {
            None
        }
    }
}

enum But {
    Arbre([usize; 4]),
    Via(usize, usize, usize),
}

/// Largeurs admises d'un reseau, de la preferee a la plus etroite (bornes min / preferee de la classe).
#[derive(Clone)]
pub struct Larg {
    pub w: Vec<f64>,
    /// largeur mini de la regle (pour CANAL_2A, 1,5 : en dessous = derogation signalee)
    pub mini_regle: f64,
}

/// Contexte d'une recherche.
struct Ctx {
    key: u32,
    w: Vec<f64>,
    offs: Vec<Vec<(i32, i32)>>,
    voffs: Vec<(i32, i32)>,
    mode: Mode,
    via_cout: f32,
    prox: f32,
    coude: f32,
    /// multiplicateur de cout par couche (sensibilite / preference de couche)
    mult: [f32; 3],
}

/// Parametres de la passe d'espacement.
#[derive(Clone)]
pub struct ParamEsp {
    pub actif: bool,
    pub prox: f64,
    pub coude: f64,
    pub allong_max: f64,
}

#[derive(Clone)]
pub struct Detail<'a> {
    pub m: &'a Modele,
    pub e: Etat,
    pub nx: usize,
    pub ny: usize,
    nc: usize,
    hard: Vec<bool>,
    zone: Vec<bool>,
    novia: Vec<u16>,
    kp: KMap,
    kt: KMap,
    kt_who: Vec<u32>,
    k2: Option<KMap>,
    k3: Option<KMap>,
    hist: Vec<f32>,
    pres: f32,
    gc: Vec<f32>,
    par: Vec<u32>,
    seen: Vec<u32>,
    din: Vec<u8>,
    ep: u32,
    tree: Vec<u32>,
    tep: u32,
    legm: Vec<u32>,
    /// cellules cibles sur l'axe d'une piste existante du bloc : arrivee par une piste de la meme couche seulement
    /// (un via pose au milieu d'une piste n'a pas son centre sur une extremite de piste)
    planaire: Vec<bool>,
    /// nombre de centres de via par cellule (regle trou a trou, y compris meme reseau)
    vias_c: Vec<u16>,
    offs_trou: Vec<(i32, i32)>,
    /// cache exact du test de via (avenant 2.2, A.4-1) : nombre de centres de via dans le disque trou a trou autour de
    /// chaque cellule, tenu a jour a la pose, au retrait et a la restauration d'un instantane
    trou_cnt: Vec<u32>,
    /// cache actif (faux avec la construction `sans_cache_via` ; modifiable par les tests de l'identite des sorties)
    pub cache_via: bool,
    /// memoire du test de via par recherche : estampille de recherche et valeur
    vsm: Vec<u32>,
    vsv: Vec<u8>,
    legv: Vec<u8>,
    /// via pose en recouvrement depuis ce noeud (estampille de recherche)
    vsoft: Vec<u32>,
    heap: BinaryHeap<HI>,
    pub nets: Vec<NetR>,
    pub rips: Vec<u32>,
    pub max_exp: usize,
    /// dernier recours autorise : via dans une pastille CMS d'un reseau de plan (GND)
    pub via_dans_pad: bool,
    /// derogations effectivement utilisees (texte)
    pub derogations: Vec<String>,
    /// amorces de sortie dans l'axe (hors grille) des pastilles a pas fin du bloc : (reseau, segments)
    pub amorces: Vec<(usize, Vec<SegD>)>,
    /// broches ordinaires amorcees (pastilles inaccessibles depuis la grille, convertisseur libere) : noeud de raccord
    pub pin_extra: std::collections::BTreeMap<(usize, usize), u32>,
    pub pieces_pont: Vec<usize>,
    /// finition ciblee : ne jamais arracher les reseaux de plan (GND) comme reseaux genants
    pub proteger_plans: bool,
    nonet: u32,
    pub emprise: Rect,
    /// cellules du cuivre du bloc (pastilles, pistes, vias, surfaces) de chaque reseau : cible de raccordement
    pub cibles_bloc: Vec<Vec<(Vec<u32>, String)>>,
    /// fenetre de la recherche courante (cellules) : boite des broches + FENETRE
    fen: [i32; 4],
    pub fenetre_mm: f64,
    /// mode rapide (optimisation conjointe) : pas de repli sur fenetre elargie
    pub rapide: bool,
}

fn dist_rect(x: f64, y: f64, r: &Rect) -> f64 {
    let dx = (r[0] - x).max(0.0).max(x - r[2]);
    let dy = (r[1] - y).max(0.0).max(y - r[3]);
    (dx * dx + dy * dy).sqrt()
}

fn dist_seg(px: f64, py: f64, s: &SegD) -> f64 {
    let (dx, dy) = (s.x2 - s.x1, s.y2 - s.y1);
    let l2 = dx * dx + dy * dy;
    let t = if l2 > 0.0 { (((px - s.x1) * dx + (py - s.y1) * dy) / l2).clamp(0.0, 1.0) } else { 0.0 };
    let (qx, qy) = (s.x1 + t * dx, s.y1 + t * dy);
    ((px - qx).powi(2) + (py - qy).powi(2)).sqrt()
}

/// Disque de cellules a tester pour une surlargeur r (0 = centre seul).
pub fn offsets(r: f64) -> Vec<(i32, i32)> {
    if r <= 1e-9 {
        return vec![(0, 0)];
    }
    // un point a distance r du centre est a moins de DIAG d'un centre de cellule : on teste jusqu'a r + 2 DIAG
    let rr = r + 2.0 * DIAG;
    let k = (rr / P).ceil() as i32;
    let mut v = vec![(0, 0)];
    for dy in -k..=k {
        for dx in -k..=k {
            if (dx, dy) != (0, 0) && ((dx * dx + dy * dy) as f64).sqrt() * P <= rr {
                v.push((dx, dy));
            }
        }
    }
    v
}

impl<'a> Detail<'a> {
    pub fn new(m: &'a Modele, e: &Etat) -> Detail<'a> {
        let nx = (m.w / P - 1e-3).ceil() as usize; // tolerance : contour lu en mils (164,0000022 mm)
        let ny = (m.h / P - 1e-3).ceil() as usize;
        let nc = nx * ny;
        let nn = NCOUCHES * nc;
        let mut d = Detail {
            m,
            e: e.clone(),
            nx,
            ny,
            nc,
            hard: vec![false; nc],
            zone: vec![false; nc],
            novia: vec![0; nc],
            kp: KMap::new(nn),
            kt: KMap::new(nn),
            kt_who: vec![0; nn],
            k2: None,
            k3: None,
            hist: vec![0.0; nn],
            pres: 1.0,
            gc: vec![0.0; nn],
            par: vec![NONE; nn],
            seen: vec![0; nn],
            din: vec![8; nn],
            ep: 0,
            tree: vec![0; nn],
            tep: 0,
            legm: vec![0; nn],
            planaire: vec![false; nn],
            vias_c: vec![0; nc],
            offs_trou: Vec::new(),
            trou_cnt: vec![0; nc],
            cache_via: !cfg!(feature = "sans_cache_via"),
            vsm: vec![0; nc],
            vsv: vec![0; nc],
            legv: vec![0; nn],
            vsoft: vec![0; nn],
            heap: BinaryHeap::new(),
            nets: vec![NetR::default(); m.nets.len()],
            rips: vec![0; m.nets.len()],
            max_exp: 3_000_000,
            via_dans_pad: false,
            derogations: Vec::new(),
            amorces: Vec::new(),
            pin_extra: std::collections::BTreeMap::new(),
            pieces_pont: Vec::new(),
            proteger_plans: false,
            nonet: m.nets.len() as u32 + 1,
            emprise: m.emprise(e),
            cibles_bloc: vec![Vec::new(); m.nets.len()],
            fen: [0, 0, i32::MAX, i32::MAX],
            fenetre_mm: 15.0,
            rapide: false,
        };
        let g = m.regles.garde_bord_fentes_trous + H_DEF + DELTA;
        // pastilles libres des trous M3 (cuivre reel, toutes couches) : isolement 0,2 mm
        let gt = m.regles.isolement.defaut + H_DEF + DELTA;
        // corps de L1 : pas de piste dessus sous L1 (noeud SW)
        for iy in 0..ny {
            for ix in 0..nx {
                let (x, y) = d.cxy(ix, iy);
                let mut h = x < g || y < g || x > m.w - g || y > m.h - g;
                // contour polygonal de la carte (donnees) : garde au bord sur tout le contour
                if !h && m.contour.len() >= 3 && dist_poly(x, y, &m.contour) > -g {
                    h = true;
                }
                for f in &m.fentes {
                    if dist_rect(x, y, &[f[0], f[1], f[0] + f[2], f[1] + f[3]]) < g {
                        h = true;
                    }
                }
                for (t, rt) in m.trous.iter().zip(m.trous_r.iter()) {
                    if ((x - t[0]).powi(2) + (y - t[1]).powi(2)).sqrt() < rt + gt {
                        h = true;
                    }
                }
                let c = iy * nx + ix;
                d.hard[c] = h;
            }
        }
        d.poser_zone_l1(true);
        for i in 0..m.comps.len() {
            d.raster_pads_piece(i, true);
        }
        // cuivre existant du bloc : obstacle (avec son isolement) ; cellules cibles par element
        let mut cible_el: Vec<Vec<u32>> = Vec::new();
        for cu in m.cuivre_bloc.clone() {
            let key = cu.net.map(|n| n as u32 + 1).unwrap_or(d.nonet);
            let clr = cu.net.map(|n| d.clr_net(n)).unwrap_or(m.regles.isolement.defaut);
            let infl = clr + H_DEF + DELTA;
            let mut cible = Vec::new();
            match cu.obj {
                CuivreObj::Seg { x1, y1, x2, y2, w, couche } => {
                    let sg = SegD { x1, y1, x2, y2, w, couche };
                    let l = couche as usize;
                    let r = infl + w / 2.0;
                    let x0 = (((x1.min(x2) - r) / P).floor().max(0.0)) as usize;
                    let y0 = (((y1.min(y2) - r) / P).floor().max(0.0)) as usize;
                    let xa = ((((x1.max(x2) + r) / P).ceil()) as usize).min(nx - 1);
                    let ya = ((((y1.max(y2) + r) / P).ceil()) as usize).min(ny - 1);
                    for iy in y0..=ya {
                        for ix in x0..=xa {
                            let (x, y) = d.cxy(ix, iy);
                            let dd = dist_seg(x, y, &sg);
                            let nd = l * nc + iy * nx + ix;
                            if dd < r {
                                d.kp.add(nd, key);
                            }
                            // extremite d'axe a l'interieur du cuivre (marge 0,03 mm)
                            if dd <= w / 2.0 - 0.03 {
                                cible.push(nd as u32);
                                d.planaire[nd] = true;
                            }
                        }
                    }
                }
                CuivreObj::Via { x, y, d: dv } => {
                    let r = [x - dv / 2.0, y - dv / 2.0, x + dv / 2.0, y + dv / 2.0];
                    for l in 0..NCOUCHES {
                        d.raster_rect(l, &r, infl, key);
                        cible.push(d.node_at(x, y, l as u8));
                    }
                    let rv = m.regles.via.diametre / 2.0 + dv / 2.0 + 0.01;
                    d.each_cell_near_rect(&[x, y, x, y], rv, |dd, c| dd.novia[c] += 1);
                }
                CuivreObj::Region { couche, r } => {
                    // surface connue par son rectangle englobant : obstacle, jamais cible (cuivre reel incertain)
                    let l = couche as usize;
                    d.raster_rect(l, &r, infl, key);
                }
            }
            cible_el.push(cible);
        }
        // un groupe de cuivre du bloc (contact reel) = une broche a relier
        for g in &m.groupes_bloc {
            let n = g.net;
            if m.nets[n].plan || g.pads.is_empty() {
                continue;
            }
            let mut cells = Vec::new();
            let mut desc = Vec::new();
            for &(i, k) in &g.pads {
                cells.extend(d.pad_cells(i, k));
                desc.push(format!("{}.{}", m.comps[i].nom, m.comps[i].pads[k].nom));
            }
            for &q in &g.cuivre {
                cells.extend(cible_el[q].iter().copied());
            }
            cells.sort();
            cells.dedup();
            d.cibles_bloc[n].push((cells, desc.join(" ")));
        }
        // trous existants (vias du bloc) pour la regle trou a trou
        d.offs_trou = {
            let k = (ENTRAXE_VIAS / P).ceil() as i32;
            let mut v = Vec::new();
            for dy in -k..=k {
                for dx in -k..=k {
                    if ((dx * dx + dy * dy) as f64).sqrt() * P < ENTRAXE_VIAS + DIAG {
                        v.push((dx, dy));
                    }
                }
            }
            v
        };
        for cu in &m.cuivre_bloc {
            if let CuivreObj::Via { x, y, .. } = cu.obj {
                let c = d.node_at(x, y, 0) as usize;
                d.vias_c[c] += 1;
                d.maj_trou(c, 1);
            }
        }
        d.poser_amorces();
        d.poser_ponts();
        d.amorcer_broches();
        d
    }

    /// Pose (ajout) ou retire les zones d'isolement et d'interdiction de via des pastilles d'une piece a sa pose courante.
    pub fn raster_pads_piece(&mut self, i: usize, ajout: bool) {
        let m = self.m;
        let nc = self.nc;
        let p = self.e.poses[i];
        for k in 0..m.comps[i].pads.len() {
            let (cx, cy, hw, hh) = m.pad_world(i, k, &p);
            let pd = &m.comps[i].pads[k];
            let key = pd.net.map(|n| n as u32 + 1).unwrap_or(self.nonet);
            let clr = pd.net.map(|n| self.clr_net(n)).unwrap_or(m.regles.isolement.defaut + MARGE_ISOL);
            let layers: Vec<usize> = if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] };
            // regle Altium : 0,124 mm seulement entre objets de M1 / M2 / U10 eux-memes ; une piste contre leurs
            // pastilles garde 0,2 mm (0,25 COMMUTATION), meme a l'interieur de leur champ de pastilles
            let cells = self.cells_pad(cx, cy, hw, hh, pd.forme, clr + H_DEF + DELTA, f64::MAX);
            for &l in &layers {
                for &c in &cells {
                    if ajout {
                        self.kp.add(l * nc + c, key);
                    } else {
                        self.kp.sub(l * nc + c, key);
                    }
                }
            }
            // regle Altium « Hole To Hole Clearance » (0,254 mm) : pas de via dont le trou approche celui d'une
            // pastille traversante (via dans la pastille compris)
            if pd.th {
                let tr = if pd.trou > 0.0 { pd.trou } else { (2.0 * hw.min(hh) - 0.3).max(0.3) };
                let r = tr / 2.0 + m.regles.via.trou / 2.0 + TROU_TROU + 0.01;
                for c in self.cells_pad(cx, cy, 0.0, 0.0, FormePad::Rond, r, f64::MAX) {
                    if ajout {
                        self.novia[c] += 1;
                    } else {
                        self.novia[c] -= 1;
                    }
                }
            }
            if !pd.th && !m.comps[i].via_sous_pad {
                let rv = m.regles.via.diametre / 2.0 + 0.01;
                for c in self.cells_pad(cx, cy, hw, hh, pd.forme, rv, f64::MAX) {
                    if ajout {
                        self.novia[c] += 1;
                    } else {
                        self.novia[c] -= 1;
                    }
                }
            }
        }
            // regions de cuivre d'empreinte : meme isolement que les pastilles (cle de leur reseau, sinon « sans reseau »)
        let regs = m.comps[i].regions.clone();
        for r in &regs {
            let (pts, ls) = m.region_monde(i, r, &p);
            if pts.len() < 3 {
                continue;
            }
            let key = r.1.map(|n| n as u32 + 1).unwrap_or(self.nonet);
            let clr = r.1.map(|n| self.clr_net(n)).unwrap_or(m.regles.isolement.defaut + MARGE_ISOL);
            let infl = clr + H_DEF + DELTA;
            let bx0 = pts.iter().map(|q| q.0).fold(f64::MAX, f64::min) - infl;
            let by0 = pts.iter().map(|q| q.1).fold(f64::MAX, f64::min) - infl;
            let bx1 = pts.iter().map(|q| q.0).fold(f64::MIN, f64::max) + infl;
            let by1 = pts.iter().map(|q| q.1).fold(f64::MIN, f64::max) + infl;
            let (ix0, iy0) = (((bx0 / P).floor().max(0.0)) as usize, ((by0 / P).floor().max(0.0)) as usize);
            let (ix1, iy1) = (((bx1 / P).ceil() as usize).min(self.nx - 1), ((by1 / P).ceil() as usize).min(self.ny - 1));
            for iy in iy0..=iy1 {
                for ix in ix0..=ix1 {
                    let (x, y) = self.cxy(ix, iy);
                    if dist_poly(x, y, &pts) < infl {
                        let c = iy * self.nx + ix;
                        for &l in &ls {
                            if ajout {
                                self.kp.add(l * nc + c, key);
                            } else {
                                self.kp.sub(l * nc + c, key);
                            }
                        }
                    }
                }
            }
        }
    }

    /// Rectangle englobant des pastilles d'une piece (champ de pastilles) a sa pose courante.
    #[allow(dead_code)]
    pub fn champ_pastilles(&self, i: usize) -> Rect {
        let p = self.e.poses[i];
        let mut r = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
        for k in 0..self.m.comps[i].pads.len() {
            let (x, y, hw, hh) = self.m.pad_world(i, k, &p);
            r = [r[0].min(x - hw), r[1].min(y - hh), r[2].max(x + hw), r[3].max(y + hh)];
        }
        r
    }

    /// Deplace une piece (pastilles re-tramees ; le routage n'est pas touche).
    pub fn deplacer(&mut self, i: usize, p: Pose) {
        let est_l1 = self.m.cfg.sans_piste_dessous.iter().any(|nom| self.m.comp_idx.get(nom) == Some(&i));
        if est_l1 {
            self.poser_zone_piece(i, false);
        }
        self.raster_pads_piece(i, false);
        self.e.poses[i] = p;
        self.raster_pads_piece(i, true);
        if est_l1 {
            self.poser_zone_piece(i, true);
        }
    }

    /// Corps de L1 interdit aux pistes de la couche du dessus (noeud SW sous la self), SAUF sur ses pastilles et a
    /// leur bord immediat (sinon les broches de L1 sont inaccessibles des que le bloc n'apporte plus son cuivre).
    fn poser_zone_l1(&mut self, poser: bool) {
        let m = self.m;
        let noms: Vec<String> = m.cfg.sans_piste_dessous.clone();
        for nom in noms {
            if let Some(&i) = m.comp_idx.get(&nom) {
                self.poser_zone_piece(i, poser);
            }
        }
    }

    fn poser_zone_piece(&mut self, i: usize, poser: bool) {
        let m = self.m;
        let p = self.e.poses[i];
        let b = m.bbox(i, &p);
        let pads: Vec<(f64, f64, f64, f64, FormePad)> = (0..m.comps[i].pads.len()).map(|k| {
            let (x, y, hw, hh) = m.pad_world(i, k, &p);
            (x, y, hw, hh, m.comps[i].pads[k].forme)
        }).collect();
        let (ix0, iy0) = (((b[0] / P).floor().max(0.0)) as usize, ((b[1] / P).floor().max(0.0)) as usize);
        let (ix1, iy1) = (((b[2] / P).ceil() as usize).min(self.nx - 1), ((b[3] / P).ceil() as usize).min(self.ny - 1));
        for iy in iy0..=iy1 {
            for ix in ix0..=ix1 {
                let (x, y) = self.cxy(ix, iy);
                let c = iy * self.nx + ix;
                if !poser {
                    self.zone[c] = false;
                    continue;
                }
                let dans = dist_rect(x, y, &b) < 1e-9;
                let pres_pad = pads.iter().any(|q| dist_pad(x, y, q.0, q.1, q.2, q.3, q.4) < 0.3);
                self.zone[c] = dans && !pres_pad;
            }
        }
    }

    /// Reseaux dont le cuivre route recouvre la zone d'isolement des pastilles de la piece (a sa pose courante).
    pub fn reseaux_genes(&self, i: usize) -> Vec<usize> {
        let m = self.m;
        let p = self.e.poses[i];
        let mut out = Vec::new();
        for k in 0..m.comps[i].pads.len() {
            let (cx, cy, hw, hh) = m.pad_world(i, k, &p);
            let pd = &m.comps[i].pads[k];
            let key = pd.net.map(|n| n as u32 + 1).unwrap_or(self.nonet);
            let clr = m.regles.isolement.defaut + MARGE_ISOL;
            let layers: Vec<usize> = if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] };
            // isolement de la pastille + demi-largeur de reference : un axe de piste etrangere dans cette zone gene
            for c in self.cells_pad(cx, cy, hw, hh, pd.forme, clr + H_DEF + DELTA + 0.3, f64::MAX) {
                for &l in &layers {
                    let nd = l * self.nc + c;
                    if !self.kt.ok(nd, key) {
                        let o = self.kt.owner(nd).unwrap_or(self.kt_who[nd]);
                        if o != 0 && o != key && o != self.nonet {
                            let on = (o - 1) as usize;
                            if !out.contains(&on) {
                                out.push(on);
                            }
                        }
                    }
                }
            }
            // un via etranger dans la zone d'interdiction de via (pastille CMS)
        }
        out
    }

    /// Arrache puis reroute (mode strict) des reseaux, avec une reparation par arrachage cible limitee.
    /// Renvoie (succes, anciens etats des reseaux touches) pour pouvoir annuler exactement.
    pub fn rerouter_opt(&mut self, nets: &[usize], reparer: bool) -> (bool, Vec<(usize, NetR)>) {
        let mut olds: Vec<(usize, NetR)> = Vec::new();
        let mut liste: Vec<usize> = nets.iter().copied().filter(|&n| self.est_routable(n)).collect();
        liste.sort_by_key(|&n| (self.priorite(n), n));
        liste.dedup();
        for &n in &liste {
            olds.push((n, self.nets[n].clone()));
            self.arracher(n);
        }
        for &n in &liste {
            self.router(n, Mode::Strict);
        }
        for _ in 0..(if reparer { 2 } else { 0 }) {
            let echecs: Vec<usize> = olds.iter().map(|x| x.0).filter(|&n| self.nets[n].ko > 0).collect();
            if echecs.is_empty() {
                break;
            }
            for n in echecs {
                self.arracher(n);
                let plan = self.m.nets[n].plan;
                let mut gen = if plan { self.router_plan(n, Mode::Sonde) } else { self.router_net(n, Mode::Sonde) };
                gen.retain(|&o| o != n && self.est_routable(o) && !(self.proteger_plans && self.m.nets[o].plan));
                for &o in &gen {
                    if !olds.iter().any(|x| x.0 == o) {
                        olds.push((o, self.nets[o].clone()));
                    }
                    self.arracher(o);
                }
                self.router(n, Mode::Strict);
                for &o in &gen {
                    self.router(o, Mode::Strict);
                }
            }
        }
        let ok = olds.iter().all(|x| self.nets[x.0].ko == 0);
        (ok, olds)
    }

    /// Descentes d'un reseau de plan regroupees par contact exact (extremites de pistes et centres de vias communs),
    /// avec les pastilles CMS qu'elles desservent : (pastilles, indices de pistes, indices de vias).
    pub fn composantes_plan(&self, g: usize) -> Vec<(Vec<(usize, usize)>, Vec<usize>, Vec<usize>)> {
        let r = &self.nets[g];
        let (ns, nv) = (r.segs.len(), r.vias.len());
        let mut par: Vec<usize> = (0..ns + nv).collect();
        fn f(par: &mut Vec<usize>, x: usize) -> usize {
            let mut x = x;
            while par[x] != x {
                par[x] = par[par[x]];
                x = par[x];
            }
            x
        }
        let cle = |x: f64, y: f64| ((x * 1e5).round() as i64, (y * 1e5).round() as i64);
        let mut pts: std::collections::BTreeMap<(i64, i64), Vec<usize>> = std::collections::BTreeMap::new();
        for (k, sg) in r.segs.iter().enumerate() {
            pts.entry(cle(sg.x1, sg.y1)).or_default().push(k);
            pts.entry(cle(sg.x2, sg.y2)).or_default().push(k);
        }
        for (k, v) in r.vias.iter().enumerate() {
            pts.entry(cle(v.x, v.y)).or_default().push(ns + k);
        }
        for l in pts.values() {
            for w in l.windows(2) {
                let (a, b) = (f(&mut par, w[0]), f(&mut par, w[1]));
                if a != b {
                    par[a] = b;
                }
            }
        }
        let mut groupes: std::collections::BTreeMap<usize, (Vec<(usize, usize)>, Vec<usize>, Vec<usize>)> = std::collections::BTreeMap::new();
        for k in 0..ns {
            let rt = f(&mut par, k);
            groupes.entry(rt).or_default().1.push(k);
        }
        for k in 0..nv {
            let rt = f(&mut par, ns + k);
            groupes.entry(rt).or_default().2.push(k);
        }
        let net = &self.m.nets[g];
        for &(i, k) in net.pins_ext.iter().chain(net.pins_plan_bloc.iter()) {
            if self.m.comps[i].pads[k].th {
                continue;
            }
            let p = self.e.poses[i];
            let (cx, cy, hw, hh) = self.m.pad_world(i, k, &p);
            let fo = self.m.comps[i].pads[k].forme;
            for (_, gr) in groupes.iter_mut() {
                let touche = gr.1.iter().any(|&q| {
                    let sg = &r.segs[q];
                    dist_pad(sg.x1, sg.y1, cx, cy, hw, hh, fo) <= 1e-6 || dist_pad(sg.x2, sg.y2, cx, cy, hw, hh, fo) <= 1e-6
                }) || gr.2.iter().any(|&q| dist_pad(r.vias[q].x, r.vias[q].y, cx, cy, hw, hh, fo) <= 1e-6);
                if touche {
                    gr.0.push((i, k));
                }
            }
        }
        groupes.into_values().collect()
    }

    /// Reroutage partiel d'un reseau de plan : retire les descentes des pastilles donnees (et celles qui passent a
    /// moins de `rayon` des points donnes), garde les autres, reroute les pastilles concernees. Renvoie (succes, ancien).
    pub fn rerouter_plan_partiel(&mut self, g: usize, pads: &[(usize, usize)], zones: &[(f64, f64, f64)]) -> (bool, NetR) {
        let old = self.nets[g].clone();
        let comps = self.composantes_plan(g);
        let mut a_router: Vec<(usize, usize)> = pads.to_vec();
        let (mut gs, mut gv) = (Vec::new(), Vec::new());
        for (pp, ss, vv) in comps {
            let pres = |x: f64, y: f64| zones.iter().any(|&(zx, zy, r)| ((x - zx).powi(2) + (y - zy).powi(2)).sqrt() < r);
            let gene = ss.iter().any(|&q| {
                let sg = &old.segs[q];
                pres(sg.x1, sg.y1) || pres(sg.x2, sg.y2) || pres((sg.x1 + sg.x2) / 2.0, (sg.y1 + sg.y2) / 2.0)
            }) || vv.iter().any(|&q| pres(old.vias[q].x, old.vias[q].y));
            let vise = pp.iter().any(|x| pads.contains(x));
            if vise || gene {
                for x in pp {
                    if !a_router.contains(&x) {
                        a_router.push(x);
                    }
                }
            } else {
                gs.extend(ss.iter().map(|&q| old.segs[q].clone()));
                gv.extend(vv.iter().map(|&q| old.vias[q].clone()));
            }
        }
        self.arracher(g);
        let mut base = old.clone();
        base.segs.clear();
        base.vias.clear();
        base.noeuds.clear();
        // listes de tramage : deja retirees par arracher ; les garder ferait retirer deux fois ces cellules au
        // prochain arrachage (cuivre GND invisible des cartes -> pistes d'autres reseaux posees dessus)
        base.kt.clear();
        base.kprox.clear();
        base.carres = 0.0;
        base.ok = 0;
        // avenant 2.2, ecart (6) : compte d'echecs exact ; les echecs des pastilles qui ne sont pas rerouees (pastille
        // CMS deja sans descente, hors des zones) restent comptes (9b5b5a3 remettait le compte a 0)
        let m = self.m;
        let gardes: Vec<String> = old
            .diag
            .iter()
            .filter(|dg| !a_router.iter().any(|&(i, k)| dg.starts_with(&format!("{}.{} : ", m.comps[i].nom, m.comps[i].pads[k].nom))))
            .cloned()
            .collect();
        base.ko = gardes.len();
        base.diag = gardes;
        self.nets[g] = base;
        self.commit_geom(g, gs, gv, Vec::new());
        a_router.retain(|&(i, k)| !self.m.comps[i].pads[k].th);
        self.router_plan_pins(g, Mode::Strict, a_router);
        let ok = self.nets[g].ko == 0;
        (ok, old)
    }

    /// Annule un reroutage : remet exactement les anciens etats des reseaux.
    pub fn annuler(&mut self, olds: Vec<(usize, NetR)>) {
        for (n, old) in olds.into_iter().rev() {
            self.arracher(n);
            self.restaurer(n, old);
        }
    }

    /// Charge un routage existant (format de sortie : mils, couches TopLayer / MidLayer2 / BottomLayer) comme etat
    /// courant : pistes et vias sont tramees dans les cartes d'isolement ; les connexions sont supposees valides
    /// (controle independant prealable par drc_maison.py).
    pub fn charger_routage_json(&mut self, chemin: &str) -> Result<usize, String> {
        let txt = std::fs::read_to_string(chemin).map_err(|e| format!("lecture {} : {}", chemin, e))?;
        let v: serde_json::Value = serde_json::from_str(&txt).map_err(|e| format!("JSON {} : {}", chemin, e))?;
        let idx: std::collections::HashMap<String, usize> = self.m.nets.iter().enumerate().map(|(i, n)| (n.nom.clone(), i)).collect();
        let mut segs: Vec<Vec<SegD>> = vec![Vec::new(); self.m.nets.len()];
        let mut vias: Vec<Vec<ViaD>> = vec![Vec::new(); self.m.nets.len()];
        let mm = |x: &serde_json::Value| x.as_f64().unwrap_or(0.0) * MIL;
        for p in v["pistes"].as_array().cloned().unwrap_or_default() {
            let n = *idx.get(p["net_name"].as_str().unwrap_or("")).ok_or(format!("reseau inconnu {}", p["net_name"]))?;
            let couche = match p["layer"].as_str() {
                Some("TopLayer") => 0u8,
                Some("BottomLayer") => 1,
                _ => 2,
            };
            // largeur : valeur de classe la plus proche (les largeurs ecrites sont arrondies vers le haut au 1/1000 mil)
            let w = mm(&p["width"]);
            let lg = self.larg(n);
            let wq = lg.w.iter().copied().find(|c| (c - w).abs() < 0.001).unwrap_or(w);
            segs[n].push(SegD { x1: mm(&p["x1"]), y1: mm(&p["y1"]), x2: mm(&p["x2"]), y2: mm(&p["y2"]), w: wq, couche });
        }
        for q in v["vias"].as_array().cloned().unwrap_or_default() {
            let n = *idx.get(q["net"].as_str().unwrap_or("")).ok_or(format!("reseau inconnu {}", q["net"]))?;
            vias[n].push(ViaD { x: mm(&q["x"]), y: mm(&q["y"]) });
        }
        // les amorces eventuelles du constructeur sont deja dans le routage charge
        self.amorces.clear();
        let mut nb = 0;
        for n in 0..self.m.nets.len() {
            self.arracher(n);
            if segs[n].is_empty() && vias[n].is_empty() {
                continue;
            }
            let pref = self.larg(n).w[0];
            let neck: f64 = segs[n].iter().filter(|s| s.w < pref - 1e-9).map(|s| ((s.x2 - s.x1).powi(2) + (s.y2 - s.y1).powi(2)).sqrt()).sum();
            nb += segs[n].len() + vias[n].len();
            let (sg, vi) = (std::mem::take(&mut segs[n]), std::mem::take(&mut vias[n]));
            self.commit_geom(n, sg, vi, Vec::new());
            self.nets[n].neck_mm = neck;
            self.nets[n].ok = 1;
            self.nets[n].ko = 0;
        }
        Ok(nb)
    }

    /// Recharge un etat de routage complet (instantane de reseaux).
    pub fn charger_reseaux(&mut self, nets: &[NetR]) {
        for n in 0..self.nets.len() {
            self.arracher(n);
            self.restaurer(n, nets[n].clone());
        }
    }

    pub fn est_routable(&self, n: usize) -> bool {
        let net = &self.m.nets[n];
        if net.plan {
            net.pins_ext.iter().chain(net.pins_plan_bloc.iter()).any(|&(i, k)| !self.m.comps[i].pads[k].th)
        } else {
            !net.bloc_seul && net.pins_ext.len() + self.cibles_bloc[n].len() >= 2
        }
    }

    /// Proximite d'un reseau aux cuivres d'autres reseaux (mm equivalents : bande 2 x isolement = 1, 3 x = 0,4),
    /// ponderee par la sensibilite du reseau ; 0 si les cartes de proximite ne sont pas actives.
    pub fn prox_net(&self, n: usize) -> f64 {
        let (k2, k3) = match (&self.k2, &self.k3) {
            (Some(a), Some(b)) => (a, b),
            _ => return 0.0,
        };
        let key = n as u32 + 1;
        let mut p = 0.0;
        for &(nd, _) in &self.nets[n].noeuds {
            let nd = nd as usize;
            if !k2.ok(nd, key) {
                p += P;
            } else if !k3.ok(nd, key) {
                p += 0.4 * P;
            }
        }
        p * self.facteur_prox(n)
    }

    /// Mesures d'un reseau route : (longueur, vias, longueur retrecie, carres des reseaux de puissance, segments, echecs).
    pub fn metrique(&self, n: usize) -> [f64; 6] {
        let r = &self.nets[n];
        let len: f64 = r.segs.iter().map(|s| ((s.x2 - s.x1).powi(2) + (s.y2 - s.y1).powi(2)).sqrt()).sum();
        let puiss = matches!(self.m.nets[n].classe.as_str(), "PUISSANCE_8V" | "ENTREE_12V" | "CANAL_2A");
        [len, r.vias.len() as f64, r.neck_mm, if puiss { r.carres } else { 0.0 }, r.segs.len() as f64, r.ko as f64]
    }

    /// Distance exacte d'un segment (capsule) a tout cuivre etranger connu avant routage (pastilles, cuivre du bloc),
    /// moins l'isolement requis : >= 0 si le segment est admissible.
    fn marge_segment(&self, n: usize, s: &SegD) -> f64 {
        let m = self.m;
        let mut pire = f64::MAX;
        let clr_n = self.clr_net(n);
        let seg_rect = |r: &Rect| -> f64 {
            let inside = |x: f64, y: f64| x >= r[0] && x <= r[2] && y >= r[1] && y <= r[3];
            if inside(s.x1, s.y1) || inside(s.x2, s.y2) {
                return 0.0;
            }
            let mut d = f64::MAX;
            for (cx, cy) in [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])] {
                d = d.min(dist_seg(cx, cy, s));
            }
            // projections des extremites sur le rectangle
            for (x, y) in [(s.x1, s.y1), (s.x2, s.y2)] {
                d = d.min(dist_rect(x, y, r));
            }
            // segment traversant un bord sans extremite dedans : echantillonnage fin
            let k = 40;
            for t in 0..=k {
                let u = t as f64 / k as f64;
                d = d.min(dist_rect(s.x1 + u * (s.x2 - s.x1), s.y1 + u * (s.y2 - s.y1), r));
            }
            d
        };
        for i in 0..m.comps.len() {
            let p = self.e.poses[i];
            for k in 0..m.comps[i].pads.len() {
                let pd = &m.comps[i].pads[k];
                if pd.net == Some(n) {
                    continue;
                }
                let layers: Vec<u8> = if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] };
                if !layers.contains(&s.couche) {
                    continue;
                }
                let (x, y, hw, hh) = m.pad_world(i, k, &p);
                if (x - s.x1).abs() > 3.0 && (x - s.x2).abs() > 3.0 {
                    continue;
                }
                // piste contre pastille (M1 / M2 / U10 compris) : isolement normal (0,124 entre leurs objets seulement)
                let req = clr_n.max(m.regles.isolement.defaut);
                let k2 = 60;
                for t in 0..=k2 {
                    let u = t as f64 / k2 as f64;
                    let (px, py) = (s.x1 + u * (s.x2 - s.x1), s.y1 + u * (s.y2 - s.y1));
                    let v = dist_pad(px, py, x, y, hw, hh, pd.forme) - s.w / 2.0 - req;
                    if v < pire && m.param.journal.debug_marge {
                        eprintln!("  marge pad {}.{} {:.4}", m.comps[i].nom, m.comps[i].pads[k].nom, v);
                    }
                    pire = pire.min(v);
                }
            }
        }
        for (t, rt) in m.trous.iter().zip(m.trous_r.iter()) {
            pire = pire.min(dist_seg(t[0], t[1], s) - rt - s.w / 2.0 - m.regles.isolement.defaut);
        }
        // regions de cuivre d'empreinte des autres reseaux
        for i in 0..m.comps.len() {
            if m.comps[i].regions.is_empty() {
                continue;
            }
            let p = self.e.poses[i];
            for r in &m.comps[i].regions {
                if r.1 == Some(n) {
                    continue;
                }
                let (pts, ls) = m.region_monde(i, r, &p);
                if !ls.contains(&(s.couche as usize)) {
                    continue;
                }
                let req = clr_n.max(m.regles.isolement.defaut);
                let k2 = 40;
                for t in 0..=k2 {
                    let u = t as f64 / k2 as f64;
                    let (px, py) = (s.x1 + u * (s.x2 - s.x1), s.y1 + u * (s.y2 - s.y1));
                    pire = pire.min(dist_poly(px, py, &pts) - s.w / 2.0 - req);
                }
            }
        }
        // amorces deja posees des autres reseaux
        for (n2, segs) in &self.amorces {
            if *n2 == n {
                continue;
            }
            let req = self.clr_net(*n2).max(clr_n);
            for o in segs {
                if o.couche != s.couche {
                    continue;
                }
                let k = 60;
                let mut d = f64::MAX;
                for t in 0..=k {
                    let u = t as f64 / k as f64;
                    d = d.min(dist_seg(s.x1 + u * (s.x2 - s.x1), s.y1 + u * (s.y2 - s.y1), o));
                    d = d.min(dist_seg(o.x1 + u * (o.x2 - o.x1), o.y1 + u * (o.y2 - o.y1), s));
                }
                if d - o.w / 2.0 - s.w / 2.0 - req < pire && m.param.journal.debug_marge {
                    eprintln!("  marge amorce {} {:.4}", m.nets[*n2].nom, d - o.w / 2.0 - s.w / 2.0 - req);
                }
                pire = pire.min(d - o.w / 2.0 - s.w / 2.0 - req);
            }
        }
        for cu in &m.cuivre_bloc {
            if cu.net == Some(n) {
                continue;
            }
            let req = cu.net.map(|q| self.clr_net(q)).unwrap_or(m.regles.isolement.defaut).max(clr_n);
            let g = match cu.obj {
                CuivreObj::Seg { x1, y1, x2, y2, w, couche } => {
                    if couche != s.couche {
                        continue;
                    }
                    let o = SegD { x1, y1, x2, y2, w, couche };
                    let mut d = f64::MAX;
                    let k = 40;
                    for t in 0..=k {
                        let u = t as f64 / k as f64;
                        d = d.min(dist_seg(s.x1 + u * (s.x2 - s.x1), s.y1 + u * (s.y2 - s.y1), &o));
                    }
                    d - w / 2.0
                }
                CuivreObj::Via { x, y, d } => dist_seg(x, y, s) - d / 2.0,
                CuivreObj::Region { couche, r } => {
                    if couche != s.couche {
                        continue;
                    }
                    seg_rect(&r)
                }
            };
            pire = pire.min(g - s.w / 2.0 - req);
        }
        pire
    }

    /// Amorces dans l'axe des pastilles a pas fin du bloc (U10, M1, M2) quand le reseau n'a pas d'autre cuivre
    /// du bloc accessible : segment exact du centre de la pastille vers l'exterieur du boitier, puis raccord vers
    /// la cellule de grille admissible la plus proche. Geometrie verifiee exactement ; l'extremite devient la cible.
    fn poser_amorces(&mut self) {
        let m = self.m;
        for n in 0..m.nets.len() {
            let net = &m.nets[n];
            if net.plan || self.cibles_bloc[n].is_empty() {
                continue;
            }
            let key = n as u32 + 1;
            let ctx = self.ctx(n, Mode::Strict);
            let offs_min = ctx.offs.last().unwrap().clone();
            // groupes sans aucune cellule cible accessible depuis la grille
            let a_amorcer: Vec<usize> = (0..self.cibles_bloc[n].len())
                .filter(|&g| !self.cibles_bloc[n][g].0.iter().any(|&c| self.disk_state(c as usize, key, &offs_min, false) == 1))
                .collect();
            if a_amorcer.is_empty() {
                continue;
            }
            let pads_groupes: Vec<(usize, Vec<(usize, usize)>)> = m.groupes_bloc.iter().filter(|g| g.net == n && !g.pads.is_empty())
                .map(|g| g.pads.clone()).enumerate().collect();
            let lg = self.larg(n);
            let w = *lg.w.last().unwrap();
            for (gi, gpads) in pads_groupes {
            if !a_amorcer.contains(&gi) {
                continue;
            }
            for &(i, k) in &gpads {
                if !m.comps[i].pas_fin || m.comps[i].pads[k].th {
                    continue;
                }
                if let Some(nd) = self.amorcer(n, i, k, w, &offs_min) {
                    self.cibles_bloc[n][gi].0.push(nd);
                    break; // une amorce suffit par groupe
                }
            }
            }
        }
    }

    /// Amorce de sortie d'une pastille CMS dans l'axe de sa longueur (hors grille) puis raccord a une cellule libre :
    /// renvoie le noeud de raccord. Le cuivre de l'amorce est pose une fois pour toutes (jamais arrache).
    fn amorcer(&mut self, n: usize, i: usize, k: usize, w: f64, offs_min: &[(i32, i32)]) -> Option<u32> {
        let m = self.m;
        let p = self.e.poses[i];
        let couche: u8 = if p.bot { 1 } else { 0 };
        let (x, y, hw, hh) = m.pad_world(i, k, &p);
        let (cx, cy) = m.centre(i, &p);
        // axe long de la pastille, sens sortant
        let (ux0, uy0) = if hw >= hh { ((x - cx).signum(), 0.0) } else { (0.0, (y - cy).signum()) };
        let demi0 = if hw >= hh { hw } else { hh };
        let large0 = if hw >= hh { hh } else { hw };
        let mut dirs = vec![(ux0, uy0, demi0, large0)];
        // broche en bout de rangee (boitier a pas fin) : sortie laterale, du cote sans voisine, en second choix
        if m.comps[i].pas_fin {
            for sg in [1.0f64, -1.0] {
                let (dx, dy) = (-uy0 * sg, ux0 * sg);
                let libre = (0..m.comps[i].pads.len()).filter(|&k2| k2 != k).all(|k2| {
                    let (x2, y2, _, _) = m.pad_world(i, k2, &p);
                    let along = (x2 - x) * dx + (y2 - y) * dy;
                    let perp = ((x2 - x) * ux0 + (y2 - y) * uy0).abs();
                    !(along > 0.0 && along < 1.5 && perp < demi0 + 0.3)
                });
                if libre {
                    dirs.push((dx, dy, large0, 0.0));
                }
            }
        }
        for (ux, uy, demi, large) in dirs {
        // decalage lateral de l'axe (dans la pastille) : au pas de 0,5 mm, deux amorces paralleles de 0,254 mm ne
        // laissent que 0,246 mm ; decaler l'une de 0,02 mm donne les 0,25 mm de COMMUTATION (comme le bloc manuel)
        let (px_, py_) = (-uy, ux);
        for &dl in &[0.0f64, 0.02, -0.02, 0.04, -0.04, 0.06, -0.06] {
        if dl.abs() > large {
            continue;
        }
        let (x, y) = (x + px_ * dl, y + py_ * dl);
        // noeud de commutation (0,25 mm) : amorce longue d'abord, pour sortir du champ de pastilles a pas fin avant de
        // rejoindre la grille (les amorces des commandes de grille voisines restent courtes)
        // boitier a pas fin : sorties en eventail decalees (une broche sur deux longue) pour que les raccords voisins ne
        // soient pas cote a cote sur la grille
        // (essai d'eventail decale une broche sur deux : plus d'echecs, abandonne ; reglage routage.amorce_eventail_paires
        // pour le refaire)
        let pair = m.param.routage.amorce_eventail_paires && m.comps[i].pads[k].nom.parse::<i32>().map(|v| v % 2 == 0).unwrap_or(false);
        let moyen = m.param.routage.amorce_moyenne;
        let longueurs: &[f64] = if self.m.nets[n].classe == "COMMUTATION" || (m.comps[i].pas_fin && pair) {
            &[1.3, 1.7, 2.2, 1.0, 0.8, 0.6, 0.4]
        } else if m.comps[i].pas_fin && moyen {
            // pas fin : raccords a 1 mm du bout des broches d'abord, la ou les sorties voisines peuvent deja s'ecarter
            &[1.0, 0.8, 1.3, 0.6, 1.7, 0.4, 2.2]
        } else {
            &[0.4, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2]
        };
        for &l_ext in longueurs {
            let (ex, ey) = (x + ux * (demi + l_ext), y + uy * (demi + l_ext));
            let s1 = SegD { x1: x, y1: y, x2: ex, y2: ey, w, couche };
            if m.param.journal.debug_amorces {
                eprintln!("amorce {} {}.{} l {} marge {:.4} w {}", m.nets[n].nom, m.comps[i].nom, m.comps[i].pads[k].nom, l_ext, self.marge_segment(n, &s1), w);
            }
            // amorce : isolement de la regle exact (sans la marge interne MARGE_ISOL) + 2 microns ; necessaire pour la
            // sortie COMMUTATION (0,25 mm) d'U10.8 entre ses voisines au pas de 0,5 mm (ecart reel 0,253 mm)
            if self.marge_segment(n, &s1) < 0.002 - MARGE_ISOL {
                continue; // longueurs non monotones (COMMUTATION : longues d'abord) : essayer les autres
            }
            // raccord vers une cellule de grille voisine admissible
            let key = n as u32 + 1;
            let mut cands: Vec<(f64, u32)> = Vec::new();
            let (ix, iy) = ((ex / P).floor() as i64, (ey / P).floor() as i64);
            for dy in -2..=2 {
                for dx in -2..=2 {
                    let (gx, gy) = (ix + dx, iy + dy);
                    if gx < 0 || gy < 0 || gx >= self.nx as i64 || gy >= self.ny as i64 {
                        continue;
                    }
                    let (qx, qy) = self.cxy(gx as usize, gy as usize);
                    // pas de retour vers le boitier
                    if (qx - ex) * ux + (qy - ey) * uy < -1e-9 {
                        continue;
                    }
                    let nd = couche as usize * self.nc + gy as usize * self.nx + gx as usize;
                    // meme critere que l'accessibilite des broches : disque de la largeur la plus fine libre
                    if self.disk_state(nd, key, &offs_min, false) != 1 {
                        continue;
                    }
                    // raccord dans l'axe de la broche d'abord (ecart lateral), puis le plus proche : un raccord
                    // de biais empiete sur le couloir de la broche voisine au pas fin
                    let lat = ((qx - ex) * px_ + (qy - ey) * py_).abs();
                    // boitier a pas fin : raccord strictement dans l'axe (le couloir lateral appartient aux voisines)
                    if m.comps[i].pas_fin && lat > 0.06 {
                        continue;
                    }
                    cands.push((lat * 10.0 + ((qx - ex).powi(2) + (qy - ey).powi(2)).sqrt(), nd as u32));
                }
            }
            cands.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
            for (_, nd) in cands {
                let (qx, qy) = self.node_xy(nd as usize);
                let s2 = SegD { x1: ex, y1: ey, x2: qx, y2: qy, w, couche };
                if self.marge_segment(n, &s2) >= 0.002 - MARGE_ISOL {
                    let mut store = Vec::new();
                    let infl = self.clr_net(n) + H_DEF + DELTA;
                    for sg in [&s1, &s2] {
                        self.raster_seg(sg, infl, key, &mut store);
                    }
                    // le stock n'est jamais retire : l'amorce reste en place quels que soient les reroutages
                    self.amorces.push((n, vec![s1.clone(), s2]));
                    return Some(nd);
                }
            }
        }
        }
        }
        None
    }

    /// Convertisseur libere : broches ordinaires sans aucune cellule accessible (pastilles a pas fin de U10 / M1 / M2,
    /// pieces serrees) -> amorce comme pour le bloc.
    fn amorcer_broches(&mut self) {
        let m = self.m;
        if !m.bloc_libre {
            return;
        }
        if m.param.routage.eventail_auto {
            for i in 0..m.comps.len() {
                if m.comps[i].pas_fin {
                    self.eventail_auto(i);
                }
            }
        }
        // COMMUTATION d'abord (amorce longue posee avant ses voisines)
        let mut ordre: Vec<usize> = (0..m.nets.len()).collect();
        ordre.sort_by_key(|&n| (m.nets[n].classe != "COMMUTATION", n));
        for n in ordre {
            let net = &m.nets[n];
            if net.plan || net.pins_ext.len() < 2 {
                continue;
            }
            let key = n as u32 + 1;
            let ctx = self.ctx(n, Mode::Strict);
            let offs_min = ctx.offs.last().unwrap().clone();
            let w = *self.larg(n).w.last().unwrap();
            for &(i, k) in &net.pins_ext.clone() {
                if m.comps[i].pads[k].th || self.pin_extra.contains_key(&(i, k)) {
                    continue;
                }
                let libre = self.pad_cells(i, k).iter().any(|&c| self.disk_state(c as usize, key, &offs_min, false) == 1);
                if libre {
                    continue;
                }
                if let Some(nd) = self.amorcer(n, i, k, w, &offs_min) {
                    self.pin_extra.insert((i, k), nd);
                }
            }
        }
    }

    /// Eventail de sortie automatique (generique) d'un boitier a pas fin : les pastilles sont groupees en rangees
    /// (meme sens de sortie, alignees) ; chaque broche sort tout droit de `l0`, puis a 45 degres vers une position
    /// laterale au pas elargi `p_out` (largeur mini + 2 x isolement de la classe la plus exigeante + 2 cellules), centre
    /// de rangee conserve ; le bout est raccorde a la cellule libre la plus proche. Rien de propre a une carte :
    /// tout vient de la geometrie des pastilles et des regles.
    fn eventail_auto(&mut self, i: usize) {
        let m = self.m;
        let p = self.e.poses[i];
        let couche: u8 = if p.bot { 1 } else { 0 };
        let (cx, cy) = m.centre(i, &p);
        let iso_max = m.regles.isolement.commutation.max(m.regles.isolement.defaut);
        // reglages routage.eventail_pas_sortie_mm (« auto » : formule de 9b5b5a3, rendue generique a l'etape 6) et
        // routage.eventail_longueur_mm
        let p_out = m.param.routage.eventail_pas_sortie_mm.valeur().unwrap_or(0.254 + 2.0 * iso_max + 2.0 * P);
        let l0: f64 = m.param.routage.eventail_longueur_mm;
        // rangees : (sens sortant ux, uy ; coordonnee d'axe arrondie) -> pastilles
        let mut rangees: Vec<((i32, i32, i64), Vec<usize>)> = Vec::new();
        for k in 0..m.comps[i].pads.len() {
            let pd = &m.comps[i].pads[k];
            if pd.th {
                continue;
            }
            let (x, y, hw, hh) = m.pad_world(i, k, &p);
            if (hw - hh).abs() < 1e-6 {
                continue; // pastille carree (plot central) : pas de sens de sortie
            }
            let (ux, uy) = if hw >= hh { ((x - cx).signum() as i32, 0) } else { (0, (y - cy).signum() as i32) };
            let axe = if ux != 0 { (x * 100.0).round() as i64 } else { (y * 100.0).round() as i64 };
            match rangees.iter_mut().find(|r| r.0 .0 == ux && r.0 .1 == uy && (r.0 .2 - axe).abs() <= 5) {
                Some(r) => r.1.push(k),
                None => rangees.push(((ux, uy, axe), vec![k])),
            }
        }
        for ((ux, uy, _), mut ks) in rangees {
            if ks.len() < 2 {
                continue;
            }
            let (ux, uy) = (ux as f64, uy as f64);
            let (px, py) = (-uy, ux);
            let lat = |k: usize| {
                let (x, y, _, _) = m.pad_world(i, k, &p);
                x * px + y * py
            };
            ks.sort_by(|a, b| lat(*a).partial_cmp(&lat(*b)).unwrap());
            let c = (lat(ks[0]) + lat(*ks.last().unwrap())) / 2.0;
            let nks = ks.len();
            // reseaux a isolement renforce (COMMUTATION) d'abord : leurs voisines se decalent ensuite pour l'eviter
            let mut ordre: Vec<(usize, usize)> = ks.iter().copied().enumerate().collect();
            ordre.sort_by_key(|&(j, k)| (m.comps[i].pads[k].net.map(|n| m.nets[n].classe != "COMMUTATION").unwrap_or(true), j));
            for (j, k) in ordre {
                let n = match m.comps[i].pads[k].net {
                    Some(n) => n,
                    None => continue,
                };
                // broche pontee (reliee par un pont a sa voisine) : pas a sortir
                if m.nets[n].plan || self.pin_extra.contains_key(&(i, k)) || m.nets[n].pins_ext.len() < 2 || !m.nets[n].pins_ext.contains(&(i, k)) {
                    continue;
                }
                let (x0, y0, hw, hh) = m.pad_world(i, k, &p);
                let demi = if ux != 0.0 { hw } else { hh };
                let large = if ux != 0.0 { hh } else { hw };
                let rang = (j as f64 - (nks as f64 - 1.0) / 2.0).abs();
                let cible = c + (j as f64 - (nks as f64 - 1.0) / 2.0) * p_out;
                let w = *self.larg(n).w.last().unwrap();
                // depart du coude decale : les broches exterieures obliquent d'abord, les interieures plus loin (sinon
                // deux obliques paralleles au pas de la rangee ne sont qu'a pas / racine(2) l'une de l'autre)
                let pas_rangee = if nks > 1 { (lat(ks[nks - 1]) - lat(ks[0])) / (nks as f64 - 1.0) } else { 0.5 };
                let decal = ((w + iso_max) * std::f64::consts::SQRT_2 - pas_rangee).max(0.0) + 0.02;
                let l0base = l0 + ((nks as f64 - 1.0) / 2.0 - rang) * decal;
                // isolement renforce (COMMUTATION) : sortie droite plus longue, au-dela des coudes des voisines, pour
                // que la grille (disque elargi de la classe) trouve un couloir ; longueurs essayees dans l'ordre
                let extras: Vec<f64> = if m.nets[n].classe == "COMMUTATION" { vec![1.2, 0.9, 1.6, 0.6, 2.0, 0.3, 0.0] } else { vec![0.0] };
                // decalage lateral de l'axe dans la pastille (0, +-0,02, +-0,04 mm) pour l'isolement exact
                let mut segs: Vec<SegD> = Vec::new();
                let mut trouve = false;
                for &ex in &extras {
                if trouve {
                    break;
                }
                let l0j = l0base + ex;
                for &dl in &[0.0f64, 0.02, -0.02, 0.04, -0.04] {
                    if dl.abs() > large {
                        continue;
                    }
                    let (x, y) = (x0 + px * dl, y0 + py * dl);
                    let delta = cible - (lat(k) + dl);
                    let (x1, y1) = (x + ux * (demi + l0j), y + uy * (demi + l0j));
                    let (x2, y2) = (x1 + ux * delta.abs() + px * delta, y1 + uy * delta.abs() + py * delta);
                    let mut ss = vec![SegD { x1: x, y1: y, x2: x1, y2: y1, w, couche }];
                    if delta.abs() > 1e-6 {
                        ss.push(SegD { x1, y1, x2, y2, w, couche });
                    }
                    if ss.iter().all(|sg| self.marge_segment(n, sg) >= 0.002 - MARGE_ISOL) {
                        segs = ss;
                        trouve = true;
                        break;
                    }
                    if segs.is_empty() {
                        segs = ss;
                    }
                }
                }
                let delta = cible - lat(k);
                if m.param.journal.debug_amorces {
                    eprintln!("eventail {}.{} delta {:.3} marges {:?}", m.comps[i].nom, m.comps[i].pads[k].nom, delta,
                        segs.iter().map(|sg| (self.marge_segment(n, sg) * 1000.0).round() / 1000.0).collect::<Vec<_>>());
                }
                if segs.iter().any(|sg| self.marge_segment(n, sg) < 0.002 - MARGE_ISOL) {
                    continue; // repli : amorce ordinaire
                }
                let key = n as u32 + 1;
                let ctx = self.ctx(n, Mode::Strict);
                let offs_min = ctx.offs.last().unwrap().clone();
                let (ex, ey) = (segs.last().unwrap().x2, segs.last().unwrap().y2);
                let (ix, iy) = ((ex / P).floor() as i64, (ey / P).floor() as i64);
                let mut cands: Vec<(f64, usize)> = Vec::new();
                for dy in -2..=2 {
                    for dx in -2..=2 {
                        let (gx, gy) = (ix + dx, iy + dy);
                        if gx < 0 || gy < 0 || gx >= self.nx as i64 || gy >= self.ny as i64 {
                            continue;
                        }
                        let (qx, qy) = self.cxy(gx as usize, gy as usize);
                        if (qx - ex) * ux + (qy - ey) * uy < -1e-9 {
                            continue;
                        }
                        let nd = couche as usize * self.nc + gy as usize * self.nx + gx as usize;
                        if self.disk_state(nd, key, &offs_min, false) != 1 {
                            continue;
                        }
                        cands.push((((qx - ex).powi(2) + (qy - ey).powi(2)).sqrt(), nd));
                    }
                }
                cands.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
                for (_, nd) in cands {
                    let (qx, qy) = self.node_xy(nd);
                    let s3 = SegD { x1: ex, y1: ey, x2: qx, y2: qy, w, couche };
                    if self.marge_segment(n, &s3) >= 0.002 - MARGE_ISOL {
                        segs.push(s3);
                        let mut store = Vec::new();
                        let infl = self.clr_net(n) + H_DEF + DELTA;
                        for sg in &segs {
                            self.raster_seg(sg, infl, key, &mut store);
                        }
                        self.amorces.push((n, segs));
                        self.pin_extra.insert((i, k), nd as u32);
                        break;
                    }
                }
            }
        }
    }

    /// Ponts de cuivre entre pastilles voisines d'un meme reseau d'une meme piece de puissance (M1, M2 : broches
    /// 1-3 / 5-8 ; convertisseur libere) : piste fixe pose une fois, la broche pontee n'est plus a router.
    fn poser_ponts(&mut self) {
        let m = self.m;
        for &(n, i, k1, k2, w) in &m.ponts {
            let p = self.e.poses[i];
            let (x1, y1, _, _) = m.pad_world(i, k1, &p);
            let (x2, y2, _, _) = m.pad_world(i, k2, &p);
            let couche: u8 = if p.bot { 1 } else { 0 };
            let s = SegD { x1, y1, x2, y2, w, couche };
            let mut store = Vec::new();
            let infl = self.clr_net(n) + H_DEF + DELTA;
            self.raster_seg(&s, infl, n as u32 + 1, &mut store);
            self.amorces.push((n, vec![s]));
            self.pieces_pont.push(i);
        }
    }

    /// Pieces portant une amorce ou un pont (a ne pas deplacer ensuite : le cuivre resterait a l'ancienne position).
    pub fn pieces_amorcees(&self) -> Vec<usize> {
        let mut v: Vec<usize> = self.pin_extra.keys().map(|&(i, _)| i).chain(self.pieces_pont.iter().copied()).collect();
        v.sort();
        v.dedup();
        v
    }

    #[inline]
    fn cxy(&self, ix: usize, iy: usize) -> (f64, f64) {
        ((ix as f64 + 0.5) * P, (iy as f64 + 0.5) * P)
    }

    #[inline]
    fn node_xy(&self, n: usize) -> (f64, f64) {
        let c = n % self.nc;
        self.cxy(c % self.nx, c / self.nx)
    }

    fn node_at(&self, x: f64, y: f64, l: u8) -> u32 {
        let ix = ((x / P).floor() as usize).min(self.nx - 1);
        let iy = ((y / P).floor() as usize).min(self.ny - 1);
        (l as usize * self.nc + iy * self.nx + ix) as u32
    }

    pub fn clr_net(&self, n: usize) -> f64 {
        MARGE_ISOL
            + if self.m.nets[n].classe == "COMMUTATION" {
                self.m.regles.isolement.commutation
            } else {
                self.m.regles.isolement.defaut
            }
    }

    /// Largeurs essayees noeud par noeud, de la preferee a la mini de la classe (>= 0,254).
    pub fn larg(&self, n: usize) -> Larg {
        let net = &self.m.nets[n];
        if net.plan {
            // descentes vers les plans : 0,5 mm pour +8V (choix dans [0,3 ; 15]), 0,3 mm pour GND
            return if net.classe == "PUISSANCE_8V" { Larg { w: vec![0.5, 0.3], mini_regle: 0.3 } } else { Larg { w: vec![0.3, 0.254], mini_regle: 0.254 } };
        }
        let l = self.m.regles.largeurs.get(&net.classe).or_else(|| self.m.regles.largeurs.get("defaut")).copied().unwrap_or([0.254, 0.3, 3.0]);
        let (mn, pf) = (l[0], l[1]);
        let pref = pf.max(mn);
        let w = match net.classe.as_str() {
            "ENTREE_12V" | "PUISSANCE_8V" => vec![pref, 1.5, 1.0, 0.5, 0.3],
            "CANAL_2A" => vec![pref, 1.0, 0.5], // regle Altium : mini 0,5 / preferee 1,5
            // noeud de commutation (convertisseur libere) : court et large, plafond de la classe 3 mm
            "COMMUTATION" if self.m.bloc_libre => vec![2.0, 1.5, 1.0, 0.5, 0.3, 0.254],
            _ => {
                let mut v = vec![pref];
                for c in [0.3, 0.254] {
                    if c < *v.last().unwrap() - 1e-9 && c >= mn.max(0.254) - 1e-9 {
                        v.push(c);
                    }
                }
                v
            }
        };
        Larg { w, mini_regle: mn }
    }

    fn via_cout(&self, n: usize) -> f32 {
        match self.m.nets[n].classe.as_str() {
            "ENTREE_12V" | "CANAL_2A" | "RAILS" | "PUISSANCE_8V" => VIA_D * 3.0,
            _ => VIA_D,
        }
    }

    fn ctx(&self, n: usize, mode: Mode) -> Ctx {
        let l = self.larg(n);
        // surcroit d'isolement de la classe au-dela de l'isolement par defaut (COMMUTATION : 0,05 mm) ; la marge
        // interne MARGE_ISOL est deja dans le gonflement des cartes (l'ancien calcul, qui la comptait une seconde fois
        // et elargissait d'une cellule le disque de toutes les pistes, est supprime : annexe A du contrat)
        let clr_x = (self.clr_net(n) - MARGE_ISOL - self.m.regles.isolement.defaut).max(0.0);
        Ctx {
            key: n as u32 + 1,
            offs: l.w.iter().map(|w| offsets(w / 2.0 - H_DEF + clr_x)).collect(),
            w: l.w,
            voffs: offsets(self.m.regles.via.diametre / 2.0 - H_DEF + clr_x),
            mode,
            via_cout: self.via_cout(n),
            prox: 0.0,
            coude: 0.0,
            mult: couche_mult(&self.m.nets[n]),
        }
    }

    fn each_cell_near_rect<F: FnMut(&mut Self, usize)>(&mut self, r: &Rect, infl: f64, mut f: F) {
        let x0 = (((r[0] - infl) / P).floor().max(0.0)) as usize;
        let y0 = (((r[1] - infl) / P).floor().max(0.0)) as usize;
        let x1 = ((((r[2] + infl) / P).ceil()) as usize).min(self.nx - 1);
        let y1 = ((((r[3] + infl) / P).ceil()) as usize).min(self.ny - 1);
        for iy in y0..=y1 {
            for ix in x0..=x1 {
                let (x, y) = self.cxy(ix, iy);
                if dist_rect(x, y, r) < infl {
                    f(self, iy * self.nx + ix);
                }
            }
        }
    }

    fn raster_rect(&mut self, l: usize, r: &Rect, infl: f64, key: u32) {
        let nc = self.nc;
        let mut cells = Vec::new();
        self.each_cell_near_rect(r, infl, |_, c| cells.push(c));
        for c in cells {
            self.kp.add(l * nc + c, key);
        }
    }

    fn raster_seg(&mut self, s: &SegD, infl: f64, key: u32, store: &mut Vec<u32>) {
        let r = infl + s.w / 2.0;
        let bb = [s.x1.min(s.x2), s.y1.min(s.y2), s.x1.max(s.x2), s.y1.max(s.y2)];
        let x0 = (((bb[0] - r) / P).floor().max(0.0)) as usize;
        let y0 = (((bb[1] - r) / P).floor().max(0.0)) as usize;
        let x1 = ((((bb[2] + r) / P).ceil()) as usize).min(self.nx - 1);
        let y1 = ((((bb[3] + r) / P).ceil()) as usize).min(self.ny - 1);
        let l = s.couche as usize;
        for iy in y0..=y1 {
            for ix in x0..=x1 {
                let (x, y) = self.cxy(ix, iy);
                if dist_seg(x, y, s) < r {
                    let n = l * self.nc + iy * self.nx + ix;
                    self.kt.add(n, key);
                    self.kt_who[n] = key;
                    store.push(n as u32);
                }
            }
        }
    }

    fn raster_via(&mut self, x: f64, y: f64, infl: f64, key: u32, store: &mut Vec<u32>) {
        let r = infl + self.m.regles.via.diametre / 2.0;
        let x0 = (((x - r) / P).floor().max(0.0)) as usize;
        let y0 = (((y - r) / P).floor().max(0.0)) as usize;
        let x1 = ((((x + r) / P).ceil()) as usize).min(self.nx - 1);
        let y1 = ((((y + r) / P).ceil()) as usize).min(self.ny - 1);
        for iy in y0..=y1 {
            for ix in x0..=x1 {
                let (cx, cy) = self.cxy(ix, iy);
                if ((cx - x).powi(2) + (cy - y).powi(2)).sqrt() < r {
                    for l in 0..NCOUCHES {
                        let n = l * self.nc + iy * self.nx + ix;
                        self.kt.add(n, key);
                        self.kt_who[n] = key;
                        store.push(n as u32);
                    }
                }
            }
        }
    }

    /// 0 = interdit, 1 = libre, 2 = occupe par une piste etrangere (permis en negociation / sonde).
    #[inline]
    fn cell_state(&self, n: usize, key: u32, souple: bool) -> u8 {
        let c = n % self.nc;
        if self.hard[c] || (self.zone[c] && n < self.nc) || !self.kp.ok(n, key) {
            return 0;
        }
        if self.kt.ok(n, key) {
            return 1;
        }
        if souple {
            2
        } else {
            0
        }
    }

    fn disk_state(&self, n: usize, key: u32, offs: &[(i32, i32)], souple: bool) -> u8 {
        let c = n % self.nc;
        let l = n / self.nc;
        let (x, y) = ((c % self.nx) as i32, (c / self.nx) as i32);
        let mut st = 1u8;
        for &(dx, dy) in offs {
            let (u, v) = (x + dx, y + dy);
            if u < 0 || v < 0 || u >= self.nx as i32 || v >= self.ny as i32 {
                return 0;
            }
            let m = l * self.nc + v as usize * self.nx + u as usize;
            match self.cell_state(m, key, souple) {
                0 => return 0,
                2 => st = 2,
                _ => {}
            }
        }
        st
    }

    /// Etat d'un noeud : 0 interdit ; sinon 1 + 2 x (indice de la largeur la plus large qui passe) + (1 si recouvrement).
    fn leg(&mut self, n: usize, ctx: &Ctx) -> u8 {
        // couche de routage absente de la carte (donnees) : aucune piste
        if !self.m.couches_actives[n / self.nc] {
            return 0;
        }
        if self.legm[n] == self.ep {
            return self.legv[n];
        }
        let souple = ctx.mode != Mode::Strict;
        let mut v = 0u8;
        let mut soft_best = 0u8;
        for (wi, offs) in ctx.offs.iter().enumerate() {
            match self.disk_state(n, ctx.key, offs, souple) {
                1 => {
                    v = 1 + 2 * wi as u8;
                    break;
                }
                2 => {
                    if soft_best == 0 {
                        soft_best = 2 + 2 * wi as u8;
                    }
                }
                _ => {}
            }
        }
        if v == 0 {
            v = soft_best;
        }
        self.legm[n] = self.ep;
        self.legv[n] = v;
        v
    }

    /// Test de via memorise pour la recherche en cours (estampille `ep`) ; sans le cache (`sans_cache_via`), test
    /// direct : les sorties sont identiques octet pour octet (avenant 2.2, A.4-1, test (a)).
    #[inline]
    fn via_state_memo(&mut self, c: usize, ctx: &Ctx, ep: u32) -> u8 {
        if !self.cache_via {
            return self.via_state(c, ctx);
        }
        if self.vsm[c] == ep {
            return self.vsv[c];
        }
        let v = self.via_state(c, ctx);
        self.vsm[c] = ep;
        self.vsv[c] = v;
        v
    }

    fn via_state(&self, c: usize, ctx: &Ctx) -> u8 {
        if self.novia[c] > 0 {
            return 0;
        }
        // trou a trou >= 0,254 mm (+ marge) avec tout via deja pose, y compris du meme reseau
        if !self.cache_via {
            let (x, y) = ((c % self.nx) as i32, (c / self.nx) as i32);
            for &(dx, dy) in &self.offs_trou {
                let (u, v) = (x + dx, y + dy);
                if u >= 0 && v >= 0 && u < self.nx as i32 && v < self.ny as i32 && self.vias_c[v as usize * self.nx + u as usize] > 0 {
                    return 0;
                }
            }
        } else if self.trou_cnt[c] > 0 {
            return 0;
        }
        let souple = ctx.mode != Mode::Strict;
        let mut st = 1;
        for l in 0..NCOUCHES {
            let a = self.disk_state(c + l * self.nc, ctx.key, &ctx.voffs, souple);
            if a == 0 {
                return 0;
            }
            st = st.max(a);
        }
        st
    }

    /// Cellules (indices de cellule, sans couche) dont le centre est a moins de `infl` du cuivre d'une pastille
    /// (dist < infl) et a plus de `-prof_max`... : utilitaire general (prof_max = f64::MAX : aucun minimum).
    fn cells_pad(&self, cx: f64, cy: f64, hw: f64, hh: f64, forme: FormePad, infl: f64, _prof: f64) -> Vec<usize> {
        let mut out = Vec::new();
        let r = infl.max(0.0);
        let x0 = (((cx - hw - r) / P).floor().max(0.0)) as usize;
        let y0 = (((cy - hh - r) / P).floor().max(0.0)) as usize;
        let x1 = ((((cx + hw + r) / P).ceil()) as usize).min(self.nx - 1);
        let y1 = ((((cy + hh + r) / P).ceil()) as usize).min(self.ny - 1);
        for iy in y0..=y1 {
            for ix in x0..=x1 {
                let (x, y) = self.cxy(ix, iy);
                if dist_pad(x, y, cx, cy, hw, hh, forme) < infl {
                    out.push(iy * self.nx + ix);
                }
            }
        }
        out
    }

    /// Cellules d'une pastille ou une piste peut se terminer : centre de cellule DANS le cuivre reel, a au moins
    /// 0,137 mm du bord (demi-largeur mini 0,127 + 0,01) ; a defaut a au moins 0,01 mm ; a defaut la cellule du centre.
    fn pad_cells(&self, i: usize, k: usize) -> Vec<u32> {
        let p = self.e.poses[i];
        let (cx, cy, hw, hh) = self.m.pad_world(i, k, &p);
        let pd = &self.m.comps[i].pads[k];
        let layers: Vec<usize> = if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] };
        let mut cells = Vec::new();
        for marge in [0.137, 0.01] {
            cells = self.cells_pad(cx, cy, hw, hh, pd.forme, -marge, f64::MAX);
            if !cells.is_empty() {
                break;
            }
        }
        if cells.is_empty() {
            cells.push(((cy / P).floor() as usize).min(self.ny - 1) * self.nx + ((cx / P).floor() as usize).min(self.nx - 1));
        }
        let mut out = Vec::new();
        for &l in &layers {
            for &c in &cells {
                out.push((l * self.nc + c) as u32);
            }
        }
        out
    }

    /// Surcout d'entree dans un noeud (en plus du pas geometrique) : resistance (pas x (pref/w - 1)),
    /// historique, recouvrement, proximite des cuivres etrangers (passe d'espacement).
    #[inline]
    fn cout_noeud(&self, st: u8, n: usize, ctx: &Ctx, step: f32) -> f32 {
        let wi = ((st - 1) / 2) as usize;
        let lm = ctx.mult[n / self.nc];
        let mut c = self.hist[n] + step * (lm * (ctx.w[0] / ctx.w[wi]) as f32 - 1.0);
        if (st - 1) % 2 == 1 {
            c += self.pres;
        }
        if ctx.prox > 0.0 {
            if let (Some(k2), Some(k3)) = (&self.k2, &self.k3) {
                if !k2.ok(n, ctx.key) {
                    c += ctx.prox * step;
                } else if !k3.ok(n, ctx.key) {
                    c += 0.4 * ctx.prox * step;
                }
            }
        }
        c
    }

    /// A* (but = arbre) ou Dijkstra borne (but = cellule de via de plan).
    fn chercher(&mut self, src: &[u32], ctx: &Ctx, but: &But) -> Option<Vec<u32>> {
        self.ep += 1;
        let ep = self.ep;
        self.heap.clear();
        let nc = self.nc;
        let nx = self.nx as i32;
        let ny = self.ny as i32;
        let pf = P as f32;
        let hfun = |s: &Self, n: usize| -> f32 {
            match but {
                But::Arbre(bb) => {
                    let c = n % s.nc;
                    let (x, y) = (c % s.nx, c / s.nx);
                    let dx = if x < bb[0] { bb[0] - x } else if x > bb[2] { x - bb[2] } else { 0 } as f32;
                    let dy = if y < bb[1] { bb[1] - y } else if y > bb[3] { y - bb[3] } else { 0 } as f32;
                    let (a, b) = if dx > dy { (dx, dy) } else { (dy, dx) };
                    (a - b + std::f32::consts::SQRT_2 * b) * pf
                }
                But::Via(..) => 0.0,
            }
        };
        for &s in src {
            let s = s as usize;
            if let But::Arbre(_) = but {
                if self.tree[s] == self.tep {
                    return Some(vec![s as u32]);
                }
            }
            if self.leg(s, ctx) == 0 {
                continue;
            }
            self.seen[s] = ep;
            self.gc[s] = 0.0;
            self.par[s] = NONE;
            self.din[s] = 8;
            let f = hfun(self, s);
            self.heap.push(HI { f, n: s as u32 });
        }
        let mut exp = 0usize;
        while let Some(HI { f, n }) = self.heap.pop() {
            let n = n as usize;
            let g = self.gc[n];
            if g + hfun(self, n) < f - 1e-3 {
                continue;
            }
            let c = n % nc;
            let l = n / nc;
            let (x, y) = ((c % self.nx) as i32, (c / self.nx) as i32);
            let fini = match but {
                But::Arbre(_) => self.tree[n] == self.tep && !(self.planaire[n] && self.din[n] == 8 && self.par[n] != NONE),
                But::Via(..) => {
                    if self.tree[n] == self.tep {
                        true
                    } else if self.par[n] != NONE {
                        let vs = self.via_state_memo(c, ctx, ep);
                        if vs == 2 {
                            self.vsoft[n] = ep;
                        }
                        vs != 0
                    } else {
                        false
                    }
                }
            };
            if fini {
                let mut path = vec![n as u32];
                let mut q = n;
                while self.par[q] != NONE {
                    q = self.par[q] as usize;
                    path.push(q as u32);
                }
                path.reverse();
                return Some(path);
            }
            exp += 1;
            if exp > self.max_exp {
                return None;
            }
            let di = self.din[n];
            for (d, &(dx, dy)) in DIRS.iter().enumerate() {
                let (u, v) = (x + dx, y + dy);
                if u < 0 || v < 0 || u >= nx || v >= ny {
                    continue;
                }
                match but {
                    But::Via(cx, cy, rmax) => {
                        if (u - *cx as i32).abs().max((v - *cy as i32).abs()) > *rmax as i32 {
                            continue;
                        }
                    }
                    But::Arbre(_) => {
                        if u < self.fen[0] || v < self.fen[1] || u > self.fen[2] || v > self.fen[3] {
                            continue;
                        }
                    }
                }
                let mnode = l * nc + v as usize * self.nx + u as usize;
                let in_tree = matches!(but, But::Arbre(_)) && self.tree[mnode] == self.tep;
                let st = if in_tree { 1 } else { self.leg(mnode, ctx) };
                if st == 0 {
                    continue;
                }
                let turn = if di == 8 {
                    0.0
                } else {
                    let k = (di as i32 - d as i32).unsigned_abs() as usize;
                    TURN[k.min(8 - k)]
                };
                if turn > 1e5 {
                    continue;
                }
                let step = if d % 2 == 1 { pf * std::f32::consts::SQRT_2 } else { pf };
                let tc = if turn > 0.0 { turn + ctx.coude } else { 0.0 };
                let cost = g + step + tc + if in_tree { 0.0 } else { self.cout_noeud(st, mnode, ctx, step) };
                if self.seen[mnode] != ep || cost < self.gc[mnode] {
                    self.seen[mnode] = ep;
                    self.gc[mnode] = cost;
                    self.par[mnode] = n as u32;
                    self.din[mnode] = d as u8;
                    let f = cost + hfun(self, mnode);
                    self.heap.push(HI { f, n: mnode as u32 });
                }
            }
            // pas de via sur l'axe d'une piste existante du bloc, sauf au bout d'une piste nouvelle de la meme couche
            let via_interdit = self.planaire[n] && (self.par[n] == NONE || self.din[n] == 8);
            if let (But::Arbre(_), false) = (but, via_interdit) {
                let st = self.via_state_memo(c, ctx, ep);
                for lo in 0..NCOUCHES {
                if lo == l || st == 0 {
                    continue;
                }
                let o = lo * nc + c;
                {
                    let in_tree = self.tree[o] == self.tep;
                    let so = if in_tree { 1 } else { self.leg(o, ctx) };
                    if so != 0 {
                        let cost = g + ctx.via_cout + if st == 2 { self.pres } else { 0.0 } + if in_tree { 0.0 } else { self.cout_noeud(so, o, ctx, pf) };
                        if st == 2 {
                            self.vsoft[n] = ep;
                        }
                        if self.seen[o] != ep || cost < self.gc[o] {
                            self.seen[o] = ep;
                            self.gc[o] = cost;
                            self.par[o] = n as u32;
                            self.din[o] = 8;
                            let f = cost + hfun(self, o);
                            self.heap.push(HI { f, n: o as u32 });
                        }
                    }
                }
                }
            }
        }
        None
    }

    /// Indice de largeur du noeud dans la recherche courante (0 si inconnu : noeud de l'arbre).
    #[inline]
    fn wi_of(&self, n: usize) -> usize {
        if self.legm[n] == self.ep && self.legv[n] > 0 {
            ((self.legv[n] - 1) / 2) as usize
        } else {
            0
        }
    }

    /// Chemin -> segments (fusion par direction et largeur), vias, noeuds d'axe, longueurs reduite / en derogation.
    fn geometrie(&self, path: &[u32], l: &Larg, via_final: bool) -> (Vec<SegD>, Vec<ViaD>, Vec<(u32, u8)>, f64, f64) {
        let mut segs = Vec::new();
        let mut vias = Vec::new();
        let mut noeuds = Vec::new();
        let (mut neck, mut derog) = (0.0, 0.0);
        let nc = self.nc;
        let n = path.len();
        for (i, &nd) in path.iter().enumerate() {
            let nd = nd as usize;
            if i == n - 1 && !via_final && n > 1 {
                break; // dernier noeud = arbre existant (deja verifie)
            }
            let mut fl = (self.wi_of(nd) as u8) << 2;
            if self.legm[nd] == self.ep && self.legv[nd] > 0 && (self.legv[nd] - 1) % 2 == 1 {
                fl |= 1; // pose en recouvrement (negociation)
            }
            if (i + 1 < n && path[i + 1] as usize % nc == nd % nc) || (i == n - 1 && via_final) {
                fl |= 2;
                if self.vsoft[nd] == self.ep {
                    fl |= 1; // via pose en recouvrement
                }
            }
            noeuds.push((nd as u32, fl));
        }
        if n < 2 {
            if via_final && n == 1 {
                let (x, y) = self.node_xy(path[0] as usize);
                vias.push(ViaD { x, y });
            }
            return (segs, vias, noeuds, neck, derog);
        }
        let mut start = path[0] as usize;
        let mut dir: Option<(i32, i32)> = None;
        let mut wcur = 0.0;
        let flush = |a: usize, b: usize, w: f64, segs: &mut Vec<SegD>| {
            if a != b {
                let (x1, y1) = self.node_xy(a);
                let (x2, y2) = self.node_xy(b);
                segs.push(SegD { x1, y1, x2, y2, w, couche: (a / nc) as u8 });
            }
        };
        let last = path[n - 1] as usize;
        for i in 1..n {
            let a = path[i - 1] as usize;
            let b = path[i] as usize;
            if a % nc == b % nc {
                flush(start, a, wcur, &mut segs);
                let (x, y) = self.node_xy(a);
                vias.push(ViaD { x, y });
                start = b;
                dir = None;
                continue;
            }
            let (ca, cb) = ((a % nc) as i64, (b % nc) as i64);
            let d = ((cb % self.nx as i64 - ca % self.nx as i64) as i32, (cb / self.nx as i64 - ca / self.nx as i64) as i32);
            let wb = if b == last && !via_final { self.wi_of(a) } else { self.wi_of(b) };
            let wi = self.wi_of(a).max(wb);
            let w = l.w[wi];
            let len = if d.0 != 0 && d.1 != 0 { P * std::f64::consts::SQRT_2 } else { P };
            if wi > 0 {
                neck += len;
            }
            if w < l.mini_regle - 1e-9 {
                derog += len;
            }
            if dir.is_some() && (dir != Some(d) || (w - wcur).abs() > 1e-9) {
                flush(start, a, wcur, &mut segs);
                start = a;
            }
            dir = Some(d);
            wcur = w;
        }
        flush(start, last, wcur, &mut segs);
        if via_final {
            let (x, y) = self.node_xy(last);
            vias.push(ViaD { x, y });
        }
        (segs, vias, noeuds, neck, derog)
    }

    fn commit_geom(&mut self, n: usize, segs: Vec<SegD>, vias: Vec<ViaD>, noeuds: Vec<(u32, u8)>) {
        let key = n as u32 + 1;
        let clr = self.clr_net(n);
        let infl = clr + H_DEF + DELTA;
        let mut store = std::mem::take(&mut self.nets[n].kt);
        for s in &segs {
            self.raster_seg(s, infl, key, &mut store);
            self.nets[n].carres += ((s.x2 - s.x1).powi(2) + (s.y2 - s.y1).powi(2)).sqrt() / s.w;
        }
        for v in &vias {
            let cv = self.node_at(v.x, v.y, 0) as usize;
            self.vias_c[cv] += 1;
            self.maj_trou(cv, 1);
            self.raster_via(v.x, v.y, infl, key, &mut store);
        }
        self.nets[n].kt = store;
        if self.k2.is_some() {
            let mut kp = std::mem::take(&mut self.nets[n].kprox);
            for s in &segs {
                self.raster_prox_seg(s.x1, s.y1, s.x2, s.y2, s.w / 2.0, s.couche as usize, key, &mut kp);
            }
            for v in &vias {
                for l in 0..NCOUCHES {
                    self.raster_prox_seg(v.x, v.y, v.x, v.y, self.m.regles.via.diametre / 2.0, l, key, &mut kp);
                }
            }
            self.nets[n].kprox = kp;
        }
        self.nets[n].segs.extend(segs);
        self.nets[n].vias.extend(vias);
        self.nets[n].noeuds.extend(noeuds);
    }

    /// Cartes de proximite : bande 1 jusqu'a 2 x isolement, bande 2 jusqu'a 3 x isolement (au-dela du cuivre + demi-piste).
    #[allow(clippy::too_many_arguments)]
    fn raster_prox_seg(&mut self, x1: f64, y1: f64, x2: f64, y2: f64, demi: f64, l: usize, key: u32, store: &mut Vec<(u32, u8)>) {
        let clr = self.m.regles.isolement.defaut;
        let r2 = demi + 2.0 * clr + H_DEF;
        let r3 = demi + 3.0 * clr + H_DEF;
        let s = SegD { x1, y1, x2, y2, w: 0.0, couche: l as u8 };
        let x0 = (((x1.min(x2) - r3) / P).floor().max(0.0)) as usize;
        let y0 = (((y1.min(y2) - r3) / P).floor().max(0.0)) as usize;
        let xa = ((((x1.max(x2) + r3) / P).ceil()) as usize).min(self.nx - 1);
        let ya = ((((y1.max(y2) + r3) / P).ceil()) as usize).min(self.ny - 1);
        for iy in y0..=ya {
            for ix in x0..=xa {
                let (x, y) = self.cxy(ix, iy);
                let d = dist_seg(x, y, &s);
                let nd = l * self.nc + iy * self.nx + ix;
                if d < r2 {
                    self.k2.as_mut().unwrap().add(nd, key);
                    store.push((nd as u32, 2));
                }
                if d < r3 {
                    self.k3.as_mut().unwrap().add(nd, key);
                    store.push((nd as u32, 3));
                }
            }
        }
    }

    pub fn arracher(&mut self, n: usize) {
        let key = n as u32 + 1;
        for v in self.nets[n].vias.clone() {
            let cv = self.node_at(v.x, v.y, 0) as usize;
            self.vias_c[cv] -= 1;
            self.maj_trou(cv, -1);
        }
        let kt = std::mem::take(&mut self.nets[n].kt);
        for &c in &kt {
            self.kt.sub(c as usize, key);
        }
        let kp = std::mem::take(&mut self.nets[n].kprox);
        for &(c, b) in &kp {
            if b == 2 {
                self.k2.as_mut().unwrap().sub(c as usize, key);
            } else {
                self.k3.as_mut().unwrap().sub(c as usize, key);
            }
        }
        self.nets[n] = NetR::default();
    }

    fn proprietaires(&self, path: &[u32], ctx: &Ctx, out: &mut Vec<usize>) {
        let key = ctx.key;
        let ajoute = |m: usize, out: &mut Vec<usize>| {
            if self.kt.ok(m, key) {
                return;
            }
            let o = self.kt.owner(m).unwrap_or(self.kt_who[m]);
            if o != key && o != self.nonet && o != 0 {
                let on = (o - 1) as usize;
                if !out.contains(&on) {
                    out.push(on);
                }
            }
        };
        for (pi, &nd) in path.iter().enumerate() {
            let n = nd as usize;
            let c = n % self.nc;
            let l = n / self.nc;
            let via = pi > 0 && (path[pi - 1] as usize) % self.nc == c;
            let (x, y) = ((c % self.nx) as i32, (c / self.nx) as i32);
            let o: &[(i32, i32)] = if via { &ctx.voffs } else { &ctx.offs[0] };
            for &(dx, dy) in o {
                let (u, v) = (x + dx, y + dy);
                if u < 0 || v < 0 || u >= self.nx as i32 || v >= self.ny as i32 {
                    continue;
                }
                let cc = v as usize * self.nx + u as usize;
                if via {
                    for ll in 0..NCOUCHES {
                        ajoute(cc + ll * self.nc, out);
                    }
                } else {
                    ajoute(l * self.nc + cc, out);
                }
            }
        }
    }

    /// Broches d'un reseau ordinaire : pastilles hors bloc + point d'attente du bloc.
    fn broches(&self, n: usize) -> (Vec<Vec<u32>>, Vec<(f64, f64)>, Vec<String>) {
        let mut cells = Vec::new();
        let mut centres = Vec::new();
        let mut noms = Vec::new();
        for &(i, k) in &self.m.nets[n].pins_ext {
            // broche amorcee (eventail / amorce) : on n'arrive QUE par le bout de l'amorce (sinon l'amorce reste en
            // antenne quand le routeur rejoint la pastille directement)
            let pc = match self.pin_extra.get(&(i, k)) {
                Some(&nd) => vec![nd],
                None => self.pad_cells(i, k),
            };
            cells.push(pc);
            let (x, y, _, _) = self.m.pad_world(i, k, &self.e.poses[i]);
            centres.push((x, y));
            noms.push(format!("{}.{}", self.m.comps[i].nom, self.m.comps[i].pads[k].nom));
        }
        for (cg, desc) in &self.cibles_bloc[n] {
            if cg.is_empty() {
                continue;
            }
            cells.push(cg.clone());
            let (sx, sy) = cg.iter().fold((0.0, 0.0), |a, &c| {
                let (x, y) = self.node_xy(c as usize);
                (a.0 + x, a.1 + y)
            });
            centres.push((sx / cg.len() as f64, sy / cg.len() as f64));
            noms.push(format!("cuivre du bloc ({})", desc));
        }
        (cells, centres, noms)
    }

    /// Route un reseau ordinaire. En mode Sonde, ne pose rien et renvoie les reseaux genants.
    fn router_net(&mut self, n: usize, mode: Mode) -> Vec<usize> {
        let ctx = self.ctx(n, mode);
        self.router_net_ctx(n, ctx)
    }

    fn router_net_ctx(&mut self, n: usize, ctx: Ctx) -> Vec<usize> {
        let mode = ctx.mode;
        let lg = self.larg(n);
        let (cells, centres, noms) = self.broches(n);
        let mut genants = Vec::new();
        if cells.len() < 2 {
            return genants;
        }
        // accessibilite des broches (pastilles voisines, bord, emprise : obstacles durs)
        let ctx_acc = self.ctx(n, Mode::Negocie);
        self.ep += 1;
        let acc: Vec<bool> = (0..cells.len()).map(|p| cells[p].iter().any(|&c| self.leg(c as usize, &ctx_acc) != 0)).collect();
        if self.m.param.journal.debug_amorces {
            for p in 0..cells.len() {
                if !acc[p] {
                    let det: Vec<(f64, f64, usize, u8)> = cells[p].iter().take(12).map(|&c| {
                        let (x, y) = self.node_xy(c as usize);
                        (x, y, c as usize / self.nc, self.cell_state(c as usize, ctx_acc.key, true))
                    }).collect();
                    eprintln!("inaccessible {} {} : {} cellules {:?} offs0 {:?}", self.m.nets[n].nom, noms[p], cells[p].len(), det, ctx_acc.offs.last().map(|o| o.len()));
                }
            }
        }
        // racine : la broche accessible la plus large (une broche amorcee, coincee entre des voisines a pas fin, ne
        // doit pas porter tout l'arbre : sinon un seul goulet fait echouer toutes les connexions du reseau)
        let racine = match (0..cells.len()).filter(|&p| acc[p]).max_by_key(|&p| (cells[p].len(), std::cmp::Reverse(p))) {
            Some(r) => r,
            None => 0,
        };
        self.tep += 1;
        let tep = self.tep;
        let mut bb = [usize::MAX, usize::MAX, 0, 0];
        let add_tree = |s: &mut Self, nd: u32, bb: &mut [usize; 4]| {
            s.tree[nd as usize] = tep;
            let c = nd as usize % s.nc;
            let (x, y) = (c % s.nx, c / s.nx);
            bb[0] = bb[0].min(x);
            bb[1] = bb[1].min(y);
            bb[2] = bb[2].max(x);
            bb[3] = bb[3].max(y);
        };
        for &c in &cells[racine] {
            add_tree(self, c, &mut bb);
        }
        let mut connected = vec![racine];
        let mut rest: Vec<usize> = (0..cells.len()).filter(|&p| p != racine).collect();
        {
            let fw = (self.fenetre_mm / P) as i32;
            let (mut x0, mut y0, mut x1, mut y1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
            for c in &centres {
                x0 = x0.min(c.0);
                y0 = y0.min(c.1);
                x1 = x1.max(c.0);
                y1 = y1.max(c.1);
            }
            self.fen = [(x0 / P) as i32 - fw, (y0 / P) as i32 - fw, (x1 / P) as i32 + fw, (y1 / P) as i32 + fw];
        }
        while !rest.is_empty() {
            let mut best = (f64::MAX, 0usize);
            for (ri, &p) in rest.iter().enumerate() {
                for &q in &connected {
                    let d = (centres[p].0 - centres[q].0).abs() + (centres[p].1 - centres[q].1).abs();
                    if d < best.0 {
                        best = (d, ri);
                    }
                }
            }
            let p = rest.swap_remove(best.1);
            let src = cells[p].clone();
            let res = if acc[p] && acc[racine] {
                let fen0 = self.fen;
                let mut r = self.chercher(&src, &ctx, &But::Arbre(bb));
                if r.is_none() && !self.rapide {
                    // repli : fenetre elargie (x3)
                    let fw = (2.0 * self.fenetre_mm / P) as i32;
                    self.fen = [fen0[0] - fw, fen0[1] - fw, fen0[2] + fw, fen0[3] + fw];
                    r = self.chercher(&src, &ctx, &But::Arbre(bb));
                    self.fen = fen0;
                }
                r
            } else {
                None
            };
            match res {
                Some(path) => {
                    connected.push(p);
                    if mode == Mode::Sonde {
                        self.proprietaires(&path, &ctx, &mut genants);
                    } else {
                        let (s, v, nd, neck, derog) = self.geometrie(&path, &lg, false);
                        if neck > 0.0 {
                            self.nets[n].largeur_reduite += 1;
                            self.nets[n].neck_mm += neck;
                            self.nets[n].derog_mm += derog;
                        }
                        self.commit_geom(n, s, v, nd);
                        self.nets[n].ok += 1;
                    }
                    for &nd in &path {
                        add_tree(self, nd, &mut bb);
                    }
                    for &c in &src {
                        add_tree(self, c, &mut bb);
                    }
                }
                None => {
                    if mode != Mode::Sonde {
                        self.nets[n].ko += 1;
                        let cause = if !acc[p] {
                            "broche inaccessible (isolement des pastilles voisines, bord ou emprise du bloc)"
                        } else if !acc[racine] {
                            "aucune broche accessible pour demarrer l'arbre"
                        } else if mode == Mode::Strict {
                            "pas de chemin libre (congestion)"
                        } else {
                            "pas de chemin meme en recouvrement"
                        };
                        self.nets[n].diag.push(format!("{} : {}", noms[p], cause));
                    }
                }
            }
        }
        genants
    }

    /// GND / +8V : pour chaque pastille CMS hors bloc, piste courte + via de descente vers le plan (<= 6 mm).
    fn router_plan(&mut self, n: usize, mode: Mode) -> Vec<usize> {
        let mut pins = self.m.nets[n].pins_ext.clone();
        pins.extend(self.m.nets[n].pins_plan_bloc.iter().copied());
        self.router_plan_pins(n, mode, pins)
    }

    fn router_plan_pins(&mut self, n: usize, mode: Mode, pins: Vec<(usize, usize)>) -> Vec<usize> {
        let mut genants = Vec::new();
        let ctx = self.ctx(n, mode);
        let lg = self.larg(n);
        let rmax = (R_VIA_PLAN / P) as usize;
        // cuivre deja relie au plan : pastilles traversantes du reseau et vias de plan deja poses
        self.tep += 1;
        let tep = self.tep;
        for &(i, k) in &self.m.nets[n].pins {
            if self.m.comps[i].pads[k].th && self.m.comps[i].statut != Statut::Bloc {
                for c in self.pad_cells(i, k) {
                    self.tree[c as usize] = tep;
                }
            }
        }
        for v in self.nets[n].vias.clone() {
            for l in 0..NCOUCHES as u8 {
                let nd = self.node_at(v.x, v.y, l) as usize;
                self.tree[nd] = tep;
            }
        }
        for (i, k) in pins {
            if self.m.comps[i].pads[k].th {
                continue;
            }
            let cells = self.pad_cells(i, k);
            let (cx, cy, _, _) = self.m.pad_world(i, k, &self.e.poses[i]);
            let (ix, iy) = ((cx / P) as usize, (cy / P) as usize);
            match self.chercher(&cells, &ctx, &But::Via(ix, iy, rmax)) {
                Some(path) if mode == Mode::Sonde => {
                    let mut p2 = path.clone();
                    // le via final occupe les trois couches : on le sonde comme un via
                    let last = *path.last().unwrap();
                    p2.push(last);
                    self.proprietaires(&p2, &ctx, &mut genants);
                }
                Some(path) => {
                    let fin = *path.last().unwrap() as usize;
                    let rejoint = self.tree[fin] == tep;
                    let (s, v, nd, neck, _) = self.geometrie(&path, &lg, !rejoint);
                    if neck > 0.0 {
                        self.nets[n].largeur_reduite += 1;
                        self.nets[n].neck_mm += neck;
                    }
                    if !rejoint {
                        for l in 0..NCOUCHES {
                            self.tree[(fin % self.nc) + l * self.nc] = tep;
                        }
                    }
                    self.commit_geom(n, s, v, nd);
                    self.nets[n].ok += 1;
                }
                // via dans la pastille : permis par une regle du fichier (ViasUnderSMD de la piece : pas une
                // derogation), sinon seulement par la derogation via_dans_pastille_meme_reseau
                None if (self.via_dans_pad || self.m.comps[i].via_sous_pad) && self.via_dans_pastille(n, &cells, &ctx) => {
                    self.nets[n].ok += 1;
                    if !self.m.comps[i].via_sous_pad {
                        self.derogations.push(format!("via dans la pastille CMS {}.{} (reseau {}) : aucun emplacement de via a moins de {} mm",
                            self.m.comps[i].nom, self.m.comps[i].pads[k].nom, self.m.nets[n].nom, R_VIA_PLAN));
                    }
                }
                None => {
                    self.nets[n].ko += 1;
                    // diagnostic : cellules de depart admissibles
                    self.ep += 1;
                    let nsrc = cells.iter().filter(|&&c| self.leg(c as usize, &ctx) != 0).count();
                    self.nets[n].diag.push(format!(
                        "{}.{} : pas de place pour un via de plan a moins de {} mm (ni via / pastille traversante du plan joignable) [{} cellule(s) de depart admissibles sur {}]",
                        self.m.comps[i].nom, self.m.comps[i].pads[k].nom, R_VIA_PLAN, nsrc, cells.len()
                    ));
                }
            }
        }
        genants
    }

    /// Dernier recours : via au centre d'une cellule de la pastille (isolement verifie sur les trois couches).
    fn via_dans_pastille(&mut self, n: usize, cells: &[u32], ctx: &Ctx) -> bool {
        let mut cs: Vec<u32> = cells.to_vec();
        if let Some(&c0) = cells.first() {
            let (x0, y0) = self.node_xy(c0 as usize);
            let (mut sx, mut sy) = (0.0, 0.0);
            for &c in cells {
                let (x, y) = self.node_xy(c as usize);
                sx += x - x0;
                sy += y - y0;
            }
            let (gx, gy) = (x0 + sx / cells.len() as f64, y0 + sy / cells.len() as f64);
            cs.sort_by(|&a, &b| {
                let (xa, ya) = self.node_xy(a as usize);
                let (xb, yb) = self.node_xy(b as usize);
                ((xa - gx).powi(2) + (ya - gy).powi(2)).partial_cmp(&((xb - gx).powi(2) + (yb - gy).powi(2))).unwrap()
            });
        }
        for c in cs {
            let cell = c as usize % self.nc;
            let ok = (0..NCOUCHES).all(|l| self.disk_state(cell + l * self.nc, ctx.key, &ctx.voffs, false) == 1);
            if ok {
                let (x, y) = self.node_xy(cell);
                self.commit_geom(n, Vec::new(), vec![ViaD { x, y }], vec![(c, 2)]);
                return true;
            }
        }
        false
    }

    fn router(&mut self, n: usize, mode: Mode) {
        if self.m.nets[n].plan {
            self.router_plan(n, mode);
        } else {
            self.router_net(n, mode);
        }
    }

    /// Noeuds en conflit (recouvrement d'un cuivre etranger) d'un reseau pose.
    /// Noeuds poses en recouvrement qui recouvrent encore un cuivre etranger, et reseaux recouverts.
    /// (Seuls les noeuds poses en recouvrement sont reverifies : un noeud pose libre l'a ete contre tout le
    /// cuivre present, et tout reseau pose apres lui l'a ete contre lui ; cela evite les faux conflits dus aux
    /// marges de discretisation, qui ne sont pas symetriques.)
    fn conflits(&mut self, n: usize) -> (Vec<u32>, Vec<usize>) {
        let ctx = self.ctx(n, Mode::Strict);
        let noeuds = self.nets[n].noeuds.clone();
        let mut out = Vec::new();
        let mut owners = Vec::new();
        for (nd, fl) in noeuds {
            if fl & 1 == 0 {
                continue;
            }
            let nd = nd as usize;
            let wi = ((fl >> 2) as usize).min(ctx.offs.len() - 1);
            let mut ko = self.disk_state(nd, ctx.key, &ctx.offs[wi], false) == 0;
            if fl & 2 != 0 && self.via_state(nd % self.nc, &ctx) == 0 {
                ko = true;
            }
            if ko {
                out.push(nd as u32);
                let c = nd % self.nc;
                let l = nd / self.nc;
                let (x, y) = ((c % self.nx) as i32, (c / self.nx) as i32);
                let offs: &[(i32, i32)] = if fl & 2 != 0 { &ctx.voffs } else { &ctx.offs[wi] };
                for &(dx, dy) in offs {
                    let (u, v) = (x + dx, y + dy);
                    if u < 0 || v < 0 || u >= self.nx as i32 || v >= self.ny as i32 {
                        continue;
                    }
                    let cc = v as usize * self.nx + u as usize;
                    let ls: &[usize] = if fl & 2 != 0 { &[0, 1, 2] } else { std::slice::from_ref(&l) };
                    for &ll in ls {
                        let mm = ll * self.nc + cc;
                        if !self.kt.ok(mm, ctx.key) {
                            let o = self.kt.owner(mm).unwrap_or(self.kt_who[mm]);
                            if o != ctx.key && o != 0 && o != self.nonet {
                                let on = (o - 1) as usize;
                                if !owners.contains(&on) {
                                    owners.push(on);
                                }
                            }
                        }
                    }
                }
            }
        }
        (out, owners)
    }

    fn priorite(&self, n: usize) -> u8 {
        let net = &self.m.nets[n];
        if net.plan {
            return 6;
        }
        // signaux d'un boitier a pas fin (commandes de grille, bootstrap, compensation...) : routes AVANT la puissance,
        // sinon les pistes larges ferment leurs couloirs de sortie (reglage routage.ordre_pas_fin_d_abord = false pour
        // l'ancien ordre)
        let puiss = matches!(net.classe.as_str(), "PUISSANCE_8V" | "ENTREE_12V" | "CANAL_2A" | "COMMUTATION");
        if !puiss && self.m.param.routage.ordre_pas_fin_d_abord && net.pins.iter().any(|&(i, _)| self.m.comps[i].pas_fin) {
            return 0;
        }
        1 + match net.classe.as_str() {
            "PUISSANCE_8V" => 0, // +8V route en premier (plus de plan, iteration 3)
            "ENTREE_12V" => 1,
            "CANAL_2A" => 1,
            "COMMUTATION" => 2,
            "SENSE" => 3,
            "TRIG_50R" => 4,
            "RAILS" => 6,
            _ => 7,
        }
    }

    pub fn a_router(&self) -> Vec<usize> {
        let m = self.m;
        let mut v: Vec<(u8, f64, usize)> = (0..m.nets.len())
            .filter(|&n| {
                let net = &m.nets[n];
                if net.plan {
                    net.pins_ext.iter().chain(net.pins_plan_bloc.iter()).any(|&(i, k)| !m.comps[i].pads[k].th)
                } else {
                    !net.bloc_seul && net.pins_ext.len() + self.cibles_bloc[n].len() >= 2
                }
            })
            .map(|n| (self.priorite(n), m.hpwl_net(n, &self.e), n))
            .collect();
        v.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.partial_cmp(&b.1).unwrap()).then(a.2.cmp(&b.2)));
        v.into_iter().map(|x| x.2).collect()
    }

    /// Negociation PathFinder, passe stricte, puis arrachage-reroutage cible.
    pub fn tout(&mut self, iters_pf: usize, tours: usize, esp: &ParamEsp, journal: &mut dyn FnMut(String)) {
        self.router_tout(iters_pf, tours, journal);
        self.finaliser(esp, journal);
    }

    /// Negociation, passe stricte, arrachage-reroutage (sans les passes de finition).
    pub fn router_tout(&mut self, iters_pf: usize, tours: usize, journal: &mut dyn FnMut(String)) {
        let t0 = std::time::Instant::now();
        let ordre = self.a_router();
        let mut en_conflit: Vec<usize> = ordre.clone();
        for it in 0..iters_pf {
            self.pres = (0.5 * 1.25f32.powi(it as i32)).min(100.0);
            for &n in &en_conflit {
                self.arracher(n);
                self.router(n, Mode::Negocie);
            }
            en_conflit.clear();
            let mut noeuds_conf = 0usize;
            for &n in &ordre {
                let (c, owners) = self.conflits(n);
                if !c.is_empty() {
                    noeuds_conf += c.len();
                    for &nd in &c {
                        self.hist[nd as usize] += HINC;
                    }
                    if !en_conflit.contains(&n) {
                        en_conflit.push(n);
                    }
                    for o in owners {
                        if !en_conflit.contains(&o) {
                            en_conflit.push(o);
                        }
                    }
                }
            }
            // ordre remis en cause : les reseaux en conflit sont reroutes du plus court au plus long
            en_conflit.sort_by(|&a, &b| self.m.hpwl_net(a, &self.e).partial_cmp(&self.m.hpwl_net(b, &self.e)).unwrap().then(a.cmp(&b)));
            journal(format!("  negociation {} : {} reseaux en conflit ({} noeuds), {} connexions impossibles", it + 1, en_conflit.len(), noeuds_conf, self.total_ko()));
            if en_conflit.is_empty() {
                break;
            }
        }
        // passe stricte : les reseaux encore en conflit sont arraches puis reroutes sans recouvrement
        for &n in &en_conflit {
            self.arracher(n);
        }
        let mut ordre_strict: Vec<usize> = ordre.iter().copied().filter(|&n| self.m.nets[n].plan).collect();
        ordre_strict.extend(ordre.iter().copied().filter(|&n| !self.m.nets[n].plan));
        for &n in &ordre_strict {
            if en_conflit.contains(&n) {
                self.router(n, Mode::Strict);
            }
        }
        journal(format!("  passe stricte : {} connexions en echec ({:.0} s depuis le debut)", self.total_ko(), t0.elapsed().as_secs_f64()));
        let mut meilleur = (self.total_ko(), self.instantane());
        for tour in 0..tours {
            let echecs: Vec<usize> = ordre.iter().copied().filter(|&n| self.nets[n].ko > 0 && self.rips[n] < RIPS_MAX).collect();
            if echecs.is_empty() {
                break;
            }
            for n in echecs {
                if self.nets[n].ko == 0 {
                    continue;
                }
                self.arracher(n);
                let plan = self.m.nets[n].plan;
                let mut gen = if plan { self.router_plan(n, Mode::Sonde) } else { self.router_net(n, Mode::Sonde) };
                gen.retain(|&o| o != n && self.rips[o] < RIPS_MAX);
                for &o in &gen {
                    self.arracher(o);
                    self.rips[o] += 1;
                }
                self.rips[n] += 1;
                self.router(n, Mode::Strict);
                for &o in &gen {
                    self.router(o, Mode::Strict);
                }
            }
            journal(format!("  tour d'arrachage {} : {} connexions en echec", tour + 1, self.total_ko()));
            if self.total_ko() < meilleur.0 {
                meilleur = (self.total_ko(), self.instantane());
            }
        }
        if self.total_ko() > meilleur.0 {
            journal(format!("  retour au meilleur etat des tours d'arrachage : {} connexions en echec", meilleur.0));
            self.restaurer_instantane(meilleur.1);
        }
        journal(format!("  fin de l'arrachage ({:.0} s)", t0.elapsed().as_secs_f64()));
        if self.total_ko() > 0 {
            self.reparer_echecs(journal);
        }
    }

    /// Retire les antennes (regle Altium Net Antennae, tolerance 0) : segment dont une extremite ne touche aucun autre
    /// cuivre de son reseau (piste, via, pastille, amorce), et amorce dont le bout n'est rejoint par rien ; iteratif
    /// (une antenne retiree peut en reveler une autre). Ne coupe aucune liaison : le bout libre ne reliait rien.
    pub fn retirer_antennes(&mut self) -> usize {
        let m = self.m;
        let vd = m.regles.via.diametre / 2.0;
        let mut total = 0;
        loop {
            let mut retire = 0;
            for n in 0..self.nets.len() {
                if self.nets[n].segs.is_empty() && !self.amorces.iter().any(|a| a.0 == n) {
                    continue;
                }
                let pads: Vec<(f64, f64, f64, f64, FormePad, Vec<u8>)> = m.nets[n].pins.iter().chain(m.ponts.iter().filter(|p| p.0 == n).map(|p| (p.1, p.3)).collect::<Vec<_>>().iter())
                    .map(|&(i, k)| {
                        let p = self.e.poses[i];
                        let (x, y, hw, hh) = m.pad_world(i, k, &p);
                        let pd = &m.comps[i].pads[k];
                        (x, y, hw, hh, pd.forme, if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] })
                    }).collect();
                let vias: Vec<(f64, f64)> = self.nets[n].vias.iter().map(|v| (v.x, v.y)).collect();
                let amorces: Vec<SegD> = self.amorces.iter().filter(|a| a.0 == n).flat_map(|a| a.1.iter().cloned()).collect();
                let segs = self.nets[n].segs.clone();
                let touche = |x: f64, y: f64, l: u8, moi: Option<usize>, autres: &[SegD], segs: &[SegD]| -> bool {
                    if vias.iter().any(|v| ((v.0 - x).powi(2) + (v.1 - y).powi(2)).sqrt() <= vd + 1e-6) {
                        return true;
                    }
                    if pads.iter().any(|q| q.5.contains(&l) && dist_pad(x, y, q.0, q.1, q.2, q.3, q.4) <= 1e-6) {
                        return true;
                    }
                    for (j, o) in segs.iter().enumerate() {
                        if Some(j) == moi || o.couche != l {
                            continue;
                        }
                        if dist_seg(x, y, o) <= o.w / 2.0 + 1e-6 {
                            return true;
                        }
                    }
                    autres.iter().any(|o| o.couche == l && dist_seg(x, y, o) <= o.w / 2.0 + 1e-6)
                };
                let mut garde = Vec::new();
                let mut change = false;
                for (j, sg) in segs.iter().enumerate() {
                    let l0 = ((sg.x2 - sg.x1).powi(2) + (sg.y2 - sg.y1).powi(2)).sqrt();
                    let ok = l0 > 1e-9 && touche(sg.x1, sg.y1, sg.couche, Some(j), &amorces, &segs) && touche(sg.x2, sg.y2, sg.couche, Some(j), &amorces, &segs);
                    if ok {
                        garde.push(sg.clone());
                    } else {
                        change = true;
                        retire += 1;
                    }
                }
                if change {
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
                    self.commit_geom(n, garde, old.vias.clone(), old.noeuds.clone());
                }
                // amorces dont le bout n'est rejoint par aucune piste / via du reseau : retirees de la sortie, mais
                // seulement si le reseau est complet (un reseau encore en echec sera peut-etre raccorde plus tard)
                if self.nets[n].ko > 0 || !self.est_routable(n) {
                    continue;
                }
                let segs2 = self.nets[n].segs.clone();
                let mut k = 0;
                while k < self.amorces.len() {
                    if self.amorces[k].0 != n {
                        k += 1;
                        continue;
                    }
                    let bout = self.amorces[k].1.last().cloned().unwrap();
                    let autres: Vec<SegD> = Vec::new();
                    if !touche(bout.x2, bout.y2, bout.couche, None, &autres, &segs2) {
                        self.amorces.remove(k);
                        retire += 1;
                    } else {
                        k += 1;
                    }
                }
            }
            total += retire;
            if retire == 0 {
                break;
            }
        }
        total
    }

    /// Controle (et reparation) des descentes de plan : chaque pastille CMS d'un reseau de plan doit toucher une piste
    /// de son reseau (extremite dans la pastille) ou un via dans la pastille ; sinon la descente est reposee.
    /// Renvoie (pastilles sans descente trouvees, reposees avec succes).
    pub fn verifier_plans(&mut self) -> (usize, usize) {
        let m = self.m;
        let vd = m.regles.via.diametre / 2.0;
        let (mut trouvees, mut reposees) = (0, 0);
        for n in 0..self.nets.len() {
            if !m.nets[n].plan {
                continue;
            }
            let mut manquantes = Vec::new();
            for &(i, k) in &m.nets[n].pins_ext {
                let pd = &m.comps[i].pads[k];
                if pd.th {
                    continue;
                }
                let p = self.e.poses[i];
                let (x, y, hw, hh) = m.pad_world(i, k, &p);
                let couche: u8 = if p.bot { 1 } else { 0 };
                let dans = |px: f64, py: f64| dist_pad(px, py, x, y, hw, hh, pd.forme) <= 1e-6;
                let ok = self.nets[n].segs.iter().any(|s| s.couche == couche && (dans(s.x1, s.y1) || dans(s.x2, s.y2)))
                    || self.nets[n].vias.iter().any(|v| dist_pad(v.x, v.y, x, y, hw, hh, pd.forme) <= vd)
                    || m.ponts.iter().any(|q| q.0 == n && q.1 == i && (q.2 == k || q.3 == k));
                if !ok {
                    manquantes.push((i, k));
                }
            }
            if manquantes.is_empty() {
                continue;
            }
            trouvees += manquantes.len();
            let ko0 = self.nets[n].ko;
            self.router_plan_pins(n, Mode::Strict, manquantes.clone());
            let ko1 = self.nets[n].ko;
            reposees += manquantes.len().saturating_sub(ko1.saturating_sub(ko0));
        }
        (trouvees, reposees)
    }

    /// Lissage sans risque : deux segments du meme reseau, meme couche, meme largeur, colineaires et bout a bout, dont
    /// la jonction n'est touchee par rien d'autre (ni troisieme segment, ni via, ni pastille) sont fusionnes. Le cuivre
    /// est identique : aucun effet sur l'isolement ni sur la connexite.
    pub fn fusionner_colineaires(&mut self) -> usize {
        let m = self.m;
        let vd = m.regles.via.diametre / 2.0;
        let mut total = 0;
        for n in 0..self.nets.len() {
            if self.nets[n].segs.len() < 2 {
                continue;
            }
            let pads: Vec<(f64, f64, f64, f64, FormePad, Vec<u8>)> = m.nets[n].pins.iter().map(|&(i, k)| {
                let p = self.e.poses[i];
                let (x, y, hw, hh) = m.pad_world(i, k, &p);
                let pd = &m.comps[i].pads[k];
                (x, y, hw, hh, pd.forme, if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] })
            }).collect();
            let vias: Vec<(f64, f64)> = self.nets[n].vias.iter().map(|v| (v.x, v.y)).collect();
            let mut segs = self.nets[n].segs.clone();
            let mut fusions = 0;
            loop {
                let mut fait = false;
                'ext: for a in 0..segs.len() {
                    for (ax, ay) in [(segs[a].x1, segs[a].y1), (segs[a].x2, segs[a].y2)] {
                        let l = segs[a].couche;
                        let proches = |x: f64, y: f64, sx: f64, sy: f64| (x - sx).abs() < 1e-6 && (y - sy).abs() < 1e-6;
                        let lies: Vec<usize> = (0..segs.len()).filter(|&b| segs[b].couche == l
                            && (proches(segs[b].x1, segs[b].y1, ax, ay) || proches(segs[b].x2, segs[b].y2, ax, ay))).collect();
                        if lies.len() != 2 {
                            continue;
                        }
                        // jonction touchee par un autre segment (en son milieu), un via ou une pastille : on garde
                        let milieu = (0..segs.len()).any(|b| !lies.contains(&b) && segs[b].couche == l && dist_seg(ax, ay, &segs[b]) <= segs[b].w / 2.0 + 1e-6);
                        if milieu || vias.iter().any(|v| ((v.0 - ax).powi(2) + (v.1 - ay).powi(2)).sqrt() <= vd + 1e-6)
                            || pads.iter().any(|q| q.5.contains(&l) && dist_pad(ax, ay, q.0, q.1, q.2, q.3, q.4) <= 1e-6) {
                            continue;
                        }
                        let b = if lies[0] == a { lies[1] } else { lies[0] };
                        if (segs[a].w - segs[b].w).abs() > 1e-9 {
                            continue;
                        }
                        let autre = |s: &SegD| if proches(s.x1, s.y1, ax, ay) { (s.x2, s.y2) } else { (s.x1, s.y1) };
                        let (pa, pb) = (autre(&segs[a]), autre(&segs[b]));
                        let (ux, uy) = (pa.0 - ax, pa.1 - ay);
                        let (vx, vy) = (pb.0 - ax, pb.1 - ay);
                        let (nu, nv) = ((ux * ux + uy * uy).sqrt(), (vx * vx + vy * vy).sqrt());
                        if nu < 1e-9 || nv < 1e-9 || (ux * vx + uy * vy) / (nu * nv) > -0.99999 {
                            continue;
                        }
                        let w = segs[a].w;
                        let neuf = SegD { x1: pa.0, y1: pa.1, x2: pb.0, y2: pb.1, w, couche: l };
                        let (i0, i1) = (a.max(b), a.min(b));
                        segs.remove(i0);
                        segs.remove(i1);
                        segs.push(neuf);
                        fusions += 1;
                        fait = true;
                        break 'ext;
                    }
                }
                if !fait {
                    break;
                }
            }
            if fusions > 0 {
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
                total += fusions;
            }
        }
        total
    }

    /// Arrachage-reroutage cible des connexions en echec : le reseau en echec d'abord, puis les reseaux qui le genent
    /// (trouves par sondage, plans GND proteges) ; garde seulement si le total d'echecs baisse. Generique.
    pub fn reparer_echecs(&mut self, journal: &mut dyn FnMut(String)) {
        let avant = self.total_ko();
        self.proteger_plans = true;
        for tour in 0..4 {
            let echecs: Vec<usize> = (0..self.nets.len()).filter(|&n| self.nets[n].ko > 0 && !self.m.nets[n].plan).collect();
            if echecs.is_empty() {
                break;
            }
            let mut progres = false;
            for n in echecs {
                let k0 = self.total_ko();
                let (_, olds) = self.rerouter_opt(&[n], true);
                if self.total_ko() < k0 {
                    progres = true;
                } else {
                    self.annuler(olds);
                }
            }
            journal(format!("  reparation ciblee, tour {} : {} connexions en echec", tour + 1, self.total_ko()));
            if !progres {
                break;
            }
        }
        self.proteger_plans = false;
        if self.total_ko() != avant {
            self.scinder_segments();
            self.retirer_antennes();
        }
    }

    /// Reparation regionale bornee (lot F, avenant B.2) : pour une connexion encore en echec, tous les reseaux qui
    /// passent dans une fenetre autour des pastilles du reseau fautif sont arraches avec lui ; le reseau fautif est
    /// reroute le premier (son acces et son site de via sont ainsi reserves), puis les autres par priorite, avec une
    /// passe d'arrachage cible. L'essai n'est garde que s'il diminue le nombre de connexions en echec, sinon il est
    /// annule exactement. Au plus `etapes` essais ; fenetres de 1,5, 3 et 5 mm, ordre des victimes direct puis inverse.
    pub fn reparer_regional(&mut self, etapes: usize, max_victimes: usize, journal: &mut dyn FnMut(String)) -> usize {
        let avant = self.total_ko();
        if avant == 0 || etapes == 0 {
            return 0;
        }
        self.proteger_plans = true;
        let rayons = [1.5f64, 3.0, 5.0];
        let mut essais = 0usize;
        let mut gardes = 0usize;
        'tours: for tentative in 0..(rayons.len() * 2) {
            let r = rayons[tentative % rayons.len()];
            let inverse = tentative >= rayons.len();
            let echecs: Vec<usize> = (0..self.nets.len()).filter(|&n| self.nets[n].ko > 0 && self.est_routable(n)).collect();
            if echecs.is_empty() {
                break;
            }
            for n in echecs {
                if essais >= etapes {
                    break 'tours;
                }
                if self.nets[n].ko == 0 {
                    continue;
                }
                essais += 1;
                // fenetre : disques de rayon r autour des pastilles du reseau fautif
                let centres: Vec<(f64, f64)> = self.m.nets[n].pins.iter().map(|&(i, k)| {
                    let (x, y, _, _) = self.m.pad_world(i, k, &self.e.poses[i]);
                    (x, y)
                }).collect();
                let r2 = r * r;
                let mut victimes: Vec<(f64, usize)> = Vec::new();
                for o in 0..self.nets.len() {
                    if o == n || !self.est_routable(o) || self.m.nets[o].plan || self.nets[o].noeuds.is_empty() {
                        continue;
                    }
                    let mut dmin = f64::MAX;
                    for &(nd, _) in &self.nets[o].noeuds {
                        let (x, y) = self.node_xy(nd as usize);
                        for &(cx, cy) in &centres {
                            dmin = dmin.min((x - cx).powi(2) + (y - cy).powi(2));
                        }
                    }
                    if dmin <= r2 {
                        victimes.push((dmin, o));
                    }
                }
                victimes.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
                victimes.truncate(max_victimes);
                let mut ordre: Vec<usize> = victimes.iter().map(|v| v.1).collect();
                ordre.sort_by_key(|&o| (self.priorite(o), o));
                if inverse {
                    ordre.reverse();
                }
                let k0 = self.total_ko();
                let mut olds: Vec<(usize, NetR)> = vec![(n, self.nets[n].clone())];
                self.arracher(n);
                for &o in &ordre {
                    olds.push((o, self.nets[o].clone()));
                    self.arracher(o);
                }
                self.router(n, Mode::Strict);
                for &o in &ordre {
                    self.router(o, Mode::Strict);
                }
                // passe d'arrachage cible sur les victimes restees en echec (sans toucher au reseau fautif)
                let restes: Vec<usize> = ordre.iter().copied().filter(|&o| self.nets[o].ko > 0).collect();
                if !restes.is_empty() {
                    let (_, o2) = self.rerouter_opt(&restes, true);
                    for x in o2 {
                        if !olds.iter().any(|y| y.0 == x.0) {
                            olds.push(x);
                        }
                    }
                }
                if self.total_ko() < k0 {
                    gardes += 1;
                    journal(format!("  reparation regionale : {} (fenetre {} mm, {} reseaux) : {} -> {} connexions en echec",
                        self.m.nets[n].nom, r, ordre.len(), k0, self.total_ko()));
                } else {
                    self.annuler(olds);
                }
            }
        }
        self.proteger_plans = false;
        if gardes > 0 {
            self.scinder_segments();
            self.retirer_antennes();
        }
        journal(format!("  reparation regionale : {} essai(s), {} garde(s) ; {} -> {} connexions en echec",
            essais, gardes, avant, self.total_ko()));
        gardes
    }

    /// Passes de finition : espacement, vias doubles, angles, elargissement, coupure aux jonctions, controle.
    pub fn finaliser(&mut self, esp: &ParamEsp, journal: &mut dyn FnMut(String)) {
        let t0 = std::time::Instant::now();
        let ordre = self.a_router();
        if esp.actif {
            let avant = self.bilan_espacement(esp);
            let (acc, tot) = self.espacer(esp);
            let apres = self.bilan_espacement(esp);
            journal(format!(
                "  espacement : {} reseaux ameliores sur {} ; score {:.1} -> {:.1} ; longueur {:.1} -> {:.1} mm ; segments {} -> {}",
                acc, tot, avant.0, apres.0, avant.1, apres.1, avant.2, apres.2
            ));
        }
        journal(format!("  fin de l'espacement ({:.0} s)", t0.elapsed().as_secs_f64()));
        let nd = self.doubler_vias();
        journal(format!("  vias doubles poses (CANAL_2A / ENTREE_12V) : {}", nd));
        let na = self.nettoyer_angles();
        journal(format!("  troncons courts en angle aigu supprimes : {}", na));
        let (ne, le) = self.elargir();
        journal(format!("  elargissement final : {} troncons rendus plus larges ({:.1} mm)", ne, le));
        let ns = self.scinder_segments();
        journal(format!("  pistes coupees aux jonctions (via ou extremite sur l'axe) : {}", ns));
        let na = self.retirer_antennes();
        journal(format!("  antennes retirees (bouts de piste ne touchant aucun cuivre de leur reseau) : {}", na));
        let (np, nr) = self.verifier_plans();
        journal(format!("  pastilles CMS de plan sans descente : {} (reposees : {})", np, nr));
        let nf = self.fusionner_colineaires();
        journal(format!("  segments colineaires fusionnes : {}", nf));
        // controle final : aucun recouvrement ne doit subsister
        let restants: usize = ordre.iter().map(|&n| self.conflits(n).0.len()).sum();
        journal(format!("  recouvrements residuels : {} noeuds", restants));
    }

    /// Facteur de sensibilite du terme de proximite (le noeud SW est entierement dans le bloc : pas de terme agresseur).
    fn facteur_prox(&self, n: usize) -> f64 {
        let net = &self.m.nets[n];
        if net.nom.starts_with("SEG") {
            0.3
        } else if net.classe == "SENSE" || net.classe == "TRIG_50R" || net.nom.ends_with("Vref") {
            2.0
        } else {
            1.0
        }
    }

    fn longueur(&self, n: usize) -> f64 {
        self.nets[n].segs.iter().map(|s| ((s.x2 - s.x1).powi(2) + (s.y2 - s.y1).powi(2)).sqrt()).sum()
    }

    /// Score d'espacement d'un reseau pose : longueur + proximite ponderee + coudes.
    fn score_esp(&self, n: usize, p: &ParamEsp) -> f64 {
        let key = n as u32 + 1;
        let (k2, k3) = (self.k2.as_ref().unwrap(), self.k3.as_ref().unwrap());
        let mut prox = 0.0;
        for &(nd, _) in &self.nets[n].noeuds {
            let nd = nd as usize;
            if !k2.ok(nd, key) {
                prox += P;
            } else if !k3.ok(nd, key) {
                prox += 0.4 * P;
            }
        }
        let coudes = self.nets[n].segs.len().saturating_sub(self.nets[n].ok) as f64;
        self.longueur(n) + p.prox * self.facteur_prox(n) * prox + p.coude * coudes
    }

    fn restaurer(&mut self, n: usize, old: NetR) {
        let mut base = old.clone();
        base.segs.clear();
        base.vias.clear();
        base.noeuds.clear();
        base.kt.clear();
        base.kprox.clear();
        base.carres = 0.0;
        self.nets[n] = base;
        self.commit_geom(n, old.segs, old.vias, old.noeuds);
    }

    pub fn activer_prox(&mut self) {
        if self.k2.is_some() {
            return;
        }
        let nn = NCOUCHES * self.nc;
        self.k2 = Some(KMap::new(nn));
        self.k3 = Some(KMap::new(nn));
        let mut poubelle = Vec::new();
        for i in 0..self.m.comps.len() {
            let p = self.e.poses[i];
            for k in 0..self.m.comps[i].pads.len() {
                let (cx, cy, hw, hh) = self.m.pad_world(i, k, &p);
                let pd = &self.m.comps[i].pads[k];
                let key = pd.net.map(|n| n as u32 + 1).unwrap_or(self.nonet);
                let layers: Vec<usize> = if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] };
                for l in layers {
                    if hw >= hh {
                        self.raster_prox_seg(cx - hw + hh, cy, cx + hw - hh, cy, hh, l, key, &mut poubelle);
                    } else {
                        self.raster_prox_seg(cx, cy - hh + hw, cx, cy + hh - hw, hw, l, key, &mut poubelle);
                    }
                }
                poubelle.clear();
            }
        }
        for n in 0..self.nets.len() {
            let segs = self.nets[n].segs.clone();
            let vias = self.nets[n].vias.clone();
            let key = n as u32 + 1;
            let mut kp = Vec::new();
            for s in &segs {
                self.raster_prox_seg(s.x1, s.y1, s.x2, s.y2, s.w / 2.0, s.couche as usize, key, &mut kp);
            }
            for v in &vias {
                for l in 0..NCOUCHES {
                    self.raster_prox_seg(v.x, v.y, v.x, v.y, self.m.regles.via.diametre / 2.0, l, key, &mut kp);
                }
            }
            self.nets[n].kprox = kp;
        }
    }

    /// (score total, longueur totale, segments) des reseaux de signal.
    pub fn bilan_espacement(&mut self, p: &ParamEsp) -> (f64, f64, usize) {
        self.activer_prox();
        let mut t = (0.0, 0.0, 0usize);
        for n in 0..self.nets.len() {
            if self.m.nets[n].plan || self.nets[n].ok == 0 {
                continue;
            }
            t.0 += self.score_esp(n, p);
            t.1 += self.longueur(n);
            t.2 += self.nets[n].segs.len();
        }
        t
    }

    /// Passe d'espacement : chaque reseau est rerouté seul avec le terme de proximite ; garde si meilleur,
    /// sans echec supplementaire et sans allonger de plus de allong_max.
    fn espacer(&mut self, p: &ParamEsp) -> (usize, usize) {
        self.activer_prox();
        for h in self.hist.iter_mut() {
            *h = 0.0;
        }
        self.pres = 0.0;
        let ordre: Vec<usize> = self.a_router().into_iter().filter(|&n| !self.m.nets[n].plan && self.nets[n].ok > 0).collect();
        let mut acc = 0;
        for &n in &ordre {
            let avant = self.score_esp(n, p);
            let l0 = self.longueur(n);
            let ko0 = self.nets[n].ko;
            let ok0 = self.nets[n].ok;
            let old = self.nets[n].clone();
            self.arracher(n);
            let mut ctx = self.ctx(n, Mode::Strict);
            ctx.prox = (p.prox * self.facteur_prox(n)) as f32;
            ctx.coude = p.coude as f32;
            self.router_net_ctx(n, ctx);
            let garde = self.nets[n].ko <= ko0
                && self.nets[n].ok >= ok0
                && self.longueur(n) <= l0 * (1.0 + p.allong_max) + 0.5
                && self.score_esp(n, p) < avant - 1e-6
                && self.conflits(n).0.is_empty();
            if garde {
                acc += 1;
            } else {
                self.arracher(n);
                self.restaurer(n, old);
            }
        }
        (acc, ordre.len())
    }

    /// Double chaque via des reseaux CANAL_2A / ENTREE_12V par un second via a 0,8 mm relie sur les deux couches.
    fn doubler_vias(&mut self) -> usize {
        let mut total = 0;
        for n in 0..self.nets.len() {
            let cl = self.m.nets[n].classe.as_str();
            let gnd_puiss = self.m.bloc_libre && self.m.nets[n].nom == "GND";
            if cl != "CANAL_2A" && cl != "ENTREE_12V" && !(self.m.bloc_libre && (cl == "COMMUTATION" || cl == "PUISSANCE_8V")) && !gnd_puiss {
                continue;
            }
            let mut vias = self.nets[n].vias.clone();
            if gnd_puiss {
                // descentes GND des pieces de puissance du convertisseur seulement (plusieurs amperes)
                let m = self.m;
                let mut pts: Vec<(f64, f64)> = Vec::new();
                for &i in &m.hacheur_puissance {
                    for k in 0..m.comps[i].pads.len() {
                        if m.comps[i].pads[k].net == Some(n) {
                            let (x, y, _, _) = m.pad_world(i, k, &self.e.poses[i]);
                            pts.push((x, y));
                        }
                    }
                }
                vias.retain(|v| pts.iter().any(|p| (p.0 - v.x).abs() < 2.5 && (p.1 - v.y).abs() < 2.5));
            }
            let lg = self.larg(n);
            for v in vias {
                let ctx = self.ctx(n, Mode::Strict);
                let c0 = self.node_at(v.x, v.y, 0) as usize;
                let (x0, y0) = ((c0 % self.nx) as i32, (c0 / self.nx) as i32);
                let mut fait = false;
                for &(dx, dy) in &[(8, 0), (-8, 0), (0, 8), (0, -8), (6, 6), (-6, 6), (6, -6), (-6, -6)] {
                    let (u, w) = (x0 + dx, y0 + dy);
                    if u < 0 || w < 0 || u >= self.nx as i32 || w >= self.ny as i32 {
                        continue;
                    }
                    let c1 = w as usize * self.nx + u as usize;
                    if self.via_state(c1, &ctx) != 1 {
                        continue;
                    }
                    // pas d'angle aigu : la liaison part a au moins 90 degres des pistes qui arrivent sur le via
                    let (vx, vy) = self.node_xy(c0);
                    let aigu = self.nets[n].segs.iter().any(|sg| {
                        let (ox, oy) = if (sg.x1 - vx).abs() < 1e-6 && (sg.y1 - vy).abs() < 1e-6 {
                            (sg.x2 - sg.x1, sg.y2 - sg.y1)
                        } else if (sg.x2 - vx).abs() < 1e-6 && (sg.y2 - vy).abs() < 1e-6 {
                            (sg.x1 - sg.x2, sg.y1 - sg.y2)
                        } else {
                            return false;
                        };
                        ox * dx as f64 + oy * dy as f64 > 1e-9
                    });
                    if aigu {
                        continue;
                    }
                    // largeur de liaison : la plus large qui passe sur tout le trajet, sur les deux couches
                    let pas = dx.abs().max(dy.abs());
                    let (sx, sy) = (dx.signum(), dy.signum());
                    let mut wi_ok = None;
                    for wi in 0..ctx.offs.len() {
                        let mut ok = true;
                        for t in 1..pas {
                            let cc = (y0 + sy * t) as usize * self.nx + (x0 + sx * t) as usize;
                            for l in 0..2 {
                                if self.disk_state(l * self.nc + cc, ctx.key, &ctx.offs[wi], false) != 1 {
                                    ok = false;
                                }
                            }
                        }
                        if ok {
                            wi_ok = Some(wi);
                            break;
                        }
                    }
                    let wi = match wi_ok {
                        Some(w) => w,
                        None => continue,
                    };
                    let (x1, y1) = self.node_xy(c1);
                    let (xa, ya) = self.node_xy(c0);
                    let segs = vec![
                        SegD { x1: xa, y1: ya, x2: x1, y2: y1, w: lg.w[wi], couche: 0 },
                        SegD { x1: xa, y1: ya, x2: x1, y2: y1, w: lg.w[wi], couche: 1 },
                    ];
                    let mut nd = Vec::new();
                    for t in 1..=pas {
                        let cc = (y0 + sy * t) as usize * self.nx + (x0 + sx * t) as usize;
                        for l in 0..2 {
                            nd.push(((l * self.nc + cc) as u32, ((wi as u8) << 2) | if t == pas && l == 0 { 2 } else { 0 }));
                        }
                    }
                    self.commit_geom(n, segs, vec![ViaD { x: x1, y: y1 }], nd);
                    self.nets[n].vias_doubles += 1;
                    total += 1;
                    fait = true;
                    break;
                }
                let _ = fait;
            }
        }
        total
    }

    /// Lissage partiel : a une jonction de deux segments du meme reseau formant un angle aigu, supprime le
    /// segment court (<= 0,35 mm) si son autre extremite reste dans le cuivre du segment long (connexion conservee).
    fn nettoyer_angles(&mut self) -> usize {
        let mut total = 0;
        for n in 0..self.nets.len() {
            loop {
                let segs = &self.nets[n].segs;
                let mut ends: std::collections::BTreeMap<(i64, i64, u8), Vec<(usize, bool)>> = std::collections::BTreeMap::new();
                for (k, s) in segs.iter().enumerate() {
                    ends.entry(((s.x1 * 1000.0).round() as i64, (s.y1 * 1000.0).round() as i64, s.couche)).or_default().push((k, true));
                    ends.entry(((s.x2 * 1000.0).round() as i64, (s.y2 * 1000.0).round() as i64, s.couche)).or_default().push((k, false));
                }
                let mut retirer = None;
                for v in ends.values() {
                    if v.len() != 2 {
                        continue;
                    }
                    let dirv = |(k, debut): (usize, bool)| {
                        let s = &segs[k];
                        if debut {
                            (s.x2 - s.x1, s.y2 - s.y1)
                        } else {
                            (s.x1 - s.x2, s.y1 - s.y2)
                        }
                    };
                    let (a, b) = (dirv(v[0]), dirv(v[1]));
                    let (la, lb) = ((a.0 * a.0 + a.1 * a.1).sqrt(), (b.0 * b.0 + b.1 * b.1).sqrt());
                    if la == 0.0 || lb == 0.0 || (a.0 * b.0 + a.1 * b.1) / (la * lb) <= 0.01 {
                        continue;
                    }
                    let (court, long) = if la <= lb { (v[0], v[1]) } else { (v[1], v[0]) };
                    let sc = &segs[court.0];
                    if sc.w.min(segs[long.0].w) <= 0.0 || la.min(lb) > 0.35 {
                        continue;
                    }
                    let (fx, fy) = if court.1 { (sc.x2, sc.y2) } else { (sc.x1, sc.y1) };
                    let sl = &segs[long.0];
                    if dist_seg(fx, fy, sl) <= sl.w.min(sc.w) / 2.0 - 1e-6 {
                        retirer = Some(court.0);
                        break;
                    }
                }
                match retirer {
                    Some(k) => {
                        // le cuivre retire reste compte dans la carte d'isolement (prudent)
                        self.nets[n].segs.remove(k);
                        total += 1;
                    }
                    None => break,
                }
            }
        }
        total
    }

    /// Coupe chaque piste aux points de son axe ou arrive un via ou une extremite d'une autre piste du meme
    /// reseau (meme couche) : chaque jonction devient une extremite commune (connexion sure dans Altium).
    pub fn scinder_segments(&mut self) -> usize {
        let mut total = 0;
        for n in 0..self.nets.len() {
            if self.nets[n].segs.is_empty() {
                continue;
            }
            let segs = self.nets[n].segs.clone();
            let vias: Vec<(f64, f64)> = self.nets[n].vias.iter().map(|v| (v.x, v.y)).collect();
            // pastilles du reseau : une piste qui les traverse sans s'y arreter est coupee au plus pres de leur centre
            // (contact franc : extremite d'axe dans le cuivre, et non simple recouvrement)
            let m = self.m;
            let pads_n: Vec<(f64, f64, f64, f64, FormePad, Vec<u8>)> = m.nets[n].pins.iter().map(|&(i, k)| {
                let p = self.e.poses[i];
                let (x, y, hw, hh) = m.pad_world(i, k, &p);
                let pd = &m.comps[i].pads[k];
                let l: Vec<u8> = if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] };
                (x, y, hw, hh, pd.forme, l)
            }).collect();
            let mut out = Vec::new();
            for (k, sg) in segs.iter().enumerate() {
                let (dx, dy) = (sg.x2 - sg.x1, sg.y2 - sg.y1);
                let l2 = dx * dx + dy * dy;
                let mut ts: Vec<f64> = Vec::new();
                let essai = |px: f64, py: f64, ts: &mut Vec<f64>| {
                    if l2 <= 0.0 {
                        return;
                    }
                    let t = ((px - sg.x1) * dx + (py - sg.y1) * dy) / l2;
                    if t <= 1e-6 || t >= 1.0 - 1e-6 {
                        return;
                    }
                    let (qx, qy) = (sg.x1 + t * dx, sg.y1 + t * dy);
                    if ((qx - px).powi(2) + (qy - py).powi(2)).sqrt() < 1e-6 {
                        ts.push(t);
                    }
                };
                for &(x, y) in &vias {
                    essai(x, y, &mut ts);
                }
                for (px, py, hw, hh, fo, ls) in &pads_n {
                    if !ls.contains(&sg.couche) || l2 <= 0.0 {
                        continue;
                    }
                    if dist_pad(sg.x1, sg.y1, *px, *py, *hw, *hh, *fo) <= 0.0 || dist_pad(sg.x2, sg.y2, *px, *py, *hw, *hh, *fo) <= 0.0 {
                        continue;
                    }
                    let t = ((px - sg.x1) * dx + (py - sg.y1) * dy) / l2;
                    if t <= 1e-6 || t >= 1.0 - 1e-6 {
                        continue;
                    }
                    let (qx, qy) = (sg.x1 + t * dx, sg.y1 + t * dy);
                    if dist_pad(qx, qy, *px, *py, *hw, *hh, *fo) < -1e-4 {
                        ts.push(t);
                    }
                }
                for (j, o) in segs.iter().enumerate() {
                    if j != k && o.couche == sg.couche {
                        essai(o.x1, o.y1, &mut ts);
                        essai(o.x2, o.y2, &mut ts);
                    }
                }
                if ts.is_empty() {
                    out.push(sg.clone());
                    continue;
                }
                ts.sort_by(|a, b| a.partial_cmp(b).unwrap());
                ts.dedup_by(|a, b| (*a - *b).abs() < 1e-9);
                let mut pts = vec![(sg.x1, sg.y1)];
                for t in &ts {
                    // points de jonction : centres de cellule exacts (recalcules sur l'axe)
                    pts.push((sg.x1 + t * dx, sg.y1 + t * dy));
                }
                pts.push((sg.x2, sg.y2));
                for w in pts.windows(2) {
                    out.push(SegD { x1: w[0].0, y1: w[0].1, x2: w[1].0, y2: w[1].1, w: sg.w, couche: sg.couche });
                }
                total += ts.len();
            }
            self.nets[n].segs = out;
        }
        total
    }

    /// Elargissement final : un troncon plus etroit que la largeur preferee (retreci a cause d'un obstacle qui a pu
    /// disparaitre depuis : arrachage, reroutage, espacement) reprend la plus grande largeur admissible sur toute sa
    /// longueur contre le cuivre final (memes tests d'isolement que la recherche). Reseau par reseau, puis re-trame.
    fn elargir(&mut self) -> (usize, f64) {
        let (mut nb, mut long) = (0usize, 0.0f64);
        for n in 0..self.nets.len() {
            if self.nets[n].segs.is_empty() {
                continue;
            }
            let ctx = self.ctx(n, Mode::Strict);
            let lg = self.larg(n);
            let mut segs = self.nets[n].segs.clone();
            let mut change = false;
            for sg in segs.iter_mut() {
                let wi0 = match lg.w.iter().position(|&w| (w - sg.w).abs() < 1e-9) {
                    Some(i) => i,
                    None => continue,
                };
                if wi0 == 0 {
                    continue;
                }
                let l = sg.couche as usize;
                let len = ((sg.x2 - sg.x1).powi(2) + (sg.y2 - sg.y1).powi(2)).sqrt();
                let k = ((len / (P / 2.0)).ceil() as usize).max(1);
                for wi in 0..wi0 {
                    let mut ok = true;
                    for t in 0..=k {
                        let u = t as f64 / k as f64;
                        let (x, y) = (sg.x1 + u * (sg.x2 - sg.x1), sg.y1 + u * (sg.y2 - sg.y1));
                        let nd = self.node_at(x, y, l as u8) as usize;
                        if self.disk_state(nd, ctx.key, &ctx.offs[wi], false) != 1 {
                            ok = false;
                            break;
                        }
                    }
                    if ok {
                        sg.w = lg.w[wi];
                        nb += 1;
                        long += len;
                        change = true;
                        break;
                    }
                }
            }
            if change {
                // re-trame du reseau avec ses nouvelles largeurs
                let old = self.nets[n].clone();
                self.arracher(n);
                let mut base = old.clone();
                base.segs.clear();
                base.vias.clear();
                base.noeuds.clear();
                base.kt.clear(); // deja retire par arracher (sinon double retrait au prochain arrachage)
                base.kprox.clear();
                base.carres = 0.0;
                self.nets[n] = base;
                self.commit_geom(n, segs, old.vias.clone(), old.noeuds.clone());
            }
        }
        (nb, long)
    }

    fn instantane(&self) -> (KMap, Vec<u32>, Vec<NetR>, Vec<u16>) {
        (self.kt.clone(), self.kt_who.clone(), self.nets.clone(), self.vias_c.clone())
    }

    fn restaurer_instantane(&mut self, i: (KMap, Vec<u32>, Vec<NetR>, Vec<u16>)) {
        self.kt = i.0;
        self.kt_who = i.1;
        self.nets = i.2;
        self.vias_c = i.3;
        self.recalculer_trou();
    }

    /// Carte d'exclusion trou a trou tenue a jour : `trou_cnt[c]` = somme des `vias_c` sur le disque `offs_trou`
    /// autour de `c` (sans le cache : rien a tenir).
    fn maj_trou(&mut self, cv: usize, delta: i32) {
        if !self.cache_via {
            return;
        }
        let (x, y) = ((cv % self.nx) as i32, (cv / self.nx) as i32);
        for k in 0..self.offs_trou.len() {
            let (dx, dy) = self.offs_trou[k];
            let (u, v) = (x - dx, y - dy);
            if u >= 0 && v >= 0 && u < self.nx as i32 && v < self.ny as i32 {
                let c = v as usize * self.nx + u as usize;
                self.trou_cnt[c] = (self.trou_cnt[c] as i64 + delta as i64) as u32;
            }
        }
    }

    fn recalculer_trou(&mut self) {
        if !self.cache_via {
            return;
        }
        for v in self.trou_cnt.iter_mut() {
            *v = 0;
        }
        for c in 0..self.nc {
            for _ in 0..self.vias_c[c] {
                self.maj_trou(c, 1);
            }
        }
    }

    pub fn total_ko(&self) -> usize {
        self.nets.iter().map(|r| r.ko).sum()
    }
    pub fn total_ok(&self) -> usize {
        self.nets.iter().map(|r| r.ok).sum()
    }
}

impl Modele {
    pub fn hpwl_net(&self, n: usize, e: &Etat) -> f64 {
        let mut r = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
        for &(i, k) in &self.nets[n].pins {
            let (x, y, _, _) = self.pad_world(i, k, &e.poses[i]);
            r = [r[0].min(x), r[1].min(y), r[2].max(x), r[3].max(y)];
        }
        if r[0] > r[2] {
            0.0
        } else {
            r[2] - r[0] + r[3] - r[1]
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::globalr::tests::{comp, donnees, pad};
    use crate::parametres::Parametres;

    fn esp_off() -> ParamEsp {
        ParamEsp { actif: false, prox: 0.0, coude: 0.0, allong_max: 0.0 }
    }

    #[test]
    fn liaison_simple_sans_violation() {
        let d = donnees(vec![comp("A", 4.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)]), comp("B", 12.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)])], vec![]);
        let m = Modele::new(&d, Parametres::defaut());
        let e = m.etat_v3();
        let mut dt = Detail::new(&m, &e);
        dt.tout(5, 0, &esp_off(), &mut |_| {});
        let n = m.nets.iter().position(|n| n.nom == "S").unwrap();
        assert_eq!(dt.nets[n].ko, 0);
        assert_eq!(dt.nets[n].ok, 1);
        let l: f64 = dt.nets[n].segs.iter().map(|s| ((s.x2 - s.x1).powi(2) + (s.y2 - s.y1).powi(2)).sqrt()).sum();
        assert!(l > 7.0 && l < 8.1, "longueur {}", l);
        assert!(dt.nets[n].segs.iter().all(|s| (s.w - 0.3).abs() < 1e-9));
        assert!(dt.nets[n].vias.is_empty());
    }

    #[test]
    fn via_de_plan() {
        let d = donnees(vec![comp("A", 4.25, 5.25, false, vec![pad("1", "GND", 0.0, 0.0, false)])], vec![]);
        let m = Modele::new(&d, Parametres::defaut());
        let e = m.etat_v3();
        let mut dt = Detail::new(&m, &e);
        dt.tout(5, 0, &esp_off(), &mut |_| {});
        let n = m.nets.iter().position(|n| n.nom == "GND").unwrap();
        assert_eq!(dt.nets[n].ok, 1);
        assert_eq!(dt.nets[n].vias.len(), 1);
        // le via ne recouvre pas la pastille (pas de via dans une pastille CMS)
        let v = &dt.nets[n].vias[0];
        let dx = ((v.x - 4.25).abs() - 0.3).max(0.0);
        let dy = ((v.y - 5.25).abs() - 0.3).max(0.0);
        assert!((dx * dx + dy * dy).sqrt() >= 0.3 - 1e-6);
    }

    #[test]
    fn retrecissement_seulement_si_utile() {
        // reseau RAILS (preferee 0,508) entre deux pastilles, sans obstacle : aucune portion retrecie
        let mut d = donnees(vec![comp("A", 4.25, 5.25, false, vec![pad("1", "+5V", 0.0, 0.0, false)]), comp("B", 12.25, 5.25, false, vec![pad("1", "+5V", 0.0, 0.0, false)])], vec![]);
        d.classes.insert("+5V".into(), "RAILS".into());
        d.regles.largeurs.insert("RAILS".into(), [0.254, 0.508, 5.0]);
        let m = Modele::new(&d, Parametres::defaut());
        let e = m.etat_v3();
        let mut dt = Detail::new(&m, &e);
        dt.tout(5, 0, &esp_off(), &mut |_| {});
        let n = m.nets.iter().position(|n| n.nom == "+5V").unwrap();
        assert_eq!(dt.nets[n].ok, 1);
        assert!(dt.nets[n].neck_mm < 1e-9, "retreci {}", dt.nets[n].neck_mm);
        assert!(dt.nets[n].segs.iter().all(|s| (s.w - 0.508).abs() < 1e-9));
    }

    /// Carte d'essai : deux rangees de 4 pastilles reliees en ordre inverse (croisements, donc vias), au pas `pas`
    /// (pastilles de 0,6 x `2 hh`), avec ou sans pastilles du plan GND.
    pub(crate) fn carte_croisee(pas: f64, hh: f64, gnd: bool, pas_fin: bool) -> crate::donnees_v1::Donnees {
        let (mut ga, mut gb) = (Vec::new(), Vec::new());
        for k in 0..4 {
            let y = (k as f64 - 1.5) * pas;
            ga.push(pad(&format!("{}", k + 1), &format!("S{}", k), 0.0, y, false));
            gb.push(pad(&format!("{}", k + 1), &format!("S{}", 3 - k), 0.0, y, false));
        }
        if gnd {
            ga.push(pad("G", "GND", 1.2, 0.0, false));
            gb.push(pad("G", "GND", -1.2, 0.0, false));
        }
        for p in ga.iter_mut().chain(gb.iter_mut()) {
            p.hh = hh;
        }
        let mut d = donnees(vec![comp("A", 5.25, 5.25, false, ga), comp("B", 14.25, 5.25, false, gb)], vec![]);
        if pas_fin {
            d.regles.isolement.pieces_pas_fin = vec!["A".into(), "B".into()];
        }
        d
    }

    fn router_carte(d: &crate::donnees_v1::Donnees, cache: bool) -> (serde_json::Value, usize, usize) {
        let m = Modele::new(d, Parametres::defaut());
        let e = m.etat_v3();
        let mut dt = Detail::new(&m, &e);
        dt.cache_via = cache;
        dt.tout(5, 1, &esp_off(), &mut |_| {});
        let vias: usize = dt.nets.iter().map(|r| r.vias.len()).sum();
        (crate::sortie::routage_json(&dt), dt.total_ko(), vias)
    }

    /// Avenant 2.2, A.4-1, test (a) en processus : routage avec et sans le cache exact du test de via sur trois cartes
    /// (pas normal, pas fin, plan) : pistes, vias et compteurs identiques.
    #[test]
    fn test_a_cache_via_sorties_identiques() {
        let cartes = [carte_croisee(1.0, 0.3, false, false), carte_croisee(0.5, 0.125, false, true), carte_croisee(1.0, 0.3, true, false)];
        let mut vias_total = 0;
        for (k, d) in cartes.iter().enumerate() {
            let (a, ko_a, v_a) = router_carte(d, true);
            let (b, ko_b, v_b) = router_carte(d, false);
            assert_eq!(a, b, "carte {} : sorties differentes avec et sans cache", k);
            assert_eq!((ko_a, v_a), (ko_b, v_b), "carte {}", k);
            vias_total += v_a;
        }
        assert!(vias_total > 0, "aucun via pose : le cache n'est pas exerce");
    }

    /// Avenant 2.2, ecart (6), test (b) : un reroutage partiel d'un plan qui ne touche pas une pastille CMS deja sans
    /// descente laisse le compte du plan >= 1 (9b5b5a3 le remettait a 0).
    #[test]
    fn test_b_reroutage_partiel_garde_l_echec_du_plan() {
        let d = carte_croisee(1.0, 0.3, true, false);
        let m = Modele::new(&d, Parametres::defaut());
        let e = m.etat_v3();
        let mut dt = Detail::new(&m, &e);
        dt.tout(5, 1, &esp_off(), &mut |_| {});
        let g = m.nets.iter().position(|n| n.nom == "GND").unwrap();
        let a = m.comp_idx["A"];
        let ka = m.comps[a].pads.iter().position(|p| p.nom == "G").unwrap();
        // B.G marquee sans descente (echec compte avec son diagnostic, comme apres une recherche de via de plan manquee)
        dt.nets[g].ko = 1;
        dt.nets[g].diag = vec!["B.G : pas de place pour un via de plan (essai)".to_string()];
        // reroutage partiel de A.G seulement : l'echec de B.G reste compte
        let (ok, _) = dt.rerouter_plan_partiel(g, &[(a, ka)], &[]);
        assert!(dt.nets[g].ko >= 1, "compte du plan remis a {}", dt.nets[g].ko);
        assert!(!ok);
    }
}
