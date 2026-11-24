# -*- coding: utf-8 -*-
"""Timelapse d'un calcul (24/11/2026) : GIF animé de 10 à 20 s fait des images légères écrites par le moteur
(``graine_N/suivi/images/*.json``, ``instantane/1``), sans bibliothèque (encodeur GIF/LZW en Python pur).

    python timelapse.py DOSSIER_GRAINE DONNEES.json SORTIE.gif [--duree 15] [--largeur 480]

Chaque image : contour de la carte, pièces (dessus / dessous), pistes quand le routage est présent, et une barre de
progression du coût. Palette fixe de 8 couleurs ; 10 images par seconde ; au plus ``duree × 10`` images (les images
du moteur sont choisies à intervalles réguliers).
"""
from __future__ import annotations

import glob
import json
import math
import os
import struct
import sys

PALETTE = [(255, 255, 255), (238, 240, 243), (60, 74, 58), (86, 96, 108), (192, 57, 43), (47, 109, 180),
           (180, 190, 198), (28, 35, 43)]
FOND, CARTE, CONTOUR, PIECE_DESSUS, PISTE_DESSUS, PISTE_DESSOUS, PIECE_DESSOUS, TEXTE = range(8)
MIL = 0.0254


# ------------------------------------------------------------------------------------------------ encodeur GIF
def _lzw(indices: bytes, bits_min: int = 3) -> bytes:
    """Compression LZW du GIF (codes de taille variable, remise à zéro à 4096 entrées)."""
    clear, fin = 1 << bits_min, (1 << bits_min) + 1
    sortie, acc, nacc = bytearray(), 0, 0
    taille = bits_min + 1
    table = {bytes([i]): i for i in range(clear)}
    suivant = fin + 1

    def ecrire(code):
        nonlocal acc, nacc
        acc |= code << nacc
        nacc += taille
        while nacc >= 8:
            sortie.append(acc & 0xFF)
            acc >>= 8
            nacc -= 8

    ecrire(clear)
    w = b""
    for b in indices:
        wc = w + bytes([b])
        if wc in table:
            w = wc
            continue
        ecrire(table[w])
        if suivant < 4096:
            table[wc] = suivant
            suivant += 1
            if suivant > (1 << taille) and taille < 12:
                taille += 1
        else:
            ecrire(clear)
            table = {bytes([i]): i for i in range(clear)}
            suivant, taille = fin + 1, bits_min + 1
        w = bytes([b])
    if w:
        ecrire(table[w])
    ecrire(fin)
    if nacc:
        sortie.append(acc & 0xFF)
    blocs = bytearray([bits_min])
    for i in range(0, len(sortie), 255):
        bloc = sortie[i:i + 255]
        blocs += bytes([len(bloc)]) + bloc
    return bytes(blocs + b"\x00")


def ecrire_gif(chemin: str, images: list[bytearray], l: int, h: int, delai_cs: int = 10) -> None:
    pal = b"".join(bytes(c) for c in PALETTE)
    with open(chemin, "wb") as f:
        f.write(b"GIF89a" + struct.pack("<HHBBB", l, h, 0xF2, 0, 0) + pal)
        f.write(b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")          # boucle infinie
        for img in images:
            f.write(b"\x21\xF9\x04\x00" + struct.pack("<H", delai_cs) + b"\x00\x00")
            f.write(b"\x2C" + struct.pack("<HHHHB", 0, 0, l, h, 0))
            f.write(_lzw(bytes(img)))
        f.write(b"\x3B")


# ------------------------------------------------------------------------------------------------ dessin
class Image:
    def __init__(self, l, h, couleur=FOND):
        self.l, self.h, self.px = l, h, bytearray([couleur]) * (l * h)

    def rect(self, x0, y0, x1, y1, c):
        x0, x1 = max(0, int(min(x0, x1))), min(self.l - 1, int(max(x0, x1)))
        y0, y1 = max(0, int(min(y0, y1))), min(self.h - 1, int(max(y0, y1)))
        for y in range(y0, y1 + 1):
            self.px[y * self.l + x0:y * self.l + x1 + 1] = bytes([c]) * (x1 - x0 + 1)

    def ligne(self, x0, y0, x1, y1, c):
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for k in range(n + 1):
            t = k / n
            x, y = int(x0 + t * (x1 - x0)), int(y0 + t * (y1 - y0))
            if 0 <= x < self.l and 0 <= y < self.h:
                self.px[y * self.l + x] = c


def _boites(donnees: dict) -> dict:
    return {c["nom"]: c.get("lbb") or [-0.5, -0.5, 0.5, 0.5] for c in donnees.get("composants") or []
            if not c.get("virtuel")}


def dessiner(inst: dict, donnees: dict, boites: dict, l: int, h: int, cmin: float, cmax: float) -> bytearray:
    bx = (donnees.get("carte") or {}).get("boite") or [0, 0, 100, 100]
    marge = 6
    ech = min((l - 2 * marge) / max(bx[2] - bx[0], 1e-6), (h - 2 * marge - 8) / max(bx[3] - bx[1], 1e-6))

    def X(x):
        return marge + (x - bx[0]) * ech

    def Y(y):
        return h - 8 - marge - (y - bx[1]) * ech

    im = Image(l, h)
    im.rect(X(bx[0]), Y(bx[3]), X(bx[2]), Y(bx[1]), CARTE)
    pts = (donnees.get("carte") or {}).get("contour") or []
    for a, b in zip(pts, pts[1:] + pts[:1]):
        im.ligne(X(a[0]), Y(a[1]), X(b[0]), Y(b[1]), CONTOUR)
    rt = inst.get("routage") or {}
    fr = MIL if rt.get("unite") == "mil" else 1.0
    for p in rt.get("pistes") or []:
        c = PISTE_DESSOUS if p.get("layer", "").lower().startswith("bottom") else PISTE_DESSUS
        im.ligne(X(p["x1"] * fr), Y(p["y1"] * fr), X(p["x2"] * fr), Y(p["y2"] * fr), c)
    pl = inst.get("placements") or {}
    fp = MIL if pl.get("unite") == "mil" else 1.0
    for q in pl.get("pieces") or []:
        b = boites.get(q.get("designator"))
        if not b:
            continue
        x, y, a = q["x"] * fp, q["y"] * fp, math.radians(q.get("rotation", 0.0))
        dessous = q.get("side") == "bottom"
        coins = []
        for u, v in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])):
            u = -u if dessous else u
            coins.append((x + u * math.cos(a) - v * math.sin(a), y + u * math.sin(a) + v * math.cos(a)))
        xs, ys = [X(c[0]) for c in coins], [Y(c[1]) for c in coins]
        im.rect(min(xs), min(ys), max(xs), max(ys), PIECE_DESSOUS if dessous else PIECE_DESSUS)
    # barre du coût (pleine au coût initial, vide au meilleur coût)
    cout = inst.get("cout")
    if isinstance(cout, (int, float)) and cout >= 0 and cmax > cmin:
        f = (cout - cmin) / (cmax - cmin)
        im.rect(marge, h - 5, marge + f * (l - 2 * marge), h - 2, TEXTE)
    return im.px


def fabriquer(dossier_graine: str, chemin_donnees: str, sortie: str, duree_s: float = 15.0, largeur: int = 480) -> int:
    """Écrit le GIF ; rend le nombre d'images (0 : aucune image du moteur, rien n'est écrit)."""
    fichiers = sorted(glob.glob(os.path.join(dossier_graine, "suivi", "images", "*.json")))
    if not fichiers:
        return 0
    n = max(1, min(len(fichiers), int(duree_s * 10)))
    choisis = [fichiers[round(i * (len(fichiers) - 1) / max(1, n - 1))] for i in range(n)]
    with open(chemin_donnees, encoding="utf-8") as f:
        donnees = json.load(f)
    bx = (donnees.get("carte") or {}).get("boite") or [0, 0, 100, 100]
    hauteur = max(60, int(largeur * (bx[3] - bx[1]) / max(bx[2] - bx[0], 1e-6)) + 8)
    insts = []
    for c in choisis:
        try:
            with open(c, encoding="utf-8") as f:
                insts.append(json.load(f))
        except (OSError, ValueError):
            pass
    couts = [i["cout"] for i in insts if isinstance(i.get("cout"), (int, float)) and i["cout"] >= 0
             and i.get("phase") not in ("boucle",)]
    cmin, cmax = (min(couts), max(couts)) if couts else (0.0, 1.0)
    boites = _boites(donnees)
    images = [dessiner(i, donnees, boites, largeur, hauteur, cmin, cmax) for i in insts]
    images += [images[-1]] * 15                       # la carte finale reste affichée 1,5 s
    ecrire_gif(sortie, images, largeur, hauteur)
    return len(insts)


if __name__ == "__main__":
    a = sys.argv[1:]
    duree = float(a[a.index("--duree") + 1]) if "--duree" in a else 15.0
    larg = int(a[a.index("--largeur") + 1]) if "--largeur" in a else 480
    print(fabriquer(a[0], a[1], a[2], duree, larg))
