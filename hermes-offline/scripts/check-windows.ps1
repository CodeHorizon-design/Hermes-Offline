# ─────────────────────────────────────────────────────────────────────────────
#  Hermes Offline — Windows Environment Diagnostic
#
#  Run this to check if everything is installed and working correctly:
#      powershell -ExecutionPolicy Bypass -File check-windows.ps1
#
#  Output: green = OK, yellow = warning, red = broken (with fix instructions)
# ─────────────────────────────────────────────────────────────────────────────

function Test-Command { param($n) return [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function OK   { param($m) Write-Host "  [OK]   $m" -ForegroundColor Green  }
function WARN { param($m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function FAIL { param($m) Write-Host "  [FAIL] $m" -ForegroundColor Red    }
function INFO { param($m) Write-Host "         $m" -ForegroundColor DarkGray }

Write-Host "`n  Hermes Offline — Windows Diagnostic`n  $('─'*40)" -ForegroundColor Cyan

# Python
$pyOk = $false
foreach ($cmd in @("py","python3","python")) {
    if (Test-Command $cmd) {
        $v = & $cmd --version 2>&1
        if ($v -match "Python (3\.1[1-3])") {
            OK "Python $($Matches[1]) ($cmd)"; $pyOk = $true; break
        }
    }
}
if (-not $pyOk) { FAIL "Python 3.11+ not found"; INFO "Fix: winget install Python.Python.3.12" }

# uv
if (Test-Command "uv") { OK "uv $(uv --version 2>&1)" }
else { WARN "uv not found (optional but faster)"; INFO "Fix: irm https://astral.sh/uv/install.ps1 | iex" }

# hermes-agent
if (Test-Command "hermes") { OK "hermes command found" }
else { FAIL "hermes not in PATH"; INFO "Fix: pip install hermes-agent  OR  re-run install-windows.bat" }

# hermes-offline
if (Test-Command "hermes-offline") { OK "hermes-offline command found" }
else {
    WARN "hermes-offline not in PATH"
    INFO "Fix: Add Python Scripts dir to PATH"
    if (Test-Command "python") {
        $sd = & python -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>$null
        if ($sd) { INFO "Run: [Environment]::SetEnvironmentVariable('PATH', `$env:PATH+';$sd', 'User')" }
    }
}

# hermes-offline Python import
try {
    $_ = & python -c "import hermes_offline; print(hermes_offline.__version__)" 2>&1
    if ($_ -match "^\d") { OK "hermes_offline package v$_ importable" }
    else { FAIL "hermes_offline import error: $_"; INFO "Fix: pip install -e ." }
} catch { FAIL "hermes_offline not importable" }

# Ollama
if (Test-Command "ollama") {
    OK "ollama command found ($(ollama --version 2>&1))"
    try {
        $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
        $models = $tags.models.name
        OK "Ollama API reachable — $($models.Count) model(s) installed"
        if ($models) { INFO ("Installed: " + ($models -join ", ")) }
        else {
            WARN "No models installed yet"
            INFO "Fix: ollama pull qwen3:4b   (for 8 GB RAM machines)"
        }
    } catch {
        WARN "Ollama not running"
        INFO "Fix: Start Ollama from the system tray, or: ollama serve"
    }
} else {
    FAIL "Ollama not installed"
    INFO "Fix: winget install Ollama.Ollama"
    INFO "     OR re-run install-windows.bat"
}

# Config
$cfg = "$env:USERPROFILE\.hermes\config.yaml"
if (Test-Path $cfg) {
    OK "Config found: $cfg"
    $content = Get-Content $cfg -Raw
    if ($content -match "ollama") { OK "Config references ollama provider" }
    else { WARN "Config may not use ollama-local provider"; INFO "Fix: run hermes-offline-setup" }
} else {
    WARN "No config at $cfg"
    INFO "Fix: run hermes-offline-setup"
}

# Rich (pretty output)
try {
    & python -c "import rich" 2>$null
    OK "rich package available (pretty output)"
} catch { WARN "rich not installed"; INFO "Fix: pip install rich" }

Write-Host "`n  $('─'*40)" -ForegroundColor Cyan
Write-Host "  Run 'hermes-offline' to start  |  're-run install-windows.bat' to fix issues`n"
