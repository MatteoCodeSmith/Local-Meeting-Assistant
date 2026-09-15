$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonw = [System.IO.Path]::GetFullPath((Join-Path $projectRoot ".venv\Scripts\pythonw.exe"))
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "Ambiente non installato. Eseguire prima scripts\install.ps1"
}
$command = '"' + $pythonw + '" -m local_meeting_assistant'
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-Item -Path $runKey -Force | Out-Null
New-ItemProperty -Path $runKey -Name "LocalMeetingAssistant" -Value $command -PropertyType String -Force | Out-Null
Write-Host "Avvio automatico abilitato per l'utente corrente."
