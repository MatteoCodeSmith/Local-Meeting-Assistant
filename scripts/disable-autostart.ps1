$ErrorActionPreference = "Stop"

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-ItemProperty -Path $runKey -Name "LocalMeetingAssistant" -ErrorAction SilentlyContinue
Write-Host "Avvio automatico disabilitato."

