# Essais de fumée du paquet portable PlaceurRouteur (contrat §5.5 ; lot D, É7). PowerShell 5.1.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File essais_fumee.ps1 -Paquet DOSSIER [-Depot DOSSIER]
#              [-Metroid] [-Simuler]
#
# -Paquet  : dossier PlaceurRouteur extrait (placeur.exe, PlaceurRouteur.exe, _internal\, exemples\) ;
# -Depot   : racine du dépôt (exemple METROID de PCB_V2\outils\placeur_routage\exemples\metroid_v2) ;
# -Metroid : ajoute l'essai --donnees sur l'exemple METROID (sur étiquette ou chaque nuit) ;
# -Simuler : écrit la liste des essais sans rien lancer (code 0).
# Code de sortie : 0 si tous les essais bloquants réussissent, 1 sinon. Aucune lettre de lecteur écrite en dur :
# les chemins viennent de $env:SystemDrive, $env:SystemRoot et du dossier temporaire.
param(
    [string]$Paquet = "",
    [string]$Depot = "",
    [switch]$Metroid,
    [switch]$Simuler
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$Essais = @(
    @{ Nom = "version";            Bloquant = $true;  Texte = "placeur.exe --version : version X.Y.Z+commit, code 0" },
    @{ Nom = "auto_test";          Bloquant = $true;  Texte = "placeur.exe --auto-test : moteurs, outils importables, dossier inscriptible, code 0 ; moteur retiré : « moteur absent, peut-être mis en quarantaine »" },
    @{ Nom = "chaine_kicad";       Bloquant = $true;  Texte = "chaîne complète sur la carte KiCad d'exemple (--qualite rapide --essais 2) : code 0, au moins une graine valide, .kicad_pcb produit et relu" },
    @{ Nom = "chaine_pcbdoc";      Bloquant = $true;  Texte = "chaîne complète sur la carte de démonstration .PcbDoc : code 0, carte relue par format_altium, sérigraphie « coherent » (sauté si la carte n'existe pas encore)" },
    @{ Nom = "mode_placement";     Bloquant = $true;  Texte = "--mode-calcul placement sur la carte de démonstration (--qualite apercu --essais 1) : code 0, carte _PLACEMENT produite (sauté sans carte de démonstration)" },
    @{ Nom = "mode_serigraphie";   Bloquant = $true;  Texte = "--mode-calcul serigraphie sur la carte de démonstration : code 0, carte _SERIGRAPHIE produite (sauté sans carte de démonstration)" },
    @{ Nom = "donnees_metroid";    Bloquant = $true;  Texte = "placeur.exe --donnees sur l'exemple METROID, une graine (seulement avec -Metroid)" },
    @{ Nom = "serigraphie";        Bloquant = $true;  Texte = "sérigraphie synthétique : moteur serigraphie sur la carte de démonstration, contrôle drc_serigraphie (sauté sans carte de démonstration)" },
    @{ Nom = "arret_lancements";   Bloquant = $true;  Texte = "fichier arret_lancements : les graines en cours finissent, code 3" },
    @{ Nom = "arret_demande";      Bloquant = $true;  Texte = "fichier arret_demande : plus aucun moteur 2 s après" },
    @{ Nom = "stop_process";       Bloquant = $true;  Texte = "Stop-Process de placeur.exe : plus aucun placeur_routage 2 s après" },
    @{ Nom = "reprise";            Bloquant = $true;  Texte = "--reprendre après un arrêt : les graines finies sont reprises telles quelles" },
    @{ Nom = "chemin_accentue";    Bloquant = $true;  Texte = "paquet extrait et carte placée sous `$env:SystemDrive\Essai é à\l'atelier (copie)\" },
    @{ Nom = "chemin_non_latin";   Bloquant = $true;  Texte = "paquet et carte sous un dossier au nom non latin" },
    @{ Nom = "chemin_unc";         Bloquant = $true;  Texte = "paquet lancé par un partage UNC (\\localhost\<lecteur>$\...)" },
    @{ Nom = "sortie_redirigee";   Bloquant = $true;  Texte = "sortie redirigée, message accentué lisible en UTF-8, pour chaque outil de la liste blanche (--outil NOM --help)" },
    @{ Nom = "fenetre";            Bloquant = $false; Texte = "PlaceurRouteur.exe --essai-fenetre 8000 : capture, fin sans blocage (non bloquant tant que l'affichage des machines hébergées n'est pas confirmé)" },
    @{ Nom = "fenetre_carte";      Bloquant = $false; Texte = "PlaceurRouteur.exe --carte demonstration.PcbDoc --rapport R.json : règles lues, aperçu (pièces, pièce verrouillée), carte d'origine inchangée (non bloquant, comme fenetre)" }
)

if ($Simuler) {
    Write-Output "Essais de fumée (§5.5), mode simulé : rien n'est lancé."
    $i = 0
    foreach ($e in $Essais) {
        $i++
        $b = if ($e.Bloquant) { "bloquant" } else { "non bloquant" }
        Write-Output ("{0,2}. {1} [{2}] : {3}" -f $i, $e.Nom, $b, $e.Texte)
    }
    Write-Output ("{0} essais." -f $Essais.Count)
    exit 0
}

if (-not $Paquet -or -not (Test-Path -LiteralPath (Join-Path $Paquet "placeur.exe"))) {
    Write-Output "erreur : -Paquet DOSSIER doit désigner un paquet extrait (placeur.exe absent)"
    exit 2
}
$Paquet = (Resolve-Path -LiteralPath $Paquet).Path
$Placeur = Join-Path $Paquet "placeur.exe"
$Fenetre = Join-Path $Paquet "PlaceurRouteur.exe"
$Travail = Join-Path ([System.IO.Path]::GetTempPath()) ("fumee_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $Travail | Out-Null
$Resultats = New-Object System.Collections.ArrayList
$OUTILS = @("importer_carte", "deduire_config", "construire_donnees", "extraire_reference", "verifier_placement",
            "drc_maison", "vias_couture", "mesures", "proprete", "mesure_convertisseurs", "visualiser",
            "generer_pcbdoc", "generer_kicad", "drc_serigraphie", "formats")

function Noter([string]$Nom, [string]$Etat, [string]$Detail) {
    [void]$Resultats.Add([pscustomobject]@{ Nom = $Nom; Etat = $Etat; Detail = $Detail })
    Write-Output ("[{0}] {1} : {2}" -f $Etat, $Nom, $Detail)
}

function Lancer([string]$Exe, [string[]]$Arguments, [string]$Sortie) {
    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -NoNewWindow -PassThru -Wait `
        -RedirectStandardOutput $Sortie -RedirectStandardError ($Sortie + ".err") -RedirectStandardInput ($Sortie + ".vide")
    return $p.ExitCode
}

function Citer([string]$Texte) { return '"' + $Texte + '"' }

function Moteurs-Vivants { return @(Get-Process -Name "placeur_routage", "serigraphie" -ErrorAction SilentlyContinue).Count }

function Dossier-Du-Calcul([string]$Sortie) {
    foreach ($l in Get-Content -LiteralPath $Sortie -Encoding UTF8 -ErrorAction SilentlyContinue) {
        if ($l -match '"type"\s*:\s*"debut"') { return (($l -replace '^EVT ', '') | ConvertFrom-Json).sortie }
    }
    return $null
}

function Exemple-Kicad { return Join-Path $Paquet "exemples\kicad_deux_couches\deux_couches.kicad_pcb" }
function Exemple-Pcbdoc { return Join-Path $Paquet "exemples\demonstration\demonstration.PcbDoc" }

function Chaine([string]$Nom, [string]$Exe, [string]$Carte, [string]$Parent) {
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    $s = Join-Path $Travail ($Nom + ".txt")
    "" | Set-Content -LiteralPath ($s + ".vide")
    $code = Lancer $Exe @((Citer $Carte), "--qualite", "rapide", "--essais", "2", "--sortie", (Citer $Parent), "--evenements") $s
    return @{ Code = $code; Sortie = $s; Dossier = (Dossier-Du-Calcul $s) }
}

function Lancement-En-Fond([string]$Carte, [string]$Parent, [string]$Nom) {
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    $s = Join-Path $Travail ($Nom + ".txt")
    $p = Start-Process -FilePath $Placeur -ArgumentList @((Citer $Carte), "--qualite", "normal", "--essais", "4",
        "--sortie", (Citer $Parent), "--evenements") -NoNewWindow -PassThru -RedirectStandardOutput $s `
        -RedirectStandardError ($s + ".err")
    $null = $p.Handle   # garde le handle : sans lui, ExitCode reste vide après WaitForExit (Start-Process -PassThru)
    $limite = (Get-Date).AddSeconds(120)
    $dossier = $null
    while (-not $dossier -and (Get-Date) -lt $limite -and -not $p.HasExited) {
        Start-Sleep -Milliseconds 500
        $dossier = Dossier-Du-Calcul $s
    }
    while ((Moteurs-Vivants) -eq 0 -and (Get-Date) -lt $limite -and -not $p.HasExited) { Start-Sleep -Milliseconds 500 }
    return @{ Processus = $p; Dossier = $dossier; Sortie = $s }
}

function Copier-Paquet([string]$Cible) {
    New-Item -ItemType Directory -Force -Path $Cible | Out-Null
    Copy-Item -Path (Join-Path $Paquet "*") -Destination $Cible -Recurse -Force
    return (Join-Path $Cible "placeur.exe")
}

try {
    # 1. version
    $s = Join-Path $Travail "version.txt"; "" | Set-Content -LiteralPath ($s + ".vide")
    $c = Lancer $Placeur @("--version") $s
    $t = (Get-Content -LiteralPath $s -Raw -Encoding UTF8)
    if ($c -eq 0 -and $t -match '\d+\.\d+\.\d+\+[0-9a-f]{7}') { Noter "version" "OK" $t.Trim() } else { Noter "version" "ÉCHEC" "code $c" }

    # 2. auto-test, puis moteur retiré
    $s = Join-Path $Travail "auto_test.txt"; "" | Set-Content -LiteralPath ($s + ".vide")
    $c = Lancer $Placeur @("--auto-test") $s
    if ($c -eq 0) { Noter "auto_test" "OK" "code 0" } else {
        $d = (@(Get-Content -LiteralPath $s, ($s + ".err") -ErrorAction SilentlyContinue) | Where-Object { $_ -match "CHEC|rreur" }) -join " | "
        Noter "auto_test" "ÉCHEC" "code $c : $d"
    }
    $copie = Copier-Paquet (Join-Path $Travail "sans_moteur")
    Remove-Item -LiteralPath (Join-Path (Split-Path $copie) "_internal\moteurs\placeur_routage.exe")
    $s = Join-Path $Travail "auto_test_sans_moteur.txt"; "" | Set-Content -LiteralPath ($s + ".vide")
    $c = Lancer $copie @("--auto-test") $s
    $t = (Get-Content -LiteralPath $s -Raw -Encoding UTF8) + (Get-Content -LiteralPath ($s + ".err") -Raw -Encoding UTF8)
    if ($c -ne 0 -and $t -match "quarantaine") { Noter "auto_test" "OK" "moteur absent signalé" } else { Noter "auto_test" "ÉCHEC" "moteur absent non signalé (code $c)" }

    # 3. chaîne KiCad
    $r = Chaine "chaine_kicad" $Placeur (Exemple-Kicad) (Join-Path $Travail "kicad")
    $produite = $r.Dossier -and @(Get-ChildItem -LiteralPath $r.Dossier -Filter "*.kicad_pcb" -ErrorAction SilentlyContinue).Count -gt 0
    if ($r.Code -eq 0 -and $produite) { Noter "chaine_kicad" "OK" "code 0, .kicad_pcb produit" } else { Noter "chaine_kicad" "ÉCHEC" ("code $($r.Code) : " + ((@(Get-Content -LiteralPath $r.Sortie, ($r.Sortie + ".err") -ErrorAction SilentlyContinue) | Where-Object { $_ -match "rreur|CHEC|refus" } | Select-Object -Last 3) -join " | ")) }

    # 4. chaîne .PcbDoc de démonstration, et 6. sérigraphie
    if (Test-Path -LiteralPath (Exemple-Pcbdoc)) {
        $r = Chaine "chaine_pcbdoc" $Placeur (Exemple-Pcbdoc) (Join-Path $Travail "pcbdoc")
        $rapport = if ($r.Dossier) { Get-Content -LiteralPath (Join-Path $r.Dossier "rapport.txt") -Raw -Encoding UTF8 -ErrorAction SilentlyContinue } else { "" }
        if ($r.Code -eq 0 -and $rapport -match "coherent") { Noter "chaine_pcbdoc" "OK" "code 0, sérigraphie coherent" } else { Noter "chaine_pcbdoc" "ÉCHEC" ("code $($r.Code) : " + ((@(Get-Content -LiteralPath $r.Sortie, ($r.Sortie + ".err") -ErrorAction SilentlyContinue) | Where-Object { $_ -match "rreur|CHEC|refus" } | Select-Object -Last 3) -join " | ")) }
        if ($rapport -match "serigraphie") { Noter "serigraphie" "OK" "sérigraphie contrôlée" } else { Noter "serigraphie" "ÉCHEC" "aucun contrôle de sérigraphie au rapport" }
    } else {
        Noter "chaine_pcbdoc" "SAUTÉ" "carte de démonstration absente du paquet (lot B)"
        Noter "serigraphie" "SAUTÉ" "carte de démonstration absente du paquet (lot B)"
    }

    # 4 bis. modes de calcul : placement seul, sérigraphie seule
    foreach ($m in @(@{ Nom = "mode_placement"; Mode = "placement"; Motif = "_PLACEMENT" }, @{ Nom = "mode_serigraphie"; Mode = "serigraphie"; Motif = "_SERIGRAPHIE" })) {
        if (Test-Path -LiteralPath (Exemple-Pcbdoc)) {
            $parent = Join-Path $Travail $m.Nom
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
            $s = Join-Path $Travail ($m.Nom + ".txt")
            "" | Set-Content -LiteralPath ($s + ".vide")
            $code = Lancer $Placeur @((Citer (Exemple-Pcbdoc)), "--qualite", "apercu", "--essais", "1", "--mode-calcul", $m.Mode, "--sortie", (Citer $parent), "--evenements") $s
            $d = Dossier-Du-Calcul $s
            $produite = $d -and @(Get-ChildItem -LiteralPath $d -Filter ("*" + $m.Motif + "*.PcbDoc") -ErrorAction SilentlyContinue).Count -gt 0
            if ($code -eq 0 -and $produite) { Noter $m.Nom "OK" ("code 0, carte " + $m.Motif + " produite") } else { Noter $m.Nom "ÉCHEC" "code $code, carte produite : $produite" }
        } else { Noter $m.Nom "SAUTÉ" "carte de démonstration absente du paquet" }
    }

    # 5. --donnees METROID
    if ($Metroid) {
        $d = Join-Path $Depot "PCB_V2\outils\placeur_routage\exemples\metroid_v2\donnees_placeur.json"
        $s = Join-Path $Travail "metroid.txt"; "" | Set-Content -LiteralPath ($s + ".vide")
        $c = Lancer $Placeur @("--donnees", (Citer $d), "--essais", "1", "--sans-pcbdoc", "--sortie", (Citer (Join-Path $Travail "metroid"))) $s
        if ($c -eq 0) { Noter "donnees_metroid" "OK" "code 0" } else { Noter "donnees_metroid" "ÉCHEC" "code $c" }
    } else { Noter "donnees_metroid" "SAUTÉ" "sans -Metroid" }

    # 7 à 9. arrêts
    $f = Lancement-En-Fond (Exemple-Pcbdoc) (Join-Path $Travail "arret1") "arret1"   # reprise jusqu'au livrable (.PcbDoc)
    if ($f.Dossier) {
        "" | Set-Content -LiteralPath (Join-Path $f.Dossier "arret_lancements")
        $f.Processus.WaitForExit(600000) | Out-Null
        if ($f.Processus.ExitCode -eq 3) { Noter "arret_lancements" "OK" "code 3" } else { Noter "arret_lancements" "ÉCHEC" "code $($f.Processus.ExitCode)" }
        # 10. reprise du calcul arrêté
        $s = Join-Path $Travail "reprise.txt"; "" | Set-Content -LiteralPath ($s + ".vide")
        $c = Lancer $Placeur @("--reprendre", (Citer $f.Dossier), "--evenements") $s
        $t = Get-Content -LiteralPath $s -Raw -Encoding UTF8
        if ($c -eq 0 -and $t -match "repris") { Noter "reprise" "OK" "graines finies reprises" } else { Noter "reprise" "ÉCHEC" ("code $c : " + ((@(Get-Content -LiteralPath $s, ($s + ".err") -ErrorAction SilentlyContinue) | Where-Object { $_ -match "rreur|refus" } | Select-Object -Last 2) -join " | ")) }
    } else { Noter "arret_lancements" "ÉCHEC" "dossier du calcul inconnu"; Noter "reprise" "ÉCHEC" "pas de calcul à reprendre" }

    $f = Lancement-En-Fond (Exemple-Kicad) (Join-Path $Travail "arret2") "arret2"
    if ($f.Dossier) {
        "" | Set-Content -LiteralPath (Join-Path $f.Dossier "arret_demande")
        $f.Processus.WaitForExit(30000) | Out-Null
        Start-Sleep -Seconds 2
        if ((Moteurs-Vivants) -eq 0) { Noter "arret_demande" "OK" "aucun moteur 2 s après" } else { Noter "arret_demande" "ÉCHEC" "moteur encore vivant" }
    } else { Noter "arret_demande" "ÉCHEC" "dossier du calcul inconnu" }

    $f = Lancement-En-Fond (Exemple-Kicad) (Join-Path $Travail "arret3") "arret3"
    Stop-Process -Id $f.Processus.Id -Force
    Start-Sleep -Seconds 2
    if (@(Get-Process -Name "placeur_routage" -ErrorAction SilentlyContinue).Count -eq 0) { Noter "stop_process" "OK" "aucun placeur_routage 2 s après" } else { Noter "stop_process" "ÉCHEC" "placeur_routage encore vivant" }

    # 11 à 13. chemins
    $accentue = Join-Path $env:SystemDrive "Essai é à\l'atelier (copie)"
    $exe = Copier-Paquet (Join-Path $accentue "PlaceurRouteur")
    Copy-Item -LiteralPath (Exemple-Pcbdoc) -Destination $accentue
    $r = Chaine "chemin_accentue" $exe (Join-Path $accentue "demonstration.PcbDoc") (Join-Path $accentue "résultats")
    if ($r.Code -eq 0) { Noter "chemin_accentue" "OK" "code 0" } else { Noter "chemin_accentue" "ÉCHEC" "code $($r.Code)" }
    Remove-Item -LiteralPath $accentue -Recurse -Force -ErrorAction SilentlyContinue

    $nonLatin = Join-Path $Travail ([string]([char]0x8A66) + [char]0x9A13 + "_" + [char]0x0418 + [char]0x0441 + [char]0x043F)
    $exe = Copier-Paquet (Join-Path $nonLatin "PlaceurRouteur")
    Copy-Item -LiteralPath (Exemple-Pcbdoc) -Destination $nonLatin
    $r = Chaine "chemin_non_latin" $exe (Join-Path $nonLatin "demonstration.PcbDoc") (Join-Path $nonLatin "sortie")
    if ($r.Code -eq 0) { Noter "chemin_non_latin" "OK" "code 0" } else { Noter "chemin_non_latin" "ÉCHEC" "code $($r.Code)" }

    $lecteur = $Paquet.Substring(0, 1)
    $unc = "\\localhost\" + $lecteur + "$" + $Paquet.Substring(2)
    if (Test-Path -LiteralPath (Join-Path $unc "placeur.exe")) {
        $s = Join-Path $Travail "unc.txt"; "" | Set-Content -LiteralPath ($s + ".vide")
        $c = Lancer (Join-Path $unc "placeur.exe") @("--auto-test") $s
        if ($c -eq 0) { Noter "chemin_unc" "OK" "code 0" } else { Noter "chemin_unc" "ÉCHEC" "code $c" }
    } else { Noter "chemin_unc" "ÉCHEC" "partage administratif inaccessible" }

    # 14. sortie redirigée de chaque outil
    $mauvais = @()
    foreach ($o in $OUTILS) {
        $s = Join-Path $Travail ("outil_" + $o + ".txt"); "" | Set-Content -LiteralPath ($s + ".vide")
        $c = Lancer $Placeur @("--outil", $o, "--help") $s
        $octets = [System.IO.File]::ReadAllBytes($s) + [System.IO.File]::ReadAllBytes($s + ".err")
        try { [void](New-Object System.Text.UTF8Encoding($false, $true)).GetString($octets) } catch { $mauvais += $o }
        if ($c -gt 2) { $mauvais += "$o (code $c)" }
    }
    if ($mauvais.Count -eq 0) { Noter "sortie_redirigee" "OK" "$($OUTILS.Count) outils" } else { Noter "sortie_redirigee" "ÉCHEC" ($mauvais -join ", ") }

    # 15. fenêtre
    $p = Start-Process -FilePath $Fenetre -ArgumentList @("--essai-fenetre", "8000") -PassThru
    if ($p.WaitForExit(60000) -and $p.ExitCode -eq 0) { Noter "fenetre" "OK" "fin sans blocage" } else {
        if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
        Noter "fenetre" "ÉCHEC (non bloquant)" "pas de fin propre en 60 s"
    }

    # 16. fenêtre sur la carte de démonstration : règles, aperçu, carte inchangée
    $demo = Exemple-Pcbdoc
    if (Test-Path -LiteralPath $demo) {
        $avant = (Get-FileHash -LiteralPath $demo -Algorithm SHA256).Hash
        $rapport = Join-Path $Travail "fenetre_carte.json"
        $p = Start-Process -FilePath $Fenetre -ArgumentList @("--essai-fenetre", "12000", "--carte", (Citer $demo), "--rapport", (Citer $rapport)) -PassThru
        if ($p.WaitForExit(90000) -and $p.ExitCode -eq 0 -and (Test-Path -LiteralPath $rapport)) {
            $r = Get-Content -LiteralPath $rapport -Raw -Encoding UTF8 | ConvertFrom-Json
            $apres = (Get-FileHash -LiteralPath $demo -Algorithm SHA256).Hash
            if ($r.regles -gt 0 -and $r.pieces -gt 0 -and $r.verrouillees -ge 1 -and $avant -eq $apres) {
                Noter "fenetre_carte" "OK" ("{0} règles, {1} pièces dont {2} verrouillée(s)" -f $r.regles, $r.pieces, $r.verrouillees)
            } else { Noter "fenetre_carte" "ÉCHEC (non bloquant)" ("règles {0}, pièces {1}, verrouillées {2}, carte inchangée {3}" -f $r.regles, $r.pieces, $r.verrouillees, ($avant -eq $apres)) }
        } else {
            if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
            Noter "fenetre_carte" "ÉCHEC (non bloquant)" "pas de rapport ni de fin propre en 90 s"
        }
    } else { Noter "fenetre_carte" "SAUTÉ" "carte de démonstration absente" }
}
catch {
    Noter "script" "ÉCHEC" ($_.Exception.Message)
}
finally {
    Get-Process -Name "placeur_routage", "serigraphie" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

$bloquants = @($Essais | Where-Object { $_.Bloquant } | ForEach-Object { $_.Nom })
$echecs = @($Resultats | Where-Object { $_.Etat -eq "ÉCHEC" -and ($bloquants -contains $_.Nom -or $_.Nom -eq "script") })
Write-Output ("{0} résultat(s), {1} échec(s) bloquant(s)." -f $Resultats.Count, $echecs.Count)
if ($echecs.Count -gt 0) { exit 1 }
exit 0
