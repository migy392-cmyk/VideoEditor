"""Análisis de un video de referencia.

Detecta:
- planos y cortes secos, y su relación con los beats de la música
- layouts apilados (paneles que aparecen uno a uno)
- transiciones: fundido/disolución, fundido a negro, flash blanco,
  deslizamiento (slide) y "subject drop" (la persona recortada entra encima)
- efectos: glitch al final de un plano, flashes dentro de un plano,
  barras de cine (letterbox)
- cantidad de movimiento de cada plano
- perfil de color (cuantiles por canal) para copiar el look

El resultado se usa para generar plantillas (`to_template`) y para aprender
estilos reutilizables (`style.learn`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import ff
from .beats import BeatInfo, detect

AW, AH = 36, 64  # resolución de análisis
QUANTILES = (0.02, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 0.98)

SLIDE_BY_DIRECTION = {"top": "slidedown", "bottom": "slideup", "left": "slideright", "right": "slideleft"}


@dataclass
class Shot:
    start: float
    end: float
    kind: str = "full"           # full | black | stack
    panels: list[float] = field(default_factory=list)  # tiempos de aparición (stack)
    glitch_at: float | None = None
    flashes: list[float] = field(default_factory=list)  # flashes dentro del plano (relativos)
    transition: dict = field(default_factory=lambda: {"type": "cut"})  # cómo entra este plano
    motion: float = 0.0          # movimiento medio (relativo al video)

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
    color: dict[str, list[float]] = field(default_factory=dict)
    letterbox: float = 0.0


def _frames(path: str, fps: float) -> np.ndarray:
    """Frames RGB pequeños, forma (n, AH, AW, 3)."""
    raw = ff.capture(["-i", path, "-an", "-vf", f"fps={fps},scale={AW}:{AH},format=rgb24",
                      "-f", "rawvideo", "-"])
    arr = np.frombuffer(raw, dtype=np.uint8)
    n = len(arr) // (AW * AH * 3)
    return arr[: n * AW * AH * 3].reshape(n, AH, AW, 3).astype(np.float32)


# --------------------------------------------------------------------------- #
# Detección de bordes entre planos
# --------------------------------------------------------------------------- #

def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Tramos contiguos [a, b) donde mask es True."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _gradual_regions(fr: np.ndarray, diff: np.ndarray, k: int = 4) -> list[tuple[int, int]]:
    """Regiones [a, b) con disolución: el frame central ≈ media de los extremos."""
    n = len(fr)
    flags = np.zeros(n, bool)
    for i in range(k, n - k):
        d = np.abs(fr[i + k] - fr[i - k]).mean()
        if d < 15:
            continue
        mix = np.abs(fr[i] - (fr[i - k] + fr[i + k]) / 2).mean()
        step = diff[i - k + 1:i + k + 1].max()
        if mix < 0.3 * d and step < 0.5 * d:
            flags[i] = True
    regions = []
    i = 0
    while i < n:
        if flags[i]:
            j = i
            while j + 1 < n and flags[j + 1]:
                j += 1
            if j - i >= 1:
                regions.append((max(0, i - k + 1), min(n, j + k)))
            i = j + 1
        else:
            i += 1
    return regions


def analyze(path: str, fps: float = 30.0, cut_threshold: float = 3.0,
            min_shot: float = 0.25) -> Analysis:
    w, h = ff.video_size(path)
    dur = ff.duration(path)
    rgb = _frames(path, fps)
    fr = rgb.mean(axis=3)
    n = len(fr)
    if n < 2:
        raise ff.FFmpegError(f"'{path}' es demasiado corto para analizar")
    t = np.arange(n) / fps
    luma = fr.mean(axis=(1, 2))

    # Brillo por tercios horizontales (detecta layouts apilados)
    thirds = np.stack([fr[:, i * AH // 3:(i + 1) * AH // 3].mean(axis=(1, 2)) for i in range(3)], axis=1)
    lit = thirds > 10
    black = ~lit.any(axis=1)

    diff = np.concatenate([[0.0], np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))])
    med = np.median(diff) + 1e-3
    score = diff / med
    green = (rgb[..., 1] - (rgb[..., 0] + rgb[..., 2]) / 2).mean(axis=(1, 2))
    third_diff = np.stack([
        np.concatenate([[0.0], np.abs(np.diff(fr[:, i * AH // 3:(i + 1) * AH // 3], axis=0)).mean(axis=(1, 2))])
        for i in range(3)], axis=1)

    # Eventos que no son cortes secos: suprimen la detección de cortes en su ventana.
    suppress = np.zeros(n, bool)
    events: list[tuple[int, dict]] = []
    inshot_flash: list[int] = []

    # Flash: pico de brillo. Si antes y después se ve lo mismo, es un flash dentro del plano.
    spike = np.zeros(n, bool)
    for i in range(n):
        lo, hi = max(0, i - 15), min(n, i + 16)
        spike[i] = luma[i] > np.median(luma[lo:hi]) + 40 and luma[i] > 90
    for a, b in _runs(spike):
        lo, hi = a, b
        while lo > 0 and luma[lo - 1] < luma[lo] - 3 and a - lo < fps // 2:
            lo -= 1  # rampa de subida hacia el blanco
        while hi < n and luma[hi] < luma[hi - 1] - 3 and hi - b < fps // 2:
            hi += 1  # rampa de bajada
        lo, hi = max(0, lo - 2), min(n, hi + 2)
        suppress[lo:hi] = True
        same = np.abs(fr[lo] - fr[hi - 1]).mean() < 12
        if same:
            inshot_flash.append(a)
        else:
            events.append((lo, {"type": "flash", "duration": round(max(0.2, (hi - lo) / fps), 3)}))

    # Fundido a negro: unos pocos frames casi negros con rampa de brillo antes o después.
    for a, b in _runs(luma < 8):
        if a == 0 or b >= n or (b - a) / fps > 0.8 or suppress[a:b].any():
            continue
        lo = a
        while lo > 0 and luma[lo - 1] > luma[lo] + 1 and a - lo < fps:
            lo -= 1
        hi = b
        while hi < n and luma[hi] > luma[hi - 1] + 1 and hi - b < fps:
            hi += 1
        if (a - lo) + (hi - b) < 4:
            continue  # corte seco a negro: se queda como plano negro
        suppress[lo:hi] = True
        events.append((lo, {"type": "fadeblack", "duration": round((hi - lo) / fps, 3)}))

    gradual = [r for r in _gradual_regions(fr, diff) if not suppress[r[0]:r[1]].any()]
    for a, b in gradual:
        suppress[a:b] = True
        events.append((a, {"type": "fade", "duration": round((b - a) / fps, 3)}))

    # (frame, tipo de transición)
    bounds: list[tuple[int, dict]] = [(0, {"type": "cut"})]
    for i in range(1, n):
        if suppress[i]:
            continue
        state_change = black[i] != black[i - 1] or (lit[i] != lit[i - 1]).any()
        if (score[i] > cut_threshold and diff[i] > 6) or state_change:
            if i - bounds[-1][0] >= 3 or state_change:
                bounds.append((i, {"type": "cut"}))
    bounds += events
    bounds.sort(key=lambda x: x[0])
    dedup: list[tuple[int, dict]] = []
    for f, tr in bounds:
        if dedup and f - dedup[-1][0] < 3:
            if tr["type"] != "cut":
                dedup[-1] = (dedup[-1][0], tr)
            continue
        dedup.append((f, tr))
    bounds = dedup + [(n, {})]

    shots: list[Shot] = []
    for (a, tr), (b, _) in zip(bounds[:-1], bounds[1:]):
        if b <= a:
            continue
        s = Shot(start=round(float(t[a]), 3), end=round(float(t[b - 1] + 1 / fps), 3), transition=dict(tr))
        seg_lit = lit[a:b]
        if black[a:b].mean() > 0.9:
            s.kind = "black"
        elif seg_lit.all(axis=1).mean() <= 0.9 and seg_lit.any(axis=1).all():
            s.kind = "stack"
        shots.append(s)

    # Planos cortos seguidos: o son una transición (slide / persona que entra),
    # o movimiento rápido / glitch que no es un corte real.
    cleaned: list[Shot] = []
    idx = 0
    while idx < len(shots):
        s = shots[idx]
        j = idx
        while (j < len(shots) and shots[j].kind == "full" and shots[j].duration < 0.45
               and shots[j].transition["type"] == "cut" and shots[j].end - s.start < 0.7):
            j += 1
        nxt = shots[j] if j < len(shots) else None
        if j > idx and cleaned and nxt is not None and nxt.kind == "full" and nxt.transition["type"] == "cut":
            bridge = Shot(start=s.start, end=shots[j - 1].end)
            tr = _classify_bridge(fr, fps, cleaned[-1], bridge, nxt)
            if tr:
                nxt.transition = tr
                nxt.start = bridge.start
                idx = j
                continue
        if cleaned and s.duration < min_shot and s.kind != "black" and cleaned[-1].kind != "black" \
                and s.transition["type"] == "cut":
            cleaned[-1].end = s.end
        else:
            cleaned.append(s)
        idx += 1
    shots = cleaned

    # Fusionar apariciones sucesivas de paneles en un único shot "stack"
    merged: list[Shot] = []
    for s in shots:
        prev = merged[-1] if merged else None
        if prev and prev.kind == "stack" and s.kind in ("stack", "full") and s.transition["type"] == "cut" \
                and _is_stack_growth(lit, third_diff, fps, prev, s):
            prev.panels.append(round(s.start - prev.start, 3))
            prev.end = s.end
            continue
        if s.kind == "stack":
            s.panels = [0.0]
        merged.append(s)

    # Efectos dentro de cada plano + movimiento
    motion_raw = []
    for s in merged:
        a, b = int(round(s.start * fps)), min(n, int(round(s.end * fps)))
        inner = diff[a + 2:b - 1] if b - a > 4 else diff[a:b]
        motion_raw.append(float(np.median(inner)) if len(inner) else 0.0)
        s.flashes = sorted({round((f - a) / fps, 3) for f in inshot_flash if a <= f < b})
        if b - a > 6 and s.kind != "black":
            base = float(np.median(green[a:a + (b - a) // 2]))
            g = green[a:b] - base
            g = np.maximum(g, np.maximum(np.roll(g, 1), np.roll(g, -1)))  # tolera huecos de 1 frame
            hot = g > 2.5
            k = len(hot)
            while k > 0 and not hot[k - 1]:
                k -= 1  # ignora los últimos frames si ya no hay glitch (p. ej. fundido a negro)
            if k == 0 or len(hot) - k > 3:
                continue
            run = k
            while run > 0 and hot[run - 1]:
                run -= 1
            if k - run >= 5 and run > (b - a) * 0.5:
                s.glitch_at = round(s.start + run / fps, 3)
    mmax = max(motion_raw) if motion_raw and max(motion_raw) > 0 else 1.0
    for s, m in zip(merged, motion_raw):
        s.motion = round(m / mmax, 3)

    color, letterbox = _color_profile(rgb, black, merged, fps)

    beats = None
    if ff.has_audio(path):
        try:
            beats = detect(path)
        except ff.FFmpegError:
            beats = None
    return Analysis(width=w, height=h, duration=dur, fps=fps, shots=merged, beats=beats,
                    color=color, letterbox=letterbox)


def _classify_bridge(fr: np.ndarray, fps: float, prev: Shot, bridge: Shot, nxt: Shot) -> dict | None:
    """Un plano corto entre dos planos: ¿deslizamiento o persona recortada que entra?"""
    a = int(round(bridge.start * fps))
    b = int(round(bridge.end * fps))
    if a < 1 or b - a < max(3, int(0.2 * fps)) or b >= len(fr):
        return None
    before = fr[a - 1]
    after = fr[min(len(fr) - 1, b + 1)]
    mid = fr[(a + b) // 2]
    changed = np.abs(mid - before) > 18
    like_next = np.abs(mid - after) < 18
    frac_changed = changed.mean()
    if frac_changed < 0.05 or like_next.mean() < 0.1:
        return None
    first = np.abs(fr[a] - before) > 18
    ys, xs = np.nonzero(first if first.any() else changed)
    cy, cx = ys.mean() / AH, xs.mean() / AW
    if abs(cy - 0.5) >= abs(cx - 0.5):
        direction = "top" if cy < 0.5 else "bottom"
    else:
        direction = "left" if cx < 0.5 else "right"
    if not _advances(fr[a:b], before, direction):
        return None
    d = round(bridge.duration, 3)
    if frac_changed < 0.65:
        return {"type": "subject_drop", "duration": d, "direction": direction}
    return {"type": SLIDE_BY_DIRECTION[direction], "duration": d}


def _advances(frames: np.ndarray, before: np.ndarray, direction: str) -> bool:
    """El contenido nuevo avanza de forma continua desde un borde (no es ruido ni movimiento)."""
    ext = []
    for f in frames:
        ch = np.abs(f - before) > 18
        prof = ch.mean(axis=1) if direction in ("top", "bottom") else ch.mean(axis=0)
        idx = np.nonzero(prof > 0.15)[0]
        if len(idx) == 0:
            ext.append(0.0)
            continue
        size = len(prof)
        if direction in ("top", "left"):
            ext.append((idx.max() + 1) / size)  # hasta dónde llegó desde el borde
        else:
            ext.append((size - idx.min()) / size)
        # lo cambiado debe estar pegado al borde de entrada
        edge = idx.min() if direction in ("top", "left") else size - 1 - idx.max()
        if edge > size * 0.2:
            return False
    ext = np.array(ext)
    if len(ext) < 2 or ext[-1] - ext[0] < 0.25:
        return False
    drops = np.maximum(0, ext[:-1] - ext[1:]).sum()
    return bool(drops < 0.15)


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


def _color_profile(rgb: np.ndarray, black: np.ndarray, shots: list[Shot], fps: float) -> tuple[dict, float]:
    keep = ~black.copy()
    for s in shots:
        if s.glitch_at is not None:
            keep[int(s.glitch_at * fps):int(s.end * fps) + 1] = False
    frames = rgb[keep] if keep.any() else rgb
    # Barras de cine: filas siempre negras arriba y abajo
    rows = frames.mean(axis=3).max(axis=0).max(axis=1)  # brillo máximo por fila
    top = 0
    while top < AH // 4 and rows[top] < 12:
        top += 1
    bot = 0
    while bot < AH // 4 and rows[AH - 1 - bot] < 12:
        bot += 1
    letterbox = round(min(top, bot) / AH, 3) if min(top, bot) >= 2 else 0.0
    if letterbox:
        cut = int(letterbox * AH)
        frames = frames[:, cut:AH - cut]
    color = {}
    for ci, ch in enumerate("rgb"):
        vals = frames[..., ci].ravel() / 255.0
        color[ch] = [round(float(q), 4) for q in np.quantile(vals, QUANTILES)]
    return color, letterbox


# --------------------------------------------------------------------------- #
# Informes y plantillas
# --------------------------------------------------------------------------- #

def nearest_beat(info: BeatInfo | None, t: float) -> tuple[int, float] | None:
    if not info or not info.beats:
        return None
    arr = np.array(info.beats)
    i = int(np.argmin(np.abs(arr - t)))
    return i, float(t - arr[i])


TR_NAMES = {"cut": "corte seco", "fade": "disolución", "fadeblack": "fundido a negro", "flash": "flash",
            "subject_drop": "persona recortada entra", "slidedown": "desliza desde arriba",
            "slideup": "desliza desde abajo", "slideleft": "desliza desde la derecha",
            "slideright": "desliza desde la izquierda"}


def report(a: Analysis) -> str:
    lines = [f"Resolución: {a.width}x{a.height}  ·  Duración: {a.duration:.2f}s"]
    if a.beats:
        lines.append(f"Música: ~{a.beats.bpm:.0f} BPM (1 beat = {a.beats.period:.3f}s), "
                     f"primer beat en {a.beats.beats[0] if a.beats.beats else 0:.2f}s")
    if a.letterbox:
        lines.append(f"Barras de cine: {a.letterbox * 100:.0f}% arriba y abajo")
    lines.append("")
    lines.append(f"{'#':>2}  {'inicio':>7}  {'fin':>7}  {'dur':>6}  {'tipo':<6}  {'mov':>4}  detalles")
    for i, s in enumerate(a.shots):
        det = []
        if i > 0:
            tr = s.transition
            name = TR_NAMES.get(tr["type"], tr["type"])
            if tr["type"] != "cut":
                name += f" {tr.get('duration', 0):.2f}s"
                if tr["type"] == "subject_drop":
                    name += f" desde {tr.get('direction', 'top')}"
            det.append(name)
        if s.kind == "stack":
            det.append("paneles en +" + ", +".join(f"{p:.2f}s" for p in s.panels))
        if s.flashes:
            det.append("flash en +" + ", +".join(f"{f:.2f}s" for f in s.flashes))
        if s.glitch_at is not None:
            det.append(f"glitch desde {s.glitch_at:.2f}s")
        nb = nearest_beat(a.beats, s.start)
        if nb and i > 0:
            det.append(f"beat {nb[0]} ({nb[1] * 1000:+.0f} ms)")
        lines.append(f"{i:>2}  {s.start:7.2f}  {s.end:7.2f}  {s.duration:6.2f}  {s.kind:<6}  "
                     f"{s.motion:4.2f}  {'; '.join(det)}")
    return "\n".join(lines)


def to_template(a: Analysis) -> dict:
    """Proyecto esqueleto con los tiempos del video analizado y fuentes de ejemplo."""
    segs = []
    n = 0
    for s in a.shots:
        d = round(s.duration, 3)
        if s.kind == "black":
            seg = {"type": "black", "duration": d}
        elif s.kind == "stack":
            panels = []
            for p in (s.panels if len(s.panels) >= 2 else [0.0, 0.0]):
                n += 1
                panels.append({"src": f"clip{n}.mov", "in": 0.0, "appear": p})
            seg = {"type": "stack", "duration": d, "panels": panels}
        else:
            n += 1
            seg = {"type": "clip", "src": f"clip{n}.mov", "in": 0.0, "duration": d}
            effects = [{"type": "flash", "times": s.flashes}] if s.flashes else []
            if s.glitch_at is not None:
                effects.append({"type": "glitch", "at": "end", "duration": round(s.end - s.glitch_at, 3)})
            if effects:
                seg["effects"] = effects
        if segs:
            tr = dict(s.transition)
            if "duration" in tr:
                tr["duration"] = round(min(tr["duration"], d * 0.8), 3)
            seg["transition"] = tr
        segs.append(seg)
    out = {
        "output": {"width": a.width - a.width % 2, "height": a.height - a.height % 2, "fps": 30},
        "music": {"src": "cancion.mp3", "start": 0.0},
        "grade": "none",
        "color_match": a.color,
        "segments": segs,
    }
    if a.letterbox:
        for seg in segs:
            seg.setdefault("effects", []).append({"type": "letterbox", "size": a.letterbox})
    return out
