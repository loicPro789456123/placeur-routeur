//! Banc des cas de validité du corpus (§7.4) : chaque cas de `corpus/validite/` est vérifié dans les deux modes et
//! comparé à ses attendus écrits à la main (`attendu_strict.json`, `attendu_altium.json`).
//!
//! Connectivité (§4.3, étape 5) : nombre et détail des liaisons ouvertes, `reseaux_a_router`, `reseaux_complets`.
//! Verdict (§4.4, §4.7, étape 6) : `valide`, `liaisons_ouvertes`, `violations_par_type`, `derogations`,
//! `avertissements` et `mesures` (à 1e-6) ; `cout_commun` égal à `attendu_mesures.json` (à 1e-6).
//! Placement (§4.5, étape 7) : `verifier_placement` (option `grille` de `cas.json`) comparé au champ `placement` de
//! `attendu_strict.json` : `valide`, `violations_par_type`, suite des (genre, pièces) et `infos` (à 1e-6).

use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use validite_rs::validite::{cout_commun, verifier_placement, verifier_routage, Mode, NatureLiaison};

fn dossier_cas() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("validite")
}

fn lire(p: &Path) -> Value {
    serde_json::from_str(&fs::read_to_string(p).expect("lecture")).expect("JSON")
}

struct Cas {
    nom: String,
    donnees: Value,
    placement: Value,
    routage: Value,
    couture: Option<Value>,
    attendus: [Value; 2],
    mesures: Value,
    grille: bool,
}

fn cas() -> Vec<Cas> {
    let mut noms: Vec<PathBuf> = fs::read_dir(dossier_cas())
        .expect("dossier des cas")
        .map(|e| e.expect("entrée").path())
        .filter(|p| p.join("cas.json").is_file())
        .collect();
    noms.sort();
    noms.iter()
        .map(|d| {
            let c = lire(&d.join("cas.json"));
            let f = &c["fichiers"];
            let fichier = |cle: &str| lire(&d.join(f[cle].as_str().expect(cle)));
            Cas {
                nom: c["nom"].as_str().expect("nom").to_string(),
                donnees: fichier("donnees"),
                placement: fichier("placement"),
                routage: fichier("routage"),
                couture: f["couture"].as_str().map(|n| lire(&d.join(n))),
                attendus: [fichier("attendu_strict"), fichier("attendu_altium")],
                mesures: fichier("attendu_mesures"),
                grille: c["grille"].as_bool().expect("grille"),
            }
        })
        .collect()
}

fn proche(a: f64, b: &Value) -> bool {
    b.as_f64().is_some_and(|b| (a - b).abs() <= 1e-6)
}

#[test]
fn connectivite_des_cas() {
    let liste = cas();
    assert!(liste.len() >= 28, "{} cas seulement (au moins 28 attendus)", liste.len());
    let mut ecarts = Vec::new();
    for c in &liste {
        for (mode, attendu) in [(Mode::Strict, &c.attendus[0]), (Mode::Altium, &c.attendus[1])] {
            let v = match verifier_routage(&c.donnees, &c.placement, &c.routage, c.couture.as_ref(), mode) {
                Ok(v) => v,
                Err(e) => {
                    ecarts.push(format!("{} {mode:?} : erreur {e}", c.nom));
                    continue;
                }
            };
            let mut ecart = |quoi: &str, obtenu: String, voulu: String| {
                if obtenu != voulu {
                    ecarts.push(format!("{} {mode:?} {quoi} : {obtenu} au lieu de {voulu}", c.nom));
                }
            };
            ecart("liaisons_ouvertes", v.liaisons_ouvertes.len().to_string(), attendu["liaisons_ouvertes"].to_string());
            ecart("reseaux_a_router", v.reseaux_a_router.to_string(), attendu["reseaux_a_router"].to_string());
            ecart("reseaux_complets", v.reseaux_complets.to_string(), attendu["reseaux_complets"].to_string());
            ecart(
                "zones_plan_isolees",
                v.avertissements["zones_plan_isolees"].to_string(),
                attendu["avertissements"]["zones_plan_isolees"].to_string(),
            );
            let detail = attendu["detail_liaisons_ouvertes"].as_array().expect("detail");
            for (k, (l, a)) in v.liaisons_ouvertes.iter().zip(detail).enumerate() {
                let nature = match l.nature {
                    NatureLiaison::Pastilles => "pastilles",
                    NatureLiaison::Ilot => "ilot",
                };
                let obtenu = format!("{} {nature} {:?} {:?}", l.reseau, l.pastilles, l.noms);
                let pastilles: Vec<&str> = a["pastilles"].as_array().unwrap().iter().map(|x| x.as_str().unwrap()).collect();
                let noms: Vec<&str> = a["noms"].as_array().unwrap().iter().map(|x| x.as_str().unwrap()).collect();
                let voulu = format!("{} {} {pastilles:?} {noms:?}", a["reseau"].as_str().unwrap(),
                                    a["nature"].as_str().unwrap());
                ecart(&format!("liaison {k}"), obtenu, voulu);
                if !(proche(l.position_mm[0], &a["position_mm"][0]) && proche(l.position_mm[1], &a["position_mm"][1])) {
                    ecart(&format!("liaison {k} position"), format!("{:?}", l.position_mm), a["position_mm"].to_string());
                }
            }
        }
    }
    assert!(ecarts.is_empty(), "{} écarts :\n{}", ecarts.len(), ecarts.join("\n"));
}

fn comp(d: &mut Value) -> &mut Value {
    &mut d["composants"][1]
}

/// Entrée invalide : `Err` qui nomme le champ fautif, sans chemin de fichier, sans panique.
#[test]
fn entrees_invalides() {
    let base = cas().into_iter().find(|c| c.nom == "015_piste_region").expect("cas 015");
    let essai = |modifier: &dyn Fn(&mut Value), attendu: &str| {
        let mut d = base.donnees.clone();
        modifier(&mut d);
        let e = verifier_routage(&d, &base.placement, &base.routage, None, Mode::Strict).unwrap_err();
        assert!(e.starts_with(attendu), "« {e} » ne commence pas par « {attendu} »");
        assert!(!e.contains('/') && !e.contains('\\'), "chemin dans « {e} »");
    };
    // Champ manquant.
    essai(&|d| {
        comp(d)["pastilles"][0].as_object_mut().unwrap().remove("couches");
    }, "donnees.composants[1].pastilles[0].couches : champ manquant");
    essai(&|d| {
        comp(d)["regions"][0].as_object_mut().unwrap().remove("points");
    }, "donnees.composants[1].regions[0].points : champ manquant");
    // Réseau non déclaré.
    essai(&|d| comp(d)["regions"][0]["reseau"] = "N9".into(), "donnees.composants[1].regions[0].reseau : réseau « N9 »");
    essai(&|d| comp(d)["regions"][0]["liaison"] = serde_json::json!(["N1", "N9"]),
          "donnees.composants[1].regions[0].liaison[1] : réseau « N9 »");
    essai(&|d| {
        d["cuivre_fixe"] = serde_json::json!([{"id": "F0", "type": "via", "reseau": "N9", "couches": ["TopLayer"],
                                                "x": 1.0, "y": 1.0, "diametre": 0.6, "trou": 0.3, "classe_isolement": 0}]);
    }, "donnees.cuivre_fixe[0].reseau : réseau « N9 »");
    // Couche inconnue.
    essai(&|d| comp(d)["pastilles"][0]["couches"] = serde_json::json!(["Top"]),
          "donnees.composants[1].pastilles[0].couches[0] : couche « Top » inconnue");
    essai(&|d| {
        d["couches"][0]["zones_plan"] = serde_json::json!([{"reseau": "N9", "contour": [[0, 0], [1, 0], [1, 1]], "trous": []}]);
    }, "donnees.couches[0].zones_plan[0].reseau : réseau « N9 »");
    // Forme inconnue, polygone dégénéré, dimension nulle.
    essai(&|d| comp(d)["pastilles"][0]["forme"] = "ovale".into(), "donnees.composants[1].pastilles[0].forme : « ovale »");
    essai(&|d| comp(d)["regions"][0]["points"] = serde_json::json!([[0, 0], [1, 1]]),
          "donnees.composants[1].regions[0].points : polygone");
    essai(&|d| comp(d)["pastilles"][0]["largeur"] = 0.0.into(), "donnees.composants[1].pastilles[0].largeur : nombre > 0");
    essai(&|d| comp(d)["pastilles"][0]["trou"] = serde_json::json!({"diametre": 0.5, "forme": "ovale", "longueur": 0.5,
        "rotation": 0.0, "decalage": [0.0, 0.0], "metallise": true}), "donnees.composants[1].pastilles[0].trou.forme");
}

/// Égalité de deux valeurs JSON, les nombres à 1e-6 près.
fn egal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => (x.as_f64().unwrap() - y.as_f64().unwrap()).abs() <= 1e-6,
        (Value::Object(x), Value::Object(y)) => x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| egal(v, w))),
        (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(v, w)| egal(v, w)),
        _ => a == b,
    }
}

#[test]
fn verdicts_des_cas() {
    let liste = cas();
    assert!(liste.len() >= 60, "{} cas seulement (au moins 60 attendus)", liste.len());
    let mut ecarts = Vec::new();
    for c in &liste {
        let mut strict = None;
        for (mode, attendu) in [(Mode::Strict, &c.attendus[0]), (Mode::Altium, &c.attendus[1])] {
            let v = match verifier_routage(&c.donnees, &c.placement, &c.routage, c.couture.as_ref(), mode) {
                Ok(v) => v,
                Err(e) => {
                    ecarts.push(format!("{} {mode:?} : erreur {e}", c.nom));
                    continue;
                }
            };
            let j = v.en_json();
            for cle in ["valide", "liaisons_ouvertes", "violations", "violations_par_type", "derogations",
                        "avertissements", "mesures"] {
                if !egal(&j[cle], &attendu[cle]) {
                    ecarts.push(format!("{} {mode:?} {cle} : {} au lieu de {}", c.nom, j[cle], attendu[cle]));
                }
            }
            if mode == Mode::Strict {
                strict = Some(v);
            }
        }
        match verifier_placement(&c.donnees, &c.placement, c.grille) {
            Err(e) => ecarts.push(format!("{} placement : erreur {e}", c.nom)),
            Ok(pl) => {
                let j = pl.en_json();
                let a = &c.attendus[0]["placement"];
                let suite = |v: &Value| -> Vec<(Value, Value)> {
                    v["violations"].as_array().map_or(Vec::new(), |l| {
                        l.iter().map(|x| (x["genre"].clone(), x["pieces"].clone())).collect()
                    })
                };
                for cle in ["valide", "violations_par_type", "infos"] {
                    if !egal(&j[cle], &a[cle]) {
                        ecarts.push(format!("{} placement {cle} : {} au lieu de {}", c.nom, j[cle], a[cle]));
                    }
                }
                if suite(&j) != suite(a) {
                    ecarts.push(format!("{} placement violations : {:?} au lieu de {:?}", c.nom, suite(&j), suite(a)));
                }
            }
        }
        if let Some(v) = strict {
            let cc = cout_commun(&c.donnees, &c.routage, &v);
            let t = &c.mesures["termes"];
            let obtenu = serde_json::json!({"cout_commun": cc.cout, "longueur_mm": cc.longueur_mm, "vias": cc.vias,
                "retreci_mm": cc.retreci_mm, "resistance_puissance_mohm": cc.resistance_puissance_mohm,
                "coudes": cc.coudes, "ecart_moyen_mm": cc.ecart_moyen_mm});
            let voulu = serde_json::json!({"cout_commun": c.mesures["cout_commun"], "longueur_mm": t["longueur_mm"],
                "vias": t["vias"], "retreci_mm": t["retreci_mm"],
                "resistance_puissance_mohm": t["resistance_puissance_mohm"], "coudes": t["coudes"],
                "ecart_moyen_mm": t["ecart_moyen_mm"]});
            if !egal(&obtenu, &voulu) {
                ecarts.push(format!("{} cout_commun : {obtenu} au lieu de {voulu}", c.nom));
            }
        }
    }
    assert!(ecarts.is_empty(), "{} écarts :
{}", ecarts.len(), ecarts.join("
"));
}

/// Deux appels successifs : `en_json` sérialisé identique octet pour octet.
#[test]
fn verdict_deterministe() {
    for c in cas() {
        for mode in [Mode::Strict, Mode::Altium] {
            let a = verifier_routage(&c.donnees, &c.placement, &c.routage, c.couture.as_ref(), mode).unwrap();
            let b = verifier_routage(&c.donnees, &c.placement, &c.routage, c.couture.as_ref(), mode).unwrap();
            assert_eq!(a.en_json().to_string(), b.en_json().to_string(), "{}", c.nom);
        }
        let a = verifier_placement(&c.donnees, &c.placement, c.grille).unwrap();
        let b = verifier_placement(&c.donnees, &c.placement, c.grille).unwrap();
        assert_eq!(a.en_json().to_string(), b.en_json().to_string(), "{} placement", c.nom);
    }
}

/// 1 500 violations provoquées (1 500 pistes de 5 mil superposées, sous la largeur minimale de 7,875 mil, sans
/// antenne : chacune touche les autres) : 1 000 détails, `detail_violations_tronque` = true, au plus 15 exemples.
#[test]
fn quinze_cents_violations() {
    let c = cas().into_iter().find(|c| c.nom == "011_isolement_piste_pastille").expect("cas 011");
    let mut r = c.routage.clone();
    let piste = serde_json::json!({"x1": 300.0, "y1": 60.0, "x2": 310.0, "y2": 60.0, "width": 5.0,
                                   "layer": "TopLayer", "net_name": "N1"});
    r["pistes"] = Value::Array(vec![piste; 1500]);
    let v = verifier_routage(&c.donnees, &c.placement, &r, None, Mode::Strict).unwrap();
    let j = v.en_json();
    assert_eq!(j["violations"], 1500, "{}", j["violations_par_type"]);
    assert_eq!(j["violations_par_type"]["largeur"], 1500);
    assert_eq!(j["detail_violations"].as_array().unwrap().len(), 1000);
    assert_eq!(j["detail_violations_tronque"], true);
    for (genre, textes) in j["exemples"].as_object().unwrap() {
        assert!(textes.as_array().unwrap().len() <= 15, "{genre}");
    }
    assert_eq!(j["exemples"]["largeur"].as_array().unwrap().len(), 15);
}
