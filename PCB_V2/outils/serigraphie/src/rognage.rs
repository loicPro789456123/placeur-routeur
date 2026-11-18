//! Rognage des traits de serigraphie trop proches des pastilles : on retire de l'axe d'une piste les
//! parametres t ou la capsule passerait a moins de la garde d'une pastille ; les morceaux restants sont
//! conserves (raccourcis / coupes), jamais deplaces. Un arc en violation est decoupe en cordes (points sur
//! l'arc) puis rogne de la meme facon. Les marques de broche 1 / polarite sont donc rognees au minimum.

use crate::donnees::*;
use crate::geom::*;

#[derive(Clone, Debug)]
pub struct Morceau {
    pub a: P,
    pub b: P,
    pub w: f64,
    pub face: Face,
    pub comp: String,
}

pub struct Rognage {
    /// indices des pistes d'origine supprimees (remplacees par leurs morceaux)
    pub pistes_sup: Vec<usize>,
    /// indices des arcs d'origine supprimes (remplaces par des cordes rognees)
    pub arcs_sup: Vec<usize>,
    pub ajouts: Vec<Morceau>,
    /// longueur de serigraphie retiree (mils) par composant
    pub retire: Vec<(String, f64)>,
}

/// forme de garde d'une pastille selon la reference (cuivre = mesure d'Altium, masque = + expansion)
pub fn forme_garde<'a>(q: &'a Pastille, masque: bool) -> &'a Forme {
    if masque {
        &q.masque
    } else {
        &q.cuivre
    }
}

fn g(a: P, b: P, t: f64, s: &Forme, seuil: f64) -> f64 {
    let q = p(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t);
    d_noyaux(&Noyau::Seg(q, q), &s.noyau) - s.r - seuil
}

/// intervalle ouvert (ta, tb) de l'axe [a, b] interdit par la forme s (distance < seuil), ou None
pub fn intervalle_interdit(a: P, b: P, s: &Forme, seuil: f64) -> Option<(f64, f64)> {
    // g convexe en t (distance d'un point mobile sur une droite a un convexe)
    let (mut lo, mut hi) = (0.0f64, 1.0f64);
    for _ in 0..80 {
        let m1 = lo + (hi - lo) / 3.0;
        let m2 = hi - (hi - lo) / 3.0;
        if g(a, b, m1, s, seuil) <= g(a, b, m2, s, seuil) {
            hi = m2;
        } else {
            lo = m1;
        }
    }
    let ts = (lo + hi) / 2.0;
    let gm = g(a, b, ts, s, seuil).min(g(a, b, 0.0, s, seuil)).min(g(a, b, 1.0, s, seuil));
    if gm >= 0.0 {
        return None;
    }
    let ts = if g(a, b, 0.0, s, seuil) == gm { 0.0 } else if g(a, b, 1.0, s, seuil) == gm { 1.0 } else { ts };
    // bord gauche : dernier t sur (g >= 0)
    let ta = if g(a, b, 0.0, s, seuil) < 0.0 {
        -1.0
    } else {
        let (mut x, mut y) = (0.0, ts); // g(x) >= 0, g(y) < 0
        for _ in 0..60 {
            let m = (x + y) / 2.0;
            if g(a, b, m, s, seuil) >= 0.0 {
                x = m
            } else {
                y = m
            }
        }
        x
    };
    let tb = if g(a, b, 1.0, s, seuil) < 0.0 {
        2.0
    } else {
        let (mut x, mut y) = (1.0, ts);
        for _ in 0..60 {
            let m = (x + y) / 2.0;
            if g(a, b, m, s, seuil) >= 0.0 {
                x = m
            } else {
                y = m
            }
        }
        x
    };
    Some((ta, tb))
}

/// morceaux conserves [t0, t1] de l'axe apres retrait des intervalles interdits
pub fn morceaux(interdits: &mut Vec<(f64, f64)>, long: f64, lmin: f64) -> Vec<(f64, f64)> {
    interdits.sort_by(|x, y| x.0.partial_cmp(&y.0).unwrap());
    let mut res = Vec::new();
    let mut cur = 0.0f64; // point sur : 0 ou bord droit (sur) d'un interdit
    for &(ta, tb) in interdits.iter() {
        if ta > cur {
            res.push((cur, ta.min(1.0)));
        }
        if tb > cur {
            cur = tb;
        }
    }
    if cur < 1.0 {
        res.push((cur, 1.0));
    }
    res.into_iter().filter(|&(s, e)| e > s && (e - s) * long >= lmin).collect()
}

pub struct Gardes<'a> {
    pub pastilles: &'a [Pastille],
    pub grilles: [&'a Grille; 2],
    pub masque: bool,
    pub garde: f64,
}

fn interdits_seg(a: P, b: P, w: f64, face: Face, gd: &Gardes) -> Vec<(f64, f64)> {
    let seuil = gd.garde + w / 2.0;
    let bb = [a.x.min(b.x), a.y.min(b.y), a.x.max(b.x), a.y.max(b.y)];
    let mut v = Vec::new();
    for i in gd.grilles[face.idx()].requete(&bb, seuil + 1.0) {
        let s = forme_garde(&gd.pastilles[i], gd.masque);
        if d_boites(&bb, &s.bb) >= seuil {
            continue;
        }
        if let Some(iv) = intervalle_interdit(a, b, s, seuil) {
            v.push(iv);
        }
    }
    v
}

pub fn rogner(d: &Donnees, gd: &Gardes, lmin: f64, fleche: f64) -> Rognage {
    let mut r = Rognage { pistes_sup: vec![], arcs_sup: vec![], ajouts: vec![], retire: vec![] };
    for (i, t) in d.pistes.iter().enumerate() {
        let mut iv = interdits_seg(t.a, t.b, t.w, t.face, gd);
        if iv.is_empty() {
            continue;
        }
        let long = ((t.b.x - t.a.x).powi(2) + (t.b.y - t.a.y).powi(2)).sqrt();
        let m = morceaux(&mut iv, long.max(1e-9), lmin);
        r.pistes_sup.push(i);
        let mut garde = 0.0;
        for (s, e) in m {
            garde += (e - s) * long;
            r.ajouts.push(Morceau {
                a: p(t.a.x + (t.b.x - t.a.x) * s, t.a.y + (t.b.y - t.a.y) * s),
                b: p(t.a.x + (t.b.x - t.a.x) * e, t.a.y + (t.b.y - t.a.y) * e),
                w: t.w,
                face: t.face,
                comp: t.comp.clone(),
            });
        }
        r.retire.push((t.comp.clone(), long - garde));
    }
    for (i, a) in d.arcs.iter().enumerate() {
        // test conservateur : cordes gonflees de la fleche
        let pts = arc_points(a.c, a.r, a.a0, a.a1, fleche);
        let viole = pts.windows(2).any(|s| {
            let gd2 = Gardes { garde: gd.garde + fleche, ..*gd };
            !interdits_seg(s[0], s[1], a.w, a.face, &gd2).is_empty()
        });
        if !viole {
            continue;
        }
        r.arcs_sup.push(i);
        let mut tot = 0.0;
        let mut garde = 0.0;
        for s in pts.windows(2) {
            let long = ((s[1].x - s[0].x).powi(2) + (s[1].y - s[0].y).powi(2)).sqrt();
            tot += long;
            let mut iv = interdits_seg(s[0], s[1], a.w, a.face, gd);
            let m = if iv.is_empty() { vec![(0.0, 1.0)] } else { morceaux(&mut iv, long.max(1e-9), lmin.min(long)) };
            for (u, v) in m {
                garde += (v - u) * long;
                r.ajouts.push(Morceau {
                    a: p(s[0].x + (s[1].x - s[0].x) * u, s[0].y + (s[1].y - s[0].y) * u),
                    b: p(s[0].x + (s[1].x - s[0].x) * v, s[0].y + (s[1].y - s[0].y) * v),
                    w: a.w,
                    face: a.face,
                    comp: a.comp.clone(),
                });
            }
        }
        r.retire.push((a.comp.clone(), tot - garde));
    }
    r
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn intervalle_rectangle() {
        // pastille 20 x 20 en (0, 0) ; piste y = 5 de x = -100 a 100 ; seuil 10 -> interdit |x| < 20
        let s = forme_pastille(0., 0., 20., 20., 0., true, 0.);
        let (ta, tb) = intervalle_interdit(p(-100., 5.), p(100., 5.), &s, 10.).unwrap();
        assert!((ta * 200. - 100. + 20.).abs() < 1e-6, "{}", ta);
        assert!((tb * 200. - 100. - 20.).abs() < 1e-6, "{}", tb);
        assert!(intervalle_interdit(p(-100., 50.), p(100., 50.), &s, 10.).is_none());
    }

    #[test]
    fn intervalle_rond_et_bout() {
        // pastille ronde r = 10 ; piste depuis le centre vers x = 100 ; seuil 5 -> interdit t < 15/100
        let s = forme_pastille(0., 0., 20., 20., 0., false, 0.);
        let (ta, tb) = intervalle_interdit(p(0., 0.), p(100., 0.), &s, 5.).unwrap();
        assert!(ta < 0.0);
        assert!((tb - 0.15).abs() < 1e-6);
    }

    #[test]
    fn decoupe_morceaux() {
        let mut iv = vec![(0.4, 0.6), (-1.0, 0.1), (0.55, 0.7)];
        let m = morceaux(&mut iv, 100., 5.);
        assert_eq!(m.len(), 2);
        assert!((m[0].0 - 0.1).abs() < 1e-12 && (m[0].1 - 0.4).abs() < 1e-12);
        assert!((m[1].0 - 0.7).abs() < 1e-12 && (m[1].1 - 1.0).abs() < 1e-12);
        // morceau trop court supprime
        let mut iv = vec![(0.02, 2.0)];
        assert!(morceaux(&mut iv, 100., 5.).is_empty());
    }
}
