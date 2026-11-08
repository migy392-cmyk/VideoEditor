"""Genera canciones sintéticas con estructura conocida (para tests y demos)."""
import wave

import numpy as np

SR = 22050


def _env(n, attack=0.002, decay=0.15):
    t = np.arange(n) / SR
    return np.minimum(1, t / attack) * np.exp(-t / decay)


def make_song(path, bpm=128, structure=(("intro", 4), ("build", 2), ("drop", 4), ("break", 2), ("drop", 4), ("outro", 2)),
              offset=0.0):
    """Escribe un WAV. Devuelve dict con los tiempos de inicio de cada sección."""
    beat = 60 / bpm
    bars = sum(b for _, b in structure)
    total = offset + bars * 4 * beat + 1.0
    out = np.zeros(int(total * SR))
    rng = np.random.default_rng(0)
    kick = np.sin(2 * np.pi * np.cumsum(np.linspace(120, 45, int(0.25 * SR))) / SR) * _env(int(0.25 * SR), decay=0.12)
    hat = rng.standard_normal(int(0.05 * SR)) * _env(int(0.05 * SR), decay=0.015)
    hat = np.diff(hat, prepend=0)
    clap = rng.standard_normal(int(0.2 * SR)) * _env(int(0.2 * SR), decay=0.05)

    def add(sig, t, gain):
        i = int(t * SR)
        j = min(len(out), i + len(sig))
        out[i:j] += gain * sig[: j - i]

    starts = {}
    t = offset
    for name, nbars in structure:
        starts.setdefault(name, []).append(round(t, 3))
        n_beats = nbars * 4
        for k in range(n_beats):
            bt = t + k * beat
            prog = k / n_beats
            if name in ("intro", "outro", "break"):
                add(hat, bt + beat / 2, 0.15)
                g = 0.12 if name != "outro" else 0.12 * (1 - prog)
                n = int(beat * SR)
                tt = np.arange(n) / SR
                add(g * (np.sin(2 * np.pi * 220 * tt) + 0.5 * np.sin(2 * np.pi * 277 * tt)), bt, 1.0)
            elif name == "build":
                hits = 1 if prog < 0.5 else (2 if prog < 0.75 else 4)
                for hh in range(hits):
                    add(clap, bt + hh * beat / hits, 0.15 + 0.5 * prog)
                n = int(beat * SR)
                add(rng.standard_normal(n) * 0.05 * (0.2 + prog), bt, 1.0)
            elif name == "drop":
                add(kick, bt, 1.0)
                add(hat, bt + beat / 2, 0.4)
                if k % 2 == 1:
                    add(clap, bt, 0.5)
                n = int(beat * SR)
                tt = np.arange(n) / SR
                add(0.35 * np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 3), bt, 1.0)
        t += nbars * 4 * beat
    out = out / (np.abs(out).max() + 1e-9) * 0.9
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((out * 32767).astype(np.int16).tobytes())
    return {"starts": starts, "beat": beat, "duration": total}
