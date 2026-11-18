"""altium_fichier : lecture / écriture directe des fichiers Altium Designer (.PcbDoc, .SchDoc, .PrjPcb).
API (jalon B1) : voir ../API.md."""
from . import chemins, pose
from .pcbdoc import ACCESSEURS, ErreurPcbDoc, PcbDoc
from .cfb import lire_cfb, ecrire_cfb

__all__ = ["PcbDoc", "ErreurPcbDoc", "ACCESSEURS", "lire_cfb", "ecrire_cfb", "pose", "chemins"]
