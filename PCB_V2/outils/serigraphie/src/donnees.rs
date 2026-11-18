//! Lecture des fichiers d'entree (mils, valeurs en chaines, jetons de couche neutres) et construction du modele.
//!
//! Jetons de couche : pastilles et composants `dessus`, `dessous` (pastilles : aussi `traversante`) ; traits,
//! arcs et textes `seri_dessus`, `seri_dessous` (serigraphie) ou tout autre jeton (texte hors serigraphie).
//! Toute erreur de lecture rend un message en francais qui ne cite que le nom du fichier (jamais un chemin).

use crate::geom::*;
use serde_json::Value;
use std::collections::HashMap;
use std::path::Path;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Face {
    Dessus,
    Dessous,
}

impl Face {
    pub fn idx(self) -> usize {
        match self {
            Face::Dessus => 0,
            Face::Dessous => 1,
        }
    }
    pub fn couche_seri(self) -> &'static str {
        match self {
            Face::Dessus => "seri_dessus",
            Face::Dessous => "seri_dessous",
        }
    }
}

pub fn face_seri(couche: &str) -> Option<Face> {
    match couche {
        "seri_dessus" => Some(Face::Dessus),
        "seri_dessous" => Some(Face::Dessous),
        _ => None,
    }
}

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(|x| x.as_str()).unwrap_or("")
}

fn court(t: &str) -> String {
    t.chars().take(40).collect()
}

fn f(v: &Value, k: &str, fichier: &str) -> Result<f64, String> {
    let t = s(v, k);
    match t.trim().parse::<f64>() {
        Ok(x) if x.is_finite() => Ok(x),
        _ => Err(format!("{} : champ {} illisible ({:?})", fichier, k, court(t))),
    }
}

fn b(v: &Value, k: &str) -> bool {
    s(v, k) == "true"
}

#[derive(Clone, Debug)]
pub struct Pastille {
    pub comp: String,
    pub nom: String,
    pub x: f64,
    pub y: f64,
    pub faces: [bool; 2],
    pub cuivre: Forme,
    pub masque: Forme,
}

#[derive(Clone, Debug)]
pub struct PisteSeri {
    pub a: P,
    pub b: P,
    pub w: f64,
    pub face: Face,
    pub comp: String,
}

#[derive(Clone, Debug)]
pub struct ArcSeri {
    pub c: P,
    pub r: f64,
    pub a0: f64,
    pub a1: f64,
    pub w: f64,
    pub face: Face,
    pub comp: String,
}

#[derive(Clone, Debug)]
pub struct Texte {
    pub texte: String,
    pub x: f64,
    pub y: f64,
    pub h: f64,
    pub w: f64,
    pub rot: f64,
    pub face: Option<Face>,
    pub couche: String,
    pub designateur: bool,
    pub commentaire: bool,
    pub comp: String,
    pub miroir: bool,
    pub ttf: bool,
    pub bounds: [f64; 4],
}

#[derive(Clone, Debug)]
pub struct Composant {
    pub nom: String,
    pub face: Face,
    pub x: f64,
    pub y: f64,
    pub rot: f64,
    pub auto_pos: bool,
    /// corps estime : boite des pastilles (cuivre) et des traits de serigraphie de l'empreinte
    pub corps: [f64; 4],
}

#[derive(Clone, Debug)]
pub struct Via {
    pub c: P,
    pub d: f64,
}

pub struct Donnees {
    pub pastilles: Vec<Pastille>,
    pub pistes: Vec<PisteSeri>,
    pub arcs: Vec<ArcSeri>,
    pub textes: Vec<Texte>,
    pub composants: Vec<Composant>,
    pub vias: Vec<Via>,
}

fn lire(dossier: &Path, nom: &str) -> Result<Vec<Value>, String> {
    let fichier = format!("{}.json", nom);
    let t = std::fs::read_to_string(dossier.join(&fichier)).map_err(|_| format!("{} : fichier absent ou illisible", fichier))?;
    let v: Vec<Value> =
        serde_json::from_str(&t).map_err(|e| format!("{} : JSON invalide (ligne {}, colonne {})", fichier, e.line(), e.column()))?;
    if v.iter().any(|e| !e.is_object()) {
        return Err(format!("{} : liste d'objets attendue", fichier));
    }
    Ok(v)
}

pub fn charger(dossier: &Path) -> Result<Donnees, String> {
    let mut pastilles = Vec::new();
    let fp = "pastilles.json";
    for v in lire(dossier, "pastilles")? {
        let rect = match s(&v, "TopShape") {
            "2" => true,
            "1" => false,
            x => return Err(format!("{} : forme de pastille {:?} non geree", fp, court(x))),
        };
        let faces = match s(&v, "Layer") {
            "dessus" => [true, false],
            "dessous" => [false, true],
            "traversante" => [true, true],
            x => return Err(format!("{} : couche de pastille {:?} inconnue", fp, court(x))),
        };
        let (x, y) = (f(&v, "X", fp)?, f(&v, "Y", fp)?);
        let (sx, sy, r) = (f(&v, "TopXSize", fp)?, f(&v, "TopYSize", fp)?, f(&v, "Rotation", fp)?);
        let e = f(&v, "SolderMaskExpansion", fp)?;
        pastilles.push(Pastille {
            comp: s(&v, "Component").to_string(),
            nom: s(&v, "Name").to_string(),
            x,
            y,
            faces,
            cuivre: forme_pastille(x, y, sx, sy, r, rect, 0.0),
            masque: forme_pastille(x, y, sx, sy, r, rect, e),
        });
    }
    let mut pistes = Vec::new();
    for n in ["pistes_serigraphie_top", "pistes_serigraphie_bottom"] {
        let fi = format!("{}.json", n);
        for v in lire(dossier, n)? {
            pistes.push(PisteSeri {
                a: p(f(&v, "X1", &fi)?, f(&v, "Y1", &fi)?),
                b: p(f(&v, "X2", &fi)?, f(&v, "Y2", &fi)?),
                w: f(&v, "Width", &fi)?,
                face: face_seri(s(&v, "Layer")).ok_or_else(|| format!("{} : couche de serigraphie attendue", fi))?,
                comp: s(&v, "Component").to_string(),
            });
        }
    }
    let mut arcs = Vec::new();
    for n in ["arcs_serigraphie_top", "arcs_serigraphie_bottom"] {
        let fi = format!("{}.json", n);
        for v in lire(dossier, n)? {
            arcs.push(ArcSeri {
                c: p(f(&v, "XCenter", &fi)?, f(&v, "YCenter", &fi)?),
                r: f(&v, "Radius", &fi)?,
                a0: f(&v, "StartAngle", &fi)?,
                a1: f(&v, "EndAngle", &fi)?,
                w: f(&v, "Width", &fi)?,
                face: face_seri(s(&v, "Layer")).ok_or_else(|| format!("{} : couche de serigraphie attendue", fi))?,
                comp: s(&v, "Component").to_string(),
            });
        }
    }
    let mut textes = Vec::new();
    let ft = "textes.json";
    for v in lire(dossier, "textes")? {
        textes.push(Texte {
            texte: s(&v, "Text").to_string(),
            x: f(&v, "X", ft)?,
            y: f(&v, "Y", ft)?,
            h: f(&v, "Height", ft)?,
            w: f(&v, "StrokeWidth", ft)?,
            rot: f(&v, "Rotation", ft)?,
            face: face_seri(s(&v, "Layer")),
            couche: s(&v, "Layer").to_string(),
            designateur: b(&v, "IsDesignator"),
            commentaire: b(&v, "IsComment"),
            comp: s(&v, "Component").to_string(),
            miroir: b(&v, "MirrorFlag"),
            ttf: b(&v, "UseTTFonts"),
            bounds: [f(&v, "BoundsLeft", ft)?, f(&v, "BoundsBottom", ft)?, f(&v, "BoundsRight", ft)?, f(&v, "BoundsTop", ft)?],
        });
    }
    let mut vias = Vec::new();
    let fv = "vias.json";
    for v in lire(dossier, "vias")? {
        vias.push(Via { c: p(f(&v, "X", fv)?, f(&v, "Y", fv)?), d: f(&v, "Size", fv)? });
    }
    // corps estimes
    let mut corps: HashMap<String, [f64; 4]> = HashMap::new();
    let mut etend = |c: &str, bb: [f64; 4]| {
        if c.is_empty() {
            return;
        }
        let e = corps.entry(c.to_string()).or_insert(bb);
        e[0] = e[0].min(bb[0]);
        e[1] = e[1].min(bb[1]);
        e[2] = e[2].max(bb[2]);
        e[3] = e[3].max(bb[3]);
    };
    for q in &pastilles {
        etend(&q.comp, q.cuivre.bb);
    }
    for t in &pistes {
        etend(&t.comp, [t.a.x.min(t.b.x), t.a.y.min(t.b.y), t.a.x.max(t.b.x), t.a.y.max(t.b.y)]);
    }
    for a in &arcs {
        let pts = arc_points(a.c, a.r, a.a0, a.a1, 0.1);
        for q in pts {
            etend(&a.comp, [q.x, q.y, q.x, q.y]);
        }
    }
    let mut composants = Vec::new();
    let fc = "composants.json";
    for v in lire(dossier, "composants")? {
        let nom = s(&v, "Designator").to_string();
        let (x, y) = (f(&v, "X", fc)?, f(&v, "Y", fc)?);
        let cb = corps.get(&nom).copied().unwrap_or([x - 10.0, y - 10.0, x + 10.0, y + 10.0]);
        let face = match s(&v, "Layer") {
            "dessus" => Face::Dessus,
            "dessous" => Face::Dessous,
            _ => return Err(format!("{} : face de composant attendue (dessus ou dessous)", fc)),
        };
        composants.push(Composant {
            face,
            x,
            y,
            rot: f(&v, "Rotation", fc)?,
            auto_pos: s(&v, "NameAutoPosition") != "0",
            corps: cb,
            nom,
        });
    }
    Ok(Donnees { pastilles, pistes, arcs, textes, composants, vias })
}

/// grille de hachage uniforme pour les requetes par boite
pub struct Grille {
    pas: f64,
    cases: HashMap<(i32, i32), Vec<usize>>,
}

impl Grille {
    pub fn new(pas: f64) -> Grille {
        Grille { pas, cases: HashMap::new() }
    }
    fn plage(&self, bb: &[f64; 4]) -> (i32, i32, i32, i32) {
        (
            (bb[0] / self.pas).floor() as i32,
            (bb[1] / self.pas).floor() as i32,
            (bb[2] / self.pas).floor() as i32,
            (bb[3] / self.pas).floor() as i32,
        )
    }
    pub fn ajoute(&mut self, bb: &[f64; 4], i: usize) {
        let (x0, y0, x1, y1) = self.plage(bb);
        for gx in x0..=x1 {
            for gy in y0..=y1 {
                self.cases.entry((gx, gy)).or_default().push(i);
            }
        }
    }
    /// indices (dedoublonnes, tries) des objets dont la case touche bb elargie de marge
    pub fn requete(&self, bb: &[f64; 4], marge: f64) -> Vec<usize> {
        let (x0, y0, x1, y1) = self.plage(&[bb[0] - marge, bb[1] - marge, bb[2] + marge, bb[3] + marge]);
        let mut v = Vec::new();
        for gx in x0..=x1 {
            for gy in y0..=y1 {
                if let Some(l) = self.cases.get(&(gx, gy)) {
                    v.extend_from_slice(l);
                }
            }
        }
        v.sort_unstable();
        v.dedup();
        v
    }
}
