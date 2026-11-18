#![allow(dead_code)]
//! Validité de référence : implémentation Rust de la définition unique de §4 du contrat (lot E).
//!
//! Le moteur (lot A) déclare ce fichier par `mod validite;` et l'appelle (§4.6) ; la caisse d'essai du lot E
//! (`corpus/validite_rs/`) le compile en bibliothèque et en binaire. Le module est autonome : `serde_json` et la
//! bibliothèque standard seulement, aucun chemin vers un autre module de la caisse qui l'inclut, aucune lecture de
//! fichier ni de l'environnement, aucune table de hachage (parcours ordonnés, résultats déterministes, §0.2-6),
//! aucune panique sur une entrée invalide : `Err(texte)`, texte qui nomme le champ fautif, jamais un chemin de
//! fichier (§5.1).
//!
//! Unités : les données (`donnees_placeur/2`) sont en mm ; le placement (`placements/2`), le routage (`routage/2`)
//! et la couture (`vias_couture/2`) sont en mil (1 mil = 0,0254 mm, §0.4). Les sorties (`verdict/2`,
//! `controle_placement/1`, coût commun) sont en mm.
//!
//! # État : étape 7 — routage (§4.1 à §4.4, §4.7) et placement (§4.5) complets
//!
//! Les types publics et les signatures sont ceux de §5.1, figés au jalon E0.
//!
//! - **Fait et testé sur les cas du corpus** : module public [`geometrie`] (avenant 2.2, A.8) ; objets de §4.1
//!   (pastilles avec `pile`, pièces virtuelles, régions et régions de liaison, vias d'empreinte, cuivre fixe, cuivre
//!   de trou, pistes, vias, surfaces, vias de couture, zones de plan) ; contacts de §4.2 dans les deux modes ;
//!   connectivité de §4.3 ; les 12 violations de §4.4 (isolement par la matrice des classes, gardes de bord au
//!   contour polygonal, de découpe et de trou, zones interdites élargies, largeur en mil arrondie au millième,
//!   dimensions des vias, via sous CMS et sa dérogation, trou à trou, antenne, couche non permise, réseau inconnu) ;
//!   avertissements ; marge d'isolement minimale ; mesures de §4.7 (écart moyen et minimal compris) ;
//!   `cout_commun` complet ; les 14 clés du contrôle de placement de §4.5 et `infos.eloignements` ; refus des
//!   entrées invalides ; sérialisation (`detail_violations` tronqué à 1 000, 15 exemples par type). Conventions du
//!   corpus : `corpus/LISEZMOI.md`.
//! - Reste provisoire (convention 34) : la position d'une violation entre deux objets est le milieu de leurs centres.

use serde_json::{Map, Value};
use std::collections::BTreeMap;

use geometrie::{Forme, Pt};

// =================================================================================================================
// Géométrie exacte (§4.1 ; avenant 2.2, A.8) : module public, réutilisable par le futur module de finition exacte
// =================================================================================================================

/// Formes exactes du cuivre et des trous, pose de §0.4, distances signées et inclusion (mm, degrés).
///
/// Toute forme est un **noyau** (segments, éventuellement réduits à un point, ou région polygonale avec trous)
/// épaissi d'un **rayon** (somme de Minkowski avec un disque) : disque = point + r ; capsule (piste, oblong) =
/// segment + r ; polyligne épaisse = segments + demi-largeur ; rectangle tourné = polygone + 0 ; rectangle arrondi =
/// rectangle réduit de r + r ; polygone (octogone, chanfreins) avec trous = région + 0. Les distances sont exactes,
/// sans grille ; un polygone est fermé implicitement ; l'intérieur suit la règle pair-impair (indifférente au sens de
/// parcours, donc au miroir du dessous).
pub mod geometrie {
    /// Point (x, y) en mm.
    pub type Pt = [f64; 2];

    /// Pose d'une pièce (§0.4) : origine en mm, rotation en degrés (quelconque), face.
    #[derive(Debug, Clone, Copy, PartialEq)]
    pub struct Pose {
        pub x: f64,
        pub y: f64,
        pub rotation: f64,
        pub dessous: bool,
    }

    /// (sinus, cosinus) d'un angle en degrés, exacts pour les multiples de 90°.
    pub fn sin_cos(angle_deg: f64) -> (f64, f64) {
        let m = angle_deg.rem_euclid(360.0);
        if m == 0.0 {
            (0.0, 1.0)
        } else if m == 90.0 {
            (1.0, 0.0)
        } else if m == 180.0 {
            (0.0, -1.0)
        } else if m == 270.0 {
            (-1.0, 0.0)
        } else {
            m.to_radians().sin_cos()
        }
    }

    /// Rotation d'un vecteur autour de l'origine (sens trigonométrique).
    pub fn tourner(p: Pt, angle_deg: f64) -> Pt {
        let (s, c) = sin_cos(angle_deg);
        [c * p[0] - s * p[1], s * p[0] + c * p[1]]
    }

    impl Pose {
        /// monde = origine + R(rotation) · M(face) · local, inverse de local = M · R(−rotation) · (monde − origine)
        /// (§0.4, M(dessous) = diag(1, −1)).
        pub fn vers_monde(&self, p: Pt) -> Pt {
            let ly = if self.dessous { -p[1] } else { p[1] };
            let q = tourner([p[0], ly], self.rotation);
            [self.x + q[0], self.y + q[1]]
        }

        /// local = M(face) · R(−rotation) · (monde − origine) (§0.4).
        pub fn vers_local(&self, m: Pt) -> Pt {
            let q = tourner([m[0] - self.x, m[1] - self.y], -self.rotation);
            [q[0], if self.dessous { -q[1] } else { q[1] }]
        }

        /// Rotation absolue d'une pastille de rotation relative `r` (§0.4) : pièce + r dessus, pièce − r dessous.
        pub fn rotation_absolue(&self, r: f64) -> f64 {
            if self.dessous {
                self.rotation - r
            } else {
                self.rotation + r
            }
        }

        /// Forme exprimée dans le repère local de la pièce, posée dans le repère commun.
        pub fn poser(&self, f: &Forme) -> Forme {
            f.transformee(|p| self.vers_monde(p))
        }
    }

    /// Noyau d'une forme.
    #[derive(Debug, Clone, PartialEq)]
    pub enum Noyau {
        /// Segments [a, b] (a = b : un point) : disque, capsule, polyligne.
        Segments(Vec<[Pt; 2]>),
        /// Région : contour et trous, polygones fermés implicitement.
        Region { contour: Vec<Pt>, trous: Vec<Vec<Pt>> },
    }

    /// Forme exacte : noyau épaissi de `rayon` (≥ 0).
    #[derive(Debug, Clone, PartialEq)]
    pub struct Forme {
        pub noyau: Noyau,
        pub rayon: f64,
    }

    fn sous(a: Pt, b: Pt) -> Pt {
        [a[0] - b[0], a[1] - b[1]]
    }

    fn vectoriel(a: Pt, b: Pt) -> f64 {
        a[0] * b[1] - a[1] * b[0]
    }

    /// Distance euclidienne de deux points.
    pub fn distance(a: Pt, b: Pt) -> f64 {
        (a[0] - b[0]).hypot(a[1] - b[1])
    }

    /// Distance d'un point à un segment (fermé ; segment de longueur nulle compris).
    pub fn distance_point_segment(p: Pt, a: Pt, b: Pt) -> f64 {
        let ab = sous(b, a);
        let l2 = ab[0] * ab[0] + ab[1] * ab[1];
        if l2 == 0.0 {
            return distance(p, a);
        }
        let t = (((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / l2).clamp(0.0, 1.0);
        distance(p, [a[0] + t * ab[0], a[1] + t * ab[1]])
    }

    /// Distance de deux segments fermés : 0 s'ils se coupent ou se touchent.
    pub fn distance_segments(a: Pt, b: Pt, c: Pt, d: Pt) -> f64 {
        let o1 = vectoriel(sous(b, a), sous(c, a));
        let o2 = vectoriel(sous(b, a), sous(d, a));
        let o3 = vectoriel(sous(d, c), sous(a, c));
        let o4 = vectoriel(sous(d, c), sous(b, c));
        if ((o1 > 0.0 && o2 < 0.0) || (o1 < 0.0 && o2 > 0.0)) && ((o3 > 0.0 && o4 < 0.0) || (o3 < 0.0 && o4 > 0.0)) {
            return 0.0;
        }
        distance_point_segment(a, c, d)
            .min(distance_point_segment(b, c, d))
            .min(distance_point_segment(c, a, b))
            .min(distance_point_segment(d, a, b))
    }

    /// Point dans un polygone fermé (règle pair-impair ; un point du bord peut tomber d'un côté ou de l'autre :
    /// les décisions le traitent par la distance au bord).
    pub fn dans_polygone(p: Pt, poly: &[Pt]) -> bool {
        let mut dedans = false;
        let n = poly.len();
        for i in 0..n {
            let a = poly[i];
            let b = poly[(i + 1) % n];
            if (a[1] > p[1]) != (b[1] > p[1]) {
                let x = a[0] + (p[1] - a[1]) / (b[1] - a[1]) * (b[0] - a[0]);
                if p[0] < x {
                    dedans = !dedans;
                }
            }
        }
        dedans
    }

    fn aretes_polygone(poly: &[Pt], sortie: &mut Vec<[Pt; 2]>) {
        let n = poly.len();
        for i in 0..n {
            sortie.push([poly[i], poly[(i + 1) % n]]);
        }
    }

    /// Paramètres t ∈ ]0, 1[ des points de [a, b] où il coupe [c, d] (segments non parallèles).
    fn intersection(a: Pt, b: Pt, c: Pt, d: Pt) -> Option<f64> {
        let r = sous(b, a);
        let s = sous(d, c);
        let den = vectoriel(r, s);
        if den == 0.0 {
            return None;
        }
        let ac = sous(c, a);
        let t = vectoriel(ac, s) / den;
        let u = vectoriel(ac, r) / den;
        if (0.0..=1.0).contains(&u) && t > 0.0 && t < 1.0 {
            Some(t)
        } else {
            None
        }
    }

    impl Forme {
        /// Disque de centre `c` et de rayon `r`.
        pub fn disque(c: Pt, r: f64) -> Forme {
            Forme { noyau: Noyau::Segments(vec![[c, c]]), rayon: r }
        }

        /// Capsule : segment [a, b] épaissi de `r` (piste de demi-largeur r, bouts ronds).
        pub fn capsule(a: Pt, b: Pt, r: f64) -> Forme {
            Forme { noyau: Noyau::Segments(vec![[a, b]]), rayon: r }
        }

        /// Polyligne épaisse de largeur totale `largeur` (bouts et coins ronds) ; un seul point : disque.
        pub fn polyligne(points: &[Pt], largeur: f64) -> Forme {
            let mut s = Vec::new();
            for w in points.windows(2) {
                s.push([w[0], w[1]]);
            }
            if s.is_empty() {
                if let Some(&p) = points.first() {
                    s.push([p, p]);
                }
            }
            Forme { noyau: Noyau::Segments(s), rayon: 0.5 * largeur }
        }

        /// Polygone (contour) avec trous.
        pub fn polygone(contour: Vec<Pt>, trous: Vec<Vec<Pt>>) -> Forme {
            Forme { noyau: Noyau::Region { contour, trous }, rayon: 0.0 }
        }

        /// Rectangle de centre `c`, de côtés `largeur` × `hauteur` dans son repère propre, tourné de `angle`.
        pub fn rectangle(c: Pt, largeur: f64, hauteur: f64, angle: f64) -> Forme {
            Self::rectangle_arrondi(c, largeur, hauteur, 0.0, angle)
        }

        /// Rectangle arrondi (rayon de coin borné à la moitié du petit côté) ; rayon = moitié du petit côté :
        /// oblong (capsule) ; carré à coins pleinement ronds : disque.
        pub fn rectangle_arrondi(c: Pt, largeur: f64, hauteur: f64, rayon_coin: f64, angle: f64) -> Forme {
            let rc = rayon_coin.clamp(0.0, 0.5 * largeur.min(hauteur));
            let a = 0.5 * largeur - rc;
            let b = 0.5 * hauteur - rc;
            let pt = |x: f64, y: f64| {
                let q = tourner([x, y], angle);
                [c[0] + q[0], c[1] + q[1]]
            };
            if a <= 0.0 && b <= 0.0 {
                Forme::disque(c, rc)
            } else if b <= 0.0 {
                Forme::capsule(pt(-a, 0.0), pt(a, 0.0), rc)
            } else if a <= 0.0 {
                Forme::capsule(pt(0.0, -b), pt(0.0, b), rc)
            } else {
                Forme {
                    noyau: Noyau::Region { contour: vec![pt(-a, -b), pt(a, -b), pt(a, b), pt(-a, b)], trous: vec![] },
                    rayon: rc,
                }
            }
        }

        /// Oblong (capsule) de côtés `largeur` × `hauteur`, tourné de `angle` ; largeur = hauteur : disque.
        pub fn oblong(c: Pt, largeur: f64, hauteur: f64, angle: f64) -> Forme {
            Self::rectangle_arrondi(c, largeur, hauteur, 0.5 * largeur.min(hauteur), angle)
        }

        /// Image de la forme par une isométrie `f` (pose, rotation, miroir).
        pub fn transformee(&self, f: impl Fn(Pt) -> Pt) -> Forme {
            let noyau = match &self.noyau {
                Noyau::Segments(s) => Noyau::Segments(s.iter().map(|&[a, b]| [f(a), f(b)]).collect()),
                Noyau::Region { contour, trous } => Noyau::Region {
                    contour: contour.iter().map(|&p| f(p)).collect(),
                    trous: trous.iter().map(|t| t.iter().map(|&p| f(p)).collect()).collect(),
                },
            };
            Forme { noyau, rayon: self.rayon }
        }

        fn points(&self) -> Vec<Pt> {
            match &self.noyau {
                Noyau::Segments(s) => s.iter().flat_map(|&[a, b]| [a, b]).collect(),
                Noyau::Region { contour, .. } => contour.clone(),
            }
        }

        fn aretes(&self) -> Vec<[Pt; 2]> {
            match &self.noyau {
                Noyau::Segments(s) => s.clone(),
                Noyau::Region { contour, trous } => {
                    let mut v = Vec::new();
                    aretes_polygone(contour, &mut v);
                    for t in trous {
                        aretes_polygone(t, &mut v);
                    }
                    v
                }
            }
        }

        /// Boîte [xmin, ymin, xmax, ymax] de la forme (rayon compris).
        pub fn boite(&self) -> [f64; 4] {
            let mut b = [f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY];
            for p in self.points() {
                b = [b[0].min(p[0]), b[1].min(p[1]), b[2].max(p[0]), b[3].max(p[1])];
            }
            [b[0] - self.rayon, b[1] - self.rayon, b[2] + self.rayon, b[3] + self.rayon]
        }

        /// Point dans le noyau seul (région : dans le contour et hors des trous ; segments : jamais strictement).
        fn dans_noyau(&self, p: Pt) -> bool {
            match &self.noyau {
                Noyau::Segments(_) => false,
                Noyau::Region { contour, trous } => {
                    dans_polygone(p, contour) && !trous.iter().any(|t| dans_polygone(p, t))
                }
            }
        }

        /// Distance signée du noyau (négative à l'intérieur d'une région).
        fn distance_signee_noyau(&self, p: Pt) -> f64 {
            let d = self.aretes().iter().map(|&[a, b]| distance_point_segment(p, a, b)).fold(f64::INFINITY, f64::min);
            if self.dans_noyau(p) {
                -d
            } else {
                d
            }
        }

        /// Distance signée d'un point à la forme : > 0 dehors, ≤ 0 dedans (− distance au bord, exacte hors des coins
        /// rentrants d'un polygone arrondi).
        pub fn distance_signee(&self, p: Pt) -> f64 {
            self.distance_signee_noyau(p) - self.rayon
        }

        /// Point dans la forme (bord compris).
        pub fn contient(&self, p: Pt) -> bool {
            self.distance_signee(p) <= 0.0
        }

        /// Distance des deux noyaux (0 s'ils se coupent ou si l'un contient l'autre).
        fn distance_noyaux(&self, autre: &Forme) -> f64 {
            for (x, y) in [(self, autre), (autre, self)] {
                if let Noyau::Region { .. } = y.noyau {
                    if x.points().iter().any(|&p| y.dans_noyau(p)) {
                        return 0.0;
                    }
                }
            }
            let ea = self.aretes();
            let eb = autre.aretes();
            let mut d = f64::INFINITY;
            for &[a, b] in &ea {
                for &[c, e] in &eb {
                    d = d.min(distance_segments(a, b, c, e));
                    if d == 0.0 {
                        return 0.0;
                    }
                }
            }
            d
        }

        /// Écart bord à bord de deux formes : distance des noyaux − rayons ; ≤ 0 en cas de recouvrement (alors une
        /// minoration de la pénétration, −(r₁ + r₂) au plus quand les noyaux se coupent).
        pub fn ecart(&self, autre: &Forme) -> f64 {
            self.distance_noyaux(autre) - self.rayon - autre.rayon
        }

        /// Écart d'un segment [a, b] (axe sans épaisseur) à la forme.
        pub fn ecart_segment(&self, a: Pt, b: Pt) -> f64 {
            self.ecart(&Forme::capsule(a, b, 0.0))
        }

        /// Plus grande distance d'un point `c` au cuivre de la forme (rayon circonscrit autour de `c`).
        pub fn rayon_circonscrit(&self, c: Pt) -> f64 {
            self.points().iter().map(|&p| distance(p, c)).fold(0.0, f64::max) + self.rayon
        }

        /// Vrai si le segment [a, b] pénètre l'intérieur de la forme de plus de `eps`.
        fn penetre(&self, a: Pt, b: Pt, eps: f64) -> bool {
            if self.rayon > eps {
                let d = match &self.noyau {
                    Noyau::Region { .. } => self.distance_noyaux(&Forme::capsule(a, b, 0.0)),
                    Noyau::Segments(s) => {
                        s.iter().map(|&[c, e]| distance_segments(a, b, c, e)).fold(f64::INFINITY, f64::min)
                    }
                };
                if d < self.rayon - eps {
                    return true;
                }
            }
            if let Noyau::Region { .. } = self.noyau {
                // Découpe de [a, b] aux bords de la région : chaque morceau est entièrement dedans ou dehors.
                let mut ts = vec![0.0, 1.0];
                for [c, e] in self.aretes() {
                    if let Some(t) = intersection(a, b, c, e) {
                        ts.push(t);
                    }
                }
                ts.sort_by(f64::total_cmp);
                for w in ts.windows(2) {
                    let t = 0.5 * (w[0] + w[1]);
                    let m = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
                    if self.distance_signee_noyau(m) < -eps {
                        return true;
                    }
                }
            }
            false
        }

        /// Recouvrement d'aire non nulle des deux formes, à `eps` près (un contact bord à bord n'en est pas un) :
        /// un point de l'une est à l'intérieur de l'autre de plus de `eps`, ou un bord de l'une pénètre l'autre.
        pub fn recouvre(&self, autre: &Forme, eps: f64) -> bool {
            let dedans = |x: &Forme, y: &Forme| x.points().iter().any(|&p| y.distance_signee(p) < -eps);
            if dedans(self, autre) || dedans(autre, self) {
                return true;
            }
            self.aretes().iter().any(|&[a, b]| autre.penetre_epaissi(a, b, self.rayon, eps))
                || autre.aretes().iter().any(|&[a, b]| self.penetre_epaissi(a, b, autre.rayon, eps))
        }

        /// Le segment [a, b] épaissi de `r` pénètre la forme de plus de `eps`.
        fn penetre_epaissi(&self, a: Pt, b: Pt, r: f64, eps: f64) -> bool {
            if r > 0.0 {
                return self.ecart(&Forme::capsule(a, b, r)) < -eps || self.penetre(a, b, eps);
            }
            self.penetre(a, b, eps)
        }

        /// Forme entièrement contenue dans la région `zone` (à `eps` près) : un point de la forme est dans la zone
        /// et aucun bord de la zone (contour et trous) ne pénètre la forme. Exact pour une zone polygonale ; une
        /// zone qui n'est pas une région donne `false` (approche prudente).
        pub fn contenue_dans(&self, zone: &Forme, eps: f64) -> bool {
            if !matches!(zone.noyau, Noyau::Region { .. }) || zone.rayon != 0.0 {
                return false;
            }
            let p0 = match self.points().first() {
                Some(&p) => p,
                None => return false,
            };
            if zone.distance_signee(p0) > -self.rayon + eps {
                return false;
            }
            !zone.aretes().iter().any(|&[a, b]| self.penetre(a, b, eps))
        }
    }

    /// Trou d'une pastille ou de la carte dans son repère propre, centré en `c` : `rond` (disque de diamètre
    /// `diametre`), `carre` (côté `diametre`, tourné de `rotation`) ou `oblong` (longueur totale
    /// max(`longueur`, `diametre`) selon l'axe tourné de `rotation`, largeur `diametre`).
    pub fn trou(forme: &str, diametre: f64, longueur: f64, rotation: f64, c: Pt) -> Result<Forme, String> {
        match forme {
            "rond" => Ok(Forme::disque(c, 0.5 * diametre)),
            "carre" => Ok(Forme::rectangle(c, diametre, diametre, rotation)),
            "oblong" => Ok(Forme::oblong(c, longueur.max(diametre), diametre, rotation)),
            f => Err(format!("forme de trou « {f} » inconnue (rond, carre ou oblong)")),
        }
    }

    /// Forme définie dans le repère propre d'une pastille, placée dans le repère local de la pièce : centre
    /// `centre`, rotation relative `rotation` (§0.4).
    pub fn dans_piece(f: &Forme, centre: Pt, rotation: f64) -> Forme {
        f.transformee(|p| {
            let q = tourner(p, rotation);
            [centre[0] + q[0], centre[1] + q[1]]
        })
    }
}

// =================================================================================================================
// Types publics (§5.1, figés au jalon E0)
// =================================================================================================================

/// Mode des contacts (§4.2) : `Strict` = exigence de pose ; `Altium` = recouvrement.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Strict,
    Altium,
}

/// Nature d'une liaison ouverte (§4.3) : composante qui contient au moins une pastille, ou îlot de pistes, de vias
/// ou de surfaces sans aucun membre.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NatureLiaison {
    Pastilles,
    Ilot,
}

/// Liaison ouverte (§4.3 ; `detail_liaisons_ouvertes` de §2.8).
#[derive(Debug, Clone, PartialEq)]
pub struct LiaisonOuverte {
    pub reseau: String,
    pub nature: NatureLiaison,
    /// Identifiants des pastilles de la composante (`<des>#<k>`, §2.8).
    pub pastilles: Vec<String>,
    /// Noms lisibles des pastilles (`D3.2`).
    pub noms: Vec<String>,
    pub position_mm: [f64; 2],
}

/// Violation bloquante de routage (§4.4).
#[derive(Debug, Clone, PartialEq)]
pub struct Violation {
    /// Clé de §4.4 (`isolement`, `largeur`…).
    pub genre: String,
    pub reseaux: Vec<String>,
    /// Identifiants des objets en cause (§2.8) ; ceux des objets neufs servent à la réparation ciblée (§4.6-2).
    pub objets: Vec<String>,
    pub position_mm: [f64; 2],
    /// Valeur mesurée (écart, largeur, dimension), en mm ; `None` si sans objet.
    pub ecart_mm: Option<f64>,
    /// Valeur requise par la règle, en mm ; `None` si sans objet.
    pub requis_mm: Option<f64>,
}

/// Mesures du verdict (§2.8 ; définitions de §4.7).
#[derive(Debug, Clone, PartialEq)]
pub struct Mesures {
    pub ecart_moyen_mm: f64,
    pub ecart_min_mm: Option<f64>,
    pub coudes: u64,
    pub coudes_90: u64,
    pub angles_aigus: u64,
    pub longueur_pistes_mm: f64,
    pub resistance_puissance_mohm: f64,
    pub vias: u64,
}

/// Verdict de routage (`verdict/2`, §2.8).
#[derive(Debug, Clone, PartialEq)]
pub struct Verdict {
    pub mode: Mode,
    pub valide: bool,
    pub liaisons_ouvertes: Vec<LiaisonOuverte>,
    pub violations: Vec<Violation>,
    pub derogations: BTreeMap<String, u64>,
    pub avertissements: BTreeMap<String, u64>,
    pub reseaux_a_router: u64,
    pub reseaux_complets: u64,
    pub marge_isolement_min_mm: Option<f64>,
    pub marge_isolement_min_paire: String,
    pub mesures: Mesures,
}

/// Violation du contrôle de placement (§4.5).
#[derive(Debug, Clone, PartialEq)]
pub struct ViolationPlacement {
    /// Clé de §4.5 (`absente`, `chevauchement`…).
    pub genre: String,
    pub pieces: Vec<String>,
    pub texte: String,
}

/// Information sur un éloignement (`infos.eloignements` de `controle_placement/1`).
#[derive(Debug, Clone, PartialEq)]
pub struct InfoEloignement {
    pub objet: String,
    pub reference: String,
    pub distance_mm: f64,
    pub minimum_mm: f64,
}

/// Contrôle de placement (`controle_placement/1`, §2.8).
#[derive(Debug, Clone, PartialEq)]
pub struct ControlePlacement {
    pub valide: bool,
    pub violations: Vec<ViolationPlacement>,
    pub eloignements: Vec<InfoEloignement>,
}

/// Coût commun (`cout_commun/2`, §4.7) et ses termes.
#[derive(Debug, Clone, PartialEq)]
pub struct CoutCommun {
    pub cout: f64,
    pub longueur_mm: f64,
    pub vias: u64,
    pub retreci_mm: f64,
    pub resistance_puissance_mohm: f64,
    pub coudes: u64,
    pub ecart_moyen_mm: f64,
}

// =================================================================================================================
// Constantes (§0.4, §2.8, §4.4, §4.5)
// =================================================================================================================

/// Nom et version de l'outil, écrits dans le champ `outil` des verdicts.
const OUTIL: &str = "validite 0.4-e7";

/// 1 mil en mm.
const MIL: f64 = 0.0254;
/// ε_contact (mm, §0.4).
const EPS_CONTACT: f64 = 1e-4;
/// ε_isolement (mm, §0.4).
const EPS_ISOLEMENT: f64 = 1e-4;
/// ε_largeur (mil, §0.4).
const EPS_LARGEUR_MIL: f64 = 1e-6;
/// ε_via (mm, §0.4).
const EPS_VIA: f64 = 1e-3;
/// ε_angle (degrés, §0.4).
const EPS_ANGLE: f64 = 1e-6;
/// Tolérance de position des pièces fixes, des groupes rigides et de `au_bord` (mm, §4.5).
const EPS_POSE_MM: f64 = 1e-3;
/// Tolérance de la grille de placement (mil, §4.5).
const EPS_GRILLE_MIL: f64 = 1e-3;
/// Tolérance du rétréci (mm, §4.7).
const EPS_RETRECI_MM: f64 = 1e-3;
/// Nombre maximal de détails de violations dans un verdict (§2.8).
const MAX_DETAILS: usize = 1000;
/// Nombre maximal d'exemples par type de violation (§2.8).
const MAX_EXEMPLES: usize = 15;
/// Tolérance de longueur d'une paire différentielle quand les données n'en donnent pas (mm, §4.4).
const TOLERANCE_PAIRE_DEFAUT_MM: f64 = 1.0;
/// Élargissement des boîtes et côté des cases de l'écart moyen (§4.7, mm).
const ECART_ELARGI_MM: f64 = 0.3;
const ECART_CASE_MM: f64 = 1.0;
/// Distance aux pastilles au-delà de laquelle une piste sous une pièce `sans_piste_dessous` est signalée (§4.4, mm).
const SOUS_PIECE_MM: f64 = 0.3;

/// Les 12 clés de `violations_par_type` (§4.4), dans l'ordre du contrat.
const GENRES_ROUTAGE: [&str; 12] = [
    "isolement",
    "garde_bord",
    "garde_decoupe",
    "garde_trou",
    "zone_interdite",
    "largeur",
    "dimension_via",
    "via_sous_cms",
    "trou_trou",
    "antenne",
    "couche_non_permise",
    "reseau_inconnu",
];

/// Les 14 clés de `violations_par_type` du contrôle de placement (§2.8, §4.5).
const GENRES_PLACEMENT: [&str; 14] = [
    "absente",
    "inconnue",
    "fixe_deplacee",
    "orientation",
    "grille",
    "chevauchement",
    "obstacle",
    "marge_bord",
    "marge_decoupe",
    "eloignement",
    "pastille_pastille",
    "zone_imposee",
    "au_bord",
    "groupe_rigide",
];

/// Clés de `avertissements` : les quatre de §2.8 et `zones_plan_isolees` (§4.1, §4.4).
const AVERTISSEMENTS: [&str; 5] = [
    "pistes_sous_pieces_sans_piste_dessous",
    "angles_aigus",
    "paires_desequilibrees",
    "regles_non_couvertes",
    "zones_plan_isolees",
];

/// Clés de `derogations` (§2.8, §4.4).
const DEROGATIONS: [&str; 1] = ["via_dans_pastille_meme_reseau"];

// =================================================================================================================
// Sérialisation (§2.8)
// =================================================================================================================

impl Verdict {
    /// Verdict au format `verdict/2` (§2.8), sans le champ facultatif `placement` (que le moteur ajoute : contenu
    /// de [`ControlePlacement::en_json`]). `violations_par_type` porte toujours les 12 clés de §4.4,
    /// `avertissements` ses 5 clés et `derogations` la sienne, à 0 si rien n'est compté ; `detail_violations` est
    /// tronqué à 1 000 et `exemples` à 15 textes par type.
    pub fn en_json(&self) -> Value {
        let mut par_type = compteurs_a_zero(&GENRES_ROUTAGE);
        for v in &self.violations {
            *par_type.entry(v.genre.clone()).or_insert(0) += 1;
        }
        let mut exemples: BTreeMap<String, Vec<Value>> = BTreeMap::new();
        for v in &self.violations {
            let textes = exemples.entry(v.genre.clone()).or_default();
            if textes.len() < MAX_EXEMPLES {
                textes.push(Value::String(texte_violation(v)));
            }
        }
        let mut derogations = compteurs_a_zero(&DEROGATIONS);
        for (cle, n) in &self.derogations {
            derogations.insert(cle.clone(), *n);
        }
        let mut avertissements = compteurs_a_zero(&AVERTISSEMENTS);
        for (cle, n) in &self.avertissements {
            avertissements.insert(cle.clone(), *n);
        }
        let mut o = Map::new();
        o.insert("format".into(), Value::from("verdict/2"));
        o.insert("outil".into(), Value::from(OUTIL));
        o.insert("mode".into(), Value::from(nom_mode(self.mode)));
        o.insert("valide".into(), Value::Bool(self.valide));
        o.insert("liaisons_ouvertes".into(), Value::from(self.liaisons_ouvertes.len() as u64));
        o.insert(
            "detail_liaisons_ouvertes".into(),
            Value::Array(self.liaisons_ouvertes.iter().map(liaison_json).collect()),
        );
        o.insert("violations".into(), Value::from(self.violations.len() as u64));
        o.insert("violations_par_type".into(), compteurs_json(&par_type));
        o.insert(
            "detail_violations".into(),
            Value::Array(self.violations.iter().take(MAX_DETAILS).map(violation_json).collect()),
        );
        o.insert("detail_violations_tronque".into(), Value::Bool(self.violations.len() > MAX_DETAILS));
        let mut ex = Map::new();
        for (genre, textes) in exemples {
            ex.insert(genre, Value::Array(textes));
        }
        o.insert("exemples".into(), Value::Object(ex));
        o.insert("derogations".into(), compteurs_json(&derogations));
        o.insert("avertissements".into(), compteurs_json(&avertissements));
        o.insert("reseaux_a_router".into(), Value::from(self.reseaux_a_router));
        o.insert("reseaux_complets".into(), Value::from(self.reseaux_complets));
        o.insert("marge_isolement_min_mm".into(), nombre_ou_nul_json(self.marge_isolement_min_mm));
        o.insert("marge_isolement_min_paire".into(), Value::from(self.marge_isolement_min_paire.as_str()));
        let m = &self.mesures;
        let mut mo = Map::new();
        mo.insert("ecart_moyen_mm".into(), Value::from(m.ecart_moyen_mm));
        mo.insert("ecart_min_mm".into(), nombre_ou_nul_json(m.ecart_min_mm));
        mo.insert("coudes".into(), Value::from(m.coudes));
        mo.insert("coudes_90".into(), Value::from(m.coudes_90));
        mo.insert("angles_aigus".into(), Value::from(m.angles_aigus));
        mo.insert("longueur_pistes_mm".into(), Value::from(m.longueur_pistes_mm));
        mo.insert("resistance_puissance_mohm".into(), Value::from(m.resistance_puissance_mohm));
        mo.insert("vias".into(), Value::from(m.vias));
        o.insert("mesures".into(), Value::Object(mo));
        Value::Object(o)
    }
}

impl ControlePlacement {
    /// Contrôle au format `controle_placement/1` (§2.8) ; `violations_par_type` porte toujours les 14 clés de §4.5.
    pub fn en_json(&self) -> Value {
        let mut par_type = compteurs_a_zero(&GENRES_PLACEMENT);
        for v in &self.violations {
            *par_type.entry(v.genre.clone()).or_insert(0) += 1;
        }
        let violations: Vec<Value> = self
            .violations
            .iter()
            .map(|v| {
                let mut o = Map::new();
                o.insert("genre".into(), Value::from(v.genre.as_str()));
                o.insert("pieces".into(), textes_json(&v.pieces));
                o.insert("texte".into(), Value::from(v.texte.as_str()));
                Value::Object(o)
            })
            .collect();
        let eloignements: Vec<Value> = self
            .eloignements
            .iter()
            .map(|e| {
                let mut o = Map::new();
                o.insert("objet".into(), Value::from(e.objet.as_str()));
                o.insert("reference".into(), Value::from(e.reference.as_str()));
                o.insert("distance_mm".into(), Value::from(e.distance_mm));
                o.insert("minimum_mm".into(), Value::from(e.minimum_mm));
                Value::Object(o)
            })
            .collect();
        let mut infos = Map::new();
        infos.insert("eloignements".into(), Value::Array(eloignements));
        let mut o = Map::new();
        o.insert("format".into(), Value::from("controle_placement/1"));
        o.insert("valide".into(), Value::Bool(self.valide));
        o.insert("violations".into(), Value::Array(violations));
        o.insert("violations_par_type".into(), compteurs_json(&par_type));
        o.insert("infos".into(), Value::Object(infos));
        Value::Object(o)
    }
}

fn nom_mode(mode: Mode) -> &'static str {
    match mode {
        Mode::Strict => "strict",
        Mode::Altium => "altium",
    }
}

fn nom_nature(nature: NatureLiaison) -> &'static str {
    match nature {
        NatureLiaison::Pastilles => "pastilles",
        NatureLiaison::Ilot => "ilot",
    }
}

fn compteurs_a_zero(cles: &[&str]) -> BTreeMap<String, u64> {
    cles.iter().map(|c| (c.to_string(), 0u64)).collect()
}

fn compteurs_json(c: &BTreeMap<String, u64>) -> Value {
    let mut o = Map::new();
    for (cle, n) in c {
        o.insert(cle.clone(), Value::from(*n));
    }
    Value::Object(o)
}

fn textes_json(l: &[String]) -> Value {
    Value::Array(l.iter().map(|t| Value::from(t.as_str())).collect())
}

fn point_json(p: [f64; 2]) -> Value {
    Value::Array(vec![Value::from(p[0]), Value::from(p[1])])
}

fn nombre_ou_nul_json(v: Option<f64>) -> Value {
    match v {
        Some(x) => Value::from(x),
        None => Value::Null,
    }
}

fn liaison_json(l: &LiaisonOuverte) -> Value {
    let mut o = Map::new();
    o.insert("reseau".into(), Value::from(l.reseau.as_str()));
    o.insert("nature".into(), Value::from(nom_nature(l.nature)));
    o.insert("pastilles".into(), textes_json(&l.pastilles));
    o.insert("noms".into(), textes_json(&l.noms));
    o.insert("position_mm".into(), point_json(l.position_mm));
    Value::Object(o)
}

fn violation_json(v: &Violation) -> Value {
    let mut o = Map::new();
    o.insert("genre".into(), Value::from(v.genre.as_str()));
    o.insert("reseaux".into(), textes_json(&v.reseaux));
    o.insert("objets".into(), textes_json(&v.objets));
    o.insert("position_mm".into(), point_json(v.position_mm));
    o.insert("ecart_mm".into(), nombre_ou_nul_json(v.ecart_mm));
    o.insert("requis_mm".into(), nombre_ou_nul_json(v.requis_mm));
    Value::Object(o)
}

/// Texte lisible d'une violation (champ `exemples` du verdict).
fn texte_violation(v: &Violation) -> String {
    let mut t = format!(
        "{} : {} ({}) en ({:.3} ; {:.3}) mm",
        v.genre,
        v.objets.join(", "),
        v.reseaux.join(" / "),
        v.position_mm[0],
        v.position_mm[1]
    );
    match (v.ecart_mm, v.requis_mm) {
        (Some(e), Some(r)) => t.push_str(&format!(" : mesuré {e:.4} mm, requis {r:.4} mm")),
        (Some(e), None) => t.push_str(&format!(" : mesuré {e:.4} mm")),
        (None, Some(r)) => t.push_str(&format!(" : requis {r:.4} mm")),
        (None, None) => {}
    }
    t
}

// =================================================================================================================
// Lecture des entrées : Err(texte) qui nomme le champ, jamais un chemin de fichier
// =================================================================================================================

fn joindre(chemin: &str, cle: &str) -> String {
    format!("{chemin}.{cle}")
}

fn objet<'a>(v: &'a Value, chemin: &str) -> Result<&'a Map<String, Value>, String> {
    v.as_object().ok_or_else(|| format!("{chemin} : objet attendu"))
}

fn champ<'a>(o: &'a Map<String, Value>, cle: &str, chemin: &str) -> Result<&'a Value, String> {
    o.get(cle).ok_or_else(|| format!("{} : champ manquant", joindre(chemin, cle)))
}

fn nombre(v: &Value, chemin: &str) -> Result<f64, String> {
    match v.as_f64() {
        Some(x) if x.is_finite() => Ok(x),
        _ => Err(format!("{chemin} : nombre attendu")),
    }
}

fn nombre_ou_nul(v: &Value, chemin: &str) -> Result<Option<f64>, String> {
    if v.is_null() {
        Ok(None)
    } else {
        nombre(v, chemin).map(Some)
    }
}

/// Entier JSON positif ou nul, écrit sans partie décimale (`4`, jamais `4.0` : comme `int` et non `float` en
/// Python, pour que Rust et Python acceptent les mêmes fichiers).
fn entier(v: &Value, chemin: &str) -> Result<u64, String> {
    v.as_u64().ok_or_else(|| format!("{chemin} : entier positif ou nul attendu"))
}

fn texte<'a>(v: &'a Value, chemin: &str) -> Result<&'a str, String> {
    v.as_str().ok_or_else(|| format!("{chemin} : texte attendu"))
}

fn booleen(v: &Value, chemin: &str) -> Result<bool, String> {
    v.as_bool().ok_or_else(|| format!("{chemin} : booléen attendu"))
}

fn liste<'a>(v: &'a Value, chemin: &str) -> Result<&'a Vec<Value>, String> {
    v.as_array().ok_or_else(|| format!("{chemin} : liste attendue"))
}

fn champ_nombre(o: &Map<String, Value>, cle: &str, chemin: &str) -> Result<f64, String> {
    nombre(champ(o, cle, chemin)?, &joindre(chemin, cle))
}

fn champ_nombre_ou_nul(o: &Map<String, Value>, cle: &str, chemin: &str) -> Result<Option<f64>, String> {
    nombre_ou_nul(champ(o, cle, chemin)?, &joindre(chemin, cle))
}

fn champ_texte<'a>(o: &'a Map<String, Value>, cle: &str, chemin: &str) -> Result<&'a str, String> {
    texte(champ(o, cle, chemin)?, &joindre(chemin, cle))
}

fn champ_booleen(o: &Map<String, Value>, cle: &str, chemin: &str) -> Result<bool, String> {
    booleen(champ(o, cle, chemin)?, &joindre(chemin, cle))
}

fn champ_liste<'a>(o: &'a Map<String, Value>, cle: &str, chemin: &str) -> Result<&'a Vec<Value>, String> {
    liste(champ(o, cle, chemin)?, &joindre(chemin, cle))
}

fn champ_objet<'a>(o: &'a Map<String, Value>, cle: &str, chemin: &str) -> Result<&'a Map<String, Value>, String> {
    objet(champ(o, cle, chemin)?, &joindre(chemin, cle))
}

/// Contrôle du champ `format` : version majeure inconnue ⇒ « format X/N non pris en charge » (§0.5).
fn verifier_format(o: &Map<String, Value>, attendu: &str, chemin: &str) -> Result<(), String> {
    let f = champ_texte(o, "format", chemin)?;
    if f == attendu {
        return Ok(());
    }
    let nom_attendu = attendu.split('/').next().unwrap_or(attendu);
    match f.split_once('/') {
        Some((nom, _)) if nom == nom_attendu => Err(format!("{chemin} : format {f} non pris en charge")),
        _ => Err(format!("{chemin}.format : « {f} » au lieu de « {attendu} »")),
    }
}

fn point(v: &Value, chemin: &str) -> Result<[f64; 2], String> {
    let l = liste(v, chemin)?;
    if l.len() != 2 {
        return Err(format!("{chemin} : point [x, y] attendu ({} valeurs)", l.len()));
    }
    Ok([nombre(&l[0], &format!("{chemin}[0]"))?, nombre(&l[1], &format!("{chemin}[1]"))?])
}

// -----------------------------------------------------------------------------------------------------------------
// Données (`donnees_placeur/2`, §2.5)
// -----------------------------------------------------------------------------------------------------------------

/// Pose d'une pièce dans le repère commun : mm, degrés, face.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Pose {
    x: f64,
    y: f64,
    rot: f64,
    dessous: bool,
}

#[derive(Debug, Clone)]
struct RegleVia {
    diametre_min: f64,
    diametre_max: Option<f64>,
    trou_min: f64,
    trou_max: Option<f64>,
}

#[derive(Debug, Clone)]
struct Reseau {
    nom: String,
    role: String,
    largeur_min_mm: f64,
    largeur_pref_mm: f64,
    largeur_max_mm: Option<f64>,
    via: RegleVia,
    couches_permises: Vec<String>,
    /// Classes d'isolement des objets neufs (§3.7) : piste, via, surface.
    classes: [usize; 3],
}

impl Reseau {
    /// Rôles dont les pistes entrent dans la résistance de puissance (§4.7).
    fn de_puissance(&self) -> bool {
        self.role == "puissance" || self.role == "alimentation"
    }
}

#[derive(Debug, Clone)]
struct Couche {
    nom: String,
    role: String,
}

/// Pastille (repère local de la pièce, mm) : formes du cuivre par couche (`pile`, §2.2) et trou.
#[derive(Debug, Clone)]
struct Pastille {
    id: String,
    nom: String,
    reseau: String,
    centre: Pt,
    rotation: f64,
    /// Rangs des couches portant du cuivre, pour la face de `pose_cible`.
    couches: Vec<usize>,
    principale: Forme,
    opposee: Option<Forme>,
    interne: Option<Forme>,
    traversante: bool,
    /// Trou (repère local) et métallisation.
    trou: Option<(Forme, bool)>,
    classe: usize,
    via_permis: bool,
}

/// Région d'empreinte (repère local) : conductrice pour `reseaux` (un réseau, ou ceux d'une liaison).
#[derive(Debug, Clone)]
struct RegionEmpreinte {
    id: String,
    reseaux: Vec<usize>,
    couches: Vec<usize>,
    forme: Forme,
    classe: usize,
}

/// Via d'empreinte (repère local).
#[derive(Debug, Clone)]
struct ViaEmpreinte {
    id: String,
    reseau: Option<usize>,
    centre: Pt,
    rayon: f64,
    rayon_trou: f64,
    classe: usize,
}

/// Zone de routage interdite (§4.4) : de la carte (repère commun) ou d'une empreinte (repère local).
#[derive(Debug, Clone)]
struct ZoneInterdite {
    id: String,
    couches: Vec<usize>,
    forme: Forme,
    /// Effets retenus : `pistes`, `vias`, `remplissages`.
    effets: Vec<String>,
}

/// Trou de la carte (`donnees.trous`, repère commun).
#[derive(Debug, Clone)]
struct TrouCarte {
    id: String,
    paroi: Forme,
    /// Trou sans cuivre (non métallisé, ou `diametre_cuivre` ≤ diamètre) : objet de `garde_trou`.
    sans_cuivre: bool,
    /// Cuivre de trou (`diametre_cuivre` > 0) : obstacle sans réseau.
    cuivre: Option<Forme>,
    classe: usize,
    /// Trou d'une pièce (`piece` renseigné) : traité par les pastilles de la pièce.
    de_piece: bool,
}

#[derive(Debug, Clone)]
struct Composant {
    nom: String,
    fixe: bool,
    virtuel: bool,
    pose_cible: Pose,
    orientations: Vec<(f64, bool)>,
    pastilles: Vec<Pastille>,
    regions: Vec<RegionEmpreinte>,
    vias: Vec<ViaEmpreinte>,
    lbb: [f64; 4],
    zones_interdites: Vec<ZoneInterdite>,
    decoupes: Vec<(String, Forme)>,
}

/// Cuivre fixe (repère commun) : conducteur de son réseau, obstacle sans réseau.
#[derive(Debug, Clone)]
struct CuivreFixe {
    id: String,
    reseau: Option<usize>,
    couches: Vec<usize>,
    forme: Forme,
    /// Centre, rayon et rayon du trou pour un cuivre fixe de type `via`.
    via: Option<(Pt, f64, f64)>,
    classe: usize,
}

/// Zone de plan (repère commun) : identifiant `Z:<couche>/<k>` (k = rang dans `couches[].zones_plan`).
#[derive(Debug, Clone)]
struct ZonePlan {
    id: String,
    reseau: usize,
    couche: usize,
    forme: Forme,
}

#[derive(Debug, Clone)]
struct Paire {
    nom: String,
    positif: String,
    negatif: String,
    tolerance_mm: f64,
}

#[derive(Debug, Clone)]
struct Donnees {
    couches: Vec<Couche>,
    index_couches: BTreeMap<String, usize>,
    reseaux: Vec<Reseau>,
    index_reseaux: BTreeMap<String, usize>,
    composants: Vec<Composant>,
    index_composants: BTreeMap<String, usize>,
    isolement_defaut_mm: f64,
    grille_mm: f64,
    paires: Vec<Paire>,
    regles_non_couvertes: u64,
    cuivre_fixe: Vec<CuivreFixe>,
    zones: Vec<ZonePlan>,
    /// `regles.connexion_plans.isolement` (mm, §4.2).
    connexion_isolement_mm: f64,
    /// `regles.isolement.matrice` et `classe_reference` (§3.7).
    matrice: Vec<Vec<f64>>,
    classe_reference: usize,
    contour: Vec<Pt>,
    /// Découpes de la carte (id, polygone, propriétaire).
    decoupes: Vec<(String, Forme, Option<String>)>,
    garde_bord_mm: f64,
    garde_decoupe_mm: f64,
    garde_trou_mm: f64,
    trou_trou_mm: f64,
    via_meme_reseau: bool,
    trous: Vec<TrouCarte>,
    zones_interdites: Vec<ZoneInterdite>,
    sans_piste_dessous: Vec<String>,
}

impl Donnees {
    fn reseau(&self, nom: &str) -> Option<&Reseau> {
        self.index_reseaux.get(nom).and_then(|&i| self.reseaux.get(i))
    }

    fn couche(&self, nom: &str) -> Option<&Couche> {
        self.index_couches.get(nom).and_then(|&i| self.couches.get(i))
    }
}

const ROLES_COUCHE: [&str; 3] = ["routage", "plan", "inutilisee"];
const ROLES_RESEAU: [&str; 5] = ["signal", "alimentation", "puissance", "commutation", "plan"];

fn lire_regle_via(v: &Value, chemin: &str) -> Result<RegleVia, String> {
    let o = objet(v, chemin)?;
    champ_nombre(o, "diametre", chemin)?;
    champ_nombre(o, "trou", chemin)?;
    Ok(RegleVia {
        diametre_min: champ_nombre(o, "diametre_min", chemin)?,
        diametre_max: champ_nombre_ou_nul(o, "diametre_max", chemin)?,
        trou_min: champ_nombre(o, "trou_min", chemin)?,
        trou_max: champ_nombre_ou_nul(o, "trou_max", chemin)?,
    })
}

fn lire_pose(v: &Value, chemin: &str) -> Result<Pose, String> {
    let l = liste(v, chemin)?;
    if l.len() != 4 {
        return Err(format!("{chemin} : pose [x, y, rotation, dessous] attendue ({} valeurs)", l.len()));
    }
    Ok(Pose {
        x: nombre(&l[0], &format!("{chemin}[0]"))?,
        y: nombre(&l[1], &format!("{chemin}[1]"))?,
        rot: nombre(&l[2], &format!("{chemin}[2]"))?,
        dessous: booleen(&l[3], &format!("{chemin}[3]"))?,
    })
}

fn lire_donnees(v: &Value) -> Result<Donnees, String> {
    let racine = "donnees";
    let o = objet(v, racine)?;
    verifier_format(o, "donnees_placeur/2", racine)?;
    let unite = champ_texte(o, "unite", racine)?;
    if unite != "mm" {
        return Err(format!("donnees.unite : « {unite} » au lieu de « mm »"));
    }

    let mut couches = Vec::new();
    let mut index_couches = BTreeMap::new();
    for (i, c) in champ_liste(o, "couches", racine)?.iter().enumerate() {
        let ch = format!("donnees.couches[{i}]");
        let co = objet(c, &ch)?;
        let nom = champ_texte(co, "nom", &ch)?;
        let role = champ_texte(co, "role", &ch)?;
        if !ROLES_COUCHE.contains(&role) {
            return Err(format!("{ch}.role : « {role} » inconnu (routage, plan ou inutilisee)"));
        }
        if index_couches.insert(nom.to_string(), couches.len()).is_some() {
            return Err(format!("{ch}.nom : couche « {nom} » en double"));
        }
        couches.push(Couche { nom: nom.to_string(), role: role.to_string() });
    }

    let mut reseaux = Vec::new();
    let mut index_reseaux = BTreeMap::new();
    for (i, r) in champ_liste(o, "reseaux", racine)?.iter().enumerate() {
        let ch = format!("donnees.reseaux[{i}]");
        let ro = objet(r, &ch)?;
        let nom = champ_texte(ro, "nom", &ch)?;
        let role = champ_texte(ro, "role", &ch)?;
        if !ROLES_RESEAU.contains(&role) {
            return Err(format!(
                "{ch}.role : « {role} » inconnu (signal, alimentation, puissance, commutation ou plan)"
            ));
        }
        let chl = format!("{ch}.largeurs_mm");
        let larg = champ_liste(ro, "largeurs_mm", &ch)?;
        if larg.len() != 3 {
            return Err(format!("{chl} : [min, pref, max|null] attendu ({} valeurs)", larg.len()));
        }
        let largeur_min_mm = nombre(&larg[0], &format!("{chl}[0]"))?;
        let largeur_pref_mm = nombre(&larg[1], &format!("{chl}[1]"))?;
        let largeur_max_mm = nombre_ou_nul(&larg[2], &format!("{chl}[2]"))?;
        let via = lire_regle_via(champ(ro, "via", &ch)?, &joindre(&ch, "via"))?;
        let mut couches_permises = Vec::new();
        for (k, c) in champ_liste(ro, "couches_permises", &ch)?.iter().enumerate() {
            let chc = format!("{ch}.couches_permises[{k}]");
            let nc = texte(c, &chc)?;
            if !index_couches.contains_key(nc) {
                return Err(format!("{chc} : couche « {nc} » inconnue"));
            }
            couches_permises.push(nc.to_string());
        }
        if index_reseaux.insert(nom.to_string(), reseaux.len()).is_some() {
            return Err(format!("{ch}.nom : réseau « {nom} » en double"));
        }
        let chci = joindre(&ch, "classes_isolement");
        let ci = champ_objet(ro, "classes_isolement", &ch)?;
        let classes = [classe(ci, "piste", &chci)?, classe(ci, "via", &chci)?, classe(ci, "surface", &chci)?];
        reseaux.push(Reseau {
            nom: nom.to_string(),
            role: role.to_string(),
            largeur_min_mm,
            largeur_pref_mm,
            largeur_max_mm,
            via,
            couches_permises,
            classes,
        });
    }

    let mut composants = Vec::new();
    let mut index_composants = BTreeMap::new();
    for (i, c) in champ_liste(o, "composants", racine)?.iter().enumerate() {
        let ch = format!("donnees.composants[{i}]");
        let co = objet(c, &ch)?;
        let nom = champ_texte(co, "nom", &ch)?;
        let fixe = match champ_texte(co, "statut", &ch)? {
            "fixe" => true,
            "mobile" => false,
            s => return Err(format!("{ch}.statut : « {s} » inconnu (mobile ou fixe)")),
        };
        let virtuel = champ_booleen(co, "virtuel", &ch)?;
        // Aucun alias : le nom de la version 1 est supprimé par §2.5 et n'est plus écrit (§6.1).
        let pose_cible = lire_pose(champ(co, "pose_cible", &ch)?, &joindre(&ch, "pose_cible"))?;
        let mut orientations = Vec::new();
        for (k, or) in champ_liste(co, "orientations", &ch)?.iter().enumerate() {
            let cho = format!("{ch}.orientations[{k}]");
            let l = liste(or, &cho)?;
            if l.len() != 2 {
                return Err(format!("{cho} : [rotation, dessous] attendu ({} valeurs)", l.len()));
            }
            orientations.push((nombre(&l[0], &format!("{cho}[0]"))?, booleen(&l[1], &format!("{cho}[1]"))?));
        }
        if !fixe && orientations.is_empty() {
            return Err(format!("{ch}.orientations : liste vide pour une pièce mobile"));
        }
        let mut pastilles = Vec::new();
        for (k, p) in champ_liste(co, "pastilles", &ch)?.iter().enumerate() {
            pastilles.push(lire_pastille(p, &format!("{ch}.pastilles[{k}]"), &index_reseaux, &index_couches)?);
        }
        let mut regions = Vec::new();
        for (k, r) in champ_liste(co, "regions", &ch)?.iter().enumerate() {
            let chr = format!("{ch}.regions[{k}]");
            let ro = objet(r, &chr)?;
            let mut reseaux_r = Vec::new();
            if let Some(i) = lire_reseau(ro, &chr, &index_reseaux)? {
                reseaux_r.push(i);
            }
            match champ(ro, "liaison", &chr)? {
                Value::Null => {}
                l => {
                    for (q, n) in liste(l, &joindre(&chr, "liaison"))?.iter().enumerate() {
                        let chl = format!("{chr}.liaison[{q}]");
                        let n = texte(n, &chl)?;
                        match index_reseaux.get(n) {
                            Some(&i) if !reseaux_r.contains(&i) => reseaux_r.push(i),
                            Some(_) => {}
                            None => return Err(format!("{chl} : réseau « {n} » non déclaré dans donnees.reseaux")),
                        }
                    }
                }
            }
            let contour = lire_polygone(champ(ro, "points", &chr)?, &joindre(&chr, "points"))?;
            regions.push(RegionEmpreinte {
                id: champ_texte(ro, "id", &chr)?.to_string(),
                reseaux: reseaux_r,
                couches: lire_couches(champ_liste(ro, "couches", &chr)?, &joindre(&chr, "couches"), &index_couches)?,
                forme: Forme::polygone(contour, vec![]),
                classe: classe(ro, "classe_isolement", &chr)?,
            });
        }
        let mut vias = Vec::new();
        for (k, v) in champ_liste(co, "vias", &ch)?.iter().enumerate() {
            let chv = format!("{ch}.vias[{k}]");
            let vo = objet(v, &chv)?;
            vias.push(ViaEmpreinte {
                id: champ_texte(vo, "id", &chv)?.to_string(),
                reseau: lire_reseau(vo, &chv, &index_reseaux)?,
                centre: [champ_nombre(vo, "x", &chv)?, champ_nombre(vo, "y", &chv)?],
                rayon: 0.5 * champ_positif(vo, "diametre", &chv)?,
                rayon_trou: 0.5 * champ_positif(vo, "trou", &chv)?,
                classe: classe(vo, "classe_isolement", &chv)?,
            });
        }
        let lbb = {
            let chb = joindre(&ch, "lbb");
            let l = champ_liste(co, "lbb", &ch)?;
            if l.len() != 4 {
                return Err(format!("{chb} : [x1, y1, x2, y2] attendu ({} valeurs)", l.len()));
            }
            let mut b = [0.0; 4];
            for (q, x) in l.iter().enumerate() {
                b[q] = nombre(x, &format!("{chb}[{q}]"))?;
            }
            b
        };
        let mut zones_c = Vec::new();
        for (k, z) in champ_liste(co, "zones_interdites", &ch)?.iter().enumerate() {
            zones_c.push(lire_zone_interdite(z, &format!("{ch}.zones_interdites[{k}]"), &index_couches)?);
        }
        let mut decoupes_c = Vec::new();
        for (k, z) in champ_liste(co, "decoupes", &ch)?.iter().enumerate() {
            let chd = format!("{ch}.decoupes[{k}]");
            // §2.5 : la pièce cite l'identifiant d'une découpe de `carte.decoupes` (géométrie et propriétaire y sont lus)
            if z.is_string() {
                continue;
            }
            let zo = objet(z, &chd)?;
            let id = zo.get("id").and_then(Value::as_str).map_or_else(|| format!("{nom}/D{k}"), str::to_string);
            decoupes_c.push((id, Forme::polygone(lire_polygone(champ(zo, "points", &chd)?, &joindre(&chd, "points"))?, vec![])));
        }
        if index_composants.insert(nom.to_string(), composants.len()).is_some() {
            return Err(format!("{ch}.nom : pièce « {nom} » en double"));
        }
        composants.push(Composant {
            nom: nom.to_string(),
            fixe,
            virtuel,
            pose_cible,
            orientations,
            pastilles,
            regions,
            vias,
            lbb,
            zones_interdites: zones_c,
            decoupes: decoupes_c,
        });
    }

    let mut cuivre_fixe = Vec::new();
    for (i, c) in champ_liste(o, "cuivre_fixe", racine)?.iter().enumerate() {
        let ch = format!("donnees.cuivre_fixe[{i}]");
        let co = objet(c, &ch)?;
        let couches_f = lire_couches(champ_liste(co, "couches", &ch)?, &joindre(&ch, "couches"), &index_couches)?;
        let (forme, via) = match champ_texte(co, "type", &ch)? {
            "piste" => {
                let a = [champ_nombre(co, "x1", &ch)?, champ_nombre(co, "y1", &ch)?];
                let b = [champ_nombre(co, "x2", &ch)?, champ_nombre(co, "y2", &ch)?];
                (Forme::capsule(a, b, 0.5 * champ_positif(co, "largeur", &ch)?), None)
            }
            "via" => {
                let c = [champ_nombre(co, "x", &ch)?, champ_nombre(co, "y", &ch)?];
                let r = 0.5 * champ_positif(co, "diametre", &ch)?;
                (Forme::disque(c, r), Some((c, r, 0.5 * champ_positif(co, "trou", &ch)?)))
            }
            "region" => {
                let contour = lire_polygone(champ(co, "points", &ch)?, &joindre(&ch, "points"))?;
                (Forme::polygone(contour, lire_trous(co, &ch)?), None)
            }
            t => return Err(format!("{ch}.type : « {t} » inconnu (piste, via ou region)")),
        };
        cuivre_fixe.push(CuivreFixe {
            id: champ_texte(co, "id", &ch)?.to_string(),
            reseau: lire_reseau(co, &ch, &index_reseaux)?,
            couches: couches_f,
            forme,
            via,
            classe: classe(co, "classe_isolement", &ch)?,
        });
    }

    let mut zones = Vec::new();
    for (i, c) in champ_liste(o, "couches", racine)?.iter().enumerate() {
        let ch = format!("donnees.couches[{i}]");
        let co = objet(c, &ch)?;
        let nom_couche = champ_texte(co, "nom", &ch)?;
        for (k, z) in champ_liste(co, "zones_plan", &ch)?.iter().enumerate() {
            let chz = format!("{ch}.zones_plan[{k}]");
            let zo = objet(z, &chz)?;
            let reseau = match lire_reseau(zo, &chz, &index_reseaux)? {
                Some(r) => r,
                None => return Err(format!("{chz}.reseau : réseau vide")),
            };
            let contour = lire_polygone(champ(zo, "contour", &chz)?, &joindre(&chz, "contour"))?;
            zones.push(ZonePlan {
                id: format!("Z:{nom_couche}/{k}"),
                reseau,
                couche: i,
                forme: Forme::polygone(contour, lire_trous(zo, &chz)?),
            });
        }
    }
    let connexion_isolement_mm = champ_nombre(
        champ_objet(champ_objet(o, "regles", racine)?, "connexion_plans", "donnees.regles")?,
        "isolement",
        "donnees.regles.connexion_plans",
    )?;

    let regles = champ_objet(o, "regles", racine)?;
    let isolement = champ_objet(regles, "isolement", "donnees.regles")?;
    let isolement_defaut_mm = champ_nombre(isolement, "defaut", "donnees.regles.isolement")?;
    let placement = champ_objet(regles, "placement", "donnees.regles")?;
    let grille_mm = champ_nombre(placement, "grille_mm", "donnees.regles.placement")?;
    let chm = "donnees.regles.isolement.matrice";
    let mut matrice = Vec::new();
    for (i, l) in champ_liste(isolement, "matrice", "donnees.regles.isolement")?.iter().enumerate() {
        let mut ligne = Vec::new();
        for (k, x) in liste(l, &format!("{chm}[{i}]"))?.iter().enumerate() {
            ligne.push(nombre(x, &format!("{chm}[{i}][{k}]"))?);
        }
        matrice.push(ligne);
    }
    let nb_classes = matrice.len();
    if nb_classes == 0 || matrice.iter().any(|l| l.len() != nb_classes) {
        return Err(format!("{chm} : matrice carrée non vide attendue"));
    }
    let classe_reference = classe(isolement, "classe_reference", "donnees.regles.isolement")?;
    let carte = champ_objet(o, "carte", racine)?;
    let contour = lire_polygone(champ(carte, "contour", "donnees.carte")?, "donnees.carte.contour")?;
    let mut decoupes = Vec::new();
    for (k, z) in champ_liste(carte, "decoupes", "donnees.carte")?.iter().enumerate() {
        let chd = format!("donnees.carte.decoupes[{k}]");
        let zo = objet(z, &chd)?;
        let proprietaire = match zo.get("proprietaire") {
            Some(Value::String(n)) => Some(n.clone()),
            _ => None,
        };
        let pts = lire_polygone(champ(zo, "points", &chd)?, &joindre(&chd, "points"))?;
        decoupes.push((champ_texte(zo, "id", &chd)?.to_string(), Forme::polygone(pts, vec![]), proprietaire));
    }
    let mut trous = Vec::new();
    for (i, t) in champ_liste(o, "trous", racine)?.iter().enumerate() {
        let ch = format!("donnees.trous[{i}]");
        let to = objet(t, &ch)?;
        let c = [champ_nombre(to, "x", &ch)?, champ_nombre(to, "y", &ch)?];
        let cht = joindre(&ch, "trou");
        let tr = champ_objet(to, "trou", &ch)?;
        let diametre = champ_positif(tr, "diametre", &cht)?;
        let propre = geometrie::trou(
            champ_texte(tr, "forme", &cht)?,
            diametre,
            champ_nombre(tr, "longueur", &cht)?,
            champ_nombre(tr, "rotation", &cht)?,
            point(champ(tr, "decalage", &cht)?, &joindre(&cht, "decalage"))?,
        )
        .map_err(|e| format!("{cht}.forme : {e}"))?;
        let metallise = champ_booleen(to, "metallise", &ch)?;
        let dcu = champ_nombre(to, "diametre_cuivre", &ch)?;
        trous.push(TrouCarte {
            id: champ_texte(to, "id", &ch)?.to_string(),
            paroi: geometrie::dans_piece(&propre, c, champ_nombre(to, "rotation", &ch)?),
            sans_cuivre: !metallise || dcu <= diametre,
            cuivre: if dcu > 0.0 { Some(Forme::disque(c, 0.5 * dcu)) } else { None },
            classe: classe(to, "classe_isolement", &ch)?,
            de_piece: !champ(to, "piece", &ch)?.is_null(),
        });
    }
    let mut zones_interdites = Vec::new();
    for (k, z) in champ_liste(o, "zones_routage_interdites", racine)?.iter().enumerate() {
        zones_interdites.push(lire_zone_interdite(z, &format!("donnees.zones_routage_interdites[{k}]"), &index_couches)?);
    }
    let mut sans_piste_dessous = Vec::new();
    for (k, n) in champ_liste(o, "sans_piste_dessous", racine)?.iter().enumerate() {
        sans_piste_dessous.push(texte(n, &format!("donnees.sans_piste_dessous[{k}]"))?.to_string());
    }
    // Toute classe d'isolement désigne une ligne de la matrice.
    let mut classes_lues: Vec<(usize, String)> = vec![(classe_reference, "donnees.regles.isolement.classe_reference".into())];
    for r in &reseaux {
        for c in r.classes {
            classes_lues.push((c, format!("réseau {} : classes_isolement", r.nom)));
        }
    }
    for c in &composants {
        for p in &c.pastilles {
            classes_lues.push((p.classe, format!("pastille {} : classe_isolement", p.id)));
        }
        for r in &c.regions {
            classes_lues.push((r.classe, format!("région {} : classe_isolement", r.id)));
        }
        for v in &c.vias {
            classes_lues.push((v.classe, format!("via {} : classe_isolement", v.id)));
        }
    }
    for f in &cuivre_fixe {
        classes_lues.push((f.classe, format!("cuivre fixe {} : classe_isolement", f.id)));
    }
    for t in &trous {
        classes_lues.push((t.classe, format!("trou {} : classe_isolement", t.id)));
    }
    if let Some((c, ou)) = classes_lues.iter().find(|(c, _)| *c >= nb_classes) {
        return Err(format!("{ou} : classe {c} hors de la matrice ({nb_classes} classes)"));
    }

    let mut paires = Vec::new();
    for (i, p) in champ_liste(o, "paires", racine)?.iter().enumerate() {
        let ch = format!("donnees.paires[{i}]");
        let po = objet(p, &ch)?;
        let nom = champ_texte(po, "nom", &ch)?;
        let positif = champ_texte(po, "positif", &ch)?;
        let negatif = champ_texte(po, "negatif", &ch)?;
        for (cle, r) in [("positif", positif), ("negatif", negatif)] {
            if !index_reseaux.contains_key(r) {
                return Err(format!("{ch}.{cle} : réseau « {r} » non déclaré dans donnees.reseaux"));
            }
        }
        let tolerance_mm =
            champ_nombre_ou_nul(po, "tolerance_longueur_mm", &ch)?.unwrap_or(TOLERANCE_PAIRE_DEFAUT_MM);
        paires.push(Paire {
            nom: nom.to_string(),
            positif: positif.to_string(),
            negatif: negatif.to_string(),
            tolerance_mm,
        });
    }

    // Avertissement `regles_non_couvertes` (§2.8) : nombre de règles de `entree_carte.regles.non_interpretees`.
    // `donnees_placeur/2` ne porte pas cette liste ; convention commune des lots C et E (besoin signalé au lot B) :
    // `parametres_deduction.regles_non_interpretees`, entier ≥ 0, 0 s'il est absent.
    let deduction = champ_objet(o, "parametres_deduction", racine)?;
    let regles_non_couvertes = match deduction.get("regles_non_interpretees") {
        Some(n) => entier(n, "donnees.parametres_deduction.regles_non_interpretees")?,
        None => 0,
    };

    Ok(Donnees {
        couches,
        index_couches,
        reseaux,
        index_reseaux,
        composants,
        index_composants,
        isolement_defaut_mm,
        grille_mm,
        paires,
        regles_non_couvertes,
        cuivre_fixe,
        zones,
        connexion_isolement_mm,
        matrice,
        classe_reference,
        contour,
        decoupes,
        garde_bord_mm: champ_nombre(carte, "garde_bord_mm", "donnees.carte")?,
        garde_decoupe_mm: champ_nombre(carte, "garde_decoupe_mm", "donnees.carte")?,
        garde_trou_mm: champ_nombre(regles, "garde_trou_mm", "donnees.regles")?,
        trou_trou_mm: champ_nombre(regles, "trou_trou_mm", "donnees.regles")?,
        via_meme_reseau: champ_booleen(regles, "via_dans_pastille_meme_reseau", "donnees.regles")?,
        trous,
        zones_interdites,
        sans_piste_dessous,
    })
}

/// Classe d'isolement (entier ≥ 0) du champ `cle`.
fn classe(o: &Map<String, Value>, cle: &str, chemin: &str) -> Result<usize, String> {
    Ok(entier(champ(o, cle, chemin)?, &joindre(chemin, cle))? as usize)
}

/// Zone de routage interdite (`zones_routage_interdites[]`, `composants[].zones_interdites[]`, §2.5) ; effets
/// retenus : `pistes`, `vias`, `remplissages` (les autres sont du placement).
fn lire_zone_interdite(v: &Value, ch: &str, index: &BTreeMap<String, usize>) -> Result<ZoneInterdite, String> {
    let o = objet(v, ch)?;
    let points = lire_points(champ(o, "points", ch)?, &joindre(ch, "points"))?;
    let forme = match champ_texte(o, "forme", ch)? {
        "polygone" => {
            if points.len() < 3 {
                return Err(format!("{ch}.points : polygone d'au moins 3 points attendu ({} points)", points.len()));
            }
            Forme::polygone(points, vec![])
        }
        "polyligne" => {
            if points.is_empty() {
                return Err(format!("{ch}.points : liste vide"));
            }
            Forme::polyligne(&points, champ_positif(o, "largeur", ch)?)
        }
        f => return Err(format!("{ch}.forme : « {f} » inconnue (polygone ou polyligne)")),
    };
    let mut effets = Vec::new();
    for (k, e) in champ_liste(o, "effets", ch)?.iter().enumerate() {
        effets.push(texte(e, &format!("{ch}.effets[{k}]"))?.to_string());
    }
    Ok(ZoneInterdite {
        id: champ_texte(o, "id", ch)?.to_string(),
        couches: lire_couches(champ_liste(o, "couches", ch)?, &joindre(ch, "couches"), index)?,
        forme,
        effets,
    })
}

fn champ_positif(o: &Map<String, Value>, cle: &str, chemin: &str) -> Result<f64, String> {
    let x = champ_nombre(o, cle, chemin)?;
    if x > 0.0 {
        Ok(x)
    } else {
        Err(format!("{chemin}.{cle} : nombre > 0 attendu"))
    }
}

/// Réseau d'un objet : `None` pour `""`, erreur s'il n'est pas déclaré.
fn lire_reseau(
    o: &Map<String, Value>,
    chemin: &str,
    index: &BTreeMap<String, usize>,
) -> Result<Option<usize>, String> {
    let r = champ_texte(o, "reseau", chemin)?;
    if r.is_empty() {
        return Ok(None);
    }
    match index.get(r) {
        Some(&i) => Ok(Some(i)),
        None => Err(format!("{chemin}.reseau : réseau « {r} » non déclaré dans donnees.reseaux")),
    }
}

/// Noms de couches → rangs (ordre de `donnees.couches`) ; « toutes » = toutes les couches.
fn lire_couches(l: &[Value], chemin: &str, index: &BTreeMap<String, usize>) -> Result<Vec<usize>, String> {
    let mut v = Vec::new();
    for (k, c) in l.iter().enumerate() {
        let ch = format!("{chemin}[{k}]");
        let n = texte(c, &ch)?;
        if n == "toutes" {
            v.extend(index.values().copied());
            continue;
        }
        match index.get(n) {
            Some(&i) => v.push(i),
            None => return Err(format!("{ch} : couche « {n} » inconnue")),
        }
    }
    v.sort_unstable();
    v.dedup();
    Ok(v)
}

fn lire_points(v: &Value, chemin: &str) -> Result<Vec<Pt>, String> {
    let mut pts = Vec::new();
    for (k, p) in liste(v, chemin)?.iter().enumerate() {
        pts.push(point(p, &format!("{chemin}[{k}]"))?);
    }
    Ok(pts)
}

fn lire_polygone(v: &Value, chemin: &str) -> Result<Vec<Pt>, String> {
    let pts = lire_points(v, chemin)?;
    if pts.len() < 3 {
        return Err(format!("{chemin} : polygone d'au moins 3 points attendu ({} points)", pts.len()));
    }
    Ok(pts)
}

fn lire_trous(o: &Map<String, Value>, chemin: &str) -> Result<Vec<Vec<Pt>>, String> {
    let mut trous = Vec::new();
    for (k, t) in champ_liste(o, "trous", chemin)?.iter().enumerate() {
        trous.push(lire_polygone(t, &format!("{chemin}.trous[{k}]"))?);
    }
    Ok(trous)
}

/// Forme du cuivre d'une pastille (ou d'une entrée de `pile`) dans le repère local de la pièce (§2.2) : `rect`,
/// `rond`, `oblong`, `rect_arrondi` centrés en `centre`, tournés de `rotation` (relative à la pièce) ; `polygone` :
/// `points` déjà dans le repère local.
fn lire_forme_cuivre(o: &Map<String, Value>, chemin: &str, centre: Pt) -> Result<(Forme, f64), String> {
    let forme = champ_texte(o, "forme", chemin)?;
    let rotation = champ_nombre(o, "rotation", chemin)?;
    if forme == "polygone" {
        let pts = lire_polygone(champ(o, "points", chemin)?, &joindre(chemin, "points"))?;
        return Ok((Forme::polygone(pts, vec![]), rotation));
    }
    let l = champ_positif(o, "largeur", chemin)?;
    let h = champ_positif(o, "hauteur", chemin)?;
    let f = match forme {
        "rect" => Forme::rectangle(centre, l, h, rotation),
        "rond" | "oblong" => Forme::oblong(centre, l, h, rotation),
        "rect_arrondi" => Forme::rectangle_arrondi(centre, l, h, champ_nombre(o, "rayon_coin", chemin)?, rotation),
        f => {
            return Err(format!(
                "{chemin}.forme : « {f} » inconnue (rect, rond, oblong, rect_arrondi ou polygone)"
            ))
        }
    };
    Ok((f, rotation))
}

fn lire_pastille(
    p: &Value,
    chp: &str,
    index_reseaux: &BTreeMap<String, usize>,
    index_couches: &BTreeMap<String, usize>,
) -> Result<Pastille, String> {
    let po = objet(p, chp)?;
    let id = champ_texte(po, "id", chp)?;
    let reseau = champ_texte(po, "reseau", chp)?;
    if !reseau.is_empty() && !index_reseaux.contains_key(reseau) {
        return Err(format!("{chp}.reseau : réseau « {reseau} » non déclaré dans donnees.reseaux"));
    }
    let centre = [champ_nombre(po, "lx", chp)?, champ_nombre(po, "ly", chp)?];
    let (principale, rotation) = lire_forme_cuivre(po, chp, centre)?;
    let couches = lire_couches(champ_liste(po, "couches", chp)?, &joindre(chp, "couches"), index_couches)?;
    if couches.is_empty() {
        return Err(format!("{chp}.couches : liste vide"));
    }
    let traversante = champ_booleen(po, "traversante", chp)?;
    // Pile (§2.2) : forme de la face opposée et des couches internes ; centre = centre de la pastille + `decalage`
    // (repère local de la pièce), rotation relative à la pièce (convention du lot E, LISEZMOI).
    let (mut opposee, mut interne) = (None, None);
    match champ(po, "pile", chp)? {
        Value::Null => {}
        pile => {
            let chpi = joindre(chp, "pile");
            let pio = objet(pile, &chpi)?;
            for (cle, cible) in [("opposee", &mut opposee), ("interne", &mut interne)] {
                match pio.get(cle) {
                    None | Some(Value::Null) => {}
                    Some(e) => {
                        let che = joindre(&chpi, cle);
                        let eo = objet(e, &che)?;
                        let d = point(champ(eo, "decalage", &che)?, &joindre(&che, "decalage"))?;
                        *cible = Some(lire_forme_cuivre(eo, &che, [centre[0] + d[0], centre[1] + d[1]])?.0);
                    }
                }
            }
        }
    }
    let trou = match champ(po, "trou", chp)? {
        Value::Null => None,
        t => {
            let cht = joindre(chp, "trou");
            let to = objet(t, &cht)?;
            let diametre = champ_positif(to, "diametre", &cht)?;
            let longueur = champ_nombre(to, "longueur", &cht)?;
            let rot_trou = champ_nombre(to, "rotation", &cht)?;
            let d = point(champ(to, "decalage", &cht)?, &joindre(&cht, "decalage"))?;
            let propre = geometrie::trou(champ_texte(to, "forme", &cht)?, diametre, longueur, rot_trou, d)
                .map_err(|e| format!("{cht}.forme : {e}"))?;
            Some((geometrie::dans_piece(&propre, centre, rotation), champ_booleen(to, "metallise", &cht)?))
        }
    };
    Ok(Pastille {
        id: id.to_string(),
        nom: champ_texte(po, "nom", chp)?.to_string(),
        reseau: reseau.to_string(),
        centre,
        rotation,
        couches,
        principale,
        opposee,
        interne,
        traversante,
        trou,
        classe: classe(po, "classe_isolement", chp)?,
        via_permis: champ_booleen(po, "via_dans_pastille_permis", chp)?,
    })
}

// -----------------------------------------------------------------------------------------------------------------
// Placement (`placements/2`, ou liste nue v1 ; mil)
// -----------------------------------------------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct EntreePlacement {
    designator: String,
    /// Pose convertie en mm.
    pose: Pose,
}

#[derive(Debug, Clone)]
struct Placement {
    /// Forme v1 (liste nue, sans les pièces fixes inchangées).
    v1: bool,
    pieces: Vec<EntreePlacement>,
}

fn lire_pieces(l: &[Value], chemin: &str) -> Result<Vec<EntreePlacement>, String> {
    let mut vus: BTreeMap<String, usize> = BTreeMap::new();
    let mut pieces = Vec::new();
    for (i, e) in l.iter().enumerate() {
        let ch = format!("{chemin}[{i}]");
        let eo = objet(e, &ch)?;
        let designator = champ_texte(eo, "designator", &ch)?;
        let dessous = match champ_texte(eo, "side", &ch)? {
            "top" => false,
            "bottom" => true,
            s => return Err(format!("{ch}.side : « {s} » au lieu de « top » ou « bottom »")),
        };
        let x = champ_nombre(eo, "x", &ch)?;
        let y = champ_nombre(eo, "y", &ch)?;
        let rot = champ_nombre(eo, "rotation", &ch)?;
        if vus.insert(designator.to_string(), i).is_some() {
            return Err(format!("{ch}.designator : pièce « {designator} » en double"));
        }
        pieces.push(EntreePlacement {
            designator: designator.to_string(),
            pose: Pose { x: x * MIL, y: y * MIL, rot, dessous },
        });
    }
    Ok(pieces)
}

fn lire_placement(v: &Value) -> Result<Placement, String> {
    match v {
        Value::Array(l) => Ok(Placement { v1: true, pieces: lire_pieces(l, "placements")? }),
        Value::Object(o) => {
            verifier_format(o, "placements/2", "placements")?;
            let unite = champ_texte(o, "unite", "placements")?;
            if unite != "mil" {
                return Err(format!("placements.unite : « {unite} » au lieu de « mil »"));
            }
            Ok(Placement { v1: false, pieces: lire_pieces(champ_liste(o, "pieces", "placements")?, "placements.pieces")? })
        }
        _ => Err("placements : objet placements/2 ou liste (forme v1) attendu".to_string()),
    }
}

// -----------------------------------------------------------------------------------------------------------------
// Routage (`routage/2`, ou forme v1 sans `format`) et couture (`vias_couture/2`) ; mil
// -----------------------------------------------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct Piste {
    x1: f64,
    y1: f64,
    x2: f64,
    y2: f64,
    largeur: f64,
    couche: String,
    reseau: String,
}

impl Piste {
    fn longueur_mm(&self) -> f64 {
        longueur_mm(self.x1, self.y1, self.x2, self.y2)
    }

    fn milieu_mm(&self) -> [f64; 2] {
        [(self.x1 + self.x2) * 0.5 * MIL, (self.y1 + self.y2) * 0.5 * MIL]
    }
}

#[derive(Debug, Clone)]
struct Via {
    x: f64,
    y: f64,
    diametre: f64,
    trou: f64,
    reseau: String,
}

#[derive(Debug, Clone)]
struct Surface {
    reseau: String,
    couche: String,
    contour: Vec<[f64; 2]>,
    /// Trous (mil) ; champ facultatif (forme v1).
    trous: Vec<Vec<[f64; 2]>>,
}

#[derive(Debug, Clone)]
struct Routage {
    pistes: Vec<Piste>,
    vias: Vec<Via>,
    surfaces: Vec<Surface>,
}

fn lire_piste(v: &Value, chemin: &str) -> Result<Piste, String> {
    let o = objet(v, chemin)?;
    Ok(Piste {
        x1: champ_nombre(o, "x1", chemin)?,
        y1: champ_nombre(o, "y1", chemin)?,
        x2: champ_nombre(o, "x2", chemin)?,
        y2: champ_nombre(o, "y2", chemin)?,
        largeur: champ_nombre(o, "width", chemin)?,
        couche: champ_texte(o, "layer", chemin)?.to_string(),
        reseau: champ_texte(o, "net_name", chemin)?.to_string(),
    })
}

fn lire_via(v: &Value, chemin: &str) -> Result<Via, String> {
    let o = objet(v, chemin)?;
    Ok(Via {
        x: champ_nombre(o, "x", chemin)?,
        y: champ_nombre(o, "y", chemin)?,
        diametre: champ_nombre(o, "size", chemin)?,
        trou: champ_nombre(o, "hole_size", chemin)?,
        reseau: champ_texte(o, "net", chemin)?.to_string(),
    })
}

fn lire_surface(v: &Value, chemin: &str) -> Result<Surface, String> {
    let o = objet(v, chemin)?;
    let mut contour = Vec::new();
    for (k, p) in champ_liste(o, "contour", chemin)?.iter().enumerate() {
        contour.push(point(p, &format!("{chemin}.contour[{k}]"))?);
    }
    let mut trous = Vec::new();
    if let Some(t) = o.get("trous") {
        for (k, t) in liste(t, &joindre(chemin, "trous"))?.iter().enumerate() {
            trous.push(lire_points(t, &format!("{chemin}.trous[{k}]"))?);
        }
    }
    Ok(Surface {
        reseau: champ_texte(o, "net", chemin)?.to_string(),
        couche: champ_texte(o, "layer", chemin)?.to_string(),
        contour,
        trous,
    })
}

fn lire_routage(v: &Value) -> Result<Routage, String> {
    let racine = "routage";
    let o = objet(v, racine)?;
    let v1 = !o.contains_key("format");
    if !v1 {
        verifier_format(o, "routage/2", racine)?;
    }
    match o.get("unite") {
        Some(u) => {
            let u = texte(u, "routage.unite")?;
            if u != "mil" {
                return Err(format!("routage.unite : « {u} » au lieu de « mil »"));
            }
        }
        None if !v1 => return Err("routage.unite : champ manquant".to_string()),
        None => {}
    }
    let mut pistes = Vec::new();
    for (k, p) in champ_liste(o, "pistes", racine)?.iter().enumerate() {
        pistes.push(lire_piste(p, &format!("routage.pistes[{k}]"))?);
    }
    let mut vias = Vec::new();
    for (k, p) in champ_liste(o, "vias", racine)?.iter().enumerate() {
        vias.push(lire_via(p, &format!("routage.vias[{k}]"))?);
    }
    let mut surfaces = Vec::new();
    let liste_surfaces = match o.get("surfaces") {
        Some(s) => Some(liste(s, "routage.surfaces")?),
        None if v1 => None,
        None => return Err("routage.surfaces : champ manquant".to_string()),
    };
    if let Some(l) = liste_surfaces {
        for (k, s) in l.iter().enumerate() {
            surfaces.push(lire_surface(s, &format!("routage.surfaces[{k}]"))?);
        }
    }
    Ok(Routage { pistes, vias, surfaces })
}

fn lire_couture(v: &Value) -> Result<Vec<Via>, String> {
    let racine = "couture";
    let o = objet(v, racine)?;
    verifier_format(o, "vias_couture/2", racine)?;
    let unite = champ_texte(o, "unite", racine)?;
    if unite != "mil" {
        return Err(format!("couture.unite : « {unite} » au lieu de « mil »"));
    }
    let mut vias = Vec::new();
    for (k, p) in champ_liste(o, "vias", racine)?.iter().enumerate() {
        vias.push(lire_via(p, &format!("couture.vias[{k}]"))?);
    }
    Ok(vias)
}

// =================================================================================================================
// Arithmétique commune (§0.4, §4.7) : mêmes formules pour le verdict et le coût commun
// =================================================================================================================

/// Longueur d'axe d'une piste en mm, coordonnées en mil (comme `campagnes.py` et `comparer_conjoint.py`).
fn longueur_mm(x1: f64, y1: f64, x2: f64, y2: f64) -> f64 {
    (x2 - x1).hypot(y2 - y1) * MIL
}

/// Résistance d'une piste en mΩ : 0,5 mΩ par carré (§4.7) ; 0 pour une largeur nulle (violation `largeur`).
fn resistance_mohm(longueur_mm: f64, largeur_mm: f64) -> f64 {
    if largeur_mm > 0.0 {
        0.5 * longueur_mm / largeur_mm
    } else {
        0.0
    }
}

/// Conversion d'une longueur en mil arrondie vers le haut au millième (§0.4), comme les largeurs écrites par le
/// moteur (`sortie.rs` de `9b5b5a3`, fonction `w3`).
fn mil_vers_haut(mm: f64) -> f64 {
    ((mm / MIL) * 1000.0 - 1e-6).ceil() / 1000.0
}

/// Clé d'une coordonnée pour le regroupement des extrémités (coudes, §4.7) : millième de mm le plus proche,
/// égalité au pair, calculé en double précision (`round(x_mm * 1000)` en Python).
fn cle_millieme_mm(x_mm: f64) -> i64 {
    (x_mm * 1000.0).round_ties_even() as i64
}

/// Plus petit écart entre deux angles, modulo 360 (degrés).
fn ecart_angle(a: f64, b: f64) -> f64 {
    let d = (a - b).rem_euclid(360.0);
    d.min(360.0 - d)
}

/// Coudes, coudes à 90° et angles aigus (§4.7) : jonctions d'exactement deux extrémités de pistes d'un même réseau
/// et d'une même couche, au même point arrondi au millième de mm.
fn compter_coudes(pistes: &[Piste]) -> (u64, u64, u64) {
    let mut bouts: BTreeMap<(i64, i64, &str, &str), Vec<(f64, f64)>> = BTreeMap::new();
    for p in pistes {
        let (ax, ay, bx, by) = (p.x1 * MIL, p.y1 * MIL, p.x2 * MIL, p.y2 * MIL);
        for (px, py, qx, qy) in [(ax, ay, bx, by), (bx, by, ax, ay)] {
            let cle = (cle_millieme_mm(px), cle_millieme_mm(py), p.couche.as_str(), p.reseau.as_str());
            bouts.entry(cle).or_default().push((qx - px, qy - py));
        }
    }
    let (mut coudes, mut coudes_90, mut aigus) = (0u64, 0u64, 0u64);
    for directions in bouts.values() {
        if directions.len() != 2 {
            continue;
        }
        let (a, b) = (directions[0], directions[1]);
        let (na, nb) = (a.0.hypot(a.1), b.0.hypot(b.1));
        if na == 0.0 || nb == 0.0 {
            continue;
        }
        let cos = (a.0 * b.0 + a.1 * b.1) / (na * nb);
        if cos > -0.999 {
            coudes += 1;
        }
        if cos > 0.01 {
            aigus += 1;
        } else if cos.abs() <= 0.01 {
            coudes_90 += 1;
        }
    }
    (coudes, coudes_90, aigus)
}

// =================================================================================================================
// Contrôle de placement (§4.5)
// =================================================================================================================

/// Tolérance des recouvrements et des inclusions du contrôle de placement (mm) : un contact bord à bord n'est pas
/// un recouvrement (convention 35 de `corpus/LISEZMOI.md`).
const EPS_PLACEMENT_MM: f64 = 1e-6;

/// Obstacle ou zone imposée de placement : faces (dessus, dessous) et forme (repère commun, mm).
#[derive(Debug, Clone)]
struct ZonePlacement {
    faces: [bool; 2],
    forme: Forme,
    etiquette: String,
    pieces: Vec<String>,
}

#[derive(Debug, Clone)]
struct Eloignement {
    dure: bool,
    objets: Vec<String>,
    reference: Vec<String>,
    distance_mm: f64,
}

/// Données propres au contrôle de placement (§4.5), lues dans `donnees_placeur/2`.
#[derive(Debug, Clone)]
struct DonneesPlacement {
    marge_piece_mm: f64,
    marge_traversante_mm: f64,
    marge_bord_mm: f64,
    marge_decoupe_mm: f64,
    boite: [f64; 4],
    obstacles: Vec<ZonePlacement>,
    zones_imposees: Vec<ZonePlacement>,
    eloignements: Vec<Eloignement>,
    /// (pièce, côté, débord).
    au_bord: Vec<(String, String, f64)>,
    /// (nom, pièces) de chaque unité rigide.
    groupes_rigides: Vec<(String, Vec<String>)>,
    /// (id, centre, rayon interdit, pièce propriétaire).
    trous: Vec<(String, Pt, f64, Option<String>)>,
    /// `pose_actuelle` de chaque composant (ordre des données).
    poses_actuelles: Vec<Pose>,
    /// `expansion_masque` de chaque pastille, par composant.
    expansions: Vec<Vec<f64>>,
}

fn lire_faces(v: &Value, ch: &str) -> Result<[bool; 2], String> {
    let mut f = [false; 2];
    for (k, x) in liste(v, ch)?.iter().enumerate() {
        match texte(x, &format!("{ch}[{k}]"))? {
            "dessus" => f[0] = true,
            "dessous" => f[1] = true,
            "toutes" => f = [true, true],
            s => return Err(format!("{ch}[{k}] : face « {s} » inconnue (dessus, dessous ou toutes)")),
        }
    }
    Ok(f)
}

fn forme_boite(b: [f64; 4]) -> Forme {
    Forme::polygone(vec![[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]], vec![])
}

fn lire_boite(v: &Value, ch: &str) -> Result<[f64; 4], String> {
    let l = liste(v, ch)?;
    if l.len() != 4 {
        return Err(format!("{ch} : [x0, y0, x1, y1] attendu ({} valeurs)", l.len()));
    }
    let mut b = [0.0; 4];
    for (q, x) in l.iter().enumerate() {
        b[q] = nombre(x, &format!("{ch}[{q}]"))?;
    }
    Ok([b[0].min(b[2]), b[1].min(b[3]), b[0].max(b[2]), b[1].max(b[3])])
}

/// Forme d'une zone de placement : `rect` [x0, y0, x1, y1] ou `polygone`.
fn lire_forme_zone(o: &Map<String, Value>, ch: &str) -> Result<Forme, String> {
    match (o.get("rect"), o.get("polygone")) {
        (Some(r), _) if !r.is_null() => Ok(forme_boite(lire_boite(r, &joindre(ch, "rect"))?)),
        (_, Some(p)) if !p.is_null() => Ok(Forme::polygone(lire_polygone(p, &joindre(ch, "polygone"))?, vec![])),
        _ => Err(format!("{ch} : « rect » ou « polygone » attendu")),
    }
}

fn liste_textes(v: &Value, ch: &str) -> Result<Vec<String>, String> {
    liste(v, ch)?.iter().enumerate().map(|(k, x)| texte(x, &format!("{ch}[{k}]")).map(str::to_string)).collect()
}

fn etiquette(o: &Map<String, Value>) -> String {
    o.get("etiquette").and_then(Value::as_str).unwrap_or("").to_string()
}

fn lire_donnees_placement(v: &Value, d: &Donnees) -> Result<DonneesPlacement, String> {
    let o = objet(v, "donnees")?;
    let regles = champ_objet(o, "regles", "donnees")?;
    let pl = champ_objet(regles, "placement", "donnees.regles")?;
    let carte = champ_objet(o, "carte", "donnees")?;
    let boite = lire_boite(champ(carte, "boite", "donnees.carte")?, "donnees.carte.boite")?;
    let mut obstacles = Vec::new();
    for (k, z) in champ_liste(o, "obstacles_placement", "donnees")?.iter().enumerate() {
        let ch = format!("donnees.obstacles_placement[{k}]");
        let zo = objet(z, &ch)?;
        obstacles.push(ZonePlacement {
            faces: lire_faces(champ(zo, "faces", &ch)?, &joindre(&ch, "faces"))?,
            forme: lire_forme_zone(zo, &ch)?,
            etiquette: etiquette(zo),
            pieces: Vec::new(),
        });
    }
    let mut zones_imposees = Vec::new();
    for (k, z) in champ_liste(o, "zones_imposees", "donnees")?.iter().enumerate() {
        let ch = format!("donnees.zones_imposees[{k}]");
        let zo = objet(z, &ch)?;
        zones_imposees.push(ZonePlacement {
            faces: lire_faces(champ(zo, "faces", &ch)?, &joindre(&ch, "faces"))?,
            forme: lire_forme_zone(zo, &ch)?,
            etiquette: etiquette(zo),
            pieces: liste_textes(champ(zo, "pieces", &ch)?, &joindre(&ch, "pieces"))?,
        });
    }
    let mut eloignements = Vec::new();
    for (k, e) in champ_liste(o, "eloignements", "donnees")?.iter().enumerate() {
        let ch = format!("donnees.eloignements[{k}]");
        let eo = objet(e, &ch)?;
        let dure = match champ_texte(eo, "type", &ch)? {
            "dure" => true,
            "souple" => false,
            t => return Err(format!("{ch}.type : « {t} » inconnu (dure ou souple)")),
        };
        let reference = liste_textes(champ(eo, "reference", &ch)?, &joindre(&ch, "reference"))?;
        if reference.is_empty() {
            return Err(format!("{ch}.reference : liste vide"));
        }
        eloignements.push(Eloignement {
            dure,
            objets: liste_textes(champ(eo, "objets", &ch)?, &joindre(&ch, "objets"))?,
            reference,
            distance_mm: champ_nombre(eo, "distance_mm", &ch)?,
        });
    }
    let mut au_bord = Vec::new();
    for (k, a) in champ_liste(o, "au_bord", "donnees")?.iter().enumerate() {
        let ch = format!("donnees.au_bord[{k}]");
        let ao = objet(a, &ch)?;
        let cote = champ_texte(ao, "cote", &ch)?;
        if !["gauche", "droite", "haut", "bas", "quelconque"].contains(&cote) {
            return Err(format!("{ch}.cote : « {cote} » inconnu (gauche, droite, haut, bas ou quelconque)"));
        }
        let debord = match ao.get("debord_mm") {
            None | Some(Value::Null) => 0.0,
            Some(x) => nombre(x, &joindre(&ch, "debord_mm"))?,
        };
        au_bord.push((champ_texte(ao, "piece", &ch)?.to_string(), cote.to_string(), debord));
    }
    let mut groupes_rigides = Vec::new();
    for (k, g) in champ_liste(o, "groupes", "donnees")?.iter().enumerate() {
        let ch = format!("donnees.groupes[{k}]");
        let go = objet(g, &ch)?;
        if !go.get("rigide").and_then(Value::as_bool).unwrap_or(false) {
            continue;
        }
        let nom = go.get("nom").and_then(Value::as_str).unwrap_or("").to_string();
        match go.get("pieces") {
            Some(p) if !p.is_null() => groupes_rigides.push((nom, liste_textes(p, &joindre(&ch, "pieces"))?)),
            _ => {
                for (q, ex) in champ_liste(go, "exemplaires", &ch)?.iter().enumerate() {
                    let che = format!("{ch}.exemplaires[{q}]");
                    let eo = objet(ex, &che)?;
                    let n = eo.get("nom").and_then(Value::as_str).unwrap_or("");
                    let pieces = liste_textes(champ(eo, "pieces", &che)?, &joindre(&che, "pieces"))?;
                    groupes_rigides.push((format!("{nom}/{n}"), pieces));
                }
            }
        }
    }
    let mut trous = Vec::new();
    for (i, t) in champ_liste(o, "trous", "donnees")?.iter().enumerate() {
        let ch = format!("donnees.trous[{i}]");
        let to = objet(t, &ch)?;
        let piece = match champ(to, "piece", &ch)? {
            Value::Null => None,
            p => Some(texte(p, &joindre(&ch, "piece"))?.to_string()),
        };
        trous.push((
            champ_texte(to, "id", &ch)?.to_string(),
            [champ_nombre(to, "x", &ch)?, champ_nombre(to, "y", &ch)?],
            champ_nombre(to, "rayon_interdit_mm", &ch)?,
            piece,
        ));
    }
    let mut poses_actuelles = Vec::new();
    let mut expansions = Vec::new();
    for (i, c) in champ_liste(o, "composants", "donnees")?.iter().enumerate() {
        let ch = format!("donnees.composants[{i}]");
        let co = objet(c, &ch)?;
        poses_actuelles.push(match co.get("pose_actuelle") {
            None | Some(Value::Null) => d.composants[i].pose_cible,
            Some(p) => lire_pose(p, &joindre(&ch, "pose_actuelle"))?,
        });
        let mut ex = Vec::new();
        for (k, p) in champ_liste(co, "pastilles", &ch)?.iter().enumerate() {
            let chp = format!("{ch}.pastilles[{k}]");
            ex.push(champ_nombre(objet(p, &chp)?, "expansion_masque", &chp)?);
        }
        expansions.push(ex);
    }
    Ok(DonneesPlacement {
        marge_piece_mm: champ_nombre(pl, "marge_piece_mm", "donnees.regles.placement")?,
        marge_traversante_mm: champ_nombre(pl, "marge_traversante_mm", "donnees.regles.placement")?,
        marge_bord_mm: champ_nombre(carte, "marge_bord_mm", "donnees.carte")?,
        marge_decoupe_mm: champ_nombre(carte, "marge_decoupe_mm", "donnees.carte")?,
        boite,
        obstacles,
        zones_imposees,
        eloignements,
        au_bord,
        groupes_rigides,
        trous,
        poses_actuelles,
        expansions,
    })
}

fn meme_pose(a: &Pose, b: &Pose) -> bool {
    (a.x - b.x).hypot(a.y - b.y) <= EPS_POSE_MM && ecart_angle(a.rot, b.rot) <= EPS_ANGLE && a.dessous == b.dessous
}

/// Boîte posée (repère commun) de la boîte locale `lbb` d'une pièce : boîte des quatre coins posés.
fn boite_posee(lbb: [f64; 4], pose: &Pose) -> [f64; 4] {
    let gp = geo_pose(pose);
    let mut b = [f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY];
    for p in [[lbb[0], lbb[1]], [lbb[2], lbb[1]], [lbb[2], lbb[3]], [lbb[0], lbb[3]]] {
        let q = gp.vers_monde(p);
        b = [b[0].min(q[0]), b[1].min(q[1]), b[2].max(q[0]), b[3].max(q[1])];
    }
    b
}

/// Recouvrement d'aire non nulle de deux boîtes (à `EPS_PLACEMENT_MM` près).
fn recouvrement_boites(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] - EPS_PLACEMENT_MM
        && b[0] < a[2] - EPS_PLACEMENT_MM
        && a[1] < b[3] - EPS_PLACEMENT_MM
        && b[1] < a[3] - EPS_PLACEMENT_MM
}

fn elargie(b: [f64; 4], e: f64) -> [f64; 4] {
    [b[0] - e, b[1] - e, b[2] + e, b[3] + e]
}

/// Côté `s` (0 gauche, 1 bas, 2 droite, 3 haut) de la boîte de la carte, décalé vers l'extérieur de `debord`.
fn cote_voulu(boite: [f64; 4], s: usize, debord: f64) -> f64 {
    if s < 2 {
        boite[s] - debord
    } else {
        boite[s] + debord
    }
}

const COTES: [&str; 4] = ["gauche", "bas", "droite", "haut"];

/// Contrôle de placement (§4.5) : `grille = false` pour une référence manuelle (option `--sans-grille`).
///
/// Une pièce « à sa pose d'entrée » est une pièce fixe posée à sa `pose_cible`, elle-même égale à sa
/// `pose_actuelle` (une pièce de `fixes_deplaces` a une `pose_cible` différente de sa pose du fichier) ; les
/// contrôles géométriques ne portent que sur les pièces qui ne le sont pas et sur les paires dont l'une au moins ne
/// l'est pas. Les pièces virtuelles ne comptent que par leurs pastilles (`pastille_pastille`). Ordre des violations :
/// par pièce (`absente`, `fixe_deplacee`, `orientation`, `grille`), `inconnue`, puis clé par clé dans l'ordre de
/// §4.5 (conventions 35 à 45 de `corpus/LISEZMOI.md`).
pub fn verifier_placement(donnees: &Value, placements: &Value, grille: bool) -> Result<ControlePlacement, String> {
    let d = lire_donnees(donnees)?;
    let dp = lire_donnees_placement(donnees, &d)?;
    let p = lire_placement(placements)?;
    let index: BTreeMap<&str, &EntreePlacement> = p.pieces.iter().map(|e| (e.designator.as_str(), e)).collect();
    let mut violations = Vec::new();
    let mut viol = |genre: &str, pieces: Vec<String>, texte: String| {
        violations.push(ViolationPlacement { genre: genre.to_string(), pieces, texte });
    };
    for c in &d.composants {
        if c.virtuel {
            continue; // une pièce virtuelle est toujours prise à sa pose cible (§2.7)
        }
        let e = match index.get(c.nom.as_str()) {
            Some(e) => *e,
            None => {
                // Forme v1 : les pièces fixes inchangées n'y figurent pas (comportement de `9b5b5a3`).
                if !(p.v1 && c.fixe) {
                    viol("absente", vec![c.nom.clone()], format!("{} : absente du placement", c.nom));
                }
                continue;
            }
        };
        let q = e.pose;
        if c.fixe {
            let cible = c.pose_cible;
            if !meme_pose(&q, &cible) {
                viol(
                    "fixe_deplacee",
                    vec![c.nom.clone()],
                    format!(
                        "{} : pièce fixe déplacée ({:.4} mm de sa pose cible ; rotation {} au lieu de {} ; face {} au lieu de {})",
                        c.nom,
                        (q.x - cible.x).hypot(q.y - cible.y),
                        q.rot,
                        cible.rot,
                        nom_face(q.dessous),
                        nom_face(cible.dessous)
                    ),
                );
            }
            continue;
        }
        let permise = c.orientations.iter().any(|&(r, b)| b == q.dessous && ecart_angle(q.rot, r) <= EPS_ANGLE);
        if !permise {
            viol(
                "orientation",
                vec![c.nom.clone()],
                format!("{} : orientation ({}, {}) non permise", c.nom, q.rot, nom_face(q.dessous)),
            );
        }
        if grille && d.grille_mm > 0.0 {
            let pas_mil = d.grille_mm / MIL;
            let hors = [q.x / MIL, q.y / MIL]
                .iter()
                .any(|&v| (v - pas_mil * (v / pas_mil).round()).abs() > EPS_GRILLE_MIL);
            if hors {
                viol(
                    "grille",
                    vec![c.nom.clone()],
                    format!(
                        "{} : origine ({:.3} ; {:.3}) mil hors de la grille de {} mm",
                        c.nom,
                        q.x / MIL,
                        q.y / MIL,
                        d.grille_mm
                    ),
                );
            }
        }
    }
    for e in &p.pieces {
        if !d.index_composants.contains_key(&e.designator) {
            viol("inconnue", vec![e.designator.clone()], format!("{} : pièce inconnue des données", e.designator));
        }
    }

    // --- Géométrie : poses, boîtes, pastilles posées -------------------------------------------------------------
    let n = d.couches.len();
    let nc = d.composants.len();
    let poses = poses_composants(&d, &p);
    let entree: Vec<bool> = (0..nc)
        .map(|i| {
            let c = &d.composants[i];
            c.fixe && meme_pose(&poses[i], &c.pose_cible) && meme_pose(&c.pose_cible, &dp.poses_actuelles[i])
        })
        .collect();
    let reelle = |i: usize| !d.composants[i].virtuel;
    let boites: Vec<[f64; 4]> = (0..nc).map(|i| boite_posee(d.composants[i].lbb, &poses[i])).collect();
    let face = |i: usize| usize::from(poses[i].dessous);
    let nom = |i: usize| d.composants[i].nom.clone();
    let vide = Routage { pistes: Vec::new(), vias: Vec::new(), surfaces: Vec::new() };
    // Pastilles posées, dans l'ordre des données : (composant, rang dans la pièce, objet).
    let mut pastilles: Vec<(usize, usize, Objet)> = Vec::new();
    {
        let mut objets = construire_objets(&d, &p, &vide, &[]).into_iter().filter(|o| o.genre == Genre::Pastille);
        for (i, c) in d.composants.iter().enumerate() {
            for k in 0..c.pastilles.len() {
                if let Some(o) = objets.next() {
                    pastilles.push((i, k, o));
                }
            }
        }
    }
    let m = dp.marge_piece_mm;

    // chevauchement : boîtes + marge sur une même face ; pastilles présentes sur la face par la face opposée.
    for i in 0..nc {
        for j in (i + 1)..nc {
            if !reelle(i) || !reelle(j) || (entree[i] && entree[j]) {
                continue;
            }
            if face(i) == face(j) {
                if recouvrement_boites(elargie(boites[i], m), elargie(boites[j], m)) {
                    viol(
                        "chevauchement",
                        vec![nom(i), nom(j)],
                        format!("{} / {} : boîtes qui se recouvrent", nom(i), nom(j)),
                    );
                }
                continue;
            }
            for (a, b) in [(i, j), (j, i)] {
                let couche = if face(a) == 1 { n - 1 } else { 0 };
                let touche = pastilles.iter().filter(|(c, _, _)| *c == b).any(|(_, k, o)| {
                    let e = dp.expansions[b].get(*k).copied().unwrap_or(0.0).max(0.0) + dp.marge_traversante_mm;
                    o.couches
                        .iter()
                        .any(|(kc, f)| *kc == couche && recouvrement_boites(elargie(boites[a], m), elargie(f.boite(), e)))
                });
                if touche {
                    viol(
                        "chevauchement",
                        vec![nom(a), nom(b)],
                        format!("{} / {} : pastille de la face opposée sous la boîte", nom(a), nom(b)),
                    );
                }
            }
        }
    }

    // obstacle : obstacles de placement de la face, zones interdites « placement » des empreintes, trous.
    for i in 0..nc {
        if !reelle(i) || entree[i] {
            continue;
        }
        let bi = forme_boite(boites[i]);
        for z in &dp.obstacles {
            if z.faces[face(i)] && bi.recouvre(&z.forme, EPS_PLACEMENT_MM) {
                viol("obstacle", vec![nom(i)], format!("{} : sur l'obstacle « {} »", nom(i), z.etiquette));
            }
        }
        for j in 0..nc {
            if j == i || !reelle(j) {
                continue;
            }
            let cj = &d.composants[j];
            let gp = geo_pose(&poses[j]);
            let retourne = poses[j].dessous != cj.pose_cible.dessous;
            for z in cj.zones_interdites.iter().filter(|z| z.effets.iter().any(|e| e == "placement")) {
                let couches = couches_posees(&z.couches, n, retourne);
                let mut faces = [couches.contains(&0), couches.contains(&(n - 1))];
                if !faces[0] && !faces[1] {
                    faces[face(j)] = true;
                }
                if faces[face(i)] && bi.recouvre(&gp.poser(&z.forme), EPS_PLACEMENT_MM) {
                    viol(
                        "obstacle",
                        vec![nom(i), nom(j)],
                        format!("{} : sur la zone interdite {} de {}", nom(i), z.id, nom(j)),
                    );
                }
            }
        }
        for (id, c, r, piece) in &dp.trous {
            let sien = piece.as_deref() == Some(d.composants[i].nom.as_str());
            if !sien && bi.recouvre(&Forme::disque(*c, *r), EPS_PLACEMENT_MM) {
                viol("obstacle", vec![nom(i)], format!("{} : dans le rayon interdit du trou {id}", nom(i)));
            }
        }
    }

    // marge_bord : boîte + marge au contour exact ; une pièce au_bord est dispensée du côté demandé.
    let contour = Forme::polygone(d.contour.clone(), vec![]);
    let nb = d.contour.len();
    for i in 0..nc {
        if !reelle(i) || entree[i] {
            continue;
        }
        let mut b = elargie(boites[i], m);
        let mut dispenses = [false; 4];
        for (piece, cote, debord) in &dp.au_bord {
            if *piece != d.composants[i].nom {
                continue;
            }
            for s in 0..4 {
                let colle = (boites[i][s] - cote_voulu(dp.boite, s, *debord)).abs() <= EPS_POSE_MM;
                if cote == COTES[s] || (cote == "quelconque" && colle) {
                    dispenses[s] = true;
                }
            }
        }
        for s in 0..4 {
            if dispenses[s] {
                b[s] = if s < 2 { b[s].max(dp.boite[s]) } else { b[s].min(dp.boite[s]) };
            }
        }
        let fb = forme_boite(b);
        let mut fautive = !fb.contenue_dans(&contour, EPS_PLACEMENT_MM);
        for k in 0..nb {
            let (a, c) = (d.contour[k], d.contour[(k + 1) % nb]);
            let sur_cote = |s: usize| {
                let axe = s % 2;
                dispenses[s]
                    && (a[axe] - dp.boite[s]).abs() <= EPS_PLACEMENT_MM
                    && (c[axe] - dp.boite[s]).abs() <= EPS_PLACEMENT_MM
            };
            if !(0..4).any(sur_cote) && fb.ecart_segment(a, c) < dp.marge_bord_mm - EPS_PLACEMENT_MM {
                fautive = true;
            }
        }
        if fautive {
            viol("marge_bord", vec![nom(i)], format!("{} : à moins de {} mm du contour", nom(i), dp.marge_bord_mm));
        }
    }

    // marge_decoupe : distance exacte de la boîte au polygone de chaque découpe (sauf les siennes).
    let a_decoupes = |n_: &str| d.index_composants.get(n_).is_some_and(|&k| !d.composants[k].decoupes.is_empty());
    let mut decoupes: Vec<(String, Forme, Option<String>)> =
        d.decoupes.iter().filter(|(_, _, prop)| !prop.as_deref().is_some_and(a_decoupes)).cloned().collect();
    for (j, c) in d.composants.iter().enumerate() {
        let gp = geo_pose(&poses[j]);
        for (id, f) in &c.decoupes {
            decoupes.push((id.clone(), gp.poser(f), Some(c.nom.clone())));
        }
    }
    for i in 0..nc {
        if !reelle(i) || entree[i] {
            continue;
        }
        let bi = forme_boite(boites[i]);
        for (id, f, prop) in &decoupes {
            if prop.as_deref() == Some(d.composants[i].nom.as_str()) {
                continue;
            }
            if bi.recouvre(f, EPS_PLACEMENT_MM) || bi.ecart(f) < dp.marge_decoupe_mm - EPS_PLACEMENT_MM {
                viol(
                    "marge_decoupe",
                    vec![nom(i)],
                    format!("{} : à moins de {} mm de la découpe {id}", nom(i), dp.marge_decoupe_mm),
                );
            }
        }
    }

    // eloignement : point de référence = milieu des centres des boîtes des pièces de référence.
    let centre = |b: [f64; 4]| [0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])];
    let mut infos = Vec::new();
    for (k, e) in dp.eloignements.iter().enumerate() {
        let mut somme = [0.0, 0.0];
        for r in &e.reference {
            let i = *d
                .index_composants
                .get(r)
                .ok_or_else(|| format!("donnees.eloignements[{k}].reference : pièce « {r} » inconnue"))?;
            let c = centre(boites[i]);
            somme = [somme[0] + c[0], somme[1] + c[1]];
        }
        let q = e.reference.len() as f64;
        let point_ref = [somme[0] / q, somme[1] / q];
        for ob in &e.objets {
            let (piece, pt) = if let Some((des, _)) = ob.split_once('#') {
                let o = pastilles
                    .iter()
                    .find(|(_, _, o)| o.id == *ob)
                    .ok_or_else(|| format!("donnees.eloignements[{k}].objets : pastille « {ob} » inconnue"))?;
                (des.to_string(), o.2.centre)
            } else {
                let i = *d
                    .index_composants
                    .get(ob)
                    .ok_or_else(|| format!("donnees.eloignements[{k}].objets : pièce « {ob} » inconnue"))?;
                (ob.clone(), centre(boites[i]))
            };
            let dist = geometrie::distance(pt, point_ref);
            let reference = e.reference.join("+");
            if e.dure && dist < e.distance_mm - EPS_PLACEMENT_MM {
                viol(
                    "eloignement",
                    vec![piece],
                    format!("{ob} : à {dist:.3} mm de {reference} (minimum {} mm)", e.distance_mm),
                );
            }
            infos.push(InfoEloignement { objet: ob.clone(), reference, distance_mm: dist, minimum_mm: e.distance_mm });
        }
    }

    // pastille_pastille : écart du cuivre sur une couche commune < requis de la matrice − ε_isolement.
    let requis_max = d.matrice.iter().flatten().copied().fold(0.0, f64::max);
    for (x, (ia, _, a)) in pastilles.iter().enumerate() {
        for (ib, _, b) in pastilles.iter().skip(x + 1) {
            if ia == ib || (entree[*ia] && entree[*ib]) {
                continue;
            }
            if !boites_se_recouvrent(elargir(a.boite, requis_max), b.boite) {
                continue;
            }
            let requis = d.matrice[a.classe][b.classe];
            let fautive = a
                .couches
                .iter()
                .any(|(ka, fa)| b.couches.iter().any(|(kb, fb)| ka == kb && fa.ecart(fb) < requis - EPS_ISOLEMENT));
            if fautive {
                viol(
                    "pastille_pastille",
                    vec![nom(*ia), nom(*ib)],
                    format!("{} / {} : écart inférieur à {requis} mm", a.nom, b.nom),
                );
            }
        }
    }

    // zone_imposee : boîte de chaque pièce citée dans la zone, sur une face de la zone.
    for z in &dp.zones_imposees {
        for pc in &z.pieces {
            let Some(&i) = d.index_composants.get(pc) else { continue };
            if !reelle(i) {
                continue;
            }
            if !z.faces[face(i)] || !forme_boite(boites[i]).contenue_dans(&z.forme, EPS_PLACEMENT_MM) {
                viol("zone_imposee", vec![nom(i)], format!("{} : hors de la zone imposée « {} »", nom(i), z.etiquette));
            }
        }
    }

    // au_bord : côté de la boîte sur le côté demandé de la boîte de la carte, décalé du débord.
    for (piece, cote, debord) in &dp.au_bord {
        let Some(&i) = d.index_composants.get(piece) else { continue };
        let colle = |s: usize| (boites[i][s] - cote_voulu(dp.boite, s, *debord)).abs() <= EPS_POSE_MM;
        let bon = match COTES.iter().position(|c| c == cote) {
            Some(s) => colle(s),
            None => (0..4).any(colle),
        };
        if !bon {
            viol("au_bord", vec![piece.clone()], format!("{piece} : pas contre le côté {cote} de la carte"));
        }
    }

    // groupe_rigide : poses relatives à la première pièce du groupe, comparées à celles de l'entrée.
    let relative = |r: &Pose, k: &Pose| {
        let l = geo_pose(r).vers_local([k.x, k.y]);
        let rot = if r.dessous { r.rot - k.rot } else { k.rot - r.rot };
        (l, rot, r.dessous != k.dessous)
    };
    for (g, pieces) in &dp.groupes_rigides {
        let idx: Vec<usize> = pieces.iter().filter_map(|pc| d.index_composants.get(pc).copied()).collect();
        let Some(&r) = idx.first() else { continue };
        let differe = idx.iter().skip(1).any(|&k| {
            let (l0, r0, f0) = relative(&d.composants[r].pose_cible, &d.composants[k].pose_cible);
            let (l1, r1, f1) = relative(&poses[r], &poses[k]);
            geometrie::distance(l0, l1) > EPS_POSE_MM || ecart_angle(r0, r1) > EPS_ANGLE || f0 != f1
        });
        if differe {
            viol(
                "groupe_rigide",
                idx.iter().map(|&i| nom(i)).collect(),
                format!("groupe rigide {g} : poses relatives différentes de l'entrée"),
            );
        }
    }

    Ok(ControlePlacement { valide: violations.is_empty(), violations, eloignements: infos })
}

fn nom_face(dessous: bool) -> &'static str {
    if dessous {
        "dessous"
    } else {
        "dessus"
    }
}

// =================================================================================================================
// Vérification du routage (§4.1 à §4.4) et mesures (§4.7)
// =================================================================================================================

fn violation(genre: &str, reseau: &str, objet: &str, position_mm: [f64; 2], ecart: Option<f64>, requis: Option<f64>) -> Violation {
    Violation {
        genre: genre.to_string(),
        reseaux: vec![reseau.to_string()],
        objets: vec![objet.to_string()],
        position_mm,
        ecart_mm: ecart,
        requis_mm: requis,
    }
}

/// Contrôle des dimensions d'un via (`dimension_via`, §4.4) ; rend la dimension fautive et la borne (mm).
fn dimension_via_fautive(v: &Via, regle: &RegleVia) -> Option<(f64, f64)> {
    let (dia, trou) = (v.diametre * MIL, v.trou * MIL);
    if dia < regle.diametre_min - EPS_VIA {
        return Some((dia, regle.diametre_min));
    }
    if let Some(mx) = regle.diametre_max {
        if dia > mx + EPS_VIA {
            return Some((dia, mx));
        }
    }
    if trou < regle.trou_min - EPS_VIA {
        return Some((trou, regle.trou_min));
    }
    if let Some(mx) = regle.trou_max {
        if trou > mx + EPS_VIA {
            return Some((trou, mx));
        }
    }
    None
}

/// Contrôles des objets neufs qui ne demandent aucune géométrie : `reseau_inconnu`, `couche_non_permise`,
/// `largeur`, `dimension_via`.
fn controler_sans_geometrie(d: &Donnees, r: &Routage, couture: &[Via], violations: &mut Vec<Violation>) {
    let couche_de_routage = |nom: &str| d.couche(nom).is_some_and(|c| c.role == "routage");
    for (k, p) in r.pistes.iter().enumerate() {
        let id = format!("P{k}");
        let pos = p.milieu_mm();
        let reseau = d.reseau(&p.reseau);
        if reseau.is_none() {
            violations.push(violation("reseau_inconnu", &p.reseau, &id, pos, None, None));
        }
        let permise = couche_de_routage(&p.couche)
            && reseau.map_or(true, |rs| rs.couches_permises.iter().any(|c| *c == p.couche));
        if !permise {
            violations.push(violation("couche_non_permise", &p.reseau, &id, pos, None, None));
        }
        if let Some(rs) = reseau {
            let largeur_mm = p.largeur * MIL;
            if p.largeur < mil_vers_haut(rs.largeur_min_mm) - EPS_LARGEUR_MIL {
                violations.push(violation("largeur", &p.reseau, &id, pos, Some(largeur_mm), Some(rs.largeur_min_mm)));
            } else if let Some(mx) = rs.largeur_max_mm {
                if p.largeur > mil_vers_haut(mx) + EPS_LARGEUR_MIL {
                    violations.push(violation("largeur", &p.reseau, &id, pos, Some(largeur_mm), Some(mx)));
                }
            }
        }
    }
    let vias = r.vias.iter().enumerate().map(|(k, v)| (format!("V{k}"), v));
    let vias_couture = couture.iter().enumerate().map(|(k, v)| (format!("C{k}"), v));
    for (id, v) in vias.chain(vias_couture) {
        let pos = [v.x * MIL, v.y * MIL];
        match d.reseau(&v.reseau) {
            None => violations.push(violation("reseau_inconnu", &v.reseau, &id, pos, None, None)),
            Some(rs) => {
                if let Some((mesure, requis)) = dimension_via_fautive(v, &rs.via) {
                    violations.push(violation("dimension_via", &v.reseau, &id, pos, Some(mesure), Some(requis)));
                }
            }
        }
    }
    for (k, s) in r.surfaces.iter().enumerate() {
        let id = format!("S{k}");
        let pos = s.contour.first().map_or([0.0, 0.0], |q| [q[0] * MIL, q[1] * MIL]);
        let reseau = d.reseau(&s.reseau);
        if reseau.is_none() {
            violations.push(violation("reseau_inconnu", &s.reseau, &id, pos, None, None));
        }
        let permise = couche_de_routage(&s.couche)
            && reseau.map_or(true, |rs| rs.couches_permises.iter().any(|c| *c == s.couche));
        if !permise {
            violations.push(violation("couche_non_permise", &s.reseau, &id, pos, None, None));
        }
    }
}

/// Paires différentielles déséquilibrées (avertissement, §4.4) : écart de longueur des deux réseaux >
/// `tolerance_longueur_mm`, ou nombres de vias différents (vias du routage).
fn compter_paires_desequilibrees(d: &Donnees, r: &Routage) -> u64 {
    let mut longueurs: BTreeMap<&str, f64> = BTreeMap::new();
    for p in &r.pistes {
        *longueurs.entry(p.reseau.as_str()).or_insert(0.0) += p.longueur_mm();
    }
    let mut vias: BTreeMap<&str, u64> = BTreeMap::new();
    for v in &r.vias {
        *vias.entry(v.reseau.as_str()).or_insert(0) += 1;
    }
    let lire = |n: &str| (longueurs.get(n).copied().unwrap_or(0.0), vias.get(n).copied().unwrap_or(0));
    d.paires
        .iter()
        .filter(|p| {
            let (lp, vp) = lire(&p.positif);
            let (ln, vn) = lire(&p.negatif);
            (lp - ln).abs() > p.tolerance_mm || vp != vn
        })
        .count() as u64
}

// =================================================================================================================
// Objets (§4.1) et connectivité (§4.2, §4.3)
// =================================================================================================================

/// Genre d'un objet de §4.1 ; l'ordre des objets (décision 8 du plan du lot E) suit cet ordre.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Genre {
    Pastille,
    Region,
    ViaEmpreinte,
    CuivreFixe,
    /// Cuivre d'un trou de la carte (`diametre_cuivre` > 0) : obstacle sans réseau.
    TrouCuivre,
    Zone,
    Piste,
    Via,
    Surface,
    Couture,
}

impl Genre {
    /// Conducteur neuf (§4.1) : piste, via, surface, via de couture.
    fn neuf(self) -> bool {
        matches!(self, Genre::Piste | Genre::Via | Genre::Surface | Genre::Couture)
    }
}

/// Objet posé dans le repère commun (mm), avec son cuivre par couche.
#[derive(Debug, Clone)]
struct Objet {
    /// Identifiant de §2.8.
    id: String,
    genre: Genre,
    /// Réseaux dont l'objet est conducteur (vide : sans réseau ; plusieurs : région de liaison).
    reseaux: Vec<usize>,
    /// (rang de couche, cuivre sur cette couche), rangs croissants.
    couches: Vec<(usize, Forme)>,
    /// Centre (pastille, via) ; milieu (piste) ; centre de la boîte (région, surface, zone, cuivre fixe).
    centre: Pt,
    /// Rayon du cuivre d'un via (via, via de couture, via d'empreinte, cuivre fixe `via`), 0 sinon.
    rayon: f64,
    est_via: bool,
    /// Pastille traversante métallisée.
    traversante: bool,
    /// Piste : extrémités d'axe et demi-largeur.
    axe: Option<(Pt, Pt, f64)>,
    /// Pastille : nom lisible « <des>.<broche> ».
    nom: String,
    boite: [f64; 4],
    /// Classe d'isolement (§3.7).
    classe: usize,
    /// Pastille CMS (sans trou) dont `via_dans_pastille_permis` est faux.
    cms_sans_via: bool,
}

fn union_boites(couches: &[(usize, Forme)]) -> [f64; 4] {
    let mut b = [f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY];
    for (_, f) in couches {
        let c = f.boite();
        b = [b[0].min(c[0]), b[1].min(c[1]), b[2].max(c[2]), b[3].max(c[3])];
    }
    b
}

fn centre_boite(b: [f64; 4]) -> Pt {
    [0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])]
}

fn nouvel_objet(id: String, genre: Genre, reseaux: Vec<usize>, couches: Vec<(usize, Forme)>) -> Objet {
    let boite = union_boites(&couches);
    Objet {
        id,
        genre,
        reseaux,
        couches,
        centre: centre_boite(boite),
        rayon: 0.0,
        est_via: false,
        traversante: false,
        axe: None,
        nom: String::new(),
        boite,
        classe: 0,
        cms_sans_via: false,
    }
}

fn objet_via(id: String, genre: Genre, reseaux: Vec<usize>, centre: Pt, rayon: f64, couches: &[usize]) -> Objet {
    let f = Forme::disque(centre, rayon);
    let mut o = nouvel_objet(id, genre, reseaux, couches.iter().map(|&c| (c, f.clone())).collect());
    o.centre = centre;
    o.rayon = rayon;
    o.est_via = true;
    o
}

fn geo_pose(p: &Pose) -> geometrie::Pose {
    geometrie::Pose { x: p.x, y: p.y, rotation: p.rot, dessous: p.dessous }
}

/// Objets de §4.1 dans l'ordre de la décision 8 : pastilles (ordre des données, pièces virtuelles comprises),
/// régions, vias d'empreinte, cuivre fixe, zones de plan, `P<k>`, `V<k>`, `S<k>`, `C<k>`. Une pièce absente du
/// placement (pièce virtuelle, forme v1) reste à `pose_cible` ; une pièce posée sur l'autre face que `pose_cible`
/// voit ses couches retournées (rang k → N − 1 − k).
fn construire_objets(d: &Donnees, placement: &Placement, r: &Routage, couture: &[Via]) -> Vec<Objet> {
    let n = d.couches.len();
    let toutes: Vec<usize> = (0..n).collect();
    let mut poses: BTreeMap<&str, Pose> = BTreeMap::new();
    for e in &placement.pieces {
        poses.insert(e.designator.as_str(), e.pose);
    }
    let pose_de = |c: &Composant| poses.get(c.nom.as_str()).copied().unwrap_or(c.pose_cible);
    let couche_posee = |k: usize, retourne: bool| if retourne { n - 1 - k } else { k };
    let mut objets = Vec::new();

    for c in &d.composants {
        let pose = pose_de(c);
        let gp = geo_pose(&pose);
        let retourne = pose.dessous != c.pose_cible.dessous;
        let face = if pose.dessous { n - 1 } else { 0 };
        for p in &c.pastilles {
            let mut couches = Vec::new();
            for &k in &p.couches {
                let kp = couche_posee(k, retourne);
                let f = if kp == face || n == 1 {
                    &p.principale
                } else if kp == 0 || kp == n - 1 {
                    p.opposee.as_ref().unwrap_or(&p.principale)
                } else {
                    p.interne.as_ref().unwrap_or(&p.principale)
                };
                couches.push((kp, gp.poser(f)));
            }
            couches.sort_by_key(|x| x.0);
            let reseaux = d.index_reseaux.get(&p.reseau).map(|&i| vec![i]).unwrap_or_default();
            let mut o = nouvel_objet(p.id.clone(), Genre::Pastille, reseaux, couches);
            o.centre = gp.vers_monde(p.centre);
            o.traversante = p.traversante && p.trou.as_ref().is_some_and(|t| t.1);
            o.nom = format!("{}.{}", c.nom, p.nom);
            o.classe = p.classe;
            o.cms_sans_via = p.trou.is_none() && !p.traversante && !p.via_permis;
            objets.push(o);
        }
    }
    for c in &d.composants {
        let pose = pose_de(c);
        let gp = geo_pose(&pose);
        let retourne = pose.dessous != c.pose_cible.dessous;
        for rg in &c.regions {
            let f = gp.poser(&rg.forme);
            let mut couches: Vec<(usize, Forme)> =
                rg.couches.iter().map(|&k| (couche_posee(k, retourne), f.clone())).collect();
            couches.sort_by_key(|x| x.0);
            let mut o = nouvel_objet(rg.id.clone(), Genre::Region, rg.reseaux.clone(), couches);
            o.classe = rg.classe;
            objets.push(o);
        }
    }
    for c in &d.composants {
        let gp = geo_pose(&pose_de(c));
        for v in &c.vias {
            let reseaux = v.reseau.map(|i| vec![i]).unwrap_or_default();
            let mut o = objet_via(v.id.clone(), Genre::ViaEmpreinte, reseaux, gp.vers_monde(v.centre), v.rayon, &toutes);
            o.classe = v.classe;
            objets.push(o);
        }
    }
    for f in &d.cuivre_fixe {
        let reseaux = f.reseau.map(|i| vec![i]).unwrap_or_default();
        let mut o = match f.via {
            Some((c, r, _)) => objet_via(f.id.clone(), Genre::CuivreFixe, reseaux, c, r, &f.couches),
            None => nouvel_objet(
                f.id.clone(),
                Genre::CuivreFixe,
                reseaux,
                f.couches.iter().map(|&k| (k, f.forme.clone())).collect(),
            ),
        };
        o.classe = f.classe;
        objets.push(o);
    }
    for t in &d.trous {
        if let (Some(cu), false) = (&t.cuivre, t.de_piece) {
            let mut o = nouvel_objet(t.id.clone(), Genre::TrouCuivre, vec![], toutes.iter().map(|&k| (k, cu.clone())).collect());
            o.classe = t.classe;
            objets.push(o);
        }
    }
    for z in &d.zones {
        objets.push(nouvel_objet(z.id.clone(), Genre::Zone, vec![z.reseau], vec![(z.couche, z.forme.clone())]));
    }
    for (k, p) in r.pistes.iter().enumerate() {
        let a = [p.x1 * MIL, p.y1 * MIL];
        let b = [p.x2 * MIL, p.y2 * MIL];
        let hw = 0.5 * p.largeur * MIL;
        let couches = d.index_couches.get(&p.couche).map(|&c| vec![(c, Forme::capsule(a, b, hw))]).unwrap_or_default();
        let reseaux = d.index_reseaux.get(&p.reseau).map(|&i| vec![i]).unwrap_or_default();
        let mut o = nouvel_objet(format!("P{k}"), Genre::Piste, reseaux, couches);
        o.centre = [0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1])];
        o.axe = Some((a, b, hw));
        o.classe = classe_neuve(d, &p.reseau, 0);
        objets.push(o);
    }
    let vias_neufs = |liste: &[Via], prefixe: &str, genre: Genre, objets: &mut Vec<Objet>| {
        for (k, v) in liste.iter().enumerate() {
            let reseaux = d.index_reseaux.get(&v.reseau).map(|&i| vec![i]).unwrap_or_default();
            let c = [v.x * MIL, v.y * MIL];
            let mut o = objet_via(format!("{prefixe}{k}"), genre, reseaux, c, 0.5 * v.diametre * MIL, &toutes);
            o.classe = classe_neuve(d, &v.reseau, 1);
            objets.push(o);
        }
    };
    vias_neufs(&r.vias, "V", Genre::Via, &mut objets);
    for (k, s) in r.surfaces.iter().enumerate() {
        let mm = |l: &[[f64; 2]]| l.iter().map(|p| [p[0] * MIL, p[1] * MIL]).collect::<Vec<Pt>>();
        let f = Forme::polygone(mm(&s.contour), s.trous.iter().map(|t| mm(t)).collect());
        let couches = match d.index_couches.get(&s.couche) {
            Some(&c) if s.contour.len() >= 3 => vec![(c, f)],
            _ => vec![],
        };
        let reseaux = d.index_reseaux.get(&s.reseau).map(|&i| vec![i]).unwrap_or_default();
        let mut o = nouvel_objet(format!("S{k}"), Genre::Surface, reseaux, couches);
        o.classe = classe_neuve(d, &s.reseau, 2);
        objets.push(o);
    }
    vias_neufs(couture, "C", Genre::Couture, &mut objets);
    objets
}

/// Classe d'un objet neuf (`quoi` : 0 piste, 1 via, 2 surface) ; réseau inconnu : classe de référence.
fn classe_neuve(d: &Donnees, reseau: &str, quoi: usize) -> usize {
    d.reseau(reseau).map_or(d.classe_reference, |r| r.classes[quoi])
}

fn recouvrement(fa: &Forme, fb: &Forme) -> bool {
    fa.ecart(fb) <= EPS_CONTACT
}

/// Contact de deux objets du même réseau sur une couche commune (§4.2, table ligne par ligne).
fn contact_couche(a: &Objet, fa: &Forme, b: &Objet, fb: &Forme, mode: Mode, iso_cx: f64) -> bool {
    // Ligne 6 : surface – tout objet.
    if a.genre == Genre::Surface || b.genre == Genre::Surface {
        return recouvrement(fa, fb);
    }
    // Lignes 8 et 9 : zone de plan.
    if a.genre == Genre::Zone || b.genre == Genre::Zone {
        let (fz, o, fo) = if a.genre == Genre::Zone { (fa, b, fb) } else { (fb, a, fa) };
        if o.genre == Genre::Piste {
            return false; // §4.2 ne définit aucun contact piste – zone (décision 10).
        }
        if o.est_via || (o.genre == Genre::Pastille && o.traversante) {
            let sd = fz.distance_signee(o.centre);
            return match mode {
                Mode::Strict => {
                    // Rayon de l'objet : via ; pastille : rayon circonscrit de son cuivre sur la couche (décision 9).
                    let r = if o.est_via { o.rayon } else { fo.rayon_circonscrit(o.centre) };
                    sd <= -(r + iso_cx) + EPS_CONTACT
                }
                Mode::Altium => sd <= EPS_CONTACT,
            };
        }
        if o.genre == Genre::Pastille {
            return match mode {
                Mode::Strict => fo.contenue_dans(fz, EPS_CONTACT),
                Mode::Altium => recouvrement(fz, fo),
            };
        }
        return recouvrement(fa, fb); // zone, région, cuivre fixe : objets préexistants entre eux.
    }
    // Ligne 7 : objets préexistants entre eux.
    if !a.genre.neuf() && !b.genre.neuf() {
        return recouvrement(fa, fb);
    }
    if mode == Mode::Altium {
        return recouvrement(fa, fb);
    }
    // Strict, lignes 1 à 5 : `n` est la piste s'il y en a une, sinon un via neuf.
    let (n, o, fo) = if a.genre == Genre::Piste {
        (a, b, fb)
    } else if b.genre == Genre::Piste || !a.genre.neuf() {
        (b, a, fa)
    } else {
        (a, b, fb)
    };
    let neuf_via = |x: &Objet| matches!(x.genre, Genre::Via | Genre::Couture);
    match n.axe {
        Some((p1, p2, hw)) => match o.axe {
            Some((q1, q2, hwo)) => {
                geometrie::distance_point_segment(p1, q1, q2) <= hwo + EPS_CONTACT
                    || geometrie::distance_point_segment(p2, q1, q2) <= hwo + EPS_CONTACT
                    || geometrie::distance_point_segment(q1, p1, p2) <= hw + EPS_CONTACT
                    || geometrie::distance_point_segment(q2, p1, p2) <= hw + EPS_CONTACT
            }
            None if neuf_via(o) => {
                geometrie::distance(p1, o.centre) <= o.rayon + EPS_CONTACT
                    || geometrie::distance(p2, o.centre) <= o.rayon + EPS_CONTACT
            }
            None => fo.distance_signee(p1) <= EPS_CONTACT || fo.distance_signee(p2) <= EPS_CONTACT,
        },
        None if neuf_via(o) => geometrie::distance(n.centre, o.centre) <= n.rayon.max(o.rayon) + EPS_CONTACT,
        None => fo.distance_signee(n.centre) <= EPS_CONTACT,
    }
}

fn contact(a: &Objet, b: &Objet, mode: Mode, iso_cx: f64) -> bool {
    for (ca, fa) in &a.couches {
        for (cb, fb) in &b.couches {
            if ca == cb && contact_couche(a, fa, b, fb, mode, iso_cx) {
                return true;
            }
        }
    }
    false
}

/// Côté des cases de la grille d'accélération (mm). La grille ne sert qu'à proposer des paires ; chaque décision
/// est prise par `contact`, exacte.
const CASE_GRILLE_MM: f64 = 2.0;

/// Paires candidates (i < j, indices dans `membres`) dont les boîtes, élargies de `marge`, partagent une case ;
/// triées et sans doublon.
fn paires_candidates(objets: &[Objet], membres: &[usize], marge: f64) -> Vec<(usize, usize)> {
    let mut cases: BTreeMap<(i64, i64), Vec<usize>> = BTreeMap::new();
    for (i, &k) in membres.iter().enumerate() {
        let b = objets[k].boite;
        if !(b[0].is_finite() && b[1].is_finite() && b[2].is_finite() && b[3].is_finite()) {
            continue;
        }
        let c = |v: f64| (v / CASE_GRILLE_MM).floor() as i64;
        for x in c(b[0] - marge)..=c(b[2] + marge) {
            for y in c(b[1] - marge)..=c(b[3] + marge) {
                cases.entry((x, y)).or_default().push(i);
            }
        }
    }
    let mut paires = Vec::new();
    for l in cases.values() {
        for (p, &i) in l.iter().enumerate() {
            for &j in &l[p + 1..] {
                paires.push((i.min(j), i.max(j)));
            }
        }
    }
    paires.sort_unstable();
    paires.dedup();
    paires
}

fn racine(parent: &mut [usize], mut i: usize) -> usize {
    while parent[i] != i {
        parent[i] = parent[parent[i]];
        i = parent[i];
    }
    i
}

/// Résultat de la connectivité (§4.3).
struct Connectivite {
    liaisons: Vec<LiaisonOuverte>,
    reseaux_a_router: u64,
    reseaux_complets: u64,
    zones_plan_isolees: u64,
}

/// Union-find par réseau avec les contacts du mode (§4.3). Membres = pastilles et zones touchées ; composantes =
/// classes contenant un membre, une piste, un via (de couture compris) ou une surface ; réseau compté s'il a au
/// moins deux membres ; référence = plus de zones, puis plus d'objets, puis plus petit identifiant (ordre des
/// objets) ; résultats dans l'ordre de `donnees.reseaux` puis des composantes.
fn connectivite(d: &Donnees, objets: &[Objet], mode: Mode) -> Connectivite {
    let mut res = Connectivite { liaisons: Vec::new(), reseaux_a_router: 0, reseaux_complets: 0, zones_plan_isolees: 0 };
    for (ir, reseau) in d.reseaux.iter().enumerate() {
        let membres: Vec<usize> = (0..objets.len()).filter(|&k| objets[k].reseaux.contains(&ir)).collect();
        if membres.is_empty() {
            continue;
        }
        let mut parent: Vec<usize> = (0..membres.len()).collect();
        let mut touche = vec![false; membres.len()];
        for (i, j) in paires_candidates(objets, &membres, EPS_CONTACT) {
            if contact(&objets[membres[i]], &objets[membres[j]], mode, d.connexion_isolement_mm) {
                touche[i] = true;
                touche[j] = true;
                let (ri, rj) = (racine(&mut parent, i), racine(&mut parent, j));
                if ri != rj {
                    parent[ri.max(rj)] = ri.min(rj);
                }
            }
        }
        let est_membre = |i: usize| match objets[membres[i]].genre {
            Genre::Pastille => true,
            Genre::Zone => touche[i],
            _ => false,
        };
        res.zones_plan_isolees +=
            (0..membres.len()).filter(|&i| objets[membres[i]].genre == Genre::Zone && !touche[i]).count() as u64;
        let nb_membres = (0..membres.len()).filter(|&i| est_membre(i)).count();
        if nb_membres < 2 {
            continue;
        }
        res.reseaux_a_router += 1;
        // Classes, dans l'ordre de leur plus petit objet (la racine est le plus petit indice de la classe).
        let mut classes: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
        for i in 0..membres.len() {
            let r = racine(&mut parent, i);
            classes.entry(r).or_default().push(i);
        }
        let composantes: Vec<&Vec<usize>> = classes
            .values()
            .filter(|c| {
                c.iter().any(|&i| {
                    est_membre(i)
                        || matches!(objets[membres[i]].genre, Genre::Piste | Genre::Via | Genre::Surface | Genre::Couture)
                })
            })
            .collect();
        if composantes.len() <= 1 {
            res.reseaux_complets += 1;
            continue;
        }
        let zones = |c: &Vec<usize>| c.iter().filter(|&&i| objets[membres[i]].genre == Genre::Zone && est_membre(i)).count();
        let mut reference = 0;
        for (q, c) in composantes.iter().enumerate() {
            let rc = composantes[reference];
            if (zones(c), c.len()) > (zones(rc), rc.len()) {
                reference = q;
            }
        }
        for (q, c) in composantes.iter().enumerate() {
            if q == reference {
                continue;
            }
            let pastilles: Vec<&Objet> =
                c.iter().map(|&i| &objets[membres[i]]).filter(|o| o.genre == Genre::Pastille).collect();
            let avec_membre = c.iter().any(|&i| est_membre(i));
            let position = pastilles.first().map(|o| o.centre).unwrap_or(objets[membres[c[0]]].centre);
            res.liaisons.push(LiaisonOuverte {
                reseau: reseau.nom.clone(),
                nature: if avec_membre { NatureLiaison::Pastilles } else { NatureLiaison::Ilot },
                pastilles: pastilles.iter().map(|o| o.id.clone()).collect(),
                noms: pastilles.iter().map(|o| o.nom.clone()).collect(),
                position_mm: position,
            });
        }
    }
    res
}

/// Vérification du routage (§4.1 à §4.4) dans le mode demandé ; `couture` : vias de couture (`vias_couture/2`),
/// objets neufs comme les vias du routage.
///
/// Étape 6 : objets, contacts, connectivité (§4.1 à §4.3), les 12 violations de §4.4, avertissements et mesures
/// de §4.7, exacts (les grilles ne proposent que des candidats).
pub fn verifier_routage(
    donnees: &Value,
    placements: &Value,
    routage: &Value,
    couture: Option<&Value>,
    mode: Mode,
) -> Result<Verdict, String> {
    let d = lire_donnees(donnees)?;
    let placement = lire_placement(placements)?;
    let r = lire_routage(routage)?;
    let vias_couture = match couture {
        Some(c) => lire_couture(c)?,
        None => Vec::new(),
    };

    let mut violations = Vec::new();
    controler_sans_geometrie(&d, &r, &vias_couture, &mut violations);

    let (coudes, coudes_90, angles_aigus) = compter_coudes(&r.pistes);
    let mut longueur_pistes_mm = 0.0;
    let mut resistance_puissance_mohm = 0.0;
    for p in &r.pistes {
        let l = p.longueur_mm();
        longueur_pistes_mm += l;
        if d.reseau(&p.reseau).is_some_and(Reseau::de_puissance) {
            resistance_puissance_mohm += resistance_mohm(l, p.largeur * MIL);
        }
    }
    // Connectivité exacte (§4.1 à §4.3) puis violations géométriques (§4.4).
    let objets = construire_objets(&d, &placement, &r, &vias_couture);
    let poses = poses_composants(&d, &placement);
    let mut derogations = compteurs_a_zero(&DEROGATIONS);
    let (marge_isolement_min_mm, marge_isolement_min_paire) = controler_isolement(&d, &objets, &mut violations);
    controler_gardes(&d, &objets, &poses, &mut violations);
    controler_zones_interdites(&d, &objets, &poses, &mut violations);
    controler_trous(&d, &objets, &poses, &r, &vias_couture, &mut violations);
    let derog = controler_via_sous_cms(&d, &objets, &mut violations);
    derogations.insert("via_dans_pastille_meme_reseau".into(), derog);
    controler_antennes(&d, &objets, &mut violations);
    let (ecart_moyen_mm, ecart_min_mm) = ecart_moyen(&d, &objets);
    let mesures = Mesures {
        ecart_moyen_mm,
        ecart_min_mm,
        coudes,
        coudes_90,
        angles_aigus,
        longueur_pistes_mm,
        resistance_puissance_mohm,
        vias: r.vias.len() as u64,
    };

    let mut avertissements = compteurs_a_zero(&AVERTISSEMENTS);
    avertissements.insert("angles_aigus".into(), angles_aigus);
    avertissements.insert("paires_desequilibrees".into(), compter_paires_desequilibrees(&d, &r));
    avertissements.insert("regles_non_couvertes".into(), d.regles_non_couvertes);
    avertissements.insert("pistes_sous_pieces_sans_piste_dessous".into(), pistes_sous_pieces(&d, &objets, &poses));

    let cx = connectivite(&d, &objets, mode);
    avertissements.insert("zones_plan_isolees".into(), cx.zones_plan_isolees);
    let liaisons_ouvertes = cx.liaisons;

    Ok(Verdict {
        mode,
        valide: liaisons_ouvertes.is_empty() && violations.is_empty(),
        liaisons_ouvertes,
        violations,
        derogations,
        avertissements,
        reseaux_a_router: cx.reseaux_a_router,
        reseaux_complets: cx.reseaux_complets,
        marge_isolement_min_mm,
        marge_isolement_min_paire,
        mesures,
    })
}

// =================================================================================================================
// Violations géométriques (§4.4), avertissements et écart moyen (§4.7)
// =================================================================================================================

/// Grille d'accélération (cases carrées de côté `pas`, repère commun) : elle ne propose que des candidats ; chaque
/// décision est prise ensuite par un calcul exact.
struct Grille {
    pas: f64,
    cases: BTreeMap<(i64, i64), Vec<usize>>,
}

fn boite_finie(b: &[f64; 4]) -> bool {
    b.iter().all(|x| x.is_finite())
}

fn elargir(b: [f64; 4], m: f64) -> [f64; 4] {
    [b[0] - m, b[1] - m, b[2] + m, b[3] + m]
}

fn boites_se_recouvrent(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3]
}

impl Grille {
    /// Range la boîte `i` dans les cases qu'elle touche (cases [k·pas, (k+1)·pas[), si `garder(i)`.
    fn new(boites: &[[f64; 4]], pas: f64, garder: impl Fn(usize) -> bool) -> Grille {
        let mut g = Grille { pas, cases: BTreeMap::new() };
        for (i, b) in boites.iter().enumerate() {
            if garder(i) && boite_finie(b) {
                let (x0, y0, x1, y1) = g.plage(*b);
                for x in x0..=x1 {
                    for y in y0..=y1 {
                        g.cases.entry((x, y)).or_default().push(i);
                    }
                }
            }
        }
        g
    }

    fn plage(&self, b: [f64; 4]) -> (i64, i64, i64, i64) {
        let c = |v: f64| (v / self.pas).floor() as i64;
        (c(b[0]), c(b[1]), c(b[2]), c(b[3]))
    }

    /// Indices rangés dans une case touchée par la boîte `b` (triés, sans doublon).
    fn candidats(&self, b: [f64; 4]) -> Vec<usize> {
        let mut v = Vec::new();
        if boite_finie(&b) {
            let (x0, y0, x1, y1) = self.plage(b);
            for x in x0..=x1 {
                for y in y0..=y1 {
                    if let Some(l) = self.cases.get(&(x, y)) {
                        v.extend_from_slice(l);
                    }
                }
            }
        }
        v.sort_unstable();
        v.dedup();
        v
    }
}

/// Nom du (premier) réseau d'un objet ; `""` sans réseau.
fn nom_reseau(d: &Donnees, o: &Objet) -> String {
    o.reseaux.first().map_or(String::new(), |&r| d.reseaux[r].nom.clone())
}

fn partagent_reseau(a: &Objet, b: &Objet) -> bool {
    a.reseaux.iter().any(|r| b.reseaux.contains(r))
}

/// Écart bord à bord minimal de deux objets sur leurs couches communes (`None` : aucune couche commune).
fn ecart_commun(a: &Objet, b: &Objet) -> Option<f64> {
    let mut m: Option<f64> = None;
    for (ca, fa) in &a.couches {
        if let Some((_, fb)) = b.couches.iter().find(|(cb, _)| cb == ca) {
            let e = fa.ecart(fb);
            m = Some(m.map_or(e, |x| x.min(e)));
        }
    }
    m
}

fn milieu(a: Pt, b: Pt) -> [f64; 2] {
    [0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1])]
}

fn violation_objets(
    genre: &str,
    reseaux: Vec<String>,
    objets: Vec<String>,
    position_mm: [f64; 2],
    ecart: Option<f64>,
    requis: Option<f64>,
) -> Violation {
    Violation { genre: genre.to_string(), reseaux, objets, position_mm, ecart_mm: ecart, requis_mm: requis }
}

/// `isolement` (§4.4) et marge d'isolement minimale (convention 11 du corpus) ; rend (marge, paire). Les zones de
/// plan ne sont jamais contrôlées (dégagement du plan) ; deux objets qui partagent un réseau (région de liaison
/// comprise) non plus.
fn controler_isolement(d: &Donnees, objets: &[Objet], violations: &mut Vec<Violation>) -> (Option<f64>, String) {
    let plus_grand = d.matrice.iter().flatten().fold(0.0f64, |m, &x| m.max(x));
    let m = plus_grand.max(ECART_ELARGI_MM);
    let boites: Vec<[f64; 4]> = objets.iter().map(|o| elargir(o.boite, m)).collect();
    let grille = Grille::new(&boites, CASE_GRILLE_MM, |i| objets[i].genre != Genre::Zone);
    let mut marge: Option<(f64, usize, usize)> = None;
    for (i, a) in objets.iter().enumerate() {
        if !a.genre.neuf() {
            continue;
        }
        for j in grille.candidats(boites[i]) {
            let b = &objets[j];
            if j == i || (b.genre.neuf() && j < i) || partagent_reseau(a, b) {
                continue;
            }
            if !boites_se_recouvrent(boites[i], boites[j]) {
                continue;
            }
            let Some(e) = ecart_commun(a, b) else { continue };
            let (p, q) = (i.min(j), i.max(j));
            let requis = d.matrice[objets[p].classe][objets[q].classe];
            let mg = e - requis;
            if marge.map_or(true, |(x, pp, qq)| mg < x || (mg == x && (p, q) < (pp, qq))) {
                marge = Some((mg, p, q));
            }
            if e < requis - EPS_ISOLEMENT {
                let (op, oq) = (&objets[p], &objets[q]);
                violations.push(violation_objets(
                    "isolement",
                    vec![nom_reseau(d, op), nom_reseau(d, oq)],
                    vec![op.id.clone(), oq.id.clone()],
                    milieu(op.centre, oq.centre),
                    Some(e),
                    Some(requis),
                ));
            }
        }
    }
    match marge {
        Some((x, p, q)) => (
            Some(x),
            format!(
                "{}({}) / {}({})",
                objets[p].id,
                nom_reseau(d, &objets[p]),
                objets[q].id,
                nom_reseau(d, &objets[q])
            ),
        ),
        None => (None, String::new()),
    }
}

/// Intervalle de [0, 1] où la fonction `f`, convexe, est < `seuil` (recherche ternaire du minimum puis dichotomie
/// des deux bords, à 1e-15 près) ; pour une fonction non convexe (pastille polygonale concave), approché.
fn intervalle_sous(f: impl Fn(f64) -> f64, seuil: f64) -> Option<(f64, f64)> {
    let (mut lo, mut hi) = (0.0f64, 1.0f64);
    for _ in 0..100 {
        let m1 = lo + (hi - lo) / 3.0;
        let m2 = hi - (hi - lo) / 3.0;
        if f(m1) <= f(m2) {
            hi = m2;
        } else {
            lo = m1;
        }
    }
    let mut tm = 0.5 * (lo + hi);
    for t in [0.0, 1.0] {
        if f(t) < f(tm) {
            tm = t;
        }
    }
    if f(tm) >= seuil {
        return None;
    }
    let bord = |mut dedans: f64, mut dehors: f64| {
        for _ in 0..60 {
            let m = 0.5 * (dedans + dehors);
            if f(m) < seuil {
                dedans = m;
            } else {
                dehors = m;
            }
        }
        dedans
    };
    let a = if f(0.0) < seuil { 0.0 } else { bord(tm, 0.0) };
    let b = if f(1.0) < seuil { 1.0 } else { bord(tm, 1.0) };
    Some((a, b))
}

fn fusionner(mut l: Vec<(f64, f64)>) -> Vec<(f64, f64)> {
    l.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut r: Vec<(f64, f64)> = Vec::new();
    for (lo, hi) in l {
        match r.last_mut() {
            Some(der) if lo <= der.1 => der.1 = der.1.max(hi),
            _ => r.push((lo, hi)),
        }
    }
    r
}

/// a \ b, intervalles fusionnés et triés.
fn soustraire(a: &[(f64, f64)], b: &[(f64, f64)]) -> Vec<(f64, f64)> {
    let mut r = Vec::new();
    for &(lo, hi) in a {
        let mut debut = lo;
        for &(blo, bhi) in b {
            if bhi <= debut || blo >= hi {
                continue;
            }
            if blo > debut {
                r.push((debut, blo));
            }
            debut = debut.max(bhi);
            if debut >= hi {
                break;
            }
        }
        if debut < hi {
            r.push((debut, hi));
        }
    }
    r
}

/// `garde_bord` d'une piste (§4.4, convention 9) : points de l'axe à moins de garde + demi-largeur du contour, ou
/// hors du contour, hors des parties de l'axe dans le cuivre d'une pastille de son réseau sur sa couche. Rend le pire
/// écart (négatif hors du contour) et son point.
fn garde_bord_piste(
    d: &Donnees,
    objets: &[Objet],
    i: usize,
    (a, b, hw): (Pt, Pt, f64),
    aretes: &[[Pt; 2]],
    pastilles: &Grille,
) -> Option<(f64, [f64; 2])> {
    let r = d.garde_bord_mm + hw - EPS_ISOLEMENT;
    let pt = |t: f64| [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
    let mut proches = Vec::new();
    for e in aretes {
        if geometrie::distance_segments(a, b, e[0], e[1]) < r {
            if let Some(iv) = intervalle_sous(|t| geometrie::distance_point_segment(pt(t), e[0], e[1]), r) {
                proches.push(iv);
            }
        }
    }
    let proches = fusionner(proches);
    // Entre deux intervalles proches, l'axe ne traverse pas le contour : morceau entièrement dedans ou dehors.
    let mut mauvais = proches.clone();
    let mut debut = 0.0f64;
    for &(lo, hi) in proches.iter().chain(std::iter::once(&(1.0, 1.0))) {
        if lo > debut && !geometrie::dans_polygone(pt(0.5 * (debut + lo)), &d.contour) {
            mauvais.push((debut, lo));
        }
        debut = debut.max(hi);
    }
    if proches.is_empty() && !geometrie::dans_polygone(a, &d.contour) {
        mauvais.push((0.0, 1.0));
    }
    let mauvais = fusionner(mauvais);
    if mauvais.is_empty() {
        return None;
    }
    let o = &objets[i];
    let mut exclus = Vec::new();
    if let (Some(&(couche, _)), Some(&rs)) = (o.couches.first(), o.reseaux.first()) {
        for j in pastilles.candidats(o.boite) {
            let p = &objets[j];
            if !p.reseaux.contains(&rs) {
                continue;
            }
            if let Some((_, f)) = p.couches.iter().find(|(k, _)| *k == couche) {
                if let Some(iv) = intervalle_sous(|t| f.distance_signee(pt(t)), 1e-12) {
                    exclus.push(iv);
                }
            }
        }
    }
    let reste = soustraire(&mauvais, &fusionner(exclus));
    let distance_signee = |q: Pt| {
        let dmin =
            aretes.iter().map(|e| geometrie::distance_point_segment(q, e[0], e[1])).fold(f64::INFINITY, f64::min);
        if geometrie::dans_polygone(q, &d.contour) {
            dmin
        } else {
            -dmin
        }
    };
    let mut pire: Option<(f64, [f64; 2])> = None;
    for &(lo, hi) in &reste {
        if hi - lo <= 1e-9 {
            continue;
        }
        for k in 0..=16 {
            let q = pt(lo + (hi - lo) * k as f64 / 16.0);
            let e = distance_signee(q) - hw;
            if pire.map_or(true, |(x, _)| e < x) {
                pire = Some((e, q));
            }
        }
    }
    pire
}

/// Découpes posées : celles de la carte (sauf celles d'une pièce qui porte ses propres découpes, qui la suivent) et
/// celles des empreintes posées.
fn decoupes_posees(d: &Donnees, poses: &[Pose]) -> Vec<(String, Forme)> {
    let propre = |n: &String| d.index_composants.get(n).is_some_and(|&i| !d.composants[i].decoupes.is_empty());
    let mut v: Vec<(String, Forme)> = d
        .decoupes
        .iter()
        .filter(|(_, _, prop)| !prop.as_ref().is_some_and(propre))
        .map(|(id, f, _)| (id.clone(), f.clone()))
        .collect();
    for (c, pose) in d.composants.iter().zip(poses) {
        let gp = geo_pose(pose);
        for (id, f) in &c.decoupes {
            v.push((id.clone(), gp.poser(f)));
        }
    }
    v
}

/// `garde_bord` et `garde_decoupe` (§4.4) des objets neufs.
fn controler_gardes(d: &Donnees, objets: &[Objet], poses: &[Pose], violations: &mut Vec<Violation>) {
    let mut ferme = d.contour.clone();
    ferme.push(d.contour[0]);
    let aretes: Vec<[Pt; 2]> = ferme.windows(2).map(|w| [w[0], w[1]]).collect();
    let bord = Forme::polyligne(&ferme, 0.0);
    let region = Forme::polygone(d.contour.clone(), vec![]);
    let g = d.garde_bord_mm;
    let boites: Vec<[f64; 4]> = objets.iter().map(|o| o.boite).collect();
    let pastilles = Grille::new(&boites, CASE_GRILLE_MM, |i| objets[i].genre == Genre::Pastille);
    for (i, o) in objets.iter().enumerate() {
        if !o.genre.neuf() {
            continue;
        }
        let faute = match o.axe {
            Some(axe) => garde_bord_piste(d, objets, i, axe, &aretes, &pastilles),
            None => {
                let mut pire: Option<(f64, [f64; 2])> = None;
                for (_, f) in &o.couches {
                    let e = bord.ecart(f);
                    let dedans = f.contenue_dans(&region, EPS_CONTACT);
                    let e = if dedans { e } else { -e.abs() };
                    if (!dedans || e < g - EPS_ISOLEMENT) && pire.map_or(true, |(x, _)| e < x) {
                        pire = Some((e, o.centre));
                    }
                }
                pire
            }
        };
        if let Some((e, pos)) = faute {
            violations.push(violation_objets("garde_bord", vec![nom_reseau(d, o)], vec![o.id.clone()], pos, Some(e), Some(g)));
        }
    }
    let gd = d.garde_decoupe_mm;
    let decoupes = decoupes_posees(d, poses);
    for o in objets.iter().filter(|o| o.genre.neuf()) {
        for (id, fd) in &decoupes {
            if !boites_se_recouvrent(elargir(o.boite, gd), fd.boite()) {
                continue;
            }
            let e = o.couches.iter().map(|(_, f)| f.ecart(fd)).fold(f64::INFINITY, f64::min);
            if e < gd - EPS_ISOLEMENT {
                violations.push(violation_objets(
                    "garde_decoupe",
                    vec![nom_reseau(d, o)],
                    vec![o.id.clone(), id.clone()],
                    o.centre,
                    Some(e),
                    Some(gd),
                ));
            }
        }
    }
}

fn couches_posees(l: &[usize], n: usize, retourne: bool) -> Vec<usize> {
    let mut v: Vec<usize> = l.iter().map(|&k| if retourne { n - 1 - k } else { k }).collect();
    v.sort_unstable();
    v
}

/// `zone_interdite` (§4.4) : cuivre neuf à moins de `isolement.defaut` d'une zone de sa couche et de son effet
/// (`pistes`, `vias`, `remplissages`), de la carte ou d'une empreinte posée.
fn controler_zones_interdites(d: &Donnees, objets: &[Objet], poses: &[Pose], violations: &mut Vec<Violation>) {
    let n = d.couches.len();
    let mut zones = d.zones_interdites.clone();
    for (c, pose) in d.composants.iter().zip(poses) {
        let gp = geo_pose(pose);
        let retourne = pose.dessous != c.pose_cible.dessous;
        for z in &c.zones_interdites {
            zones.push(ZoneInterdite {
                id: z.id.clone(),
                couches: couches_posees(&z.couches, n, retourne),
                forme: gp.poser(&z.forme),
                effets: z.effets.clone(),
            });
        }
    }
    let marge = d.isolement_defaut_mm;
    for o in objets.iter().filter(|o| o.genre.neuf()) {
        let effet = match o.genre {
            Genre::Piste => "pistes",
            Genre::Surface => "remplissages",
            _ => "vias",
        };
        for z in &zones {
            if !z.effets.iter().any(|e| e == effet) || !boites_se_recouvrent(elargir(o.boite, marge), z.forme.boite()) {
                continue;
            }
            let e = o
                .couches
                .iter()
                .filter(|(k, _)| z.couches.contains(k))
                .map(|(_, f)| f.ecart(&z.forme))
                .fold(f64::INFINITY, f64::min);
            if e < marge - EPS_ISOLEMENT {
                violations.push(violation_objets(
                    "zone_interdite",
                    vec![nom_reseau(d, o)],
                    vec![o.id.clone(), z.id.clone()],
                    o.centre,
                    Some(e),
                    Some(marge),
                ));
            }
        }
    }
}

/// Paroi de trou posée (§4.1).
struct Trou {
    id: String,
    reseau: String,
    forme: Forme,
    /// Trou d'un via neuf (routage ou couture).
    neuf: bool,
    /// Trou sans cuivre : objet de `garde_trou`.
    garde: bool,
}

fn trous_poses(d: &Donnees, poses: &[Pose], r: &Routage, couture: &[Via]) -> Vec<Trou> {
    let mut v = Vec::new();
    for (c, pose) in d.composants.iter().zip(poses) {
        let gp = geo_pose(pose);
        for p in &c.pastilles {
            if let Some((f, metallise)) = &p.trou {
                v.push(Trou { id: p.id.clone(), reseau: p.reseau.clone(), forme: gp.poser(f), neuf: false, garde: !metallise });
            }
        }
    }
    for (c, pose) in d.composants.iter().zip(poses) {
        let gp = geo_pose(pose);
        for via in &c.vias {
            v.push(Trou {
                id: via.id.clone(),
                reseau: via.reseau.map_or(String::new(), |i| d.reseaux[i].nom.clone()),
                forme: Forme::disque(gp.vers_monde(via.centre), via.rayon_trou),
                neuf: false,
                garde: false,
            });
        }
    }
    for f in &d.cuivre_fixe {
        if let Some((c, _, rt)) = f.via {
            v.push(Trou {
                id: f.id.clone(),
                reseau: f.reseau.map_or(String::new(), |i| d.reseaux[i].nom.clone()),
                forme: Forme::disque(c, rt),
                neuf: false,
                garde: false,
            });
        }
    }
    for t in d.trous.iter().filter(|t| !t.de_piece) {
        v.push(Trou { id: t.id.clone(), reseau: String::new(), forme: t.paroi.clone(), neuf: false, garde: t.sans_cuivre });
    }
    for (prefixe, liste) in [("V", r.vias.as_slice()), ("C", couture)] {
        for (k, via) in liste.iter().enumerate() {
            v.push(Trou {
                id: format!("{prefixe}{k}"),
                reseau: via.reseau.clone(),
                forme: Forme::disque([via.x * MIL, via.y * MIL], 0.5 * via.trou * MIL),
                neuf: true,
                garde: false,
            });
        }
    }
    v
}

/// `garde_trou` et `trou_trou` (§4.4, convention 7).
fn controler_trous(
    d: &Donnees,
    objets: &[Objet],
    poses: &[Pose],
    r: &Routage,
    couture: &[Via],
    violations: &mut Vec<Violation>,
) {
    let trous = trous_poses(d, poses, r, couture);
    let boites: Vec<[f64; 4]> = trous.iter().map(|t| t.forme.boite()).collect();
    let g = d.garde_trou_mm;
    let el_g: Vec<[f64; 4]> = boites.iter().map(|b| elargir(*b, g)).collect();
    let grille_g = Grille::new(&el_g, CASE_GRILLE_MM, |i| trous[i].garde);
    for o in objets.iter().filter(|o| o.genre.neuf()) {
        for j in grille_g.candidats(o.boite) {
            let t = &trous[j];
            let e = o.couches.iter().map(|(_, f)| f.ecart(&t.forme)).fold(f64::INFINITY, f64::min);
            if e < g - EPS_ISOLEMENT {
                violations.push(violation_objets(
                    "garde_trou",
                    vec![nom_reseau(d, o), t.reseau.clone()],
                    vec![o.id.clone(), t.id.clone()],
                    milieu(o.centre, centre_boite(boites[j])),
                    Some(e),
                    Some(g),
                ));
            }
        }
    }
    let tt = d.trou_trou_mm;
    let el: Vec<[f64; 4]> = boites.iter().map(|b| elargir(*b, tt)).collect();
    let grille = Grille::new(&el, CASE_GRILLE_MM, |_| true);
    for (i, a) in trous.iter().enumerate() {
        if !a.neuf {
            continue;
        }
        for j in grille.candidats(el[i]) {
            let b = &trous[j];
            if j == i || (b.neuf && j < i) {
                continue;
            }
            let e = a.forme.ecart(&b.forme);
            if e < tt - EPS_ISOLEMENT {
                let (p, q) = (&trous[i.min(j)], &trous[i.max(j)]);
                violations.push(violation_objets(
                    "trou_trou",
                    vec![p.reseau.clone(), q.reseau.clone()],
                    vec![p.id.clone(), q.id.clone()],
                    milieu(centre_boite(p.forme.boite()), centre_boite(q.forme.boite())),
                    Some(e),
                    Some(tt),
                ));
            }
        }
    }
}

/// `via_sous_cms` (§4.4, convention 9) : centre du via à une distance du cuivre d'une pastille CMS sans
/// `via_dans_pastille_permis` < rayon du via − ε ; même réseau et `via_dans_pastille_meme_reseau` : dérogation.
/// Rend le nombre de dérogations.
fn controler_via_sous_cms(d: &Donnees, objets: &[Objet], violations: &mut Vec<Violation>) -> u64 {
    let boites: Vec<[f64; 4]> = objets.iter().map(|o| o.boite).collect();
    let grille = Grille::new(&boites, CASE_GRILLE_MM, |i| objets[i].cms_sans_via);
    let mut derogations = 0;
    for v in objets.iter().filter(|o| o.est_via && o.genre.neuf()) {
        for j in grille.candidats(v.boite) {
            let p = &objets[j];
            if !p.couches.iter().any(|(_, f)| f.distance_signee(v.centre) < v.rayon - EPS_CONTACT) {
                continue;
            }
            if d.via_meme_reseau && partagent_reseau(v, p) {
                derogations += 1;
            } else {
                violations.push(violation_objets(
                    "via_sous_cms",
                    vec![nom_reseau(d, p), nom_reseau(d, v)],
                    vec![p.id.clone(), v.id.clone()],
                    v.centre,
                    None,
                    None,
                ));
            }
        }
    }
    derogations
}

/// `antenne` (§4.4, convention 6) : extrémité d'une piste neuve sans contact strict avec un autre cuivre de son
/// réseau sur sa couche (jamais une zone de plan) ; une violation par piste, à sa première extrémité fautive. Une
/// piste de réseau inconnu relève de `reseau_inconnu` seulement.
fn controler_antennes(d: &Donnees, objets: &[Objet], violations: &mut Vec<Violation>) {
    let boites: Vec<[f64; 4]> = objets.iter().map(|o| o.boite).collect();
    let grille = Grille::new(&boites, CASE_GRILLE_MM, |i| objets[i].genre != Genre::Zone);
    for (i, o) in objets.iter().enumerate() {
        let (Genre::Piste, Some((a, b, _)), Some(&rs), Some(&(couche, _))) =
            (o.genre, o.axe, o.reseaux.first(), o.couches.first())
        else {
            continue;
        };
        for e in [a, b] {
            let petite = [e[0] - EPS_CONTACT, e[1] - EPS_CONTACT, e[0] + EPS_CONTACT, e[1] + EPS_CONTACT];
            let touche = grille.candidats(petite).into_iter().any(|j| {
                let x = &objets[j];
                if j == i || !x.reseaux.contains(&rs) {
                    return false;
                }
                let Some((_, f)) = x.couches.iter().find(|(k, _)| *k == couche) else { return false };
                match x.axe {
                    Some((q1, q2, hwo)) => geometrie::distance_point_segment(e, q1, q2) <= hwo + EPS_CONTACT,
                    None if x.est_via => geometrie::distance(e, x.centre) <= x.rayon + EPS_CONTACT,
                    None => f.distance_signee(e) <= EPS_CONTACT,
                }
            });
            if !touche {
                violations.push(violation_objets("antenne", vec![nom_reseau(d, o)], vec![o.id.clone()], e, None, None));
                break;
            }
        }
    }
}

/// Avertissement `pistes_sous_pieces_sans_piste_dessous` (§4.4) : piste sur la couche de la face d'une pièce de
/// `sans_piste_dessous` dont le cuivre coupe la boîte posée de la pièce (`lbb`) et qui est à plus de 0,3 mm de
/// chacune de ses pastilles ; un compte par couple (piste, pièce).
fn pistes_sous_pieces(d: &Donnees, objets: &[Objet], poses: &[Pose]) -> u64 {
    let n = d.couches.len();
    let mut total = 0;
    for nom in &d.sans_piste_dessous {
        let Some(&ic) = d.index_composants.get(nom) else { continue };
        let (c, pose) = (&d.composants[ic], poses[ic]);
        let gp = geo_pose(&pose);
        let face = if pose.dessous { n - 1 } else { 0 };
        let b = c.lbb;
        let coins = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];
        let boite = Forme::polygone(coins.iter().map(|&q| gp.vers_monde(q)).collect(), vec![]);
        let prefixe = format!("{nom}#");
        let pastilles: Vec<&Objet> =
            objets.iter().filter(|o| o.genre == Genre::Pastille && o.id.starts_with(&prefixe)).collect();
        for o in objets.iter().filter(|o| o.genre == Genre::Piste) {
            let Some((k, f)) = o.couches.first() else { continue };
            if *k != face || f.ecart(&boite) > 0.0 {
                continue;
            }
            let loin = pastilles
                .iter()
                .all(|p| p.couches.iter().filter(|(kp, _)| kp == k).all(|(_, fp)| fp.ecart(f) > SOUS_PIECE_MM));
            if loin {
                total += 1;
            }
        }
    }
    total
}

/// Écart moyen et minimal (§4.7, convention 10) : pour chaque piste neuve, plus petit écart à un objet d'un autre
/// réseau ou sans réseau (zones de plan exclues), sur une couche commune, dont la boîte élargie de 0,3 mm partage une
/// case de 1 mm avec la sienne ; moyenne de min(5 × défaut ; écart) sur les pistes qui ont un voisin ; minimum des
/// écarts (non plafonnés).
fn ecart_moyen(d: &Donnees, objets: &[Objet]) -> (f64, Option<f64>) {
    let plafond = 5.0 * d.isolement_defaut_mm;
    let boites: Vec<[f64; 4]> = objets.iter().map(|o| elargir(o.boite, ECART_ELARGI_MM)).collect();
    let grille = Grille::new(&boites, ECART_CASE_MM, |i| objets[i].genre != Genre::Zone);
    let (mut somme, mut n, mut min) = (0.0, 0u64, None::<f64>);
    for (i, o) in objets.iter().enumerate() {
        if o.genre != Genre::Piste {
            continue;
        }
        let mut e_piste: Option<f64> = None;
        for j in grille.candidats(boites[i]) {
            if j == i || partagent_reseau(o, &objets[j]) {
                continue;
            }
            if let Some(e) = ecart_commun(o, &objets[j]) {
                e_piste = Some(e_piste.map_or(e, |x| x.min(e)));
            }
        }
        if let Some(e) = e_piste {
            n += 1;
            somme += e.min(plafond);
            min = Some(min.map_or(e, |x| x.min(e)));
        }
    }
    (somme / n.max(1) as f64, min)
}

/// Pose de chaque pièce (ordre de `donnees.composants`) : celle du placement, sinon `pose_cible`.
fn poses_composants(d: &Donnees, placement: &Placement) -> Vec<Pose> {
    let mut m: BTreeMap<&str, Pose> = BTreeMap::new();
    for e in &placement.pieces {
        m.insert(e.designator.as_str(), e.pose);
    }
    d.composants.iter().map(|c| m.get(c.nom.as_str()).copied().unwrap_or(c.pose_cible)).collect()
}

// =================================================================================================================
// Coût commun (§4.7)
// =================================================================================================================

/// Coût commun `cout_commun/2` (§4.7) d'un routage, avec les coudes et l'écart moyen du verdict strict :
///
/// `longueur_mm + 1,5 × vias + 2,0 × retreci_mm + 3,0 × resistance_puissance_mohm + 0,1 × coudes
///  + 1000 × max(0 ; s − ecart_moyen_mm)`, avec s = 3 × `isolement.defaut`.
///
/// Ne panique jamais : une entrée illisible donne les termes qu'on peut lire (une piste ou un réseau mal formé est
/// ignoré ; `isolement.defaut` illisible vaut 0). Les vias de couture ne sont pas dans `routage` : ils sont exclus.
pub fn cout_commun(donnees: &Value, routage: &Value, verdict_strict: &Verdict) -> CoutCommun {
    let defaut = donnees
        .pointer("/regles/isolement/defaut")
        .and_then(Value::as_f64)
        .filter(|x| x.is_finite())
        .unwrap_or(0.0);
    let seuil_ecart_mm = 3.0 * defaut;
    // Réseau → (largeur préférée max(largeurs_mm[1], largeurs_mm[0]), rôle de puissance).
    let mut reseaux: BTreeMap<&str, (f64, bool)> = BTreeMap::new();
    if let Some(l) = donnees.get("reseaux").and_then(Value::as_array) {
        for r in l {
            let Some(nom) = r.get("nom").and_then(Value::as_str) else { continue };
            let larg = r.get("largeurs_mm").and_then(Value::as_array);
            let lire = |i: usize| larg.and_then(|l| l.get(i)).and_then(Value::as_f64).unwrap_or(0.0);
            let role = r.get("role").and_then(Value::as_str).unwrap_or("");
            reseaux.insert(nom, (lire(1).max(lire(0)), role == "puissance" || role == "alimentation"));
        }
    }
    let (mut longueur, mut retreci, mut resistance) = (0.0, 0.0, 0.0);
    if let Some(l) = routage.get("pistes").and_then(Value::as_array) {
        for p in l {
            let f = |cle: &str| p.get(cle).and_then(Value::as_f64).filter(|x| x.is_finite());
            let (Some(x1), Some(y1), Some(x2), Some(y2), Some(w)) = (f("x1"), f("y1"), f("x2"), f("y2"), f("width"))
            else {
                continue;
            };
            let l_mm = longueur_mm(x1, y1, x2, y2);
            let w_mm = w * MIL;
            longueur += l_mm;
            if let Some(&(preferee, puissance)) = p.get("net_name").and_then(Value::as_str).and_then(|n| reseaux.get(n)) {
                if w_mm < preferee - EPS_RETRECI_MM {
                    retreci += l_mm;
                }
                if puissance {
                    resistance += resistance_mohm(l_mm, w_mm);
                }
            }
        }
    }
    let vias = routage.get("vias").and_then(Value::as_array).map_or(0, |l| l.len() as u64);
    let coudes = verdict_strict.mesures.coudes;
    let ecart_moyen_mm = verdict_strict.mesures.ecart_moyen_mm;
    let terme_ecart = 1000.0 * (seuil_ecart_mm - ecart_moyen_mm).max(0.0);
    let cout = longueur + 1.5 * vias as f64 + 2.0 * retreci + 3.0 * resistance + 0.1 * coudes as f64 + terme_ecart;
    CoutCommun {
        cout,
        longueur_mm: longueur,
        vias,
        retreci_mm: retreci,
        resistance_puissance_mohm: resistance,
        coudes,
        ecart_moyen_mm,
    }
}

// =================================================================================================================
// Tests internes (sans fichier) : fonctions privées
// =================================================================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // --- Géométrie (valeurs calculées à la main) ------------------------------------------------------------------

    use geometrie::{dans_piece, tourner, trou};

    fn proche(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-9, "{a} au lieu de {b}");
    }

    #[test]
    fn geometrie_rectangle_tourne_30() {
        // Rectangle 4 × 2 centré en (1 ; 2), tourné de 30° : un point à 3 sur l'axe long est à 3 − 2 = 1 du bord ;
        // à 0,5 sur l'axe court : −0,5 ; au point (3 ; 2) du repère propre : coin (2 ; 1) à √2.
        let f = Forme::rectangle([1.0, 2.0], 4.0, 2.0, 30.0);
        let pt = |x: f64, y: f64| {
            let q = tourner([x, y], 30.0);
            [1.0 + q[0], 2.0 + q[1]]
        };
        // R(30°)·(3 ; 0) = (2,598076211 ; 1,5).
        let p = pt(3.0, 0.0);
        proche(p[0], 1.0 + 2.598076211353316);
        proche(p[1], 3.5);
        proche(f.distance_signee(p), 1.0);
        proche(f.distance_signee(pt(0.0, 0.5)), -0.5);
        proche(f.distance_signee(pt(3.0, 2.0)), 2f64.sqrt());
        assert!(f.contient(pt(1.9, 0.9)) && !f.contient(pt(2.1, 0.0)));
    }

    #[test]
    fn geometrie_capsule_et_rectangle_arrondi() {
        let c = Forme::capsule([0.0, 0.0], [4.0, 0.0], 1.0);
        proche(c.distance_signee([2.0, 3.0]), 2.0);
        proche(c.distance_signee([6.0, 0.0]), 1.0);
        proche(c.distance_signee([-1.0, -1.0]), 2f64.sqrt() - 1.0);
        proche(c.distance_signee([2.0, 0.0]), -1.0);
        // Rectangle 4 × 2, rayon de coin 0,5 : noyau ±1,5 × ±0,5. (2,5 ; 1,5) : √2 − 0,5 ; coin de la boîte (2 ; 1) :
        // √0,5 − 0,5 = 0,2071 (dehors) ; milieu du côté (2 ; 0) : 0.
        let r = Forme::rectangle_arrondi([0.0, 0.0], 4.0, 2.0, 0.5, 0.0);
        proche(r.distance_signee([2.5, 1.5]), 2f64.sqrt() - 0.5);
        proche(r.distance_signee([2.0, 1.0]), 0.5f64.sqrt() - 0.5);
        proche(r.distance_signee([2.0, 0.0]), 0.0);
        proche(r.distance_signee([0.0, 0.0]), -1.0);
        // Écart bord à bord : capsule (y = 0, r 1) et rectangle arrondi translaté de 5 en y : 5 − 1 − 1 = 3.
        let r5 = Forme::rectangle_arrondi([2.0, 5.0], 4.0, 2.0, 0.5, 0.0);
        proche(c.ecart(&r5), 3.0);
        assert_eq!(r.boite(), [-2.0, -1.0, 2.0, 1.0]);
    }

    #[test]
    fn geometrie_octogone_et_inclusion() {
        // Octogone : carré [−1 ; 1]², chanfrein de 25 % du petit côté (0,5). Coin (1 ; 1) : droite x + y = 1,5 à
        // 0,5/√2 = 0,353553 ; centre : −1 (côté x = 1 à 1, chanfrein à 1,5/√2 = 1,0607).
        let o = Forme::polygone(
            vec![[-0.5, -1.0], [0.5, -1.0], [1.0, -0.5], [1.0, 0.5], [0.5, 1.0], [-0.5, 1.0], [-1.0, 0.5], [-1.0, -0.5]],
            vec![],
        );
        proche(o.distance_signee([1.0, 1.0]), 0.5 / 2f64.sqrt());
        proche(o.distance_signee([0.0, 0.0]), -1.0);
        // Inclusion dans une zone carrée [−2 ; 2]² percée d'un trou [1,5 ; 1,9] × [−0,2 ; 0,2] : contenu ; trou
        // [0,8 ; 1,9] × [−0,2 ; 0,2] (qui mord le côté x = 1) : non contenu.
        let zone = |x0: f64| {
            Forme::polygone(
                vec![[-2.0, -2.0], [2.0, -2.0], [2.0, 2.0], [-2.0, 2.0]],
                vec![vec![[x0, -0.2], [1.9, -0.2], [1.9, 0.2], [x0, 0.2]]],
            )
        };
        assert!(o.contenue_dans(&zone(1.5), 1e-9));
        assert!(!o.contenue_dans(&zone(0.8), 1e-9));
        // Disque de rayon 0,5 en (1,6 ; 1,6) : bord à 1,6 + 0,5 = 2,1 > 2 : non contenu ; en (1,4 ; 1,4) : contenu.
        assert!(!Forme::disque([1.6, 1.6], 0.5).contenue_dans(&zone(1.5), 1e-9));
        assert!(Forme::disque([1.4, 1.4], 0.5).contenue_dans(&zone(1.5), 1e-9));
        // Écart polygone – polygone : octogone et carré [2 ; 3]² : coin (2 ; 2) au chanfrein : 2,5/√2 = 1,767767.
        let carre = Forme::polygone(vec![[2.0, 2.0], [3.0, 2.0], [3.0, 3.0], [2.0, 3.0]], vec![]);
        proche(o.ecart(&carre), (4.0 - 1.5) / 2f64.sqrt());
    }

    #[test]
    fn geometrie_trou_oblong_decale_et_piece_dessous() {
        // Pastille en (1 ; 0) (repère local), rotation relative 90° ; trou oblong Ø 0,8, longueur 2, rotation 0
        // relative à la pastille, décalage (0,5 ; 0) dans le repère de la pastille ; pièce en (10 ; 5), 0°, dessus.
        // Centre local = (1 ; 0) + R(90°)·(0,5 ; 0) = (1 ; 0,5) ; axe à 90° : segment (11 ; 4,9) – (11 ; 6,1), r 0,4.
        let propre = trou("oblong", 0.8, 2.0, 0.0, [0.5, 0.0]).unwrap();
        let local = dans_piece(&propre, [1.0, 0.0], 90.0);
        let pose = geometrie::Pose { x: 10.0, y: 5.0, rotation: 0.0, dessous: false };
        let t = pose.poser(&local);
        proche(t.distance_signee([11.0, 6.5]), 0.0);
        proche(t.distance_signee([11.4, 5.5]), 0.0);
        proche(t.distance_signee([12.0, 5.5]), 0.6);
        proche(t.distance_signee([11.0, 7.0]), 0.5);
        proche(t.distance_signee([11.0, 5.5]), -0.4);
        assert!(trou("ovale", 1.0, 1.0, 0.0, [0.0, 0.0]).is_err());
        // Pièce dessous à 12,5° en (10 ; 5) : local (2 ; 1) → miroir (2 ; −1) → R(12,5°) :
        // (2 cos + sin ; 2 sin − cos) = (2,1690316281 ; −0,5434167793) → monde (12,1690316281 ; 4,4565832207).
        let dessous = geometrie::Pose { x: 10.0, y: 5.0, rotation: 12.5, dessous: true };
        let m = dessous.vers_monde([2.0, 1.0]);
        proche(m[0], 12.1690316281);
        proche(m[1], 4.4565832207);
        let l = dessous.vers_local(m);
        proche(l[0], 2.0);
        proche(l[1], 1.0);
        // Pastille 2 × 1, rotation relative 30° : rotation absolue 12,5 − 30 = −17,5° ; le point à 1,5 sur son axe
        // long absolu est à 0,5 du bord, celui à 0,6 sur l'axe court à 0,1.
        proche(dessous.rotation_absolue(30.0), -17.5);
        let p = dessous.poser(&Forme::rectangle([2.0, 1.0], 2.0, 1.0, 30.0));
        let axe = |x: f64, y: f64| {
            let q = tourner([x, y], -17.5);
            [m[0] + q[0], m[1] + q[1]]
        };
        proche(p.distance_signee(axe(1.5, 0.0)), 0.5);
        proche(p.distance_signee(axe(0.0, 0.6)), 0.1);
        proche(p.distance_signee(axe(0.0, 0.0)), -0.5);
    }

    fn verdict_avec_ecart(ecart_moyen_mm: f64, coudes: u64) -> Verdict {
        Verdict {
            mode: Mode::Strict,
            valide: true,
            liaisons_ouvertes: Vec::new(),
            violations: Vec::new(),
            derogations: compteurs_a_zero(&DEROGATIONS),
            avertissements: compteurs_a_zero(&AVERTISSEMENTS),
            reseaux_a_router: 0,
            reseaux_complets: 0,
            marge_isolement_min_mm: None,
            marge_isolement_min_paire: String::new(),
            mesures: Mesures {
                ecart_moyen_mm,
                ecart_min_mm: None,
                coudes,
                coudes_90: 0,
                angles_aigus: 0,
                longueur_pistes_mm: 0.0,
                resistance_puissance_mohm: 0.0,
                vias: 0,
            },
        }
    }

    /// Formule complète de §4.7, terme d'écart compris.
    #[test]
    fn cout_commun_terme_ecart() {
        let donnees = json!({"regles": {"isolement": {"defaut": 0.2}},
                             "reseaux": [{"nom": "SIG", "role": "signal", "largeurs_mm": [0.254, 0.254, null]}]});
        // 400 mil = 10,16 mm, 10 mil = 0,254 mm (largeur préférée : pas de rétréci).
        let routage = json!({"pistes": [{"x1": 0.0, "y1": 0.0, "x2": 400.0, "y2": 0.0, "width": 10.0,
                                         "layer": "Dessus", "net_name": "SIG"}], "vias": []});
        // s = 3 × 0,2 = 0,6 : 10,16 + 0,1 × 2 + 1000 × (0,6 − 0,25) = 360,36.
        let c = cout_commun(&donnees, &routage, &verdict_avec_ecart(0.25, 2));
        assert!((c.cout - 360.36).abs() < 1e-9, "{c:?}");
        assert_eq!(c.ecart_moyen_mm, 0.25);
        // Écart moyen de 0,9 mm ≥ s : terme nul.
        let c = cout_commun(&donnees, &routage, &verdict_avec_ecart(0.9, 2));
        assert!((c.cout - 10.36).abs() < 1e-9, "{c:?}");
        // Aucune piste avec voisin (écart 0) : terme compté à son maximum, 1000 × s.
        let c = cout_commun(&donnees, &routage, &verdict_avec_ecart(0.0, 2));
        assert!((c.cout - 610.36).abs() < 1e-9, "{c:?}");
    }
}
