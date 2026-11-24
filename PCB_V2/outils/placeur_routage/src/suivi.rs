//! Suivi en direct et images du timelapse (24/11/2026) : instantanés légers écrits à intervalle de temps (jamais à
//! chaque mouvement) dans le dossier `--suivi D` :
//! - `instantane.json` (remplacé à chaque instantané, écriture atomique) : `{"t", "phase", "cout", "placements",
//!   "routage"}` (placements/2, routage/2 si un routage détaillé existe, sinon null) ;
//! - `courbe.jsonl` : une ligne `{"t", "phase", "cout"}` par instantané (courbe de décroissance du coût) ;
//! - `images/NNNNN.json` (avec `--timelapse`) : copie de chaque instantané, pour la vidéo faite après le calcul.
//! Rien n'est écrit sans `--suivi`. Le coût d'un appel qui n'est pas dû est un test d'horloge.

use crate::detail::Detail;
use crate::geom::{Etat, Modele};
use serde_json::{json, Value};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Instant;

struct Suivi {
    dossier: PathBuf,
    periode_s: f64,
    timelapse: bool,
    t0: Instant,
    dernier: f64,
    images: usize,
}

static SUIVI: Mutex<Option<Suivi>> = Mutex::new(None);

/// Nombre maximal d'images gardées pour le timelapse (une vidéo de 10 à 20 s en prend 100 à 200).
const IMAGES_MAX: usize = 1500;

/// Active le suivi (dossier créé) ; `periode_s` : intervalle minimal entre deux instantanés.
pub fn configurer(dossier: Option<PathBuf>, periode_s: f64, timelapse: bool) {
    let s = dossier.map(|d| {
        let _ = std::fs::create_dir_all(d.join("images"));
        Suivi { dossier: d, periode_s: periode_s.max(0.2), timelapse, t0: Instant::now(), dernier: f64::NEG_INFINITY, images: 0 }
    });
    if let Ok(mut g) = SUIVI.lock() {
        *g = s;
    }
}

/// Vrai si un instantané est dû (suivi actif et intervalle écoulé).
pub fn du() -> bool {
    match SUIVI.try_lock() {
        Ok(g) => g.as_ref().map_or(false, |s| s.t0.elapsed().as_secs_f64() - s.dernier >= s.periode_s),
        Err(_) => false,
    }
}

/// Instantané du placement (et du routage s'il est donné), si dû. `forcer` : écrit même si l'intervalle n'est pas
/// écoulé (fin de phase).
pub fn noter(m: &Modele, e: &Etat, phase: &str, cout: f64, detail: Option<&Detail>, forcer: bool) {
    if !forcer && !du() {
        return;
    }
    let Ok(mut g) = SUIVI.lock() else { return };
    let Some(s) = g.as_mut() else { return };
    let t = s.t0.elapsed().as_secs_f64();
    s.dernier = t;
    let routage = detail.map(crate::sortie::routage_json).unwrap_or(Value::Null);
    let cout = if cout.is_finite() { (cout * 10.0).round() / 10.0 } else { -1.0 };
    let v = json!({"format": "instantane/1", "t": (t * 10.0).round() / 10.0, "phase": phase, "cout": cout,
        "placements": crate::sortie::placement_json(m, e), "routage": routage});
    let texte = v.to_string();
    let tmp = s.dossier.join("instantane.json.tmp");
    if std::fs::write(&tmp, &texte).is_ok() {
        let _ = std::fs::rename(&tmp, s.dossier.join("instantane.json"));
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(s.dossier.join("courbe.jsonl")) {
        let _ = writeln!(f, "{}", json!({"t": (t * 10.0).round() / 10.0, "phase": phase, "cout": cout}));
    }
    // images du timelapse : placement seul (le routage, lourd, seulement en fin de tour de boucle et à la fin),
    // au plus IMAGES_MAX images
    if s.timelapse && s.images < IMAGES_MAX {
        let img = if matches!(phase, "boucle" | "fin") {
            texte
        } else {
            json!({"format": "instantane/1", "t": v["t"], "phase": phase, "cout": cout, "placements": v["placements"], "routage": null}).to_string()
        };
        let _ = std::fs::write(s.dossier.join("images").join(format!("{:05}.json", s.images)), img);
        s.images += 1;
    }
}
