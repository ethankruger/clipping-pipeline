# setup.ps1 — One-click Windows installer for Clipping Pipeline
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

Write-Host "`n[1/5] Installing Python, FFmpeg, Ollama, and Docker Desktop via winget..." -ForegroundColor Cyan
$pkgs = @("Python.Python.3.12", "Gyan.FFmpeg", "Ollama.Ollama", "Docker.DockerDesktop")
foreach ($id in $pkgs) {
    winget install -e --id $id --accept-source-agreements --accept-package-agreements --silent
}

# Refresh PATH in current terminal
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

Write-Host "`n[2/5] Creating Python virtual environment and installing packages..." -ForegroundColor Cyan
python -m venv "$Root\.venv"
& "$Root\.venv\Scripts\pip.exe" install --upgrade pip
& "$Root\.venv\Scripts\pip.exe" install -r "$Root\requirements.txt"

Write-Host "`n[3/5] Pulling local Ollama scoring models (qwen2.5:7b and qwen2.5:3b)..." -ForegroundColor Cyan
ollama pull qwen2.5:7b
ollama pull qwen2.5:3b

Write-Host "`n[4/5] Creating local .env and config.json from templates..." -ForegroundColor Cyan
if (!(Test-Path "$Root\.env")) {
    $token = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
    (Get-Content "$Root\.env.example") -replace "YOUR_WORKER_TOKEN_HERE", $token | Set-Content "$Root\.env" -Encoding UTF8
    Write-Host "  -> Created .env with a fresh WORKER_TOKEN." -ForegroundColor Green
}
if (!(Test-Path "$Root\config.json")) {
    Copy-Item "$Root\config.example.json" "$Root\config.json"
    Write-Host "  -> Created config.json from config.example.json." -ForegroundColor Green
}

Write-Host "`n[5/5] Creating Desktop shortcut: Clipping Control Panel..." -ForegroundColor Cyan
$desktop = [Environment]::GetFolderPath("Desktop")
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut("$desktop\Clipping Control Panel.lnk")
$sc.TargetPath = "$Root\.venv\Scripts\pythonw.exe"
$sc.Arguments = "`"$Root\panel.py`""
$sc.WorkingDirectory = $Root
$sc.Save()

Write-Host "`nSetup complete!" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Open .env and fill in your Twitch, Gemini, Buffer, and S3/R2 keys."
Write-Host "  2. Open config.json and update your creators, Buffer channel IDs, and timezone."
Write-Host "  3. Start Docker Desktop, then run: docker compose up -d"
Write-Host "  4. Open http://localhost:5678, import workflows\clipping-pipeline.json, paste your WORKER_TOKEN into the HTTP nodes, and toggle Active."
Write-Host "  5. Double-click 'Clipping Control Panel' on your Desktop and click Start Worker."
