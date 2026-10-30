<#
.SYNOPSIS
    Installazione idempotente su un nuovo Windows Server: ambienti Python
    (uv), database Postgres (ruolo/password generata), migrazioni Alembic,
    primo utente admin, servizio Windows ocr-cannabis-web (WinSW).

.DESCRIPTION
    Prerequisiti gia' predisposti sul server da uno script di setup, NON
    gestiti/reinstallati da questo script (vedi DEPLOY_WINDOWS.md): Git,
    Python 3.11 via uv (UV_PYTHON_INSTALL_DIR), uv nel PATH, VC++
    2013/2015-2022, PostgreSQL 16 (servizio postgresql-x64-16, solo
    localhost, bin nel PATH), Tesseract-OCR con lingua ita (nel PATH),
    Ollama condiviso (servizio infra-ollama, modelli gia' scaricati),
    WinSW in E:\infra\tools\winsw, IIS come reverse proxy HTTPS
    (certificato/binding configurati a mano). Nessun utente di servizio
    dedicato: i servizi girano con l'utenza unica del progetto (account
    amministratore gia' esistente), la cui password viene chiesta qui.

    Rieseguibile in sicurezza: ogni passo controlla lo stato attuale prima
    di agire (cartelle/servizio/database/ruolo gia' esistenti vengono
    saltati, non ricreati da zero).

    NON TESTATO su un vero Windows Server  -  verifica ogni passo con
    attenzione al primo uso reale (vedi "Verifica" in DEPLOY_WINDOWS.md).

.PARAMETER SaltaServizi
    Prepara tutto (ambienti, DB, migrazioni, admin) ma non registra il
    servizio Windows  -  utile per un primo giro di verifica manuale
    (avvio diretto di uvicorn in primo piano).
#>
param(
    [switch]$SaltaServizi
)

. (Join-Path $PSScriptRoot "common.ps1")

Test-Amministratore

# -- 1. Prerequisiti --------------------------------------------------------
Write-Step "Verifico i prerequisiti (servizi, uv, Python, Tesseract, modelli Ollama, permessi)"
foreach ($comando in @("git", "uv", "tesseract")) {
    if (-not (Get-Command $comando -ErrorAction SilentlyContinue)) {
        throw "'$comando' non e' nel PATH  -  verifica l'installazione del server (vedi DEPLOY_WINDOWS.md), non e' compito di questo script installarlo."
    }
}
$linguaIta = (& tesseract --list-langs 2>&1) -join "`n"
if ($linguaIta -notmatch "ita") {
    throw "Tesseract non ha la lingua 'ita' installata (tesseract --list-langs) -  correggi prima di continuare."
}

$pgSvc = Get-Service -Name $ServizioPostgresEsterno -ErrorAction SilentlyContinue
if (-not $pgSvc) {
    throw "Servizio '$ServizioPostgresEsterno' non trovato  -  PostgreSQL 16 e' gia' installato sul server? (vedi DEPLOY_WINDOWS.md, prerequisito non gestito da questo script)."
}
if ($pgSvc.Status -ne "Running") {
    throw "Servizio '$ServizioPostgresEsterno' non e' in esecuzione  -  avvialo prima di continuare."
}

$ollamaSvc = Get-Service -Name $ServizioOllamaEsterno -ErrorAction SilentlyContinue
if (-not $ollamaSvc) {
    throw "Servizio '$ServizioOllamaEsterno' non trovato  -  Ollama condiviso e' gia' installato sul server? (vedi DEPLOY_WINDOWS.md, prerequisito non gestito da questo script)."
}
if ($ollamaSvc.Status -ne "Running") {
    throw "Servizio '$ServizioOllamaEsterno' non e' in esecuzione  -  avvialo prima di continuare."
}
try {
    $modelliOllama = (& ollama list 2>&1) -join "`n"
} catch {
    throw "Impossibile interrogare Ollama (comando 'ollama list')  -  $($_.Exception.Message)"
}
foreach ($modello in @("qwen3-vl:30b-a3b-instruct", "qwen3-vl:8b-instruct")) {
    if ($modelliOllama -notmatch [regex]::Escape($modello)) {
        throw "Modello Ollama '$modello' non trovato (ollama list)  -  dovrebbe essere gia' scaricato su questo server (vedi DEPLOY_WINDOWS.md)."
    }
}

if (-not (Test-Path (Join-Path $PipelineRoot "pyproject.toml"))) {
    throw @"
Repo pipeline non trovata/non pronta in $PipelineRoot (pyproject.toml assente).
Clona ocr-cannabis-ats li' (branch allineato a questo, vedi CLAUDE.md) prima
di continuare -  sovrascrivibile con la variabile d'ambiente ATS_PIPELINE_ROOT.
"@
}

Write-Host "    prerequisiti ok ($ServizioPostgresEsterno, $ServizioOllamaEsterno, modelli Ollama, repo pipeline, Tesseract ita)"

# -- Cartelle --------------------------------------------------------------
Write-Step "Creo le cartelle dati/log/backup (se mancanti)"
$cartelle = @($DataRoot, (Join-Path $DataRoot "storage"), (Join-Path $DataRoot "models\easyocr"),
              $LogsRoot, (Join-Path $LogsRoot "winsw"), $BackupRoot)
foreach ($cartella in $cartelle) {
    if (-not (Test-Path $cartella)) {
        New-Item -ItemType Directory -Path $cartella -Force | Out-Null
        Write-Host "    creata: $cartella"
    }
}

# -- 2. Ambienti Python (uv) -------------------------------------------------
Write-Step "Sincronizzo l'ambiente della webapp (uv sync, $RepoRoot)"
Push-Location $RepoRoot
try { uv sync } finally { Pop-Location }

Write-Step "Sincronizzo l'ambiente della pipeline (uv sync, $PipelineRoot  -  repo separata)"
Push-Location $PipelineRoot
try { uv sync } finally { Pop-Location }

$webappPython   = Join-Path $WebappVenv "Scripts\python.exe"
$pipelinePython = Join-Path $PipelineVenv "Scripts\python.exe"
foreach ($py in @($webappPython, $pipelinePython)) {
    if (-not (Test-Path $py)) { throw "Interprete atteso ma non trovato: $py  -  'uv sync' e' andato a buon fine?" }
}

# -- .env --------------------------------------------------------------------
$envPath = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envPath)) {
    Write-Step "Creo .env da .env.example  -  DA COMPLETARE A MANO prima di procedere"
    Copy-Item (Join-Path $RepoRoot ".env.example") $envPath
    Write-Avviso "$envPath creato dal modello  -  imposta almeno APP_ENV=production, POSTGRES_DB/POSTGRES_USER (es. ocr_cannabis), PIPELINE_RUNNER=local, PIPELINE_PYTHON, PIPELINE_DIR, STORAGE_LOCAL_DIR, poi rilancia install.ps1 (POSTGRES_PASSWORD e SESSION_SECRET_KEY, se lasciate vuote, vengono generate automaticamente al prossimo giro)."
    return
}

# -- Smoke test ambiente pipeline --------------------------------------------
Write-Step "Smoke test dell'ambiente pipeline (import, lettura barcode, tesseract ita)"
$barcodeFixture = Join-Path $PSScriptRoot "barcode_test.png"
if (-not (Test-Path $barcodeFixture)) {
    throw "Fixture di test non trovata: $barcodeFixture"
}
$smokeTestPython = @"
import sys

moduli = [
    "fitz", "cv2", "numpy", "PIL", "pytesseract", "pyzbar.pyzbar",
    "deskew", "pandas", "openpyxl", "ollama", "easyocr", "torch", "psutil",
]
falliti = []
for nome in moduli:
    try:
        __import__(nome)
    except Exception as exc:
        falliti.append(f"{nome}: {exc}")
if falliti:
    print("Import falliti:")
    for riga in falliti:
        print(f"  - {riga}")
    sys.exit(1)
print(f"Import ok per {len(moduli)} moduli.")

from pyzbar.pyzbar import decode
from PIL import Image
risultati = decode(Image.open(r"$barcodeFixture"))
if not risultati or risultati[0].data.decode("utf-8") != "8012345678905":
    print("Lettura barcode di test FALLITA (pyzbar/libzbar non funzionante).")
    sys.exit(1)
print("Lettura barcode di test ok.")

import subprocess
esito = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
lingue = (esito.stdout + esito.stderr)
if "ita" not in lingue:
    print("Lingua 'ita' non installata in Tesseract (tesseract --list-langs):")
    print(lingue)
    sys.exit(1)
print("Tesseract con lingua ita ok.")
"@
& $pipelinePython -c $smokeTestPython
if ($LASTEXITCODE -ne 0) {
    throw "Smoke test dell'ambiente pipeline fallito (vedi output sopra) - correggi prima di procedere."
}

# -- 3. Database e ruolo Postgres --------------------------------------------
Write-Step "Database e ruolo Postgres (ENCODING UTF8, TEMPLATE template0)"
$pgBin = Find-PsqlBin
$env:Path = "$pgBin;$env:Path"
$conn = Get-ConnessionePostgres
if (-not $conn.Db -or -not $conn.Utente) {
    throw "POSTGRES_DB/POSTGRES_USER non impostati in .env  -  completali prima di rilanciare install.ps1 (es. ocr_cannabis per entrambi)."
}

if (-not $conn.Password) {
    Write-Host "    POSTGRES_PASSWORD assente: genero una password casuale per il ruolo '$($conn.Utente)' e la scrivo in .env"
    $conn.Password = New-PasswordCasuale
    Set-DotEnvValue -Chiave "POSTGRES_PASSWORD" -Valore $conn.Password
}

$sicuraPostgres = Read-Host "Password del superuser 'postgres' di PostgreSQL" -AsSecureString
$passwordPostgres = ConvertFrom-SecureStringPlain -Sicura $sicuraPostgres
$env:PGPASSWORD = $passwordPostgres
try {
    $utenteEsiste = (& psql -h $conn.Host -p $conn.Porta -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$($conn.Utente)'" 2>$null)
    if ($utenteEsiste -ne "1") {
        Write-Host "    creo il ruolo $($conn.Utente)"
        & psql -h $conn.Host -p $conn.Porta -U postgres -c "CREATE ROLE `"$($conn.Utente)`" LOGIN PASSWORD '$($conn.Password)';"
        if ($LASTEXITCODE -ne 0) { throw "Creazione del ruolo '$($conn.Utente)' fallita (codice $LASTEXITCODE)  -  password del superuser postgres corretta?" }
    } else {
        Write-Host "    ruolo $($conn.Utente) gia' esistente, aggiorno la password a quella in .env"
        & psql -h $conn.Host -p $conn.Porta -U postgres -c "ALTER ROLE `"$($conn.Utente)`" WITH PASSWORD '$($conn.Password)';"
    }
    $dbEsiste = (& psql -h $conn.Host -p $conn.Porta -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$($conn.Db)'" 2>$null)
    if ($dbEsiste -ne "1") {
        Write-Host "    creo il database $($conn.Db) (UTF8, template0)"
        & psql -h $conn.Host -p $conn.Porta -U postgres -c "CREATE DATABASE `"$($conn.Db)`" OWNER `"$($conn.Utente)`" ENCODING 'UTF8' TEMPLATE template0;"
        if ($LASTEXITCODE -ne 0) { throw "Creazione del database '$($conn.Db)' fallita (codice $LASTEXITCODE)." }
    }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    $passwordPostgres = $null
}

# -- 4. SESSION_SECRET_KEY ----------------------------------------------------
Write-Step "SESSION_SECRET_KEY"
$secretAttuale = Get-DotEnvValue -Chiave "SESSION_SECRET_KEY" -Default ""
if (-not $secretAttuale) {
    Write-Host "    assente: ne genero una e la scrivo in .env"
    Set-DotEnvValue -Chiave "SESSION_SECRET_KEY" -Valore (New-PasswordCasuale -Lunghezza 64)
} else {
    Write-Host "    gia' presente in .env, non la cambio"
}

# -- 5. Migrazioni Alembic ----------------------------------------------------
Write-Step "Applico le migrazioni Alembic (alembic upgrade head)"
Push-Location $RepoRoot
try {
    & $webappPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head ha restituito un errore (codice $LASTEXITCODE)." }
} finally {
    Pop-Location
}

# -- 6. Primo utente admin ----------------------------------------------------
Write-Step "Creo il primo utente admin (se non esiste gia')"
$sicuraAdmin = Read-Host "Password per l'utente admin" -AsSecureString
$passwordAdmin = ConvertFrom-SecureStringPlain -Sicura $sicuraAdmin
try {
    $env:ADMIN_INITIAL_PASSWORD = $passwordAdmin
    Push-Location $RepoRoot
    try { & $webappPython seed_admin.py } finally { Pop-Location }
} finally {
    Remove-Item Env:\ADMIN_INITIAL_PASSWORD -ErrorAction SilentlyContinue
    $passwordAdmin = $null
}

# -- 7. Modelli EasyOCR --------------------------------------------------------
Write-Step "Scarico i modelli EasyOCR in anticipo (se mancanti)"
$easyocrDir = Get-DotEnvValue -Chiave "EASYOCR_MODEL_DIR" -Default (Join-Path $DataRoot "models\easyocr")
$scriptEasyOcr = @"
import easyocr
easyocr.Reader(["it"], gpu=False, model_storage_directory=r"$easyocrDir")
print("Modelli EasyOCR pronti in $easyocrDir")
"@
& $pipelinePython -c $scriptEasyOcr

if ($SaltaServizi) {
    Write-Step "Servizio Windows saltato (-SaltaServizi)  -  installazione preparata, avvia manualmente per verificare:"
    Write-Host "    & `"$webappPython`" -m uvicorn app.main:app --host 127.0.0.1 --port 8000" -ForegroundColor Green
    return
}

# -- 8. Servizio Windows (WinSW) -----------------------------------------------
Write-Step "Registro il servizio Windows $ServiceWebapp (WinSW)"
$winswExe = Join-Path $WinswRoot "WinSW-x64.exe"
if (-not (Test-Path $winswExe)) {
    throw "WinSW-x64.exe non trovato in $WinswRoot  -  scaricalo da https://github.com/winsw/winsw/releases (vedi DEPLOY_WINDOWS.md) e rilancia install.ps1."
}

function Format-WinswXml {
    param(
        [string]$Id, [string]$Nome, [string]$Descrizione,
        [string]$Eseguibile, [string]$Argomenti, [string]$CartellaLavoro,
        [string[]]$Dipendenze = @(), [hashtable]$Ambiente = @{}
    )
    $logPath = Join-Path $LogsRoot "winsw"
    $envXml = ($Ambiente.GetEnumerator() | ForEach-Object { "  <env name=`"$($_.Key)`" value=`"$($_.Value)`"/>" }) -join "`n"
    $dependXml = ($Dipendenze | ForEach-Object { "  <depend>$_</depend>" }) -join "`n"
    return @"
<service>
  <id>$Id</id>
  <name>$Nome</name>
  <description>$Descrizione</description>
  <executable>$Eseguibile</executable>
  <arguments>$Argomenti</arguments>
  <workingdirectory>$CartellaLavoro</workingdirectory>
  <logpath>$logPath</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <resetfailure>1 hour</resetfailure>
$dependXml
$envXml
</service>
"@
}

$rootPath = Get-DotEnvValue -Chiave "ROOT_PATH" -Default ""
$argRootPath = if ($rootPath) { " --root-path $rootPath" } else { "" }
$appPort = Get-DotEnvValue -Chiave "APP_PORT" -Default "8000"

$xmlWebapp = Format-WinswXml -Id $ServiceWebapp -Nome "ATS Insubria - Gestione Prescrizioni Cannabis" `
    -Descrizione "Webapp FastAPI (uvicorn) - gestione prescrizioni cannabis terapeutica" `
    -Eseguibile $webappPython `
    -Argomenti "-m uvicorn app.main:app --host 127.0.0.1 --port $appPort --proxy-headers --forwarded-allow-ips=127.0.0.1$argRootPath" `
    -CartellaLavoro $RepoRoot `
    -Dipendenze @($ServizioOllamaEsterno, $ServizioPostgresEsterno)
Set-Content -Path (Join-Path $WinswRoot "$ServiceWebapp.xml") -Value $xmlWebapp -Encoding UTF8

$svcExe = Join-Path $WinswRoot "$ServiceWebapp.exe"
Copy-Item $winswExe $svcExe -Force
if (Get-Service -Name $ServiceWebapp -ErrorAction SilentlyContinue) {
    Write-Host "    servizio $ServiceWebapp gia' registrato, aggiorno solo la configurazione"
} else {
    & $svcExe install (Join-Path $WinswRoot "$ServiceWebapp.xml")
}

Write-Step "Imposto $ServiceAccount come account di esecuzione del servizio"
$sicuraAccount = Read-Host "Password dell'account di servizio ($ServiceAccount)" -AsSecureString
$passwordAccount = ConvertFrom-SecureStringPlain -Sicura $sicuraAccount
try {
    & sc.exe config $ServiceWebapp obj= "$ServiceAccount" password= "$passwordAccount" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Avviso "sc.exe config e' fallito (codice $LASTEXITCODE)  -  imposta l'account a mano da services.msc (scheda 'Accesso'). Verifica anche che '$ServiceAccount' abbia il diritto 'Log on as a service' (secpol.msc), altrimenti il servizio non partira'."
    }
} finally {
    $passwordAccount = $null
}

# -- 9. Avvio e verifica -------------------------------------------------------
Write-Step "Avvio il servizio $ServiceWebapp"
Invoke-ServiceControl -Nome $ServiceWebapp -Azione start
if (Test-Health) {
    Write-Host "OK  -  l'app risponde su /health." -ForegroundColor Green
} else {
    Write-Host "Il servizio e' avviato ma /health non risponde  -  controlla i log in $LogsRoot\winsw\ e $LogsRoot" -ForegroundColor Red
}

Write-Step "Attivita' pianificata di backup giornaliero"
$azione = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'backup.ps1')`""
$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
if (Get-ScheduledTask -TaskName "ocr-cannabis-backup" -ErrorAction SilentlyContinue) {
    Write-Host "    attivita' 'ocr-cannabis-backup' gia' presente, la lascio invariata (modificala a mano se serve)"
} else {
    # -User/-Password (non -Principal, che non memorizza la password) e'
    # la combinazione che registra l'attivita' con una password salvata,
    # necessaria perche' parta anche senza una sessione interattiva aperta.
    $sicuraTask = Read-Host "Password di $ServiceAccount per l'attivita' pianificata di backup" -AsSecureString
    $passwordTask = ConvertFrom-SecureStringPlain -Sicura $sicuraTask
    try {
        Register-ScheduledTask -TaskName "ocr-cannabis-backup" -Action $azione -Trigger $trigger `
            -User $ServiceAccount -Password $passwordTask -RunLevel Highest `
            -Description "Backup giornaliero DB + dati ATS Insubria" | Out-Null
    } finally {
        $passwordTask = $null
    }
}

Write-Step "Installazione completata"
Write-Host "Verifica con: .\status.ps1" -ForegroundColor Green
