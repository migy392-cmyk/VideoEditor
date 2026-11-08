"""Análisis de un video de referencia: cortes, paneles, negros, glitch y beats.

Sirve para estudiar un edit que te gusta y convertir su estructura de tiempos
en una plantilla editable (JSON) donde sólo tienes que poner tus clips.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import ff
from .beats import BeatInfo, detect

AW, AH = 36, 64  # resolución de análisis


@dataclass
class Shot:
    start: float
    end: float
    kind: str = "full"           # full | black | stack
    panels: list[float] = field(default_factory=list)  # tiempos de aparición (stack)
    glitch_at: float | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Analysis:
    width: int
    height: int
    duration: float
    fps: float
    shots: list[Shot]
    beats: BeatInfo | None


def _frames(path: str, fps: float) -> np.ndarray:
    """Frames RGB pequeños, forma (n, AH, AW, 3)."""
    raw = ff.capture(["-i", path, "-an", "-vf", f"fps={fps},scale={AW}:{AH},format=rgb24",
                      "-f", "rawvideo", "-"])
    arr = np.frombuffer(raw, dtype=np.uint8)
    n = len(arr) // (AW * AH * 3)
    return arr[: n * AW * AH * 3].reshape(n, AH, AW, 3).astype(np.float32)


def analyze(path: str, fps: float = 30.0, cut_threshold: float = 3.0,
            min_shot: float = 0.25) -> Analysis:
    w, h = ff.video_size(path)
    dur = ff.duration(path)
    rgb = _frames(path, fps)
    fr = rgb.mean(axis=3)
    n = len(fr)
    t = np.arange(n) / fps

    # Brillo por tercios horizontales (detecta layouts apilados)
    thirds = np.stack([fr[:, i * AH // 3:(i + 1) * AH // 3].mean(axis=(1, 2)) for i in range(3)], axis=1)
    lit = thirds > 10
    black = ~lit.any(axis=1)

    # Diferencia entre frames → cortes
    diff = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    diff = np.concatenate([[0.0], diff])
    med = np.median(diff) + 1e-3
    score = diff / med
    # dominante verde → glitch de compresión / datamosh
    green = (rgb[..., 1] - (rgb[..., 0] + rgb[..., 2]) / 2).mean(axis=(1, 2))
    # diferencia por tercio en cada frame (para distinguir "aparece un panel" de "corte")
    third_diff = np.stack([
        np.concatenate([[0.0], np.abs(np.diff(fr[:, i * AH // 3:(i + 1) * AH // 3], axis=0)).mean(axis=(1, 2))])
        for i in range(3)], axis=1)

    cuts = [0]
    for i in range(1, n):
        state_change = black[i] != black[i - 1] or (lit[i] != lit[i - 1]).any()
        if (score[i] > cut_threshold and diff[i] > 6) or state_change:
            if i - cuts[-1] >= 3 or state_change:
                cuts.append(i)
    cuts.append(n)

    shots: list[Shot] = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        s = Shot(start=round(float(t[a]), 3), end=round(float(t[b - 1] + 1 / fps), 3))
        seg_lit = lit[a:b]
        if black[a:b].mean() > 0.9:
            s.kind = "black"
        elif seg_lit.all(axis=1).mean() <= 0.9 and seg_lit.any(axis=1).all():
            s.kind = "stack"
        shots.append(s)

    # Planos demasiado cortos suelen ser movimiento rápido o glitch, no cortes reales
    cleaned: list[Shot] = []
    for s in shots:
        if cleaned and s.duration < min_shot and s.kind != "black" and cleaned[-1].kind != "black":
            cleaned[-1].end = s.end
        else:
            cleaned.append(s)
    shots = cleaned

    # Fusionar apariciones sucesivas de paneles en un único shot "stack"
    merged: list[Shot] = []
    for s in shots:
        prev = merged[-1] if merged else None
        if prev and prev.kind == "stack" and s.kind in ("stack", "full") and _is_stack_growth(lit, third_diff, fps, prev, s):
            prev.panels.append(round(s.start - prev.start, 3))
            prev.end = s.end
            continue
        if s.kind == "stack":
            s.panels = [0.0]
        merged.append(s)

    # Glitch: tramo final del plano con dominante verde sostenida
    for s in merged:
        a, b = int(s.start * fps), min(n, int(s.end * fps))
        if b - a > 6 and s.kind != "black":
            base = float(np.median(green[a:a + (b - a) // 2]))
            hot = green[a:b] - base > 2.5
            k = len(hot)
            while k > 0 and not hot[k - 1]:
                k -= 1  # ignora los últimos frames si ya no hay glitch (p. ej. fundido a negro)
            if k == 0 or len(hot) - k > 3:
                continue
            run = k
            while run > 0 and hot[run - 1]:
                run -= 1
            if k - run >= 2 and run > (b - a) * 0.5:
                s.glitch_at = round(s.start + run / fps, 3)

    beats = None
    if ff.has_audio(path):
        try:
            beats = detect(path)
        except ff.FFmpegError:
            beats = None
    return Analysis(width=w, height=h, duration=dur, fps=fps, shots=merged, beats=beats)


def _is_stack_growth(lit: np.ndarray, third_diff: np.ndarray, fps: float, prev: Shot, cur: Shot) -> bool:
    """True si `cur` es el mismo layout apilado con un panel más encendido
    (los paneles que ya estaban no cambian de golpe en el corte)."""
    a = int(prev.start * fps)
    b = int(cur.start * fps)
    c = min(len(lit), int(cur.end * fps))
    if b >= c:
        return False
    before = lit[max(a, b - 3):b].all(axis=0)  # estado justo antes del cambio
    after = lit[b:c].mean(axis=0) > 0.5
    if not (after.sum() > before.sum() and (after | ~before).all()):
        return False
    kept = before & after
    return bool((third_diff[b, kept] < 8).all())


def nearest_beat(info: BeatInfo | None, t: float) -> tuple[int, float] | None:
    if not info or not info.beats:
        return None
    arr = np.array(info.beats)
    i = int(np.argmin(np.abs(arr - t)))
    return i, float(t - arr[i])


def report(a: Analysis) -> str:
    lines = [
        f"Resolución: {a.width}x{a.height}  ·  Duración: {a.duration:.2f}s",
    ]
    if a.beats:
        lines.append(f"Música: ~{a.beats.bpm:.0f} BPM (1 beat = {a.beats.period:.3f}s), primer beat en {a.beats.beats[0] if a.beats.beats else 0:.2f}s")
    lines.append("")
    lines.append(f"{'#':>2}  {'inicio':>7}  {'fin':>7}  {'dur':>6}  tipo    detalles")
    for i, s in enumerate(a.shots):
        det = []
        if s.kind == "stack":
            det.append("paneles aparecen en +" + ", +".join(f"{p:.2f}s" for p in s.panels))
        if s.glitch_at is not None:
            det.append(f"glitch desde {s.glitch_at:.2f}s")
        nb = nearest_beat(a.beats, s.start)
        if nb and i > 0:
            det.append(f"corte en beat {nb[0]} ({nb[1] * 1000:+.0f} ms)")
        lines.append(f"{i:>2}  {s.start:7.2f}  {s.end:7.2f}  {s.duration:6.2f}  {s.kind:<6}  {'; '.join(det)}")
    return "\n".join(lines)


def to_template(a: Analysis) -> dict:
    """Proyecto esqueleto con los tiempos del video analizado y fuentes de ejemplo."""
    segs = []
    n = 0
    pending_tr: dict | None = None
    shots = list(a.shots)
    for idx, s in enumerate(shots):
        d = round(s.duration, 3)
        nxt = shots[idx + 1] if idx + 1 < len(shots) else None
        # Un plano muy corto entre un stack y un plano completo es la transición de entrada.
        if (s.kind == "full" and s.duration < 0.5 and segs and segs[-1]["type"] == "stack"
                and nxt is not None and nxt.kind == "full"):
            pending_tr = {"type": "slidedown", "duration": d}
            continue
        if s.kind == "black":
            segs.append({"type": "black", "duration": d})
        elif s.kind == "stack":
            panels = []
            for p in (s.panels if len(s.panels) >= 2 else [0.0, 0.0]):
                n += 1
                panels.append({"src": f"clip{n}.mov", "in": 0.0, "appear": p})
            segs.append({"type": "stack", "duration": d, "panels": panels})
        else:
            n += 1
            seg = {"type": "clip", "src": f"clip{n}.mov", "in": 0.0, "duration": d, "transition": "cut"}
            if pending_tr:
                seg["duration"] = round(d + pending_tr["duration"], 3)
                seg["transition"] = pending_tr
                pending_tr = None
            if s.glitch_at is not None:
                seg["effects"] = [{"type": "glitch", "at": "end", "duration": round(s.end - s.glitch_at, 3)}]
            segs.append(seg)
    return {
        "output": {"width": a.width - a.width % 2, "height": a.height - a.height % 2, "fps": 30},
        "music": {"src": "cancion.mp3", "start": 0.0},
        "grade": "night_blue",
        "segments": segs,
    }
