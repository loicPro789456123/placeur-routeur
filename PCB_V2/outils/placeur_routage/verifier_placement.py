# -*- coding: utf-8 -*-
"""Contrôle indépendant d'un placement (§4.5 du contrat) : format ``controle_placement/1`` (§2.8).

    python verifier_placement.py --donnees donnees_placeur.json --placement placements_routage.json
                                 [--sortie controle_placement.json] [--sans-grille]

Tout se lit dans les données (``donnees_placeur/2``) : statuts, poses cibles, orientations, boîtes ``lbb``,
pastilles, obstacles, trous, découpes, zones imposées, éloignements, pièces ``au_bord``, groupes ; les paramètres
(``grille_mm``, ``marge_piece_mm``, ``marge_traversante_mm``) dans ``donnees.regles.placement``, les marges de bord
et de découpe dans ``donnees.carte``. Aucun nom de carte, de pièce, de réseau ou de couche n'est interprété.

Les 14 types de §4.5 : absente, inconnue, fixe_deplacee, orientation, grille (tolérance 1e-3 mil), chevauchement,
obstacle, marge_bord, marge_decoupe, eloignement (``dure``), pastille_pastille (matrice d'isolement), zone_imposee,
au_bord, groupe_rigide.

Choix d'interprétation (§8.0-2 c, journal du lot C, étape C4) :

- pièce « à sa pose d'entrée » : pièce fixe dont la ``pose_cible`` est sa ``pose_actuelle`` (pas de
  ``fixes_deplaces``) et qui est posée à cette pose ; une pièce virtuelle aussi. Les paires de deux telles pièces
  sont exclues ; une pièce à sa pose d'entrée est aussi dispensée d'``obstacle``, de ``marge_bord`` et de
  ``marge_decoupe`` (dessin d'origine) ;
- boîte d'une pièce : rectangle ``lbb`` posé et tourné (exact, rotation quelconque) ; ``chevauchement`` et
  ``obstacle`` : boîtes élargies chacune de ``marge_piece_mm`` (comme 9b5b5a3) qui se recouvrent de plus de 1e-6 mm ;
  ``marge_decoupe`` : boîte sans élargissement ; ``zone_imposee`` et ``au_bord`` : boîte sans élargissement ;
- pastille présente par la face opposée : pastille d'une pièce posée sur l'autre face dont le cuivre est sur la
  couche extérieure de la face contrôlée (traversante, ou CMS de cette couche) ; boîte axiale de son cuivre sur
  cette couche élargie de ``max(0, expansion_masque)`` + ``marge_traversante_mm`` ; pièces virtuelles comprises ;
- ``pastille_pastille`` : pastilles de réseaux différents (deux pastilles du même réseau ne sont pas un défaut
  d'isolement) ; une violation par couple de pièces (écart minimal dans le texte) ;
- ``marge_bord`` d'une pièce ``au_bord`` : arêtes du contour situées sur le côté demandé de la boîte de la carte
  ignorées (côté ``quelconque`` : celui dont la boîte de la pièce est le plus proche) ;
- ``zone_imposee`` : boîte hors du polygone, ou face de la pièce absente de ``faces`` (quand ``faces`` est non vide) ;
- ``infos.eloignements`` : une ligne par objet de chaque éloignement (``dure`` et ``souple``), ``reference`` =
  désignateurs joints par « + », distance arrondie au millième ;
- une pièce citée deux fois dans le placement : code 2.

Codes (§1.4) : 0 placement valide, 1 violation(s), 2 entrée invalide, 4 erreur interne.
"""
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)
import formats  # noqa: E402
import geom_commune as gc  # noqa: E402

FORMAT = "controle_placement/1"
OUTIL = "verifier_placement"
TYPES = ("absente", "inconnue", "fixe_deplacee", "orientation", "grille", "chevauchement", "obstacle",
         "marge_bord", "marge_decoupe", "eloignement", "pastille_pastille", "zone_imposee", "au_bord",
         "groupe_rigide")
EPS_POSE_MM = 1e-3          # fixe_deplacee, au_bord, groupe_rigide (§4.5)
EPS_GRILLE_MIL = 1e-3       # grille (§4.5)
EPS_RECOUVREMENT = 1e-6     # deux boîtes qui se touchent ne se recouvrent pas
FACES = ("dessus", "dessous")
COTES = ("gauche", "droite", "bas", "haut")


def _f(v, d=3):
    return gc.nombre_fr(v, d)


class ControlePlacement:
    """État du contrôle d'un couple (placement, données)."""

    def __init__(self, placements, donnees, grille=True):
        gc.exiger_format(donnees, "donnees_placeur/2", "donnees")
        self.d = donnees
        self.grille = grille
        self.violations = []
        self.par_type = {t: 0 for t in TYPES}
        regles = gc.exiger(donnees, "regles", "donnees")
        pl = gc.exiger(regles, "placement", "donnees.regles")
        ch = "donnees.regles.placement"
        g = pl.get("grille_mm")
        self.grille_mm = None if g is None else gc.nombre(g, ch + ".grille_mm")
        self.marge = gc.nombre(gc.exiger(pl, "marge_piece_mm", ch), ch + ".marge_piece_mm")
        self.marge_trav = gc.nombre(gc.exiger(pl, "marge_traversante_mm", ch), ch + ".marge_traversante_mm")
        carte = gc.exiger(donnees, "carte", "donnees")
        self.marge_bord = gc.nombre(gc.exiger(carte, "marge_bord_mm", "donnees.carte"), "donnees.carte.marge_bord_mm")
        self.marge_decoupe = gc.nombre(gc.exiger(carte, "marge_decoupe_mm", "donnees.carte"),
                                       "donnees.carte.marge_decoupe_mm")
        self.contour = gc.forme_polygone(gc.points(gc.exiger(carte, "contour", "donnees.carte"),
                                                   "donnees.carte.contour"), chemin="donnees.carte.contour")
        b = gc.liste(gc.exiger(carte, "boite", "donnees.carte"), "donnees.carte.boite")
        if len(b) != 4:
            raise gc.ErreurEntree("donnees.carte.boite : [x0, y0, x1, y1] attendu")
        self.boite_carte = [gc.nombre(v, "donnees.carte.boite") for v in b]
        iso = gc.exiger(regles, "isolement", "donnees.regles")
        self.matrice = [[gc.nombre(x, "donnees.regles.isolement.matrice") for x in gc.liste(l, "matrice")]
                        for l in gc.liste(gc.exiger(iso, "matrice", "donnees.regles.isolement"),
                                          "donnees.regles.isolement.matrice")]
        if not self.matrice or any(len(l) != len(self.matrice) for l in self.matrice):
            raise gc.ErreurEntree("donnees.regles.isolement.matrice : matrice carrée non vide attendue")
        self.ref = gc.entier(iso.get("classe_reference", 0), "donnees.regles.isolement.classe_reference")
        if not 0 <= self.ref < len(self.matrice):
            raise gc.ErreurEntree("donnees.regles.isolement.classe_reference : hors de la matrice")
        self.iso_max = max(max(l) for l in self.matrice)
        # Pose des objets de cuivre (pastilles des pièces posées) ; poses des pièces.
        self.sc = gc.poser(donnees, placements)
        if self.sc.infos_placement["doublons"]:
            raise gc.ErreurEntree("placement : pièce(s) citée(s) plusieurs fois : %s"
                                  % ", ".join(self.sc.infos_placement["doublons"]))
        self.comps = {c["nom"]: c for c in donnees["composants"]}
        self.poses = self.sc.poses
        self.boites = {}
        for nom, c in self.comps.items():
            lbb = gc.liste(gc.exiger(c, "lbb", "composant %s" % nom), "composant %s.lbb" % nom)
            if len(lbb) != 4:
                raise gc.ErreurEntree("composant %s.lbb : [x0, y0, x1, y1] attendu" % nom)
            x0, y0, x1, y1 = (gc.nombre(v, "composant %s.lbb" % nom) for v in lbb)
            p = self.poses[nom]
            coins = [p.monde(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            self.boites[nom] = {"coins": coins, "centre": p.monde((x0 + x1) / 2.0, (y0 + y1) / 2.0),
                                "nue": gc.forme_polygone(coins, chemin="composant %s.lbb" % nom),
                                "elargie": gc.forme_polygone(coins, rayon=self.marge,
                                                             chemin="composant %s.lbb" % nom)}
        self.entree = {nom: self._a_pose_entree(c) for nom, c in self.comps.items()}
        self.au_bord = {}
        for i, e in enumerate(gc.liste(gc.exiger(donnees, "au_bord", "donnees"), "donnees.au_bord")):
            chb = "donnees.au_bord[%d]" % i
            cote = gc.texte(gc.exiger(e, "cote", chb), chb + ".cote")
            if cote not in COTES + ("quelconque",):
                raise gc.ErreurEntree("%s.cote : « %s » invalide" % (chb, cote))
            self.au_bord[gc.texte(gc.exiger(e, "piece", chb), chb + ".piece")] = (
                cote, gc.nombre(e.get("debord_mm", 0.0) or 0.0, chb + ".debord_mm"))
        self.infos = {"eloignements": []}

    # -- outils ----------------------------------------------------------------------------------------------------
    def ajouter(self, genre, pieces, texte):
        self.par_type[genre] += 1
        self.violations.append({"genre": genre, "pieces": list(pieces), "texte": texte})

    def _a_pose_entree(self, c):
        cible = c.get("pose_cible")
        actuelle = c.get("pose_actuelle", cible)
        if c.get("statut") != "fixe" and not c.get("virtuel"):
            return False
        if not self._meme_pose(cible, actuelle):
            return False
        p = self.poses[c["nom"]]
        return self._meme_pose(cible, (p.x, p.y, p.rotation, p.dessous))

    @staticmethod
    def _meme_pose(a, b):
        return (abs(float(a[0]) - float(b[0])) <= EPS_POSE_MM and abs(float(a[1]) - float(b[1])) <= EPS_POSE_MM
                and gc.ecart_angle(a[2], b[2]) <= gc.EPS_ANGLE and bool(a[3]) == bool(b[3]))

    def face(self, nom):
        return "dessous" if self.poses[nom].dessous else "dessus"

    def rang_face(self, face):
        return self.sc.n - 1 if face == "dessous" else 0

    def reelles(self):
        """Pièces non virtuelles, dans l'ordre des données."""
        return [n for n, c in self.comps.items() if not c.get("virtuel")]

    def classe(self, o):
        c = o.classe
        if isinstance(c, int) and not isinstance(c, bool) and 0 <= c < len(self.matrice):
            return c
        return self.ref

    def masque_zone(self, couches, retournee):
        """Masque des couches d'une zone d'empreinte (``toutes`` ou noms connus ; un nom non cuivre est ignoré)."""
        m = 0
        for nom in couches or []:
            if nom == "toutes":
                return self.sc.tout
            r = self.sc.rang.get(nom)
            if r is not None:
                m |= 1 << (self.sc.n - 1 - r if retournee else r)
        return m

    # -- règles individuelles --------------------------------------------------------------------------------------
    def presence(self):
        for nom in self.sc.infos_placement["absentes"]:
            self.ajouter("absente", [nom], "%s : pièce absente du placement" % nom)
        for nom in self.sc.infos_placement["inconnues"]:
            self.ajouter("inconnue", [nom], "%s : désignateur inconnu des données" % nom)

    def poses_et_orientations(self):
        absentes = set(self.sc.infos_placement["absentes"])
        for nom in self.reelles():
            if nom in absentes:
                continue
            c = self.comps[nom]
            p = self.poses[nom]
            if c.get("statut") == "fixe":
                cible = c["pose_cible"]
                if not self._meme_pose(cible, (p.x, p.y, p.rotation, p.dessous)):
                    self.ajouter("fixe_deplacee", [nom], "%s : pièce fixe posée en (%s ; %s) mm, %s°, %s au lieu de "
                                 "(%s ; %s) mm, %s°, %s" % (
                                     nom, _f(p.x), _f(p.y), _f(p.rotation, 1), self.face(nom),
                                     _f(float(cible[0])), _f(float(cible[1])), _f(float(cible[2]), 1),
                                     "dessous" if cible[3] else "dessus"))
                continue
            ors = gc.liste(c.get("orientations") or [], "composant %s.orientations" % nom)
            if not any(gc.ecart_angle(o[0], p.rotation) <= gc.EPS_ANGLE and bool(o[1]) == p.dessous for o in ors):
                self.ajouter("orientation", [nom], "%s : orientation (%s°, %s) non permise (règle : %s)"
                             % (nom, _f(p.rotation, 1), self.face(nom), c.get("regle") or "-"))
            if self.grille and self.grille_mm:
                g = self.grille_mm
                for v in (p.x, p.y):
                    q = v / g
                    if abs(q - round(q)) * g / gc.MIL > EPS_GRILLE_MIL:
                        self.ajouter("grille", [nom], "%s : origine hors de la grille de %s mm" % (nom, _f(g)))
                        break

    def chevauchements(self):
        noms = [n for n in self.reelles() if n not in self.sc.infos_placement["absentes"]]
        idx = gc.IndexCases(case=2.0, marge=0.0)
        for i, n in enumerate(noms):
            idx.ajouter(i, self.boites[n]["elargie"].boite)
        vues = set()
        for i, a in enumerate(noms):
            fa = self.boites[a]["elargie"]
            for lst in idx.listes(fa.boite):
                for j in lst:
                    if j <= i or (i, j) in vues:
                        continue
                    vues.add((i, j))
                    b = noms[j]
                    if self.face(a) != self.face(b) or (self.entree[a] and self.entree[b]):
                        continue
                    fb = self.boites[b]["elargie"]
                    if gc.distance_boites(fa.boite, fb.boite) > 0.0:
                        continue
                    e = gc.ecart_formes(fa, fb)
                    if e < -EPS_RECOUVREMENT:
                        self.ajouter("chevauchement", [a, b], "%s et %s : boîtes (+ %s mm) qui se recouvrent (face %s)"
                                     % (a, b, _f(self.marge, 2), self.face(a)))
        # Pastilles présentes sur une face par la face opposée.
        pastilles = [o for o in self.sc.objets if o.genre == gc.PASTILLE]
        for face in FACES:
            rang = self.rang_face(face)
            boites_p = []
            for o in pastilles:
                if not (o.masque >> rang) & 1 or self.face(o.piece) == face:
                    continue
                if o.piece in self.sc.infos_placement["absentes"]:
                    continue
                exp = o.info.get("expansion_masque") or 0.0
                m = max(0.0, float(exp)) + self.marge_trav
                bx = o.forme_sur(rang).boite
                boites_p.append((o, gc.forme_polygone([(bx[0], bx[1]), (bx[2], bx[1]), (bx[2], bx[3]), (bx[0], bx[3])],
                                                      rayon=m, chemin="pastille %s" % o.id)))
            if not boites_p:
                continue
            idxp = gc.IndexCases(case=2.0, marge=0.0)
            for k, (o, f) in enumerate(boites_p):
                idxp.ajouter(k, f.boite)
            for a in noms:
                if self.face(a) != face:
                    continue
                fa = self.boites[a]["elargie"]
                touchees = {}
                for lst in idxp.listes(fa.boite):
                    for k in lst:
                        o, f = boites_p[k]
                        if o.piece == a or o.piece in touchees or (self.entree[a] and self.entree[o.piece]):
                            continue
                        if gc.distance_boites(fa.boite, f.boite) > 0.0:
                            continue
                        if gc.ecart_formes(fa, f) < -EPS_RECOUVREMENT:
                            touchees[o.piece] = o.id
                for q, pid in touchees.items():
                    self.ajouter("chevauchement", [a, q], "%s : boîte sur la pastille %s de %s, présente sur la face "
                                 "%s par la face opposée" % (a, pid, q, face))

    def obstacles(self):
        obs = []   # (faces, forme, etiquette, piece exclue)
        for i, o in enumerate(gc.liste(gc.exiger(self.d, "obstacles_placement", "donnees"),
                                       "donnees.obstacles_placement")):
            ch = "donnees.obstacles_placement[%d]" % i
            faces = [gc.texte(f, ch + ".faces") for f in gc.liste(gc.exiger(o, "faces", ch), ch + ".faces")]
            if o.get("polygone") is not None:
                forme = gc.forme_polygone(gc.points(o["polygone"], ch + ".polygone"), chemin=ch + ".polygone")
            else:
                r = gc.liste(gc.exiger(o, "rect", ch), ch + ".rect")
                if len(r) != 4:
                    raise gc.ErreurEntree("%s.rect : [x0, y0, x1, y1] attendu" % ch)
                x0, y0, x1, y1 = (gc.nombre(v, ch + ".rect") for v in r)
                forme = gc.forme_polygone([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], chemin=ch + ".rect")
            obs.append((faces, [forme], o.get("etiquette") or "obstacle %d" % (i + 1), None))
        for i, t in enumerate(gc.liste(gc.exiger(self.d, "trous", "donnees"), "donnees.trous")):
            ch = "donnees.trous[%d]" % i
            r = gc.nombre(t.get("rayon_interdit_mm", 0.0) or 0.0, ch + ".rayon_interdit_mm")
            if r <= 0.0:
                continue
            x, y = gc.nombre(gc.exiger(t, "x", ch), ch + ".x"), gc.nombre(gc.exiger(t, "y", ch), ch + ".y")
            obs.append((list(FACES), [gc.forme_point(x, y, r)], "disque interdit du trou %s" % (t.get("id") or i),
                        t.get("piece")))
        for nom, c in self.comps.items():
            if nom in self.sc.infos_placement["absentes"]:
                continue
            p = self.poses[nom]
            for k, z in enumerate(c.get("zones_interdites") or []):
                if "placement" not in (z.get("effets") or []):
                    continue
                ch = "composant %s.zones_interdites[%d]" % (nom, k)
                m = self.masque_zone(z.get("couches"), p.retournee)
                faces = [f for f in FACES if (m >> self.rang_face(f)) & 1]
                if not faces:
                    continue
                pts = [p.monde(x, y) for x, y in gc.points(gc.exiger(z, "points", ch), ch + ".points")]
                if z.get("forme") == "polyligne":
                    r = gc.nombre(z.get("largeur", 0.0) or 0.0, ch + ".largeur") / 2.0
                    formes = [gc.forme_segment(a[0], a[1], b[0], b[1], r) for a, b in zip(pts, pts[1:])]
                    if len(pts) == 1:
                        formes = [gc.forme_point(pts[0][0], pts[0][1], r)]
                else:
                    formes = [gc.forme_polygone(pts, chemin=ch + ".points")]
                obs.append((faces, formes, "zone interdite %s de %s" % (z.get("id") or k, nom), nom))
        for a in self.reelles():
            if a in self.sc.infos_placement["absentes"] or self.entree[a]:
                continue
            fa = self.boites[a]["elargie"]
            for faces, formes, etiquette, exclue in obs:
                if self.face(a) not in faces or exclue == a:
                    continue
                for f in formes:
                    if gc.distance_boites(fa.boite, f.boite) <= 0.0 and gc.ecart_formes(fa, f) < -EPS_RECOUVREMENT:
                        self.ajouter("obstacle", [a] + ([exclue] if exclue else []),
                                     "%s : boîte (+ %s mm) sur %s (face %s)" % (a, _f(self.marge, 2), etiquette,
                                                                               self.face(a)))
                        break

    def _cote_de(self, nom):
        """Côté de la boîte de la carte demandé pour une pièce ``au_bord`` (``quelconque`` : le plus proche)."""
        cote, debord = self.au_bord[nom]
        bx = self.boites[nom]["nue"].boite
        X0, Y0, X1, Y1 = self.boite_carte
        ecarts = {"gauche": abs(bx[0] - (X0 - debord)), "droite": abs(bx[2] - (X1 + debord)),
                  "bas": abs(bx[1] - (Y0 - debord)), "haut": abs(bx[3] - (Y1 + debord))}
        if cote == "quelconque":
            cote = min(COTES, key=lambda k: ecarts[k])
        return cote, ecarts[cote]

    def marges(self):
        X0, Y0, X1, Y1 = self.boite_carte
        decoupes = []
        for i, dc in enumerate(gc.liste(gc.exiger(self.d["carte"], "decoupes", "donnees.carte"),
                                        "donnees.carte.decoupes")):
            ch = "donnees.carte.decoupes[%d]" % i
            decoupes.append((dc.get("id") or "D%d" % (i + 1), dc.get("proprietaire"),
                             gc.forme_polygone(gc.points(gc.exiger(dc, "points", ch), ch + ".points"),
                                               chemin=ch + ".points")))
        for a in self.reelles():
            if a in self.sc.infos_placement["absentes"] or self.entree[a]:
                continue
            fa = self.boites[a]["elargie"]
            if a in self.au_bord:
                cote, _ = self._cote_de(a)
                coord = {"gauche": X0, "droite": X1, "bas": Y0, "haut": Y1}[cote]
                axe = 0 if cote in ("gauche", "droite") else 1
                aretes = [s for s in self.contour.aretes
                          if not (abs(s[axe] - coord) <= EPS_POSE_MM and abs(s[axe + 2] - coord) <= EPS_POSE_MM)]
                e = gc.ecart_aux_aretes(fa, aretes)
            else:
                e = gc.ecart_au_bord(fa, self.contour)
            if e < self.marge_bord - gc.EPS_CONTACT:
                self.ajouter("marge_bord", [a], "%s : boîte (+ %s mm) à %s mm du bord (marge %s mm)"
                             % (a, _f(self.marge, 2), _f(e), _f(self.marge_bord, 2)))
            fn = self.boites[a]["nue"]
            for ident, proprio, f in decoupes:
                if proprio == a:
                    continue
                e = gc.ecart_formes(fn, f)
                if e < self.marge_decoupe - gc.EPS_CONTACT:
                    self.ajouter("marge_decoupe", [a], "%s : boîte à %s mm de la découpe %s (marge %s mm)"
                                 % (a, _f(e), ident, _f(self.marge_decoupe, 2)))

    def _point(self, ident, ch):
        if ident in self.comps:
            return self.boites[ident]["centre"]
        for o in self.sc.objets:
            if o.genre == gc.PASTILLE and o.id == ident:
                return o.info["centre"]
        raise gc.ErreurEntree("%s : objet « %s » inconnu (pièce ou PIECE#k attendu)" % (ch, ident))

    def eloignements(self):
        for i, e in enumerate(gc.liste(gc.exiger(self.d, "eloignements", "donnees"), "donnees.eloignements")):
            ch = "donnees.eloignements[%d]" % i
            ref = [gc.texte(r, ch + ".reference") for r in gc.liste(gc.exiger(e, "reference", ch), ch + ".reference")]
            if not ref:
                raise gc.ErreurEntree("%s.reference : liste vide" % ch)
            for r in ref:
                if r not in self.comps:
                    raise gc.ErreurEntree("%s.reference : pièce « %s » inconnue" % (ch, r))
            cx = sum(self.boites[r]["centre"][0] for r in ref) / len(ref)
            cy = sum(self.boites[r]["centre"][1] for r in ref) / len(ref)
            mini = gc.nombre(gc.exiger(e, "distance_mm", ch), ch + ".distance_mm")
            dure = gc.texte(gc.exiger(e, "type", ch), ch + ".type") == "dure"
            for ob in gc.liste(gc.exiger(e, "objets", ch), ch + ".objets"):
                ob = gc.texte(ob, ch + ".objets")
                x, y = self._point(ob, ch + ".objets")
                d = math.hypot(x - cx, y - cy)
                self.infos["eloignements"].append({"objet": ob, "reference": "+".join(ref),
                                                   "distance_mm": round(d, 3), "minimum_mm": mini})
                if dure and d < mini - gc.EPS_CONTACT:
                    piece = ob.split("#")[0]
                    self.ajouter("eloignement", [piece] + [r for r in ref if r != piece],
                                 "%s : à %s mm de %s (minimum %s mm)" % (ob, _f(d), "+".join(ref), _f(mini, 2)))

    def pastilles_pastilles(self):
        pads = [o for o in self.sc.objets if o.genre == gc.PASTILLE
                and o.piece not in self.sc.infos_placement["absentes"]]
        idx = gc.IndexCases(case=1.0, marge=self.iso_max)
        for k, o in enumerate(pads):
            idx.ajouter(k, o.boite)
        pires = {}
        vues = set()
        for i, o in enumerate(pads):
            for lst in idx.listes(o.boite):
                for j in lst:
                    if j <= i or (i, j) in vues:
                        continue
                    vues.add((i, j))
                    q = pads[j]
                    if q.piece == o.piece or (self.entree[o.piece] and self.entree[q.piece]):
                        continue
                    if not (o.masque & q.masque) or gc.meme_reseau(o, q):
                        continue
                    requis = self.matrice[self.classe(o)][self.classe(q)]
                    if gc.distance_boites(o.boite, q.boite) >= requis:
                        continue
                    e = gc.ecart_objets(o, q)
                    if e is not None and e < requis - gc.EPS_ISOLEMENT:
                        cle = tuple(sorted((o.piece, q.piece)))
                        if cle not in pires or e - requis < pires[cle][0] - pires[cle][1]:
                            pires[cle] = (e, requis, o.id, q.id)
        for (a, b), (e, requis, oa, ob) in sorted(pires.items()):
            self.ajouter("pastille_pastille", [a, b], "%s / %s : écart %s mm < %s mm requis"
                         % (oa, ob, _f(e, 4), _f(requis, 4)))

    def zones_imposees(self):
        for i, z in enumerate(gc.liste(gc.exiger(self.d, "zones_imposees", "donnees"), "donnees.zones_imposees")):
            ch = "donnees.zones_imposees[%d]" % i
            zone = gc.forme_polygone(gc.points(gc.exiger(z, "polygone", ch), ch + ".polygone"), chemin=ch + ".polygone")
            faces = z.get("faces") or []
            etiquette = z.get("etiquette") or "zone %d" % (i + 1)
            for nom in gc.liste(gc.exiger(z, "pieces", ch), ch + ".pieces"):
                if nom not in self.comps or nom in self.sc.infos_placement["absentes"]:
                    continue
                if faces and self.face(nom) not in faces:
                    self.ajouter("zone_imposee", [nom], "%s : face %s hors des faces de la zone imposée %s"
                                 % (nom, self.face(nom), etiquette))
                elif not gc.inclus_dans_polygone(self.boites[nom]["nue"], zone):
                    self.ajouter("zone_imposee", [nom], "%s : boîte hors de la zone imposée %s" % (nom, etiquette))

    def bords(self):
        for nom, (cote, debord) in self.au_bord.items():
            if nom not in self.comps or nom in self.sc.infos_placement["absentes"]:
                continue
            c, e = self._cote_de(nom)
            if e > EPS_POSE_MM:
                self.ajouter("au_bord", [nom], "%s : boîte à %s mm du côté %s de la carte (débord %s mm)"
                             % (nom, _f(e), c if cote != "quelconque" else "le plus proche (%s)" % c, _f(debord, 2)))

    def groupes_rigides(self):
        for i, g in enumerate(gc.liste(gc.exiger(self.d, "groupes", "donnees"), "donnees.groupes")):
            if not g.get("rigide"):
                continue
            ensembles = [g["pieces"]] if g.get("pieces") else [e.get("pieces") or [] for e in g.get("exemplaires") or []]
            for pieces in ensembles:
                pieces = [p for p in pieces if p in self.comps and p not in self.sc.infos_placement["absentes"]]
                if len(pieces) < 2:
                    continue
                a = pieces[0]
                ea = gc.Pose(*self.comps[a]["pose_cible"])
                pa = self.poses[a]
                for b in pieces[1:]:
                    eb = gc.Pose(*self.comps[b]["pose_cible"])
                    pb = self.poses[b]
                    l0 = ea.local(eb.x, eb.y)
                    l1 = pa.local(pb.x, pb.y)
                    r0 = (eb.rotation - ea.rotation) * (-1.0 if ea.dessous else 1.0)
                    r1 = (pb.rotation - pa.rotation) * (-1.0 if pa.dessous else 1.0)
                    if (math.hypot(l0[0] - l1[0], l0[1] - l1[1]) > EPS_POSE_MM
                            or gc.ecart_angle(r0, r1) > gc.EPS_ANGLE
                            or (ea.dessous != eb.dessous) != (pa.dessous != pb.dessous)):
                        self.ajouter("groupe_rigide", [a, b], "groupe rigide %s : pose de %s relative à %s différente "
                                     "de celle de l'entrée" % (g.get("nom") or i, b, a))

    def executer(self):
        self.presence()
        self.poses_et_orientations()
        self.chevauchements()
        self.obstacles()
        self.marges()
        self.eloignements()
        self.pastilles_pastilles()
        self.zones_imposees()
        self.bords()
        self.groupes_rigides()
        return {"format": FORMAT, "valide": not self.violations, "violations": self.violations,
                "violations_par_type": dict(self.par_type), "infos": self.infos}


def verifier(placements, donnees, grille=True):
    """Contrôle d'un placement (``placements/2`` ou liste v1) contre ``donnees_placeur/2`` : ``controle_placement/1``."""
    return ControlePlacement(placements, donnees, grille).executer()


# ---------------------------------------------------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------------------------------------------------
OPTIONS = (
    gc.Option("--donnees", "données du moteur (donnees_placeur/2)", obligatoire=True, meta="D"),
    gc.Option("--placement", "placement (placements/2 ou liste v1)", obligatoire=True, meta="P"),
    gc.Option("--sortie", "fichier controle_placement/1 écrit (facultatif)", meta="S"),
    gc.Option("--sans-grille", "ne pas contrôler la grille de placement", nargs=0),
)


def _charger(chemin, fmt):
    try:
        return formats.charger(chemin, fmt)
    except formats.ErreurFormat as e:
        raise gc.ErreurEntree("; ".join(e.erreurs))


def _principal(args):
    donnees = _charger(args["donnees"], "donnees_placeur")
    placements = _charger(args["placement"], "placements")
    v = verifier(placements, donnees, grille=not args["sans_grille"])
    if args.get("sortie"):
        gc.ecrire_json(args["sortie"], v)
    vt = ", ".join("%s %d" % (t, n) for t, n in v["violations_par_type"].items() if n)
    gc.afficher("placement %s : %d violation(s)%s" % ("valide" if v["valide"] else "invalide", len(v["violations"]),
                                                      (" (%s)" % vt) if vt else ""))
    for x in v["violations"][:30]:
        gc.afficher("  - " + x["texte"])
    return gc.CODE_SUCCES if v["valide"] else gc.CODE_NEGATIF


def main(argv=None):
    """Ligne de commande (§5.2) ; rend le code de sortie (§1.4) sans jamais quitter le processus."""
    return gc.executer(_principal, argv, OPTIONS, "verifier_placement.py",
                       "Contrôle indépendant du placement (controle_placement/1, §4.5 du contrat).")


if __name__ == "__main__":
    gc.configurer_flux()
    sys.exit(main())
