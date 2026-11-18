//! Cartes d'essai au format `donnees_placeur/2` (plan du lot A, §1 point 4) : la structure de l'exemple de §2.5,
//! complétée pour que chaque liste de références croisées de §2.1 porte au moins un élément, avec des noms neutres
//! (couches `Dessus`, `Int1`…, réseaux `MASSE`, `VIN`, `SORTIE`, `NOEUD`…) et des dimensions sans rapport avec une
//! carte réelle. Variantes : 2, 4 ou 6 couches (plans, plan scindé), avec ou sans convertisseur.
//!
//! `cargo test --release --offline -- --ignored essais::ecrire_cartes` les écrit dans `target/cartes_essai/` (non
//! suivi), avec un placement (`placements/2` aux poses cibles) et un routage vide (`routage/2`), pour les essais de
//! la ligne de commande.

use serde_json::{json, Value};

/// Noms des couches de cuivre d'une variante, de haut en bas, et indices des couches de plan.
fn noms_couches(n: usize) -> (Vec<&'static str>, Vec<usize>) {
    match n {
        2 => (vec!["Dessus", "Dessous"], vec![]),
        6 => (vec!["Dessus", "Int1", "Int2", "Int3", "Int4", "Dessous"], vec![1, 4]),
        _ => (vec!["Dessus", "Int1", "Int2", "Dessous"], vec![1]),
    }
}

const CONTOUR: [[f64; 2]; 4] = [[0.0, 0.0], [40.0, 0.0], [40.0, 30.0], [0.0, 30.0]];

struct Pad {
    nom: &'static str,
    reseau: &'static str,
    lx: f64,
    ly: f64,
    l: f64,
    h: f64,
    traversante: bool,
}

const fn cms(nom: &'static str, reseau: &'static str, lx: f64, ly: f64, l: f64, h: f64) -> Pad {
    Pad { nom, reseau, lx, ly, l, h, traversante: false }
}

const fn tr(nom: &'static str, reseau: &'static str, lx: f64, ly: f64) -> Pad {
    Pad { nom, reseau, lx, ly, l: 1.7, h: 1.7, traversante: true }
}

fn pastille(des: &str, k: usize, p: &Pad, toutes: &[&str], classe: usize) -> Value {
    let couches: Vec<&str> = if p.traversante { toutes.to_vec() } else { vec![toutes[0]] };
    let trou = if p.traversante {
        json!({"diametre": 1.0, "forme": "rond", "longueur": 0.0, "rotation": 0.0, "decalage": [0.0, 0.0], "metallise": true})
    } else {
        Value::Null
    };
    json!({"id": format!("{}#{}", des, k), "nom": p.nom, "reseau": p.reseau, "lx": p.lx, "ly": p.ly,
           "forme": if p.traversante { "rond" } else { "rect" }, "largeur": p.l, "hauteur": p.h, "rotation": 0.0,
           "rayon_coin": 0.0, "points": [], "couches": couches,
           "face": if p.traversante { "toutes" } else { "composant" }, "trou": trou, "traversante": p.traversante,
           "expansion_masque": 0.05, "pile": null, "via_dans_pastille_permis": p.traversante,
           "classe_isolement": classe, "hw": p.l / 2.0, "hh": p.h / 2.0, "th": p.traversante})
}

struct Piece {
    nom: &'static str,
    nature: &'static str,
    x: f64,
    y: f64,
    mobile: bool,
    pas_fin: bool,
    pastilles: Vec<Pad>,
}

fn deux(nom: &'static str, nature: &'static str, x: f64, y: f64, a: &'static str, b: &'static str) -> Piece {
    Piece {
        nom,
        nature,
        x,
        y,
        mobile: true,
        pas_fin: false,
        pastilles: vec![cms("1", a, -0.5, 0.0, 0.6, 0.6), cms("2", b, 0.5, 0.0, 0.6, 0.6)],
    }
}

fn pieces(convertisseur: bool) -> Vec<Piece> {
    let mut v = vec![
        Piece {
            nom: "U1",
            nature: "circuit",
            x: 15.0,
            y: 15.0,
            mobile: true,
            pas_fin: true,
            pastilles: vec![
                cms("1", "VIN", -1.4, -0.75, 0.6, 0.25),
                cms("2", "SIG1", -1.4, -0.25, 0.6, 0.25),
                cms("3", "SIG2", -1.4, 0.25, 0.6, 0.25),
                cms("4", "MASSE", -1.4, 0.75, 0.6, 0.25),
                cms("5", "GH", 1.4, 0.75, 0.6, 0.25),
                cms("6", "BOOT", 1.4, 0.25, 0.6, 0.25),
                cms("7", "DP", 1.4, -0.25, 0.6, 0.25),
                cms("8", "DN", 1.4, -0.75, 0.6, 0.25),
                cms("9", "MASSE", 0.0, 0.0, 1.2, 1.6),
            ],
        },
        Piece {
            nom: "Q1",
            nature: "transistor",
            x: 22.0,
            y: 18.0,
            mobile: true,
            pas_fin: false,
            pastilles: vec![
                cms("1", "VIN", -1.0, -0.6, 0.8, 0.6),
                cms("2", "NOEUD", 1.0, -0.6, 0.8, 0.6),
                cms("3", "GH", 0.0, 0.9, 0.8, 0.6),
            ],
        },
        deux("D1", "diode", 22.0, 12.0, "NOEUD", "MASSE"),
        deux("L3", "inductance", 28.0, 15.0, "NOEUD", "SORTIE"),
        deux("C1", "condensateur", 18.0, 22.0, "VIN", "MASSE"),
        deux("C2", "condensateur", 32.0, 10.0, "SORTIE", "MASSE"),
        deux("C3", "condensateur", 12.0, 22.0, "VIN", "MASSE"),
        deux("C4", "condensateur", 19.0, 9.0, "BOOT", "NOEUD"),
        deux("R1", "resistance", 10.0, 10.0, "SIG1", "SORTIE"),
        deux("R2", "resistance", 10.0, 13.0, "SIG2", "MASSE"),
        Piece {
            nom: "J1",
            nature: "connecteur",
            x: 5.0,
            y: 15.0,
            mobile: false,
            pas_fin: false,
            pastilles: vec![tr("1", "VIN", 0.0, -3.81), tr("2", "MASSE", 0.0, -1.27), tr("3", "DP", 0.0, 1.27), tr("4", "DN", 0.0, 3.81)],
        },
    ];
    if !convertisseur {
        // sans convertisseur : le nœud de commutation devient un signal ordinaire, rien d'autre ne change
        for p in &mut v {
            for q in &mut p.pastilles {
                if q.reseau == "NOEUD" {
                    q.reseau = "SIG3";
                }
            }
        }
    }
    v
}

fn reseau(nom: &str, role: &str, isolement: f64, classe: usize, routage: &[&str], plans: &[&str], rempl: &[&str]) -> Value {
    let largeurs = match role {
        "puissance" => json!([0.3, 1.0, 5.0]),
        "plan" | "alimentation" => json!([0.25, 0.4, 3.0]),
        _ => json!([0.2, 0.3, 2.0]),
    };
    let paire = if nom == "DP" || nom == "DN" { json!("PAIRE1") } else { Value::Null };
    json!({"nom": nom, "classe": "", "role": role, "masse": nom == "MASSE", "sensibilite": if nom == "SIG1" { "sensible" } else { "normale" },
           "largeurs_mm": largeurs, "descente_large": false, "isolement_mm": isolement,
           "classes_isolement": {"piste": classe, "via": classe, "surface": classe},
           "via": {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.45, "diametre_max": 0.8, "trou_min": 0.2, "trou_max": 0.4},
           "poids": if role == "plan" { 0.0 } else { 1.0 }, "priorite": null, "priorite_routage": 0,
           "couche_preferee": if role == "puissance" { json!(routage[routage.len() - 1]) } else { Value::Null },
           "couches_permises": routage, "plan_couches": plans, "remplissages": rempl,
           "alimentation_schema": role != "signal", "paire": paire})
}

/// Carte d'essai : `n_couches` ∈ {2, 4, 6} ; `convertisseur` : un abaisseur asynchrone (Q1, D1, L3, C1 à C4).
pub fn carte(n_couches: usize, convertisseur: bool) -> Value {
    let (noms, plans) = noms_couches(n_couches);
    let routage: Vec<&str> = noms.iter().enumerate().filter(|(i, _)| !plans.contains(i)).map(|(_, n)| *n).collect();
    let dessus = noms[0];
    let tout = json!(CONTOUR);
    let moitie_g = json!([[0.0, 0.0], [20.0, 0.0], [20.0, 30.0], [0.0, 30.0]]);
    let moitie_d = json!([[20.0, 0.0], [40.0, 0.0], [40.0, 30.0], [20.0, 30.0]]);
    let mut plans_masse = Vec::new();
    let mut plans_vin = Vec::new();
    let couches: Vec<Value> = noms
        .iter()
        .enumerate()
        .map(|(i, nom)| {
            let zones = if !plans.contains(&i) {
                json!([])
            } else if n_couches == 6 && i == 4 {
                // plan scindé : une zone par réseau
                plans_masse.push(*nom);
                plans_vin.push(*nom);
                json!([{"reseau": "MASSE", "contour": moitie_g, "trous": []}, {"reseau": "VIN", "contour": moitie_d, "trous": []}])
            } else {
                plans_masse.push(*nom);
                json!([{"reseau": "MASSE", "contour": tout, "trous": []}])
            };
            json!({"nom": nom, "rang": i, "role": if plans.contains(&i) { "plan" } else { "routage" }, "zones_plan": zones,
                   "epaisseur_cuivre_mm": 0.035, "direction_preferee": null})
        })
        .collect();
    let pieces = pieces(convertisseur);
    let classe_pas_fin = 1;
    let mut composants: Vec<Value> = pieces
        .iter()
        .map(|p| {
            let classe = if p.pas_fin { classe_pas_fin } else { 2 };
            let pads: Vec<Value> = p.pastilles.iter().enumerate().map(|(k, q)| pastille(p.nom, k, q, &noms, classe)).collect();
            let regions = if p.nom == "U1" {
                json!([{"id": "U1/R0", "couches": [dessus], "face": "composant", "pastille": "U1#8", "reseau": "MASSE", "liaison": null,
                        "points": [[-0.5, -0.7], [0.5, -0.7], [0.5, 0.7], [-0.5, 0.7]], "classe_isolement": classe_pas_fin}])
            } else {
                json!([])
            };
            let (vias, zones, decoupes) = if p.nom == "J1" {
                (
                    json!([{"id": "J1/V0", "x": 1.2, "y": 0.0, "diametre": 0.6, "trou": 0.3, "reseau": "MASSE", "classe_isolement": 2}]),
                    json!([{"id": "J1/Z0", "couches": ["toutes"], "forme": "polygone", "points": [[-1.5, -5.0], [-0.9, -5.0], [-0.9, 5.0], [-1.5, 5.0]],
                            "largeur": 0.0, "effets": ["pistes", "vias"], "source": "keepout"}]),
                    json!(["D1"]),
                )
            } else {
                (json!([]), json!([]), json!([]))
            };
            let demi = if p.nom == "U1" { 2.0 } else if p.nom == "J1" { 4.5 } else { 1.2 };
            let pas_min = if p.nom == "U1" { 0.5 } else if p.nom == "J1" { 2.54 } else if p.pastilles.len() == 3 { 1.5 } else { 1.0 };
            let orientations = if p.mobile { json!([[0, false], [90, false], [180, false], [270, false]]) } else { json!([]) };
            json!({"nom": p.nom, "empreinte": format!("EMP_{}", p.nom), "nature": p.nature, "genre": "standard", "valeur": "",
                   "hauteur": 1.0, "statut": if p.mobile { "mobile" } else { "fixe" },
                   "raison_statut": if p.mobile { "libre" } else { "contrainte" }, "virtuel": false,
                   "lbb": [-demi, -demi, demi, demi], "pastilles": pads, "regions": regions, "vias": vias,
                   "zones_interdites": zones, "decoupes": decoupes,
                   "pose_actuelle": [p.x, p.y, 0.0, false], "pose_cible": [p.x, p.y, 0.0, false],
                   "orientations": orientations, "regle": "essai", "pas_fin": p.pas_fin, "matrice": false, "pas_min_mm": pas_min})
        })
        .collect();
    // pièce virtuelle portant une pastille libre avec réseau
    composants.push(json!({"nom": "__libre_0", "empreinte": "", "nature": "autre", "genre": "standard", "valeur": "", "hauteur": 0.0,
        "statut": "fixe", "raison_statut": "virtuel", "virtuel": true, "lbb": [-0.3, -0.3, 0.3, 0.3],
        "pastilles": [pastille("__libre_0", 0, &cms("1", "MASSE", 0.0, 0.0, 0.6, 0.6), &noms, 2)],
        "regions": [], "vias": [], "zones_interdites": [], "decoupes": [],
        "pose_actuelle": [36.0, 26.0, 0.0, false], "pose_cible": [36.0, 26.0, 0.0, false],
        "orientations": [], "regle": "", "pas_fin": false, "matrice": false, "pas_min_mm": null}));
    let role_masse = if plans.is_empty() { "alimentation" } else { "plan" };
    let noeud = if convertisseur { ("NOEUD", "commutation", 0.25, 0) } else { ("SIG3", "signal", 0.2, 2) };
    let reseaux = vec![
        reseau("MASSE", role_masse, 0.2, 2, &routage, &plans_masse, &[dessus]),
        reseau("VIN", "puissance", 0.2, 2, &routage, &plans_vin, &[]),
        reseau("SORTIE", "puissance", 0.2, 2, &routage, &[], &[]),
        reseau(noeud.0, noeud.1, noeud.2, noeud.3, &routage, &[], &[]),
        reseau("SIG1", "signal", 0.2, 2, &routage, &[], &[]),
        reseau("SIG2", "signal", 0.2, 2, &routage, &[], &[]),
        reseau("GH", "signal", 0.2, 2, &routage, &[], &[]),
        reseau("BOOT", "signal", 0.2, 2, &routage, &[], &[]),
        reseau("DP", "signal", 0.2, 2, &routage, &[], &[]),
        reseau("DN", "signal", 0.2, 2, &routage, &[], &[]),
    ];
    let convertisseurs = if convertisseur {
        json!([{"nom": "abaisseur VIN -> SORTIE", "topologie": "abaisseur", "commutation": "asynchrone",
                "haut": "Q1", "bas": "D1", "pilote": "U1", "inductance": "L3",
                "cond_entree": ["C1"], "cond_sortie": ["C2"], "cond_bulk": ["C3"], "cond_pilote": [], "cond_bootstrap": ["C4"],
                "reseau_entree": "VIN", "reseau_sortie": "SORTIE", "reseau_masse": "MASSE", "reseau_commutation": "NOEUD",
                "reseaux_courts": ["BOOT", "GH"], "reseaux_sensibles": ["SIG1"],
                "boucle_chaude": {"condensateurs": ["C1"], "reseau_rail": "VIN", "reseau_masse": "MASSE"}, "origine": "deduit"}])
    } else {
        json!([])
    };
    json!({
        "format": "donnees_placeur/2",
        "source": {"entree": "entree_carte.json", "sha256_entree": "0", "contraintes": null, "config": "config_carte.json",
                   "controle_gabarit": null, "construit_par": "essais du lot A"},
        "unite": "mm",
        "carte": {"contour": tout, "boite": [0.0, 0.0, 40.0, 30.0],
                  "decoupes": [{"id": "D1", "points": [[1.0, 22.0], [4.0, 22.0], [4.0, 27.0], [1.0, 27.0]], "proprietaire": "J1"}],
                  "remplissages": [{"couche": dessus, "reseau": "MASSE", "contour": tout, "trous": []}],
                  "marge_bord_mm": 1.0, "marge_decoupe_mm": 0.8, "garde_bord_mm": 0.5, "garde_decoupe_mm": 0.5},
        "couches": couches,
        "trous": [{"id": "T1", "x": 37.0, "y": 3.0, "rotation": 0.0,
                   "trou": {"diametre": 3.0, "forme": "rond", "longueur": 0.0, "rotation": 0.0, "decalage": [0.0, 0.0]},
                   "metallise": false, "diametre_cuivre": 0.0, "rayon_interdit_mm": 2.1, "classe_isolement": 2, "piece": null}],
        "obstacles_placement": [{"faces": ["dessus"], "rect": [33.0, 0.0, 40.0, 5.0], "etiquette": "zone autour du trou"}],
        "zones_imposees": [{"pieces": ["R1"], "faces": ["dessus"], "polygone": [[8.0, 8.0], [12.0, 8.0], [12.0, 16.0], [8.0, 16.0]],
                            "etiquette": "zone d'essai", "source": "contrainte"}],
        "zones_routage_interdites": [{"id": "Z1", "couches": [dessus], "forme": "polygone",
                                      "points": [[25.0, 25.0], [28.0, 25.0], [28.0, 28.0], [25.0, 28.0]], "largeur": 0.0,
                                      "effets": ["pistes", "vias"], "etiquette": "zone d'essai"}],
        "cuivre_fixe": [{"id": "F1", "type": "piste", "reseau": "", "couches": [noms[noms.len() - 1]],
                         "x1": 35.0, "y1": 20.0, "x2": 38.0, "y2": 20.0, "largeur": 0.3, "classe_isolement": 2}],
        "composants": composants,
        "reseaux": reseaux,
        "regles": {"isolement": {"defaut": 0.2, "classes": ["commutation", "objets des pièces à pas fin", "autres objets"],
                                 "matrice": [[0.25, 0.25, 0.25], [0.25, 0.125, 0.2], [0.25, 0.2, 0.2]], "classe_reference": 2},
                   "via": {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.45, "diametre_max": 0.8, "trou_min": 0.2, "trou_max": 0.4},
                   "trou_trou_mm": 0.25, "garde_trou_mm": 0.5,
                   "placement": {"grille_mm": 0.1, "marge_piece_mm": 0.2, "marge_traversante_mm": 0.5},
                   "via_dans_pastille_meme_reseau": false, "connexion_plans": {"isolement": 0.4, "connexion": "relief"}},
        "convertisseurs": convertisseurs,
        "sans_piste_dessous": ["L3"],
        "decouplages": [{"comp": "C1", "pastille": "C1#0", "reseau": "VIN", "broches_ci": ["U1#0"]}],
        "eloignements": [{"type": "dure", "objets": ["U1#1", "R2#0", "R1"], "reference": ["Q1"], "distance_mm": 3.0,
                          "poids_par_mm": 300.0, "marge_mm": 1.0}],
        "charges": [{"broche": "R1.2", "pastilles": ["R1#1"], "courant_a": 1.0}],
        "groupes": [{"type": "fonctionnel", "nom": "U1", "pieces": ["U1", "R1", "R2"], "rigide": false},
                    {"type": "repete", "nom": "CANAL", "famille": "_K", "source": "hierarchie", "rigide": false,
                     "exemplaires": [{"nom": "K1", "pieces": ["R1"]}, {"nom": "K2", "pieces": ["R2"]}],
                     "correspondance_pieces": [["R1", "R2"]], "correspondance_reseaux": [["SIG1", "SIG2"]]}],
        "paires": [{"nom": "PAIRE1", "positif": "DP", "negatif": "DN", "ecart_mm": null, "largeur_mm": null, "tolerance_longueur_mm": 1.0}],
        "au_bord": [{"piece": "J1", "cote": "gauche", "debord_mm": 0.0}],
        "masses": ["MASSE"],
        "parametres_deduction": {"pas_fin_mm": 0.65}
    })
}

/// Carte complète de référence des tests : 4 couches, un plan, un convertisseur.
pub fn carte_complete() -> Value {
    carte(4, true)
}

/// Placement `placements/2` : toutes les pièces non virtuelles à leur `pose_cible` (mil).
pub fn placement_cible(d: &Value) -> Value {
    let pieces: Vec<Value> = d["composants"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["virtuel"] == false)
        .map(|c| {
            let p = &c["pose_cible"];
            json!({"designator": c["nom"], "side": if p[3] == true { "bottom" } else { "top" },
                   "x": p[0].as_f64().unwrap() / 0.0254, "y": p[1].as_f64().unwrap() / 0.0254,
                   "rotation": p[2], "statut": c["statut"]})
        })
        .collect();
    json!({"format": "placements/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour", "pieces": pieces})
}

/// Routage `routage/2` vide.
pub fn routage_vide(d: &Value) -> Value {
    let couches: Vec<Value> =
        d["couches"].as_array().unwrap().iter().filter(|c| c["role"] == "routage").map(|c| c["nom"].clone()).collect();
    json!({"format": "routage/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour", "couches_routage": couches,
           "pistes": [], "vias": [], "surfaces": [], "amorces_pas_fin": [], "derogations": [], "reseaux": [], "echecs": []})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "écrit les cartes d'essai dans target/cartes_essai/ pour les essais de la ligne de commande"]
    fn ecrire_cartes() {
        let d = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("target").join("cartes_essai");
        std::fs::create_dir_all(&d).unwrap();
        for (n, conv) in [(2, false), (2, true), (4, false), (4, true), (6, false), (6, true)] {
            let c = carte(n, conv);
            let nom = format!("donnees_{}couches{}", n, if conv { "_convertisseur" } else { "" });
            crate::fichiers::ecrire_json_atomique(&d.join(format!("{}.json", nom)), &c).unwrap();
            crate::fichiers::ecrire_json_atomique(&d.join(format!("{}_placement.json", nom)), &placement_cible(&c)).unwrap();
            crate::fichiers::ecrire_json_atomique(&d.join(format!("{}_routage.json", nom)), &routage_vide(&c)).unwrap();
        }
    }

    #[test]
    fn variantes_lues() {
        for n in [2, 4, 6] {
            for conv in [false, true] {
                let d = crate::donnees::lire(&carte(n, conv)).unwrap_or_else(|e| panic!("{} couches, {} : {}", n, conv, e));
                assert_eq!(d.couches.len(), n);
                assert_eq!(d.convertisseurs.len(), conv as usize);
            }
        }
    }
}
