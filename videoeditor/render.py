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

from . import ff, luts
from .effects import effect_filters, grade_filters
from .project import Project, Segment, Source, Text


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def speed_pieces(src: Source, dur: float) -> list[tuple[float, float, float]]:
    """Tramos de velocidad constante: [(inicio en el edit, inicio en el clip, velocidad)].
    Los cambios de una rampa se suavizan con varios escalones (curva en S)."""
    pts: list[tuple[float, float]] = [(0.0, src.speed)]
    prev = src.speed
    for t, v in src.ramp:
        e = src.ramp_ease
        if e <= 0:
            pts.append((t, v))
        else:
            n = 8
            for k in range(n):
                x = (k + 0.5) / n
                sm = x * x * (3 - 2 * x)
                tt = t - e / 2 + e * k / n
                pts.append((max(0.0, tt), prev + (v - prev) * sm))
        prev = v
    pts = [p for p in sorted(pts) if p[0] < dur] or [(0.0, src.speed)]
    pieces, s_pos = [], 0.0
    for i, (t, v) in enumerate(pts):
        if pieces:
            o0, s0, v0 = pieces[-1]
            s_pos = s0 + (t - o0) * v0
        pieces.append((t, s_pos, v))
    return pieces


def source_length(src: Source, dur: float) -> float:
    """Segundos de clip que consume un plano de `dur` segundos."""
    o, s, v = speed_pieces(src, dur)[-1]
    return s + (dur - o) * v


def _nested(pieces: list[tuple[float, float, float]]) -> str:
    """Expresión de setpts: tiempo de salida en función del tiempo de clip (T)."""
    o, s0, v = pieces[-1]
    out = f"{o:.5f}+(T-{s0:.5f})/{v:.5f}"
    for i in range(len(pieces) - 2, -1, -1):
        o, s0, v = pieces[i]
        boundary = pieces[i + 1][1]
        out = f"if(lt(T\\,{boundary:.5f})\\,{o:.5f}+(T-{s0:.5f})/{v:.5f}\\,{out})"
    return out


def _hdr_transfer(path: str) -> str | None:
    from .qa import HDR_TRANSFERS

    try:
        for st in ff.probe(path).get("streams", []):
            if st.get("codec_type") == "video":
                t = st.get("color_transfer")
                return t if t in HDR_TRANSFERS else None
    except ff.FFmpegError:
        return None
    return None


def _hdr_chain(trc: str) -> list[str]:
    """HDR (iPhone) a SDR con mapeo de tonos; si no, se ve lavado/gris."""
    return [f"zscale=tin={trc}:min=bt2020nc:pin=bt2020:t=linear:npl=100", "format=gbrpf32le",
            "zscale=p=bt709", "tonemap=tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv",
            "format=yuv420p"]


def _source_chain(src: Source, dur: float, w: int, h: int, fps: int) -> list[str]:
    """Filtros que convierten un input (ya recortado con -ss/-t) en un plano de w x h y `dur` segundos."""
    chain = []
    trc = _hdr_transfer(src.src)
    if trc:
        chain += _hdr_chain(trc)
    if src.stabilize:
        chain.append(f"vidstabtransform=input='{src.stabilize}':smoothing=12:zoom=4:optzoom=0:interpol=bilinear")
    if src.reverse:
        chain.append("reverse")
    if src.ramp and not src.reverse:
        chain.append("setpts=PTS-STARTPTS")
        chain.append(f"setpts='{_nested(speed_pieces(src, dur))}/TB'")
    else:
        chain.append(f"setpts=(PTS-STARTPTS)/{src.speed}")
    chain.append(f"fps={fps}")
    sw, sh = _even(w * src.zoom), _even(h * src.zoom)
    chain.append(f"scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=lanczos")
    fx, fy = (min(1.0, max(0.0, v)) for v in src.focus)
    chain.append(f"crop={w}:{h}:x='(iw-ow)*{fx}':y='(ih-oh)*{fy}'")
    if src.mirror:
        chain.append("hflip")
    # Red de seguridad: si aun así faltara clip, se congela el último frame (el QA lo avisa).
    chain.append(f"tpad=stop_mode=clone:stop_duration={dur:.4f}")
    chain.append(f"trim=duration={dur:.4f}")
    chain.append("setsar=1")
    return chain


def panel_boxes(seg: Segment, w: int, h: int) -> list[tuple[int, int, int, int]]:
    """Posición y tamaño (x, y, ancho, alto) de cada panel de un 'stack' según su `layout`:
    rows = franjas horizontales de arriba a abajo, columns = columnas de izquierda a derecha,
    grid = cuadrícula de 2 columnas (de izquierda a derecha y de arriba a abajo)."""
    n, g = len(seg.panels), seg.gap
    out = []
    if seg.layout == "columns":
        pw = _even((w - g * (n - 1)) / n)
        for i in range(n):
            out.append((i * (pw + g) if i < n - 1 else w - pw, 0, pw, h))
    elif seg.layout == "grid":
        cols, rows = 2, (n + 1) // 2
        pw = _even((w - g) / 2)
        ph = _even((h - g * (rows - 1)) / rows)
        for i in range(n):
            r, c = divmod(i, cols)
            out.append((0 if c == 0 else w - pw, r * (ph + g) if r < rows - 1 else h - ph, pw, ph))
    else:
        ph = _even((h - g * (n - 1)) / n)
        for i in range(n):
            out.append((0, i * (ph + g) if i < n - 1 else h - ph, w, ph))
    return out


def expand_transitions(project: Project) -> Project:
    """Las transiciones centradas en el corte (zoomthrough, whip) se convierten en efectos:
    la mitad de salida al final del segmento anterior y la de entrada al principio del siguiente."""
    from .project import Effect

    segs = list(project.segments)
    for i in range(1, len(segs)):
        tr = segs[i].transition
        if not tr.centered or tr.duration <= 0:
            continue
        half = round(tr.duration / 2, 4)
        kind = "tr_zoom" if tr.type == "zoomthrough" else "tr_whip"
        params = {"duration": half}
        if kind == "tr_whip":
            params["direction"] = tr.direction
        # la salida va al final (después de los efectos del plano); la entrada, antes que todos
        segs[i - 1] = replace(segs[i - 1], effects=segs[i - 1].effects + [Effect(kind, {**params, "phase": "out"})])
        segs[i] = replace(segs[i], effects=[Effect(kind, {**params, "phase": "in"})] + segs[i].effects)
    return replace(project, segments=segs)


def _source_input(src: Source, dur: float) -> list[str]:
    need = source_length(src, dur) + 0.25
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


@lru_cache(maxsize=256)
def _source_quantiles(path: str, inpoint: float, length: float) -> tuple[tuple[float, ...], ...]:
    import numpy as np

    from .analyze import QUANTILES

    raw = ff.capture(["-ss", f"{inpoint:.3f}", "-t", f"{max(0.2, length):.3f}", "-i", path, "-an",
                      "-vf", "fps=4,scale=64:-2,format=rgb24", "-f", "rawvideo", "-"])
    px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.float32) / 255.0
    if len(px) == 0:
        return tuple(tuple(QUANTILES) for _ in range(3))
    return tuple(tuple(float(v) for v in np.quantile(px[:, c], QUANTILES)) for c in range(3))


def _color_match_filter(src: Source, dur: float, target: dict, strength: float) -> str:
    """Curvas RGB que llevan la distribución de color del clip hacia la aprendida."""
    srcq = _source_quantiles(src.src, src.inpoint, dur * src.speed)
    parts = []
    for c, ch in enumerate("rgb"):
        pts = [(0.0, 0.0)]
        for x, y in zip(srcq[c], target[ch]):
            y = x + strength * (y - x)
            if pts[-1][0] + 0.01 < x < 0.99:
                pts.append((x, max(pts[-1][1], min(1.0, y))))
        pts.append((1.0, max(pts[-1][1], 1.0)))
        parts.append(f"{ch}='" + " ".join(f"{x:.3f}/{y:.3f}" for x, y in pts) + "'")
    return "curves=" + ":".join(parts)


def _look(project: Project, seg: Segment, src: Source | None, dur: float) -> list[str]:
    """Color de un plano: look aprendido (color_match) + grade + LUT (preset, aprendido o archivo)."""
    look = []
    if project.color_match and src is not None and project.color_strength > 0:
        look.append(_color_match_filter(src, dur, project.color_match, min(1.0, project.color_strength)))
    look += grade_filters(seg.grade if seg.grade is not None else project.grade)
    # LUT: el del segmento si lo tiene ("none" lo desactiva), si no el del proyecto.
    # Se aplica por plano (no a negros/color ni a los fondos de los stacks).
    spec = seg.lut if seg.lut is not None else project.lut
    strength = seg.lut_strength if seg.lut_strength is not None else project.lut_strength
    flt = luts.lut_filter(spec, strength, project.base_dir)
    if flt:
        look.append(flt)
    return look


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

    if any(e.type == "stabilize" for e in seg.effects) and seg.source:
        trf = os.path.join(tmpdir, f"stab_{tag}.trf")
        ff.run([*_source_input(seg.source, dur), "-vf", f"vidstabdetect=shakiness=7:accuracy=12:result='{trf}'",
                "-f", "null", "-"])
        seg = replace(seg, source=replace(seg.source, stabilize=trf))

    if seg.type in ("black", "color"):
        color = "black" if seg.type == "black" else seg.color
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={dur:.4f}"]
        graph.append("[0:v]setsar=1[base]")
    elif seg.type == "clip":
        inputs += _source_input(seg.source, dur)
        look = _look(project, seg, seg.source, dur)
        graph.append("[0:v]" + ",".join(_source_chain(seg.source, dur, w, h, fps) + look) + "[base]")
    elif seg.type == "stack":
        inputs += ["-f", "lavfi", "-i", f"color=c={seg.color}:s={w}x{h}:r={fps}:d={dur:.4f}"]
        prev = "0:v"
        for i, (p, (x, y, pw, ph)) in enumerate(zip(seg.panels, panel_boxes(seg, w, h))):
            appear = min(p.appear, dur)
            pdur = max(0.05, dur - appear)
            inputs += _source_input(p, pdur)
            # El look se aplica a cada panel para no teñir el fondo/gaps.
            chain = _source_chain(p, pdur, pw, ph, fps) + _look(project, seg, p, pdur)
            chain.append(f"setpts=PTS+{appear:.4f}/TB")
            graph.append(f"[{i + 1}:v]" + ",".join(chain) + f"[p{i}]")
            label = f"s{i}"
            graph.append(f"[{prev}][p{i}]overlay=x={x}:y={y}:eof_action=pass:repeatlast=0[{label}]")
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


def _drop_xy(direction: str, start: float, d: float) -> tuple[str, str]:
    p = f"pow(1-clip((t-{start:.4f})/{d:.4f}\\,0\\,1)\\,3)"  # ease-out cúbico
    return {
        "top": ("0", f"-main_h*{p}"),
        "bottom": ("0", f"main_h*{p}"),
        "left": (f"-main_w*{p}", "0"),
        "right": (f"main_w*{p}", "0"),
    }[direction]


def build_assemble_command(project: Project, seg_files: list[str], durations: list[float],
                           out_path: str, cutouts: dict[int, str] | None = None) -> list[str]:
    cutouts = cutouts or {}
    o = project.output
    segs = project.segments
    inputs: list[str] = []
    graph: list[str] = []
    for i, f in enumerate(seg_files):
        inputs += ["-i", f]
        graph.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={o.fps},settb=AVTB,setsar=1,format=yuv420p[v{i}]")

    cut_idx = {}
    for i, path in cutouts.items():
        cut_idx[i] = len(seg_files) + len(cut_idx)
        inputs += ["-i", path]

    cur = "v0"
    starts = project.segment_starts()
    for i in range(1, len(segs)):
        tr = segs[i].transition
        nxt = f"c{i}"
        if tr.type == "subject_drop" and i in cutouts:
            x, y = _drop_xy(tr.direction, starts[i], tr.duration)
            graph.append(f"[{cut_idx[i]}:v]setpts=PTS-STARTPTS+{starts[i]:.4f}/TB,format=yuva420p[cut{i}]")
            graph.append(f"[{cur}][cut{i}]overlay=x='{x}':y='{y}':eval=frame:eof_action=pass[ov{i}]")
            graph.append(f"[v{i}]trim=start={tr.duration:.4f},setpts=PTS-STARTPTS[vb{i}]")
            graph.append(f"[ov{i}][vb{i}]concat=n=2:v=1:a=0[{nxt}]")
        elif tr.xfade is None:
            graph.append(f"[{cur}][v{i}]concat=n=2:v=1:a=0[{nxt}]")
        else:
            graph.append(
                f"[{cur}][v{i}]xfade=transition={tr.xfade}:duration={tr.duration:.4f}:offset={starts[i]:.4f}[{nxt}]"
            )
        cur = nxt

    total = project.total_duration
    post = []
    if project.motion_blur:
        from .sfx import MOVING

        wins = []
        for i in range(1, len(segs)):
            tr = segs[i].transition
            if tr.type in MOVING and tr.duration > 0:
                a = starts[i] - (tr.duration / 2 if tr.centered else 0.0)
                wins.append((max(0.0, a), a + tr.duration))
        if wins:  # mezcla de fotogramas vecinos = desenfoque de movimiento durante la transición
            en = "+".join(f"between(t\\,{a:.4f}\\,{b:.4f})" for a, b in wins)
            # tmix con `enable` retrasa todo el video 2 fotogramas (y 1 dentro de la ventana): se
            # mezcla una copia, se centra (fotograma k = mezcla de k-1, k, k+1) y se superpone
            # sólo durante las transiciones, sin desplazar nada.
            # (marcas de tiempo idénticas en las dos ramas, si no overlay repite fotogramas)
            graph.append(f"[{cur}]setpts=N/({o.fps}*TB),split[mb0][mb1]")
            graph.append(f"[mb1]tmix=frames=3:weights='1 2 1',trim=start_frame=1,setpts=N/({o.fps}*TB)[mb2]")
            graph.append(f"[mb0][mb2]overlay=eof_action=pass:enable='{en}'[mbo]")
            cur = "mbo"
    post += [f"trim=duration={total:.4f}", "setpts=PTS-STARTPTS"]
    graph.append(f"[{cur}]" + ",".join(post) + "[vout]")
    maps = ["-map", "[vout]"]

    n_in = len(seg_files) + len(cutouts)
    music_label = None
    m = project.music
    if m:
        ai = n_in
        n_in += 1
        inputs += ["-ss", f"{m.start:.4f}", "-i", m.src]
        af = [f"atrim=duration={total:.4f}", "asetpts=PTS-STARTPTS"]
        if m.normalize:  # volumen estándar para redes (-14 LUFS) sin saturar
            af.append("loudnorm=I=-14:TP=-1.5:LRA=11")
        af += [f"volume={m.volume}", f"apad=whole_dur={total:.4f}"]
        if m.fade_in > 0:
            af.append(f"afade=t=in:st=0:d={m.fade_in}")
        if m.fade_out > 0:
            af.append(f"afade=t=out:st={max(0.0, total - m.fade_out):.4f}:d={m.fade_out}")
        graph.append(f"[{ai}:a]" + ",".join(af) + ",aformat=sample_rates=44100:channel_layouts=stereo[amus]")
        music_label = "amus"

    cues = _sfx_cues(project)
    sfx_labels = []
    for k, c in enumerate(cues):
        from .sfx import sample

        inputs += ["-i", sample(c["type"], c.get("duration"))]
        ms = int(c["at"] * 1000)
        vol = c.get("volume", 0.6) * project.sfx_volume
        graph.append(f"[{n_in}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={vol:.3f},"
                     f"adelay={ms}|{ms}[sx{k}]")
        sfx_labels.append(f"sx{k}")
        n_in += 1
    if music_label or sfx_labels:
        labels = ([music_label] if music_label else []) + sfx_labels
        if len(labels) == 1:
            graph.append(f"[{labels[0]}]atrim=duration={total:.4f},apad=whole_dur={total:.4f}[aout]")
        else:
            graph.append("".join(f"[{l_}]" for l_ in labels) +
                         f"amix=inputs={len(labels)}:duration=first:normalize=0,"
                         f"alimiter=limit=0.79:level=0,atrim=duration={total:.4f},apad=whole_dur={total:.4f}[aout]")
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


def _sfx_cues(project: Project) -> list[dict]:
    from .sfx import auto_cues

    cues = list(project.sfx or [])
    if project.sound_design:
        cues += auto_cues(project, project.drop)
    total = project.total_duration
    return [c for c in cues if c["at"] < total - 0.05]


def preview_project(project: Project, scale: float = 0.5) -> Project:
    o = project.output
    small = replace(o, width=_even(o.width * scale), height=_even(o.height * scale),
                    crf=max(o.crf, 26), preset="ultrafast")
    return replace(project, output=small)


def _make_cutouts(project: Project, tmpdir: str, log) -> dict[int, str]:
    """Recorta a la persona del inicio de cada segmento que entra con `subject_drop`.
    Si no hay segmentación disponible se usa la transición de respaldo (slidedown)."""
    from . import segment

    o = project.output
    out: dict[int, str] = {}
    for i, seg in enumerate(project.segments):
        tr = seg.transition
        if tr.type != "subject_drop" or i == 0:
            continue
        if seg.type != "clip":
            log(f"  Aviso: subject_drop en el segmento {i} necesita un clip; uso 'slidedown'")
            continue
        look = _look(project, seg, seg.source, tr.duration)
        chain = ",".join(_source_chain(seg.source, tr.duration, o.width, o.height, o.fps) + look)
        path = os.path.join(tmpdir, f"cutout_{i:03d}.mkv")
        try:
            log(f"  Recortando persona para la transición del segmento {i + 1}…")
            segment.cutout_video(_source_input(seg.source, tr.duration), chain, o.width, o.height,
                                 o.fps, tr.duration, path)
            out[i] = path
        except segment.SegmentationUnavailable as e:
            log(f"  Aviso: {e}\n  Uso 'slidedown' en su lugar.")
    return out


def render(project: Project, out_path: str, *, verbose: bool = False, keep_temp: bool = False,
           log=print, qa: bool = True, report: dict | None = None) -> str:
    """Renderiza el proyecto. Con `qa`, antes corrige lo que pueda (clips cortos…) y
    después revisa el resultado; los avisos van al log y a `report` si se pasa."""
    from . import qa as qa_mod

    ff.require_ffmpeg()
    t0 = time.time()
    if qa:
        project, warns = qa_mod.preflight(project)
        for w_ in warns:
            log(f"  ⚠ {w_}")
        if report is not None:
            report["preflight"] = warns
    tmpdir = tempfile.mkdtemp(prefix="videoeditor_")
    try:
        project = expand_transitions(project)
        segs = project.segments
        seg_files, durs = [], []
        for i, seg in enumerate(segs):
            tail = segs[i + 1].transition.overlap if i + 1 < len(segs) else 0.0
            dur = seg.duration + tail
            path = os.path.join(tmpdir, f"seg_{i:03d}.mp4")
            log(f"  [{i + 1}/{len(segs)}] {seg.type:<5} {seg.duration:6.2f}s"
                + (f"  → transición '{segs[i + 1].transition.type}'" if tail else ""))
            ff.run(build_segment_command(project, seg, dur, path, tmpdir, str(i)), verbose=verbose)
            seg_files.append(path)
            durs.append(dur)
        cutouts = _make_cutouts(project, tmpdir, log)
        log("  Uniendo segmentos y música…")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        ff.run(build_assemble_command(project, seg_files, durs, out_path, cutouts), verbose=verbose)
        log(f"  Listo en {time.time() - t0:.1f}s → {out_path}")
        if qa:
            res = qa_mod.check_output(out_path, project, beats=getattr(project, "beats", None))
            if report is not None:
                report["check"] = res
            if res["ok"]:
                log("  ✓ Revisión: sin problemas" + (f" · {res['stats'].get('lufs')} LUFS" if "lufs" in res["stats"] else ""))
            for i_ in res["issues"]:
                log(f"  ⚠ {i_}")
        return out_path
    finally:
        if keep_temp:
            log(f"  Temporales en {tmpdir}")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)
