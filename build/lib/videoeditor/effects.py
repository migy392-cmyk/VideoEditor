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
    "desaturated": "hue=s=0.35,eq=contrast=1.1",
    "vivid": "eq=contrast=1.08:saturation=1.35",
    "faded": "eq=contrast=0.9:brightness=0.03:saturation=0.8,curves=all='0/0.06 1/0.94'",
    # Cine: curva en S suave, negros levantados, sombras frías y altas luces cálidas, algo menos de saturación
    "film": (
        "curves=all='0/0.045 0.25/0.26 0.5/0.53 0.75/0.79 1/0.96',"
        "colorbalance=rs=-0.02:bs=0.03:rh=0.04:gh=0.015:bh=-0.03,"
        "eq=saturation=0.9,vignette=angle=PI/7"
    ),
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
        f"{amt}*gte(it\\,{t:.4f})*exp(-(it-{t:.4f})/{decay})" for t in times
    ) or "0"
    # zoompan hace el zoom desde el centro (scale+crop dinámicos lo anclaban arriba a la izquierda)
    return [f"zoompan=z='1+{terms}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={w}x{h}:fps={fps}",
            "setsar=1"]


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


_HAL = [0]


def fx_halation(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Halo rojizo alrededor de las luces fuertes, como en la película. Parámetro: amount (0.35)."""
    amt = float(e.get("amount", 0.35))
    _HAL[0] += 1
    k = _HAL[0]
    sigma = max(4, int(min(w, h) * 0.012))
    # la mezcla "screen" debe hacerse en RGB (en YUV tiñe la imagen de morado)
    return [f"format=gbrp,split[hal{k}a][hal{k}b];[hal{k}b]curves=all='0/0 0.72/0 1/1',gblur=sigma={sigma},"
            f"colorchannelmixer=rr=1:gg=0.35:bb=0.15[hal{k}c];[hal{k}a][hal{k}c]blend=all_mode=screen:all_opacity={amt}",
            "format=yuv420p"]


def fx_film_grain(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Grano de película (cambia cada fotograma, más visible en medios tonos). Parámetro: amount (12)."""
    amt = int(e.get("amount", 12))
    return [f"noise=c0s={amt}:c0f=t+u"]


def fx_stabilize(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Estabiliza la cámara (se resuelve en el render con vidstab en dos pasadas)."""
    return []


def fx_freeze(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Imagen congelada: se detiene en un fotograma y luego el plano sigue desde ahí.
    Parámetros: at (segundos o "start"/"end"), duration (0.5). Se aplica antes que el resto
    de efectos, así un flash o un zoom de golpe en el mismo instante se ven sobre la imagen quieta.
    Las congelaciones de un mismo segmento se combinan en `_freeze_chain`."""
    return _freeze_chain([e], seg_dur, fps)


def freeze_windows(effects: list[Effect], seg_dur: float) -> list[tuple[float, float]]:
    """Tramos [inicio, fin) congelados de un segmento (en segundos del segmento)."""
    wins = []
    for e in effects:
        if e.type == "freeze":
            a, b = _window(e, seg_dur, 0.5)
            if b - a > 0.01:
                wins.append((a, b))
    return sorted(wins)


def _freeze_chain(effects: list[Effect], seg_dur: float, fps: int) -> list[str]:
    # Se retrasan los fotogramas posteriores a cada congelación y `fps` rellena el hueco
    # repitiendo el último fotograma anterior: la imagen se queda quieta exactamente ahí.
    terms, shift = [], 0.0
    for a, b in freeze_windows(effects, seg_dur):
        src_t = a - shift  # instante del clip (antes de las congelaciones anteriores)
        terms.append(f"{b - a:.4f}*gte(T\\,{src_t + 0.5 / fps:.4f})")
        shift += b - a
    if not terms:
        return []
    return [f"setpts='PTS+({'+'.join(terms)})/TB'", f"fps={fps}"]


def fx_strobe(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Estroboscopio: la imagen parpadea (se oscurece o se ilumina) a ritmo fijo.
    Parámetros: at, duration (1.0), rate (parpadeos por segundo, 8), amount (0..1, 1 = negro total),
    color ("black" o "white"), duty (fracción de cada ciclo apagada, 0.5)."""
    a, b = _window(e, seg_dur, 1.0)
    rate = max(0.5, float(e.get("rate", 8.0)))
    amt = min(1.0, max(0.0, float(e.get("amount", 1.0))))
    duty = min(0.9, max(0.1, float(e.get("duty", 0.5))))
    color = str(e.get("color", "black"))
    per = 1.0 / rate
    on = f"{_between(a, b - 0.5 / fps)}*lt(mod(t-{a:.4f}+0.001\\,{per:.5f})\\,{per * duty:.5f})"
    return [f"drawbox=x=0:y=0:w=iw:h=ih:color={color}@{amt:.3f}:t=fill:enable='{on}'"]


def fx_bw_flash(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Destello en blanco y negro: unos fotogramas sin color (p. ej. en los beats).
    Parámetros: times [s], duration (0.12)."""
    d = max(1.0 / fps, float(e.get("duration", 0.12)))
    en = "+".join(_between(t, t + d - 0.5 / fps) for t in _times(e)) or "0"
    return [f"hue=s=0:enable='{en}'", f"eq=contrast=1.2:enable='{en}'"]


def _phase(e: Effect, seg_dur: float) -> tuple[float, float, bool]:
    """Ventana de la mitad de una transición centrada: (inicio, duración, ¿es la salida?)."""
    d = min(seg_dur, max(0.02, float(e.get("duration", 0.2))))
    out = e.get("phase", "out") == "out"
    return (seg_dur - d if out else 0.0), d, out


def fx_tr_zoom(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Mitad de la transición `zoomthrough` (uso interno del render).
    phase "out": zoom que acelera hasta el corte; "in": entra ampliado y se asienta."""
    a, d, out = _phase(e, seg_dur)
    amt = float(e.get("amount", 1.0 if out else 0.6))
    u = f"clip((it-{a:.4f})/{d:.4f}\\,0\\,1)"
    # (zoompan con zoom exactamente 1 recorta distinto que con 1.00x: un mínimo fijo evita
    # un salto de un fotograma justo cuando el zoom termina o empieza)
    z = f"1.004+{amt}*pow({u}\\,2)" if out else f"1.004+{amt}*pow(1-{u}\\,2)"
    return [f"zoompan=z='{z}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={w}x{h}:fps={fps}", "setsar=1"]


_WHIP = [0]


def fx_tr_whip(e: Effect, seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    """Mitad de la transición `whip` (uso interno del render): la imagen se desplaza muy rápido
    hacia `direction` (left/right/top/bottom) con desenfoque direccional creciente (salida)
    o decreciente (entrada). La imagen se repite en mosaico, como un barrido real de cámara."""
    a, d, out = _phase(e, seg_dur)
    direction = e.get("direction", "left")
    horiz = direction in ("left", "right")
    size = w if horiz else h
    u = f"clip((t-{a:.4f})/{d:.4f}\\,0\\,1)"
    travel = 0.9 * size
    # o: cuánto se ha desplazado el contenido hacia la izquierda/arriba (en mosaico)
    o = f"{travel:.1f}*pow({u}\\,2)" if out else f"{size}-{travel:.1f}*pow(1-{u}\\,2)"
    if direction in ("right", "bottom"):
        o = f"{size}-({o})"
    _WHIP[0] += 1
    k = _WHIP[0]
    stack = "hstack" if horiz else "vstack"
    crop = f"crop={w}:{h}:x='{o}':y=0" if horiz else f"crop={w}:{h}:x=0:y='{o}'"
    chain = [f"split[wh{k}a][wh{k}b];[wh{k}a][wh{k}b]{stack}", crop]
    # desenfoque direccional por tramos (más fuerte cuanto más rápido va)
    steps = (0.02, 0.05, 0.09) if out else (0.09, 0.05, 0.02)
    for j, frac in enumerate(steps):
        lo, hi = a + d * j / 3, a + d * (j + 1) / 3
        sg = max(1.0, size * frac)
        sx, sy = (sg, 0.5) if horiz else (0.5, sg)
        chain.append(f"gblur=sigma={sx:.1f}:sigmaV={sy:.1f}:enable='{_between(lo, hi)}'")
    return chain


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
    "halation": fx_halation,
    "film_grain": fx_film_grain,
    "stabilize": fx_stabilize,
    "freeze": fx_freeze,
    "strobe": fx_strobe,
    "bw_flash": fx_bw_flash,
    # internos: el render los añade a ambos lados de las transiciones zoomthrough / whip
    "tr_zoom": fx_tr_zoom,
    "tr_whip": fx_tr_whip,
}


INTERNAL = ("tr_zoom", "tr_whip")  # no se ofrecen al usuario


def effect_filters(effects: list[Effect], seg_dur: float, w: int, h: int, fps: int) -> list[str]:
    # Las congelaciones van primero: cambian el tiempo del plano y el resto de efectos
    # (flash, zoom de golpe…) se colocan sobre el resultado.
    out: list[str] = _freeze_chain([e for e in effects if e.type == "freeze"], seg_dur, fps)
    for e in effects:
        if e.type != "freeze":
            out.extend(EFFECTS[e.type](e, seg_dur, w, h, fps))
    return out

