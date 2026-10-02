# Create local folders and smoke-check the kit on Windows. Safe to re-run.
# Usage (from the kit root or anywhere): powershell -ExecutionPolicy Bypass -File adapters\windows\setup.ps1
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Py = if ($env:PYTHON) { $env:PYTHON } elseif (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
& $Py -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'need Python 3.12+' }
foreach ($d in 'state', 'drop\returns', 'drop\_archive') {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null
}
Push-Location $Root
try {
    & $Py grokkit.py list | Out-Null
    Write-Output "grokkit ok at $Root"
    & $Py grokkit.py inbox
} finally {
    Pop-Location
}
