//! Lecture du JSON produit par `export_donnees.py` (unites mm, origine coin bas-gauche).
use serde::Deserialize;
use std::collections::HashMap;

#[derive(Deserialize, Clone)]
pub struct Contour {
    pub w: f64,
    pub h: f64,
}

#[derive(Deserialize, Clone)]
pub struct PadIn {
    pub nom: String,
    pub reseau: String,
    pub lx: f64,
    pub ly: f64,
    pub hw: f64,
    pub hh: f64,
    pub th: bool,
}

#[derive(Deserialize, Clone)]
pub struct CompIn {
    pub nom: String,
    pub statut: String,
    pub lbb: [f64; 4],
    pub pastilles: Vec<PadIn>,
    pub pose_actuelle: (f64, f64, i32, bool),
    pub pose_v3: (f64, f64, i32, bool),
    #[serde(default)]
    pub orientations: Vec<(i32, bool)>,
}

#[derive(Deserialize, Clone)]
pub struct ObsIn {
    pub face: u8,
    pub rect: [f64; 4],
}

#[derive(Deserialize, Clone)]
pub struct FaceRect {
    pub face: u8,
    pub rect: [f64; 4],
}

#[derive(Deserialize, Clone)]
pub struct BlocIn {
    pub rects_base: Vec<FaceRect>,
    pub delta_v3: [f64; 2],
    pub emprise_base: [f64; 4],
    pub colonne: [f64; 2],
    pub broches_u25: Vec<[f64; 2]>,
    pub m1_centre_base: [f64; 2],
    pub dist_sw_min: f64,
}

#[derive(Deserialize, Clone)]
pub struct DecIn {
    pub comp: String,
    pub pastille: usize,
    pub broches_ci: Vec<(String, usize)>,
}

#[derive(Deserialize, Clone)]
pub struct Isolement {
    pub defaut: f64,
    #[serde(rename = "COMMUTATION")]
    pub commutation: f64,
    #[allow(dead_code)]
    pub pastilles_pas_fin: f64,
    pub pieces_pas_fin: Vec<String>,
}

#[derive(Deserialize, Clone)]
pub struct ViaR {
    pub diametre: f64,
    pub trou: f64,
    pub via_sous_cms_permis: Vec<String>,
}

#[derive(Deserialize, Clone)]
pub struct Regles {
    pub isolement: Isolement,
    pub largeurs: HashMap<String, [f64; 3]>,
    pub via: ViaR,
    pub garde_bord_fentes_trous: f64,
    pub rayon_trou_m3: f64,
}

#[derive(Deserialize, Clone)]
pub struct Donnees {
    pub contour: Contour,
    pub marge_bord: f64,
    pub composants: Vec<CompIn>,
    pub obstacles_placement: Vec<ObsIn>,
    pub bloc: BlocIn,
    pub trous_m3: Vec<[f64; 2]>,
    pub fentes: Vec<[f64; 4]>,
    pub soft_sw: Vec<String>,
    pub decouplages: Vec<DecIn>,
    pub poids_reseaux: HashMap<String, f64>,
    pub reseaux_plans: Vec<String>,
    pub classes: HashMap<String, String>,
    pub regles: Regles,
}

pub fn lire(chemin: &str) -> Result<Donnees, String> {
    let s = std::fs::read_to_string(chemin).map_err(|e| format!("lecture {} : {}", chemin, e))?;
    serde_json::from_str(&s).map_err(|e| format!("JSON {} : {}", chemin, e))
}

/// Entree de placement au format de `placements_v3.json` (mils).
#[derive(Deserialize, Clone)]
pub struct PlacementIn {
    pub designator: String,
    pub side: String,
    pub x: f64,
    pub y: f64,
    pub rotation: i32,
}

pub fn lire_placement(chemin: &str) -> Result<Vec<PlacementIn>, String> {
    let s = std::fs::read_to_string(chemin).map_err(|e| format!("lecture {} : {}", chemin, e))?;
    serde_json::from_str(&s).map_err(|e| format!("JSON {} : {}", chemin, e))
}
