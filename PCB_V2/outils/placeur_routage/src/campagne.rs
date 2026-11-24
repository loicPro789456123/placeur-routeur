//! Deroulement d'une campagne depuis zero pour une graine : placement aleatoire legal, recuit global, routage detaille,
//! boucle placement <-> routage detaille, phase conjointe, finition.
//!
//! Code de `9b5b5a3` (`run_campagne`, `zones_echecs`, `mesurer`) : les options et les variables d'environnement d'alors
//! sont remplacees par les reglages de `parametres_moteur/1`, portes par le modele (`Modele::param`). La campagne rend
//! un bilan (`Bilan`) ; les fichiers de sortie (`placements/2`, `routage/2`, `stats/2`) et la verification finale sont
//! ecrits par `moteur.rs`.
use crate::conjoint;
use crate::detail;
use crate::geom::*;
use crate::globalr::*;
use crate::parametres::{ArretApres, Parametres};
use crate::recuit;
use rayon::prelude::*;
use serde_json::{json, Value};
use std::time::Instant;

/// Reparation regionale (lot F) : nombre maximal d'essais et de reseaux arraches par essai.
const REPARATION_ETAPES: usize = 24;
const REPARATION_VICTIMES: usize = 12;

/// Iterations PathFinder du routeur global pour la mesure d'un candidat de la boucle.
const ITERS_MESURE: usize = 12;

/// Mesure d'un placement par le routeur global.
pub struct Ligne {
    pub ms: Mesure,
    pub hpwl: f64,
    pub extra: f64,
    pub decaps_loin: usize,
    pub temps: f64,
}

pub fn mesurer<'a>(m: &'a Modele, e: &Etat, pas: f64) -> (Ligne, Routeur<'a>) {
    let t = Instant::now();
    let mut rt = Routeur::new(m, e, pas);
    rt.route_tout(e, ITERS_MESURE);
    let ms = rt.mesure();
    (Ligne { ms, hpwl: m.hpwl(e), extra: m.cout_extra(e), decaps_loin: m.decaps_loin(e), temps: t.elapsed().as_secs_f64() }, rt)
}

/// Zones de pression tirees des echecs d'un routage detaille (une par broche en echec) : (x, y, rayon, poids, reseau
/// exempte).
pub fn zones_echecs(d: &detail::Detail, m: &Modele, e: &Etat, rayon: f64, w: f64) -> Vec<(f64, f64, f64, f64, Option<usize>)> {
    let mut z = Vec::new();
    for (n, r) in d.nets.iter().enumerate() {
        for dg in &r.diag {
            let nom = dg.split(" : ").next().unwrap_or("");
            let plan = dg.contains("via de plan");
            let pos = if nom.starts_with("cuivre du bloc") {
                m.nets[n].attentes.first().map(|a| (a.x, a.y))
            } else if let Some(p) = nom.rfind('.') {
                let (c, k) = (&nom[..p], &nom[p + 1..]);
                m.comp_idx.get(c).and_then(|&i| m.comps[i].pads.iter().position(|q| q.nom == k).map(|k| {
                    let (x, y, _, _) = m.pad_world(i, k, &e.poses[i]);
                    (x, y)
                }))
            } else {
                None
            };
            if let Some((x, y)) = pos {
                z.push((x, y, rayon, w, if plan { None } else { Some(n) }));
            }
        }
    }
    z
}

/// Parametres de la passe d'espacement (`routage.espacement`).
pub fn param_esp(p: &Parametres) -> detail::ParamEsp {
    let e = &p.routage.espacement;
    detail::ParamEsp { actif: e.actif, prox: e.prox, coude: e.coude, allong_max: e.allongement_max }
}

/// Raison de la fin d'une campagne (`stats.fin`).
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Fin {
    Normale,
    ArretApres,
    Abandon,
    PlafondTemps,
    ArretDemande,
}

impl Fin {
    pub fn nom(self) -> &'static str {
        match self {
            Fin::Normale => "normale",
            Fin::ArretApres => "arret_apres",
            Fin::Abandon => "abandon",
            Fin::PlafondTemps => "plafond_temps",
            Fin::ArretDemande => "arret_demande",
        }
    }
}

/// Fichier d'arret de l'execution (`--arret`), vu aussi a l'interieur des phases longues (recuit, negociation,
/// phase conjointe) : sans lui, un arret demande attendait la fin de la phase (recuit long : plusieurs minutes).
static FICHIER_ARRET: std::sync::OnceLock<Option<std::path::PathBuf>> = std::sync::OnceLock::new();

pub fn fixer_fichier_arret(f: Option<std::path::PathBuf>) {
    let _ = FICHIER_ARRET.set(f);
}

/// Arret demande (fichier `--arret` present).
pub fn arret_global() -> bool {
    FICHIER_ARRET.get().and_then(|f| f.as_ref()).map(|f| f.exists()).unwrap_or(false)
}

/// Contexte d'execution : journal, demande d'arret, evenements, chronometre.
pub struct Contexte<'c> {
    pub journal: &'c mut dyn FnMut(String),
    pub arret: &'c dyn Fn() -> bool,
    pub evenements: bool,
    pub t0: Instant,
    /// seuil d'abandon apres la boucle (connexions en echec)
    pub seuil_abandon: usize,
    /// durees par phase (cles de `stats.execution.temps_phases_s`)
    pub phases: Vec<(&'static str, f64)>,
    t_phase: f64,
}

impl<'c> Contexte<'c> {
    pub fn new(journal: &'c mut dyn FnMut(String), arret: &'c dyn Fn() -> bool, evenements: bool, t0: Instant, seuil_abandon: usize) -> Self {
        Contexte { journal, arret, evenements, t0, seuil_abandon, phases: Vec::new(), t_phase: 0.0 }
    }

    pub fn log(&mut self, s: String) {
        (self.journal)(s);
    }

    /// Debut d'une phase (evenement `phase`).
    pub fn debut(&mut self, nom: &str) {
        self.t_phase = self.t0.elapsed().as_secs_f64();
        if self.evenements {
            println!("EVT {}", json!({"type": "phase", "phase": nom, "etat": "debut"}));
        }
    }

    /// Fin d'une phase : duree ajoutee a la phase (cumulee).
    pub fn fin(&mut self, nom: &'static str) {
        let now = self.t0.elapsed().as_secs_f64();
        let dt = now - self.t_phase;
        match self.phases.iter_mut().find(|p| p.0 == nom) {
            Some(p) => p.1 += dt,
            None => self.phases.push((nom, dt)),
        }
        self.t_phase = now;
        if self.evenements {
            println!("EVT {}", json!({"type": "phase", "phase": nom, "etat": "fin"}));
        }
    }

    pub fn ecoule(&self) -> f64 {
        self.t0.elapsed().as_secs_f64()
    }
}

/// Bilan d'une campagne.
pub struct Bilan<'a> {
    pub fin: Fin,
    /// cause d'invalidite connue avant la verification ("placement impossible", "routage detaille incomplet",
    /// "non route") ; vide sinon
    pub cause: &'static str,
    pub etat: Option<Etat>,
    pub detail: Option<detail::Detail<'a>>,
    pub recuit: Value,
    pub boucle: Value,
    pub conjointe: Value,
    pub finition: Value,
    pub routage_initial: Option<usize>,
    pub convertisseurs: Value,
    pub plafond_conjointe: bool,
}

fn bilan_vide<'a>(fin: Fin, cause: &'static str) -> Bilan<'a> {
    Bilan {
        fin,
        cause,
        etat: None,
        detail: None,
        recuit: recuit_vide(),
        boucle: json!({"tours": 0, "detail": []}),
        conjointe: json!({"essais": 0, "acceptes": 0, "illegaux": 0, "refuses": 0, "cout_depart": 0.0, "cout": 0.0}),
        finition: json!({"antennes_retirees": 0, "fusions": 0, "descentes_reposees": 0, "ko_avant": 0, "ko_apres": 0,
            "reparations_verification": 0, "violations_reparees": 0}),
        routage_initial: None,
        convertisseurs: json!([]),
        plafond_conjointe: false,
    }
}

pub fn recuit_vide() -> Value {
    json!({"cout_depart": 0.0, "cout": 0.0, "t0": 0.0, "acceptation_initiale": 0.0, "paliers": 0, "rechauffes": 0, "mouvements": 0,
        "hpwl_depart": 0.0, "hpwl": 0.0})
}

fn fini(v: f64) -> f64 {
    if v.is_finite() {
        v
    } else {
        0.0
    }
}

/// Campagne complete depuis zero pour une graine.
pub fn run_campagne<'a>(m: &'a Modele, graine: u64, cx: &mut Contexte) -> Bilan<'a> {
    let pm = &m.param;
    let t = Instant::now();
    if (cx.arret)() {
        return bilan_vide(Fin::ArretDemande, "non route");
    }
    cx.debut("placement_initial");
    let e0 = recuit::placement_aleatoire(m, graine);
    cx.fin("placement_initial");
    let e0 = match e0 {
        Some(e) => e,
        None => {
            cx.log(format!("graine {} : placement aleatoire impossible", graine));
            return bilan_vide(Fin::Normale, "placement impossible");
        }
    };
    let hp0 = m.hpwl(&e0);
    cx.log(format!("graine {} : placement aleatoire legal (HPWL {:.0} mm, {:.0} s)", graine, hp0, t.elapsed().as_secs_f64()));
    if (cx.arret)() {
        let mut b = bilan_vide(Fin::ArretDemande, "non route");
        b.etat = Some(e0);
        return b;
    }
    let groupes = recuit::groupes_fonctionnels(m);
    cx.log(format!("{} groupes fonctionnels", groupes.len()));
    let pc = recuit::ParamCampagne {
        mouvements_max: pm.recuit.mouvements_max as usize,
        alpha: pm.recuit.alpha,
        palier_par_piece: pm.recuit.palier_par_piece,
        acc_initiale: pm.recuit.acceptation_initiale,
        pas: pm.recuit.pas_grille_globale_mm,
        periode: pm.recuit.periode_reroutage,
        t_fin_rel: pm.recuit.t_fin_relative,
        rudy_cap: pm.recuit.rudy_capacite,
        rudy_lambda: pm.recuit.rudy_lambda,
    };
    let jl = |s: String| println!("{}", s);
    cx.debut("recuit");
    let rs = recuit::chaine_campagne(m, &e0, graine, &pc, &groupes, &jl);
    cx.fin("recuit");
    cx.log(format!(
        "recuit global : cout {:.1} -> {:.1} ; T0 {:.2} ; acceptation initiale mesuree {:.0} % ; {} paliers, {} rechauffes, {} mouvements, {:.0} s ; HPWL {:.0} mm",
        rs.cout_depart, rs.cout, rs.t0, 100.0 * rs.acc_initiale_mesuree, rs.paliers, rs.rechauffes, rs.mouvements, rs.temps, m.hpwl(&rs.etat)
    ));
    let convertisseurs: Vec<Value> = m
        .cfg
        .convertisseurs
        .iter()
        .zip(rs.noeuds_exploration.iter())
        .map(|(cv, n)| {
            let evaluee = m.boucle_chaude_cv(cv, &rs.etat).is_some();
            match n {
                Some(l) => json!({"nom": cv.nom, "boucle_chaude_evaluee": evaluee, "noeud_estime_mm": (fini(*l) * 10.0).round() / 10.0}),
                None => json!({"nom": cv.nom, "boucle_chaude_evaluee": evaluee}),
            }
        })
        .collect();
    let sa = json!({"cout_depart": fini(rs.cout_depart), "cout": fini(rs.cout), "t0": fini(rs.t0).max(0.0),
        "acceptation_initiale": fini(rs.acc_initiale_mesuree).clamp(0.0, 1.0), "paliers": rs.paliers,
        "rechauffes": rs.rechauffes, "mouvements": rs.mouvements, "hpwl_depart": hp0, "hpwl": m.hpwl(&rs.etat),
        "exploration": {"paliers": rs.exploration.0, "mouvements": rs.exploration.1, "hpwl": fini(rs.exploration.2), "cout": fini(rs.exploration.3)}});
    let mut bilan = bilan_vide(Fin::Normale, "");
    bilan.recuit = sa;
    bilan.convertisseurs = Value::Array(convertisseurs);
    bilan.etat = Some(rs.etat.clone());
    if pm.campagne.arret_apres == ArretApres::Recuit {
        bilan.fin = Fin::ArretApres;
        bilan.cause = "non route";
        return bilan;
    }
    if (cx.arret)() {
        bilan.fin = Fin::ArretDemande;
        bilan.cause = "non route";
        return bilan;
    }
    let plafond = |cx: &Contexte| pm.campagne.duree_max_s > 0.0 && cx.ecoule() > pm.campagne.duree_max_s;
    // routage detaille complet
    let mut etat = rs.etat.clone();
    cx.debut("routage_detaille");
    let mut base = detail::Detail::new(m, &etat);
    base.via_dans_pad = m.via_dans_pad;
    base.router_tout(pm.routage.iters_negociation, pm.routage.tours_arrachage, &mut |s| println!("{}", s));
    cx.fin("routage_detaille");
    bilan.routage_initial = Some(base.total_ko());
    cx.log(format!("routage detaille : {} connexions en echec ({:.0} s)", base.total_ko(), t.elapsed().as_secs_f64()));
    if pm.campagne.arret_apres == ArretApres::Routage {
        bilan.fin = Fin::ArretApres;
        bilan.etat = Some(base.e.clone());
        bilan.detail = Some(base);
        return bilan;
    }
    // boucle placement <-> routage detaille : zones de pression autour des echecs, recuit local des pieces voisines
    let mut zones: Vec<(f64, f64, f64, f64, Option<usize>)> = Vec::new();
    let mut poids = pm.boucle.poids_initial;
    let mut tours_boucle = 0;
    let mut detail_boucle = Vec::new();
    for tour in 0..pm.boucle.tours {
        let ko = base.total_ko();
        if ko == 0 {
            break;
        }
        if (cx.arret)() {
            bilan.fin = Fin::ArretDemande;
            break;
        }
        if plafond(cx) {
            bilan.fin = Fin::PlafondTemps;
            break;
        }
        cx.debut("boucle");
        tours_boucle += 1;
        for z in zones_echecs(&base, m, &etat, pm.boucle.rayon_zone_mm, poids) {
            if let Some(e) = zones.iter_mut().find(|q| (q.0 - z.0).abs() + (q.1 - z.1).abs() < 1.0 && q.4 == z.4) {
                e.3 += z.3;
            } else {
                zones.push(z);
            }
        }
        let mut mr = m.clone();
        mr.pression = zones.clone();
        let movers: Vec<usize> = m.mobiles.iter().copied().filter(|&i| {
            (0..m.comps[i].pads.len()).any(|k| {
                let (x, y, _, _) = m.pad_world(i, k, &etat.poses[i]);
                zones.iter().any(|z| ((z.0 - x).powi(2) + (z.1 - y).powi(2)).sqrt() < z.2 + pm.boucle.rayon_pieces_mm)
            })
        }).collect();
        if movers.is_empty() {
            cx.fin("boucle");
            cx.log("boucle : aucune piece mobile pres des echecs".into());
            break;
        }
        let p = recuit::Params {
            mouvements: pm.boucle.mouvements_locaux,
            periode: pm.recuit.periode_reroutage,
            iters_pf: 4,
            t0_facteur: pm.boucle.t0_facteur,
            pas: pm.recuit.pas_grille_globale_mm,
            sigma0: pm.boucle.sigma_mm,
            mobiles: Some(movers.clone()),
        };
        let res: Vec<recuit::Resultat> = (0..pm.campagne.chaines.max(1)).into_par_iter().map(|k| recuit::chaine(&mr, &etat, graine * 1000 + tour as u64 * 16 + k as u64, &p)).collect();
        let mut mes: Vec<(f64, usize)> = res.par_iter().enumerate().map(|(k, r)| {
            let (l, _) = mesurer(&mr, &r.etat, pm.recuit.pas_grille_globale_mm);
            let pr: f64 = (0..mr.comps.len()).map(|i| mr.pression_piece(i, &r.etat.poses[i])).sum();
            (l.ms.objectif_routage + l.extra + pr, k)
        }).collect();
        mes.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1)));
        let cand = res[mes[0].1].etat.clone();
        let mut d2 = detail::Detail::new(m, &cand);
        d2.via_dans_pad = m.via_dans_pad;
        d2.router_tout(pm.routage.iters_negociation, pm.routage.tours_arrachage, &mut |s| println!("{}", s));
        let kn = d2.total_ko();
        cx.fin("boucle");
        cx.log(format!("boucle {} : {} zones, {} pieces deplacables ; echecs {} -> {} ({}) ({:.0} s)", tour + 1, zones.len(), movers.len(), ko, kn,
            if kn < ko { "retenu" } else { "rejete, poids x2" }, t.elapsed().as_secs_f64()));
        detail_boucle.push(json!({"tour": tour + 1, "zones": zones.len(), "pieces": movers.len(), "ko_avant": ko, "ko_apres": kn, "retenu": kn < ko}));
        if kn < ko {
            etat = cand;
            base = d2;
        } else {
            poids *= 2.0;
            for z in zones.iter_mut() {
                z.3 *= 2.0;
            }
        }
    }
    bilan.boucle = json!({"tours": tours_boucle, "detail": detail_boucle});
    bilan.etat = Some(base.e.clone());
    let ko = base.total_ko();
    if bilan.fin == Fin::ArretDemande {
        bilan.detail = Some(base);
        return bilan;
    }
    // peu d'echecs : la phase conjointe (cout 1e5 par connexion en echec, pieces voisines tirees en priorite) tente
    // de les resorber ; au-dela, la campagne s'arrete ici (invalide)
    if ko > cx.seuil_abandon {
        cx.log(format!("abandon : {} connexions en echec apres la boucle (seuil {})", ko, cx.seuil_abandon));
        bilan.fin = Fin::Abandon;
        bilan.cause = "routage detaille incomplet";
        bilan.detail = Some(base);
        return bilan;
    }
    let mut d = base.clone();
    if bilan.fin != Fin::PlafondTemps && !plafond(cx) {
        // phase conjointe (cout sur le routage detaille, espacement compris, reroutage de zone periodique) ; budget en
        // essais (avenant 2.2, A.1), la duree `plafond_s` n'est qu'un plafond
        let p = conjoint::ParamConj {
            essais: conjoint::budget_essais(pm.conjointe.essais, pm.conjointe.essais_par_piece_mobile, m.mobiles.len()),
            budget_s: pm.conjointe.plafond_s,
            t0: pm.conjointe.t0,
            t_fin: pm.conjointe.t_fin,
            sigma: pm.conjointe.sigma_mm,
            prox_poids: pm.conjointe.poids_proximite,
            zone_periode: pm.conjointe.periode_zone,
        };
        cx.debut("conjointe");
        let tc = Instant::now();
        let rc = conjoint::chaine(&base, graine * 7 + 3, &p, &jl);
        bilan.plafond_conjointe = rc.essais < p.essais && tc.elapsed().as_secs_f64() >= p.budget_s;
        cx.fin("conjointe");
        cx.log(format!("phase conjointe : cout {:.1} -> {:.1} ; essais {} acceptes {} illegaux {} refuses {}", rc.cout_depart, rc.cout, rc.essais, rc.acceptes, rc.illegaux, rc.refuses_routage));
        bilan.conjointe = json!({"essais": rc.essais, "acceptes": rc.acceptes, "illegaux": rc.illegaux, "refuses": rc.refuses_routage,
            "cout_depart": fini(rc.cout_depart), "cout": fini(rc.cout)});
        for i in 0..m.comps.len() {
            if d.e.poses[i] != rc.etat.poses[i] {
                d.deplacer(i, rc.etat.poses[i]);
            }
        }
        d.charger_reseaux(&rc.nets);
    } else {
        bilan.fin = Fin::PlafondTemps;
    }
    cx.debut("finition");
    let ko_avant = d.total_ko();
    let esp = param_esp(pm);
    d.finaliser(&esp, &mut |s| println!("{}", s));
    // reste quelques echecs : arrachage-reroutage cible (voir Detail::reparer_echecs)
    if d.total_ko() > 0 {
        d.reparer_echecs(&mut |s| println!("{}", s));
        cx.log(format!("finition ciblee : {} connexions en echec", d.total_ko()));
    }
    // reste des echecs locaux : reparation regionale bornee (lot F)
    if d.total_ko() > 0 && !(cx.arret)() {
        d.reparer_regional(REPARATION_ETAPES, REPARATION_VICTIMES, &mut |s| println!("{}", s));
        cx.log(format!("reparation regionale : {} connexions en echec", d.total_ko()));
    }
    // toujours des echecs : routage detaille complet refait sur le placement final, negociation plus longue
    // (desactive par defaut : long et il a degrade le resultat sur plusieurs graines ; finition.routage_complet)
    if d.total_ko() > 0 && d.total_ko() <= 5 && pm.finition.routage_complet && bilan.fin != Fin::PlafondTemps {
        let (iters, tours) = (pm.finition.routage_complet_iters, pm.finition.routage_complet_tours);
        cx.log(format!("finition : {} connexions en echec, nouveau routage complet ({} iterations, {} tours)", d.total_ko(), iters, tours));
        let mut d2 = detail::Detail::new(m, &d.e);
        d2.via_dans_pad = m.via_dans_pad;
        d2.router_tout(iters, tours, &mut |s| println!("{}", s));
        d2.finaliser(&esp, &mut |s| println!("{}", s));
        cx.log(format!("finition : {} -> {} connexions en echec", d.total_ko(), d2.total_ko()));
        if d2.total_ko() < d.total_ko() {
            d = d2;
        }
    }
    cx.fin("finition");
    bilan.finition = json!({"antennes_retirees": 0, "fusions": 0, "descentes_reposees": 0, "ko_avant": ko_avant, "ko_apres": d.total_ko(),
        "reparations_verification": 0, "violations_reparees": 0});
    bilan.etat = Some(d.e.clone());
    bilan.detail = Some(d);
    bilan
}

/// Routage existant d'un placement donne (sous-commande `router --routage-initial`) : charge tel quel (pas de
/// routage), pour le post-traitement (lissage, accord des longueurs) et la verification.
pub fn run_charge<'a>(m: &'a Modele, e: &Etat, chemin: &str, cx: &mut Contexte) -> Result<Bilan<'a>, String> {
    let mut bilan = bilan_vide(Fin::Normale, "");
    bilan.etat = Some(e.clone());
    cx.debut("routage_detaille");
    let mut d = detail::Detail::new(m, e);
    d.via_dans_pad = m.via_dans_pad;
    let nb = d.charger_routage_json(chemin)?;
    cx.fin("routage_detaille");
    cx.log(format!("routage initial charge : {} pistes et vias", nb));
    bilan.routage_initial = Some(0);
    bilan.detail = Some(d);
    Ok(bilan)
}

/// Routage seul d'un placement donne (sous-commande `router`) : routage detaille complet puis finition.
pub fn run_router<'a>(m: &'a Modele, e: &Etat, cx: &mut Contexte) -> Bilan<'a> {
    let pm = &m.param;
    let mut bilan = bilan_vide(Fin::Normale, "");
    bilan.etat = Some(e.clone());
    if (cx.arret)() {
        bilan.fin = Fin::ArretDemande;
        bilan.cause = "non route";
        return bilan;
    }
    cx.debut("routage_detaille");
    let mut d = detail::Detail::new(m, e);
    d.via_dans_pad = m.via_dans_pad;
    d.router_tout(pm.routage.iters_negociation, pm.routage.tours_arrachage, &mut |s| println!("{}", s));
    cx.fin("routage_detaille");
    bilan.routage_initial = Some(d.total_ko());
    cx.log(format!("routage detaille : {} connexions en echec", d.total_ko()));
    if pm.campagne.arret_apres == ArretApres::Routage {
        bilan.fin = Fin::ArretApres;
        bilan.detail = Some(d);
        return bilan;
    }
    if (cx.arret)() {
        bilan.fin = Fin::ArretDemande;
        bilan.detail = Some(d);
        return bilan;
    }
    cx.debut("finition");
    let ko_avant = d.total_ko();
    d.finaliser(&param_esp(pm), &mut |s| println!("{}", s));
    if d.total_ko() > 0 {
        d.reparer_echecs(&mut |s| println!("{}", s));
    }
    if d.total_ko() > 0 && !(cx.arret)() {
        d.reparer_regional(REPARATION_ETAPES, REPARATION_VICTIMES, &mut |s| println!("{}", s));
    }
    cx.fin("finition");
    bilan.finition = json!({"antennes_retirees": 0, "fusions": 0, "descentes_reposees": 0, "ko_avant": ko_avant, "ko_apres": d.total_ko(),
        "reparations_verification": 0, "violations_reparees": 0});
    bilan.detail = Some(d);
    bilan
}
