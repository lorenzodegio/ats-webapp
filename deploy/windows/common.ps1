<#
.SYNOPSIS
    Valori e funzioni condivise da install.ps1/update.ps1/backup.ps1/
    restore.ps1/status.ps1  -  dot-sorgente (". .\common.ps1"), mai eseguito
    da solo.

.DESCRIPTION
    Layout assunto sul server, gia' predisposto da uno script di setup
    (vedi DEPLOY_WINDOWS.md per i dettagli, NON gestito da questi script):
      E:\infra\tools\uv             uv (nel PATH)
      E:\infra\tools\winsw          WinSW-x64.exe + XML dei servizi generati
      E:\infra\python               Python gestito da uv (UV_PYTHON_INSTALL_DIR)
      E:\infra\cache\uv             cache di uv (UV_CACHE_DIR)
      servizio "infra-ollama"       Ollama condiviso, gia' in esecuzione,
                                     modelli gia' scaricati  -  NON gestito
                                     da install.ps1, solo una dipendenza
      servizio "postgresql-x64-16"  PostgreSQL 16, solo localhost, bin nel PATH
      E:\apps\ocr-cannabis\app        questa repository (checkout git)
      E:\apps\ocr-cannabis\app\.venv  ambiente Python della webapp (uv)
      E:\apps\ocr-cannabis\pipeline        repo SEPARATA ocr-cannabis-ats (checkout git)
      E:\apps\ocr-cannabis\pipeline\.venv  ambiente Python della pipeline (uv)
      E:\apps\ocr-cannabis\data            STORAGE_LOCAL_DIR + modelli EasyOCR
      E:\apps\ocr-cannabis\logs            log della webapp (rotazione)
      E:\apps\ocr-cannabis\backup          dump Postgres + copie di data\

    Nessun utente di servizio dedicato: i servizi girano con l'utenza unica
    del progetto (account amministratore gia' esistente), la cui password
    viene chiesta all'installazione  -  non creata/gestita qui.

    NON TESTATO su un vero Windows Server  -  scritto leggendo il codice reale
    (app/database.py, app/storage.py, alembic/, pyproject.toml) e la
    convenzione del progetto, ma verificalo con attenzione al primo uso
    reale (vedi "Verifica" in DEPLOY_WINDOWS.md) prima di fidartene in
    produzione.
#>

$ErrorActionPreference = "Stop"

# -- Layout ----------------------------------------------------------------
# RepoRoot = cartella "app" (questa repo) -  PipelineRoot = cartella
# "pipeline" SIBLING, non dentro RepoRoot (repo ocr-cannabis-ats separata,
# vedi CLAUDE.md): niente piu' docker\.venv dentro questa repo.
$script:RepoRoot     = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:AppsRoot      = Split-Path $RepoRoot -Parent
$script:PipelineRoot  = if ($env:ATS_PIPELINE_ROOT) { $env:ATS_PIPELINE_ROOT } else { Join-Path $AppsRoot "pipeline" }
$script:InfraRoot     = if ($env:ATS_INFRA_ROOT) { $env:ATS_INFRA_ROOT } else { "E:\infra" }
$script:WinswRoot     = Join-Path $InfraRoot "tools\winsw"
$script:DataRoot      = Join-Path $AppsRoot "data"
$script:LogsRoot      = Join-Path $AppsRoot "logs"
$script:BackupRoot    = Join-Path $AppsRoot "backup"
$script:PipelineVenv  = Join-Path $PipelineRoot ".venv"
$script:WebappVenv    = Join-Path $RepoRoot ".venv"

# -- Servizi Windows (WinSW) -----------------------------------------------
# "infra-ollama"/"postgresql-x64-16" sono infrastruttura CONDIVISA, gia'
# installata da altri -  solo nomi di riferimento per le dipendenze, questi
# script non li registrano/avviano/fermano mai.
$script:ServicePrefix       = "ocr-cannabis-"
$script:ServiceWebapp       = "${ServicePrefix}web"
$script:ServizioOllamaEsterno    = "infra-ollama"
$script:ServizioPostgresEsterno  = "postgresql-x64-16"

# -- Account di servizio ----------------------------------------------------
# Utenza unica del progetto (account amministratore gia' esistente, non
# creato qui) -  default: chi esegue questo script; sovrascrivibile.
$script:ServiceAccount = if ($env:ATS_SERVICE_ACCOUNT) { $env:ATS_SERVICE_ACCOUNT } else { "$env:COMPUTERNAME\$env:USERNAME" }

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
        throw "Non trovo $percorso  -  copia .env.example in .env e configuralo prima di procedere."
    }
    return $percorso
}

function Get-DotEnvValue {
    <# Legge UN valore da .env senza eseguirlo come script  -  per riusare le
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

function Set-DotEnvValue {
    <# Scrive/aggiorna UNA chiave in .env, preservando tutte le altre righe
       (commenti compresi) -  usata per scrivere la password generata del
       ruolo Postgres dell'app e SESSION_SECRET_KEY quando assenti, invece
       di fermarsi e chiedere all'operatore di completarle a mano. Se la
       chiave esiste gia' (anche commentata con "# CHIAVE=") la riga viene
       sostituita con una versione non commentata; altrimenti viene
       aggiunta in fondo al file. #>
    param([Parameter(Mandatory)][string]$Chiave, [Parameter(Mandatory)][string]$Valore)
    $percorso = Get-DotEnvPath
    $righe = Get-Content $percorso
    $trovata = $false
    $nuoveRighe = $righe | ForEach-Object {
        if ($_ -match "^\s*#?\s*$Chiave\s*=") {
            $trovata = $true
            "$Chiave=$Valore"
        } else {
            $_
        }
    }
    if (-not $trovata) {
        $nuoveRighe += "$Chiave=$Valore"
    }
    # Retry breve: un antivirus/indexer che tiene il file aperto un istante
    # e' un lock transitorio, non un errore reale -  vedi la stessa
    # situazione (ben peggiore, un file perso) incontrata scrivendo questi
    # stessi script con Move-Item invece di un retry in-place come questo.
    $tentativi = 0
    while ($true) {
        try {
            Set-Content -Path $percorso -Value $nuoveRighe -Encoding UTF8 -ErrorAction Stop
            break
        } catch {
            $tentativi++
            if ($tentativi -ge 5) { throw }
            Start-Sleep -Milliseconds 300
        }
    }
}

function New-PasswordCasuale {
    <# Password casuale per il ruolo Postgres dell'app (non per account
       Windows/admin, quelle restano sempre chieste all'operatore) -  solo
       caratteri alfanumerici, cosi' non serve preoccuparsi di escaping in
       stringhe di connessione o comandi psql. #>
    param([int]$Lunghezza = 32)
    $alfabeto = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    $bytes = New-Object byte[] $Lunghezza
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $alfabeto[$_ % $alfabeto.Length] })
}

function ConvertFrom-SecureStringPlain {
    <# Decodifica una SecureString (da Read-Host -AsSecureString) nel testo
       in chiaro SOLO per il tempo necessario a usarla (PGPASSWORD, sc.exe
       config, ecc.) -  mai scritta su disco da questa funzione. #>
    param([Parameter(Mandatory)][System.Security.SecureString]$Sicura)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Sicura)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Get-ConnessionePostgres {
    <# Ritorna un hashtable con host/porta/db/utente/password letti da .env
       (stessa fonte di app/database.py)  -  usato da backup/restore. #>
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
       PATH dopo un'installazione standard  -  cerca nelle posizioni tipiche
       prima di arrendersi. #>
    $candidati = @("psql.exe", "createdb.exe", "pg_dump.exe") | ForEach-Object {
        $cmd = Get-Command $_ -ErrorAction SilentlyContinue
        if ($cmd) { return (Split-Path $cmd.Source -Parent) }
    }
    if ($candidati) { return $candidati | Select-Object -First 1 }
    $pattern = "C:\Program Files\PostgreSQL\*\bin"
    $trovato = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if ($trovato) { return $trovato.FullName }
    throw "Non trovo gli strumenti psql/pg_dump/createdb  -  aggiungi 'C:\Program Files\PostgreSQL\<versione>\bin' al PATH di sistema."
}

function Invoke-ServiceControl {
    param([Parameter(Mandatory)][string]$Nome, [Parameter(Mandatory)][ValidateSet("start", "stop", "restart")][string]$Azione)
    $servizio = Get-Service -Name $Nome -ErrorAction SilentlyContinue
    if (-not $servizio) {
        Write-Avviso "Servizio '$Nome' non registrato  -  esegui prima install.ps1."
        return
    }
    switch ($Azione) {
        "start"   { if ($servizio.Status -ne "Running") { Start-Service $Nome } }
        "stop"    { if ($servizio.Status -eq "Running") { Stop-Service $Nome } }
        "restart" { Restart-Service $Nome -Force }
    }
}

function Test-Health {
    <# Interroga sempre /health direttamente su uvicorn (loopback), non
       passando da IIS: ROOT_PATH riguarda solo come l'app genera i
       propri link/redirect per il browser esterno, non il path interno
       che uvicorn instrada -  /health resta "/health" anche con
       ROOT_PATH impostata. #>
    param([int]$TentativiMax = 10, [int]$AttesaSecondi = 3)
    $porta = Get-DotEnvValue -Chiave "APP_PORT" -Default "8000"
    for ($i = 1; $i -le $TentativiMax; $i++) {
        try {
            $risposta = Invoke-WebRequest -Uri "http://127.0.0.1:$porta/health" -UseBasicParsing -TimeoutSec 5
            if ($risposta.StatusCode -eq 200) { return $true }
        } catch {
            Write-Verbose "Tentativo $i/$TentativiMax su /health fallito: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds $AttesaSecondi
    }
    return $false
}
