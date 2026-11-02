param()

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Test-Cmd($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Find-Exe($patterns) {
    foreach ($p in $patterns) {
        $found = Get-ChildItem -Path $p -Recurse -Filter "*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    # try PATH
    $cmd = Get-Command (Split-Path -Leaf $patterns[0]) -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

# --- Python ---
if (-not (Test-Cmd "python")) {
    if (Test-Cmd "winget") {
        Write-Host "Python chybi, instaluji pres winget..."
        winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        # refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    } else {
        Write-Host "Python neni nainstalovan. Nainstalujte z https://www.python.org/"
        Read-Host "Stisknete Enter pro ukonceni"
        exit 1
    }
}
if (-not (Test-Cmd "python")) {
    Write-Host "Python stale neni v PATH. Restartujte terminal/PC a spustte znovu."
    Read-Host "Stisknete Enter pro ukonceni"
    exit 1
}

# --- Flask ---
python -c "import flask" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instaluji Flask..."
    python -m pip install --upgrade pip
    python -m pip install flask
}

# --- MakeMKV ---
$mkv = Find-Exe @("C:\Program Files (x86)\MakeMKV","C:\Program Files\MakeMKV","$env:LOCALAPPDATA\Microsoft\WinGet\Packages")
if (-not $mkv -or (Split-Path -Leaf $mkv) -notmatch "makemkv") {
    # try winget
    $mkv = (Get-ChildItem -Path "C:\","$env:LOCALAPPDATA" -Recurse -Filter "makemkvcon64.exe" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
}
if (-not $mkv) {
    if (Test-Cmd "winget") {
        Write-Host "MakeMKV chybi, instaluji pres winget..."
        winget install --id GuinpinSoft.MakeMKV --silent --accept-package-agreements --accept-source-agreements
        $mkv = (Get-ChildItem -Path "C:\","$env:LOCALAPPDATA" -Recurse -Filter "makemkvcon64.exe" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
    }
}
if (-not $mkv) {
    Write-Host "MakeMKV nenalezeno. Nainstalujte rucne z https://www.makemkv.com/"
}

# --- HandBrake CLI ---
$hb = $null
$hb = (Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages","C:\Program Files","C:\Program Files (x86)" -Recurse -Filter "HandBrakeCLI.exe" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
if (-not $hb) {
    if (Test-Cmd "winget") {
        Write-Host "HandBrake CLI chybi, instaluji pres winget..."
        winget install --id HandBrake.HandBrake.CLI --silent --accept-package-agreements --accept-source-agreements
        $hb = (Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages","C:\Program Files","C:\Program Files (x86)" -Recurse -Filter "HandBrakeCLI.exe" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
    }
}
if (-not $hb) {
    Write-Host "HandBrake CLI nenalezeno. Nainstalujte rucne."
}

# --- Update config.json ---
$configPath = Join-Path $PSScriptRoot "config.json"
$config = @{}
if (Test-Path $configPath) {
    try {
        $obj = Get-Content $configPath -Raw | ConvertFrom-Json
        foreach ($p in $obj.PSObject.Properties) { $config[$p.Name] = $p.Value }
    } catch { $config = @{} }
}
if ($mkv) { $config["makemkv"] = $mkv }
if ($hb) { $config["handbrake"] = $hb }
if (-not $config.ContainsKey("dynamic_minlength")) { $config["dynamic_minlength"] = $true }
$config | ConvertTo-Json -Depth 5 | Set-Content -Path $configPath -Encoding UTF8

Write-Host "Spoustim DVD AutoRip na http://localhost:8080"
python (Join-Path $PSScriptRoot "app.py")
Read-Host "Stisknete Enter pro ukonceni"