//! Caisse d'essai du lot E : expose `src/validite.rs` (le module de validité du moteur) comme module public d'une
//! bibliothèque, pour les tests de `tests/`. Le binaire `validite_verifier` (`verifier.rs`) inclut le même fichier en
//! module privé, comme le moteur du lot A.

#[path = "../../src/validite.rs"]
pub mod validite;
