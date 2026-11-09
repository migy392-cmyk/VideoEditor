# Instalador de VideoEditor para Windows 10/11.
#
# Uso (PowerShell, sin permisos de administrador):
#   irm https://raw.githubusercontent.com/migy392-cmyk/VideoEditor/claude/serene-brown-fggcdl/scripts/instalar_windows.ps1 | iex
# o, si ya tienes el proyecto descargado y descomprimido, doble clic en INSTALAR_WINDOWS.bat
#
# Que hace (se puede ejecutar varias veces; lo que ya esta instalado se salta):
#   1. Instala Python 3.12, Git y FFmpeg (version "full") con winget.
#   2. Descarga el proyecto en C:\VideoEditor (o lo actualiza si ya existe).
#   3. Crea el entorno de Python (.venv) e instala las librerias.
#   4. Comprueba la instalacion (python -m videoeditor doctor).
#   5. Crea "Abrir VideoEditor.bat" y un acceso directo en el escritorio.
#
# Variables opcionales (solo si lo lanzas desde PowerShell con irm ... | iex):
#   $env:VE_DIR    carpeta de instalacion (por defecto C:\VideoEditor)
#   $env:VE_BRANCH rama de GitHub (por defecto claude/serene-brown-fggcdl)

# Todo va dentro de un bloque & { } para no dejar variables ni ajustes en tu ventana de PowerShell.
& {
# 'Continue': en Windows PowerShell 5.1, con 'Stop' cualquier texto que un programa escriba
# en stderr (git lo hace siempre) se convierte en error. Se comprueba $LASTEXITCODE en su lugar.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$env:GIT_TERMINAL_PROMPT = '0'   # nunca pedir usuario/contrasena de GitHub (el repositorio es publico)

$RepoUrl = 'https://github.com/migy392-cmyk/VideoEditor.git'
$Branch  = if ($env:VE_BRANCH) { $env:VE_BRANCH } else { 'claude/serene-brown-fggcdl' }
$Dest    = if ($env:VE_DIR)    { $env:VE_DIR }    else { 'C:\VideoEditor' }
$FfmpegBin = $null

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    AVISO  $msg" -ForegroundColor Yellow }
function Stop-Install($msg) {
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    Write-Host "La instalacion se ha detenido. Puedes volver a ejecutar el instalador cuando lo soluciones."
    throw $msg
}

function Update-SessionPath {
    # winget anade programas al PATH del sistema/usuario, pero esta ventana no lo ve hasta
    # que se vuelve a abrir: lo recargamos aqui. Tambien se incluye la carpeta de enlaces de
    # winget, donde quedan los programas "portables" como FFmpeg.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $links   = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
    $env:Path = (@($machine, $user, $links) | Where-Object { $_ }) -join ';'
}

function Test-Command($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Install-WithWinget($id, $label, $probe) {
    Write-Host "    Instalando $label (puede tardar unos minutos)..."
    Write-Host "    Si Windows pide permiso (ventana 'Desea permitir...'), pulsa Si. Puede aparecer detras de esta ventana." -ForegroundColor Yellow
    & winget install --id $id --exact --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    Update-SessionPath
    # Versiones antiguas de winget no conocen --disable-interactivity: se reintenta sin esa opcion.
    # (winget tambien devuelve un codigo distinto de 0 cuando el programa ya estaba instalado.)
    if ($LASTEXITCODE -ne 0 -and -not (Test-Command $probe)) {
        & winget install --id $id --exact --source winget --silent --accept-package-agreements --accept-source-agreements
        Update-SessionPath
    }
}

function Get-PythonCmd {
    # Devuelve el comando para ejecutar Python 3.10+ como lista: @('py','-3.12') o @('C:\...\python.exe').
    # Se prefiere 3.12: es la version con mejor soporte de MediaPipe (recorte de personas).
    if (Test-Command 'py') {
        foreach ($v in @('3.12', '3.11', '3.13', '3.10')) {
            & py "-$v" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return ,@('py', "-$v") }
        }
    }
    if (Test-Command 'python') {
        # "python" puede ser el atajo de la Microsoft Store que no hace nada: comprobamos la version.
        $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -and ([version]$ver -ge [version]'3.10')) { return ,@('python') }
    }
    # Instalado por winget pero sin anadir al PATH: se busca en las carpetas habituales.
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                     "$env:ProgramFiles\Python312\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe")) {
        if (Test-Path $p) { return ,@($p) }
    }
    return $null
}

Write-Host "Instalador de VideoEditor" -ForegroundColor White
Write-Host "Carpeta: $Dest   -   Rama: $Branch"

# ---------------------------------------------------------------- 1. Programas
Write-Step "1/5  Programas necesarios (Python, Git, FFmpeg)"
if (-not (Test-Command 'winget')) {
    Stop-Install ("No se encuentra 'winget'. Instala 'App Installer' desde la Microsoft Store " +
                  "(o actualiza Windows) y vuelve a ejecutar este instalador.")
}
Update-SessionPath

$py = Get-PythonCmd
if ($py) { Write-Ok "Python ya instalado ($($py -join ' '))" }
else {
    Install-WithWinget 'Python.Python.3.12' 'Python 3.12' 'py'
    $py = Get-PythonCmd
    if (-not $py) { Stop-Install "Python no aparece tras instalarlo. Cierra esta ventana, abre otra de PowerShell y vuelve a ejecutar el instalador." }
    Write-Ok "Python instalado"
}

if (Test-Command 'git') { Write-Ok "Git ya instalado" }
else {
    Install-WithWinget 'Git.Git' 'Git' 'git'
    if (-not (Test-Command 'git')) {
        $gitDir = 'C:\Program Files\Git\cmd'
        if (Test-Path (Join-Path $gitDir 'git.exe')) { $env:Path = "$gitDir;$env:Path" }
    }
    if (-not (Test-Command 'git')) { Stop-Install "Git no aparece tras instalarlo. Cierra esta ventana, abre otra y vuelve a ejecutar el instalador." }
    Write-Ok "Git instalado"
}

if ((Test-Command 'ffmpeg') -and (Test-Command 'ffprobe')) { Write-Ok "FFmpeg ya instalado" }
else {
    Install-WithWinget 'Gyan.FFmpeg' 'FFmpeg (version full)' 'ffmpeg'
    if (-not ((Test-Command 'ffmpeg') -and (Test-Command 'ffprobe'))) {
        # Ultimo recurso: buscar el ffmpeg.exe que haya dejado winget (solo para esta ventana;
        # "Abrir VideoEditor.bat" lo vuelve a anadir si hace falta).
        $found = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $env:Path = "$($found.DirectoryName);$env:Path"; $FfmpegBin = $found.DirectoryName }
    }
    if (-not (Test-Command 'ffmpeg')) { Stop-Install "FFmpeg no aparece tras instalarlo. Cierra esta ventana, abre otra y vuelve a ejecutar el instalador." }
    Write-Ok "FFmpeg instalado"
}

# ---------------------------------------------------------------- 2. Proyecto
Write-Step "2/5  Proyecto en $Dest"
if (Test-Path (Join-Path $Dest '.git')) {
    Write-Host "    Ya existe: actualizando..."
    & git -C $Dest fetch origin $Branch
    if ($LASTEXITCODE -ne 0) { Stop-Install "No se pudo descargar la actualizacion (hay conexion a internet?)." }
    & git -C $Dest checkout $Branch
    & git -C $Dest pull --ff-only origin $Branch
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "No se pudo actualizar automaticamente (has modificado archivos del programa?). Se usa la version que ya tenias."
    } else { Write-Ok "Actualizado" }
} elseif ((Test-Path $Dest) -and (Get-ChildItem $Dest -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    Stop-Install ("La carpeta $Dest ya existe y no esta vacia (y no es una instalacion de VideoEditor). " +
                  "Muevela o borrala, o (desde PowerShell, con el comando irm ... | iex) elige otra carpeta con " +
                  "`$env:VE_DIR = 'D:\VideoEditor' antes de lanzarlo.")
} else {
    & git clone --branch $Branch $RepoUrl $Dest
    if ($LASTEXITCODE -ne 0) {
        Stop-Install ("No se pudo descargar el proyecto. Si no tienes permiso para crear carpetas en C:\, " +
                      "lanza el instalador desde PowerShell con: `$env:VE_DIR = `"$env:USERPROFILE\VideoEditor`"; irm ... | iex")
    }
    Write-Ok "Descargado"
}

# ---------------------------------------------------------------- 3. Entorno de Python
Write-Step "3/5  Entorno de Python y librerias"
$venvPy = Join-Path $Dest '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    $py = @($py)
    $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
    & $pyExe @pyArgs -m venv (Join-Path $Dest '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Stop-Install "No se pudo crear el entorno de Python." }
}
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPy -m pip install -r (Join-Path $Dest 'requirements-extra.txt') --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "No se pudo instalar MediaPipe (recorte de personas). Se instala lo imprescindible; esa transicion sera un deslizamiento normal."
    & $venvPy -m pip install -r (Join-Path $Dest 'requirements.txt') --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Stop-Install "No se pudieron instalar las librerias de Python (hay conexion a internet?)." }
}
Write-Ok "Librerias instaladas"

# ---------------------------------------------------------------- 4. Comprobacion
Write-Step "4/5  Comprobando la instalacion"
Push-Location $Dest
try {
    & $venvPy -m videoeditor doctor
    $doctorOk = ($LASTEXITCODE -eq 0)
} finally { Pop-Location }
if (-not $doctorOk) { Write-Warn2 "La comprobacion encontro algo imprescindible que falta (mira la lista de arriba)." }

# ---------------------------------------------------------------- 5. Accesos directos
Write-Step "5/5  Accesos directos"
$launcher = Join-Path $Dest 'Abrir VideoEditor.bat'
$extraPath = if ($FfmpegBin) { "set `"PATH=$($FfmpegBin);%PATH%`"" } else { 'rem' }
$bat = @"
@echo off
title VideoEditor
cd /d "%~dp0"
$extraPath
echo Abriendo VideoEditor en el navegador... (cierra esta ventana o pulsa Ctrl+C para salir)
".venv\Scripts\python.exe" -m videoeditor serve
pause
"@
Set-Content -Path $launcher -Value $bat -Encoding ASCII
Write-Ok "Creado $launcher"

try {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut((Join-Path $desktop 'VideoEditor.lnk'))
    $lnk.TargetPath = $launcher
    $lnk.WorkingDirectory = $Dest
    $lnk.Description = 'Abrir VideoEditor'
    $lnk.Save()
    Write-Ok "Acceso directo 'VideoEditor' en el escritorio"
} catch {
    Write-Warn2 "No se pudo crear el acceso directo del escritorio; usa $launcher"
}

Write-Host ""
Write-Host "Listo." -ForegroundColor Green
Write-Host "Para empezar: doble clic en 'VideoEditor' en el escritorio (o en $launcher)."
Write-Host "Se abrira el navegador en http://127.0.0.1:8765"
Write-Host "Tus clips, estilos y videos se guardan en $Dest\workspace"
}
