"""API.md : chaque fonction publique de PcbDoc et des modules de l'API y est décrite, et chaque nom cité existe."""
import inspect
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commun  # noqa: E402
from altium_fichier import cfb, chemins, couches, flux, gabarits, pcbdoc, pose, proprietes  # noqa: E402
from altium_fichier.pcbdoc import PcbDoc  # noqa: E402

MODULES = {"PcbDoc": PcbDoc, "pcbdoc": pcbdoc, "pose": pose, "chemins": chemins, "gabarits": gabarits,
           "couches": couches, "cfb": cfb, "flux": flux, "proprietes": proprietes}
# modules dont chaque fonction publique doit être décrite (flux : fonctions seulement, classes citées au besoin)
CITE = re.compile(r"`(" + "|".join(MODULES) + r")\.([A-Za-z_]\w*)")


def lire_api():
    with open(os.path.join(commun.RACINE, "API.md"), encoding="utf-8") as f:
        return f.read()


def publics(nom, objet):
    """Fonctions (et méthodes, propriétés, classes) publiques définies par `objet`."""
    if nom == "PcbDoc":
        return {n for n in vars(PcbDoc) if not n.startswith("_")}
    out = set()
    for n, v in vars(objet).items():
        if n.startswith("_") or getattr(v, "__module__", None) != objet.__name__:
            continue
        if inspect.isfunction(v) or (inspect.isclass(v) and nom != "flux"):
            out.add(n)
    return out


class TestApiDocumentee(unittest.TestCase):
    def test_api_documentee(self):
        texte = lire_api()
        cites = {(m, n) for m, n in CITE.findall(texte)}
        for nom, objet in MODULES.items():
            for n in sorted(publics(nom, objet)):
                with self.subTest(fonction=f"{nom}.{n}"):
                    self.assertTrue((nom, n) in cites, f"{nom}.{n} absent de API.md")
        for m, n in sorted(cites):
            with self.subTest(cite=f"{m}.{n}"):
                self.assertTrue(hasattr(MODULES[m], n), f"{m}.{n} cité dans API.md mais inexistant")

    def test_signatures_des_ajouts(self):
        """Interface exacte du contrat (§8.2, B1)."""
        attendu = {"ajouter_piste": "(self, couche, debut_mil, fin_mil, largeur_mil, reseau=None)",
                   "ajouter_via": "(self, pos_mil, diametre_mil, trou_mil, reseau=None)",
                   "ajouter_region": "(self, couche, contour_mil, reseau, trous=())",
                   "effacer_chemins": "(self, tout=False)"}
        texte = lire_api()
        for n, sig in attendu.items():
            self.assertEqual(str(inspect.signature(getattr(PcbDoc, n))), sig)
            self.assertIn(f"`PcbDoc.{n}{sig.replace('self, ', '')}`", texte)

    def test_sans_chemin_absolu(self):
        texte = lire_api()
        self.assertEqual(chemins.trouver_octets(texte.encode("utf-8")), [])
        self.assertNotRegex(texte, r"[A-Za-z]:\\")


if __name__ == "__main__":
    unittest.main(argv=commun.extraire_options(sys.argv))
