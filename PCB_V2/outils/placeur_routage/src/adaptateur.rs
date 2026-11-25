//! Modèle interne du cœur (`geom::Modele`, hérité de `9b5b5a3`) construit depuis `donnees_placeur/2` (étape 3 du
//! lot A).
//!
//! Tout vient des données : contour et découpes, trous, couches de routage et plans, règles (isolements, largeurs,
//! vias, gardes), pièces et pastilles (géométrie du cuivre, perçages), régions d'empreinte, convertisseurs,
//! découplages, éloignements, pièces sans piste dessous. L'adaptateur remplit la structure d'entrée du cœur
//! (`donnees_v1`), appelle `Modele::new`, puis pose ce que l'ancien chemin lisait dans des fichiers à part
//! (géométrie réelle des pastilles, régions, configuration de carte) et libère toutes les pièces non fixes
//! (fonctionnement par défaut de `9b5b5a3`).
//!
//! Repère interne : celui des données translaté pour que le coin bas-gauche de la boîte de la carte soit à (0, 0),
//! translation arrondie au pas de grille du cœur (les poses restent sur la grille des données). Les sorties sont
//! écrites dans le repère des données (`Modele::origine`).
//!
//! Limites connues du cœur, signalées par un avertissement : 3 couches de routage au plus (dessus, dessous, une
//! interne) ; un réseau n'est traité en plan que s'il a une seule zone de plan, sans trou, seule sur sa couche (sinon
//! il est routé en pistes) ; rotations au quart de tour ; zones interdites de routage, cuivre fixe et vias
//! d'empreinte non pris en compte par le routeur (la vérification finale les contrôle).

use crate::donnees::{self, Donnees, RoleCouche};
use crate::donnees_v1 as v1;
use crate::geom::{self, Etat, FormePad, Modele, Pose, Statut, ZoneR};
use crate::parametres::Parametres;
use std::collections::{BTreeMap, HashMap};

/// Rotation au quart de tour la plus proche (degrés entiers 0, 90, 180, 270) et écart.
fn quart(r: f64) -> (i32, f64) {
    let q = ((r.rem_euclid(360.0) / 90.0).round() as i32).rem_euclid(4) * 90;
    let e = (r.rem_euclid(360.0) - q as f64).abs();
    (q, e.min(360.0 - e))
}

fn boite(points: &[[f64; 2]]) -> [f64; 4] {
    let mut b = [f64::MAX, f64::MAX, f64::MIN, f64::MIN];
    for p in points {
        b[0] = b[0].min(p[0]);
        b[1] = b[1].min(p[1]);
        b[2] = b[2].max(p[0]);
        b[3] = b[3].max(p[1]);
    }
    b
}

/// Contour rectangulaire aligné sur sa boîte (le cœur garde alors son test rectangulaire, comme `9b5b5a3`).
fn est_rectangle(points: &[[f64; 2]], b: &[f64; 4]) -> bool {
    points.len() == 4
        && points.iter().all(|p| {
            ((p[0] - b[0]).abs() < 1e-6 || (p[0] - b[2]).abs() < 1e-6) && ((p[1] - b[1]).abs() < 1e-6 || (p[1] - b[3]).abs() < 1e-6)
        })
}

/// Modèle du cœur et avertissements de l'adaptation.
pub struct Adapte {
    pub modele: Modele,
    pub avertissements: Vec<String>,
}

/// Construit le modèle interne du cœur depuis les données `donnees_placeur/2` (déjà contrôlées par `donnees::lire`).
pub fn construire(d: &Donnees, param: Parametres) -> Result<Adapte, String> {
    let mut av: Vec<String> = Vec::new();
    geom::fixer_pas_snap(d.regles.grille_placement_mm);
    let pas = geom::pas_snap();
    let bc = d.carte.boite;
    let ox = (bc[0] / pas).floor() * pas;
    let oy = (bc[1] / pas).floor() * pas;
    let tr = |p: &[f64; 2]| [p[0] - ox, p[1] - oy];

    // ------------------------------------------------------------------ couches de routage du cœur et plans
    let routage: Vec<&donnees::Couche> = d.couches.iter().filter(|c| c.role == RoleCouche::Routage).collect();
    if routage.is_empty() {
        return Err("aucune couche de routage dans les données".to_string());
    }
    let mut noms = [routage[0].nom.clone(), routage[routage.len() - 1].nom.clone(), String::new()];
    let mut actives = [true, routage.len() >= 2, routage.len() >= 3];
    if routage.len() >= 3 {
        noms[2] = routage[1].nom.clone();
    }
    if routage.len() > 3 {
        av.push(format!(
            "{} couches de routage : le cœur route sur 3 ({}, {}, {}), les autres restent libres",
            routage.len(),
            noms[0],
            noms[1],
            noms[2]
        ));
    }
    if routage.len() == 1 {
        noms[1] = noms[0].clone();
        actives[1] = false;
    }
    let cuivre: Vec<&donnees::Couche> = d.couches.iter().filter(|c| c.role != RoleCouche::Inutilisee).collect();
    let via_de_a = [cuivre[0].nom.clone(), cuivre[cuivre.len() - 1].nom.clone()];
    // réseaux traités en plan : une seule zone de plan, sans trou, seule sur sa couche
    let mut zones: BTreeMap<&str, Vec<(&str, usize, bool)>> = BTreeMap::new();
    for c in &d.couches {
        if c.role != RoleCouche::Plan {
            continue;
        }
        for z in &c.zones_plan {
            zones.entry(z.reseau.as_str()).or_default().push((c.nom.as_str(), c.zones_plan.len(), z.trous.is_empty()));
        }
    }
    let mut plans: Vec<String> = Vec::new();
    for r in &d.reseaux {
        match zones.get(r.nom.as_str()) {
            Some(z) if z.len() == 1 && z[0].1 == 1 && z[0].2 => plans.push(r.nom.clone()),
            Some(_) => av.push(format!("réseau {} : plan partagé ou découpé, routé en pistes", donnees::court(&r.nom))),
            None => {}
        }
    }

    // ------------------------------------------------------------------ règles
    let defaut_iso = d
        .reseaux
        .iter()
        .filter(|r| r.role != donnees::Role::Commutation)
        .map(|r| r.isolement_mm)
        .fold(d.regles.isolement.defaut, f64::max);
    if defaut_iso > d.regles.isolement.defaut + 1e-9 {
        av.push(format!(
            "isolement de réseau supérieur au défaut : le cœur isole tous les réseaux à {:.4} mm",
            defaut_iso
        ));
    }
    let commutation_iso = d
        .reseaux
        .iter()
        .filter(|r| r.role == donnees::Role::Commutation)
        .map(|r| r.isolement_mm)
        .fold(defaut_iso, f64::max);
    // classe du cœur : rôle commutation, sinon classe des données (« defaut » si vide)
    let classe_coeur = |r: &donnees::Reseau| -> String {
        if r.role == donnees::Role::Commutation {
            "COMMUTATION".to_string()
        } else if r.classe.is_empty() {
            "defaut".to_string()
        } else {
            r.classe.clone()
        }
    };
    let mut largeurs: HashMap<String, [f64; 3]> = HashMap::new();
    let mut classes: HashMap<String, String> = HashMap::new();
    let mut poids: HashMap<String, f64> = HashMap::new();
    for r in &d.reseaux {
        let k = classe_coeur(r);
        let l = [r.largeur_min, r.largeur_pref.max(r.largeur_min), r.largeur_max.unwrap_or(f64::MAX)];
        largeurs
            .entry(k.clone())
            .and_modify(|e| {
                e[0] = e[0].max(l[0]);
                e[1] = e[1].max(l[1]);
                e[2] = e[2].min(l[2]);
            })
            .or_insert(l);
        if k != "defaut" {
            classes.insert(r.nom.clone(), k);
        }
        poids.insert(r.nom.clone(), r.poids);
    }
    for l in largeurs.values_mut() {
        if l[2] == f64::MAX || l[2] < l[1] {
            l[2] = l[1].max(3.0);
        }
    }
    let pas_fin: Vec<String> = d.composants.iter().filter(|c| c.pas_fin).map(|c| c.nom.clone()).collect();
    let via_sous: Vec<String> = d
        .composants
        .iter()
        .filter(|c| !c.pastilles.is_empty() && c.pastilles.iter().all(|p| p.via_dans_pastille_permis))
        .map(|c| c.nom.clone())
        .collect();
    let regles = v1::Regles {
        isolement: v1::Isolement { defaut: defaut_iso, commutation: commutation_iso, pastilles_pas_fin: defaut_iso, pieces_pas_fin: pas_fin },
        largeurs,
        via: v1::ViaR { diametre: d.regles.via.diametre, trou: d.regles.via.trou, via_sous_cms_permis: via_sous },
        garde_bord_fentes_trous: d.carte.garde_bord_mm.max(d.carte.garde_decoupe_mm).max(d.regles.garde_trou_mm),
        rayon_trou_m3: 0.0,
    };

    // ------------------------------------------------------------------ contour, découpes, trous, obstacles
    let w = bc[2] - ox;
    let h = bc[3] - oy;
    let contour_pts: Vec<[f64; 2]> = if est_rectangle(&d.carte.contour, &bc) { Vec::new() } else { d.carte.contour.iter().map(tr).collect() };
    let mut obstacles: Vec<v1::ObsIn> = Vec::new();
    let mut fentes = Vec::new();
    for dc in &d.carte.decoupes {
        let b = boite(&dc.points.iter().map(tr).collect::<Vec<_>>());
        fentes.push([b[0], b[1], b[2] - b[0], b[3] - b[1]]);
        let g = d.carte.marge_decoupe_mm;
        for face in 0..2 {
            obstacles.push(v1::ObsIn { face, rect: [b[0] - g, b[1] - g, b[2] + g, b[3] + g] });
        }
    }
    let mut trous = Vec::new();
    let mut trous_r = Vec::new();
    for t in &d.trous {
        if t.piece.as_ref().map(|p| d.index_composants.get(p).map(|&i| d.composants[i].mobile).unwrap_or(false)).unwrap_or(false) {
            av.push(format!("trou {} d'une pièce mobile : traité à sa position des données", donnees::court(&t.id)));
        }
        let [x, y] = tr(&[t.x, t.y]);
        let r = (t.diametre_cuivre.max(t.trou.diametre) / 2.0).max(t.trou.longueur / 2.0);
        trous.push([x, y]);
        trous_r.push(r);
        let ri = t.rayon_interdit_mm.max(r);
        for face in 0..2 {
            obstacles.push(v1::ObsIn { face, rect: [x - ri, y - ri, x + ri, y + ri] });
        }
    }
    for o in &d.obstacles_placement {
        let b = match &o.forme {
            donnees::Forme::Rect(r) => {
                let a = tr(&[r[0], r[1]]);
                let c = tr(&[r[2], r[3]]);
                [a[0], a[1], c[0], c[1]]
            }
            donnees::Forme::Polygone(p) => boite(&p.iter().map(tr).collect::<Vec<_>>()),
        };
        for f in &o.faces {
            obstacles.push(v1::ObsIn { face: if f == "dessous" { 1 } else { 0 }, rect: b });
        }
    }
    for z in &d.zones_routage_interdites {
        if z.effets.iter().any(|e| e == "placement") {
            let b = boite(&z.points.iter().map(tr).collect::<Vec<_>>());
            let g = z.largeur / 2.0;
            for face in 0..2 {
                obstacles.push(v1::ObsIn { face, rect: [b[0] - g, b[1] - g, b[2] + g, b[3] + g] });
            }
        }
    }
    if !d.cuivre_fixe.is_empty() {
        av.push(format!("{} objet(s) de cuivre fixe : non vus par le routeur", d.cuivre_fixe.len()));
    }
    let vias_empreinte: usize = d.composants.iter().map(|c| c.vias.len()).sum();
    if vias_empreinte > 0 {
        av.push(format!("{} via(s) d'empreinte : non vus par le routeur", vias_empreinte));
    }

    // ------------------------------------------------------------------ pièces
    let mut composants = Vec::new();
    let mut hors_quart = Vec::new();
    for c in &d.composants {
        let pose = |p: &donnees::Pose| -> (f64, f64, i32, bool) {
            let (q, _) = quart(p.rot);
            (p.x - ox, p.y - oy, q, p.dessous)
        };
        if quart(c.pose_cible.rot).1 > 1e-6 && !c.virtuel {
            hors_quart.push(c.nom.clone());
        }
        let mut orientations: Vec<(i32, bool)> = Vec::new();
        for &(r, b) in &c.orientations {
            let (q, e) = quart(r);
            if e < 1e-6 && !orientations.contains(&(q, b)) {
                orientations.push((q, b));
            }
        }
        if orientations.is_empty() {
            orientations.push((pose(&c.pose_cible).2, c.pose_cible.dessous));
        }
        let pastilles = if c.virtuel {
            Vec::new()
        } else {
            c.pastilles
                .iter()
                .map(|p| {
                    let m = p.expansion_masque.max(0.0);
                    v1::PadIn { nom: p.nom.clone(), reseau: p.reseau.clone(), lx: p.lx, ly: p.ly, hw: p.hw + m, hh: p.hh + m, th: p.th }
                })
                .collect()
        };
        let regions = c
            .regions
            .iter()
            .map(|r| v1::RegionIn {
                face: r.face.clone(),
                pastille: r.pastille.as_ref().and_then(|id| d.index_pastilles.get(id)).map(|&(i, k)| d.composants[i].pastilles[k].nom.clone()),
                reseau: if r.reseau.is_empty() { None } else { Some(r.reseau.clone()) },
                points: r.points.clone(),
            })
            .collect();
        composants.push(v1::CompIn {
            nom: c.nom.clone(),
            statut: if c.mobile && !c.virtuel { "mobile".to_string() } else { "fixe".to_string() },
            lbb: c.lbb,
            pastilles,
            pose_actuelle: pose(&c.pose_actuelle),
            pose_cible: pose(&c.pose_cible),
            orientations,
            regions,
        });
    }
    if !hors_quart.is_empty() {
        av.push(format!(
            "{} pièce(s) hors quart de tour, posées au quart de tour le plus proche par le routeur : {}",
            hors_quart.len(),
            hors_quart.iter().take(8).map(|s| donnees::court(s)).collect::<Vec<_>>().join(", ")
        ));
    }

    // ------------------------------------------------------------------ découplages, éloignements souples
    let pad_ref = |id: &str| d.index_pastilles.get(id).copied();
    let mut decouplages = Vec::new();
    for x in &d.decouplages {
        let (i, k) = match pad_ref(&x.pastille) {
            Some(v) => v,
            None => continue,
        };
        if d.composants[i].virtuel {
            continue;
        }
        let broches: Vec<(String, usize)> = x.broches_ci.iter().filter_map(|b| pad_ref(b)).map(|(a, b)| (d.composants[a].nom.clone(), b)).collect();
        if !broches.is_empty() {
            decouplages.push(v1::DecIn { comp: d.composants[i].nom.clone(), pastille: k, broches_ci: broches });
        }
    }
    let soft_sw: Vec<String> = d
        .eloignements
        .iter()
        .filter(|e| !e.dure)
        .flat_map(|e| e.objets.iter().filter(|o| d.index_composants.contains_key(*o)).cloned())
        .collect();

    let entree = v1::Donnees {
        contour: v1::Contour { w, h, points: contour_pts },
        marge_bord: d.carte.marge_bord_mm,
        composants,
        obstacles_placement: obstacles,
        bloc: v1::BlocIn::default(),
        trous_m3: trous.clone(),
        fentes,
        soft_sw,
        decouplages,
        poids_reseaux: poids,
        reseaux_plans: plans,
        classes,
        regles,
    };
    let mut m = Modele::new(&entree, param);

    // ------------------------------------------------------------------ géométrie réelle des pastilles
    for (i, c) in d.composants.iter().enumerate() {
        if c.virtuel {
            continue;
        }
        for (k, p) in c.pastilles.iter().enumerate() {
            let q = &mut m.comps[i].pads[k];
            q.hw = p.hw;
            q.hh = p.hh;
            q.th = p.th;
            q.trou = p.trou.as_ref().map(|t| t.diametre.max(t.longueur)).unwrap_or(0.0);
            q.forme = match p.forme.as_str() {
                "rond" | "oblong" => FormePad::Rond,
                // polygone (octogone d'Altium, chanfreins) : rectangle chanfreine qui le contient exactement quand
                // c'est un octogone (chanfrein lu sur les sommets)
                "polygone" if !p.points.is_empty() => {
                    let k = p.points.iter().map(|q| (q[0] - p.lx).abs() + (q[1] - p.ly).abs()).fold(0.0f64, f64::max);
                    let c = (p.hw + p.hh - k).max(0.0).min(p.hw.min(p.hh));
                    if c > 1e-6 { FormePad::Octo(c) } else { FormePad::Rect }
                }
                _ => FormePad::Rect,
            };
        }
        m.comps[i].has_th = m.comps[i].pads.iter().any(|p| p.th);
    }
    m.trous = trous;
    m.trous_r = trous_r;
    let par: Vec<(String, Vec<v1::RegionIn>)> =
        entree.composants.iter().filter(|c| !c.regions.is_empty()).map(|c| (c.nom.clone(), c.regions.clone())).collect();
    m.charger_regions(&par);
    for n in m.nets.iter_mut() {
        if let Some(&r) = d.index_reseaux.get(&n.nom) {
            n.role = match d.reseaux[r].role {
                donnees::Role::Signal => "signal",
                donnees::Role::Alimentation => "alimentation",
                donnees::Role::Puissance => "puissance",
                donnees::Role::Commutation => "commutation",
                donnees::Role::Plan => "plan",
            }
            .to_string();
        }
    }

    // ------------------------------------------------------------------ configuration de carte
    let premier = |l: &[String]| l.first().cloned().unwrap_or_default();
    let mut cfg = geom::ConfigCarte { convertisseurs: Vec::new(), sans_piste_dessous: d.sans_piste_dessous.clone() };
    for cv in &d.convertisseurs {
        let mut eloignement = Vec::new();
        for e in d.eloignements.iter().filter(|e| e.dure && e.reference.iter().any(|r| *r == cv.haut)) {
            let mut broches = Vec::new();
            for o in &e.objets {
                if let Some((i, k)) = pad_ref(o) {
                    broches.push(format!("{}.{}", d.composants[i].nom, d.composants[i].pastilles[k].nom));
                } else if let Some(&i) = d.index_composants.get(o) {
                    for p in &d.composants[i].pastilles {
                        broches.push(format!("{}.{}", d.composants[i].nom, p.nom));
                    }
                }
            }
            eloignement.push(geom::Eloignement { broches, distance_mm: e.distance_mm });
        }
        cfg.convertisseurs.push(geom::Convertisseur {
            nom: cv.nom.clone(),
            haut: cv.haut.clone(),
            bas: cv.bas.clone(),
            pilote: cv.pilote.clone(),
            inductance: cv.inductance.clone(),
            cond_entree: cv.cond_entree.clone(),
            cond_bulk: premier(&cv.cond_bulk),
            cond_sortie: premier(&cv.cond_sortie),
            reseau_entree: cv.reseau_entree.clone(),
            reseau_masse: cv.reseau_masse.clone(),
            reseau_sortie: cv.reseau_sortie.clone(),
            reseau_commutation: cv.reseau_commutation.clone(),
            reseaux_courts: cv.reseaux_courts.clone(),
            reseaux_sensibles: cv.reseaux_sensibles.clone(),
            cond_pilote: premier(&cv.cond_pilote),
            eloignement,
        });
    }
    m.cfg = cfg;
    m.liberer_bloc();
    // les pièces d'un convertisseur (groupe compact du hacheur) sont placées par ses termes dédiés (boucle chaude,
    // découplage du pilote, bulk) : pas de terme de découplage générique en plus (9b5b5a3 n'en avait pas ; le double
    // terme tirait C7, C15, C17, C16, C18 contre U10 et M1, là où le routage échoue)
    let hacheur = m.hacheur.clone();
    m.decaps.retain(|x| !hacheur.contains(&x.0));

    // zones interdites de routage (carte et empreintes) : effets « pistes » et « vias », par couche ; une zone
    // d'empreinte suit sa piece (rotation, face : l'empilage est vu en miroir quand la piece est retournee)
    {
        let nco = d.couches.len();
        let rang = |nom: &str| d.couches.iter().position(|c| c.nom == nom);
        let masque = |couches: &Vec<String>, retourne: bool| -> [bool; 3] {
            let mut mk = [false; 3];
            for l in 0..3 {
                if !actives[l] {
                    continue;
                }
                let Some(k) = rang(&noms[l]) else { continue };
                let ks = if retourne { nco - 1 - k } else { k };
                mk[l] = couches.iter().any(|c| c == "toutes" || rang(c) == Some(ks));
            }
            mk
        };
        let zone = |z: &donnees::ZoneInterdite, local: bool, bot_ref: bool| -> Option<ZoneR> {
            let pistes = z.effets.iter().any(|e| e == "pistes");
            let vias = z.effets.iter().any(|e| e == "vias");
            let placement = local && z.effets.iter().any(|e| e == "placement" || e == "pastilles");
            if (!pistes && !vias && !placement) || z.points.is_empty() {
                return None;
            }
            let pts = z.points.iter().map(|p| if local { (p[0], p[1]) } else { let q = tr(p); (q[0], q[1]) }).collect();
            Some(ZoneR { polyligne: z.polyligne, pts, largeur: z.largeur, pistes, vias, placement, masque: masque(&z.couches, false),
                masque_ret: masque(&z.couches, true), bot_ref })
        };
        m.zones_carte = d.zones_routage_interdites.iter().filter_map(|z| zone(z, false, false)).collect();
        for (i, c) in d.composants.iter().enumerate() {
            if c.virtuel {
                continue;
            }
            m.comps[i].zones = c.zones_interdites.iter().filter_map(|z| zone(z, true, c.pose_cible.dessous)).collect();
        }
    }
    // ------------------------------------------------------------------ pièces fixes : obstacles de placement
    let e = m.etat_v3();
    for i in 0..m.comps.len() {
        if m.comps[i].statut == Statut::Fixe && !d.composants[i].virtuel {
            let rs = m.rects_of(i, &e.poses[i]);
            m.obstacles.extend(rs);
        }
    }
    m.poses_donnees = d
        .composants
        .iter()
        .map(|c| if c.mobile && !c.virtuel { None } else { Some((c.pose_cible.x, c.pose_cible.y, c.pose_cible.rot, c.pose_cible.dessous)) })
        .collect();
    m.origine = [ox, oy];
    m.noms_couches = noms;
    m.couches_actives = actives;
    m.via_de_a = via_de_a;
    m.via_dans_pad = d.regles.via_dans_pastille_meme_reseau;
    m.mode_routage = mode_routage(&m.param.routage.mode, &m.param.routage.modes_multi, d.regles.mode_routage.as_deref());
    let idx = |nom: &str| m.nets.iter().position(|n| n.nom == nom);
    let mut groupes = Vec::new();
    for p in &d.paires {
        if let (Some(a), Some(b)) = (idx(&p.positif), idx(&p.negatif)) {
            groupes.push((vec![a, b], p.tolerance_longueur_mm, true));
        }
    }
    for g in &d.longueurs_appariees {
        let v: Vec<usize> = g.reseaux.iter().filter_map(|n| idx(n)).collect();
        if v.len() >= 2 {
            groupes.push((v, g.tolerance_mm, false));
        }
    }
    let bornees: Vec<(usize, f64, f64)> =
        d.longueurs.iter().filter_map(|l| idx(&l.reseau).map(|n| (n, l.min_mm.unwrap_or(0.0), l.max_mm.unwrap_or(f64::INFINITY)))).collect();
    m.groupes_longueur = groupes;
    m.paires = d
        .paires
        .iter()
        .filter_map(|p| match (idx(&p.positif), idx(&p.negatif)) {
            (Some(a), Some(b)) => Some((a, b, p.ecart_mm.unwrap_or(d.regles.isolement.defaut))),
            _ => None,
        })
        .collect();
    m.longueurs_bornees = bornees;
    m.remplir_cache_rects();
    Ok(Adapte { modele: m, avertissements: av })
}

/// Style des pistes (detail::MODE_*) : `routage.mode` ; « auto » = `regles.mode_routage` des données, sinon
/// octogonal ; « multi » = premier mode de `routage.modes_multi` (les autres sont essayés à la fin par moteur.rs).
pub fn mode_routage(mode: &str, multi: &[String], regle: Option<&str>) -> u8 {
    let code = |s: &str| match s {
        "manhattan" => crate::detail::MODE_MANHATTAN,
        "libre" | "plus_court" => crate::detail::MODE_LIBRE,
        _ => crate::detail::MODE_OCTOGONAL,
    };
    match mode {
        "auto" => code(regle.unwrap_or("octogonal")),
        "multi" => code(multi.first().map(|s| s.as_str()).unwrap_or("octogonal")),
        m => code(m),
    }
}

/// État du cœur depuis des poses des données (repère des données ; une par composant).
pub fn etat_depuis(m: &Modele, poses: &[donnees::Pose]) -> Etat {
    let [ox, oy] = m.origine;
    Etat {
        poses: poses
            .iter()
            .map(|p| Pose { x: p.x - ox, y: p.y - oy, rot: quart(p.rot).0, bot: p.dessous })
            .collect(),
        bdx: 0.0,
        bdy: 0.0,
    }
}
