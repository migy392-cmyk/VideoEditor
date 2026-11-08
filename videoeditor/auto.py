"""Edición automática: clips + canción + plantilla → proyecto JSON sincronizado al beat."""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from . import ff
from .beats import BeatInfo, detect


# --------------------------------------------------------------------------- #
# Selección del mejor fragmento de cada clip (más movimiento)
# --------------------------------------------------------------------------- #

def motion_curve(path: str, fps: float = 6.0) -> tuple[np.ndarray, float]:
    """Energía de movimiento por frame (muestreado a `fps`)."""
    w, h = 32, 56
    raw = ff.capture(["-i", path, "-an", "-vf", f"fps={fps},scale={w}:{h},format=gray",
                      "-f", "rawvideo", "-"])
    frames = np.frombuffer(raw, dtype=np.uint8)
    n = len(frames) // (w * h)
    if n < 2:
        return np.zeros(1), fps
    frames = frames[: n * w * h].reshape(n, h, w).astype(np.float32)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    return np.concatenate([[diff[0]], diff]), fps


def best_inpoint(path: str, length: float, skip_edges: float = 0.3) -> float:
    """Inicio del tramo de `length` segundos con más movimiento."""
    dur = ff.duration(path)
    if dur <= length + 0.1:
        return 0.0
    try:
        curve, fps = motion_curve(path)
    except ff.FFmpegError:
        return max(0.0, (dur - length) / 2)
    win = max(1, int(round(length * fps)))
    if len(curve) <= win:
        return 0.0
    sums = np.convolve(curve, np.ones(win), mode="valid")
    lo = int(skip_edges * fps)
    hi = max(lo + 1, len(sums) - int(skip_edges * fps))
    i = lo + int(np.argmax(sums[lo:hi])) if hi > lo else int(np.argmax(sums))
    return round(float(min(i / fps, dur - length)), 3)


# --------------------------------------------------------------------------- #
# Plantillas
# --------------------------------------------------------------------------- #

@dataclass
class AutoOptions:
    width: int | None = None   # None: el del estilo, o 1080x1920
    height: int | None = None
    fps: int = 30
    grade: str | None = None   # None: night_blue en plantillas, sin grade en estilos aprendidos
    max_duration: float | None = None
    music_start: float = 0.0
    smart_inpoints: bool = True


def _rel(path: str, base: str) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def _beat_grid(info: BeatInfo, needed_beats: int) -> list[float]:
    """Rejilla de beats; si la canción es corta, se extrapola."""
    beats = list(info.beats)
    if not beats:
        beats = [0.0]
    while len(beats) < needed_beats + 1:
        beats.append(round(beats[-1] + info.period, 3))
    return beats


def template_gym_stack(clips: list[str], info: BeatInfo, opt: AutoOptions, base: str) -> list[dict]:
    """Estilo del video de referencia:

    - negro hasta el primer beat
    - 3 paneles apilados que aparecen cada 2 beats
    - en el beat 6 el siguiente plano cae desde arriba (slidedown, 1 beat)
    - un clip a pantalla completa cada 4 beats (cortes secos en el beat)
    - el último clip dura 8 beats y termina con glitch
    """
    if len(clips) < 4:
        raise ValueError("La plantilla 'gym_stack' necesita al menos 4 clips (3 paneles + 1 pantalla completa)")
    panels, full = clips[:3], clips[3:]
    stack_beats, first_beats, beats_per_clip, last_beats = 6, 5, 4, 8

    def needed(nfull: int) -> int:
        if nfull == 1:
            return stack_beats + last_beats
        return stack_beats + first_beats + beats_per_clip * (nfull - 2) + last_beats

    if opt.max_duration:
        b0 = info.beats[0] if info.beats else 0.0
        avail = int((opt.max_duration - b0) / info.period)
        while len(full) > 1 and needed(len(full)) > avail:
            full = full[:-1]
    grid = _beat_grid(info, needed(len(full)))
    p = info.period

    segs: list[dict] = []
    if grid[0] > 0.05:
        segs.append({"type": "black", "duration": round(grid[0], 3)})

    stack_dur = grid[stack_beats] - grid[0]
    panel_list = []
    for i, c in enumerate(panels):
        appear = round(grid[2 * i] - grid[0], 3)
        panel_list.append({
            "src": _rel(c, base),
            "in": best_inpoint(c, stack_dur - appear) if opt.smart_inpoints else 0.0,
            "appear": appear,
        })
    segs.append({"type": "stack", "duration": round(stack_dur, 3), "panels": panel_list})

    k = stack_beats
    for j, c in enumerate(full):
        last = j == len(full) - 1
        nb = last_beats if last else (first_beats if j == 0 else beats_per_clip)
        dur = grid[k + nb] - grid[k]
        seg = {
            "type": "clip",
            "src": _rel(c, base),
            "in": best_inpoint(c, dur) if opt.smart_inpoints else 0.0,
            "duration": round(dur, 3),
            "transition": "cut",
            "effects": [],
        }
        if j == 0:
            seg["transition"] = {"type": "slidedown", "duration": round(0.9 * p, 3)}
        if last:
            seg["effects"].append({"type": "glitch", "at": "end", "duration": round(min(1.2 * p, dur / 2), 3)})
        segs.append(seg)
        k += nb
    return segs


def template_beat_cuts(clips: list[str], info: BeatInfo, opt: AutoOptions, base: str,
                       beats_per_clip: int = 2) -> list[dict]:
    """Montaje rápido: corte a pantalla completa cada N beats, zoom de golpe en cada
    corte y flash al entrar al último clip. Los clips se repiten si hace falta."""
    if not clips:
        raise ValueError("Hacen falta clips")
    total = opt.max_duration or max(info.duration, len(clips) * beats_per_clip * info.period)
    n_cuts = max(1, int((total - (info.beats[0] if info.beats else 0)) / (beats_per_clip * info.period)))
    grid = _beat_grid(info, n_cuts * beats_per_clip)
    segs: list[dict] = []
    if grid[0] > 0.05:
        segs.append({"type": "black", "duration": round(grid[0], 3)})
    used: dict[str, float] = {}
    for i in range(n_cuts):
        c = clips[i % len(clips)]
        dur = grid[(i + 1) * beats_per_clip] - grid[i * beats_per_clip]
        if c in used:
            inpoint = used[c]
        else:
            inpoint = best_inpoint(c, dur) if opt.smart_inpoints else 0.0
        used[c] = round(inpoint + dur, 3) if inpoint + 2 * dur < ff.duration(c) else 0.0
        seg = {
            "type": "clip", "src": _rel(c, base), "in": round(inpoint, 3), "duration": round(dur, 3),
            "transition": "cut",
            "effects": [{"type": "punch", "times": [0.0], "amount": 0.07}],
        }
        if i == n_cuts - 1:
            seg["transition"] = {"type": "flash", "duration": round(min(0.25, dur / 3), 3)}
            seg["effects"].append({"type": "fade_out", "duration": round(min(0.6, dur / 2), 3)})
        segs.append(seg)
    return segs


TEMPLATES = {
    "gym_stack": template_gym_stack,
    "beat_cuts": template_beat_cuts,
}


def build_project(clips: list[str], music: str, template: str = "gym_stack",
                  opt: AutoOptions | None = None, project_dir: str = ".") -> tuple[dict, BeatInfo]:
    opt = opt or AutoOptions()
    if template not in TEMPLATES:
        raise ValueError(f"Plantilla '{template}' desconocida. Opciones: {', '.join(TEMPLATES)}")
    clips = [os.path.abspath(c) for c in clips]
    for c in clips + [music]:
        if not os.path.exists(c):
            raise FileNotFoundError(c)
    info = detect(music, start=opt.music_start, duration=opt.max_duration)
    base = os.path.abspath(project_dir)
    segs = TEMPLATES[template](clips, info, opt, base)
    project = {
        "output": {"width": opt.width or 1080, "height": opt.height or 1920, "fps": opt.fps, "crf": 18},
        "music": {"src": _rel(os.path.abspath(music), base), "start": opt.music_start, "fade_out": 0.4},
        "grade": opt.grade or "night_blue",
        "info": {"template": template, "bpm": info.bpm,
                 "beats": [round(b, 3) for b in info.beats[:64]]},
        "segments": segs,
    }
    return project, info
