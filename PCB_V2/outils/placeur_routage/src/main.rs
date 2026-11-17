//! placeur_routage : placement par recuit simule dont le cout vient d'un routage global reel,
//! puis routage detaille du placement retenu.
//!
//! Usage (depuis PCB_V2/outils/placeur_routage) :
//!   target/release/placeur_routage.exe [options]
//! Options :
//!   --donnees F        JSON d'entree (defaut donnees_placeur.json, produit par export_donnees.py)
//!   --sortie D         dossier de sortie (defaut sorties)
//!   --graine N         graine de base (defaut 1) ; chaine k utilise N*1000+k
//!   --chaines K        nombre de chaines de recuit en parallele (defaut 12)
//!   --threads T        fils rayon (defaut = nombre de coeurs)
//!   --mouvements M     mouvements par chaine (defaut 60000)
//!   --periode P        reroutage complet tous les P mouvements (defaut 3000)
//!   --pas X            pas de la grille du routeur global en mm (defaut 0.5)
//!   --t0 F             facteur de temperature initiale (defaut 0.3)
//!   --sigma S          ecart-type initial des deplacements en mm (defaut 4)
//!   --tours-rr R       tours d'arrachage-reroutage du routage detaille (defaut 4)
//!   --top-detail K     nombre de meilleures chaines departagees par le routage detaille (defaut 4)
//!   --iters-det N      iterations de negociation PathFinder du routage detaille (defaut 30)
//!   --esp-prox X       poids du terme de proximite de la passe d'espacement (defaut 1.0, par mm)
//!   --esp-coude X      penalite par coude de la passe d'espacement (defaut 0.3 mm)
//!   --esp-allong X     allongement relatif maximal accepte par reseau (defaut 0.15)
//!   --sans-espacement  sauter la passe d'espacement
//!   --placement F      ne pas optimiser : mesurer / router ce placement (format placements_v3.json)
//!   --depart F         placement de depart du recuit et de la boucle (defaut : v3)
//!   --cuivre-bloc F    cuivre existant du bloc hacheur (defaut cuivre_bloc_hacheur.json)
//!   --pastilles F      geometrie reelle des pastilles (defaut pastilles_reelles.json, export_pastilles_reelles.py)
//!   --boucle R         tours de la boucle placement <-> routage detaille (defaut 6)
//!   --conjoint         optimisation conjointe placement + routage detaille, depart = --depart (placement pose) ;
//!                      sorties dans --sortie-conjoint (defaut sorties/conjoint), budget --budget-s (defaut 3000 s),
//!                      --chaines-conjoint K chaines paralleles (defaut 3) ; --routage-depart F : routage existant
//!                      (valide) du placement de depart, charge tel quel au lieu d'etre reroute
//!   --campagne         campagne complete depuis ZERO pour la graine --graine : placement aleatoire legal, recuit
//!                      global a paliers (--mouvements max), routage detaille, phase conjointe (--budget-s),
//!                      finition ; sorties dans --sortie (orchestre par campagnes.py)
//!   --bloc-libre       libere le bloc hacheur : pieces mobiles (groupe compact, termes boucle chaude / noeud de
//!                      commutation / bootstrap / decouplages / compensation), cuivre manuel ignore, tout est route
//!   --via-dans-pad     dernier recours : via dans une pastille CMS GND si aucun emplacement a moins de 6 mm
//!   --mouv-local M     mouvements par chaine du recuit local de chaque tour (defaut 60000)
//!   --sans-detail      sauter le routage detaille
//!   --gpu              accepte mais sans effet (voir rapport : non rentable ici)
mod conjoint;
mod detail;
mod donnees;
mod geom;
mod globalr;
mod recuit;
mod sortie;

use geom::*;
use globalr::*;
use rayon::prelude::*;
use serde_json::json;
use std::io::Write;
use std::time::Instant;

const ITERS_MESURE: usize = 12;

struct Opts {
    donnees: String,
    sortie: String,
    graine: u64,
    chaines: usize,
    threads: usize,
    mouvements: usize,
    periode: usize,
    pas: f64,
    t0: f64,
    sigma: f64,
    tours_rr: usize,
    iters_det: usize,
    top_detail: usize,
    esp: detail::ParamEsp,
    placement: Option<String>,
    depart: Option<String>,
    cuivre_bloc: String,
    pastilles: String,
    boucle: usize,
    mouv_local: usize,
    via_dans_pad: bool,
    conjoint: bool,
    campagne: bool,
    bloc_libre: bool,
    routage_depart: Option<String>,
    budget_s: f64,
    chaines_conj: usize,
    sortie_conj: String,
    detail: bool,
    gpu: bool,
}

fn opts() -> Result<Opts, String> {
    let mut o = Opts {
        donnees: "donnees_placeur.json".into(),
        sortie: "sorties".into(),
        graine: 1,
        chaines: 12,
        threads: 0,
        mouvements: 60000,
        periode: 3000,
        pas: 0.5,
        t0: 0.3,
        sigma: 4.0,
        tours_rr: 4,
        iters_det: 30,
        top_detail: 4,
        esp: detail::ParamEsp { actif: true, prox: 1.0, coude: 0.3, allong_max: 0.15 },
        placement: None,
        depart: None,
        cuivre_bloc: "cuivre_bloc_hacheur.json".into(),
        pastilles: "pastilles_reelles.json".into(),
        boucle: 6,
        mouv_local: 60000,
        via_dans_pad: false,
        conjoint: false,
        campagne: false,
        bloc_libre: false,
        routage_depart: None,
        budget_s: 3000.0,
        chaines_conj: 3,
        sortie_conj: "sorties/conjoint".into(),
        detail: true,
        gpu: false,
    };
    let a: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    let val = |i: &mut usize| -> Result<String, String> {
        *i += 1;
        a.get(*i).cloned().ok_or(format!("valeur manquante apres {}", a[*i - 1]))
    };
    let num = |s: String| -> Result<f64, String> { s.parse::<f64>().map_err(|e| format!("{} : {}", s, e)) };
    while i < a.len() {
        match a[i].as_str() {
            "--donnees" => o.donnees = val(&mut i)?,
            "--sortie" => o.sortie = val(&mut i)?,
            "--graine" => o.graine = num(val(&mut i)?)? as u64,
            "--chaines" => o.chaines = num(val(&mut i)?)? as usize,
            "--threads" => o.threads = num(val(&mut i)?)? as usize,
            "--mouvements" => o.mouvements = num(val(&mut i)?)? as usize,
            "--periode" => o.periode = num(val(&mut i)?)?.max(1.0) as usize,
            "--pas" => o.pas = num(val(&mut i)?)?,
            "--t0" => o.t0 = num(val(&mut i)?)?,
            "--sigma" => o.sigma = num(val(&mut i)?)?,
            "--tours-rr" => o.tours_rr = num(val(&mut i)?)? as usize,
            "--iters-det" => o.iters_det = num(val(&mut i)?)? as usize,
            "--top-detail" => o.top_detail = num(val(&mut i)?)?.max(1.0) as usize,
            "--esp-prox" => o.esp.prox = num(val(&mut i)?)?,
            "--esp-coude" => o.esp.coude = num(val(&mut i)?)?,
            "--esp-allong" => o.esp.allong_max = num(val(&mut i)?)?,
            "--sans-espacement" => o.esp.actif = false,
            "--placement" => o.placement = Some(val(&mut i)?),
            "--depart" => o.depart = Some(val(&mut i)?),
            "--cuivre-bloc" => o.cuivre_bloc = val(&mut i)?,
            "--pastilles" => o.pastilles = val(&mut i)?,
            "--boucle" => o.boucle = num(val(&mut i)?)? as usize,
            "--via-dans-pad" => o.via_dans_pad = true,
            "--conjoint" => o.conjoint = true,
            "--campagne" => o.campagne = true,
            "--bloc-libre" => o.bloc_libre = true,
            "--routage-depart" => o.routage_depart = Some(val(&mut i)?),
            "--budget-s" => o.budget_s = num(val(&mut i)?)?,
            "--chaines-conjoint" => o.chaines_conj = num(val(&mut i)?)?.max(1.0) as usize,
            "--sortie-conjoint" => o.sortie_conj = val(&mut i)?,
            "--mouv-local" => o.mouv_local = num(val(&mut i)?)? as usize,
            "--sans-detail" => o.detail = false,
            "--gpu" => o.gpu = true,
            x => return Err(format!("option inconnue {}", x)),
        }
        i += 1;
    }
    Ok(o)
}

struct Ligne {
    ms: Mesure,
    hpwl: f64,
    extra: f64,
    decaps_loin: usize,
    temps: f64,
}

fn mesurer<'a>(m: &'a Modele, e: &Etat, pas: f64) -> (Ligne, Routeur<'a>) {
    let t = Instant::now();
    let mut rt = Routeur::new(m, e, pas);
    rt.route_tout(e, ITERS_MESURE);
    let ms = rt.mesure();
    (Ligne { ms, hpwl: m.hpwl(e), extra: m.cout_extra(e), decaps_loin: m.decaps_loin(e), temps: t.elapsed().as_secs_f64() }, rt)
}

fn detailler<'a>(m: &'a Modele, e: &Etat, o: &Opts) -> (detail::Detail<'a>, serde_json::Value) {
    let t = Instant::now();
    let mut dt = detail::Detail::new(m, e);
    dt.via_dans_pad = o.via_dans_pad;
    dt.tout(o.iters_det, o.tours_rr, &o.esp, &mut |s| println!("{}", s));
    let nets_ok = dt.a_router().iter().filter(|&&n| dt.nets[n].ko == 0).count();
    let longueur: f64 = dt.nets.iter().flat_map(|r| r.segs.iter()).map(|s| ((s.x2 - s.x1).powi(2) + (s.y2 - s.y1).powi(2)).sqrt()).sum();
    let mut r_puiss = 0.0;
    let mut derog = 0.0;
    for (n, r) in dt.nets.iter().enumerate() {
        if matches!(m.nets[n].classe.as_str(), "ENTREE_12V" | "CANAL_2A" | "RAILS" | "PUISSANCE_8V") {
            r_puiss += r.carres * sortie::MOHM_CARRE;
        }
        if m.nets[n].classe == "CANAL_2A" {
            derog += r.derog_mm;
        }
    }
    let st = json!({"connexions_reussies": dt.total_ok(), "connexions_echouees": dt.total_ko(), "reseaux_complets_selon_routeur": nets_ok,
        "reseaux_a_router": dt.a_router().len(), "connexions_en_largeur_reduite": dt.nets.iter().map(|r| r.largeur_reduite).sum::<usize>(),
        "longueur_reduite_mm": (dt.nets.iter().map(|r| r.neck_mm).sum::<f64>() * 10.0).round() / 10.0,
        "canal_2a_sous_1_5mm_mm": (derog * 10.0).round() / 10.0,
        "resistance_reseaux_puissance_mohm": (r_puiss * 100.0).round() / 100.0,
        "vias_doubles": dt.nets.iter().map(|r| r.vias_doubles).sum::<usize>(),
        "segments": dt.nets.iter().map(|r| r.segs.len()).sum::<usize>(), "vias": dt.nets.iter().map(|r| r.vias.len()).sum::<usize>(),
        "longueur_mm": (longueur * 10.0).round() / 10.0, "temps_s": (t.elapsed().as_secs_f64() * 10.0).round() / 10.0});
    (dt, st)
}

fn main() {
    let o = match opts() {
        Ok(o) => o,
        Err(e) => {
            eprintln!("erreur : {}", e);
            std::process::exit(2);
        }
    };
    if let Err(e) = run(o) {
        eprintln!("erreur : {}", e);
        std::process::exit(1);
    }
}

struct Cand<'a> {
    etat: Etat,
    nom: String,
    det: Option<(detail::Detail<'a>, serde_json::Value)>,
}

fn ko_of(c: &Cand) -> (u64, u64) {
    match &c.det {
        Some((_, st)) => (st["connexions_echouees"].as_u64().unwrap(), (st["longueur_mm"].as_f64().unwrap() * 10.0) as u64),
        None => (u64::MAX, 0),
    }
}

/// Zones de pression tirees des echecs d'un routage detaille (une par broche en echec).
fn zones_echecs(d: &detail::Detail, m: &Modele, e: &Etat, w: f64) -> Vec<(f64, f64, f64, f64, Option<usize>)> {
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
                z.push((x, y, 3.5, w, if plan { None } else { Some(n) }));
            }
        }
    }
    z
}

fn run(o: Opts) -> Result<(), String> {
    std::fs::create_dir_all(&o.sortie).map_err(|e| e.to_string())?;
    let mut jf = std::fs::File::create(format!("{}/journal.txt", o.sortie)).map_err(|e| e.to_string())?;
    let mut log = |s: String| {
        println!("{}", s);
        let _ = writeln!(jf, "{}", s);
    };
    if o.gpu {
        log("--gpu : option sans effet, le calcul reste sur CPU (voir rapport : non rentable pour ce probleme).".into());
    }
    if o.threads > 0 {
        let _ = rayon::ThreadPoolBuilder::new().num_threads(o.threads).build_global();
    }
    let t_tot = Instant::now();
    let d = donnees::lire(&o.donnees)?;
    let mut mm = Modele::new(&d);
    if std::path::Path::new(&o.pastilles).exists() {
        let n = mm.charger_pastilles_reelles(&o.pastilles)?;
        log(format!("geometrie reelle des pastilles (Altium) : {} pastilles, {} pastilles libres de trous M3 ({})", n, mm.trous.len(), o.pastilles));
    } else {
        log(format!("ATTENTION : {} absent, pastilles approchees par leurs rectangles (masque compris)", o.pastilles));
    }
    let cfgp = std::env::var("CONFIG_CARTE").unwrap_or_else(|_| "config_carte.json".to_string());
    if std::path::Path::new(&cfgp).exists() {
        mm.cfg = geom::ConfigCarte::lire(&cfgp)?;
        log(format!("configuration de carte {} : {} convertisseur(s), {} piece(s) sans piste dessous", cfgp, mm.cfg.convertisseurs.len(), mm.cfg.sans_piste_dessous.len()));
    }
    if o.bloc_libre {
        mm.liberer_bloc();
        // eventail recopie du cuivre manuel de rev4 : specifique a cette carte, seulement sur demande (comparaison)
        if std::env::var("EVENTAIL_REV4").is_ok() && std::path::Path::new(&o.cuivre_bloc).exists() {
            let l = std::env::var("EVENTAIL_L").ok().and_then(|v| v.parse().ok()).unwrap_or(1.6);
            let n = mm.charger_eventail_u10(&o.cuivre_bloc, l)?;
            log(format!("eventail de sortie fixe d'U10 (bloc manuel, {} mm au plus) : {} broches", l, n));
        }
        log(format!("bloc hacheur LIBERE : {} pieces mobiles au total, cuivre manuel ignore ; {} pieces sous termes du convertisseur",
            mm.mobiles.len(), mm.hacheur.len()));
    } else if std::path::Path::new(&o.cuivre_bloc).exists() {
        let n = mm.charger_cuivre_bloc(&o.cuivre_bloc)?;
        log(format!("cuivre existant du bloc : {} objets ({})", n, o.cuivre_bloc));
        for s in mm.cuivre_isole() {
            log(format!("  cuivre du bloc non relie a une pastille (obstacle, pas cible) : {}", s));
        }
    } else {
        log(format!("ATTENTION : {} absent, cuivre du bloc inconnu", o.cuivre_bloc));
    }
    let m = mm; // plus modifie ensuite (les routages detailles l'empruntent)
    if o.conjoint {
        return run_conjoint(&o, &m, &mut log);
    }
    if o.campagne {
        return run_campagne(&o, &m, &mut log);
    }
    log(format!("{} pieces ({} mobiles, {} bloc), {} reseaux ; pas global {} mm", m.comps.len(), m.mobiles.len(), m.bloc.len(), m.nets.len(), o.pas));
    let v3 = m.etat_v3();
    let depart = match &o.depart {
        Some(f) => m.etat_depuis(&donnees::lire_placement(f)?)?,
        None => v3.clone(),
    };
    let (lv3, _) = mesurer(&m, &v3, o.pas);
    log(format!("v3 mesure : {:?} hpwl {:.1} ({:.2} s)", lv3.ms, lv3.hpwl, lv3.temps));

    // ---------------------------------------------------------------- candidats initiaux
    let mut cands: Vec<Cand> = Vec::new();
    let mut t_opt = 0.0;
    let mut infos: Vec<(u64, f64, f64)> = Vec::new();
    let lancer = |mr: &Modele, base: &Etat, p: &recuit::Params, graine: u64, n: usize| -> Vec<recuit::Resultat> {
        (0..n).into_par_iter().map(|k| recuit::chaine(mr, base, graine * 1000 + k as u64, p)).collect()
    };
    if let Some(f) = &o.placement {
        cands.push(Cand { etat: m.etat_depuis(&donnees::lire_placement(f)?)?, nom: format!("placement {}", f), det: None });
    } else if o.mouvements > 0 {
        let p = recuit::Params { mouvements: o.mouvements, periode: o.periode, iters_pf: 4, t0_facteur: o.t0, pas: o.pas, sigma0: o.sigma, mobiles: None };
        let t = Instant::now();
        let res = lancer(&m, &depart, &p, o.graine, o.chaines);
        t_opt = t.elapsed().as_secs_f64();
        let (mut ess, mut ill) = (0, 0);
        for r in &res {
            log(format!(
                "chaine graine {} : depart {:.1} -> {:.1} ; essais {} acceptes {} illegaux {} (spirale {}, ejection {}) ; T0 {:.2} ; {:.0} mouvements/s ; profil legalisation {:.1} s, cout annexe {:.1} s, routage {:.1} s",
                r.graine, r.cout_depart, r.cout, r.essais, r.acceptes, r.illegaux, r.legalises, r.ejections, r.t0, r.essais as f64 / r.temps, r.prof[0], r.prof[1], r.prof[2]
            ));
            ess += r.essais;
            ill += r.illegaux;
        }
        log(format!("recuit global : {:.1} % illegaux, {:.0} s", 100.0 * ill as f64 / ess.max(1) as f64, t_opt));
        let mut mes: Vec<(f64, usize)> = res.par_iter().enumerate().map(|(k, r)| {
            let (l, _) = mesurer(&m, &r.etat, o.pas);
            (l.ms.objectif_routage + l.extra, k)
        }).collect();
        infos = res.iter().zip(mes.iter()).map(|(r, x)| (r.graine, r.cout_depart, x.0)).collect();
        mes.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
        for &(_, i) in mes.iter().take(if o.detail { o.top_detail } else { 1 }) {
            cands.push(Cand { etat: res[i].etat.clone(), nom: format!("recuit, graine {}", res[i].graine), det: None });
        }
    } else {
        cands.push(Cand { etat: depart.clone(), nom: match &o.depart { Some(f) => format!("depart {}", f), None => "v3".into() }, det: None });
    }

    // ---------------------------------------------------------------- routage detaille des candidats (+ v3)
    let avec_v3 = o.detail && o.placement.is_none();
    let mut det_v3 = None;
    if o.detail {
        let mut jobs: Vec<Etat> = cands.iter().map(|c| c.etat.clone()).collect();
        if avec_v3 {
            jobs.push(v3.clone());
        }
        let mut outs: Vec<(detail::Detail, serde_json::Value)> = jobs.par_iter().map(|e| detailler(&m, e, &o)).collect();
        if avec_v3 {
            det_v3 = outs.pop();
        }
        for (c, d) in cands.iter_mut().zip(outs.into_iter()) {
            log(format!("detaille {} : {} connexions en echec, {} mm", c.nom, d.1["connexions_echouees"], d.1["longueur_mm"]));
            c.det = Some(d);
        }
    }
    let ib = (0..cands.len()).min_by_key(|&k| ko_of(&cands[k])).unwrap();
    let mut best = cands.swap_remove(ib);
    drop(cands);

    // ---------------------------------------------------------------- boucle placement <-> routage detaille
    let mut zones: Vec<(f64, f64, f64, f64, Option<usize>)> = Vec::new();
    let mut poids = 30.0;
    let mut md_boucle = String::from("\n### Boucle placement <-> routage detaille\n\n| Tour | Zones de pression | Pieces mobiles concernees | Echecs avant | Meilleur candidat | Retenu |\n|---|---:|---:|---:|---:|---|\n");
    if o.detail && o.placement.is_none() {
        for tour in 0..o.boucle {
            let ko = ko_of(&best).0;
            if ko == 0 {
                break;
            }
            let (dt, _) = best.det.as_ref().unwrap();
            for z in zones_echecs(dt, &m, &best.etat, poids) {
                if let Some(e) = zones.iter_mut().find(|q| (q.0 - z.0).abs() + (q.1 - z.1).abs() < 1.0 && q.4 == z.4) {
                    e.3 += z.3;
                } else {
                    zones.push(z);
                }
            }
            let mut mr = m.clone();
            mr.pression = zones.clone();
            // pieces deplacables : mobiles dont une pastille est a moins de rayon + 4 mm d'une zone
            let movers: Vec<usize> = m.mobiles.iter().copied().filter(|&i| {
                (0..m.comps[i].pads.len()).any(|k| {
                    let (x, y, _, _) = m.pad_world(i, k, &best.etat.poses[i]);
                    zones.iter().any(|z| ((z.0 - x).powi(2) + (z.1 - y).powi(2)).sqrt() < z.2 + 4.0)
                })
            }).collect();
            if movers.is_empty() {
                log("boucle : aucune piece mobile pres des echecs".into());
                break;
            }
            let p = recuit::Params { mouvements: o.mouv_local, periode: o.periode, iters_pf: 4, t0_facteur: 0.15, pas: o.pas, sigma0: 2.5, mobiles: Some(movers.clone()) };
            let res = lancer(&mr, &best.etat, &p, o.graine * 100 + tour as u64 + 1, o.chaines);
            let mut mes: Vec<(f64, usize)> = res.par_iter().enumerate().map(|(k, r)| {
                let (l, _) = mesurer(&mr, &r.etat, o.pas);
                let pr: f64 = (0..mr.comps.len()).map(|i| mr.pression_piece(i, &r.etat.poses[i])).sum();
                (l.ms.objectif_routage + l.extra + pr, k)
            }).collect();
            mes.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
            let jobs: Vec<(Etat, String)> = mes.iter().take(o.top_detail).map(|&(_, i)| (res[i].etat.clone(), format!("boucle {} graine {}", tour + 1, res[i].graine))).collect();
            let outs: Vec<(detail::Detail, serde_json::Value)> = jobs.par_iter().map(|(e, _)| detailler(&m, e, &o)).collect();
            let mut nouveaux: Vec<Cand> = jobs.into_iter().zip(outs.into_iter()).map(|((e, n), d)| Cand { etat: e, nom: n, det: Some(d) }).collect();
            let k = (0..nouveaux.len()).min_by_key(|&k| ko_of(&nouveaux[k])).unwrap();
            let kn = ko_of(&nouveaux[k]);
            let retenu = kn < ko_of(&best);
            md_boucle.push_str(&format!("| {} | {} | {} | {} | {} ({}) | {} |\n", tour + 1, zones.len(), movers.len(), ko, kn.0, nouveaux[k].nom, if retenu { "oui" } else { "non (poids x2)" }));
            log(format!("boucle {} : {} zones, {} pieces deplacables ; echecs {} -> meilleur candidat {} ({})", tour + 1, zones.len(), movers.len(), ko, kn.0, if retenu { "retenu" } else { "rejete, poids x2" }));
            if retenu {
                best = nouveaux.swap_remove(k);
            } else {
                poids *= 2.0;
                for z in zones.iter_mut() {
                    z.3 *= 2.0;
                }
            }
        }
    }
    let choisi = best.etat.clone();
    let nom_choisi = best.nom.clone();
    let (lopt, rt_opt) = mesurer(&m, &choisi, o.pas);
    log(format!("{} mesure : {:?} hpwl {:.1}", nom_choisi, lopt.ms, lopt.hpwl));

    // ---------------------------------------------------------------- sorties
    let pj = sortie::placement_json(&m, &choisi);
    std::fs::write(format!("{}/placements_routage.json", o.sortie), serde_json::to_string_pretty(&pj).unwrap()).map_err(|e| e.to_string())?;
    let (_, rt_v3) = mesurer(&m, &v3, o.pas);
    sortie::svg_global(&format!("{}/placement_routage.svg", o.sortie), &m, &choisi, &rt_opt, &format!("Placement ({}) - routage global", nom_choisi)).map_err(|e| e.to_string())?;
    sortie::svg_global(&format!("{}/placement_v3_routage_global.svg", o.sortie), &m, &v3, &rt_v3, "Placement v3 - routage global").map_err(|e| e.to_string())?;
    let mut md = String::new();
    md.push_str(&format!("Routeur global commun : pas {} mm, 3 couches (TopLayer, MidLayer2, BottomLayer), capacite 1, {} iterations PathFinder.\n\n", o.pas, ITERS_MESURE));
    md.push_str("| Mesure (meme routeur global) | Placement v3 | Placement retenu |\n|---|---:|---:|\n");
    let row = |md: &mut String, n: &str, a: String, b: String| md.push_str(&format!("| {} | {} | {} |\n", n, a, b));
    row(&mut md, "Longueur routee des reseaux de signal (mm)", format!("{:.1}", lv3.ms.longueur), format!("{:.1}", lopt.ms.longueur));
    row(&mut md, "Vias de signal", format!("{}", lv3.ms.vias_signal), format!("{}", lopt.ms.vias_signal));
    row(&mut md, "Debordement de capacite (cellules en exces)", format!("{}", lv3.ms.debordement), format!("{}", lopt.ms.debordement));
    row(&mut md, "Connexions impossibles", format!("{}", lv3.ms.echecs), format!("{}", lopt.ms.echecs));
    row(&mut md, "Objectif de routage", format!("{:.1}", lv3.ms.objectif_routage), format!("{:.1}", lopt.ms.objectif_routage));
    row(&mut md, "Decouplages a plus de 4 mm", format!("{}", lv3.decaps_loin), format!("{}", lopt.decaps_loin));
    row(&mut md, "HPWL hors GND/+8V (reference, mm)", format!("{:.1}", lv3.hpwl), format!("{:.1}", lopt.hpwl));
    md.push_str(&format!("\nRecuit global : {:.1} s ; {} chaines x {} mouvements.\n", t_opt, o.chaines, o.mouvements));
    if !infos.is_empty() {
        md.push_str("\n| Graine | Cout de depart | Cout final (mesure commune) |\n|---|---:|---:|\n");
        for (g, a, b) in &infos {
            md.push_str(&format!("| {} | {:.1} | {:.1} |\n", g, a, b));
        }
    }
    md.push_str(&md_boucle);
    if let Some((dt, st)) = &best.det {
        log(format!("routage detaille ({}) : {}", nom_choisi, st));
        let rj = sortie::routage_json(dt, st.clone());
        std::fs::write(format!("{}/routage_detaille.json", o.sortie), serde_json::to_string_pretty(&rj).unwrap()).map_err(|e| e.to_string())?;
        sortie::svg_detail(&format!("{}/routage_detaille.svg", o.sortie), dt, &format!("Routage detaille - {}", nom_choisi)).map_err(|e| e.to_string())?;
        let vide = json!({});
        let sv = if let Some((dv, sv)) = &det_v3 {
            let rj = sortie::routage_json(dv, sv.clone());
            std::fs::write(format!("{}/routage_detaille_v3.json", o.sortie), serde_json::to_string_pretty(&rj).unwrap()).map_err(|e| e.to_string())?;
            sv
        } else {
            &vide
        };
        md.push_str("\n### Routage detaille (meme routeur)\n\n| Mesure | Placement v3 | Placement retenu |\n|---|---:|---:|\n");
        for (k, lab) in [("connexions_reussies", "Connexions reussies"), ("connexions_echouees", "Connexions en echec"),
            ("reseaux_complets_selon_routeur", "Reseaux complets (selon le routeur)"), ("reseaux_a_router", "Reseaux a router"),
            ("longueur_mm", "Longueur de piste (mm)"), ("vias", "Vias"), ("vias_doubles", "dont vias doubles"),
            ("longueur_reduite_mm", "Longueur retrecie (mm)"), ("resistance_reseaux_puissance_mohm", "Resistance des reseaux de puissance (mOhm)"), ("temps_s", "Temps (s)")] {
            md.push_str(&format!("| {} | {} | {} |\n", lab, sv.get(k).cloned().unwrap_or(json!("-")), st[k]));
        }
    }
    std::fs::write(format!("{}/comparatif.md", o.sortie), &md).map_err(|e| e.to_string())?;
    log(md.clone());
    log(format!("termine en {:.1} s ; sorties dans {}", t_tot.elapsed().as_secs_f64(), o.sortie));
    Ok(())
}

/// Optimisation conjointe placement + routage detaille (iteration 6).
fn run_conjoint(o: &Opts, m: &Modele, log: &mut dyn FnMut(String)) -> Result<(), String> {
    let t = Instant::now();
    let dep = o.depart.as_ref().ok_or("--conjoint exige --depart (placement pose)")?;
    let e0 = m.etat_depuis(&donnees::lire_placement(dep)?)?;
    std::fs::create_dir_all(&o.sortie_conj).map_err(|e| e.to_string())?;
    let mut base = detail::Detail::new(m, &e0);
    base.via_dans_pad = false;
    if let Some(rf) = &o.routage_depart {
        let nb = base.charger_routage_json(rf)?;
        log(format!("etat de depart : placement {} + routage {} charge tel quel ({} objets)", dep, rf, nb));
    } else {
        base.router_tout(o.iters_det, o.tours_rr, &mut |s| println!("{}", s));
    }
    let ko0 = base.total_ko();
    log(format!("etat de depart : {} connexions en echec ({:.0} s)", ko0, t.elapsed().as_secs_f64()));
    if ko0 > 0 {
        return Err("etat de depart invalide : le routage du placement de depart n'est pas complet".into());
    }
    let p = conjoint::ParamConj { budget_s: o.budget_s, t0: 3.0, t_fin: 0.05, sigma: 1.5, prox_poids: 0.0, zone_periode: 0 };
    let jl = |s: String| println!("{}", s);
    let res: Vec<conjoint::ResConj> = (0..o.chaines_conj)
        .into_par_iter()
        .map(|k| conjoint::chaine(&base, o.graine * 100 + k as u64 + 1, &p, &jl))
        .collect();
    for r in &res {
        log(format!(
            "chaine graine {} : cout {:.1} -> {:.1} ; essais {} acceptes {} illegaux {} refuses par le routage {}",
            r.graine, r.cout_depart, r.cout, r.essais, r.acceptes, r.illegaux, r.refuses_routage
        ));
    }
    let b = res.iter().min_by(|a, b| a.cout.partial_cmp(&b.cout).unwrap()).unwrap();
    // reconstruction exacte du meilleur etat valide
    let mut d = base.clone();
    let mut deplaces = Vec::new();
    for i in 0..m.comps.len() {
        if d.e.poses[i] != b.etat.poses[i] {
            let (x0, y0) = m.centre(i, &d.e.poses[i]);
            let (x1, y1) = m.centre(i, &b.etat.poses[i]);
            deplaces.push(json!({"piece": m.comps[i].nom, "deplacement_mm": (((x1 - x0).powi(2) + (y1 - y0).powi(2)).sqrt() * 1000.0).round() / 1000.0,
                "rotation_avant": d.e.poses[i].rot, "rotation_apres": b.etat.poses[i].rot, "face_changee": d.e.poses[i].bot != b.etat.poses[i].bot}));
            d.deplacer(i, b.etat.poses[i]);
        }
    }
    d.charger_reseaux(&b.nets);
    log(format!("meilleur etat (graine {}) : cout {:.1} (depart {:.1}) ; {} pieces deplacees ; {} connexions en echec", b.graine, b.cout, b.cout_depart, deplaces.len(), d.total_ko()));
    d.finaliser(&o.esp, &mut |s| println!("{}", s));
    let nets_ok = d.a_router().iter().filter(|&&n| d.nets[n].ko == 0).count();
    let st = json!({"connexions_reussies": d.total_ok(), "connexions_echouees": d.total_ko(), "reseaux_complets_selon_routeur": nets_ok,
        "reseaux_a_router": d.a_router().len(), "cout_depart": b.cout_depart, "cout_final_avant_finition": b.cout,
        "pieces_deplacees": deplaces.len(), "temps_s": t.elapsed().as_secs_f64()});
    let pj = sortie::placement_json(m, &d.e);
    std::fs::write(format!("{}/placements_routage.json", o.sortie_conj), serde_json::to_string_pretty(&pj).unwrap()).map_err(|e| e.to_string())?;
    let rj = sortie::routage_json(&d, st.clone());
    std::fs::write(format!("{}/routage_detaille.json", o.sortie_conj), serde_json::to_string_pretty(&rj).unwrap()).map_err(|e| e.to_string())?;
    std::fs::write(format!("{}/deplacements.json", o.sortie_conj), serde_json::to_string_pretty(&json!(deplaces)).unwrap()).map_err(|e| e.to_string())?;
    sortie::svg_detail(&format!("{}/routage_detaille.svg", o.sortie_conj), &d, "Optimisation conjointe placement + routage").map_err(|e| e.to_string())?;
    log(format!("conjoint : {} ; sorties dans {} ({:.0} s)", st, o.sortie_conj, t.elapsed().as_secs_f64()));
    Ok(())
}

/// Campagne complete depuis zero (iteration 7) pour une graine.
fn run_campagne(o: &Opts, m: &Modele, log: &mut dyn FnMut(String)) -> Result<(), String> {
    let t = Instant::now();
    let dir = &o.sortie;
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let ecrire_stats = |v: &serde_json::Value| std::fs::write(format!("{}/stats.json", dir), serde_json::to_string_pretty(v).unwrap());
    let e0 = match recuit::placement_aleatoire(m, o.graine) {
        Some(e) => e,
        None => {
            let _ = ecrire_stats(&json!({"graine": o.graine, "valide": false, "cause": "placement aleatoire impossible", "temps_s": t.elapsed().as_secs_f64()}));
            return Ok(());
        }
    };
    let hp0 = m.hpwl(&e0);
    log(format!("graine {} : placement aleatoire legal (HPWL {:.0} mm, {:.0} s)", o.graine, hp0, t.elapsed().as_secs_f64()));
    let groupes = recuit::groupes_fonctionnels(m);
    log(format!("{} groupes fonctionnels (dont 2 canaux complets)", groupes.len()));
    let envf = |k: &str, d: f64| std::env::var(k).ok().and_then(|v| v.parse::<f64>().ok()).unwrap_or(d);
    let pc = recuit::ParamCampagne { mouvements_max: o.mouvements, alpha: envf("CAMP_ALPHA", 0.99), palier_par_piece: envf("CAMP_PALIER", 100.0) as usize, acc_initiale: 0.85, pas: o.pas,
        periode: 3000, t_fin_rel: 1e-6, rudy_cap: envf("CAMP_RUDY_CAP", 2.0), rudy_lambda: envf("CAMP_RUDY_LAMBDA", 2.0) };
    {
        let v3 = m.etat_v3();
        let r = recuit::Rudy::new(m, &v3, pc.rudy_cap, pc.rudy_lambda);
        let (mx, p95, dep) = r.stats();
        log(format!("reference v3 : HPWL {:.0} mm ; congestion RUDY max {:.2} p95 {:.2} mm/mm2, depassement {:.0} mm", m.hpwl(&v3), mx, p95, dep));
    }
    let jl = |s: String| println!("{}", s);
    let rs = recuit::chaine_campagne(m, &e0, o.graine, &pc, &groupes, &jl);
    log(format!(
        "recuit global : cout {:.1} -> {:.1} ; T0 {:.2} ; acceptation initiale mesuree {:.0} % ; {} paliers, {} rechauffes, {} mouvements, {:.0} s ; HPWL {:.0} mm",
        rs.cout_depart, rs.cout, rs.t0, 100.0 * rs.acc_initiale_mesuree, rs.paliers, rs.rechauffes, rs.mouvements, rs.temps, m.hpwl(&rs.etat)
    ));
    if m.bloc_libre {
        let v3 = m.etat_v3();
        log(format!("convertisseur : boucle chaude {:.1} mm2 (bloc manuel v3 : {:.1}) ; demi-perimetre noeud de commutation {:.1} mm (v3 : {:.1}) ; cout dedie {:.0} (v3 : {:.0})",
            m.boucle_chaude(&rs.etat).unwrap_or(-1.0), m.boucle_chaude(&v3).unwrap_or(-1.0),
            m.sw_net().map(|n| m.hpwl_net(n, &rs.etat)).unwrap_or(0.0), m.sw_net().map(|n| m.hpwl_net(n, &v3)).unwrap_or(0.0), m.cout_hacheur(&rs.etat), m.cout_hacheur(&v3)));
    }
    if std::env::var("CAMP_SA_SEUL").is_ok() {
        let pj = sortie::placement_json(m, &rs.etat);
        std::fs::write(format!("{}/placements_routage.json", dir), serde_json::to_string_pretty(&pj).unwrap()).map_err(|e| e.to_string())?;
        return Ok(());
    }
    let sa = json!({"cout_depart": rs.cout_depart, "cout": rs.cout, "t0": rs.t0, "acceptation_initiale": rs.acc_initiale_mesuree, "paliers": rs.paliers,
        "rechauffes": rs.rechauffes, "mouvements": rs.mouvements, "temps_s": rs.temps, "hpwl_depart": hp0, "hpwl": m.hpwl(&rs.etat)});
    // routage detaille complet
    let mut etat = rs.etat.clone();
    let mut base = detail::Detail::new(m, &etat);
    base.router_tout(o.iters_det, o.tours_rr, &mut |s| println!("{}", s));
    log(format!("routage detaille : {} connexions en echec ({:.0} s)", base.total_ko(), t.elapsed().as_secs_f64()));
    // boucle placement <-> routage detaille : zones de pression autour des echecs, recuit local des pieces voisines
    let mut zones: Vec<(f64, f64, f64, f64, Option<usize>)> = Vec::new();
    let mut poids = 30.0;
    let mut tours_boucle = 0;
    for tour in 0..o.boucle {
        let ko = base.total_ko();
        if ko == 0 {
            break;
        }
        tours_boucle += 1;
        for z in zones_echecs(&base, m, &etat, poids) {
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
                zones.iter().any(|z| ((z.0 - x).powi(2) + (z.1 - y).powi(2)).sqrt() < z.2 + 4.0)
            })
        }).collect();
        if movers.is_empty() {
            log("boucle : aucune piece mobile pres des echecs".into());
            break;
        }
        let p = recuit::Params { mouvements: o.mouv_local, periode: o.periode, iters_pf: 4, t0_facteur: 0.15, pas: o.pas, sigma0: 2.5, mobiles: Some(movers.clone()) };
        let res: Vec<recuit::Resultat> = (0..o.chaines.max(1)).into_par_iter().map(|k| recuit::chaine(&mr, &etat, o.graine * 1000 + tour as u64 * 16 + k as u64, &p)).collect();
        let mut mes: Vec<(f64, usize)> = res.par_iter().enumerate().map(|(k, r)| {
            let (l, _) = mesurer(&mr, &r.etat, o.pas);
            let pr: f64 = (0..mr.comps.len()).map(|i| mr.pression_piece(i, &r.etat.poses[i])).sum();
            (l.ms.objectif_routage + l.extra + pr, k)
        }).collect();
        mes.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
        let cand = res[mes[0].1].etat.clone();
        let mut d2 = detail::Detail::new(m, &cand);
        d2.router_tout(o.iters_det, o.tours_rr, &mut |s| println!("{}", s));
        let kn = d2.total_ko();
        log(format!("boucle {} : {} zones, {} pieces deplacables ; echecs {} -> {} ({}) ({:.0} s)", tour + 1, zones.len(), movers.len(), ko, kn,
            if kn < ko { "retenu" } else { "rejete, poids x2" }, t.elapsed().as_secs_f64()));
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
    let ko = base.total_ko();
    let _ = &etat;
    let pj = sortie::placement_json(m, &base.e);
    std::fs::write(format!("{}/placements_routage.json", dir), serde_json::to_string_pretty(&pj).unwrap()).map_err(|e| e.to_string())?;
    // peu d'echecs : la phase conjointe (cout 1e5 par connexion en echec, pieces voisines tirees en priorite) tente
    // de les resorber ; au-dela, la campagne s'arrete ici (invalide)
    if ko > 10 {
        let echecs: Vec<String> = base.nets.iter().flat_map(|r| r.diag.iter().cloned()).collect();
        let _ = ecrire_stats(&json!({"graine": o.graine, "valide": false, "cause": "routage detaille incomplet", "connexions_echouees": ko,
            "echecs": echecs, "recuit": sa, "tours_boucle": tours_boucle, "temps_s": t.elapsed().as_secs_f64()}));
        return Ok(());
    }
    // phase conjointe (cout sur le routage detaille, espacement compris, reroutage de zone periodique)
    let p = conjoint::ParamConj { budget_s: o.budget_s, t0: 3.0, t_fin: 0.05, sigma: 1.5, prox_poids: 1.0, zone_periode: 40 };
    let rc = conjoint::chaine(&base, o.graine * 7 + 3, &p, &jl);
    log(format!("phase conjointe : cout {:.1} -> {:.1} ; essais {} acceptes {} illegaux {} refuses {}", rc.cout_depart, rc.cout, rc.essais, rc.acceptes, rc.illegaux, rc.refuses_routage));
    let mut d = base.clone();
    for i in 0..m.comps.len() {
        if d.e.poses[i] != rc.etat.poses[i] {
            d.deplacer(i, rc.etat.poses[i]);
        }
    }
    d.charger_reseaux(&rc.nets);
    d.finaliser(&o.esp, &mut |s| println!("{}", s));
    // reste quelques echecs : arrachage-reroutage cible (voir Detail::reparer_echecs)
    if d.total_ko() > 0 {
        d.reparer_echecs(&mut |s| println!("{}", s));
        log(format!("finition ciblee : {} connexions en echec", d.total_ko()));
    }
    // toujours des echecs : routage detaille complet refait sur le placement final, negociation plus longue
    if d.total_ko() > 0 && d.total_ko() <= 5 {
        log(format!("finition : {} connexions en echec, nouveau routage complet (80 iterations, 8 tours)", d.total_ko()));
        let mut d2 = detail::Detail::new(m, &d.e);
        d2.router_tout(80, 8, &mut |s| println!("{}", s));
        d2.finaliser(&o.esp, &mut |s| println!("{}", s));
        log(format!("finition : {} -> {} connexions en echec", d.total_ko(), d2.total_ko()));
        if d2.total_ko() < d.total_ko() {
            d = d2;
        }
    }
    let st = json!({"graine": o.graine, "valide": d.total_ko() == 0, "cause": if d.total_ko() == 0 { "" } else { "echecs apres finition" }, "connexions_echouees": d.total_ko(), "echecs": d.nets.iter().flat_map(|r| r.diag.iter().cloned()).collect::<Vec<String>>(), "recuit": sa, "tours_boucle": tours_boucle,
        "conjoint": {"cout_depart": rc.cout_depart, "cout": rc.cout, "essais": rc.essais, "acceptes": rc.acceptes},
        "temps_s": t.elapsed().as_secs_f64()});
    let pj = sortie::placement_json(m, &d.e);
    std::fs::write(format!("{}/placements_routage.json", dir), serde_json::to_string_pretty(&pj).unwrap()).map_err(|e| e.to_string())?;
    let rj = sortie::routage_json(&d, st.clone());
    std::fs::write(format!("{}/routage_detaille.json", dir), serde_json::to_string_pretty(&rj).unwrap()).map_err(|e| e.to_string())?;
    let _ = ecrire_stats(&st);
    log(format!("campagne {} terminee : {} ({:.0} s)", o.graine, st, t.elapsed().as_secs_f64()));
    Ok(())
}
