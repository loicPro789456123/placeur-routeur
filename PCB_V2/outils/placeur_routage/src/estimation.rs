//! Sous-commandes `estimer` (format `estimation/1`) et `etalonner` (format `etalonnage/1`), contrat §5.1.
//!
//! **Mémoire estimée : formule provisoire** (étape 2 du lot A, recalée à l'étape 9 sur les pics mesurés de METROID,
//! SL1 et WiFi ; critère 10 : entre 0,8 et 1,5 fois le pic). Elle suit les allocations du routeur détaillé de
//! `9b5b5a3` (`Detail::new`) :
//! - grille fine : `OCTETS_NOEUD` par nœud et par couche de routage (coûts, historique, parents, estampilles, cartes
//!   d'occupation…) plus `OCTETS_CELLULE` par cellule du plan (obstacles, zones, vias) ;
//! - la phase conjointe garde un clone de l'état du routeur détaillé : la grille fine compte deux fois ;
//! - routeur global : `OCTETS_GLOBAL` par cellule et par couche de routage, une instance par chaîne du recuit ;
//! - modèle : `OCTETS_PASTILLE` par pastille, plus un socle fixe `SOCLE_MO`.

use crate::donnees::{vers_monde, Donnees, Role};
use crate::parametres::Parametres;
use crate::regles;
use serde_json::{json, Value};

const OCTETS_NOEUD: f64 = 60.0;
const OCTETS_CELLULE: f64 = 6.0;
const COPIES_DETAIL: f64 = 2.0;
const OCTETS_GLOBAL: f64 = 48.0;
const OCTETS_PASTILLE: f64 = 512.0;
const SOCLE_MO: f64 = 16.0;
const MO: f64 = 1024.0 * 1024.0;

/// Estimation (`estimation/1`).
#[derive(Clone, Debug, PartialEq)]
pub struct Estimation {
    pub memoire_mo: f64,
    /// nœuds de la grille fine : cellules du plan × couches de routage
    pub cellules_detail: u64,
    pub pas_grille_mm: f64,
    pub couches_routage: usize,
    pub connexions: u64,
    pub pieces_mobiles: usize,
    pub essais_conjointe: u64,
    pub avertissements: Vec<String>,
}

impl Estimation {
    pub fn en_json(&self) -> Value {
        json!({
            "format": "estimation/1",
            "memoire_mo": self.memoire_mo,
            "cellules_detail": self.cellules_detail,
            "pas_grille_mm": self.pas_grille_mm,
            "couches_routage": self.couches_routage,
            "connexions": self.connexions,
            "pieces_mobiles": self.pieces_mobiles,
            "essais_conjointe": self.essais_conjointe,
        })
    }
}

/// Étendue d'une grille de pas `pas` (décision D6) : boîte du contour élargie d'un nombre entier de cellules de
/// chaque côté jusqu'à couvrir toutes les pastilles à leur pose d'entrée ; rend (nx, ny), en flottants (aucun
/// débordement d'entier, même sur des données extrêmes).
pub fn etendue_grille(d: &Donnees, pas: f64) -> (f64, f64) {
    let b = d.carte.boite;
    let mut e = b;
    for c in &d.composants {
        for p in &c.pastilles {
            let [x, y] = vers_monde(&c.pose_cible, p.lx, p.ly);
            let r = p.hw.hypot(p.hh);
            e = [e[0].min(x - r), e[1].min(y - r), e[2].max(x + r), e[3].max(y + r)];
        }
    }
    let cellules = |debord: f64| if debord > 0.0 { (debord / pas).ceil() } else { 0.0 };
    let nx = ((b[2] - b[0]) / pas).ceil() + cellules(b[0] - e[0]) + cellules(e[2] - b[2]);
    let ny = ((b[3] - b[1]) / pas).ceil() + cellules(b[1] - e[1]) + cellules(e[3] - b[3]);
    (nx.max(1.0), ny.max(1.0))
}

/// Connexions (décision D21) : somme, sur les réseaux à router hors plans, de (pastilles − 1), plus les pastilles
/// CMS des réseaux de plan.
pub fn connexions(d: &Donnees) -> u64 {
    let mut par_reseau = vec![0u64; d.reseaux.len()];
    let mut cms_plan = 0u64;
    for c in &d.composants {
        for p in &c.pastilles {
            if let Some(&i) = d.index_reseaux.get(&p.reseau) {
                par_reseau[i] += 1;
                if d.reseaux[i].role == Role::Plan && !p.traversante {
                    cms_plan += 1;
                }
            }
        }
    }
    let signaux: u64 =
        d.reseaux.iter().zip(&par_reseau).filter(|(r, _)| r.role != Role::Plan).map(|(_, &n)| n.saturating_sub(1)).sum();
    signaux + cms_plan
}

/// Essais de la phase conjointe : `conjointe.essais`, ou ⌊`essais_par_piece_mobile` × pièces mobiles + 0,5⌋ (§2.6).
pub fn essais_conjointe(p: &Parametres, pieces_mobiles: usize) -> u64 {
    match p.conjointe.essais {
        Some(n) => n,
        None => (p.conjointe.essais_par_piece_mobile * pieces_mobiles as f64 + 0.5).floor() as u64,
    }
}

/// Estimation ; `Err` (code 2) si le pas de grille « auto » est incohérent.
pub fn estimer(d: &Donnees, p: &Parametres) -> Result<Estimation, String> {
    let pas = regles::pas_grille_detail(d, &p.routage.pas_grille_detail_mm)?;
    let couches = d.couches_routage().len();
    let (nx, ny) = etendue_grille(d, pas.pas_mm);
    let plan = nx * ny;
    let noeuds = plan * couches as f64;
    let (gx, gy) = etendue_grille(d, p.recuit.pas_grille_globale_mm);
    let pastilles: usize = d.composants.iter().map(|c| c.pastilles.len()).sum();
    let octets = COPIES_DETAIL * (noeuds * OCTETS_NOEUD + plan * OCTETS_CELLULE)
        + gx * gy * couches as f64 * OCTETS_GLOBAL * p.campagne.chaines.max(1) as f64
        + pastilles as f64 * OCTETS_PASTILLE;
    let pieces_mobiles = d.composants.iter().filter(|c| c.mobile && !c.virtuel).count();
    Ok(Estimation {
        memoire_mo: ((octets / MO + SOCLE_MO) * 10.0).round() / 10.0,
        cellules_detail: noeuds as u64,
        pas_grille_mm: pas.pas_mm,
        couches_routage: couches,
        connexions: connexions(d),
        pieces_mobiles,
        essais_conjointe: essais_conjointe(p, pieces_mobiles),
        avertissements: pas.avertissements,
    })
}

/// Contrôle de `campagne.memoire_max_mo` (0 = aucune limite) pour `campagne` et `router` : `Err` ⇒ code 2.
pub fn controler_memoire(e: &Estimation, p: &Parametres) -> Result<(), String> {
    let max = p.campagne.memoire_max_mo;
    if max > 0.0 && e.memoire_mo > max {
        return Err(format!(
            "mémoire estimée {} Mo au-delà de campagne.memoire_max_mo ({} Mo) : augmenter la limite, ou réduire \
             campagne.chaines ou la finesse de la grille (routage.pas_grille_detail_mm)",
            e.memoire_mo, max
        ));
    }
    Ok(())
}

// =================================================================================================================
// Étalonnage (décision D24) : A* fixe sur une grille synthétique, travail déterministe
// =================================================================================================================

/// Grille de l'étalonnage : côté (cellules), couches, part d'obstacles (sur 1000), graine du générateur.
const ETAL_COTE: usize = 320;
const ETAL_COUCHES: usize = 2;
const ETAL_OBSTACLES_POUR_MILLE: u64 = 280;
const ETAL_GRAINE: u64 = 0x0E7A_105A_6E00_0001;
/// Nombre de recherches de l'étalonnage complet (environ 10 s sur le PC de développement, un fil).
pub const ETAL_RECHERCHES: usize = 13_000;
/// Coûts d'un pas droit, d'un pas diagonal et d'un changement de couche.
const ETAL_DROIT: u32 = 10;
const ETAL_DIAG: u32 = 14;
const ETAL_VIA: u32 = 60;

/// Générateur xorshift64* (déterministe, sans dépendance).
struct Alea(u64);

impl Alea {
    fn suivant(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    fn sous(&mut self, n: u64) -> u64 {
        self.suivant() % n
    }
}

/// Résultat d'un travail d'étalonnage : nœuds développés et somme de contrôle (coûts des chemins trouvés).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Travail {
    pub expansions: u64,
    pub controle: u64,
}

/// Exécute `recherches` recherches A* entre des paires de cellules libres tirées d'un générateur à graine fixe.
pub fn travail_etalonnage(recherches: usize) -> Travail {
    use std::cmp::Reverse;
    use std::collections::BinaryHeap;
    let n = ETAL_COTE;
    let plan = n * n;
    let total = plan * ETAL_COUCHES;
    let mut alea = Alea(ETAL_GRAINE);
    let libre: Vec<bool> = (0..total).map(|_| alea.sous(1000) >= ETAL_OBSTACLES_POUR_MILLE).collect();
    let mut g = vec![u32::MAX; total];
    let mut estampe = vec![0u32; total];
    let mut ferme = vec![0u32; total];
    let mut tas: BinaryHeap<Reverse<(u32, u32, u32)>> = BinaryHeap::new();
    let mut t = Travail { expansions: 0, controle: 0 };
    let tirer = |alea: &mut Alea| loop {
        let k = alea.sous(total as u64) as usize;
        if libre[k] {
            return k;
        }
    };
    let h = |a: usize, b: usize| -> u32 {
        let (ax, ay, az) = ((a % plan) % n, (a % plan) / n, a / plan);
        let (bx, by, bz) = ((b % plan) % n, (b % plan) / n, b / plan);
        let (dx, dy) = (ax.abs_diff(bx) as u32, ay.abs_diff(by) as u32);
        let (lo, hi) = (dx.min(dy), dx.max(dy));
        lo * ETAL_DIAG + (hi - lo) * ETAL_DROIT + if az != bz { ETAL_VIA } else { 0 }
    };
    const VOISINS: [(i32, i32, u32); 8] = [
        (1, 0, ETAL_DROIT),
        (-1, 0, ETAL_DROIT),
        (0, 1, ETAL_DROIT),
        (0, -1, ETAL_DROIT),
        (1, 1, ETAL_DIAG),
        (1, -1, ETAL_DIAG),
        (-1, 1, ETAL_DIAG),
        (-1, -1, ETAL_DIAG),
    ];
    for r in 0..recherches {
        let ep = r as u32 + 1;
        let (s, cible) = (tirer(&mut alea), tirer(&mut alea));
        tas.clear();
        g[s] = 0;
        estampe[s] = ep;
        tas.push(Reverse((h(s, cible), 0, s as u32)));
        while let Some(Reverse((_, gs, u))) = tas.pop() {
            let u = u as usize;
            if ferme[u] == ep || gs > g[u] {
                continue;
            }
            ferme[u] = ep;
            t.expansions += 1;
            if u == cible {
                t.controle = t.controle.wrapping_mul(31).wrapping_add(gs as u64);
                break;
            }
            let (z, x, y) = (u / plan, ((u % plan) % n) as i32, ((u % plan) / n) as i32);
            let mut essayer = |v: usize, c: u32| {
                if !libre[v] || ferme[v] == ep {
                    return;
                }
                let gv = gs + c;
                if estampe[v] != ep || gv < g[v] {
                    estampe[v] = ep;
                    g[v] = gv;
                    tas.push(Reverse((gv + h(v, cible), gv, v as u32)));
                }
            };
            for (dx, dy, c) in VOISINS {
                let (vx, vy) = (x + dx, y + dy);
                if vx >= 0 && vy >= 0 && (vx as usize) < n && (vy as usize) < n {
                    essayer(z * plan + vy as usize * n + vx as usize, c);
                }
            }
            for z2 in 0..ETAL_COUCHES {
                if z2 != z {
                    essayer(z2 * plan + (u % plan), ETAL_VIA);
                }
            }
        }
    }
    t
}

/// Étalonnage complet : score (expansions par seconde), travail et durée.
pub fn etalonner() -> (f64, Travail, f64) {
    let debut = std::time::Instant::now();
    let t = travail_etalonnage(ETAL_RECHERCHES);
    let s = debut.elapsed().as_secs_f64().max(1e-9);
    ((t.expansions as f64 / s).round(), t, s)
}
