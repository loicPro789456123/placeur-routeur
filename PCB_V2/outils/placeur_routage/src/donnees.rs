//! Lecture stricte de `donnees_placeur/2` (contrat §2.1, §2.2, §2.5) et des placements (forme v1 en liste nue ou
//! `placements/2`, §2.7).
//!
//! Le lecteur travaille sur la valeur JSON déjà analysée (`fichiers::analyser_json`, décision D3 du lot A) : tous les
//! champs de §2.5 sont obligatoires ; un champ manquant, mal typé, une valeur hors de son énumération ou une
//! référence croisée invalide rend `Err` avec le chemin du champ (« donnees.composants[12].pastilles[3].forme :
//! champ obligatoire manquant ») ; les champs inconnus et les clés `_*` sont ignorés. Le texte d'erreur ne contient
//! jamais de chemin de fichier : l'appelant le préfixe du nom du fichier seul.
//!
//! Choix de lecture (§8.0-2 c, notés au journal du lot A) :
//! - `largeur` d'une zone interdite n'est exigée que pour une polyligne (§2.2 : « largeur (polyligne) ») ;
//! - `trous[].trou.metallise` n'est pas exigé (le drapeau est `trous[].metallise`, exemple de §2.5) ; il l'est pour le
//!   trou d'une pastille (§2.2) ;
//! - une pièce fixe peut porter des orientations (ignorées) ; une pièce mobile doit en avoir au moins une ;
//! - aucun alias de `pose_cible` n'est lu (décision D2) : `pose_cible` est obligatoire.

use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};

/// 1 mil en mm (§0.4).
pub const MIL_MM: f64 = 0.0254;

type R<T> = Result<T, String>;

// =================================================================================================================
// Curseur de lecture : valeur et chemin du champ
// =================================================================================================================

fn decrire(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "un booléen",
        Value::Number(_) => "un nombre",
        Value::String(_) => "un texte",
        Value::Array(_) => "une liste",
        Value::Object(_) => "un objet",
    }
}

/// Texte court et sans retour à la ligne, pour citer une valeur de la carte (nom de pièce, de réseau) dans un message.
pub fn court(s: &str) -> String {
    let mut t: String = s.chars().filter(|c| !c.is_control()).take(60).collect();
    if s.chars().count() > 60 {
        t.push('…');
    }
    t
}

#[derive(Clone)]
struct C<'a> {
    v: &'a Value,
    ch: String,
}

impl<'a> C<'a> {
    fn err<T>(&self, msg: impl std::fmt::Display) -> R<T> {
        Err(format!("{} : {}", self.ch, msg))
    }

    fn attendu<T>(&self, quoi: &str) -> R<T> {
        self.err(format!("{} attendu, trouvé {}", quoi, decrire(self.v)))
    }

    fn obj(&self) -> R<&'a Map<String, Value>> {
        match self.v {
            Value::Object(o) => Ok(o),
            _ => self.attendu("objet"),
        }
    }

    /// Champ obligatoire.
    fn f(&self, nom: &str) -> R<C<'a>> {
        match self.obj()?.get(nom) {
            Some(v) => Ok(C { v, ch: format!("{}.{}", self.ch, nom) }),
            None => Err(format!("{}.{} : champ obligatoire manquant", self.ch, nom)),
        }
    }

    fn a(&self, nom: &str) -> bool {
        matches!(self.v, Value::Object(o) if o.contains_key(nom))
    }

    fn liste(&self) -> R<Vec<C<'a>>> {
        match self.v {
            Value::Array(l) => Ok(l.iter().enumerate().map(|(i, v)| C { v, ch: format!("{}[{}]", self.ch, i) }).collect()),
            _ => self.attendu("liste"),
        }
    }

    fn null(&self) -> bool {
        self.v.is_null()
    }

    fn txt(&self) -> R<String> {
        match self.v {
            Value::String(s) => Ok(s.clone()),
            _ => self.attendu("texte"),
        }
    }

    fn txt_ou_null(&self) -> R<Option<String>> {
        if self.null() {
            Ok(None)
        } else {
            self.txt().map(Some)
        }
    }

    fn nb(&self) -> R<f64> {
        match self.v.as_f64() {
            Some(x) if x.is_finite() => Ok(x),
            _ => self.attendu("nombre"),
        }
    }

    fn nb_ou_null(&self) -> R<Option<f64>> {
        if self.null() {
            Ok(None)
        } else {
            self.nb().map(Some)
        }
    }

    /// Nombre positif ou nul.
    fn pos(&self) -> R<f64> {
        let x = self.nb()?;
        if x < 0.0 {
            return self.err("nombre positif ou nul attendu");
        }
        Ok(x)
    }

    /// Nombre strictement positif.
    fn spos(&self) -> R<f64> {
        let x = self.nb()?;
        if x <= 0.0 {
            return self.err("nombre strictement positif attendu");
        }
        Ok(x)
    }

    fn ent(&self) -> R<i64> {
        if let Some(i) = self.v.as_i64() {
            return Ok(i);
        }
        match self.v.as_f64() {
            Some(x) if x.is_finite() && x.fract() == 0.0 && x.abs() < 9.0e15 => Ok(x as i64),
            _ => self.attendu("nombre entier"),
        }
    }

    fn ent_ou_null(&self) -> R<Option<i64>> {
        if self.null() {
            Ok(None)
        } else {
            self.ent().map(Some)
        }
    }

    fn naturel(&self) -> R<usize> {
        let i = self.ent()?;
        if i < 0 {
            return self.err("nombre entier positif ou nul attendu");
        }
        Ok(i as usize)
    }

    fn booleen(&self) -> R<bool> {
        match self.v {
            Value::Bool(b) => Ok(*b),
            _ => self.attendu("booléen"),
        }
    }

    fn parmi(&self, permis: &[&str]) -> R<String> {
        let s = self.txt()?;
        if permis.contains(&s.as_str()) {
            Ok(s)
        } else {
            self.err(format!("« {} » au lieu de {}", court(&s), permis.join(", ")))
        }
    }

    fn textes(&self) -> R<Vec<String>> {
        self.liste()?.iter().map(|c| c.txt()).collect()
    }

    fn nombres(&self, n: usize, quoi: &str) -> R<Vec<f64>> {
        let l = match self.v {
            Value::Array(l) => l,
            _ => return self.attendu(quoi),
        };
        if l.len() != n {
            return self.err(format!("{} attendu ({} nombres, trouvé {} ; virgule décimale ?)", quoi, n, l.len()));
        }
        self.liste()?.iter().map(|c| c.nb()).collect()
    }

    fn point(&self) -> R<[f64; 2]> {
        let v = self.nombres(2, "point [x, y]")?;
        Ok([v[0], v[1]])
    }

    fn rect(&self) -> R<[f64; 4]> {
        let v = self.nombres(4, "rectangle [x0, y0, x1, y1]")?;
        Ok([v[0], v[1], v[2], v[3]])
    }

    fn points(&self) -> R<Vec<[f64; 2]>> {
        self.liste()?.iter().map(|c| c.point()).collect()
    }

    /// Polygone : au moins 3 points.
    fn polygone(&self) -> R<Vec<[f64; 2]>> {
        let p = self.points()?;
        if p.len() < 3 {
            return self.err(format!("polygone d'au moins 3 points attendu, trouvé {}", p.len()));
        }
        Ok(p)
    }

    fn polygones(&self) -> R<Vec<Vec<[f64; 2]>>> {
        self.liste()?.iter().map(|c| c.polygone()).collect()
    }

    /// `[x, y, rot, dessous]`.
    fn pose(&self) -> R<Pose> {
        let l = match self.v {
            Value::Array(l) if l.len() == 4 => self.liste()?,
            Value::Array(l) => {
                return self.err(format!("pose [x, y, rotation, dessous] attendue (4 valeurs, trouvé {})", l.len()))
            }
            _ => return self.attendu("pose [x, y, rotation, dessous]"),
        };
        Ok(Pose { x: l[0].nb()?, y: l[1].nb()?, rot: l[2].nb()?, dessous: l[3].booleen()? })
    }
}

// =================================================================================================================
// Modèle lu
// =================================================================================================================

/// Pose d'une pièce (mm, degrés ; `dessous` = face opposée).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pose {
    pub x: f64,
    pub y: f64,
    pub rot: f64,
    pub dessous: bool,
}

/// Point local d'une pièce vers le repère de la carte (convention de `geom::fwd` : miroir `y → −y` pour la face
/// dessous, puis rotation ; valeurs exactes aux multiples de 90°).
pub fn vers_monde(p: &Pose, lx: f64, ly: f64) -> [f64; 2] {
    let (vx, vy) = if p.dessous { (lx, -ly) } else { (lx, ly) };
    let r = p.rot.rem_euclid(360.0);
    let (dx, dy) = if r == 0.0 {
        (vx, vy)
    } else if r == 90.0 {
        (-vy, vx)
    } else if r == 180.0 {
        (-vx, -vy)
    } else if r == 270.0 {
        (vy, -vx)
    } else {
        let (s, c) = r.to_radians().sin_cos();
        (vx * c - vy * s, vx * s + vy * c)
    };
    [p.x + dx, p.y + dy]
}

#[derive(Clone, Debug)]
pub struct Source {
    pub entree: String,
    pub sha256_entree: String,
    pub contraintes: Option<String>,
    pub config: String,
    pub controle_gabarit: Option<String>,
    pub construit_par: String,
}

#[derive(Clone, Debug)]
pub struct Decoupe {
    pub id: String,
    pub points: Vec<[f64; 2]>,
    pub proprietaire: Option<String>,
}

/// Surface de cuivre (remplissage de la carte ou zone de plan) : contour et trous.
#[derive(Clone, Debug)]
pub struct Surface {
    pub couche: String,
    pub reseau: String,
    pub contour: Vec<[f64; 2]>,
    pub trous: Vec<Vec<[f64; 2]>>,
}

#[derive(Clone, Debug)]
pub struct Carte {
    pub contour: Vec<[f64; 2]>,
    pub boite: [f64; 4],
    pub decoupes: Vec<Decoupe>,
    pub remplissages: Vec<Surface>,
    pub marge_bord_mm: f64,
    pub marge_decoupe_mm: f64,
    pub garde_bord_mm: f64,
    pub garde_decoupe_mm: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RoleCouche {
    Routage,
    Plan,
    Inutilisee,
}

#[derive(Clone, Debug)]
pub struct Couche {
    pub nom: String,
    pub rang: usize,
    pub role: RoleCouche,
    /// zones du plan (le champ `couche` de chaque surface est le nom de cette couche)
    pub zones_plan: Vec<Surface>,
    pub epaisseur_cuivre_mm: f64,
    pub direction_preferee: Option<String>,
}

/// Perçage d'une pastille ou d'un trou (§2.2).
#[derive(Clone, Debug)]
pub struct Percage {
    pub diametre: f64,
    pub forme: String,
    pub longueur: f64,
    pub rotation: f64,
    pub decalage: [f64; 2],
    /// `None` pour `trous[].trou` (drapeau porté par `trous[].metallise`)
    pub metallise: Option<bool>,
}

#[derive(Clone, Debug)]
pub struct Trou {
    pub id: String,
    pub x: f64,
    pub y: f64,
    pub rotation: f64,
    pub trou: Percage,
    pub metallise: bool,
    pub diametre_cuivre: f64,
    pub rayon_interdit_mm: f64,
    pub classe_isolement: usize,
    pub piece: Option<String>,
}

#[derive(Clone, Debug)]
pub enum Forme {
    Rect([f64; 4]),
    Polygone(Vec<[f64; 2]>),
}

#[derive(Clone, Debug)]
pub struct ObstaclePlacement {
    pub faces: Vec<String>,
    pub forme: Forme,
    pub etiquette: String,
}

#[derive(Clone, Debug)]
pub struct ZoneImposee {
    pub pieces: Vec<String>,
    pub faces: Vec<String>,
    pub polygone: Vec<[f64; 2]>,
    pub etiquette: String,
    pub source: String,
}

/// Zone interdite de la carte (`zones_routage_interdites`) ou d'une empreinte (`composants[].zones_interdites`,
/// repère local).
#[derive(Clone, Debug)]
pub struct ZoneInterdite {
    pub id: String,
    /// noms de couches, ou `["toutes"]`
    pub couches: Vec<String>,
    pub polyligne: bool,
    pub points: Vec<[f64; 2]>,
    pub largeur: f64,
    pub effets: Vec<String>,
}

#[derive(Clone, Debug)]
pub enum GeomFixe {
    Piste { x1: f64, y1: f64, x2: f64, y2: f64, largeur: f64 },
    Via { x: f64, y: f64, diametre: f64, trou: f64 },
    Region { points: Vec<[f64; 2]>, trous: Vec<Vec<[f64; 2]>> },
}

#[derive(Clone, Debug)]
pub struct CuivreFixe {
    pub id: String,
    pub reseau: String,
    pub couches: Vec<String>,
    pub geom: GeomFixe,
    pub classe_isolement: usize,
}

/// Forme de cuivre d'une pile de pastille (`pile.opposee`, `pile.interne`).
#[derive(Clone, Debug)]
pub struct FormeCuivre {
    pub forme: String,
    pub largeur: f64,
    pub hauteur: f64,
    pub rotation: f64,
    pub rayon_coin: f64,
    pub points: Vec<[f64; 2]>,
    pub decalage: [f64; 2],
}

#[derive(Clone, Debug)]
pub struct Pastille {
    pub id: String,
    pub nom: String,
    pub reseau: String,
    pub lx: f64,
    pub ly: f64,
    pub forme: String,
    pub largeur: f64,
    pub hauteur: f64,
    pub rotation: f64,
    pub rayon_coin: f64,
    pub points: Vec<[f64; 2]>,
    pub couches: Vec<String>,
    pub face: String,
    pub trou: Option<Percage>,
    pub traversante: bool,
    pub expansion_masque: f64,
    pub pile: Option<(FormeCuivre, FormeCuivre)>,
    pub via_dans_pastille_permis: bool,
    pub classe_isolement: usize,
    pub hw: f64,
    pub hh: f64,
    pub th: bool,
}

#[derive(Clone, Debug)]
pub struct RegionEmpreinte {
    pub id: String,
    pub couches: Vec<String>,
    pub face: String,
    pub pastille: Option<String>,
    pub reseau: String,
    pub liaison: Option<Vec<String>>,
    pub points: Vec<[f64; 2]>,
    pub classe_isolement: usize,
}

#[derive(Clone, Debug)]
pub struct ViaEmpreinte {
    pub id: String,
    pub x: f64,
    pub y: f64,
    pub diametre: f64,
    pub trou: f64,
    pub reseau: String,
    pub classe_isolement: usize,
}

#[derive(Clone, Debug)]
pub struct Composant {
    pub nom: String,
    pub empreinte: String,
    pub nature: String,
    pub genre: String,
    pub valeur: String,
    pub hauteur: f64,
    pub mobile: bool,
    pub raison_statut: String,
    pub virtuel: bool,
    pub lbb: [f64; 4],
    pub pastilles: Vec<Pastille>,
    pub regions: Vec<RegionEmpreinte>,
    pub vias: Vec<ViaEmpreinte>,
    pub zones_interdites: Vec<ZoneInterdite>,
    pub decoupes: Vec<String>,
    pub pose_actuelle: Pose,
    pub pose_cible: Pose,
    pub orientations: Vec<(f64, bool)>,
    pub regle: String,
    pub pas_fin: bool,
    pub matrice: bool,
    pub pas_min_mm: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    Signal,
    Alimentation,
    Puissance,
    Commutation,
    Plan,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Sensibilite {
    Normale,
    Sensible,
    PeuSensible,
}

/// Règle de via (`RoutingVias`, §3.7), mm.
#[derive(Clone, Debug, PartialEq)]
pub struct ReglesVia {
    pub diametre: f64,
    pub trou: f64,
    pub diametre_min: f64,
    pub diametre_max: Option<f64>,
    pub trou_min: f64,
    pub trou_max: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ClassesObjets {
    pub piste: usize,
    pub via: usize,
    pub surface: usize,
}

#[derive(Clone, Debug)]
pub struct Reseau {
    pub nom: String,
    pub classe: String,
    pub role: Role,
    pub masse: bool,
    pub sensibilite: Sensibilite,
    /// [min, préférée] ; `largeur_max` : null = aucun maximum
    pub largeur_min: f64,
    pub largeur_pref: f64,
    pub largeur_max: Option<f64>,
    pub descente_large: bool,
    pub isolement_mm: f64,
    pub classes_isolement: ClassesObjets,
    pub via: ReglesVia,
    pub poids: f64,
    pub priorite: Option<i64>,
    pub priorite_routage: i64,
    pub couche_preferee: Option<String>,
    pub couches_permises: Vec<String>,
    pub plan_couches: Vec<String>,
    pub remplissages: Vec<String>,
    pub alimentation_schema: bool,
    pub paire: Option<String>,
}

#[derive(Clone, Debug)]
pub struct Isolement {
    pub defaut: f64,
    pub classes: Vec<String>,
    pub matrice: Vec<Vec<f64>>,
    pub classe_reference: usize,
}

impl Isolement {
    /// Isolement requis entre deux classes (§3.7).
    pub fn requis(&self, a: usize, b: usize) -> f64 {
        self.matrice[a][b]
    }
}

#[derive(Clone, Debug)]
pub struct Regles {
    pub isolement: Isolement,
    pub via: ReglesVia,
    pub trou_trou_mm: f64,
    pub garde_trou_mm: f64,
    pub grille_placement_mm: f64,
    pub marge_piece_mm: f64,
    pub marge_traversante_mm: f64,
    pub via_dans_pastille_meme_reseau: bool,
    pub plans_isolement_mm: f64,
    /// `relief` ou `direct`
    pub plans_connexion: String,
}

#[derive(Clone, Debug)]
pub struct BoucleChaude {
    pub condensateurs: Vec<String>,
    pub reseau_rail: String,
    pub reseau_masse: String,
}

#[derive(Clone, Debug)]
pub struct Convertisseur {
    pub nom: String,
    pub topologie: String,
    pub commutation: String,
    pub haut: String,
    pub bas: String,
    pub pilote: String,
    pub inductance: String,
    pub cond_entree: Vec<String>,
    pub cond_sortie: Vec<String>,
    pub cond_bulk: Vec<String>,
    pub cond_pilote: Vec<String>,
    pub cond_bootstrap: Vec<String>,
    pub reseau_entree: String,
    pub reseau_sortie: String,
    pub reseau_masse: String,
    pub reseau_commutation: String,
    pub reseaux_courts: Vec<String>,
    pub reseaux_sensibles: Vec<String>,
    pub boucle_chaude: BoucleChaude,
    pub origine: String,
}

#[derive(Clone, Debug)]
pub struct Decouplage {
    pub comp: String,
    pub pastille: String,
    pub reseau: String,
    pub broches_ci: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Eloignement {
    pub dure: bool,
    pub objets: Vec<String>,
    pub reference: Vec<String>,
    pub distance_mm: f64,
    pub poids_par_mm: f64,
    pub marge_mm: f64,
}

#[derive(Clone, Debug)]
pub struct Charge {
    pub broche: String,
    pub pastilles: Vec<String>,
    pub courant_a: f64,
}

#[derive(Clone, Debug)]
pub struct Exemplaire {
    pub nom: String,
    pub pieces: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Groupe {
    /// `fonctionnel`, `impose` ou `repete`
    pub genre: String,
    pub nom: String,
    pub rigide: bool,
    /// pièces du groupe (`fonctionnel`, `impose`) ; vide pour `repete`
    pub pieces: Vec<String>,
    pub famille: String,
    pub source: String,
    pub exemplaires: Vec<Exemplaire>,
    pub correspondance_pieces: Vec<Vec<String>>,
    pub correspondance_reseaux: Vec<Vec<String>>,
}

#[derive(Clone, Debug)]
pub struct Paire {
    pub nom: String,
    pub positif: String,
    pub negatif: String,
    pub ecart_mm: Option<f64>,
    pub largeur_mm: Option<f64>,
    pub tolerance_longueur_mm: f64,
}

#[derive(Clone, Debug)]
pub struct AuBord {
    pub piece: String,
    pub cote: String,
    pub debord_mm: f64,
}

/// Données du moteur (`donnees_placeur/2`), lues et contrôlées.
#[derive(Clone, Debug)]
pub struct Donnees {
    pub source: Source,
    pub carte: Carte,
    pub couches: Vec<Couche>,
    pub trous: Vec<Trou>,
    pub obstacles_placement: Vec<ObstaclePlacement>,
    pub zones_imposees: Vec<ZoneImposee>,
    pub zones_routage_interdites: Vec<ZoneInterdite>,
    pub cuivre_fixe: Vec<CuivreFixe>,
    pub composants: Vec<Composant>,
    pub reseaux: Vec<Reseau>,
    pub regles: Regles,
    pub convertisseurs: Vec<Convertisseur>,
    pub sans_piste_dessous: Vec<String>,
    pub decouplages: Vec<Decouplage>,
    pub eloignements: Vec<Eloignement>,
    pub charges: Vec<Charge>,
    pub groupes: Vec<Groupe>,
    pub paires: Vec<Paire>,
    pub au_bord: Vec<AuBord>,
    pub masses: Vec<String>,
    pub parametres_deduction: Value,
    /// nom de pièce → indice dans `composants`
    pub index_composants: BTreeMap<String, usize>,
    /// nom de réseau → indice dans `reseaux`
    pub index_reseaux: BTreeMap<String, usize>,
    /// nom de couche → indice dans `couches` (= rang)
    pub index_couches: BTreeMap<String, usize>,
    /// identifiant de pastille → (pièce, rang de la pastille)
    pub index_pastilles: BTreeMap<String, (usize, usize)>,
}

impl Donnees {
    /// Couches de routage, de haut en bas.
    pub fn couches_routage(&self) -> Vec<&Couche> {
        self.couches.iter().filter(|c| c.role == RoleCouche::Routage).collect()
    }
}

// =================================================================================================================
// Lecture des éléments
// =================================================================================================================

const FACES_PLACEMENT: &[&str] = &["dessus", "dessous"];
const FACES_PASTILLE: &[&str] = &["composant", "opposee", "toutes"];
const FORMES_PASTILLE: &[&str] = &["rect", "rond", "oblong", "rect_arrondi", "polygone"];
const FORMES_TROU: &[&str] = &["rond", "carre", "oblong"];
const EFFETS: &[&str] = &["pistes", "vias", "remplissages", "pastilles", "placement"];
const NATURES: &[&str] = &[
    "condensateur",
    "resistance",
    "inductance",
    "ferrite",
    "transformateur",
    "transistor",
    "diode",
    "circuit",
    "connecteur",
    "quartz",
    "interface",
    "mecanique",
    "graphique",
    "liaison",
    "autre",
];
const GENRES: &[&str] = &["standard", "mecanique", "graphique", "liaison_reseaux", "cavalier"];
const TOPOLOGIES: &[&str] = &["abaisseur", "elevateur", "abaisseur_elevateur", "inverseur", "autre"];
const COMMUTATIONS: &[&str] = &["synchrone", "asynchrone", "integre"];
const ORIGINES: &[&str] = &["deduit", "impose", "modifie"];
const COTES: &[&str] = &["gauche", "droite", "haut", "bas", "quelconque"];

fn lire_source(c: &C) -> R<Source> {
    Ok(Source {
        entree: c.f("entree")?.txt()?,
        sha256_entree: c.f("sha256_entree")?.txt()?,
        contraintes: c.f("contraintes")?.txt_ou_null()?,
        config: c.f("config")?.txt()?,
        controle_gabarit: c.f("controle_gabarit")?.txt_ou_null()?,
        construit_par: c.f("construit_par")?.txt()?,
    })
}

fn lire_surface(c: &C, couche: String) -> R<Surface> {
    Ok(Surface { couche, reseau: c.f("reseau")?.txt()?, contour: c.f("contour")?.polygone()?, trous: c.f("trous")?.polygones()? })
}

fn lire_carte(c: &C) -> R<Carte> {
    let mut decoupes = Vec::new();
    for e in c.f("decoupes")?.liste()? {
        decoupes.push(Decoupe {
            id: e.f("id")?.txt()?,
            points: e.f("points")?.polygone()?,
            proprietaire: e.f("proprietaire")?.txt_ou_null()?,
        });
    }
    let mut remplissages = Vec::new();
    for e in c.f("remplissages")?.liste()? {
        let couche = e.f("couche")?.txt()?;
        remplissages.push(lire_surface(&e, couche)?);
    }
    let boite = c.f("boite")?;
    let b = boite.rect()?;
    if b[2] <= b[0] || b[3] <= b[1] {
        return boite.err("boîte vide ([x0, y0, x1, y1] avec x1 > x0 et y1 > y0 attendu)");
    }
    Ok(Carte {
        contour: c.f("contour")?.polygone()?,
        boite: b,
        decoupes,
        remplissages,
        marge_bord_mm: c.f("marge_bord_mm")?.pos()?,
        marge_decoupe_mm: c.f("marge_decoupe_mm")?.pos()?,
        garde_bord_mm: c.f("garde_bord_mm")?.pos()?,
        garde_decoupe_mm: c.f("garde_decoupe_mm")?.pos()?,
    })
}

fn lire_couches(c: &C) -> R<Vec<Couche>> {
    let mut couches = Vec::new();
    for (i, e) in c.liste()?.into_iter().enumerate() {
        let nom = e.f("nom")?.txt()?;
        let rang_c = e.f("rang")?;
        let rang = rang_c.naturel()?;
        if rang != i {
            return rang_c.err(format!("rang {} au lieu de {} (couches de haut en bas, rangs 0 à N−1)", rang, i));
        }
        let role_c = e.f("role")?;
        let role = match role_c.parmi(&["routage", "plan", "inutilisee"])?.as_str() {
            "routage" => RoleCouche::Routage,
            "plan" => RoleCouche::Plan,
            _ => RoleCouche::Inutilisee,
        };
        let zc = e.f("zones_plan")?;
        let mut zones_plan = Vec::new();
        for z in zc.liste()? {
            zones_plan.push(lire_surface(&z, nom.clone())?);
        }
        if (role == RoleCouche::Plan) != !zones_plan.is_empty() {
            return zc.err("non vide si et seulement si le rôle de la couche est « plan »");
        }
        let dir = e.f("direction_preferee")?;
        let direction_preferee = if dir.null() { None } else { Some(dir.parmi(&["H", "V"])?) };
        couches.push(Couche { nom, rang, role, zones_plan, epaisseur_cuivre_mm: e.f("epaisseur_cuivre_mm")?.pos()?, direction_preferee });
    }
    if couches.is_empty() {
        return c.err("au moins une couche de cuivre attendue");
    }
    if !couches.iter().any(|k| k.role == RoleCouche::Routage) {
        return c.err("aucune couche de rôle « routage »");
    }
    Ok(couches)
}

fn lire_percage(c: &C, metallise_exige: bool) -> R<Percage> {
    Ok(Percage {
        diametre: c.f("diametre")?.spos()?,
        forme: c.f("forme")?.parmi(FORMES_TROU)?,
        longueur: c.f("longueur")?.pos()?,
        rotation: c.f("rotation")?.nb()?,
        decalage: c.f("decalage")?.point()?,
        metallise: if metallise_exige || c.a("metallise") { Some(c.f("metallise")?.booleen()?) } else { None },
    })
}

/// Zone interdite ; `info` : champ d'information obligatoire (`etiquette` pour la carte, `source` pour une empreinte).
fn lire_zone_interdite(c: &C, info: &str) -> R<ZoneInterdite> {
    c.f(info)?.txt()?;
    let polyligne = c.f("forme")?.parmi(&["polygone", "polyligne"])? == "polyligne";
    let pc = c.f("points")?;
    let points = if polyligne { pc.points()? } else { pc.polygone()? };
    if polyligne && points.len() < 2 {
        return pc.err("polyligne d'au moins 2 points attendue");
    }
    let largeur = if polyligne || c.a("largeur") { c.f("largeur")?.pos()? } else { 0.0 };
    let ec = c.f("effets")?;
    let effets = ec.textes()?;
    for (k, e) in effets.iter().enumerate() {
        if !EFFETS.contains(&e.as_str()) {
            return Err(format!("{}[{}] : effet « {} » inconnu ({})", ec.ch, k, court(e), EFFETS.join(", ")));
        }
    }
    Ok(ZoneInterdite { id: c.f("id")?.txt()?, couches: c.f("couches")?.textes()?, polyligne, points, largeur, effets })
}

fn lire_forme_cuivre(c: &C) -> R<FormeCuivre> {
    Ok(FormeCuivre {
        forme: c.f("forme")?.parmi(FORMES_PASTILLE)?,
        largeur: c.f("largeur")?.pos()?,
        hauteur: c.f("hauteur")?.pos()?,
        rotation: c.f("rotation")?.nb()?,
        rayon_coin: c.f("rayon_coin")?.pos()?,
        points: c.f("points")?.points()?,
        decalage: c.f("decalage")?.point()?,
    })
}

fn lire_pastille(c: &C) -> R<Pastille> {
    let trou = c.f("trou")?;
    let pile = c.f("pile")?;
    let forme = c.f("forme")?.parmi(FORMES_PASTILLE)?;
    let pts = c.f("points")?;
    let points = if forme == "polygone" { pts.polygone()? } else { pts.points()? };
    Ok(Pastille {
        id: c.f("id")?.txt()?,
        nom: c.f("nom")?.txt()?,
        reseau: c.f("reseau")?.txt()?,
        lx: c.f("lx")?.nb()?,
        ly: c.f("ly")?.nb()?,
        forme,
        largeur: c.f("largeur")?.spos()?,
        hauteur: c.f("hauteur")?.spos()?,
        rotation: c.f("rotation")?.nb()?,
        rayon_coin: c.f("rayon_coin")?.pos()?,
        points,
        couches: c.f("couches")?.textes()?,
        face: c.f("face")?.parmi(FACES_PASTILLE)?,
        trou: if trou.null() { None } else { Some(lire_percage(&trou, true)?) },
        traversante: c.f("traversante")?.booleen()?,
        expansion_masque: c.f("expansion_masque")?.nb()?,
        pile: if pile.null() { None } else { Some((lire_forme_cuivre(&pile.f("opposee")?)?, lire_forme_cuivre(&pile.f("interne")?)?)) },
        via_dans_pastille_permis: c.f("via_dans_pastille_permis")?.booleen()?,
        classe_isolement: c.f("classe_isolement")?.naturel()?,
        hw: c.f("hw")?.pos()?,
        hh: c.f("hh")?.pos()?,
        th: c.f("th")?.booleen()?,
    })
}

fn lire_composant(c: &C) -> R<Composant> {
    let mut pastilles = Vec::new();
    for e in c.f("pastilles")?.liste()? {
        pastilles.push(lire_pastille(&e)?);
    }
    let mut regions = Vec::new();
    for e in c.f("regions")?.liste()? {
        let li = e.f("liaison")?;
        regions.push(RegionEmpreinte {
            id: e.f("id")?.txt()?,
            couches: e.f("couches")?.textes()?,
            face: e.f("face")?.parmi(FACES_PASTILLE)?,
            pastille: e.f("pastille")?.txt_ou_null()?,
            reseau: e.f("reseau")?.txt()?,
            liaison: if li.null() { None } else { Some(li.textes()?) },
            points: e.f("points")?.polygone()?,
            classe_isolement: e.f("classe_isolement")?.naturel()?,
        });
    }
    let mut vias = Vec::new();
    for e in c.f("vias")?.liste()? {
        vias.push(ViaEmpreinte {
            id: e.f("id")?.txt()?,
            x: e.f("x")?.nb()?,
            y: e.f("y")?.nb()?,
            diametre: e.f("diametre")?.spos()?,
            trou: e.f("trou")?.pos()?,
            reseau: e.f("reseau")?.txt()?,
            classe_isolement: e.f("classe_isolement")?.naturel()?,
        });
    }
    let mut zones_interdites = Vec::new();
    for e in c.f("zones_interdites")?.liste()? {
        zones_interdites.push(lire_zone_interdite(&e, "source")?);
    }
    let mut orientations = Vec::new();
    for e in c.f("orientations")?.liste()? {
        let l = match e.v {
            Value::Array(l) if l.len() == 2 => e.liste()?,
            _ => return e.err("orientation [rotation, dessous] attendue"),
        };
        orientations.push((l[0].nb()?, l[1].booleen()?));
    }
    let mobile = c.f("statut")?.parmi(&["mobile", "fixe"])? == "mobile";
    if mobile && orientations.is_empty() {
        return c.f("orientations")?.err("au moins une orientation attendue pour une pièce mobile");
    }
    let lbb_c = c.f("lbb")?;
    let lbb = lbb_c.rect()?;
    if lbb[2] < lbb[0] || lbb[3] < lbb[1] {
        return lbb_c.err("boîte inversée ([x0, y0, x1, y1] avec x1 ≥ x0 et y1 ≥ y0 attendu)");
    }
    let pm = c.f("pas_min_mm")?;
    let pas_min_mm = if pm.null() { None } else { Some(pm.spos()?) };
    Ok(Composant {
        nom: c.f("nom")?.txt()?,
        empreinte: c.f("empreinte")?.txt()?,
        nature: c.f("nature")?.parmi(NATURES)?,
        genre: c.f("genre")?.parmi(GENRES)?,
        valeur: c.f("valeur")?.txt()?,
        hauteur: c.f("hauteur")?.pos()?,
        mobile,
        raison_statut: c.f("raison_statut")?.txt()?,
        virtuel: c.f("virtuel")?.booleen()?,
        lbb,
        pastilles,
        regions,
        vias,
        zones_interdites,
        decoupes: c.f("decoupes")?.textes()?,
        pose_actuelle: c.f("pose_actuelle")?.pose()?,
        pose_cible: c.f("pose_cible")?.pose()?,
        orientations,
        regle: c.f("regle")?.txt()?,
        pas_fin: c.f("pas_fin")?.booleen()?,
        matrice: c.f("matrice")?.booleen()?,
        pas_min_mm,
    })
}

fn lire_via_regle(c: &C) -> R<ReglesVia> {
    Ok(ReglesVia {
        diametre: c.f("diametre")?.spos()?,
        trou: c.f("trou")?.spos()?,
        diametre_min: c.f("diametre_min")?.pos()?,
        diametre_max: c.f("diametre_max")?.nb_ou_null()?,
        trou_min: c.f("trou_min")?.pos()?,
        trou_max: c.f("trou_max")?.nb_ou_null()?,
    })
}

fn lire_reseau(c: &C) -> R<Reseau> {
    let lc = c.f("largeurs_mm")?;
    let l = match lc.v {
        Value::Array(l) if l.len() == 3 => lc.liste()?,
        Value::Array(l) => return lc.err(format!("[min, préférée, max|null] attendu (3 valeurs, trouvé {} ; virgule décimale ?)", l.len())),
        _ => return lc.attendu("liste [min, préférée, max|null]"),
    };
    let (largeur_min, largeur_pref, largeur_max) = (l[0].spos()?, l[1].spos()?, l[2].nb_ou_null()?);
    if largeur_pref < largeur_min || largeur_max.is_some_and(|m| m < largeur_pref) {
        return lc.err("largeurs non ordonnées (min ≤ préférée ≤ max attendu)");
    }
    let ci = c.f("classes_isolement")?;
    let role = match c.f("role")?.parmi(&["signal", "alimentation", "puissance", "commutation", "plan"])?.as_str() {
        "signal" => Role::Signal,
        "alimentation" => Role::Alimentation,
        "puissance" => Role::Puissance,
        "commutation" => Role::Commutation,
        _ => Role::Plan,
    };
    let sensibilite = match c.f("sensibilite")?.parmi(&["normale", "sensible", "peu_sensible"])?.as_str() {
        "normale" => Sensibilite::Normale,
        "sensible" => Sensibilite::Sensible,
        _ => Sensibilite::PeuSensible,
    };
    Ok(Reseau {
        nom: c.f("nom")?.txt()?,
        classe: c.f("classe")?.txt()?,
        role,
        masse: c.f("masse")?.booleen()?,
        sensibilite,
        largeur_min,
        largeur_pref,
        largeur_max,
        descente_large: c.f("descente_large")?.booleen()?,
        isolement_mm: c.f("isolement_mm")?.pos()?,
        classes_isolement: ClassesObjets {
            piste: ci.f("piste")?.naturel()?,
            via: ci.f("via")?.naturel()?,
            surface: ci.f("surface")?.naturel()?,
        },
        via: lire_via_regle(&c.f("via")?)?,
        poids: c.f("poids")?.pos()?,
        priorite: c.f("priorite")?.ent_ou_null()?,
        priorite_routage: c.f("priorite_routage")?.ent()?,
        couche_preferee: c.f("couche_preferee")?.txt_ou_null()?,
        couches_permises: c.f("couches_permises")?.textes()?,
        plan_couches: c.f("plan_couches")?.textes()?,
        remplissages: c.f("remplissages")?.textes()?,
        alimentation_schema: c.f("alimentation_schema")?.booleen()?,
        paire: c.f("paire")?.txt_ou_null()?,
    })
}

fn lire_regles(c: &C) -> R<Regles> {
    let iso = c.f("isolement")?;
    let classes = iso.f("classes")?.textes()?;
    let mc = iso.f("matrice")?;
    let n = classes.len();
    if n == 0 {
        return iso.f("classes")?.err("au moins une classe d'isolement attendue");
    }
    let mut matrice = Vec::new();
    let lignes = mc.liste()?;
    if lignes.len() != n {
        return mc.err(format!("matrice carrée {} × {} attendue (une ligne par classe), trouvé {} ligne(s)", n, n, lignes.len()));
    }
    for ligne in &lignes {
        let v = ligne.nombres(n, "ligne de la matrice")?;
        if let Some(k) = v.iter().position(|x| *x < 0.0) {
            return Err(format!("{}[{}] : isolement négatif", ligne.ch, k));
        }
        matrice.push(v);
    }
    for i in 0..n {
        for j in 0..i {
            if (matrice[i][j] - matrice[j][i]).abs() > 1e-9 {
                return Err(format!("{}[{}][{}] : matrice non symétrique ({} ≠ {})", mc.ch, i, j, matrice[i][j], matrice[j][i]));
            }
        }
    }
    let rc = iso.f("classe_reference")?;
    let classe_reference = rc.naturel()?;
    if classe_reference >= n {
        return rc.err(format!("classe {} hors de la matrice ({} classe(s))", classe_reference, n));
    }
    let pl = c.f("placement")?;
    let cp = c.f("connexion_plans")?;
    Ok(Regles {
        isolement: Isolement { defaut: iso.f("defaut")?.pos()?, classes, matrice, classe_reference },
        via: lire_via_regle(&c.f("via")?)?,
        trou_trou_mm: c.f("trou_trou_mm")?.pos()?,
        garde_trou_mm: c.f("garde_trou_mm")?.pos()?,
        grille_placement_mm: pl.f("grille_mm")?.pos()?,
        marge_piece_mm: pl.f("marge_piece_mm")?.pos()?,
        marge_traversante_mm: pl.f("marge_traversante_mm")?.pos()?,
        via_dans_pastille_meme_reseau: c.f("via_dans_pastille_meme_reseau")?.booleen()?,
        plans_isolement_mm: cp.f("isolement")?.pos()?,
        plans_connexion: cp.f("connexion")?.parmi(&["relief", "direct"])?,
    })
}

fn lire_convertisseur(c: &C) -> R<Convertisseur> {
    let b = c.f("boucle_chaude")?;
    Ok(Convertisseur {
        nom: c.f("nom")?.txt()?,
        topologie: c.f("topologie")?.parmi(TOPOLOGIES)?,
        commutation: c.f("commutation")?.parmi(COMMUTATIONS)?,
        haut: c.f("haut")?.txt()?,
        bas: c.f("bas")?.txt()?,
        pilote: c.f("pilote")?.txt()?,
        inductance: c.f("inductance")?.txt()?,
        cond_entree: c.f("cond_entree")?.textes()?,
        cond_sortie: c.f("cond_sortie")?.textes()?,
        cond_bulk: c.f("cond_bulk")?.textes()?,
        cond_pilote: c.f("cond_pilote")?.textes()?,
        cond_bootstrap: c.f("cond_bootstrap")?.textes()?,
        reseau_entree: c.f("reseau_entree")?.txt()?,
        reseau_sortie: c.f("reseau_sortie")?.txt()?,
        reseau_masse: c.f("reseau_masse")?.txt()?,
        reseau_commutation: c.f("reseau_commutation")?.txt()?,
        reseaux_courts: c.f("reseaux_courts")?.textes()?,
        reseaux_sensibles: c.f("reseaux_sensibles")?.textes()?,
        boucle_chaude: BoucleChaude {
            condensateurs: b.f("condensateurs")?.textes()?,
            reseau_rail: b.f("reseau_rail")?.txt()?,
            reseau_masse: b.f("reseau_masse")?.txt()?,
        },
        origine: c.f("origine")?.parmi(ORIGINES)?,
    })
}

fn lire_groupe(c: &C) -> R<Groupe> {
    let genre = c.f("type")?.parmi(&["fonctionnel", "impose", "repete"])?;
    let nom = c.f("nom")?.txt()?;
    let rigide = c.f("rigide")?.booleen()?;
    if genre != "repete" {
        return Ok(Groupe {
            genre,
            nom,
            rigide,
            pieces: c.f("pieces")?.textes()?,
            famille: String::new(),
            source: String::new(),
            exemplaires: Vec::new(),
            correspondance_pieces: Vec::new(),
            correspondance_reseaux: Vec::new(),
        });
    }
    let mut exemplaires = Vec::new();
    for e in c.f("exemplaires")?.liste()? {
        exemplaires.push(Exemplaire { nom: e.f("nom")?.txt()?, pieces: e.f("pieces")?.textes()? });
    }
    let listes = |nom: &str| -> R<Vec<Vec<String>>> { c.f(nom)?.liste()?.iter().map(|l| l.textes()).collect() };
    Ok(Groupe {
        genre,
        nom,
        rigide,
        pieces: Vec::new(),
        famille: c.f("famille")?.txt()?,
        source: c.f("source")?.parmi(&["hierarchie", "motif"])?,
        exemplaires,
        correspondance_pieces: listes("correspondance_pieces")?,
        correspondance_reseaux: listes("correspondance_reseaux")?,
    })
}

fn liste_de<'a, T>(c: &C<'a>, nom: &str, lire: impl Fn(&C<'a>) -> R<T>) -> R<Vec<T>> {
    c.f(nom)?.liste()?.iter().map(lire).collect()
}

// =================================================================================================================
// Lecture complète et références croisées (§2.1)
// =================================================================================================================

/// Lit et contrôle `donnees_placeur/2` (valeur JSON déjà analysée).
pub fn lire(v: &Value) -> R<Donnees> {
    let r = C { v, ch: "donnees".to_string() };
    r.obj()?;
    let fc = r.f("format")?;
    let f = fc.txt()?;
    if f != "donnees_placeur/2" {
        let lisible: String = court(&f);
        if f.starts_with("donnees_placeur/") {
            return fc.err(format!("format {} non pris en charge (donnees_placeur/2 attendu)", lisible));
        }
        return fc.err(format!("« {} » au lieu de donnees_placeur/2", lisible));
    }
    let uc = r.f("unite")?;
    if uc.txt()? != "mm" {
        return uc.err("« mm » attendu");
    }
    let d = Donnees {
        source: lire_source(&r.f("source")?)?,
        carte: lire_carte(&r.f("carte")?)?,
        couches: lire_couches(&r.f("couches")?)?,
        trous: liste_de(&r, "trous", |e| {
            Ok(Trou {
                id: e.f("id")?.txt()?,
                x: e.f("x")?.nb()?,
                y: e.f("y")?.nb()?,
                rotation: e.f("rotation")?.nb()?,
                trou: lire_percage(&e.f("trou")?, false)?,
                metallise: e.f("metallise")?.booleen()?,
                diametre_cuivre: e.f("diametre_cuivre")?.pos()?,
                rayon_interdit_mm: e.f("rayon_interdit_mm")?.pos()?,
                classe_isolement: e.f("classe_isolement")?.naturel()?,
                piece: e.f("piece")?.txt_ou_null()?,
            })
        })?,
        obstacles_placement: liste_de(&r, "obstacles_placement", |e| {
            let forme = match (e.a("rect"), e.a("polygone")) {
                (true, false) => Forme::Rect(e.f("rect")?.rect()?),
                (false, true) => Forme::Polygone(e.f("polygone")?.polygone()?),
                _ => return e.err("exactement un des champs « rect » ou « polygone » attendu"),
            };
            let fc = e.f("faces")?;
            let faces = fc.textes()?;
            if let Some(k) = faces.iter().position(|x| !FACES_PLACEMENT.contains(&x.as_str())) {
                return Err(format!("{}[{}] : face « {} » au lieu de dessus, dessous", fc.ch, k, court(&faces[k])));
            }
            Ok(ObstaclePlacement { faces, forme, etiquette: e.f("etiquette")?.txt()? })
        })?,
        zones_imposees: liste_de(&r, "zones_imposees", |e| {
            Ok(ZoneImposee {
                pieces: e.f("pieces")?.textes()?,
                faces: e.f("faces")?.textes()?,
                polygone: e.f("polygone")?.polygone()?,
                etiquette: e.f("etiquette")?.txt()?,
                source: e.f("source")?.parmi(&["contrainte", "salle"])?,
            })
        })?,
        zones_routage_interdites: liste_de(&r, "zones_routage_interdites", |e| lire_zone_interdite(e, "etiquette"))?,
        cuivre_fixe: liste_de(&r, "cuivre_fixe", |e| {
            let geom = match e.f("type")?.parmi(&["piste", "via", "region"])?.as_str() {
                "piste" => GeomFixe::Piste {
                    x1: e.f("x1")?.nb()?,
                    y1: e.f("y1")?.nb()?,
                    x2: e.f("x2")?.nb()?,
                    y2: e.f("y2")?.nb()?,
                    largeur: e.f("largeur")?.spos()?,
                },
                "via" => GeomFixe::Via { x: e.f("x")?.nb()?, y: e.f("y")?.nb()?, diametre: e.f("diametre")?.spos()?, trou: e.f("trou")?.pos()? },
                _ => GeomFixe::Region { points: e.f("points")?.polygone()?, trous: e.f("trous")?.polygones()? },
            };
            Ok(CuivreFixe {
                id: e.f("id")?.txt()?,
                reseau: e.f("reseau")?.txt()?,
                couches: e.f("couches")?.textes()?,
                geom,
                classe_isolement: e.f("classe_isolement")?.naturel()?,
            })
        })?,
        composants: liste_de(&r, "composants", lire_composant)?,
        reseaux: liste_de(&r, "reseaux", lire_reseau)?,
        regles: lire_regles(&r.f("regles")?)?,
        convertisseurs: liste_de(&r, "convertisseurs", lire_convertisseur)?,
        sans_piste_dessous: r.f("sans_piste_dessous")?.textes()?,
        decouplages: liste_de(&r, "decouplages", |e| {
            Ok(Decouplage {
                comp: e.f("comp")?.txt()?,
                pastille: e.f("pastille")?.txt()?,
                reseau: e.f("reseau")?.txt()?,
                broches_ci: e.f("broches_ci")?.textes()?,
            })
        })?,
        eloignements: liste_de(&r, "eloignements", |e| {
            Ok(Eloignement {
                dure: e.f("type")?.parmi(&["dure", "souple"])? == "dure",
                objets: e.f("objets")?.textes()?,
                reference: e.f("reference")?.textes()?,
                distance_mm: e.f("distance_mm")?.pos()?,
                poids_par_mm: e.f("poids_par_mm")?.pos()?,
                marge_mm: e.f("marge_mm")?.pos()?,
            })
        })?,
        charges: liste_de(&r, "charges", |e| {
            Ok(Charge { broche: e.f("broche")?.txt()?, pastilles: e.f("pastilles")?.textes()?, courant_a: e.f("courant_a")?.pos()? })
        })?,
        groupes: liste_de(&r, "groupes", lire_groupe)?,
        paires: liste_de(&r, "paires", |e| {
            Ok(Paire {
                nom: e.f("nom")?.txt()?,
                positif: e.f("positif")?.txt()?,
                negatif: e.f("negatif")?.txt()?,
                ecart_mm: e.f("ecart_mm")?.nb_ou_null()?,
                largeur_mm: e.f("largeur_mm")?.nb_ou_null()?,
                // null admis (formats.py : Nul(NBP)) : tolérance par défaut de validite.rs (1 mm)
                tolerance_longueur_mm: e.f("tolerance_longueur_mm")?.nb_ou_null()?.unwrap_or(1.0).max(0.0),
            })
        })?,
        au_bord: liste_de(&r, "au_bord", |e| {
            Ok(AuBord { piece: e.f("piece")?.txt()?, cote: e.f("cote")?.parmi(COTES)?, debord_mm: e.f("debord_mm")?.nb()? })
        })?,
        masses: r.f("masses")?.textes()?,
        parametres_deduction: {
            let p = r.f("parametres_deduction")?;
            p.obj()?;
            p.v.clone()
        },
        index_composants: BTreeMap::new(),
        index_reseaux: BTreeMap::new(),
        index_couches: BTreeMap::new(),
        index_pastilles: BTreeMap::new(),
    };
    references_croisees(d)
}

/// Index des noms uniques ; doublon ⇒ erreur au chemin `liste[i].champ`.
fn indexer<'a>(noms: impl Iterator<Item = &'a str>, chemin: &str, champ: &str, quoi: &str) -> R<BTreeMap<String, usize>> {
    let mut m = BTreeMap::new();
    for (i, n) in noms.enumerate() {
        if m.insert(n.to_string(), i).is_some() {
            return Err(format!("donnees.{}[{}].{} : {} « {} » en double", chemin, i, champ, quoi, court(n)));
        }
    }
    Ok(m)
}

struct Refs<'a> {
    d: &'a Donnees,
}

impl Refs<'_> {
    fn piece(&self, nom: &str, ch: impl FnOnce() -> String) -> R<()> {
        if self.d.index_composants.contains_key(nom) {
            Ok(())
        } else {
            Err(format!("{} : pièce {} inconnue", ch(), court(nom)))
        }
    }

    fn pieces(&self, l: &[String], ch: &str) -> R<()> {
        l.iter().enumerate().try_for_each(|(k, n)| self.piece(n, || format!("{}[{}]", ch, k)))
    }

    /// Réseau ; `vide_permis` : `""` (aucun réseau) accepté.
    fn reseau(&self, nom: &str, vide_permis: bool, ch: impl FnOnce() -> String) -> R<()> {
        if (vide_permis && nom.is_empty()) || self.d.index_reseaux.contains_key(nom) {
            Ok(())
        } else {
            Err(format!("{} : réseau {} non déclaré dans donnees.reseaux", ch(), court(nom)))
        }
    }

    fn reseaux(&self, l: &[String], ch: &str) -> R<()> {
        l.iter().enumerate().try_for_each(|(k, n)| self.reseau(n, false, || format!("{}[{}]", ch, k)))
    }

    fn couche(&self, nom: &str, ch: impl FnOnce() -> String) -> R<()> {
        if self.d.index_couches.contains_key(nom) {
            Ok(())
        } else {
            Err(format!("{} : couche {} inconnue", ch(), court(nom)))
        }
    }

    /// Couches ; `toutes` : `["toutes"]` accepté.
    fn couches(&self, l: &[String], ch: &str, toutes: bool) -> R<()> {
        if toutes && l.len() == 1 && l[0] == "toutes" {
            return Ok(());
        }
        l.iter().enumerate().try_for_each(|(k, n)| self.couche(n, || format!("{}[{}]", ch, k)))
    }

    fn pastille(&self, id: &str, ch: impl FnOnce() -> String) -> R<()> {
        if self.d.index_pastilles.contains_key(id) {
            Ok(())
        } else {
            Err(format!("{} : pastille {} inconnue", ch(), court(id)))
        }
    }

    fn pastilles(&self, l: &[String], ch: &str) -> R<()> {
        l.iter().enumerate().try_for_each(|(k, n)| self.pastille(n, || format!("{}[{}]", ch, k)))
    }

    /// Objet d'un éloignement : `PIECE`, `PIECE#k` ou `PIECE.BROCHE` (§2.3).
    fn objet(&self, o: &str, ch: String) -> R<()> {
        if self.d.index_composants.contains_key(o) || self.d.index_pastilles.contains_key(o) {
            return Ok(());
        }
        if let Some((p, broche)) = o.rsplit_once('.') {
            if let Some(&i) = self.d.index_composants.get(p) {
                if self.d.composants[i].pastilles.iter().any(|x| x.nom == broche) {
                    return Ok(());
                }
            }
        }
        Err(format!("{} : objet {} inconnu (pièce, pièce#k ou pièce.broche attendu)", ch, court(o)))
    }

    fn classe(&self, k: usize, ch: impl FnOnce() -> String) -> R<()> {
        let n = self.d.regles.isolement.classes.len();
        if k < n {
            Ok(())
        } else {
            Err(format!("{} : classe d'isolement {} hors de la matrice ({} classe(s))", ch(), k, n))
        }
    }
}

fn ensemble(l: &[String]) -> BTreeSet<&str> {
    l.iter().map(|s| s.as_str()).collect()
}

fn references_croisees(mut d: Donnees) -> R<Donnees> {
    d.index_composants = indexer(d.composants.iter().map(|c| c.nom.as_str()), "composants", "nom", "pièce")?;
    d.index_reseaux = indexer(d.reseaux.iter().map(|c| c.nom.as_str()), "reseaux", "nom", "réseau")?;
    d.index_couches = indexer(d.couches.iter().map(|c| c.nom.as_str()), "couches", "nom", "couche")?;
    let mut idx_p = BTreeMap::new();
    for (i, c) in d.composants.iter().enumerate() {
        for (k, p) in c.pastilles.iter().enumerate() {
            if idx_p.insert(p.id.clone(), (i, k)).is_some() {
                return Err(format!("donnees.composants[{}].pastilles[{}].id : pastille « {} » en double", i, k, court(&p.id)));
            }
        }
    }
    d.index_pastilles = idx_p;
    let r = Refs { d: &d };

    // carte
    let ids_decoupes = indexer(d.carte.decoupes.iter().map(|x| x.id.as_str()), "carte.decoupes", "id", "découpe")?;
    for (i, x) in d.carte.decoupes.iter().enumerate() {
        if let Some(p) = &x.proprietaire {
            r.piece(p, || format!("donnees.carte.decoupes[{}].proprietaire", i))?;
        }
    }
    for (i, s) in d.carte.remplissages.iter().enumerate() {
        r.couche(&s.couche, || format!("donnees.carte.remplissages[{}].couche", i))?;
        r.reseau(&s.reseau, false, || format!("donnees.carte.remplissages[{}].reseau", i))?;
    }
    for (i, c) in d.couches.iter().enumerate() {
        for (k, z) in c.zones_plan.iter().enumerate() {
            r.reseau(&z.reseau, false, || format!("donnees.couches[{}].zones_plan[{}].reseau", i, k))?;
        }
    }
    for (i, t) in d.trous.iter().enumerate() {
        r.classe(t.classe_isolement, || format!("donnees.trous[{}].classe_isolement", i))?;
        if let Some(p) = &t.piece {
            r.piece(p, || format!("donnees.trous[{}].piece", i))?;
        }
    }
    for (i, z) in d.zones_imposees.iter().enumerate() {
        r.pieces(&z.pieces, &format!("donnees.zones_imposees[{}].pieces", i))?;
    }
    for (i, z) in d.zones_routage_interdites.iter().enumerate() {
        r.couches(&z.couches, &format!("donnees.zones_routage_interdites[{}].couches", i), true)?;
    }
    for (i, f) in d.cuivre_fixe.iter().enumerate() {
        r.reseau(&f.reseau, true, || format!("donnees.cuivre_fixe[{}].reseau", i))?;
        r.couches(&f.couches, &format!("donnees.cuivre_fixe[{}].couches", i), false)?;
        r.classe(f.classe_isolement, || format!("donnees.cuivre_fixe[{}].classe_isolement", i))?;
    }

    // composants
    for (i, c) in d.composants.iter().enumerate() {
        let ch = format!("donnees.composants[{}]", i);
        for (k, p) in c.pastilles.iter().enumerate() {
            let chp = format!("{}.pastilles[{}]", ch, k);
            r.reseau(&p.reseau, true, || format!("{}.reseau", chp))?;
            r.couches(&p.couches, &format!("{}.couches", chp), false)?;
            r.classe(p.classe_isolement, || format!("{}.classe_isolement", chp))?;
        }
        for (k, g) in c.regions.iter().enumerate() {
            let chr = format!("{}.regions[{}]", ch, k);
            r.reseau(&g.reseau, true, || format!("{}.reseau", chr))?;
            r.couches(&g.couches, &format!("{}.couches", chr), false)?;
            r.classe(g.classe_isolement, || format!("{}.classe_isolement", chr))?;
            if let Some(l) = &g.liaison {
                r.reseaux(l, &format!("{}.liaison", chr))?;
            }
            if let Some(p) = &g.pastille {
                if !c.pastilles.iter().any(|x| &x.id == p) {
                    return Err(format!("{}.pastille : pastille {} inconnue dans la pièce {}", chr, court(p), court(&c.nom)));
                }
            }
        }
        for (k, v) in c.vias.iter().enumerate() {
            r.reseau(&v.reseau, true, || format!("{}.vias[{}].reseau", ch, k))?;
            r.classe(v.classe_isolement, || format!("{}.vias[{}].classe_isolement", ch, k))?;
        }
        for (k, z) in c.zones_interdites.iter().enumerate() {
            r.couches(&z.couches, &format!("{}.zones_interdites[{}].couches", ch, k), true)?;
        }
        for (k, x) in c.decoupes.iter().enumerate() {
            if !ids_decoupes.contains_key(x) {
                return Err(format!("{}.decoupes[{}] : découpe {} inconnue dans donnees.carte.decoupes", ch, k, court(x)));
            }
        }
    }

    // réseaux
    let noms_paires: BTreeSet<&str> = d.paires.iter().map(|p| p.nom.as_str()).collect();
    for (i, n) in d.reseaux.iter().enumerate() {
        let ch = format!("donnees.reseaux[{}]", i);
        for (nom, k) in [("piste", n.classes_isolement.piste), ("via", n.classes_isolement.via), ("surface", n.classes_isolement.surface)] {
            r.classe(k, || format!("{}.classes_isolement.{}", ch, nom))?;
        }
        if let Some(c) = &n.couche_preferee {
            r.couche(c, || format!("{}.couche_preferee", ch))?;
        }
        r.couches(&n.couches_permises, &format!("{}.couches_permises", ch), false)?;
        r.couches(&n.plan_couches, &format!("{}.plan_couches", ch), false)?;
        r.couches(&n.remplissages, &format!("{}.remplissages", ch), false)?;
        let plans: BTreeSet<&str> =
            d.couches.iter().filter(|c| c.zones_plan.iter().any(|z| z.reseau == n.nom)).map(|c| c.nom.as_str()).collect();
        if ensemble(&n.plan_couches) != plans {
            return Err(format!("{}.plan_couches : incohérent avec donnees.couches[].zones_plan (source unique, §2.5)", ch));
        }
        let rempl: BTreeSet<&str> =
            d.carte.remplissages.iter().filter(|s| s.reseau == n.nom).map(|s| s.couche.as_str()).collect();
        if ensemble(&n.remplissages) != rempl {
            return Err(format!("{}.remplissages : incohérent avec donnees.carte.remplissages (source unique, §2.5)", ch));
        }
        if let Some(p) = &n.paire {
            if !noms_paires.contains(p.as_str()) {
                return Err(format!("{}.paire : paire {} inconnue dans donnees.paires", ch, court(p)));
            }
        }
    }

    // listes de références
    for (i, c) in d.convertisseurs.iter().enumerate() {
        let ch = format!("donnees.convertisseurs[{}]", i);
        for (nom, p) in [("haut", &c.haut), ("bas", &c.bas), ("inductance", &c.inductance)] {
            r.piece(p, || format!("{}.{}", ch, nom))?;
        }
        if !c.pilote.is_empty() {
            r.piece(&c.pilote, || format!("{}.pilote", ch))?;
        }
        for (nom, l) in [
            ("cond_entree", &c.cond_entree),
            ("cond_sortie", &c.cond_sortie),
            ("cond_bulk", &c.cond_bulk),
            ("cond_pilote", &c.cond_pilote),
            ("cond_bootstrap", &c.cond_bootstrap),
        ] {
            r.pieces(l, &format!("{}.{}", ch, nom))?;
        }
        for (nom, n) in [
            ("reseau_entree", &c.reseau_entree),
            ("reseau_sortie", &c.reseau_sortie),
            ("reseau_masse", &c.reseau_masse),
            ("reseau_commutation", &c.reseau_commutation),
        ] {
            r.reseau(n, false, || format!("{}.{}", ch, nom))?;
        }
        r.reseaux(&c.reseaux_courts, &format!("{}.reseaux_courts", ch))?;
        r.reseaux(&c.reseaux_sensibles, &format!("{}.reseaux_sensibles", ch))?;
        r.pieces(&c.boucle_chaude.condensateurs, &format!("{}.boucle_chaude.condensateurs", ch))?;
        r.reseau(&c.boucle_chaude.reseau_rail, false, || format!("{}.boucle_chaude.reseau_rail", ch))?;
        r.reseau(&c.boucle_chaude.reseau_masse, false, || format!("{}.boucle_chaude.reseau_masse", ch))?;
    }
    r.pieces(&d.sans_piste_dessous, "donnees.sans_piste_dessous")?;
    for (i, x) in d.decouplages.iter().enumerate() {
        let ch = format!("donnees.decouplages[{}]", i);
        r.piece(&x.comp, || format!("{}.comp", ch))?;
        r.pastille(&x.pastille, || format!("{}.pastille", ch))?;
        r.reseau(&x.reseau, false, || format!("{}.reseau", ch))?;
        r.pastilles(&x.broches_ci, &format!("{}.broches_ci", ch))?;
    }
    for (i, e) in d.eloignements.iter().enumerate() {
        for (k, o) in e.objets.iter().enumerate() {
            r.objet(o, format!("donnees.eloignements[{}].objets[{}]", i, k))?;
        }
        r.pieces(&e.reference, &format!("donnees.eloignements[{}].reference", i))?;
    }
    for (i, c) in d.charges.iter().enumerate() {
        r.pastilles(&c.pastilles, &format!("donnees.charges[{}].pastilles", i))?;
    }
    for (i, g) in d.groupes.iter().enumerate() {
        let ch = format!("donnees.groupes[{}]", i);
        r.pieces(&g.pieces, &format!("{}.pieces", ch))?;
        for (k, e) in g.exemplaires.iter().enumerate() {
            r.pieces(&e.pieces, &format!("{}.exemplaires[{}].pieces", ch, k))?;
        }
        for (k, l) in g.correspondance_pieces.iter().enumerate() {
            r.pieces(l, &format!("{}.correspondance_pieces[{}]", ch, k))?;
        }
        for (k, l) in g.correspondance_reseaux.iter().enumerate() {
            r.reseaux(l, &format!("{}.correspondance_reseaux[{}]", ch, k))?;
        }
    }
    for (i, p) in d.paires.iter().enumerate() {
        r.reseau(&p.positif, false, || format!("donnees.paires[{}].positif", i))?;
        r.reseau(&p.negatif, false, || format!("donnees.paires[{}].negatif", i))?;
    }
    for (i, a) in d.au_bord.iter().enumerate() {
        r.piece(&a.piece, || format!("donnees.au_bord[{}].piece", i))?;
    }
    r.reseaux(&d.masses, "donnees.masses")?;
    Ok(d)
}

// =================================================================================================================
// Placements (forme v1 ou `placements/2`, mil)
// =================================================================================================================

/// Poses lues d'un placement, une par composant (indice de `composants`) : les pièces absentes et les pièces
/// virtuelles sont prises à leur `pose_cible` (§2.7). Désignateur inconnu ou en double ⇒ `Err` (chemin du champ).
pub fn lire_placements(v: &Value, d: &Donnees) -> R<Vec<Pose>> {
    let r = C { v, ch: "placement".to_string() };
    let pieces = match v {
        Value::Array(_) => r.liste()?,
        Value::Object(_) => {
            let fc = r.f("format")?;
            let f = fc.txt()?;
            if f != "placements/2" {
                return fc.err(format!("format {} non pris en charge (placements/2 ou liste v1 attendu)", court(&f)));
            }
            let uc = r.f("unite")?;
            if uc.txt()? != "mil" {
                return uc.err("« mil » attendu");
            }
            r.f("origine")?.txt()?;
            r.f("pieces")?.liste()?
        }
        _ => return r.attendu("objet placements/2 ou liste (forme v1)"),
    };
    let mut poses: Vec<Pose> = d.composants.iter().map(|c| c.pose_cible).collect();
    let mut vus = BTreeSet::new();
    for e in pieces {
        let dc = e.f("designator")?;
        let des = dc.txt()?;
        let i = match d.index_composants.get(&des) {
            Some(&i) => i,
            None => return dc.err(format!("pièce {} inconnue dans les données", court(&des))),
        };
        if !vus.insert(i) {
            return dc.err(format!("pièce {} en double", court(&des)));
        }
        let dessous = e.f("side")?.parmi(&["top", "bottom"])? == "bottom";
        let pose = Pose { x: e.f("x")?.nb()? * MIL_MM, y: e.f("y")?.nb()? * MIL_MM, rot: e.f("rotation")?.nb()?, dessous };
        if !d.composants[i].virtuel {
            poses[i] = pose;
        }
    }
    Ok(poses)
}

// =================================================================================================================
// Tests du lecteur (critères du jalon A1)
// =================================================================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use crate::essais;
    use serde_json::json;

    fn erreur(v: &Value) -> String {
        match lire(v) {
            Ok(_) => panic!("données acceptées à tort"),
            Err(e) => e,
        }
    }

    /// Chemins de tous les champs d'objets de la valeur (« donnees.a.b[0].c ») et leurs pointeurs JSON, hors du
    /// contenu de `parametres_deduction` (trace).
    fn chemins(v: &Value, ch: &str, pointeur: &str, sortie: &mut Vec<(String, String)>) {
        match v {
            Value::Object(o) => {
                for (k, x) in o {
                    let (c, p) = (format!("{}.{}", ch, k), format!("{}/{}", pointeur, k));
                    sortie.push((c.clone(), p.clone()));
                    if k != "parametres_deduction" {
                        chemins(x, &c, &p, sortie);
                    }
                }
            }
            Value::Array(l) => {
                for (i, x) in l.iter().enumerate() {
                    chemins(x, &format!("{}[{}]", ch, i), &format!("{}/{}", pointeur, i), sortie);
                }
            }
            _ => {}
        }
    }

    fn retirer(v: &mut Value, pointeur: &str) {
        let (parent, cle) = pointeur.rsplit_once('/').unwrap();
        let p = if parent.is_empty() { v } else { v.pointer_mut(parent).unwrap() };
        p.as_object_mut().unwrap().remove(cle);
    }

    #[test]
    fn exemple_complet_accepte() {
        let d = lire(&essais::carte_complete()).unwrap();
        assert_eq!(d.couches.len(), 4);
        assert_eq!(d.couches_routage().len(), 3);
        assert_eq!(d.composants.len(), 12);
        assert_eq!(d.index_pastilles["U1#8"], (0, 8));
        assert_eq!(d.regles.isolement.requis(0, 1), 0.25);
        assert_eq!(d.convertisseurs[0].boucle_chaude.condensateurs, vec!["C1".to_string()]);
        assert!(d.composants.iter().any(|c| c.virtuel));
        for n in [2, 6] {
            lire(&essais::carte(n, false)).unwrap();
        }
    }

    #[test]
    fn chaque_champ_obligatoire_retire_code_2_avec_son_chemin() {
        let base = essais::carte_complete();
        let mut champs = Vec::new();
        chemins(&base, "donnees", "", &mut champs);
        assert!(champs.len() > 500, "{} champs", champs.len());
        let mut verifies = 0;
        for (chemin, pointeur) in &champs {
            let mut v = base.clone();
            retirer(&mut v, pointeur);
            let r = lire(&v);
            if chemin == "donnees.parametres_deduction.pas_fin_mm" {
                // trace sans contenu imposé
                continue;
            }
            if chemin.ends_with(".largeur") && chemin.contains("zones_") {
                // largeur exigée pour une polyligne seulement (les zones de l'exemple sont des polygones)
                assert!(r.is_ok(), "{} : {:?}", chemin, r.err());
                continue;
            }
            let e = r.err().unwrap_or_else(|| panic!("{} retiré : données acceptées", chemin));
            if chemin.ends_with(".rect") {
                assert!(e.contains("exactement un des champs « rect » ou « polygone »"), "{} : {}", chemin, e);
            } else {
                assert_eq!(e, format!("{} : champ obligatoire manquant", chemin));
            }
            verifies += 1;
        }
        assert!(verifies > 500, "{}", verifies);
    }

    #[test]
    fn reseau_non_declare_code_2() {
        for (pointeur, chemin) in [
            ("/composants/0/pastilles/0/reseau", "donnees.composants[0].pastilles[0].reseau"),
            ("/composants/0/regions/0/reseau", "donnees.composants[0].regions[0].reseau"),
            ("/composants/10/vias/0/reseau", "donnees.composants[10].vias[0].reseau"),
            ("/cuivre_fixe/0/reseau", "donnees.cuivre_fixe[0].reseau"),
            ("/carte/remplissages/0/reseau", "donnees.carte.remplissages[0].reseau"),
            ("/couches/1/zones_plan/0/reseau", "donnees.couches[1].zones_plan[0].reseau"),
        ] {
            let mut v = essais::carte_complete();
            *v.pointer_mut(pointeur).unwrap() = json!("ABSENT");
            assert_eq!(erreur(&v), format!("{} : réseau ABSENT non déclaré dans donnees.reseaux", chemin));
        }
        // une pastille sans réseau (texte vide) est permise
        let mut v = essais::carte_complete();
        v["composants"][0]["pastilles"][1]["reseau"] = json!("");
        lire(&v).unwrap();
    }

    #[test]
    fn references_croisees_invalides_dans_chaque_liste() {
        let cas: &[(&str, Value, &str)] = &[
            ("/convertisseurs/0/haut", json!("X99"), "donnees.convertisseurs[0].haut : pièce X99 inconnue"),
            ("/convertisseurs/0/pilote", json!("X99"), "donnees.convertisseurs[0].pilote : pièce X99 inconnue"),
            ("/convertisseurs/0/cond_bulk/0", json!("X99"), "donnees.convertisseurs[0].cond_bulk[0] : pièce X99 inconnue"),
            ("/convertisseurs/0/reseau_commutation", json!("X99"), "donnees.convertisseurs[0].reseau_commutation : réseau X99 non déclaré"),
            ("/convertisseurs/0/reseaux_courts/1", json!("X99"), "donnees.convertisseurs[0].reseaux_courts[1] : réseau X99 non déclaré"),
            ("/convertisseurs/0/boucle_chaude/condensateurs/0", json!("X99"), "donnees.convertisseurs[0].boucle_chaude.condensateurs[0] : pièce X99 inconnue"),
            ("/convertisseurs/0/boucle_chaude/reseau_rail", json!("X99"), "donnees.convertisseurs[0].boucle_chaude.reseau_rail : réseau X99 non déclaré"),
            ("/groupes/0/pieces/1", json!("X99"), "donnees.groupes[0].pieces[1] : pièce X99 inconnue"),
            ("/groupes/1/exemplaires/1/pieces/0", json!("X99"), "donnees.groupes[1].exemplaires[1].pieces[0] : pièce X99 inconnue"),
            ("/groupes/1/correspondance_pieces/0/1", json!("X99"), "donnees.groupes[1].correspondance_pieces[0][1] : pièce X99 inconnue"),
            ("/groupes/1/correspondance_reseaux/0/0", json!("X99"), "donnees.groupes[1].correspondance_reseaux[0][0] : réseau X99 non déclaré"),
            ("/eloignements/0/objets/0", json!("U1#99"), "donnees.eloignements[0].objets[0] : objet U1#99 inconnu"),
            ("/eloignements/0/objets/1", json!("R2.7"), "donnees.eloignements[0].objets[1] : objet R2.7 inconnu"),
            ("/eloignements/0/reference/0", json!("X99"), "donnees.eloignements[0].reference[0] : pièce X99 inconnue"),
            ("/decouplages/0/comp", json!("X99"), "donnees.decouplages[0].comp : pièce X99 inconnue"),
            ("/decouplages/0/pastille", json!("C1#9"), "donnees.decouplages[0].pastille : pastille C1#9 inconnue"),
            ("/decouplages/0/reseau", json!("X99"), "donnees.decouplages[0].reseau : réseau X99 non déclaré"),
            ("/decouplages/0/broches_ci/0", json!("U1#99"), "donnees.decouplages[0].broches_ci[0] : pastille U1#99 inconnue"),
            ("/charges/0/pastilles/0", json!("R1#5"), "donnees.charges[0].pastilles[0] : pastille R1#5 inconnue"),
            ("/sans_piste_dessous/0", json!("X99"), "donnees.sans_piste_dessous[0] : pièce X99 inconnue"),
            ("/zones_imposees/0/pieces/0", json!("X99"), "donnees.zones_imposees[0].pieces[0] : pièce X99 inconnue"),
            ("/zones_routage_interdites/0/couches/0", json!("Int9"), "donnees.zones_routage_interdites[0].couches[0] : couche Int9 inconnue"),
            ("/au_bord/0/piece", json!("X99"), "donnees.au_bord[0].piece : pièce X99 inconnue"),
            ("/paires/0/positif", json!("X99"), "donnees.paires[0].positif : réseau X99 non déclaré"),
            ("/masses/0", json!("X99"), "donnees.masses[0] : réseau X99 non déclaré"),
            ("/reseaux/1/couche_preferee", json!("Int9"), "donnees.reseaux[1].couche_preferee : couche Int9 inconnue"),
            ("/reseaux/1/couches_permises/0", json!("Int9"), "donnees.reseaux[1].couches_permises[0] : couche Int9 inconnue"),
            ("/reseaux/0/plan_couches", json!(["Int2"]), "donnees.reseaux[0].plan_couches : incohérent avec donnees.couches[].zones_plan"),
            ("/reseaux/1/remplissages", json!(["Dessus"]), "donnees.reseaux[1].remplissages : incohérent avec donnees.carte.remplissages"),
            ("/reseaux/8/paire", json!("PAIRE9"), "donnees.reseaux[8].paire : paire PAIRE9 inconnue"),
            ("/composants/0/pastilles/0/couches/0", json!("Int9"), "donnees.composants[0].pastilles[0].couches[0] : couche Int9 inconnue"),
            ("/composants/0/regions/0/pastille", json!("U1#99"), "donnees.composants[0].regions[0].pastille : pastille U1#99 inconnue"),
            ("/composants/0/regions/0/couches/0", json!("Int9"), "donnees.composants[0].regions[0].couches[0] : couche Int9 inconnue"),
            ("/composants/10/zones_interdites/0/couches", json!(["Int9"]), "donnees.composants[10].zones_interdites[0].couches[0] : couche Int9 inconnue"),
            ("/composants/10/decoupes/0", json!("D9"), "donnees.composants[10].decoupes[0] : découpe D9 inconnue"),
            ("/carte/decoupes/0/proprietaire", json!("X99"), "donnees.carte.decoupes[0].proprietaire : pièce X99 inconnue"),
            ("/carte/remplissages/0/couche", json!("Int9"), "donnees.carte.remplissages[0].couche : couche Int9 inconnue"),
            ("/cuivre_fixe/0/couches/0", json!("Int9"), "donnees.cuivre_fixe[0].couches[0] : couche Int9 inconnue"),
            ("/trous/0/piece", json!("X99"), "donnees.trous[0].piece : pièce X99 inconnue"),
            ("/composants/0/pastilles/2/classe_isolement", json!(7), "donnees.composants[0].pastilles[2].classe_isolement : classe d'isolement 7 hors de la matrice"),
            ("/reseaux/3/classes_isolement/via", json!(3), "donnees.reseaux[3].classes_isolement.via : classe d'isolement 3 hors de la matrice"),
            ("/regles/isolement/classe_reference", json!(3), "donnees.regles.isolement.classe_reference : classe 3 hors de la matrice"),
            ("/regles/isolement/matrice/0/1", json!(0.3), "donnees.regles.isolement.matrice[1][0] : matrice non symétrique"),
            ("/regles/isolement/matrice/2", json!([0.25, 0.2]), "donnees.regles.isolement.matrice[2] : ligne de la matrice attendu (3 nombres, trouvé 2 ; virgule décimale ?)"),
            ("/composants/5/nom", json!("C1"), "donnees.composants[5].nom : pièce « C1 » en double"),
            ("/composants/1/pastilles/0/id", json!("Q1#1"), "donnees.composants[1].pastilles[1].id : pastille « Q1#1 » en double"),
            ("/couches/2/rang", json!(3), "donnees.couches[2].rang : rang 3 au lieu de 2"),
            ("/couches/1/zones_plan", json!([]), "donnees.couches[1].zones_plan : non vide si et seulement si"),
        ];
        for (pointeur, valeur, attendu) in cas {
            let mut v = essais::carte_complete();
            *v.pointer_mut(pointeur).unwrap_or_else(|| panic!("{}", pointeur)) = valeur.clone();
            let e = lire(&v).err().unwrap_or_else(|| panic!("{} : données acceptées à tort", pointeur));
            assert!(e.starts_with(attendu), "{} : {}", pointeur, e);
        }
    }

    #[test]
    fn format_3_refuse_champ_inconnu_ignore() {
        let mut v = essais::carte_complete();
        v["format"] = json!("donnees_placeur/3");
        assert!(erreur(&v).contains("format donnees_placeur/3 non pris en charge"));
        let mut v = essais::carte_complete();
        v["champ_futur"] = json!({"x": 1});
        v["_commentaire"] = json!("libre");
        v["composants"][0]["pastilles"][0]["inconnu"] = json!([1, 2]);
        v["regles"]["_note"] = json!(null);
        lire(&v).unwrap();
        // types et énumérations : chemin du champ
        let mut v = essais::carte_complete();
        v["composants"][2]["pastilles"][1]["forme"] = json!("etoile");
        assert!(erreur(&v).starts_with("donnees.composants[2].pastilles[1].forme : « etoile » au lieu de rect, rond"));
        let mut v = essais::carte_complete();
        v["carte"]["marge_bord_mm"] = json!("1.0");
        assert_eq!(erreur(&v), "donnees.carte.marge_bord_mm : nombre attendu, trouvé un texte");
        let mut v = essais::carte_complete();
        v["carte"]["contour"][1] = json!([40.0]);
        assert!(erreur(&v).contains("virgule décimale ?"));
    }

    #[test]
    fn placements_v1_et_v2() {
        let c = essais::carte_complete();
        let d = lire(&c).unwrap();
        let p2 = essais::placement_cible(&c);
        let poses = lire_placements(&p2, &d).unwrap();
        for (i, comp) in d.composants.iter().enumerate() {
            assert!((poses[i].x - comp.pose_cible.x).abs() < 1e-9 && (poses[i].y - comp.pose_cible.y).abs() < 1e-9);
        }
        // forme v1 : liste nue, rotation flottante, pièce absente prise à sa pose cible, pièce virtuelle ignorée
        let v1 = json!([{"designator": "R1", "side": "bottom", "x": 500.0, "y": 250.0, "rotation": 12.5},
                        {"designator": "__libre_0", "side": "top", "x": 0.0, "y": 0.0, "rotation": 0.0}]);
        let poses = lire_placements(&v1, &d).unwrap();
        let r1 = d.index_composants["R1"];
        assert_eq!(poses[r1], Pose { x: 500.0 * MIL_MM, y: 250.0 * MIL_MM, rot: 12.5, dessous: true });
        let u1 = d.index_composants["U1"];
        assert_eq!(poses[u1], d.composants[u1].pose_cible);
        let v = d.index_composants["__libre_0"];
        assert_eq!(poses[v], d.composants[v].pose_cible);
        // erreurs : pièce inconnue, en double, face, format
        let e = lire_placements(&json!([{"designator": "X9", "side": "top", "x": 0, "y": 0, "rotation": 0}]), &d).unwrap_err();
        assert_eq!(e, "placement[0].designator : pièce X9 inconnue dans les données");
        let r = json!({"designator": "R1", "side": "top", "x": 0, "y": 0, "rotation": 0});
        assert!(lire_placements(&json!([r.clone(), r]), &d).unwrap_err().contains("en double"));
        let e = lire_placements(&json!([{"designator": "R1", "side": "haut", "x": 0, "y": 0, "rotation": 0}]), &d).unwrap_err();
        assert!(e.starts_with("placement[0].side : « haut » au lieu de top, bottom"), "{}", e);
        let mut p = essais::placement_cible(&c);
        p["format"] = json!("placements/3");
        assert!(lire_placements(&p, &d).unwrap_err().contains("placements/3 non pris en charge"));
    }

    #[test]
    fn vers_monde_exact_aux_quarts_de_tour() {
        let p = Pose { x: 10.0, y: 20.0, rot: 90.0, dessous: false };
        assert_eq!(vers_monde(&p, 1.0, 0.5), [9.5, 21.0]);
        let p = Pose { x: 0.0, y: 0.0, rot: -90.0, dessous: true };
        assert_eq!(vers_monde(&p, 1.0, 0.5), [-0.5, -1.0]);
    }

    /// Générateur déterministe des altérations.
    struct Alea(u64);

    impl Alea {
        fn sous(&mut self, n: usize) -> usize {
            self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((self.0 >> 33) % n as u64) as usize
        }
    }

    #[test]
    fn aucune_panique_sur_200_variantes_alterees() {
        let base = essais::carte_complete();
        let texte = crate::fichiers::texte_json(&base);
        let mut champs = Vec::new();
        chemins(&base, "donnees", "", &mut champs);
        let d = crate::fichiers::tests::dossier_essai("variantes_alterees");
        let fichier = d.join("donnees_placeur.json");
        let ch = fichier.to_string_lossy().into_owned();
        let mut a = Alea(20261118);
        let remplacements =
            [json!(null), json!(-1), json!(1e300), json!(""), json!("x"), json!([]), json!({}), json!(true), json!([1, 2, 3]), json!(0)];
        let mut codes = [0usize; 5];
        for k in 0..200 {
            let contenu = match k % 4 {
                // texte tronqué (coupé sur une limite de caractère)
                0 => {
                    let mut n = a.sous(texte.len());
                    while !texte.is_char_boundary(n) {
                        n -= 1;
                    }
                    texte[..n].to_string()
                }
                // champ retiré
                1 => {
                    let mut v = base.clone();
                    retirer(&mut v, &champs[a.sous(champs.len())].1);
                    crate::fichiers::texte_json(&v)
                }
                // valeurs remplacées (deux)
                _ => {
                    let mut v = base.clone();
                    for _ in 0..2 {
                        let p = &champs[a.sous(champs.len())].1;
                        if let Some(x) = v.pointer_mut(p) {
                            *x = remplacements[a.sous(remplacements.len())].clone();
                        }
                    }
                    crate::fichiers::texte_json(&v)
                }
            };
            std::fs::write(&fichier, contenu.as_bytes()).unwrap();
            let args: Vec<String> = ["estimer", "--donnees", ch.as_str()].iter().map(|s| s.to_string()).collect();
            let (mut out, mut err) = (Vec::new(), Vec::new());
            let code = crate::executer(&args, &mut out, &mut err);
            let e = String::from_utf8_lossy(&err);
            assert!(code == 0 || code == 2, "variante {} : code {} : {}", k, code, e);
            codes[code as usize] += 1;
            assert!(!e.contains("variantes_alterees"), "chemin recopié : {}", e);
        }
        assert!(codes[2] > 150, "{:?}", codes);
    }
}
