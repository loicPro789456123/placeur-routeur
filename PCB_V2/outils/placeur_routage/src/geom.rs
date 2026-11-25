//! Modele de la carte : pieces, pastilles, reseaux, poses, contraintes de placement (memes regles que placer_v3.py).
//! Les reglages du moteur (`parametres_moteur/1`) sont portes par le modele (champ `param`).
use crate::donnees_v1::*;
use crate::parametres::Parametres;
use std::collections::HashMap;

pub const MIL: f64 = 0.0254;
/// Pas de la grille des origines des pièces : 5 mil par défaut (comme v3), sinon `regles.placement.grille_mm`
/// des données (fixé une fois par l'adaptateur avant tout calcul).
static PAS_SNAP_BITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub fn pas_snap() -> f64 {
    let b = PAS_SNAP_BITS.load(std::sync::atomic::Ordering::Relaxed);
    if b == 0 { 5.0 * MIL } else { f64::from_bits(b) }
}
pub fn fixer_pas_snap(pas_mm: f64) {
    if pas_mm > 0.0 {
        PAS_SNAP_BITS.store(pas_mm.to_bits(), std::sync::atomic::Ordering::Relaxed);
    }
}
pub const PIECE_MARGIN: f64 = 0.25;
pub const TH_MARGIN: f64 = 0.6;
pub const DEC_LIM: f64 = 4.0;
pub const DEC_W: f64 = 25.0;
pub const SW_SOFT_W: f64 = 5.0;
pub const SW_DIST: f64 = 30.0;
/// convertisseur libere : poids des termes (mm equivalents)
pub const W_BOUCLE: f64 = 8.0; // par mm2 de boucle chaude
pub const W_PERIM: f64 = 10.0; // par mm de perimetre de boucle chaude
pub const W_SW: f64 = 15.0; // par mm de demi-perimetre du noeud de commutation
pub const W_PROCHE: f64 = 10.0; // bootstrap, decouplages d'U10, commandes de grille
pub const W_DEC_VIN: f64 = 10.0;
pub const W_BULK: f64 = 3.0;
pub const W_COMP: f64 = 40.0;
pub const D_COMP: f64 = 4.0;
/// reseaux de compensation / retour de tension d'U10 (broches 3, 4, 5)

/// Description d'un convertisseur a decoupage (fichier de configuration de carte, config_carte.json) : roles des
/// pieces et des reseaux ; rien de cela n'est code en dur dans le placeur.
#[derive(Clone, Debug, Default, serde::Deserialize)]
pub struct Convertisseur {
    #[serde(default)]
    #[allow(dead_code)]
    pub nom: String,
    /// interrupteurs haut et bas (demi-pont), pilote, inductance
    pub haut: String,
    pub bas: String,
    #[serde(default)]
    pub pilote: String,
    #[serde(default)]
    pub inductance: String,
    /// condensateurs d'entree ceramiques (boucle chaude), electrolytique d'entree, condensateur de sortie
    #[serde(default)]
    pub cond_entree: Vec<String>,
    #[serde(default)]
    pub cond_bulk: String,
    #[serde(default)]
    pub cond_sortie: String,
    pub reseau_entree: String,
    pub reseau_masse: String,
    #[serde(default)]
    pub reseau_sortie: String,
    /// noeud de commutation ; vide = deduit de la classe COMMUTATION
    #[serde(default)]
    pub reseau_commutation: String,
    /// reseaux a garder courts (bootstrap, commandes de grille, decouplages du pilote)
    #[serde(default)]
    pub reseaux_courts: Vec<String>,
    /// reseaux sensibles (compensation, retour de tension) a eloigner du noeud de commutation et de l'inductance
    #[serde(default)]
    pub reseaux_sensibles: Vec<String>,
    /// decouplage d'entree du pilote : condensateur a rapprocher de la broche d'entree du pilote
    #[serde(default)]
    pub cond_pilote: String,
    /// distances minimales entre le noeud de commutation (centre de l'interrupteur haut) et des broches sensibles
    /// ("PIECE.BROCHE")
    #[serde(default)]
    pub eloignement: Vec<Eloignement>,
}

#[derive(Clone, Debug, Default, serde::Deserialize)]
pub struct Eloignement {
    pub broches: Vec<String>,
    pub distance_mm: f64,
}

/// Convertisseurs et pieces sans piste dessous (copiees des donnees du placeur a partir de l'etape 3 du lot A ; plus
/// aucune lecture implicite d'un fichier de configuration).
#[derive(Clone, Debug, Default, serde::Deserialize)]
pub struct ConfigCarte {
    #[serde(default)]
    pub convertisseurs: Vec<Convertisseur>,
    /// pieces sous lesquelles aucune piste n'est posee sur leur face (inductances, sauf sur leurs pastilles)
    #[serde(default)]
    pub sans_piste_dessous: Vec<String>,
}

pub type Rect = [f64; 4];

pub fn snap(v: f64) -> f64 {
    (v / pas_snap()).round() * pas_snap()
}

pub fn ov(a: &Rect, b: &Rect) -> bool {
    a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]
}

/// Convention verifiee par placer_v3 (0,0000 mm) : dessous = miroir Y local puis rotation.
pub fn fwd(lx: f64, ly: f64, rot: i32, bot: bool) -> (f64, f64) {
    let (vx, vy) = if bot { (lx, -ly) } else { (lx, ly) };
    match rot {
        0 => (vx, vy),
        90 => (-vy, vx),
        180 => (-vx, -vy),
        _ => (vy, -vx),
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pose {
    pub x: f64,
    pub y: f64,
    pub rot: i32,
    pub bot: bool,
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Statut {
    Fixe,
    Bloc,
    Mobile,
}

/// Forme du cuivre d'une pastille (verite Altium, iteration 4) : rectangle, ou rond / oblong
/// (capsule d'axe le long de la plus grande dimension, rayon = demi-petite dimension).
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum FormePad {
    Rect,
    Rond,
    /// rectangle chanfreine a 45 degres (octogone d'Altium, polygone convexe) : longueur du chanfrein le long d'un
    /// axe ; le cuivre est contenu dans { rectangle } ∩ { |dx| + |dy| <= hw + hh - c }
    Octo(f64),
}

#[derive(Clone)]
pub struct Pad {
    pub nom: String,
    pub net: Option<usize>,
    pub lx: f64,
    pub ly: f64,
    pub hw: f64,
    pub hh: f64,
    pub th: bool,
    pub forme: FormePad,
    /// demi-dimensions de placement (boite de la pastille, masque compris, comme placer_v3) : zones des traversantes
    pub pw: f64,
    pub ph: f64,
    /// diametre du trou (pastille traversante ; 0 si inconnu ou CMS)
    pub trou: f64,
}

/// Distance signee d'un point au cuivre d'une pastille (centre, demi-dimensions monde) : negative a l'interieur
/// (= - profondeur).
pub fn dist_pad(px: f64, py: f64, cx: f64, cy: f64, hw: f64, hh: f64, forme: FormePad) -> f64 {
    match forme {
        FormePad::Rect => {
            let dx = (px - cx).abs() - hw;
            let dy = (py - cy).abs() - hh;
            if dx <= 0.0 && dy <= 0.0 {
                dx.max(dy)
            } else {
                (dx.max(0.0).powi(2) + dy.max(0.0).powi(2)).sqrt()
            }
        }
        FormePad::Octo(c) => {
            // max des distances signees aux demi-plans : exacte a l'interieur, minorant a l'exterieur (prudent
            // pour l'isolement)
            let dr = dist_pad(px, py, cx, cy, hw, hh, FormePad::Rect);
            let dc = ((px - cx).abs() + (py - cy).abs() - (hw + hh - c)) / std::f64::consts::SQRT_2;
            dr.max(dc)
        }
        FormePad::Rond => {
            let r = hw.min(hh);
            let (ax, ay, bx, by) = if hw >= hh { (cx - (hw - r), cy, cx + (hw - r), cy) } else { (cx, cy - (hh - r), cx, cy + (hh - r)) };
            d_pt_seg(px, py, ax, ay, bx, by) - r
        }
    }
}

/// Groupe de cuivre du bloc d'un meme reseau relie par contact reel (pastilles, pistes, vias, surfaces).
#[derive(Clone, Debug)]
pub struct GroupeBloc {
    pub net: usize,
    pub pads: Vec<(usize, usize)>,
    pub cuivre: Vec<usize>,
    /// relie au plan GND (contient un via ou une pastille traversante)
    pub plan: bool,
}

#[derive(Clone)]
pub struct Comp {
    pub nom: String,
    pub statut: Statut,
    pub lbb: Rect,
    pub pads: Vec<Pad>,
    pub orients: Vec<(i32, bool)>,
    pub has_th: bool,
    pub pose_base: Pose,
    pub pose_v3: Pose,
    pub pas_fin: bool,
    pub via_sous_pad: bool,
    /// regions de cuivre d'empreinte (locales) : (face relative 0 composant / 1 opposee / 2 toutes, reseau, polygone)
    pub regions: Vec<(u8, Option<usize>, Vec<(f64, f64)>)>,
    /// zones interdites de l'empreinte (repere local) : suivent la piece (rotation, face)
    pub zones: Vec<ZoneR>,
}

/// Zone interdite de routage (carte : repere de la carte ; empreinte : repere local de la piece). Generique : toute
/// forme (polygone, ou polyligne epaisse), par couche, avec ses effets (pistes, vias).
#[derive(Clone, Debug)]
pub struct ZoneR {
    pub polyligne: bool,
    pub pts: Vec<(f64, f64)>,
    pub largeur: f64,
    pub pistes: bool,
    pub vias: bool,
    /// interdit aussi aux autres pieces (effets « placement » ou « pastilles ») : obstacle de placement
    pub placement: bool,
    /// couches de routage du moteur (0 dessus, 1 dessous, 2 interne) touchees, piece sur sa face de reference
    pub masque: [bool; 3],
    /// idem, piece retournee par rapport a sa face de reference (empilage vu en miroir)
    pub masque_ret: [bool; 3],
    /// face de reference (pose des donnees) : dessous ?
    pub bot_ref: bool,
}

impl ZoneR {
    /// Distance d'un point a la zone posee (points deja dans le repere de la carte) : <= 0 dedans.
    pub fn dist(&self, pts: &[(f64, f64)], x: f64, y: f64) -> f64 {
        if self.polyligne {
            let mut d = f64::MAX;
            if pts.len() == 1 {
                d = ((x - pts[0].0).powi(2) + (y - pts[0].1).powi(2)).sqrt();
            }
            for k in 0..pts.len().saturating_sub(1) {
                d = d.min(d_pt_seg(x, y, pts[k].0, pts[k].1, pts[k + 1].0, pts[k + 1].1));
            }
            d - self.largeur / 2.0
        } else {
            dist_poly(x, y, pts)
        }
    }
}

/// Point d'attente au bord de l'emprise du bloc : le reseau y arrive, le dernier raccord vers la pastille
/// interieure du bloc se fait dans le cuivre existant du bloc (hors de ce logiciel).
#[derive(Clone, Debug)]
pub struct Attente {
    pub x: f64,
    pub y: f64,
    pub couche: u8,
    #[allow(dead_code)]
    pub desc: String,
}

/// Cuivre existant du bloc hacheur (lu dans Altium, cuivre_bloc_hacheur.json), en mm.
#[derive(Clone, Debug)]
pub enum CuivreObj {
    Seg { x1: f64, y1: f64, x2: f64, y2: f64, w: f64, couche: u8 },
    Via { x: f64, y: f64, d: f64 },
    Region { couche: u8, r: Rect },
}

#[derive(Clone, Debug)]
pub struct Cuivre {
    pub net: Option<usize>,
    pub obj: CuivreObj,
    /// appartient a un groupe de cuivre du bloc qui contient une pastille (sinon amorce isolee : obstacle seulement)
    pub relie: bool,
}

fn d_pt_seg(px: f64, py: f64, ax: f64, ay: f64, bx: f64, by: f64) -> f64 {
    let (dx, dy) = (bx - ax, by - ay);
    let l2 = dx * dx + dy * dy;
    let t = if l2 > 0.0 { (((px - ax) * dx + (py - ay) * dy) / l2).clamp(0.0, 1.0) } else { 0.0 };
    ((px - ax - t * dx).powi(2) + (py - ay - t * dy).powi(2)).sqrt()
}


#[derive(Clone)]
pub struct Net {
    /// un point cible par groupe de cuivre du bloc (routeur global et recuit)
    pub attentes: Vec<Attente>,
    /// GND : pastilles CMS du bloc dont le groupe n'a aucun via vers le plan (descente a poser)
    pub pins_plan_bloc: Vec<(usize, usize)>,
    /// broches hors bloc (les pastilles d'un meme reseau dans le bloc sont reliees par son cuivre)
    pub pins_ext: Vec<(usize, usize)>,
    pub nom: String,
    pub pins: Vec<(usize, usize)>,
    pub plan: bool,
    pub poids: f64,
    pub classe: String,
    pub bloc_seul: bool,
    pub touche_bloc: bool,
    /// role du reseau dans les donnees (`signal`, `alimentation`, `puissance`, `commutation`, `plan`)
    pub role: String,
}

#[derive(Clone)]
pub struct Modele {
    pub w: f64,
    pub h: f64,
    pub marge: f64,
    pub comps: Vec<Comp>,
    pub nets: Vec<Net>,
    #[cfg_attr(not(test), allow(dead_code))]
    pub net_idx: HashMap<String, usize>,
    pub comp_idx: HashMap<String, usize>,
    pub mobiles: Vec<usize>,
    pub bloc: Vec<usize>,
    pub obstacles: Vec<(u8, Rect)>,
    pub bloc_rects_base: Vec<(u8, Rect)>,
    pub emprise_base: Rect,
    #[allow(dead_code)] // contrainte du bloc mobile (bloc fige depuis l'iteration 2)
    pub colonne: [f64; 2],
    pub u25: Vec<[f64; 2]>,
    pub m1c: [f64; 2],
    pub sw_min: f64,
    pub soft: Vec<usize>,
    pub decaps: Vec<(usize, usize, Vec<(usize, usize)>)>,
    pub comp_nets: Vec<Vec<usize>>,
    pub trous: Vec<[f64; 2]>,
    /// rayon du cuivre des pastilles libres des trous M3 (verite Altium) ; isolement 0,2 mm autour
    pub trous_r: Vec<f64>,
    pub groupes_bloc: Vec<GroupeBloc>,
    pub fentes: Vec<[f64; 4]>,
    pub regles: Regles,
    pub delta_v3: [f64; 2],
    pub cuivre_bloc: Vec<Cuivre>,
    /// zones de pression (x, y, rayon, poids, reseau exempte) issues des echecs du routage detaille
    pub pression: Vec<(f64, f64, f64, f64, Option<usize>)>,
    /// bloc hacheur libere (iteration 7 bis) : ses pieces sont mobiles, son cuivre manuel n'est plus repris
    pub bloc_libre: bool,
    /// pieces du convertisseur (ancien bloc + reseau de compensation) : termes de cout dedies
    pub hacheur: Vec<usize>,
    /// pieces de puissance du convertisseur (vias GND de puissance doubles a proximite)
    pub hacheur_puissance: Vec<usize>,
    /// ponts (reseau, piece, pastille gardee, pastille pontee, largeur) entre pastilles voisines d'un meme reseau
    pub ponts: Vec<(usize, usize, usize, usize, f64)>,
    /// configuration de carte (convertisseurs, pieces sans piste dessous)
    pub cfg: ConfigCarte,
    /// contour polygonal de la carte (vide = rectangle w x h)
    pub contour: Vec<(f64, f64)>,
    /// reglages du moteur (`parametres_moteur/1`)
    pub param: Parametres,
    /// origine du repere interne dans le repere des donnees (mm) : point (0, 0) du modele
    pub origine: [f64; 2],
    /// noms des couches de routage du coeur (0 dessus, 1 dessous, 2 interne), lus dans les donnees
    pub noms_couches: [String; 3],
    /// couches de routage du coeur presentes sur la carte
    pub couches_actives: [bool; 3],
    /// couches extremes des vias traversants (premiere et derniere couche de cuivre)
    pub via_de_a: [String; 2],
    /// pose exacte des donnees (repere des donnees, mm, degres) des pieces fixes ou virtuelles, ecrite telle quelle
    pub poses_donnees: Vec<Option<(f64, f64, f64, bool)>>,
    /// zones interdites de routage de la carte (repere de la carte)
    pub zones_carte: Vec<ZoneR>,
    /// derogation des donnees : via dans une pastille CMS du meme reseau (`regles.via_dans_pastille_meme_reseau`)
    pub via_dans_pad: bool,
    /// style des pistes (detail::MODE_*) : routage.mode, sinon regles.mode_routage des donnees
    pub mode_routage: u8,
    /// groupes de longueurs appariees (paires differentielles et longueurs_appariees des donnees) : reseaux, tolerance (mm)
    pub groupes_longueur: Vec<(Vec<usize>, f64, bool)>,
    /// longueurs bornees (reseau, min, max en mm) : longueurs des donnees
    pub longueurs_bornees: Vec<(usize, f64, f64)>,
    /// paires différentielles (positif, négatif, écart bord à bord en mm) : routage couplé
    pub paires: Vec<(usize, usize, f64)>,
    /// cache des rectangles occupes par piece et orientation (rects_of), a l'origine
    pub rects_rel: Vec<Vec<Vec<(u8, Rect)>>>,
}

#[derive(Clone)]
pub struct Etat {
    pub poses: Vec<Pose>,
    pub bdx: f64,
    pub bdy: f64,
}

impl Modele {
    pub fn new(d: &Donnees, param: Parametres) -> Modele {
        let mut net_names: Vec<String> = Vec::new();
        for c in &d.composants {
            for p in &c.pastilles {
                if !p.reseau.is_empty() {
                    net_names.push(p.reseau.clone());
                }
            }
        }
        net_names.sort();
        net_names.dedup();
        let net_idx: HashMap<String, usize> = net_names.iter().enumerate().map(|(i, n)| (n.clone(), i)).collect();
        let fin: Vec<&String> = d.regles.isolement.pieces_pas_fin.iter().collect();
        let mut comps = Vec::new();
        for c in &d.composants {
            let st = match c.statut.as_str() {
                "fixe" => Statut::Fixe,
                "bloc" => Statut::Bloc,
                _ => Statut::Mobile,
            };
            let pads: Vec<Pad> = c
                .pastilles
                .iter()
                .map(|p| Pad {
                    nom: p.nom.clone(),
                    net: if p.reseau.is_empty() { None } else { Some(net_idx[&p.reseau]) },
                    lx: p.lx,
                    ly: p.ly,
                    hw: p.hw,
                    hh: p.hh,
                    th: p.th,
                    forme: FormePad::Rect,
                    pw: p.hw,
                    ph: p.hh,
                    trou: 0.0,
                })
                .collect();
            let has_th = pads.iter().any(|p| p.th);
            let pa = c.pose_actuelle;
            let pv = c.pose_cible;
            comps.push(Comp {
                nom: c.nom.clone(),
                statut: st,
                lbb: c.lbb,
                pads,
                orients: c.orientations.clone(),
                has_th,
                pose_base: Pose { x: pa.0, y: pa.1, rot: pa.2, bot: pa.3 },
                pose_v3: Pose { x: pv.0, y: pv.1, rot: pv.2, bot: pv.3 },
                pas_fin: fin.iter().any(|f| **f == c.nom),
                via_sous_pad: d.regles.via.via_sous_cms_permis.iter().any(|f| *f == c.nom),
                regions: Vec::new(),
                zones: Vec::new(),
            });
        }
        let comp_idx: HashMap<String, usize> = comps.iter().enumerate().map(|(i, c)| (c.nom.clone(), i)).collect();
        let mut nets: Vec<Net> = net_names
            .iter()
            .map(|n| Net {
                attentes: Vec::new(),
                pins_plan_bloc: Vec::new(),
                pins_ext: Vec::new(),
                nom: n.clone(),
                pins: Vec::new(),
                plan: d.reseaux_plans.contains(n),
                poids: *d.poids_reseaux.get(n).unwrap_or(&1.0),
                classe: d.classes.get(n).cloned().unwrap_or_else(|| "defaut".to_string()),
                bloc_seul: false,
                touche_bloc: false,
                role: if d.reseaux_plans.contains(n) { "plan".to_string() } else { "signal".to_string() },
            })
            .collect();
        let mut comp_nets = vec![Vec::new(); comps.len()];
        for (i, c) in comps.iter().enumerate() {
            for (k, p) in c.pads.iter().enumerate() {
                if let Some(n) = p.net {
                    nets[n].pins.push((i, k));
                    comp_nets[i].push(n);
                }
            }
            comp_nets[i].sort();
            comp_nets[i].dedup();
        }
        for n in nets.iter_mut() {
            n.bloc_seul = n.pins.iter().all(|&(i, _)| comps[i].statut == Statut::Bloc);
            n.touche_bloc = n.pins.iter().any(|&(i, _)| comps[i].statut == Statut::Bloc);
        }
        for n in nets.iter_mut() {
            n.pins_ext = n.pins.iter().copied().filter(|&(i, _)| comps[i].statut != Statut::Bloc).collect();
        }
        let mobiles = (0..comps.len()).filter(|&i| comps[i].statut == Statut::Mobile).collect();
        let bloc = (0..comps.len()).filter(|&i| comps[i].statut == Statut::Bloc).collect();
        let decaps = d
            .decouplages
            .iter()
            .map(|x| (comp_idx[&x.comp], x.pastille, x.broches_ci.iter().map(|(c, k)| (comp_idx[c], *k)).collect()))
            .collect();
        let mut m = Modele {
            w: d.contour.w,
            h: d.contour.h,
            marge: d.marge_bord,
            comps,
            nets,
            net_idx,
            mobiles,
            bloc,
            obstacles: d.obstacles_placement.iter().map(|o| (o.face, o.rect)).collect(),
            bloc_rects_base: d.bloc.rects_base.iter().map(|o| (o.face, o.rect)).collect(),
            emprise_base: d.bloc.emprise_base,
            colonne: d.bloc.colonne,
            u25: d.bloc.broches_u25.clone(),
            m1c: d.bloc.m1_centre_base,
            sw_min: d.bloc.dist_sw_min,
            soft: d.soft_sw.iter().map(|n| comp_idx[n]).collect(),
            decaps,
            comp_nets,
            comp_idx,
            trous: d.trous_m3.clone(),
            trous_r: vec![d.regles.rayon_trou_m3; d.trous_m3.len()],
            groupes_bloc: Vec::new(),
            fentes: d.fentes.clone(),
            regles: d.regles.clone(),
            delta_v3: d.bloc.delta_v3,
            cuivre_bloc: Vec::new(),
            pression: Vec::new(),
            bloc_libre: false,
            hacheur: Vec::new(),
            hacheur_puissance: Vec::new(),
            ponts: Vec::new(),
            cfg: ConfigCarte::default(),
            contour: d.contour.points.iter().map(|p| (p[0], p[1])).collect(),
            param,
            origine: [0.0, 0.0],
            noms_couches: ["TopLayer".to_string(), "BottomLayer".to_string(), "MidLayer2".to_string()],
            couches_actives: [true; 3],
            via_de_a: ["TopLayer".to_string(), "BottomLayer".to_string()],
            poses_donnees: Vec::new(),
            zones_carte: Vec::new(),
            via_dans_pad: false,
            mode_routage: 0,
            groupes_longueur: Vec::new(),
            longueurs_bornees: Vec::new(),
            paires: Vec::new(),
            rects_rel: Vec::new(),
        };
        m.calculer_attentes();
        m
    }

    /// Libere le bloc hacheur : pieces mobiles (4 rotations sur leur face d'origine), plus d'emprise reservee,
    /// plus de cuivre manuel (a appeler AVANT tout chargement de cuivre_bloc_hacheur.json), tous les reseaux du bloc
    /// sont routes comme les autres (y compris NetC18_2, NetC19_1, NetC19_2, NetM1_4, jusque-la internes).
    pub fn liberer_bloc(&mut self) {
        let anciens = self.bloc.clone();
        for &i in &anciens {
            let c = &mut self.comps[i];
            c.statut = Statut::Mobile;
            let bot = c.pose_v3.bot;
            if c.orients.is_empty() || c.orients.len() == 1 {
                c.orients = vec![(0, bot), (90, bot), (180, bot), (270, bot)];
            }
            self.mobiles.push(i);
        }
        self.mobiles.sort();
        self.bloc.clear();
        self.bloc_rects_base.clear();
        self.cuivre_bloc.clear();
        self.groupes_bloc.clear();
        for n in self.nets.iter_mut() {
            n.bloc_seul = false;
            n.touche_bloc = false;
            n.pins_ext = n.pins.clone();
            n.attentes.clear();
            n.pins_plan_bloc.clear();
        }
        self.bloc_libre = true;
        // pieces concernees par les termes des convertisseurs (configuration de carte) : leurs pieces nommees + les
        // pieces des reseaux sensibles
        let mut h = anciens.clone();
        let mut puiss = Vec::new();
        for cv in self.cfg.convertisseurs.clone() {
            for nom in [&cv.haut, &cv.bas, &cv.pilote, &cv.inductance, &cv.cond_bulk, &cv.cond_sortie, &cv.cond_pilote].into_iter().chain(cv.cond_entree.iter()) {
                if let Some(&i) = self.comp_idx.get(nom.as_str()) {
                    if !h.contains(&i) && self.comps[i].statut == Statut::Mobile {
                        h.push(i);
                    }
                }
            }
            for nom in [&cv.haut, &cv.bas, &cv.inductance, &cv.cond_bulk, &cv.cond_sortie].into_iter().chain(cv.cond_entree.iter()) {
                if let Some(&i) = self.comp_idx.get(nom.as_str()) {
                    puiss.push(i);
                }
            }
            for nom in cv.reseaux_sensibles.iter().chain(cv.reseaux_courts.iter()) {
                if let Some(&n) = self.net_idx.get(nom.as_str()) {
                    for &(i, _) in &self.nets[n].pins {
                        if !h.contains(&i) && self.comps[i].statut == Statut::Mobile {
                            h.push(i);
                        }
                    }
                }
            }
        }
        self.hacheur = h;
        self.hacheur_puissance = puiss;
        // ponts (generique, toute piece) : pastilles CMS voisines (bord a bord < 0,5 mm, alignees) d'un meme reseau,
        // reliees par une piste de 0,3 mm entre leurs centres ; la pastille pontee sort des broches a router
        for i in 0..self.comps.len() {
            if self.comps[i].statut != Statut::Mobile {
                continue;
            }
            let pv = self.comps[i].pose_v3;
            let np = self.comps[i].pads.len();
            // ordre canonique des pastilles (nom au sens naturel, puis position locale) : le resultat ne depend pas de
            // l'ordre des pastilles dans les donnees (l'ancien balayage glouton, dans l'ordre du fichier, laissait des
            // pastilles voisines non pontees selon cet ordre : broches de plus a router sous les boitiers a pas fin)
            let cle = |p: &Pad| (p.nom.parse::<u64>().unwrap_or(u64::MAX), p.nom.clone(), (p.lx * 1e4).round() as i64, (p.ly * 1e4).round() as i64);
            let mut ordre: Vec<usize> = (0..np).collect();
            ordre.sort_by_key(|&k| cle(&self.comps[i].pads[k]));
            // aretes voisines (memes regles qu'avant), puis arbre couvrant de chaque groupe de pastilles reliees
            let mut chef: Vec<usize> = (0..np).collect();
            fn racine(c: &mut Vec<usize>, x: usize) -> usize {
                let mut x = x;
                while c[x] != x {
                    c[x] = c[c[x]];
                    x = c[x];
                }
                x
            }
            let mut aretes: Vec<(usize, usize, usize)> = Vec::new();
            for a1 in 0..np {
                for a2 in (a1 + 1)..np {
                    let (k1, k2) = (ordre[a1], ordre[a2]);
                    let (a, b) = (&self.comps[i].pads[k1], &self.comps[i].pads[k2]);
                    if a.net.is_none() || a.net != b.net || a.th || b.th || a.nom == b.nom {
                        continue;
                    }
                    let n = a.net.unwrap();
                    if self.nets[n].plan && !self.param.routage.ponts_plans && !self.hacheur_puissance.contains(&i) {
                        continue;
                    }
                    let (x1, y1, w1, h1) = self.pad_world(i, k1, &pv);
                    let (x2, y2, w2, h2) = self.pad_world(i, k2, &pv);
                    let gx = (x2 - x1).abs() - w1 - w2;
                    let gy = (y2 - y1).abs() - h1 - h2;
                    let voisin = (gx.max(gy) < 0.5) && (gx.min(gy) < 0.0);
                    if !voisin {
                        continue;
                    }
                    let (r1, r2) = (racine(&mut chef, a1), racine(&mut chef, a2));
                    if r1 != r2 {
                        // la racine d'un groupe est sa pastille de plus petit rang canonique
                        chef[r1.max(r2)] = r1.min(r2);
                        aretes.push((n, a1, a2));
                    }
                }
            }
            // orientation depuis la racine : chaque pastille pontee l'est une fois, depuis une pastille deja reliee
            let mut relie: Vec<bool> = (0..np).map(|a| racine(&mut chef, a) == a).collect();
            let mut restantes = aretes;
            while !restantes.is_empty() {
                let avant = restantes.len();
                restantes.retain(|&(n, a1, a2)| {
                    let (garde, pontee) = if relie[a1] && !relie[a2] {
                        (a1, a2)
                    } else if relie[a2] && !relie[a1] {
                        (a2, a1)
                    } else {
                        return true;
                    };
                    relie[pontee] = true;
                    let wmin = self.regles.largeurs.get(&self.nets[n].classe).or_else(|| self.regles.largeurs.get("defaut")).map_or(0.0, |l| l[0]);
                    self.ponts.push((n, i, ordre[garde], ordre[pontee], wmin.max(0.3)));
                    false
                });
                if restantes.len() == avant {
                    break;
                }
            }
        }
        for &(n, i, _, k2, _) in &self.ponts.clone() {
            self.nets[n].pins.retain(|&(a, b)| !(a == i && b == k2));
            self.nets[n].pins_ext.retain(|&(a, b)| !(a == i && b == k2));
        }
    }

    /// Regions de cuivre d'empreinte : depuis les donnees (champ `regions` de chaque composant) ou un fichier
    /// {"PIECE": [regions]} au meme format (donnee d'essai). Renvoie le nombre de regions lues.
    pub fn charger_regions(&mut self, par_piece: &[(String, Vec<RegionIn>)]) -> usize {
        let mut n = 0;
        for (nom, rs) in par_piece {
            let i = match self.comp_idx.get(nom) {
                Some(&i) => i,
                None => continue,
            };
            for r in rs {
                let net = match (&r.pastille, &r.reseau) {
                    (Some(p), _) => self.comps[i].pads.iter().find(|q| q.nom == *p).and_then(|q| q.net),
                    (None, Some(nn)) => self.net_idx.get(nn).copied(),
                    _ => None,
                };
                let face = match r.face.as_str() {
                    "opposee" => 1,
                    "toutes" => 2,
                    _ => 0,
                };
                self.comps[i].regions.push((face, net, r.points.iter().map(|p| (p[0], p[1])).collect()));
                n += 1;
            }
        }
        n
    }

    /// Zone interdite d'une piece posee : points dans le repere de la carte et couches de routage touchees.
    pub fn zone_monde(&self, z: &ZoneR, p: &Pose) -> (Vec<(f64, f64)>, [bool; 3]) {
        let pts = z.pts.iter().map(|&(lx, ly)| {
            let (dx, dy) = fwd(lx, ly, p.rot, p.bot);
            (p.x + dx, p.y + dy)
        }).collect();
        (pts, if p.bot == z.bot_ref { z.masque } else { z.masque_ret })
    }

    /// Polygone monde d'une region et couches de cuivre concernees.
    pub fn region_monde(&self, i: usize, r: &(u8, Option<usize>, Vec<(f64, f64)>), p: &Pose) -> (Vec<(f64, f64)>, Vec<usize>) {
        let pts = r.2.iter().map(|&(lx, ly)| {
            let (dx, dy) = fwd(lx, ly, p.rot, p.bot);
            (p.x + dx, p.y + dy)
        }).collect();
        let face = if p.bot { 1 } else { 0 };
        let l = match r.0 {
            0 => vec![face],
            1 => vec![1 - face],
            _ => vec![0, 1, 2],
        };
        let _ = i;
        (pts, l)
    }

    /// Centre des pastilles d'une piece sur un reseau (None si aucune).
    fn centre_net(&self, i: usize, net: &str, e: &Etat) -> Option<(f64, f64)> {
        let n = *self.net_idx.get(net)?;
        let (mut sx, mut sy, mut c) = (0.0, 0.0, 0.0);
        for k in 0..self.comps[i].pads.len() {
            if self.comps[i].pads[k].net == Some(n) {
                let (x, y, _, _) = self.pad_world(i, k, &e.poses[i]);
                sx += x;
                sy += y;
                c += 1.0;
            }
        }
        if c > 0.0 {
            Some((sx / c, sy / c))
        } else {
            None
        }
    }

    fn hpwl_nom(&self, net: &str, e: &Etat) -> f64 {
        match self.net_idx.get(net) {
            Some(&n) => self.hpwl_net(n, e),
            None => 0.0,
        }
    }

    /// Noeud de commutation d'un convertisseur : reseau nomme, sinon le reseau de classe COMMUTATION de son
    /// interrupteur haut.
    pub fn reseau_commutation(&self, cv: &Convertisseur) -> Option<usize> {
        if !cv.reseau_commutation.is_empty() {
            return self.net_idx.get(&cv.reseau_commutation).copied();
        }
        let h = *self.comp_idx.get(&cv.haut)?;
        self.comp_nets[h].iter().copied().find(|&n| self.nets[n].classe == "COMMUTATION")
    }

    /// Boucle chaude d'un convertisseur : condensateur d'entree (entree) -> haut (entree) -> haut (commutation) ->
    /// bas (commutation) -> bas (masse) -> condensateur (masse), polygone par la formule du lacet ; meilleur
    /// condensateur d'entree. (surface mm2, perimetre mm) ; le perimetre empeche une boucle aplatie mais longue de
    /// paraitre bonne.
    pub fn boucle_chaude_cv(&self, cv: &Convertisseur, e: &Etat) -> Option<(f64, f64)> {
        let (m1, m2) = (*self.comp_idx.get(&cv.haut)?, *self.comp_idx.get(&cv.bas)?);
        let sw = self.nets[self.reseau_commutation(cv)?].nom.clone();
        let a = self.centre_net(m1, &cv.reseau_entree, e)?;
        let b = self.centre_net(m1, &sw, e)?;
        let c = self.centre_net(m2, &sw, e)?;
        let d = self.centre_net(m2, &cv.reseau_masse, e)?;
        let mut best: Option<(f64, f64)> = None;
        for cap in &cv.cond_entree {
            let ci = match self.comp_idx.get(cap) {
                Some(&x) => x,
                None => continue,
            };
            let (p, g) = match (self.centre_net(ci, &cv.reseau_entree, e), self.centre_net(ci, &cv.reseau_masse, e)) {
                (Some(p), Some(g)) => (p, g),
                _ => continue,
            };
            let poly = [p, a, b, c, d, g];
            let mut s = 0.0;
            let mut per = 0.0;
            for k in 0..poly.len() {
                let (x1, y1) = poly[k];
                let (x2, y2) = poly[(k + 1) % poly.len()];
                s += x1 * y2 - x2 * y1;
                per += ((x2 - x1).powi(2) + (y2 - y1).powi(2)).sqrt();
            }
            let ar = s.abs() / 2.0;
            best = Some(match best {
                Some((a0, p0)) if a0 + W_PERIM / W_BOUCLE * p0 <= ar + W_PERIM / W_BOUCLE * per => (a0, p0),
                _ => (ar, per),
            });
        }
        best
    }

    /// Noeud de commutation du premier convertisseur (journal).
    pub fn sw_net(&self) -> Option<usize> {
        self.cfg.convertisseurs.first().and_then(|cv| self.reseau_commutation(cv))
    }

    /// Boucle chaude du premier convertisseur de la configuration (journal).
    pub fn boucle_chaude_ap(&self, e: &Etat) -> Option<(f64, f64)> {
        self.cfg.convertisseurs.first().and_then(|cv| self.boucle_chaude_cv(cv, e))
    }

    /// Surface seule de la boucle chaude (mm2).
    pub fn boucle_chaude(&self, e: &Etat) -> Option<f64> {
        self.boucle_chaude_ap(e).map(|x| x.0)
    }

    /// Termes de cout des convertisseurs (mm equivalents), d'apres la configuration de carte.
    pub fn cout_hacheur(&self, e: &Etat) -> f64 {
        let mut c = 0.0;
        let ci = |n: &str| self.comp_idx.get(n).copied();
        let dist = |p: (f64, f64), q: (f64, f64)| ((p.0 - q.0).powi(2) + (p.1 - q.1).powi(2)).sqrt();
        for cv in &self.cfg.convertisseurs {
            if let Some((a, p)) = self.boucle_chaude_cv(cv, e) {
                c += W_BOUCLE * a + W_PERIM * p;
            }
            let sw = self.reseau_commutation(cv);
            if let Some(n) = sw {
                c += W_SW * self.hpwl_net(n, e);
            }
            for n in &cv.reseaux_courts {
                c += W_PROCHE * self.hpwl_nom(n, e);
            }
            if let (Some(u), Some(cp)) = (ci(&cv.pilote), ci(&cv.cond_pilote)) {
                if let (Some(p), Some(q)) = (self.centre_net(u, &cv.reseau_entree, e), self.centre_net(cp, &cv.reseau_entree, e)) {
                    c += W_DEC_VIN * dist(p, q);
                }
            }
            if let (Some(cb), Some(h)) = (ci(&cv.cond_bulk), ci(&cv.haut)) {
                if let (Some(p), Some(q)) = (self.centre_net(cb, &cv.reseau_entree, e), self.centre_net(h, &cv.reseau_entree, e)) {
                    c += W_BULK * dist(p, q);
                }
            }
            if let (Some(cs), Some(l)) = (ci(&cv.cond_sortie), ci(&cv.inductance)) {
                if let (Some(p), Some(q)) = (self.centre_net(cs, &cv.reseau_sortie, e), self.centre_net(l, &cv.reseau_sortie, e)) {
                    c += W_BULK * dist(p, q);
                }
            }
            // reseaux sensibles loin du noeud de commutation et de l'inductance
            let sw = match sw {
                Some(n) => n,
                None => continue,
            };
            let mut sw_pts: Vec<(f64, f64)> = self.nets[sw].pins.iter().map(|&(i, k)| {
                let (x, y, _, _) = self.pad_world(i, k, &e.poses[i]);
                (x, y)
            }).collect();
            if let Some(l) = ci(&cv.inductance) {
                sw_pts.push(self.centre(l, &e.poses[l]));
            }
            let pilote = ci(&cv.pilote);
            for nom in &cv.reseaux_sensibles {
                if let Some(&n) = self.net_idx.get(nom) {
                    for &(i, k) in &self.nets[n].pins {
                        if Some(i) == pilote {
                            continue;
                        }
                        let (x, y, _, _) = self.pad_world(i, k, &e.poses[i]);
                        let d = sw_pts.iter().map(|p| dist(*p, (x, y))).fold(f64::MAX, f64::min);
                        if d < D_COMP {
                            c += W_COMP * (D_COMP - d);
                        }
                    }
                }
            }
        }
        // distances minimales du noeud de commutation aux broches sensibles (configuration de carte ; sinon ancienne
        // donnee du plan, broches fixes et distance)
        for cv in &self.cfg.convertisseurs {
            let h = match ci(&cv.haut) {
                Some(h) => h,
                None => continue,
            };
            let (sx, sy) = self.centre(h, &e.poses[h]);
            for el in &cv.eloignement {
                for b in &el.broches {
                    let (pc, pk) = match b.rsplit_once('.') {
                        Some(x) => x,
                        None => continue,
                    };
                    let i = match ci(pc) {
                        Some(i) => i,
                        None => continue,
                    };
                    if let Some(k) = self.comps[i].pads.iter().position(|q| q.nom == pk) {
                        let (x, y, _, _) = self.pad_world(i, k, &e.poses[i]);
                        let dd = dist((x, y), (sx, sy));
                        if dd < el.distance_mm + 1.0 {
                            c += 300.0 * (el.distance_mm + 1.0 - dd);
                        }
                    }
                }
            }
        }
        if self.cfg.convertisseurs.iter().all(|cv| cv.eloignement.is_empty()) && !self.u25.is_empty() {
            let (sx, sy) = self.sw_node(e);
            let dmin = self.u25.iter().map(|p| ((p[0] - sx).powi(2) + (p[1] - sy).powi(2)).sqrt()).fold(f64::MAX, f64::min);
            if dmin < self.sw_min + 1.0 {
                c += 300.0 * (self.sw_min + 1.0 - dmin);
            }
        }
        c
    }

    /// Lecture du cuivre existant du bloc (mils -> mm). Format : voir le champ _doc du fichier.
    pub fn charger_cuivre_bloc(&mut self, chemin: &str) -> Result<usize, String> {
        let txt = std::fs::read_to_string(chemin).map_err(|e| format!("lecture {} : {}", chemin, e))?;
        let v: serde_json::Value = serde_json::from_str(&txt).map_err(|e| format!("JSON {} : {}", chemin, e))?;
        let f = |x: &serde_json::Value| x.as_f64().unwrap_or(0.0) * MIL;
        let couche = |x: &serde_json::Value| if x.as_str() == Some("B") { 1u8 } else { 0u8 };
        let net = |x: &serde_json::Value, m: &Modele| x.as_str().and_then(|s| m.net_idx.get(s).copied());
        let mut out = Vec::new();
        for p in v["pistes"].as_array().cloned().unwrap_or_default() {
            out.push(Cuivre { relie: false, net: net(&p[6], self), obj: CuivreObj::Seg { x1: f(&p[0]), y1: f(&p[1]), x2: f(&p[2]), y2: f(&p[3]), w: f(&p[4]), couche: couche(&p[5]) } });
        }
        for p in v["vias"].as_array().cloned().unwrap_or_default() {
            out.push(Cuivre { relie: false, net: net(&p[4], self), obj: CuivreObj::Via { x: f(&p[0]), y: f(&p[1]), d: f(&p[2]) } });
        }
        for p in v["regions"].as_array().cloned().unwrap_or_default() {
            out.push(Cuivre { relie: false, net: net(&p[1], self), obj: CuivreObj::Region { couche: couche(&p[0]), r: [f(&p[2]), f(&p[3]), f(&p[4]), f(&p[5])] } });
        }
        let n = out.len();
        self.cuivre_bloc = out;
        self.marquer_relies();
        Ok(n)
    }

    /// Geometrie reelle des pastilles (pastilles_reelles.json, tire d'Altium) : formes et dimensions du cuivre,
    /// pastilles libres des trous M3.
    pub fn charger_pastilles_reelles(&mut self, chemin: &str) -> Result<usize, String> {
        let txt = std::fs::read_to_string(chemin).map_err(|e| format!("lecture {} : {}", chemin, e))?;
        let v: serde_json::Value = serde_json::from_str(&txt).map_err(|e| format!("JSON {} : {}", chemin, e))?;
        let mut n = 0;
        for c in self.comps.iter_mut() {
            let l = v["pieces"][&c.nom].as_array().ok_or(format!("piece {} absente de {}", c.nom, chemin))?;
            if l.len() != c.pads.len() {
                return Err(format!("nombre de pastilles different pour {}", c.nom));
            }
            for (p, q) in c.pads.iter_mut().zip(l.iter()) {
                p.hw = q["hw"].as_f64().unwrap();
                p.hh = q["hh"].as_f64().unwrap();
                p.th = q["th"].as_bool().unwrap();
                p.trou = q["trou"].as_f64().unwrap_or(0.0);
                p.forme = if q["forme"].as_str() == Some("rect") { FormePad::Rect } else { FormePad::Rond };
                n += 1;
            }
        }
        let libres = v["libres"].as_array().cloned().unwrap_or_default();
        if !libres.is_empty() {
            self.trous = libres.iter().map(|q| [q["x"].as_f64().unwrap(), q["y"].as_f64().unwrap()]).collect();
            self.trous_r = libres.iter().map(|q| q["d"].as_f64().unwrap() / 2.0).collect();
        }
        Ok(n)
    }

    /// Groupes de cuivre du bloc par reseau (contact reel au sens d'Altium : recouvrement de cuivre ; les surfaces,
    /// connues par leur rectangle englobant, relient ce qu'elles recouvrent). Calcule les points cibles du routeur
    /// global, les pastilles GND du bloc a descendre vers le plan.
    fn marquer_relies(&mut self) {
        let e = self.etat_v3();
        #[derive(Clone)]
        enum G {
            Pad(f64, f64, f64, f64, FormePad, Vec<u8>),
            Seg(f64, f64, f64, f64, f64, u8),
            Via(f64, f64, f64),
            Reg(Rect, u8),
        }
        let couches = |g: &G| -> Vec<u8> {
            match g {
                G::Pad(.., l) => l.clone(),
                G::Seg(.., l) => vec![*l],
                G::Via(..) => vec![0, 1, 2],
                G::Reg(_, l) => vec![*l],
            }
        };
        // distance d'un point a l'element (0 dedans)
        let d_pt = |g: &G, x: f64, y: f64| -> f64 {
            match g {
                G::Pad(cx, cy, hw, hh, f, _) => dist_pad(x, y, *cx, *cy, *hw, *hh, *f).max(0.0),
                G::Seg(x1, y1, x2, y2, w, _) => (d_pt_seg(x, y, *x1, *y1, *x2, *y2) - w / 2.0).max(0.0),
                G::Via(vx, vy, r) => (((x - vx).powi(2) + (y - vy).powi(2)).sqrt() - r).max(0.0),
                G::Reg(r, _) => {
                    let dx = (r[0] - x).max(0.0).max(x - r[2]);
                    let dy = (r[1] - y).max(0.0).max(y - r[3]);
                    (dx * dx + dy * dy).sqrt()
                }
            }
        };
        // points d'echantillonnage (avec rayon) d'un element
        let echant = |g: &G| -> Vec<(f64, f64, f64)> {
            match g {
                G::Pad(cx, cy, hw, hh, _, _) => {
                    let mut v = Vec::new();
                    for i in 0..=8 {
                        for j in 0..=8 {
                            v.push((cx - hw + 2.0 * hw * i as f64 / 8.0, cy - hh + 2.0 * hh * j as f64 / 8.0, 0.0));
                        }
                    }
                    v
                }
                G::Seg(x1, y1, x2, y2, w, _) => {
                    let l = ((x2 - x1).powi(2) + (y2 - y1).powi(2)).sqrt();
                    let k = ((l / 0.02).ceil() as usize).max(1);
                    (0..=k).map(|t| (x1 + (x2 - x1) * t as f64 / k as f64, y1 + (y2 - y1) * t as f64 / k as f64, w / 2.0)).collect()
                }
                G::Via(x, y, r) => vec![(*x, *y, *r)],
                G::Reg(r, _) => {
                    let mut v = Vec::new();
                    for i in 0..=10 {
                        for j in 0..=10 {
                            v.push((r[0] + (r[2] - r[0]) * i as f64 / 10.0, r[1] + (r[3] - r[1]) * j as f64 / 10.0, 0.0));
                        }
                    }
                    v
                }
            }
        };
        let contact = |a: &G, b: &G| -> bool {
            let la = couches(a);
            if !couches(b).iter().any(|l| la.contains(l)) {
                return false;
            }
            // pastille contre pastille (pastilles superposees d'une empreinte) : echantillons
            echant(a).iter().any(|&(x, y, r)| d_pt(b, x, y) <= r + 1e-6) || echant(b).iter().any(|&(x, y, r)| d_pt(a, x, y) <= r + 1e-6)
        };
        for n in self.nets.iter_mut() {
            n.attentes.clear();
            n.pins_plan_bloc.clear();
        }
        let mut groupes = Vec::new();
        for ni in 0..self.nets.len() {
            if !self.nets[ni].touche_bloc {
                continue;
            }
            let mut el: Vec<(G, Option<(usize, usize)>, Option<usize>)> = Vec::new();
            for &(i, k) in &self.nets[ni].pins {
                if self.comps[i].statut != Statut::Bloc {
                    continue;
                }
                let p = e.poses[i];
                let (x, y, hw, hh) = self.pad_world(i, k, &p);
                let pd = &self.comps[i].pads[k];
                let l: Vec<u8> = if pd.th { vec![0, 1, 2] } else if p.bot { vec![1] } else { vec![0] };
                el.push((G::Pad(x, y, hw, hh, pd.forme, l), Some((i, k)), None));
            }
            for (q, c) in self.cuivre_bloc.iter().enumerate() {
                if c.net != Some(ni) {
                    continue;
                }
                let g = match c.obj {
                    CuivreObj::Seg { x1, y1, x2, y2, w, couche } => G::Seg(x1, y1, x2, y2, w, couche),
                    CuivreObj::Via { x, y, d } => G::Via(x, y, d / 2.0),
                    CuivreObj::Region { couche, r } => G::Reg(r, couche),
                };
                el.push((g, None, Some(q)));
            }
            let nel = el.len();
            let mut par: Vec<usize> = (0..nel).collect();
            fn f(par: &mut Vec<usize>, x: usize) -> usize {
                let mut x = x;
                while par[x] != x {
                    par[x] = par[par[x]];
                    x = par[x];
                }
                x
            }
            for a in 0..nel {
                for b in (a + 1)..nel {
                    if contact(&el[a].0, &el[b].0) {
                        let (ra, rb) = (f(&mut par, a), f(&mut par, b));
                        if ra != rb {
                            par[ra] = rb;
                        }
                    }
                }
            }
            let mut idx: HashMap<usize, usize> = HashMap::new();
            let base = groupes.len();
            for a in 0..nel {
                let r = f(&mut par, a);
                let gi = *idx.entry(r).or_insert_with(|| {
                    groupes.push(GroupeBloc { net: ni, pads: Vec::new(), cuivre: Vec::new(), plan: false });
                    groupes.len() - 1
                });
                if let Some(pk) = el[a].1 {
                    groupes[gi].pads.push(pk);
                    if self.comps[pk.0].pads[pk.1].th {
                        groupes[gi].plan = true;
                    }
                }
                if let Some(q) = el[a].2 {
                    groupes[gi].cuivre.push(q);
                    if matches!(self.cuivre_bloc[q].obj, CuivreObj::Via { .. }) {
                        groupes[gi].plan = true;
                    }
                }
            }
            // groupes sans pastille : cuivre isole (amorce posee a la main) -> obstacle seulement
            for g in &groupes[base..] {
                let relie = !g.pads.is_empty();
                for &q in &g.cuivre {
                    self.cuivre_bloc[q].relie = relie;
                }
            }
        }
        // GND : pastilles CMS des groupes sans via ni pastille traversante -> descente a poser
        for g in &groupes {
            if self.nets[g.net].plan && !g.plan && !g.pads.is_empty() {
                for &(i, k) in &g.pads {
                    if !self.comps[i].pads[k].th {
                        self.nets[g.net].pins_plan_bloc.push((i, k));
                    }
                }
            }
        }
        self.groupes_bloc = groupes;
        self.calculer_attentes();
    }

    /// Cuivre du bloc non relie a une pastille (amorces isolees) : (reseau, description).
    pub fn cuivre_isole(&self) -> Vec<String> {
        self.cuivre_bloc
            .iter()
            .filter(|c| !c.relie && c.net.is_some())
            .map(|c| format!("{} : {:?}", self.nets[c.net.unwrap()].nom, c.obj))
            .collect()
    }

    /// Groupes de cuivre du bloc (avec pastille) d'un reseau de signal.
    pub fn groupes_de(&self, n: usize) -> Vec<usize> {
        (0..self.groupes_bloc.len()).filter(|&g| self.groupes_bloc[g].net == n && !self.groupes_bloc[g].pads.is_empty()).collect()
    }

    /// Point cible de chaque groupe du bloc d'un reseau de signal (routeur global, recuit) : l'element le plus
    /// proche du barycentre des broches exterieures.
    fn calculer_attentes(&mut self) {
        let e = self.etat_v3();
        for ni in 0..self.nets.len() {
            self.nets[ni].attentes.clear();
            let n = &self.nets[ni];
            if n.plan || !n.touche_bloc {
                continue;
            }
            let (mut gx, mut gy, mut c) = (0.0, 0.0, 0.0);
            for &(i, k) in &n.pins_ext {
                let (x, y, _, _) = self.pad_world(i, k, &e.poses[i]);
                gx += x;
                gy += y;
                c += 1.0;
            }
            let groupes = self.groupes_de(ni);
            if c == 0.0 && groupes.len() < 2 {
                continue;
            }
            let mut att = Vec::new();
            for g in groupes {
                let gr = &self.groupes_bloc[g];
                let mut cands: Vec<(f64, f64, u8, String)> = Vec::new();
                for &(i, k) in &gr.pads {
                    let p = e.poses[i];
                    let (x, y, _, _) = self.pad_world(i, k, &p);
                    let couche = if self.comps[i].pads[k].th || !p.bot { 0 } else { 1 };
                    cands.push((x, y, couche, format!("{}.{}", self.comps[i].nom, self.comps[i].pads[k].nom)));
                }
                for &q in &gr.cuivre {
                    match &self.cuivre_bloc[q].obj {
                        CuivreObj::Seg { x1, y1, x2, y2, couche, .. } => {
                            cands.push((*x1, *y1, *couche, "piste du bloc".into()));
                            cands.push((*x2, *y2, *couche, "piste du bloc".into()));
                        }
                        CuivreObj::Via { x, y, .. } => cands.push((*x, *y, 0, "via du bloc".into())),
                        CuivreObj::Region { .. } => {}
                    }
                }
                let (rx, ry) = if c > 0.0 { (gx / c, gy / c) } else { (cands[0].0, cands[0].1) };
                if let Some(b) = cands.into_iter().min_by(|a, b| {
                    ((a.0 - rx).abs() + (a.1 - ry).abs()).partial_cmp(&((b.0 - rx).abs() + (b.1 - ry).abs())).unwrap()
                }) {
                    att.push(Attente { x: b.0, y: b.1, couche: b.2, desc: b.3 });
                }
            }
            self.nets[ni].attentes = att;
        }
    }

    /// Penalite de pression d'une piece : poids de chaque zone de pression contenant une de ses pastilles
    /// (les pieces du reseau en echec sont exemptees de la zone qui le concerne).
    pub fn pression_piece(&self, i: usize, p: &Pose) -> f64 {
        if self.pression.is_empty() || self.comps[i].statut != Statut::Mobile {
            return 0.0;
        }
        let mut c = 0.0;
        for k in 0..self.comps[i].pads.len() {
            let (x, y, hw, hh) = self.pad_world(i, k, p);
            for &(zx, zy, r, w, net) in &self.pression {
                if net.is_some() && self.comp_nets[i].contains(&net.unwrap()) {
                    continue;
                }
                let dx = ((zx - x).abs() - hw).max(0.0);
                let dy = ((zy - y).abs() - hh).max(0.0);
                if dx * dx + dy * dy < r * r {
                    c += w;
                }
            }
        }
        c
    }

    /// Centre et demi-dimensions d'une pastille dans le monde.
    pub fn pad_world(&self, i: usize, k: usize, p: &Pose) -> (f64, f64, f64, f64) {
        let pd = &self.comps[i].pads[k];
        let (dx, dy) = fwd(pd.lx, pd.ly, p.rot, p.bot);
        let (hw, hh) = if p.rot == 90 || p.rot == 270 { (pd.hh, pd.hw) } else { (pd.hw, pd.hh) };
        (p.x + dx, p.y + dy, hw, hh)
    }

    /// Boite orientee relative a l'origine.
    pub fn bb_rel(&self, i: usize, rot: i32, bot: bool) -> Rect {
        let l = self.comps[i].lbb;
        let mut r = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
        for &x in &[l[0], l[2]] {
            for &y in &[l[1], l[3]] {
                let (a, b) = fwd(x, y, rot, bot);
                r[0] = r[0].min(a);
                r[1] = r[1].min(b);
                r[2] = r[2].max(a);
                r[3] = r[3].max(b);
            }
        }
        r
    }

    pub fn bbox(&self, i: usize, p: &Pose) -> Rect {
        let b = self.bb_rel(i, p.rot, p.bot);
        [p.x + b[0], p.y + b[1], p.x + b[2], p.y + b[3]]
    }

    pub fn centre(&self, i: usize, p: &Pose) -> (f64, f64) {
        let b = self.bbox(i, p);
        ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
    }

    /// Rectangles occupes : boite gonflee sur sa face, pastilles traversantes +0,6 sur l'autre face.
    pub fn rects_of(&self, i: usize, p: &Pose) -> Vec<(u8, Rect)> {
        // cache des 8 orientations (4 rotations x 2 faces), calcule une fois a l'origine puis translate
        if let Some(c) = self.rects_rel.get(i) {
            if p.rot.rem_euclid(90) == 0 {
                let k = (p.rot.rem_euclid(360) / 90) as usize * 2 + p.bot as usize;
                return c[k].iter().map(|&(f, r)| (f, [r[0] + p.x, r[1] + p.y, r[2] + p.x, r[3] + p.y])).collect();
            }
        }
        self.rects_of_calcul(i, p)
    }

    /// Remplit le cache de `rects_of` (a appeler quand le modele est complet).
    pub fn remplir_cache_rects(&mut self) {
        let mut c = Vec::with_capacity(self.comps.len());
        for i in 0..self.comps.len() {
            let mut v = Vec::with_capacity(8);
            for k in 0..8 {
                v.push(self.rects_of_calcul(i, &Pose { x: 0.0, y: 0.0, rot: (k / 2) as i32 * 90, bot: k % 2 == 1 }));
            }
            c.push(v);
        }
        self.rects_rel = c;
    }

    /// Marge de placement autour de la boite d'une piece. Au moins `PIECE_MARGIN` ; et, pour que le placement reste
    /// routable, de quoi laisser un couloir d'une piste de signal entre deux pieces voisines : largeur preferee de la
    /// classe par defaut + deux isolements + l'arrondi de la grille de routage, la moitie pour chaque piece. Deduit des
    /// regles de la carte (rien de propre a une carte) ; sans ce couloir, des boites reduites aux pastilles se
    /// touchent a 0,5 mm et aucune piste ne passe entre deux pieces (campagnes du 24/11 : 8/40 contre 19/40 avec des
    /// boites plus larges de 0,27 mm).
    pub fn marge_piece(&self) -> f64 {
        if let Some(v) = std::env::var("PLACEUR_MARGE_PIECE").ok().and_then(|v| v.parse::<f64>().ok()) {
            return v;
        }
        let w = self.regles.largeurs.get("defaut").map_or(0.3, |l| l[1].max(l[0]).min(0.5));
        // arrondi de grille : un pas de 0,1 mm + les marges internes du tramage (0,03 mm), de chaque cote
        let couloir = w + 2.0 * self.regles.isolement.defaut + 2.0 * 0.13;
        PIECE_MARGIN.max(couloir / 2.0)
    }

    fn rects_of_calcul(&self, i: usize, p: &Pose) -> Vec<(u8, Rect)> {
        let b = self.bbox(i, p);
        // convertisseur libere : degagement autour des boitiers a pas fin (U10, M1, M2) pour que leurs broches au pas
        // de 0,5 / 0,65 mm puissent sortir en eventail (sinon les voisins colles bouchent les sorties) ; reglage
        // placement.degagement_pas_fin_mm
        let mp = self.marge_piece();
        let m = if self.bloc_libre && self.comps[i].pas_fin { mp.max(PIECE_MARGIN + self.param.placement.degagement_pas_fin_mm) } else { mp };
        let f: u8 = if p.bot { 1 } else { 0 };
        let mut out = vec![(f, [b[0] - m, b[1] - m, b[2] + m, b[3] + m])];
        // zones de l'empreinte interdites aux autres pieces : elles suivent la piece (une face par couche externe)
        for z in self.comps[i].zones.iter().filter(|z| z.placement) {
            let (pts, mk) = self.zone_monde(z, p);
            let g = z.largeur / 2.0;
            let r = [pts.iter().map(|q| q.0).fold(f64::MAX, f64::min) - g, pts.iter().map(|q| q.1).fold(f64::MAX, f64::min) - g,
                pts.iter().map(|q| q.0).fold(f64::MIN, f64::max) + g, pts.iter().map(|q| q.1).fold(f64::MIN, f64::max) + g];
            for face in 0..2u8 {
                if mk[face as usize] && !(face == f && r[0] >= b[0] - m && r[1] >= b[1] - m && r[2] <= b[2] + m && r[3] <= b[3] + m) {
                    out.push((face, r));
                }
            }
        }
        if self.comps[i].has_th {
            for k in 0..self.comps[i].pads.len() {
                if self.comps[i].pads[k].th {
                    // boite de placement de la pastille (pas le cuivre reel, plus petit) : meme regle que le controle
                    let (x, y, _, _) = self.pad_world(i, k, p);
                    let pd = &self.comps[i].pads[k];
                    let (hw, hh) = if p.rot == 90 || p.rot == 270 { (pd.ph, pd.pw) } else { (pd.pw, pd.ph) };
                    out.push((1 - f, [x - hw - TH_MARGIN, y - hh - TH_MARGIN, x + hw + TH_MARGIN, y + hh + TH_MARGIN]));
                }
            }
        }
        out
    }

    pub fn bloc_rects(&self, dx: f64, dy: f64) -> Vec<(u8, Rect)> {
        self.bloc_rects_base.iter().map(|&(f, r)| (f, [r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy])).collect()
    }

    pub fn emprise(&self, e: &Etat) -> Rect {
        let r = self.emprise_base;
        [r[0] + e.bdx, r[1] + e.bdy, r[2] + e.bdx, r[3] + e.bdy]
    }

    pub fn in_board(&self, r: &Rect) -> bool {
        let m = self.marge;
        if self.contour.len() >= 3 {
            // contour polygonal : les quatre coins a au moins la marge a l'interieur (+ centre, pour les encoches)
            return [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]), ((r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0)]
                .iter()
                .all(|&(x, y)| dist_poly(x, y, &self.contour) <= -m);
        }
        r[0] >= m && r[1] >= m && r[2] <= self.w - m && r[3] <= self.h - m
    }

    pub fn etat_v3(&self) -> Etat {
        Etat { poses: self.comps.iter().map(|c| c.pose_v3).collect(), bdx: self.delta_v3[0], bdy: self.delta_v3[1] }
    }

    /// Poses lues dans un JSON au format placements_v3 (mils) ; pieces absentes = pose d'origine.
    pub fn etat_depuis(&self, pl: &[PlacementIn]) -> Result<Etat, String> {
        let mut e = Etat { poses: self.comps.iter().map(|c| c.pose_base).collect(), bdx: 0.0, bdy: 0.0 };
        for p in pl {
            let i = *self.comp_idx.get(&p.designator).ok_or(format!("piece inconnue {}", p.designator))?;
            e.poses[i] = Pose { x: p.x * MIL, y: p.y * MIL, rot: p.rotation.rem_euclid(360), bot: p.side == "bottom" };
        }
        // bloc fige : translation lue sur sa premiere piece (aucune si pas de bloc)
        if let Some(&m1) = self.bloc.first() {
            e.bdx = ((e.poses[m1].x - self.comps[m1].pose_base.x) / MIL).round() * MIL;
            e.bdy = ((e.poses[m1].y - self.comps[m1].pose_base.y) / MIL).round() * MIL;
        }
        let (dx, dy) = (e.bdx, e.bdy);
        self.set_bloc(&mut e, dx, dy);
        Ok(e)
    }

    pub fn set_bloc(&self, e: &mut Etat, dx: f64, dy: f64) {
        e.bdx = dx;
        e.bdy = dy;
        for &i in &self.bloc {
            let b = self.comps[i].pose_base;
            e.poses[i] = Pose { x: b.x + dx, y: b.y + dy, rot: b.rot, bot: b.bot };
        }
    }

    pub fn sw_node(&self, e: &Etat) -> (f64, f64) {
        if self.bloc_libre {
            let cv = self.cfg.convertisseurs.first();
            if let (Some(&a), Some(&b)) = (cv.and_then(|c| self.comp_idx.get(&c.haut)), cv.and_then(|c| self.comp_idx.get(&c.bas))) {
                let (p, q) = (self.centre(a, &e.poses[a]), self.centre(b, &e.poses[b]));
                return ((p.0 + q.0) / 2.0, (p.1 + q.1) / 2.0);
            }
        }
        (self.m1c[0] + e.bdx, self.m1c[1] + e.bdy)
    }

    /// Termes conserves de v3 : decouplage (attraction + penalite au-dela de 3,8 mm) et distance douce au noeud SW.
    pub fn cout_extra(&self, e: &Etat) -> f64 {
        let mut c = 0.0;
        for (i, k, ics) in &self.decaps {
            let (x, y, _, _) = self.pad_world(*i, *k, &e.poses[*i]);
            let mut best = f64::MAX;
            for &(a, b) in ics {
                let (u, v, _, _) = self.pad_world(a, b, &e.poses[a]);
                best = best.min(((u - x).powi(2) + (v - y).powi(2)).sqrt());
            }
            c += best;
            if best > DEC_LIM - 0.2 {
                c += DEC_W * (best - DEC_LIM + 0.2);
            }
        }
        if self.bloc_libre {
            c += self.cout_hacheur(e);
        }
        let (sx, sy) = self.sw_node(e);
        for &i in &self.soft {
            let (cx, cy) = self.centre(i, &e.poses[i]);
            let d = ((cx - sx).powi(2) + (cy - sy).powi(2)).sqrt();
            if d < SW_DIST {
                c += SW_SOFT_W * (SW_DIST - d);
            }
        }
        c
    }

    pub fn decaps_loin(&self, e: &Etat) -> usize {
        let mut n = 0;
        for (i, k, ics) in &self.decaps {
            let (x, y, _, _) = self.pad_world(*i, *k, &e.poses[*i]);
            let best = ics
                .iter()
                .map(|&(a, b)| {
                    let (u, v, _, _) = self.pad_world(a, b, &e.poses[a]);
                    ((u - x).powi(2) + (v - y).powi(2)).sqrt()
                })
                .fold(f64::MAX, f64::min);
            if best > DEC_LIM {
                n += 1;
            }
        }
        n
    }

    /// HPWL non pondere hors GND/+8V (meme definition que le rapport v3).
    pub fn hpwl(&self, e: &Etat) -> f64 {
        let mut s = 0.0;
        for n in &self.nets {
            if n.plan || n.pins.is_empty() {
                continue;
            }
            let mut r = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
            for &(i, k) in &n.pins {
                let (x, y, _, _) = self.pad_world(i, k, &e.poses[i]);
                r[0] = r[0].min(x);
                r[1] = r[1].min(y);
                r[2] = r[2].max(x);
                r[3] = r[3].max(y);
            }
            s += r[2] - r[0] + r[3] - r[1];
        }
        s
    }
}

// --------------------------------------------------------------------------- occupation (grille seau 2 mm)
pub const OWN_STATIC: i32 = -1;
pub const OWN_BLOC: i32 = -2;

pub struct Occup {
    nx: usize,
    ny: usize,
    cell: f64,
    cells: [Vec<Vec<u32>>; 2],
    items: Vec<(u8, Rect, i32)>,
    pub items_of: Vec<Vec<u32>>,
}

impl Occup {
    pub fn new(m: &Modele, e: &Etat) -> Occup {
        let cell = 2.0;
        let nx = (m.w / cell).ceil() as usize + 1;
        let ny = (m.h / cell).ceil() as usize + 1;
        let mut o = Occup {
            nx,
            ny,
            cell,
            cells: [vec![Vec::new(); nx * ny], vec![Vec::new(); nx * ny]],
            items: Vec::new(),
            items_of: vec![Vec::new(); m.comps.len()],
        };
        for &(f, r) in &m.obstacles {
            o.add(f, r, OWN_STATIC);
        }
        for (f, r) in m.bloc_rects(e.bdx, e.bdy) {
            o.add(f, r, OWN_BLOC);
        }
        for &i in &m.mobiles {
            for (f, r) in m.rects_of(i, &e.poses[i]) {
                let it = o.add(f, r, i as i32);
                o.items_of[i].push(it);
            }
        }
        o
    }

    /// Occupation sans les pieces mobiles (placement initial aleatoire, pieces inserees une a une).
    pub fn new_sans_mobiles(m: &Modele, e: &Etat) -> Occup {
        let cell = 2.0;
        let nx = (m.w / cell).ceil() as usize + 1;
        let ny = (m.h / cell).ceil() as usize + 1;
        let mut o = Occup {
            nx,
            ny,
            cell,
            cells: [vec![Vec::new(); nx * ny], vec![Vec::new(); nx * ny]],
            items: Vec::new(),
            items_of: vec![Vec::new(); m.comps.len()],
        };
        for &(f, r) in &m.obstacles {
            o.add(f, r, OWN_STATIC);
        }
        for (f, r) in m.bloc_rects(e.bdx, e.bdy) {
            o.add(f, r, OWN_BLOC);
        }
        o
    }

    pub fn inserer(&mut self, i: usize, rs: &[(u8, Rect)]) {
        for (f, r) in rs {
            let it = self.add(*f, *r, i as i32);
            self.items_of[i].push(it);
        }
    }

    fn range(&self, r: &Rect) -> (usize, usize, usize, usize) {
        let c = |v: f64, n: usize| -> usize { ((v / self.cell).floor().max(0.0) as usize).min(n - 1) };
        (c(r[0], self.nx), c(r[1], self.ny), c(r[2], self.nx), c(r[3], self.ny))
    }

    fn add(&mut self, f: u8, r: Rect, owner: i32) -> u32 {
        let it = self.items.len() as u32;
        self.items.push((f, r, owner));
        let (x1, y1, x2, y2) = self.range(&r);
        for x in x1..=x2 {
            for y in y1..=y2 {
                self.cells[f as usize][y * self.nx + x].push(it);
            }
        }
        it
    }

    fn move_item(&mut self, it: u32, f: u8, r: Rect) {
        let (of, or, _) = self.items[it as usize];
        let (x1, y1, x2, y2) = self.range(&or);
        for x in x1..=x2 {
            for y in y1..=y2 {
                let v = &mut self.cells[of as usize][y * self.nx + x];
                if let Some(p) = v.iter().position(|&q| q == it) {
                    v.swap_remove(p);
                }
            }
        }
        self.items[it as usize].0 = f;
        self.items[it as usize].1 = r;
        let (x1, y1, x2, y2) = self.range(&r);
        for x in x1..=x2 {
            for y in y1..=y2 {
                self.cells[f as usize][y * self.nx + x].push(it);
            }
        }
    }

    pub fn free(&self, f: u8, r: &Rect, excl: &[i32]) -> bool {
        let (x1, y1, x2, y2) = self.range(r);
        for x in x1..=x2 {
            for y in y1..=y2 {
                for &it in &self.cells[f as usize][y * self.nx + x] {
                    let (_, q, o) = &self.items[it as usize];
                    if excl.contains(o) {
                        continue;
                    }
                    if ov(r, q) {
                        return false;
                    }
                }
            }
        }
        true
    }

    pub fn legal(&self, m: &Modele, i: usize, p: &Pose, excl: &[i32]) -> Option<Vec<(u8, Rect)>> {
        let rs = m.rects_of(i, p);
        if !m.in_board(&rs[0].1) {
            return None;
        }
        for (f, r) in &rs {
            if !self.free(*f, r, excl) {
                return None;
            }
        }
        Some(rs)
    }

    pub fn reinsert(&mut self, i: usize, rs: &[(u8, Rect)]) {
        let its = self.items_of[i].clone();
        assert_eq!(its.len(), rs.len());
        for (it, (f, r)) in its.iter().zip(rs.iter()) {
            self.move_item(*it, *f, *r);
        }
    }

    /// Proprietaires des rectangles qui chevauchent r sur la face f.
    pub fn blockers(&self, f: u8, r: &Rect, excl: &[i32]) -> Vec<i32> {
        let (x1, y1, x2, y2) = self.range(r);
        let mut out = Vec::new();
        for x in x1..=x2 {
            for y in y1..=y2 {
                for &it in &self.cells[f as usize][y * self.nx + x] {
                    let (_, q, o) = &self.items[it as usize];
                    if !excl.contains(o) && ov(r, q) && !out.contains(o) {
                        out.push(*o);
                    }
                }
            }
        }
        out
    }
}

// --------------------------------------------------------------------------- generateur pseudo-aleatoire reproductible
pub struct Rng {
    s: [u64; 4],
}

impl Rng {
    pub fn new(seed: u64) -> Rng {
        let mut z = seed.wrapping_add(0x9E3779B97F4A7C15);
        let mut s = [0u64; 4];
        for v in s.iter_mut() {
            z = z.wrapping_add(0x9E3779B97F4A7C15);
            let mut x = z;
            x = (x ^ (x >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
            x = (x ^ (x >> 27)).wrapping_mul(0x94D049BB133111EB);
            *v = x ^ (x >> 31);
        }
        Rng { s }
    }
    pub fn next(&mut self) -> u64 {
        let r = self.s[1].wrapping_mul(5).rotate_left(7).wrapping_mul(9);
        let t = self.s[1] << 17;
        self.s[2] ^= self.s[0];
        self.s[3] ^= self.s[1];
        self.s[1] ^= self.s[2];
        self.s[0] ^= self.s[3];
        self.s[2] ^= t;
        self.s[3] = self.s[3].rotate_left(45);
        r
    }
    pub fn f64(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
    pub fn below(&mut self, n: usize) -> usize {
        (self.f64() * n as f64) as usize % n.max(1)
    }
    pub fn gauss(&mut self) -> f64 {
        let u1 = self.f64().max(1e-12);
        let u2 = self.f64();
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }
}

/// Distance signee d'un point a un polygone simple (negative a l'interieur).
pub fn dist_poly(px: f64, py: f64, pts: &[(f64, f64)]) -> f64 {
    let n = pts.len();
    if n == 0 {
        return f64::MAX;
    }
    let mut d = f64::MAX;
    let mut dedans = false;
    for k in 0..n {
        let (ax, ay) = pts[k];
        let (bx, by) = pts[(k + 1) % n];
        d = d.min(d_pt_seg(px, py, ax, ay, bx, by));
        if (ay > py) != (by > py) && px < ax + (py - ay) * (bx - ax) / (by - ay) {
            dedans = !dedans;
        }
    }
    if dedans {
        -d
    } else {
        d
    }
}

#[cfg(test)]
mod tests_ponts {
    use super::*;
    use crate::globalr::tests::{comp, donnees, pad};

    /// Pastilles voisines d'un meme reseau pontees quel que soit leur ordre dans les donnees : une seule broche reste
    /// a router (l'ancien balayage glouton en laissait deux pour l'ordre 1, 3, 2 : M1.3 sur METROID).
    fn broches_restantes(ordre: &[&str]) -> usize {
        let pads: Vec<PadIn> = ordre.iter().map(|&n| pad(n, "S", n.parse::<f64>().unwrap() * 0.65, 0.0, false)).collect();
        let mut c = comp("Q", 10.0, 5.0, false, pads);
        c.statut = "mobile".into();
        let mut m = Modele::new(&donnees(vec![c], vec![]), Parametres::defaut());
        m.liberer_bloc();
        m.nets[m.net_idx["S"]].pins.len()
    }

    #[test]
    fn ponts_independants_de_l_ordre_des_pastilles() {
        for ordre in [["1", "2", "3"], ["1", "3", "2"], ["3", "2", "1"], ["2", "3", "1"]] {
            assert_eq!(broches_restantes(&ordre), 1, "ordre {:?}", ordre);
        }
    }
}
