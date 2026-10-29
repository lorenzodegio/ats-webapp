<#
.SYNOPSIS
    Valori e funzioni condivise da install.ps1/update.ps1/backup.ps1/
    restore.ps1/status.ps1 — dot-sorgente (". .\common.ps1"), mai eseguito
    da solo.

.DESCRIPTION
    Layout assunto sul server (vedi DEPLOY_WINDOWS.md per i dettagli):
      E:\infra\ollama               servizio Ollama standalone (dati/modelli)
      E:\infra\winsw                WinSW.exe + XML dei servizi generati
      E:\apps\ocr-cannabis           questo repository (checkout git)
      E:\apps\ocr-cannabis\docker\.venv   ambiente Python della pipeline (uv)
      E:\apps\ocr-cannabis\.venv          ambiente Python della webapp (uv)
      E:\apps\ocr-cannabis\data            STORAGE_LOCAL_DIR + modelli EasyOCR
      E:\apps\ocr-cannabis\logs            log della webapp (rotazione)
      E:\apps\ocr-cannabis\backup          dump Postgres + copie di data\

    NON TESTATO su un vero Windows Server — scritto leggendo il codice reale
    (app/database.py, app/storage.py, alembic/, pyproject.toml) e la
    convenzione del progetto, ma verificalo con attenzione al primo uso
    reale (vedi "Verifica" in DEPLOY_WINDOWS.md) prima di fidartene in
    produzione.
#>

$ErrorActionPreference = "Stop"

# ── Layout ──────────────────────────────────────────────────────────────
$script:RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:InfraRoot  = if ($env:ATS_INFRA_ROOT) { $env:ATS_INFRA_ROOT } else { "E:\infra" }
$script:OllamaRoot = Join-Path $InfraRoot "ollama"
$script:WinswRoot  = Join-Path $InfraRoot "winsw"
$script:DataRoot   = Join-Path $RepoRoot "data"
$script:LogsRoot   = Join-Path $RepoRoot "logs"
$script:BackupRoot = Join-Path $RepoRoot "backup"
$script:PipelineVenv = Join-Path $RepoRoot "docker\.venv"
$script:WebappVenv   = Join-Path $RepoRoot ".venv"

# ── Servizi Windows (WinSW) ─────────────────────────────────────────────
$script:ServicePrefix  = "ATS-"
$script:ServiceOllama  = "${ServicePrefix}Ollama"
$script:ServiceWebapp  = "${ServicePrefix}Webapp"

# ── Utente dedicato ──────────────────────────────────────────────────────
$script:ServiceAccount = if ($env:ATS_SERVICE_ACCOUNT) { $env:ATS_SERVICE_ACCOUNT } else { "$env:COMPUTERNAME\svc_ocr" }

function Write-Step {
    param([string]$Messaggio)
    Write-Host ""
    Write-Host "==> $Messaggio" -ForegroundColor Cyan
}

function Write-Avviso {
    param([string]$Messaggio)
    Write-Host "    ATTENZIONE: $Messaggio" -ForegroundColor Yellow
}

function Test-Amministratore {
    $identita = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principale = New-Object Security.Principal.WindowsPrincipal($identita)
    if (-not $principale.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Esegui questo script da una PowerShell aperta come Amministratore."
    }
}

function Get-DotEnvPath {
    $percorso = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $percorso)) {
        throw "Non trovo $percorso — copia .env.example in .env e configuralo prima di procedere."
    }
    return $percorso
}

function Get-DotEnvValue {
    <# Legge UN valore da .env senza eseguirlo come script — per riusare le
       stesse credenziali Postgres gia' configurate per la webapp, invece
       di chiederle una seconda volta o duplicarle in un file diverso. #>
    param([Parameter(Mandatory)][string]$Chiave, [string]$Default = "")
    $percorso = Get-DotEnvPath
    $riga = Get-Content $percorso | Where-Object { $_ -match "^\s*$Chiave\s*=" } | Select-Object -Last 1
    if (-not $riga) { return $Default }
    $valore = ($riga -split "=", 2)[1].Trim()
    $valore = $valore.Trim('"').Trim("'")
    if ([string]::IsNullOrEmpty($valore)) { return $Default }
    return $valore
}

function Get-ConnessionePostgres {
    <# Ritorna un hashtable con host/porta/db/utente/password letti da .env
       (stessa fonte di app/database.py) — usato da backup/restore. #>
    return @{
        Host     = Get-DotEnvValue -Chiave "POSTGRES_HOST" -Default "localhost"
        Porta    = Get-DotEnvValue -Chiave "POSTGRES_PORT" -Default "5432"
        Db       = Get-DotEnvValue -Chiave "POSTGRES_DB"
        Utente   = Get-DotEnvValue -Chiave "POSTGRES_USER"
        Password = Get-DotEnvValue -Chiave "POSTGRES_PASSWORD"
    }
}

function Find-PsqlBin {
    <# Gli strumenti a riga di comando di PostgreSQL non sono sempre nel
       PATH dopo un'installazione standard — cerca nelle posizioni tipiche
       prima di arrendersi. #>
    $candidati = @("psql.exe", "createdb.exe", "pg_dump.exe") | ForEach-Object {
        $cmd = Get-Command $_ -ErrorAction SilentlyContinue
        if ($cmd) { return (Split-Path $cmd.Source -Parent) }
    }
    if ($candidati) { return $candidati | Select-Object -First 1 }
    $pattern = "C:\Program Files\PostgreSQL\*\bin"
    $trovato = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if ($trovato) { return $trovato.FullName }
    throw "Non trovo gli strumenti psql/pg_dump/createdb — aggiungi 'C:\Program Files\PostgreSQL\<versione>\bin' al PATH di sistema."
}

function Invoke-ServiceControl {
    param([Parameter(Mandatory)][string]$Nome, [Parameter(Mandatory)][ValidateSet("start", "stop", "restart")][string]$Azione)
    $servizio = Get-Service -Name $Nome -ErrorAction SilentlyContinue
    if (-not $servizio) {
        Write-Avviso "Servizio '$Nome' non registrato — esegui prima install.ps1."
        return
    }
    switch ($Azione) {
        "start"   { if ($servizio.Status -ne "Running") { Start-Service $Nome } }
        "stop"    { if ($servizio.Status -eq "Running") { Stop-Service $Nome } }
        "restart" { Restart-Service $Nome -Force }
    }
}

function Test-Health {
    param([int]$TentativiMax = 10, [int]$AttesaSecondi = 3)
    for ($i = 1; $i -le $TentativiMax; $i++) {
        try {
            $risposta = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 5
            if ($risposta.StatusCode -eq 200) { return $true }
        } catch {
            Write-Verbose "Tentativo $i/$TentativiMax su /health fallito: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds $AttesaSecondi
    }
    return $false
}
