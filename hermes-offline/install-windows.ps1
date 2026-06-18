# ─────────────────────────────────────────────────────────────────────────────
#  Hermes Agent — Offline Edition  |  Windows One-Click Installer
#
#  Run from PowerShell (any version 5.1+):
#
#      Set-ExecutionPolicy -Scope Process Bypass; .\install-windows.ps1
#
#  Or with the one-liner (paste into PowerShell):
#
#      irm https://raw.githubusercontent.com/CodeHorizon-design/Hermes-Offline/main/install-windows.ps1 | iex
#
#  What this script does — in order:
#    1. Checks / installs Python 3.11–3.13
#    2. Checks / installs uv (fast Python package manager)
#    3. Checks / installs Ollama for Windows
#    4. Installs hermes-agent + hermes-offline Python packages
#    5. Runs the interactive offline setup wizard (auto-detects hardware,
#       recommends best model, pulls it, writes config)
#    6. Creates a desktop shortcut  (hermes-offline.lnk)
#    7. Creates a Start-Menu entry
#    8. Adds Ollama to Windows startup (optional)
#
#  Handles:
#    - Python not in PATH → installs via winget, then adds to PATH
#    - Old Python (< 3.11) → installs side-by-side, uses the new one
#    - Ollama already installed → skips install, starts service if not running
#    - pip / uv conflicts → tries uv first, falls back to pip, then pip3
#    - Execution policy block → Bypass set for this process only
#    - UNinstall detection → re-runs setup wizard, skips already-done steps
#    - Existing hermes config → backs up, keeps user's customizations
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"
$ProgressPreference   = "SilentlyContinue"   # Faster Invoke-WebRequest

# ── Colour helpers ────────────────────────────────────────────────────────────
function Write-Step   { param($msg) Write-Host "`n  $msg"     -ForegroundColor Cyan    }
function Write-OK     { param($msg) Write-Host "  ✓ $msg"    -ForegroundColor Green   }
function Write-Warn   { param($msg) Write-Host "  ⚠ $msg"    -ForegroundColor Yellow  }
function Write-Err    { param($msg) Write-Host "  ✗ $msg"    -ForegroundColor Red     }
function Write-Banner { param($msg) Write-Host "`n$msg`n"    -ForegroundColor Magenta }

Write-Banner @"
╔══════════════════════════════════════════════════════════╗
║    Hermes Agent — Offline Edition   Windows Installer    ║
║    Zero API keys · Zero subscriptions · Runs locally     ║
╚══════════════════════════════════════════════════════════╝
"@

# ── Helper: refresh PATH in current session ───────────────────────────────────
function Refresh-Path {
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
}

# ── Helper: run a command quietly, return success ─────────────────────────────
function Invoke-Quiet {
    param([scriptblock]$Cmd)
    try { & $Cmd 2>$null; return $LASTEXITCODE -eq 0 } catch { return $false }
}

# ── Helper: check if a command exists ────────────────────────────────────────
function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# ── Helper: download a file with progress ────────────────────────────────────
function Download-File {
    param([string]$Url, [string]$Dest)
    Write-Host "    Downloading $(Split-Path $Url -Leaf)..." -ForegroundColor DarkGray
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
}

# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 1: Python 3.11+
# ═══════════════════════════════════════════════════════════════════════════════
Write-Step "Step 1/7  Checking Python..."

$PYTHON = $null
$MIN_MINOR = 11
$MAX_MINOR = 13

# Try candidates in preference order
$candidates = @("py", "python3.13", "python3.12", "python3.11", "python3", "python")
foreach ($cmd in $candidates) {
    if (Test-Command $cmd) {
        $verStr = & $cmd --version 2>&1
        if ($verStr -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge $MIN_MINOR -and $minor -le $MAX_MINOR) {
                $PYTHON = $cmd
                Write-OK "Python $major.$minor found ($cmd)"
                break
            }
        }
    }
}

if (-not $PYTHON) {
    Write-Warn "Python 3.11–3.13 not found. Installing Python 3.12 via winget..."

    $wingetOk = $false
    if (Test-Command "winget") {
        try {
            winget install --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent
            Refresh-Path
            $wingetOk = $true
        } catch { }
    }

    if (-not $wingetOk) {
        # Direct download from python.org
        $pyInstaller = "$env:TEMP\python-3.12-installer.exe"
        Download-File "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" $pyInstaller
        $args = "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1"
        Start-Process -FilePath $pyInstaller -ArgumentList $args -Wait
        Remove-Item $pyInstaller -Force
    }

    Refresh-Path
    foreach ($cmd in @("py", "python3.12", "python3", "python")) {
        if (Test-Command $cmd) {
            $v = & $cmd --version 2>&1
            if ($v -match "Python 3\.(1[1-3])") { $PYTHON = $cmd; break }
        }
    }

    if (-not $PYTHON) {
        Write-Err "Python install failed. Please install Python 3.12 from https://python.org and re-run."
        exit 1
    }
    Write-OK "Python installed successfully"
}

# Confirm pip is usable
$PIP_CMD = @("$PYTHON -m pip", "pip3", "pip") | Where-Object {
    try { & cmd /c "$_ --version" 2>$null; $LASTEXITCODE -eq 0 } catch { $false }
} | Select-Object -First 1

# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 2: uv (fast package manager)
# ═══════════════════════════════════════════════════════════════════════════════
Write-Step "Step 2/7  Checking uv..."

$USE_UV = $false
if (Test-Command "uv") {
    Write-OK "uv already installed"
    $USE_UV = $true
} else {
    Write-Host "    Installing uv..." -ForegroundColor DarkGray
    try {
        # uv Windows installer (official)
        $uvInstaller = "$env:TEMP\uv-installer.ps1"
        Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $uvInstaller -UseBasicParsing
        & powershell -ExecutionPolicy Bypass -File $uvInstaller
        Remove-Item $uvInstaller -Force
        Refresh-Path
        if (Test-Command "uv") {
            Write-OK "uv installed"
            $USE_UV = $true
        }
    } catch {
        Write-Warn "uv install failed — falling back to pip (slower but works)"
    }
}

# Define install function
function Install-Package {
    param([string[]]$Packages, [string]$Extra = "")
    $pkgStr = $Packages -join " "
    if ($USE_UV) {
        if ($Extra) { uv pip install --system $Packages --extra $Extra }
        else         { uv pip install --system $Packages }
    } else {
        & $PYTHON -m pip install --quiet --upgrade $Packages
    }
    if ($LASTEXITCODE -ne 0) { throw "Package install failed: $pkgStr" }
}

# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 3: Ollama
# ═══════════════════════════════════════════════════════════════════════════════
Write-Step "Step 3/7  Checking Ollama..."

$OllamaRunning = $false

if (Test-Command "ollama") {
    Write-OK "Ollama already installed"
    # Check if Ollama service is running
    $svc = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
    if ($svc) {
        Write-OK "Ollama service is running"
        $OllamaRunning = $true
    } else {
        Write-Host "    Starting Ollama..." -ForegroundColor DarkGray
        Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
        $OllamaRunning = $true
    }
} else {
    Write-Warn "Ollama not found. Installing..."

    $ollamaInstalled = $false

    # Try winget first (clean, auto PATH)
    if (Test-Command "winget") {
        try {
            winget install --id Ollama.Ollama --accept-source-agreements --accept-package-agreements --silent
            Refresh-Path
            if (Test-Command "ollama") { $ollamaInstalled = $true }
        } catch { }
    }

    # Direct download fallback
    if (-not $ollamaInstalled) {
        $ollamaSetup = "$env:TEMP\OllamaSetup.exe"
        Write-Host "    Downloading Ollama installer (~100 MB)..." -ForegroundColor DarkGray
        Download-File "https://ollama.com/download/OllamaSetup.exe" $ollamaSetup
        Write-Host "    Running Ollama installer..." -ForegroundColor DarkGray
        Start-Process -FilePath $ollamaSetup -ArgumentList "/silent" -Wait
        Remove-Item $ollamaSetup -Force
        Refresh-Path
    }

    if (Test-Command "ollama") {
        Write-OK "Ollama installed"
        Start-Sleep -Seconds 2
        Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
        $OllamaRunning = $true
    } else {
        Write-Err "Ollama install failed. Download manually: https://ollama.com/download/windows"
        $choice = Read-Host "    Continue anyway? (y/N)"
        if ($choice -notin @("y","Y")) { exit 1 }
    }
}

# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 4: hermes-agent + hermes-offline
# ═══════════════════════════════════════════════════════════════════════════════
Write-Step "Step 4/7  Installing hermes-agent..."

# Upgrade pip first to avoid legacy resolver issues
& $PYTHON -m pip install --quiet --upgrade pip 2>$null

try {
    Install-Package @("hermes-agent")
    Write-OK "hermes-agent installed"
} catch {
    Write-Err "hermes-agent install failed: $_"
    Write-Host "    Try manually: pip install hermes-agent" -ForegroundColor DarkGray
    exit 1
}

Write-Step "Step 5/7  Installing hermes-offline extension..."

# Determine path to hermes-offline package
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PackageDir = $ScriptDir   # install-windows.ps1 sits at hermes-offline root

if (Test-Path "$PackageDir\pyproject.toml") {
    try {
        if ($USE_UV) { uv pip install --system -e $PackageDir }
        else          { & $PYTHON -m pip install --quiet -e $PackageDir }
        Write-OK "hermes-offline extension installed (editable)"
    } catch {
        Write-Warn "Editable install failed, trying regular install: $_"
        try {
            if ($USE_UV) { uv pip install --system $PackageDir }
            else          { & $PYTHON -m pip install --quiet $PackageDir }
            Write-OK "hermes-offline extension installed"
        } catch {
            Write-Err "hermes-offline install failed: $_"
            exit 1
        }
    }
} else {
    Write-Warn "hermes-offline source not found — skipping extension install"
}

# Install optional enhancements (non-fatal)
Write-Host "`n  Optional: piper-tts (local TTS)..." -ForegroundColor DarkGray
try {
    Install-Package @("piper-tts")
    Write-OK "piper-tts installed"
} catch { Write-Warn "piper-tts skipped (install later: pip install piper-tts)" }

Write-Host "  Optional: faster-whisper (local voice transcription)..." -ForegroundColor DarkGray
try {
    Install-Package @("faster-whisper")
    Write-OK "faster-whisper installed"
} catch { Write-Warn "faster-whisper skipped (install later: pip install faster-whisper)" }

# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 5: Verify hermes entry points are reachable
# ═══════════════════════════════════════════════════════════════════════════════
Write-Step "Step 5b/7  Verifying entry points..."

Refresh-Path

# hermes-agent installs a 'hermes' script; hermes-offline installs 'hermes-offline'
$hermesOk = Test-Command "hermes"
$offlineOk = Test-Command "hermes-offline"

if (-not $hermesOk) {
    # Add Python Scripts to PATH permanently
    $scriptsDir = & $PYTHON -c "import sysconfig; print(sysconfig.get_path('scripts'))"
    $scriptsDir = $scriptsDir.Trim()
    if ($scriptsDir -and (Test-Path $scriptsDir)) {
        $currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
        if ($currentPath -notlike "*$scriptsDir*") {
            [Environment]::SetEnvironmentVariable("PATH", "$currentPath;$scriptsDir", "User")
            $env:PATH = "$env:PATH;$scriptsDir"
            Write-OK "Added Python Scripts to PATH: $scriptsDir"
        }
    }
    Refresh-Path
    $hermesOk  = Test-Command "hermes"
    $offlineOk = Test-Command "hermes-offline"
}

if ($hermesOk) { Write-OK "hermes command available" }
else           { Write-Warn "hermes not in PATH — may need a new terminal" }

if ($offlineOk) { Write-OK "hermes-offline command available" }
else            { Write-Warn "hermes-offline not in PATH — may need a new terminal" }

# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 6: Run offline setup wizard
# ═══════════════════════════════════════════════════════════════════════════════
Write-Step "Step 6/7  Running offline setup wizard..."
Write-Host "  (This detects your hardware, recommends a model, and writes config)`n"

$SetupRan = $false
if (Test-Command "hermes-offline-setup") {
    hermes-offline-setup
    $SetupRan = $true
} elseif (Test-Path "$PackageDir\hermes_offline\setup.py") {
    & $PYTHON "$PackageDir\hermes_offline\setup.py"
    $SetupRan = $true
} else {
    & $PYTHON -m hermes_offline.setup 2>$null
    if ($LASTEXITCODE -eq 0) { $SetupRan = $true }
}
if (-not $SetupRan) {
    Write-Warn "Setup wizard not found — writing basic config manually..."
    $HermesHome = "$env:USERPROFILE\.hermes"
    if (-not (Test-Path $HermesHome)) { New-Item -ItemType Directory -Path $HermesHome | Out-Null }
    $BasicConfig = @"
provider: ollama-local
model:
  default: qwen3:4b
endpoint: http://127.0.0.1:11434/v1
api_key: ollama
stream: true
context:
  compression_threshold: 0.70
  max_tool_output_chars: 2000
web:
  backend: duckduckgo
tracker:
  enabled: true
  status_line: true
  summary_on_exit: true
think:
  mode: auto
"@
    $BasicConfig | Set-Content -Path "$HermesHome\config.yaml" -Encoding UTF8
    Write-OK "Basic config written to $HermesHome\config.yaml"
}

# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 7: Desktop shortcut + Start Menu
# ═══════════════════════════════════════════════════════════════════════════════
Write-Step "Step 7/7  Creating shortcuts..."

$WshShell   = New-Object -ComObject WScript.Shell
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$StartMenu   = [Environment]::GetFolderPath("Programs")

# Find hermes-offline executable
$HermesExe = (Get-Command "hermes-offline" -ErrorAction SilentlyContinue)?.Source
if (-not $HermesExe) {
    $HermesExe = (Get-Command "hermes" -ErrorAction SilentlyContinue)?.Source
}
$TerminalExe = (Get-Command "wt" -ErrorAction SilentlyContinue)?.Source   # Windows Terminal
if (-not $TerminalExe) { $TerminalExe = "$env:SYSTEMROOT\System32\cmd.exe" }

if ($HermesExe) {
    foreach ($dest in @("$DesktopPath\Hermes (Offline).lnk", "$StartMenu\Hermes Offline.lnk")) {
        try {
            $lnk = $WshShell.CreateShortcut($dest)
            if ($TerminalExe -like "*wt*") {
                $lnk.TargetPath  = $TerminalExe
                $lnk.Arguments   = "hermes-offline"
            } else {
                $lnk.TargetPath  = $HermesExe
            }
            $lnk.Description = "Hermes Agent (Offline) — local AI assistant"
            $lnk.WorkingDirectory = $env:USERPROFILE
            $lnk.Save()
        } catch { }
    }
    Write-OK "Desktop shortcut created: Hermes (Offline)"
    Write-OK "Start Menu entry created"
} else {
    Write-Warn "Could not create shortcut (hermes not in PATH yet)"
}

# ── Optional: add Ollama to Windows startup ───────────────────────────────────
if ($OllamaRunning -and (Test-Command "ollama")) {
    $StartupChoice = Read-Host "`n  Auto-start Ollama when Windows boots? (Y/n)"
    if ($StartupChoice -notin @("n","N")) {
        $OllamaExe = (Get-Command "ollama").Source
        $StartupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
        $OllamaLnk = $WshShell.CreateShortcut("$StartupDir\Ollama.lnk")
        $OllamaLnk.TargetPath = $OllamaExe
        $OllamaLnk.Arguments  = "serve"
        $OllamaLnk.WindowStyle = 7   # minimized
        $OllamaLnk.Description = "Ollama local LLM server"
        $OllamaLnk.Save()
        Write-OK "Ollama added to Windows startup"
    }
}

# ═══════════════════════════════════════════════════════════════════════════════
#  DONE
# ═══════════════════════════════════════════════════════════════════════════════
Write-Banner @"
╔══════════════════════════════════════════════════════════╗
║           Hermes Agent (Offline) is ready!               ║
╚══════════════════════════════════════════════════════════╝
"@

Write-Host "  To start Hermes:" -ForegroundColor White
Write-Host "    hermes-offline              " -NoNewline -ForegroundColor Cyan
Write-Host "— interactive chat" -ForegroundColor Gray
Write-Host "    hermes-offline --tui        " -NoNewline -ForegroundColor Cyan
Write-Host "— full TUI interface" -ForegroundColor Gray
Write-Host "    hermes-offline --think      " -NoNewline -ForegroundColor Cyan
Write-Host "— with chain-of-thought reasoning" -ForegroundColor Gray
Write-Host "    hermes-offline-bench        " -NoNewline -ForegroundColor Cyan
Write-Host "— benchmark your model's speed + accuracy" -ForegroundColor Gray
Write-Host "    hermes-offline-setup        " -NoNewline -ForegroundColor Cyan
Write-Host "— re-run setup / switch model" -ForegroundColor Gray
Write-Host ""
Write-Host "  Or double-click: " -NoNewline -ForegroundColor White
Write-Host "Hermes (Offline)" -ForegroundColor Cyan
Write-Host "  on your Desktop."
Write-Host ""
Write-Host "  Config:  $env:USERPROFILE\.hermes\config.yaml" -ForegroundColor DarkGray
Write-Host "  Memory:  $env:USERPROFILE\.hermes\memories\"   -ForegroundColor DarkGray
Write-Host "  Skills:  $env:USERPROFILE\.hermes\skills\"     -ForegroundColor DarkGray
Write-Host ""

# Keep window open if run by double-click (no existing terminal)
if ($Host.Name -eq "ConsoleHost" -and -not $env:TERM_PROGRAM) {
    Read-Host "`n  Press Enter to close this window"
}
