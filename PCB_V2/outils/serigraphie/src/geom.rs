//! Geometrie plane (mils) : formes convexes gonflees, distances, modele du texte trait d'Altium.
//!
//! Forme = noyau convexe (segment ou polygone convexe) gonfle d'un rayon r.
//!   piste de serigraphie : segment, r = largeur / 2
//!   pastille ronde / oblongue : segment (axe long), r = demi petite dimension (+ expansion)
//!   pastille rectangulaire : polygone (4 coins, angles vifs), r = 0
//!   texte : rectangle d'encre (axes des traits), r = epaisseur / 2
//! distance(A, B) = max(0, distance des noyaux - rA - rB).

pub const MIL_MM: f64 = 0.0254;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct P {
    pub x: f64,
    pub y: f64,
}

pub fn p(x: f64, y: f64) -> P {
    P { x, y }
}

#[derive(Clone, Debug)]
pub enum Noyau {
    Seg(P, P),
    Poly(Vec<P>),
}

#[derive(Clone, Debug)]
pub struct Forme {
    pub noyau: Noyau,
    pub r: f64,
    /// boite englobante [xmin, ymin, xmax, ymax] (gonflement compris)
    pub bb: [f64; 4],
}

impl Forme {
    pub fn seg(a: P, b: P, r: f64) -> Forme {
        let bb = [a.x.min(b.x) - r, a.y.min(b.y) - r, a.x.max(b.x) + r, a.y.max(b.y) + r];
        Forme { noyau: Noyau::Seg(a, b), r, bb }
    }
    pub fn poly(pts: Vec<P>, r: f64) -> Forme {
        let mut bb = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
        for q in &pts {
            bb[0] = bb[0].min(q.x);
            bb[1] = bb[1].min(q.y);
            bb[2] = bb[2].max(q.x);
            bb[3] = bb[3].max(q.y);
        }
        Forme { noyau: Noyau::Poly(pts), r, bb: [bb[0] - r, bb[1] - r, bb[2] + r, bb[3] + r] }
    }
    pub fn rect(b: [f64; 4], r: f64) -> Forme {
        Forme::poly(vec![p(b[0], b[1]), p(b[2], b[1]), p(b[2], b[3]), p(b[0], b[3])], r)
    }
}

pub fn d_pt_seg(q: P, a: P, b: P) -> f64 {
    let (dx, dy) = (b.x - a.x, b.y - a.y);
    let l2 = dx * dx + dy * dy;
    let t = if l2 == 0.0 { 0.0 } else { (((q.x - a.x) * dx + (q.y - a.y) * dy) / l2).clamp(0.0, 1.0) };
    ((q.x - a.x - t * dx).powi(2) + (q.y - a.y - t * dy).powi(2)).sqrt()
}

fn cr(o: P, a: P, b: P) -> f64 {
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
}

pub fn segs_coupent(a: P, b: P, c: P, d: P) -> bool {
    let (d1, d2, d3, d4) = (cr(c, d, a), cr(c, d, b), cr(a, b, c), cr(a, b, d));
    ((d1 > 0.0 && d2 < 0.0) || (d1 < 0.0 && d2 > 0.0)) && ((d3 > 0.0 && d4 < 0.0) || (d3 < 0.0 && d4 > 0.0))
}

pub fn d_seg_seg(a: P, b: P, c: P, d: P) -> f64 {
    if segs_coupent(a, b, c, d) {
        return 0.0;
    }
    d_pt_seg(a, c, d).min(d_pt_seg(b, c, d)).min(d_pt_seg(c, a, b)).min(d_pt_seg(d, a, b))
}

/// point dans un polygone convexe (bord compris), quel que soit le sens de parcours
pub fn dans_poly(q: P, pts: &[P]) -> bool {
    let n = pts.len();
    let mut sg = 0i8;
    for i in 0..n {
        let c = cr(pts[i], pts[(i + 1) % n], q);
        let s = if c > 1e-12 { 1 } else if c < -1e-12 { -1 } else { continue };
        if sg == 0 {
            sg = s;
        } else if s != sg {
            return false;
        }
    }
    true
}

fn sommets(n: &Noyau) -> Vec<P> {
    match n {
        Noyau::Seg(a, b) => vec![*a, *b],
        Noyau::Poly(v) => v.clone(),
    }
}

fn aretes(n: &Noyau) -> Vec<(P, P)> {
    match n {
        Noyau::Seg(a, b) => vec![(*a, *b)],
        Noyau::Poly(v) => (0..v.len()).map(|i| (v[i], v[(i + 1) % v.len()])).collect(),
    }
}

pub fn d_noyaux(a: &Noyau, b: &Noyau) -> f64 {
    if let Noyau::Poly(pa) = a {
        if sommets(b).iter().any(|q| dans_poly(*q, pa)) {
            return 0.0;
        }
    }
    if let Noyau::Poly(pb) = b {
        if sommets(a).iter().any(|q| dans_poly(*q, pb)) {
            return 0.0;
        }
    }
    let mut m = f64::MAX;
    for (x, y) in aretes(a) {
        for (u, v) in aretes(b) {
            m = m.min(d_seg_seg(x, y, u, v));
        }
    }
    m
}

/// boite englobante d'une liste de points
pub fn boite_points(pts: &[P]) -> [f64; 4] {
    let mut bb = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
    for q in pts {
        bb[0] = bb[0].min(q.x);
        bb[1] = bb[1].min(q.y);
        bb[2] = bb[2].max(q.x);
        bb[3] = bb[3].max(q.y);
    }
    bb
}

/// point dans un polygone quelconque (non convexe), par la regle pair-impair
pub fn dans_polygone(q: P, pts: &[P]) -> bool {
    let n = pts.len();
    let mut dedans = false;
    let mut j = n.wrapping_sub(1);
    for i in 0..n {
        let (a, b) = (pts[i], pts[j]);
        if (a.y > q.y) != (b.y > q.y) && q.x < (b.x - a.x) * (q.y - a.y) / (b.y - a.y) + a.x {
            dedans = !dedans;
        }
        j = i;
    }
    dedans
}

fn coins(bb: &[f64; 4]) -> [P; 4] {
    [p(bb[0], bb[1]), p(bb[2], bb[1]), p(bb[2], bb[3]), p(bb[0], bb[3])]
}

/// plus petite distance entre les aretes de la boite et celles du polygone (0 si elles se coupent)
fn d_aretes_boite_polygone(bb: &[f64; 4], pts: &[P]) -> f64 {
    let c = coins(bb);
    let mut m = f64::MAX;
    for i in 0..4 {
        for k in 0..pts.len() {
            m = m.min(d_seg_seg(c[i], c[(i + 1) % 4], pts[k], pts[(k + 1) % pts.len()]));
        }
    }
    m
}

/// boite entierement dans le polygone, a au moins g de son bord
pub fn boite_dans_polygone(bb: &[f64; 4], pts: &[P], g: f64) -> bool {
    pts.len() >= 3 && coins(bb).iter().all(|q| dans_polygone(*q, pts)) && d_aretes_boite_polygone(bb, pts) >= g
}

/// boite entierement hors du polygone, a au moins g de son bord
pub fn boite_hors_polygone(bb: &[f64; 4], pts: &[P], g: f64) -> bool {
    if pts.len() < 3 {
        return true;
    }
    if coins(bb).iter().any(|q| dans_polygone(*q, pts)) {
        return false;
    }
    if pts.iter().any(|q| q.x >= bb[0] && q.x <= bb[2] && q.y >= bb[1] && q.y <= bb[3]) {
        return false;
    }
    d_aretes_boite_polygone(bb, pts) >= g
}

/// distance entre boites (0 si elles se touchent ou se recouvrent)
pub fn d_boites(a: &[f64; 4], b: &[f64; 4]) -> f64 {
    let dx = (a[0] - b[2]).max(b[0] - a[2]).max(0.0);
    let dy = (a[1] - b[3]).max(b[1] - a[3]).max(0.0);
    (dx * dx + dy * dy).sqrt()
}

pub fn boites_se_coupent(a: &[f64; 4], b: &[f64; 4]) -> bool {
    a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]
}

/// distance bord a bord des deux formes
pub fn dist(a: &Forme, b: &Forme) -> f64 {
    (d_noyaux(&a.noyau, &b.noyau) - a.r - b.r).max(0.0)
}

/// vrai si dist(a, b) < seuil (pre-filtre par boites)
pub fn plus_pres_que(a: &Forme, b: &Forme, seuil: f64) -> bool {
    if d_boites(&a.bb, &b.bb) >= seuil {
        return false;
    }
    dist(a, b) < seuil
}

pub fn rot(x: f64, y: f64, deg: f64) -> (f64, f64) {
    let k = deg / 90.0;
    if (k - k.round()).abs() < 1e-9 {
        let (c, s) = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)][(k.round() as i64).rem_euclid(4) as usize];
        return (x * c - y * s, x * s + y * c);
    }
    let a = deg.to_radians();
    (x * a.cos() - y * a.sin(), x * a.sin() + y * a.cos())
}

/// rectangle (sx, sy) centre en (cx, cy) tourne de deg
pub fn rect_tourne(cx: f64, cy: f64, sx: f64, sy: f64, deg: f64, r: f64) -> Forme {
    let pts = [(-sx / 2.0, -sy / 2.0), (sx / 2.0, -sy / 2.0), (sx / 2.0, sy / 2.0), (-sx / 2.0, sy / 2.0)]
        .iter()
        .map(|&(u, v)| {
            let (a, b) = rot(u, v, deg);
            p(cx + a, cy + b)
        })
        .collect();
    Forme::poly(pts, r)
}

/// pastille Altium : rect = TopShape 2 ; rond / oblong = TopShape 1 (extremites rondes si X != Y)
pub fn forme_pastille(x: f64, y: f64, sx: f64, sy: f64, deg: f64, rect: bool, expansion: f64) -> Forme {
    if rect {
        return rect_tourne(x, y, sx + 2.0 * expansion, sy + 2.0 * expansion, deg, 0.0);
    }
    let r = sx.min(sy) / 2.0 + expansion;
    let l = (sx - sy).abs() / 2.0;
    let (ux, uy) = if sx >= sy { rot(l, 0.0, deg) } else { rot(0.0, l, deg) };
    Forme::seg(p(x - ux, y - uy), p(x + ux, y + uy), r)
}

/// arc Altium (sens trigonometrique de a0 a a1, degres) -> points sur l'arc (cordes de fleche <= fleche)
pub fn arc_points(c: P, rr: f64, a0: f64, a1: f64, fleche: f64) -> Vec<P> {
    let mut span = (a1 - a0).rem_euclid(360.0);
    if span < 1e-9 {
        span = 360.0;
    }
    let pas = if rr > fleche { 2.0 * (1.0 - fleche / rr).acos().to_degrees() } else { 360.0 };
    let n = ((span / pas).ceil() as usize).clamp(2, 128);
    (0..=n)
        .map(|i| {
            let t = (a0 + span * i as f64 / n as f64).to_radians();
            p(c.x + rr * t.cos(), c.y + rr * t.sin())
        })
        .collect()
}

// ---------------------------------------------------------------------------------------------
// Texte en police trait d'Altium (police « Default »), calibre sur les Bounds exportes (textes.json) :
//   * repere du texte : u le long de la ligne, v vers le haut ; ancre (X, Y) = debut de la ligne de base
//     (axe du premier trait, bas des majuscules) ;
//   * monde = (X, Y) + R(Rotation) . (m u, v), m = -1 si MirrorFlag (face inferieure), +1 sinon ;
//     donc pour un texte miroir a 0 deg, (X, Y) est le coin bas-DROIT de l'encre vue de dessus ;
//   * l'encre (axes des traits) occupe v dans [0, h] (h = Height), et v >= -h/6 si le texte contient '_' ;
//   * largeur des axes, en unites de h/6 : somme des avances des caracteres sauf le dernier (6, sauf '1', '.'
//     et 'I' : 5) + extension du dernier (4, sauf '1' : 2, 'k' : 3) ;
//   * Altium donne des Bounds = encre gonflee de l'epaisseur entiere ; le trait reel deborde de w/2.
//   Verifie a 0,2 mil pres sur les 79 textes trait exportes (toutes rotations, miroir ou non) ; l'unite reelle
//   d'Altium est h/6 x 0,99967 : le modele surestime de 0,03 %, du cote sur.
// ---------------------------------------------------------------------------------------------

fn avance(c: char) -> f64 {
    match c {
        '1' | '.' | 'I' => 5.0,
        _ => 6.0,
    }
}

fn extension_dernier(c: char) -> f64 {
    match c {
        '1' => 2.0,
        'k' => 3.0,
        _ => 4.0,
    }
}

pub fn largeur_encre(t: &str, h: f64) -> f64 {
    let v: Vec<char> = t.chars().collect();
    if v.is_empty() {
        return 0.0;
    }
    let u: f64 = v[..v.len() - 1].iter().map(|&c| avance(c)).sum::<f64>() + extension_dernier(v[v.len() - 1]);
    u * h / 6.0
}

pub fn bas_encre(t: &str, h: f64) -> f64 {
    if t.chars().any(|c| "_gjpqy,".contains(c)) {
        -h / 6.0
    } else {
        0.0
    }
}

/// boite (sans gonflement) de l'encre d'un texte d'ancre (x, y)
pub fn boite_encre(t: &str, x: f64, y: f64, h: f64, rot_deg: f64, miroir: bool) -> [f64; 4] {
    let l = largeur_encre(t, h);
    let b = bas_encre(t, h);
    let m = if miroir { -1.0 } else { 1.0 };
    let mut bb = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
    for (u, v) in [(0.0, b), (l, b), (l, h), (0.0, h)] {
        let (a, c) = rot(m * u, v, rot_deg);
        bb[0] = bb[0].min(x + a);
        bb[1] = bb[1].min(y + c);
        bb[2] = bb[2].max(x + a);
        bb[3] = bb[3].max(y + c);
    }
    bb
}

/// forme DRC d'un texte trait (rotations multiples de 90 deg) : rectangle d'encre gonfle de w/2
pub fn forme_texte(t: &str, x: f64, y: f64, h: f64, w: f64, rot_deg: f64, miroir: bool) -> Forme {
    Forme::rect(boite_encre(t, x, y, h, rot_deg, miroir), w / 2.0)
}

/// ancre (x, y) qui place le coin bas-gauche de la boite d'encre en (bx, by)
pub fn ancre_pour_boite(t: &str, bx: f64, by: f64, h: f64, rot_deg: f64, miroir: bool) -> (f64, f64) {
    let b0 = boite_encre(t, 0.0, 0.0, h, rot_deg, miroir);
    (bx - b0[0], by - b0[1])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn proche(a: f64, b: f64, e: f64) -> bool {
        (a - b).abs() <= e
    }

    #[test]
    fn distance_segments() {
        assert!(proche(d_seg_seg(p(0., 0.), p(10., 0.), p(0., 5.), p(10., 5.)), 5.0, 1e-12));
        assert_eq!(d_seg_seg(p(0., 0.), p(10., 10.), p(0., 10.), p(10., 0.)), 0.0);
        assert!(proche(d_seg_seg(p(0., 0.), p(1., 0.), p(4., 4.), p(4., 10.)), 5.0, 1e-12));
    }

    #[test]
    fn distance_capsule_rectangle() {
        // pastille 20 x 10 centree en 0 ; piste horizontale y = 20, largeur 6 -> 20 - 5 - 3 = 12
        let pad = forme_pastille(0., 0., 20., 10., 0., true, 0.);
        let t = Forme::seg(p(-50., 20.), p(50., 20.), 3.);
        assert!(proche(dist(&pad, &t), 12.0, 1e-9));
        // rotation 90 : la pastille devient 10 x 20 -> 20 - 10 - 3 = 7
        let pad90 = forme_pastille(0., 0., 20., 10., 90., true, 0.);
        assert!(proche(dist(&pad90, &t), 7.0, 1e-9));
        // expansion 4 : 7 - 4 = 3
        let pad90e = forme_pastille(0., 0., 20., 10., 90., true, 4.);
        assert!(proche(dist(&pad90e, &t), 3.0, 1e-9));
    }

    #[test]
    fn distance_coin_rectangle_vif() {
        // coin vif : distance euclidienne au coin (pas de coin arrondi)
        let pad = forme_pastille(0., 0., 10., 10., 0., true, 0.);
        let q = Forme::seg(p(8., 8.), p(8., 8.), 0.);
        assert!(proche(dist(&pad, &q), (18.0f64).sqrt(), 1e-9));
    }

    #[test]
    fn pastille_oblongue() {
        // 30 x 10, extremites rondes : bout a x = 15 ; point (20, 0) -> 5 ; point (10, 8) -> 3
        let pad = forme_pastille(0., 0., 30., 10., 0., false, 0.);
        let a = Forme::seg(p(20., 0.), p(20., 0.), 0.);
        let b = Forme::seg(p(10., 8.), p(10., 8.), 0.);
        assert!(proche(dist(&pad, &a), 5.0, 1e-9));
        assert!(proche(dist(&pad, &b), 3.0, 1e-9));
        // ronde
        let r = forme_pastille(0., 0., 10., 10., 0., false, 2.);
        assert!(proche(dist(&r, &a), 13.0, 1e-9));
    }

    #[test]
    fn recouvrement_nul() {
        let a = Forme::rect([0., 0., 10., 10.], 0.);
        let b = Forme::rect([5., 5., 20., 20.], 0.);
        assert_eq!(dist(&a, &b), 0.0);
        let c = Forme::rect([2., 2., 3., 3.], 0.); // contenu
        assert_eq!(dist(&a, &c), 0.0);
    }

    #[test]
    fn arc_points_sur_cercle() {
        let v = arc_points(p(1., 2.), 10., 0., 90., 0.01);
        assert!(proche(v[0].x, 11., 1e-9) && proche(v[0].y, 2., 1e-9));
        let d = v.last().unwrap();
        assert!(proche(d.x, 1., 1e-9) && proche(d.y, 12., 1e-9));
        for q in &v {
            assert!(proche(((q.x - 1.).powi(2) + (q.y - 2.).powi(2)).sqrt(), 10., 1e-9));
        }
        // cercle complet
        let c = arc_points(p(0., 0.), 5., 0., 360., 0.01);
        assert!(proche(c[0].x, c.last().unwrap().x, 1e-9));
    }

    #[test]
    fn polygone_non_convexe() {
        // contour en L : 100 x 100 prive du carre [50, 100] x [50, 100]
        let l = vec![p(0., 0.), p(100., 0.), p(100., 50.), p(50., 50.), p(50., 100.), p(0., 100.)];
        assert!(dans_polygone(p(25., 75.), &l));
        assert!(!dans_polygone(p(75., 75.), &l));
        assert!(boite_dans_polygone(&[10., 10., 40., 40.], &l, 5.0));
        assert!(!boite_dans_polygone(&[10., 10., 40., 40.], &l, 15.0));
        // boite a cheval sur l'angle rentrant
        assert!(!boite_dans_polygone(&[40., 40., 60., 60.], &l, 0.0));
        // decoupe carree [40, 60]^2 : boite a 5 de son bord
        let d = vec![p(40., 40.), p(60., 40.), p(60., 60.), p(40., 60.)];
        assert!(boite_hors_polygone(&[65., 40., 70., 45.], &d, 4.0));
        assert!(!boite_hors_polygone(&[65., 40., 70., 45.], &d, 6.0));
        assert!(!boite_hors_polygone(&[45., 45., 50., 50.], &d, 0.0)); // dedans
        assert!(!boite_hors_polygone(&[0., 0., 100., 100.], &d, 0.0)); // l'englobe
    }
}
