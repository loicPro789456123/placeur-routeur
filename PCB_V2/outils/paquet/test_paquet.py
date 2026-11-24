# -*- coding: utf-8 -*-
"""Tests du paquet portable (contrat §5.5 ; lot D, É7, critères 1 à 4).

    cd PCB_V2/outils/paquet && python -m unittest test_paquet -v

- documents générés (generer_documents.py) ;
- verifier_paquet.py (a) à (e) sur un paquet simulé : DLL et .pyd du Python local, moteur construit en
  +crt-static avec --remap-path-prefix (cargo build --release --offline, dossier temporaire), moteur dynamique
  d'une petite crate sans dépendance ;
- analyse statique du fichier spec, de paquet_windows.yml, de construire.py, de essais_fumee.ps1 (et son mode
  -Simuler sous PowerShell 5.1 quand il existe), de NOTES_VERSION.md et de exigences-paquet.txt.
Aucun accès réseau, rien n'est installé ; PyInstaller n'est pas nécessaire.
"""
from __future__ import annotations

import ast
import hashlib
import io
import marshal
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

ICI = os.path.dirname(os.path.abspath(__file__))
OUTILS = os.path.dirname(ICI)
DEPOT = os.path.dirname(os.path.dirname(OUTILS))
SOURCES = os.path.join(OUTILS, "placeur_routage")
for _c in (ICI, SOURCES):
    if _c not in sys.path:
        sys.path.insert(0, _c)

import chemins  # noqa: E402
import construire  # noqa: E402
import generer_documents  # noqa: E402
import parametres  # noqa: E402
import verifier_paquet as vp  # noqa: E402

REGISTRE = os.path.join(os.path.expanduser("~"), ".cargo", "registry", "src")
CARGO = shutil.which("cargo")
PIEGE = "C:" + "\\Users\\" + "quelquun" + "\\Documents\\carte"   # chemin piégé, assemblé pour ne pas se signaler


def lire(chemin: str, mode: str = "r") -> str | bytes:
    if mode == "rb":
        with open(chemin, "rb") as f:
            return f.read()
    with open(chemin, encoding="utf-8-sig") as f:
        return f.read()


def sha(donnees: bytes) -> str:
    return hashlib.sha256(donnees).hexdigest()


def fabriquer_pyz(entrees: dict[str, bytes]) -> bytes:
    """Archive PYZ au format de PyInstaller 6 : PYZ\\0, magie Python, décalage de la table, entrées zlib, table
    marshal (nom, (type, décalage, longueur))."""
    corps, table, pos = b"", [], 12
    for nom, contenu in entrees.items():
        c = zlib.compress(contenu)
        table.append((nom, (0, pos, len(c))))
        corps += c
        pos += len(c)
    return b"PYZ\x00" + b"\x00" * 4 + struct.pack("!i", pos) + corps + marshal.dumps(table)


def fabriquer_carchive(entrees: dict[str, bytes]) -> bytes:
    """CArchive (PKG) de PyInstaller 6, à ajouter à un exécutable."""
    corps, toc = b"", b""
    for nom, contenu in entrees.items():
        c = zlib.compress(contenu)
        n = nom.encode() + b"\x00"
        n += b"\x00" * ((-(18 + len(n))) % 16)
        toc += struct.pack("!IIIIBc", 18 + len(n), len(corps), len(c), len(contenu), 1, b"z") + n
        corps += c
    taille = len(corps) + len(toc) + 88
    return corps + toc + struct.pack("!8sIIii64s", vp.MAGIE_CARCHIVE, taille, len(corps), len(toc), 312,
                                     b"python312.dll")


def construire_crate(dossier: str, nom: str, statique: bool, manifeste: str | None = None) -> str | None:
    """Construit un moteur (cargo build --release --offline, 4 tâches au plus) ; rend l'exécutable ou None."""
    env = chemins.environnement_sous_processus(complet=True)
    for v in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS"):
        env.pop(v, None)
    if statique:
        env["CARGO_ENCODED_RUSTFLAGS"] = construire.drapeaux_rust(os.path.join(os.path.expanduser("~"), ".cargo"),
                                                                 DEPOT)
    if manifeste is None:
        source = os.path.join(dossier, "source_" + nom)
        os.makedirs(os.path.join(source, "src"), exist_ok=True)
        with open(os.path.join(source, "Cargo.toml"), "w", encoding="utf-8") as f:
            f.write(f'[package]\nname = "{nom}"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n')
        with open(os.path.join(source, "src", "main.rs"), "w", encoding="utf-8") as f:
            f.write('fn main() { println!("essai"); }\n')
        manifeste = os.path.join(source, "Cargo.toml")
    cible = os.path.join(dossier, "cible_" + nom + ("_s" if statique else "_d"))
    r = subprocess.run([CARGO, "build", "--release", "--offline", "-j", "4", "--manifest-path", manifeste,
                        "--target-dir", cible], env=env, capture_output=True, text=True, errors="replace")
    exe = os.path.join(cible, "release", nom + ".exe")
    return exe if r.returncode == 0 and os.path.isfile(exe) else None


# ------------------------------------------------------------------------------------------------ documents

class TestDocuments(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="essai_documents_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    @unittest.skipUnless(os.path.isdir(REGISTRE), "registre cargo local absent")
    def test_documents_generes(self):
        flux = io.StringIO()
        self.assertEqual(generer_documents.generer(self.tmp, REGISTRE, sys.base_prefix, False, flux), 0,
                         flux.getvalue())
        h = lire(os.path.join(self.tmp, "MODE_EMPLOI.html"))
        self.assertIn("<table>", h)
        self.assertIn("<h2", h)
        self.assertIn("<td>", h)
        ini = lire(os.path.join(self.tmp, "parametres_modele.ini"), "rb")
        self.assertTrue(ini.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(ini[3:].decode("utf-8"), parametres.MODELE_INI.replace("\n", "\r\n"))
        lire_avant = lire(os.path.join(self.tmp, "0_LIRE_AVANT.txt"), "rb")
        self.assertTrue(lire_avant.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(lire_avant.count(b"\r\n"), 5)
        self.assertEqual(lire_avant.count(b"\n"), 5)
        self.assertIn("Débloquer", lire_avant.decode("utf-8-sig"))
        self.assertIn("Extraire tout", lire_avant.decode("utf-8-sig"))
        self.assertIn("PlaceurRouteur.exe", lire_avant.decode("utf-8-sig"))
        licences = lire(os.path.join(self.tmp, "LICENCES.txt"))
        crates = generer_documents.crates_des_locks()
        self.assertGreater(len(crates), 5)
        for nom, ver in crates:
            self.assertIn(f"--- crate {nom} {ver} :", licences, f"avis absent : {nom} {ver}")
        for attendu in ("Python", "Tk 8.6", "olefile"):
            self.assertIn(attendu, licences)

    def test_avis_manquant_code_non_nul(self):
        flux = io.StringIO()
        code = generer_documents.generer(self.tmp, os.path.join(self.tmp, "registre_vide"), sys.base_prefix, False,
                                         flux)
        self.assertEqual(code, 1)
        self.assertIn("avis manquant : crate", flux.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "LICENCES.txt")))

    def test_pyinstaller_exige(self):
        with mock.patch.object(generer_documents, "avis_distribution",
                               side_effect=lambda n: [] if n == "pyinstaller" else [("L", "texte")]):
            _t, manques = generer_documents.construire_licences(REGISTRE, sys.base_prefix, True, "2.0.0")
        self.assertTrue(any("pyinstaller" in m for m in manques))

    def test_notes_de_version_portent_les_5_lignes(self):
        notes = lire(os.path.join(ICI, "NOTES_VERSION.md"))
        for ligne in generer_documents.lignes_lire_avant(chemins.lire_version()):
            self.assertIn(ligne, notes)
        self.assertIn("COMPTE RENDU :", notes)

    def test_markdown_vers_html(self):
        h = generer_documents.markdown_vers_html("# T\n\n| a | b |\n|---|---|\n| `x<y` | **g** |\n\n- un\n- deux\n",
                                                 "t")
        self.assertIn("<h1", h)
        self.assertIn("<code>x&lt;y</code>", h)
        self.assertIn("<strong>g</strong>", h)
        self.assertIn("<ul><li>un</li><li>deux</li></ul>", h)


# ------------------------------------------------------------------------------------------------ paquet simulé

@unittest.skipUnless(os.name == "nt", "paquet Windows")
class TestVerifierPaquet(unittest.TestCase):
    """verifier_paquet (a) à (e) sur un paquet simulé."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="essai_paquet_")
        cls.statique = cls.dynamique = None
        if CARGO:
            cls.statique = construire_crate(cls.tmp, "serigraphie", True,
                                            os.path.join(OUTILS, "serigraphie", "Cargo.toml"))
            cls.dynamique = construire_crate(cls.tmp, "moteur_dynamique", False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        self.p = tempfile.mkdtemp(prefix="paquet_", dir=self.tmp)
        b = sys.base_prefix
        interne = os.path.join(self.p, "_internal")
        os.makedirs(os.path.join(interne, "moteurs"))
        shutil.copy2(sys.executable if os.path.basename(sys.executable).lower() == "python.exe"
                     else os.path.join(b, "python.exe"), os.path.join(self.p, vp.EXE_FENETRE))
        shutil.copy2(os.path.join(self.p, vp.EXE_FENETRE), os.path.join(self.p, vp.EXE_CONSOLE))
        for n in os.listdir(b):
            if n.lower().endswith(".dll"):
                shutil.copy2(os.path.join(b, n), interne)
        for n in os.listdir(os.path.join(b, "DLLs")):
            if n.lower().endswith((".dll", ".pyd")):
                shutil.copy2(os.path.join(b, "DLLs", n), interne)
        for n, t in (("VERSION", "2.0.0\n"), ("COMMIT", "abcdef1\n")):
            with open(os.path.join(interne, n), "w", encoding="utf-8") as f:
                f.write(t)
        for n in ("0_LIRE_AVANT.txt", "MODE_EMPLOI.html", "parametres_modele.ini", "LICENCES.txt"):
            with open(os.path.join(self.p, n), "w", encoding="utf-8") as f:
                f.write("contenu\n")
        if self.statique:
            for m in vp.MOTEURS:
                shutil.copy2(self.statique, os.path.join(interne, *m.split("/")))
        self.ref = vp.empreintes_reference(sys.base_prefix)

    def ecrire(self, rel: str, donnees: bytes) -> str:
        c = os.path.join(self.p, *rel.split("/"))
        os.makedirs(os.path.dirname(c), exist_ok=True)
        with open(c, "wb") as f:
            f.write(donnees)
        return c

    # (a)
    def test_a_fermeture_des_dll(self):
        self.assertEqual(vp.verifier_dll(self.p), [])
        os.remove(os.path.join(self.p, "_internal", "libffi-8.dll"))
        erreurs = vp.verifier_dll(self.p)
        self.assertTrue(any("_ctypes.pyd importe libffi-8.dll" in e for e in erreurs), erreurs)

    # (b)
    def test_b_moteur_statique_accepte_dynamique_refuse(self):
        if not (self.statique and self.dynamique):
            self.skipTest("cargo absent ou construction hors ligne impossible")
        self.assertEqual(vp.verifier_moteurs(self.p), [])
        self.assertEqual(vp.verifier_anonymat(os.path.join(self.p, "_internal", "moteurs"), set())[0], [],
                         "moteur statique construit avec --remap-path-prefix : aucun chemin de la machine")
        shutil.copy2(self.dynamique, os.path.join(self.p, "_internal", "moteurs", "serigraphie.exe"))
        erreurs = vp.verifier_moteurs(self.p)
        self.assertTrue(any("serigraphie.exe importe VCRUNTIME" in e.upper().replace("IMPORTE", "importe")
                            or "vcruntime" in e.lower() for e in erreurs), erreurs)

    # (c)
    def test_c_anonymat_et_exemptions(self):
        sans_ref, _n = vp.verifier_anonymat(self.p, set())
        self.assertTrue(sans_ref, "les binaires officiels de Python portent le chemin de leur construction")
        erreurs, n = vp.verifier_anonymat(self.p, self.ref)
        self.assertEqual(erreurs, [])
        self.assertGreater(n, 10)
        piege = (PIEGE + "\n").encode()
        self.ecrire("_internal/piege.txt", piege)
        erreurs, _ = vp.verifier_anonymat(self.p, self.ref)
        self.assertTrue(any("piege.txt" in e for e in erreurs), erreurs)
        self.assertFalse(any("quelquun" in e for e in erreurs), "l'extrait trouvé n'est jamais écrit")
        self.assertEqual(vp.verifier_anonymat(self.p, self.ref | {sha(piege)})[0], [], "exemption par empreinte")

    def test_c_archive_pyz_et_carchive(self):
        propre = fabriquer_pyz({"module_propre": b"rien a signaler"})
        self.ecrire("_internal/PYZ-00.pyz", propre)
        self.assertEqual(vp.verifier_anonymat(self.p, self.ref)[0], [])
        self.assertEqual(len(vp.contenus_pyz(propre)), 1)
        self.ecrire("_internal/PYZ-00.pyz", fabriquer_pyz({"module_propre": b"x", "module_piege": PIEGE.encode()}))
        erreurs = vp.verifier_anonymat(self.p, self.ref)[0]
        self.assertTrue(any("PYZ-00.pyz!module_piege" in e for e in erreurs), erreurs)
        os.remove(os.path.join(self.p, "_internal", "PYZ-00.pyz"))
        exe = lire(os.path.join(self.p, vp.EXE_CONSOLE), "rb")
        pyz = fabriquer_pyz({"dans_l_exe": PIEGE.encode()})
        self.ecrire(vp.EXE_CONSOLE, exe + fabriquer_carchive({"PYZ-00.pyz": pyz}))
        erreurs = vp.verifier_anonymat(self.p, self.ref)[0]
        self.assertTrue(any("placeur.exe!PYZ-00.pyz!dans_l_exe" in e for e in erreurs), erreurs)

    # (d)
    def test_d_liste_blanche(self):
        if not self.statique:
            self.skipTest("moteur non construit")
        self.assertEqual(vp.verifier_liste_blanche(self.p), [])
        self.ecrire("exemples/kicad/carte.kicad_pcb", b"(kicad_pcb)")
        self.assertEqual(vp.verifier_liste_blanche(self.p), [])
        self.ecrire("en_trop.txt", b"x")
        self.ecrire("_internal/carte.PcbDoc", b"x")
        self.ecrire("_internal/inconnu.txt", b"x")
        self.ecrire("_internal/donnees_placeur.json", b"{}")
        erreurs = vp.verifier_liste_blanche(self.p)
        self.assertTrue(any("inattendu au premier niveau : en_trop.txt" in e for e in erreurs), erreurs)
        self.assertTrue(any("donnée de carte hors exemples/ : _internal/carte.PcbDoc" in e for e in erreurs))
        self.assertTrue(any("donnée de carte hors exemples/ : _internal/donnees_placeur.json" in e for e in erreurs))
        self.assertTrue(any("non autorisé dans _internal : _internal/inconnu.txt" in e for e in erreurs))
        os.remove(os.path.join(self.p, "LICENCES.txt"))
        self.assertTrue(any("absent du premier niveau : LICENCES.txt" in e for e in vp.verifier_liste_blanche(self.p)))

    # (e)
    def test_e_ressources_de_version_lues_sur_python_exe(self):
        chaines = vp.chaines_version(lire(os.path.join(sys.base_prefix, "python.exe"), "rb"))
        self.assertEqual(chaines.get("ProductName"), "Python")
        self.assertEqual(chaines.get("CompanyName"), "Python Software Foundation")
        self.assertEqual(chaines.get("FileVersion"), "%d.%d.%d" % sys.version_info[:3])
        erreurs = vp.verifier_version(self.p)
        self.assertTrue(any("ProductName = 'Python', attendu 'PlaceurRouteur'" in e for e in erreurs), erreurs)
        self.assertTrue(any("ProductVersion" in e for e in erreurs))

    def test_ligne_de_commande(self):
        flux = io.StringIO()
        with mock.patch("sys.stdout", flux):
            self.assertEqual(vp.main([self.p, "--seulement", "a"]), 0)
            self.assertEqual(vp.main([self.p]), 1)
            self.assertEqual(vp.main([os.path.join(self.p, "absent")]), 2)
        self.assertIn("paquet NON conforme", flux.getvalue())


class TestLecteurPE(unittest.TestCase):

    def test_pe_invalide(self):
        with self.assertRaises(vp.ErreurPE):
            vp.PE(b"pas un exe" * 10)

    @unittest.skipUnless(os.name == "nt", "binaires Windows")
    def test_imports_de_python312(self):
        noms = [n.lower() for n in vp.PE(lire(os.path.join(sys.base_prefix, "python312.dll"), "rb")).imports()]
        self.assertIn("kernel32.dll", noms)
        self.assertIn("vcruntime140.dll", noms)


# ------------------------------------------------------------------------------------------------ analyse statique

class TestStatique(unittest.TestCase):

    def test_spec(self):
        texte = lire(os.path.join(ICI, "PlaceurRouteur.spec"))
        ast.parse(texte)
        self.assertEqual(len(re.findall(r"^a = Analysis\(", texte, re.M)), 1)
        self.assertEqual(texte.count("EXE("), 2)
        self.assertEqual(texte.count("COLLECT("), 1)
        self.assertIn('os.path.join(SOURCES, "lanceur.py")', texte)
        self.assertIn('name="PlaceurRouteur",\n    console=False', texte)
        self.assertIn('name="placeur",\n    console=True', texte)
        self.assertIn('"moteurs")', texte)
        self.assertIn('excludes=["numpy", "matplotlib", "scipy", "PIL"]', texte)
        self.assertIn("optimize=0", texte)
        self.assertEqual(texte.count("upx=False,\n"), 3)
        self.assertEqual(texte.count("uac_admin=False,\n"), 2)
        self.assertEqual(texte.count("exclude_binaries=True"), 2)
        for cle in ('StringStruct("ProductName", "PlaceurRouteur")', 'StringStruct("CompanyName", "")',
                    'StringStruct("LegalCopyright", "voir LICENCES.txt")', 'StringStruct("ProductVersion", VERSION)'):
            self.assertIn(cle, texte)
        self.assertIn('"VERSION"', texte)
        self.assertNotIn("onefile", texte.lower().replace("pas onefile", ""))

    def test_paquet_windows_yml(self):
        y = lire(os.path.join(DEPOT, ".github", "workflows", "paquet_windows.yml"))
        self.assertRegex(y, r"image: \$\{\{ fromJSON\(vars\.[A-Z_]+ \|\| '\[\"windows-2022\", \"windows-2025\"\]'\) \}\}")
        self.assertIn('RUST_VERSION: "1.99.0"', y)
        self.assertIn("rustup toolchain install $env:RUST_VERSION", y)
        for morceau in ('"target-feature=+crt-static"', '"--remap-path-prefix=$cargoHome=/cargo"',
                        '"--remap-path-prefix=$env:GITHUB_WORKSPACE=/src"', '"link-arg=/Brepro"',
                        "CARGO_ENCODED_RUSTFLAGS=$drapeaux", "[char]0x1f"):
            self.assertIn(morceau, y)
        self.assertIn("cargo build --release --locked", y)
        self.assertIn("cargo test --release --locked", y)
        self.assertIn('PYTHON_VERSION: "3.12.10"', y)
        self.assertIn("architecture: x64", y)
        self.assertIn("pip install --require-hashes --no-binary pyinstaller", y)
        self.assertIn('PYINSTALLER_COMPILE_BOOTLOADER: "1"', y)
        self.assertIn("SOURCE_DATE_EPOCH=", y)
        self.assertIn("--date-source $env:SOURCE_DATE_EPOCH", y)
        self.assertIn('PYTHONHASHSEED: "0"', y)
        self.assertIn("python -m unittest test_paquet", y)
        self.assertIn("python -m unittest test_parametres test_placeur test_lanceur test_anonymat", y)
        self.assertIn("verifier_paquet.py", y)
        self.assertIn("essais_fumee.ps1", y)
        self.assertIn("MpCmdRun", y)
        self.assertIn("construire.py", y)
        self.assertIn("PlaceurRouteur-*.zip.sha256", y)
        # job croisé, PATH réduit
        self.assertRegex(y, r"construit: windows-2022\n\s+essaye: windows-2025")
        self.assertRegex(y, r"construit: windows-2025\n\s+essaye: windows-2022")
        self.assertIn("$env:PATH = 'C:\\Windows\\system32;C:\\Windows'", y)
        # version et étiquette
        self.assertIn('construire.py" --controler-version\n', y)
        self.assertIn('--controler-version --etiquette "${GITHUB_REF_NAME}"', y)
        self.assertIn('tags: ["v*"]', y)
        # verrouillage des empreintes
        self.assertIn("verrouiller:", y)
        self.assertIn("--generate-hashes", y)
        # publication présente mais désactivée
        self.assertIn("  publier:", y)
        self.assertIn("if: false && startsWith(github.ref, 'refs/tags/v')", y)
        self.assertIn('"${GITHUB_ACTOR}" != "${GITHUB_REPOSITORY_OWNER}"', y)
        self.assertIn("COMPTE RENDU : fait le", y)
        self.assertIn('--notes-file "$PAQUET/NOTES_VERSION.md"', y)
        self.assertIn("name: paquet-windows-2022", y)
        self.assertIn("secrets.GITHUB_TOKEN", y)

    def test_construire_zip_et_version(self):
        self.assertEqual(construire.TAILLE_MAX_ZIP, 40 * 1024 * 1024)
        d = construire.drapeaux_rust("H", "W").split("\x1f")
        self.assertEqual(d, ["-C", "target-feature=+crt-static", "--remap-path-prefix=H=/cargo",
                             "--remap-path-prefix=W=/src", "-C", "link-arg=/Brepro"])
        v = chemins.lire_version()
        with mock.patch.object(construire, "version_cargo", return_value=v):
            self.assertEqual(construire.controler_version(None, io.StringIO()), 0)
            self.assertEqual(construire.controler_version("refs/tags/v" + v, io.StringIO()), 0)
            self.assertEqual(construire.controler_version("v9.9.9", io.StringIO()), 1)
        with mock.patch.object(construire, "version_cargo", return_value="0.0.1"):
            self.assertEqual(construire.controler_version(None, io.StringIO()), 1)

    def test_zip_deterministe(self):
        tmp = tempfile.mkdtemp(prefix="essai_zip_")
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "PlaceurRouteur")
        os.makedirs(os.path.join(src, "_internal"))
        for n in ("b.txt", "a.txt", "_internal/c.dll"):
            with open(os.path.join(src, n), "w") as f:
                f.write(n)
        empreintes = set()
        for i in range(2):
            z = os.path.join(tmp, f"{i}.zip")
            construire.zip_deterministe(src, z, 1700000000)
            empreintes.add(construire.sha256(z))
        self.assertEqual(len(empreintes), 1)

    def test_exigences_epinglees(self):
        lignes = [l.split("#")[0].strip() for l in lire(os.path.join(ICI, "exigences-paquet.txt")).splitlines()]
        # format de pip --require-hashes : « nom==version \ » suivi de lignes « --hash=sha256:… »
        lignes = [l.rstrip("\\").strip() for l in lignes if l and not l.startswith("--hash=sha256:")]
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9.]*", l) for l in lignes), lignes)
        self.assertIn("--hash=sha256:", lire(os.path.join(ICI, "exigences-paquet.txt")))
        self.assertIn("olefile==0.47", lignes)
        self.assertTrue(any(l.startswith("pyinstaller==") for l in lignes))

    def test_essais_fumee_ps1(self):
        brut = lire(os.path.join(ICI, "essais_fumee.ps1"), "rb")
        self.assertTrue(brut.startswith(b"\xef\xbb\xbf"), "UTF-8 avec BOM (PowerShell 5.1)")
        texte = brut[3:].decode("utf-8")
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", texte), "lettre de lecteur en dur")
        self.assertIn("$env:SystemDrive", texte)
        for essai in ("version", "auto_test", "chaine_kicad", "chaine_pcbdoc", "donnees_metroid", "serigraphie",
                      "arret_lancements", "arret_demande", "stop_process", "reprise", "chemin_accentue",
                      "chemin_non_latin", "chemin_unc", "sortie_redirigee", "fenetre", "fenetre_carte"):
            self.assertIn(f'Nom = "{essai}"', texte)
        ps = shutil.which("powershell")
        if not ps:
            self.skipTest("PowerShell 5.1 absent")
        r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            os.path.join(ICI, "essais_fumee.ps1"), "-Simuler"], capture_output=True, timeout=120)
        sortie = r.stdout.decode("utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, sortie + r.stderr.decode("utf-8", errors="replace"))
        self.assertIn("16 essais.", sortie)
        self.assertEqual(r.stderr.strip(), b"")


if __name__ == "__main__":
    unittest.main()
