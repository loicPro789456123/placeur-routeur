//! Lecteur provisoire de l'ancien format des données (version 1, unités mm, origine coin bas-gauche) : utilisé
//! seulement par le cœur non branché et ses tests jusqu'à l'étape 3 du lot A, où il est supprimé.
//! Le format du contrat (`donnees_placeur/2`) est lu par `donnees.rs`.
use serde::Deserialize;
use std::collections::HashMap;

/// Contour de la carte : rectangle w x h (origine coin bas-gauche), ou polygone `points` (mm) ; w / h deduits.
#[derive(Deserialize, Clone)]
pub struct Contour {
    #[serde(default)]
    pub w: f64,
    #[serde(default)]
    pub h: f64,
    #[serde(default)]
    pub points: Vec<[f64; 2]>,
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

/// Region de cuivre d'empreinte (coordonnees locales de la piece, mm, meme convention que les pastilles).
#[derive(Deserialize, Clone, Debug)]
pub struct RegionIn {
    /// "composant" (face de la piece), "opposee" ou "toutes"
    #[serde(default = "face_composant")]
    pub face: String,
    /// pastille a laquelle la region est reliee (elle en herite le reseau)
    #[serde(default)]
    pub pastille: Option<String>,
    #[serde(default)]
    pub reseau: Option<String>,
    pub points: Vec<[f64; 2]>,
}

fn face_composant() -> String {
    "composant".to_string()
}

#[derive(Deserialize, Clone)]
pub struct CompIn {
    pub nom: String,
    pub statut: String,
    pub lbb: [f64; 4],
    pub pastilles: Vec<PadIn>,
    pub pose_actuelle: (f64, f64, i32, bool),
    /// pose de reference (ancien nom pose_v3, accepte)
    #[serde(alias = "pose_v3")]
    pub pose_cible: (f64, f64, i32, bool),
    #[serde(default)]
    pub orientations: Vec<(i32, bool)>,
    #[serde(default)]
    pub regions: Vec<RegionIn>,
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

/// Bloc rigide (ancien mode de comparaison, facultatif).
#[derive(Deserialize, Clone, Default)]
#[serde(default)]
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
    #[serde(default)]
    pub obstacles_placement: Vec<ObsIn>,
    #[serde(default)]
    pub bloc: BlocIn,
    #[serde(default)]
    pub trous_m3: Vec<[f64; 2]>,
    #[serde(default)]
    pub fentes: Vec<[f64; 4]>,
    #[serde(default)]
    pub soft_sw: Vec<String>,
    #[serde(default)]
    pub decouplages: Vec<DecIn>,
    #[serde(default)]
    pub poids_reseaux: HashMap<String, f64>,
    pub reseaux_plans: Vec<String>,
    pub classes: HashMap<String, String>,
    pub regles: Regles,
}

pub fn lire(chemin: &str) -> Result<Donnees, String> {
    let s = std::fs::read_to_string(chemin).map_err(|e| format!("lecture {} : {}", chemin, e))?;
    let mut d: Donnees = serde_json::from_str(&s).map_err(|e| format!("JSON {} : {}", chemin, e))?;
    // contour polygonal : largeur / hauteur = boite englobante (origine au coin bas-gauche du contour)
    if !d.contour.points.is_empty() && (d.contour.w <= 0.0 || d.contour.h <= 0.0) {
        d.contour.w = d.contour.points.iter().map(|p| p[0]).fold(f64::MIN, f64::max);
        d.contour.h = d.contour.points.iter().map(|p| p[1]).fold(f64::MIN, f64::max);
    }
    Ok(d)
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
