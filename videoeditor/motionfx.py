"""Detección de técnicas de cámara y de edición dentro de cada plano:

- zoom de golpe (punch): la imagen salta a más escala de un fotograma al siguiente
- temblor (shake): sacudidas rápidas de la imagen (componente de alta frecuencia
  del desplazamiento)
- cambios de velocidad (cámara lenta / rampas): el movimiento cae bruscamente
  dentro de un mismo plano y luego se recupera
- textos sobreimpresos: zonas pequeñas, muy contrastadas y fijas mientras el resto
  de la imagen cambia (con OCR si `tesseract` está instalado)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import numpy as np

from . import ff

MW, MH = 90, 160          # resolución para movimiento
SCALES = np.array([0.88, 0.91, 0.94, 0.96, 0.98, 1.0, 1.02, 1.04, 1.06, 1.08, 1.1, 1.13, 1.16, 1.2, 1.25])


def gray_frames(path: str, fps: float, w: int = MW, h: int = MH) -> np.ndarray:
    raw = ff.capture(["-i", path, "-an", "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"])
    arr = np.frombuffer(raw, np.uint8)
    n = len(arr) // (w * h)
    return arr[: n * w * h].reshape(n, h, w).astype(np.float32)


def _scale_maps(h: int, w: int) -> list[tuple[np.ndarray, np.ndarray]]:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy, cx = (h - 1) / 2, (w - 1) / 2
    maps = []
    for s in SCALES:
        # muestrear el fotograma anterior como si se hubiera hecho zoom `s`
        maps.append((cy + (yy - cy) / s, cx + (xx - cx) / s))
    return maps


def _sample(img: np.ndarray, my: np.ndarray, mx: np.ndarray) -> np.ndarray:
    h, w = img.shape
    y0 = np.clip(np.floor(my).astype(int), 0, h - 2)
    x0 = np.clip(np.floor(mx).astype(int), 0, w - 2)
    fy = np.clip(my - y0, 0, 1)
    fx = np.clip(mx - x0, 0, 1)
    a = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    b = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return a * (1 - fy) + b * fy


def _shift_candidates(a: np.ndarray, b: np.ndarray, k: int = 3) -> list[tuple[int, int]]:
    """Desplazamientos (dx, dy) candidatos a→b: los picos más altos de la correlación de fase."""
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa = np.fft.fft2((a - a.mean()) * win)
    fb = np.fft.fft2((b - b.mean()) * win)
    r = fa.conj() * fb
    r /= np.abs(r) + 1e-9
    c = np.abs(np.fft.ifft2(r))
    h, w = a.shape
    out = []
    flat = np.argsort(c.ravel())[::-1]
    for idx in flat[: 40]:
        y, x = divmod(int(idx), w)
        if y > h // 2:
            y -= h
        if x > w // 2:
            x -= w
        if all(abs(x - ox) > 1 or abs(y - oy) > 1 for ox, oy in out):
            out.append((x, y))
        if len(out) >= k:
            break
    return out


def frame_motion(fr: np.ndarray) -> dict[str, np.ndarray]:
    """Por cada fotograma (respecto al anterior): escala, dx, dy y cambio residual."""
    n, h, w = fr.shape
    maps = _scale_maps(h, w)
    m = (slice(h // 8, h - h // 8), slice(w // 8, w - w // 8))
    scale = np.ones(n)
    dx = np.zeros(n)
    dy = np.zeros(n)
    resid = np.zeros(n)
    raw = np.zeros(n)
    for i in range(1, n):
        prev, cur = fr[i - 1], fr[i]
        errs = []
        for my, mx in maps:
            errs.append(np.abs(_sample(prev, my, mx)[m] - cur[m]).mean())
        errs = np.array(errs)
        k = int(np.argmin(errs))
        base = errs[SCALES == 1.0][0]
        scale[i] = SCALES[k] if errs[k] < base * 0.85 else 1.0
        # de los picos candidatos, el desplazamiento que mejor explica la imagen
        best_e, best = None, (0, 0)
        for cx, cy in _shift_candidates(prev, cur):
            e = np.abs(np.roll(prev, (cy, cx), axis=(0, 1))[m] - cur[m]).mean()
            if best_e is None or e < best_e:
                best_e, best = e, (cx, cy)
        dx[i], dy[i] = best
        raw[i] = base
        resid[i] = min(errs[k], best_e)
    # raw: cambio sin compensar; resid: cambio que queda tras compensar zoom o desplazamiento
    return {"scale": scale, "dx": dx, "dy": dy, "resid": resid, "raw": raw}


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    if len(x) < k:
        return x.copy()
    pad = np.pad(x, (k // 2, k // 2), mode="edge")
    return np.convolve(pad, np.ones(k) / k, mode="valid")[: len(x)]


def detect_in_shot(mot: dict, a: int, b: int, fps: float, w: int = MW) -> dict:
    """Efectos dentro de los fotogramas [a, b) de un plano."""
    out = {"punches": [], "shakes": [], "speed": []}
    if b - a < 6:
        return out
    sc = mot["scale"][a:b]
    # Zoom de golpe: salto de escala >= 4 % en un fotograma, tras fotogramas estables
    for i in range(2, len(sc)):
        settles = i + 2 >= len(sc) or (sc[i + 1] <= 1.0 and sc[i + 2] <= 1.0)  # luego vuelve (decae)
        if sc[i] >= 1.05 and sc[i - 1] < 1.03 and sc[i - 2] < 1.03 and settles:
            if not out["punches"] or i / fps - out["punches"][-1][0] > 0.3:
                out["punches"].append((round(i / fps, 3), round(float(sc[i] - 1), 3)))
    # Temblor: desplazamiento de alta frecuencia sostenido
    dx, dy = mot["dx"][a:b], mot["dy"][a:b]
    jit = np.hypot(dx - _smooth(dx, 7), dy - _smooth(dy, 7))
    rms = np.sqrt(_smooth(jit ** 2, max(3, int(fps * 0.25))))
    # sacudida = vaivén: el desplazamiento cambia de signo a menudo (no es un objeto que cruza)
    sign_x = np.sign(dx - _smooth(dx, 7))
    flips = _smooth((np.abs(np.diff(sign_x, prepend=sign_x[:1])) > 0).astype(float), max(3, int(fps * 0.3)))
    shaky = (rms > w * 0.018) & (flips > 0.25)
    i = 0
    while i < len(shaky):
        if shaky[i]:
            j = i
            while j < len(shaky) and shaky[j]:
                j += 1
            if (j - i) / fps >= 0.4:
                amt = float(np.clip(rms[i:j].mean() / (w * 0.03), 0.2, 1.0))
                out["shakes"].append((round(i / fps, 3), round((j - i) / fps, 3), round(amt, 2)))
            i = j
        else:
            i += 1
    # Cámara lenta: el cambio entre fotogramas cae mucho respecto al resto del plano
    raw_act = mot["resid"][a:b] + np.hypot(dx, dy) * 0.5
    raw_act[:2] = np.median(raw_act)  # el primer fotograma (corte) no cuenta
    act = _smooth(raw_act, max(3, int(fps * 0.2)))
    base = np.percentile(act, 75)  # la velocidad "normal" del plano
    if base > 0.8:
        slow = act < base * 0.4
        i = 0
        while i < len(slow):
            if slow[i]:
                j = i
                while j < len(slow) and slow[j]:
                    j += 1
                dur = (j - i) / fps
                # patrón de rampa: velocidad normal antes Y después (si no, puede ser alguien quieto)
                if dur >= 0.5 and i > fps * 0.2 and j < len(slow) - fps * 0.2 and act[i:j].mean() < base * 0.35:
                    sp = float(np.clip(act[i:j].mean() / base, 0.2, 0.8))
                    out["speed"].append((round(i / fps, 3), round(dur, 3), round(sp, 2)))
                i = j
            else:
                i += 1
    return out


# --------------------------------------------------------------------------- #
# Textos
# --------------------------------------------------------------------------- #

def text_regions(path: str, fps: float, shots: list[tuple[float, float]]) -> list[dict]:
    """Textos fijos sobreimpresos: [{start, end, box: [x, y, w, h] (0-1), text}]."""
    w, h = 180, 320
    fr = gray_frames(path, min(fps, 10.0), w, h)
    tfps = min(fps, 10.0)
    if len(fr) < 4:
        return []
    gy = np.abs(np.diff(fr, axis=1, prepend=fr[:, :1]))
    gx = np.abs(np.diff(fr, axis=2, prepend=fr[:, :, :1]))
    edge = (gx + gy) > 60
    bright = (fr > 200) | (fr < 25)
    out = []
    win = max(3, int(tfps * 0.6))
    i = 0
    active = None
    while i + win <= len(fr):
        seg = slice(i, i + win)
        stable_edge = edge[seg].mean(axis=0) > 0.85
        still = fr[seg].std(axis=0) < 6
        cand = stable_edge & still & bright[seg].mean(axis=0).astype(bool)
        # el resto de la imagen debe moverse (si no, es un plano fijo, no un texto)
        # (con OCR disponible, la lectura del texto ya filtra los falsos positivos)
        moving = (fr[seg].std(axis=0) > 6).mean() > 0.03 or bool(shutil.which("tesseract"))
        box = _box(cand) if moving else None
        t0 = i / tfps
        if box is not None:
            if active and _iou(active["box"], box) > 0.3:
                active["end"] = round(t0 + win / tfps, 2)
            else:
                if active:
                    out.append(active)
                active = {"start": round(t0, 2), "end": round(t0 + win / tfps, 2), "box": box, "frame": i + win // 2}
        elif active:
            out.append(active)
            active = None
        i += max(1, win // 2)
    if active:
        out.append(active)
    out = [t for t in out if t["end"] - t["start"] >= 0.5]
    for t in out:
        t["text"] = _ocr(path, t["start"] + (t["end"] - t["start"]) / 2, t["box"])
        t.pop("frame", None)
    if shutil.which("tesseract"):  # con OCR disponible, sólo cuenta si se lee algo
        out = [t for t in out if sum(ch.isalnum() for ch in t["text"]) >= 3]
    return out


def _box(mask: np.ndarray) -> list[float] | None:
    h, w = mask.shape
    rows = mask.sum(axis=1)
    if rows.max() < 4:
        return None
    ys = np.nonzero(rows >= 3)[0]
    # el grupo de filas más denso
    groups, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 3:
            cur.append(y)
        else:
            groups.append(cur)
            cur = [y]
    groups.append(cur)
    g = max(groups, key=lambda gg: rows[gg].sum())
    y0, y1 = g[0], g[-1] + 1
    if (y1 - y0) / h > 0.25 or (y1 - y0) < 5:
        return None
    cols = np.nonzero(mask[y0:y1].sum(axis=0) > 0)[0]
    if len(cols) < 6:
        return None
    x0, x1 = cols[0], cols[-1] + 1
    if mask[y0:y1, x0:x1].mean() < 0.08:
        return None
    return [round(x0 / w, 3), round(y0 / h, 3), round((x1 - x0) / w, 3), round((y1 - y0) / h, 3)]


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    ix = max(0.0, min(ax1, bx1) - max(a[0], b[0]))
    iy = max(0.0, min(ay1, by1) - max(a[1], b[1]))
    inter = ix * iy
    return inter / (a[2] * a[3] + b[2] * b[3] - inter + 1e-9)


def _ocr(path: str, t: float, box: list[float]) -> str:
    if not shutil.which("tesseract"):
        return ""
    vw, vh = ff.video_size(path)
    x, y, w, h = box
    pad = 0.02
    cx, cy = max(0, int((x - pad) * vw)), max(0, int((y - pad) * vh))
    cw, ch = min(vw - cx, int((w + 2 * pad) * vw)), min(vh - cy, int((h + 2 * pad) * vh))
    with tempfile.TemporaryDirectory() as d:
        img = os.path.join(d, "t.png")
        try:
            ff.run(["-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                    "-vf", f"crop={cw}:{ch}:{cx}:{cy},scale=-2:120,format=gray", img])
        except ff.FFmpegError:
            return ""
        r = subprocess.run(["tesseract", img, "-", "-l", "spa+eng", "--psm", "7"], capture_output=True, text=True)
    return " ".join(r.stdout.split())[:60]
