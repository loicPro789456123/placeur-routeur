//! Légalité exacte du placement (§4.5), sous forme de fonctions pures (étape 8, partie pure).
//!
//! Le contour est un ensemble d'anneaux (le contour puis ses découpes, règle pair-impair) muni de deux structures
//! d'accélération : des bandes horizontales (test « dedans » exact, n'examinant que les arêtes de la bande) et une
//! grille de cases (arêtes proches d'une pièce). La décision finale est toujours exacte : les structures ne font que
//! restreindre l'ensemble des arêtes examinées, sans approximation. Un contour de plusieurs centaines de sommets
//! coûte donc à peu près autant qu'un carré pour une pièce loin du bord.
//!
//! Décision §8.0-2 (c) : une distance nulle est toujours un contact refusé, même avec une marge nulle.

pub type P = [f64; 2];

fn sous(a: P, b: P) -> P {
    [a[0] - b[0], a[1] - b[1]]
}
fn pv(a: P, b: P) -> f64 {
    a[0] * b[1] - a[1] * b[0]
}
fn ps(a: P, b: P) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}
fn orient(a: P, b: P, c: P) -> f64 {
    pv(sous(b, a), sous(c, a))
}

/// Cosinus et sinus, exacts aux multiples de 90°.
pub fn cos_sin(angle_deg: f64) -> (f64, f64) {
    let a = angle_deg.rem_euclid(360.0);
    if a == 0.0 {
        (1.0, 0.0)
    } else if a == 90.0 {
        (0.0, 1.0)
    } else if a == 180.0 {
        (-1.0, 0.0)
    } else if a == 270.0 {
        (0.0, -1.0)
    } else {
        let r = a.to_radians();
        (r.cos(), r.sin())
    }
}

/// Rectangle de centre `c`, demi-côtés `demi`, tourné de `angle_deg` (sens trigonométrique).
pub fn rectangle(c: P, demi: [f64; 2], angle_deg: f64) -> Vec<P> {
    let (co, si) = cos_sin(angle_deg);
    [[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]]
        .iter()
        .map(|s| {
            let (x, y) = (s[0] * demi[0], s[1] * demi[1]);
            [c[0] + x * co - y * si, c[1] + x * si + y * co]
        })
        .collect()
}

/// Polygone local placé à la pose (centre, angle).
pub fn poser(local: &[P], c: P, angle_deg: f64) -> Vec<P> {
    let (co, si) = cos_sin(angle_deg);
    local.iter().map(|p| [c[0] + p[0] * co - p[1] * si, c[1] + p[0] * si + p[1] * co]).collect()
}

pub fn dist_point_segment(p: P, a: P, b: P) -> f64 {
    let ab = sous(b, a);
    let ap = sous(p, a);
    let l2 = ps(ab, ab);
    let t = if l2 > 0.0 { (ps(ap, ab) / l2).clamp(0.0, 1.0) } else { 0.0 };
    let q = [a[0] + t * ab[0], a[1] + t * ab[1]];
    ((p[0] - q[0]).powi(2) + (p[1] - q[1]).powi(2)).sqrt()
}

fn sur(a: P, b: P, p: P) -> bool {
    p[0] >= a[0].min(b[0]) && p[0] <= a[0].max(b[0]) && p[1] >= a[1].min(b[1]) && p[1] <= a[1].max(b[1])
}

pub fn segments_se_coupent(a: P, b: P, c: P, d: P) -> bool {
    let (d1, d2, d3, d4) = (orient(c, d, a), orient(c, d, b), orient(a, b, c), orient(a, b, d));
    if ((d1 > 0.0 && d2 < 0.0) || (d1 < 0.0 && d2 > 0.0)) && ((d3 > 0.0 && d4 < 0.0) || (d3 < 0.0 && d4 > 0.0)) {
        return true;
    }
    (d1 == 0.0 && sur(c, d, a)) || (d2 == 0.0 && sur(c, d, b)) || (d3 == 0.0 && sur(a, b, c)) || (d4 == 0.0 && sur(a, b, d))
}

pub fn dist_segments(a: P, b: P, c: P, d: P) -> f64 {
    if segments_se_coupent(a, b, c, d) {
        return 0.0;
    }
    dist_point_segment(a, c, d).min(dist_point_segment(b, c, d)).min(dist_point_segment(c, a, b)).min(dist_point_segment(d, a, b))
}

/// Point dans un polygone simple (règle pair-impair, demi-ouverte).
pub fn dedans(poly: &[P], p: P) -> bool {
    let mut c = false;
    let n = poly.len();
    if n < 3 {
        return false;
    }
    let mut j = n - 1;
    for i in 0..n {
        if croise(poly[i], poly[j], p) {
            c = !c;
        }
        j = i;
    }
    c
}

/// L'arête (pi, pj) coupe-t-elle le rayon horizontal issu de `p` vers les x croissants ?
fn croise(pi: P, pj: P, p: P) -> bool {
    (pi[1] > p[1]) != (pj[1] > p[1]) && p[0] < pj[0] + (p[1] - pj[1]) * (pi[0] - pj[0]) / (pi[1] - pj[1])
}

fn aretes(poly: &[P]) -> impl Iterator<Item = (P, P)> + '_ {
    (0..poly.len()).map(move |i| (poly[i], poly[(i + 1) % poly.len()]))
}

pub fn dist_point_polygone(p: P, poly: &[P]) -> f64 {
    if dedans(poly, p) {
        return 0.0;
    }
    aretes(poly).map(|(a, b)| dist_point_segment(p, a, b)).fold(f64::INFINITY, f64::min)
}

pub fn dist_segment_polygone(a: P, b: P, poly: &[P]) -> f64 {
    if dedans(poly, a) || dedans(poly, b) {
        return 0.0;
    }
    aretes(poly).map(|(c, d)| dist_segments(a, b, c, d)).fold(f64::INFINITY, f64::min)
}

/// Distance entre deux polygones simples (0 s'ils se chevauchent ou si l'un contient l'autre).
pub fn dist_polygones(a: &[P], b: &[P]) -> f64 {
    if a.iter().any(|&p| dedans(b, p)) || b.iter().any(|&p| dedans(a, p)) {
        return 0.0;
    }
    let mut d = f64::INFINITY;
    for (p, q) in aretes(a) {
        for (r, s) in aretes(b) {
            d = d.min(dist_segments(p, q, r, s));
        }
    }
    d
}

fn boite(poly: &[P]) -> (P, P) {
    let mut mn = [f64::INFINITY; 2];
    let mut mx = [f64::NEG_INFINITY; 2];
    for p in poly {
        for k in 0..2 {
            mn[k] = mn[k].min(p[k]);
            mx[k] = mx[k].max(p[k]);
        }
    }
    (mn, mx)
}

/// Distance acceptable : au moins `marge` et jamais nulle (contact refusé).
fn assez_loin(d: f64, marge: f64) -> bool {
    d >= marge && d > 0.0
}

/// Contour de carte et découpes, avec structures d'accélération.
pub struct Contour {
    pub anneaux: Vec<Vec<P>>,
    aretes: Vec<(P, P)>,
    o: P,
    pas: [f64; 2],
    n: [usize; 2],
    bandes: Vec<Vec<u32>>,
    cases: Vec<Vec<u32>>,
}

impl Contour {
    /// `contour` puis `decoupes` (anneaux disjoints, intérieurs au contour : règle pair-impair).
    pub fn nouveau(contour: Vec<P>, decoupes: Vec<Vec<P>>) -> Contour {
        let mut anneaux = vec![contour];
        anneaux.extend(decoupes);
        let aretes: Vec<(P, P)> = anneaux.iter().flat_map(|a| aretes(a).collect::<Vec<_>>()).collect();
        let (mn, mx) = boite(&anneaux[0]);
        let cote = ((aretes.len() as f64).sqrt() * 2.0).ceil().clamp(4.0, 128.0) as usize;
        let n = [cote, cote];
        let pas = [((mx[0] - mn[0]) / cote as f64).max(1e-9), ((mx[1] - mn[1]) / cote as f64).max(1e-9)];
        let mut c = Contour { anneaux, aretes, o: mn, pas, n, bandes: vec![Vec::new(); cote], cases: vec![Vec::new(); cote * cote] };
        for (k, &(a, b)) in c.aretes.iter().enumerate() {
            let (i0, i1) = (c.indice(a[0].min(b[0]), 0), c.indice(a[0].max(b[0]), 0));
            let (j0, j1) = (c.indice(a[1].min(b[1]), 1), c.indice(a[1].max(b[1]), 1));
            for j in j0..=j1 {
                c.bandes[j].push(k as u32);
                for i in i0..=i1 {
                    c.cases[j * cote + i].push(k as u32);
                }
            }
        }
        c
    }

    fn indice(&self, v: f64, axe: usize) -> usize {
        let f = ((v - self.o[axe]) / self.pas[axe]).floor();
        if f <= 0.0 {
            0
        } else {
            (f as usize).min(self.n[axe] - 1)
        }
    }

    /// Point dans la région utile (dans le contour, hors des découpes), exact.
    pub fn dans_region(&self, p: P) -> bool {
        let (mn, mx) = (self.o, [self.o[0] + self.pas[0] * self.n[0] as f64, self.o[1] + self.pas[1] * self.n[1] as f64]);
        if p[0] < mn[0] || p[0] > mx[0] || p[1] < mn[1] || p[1] > mx[1] {
            return false;
        }
        let mut c = false;
        for &k in &self.bandes[self.indice(p[1], 1)] {
            let (a, b) = self.aretes[k as usize];
            if croise(a, b, p) {
                c = !c;
            }
        }
        c
    }

    /// Même résultat que `dans_region`, sans accélération (référence des tests).
    pub fn dans_region_brut(&self, p: P) -> bool {
        self.anneaux.iter().filter(|a| dedans(a, p)).count() % 2 == 1
    }

    fn aretes_proches(&self, mn: P, mx: P) -> Vec<u32> {
        let mut v = Vec::new();
        for j in self.indice(mn[1], 1)..=self.indice(mx[1], 1) {
            for i in self.indice(mn[0], 0)..=self.indice(mx[0], 0) {
                v.extend_from_slice(&self.cases[j * self.n[0] + i]);
            }
        }
        v.sort_unstable();
        v.dedup();
        v
    }

    /// Pièce (polygone simple) entièrement dans la région utile, à au moins `marge` du contour et des découpes.
    pub fn piece_legale(&self, piece: &[P], marge: f64) -> bool {
        let (mut mn, mut mx) = boite(piece);
        for k in 0..2 {
            mn[k] -= marge;
            mx[k] += marge;
        }
        if !self.dans_region(piece[0]) {
            return false;
        }
        let proches = self.aretes_proches(mn, mx);
        if proches.is_empty() {
            return true;
        }
        piece.iter().all(|&p| self.dans_region(p))
            && proches.iter().all(|&k| {
                let (a, b) = self.aretes[k as usize];
                assez_loin(dist_segment_polygone(a, b, piece), marge)
            })
    }

    /// Référence exacte sans accélération.
    pub fn piece_legale_brut(&self, piece: &[P], marge: f64) -> bool {
        piece.iter().all(|&p| self.dans_region_brut(p))
            && self.aretes.iter().all(|&(a, b)| assez_loin(dist_segment_polygone(a, b, piece), marge))
    }

    /// Distance de la pièce au contour extérieur (anneau 0).
    pub fn dist_bord(&self, piece: &[P]) -> f64 {
        aretes(&self.anneaux[0]).map(|(a, b)| dist_segment_polygone(a, b, piece)).fold(f64::INFINITY, f64::min)
    }
}

/// Pastille d'une pièce, dans le repère de la pièce.
pub struct PastilleLocale {
    pub poly: Vec<P>,
    pub expansion_masque: f64,
    pub traversante: bool,
}

pub struct Piece {
    pub c: P,
    pub angle: f64,
    /// Corps (emprise) dans le repère de la pièce.
    pub corps: Vec<P>,
    pub dessus: bool,
    pub fixe: bool,
    pub au_bord: bool,
    pub zone: Option<Vec<P>>,
    pub pastilles: Vec<PastilleLocale>,
}

impl Piece {
    pub fn corps_monde(&self) -> Vec<P> {
        poser(&self.corps, self.c, self.angle)
    }
}

pub struct Regles {
    pub marge_contour: f64,
    pub marge_trou: f64,
    pub marge_obstacle: f64,
    pub marge_pieces: f64,
    pub marge_traversante: f64,
    pub tol_bord: f64,
    pub grille: Option<f64>,
}

pub struct Groupe {
    pub chef: usize,
    /// (membre, décalage dans le repère du chef, angle relatif)
    pub membres: Vec<(usize, P, f64)>,
}

pub struct Plateau {
    pub contour: Contour,
    /// Obstacles rectangulaires ou polygonaux (repère monde), avec leur face (None = les deux).
    pub obstacles: Vec<(Vec<P>, Option<bool>)>,
    /// Trous (centre, rayon), et zones interdites d'empreinte à effet placement assimilées à des disques ou des
    /// polygones dans `obstacles`.
    pub trous: Vec<(P, f64)>,
    pub eloignements: Vec<(usize, usize, f64)>,
    pub groupes: Vec<Groupe>,
    pub regles: Regles,
}

const TOL: f64 = 1e-6;

impl Plateau {
    /// Raisons d'illégalité de la pièce `i` (vide si légale). C'est le test local du recuit pour une pièce déplacée.
    pub fn violations_piece(&self, pieces: &[Piece], i: usize) -> Vec<String> {
        let r = &self.regles;
        let p = &pieces[i];
        let corps = p.corps_monde();
        let mut v = Vec::new();
        if !self.contour.piece_legale(&corps, r.marge_contour) {
            v.push("contour".to_string());
        }
        for (o, face) in &self.obstacles {
            if face.map_or(true, |f| f == p.dessus) && !assez_loin(dist_polygones(&corps, o), r.marge_obstacle) {
                v.push("obstacle".to_string());
            }
        }
        for &(c, ray) in &self.trous {
            if !assez_loin(dist_point_polygone(c, &corps), ray + r.marge_trou) {
                v.push("trou".to_string());
            }
        }
        for (j, q) in pieces.iter().enumerate() {
            if j == i {
                continue;
            }
            if q.dessus == p.dessus {
                if !assez_loin(dist_polygones(&corps, &q.corps_monde()), r.marge_pieces) {
                    v.push(format!("chevauchement:{j}"));
                }
            } else {
                // Pastilles de la face opposée : expansion de masque positive seulement, marge des traversantes.
                let viol = |pa: &PastilleLocale, corps_a: &[P], pose: &Piece| {
                    let m = pa.expansion_masque.max(0.0) + if pa.traversante { r.marge_traversante } else { 0.0 };
                    !assez_loin(dist_polygones(corps_a, &poser(&pa.poly, pose.c, pose.angle)), m)
                };
                if q.pastilles.iter().any(|pa| viol(pa, &corps, q)) || p.pastilles.iter().any(|pa| viol(pa, &q.corps_monde(), p)) {
                    v.push(format!("pastille_opposee:{j}"));
                }
            }
        }
        for &(a, b, d) in &self.eloignements {
            if a == i || b == i {
                let j = if a == i { b } else { a };
                if !assez_loin(dist_polygones(&corps, &pieces[j].corps_monde()), d) {
                    v.push(format!("eloignement:{j}"));
                }
            }
        }
        if p.au_bord && self.contour.dist_bord(&corps) > r.tol_bord {
            v.push("au_bord".to_string());
        }
        if let Some(z) = &p.zone {
            let inclus = corps.iter().all(|&q| dedans(z, q))
                && !z.iter().any(|&q| dedans(&corps, q))
                && !aretes(z).any(|(a, b)| aretes(&corps).any(|(c, d)| segments_se_coupent(a, b, c, d)));
            if !inclus {
                v.push("zone".to_string());
            }
        }
        if let (Some(g), false) = (r.grille, p.fixe) {
            if p.c.iter().any(|x| ((x / g) - (x / g).round()).abs() > TOL) {
                v.push("grille".to_string());
            }
        }
        for gr in &self.groupes {
            let chef = &pieces[gr.chef];
            let (co, si) = cos_sin(chef.angle);
            for &(m, d, rel) in &gr.membres {
                if m != i && gr.chef != i {
                    continue;
                }
                let q = &pieces[m];
                let att = [chef.c[0] + d[0] * co - d[1] * si, chef.c[1] + d[0] * si + d[1] * co];
                let da = (q.angle - chef.angle - rel).rem_euclid(360.0);
                if (q.c[0] - att[0]).abs() > TOL || (q.c[1] - att[1]).abs() > TOL || da.min(360.0 - da) > TOL {
                    v.push(format!("groupe:{m}"));
                }
            }
        }
        v
    }

    /// Légalité de tout le placement (la même règle pour le placement initial, le recuit, la boucle et la conjointe).
    pub fn placement_legal(&self, pieces: &[Piece]) -> bool {
        (0..pieces.len()).all(|i| self.violations_piece(pieces, i).is_empty())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn regles() -> Regles {
        Regles { marge_contour: 0.2, marge_trou: 0.2, marge_obstacle: 0.1, marge_pieces: 0.1, marge_traversante: 0.3, tol_bord: 0.5, grille: None }
    }
    fn carre(c: P, d: f64) -> Vec<P> {
        rectangle(c, [d, d], 0.0)
    }
    fn piece(c: P, demi: [f64; 2], angle: f64) -> Piece {
        Piece { c, angle, corps: rectangle([0.0, 0.0], demi, 0.0), dessus: true, fixe: false, au_bord: false, zone: None, pastilles: Vec::new() }
    }
    fn plateau(contour: Contour) -> Plateau {
        Plateau { contour, obstacles: Vec::new(), trous: Vec::new(), eloignements: Vec::new(), groupes: Vec::new(), regles: regles() }
    }
    fn rond(n: usize, r: f64) -> Vec<P> {
        (0..n).map(|k| {
            let a = 2.0 * std::f64::consts::PI * k as f64 / n as f64;
            [50.0 + r * a.cos(), 50.0 + r * a.sin()]
        }).collect()
    }
    /// Générateur congruentiel fixe (reproductible).
    struct Gen(u64);
    impl Gen {
        fn u(&mut self) -> f64 {
            self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            (self.0 >> 11) as f64 / (1u64 << 53) as f64
        }
    }

    #[test]
    fn encoche_piece_sur_la_fente_refusee() {
        // Carré 0..40 avec une fente de 2 mm de large, de y = 0 à y = 20, en x = 19..21.
        let c = Contour::nouveau(vec![[0.0, 0.0], [19.0, 0.0], [19.0, 20.0], [21.0, 20.0], [21.0, 0.0], [40.0, 0.0], [40.0, 40.0], [0.0, 40.0]], vec![]);
        assert!(!c.piece_legale(&carre([20.0, 10.0], 3.0), 0.2));
        assert!(!c.piece_legale(&carre([20.0, 10.0], 0.5), 0.2), "pièce dans la fente");
        assert!(c.piece_legale(&carre([10.0, 10.0], 3.0), 0.2));
        assert!(c.piece_legale(&carre([20.0, 30.0], 3.0), 0.2));
        // Découpe intérieure (distance au polygone).
        let d = Contour::nouveau(carre([20.0, 20.0], 20.0), vec![carre([20.0, 20.0], 2.0)]);
        assert!(!d.piece_legale(&carre([20.0, 20.0], 3.0), 0.2), "pièce couvrant la découpe");
        assert!(!d.piece_legale(&carre([25.1, 20.0], 3.0), 0.2), "à 0,1 mm de la découpe");
        assert!(d.piece_legale(&carre([25.3, 20.0], 3.0), 0.2));
    }

    #[test]
    fn contour_en_l() {
        let c = Contour::nouveau(vec![[0.0, 0.0], [40.0, 0.0], [40.0, 15.0], [15.0, 15.0], [15.0, 40.0], [0.0, 40.0]], vec![]);
        assert!(c.piece_legale(&carre([7.0, 30.0], 3.0), 0.2));
        assert!(c.piece_legale(&carre([30.0, 7.0], 3.0), 0.2));
        assert!(!c.piece_legale(&carre([30.0, 30.0], 3.0), 0.2), "hors du L");
        assert!(!c.piece_legale(&carre([15.0, 15.0], 1.0), 0.2), "sur le coin rentrant");
        assert!(!c.piece_legale(&rectangle([16.5, 16.5], [3.0, 0.5], 45.0), 0.2), "pièce tournée à 45° sur le coin rentrant");
    }

    #[test]
    fn contour_circulaire_de_256_sommets_exact() {
        let c = Contour::nouveau(rond(256, 40.0), vec![rond(40, 5.0)]);
        assert!(c.piece_legale(&carre([50.0, 20.0], 2.0), 0.2));
        assert!(!c.piece_legale(&carre([50.0, 89.0], 2.0), 0.2));
        let mut g = Gen(3001);
        let (mut oui, mut non) = (0, 0);
        for _ in 0..3000 {
            let p = rectangle([5.0 + 90.0 * g.u(), 5.0 + 90.0 * g.u()], [0.5 + 3.0 * g.u(), 0.5 + 2.0 * g.u()], 360.0 * g.u());
            let a = c.piece_legale(&p, 0.25);
            assert_eq!(a, c.piece_legale_brut(&p, 0.25));
            if a { oui += 1 } else { non += 1 }
            let q = [5.0 + 90.0 * g.u(), 5.0 + 90.0 * g.u()];
            assert_eq!(c.dans_region(q), c.dans_region_brut(q));
        }
        assert!(oui > 100 && non > 100, "{oui} {non}");
    }

    #[test]
    fn obstacle_polygonal_et_trou() {
        let mut pl = plateau(Contour::nouveau(carre([20.0, 20.0], 20.0), vec![]));
        pl.obstacles.push((vec![[10.0, 10.0], [20.0, 10.0], [15.0, 18.0]], None));
        pl.trous.push(([30.0, 30.0], 1.6));
        let mut ps = vec![piece([15.0, 12.0], [1.0, 1.0], 0.0)];
        assert_eq!(pl.violations_piece(&ps, 0), vec!["obstacle"]);
        ps[0].c = [21.0, 17.0];
        assert!(pl.violations_piece(&ps, 0).is_empty(), "à côté de la pente du triangle");
        ps[0].c = [30.0, 32.5];
        assert_eq!(pl.violations_piece(&ps, 0), vec!["trou"]);
        ps[0].c = [30.0, 32.9];
        assert!(pl.violations_piece(&ps, 0).is_empty());
        // Obstacle d'une seule face : sans effet sur l'autre face.
        pl.obstacles[0].1 = Some(false);
        ps[0].c = [15.0, 12.0];
        assert!(pl.violations_piece(&ps, 0).is_empty());
    }

    #[test]
    fn pastilles_cms_de_face_opposee() {
        let pl = plateau(Contour::nouveau(carre([20.0, 20.0], 20.0), vec![]));
        let mut dessous = piece([20.0, 20.0], [3.0, 3.0], 0.0);
        dessous.dessus = false;
        dessous.pastilles.push(PastilleLocale { poly: rectangle([2.0, 0.0], [0.5, 0.5], 0.0), expansion_masque: 0.1, traversante: false });
        let dessus = piece([24.0, 20.0], [1.0, 1.0], 0.0); // corps de 23 à 25 ; pastille jusqu'à 22,5
        let mut ps = vec![dessus, dessous];
        assert!(pl.violations_piece(&ps, 0).is_empty(), "0,5 mm > 0,1 mm d'expansion");
        ps[1].pastilles[0].expansion_masque = 0.6;
        assert_eq!(pl.violations_piece(&ps, 0), vec!["pastille_opposee:1"]);
        ps[1].pastilles[0].expansion_masque = -0.6;
        assert!(pl.violations_piece(&ps, 0).is_empty(), "expansion négative sans effet");
        ps[0].c = [23.7, 20.0]; // écart de 0,2 mm
        assert!(pl.violations_piece(&ps, 0).is_empty(), "CMS, expansion négative");
        ps[1].pastilles[0].traversante = true;
        assert_eq!(pl.violations_piece(&ps, 0), vec!["pastille_opposee:1"], "traversante : marge de 0,3 mm");
    }

    #[test]
    fn eloignement_dur() {
        let mut pl = plateau(Contour::nouveau(carre([20.0, 20.0], 20.0), vec![]));
        pl.eloignements.push((0, 1, 5.0));
        let mut ps = vec![piece([10.0, 10.0], [1.0, 1.0], 0.0), piece([15.0, 10.0], [1.0, 1.0], 0.0)];
        assert_eq!(pl.violations_piece(&ps, 0), vec!["eloignement:1"]);
        assert_eq!(pl.violations_piece(&ps, 1), vec!["eloignement:0"]);
        ps[1].c = [17.0, 10.0];
        assert!(pl.placement_legal(&ps));
        ps[1].angle = 45.0; // la diagonale rapproche le coin
        assert!(!pl.placement_legal(&ps));
    }

    #[test]
    fn groupe_rigide() {
        let mut pl = plateau(Contour::nouveau(carre([20.0, 20.0], 20.0), vec![]));
        pl.groupes.push(Groupe { chef: 0, membres: vec![(1, [5.0, 0.0], 0.0)] });
        let mut ps = vec![piece([10.0, 10.0], [1.0, 1.0], 90.0), piece([10.0, 15.0], [1.0, 1.0], 90.0)];
        assert!(pl.placement_legal(&ps));
        ps[1].c = [15.0, 10.0];
        assert_eq!(pl.violations_piece(&ps, 1), vec!["groupe:1"]);
        ps[1].c = [10.0, 15.0];
        ps[1].angle = 0.0;
        assert_eq!(pl.violations_piece(&ps, 0), vec!["groupe:1"]);
    }

    #[test]
    fn au_bord_zone_imposee_grille() {
        let mut pl = plateau(Contour::nouveau(carre([20.0, 20.0], 20.0), vec![]));
        let mut ps = vec![piece([1.5, 20.0], [1.0, 1.0], 0.0)];
        ps[0].au_bord = true;
        assert!(pl.violations_piece(&ps, 0).is_empty(), "à 0,5 mm du bord");
        ps[0].c = [3.0, 20.0];
        assert_eq!(pl.violations_piece(&ps, 0), vec!["au_bord"]);
        ps[0].au_bord = false;
        ps[0].c = [5.0, 20.0];
        ps[0].zone = Some(carre([5.0, 20.0], 3.0));
        assert!(pl.violations_piece(&ps, 0).is_empty());
        ps[0].c = [7.5, 20.0];
        assert_eq!(pl.violations_piece(&ps, 0), vec!["zone"]);
        ps[0].zone = None;
        pl.regles.grille = Some(0.5);
        assert!(pl.violations_piece(&ps, 0).is_empty());
        ps[0].c = [7.55, 20.0];
        assert_eq!(pl.violations_piece(&ps, 0), vec!["grille"]);
        ps[0].fixe = true;
        assert!(pl.violations_piece(&ps, 0).is_empty(), "pièce fixe hors grille");
    }

    /// Coût d'un contour rond de 256 sommets contre un carré, sur les mêmes 20 000 essais (médiane de 3).
    #[test]
    fn contour_rond_pas_plus_lent_que_le_carre() {
        let rondc = Contour::nouveau(rond(256, 50.0), vec![]);
        let carc = Contour::nouveau(carre([50.0, 50.0], 50.0), vec![]);
        let mesure = |c: &Contour| {
            let mut t = Vec::new();
            for g0 in [1u64, 2, 3] {
                let mut g = Gen(g0);
                let d = std::time::Instant::now();
                let mut n = 0;
                for _ in 0..20000 {
                    let p = rectangle([5.0 + 90.0 * g.u(), 5.0 + 90.0 * g.u()], [1.0, 0.6], 360.0 * g.u());
                    n += c.piece_legale(&p, 0.2) as u32;
                }
                t.push((d.elapsed().as_secs_f64(), n));
            }
            t.sort_by(|a, b| a.0.total_cmp(&b.0));
            t[1].0
        };
        let (tr, tc) = (mesure(&rondc), mesure(&carc));
        println!("contour rond {tr:.4} s, carré {tc:.4} s, rapport {:.2}", tr / tc);
        assert!(tr <= 1.2 * tc + 0.002, "rond {tr} carré {tc}");
    }
}
