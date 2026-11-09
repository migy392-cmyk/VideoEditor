"""LUTs 3D de color (.cube): generación procedural, looks aprendidos, caché e intensidad.

Un LUT es una tabla 33x33x33 que transforma cada color RGB. Se aplica en el render con el
filtro `lut3d` de ffmpeg, así que es muy barato. Aquí se generan con numpy:

* **Presets** pensados para edits verticales de gym de noche (Reels/TikTok): cada look es una
  lista de operaciones simples y componibles (exposición, curva de contraste sobre la
  luminancia, levantar negros, caída de altas luces, tintes en sombras/luces, ajustes de
  saturación por tono, mezcla de canales…). Las operaciones que podrían estropear la piel
  aceptan `protect`: protegen los tonos de piel (naranjas de saturación media).
* **LUT aprendido** de un estilo (`learned_lut`) o de un video de referencia (`lut_from_video`).
* **Intensidad**: `resolve(spec, strength)` mezcla identidad→look y guarda el resultado en
  `~/.cache/videoeditor/luts`, de modo que el render sólo hace `lut3d=file=...`.

Un LUT se indica (en el proyecto, la CLI o la web) con una de estas formas:

    "noche_azul"       id de un preset (ver `python -m videoeditor luts`)
    "style:gym_noche"  LUT aprendido del color de un estilo
    "mis_luts/x.cube"  ruta a un .cube (o .3dl) propio; también vale un nombre de la
                       carpeta `<workspace>/luts/`

Los presets se publican también como archivos en `videoeditor/luts/*.cube` para usarlos en
CapCut, Premiere o DaVinci. Para regenerarlos: `python -m videoeditor.luts`.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

SIZE = 33
SHIPPED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "luts")
LUT_EXT = (".cube", ".3dl", ".dat", ".m3d")
LUMA = np.array([0.2126, 0.7152, 0.0722])

# Carpeta de trabajo (workspace) registrada por la CLI / el servidor: de ahí salen los LUTs
# propios (`<workspace>/luts`) y los estilos aprendidos (`<workspace>/styles`).
_WORKSPACE: str | None = None


class LutError(ValueError):
    pass


def set_workspace(path: str | None) -> None:
    global _WORKSPACE
    _WORKSPACE = os.path.abspath(path) if path else None


def cache_dir() -> str:
    d = os.environ.get("VIDEOEDITOR_LUT_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "videoeditor", "luts")
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Color: utilidades
# --------------------------------------------------------------------------- #

def identity_lattice(n: int = SIZE) -> np.ndarray:
    """Los n^3 colores de la rejilla, con el rojo variando más rápido (orden de .cube). Forma (n^3, 3)."""
    g = np.linspace(0.0, 1.0, n)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r.ravel(), gg.ravel(), b.ravel()], axis=1)


def luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ LUMA


def smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    d = mx - mn
    nz = d > 1e-12
    dd = np.where(nz, d, 1.0)
    h = np.zeros_like(mx)
    rc = nz & (mx == r)
    gc = nz & (mx == g) & ~rc
    bc = nz & ~rc & ~gc
    h[rc] = ((g - b) / dd)[rc] % 6
    h[gc] = ((b - r) / dd)[gc] + 2
    h[bc] = ((r - g) / dd)[bc] + 4
    s = np.where(mx > 1e-12, d / np.maximum(mx, 1e-12), 0.0)
    return h * 60.0, s, mx


def hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    h6 = (h % 360.0) / 60.0
    i = np.floor(h6).astype(int) % 6
    f = h6 - np.floor(h6)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=1)


def hue_dist(h: np.ndarray, center: float) -> np.ndarray:
    return np.abs((h - center + 180.0) % 360.0 - 180.0)


def tint_vector(hue: float) -> np.ndarray:
    """Dirección de color (suma de luminancia cero) para un tono dado."""
    t = hsv_to_rgb(np.array([hue]), np.array([1.0]), np.array([1.0]))[0]
    return t - float(luma(t))


def skin_mask(rgb: np.ndarray) -> np.ndarray:
    """1 en tonos de piel: naranjas (~22°) de saturación media, ni muy apagados ni neones."""
    h, s, v = rgb_to_hsv(np.clip(rgb, 0, 1))
    hue = np.exp(-0.5 * (hue_dist(h, 22.0) / 14.0) ** 2)
    sat = smoothstep(0.10, 0.24, s) * (1 - smoothstep(0.62, 0.88, s))
    return hue * sat * smoothstep(0.04, 0.18, v)


def _apply_luma(rgb: np.ndarray, f: Callable[[np.ndarray], np.ndarray], eps: float = 0.02) -> np.ndarray:
    """Aplica una curva a la luminancia conservando el cociente entre canales (no desvía el tono)."""
    y = luma(rgb)
    gain = (f(np.clip(y, 0, 1)) + eps) / (y + eps)
    return rgb * np.minimum(gain, 8.0)[:, None]


def _pchip(x: np.ndarray, y: np.ndarray, xq: np.ndarray) -> np.ndarray:
    """Interpolación cúbica monótona (Fritsch-Carlson); fuera de rango, extrapola con la pendiente final."""
    h = np.diff(x)
    d = np.diff(y) / h
    m = np.zeros_like(y)
    m[0], m[-1] = d[0], d[-1]
    for k in range(1, len(y) - 1):
        m[k] = 0.0 if d[k - 1] * d[k] <= 0 else 2 * d[k - 1] * d[k] / (d[k - 1] + d[k])
    for k in range(len(d)):
        if abs(d[k]) < 1e-12:
            m[k] = m[k + 1] = 0.0
        else:
            a, b = m[k] / d[k], m[k + 1] / d[k]
            s = a * a + b * b
            if s > 9:
                t = 3 / np.sqrt(s)
                m[k], m[k + 1] = t * a * d[k], t * b * d[k]
    idx = np.clip(np.searchsorted(x, xq) - 1, 0, len(h) - 1)
    t = (xq - x[idx]) / h[idx]
    t2, t3 = t * t, t * t * t
    out = ((2 * t3 - 3 * t2 + 1) * y[idx] + (t3 - 2 * t2 + t) * h[idx] * m[idx]
           + (-2 * t3 + 3 * t2) * y[idx + 1] + (t3 - t2) * h[idx] * m[idx + 1])
    return np.where(xq < x[0], y[0] + (xq - x[0]) * m[0], np.where(xq > x[-1], y[-1] + (xq - x[-1]) * m[-1], out))


# --------------------------------------------------------------------------- #
# Operaciones componibles. Cada una recibe (rgb, ctx) y devuelve rgb.
# `ctx["skin"]` es la máscara de piel del color ORIGINAL.
# --------------------------------------------------------------------------- #

Op = Callable[[np.ndarray, dict], np.ndarray]


def _protected(fn: Callable[[np.ndarray], np.ndarray], protect: float) -> Op:
    def op(rgb: np.ndarray, ctx: dict) -> np.ndarray:
        new = fn(rgb)
        if protect <= 0:
            return new
        w = (1.0 - protect * ctx["skin"])[:, None]
        return rgb + (new - rgb) * w
    return op


def exposure(ev: float) -> Op:
    """Sube o baja la exposición en pasos (EV)."""
    return lambda rgb, ctx: rgb * (2.0 ** ev)


def tone(contrast: float = 0.0, pivot: float = 0.35, shadows: float = 0.0, highlights: float = 0.0,
         knee: float | None = None, black: float = 0.0, white: float = 1.0, gamma: float = 1.0) -> Op:
    """Curva de tono sobre la luminancia.

    contrast   curva en S alrededor de `pivot` (-1..1); en vídeo oscuro el pivote va bajo (0.25-0.4)
    shadows    levanta (+) o hunde (-) las sombras medias; highlights, lo mismo en las luces
    knee       a partir de ese nivel las altas luces se comprimen suavemente (caída)
    black      levanta los negros (fade); white: techo de los blancos
    gamma      >1 aclara los medios
    """
    def curve(x: np.ndarray) -> np.ndarray:
        y = x ** (1.0 / gamma)
        y = y + shadows * 6.75 * y * (1 - y) ** 2 + highlights * 6.75 * y ** 2 * (1 - y)
        y = np.clip(y, 0, 1)
        if contrast:
            g = np.log(0.5) / np.log(pivot)
            yg = y ** g
            sg = yg * yg * (3 - 2 * yg)
            y = (yg + contrast * (sg - yg)) ** (1.0 / g)
        if knee is not None:
            over = np.maximum(y - knee, 0.0)
            y = np.where(y > knee, knee + (1 - knee) * (1 - np.exp(-over / (1 - knee))), y)
        return y

    def op(rgb: np.ndarray, ctx: dict) -> np.ndarray:
        out = _apply_luma(np.clip(rgb, 0, None), curve)
        if black or white != 1.0:
            out = black + np.clip(out, 0, 1) * (white - black)
        return out
    return op


def split_tone(shadow: tuple[float, float] | None = None, highlight: tuple[float, float] | None = None,
               balance: float = 0.0, protect: float = 0.0) -> Op:
    """Tinte en sombras y en luces: (tono en grados, cantidad). `balance` desplaza el reparto
    (positivo = más zona de sombras). El negro puro no se tiñe."""
    sv = tint_vector(shadow[0]) * shadow[1] if shadow else None
    hv = tint_vector(highlight[0]) * highlight[1] if highlight else None

    def fn(rgb: np.ndarray) -> np.ndarray:
        y = luma(np.clip(rgb, 0, 1))
        keep = 0.35 + 0.65 * smoothstep(0.0, 0.05, y)
        out = rgb.copy()
        if sv is not None:
            out += ((1 - smoothstep(0.05, 0.6 + balance, y)) * keep)[:, None] * sv
        if hv is not None:
            out += (smoothstep(0.3 + balance, 0.95, y) * keep)[:, None] * hv
        return out
    return _protected(fn, protect)


def hue_adjust(*adj: tuple[float, float, float, float, float], protect: float = 0.0) -> Op:
    """Ajustes por tono. Cada uno: (tono central °, anchura °, giro de tono °, saturación ±, brillo ±).
    Sólo afecta a colores con algo de saturación (los grises no se tocan)."""
    def fn(rgb: np.ndarray) -> np.ndarray:
        h, s, v = rgb_to_hsv(np.clip(rgb, 0, 1))
        base = smoothstep(0.04, 0.25, s) * smoothstep(0.0, 0.04, v)
        h2, s2, v2 = h.copy(), s.copy(), v.copy()
        for center, width, dh, ds, dv in adj:
            w = np.exp(-0.5 * (hue_dist(h, center) / width) ** 2) * base
            h2 += dh * w
            s2 *= 1 + ds * w
            v2 *= 1 + dv * w
        return hsv_to_rgb(h2, np.clip(s2, 0, 1), np.clip(v2, 0, 1.5))
    return _protected(fn, protect)


def saturation(amount: float, vibrance: float = 0.0, protect: float = 0.0) -> Op:
    """Saturación global (1 = igual). `vibrance` mueve menos los colores que ya son muy saturados."""
    def fn(rgb: np.ndarray) -> np.ndarray:
        y = luma(rgb)[:, None]
        _, s, _ = rgb_to_hsv(np.clip(rgb, 0, 1))
        k = 1 + (amount - 1) * (1 - vibrance * s)[:, None]
        return y + (rgb - y) * k
    return _protected(fn, protect)


def channel_mix(matrix: list[list[float]], protect: float = 0.0) -> Op:
    """Mezcla de canales: cada fila da el canal de salida (R, G, B) en función de (R, G, B)."""
    m = np.array(matrix, dtype=float)
    return _protected(lambda rgb: rgb @ m.T, protect)


def mono(weights: tuple[float, float, float] = (0.3, 0.55, 0.15)) -> Op:
    """Blanco y negro con los pesos de cada canal (como un filtro de color delante del objetivo)."""
    w = np.array(weights, dtype=float)
    w = w / w.sum()
    return lambda rgb, ctx: np.repeat((rgb @ w)[:, None], 3, axis=1)


def bleach(amount: float) -> Op:
    """Bleach bypass: mezcla con su versión en B/N con contraste (overlay), como la plata sin blanquear."""
    def op(rgb: np.ndarray, ctx: dict) -> np.ndarray:
        c = np.clip(rgb, 0, 1)
        y = luma(c)[:, None]
        ov = np.where(c < 0.5, 2 * c * y, 1 - 2 * (1 - c) * (1 - y))
        return rgb + (ov - rgb) * amount
    return op


def lift_color(hue: float, amount: float) -> Op:
    """Tiñe sólo los negros (levanta el negro hacia un color): efecto película desvanecida."""
    v = tint_vector(hue) * amount

    def op(rgb: np.ndarray, ctx: dict) -> np.ndarray:
        y = luma(np.clip(rgb, 0, 1))
        return rgb + (1 - smoothstep(0.0, 0.35, y))[:, None] * v
    return op


@dataclass
class Look:
    id: str
    name: str
    description: str
    ops: list[Op] = field(default_factory=list)

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        ctx = {"skin": skin_mask(rgb)}
        out = rgb.astype(float)
        for op in self.ops:
            out = op(np.clip(out, -0.5, 2.0), ctx)
        return np.clip(out, 0.0, 1.0)

    def lattice(self, strength: float = 1.0, n: int = SIZE) -> np.ndarray:
        return blend(identity_lattice(n), self.apply(identity_lattice(n)), strength)


def blend(identity: np.ndarray, look: np.ndarray, strength: float) -> np.ndarray:
    """Intensidad 0..1: 0 = sin cambios, 1 = el look completo."""
    s = min(1.0, max(0.0, float(strength)))
    return np.clip(identity + (look - identity) * s, 0.0, 1.0)


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #

def _presets() -> list[Look]:
    return [
        Look("noche_azul", "Noche azul",
             "El look de Gym noche: sombras azuladas, luces cálidas y contraste alto.",
             [tone(contrast=0.38, pivot=0.30, shadows=0.10, knee=0.82),
              split_tone(shadow=(222, 0.095), highlight=(34, 0.085), protect=0.5),
              hue_adjust((120, 45, 0, -0.35, 0), (235, 30, -4, 0.10, 0)),
              saturation(1.06, vibrance=0.4)]),
        Look("acero_frio", "Acero frío",
             "Gris azulado desaturado y duro, como metal bajo luz fría.",
             [tone(contrast=0.40, pivot=0.30, shadows=0.14, gamma=1.08, knee=0.85),
              saturation(0.38, vibrance=0.2),
              split_tone(shadow=(224, 0.085), highlight=(218, 0.045), protect=0.3),
              hue_adjust((235, 35, 0, 0.0, 0.05), (25, 25, 0, -0.2, 0), (60, 30, 0, -0.3, 0), (120, 40, 25, -0.3, 0))]),
        Look("oro_gym", "Oro gym",
             "Luces doradas de tungsteno y sombras profundas, aspecto de gimnasio de los 90.",
             [tone(contrast=0.38, pivot=0.30, shadows=0.06, knee=0.8),
              split_tone(shadow=(28, 0.04), highlight=(40, 0.12), protect=0.2),
              hue_adjust((60, 30, -6, 0.15, 0.03), (230, 35, 18, -0.38, -0.08)),
              channel_mix([[1.04, 0, 0], [0, 1.0, 0], [0, 0, 0.90]]),
              saturation(1.10, vibrance=0.4)]),
        Look("teal_orange", "Teal y naranja",
             "El clásico de cine: sombras verde azuladas y piel naranja.",
             [tone(contrast=0.32, pivot=0.30, shadows=0.08, knee=0.84),
              split_tone(shadow=(190, 0.115), highlight=(30, 0.10), protect=0.0),
              hue_adjust((235, 35, -22, 0.12, 0), (120, 40, 18, -0.4, 0)),
              saturation(1.12, vibrance=0.5)]),
        Look("bleach_bypass", "Bleach bypass",
             "Crudo y granuloso: poca saturación, contraste alto y metal con textura.",
             [bleach(0.45),
              tone(contrast=0.42, pivot=0.30, shadows=0.20, gamma=1.22, knee=0.84),
              saturation(0.60, vibrance=0.3),
              split_tone(shadow=(215, 0.03), highlight=(45, 0.03))]),
        Look("phonk_rojo", "Phonk rojo",
             "Agresivo: negros aplastados, acentos rojos y magenta, azules hacia violeta.",
             [exposure(0.35),
              tone(contrast=0.55, pivot=0.34, shadows=-0.10, highlights=0.08, knee=0.86),
              split_tone(shadow=(350, 0.035), highlight=(8, 0.10), protect=0.3),
              hue_adjust((235, 40, 38, 0.12, 0), (120, 50, 0, -0.65, -0.15),
                         (0, 25, -6, 0.30, 0.05), (320, 30, 0, 0.25, 0)),
              channel_mix([[1.12, 0, -0.02], [0, 0.90, 0], [0.04, 0, 0.95]], protect=0.5),
              saturation(1.15, vibrance=0.4)]),
        Look("mate_film", "Mate film",
             "Negros levantados y color desvaído de película mate, suave y nostálgico.",
             [tone(contrast=0.10, pivot=0.32, shadows=0.18, gamma=1.15, knee=0.76, black=0.10, white=0.94),
              saturation(0.78, vibrance=0.3),
              split_tone(shadow=(185, 0.05), highlight=(42, 0.06), protect=0.4),
              lift_color(200, 0.03)]),
        Look("monocromo_contraste", "Monocromo contraste",
             "Blanco y negro duro: negros profundos y luces limpias.",
             [mono((0.42, 0.46, 0.12)),
              tone(contrast=0.55, pivot=0.30, shadows=0.14, knee=0.86)]),
        Look("neon_morado", "Neón morado",
             "Ambiente de luces LED violeta y rosa; azules hacia violeta y negros morados.",
             [tone(contrast=0.34, pivot=0.30, shadows=0.12, knee=0.84),
              split_tone(shadow=(270, 0.12), highlight=(318, 0.08), protect=0.4),
              hue_adjust((235, 40, 30, 0.22, 0.05), (290, 30, 0, 0.25, 0.05), (120, 50, 0, -0.5, 0)),
              saturation(1.12, vibrance=0.4)]),
    ]


PRESETS: dict[str, Look] = {p.id: p for p in _presets()}


# --------------------------------------------------------------------------- #
# LUT aprendido de un perfil de color
# --------------------------------------------------------------------------- #

# Distribución de una toma "típica y neutra" (bien expuesta, sin dominante) en los cuantiles
# de analyze.QUANTILES. Es el punto de partida que se lleva hacia el perfil aprendido.
NEUTRAL_PROFILE = {
    "r": [0.02, 0.07, 0.19, 0.32, 0.41, 0.50, 0.63, 0.80, 0.93],
    "g": [0.02, 0.07, 0.19, 0.32, 0.41, 0.50, 0.63, 0.80, 0.93],
    "b": [0.02, 0.07, 0.19, 0.32, 0.41, 0.50, 0.63, 0.80, 0.93],
}


def _profile_array(profile: dict, k: int) -> np.ndarray:
    try:
        arr = np.array([np.asarray(profile[c], dtype=float) for c in "rgb"])
    except (KeyError, TypeError, ValueError):
        raise LutError("El perfil de color debe tener listas 'r', 'g' y 'b' de cuantiles")
    if arr.ndim != 2 or arr.shape[1] < 3:
        raise LutError("El perfil de color necesita al menos 3 cuantiles por canal")
    if arr.shape[1] != k:  # otra resolución: se remuestrea sobre la misma escala de probabilidades
        src = np.linspace(0, 1, arr.shape[1])
        dst = np.linspace(0, 1, k)
        arr = np.array([np.interp(dst, src, row) for row in arr])
    return np.clip(arr, 0.0, 1.0)


def learned_lattice(profile: dict, neutral: dict | None = None, *, exposure_follow: float = 0.4,
                    tone_gain: float = 1.0, cast_gain: float = 1.0, n: int = SIZE) -> np.ndarray:
    """Rejilla de un LUT que lleva una toma «neutra típica» hacia un perfil de color aprendido.

    Enfoque (en dos partes, para no estropear la imagen con una igualación canal a canal):

    1. **Tono**: la luminancia de la distribución neutra (cuantiles Ln) se lleva a la del perfil
       (Lp) con una curva monótona suave. Para que un perfil oscuro no apague todo el material,
       la curva se recentra: el nivel medio sólo sigue al del perfil en `exposure_follow`
       (0 = conserva el brillo, 1 = lo copia entero) mediante una media geométrica, mientras
       que la forma —cuántos «stops» separan sombras, medios y luces, es decir el contraste—
       se copia entera. Se mide en stops (log2) respecto a la mediana.
    2. **Color**: en cada nivel de luminancia, la diferencia entre la dominante del perfil
       (canal − luminancia) y la de la toma neutra se suma como desplazamiento de color
       (suma cero, así no cambia el brillo). Se desvanece en el negro y en el blanco puros.

    `tone_gain` y `cast_gain` escalan cada parte; la intensidad global se aplica después.
    """
    from .analyze import QUANTILES

    k = len(QUANTILES)
    prof = _profile_array(profile, k)
    neu = _profile_array(neutral or NEUTRAL_PROFILE, k)
    lp, ln = prof.T @ LUMA, neu.T @ LUMA
    floor = 0.004
    lp_med, ln_med = max(lp[k // 2], floor), max(ln[k // 2], floor)
    m_star = ln_med ** (1 - exposure_follow) * lp_med ** exposure_follow
    # forma en stops (log2 respecto a la mediana) igual que la del perfil, recentrada en m_star
    y_nodes = np.clip(m_star * 2.0 ** np.log2(np.maximum(lp, floor) / lp_med), 0.0, 0.985)
    x_nodes = np.maximum.accumulate(ln + np.arange(k) * 1e-4)
    y_nodes = np.maximum.accumulate(y_nodes + np.arange(k) * 1e-4)
    # extremos: el negro se queda en negro; arriba se continúa con una pendiente razonable
    top_slope = float(np.clip((y_nodes[-1] - y_nodes[-2]) / (x_nodes[-1] - x_nodes[-2]), 0.4, 1.2))
    xs = np.concatenate([[0.0], x_nodes, [1.0]])
    ys = np.concatenate([[0.0], y_nodes, [min(1.0, y_nodes[-1] + top_slope * (1 - x_nodes[-1]))]])
    ys = np.maximum.accumulate(ys + np.arange(len(ys)) * 1e-5)
    ys = np.minimum(ys, 1.0)
    for i in range(len(ys) - 2, -1, -1):  # el recorte a 1 no debe romper la monotonía
        ys[i] = min(ys[i], ys[i + 1])

    def curve(x: np.ndarray) -> np.ndarray:
        y = _pchip(xs, ys, x)
        return np.clip(x + tone_gain * (y - x), 0.0, 1.0)

    # dominantes de color por nivel de luminancia (de salida)
    cast = (prof - lp) - (neu - ln)
    cast -= cast.mean(axis=0)
    levels = np.concatenate([[0.0], y_nodes, [1.0]])
    cast_nodes = np.concatenate([np.zeros((3, 1)), cast, np.zeros((3, 1))], axis=1)

    lat = identity_lattice(n)
    out = _apply_luma(lat, curve)
    yo = luma(np.clip(out, 0, 1))
    fade = smoothstep(0.0, 0.04, yo) * (1 - smoothstep(0.92, 1.0, yo))
    for c in range(3):
        out[:, c] += cast_gain * fade * np.interp(yo, levels, cast_nodes[c])
    return np.clip(out, 0.0, 1.0)


def learned_lut(profile: dict, neutral: dict | None = None, *, strength: float = 1.0,
                title: str = "Aprendido", **kw) -> str:
    """Texto .cube de un LUT aprendido de un perfil de color (ver `learned_lattice`)."""
    lat = identity_lattice()
    return format_cube(blend(lat, learned_lattice(profile, neutral, **kw), strength), title)


def video_profile(path: str, max_seconds: float = 40.0) -> dict:
    """Cuantiles RGB de un video (los mismos que aprende un estilo): sin fotogramas negros ni barras de cine."""
    from . import ff
    from .analyze import QUANTILES
    from .render import _hdr_chain, _hdr_transfer

    chain = _hdr_chain(_hdr_transfer(path)) if _hdr_transfer(path) else []
    raw = ff.capture(["-t", f"{max_seconds:.1f}", "-i", path, "-an",
                      "-vf", ",".join(chain + ["fps=3", "scale=36:64", "format=rgb24"]), "-f", "rawvideo", "-"])
    fr = np.frombuffer(raw, np.uint8)
    fr = fr[: len(fr) // (36 * 64 * 3) * 36 * 64 * 3].reshape(-1, 64, 36, 3).astype(float) / 255.0
    if not len(fr):
        raise LutError(f"No se pudieron leer fotogramas de {path}")
    fr = fr[fr.mean(axis=(1, 2, 3)) > 0.012]  # fuera los negros
    if not len(fr):
        raise LutError(f"{os.path.basename(path)} es todo negro")
    rows = fr.mean(axis=3).max(axis=0).max(axis=1)  # barras de cine: filas siempre negras
    top = bot = 0
    while top < 16 and rows[top] < 0.05:
        top += 1
    while bot < 16 and rows[63 - bot] < 0.05:
        bot += 1
    cut = min(top, bot) if min(top, bot) >= 2 else 0
    if cut:
        fr = fr[:, cut:64 - cut]
    return {ch: [round(float(q), 4) for q in np.quantile(fr[..., i].ravel(), QUANTILES)]
            for i, ch in enumerate("rgb")}


def lut_from_video(path: str, neutral: dict | None = None, *, strength: float = 1.0, **kw) -> str:
    """Texto .cube de un LUT aprendido directamente de un video de referencia.
    Con `neutral=video_profile(mi_clip)` el LUT lleva *tu* material hacia el de la referencia."""
    return learned_lut(video_profile(path), neutral, strength=strength,
                       title=f"Aprendido de {os.path.basename(path)}", **kw)


# --------------------------------------------------------------------------- #
# Formato .cube
# --------------------------------------------------------------------------- #

def format_cube(lattice: np.ndarray, title: str, comment: str | None = None, n: int = SIZE) -> str:
    if lattice.shape != (n ** 3, 3):
        raise LutError(f"La rejilla debe tener {n ** 3} filas")
    head = [f'TITLE "{title.replace(chr(34), chr(39))}"']
    if comment:
        head.append(f"# {comment}")
    head += [f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    body = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in np.clip(lattice, 0, 1))
    return "\n".join(head) + body + "\n"


def parse_cube(text: str) -> tuple[int, np.ndarray]:
    """Lee un .cube 3D. Devuelve (tamaño, rejilla (n^3, 3)) con dominio normalizado a 0..1."""
    size, rows = None, []
    dmin, dmax = np.zeros(3), np.ones(3)
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key = line.split(None, 1)[0].upper()
        if key == "LUT_3D_SIZE":
            size = int(line.split()[1])
        elif key == "LUT_1D_SIZE":
            raise LutError("Es un LUT 1D; hace falta un LUT 3D (LUT_3D_SIZE)")
        elif key == "DOMAIN_MIN":
            dmin = np.array([float(v) for v in line.split()[1:4]])
        elif key == "DOMAIN_MAX":
            dmax = np.array([float(v) for v in line.split()[1:4]])
        elif key in ("TITLE", "LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE") or key.isalpha():
            continue
        else:
            try:
                vals = [float(v) for v in line.split()]
            except ValueError:
                raise LutError(f"Línea no válida en el .cube: {line[:40]!r}")
            if len(vals) != 3:
                raise LutError(f"Línea no válida en el .cube: {line[:40]!r}")
            rows.append(vals)
    if not size or size < 2:
        raise LutError("El .cube no indica LUT_3D_SIZE")
    if len(rows) != size ** 3:
        raise LutError(f"El .cube debería tener {size ** 3} filas de color y tiene {len(rows)}")
    arr = (np.array(rows) - dmin) / np.maximum(dmax - dmin, 1e-9)
    return size, arr


def read_cube(path: str) -> tuple[int, np.ndarray]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_cube(f.read())
    except OSError as e:
        raise LutError(f"No se pudo leer el LUT {path}: {e}")


def _write_atomic(path: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".part")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def preset_cube(look: Look, strength: float = 1.0) -> str:
    return format_cube(look.lattice(strength), look.name,
                       f"{look.description} · VideoEditor, preset '{look.id}'")


def export_presets(out_dir: str) -> list[str]:
    """Escribe todos los presets como .cube (para CapCut, Premiere, DaVinci…)."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for look in PRESETS.values():
        p = os.path.join(out_dir, f"{look.id}.cube")
        _write_atomic(p, preset_cube(look))
        paths.append(p)
    return paths


# --------------------------------------------------------------------------- #
# Resolución de un LUT (preset, estilo o archivo) a un .cube listo para ffmpeg
# --------------------------------------------------------------------------- #

def _style_dirs(base_dir: str | None) -> list[str]:
    dirs = []
    if _WORKSPACE:
        dirs.append(os.path.join(_WORKSPACE, "styles"))
    if base_dir:
        dirs += [os.path.normpath(os.path.join(base_dir, "..", "styles")), os.path.join(base_dir, "styles")]
    return dirs


def _user_dirs(base_dir: str | None) -> list[str]:
    dirs = []
    if _WORKSPACE:
        dirs.append(os.path.join(_WORKSPACE, "luts"))
    if base_dir:
        dirs += [os.path.normpath(os.path.join(base_dir, "..", "luts")), os.path.join(base_dir, "luts")]
    return dirs


def find_style_profile(style_id: str, base_dir: str | None = None) -> tuple[dict, str]:
    """(perfil de color, id) del estilo `style_id`; busca en la carpeta de trabajo y en los incluidos."""
    from .style import find_style

    last = None
    for d in _style_dirs(base_dir) + [None]:
        try:
            st = find_style(style_id, d)
            break
        except ValueError as e:
            last = e
    else:
        raise LutError(f"No existe el estilo '{style_id}' para el LUT aprendido ({last})")
    if not st.get("color"):
        raise LutError(f"El estilo '{st.get('name', style_id)}' no tiene color aprendido")
    return st["color"], st.get("id") or style_id


def classify(spec: str, base_dir: str | None = None) -> tuple[str, str]:
    """('preset', id) | ('style', id) | ('file', ruta absoluta). Lanza LutError si no se entiende."""
    if not isinstance(spec, str) or not spec.strip():
        raise LutError("el LUT debe ser un texto (id de preset, 'style:id' o ruta a un .cube)")
    spec = spec.strip()
    if spec in PRESETS:
        return "preset", spec
    if spec.startswith("style:"):
        return "style", spec[6:].strip()
    path = os.path.expanduser(spec)
    cands = []
    if os.path.isabs(path):
        cands.append(path)
    else:
        cands.append(os.path.normpath(os.path.join(base_dir or ".", path)))
        cands += [os.path.join(d, path) for d in _user_dirs(base_dir)]
    pathlike = os.sep in spec or "/" in spec or spec.lower().endswith(LUT_EXT)
    for c in cands:
        for variant in (c, c + ".cube"):
            if os.path.isfile(variant):
                return "file", variant
    if pathlike:
        if not spec.lower().endswith(LUT_EXT):
            raise LutError(f"'{spec}': el LUT debe ser un archivo {'/'.join(LUT_EXT)}")
        return "file", cands[0]  # no existe: lo avisa check_sources
    raise LutError(f"LUT '{spec}' desconocido. Presets: {', '.join(PRESETS)}; también 'style:<estilo>' o la ruta a un .cube")


def validate_spec(spec: str, base_dir: str | None = None) -> str:
    """Comprueba que el LUT se entiende (y, para estilos, que existe). Devuelve el valor a guardar:
    los archivos se dejan como estaban escritos."""
    kind, value = classify(spec, base_dir)
    if kind == "style":
        find_style_profile(value, base_dir)
    return spec.strip()


def referenced_file(spec: str, base_dir: str | None = None) -> str | None:
    """Ruta del archivo que usa el LUT (None si es preset/estilo): para comprobar que existe."""
    kind, value = classify(spec, base_dir)
    return value if kind == "file" else None


def _tag(strength: float) -> str:
    return f"s{int(round(min(1.0, max(0.0, strength)) * 100)):03d}"


_resolved: dict[tuple, str] = {}


def resolve(spec: str, strength: float = 1.0, base_dir: str | None = None) -> str:
    """Ruta de un .cube que ya lleva la intensidad aplicada, listo para `lut3d`.

    - preset a intensidad 1: el .cube incluido en `videoeditor/luts`
    - resto de casos: se genera/mezcla con la identidad y se guarda en la caché
    """
    kind, value = classify(spec, base_dir)
    strength = min(1.0, max(0.0, float(strength)))
    key = (kind, value, _tag(strength), os.path.getmtime(value) if kind == "file" and os.path.exists(value) else 0)
    if key in _resolved and os.path.exists(_resolved[key]):
        return _resolved[key]
    tag = _tag(strength)
    if kind == "preset":
        shipped = os.path.join(SHIPPED_DIR, f"{value}.cube")
        if tag == "s100" and os.path.isfile(shipped):
            out = shipped
        else:
            out = os.path.join(cache_dir(), f"{value}_{tag}.cube")
            if not os.path.exists(out):
                _write_atomic(out, preset_cube(PRESETS[value], strength))
    elif kind == "style":
        profile, sid = find_style_profile(value, base_dir)
        h = hashlib.sha1(repr(profile).encode()).hexdigest()[:8]
        out = os.path.join(cache_dir(), f"style_{re.sub(r'[^a-z0-9_]', '_', sid.lower())}_{h}_{tag}.cube")
        if not os.path.exists(out):
            lat = identity_lattice()
            _write_atomic(out, format_cube(blend(lat, learned_lattice(profile), strength), f"Aprendido de {sid}"))
    else:
        if not os.path.isfile(value):
            raise LutError(f"No se encuentra el LUT: {value}")
        if tag == "s100" and value.lower().endswith(".cube"):
            out = value
        else:
            if not value.lower().endswith(".cube"):
                raise LutError(f"Para ajustar la intensidad hace falta un .cube (recibido {os.path.basename(value)})")
            st = os.stat(value)
            h = hashlib.sha1(f"{value}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:10]
            out = os.path.join(cache_dir(), f"user_{h}_{tag}.cube")
            if not os.path.exists(out):
                n, arr = read_cube(value)
                ident = identity_lattice(n)
                _write_atomic(out, format_cube(blend(ident, arr, strength), os.path.basename(value), n=n))
    _resolved[key] = out
    return out


def ff_escape(path: str) -> str:
    """Ruta lista para el valor de una opción de filtro de ffmpeg (p. ej. `lut3d=file=...`).

    La cadena se interpreta dos veces: primero como grafo de filtros (donde `\\`, `'`, `,`, `;`,
    `[` y `]` son especiales) y luego como opciones del filtro (`\\`, `'` y `:`). Se escapa con
    barra invertida en ambos niveles, de modo que valen rutas con espacios, comas, comillas,
    corchetes o dos puntos (también `C:/...` en Windows)."""
    p = path.replace("\\", "/")  # ffmpeg acepta / también en Windows
    p = re.sub(r"([\\':])", r"\\\1", p)
    return re.sub(r"([\\',;\[\]])", r"\\\1", p)


def lut_filter(spec: str | None, strength: float = 1.0, base_dir: str | None = None) -> str | None:
    """Filtro `lut3d=...` para un LUT, o None si no hay LUT / la intensidad es 0."""
    if not spec or spec == "none" or strength <= 0.001:
        return None
    return f"lut3d=file={ff_escape(resolve(spec, strength, base_dir))}"


# --------------------------------------------------------------------------- #
# Listado
# --------------------------------------------------------------------------- #

def list_luts(workspace: str | None = None, with_styles: bool = True) -> list[dict]:
    """Presets + LUTs propios de `<workspace>/luts` (+ LUT aprendido de cada estilo con color).
    Cada elemento: id, name, description, kind (preset|user|style) y value (lo que va en el JSON)."""
    ws = workspace or _WORKSPACE
    out = [{"id": p.id, "name": p.name, "description": p.description, "kind": "preset", "value": p.id}
           for p in PRESETS.values()]
    if ws:
        d = os.path.join(ws, "luts")
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith(LUT_EXT) and not fn.startswith("."):
                    out.append({"id": fn, "name": os.path.splitext(fn)[0], "description": "LUT propio",
                                "kind": "user", "value": f"../luts/{fn}"})
        if with_styles:
            from .style import list_styles

            for s in list_styles(os.path.join(ws, "styles")):
                if s.get("color"):
                    out.append({"id": f"style:{s['id']}", "name": f"Aprendido: {s['name']}",
                                "description": "Color aprendido del estilo", "kind": "style",
                                "value": f"style:{s['id']}"})
    return out


# --------------------------------------------------------------------------- #
# Vistas previas
# --------------------------------------------------------------------------- #

def _frame_filters(clip: str) -> list[str]:
    from .render import _hdr_chain, _hdr_transfer

    trc = _hdr_transfer(clip)
    return _hdr_chain(trc) if trc else []


def preview_frame(clip: str, at: float, out: str, spec: str | None = None, strength: float = 1.0,
                  width: int = 180, base_dir: str | None = None) -> str:
    """Un fotograma del clip (con el LUT aplicado, si se indica) como JPEG."""
    from . import ff

    vf = _frame_filters(clip) + [f"scale={width}:-2"]
    f = lut_filter(spec, strength, base_dir) if spec else None
    if f:
        vf.append(f)
    ff.run(["-ss", f"{max(0.0, at):.2f}", "-i", clip, "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "3", out])
    return out


def contact_sheet(clip: str, out: str, at: float = 1.0, specs: list[tuple[str, str]] | None = None,
                  strength: float = 1.0, cell_w: int = 270, cols: int | None = None,
                  base_dir: str | None = None) -> str:
    """Imagen con un fotograma del clip con cada LUT, rotulado con su nombre.
    `specs` = [(etiqueta, spec)]; por defecto «Original» + todos los presets."""
    from . import ff
    from .render import _font_file

    if specs is None:
        specs = [("Original", "none")] + [(p.name, p.id) for p in PRESETS.values()]
    w, h = cell_w, 0
    tmp = tempfile.mkdtemp(prefix="ve_sheet_")
    try:
        frame = os.path.join(tmp, "frame.png")
        ff.run(["-ss", f"{max(0.0, at):.2f}", "-i", clip, "-frames:v", "1",
                "-vf", ",".join(_frame_filters(clip) + [f"scale={w}:-2"]), frame])
        h = int(ff.video_size(frame)[1])
        n = len(specs)
        cols = cols or min(n, 5 if h > w else 4)
        rows = -(-n // cols)
        font = _font_file("Sans:style=Bold")
        graph = [f"[0:v]split={n}" + "".join(f"[f{i}]" for i in range(n))]
        for i, (label, spec) in enumerate(specs):
            tf = os.path.join(tmp, f"t{i}.txt")
            with open(tf, "w", encoding="utf-8") as fh:
                fh.write(label)
            chain = []
            flt = lut_filter(spec, strength, base_dir) if spec and spec != "none" else None
            if flt:
                chain.append(flt)
            dt = [f"textfile='{tf}'", f"fontsize={max(12, w // 13)}", "fontcolor=white", "box=1",
                  "boxcolor=black@0.6", "boxborderw=6", "x=(w-text_w)/2", f"y=h-text_h-{max(10, h // 40)}"]
            if font:
                dt.insert(1, f"fontfile='{font}'")
            chain.append("drawtext=" + ":".join(dt))
            graph.append(f"[f{i}]" + ",".join(chain) + f"[o{i}]")
        layout = "|".join(f"{(i % cols) * w}_{(i // cols) * h}" for i in range(n))
        graph.append("".join(f"[o{i}]" for i in range(n)) +
                     f"xstack=inputs={n}:layout={layout}:fill=black,format=yuvj420p[out]")
        ff.run(["-i", frame, "-filter_complex", ";".join(graph), "-map", "[out]", "-frames:v", "1", "-q:v", "2", out])
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def apply_to_project_data(data: dict, lut: str | None, strength: float = 1.0, *,
                          style: dict | None = None, project_dir: str = ".") -> str | None:
    """Escribe el LUT elegido en el dict del proyecto. `lut` puede ser un preset, un archivo,
    'style:<id>' o 'style'/'learned' (el LUT aprendido de `style`). Devuelve el valor guardado."""
    if not lut or lut == "none":
        return None
    if lut in ("style", "learned", "aprendido"):
        if not style or not style.get("color"):
            raise LutError("Ese estilo no tiene color aprendido para generar un LUT")
        lut = f"style:{style.get('id') or style.get('name')}"
    else:
        kind, value = classify(lut, project_dir)
        if kind == "file":
            lut = os.path.relpath(value, project_dir) if os.path.isfile(value) else lut
        elif kind == "style":
            find_style_profile(value, project_dir)
    if not 0.0 <= float(strength) <= 1.0:
        raise LutError("la intensidad del LUT debe estar entre 0 y 1")
    data["lut"] = lut
    data["lut_strength"] = round(float(strength), 3)
    return lut


def main(argv: list[str] | None = None) -> int:
    """`python -m videoeditor.luts [--out DIR]`: regenera los .cube incluidos en videoeditor/luts."""
    import argparse

    ap = argparse.ArgumentParser(description="Regenera los LUTs .cube incluidos")
    ap.add_argument("--out", default=SHIPPED_DIR, help=f"Carpeta de salida (por defecto {SHIPPED_DIR})")
    a = ap.parse_args(argv)
    for p in export_presets(a.out):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
