"""Utilidades mínimas para invocar ffmpeg / ffprobe."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache

from . import sysutil


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(f"No se encontró '{tool}' en el PATH. {sysutil.ffmpeg_install_hint()} "
                              "(comprueba tu instalación con: python -m videoeditor doctor)")


def filter_value(path: str) -> str:
    """Ruta lista para el valor de una opción de filtro de ffmpeg (`lut3d=file=...`,
    `drawtext=textfile=...`, `vidstabdetect=result=...`, `subtitles=...`).

    La cadena se interpreta dos veces: primero como grafo de filtros (donde `\\`, `'`, `,`, `;`,
    `[` y `]` son especiales) y luego como opciones del filtro (`\\`, `'` y `:`). Se escapa con
    barra invertida en ambos niveles, así valen rutas con espacios, comas, comillas, corchetes,
    tildes o dos puntos. Las barras invertidas de Windows pasan a `/` (ffmpeg las acepta) y
    `C:` queda como `C\\:` (el colon escapado)."""
    p = path.replace("\\", "/")
    p = re.sub(r"([\\':])", r"\\\1", p)
    return re.sub(r"([\\',;\[\]])", r"\\\1", p)


@lru_cache(maxsize=1)
def available_filters() -> frozenset[str]:
    """Nombres de filtros que esta build de ffmpeg incluye (`ffmpeg -filters`)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
    except OSError:
        return frozenset()
    names = set()
    for line in out.splitlines():
        m = re.match(r"^\s*[A-Z.|]{3}\s+(\S+)\s", line)
        if m:
            names.add(m.group(1))
    return frozenset(names)


def has_filter(name: str) -> bool:
    return name in available_filters()


@lru_cache(maxsize=1)
def available_encoders() -> frozenset[str]:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
    except OSError:
        return frozenset()
    return frozenset(m.group(1) for m in (re.match(r"^\s*[VAS][A-Z.]{5}\s+(\S+)", l) for l in out.splitlines()) if m)


def video_codec_args(crf: int, preset: str) -> list[str]:
    """Argumentos de codificación de video: libx264 si existe; si no, un respaldo con aviso."""
    enc = available_encoders()
    if "libx264" in enc or not enc:
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    if "libopenh264" in enc:
        sysutil.warn_once("esta build de FFmpeg no incluye libx264; se usa libopenh264 (menos calidad). "
                          "Instala la build 'full' (ver: python -m videoeditor doctor).")
        return ["-c:v", "libopenh264", "-b:v", "8M"]
    sysutil.warn_once("esta build de FFmpeg no incluye libx264; se usa mpeg4 (menos calidad). "
                      "Instala la build 'full' (ver: python -m videoeditor doctor).")
    return ["-c:v", "mpeg4", "-q:v", str(max(2, min(10, crf // 4)))]


@lru_cache(maxsize=1)
def has_fontconfig() -> bool:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-buildconf"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
    except OSError:
        return False
    return "libfontconfig" in out


def version() -> str:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
    except OSError:
        return ""
    return out.splitlines()[0] if out else ""


def run(args: list[str], verbose: bool = False) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "info" if verbose else "error", *args]
    if verbose:
        print("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=not verbose, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        err = proc.stderr if proc.stderr else ""
        raise FFmpegError(f"ffmpeg falló ({proc.returncode}):\n{' '.join(cmd)}\n{err[-4000:]}")


def capture(args: list[str]) -> bytes:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode(errors="replace")[-4000:])
    return proc.stdout


def capture_stderr(args: list[str]) -> str:
    """Ejecuta ffmpeg y devuelve su salida de diagnóstico (para filtros de medida como ebur128)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.stderr


def probe(path: str) -> dict:
    """Info de ffprobe. La caché depende también del tamaño y la fecha del archivo,
    así un archivo sobrescrito (p. ej. un render nuevo con el mismo nombre) se vuelve a leer."""
    try:
        st = os.stat(path)
        key = (st.st_size, st.st_mtime_ns)
    except OSError:
        key = (None, None)
    return _probe(path, *key)


def _clear_probe_cache() -> None:
    _probe.cache_clear()


probe.cache_clear = _clear_probe_cache  # compatibilidad con el código que limpia la caché


@lru_cache(maxsize=256)
def _probe(path: str, size, mtime) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"No se pudo leer '{path}': {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def duration(path: str) -> float:
    info = probe(path)
    d = info.get("format", {}).get("duration")
    if d is None:
        for s in info.get("streams", []):
            if "duration" in s:
                return float(s["duration"])
        return 0.0
    return float(d)


def has_audio(path: str) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def video_size(path: str) -> tuple[int, int]:
    for s in probe(path).get("streams", []):
        if s.get("codec_type") == "video":
            w, h = int(s["width"]), int(s["height"])
            rot = 0
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(sd["rotation"])
            if abs(rot) in (90, 270):
                w, h = h, w
            return w, h
    raise FFmpegError(f"'{path}' no tiene pista de video")
