"""`python -m videoeditor doctor`: comprueba el entorno y explica cómo arreglar lo que falte."""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile

from . import ff, sysutil

OK, WARN, FAIL = "OK", "AVISO", "FALTA"

# filtro -> (qué se pierde sin él, ¿imprescindible?)
FILTERS = {
    "libx264": ("la codificación H.264 de calidad (se usa un respaldo peor)", False),
    "zscale": ("la conversión de videos HDR de iPhone a SDR (saldrían lavados)", False),
    "vidstab": ("el efecto 'stabilize'", False),
    "drawtext": ("los textos sobre el video", False),
    "lut3d": ("los LUTs de color", True),
    "zoompan": ("los zooms y efectos de golpe", True),
    "xfade": ("las transiciones", True),
}

FFMPEG_FIX = {
    "windows": "winget install Gyan.FFmpeg   (o: choco install ffmpeg-full; o descarga la build 'full' de "
               "https://www.gyan.dev/ffmpeg/builds/ y añade su carpeta bin al PATH). Abre una terminal nueva.",
    "macos": "brew install ffmpeg",
    "linux": "sudo apt install ffmpeg   (Fedora: sudo dnf install ffmpeg; Arch: sudo pacman -S ffmpeg)",
}
FULL_BUILD_FIX = {
    "windows": "Usa la build 'full' (no 'essentials'): winget install Gyan.FFmpeg o choco install ffmpeg-full.",
    "macos": "brew install ffmpeg (la fórmula de Homebrew incluye todo). Si la instalaste de otra forma, reinstala con brew.",
    "linux": "sudo apt install ffmpeg (Debian/Ubuntu lo incluye). Evita builds estáticas recortadas.",
}
MEDIAPIPE_FIX = {
    "windows": "pip install -r requirements-extra.txt",
    "macos": "pip install -r requirements-extra.txt",
    "linux": "pip install -r requirements-extra.txt  y  sudo apt install libegl1 libgles2",
}
TESSERACT_FIX = {
    "windows": "winget install UB-Mannheim.TesseractOCR  (y añade su carpeta al PATH)",
    "macos": "brew install tesseract tesseract-lang",
    "linux": "sudo apt install tesseract-ocr tesseract-ocr-spa",
}


def _os_key() -> str:
    return "windows" if sysutil.is_windows() else "macos" if sysutil.is_macos() else "linux"


def _ffmpeg_version_ok(first_line: str) -> bool:
    m = re.search(r"version\s+[nN]?(\d+)", first_line)
    return not m or int(m.group(1)) >= 4 or "git" in first_line or "N-" in first_line


def run_checks(workspace: str) -> list[tuple[str, str, str, str]]:
    """Lista de (estado, nombre, detalle, cómo arreglarlo)."""
    osk = _os_key()
    res: list[tuple[str, str, str, str]] = []

    v = sys.version_info
    if v >= (3, 10):
        res.append((OK, "Python", f"{v.major}.{v.minor}.{v.micro}", ""))
    else:
        res.append((FAIL, "Python", f"{v.major}.{v.minor}.{v.micro} (hace falta 3.10 o superior)",
                    "Instala Python 3.10+ desde https://www.python.org/downloads/"))

    try:
        import numpy
        res.append((OK, "numpy", numpy.__version__, ""))
    except ImportError:
        res.append((FAIL, "numpy", "no instalado", "pip install -r requirements.txt"))

    have_ff = True
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if not path:
            have_ff = False
            res.append((FAIL, tool, "no está en el PATH", FFMPEG_FIX[osk]))
        else:
            res.append((OK, tool, path, ""))
    if have_ff:
        ver = ff.version()
        ok = _ffmpeg_version_ok(ver)
        res.append((OK if ok else WARN, "Versión de FFmpeg", ver or "desconocida",
                    "" if ok else "Actualiza FFmpeg a la versión 5 o superior."))
        filters = ff.available_filters()
        encoders = ff.available_encoders()
        for name, (lost, required) in FILTERS.items():
            if name == "libx264":
                present = "libx264" in encoders
            elif name == "vidstab":
                present = "vidstabdetect" in filters and "vidstabtransform" in filters
            else:
                present = name in filters
            if present:
                res.append((OK, f"Filtro/codec {name}", "disponible", ""))
            else:
                res.append((FAIL if required else WARN, f"Filtro/codec {name}",
                            f"no disponible: sin él se pierde {lost}", FULL_BUILD_FIX[osk]))

    from . import segment
    if segment.available():
        res.append((OK, "mediapipe (opcional)", "disponible: transición 'subject_drop' con recorte de persona", ""))
    else:
        res.append((WARN, "mediapipe (opcional)",
                    "no disponible: 'subject_drop' se sustituye por un deslizamiento normal", MEDIAPIPE_FIX[osk]))

    tess = shutil.which("tesseract")
    if tess:
        res.append((OK, "tesseract (opcional)", tess, ""))
    else:
        res.append((WARN, "tesseract (opcional)", "no disponible: no se leen los textos de las referencias",
                    TESSERACT_FIX[osk]))

    try:
        os.makedirs(workspace, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=workspace, prefix=".doctor_", delete=True):
            pass
        res.append((OK, "Carpeta de trabajo", f"{os.path.abspath(workspace)} (se puede escribir)", ""))
    except OSError as e:
        res.append((FAIL, "Carpeta de trabajo", f"{os.path.abspath(workspace)}: {e}",
                    "Usa otra con: python -m videoeditor -w RUTA serve (o la variable VIDEOEDITOR_WORKSPACE)"))
    try:
        res.append((OK, "Carpeta de caché", sysutil.cache_dir(), ""))
    except OSError as e:
        res.append((WARN, "Carpeta de caché", str(e), "Define VIDEOEDITOR_CACHE con una carpeta escribible."))

    font = None
    try:
        from .render import _font_file
        font = _font_file("Sans:style=Bold")
    except Exception:
        pass
    if font:
        res.append((OK, "Fuente para textos", font, ""))
    else:
        res.append((WARN, "Fuente para textos", "no se encontró ninguna fuente del sistema",
                    "Instala una fuente (p. ej. DejaVu) o usa la ruta de un .ttf en el campo 'font' del texto."))
    return res


def main(workspace: str) -> int:
    print(f"VideoEditor doctor · {sys.platform} · Python {sys.version.split()[0]}\n")
    checks = run_checks(workspace)
    marks = {OK: "[ OK  ]", WARN: "[AVISO]", FAIL: "[FALTA]"}
    for state, name, detail, fix in checks:
        print(f"{marks[state]} {name}: {detail}")
        if fix and state != OK:
            print(f"         Cómo arreglarlo: {fix}")
    fails = sum(1 for c in checks if c[0] == FAIL)
    warns = sum(1 for c in checks if c[0] == WARN)
    print()
    if fails:
        print(f"Resultado: {fails} problema(s) que impiden usar el editor y {warns} aviso(s).")
        return 1
    extra = f" ({warns} aviso(s) sobre funciones opcionales)" if warns else ""
    print("Resultado: todo lo imprescindible está listo" + extra + ".")
    return 0
