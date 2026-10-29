<#
.SYNOPSIS
    Ripristina un backup creato da backup.ps1 — database Postgres e,
    opzionalmente, la cartella data\.

.DESCRIPTION
    DISTRUTTIVO: sovrascrive il database attuale. Ferma i servizi prima di
    ripristinare e chiede conferma esplicita.

.PARAMETER CartellaBackup
    Percorso completo della cartella di backup da ripristinare (es.
    "E:\apps\ocr-cannabis\backup\2026-10-29_030000"). Obbligatorio.

.PARAMETER SoloDatabase
    Ripristina solo il database, lascia invariata la cartella data\
    attuale.
#>
param(
    [Parameter(Mandatory)][string]$CartellaBackup,
    [switch]$SoloDatabase
)

. (Join-Path $PSScriptRoot "common.ps1")

Test-Amministratore

if (-not (Test-Path $CartellaBackup)) {
    throw "Cartella di backup non trovata: $CartellaBackup"
}
$conn = Get-ConnessionePostgres
$dumpPath = Join-Path $CartellaBackup "$($conn.Db).dump"
if (-not (Test-Path $dumpPath)) {
    throw "Dump non trovato in $dumpPath — la cartella e' davvero un backup prodotto da backup.ps1?"
}

Write-Host ""
Write-Host "QUESTA OPERAZIONE SOVRASCRIVE il database '$($conn.Db)' attuale con il contenuto di:" -ForegroundColor Yellow
Write-Host "  $dumpPath" -ForegroundColor Yellow
if (-not $SoloDatabase) {
    Write-Host "e la cartella '$DataRoot' con il contenuto di '$CartellaBackup\data'." -ForegroundColor Yellow
}
$conferma = Read-Host "Scrivi ESATTAMENTE 'ripristina' per procedere"
if ($conferma -ne "ripristina") {
    Write-Host "Annullato." -ForegroundColor Red
    exit 1
}

Write-Step "Fermo i servizi"
Invoke-ServiceControl -Nome $ServiceWebapp -Azione stop

Write-Step "Ripristino il database (pg_restore --clean)"
$pgBin = Find-PsqlBin
$env:Path = "$pgBin;$env:Path"
$env:PGPASSWORD = $conn.Password
try {
    & pg_restore -h $conn.Host -p $conn.Porta -U $conn.Utente -d $conn.Db --clean --if-exists $dumpPath
    if ($LASTEXITCODE -ne 0) { Write-Avviso "pg_restore ha restituito codice $LASTEXITCODE — controlla l'output sopra: alcuni avvisi (es. oggetti che 'non esistono' durante --clean) sono normali, un fallimento totale non lo e'." }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

if (-not $SoloDatabase) {
    $sorgenteDati = Join-Path $CartellaBackup "data"
    if (Test-Path $sorgenteDati) {
        Write-Step "Ripristino la cartella data\"
        if (Test-Path $DataRoot) { Remove-Item $DataRoot -Recurse -Force }
        Copy-Item -Path $sorgenteDati -Destination $DataRoot -Recurse -Force
    } else {
        Write-Avviso "Nessuna cartella 'data' nel backup, salto (era forse un backup con -SoloDatabase?)."
    }
}

Write-Step "Riavvio i servizi"
Invoke-ServiceControl -Nome $ServiceWebapp -Azione start

if (Test-Health) {
    Write-Host "Ripristino completato, l'app risponde su /health." -ForegroundColor Green
} else {
    Write-Host "Ripristino eseguito ma l'app NON risponde su /health — controlla i log." -ForegroundColor Red
}
