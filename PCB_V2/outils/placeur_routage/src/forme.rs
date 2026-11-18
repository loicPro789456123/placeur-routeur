//! Formes exactes du cuivre et des perçages (étape 7b, §2.2 et §4).
//!
//! Distances signées exactes (négatives à l'intérieur) des pastilles rondes, oblongues, rectangulaires, à coins
//! arrondis et polygonales, sous une rotation quelconque (multiples de 90° traités sans erreur d'arrondi), des piles
//! (face de la pièce, face opposée, couches internes) et des trous ronds, carrés ou oblongs, tournés et décalés.
//! L'isolement se décide sur ces distances, jamais sur les demi-dimensions `hw`/`hh`.

use crate::donnees::{FormeCuivre, Pastille, Percage};

/// Position et orientation d'un repère dans le monde (mm, degrés, sens trigonométrique).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pose {
    pub x: f64,
    pub y: f64,
    pub angle: f64,
}

/// Cosinus et sinus exacts aux multiples de 90°.
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

impl Pose {
    pub fn vers_monde(&self, p: [f64; 2]) -> [f64; 2] {
        let (c, s) = cos_sin(self.angle);
        [self.x + c * p[0] - s * p[1], self.y + s * p[0] + c * p[1]]
    }
    /// Repère d'un enfant (pastille, trou) placé en `local` dans ce repère, tourné de `angle` en plus.
    pub fn composer(&self, local: [f64; 2], angle: f64) -> Pose {
        let q = self.vers_monde(local);
        Pose { x: q[0], y: q[1], angle: self.angle + angle }
    }
}

/// Forme exacte dans le monde.
#[derive(Clone, Debug)]
pub enum FormeExacte {
    /// Segment [a, b] épaissi de `r` (rond si a = b, oblong sinon).
    Stade { a: [f64; 2], b: [f64; 2], r: f64 },
    /// Rectangle de centre `c`, d'axe unitaire `u`, de demi-côtés `dl`, `dh`, coins arrondis de rayon `r`.
    Rect { c: [f64; 2], u: [f64; 2], dl: f64, dh: f64, r: f64 },
    /// Polygone simple (sommets dans le monde).
    Polygone(Vec<[f64; 2]>),
}

fn dist_segment(p: [f64; 2], a: [f64; 2], b: [f64; 2]) -> f64 {
    let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
    let l2 = dx * dx + dy * dy;
    let t = if l2 > 0.0 { (((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2).clamp(0.0, 1.0) } else { 0.0 };
    let (qx, qy) = (a[0] + t * dx - p[0], a[1] + t * dy - p[1]);
    (qx * qx + qy * qy).sqrt()
}

fn dans_polygone(p: [f64; 2], pts: &[[f64; 2]]) -> bool {
    let mut dedans = false;
    let n = pts.len();
    let mut j = n.wrapping_sub(1);
    for i in 0..n {
        let (a, b) = (pts[i], pts[j]);
        if (a[1] > p[1]) != (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0] {
            dedans = !dedans;
        }
        j = i;
    }
    dedans
}

impl FormeExacte {
    /// Distance signée exacte du point `p` au bord (négative à l'intérieur).
    pub fn distance(&self, p: [f64; 2]) -> f64 {
        match self {
            FormeExacte::Stade { a, b, r } => dist_segment(p, *a, *b) - r,
            FormeExacte::Rect { c, u, dl, dh, r } => {
                let (vx, vy) = (p[0] - c[0], p[1] - c[1]);
                let lx = (vx * u[0] + vy * u[1]).abs() - (dl - r);
                let ly = (-vx * u[1] + vy * u[0]).abs() - (dh - r);
                let ext = (lx.max(0.0).powi(2) + ly.max(0.0).powi(2)).sqrt();
                ext + lx.max(ly).min(0.0) - r
            }
            FormeExacte::Polygone(pts) => {
                let n = pts.len();
                let mut d = f64::INFINITY;
                for i in 0..n {
                    d = d.min(dist_segment(p, pts[i], pts[(i + 1) % n]));
                }
                if dans_polygone(p, pts) { -d } else { d }
            }
        }
    }

    /// Boîte englobante exacte [xmin, ymin, xmax, ymax].
    pub fn boite(&self) -> [f64; 4] {
        match self {
            FormeExacte::Stade { a, b, r } => {
                [a[0].min(b[0]) - r, a[1].min(b[1]) - r, a[0].max(b[0]) + r, a[1].max(b[1]) + r]
            }
            FormeExacte::Rect { c, u, dl, dh, r } => {
                let ex = (dl - r) * u[0].abs() + (dh - r) * u[1].abs() + r;
                let ey = (dl - r) * u[1].abs() + (dh - r) * u[0].abs() + r;
                [c[0] - ex, c[1] - ey, c[0] + ex, c[1] + ey]
            }
            FormeExacte::Polygone(pts) => pts.iter().fold(
                [f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY],
                |b, q| [b[0].min(q[0]), b[1].min(q[1]), b[2].max(q[0]), b[3].max(q[1])],
            ),
        }
    }
}

/// Forme de cuivre (`forme`, `largeur`, `hauteur`, `rayon_coin`, `points` locaux) posée dans le repère `pose`.
pub fn forme_cuivre(forme: &str, largeur: f64, hauteur: f64, rayon_coin: f64, points: &[[f64; 2]], pose: Pose) -> FormeExacte {
    let (c, s) = cos_sin(pose.angle);
    let centre = [pose.x, pose.y];
    match forme {
        "rond" => FormeExacte::Stade { a: centre, b: centre, r: 0.5 * largeur.max(hauteur) },
        "oblong" => {
            let r = 0.5 * largeur.min(hauteur);
            let demi = 0.5 * (largeur - hauteur).abs();
            let local = if largeur >= hauteur { [demi, 0.0] } else { [0.0, demi] };
            let a = pose.vers_monde(local);
            let b = pose.vers_monde([-local[0], -local[1]]);
            FormeExacte::Stade { a, b, r }
        }
        "polygone" => FormeExacte::Polygone(points.iter().map(|q| pose.vers_monde(*q)).collect()),
        _ => {
            let r = if forme == "rect_arrondi" { rayon_coin.clamp(0.0, 0.5 * largeur.min(hauteur)) } else { 0.0 };
            FormeExacte::Rect { c: centre, u: [c, s], dl: 0.5 * largeur, dh: 0.5 * hauteur, r }
        }
    }
}

/// Couche vue par une pastille de pile (§2.2).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CouchePile {
    FacePiece,
    FaceOpposee,
    Interne,
}

/// Repère de la pastille dans le monde, la pièce étant posée en `piece`.
pub fn pose_pastille(p: &Pastille, piece: Pose) -> Pose {
    piece.composer([p.lx, p.ly], p.rotation)
}

/// Cuivre exact de la pastille sur une couche (pile comprise).
pub fn cuivre_pastille(p: &Pastille, piece: Pose, couche: CouchePile) -> FormeExacte {
    let pp = pose_pastille(p, piece);
    let fc: Option<&FormeCuivre> = match (&p.pile, couche) {
        (Some((o, _)), CouchePile::FaceOpposee) => Some(o),
        (Some((_, i)), CouchePile::Interne) => Some(i),
        _ => None,
    };
    match fc {
        None => forme_cuivre(&p.forme, p.largeur, p.hauteur, p.rayon_coin, &p.points, pp),
        Some(f) => {
            let q = pp.composer(f.decalage, f.rotation);
            forme_cuivre(&f.forme, f.largeur, f.hauteur, f.rayon_coin, &f.points, q)
        }
    }
}

/// Trou exact (rond, carré ou oblong ; tourné et décalé) d'un perçage posé dans le repère `pose`.
pub fn trou_exact(t: &Percage, pose: Pose) -> FormeExacte {
    let q = pose.composer(t.decalage, t.rotation);
    match t.forme.as_str() {
        "carre" => forme_cuivre("rect", t.diametre, t.diametre, 0.0, &[], q),
        "oblong" => forme_cuivre("oblong", t.longueur.max(t.diametre), t.diametre, 0.0, &[], q),
        _ => forme_cuivre("rond", t.diametre, t.diametre, 0.0, &[], q),
    }
}

/// Grille régulière (origine du centre de la cellule (0, 0), pas, dimensions).
#[derive(Clone, Copy, Debug)]
pub struct Grille {
    pub x0: f64,
    pub y0: f64,
    pub pas: f64,
    pub nx: usize,
    pub ny: usize,
}

impl Grille {
    pub fn centre(&self, i: usize, j: usize) -> [f64; 2] {
        [self.x0 + i as f64 * self.pas, self.y0 + j as f64 * self.pas]
    }
}

/// Marque `bloque[j * nx + i]` pour toute cellule dont le centre est à moins de `requis` de la forme exacte.
/// Le balayage est limité à la boîte exacte gonflée de `requis` ; la décision est la distance exacte.
pub fn marquer(bloque: &mut [bool], g: &Grille, f: &FormeExacte, requis: f64) {
    let b = f.boite();
    let i0 = (((b[0] - requis - g.x0) / g.pas).floor().max(0.0)) as usize;
    let j0 = (((b[1] - requis - g.y0) / g.pas).floor().max(0.0)) as usize;
    let i1 = ((((b[2] + requis - g.x0) / g.pas).ceil()).max(-1.0) as i64 + 1).clamp(0, g.nx as i64) as usize;
    let j1 = ((((b[3] + requis - g.y0) / g.pas).ceil()).max(-1.0) as i64 + 1).clamp(0, g.ny as i64) as usize;
    for j in j0..j1 {
        for i in i0..i1 {
            if f.distance(g.centre(i, j)) < requis {
                bloque[j * g.nx + i] = true;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const EPS: f64 = 1e-9;

    fn grille() -> Grille {
        Grille { x0: -3.0, y0: -3.0, pas: 0.05, nx: 121, ny: 121 }
    }

    /// Échantillonne finement l'intérieur d'une forme locale (dans son repère) et le passe au monde.
    fn echantillons(dedans: impl Fn([f64; 2]) -> bool, pose: Pose, demi: f64) -> Vec<[f64; 2]> {
        let n = 160;
        let mut v = Vec::new();
        for a in 0..=n {
            for b in 0..=n {
                let p = [-demi + 2.0 * demi * a as f64 / n as f64, -demi + 2.0 * demi * b as f64 / n as f64];
                if dedans(p) {
                    v.push(pose.vers_monde(p));
                }
            }
        }
        v
    }

    /// Aucune cellule jugée libre à moins de `requis` (moins la finesse d'échantillonnage) d'un point de cuivre.
    fn verifier_aucune_cellule_libre_trop_proche(f: &FormeExacte, cuivre: &[[f64; 2]], requis: f64) {
        let g = grille();
        let mut bl = vec![false; g.nx * g.ny];
        marquer(&mut bl, &g, f, requis);
        assert!(!cuivre.is_empty());
        let mut libres_pres = 0;
        for j in 0..g.ny {
            for i in 0..g.nx {
                if bl[j * g.nx + i] {
                    continue;
                }
                let c = g.centre(i, j);
                let dmin = cuivre.iter().map(|q| ((q[0] - c[0]).powi(2) + (q[1] - c[1]).powi(2)).sqrt()).fold(f64::INFINITY, f64::min);
                if dmin < requis - 1e-6 {
                    libres_pres += 1;
                }
                // la distance exacte ne peut pas excéder la distance à un point du cuivre
                assert!(f.distance(c) <= dmin + 1e-9);
            }
        }
        assert_eq!(libres_pres, 0);
    }

    #[test]
    fn rotations_exactes_aux_quarts_de_tour() {
        for (a, cs) in [(0.0, (1.0, 0.0)), (90.0, (0.0, 1.0)), (180.0, (-1.0, 0.0)), (270.0, (0.0, -1.0)), (-90.0, (0.0, -1.0))] {
            assert_eq!(cos_sin(a), cs);
        }
        let p = Pose { x: 1.0, y: 2.0, angle: 90.0 };
        assert_eq!(p.vers_monde([0.3, 0.0]), [1.0, 2.3]);
    }

    #[test]
    fn distances_signees_exactes() {
        let r = forme_cuivre("rect", 2.0, 1.0, 0.0, &[], Pose { x: 0.0, y: 0.0, angle: 0.0 });
        assert!((r.distance([2.0, 0.0]) - 1.0).abs() < EPS);
        assert!((r.distance([0.0, 0.0]) + 0.5).abs() < EPS);
        assert!((r.distance([2.0, 1.5]) - 2f64.sqrt()).abs() < EPS);
        let ra = forme_cuivre("rect_arrondi", 2.0, 1.0, 0.25, &[], Pose { x: 0.0, y: 0.0, angle: 0.0 });
        // coin arrondi : distance au centre du congé (0,75 ; 0,25) moins 0,25
        let d = ((2.0f64 - 0.75).powi(2) + (1.5f64 - 0.25).powi(2)).sqrt() - 0.25;
        assert!((ra.distance([2.0, 1.5]) - d).abs() < EPS);
        let o = forme_cuivre("oblong", 2.0, 1.0, 0.0, &[], Pose { x: 0.0, y: 0.0, angle: 90.0 });
        assert!((o.distance([0.0, 1.5]) - 0.5).abs() < EPS);
        assert!((o.distance([1.0, 0.0]) - 0.5).abs() < EPS);
        let c = forme_cuivre("rond", 1.0, 1.0, 0.0, &[], Pose { x: 1.0, y: 1.0, angle: 33.0 });
        assert!((c.distance([2.0, 1.0]) - 0.5).abs() < EPS);
        // rectangle à 45° : le coin est sur l'axe x à √(1² + 0,5²)… ici carré 1×1, coin à √2/2
        let q = forme_cuivre("rect", 1.0, 1.0, 0.0, &[], Pose { x: 0.0, y: 0.0, angle: 45.0 });
        assert!((q.distance([1.0, 0.0]) - (1.0 - 0.5 * 2f64.sqrt())).abs() < 1e-12);
        // les demi-dimensions alignées (hw = hh = 0,5) jugeraient faussement ce point à 0,5 du cuivre
        assert!(q.distance([1.0, 0.0]) < 0.5);
    }

    fn octogone(cote: f64) -> Vec<[f64; 2]> {
        let k = cote * (1.0 + 2f64.sqrt()) / 2.0;
        let h = cote / 2.0;
        vec![[k, h], [h, k], [-h, k], [-k, h], [-k, -h], [-h, -k], [h, -k], [k, -h]]
    }

    #[test]
    fn pastilles_tournees_a_45_degres_aucune_cellule_libre_trop_proche() {
        let pose = Pose { x: 0.13, y: -0.07, angle: 45.0 };
        let requis = 0.2;
        // rond
        let f = forme_cuivre("rond", 1.2, 1.2, 0.0, &[], pose);
        let e = echantillons(|p| p[0] * p[0] + p[1] * p[1] <= 0.36, pose, 0.6);
        verifier_aucune_cellule_libre_trop_proche(&f, &e, requis);
        // rectangle
        let f = forme_cuivre("rect", 2.0, 0.8, 0.0, &[], pose);
        let e = echantillons(|p| p[0].abs() <= 1.0 && p[1].abs() <= 0.4, pose, 1.0);
        verifier_aucune_cellule_libre_trop_proche(&f, &e, requis);
        // oblong
        let f = forme_cuivre("oblong", 2.0, 0.8, 0.0, &[], pose);
        let e = echantillons(|p| dist_segment(p, [-0.6, 0.0], [0.6, 0.0]) <= 0.4, pose, 1.0);
        verifier_aucune_cellule_libre_trop_proche(&f, &e, requis);
        // rectangle arrondi
        let f = forme_cuivre("rect_arrondi", 2.0, 1.0, 0.3, &[], pose);
        let e = echantillons(
            |p| {
                let lx = (p[0].abs() - 0.7).max(0.0);
                let ly = (p[1].abs() - 0.2).max(0.0);
                lx * lx + ly * ly <= 0.09 && p[0].abs() <= 1.0 && p[1].abs() <= 0.5
            },
            pose,
            1.0,
        );
        verifier_aucune_cellule_libre_trop_proche(&f, &e, requis);
        // octogone (polygone)
        let oct = octogone(0.6);
        let f = forme_cuivre("polygone", 0.0, 0.0, 0.0, &oct, pose);
        let e = echantillons(|p| dans_polygone(p, &oct), pose, 0.8);
        verifier_aucune_cellule_libre_trop_proche(&f, &e, requis);
    }

    fn pastille_test() -> Pastille {
        Pastille {
            id: "p".into(),
            nom: "1".into(),
            reseau: "N".into(),
            lx: 1.0,
            ly: 0.0,
            forme: "rect".into(),
            largeur: 2.0,
            hauteur: 1.0,
            rotation: 45.0,
            rayon_coin: 0.0,
            points: vec![],
            couches: vec![],
            face: "dessus".into(),
            trou: Some(Percage {
                diametre: 0.4,
                forme: "rond".into(),
                longueur: 0.0,
                rotation: 0.0,
                decalage: [0.5, 0.0],
                metallise: Some(true),
            }),
            traversante: true,
            expansion_masque: 0.0,
            pile: Some((
                FormeCuivre { forme: "rond".into(), largeur: 1.0, hauteur: 1.0, rotation: 0.0, rayon_coin: 0.0, points: vec![], decalage: [0.0, 0.0] },
                FormeCuivre { forme: "rond".into(), largeur: 0.8, hauteur: 0.8, rotation: 0.0, rayon_coin: 0.0, points: vec![], decalage: [0.5, 0.0] },
            )),
            via_dans_pastille_permis: false,
            classe_isolement: 0,
            hw: 1.0,
            hh: 0.5,
            th: true,
        }
    }

    #[test]
    fn pile_par_couche() {
        let p = pastille_test();
        let piece = Pose { x: 0.0, y: 0.0, angle: 90.0 };
        // centre de la pastille : (0, 1) ; repère à 135°
        let dessus = cuivre_pastille(&p, piece, CouchePile::FacePiece);
        let dessous = cuivre_pastille(&p, piece, CouchePile::FaceOpposee);
        let interne = cuivre_pastille(&p, piece, CouchePile::Interne);
        assert!((dessous.distance([0.0, 1.0]) + 0.5).abs() < EPS);
        assert!((dessous.distance([0.0, 2.0]) - 0.5).abs() < EPS);
        // interne : disque de 0,4 de rayon centré à 0,5 le long de l'axe à 135°
        let s = 0.5 / 2f64.sqrt();
        let ci = [-s, 1.0 + s];
        assert!((interne.distance(ci) + 0.4).abs() < EPS);
        assert!(dessus.distance([0.0, 1.0]) < 0.0);
        assert!(matches!(dessus, FormeExacte::Rect { .. }));
    }

    #[test]
    fn trou_decale_exclusion_de_via_autour_du_trou_reel() {
        let p = pastille_test();
        let piece = Pose { x: 0.0, y: 0.0, angle: 0.0 };
        let pp = pose_pastille(&p, piece);
        let t = trou_exact(p.trou.as_ref().unwrap(), pp);
        // centre réel du trou : (1, 0) + R(45°)(0,5 ; 0)
        let s = 0.5 / 2f64.sqrt();
        let ct = [1.0 + s, s];
        assert!((t.distance(ct) + 0.2).abs() < EPS);
        // exclusion de via : trou à trou 0,25 + rayon du trou de via 0,15
        let g = grille();
        let mut bl = vec![false; g.nx * g.ny];
        marquer(&mut bl, &g, &t, 0.4);
        for j in 0..g.ny {
            for i in 0..g.nx {
                let c = g.centre(i, j);
                let d = ((c[0] - ct[0]).powi(2) + (c[1] - ct[1]).powi(2)).sqrt() - 0.2;
                assert_eq!(bl[j * g.nx + i], d < 0.4, "cellule ({i}, {j})");
            }
        }
        // point symétrique du trou par rapport au centre de la pastille : à 1,0 du trou réel, donc libre ;
        // un trou supposé centré l'aurait bloqué (0,5 - 0,2 = 0,3 < 0,4)
        let sym = [1.0 - s, -s];
        assert!(t.distance(sym) >= 0.4);
        let centre = forme_cuivre("rond", 0.4, 0.4, 0.0, &[], pp);
        assert!(centre.distance(sym) < 0.4);
    }

    #[test]
    fn trous_carre_et_oblong_tournes() {
        let carre = Percage { diametre: 1.0, forme: "carre".into(), longueur: 0.0, rotation: 45.0, decalage: [0.0, 0.0], metallise: Some(false) };
        let t = trou_exact(&carre, Pose { x: 0.0, y: 0.0, angle: 0.0 });
        assert!((t.distance([1.0, 0.0]) - (1.0 - 0.5 * 2f64.sqrt())).abs() < 1e-12);
        let obl = Percage { diametre: 0.6, forme: "oblong".into(), longueur: 1.6, rotation: 90.0, decalage: [0.2, 0.0], metallise: Some(true) };
        let t = trou_exact(&obl, Pose { x: 0.0, y: 0.0, angle: 0.0 });
        // centre (0,2 ; 0), axe vertical, demi-segment 0,5, rayon 0,3
        assert!((t.distance([0.2, 1.0]) - 0.2).abs() < EPS);
        assert!((t.distance([0.7, 0.0]) - 0.2).abs() < EPS);
    }

    #[test]
    fn piece_a_12_5_degres_pastilles_posees_exactement() {
        let mut p = pastille_test();
        p.rotation = 0.0;
        let piece = Pose { x: 10.0, y: 5.0, angle: 12.5 };
        let pp = pose_pastille(&p, piece);
        let a = 12.5f64.to_radians();
        assert!((pp.x - (10.0 + a.cos())).abs() < EPS && (pp.y - (5.0 + a.sin())).abs() < EPS);
        assert_eq!(pp.angle, 12.5);
        let f = cuivre_pastille(&p, piece, CouchePile::FacePiece);
        // coin du rectangle 2×1 dans le repère tourné : à distance nulle
        let coin = pp.vers_monde([1.0, 0.5]);
        assert!(f.distance(coin).abs() < 1e-12);
        let b = f.boite();
        assert!(b[0] <= coin[0] && coin[0] <= b[2] && b[1] <= coin[1] && coin[1] <= b[3]);
    }
}
