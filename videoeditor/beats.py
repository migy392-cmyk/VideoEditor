"""Detección de tempo y beats (sólo numpy + ffmpeg para decodificar)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import ff

SR = 22050
HOP = 512
WIN = 1024


@dataclass
class BeatInfo:
    bpm: float
    period: float
    beats: list[float]       # rejilla regular de beats (segundos)
    onsets: list[float]      # golpes detectados (segundos)
    duration: float

    def beats_between(self, start: float, end: float) -> list[float]:
        return [b for b in self.beats if start <= b < end]


def load_audio(path: str, start: float = 0.0, duration: float | None = None) -> np.ndarray:
    args = ["-ss", f"{start}"]
    if duration:
        args += ["-t", f"{duration}"]
    args += ["-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = ff.capture(args)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def onset_envelope(audio: np.ndarray) -> np.ndarray:
    if len(audio) < WIN * 2:
        return np.zeros(1)
    frames = np.lib.stride_tricks.sliding_window_view(audio, WIN)[::HOP]
    spec = np.abs(np.fft.rfft(frames * np.hanning(WIN), axis=1))
    # flujo espectral logarítmico, ponderando graves (bombo) un poco más
    weights = np.ones(spec.shape[1])
    weights[: spec.shape[1] // 8] = 1.6
    flux = np.maximum(0.0, np.diff(np.log1p(spec * 10), axis=0)) @ weights
    flux = np.concatenate([[0.0], flux])
    std = flux.std() or 1.0
    return (flux - flux.mean()) / std


def detect(path: str, start: float = 0.0, duration: float | None = None,
           min_bpm: float = 70, max_bpm: float = 180) -> BeatInfo:
    audio = load_audio(path, start, duration)
    total = len(audio) / SR
    env = onset_envelope(audio)
    fps = SR / HOP
    if len(env) < 8:
        return BeatInfo(bpm=120.0, period=0.5, beats=[], onsets=[], duration=total)

    # Onsets: picos locales por encima del umbral
    thr = 1.2
    peaks = [i for i in range(1, len(env) - 1) if env[i] > thr and env[i] >= env[i - 1] and env[i] > env[i + 1]]
    onsets = [round(float(i / fps), 3) for i in peaks]

    # Tempo por autocorrelación con preferencia suave por ~120 BPM
    e = np.maximum(env, 0)
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lags = np.arange(len(ac)) / fps
    valid = (lags >= 60.0 / max_bpm) & (lags <= 60.0 / min_bpm)
    if not valid.any():
        return BeatInfo(bpm=120.0, period=0.5, beats=[], onsets=onsets, duration=total)
    bpms = 60.0 / np.where(lags > 0, lags, 1)
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    score = np.where(valid, ac * prior, -np.inf)
    lag_i = int(np.argmax(score))
    # refinamiento parabólico
    if 1 <= lag_i < len(ac) - 1:
        a, b, c = ac[lag_i - 1], ac[lag_i], ac[lag_i + 1]
        denom = a - 2 * b + c
        shift = 0.5 * (a - c) / denom if denom != 0 else 0.0
    else:
        shift = 0.0
    period = (lag_i + shift) / fps

    # Fase: el desplazamiento que más energía de onset acumula sobre la rejilla
    step = period * fps
    best_phase, best = 0.0, -1.0
    for ph in np.arange(0, step, 0.5):
        idx = np.round(np.arange(ph, len(e), step)).astype(int)
        idx = idx[idx < len(e)]
        s = e[idx].sum()
        if s > best:
            best, best_phase = s, ph
    first = best_phase / fps
    beats = [round(float(t), 3) for t in np.arange(first, total, period)]
    return BeatInfo(bpm=round(float(60.0 / period), 2), period=float(period), beats=beats, onsets=onsets, duration=total)
