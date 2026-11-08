"""Corrección de color (grades) y efectos, expresados como filtros de ffmpeg.

Cada efecto es una función que recibe el efecto, la duración del segmento y el
tamaño de salida, y devuelve una lista de filtros (strings) que se encadenan
sobre el video del segmento. Los tiempos son relativos al inicio del segmento.
"""
from __future__ import annotations

from typing import Callable

from .project import Effect, ProjectError

# --------------------------------------------------------------------------- #
# Grades / looks de color
# --------------------------------------------------------------------------- #

GRADES: dict[str, str] = {
    "none": "",
    # Look del video de referencia: noche, sombras azuladas, luces cálidas, contraste alto.
    "night_blue": (
        "eq=contrast=1.08:saturation=1.06,"
        "colorbalance=rs=-0.03:bs=0.07:bm=0.03:rh=0.04:gh=0.015:bh=-0.04,"
        "vignette=angle=PI/6"
    ),
    "teal_orange": (
        "eq=contrast=1.1:saturation=1.15,"
        "colorbalance=rs=-0.08:gs=0.02:bs=0.10:rh=0.10:gh=0.03:bh=-0.08"
    ),
    "moody": (
        "eq=contrast=1.18:brightness=-0.05:saturation=0.85:gamma=0.92,"
        "colorbalance=bs=0.06:rh=0.03,vignette=angle=PI/4"
    ),
    "warm": "eq=contrast=1.05:saturation=1.1,colorbalance=rs=0.04:rm=0.05:rh=0.06:bh=-0.06",
    "cold": "eq=contrast=1.06:saturation=0.95,colorbalance=rs=-0.05:bs=0.06:bm=0.04:rh=-0.03:bh=0.04",
    "bw": "hue=s=0,eq=contrast=1.25:brightness=-0.02",
    "vivid": "eq=contrast=1.08:saturation=1.35",
    "faded": "eq=contrast=0.9:brightness=0.03:saturation=0.8,curves=all='0/0.06 1/0.94'",
}


def grade_filters(name: str | None) -> list[str]:
    if not name:
        return []
    f = GRADES.get(name)
    if f is None:
        raise ProjectError(f"grade '{name}' desconocido")
    return [f] if f else []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _window(e: Effect, seg_dur: float, default_dur: float) -> tuple[float, float]:
    """Devuelve (inicio, fin) del efecto dentro del segmento."""
    dur = float(e.get("duration", default_dur))
    at = e.get("at", "end")
    if at == "end":
        start = seg_dur - dur
    elif at == "start":
        start = 0.0
    else:
        start = float(at)
    start = max(0.0, start)
    end = min(seg_dur, start + dur)
    return start, end


def _between(a: float, b: float) -> str:
    return f"between(t\\,{a:.4f}\\,{b:.4f})"


def _times(e: Effect) -> list[float]:
    times = e.get("times")
    if times is None:
        times = [float(e.get("at", 0.0))]
    return [float(t) for t in times]


# --------------------------------------------------------------------------- #
# Efectos
# --------------------------------------------------------------------------- #

def fx_glitch(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Glitch digital tipo 'datamosh': separación RGB, ruido de croma verde,
    bloques pixelados y saltos horizontales. Se intensifica hacia el final.

    Parámetros: at ("end"|"start"|segundos), duration (0.6), intensity (1.0)
    """
    a, b = _window(e, seg_dur, 0.6)
    k = float(e.get("intensity", 1.0))
    mid = a + (b - a) * 0.35
    late = a + (b - a) * 0.7
    shift = max(2, int(round(w * 0.012 * k)))
    jump = max(2, int(round(w * 0.03 * k)))
    block = max(4, int(round(w * 0.018 * k)))
    big = max(block + 2, int(round(w * 0.04 * k)))
    noise = min(100, int(80 * k))
    return [
        # salto horizontal aleatorio por frame
        f"scale={w + 2 * jump}:{h}", "setsar=1",
        f"crop={w}:{h}:x='{jump}+if({_between(a, b)}\\,(random(0)-0.5)*2*{jump}\\,0)':y=0",
        f"rgbashift=rh={-shift}:bh={shift}:gv={shift // 2}:edge=wrap:enable='{_between(a, b)}'",
        f"noise=c1s={noise}:c2s={noise}:c0s={noise // 3}:allf=t:enable='{_between(mid, b)}'",
        f"pixelize=w={block}:h={block}:enable='{_between(mid, late)}'",
        f"pixelize=w={big}:h={max(4, big // 3)}:enable='{_between(late, b)}'",
        # dominante verde como en los fallos de compresión
        f"colorchannelmixer=rr=0.75:gg=1.2:bb=0.75:enable='{_between(mid, b)}'",
    ]


def fx_rgb_split(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Separación RGB constante (o en una ventana). Parámetros: amount (px), at, duration."""
    amount = int(e.get("amount", max(2, w // 120)))
    if "duration" in e.params or "at" in e.params:
        a, b = _window(e, seg_dur, seg_dur)
        en = f":enable='{_between(a, b)}'"
    else:
        en = ""
    return [f"rgbashift=rh={-amount}:bh={amount}:edge=smear{en}"]


def fx_shake(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Temblor de cámara. Parámetros: amount (0..1, por defecto 0.5), at, duration, speed."""
    a, b = _window(e, seg_dur, float(e.get("duration", seg_dur)))
    amt = float(e.get("amount", 0.5))
    speed = float(e.get("speed", 30.0))
    pad = max(4, int(round(min(w, h) * 0.03 * amt))) * 2
    sw, sh = w + pad, h + pad
    half = pad // 2
    en = _between(a, b)
    x = f"{half}+if({en}\\,{half}*sin(t*{speed})*cos(t*{speed * 0.7:.2f})\\,0)"
    y = f"{half}+if({en}\\,{half}*sin(t*{speed * 1.3:.2f}+1)\\,0)"
    return [f"scale={sw}:{sh}", "setsar=1", f"crop={w}:{h}:x='{x}':y='{y}'"]


def fx_punch(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Zoom de golpe que decae (ideal en los beats). Parámetros: times [s], amount (0.12), decay (0.18)."""
    times = _times(e)
    amt = float(e.get("amount", 0.12))
    decay = max(0.02, float(e.get("decay", 0.18)))
    terms = "+".join(
        f"{amt}*gte(t\\,{t:.4f})*exp(-(t-{t:.4f})/{decay})" for t in times
    ) or "0"
    z = f"(1+{terms})"
    return [
        f"scale=w='trunc({w}*{z}/2)*2':h='trunc({h}*{z}/2)*2':eval=frame",
        f"crop={w}:{h}",
    ]


def fx_flash(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Destello blanco en uno o varios instantes. Parámetros: times [s], amount (0.7), decay (0.08)."""
    times = _times(e)
    amt = float(e.get("amount", 0.7))
    decay = max(0.01, float(e.get("decay", 0.08)))
    terms = "+".join(f"{amt}*gte(t\\,{t:.4f})*exp(-(t-{t:.4f})/{decay})" for t in times) or "0"
    return [f"eq=brightness='min(1\\,{terms})':eval=frame"]


def fx_fade_in(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    d = float(e.get("duration", 0.4))
    color = e.get("color", "black")
    return [f"fade=t=in:st=0:d={d}:color={color}"]


def fx_fade_out(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    d = float(e.get("duration", 0.4))
    color = e.get("color", "black")
    return [f"fade=t=out:st={max(0.0, seg_dur - d):.4f}:d={d}:color={color}"]


def fx_blur(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    sigma = float(e.get("amount", 12))
    if "duration" in e.params or "at" in e.params:
        a, b = _window(e, seg_dur, seg_dur)
        return [f"gblur=sigma={sigma}:enable='{_between(a, b)}'"]
    return [f"gblur=sigma={sigma}"]


def fx_grain(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    amt = int(e.get("amount", 10))
    return [f"noise=c0s={amt}:allf=t"]


def fx_sharpen(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    amt = float(e.get("amount", 0.8))
    return [f"unsharp=5:5:{amt}:5:5:0"]


def fx_letterbox(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Barras negras cinematográficas. Parámetro: size (fracción del alto, 0.08)."""
    s = float(e.get("size", 0.08))
    bh = int(h * s)
    return [
        f"drawbox=x=0:y=0:w={w}:h={bh}:color=black:t=fill",
        f"drawbox=x=0:y={h - bh}:w={w}:h={bh}:color=black:t=fill",
    ]


EffectFn = Callable[[Effect, float, int, int, int], list[str]]

EFFECTS: dict[str, EffectFn] = {
    "glitch": fx_glitch,
    "rgb_split": fx_rgb_split,
    "shake": fx_shake,
    "punch": fx_punch,
    "flash": fx_flash,
    "fade_in": fx_fade_in,
    "fade_out": fx_fade_out,
    "blur": fx_blur,
    "grain": fx_grain,
    "sharpen": fx_sharpen,
    "letterbox": fx_letterbox,
}


def effect_filters(effects: list[Effect], seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    out: list[str] = []
    for e in effects:
        out.extend(EFFECTS[e.type](e, seg_dur, w, h, fps))
    return out

