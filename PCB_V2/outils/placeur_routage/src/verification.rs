//! Vérification (contrat §4.6, §5.1) : interface du vérificateur et sous-commande `verifier`.
//!
//! Le compte exact (`ko_exact`), la réparation et le verdict du moteur passent par le trait [`Verificateur`] (plan
//! du lot A, §1 point 5) : l'implémentation de production, [`Reference`], appelle `validite.rs` (lot E) ; les tests
//! la remplacent par un vérificateur scénarisé, ce qui découple les tests du moteur de l'avancement de `validite.rs`.

use crate::validite::{self, ControlePlacement, Mode, Verdict};
use serde_json::Value;

/// Vérificateur injectable : mêmes entrées et mêmes sorties que l'API de `validite.rs` (§5.1).
pub trait Verificateur: Sync {
    fn placement(&self, donnees: &Value, placements: &Value, grille: bool) -> Result<ControlePlacement, String>;
    fn routage(
        &self,
        donnees: &Value,
        placements: &Value,
        routage: &Value,
        couture: Option<&Value>,
        mode: Mode,
    ) -> Result<Verdict, String>;
}

/// Vérificateur de production : `validite.rs`.
pub struct Reference;

impl Verificateur for Reference {
    fn placement(&self, donnees: &Value, placements: &Value, grille: bool) -> Result<ControlePlacement, String> {
        validite::verifier_placement(donnees, placements, grille)
    }

    fn routage(
        &self,
        donnees: &Value,
        placements: &Value,
        routage: &Value,
        couture: Option<&Value>,
        mode: Mode,
    ) -> Result<Verdict, String> {
        validite::verifier_routage(donnees, placements, routage, couture, mode)
    }
}

/// Options de la vérification (`verifier` : `--mode`, `--sans-grille`, `--couture`).
pub struct Demande<'a> {
    pub donnees: &'a Value,
    pub placements: &'a Value,
    pub routage: &'a Value,
    pub couture: Option<&'a Value>,
    pub mode: Mode,
    pub grille: bool,
}

/// `verdict/2` complété du champ `placement` (contenu `controle_placement/1`, §2.8).
pub fn verdict_complet(v: &dyn Verificateur, q: &Demande) -> Result<Value, String> {
    let placement = v.placement(q.donnees, q.placements, q.grille)?;
    let verdict = v.routage(q.donnees, q.placements, q.routage, q.couture, q.mode)?;
    let mut j = verdict.en_json();
    match &mut j {
        Value::Object(o) => {
            o.insert("placement".to_string(), placement.en_json());
        }
        _ => return Err("verdict : objet JSON attendu du vérificateur".to_string()),
    }
    Ok(j)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::essais;
    use std::collections::BTreeMap;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// Vérificateur scénarisé : rend un verdict fixé et compte les appels.
    struct Scenario {
        liaisons: usize,
        appels: AtomicUsize,
    }

    impl Verificateur for Scenario {
        fn placement(&self, _: &Value, _: &Value, grille: bool) -> Result<ControlePlacement, String> {
            self.appels.fetch_add(1, Ordering::SeqCst);
            Ok(ControlePlacement { valide: grille, violations: Vec::new(), eloignements: Vec::new() })
        }

        fn routage(&self, _: &Value, _: &Value, _: &Value, _: Option<&Value>, mode: Mode) -> Result<Verdict, String> {
            self.appels.fetch_add(1, Ordering::SeqCst);
            let liaisons_ouvertes = (0..self.liaisons)
                .map(|k| validite::LiaisonOuverte {
                    reseau: "SIG".to_string(),
                    nature: validite::NatureLiaison::Pastilles,
                    pastilles: vec![format!("U1#{}", k)],
                    noms: vec![format!("U1.{}", k + 1)],
                    position_mm: [1.0, 2.0],
                })
                .collect();
            Ok(Verdict {
                mode,
                valide: self.liaisons == 0,
                liaisons_ouvertes,
                violations: Vec::new(),
                derogations: BTreeMap::new(),
                avertissements: BTreeMap::new(),
                reseaux_a_router: 3,
                reseaux_complets: 3 - self.liaisons.min(3) as u64,
                marge_isolement_min_mm: None,
                marge_isolement_min_paire: String::new(),
                mesures: validite::Mesures {
                    ecart_moyen_mm: 0.0,
                    ecart_min_mm: None,
                    coudes: 0,
                    coudes_90: 0,
                    angles_aigus: 0,
                    longueur_pistes_mm: 0.0,
                    resistance_puissance_mohm: 0.0,
                    vias: 0,
                },
            })
        }
    }

    #[test]
    fn verificateur_injecte_remplace_la_reference() {
        let d = essais::carte_complete();
        let p = essais::placement_cible(&d);
        let r = essais::routage_vide(&d);
        let s = Scenario { liaisons: 2, appels: AtomicUsize::new(0) };
        let q = Demande { donnees: &d, placements: &p, routage: &r, couture: None, mode: Mode::Altium, grille: false };
        let v = verdict_complet(&s, &q).unwrap();
        assert_eq!(s.appels.load(Ordering::SeqCst), 2);
        assert_eq!(v["format"], "verdict/2");
        assert_eq!(v["mode"], "altium");
        assert_eq!(v["valide"], false);
        assert_eq!(v["liaisons_ouvertes"], 2);
        assert_eq!(v["placement"]["valide"], false, "grille = false transmise au vérificateur");
    }

    #[test]
    fn reference_sur_la_carte_complete() {
        let d = essais::carte_complete();
        let p = essais::placement_cible(&d);
        let r = essais::routage_vide(&d);
        let q = Demande { donnees: &d, placements: &p, routage: &r, couture: None, mode: Mode::Strict, grille: true };
        let v = verdict_complet(&Reference, &q).unwrap();
        assert_eq!(v["format"], "verdict/2");
        assert_eq!(v["mode"], "strict");
        assert!(v["placement"].is_object(), "{}", v);
        assert!(v["violations_par_type"].is_object());
    }
}
