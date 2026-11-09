"""Análisis de un video de referencia.

Detecta:
- planos y cortes secos, y su relación con los beats de la música
- layouts apilados (paneles que aparecen uno a uno)
- transiciones: fundido/disolución, fundido a negro, flash blanco,
  deslizamiento (slide) y "subject drop" (la persona recortada entra encima)
- transiciones centradas en el corte: zoom a través del corte y barrido (whip pan)
- layouts en columnas o en cuadrícula
- efectos: glitch al final de un plano, flashes dentro de un plano,
  barras de cine (letterbox), imagen congelada, estroboscopio, planos en blanco y
  negro / desaturados y destellos en blanco y negro
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
CENTERED = ("zoomthrough", "whip")  # transiciones centradas en el corte


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
    punches: list[tuple[float, float]] = field(default_factory=list)       # (t relativo, cantidad)
    shakes: list[tuple[float, float, float]] = field(default_factory=list)  # (t, duración, cantidad)
    speed: list[tuple[float, float, float]] = field(default_factory=list)   # (t, duración, velocidad)
    texts: list[dict] = field(default_factory=list)  # {start, end (relativos), box, text}
    freezes: list[tuple[float, float]] = field(default_factory=list)  # (t relativo, duración)
    strobes: list[dict] = field(default_factory=list)  # {at, duration, rate (Hz), amount, duty, color}
    bw_flashes: list[tuple[float, float]] = field(default_factory=list)  # (t relativo, duración)
    grade: str | None = None     # "bw" / "desaturated" si el plano va sin color (o casi)
    layout: str = "rows"         # stack: rows | columns | grid

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
            min_shot: float = 0.25, deep: bool = True) -> Analysis:
    w, h = ff.video_size(path)
    dur = ff.duration(path)
    rgb = _frames(path, fps)
    fr = rgb.mean(axis=3)
    n = len(fr)
    mot = None
    if deep:
        from . import motionfx

        mot = motionfx.frame_motion(motionfx.gray_frames(path, fps))
        if len(mot["raw"]) < n:
            mot = None
    # un "corte" que se explica con un zoom o un desplazamiento de la imagen anterior no es un corte
    camera_move = np.zeros(n, bool)
    if mot is not None:
        # Un desplazamiento grande y sostenido en una dirección es un slide (o un barrido),
        # no un zoom de golpe o una sacudida: ahí sí se deja actuar al detector de cortes.
        sustained = np.zeros(n, bool)
        # (si el fotograma se explica con un zoom claro, el desplazamiento medido no cuenta)
        zooming = np.abs(mot["scale"][:n] - 1.0) >= 0.03
        for comp in (mot["dx"][:n], mot["dy"][:n]):
            sgn = np.where((np.abs(comp) >= 3) & ~zooming, np.sign(comp), 0)
            for i in range(n):
                if sgn[i] and i + 2 < n and sgn[i] == sgn[i + 1] == sgn[i + 2]:
                    sustained[i:i + 3] = True
        camera_move = (mot["resid"][:n] < 0.45 * mot["raw"][:n]) & (mot["raw"][:n] > 1) & ~sustained
    if n < 2:
        raise ff.FFmpegError(f"'{path}' es demasiado corto para analizar")
    t = np.arange(n) / fps
    luma = fr.mean(axis=(1, 2))

    # Brillo por tercios horizontales (detecta layouts apilados)
    thirds = np.stack([fr[:, i * AH // 3:(i + 1) * AH // 3].mean(axis=(1, 2)) for i in range(3)], axis=1)
    # (una franja "apagada" tiene que ser negra de verdad: en un plano nocturno o en blanco y
    # negro muy oscuro, la parte de arriba puede tener poco brillo medio y no es un panel vacío)
    true_black = np.stack([(fr[:, i * AH // 3:(i + 1) * AH // 3] < 8).mean(axis=(1, 2)) for i in range(3)], axis=1)
    lit = (thirds > 10) | (true_black < 0.85)
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

    # Estroboscopio: parpadeo regular (a negro o a blanco) sobre la misma imagen. No son cortes.
    strobe_regs = _strobe_regions(luma, fr, fps)
    strobe_mask = np.zeros(n, bool)
    for r in strobe_regs:
        suppress[r["a"]:min(n, r["b"] + 1)] = True
        strobe_mask[r["a"]:r["b"]] = True

    # Flash: pico de brillo. Si antes y después se ve lo mismo, es un flash dentro del plano.
    spike = np.zeros(n, bool)
    for i in range(n):
        lo, hi = max(0, i - 15), min(n, i + 16)
        spike[i] = luma[i] > np.median(luma[lo:hi]) + 40 and luma[i] > 90 and not strobe_mask[i]
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

    # Destello en blanco y negro: unos fotogramas sin color en mitad de un plano en color
    chroma = (rgb.max(axis=3) - rgb.min(axis=3)).mean(axis=(1, 2))
    bw_runs = _bw_flash_runs(chroma, luma, fr, fps, suppress)
    for a, b in bw_runs:
        suppress[a:b + 1] = True

    # Barrido (whip): la imagen sale disparada con desenfoque direccional, corte en mitad
    # del movimiento y el plano nuevo llega igual de rápido. El corte cae en el centro.
    if mot is not None:
        cand = np.nonzero((score > cut_threshold) & (diff > 6) & ~suppress)[0]
        for c in sorted(cand, key=lambda i: -diff[i]):
            if suppress[c]:
                continue
            wh = _whip_at(mot, fr, diff, int(c), n, fps)
            if wh is None or suppress[wh["a"]:wh["b"]].any():
                continue
            suppress[wh["a"]:wh["b"] + 2] = True
            events.append((wh["cut"], {"type": "whip", "duration": round((wh["b"] - wh["a"]) / fps, 3),
                                       "direction": wh["direction"]}))

    # Zoom a través del corte: la imagen se amplía cada vez más rápido hasta el corte y el
    # plano nuevo entra ampliado y se asienta. Los fotogramas del zoom no son cortes.
    if mot is not None:
        for c in np.nonzero((score > cut_threshold) & (diff > 6) & ~suppress & ~camera_move)[0]:
            if suppress[c]:
                continue
            z = _zoom_through(mot["scale"][:n], int(c), fps)
            if z is not None:
                suppress[z[1]:z[2] + 2] = True  # (+2: el desenfoque de movimiento termina con un salto)
                events.append((int(c), {"type": "zoomthrough", "duration": z[0]}))

    # (frame, tipo de transición)
    bounds: list[tuple[int, dict]] = [(0, {"type": "cut"})]
    for i in range(1, n):
        if suppress[i]:
            continue
        state_change = black[i] != black[i - 1] or (lit[i] != lit[i - 1]).any()
        if (score[i] > cut_threshold and diff[i] > 6 and not camera_move[i]) or state_change:
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
        a_, b_ = int(round(s.start * fps)), int(round(s.end * fps))
        glitchy = b_ > a_ and float(np.mean(green[a_:b_])) - float(np.median(green)) > 2.0
        if cleaned and (s.duration < min_shot or (glitchy and s.duration < 0.45)) and s.kind != "black" \
                and cleaned[-1].kind != "black" and s.transition["type"] == "cut":
            cleaned[-1].end = s.end
        else:
            cleaned.append(s)
        idx += 1
    shots = cleaned

    # Fusionar apariciones sucesivas de paneles en un único shot "stack"
    relabeled = _relabel_layouts(fr, fps, shots)
    merged: list[Shot] = []
    for s in shots:
        prev = merged[-1] if merged else None
        if prev and prev.kind == "stack" and prev.layout == "rows" and s.kind in ("stack", "full") \
                and s.layout == "rows" and s.transition["type"] == "cut" \
                and _is_stack_growth(lit, third_diff, fps, prev, s):
            prev.panels.append(round(s.start - prev.start, 3))
            prev.end = s.end
            continue
        if s.kind == "stack" and s.layout == "rows":
            s.panels = [0.0]
        merged.append(s)
    merged = _other_layouts(fr, fps, merged, relabeled)

    # Efectos dentro de cada plano + movimiento
    motion_raw = []
    for k, s in enumerate(merged):
        a, b = int(round(s.start * fps)), min(n, int(round(s.end * fps)))
        inner = diff[a + 2:b - 1] if b - a > 4 else diff[a:b]
        motion_raw.append(float(np.median(inner)) if len(inner) else 0.0)
        s.flashes = sorted({round((f - a) / fps, 3) for f in inshot_flash if a <= f < b})
        if s.kind == "stack" and s.panels:  # con paneles que aún aparecen, el color cambia solo
            a = min(b, a + int(round(max(s.panels) * fps)))
        nxt = merged[k + 1].transition if k + 1 < len(merged) else {}
        if nxt.get("type") in CENTERED:  # la salida de un zoom / barrido no es un glitch
            b = max(a, b - int(nxt.get("duration", 0) * fps / 2 + 0.5))
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
                s.glitch_at = round((a + run) / fps, 3)
    mmax = max(motion_raw) if motion_raw and max(motion_raw) > 0 else 1.0
    for s, m in zip(merged, motion_raw):
        s.motion = round(m / mmax, 3)

    for r in strobe_regs:
        for s in merged:
            if s.start <= r["a"] / fps < s.end:
                s.strobes.append({"at": round(r["a"] / fps - s.start, 3), "duration": r["duration"],
                                  "rate": r["rate"], "amount": r["amount"], "duty": r["duty"], "color": r["color"]})
                break
    for a, b in bw_runs:
        for s in merged:
            if s.start <= a / fps < s.end:
                s.bw_flashes.append((round(a / fps - s.start, 3), round((b - a) / fps, 3)))
                break
    _color_shots(chroma, luma, fps, merged, suppress | strobe_mask, inshot_flash)
    for k, s in enumerate(merged):
        if s.kind == "full":
            a, b = _inner_range(merged, k, fps, n)
            off = a / fps - s.start
            s.freezes = [(round(t + off, 3), d) for t, d in _freezes(diff, luma, a, b, fps, suppress | strobe_mask)]

    color, letterbox = _color_profile(rgb, black, merged, fps)
    if mot is not None:
        _deep_effects(path, merged, fps, mot)

    beats = None
    if ff.has_audio(path):
        try:
            beats = detect(path)
        except ff.FFmpegError:
            beats = None
    return Analysis(width=w, height=h, duration=dur, fps=fps, shots=merged, beats=beats,
                    color=color, letterbox=letterbox)


# --------------------------------------------------------------------------- #
# Técnicas: estroboscopio, barrido, zoom a través del corte, congelación, color, layouts
# --------------------------------------------------------------------------- #

def _inner_range(shots: list[Shot], k: int, fps: float, n: int) -> tuple[int, int]:
    """Fotogramas [a, b) de un plano sin sus transiciones de entrada y de salida."""
    s = shots[k]
    a, b = int(round(s.start * fps)), min(n, int(round(s.end * fps)))
    tr = s.transition
    if tr.get("type") not in (None, "cut"):
        a += int(tr.get("duration", 0) * fps / (2 if tr.get("type") in CENTERED else 1) + 0.5)
    nxt = shots[k + 1].transition if k + 1 < len(shots) else {}
    if nxt.get("type") in CENTERED:
        b -= int(nxt.get("duration", 0) * fps / 2 + 0.5)
    return a, max(a, b)


def _strobe_regions(luma: np.ndarray, fr: np.ndarray, fps: float) -> list[dict]:
    """Parpadeos regulares (al menos 3) a negro o a blanco sobre la misma imagen."""
    n = len(luma)
    out: list[dict] = []
    max_pulse = max(2, int(round(0.2 * fps)))
    for color, sig in (("black", luma), ("white", 255.0 - luma)):
        lmax = np.array([sig[max(0, i - 3):i + 4].max() for i in range(n)])
        dark = (sig < 0.6 * lmax) & (lmax > 25)
        # pulsos cortos; entre uno y otro se ve la misma imagen (si cambia, es un corte)
        pulses = [(a, b) for a, b in _runs(dark) if b - a <= max_pulse and a > 0 and b < n
                  and np.abs(fr[a - 1] - fr[b]).mean() <= 25]
        groups: list[list[tuple[int, int]]] = []
        for p in pulses:
            g = groups[-1] if groups else None
            gap = p[0] - g[-1][0] if g else 0
            regular = g is not None and (len(g) < 2 or abs(gap - (g[-1][0] - g[-2][0])) <= max(1.5, 0.35 * gap))
            if g and gap <= 0.4 * fps and regular:
                g.append(p)
            elif g and gap <= 0.4 * fps:  # cambia el ritmo: puede empezar otra serie con el anterior
                groups.append([g[-1], p])
            else:
                groups.append([p])
        for g in groups:
            if len(g) < 3:
                continue
            gaps = np.diff([p[0] for p in g]).astype(float)
            if gaps.min() < 2 or gaps.std() > 0.35 * gaps.mean():
                continue
            a0, b1 = g[0][0], g[-1][1]
            on = np.zeros(b1 - a0, bool)
            for a, b in g:
                on[a - a0:b - a0] = True
            lit_v = float(np.mean(sig[a0:b1][~on])) if (~on).any() else float(lmax[a0])
            amount = float(np.clip(1 - np.mean(sig[a0:b1][on]) / max(lit_v, 1.0), 0.1, 1.0))
            per = float(gaps.mean())
            out.append({"a": a0, "b": b1, "rate": round(fps / per, 2),
                        "duration": round(min(len(g) * per, b1 - a0 + per) / fps, 3),
                        "amount": round(amount, 2), "duty": round(float(np.mean([b - a for a, b in g])) / per, 2),
                        "color": color})
    # Entre destellos negros, los fotogramas con imagen también parecen destellos blancos (y al
    # revés): si se solapan, el estroboscopio de verdad es el que empieza antes.
    out.sort(key=lambda r: (r["a"], -(r["b"] - r["a"])))
    kept: list[dict] = []
    for r in out:
        if not any(r["a"] < k["b"] and k["a"] < r["b"] for k in kept):
            kept.append(r)
    return kept


def _whip_at(mot: dict, fr: np.ndarray, diff: np.ndarray, c: int, n: int, fps: float) -> dict | None:
    """¿El cambio brusco cerca del fotograma `c` es el centro de un barrido? Hace falta un cambio
    de plano con desenfoque direccional a ambos lados del corte, y desplazamiento en un solo
    sentido de ese eje o desenfoque claramente en un solo eje. Devuelve {cut, a, b, direction}."""
    from .motionfx import MH, MW

    k = int(round(0.4 * fps))
    pre = slice(max(0, c - int(fps)), max(0, c - k))
    post = slice(min(n, c + k), min(n, c + int(fps)))
    if pre.stop - pre.start < 4 or post.stop - post.start < 4 or c - k < 1 or c + k >= n:
        return None
    for axis, g_al, g_cr, d, size in (("h", mot["gx"], mot["gy"], mot["dx"], MW),
                                      ("v", mot["gy"], mot["gx"], mot["dy"], MH)):
        base_al = (float(np.median(g_al[pre])), float(np.median(g_al[post])))
        base_cr = (float(np.median(g_cr[pre])), float(np.median(g_cr[post])))
        if min(base_al) < 0.5 or min(base_cr) < 0.3:
            continue  # imagen sin detalle: no se puede medir el desenfoque
        # más borroso que los DOS planos (una mezcla de un plano liso y uno con detalle no lo es)
        rel_al = g_al[c - k:c + k] / min(base_al)
        rel_cr = g_cr[c - k:c + k] / min(base_cr)
        blur = rel_al < 0.6
        if blur.sum() < 4:
            continue
        idx = np.nonzero(blur)[0]
        lo, hi = c - k + int(idx[0]), c - k + int(idx[-1]) + 1
        moving = [float(d[i]) for i in range(lo, hi) if abs(d[i]) >= 2]
        sign = float(np.sign(np.sum(np.sign(moving)))) if moving else 0.0
        sign = sign or -1.0

        def going(i: int) -> bool:
            r = g_al[i] / min(base_al)
            return bool(np.sign(d[i]) == sign and abs(d[i]) >= 2 or r < 0.8)

        # el barrido empieza donde la imagen ya se mueve (o se emborrona) en ese sentido
        # (se tolera un fotograma suelto en el que la medida del desplazamiento falla)
        while lo - 1 >= c - k and (going(lo - 1) or (lo - 2 >= c - k and going(lo - 2))):
            lo -= 1
        while hi < c + k and (going(hi) or (hi + 1 < c + k and going(hi + 1))):
            hi += 1
        if lo < 1 or hi >= n:
            continue
        # Dónde cambia de plano: el perfil a lo largo del eje del barrido no depende del
        # desplazamiento, así que cada fotograma se parece al plano del que sale o al que llega.
        ax = 2 if axis == "h" else 1
        prof = fr[lo - 1:hi + 1].mean(axis=ax)
        d_prev = np.abs(prof - prof[0]).mean(axis=1)
        d_next = np.abs(prof - prof[-1]).mean(axis=1)
        if np.abs(fr[lo - 1] - fr[hi]).mean() < 20:
            continue  # al terminar se ve lo mismo: es un movimiento brusco dentro del plano
        total = float(np.abs(prof[0] - prof[-1]).mean())
        if total >= 8:
            nxt = np.nonzero(d_next[1:-1] < d_prev[1:-1])[0]
            cut = lo + int(nxt[0]) if len(nxt) else lo
            if cut - lo < 2 or hi - cut < 2:
                continue
            # el cambio es de golpe: antes del corte todo se parece al plano que sale y después al
            # que llega (en un slide se ven trozos de los dos a la vez y el perfil cambia poco a poco)
            if np.median(d_prev[1:cut - lo + 1]) > 0.35 * total or \
                    np.median(d_next[cut - lo + 1:-1]) > 0.35 * total:
                continue
        else:  # los dos planos tienen el mismo perfil en ese eje: el corte, en el centro del desenfoque
            cut = c - k + (int(idx[0]) + int(idx[-1]) + 1) // 2
        sel = range(lo, hi)
        before = [i for i in sel if c - k <= i < cut and blur[i - (c - k)]]
        after = [i for i in sel if i >= cut and i < c + k and blur[i - (c - k)]]
        if len(before) < 2 or len(after) < 2:
            continue  # el desenfoque tiene que estar a los dos lados del corte
        core = range(before[0], after[-1] + 1)
        r_al = np.mean([rel_al[i - (c - k)] for i in core])
        r_cr = np.mean([rel_cr[i - (c - k)] for i in core])
        big = [float(d[i]) for i in range(lo, hi) if abs(d[i]) >= 0.05 * size]
        shift_ok = len(big) >= 3 and abs(np.sign(big).sum()) >= 0.8 * len(big)
        # va rápido en ese eje (al menos en el arranque o en la llegada, donde aún se puede medir)
        if len(big) < 2 or not (r_al < 0.8 * r_cr or shift_ok):
            continue
        # un barrido va en un solo sentido; idas y vueltas son un tirón o una sacudida de cámara
        # (los fotogramas pegados al corte mezclan los dos planos y su medida no vale)
        clear = [float(d[i]) for i in range(lo, hi) if abs(d[i]) >= 3 and abs(i - cut) > 1]
        if clear and abs(np.sum(clear)) < 0.7 * np.sum(np.abs(clear)):
            continue
        if axis == "h":
            direction = "left" if sign < 0 else "right"
        else:
            direction = "top" if sign < 0 else "bottom"
        return {"cut": cut, "a": lo, "b": hi, "direction": direction}
    return None


def _zoom_through(scale: np.ndarray, c: int, fps: float) -> tuple[float, int, int] | None:
    """Zoom a través del corte en el fotograma `c`: antes, la imagen se amplía cada vez más
    rápido hasta el corte; después, el plano nuevo se va reduciendo (entra ampliado y se
    asienta). Devuelve (duración, primer fotograma, último + 1) o None."""
    n = len(scale)
    k = int(round(0.45 * fps))
    if c < 3 or c + 3 >= n:
        return None
    # antes del corte (puede haber 1-2 fotogramas mezclados por el desenfoque de movimiento)
    first, zin, gap = None, 1.0, 0
    for i in range(c - 1, max(-1, c - k), -1):
        if scale[i] > 1.01:
            first, zin, gap = i, zin * scale[i], 0
        else:
            gap += 1
            if gap > (3 if first is None else 1):
                break
    if scale[c] > 1.01:
        return None  # todavía es el zoom de salida: el corte viene después
    last, zout, gap = None, 1.0, 0
    for i in range(c, min(n, c + k)):
        if scale[i] < 0.99:
            last, zout, gap = i, zout * scale[i], 0
        else:
            gap += 1
            if gap > (3 if last is None else 1):
                break
    if first is None or last is None or zin < 1.2 or zout > 0.87:
        return None
    return round((last - first + 1) / fps, 3), first, last + 1


def _freezes(diff: np.ndarray, luma: np.ndarray, a: int, b: int, fps: float,
             ignore: np.ndarray) -> list[tuple[float, float]]:
    """Imagen congelada dentro de un plano: varios fotogramas idénticos seguidos (al menos
    0.2 s, más que la repetición de fotogramas de una cámara lenta) con movimiento antes
    (y después, si no es el final del plano). Devuelve [(t relativo, duración)]."""
    if b - a < 8:
        return []
    d = diff[a:b]
    still = (d < 0.2) & (luma[a:b] > 12) & ~ignore[a:b]
    still[0] = False  # el primer fotograma del plano compara con el plano anterior
    moving = d[1:][~still[1:] & ~ignore[a + 1:b]]
    if len(moving) < max(4, 0.3 * (b - a)) or np.median(moving) < 0.4:
        return []  # plano casi fijo: no se distingue una congelación
    out = []
    for i, j in _runs(still):
        if (j - i) < max(6, int(round(0.2 * fps))):
            continue
        prev_m = d[max(1, i - 4):i]
        if len(prev_m) < 2 or np.median(prev_m) < 0.4:
            continue  # sin movimiento antes: es un tramo quieto del propio clip
        if j < len(d):
            post_m = d[j:j + 4]
            if np.median(post_m) < 0.4:
                continue
        out.append((round((i - 1) / fps, 3), round((j - i) / fps, 3)))
    return out


def _color_shots(chroma: np.ndarray, luma: np.ndarray, fps: float, shots: list[Shot],
                 ignore: np.ndarray, flashes: list[int]) -> None:
    """Planos en blanco y negro (o muy desaturados respecto al resto del video)."""
    n = len(chroma)
    noflash = np.ones(n, bool)
    for f in flashes:
        noflash[max(0, f - 3):f + 6] = False
    meds: dict[int, float] = {}
    for k, s in enumerate(shots):
        if s.kind != "full":
            continue
        a, b = int(round(s.start * fps)), min(n, int(round(s.end * fps)))
        ok = (luma[a:b] > 20) & ~ignore[a:b] & noflash[a:b]
        if ok.sum() >= 4:
            meds[k] = float(np.median(chroma[a:b][ok]))
    for k, m in meds.items():
        others = [v for j, v in meds.items() if j != k]
        ref = float(np.median(others)) if others else 0.0
        if m < 6:
            shots[k].grade = "bw"
        elif ref > 30 and m < 0.3 * ref:
            shots[k].grade = "desaturated"


def _bw_flash_runs(chroma: np.ndarray, luma: np.ndarray, fr: np.ndarray, fps: float,
                   ignore: np.ndarray) -> list[tuple[int, int]]:
    """Tramos cortos (<= 0.4 s) sin color en mitad de imagen en color, con la misma imagen
    antes y después (no es un plano distinto ni un flash blanco)."""
    n = len(chroma)
    ref = np.array([np.median(chroma[max(0, i - 15):i + 16]) for i in range(n)])
    gray = (chroma < 0.3 * ref) & (ref > 8) & (luma > 8) & ~ignore
    out = []
    for a, b in _runs(gray):
        if a == 0 or b >= n or b - a > 0.4 * fps:
            continue
        if chroma[a - 1] < 0.6 * ref[a] or chroma[b] < 0.6 * ref[a]:
            continue  # antes y después hay color
        if np.abs(fr[a - 1] - fr[b]).mean() > 25:
            continue  # la misma imagen a los dos lados
        out.append((a, b))
    return out


def _zone_parts(fr: np.ndarray, layout: str) -> list[np.ndarray]:
    """Trozos de la imagen por zona: rows = 3 franjas, columns = 3 columnas, grid = 4 cuadrantes."""
    if layout == "rows":
        return [fr[:, i * AH // 3:(i + 1) * AH // 3] for i in range(3)]
    if layout == "columns":
        return [fr[:, :, i * AW // 3:(i + 1) * AW // 3] for i in range(3)]
    return [fr[:, r * AH // 2:(r + 1) * AH // 2, c * AW // 2:(c + 1) * AW // 2] for r in range(2) for c in range(2)]


def _zones(fr: np.ndarray, layout: str) -> np.ndarray:
    """Brillo medio por zona (n, zonas)."""
    return np.stack([p.mean(axis=(1, 2)) for p in _zone_parts(fr, layout)], axis=1)


def _layout_fit(fr: np.ndarray, layout: str, a: int, b: int) -> float:
    """Qué tan bien encaja [a, b) con paneles de `layout` a medio aparecer (0..1): en casi todos
    los fotogramas hay zonas apagadas y zonas con imagen, las apagadas son negras del todo y
    las encendidas no tienen grandes trozos negros (si no, el panel tiene otra forma)."""
    parts = _zone_parts(fr[a:b], layout)
    z = np.stack([p.mean(axis=(1, 2)) for p in parts], axis=1)
    lit = z > 10
    partial = ~lit.all(axis=1) & lit.any(axis=1)
    if partial.mean() < 0.9:
        return 0.0
    pure = []
    for zi, p in enumerate(parts):
        dark = (p < 6).mean(axis=(1, 2))  # negro de verdad (el fondo), no zonas oscuras de la imagen
        pure.append(float(np.where(lit[:, zi], 1 - dark, dark).mean()))
    return float(np.mean(pure))


def _relabel_layouts(fr: np.ndarray, fps: float, shots: list[Shot]) -> dict[int, str]:
    """Planos con paneles en columnas o en cuadrícula a medio aparecer (las franjas
    horizontales ya las detecta el análisis por tercios). Devuelve {id(plano): tipo anterior}."""
    n = len(fr)
    before: dict[int, str] = {}
    for s in shots:
        if s.kind not in ("full", "stack"):
            continue
        a, b = int(round(s.start * fps)), min(n, int(round(s.end * fps)))
        if b - a < 4:
            continue
        fits = {lay: _layout_fit(fr, lay, a, b) for lay in ("columns", "grid")}
        lay = max(fits, key=fits.get)
        rows = _layout_fit(fr, "rows", a, b) if s.kind == "stack" else 0.0
        if fits[lay] > 0.85 and fits[lay] > rows + 0.1:
            before[id(s)] = s.kind
            s.kind, s.layout = "stack", lay
    return before


def _other_layouts(fr: np.ndarray, fps: float, shots: list[Shot], before: dict[int, str]) -> list[Shot]:
    """Junta las apariciones sucesivas de paneles en columnas / cuadrícula (cada panel nuevo
    llega como un corte) en un único plano 'stack'."""
    out: list[Shot] = []
    zone_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for s in shots:
        prev = out[-1] if out else None
        if prev and prev.kind == "stack" and prev.layout != "rows" and s.transition["type"] == "cut" \
                and (s.kind == "full" or (s.kind == "stack" and s.layout == prev.layout)):
            if prev.layout not in zone_cache:
                zone_cache[prev.layout] = (
                    _zones(fr, prev.layout) > 10,
                    _zones(np.concatenate([fr[:1] * 0, np.abs(np.diff(fr, axis=0))]), prev.layout))
            zl, zd = zone_cache[prev.layout]
            if _is_stack_growth(zl, zd, fps, prev, s):
                prev.panels.append(round(s.start - prev.start, 3))
                prev.end = s.end
                continue
        if s.kind == "stack" and s.layout != "rows":
            s.panels = [0.0]
        out.append(s)
    for s in out:  # un solo panel no es una pantalla dividida (puede ser un plano con un lado negro)
        if s.kind == "stack" and s.layout != "rows" and len(s.panels) < 2:
            s.kind, s.layout = before.get(id(s), "full"), "rows"
            s.panels = [0.0] if s.kind == "stack" else []
    return out


def _deep_effects(path: str, shots: list[Shot], fps: float, mot: dict) -> None:
    """Zooms de golpe, temblores, cambios de velocidad y textos dentro de cada plano."""
    from . import motionfx

    n = len(mot["raw"])
    for k, s in enumerate(shots):
        if s.kind != "full":
            continue
        a, b = _inner_range(shots, k, fps, n)  # las transiciones no cuentan como efecto
        if s.glitch_at is not None:  # el glitch empieza antes de que se note el verde
            b = min(b, int((s.glitch_at - 0.4) * fps))
        # lo que cae en una congelación, un estroboscopio o un destello en B/N no es movimiento
        # de cámara: esos fotogramas se sustituyen por valores neutros antes de medir
        busy = [(f[0], f[0] + f[1]) for f in s.freezes] + \
               [(st["at"], st["at"] + st["duration"]) for st in s.strobes] + \
               [(f[0], f[0] + f[1]) for f in s.bw_flashes]
        m = mot
        if busy and b > a:
            m = {key: v.copy() for key, v in mot.items()}
            for lo, hi in busy:
                i0 = max(a, int((s.start + lo) * fps) - 1)
                i1 = min(b, int((s.start + hi) * fps) + 2)
                if i1 > i0:
                    m["resid"][i0:i1] = np.median(mot["resid"][a:b])
                    m["raw"][i0:i1] = np.median(mot["raw"][a:b])
                    m["dx"][i0:i1] = m["dy"][i0:i1] = 0
                    m["scale"][i0:i1] = 1.0
        r = motionfx.detect_in_shot(m, a, b, fps)
        off = a / fps - s.start

        def free(t0: float, d: float = 0.0) -> bool:
            return not any(t0 < hi + 0.1 and t0 + d > lo - 0.1 for lo, hi in busy)

        s.punches = [(round(t + off, 3), amt) for t, amt in r["punches"] if free(t + off)]
        s.shakes = [(round(t + off, 3), d, amt) for t, d, amt in r["shakes"] if free(t + off, d)]
        s.speed = [(round(t + off, 3), d, sp) for t, d, sp in r["speed"] if free(t + off, d)]
    try:
        texts = motionfx.text_regions(path, fps, [(s.start, s.end) for s in shots])
    except ff.FFmpegError:
        texts = []
    for t in texts:
        mid = (t["start"] + t["end"]) / 2
        for s in shots:
            if s.start <= mid < s.end:
                s.texts.append({"start": round(max(0.0, t["start"] - s.start), 2),
                                "end": round(min(s.duration, t["end"] - s.start), 2),
                                "box": [float(v) for v in t["box"]], "text": t["text"]})
                break


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
        if s.grade:  # los planos en blanco y negro no cuentan para el look de color
            keep[int(s.start * fps):int(s.end * fps) + 1] = False
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
            "slideright": "desliza desde la izquierda", "zoomthrough": "zoom a través del corte",
            "whip": "barrido"}
DIR_NAMES = {"left": "hacia la izquierda", "right": "hacia la derecha", "top": "hacia arriba", "bottom": "hacia abajo"}
LAYOUT_NAMES = {"columns": "en columnas", "grid": "en cuadrícula"}
GRADE_NAMES = {"bw": "en blanco y negro", "desaturated": "desaturado"}


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
                if tr["type"] == "whip":
                    name += " " + DIR_NAMES.get(tr.get("direction", "left"), "")
            det.append(name)
        if s.kind == "stack":
            lay = LAYOUT_NAMES.get(s.layout)
            det.append("paneles " + (lay + " " if lay else "") + "en +" + ", +".join(f"{p:.2f}s" for p in s.panels))
        if s.grade:
            det.append(GRADE_NAMES.get(s.grade, s.grade))
        if s.flashes:
            det.append("flash en +" + ", +".join(f"{f:.2f}s" for f in s.flashes))
        if s.glitch_at is not None:
            det.append(f"glitch desde {s.glitch_at:.2f}s")
        if s.punches:
            det.append("zoom de golpe en +" + ", +".join(f"{t:.2f}s" for t, _ in s.punches))
        for t, d, amt in s.shakes:
            det.append(f"temblor +{t:.2f}s ({d:.2f}s)")
        for t, d, sp in s.speed:
            det.append(f"cámara lenta x{sp:.2f} en +{t:.2f}s ({d:.2f}s)")
        for t, d in s.freezes:
            det.append(f"imagen congelada +{t:.2f}s ({d:.2f}s)")
        for st in s.strobes:
            det.append(f"estroboscopio{' blanco' if st['color'] == 'white' else ''} +{st['at']:.2f}s "
                       f"({st['duration']:.2f}s, {st['rate']:.1f} Hz)")
        if s.bw_flashes:
            det.append("destello en B/N en +" + ", +".join(f"{t:.2f}s" for t, _ in s.bw_flashes))
        for tx in s.texts:
            det.append(f"texto «{tx['text'] or '?'}» +{tx['start']:.2f}-{tx['end']:.2f}s")
        nb = nearest_beat(a.beats, s.start)
        if nb and i > 0:
            det.append(f"beat {nb[0]} ({nb[1] * 1000:+.0f} ms)")
        lines.append(f"{i:>2}  {s.start:7.2f}  {s.end:7.2f}  {s.duration:6.2f}  {s.kind:<6}  "
                     f"{s.motion:4.2f}  {'; '.join(det)}")
    return "\n".join(lines)


def _text_spec(tx: dict, height: int) -> dict:
    x, y, w, h = tx["box"]
    return {"text": tx["text"] or "TEXTO", "start": tx["start"], "end": tx["end"],
            "x": round(x + w / 2, 3), "y": round(y + h / 2, 3), "size": max(16, int(h * height * 1.15))}


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
            if s.layout != "rows":
                seg["layout"] = s.layout
        else:
            n += 1
            seg = {"type": "clip", "src": f"clip{n}.mov", "in": 0.0, "duration": d}
            effects = [{"type": "flash", "times": s.flashes}] if s.flashes else []
            effects += [{"type": "punch", "times": [t], "amount": amt} for t, amt in s.punches]
            effects += [{"type": "shake", "at": t, "duration": d, "amount": amt} for t, d, amt in s.shakes]
            effects += [{"type": "freeze", "at": t, "duration": d} for t, d in s.freezes]
            effects += [{"type": "strobe", "at": st["at"], "duration": st["duration"], "rate": st["rate"],
                         "amount": st["amount"], "duty": st["duty"], "color": st["color"]} for st in s.strobes]
            if s.bw_flashes:
                effects.append({"type": "bw_flash", "times": [t for t, _ in s.bw_flashes],
                                "duration": round(max(d for _, d in s.bw_flashes), 3)})
            if s.grade:
                seg["grade"] = s.grade
            if s.speed:
                seg["ramp"] = []
                for t_sp, d_sp, sp in s.speed:
                    seg["ramp"] += [[t_sp, sp], [round(t_sp + d_sp, 3), 1.0]]
            if s.texts:
                seg["texts"] = [_text_spec(tx, a.height) for tx in s.texts]
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
