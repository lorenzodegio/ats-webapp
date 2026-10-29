<#
.SYNOPSIS
    Backup giornaliero: dump Postgres (pg_dump, formato custom) + copia
    della cartella data\ (storage locale, se STORAGE_BACKEND=local) — con
    rotazione (default 14 giorni).

.PARAMETER GiorniRotazione
    Quanti giorni di backup tenere prima di cancellare i piu' vecchi.
#>
param(
    [int]$GiorniRotazione = 14
)

. (Join-Path $PSScriptRoot "common.ps1")

$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$cartellaBackup = Join-Path $BackupRoot $timestamp
New-Item -ItemType Directory -Path $cartellaBackup -Force | Out-Null

Write-Step "Dump del database Postgres"
$pgBin = Find-PsqlBin
$env:Path = "$pgBin;$env:Path"
$conn = Get-ConnessionePostgres
$dumpPath = Join-Path $cartellaBackup "$($conn.Db).dump"
$env:PGPASSWORD = $conn.Password
try {
    & pg_dump -h $conn.Host -p $conn.Porta -U $conn.Utente -F custom -f $dumpPath $conn.Db
    if ($LASTEXITCODE -ne 0) { throw "pg_dump ha restituito un errore (codice $LASTEXITCODE)." }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
Write-Host "    $dumpPath ($([math]::Round((Get-Item $dumpPath).Length / 1MB, 1)) MB)"

Write-Step "Copia della cartella data\ (storage locale)"
if (Test-Path $DataRoot) {
    $destDati = Join-Path $cartellaBackup "data"
    Copy-Item -Path $DataRoot -Destination $destDati -Recurse -Force
    Write-Host "    copiata in $destDati"
} else {
    Write-Avviso "$DataRoot non esiste, salto la copia (STORAGE_BACKEND diverso da 'local'?)."
}

Write-Step "Rotazione backup piu' vecchi di $GiorniRotazione giorni"
$sogliaData = (Get-Date).AddDays(-$GiorniRotazione)
Get-ChildItem -Path $BackupRoot -Directory | Where-Object { $_.CreationTime -lt $sogliaData } | ForEach-Object {
    Write-Host "    elimino $($_.FullName)"
    Remove-Item $_.FullName -Recurse -Force
}

Write-Step "Backup completato: $cartellaBackup"
