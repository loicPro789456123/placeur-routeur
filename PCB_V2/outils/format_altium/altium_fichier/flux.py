"""Codecs des flux d'un .PcbDoc : octets <-> objets, sans perte.

Chaque codec expose :
  decoder(octets) -> objet ; objet.encoder() -> octets ; objet.couverture() -> {"C","S","I"}: octets ;
  len(objet) -> nombre d'enregistrements (0 pour un flux vide).
Un flux inconnu est conservé brut (FluxBrut) et compté comme inconnu.

Table unique `CODECS` : stockage -> (classe, options) du codec de son flux `Data`. Chaque codec accepte
0 octet : le `Data` vide d'un stockage connu donne un objet du même type que s'il était plein, sans
enregistrement, réencodé en 0 octet (`flux_vide`).
"""
from __future__ import annotations

import re
import struct
import zlib

from . import gabarits as G
from .proprietes import Proprietes

U32 = struct.Struct("<I")


def _cov(c=0, s=0, i=0):
    return {"C": c, "S": s, "I": i}


def _add(a, b):
    for k in a:
        a[k] += b[k]
    return a


# =============================================================================
class FluxBrut:
    """Flux non interprété (conservé tel quel)."""
    nature = "brut"

    def __init__(self, octets: bytes, statut="I", note=""):
        self.octets = octets
        self.statut = statut
        self.note = note

    def __len__(self):
        return 1 if self.octets else 0

    def encoder(self):
        return self.octets

    def couverture(self):
        r = _cov()
        r[self.statut] = len(self.octets)
        return r


class FluxCompteur:
    """Flux `Header` : u32 = nombre d'enregistrements du flux Data voisin."""
    nature = "compteur"

    def __init__(self, octets):
        self.reste = b""
        if len(octets) == 4:
            self.valeur = U32.unpack(octets)[0]
        else:
            self.valeur = None
            self.reste = octets

    def __len__(self):
        return 0 if self.valeur is None and not self.reste else 1

    def encoder(self):
        return U32.pack(self.valeur) if self.valeur is not None else self.reste

    def couverture(self):
        return _cov(c=4) if self.valeur is not None else _cov(i=len(self.reste))


# =============================================================================
# Propriétés texte : [préfixe fixe][u32 longueur (octet haut = drapeaux)][texte]
# =============================================================================
class EnrProps:
    __slots__ = ("prefixe", "drapeaux", "props", "binaire")

    def __init__(self, props, prefixe=b"", drapeaux=0, binaire=None):
        self.props = props
        self.prefixe = prefixe
        self.drapeaux = drapeaux
        self.binaire = binaire  # si drapeaux != 0 : contenu binaire non textuel

    def encoder(self):
        corps = self.binaire if self.binaire is not None else self.props.vers_octets()
        return self.prefixe + U32.pack(len(corps) | (self.drapeaux << 24)) + corps


class FluxProps:
    nature = "proprietes"

    def __init__(self, octets: bytes, taille_prefixe=0, cles=None):
        self.taille_prefixe = taille_prefixe
        self.enregistrements: list[EnrProps] = []
        self.reste = b""
        self.cles = cles  # dictionnaire de clés connues (pour la couverture sémantique)
        p, n = 0, len(octets)
        while p < n:
            if p + taille_prefixe + 4 > n:
                break
            pre = octets[p:p + taille_prefixe]
            L = U32.unpack_from(octets, p + taille_prefixe)[0]
            dr, L = L >> 24, L & 0xFFFFFF
            debut = p + taille_prefixe + 4
            if debut + L > n:
                break
            corps = octets[debut:debut + L]
            if dr == 0:
                e = EnrProps(Proprietes.depuis_octets(corps), pre, 0)
                if e.props.vers_octets() != corps:  # sécurité : ne devrait jamais arriver
                    e = EnrProps(None, pre, 0, corps)
            else:
                e = EnrProps(None, pre, dr, corps)
            self.enregistrements.append(e)
            p = debut + L
        self.reste = octets[p:]

    def __iter__(self):
        return iter(self.enregistrements)

    def __len__(self):
        return len(self.enregistrements)

    def __getitem__(self, i) -> Proprietes:
        return self.enregistrements[i].props

    def ajouter(self, props: Proprietes, prefixe=b""):
        self.enregistrements.append(EnrProps(props, prefixe or b"\0" * self.taille_prefixe))
        return len(self.enregistrements) - 1

    def encoder(self):
        return b"".join(e.encoder() for e in self.enregistrements) + self.reste

    def couverture(self):
        r = _cov(i=len(self.reste))
        for e in self.enregistrements:
            r["C"] += len(e.prefixe) + 4
            if e.binaire is not None:
                r["I"] += len(e.binaire)
                continue
            r["C"] += len(e.props.terminateur)
            n = len(e.props.elements)
            for j, (k, v) in enumerate(e.props.elements):
                t = len(k.encode("latin-1")) + (0 if v is None else 1 + len(v.encode("latin-1"))) + (1 if j < n - 1 else 0)
                st = self.cles.statut(k) if self.cles else "C"
                r[st] += t
        return r


# =============================================================================
# Primitives binaires : [u8 type][n x (u32 longueur, octets)]
# =============================================================================
NB_SOUS = {1: 1, 2: 6, 3: 1, 4: 1, 5: 2, 6: 1, 11: 1, 12: 1}
NOMS_TYPES = {1: "arc", 2: "pastille", 3: "via", 4: "piste", 5: "texte", 6: "remplissage",
              11: "region", 12: "corps3d"}


class ChainePascal:
    """Sous-enregistrement « chaîne courte » : u8 longueur + octets (+ reste éventuel)."""
    __slots__ = ("texte", "reste")

    def __init__(self, b: bytes):
        n = b[0] if b else 0
        if b and 1 + n <= len(b):
            self.texte = b[1:1 + n].decode("latin-1")
            self.reste = b[1 + n:]
        else:
            self.texte = None
            self.reste = b

    def encoder(self):
        if self.texte is None:
            return self.reste
        t = self.texte.encode("latin-1")
        return bytes([len(t)]) + t + self.reste

    def couverture(self):
        if self.texte is None:
            return _cov(i=len(self.reste))
        return _cov(c=1 + len(self.texte), i=len(self.reste))


class SousBrut:
    __slots__ = ("octets",)

    def __init__(self, b):
        self.octets = b

    def encoder(self):
        return self.octets

    def couverture(self):
        return _cov(i=len(self.octets))


class SousBloc:
    __slots__ = ("bloc",)

    def __init__(self, bloc):
        self.bloc = bloc

    def encoder(self):
        return G.encoder_bloc(self.bloc)

    def couverture(self):
        return G.couverture_bloc(self.bloc)


class SousRegion:
    """Région / corps 3D : en-tête 18 o + propriétés + contour + trous."""
    __slots__ = ("entete", "drapeaux_props", "props", "etendu", "contour", "trous", "reste", "_brut_props")

    def __init__(self, b: bytes, etendu: bool):
        self.etendu = etendu
        self.entete = G.decoder_bloc(G.ENTETE_REGION, b[:18], "entete_region")
        p = 18
        L = U32.unpack_from(b, p)[0]
        self.drapeaux_props, L = L >> 24, L & 0xFFFFFF
        p += 4
        self.props = Proprietes.depuis_octets(b[p:p + L])
        p += L
        self.contour, self.trous, self.reste = [], [], b""
        try:
            nb = U32.unpack_from(b, p)[0]
            p += 4
            if etendu:
                nb += 1  # sommet de fermeture
                for _ in range(nb):
                    self.contour.append(struct.unpack_from("<B5i2d", b, p))
                    p += 37
            else:
                for _ in range(nb):
                    self.contour.append(struct.unpack_from("<2d", b, p))
                    p += 16
            for _ in range(self.entete.get("nb_trous", 0)):
                nt = U32.unpack_from(b, p)[0]
                p += 4
                self.trous.append([struct.unpack_from("<2d", b, p + 16 * i) for i in range(nt)])
                p += 16 * nt
        except struct.error:
            # structure inattendue : on conserve tout ce qui suit les propriétés
            self.contour, self.trous = None, None
            p = 18 + 4 + L
        self.reste = b[p:]
        assert self.encoder() == b, "région : aller-retour impossible"

    def encoder(self):
        out = bytearray(G.encoder_bloc(self.entete))
        pb = self.props.vers_octets()
        out += U32.pack(len(pb) | (self.drapeaux_props << 24)) + pb
        if self.contour is not None:
            if self.etendu:
                out += U32.pack(len(self.contour) - 1)
                for s in self.contour:
                    out += struct.pack("<B5i2d", *s)
            else:
                out += U32.pack(len(self.contour))
                for s in self.contour:
                    out += struct.pack("<2d", *s)
            for t in self.trous:
                out += U32.pack(len(t))
                for s in t:
                    out += struct.pack("<2d", *s)
        out += self.reste
        return bytes(out)

    def couverture(self):
        r = G.couverture_bloc(self.entete)
        pb = len(self.props.vers_octets())
        r["C"] += 4 + pb
        if self.contour is not None:
            r["C"] += 4 + len(self.contour) * (37 if self.etendu else 16)
            r["C"] += sum(4 + 16 * len(t) for t in self.trous)
        r["I"] += len(self.reste)
        return r


class Primitive:
    __slots__ = ("type", "sous")

    def __init__(self, type_, sous):
        self.type = type_
        self.sous = sous

    @property
    def nom_type(self):
        return NOMS_TYPES.get(self.type, f"type{self.type}")

    @property
    def bloc(self) -> G.Bloc:
        """Bloc principal (sous-enregistrement des coordonnées)."""
        if self.type == 2:
            return self.sous[4].bloc
        s = self.sous[0]
        return s.entete if isinstance(s, SousRegion) else s.bloc

    def encoder(self):
        out = bytearray([self.type])
        for s in self.sous:
            b = s.encoder()
            out += U32.pack(len(b)) + b
        return bytes(out)

    def couverture(self):
        r = _cov(c=1 + 4 * len(self.sous))
        for s in self.sous:
            _add(r, s.couverture())
        return r


def _decoder_sous(type_, idx, b, etendu):
    if type_ == 1:
        return SousBloc(G.decoder_bloc(G.ARC, b, "arc"))
    if type_ == 3:
        return SousBloc(G.decoder_bloc(G.VIA, b, "via"))
    if type_ == 4:
        return SousBloc(G.decoder_bloc(G.PISTE, b, "piste"))
    if type_ == 6:
        return SousBloc(G.decoder_bloc(G.REMPLI, b, "remplissage"))
    if type_ == 5:
        if idx == 0:
            return SousBloc(G.decoder_bloc(G.TEXTE, b, "texte"))
        return ChainePascal(b)
    if type_ == 2:
        if idx in (0, 1, 2, 3):
            return ChainePascal(b) if b else SousBrut(b)
        if idx == 4:
            return SousBloc(G.decoder_bloc(G.PASTILLE, b, "pastille"))
        if idx == 5:
            return SousBloc(G.decoder_bloc(G.PASTILLE_PILE, b, "pastille_pile")) if b else SousBrut(b)
    if type_ in (11, 12):
        try:
            return SousRegion(b, etendu)
        except Exception:
            return SousBrut(b)
    return SousBrut(b)


class FluxPrimitives:
    nature = "primitives"

    def __init__(self, octets: bytes, etendu=False):
        self.etendu = etendu
        self.primitives: list[Primitive] = []
        p, n = 0, len(octets)
        while p < n:
            t = octets[p]
            if t not in NB_SOUS:
                break
            q = p + 1
            sous = []
            ok = True
            for i in range(NB_SOUS[t]):
                if q + 4 > n:
                    ok = False
                    break
                L = U32.unpack_from(octets, q)[0]
                if q + 4 + L > n:
                    ok = False
                    break
                sous.append(_decoder_sous(t, i, octets[q + 4:q + 4 + L], etendu))
                q += 4 + L
            if not ok:
                break
            self.primitives.append(Primitive(t, sous))
            p = q
        self.reste = octets[p:]

    def __iter__(self):
        return iter(self.primitives)

    def __len__(self):
        return len(self.primitives)

    def __getitem__(self, i):
        return self.primitives[i]

    def encoder(self):
        return b"".join(pr.encoder() for pr in self.primitives) + self.reste

    def couverture(self):
        r = _cov(i=len(self.reste))
        for pr in self.primitives:
            _add(r, pr.couverture())
        return r


# =============================================================================
# Flux spécifiques
# =============================================================================
class FluxChainesLarges:
    """WideStrings6 : suite de (u32 index, u32 longueur en octets, UTF-16LE terminé par NUL)."""
    nature = "chaines_larges"

    def __init__(self, octets):
        self.chaines = []  # [index, texte, brut_si_anormal]
        p, n = 0, len(octets)
        while p + 8 <= n:
            idx, L = struct.unpack_from("<II", octets, p)
            if p + 8 + L > n:
                break
            brut = octets[p + 8:p + 8 + L]
            t = brut[:-2].decode("utf-16-le", "surrogatepass") if L >= 2 else ""
            normal = L >= 2 and brut[-2:] == b"\0\0" and t.encode("utf-16-le", "surrogatepass") + b"\0\0" == brut
            self.chaines.append([idx, t if normal else None, None if normal else brut])
            p += 8 + L
        self.reste = octets[p:]

    def __len__(self):
        return len(self.chaines)

    def dict(self):
        return {i: t for i, t, _ in self.chaines}

    def ajouter(self, texte):
        idx = max([c[0] for c in self.chaines], default=-1) + 1
        self.chaines.append([idx, texte, None])
        return idx

    def encoder(self):
        out = bytearray()
        for idx, t, brut in self.chaines:
            b = brut if brut is not None else t.encode("utf-16-le", "surrogatepass") + b"\0\0"
            out += struct.pack("<II", idx, len(b)) + b
        return bytes(out) + self.reste

    def couverture(self):
        tot = len(self.encoder())
        return _cov(c=tot - len(self.reste), i=len(self.reste))


class FluxGuids:
    """PrimitiveGuids : suite de (u32 type de primitive, u32 index, GUID 16 o) = 24 o."""
    nature = "guids"

    def __init__(self, octets):
        n = len(octets) // 24
        self.entrees = [struct.unpack_from("<II16s", octets, 24 * i) for i in range(n)]
        self.reste = octets[24 * n:]

    def __len__(self):
        return len(self.entrees)

    def encoder(self):
        return b"".join(struct.pack("<II16s", *e) for e in self.entrees) + self.reste

    def couverture(self):
        return _cov(s=24 * len(self.entrees), i=len(self.reste))


def _chaine_u32_utf16(b, p):
    L = U32.unpack_from(b, p)[0]
    return b[p + 4:p + 4 + L].decode("utf-16-le"), p + 4 + L


class FluxPolices:
    """EmbeddedFonts6 : par police : 3 chaînes (u32 long. + UTF-16) [famille, nom, style],
    2 octets inconnus, u8 indicateur, u32 longueur, police TrueType compressée zlib."""
    nature = "polices"

    def __init__(self, octets):
        self.polices = []
        p = 0
        try:
            while p < len(octets):
                q = p
                noms = []
                for _ in range(3):
                    s, q = _chaine_u32_utf16(octets, q)
                    noms.append(s)
                inc = octets[q:q + 3]
                q += 3
                L = U32.unpack_from(octets, q)[0]
                q += 4
                donnees = octets[q:q + L]
                if len(donnees) != L:
                    raise ValueError
                self.polices.append({"noms": noms, "inconnu": inc, "zlib": donnees})
                p = q + L
        except Exception:
            pass
        self.reste = octets[p:]

    def __len__(self):
        return len(self.polices)

    def encoder(self):
        out = bytearray()
        for f in self.polices:
            for s in f["noms"]:
                e = s.encode("utf-16-le")
                out += U32.pack(len(e)) + e
            out += f["inconnu"] + U32.pack(len(f["zlib"])) + f["zlib"]
        return bytes(out) + self.reste

    def ttf(self, i=0):
        return zlib.decompress(self.polices[i]["zlib"])

    def couverture(self):
        tot = len(self.encoder()) - len(self.reste)
        inc = 3 * len(self.polices)
        return _cov(c=tot - inc, i=inc + len(self.reste))


class FluxCorrespondanceCouches:
    """LayerKindMapping : u32 long. + UTF-16 version ('1.0'), u32 inconnu, u32 nombre,
    puis nombre x (u32 identifiant de couche, u32 type mécanique)."""
    nature = "types_couches"

    def __init__(self, octets):
        self.version, self.inconnu, self.paires, self.reste = None, 0, [], octets
        try:
            version, p = _chaine_u32_utf16(octets, 0)
            inconnu, n = struct.unpack_from("<II", octets, p)
            p += 8
            paires = [struct.unpack_from("<II", octets, p + 8 * i) for i in range(n)]
        except (struct.error, UnicodeDecodeError):
            return  # vide ou structure inattendue : conservé brut dans `reste`
        self.version, self.inconnu, self.paires = version, inconnu, paires
        self.reste = octets[p + 8 * n:]

    def __len__(self):
        return len(self.paires)

    def encoder(self):
        if self.version is None and not self.paires:
            return self.reste
        v = (self.version if self.version is not None else "1.0").encode("utf-16-le")
        out = U32.pack(len(v)) + v + struct.pack("<II", self.inconnu, len(self.paires))
        return out + b"".join(struct.pack("<II", *c) for c in self.paires) + self.reste

    def couverture(self):
        if self.version is None:
            return _cov(i=len(self.encoder()))
        tot = len(self.encoder())
        return _cov(c=tot - 4 - len(self.reste), s=0, i=4 + len(self.reste))


class FluxEnteteFichier:
    """FileHeader : u32 nombre de caractères + chaîne UTF-16 (tronquée par Altium à 24 octets)."""
    nature = "entete"

    def __init__(self, octets):
        self.octets = octets
        self.n = U32.unpack_from(octets)[0] if len(octets) >= 4 else None
        self.texte = octets[4:].decode("utf-16-le", "replace")

    def __len__(self):
        return 1 if self.octets else 0

    def encoder(self):
        return self.octets

    def couverture(self):
        return _cov(c=len(self.octets))


class FluxEnteteSix:
    """FileHeaderSix : u32 + chaîne pascal 'PCB 6.0 Binary File', f64 version (5.01),
    u32 + chaîne pascal GUID du document. Un flux de structure inattendue est conservé brut."""
    nature = "entete6"

    def __init__(self, octets):
        self.octets = octets
        self.titre = self.version = self.guid = None
        self._n = (0, 0)
        self.reste = octets
        try:
            p = 0
            n1 = U32.unpack_from(octets, p)[0]; p += 4
            l1 = octets[p]; p += 1
            titre = octets[p:p + l1].decode("latin-1"); p += l1
            version = struct.unpack_from("<d", octets, p)[0]; p += 8
            n2 = U32.unpack_from(octets, p)[0]; p += 4
            l2 = octets[p]; p += 1
            guid = octets[p:p + l2].decode("latin-1"); p += l2
            if p > len(octets):
                raise IndexError
        except (struct.error, IndexError):
            return
        self.titre, self.version, self.guid, self._n = titre, version, guid, (n1, n2)
        self.reste = octets[p:]

    def __len__(self):
        return 1 if self.octets else 0

    def encoder(self):
        if self.titre is None:
            return self.reste
        t, g = self.titre.encode("latin-1"), self.guid.encode("latin-1")
        return (U32.pack(self._n[0]) + bytes([len(t)]) + t + struct.pack("<d", self.version)
                + U32.pack(self._n[1]) + bytes([len(g)]) + g + self.reste)

    def couverture(self):
        if self.titre is None:
            return _cov(i=len(self.reste))
        return _cov(c=len(self.octets) - len(self.reste), i=len(self.reste))


class FluxModele3D:
    """Models/N : modèle STEP compressé zlib. Les octets compressés d'origine sont conservés
    (une recompression ne redonnerait pas les mêmes octets)."""
    nature = "modele3d"

    def __init__(self, octets):
        self.octets = octets
        try:
            d = zlib.decompressobj()
            self.taille_step = len(d.decompress(octets))
            self.reste_zlib = d.unused_data
            self.valide = d.eof
        except zlib.error:
            self.valide = False
            self.taille_step = None

    def __len__(self):
        return 1 if self.octets else 0

    def step(self) -> bytes:
        return zlib.decompress(self.octets)

    def remplacer_step(self, step: bytes):
        self.octets = zlib.compress(step, 9)

    def encoder(self):
        return self.octets

    def couverture(self):
        return _cov(c=len(self.octets)) if self.valide else _cov(i=len(self.octets))


class FluxGestionnairePercage:
    """DrillManager : i32 (-1), u32 nombre de groupes, puis par groupe :
    u32 longueur + propriétés (outil de perçage), puis deux listes (u32 n, n x u32) :
    index des pastilles puis index des vias utilisant cet outil (supposé, d'après OBJECTID)."""
    nature = "percage"

    def __init__(self, octets):
        self.groupes = []
        self._n_bruts = 0
        self.a = None  # None : flux vide ou en-tête illisible (tout est conservé dans `reste`)
        if len(octets) < 8:
            self.reste = octets
            return
        self.a, n = struct.unpack_from("<iI", octets, 0)
        p = 8
        dernier_ok = p
        try:
            for _ in range(n):
                L = U32.unpack_from(octets, p)[0]
                if p + 4 + L > len(octets):
                    break
                pr = Proprietes.depuis_octets(octets[p + 4:p + 4 + L])
                p += 4 + L
                listes = []
                for _ in range(2):
                    k = U32.unpack_from(octets, p)[0]
                    listes.append(list(struct.unpack_from(f"<{k}I", octets, p + 4)))
                    p += 4 + 4 * k
                self.groupes.append((pr, listes))
                dernier_ok = p
        except struct.error:
            pass
        # groupes annoncés mais non décodés (structure inattendue) : restent bruts dans `reste`
        self._n_bruts = n - len(self.groupes)
        self.reste = octets[dernier_ok:]

    def __len__(self):
        return len(self.groupes)

    def encoder(self):
        if self.a is None:
            if not self.groupes:
                return self.reste
            self.a, self._n_bruts = -1, 0  # premier groupe ajouté dans un flux vide : en-tête observé (i32 -1)
        out = bytearray(struct.pack("<iI", self.a, len(self.groupes) + self._n_bruts))
        for pr, listes in self.groupes:
            b = pr.vers_octets()
            out += U32.pack(len(b)) + b
            for idx in listes:
                out += U32.pack(len(idx)) + struct.pack(f"<{len(idx)}I", *idx)
        return bytes(out) + self.reste

    def couverture(self):
        if self.a is None:
            return _cov(i=len(self.reste))
        tot = len(self.encoder()) - len(self.reste)
        return _cov(c=tot - 4, s=4, i=len(self.reste))


class FluxBlocsLongueur:
    """Suite de (u32 longueur, bloc binaire) décodés avec un gabarit (Connections6)."""
    nature = "blocs"

    def __init__(self, octets, gabarit=None, nom="connexion"):
        gabarit = G.CONNEXION if gabarit is None else gabarit
        self.blocs = []
        p = 0
        while p + 4 <= len(octets):
            L = U32.unpack_from(octets, p)[0]
            if p + 4 + L > len(octets):
                break
            self.blocs.append(G.decoder_bloc(gabarit, octets[p + 4:p + 4 + L], nom))
            p += 4 + L
        self.reste = octets[p:]

    def __iter__(self):
        return iter(self.blocs)

    def __len__(self):
        return len(self.blocs)

    def __getitem__(self, i):
        return self.blocs[i]

    def encoder(self):
        out = bytearray()
        for b in self.blocs:
            e = G.encoder_bloc(b)
            out += U32.pack(len(e)) + e
        return bytes(out) + self.reste

    def couverture(self):
        r = _cov(c=4 * len(self.blocs), i=len(self.reste))
        for b in self.blocs:
            _add(r, G.couverture_bloc(b))
        return r


class FluxChaineUtf16:
    """u32 longueur + UTF-16LE (ConstraintManager : base64 d'un zlib)."""
    nature = "chaine_utf16"

    def __init__(self, octets):
        self.octets = octets
        L = U32.unpack_from(octets)[0] if len(octets) >= 4 else -1
        self.ok = L == len(octets) - 4
        self.texte = octets[4:].decode("utf-16-le", "replace") if self.ok else None

    def __len__(self):
        return 1 if self.octets else 0

    def encoder(self):
        return self.octets

    def couverture(self):
        return _cov(c=len(self.octets)) if self.ok else _cov(i=len(self.octets))


class FluxGeometrieLettres:
    """LettersGeometry : cache de géométrie des glyphes TrueType (polygones des lettres).
    Structure : suite de blocs (u32 longueur + UTF-16 caractère/police, puis données binaires).
    Décodage partiel : la structure interne n'est pas nécessaire pour réécrire le fichier
    (cache recalculé par Altium, confirmé par l'expérience E10)."""
    nature = "lettres"

    def __init__(self, octets):
        self.octets = octets

    def __len__(self):
        return 1 if self.octets else 0

    def encoder(self):
        return self.octets

    def couverture(self):
        return _cov(i=len(self.octets))


class FluxNomsUnions:
    """UnionNames : u32 n, puis n x (u32 index d'union, u32 longueur en octets, UTF-16LE terminé par NUL).
    Le flux `Header` voisin vaut 1 (une table), quel que soit n. Structure inattendue : conservée brute."""
    nature = "noms_unions"

    def __init__(self, octets):
        self.noms, self.reste, self.n = [], octets, None
        if len(octets) < 4:
            return
        n = U32.unpack_from(octets, 0)[0]
        p, noms = 4, []
        try:
            for _ in range(n):
                idx, L = struct.unpack_from("<II", octets, p)
                brut = octets[p + 8:p + 8 + L]
                if len(brut) != L or L < 2 or brut[-2:] != b"\0\0":
                    raise ValueError
                t = brut[:-2].decode("utf-16-le")
                if t.encode("utf-16-le") + b"\0\0" != brut:
                    raise ValueError
                noms.append([idx, t])
                p += 8 + L
        except (struct.error, ValueError, UnicodeDecodeError):
            return
        self.noms, self.n, self.reste = noms, n, octets[p:]

    def __len__(self):
        return len(self.noms)

    def encoder(self):
        if self.n is None and not self.noms:
            return self.reste
        out = bytearray(U32.pack(len(self.noms)))
        for idx, t in self.noms:
            b = t.encode("utf-16-le") + b"\0\0"
            out += struct.pack("<II", idx, len(b)) + b
        return bytes(out) + self.reste

    def couverture(self):
        if self.n is None:
            return _cov(i=len(self.reste))
        return _cov(c=len(self.encoder()) - len(self.reste), i=len(self.reste))


# =============================================================================
# Table unique : nom de stockage -> codec de son flux Data
# =============================================================================
FLUX_PROPS = {
    "Board6", "Classes6", "Components6", "Nets6", "Polygons6", "Models", "ModelsNoEmbed",
    "Advanced Placer Options6", "Design Rule Checker Options6", "Pin Swap Options6",
    "PadViaLibrary", "PadViaLibraryCache", "PadViaLibraryLinks", "PrimitiveParameters",
    "UniqueIDPrimitiveInformation", "SignalClasses", "SmartUnions", "FileVersionInfo",
    "TSilkToSolderMaskClearanceViola", "DifferentialPairs6", "FromTos6",
    "Coordinates6", "EmbeddedBoards6", "ExtendedPrimitiveInformation", "WaivedViolations",
    "Embeddeds6", "Textures", "PinPairsSection",
    # violations DRC enregistrées (observées) ; les autres sont reconnues par MOTIF_VIOLATIONS
    "TClearanceViolation", "TComponentClearanceViolation", "TNetAntennaeViolation",
    "TRoutingViaStyleViolation", "TShortCircuitViolation", "TSilkToSilkClearanceViolation",
}
FLUX_PROPS_PREFIXE2 = {"Rules6", "Dimensions6"}
FLUX_PRIMITIVES = {"Arcs6", "Pads6", "Vias6", "Tracks6", "Texts6", "Texts", "Fills6",
                   "Regions6", "ComponentBodies6", "BoardRegions"}
FLUX_PRIMITIVES_ETENDUS = {"ShapeBasedRegions6", "ShapeBasedComponentBodies6"}
# flux de violations DRC enregistrées : « T<type>Violation » (nom tronqué à 31 caractères par le CFB)
MOTIF_VIOLATIONS = re.compile(r"T\w*Viola(t(i(on?)?)?)?")

CODECS = {}
CODECS.update({st: (FluxProps, {"taille_prefixe": 0}) for st in FLUX_PROPS})
CODECS.update({st: (FluxProps, {"taille_prefixe": 2}) for st in FLUX_PROPS_PREFIXE2})
CODECS.update({st: (FluxPrimitives, {"etendu": False}) for st in FLUX_PRIMITIVES})
CODECS.update({st: (FluxPrimitives, {"etendu": True}) for st in FLUX_PRIMITIVES_ETENDUS})
CODECS.update({
    "Connections6": (FluxBlocsLongueur, {}),
    "WideStrings6": (FluxChainesLarges, {}),
    "PrimitiveGuids": (FluxGuids, {}),
    "EmbeddedFonts6": (FluxPolices, {}),
    "LayerKindMapping": (FluxCorrespondanceCouches, {}),
    "DrillManager": (FluxGestionnairePercage, {}),
    "ConstraintManager": (FluxChaineUtf16, {}),
    "LettersGeometry": (FluxGeometrieLettres, {}),
    "UnionNames": (FluxNomsUnions, {}),
})


def codec(storage):
    """(classe, options) du codec du flux `<storage>/Data`, ou None pour un stockage inconnu."""
    c = CODECS.get(storage)
    if c is None and MOTIF_VIOLATIONS.fullmatch(storage):
        c = (FluxProps, {"taille_prefixe": 0})
    return c


def decoder_data(storage, octets: bytes, cles=None):
    """Décode le flux `Data` d'un stockage connu (0 octet accepté) ; stockage inconnu : None."""
    c = codec(storage)
    if c is None:
        return None
    classe, options = c
    if classe is FluxProps:
        return FluxProps(octets, options["taille_prefixe"], cles(storage) if cles else None)
    return classe(octets, **options)


def flux_vide(storage, cles=None):
    """Objet vide du type du flux `Data` plein d'un stockage connu (réencodé en 0 octet) ; inconnu : None."""
    return decoder_data(storage, b"", cles)


def decoder_flux(chemin: str, octets: bytes, cles=None):
    parties = chemin.split("/")
    storage, feuille = (parties[0], parties[-1]) if len(parties) > 1 else ("", parties[0])
    if chemin == "FileHeader":
        return FluxEnteteFichier(octets)
    if chemin == "FileHeaderSix":
        return FluxEnteteSix(octets)
    if feuille == "Header" and len(parties) == 2:
        return FluxCompteur(octets)
    if storage == "Models" and feuille.isdigit():
        return FluxModele3D(octets)
    if feuille != "Data":
        if chemin == "LettersGeometry/PrimIndexes":
            return FluxBrut(octets, "I", "index de primitives du cache de lettres (zéros)")
        return FluxBrut(octets)
    if len(parties) == 2:
        f = decoder_data(storage, octets, cles)
        if f is not None:
            return f
    if not octets:
        return FluxBrut(b"", "C")
    # stockage inconnu : essai en propriétés texte
    c = cles(storage) if cles else None
    try:
        f = FluxProps(octets, 0, c)
        if not f.reste and len(f):
            return f
    except Exception:
        pass
    return FluxBrut(octets)
