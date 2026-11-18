//! Finition exacte (avenant 2.2, A.4-5) : point d'appel place entre la finition et la verification finale.
//!
//! En vague 1, `appliquer` rend la geometrie inchangee : le point d'appel existe pour que la vague 2 y branche
//! la finition exacte sans toucher au protocole de campagne. Le compteur d'appels sert aux tests (« appelee et
//! sans effet »).

use crate::detail::NetR;
use std::sync::atomic::{AtomicU64, Ordering};

static APPELS: AtomicU64 = AtomicU64::new(0);

/// Applique la finition exacte aux reseaux routes. Vague 1 : aucun changement ; renvoie le nombre de reseaux
/// modifies (toujours 0).
pub fn appliquer(reseaux: &mut [NetR]) -> usize {
    APPELS.fetch_add(1, Ordering::SeqCst);
    let _ = reseaux;
    0
}

/// Nombre d'appels depuis le debut du processus (tests).
pub fn appels() -> u64 {
    APPELS.load(Ordering::SeqCst)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn appliquer_appelee_et_sans_effet() {
        let mut v: Vec<NetR> = Vec::new();
        let avant = appels();
        assert_eq!(appliquer(&mut v), 0);
        assert!(v.is_empty());
        assert!(appels() > avant);
    }

    /// Reproductibilite (§0.2-6) : FMA3 desactive dans la construction livree (aucune contraction des
    /// multiplications-additions, memes resultats sur toute machine x86-64).
    #[test]
    fn fma3_desactive() {
        assert!(!cfg!(target_feature = "fma"), "FMA3 active : resultats flottants dependants de la machine");
        // contraction absente : a*b+c arrondi en deux temps
        let (a, b, c) = (0.1f64, 10.0f64, -1.0f64);
        let separe = std::hint::black_box(a) * std::hint::black_box(b) + std::hint::black_box(c);
        assert_eq!(separe.to_bits(), (a * b + c).to_bits());
    }
}
