"""Recorte de personas (segmentación) con MediaPipe.

Genera un video con canal alfa donde sólo queda la persona. Se usa en la
transición `subject_drop` (la silueta del plano siguiente cae sobre el actual)
y en el efecto de recorte en general.

El modelo se descarga la primera vez a ~/.cache/videoeditor/models
(o a la carpeta indicada en la variable de entorno VIDEOEDITOR_MODELS).
"""
from __future__ import annotations

import os
import subprocess
import urllib.request

import numpy as np

from . import ff

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/"
             "selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite")
MODEL_NAME = "selfie_multiclass_256x256.tflite"


class SegmentationUnavailable(RuntimeError):
    pass


def models_dir() -> str:
    d = os.environ.get("VIDEOEDITOR_MODELS") or os.path.join(os.path.expanduser("~"), ".cache", "videoeditor", "models")
    os.makedirs(d, exist_ok=True)
    return d


def ensure_model() -> str:
    path = os.path.join(models_dir(), MODEL_NAME)
    if not os.path.exists(path):
        tmp = path + ".part"
        try:
            urllib.request.urlretrieve(MODEL_URL, tmp)
        except OSError as e:
            raise SegmentationUnavailable(f"No se pudo descargar el modelo de segmentación: {e}")
        os.replace(tmp, path)
    return path


def available() -> bool:
    try:
        import mediapipe  # noqa: F401
        from mediapipe.tasks.python import vision  # noqa: F401
        return True
    except Exception:
        return False


class PersonSegmenter:
    def __init__(self) -> None:
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
        except Exception as e:  # ImportError o falta de libEGL
            raise SegmentationUnavailable(
                "Falta MediaPipe para recortar personas: pip install mediapipe "
                f"(en Linux también libegl1 y libgles2). Detalle: {e}")
        self._mp = mp
        opts = vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=ensure_model()),
            output_confidence_masks=True,
            output_category_mask=False,
        )
        self._seg = vision.ImageSegmenter.create_from_options(opts)
        self._prev: np.ndarray | None = None

    def matte(self, rgb: np.ndarray, smooth: float = 0.5) -> np.ndarray:
        """Alfa en [0,1] (float32, mismo tamaño que la imagen) para la persona."""
        mp = self._mp
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        res = self._seg.segment(img)
        conf = 1.0 - res.confidence_masks[0].numpy_view().reshape(rgb.shape[:2])  # 0 = fondo
        # Umbral suave: elimina la "neblina" del fondo y mantiene el borde suave.
        a = np.clip((conf - 0.45) / 0.3, 0.0, 1.0).astype(np.float32)
        if self._prev is not None and self._prev.shape == a.shape:
            a = smooth * self._prev + (1 - smooth) * a  # estabilidad temporal
        self._prev = a
        return a

    def close(self) -> None:
        self._seg.close()


def _resize_bilinear(a: np.ndarray, h: int, w: int) -> np.ndarray:
    ys = np.linspace(0, a.shape[0] - 1, h)
    xs = np.linspace(0, a.shape[1] - 1, w)
    y0 = np.floor(ys).astype(int)
    x0 = np.floor(xs).astype(int)
    y1 = np.minimum(y0 + 1, a.shape[0] - 1)
    x1 = np.minimum(x0 + 1, a.shape[1] - 1)
    fy = (ys - y0)[:, None]
    fx = (xs - x0)[None, :]
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bot = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return top * (1 - fy) + bot * fy


def cutout_video(inputs: list[str], chain: str, w: int, h: int, fps: int, dur: float, out_path: str,
                 work_scale: float = 0.5) -> str:
    """Decodifica (inputs + cadena de filtros ya preparada), recorta a la persona
    y escribe un video con alfa (FFV1 yuva420p en .mkv)."""
    ww, wh = max(64, int(w * work_scale) // 2 * 2), max(64, int(h * work_scale) // 2 * 2)
    dec = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", f"[0:v]{chain}[o]", "-map", "[o]", "-t", f"{dur:.4f}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
         "-c:v", "ffv1", "-pix_fmt", "yuva420p", out_path],
        stdin=subprocess.PIPE)
    seg = PersonSegmenter()
    frame_bytes = w * h * 3
    try:
        while True:
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            rgb = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            small = rgb[:: max(1, h // wh), :: max(1, w // ww)]
            a = seg.matte(small)
            alpha = (_resize_bilinear(a, h, w) * 255).astype(np.uint8)
            enc.stdin.write(np.dstack([rgb, alpha]).tobytes())
    finally:
        seg.close()
        dec.stdout.close()
        dec.wait()
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0 or not os.path.exists(out_path):
        raise ff.FFmpegError("No se pudo escribir el video recortado")
    return out_path
