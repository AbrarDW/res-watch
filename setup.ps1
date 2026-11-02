# res-watch installer for Windows (PowerShell).
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#   powershell -ExecutionPolicy Bypass -File setup.ps1 --where local --os windows --start autostart --yes
# Creates .venv, installs dependencies, then runs `python -m reswatch setup <args>`.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = $null
foreach ($c in @($env:PYTHON, "py -3", "python", "python3")) {
    if (-not $c) { continue }
    $parts = $c.Split(" ")
    try {
        & $parts[0] $parts[1..($parts.Length)] -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $parts; break }
    } catch { }
}
if (-not $py) { Write-Error "Python 3.10 or newer is required (https://www.python.org/downloads/)"; exit 1 }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment in .venv"
    & $py[0] $py[1..($py.Length)] -m venv .venv
}
$vpy = ".venv\Scripts\python.exe"
& $vpy -m pip install --quiet --upgrade pip
Write-Host "Installing dependencies"
& $vpy -m pip install --quiet -r requirements.txt
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "NVIDIA GPU detected: installing GPU support"
    & $vpy -m pip install --quiet -r requirements-gpu.txt
}
& $vpy -m pip install --quiet --no-deps -e .

& $vpy -m reswatch setup @args
exit $LASTEXITCODE
