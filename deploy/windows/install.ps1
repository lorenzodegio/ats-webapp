<#
.SYNOPSIS
    Installazione idempotente su un nuovo Windows Server: cartelle/permessi,
    ambienti Python (uv), modelli Ollama/EasyOCR, database Postgres,
    migrazioni Alembic, servizi Windows (WinSW), attivita' pianificata di backup.

.DESCRIPTION
    Prerequisiti (installati a mano, non gestiti da questo script  -  vedi
    DEPLOY_WINDOWS.md): Git, Python 3.11, uv, VC++ 2013/2015-2022,
    PostgreSQL 16 (servizio, solo localhost), Ollama standalone, IIS come
    reverse proxy HTTPS (certificato/binding configurati a mano), utente
    locale svc_ocr gia' creato.

    Rieseguibile in sicurezza: ogni passo controlla lo stato attuale prima
    di agire (cartelle/servizi/database gia' esistenti vengono saltati, non
    ricreati da zero).

    NON TESTATO su un vero Windows Server  -  verifica ogni passo con
    attenzione al primo uso reale (vedi "Verifica" in DEPLOY_WINDOWS.md).

.PARAMETER SaltaModelli
    Salta il download dei modelli Ollama/EasyOCR (utile per rieseguire lo
    script senza riscaricare GB di modelli gia' presenti).

.PARAMETER SaltaServizi
    Prepara tutto (cartelle, ambienti, DB, migrazioni) ma non registra i
    servizi Windows  -  utile per un primo giro di verifica manuale.
#>
param(
    [switch]$SaltaModelli,
    [switch]$SaltaServizi
)

. (Join-Path $PSScriptRoot "common.ps1")

Test-Amministratore

Write-Step "Verifico i prerequisiti installati a mano"
foreach ($comando in @("git", "python", "uv")) {
    if (-not (Get-Command $comando -ErrorAction SilentlyContinue)) {
        throw "'$comando' non e' nel PATH  -  installalo prima di continuare (vedi DEPLOY_WINDOWS.md)."
    }
}
if (-not (Get-Command "ollama" -ErrorAction SilentlyContinue)) {
    Write-Avviso "'ollama' non e' nel PATH  -  assicurati che Ollama standalone sia installato e raggiungibile."
}

# -- Cartelle e permessi --------------------------------------------------
Write-Step "Creo le cartelle (se mancanti) e imposto i permessi per $ServiceAccount"
$cartelle = @($InfraRoot, $OllamaRoot, (Join-Path $OllamaRoot "models"), $WinswRoot,
              $DataRoot, (Join-Path $DataRoot "storage"), (Join-Path $DataRoot "models\easyocr"),
              $LogsRoot, (Join-Path $LogsRoot "winsw"), $BackupRoot)
foreach ($cartella in $cartelle) {
    if (-not (Test-Path $cartella)) {
        New-Item -ItemType Directory -Path $cartella -Force | Out-Null
        Write-Host "    creata: $cartella"
    }
}
foreach ($cartella in @($RepoRoot, $InfraRoot)) {
    try {
        icacls $cartella /grant "${ServiceAccount}:(OI)(CI)M" /T /Q | Out-Null
    } catch {
        Write-Avviso "icacls su '$cartella' fallito: $($_.Exception.Message)  -  concedi i permessi a $ServiceAccount a mano."
    }
}

# -- Ambienti Python (uv) -------------------------------------------------
Write-Step "Sincronizzo l'ambiente della webapp (uv sync, radice del repo)"
Push-Location $RepoRoot
try { uv sync } finally { Pop-Location }

Write-Step "Sincronizzo l'ambiente della pipeline (uv sync, docker/  -  separato dalla webapp)"
Push-Location (Join-Path $RepoRoot "docker")
try { uv sync } finally { Pop-Location }

$webappPython   = Join-Path $WebappVenv "Scripts\python.exe"
$pipelinePython = Join-Path $PipelineVenv "Scripts\python.exe"
foreach ($py in @($webappPython, $pipelinePython)) {
    if (-not (Test-Path $py)) { throw "Interprete atteso ma non trovato: $py  -  'uv sync' e' andato a buon fine?" }
}

# -- .env ------------------------------------------------------------------
$envPath = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envPath)) {
    Write-Step "Creo .env da .env.example  -  DA COMPLETARE A MANO prima di avviare i servizi"
    Copy-Item (Join-Path $RepoRoot ".env.example") $envPath
    Write-Avviso "$envPath creato dal modello  -  imposta almeno POSTGRES_PASSWORD, SESSION_SECRET_KEY, PIPELINE_PYTHON, PIPELINE_DIR, STORAGE_LOCAL_DIR prima di procedere, poi rilancia install.ps1."
    return
}

# -- Modelli Ollama/EasyOCR -----------------------------------------------
if (-not $SaltaModelli) {
    Write-Step "Scarico i modelli Ollama (se mancanti)  -  qwen3-vl:30b-a3b-instruct, qwen3-vl:8b-instruct"
    foreach ($modello in @("qwen3-vl:30b-a3b-instruct", "qwen3-vl:8b-instruct")) {
        Write-Host "    ollama pull $modello (puo' richiedere diversi minuti)"
        & ollama pull $modello
    }

    $easyocrDir = Get-DotEnvValue -Chiave "EASYOCR_MODEL_DIR" -Default (Join-Path $DataRoot "models\easyocr")
    Write-Step "Scarico i modelli EasyOCR in anticipo (in $easyocrDir, non nel profilo di $ServiceAccount)"
    $scriptPython = @"
import easyocr
easyocr.Reader(["it"], gpu=False, model_storage_directory=r"$easyocrDir")
print("Modelli EasyOCR pronti in $easyocrDir")
"@
    & $pipelinePython -c $scriptPython
} else {
    Write-Step "Download modelli saltato (-SaltaModelli)"
}

# -- Smoke test ambiente pipeline ------------------------------------------
# Eseguito sempre (anche con -SaltaModelli): verifica che le dipendenze
# native (torch/easyocr/opencv/pyzbar/tesseract) funzionino davvero su
# questa macchina, non solo che uv le abbia scaricate. Non stampa mai
# contenuto di documenti reali, solo esito import/decodifica.
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

# -- Database Postgres ----------------------------------------------------
Write-Step "Creo database/utente Postgres (se mancanti)  -  encoding UTF8 da template0"
$pgBin = Find-PsqlBin
$env:Path = "$pgBin;$env:Path"
$conn = Get-ConnessionePostgres
if (-not $conn.Db -or -not $conn.Utente -or -not $conn.Password) {
    throw "POSTGRES_DB/POSTGRES_USER/POSTGRES_PASSWORD non impostati in .env  -  completali prima di rilanciare install.ps1."
}
$env:PGPASSWORD = $conn.Password  # solo per questa sessione dello script, non scritto su disco
try {
    $utenteEsiste = (& psql -h $conn.Host -p $conn.Porta -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$($conn.Utente)'" 2>$null)
    if ($utenteEsiste -ne "1") {
        Write-Host "    creo il ruolo $($conn.Utente)"
        & psql -h $conn.Host -p $conn.Porta -U postgres -c "CREATE ROLE `"$($conn.Utente)`" LOGIN PASSWORD '$($conn.Password)';"
    }
    $dbEsiste = (& psql -h $conn.Host -p $conn.Porta -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$($conn.Db)'" 2>$null)
    if ($dbEsiste -ne "1") {
        Write-Host "    creo il database $($conn.Db) (UTF8, template0)"
        & psql -h $conn.Host -p $conn.Porta -U postgres -c "CREATE DATABASE `"$($conn.Db)`" OWNER `"$($conn.Utente)`" ENCODING 'UTF8' TEMPLATE template0;"
    }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
Write-Avviso "Il comando sopra usa 'psql -U postgres': se il superuser locale ha un nome/autenticazione diversi, aggiusta a mano ed esegui le due CREATE restanti."

# -- Migrazioni Alembic ---------------------------------------------------
Write-Step "Applico le migrazioni Alembic (alembic upgrade head)"
Push-Location $RepoRoot
try {
    & $webappPython -m alembic upgrade head
} finally {
    Pop-Location
}

# -- Primo utente admin ---------------------------------------------------
Write-Step "Creo il primo utente admin (se non esiste gia')"
$sicura = Read-Host "Password per l'utente admin" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sicura)
try {
    $env:ADMIN_INITIAL_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    Push-Location $RepoRoot
    try { & $webappPython seed_admin.py } finally { Pop-Location }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    Remove-Item Env:\ADMIN_INITIAL_PASSWORD -ErrorAction SilentlyContinue
}

if ($SaltaServizi) {
    Write-Step "Servizi Windows saltati (-SaltaServizi)  -  installazione preparata, avvia manualmente per verificare prima di registrare i servizi."
    return
}

# -- Servizi Windows (WinSW) ----------------------------------------------
Write-Step "Registro i servizi Windows (WinSW)"
$winswExe = Join-Path $WinswRoot "WinSW.exe"
if (-not (Test-Path $winswExe)) {
    throw "WinSW.exe non trovato in $WinswRoot  -  scaricalo da https://github.com/winsw/winsw/releases (vedi DEPLOY_WINDOWS.md) e rilancia install.ps1."
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

$xmlOllama = Format-WinswXml -Id $ServiceOllama -Nome "ATS Ollama" `
    -Descrizione "Ollama (Qwen3-VL) per la pipeline OCR ATS Insubria" `
    -Eseguibile (Get-Command ollama).Source -Argomenti "serve" -CartellaLavoro $OllamaRoot `
    -Ambiente @{
        OLLAMA_HOST             = "127.0.0.1:11434"
        OLLAMA_MODELS           = (Join-Path $OllamaRoot "models")
        OLLAMA_KEEP_ALIVE       = "-1"
        OLLAMA_MAX_LOADED_MODELS = "2"
        OLLAMA_NUM_PARALLEL     = "1"
    }
Set-Content -Path (Join-Path $WinswRoot "$ServiceOllama.xml") -Value $xmlOllama -Encoding UTF8

$xmlWebapp = Format-WinswXml -Id $ServiceWebapp -Nome "ATS Webapp" `
    -Descrizione "ATS Insubria - Gestione Prescrizioni Cannabis (FastAPI)" `
    -Eseguibile $webappPython `
    -Argomenti "-m uvicorn app.main:app --host 127.0.0.1 --port $appPort --proxy-headers --forwarded-allow-ips=127.0.0.1$argRootPath" `
    -CartellaLavoro $RepoRoot `
    -Dipendenze @($ServiceOllama, "postgresql-x64-16")
Set-Content -Path (Join-Path $WinswRoot "$ServiceWebapp.xml") -Value $xmlWebapp -Encoding UTF8
Write-Avviso "Dipendenza 'postgresql-x64-16' presuppone quel nome per il servizio Postgres  -  verifica con 'Get-Service postgresql*' e aggiusta l'XML se diverso."

foreach ($id in @($ServiceOllama, $ServiceWebapp)) {
    $xmlPath = Join-Path $WinswRoot "$id.xml"
    $svcExe = Join-Path $WinswRoot "$id.exe"
    Copy-Item $winswExe $svcExe -Force
    if (Get-Service -Name $id -ErrorAction SilentlyContinue) {
        Write-Host "    servizio $id gia' registrato, aggiorno solo la configurazione"
    } else {
        & $svcExe install $xmlPath
    }
}

Write-Step "Imposto $ServiceAccount come account di esecuzione dei servizi (richiede il 'logon as a service' gia' concesso)"
Write-Avviso "Questo passo NON e' automatizzato qui: imposta a mano l'account di accesso di $ServiceOllama/$ServiceWebapp da services.msc (scheda 'Accesso') o con 'sc.exe config <nome> obj= `"$ServiceAccount`" password= <password>'  -  vedi DEPLOY_WINDOWS.md per i dettagli e per concedere 'Log on as a service'."

Write-Step "Attivita' pianificata di backup giornaliero"
$azione = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'backup.ps1')`""
$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
$principale = New-ScheduledTaskPrincipal -UserId $ServiceAccount -LogonType Password -RunLevel Highest
if (Get-ScheduledTask -TaskName "ATS-Backup" -ErrorAction SilentlyContinue) {
    Write-Host "    attivita' 'ATS-Backup' gia' presente, la lascio invariata (modificala a mano se serve)"
} else {
    Register-ScheduledTask -TaskName "ATS-Backup" -Action $azione -Trigger $trigger -Principal $principale -Description "Backup giornaliero DB + dati ATS Insubria" | Out-Null
    Write-Avviso "L'attivita' pianificata e' stata creata con RunLevel Highest ma senza password salvata  -  aprila in Utilita' di pianificazione e inserisci le credenziali di $ServiceAccount, altrimenti non partira'."
}

Write-Step "Installazione completata"
Write-Host "Avvia i servizi con: Start-Service $ServiceOllama; Start-Service $ServiceWebapp" -ForegroundColor Green
Write-Host "Poi verifica con: .\status.ps1" -ForegroundColor Green
