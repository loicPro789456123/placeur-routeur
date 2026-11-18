//! Essai de performance (§4.6-3, étape 6 du lot E) : vérification stricte d'une carte synthétique de la taille de
//! METROID construite en mémoire (164 × 97 mm, 4 couches dont un plan GND ; 2 500 pastilles, 2 450 pistes,
//! 500 vias), en release et sur un fil (le module n'en lance aucun) ; durée affichée, au plus 2 s (objectif ≈ 1 s).
//! Le gabarit des objets (pièce, pastille, réseau, règles) est pris dans le cas 011 du corpus.
//!
//! Commande : `cargo test --release --offline -j 4 --manifest-path <caisse>/Cargo.toml --test performance
//! -- --nocapture`.

use serde_json::{json, Value};
use std::fs;
use std::path::Path;
use std::time::Instant;
use validite_rs::validite::{verifier_routage, Mode};

const MIL: f64 = 0.0254;

fn lire(nom: &str) -> Value {
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("validite").join("011_isolement_piste_pastille").join(nom);
    serde_json::from_str(&fs::read_to_string(p).expect("lecture")).expect("JSON")
}

fn mil(v_mm: f64) -> f64 {
    (v_mm / MIL * 1000.0).round() / 1000.0
}

/// Carte synthétique : 25 rangées de 50 pièces à deux pastilles CMS (0,8 × 0,8 mm, entraxe 1,2 mm) ; la pastille 2
/// d'une pièce et la pastille 1 de la suivante forment un réseau, relié par deux pistes alignées de 0,254 mm ;
/// 500 vias GND dans le plan de MidLayer1, entre les rangées. Rend (données, placement, routage).
fn carte_metroid() -> (Value, Value, Value) {
    let mut d = lire("donnees_placeur.json");
    let gabarit_piece = d["composants"][0].clone();
    let gabarit_pastille = gabarit_piece["pastilles"][0].clone();
    let gabarit_reseau = d["reseaux"][0].clone();
    let (l, h) = (164.0, 97.0);
    d["carte"]["contour"] = json!([[0.0, 0.0], [l, 0.0], [l, h], [0.0, h]]);
    d["carte"]["boite"] = json!([0.0, 0.0, l, h]);
    let couche = |nom: &str, rang: u64, role: &str, zones: Value| {
        json!({"nom": nom, "rang": rang, "role": role, "zones_plan": zones, "epaisseur_cuivre_mm": 0.035,
               "direction_preferee": null})
    };
    d["couches"] = json!([
        couche("TopLayer", 0, "routage", json!([])),
        couche("MidLayer1", 1, "plan", json!([{"reseau": "GND", "contour": [[0.5, 0.5], [l - 0.5, 0.5], [l - 0.5, h - 0.5],
                                                                          [0.5, h - 0.5]], "trous": []}])),
        couche("MidLayer2", 2, "routage", json!([])),
        couche("BottomLayer", 3, "routage", json!([])),
    ]);
    let (colonnes, rangees) = (50usize, 25usize);
    let (pas_x, pas_y) = (3.1, 3.6);
    let mut composants = Vec::new();
    let mut pieces_placees = Vec::new();
    let mut reseaux = Vec::new();
    let mut pistes = Vec::new();
    let reseau = |nom: String, role: &str| {
        let mut r = gabarit_reseau.clone();
        r["nom"] = nom.into();
        r["role"] = role.into();
        r["couches_permises"] = json!(["TopLayer", "MidLayer2", "BottomLayer"]);
        r
    };
    for ry in 0..rangees {
        let y = 4.0 + ry as f64 * pas_y;
        for cx in 0..colonnes {
            let x = 4.0 + cx as f64 * pas_x;
            let nom = format!("R{ry}_{cx}");
            let gauche = if cx == 0 { format!("A{ry}") } else { format!("N{ry}_{}", cx - 1) };
            let droite = if cx == colonnes - 1 { format!("B{ry}") } else { format!("N{ry}_{cx}") };
            if cx == 0 {
                reseaux.push(reseau(gauche.clone(), "signal"));
            }
            reseaux.push(reseau(droite.clone(), "signal"));
            let mut pastilles = Vec::new();
            for (k, (lx, net)) in [(-0.6, &gauche), (0.6, &droite)].into_iter().enumerate() {
                let mut p = gabarit_pastille.clone();
                p["id"] = format!("{nom}#{k}").into();
                p["nom"] = format!("{}", k + 1).into();
                p["reseau"] = net.as_str().into();
                p["lx"] = lx.into();
                p["ly"] = 0.0.into();
                p["largeur"] = 0.8.into();
                p["hauteur"] = 0.8.into();
                p["hw"] = 0.4.into();
                p["hh"] = 0.4.into();
                pastilles.push(p);
            }
            let mut c = gabarit_piece.clone();
            c["nom"] = nom.as_str().into();
            c["pastilles"] = Value::Array(pastilles);
            c["lbb"] = json!([-1.0, -0.4, 1.0, 0.4]);
            c["pose_actuelle"] = json!([x, y, 0.0, false]);
            c["pose_cible"] = json!([x, y, 0.0, false]);
            composants.push(c);
            pieces_placees.push(json!({"designator": nom, "side": "top", "x": mil(x), "y": mil(y), "rotation": 0.0,
                                       "statut": "fixe"}));
            if cx + 1 < colonnes {
                let (a, b) = (x + 0.6, x + pas_x - 0.6);
                let m = 0.5 * (a + b);
                for (x1, x2) in [(a, m), (m, b)] {
                    pistes.push(json!({"x1": mil(x1), "y1": mil(y), "x2": mil(x2), "y2": mil(y), "width": 10.0,
                                       "layer": "TopLayer", "net_name": droite}));
                }
            }
        }
    }
    reseaux.push(reseau("GND".into(), "plan"));
    let mut vias = Vec::new();
    'vias: for ry in 0..rangees - 1 {
        for k in 0..colonnes - 1 {
            if vias.len() == 500 {
                break 'vias;
            }
            let (x, y) = (4.0 + k as f64 * pas_x + 0.5 * pas_x, 4.0 + ry as f64 * pas_y + 0.5 * pas_y);
            vias.push(json!({"x": mil(x), "y": mil(y), "size": 23.622, "hole_size": 11.811, "net": "GND",
                             "de": "TopLayer", "a": "BottomLayer"}));
        }
    }
    d["composants"] = Value::Array(composants);
    d["reseaux"] = Value::Array(reseaux);
    let mut p = lire("placements_routage.json");
    p["pieces"] = Value::Array(pieces_placees);
    let mut r = lire("routage_detaille.json");
    r["couches_routage"] = json!(["TopLayer", "MidLayer2", "BottomLayer"]);
    r["pistes"] = Value::Array(pistes);
    r["vias"] = Value::Array(vias);
    (d, p, r)
}

#[test]
fn verification_stricte_taille_metroid() {
    let (d, p, r) = carte_metroid();
    assert_eq!(d["composants"].as_array().unwrap().len() * 2, 2500);
    assert_eq!(r["pistes"].as_array().unwrap().len(), 2450);
    assert_eq!(r["vias"].as_array().unwrap().len(), 500);
    let debut = Instant::now();
    let v = verifier_routage(&d, &p, &r, None, Mode::Strict).expect("vérification");
    let duree = debut.elapsed().as_secs_f64();
    println!("vérification stricte (2 500 pastilles, 2 450 pistes, 500 vias, 4 couches) : {duree:.3} s");
    assert!(v.liaisons_ouvertes.is_empty(), "{} liaisons ouvertes", v.liaisons_ouvertes.len());
    assert!(v.violations.is_empty(), "{} violations : {:?}", v.violations.len(), v.violations.first());
    assert_eq!(v.reseaux_a_router, 25 * 49);
    assert!(duree <= 2.0, "vérification stricte en {duree:.3} s (> 2 s)");
}
