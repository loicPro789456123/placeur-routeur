//! Sous-commandes `campagne` et `router` (§5.1) : modèle interne depuis `donnees_placeur/2` (`adaptateur.rs`), cœur de
//! `9b5b5a3` (`campagne.rs`), sorties du contrat et vérification finale.
//!
//! Sorties dans le dossier `--sortie` : `placements_routage.json` (`placements/2`), `routage_detaille.json`
//! (`routage/2`, si un routage existe), `stats.json` (`stats/2`, écrit en dernier). `stats.valide` est le verdict de
//! `validite.rs` (mode strict, contrôle de placement compris) : jamais « valide » refusé par la vérification.

use crate::adaptateur;
use crate::campagne::{self, Bilan, Contexte, Fin};
use crate::detail::Detail;
use crate::donnees::{self, Donnees};
use crate::fichiers;
use crate::finition_exacte;
use crate::parametres::Lus;
use crate::sortie;
use crate::validite::{self, Mode};
use crate::verification::{Reference, Verificateur};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::Instant;

/// Phases de `stats.execution.temps_phases_s` (§2.7), toutes présentes.
const PHASES: [&str; 9] =
    ["chargement", "placement_initial", "recuit", "routage_detaille", "boucle", "conjointe", "finition", "verification", "ecriture"];

/// Entrées contrôlées d'une exécution.
pub struct Entrees<'e> {
    pub valeur_donnees: &'e Value,
    pub donnees: &'e Donnees,
    pub sha_donnees: String,
    pub sha_parametres: String,
    pub lus: &'e Lus,
    pub fils: usize,
    pub memoire_estimee_mo: f64,
    pub connexions: u64,
    pub evenements: bool,
    pub arret: Option<PathBuf>,
}

fn ecrire(dir: &Path, nom: &str, v: &Value) -> Result<(), String> {
    fichiers::ecrire_json_atomique(&dir.join(nom), v)
}

fn r1(v: f64) -> f64 {
    if v.is_finite() {
        (v * 10.0).round() / 10.0
    } else {
        0.0
    }
}

/// Mesures du routage (`stats.mesures`).
fn mesures(d: Option<&Detail>) -> Value {
    let d = match d {
        Some(d) => d,
        None => {
            return json!({"longueur_mm": 0.0, "vias": 0, "vias_doubles": 0, "segments": 0, "longueur_retrecie_mm": 0.0,
                "resistance_puissance_mohm": 0.0})
        }
    };
    let m = d.m;
    let actif = |c: u8| m.couches_actives[c as usize];
    let longueur: f64 = d
        .nets
        .iter()
        .flat_map(|r| r.segs.iter())
        .filter(|s| actif(s.couche))
        .map(|s| ((s.x2 - s.x1).powi(2) + (s.y2 - s.y1).powi(2)).sqrt())
        .sum();
    let mut r_puiss = 0.0;
    for (n, r) in d.nets.iter().enumerate() {
        if matches!(m.nets[n].role.as_str(), "puissance" | "alimentation") {
            r_puiss += r.carres * sortie::MOHM_CARRE;
        }
    }
    json!({"longueur_mm": r1(longueur), "vias": d.nets.iter().map(|r| r.vias.len()).sum::<usize>(),
        "vias_doubles": d.nets.iter().map(|r| r.vias_doubles).sum::<usize>(),
        "segments": d.nets.iter().flat_map(|r| r.segs.iter()).filter(|s| actif(s.couche)).count(),
        "longueur_retrecie_mm": r1(d.nets.iter().map(|r| r.neck_mm).sum::<f64>()),
        "resistance_puissance_mohm": (r_puiss * 100.0).round() / 100.0})
}

/// Résultat de la vérification finale : verdict (avec `placement`), validité, cause, coût commun.
struct Verification {
    verdict: Value,
    valide: bool,
    cause: &'static str,
    cout: Option<f64>,
}

fn verifier(en: &Entrees, placements: &Value, routage: &Value) -> Result<Verification, String> {
    let v = Reference;
    let placement = v.placement(en.valeur_donnees, placements, true)?;
    let verdict = v.routage(en.valeur_donnees, placements, routage, None, Mode::Strict)?;
    let cout = validite::cout_commun(en.valeur_donnees, routage, &verdict).cout;
    let mut j = verdict.en_json();
    if let Value::Object(o) = &mut j {
        o.insert("placement".to_string(), placement.en_json());
    }
    let cause = if !placement.valide {
        "placement invalide"
    } else if !verdict.liaisons_ouvertes.is_empty() {
        "liaisons ouvertes"
    } else if !verdict.violations.is_empty() || !verdict.valide {
        "violations"
    } else {
        ""
    };
    Ok(Verification { verdict: j, valide: cause.is_empty(), cause, cout: if cout.is_finite() { Some(cout) } else { None } })
}

/// Exécute `campagne` (graine donnée) ou `router` (poses données) ; rend 0, ou 3 sur arrêt demandé.
pub fn executer(en: &Entrees, graine: Option<u64>, poses: Option<Vec<donnees::Pose>>, dir: &Path) -> Result<i32, String> {
    let t0 = Instant::now();
    std::fs::create_dir_all(dir).map_err(|e| format!("dossier de sortie : {}", fichiers::texte_erreur_es(&e)))?;
    let fichier_arret = en.arret.clone();
    let arret = move || fichier_arret.as_ref().map(|f| f.exists()).unwrap_or(false);
    let mut journal = |s: String| println!("{}", s);
    let connexions = en.connexions as f64;
    let seuil = (en.lus.parametres.campagne.seuil_abandon_echecs as f64)
        .max((en.lus.parametres.campagne.seuil_abandon_part * connexions).ceil()) as usize;
    let mut cx = Contexte::new(&mut journal, &arret, en.evenements, t0, seuil);
    cx.debut("chargement");
    let ad = adaptateur::construire(en.donnees, en.lus.parametres.clone())?;
    for a in &ad.avertissements {
        cx.log(format!("avertissement : {}", a));
    }
    let m = ad.modele;
    cx.log(format!(
        "{} pièces ({} mobiles), {} réseaux, couches de routage {:?}, plans {:?}",
        m.comps.len(),
        m.mobiles.len(),
        m.nets.len(),
        (0..3).filter(|&l| m.couches_actives[l]).map(|l| m.noms_couches[l].as_str()).collect::<Vec<_>>(),
        m.nets.iter().filter(|n| n.plan).map(|n| n.nom.as_str()).collect::<Vec<_>>()
    ));
    cx.fin("chargement");
    let mut bilan: Bilan = match &poses {
        None => campagne::run_campagne(&m, graine.unwrap_or(0), &mut cx),
        Some(p) => {
            let e = adaptateur::etat_depuis(&m, p);
            campagne::run_router(&m, &e, &mut cx)
        }
    };
    if let Some(d) = bilan.detail.as_mut() {
        finition_exacte::appliquer(&mut d.nets);
    }
    // vérification finale sur la géométrie telle qu'elle est écrite
    cx.debut("verification");
    let placements = bilan.etat.as_ref().map(|e| sortie::placement_json(&m, e));
    let routage = bilan.detail.as_ref().map(sortie::routage_json);
    let verif = match (&placements, &routage) {
        (Some(p), Some(r)) => Some(verifier(en, p, r)?),
        _ => None,
    };
    cx.fin("verification");
    cx.debut("ecriture");
    let _ = std::fs::remove_file(dir.join("stats.json"));
    if let Some(p) = &placements {
        ecrire(dir, "placements_routage.json", p)?;
    }
    if let Some(r) = &routage {
        ecrire(dir, "routage_detaille.json", r)?;
    }
    cx.fin("ecriture");
    let arrete = bilan.fin == Fin::ArretDemande;
    let (valide, cause, validite_json, cout) = match &verif {
        Some(v) => {
            let cause = if !bilan.cause.is_empty() { bilan.cause } else { v.cause };
            (v.valide && bilan.cause.is_empty() && !arrete, cause, v.verdict.clone(), v.cout)
        }
        None => (false, if bilan.cause.is_empty() { "non route" } else { bilan.cause }, Value::Null, None),
    };
    let echecs: Vec<String> = bilan.detail.as_ref().map(|d| d.nets.iter().flat_map(|r| r.diag.iter().cloned()).collect()).unwrap_or_default();
    // paramètres effectifs : sans `campagne.threads` (§2.7 : le nombre de fils est dans `execution.fils`)
    let mut effectifs = en.lus.valeur.clone();
    if let Some(c) = effectifs.get_mut("campagne").and_then(Value::as_object_mut) {
        c.remove("threads");
    }
    let mut tp = serde_json::Map::new();
    for p in PHASES {
        let v = cx.phases.iter().find(|q| q.0 == p).map(|q| q.1).unwrap_or(0.0);
        tp.insert(p.to_string(), json!((v.max(0.0) * 1000.0).round() / 1000.0));
    }
    let st = json!({
        "format": "stats/2", "version_moteur": crate::version_moteur(), "graine": graine.unwrap_or(0),
        "fin": bilan.fin.nom(), "valide": valide, "cause": cause, "piece": null, "raison": null,
        "validite": validite_json, "cout_commun": cout,
        "connexions_echouees_routeur": bilan.detail.as_ref().map(|d| d.total_ko()).unwrap_or(0),
        "echecs": echecs, "recuit": bilan.recuit, "boucle": bilan.boucle, "conjointe": bilan.conjointe, "finition": bilan.finition,
        "convertisseurs": bilan.convertisseurs, "mesures": mesures(bilan.detail.as_ref()),
        "empreintes": {"donnees_sha256": en.sha_donnees, "parametres_sha256": en.sha_parametres},
        "parametres_effectifs": effectifs,
        "execution": {"temps_phases_s": tp, "temps_total_s": (t0.elapsed().as_secs_f64() * 1000.0).round() / 1000.0,
            "fils": crate::nombre_fils(en.fils), "plafond_temps_atteint": bilan.fin == Fin::PlafondTemps,
            "conjointe_plafond_atteint": bilan.plafond_conjointe, "memoire_estimee_mo": en.memoire_estimee_mo, "memoire_pic_mo": 0.0}
    });
    ecrire(dir, "stats.json", &st)?;
    println!(
        "{} terminée : fin {}, valide {}, cause « {} », coût commun {}, {} connexion(s) en échec selon le routeur ({:.1} s)",
        if poses.is_some() { "routage" } else { "campagne" },
        bilan.fin.nom(),
        valide,
        cause,
        cout.map(|c| format!("{:.1}", c)).unwrap_or_else(|| "-".to_string()),
        st["connexions_echouees_routeur"],
        t0.elapsed().as_secs_f64()
    );
    Ok(if arrete { 3 } else { 0 })
}
