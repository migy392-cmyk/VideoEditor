"""Línea de comandos.

    python -m videoeditor render proyecto.json -o salida.mp4 [--preview]
    python -m videoeditor auto --music cancion.mp3 clip1.mov clip2.mov ... -o edit.mp4
    python -m videoeditor learn referencia1.mov referencia2.mov --name gym
    python -m videoeditor auto --style gym --music cancion.mp3 clips... -o edit.mp4
    python -m videoeditor styles
    python -m videoeditor analyze referencia.mov [--template plantilla.json]
    python -m videoeditor serve      (interfaz web)
    python -m videoeditor beats cancion.mp3
    python -m videoeditor list
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, ff

DEFAULT_WORKSPACE = os.environ.get("VIDEOEDITOR_WORKSPACE", "workspace")


def styles_dir(args: argparse.Namespace) -> str:
    return os.path.join(getattr(args, "workspace", None) or DEFAULT_WORKSPACE, "styles")


def cmd_render(args: argparse.Namespace) -> int:
    from .project import check_sources, load_project
    from .render import preview_project, render

    project = load_project(args.project)
    missing = check_sources(project)
    if missing:
        print("Faltan archivos:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2
    if args.preview:
        project = preview_project(project)
    out = args.output or os.path.splitext(args.project)[0] + (".preview.mp4" if args.preview else ".mp4")
    print(f"Renderizando {len(project.segments)} segmentos · {project.total_duration:.2f}s · "
          f"{project.output.width}x{project.output.height}@{project.output.fps}")
    render(project, out, verbose=args.verbose, keep_temp=args.keep_temp)
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    from .auto import AutoOptions, build_project
    from .project import parse_project
    from .render import preview_project, render

    out = args.output or "edit.mp4"
    project_path = args.save or os.path.splitext(out)[0] + ".json"
    project_dir = os.path.dirname(os.path.abspath(project_path))
    opt = AutoOptions(width=args.width, height=args.height, fps=args.fps, grade=args.grade,
                      max_duration=args.duration, music_start=args.music_start,
                      smart_inpoints=not args.no_smart)
    if args.style:
        from .style import apply_style, find_style

        style = find_style(args.style, styles_dir(args))
        print(f"Aplicando el estilo '{style['name']}' ({len(style['variants'])} variante(s))…")
        data, info = apply_style(style, args.clips, args.music, opt, project_dir,
                                 variant=args.variant, keep_order=args.keep_order,
                                 color_strength=args.color_strength)
    else:
        print(f"Analizando música y clips (plantilla '{args.template}')…")
        data, info = build_project(args.clips, args.music, args.template, opt, project_dir)
    print(f"  Tempo: {info.bpm:.1f} BPM · {len(data['segments'])} segmentos")
    with open(project_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Proyecto guardado en {project_path} (edítalo y vuelve a renderizar con 'render')")
    if args.no_render:
        return 0
    project = parse_project(data, base_dir=project_dir)
    if args.preview:
        project = preview_project(project)
    render(project, out, verbose=args.verbose)
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    from .style import learn

    style = learn(args.videos, args.name, styles_dir(args))
    st = style["stats"]
    print(f"Estilo '{style['name']}' guardado en {style['path']}")
    print(f"  Variantes: {len(style['variants'])} · plano medio: {st['avg_clip_beats']} beats")
    print(f"  Transiciones: {st['transitions']}  ·  Efectos: {st['effects'] or '-'}")
    return 0


def cmd_styles(args: argparse.Namespace) -> int:
    from .style import list_styles

    styles = list_styles(styles_dir(args))
    if not styles:
        print("No hay estilos. Crea uno con: python -m videoeditor learn video.mov --name mi_estilo")
    for s in styles:
        st = s.get("stats", {})
        tag = " (incluido)" if s.get("builtin") else ""
        print(f"- {s['name']}{tag}: {len(s.get('variants', []))} variante(s), "
              f"aprendido de {', '.join(s.get('sources', []))}; transiciones {st.get('transitions')}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(args.workspace or DEFAULT_WORKSPACE, host=args.host, port=args.port)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analyze import analyze, report, to_template

    a = analyze(args.video)
    print(report(a))
    if args.template:
        with open(args.template, "w", encoding="utf-8") as f:
            json.dump(to_template(a), f, indent=2, ensure_ascii=False)
        print(f"\nPlantilla con los tiempos guardada en {args.template}")
    return 0


def cmd_beats(args: argparse.Namespace) -> int:
    from .beats import detect

    info = detect(args.audio, start=args.start, duration=args.duration)
    print(f"Tempo: {info.bpm:.1f} BPM  (1 beat = {info.period:.3f}s)")
    print("Beats: " + ", ".join(f"{b:.2f}" for b in info.beats[: args.limit]))
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    from .auto import TEMPLATES
    from .effects import EFFECTS, GRADES
    from .project import TRANSITIONS

    print("Plantillas automáticas: " + ", ".join(TEMPLATES))
    print("Looks de color (grade): " + ", ".join(GRADES))
    print("Efectos: " + ", ".join(EFFECTS))
    print("Transiciones: " + ", ".join(TRANSITIONS))
    print("Tipos de segmento: clip, stack, black, color")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="videoeditor", description="Editor de video por proyectos JSON (FFmpeg)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--workspace", "-w", help=f"Carpeta de trabajo (por defecto '{DEFAULT_WORKSPACE}')")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="Renderiza un proyecto JSON")
    r.add_argument("project")
    r.add_argument("-o", "--output")
    r.add_argument("--preview", action="store_true", help="Render rápido a media resolución")
    r.add_argument("--keep-temp", action="store_true")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=cmd_render)

    a = sub.add_parser("auto", help="Crea un edit automático sincronizado a la música")
    a.add_argument("clips", nargs="+")
    a.add_argument("--music", "-m", required=True)
    a.add_argument("--style", "-s", help="Estilo aprendido (nombre o ruta .json)")
    a.add_argument("--template", "-t", default="gym_stack", help="Plantilla fija si no usas --style")
    a.add_argument("--variant", type=int, help="Variante del estilo (por defecto, la que mejor encaja)")
    a.add_argument("--keep-order", action="store_true", help="Respetar el orden de los clips")
    a.add_argument("--color-strength", type=float, default=0.8, help="Cuánto copiar el color aprendido (0-1)")
    a.add_argument("-o", "--output")
    a.add_argument("--save", help="Ruta del proyecto JSON generado")
    a.add_argument("--duration", type=float, help="Duración máxima en segundos")
    a.add_argument("--music-start", type=float, default=0.0, help="Segundo de la canción donde empezar")
    a.add_argument("--grade", help="Look de color adicional (ver 'list')")
    a.add_argument("--width", type=int)
    a.add_argument("--height", type=int)
    a.add_argument("--fps", type=int, default=30)
    a.add_argument("--no-smart", action="store_true", help="No buscar el tramo con más movimiento")
    a.add_argument("--no-render", action="store_true", help="Sólo generar el JSON")
    a.add_argument("--preview", action="store_true")
    a.add_argument("-v", "--verbose", action="store_true")
    a.set_defaults(fn=cmd_auto)

    le = sub.add_parser("learn", help="Aprende un estilo de edición de uno o varios videos")
    le.add_argument("videos", nargs="+")
    le.add_argument("--name", "-n", required=True)
    le.set_defaults(fn=cmd_learn)

    sub.add_parser("styles", help="Lista los estilos aprendidos").set_defaults(fn=cmd_styles)

    sv = sub.add_parser("serve", help="Abre la interfaz web")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(fn=cmd_serve)

    z = sub.add_parser("analyze", help="Analiza un video de referencia (cortes, paneles, beats, glitch)")
    z.add_argument("video")
    z.add_argument("--template", help="Guarda un proyecto esqueleto con los mismos tiempos")
    z.set_defaults(fn=cmd_analyze)

    b = sub.add_parser("beats", help="Detecta tempo y beats de una canción")
    b.add_argument("audio")
    b.add_argument("--start", type=float, default=0.0)
    b.add_argument("--duration", type=float)
    b.add_argument("--limit", type=int, default=32)
    b.set_defaults(fn=cmd_beats)

    sub.add_parser("list", help="Lista plantillas, looks, efectos y transiciones").set_defaults(fn=cmd_list)

    args = p.parse_args(argv)
    try:
        ff.require_ffmpeg()
        return args.fn(args)
    except (ff.FFmpegError, ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
