"""Point d'entrée unique du placeur-routeur, en mode source comme dans le paquet figé (§5.5, lot D).

- Mode figé (PyInstaller) : le mode vient du nom de l'exécutable : `PlaceurRouteur.exe` ouvre la fenêtre,
  `placeur.exe` est la ligne de commande (`placeur.py`, §5.3) ; les `.ini` implicites ne sont lus qu'ici
  (parametres.charger, d'après `chemins.est_fige()`).
- Mode source : `python lanceur.py [options de placeur.py]` ; `python lanceur.py --fenetre [--essai-fenetre MS]`.
- `--outil NOM [arguments]` : aiguillage vers `NOM.main(arguments)` d'un outil de la liste blanche
  (`chemins.OUTILS_AIGUILLES`, §5.2) ; un nom hors liste rend le code 2.
- `multiprocessing.freeze_support()` est appelé en premier ; hors console, stdout et stderr sont en UTF-8.
- Glisser-déposer d'une carte sur `placeur.exe` : à la fin, attente d'Entrée si le programme est seul dans sa
  console (`GetConsoleProcessList`), pour que la fenêtre de console ne se ferme pas avant d'être lue.

Aucune variable d'environnement n'est lue (§0.2-2).
"""
from __future__ import annotations

import importlib
import multiprocessing
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import chemins  # noqa: E402

# lot qui livre chaque outil de la liste blanche (§8.8)
LOTS_OUTILS = {
    "importer_carte": "B", "deduire_config": "B", "construire_donnees": "B", "extraire_reference": "B",
    "verifier_placement": "C", "drc_maison": "C", "vias_couture": "C", "mesures": "C", "proprete": "C",
    "mesure_convertisseurs": "C", "visualiser": "C", "generer_pcbdoc": "C", "generer_kicad": "C",
    "drc_serigraphie": "C", "formats": "E",
}
MESSAGE_ENTREE = "Appuyer sur Entrée pour fermer cette fenêtre."


def configurer_flux() -> None:
    """stdout et stderr en UTF-8 hors console (redirigés, tubes) ; sans console du tout (exe fenêtre) : sorties
    vides plutôt que None."""
    for nom in ("stdout", "stderr"):
        f = getattr(sys, nom)
        if f is None:
            setattr(sys, nom, open(os.devnull, "w", encoding="utf-8"))
            continue
        try:
            console = f.isatty()
        except (AttributeError, ValueError, OSError):
            console = False
        if not console:
            try:
                f.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def ajouter_chemins_source() -> None:
    """Mode source : les outils de la sérigraphie (drc_serigraphie) sont dans PCB_V2/outils/serigraphie."""
    if chemins.est_fige():
        return
    seri = os.path.join(chemins.OUTILS, "serigraphie")
    if os.path.isdir(seri) and seri not in sys.path:
        sys.path.append(seri)


def choisir_mode(argv: list[str]) -> str:
    """« outil », « fenetre » ou « console »."""
    if argv[:1] == ["--outil"]:
        return "outil"
    if chemins.est_fige():
        exe = os.path.basename(sys.executable).lower()
        return "fenetre" if exe == chemins.NOM_EXE_FENETRE.lower() else "console"
    return "fenetre" if "--fenetre" in argv else "console"


def _code(valeur) -> int:
    if valeur is None:
        return 0
    if isinstance(valeur, bool):
        return int(valeur)
    if isinstance(valeur, int):
        return valeur
    sys.stderr.write(f"{valeur}\n")
    return 1


def aiguiller(nom: str, arguments: list[str]) -> int:
    """Appelle `nom.main(arguments)` pour un outil de la liste blanche et rend son code."""
    if nom not in chemins.OUTILS_AIGUILLES:
        sys.stderr.write(f"erreur : outil inconnu : {nom} (outils : {', '.join(chemins.OUTILS_AIGUILLES)})\n")
        return 2
    ajouter_chemins_source()
    try:
        module = importlib.import_module(nom)
    except ModuleNotFoundError as e:
        if e.name != nom:
            raise
        sys.stderr.write(f"erreur : outil {nom} absent (livré par le lot {LOTS_OUTILS[nom]})\n")
        return 2
    fonction = getattr(module, "main", None)
    if not callable(fonction):
        sys.stderr.write(f"erreur : l'outil {nom} n'a pas encore de point d'entrée main(argv) "
                         f"(lot {LOTS_OUTILS[nom]})\n")
        return 2
    try:
        return _code(fonction(list(arguments)))
    except SystemExit as e:
        return _code(e.code)


def processus_de_la_console() -> int:
    """Nombre de processus rattachés à la console (Windows) ; 0 sans console ou hors Windows."""
    if not chemins.WINDOWS:
        return 0
    import ctypes
    tableau = (ctypes.c_uint32 * 8)()
    try:
        return int(ctypes.windll.kernel32.GetConsoleProcessList(tableau, 8))
    except (AttributeError, OSError):
        return 0


def entree_console() -> bool:
    """Vrai si l'entrée standard est un clavier (console) : jamais d'attente quand elle est vide ou redirigée
    (fenêtre, CI, sous-processus)."""
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return False
        if not chemins.WINDOWS:
            return True
        import ctypes
        import msvcrt
        mode = ctypes.c_uint32()               # isatty() est vrai pour NUL sous Windows : GetConsoleMode tranche
        poignee = msvcrt.get_osfhandle(sys.stdin.fileno())
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(poignee), ctypes.byref(mode)))
    except (AttributeError, ValueError, OSError):
        return False


def attendre_entree() -> bool:
    """Attend Entrée si le programme est seul dans sa console (glisser-déposer, double-clic) et que l'entrée
    est le clavier. Rend vrai si attendu."""
    if processus_de_la_console() != 1 or not entree_console():
        return False
    try:
        sys.stdout.write(MESSAGE_ENTREE + "\n")
        sys.stdout.flush()
        input()
    except (EOFError, OSError, ValueError, RuntimeError):
        pass
    return True


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()      # en premier (paquet figé, §5.5)
    configurer_flux()
    argv = sys.argv[1:] if argv is None else list(argv)
    mode = choisir_mode(argv)
    if mode == "outil":
        if len(argv) < 2:
            sys.stderr.write("usage : --outil NOM [arguments]\n")
            return 2
        return aiguiller(argv[1], argv[2:])
    if mode == "fenetre":
        import interface
        return interface.main([a for a in argv if a != "--fenetre"])
    import placeur
    try:
        code = placeur.main(argv)
    finally:
        attendre_entree()
    return code


if __name__ == "__main__":
    sys.exit(main())
