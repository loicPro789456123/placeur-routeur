//! Binaire d'essai du lot E : `validite_verifier`.
//!
//! Il inclut `src/validite.rs` en module **privé**, comme le moteur du lot A (`mod validite;`) : sa construction
//! montre les avertissements que le lot A aurait. Ligne de commande (mêmes options et même sortie que
//! `placeur_routage verifier`, §5.1, plus `--mesures`, propre à la caisse d'essai, qui écrit `mesures/2` calculé par
//! `validite::cout_commun`) :
//!
//! ```text
//! validite_verifier verifier --donnees F --placement F --routage F [--couture F] [--mode strict|altium]
//!                            [--sans-grille] --sortie F [--mesures F]
//! ```
//!
//! Codes de sortie (§1.4) : 0 fait (verdict dans le fichier écrit), 2 entrée invalide, 4 erreur interne. Les
//! messages ne nomment que des fichiers, jamais un dossier, et ne recopient jamais une valeur reçue en argument
//! (§0.2-7 : arguments intervertis, un chemin pourrait s'y trouver) ; seuls les noms d'options simples sont cités.
//! Jalon E0 : la vérification elle-même est provisoire (voir l'en-tête de `validite.rs`).

#[path = "../../src/validite.rs"]
mod validite;

use serde_json::{Map, Value};
use std::io::ErrorKind;
use std::path::Path;
use std::process::ExitCode;
use std::time::Duration;

const USAGE: &str = "usage : validite_verifier verifier --donnees F --placement F --routage F [--couture F] \
                     [--mode strict|altium] [--sans-grille] --sortie F [--mesures F]";

struct Options {
    donnees: String,
    placement: String,
    routage: String,
    couture: Option<String>,
    mode: validite::Mode,
    grille: bool,
    sortie: String,
    mesures: Option<String>,
}

/// Nom du fichier seul (jamais le dossier, §0.2-7).
fn nom_fichier(chemin: &str) -> String {
    Path::new(chemin)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "(fichier)".to_string())
}

/// « (--x) » pour citer une option reçue dans un message, seulement si c'est un nom d'option simple (un ou deux
/// tirets, puis 1 à 30 lettres minuscules ASCII, chiffres ou tirets) ; sinon rien : un argument qui contient un
/// chemin ou une valeur n'est jamais recopié (§0.2-7).
fn citer_option(o: &str) -> String {
    let nom = o.strip_prefix("--").or_else(|| o.strip_prefix('-')).unwrap_or("");
    let simple = !nom.is_empty()
        && nom.len() <= 30
        && nom.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-');
    if simple {
        format!(" ({o})")
    } else {
        String::new()
    }
}

fn raison_io(e: &std::io::Error) -> &'static str {
    match e.kind() {
        ErrorKind::NotFound => "fichier ou dossier introuvable",
        ErrorKind::PermissionDenied => "accès refusé",
        _ => "erreur d'entrée-sortie",
    }
}

fn lire_options(args: &[String]) -> Result<Options, String> {
    let mut it = args.iter();
    match it.next().map(String::as_str) {
        Some("verifier") => {}
        Some(c) if c.starts_with('-') => {
            return Err(format!("commande manquante avant l'option{}\n{USAGE}", citer_option(c)))
        }
        Some(_) => return Err(format!("commande inconnue (seule « verifier » existe)\n{USAGE}")),
        None => return Err(USAGE.to_string()),
    }
    let mut valeurs: Map<String, Value> = Map::new();
    let mut grille = true;
    while let Some(a) = it.next() {
        match a.as_str() {
            "--donnees" | "--placement" | "--routage" | "--couture" | "--mode" | "--sortie" | "--mesures" => {
                let v = it.next().ok_or_else(|| format!("{a} : valeur manquante"))?;
                if valeurs.insert(a.clone(), Value::from(v.as_str())).is_some() {
                    return Err(format!("{a} : option donnée deux fois"));
                }
            }
            "--sans-grille" => grille = false,
            o if o.starts_with('-') => return Err(format!("option inconnue{}\n{USAGE}", citer_option(o))),
            _ => return Err(format!("argument inattendu (valeur sans option)\n{USAGE}")),
        }
    }
    let lire = |cle: &str| valeurs.get(cle).and_then(Value::as_str).map(str::to_string);
    let obligatoire = |cle: &str| lire(cle).ok_or_else(|| format!("option {cle} obligatoire\n{USAGE}"));
    let mode = match lire("--mode").as_deref() {
        None | Some("strict") => validite::Mode::Strict,
        Some("altium") => validite::Mode::Altium,
        // La valeur reçue n'est pas recopiée : ce pourrait être un chemin (arguments intervertis).
        Some(_) => return Err("--mode : valeur non reconnue (strict ou altium attendu)".to_string()),
    };
    Ok(Options {
        donnees: obligatoire("--donnees")?,
        placement: obligatoire("--placement")?,
        routage: obligatoire("--routage")?,
        couture: lire("--couture"),
        mode,
        grille,
        sortie: obligatoire("--sortie")?,
        mesures: lire("--mesures"),
    })
}

fn lire_json(chemin: &str) -> Result<Value, String> {
    let nom = nom_fichier(chemin);
    let octets = std::fs::read(chemin).map_err(|e| format!("{nom} : lecture impossible ({})", raison_io(&e)))?;
    let texte = octets.strip_prefix(b"\xEF\xBB\xBF").unwrap_or(&octets);
    serde_json::from_slice(texte)
        .map_err(|e| format!("{nom} : JSON invalide (ligne {}, colonne {})", e.line(), e.column()))
}

/// Écriture atomique : `<nom>.tmp` puis renommage, réessayé 5 fois toutes les 0,2 s si le fichier est tenu.
fn ecrire_json(chemin: &str, v: &Value) -> Result<(), String> {
    let nom = nom_fichier(chemin);
    let mut texte = serde_json::to_string_pretty(v).map_err(|_| format!("{nom} : sérialisation impossible"))?;
    texte.push('\n');
    let tmp = format!("{chemin}.tmp");
    std::fs::write(&tmp, texte.as_bytes()).map_err(|e| format!("{nom} : écriture impossible ({})", raison_io(&e)))?;
    let mut essais = 0;
    loop {
        match std::fs::rename(&tmp, chemin) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == ErrorKind::PermissionDenied && essais < 5 => {
                essais += 1;
                std::thread::sleep(Duration::from_millis(200));
            }
            Err(e) => {
                let _ = std::fs::remove_file(&tmp);
                return Err(format!("{nom} : écriture impossible ({})", raison_io(&e)));
            }
        }
    }
}

/// `mesures/2` (§2.8) à partir du coût commun.
fn mesures_json(donnees: &Value, c: &validite::CoutCommun) -> Value {
    let defaut = donnees.pointer("/regles/isolement/defaut").and_then(Value::as_f64).unwrap_or(0.0);
    let mut termes = Map::new();
    termes.insert("longueur_mm".into(), Value::from(c.longueur_mm));
    termes.insert("vias".into(), Value::from(c.vias));
    termes.insert("retreci_mm".into(), Value::from(c.retreci_mm));
    termes.insert("resistance_puissance_mohm".into(), Value::from(c.resistance_puissance_mohm));
    termes.insert("coudes".into(), Value::from(c.coudes));
    termes.insert("ecart_moyen_mm".into(), Value::from(c.ecart_moyen_mm));
    let mut parametres = Map::new();
    parametres.insert("seuil_ecart_mm".into(), Value::from(3.0 * defaut));
    parametres.insert("plafond_ecart_mm".into(), Value::from(5.0 * defaut));
    let mut o = Map::new();
    o.insert("format".into(), Value::from("mesures/2"));
    o.insert("formule".into(), Value::from("cout_commun/2"));
    o.insert("cout_commun".into(), Value::from(c.cout));
    o.insert("termes".into(), Value::Object(termes));
    o.insert("parametres".into(), Value::Object(parametres));
    Value::Object(o)
}

fn executer(args: &[String]) -> Result<(), String> {
    let o = lire_options(args)?;
    let donnees = lire_json(&o.donnees)?;
    let placement = lire_json(&o.placement)?;
    let routage = lire_json(&o.routage)?;
    let couture = match &o.couture {
        Some(c) => Some(lire_json(c)?),
        None => None,
    };
    let controle = validite::verifier_placement(&donnees, &placement, o.grille)?;
    let verdict = validite::verifier_routage(&donnees, &placement, &routage, couture.as_ref(), o.mode)?;
    let mut sortie = verdict.en_json();
    if let Value::Object(m) = &mut sortie {
        m.insert("placement".into(), controle.en_json());
    }
    ecrire_json(&o.sortie, &sortie)?;
    if let Some(f) = &o.mesures {
        // Coût commun : verdict strict sur le placement et le routage seuls (couture exclue, §4.7).
        let strict = if o.mode == validite::Mode::Strict && couture.is_none() {
            verdict
        } else {
            validite::verifier_routage(&donnees, &placement, &routage, None, validite::Mode::Strict)?
        };
        let c = validite::cout_commun(&donnees, &routage, &strict);
        ecrire_json(f, &mesures_json(&donnees, &c))?;
    }
    Ok(())
}

fn main() -> ExitCode {
    // Aucune trace de panique (elle nommerait un fichier source) : « erreur interne » et code 4.
    std::panic::set_hook(Box::new(|_| {}));
    let args: Vec<String> = std::env::args_os().skip(1).map(|a| a.to_string_lossy().into_owned()).collect();
    match std::panic::catch_unwind(|| executer(&args)) {
        Ok(Ok(())) => ExitCode::SUCCESS,
        Ok(Err(message)) => {
            eprintln!("validite_verifier : {message}");
            ExitCode::from(2)
        }
        Err(_) => {
            eprintln!("validite_verifier : erreur interne");
            ExitCode::from(4)
        }
    }
}
