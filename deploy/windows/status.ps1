<#
.SYNOPSIS
    Stato rapido dell'installazione: servizi Windows, /health, spazio su
    disco, ultimi errori nei log  -  per una verifica veloce dopo un
    aggiornamento o un riavvio del server.
#>

. (Join-Path $PSScriptRoot "common.ps1")

Write-Step "Servizi Windows"
foreach ($nome in @($ServiceOllama, $ServiceWebapp)) {
    $svc = Get-Service -Name $nome -ErrorAction SilentlyContinue
    if ($svc) {
        $colore = if ($svc.Status -eq "Running") { "Green" } else { "Red" }
        Write-Host ("    {0,-20} {1}" -f $nome, $svc.Status) -ForegroundColor $colore
    } else {
        Write-Host ("    {0,-20} non registrato" -f $nome) -ForegroundColor Red
    }
}
$pgSvc = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pgSvc) {
    $colore = if ($pgSvc.Status -eq "Running") { "Green" } else { "Red" }
    Write-Host ("    {0,-20} {1}" -f $pgSvc.Name, $pgSvc.Status) -ForegroundColor $colore
} else {
    Write-Avviso "Nessun servizio 'postgresql*' trovato."
}

Write-Step "/health"
try {
    $risposta = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "    HTTP $($risposta.StatusCode)  -  $($risposta.Content)" -ForegroundColor Green
} catch {
    Write-Host "    Non raggiungibile: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Step "Spazio su disco (E:)"
$disco = Get-PSDrive -Name "E" -ErrorAction SilentlyContinue
if ($disco) {
    $liberoGB = [math]::Round($disco.Free / 1GB, 1)
    $usatoGB = [math]::Round($disco.Used / 1GB, 1)
    $coloreDisco = if ($liberoGB -lt 50) { "Red" } elseif ($liberoGB -lt 150) { "Yellow" } else { "Green" }
    Write-Host "    liberi $liberoGB GB / usati $usatoGB GB" -ForegroundColor $coloreDisco
} else {
    Write-Avviso "Disco E: non trovato."
}

Write-Step "Ultimi errori nei log della webapp (ultime 24 ore)"
$logWebapp = Join-Path $LogsRoot "ats-webapp.log"
if (Test-Path $logWebapp) {
    # Formato riga: "yyyy-MM-dd HH:mm:ss,fff [LIVELLO] ..." (vedi il
    # Formatter in app/main.py)  -  confrontabile come stringa perche'
    # ISO-simile, senza dover fare il parsing di ogni riga.
    $sogliaTesto = (Get-Date).AddHours(-24).ToString("yyyy-MM-dd HH:mm:ss")
    $righeErrore = Get-Content $logWebapp -Tail 2000 | Where-Object {
        $_ -match "\[ERROR\]" -and ($_.Substring(0, [Math]::Min(19, $_.Length)) -ge $sogliaTesto)
    }
    if ($righeErrore) {
        $righeErrore | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    } else {
        Write-Host "    nessun errore nelle ultime righe del log." -ForegroundColor Green
    }
} else {
    Write-Avviso "Log non trovato in $logWebapp."
}

Write-Step "Ultimo backup"
$ultimoBackup = Get-ChildItem -Path $BackupRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
if ($ultimoBackup) {
    $eta = (Get-Date) - $ultimoBackup.CreationTime
    $colore = if ($eta.TotalHours -gt 48) { "Red" } else { "Green" }
    Write-Host "    $($ultimoBackup.Name) ($([math]::Round($eta.TotalHours, 1)) ore fa)" -ForegroundColor $colore
} else {
    Write-Avviso "Nessun backup trovato in $BackupRoot."
}
