"""Estilos aprendidos de videos de referencia.

`learn()` analiza uno o varios videos y guarda su "receta" de edición medida en
beats (no en segundos), de modo que se puede aplicar a cualquier canción:

- estructura: negro inicial, paneles apilados y cuándo entra cada panel,
  duración de cada plano en beats
- transiciones (corte, disolución, flash, fundido a negro, slide, persona que entra)
  y su duración en beats
- efectos (glitch al final, flashes, barras de cine)
- cuánto movimiento tiene cada plano (para colocar tus clips más movidos donde
  la referencia tiene los planos más movidos)
- el perfil de color, para copiar el look

`apply_style()` convierte un estilo + tus clips + una canción en un proyecto JSON.
Si aprendes con el mismo nombre varias veces, cada video se guarda como una
variante del estilo y el color se promedia.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re

import numpy as np

from .analyze import Analysis, analyze
from .auto import AutoOptions, _rel, best_inpoint, motion_curve
from .beats import detect
from . import ff

BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "styles")
Q = 0.25  # resolución en beats


def _q(x: float, minimum: float = 0.0) -> float:
    return max(minimum, round(x / Q) * Q)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    return s or "estilo"


# --------------------------------------------------------------------------- #
# Aprender
# --------------------------------------------------------------------------- #

def variant_from_analysis(a: Analysis, source: str) -> dict:
    synced = bool(a.beats and a.beats.beats)
    b0 = a.beats.beats[0] if synced else 0.0
    p = a.beats.period if synced else 0.5

    def pos(t: float) -> float:
        return (t - b0) / p

    sections = []
    for i, s in enumerate(a.shots):
        start, end = _q(pos(s.start), minimum=-1e9), _q(pos(s.end), minimum=-1e9)
        sec: dict = {}
        if i == 0 and s.kind == "black" and s.start < 0.05 and abs(s.end - b0) < 0.2 and synced:
            sections.append({"type": "black", "until": "first_beat"})
            continue
        beats = max(Q, end - start)
        if s.kind == "black":
            sec = {"type": "black", "beats": beats}
        elif s.kind == "stack":
            sec = {"type": "stack", "beats": beats,
                   "panels": [_q(x / p) for x in (s.panels if len(s.panels) >= 2 else [0.0, 0.0])],
                   "motion": s.motion}
        else:
            sec = {"type": "clip", "beats": beats, "motion": s.motion}
            fx = []
            if s.glitch_at is not None:
                fx.append({"type": "glitch", "at": "end", "beats": _q((s.end - s.glitch_at) / p, Q)})
            if s.flashes:
                fx.append({"type": "flash", "beats": [_q(f / p) for f in s.flashes]})
            if fx:
                sec["effects"] = fx
        if sections and s.transition.get("type", "cut") != "cut":
            tr = {"type": s.transition["type"],
                  "beats": min(_q(s.transition.get("duration", 0.3) / p, Q), beats * 0.8)}
            if "direction" in s.transition:
                tr["direction"] = s.transition["direction"]
            sec["transition"] = tr
        sections.append(sec)
    first = a.shots[0] if a.shots else None
    start_beat = 0.0 if (sections and sections[0].get("until") == "first_beat") else \
        (_q(pos(first.start), minimum=-1e9) if first else 0.0)
    return {
        "source": os.path.basename(source),
        "start_beat": start_beat,
        "bpm": a.beats.bpm if synced else None,
        "beat_synced": synced,
        "duration": round(a.duration, 3),
        "sections": sections,
    }


def _stats(variants: list[dict]) -> dict:
    trans: dict[str, int] = {}
    effects: dict[str, int] = {}
    clip_beats = []
    for v in variants:
        for s in v["sections"]:
            t = s.get("transition", {}).get("type", "cut") if s["type"] != "black" else None
            if t:
                trans[t] = trans.get(t, 0) + 1
            for e in s.get("effects", []):
                effects[e["type"]] = effects.get(e["type"], 0) + 1
            if s["type"] == "clip":
                clip_beats.append(s["beats"])
    return {
        "transitions": trans,
        "effects": effects,
        "avg_clip_beats": round(float(np.mean(clip_beats)), 2) if clip_beats else None,
        "uses_stack": any(s["type"] == "stack" for v in variants for s in v["sections"]),
    }


def learn(videos: list[str], name: str, styles_dir: str, log=print) -> dict:
    """Analiza los videos y crea (o amplía) el estilo `name`."""
    os.makedirs(styles_dir, exist_ok=True)
    slug = slugify(name)
    path = os.path.join(styles_dir, slug + ".json")
    style = load_style(path) if os.path.exists(path) else None
    if style is None:
        style = {"name": name, "id": slug, "version": 1, "sources": [], "variants": [],
                 "color": None, "color_weight": 0, "letterbox": 0.0}
    for v in videos:
        log(f"  Analizando {os.path.basename(v)}…")
        a = analyze(v)
        var = variant_from_analysis(a, v)
        style["variants"].append(var)
        style["sources"].append(os.path.basename(v))
        style.setdefault("output", {"width": a.width - a.width % 2, "height": a.height - a.height % 2})
        wgt = style.get("color_weight", 0)
        if a.color:
            if style.get("color"):
                style["color"] = {c: [round((x * wgt + y) / (wgt + 1), 4) for x, y in zip(style["color"][c], a.color[c])]
                                  for c in "rgb"}
            else:
                style["color"] = a.color
            style["color_weight"] = wgt + 1
        style["letterbox"] = max(style.get("letterbox", 0.0), a.letterbox)
        log(f"    {len(var['sections'])} secciones · "
            f"{'%.0f BPM' % var['bpm'] if var['bpm'] else 'sin música (120 BPM supuestos)'}")
    style["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
    style["stats"] = _stats(style["variants"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(style, f, indent=2, ensure_ascii=False)
    style["path"] = path
    return style


# --------------------------------------------------------------------------- #
# Biblioteca
# --------------------------------------------------------------------------- #

def load_style(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    s["path"] = path
    return s


def list_styles(styles_dir: str | None) -> list[dict]:
    seen, out = set(), []
    for d in [styles_dir, BUILTIN_DIR]:
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json") and fn not in seen:
                seen.add(fn)
                try:
                    s = load_style(os.path.join(d, fn))
                    s["builtin"] = d == BUILTIN_DIR
                    out.append(s)
                except (OSError, ValueError):
                    continue
    return out


def find_style(name: str, styles_dir: str | None) -> dict:
    if os.path.isfile(name):
        return load_style(name)
    slug = slugify(name)
    for s in list_styles(styles_dir):
        if s.get("id") == slug or slugify(s.get("name", "")) == slug:
            return s
    raise ValueError(f"No existe el estilo '{name}'. Créalo con 'learn'.")


# --------------------------------------------------------------------------- #
# Aplicar
# --------------------------------------------------------------------------- #

def _slots(sections: list[dict]) -> int:
    return sum(len(s["panels"]) if s["type"] == "stack" else 1 for s in sections if s["type"] in ("stack", "clip"))


def _fit_sections(sections: list[dict], n_clips: int, max_beats: float | None) -> list[dict]:
    """Repite el 'cuerpo' (planos con corte seco y sin efectos) para usar más clips,
    o lo recorta si no cabe en la duración pedida."""
    secs = [dict(s) for s in sections]
    clip_idx = [i for i, s in enumerate(secs) if s["type"] == "clip"]
    body = [i for i in clip_idx[:-1]
            if "effects" not in secs[i] and secs[i].get("transition", {}).get("type", "cut") == "cut"]
    total = lambda ss: sum(s.get("beats", 0) for s in ss)  # noqa: E731
    if body:
        insert_at = body[-1] + 1
        k = 0
        while _slots(secs) < n_clips:
            tpl = dict(sections[body[k % len(body)]])
            if max_beats and total(secs) + tpl["beats"] > max_beats:
                break
            secs.insert(insert_at, tpl)
            insert_at += 1
            k += 1
    if max_beats:
        while total(secs) > max_beats:
            removable = [i for i, s in enumerate(secs) if s["type"] == "clip"][:-1]
            removable = [i for i in removable if "effects" not in secs[i]] or removable
            if not removable:
                break
            secs.pop(removable[-1])
    return secs


def _pick_variant(style: dict, n_clips: int, variant: int | None) -> dict:
    vs = style.get("variants") or []
    if not vs:
        raise ValueError(f"El estilo '{style.get('name')}' no tiene variantes")
    if variant is not None:
        return vs[variant % len(vs)]
    return min(vs, key=lambda v: abs(_slots(v["sections"]) - n_clips))


def _clip_motion(path: str) -> float:
    try:
        curve, _ = motion_curve(path, fps=4.0)
        return float(np.mean(curve))
    except ff.FFmpegError:
        return 0.0


def apply_style(style: dict, clips: list[str], music: str, opt: AutoOptions | None = None,
                project_dir: str = ".", variant: int | None = None, keep_order: bool = False,
                color_strength: float = 0.8) -> tuple[dict, object]:
    opt = opt or AutoOptions()
    if not clips:
        raise ValueError("Hacen falta clips")
    clips = [os.path.abspath(c) for c in clips]
    for c in clips + [music]:
        if not os.path.exists(c):
            raise FileNotFoundError(c)
    base = os.path.abspath(project_dir)
    info = detect(music, start=opt.music_start, duration=opt.max_duration)
    b0 = info.beats[0] if info.beats else 0.0
    p = info.period
    var = _pick_variant(style, len(clips), variant)
    max_beats = (opt.max_duration - b0) / p if opt.max_duration else None
    sections = _fit_sections(var["sections"], len(clips), max_beats)

    # Huecos para clips con su "movimiento" deseado
    slot_motion: list[float] = []
    for s in sections:
        if s["type"] == "stack":
            slot_motion += [s.get("motion", 0.5)] * len(s["panels"])
        elif s["type"] == "clip":
            slot_motion.append(s.get("motion", 0.5))
    order = list(range(len(slot_motion)))
    pool = [clips[i % len(clips)] for i in range(len(slot_motion))]
    if not keep_order and len(set(pool)) > 1:
        motion = {c: _clip_motion(c) for c in set(pool)}
        slots_sorted = sorted(order, key=lambda i: -slot_motion[i])
        clips_sorted = sorted(pool, key=lambda c: -motion[c])
        assigned = [""] * len(order)
        for si, c in zip(slots_sorted, clips_sorted):
            assigned[si] = c
        pool = assigned

    used_until: dict[str, float] = {}

    def inpoint(c: str, length: float) -> float:
        if c in used_until:  # clip repetido: siguiente tramo, o vuelta al principio
            start = used_until[c]
            if start + length > ff.duration(c):
                start = 0.0
        else:
            start = best_inpoint(c, length) if opt.smart_inpoints else 0.0
        used_until[c] = start + length
        return round(start, 3)

    segs: list[dict] = []
    pos = float(var.get("start_beat", 0.0))  # en beats desde el primer beat
    slot = 0

    def t(beat: float) -> float:
        return b0 + beat * p

    for s in sections:
        if s["type"] == "black" and s.get("until") == "first_beat":
            if b0 > 0.05:
                segs.append({"type": "black", "duration": round(b0, 3)})
            continue
        dur = round(t(pos + s["beats"]) - max(0.0, t(pos)), 3)
        if dur <= 0.02:
            pos += s["beats"]
            continue
        if s["type"] == "black":
            seg = {"type": "black", "duration": dur}
        elif s["type"] == "stack":
            panels = []
            for ap in s["panels"]:
                c = pool[slot]
                slot += 1
                appear = round(min(ap * p, dur * 0.9), 3)
                panels.append({"src": _rel(c, base), "in": inpoint(c, dur - appear), "appear": appear})
            seg = {"type": "stack", "duration": dur, "panels": panels}
        else:
            c = pool[slot]
            slot += 1
            seg = {"type": "clip", "src": _rel(c, base), "in": inpoint(c, dur), "duration": dur}
            fx = []
            for e in s.get("effects", []):
                if e["type"] == "glitch":
                    fx.append({"type": "glitch", "at": "end", "duration": round(min(e["beats"] * p, dur / 2), 3)})
                elif e["type"] == "flash":
                    fx.append({"type": "flash", "times": [round(b * p, 3) for b in e["beats"]]})
            if fx:
                seg["effects"] = fx
        tr = s.get("transition")
        if tr and segs:
            d = round(min(tr["beats"] * p, dur * 0.8), 3)
            seg["transition"] = {"type": tr["type"], "duration": d}
            if "direction" in tr:
                seg["transition"]["direction"] = tr["direction"]
        if style.get("letterbox"):
            seg.setdefault("effects", []).append({"type": "letterbox", "size": style["letterbox"]})
        segs.append(seg)
        pos += s["beats"]

    out = style.get("output", {})
    project = {
        "output": {"width": opt.width or out.get("width", 1080),
                   "height": opt.height or out.get("height", 1920),
                   "fps": opt.fps, "crf": 18},
        "music": {"src": _rel(os.path.abspath(music), base), "start": opt.music_start, "fade_out": 0.4},
        "grade": opt.grade or "none",
        "info": {"style": style.get("name"), "variant": var.get("source"), "bpm": info.bpm,
                 "beats": [round(b, 3) for b in info.beats[:128]]},
        "segments": segs,
    }
    if style.get("color") and color_strength > 0:
        project["color_match"] = style["color"]
        project["color_strength"] = color_strength
    return project, info
