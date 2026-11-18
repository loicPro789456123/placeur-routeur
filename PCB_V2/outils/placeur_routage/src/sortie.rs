//! Sorties : placement (format placements_v3.json), routage detaille (mils), dessins SVG.
use crate::detail::*;
use crate::geom::*;
use crate::globalr::*;
use serde_json::{json, Value};
use std::fmt::Write as _;

fn r2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

/// coordonnee en mils, 3 decimales (meme arrondi pour une extremite de piste et le via qui la rejoint)
fn r3(v: f64) -> f64 {
    (v * 1000.0).round() / 1000.0
}

/// largeur en mils, 3 decimales, arrondie VERS LE HAUT (la regle Altium compare au millieme de mil)
fn w3(mm: f64) -> f64 {
    ((mm / MIL) * 1000.0 - 1e-6).ceil() / 1000.0
}

/// Texte du champ `origine` des sorties (repere des donnees).
pub const ORIGINE: &str = "repere des donnees du placeur (donnees_placeur/2)";

/// Placement au format `placements/2` (mil, repere des donnees) : toutes les pieces, fixes comprises (pose exacte des
/// donnees pour les pieces fixes et virtuelles).
pub fn placement_json(m: &Modele, e: &Etat) -> Value {
    let mut v = Vec::new();
    let [ox, oy] = m.origine;
    for (i, c) in m.comps.iter().enumerate() {
        let p = e.poses[i];
        let exacte = if c.statut == Statut::Mobile { None } else { m.poses_donnees.get(i).copied().flatten() };
        let (x, y, rot, bot) = match exacte {
            Some(q) => q,
            None => (p.x + ox, p.y + oy, p.rot as f64, p.bot),
        };
        let mut o = json!({"designator": c.nom, "side": if bot {"bottom"} else {"top"},
            "x": r2(x / MIL), "y": r2(y / MIL), "rotation": rot,
            "statut": if c.statut == Statut::Mobile { "mobile" } else { "fixe" }});
        if bot {
            o["rotation_si_convention_miroir_x"] = json!((rot + 180.0).rem_euclid(360.0));
        }
        v.push(o);
    }
    json!({"format": "placements/2", "unite": "mil", "origine": ORIGINE, "pieces": v})
}

/// Resistance d'une piste en milliohms : cuivre 35 um, 0,5 mOhm par carre.
pub const MOHM_CARRE: f64 = 0.5;

/// Routage detaille au format `routage/2` (mil, repere des donnees, noms de couches des donnees). Les segments poses
/// sur une couche absente de la carte (a l'interieur d'une pastille traversante, sans effet) ne sont pas ecrits.
pub fn routage_json(d: &Detail) -> Value {
    let m = d.m;
    let [ox, oy] = m.origine;
    let (mut p, mut v) = (Vec::new(), Vec::new());
    let via_d = m.regles.via.diametre;
    let via_t = m.regles.via.trou;
    let mut echecs = Vec::new();
    let mut reseaux = Vec::new();
    let piste = |s: &SegD, nom: &str| {
        json!({"x1": r3((s.x1 + ox) / MIL), "y1": r3((s.y1 + oy) / MIL), "x2": r3((s.x2 + ox) / MIL), "y2": r3((s.y2 + oy) / MIL),
            "width": w3(s.w), "layer": m.noms_couches[s.couche as usize], "net_name": nom})
    };
    for (n, segs) in &d.amorces {
        for s in segs {
            if m.couches_actives[s.couche as usize] {
                p.push(piste(s, &m.nets[*n].nom));
            }
        }
    }
    for (n, r) in d.nets.iter().enumerate() {
        let nom = &m.nets[n].nom;
        let mut long = 0.0;
        for s in &r.segs {
            if !m.couches_actives[s.couche as usize] {
                continue;
            }
            long += ((s.x2 - s.x1).powi(2) + (s.y2 - s.y1).powi(2)).sqrt();
            p.push(piste(s, nom));
        }
        for x in &r.vias {
            v.push(json!({"x": r3((x.x + ox) / MIL), "y": r3((x.y + oy) / MIL), "size": w3(via_d), "hole_size": w3(via_t), "net": nom,
                "de": m.via_de_a[0], "a": m.via_de_a[1]}));
        }
        if r.ko > 0 {
            echecs.push(json!({"net": nom, "role": m.nets[n].role, "connexions_echouees": r.ko, "connexions_reussies": r.ok, "diagnostic": r.diag}));
        }
        if !r.segs.is_empty() {
            reseaux.push(json!({"net": nom, "role": m.nets[n].role, "longueur_mm": (long * 100.0).round() / 100.0,
                "resistance_mohm": (r.carres * MOHM_CARRE * 1000.0).round() / 1000.0, "longueur_retrecie_mm": (r.neck_mm * 100.0).round() / 100.0,
                "longueur_sous_mini_regle_mm": (r.derog_mm * 100.0).round() / 100.0, "vias": r.vias.len(), "vias_doubles": r.vias_doubles}));
        }
    }
    let couches: Vec<&String> = (0..3).filter(|&l| m.couches_actives[l]).map(|l| &m.noms_couches[l]).collect();
    json!({
        "format": "routage/2", "unite": "mil", "origine": ORIGINE, "couches_routage": couches,
        "pistes": p, "vias": v, "surfaces": [],
        "amorces_pas_fin": d.amorces.iter().map(|(n, _)| m.nets[*n].nom.clone()).collect::<Vec<_>>(),
        "derogations": d.derogations, "reseaux": reseaux, "echecs": echecs
    })
}

struct Svg {
    s: String,
    sc: f64,
    ox: f64,
    oy: f64,
    h: f64,
}

impl Svg {
    fn x(&self, x: f64) -> f64 {
        self.ox + x * self.sc
    }
    fn y(&self, y: f64) -> f64 {
        self.oy + (self.h - y) * self.sc
    }
    fn rect(&mut self, r: &Rect, fill: &str, stroke: &str, op: f64, sw: f64) {
        let _ = writeln!(
            self.s,
            r#"<rect x="{:.1}" y="{:.1}" width="{:.1}" height="{:.1}" fill="{}" stroke="{}" fill-opacity="{:.2}" stroke-width="{:.1}"/>"#,
            self.x(r[0]),
            self.y(r[3]),
            (r[2] - r[0]) * self.sc,
            (r[3] - r[1]) * self.sc,
            fill,
            stroke,
            op,
            sw
        );
    }
    fn line(&mut self, x1: f64, y1: f64, x2: f64, y2: f64, w: f64, col: &str, op: f64) {
        let _ = writeln!(
            self.s,
            r#"<line x1="{:.1}" y1="{:.1}" x2="{:.1}" y2="{:.1}" stroke="{}" stroke-width="{:.2}" stroke-opacity="{:.2}" stroke-linecap="round"/>"#,
            self.x(x1),
            self.y(y1),
            self.x(x2),
            self.y(y2),
            col,
            (w * self.sc).max(0.6),
            op
        );
    }
    fn circle(&mut self, x: f64, y: f64, r: f64, fill: &str) {
        let _ = writeln!(self.s, r#"<circle cx="{:.1}" cy="{:.1}" r="{:.1}" fill="{}"/>"#, self.x(x), self.y(y), r * self.sc, fill);
    }
    fn text(&mut self, x: f64, y: f64, t: &str, fs: u32) {
        let _ = writeln!(self.s, r#"<text x="{:.1}" y="{:.1}" font-size="{}" font-family="Arial">{}</text>"#, x, y, fs, t);
    }
}

fn fond(g: &mut Svg, m: &Modele, e: &Etat, face: Option<u8>) {
    g.rect(&[0.0, 0.0, m.w, m.h], "#f4f8f0", "black", 1.0, 1.2);
    for f in &m.fentes {
        g.rect(&[f[0], f[1], f[0] + f[2], f[1] + f[3]], "#555", "#000", 1.0, 0.5);
    }
    for t in &m.trous {
        g.circle(t[0], t[1], 1.6, "#333");
    }
    let em = m.emprise(e);
    g.rect(&em, "#ffd0d0", "#c00", 0.5, 1.0);
    for (i, c) in m.comps.iter().enumerate() {
        let p = e.poses[i];
        let f: u8 = if p.bot { 1 } else { 0 };
        if face.map(|x| x == f).unwrap_or(true) {
            let col = match c.statut {
                Statut::Bloc => "#c00",
                Statut::Fixe => "#888",
                Statut::Mobile => {
                    if f == 0 {
                        "#2a6fdb"
                    } else {
                        "#1a9a4a"
                    }
                }
            };
            let b = m.bbox(i, &p);
            g.rect(&b, col, col, if face.is_some() { 0.18 } else { 0.06 }, 0.4);
        }
        for k in 0..c.pads.len() {
            let th = c.pads[k].th;
            if face.map(|x| x == f || th).unwrap_or(true) {
                let (x, y, hw, hh) = m.pad_world(i, k, &p);
                g.rect(&[x - hw, y - hh, x + hw, y + hh], if th { "#a0522d" } else { "#d4a017" }, "none", 0.85, 0.0);
            }
        }
    }
}

/// Placement + routage global (cellules utilisees, par couche).
pub fn svg_global(chemin: &str, m: &Modele, e: &Etat, rt: &Routeur, titre: &str) -> std::io::Result<()> {
    let sc = 6.0;
    let pad = 10.0;
    let ph = m.h * sc;
    let mut g = Svg { s: String::new(), sc, ox: pad, oy: 0.0, h: m.h };
    let wpx = m.w * sc + 2.0 * pad;
    let hpx = 2.0 * ph + 4.0 * pad + 40.0;
    let mut out = format!(r#"<svg xmlns="http://www.w3.org/2000/svg" width="{:.0}" height="{:.0}"><rect width="100%" height="100%" fill="white"/>"#, wpx, hpx);
    out.push('\n');
    g.text(pad, 16.0, titre, 14);
    for face in 0..2u8 {
        g.oy = 40.0 + pad + face as f64 * (ph + 2.0 * pad);
        g.text(pad, g.oy - 4.0, if face == 0 { "DESSUS (L1) : pieces + routage global couche dessus (rouge)" } else { "DESSOUS (L4), vue de dessus : pieces + routage global couche dessous (bleu)" }, 12);
        fond(&mut g, m, e, Some(face));
        let p = rt.pas;
        for r in &rt.routes {
            for &n in &r.nodes {
                let n = n as usize;
                let lc = n / rt.nc;
                if !(lc == face as usize || (face == 0 && lc == 2)) {
                    continue;
                }
                let col = ["#d22", "#22d", "#1a1"][lc];
                let c = n % rt.nc;
                let (x, y) = (((c % rt.nx) as f64 + 0.5) * p, ((c / rt.nx) as f64 + 0.5) * p);
                g.rect(&[x - p * 0.35, y - p * 0.35, x + p * 0.35, y + p * 0.35], col, "none", 0.75, 0.0);
            }
        }
        // vias (changements de couche) : noeuds presents sur les deux couches dans une meme route
        for r in &rt.routes {
            let mut s0 = std::collections::HashSet::new();
            for &n in &r.nodes {
                if (n as usize) < rt.nc {
                    s0.insert(n as usize);
                }
            }
            for &n in &r.nodes {
                let n = n as usize;
                if n >= rt.nc && s0.contains(&(n - rt.nc)) {
                    let c = n - rt.nc;
                    let (x, y) = (((c % rt.nx) as f64 + 0.5) * p, ((c / rt.nx) as f64 + 0.5) * p);
                    g.circle(x, y, 0.3, "black");
                }
            }
        }
    }
    out.push_str(&g.s);
    out.push_str("</svg>\n");
    std::fs::write(chemin, out)
}

/// Routage detaille : pistes dessus (rouge), dessous (bleu), vias (noir).
pub fn svg_detail(chemin: &str, d: &Detail, titre: &str) -> std::io::Result<()> {
    let m = d.m;
    let sc = 8.0;
    let pad = 10.0;
    let mut g = Svg { s: String::new(), sc, ox: pad, oy: 40.0, h: m.h };
    let wpx = m.w * sc + 2.0 * pad;
    let hpx = m.h * sc + 60.0;
    let mut out = format!(r#"<svg xmlns="http://www.w3.org/2000/svg" width="{:.0}" height="{:.0}"><rect width="100%" height="100%" fill="white"/>"#, wpx, hpx);
    out.push('\n');
    g.text(pad, 16.0, titre, 14);
    g.text(pad, 32.0, "rouge = TopLayer, vert = MidLayer2 (L3), bleu = BottomLayer (vue de dessus), noir = vias, rose = emprise du bloc hacheur", 11);
    fond(&mut g, m, &d.e, None);
    for couche in [1u8, 2u8, 0u8] {
        let col = ["#d22", "#22d", "#1a1"][couche as usize];
        for r in &d.nets {
            for s in &r.segs {
                if s.couche == couche {
                    g.line(s.x1, s.y1, s.x2, s.y2, s.w, col, 0.7);
                }
            }
        }
    }
    for r in &d.nets {
        for v in &r.vias {
            g.circle(v.x, v.y, 0.3, "black");
        }
    }
    out.push_str(&g.s);
    out.push_str("</svg>\n");
    std::fs::write(chemin, out)
}
