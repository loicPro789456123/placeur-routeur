//! Fichiers : lecture stricte des fichiers JSON écrits à la main (contrat §2.1), écriture atomique (§0.2-5).
//!
//! - Lecture : BOM UTF-8 du bloc-notes accepté ; repli sur cp1252 (Windows-1252) signalé par un avertissement ;
//!   clé répétée dans un même objet refusée (la première serait sinon perdue en silence) ; erreurs de syntaxe
//!   traduites en français avec la ligne, la position (décision D27 du lot A : « position » plutôt que le mot de la
//!   recherche du critère A2) et la cause probable (virgule finale, apostrophes, barre oblique inverse non doublée,
//!   virgule décimale, commentaire, mots en majuscules).
//! - Écriture : `<nom>.tmp` puis renommage, réessayé 5 fois toutes les 0,2 s sur un refus d'accès (antivirus, outil de
//!   synchronisation) ; JSON en UTF-8 sans BOM, fins de ligne LF.
//! - Messages : seulement le nom du fichier, jamais le chemin reçu (§0.2-7).
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use std::fmt;
use std::io::{ErrorKind, Write};
use std::path::Path;

/// Nom du fichier seul (dernier élément du chemin), pour les messages.
pub fn nom_fichier(chemin: &Path) -> String {
    chemin.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "(fichier sans nom)".to_string())
}

/// Texte d'une erreur d'entrée-sortie, sans chemin.
pub fn texte_erreur_es(e: &std::io::Error) -> String {
    match e.kind() {
        ErrorKind::NotFound => "fichier introuvable".to_string(),
        ErrorKind::PermissionDenied => "accès refusé (fichier ouvert ailleurs, dossier, ou droits insuffisants)".to_string(),
        ErrorKind::AlreadyExists => "existe déjà".to_string(),
        ErrorKind::InvalidData => "contenu illisible".to_string(),
        ErrorKind::OutOfMemory => "mémoire insuffisante".to_string(),
        ErrorKind::IsADirectory => "c'est un dossier, pas un fichier".to_string(),
        ErrorKind::NotADirectory => "un élément du chemin n'est pas un dossier".to_string(),
        ErrorKind::StorageFull => "disque plein".to_string(),
        ErrorKind::ReadOnlyFilesystem => "support en lecture seule".to_string(),
        k => format!("erreur d'entrée-sortie ({:?})", k),
    }
}

/// Texte décodé d'un fichier, et avertissements de lecture.
#[derive(Debug)]
pub struct TexteLu {
    pub texte: String,
    pub avertissements: Vec<String>,
}

/// Table de Windows-1252 pour les octets 0x80 à 0x9F (les autres octets valent leur point de code Unicode).
const CP1252_80_9F: [char; 32] = [
    '\u{20AC}', '\u{FFFD}', '\u{201A}', '\u{0192}', '\u{201E}', '\u{2026}', '\u{2020}', '\u{2021}', '\u{02C6}', '\u{2030}',
    '\u{0160}', '\u{2039}', '\u{0152}', '\u{FFFD}', '\u{017D}', '\u{FFFD}', '\u{FFFD}', '\u{2018}', '\u{2019}', '\u{201C}',
    '\u{201D}', '\u{2022}', '\u{2013}', '\u{2014}', '\u{02DC}', '\u{2122}', '\u{0161}', '\u{203A}', '\u{0153}', '\u{FFFD}',
    '\u{017E}', '\u{0178}',
];

/// Décodage d'octets lus : UTF-8 (BOM retiré), sinon Windows-1252 avec un avertissement.
pub fn decoder_texte(octets: &[u8], nom: &str) -> TexteLu {
    let sans_bom = octets.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(octets);
    match std::str::from_utf8(sans_bom) {
        Ok(t) => TexteLu { texte: t.to_string(), avertissements: Vec::new() },
        Err(_) => {
            let texte: String = sans_bom
                .iter()
                .map(|&o| match o {
                    0x80..=0x9F => CP1252_80_9F[(o - 0x80) as usize],
                    _ => o as char,
                })
                .collect();
            TexteLu {
                texte,
                avertissements: vec![format!(
                    "{} : le fichier n'est pas en UTF-8 ; lu en Windows-1252 (cp1252). Enregistrez-le en UTF-8 pour éviter \
                     toute erreur sur les caractères accentués.",
                    nom
                )],
            }
        }
    }
}

/// Octets d'un fichier (message d'erreur sans chemin).
pub fn lire_octets(chemin: &Path) -> Result<Vec<u8>, String> {
    let nom = nom_fichier(chemin);
    if chemin.is_dir() {
        return Err(format!("{} : c'est un dossier, pas un fichier", nom));
    }
    std::fs::read(chemin).map_err(|e| format!("{} : {}", nom, texte_erreur_es(&e)))
}

/// Valeur JSON construite en refusant une clé répétée dans un même objet.
struct ValeurStricte(Value);

impl<'de> Deserialize<'de> for ValeurStricte {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        d.deserialize_any(VisiteurStrict).map(ValeurStricte)
    }
}

struct VisiteurStrict;

impl<'de> Visitor<'de> for VisiteurStrict {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("une valeur JSON")
    }
    fn visit_bool<E: de::Error>(self, v: bool) -> Result<Value, E> {
        Ok(Value::Bool(v))
    }
    fn visit_i64<E: de::Error>(self, v: i64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }
    fn visit_u64<E: de::Error>(self, v: u64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }
    fn visit_f64<E: de::Error>(self, v: f64) -> Result<Value, E> {
        Number::from_f64(v).map(Value::Number).ok_or_else(|| E::custom("nombre non fini"))
    }
    fn visit_str<E: de::Error>(self, v: &str) -> Result<Value, E> {
        Ok(Value::String(v.to_string()))
    }
    fn visit_string<E: de::Error>(self, v: String) -> Result<Value, E> {
        Ok(Value::String(v))
    }
    fn visit_unit<E: de::Error>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }
    fn visit_none<E: de::Error>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }
    fn visit_some<D: Deserializer<'de>>(self, d: D) -> Result<Value, D::Error> {
        ValeurStricte::deserialize(d).map(|v| v.0)
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut v = Vec::new();
        while let Some(e) = seq.next_element::<ValeurStricte>()? {
            v.push(e.0);
        }
        Ok(Value::Array(v))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut o = Map::new();
        while let Some(k) = map.next_key::<String>()? {
            if o.contains_key(&k) {
                return Err(de::Error::custom(format!("{}clé « {} » répétée dans le même objet", MARQUE_REPETEE, k)));
            }
            let v = map.next_value::<ValeurStricte>()?;
            o.insert(k, v.0);
        }
        Ok(Value::Object(o))
    }
}

/// Préfixe interne des erreurs de clé répétée (retiré du message).
const MARQUE_REPETEE: &str = "\u{1}";

/// Traduction des messages de serde_json (texte de la version figée par Cargo.lock).
fn traduire_message(m: &str) -> String {
    let t = match m {
        "EOF while parsing a list" => "fin du fichier au milieu d'une liste (crochet fermant « ] » manquant ?)",
        "EOF while parsing an object" => "fin du fichier au milieu d'un objet (accolade fermante « } » manquante ?)",
        "EOF while parsing a string" => "fin du fichier au milieu d'un texte (guillemet fermant manquant ?)",
        "EOF while parsing a value" => "fin du fichier : une valeur est attendue (fichier vide ou incomplet ?)",
        "expected `:`" => "deux-points « : » attendu après la clé",
        "expected `,` or `]`" => "virgule « , » ou crochet fermant « ] » attendu",
        "expected `,` or `}`" => "virgule « , » ou accolade fermante « } » attendue",
        "expected ident" => "mot inconnu (seuls true, false et null s'écrivent sans guillemets)",
        "expected value" => "valeur attendue",
        "expected `\"`" => "guillemet « \" » attendu",
        "invalid escape" => "séquence d'échappement invalide après une barre oblique inverse",
        "invalid number" => "nombre mal écrit",
        "number out of range" => "nombre hors des limites représentables",
        "invalid unicode code point" => "caractère Unicode invalide",
        "control character (\\u0000-\\u001F) found while parsing a string" => {
            "caractère de contrôle (tabulation ou retour à la ligne) dans un texte"
        }
        "key must be a string" => "une clé doit être un texte entre guillemets",
        "lone leading surrogate in hex escape" | "unexpected end of hex escape" => "échappement \\u invalide",
        "trailing comma" => "virgule en trop avant « } » ou « ] »",
        "trailing characters" => "caractères en trop après la fin du document JSON",
        "recursion limit exceeded" => "imbrication trop profonde",
        autre => {
            if let Some(r) = autre.strip_prefix(MARQUE_REPETEE) {
                return r.to_string();
            }
            return "syntaxe JSON invalide".to_string();
        }
    };
    t.to_string()
}

/// Cause probable d'une erreur de syntaxe, d'après le texte autour de la position fautive.
fn cause_probable(message: &str, texte: &str, decalage: usize) -> Option<&'static str> {
    if message == "trailing comma" {
        return Some("virgule finale : supprimer la virgule qui précède « } » ou « ] »");
    }
    if message == "invalid escape" {
        return Some("barre oblique inverse non doublée : écrire \\\\ (ou /) dans un texte");
    }
    let octets = texte.as_bytes();
    let d = decalage.min(octets.len());
    // caractère fautif et caractère non blanc qui le précède
    let ici = texte[d..].chars().next();
    let avant = texte[..d].trim_end_matches([' ', '\t']).chars().next_back();
    let avant2 = {
        let t = texte[..d].trim_end_matches([' ', '\t']);
        let t = t.strip_suffix(',').unwrap_or("");
        t.chars().next_back()
    };
    let suite = &texte[d..];
    if let Some(c) = ici {
        if c == '\'' || texte[..d].ends_with('\'') {
            return Some("apostrophes : les clés et les textes s'écrivent entre guillemets droits \"…\"");
        }
        if matches!(c, '\u{201C}' | '\u{201D}' | '\u{2018}' | '\u{2019}' | '\u{00AB}' | '\u{00BB}') {
            return Some("guillemets typographiques : utiliser des guillemets droits \"…\"");
        }
        if suite.starts_with("//") || suite.starts_with("/*") || c == '#' {
            return Some("commentaire non permis en JSON : utiliser une clé commençant par _ (par exemple \"_commentaire\")");
        }
        if suite.starts_with("True") || suite.starts_with("False") || suite.starts_with("None") || suite.starts_with("TRUE")
            || suite.starts_with("FALSE") || suite.starts_with("NULL") || suite.starts_with("Null")
        {
            return Some("écrire true, false et null en minuscules (None s'écrit null)");
        }
        if c.is_ascii_digit() && avant == Some(',') && avant2.is_some_and(|p| p.is_ascii_digit()) {
            return Some("virgule décimale ? écrire 0.5 et non 0,5");
        }
        if (message == "expected `,` or `}`" || message == "expected `,` or `]`") && (c == '"' || c == '{' || c == '[' || c.is_ascii_digit()) {
            return Some("virgule manquante entre deux éléments ?");
        }
    }
    if message.starts_with("EOF") {
        return Some("accolade « } », crochet « ] » ou guillemet fermant manquant");
    }
    None
}

/// Décalage en octets d'une position (ligne et position comptées à partir de 1 par serde_json, en octets).
fn decalage_de(texte: &str, ligne: usize, position: usize) -> usize {
    let mut debut = 0;
    for _ in 1..ligne {
        match texte[debut..].find('\n') {
            Some(p) => debut += p + 1,
            None => break,
        }
    }
    let mut d = (debut + position.saturating_sub(1)).min(texte.len());
    while d > 0 && !texte.is_char_boundary(d) {
        d -= 1;
    }
    d
}

/// Analyse stricte d'un texte JSON ; message d'erreur traduit, préfixé du nom du fichier.
pub fn analyser_json(texte: &str, nom: &str) -> Result<Value, String> {
    match serde_json::from_str::<ValeurStricte>(texte) {
        Ok(v) => Ok(v.0),
        Err(e) => {
            let brut = e.to_string();
            let suffixe = format!(" at line {} column {}", e.line(), e.column());
            let message = brut.strip_suffix(&suffixe).unwrap_or(&brut).to_string();
            if e.line() == 0 {
                return Err(format!("{} : JSON invalide : {}", nom, traduire_message(&message)));
            }
            let d = decalage_de(texte, e.line(), e.column());
            // position en caractères (et non en octets) dans la ligne
            let debut_ligne = texte[..d].rfind('\n').map(|p| p + 1).unwrap_or(0);
            let position = texte[debut_ligne..d].chars().count() + 1;
            let mut s = format!("{} : JSON invalide, ligne {}, position {} : {}", nom, e.line(), position, traduire_message(&message));
            if let Some(c) = cause_probable(&message, texte, d) {
                s.push_str(" ; cause probable : ");
                s.push_str(c);
            }
            Err(s)
        }
    }
}

/// Écriture atomique : `<nom>.tmp` dans le même dossier puis renommage (remplace le fichier existant).
pub fn ecrire_atomique(chemin: &Path, octets: &[u8]) -> Result<(), String> {
    let nom = nom_fichier(chemin);
    let tmp = chemin.with_file_name(format!("{}.tmp", nom));
    let ecrire = || -> std::io::Result<()> {
        let mut f = std::fs::File::create(&tmp)?;
        f.write_all(octets)?;
        f.sync_all()
    };
    if let Err(e) = ecrire() {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("{} : écriture impossible : {}", nom, texte_erreur_es(&e)));
    }
    let mut essai = 0;
    loop {
        match std::fs::rename(&tmp, chemin) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == ErrorKind::PermissionDenied && essai < 5 => {
                essai += 1;
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
            Err(e) => {
                let _ = std::fs::remove_file(&tmp);
                return Err(format!("{} : remplacement impossible : {}", nom, texte_erreur_es(&e)));
            }
        }
    }
}

/// Texte JSON indenté, terminé par une fin de ligne (UTF-8, LF, caractères non ASCII écrits tels quels).
pub fn texte_json(v: &Value) -> String {
    let mut s = serde_json::to_string_pretty(v).unwrap_or_else(|_| "null".to_string());
    s.push('\n');
    s
}

/// Écriture atomique d'un JSON.
pub fn ecrire_json_atomique(chemin: &Path, v: &Value) -> Result<(), String> {
    ecrire_atomique(chemin, texte_json(v).as_bytes())
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::path::PathBuf;

    /// Dossier d'essai propre à un test, sous `target/` (jamais suivi par git), vidé au départ.
    pub(crate) fn dossier_essai(nom: &str) -> PathBuf {
        let d = Path::new(env!("CARGO_MANIFEST_DIR")).join("target").join("essais_unitaires").join(nom);
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn bom_accepte_sans_avertissement() {
        let mut o = vec![0xEF, 0xBB, 0xBF];
        o.extend_from_slice("{\"a\": \"é\"}".as_bytes());
        let lu = decoder_texte(&o, "p.json");
        assert_eq!(lu.texte, "{\"a\": \"é\"}");
        assert!(lu.avertissements.is_empty());
        let v = analyser_json(&lu.texte, "p.json").unwrap();
        assert_eq!(v["a"], "é");
    }

    #[test]
    fn repli_cp1252_signale() {
        // « réglage – essai » en Windows-1252 : é = 0xE9, tiret demi-cadratin = 0x96
        let o = b"{\"_c\": \"r\xE9glage \x96 essai\"}";
        let lu = decoder_texte(o, "p.json");
        assert_eq!(lu.texte, "{\"_c\": \"réglage – essai\"}");
        assert_eq!(lu.avertissements.len(), 1);
        assert!(lu.avertissements[0].contains("cp1252"), "{}", lu.avertissements[0]);
        assert!(lu.avertissements[0].starts_with("p.json : "));
    }

    #[test]
    fn json_fautif_traduit() {
        let cas: &[(&str, &[&str])] = &[
            ("{\"recuit\": {\"alpha\": 0.9,}}", &["ligne 1", "position 26", "virgule finale"]),
            ("{'recuit': {}}", &["ligne 1", "position 2", "apostrophes"]),
            ("{\"_c\": \"dossier\\sous\"}", &["ligne 1", "barre oblique inverse non doublée"]),
            ("{\n  \"a\": 0,5\n}", &["ligne 2", "virgule décimale"]),
            ("{\n  \"a\": True\n}", &["ligne 2", "minuscules"]),
            ("{\n  // note\n  \"a\": 1\n}", &["ligne 2", "commentaire"]),
            ("{\"a\": 1 \"b\": 2}", &["virgule manquante"]),
            ("{\"a\": [1, 2}", &["crochet"]),
            ("{\"a\": 1", &["fin du fichier"]),
            ("", &["fin du fichier"]),
            ("{\"a\": 1, \"a\": 2}", &["clé « a » répétée"]),
            ("{\"b\": {\"x\": 1, \"x\": 1}}", &["clé « x » répétée"]),
            ("{\"é\": 1,}", &["position 9", "virgule finale"]),
        ];
        for (texte, attendus) in cas {
            let e = analyser_json(texte, "parametres_moteur.json").unwrap_err();
            assert!(e.starts_with("parametres_moteur.json : JSON invalide"), "{}", e);
            for a in attendus.iter() {
                assert!(e.contains(a), "texte {:?} : message {:?} sans {:?}", texte, e, a);
            }
            // aucun message anglais de serde_json
            for anglais in ["expected", "trailing", "EOF", "at line", "column"] {
                assert!(!e.contains(anglais), "message non traduit : {}", e);
            }
        }
    }

    #[test]
    fn lecture_sans_chemin_dans_les_messages() {
        let d = dossier_essai("lecture_sans_chemin");
        let absent = d.join("absent.json");
        let e = lire_octets(&absent).unwrap_err();
        assert_eq!(e, "absent.json : fichier introuvable");
        let e = lire_octets(&d).unwrap_err();
        assert!(e.contains("dossier") && !e.contains("essais_unitaires"), "{}", e);
    }

    #[test]
    fn ecriture_atomique_remplace_et_ne_laisse_rien() {
        let d = dossier_essai("ecriture_atomique");
        let f = d.join("stats.json");
        ecrire_json_atomique(&f, &serde_json::json!({"a": 1, "texte": "é"})).unwrap();
        ecrire_json_atomique(&f, &serde_json::json!({"a": 2, "texte": "è"})).unwrap();
        let o = std::fs::read(&f).unwrap();
        assert!(!o.starts_with(&[0xEF, 0xBB, 0xBF]), "pas de BOM");
        let t = String::from_utf8(o).unwrap();
        assert!(!t.contains('\r'), "fins de ligne LF");
        assert!(t.ends_with("}\n"));
        assert!(t.contains("\"è\""), "UTF-8 sans échappement");
        assert_eq!(analyser_json(&t, "stats.json").unwrap()["a"], 2);
        let restes: Vec<_> = std::fs::read_dir(&d).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(restes.len(), 1, "aucun fichier .tmp laissé : {:?}", restes);
    }
}
