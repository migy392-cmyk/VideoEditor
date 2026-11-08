"""Render de proyectos con ffmpeg.

Estrategia:
1. Cada segmento se renderiza a un archivo intermedio (mismo tamaño, fps y
   formato). Si el segmento siguiente entra con una transición de duración d,
   este segmento se renderiza d segundos más largo para que la transición
   solape sin desplazar los tiempos: así los cortes caen exactamente en los
   tiempos (beats) del proyecto.
2. Los intermedios se unen con concat (cortes secos) o xfade (transiciones).
3. Se añade la música.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import subprocess
import time
from dataclasses import replace
from functools import lru_cache

from . import ff
from .effects import effect_filters, grade_filters
from .project import Project, Segment, Source, Text


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def _source_chain(src: Source, dur: float, w: int, h: int, fps: int) -> list[str]:
    """Filtros que convierten un input (ya recortado con -ss/-t) en un plano de w x h y `dur` segundos."""
    chain = []
    if src.reverse:
        chain.append("reverse")
    chain.append(f"setpts=(PTS-STARTPTS)/{src.speed}")
    chain.append(f"fps={fps}")
    sw, sh = _even(w * src.zoom), _even(h * src.zoom)
    chain.append(f"scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=lanczos")
    fx, fy = (min(1.0, max(0.0, v)) for v in src.focus)
    chain.append(f"crop={w}:{h}:x='(iw-ow)*{fx}':y='(ih-oh)*{fy}'")
    if src.mirror:
        chain.append("hflip")
    # Si el clip es más corto que lo pedido se congela el último frame.
    chain.append(f"tpad=stop_mode=clone:stop_duration={dur:.4f}")
    chain.append(f"trim=duration={dur:.4f}")
    chain.append("setsar=1")
    return chain


def _source_input(src: Source, dur: float) -> list[str]:
    need = dur * src.speed + 0.25
    return ["-ss", f"{src.inpoint:.4f}", "-t", f"{need:.4f}", "-i", src.src]


@lru_cache(maxsize=32)
def _font_file(font: str) -> str | None:
    """Resuelve un nombre de fuente (patrón fontconfig) o ruta a un archivo de fuente."""
    if os.path.isfile(font):
        return font
    if shutil.which("fc-match"):
        r = subprocess.run(["fc-match", "-f", "%{file}", font], capture_output=True, text=True)
        if r.returncode == 0 and os.path.isfile(r.stdout.strip()):
            return r.stdout.strip()
    return None


def _text_filters(texts: list[Text], seg_dur: float, tmpdir: str, tag: str) -> list[str]:
    out = []
    for i, t in enumerate(texts):
        path = os.path.join(tmpdir, f"text_{tag}_{i}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(t.text)
        end = seg_dur if t.end is None else t.end
        opts = [
            f"textfile='{path}'",
            f"fontsize={t.size}",
            f"fontcolor={t.color}",
            f"x=w*{t.x}-text_w/2",
            f"y=h*{t.y}-text_h/2",
            "shadowcolor=black@0.6:shadowx=2:shadowy=2",
            f"enable='between(t\\,{t.start:.3f}\\,{end:.3f})'",
        ]
        font = _font_file(t.font)
        if font:
            opts.insert(1, f"fontfile='{font}'")
        if t.box:
            opts += ["box=1", "boxcolor=black@0.5", "boxborderw=18"]
        out.append("drawtext=" + ":".join(opts))
    return out


def build_segment_command(project: Project, seg: Segment, dur: float, out_path: str,
                          tmpdir: str, tag: str) -> list[str]:
    o = project.output
    w, h, fps = o.width, o.height, o.fps
    inputs: list[str] = []
    graph: list[str] = []
    look = grade_filters(seg.grade if seg.grade is not None else project.grade)
    if project.lut:
        look.append(f"lut3d=file='{project.lut}'")

    if seg.type in ("black", "color"):
        color = "black" if seg.type == "black" else seg.color
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={dur:.4f}"]
        graph.append("[0:v]setsar=1[base]")
    elif seg.type == "clip":
        inputs += _source_input(seg.source, dur)
        graph.append("[0:v]" + ",".join(_source_chain(seg.source, dur, w, h, fps) + look) + "[base]")
    elif seg.type == "stack":
        n = len(seg.panels)
        ph = _even((h - seg.gap * (n - 1)) / n)
        inputs += ["-f", "lavfi", "-i", f"color=c={seg.color}:s={w}x{h}:r={fps}:d={dur:.4f}"]
        prev = "0:v"
        for i, p in enumerate(seg.panels):
            appear = min(p.appear, dur)
            pdur = max(0.05, dur - appear)
            inputs += _source_input(p, pdur)
            # El look se aplica a cada panel para no teñir el fondo/gaps.
            chain = _source_chain(p, pdur, w, ph, fps) + look
            chain.append(f"setpts=PTS+{appear:.4f}/TB")
            graph.append(f"[{i + 1}:v]" + ",".join(chain) + f"[p{i}]")
            y = i * (ph + seg.gap) if i < n - 1 else h - ph
            label = f"s{i}"
            graph.append(f"[{prev}][p{i}]overlay=x=0:y={y}:eof_action=pass:repeatlast=0[{label}]")
            prev = label
        graph.append(f"[{prev}]null[base]")
    else:  # pragma: no cover - validado en project.py
        raise ValueError(seg.type)

    post = effect_filters(seg.effects, seg.duration, w, h, fps)
    post += _text_filters(seg.texts, seg.duration, tmpdir, tag)
    post += [f"trim=duration={dur:.4f}", "setsar=1", "format=yuv420p"]
    graph.append("[base]" + ",".join(post) + "[out]")

    return [
        *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[out]",
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(max(0, o.crf - 4)),
        "-r", str(fps),
        out_path,
    ]


def build_assemble_command(project: Project, seg_files: list[str], durations: list[float],
                           out_path: str) -> list[str]:
    o = project.output
    segs = project.segments
    inputs: list[str] = []
    graph: list[str] = []
    for i, f in enumerate(seg_files):
        inputs += ["-i", f]
        graph.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={o.fps},settb=AVTB,setsar=1,format=yuv420p[v{i}]")

    cur = "v0"
    starts = project.segment_starts()
    for i in range(1, len(segs)):
        tr = segs[i].transition
        nxt = f"c{i}"
        if tr.xfade is None:
            graph.append(f"[{cur}][v{i}]concat=n=2:v=1:a=0[{nxt}]")
        else:
            graph.append(
                f"[{cur}][v{i}]xfade=transition={tr.xfade}:duration={tr.duration:.4f}:offset={starts[i]:.4f}[{nxt}]"
            )
        cur = nxt

    total = project.total_duration
    graph.append(f"[{cur}]trim=duration={total:.4f},setpts=PTS-STARTPTS[vout]")
    maps = ["-map", "[vout]"]

    m = project.music
    if m:
        ai = len(seg_files)
        inputs += ["-ss", f"{m.start:.4f}", "-i", m.src]
        af = [f"atrim=duration={total:.4f}", "asetpts=PTS-STARTPTS", f"volume={m.volume}",
              f"apad=whole_dur={total:.4f}"]
        if m.fade_in > 0:
            af.append(f"afade=t=in:st=0:d={m.fade_in}")
        if m.fade_out > 0:
            af.append(f"afade=t=out:st={max(0.0, total - m.fade_out):.4f}:d={m.fade_out}")
        graph.append(f"[{ai}:a]" + ",".join(af) + "[aout]")
        maps += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]

    return [
        *inputs,
        "-filter_complex", ";".join(graph),
        *maps,
        "-c:v", "libx264", "-preset", o.preset, "-crf", str(o.crf),
        "-pix_fmt", "yuv420p", "-r", str(o.fps),
        "-movflags", "+faststart",
        "-t", f"{total:.4f}",
        out_path,
    ]


def preview_project(project: Project, scale: float = 0.5) -> Project:
    o = project.output
    small = replace(o, width=_even(o.width * scale), height=_even(o.height * scale),
                    crf=max(o.crf, 26), preset="ultrafast")
    return replace(project, output=small)


def render(project: Project, out_path: str, *, verbose: bool = False, keep_temp: bool = False,
           log=print) -> str:
    ff.require_ffmpeg()
    t0 = time.time()
    tmpdir = tempfile.mkdtemp(prefix="videoeditor_")
    try:
        segs = project.segments
        seg_files, durs = [], []
        for i, seg in enumerate(segs):
            tail = segs[i + 1].transition.duration if i + 1 < len(segs) else 0.0
            dur = seg.duration + tail
            path = os.path.join(tmpdir, f"seg_{i:03d}.mp4")
            log(f"  [{i + 1}/{len(segs)}] {seg.type:<5} {seg.duration:6.2f}s"
                + (f"  → transición '{segs[i + 1].transition.type}'" if tail else ""))
            ff.run(build_segment_command(project, seg, dur, path, tmpdir, str(i)), verbose=verbose)
            seg_files.append(path)
            durs.append(dur)
        log("  Uniendo segmentos y música…")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        ff.run(build_assemble_command(project, seg_files, durs, out_path), verbose=verbose)
        log(f"  Listo en {time.time() - t0:.1f}s → {out_path}")
        return out_path
    finally:
        if keep_temp:
            log(f"  Temporales en {tmpdir}")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)
