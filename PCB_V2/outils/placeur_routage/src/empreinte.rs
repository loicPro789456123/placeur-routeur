//! Empreinte SHA-256 (FIPS 180-4), écrite en Rust sans dépendance (contrat §0.2-3).
//!
//! Sert aux empreintes des entrées (`stats.empreintes`, décision D4 du lot A) et aux comparaisons de reproductibilité.

const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
    0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
    0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

const H0: [u32; 8] = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

/// Calcul incrémental : `ajouter` autant de fois que voulu, puis `finir`.
pub struct Sha256 {
    etat: [u32; 8],
    bloc: [u8; 64],
    rempli: usize,
    longueur: u64,
}

impl Default for Sha256 {
    fn default() -> Self {
        Sha256::new()
    }
}

impl Sha256 {
    pub fn new() -> Sha256 {
        Sha256 { etat: H0, bloc: [0; 64], rempli: 0, longueur: 0 }
    }

    pub fn ajouter(&mut self, mut donnees: &[u8]) {
        self.longueur = self.longueur.wrapping_add(donnees.len() as u64);
        if self.rempli > 0 {
            let n = (64 - self.rempli).min(donnees.len());
            self.bloc[self.rempli..self.rempli + n].copy_from_slice(&donnees[..n]);
            self.rempli += n;
            donnees = &donnees[n..];
            if self.rempli < 64 {
                return;
            }
            let b = self.bloc;
            compresser(&mut self.etat, &b);
            self.rempli = 0;
        }
        while donnees.len() >= 64 {
            let mut b = [0u8; 64];
            b.copy_from_slice(&donnees[..64]);
            compresser(&mut self.etat, &b);
            donnees = &donnees[64..];
        }
        self.bloc[..donnees.len()].copy_from_slice(donnees);
        self.rempli = donnees.len();
    }

    pub fn finir(mut self) -> [u8; 32] {
        let bits = self.longueur.wrapping_mul(8);
        // bourrage : un bit 1, des zéros jusqu'à 56 octets modulo 64, puis la longueur en bits (64 bits, gros-boutiste)
        let mut fin = vec![0x80u8];
        let reste = (self.rempli + 1) % 64;
        let zeros = if reste <= 56 { 56 - reste } else { 120 - reste };
        fin.extend(std::iter::repeat_n(0u8, zeros));
        fin.extend_from_slice(&bits.to_be_bytes());
        let longueur = self.longueur;
        self.ajouter(&fin);
        debug_assert_eq!(self.rempli, 0);
        self.longueur = longueur;
        let mut sortie = [0u8; 32];
        for (i, mot) in self.etat.iter().enumerate() {
            sortie[4 * i..4 * i + 4].copy_from_slice(&mot.to_be_bytes());
        }
        sortie
    }
}

fn compresser(etat: &mut [u32; 8], bloc: &[u8; 64]) {
    let mut w = [0u32; 64];
    for t in 0..16 {
        w[t] = u32::from_be_bytes([bloc[4 * t], bloc[4 * t + 1], bloc[4 * t + 2], bloc[4 * t + 3]]);
    }
    for t in 16..64 {
        let s0 = w[t - 15].rotate_right(7) ^ w[t - 15].rotate_right(18) ^ (w[t - 15] >> 3);
        let s1 = w[t - 2].rotate_right(17) ^ w[t - 2].rotate_right(19) ^ (w[t - 2] >> 10);
        w[t] = w[t - 16].wrapping_add(s0).wrapping_add(w[t - 7]).wrapping_add(s1);
    }
    let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = *etat;
    for t in 0..64 {
        let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
        let ch = (e & f) ^ (!e & g);
        let t1 = h.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[t]).wrapping_add(w[t]);
        let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
        let maj = (a & b) ^ (a & c) ^ (b & c);
        let t2 = s0.wrapping_add(maj);
        h = g;
        g = f;
        f = e;
        e = d.wrapping_add(t1);
        d = c;
        c = b;
        b = a;
        a = t1.wrapping_add(t2);
    }
    for (x, v) in etat.iter_mut().zip([a, b, c, d, e, f, g, h]) {
        *x = x.wrapping_add(v);
    }
}

/// SHA-256 d'un tableau d'octets.
pub fn sha256(donnees: &[u8]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.ajouter(donnees);
    h.finir()
}

/// Écriture hexadécimale en minuscules.
pub fn en_hex(octets: &[u8]) -> String {
    const CHIFFRES: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(2 * octets.len());
    for &o in octets {
        s.push(CHIFFRES[(o >> 4) as usize] as char);
        s.push(CHIFFRES[(o & 15) as usize] as char);
    }
    s
}

/// SHA-256 d'un tableau d'octets, en hexadécimal (64 caractères).
pub fn sha256_hex(donnees: &[u8]) -> String {
    en_hex(&sha256(donnees))
}

#[cfg(test)]
mod tests {
    use super::*;

    // vecteurs officiels (FIPS 180-2, annexe B, et exemples du NIST)
    #[test]
    fn vecteur_texte_vide() {
        assert_eq!(sha256_hex(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    }

    #[test]
    fn vecteur_abc() {
        assert_eq!(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn vecteur_448_bits() {
        let m = b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
        assert_eq!(m.len() * 8, 448);
        assert_eq!(sha256_hex(m), "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    }

    #[test]
    fn vecteur_896_bits() {
        let m = b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu";
        assert_eq!(m.len() * 8, 896);
        assert_eq!(sha256_hex(m), "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1");
    }

    #[test]
    fn vecteur_million_de_a() {
        let m = vec![b'a'; 1_000_000];
        assert_eq!(sha256_hex(&m), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
        // même résultat en morceaux de tailles quelconques (calcul incrémental)
        let mut h = Sha256::new();
        let mut reste: &[u8] = &m;
        let mut taille = 1;
        while !reste.is_empty() {
            let n = taille.min(reste.len());
            h.ajouter(&reste[..n]);
            reste = &reste[n..];
            taille = taille * 7 % 131 + 1;
        }
        assert_eq!(en_hex(&h.finir()), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    }

    #[test]
    fn bourrage_aux_limites_de_bloc() {
        // longueurs autour de 55, 56, 63, 64 octets : le découpage incrémental donne le même résultat que le calcul direct
        for n in [0usize, 1, 54, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 129] {
            let m: Vec<u8> = (0..n).map(|i| (i * 31 + 7) as u8).collect();
            let direct = sha256(&m);
            let mut h = Sha256::new();
            for octet in &m {
                h.ajouter(std::slice::from_ref(octet));
            }
            assert_eq!(h.finir(), direct, "longueur {}", n);
        }
    }
}
