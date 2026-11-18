//! Dimensions du routage dérivées des règles de la carte (contrat §2.6, table « Grandeur / Règle générique »).
//!
//! Étape 2 du lot A : pas de la grille fine (`routage.pas_grille_detail_mm`, valeur `"auto"`). Les autres
//! grandeurs de la table (demi-largeur de référence, gonflements, entraxe des vias, éventails…) rejoignent ce module
//! à l'étape 6.

use crate::donnees::{court, Donnees};
use crate::parametres::AutoOuNombre;

/// Plafond du pas « auto » (mm) : pas de la grille fine de `9b5b5a3`.
pub const PAS_AUTO_MAX_MM: f64 = 0.1;
/// Diviseur de la largeur minimale des réseaux dans la règle « auto ».
const DIVISEUR_LARGEUR: f64 = 2.0;
/// Diviseur du plus petit pas des pièces à pas fin dans la règle « auto ».
const DIVISEUR_PAS_FIN: f64 = 5.0;
/// Nombre minimal de pas de grille entre deux pastilles voisines d'une pièce (en deçà : avertissement).
const PAS_PAR_PASTILLE_MIN: f64 = 4.0;
/// Plus petit pas accepté (mm) : en deçà, les largeurs ou les pas des pastilles des données sont incohérents.
pub const PAS_MIN_MM: f64 = 0.001;
/// Nombre maximal de pièces citées dans l'avertissement.
const PIECES_CITEES_MAX: usize = 10;

/// Pas de grille retenu et avertissements.
#[derive(Clone, Debug, PartialEq)]
pub struct PasGrille {
    pub pas_mm: f64,
    /// `true` si le pas vient de la règle « auto »
    pub auto: bool,
    pub avertissements: Vec<String>,
}

/// Arrondi au micromètre (un pas de 0,4 / 5 s'écrit 0,08 et non 0,080000000000000002).
fn au_micron(x: f64) -> f64 {
    (x * 1e6).round() / 1e6
}

/// Règle « auto » : min(0,1 ; largeur minimale des réseaux / 2 ; plus petit `pas_min_mm` des pièces à pas fin / 5).
pub fn pas_auto(d: &Donnees) -> f64 {
    let mut p = PAS_AUTO_MAX_MM;
    for r in &d.reseaux {
        p = p.min(r.largeur_min / DIVISEUR_LARGEUR);
    }
    for c in d.composants.iter().filter(|c| c.pas_fin) {
        if let Some(m) = c.pas_min_mm {
            p = p.min(m / DIVISEUR_PAS_FIN);
        }
    }
    au_micron(p)
}

/// Pas de la grille fine : valeur donnée, ou règle « auto » ; avertissement si le pas d'une pièce est inférieur à
/// 4 pas de grille. `Err` (code 2) si la règle « auto » donne moins de `PAS_MIN_MM`.
pub fn pas_grille_detail(d: &Donnees, demande: &AutoOuNombre) -> Result<PasGrille, String> {
    let (pas_mm, auto) = match demande.valeur() {
        Some(x) => (x, false),
        None => (pas_auto(d), true),
    };
    if pas_mm < PAS_MIN_MM {
        return Err(format!(
            "routage.pas_grille_detail_mm : la règle « auto » donne {} mm, moins que {} mm (largeur minimale d'un réseau \
             ou pas des pastilles d'une pièce à pas fin incohérent dans les données)",
            pas_mm, PAS_MIN_MM
        ));
    }
    let seuil = PAS_PAR_PASTILLE_MIN * pas_mm;
    let serrees: Vec<String> = d
        .composants
        .iter()
        .filter(|c| c.pas_min_mm.is_some_and(|m| m < seuil - 1e-9))
        .map(|c| format!("{} ({} mm)", court(&c.nom), c.pas_min_mm.unwrap_or(0.0)))
        .collect();
    let mut avertissements = Vec::new();
    if !serrees.is_empty() {
        let mut liste = serrees.iter().take(PIECES_CITEES_MAX).cloned().collect::<Vec<_>>().join(", ");
        if serrees.len() > PIECES_CITEES_MAX {
            liste = format!("{}, … ({} pièces en tout)", liste, serrees.len());
        }
        avertissements.push(format!(
            "pas de grille fine {} mm : pastilles plus serrées que {} pas de grille ({} mm) sur {}",
            pas_mm, PAS_PAR_PASTILLE_MIN, au_micron(seuil), liste
        ));
    }
    Ok(PasGrille { pas_mm, auto, avertissements })
}

/// Tolérance des comparaisons de largeurs (mm), contrat §2.6.
const EPS_LARGEUR: f64 = 1e-9;
/// Rapport en deçà duquel le dernier palier est trop loin du minimum (étape 3 de l'échelle).
const RAPPORT_DERNIER_PALIER: f64 = 0.9;
/// Largeur de repli des descentes, des ponts et des liaisons des vias doublés, bornée au réseau (§2.6).
pub const LARGEUR_REPLI_MM: f64 = 0.3;
/// Marge ajoutée à l'entraxe minimal entre vias (§2.6).
const MARGE_ENTRAXE_VIAS_MM: f64 = 0.05;
/// Plancher du décalage d'un via doublé (§2.6).
const DECALAGE_VIA_DOUBLE_MIN_MM: f64 = 0.8;
/// Marge du disque sans via autour d'une traversante (§2.6).
const MARGE_DISQUE_TRAVERSANTE_MM: f64 = 0.01;

/// Largeurs [min, pref, max] d'un réseau ; `max` = `None` : aucun maximum.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Largeurs {
    pub min: f64,
    pub pref: f64,
    pub max: Option<f64>,
}

impl Largeurs {
    /// clamp(v) = min(max(v, min), max).
    pub fn borner(&self, v: f64) -> f64 {
        let v = v.max(self.min);
        match self.max {
            Some(m) => v.min(m),
            None => v,
        }
    }
}

/// Échelle des largeurs d'un réseau (§2.6, DOIT), dans l'ordre d'essai.
///
/// `preferee_role` : `largeur_preferee_mm` du rôle ; `pastille_puissance` : plus grand côté de la plus grande pastille
/// du réseau appartenant à `haut`, `bas` ou `pilote` d'un convertisseur, utilisé si `largeur_preferee_pastille`.
pub fn echelle_largeurs(
    l: &Largeurs,
    preferee_role: Option<f64>,
    preferee_pastille: bool,
    pastille_puissance: Option<f64>,
    paliers: &[f64],
    plancher: f64,
) -> Vec<f64> {
    let p = match preferee_role {
        Some(p) => match (preferee_pastille, pastille_puissance) {
            (true, Some(c)) => p.min(c),
            _ => p,
        },
        None => l.min.max(l.pref),
    };
    let pref = l.borner(p);
    let bas = l.min.max(plancher) - EPS_LARGEUR;
    let mut e = vec![pref];
    e.extend(paliers.iter().copied().filter(|&q| bas <= q && q < pref - EPS_LARGEUR));
    let d = *e.last().unwrap();
    let seuil = RAPPORT_DERNIER_PALIER * d - EPS_LARGEUR;
    if l.min < seuil {
        let m = (pref + l.min) / 2.0;
        if m < seuil {
            e.push(m);
        }
        e.push(l.min);
    }
    e
}

/// Descentes d'un réseau de plan (§2.6) : liste du rôle filtrée à [min, max] ; vide ⇒ [clamp(0,3)].
pub fn descentes_filtrees(l: &Largeurs, descentes: &[f64], descente_puissance: &[f64], large: bool) -> Vec<f64> {
    let src = if large { descente_puissance } else { descentes };
    let f: Vec<f64> = src
        .iter()
        .copied()
        .filter(|&q| q >= l.min - EPS_LARGEUR && l.max.map_or(true, |m| q <= m + EPS_LARGEUR))
        .collect();
    if f.is_empty() {
        vec![l.borner(LARGEUR_REPLI_MM)]
    } else {
        f
    }
}

/// Largeur des ponts entre pastilles et des liaisons des vias doublés : clamp(0,3) du réseau.
pub fn largeur_pont(l: &Largeurs) -> f64 {
    l.borner(LARGEUR_REPLI_MM)
}

/// Demi-largeur de référence : plus petite demi-largeur des échelles de la carte.
pub fn demi_largeur_reference(echelles: &[Vec<f64>]) -> f64 {
    echelles.iter().flatten().fold(f64::INFINITY, |a, &b| a.min(b)) / 2.0
}

/// Entraxe minimal entre vias : trou du plus gros des deux vias + `trou_trou_mm` + 0,05.
pub fn entraxe_vias(trou_a: f64, trou_b: f64, trou_trou: f64) -> f64 {
    trou_a.max(trou_b) + trou_trou + MARGE_ENTRAXE_VIAS_MM
}

/// Décalage d'un via doublé : max(0,8 ; entraxe minimal).
pub fn decalage_via_double(entraxe: f64) -> f64 {
    DECALAGE_VIA_DOUBLE_MIN_MM.max(entraxe)
}

/// Rayon du disque sans via autour d'une traversante : trou/2 + trou du via/2 + `trou_trou_mm` + 0,01.
pub fn disque_sans_via(trou: f64, trou_via: f64, trou_trou: f64) -> f64 {
    trou / 2.0 + trou_via / 2.0 + trou_trou + MARGE_DISQUE_TRAVERSANTE_MM
}

/// Garde du routeur global : `garde_bord_mm` + plus petite demi-largeur.
pub fn garde_routeur_global(garde_bord: f64, demi_largeur: f64) -> f64 {
    garde_bord + demi_largeur
}

/// Gonflement des pastilles du routeur global : isolement du réseau + plus petite demi-largeur.
pub fn gonflement_global(isolement: f64, demi_largeur: f64) -> f64 {
    isolement + demi_largeur
}

/// Emprise d'un via du routeur global : diamètre du via du réseau + 2 × isolement du réseau.
pub fn emprise_via_globale(diametre_via: f64, isolement: f64) -> f64 {
    diametre_via + 2.0 * isolement
}

/// Pas de sortie d'éventail « auto » : largeur minimale des réseaux de la pièce + 2 × plus grand isolement de ses
/// réseaux + 2 × pas de grille.
pub fn pas_sortie_eventail(largeur_min: f64, isolement_max: f64, pas_grille: f64) -> f64 {
    largeur_min + 2.0 * isolement_max + 2.0 * pas_grille
}

#[cfg(test)]
mod tests_derivees {
    use super::*;

    const PALIERS_SIGNAL: [f64; 2] = [0.3, 0.254];
    const PALIERS_PUISS: [f64; 4] = [1.5, 1.0, 0.5, 0.3];
    const PALIERS_COMMUT: [f64; 5] = [1.5, 1.0, 0.5, 0.3, 0.254];
    const PLANCHER: f64 = 0.254;

    fn l(min: f64, pref: f64, max: f64) -> Largeurs {
        Largeurs { min, pref, max: Some(max) }
    }

    fn sig(x: Largeurs) -> Vec<f64> {
        echelle_largeurs(&x, None, false, None, &PALIERS_SIGNAL, PLANCHER)
    }

    fn proche(a: &[f64], b: &[f64]) {
        assert_eq!(a.len(), b.len(), "{a:?} contre {b:?}");
        for (x, y) in a.iter().zip(b) {
            assert!((x - y).abs() < 1e-9, "{a:?} contre {b:?}");
        }
    }

    #[test]
    fn six_cas_de_la_carte_de_reference_par_leurs_valeurs() {
        let p = |x| echelle_largeurs(&x, None, false, None, &PALIERS_PUISS, PLANCHER);
        proche(&p(l(0.3, 2.4892, 15.0)), &[2.4892, 1.5, 1.0, 0.5, 0.3]);
        proche(&p(l(0.3, 2.0066, 15.0)), &[2.0066, 1.5, 1.0, 0.5, 0.3]);
        proche(&p(l(0.5, 1.5, 15.0)), &[1.5, 1.0, 0.5]);
        let c = echelle_largeurs(&l(0.254, 0.3, 3.0), Some(2.0), true, Some(2.3), &PALIERS_COMMUT, PLANCHER);
        proche(&c, &[2.0, 1.5, 1.0, 0.5, 0.3, 0.254]);
        proche(&sig(l(0.254, 0.3, 3.0)), &[0.3, 0.254]);
        proche(&sig(l(0.3, 0.3048, 3.0)), &[0.3048, 0.3]);
    }

    #[test]
    fn cas_generiques() {
        proche(&sig(l(0.254, 0.254, 0.254)), &[0.254]);
        let p = echelle_largeurs(&l(0.254, 0.254, 0.254), None, false, None, &PALIERS_PUISS, PLANCHER);
        proche(&p, &[0.254]);
        proche(&sig(l(0.4, 0.5, 3.0)), &[0.5, 0.4]);
        proche(&sig(l(0.1, 0.3, 1.0)), &[0.3, 0.254, 0.2, 0.1]);
        let sw = echelle_largeurs(&l(0.254, 0.3, 3.0), Some(2.0), true, Some(0.6), &PALIERS_COMMUT, PLANCHER);
        assert!((sw[0] - 0.6).abs() < 1e-12, "{sw:?}");
    }

    #[test]
    fn descentes_filtrees_et_repli() {
        let d = [0.3, 0.254];
        let dp = [0.5, 0.3];
        proche(&descentes_filtrees(&l(0.254, 0.3, 3.0), &d, &dp, false), &[0.3, 0.254]);
        proche(&descentes_filtrees(&l(0.254, 0.3, 3.0), &d, &dp, true), &[0.5, 0.3]);
        proche(&descentes_filtrees(&l(0.3, 0.3, 3.0), &d, &dp, false), &[0.3]);
        // aucune descente dans [0,6 ; 3] : repli sur clamp(0,3) = 0,6
        proche(&descentes_filtrees(&l(0.6, 1.0, 3.0), &d, &dp, false), &[0.6]);
        proche(&descentes_filtrees(&l(0.1, 0.2, 0.2), &d, &dp, false), &[0.2]);
        assert!((largeur_pont(&l(0.1, 0.15, 0.2)) - 0.2).abs() < 1e-12);
    }

    #[test]
    fn dimensions_derivees() {
        assert!((entraxe_vias(0.6, 0.3, 0.254) - (0.6 + 0.254 + 0.05)).abs() < 1e-12);
        let e = entraxe_vias(0.3, 0.3, 0.254);
        assert!((e - 0.604).abs() < 1e-12);
        assert!((decalage_via_double(e) - 0.8).abs() < 1e-12);
        assert!((decalage_via_double(1.2) - 1.2).abs() < 1e-12);
        let h = demi_largeur_reference(&[vec![0.3, 0.254], vec![2.0, 1.5]]);
        assert!((h - 0.127).abs() < 1e-12);
        assert!((pas_sortie_eventail(0.254, 0.25, 0.1) - 0.954).abs() < 1e-12);
        assert!((garde_routeur_global(0.6, h) - 0.727).abs() < 1e-12);
        assert!((gonflement_global(0.2, h) - 0.327).abs() < 1e-12);
        assert!((emprise_via_globale(0.6, 0.2) - 1.0).abs() < 1e-12);
        assert!((disque_sans_via(1.0, 0.3, 0.254) - (0.5 + 0.15 + 0.254 + 0.01)).abs() < 1e-12);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{donnees, essais};
    use serde_json::{json, Value};

    fn pas(v: &Value, demande: &AutoOuNombre) -> PasGrille {
        pas_grille_detail(&donnees::lire(v).unwrap(), demande).unwrap()
    }

    fn auto() -> AutoOuNombre {
        AutoOuNombre::Texte("auto".to_string())
    }

    fn largeur_min(v: &mut Value, l: f64) {
        for r in v["reseaux"].as_array_mut().unwrap() {
            r["largeurs_mm"][0] = json!(l);
        }
    }

    #[test]
    fn pas_auto_des_regles() {
        // règles du type de la carte de référence : largeur minimale 0,254 mm, pas fin de 0,5 mm ⇒ 0,1
        let mut v = essais::carte_complete();
        largeur_min(&mut v, 0.254);
        assert_eq!(pas(&v, &auto()), PasGrille { pas_mm: 0.1, auto: true, avertissements: vec![] });
        // une largeur minimale de 0,1 mm ⇒ 0,05
        v["reseaux"][4]["largeurs_mm"][0] = json!(0.1);
        assert_eq!(pas(&v, &auto()).pas_mm, 0.05);
        // pas fin de 0,4 mm ⇒ 0,08
        let mut v = essais::carte_complete();
        largeur_min(&mut v, 0.254);
        v["composants"][0]["pas_min_mm"] = json!(0.4);
        let p = pas(&v, &auto());
        assert_eq!(p.pas_mm, 0.08);
        assert!(p.avertissements.is_empty(), "{:?}", p.avertissements);
        // valeur donnée : gardée telle quelle
        assert_eq!(pas(&v, &AutoOuNombre::Nombre(0.05)), PasGrille { pas_mm: 0.05, auto: false, avertissements: vec![] });
    }

    #[test]
    fn avertissement_si_pastilles_plus_serrees_que_quatre_pas() {
        // pièce non marquée « pas fin » dont les pastilles sont à 0,3 mm : le pas « auto » reste 0,1 ⇒ avertissement
        let mut v = essais::carte_complete();
        v["composants"][0]["pas_fin"] = json!(false);
        v["composants"][0]["pas_min_mm"] = json!(0.3);
        let p = pas(&v, &auto());
        assert_eq!(p.pas_mm, 0.1);
        assert_eq!(p.avertissements.len(), 1);
        assert!(p.avertissements[0].contains("U1 (0.3 mm)") && p.avertissements[0].contains("4 pas de grille (0.4 mm)"), "{:?}", p);
        // pas donné trop gros pour une pièce à pas fin
        let v = essais::carte_complete();
        let p = pas(&v, &AutoOuNombre::Nombre(0.2));
        assert!(p.avertissements[0].contains("U1 (0.5 mm)"), "{:?}", p);
        // règle « auto » incohérente ⇒ erreur
        let mut v = essais::carte_complete();
        v["reseaux"][2]["largeurs_mm"][0] = json!(1e-6);
        assert!(pas_grille_detail(&donnees::lire(&v).unwrap(), &auto()).unwrap_err().contains("moins que 0.001 mm"));
    }
}
