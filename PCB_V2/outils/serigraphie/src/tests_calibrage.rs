//! Tests de calibrage sur le jeu d'essai `altium_rev4/` (textes trait exportes de la carte d'exemple) et
//! chargement complet de ce jeu d'essai. Ces tests sont les seuls a citer des donnees d'une carte reelle.

use crate::donnees::*;
use crate::geom::*;

fn proche(a: f64, b: f64, e: f64) -> bool {
    (a - b).abs() <= e
}

#[test]
fn chargement_jeu_essai_rev4() {
    let dossier = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("altium_rev4");
    let d = charger(&dossier).expect("jeu d'essai lisible");
    assert_eq!((d.pastilles.len(), d.composants.len(), d.vias.len(), d.textes.len()), (1036, 359, 1008, 740));
    assert!(d.composants.iter().any(|c| c.face == Face::Dessous));
    let c = crate::lire_carte(&dossier.join("carte.json")).expect("carte.json lisible");
    assert_eq!((c.0.contour.len(), c.0.decoupes.len(), c.0.trous.len()), (4, 4, 5));
    assert_eq!(c.1.tailles_mm.len(), 3);
}

/// calibrage : Bounds Altium = encre gonflee de w (textes trait exportes de rev4)
#[test]
fn calibrage_bounds_altium() {
    // (texte, X, Y, h, w, rot, miroir, Bounds L, B, R, T)
    let cas = [
        ("P1", 3419.5616, 3517.8567, 37.6772, 5.6496, 180., false, 3363.692, 3474.5423, 3425.2112, 3523.5065),
        ("TRIG_AL_OUT1", 3410.8375, 420.0198, 26.1024, 3.9134, 0., false, 3406.9241, 411.7574, 3706.1339, 450.0272),
        ("OUT1", 339.9715, 420.0198, 26.1024, 3.9134, 0., false, 336.0581, 416.1064, 430.8649, 450.0272),
    ];
    for (t, x, y, h, w, r, m, l, b, rr, tt) in cas {
        let e = boite_encre(t, x, y, h, r, m);
        let g = [e[0] - w, e[1] - w, e[2] + w, e[3] + w];
        for (u, v) in g.iter().zip([l, b, rr, tt].iter()) {
            assert!(proche(*u, *v, 0.1), "{} {:?} vs {:?}", t, g, [l, b, rr, tt]);
        }
    }
}

#[test]
fn texte_miroir_relatif() {
    // h = 60, w = 10, « 330 » miroir a 0 deg : Bounds = [X - 169.95, Y - 10, X + 10, Y + 69.98]
    let e = boite_encre("330", 0., 0., 60., 0., true);
    assert!(proche(e[0], -160.0, 0.05) && proche(e[2], 0.0, 1e-9) && proche(e[1], 0.0, 1e-9) && proche(e[3], 60.0, 1e-9));
    // « C32_ALIM1 » miroir a 90 deg : Bounds rel. [-69.98, -499.84, 20, 10]
    let e = boite_encre("C32_ALIM1", 0., 0., 60., 90., true);
    assert!(proche(e[0] - 10., -69.98, 0.05) && proche(e[1] - 10., -499.84, 0.2) && proche(e[2] + 10., 20.0, 0.05));
    // ancre_pour_boite est l'inverse de boite_encre
    for &(r, m) in &[(0., false), (90., false), (0., true), (270., true), (90., true), (180., false)] {
        let (x, y) = ancre_pour_boite("R12_A", 100., 200., 39.37, r, m);
        let b = boite_encre("R12_A", x, y, 39.37, r, m);
        assert!(proche(b[0], 100., 1e-9) && proche(b[1], 200., 1e-9));
    }
}

#[test]
fn largeurs_police() {
    assert!(proche(largeur_encre("R101_ALIM1", 6.0), 53.0, 1e-9));
    assert!(proche(largeur_encre("SENSE2", 6.0), 34.0, 1e-9));
    assert!(proche(largeur_encre("100nF", 6.0), 27.0, 1e-9));
    assert!(proche(largeur_encre("0.11", 6.0), 18.0, 1e-9));
}

