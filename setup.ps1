# res-watch installer for Windows (PowerShell).
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#   powershell -ExecutionPolicy Bypass -File setup.ps1 --where local --os windows --start autostart --yes
# Creates .venv, installs dependencies, then runs `python -m reswatch setup <args>`.
#
# Output: in an interactive console you get a progress bar (Write-Progress) and pip's
# progress bars. With --yes / -y, redirected output, or CI set, output is plain text.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$Total = 5
$Fancy = [Environment]::UserInteractive -and -not [Console]::IsOutputRedirected -and -not $env:CI
foreach ($a in $args) { if ($a -eq "--yes" -or $a -eq "-y") { $Fancy = $false } }
if ($Fancy) { $PipOut = @("--progress-bar", "on") } else { $PipOut = @("--progress-bar", "off", "--quiet"); $ProgressPreference = "SilentlyContinue" }

function Step([int]$n, [string]$text) {
    Write-Host ("[{0}/{1}] {2}" -f $n, $Total, $text)
    if ($Fancy) {
        Write-Progress -Activity "res-watch setup" -Status ("[{0}/{1}] {2}" -f $n, $Total, $text) -PercentComplete ([int](($n - 1) * 100 / $Total))
    }
}
function Info([string]$text) { Write-Host "      $text" }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { Write-Progress -Activity "res-watch setup" -Completed; Write-Error "$what failed (exit $LASTEXITCODE)"; exit 1 } }

# ---------------------------------------------------------------- 1. python
Step 1 "Checking Python"
$pyExe = $null; $pyArgs = @()
foreach ($c in @($env:PYTHON, "py -3", "python", "python3")) {
    if (-not $c) { continue }
    $parts = @($c.Split(" ", [StringSplitOptions]::RemoveEmptyEntries))
    $rest = @(if ($parts.Count -gt 1) { $parts[1..($parts.Count - 1)] })
    try {
        & $parts[0] @rest -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>$null
        if ($LASTEXITCODE -eq 0) { $pyExe = $parts[0]; $pyArgs = $rest; break }
    } catch { }
}
if (-not $pyExe) { Write-Error "Python 3.10 or newer is required (https://www.python.org/downloads/)"; exit 1 }
Info ("found " + (& $pyExe @pyArgs --version 2>&1))

# ------------------------------------------------------------------ 2. venv
Step 2 "Creating virtual environment (.venv)"
if (Test-Path ".venv\Scripts\python.exe") {
    Info "already exists, reusing it"
} else {
    Info "creating .venv"
    & $pyExe @pyArgs -m venv .venv; Check "creating the venv"
}
$vpy = ".venv\Scripts\python.exe"
Info "updating pip"
& $vpy -m pip install --quiet --upgrade pip; Check "updating pip"

# ------------------------------------------------------------------ 3. deps
Step 3 "Installing dependencies (requirements.txt)"
& $vpy -m pip install @PipOut -r requirements.txt; Check "installing dependencies"
Info "installing res-watch (editable)"
& $vpy -m pip install --quiet --no-deps -e .; Check "installing res-watch"

# ------------------------------------------------------------------- 4. gpu
Step 4 "GPU support"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Info "NVIDIA GPU detected: installing NVML bindings"
    & $vpy -m pip install @PipOut -r requirements-gpu.txt; Check "installing GPU support"
} else {
    Info "skipped (no nvidia-smi found; the GPU tab will say metrics are unavailable)"
}

# ---------------------------------------------------------------- 5. wizard
Step 5 "Running the setup wizard"
if ($Fancy) { Write-Progress -Activity "res-watch setup" -Completed }   # don't cover the wizard's prompts
& $vpy -m reswatch setup @args
exit $LASTEXITCODE
