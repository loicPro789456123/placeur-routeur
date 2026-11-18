//! Tests du jalon E0 (lot E) : API exacte de §5.1, sérialisation (`verdict/2`, `controle_placement/1`), contrôles
//! sans géométrie, coût commun (§4.7), refus des entrées invalides sans panique, binaire d'essai.
//!
//! Les attendus numériques sont calculés à la main dans les commentaires.

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::process::Command;
use validite_rs::validite::{
    cout_commun, verifier_placement, verifier_routage, ControlePlacement, CoutCommun, InfoEloignement, LiaisonOuverte,
    Mesures, Mode, NatureLiaison, Verdict, Violation, ViolationPlacement,
};

// -----------------------------------------------------------------------------------------------------------------
// Jeu d'essai : deux points de test (une pastille CMS chacun, réseau SIG) reliés par une piste
// -----------------------------------------------------------------------------------------------------------------

fn pastille(id: &str, reseau: &str) -> Value {
    json!({"id": id, "nom": "1", "reseau": reseau, "lx": 0.0, "ly": 0.0, "forme": "rond", "largeur": 1.0,
           "hauteur": 1.0, "rotation": 0.0, "rayon_coin": 0.0, "points": [], "couches": ["TopLayer"],
           "face": "composant", "trou": null, "traversante": false, "expansion_masque": 0.05, "pile": null,
           "via_dans_pastille_permis": false, "classe_isolement": 0, "hw": 0.5, "hh": 0.5, "th": false})
}

fn composant(nom: &str, reseau: &str, x: f64, y: f64, statut: &str) -> Value {
    let orientations = if statut == "mobile" { json!([[0, false], [90, false], [180, false], [270, false]]) } else { json!([]) };
    json!({"nom": nom, "empreinte": "TP", "nature": "autre", "genre": "standard", "valeur": "", "hauteur": 0.0,
           "statut": statut, "raison_statut": if statut == "mobile" { "libre" } else { "contrainte" },
           "virtuel": false, "lbb": [-0.5, -0.5, 0.5, 0.5],
           "pastilles": [pastille(&format!("{nom}#0"), reseau)], "regions": [], "vias": [], "zones_interdites": [],
           "decoupes": [], "pose_actuelle": [x, y, 0.0, false], "pose_cible": [x, y, 0.0, false],
           "orientations": orientations, "regle": "", "pas_fin": false, "matrice": false, "pas_min_mm": null})
}

fn reseau(nom: &str, role: &str, largeurs: Value) -> Value {
    json!({"nom": nom, "classe": "", "role": role, "masse": false, "sensibilite": "normale", "largeurs_mm": largeurs,
           "descente_large": false, "isolement_mm": 0.2, "classes_isolement": {"piste": 0, "via": 0, "surface": 0},
           "via": {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.5, "diametre_max": 0.8, "trou_min": 0.25,
                   "trou_max": 0.4},
           "poids": 1.0, "priorite": null, "priorite_routage": 0, "couche_preferee": null,
           "couches_permises": ["TopLayer", "BottomLayer"], "plan_couches": [], "remplissages": [],
           "alimentation_schema": false, "paire": null})
}

/// `donnees_placeur/2` complet (§2.5) : carte de 20 × 10 mm à deux couches de routage, TP1 en (5,08 ; 5,08) et TP2 en
/// (15,24 ; 5,08) mm, soit (200 ; 200) et (600 ; 200) mil, sur la grille de 0,127 mm (5 mil).
fn donnees() -> Value {
    json!({
        "format": "donnees_placeur/2",
        "source": {"entree": "entree_carte.json", "sha256_entree": "", "contraintes": null, "config": "config_carte.json",
                   "controle_gabarit": null, "construit_par": "essai"},
        "unite": "mm",
        "carte": {"contour": [[0, 0], [20, 0], [20, 10], [0, 10]], "boite": [0, 0, 20, 10], "decoupes": [],
                  "remplissages": [], "marge_bord_mm": 1.0, "marge_decoupe_mm": 1.0, "garde_bord_mm": 0.6,
                  "garde_decoupe_mm": 0.6},
        "couches": [
            {"nom": "TopLayer", "rang": 0, "role": "routage", "zones_plan": [], "epaisseur_cuivre_mm": 0.035,
             "direction_preferee": null},
            {"nom": "BottomLayer", "rang": 1, "role": "routage", "zones_plan": [], "epaisseur_cuivre_mm": 0.035,
             "direction_preferee": null}],
        "trous": [], "obstacles_placement": [], "zones_imposees": [], "zones_routage_interdites": [], "cuivre_fixe": [],
        "composants": [composant("TP1", "SIG", 5.08, 5.08, "mobile"), composant("TP2", "SIG", 15.24, 5.08, "mobile")],
        "reseaux": [reseau("SIG", "signal", json!([0.254, 0.254, null]))],
        "regles": {"isolement": {"defaut": 0.2, "classes": ["defaut"], "matrice": [[0.2]], "classe_reference": 0},
                   "via": {"diametre": 0.6, "trou": 0.3, "diametre_min": 0.5, "diametre_max": 0.8, "trou_min": 0.25,
                           "trou_max": 0.4},
                   "trou_trou_mm": 0.254, "garde_trou_mm": 0.6,
                   "placement": {"grille_mm": 0.127, "marge_piece_mm": 0.25, "marge_traversante_mm": 0.6},
                   "via_dans_pastille_meme_reseau": false, "connexion_plans": {"isolement": 0.5, "connexion": "relief"}},
        "convertisseurs": [], "sans_piste_dessous": [], "decouplages": [], "eloignements": [], "charges": [],
        "groupes": [], "paires": [], "au_bord": [], "masses": [], "parametres_deduction": {"pas_fin_mm": 0.65}
    })
}

fn placement() -> Value {
    json!({"format": "placements/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour",
           "pieces": [{"designator": "TP1", "side": "top", "x": 200.0, "y": 200.0, "rotation": 0.0, "statut": "mobile"},
                      {"designator": "TP2", "side": "top", "x": 600.0, "y": 200.0, "rotation": 0.0, "statut": "mobile"}]})
}

fn piste(x1: f64, y1: f64, x2: f64, y2: f64, largeur: f64, couche: &str, net: &str) -> Value {
    json!({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": largeur, "layer": couche, "net_name": net})
}

fn routage(pistes: Vec<Value>, vias: Vec<Value>) -> Value {
    json!({"format": "routage/2", "unite": "mil", "origine": "coin bas-gauche de la boite du contour",
           "couches_routage": ["TopLayer", "BottomLayer"], "pistes": pistes, "vias": vias, "surfaces": [],
           "amorces_pas_fin": [], "derogations": [], "reseaux": [], "echecs": []})
}

/// La piste de TP1 à TP2 : 400 mil = 10,16 mm, 10 mil = 0,254 mm de large.
fn routage_minimal() -> Value {
    routage(vec![piste(200.0, 200.0, 600.0, 200.0, 10.0, "TopLayer", "SIG")], vec![])
}

fn cles(v: &Value) -> BTreeSet<String> {
    v.as_object().map(|o| o.keys().cloned().collect()).unwrap_or_default()
}

fn ensemble(l: &[&str]) -> BTreeSet<String> {
    l.iter().map(|s| s.to_string()).collect()
}

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

fn par_type(verdict: &Verdict) -> BTreeMap<String, u64> {
    let mut c = BTreeMap::new();
    for v in &verdict.violations {
        *c.entry(v.genre.clone()).or_insert(0) += 1;
    }
    c
}

// -----------------------------------------------------------------------------------------------------------------
// API exacte (§5.1)
// -----------------------------------------------------------------------------------------------------------------

/// Chaque fonction de §5.1 est affectée à un pointeur de fonction de son type exact ; chaque structure est construite
/// avec tous ses champs nommés, puis déstructurée sans `..` (un champ de plus ou de moins ne compile pas), et chaque
/// champ est lié à une variable de son type exact ; les énumérations sont filtrées sans joker.
#[test]
fn api_exacte() {
    let _vp: fn(&Value, &Value, bool) -> Result<ControlePlacement, String> = verifier_placement;
    let _vr: fn(&Value, &Value, &Value, Option<&Value>, Mode) -> Result<Verdict, String> = verifier_routage;
    let _cc: fn(&Value, &Value, &Verdict) -> CoutCommun = cout_commun;
    let _vj: fn(&Verdict) -> Value = Verdict::en_json;
    let _cj: fn(&ControlePlacement) -> Value = ControlePlacement::en_json;

    for m in [Mode::Strict, Mode::Altium] {
        match m {
            Mode::Strict | Mode::Altium => {}
        }
    }
    for n in [NatureLiaison::Pastilles, NatureLiaison::Ilot] {
        match n {
            NatureLiaison::Pastilles | NatureLiaison::Ilot => {}
        }
    }

    let liaison = LiaisonOuverte {
        reseau: String::from("SIG"),
        nature: NatureLiaison::Pastilles,
        pastilles: vec![String::from("TP2#0")],
        noms: vec![String::from("TP2.1")],
        position_mm: [15.24, 5.08],
    };
    let LiaisonOuverte { reseau, nature, pastilles, noms, position_mm } = liaison.clone();
    let _: String = reseau;
    let _: NatureLiaison = nature;
    let _: Vec<String> = pastilles;
    let _: Vec<String> = noms;
    let _: [f64; 2] = position_mm;

    let violation = Violation {
        genre: String::from("isolement"),
        reseaux: vec![String::from("SIG"), String::from("GND")],
        objets: vec![String::from("P0"), String::from("TP1#0")],
        position_mm: [1.0, 2.0],
        ecart_mm: Some(0.1),
        requis_mm: None,
    };
    let Violation { genre, reseaux, objets, position_mm, ecart_mm, requis_mm } = violation.clone();
    let _: String = genre;
    let _: Vec<String> = reseaux;
    let _: Vec<String> = objets;
    let _: [f64; 2] = position_mm;
    let _: Option<f64> = ecart_mm;
    let _: Option<f64> = requis_mm;

    let mesures = Mesures {
        ecart_moyen_mm: 0.5,
        ecart_min_mm: None,
        coudes: 1,
        coudes_90: 1,
        angles_aigus: 0,
        longueur_pistes_mm: 10.16,
        resistance_puissance_mohm: 0.0,
        vias: 0,
    };
    let Mesures {
        ecart_moyen_mm,
        ecart_min_mm,
        coudes,
        coudes_90,
        angles_aigus,
        longueur_pistes_mm,
        resistance_puissance_mohm,
        vias,
    } = mesures.clone();
    let _: f64 = ecart_moyen_mm;
    let _: Option<f64> = ecart_min_mm;
    let _: u64 = coudes;
    let _: u64 = coudes_90;
    let _: u64 = angles_aigus;
    let _: f64 = longueur_pistes_mm;
    let _: f64 = resistance_puissance_mohm;
    let _: u64 = vias;

    let verdict = Verdict {
        mode: Mode::Strict,
        valide: false,
        liaisons_ouvertes: vec![liaison],
        violations: vec![violation],
        derogations: BTreeMap::new(),
        avertissements: BTreeMap::new(),
        reseaux_a_router: 1,
        reseaux_complets: 0,
        marge_isolement_min_mm: Some(-0.1),
        marge_isolement_min_paire: String::from("P0(SIG) / TP1#0(GND)"),
        mesures,
    };
    let Verdict {
        mode,
        valide,
        liaisons_ouvertes,
        violations,
        derogations,
        avertissements,
        reseaux_a_router,
        reseaux_complets,
        marge_isolement_min_mm,
        marge_isolement_min_paire,
        mesures,
    } = verdict.clone();
    let _: Mode = mode;
    let _: bool = valide;
    let _: Vec<LiaisonOuverte> = liaisons_ouvertes;
    let _: Vec<Violation> = violations;
    let _: BTreeMap<String, u64> = derogations;
    let _: BTreeMap<String, u64> = avertissements;
    let _: u64 = reseaux_a_router;
    let _: u64 = reseaux_complets;
    let _: Option<f64> = marge_isolement_min_mm;
    let _: String = marge_isolement_min_paire;
    let _: Mesures = mesures;

    let vp = ViolationPlacement { genre: String::from("absente"), pieces: vec![String::from("TP2")], texte: String::new() };
    let ViolationPlacement { genre, pieces, texte } = vp.clone();
    let _: String = genre;
    let _: Vec<String> = pieces;
    let _: String = texte;

    let info = InfoEloignement {
        objet: String::from("TP1"),
        reference: String::from("TP2"),
        distance_mm: 10.16,
        minimum_mm: 5.0,
    };
    let InfoEloignement { objet, reference, distance_mm, minimum_mm } = info.clone();
    let _: String = objet;
    let _: String = reference;
    let _: f64 = distance_mm;
    let _: f64 = minimum_mm;

    let controle = ControlePlacement { valide: false, violations: vec![vp], eloignements: vec![info] };
    let ControlePlacement { valide, violations, eloignements } = controle.clone();
    let _: bool = valide;
    let _: Vec<ViolationPlacement> = violations;
    let _: Vec<InfoEloignement> = eloignements;

    let cout = CoutCommun {
        cout: 0.0,
        longueur_mm: 0.0,
        vias: 0,
        retreci_mm: 0.0,
        resistance_puissance_mohm: 0.0,
        coudes: 0,
        ecart_moyen_mm: 0.0,
    };
    let CoutCommun { cout, longueur_mm, vias, retreci_mm, resistance_puissance_mohm, coudes, ecart_moyen_mm } =
        cout.clone();
    let _: f64 = cout;
    let _: f64 = longueur_mm;
    let _: u64 = vias;
    let _: f64 = retreci_mm;
    let _: f64 = resistance_puissance_mohm;
    let _: u64 = coudes;
    let _: f64 = ecart_moyen_mm;

    // Les sérialisations s'appliquent à des valeurs construites à la main.
    assert_eq!(verdict.en_json()["format"], "verdict/2");
    assert_eq!(controle.en_json()["format"], "controle_placement/1");
}

// -----------------------------------------------------------------------------------------------------------------
// Sérialisation
// -----------------------------------------------------------------------------------------------------------------

/// Deux pastilles et une piste : verdict `verdict/2` complet (§2.8), 12 clés de §4.4, 5 clés d'avertissements dont
/// `zones_plan_isolees`, la clé de dérogation, les 8 clés de mesures.
#[test]
fn verdict_minimal() {
    let verdict = verifier_routage(&donnees(), &placement(), &routage_minimal(), None, Mode::Strict).unwrap();
    let j = verdict.en_json();
    assert_eq!(
        cles(&j),
        ensemble(&[
            "format",
            "outil",
            "mode",
            "valide",
            "liaisons_ouvertes",
            "detail_liaisons_ouvertes",
            "violations",
            "violations_par_type",
            "detail_violations",
            "detail_violations_tronque",
            "exemples",
            "derogations",
            "avertissements",
            "reseaux_a_router",
            "reseaux_complets",
            "marge_isolement_min_mm",
            "marge_isolement_min_paire",
            "mesures",
        ])
    );
    assert_eq!(j["format"], "verdict/2");
    assert!(j["outil"].as_str().unwrap().starts_with("validite "));
    assert_eq!(j["mode"], "strict");
    assert_eq!(cles(&j["violations_par_type"]), ensemble(&GENRES_ROUTAGE));
    assert!(j["violations_par_type"].as_object().unwrap().values().all(|n| n == 0));
    assert_eq!(
        cles(&j["avertissements"]),
        ensemble(&[
            "pistes_sous_pieces_sans_piste_dessous",
            "angles_aigus",
            "paires_desequilibrees",
            "regles_non_couvertes",
            "zones_plan_isolees",
        ])
    );
    assert_eq!(cles(&j["derogations"]), ensemble(&["via_dans_pastille_meme_reseau"]));
    assert_eq!(j["derogations"]["via_dans_pastille_meme_reseau"], 0);
    assert_eq!(
        cles(&j["mesures"]),
        ensemble(&[
            "ecart_moyen_mm",
            "ecart_min_mm",
            "coudes",
            "coudes_90",
            "angles_aigus",
            "longueur_pistes_mm",
            "resistance_puissance_mohm",
            "vias",
        ])
    );
    assert_eq!(j["valide"], true);
    assert_eq!(j["liaisons_ouvertes"], 0);
    assert_eq!(j["violations"], 0);
    assert_eq!(j["detail_liaisons_ouvertes"], json!([]));
    assert_eq!(j["detail_violations"], json!([]));
    assert_eq!(j["detail_violations_tronque"], false);
    assert_eq!(j["exemples"], json!({}));
    assert_eq!(j["reseaux_a_router"], 1);
    assert_eq!(j["reseaux_complets"], 1);
    assert_eq!(j["marge_isolement_min_mm"], Value::Null);
    assert_eq!(j["marge_isolement_min_paire"], "");
    // 400 mil × 0,0254 = 10,16 mm.
    assert!((j["mesures"]["longueur_pistes_mm"].as_f64().unwrap() - 10.16).abs() < 1e-9);
    assert_eq!(j["mesures"]["vias"], 0);
    assert_eq!(j["mesures"]["coudes"], 0);

    let altium = verifier_routage(&donnees(), &placement(), &routage_minimal(), None, Mode::Altium).unwrap();
    assert_eq!(altium.mode, Mode::Altium);
    assert_eq!(altium.en_json()["mode"], "altium");

    // Deux appels successifs : sérialisations identiques octet pour octet.
    let a = serde_json::to_string(&verdict.en_json()).unwrap();
    let b = serde_json::to_string(
        &verifier_routage(&donnees(), &placement(), &routage_minimal(), None, Mode::Strict).unwrap().en_json(),
    )
    .unwrap();
    assert_eq!(a, b);
}

/// 1 500 violations : 1 000 détails, `detail_violations_tronque`, 15 exemples par type, compte total exact ; les
/// clés de dérogations et d'avertissements fournies s'ajoutent aux clés obligatoires.
#[test]
fn verdict_tronque() {
    let v = |genre: &str, k: usize| Violation {
        genre: genre.to_string(),
        reseaux: vec!["SIG".to_string()],
        objets: vec![format!("P{k}")],
        position_mm: [k as f64, 0.0],
        ecart_mm: Some(0.1),
        requis_mm: Some(0.2),
    };
    let mut violations: Vec<Violation> = (0..1000).map(|k| v("isolement", k)).collect();
    violations.extend((0..500).map(|k| v("antenne", k)));
    let mut derogations = BTreeMap::new();
    derogations.insert("via_dans_pastille_meme_reseau".to_string(), 3);
    let mut avertissements = BTreeMap::new();
    avertissements.insert("angles_aigus".to_string(), 2);
    let verdict = Verdict {
        mode: Mode::Strict,
        valide: false,
        liaisons_ouvertes: vec![],
        violations,
        derogations,
        avertissements,
        reseaux_a_router: 0,
        reseaux_complets: 0,
        marge_isolement_min_mm: Some(-0.1),
        marge_isolement_min_paire: "P0(SIG) / P1(GND)".to_string(),
        mesures: Mesures {
            ecart_moyen_mm: 0.0,
            ecart_min_mm: Some(0.1),
            coudes: 0,
            coudes_90: 0,
            angles_aigus: 2,
            longueur_pistes_mm: 0.0,
            resistance_puissance_mohm: 0.0,
            vias: 0,
        },
    };
    let j = verdict.en_json();
    assert_eq!(j["violations"], 1500);
    assert_eq!(j["violations_par_type"]["isolement"], 1000);
    assert_eq!(j["violations_par_type"]["antenne"], 500);
    assert_eq!(j["detail_violations"].as_array().unwrap().len(), 1000);
    assert_eq!(j["detail_violations_tronque"], true);
    assert_eq!(j["exemples"]["isolement"].as_array().unwrap().len(), 15);
    assert_eq!(j["exemples"]["antenne"].as_array().unwrap().len(), 15);
    assert_eq!(cles(&j["exemples"]), ensemble(&["isolement", "antenne"]));
    assert_eq!(j["derogations"]["via_dans_pastille_meme_reseau"], 3);
    assert_eq!(j["avertissements"]["angles_aigus"], 2);
    assert_eq!(j["avertissements"]["zones_plan_isolees"], 0);
    assert_eq!(j["marge_isolement_min_mm"], -0.1);
    assert_eq!(j["mesures"]["ecart_min_mm"], 0.1);
    let d0 = &j["detail_violations"][0];
    assert_eq!(cles(d0), ensemble(&["genre", "reseaux", "objets", "position_mm", "ecart_mm", "requis_mm"]));
    assert_eq!(d0["objets"], json!(["P0"]));
}

// -----------------------------------------------------------------------------------------------------------------
// Contrôle de placement (contrôles sans géométrie du jalon E0)
// -----------------------------------------------------------------------------------------------------------------

#[test]
fn controle_placement_minimal() {
    let c = verifier_placement(&donnees(), &placement(), true).unwrap();
    assert!(c.valide, "{:?}", c.violations);
    let j = c.en_json();
    assert_eq!(cles(&j), ensemble(&["format", "valide", "violations", "violations_par_type", "infos"]));
    assert_eq!(cles(&j["violations_par_type"]), ensemble(&GENRES_PLACEMENT));
    assert_eq!(j["infos"], json!({"eloignements": []}));

    // TP2 absente, X9 inconnue, TP1 tournée de 45° (orientation) et hors grille (201 mil n'est pas un multiple de 5).
    let p = json!({"format": "placements/2", "unite": "mil", "origine": "",
                   "pieces": [{"designator": "TP1", "side": "top", "x": 201.0, "y": 200.0, "rotation": 45.0, "statut": "mobile"},
                              {"designator": "X9", "side": "top", "x": 0.0, "y": 0.0, "rotation": 0.0, "statut": "mobile"}]});
    let c = verifier_placement(&donnees(), &p, true).unwrap();
    assert!(!c.valide);
    let j = c.en_json();
    for (genre, n) in [("absente", 1), ("inconnue", 1), ("orientation", 1), ("grille", 1), ("fixe_deplacee", 0)] {
        assert_eq!(j["violations_par_type"][genre], n, "{genre}");
    }
    // Sans grille (référence manuelle) : plus de violation de grille.
    let c = verifier_placement(&donnees(), &p, false).unwrap();
    assert_eq!(c.en_json()["violations_par_type"]["grille"], 0);
    // Rotation 360 = 0 (modulo 360) ; 200,0004 mil est sur la grille à 1e-3 mil près.
    let p = json!([{"designator": "TP1", "side": "top", "x": 200.0004, "y": 200.0, "rotation": 360.0},
                   {"designator": "TP2", "side": "top", "x": 600.0, "y": 200.0, "rotation": -90.0}]);
    let c = verifier_placement(&donnees(), &p, true).unwrap();
    assert!(c.valide, "{:?}", c.violations);
}

#[test]
fn placement_pieces_fixes() {
    let mut d = donnees();
    d["composants"][1] = composant("TP2", "SIG", 15.24, 5.08, "fixe");
    // Forme v1 (liste nue) : une pièce fixe inchangée n'y figure pas, ce n'est pas une absence.
    let v1 = json!([{"designator": "TP1", "side": "top", "x": 200.0, "y": 200.0, "rotation": 0.0}]);
    let c = verifier_placement(&d, &v1, true).unwrap();
    assert!(c.valide, "{:?}", c.violations);
    // Forme v2 : toute pièce non virtuelle doit y figurer.
    let v2 = json!({"format": "placements/2", "unite": "mil", "origine": "", "pieces": v1});
    let c = verifier_placement(&d, &v2, true).unwrap();
    assert_eq!(c.en_json()["violations_par_type"]["absente"], 1);
    // Pièce fixe déplacée de 0,0254 mm (1 mil) : au-delà de 1e-3 mm ; posée dessous : face différente.
    for (x, side, n) in [(601.0, "top", 1), (600.0, "bottom", 1), (600.0, "top", 0), (600.03, "top", 0)] {
        let p = json!([{"designator": "TP1", "side": "top", "x": 200.0, "y": 200.0, "rotation": 0.0},
                       {"designator": "TP2", "side": side, "x": x, "y": 200.0, "rotation": 0.0}]);
        let c = verifier_placement(&d, &p, true).unwrap();
        // 0,03 mil = 0,000762 mm < 1e-3 mm : pas une violation.
        assert_eq!(c.en_json()["violations_par_type"]["fixe_deplacee"], n, "x = {x}, {side}");
    }
}

// -----------------------------------------------------------------------------------------------------------------
// Violations sans géométrie, mesures, coût commun
// -----------------------------------------------------------------------------------------------------------------

#[test]
fn violations_sans_geometrie() {
    let pistes = vec![
        piste(200.0, 200.0, 600.0, 200.0, 9.0, "TopLayer", "SIG"),   // largeur : 9 < 10 mil
        piste(200.0, 200.0, 600.0, 200.0, 10.0, "MidLayer1", "SIG"), // couche inconnue des données
        piste(200.0, 200.0, 600.0, 200.0, 10.0, "TopLayer", "XYZ"),  // réseau inconnu
    ];
    let vias = vec![
        json!({"x": 300.0, "y": 300.0, "size": 15.0, "hole_size": 10.0, "net": "SIG", "de": "TopLayer", "a": "BottomLayer"}),
        json!({"x": 300.0, "y": 300.0, "size": 24.0, "hole_size": 12.0, "net": "SIG", "de": "TopLayer", "a": "BottomLayer"}),
    ];
    let v = verifier_routage(&donnees(), &placement(), &routage(pistes, vias), None, Mode::Strict).unwrap();
    let c = par_type(&v);
    assert_eq!(c.get("largeur"), Some(&1));
    assert_eq!(c.get("couche_non_permise"), Some(&1));
    assert_eq!(c.get("reseau_inconnu"), Some(&1));
    // 15 mil = 0,381 mm < diametre_min 0,5 mm ; 24 mil = 0,6096 mm et 12 mil = 0,3048 mm : dans les bornes.
    assert_eq!(c.get("dimension_via"), Some(&1));
    assert!(!v.valide);
    let largeur = v.violations.iter().find(|x| x.genre == "largeur").unwrap();
    assert_eq!(largeur.objets, vec!["P0".to_string()]);
    assert_eq!(largeur.reseaux, vec!["SIG".to_string()]);
    assert!((largeur.ecart_mm.unwrap() - 0.2286).abs() < 1e-12);
    assert_eq!(largeur.requis_mm, Some(0.254));
    // milieu de la piste : (400 ; 200) mil = (10,16 ; 5,08) mm
    assert!((largeur.position_mm[0] - 10.16).abs() < 1e-9 && (largeur.position_mm[1] - 5.08).abs() < 1e-9);
    let via = v.violations.iter().find(|x| x.genre == "dimension_via").unwrap();
    assert_eq!(via.objets, vec!["V0".to_string()]);

    // Vias de couture : objets neufs `C<k>` ; réseau inconnu.
    let couture = json!({"format": "vias_couture/2", "unite": "mil", "reseaux": ["GND"], "pas_grille_mm": 2.5,
                         "vias": [{"x": 100.0, "y": 100.0, "size": 24.0, "hole_size": 12.0, "net": "GND"}], "refus": {}});
    let v = verifier_routage(&donnees(), &placement(), &routage_minimal(), Some(&couture), Mode::Strict).unwrap();
    assert_eq!(v.violations.len(), 1);
    assert_eq!(v.violations[0].genre, "reseau_inconnu");
    assert_eq!(v.violations[0].objets, vec!["C0".to_string()]);
    assert_eq!(v.mesures.vias, 0, "les vias de couture ne sont pas comptés dans les mesures");
}

/// Bornes de largeur converties en mil et arrondies vers le haut au millième (§0.4), comme les largeurs écrites.
#[test]
fn largeur_arrondie_au_millieme_de_mil() {
    // 0,254 mm = 10 mil exactement (aucun arrondi parasite à 10,001) : 10,000 mil passe.
    let v = verifier_routage(&donnees(), &placement(), &routage_minimal(), None, Mode::Strict).unwrap();
    assert!(v.valide, "{:?}", v.violations);
    // min 0,2 mm = 7,874 015… mil, borne arrondie vers le haut à 7,875 : 7,874 est refusé, 7,875 passe.
    // max 0,3 mm = 11,811 023… mil, borne arrondie vers le haut à 11,812 : 11,812 passe, 11,813 est refusé.
    let mut d = donnees();
    d["reseaux"][0]["largeurs_mm"] = json!([0.2, 0.2, 0.3]);
    for (w, attendu) in [(7.874, 1), (7.875, 0), (11.811, 0), (11.812, 0), (11.813, 1)] {
        let r = routage(vec![piste(200.0, 200.0, 600.0, 200.0, w, "TopLayer", "SIG")], vec![]);
        let v = verifier_routage(&d, &placement(), &r, None, Mode::Strict).unwrap();
        assert_eq!(par_type(&v).get("largeur").copied().unwrap_or(0), attendu, "largeur {w} mil");
    }
}

#[test]
fn coudes_et_angles() {
    // L : (200;200)-(400;200) puis (400;200)-(400;400) : un coude à 90°.
    // Aigu : (400;400)-(300;300) repart vers l'arrière depuis (400;400) : cos = +0,707 > 0,01, un coude aigu.
    // Droit : deux pistes alignées en (600;200) : cos = −1, pas un coude.
    let pistes = vec![
        piste(200.0, 200.0, 400.0, 200.0, 10.0, "TopLayer", "SIG"),
        piste(400.0, 200.0, 400.0, 400.0, 10.0, "TopLayer", "SIG"),
        piste(400.0, 400.0, 300.0, 300.0, 10.0, "TopLayer", "SIG"),
        piste(500.0, 100.0, 600.0, 100.0, 10.0, "BottomLayer", "SIG"),
        piste(600.0, 100.0, 700.0, 100.0, 10.0, "BottomLayer", "SIG"),
        // même point mais autre couche que la première : jonction différente (une seule extrémité).
        piste(200.0, 200.0, 200.0, 300.0, 10.0, "BottomLayer", "SIG"),
    ];
    let v = verifier_routage(&donnees(), &placement(), &routage(pistes, vec![]), None, Mode::Strict).unwrap();
    assert_eq!(v.mesures.coudes, 2);
    assert_eq!(v.mesures.coudes_90, 1);
    assert_eq!(v.mesures.angles_aigus, 1);
    assert_eq!(v.avertissements.get("angles_aigus"), Some(&1));
}

/// Formule de §4.7 avec des termes calculés à la main, terme `1000 × max(0 ; s − écart)` compris (étape 6).
#[test]
fn cout_commun_formule() {
    let mut d = donnees();
    d["reseaux"] = json!([reseau("SIG", "signal", json!([0.254, 0.254, null])),
                          reseau("P", "puissance", json!([0.3, 1.0, null]))]);
    let r = routage(
        vec![
            // P : 1000 mil = 25,4 mm ; 20 mil = 0,508 mm < 1,0 − 0,001 : rétréci ; résistance 0,5 × 25,4 / 0,508 = 25 mΩ
            piste(0.0, 0.0, 1000.0, 0.0, 20.0, "TopLayer", "P"),
            // SIG : 400 mil = 10,16 mm ; 0,254 = préférée : pas rétréci ; rôle signal : pas de résistance
            piste(0.0, 100.0, 0.0, 500.0, 10.0, "TopLayer", "SIG"),
        ],
        vec![
            json!({"x": 0.0, "y": 0.0, "size": 24.0, "hole_size": 12.0, "net": "P", "de": "TopLayer", "a": "BottomLayer"}),
            json!({"x": 0.0, "y": 500.0, "size": 24.0, "hole_size": 12.0, "net": "SIG", "de": "TopLayer", "a": "BottomLayer"}),
        ],
    );
    let mut verdict = verifier_routage(&d, &placement(), &r, None, Mode::Strict).unwrap();
    // Aucune piste n'a de voisin d'un autre réseau (cases de 1 mm non partagées) : écart moyen 0.
    assert_eq!(verdict.mesures.ecart_moyen_mm, 0.0);
    let c = cout_commun(&d, &r, &verdict);
    // 35,56 + 1,5 × 2 + 2,0 × 25,4 + 3,0 × 25 + 0,1 × 0 + 1000 × 0,6 = 764,36 (s = 3 × 0,2).
    assert!((c.cout - 764.36).abs() < 1e-9, "{c:?}");
    verdict.mesures.coudes = 3;
    verdict.mesures.ecart_moyen_mm = 0.25;
    let c = cout_commun(&d, &r, &verdict);
    // 35,56 + 1,5 × 2 + 2,0 × 25,4 + 3,0 × 25 + 0,1 × 3 + 1000 × (0,6 − 0,25) = 514,66.
    assert!((c.longueur_mm - 35.56).abs() < 1e-9, "{c:?}");
    assert_eq!(c.vias, 2);
    assert!((c.retreci_mm - 25.4).abs() < 1e-9, "{c:?}");
    assert!((c.resistance_puissance_mohm - 25.0).abs() < 1e-9, "{c:?}");
    assert_eq!(c.coudes, 3);
    assert_eq!(c.ecart_moyen_mm, 0.25, "écart du verdict recopié");
    assert!((c.cout - 514.66).abs() < 1e-9, "{c:?}");
    // Mêmes longueur et résistance que les mesures du verdict.
    assert!((verdict.mesures.longueur_pistes_mm - c.longueur_mm).abs() < 1e-12);
    assert!((verdict.mesures.resistance_puissance_mohm - c.resistance_puissance_mohm).abs() < 1e-12);
    verdict.mesures.ecart_moyen_mm = 0.9;
    assert!((cout_commun(&d, &r, &verdict).cout - 164.66).abs() < 1e-9);
    // Entrées illisibles : aucune panique.
    let vide = cout_commun(&Value::Null, &json!(42), &verdict);
    assert_eq!(vide.vias, 0);
    assert_eq!(vide.longueur_mm, 0.0);
}

#[test]
fn paires_et_regles_non_couvertes() {
    let mut d = donnees();
    d["reseaux"] = json!([reseau("SIG", "signal", json!([0.254, 0.254, null])),
                          reseau("D_P", "signal", json!([0.254, 0.254, null])),
                          reseau("D_N", "signal", json!([0.254, 0.254, null]))]);
    d["paires"] = json!([{"nom": "D", "positif": "D_P", "negatif": "D_N", "ecart_mm": null, "largeur_mm": null,
                          "tolerance_longueur_mm": 1.0}]);
    // Sans le champ : 0 règle non couverte.
    let v = verifier_routage(&d, &placement(), &routage_minimal(), None, Mode::Strict).unwrap();
    assert_eq!(v.avertissements.get("regles_non_couvertes"), Some(&0));
    // Convention des lots C et E : `parametres_deduction.regles_non_interpretees` (entier ≥ 0).
    d["parametres_deduction"]["regles_non_interpretees"] = json!(4);
    // D_P : 100 mil = 2,54 mm ; D_N : 150 mil = 3,81 mm ; écart 1,27 mm > 1 mm.
    let r = routage(
        vec![piste(0.0, 0.0, 100.0, 0.0, 10.0, "TopLayer", "D_P"), piste(0.0, 50.0, 150.0, 50.0, 10.0, "TopLayer", "D_N")],
        vec![],
    );
    let v = verifier_routage(&d, &placement(), &r, None, Mode::Strict).unwrap();
    assert_eq!(v.avertissements.get("paires_desequilibrees"), Some(&1));
    assert_eq!(v.avertissements.get("regles_non_couvertes"), Some(&4));
    // Un autre nom n'est pas lu ; une valeur qui n'est pas un entier ≥ 0 est une entrée invalide qui nomme le champ.
    let mut autre = d.clone();
    autre["parametres_deduction"] = json!({"pas_fin_mm": 0.65, "regles_non_couvertes": 7});
    let v = verifier_routage(&autre, &placement(), &r, None, Mode::Strict).unwrap();
    assert_eq!(v.avertissements.get("regles_non_couvertes"), Some(&0));
    for fautif in [json!(4.0), json!(-1), json!("4"), Value::Null] {
        let mut f = d.clone();
        f["parametres_deduction"]["regles_non_interpretees"] = fautif.clone();
        let e = verifier_routage(&f, &placement(), &r, None, Mode::Strict).unwrap_err();
        assert_eq!(e, "donnees.parametres_deduction.regles_non_interpretees : entier positif ou nul attendu", "{fautif}");
    }
    // Écart de 0,762 mm (130 mil contre 100) : dans la tolérance.
    let r = routage(
        vec![piste(0.0, 0.0, 100.0, 0.0, 10.0, "TopLayer", "D_P"), piste(0.0, 50.0, 130.0, 50.0, 10.0, "TopLayer", "D_N")],
        vec![],
    );
    let v = verifier_routage(&d, &placement(), &r, None, Mode::Strict).unwrap();
    assert_eq!(v.avertissements.get("paires_desequilibrees"), Some(&0));
}

// -----------------------------------------------------------------------------------------------------------------
// Entrées invalides : Err qui nomme le champ, sans panique
// -----------------------------------------------------------------------------------------------------------------

#[test]
fn entrees_invalides() {
    let err_routage = |d: &Value, p: &Value, r: &Value| verifier_routage(d, p, r, None, Mode::Strict).unwrap_err();

    let mut d = donnees();
    d.as_object_mut().unwrap().remove("reseaux");
    assert_eq!(err_routage(&d, &placement(), &routage_minimal()), "donnees.reseaux : champ manquant");

    let mut d = donnees();
    d["format"] = json!("donnees_placeur/3");
    assert_eq!(err_routage(&d, &placement(), &routage_minimal()), "donnees : format donnees_placeur/3 non pris en charge");

    let mut d = donnees();
    d["composants"][1]["pastilles"][0]["reseau"] = json!("GND");
    let e = err_routage(&d, &placement(), &routage_minimal());
    assert!(e.starts_with("donnees.composants[1].pastilles[0].reseau : "), "{e}");
    assert!(e.contains("GND"), "{e}");

    let mut d = donnees();
    d["reseaux"][0]["couches_permises"] = json!(["TopLayer", "In9.Cu"]);
    let e = err_routage(&d, &placement(), &routage_minimal());
    assert!(e.starts_with("donnees.reseaux[0].couches_permises[1] : "), "{e}");

    let mut d = donnees();
    d["reseaux"][0]["largeurs_mm"] = json!([0.254, "0,3", null]);
    assert_eq!(err_routage(&d, &placement(), &routage_minimal()), "donnees.reseaux[0].largeurs_mm[1] : nombre attendu");

    let mut r = routage_minimal();
    r["pistes"][0].as_object_mut().unwrap().remove("width");
    assert_eq!(err_routage(&donnees(), &placement(), &r), "routage.pistes[0].width : champ manquant");

    let mut r = routage_minimal();
    r["format"] = json!("routage/3");
    assert_eq!(err_routage(&donnees(), &placement(), &r), "routage : format routage/3 non pris en charge");

    let p = json!([{"designator": "TP1", "side": "top", "x": 1.0, "y": 1.0, "rotation": 0.0},
                   {"designator": "TP1", "side": "top", "x": 2.0, "y": 2.0, "rotation": 0.0}]);
    let e = verifier_placement(&donnees(), &p, true).unwrap_err();
    assert!(e.starts_with("placements[1].designator : "), "{e}");

    let p = json!([{"designator": "TP1", "side": "dessus", "x": 1.0, "y": 1.0, "rotation": 0.0}]);
    let e = verifier_placement(&donnees(), &p, true).unwrap_err();
    assert!(e.starts_with("placements[0].side : "), "{e}");

    // Valeurs quelconques : jamais de panique.
    for v in [Value::Null, json!(1), json!("x"), json!([]), json!({}), json!([1, 2]), json!({"format": 3})] {
        assert!(verifier_routage(&v, &v, &v, Some(&v), Mode::Altium).is_err());
        assert!(verifier_placement(&v, &v, false).is_err());
        let verdict = verifier_routage(&donnees(), &placement(), &routage_minimal(), None, Mode::Strict).unwrap();
        let _ = cout_commun(&v, &v, &verdict);
    }
    // Aucun message ne contient de chemin de fichier.
    let e = err_routage(&Value::Null, &placement(), &routage_minimal());
    assert_eq!(e, "donnees : objet attendu");
}

#[test]
fn formes_v1() {
    // Routage v1 : sans `format` ni `surfaces`, vias sans `de`/`a` ; placement v1 en liste nue. La piste est coupée
    // au via : un via au milieu d'une piste n'a pas de contact strict avec elle (§4.2, ligne 3).
    let r = json!({"unite": "mil", "pistes": [{"x1": 200.0, "y1": 200.0, "x2": 400.0, "y2": 200.0, "width": 10.0,
                                                "layer": "TopLayer", "net_name": "SIG"},
                                               {"x1": 400.0, "y1": 200.0, "x2": 600.0, "y2": 200.0, "width": 10.0,
                                                "layer": "TopLayer", "net_name": "SIG"}],
                   "vias": [{"x": 400.0, "y": 200.0, "size": 24.0, "hole_size": 12.0, "net": "SIG"}]});
    let p = json!([{"designator": "TP1", "side": "top", "x": 200.0, "y": 200.0, "rotation": 0},
                   {"designator": "TP2", "side": "top", "x": 600.0, "y": 200.0, "rotation": 0}]);
    let v = verifier_routage(&donnees(), &p, &r, None, Mode::Strict).unwrap();
    assert!(v.valide, "{:?}", v.violations);
    assert_eq!(v.mesures.vias, 1);
    assert!(verifier_placement(&donnees(), &p, true).unwrap().valide);
    // Les données, elles, n'ont pas de forme v1 lue : le nom de `pose_cible` dans la version 1 (supprimé par §2.5)
    // n'est pas un alias ; sans `pose_cible`, la pièce est une entrée invalide.
    let mut d = donnees();
    let pose = d["composants"][0]["pose_cible"].clone();
    d["composants"][0].as_object_mut().unwrap().remove("pose_cible");
    d["composants"][0]["pose_v3"] = pose;
    let e = verifier_routage(&d, &p, &r, None, Mode::Strict).unwrap_err();
    assert_eq!(e, "donnees.composants[0].pose_cible : champ manquant");
    assert_eq!(verifier_placement(&d, &p, true).unwrap_err(), e);
}

// -----------------------------------------------------------------------------------------------------------------
// Binaire d'essai `validite_verifier`
// -----------------------------------------------------------------------------------------------------------------

#[test]
fn binaire_verifier() {
    let dossier = std::path::Path::new(env!("CARGO_TARGET_TMPDIR")).join("binaire_verifier");
    let _ = std::fs::remove_dir_all(&dossier);
    std::fs::create_dir_all(&dossier).unwrap();
    let ecrire = |nom: &str, v: &Value| {
        let f = dossier.join(nom);
        std::fs::write(&f, serde_json::to_string(v).unwrap()).unwrap();
        f.to_string_lossy().into_owned()
    };
    let fd = ecrire("donnees_placeur.json", &donnees());
    let fp = ecrire("placements_routage.json", &placement());
    let fr = ecrire("routage_detaille.json", &routage_minimal());
    let fs = dossier.join("verdict.json").to_string_lossy().into_owned();
    let fm = dossier.join("mesures.json").to_string_lossy().into_owned();
    let (fd, fp, fr, fs, fm) = (fd.as_str(), fp.as_str(), fr.as_str(), fs.as_str(), fm.as_str());
    let exe = env!("CARGO_BIN_EXE_validite_verifier");
    let lancer = |args: &[&str]| Command::new(exe).args(args).output().unwrap();

    let o = lancer(&["verifier", "--donnees", fd, "--placement", fp, "--routage", fr, "--sortie", fs, "--mesures", fm]);
    assert_eq!(o.status.code(), Some(0), "{}", String::from_utf8_lossy(&o.stderr));
    let verdict: Value = serde_json::from_slice(&std::fs::read(fs).unwrap()).unwrap();
    assert_eq!(verdict["format"], "verdict/2");
    assert_eq!(verdict["placement"]["format"], "controle_placement/1");
    let mesures: Value = serde_json::from_slice(&std::fs::read(fm).unwrap()).unwrap();
    assert_eq!(mesures["format"], "mesures/2");
    assert_eq!(mesures["formule"], "cout_commun/2");
    // 10,16 mm de piste, aucun voisin (écart moyen 0) : 10,16 + 1000 × 0,6 = 610,16.
    assert!((mesures["cout_commun"].as_f64().unwrap() - 610.16).abs() < 1e-9, "{mesures}");
    assert_eq!(mesures["termes"]["ecart_moyen_mm"], 0.0);
    // Deux exécutions : sorties identiques octet pour octet.
    let premier = std::fs::read(fs).unwrap();
    let o = lancer(&["verifier", "--donnees", fd, "--placement", fp, "--routage", fr, "--sortie", fs, "--mode", "strict"]);
    assert_eq!(o.status.code(), Some(0));
    assert_eq!(std::fs::read(fs).unwrap(), premier);

    // Option inconnue, fichier absent, JSON invalide, donnée invalide, option manquante, commande inconnue ou
    // absente : code 2, message sans dossier.
    let invalide = ecrire("invalide.json", &json!({"format": "donnees_placeur/9"}));
    std::fs::write(dossier.join("casse.json"), "{\"a\": ").unwrap();
    let casse = dossier.join("casse.json").to_string_lossy().into_owned();
    let absent = dossier.join("absent.json").to_string_lossy().into_owned();
    let (invalide, casse, absent) = (invalide.as_str(), casse.as_str(), absent.as_str());
    let racine = dossier.to_string_lossy().into_owned();
    // Arguments intervertis ou collés : un chemin reçu là où une valeur courte ou un nom d'option est attendu.
    let option_collee = format!("--donnees={fd}");
    let cas: Vec<Vec<&str>> = vec![
        vec!["verifier", "--donnees", fd, "--placement", fp, "--routage", fr, "--sortie", fs, "--x"],
        vec!["verifier", "--donnees", absent, "--placement", fp, "--routage", fr, "--sortie", fs],
        vec!["verifier", "--donnees", casse, "--placement", fp, "--routage", fr, "--sortie", fs],
        vec!["verifier", "--donnees", invalide, "--placement", fp, "--routage", fr, "--sortie", fs],
        vec!["verifier", "--donnees", fd, "--placement", fp, "--routage", fr],
        vec!["verifier", "--donnees", fd, "--placement", fp, "--routage", fr, "--sortie", fs, "--mode", fd],
        vec!["verifier", option_collee.as_str(), "--placement", fp, "--routage", fr, "--sortie", fs],
        vec![option_collee.as_str(), "verifier"],
        vec!["verifier", "--donnees", fd, fp],
        vec![fd, "verifier"],
        vec!["autre"],
        vec![],
    ];
    for args in &cas {
        let o = lancer(args);
        let err = String::from_utf8_lossy(&o.stderr).into_owned();
        assert_eq!(o.status.code(), Some(2), "{args:?} : {err}");
        assert!(!err.is_empty());
        assert!(!err.contains(&racine), "chemin dans le message : {err}");
        // Aucun de ces messages ne porte sur le fichier de données : son nom n'y est jamais recopié.
        assert!(!err.contains("donnees_placeur.json"), "valeur recopiée : {err}");
    }
    let o = lancer(&["verifier", "--donnees", invalide, "--placement", fp, "--routage", fr, "--sortie", fs]);
    let err = String::from_utf8_lossy(&o.stderr).into_owned();
    assert!(err.contains("format donnees_placeur/9 non pris en charge"), "{err}");
    // Un nom d'option simple est cité ; une valeur de --mode ne l'est jamais.
    let o = lancer(&["verifier", "--donnees", fd, "--placement", fp, "--routage", fr, "--sortie", fs, "--x"]);
    assert!(String::from_utf8_lossy(&o.stderr).starts_with("validite_verifier : option inconnue (--x)"));
    let o = lancer(&["verifier", "--donnees", fd, "--placement", fp, "--routage", fr, "--sortie", fs, "--mode", "Strict"]);
    let err = String::from_utf8_lossy(&o.stderr).into_owned();
    assert_eq!(err.trim_end(), "validite_verifier : --mode : valeur non reconnue (strict ou altium attendu)");
    let _ = std::fs::remove_dir_all(&dossier);
}
