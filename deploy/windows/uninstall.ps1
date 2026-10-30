<#
.SYNOPSIS
    Rimuove i servizi Windows registrati da install.ps1 per QUESTA app
    (ocr-cannabis-web ed eventuali worker con lo stesso prefisso) e
    l'attivita' pianificata di backup.

.DESCRIPTION
    NON tocca: dati (database Postgres, cartella data\, log, backup),
    infrastruttura condivisa (infra-ollama, postgresql-x64-16, uv, WinSW),
    il checkout git di questa repo o della repo pipeline. Serve solo a
    disinstallare il servizio applicativo, non l'ambiente che lo ospita -
    per una disinstallazione completa, rimuovi a mano anche cartelle e
    database dopo aver controllato di non avere piu' bisogno dei dati.

.PARAMETER MantieniAttivitaBackup
    Non rimuove l'attivita' pianificata "ocr-cannabis-backup" (utile se
    si vuole continuare a fare backup di un database che resta al suo
    posto).
#>
param(
    [switch]$MantieniAttivitaBackup
)

. (Join-Path $PSScriptRoot "common.ps1")

Test-Amministratore

Write-Step "Fermo e rimuovo i servizi Windows con prefisso '$ServicePrefix'"
$serviziApp = Get-Service -Name "$ServicePrefix*" -ErrorAction SilentlyContinue
if (-not $serviziApp) {
    Write-Host "    nessun servizio con prefisso '$ServicePrefix' trovato."
} else {
    foreach ($svc in $serviziApp) {
        Write-Host "    $($svc.Name) ($($svc.Status))"
        if ($svc.Status -eq "Running") {
            Stop-Service -Name $svc.Name -Force
        }
        $xmlPath = Join-Path $WinswRoot "$($svc.Name).xml"
        $svcExe = Join-Path $WinswRoot "$($svc.Name).exe"
        if (Test-Path $svcExe) {
            & $svcExe uninstall $xmlPath
        } else {
            # WinSW non trovato per questo servizio (installato a mano o
            # con un altro strumento) - rimozione diretta come fallback.
            sc.exe delete $svc.Name | Out-Null
        }
        Remove-Item $svcExe -ErrorAction SilentlyContinue
        Remove-Item $xmlPath -ErrorAction SilentlyContinue
    }
}

if (-not $MantieniAttivitaBackup) {
    Write-Step "Rimuovo l'attivita' pianificata 'ocr-cannabis-backup'"
    if (Get-ScheduledTask -TaskName "ocr-cannabis-backup" -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName "ocr-cannabis-backup" -Confirm:$false
        Write-Host "    rimossa."
    } else {
        Write-Host "    non presente."
    }
} else {
    Write-Step "Attivita' pianificata di backup mantenuta (-MantieniAttivitaBackup)"
}

Write-Step "Disinstallazione completata"
Write-Host "NON toccati: database Postgres, $DataRoot, $LogsRoot, $BackupRoot, infra-ollama, postgresql-x64-16." -ForegroundColor Yellow
Write-Host "Rimuovili a mano se non servono piu' (dopo aver verificato di non avere bisogno dei dati)." -ForegroundColor Yellow
