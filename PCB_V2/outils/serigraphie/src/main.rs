//! serigraphie : placement autonome de la serigraphie d'une carte, apres routage (moteur generique).
//!
//! Usage :
//!   serigraphie --entree D --sortie F --carte C [options]
//! Options obligatoires :
//!   --entree D          dossier des fichiers d'entree (pastilles, composants, vias, textes, traits et arcs de
//!                       serigraphie ; jetons de couche neutres, voir donnees.rs)
//!   --sortie F          fichier de resultat (JSON)
//!   --carte C           geometrie et regles de la carte (format carte_serigraphie/1, mils, repere commun) :
//!                       {"format", "unites": "mil", "contour": [[x, y], ...], "decoupes": [[[x, y], ...], ...],
//!                        "trous": [[x, y, rayon], ...], "regles": {"garde_masque_mm", "garde_bord_mm",
//!                        "tailles_mm": [[hauteur, epaisseur], ...], "garde_trait_mm"?, "garde_texte_mm"?}}
//! Options :
//!   --graine N (1) ; --chaines K (16) ; --mouvements M (20000000) ; --threads T (0 = tous les coeurs)
//!   --reference cuivre|masque (cuivre) ; --marge X (0.02 mm, ajoutee a garde_masque_mm)
//!   --commentaires-visibles   les commentaires de la serigraphie sont des obstacles (defaut : supposes caches)
//! Codes de sortie : 0 fait ; 2 entree invalide (message en francais, sans chemin, jamais de panique) ;
//! 4 erreur interne (le calcul tourne sous catch_unwind). Le resultat ne depend pas du nombre de fils.

mod donnees;
mod etiquetage;
mod geom;
mod rognage;
#[cfg(test)]
mod tests_calibrage;
#[cfg(test)]
mod tests_moteur;

use donnees::*;
use etiquetage::*;
use geom::*;
use rayon::prelude::*;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

pub const CODE_ENTREE: i32 = 2;
pub const CODE_INTERNE: i32 = 4;
/// pile de chaque fil du calcul (octets)
const PILE_FIL: usize = 16 << 20;

fn r3(v: f64) -> f64 {
    (v * 1000.0).round() / 1000.0
}

fn mm(v: f64) -> f64 {
    v / MIL_MM
}

/// nom de fichier seul (jamais de chemin dans un message)
fn nom(f: &Path) -> String {
    f.file_name().map(|x| x.to_string_lossy().into_owned()).unwrap_or_else(|| "?".to_string())
}

/// regles de la carte (mm)
#[derive(Clone, Debug)]
pub struct Regles {
    pub garde_masque_mm: f64,
    pub garde_bord_mm: f64,
    pub garde_trait_mm: f64,
    pub garde_texte_mm: f64,
    pub tailles_mm: Vec<(f64, f64)>,
}

fn nombre(v: &Value, ch: &str) -> Result<f64, String> {
    match v.as_f64() {
        Some(x) if x.is_finite() && x.abs() <= 1.0e7 => Ok(x),
        _ => Err(format!("{} : nombre attendu", ch)),
    }
}

fn point(v: &Value, ch: &str) -> Result<P, String> {
    match v.as_array() {
        Some(a) if a.len() == 2 => Ok(p(nombre(&a[0], ch)?, nombre(&a[1], ch)?)),
        _ => Err(format!("{} : point [x, y] attendu", ch)),
    }
}

fn polygone(v: &Value, ch: &str) -> Result<Vec<P>, String> {
    let a = v.as_array().ok_or_else(|| format!("{} : liste de points attendue", ch))?;
    let pts = a.iter().enumerate().map(|(i, q)| point(q, &format!("{}[{}]", ch, i))).collect::<Result<Vec<P>, String>>()?;
    if pts.len() < 3 {
        return Err(format!("{} : au moins 3 sommets attendus", ch));
    }
    Ok(pts)
}

fn regle(r: &Value, k: &str, defaut: Option<f64>) -> Result<f64, String> {
    let ch = format!("carte.json : regles.{}", k);
    match r.get(k) {
        None | Some(Value::Null) => defaut.ok_or_else(|| format!("{} manquant", ch)),
        Some(v) => {
            let x = nombre(v, &ch)?;
            if x < 0.0 {
                return Err(format!("{} : valeur negative", ch));
            }
            Ok(x)
        }
    }
}

/// carte.json (format carte_serigraphie/1) : geometrie (mils) et regles (mm)
pub fn lire_carte(f: &Path) -> Result<(Carte, Regles), String> {
    let n = nom(f);
    let t = std::fs::read_to_string(f).map_err(|_| format!("{} : fichier absent ou illisible", n))?;
    let v: Value = serde_json::from_str(&t).map_err(|e| format!("{} : JSON invalide (ligne {}, colonne {})", n, e.line(), e.column()))?;
    if !v.is_object() {
        return Err(format!("{} : objet attendu", n));
    }
    match v.get("format").and_then(|x| x.as_str()) {
        Some(x) if x == "carte_serigraphie/1" || x.starts_with("carte_serigraphie/1.") => {}
        _ => return Err(format!("{} : format carte_serigraphie/1 attendu", n)),
    }
    if let Some(u) = v.get("unites") {
        if u.as_str() != Some("mil") {
            return Err(format!("{} : unites \"mil\" attendues", n));
        }
    }
    let contour = polygone(v.get("contour").unwrap_or(&Value::Null), "carte.json : contour")?;
    let mut decoupes = Vec::new();
    if let Some(l) = v.get("decoupes") {
        let a = l.as_array().ok_or_else(|| "carte.json : decoupes : liste attendue".to_string())?;
        for (i, d) in a.iter().enumerate() {
            decoupes.push(polygone(d, &format!("carte.json : decoupes[{}]", i))?);
        }
    }
    let mut trous = Vec::new();
    if let Some(l) = v.get("trous") {
        let a = l.as_array().ok_or_else(|| "carte.json : trous : liste attendue".to_string())?;
        for (i, e) in a.iter().enumerate() {
            let ch = format!("carte.json : trous[{}]", i);
            match e.as_array() {
                Some(t) if t.len() == 3 => {
                    let r = nombre(&t[2], &ch)?;
                    if r < 0.0 {
                        return Err(format!("{} : rayon negatif", ch));
                    }
                    trous.push((p(nombre(&t[0], &ch)?, nombre(&t[1], &ch)?), r));
                }
                _ => return Err(format!("{} : [x, y, rayon] attendu", ch)),
            }
        }
    }
    let r = v.get("regles").filter(|x| x.is_object()).ok_or_else(|| "carte.json : regles manquantes".to_string())?;
    let mut tailles = Vec::new();
    let tl = r.get("tailles_mm").and_then(|x| x.as_array()).ok_or_else(|| "carte.json : regles.tailles_mm : liste attendue".to_string())?;
    for (i, e) in tl.iter().enumerate() {
        let ch = format!("carte.json : regles.tailles_mm[{}]", i);
        match e.as_array() {
            Some(t) if t.len() == 2 => {
                let (h, w) = (nombre(&t[0], &ch)?, nombre(&t[1], &ch)?);
                if h <= 0.0 || w <= 0.0 || w >= h {
                    return Err(format!("{} : 0 < epaisseur < hauteur attendu", ch));
                }
                tailles.push((h, w));
            }
            _ => return Err(format!("{} : [hauteur, epaisseur] attendu", ch)),
        }
    }
    if tailles.is_empty() {
        return Err("carte.json : regles.tailles_mm : au moins une taille attendue".to_string());
    }
    let regles = Regles {
        garde_masque_mm: regle(r, "garde_masque_mm", None)?,
        garde_bord_mm: regle(r, "garde_bord_mm", None)?,
        garde_trait_mm: regle(r, "garde_trait_mm", Some(0.05))?,
        garde_texte_mm: regle(r, "garde_texte_mm", Some(0.1))?,
        tailles_mm: tailles,
    };
    Ok((Carte::new(contour, decoupes, trous), regles))
}

/// options de la ligne de commande
#[derive(Clone, Debug)]
pub struct Options {
    pub entree: PathBuf,
    pub sortie: PathBuf,
    pub carte: PathBuf,
    pub graine: u64,
    pub chaines: usize,
    pub mouvements: usize,
    pub threads: usize,
    pub masque: bool,
    pub marge: f64,
    pub commentaires_visibles: bool,
}

fn entier<T: std::str::FromStr>(opt: &str, v: &str) -> Result<T, String> {
    v.trim().parse::<T>().map_err(|_| format!("{} : entier positif attendu", opt))
}

pub fn analyser(args: &[String]) -> Result<Options, String> {
    let (mut entree, mut sortie, mut carte) = (None, None, None);
    let mut o = Options {
        entree: PathBuf::new(),
        sortie: PathBuf::new(),
        carte: PathBuf::new(),
        graine: 1,
        chaines: 16,
        mouvements: 20_000_000,
        threads: 0,
        masque: false,
        marge: 0.02,
        commentaires_visibles: false,
    };
    let connues = ["--entree", "--sortie", "--carte", "--graine", "--chaines", "--mouvements", "--threads", "--reference", "--marge"];
    let mut i = 0;
    while i < args.len() {
        let a = args[i].as_str();
        if a == "--commentaires-visibles" {
            o.commentaires_visibles = true;
            i += 1;
            continue;
        }
        if !connues.contains(&a) {
            return Err(format!("option inconnue : {}", a.chars().take(40).collect::<String>()));
        }
        let v = args.get(i + 1).ok_or_else(|| format!("{} : valeur manquante", a))?.clone();
        match a {
            "--entree" => entree = Some(PathBuf::from(v)),
            "--sortie" => sortie = Some(PathBuf::from(v)),
            "--carte" => carte = Some(PathBuf::from(v)),
            "--graine" => o.graine = entier(a, &v)?,
            "--chaines" => o.chaines = entier(a, &v)?,
            "--mouvements" => o.mouvements = entier(a, &v)?,
            "--threads" => o.threads = entier(a, &v)?,
            "--reference" => {
                o.masque = match v.as_str() {
                    "masque" => true,
                    "cuivre" => false,
                    _ => return Err("--reference : cuivre ou masque attendu".to_string()),
                }
            }
            _ => {
                o.marge = match v.trim().parse::<f64>() {
                    Ok(x) if x.is_finite() && (0.0..=5.0).contains(&x) => x,
                    _ => return Err("--marge : nombre de 0 a 5 (mm) attendu".to_string()),
                }
            }
        }
        i += 2;
    }
    if o.chaines == 0 {
        return Err("--chaines : au moins 1".to_string());
    }
    o.entree = entree.ok_or_else(|| "option --entree obligatoire (dossier des fichiers d'entree)".to_string())?;
    o.sortie = sortie.ok_or_else(|| "option --sortie obligatoire (fichier de resultat)".to_string())?;
    o.carte = carte.ok_or_else(|| "option --carte obligatoire (geometrie et regles de la carte)".to_string())?;
    Ok(o)
}

/// nombre de fils explicite : --threads, sinon le nombre de coeurs disponibles
pub fn nombre_fils(demande: usize) -> usize {
    if demande > 0 {
        demande
    } else {
        std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1)
    }
}

/// calcul complet (rognage, candidats, recuit) : rend le document de sortie
pub fn calculer(d: &Donnees, carte: &Carte, rg_: &Regles, o: &Options) -> Value {
    let t0 = Instant::now();
    println!("donnees : {} pastilles, {} pistes, {} arcs, {} textes, {} composants, {} vias",
             d.pastilles.len(), d.pistes.len(), d.arcs.len(), d.textes.len(), d.composants.len(), d.vias.len());

    let garde = mm(rg_.garde_masque_mm + o.marge);
    let masque = o.masque;
    let pr = Params {
        garde_pastille: garde,
        masque,
        garde_trait: mm(rg_.garde_trait_mm),
        garde_texte: mm(rg_.garde_texte_mm),
        garde_bord: mm(rg_.garde_bord_mm),
        niveaux: rg_.tailles_mm.iter().map(|&(h, w)| (mm(h), mm(w))).collect(),
        ecarts: [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0].iter().map(|&x| mm(x)).collect(),
        pas: mm(0.1),
        k_max: 3000,
        chaines: o.chaines,
        mouvements: o.mouvements,
        graine: o.graine,
        cout_masque: 150.0,
        cout_conflit: 1000.0,
        cout_align: 0.8,
        cout_ambigu: 15.0,
        cout_corps: 6.0,
        cout_niveau: 1.5,
        cout_vertical: 0.5,
        cout_via: 0.6,
        marge_ambigu: mm(0.05),
    };

    // grilles des pastilles par face
    let pas_g = mm(1.0);
    let mut g_pastilles = [Grille::new(pas_g), Grille::new(pas_g)];
    for (k, q) in d.pastilles.iter().enumerate() {
        let s = if masque { &q.masque } else { &q.cuivre };
        for f in 0..2 {
            if q.faces[f] {
                g_pastilles[f].ajoute(&s.bb, k);
            }
        }
    }

    // 1. rognage des traits
    let fleche = 0.25;
    let rg = {
        let gd = rognage::Gardes { pastilles: &d.pastilles, grilles: [&g_pastilles[0], &g_pastilles[1]], masque, garde };
        rognage::rogner(&d, &gd, mm(0.1), fleche)
    };
    println!("rognage : {} pistes et {} arcs modifies -> {} morceaux ({:.1} s)", rg.pistes_sup.len(), rg.arcs_sup.len(),
             rg.ajouts.len(), t0.elapsed().as_secs_f64());

    // traits finaux (obstacles des textes)
    let mut traits: [Vec<Forme>; 2] = [Vec::new(), Vec::new()];
    let sup_p: std::collections::HashSet<usize> = rg.pistes_sup.iter().copied().collect();
    let sup_a: std::collections::HashSet<usize> = rg.arcs_sup.iter().copied().collect();
    for (k, t) in d.pistes.iter().enumerate() {
        if !sup_p.contains(&k) {
            traits[t.face.idx()].push(Forme::seg(t.a, t.b, t.w / 2.0));
        }
    }
    for m in &rg.ajouts {
        traits[m.face.idx()].push(Forme::seg(m.a, m.b, m.w / 2.0));
    }
    for (k, a) in d.arcs.iter().enumerate() {
        if !sup_a.contains(&k) {
            let pts = arc_points(a.c, a.r, a.a0, a.a1, fleche);
            for s in pts.windows(2) {
                traits[a.face.idx()].push(Forme::seg(s[0], s[1], a.w / 2.0 + fleche));
            }
        }
    }
    // textes conserves visibles : textes libres (hors composant) des couches de serigraphie (+ commentaires)
    let mut textes_fixes: [Vec<Forme>; 2] = [Vec::new(), Vec::new()];
    for t in &d.textes {
        if let Some(f) = t.face {
            if t.designateur || (t.commentaire && !o.commentaires_visibles) {
                continue;
            }
            let fo = if t.ttf { Forme::rect(t.bounds, 0.0) } else { forme_texte(&t.texte, t.x, t.y, t.h, t.w, t.rot, t.miroir) };
            textes_fixes[f.idx()].push(fo);
        }
    }
    let mut g_traits = [Grille::new(pas_g), Grille::new(pas_g)];
    let mut g_textes = [Grille::new(pas_g), Grille::new(pas_g)];
    for f in 0..2 {
        for (k, s) in traits[f].iter().enumerate() {
            g_traits[f].ajoute(&s.bb, k);
        }
        for (k, s) in textes_fixes[f].iter().enumerate() {
            g_textes[f].ajoute(&s.bb, k);
        }
    }
    let mut g_corps = [Grille::new(pas_g), Grille::new(pas_g)];
    for (k, c) in d.composants.iter().enumerate() {
        g_corps[c.face.idx()].ajoute(&c.corps, k);
    }
    // vias : ouverture de masque (non bouches) ~ diametre + 4 mil d'expansion (hypothese)
    let vias: Vec<Forme> = d.vias.iter().map(|v| Forme::seg(v.c, v.c, v.d / 2.0 + 4.0)).collect();
    let mut g_vias = Grille::new(pas_g);
    for (k, v) in vias.iter().enumerate() {
        g_vias.ajoute(&v.bb, k);
    }
    println!("carte : contour de {} sommets, {} decoupes, {} trous", carte.contour.len(), carte.decoupes.len(), carte.trous.len());
    let monde = Monde { d, carte, traits, textes_fixes, g_pastilles, g_traits, g_textes, g_corps, vias, g_vias };

    // 2. candidats
    let t1 = Instant::now();
    let cands: Vec<Vec<Cand>> = (0..d.composants.len()).into_par_iter().map(|ic| candidats(&monde, ic, &pr)).collect();
    let sans: Vec<&str> = d.composants.iter().zip(&cands).filter(|(_, v)| v.is_empty()).map(|(c, _)| c.nom.as_str()).collect();
    println!("candidats : {} au total, {} composants sans place legale {:?} ({:.1} s)",
             cands.iter().map(|v| v.len()).sum::<usize>(), sans.len(), sans, t1.elapsed().as_secs_f64());

    // 3. recuit
    let t2 = Instant::now();
    let pb = Probleme::new(&cands, &d.composants, &pr);
    let (cout, s, couts) = pb.resoudre();
    println!("recuit : {} chaines x {} mouvements, cout retenu {:.2} (chaines : min {:.2} max {:.2}) ({:.1} s)", o.chaines, o.mouvements,
             cout, couts.iter().cloned().fold(f64::MAX, f64::min), couts.iter().cloned().fold(f64::MIN, f64::max),
             t2.elapsed().as_secs_f64());
    // garantie : aucun conflit restant (sinon on masque le moins bien loti)
    let mut s = s;
    loop {
        let mut pire: Option<usize> = None;
        for i in 0..s.len() {
            if s[i] == MASQUE {
                continue;
            }
            for &j in &pb.voisins[i] {
                if s[j] != MASQUE && pb.conflit(Some(&cands[i][s[i]]), Some(&cands[j][s[j]])) {
                    pire = Some(if cands[i].len() <= cands[j].len() { i } else { j });
                }
            }
        }
        match pire {
            Some(i) => s[i] = MASQUE,
            None => break,
        }
    }

    // 4. sortie
    let mut anciens: HashMap<&str, &Texte> = HashMap::new();
    for t in &d.textes {
        if t.designateur && t.face.is_some() {
            anciens.insert(t.comp.as_str(), t);
        }
    }
    let mut textes = Vec::new();
    let (mut n_dep, mut n_red, mut n_masq, mut n_amb, mut n_corps, mut n_vias) = (0, 0, 0, 0, 0, 0);
    let mut masques = Vec::new();
    let mut ambigus = Vec::new();
    let mut dans_corps = Vec::new();
    for (ic, c) in d.composants.iter().enumerate() {
        let anc = anciens.get(c.nom.as_str());
        let ancien = anc.map(|t| json!({"x": r3(t.x), "y": r3(t.y), "height": r3(t.h), "stroke_width": r3(t.w), "rotation": t.rot,
                                         "ttf": t.ttf, "layer": t.couche}));
        let layer = c.face.couche_seri();
        let miroir = c.face == Face::Dessous;
        if s[ic] == MASQUE {
            n_masq += 1;
            masques.push(c.nom.clone());
            let (x, y, h, w, r) = anc.map(|t| (t.x, t.y, t.h, t.w, t.rot)).unwrap_or((c.x, c.y, mm(0.8), mm(0.12), 0.0));
            textes.push(json!({"component": c.nom, "text": c.nom, "x": r3(x), "y": r3(y), "height": r3(h.max(mm(0.8))),
                "stroke_width": r3(w.max(mm(0.12))), "rotation": r, "layer": layer, "mirror": miroir, "hidden": true,
                "font": "stroke", "use_ttfonts": false, "ancien": ancien, "raison": "aucune place legale"}));
            continue;
        }
        let cd = &cands[ic][s[ic]];
        let deplace = anc.map(|t| (t.x - cd.x).abs() > 0.5 || (t.y - cd.y).abs() > 0.5 || t.rot != cd.rot).unwrap_or(true);
        if deplace {
            n_dep += 1;
        }
        if cd.niv > 0 {
            n_red += 1;
        }
        if cd.ambigu {
            n_amb += 1;
            ambigus.push(c.nom.clone());
        }
        if cd.dans_corps {
            n_corps += 1;
            dans_corps.push(c.nom.clone());
        }
        n_vias += cd.vias;
        textes.push(json!({"component": c.nom, "text": c.nom, "x": r3(cd.x), "y": r3(cd.y), "height": r3(cd.h),
            "stroke_width": r3(cd.w), "rotation": cd.rot, "layer": layer, "mirror": miroir, "hidden": false,
            "font": "stroke", "use_ttfonts": false, "taille_mm": r3(cd.h * MIL_MM), "cote": cd.cote, "vertical": cd.vertical,
            "boite_encre": [r3(cd.bb[0] + cd.w / 2.0), r3(cd.bb[1] + cd.w / 2.0), r3(cd.bb[2] - cd.w / 2.0), r3(cd.bb[3] - cd.w / 2.0)],
            "ambigu": cd.ambigu, "sur_son_corps": cd.dans_corps, "vias_touches": cd.vias, "auto_position_altium": c.auto_pos,
            "ancien": ancien}));
    }
    let pistes_supprimer: Vec<Value> = rg.pistes_sup.iter().map(|&k| {
        let t = &d.pistes[k];
        json!({"layer": t.face.couche_seri(), "x1": t.a.x, "y1": t.a.y, "x2": t.b.x, "y2": t.b.y, "width": t.w, "component": t.comp})
    }).collect();
    let arcs_supprimer: Vec<Value> = rg.arcs_sup.iter().map(|&k| {
        let a = &d.arcs[k];
        json!({"layer": a.face.couche_seri(), "xcenter": a.c.x, "ycenter": a.c.y, "radius": a.r, "start_angle": a.a0,
               "end_angle": a.a1, "width": a.w, "component": a.comp})
    }).collect();
    let pistes_ajouter: Vec<Value> = rg.ajouts.iter().map(|m| json!({"x1": r3(m.a.x), "y1": r3(m.a.y), "x2": r3(m.b.x), "y2": r3(m.b.y),
        "width": r3(m.w), "layer": m.face.couche_seri(), "component": m.comp})).collect();
    let mut retire: HashMap<String, f64> = HashMap::new();
    for (c, l) in &rg.retire {
        *retire.entry(c.clone()).or_default() += l;
    }
    let mut retire_v: Vec<(String, f64)> = retire.into_iter().collect();
    retire_v.sort_by(|a, b| a.0.cmp(&b.0));
    let stats = json!({
        "designateurs": d.composants.len(), "deplaces": n_dep, "reduits": n_red, "masques": n_masq,
        "ambigus": n_amb, "sur_leur_corps": n_corps, "vias_touches": n_vias,
        "liste_masques": masques, "liste_ambigus": ambigus, "liste_sur_leur_corps": dans_corps,
        "pistes_rognees": rg.pistes_sup.len(), "arcs_decoupes": rg.arcs_sup.len(), "morceaux_ajoutes": rg.ajouts.len(),
        "longueur_retiree_mm_par_composant": retire_v.iter().map(|(c, l)| json!([c, r3(l * MIL_MM)])).collect::<Vec<_>>(),
        "cout": r3(cout), "graine": o.graine, "chaines": o.chaines, "mouvements": o.mouvements,
        "reference": if masque { "masque" } else { "cuivre" }, "garde_mm": r3(rg_.garde_masque_mm + o.marge),
    });
    let doc = "Unites : mil (3 decimales), origine coin bas-gauche de la carte. Textes en police TRAIT d'Altium (UseTTFonts = false, \
police Default). Convention de position (calibree sur les Bounds exportes) : X, Y = ancre = debut de la ligne de base du texte \
(axe du premier trait, bas des majuscules) ; point d'encre (u le long du texte, v vers le haut) -> monde = (X, Y) + R(rotation).(m.u, v) \
avec m = -1 si mirror (face inferieure), +1 sinon. Donc texte du dessus a 0 deg : (X, Y) = coin bas-gauche ; texte miroir du dessous \
a 0 deg : (X, Y) = coin bas-DROIT vu de dessus. Sens de lecture : dessus 0 / 90 ; dessous (miroir) 0 / 270 (= 0 / 90 vu de dessous). \
Mettre la position du designateur en manuel (NameAutoPosition = Manual) avant d'appliquer. pistes_supprimer / arcs_supprimer : \
identifiees par layer + coordonnees exactes de l'export ; pistes_ajouter : morceaux conserves (rognes) a creer.";
    let out = json!({
        "_doc": doc, "unites": "mil", "commentaires_caches": !o.commentaires_visibles,
        "textes": textes, "pistes_supprimer": pistes_supprimer, "pistes_ajouter": pistes_ajouter,
        "arcs_supprimer": arcs_supprimer, "stats": stats,
    });
    println!("designateurs : {} places ({} deplaces), {} reduits, {} masques {:?}, {} ambigus, {} sur leur corps, {} vias touches",
             d.composants.len() - n_masq, n_dep, n_red, n_masq, masques, n_amb, n_corps, n_vias);
    println!("calcul : {:.1} s", t0.elapsed().as_secs_f64());
    out
}

/// execute le moteur ; erreur = (code, message sans chemin)
pub fn executer(o: &Options) -> Result<(), (i32, String)> {
    let d = charger(&o.entree).map_err(|m| (CODE_ENTREE, m))?;
    let (carte, regles) = lire_carte(&o.carte).map_err(|m| (CODE_ENTREE, m))?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(nombre_fils(o.threads))
        .stack_size(PILE_FIL)
        .build()
        .map_err(|_| (CODE_INTERNE, "erreur interne : creation des fils impossible".to_string()))?;
    let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| pool.install(|| calculer(&d, &carte, &regles, o))));
    let out = res.map_err(|_| (CODE_INTERNE, "erreur interne du moteur de serigraphie".to_string()))?;
    let texte = serde_json::to_string_pretty(&out).map_err(|_| (CODE_INTERNE, "erreur interne : resultat non serialisable".to_string()))?;
    let ecriture = || (CODE_ENTREE, format!("{} : ecriture impossible", nom(&o.sortie)));
    if let Some(dir) = o.sortie.parent() {
        if !dir.as_os_str().is_empty() {
            std::fs::create_dir_all(dir).map_err(|_| ecriture())?;
        }
    }
    let tmp = o.sortie.with_extension("json.tmp");
    std::fs::write(&tmp, texte).map_err(|_| ecriture())?;
    std::fs::rename(&tmp, &o.sortie).map_err(|_| ecriture())?;
    println!("ecrit {}", nom(&o.sortie));
    Ok(())
}

fn main() {
    // aucune trace de panique sur la console : une panique du calcul devient le code 4
    std::panic::set_hook(Box::new(|_| {}));
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() == 1 && args[0] == "--version" {
        // même forme que le moteur placeur_routage (contrôlée par placeur.exe --auto-test)
        println!("serigraphie {}+{}", env!("CARGO_PKG_VERSION"), option_env!("PLACEUR_COMMIT").unwrap_or("inconnu"));
        std::process::exit(0);
    }
    let code = match analyser(&args) {
        Err(m) => {
            eprintln!("serigraphie : erreur d'entree : {}", m);
            eprintln!("usage : serigraphie --entree DOSSIER --sortie RESULTAT.json --carte CARTE.json [options]");
            CODE_ENTREE
        }
        Ok(o) => match executer(&o) {
            Ok(()) => 0,
            Err((c, m)) => {
                eprintln!("serigraphie : {}{}", if c == CODE_ENTREE { "erreur d'entree : " } else { "" }, m);
                c
            }
        },
    };
    std::process::exit(code);
}
