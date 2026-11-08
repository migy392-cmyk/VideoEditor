"""Diseño sonoro: efectos sintetizados (sin samples con derechos) y su colocación
automática según el montaje.

Tipos: whoosh (transiciones con movimiento), impact (golpe grave en el drop o al
aterrizar), riser (subida de tensión antes del drop), hit (golpe corto), glitch.
"""
from __future__ import annotations

import os
import wave

import numpy as np

SR = 44100
TYPES = ("whoosh", "impact", "riser", "hit", "glitch")


def _cache_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), ".cache", "videoeditor", "sfx")
    os.makedirs(d, exist_ok=True)
    return d


def _lowpass(x: np.ndarray, cutoff: np.ndarray | float) -> np.ndarray:
    """Filtro paso bajo de un polo con frecuencia de corte variable."""
    c = np.broadcast_to(np.asarray(cutoff, dtype=float), x.shape)
    a = 1 - np.exp(-2 * np.pi * c / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    return y


def _synth(kind: str, dur: float) -> np.ndarray:
    rng = np.random.default_rng(7)
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = np.linspace(0, 1, n)
    if kind == "whoosh":
        noise = rng.standard_normal(n)
        env = np.sin(np.pi * x) ** 2
        cutoff = 300 + 5000 * np.sin(np.pi * x) ** 1.5
        sig = _lowpass(noise, cutoff) * env
        sig -= _lowpass(sig, 120.0)  # sin graves sucios
    elif kind == "impact":
        f = 95 * np.exp(-t * 6) + 38
        boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.2)
        crack = _lowpass(rng.standard_normal(n), 2500.0) * np.exp(-t * 28)
        sig = boom + 0.6 * crack
    elif kind == "riser":
        noise = _lowpass(rng.standard_normal(n), 200 + 7000 * x ** 2)
        f = 180 * 2 ** (3 * x)
        tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.35
        sig = (noise + tone) * x ** 2.2
        sig[-int(0.01 * SR):] *= np.linspace(1, 0, int(0.01 * SR))
    elif kind == "hit":
        sig = (np.sin(2 * np.pi * 70 * t) * np.exp(-t * 18) +
               0.5 * _lowpass(rng.standard_normal(n), 4000.0) * np.exp(-t * 40))
    elif kind == "glitch":
        step = int(SR / 180)
        sq = np.sign(np.sin(2 * np.pi * 220 * (1 + 3 * (rng.random(n // step + 1).repeat(step)[:n])) * t))
        crush = np.round(rng.standard_normal(n) * 3) / 3
        gate = (rng.random(n // 1500 + 1) > 0.35).repeat(1500)[:n]
        sig = (0.5 * sq + 0.5 * crush) * gate * np.exp(-t * 2)
    else:
        raise ValueError(f"Efecto de sonido desconocido: {kind}")
    peak = np.abs(sig).max() or 1.0
    return (sig / peak * 0.9).astype(np.float32)


DEFAULT_DUR = {"whoosh": 0.6, "impact": 1.6, "riser": 3.0, "hit": 0.4, "glitch": 0.6}


def sample(kind: str, dur: float | None = None) -> str:
    """Ruta a un WAV del efecto (se genera y se guarda la primera vez)."""
    dur = round(float(dur or DEFAULT_DUR[kind]), 2)
    path = os.path.join(_cache_dir(), f"{kind}_{dur:.2f}.wav")
    if not os.path.exists(path):
        sig = _synth(kind, dur)
        stereo = np.repeat((sig * 32767).astype(np.int16)[:, None], 2, axis=1)
        tmp = path + ".part"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(stereo.tobytes())
        os.replace(tmp, path)
    return path


MOVING = {"slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "smoothup",
          "smoothdown", "zoomin", "squeezeh", "squeezev", "subject_drop", "wipeleft", "wiperight",
          "wipeup", "wipedown", "pixelize", "hblur", "circleopen", "radial"}


def auto_cues(project, drop: float | None = None) -> list[dict]:
    """Coloca efectos de sonido según el montaje:
    - whoosh en transiciones con movimiento (centrado en la transición)
    - impact cuando aterriza un subject_drop y en el drop de la canción
    - riser los 2 compases antes del drop
    - glitch donde hay efecto glitch
    """
    cues: list[dict] = []
    t = 0.0
    for i, seg in enumerate(project.segments):
        tr = seg.transition
        if i > 0 and tr.type in MOVING:
            cues.append({"type": "whoosh", "at": max(0.0, t - 0.2), "duration": max(0.4, tr.duration + 0.3),
                         "volume": 0.5})
            if tr.type == "subject_drop":
                cues.append({"type": "impact", "at": t + tr.duration, "volume": 0.7})
        elif i > 0 and tr.type in ("flash", "fadewhite"):
            cues.append({"type": "hit", "at": t, "volume": 0.5})
        for e in seg.effects:
            if e.type == "glitch":
                d = float(e.get("duration", 0.6))
                at = e.get("at", "end")
                start = t + (seg.duration - d if at == "end" else 0.0 if at == "start" else float(at))
                cues.append({"type": "glitch", "at": max(0.0, start), "duration": d, "volume": 0.45})
        t += seg.duration
    if drop is not None and 0.5 < drop < t:
        if not any(c["type"] == "impact" and abs(c["at"] - drop) < 0.3 for c in cues):
            cues.append({"type": "impact", "at": drop, "volume": 0.75})
        p = (project.beats[1] - project.beats[0]) if project.beats and len(project.beats) > 1 else 0.5
        rd = min(8 * p, drop - 0.2)
        if rd > 1.0:
            cues.append({"type": "riser", "at": drop - rd, "duration": rd, "volume": 0.4})
    return sorted(cues, key=lambda c: c["at"])
