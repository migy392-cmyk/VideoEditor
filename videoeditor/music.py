"""Análisis musical: el "mapa" de la canción que guía la edición.

- tempo, beats, primer tiempo de cada compás (downbeats) y frases de 4 compases
- curva de energía por beat (volumen, graves, agudos, golpes)
- secciones: intro, subida (build), drop / estribillo, bajada (breakdown), final
- el drop principal (el momento de más impacto)
- golpes fuertes (acentos) donde meter zooms o flashes
- pausas (silencios cortos, típicos justo antes del drop)
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

import numpy as np

from .beats import HOP, SR, WIN, detect, load_audio, onset_envelope

LABELS_ES = {"intro": "intro", "build": "subida", "drop": "drop", "break": "bajada", "outro": "final",
             "verse": "parte media"}


@dataclass
class Section:
    start: float
    end: float
    label: str      # intro | build | drop | break | verse | outro
    energy: float   # 0..1

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class MusicMap:
    duration: float
    bpm: float
    period: float
    beats: list[float]
    downbeats: list[float]
    beat_energy: list[float]          # 0..1 por beat
    sections: list[Section]
    drop: float | None                # segundo del drop principal
    drops: list[float] = field(default_factory=list)
    hits: list[float] = field(default_factory=list)       # golpes fuertes
    pauses: list[list[float]] = field(default_factory=list)  # [inicio, fin]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MusicMap":
        d = dict(d)
        d["sections"] = [Section(**s) for s in d["sections"]]
        return cls(**d)

    # ---- consultas ----
    def energy_at(self, t: float) -> float:
        if not self.beats:
            return 0.5
        i = int(np.clip(np.searchsorted(self.beats, t) - 1, 0, len(self.beat_energy) - 1))
        return self.beat_energy[i]

    def section_at(self, t: float) -> Section | None:
        for s in self.sections:
            if s.start <= t < s.end:
                return s
        return self.sections[-1] if self.sections else None

    def nearest_downbeat(self, t: float) -> float:
        if not self.downbeats:
            return t
        return min(self.downbeats, key=lambda d: abs(d - t))

    def shifted(self, start: float, length: float | None = None) -> "MusicMap":
        """Mapa visto desde `start` (como si la canción empezara ahí)."""
        end = self.duration if length is None else min(self.duration, start + length)

        def sh(ts):
            return [round(t - start, 3) for t in ts if start - 1e-6 <= t < end]

        keep = [i for i, b in enumerate(self.beats) if start - 1e-6 <= b < end]
        secs = [Section(max(0.0, s.start - start), min(end, s.end) - start, s.label, s.energy)
                for s in self.sections if s.end > start and s.start < end]
        return MusicMap(duration=round(end - start, 3), bpm=self.bpm, period=self.period,
                        beats=sh(self.beats), downbeats=sh(self.downbeats),
                        beat_energy=[self.beat_energy[i] for i in keep], sections=secs,
                        drop=(self.drop - start) if self.drop is not None and start <= self.drop < end else None,
                        drops=sh(self.drops), hits=sh(self.hits),
                        pauses=[[a - start, b - start] for a, b in self.pauses if start <= a < end])


def _band_energy(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frames = np.lib.stride_tricks.sliding_window_view(audio, WIN)[::HOP]
    spec = np.abs(np.fft.rfft(frames * np.hanning(WIN), axis=1)) ** 2
    freqs = np.fft.rfftfreq(WIN, 1 / SR)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    low = spec[:, freqs < 150].sum(axis=1)
    high = spec[:, freqs > 4000].sum(axis=1)
    return rms, low, high


def _norm(x: np.ndarray, lo_q: float = 5, hi_q: float = 98) -> np.ndarray:
    lo, hi = np.percentile(x, lo_q), np.percentile(x, hi_q)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, 1)


def analyze_music(path: str) -> MusicMap:
    info = detect(path)
    audio = load_audio(path)
    duration = len(audio) / SR
    p = info.period
    beats = list(info.beats)
    if len(beats) < 8 or len(audio) < WIN * 4:
        return MusicMap(duration=duration, bpm=info.bpm, period=p, beats=beats, downbeats=beats[::4],
                        beat_energy=[0.5] * len(beats), sections=[Section(0, duration, "verse", 0.5)], drop=None)

    rms, low, high = _band_energy(audio)
    env = onset_envelope(audio)
    fps = SR / HOP
    db = 20 * np.log10(rms + 1e-5)

    # Rasgos por beat
    feats = []
    for i, b in enumerate(beats):
        a = int(b * fps)
        e = int((beats[i + 1] if i + 1 < len(beats) else b + p) * fps)
        a = min(a, len(db) - 1)
        e = min(max(e, a + 1), len(db), len(env))
        feats.append([db[a:e].mean(), np.log10(low[a:e].mean() + 1e-9), np.log10(high[a:e].mean() + 1e-9),
                      env[a:e].max() if e <= len(env) else 0.0, low[a:min(a + 3, len(low))].mean()])
    feats = np.array(feats)
    loud, lowb, highb, onset, kick = (feats[:, k] for k in range(5))
    energy = 0.5 * _norm(loud) + 0.35 * _norm(lowb) + 0.15 * _norm(highb)
    flat = np.percentile(loud, 95) - np.percentile(loud, 5) < 4.0  # canción sin dinámica
    if flat:
        energy = np.full(len(loud), 0.7)

    # Downbeats: la fase (0-3) con más bombo/graves al inicio del beat
    # Los cambios de energía (entrada de un drop, de una subida…) caen al inicio de compás.
    # Contraste entre los 4 beats siguientes y los 4 anteriores (robusto a patrones alternos).
    fb = np.stack([_norm(loud), _norm(lowb), _norm(highb), _norm(onset)], axis=1)
    contrast = np.zeros(len(fb))
    for i in range(4, len(fb) - 3):
        contrast[i] = np.linalg.norm(fb[i:i + 4].mean(axis=0) - fb[i - 4:i].mean(axis=0))
    kick_n = _norm(kick)
    peaks = np.zeros_like(contrast)
    for i in range(1, len(contrast) - 1):
        if contrast[i] >= contrast[i - 1] and contrast[i] >= contrast[i + 1]:
            peaks[i] = contrast[i] ** 2  # cuentan sobre todo los cambios grandes
    phase = int(np.argmax([peaks[k::4].sum() + 0.01 * kick_n[k::4].mean() for k in range(4)]))
    downbeats = beats[phase::4]

    # Energía por compás y frases
    bar_e = np.array([energy[phase + 4 * k: phase + 4 * k + 4].mean() for k in range(len(downbeats))])
    if len(bar_e) == 0:
        bar_e = np.array([energy.mean()])

    nb = len(bar_e)
    # Rasgos por compás: volumen, graves, agudos, densidad de golpes
    def bar_feat(arr):
        return np.array([arr[phase + 4 * k: phase + 4 * k + 4].mean() for k in range(nb)])
    F = np.stack([bar_feat(_norm(loud)), bar_feat(_norm(lowb)), bar_feat(_norm(highb)),
                  bar_feat(_norm(onset))], axis=1) if nb else np.zeros((0, 4))

    # Fronteras: cambio de nivel de energía o de "textura" (novedad entre ventanas de 2 compases)
    hi_t = max(0.55, float(np.percentile(bar_e, 60))) if bar_e.max() > 0.5 else 1.1
    levels = np.where(bar_e >= hi_t, 2, np.where(bar_e >= 0.3, 1, 0))
    for k in range(1, nb - 1):
        if levels[k - 1] == levels[k + 1] != levels[k]:
            levels[k] = levels[k - 1]
    nov = np.zeros(nb)
    for k in range(2, nb - 1):
        nov[k] = np.linalg.norm(F[k:k + 2].mean(axis=0) - F[k - 2:k].mean(axis=0))
    thr = max(0.25, float(nov.mean() + nov.std())) if nb > 4 else 9
    bounds = {0}
    for k in range(1, nb):
        if levels[k] != levels[k - 1]:
            bounds.add(k)
        elif nov[k] >= thr and nov[k] >= nov[k - 1] and (k + 1 >= nb or nov[k] >= nov[k + 1]):
            bounds.add(k)
    bl = sorted(bounds)
    # Secciones de menos de 2 compases se funden con la vecina más parecida en energía
    segs_b = [[k, bl[i + 1] if i + 1 < len(bl) else nb] for i, k in enumerate(bl)]
    changed = True
    while changed and len(segs_b) > 1:
        changed = False
        for i, (a, b) in enumerate(segs_b):
            if b - a >= 2:
                continue
            e = bar_e[a:b].mean()
            prev_d = abs(e - bar_e[segs_b[i - 1][0]:segs_b[i - 1][1]].mean()) if i > 0 else 9
            next_d = abs(e - bar_e[segs_b[i + 1][0]:segs_b[i + 1][1]].mean()) if i + 1 < len(segs_b) else 9
            if prev_d <= next_d:
                segs_b[i - 1][1] = b
            else:
                segs_b[i + 1][0] = a
            segs_b.pop(i)
            changed = True
            break
    if not segs_b:
        segs_b = [[0, max(1, nb)]]

    def bar_time(k: int) -> float:
        if k <= 0:
            return 0.0
        if k < len(downbeats):
            return downbeats[k]
        return duration

    infos = []
    for a, b in segs_b:
        e = float(bar_e[a:b].mean())
        slope = float(np.polyfit(np.arange(b - a), bar_e[a:b], 1)[0]) if b - a >= 2 else 0.0
        dens = float(F[a:b, 3].mean()) if nb else 0.0
        infos.append((a, b, e, slope, dens))
    is_drop = [e >= hi_t * 0.95 or (levels[a:b] == 2).mean() >= 0.5 for a, b, e, _, _ in infos]

    sections: list[Section] = []
    for idx, (a, b, e, slope, dens) in enumerate(infos):
        nxt_drop = idx + 1 < len(infos) and is_drop[idx + 1]
        prev_drop = idx > 0 and is_drop[idx - 1]
        last = idx == len(infos) - 1
        if is_drop[idx]:
            label = "drop"
        elif nxt_drop and (slope > 0.01 or dens > (infos[idx - 1][4] if idx else 0) + 0.05) and \
                (idx > 0 or slope > 0.02):
            label = "build"  # sube la energía o se densifican los golpes justo antes de un drop
        elif idx == 0:
            label = "intro"
        elif last:
            label = "outro"
        elif prev_drop:
            label = "break"
        elif nxt_drop:
            label = "build"
        else:
            label = "verse"
        sections.append(Section(round(bar_time(a), 3), round(bar_time(b), 3), label, round(e, 3)))
    if sections:
        sections[-1].end = round(duration, 3)

    drops = [s.start for s in sections if s.label == "drop"]
    # El drop principal: el primero cuyo salto de energía al entrar es casi el mayor
    jumps_at = []
    for s_ in sections:
        if s_.label != "drop":
            continue
        k = int(np.searchsorted(downbeats, s_.start - 1e-3))
        before = bar_e[max(0, k - 2):k].mean() if k > 0 else 0.0
        jumps_at.append((s_.start, float(bar_e[k:k + 2].mean() - before)))
    drop = None
    if jumps_at and not flat and len(sections) > 1:
        top = max(j for _, j in jumps_at)
        drop = next(t for t, j in jumps_at if j >= 0.8 * top)

    # Golpes fuertes (acentos): el 25 % de golpes más fuertes, separados al menos 2 beats
    t_env = np.arange(len(env)) / fps
    peaks = [i for i in range(1, len(env) - 1) if env[i] > 0 and env[i] >= env[i - 1] and env[i] > env[i + 1]]
    hits = []
    if peaks:
        strong = np.percentile([env[i] for i in peaks], 75)
        for i in sorted(peaks, key=lambda i: -env[i]):
            if env[i] < strong:
                break
            t = float(t_env[i])
            if all(abs(t - h) >= 2 * p * 0.95 for h in hits):
                hits.append(t)
        # sólo los que caen cerca de un beat o medio beat
        hits = sorted(round(h, 3) for h in hits
                      if min(abs((h - beats[0]) / (p / 2) - round((h - beats[0]) / (p / 2))), 1) * p / 2 < 0.06)

    # Pausas: tramos casi en silencio de menos de 2 compases
    quiet = db < (np.percentile(db, 90) - 30)
    pauses = []
    i = 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            a, b = i / fps, j / fps
            if 0.15 < b - a < 8 * p and a > 0.5:
                pauses.append([round(a, 3), round(b, 3)])
            i = j
        else:
            i += 1

    return MusicMap(duration=round(duration, 3), bpm=info.bpm, period=p, beats=beats, downbeats=downbeats,
                    beat_energy=[round(float(x), 3) for x in energy], sections=sections, drop=drop,
                    drops=drops, hits=hits, pauses=pauses)


def cached_music_map(path: str, cache_dir: str | None = None) -> MusicMap:
    cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".cache", "videoeditor", "music")
    os.makedirs(cache_dir, exist_ok=True)
    st = os.stat(path)
    key = f"{os.path.basename(path)}_{st.st_size}_{int(st.st_mtime)}_v2.json"
    cp = os.path.join(cache_dir, key.replace(os.sep, "_"))
    if os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as f:
                return MusicMap.from_dict(json.load(f))
        except (OSError, ValueError, TypeError):
            pass
    m = analyze_music(path)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(m.to_dict(), f)
    return m


def report(m: MusicMap) -> str:
    lines = [f"Duración: {m.duration:.1f}s · Tempo: {m.bpm:.1f} BPM (1 beat = {m.period:.3f}s, "
             f"1 compás = {4 * m.period:.2f}s)"]
    if m.drop is not None:
        lines.append(f"Drop principal: {m.drop:.2f}s")
    lines.append("")
    lines.append(f"{'sección':<12} {'inicio':>7} {'fin':>7} {'compases':>9}  energía")
    for s in m.sections:
        bars = s.duration / (4 * m.period)
        bar = "█" * int(round(s.energy * 20))
        lines.append(f"{LABELS_ES.get(s.label, s.label):<12} {s.start:7.2f} {s.end:7.2f} {bars:9.1f}  {bar}")
    if m.hits:
        lines.append("")
        lines.append(f"Golpes fuertes ({len(m.hits)}): " + ", ".join(f"{h:.2f}" for h in m.hits[:24])
                     + (" …" if len(m.hits) > 24 else ""))
    if m.pauses:
        lines.append("Pausas: " + ", ".join(f"{a:.2f}-{b:.2f}" for a, b in m.pauses))
    return "\n".join(lines)


__all__ = ["MusicMap", "Section", "analyze_music", "cached_music_map", "report"]
