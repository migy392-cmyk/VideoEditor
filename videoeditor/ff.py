"""Utilidades mínimas para invocar ffmpeg / ffprobe."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(f"No se encontró '{tool}' en el PATH. Instala FFmpeg.")


def run(args: list[str], verbose: bool = False) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "info" if verbose else "error", *args]
    if verbose:
        print("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=not verbose, text=True)
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
    proc = subprocess.run(cmd, capture_output=True, text=True)
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
    proc = subprocess.run(cmd, capture_output=True, text=True)
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
