//! placeur_routage : moteur générique de placement et de routage de circuits imprimés (lot A, contrat §5.1).
//!
//! ```text
//! placeur_routage campagne --donnees F --sortie D --graine N [--parametres P] [--threads T] [--evenements] [--arret FICHIER]
//! placeur_routage router   --donnees F --placement F --sortie D [--parametres P] [--threads T] [--evenements] [--arret FICHIER]
//! placeur_routage verifier --donnees F --placement F --routage F [--couture F] [--mode strict|altium] [--sans-grille] --sortie F
//! placeur_routage parametres-defaut [--qualite apercu|rapide|normal|soigne]
//! placeur_routage estimer --donnees F [--parametres P]
//! placeur_routage etalonner
//! placeur_routage --version
//! ```
//!
//! Codes de sortie (§1.4) : 0 fait ; 2 entrée invalide (message en français sur la sortie d'erreur, sans chemin, aucun
//! fichier lu avant que toutes les options soient valides) ; 3 arrêt demandé ; 4 erreur interne (panique attrapée).
//! Aucune variable d'environnement n'est lue (§0.2-2) : tout le réglage est dans `--parametres` (`parametres_moteur/1`) ;
//! le pool de fils a un nombre de fils et une pile explicites.
//!
//! `campagne` et `router` lisent et contrôlent leurs options et leurs entrées (données `donnees_placeur/2`, placement,
//! mémoire estimée), construisent le modèle interne du cœur de `9b5b5a3` (`adaptateur.rs`) et exécutent le moteur
//! (`moteur.rs`) : sorties `placements/2`, `routage/2` et `stats/2`, vérification finale par `validite.rs`.

// Cœur du moteur de `9b5b5a3` : fonctions héritées encore sans appelant (comparaisons, dessins) tolérées.
mod adaptateur;
#[allow(dead_code)]
mod campagne;
#[allow(dead_code)]
mod conjoint;
#[allow(dead_code)]
mod detail;
// Lecteur de `donnees_placeur/2` : la structure typée est lue par le cœur à partir de l'étape 3 (code mort toléré
// jusque-là pour les champs sans lecteur).
#[allow(dead_code)]
mod donnees;
#[allow(dead_code)]
mod donnees_v1;
mod empreinte;
#[cfg(test)]
mod essais;
mod estimation;
mod fichiers;
#[allow(dead_code)]
mod forme;
#[allow(dead_code)]
mod finition_exacte;
#[allow(dead_code)]
mod geom;
#[allow(dead_code)]
mod globalr;
mod moteur;
mod parametres;
#[allow(dead_code)]
mod placement_exact;
#[allow(dead_code)]
mod recuit;
#[allow(dead_code)]
mod regles;
#[allow(dead_code)]
mod sortie;
// `src/validite.rs` (lot E, jalon E0), jamais modifié ici ; `cout_commun` est appelé par le cœur à l'étape 4.
#[allow(dead_code)]
mod validite;
mod verification;

use parametres::Niveau;
use serde_json::Value;
use std::collections::BTreeMap;
use std::io::Write;
use std::path::Path;

/// Version du moteur : version du paquet et commit de construction (`PLACEUR_COMMIT`, lu à la construction).
pub fn version_moteur() -> String {
    format!("{}+{}", env!("CARGO_PKG_VERSION"), option_env!("PLACEUR_COMMIT").unwrap_or("inconnu"))
}

// ----------------------------------------------------------------------------------------------------------- fils

/// Pile de chaque fil du pool de calcul (explicite : la variable de la bibliothèque standard n'est jamais lue).
const PILE_FILS: usize = 64 * 1024 * 1024;
/// Nombre de fils au plus.
const FILS_MAX: usize = 1024;

/// Nombre de fils effectif : la valeur demandée, ou le parallélisme disponible si 0.
pub fn nombre_fils(demande: usize) -> usize {
    if demande > 0 {
        demande.min(FILS_MAX)
    } else {
        std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1)
    }
}

/// Pool `rayon` local, au nombre de fils et à la pile explicites (§0.2-2 : `rayon` ne lit alors aucune variable).
pub fn construire_pool(fils: usize) -> Result<rayon::ThreadPool, String> {
    rayon::ThreadPoolBuilder::new()
        .num_threads(nombre_fils(fils))
        .stack_size(PILE_FILS)
        .thread_name(|i| format!("placeur-{}", i))
        .build()
        .map_err(|e| format!("pool de fils impossible à créer : {}", e))
}

/// Exécute `travail` dans le pool (fils à pile explicite) ; une panique se propage à l'appelant, qui l'attrape.
pub fn executer_dans_pool<T: Send>(fils: usize, travail: impl FnOnce() -> T + Send) -> Result<T, String> {
    let pool = construire_pool(fils)?;
    Ok(pool.install(travail))
}

/// Texte d'une panique attrapée.
fn message_panique(p: &(dyn std::any::Any + Send)) -> String {
    if let Some(s) = p.downcast_ref::<&str>() {
        s.to_string()
    } else if let Some(s) = p.downcast_ref::<String>() {
        s.clone()
    } else {
        "panique sans message".to_string()
    }
}

/// Crochet de panique : message en français, nom du fichier source seulement (jamais un chemin de construction).
fn installer_crochet_panique() {
    std::panic::set_hook(Box::new(|info| {
        let lieu = info
            .location()
            .map(|l| format!(" ({}:{})", l.file().rsplit(['/', '\\']).next().unwrap_or(""), l.line()))
            .unwrap_or_default();
        let _ = writeln!(std::io::stderr(), "erreur interne : {}{}", message_panique(info.payload()), lieu);
    }));
}

// ----------------------------------------------------------------------------------------------------------- options

/// Option d'une sous-commande : nom, et si elle prend une valeur. Une valeur n'est recopiée dans un message que si
/// elle est courte et sans séparateur de chemin (fonction `sur`).
struct SpecOption {
    nom: &'static str,
    valeur: bool,
}

const fn opt(nom: &'static str, valeur: bool) -> SpecOption {
    SpecOption { nom, valeur }
}

const OPTS_CAMPAGNE: &[SpecOption] = &[
    opt("--donnees", true),
    opt("--sortie", true),
    opt("--graine", true),
    opt("--parametres", true),
    opt("--threads", true),
    opt("--evenements", false),
    opt("--arret", true),
    opt("--mode-routage", true),
];
const OPTS_ROUTER: &[SpecOption] = &[
    opt("--donnees", true),
    opt("--placement", true),
    opt("--routage-initial", true),
    opt("--sortie", true),
    opt("--parametres", true),
    opt("--threads", true),
    opt("--evenements", false),
    opt("--arret", true),
    opt("--mode-routage", true),
];
const OPTS_VERIFIER: &[SpecOption] = &[
    opt("--donnees", true),
    opt("--placement", true),
    opt("--routage", true),
    opt("--couture", true),
    opt("--mode", true),
    opt("--sans-grille", false),
    opt("--sortie", true),
];
const OPTS_DEFAUT: &[SpecOption] = &[opt("--qualite", true)];
const OPTS_ESTIMER: &[SpecOption] = &[opt("--donnees", true), opt("--parametres", true)];
const OPTS_ETALONNER: &[SpecOption] = &[];

/// Sous-commandes et leurs options.
const SOUS_COMMANDES: &[(&str, &[SpecOption])] = &[
    ("campagne", OPTS_CAMPAGNE),
    ("router", OPTS_ROUTER),
    ("verifier", OPTS_VERIFIER),
    ("parametres-defaut", OPTS_DEFAUT),
    ("estimer", OPTS_ESTIMER),
    ("etalonner", OPTS_ETALONNER),
];

/// Options supprimées (§5.1, annexe A) et ce qui les remplace.
const SUPPRIMEES: &[(&str, &str)] = &[
    ("--bloc-fige", "plus de bloc figé : toutes les pièces non fixes sont placées et routées"),
    ("--bloc-libre", "plus de bloc figé : c'est désormais le seul fonctionnement"),
    ("--cuivre-bloc", "plus de bloc figé ; le cuivre à garder se déclare dans les contraintes (cuivre_a_garder)"),
    ("--pastilles", "la géométrie réelle des pastilles est dans le fichier --donnees"),
    ("--gpu", "aucun calcul sur GPU"),
    ("--conjoint", "la phase conjointe fait partie de la sous-commande campagne (réglages conjointe.* du fichier --parametres)"),
    ("--routage-depart", "pour router un placement donné : sous-commande router"),
    ("--depart", "pour router un placement donné : sous-commande router --placement"),
    ("--sortie-conjoint", "une seule sortie : --sortie"),
    ("--chaines-conjoint", "réglages conjointe.* du fichier --parametres"),
    ("--campagne", "sous-commande campagne"),
    ("--top-detail", "le mode comparatif est supprimé"),
    ("--sans-detail", "réglage campagne.arret_apres = \"recuit\" du fichier --parametres"),
    ("--via-dans-pad", "dérogation via_dans_pastille_meme_reseau des contraintes, portée par le fichier --donnees"),
    ("--mouvements", "réglage recuit.mouvements_max du fichier --parametres"),
    ("--budget-s", "réglages conjointe.essais_par_piece_mobile ou conjointe.essais du fichier --parametres"),
    ("--boucle", "réglage boucle.tours du fichier --parametres"),
    ("--iters-det", "réglage routage.iters_negociation du fichier --parametres"),
    ("--tours-rr", "réglage routage.tours_arrachage du fichier --parametres"),
    ("--periode", "réglage recuit.periode_reroutage du fichier --parametres"),
    ("--pas", "réglage recuit.pas_grille_globale_mm du fichier --parametres"),
    ("--t0", "réglage boucle.t0_facteur du fichier --parametres"),
    ("--sigma", "réglage boucle.sigma_mm du fichier --parametres"),
    ("--esp-prox", "réglage routage.espacement.prox du fichier --parametres"),
    ("--esp-coude", "réglage routage.espacement.coude du fichier --parametres"),
    ("--esp-allong", "réglage routage.espacement.allongement_max du fichier --parametres"),
    ("--sans-espacement", "réglage routage.espacement.actif = false du fichier --parametres"),
    ("--mouv-local", "réglage boucle.mouvements_locaux du fichier --parametres"),
    ("--chaines", "réglage campagne.chaines du fichier --parametres"),
];

const USAGE: &str = "\
Usage :
  placeur_routage campagne --donnees F --sortie D --graine N [--parametres P] [--threads T] [--evenements] [--arret FICHIER] [--mode-routage M]
  placeur_routage router   --donnees F --placement F --sortie D [--parametres P] [--threads T] [--evenements] [--arret FICHIER] [--mode-routage M] [--routage-initial F]
  placeur_routage verifier --donnees F --placement F --routage F [--couture F] [--mode strict|altium] [--sans-grille] --sortie F
  placeur_routage parametres-defaut [--qualite apercu|rapide|normal|soigne]
  placeur_routage estimer --donnees F [--parametres P]
  placeur_routage etalonner
  placeur_routage --version
Tout le réglage du moteur est dans le fichier --parametres (format parametres_moteur/1) ;
« placeur_routage parametres-defaut » en donne le contenu complet.
--mode-routage M (auto, octogonal, manhattan, libre, multi) remplace routage.mode du fichier --parametres.";

/// Texte sûr à recopier dans un message (jamais un chemin) : court, lettres, chiffres et `-_.+` seulement.
fn sur(s: &str) -> Option<&str> {
    let ok = !s.is_empty() && s.chars().count() <= 40 && s.chars().all(|c| c.is_ascii_alphanumeric() || "-_.+".contains(c));
    if ok {
        Some(s)
    } else {
        None
    }
}

/// Nom de format sûr à recopier : `famille/N`, N en chiffres.
fn format_sur(f: &str) -> &str {
    match f.split_once('/') {
        Some((famille, n)) if sur(famille).is_some() && !n.is_empty() && n.len() <= 6 && n.chars().all(|c| c.is_ascii_digit()) => f,
        _ => "(format illisible)",
    }
}

/// Options lues : nom → valeur (`None` pour un drapeau).
type Options = BTreeMap<&'static str, Option<String>>;

fn analyser_options(sous: &str, args: &[String], spec: &'static [SpecOption]) -> Result<Options, String> {
    let mut o: Options = BTreeMap::new();
    let mut i = 0;
    while i < args.len() {
        let a = args[i].as_str();
        if let Some((nom, motif)) = SUPPRIMEES.iter().find(|(n, _)| *n == a) {
            return Err(format!("option {} supprimée : {}", nom, motif));
        }
        match spec.iter().find(|s| s.nom == a) {
            Some(s) => {
                if o.contains_key(s.nom) {
                    return Err(format!("option {} donnée deux fois", s.nom));
                }
                if s.valeur {
                    match args.get(i + 1) {
                        Some(v) if !v.starts_with("--") && !v.is_empty() => {
                            o.insert(s.nom, Some(v.clone()));
                            i += 1;
                        }
                        _ => return Err(format!("valeur manquante après {}", s.nom)),
                    }
                } else {
                    o.insert(s.nom, None);
                }
            }
            None => {
                if let Some((avant, _)) = a.split_once('=') {
                    if spec.iter().any(|s| s.nom == avant) {
                        return Err(format!("écrire « {} valeur » (avec une espace) et non « {}=valeur »", avant, avant));
                    }
                }
                if a.starts_with('-') {
                    let nom = sur(a).unwrap_or("(option illisible)");
                    let ailleurs: Vec<&str> = SOUS_COMMANDES
                        .iter()
                        .filter(|(n, sp)| *n != sous && sp.iter().any(|s| s.nom == a))
                        .map(|(n, _)| *n)
                        .collect();
                    if !ailleurs.is_empty() {
                        return Err(format!("option {} non acceptée par {} (elle sert à {})", nom, sous, ailleurs.join(", ")));
                    }
                    let mut m = format!("option inconnue {} pour {}", nom, sous);
                    if let Some((p, d)) = parametres::plus_proche(a, spec.iter().map(|s| s.nom)) {
                        if d <= 3 {
                            m = format!("{} ; vouliez-vous dire {} ?", m, p);
                        }
                    }
                    return Err(m);
                }
                return Err(format!("argument inattendu en position {} après {} (les options commencent par --)", i + 2, sous));
            }
        }
        i += 1;
    }
    Ok(o)
}

fn exiger(o: &Options, sous: &str, requises: &[&str]) -> Result<(), String> {
    for r in requises {
        if !o.contains_key(r) {
            return Err(format!("option obligatoire manquante pour {} : {}", sous, r));
        }
    }
    Ok(())
}

fn valeur<'a>(o: &'a Options, nom: &str) -> Option<&'a str> {
    o.get(nom).and_then(|v| v.as_deref())
}

fn entier(o: &Options, nom: &str, max: u64) -> Result<Option<u64>, String> {
    match valeur(o, nom) {
        None => Ok(None),
        Some(v) => match v.parse::<u64>() {
            Ok(n) if n <= max => Ok(Some(n)),
            _ => Err(format!("{} : nombre entier de 0 à {} attendu, trouvé « {} »", nom, max, sur(v).unwrap_or("(valeur illisible)"))),
        },
    }
}

// ----------------------------------------------------------------------------------------------------------- entrées

/// Fichier d'entrée lu : nom, empreinte SHA-256 des octets (décision D4), valeur JSON, avertissements de lecture.
struct Entree {
    nom: String,
    sha256: String,
    valeur: Value,
    avertissements: Vec<String>,
}

fn lire_entree(chemin: &str) -> Result<Entree, String> {
    let p = Path::new(chemin);
    let nom = fichiers::nom_fichier(p);
    let octets = fichiers::lire_octets(p)?;
    let sha256 = empreinte::sha256_hex(&octets);
    let lu = fichiers::decoder_texte(&octets, &nom);
    let valeur = fichiers::analyser_json(&lu.texte, &nom)?;
    Ok(Entree { nom, sha256, valeur, avertissements: lu.avertissements })
}

/// Contrôle de la version majeure d'un format (§0.5).
fn controler_format(e: &Entree, attendu: &str) -> Result<(), String> {
    let (famille, _) = attendu.split_once('/').unwrap_or((attendu, ""));
    match e.valeur.get("format") {
        Some(Value::String(f)) if f == attendu => Ok(()),
        Some(Value::String(f)) if f.starts_with(&format!("{}/", famille)) => {
            Err(format!("{} : format {} non pris en charge ({} attendu)", e.nom, format_sur(f), attendu))
        }
        Some(_) => Err(format!("{} : champ « format » : {} attendu", e.nom, attendu)),
        None => Err(format!("{} : champ « format » manquant ({} attendu)", e.nom, attendu)),
    }
}

/// Paramètres : fichier `--parametres` fusionné sur les défauts, ou défauts du niveau `normal` ; empreinte (D4 : celle
/// du texte vide sans fichier).
fn lire_parametres(o: &Options) -> Result<(parametres::Lus, String, String), String> {
    match valeur(o, "--parametres") {
        None => Ok((parametres::sans_fichier(), empreinte::sha256_hex(b""), "défauts du niveau normal".to_string())),
        Some(ch) => {
            let p = Path::new(ch);
            let nom = fichiers::nom_fichier(p);
            let octets = fichiers::lire_octets(p)?;
            let lu = fichiers::decoder_texte(&octets, &nom);
            let mut lus = parametres::fusionner_texte(&lu.texte, &nom)?;
            let mut av = lu.avertissements;
            av.append(&mut lus.avertissements);
            lus.avertissements = av;
            Ok((lus, empreinte::sha256_hex(&octets), nom))
        }
    }
}

/// Données du moteur : version majeure contrôlée (§0.5), puis lecture stricte de `donnees_placeur/2` (`donnees.rs`).
fn lire_donnees_placeur(e: &Entree) -> Result<donnees::Donnees, String> {
    controler_format(e, "donnees_placeur/2")?;
    donnees::lire(&e.valeur).map_err(|m| format!("{} : {}", e.nom, m))
}

/// Placement d'entrée : liste v1 ou `placements/2`.
fn controler_placement(e: &Entree) -> Result<(), String> {
    match &e.valeur {
        Value::Array(_) => Ok(()),
        Value::Object(_) => controler_format(e, "placements/2"),
        _ => Err(format!("{} : placement attendu (liste v1 ou objet placements/2)", e.nom)),
    }
}

// ----------------------------------------------------------------------------------------------------------- exécution

/// Crochets de test (jamais activés par la ligne de commande).
#[derive(Default)]
pub struct Crochets {
    /// provoque une panique dans le pool de calcul (test du code 4)
    pub paniquer: bool,
}

/// Échec d'une commande (code 2) : texte (une erreur par ligne) et rappel de l'usage.
struct Echec {
    texte: String,
    usage: bool,
}

impl From<String> for Echec {
    fn from(texte: String) -> Echec {
        Echec { texte, usage: false }
    }
}

fn avec_usage(texte: String) -> Echec {
    Echec { texte, usage: true }
}

fn commande(args: &[String], out: &mut dyn Write, err: &mut dyn Write, crochets: &Crochets) -> Result<i32, Echec> {
    let premier = match args.first() {
        Some(a) => a.as_str(),
        None => return Err(avec_usage("sous-commande manquante".to_string())),
    };
    if premier == "--version" {
        if args.len() > 1 {
            return Err("--version s'emploie seul".to_string().into());
        }
        let _ = writeln!(out, "placeur_routage {}", version_moteur());
        return Ok(0);
    }
    if matches!(premier, "--aide" | "--help" | "-h" | "aide") {
        let _ = writeln!(out, "placeur_routage {} : placement et routage automatiques de circuits imprimés.\n{}", version_moteur(), USAGE);
        return Ok(0);
    }
    if let Some((nom, motif)) = SUPPRIMEES.iter().find(|(n, _)| *n == premier) {
        return Err(format!("option {} supprimée : {}", nom, motif).into());
    }
    let (sous, spec) = match SOUS_COMMANDES.iter().find(|(n, _)| *n == premier) {
        Some(x) => *x,
        None => {
            if premier.starts_with('-') {
                return Err(avec_usage(format!("sous-commande manquante avant {}", sur(premier).unwrap_or("les options"))));
            }
            let mut m = match sur(premier) {
                Some(s) => format!("sous-commande inconnue « {} »", s),
                None => "sous-commande inconnue".to_string(),
            };
            if let Some((p, d)) = parametres::plus_proche(premier, SOUS_COMMANDES.iter().map(|(n, _)| *n)) {
                if d <= 3 {
                    m = format!("{} ; vouliez-vous dire {} ?", m, p);
                }
            }
            return Err(avec_usage(m));
        }
    };
    let o = analyser_options(sous, &args[1..], spec)?;
    match sous {
        "parametres-defaut" => {
            let niveau = match valeur(&o, "--qualite") {
                None => Niveau::Normal,
                Some(q) => Niveau::depuis_nom(q).ok_or_else(|| {
                    format!("--qualite : niveau « {} » inconnu ; niveaux : apercu, rapide, normal, soigne", sur(q).unwrap_or("?"))
                })?,
            };
            let _ = out.write_all(parametres::texte_ordonne(&parametres::defauts(niveau)).as_bytes());
            Ok(0)
        }
        "campagne" | "router" | "estimer" => {
            let requises: &[&str] = match sous {
                "campagne" => &["--donnees", "--sortie", "--graine"],
                "router" => &["--donnees", "--placement", "--sortie"],
                _ => &["--donnees"],
            };
            exiger(&o, sous, requises)?;
            let graine = entier(&o, "--graine", u32::MAX as u64)?;
            let threads = entier(&o, "--threads", FILS_MAX as u64)?;
            // paramètres d'abord (le nombre de fils peut en venir), puis les entrées, lues dans le pool
            let (mut lus, sha_parametres, nom_parametres) = lire_parametres(&o)?;
            if let Some(mode) = valeur(&o, "--mode-routage") {
                if !parametres::MODES_ROUTAGE.contains(&mode) {
                    return Err(format!("--mode-routage : « {} » inconnu ; modes : {}", sur(mode).unwrap_or("?"), parametres::MODES_ROUTAGE.join(", ")).into());
                }
                lus.parametres.routage.mode = mode.to_string();
                if let Some(r) = lus.valeur.get_mut("routage").and_then(Value::as_object_mut) {
                    r.insert("mode".to_string(), Value::String(mode.to_string()));
                }
            }
            for a in &lus.avertissements {
                let _ = writeln!(err, "avertissement : {}", a);
            }
            let fils = threads.map(|t| t as usize).unwrap_or(lus.parametres.campagne.threads);
            let (estimation, avertissements, code) = executer_dans_pool(fils, || -> Result<(Value, Vec<String>, i32), String> {
                if crochets.paniquer {
                    panic!("panique provoquée par un crochet de test");
                }
                let entree = lire_entree(valeur(&o, "--donnees").unwrap_or_default())?;
                let d = lire_donnees_placeur(&entree)?;
                let est = estimation::estimer(&d, &lus.parametres).map_err(|m| format!("{} : {}", entree.nom, m))?;
                let mut av = entree.avertissements.clone();
                av.extend(est.avertissements.iter().cloned());
                if sous == "estimer" {
                    return Ok((est.en_json(), av, 0));
                }
                estimation::controler_memoire(&est, &lus.parametres)?;
                let mut texte = format!(
                    "entrées contrôlées : {} (sha256 {}), paramètres {} (sha256 {}), {} fil(s), {} chaîne(s), mémoire estimée {} Mo",
                    entree.nom,
                    entree.sha256,
                    nom_parametres,
                    sha_parametres,
                    nombre_fils(fils),
                    lus.parametres.campagne.chaines,
                    est.memoire_mo
                );
                if let Some(g) = graine {
                    texte = format!("{}, graine {}", texte, g);
                }
                let mut poses = None;
                if sous == "router" {
                    let pl = lire_entree(valeur(&o, "--placement").unwrap_or_default())?;
                    controler_placement(&pl)?;
                    poses = Some(donnees::lire_placements(&pl.valeur, &d).map_err(|m| format!("{} : {}", pl.nom, m))?);
                    av.extend(pl.avertissements.iter().cloned());
                    texte = format!("{}, placement {} (sha256 {})", texte, pl.nom, pl.sha256);
                }
                for a in &av {
                    let _ = writeln!(std::io::stderr(), "avertissement : {}", a);
                }
                println!("{}", texte);
                let en = moteur::Entrees {
                    valeur_donnees: &entree.valeur,
                    donnees: &d,
                    sha_donnees: entree.sha256.clone(),
                    sha_parametres: sha_parametres.clone(),
                    lus: &lus,
                    fils,
                    memoire_estimee_mo: est.memoire_mo,
                    connexions: estimation::connexions(&d),
                    evenements: o.contains_key("--evenements"),
                    arret: valeur(&o, "--arret").map(std::path::PathBuf::from),
                    routage_initial: valeur(&o, "--routage-initial").map(std::path::PathBuf::from),
                };
                let code = moteur::executer(&en, graine, poses, Path::new(valeur(&o, "--sortie").unwrap_or_default()))?;
                Ok((Value::String(texte), Vec::new(), code))
            })??;
            for a in &avertissements {
                let _ = writeln!(err, "avertissement : {}", a);
            }
            if sous == "estimer" {
                let _ = out.write_all(fichiers::texte_json(&estimation).as_bytes());
            } else if let Value::String(t) = &estimation {
                let _ = writeln!(err, "{}", t);
            }
            Ok(code)
        }
        "verifier" => {
            exiger(&o, sous, &["--donnees", "--placement", "--routage", "--sortie"])?;
            let mode = match valeur(&o, "--mode") {
                None | Some("strict") => validite::Mode::Strict,
                Some("altium") => validite::Mode::Altium,
                Some(m) => return Err(format!("--mode : « {} » inconnu ; modes : strict, altium", sur(m).unwrap_or("?")).into()),
            };
            let entree = lire_entree(valeur(&o, "--donnees").unwrap_or_default())?;
            lire_donnees_placeur(&entree)?;
            let pl = lire_entree(valeur(&o, "--placement").unwrap_or_default())?;
            controler_placement(&pl)?;
            let ro = lire_entree(valeur(&o, "--routage").unwrap_or_default())?;
            let couture = match valeur(&o, "--couture") {
                Some(c) => Some(lire_entree(c)?),
                None => None,
            };
            for e in [Some(&entree), Some(&pl), Some(&ro), couture.as_ref()].into_iter().flatten() {
                for a in &e.avertissements {
                    let _ = writeln!(err, "avertissement : {}", a);
                }
            }
            let demande = verification::Demande {
                donnees: &entree.valeur,
                placements: &pl.valeur,
                routage: &ro.valeur,
                couture: couture.as_ref().map(|c| &c.valeur),
                mode,
                grille: !o.contains_key("--sans-grille"),
            };
            let verdict = verification::verdict_complet(&verification::Reference, &demande)
                .map_err(|m| format!("vérification refusée (entrée invalide) : {}", m))?;
            let sortie = Path::new(valeur(&o, "--sortie").unwrap_or_default());
            fichiers::ecrire_json_atomique(sortie, &verdict)?;
            Ok(0)
        }
        "etalonner" => {
            let (score, travail, duree) = estimation::etalonner();
            let _ = writeln!(
                err,
                "étalonnage : {} expansions (contrôle {}) en {:.2} s",
                travail.expansions, travail.controle, duree
            );
            let _ = out.write_all(fichiers::texte_json(&serde_json::json!({"format": "etalonnage/1", "score": score})).as_bytes());
            Ok(0)
        }
        _ => Err(format!("sous-commande {} non traitée", sous).into()),
    }
}

/// Exécute une ligne de commande (arguments sans le nom du programme) ; rend le code de sortie de §1.4.
pub fn executer(args: &[String], out: &mut dyn Write, err: &mut dyn Write) -> i32 {
    executer_avec(args, out, err, &Crochets::default())
}

fn executer_avec(args: &[String], out: &mut dyn Write, err: &mut dyn Write, crochets: &Crochets) -> i32 {
    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| commande(args, out, err, crochets)));
    let code = match r {
        Ok(Ok(code)) => code,
        Ok(Err(e)) => {
            for l in e.texte.lines() {
                let _ = writeln!(err, "erreur : {}", l);
            }
            if e.usage {
                let _ = writeln!(err, "{}", USAGE);
            }
            2
        }
        Err(p) => {
            let _ = writeln!(err, "erreur interne : panique attrapée ({}) ; code 4", message_panique(&*p));
            4
        }
    };
    let _ = out.flush();
    let _ = err.flush();
    code
}

fn main() {
    installer_crochet_panique();
    let args: Result<Vec<String>, _> = std::env::args_os().skip(1).map(|a| a.into_string()).collect();
    // flux non verrouillés : un fil du pool doit pouvoir écrire sur la sortie d'erreur pendant le calcul
    let (mut out, mut err) = (std::io::stdout(), std::io::stderr());
    let code = match args {
        Ok(a) => executer(&a, &mut out, &mut err),
        Err(_) => {
            let _ = writeln!(err, "erreur : argument illisible (caractères non Unicode)");
            2
        }
    };
    std::process::exit(code);
}

// ----------------------------------------------------------------------------------------------------------- tests

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fichiers::tests::dossier_essai;

    fn lancer(args: &[&str]) -> (i32, String, String) {
        lancer_avec(args, &Crochets::default())
    }

    fn lancer_avec(args: &[&str], crochets: &Crochets) -> (i32, String, String) {
        let a: Vec<String> = args.iter().map(|s| s.to_string()).collect();
        let (mut out, mut err) = (Vec::new(), Vec::new());
        let code = executer_avec(&a, &mut out, &mut err, crochets);
        (code, String::from_utf8(out).unwrap(), String::from_utf8(err).unwrap())
    }

    /// Aucun message ne recopie un chemin reçu.
    fn sans_chemin(texte: &str, dossier: &Path) {
        let d = dossier.to_string_lossy();
        assert!(!texte.contains(d.as_ref()), "chemin recopié dans {:?}", texte);
        assert!(!texte.contains("essais_unitaires"), "chemin recopié dans {:?}", texte);
    }

    #[test]
    fn version() {
        let (code, out, err) = lancer(&["--version"]);
        assert_eq!(code, 0, "{}", err);
        assert_eq!(env!("CARGO_PKG_VERSION"), "2.0.0");
        assert_eq!(out, format!("placeur_routage 2.0.0+{}\n", option_env!("PLACEUR_COMMIT").unwrap_or("inconnu")));
        assert_eq!(lancer(&["--version", "--graine"]).0, 2);
    }

    #[test]
    fn sans_argument_code_2() {
        let (code, out, err) = lancer(&[]);
        assert_eq!(code, 2);
        assert!(out.is_empty());
        assert!(err.starts_with("erreur : sous-commande manquante"), "{}", err);
    }

    #[test]
    fn options_supprimees_code_2() {
        for (opt, _) in SUPPRIMEES {
            for mut args in [
                vec!["campagne", "--donnees", "d.json", "--sortie", "s", "--graine", "1"],
                vec!["router", "--donnees", "d.json", "--placement", "p.json", "--sortie", "s"],
            ] {
                let sous = args[0];
                args.push(opt);
                args.push("5");
                let (code, out, err) = lancer(&args);
                assert_eq!(code, 2, "{} {}", sous, opt);
                assert!(out.is_empty());
                assert!(err.contains(&format!("option {} supprimée", opt)), "{}", err);
                assert!(!err.contains("d.json"), "aucun chemin recopié : {}", err);
            }
            // aussi en tête de ligne de commande (ancienne forme)
            let (code, _, err) = lancer(&[opt]);
            assert_eq!(code, 2);
            assert!(err.contains("supprimée"), "{}", err);
        }
        // les cinq options du critère A3 avec leur renvoi au fichier de paramètres
        let (_, _, err) = lancer(&["campagne", "--donnees", "d.json", "--sortie", "s", "--graine", "1", "--mouvements", "5"]);
        assert!(err.contains("recuit.mouvements_max"), "{}", err);
        let (_, _, err) = lancer(&["campagne", "--donnees", "d.json", "--sortie", "s", "--graine", "1", "--chaines", "4"]);
        assert!(err.contains("campagne.chaines"), "{}", err);
    }

    #[test]
    fn option_inconnue_code_2() {
        let (code, _, err) = lancer(&["campagne", "--donees", "d.json"]);
        assert_eq!(code, 2);
        assert!(err.contains("option inconnue --donees pour campagne ; vouliez-vous dire --donnees ?"), "{}", err);
        let (code, _, err) = lancer(&["campagne", "--placement", "p.json"]);
        assert_eq!(code, 2);
        assert!(err.contains("non acceptée par campagne"), "{}", err);
        let (code, _, err) = lancer(&["campagne", "--graine=3"]);
        assert_eq!(code, 2);
        assert!(err.contains("avec une espace"), "{}", err);
        let (code, _, err) = lancer(&["campagne", "chemin/vers/fichier.json"]);
        assert_eq!(code, 2);
        assert!(!err.contains("chemin/vers"), "{}", err);
        let (code, _, err) = lancer(&["campagnes"]);
        assert_eq!(code, 2);
        assert!(err.contains("vouliez-vous dire campagne ?"), "{}", err);
        let (code, _, err) = lancer(&["campagne", "--graine", "1", "--graine", "2"]);
        assert_eq!(code, 2);
        assert!(err.contains("donnée deux fois"), "{}", err);
        let (code, _, err) = lancer(&["campagne", "--sortie", "--graine", "1"]);
        assert_eq!(code, 2);
        assert!(err.contains("valeur manquante après --sortie"), "{}", err);
    }

    #[test]
    fn sous_commande_sans_donnees_code_2() {
        for (sous, args) in [
            ("campagne", vec!["campagne", "--sortie", "s", "--graine", "1"]),
            ("router", vec!["router", "--placement", "p.json", "--sortie", "s"]),
            ("verifier", vec!["verifier", "--placement", "p.json", "--routage", "r.json", "--sortie", "v.json"]),
            ("estimer", vec!["estimer"]),
        ] {
            let (code, out, err) = lancer(&args);
            assert_eq!(code, 2, "{}", sous);
            assert!(out.is_empty());
            assert!(err.contains(&format!("option obligatoire manquante pour {} : --donnees", sous)), "{}", err);
        }
        let (code, _, err) = lancer(&["campagne"]);
        assert_eq!(code, 2);
        assert!(err.contains("--donnees"), "{}", err);
    }

    #[test]
    fn valeurs_d_options_controlees() {
        let (code, _, err) = lancer(&["campagne", "--donnees", "d.json", "--sortie", "s", "--graine", "abc"]);
        assert_eq!(code, 2);
        assert!(err.contains("--graine : nombre entier"), "{}", err);
        let (code, _, err) = lancer(&["campagne", "--donnees", "d.json", "--sortie", "s", "--graine", "1", "--threads", "-2"]);
        assert_eq!(code, 2);
        assert!(err.contains("valeur manquante après --threads") || err.contains("--threads"), "{}", err);
        let (code, _, err) = lancer(&["verifier", "--donnees", "d", "--placement", "p", "--routage", "r", "--sortie", "v", "--mode", "rapide"]);
        assert_eq!(code, 2);
        assert!(err.contains("--mode : « rapide » inconnu"), "{}", err);
        let (code, _, err) = lancer(&["parametres-defaut", "--qualite", "maximale"]);
        assert_eq!(code, 2);
        assert!(err.contains("niveau « maximale » inconnu"), "{}", err);
    }

    #[test]
    fn parametres_defaut_des_quatre_niveaux() {
        let (code, out, _) = lancer(&["parametres-defaut"]);
        assert_eq!(code, 0);
        let annexe: Value = serde_json::from_str(parametres::tests::ANNEXE_C_AVENANT_D).unwrap();
        assert_eq!(serde_json::from_str::<Value>(&out).unwrap(), annexe, "sortie égale à l'annexe C (avenant 2.2, partie D)");
        for n in Niveau::TOUS {
            let (code, out, err) = lancer(&["parametres-defaut", "--qualite", n.nom()]);
            assert_eq!(code, 0, "{}", err);
            let v: Value = serde_json::from_str(&out).unwrap();
            assert_eq!(v, parametres::defauts(n));
            // la sortie se relit comme fichier de paramètres
            assert_eq!(parametres::fusionner_texte(&out, "p.json").unwrap().valeur, v);
        }
    }

    #[test]
    fn panique_de_test_code_4() {
        let d = dossier_essai("panique_de_test");
        let donnees = d.join("donnees.json");
        let ch = donnees.to_string_lossy().into_owned();
        let crochets = Crochets { paniquer: true };
        let (code, _, err) = lancer_avec(&["campagne", "--donnees", &ch, "--sortie", "s", "--graine", "1", "--threads", "2"], &crochets);
        assert_eq!(code, 4, "{}", err);
        assert!(err.contains("erreur interne : panique attrapée (panique provoquée par un crochet de test)"), "{}", err);
        sans_chemin(&err, &d);
    }

    /// Réglages courts pour les essais de bout en bout (carte de 10 pièces mobiles).
    const PARAMETRES_COURTS: &str =
        r#"{"campagne": {"chaines": 2}, "recuit": {"mouvements_max": 3000}, "boucle": {"tours": 1}, "conjointe": {"essais": 5}}"#;

    /// Contrôle des sorties d'une exécution de `campagne` ou `router` : formats du contrat, `stats.valide` égal au
    /// verdict de `validite.rs`.
    fn controler_sorties(dir: &Path, graine: u64) -> Value {
        let lire = |nom: &str| -> Value { serde_json::from_slice(&std::fs::read(dir.join(nom)).unwrap()).unwrap() };
        let st = lire("stats.json");
        assert_eq!(st["format"], "stats/2");
        assert_eq!(st["graine"], graine);
        assert!(st["parametres_effectifs"]["campagne"].get("threads").is_none());
        for p in ["chargement", "placement_initial", "recuit", "routage_detaille", "boucle", "conjointe", "finition", "verification", "ecriture"] {
            assert!(st["execution"]["temps_phases_s"][p].is_number(), "{} : {}", p, st);
        }
        assert_eq!(lire("placements_routage.json")["format"], "placements/2");
        let ro = lire("routage_detaille.json");
        assert_eq!(ro["format"], "routage/2");
        assert!(ro["couches_routage"].as_array().map(|c| !c.is_empty()).unwrap_or(false));
        let v = &st["validite"];
        assert_eq!(v["format"], "verdict/2", "{}", st);
        let valide = v["valide"] == Value::Bool(true) && v["placement"]["valide"] == Value::Bool(true);
        assert_eq!(st["valide"], Value::Bool(valide && st["cause"] == ""), "{}", st);
        st
    }

    #[test]
    fn entrees_controlees_puis_campagne() {
        let d = dossier_essai("entrees_controlees");
        let donnees = d.join("donnees_placeur.json");
        let texte_carte = fichiers::texte_json(&essais::carte_complete());
        std::fs::write(&donnees, texte_carte.as_bytes()).unwrap();
        let params = d.join("parametres_moteur.json");
        std::fs::write(&params, format!("\u{FEFF}{}", PARAMETRES_COURTS).as_bytes()).unwrap();
        let (cd, cp) = (donnees.to_string_lossy().into_owned(), params.to_string_lossy().into_owned());
        let sortie = d.join("sortie");
        let cs = sortie.to_string_lossy().into_owned();
        let (code, out, err) = lancer(&["campagne", "--donnees", &cd, "--sortie", &cs, "--graine", "7", "--parametres", &cp, "--threads", "2"]);
        assert_eq!(code, 0, "{}", err);
        assert!(out.is_empty());
        let st = controler_sorties(&sortie, 7);
        assert_eq!(st["empreintes"]["donnees_sha256"], empreinte::sha256_hex(texte_carte.as_bytes()));
        assert_eq!(st["execution"]["fils"], 2);
        // fichier d'arrêt présent : code 3, stats.fin = arret_demande, jamais valide
        let arret = d.join("ARRET");
        std::fs::write(&arret, b"").unwrap();
        let ca = arret.to_string_lossy().into_owned();
        let (code, _, e2) = lancer(&["campagne", "--donnees", &cd, "--sortie", &cs, "--graine", "7", "--parametres", &cp, "--arret", &ca]);
        assert_eq!(code, 3, "{}", e2);
        let sa: Value = serde_json::from_slice(&std::fs::read(sortie.join("stats.json")).unwrap()).unwrap();
        assert_eq!(sa["fin"], "arret_demande");
        assert_eq!(sa["valide"], false);
        assert!(err.contains(&empreinte::sha256_hex(texte_carte.as_bytes())), "empreinte des données : {}", err);
        assert!(err.contains("2 chaîne(s)") && err.contains("graine 7") && err.contains("2 fil(s)"), "{}", err);
        sans_chemin(&err, &d);
        // version majeure inconnue des données ⇒ 2 avec le message de §0.5
        std::fs::write(&donnees, b"{\"format\": \"donnees_placeur/3\"}").unwrap();
        let (code, _, err) = lancer(&["campagne", "--donnees", &cd, "--sortie", "s", "--graine", "7"]);
        assert_eq!(code, 2);
        assert!(err.contains("donnees_placeur.json : format donnees_placeur/3 non pris en charge"), "{}", err);
        // clé de paramètre inconnue ⇒ 2 avec le chemin et la suggestion, avant toute lecture des données
        std::fs::write(&params, b"{\"routage\": {\"espacment\": {}}}").unwrap();
        let (code, _, err) = lancer(&["estimer", "--donnees", &cd, "--parametres", &cp]);
        assert_eq!(code, 2);
        assert_eq!(err, "erreur : parametres_moteur.json : routage.espacment : clé inconnue ; vouliez-vous dire espacement ?\n");
        // fichier absent : nom seul
        let absent = d.join("absent.json").to_string_lossy().into_owned();
        let (code, _, err) = lancer(&["estimer", "--donnees", &absent]);
        assert_eq!(code, 2);
        assert_eq!(err, "erreur : absent.json : fichier introuvable\n");
        // paramètres en cp1252 : avertissement
        std::fs::write(&params, b"{\"_c\": \"r\xE9glage\"}").unwrap();
        std::fs::write(&donnees, b"{\"format\": \"donnees_placeur/2\"}").unwrap();
        let (code, _, err) = lancer(&["estimer", "--donnees", &cd, "--parametres", &cp]);
        assert_eq!(code, 2);
        assert!(err.starts_with("avertissement : parametres_moteur.json : le fichier n'est pas en UTF-8"), "{}", err);
        sans_chemin(&err, &d);
    }

    /// Écrit la carte complète, son placement aux poses cibles et un routage vide ; rend leurs chemins.
    fn ecrire_essai(d: &Path) -> (String, String, String) {
        let c = essais::carte_complete();
        let ch = |nom: &str, v: &Value| {
            let p = d.join(nom);
            fichiers::ecrire_json_atomique(&p, v).unwrap();
            p.to_string_lossy().into_owned()
        };
        (ch("donnees_placeur.json", &c), ch("placement.json", &essais::placement_cible(&c)), ch("routage.json", &essais::routage_vide(&c)))
    }

    #[test]
    fn verifier_ecrit_un_verdict_avec_placement() {
        let d = dossier_essai("verifier");
        let (cd, cp, cr) = ecrire_essai(&d);
        let sortie = d.join("v.json").to_string_lossy().into_owned();
        let (code, out, err) = lancer(&["verifier", "--donnees", &cd, "--placement", &cp, "--routage", &cr, "--sortie", &sortie]);
        assert_eq!(code, 0, "{}", err);
        assert!(out.is_empty());
        let v: Value = serde_json::from_slice(&std::fs::read(&sortie).unwrap()).unwrap();
        assert_eq!(v["format"], "verdict/2");
        assert_eq!(v["mode"], "strict");
        assert_eq!(v["placement"]["format"], "controle_placement/1", "{}", v);
        // --mode altium, --sans-grille et --couture acceptés
        let couture = d.join("couture.json");
        std::fs::write(&couture, br#"{"format": "vias_couture/2", "unite": "mil", "reseaux": ["MASSE"], "pas_grille_mm": 2.5, "vias": [], "refus": {}}"#).unwrap();
        let cc = couture.to_string_lossy().into_owned();
        let (code, _, err) = lancer(&[
            "verifier", "--donnees", &cd, "--placement", &cp, "--routage", &cr, "--couture", &cc, "--mode", "altium", "--sans-grille", "--sortie", &sortie,
        ]);
        assert_eq!(code, 0, "{}", err);
        let v: Value = serde_json::from_slice(&std::fs::read(&sortie).unwrap()).unwrap();
        assert_eq!(v["mode"], "altium");
        // données fautives ⇒ 2, message sans chemin, verdict précédent non remplacé
        let mut fautive = essais::carte_complete();
        fautive["composants"][0]["pastilles"][0]["reseau"] = Value::from("INCONNU");
        fichiers::ecrire_json_atomique(Path::new(&cd), &fautive).unwrap();
        let (code, _, err) = lancer(&["verifier", "--donnees", &cd, "--placement", &cp, "--routage", &cr, "--sortie", &sortie]);
        assert_eq!(code, 2);
        assert!(err.contains("donnees_placeur.json : donnees.composants[0].pastilles[0].reseau : réseau INCONNU non déclaré"), "{}", err);
        sans_chemin(&err, &d);
        // routage fautif (refusé par validite.rs) ⇒ 2
        std::fs::write(&cr, b"{\"format\": \"routage/9\"}").unwrap();
        fichiers::ecrire_json_atomique(Path::new(&cd), &essais::carte_complete()).unwrap();
        let (code, _, err) = lancer(&["verifier", "--donnees", &cd, "--placement", &cp, "--routage", &cr, "--sortie", &sortie]);
        assert_eq!(code, 2, "{}", err);
        sans_chemin(&err, &d);
    }

    #[test]
    fn estimer_et_memoire_max() {
        let d = dossier_essai("estimer");
        let (cd, cp, _) = ecrire_essai(&d);
        let (code, out, err) = lancer(&["estimer", "--donnees", &cd]);
        assert_eq!(code, 0, "{}", err);
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["format"], "estimation/1");
        for k in ["memoire_mo", "cellules_detail", "pas_grille_mm", "couches_routage", "connexions", "pieces_mobiles", "essais_conjointe"] {
            assert!(v[k].is_number(), "{} : {}", k, v);
        }
        assert_eq!(v.as_object().unwrap().len(), 8);
        assert_eq!(v["couches_routage"], 3);
        assert_eq!(v["pieces_mobiles"], 10);
        assert_eq!(v["essais_conjointe"], 60, "⌊6,0 × 10 + 0,5⌋");
        // mémoire estimée au-delà de campagne.memoire_max_mo ⇒ 2 pour campagne et router
        let params = d.join("parametres_moteur.json");
        std::fs::write(&params, br#"{"campagne": {"memoire_max_mo": 1}}"#).unwrap();
        let pp = params.to_string_lossy().into_owned();
        for args in [
            vec!["campagne", "--donnees", &cd, "--sortie", "s", "--graine", "1", "--parametres", &pp],
            vec!["router", "--donnees", &cd, "--placement", &cp, "--sortie", "s", "--parametres", &pp],
        ] {
            let (code, _, err) = lancer(&args);
            assert_eq!(code, 2);
            assert!(err.contains("mémoire estimée") && err.contains("campagne.memoire_max_mo (1 Mo)"), "{}", err);
            sans_chemin(&err, &d);
        }
        // sans limite : routage du placement donné (contrôlé), sorties du contrat
        let sortie = d.join("sortie");
        let cs = sortie.to_string_lossy().into_owned();
        std::fs::write(&params, PARAMETRES_COURTS.as_bytes()).unwrap();
        let (code, _, err) = lancer(&["router", "--donnees", &cd, "--placement", &cp, "--sortie", &cs, "--parametres", &pp]);
        assert_eq!(code, 0, "{}", err);
        assert!(err.contains("placement placement.json"), "{}", err);
        controler_sorties(&sortie, 0);
        std::fs::write(&cp, br#"[{"designator": "X9", "side": "top", "x": 0, "y": 0, "rotation": 0}]"#).unwrap();
        let (code, _, err) = lancer(&["router", "--donnees", &cd, "--placement", &cp, "--sortie", &cs]);
        assert_eq!(code, 2);
        assert!(err.contains("placement.json : placement[0].designator : pièce X9 inconnue"), "{}", err);
    }

    #[test]
    fn etalonnage_travail_identique() {
        let a = estimation::travail_etalonnage(12);
        let b = estimation::travail_etalonnage(12);
        assert_eq!(a, b);
        assert!(a.expansions > 10_000, "{:?}", a);
        // le travail croît avec le nombre de recherches (les premières sont identiques)
        assert!(estimation::travail_etalonnage(24).expansions > a.expansions);
    }

    /// Le binaire de test se relance dans un dossier vide (dossier courant du processus enfant) et y exécute
    /// `campagne` sans argument : code 2, et le dossier reste vide.
    #[test]
    fn dossier_vide_reste_vide() {
        let d = dossier_essai("dossier_vide");
        let exe = std::env::current_exe().unwrap();
        let enfant = std::process::Command::new(exe)
            .args(["--exact", "tests::processus_enfant_campagne_sans_argument", "--ignored", "--test-threads", "1"])
            .current_dir(&d)
            .output()
            .unwrap();
        let sortie = String::from_utf8_lossy(&enfant.stdout);
        assert!(enfant.status.success() && sortie.contains("1 passed"), "{}", sortie);
        assert_eq!(std::fs::read_dir(&d).unwrap().count(), 0, "le dossier doit rester vide");
    }

    #[test]
    #[ignore = "lancé dans un processus enfant par dossier_vide_reste_vide"]
    fn processus_enfant_campagne_sans_argument() {
        let (code, out, err) = lancer(&["campagne"]);
        assert_eq!(code, 2);
        assert!(out.is_empty() && err.contains("--donnees"), "{}", err);
    }

    /// Récursion d'environ 12 Mo de pile (un fil a 2 Mo par défaut) : rend une somme et l'étendue de pile parcourue
    /// (écart d'adresse entre le premier et le dernier appel).
    fn recursion_profonde(n: usize, haut: usize) -> (u64, usize) {
        let mut t = [0u8; 4096];
        t[n % 4096] = n as u8;
        let t = std::hint::black_box(t);
        let ici = &t as *const [u8; 4096] as usize;
        let haut = if haut == 0 { ici } else { haut };
        if n == 0 {
            return (t[0] as u64, haut.abs_diff(ici));
        }
        // l'élément lu après l'appel garde le tableau de 4 Ko sur la pile pendant toute la récursion
        let (s, etendue) = recursion_profonde(n - 1, haut);
        (s + t[n % 4096] as u64, etendue)
    }

    #[test]
    fn pool_explicite_nombre_de_fils_et_pile() {
        let pool = construire_pool(3).unwrap();
        assert_eq!(pool.current_num_threads(), 3);
        let (n, (s, etendue)) = pool.install(|| (rayon::current_num_threads(), recursion_profonde(3000, 0)));
        assert_eq!(n, 3);
        assert!(s > 0);
        assert!(etendue > 8 << 20, "pile parcourue : {} octets", etendue);
        // 0 = parallélisme disponible (pool non construit ici : machine partagée)
        assert_eq!(nombre_fils(0), std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1));
        assert_eq!(nombre_fils(5), 5);
        // chaque fil du pool a la pile explicite
        let pool = construire_pool(2).unwrap();
        let etendues: Vec<usize> = pool.broadcast(|_| recursion_profonde(3000, 0).1);
        assert_eq!(etendues.len(), 2);
        assert!(etendues.iter().all(|&e| e > 8 << 20), "{:?}", etendues);
        // une panique dans le pool remonte à l'appelant (attrapée par executer : code 4)
        let r = std::panic::catch_unwind(|| executer_dans_pool(2, || panic!("essai")));
        assert!(r.is_err());
    }
}
