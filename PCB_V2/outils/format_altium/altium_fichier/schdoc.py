"""Lecture des schémas Altium (.SchDoc) et des projets (.PrjPcb).

.SchDoc = conteneur CFB avec :
  FileHeader : enregistrements [u32 longueur (octet haut = drapeau binaire)][propriétés texte] ;
               le premier est l'en-tête (HEADER=Protel for Windows - Schematic Capture Binary File Version 5.0,
               Weight = nombre d'objets), puis un enregistrement par objet (clé RECORD = type).
               OWNERINDEX = rang (à partir de 0, en-tête exclu) de l'objet parent.
  Storage    : images incorporées (en-tête texte puis enregistrements binaires zlib).
  Additional : enregistrements complémentaires (même format que FileHeader).
Coordonnées : LOCATION.X / LOCATION.Y en unités de 10 mil, + *_FRAC en 1/100000 de cette unité.
"""
from __future__ import annotations

import configparser
import io

from . import cfb
from .flux import FluxProps

TYPES_SCH = {
    "1": "composant", "2": "broche", "3": "symbole IEEE", "4": "étiquette texte", "5": "Bézier", "6": "polyligne",
    "7": "polygone", "8": "ellipse", "9": "camembert", "10": "rectangle arrondi", "11": "arc elliptique",
    "12": "arc", "13": "ligne", "14": "rectangle", "15": "symbole de feuille", "16": "entrée de feuille",
    "17": "port d'alimentation", "18": "port", "22": "pas d'ERC", "25": "étiquette de réseau", "26": "bus",
    "27": "fil", "28": "cadre de texte", "29": "jonction", "30": "image", "31": "feuille (options du document)",
    "32": "nom de feuille", "33": "nom de fichier de feuille", "34": "désignateur", "37": "entrée de bus",
    "39": "modèle de feuille", "41": "paramètre", "43": "panneau d'avertissement", "44": "liste d'implémentations",
    "45": "implémentation (modèle : empreinte, simulation...)", "46": "liste de correspondances",
    "47": "correspondance broche-pastille", "48": "paramètres d'implémentation", "209": "note",
    "215": "connecteur de faisceau", "216": "entrée de faisceau", "217": "type de faisceau", "218": "faisceau",
    "226": "conteneur de paramètres", "228": "boîte de requête",
}


def _coord(p, base):
    v = p.geti(base)
    f = p.geti(base + "_FRAC")
    if v is None and f is None:
        return 0.0
    return (int(v or 0) + int(f or 0) / 100000.0) * 10.0  # mil


class SchDoc:
    def __init__(self, racine: cfb.Noeud, chemin=None):
        self.racine = racine
        self.chemin = chemin
        self.flux = {c: FluxProps(n.donnees) for c, n in racine.flux() if c in ("FileHeader", "Additional")}
        self.autres = {c: n.donnees for c, n in racine.flux() if c not in self.flux}
        fh = self.flux["FileHeader"]
        self.entete = fh.enregistrements[0].props
        self.objets = []  # (rang, props) hors en-tête
        for i, e in enumerate(fh.enregistrements[1:]):
            self.objets.append(e.props)

    @classmethod
    def ouvrir(cls, chemin):
        return cls(cfb.lire_cfb(chemin), chemin)

    def aller_retour_ok(self):
        return all(f.encoder() == self.racine.lire(c) for c, f in self.flux.items())

    def enfants(self, rang):
        s = str(rang)
        return [(i, p) for i, p in enumerate(self.objets) if p is not None and p.geti("OWNERINDEX") == s]

    def par_type(self, rec):
        return [(i, p) for i, p in enumerate(self.objets) if p is not None and p.geti("RECORD") == str(rec)]

    # ------------------------------------------------------------------
    def composants(self):
        out = []
        for i, p in self.par_type(1):
            enf = self.enfants(i)
            des = next((q.texte("TEXT") for _, q in enf if q.geti("RECORD") == "34"), None)
            params = {q.texte("NAME"): q.texte("TEXT") for _, q in enf if q.geti("RECORD") == "41"}
            empreintes = []
            for j, q in enf:
                if q.geti("RECORD") == "44":
                    for _, m in self.enfants(j):
                        if m.geti("RECORD") == "45":
                            empreintes.append({"type": m.geti("MODELTYPE"), "nom": m.texte("MODELNAME"),
                                               "courant": m.geti("ISCURRENT") == "T", "description": m.texte("DESCRIPTION")})
            broches = [{"designateur": q.texte("DESIGNATOR"), "nom": q.texte("NAME"),
                        "x": _coord(q, "LOCATION.X"), "y": _coord(q, "LOCATION.Y"),
                        "type_electrique": q.geti("ELECTRICAL"), "longueur": _coord(q, "PINLENGTH"),
                        "partie": q.geti("OWNERPARTID")}
                       for _, q in enf if q.geti("RECORD") == "2"]
            out.append({"rang": i, "designateur": des, "reference": p.texte("LIBREFERENCE"),
                        "description": p.texte("COMPONENTDESCRIPTION"), "unique_id": p.geti("UNIQUEID"),
                        "x": _coord(p, "LOCATION.X"), "y": _coord(p, "LOCATION.Y"),
                        "bibliotheque": p.texte("SOURCELIBRARYNAME"), "partie_courante": p.geti("CURRENTPARTID"),
                        "nb_parties": p.geti("PARTCOUNT"), "parametres": params, "empreintes": empreintes,
                        "broches": broches})
        return out

    def fils(self):
        out = []
        for i, p in self.par_type(27):
            n = int(p.geti("LOCATIONCOUNT", "0"))
            out.append([(_coord(p, f"X{k}"), _coord(p, f"Y{k}")) for k in range(1, n + 1)])
        return out

    def etiquettes_reseau(self):
        return [{"texte": p.texte("TEXT"), "x": _coord(p, "LOCATION.X"), "y": _coord(p, "LOCATION.Y")}
                for _, p in self.par_type(25)]

    def ports(self):
        return [{"nom": p.texte("NAME"), "x": _coord(p, "LOCATION.X"), "y": _coord(p, "LOCATION.Y"),
                 "io": p.geti("IOTYPE"), "unique_id": p.geti("UNIQUEID")} for _, p in self.par_type(18)]

    def ports_alimentation(self):
        return [{"texte": p.texte("TEXT"), "x": _coord(p, "LOCATION.X"), "y": _coord(p, "LOCATION.Y"),
                 "style": p.geti("STYLE")} for _, p in self.par_type(17)]

    def jonctions(self):
        return [(_coord(p, "LOCATION.X"), _coord(p, "LOCATION.Y")) for _, p in self.par_type(29)]

    def statistiques(self):
        import collections
        c = collections.Counter(p.geti("RECORD") for p in self.objets if p is not None)
        return {f"{k} ({TYPES_SCH.geti(k, '?')})": v for k, v in sorted(c.items(), key=lambda x: int(x[0] or 0))}


class PrjPcb:
    """Projet .PrjPcb : fichier INI (UTF-8 avec BOM). Le texte d'origine est conservé tel quel."""

    def __init__(self, texte: str, chemin=None):
        self.texte = texte
        self.chemin = chemin
        self.ini = configparser.ConfigParser(interpolation=None, strict=False)
        self.ini.optionxform = str
        self.ini.read_file(io.StringIO(texte))

    @classmethod
    def ouvrir(cls, chemin):
        with open(chemin, "r", encoding="utf-8-sig", newline="") as f:
            return cls(f.read(), chemin)

    def documents(self):
        out = []
        for s in self.ini.sections():
            if s.startswith("Document") and s[8:].isdigit():
                out.append(dict(self.ini[s]))
        return out

    def chemins_documents(self):
        return [d.get("DocumentPath") for d in self.documents()]

    def options(self, section="Design"):
        return dict(self.ini[section]) if self.ini.has_section(section) else {}

    def sections(self):
        return self.ini.sections()
