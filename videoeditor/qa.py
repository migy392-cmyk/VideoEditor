"""Control de calidad: antes de renderizar (y corrige lo que pueda) y después
(revisa el video final).

Antes:
- clip demasiado corto para su hueco → se mueve el punto de entrada hacia atrás
  o, si no basta, se ralentiza un poco (en vez de congelar el último fotograma)
- punto de entrada más allá del final del clip
- clips HDR (iPhone) → se avisa de que se convierten a SDR
Después:
- fotogramas congelados o negros que no estaban previstos (las congelaciones del efecto
  `freeze` y los parpadeos del estroboscopio sí lo están)
- cortes fuera de tiempo respecto a los beats
- audio saturado y volumen (LUFS) para redes
- duración final
"""
from __future__ import annotations

import os
import re
from dataclasses import replace

import numpy as np

from . import ff, sysutil
from .effects import _window, freeze_windows
from .project import Project, Source

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def is_hdr(path: str) -> bool:
    for s in ff.probe(path).get("streams", []):
        if s.get("codec_type") == "video":
            return s.get("color_transfer") in HDR_TRANSFERS
    return False


def _fix_source(src: Source, dur: float, warn: list[str], where: str) -> Source:
    from .render import source_length

    try:
        avail = ff.duration(src.src)
    except ff.FFmpegError as e:
        warn.append(f"{where}: no se puede leer {src.src}: {e}")
        return src
    need = source_length(src, dur)
    if src.inpoint >= avail - 0.05:
        new_in = max(0.0, avail - need)
        warn.append(f"{where}: el punto de entrada ({src.inpoint:.2f}s) está fuera del clip "
                    f"({avail:.2f}s); se usa {new_in:.2f}s")
        src = replace(src, inpoint=new_in)
    if src.inpoint + need > avail + 0.04:
        short = src.inpoint + need - avail
        new_in = max(0.0, avail - need)
        if new_in < src.inpoint:
            warn.append(f"{where}: al clip le faltaban {short:.2f}s; entrada movida de "
                        f"{src.inpoint:.2f}s a {new_in:.2f}s")
            src = replace(src, inpoint=new_in)
        if src.inpoint + need > avail + 0.04 and not src.ramp:
            factor = max(0.4, (avail - src.inpoint) / need)
            warn.append(f"{where}: el clip dura {avail:.2f}s y el hueco necesita {need:.2f}s; "
                        f"se ralentiza a x{src.speed * factor:.2f} para no congelar la imagen")
            src = replace(src, speed=src.speed * factor)
    return src


def check_project(project: Project) -> list[str]:
    """Avisos sobre el proyecto que no se pueden corregir solos (música corta, transiciones que se pisan)."""
    warn: list[str] = []
    m = project.music
    total = project.total_duration
    if m:
        try:
            mdur = ff.duration(m.src)
        except ff.FFmpegError:
            mdur = None
        if mdur is not None:
            if m.start >= mdur - 0.05:
                warn.append(f"La música empieza en {m.start:.2f}s, más allá del final de la canción "
                            f"({mdur:.2f}s): el video saldrá sin sonido")
            elif mdur - m.start < total - 0.05:
                warn.append(f"La música ({mdur - m.start:.2f}s desde el segundo {m.start:.2f}) es más corta que "
                            f"el video ({total:.2f}s): el final quedará en silencio")
    segs = project.segments
    for i in range(1, len(segs) - 1):
        a, b = segs[i].transition, segs[i + 1].transition
        if a.centered and b.centered and a.duration / 2 + b.duration / 2 > segs[i].duration + 1e-6:
            warn.append(f"Segmento {i + 1}: las transiciones '{a.type}' y '{b.type}' se solapan dentro de un plano "
                        f"de {segs[i].duration:.2f}s (necesitan {a.duration / 2 + b.duration / 2:.2f}s); "
                        f"alarga el plano o acorta las transiciones")
    return warn


def preflight(project: Project) -> tuple[Project, list[str]]:
    """Revisa y corrige el proyecto antes de renderizar. Devuelve (proyecto corregido, avisos)."""
    warn: list[str] = []
    segs = []
    hdr_seen = set()
    for i, seg in enumerate(project.segments):
        where = f"Segmento {i + 1}"
        tail = project.segments[i + 1].transition.overlap if i + 1 < len(project.segments) else 0.0
        if seg.source:
            seg = replace(seg, source=_fix_source(seg.source, seg.duration + tail, warn, where))
            if is_hdr(seg.source.src):
                hdr_seen.add(seg.source.src)
        if seg.panels:
            panels = []
            for k, p in enumerate(seg.panels):
                panels.append(_fix_source(p, max(0.05, seg.duration + tail - p.appear), warn, f"{where}, panel {k + 1}"))
                if is_hdr(p.src):
                    hdr_seen.add(p.src)
            seg = replace(seg, panels=panels)
        segs.append(seg)
    warn += check_project(project)
    for h in sorted(hdr_seen):
        name = os.path.basename(h.replace("\\", "/"))
        warn.append(f"{name} es HDR (iPhone): se convierte a SDR con mapeo de tonos para que no salga lavado")
    return replace(project, segments=segs), warn


# --------------------------------------------------------------------------- #
# Después del render
# --------------------------------------------------------------------------- #

def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def check_output(path: str, project: Project, beats: list[float] | None = None) -> dict:
    """Revisa el video final. Devuelve {"ok": bool, "issues": [...], "stats": {...}}."""
    issues: list[str] = []
    stats: dict = {}
    dur = ff.duration(path)
    stats["duration"] = round(dur, 2)
    if abs(dur - project.total_duration) > 0.15:
        issues.append(f"La duración final ({dur:.2f}s) no coincide con el proyecto ({project.total_duration:.2f}s)")

    fps = 15.0
    raw = ff.capture(["-i", path, "-an", "-vf", f"fps={fps},scale=48:84,format=gray", "-f", "rawvideo", "-"])
    fr = np.frombuffer(raw, np.uint8)
    n = len(fr) // (48 * 84)
    fr = fr[: n * 48 * 84].reshape(n, 84, 48).astype(np.float32)

    # Tramos previstos: negros, cámara lenta (puede repetir fotogramas) y efectos de fundido
    planned_black = np.zeros(n, bool)
    slow = np.zeros(n, bool)
    t = 0.0
    for seg in project.segments:
        a, b = int(t * fps), int((t + seg.duration) * fps)
        if seg.type in ("black", "color"):
            planned_black[max(0, a - 2):b + 2] = True
        if seg.type == "stack" and seg.panels:  # hasta que entran todos los paneles hay zonas negras
            planned_black[a:a + int(max(p.appear for p in seg.panels) * fps) + 2] = True
        if any(e.type in ("fade_in", "fade_out") for e in seg.effects) or seg.transition.type == "fadeblack":
            planned_black[max(0, a - 8):a + 8] = True
            planned_black[max(0, b - 10):b + 2] = True
        src = seg.source
        if (src and (src.speed < 0.9 or src.ramp)) or any(p.speed < 0.9 for p in seg.panels):
            slow[a:b] = True
        # imagen congelada intencionada (efecto freeze) y parpadeos de un estroboscopio
        for fa, fb in freeze_windows(seg.effects, seg.duration):
            slow[max(0, int((t + fa) * fps) - 1):int((t + fb) * fps) + 2] = True
        for e in seg.effects:
            if e.type == "strobe":
                sa, sb = _window(e, seg.duration, 1.0)
                planned_black[max(0, int((t + sa) * fps) - 1):int((t + sb) * fps) + 2] = True
        t += seg.duration

    if n > 2:
        luma = fr.mean(axis=(1, 2))
        for a, b in _runs((luma < 6) & ~planned_black):
            if (b - a) / fps >= 0.2:
                issues.append(f"Negro no previsto entre {a / fps:.2f}s y {b / fps:.2f}s")
        diff = np.concatenate([[1.0], np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))])
        frozen = (diff < 0.15) & (luma >= 6) & ~slow
        for a, b in _runs(frozen):
            if (b - a) / fps >= 0.5:
                issues.append(f"Imagen congelada entre {a / fps:.2f}s y {b / fps:.2f}s (¿clip demasiado corto?)")

    if beats:
        off = []
        t = 0.0
        segs_ = project.segments
        for k, seg in enumerate(segs_[:-1]):
            t += seg.duration
            if t < 0.3:
                continue
            nxt = segs_[k + 1].transition
            hit = t + nxt.overlap  # con slide, cuenta el aterrizaje (en las centradas, el corte)
            d = min(min(abs(t - b) for b in beats), min(abs(hit - b) for b in beats))
            if d > 0.07:
                off.append(f"{t:.2f}s ({d * 1000:.0f} ms)")
        stats["cuts_off_beat"] = len(off)
        if off:
            issues.append("Cortes fuera de tiempo: " + ", ".join(off[:6]) + (" …" if len(off) > 6 else ""))

    if ff.has_audio(path):
        proc = ff.capture_stderr(["-i", path, "-vn", "-af", "astats=metadata=0,ebur128=framelog=quiet", "-f", "null", "-"])
        peak = re.findall(r"Peak level dB:\s*(-?[\d.]+|-inf)", proc)
        lufs = re.findall(r"I:\s*(-?[\d.]+) LUFS", proc)
        if peak:
            p = max(float(x) if x != "-inf" else -99.0 for x in peak)
            stats["peak_db"] = p
            if p > -0.1:
                issues.append(f"El audio satura (pico {p:.1f} dBFS)")
        if lufs:
            lv = float(lufs[-1])
            stats["lufs"] = lv
            if lv < -20:
                issues.append(f"El audio está bajo ({lv:.1f} LUFS; para redes se recomienda unos -14)")
            elif lv > -8:
                issues.append(f"El audio está muy alto ({lv:.1f} LUFS)")
    return {"ok": not issues, "issues": issues, "stats": stats}
