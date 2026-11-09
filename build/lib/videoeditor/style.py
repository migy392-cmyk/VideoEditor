"""Estilos aprendidos de videos de referencia.

`learn()` analiza uno o varios videos y guarda su "receta" de edición medida en
beats (no en segundos), de modo que se puede aplicar a cualquier canción:

- estructura: negro inicial, paneles apilados y cuándo entra cada panel,
  duración de cada plano en beats
- transiciones (corte, disolución, flash, fundido a negro, slide, persona que entra,
  zoom a través del corte, barrido) y su duración en beats
- efectos (glitch al final, flashes, barras de cine, imagen congelada, estroboscopio
  con su ritmo en subdivisiones del beat, destellos en blanco y negro)
- planos en blanco y negro o desaturados y paneles en columnas o en cuadrícula
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
from .luts import apply_to_project_data
from . import ff

BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "styles")
Q = 0.25  # resolución en beats


def _q(x: float, minimum: float = 0.0) -> float:
    return max(minimum, round(x / Q) * Q)


# Subdivisiones del beat a las que se ajusta el ritmo de un estroboscopio
SUBDIV = (1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2, 2 / 3, 1.0, 2.0)
CENTERED = ("zoomthrough", "whip")  # transiciones centradas en el corte (el corte cae en el beat)


def _subdiv(beats: float) -> float:
    """Ajusta un periodo (en beats) a la subdivisión musical más cercana."""
    return round(min(SUBDIV, key=lambda s: abs(np.log(max(beats, 1e-3) / s))), 4)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    return s or "estilo"


# --------------------------------------------------------------------------- #
# Aprender
# --------------------------------------------------------------------------- #

def _music_energy_per_shot(a: Analysis, source: str) -> list[float | None]:
    from .music import analyze_music

    if not (a.beats and a.beats.beats):
        return [None] * len(a.shots)
    try:
        mm = analyze_music(source)
    except Exception:  # noqa: BLE001 - la energía es opcional
        return [None] * len(a.shots)
    out = []
    for sh in a.shots:
        idx = [i for i, b in enumerate(mm.beats) if sh.start - 0.05 <= b < sh.end]
        out.append(round(float(np.mean([mm.beat_energy[i] for i in idx])), 3) if idx else None)
    return out


def variant_from_analysis(a: Analysis, source: str) -> dict:
    synced = bool(a.beats and a.beats.beats)
    b0 = a.beats.beats[0] if synced else 0.0
    p = a.beats.period if synced else 0.5

    def pos(t: float) -> float:
        return (t - b0) / p

    menergy = _music_energy_per_shot(a, source)
    def snap(x: float) -> float:
        """Al beat si está cerca (la referencia puede tener pequeñas imprecisiones), si no a ½ beat."""
        return float(round(x)) if abs(x - round(x)) <= 0.3 else round(x * 2) / 2

    # Puntos de corte: si el plano entra con transición, lo que cae en el beat es el final
    # de la transición (el impacto), no su inicio.
    bounds = []
    for s in a.shots:
        ttype = s.transition.get("type", "cut")
        moving = ttype not in ("cut", "fade", "fadeblack", "flash", "dissolve", "fadewhite", *CENTERED)
        d = s.transition.get("duration", 0) if moving else 0
        if d:  # slide / persona que entra: en el beat cae el aterrizaje
            bounds.append(snap(pos(s.start + d)) - _q(d / p, Q))
        else:
            bounds.append(snap(pos(s.start)))
    ends = bounds[1:] + [snap(pos(a.shots[-1].end))] if a.shots else []

    sections = []
    for i, s in enumerate(a.shots):
        start, end = bounds[i], ends[i]
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
            if s.layout != "rows":
                sec["layout"] = s.layout
        else:
            sec = {"type": "clip", "beats": beats, "motion": s.motion}
            if menergy[i] is not None:
                sec["music_energy"] = menergy[i]
            fx = []
            if s.glitch_at is not None:
                fx.append({"type": "glitch", "at": "end", "beats": _q((s.end - s.glitch_at) / p, Q)})
            if s.flashes:
                fx.append({"type": "flash", "beats": [_q(f / p) for f in s.flashes]})
            if s.punches:
                fx.append({"type": "punch", "beats": [_q(t / p) for t, _ in s.punches],
                           "amount": round(float(np.mean([a_ for _, a_ in s.punches])), 3)})
            for t0, d, amt in s.shakes:
                fx.append({"type": "shake", "at_beats": _q(t0 / p), "beats": _q(d / p, Q), "amount": amt})
            for t0, d in s.freezes:
                fx.append({"type": "freeze", "at_beats": _q(t0 / p), "beats": _q(d / p, Q)})
            for st in s.strobes:
                fx.append({"type": "strobe", "at_beats": _q(st["at"] / p), "beats": _q(st["duration"] / p, Q),
                           "period_beats": _subdiv(1.0 / (st["rate"] * p)), "amount": st["amount"],
                           "duty": st["duty"], "color": st["color"]})
            if s.bw_flashes:
                fx.append({"type": "bw_flash", "beats": [_q(t0 / p) for t0, _ in s.bw_flashes],
                           "dur_beats": round(max(d for _, d in s.bw_flashes) / p, 3)})
            if s.grade:
                sec["grade"] = s.grade
            if s.speed:
                ramp = []
                for t0, d, sp in s.speed:
                    ramp += [[_q(t0 / p), sp], [_q((t0 + d) / p), 1.0]]
                sec["ramp_beats"] = ramp
            if s.texts:
                sec["texts"] = [{"text": tx["text"] or "TEXTO", "start_beats": _q(tx["start"] / p),
                                 "end_beats": _q(tx["end"] / p),
                                 "x": round(tx["box"][0] + tx["box"][2] / 2, 3),
                                 "y": round(tx["box"][1] + tx["box"][3] / 2, 3),
                                 "size_frac": round(tx["box"][3] * 1.15, 3)} for tx in s.texts]
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
            if s.get("ramp_beats"):
                effects["camara_lenta"] = effects.get("camara_lenta", 0) + 1
            if s.get("texts"):
                effects["texto"] = effects.get("texto", 0) + len(s["texts"])
            if s.get("grade"):
                effects[s["grade"]] = effects.get(s["grade"], 0) + 1
            if s.get("layout"):
                effects["paneles_" + s["layout"]] = effects.get("paneles_" + s["layout"], 0) + 1
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


def _fit_sections(sections: list[dict], n_clips: int, max_beats: float | None,
                  fill: bool = False) -> list[dict]:
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
        while _slots(secs) < n_clips or (fill and max_beats and total(secs) < max_beats):
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


def _climax_beats(var: dict) -> float:
    """Beats desde el primer beat hasta el primer plano completo tras la intro
    (el momento que conviene hacer coincidir con el drop)."""
    pos = float(var.get("start_beat", 0.0))
    for sec in var["sections"]:
        if sec["type"] == "black" and sec.get("until") == "first_beat":
            continue
        if sec["type"] == "clip":
            tr = sec.get("transition") or {}
            # si entra con transición, el impacto es al terminarla (en las centradas, en el corte)
            return pos + (tr.get("beats", 0) if tr.get("type") not in (None, "cut", *CENTERED) else 0)
        pos += sec.get("beats", 0)
    return 0.0


def _pace_by_music(sections: list[dict], mm, b0: float, p: float, start_beat: float,
                   max_beats: float | None) -> list[dict]:
    """Adapta el ritmo de cortes a la canción:
    - corta justo al empezar cada sección de la canción (drop, bajada…)
    - más cortes si la canción tiene más energía que la referencia en ese punto, menos si tiene menos
    """
    bounds = [(sec.start - b0) / p for sec in mm.sections[1:]]
    out: list[dict] = []
    pos = start_beat
    for sec in sections:
        if sec["type"] != "clip" or sec.get("until"):
            out.append(sec)
            pos += sec.get("beats", 0)
            continue
        beats = sec["beats"]
        body = "effects" not in sec and (sec.get("transition") or {}).get("type", "cut") == "cut"
        t_mid = b0 + (pos + beats / 2) * p
        e_new = mm.energy_at(t_mid)
        e_ref = sec.get("music_energy", 0.7)
        pieces = [beats]
        if body:
            if e_new >= e_ref + 0.25 and beats >= 4:
                pieces = [beats / 2, beats / 2]
            elif e_new <= e_ref - 0.3 and beats <= 4:
                pieces = [beats * 2]
        # partir en la frontera de sección musical si cae dentro del plano
        split = []
        cur = pos
        for pc in pieces:
            inner = [bd for bd in bounds if cur + 1 <= bd <= cur + pc - 1]
            if inner and body:
                bd = round(inner[0] / Q) * Q
                split += [bd - cur, cur + pc - bd]
            else:
                split.append(pc)
            cur += pc
        for k, pc in enumerate(split):
            piece = dict(sec)
            piece["beats"] = pc
            if k > 0:
                piece.pop("transition", None)
                piece.pop("effects", None)
            out.append(piece)
        pos += sum(split)
    if max_beats:
        total = start_beat
        trimmed = []
        for sec in out:
            if total + sec.get("beats", 0) > max_beats + 1e-6 and sec["type"] == "clip" and trimmed:
                # el último plano se ajusta para terminar justo en la duración
                rest = max_beats - total
                if rest >= 1:
                    last = dict(sec)
                    last["beats"] = rest
                    trimmed.append(last)
                break
            trimmed.append(sec)
            total += sec.get("beats", 0)
        out = trimmed
        # el efecto final (glitch…) del estilo va siempre en el último plano
        final_fx = next((s_.get("effects") for s_ in reversed(sections) if s_["type"] == "clip" and s_.get("effects")), None)
        if final_fx and out and out[-1]["type"] == "clip" and not out[-1].get("effects"):
            out[-1] = dict(out[-1], effects=final_fx)
    return out


def apply_style(style: dict, clips: list[str], music: str, opt: AutoOptions | None = None,
                project_dir: str = ".", variant: int | None = None, keep_order: bool = False,
                color_strength: float = 0.8, follow_music: bool = True, accents: bool = True,
                sound_design: bool = True, cinematic: bool = False,
                lut: str | None = None, lut_strength: float = 1.0) -> tuple[dict, object]:
    """Convierte estilo + clips + canción en un proyecto.

    Con `follow_music` el editor primero analiza la canción (secciones, drop, energía,
    golpes) y edita en función de ella:
    - si no se indica dónde empezar la canción, elige el inicio para que el momento
      fuerte del estilo coincida con el drop
    - si no se indica duración, termina al acabar la sección del drop (en un compás)
    - corta justo al cambiar de sección y ajusta la densidad de cortes a la energía
    - cámara lenta en las bajadas y rampa de cámara lenta justo antes del drop
    - flash + zoom de golpe en el drop y zooms en los golpes fuertes

    `lut` (opcional) se escribe en el proyecto: un preset (ver `luts.PRESETS`), un .cube, o
    "style" / "style:<id>" para el LUT aprendido del color de este estilo (en ese caso no se
    añade además el `color_match` por curvas, que haría lo mismo dos veces). `lut_strength` 0..1.
    """
    from .music import cached_music_map

    opt = opt or AutoOptions()
    if not clips:
        raise ValueError("Hacen falta clips")
    clips = [os.path.abspath(c) for c in clips]
    for c in clips + [music]:
        if not os.path.exists(c):
            raise FileNotFoundError(c)
    base = os.path.abspath(project_dir)
    var = _pick_variant(style, len(clips), variant)
    full = cached_music_map(music)
    p = full.period

    # --- dónde empieza la canción
    lead = 1.0 if var["sections"] and var["sections"][0].get("until") == "first_beat" else 0.0
    if opt.music_start is not None:
        start = float(opt.music_start)
    elif follow_music and full.drop is not None:
        start = full.drop - (_climax_beats(var) + lead) * p
        if start < 0:
            start = 0.0
        else:  # que caiga exactamente en un beat
            start = max(0.0, min(full.beats, key=lambda b: abs(b - start)) if full.beats else start)
    else:
        start = 0.0
    mm = full.shifted(start)
    if not mm.beats:
        raise ValueError("No se encontraron beats en la canción a partir de ese punto")
    b0 = mm.beats[0]
    if lead and b0 < 0.2:  # deja un beat de negro antes de empezar, como la referencia
        b0 += p

    # --- cuánto dura
    natural = sum(sec.get("beats", 0) for sec in var["sections"]) * p + b0
    if opt.max_duration:
        target = min(opt.max_duration, mm.duration)
    elif follow_music and mm.drop is not None:
        sec = mm.section_at(mm.drop + 0.01)
        target = min(45.0, mm.duration, max(natural, sec.end if sec else natural))
    else:
        target = None  # la duración natural del estilo (más larga si hay más clips)
    max_beats = (target - b0) / p if target else None
    start_beat = float(var.get("start_beat", 0.0))
    sections = _fit_sections(var["sections"], len(clips), max_beats, fill=follow_music and not opt.max_duration)
    if follow_music:
        sections = _pace_by_music(sections, mm, b0, p, start_beat, max_beats)

    # Huecos para clips con su "movimiento" deseado
    slot_motion: list[float] = []
    for s_ in sections:
        if s_["type"] == "stack":
            slot_motion += [s_.get("motion", 0.5)] * len(s_["panels"])
        elif s_["type"] == "clip":
            slot_motion.append(s_.get("motion", 0.5))
    order = list(range(len(slot_motion)))
    pool = [clips[i % len(clips)] for i in range(len(slot_motion))]
    if not keep_order and len(set(pool)) > 1:
        motion = {c: _clip_motion(c) for c in set(pool)}
        slots_sorted = sorted(order, key=lambda i: -slot_motion[i])
        clips_sorted = sorted(pool, key=lambda c: -motion[c])
        assigned = [""] * len(order)
        for si, c in zip(slots_sorted, clips_sorted):
            assigned[si] = c
        # evita el mismo clip dos veces seguidas
        for i in range(1, len(assigned)):
            if assigned[i] == assigned[i - 1]:
                for j in range(i + 1, len(assigned)):
                    if assigned[j] != assigned[i] and (j + 1 >= len(assigned) or assigned[j + 1] != assigned[i - 1]):
                        assigned[i], assigned[j] = assigned[j], assigned[i]
                        break
        pool = assigned

    used_until: dict[str, float] = {}

    def inpoint(c: str, length: float) -> float:
        if c in used_until:  # clip repetido: siguiente tramo, o vuelta al principio
            st = used_until[c]
            if st + length > ff.duration(c):
                st = 0.0
        else:
            st = best_inpoint(c, length) if opt.smart_inpoints else 0.0
        used_until[c] = st + length
        return round(st, 3)

    segs: list[dict] = []
    pos = start_beat  # en beats desde el primer beat
    slot = 0

    def t(beat: float) -> float:
        return b0 + beat * p

    for s_ in sections:
        if s_["type"] == "black" and s_.get("until") == "first_beat":
            if b0 > 0.05:
                segs.append({"type": "black", "duration": round(b0, 3)})
            continue
        dur = round(t(pos + s_["beats"]) - max(0.0, t(pos)), 3)
        if dur <= 0.02:
            pos += s_["beats"]
            continue
        seg_start = max(0.0, t(pos))
        if s_["type"] == "black":
            seg = {"type": "black", "duration": dur}
        elif s_["type"] == "stack":
            panels = []
            for ap in s_["panels"]:
                c = pool[slot]
                slot += 1
                appear = round(min(ap * p, dur * 0.9), 3)
                panels.append({"src": _rel(c, base), "in": inpoint(c, dur - appear), "appear": appear})
            seg = {"type": "stack", "duration": dur, "panels": panels}
            if s_.get("layout"):
                seg["layout"] = s_["layout"]
        else:
            c = pool[slot]
            slot += 1
            seg = {"type": "clip", "src": _rel(c, base), "duration": dur}
            fx = []
            for e in s_.get("effects", []):
                if e["type"] == "glitch":
                    fx.append({"type": "glitch", "at": "end", "duration": round(min(e["beats"] * p, dur / 2), 3)})
                elif e["type"] == "flash":
                    fx.append({"type": "flash", "times": [round(b * p, 3) for b in e["beats"]]})
                elif e["type"] == "punch":
                    fx.append({"type": "punch", "times": [round(b * p, 3) for b in e["beats"]],
                               "amount": e.get("amount", 0.1)})
                elif e["type"] == "shake":
                    fx.append({"type": "shake", "at": round(e.get("at_beats", 0) * p, 3),
                               "duration": round(min(e["beats"] * p, dur), 3), "amount": e.get("amount", 0.5)})
                elif e["type"] == "freeze":
                    at = round(e.get("at_beats", 0) * p, 3)
                    if at < dur - 0.1:
                        fx.append({"type": "freeze", "at": at, "duration": round(min(e["beats"] * p, dur - at), 3)})
                elif e["type"] == "strobe":
                    at = round(e.get("at_beats", 0) * p, 3)
                    if at < dur - 0.1:
                        fx.append({"type": "strobe", "at": at, "duration": round(min(e["beats"] * p, dur - at), 3),
                                   "rate": round(1.0 / (e.get("period_beats", 0.25) * p), 3),
                                   "amount": e.get("amount", 1.0), "duty": e.get("duty", 0.5),
                                   "color": e.get("color", "black")})
                elif e["type"] == "bw_flash":
                    times = [round(b * p, 3) for b in e["beats"] if b * p < dur - 0.05]
                    if times:
                        fx.append({"type": "bw_flash", "times": times,
                                   "duration": round(max(0.04, e.get("dur_beats", 0.25) * p), 3)})
            if s_.get("texts"):
                H = opt.height or style.get("output", {}).get("height", 1920)
                seg["texts"] = [{"text": tx["text"], "start": round(tx["start_beats"] * p, 3),
                                 "end": round(min(dur, tx["end_beats"] * p), 3), "x": tx["x"], "y": tx["y"],
                                 "size": max(16, int(tx["size_frac"] * H))} for tx in s_["texts"]]
            if s_.get("grade"):
                seg["grade"] = s_["grade"]
            if s_.get("speed"):
                seg["speed"] = s_["speed"]
            if s_.get("ramp_beats"):
                seg["ramp"] = [[round(b * p, 3), v] for b, v in s_["ramp_beats"]]
            if follow_music:
                lbl = (mm.section_at(seg_start + dur / 2) or None)
                if lbl and lbl.label == "break" and "speed" not in seg and "ramp" not in seg:
                    seg["speed"] = 0.6  # cámara lenta en las bajadas
                if accents and mm.drop is not None:
                    # zoom de golpe en golpes fuertes dentro de los drops
                    if lbl and lbl.label == "drop":
                        hit = next((h - seg_start for h in mm.hits if seg_start + 0.3 < h < seg_start + dur - 0.3), None)
                        if hit is not None:
                            fx.append({"type": "punch", "times": [round(hit, 3)], "amount": 0.06})
            seg["effects"] = fx
            seg["in"] = inpoint(c, dur * float(seg.get("speed", 1.0)))
        tr = s_.get("transition")
        if tr and segs:
            d = round(min(tr["beats"] * p, dur * 0.8), 3)
            if tr["type"] in CENTERED:  # la mitad de salida va al final del plano anterior
                d = round(min(d, segs[-1]["duration"] * 1.6), 3)
            seg["transition"] = {"type": tr["type"], "duration": d}
            if "direction" in tr:
                seg["transition"]["direction"] = tr["direction"]
        if style.get("letterbox"):
            seg.setdefault("effects", []).append({"type": "letterbox", "size": style["letterbox"]})
        if not seg.get("effects"):
            seg.pop("effects", None)
        segs.append(seg)
        pos += s_["beats"]

    if follow_music and accents and mm.drop is not None:
        _accent_drop(segs, mm.drop, p)

    out = style.get("output", {})
    total = sum(sg["duration"] for sg in segs)
    project = {
        "output": {"width": opt.width or out.get("width", 1080),
                   "height": opt.height or out.get("height", 1920),
                   "fps": opt.fps, "crf": 18},
        "music": {"src": _rel(os.path.abspath(music), base), "start": round(start, 3),
                  "fade_in": 0.15 if start > 0 else 0.0, "fade_out": 0.6},
        "grade": opt.grade or "none",
        "info": {"style": style.get("name"), "variant": var.get("source"), "bpm": mm.bpm,
                 "beats": [round(b, 3) for b in mm.beats if b <= total + 0.01],
                 "downbeats": [round(b, 3) for b in mm.downbeats if b <= total + 0.01],
                 "drop": mm.drop if mm.drop is not None and mm.drop <= total else None,
                 "sections": [{"start": round(x.start, 3), "end": round(min(x.end, total), 3), "label": x.label,
                               "energy": x.energy} for x in mm.sections if x.start < total],
                 "hits": [h for h in mm.hits if h <= total]},
        "segments": segs,
    }
    if sound_design:
        project["sound_design"] = True
    if cinematic:  # halo en las luces y grano de película en todos los planos con imagen
        for sg in segs:
            if sg["type"] in ("clip", "stack"):
                sg.setdefault("effects", []).extend([{"type": "halation", "amount": 0.3},
                                                     {"type": "film_grain", "amount": 7}])
    chosen = apply_to_project_data(project, lut, lut_strength, style=style, project_dir=base) if lut else None
    # Si se elige un LUT, el LUT manda: el color aprendido por curvas se aplicaría encima y
    # mezclaría dos looks distintos.
    if style.get("color") and color_strength > 0 and not chosen:
        project["color_match"] = style["color"]
        project["color_strength"] = color_strength
    return project, mm


def _accent_drop(segs: list[dict], drop: float, p: float) -> None:
    """Rampa de cámara lenta en el beat antes del drop y flash + zoom en el drop."""
    t = 0.0
    for i, sg in enumerate(segs):
        st, en = t, t + sg["duration"]
        t = en
        if abs(st - drop) < 0.06 and sg["type"] == "clip":
            fx = sg.setdefault("effects", [])
            if not any(e["type"] == "flash" for e in fx):
                fx.insert(0, {"type": "flash", "times": [0.0], "amount": 0.55, "decay": 0.1})
            fx[:] = [e for e in fx if not (e["type"] == "punch" and e["times"][0] < 1.0)]
            fx.insert(0, {"type": "punch", "times": [0.0], "amount": 0.1})
            prev = segs[i - 1] if i > 0 else None
            if prev and prev["type"] == "clip" and "ramp" not in prev and prev["duration"] > 2 * p:
                prev["ramp"] = [[round(prev["duration"] - p, 3), 0.35]]
                prev["ramp_ease"] = round(min(0.3, p / 2), 3)
            return
        if st < drop < en and sg["type"] == "clip" and drop - st > 0.3:
            fx = sg.setdefault("effects", [])
            fx[:] = [e for e in fx if not (e["type"] == "punch" and abs(e["times"][0] - (drop - st)) < 1.0)]
            fx.append({"type": "flash", "times": [round(drop - st, 3)], "amount": 0.55, "decay": 0.1})
            fx.append({"type": "punch", "times": [round(drop - st, 3)], "amount": 0.1})
            return
