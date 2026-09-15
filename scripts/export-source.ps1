[CmdletBinding()]
param([switch]$IncludeTests)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$prefix = $projectRoot.TrimEnd('\') + '\'
$files = [Collections.Generic.List[string]]::new()

function Add-SourceFile([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "File esterno al progetto." }
    # Refuse links/junctions at every level: the export must not follow private external data.
    $item = Get-Item -LiteralPath $full -Force
    while ($item -and $item.FullName -ne $projectRoot) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Collegamento non esportabile: $full" }
        $item = if ($item.PSIsContainer) { $item.Parent } else { $item.Directory }
    }
    $files.Add($full)
}

foreach ($name in @('README.md','pyproject.toml','LICENSE','THIRD_PARTY_NOTICES.md','AGENTS.md','.gitignore','create-desktop-shortcut.ps1')) {
    Add-SourceFile (Join-Path $projectRoot $name)
}
foreach ($folder in @('src','scripts')) {
    Get-ChildItem -LiteralPath (Join-Path $projectRoot $folder) -Recurse -File |
        Where-Object { $_.Extension -in @('.py','.ps1') -and $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
        ForEach-Object { Add-SourceFile $_.FullName }
}
Add-SourceFile (Join-Path $projectRoot 'src\local_meeting_assistant\assets\assistant-icon.png')
Add-SourceFile (Join-Path $projectRoot 'src\local_meeting_assistant\assets\assistant-icon.ico')
if ($IncludeTests) {
    Get-ChildItem -LiteralPath (Join-Path $projectRoot 'tests') -File -Filter '*.py' |
        ForEach-Object { Add-SourceFile $_.FullName }
}
$outputDir = Join-Path $projectRoot '.local'
if (Test-Path -LiteralPath $outputDir) {
    if ((Get-Item -LiteralPath $outputDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'La cartella export e un collegamento.' }
} else {
    New-Item -ItemType Directory -Path $outputDir | Out-Null
}
$target = Join-Path $outputDir ('bionic-source-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.zip')
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::Open($target, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in $files) {
        $relative = $file.Substring($prefix.Length).Replace('\','/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file, 'BIONIC/' + $relative) | Out-Null
    }
} finally {
    $archive.Dispose()
}
Write-Host "Sorgenti esportati: $target"
Write-Host "File inclusi: $($files.Count). Nessun modello, ambiente Python o dato dei meeting incluso."
