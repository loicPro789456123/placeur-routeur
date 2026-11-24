"""Règles modifiables de la fenêtre (regles_utilisateur.py) et aperçu (apercu.py), sur la carte de démonstration."""
import os
import sys
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

import apercu  # noqa: E402
import formats  # noqa: E402
import importer_carte  # noqa: E402
import regles_utilisateur as RU  # noqa: E402

DEMO = os.path.join(ICI, "corpus", "pcbdoc", "demonstration.PcbDoc")


class TestRegles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entree = importer_carte.importer(DEMO)
        cls.lignes = RU.lignes(cls.entree)

    def valeurs(self, **changes):
        v = RU.valeurs_origine(self.lignes)
        v.update({k.replace("__", "."): x for k, x in changes.items()})
        return v

    def test_lignes_lues(self):
        cles = {l.cle for l in self.lignes}
        for c in ("largeur_defaut.0", "largeur_defaut.1", "largeur_defaut.2", "isolement.defaut", "via.diametre",
                  "via.trou", "trou_trou", "connexion_plans.isolement"):
            self.assertIn(c, cles)
        self.assertTrue(any(c.startswith("reseau:") for c in cles))
        self.assertEqual(RU.surcharges(self.entree, self.lignes, RU.valeurs_origine(self.lignes)), {})

    def test_lire_valeur(self):
        l = next(x for x in self.lignes if x.cle == "via.diametre")
        self.assertEqual(RU.lire_valeur(l, " 0,45 mm"), (0.45, None))
        for mauvais in ("", "abc", "-1", "0", "500"):
            v, err = RU.lire_valeur(l, mauvais)
            self.assertIsNotNone(err, mauvais)
        nul = next(x for x in self.lignes if x.nul_permis)
        self.assertEqual(RU.lire_valeur(nul, "—"), (None, None))

    def test_coherence(self):
        v = self.valeurs(largeur_defaut__1=0.5, largeur_defaut__2=0.3, via__trou=0.7)
        cles = {c for c, _ in RU.verifier(self.lignes, v)}
        self.assertIn("largeur_defaut.2", cles)
        self.assertIn("via.trou", cles)
        self.assertEqual(RU.verifier(self.lignes, RU.valeurs_origine(self.lignes)), [])

    def test_surcharge_valide_et_appliquee(self):
        v = self.valeurs(largeur_defaut__1=0.3, largeur_defaut__2=0.4, isolement__defaut=0.2, via__diametre=0.7)
        m = RU.surcharges(self.entree, self.lignes, v)
        ct = RU.fusionner({"format": "contraintes/2", "fixes": ["J1"]}, m)
        self.assertEqual(formats.valider_contraintes(ct), [])
        self.assertEqual(ct["fixes"], ["J1"])
        rg = RU.appliquer(self.entree["regles"], ct["regles"])
        self.assertEqual(rg["largeur_defaut"], [0.254, 0.3, 0.4])
        self.assertEqual(rg["isolement"]["defaut"], 0.2)
        self.assertTrue(all(x == 0.2 for r in rg["isolement"]["matrice"] for x in r))
        self.assertEqual(rg["via"]["diametre"], 0.7)
        propres = [n for n, r in rg["reseaux"].items() if r.get("regle_largeur_propre")]
        libres = [n for n, r in rg["reseaux"].items() if not r.get("regle_largeur_propre")]
        self.assertTrue(propres and libres)
        self.assertTrue(all(rg["reseaux"][n]["largeurs"] == [0.254, 0.3, 0.4] for n in libres))
        self.assertTrue(all(rg["reseaux"][n]["largeurs"] != [0.254, 0.3, 0.4] for n in propres))
        self.assertNotEqual(self.entree["regles"]["largeur_defaut"], [0.254, 0.3, 0.4])   # entrée intacte

    def test_reseau_inconnu_refuse(self):
        with self.assertRaises(RU.ErreurRegles):
            RU.appliquer(self.entree["regles"], {"reseaux": {"PAS_UN_RESEAU": {"largeurs": [0.2, 0.2, None]}}})
        with self.assertRaises(RU.ErreurRegles):
            RU.appliquer(self.entree["regles"], {"isolement": {"matrice": [[0.1]]}})

    def test_apercu(self):
        s = apercu.depuis_entree(self.entree)
        r = s.resume()
        self.assertEqual(r["pieces"], len(self.entree["composants"]))
        self.assertEqual(r["verrouillees"], sum(1 for c in self.entree["composants"] if c["verrouille"]))
        self.assertGreater(r["cuivre"], 0)
        self.assertLess(s.boite[0], s.boite[2])
        self.assertIsNone(apercu.depuis_resultat(os.path.join(ICI, "pas_de_calcul")))


if __name__ == "__main__":
    unittest.main()
