//! Paramètres du moteur : format `parametres_moteur/1` (contrat §2.6 et annexe C, modifiés par la partie D de
//! l'avenant 2.2 : `conjointe.essais_par_piece_mobile` = 6,0 au niveau `normal`).
//!
//! - Une seule source fait foi : le schéma ci-dessous, avec ses défauts (niveau `normal`), que rend
//!   `placeur_routage parametres-defaut [--qualite …]` ; les autres niveaux ne diffèrent que par les 6 clés de la table
//!   des niveaux de §2.6.
//! - Fusion profonde du fichier reçu sur les défauts : objet par objet ; un tableau est remplacé en bloc ; un scalaire
//!   est remplacé.
//! - Lecture stricte (§0.5, §2.1) : clé inconnue refusée avec son chemin et la clé la plus proche ; clés commençant par
//!   `_` ignorées à tout niveau ; `null` seulement là où l'annexe C le montre (décision du lot A : pour toute clé de
//!   même nature, par exemple `largeur_preferee_mm` de tous les rôles) ; nombre ou booléen écrit entre guillemets
//!   refusé ; entiers, bornes, énumérations et listes contrôlés ; clés réservées acceptées (type contrôlé) puis
//!   ignorées, avec un avertissement si leur valeur diffère du défaut (décision D22).
use crate::fichiers;
use serde::Deserialize;
use serde_json::{json, Map, Value};

/// Format des paramètres.
pub const FORMAT: &str = "parametres_moteur/1";

// ----------------------------------------------------------------------------------------------------------- niveaux

/// Niveaux de qualité (§2.6, table des niveaux).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Niveau {
    Apercu,
    Rapide,
    Normal,
    Soigne,
}

impl Niveau {
    pub const TOUS: [Niveau; 4] = [Niveau::Apercu, Niveau::Rapide, Niveau::Normal, Niveau::Soigne];

    pub fn nom(self) -> &'static str {
        match self {
            Niveau::Apercu => "apercu",
            Niveau::Rapide => "rapide",
            Niveau::Normal => "normal",
            Niveau::Soigne => "soigne",
        }
    }

    pub fn depuis_nom(s: &str) -> Option<Niveau> {
        Niveau::TOUS.into_iter().find(|n| n.nom() == s)
    }
}

/// Les 6 clés du moteur qui changent d'un niveau à l'autre, avec leur valeur (§2.6 ; avenant 2.2, A.1).
pub fn cles_du_niveau(niveau: Niveau) -> [(&'static str, Value); 6] {
    let (mouvements, iterations, tours, essais, plafond, duree) = match niveau {
        Niveau::Apercu => (json!(20000000), json!(30), json!(0), json!(0.0), json!(0), json!(900)),
        Niveau::Rapide => (json!(20000000), json!(30), json!(1), json!(0.48), json!(600), json!(2700)),
        Niveau::Normal => (json!(250000000), json!(40), json!(4), json!(6.0), json!(1800), json!(7200)),
        Niveau::Soigne => (json!(250000000), json!(40), json!(6), json!(6.37), json!(5400), json!(14400)),
    };
    [
        ("recuit.mouvements_max", mouvements),
        ("routage.iters_negociation", iterations),
        ("boucle.tours", tours),
        ("conjointe.essais_par_piece_mobile", essais),
        ("conjointe.plafond_s", plafond),
        ("campagne.duree_max_s", duree),
    ]
}

// ----------------------------------------------------------------------------------------------------------- schéma

/// Bornes d'un nombre.
#[derive(Clone, Copy, Debug)]
pub struct Bornes {
    pub min: f64,
    pub max: f64,
    pub min_exclu: bool,
    pub max_exclu: bool,
}

const POSITIF: Bornes = Bornes { min: 0.0, max: f64::INFINITY, min_exclu: false, max_exclu: false };
const STRICT_POSITIF: Bornes = Bornes { min: 0.0, max: f64::INFINITY, min_exclu: true, max_exclu: false };
const OUVERT_0_1: Bornes = Bornes { min: 0.0, max: 1.0, min_exclu: true, max_exclu: true };
const FERME_0_1: Bornes = Bornes { min: 0.0, max: 1.0, min_exclu: false, max_exclu: false };
const DEMI_OUVERT_0_1: Bornes = Bornes { min: 0.0, max: 1.0, min_exclu: true, max_exclu: false };
/// Longueur en mm : > 0 et au plus 1 m.
const LONGUEUR_MM: Bornes = Bornes { min: 0.0, max: 1000.0, min_exclu: true, max_exclu: false };
/// Longueur en mm qui peut être nulle.
const LONGUEUR_MM_0: Bornes = Bornes { min: 0.0, max: 1000.0, min_exclu: false, max_exclu: false };
/// Pas de grille en mm : > 0 et au plus 1 mm.
const PAS_MM: Bornes = Bornes { min: 0.0, max: 1.0, min_exclu: true, max_exclu: false };
/// Pas de grille du routeur global en mm : > 0 et au plus 10 mm.
const PAS_GLOBAL_MM: Bornes = Bornes { min: 0.0, max: 10.0, min_exclu: true, max_exclu: false };

const GRAND: i64 = 1_000_000_000_000;

/// Nature d'une clé.
#[derive(Clone, Copy)]
pub enum Genre {
    /// `format` (texte fixe, version majeure contrôlée)
    Format,
    Objet(&'static [Cle]),
    Nombre(Bornes),
    Entier(i64, i64),
    Booleen,
    Choix(&'static [&'static str]),
    /// `"auto"` ou un nombre
    AutoOuNombre(Bornes),
    /// liste de nombres (bornes de chaque élément, longueur minimale)
    ListeNombres(Bornes, usize),
    /// liste de textes pris dans une énumération, sans répétition
    ListeChoix(&'static [&'static str]),
    /// liste de conditions de couche `{"si": condition, "facteur": nombre}` (§2.6)
    ListeConditions,
}

/// Une clé du schéma : nom, nature, `null` permis, réservée, défaut du niveau `normal` (texte JSON ; vide pour un
/// objet, dont le défaut est celui de ses clés).
pub struct Cle {
    pub nom: &'static str,
    pub genre: Genre,
    pub nul: bool,
    pub reserve: bool,
    pub defaut: &'static str,
}

const fn c(nom: &'static str, genre: Genre, defaut: &'static str) -> Cle {
    Cle { nom, genre, nul: false, reserve: false, defaut }
}
const fn c_nul(nom: &'static str, genre: Genre, defaut: &'static str) -> Cle {
    Cle { nom, genre, nul: true, reserve: false, defaut }
}
const fn c_res(nom: &'static str, genre: Genre, defaut: &'static str) -> Cle {
    Cle { nom, genre, nul: false, reserve: true, defaut }
}
const fn c_res_nul(nom: &'static str, genre: Genre, defaut: &'static str) -> Cle {
    Cle { nom, genre, nul: true, reserve: true, defaut }
}
const fn objet(nom: &'static str, cles: &'static [Cle]) -> Cle {
    Cle { nom, genre: Genre::Objet(cles), nul: false, reserve: false, defaut: "" }
}
const fn objet_res(nom: &'static str, cles: &'static [Cle]) -> Cle {
    Cle { nom, genre: Genre::Objet(cles), nul: false, reserve: true, defaut: "" }
}

const fn nb(b: Bornes) -> Genre {
    Genre::Nombre(b)
}
const fn ent(min: i64, max: i64) -> Genre {
    Genre::Entier(min, max)
}
const BOOL: Genre = Genre::Booleen;

/// Rôles des réseaux (§3.3) et conditions de couche (§2.6).
pub const ROLES: &[&str] = &["signal", "alimentation", "puissance", "commutation", "plan"];
pub const CONDITIONS: &[&str] = &["preferee_puissance", "adjacente_plan", "face_convertisseur", "externe", "interne"];
pub const ARRET_APRES: &[&str] = &["recuit", "routage", "complet"];
pub const DEBUG: &[&str] = &["marge", "amorces", "conjointe"];
/// Niveaux du journal (décision du lot A : l'annexe C ne montre que « normal »).
pub const NIVEAUX_JOURNAL: &[&str] = &["normal", "detaille"];

const CAMPAGNE: &[Cle] = &[
    c("threads", ent(0, 1024), "0"),
    c("chaines", ent(1, 256), "4"),
    c("duree_max_s", nb(POSITIF), "7200"),
    c("arret_apres", Genre::Choix(ARRET_APRES), "\"complet\""),
    c("seuil_abandon_echecs", ent(0, GRAND), "10"),
    c("seuil_abandon_part", nb(FERME_0_1), "0.005"),
    c("memoire_max_mo", nb(POSITIF), "0"),
];

const PLACEMENT: &[Cle] = &[c("degagement_pas_fin_mm", nb(LONGUEUR_MM_0), "0.8"), c_res("copier_motifs", BOOL, "false")];

const AFFINAGE: &[Cle] = &[
    c("fraction_mouvements", nb(FERME_0_1), "0.05"),
    c("acceptation", nb(OUVERT_0_1), "0.03"),
    c("t_fin_relative", nb(DEMI_OUVERT_0_1), "0.01"),
    c("alpha", nb(OUVERT_0_1), "0.85"),
    c("palier_par_piece", ent(1, GRAND), "25"),
];

const RECUIT: &[Cle] = &[
    c("mouvements_max", ent(0, GRAND), "250000000"),
    c("alpha", nb(OUVERT_0_1), "0.996"),
    c("palier_par_piece", ent(1, GRAND), "100"),
    c("acceptation_initiale", nb(OUVERT_0_1), "0.85"),
    c("t_fin_relative", nb(DEMI_OUVERT_0_1), "1e-06"),
    c("rudy_capacite", nb(STRICT_POSITIF), "2.0"),
    c("rudy_lambda", nb(POSITIF), "2.0"),
    c("rechauffes", ent(0, 1000), "0"),
    c("arret_paliers_sans_progres", ent(1, GRAND), "375"),
    c("periode_reroutage", ent(0, GRAND), "3000"),
    c("pas_grille_globale_mm", nb(PAS_GLOBAL_MM), "0.5"),
    objet("affinage", AFFINAGE),
];

const BOUCLE: &[Cle] = &[
    c("tours", ent(0, 1000), "4"),
    c("mouvements_locaux", ent(0, GRAND), "60000"),
    c("rayon_zone_mm", nb(LONGUEUR_MM), "3.5"),
    c("poids_initial", nb(POSITIF), "30"),
    c("rayon_pieces_mm", nb(LONGUEUR_MM_0), "4.0"),
    c("t0_facteur", nb(STRICT_POSITIF), "0.15"),
    c("sigma_mm", nb(LONGUEUR_MM), "2.5"),
    c_res("reroutage_local", BOOL, "false"),
];

const ESPACEMENT: &[Cle] = &[
    c("actif", BOOL, "true"),
    c("prox", nb(POSITIF), "1.0"),
    c("coude", nb(POSITIF), "0.3"),
    c("allongement_max", nb(POSITIF), "0.15"),
];

const SENS_PAR_COUCHE: &[Cle] = &[c("actif", BOOL, "false"), c("penalite", nb(POSITIF), "0.2")];

const GLOBAL: &[Cle] = &[
    c("cout_via", nb(POSITIF), "2.0"),
    c("penalite_via", nb(POSITIF), "2.0"),
    c("penalite_debordement", nb(POSITIF), "5.0"),
    c("penalite_echec", nb(POSITIF), "100.0"),
    c("max_expansions", ent(1, GRAND), "250000"),
    c("rayon_echappement_cellules", ent(0, 10000), "8"),
];

const ROUTAGE: &[Cle] = &[
    c("iters_negociation", ent(0, 10000), "40"),
    c("tours_arrachage", ent(0, 10000), "4"),
    c_res("arret_si_aggravation", BOOL, "false"),
    c("ordre_pas_fin_d_abord", BOOL, "true"),
    c("pas_grille_detail_mm", Genre::AutoOuNombre(PAS_MM), "\"auto\""),
    c("largeur_plancher_mm", nb(LONGUEUR_MM_0), "0.254"),
    c("eventail_auto", BOOL, "true"),
    c("eventail_pas_sortie_mm", Genre::AutoOuNombre(LONGUEUR_MM), "\"auto\""),
    c("eventail_longueur_mm", nb(LONGUEUR_MM_0), "0.3"),
    c("amorce_eventail_paires", BOOL, "false"),
    c("amorce_moyenne", BOOL, "false"),
    c("ponts_plans", BOOL, "false"),
    c("rayon_via_plan_mm", nb(LONGUEUR_MM), "6.0"),
    c("fenetre_mm", nb(LONGUEUR_MM), "15.0"),
    objet("espacement", ESPACEMENT),
    objet_res("sens_par_couche", SENS_PAR_COUCHE),
    c_res("copier_motifs", BOOL, "false"),
    objet("global", GLOBAL),
];

const SURFACES_PUISSANCE: &[Cle] = &[
    c("actif", BOOL, "false"),
    c("roles", Genre::ListeChoix(ROLES), "[\"puissance\", \"commutation\"]"),
    c("largeur_min_mm", nb(LONGUEUR_MM), "1.0"),
];

const FINITION: &[Cle] = &[
    c("reparer_tours", ent(0, 10000), "4"),
    c_res_nul("reparer_si_echecs_max", ent(0, GRAND), "null"),
    c("routage_complet", BOOL, "false"),
    c("routage_complet_iters", ent(0, 10000), "80"),
    c("routage_complet_tours", ent(0, 10000), "8"),
    c("verification_reparer_tours", ent(0, 10000), "2"),
    c_res("lissage_marches", BOOL, "false"),
    objet_res("surfaces_puissance", SURFACES_PUISSANCE),
];

const CONJOINTE: &[Cle] = &[
    c_nul("essais", ent(0, GRAND), "null"),
    c("essais_par_piece_mobile", nb(POSITIF), "6.0"),
    c("plafond_s", nb(POSITIF), "1800"),
    c("t0", nb(STRICT_POSITIF), "3.0"),
    c("t_fin", nb(STRICT_POSITIF), "0.05"),
    c("sigma_mm", nb(LONGUEUR_MM), "1.5"),
    c("poids_proximite", nb(POSITIF), "1.0"),
    c("periode_zone", ent(0, GRAND), "40"),
    c("max_expansions", ent(1, GRAND), "200000"),
];

const COUTS_CONJOINTE: &[Cle] = &[
    c("longueur", nb(POSITIF), "1.0"),
    c("via", nb(POSITIF), "1.5"),
    c("retreci", nb(POSITIF), "2.0"),
    c("resistance_carre", nb(POSITIF), "1.5"),
    c("segment", nb(POSITIF), "0.1"),
    c("echec", nb(POSITIF), "100000"),
];

const COUTS_CONVERTISSEUR: &[Cle] = &[
    c("boucle_mm2", nb(POSITIF), "8"),
    c("perimetre", nb(POSITIF), "10"),
    c("noeud", nb(POSITIF), "15"),
    c("courts", nb(POSITIF), "10"),
    c("decouplage_pilote", nb(POSITIF), "10"),
    c("bulk", nb(POSITIF), "3"),
    c("sensible", nb(POSITIF), "40"),
    c("distance_sensible_mm", nb(LONGUEUR_MM_0), "4"),
];

const COUTS_DECOUPLAGE: &[Cle] = &[c("limite_mm", nb(LONGUEUR_MM_0), "4.0"), c("poids", nb(POSITIF), "25.0")];

const COUTS: &[Cle] = &[
    objet("conjointe", COUTS_CONJOINTE),
    objet("convertisseur", COUTS_CONVERTISSEUR),
    objet("decouplage", COUTS_DECOUPLAGE),
];

const LISTE_MM: Genre = Genre::ListeNombres(LONGUEUR_MM, 0);
const LISTE_MM_NON_VIDE: Genre = Genre::ListeNombres(LONGUEUR_MM, 1);

const SIGNAL_ROLE: &[Cle] = &[
    c("priorite", ent(0, 1000), "8"),
    c_nul("largeur_preferee_mm", nb(LONGUEUR_MM), "null"),
    c("largeur_preferee_pastille", BOOL, "false"),
    c("paliers_mm", LISTE_MM, "[0.3, 0.254]"),
    c("facteur_via", nb(POSITIF), "1"),
    c("vias_doubles", BOOL, "false"),
    c_nul("couches", Genre::ListeConditions, "null"),
    c("resistance_conjointe", BOOL, "false"),
    c("sorties_longues", BOOL, "false"),
];

const ALIMENTATION_ROLE: &[Cle] = &[
    c("priorite", ent(0, 1000), "7"),
    c_nul("largeur_preferee_mm", nb(LONGUEUR_MM), "null"),
    c("largeur_preferee_pastille", BOOL, "false"),
    c("paliers_mm", LISTE_MM, "[0.3, 0.254]"),
    c("facteur_via", nb(POSITIF), "3"),
    c("vias_doubles", BOOL, "false"),
    c_nul("couches", Genre::ListeConditions, "null"),
    c("resistance_conjointe", BOOL, "false"),
    c("sorties_longues", BOOL, "false"),
];

const PUISSANCE_ROLE: &[Cle] = &[
    c("priorite", ent(0, 1000), "1"),
    c_nul("largeur_preferee_mm", nb(LONGUEUR_MM), "null"),
    c("largeur_preferee_pastille", BOOL, "false"),
    c("paliers_mm", LISTE_MM, "[1.5, 1.0, 0.5, 0.3]"),
    c("facteur_via", nb(POSITIF), "3"),
    c("vias_doubles", BOOL, "true"),
    c_nul("couches", Genre::ListeConditions, "null"),
    c("resistance_conjointe", BOOL, "true"),
    c("sorties_longues", BOOL, "false"),
];

const COMMUTATION_ROLE: &[Cle] = &[
    c("priorite", ent(0, 1000), "3"),
    c_nul("largeur_preferee_mm", nb(LONGUEUR_MM), "2.0"),
    c("largeur_preferee_pastille", BOOL, "true"),
    c("paliers_mm", LISTE_MM, "[1.5, 1.0, 0.5, 0.3, 0.254]"),
    c("facteur_via", nb(POSITIF), "1"),
    c("vias_doubles", BOOL, "true"),
    c_nul(
        "couches",
        Genre::ListeConditions,
        "[{\"si\": \"face_convertisseur\", \"facteur\": 1.0}, {\"si\": \"externe\", \"facteur\": 1.2}, \
         {\"si\": \"interne\", \"facteur\": 3.0}]",
    ),
    c("resistance_conjointe", BOOL, "false"),
    c("sorties_longues", BOOL, "true"),
];

const PLAN_ROLE: &[Cle] = &[
    c("priorite", ent(0, 1000), "6"),
    c("descentes_mm", LISTE_MM_NON_VIDE, "[0.3, 0.254]"),
    c("descente_puissance_mm", LISTE_MM_NON_VIDE, "[0.5, 0.3]"),
    c("rayon_vias_doubles_mm", nb(LONGUEUR_MM_0), "2.5"),
    c("facteur_via", nb(POSITIF), "1"),
    c("vias_doubles", BOOL, "false"),
    c_nul("couches", Genre::ListeConditions, "null"),
    c("resistance_conjointe", BOOL, "false"),
    c("sorties_longues", BOOL, "false"),
];

const ROLES_CLES: &[Cle] = &[
    objet("signal", SIGNAL_ROLE),
    objet("alimentation", ALIMENTATION_ROLE),
    objet("puissance", PUISSANCE_ROLE),
    objet("commutation", COMMUTATION_ROLE),
    objet("plan", PLAN_ROLE),
];

const SENSIBILITE_NORMALE: &[Cle] = &[
    c_nul("couches", Genre::ListeConditions, "null"),
    c("proximite", nb(POSITIF), "1.0"),
    c_nul("priorite_signal", ent(0, 1000), "null"),
];

const SENSIBILITE_SENSIBLE: &[Cle] = &[
    c_nul(
        "couches",
        Genre::ListeConditions,
        "[{\"si\": \"preferee_puissance\", \"facteur\": 3.0}, {\"si\": \"adjacente_plan\", \"facteur\": 1.0}, \
         {\"si\": \"externe\", \"facteur\": 1.2}, {\"si\": \"interne\", \"facteur\": 3.0}]",
    ),
    c("proximite", nb(POSITIF), "2.0"),
    c_nul("priorite_signal", ent(0, 1000), "4"),
];

const SENSIBILITE_PEU_SENSIBLE: &[Cle] = &[
    c_nul("couches", Genre::ListeConditions, "null"),
    c("proximite", nb(POSITIF), "0.3"),
    c_nul("priorite_signal", ent(0, 1000), "null"),
];

const SENSIBILITES: &[Cle] = &[
    objet("normale", SENSIBILITE_NORMALE),
    objet("sensible", SENSIBILITE_SENSIBLE),
    objet("peu_sensible", SENSIBILITE_PEU_SENSIBLE),
];

const COUCHE_PREFEREE: &[Cle] = &[c("penalite_ailleurs", nb(STRICT_POSITIF), "1.5")];

const JOURNAL: &[Cle] = &[
    c("niveau", Genre::Choix(NIVEAUX_JOURNAL), "\"normal\""),
    c("debug", Genre::ListeChoix(DEBUG), "[]"),
];

/// Schéma complet, dans l'ordre de l'annexe C.
pub const SCHEMA: &[Cle] = &[
    c("format", Genre::Format, "\"parametres_moteur/1\""),
    objet("campagne", CAMPAGNE),
    objet("placement", PLACEMENT),
    objet("recuit", RECUIT),
    objet("boucle", BOUCLE),
    objet("routage", ROUTAGE),
    objet("finition", FINITION),
    objet("conjointe", CONJOINTE),
    objet("couts", COUTS),
    objet("roles", ROLES_CLES),
    objet("sensibilites", SENSIBILITES),
    objet("couche_preferee", COUCHE_PREFEREE),
    objet("journal", JOURNAL),
];

/// Clés d'une condition de couche, dans l'ordre d'écriture.
const CLES_CONDITION: &[&str] = &["si", "facteur"];

// ----------------------------------------------------------------------------------------------------------- défauts

fn defauts_objet(cles: &[Cle]) -> Value {
    let mut o = Map::new();
    for k in cles {
        let v = match k.genre {
            Genre::Objet(sous) => defauts_objet(sous),
            _ => serde_json::from_str(k.defaut).unwrap_or_else(|_| panic!("défaut illisible pour la clé {}", k.nom)),
        };
        o.insert(k.nom.to_string(), v);
    }
    Value::Object(o)
}

fn pointer_mut<'a>(v: &'a mut Value, chemin: &str) -> &'a mut Value {
    let mut cur = v;
    for p in chemin.split('.') {
        cur = cur.get_mut(p).unwrap_or_else(|| panic!("clé de niveau absente du schéma : {}", chemin));
    }
    cur
}

/// Paramètres complets d'un niveau (valeur JSON).
pub fn defauts(niveau: Niveau) -> Value {
    let mut v = defauts_objet(SCHEMA);
    for (chemin, val) in cles_du_niveau(niveau) {
        *pointer_mut(&mut v, chemin) = val;
    }
    v
}

// ----------------------------------------------------------------------------------------------------------- écriture ordonnée

fn ecrire_ordonne(s: &mut String, v: &Value, cles: Option<&[Cle]>, retrait: usize) {
    match v {
        Value::Object(o) => {
            let ordre: Vec<&str> = match cles {
                Some(cl) => cl.iter().map(|k| k.nom).filter(|n| o.contains_key(*n)).collect(),
                None => o.keys().map(|k| k.as_str()).collect(),
            };
            let mut ordre = ordre;
            for k in o.keys() {
                if !ordre.contains(&k.as_str()) {
                    ordre.push(k);
                }
            }
            s.push_str("{\n");
            for (i, k) in ordre.iter().enumerate() {
                let sous = cles.and_then(|cl| cl.iter().find(|c| c.nom == *k)).and_then(|c| match c.genre {
                    Genre::Objet(x) => Some(x),
                    _ => None,
                });
                s.push_str(&" ".repeat(retrait + 2));
                s.push_str(&serde_json::to_string(k).unwrap_or_default());
                s.push_str(": ");
                ecrire_ordonne(s, &o[*k], sous, retrait + 2);
                if i + 1 < ordre.len() {
                    s.push(',');
                }
                s.push('\n');
            }
            s.push_str(&" ".repeat(retrait));
            s.push('}');
        }
        Value::Array(a) => {
            s.push('[');
            for (i, e) in a.iter().enumerate() {
                if i > 0 {
                    s.push_str(", ");
                }
                ecrire_en_ligne(s, e);
            }
            s.push(']');
        }
        _ => s.push_str(&serde_json::to_string(v).unwrap_or_default()),
    }
}

/// Valeur sur une seule ligne ; les conditions de couche gardent l'ordre « si », « facteur ».
fn ecrire_en_ligne(s: &mut String, v: &Value) {
    match v {
        Value::Object(o) => {
            let mut ordre: Vec<&str> = CLES_CONDITION.iter().copied().filter(|k| o.contains_key(*k)).collect();
            for k in o.keys() {
                if !ordre.contains(&k.as_str()) {
                    ordre.push(k);
                }
            }
            s.push('{');
            for (i, k) in ordre.iter().enumerate() {
                if i > 0 {
                    s.push_str(", ");
                }
                s.push_str(&serde_json::to_string(k).unwrap_or_default());
                s.push_str(": ");
                ecrire_en_ligne(s, &o[*k]);
            }
            s.push('}');
        }
        Value::Array(a) => {
            s.push('[');
            for (i, e) in a.iter().enumerate() {
                if i > 0 {
                    s.push_str(", ");
                }
                ecrire_en_ligne(s, e);
            }
            s.push(']');
        }
        _ => s.push_str(&serde_json::to_string(v).unwrap_or_default()),
    }
}

/// Texte JSON des paramètres, clés dans l'ordre de l'annexe C (sortie de `parametres-defaut`).
pub fn texte_ordonne(v: &Value) -> String {
    let mut s = String::new();
    ecrire_ordonne(&mut s, v, Some(SCHEMA), 0);
    s.push('\n');
    s
}

// ----------------------------------------------------------------------------------------------------------- suggestions

/// Distance d'édition (Levenshtein) sur les caractères, sans tenir compte de la casse.
pub fn distance_edition(a: &str, b: &str) -> usize {
    let a: Vec<char> = a.to_lowercase().chars().collect();
    let b: Vec<char> = b.to_lowercase().chars().collect();
    let mut prec: Vec<usize> = (0..=b.len()).collect();
    for i in 1..=a.len() {
        let mut cour = vec![i; b.len() + 1];
        for j in 1..=b.len() {
            let sub = prec[j - 1] + usize::from(a[i - 1] != b[j - 1]);
            cour[j] = sub.min(prec[j] + 1).min(cour[j - 1] + 1);
        }
        prec = cour;
    }
    prec[b.len()]
}

/// Candidat le plus proche (premier en cas d'égalité) et sa distance.
pub fn plus_proche<'a, I: IntoIterator<Item = &'a str>>(mot: &str, candidats: I) -> Option<(&'a str, usize)> {
    let mut meilleur: Option<(&'a str, usize)> = None;
    for c in candidats {
        let d = distance_edition(mot, c);
        if meilleur.is_none_or(|(_, m)| d < m) {
            meilleur = Some((c, d));
        }
    }
    meilleur
}

/// Chemins complets des clés du schéma qui portent exactement ce nom.
fn chemins_du_nom(nom: &str) -> Vec<String> {
    fn parcourir(cles: &[Cle], prefixe: &str, nom: &str, out: &mut Vec<String>) {
        for k in cles {
            let ch = if prefixe.is_empty() { k.nom.to_string() } else { format!("{}.{}", prefixe, k.nom) };
            if k.nom.eq_ignore_ascii_case(nom) {
                out.push(ch.clone());
            }
            if let Genre::Objet(sous) = k.genre {
                parcourir(sous, &ch, nom, out);
            }
        }
    }
    let mut out = Vec::new();
    parcourir(SCHEMA, "", nom, &mut out);
    out
}

fn texte_cle_inconnue(chemin: &str, cle: &str, voisines: &[&str]) -> String {
    let proche = plus_proche(cle, voisines.iter().copied());
    let ailleurs: Vec<String> = chemins_du_nom(cle).into_iter().filter(|c| c != chemin).collect();
    match proche {
        Some((p, d)) if d <= 2 || ailleurs.is_empty() => format!("{} : clé inconnue ; vouliez-vous dire {} ?", chemin, p),
        _ if !ailleurs.is_empty() => format!("{} : clé inconnue ici ; vouliez-vous dire {} ?", chemin, ailleurs.join(" ou ")),
        _ => format!("{} : clé inconnue", chemin),
    }
}

// ----------------------------------------------------------------------------------------------------------- validation

fn texte_bornes(b: &Bornes) -> String {
    if b.max.is_infinite() {
        if b.min_exclu {
            format!("> {}", b.min)
        } else {
            format!("≥ {}", b.min)
        }
    } else {
        let q = match (b.min_exclu, b.max_exclu) {
            (true, true) => " (bornes exclues)".to_string(),
            (true, false) => format!(" ({} exclu)", b.min),
            (false, true) => format!(" ({} exclu)", b.max),
            (false, false) => String::new(),
        };
        format!("entre {} et {}{}", b.min, b.max, q)
    }
}

fn dans_bornes(x: f64, b: &Bornes) -> bool {
    let bas = if b.min_exclu { x > b.min } else { x >= b.min };
    let haut = if b.max_exclu { x < b.max } else { x <= b.max };
    x.is_finite() && bas && haut
}

fn tronque(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(n).collect::<String>())
    }
}

/// Description courte d'une valeur trouvée, pour les messages.
fn decrire(v: &Value) -> String {
    match v {
        Value::Null => "null".to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => format!("le texte « {} »", tronque(s, 30)),
        Value::Array(_) => "une liste […]".to_string(),
        Value::Object(_) => "un objet {…}".to_string(),
    }
}

fn texte_attendu(g: &Genre) -> String {
    match g {
        Genre::Format => format!("le texte « {} »", FORMAT),
        Genre::Objet(_) => "un objet {…}".to_string(),
        Genre::Nombre(b) => format!("un nombre {}", texte_bornes(b)),
        Genre::Entier(a, b) => format!("un nombre entier entre {} et {}", a, b),
        Genre::Booleen => "true ou false".to_string(),
        Genre::Choix(l) => format!("un texte parmi {}", l.join(", ")),
        Genre::AutoOuNombre(b) => format!("« auto » ou un nombre {}", texte_bornes(b)),
        Genre::ListeNombres(b, _) => format!("une liste de nombres {}", texte_bornes(b)),
        Genre::ListeChoix(l) => format!("une liste de textes parmi {}", l.join(", ")),
        Genre::ListeConditions => "une liste de conditions {\"si\": …, \"facteur\": …}".to_string(),
    }
}

/// Indice pour un texte qui contient un nombre ou un booléen.
fn indice_guillemets(s: &str) -> Option<String> {
    let t = s.trim();
    if t.parse::<f64>().is_ok() {
        return Some(format!("nombre écrit entre guillemets : écrire {} sans guillemets", t));
    }
    if t.contains(',') && t.replace(',', ".").parse::<f64>().is_ok() {
        return Some(format!("nombre écrit entre guillemets avec une virgule décimale : écrire {} sans guillemets", t.replace(',', ".")));
    }
    if t == "true" || t == "false" {
        return Some(format!("booléen écrit entre guillemets : écrire {} sans guillemets", t));
    }
    if t == "null" {
        return Some("écrire null sans guillemets".to_string());
    }
    None
}

fn erreur_type(chemin: &str, g: &Genre, v: &Value) -> String {
    let mut s = format!("{} : {} attendu, trouvé {}", chemin, texte_attendu(g), decrire(v));
    if let Value::String(t) = v {
        if let Some(i) = indice_guillemets(t) {
            s = format!("{} ; {}", s, i);
        }
    }
    s
}

fn valider_nombre(chemin: &str, g: &Genre, b: &Bornes, v: &Value, err: &mut Vec<String>) -> Option<Value> {
    match v {
        Value::Number(n) => {
            let x = n.as_f64().unwrap_or(f64::NAN);
            if dans_bornes(x, b) {
                Some(v.clone())
            } else {
                err.push(format!("{} : {} hors bornes (attendu un nombre {})", chemin, n, texte_bornes(b)));
                None
            }
        }
        _ => {
            err.push(erreur_type(chemin, g, v));
            None
        }
    }
}

fn valider_entier(chemin: &str, g: &Genre, min: i64, max: i64, v: &Value, err: &mut Vec<String>) -> Option<Value> {
    match v {
        Value::Number(n) => {
            let x = n.as_f64().unwrap_or(f64::NAN);
            if x.fract() != 0.0 || !x.is_finite() {
                err.push(format!("{} : nombre entier attendu, trouvé {}", chemin, n));
                return None;
            }
            if x < min as f64 || x > max as f64 {
                err.push(format!("{} : {} hors bornes (attendu un nombre entier entre {} et {})", chemin, n, min, max));
                return None;
            }
            Some(json!(x as i64))
        }
        _ => {
            err.push(erreur_type(chemin, g, v));
            None
        }
    }
}

fn valider_choix(chemin: &str, g: &Genre, liste: &[&str], v: &Value, err: &mut Vec<String>) -> Option<Value> {
    match v {
        Value::String(s) if liste.contains(&s.as_str()) => Some(v.clone()),
        Value::String(s) => {
            let mut m = format!("{} : valeur « {} » inconnue ; valeurs permises : {}", chemin, tronque(s, 30), liste.join(", "));
            if let Some((p, d)) = plus_proche(s, liste.iter().copied()) {
                if d <= 2 {
                    m = format!("{} ; vouliez-vous dire {} ?", m, p);
                }
            }
            err.push(m);
            None
        }
        _ => {
            err.push(erreur_type(chemin, g, v));
            None
        }
    }
}

fn valider_conditions(chemin: &str, g: &Genre, v: &Value, err: &mut Vec<String>) -> Option<Value> {
    let a = match v {
        Value::Array(a) => a,
        _ => {
            err.push(erreur_type(chemin, g, v));
            return None;
        }
    };
    let n0 = err.len();
    let mut sortie = Vec::new();
    for (i, e) in a.iter().enumerate() {
        let ce = format!("{}[{}]", chemin, i);
        let o = match e {
            Value::Object(o) => o,
            _ => {
                err.push(format!("{} : condition {{\"si\": …, \"facteur\": …}} attendue, trouvé {}", ce, decrire(e)));
                continue;
            }
        };
        for k in o.keys() {
            if !k.starts_with('_') && !CLES_CONDITION.contains(&k.as_str()) {
                err.push(texte_cle_inconnue(&format!("{}.{}", ce, k), k, CLES_CONDITION));
            }
        }
        let si = match o.get("si") {
            Some(x) => valider_choix(&format!("{}.si", ce), &Genre::Choix(CONDITIONS), CONDITIONS, x, err),
            None => {
                err.push(format!("{} : clé « si » manquante", ce));
                None
            }
        };
        let facteur = match o.get("facteur") {
            Some(x) => valider_nombre(&format!("{}.facteur", ce), &Genre::Nombre(POSITIF), &POSITIF, x, err),
            None => {
                err.push(format!("{} : clé « facteur » manquante", ce));
                None
            }
        };
        if let (Some(si), Some(facteur)) = (si, facteur) {
            sortie.push(json!({"si": si, "facteur": facteur}));
        }
    }
    if err.len() > n0 {
        None
    } else {
        Some(Value::Array(sortie))
    }
}

/// Valide une valeur feuille (tout sauf un objet) ; rend la valeur normalisée (entiers écrits en entiers).
fn valider_feuille(k: &Cle, chemin: &str, v: &Value, err: &mut Vec<String>) -> Option<Value> {
    if v.is_null() {
        if k.nul {
            return Some(Value::Null);
        }
        err.push(format!("{} : null interdit ici ({} attendu)", chemin, texte_attendu(&k.genre)));
        return None;
    }
    let g = &k.genre;
    match g {
        Genre::Format | Genre::Objet(_) => None,
        Genre::Nombre(b) => valider_nombre(chemin, g, b, v, err),
        Genre::Entier(a, b) => valider_entier(chemin, g, *a, *b, v, err),
        Genre::Booleen => match v {
            Value::Bool(_) => Some(v.clone()),
            _ => {
                err.push(erreur_type(chemin, g, v));
                None
            }
        },
        Genre::Choix(l) => valider_choix(chemin, g, l, v, err),
        Genre::AutoOuNombre(b) => match v {
            Value::String(s) if s == "auto" => Some(v.clone()),
            Value::Number(_) => valider_nombre(chemin, g, b, v, err),
            _ => {
                err.push(erreur_type(chemin, g, v));
                None
            }
        },
        Genre::ListeNombres(b, lmin) => {
            let a = match v {
                Value::Array(a) => a,
                _ => {
                    err.push(erreur_type(chemin, g, v));
                    return None;
                }
            };
            let n0 = err.len();
            for (i, e) in a.iter().enumerate() {
                valider_nombre(&format!("{}[{}]", chemin, i), &Genre::Nombre(*b), b, e, err);
            }
            if a.len() < *lmin {
                err.push(format!("{} : au moins {} élément(s) attendu(s)", chemin, lmin));
            }
            if err.len() > n0 {
                // « [0,3, 0,254] » est lu comme quatre nombres : indice de virgule décimale
                if a.iter().any(|e| e.as_i64() == Some(0)) {
                    let dernier = err.len() - 1;
                    err[dernier].push_str(" ; virgule décimale ? écrire [0.3, 0.254] et non [0,3, 0,254]");
                }
                None
            } else {
                Some(v.clone())
            }
        }
        Genre::ListeChoix(l) => {
            let a = match v {
                Value::Array(a) => a,
                _ => {
                    err.push(erreur_type(chemin, g, v));
                    return None;
                }
            };
            let n0 = err.len();
            let mut vus: Vec<&str> = Vec::new();
            for (i, e) in a.iter().enumerate() {
                let ce = format!("{}[{}]", chemin, i);
                if valider_choix(&ce, &Genre::Choix(l), l, e, err).is_some() {
                    let s = e.as_str().unwrap_or_default();
                    if vus.contains(&s) {
                        err.push(format!("{} : « {} » répété", ce, s));
                    }
                    vus.push(s);
                }
            }
            if err.len() > n0 {
                None
            } else {
                Some(v.clone())
            }
        }
        Genre::ListeConditions => valider_conditions(chemin, g, v, err),
    }
}

/// Fusionne `entree` sur `base` (défauts) selon le schéma `cles` ; les erreurs sont accumulées.
fn fusionner(cles: &[Cle], base: &mut Map<String, Value>, entree: &Map<String, Value>, prefixe: &str, err: &mut Vec<String>) {
    let voisines: Vec<&str> = cles.iter().map(|k| k.nom).collect();
    for (nom, v) in entree {
        if nom.starts_with('_') {
            continue;
        }
        let chemin = if prefixe.is_empty() { nom.clone() } else { format!("{}.{}", prefixe, nom) };
        let k = match cles.iter().find(|k| k.nom == nom) {
            Some(k) => k,
            None => {
                err.push(texte_cle_inconnue(&chemin, nom, &voisines));
                continue;
            }
        };
        match k.genre {
            Genre::Format => {} // contrôlé avant la fusion
            Genre::Objet(sous) => match v {
                Value::Object(o) => {
                    if let Some(Value::Object(b)) = base.get_mut(nom.as_str()) {
                        fusionner(sous, b, o, &chemin, err);
                    }
                }
                _ => err.push(erreur_type(&chemin, &k.genre, v)),
            },
            _ => {
                if let Some(n) = valider_feuille(k, &chemin, v, err) {
                    base.insert(nom.clone(), n);
                }
            }
        }
    }
}

/// Contrôle de `format` (version majeure, §0.5).
fn controler_format(v: Option<&Value>) -> Result<(), String> {
    match v {
        None => Ok(()),
        Some(Value::String(s)) if s == FORMAT => Ok(()),
        Some(Value::String(s)) if s.starts_with("parametres_moteur/") => {
            let n = &s["parametres_moteur/".len()..];
            let affiche = if !n.is_empty() && n.len() <= 6 && n.chars().all(|c| c.is_ascii_digit()) { s.as_str() } else { "(format illisible)" };
            Err(format!("format {} non pris en charge ({} attendu)", affiche, FORMAT))
        }
        Some(x) => Err(format!("format : {} attendu, trouvé {}", FORMAT, decrire(x))),
    }
}

/// Chemins des clés réservées (au sens de §0.5) du schéma.
fn chemins_reserves() -> Vec<String> {
    fn parcourir(cles: &[Cle], prefixe: &str, out: &mut Vec<String>) {
        for k in cles {
            let ch = if prefixe.is_empty() { k.nom.to_string() } else { format!("{}.{}", prefixe, k.nom) };
            if k.reserve {
                out.push(ch);
            } else if let Genre::Objet(sous) = k.genre {
                parcourir(sous, &ch, out);
            }
        }
    }
    let mut out = Vec::new();
    parcourir(SCHEMA, "", &mut out);
    out
}

fn valeur_au_chemin<'a>(v: &'a Value, chemin: &str) -> Option<&'a Value> {
    chemin.split('.').try_fold(v, |cur, p| cur.get(p))
}

/// Paramètres lus : valeur effective complète, structure typée, avertissements (repli cp1252, clés réservées).
#[derive(Clone, Debug)]
pub struct Lus {
    /// valeur effective complète (lue pour `stats.parametres_effectifs` à l'étape 4 : autorisation temporaire)
    #[allow(dead_code)]
    pub valeur: Value,
    pub parametres: Parametres,
    pub avertissements: Vec<String>,
}

const ERREURS_MAX: usize = 20;

/// Fusionne une valeur JSON déjà lue sur les défauts du niveau `normal` (`nom` : nom du fichier, pour les messages).
pub fn fusionner_valeur(entree: &Value, nom: &str) -> Result<Lus, String> {
    let o = match entree {
        Value::Object(o) => o,
        _ => return Err(format!("{} : le fichier doit contenir un objet JSON {{…}}, trouvé {}", nom, decrire(entree))),
    };
    controler_format(o.get("format")).map_err(|e| format!("{} : {}", nom, e))?;
    let mut base = defauts(Niveau::Normal);
    let mut err = Vec::new();
    if let Value::Object(b) = &mut base {
        fusionner(SCHEMA, b, o, "", &mut err);
    }
    if !err.is_empty() {
        let n = err.len();
        let mut lignes: Vec<String> = err.into_iter().take(ERREURS_MAX).map(|e| format!("{} : {}", nom, e)).collect();
        if n > ERREURS_MAX {
            lignes.push(format!("{} : … et {} autre(s) erreur(s)", nom, n - ERREURS_MAX));
        }
        return Err(lignes.join("\n"));
    }
    let normal = defauts(Niveau::Normal);
    let avertissements = chemins_reserves()
        .into_iter()
        .filter(|ch| valeur_au_chemin(&base, ch) != valeur_au_chemin(&normal, ch))
        .map(|ch| format!("{} : {} est réservé : accepté mais sans effet dans cette version", nom, ch))
        .collect();
    let parametres = Parametres::depuis_valeur(&base);
    Ok(Lus { valeur: base, parametres, avertissements })
}

/// Fusionne un texte JSON (lecture stricte) sur les défauts du niveau `normal`.
pub fn fusionner_texte(texte: &str, nom: &str) -> Result<Lus, String> {
    let v = fichiers::analyser_json(texte, nom)?;
    fusionner_valeur(&v, nom)
}

/// Paramètres du niveau `normal`, sans fichier.
pub fn sans_fichier() -> Lus {
    Lus { valeur: defauts(Niveau::Normal), parametres: Parametres::defaut(), avertissements: Vec::new() }
}

// ----------------------------------------------------------------------------------------------------------- structure typée
//
// Champs lus par le cœur du moteur, branché sur ces paramètres à l'étape 3 du lot A : jusque-là, la plupart ne sont
// lus par personne (autorisation temporaire de code mort, notée au journal du lot).

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Parametres {
    pub campagne: Campagne,
    pub placement: Placement,
    pub recuit: Recuit,
    pub boucle: Boucle,
    pub routage: Routage,
    pub finition: Finition,
    pub conjointe: Conjointe,
    pub couts: Couts,
    pub roles: Roles,
    pub sensibilites: Sensibilites,
    pub couche_preferee: CouchePreferee,
    pub journal: Journal,
}

/// `campagne.arret_apres`.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ArretApres {
    Recuit,
    Routage,
    Complet,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Campagne {
    pub threads: usize,
    pub chaines: usize,
    pub duree_max_s: f64,
    pub arret_apres: ArretApres,
    pub seuil_abandon_echecs: usize,
    pub seuil_abandon_part: f64,
    pub memoire_max_mo: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Placement {
    pub degagement_pas_fin_mm: f64,
    pub copier_motifs: bool,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Recuit {
    pub mouvements_max: u64,
    pub alpha: f64,
    pub palier_par_piece: usize,
    pub acceptation_initiale: f64,
    pub t_fin_relative: f64,
    pub rudy_capacite: f64,
    pub rudy_lambda: f64,
    pub rechauffes: usize,
    pub arret_paliers_sans_progres: usize,
    pub periode_reroutage: usize,
    pub pas_grille_globale_mm: f64,
    pub affinage: Affinage,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Affinage {
    pub fraction_mouvements: f64,
    pub acceptation: f64,
    pub t_fin_relative: f64,
    pub alpha: f64,
    pub palier_par_piece: usize,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Boucle {
    pub tours: usize,
    pub mouvements_locaux: usize,
    pub rayon_zone_mm: f64,
    pub poids_initial: f64,
    pub rayon_pieces_mm: f64,
    pub t0_facteur: f64,
    pub sigma_mm: f64,
    pub reroutage_local: bool,
}

/// Valeur `"auto"` ou nombre.
#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum AutoOuNombre {
    Nombre(f64),
    Texte(String),
}

impl AutoOuNombre {
    /// Le nombre donné, ou `None` pour `"auto"`.
    pub fn valeur(&self) -> Option<f64> {
        match self {
            AutoOuNombre::Nombre(x) => Some(*x),
            AutoOuNombre::Texte(_) => None,
        }
    }
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Routage {
    pub iters_negociation: usize,
    pub tours_arrachage: usize,
    pub arret_si_aggravation: bool,
    pub ordre_pas_fin_d_abord: bool,
    pub pas_grille_detail_mm: AutoOuNombre,
    pub largeur_plancher_mm: f64,
    pub eventail_auto: bool,
    pub eventail_pas_sortie_mm: AutoOuNombre,
    pub eventail_longueur_mm: f64,
    pub amorce_eventail_paires: bool,
    pub amorce_moyenne: bool,
    pub ponts_plans: bool,
    pub rayon_via_plan_mm: f64,
    pub fenetre_mm: f64,
    pub espacement: Espacement,
    pub sens_par_couche: SensParCouche,
    pub copier_motifs: bool,
    pub global: RoutageGlobal,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Espacement {
    pub actif: bool,
    pub prox: f64,
    pub coude: f64,
    pub allongement_max: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct SensParCouche {
    pub actif: bool,
    pub penalite: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct RoutageGlobal {
    pub cout_via: f64,
    pub penalite_via: f64,
    pub penalite_debordement: f64,
    pub penalite_echec: f64,
    pub max_expansions: usize,
    pub rayon_echappement_cellules: usize,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Finition {
    pub reparer_tours: usize,
    pub reparer_si_echecs_max: Option<u64>,
    pub routage_complet: bool,
    pub routage_complet_iters: usize,
    pub routage_complet_tours: usize,
    pub verification_reparer_tours: usize,
    pub lissage_marches: bool,
    pub surfaces_puissance: SurfacesPuissance,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct SurfacesPuissance {
    pub actif: bool,
    pub roles: Vec<String>,
    pub largeur_min_mm: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Conjointe {
    pub essais: Option<u64>,
    pub essais_par_piece_mobile: f64,
    pub plafond_s: f64,
    pub t0: f64,
    pub t_fin: f64,
    pub sigma_mm: f64,
    pub poids_proximite: f64,
    pub periode_zone: usize,
    pub max_expansions: usize,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Couts {
    pub conjointe: CoutsConjointe,
    pub convertisseur: CoutsConvertisseur,
    pub decouplage: CoutsDecouplage,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct CoutsConjointe {
    pub longueur: f64,
    pub via: f64,
    pub retreci: f64,
    pub resistance_carre: f64,
    pub segment: f64,
    pub echec: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct CoutsConvertisseur {
    pub boucle_mm2: f64,
    pub perimetre: f64,
    pub noeud: f64,
    pub courts: f64,
    pub decouplage_pilote: f64,
    pub bulk: f64,
    pub sensible: f64,
    pub distance_sensible_mm: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct CoutsDecouplage {
    pub limite_mm: f64,
    pub poids: f64,
}

/// Condition d'une liste `couches` (§2.6).
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Condition {
    PrefereePuissance,
    AdjacentePlan,
    FaceConvertisseur,
    Externe,
    Interne,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct CondCouche {
    pub si: Condition,
    pub facteur: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Roles {
    pub signal: Role,
    pub alimentation: Role,
    pub puissance: Role,
    pub commutation: Role,
    pub plan: RolePlan,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Role {
    pub priorite: u32,
    pub largeur_preferee_mm: Option<f64>,
    pub largeur_preferee_pastille: bool,
    pub paliers_mm: Vec<f64>,
    pub facteur_via: f64,
    pub vias_doubles: bool,
    pub couches: Option<Vec<CondCouche>>,
    pub resistance_conjointe: bool,
    pub sorties_longues: bool,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct RolePlan {
    pub priorite: u32,
    pub descentes_mm: Vec<f64>,
    pub descente_puissance_mm: Vec<f64>,
    pub rayon_vias_doubles_mm: f64,
    pub facteur_via: f64,
    pub vias_doubles: bool,
    pub couches: Option<Vec<CondCouche>>,
    pub resistance_conjointe: bool,
    pub sorties_longues: bool,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Sensibilites {
    pub normale: Sensibilite,
    pub sensible: Sensibilite,
    pub peu_sensible: Sensibilite,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Sensibilite {
    pub couches: Option<Vec<CondCouche>>,
    pub proximite: f64,
    pub priorite_signal: Option<u32>,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct CouchePreferee {
    pub penalite_ailleurs: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct Journal {
    pub niveau: String,
    pub debug: Vec<String>,
    /// `journal.debug` contient « marge » (remplace l'ancienne variable de mise au point des marges)
    #[serde(skip)]
    pub debug_marge: bool,
    /// `journal.debug` contient « amorces »
    #[serde(skip)]
    pub debug_amorces: bool,
    /// `journal.debug` contient « conjointe »
    #[serde(skip)]
    pub debug_conjointe: bool,
}

impl Parametres {
    /// Structure typée d'une valeur déjà validée par le schéma (une erreur ici est une erreur interne).
    pub fn depuis_valeur(v: &Value) -> Parametres {
        let mut p: Parametres = match serde_json::from_value(v.clone()) {
            Ok(p) => p,
            Err(e) => panic!("paramètres validés mais illisibles par la structure typée : {}", e),
        };
        let a = |k: &str| p.journal.debug.iter().any(|d| d == k);
        let (m, am, cj) = (a("marge"), a("amorces"), a("conjointe"));
        p.journal.debug_marge = m;
        p.journal.debug_amorces = am;
        p.journal.debug_conjointe = cj;
        p
    }

    /// Paramètres du niveau `normal`.
    pub fn defaut() -> Parametres {
        Parametres::depuis_valeur(&defauts(Niveau::Normal))
    }
}

// ----------------------------------------------------------------------------------------------------------- tests

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::collections::BTreeMap;

    /// Annexe C du contrat 2.1, ligne `conjointe` remplacée par celle de la partie D de l'avenant 2.2 (copie à la main
    /// du texte normatif : le test compare la sortie du moteur à ce texte, valeur par valeur).
    pub(crate) const ANNEXE_C_AVENANT_D: &str = r#"{
 "format": "parametres_moteur/1",
 "campagne": {"threads": 0, "chaines": 4, "duree_max_s": 7200, "arret_apres": "complet",
              "seuil_abandon_echecs": 10, "seuil_abandon_part": 0.005, "memoire_max_mo": 0},
 "placement": {"degagement_pas_fin_mm": 0.8, "copier_motifs": false},
 "recuit": {"mouvements_max": 250000000, "alpha": 0.996, "palier_par_piece": 100, "acceptation_initiale": 0.85,
            "t_fin_relative": 1e-06, "rudy_capacite": 2.0, "rudy_lambda": 2.0, "rechauffes": 0,
            "arret_paliers_sans_progres": 375, "periode_reroutage": 3000, "pas_grille_globale_mm": 0.5,
            "affinage": {"fraction_mouvements": 0.05, "acceptation": 0.03, "t_fin_relative": 0.01, "alpha": 0.85,
                         "palier_par_piece": 25}},
 "boucle": {"tours": 4, "mouvements_locaux": 60000, "rayon_zone_mm": 3.5, "poids_initial": 30, "rayon_pieces_mm": 4.0,
            "t0_facteur": 0.15, "sigma_mm": 2.5, "reroutage_local": false},
 "routage": {"iters_negociation": 40, "tours_arrachage": 4, "arret_si_aggravation": false, "ordre_pas_fin_d_abord": true,
             "pas_grille_detail_mm": "auto", "largeur_plancher_mm": 0.254, "eventail_auto": true,
             "eventail_pas_sortie_mm": "auto", "eventail_longueur_mm": 0.3, "amorce_eventail_paires": false,
             "amorce_moyenne": false, "ponts_plans": false, "rayon_via_plan_mm": 6.0, "fenetre_mm": 15.0,
             "espacement": {"actif": true, "prox": 1.0, "coude": 0.3, "allongement_max": 0.15},
             "sens_par_couche": {"actif": false, "penalite": 0.2}, "copier_motifs": false,
             "global": {"cout_via": 2.0, "penalite_via": 2.0, "penalite_debordement": 5.0, "penalite_echec": 100.0,
                        "max_expansions": 250000, "rayon_echappement_cellules": 8}},
 "finition": {"reparer_tours": 4, "reparer_si_echecs_max": null, "routage_complet": false, "routage_complet_iters": 80,
              "routage_complet_tours": 8, "verification_reparer_tours": 2, "lissage_marches": false,
              "surfaces_puissance": {"actif": false, "roles": ["puissance", "commutation"], "largeur_min_mm": 1.0}},
 "conjointe": {"essais": null, "essais_par_piece_mobile": 6.0, "plafond_s": 1800, "t0": 3.0, "t_fin": 0.05,
               "sigma_mm": 1.5, "poids_proximite": 1.0, "periode_zone": 40, "max_expansions": 200000},
 "couts": {"conjointe": {"longueur": 1.0, "via": 1.5, "retreci": 2.0, "resistance_carre": 1.5, "segment": 0.1, "echec": 100000},
           "convertisseur": {"boucle_mm2": 8, "perimetre": 10, "noeud": 15, "courts": 10, "decouplage_pilote": 10, "bulk": 3,
                             "sensible": 40, "distance_sensible_mm": 4},
           "decouplage": {"limite_mm": 4.0, "poids": 25.0}},
 "roles": {
  "signal": {"priorite": 8, "largeur_preferee_mm": null, "largeur_preferee_pastille": false, "paliers_mm": [0.3, 0.254],
             "facteur_via": 1, "vias_doubles": false, "couches": null, "resistance_conjointe": false, "sorties_longues": false},
  "alimentation": {"priorite": 7, "largeur_preferee_mm": null, "largeur_preferee_pastille": false, "paliers_mm": [0.3, 0.254],
                   "facteur_via": 3, "vias_doubles": false, "couches": null, "resistance_conjointe": false, "sorties_longues": false},
  "puissance": {"priorite": 1, "largeur_preferee_mm": null, "largeur_preferee_pastille": false, "paliers_mm": [1.5, 1.0, 0.5, 0.3],
                "facteur_via": 3, "vias_doubles": true, "couches": null, "resistance_conjointe": true, "sorties_longues": false},
  "commutation": {"priorite": 3, "largeur_preferee_mm": 2.0, "largeur_preferee_pastille": true,
                  "paliers_mm": [1.5, 1.0, 0.5, 0.3, 0.254], "facteur_via": 1, "vias_doubles": true,
                  "couches": [{"si": "face_convertisseur", "facteur": 1.0}, {"si": "externe", "facteur": 1.2},
                              {"si": "interne", "facteur": 3.0}],
                  "resistance_conjointe": false, "sorties_longues": true},
  "plan": {"priorite": 6, "descentes_mm": [0.3, 0.254], "descente_puissance_mm": [0.5, 0.3], "rayon_vias_doubles_mm": 2.5,
           "facteur_via": 1, "vias_doubles": false, "couches": null, "resistance_conjointe": false, "sorties_longues": false}},
 "sensibilites": {
  "normale": {"couches": null, "proximite": 1.0, "priorite_signal": null},
  "sensible": {"couches": [{"si": "preferee_puissance", "facteur": 3.0}, {"si": "adjacente_plan", "facteur": 1.0},
                           {"si": "externe", "facteur": 1.2}, {"si": "interne", "facteur": 3.0}],
               "proximite": 2.0, "priorite_signal": 4},
  "peu_sensible": {"couches": null, "proximite": 0.3, "priorite_signal": null}},
 "couche_preferee": {"penalite_ailleurs": 1.5},
 "journal": {"niveau": "normal", "debug": []}
}"#;

    /// Feuilles d'une valeur (chemin → valeur ; une liste est une feuille).
    pub(crate) fn feuilles(v: &Value) -> BTreeMap<String, Value> {
        fn parcourir(v: &Value, prefixe: &str, out: &mut BTreeMap<String, Value>) {
            match v {
                Value::Object(o) => {
                    for (k, x) in o {
                        let ch = if prefixe.is_empty() { k.clone() } else { format!("{}.{}", prefixe, k) };
                        parcourir(x, &ch, out);
                    }
                }
                _ => {
                    out.insert(prefixe.to_string(), v.clone());
                }
            }
        }
        let mut out = BTreeMap::new();
        parcourir(v, "", &mut out);
        out
    }

    #[test]
    fn defauts_egaux_a_l_annexe_c_avec_l_avenant() {
        let annexe: Value = serde_json::from_str(ANNEXE_C_AVENANT_D).unwrap();
        let moteur = defauts(Niveau::Normal);
        // égalité exacte des valeurs JSON (mêmes nombres entiers et flottants que l'annexe)
        let (fa, fm) = (feuilles(&annexe), feuilles(&moteur));
        for (k, v) in &fa {
            assert_eq!(fm.get(k), Some(v), "clé {}", k);
        }
        assert_eq!(fa.len(), fm.len(), "mêmes clés");
        assert_eq!(annexe, moteur);
        // le texte ordonné se relit en la même valeur
        let relu: Value = serde_json::from_str(&texte_ordonne(&moteur)).unwrap();
        assert_eq!(relu, annexe);
        assert_eq!(moteur["conjointe"]["essais_par_piece_mobile"], json!(6.0));
    }

    #[test]
    fn niveaux_ne_different_que_par_les_six_cles() {
        let normal = feuilles(&defauts(Niveau::Normal));
        let attendus: [(&str, [Value; 4]); 6] = [
            ("recuit.mouvements_max", [json!(20000000), json!(20000000), json!(250000000), json!(250000000)]),
            ("routage.iters_negociation", [json!(30), json!(30), json!(40), json!(40)]),
            ("boucle.tours", [json!(0), json!(1), json!(4), json!(6)]),
            ("conjointe.essais_par_piece_mobile", [json!(0.0), json!(0.48), json!(6.0), json!(6.37)]),
            ("conjointe.plafond_s", [json!(0), json!(600), json!(1800), json!(5400)]),
            ("campagne.duree_max_s", [json!(900), json!(2700), json!(7200), json!(14400)]),
        ];
        for (i, niveau) in Niveau::TOUS.iter().enumerate() {
            let f = feuilles(&defauts(*niveau));
            assert_eq!(f.len(), normal.len());
            for (k, v) in &f {
                if let Some((_, vals)) = attendus.iter().find(|(c, _)| c == k) {
                    assert_eq!(v.as_f64(), vals[i].as_f64(), "{} : {}", niveau.nom(), k);
                } else {
                    assert_eq!(Some(v), normal.get(k), "{} : la clé {} ne doit pas changer", niveau.nom(), k);
                }
            }
            // chaque niveau se relit sans erreur et donne une structure typée
            let lus = fusionner_valeur(&defauts(*niveau), "p.json").unwrap();
            assert_eq!(lus.valeur, defauts(*niveau));
            assert!(lus.avertissements.is_empty());
        }
    }

    #[test]
    fn structure_typee_du_niveau_normal() {
        let p = Parametres::defaut();
        assert_eq!(p.campagne.chaines, 4);
        assert_eq!(p.campagne.arret_apres, ArretApres::Complet);
        assert_eq!(p.recuit.mouvements_max, 250_000_000);
        assert_eq!(p.routage.pas_grille_detail_mm, AutoOuNombre::Texte("auto".into()));
        assert_eq!(p.routage.pas_grille_detail_mm.valeur(), None);
        assert_eq!(p.conjointe.essais, None);
        assert_eq!(p.conjointe.essais_par_piece_mobile, 6.0);
        assert_eq!(p.roles.commutation.largeur_preferee_mm, Some(2.0));
        assert_eq!(p.roles.commutation.couches.as_ref().unwrap()[0].si, Condition::FaceConvertisseur);
        assert_eq!(p.sensibilites.sensible.priorite_signal, Some(4));
        assert!(!p.journal.debug_marge && !p.journal.debug_amorces && !p.journal.debug_conjointe);
    }

    #[test]
    fn fusion_objet_partiel_tableau_remplace_cle_commentaire_ignoree() {
        let texte = r#"{
            "_commentaire": "réglage d'essai",
            "recuit": {"alpha": 0.95, "_note": 3, "affinage": {"alpha": 0.8}},
            "roles": {"signal": {"paliers_mm": [0.5]}},
            "journal": {"debug": ["amorces", "marge"]}
        }"#;
        let lus = fusionner_texte(texte, "p.json").unwrap();
        let v = &lus.valeur;
        // objet partiel : seules les clés données changent, les autres gardent le défaut
        assert_eq!(v["recuit"]["alpha"], json!(0.95));
        assert_eq!(v["recuit"]["palier_par_piece"], json!(100));
        assert_eq!(v["recuit"]["affinage"]["alpha"], json!(0.8));
        assert_eq!(v["recuit"]["affinage"]["palier_par_piece"], json!(25));
        // tableau remplacé en bloc
        assert_eq!(v["roles"]["signal"]["paliers_mm"], json!([0.5]));
        assert_eq!(v["roles"]["alimentation"]["paliers_mm"], json!([0.3, 0.254]));
        // clés _x ignorées à tout niveau
        assert!(v.get("_commentaire").is_none());
        assert!(v["recuit"].get("_note").is_none());
        // aucune autre feuille n'a changé
        let (f, n) = (feuilles(v), feuilles(&defauts(Niveau::Normal)));
        let changees: Vec<&String> = f.keys().filter(|k| f[*k] != n[*k]).collect();
        assert_eq!(changees, ["journal.debug", "recuit.affinage.alpha", "recuit.alpha", "roles.signal.paliers_mm"]);
        assert!(lus.parametres.journal.debug_amorces && lus.parametres.journal.debug_marge);
        assert!(!lus.parametres.journal.debug_conjointe);
        // un entier écrit 4.0 est accepté et normalisé en entier
        let lus = fusionner_texte(r#"{"campagne": {"chaines": 4.0}, "conjointe": {"essais": 25}}"#, "p.json").unwrap();
        assert_eq!(lus.valeur["campagne"]["chaines"], json!(4));
        assert_eq!(lus.parametres.conjointe.essais, Some(25));
    }

    #[test]
    fn cle_inconnue_avec_chemin_et_suggestion() {
        let e = fusionner_texte(r#"{"routage": {"espacment": {"actif": false}}}"#, "parametres_moteur.json").unwrap_err();
        assert_eq!(e, "parametres_moteur.json : routage.espacment : clé inconnue ; vouliez-vous dire espacement ?");
        let e = fusionner_texte(r#"{"chaines": 4}"#, "p.json").unwrap_err();
        assert!(e.contains("chaines : clé inconnue ici ; vouliez-vous dire campagne.chaines ?"), "{}", e);
        let e = fusionner_texte(r#"{"roles": {"signal": {"couches": [{"si": "externe", "facteurr": 1.0}]}}}"#, "p.json").unwrap_err();
        assert!(e.contains("roles.signal.couches[0].facteurr : clé inconnue ; vouliez-vous dire facteur ?"), "{}", e);
        let e = fusionner_texte(r#"{"roles": {"bus": {}}}"#, "p.json").unwrap_err();
        assert!(e.contains("roles.bus : clé inconnue"), "{}", e);
    }

    #[test]
    fn null_seulement_la_ou_l_annexe_le_montre() {
        let e = fusionner_texte(r#"{"routage": {"fenetre_mm": null}}"#, "p.json").unwrap_err();
        assert!(e.contains("routage.fenetre_mm : null interdit ici"), "{}", e);
        let e = fusionner_texte(r#"{"recuit": null}"#, "p.json").unwrap_err();
        assert!(e.contains("recuit : un objet {…} attendu, trouvé null"), "{}", e);
        // null permis : conjointe.essais, finition.reparer_si_echecs_max, largeur préférée et couches des rôles, etc.
        let ok = r#"{"conjointe": {"essais": null}, "finition": {"reparer_si_echecs_max": null},
                     "roles": {"commutation": {"largeur_preferee_mm": null, "couches": null}},
                     "sensibilites": {"sensible": {"priorite_signal": null, "couches": null}}}"#;
        let lus = fusionner_texte(ok, "p.json").unwrap();
        assert_eq!(lus.parametres.roles.commutation.largeur_preferee_mm, None);
        assert!(lus.parametres.roles.commutation.couches.is_none());
    }

    #[test]
    fn nombre_entre_guillemets_refuse() {
        let e = fusionner_texte(r#"{"recuit": {"alpha": "0.99"}}"#, "p.json").unwrap_err();
        assert!(e.contains("recuit.alpha"), "{}", e);
        assert!(e.contains("nombre écrit entre guillemets : écrire 0.99 sans guillemets"), "{}", e);
        let e = fusionner_texte(r#"{"campagne": {"chaines": "4"}}"#, "p.json").unwrap_err();
        assert!(e.contains("campagne.chaines") && e.contains("entre guillemets"), "{}", e);
        let e = fusionner_texte(r#"{"routage": {"eventail_auto": "false"}}"#, "p.json").unwrap_err();
        assert!(e.contains("booléen écrit entre guillemets"), "{}", e);
        let e = fusionner_texte(r#"{"routage": {"pas_grille_detail_mm": "0.05"}}"#, "p.json").unwrap_err();
        assert!(e.contains("routage.pas_grille_detail_mm") && e.contains("entre guillemets"), "{}", e);
        let e = fusionner_texte(r#"{"recuit": {"alpha": "0,99"}}"#, "p.json").unwrap_err();
        assert!(e.contains("virgule décimale"), "{}", e);
    }

    #[test]
    fn bornes_entiers_enumerations_listes() {
        let cas: &[(&str, &str)] = &[
            (r#"{"recuit": {"alpha": 1.5}}"#, "recuit.alpha : 1.5 hors bornes"),
            (r#"{"campagne": {"chaines": 4.5}}"#, "campagne.chaines : nombre entier attendu"),
            (r#"{"campagne": {"chaines": 0}}"#, "campagne.chaines : 0 hors bornes"),
            (r#"{"campagne": {"arret_apres": "recuits"}}"#, "vouliez-vous dire recuit ?"),
            (r#"{"journal": {"debug": ["marge", "marge"]}}"#, "journal.debug[1] : « marge » répété"),
            (r#"{"journal": {"debug": ["tout"]}}"#, "journal.debug[0] : valeur « tout » inconnue"),
            (r#"{"roles": {"plan": {"descentes_mm": []}}}"#, "roles.plan.descentes_mm : au moins 1 élément"),
            (r#"{"roles": {"signal": {"paliers_mm": [0,3, 0,254]}}}"#, "virgule décimale"),
            (r#"{"roles": {"signal": {"couches": [{"si": "dessus", "facteur": 1}]}}}"#, "roles.signal.couches[0].si : valeur « dessus » inconnue"),
            (r#"{"roles": {"signal": {"couches": [{"si": "externe"}]}}}"#, "roles.signal.couches[0] : clé « facteur » manquante"),
            (r#"{"routage": {"pas_grille_detail_mm": "automatique"}}"#, "« auto » ou un nombre"),
            (r#"{"routage": {"global": 3}}"#, "routage.global : un objet {…} attendu, trouvé 3"),
            (r#"{"format": "parametres_moteur/2"}"#, "format parametres_moteur/2 non pris en charge"),
            (r#"[1, 2]"#, "le fichier doit contenir un objet JSON"),
        ];
        for (texte, attendu) in cas {
            let e = fusionner_texte(texte, "p.json").unwrap_err();
            assert!(e.contains(attendu), "texte {} : message {:?} sans {:?}", texte, e, attendu);
            assert!(e.starts_with("p.json : "), "{}", e);
        }
        // plusieurs erreurs : toutes signalées, une par ligne
        let e = fusionner_texte(r#"{"recuit": {"alpha": 2, "alfa": 1}, "boucle": {"tours": -1}}"#, "p.json").unwrap_err();
        assert_eq!(e.lines().count(), 3, "{}", e);
    }

    #[test]
    fn cles_reservees_acceptees_avec_avertissement() {
        let lus = fusionner_texte(r#"{"routage": {"copier_motifs": true, "sens_par_couche": {"actif": true}}}"#, "p.json").unwrap();
        assert_eq!(lus.avertissements.len(), 2, "{:?}", lus.avertissements);
        assert!(lus.avertissements.iter().any(|a| a.contains("routage.copier_motifs est réservé")));
        let lus = fusionner_texte(r#"{"routage": {"copier_motifs": false}}"#, "p.json").unwrap();
        assert!(lus.avertissements.is_empty());
        // type contrôlé même pour une clé réservée
        assert!(fusionner_texte(r#"{"routage": {"copier_motifs": 1}}"#, "p.json").is_err());
    }

    #[test]
    fn distance_et_suggestion() {
        assert_eq!(distance_edition("espacment", "espacement"), 1);
        assert_eq!(distance_edition("Alpha", "alpha"), 0);
        assert_eq!(plus_proche("fixe", ["fixes", "mobiles"]), Some(("fixes", 1)));
    }
}
