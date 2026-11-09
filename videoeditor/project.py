"""Modelo de proyecto: carga y valida el JSON que describe una edición.

Formato (todas las rutas son relativas al archivo JSON):

{
  "output":  {"width": 1080, "height": 1920, "fps": 30, "crf": 18},
  "music":   {"src": "cancion.mp3", "start": 0, "volume": 1.0, "fade_out": 0.4},
  "grade":   "night_blue",
  "lut":     "noche_azul",       // opcional: preset, "style:<estilo>" o ruta a un .cube
  "lut_strength": 0.8,           // 0..1 (cada segmento puede tener su "lut" / "lut_strength")
  "segments": [
    {"type": "black", "duration": 0.5},
    {"type": "stack", "duration": 3.5, "panels": [
        {"src": "a.mov", "in": 0.0, "appear": 0.0},
        {"src": "b.mov", "in": 1.0, "appear": 1.0},
        {"src": "c.mov", "in": 0.0, "appear": 2.0}]},
    {"type": "clip", "src": "d.mov", "in": 2.0, "duration": 2.0,
     "speed": 1.0, "zoom": 1.0, "focus": [0.5, 0.5],
     "transition": {"type": "cut"},
     "effects": [{"type": "glitch", "at": "end", "duration": 0.6}]}
  ]
}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

SEGMENT_TYPES = ("clip", "stack", "black", "color")

# Transiciones soportadas -> nombre de transición de xfade de ffmpeg.
TRANSITIONS = {
    "cut": None,
    "fade": "fade",
    "fadeblack": "fadeblack",
    "flash": "fadewhite",
    "fadewhite": "fadewhite",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "slideup": "slideup",
    "slidedown": "slidedown",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "wipeup": "wipeup",
    "wipedown": "wipedown",
    "zoom": "zoomin",
    "zoomin": "zoomin",
    "pixelize": "pixelize",
    "dissolve": "dissolve",
    "circleopen": "circleopen",
    "radial": "radial",
    "smoothleft": "smoothleft",
    "smoothright": "smoothright",
    "smoothup": "smoothup",
    "smoothdown": "smoothdown",
    "squeezeh": "squeezeh",
    "squeezev": "squeezev",
    "hblur": "hblur",
    # Especial: la persona del plano siguiente (recortada del fondo) entra sobre el plano actual.
    "subject_drop": "slidedown",
}

DROP_DIRECTIONS = ("top", "bottom", "left", "right")


class ProjectError(ValueError):
    pass


@dataclass
class Transition:
    type: str = "cut"
    duration: float = 0.0
    direction: str = "top"  # sólo subject_drop

    @property
    def xfade(self) -> str | None:
        return TRANSITIONS[self.type]


@dataclass
class Effect:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


@dataclass
class Source:
    """Un fragmento de un archivo fuente colocado en pantalla."""

    src: str
    inpoint: float = 0.0
    speed: float = 1.0
    zoom: float = 1.0
    focus: tuple[float, float] = (0.5, 0.5)
    appear: float = 0.0  # sólo para paneles: segundo (relativo al segmento) en que aparece
    mirror: bool = False
    reverse: bool = False
    # Rampa de velocidad: cambios de velocidad en segundos del segmento [(t, velocidad), ...].
    # Antes del primer punto se usa `speed`. Cada cambio se suaviza durante `ramp_ease` segundos.
    ramp: tuple[tuple[float, float], ...] = ()
    ramp_ease: float = 0.25
    stabilize: str | None = None  # interno: archivo de vidstabdetect


@dataclass
class Text:
    text: str
    start: float = 0.0
    end: float | None = None
    x: float = 0.5  # 0..1, centro del texto
    y: float = 0.5
    size: int = 72
    color: str = "white"
    font: str = "Sans:style=Bold"
    box: bool = False


@dataclass
class Segment:
    type: str
    duration: float
    source: Source | None = None
    panels: list[Source] = field(default_factory=list)
    color: str = "black"
    gap: int = 0
    transition: Transition = field(default_factory=Transition)
    effects: list[Effect] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    grade: str | None = None  # None => usa el grade global
    lut: str | None = None    # None => el del proyecto; "none" => sin LUT en este segmento
    lut_strength: float | None = None  # None => la del proyecto


@dataclass
class Music:
    src: str
    start: float = 0.0
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.4
    normalize: bool = True


@dataclass
class Output:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 18
    preset: str = "medium"


@dataclass
class Project:
    output: Output
    segments: list[Segment]
    music: Music | None = None
    grade: str = "none"
    lut: str | None = None             # preset, "style:<id>" o ruta a un .cube
    lut_strength: float = 1.0          # 0..1: mezcla identidad -> LUT
    color_match: dict[str, list[float]] | None = None  # cuantiles RGB objetivo (look aprendido)
    color_strength: float = 0.8
    beats: list[float] | None = None  # beats de la música (para revisar los cortes)
    drop: float | None = None          # segundo del drop en el edit
    sound_design: bool = False         # efectos de sonido automáticos (whoosh, impacto, subida…)
    sfx: list[dict] | None = None      # efectos de sonido manuales [{type, at, duration?, volume?}]
    sfx_volume: float = 1.0
    motion_blur: bool = True           # desenfoque de movimiento en transiciones con movimiento
    base_dir: str = "."

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def segment_starts(self) -> list[float]:
        t, out = 0.0, []
        for s in self.segments:
            out.append(t)
            t += s.duration
        return out


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _num(d: dict, key: str, default: float, *, minimum: float | None = None, where: str = "") -> float:
    v = d.get(key, default)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ProjectError(f"{where}: '{key}' debe ser un número (recibido {v!r})")
    if minimum is not None and v < minimum:
        raise ProjectError(f"{where}: '{key}' debe ser >= {minimum} (recibido {v})")
    return v


def _resolve(base: str, path: str) -> str:
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(base, path))


def _source(d: dict, base: str, where: str) -> Source:
    if "src" not in d:
        raise ProjectError(f"{where}: falta 'src'")
    focus = d.get("focus", [0.5, 0.5])
    if not (isinstance(focus, (list, tuple)) and len(focus) == 2):
        raise ProjectError(f"{where}: 'focus' debe ser [x, y] entre 0 y 1")
    return Source(
        src=_resolve(base, d["src"]),
        inpoint=_num(d, "in", 0.0, minimum=0, where=where),
        speed=_num(d, "speed", 1.0, minimum=0.05, where=where),
        zoom=_num(d, "zoom", 1.0, minimum=1.0, where=where),
        focus=(float(focus[0]), float(focus[1])),
        appear=_num(d, "appear", 0.0, minimum=0, where=where),
        mirror=bool(d.get("mirror", False)),
        reverse=bool(d.get("reverse", False)),
        ramp=_ramp(d.get("ramp"), where),
        ramp_ease=_num(d, "ramp_ease", 0.25, minimum=0, where=where),
    )


def _ramp(v: Any, where: str) -> tuple[tuple[float, float], ...]:
    if not v:
        return ()
    pts = []
    for i, p in enumerate(v):
        if isinstance(p, dict):
            p = (p.get("at", 0), p.get("speed", 1))
        try:
            t, sp = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise ProjectError(f"{where}.ramp[{i}]: usa [segundo, velocidad] o {{\"at\": s, \"speed\": v}}")
        if sp < 0.05:
            raise ProjectError(f"{where}.ramp[{i}]: la velocidad mínima es 0.05")
        pts.append((t, sp))
    return tuple(sorted(pts))


def _transition(v: Any, where: str) -> Transition:
    if v is None:
        return Transition()
    if isinstance(v, str):
        v = {"type": v}
    t = v.get("type", "cut")
    if t not in TRANSITIONS:
        raise ProjectError(f"{where}: transición '{t}' desconocida. Opciones: {', '.join(TRANSITIONS)}")
    dur = 0.0 if t == "cut" else _num(v, "duration", 0.3, minimum=0.01, where=where)
    direction = str(v.get("direction", "top"))
    if direction not in DROP_DIRECTIONS:
        raise ProjectError(f"{where}: 'direction' debe ser una de {', '.join(DROP_DIRECTIONS)}")
    return Transition(type=t, duration=dur, direction=direction)


def _effects(lst: Any, where: str) -> list[Effect]:
    from .effects import EFFECTS  # import diferido para evitar ciclos

    out = []
    for i, e in enumerate(lst or []):
        if isinstance(e, str):
            e = {"type": e}
        t = e.get("type")
        if t not in EFFECTS:
            raise ProjectError(f"{where}.effects[{i}]: efecto '{t}' desconocido. Opciones: {', '.join(EFFECTS)}")
        out.append(Effect(type=t, params={k: v for k, v in e.items() if k != "type"}))
    return out


def _texts(lst: Any, where: str) -> list[Text]:
    out = []
    for i, t in enumerate(lst or []):
        if isinstance(t, str):
            t = {"text": t}
        w = f"{where}.texts[{i}]"
        if "text" not in t:
            raise ProjectError(f"{w}: falta 'text'")
        out.append(Text(
            text=str(t["text"]),
            start=_num(t, "start", 0.0, minimum=0, where=w),
            end=None if t.get("end") is None else _num(t, "end", 0, where=w),
            x=_num(t, "x", 0.5, where=w),
            y=_num(t, "y", 0.5, where=w),
            size=int(_num(t, "size", 72, minimum=1, where=w)),
            color=str(t.get("color", "white")),
            font=str(t.get("font", "Sans:style=Bold")),
            box=bool(t.get("box", False)),
        ))
    return out


def _sfx_list(v: Any) -> list[dict] | None:
    from .sfx import TYPES

    if not v:
        return None
    out = []
    for i, c in enumerate(v):
        if c.get("type") not in TYPES:
            raise ProjectError(f"sfx[{i}]: tipo '{c.get('type')}' desconocido. Opciones: {', '.join(TYPES)}")
        out.append({"type": c["type"], "at": _num(c, "at", 0, minimum=0, where=f"sfx[{i}]"),
                    "volume": _num(c, "volume", 0.6, minimum=0, where=f"sfx[{i}]"),
                    **({"duration": _num(c, "duration", 0.5, minimum=0.1, where=f"sfx[{i}]")} if "duration" in c else {})})
    return out


def _lut_spec(v: Any, base_dir: str, where: str, explicit_none: bool = False) -> str | None:
    """Valida un LUT (preset, 'style:id' o ruta). None/"none" -> sin LUT ("none" si es explícito en un segmento)."""
    from . import luts

    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "none", "ninguno")):
        return "none" if explicit_none else None
    if not isinstance(v, str):
        raise ProjectError(f"{where}: 'lut' debe ser un texto (id de preset, 'style:id' o ruta a un .cube)")
    try:
        kind, value = luts.classify(v, base_dir)
        luts.validate_spec(v, base_dir)
    except luts.LutError as e:
        raise ProjectError(f"{where}: {e}")
    return value if kind == "file" else v.strip()


def _lut_strength(v: Any, where: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ProjectError(f"{where}: 'lut_strength' debe ser un número entre 0 y 1 (recibido {v!r})")
    if not 0.0 <= x <= 1.0:
        raise ProjectError(f"{where}: 'lut_strength' debe estar entre 0 y 1 (recibido {x})")
    return x


def parse_project(data: dict, base_dir: str = ".") -> Project:
    from .effects import GRADES

    o = data.get("output", {})
    output = Output(
        width=int(o.get("width", 1080)),
        height=int(o.get("height", 1920)),
        fps=int(o.get("fps", 30)),
        crf=int(o.get("crf", 18)),
        preset=str(o.get("preset", "medium")),
    )
    if output.width % 2 or output.height % 2:
        raise ProjectError("output.width y output.height deben ser pares")

    grade = data.get("grade", "none")
    if grade not in GRADES:
        raise ProjectError(f"grade '{grade}' desconocido. Opciones: {', '.join(GRADES)}")

    music = None
    if data.get("music"):
        m = data["music"]
        if isinstance(m, str):
            m = {"src": m}
        music = Music(
            src=_resolve(base_dir, m["src"]),
            start=_num(m, "start", 0.0, minimum=0, where="music"),
            volume=_num(m, "volume", 1.0, minimum=0, where="music"),
            fade_in=_num(m, "fade_in", 0.0, minimum=0, where="music"),
            fade_out=_num(m, "fade_out", 0.4, minimum=0, where="music"),
            normalize=bool(m.get("normalize", True)),
        )

    segs_raw = data.get("segments")
    if not segs_raw:
        raise ProjectError("El proyecto no tiene 'segments'")

    segments: list[Segment] = []
    for i, s in enumerate(segs_raw):
        where = f"segments[{i}]"
        t = s.get("type", "clip")
        if t not in SEGMENT_TYPES:
            raise ProjectError(f"{where}: tipo '{t}' desconocido. Opciones: {', '.join(SEGMENT_TYPES)}")
        seg = Segment(
            type=t,
            duration=_num(s, "duration", 0, minimum=0.01, where=where) if "duration" in s else -1,
            transition=_transition(s.get("transition"), where),
            effects=_effects(s.get("effects"), where),
            texts=_texts(s.get("texts"), where),
            color=str(s.get("color", "black")),
            gap=int(s.get("gap", 0)),
            grade=s.get("grade"),
        )
        if "lut" in s:  # ausente = hereda; null / "none" = sin LUT
            seg.lut = _lut_spec(s["lut"], base_dir, where, explicit_none=True)
        if s.get("lut_strength") is not None:
            seg.lut_strength = _lut_strength(s["lut_strength"], where)
        if seg.grade is not None and seg.grade not in GRADES:
            raise ProjectError(f"{where}: grade '{seg.grade}' desconocido")
        if t == "clip":
            seg.source = _source(s, base_dir, where)
        elif t == "stack":
            panels = s.get("panels") or []
            if len(panels) < 2:
                raise ProjectError(f"{where}: un 'stack' necesita al menos 2 paneles")
            seg.panels = [_source(p, base_dir, f"{where}.panels[{j}]") for j, p in enumerate(panels)]
        if seg.duration < 0:
            if t == "clip":
                from .ff import duration as fdur
                seg.duration = max(0.05, (fdur(seg.source.src) - seg.source.inpoint) / seg.source.speed)
            else:
                raise ProjectError(f"{where}: falta 'duration'")
        if i == 0 and seg.transition.type != "cut":
            seg.transition = Transition()  # el primer segmento no tiene transición de entrada
        segments.append(seg)

    for i in range(1, len(segments)):
        tr = segments[i].transition
        if tr.duration >= segments[i].duration:
            raise ProjectError(f"segments[{i}]: la transición ({tr.duration}s) debe ser más corta que el segmento")

    cm = data.get("color_match")
    if cm is not None:
        if not (isinstance(cm, dict) and all(isinstance(cm.get(c), list) and len(cm[c]) >= 3 for c in "rgb")):
            raise ProjectError("color_match debe tener listas 'r', 'g' y 'b' de cuantiles")
    lut = _lut_spec(data.get("lut"), base_dir, "lut")
    lut_strength = _lut_strength(data["lut_strength"], "lut_strength") if data.get("lut_strength") is not None else 1.0
    return Project(
        output=output,
        segments=segments,
        music=music,
        grade=grade,
        lut=lut,
        lut_strength=lut_strength,
        color_match=cm,
        beats=(data.get("info") or {}).get("beats"),
        drop=(data.get("info") or {}).get("drop"),
        sound_design=bool(data.get("sound_design", False)),
        sfx=_sfx_list(data.get("sfx")),
        sfx_volume=_num(data, "sfx_volume", 1.0, minimum=0, where="sfx_volume"),
        motion_blur=bool(data.get("motion_blur", True)),
        color_strength=_num(data, "color_strength", 0.8, minimum=0, where="color_strength"),
        base_dir=base_dir,
    )


def load_project(path: str) -> Project:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return parse_project(data, base_dir=os.path.dirname(os.path.abspath(path)))


def check_sources(project: Project) -> list[str]:
    """Devuelve la lista de archivos fuente que no existen."""
    missing = []
    paths = [project.music.src] if project.music else []
    for s in project.segments:
        if s.source:
            paths.append(s.source.src)
        paths.extend(p.src for p in s.panels)
    from . import luts

    for spec in [project.lut] + [s.lut for s in project.segments]:
        if spec and spec != "none":
            try:
                f = luts.referenced_file(spec, project.base_dir)
            except luts.LutError:
                f = None
            if f:
                paths.append(f)
    for p in paths:
        if not os.path.exists(p) and p not in missing:
            missing.append(p)
    return missing
