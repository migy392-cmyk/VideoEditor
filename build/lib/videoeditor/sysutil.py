"""Utilidades que dependen del sistema operativo: carpetas de caché, fuentes y avisos."""
from __future__ import annotations

import os
import sys

APP = "videoeditor"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def cache_root() -> str:
    """Carpeta de caché propia de la plataforma.

    Windows: %LOCALAPPDATA%\\videoeditor\\Cache · macOS: ~/Library/Caches/videoeditor ·
    Linux: $XDG_CACHE_HOME o ~/.cache/videoeditor. Se puede forzar con VIDEOEDITOR_CACHE."""
    forced = os.environ.get("VIDEOEDITOR_CACHE")
    if forced:
        return forced
    home = os.path.expanduser("~")
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, APP, "Cache")
    if is_macos():
        return os.path.join(home, "Library", "Caches", APP)
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"), APP)


def cache_dir(*parts: str) -> str:
    d = os.path.join(cache_root(), *parts)
    os.makedirs(d, exist_ok=True)
    return d


# Fuentes negritas habituales (nada propietario se distribuye con el programa: se usan las del sistema).
FONT_FALLBACKS = [
    # Windows
    r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\calibrib.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/HelveticaNeue.ttc",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def fallback_font(candidates: list[str] | None = None) -> str | None:
    for p in (FONT_FALLBACKS if candidates is None else candidates):
        if os.path.isfile(p):
            return p
    return None


_warned: set[str] = set()


def warn_once(msg: str) -> None:
    """Aviso en español por stderr, una sola vez por mensaje."""
    if msg not in _warned:
        _warned.add(msg)
        print(f"Aviso: {msg}", file=sys.stderr)


def ffmpeg_install_hint() -> str:
    if is_windows():
        return ("Instálalo con 'winget install Gyan.FFmpeg' (o 'choco install ffmpeg-full'), "
                "o descarga la build 'full' de https://www.gyan.dev/ffmpeg/builds/ y añade su carpeta bin al PATH. "
                "Después abre una terminal nueva.")
    if is_macos():
        return "Instálalo con 'brew install ffmpeg'."
    return "Instálalo con 'sudo apt install ffmpeg' (o el gestor de tu distribución)."


def replace_file(src: str, dst: str, retries: int = 8) -> None:
    """os.replace con reintentos: en Windows falla con PermissionError si otro proceso
    (antivirus, navegador, ffmpeg) tiene el destino abierto en ese momento."""
    import time

    for i in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == retries - 1:
                raise
            time.sleep(0.1 * (i + 1))
