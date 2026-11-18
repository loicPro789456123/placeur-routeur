//! Routeur global rapide servant de fonction de cout.
//!
//! Grille reguliere (pas ~0,5 mm), 2 couches de signaux (0 = dessus L1, 1 = dessous L4), capacite 1 piste
//! par cellule et par couche, via = changement de couche dans la meme cellule.
//! Obstacles : pastilles des autres reseaux (et pastilles sans reseau), bord, fentes, trous M3 (garde 0,6 mm
//! + demi-piste), emprise du bloc hacheur (seuls les reseaux qui touchent le bloc y entrent).
//! Reseau ordinaire = arbre de Steiner approche (croissance de Prim : chaque broche est reliee par A*
//! a l'arbre deja construit, heuristique admissible = distance a la boite de l'arbre).
//! GND et +8V (plans L2/L3) : une tache par pastille CMS = trouver par Dijkstra une cellule libre proche
//! pour un via de descente (piste courte + 1 via) ; les pastilles traversantes touchent le plan directement.
//! Congestion : negociation de type PathFinder (cout present + historique) sur un routage complet ;
//! en mode incremental (recuit) seules les taches des pieces deplacees (et les victimes) sont rerouties.
use crate::geom::*;
use std::cmp::Ordering;
use std::collections::BinaryHeap;

pub const CAP: u16 = 1;
/// Couches de routage : 0 = TopLayer (L1), 1 = BottomLayer (L4), 2 = MidLayer2 (L3, couche interne).
/// L2 reste un plan GND plein. Les vias sont traversants : ils occupent les trois couches.
pub const NCOUCHES: usize = 3;

/// Multiplicateur de cout par couche selon la sensibilite du reseau (TopLayer, BottomLayer, MidLayer2).
pub fn couche_mult(net: &Net) -> [f32; 3] {
    if net.classe == "PUISSANCE_8V" {
        [1.5, 1.5, 1.0]
    } else if matches!(net.classe.as_str(), "SENSE" | "TRIG_50R" | "COMMUTATION") || net.nom.ends_with("Vref") {
        [1.0, 1.2, 3.0]
    } else {
        [1.0, 1.0, 1.0]
    }
}
pub const VIA_G: f32 = 2.0; // cout de recherche d'un via (mm equivalents)
pub const VIA_PEN: f64 = 2.0; // penalite de via dans l'objectif
pub const OVF_PEN: f64 = 5.0; // par unite de debordement de capacite
pub const FAIL_PEN: f64 = 100.0; // par connexion impossible
pub const GARDE: f64 = 0.6; // garde cuivre au bord / fentes / trous
const NONE: u32 = u32::MAX;
const R_ECHAP: i32 = 8; // rayon de recherche du via de plan (cellules)
/// Gonflement des pastilles dans la grille globale : isolement 0,2 + demi-piste 0,15.
pub const INF_PAD: f64 = 0.35;

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

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Genre {
    Arbre,
    Echap,
}

pub struct Tache {
    pub net: usize,
    pub genre: Genre,
    pub pins: Vec<(usize, usize)>,
    pub poids: f64,
    pub key: u32,
    pub tb: bool,
    /// points cibles des groupes de cuivre du bloc (un par groupe a relier)
    pub attente: Vec<(f64, f64, u8)>,
    /// demi-surlargeur en cellules (pistes larges : emprise de capacite elargie)
    pub marge: usize,
    pub mult: [f32; 3],
}

#[derive(Clone, Default)]
pub struct Route {
    pub nodes: Vec<u32>,
    pub len: f64,
    pub vias: u32,
    pub fails: u32,
}

#[derive(Clone, Debug, Default)]
pub struct Mesure {
    pub longueur: f64,
    pub longueur_ponderee: f64,
    pub longueur_echap: f64,
    pub vias_signal: i64,
    pub vias_plan: i64,
    pub debordement: i64,
    pub echecs: i64,
    pub taches_en_echec: usize,
    pub reseaux_en_echec: usize,
    pub objectif_routage: f64,
}

pub struct Routeur<'a> {
    pub m: &'a Modele,
    pub pas: f64,
    pub nx: usize,
    pub ny: usize,
    pub nc: usize,
    hard: Vec<bool>,
    zone: Vec<bool>,
    pcnt: Vec<u16>,
    ps1: Vec<u32>,
    ps2: Vec<u64>,
    pad_nodes: Vec<Vec<Vec<u32>>>,
    pub usage: Vec<u16>,
    hist: Vec<f32>,
    cell_task: Vec<u32>,
    pub taches: Vec<Tache>,
    pub routes: Vec<Route>,
    pub taches_de_comp: Vec<Vec<usize>>,
    ordre: Vec<usize>,
    pub s_wlen: f64,
    pub s_len: f64,
    pub s_vias: i64,
    pub s_fails: i64,
    pub s_ovf: i64,
    gc: Vec<f32>,
    par: Vec<u32>,
    seen: Vec<u32>,
    ep: u32,
    tree: Vec<u32>,
    tep: u32,
    term: Vec<u32>,
    tmep: u32,
    pub pres: f32,
    heap: BinaryHeap<HI>,
    pub max_exp: usize,
    pub nonet: u32,
    /// heuristique dirigee vers la broche partenaire (recuit : +65 % de mouvements/s) ; false = admissible (mesure)
    pub focus: bool,
}

impl<'a> Routeur<'a> {
    pub fn new(m: &'a Modele, e: &Etat, pas: f64) -> Routeur<'a> {
        let nx = (m.w / pas).ceil() as usize;
        let ny = (m.h / pas).ceil() as usize;
        let nc = nx * ny;
        let nn = NCOUCHES * nc;
        let mut r = Routeur {
            m,
            pas,
            nx,
            ny,
            nc,
            hard: vec![false; nc],
            zone: vec![false; nc],
            pcnt: vec![0; nn],
            ps1: vec![0; nn],
            ps2: vec![0; nn],
            pad_nodes: m.comps.iter().map(|c| vec![Vec::new(); c.pads.len()]).collect(),
            usage: vec![0; nn],
            hist: vec![0.0; nn],
            cell_task: vec![NONE; nn],
            taches: Vec::new(),
            routes: Vec::new(),
            taches_de_comp: vec![Vec::new(); m.comps.len()],
            ordre: Vec::new(),
            s_wlen: 0.0,
            s_len: 0.0,
            s_vias: 0,
            s_fails: 0,
            s_ovf: 0,
            gc: vec![0.0; nn],
            par: vec![NONE; nn],
            seen: vec![0; nn],
            ep: 0,
            tree: vec![0; nn],
            tep: 0,
            term: vec![0; nn],
            tmep: 0,
            pres: 4.0,
            heap: BinaryHeap::new(),
            max_exp: 250_000,
            nonet: m.nets.len() as u32 + 1,
            focus: false,
        };
        // obstacles durs : bord, fentes, trous (garde + demi-piste 0,15)
        let g = GARDE + 0.15;
        // pastilles libres des trous M3 : cuivre reel + isolement 0,2 mm (+ demi-piste)
        let gt = m.regles.isolement.defaut + 0.15;
        for iy in 0..ny {
            for ix in 0..nx {
                let (x, y) = r.centre_cell(ix, iy);
                let mut h = x < g || y < g || x > m.w - g || y > m.h - g;
                for f in &m.fentes {
                    if x > f[0] - g && x < f[0] + f[2] + g && y > f[1] - g && y < f[1] + f[3] + g {
                        h = true;
                    }
                }
                for (t, rt) in m.trous.iter().zip(m.trous_r.iter()) {
                    if ((x - t[0]).powi(2) + (y - t[1]).powi(2)).sqrt() < rt + gt {
                        h = true;
                    }
                }
                r.hard[iy * nx + ix] = h;
            }
        }
        // bloc fige : plus d'interdiction globale de l'emprise ; son cuivre existant est un obstacle (cle de son reseau)
        for cu in &m.cuivre_bloc {
            let key = cu.net.map(|n| n as u32 + 1).unwrap_or(r.nonet);
            let (layers, rect): (Vec<usize>, Rect) = match &cu.obj {
                CuivreObj::Seg { x1, y1, x2, y2, w, couche } => (vec![*couche as usize], [x1.min(*x2) - w / 2.0, y1.min(*y2) - w / 2.0, x1.max(*x2) + w / 2.0, y1.max(*y2) + w / 2.0]),
                CuivreObj::Via { x, y, d } => (vec![0, 1, 2], [x - d / 2.0, y - d / 2.0, x + d / 2.0, y + d / 2.0]),
                CuivreObj::Region { couche, r: rr } => (vec![*couche as usize], *rr),
            };
            // segments obliques : boite englobante (prudent)
            for l in layers {
                for iy in 0..ny {
                    let yc = (iy as f64 + 0.5) * pas;
                    if yc < rect[1] - INF_PAD || yc > rect[3] + INF_PAD {
                        continue;
                    }
                    for ix in 0..nx {
                        let xc = (ix as f64 + 0.5) * pas;
                        if xc < rect[0] - INF_PAD || xc > rect[2] + INF_PAD {
                            continue;
                        }
                        let n = l * nc + iy * nx + ix;
                        r.pcnt[n] += 1;
                        r.ps1[n] += key;
                        r.ps2[n] += (key as u64) * (key as u64);
                    }
                }
            }
        }
        for i in 0..m.comps.len() {
            r.poser_pads(i, &e.poses[i]);
        }
        // taches
        for (ni, n) in m.nets.iter().enumerate() {
            if n.bloc_seul || n.pins.is_empty() {
                continue;
            }
            let key = ni as u32 + 1;
            if n.plan {
                for &(i, k) in n.pins.iter().filter(|&&(i, _)| m.comps[i].statut != Statut::Bloc).chain(n.pins_plan_bloc.iter()) {
                    if m.comps[i].pads[k].th {
                        continue;
                    }
                    r.taches.push(Tache { net: ni, genre: Genre::Echap, pins: vec![(i, k)], poids: 1.0, key, tb: false, attente: Vec::new(), marge: 0, mult: [1.0; 3] });
                }
            } else if n.pins_ext.len() + n.attentes.len() >= 2 {
                let lg = m.regles.largeurs.get(&n.classe).or_else(|| m.regles.largeurs.get("defaut")).copied().unwrap_or([0.254, 0.3, 3.0]);
                let wpref = lg[1].max(lg[0]);
                let marge = (((wpref - 0.3) / 2.0) / pas).round().max(0.0) as usize;
                r.taches.push(Tache {
                    net: ni,
                    genre: Genre::Arbre,
                    pins: n.pins_ext.clone(),
                    poids: n.poids.max(1.0),
                    key,
                    tb: false,
                    attente: n.attentes.iter().map(|a| (a.x, a.y, a.couche)).collect(),
                    marge,
                    mult: couche_mult(n),
                });
            }
        }
        r.routes = vec![Route::default(); r.taches.len()];
        for (t, ta) in r.taches.iter().enumerate() {
            for &(i, _) in &ta.pins {
                if !r.taches_de_comp[i].contains(&t) {
                    r.taches_de_comp[i].push(t);
                }
            }
        }
        // ordre de routage complet : echappements d'abord, puis arbres par demi-perimetre croissant
        let mut o: Vec<(u8, f64, usize)> = r
            .taches
            .iter()
            .enumerate()
            .map(|(t, ta)| {
                let mut b = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
                for &(i, k) in &ta.pins {
                    let (x, y, _, _) = m.pad_world(i, k, &e.poses[i]);
                    b = [b[0].min(x), b[1].min(y), b[2].max(x), b[3].max(y)];
                }
                for &(x, y, _) in &ta.attente {
                    b = [b[0].min(x), b[1].min(y), b[2].max(x), b[3].max(y)];
                }
                (if ta.genre == Genre::Echap { 0 } else { 1 }, b[2] - b[0] + b[3] - b[1], t)
            })
            .collect();
        o.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.partial_cmp(&b.1).unwrap()).then(a.2.cmp(&b.2)));
        r.ordre = o.into_iter().map(|x| x.2).collect();
        r
    }

    #[inline]
    fn centre_cell(&self, ix: usize, iy: usize) -> (f64, f64) {
        ((ix as f64 + 0.5) * self.pas, (iy as f64 + 0.5) * self.pas)
    }

    #[inline]
    pub fn cell_of(&self, x: f64, y: f64) -> usize {
        let ix = ((x / self.pas).floor().max(0.0) as usize).min(self.nx - 1);
        let iy = ((y / self.pas).floor().max(0.0) as usize).min(self.ny - 1);
        iy * self.nx + ix
    }

    fn pad_key(&self, i: usize, k: usize) -> u32 {
        match self.m.comps[i].pads[k].net {
            Some(n) => n as u32 + 1,
            None => self.nonet,
        }
    }

    /// Noeuds couverts par une pastille (centre de cellule dans la pastille gonflee de INF_PAD, + cellule du centre).
    fn pad_cover(&self, i: usize, k: usize, p: &Pose) -> Vec<u32> {
        let (cx, cy, hw, hh) = self.m.pad_world(i, k, p);
        let th = self.m.comps[i].pads[k].th;
        let layers: &[usize] = if th { &[0, 1, 2] } else if p.bot { &[1] } else { &[0] };
        let inf = INF_PAD;
        let x0 = (((cx - hw - inf) / self.pas).floor().max(0.0)) as usize;
        let x1 = ((((cx + hw + inf) / self.pas).floor()) as usize).min(self.nx - 1);
        let y0 = (((cy - hh - inf) / self.pas).floor().max(0.0)) as usize;
        let y1 = ((((cy + hh + inf) / self.pas).floor()) as usize).min(self.ny - 1);
        let cc = self.cell_of(cx, cy);
        let mut out = Vec::new();
        for &l in layers {
            out.push((l * self.nc + cc) as u32);
            for iy in y0..=y1 {
                for ix in x0..=x1 {
                    let (x, y) = self.centre_cell(ix, iy);
                    let c = iy * self.nx + ix;
                    if c != cc && (x - cx).abs() <= hw + inf && (y - cy).abs() <= hh + inf {
                        out.push((l * self.nc + c) as u32);
                    }
                }
            }
        }
        out
    }

    /// Noeuds terminaux d'une broche : cellule du centre sur la (les) couche(s) de la pastille.
    fn pin_nodes(&self, i: usize, k: usize, p: &Pose) -> Vec<u32> {
        let (cx, cy, _, _) = self.m.pad_world(i, k, p);
        let c = self.cell_of(cx, cy);
        if self.m.comps[i].pads[k].th {
            vec![c as u32, (c + self.nc) as u32, (c + 2 * self.nc) as u32]
        } else if p.bot {
            vec![(c + self.nc) as u32]
        } else {
            vec![c as u32]
        }
    }

    pub fn poser_pads(&mut self, i: usize, p: &Pose) {
        for k in 0..self.m.comps[i].pads.len() {
            let key = self.pad_key(i, k);
            let nodes = self.pad_cover(i, k, p);
            for &n in &nodes {
                let n = n as usize;
                self.pcnt[n] += 1;
                self.ps1[n] += key;
                self.ps2[n] += (key as u64) * (key as u64);
            }
            self.pad_nodes[i][k] = nodes;
        }
    }

    pub fn retirer_pads(&mut self, i: usize) {
        for k in 0..self.m.comps[i].pads.len() {
            let key = self.pad_key(i, k);
            let nodes = std::mem::take(&mut self.pad_nodes[i][k]);
            for &n in &nodes {
                let n = n as usize;
                self.pcnt[n] -= 1;
                self.ps1[n] -= key;
                self.ps2[n] -= (key as u64) * (key as u64);
            }
        }
    }

    #[inline]
    fn pad_ok(&self, n: usize, key: u32) -> bool {
        let c = self.pcnt[n] as u32;
        c == 0 || (self.ps1[n] == c * key && self.ps2[n] == (c as u64) * (key as u64) * (key as u64))
    }

    #[inline]
    fn passable(&self, n: usize, key: u32, tb: bool) -> bool {
        let c = n % self.nc;
        !self.hard[c] && (tb || !self.zone[c]) && self.pad_ok(n, key)
    }

    #[inline]
    fn cong(&self, n: usize) -> f32 {
        let u = self.usage[n];
        if u >= CAP {
            self.pres * self.pas as f32 * (u - CAP + 1) as f32
        } else {
            0.0
        }
    }

    /// Taches dont la route passe sur des noeuds donnes et appartient a un autre reseau (victimes d'un deplacement).
    pub fn victimes(&self, nodes: &[u32], key_exclue: Option<u32>, hors_bloc: bool) -> Vec<usize> {
        let mut v = Vec::new();
        for &n in nodes {
            let n = n as usize;
            if self.usage[n] > 0 {
                let t = self.cell_task[n];
                if t != NONE {
                    let ta = &self.taches[t as usize];
                    if hors_bloc && ta.tb {
                        continue;
                    }
                    if Some(ta.key) != key_exclue && !v.contains(&(t as usize)) {
                        v.push(t as usize);
                    }
                }
            }
        }
        v
    }

    pub fn victimes_pads(&self, i: usize) -> Vec<usize> {
        let mut v = Vec::new();
        for k in 0..self.m.comps[i].pads.len() {
            let key = self.pad_key(i, k);
            for t in self.victimes(&self.pad_nodes[i][k], Some(key), false) {
                if !v.contains(&t) {
                    v.push(t);
                }
            }
        }
        v
    }

    pub fn commit(&mut self, t: usize, r: Route) {
        for &n in &r.nodes {
            let n = n as usize;
            if self.usage[n] >= CAP {
                self.s_ovf += 1;
            }
            self.usage[n] += 1;
            self.cell_task[n] = t as u32;
        }
        self.s_wlen += r.len * self.taches[t].poids;
        self.s_len += r.len;
        self.s_vias += r.vias as i64;
        self.s_fails += r.fails as i64;
        self.routes[t] = r;
    }

    pub fn ripup(&mut self, t: usize) -> Route {
        let r = std::mem::take(&mut self.routes[t]);
        for &n in &r.nodes {
            let n = n as usize;
            if self.usage[n] > CAP {
                self.s_ovf -= 1;
            }
            self.usage[n] -= 1;
            if self.cell_task[n] == t as u32 {
                self.cell_task[n] = NONE;
            }
        }
        self.s_wlen -= r.len * self.taches[t].poids;
        self.s_len -= r.len;
        self.s_vias -= r.vias as i64;
        self.s_fails -= r.fails as i64;
        r
    }

    pub fn objectif(&self) -> f64 {
        self.s_wlen + VIA_PEN * self.s_vias as f64 + OVF_PEN * self.s_ovf as f64 + FAIL_PEN * self.s_fails as f64
    }

    pub fn route_tache(&mut self, t: usize, e: &Etat) -> Route {
        match self.taches[t].genre {
            Genre::Arbre => self.route_arbre(t, e),
            Genre::Echap => self.route_echap(t, e),
        }
    }

    fn route_arbre(&mut self, t: usize, e: &Etat) -> Route {
        let key = self.taches[t].key;
        let tb = self.taches[t].tb;
        let mut pins: Vec<Vec<u32>> = self.taches[t].pins.iter().map(|&(i, k)| self.pin_nodes(i, k, &e.poses[i])).collect();
        for &(x, y, l) in &self.taches[t].attente.clone() {
            pins.push(vec![(self.cell_of(x, y) + l as usize * self.nc) as u32]);
        }
        self.tmep += 1;
        for p in &pins {
            for &n in p {
                self.term[n as usize] = self.tmep;
            }
        }
        self.tep += 1;
        let mut r = Route::default();
        let mut bb = [usize::MAX, usize::MAX, 0usize, 0usize];
        let mut connected: Vec<usize> = vec![0];
        let mut via_cells: Vec<usize> = Vec::new();
        for &n in &pins[0] {
            self.add_tree(n, &mut r, &mut bb);
        }
        let mut rest: Vec<usize> = (1..pins.len()).collect();
        while !rest.is_empty() {
            // broche la plus proche (Manhattan) des broches deja reliees
            let mut best = (usize::MAX, 0usize, 0usize);
            for (ri, &p) in rest.iter().enumerate() {
                let (px, py) = self.xy(pins[p][0]);
                for &q in &connected {
                    let (qx, qy) = self.xy(pins[q][0]);
                    let d = px.abs_diff(qx) + py.abs_diff(qy);
                    if d < best.0 {
                        best = (d, ri, q);
                    }
                }
            }
            let p = rest.swap_remove(best.1);
            connected.push(p);
            let src = pins[p].clone();
            // heuristique dirigee vers la broche partenaire (Prim) : recherche plus focalisee que la boite de l'arbre
            // (non admissible vis-a-vis des autres noeuds de l'arbre, qui restent des buts valides)
            let cible = if self.focus {
                let (qx, qy) = self.xy(pins[best.2][0]);
                [qx, qy, qx, qy]
            } else {
                bb
            };
            let mult = self.taches[t].mult;
            match self.astar_tree(&src, key, tb, cible, mult) {
                Some(path) => {
                    for w in path.windows(2) {
                        if (w[0] as usize) % self.nc == (w[1] as usize) % self.nc {
                            r.vias += 1;
                            via_cells.push((w[0] as usize) % self.nc);
                        } else {
                            r.len += self.pas;
                        }
                    }
                    for &n in &path {
                        self.add_tree(n, &mut r, &mut bb);
                    }
                    for &n in &src {
                        self.add_tree(n, &mut r, &mut bb);
                    }
                }
                None => r.fails += 1,
            }
        }
        // emprise d'un via (diametre 0,6 + isolement + demi-piste ~ 1,1 mm) : croix de 5 cellules sur les deux couches
        for c in via_cells {
            self.emprise_via(c, key, tb, &mut r);
        }
        // pistes larges : la capacite consommee couvre toute la largeur
        let marge = self.taches[t].marge as i64;
        if marge > 0 {
            let base = r.nodes.clone();
            for n in base {
                let c = n as usize % self.nc;
                let l = n as usize / self.nc;
                let (x, y) = ((c % self.nx) as i64, (c / self.nx) as i64);
                for dy in -marge..=marge {
                    for dx in -marge..=marge {
                        let (u, v) = (x + dx, y + dy);
                        if u < 0 || v < 0 || u >= self.nx as i64 || v >= self.ny as i64 {
                            continue;
                        }
                        let m = (l * self.nc + v as usize * self.nx + u as usize) as u32;
                        if self.tree[m as usize] != self.tep && self.passable(m as usize, key, tb) {
                            self.tree[m as usize] = self.tep;
                            r.nodes.push(m);
                        }
                    }
                }
            }
        }
        r
    }

    #[inline]
    fn xy(&self, n: u32) -> (usize, usize) {
        let c = n as usize % self.nc;
        (c % self.nx, c / self.nx)
    }

    fn add_tree(&mut self, n: u32, r: &mut Route, bb: &mut [usize; 4]) {
        if self.tree[n as usize] != self.tep {
            self.tree[n as usize] = self.tep;
            r.nodes.push(n);
            let (x, y) = self.xy(n);
            bb[0] = bb[0].min(x);
            bb[1] = bb[1].min(y);
            bb[2] = bb[2].max(x);
            bb[3] = bb[3].max(y);
        }
    }

    fn astar_tree(&mut self, src: &[u32], key: u32, tb: bool, bb: [usize; 4], mult: [f32; 3]) -> Option<Vec<u32>> {
        self.ep += 1;
        let ep = self.ep;
        self.heap.clear();
        let pas = self.pas as f32;
        let h = |s: &Self, n: usize| -> f32 {
            let c = n % s.nc;
            let (x, y) = (c % s.nx, c / s.nx);
            let dx = if x < bb[0] { bb[0] - x } else if x > bb[2] { x - bb[2] } else { 0 };
            let dy = if y < bb[1] { bb[1] - y } else if y > bb[3] { y - bb[3] } else { 0 };
            (dx + dy) as f32 * pas
        };
        for &s in src {
            let s = s as usize;
            if self.tree[s] == self.tep {
                return Some(vec![s as u32]);
            }
            self.seen[s] = ep;
            self.gc[s] = 0.0;
            self.par[s] = NONE;
            let f = h(self, s);
            self.heap.push(HI { f, n: s as u32 });
        }
        let mut exp = 0usize;
        let nc = self.nc;
        let nx = self.nx;
        let ny = self.ny;
        while let Some(HI { f, n }) = self.heap.pop() {
            let n = n as usize;
            let g = self.gc[n];
            if g + h(self, n) < f - 1e-3 {
                continue;
            }
            if self.tree[n] == self.tep {
                let mut path = vec![n as u32];
                let mut c = n;
                while self.par[c] != NONE {
                    c = self.par[c] as usize;
                    path.push(c as u32);
                }
                path.reverse();
                return Some(path);
            }
            exp += 1;
            if exp > self.max_exp {
                return None;
            }
            let c = n % nc;
            let l = n / nc;
            let (x, y) = (c % nx, c / nx);
            let mut nb = [NONE as usize; 6];
            if x > 0 {
                nb[0] = n - 1;
            }
            if x + 1 < nx {
                nb[1] = n + 1;
            }
            if y > 0 {
                nb[2] = n - nx;
            }
            if y + 1 < ny {
                nb[3] = n + nx;
            }
            // via traversant : les trois couches de la cellule doivent etre libres (ou au reseau)
            let via_ok = (0..NCOUCHES).all(|ll| {
                let q = ll * nc + c;
                self.term[q] == self.tmep || self.tree[q] == self.tep || self.passable(q, key, tb)
            });
            if via_ok {
                let mut k = 4;
                for ll in 0..NCOUCHES {
                    if ll != l {
                        nb[k] = ll * nc + c;
                        k += 1;
                    }
                }
            }
            for (d, &m) in nb.iter().enumerate() {
                if m == NONE as usize {
                    continue;
                }
                let ok = self.term[m] == self.tmep || self.tree[m] == self.tep || self.passable(m, key, tb);
                if !ok {
                    continue;
                }
                let lm = m / nc;
                if !self.m.couches_actives[lm] {
                    continue;
                }
                let step = if d >= 4 { VIA_G } else { pas * (1.0 + self.hist[m]) * mult[lm] };
                let cost = g + step + self.cong(m);
                if self.seen[m] != ep || cost < self.gc[m] {
                    self.seen[m] = ep;
                    self.gc[m] = cost;
                    self.par[m] = n as u32;
                    let f = cost + h(self, m);
                    self.heap.push(HI { f, n: m as u32 });
                }
            }
        }
        None
    }

    /// Descente vers un plan : piste courte depuis la pastille CMS puis via dans une cellule sans pastille.
    fn route_echap(&mut self, t: usize, e: &Etat) -> Route {
        let (i, k) = self.taches[t].pins[0];
        let key = self.taches[t].key;
        let tb = self.taches[t].tb;
        let src = self.pin_nodes(i, k, &e.poses[i])[0] as usize;
        let nc = self.nc;
        let nx = self.nx;
        let ny = self.ny;
        let (sx, sy) = self.xy(src as u32);
        self.ep += 1;
        let ep = self.ep;
        self.heap.clear();
        self.seen[src] = ep;
        self.gc[src] = 0.0;
        self.par[src] = NONE;
        self.heap.push(HI { f: 0.0, n: src as u32 });
        let pas = self.pas as f32;
        let mut r = Route::default();
        while let Some(HI { f, n }) = self.heap.pop() {
            let n = n as usize;
            if f > self.gc[n] + 1e-3 {
                continue;
            }
            let c = n % nc;
            let l = n / nc;
            if n != src && !self.hard[c] && (tb || !self.zone[c]) && (0..NCOUCHES).all(|ll| self.pcnt[ll * nc + c] == 0) {
                let mut path: Vec<u32> = (0..NCOUCHES).filter(|&ll| ll != l).map(|ll| (ll * nc + c) as u32).collect();
                path.push(n as u32);
                let mut q = n;
                while self.par[q] != NONE {
                    q = self.par[q] as usize;
                    path.push(q as u32);
                    r.len += self.pas;
                }
                r.vias = 1;
                self.tep += 1;
                for &q in &path {
                    self.tree[q as usize] = self.tep;
                }
                r.nodes = path;
                self.emprise_via(c, key, tb, &mut r);
                return r;
            }
            let (x, y) = (c % nx, c / nx);
            let cand = [
                if x > 0 { Some(n - 1) } else { None },
                if x + 1 < nx { Some(n + 1) } else { None },
                if y > 0 { Some(n - nx) } else { None },
                if y + 1 < ny { Some(n + nx) } else { None },
            ];
            for m in cand.iter().flatten() {
                let m = *m;
                let (mx, my) = self.xy(m as u32);
                if (mx.abs_diff(sx) + my.abs_diff(sy)) as i32 > R_ECHAP {
                    continue;
                }
                if !self.passable(m, key, tb) {
                    continue;
                }
                let cost = self.gc[n] + pas * (1.0 + self.hist[m]) + self.cong(m);
                if self.seen[m] != ep || cost < self.gc[m] {
                    self.seen[m] = ep;
                    self.gc[m] = cost;
                    self.par[m] = n as u32;
                    self.heap.push(HI { f: cost, n: m as u32 });
                }
            }
        }
        r.fails = 1;
        r
    }

    fn emprise_via(&mut self, c: usize, key: u32, tb: bool, r: &mut Route) {
        let (x, y) = ((c % self.nx) as i64, (c / self.nx) as i64);
        for (dx, dy) in [(1i64, 0i64), (-1, 0), (0, 1), (0, -1)] {
            let (u, v) = (x + dx, y + dy);
            if u < 0 || v < 0 || u >= self.nx as i64 || v >= self.ny as i64 {
                continue;
            }
            for l in 0..NCOUCHES {
                let m = l * self.nc + v as usize * self.nx + u as usize;
                if self.tree[m] != self.tep && self.passable(m, key, tb) {
                    self.tree[m] = self.tep;
                    r.nodes.push(m as u32);
                }
            }
        }
        // le via lui-meme traverse les trois couches
        for l in 0..NCOUCHES {
            let m = l * self.nc + c;
            if self.tree[m] != self.tep {
                self.tree[m] = self.tep;
                r.nodes.push(m as u32);
            }
        }
    }

    fn uses_overflow(&self, t: usize) -> bool {
        self.routes[t].nodes.iter().any(|&n| self.usage[n as usize] > CAP)
    }

    /// Routage complet negocie (PathFinder) : reroute tout, puis seulement les taches en debordement.
    pub fn route_tout(&mut self, e: &Etat, iters: usize) {
        for t in 0..self.taches.len() {
            self.ripup(t);
        }
        for v in self.hist.iter_mut() {
            *v = 0.0;
        }
        let ordre = self.ordre.clone();
        for it in 0..iters.max(1) {
            self.pres = (0.5 * 2f32.powi(it as i32)).min(64.0);
            for &t in &ordre {
                if it > 0 && !self.uses_overflow(t) {
                    continue;
                }
                self.ripup(t);
                let r = self.route_tache(t, e);
                self.commit(t, r);
            }
            if self.s_ovf == 0 {
                break;
            }
            for n in 0..self.usage.len() {
                if self.usage[n] > CAP {
                    self.hist[n] += 0.5;
                }
            }
        }
        self.pres = 8.0;
    }

    pub fn mesure(&self) -> Mesure {
        let mut ms = Mesure::default();
        let mut nets_ko = std::collections::HashSet::new();
        for (t, r) in self.routes.iter().enumerate() {
            let ta = &self.taches[t];
            match ta.genre {
                Genre::Arbre => {
                    ms.longueur += r.len;
                    ms.longueur_ponderee += r.len * ta.poids;
                    ms.vias_signal += r.vias as i64;
                }
                Genre::Echap => {
                    ms.longueur_echap += r.len;
                    ms.vias_plan += r.vias as i64;
                }
            }
            ms.echecs += r.fails as i64;
            if r.fails > 0 {
                ms.taches_en_echec += 1;
                nets_ko.insert(ta.net);
            }
        }
        ms.reseaux_en_echec = nets_ko.len();
        ms.debordement = self.s_ovf;
        ms.objectif_routage = self.objectif();
        ms
    }
}

#[cfg(test)]
pub(crate) mod tests {
    //! Tests sur une petite carte synthetique (2 pieces a 2 pastilles + 1 pastille isolee).
    use super::*;
    use crate::donnees_v1::*;
    use crate::parametres::Parametres;
    use std::collections::HashMap;

    pub(crate) fn pad(nom: &str, net: &str, lx: f64, ly: f64, bot_th: bool) -> PadIn {
        PadIn { nom: nom.into(), reseau: net.into(), lx, ly, hw: 0.3, hh: 0.3, th: bot_th }
    }

    pub(crate) fn comp(nom: &str, x: f64, y: f64, bot: bool, pads: Vec<PadIn>) -> CompIn {
        CompIn {
            nom: nom.into(),
            statut: "fixe".into(),
            lbb: [-0.5, -0.5, 0.5, 0.5],
            pastilles: pads,
            pose_actuelle: (x, y, 0, bot),
            pose_cible: (x, y, 0, bot),
            orientations: vec![],
            regions: vec![],
        }
    }

    pub(crate) fn donnees(comps: Vec<CompIn>, fentes: Vec<[f64; 4]>) -> Donnees {
        let mut larg = HashMap::new();
        larg.insert("defaut".to_string(), [0.254, 0.3, 3.0]);
        let mut all = comps;
        // M1 et U25 factices (exiges par le modele)
        all.push(comp("M1", 1.0, 1.0, false, vec![]));
        Donnees {
            contour: Contour { w: 20.0, h: 10.0, points: vec![] },
            marge_bord: 0.5,
            composants: all,
            obstacles_placement: vec![],
            bloc: BlocIn {
                rects_base: vec![FaceRect { face: 0, rect: [0.0, 0.0, 0.1, 0.1] }, FaceRect { face: 1, rect: [0.0, 0.0, 0.1, 0.1] }],
                delta_v3: [0.0, 0.0],
                emprise_base: [-5.0, -5.0, -4.0, -4.0],
                colonne: [0.0, 20.0],
                broches_u25: vec![],
                m1_centre_base: [0.0, 0.0],
                dist_sw_min: 0.0,
            },
            trous_m3: vec![],
            fentes,
            soft_sw: vec![],
            decouplages: vec![],
            poids_reseaux: HashMap::new(),
            reseaux_plans: vec!["GND".into()],
            classes: HashMap::new(),
            regles: Regles {
                isolement: Isolement { defaut: 0.2, commutation: 0.25, pastilles_pas_fin: 0.124, pieces_pas_fin: vec![] },
                largeurs: larg,
                via: ViaR { diametre: 0.6, trou: 0.3, via_sous_cms_permis: vec![] },
                garde_bord_fentes_trous: 0.6,
                rayon_trou_m3: 1.7,
            },
        }
    }

    fn route_net(d: &Donnees, net: &str) -> Route {
        let m = Modele::new(d, Parametres::defaut());
        let e = m.etat_v3();
        let mut r = Routeur::new(&m, &e, 0.5);
        r.route_tout(&e, 4);
        let ni = m.net_idx[net];
        let t = r.taches.iter().position(|t| t.net == ni).unwrap();
        r.routes[t].clone()
    }

    #[test]
    fn chemin_simple() {
        // deux pastilles dessus a 8 mm l'une de l'autre sur la meme ligne
        let d = donnees(vec![comp("A", 4.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)]), comp("B", 12.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)])], vec![]);
        let r = route_net(&d, "S");
        assert_eq!(r.fails, 0);
        assert_eq!(r.vias, 0);
        assert!((r.len - 8.0).abs() < 1e-9, "longueur {}", r.len);
    }

    #[test]
    fn contournement_obstacle() {
        // une fente verticale entre les deux pastilles : la route doit la contourner (plus longue que 8 mm)
        let d = donnees(
            vec![comp("A", 4.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)]), comp("B", 12.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)])],
            vec![[7.9, 2.0, 0.6, 8.5]],
        );
        let r = route_net(&d, "S");
        assert_eq!(r.fails, 0);
        assert!(r.len > 10.0, "longueur {}", r.len);
    }

    #[test]
    fn via_entre_faces() {
        // une pastille dessus, une dessous : au moins un via
        let d = donnees(vec![comp("A", 4.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)]), comp("B", 12.25, 5.25, true, vec![pad("1", "S", 0.0, 0.0, false)])], vec![]);
        let r = route_net(&d, "S");
        assert_eq!(r.fails, 0);
        assert_eq!(r.vias, 1);
    }

    #[test]
    fn pastille_etrangere_bloque() {
        // pastille d'un autre reseau sur la ligne directe : la route du dessus l'evite (detour ou via)
        let d = donnees(
            vec![
                comp("A", 4.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)]),
                comp("B", 12.25, 5.25, false, vec![pad("1", "S", 0.0, 0.0, false)]),
                comp("C", 8.25, 5.25, false, vec![pad("1", "X", 0.0, 0.0, false)]),
            ],
            vec![],
        );
        let r = route_net(&d, "S");
        assert_eq!(r.fails, 0);
        assert!(r.len > 8.0 || r.vias >= 2);
    }

    #[test]
    fn echappement_plan() {
        let d = donnees(vec![comp("A", 4.25, 5.25, false, vec![pad("1", "GND", 0.0, 0.0, false)])], vec![]);
        let r = route_net(&d, "GND");
        assert_eq!(r.fails, 0);
        assert_eq!(r.vias, 1);
    }
}
