$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = [System.IO.Path]::GetFullPath((Join-Path $projectRoot ".venv\Scripts\pythonw.exe"))
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "Ambiente non installato. Eseguire prima .\scripts\install.ps1"
}
$iconPath = Join-Path $projectRoot "src\local_meeting_assistant\assets\assistant-icon.ico"
if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
    throw "Icona mancante. Ripristinare assistant-icon.ico dai sorgenti oppure eseguire .venv\Scripts\python.exe scripts\build_desktop_icon.py"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Local Meeting Assistant.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m local_meeting_assistant"
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = "Registra, trascrive e riassume meeting in locale"
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Save()

Write-Host "Collegamento creato: $shortcutPath"
