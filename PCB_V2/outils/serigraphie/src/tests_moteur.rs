//! Tests de la ligne de commande, de carte.json et du determinisme du moteur.

use crate::*;

fn a(v: &[&str]) -> Vec<String> {
    v.iter().map(|x| x.to_string()).collect()
}

fn ecrire(nom: &str, texte: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("serigraphie_tests_{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    let f = d.join(nom);
    std::fs::write(&f, texte).unwrap();
    f
}

#[test]
fn options_obligatoires() {
    assert!(analyser(&a(&[])).unwrap_err().contains("--entree"));
    assert!(analyser(&a(&["--entree", "e", "--sortie", "s"])).unwrap_err().contains("--carte"));
    assert!(analyser(&a(&["--entree", "e", "--carte", "c"])).unwrap_err().contains("--sortie"));
    assert!(analyser(&a(&["--entree"])).is_err());
    assert!(analyser(&a(&["--entree", "e", "--sortie", "s", "--carte", "c", "--graine", "x"])).is_err());
    assert!(analyser(&a(&["--inconnue"])).is_err());
    let o = analyser(&a(&["--entree", "e", "--sortie", "s", "--carte", "c", "--threads", "2"])).unwrap();
    assert_eq!((o.threads, o.chaines, o.graine), (2, 16, 1));
    assert_eq!(nombre_fils(3), 3);
    assert!(nombre_fils(0) >= 1);
}

#[test]
fn carte_invalide() {
    assert!(lire_carte(Path::new("absent_introuvable.json")).unwrap_err().contains("absent_introuvable.json"));
    let f = ecrire("tronque.json", "{\"format\": \"carte_serigraphie/1\", \"contour\": [[0, 0], [1");
    let m = lire_carte(&f).unwrap_err();
    assert!(m.contains("JSON invalide") && !m.contains(std::path::MAIN_SEPARATOR), "{}", m);
    let f = ecrire("sans_regles.json", "{\"format\": \"carte_serigraphie/1\", \"contour\": [[0, 0], [100, 0], [0, 100]]}");
    assert!(lire_carte(&f).unwrap_err().contains("regles"));
    let f = ecrire("format.json", "{\"format\": \"carte/9\"}");
    assert!(lire_carte(&f).unwrap_err().contains("format"));
}

#[test]
fn carte_polygonale_et_regles() {
    let f = ecrire(
        "carte.json",
        "{\"format\": \"carte_serigraphie/1\", \"unites\": \"mil\", \
         \"contour\": [[0, 0], [1000, 0], [1000, 500], [500, 500], [500, 1000], [0, 1000]], \
         \"decoupes\": [[[100, 100], [200, 100], [150, 200]]], \"trous\": [[800, 100, 40]], \
         \"regles\": {\"garde_masque_mm\": 0.2, \"garde_bord_mm\": 0.5, \"tailles_mm\": [[1.2, 0.18]]}}",
    );
    let (c, r) = lire_carte(&f).unwrap();
    assert_eq!((c.contour.len(), c.decoupes.len(), c.trous.len()), (6, 1, 1));
    assert_eq!((r.garde_masque_mm, r.garde_trait_mm, r.tailles_mm.clone()), (0.2, 0.05, vec![(1.2, 0.18)]));
    let g = 0.5 / MIL_MM;
    assert!(c.accepte(&[300., 600., 400., 650.], g)); // bras gauche du L
    assert!(!c.accepte(&[600., 600., 700., 650.], g)); // dans l'angle retire
    assert!(!c.accepte(&[120., 120., 160., 150.], g)); // dans la decoupe
    assert!(!c.accepte(&[780., 80., 820., 120.], g)); // sur le trou
    assert!(!c.accepte(&[5., 300., 50., 320.], g)); // dans la garde du bord
}

/// meme sortie pour 2 et 4 fils (jeu d'essai, recuit court)
#[test]
fn determinisme_nombre_de_fils() {
    let dossier = Path::new(env!("CARGO_MANIFEST_DIR")).join("altium_rev4");
    if !dossier.exists() {
        eprintln!("saute : jeu d'essai prive altium_rev4 absent (non publie)");
        return;
    }
    let d = charger(&dossier).unwrap();
    let (c, r) = lire_carte(&dossier.join("carte.json")).unwrap();
    let mut o = analyser(&a(&["--entree", "e", "--sortie", "s", "--carte", "c", "--chaines", "3", "--mouvements", "20000"])).unwrap();
    let mut sorties = Vec::new();
    for n in [2usize, 4] {
        o.threads = n;
        let pool = rayon::ThreadPoolBuilder::new().num_threads(n).stack_size(PILE_FIL).build().unwrap();
        sorties.push(serde_json::to_string(&pool.install(|| calculer(&d, &c, &r, &o))).unwrap());
    }
    assert_eq!(sorties[0], sorties[1]);
}
