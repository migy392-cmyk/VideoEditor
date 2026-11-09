"""Servidor local de la interfaz web (sólo librería estándar).

    python -m videoeditor serve [--port 8765] [-w workspace]

Carpeta de trabajo:
    workspace/clips        tus clips
    workspace/music        canciones
    workspace/references   videos de referencia para aprender estilos
    workspace/luts         tus LUTs de color (.cube)
    workspace/styles       estilos aprendidos (JSON)
    workspace/projects     proyectos (JSON)
    workspace/renders      videos renderizados
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import ff, luts

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
KINDS = ("clips", "music", "references", "luts")
MEDIA_KINDS = KINDS + ("renders",)
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"} | VIDEO_EXT


def safe_name(name: str) -> str:
    name = os.path.basename(unquote(name)).strip()
    name = re.sub(r"[^\w.\- ()áéíóúñÁÉÍÓÚÑ]+", "_", name)
    if not name or name.startswith("."):
        raise ValueError("Nombre de archivo no válido")
    return name


class Jobs:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, fn, *args) -> str:
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "kind": kind, "status": "running", "progress": 0.0, "log": [],
               "result": None, "error": None, "started": time.time()}
        with self._lock:
            self._jobs[jid] = job

        def log(msg: str) -> None:
            msg = str(msg)
            job["log"].append(msg)
            m = re.search(r"\[(\d+)/(\d+)\]", msg)
            if m:
                job["progress"] = 0.9 * (int(m.group(1)) - 1) / int(m.group(2))
            elif "Uniendo" in msg:
                job["progress"] = 0.92

        def run() -> None:
            try:
                job["result"] = fn(log, *args)
                job["status"] = "done"
                job["progress"] = 1.0
            except Exception as e:  # noqa: BLE001 - se informa al usuario
                job["status"] = "error"
                job["error"] = str(e)
                job["log"].append(traceback.format_exc(limit=3))

        threading.Thread(target=run, daemon=True).start()
        return jid

    def get(self, jid: str) -> dict | None:
        return self._jobs.get(jid)


class App:
    def __init__(self, workspace: str) -> None:
        self.ws = os.path.abspath(workspace)
        for d in KINDS + ("styles", "projects", "renders", "cache"):
            os.makedirs(os.path.join(self.ws, d), exist_ok=True)
        self.jobs = Jobs()
        luts.set_workspace(self.ws)

    def path(self, kind: str, name: str = "") -> str:
        if kind not in MEDIA_KINDS + ("styles", "projects", "cache"):
            raise ValueError("Tipo no válido")
        return os.path.join(self.ws, kind, safe_name(name)) if name else os.path.join(self.ws, kind)

    # ---------------- biblioteca ----------------
    def library(self) -> dict:
        out = {}
        for kind in MEDIA_KINDS:
            items = []
            d = self.path(kind)
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if fn.startswith(".") or not os.path.isfile(p) or fn.endswith(".part"):
                    continue
                item = {"name": fn, "size": os.path.getsize(p)}
                if kind == "luts":
                    items.append(item)
                    continue
                try:
                    item["duration"] = round(ff.duration(p), 2)
                    if os.path.splitext(fn)[1].lower() in VIDEO_EXT:
                        item["width"], item["height"] = ff.video_size(p)
                except (ff.FFmpegError, KeyError, ValueError):
                    item["error"] = "no se pudo leer"
                items.append(item)
            out[kind] = items
        return out

    def thumb(self, kind: str, name: str, t: float) -> str:
        src = self.path(kind, name)
        key = re.sub(r"[^\w]", "_", f"{kind}_{name}_{t:.2f}") + ".jpg"
        out = os.path.join(self.ws, "cache", key)
        if not os.path.exists(out):
            ff.run(["-ss", f"{max(0.0, t):.2f}", "-i", src, "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "4", out])
        return out

    # ---------------- LUTs ----------------
    def luts(self) -> list[dict]:
        return luts.list_luts(self.ws)

    def lut_thumb(self, clip: str, spec: str, strength: float, t: float) -> str:
        """Fotograma del clip con el LUT aplicado (miniatura en caché). spec "none" = sin LUT."""
        src = self.path("clips", clip)
        spec = (spec or "none").strip()
        strength = min(1.0, max(0.0, strength))
        if spec != "none":
            kind, value = luts.classify(spec, os.path.join(self.ws, "projects"))
            if kind == "file":
                spec = value
        sig = f"{clip}|{spec}|{strength:.2f}|{t:.2f}|{os.path.getmtime(src)}"
        if spec.startswith("style:") or os.path.isfile(spec):
            sig += f"|{self._lut_mtime(spec)}"
        key = "lut_" + hashlib.sha1(sig.encode()).hexdigest()[:16] + ".jpg"
        out = os.path.join(self.ws, "cache", key)
        if not os.path.exists(out):
            tmp = out + ".part.jpg"
            luts.preview_frame(src, t, tmp, None if spec == "none" else spec, strength, width=180,
                               base_dir=os.path.join(self.ws, "projects"))
            os.replace(tmp, out)
        return out

    def _lut_mtime(self, spec: str) -> float:
        if spec.startswith("style:"):
            p = os.path.join(self.ws, "styles", spec[6:] + ".json")
            return os.path.getmtime(p) if os.path.exists(p) else 0.0
        return os.path.getmtime(spec)

    def save_lut(self, dest: str) -> None:
        """Comprueba que el archivo subido es un LUT 3D válido; si no, lo borra."""
        try:
            luts.read_cube(dest)
        except luts.LutError:
            os.remove(dest)
            raise

    # ---------------- estilos ----------------
    def styles(self) -> list[dict]:
        from .style import list_styles

        out = []
        for s in list_styles(self.path("styles")):
            s = dict(s)
            s.pop("path", None)
            out.append(s)
        return out

    def learn(self, log, name: str, refs: list[str]) -> dict:
        from .style import learn

        paths = [self.path("references", r) for r in refs]
        style = learn(paths, name, self.path("styles"), log=log)
        style.pop("path", None)
        return style

    # ---------------- proyectos ----------------
    def project_path(self, name: str) -> str:
        name = safe_name(name)
        if not name.endswith(".json"):
            name += ".json"
        return os.path.join(self.ws, "projects", name)

    def projects(self) -> list[dict]:
        out = []
        d = self.path("projects")
        for fn in sorted(os.listdir(d), key=lambda f: -os.path.getmtime(os.path.join(d, f))):
            if fn.endswith(".json"):
                out.append({"name": fn[:-5], "modified": os.path.getmtime(os.path.join(d, fn))})
        return out

    def create(self, log, req: dict) -> dict:
        from .auto import AutoOptions, build_project
        from .style import apply_style, find_style

        clips = [self.path("clips", c) for c in req.get("clips", [])]
        music = self.path("music", req["music"])
        opt = AutoOptions(max_duration=req.get("duration") or None,
                          music_start=float(req["music_start"]) if req.get("music_start") not in (None, "") else None,
                          grade=req.get("grade") or None,
                          smart_inpoints=not req.get("no_smart", False))
        pdir = self.path("projects")
        lut, lut_strength = req.get("lut") or None, float(req.get("lut_strength", 1.0))
        if req.get("style"):
            log("Aplicando estilo…")
            style = find_style(req["style"], self.path("styles"))
            log("Analizando la canción (secciones, drop, energía, golpes)…")
            data, _ = apply_style(style, clips, music, opt, pdir, keep_order=bool(req.get("keep_order")),
                                  color_strength=float(req.get("color_strength", 0.8)),
                                  follow_music=req.get("follow_music", True) is not False,
                                  accents=req.get("accents", True) is not False,
                                  sound_design=req.get("sound_design", True) is not False,
                                  cinematic=bool(req.get("cinematic")), lut=lut, lut_strength=lut_strength)
            mi = data.get("info", {})
            log(f"Canción desde {data['music']['start']:.2f}s"
                + (f" · drop en el segundo {mi['drop']:.2f} del edit" if mi.get("drop") is not None else ""))
        else:
            log("Aplicando plantilla…")
            data, _ = build_project(clips, music, req.get("template") or "gym_stack", opt, pdir)
            if lut:
                luts.apply_to_project_data(data, lut, lut_strength, project_dir=pdir)
        name = safe_name(req.get("name") or f"edit_{time.strftime('%Y%m%d_%H%M%S')}")
        with open(self.project_path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log("Proyecto creado")
        return {"project": name.removesuffix(".json")}

    def save_project(self, name: str, data: dict) -> None:
        from .project import parse_project

        parse_project(data, self.path("projects"))  # valida
        with open(self.project_path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def render(self, log, name: str, preview: bool) -> dict:
        from .project import check_sources, load_project
        from .render import preview_project, render

        project = load_project(self.project_path(name))
        missing = check_sources(project)
        if missing:
            raise ValueError("Faltan archivos: " + ", ".join(os.path.basename(m) for m in missing))
        if preview:
            project = preview_project(project)
        out_name = f"{safe_name(name)}{'_preview' if preview else ''}.mp4"
        report: dict = {}
        render(project, os.path.join(self.ws, "renders", out_name), log=log, report=report)
        return {"output": out_name, "duration": round(project.total_duration, 2), "qa": report}

    def music_map(self, music: str) -> dict:
        from .music import cached_music_map

        return cached_music_map(self.path("music", music), os.path.join(self.ws, "cache")).to_dict()

    def beats(self, music: str, start: float, duration: float | None) -> dict:
        from .beats import detect

        cache = os.path.join(self.ws, "cache", re.sub(r"[^\w]", "_", f"beats_{music}_{start}_{duration}") + ".json")
        if os.path.exists(cache):
            with open(cache) as f:
                return json.load(f)
        info = detect(self.path("music", music), start=start, duration=duration)
        out = {"bpm": info.bpm, "period": info.period, "beats": info.beats}
        with open(cache, "w") as f:
            json.dump(out, f)
        return out


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VideoEditor/1"

        def log_message(self, fmt, *args):  # silencio salvo errores
            if args and str(args[1]).startswith(("4", "5")):
                super().log_message(fmt, *args)

        # ---------- utilidades ----------
        def send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def error(self, msg, status=400):
            self.send_json({"error": str(msg)}, status)

        def read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def send_file(self, path: str, ctype: str | None = None):
            if not os.path.isfile(path):
                return self.error("No encontrado", 404)
            size = os.path.getsize(path)
            ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)", rng)
                if m:
                    if m.group(1):
                        start = int(m.group(1))
                        if m.group(2):
                            end = min(int(m.group(2)), size - 1)
                    elif m.group(2):
                        start = max(0, size - int(m.group(2)))
                if start > end or start >= size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                try:
                    while left > 0:
                        chunk = f.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        # ---------- rutas ----------
        def do_GET(self):
            u = urlparse(self.path)
            parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if not parts:
                    return self.send_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
                if parts[0] == "static" and len(parts) == 2:
                    return self.send_file(os.path.join(WEB_DIR, safe_name(parts[1])))
                if parts[0] == "media" and len(parts) == 3 and parts[1] in MEDIA_KINDS:
                    return self.send_file(app.path(parts[1], parts[2]))
                if parts[0] != "api":
                    return self.error("No encontrado", 404)
                api = parts[1:]
                if api == ["meta"]:
                    from . import segment
                    from .auto import TEMPLATES
                    from .effects import EFFECTS, GRADES, INTERNAL
                    from .project import DROP_DIRECTIONS, LAYOUTS, TRANSITIONS
                    return self.send_json({"effects": [e for e in EFFECTS if e not in INTERNAL],
                                           "transitions": list(TRANSITIONS), "layouts": list(LAYOUTS),
                                           "grades": list(GRADES), "templates": list(TEMPLATES),
                                           "directions": list(DROP_DIRECTIONS),
                                           "segmentation": segment.available(), "workspace": app.ws})
                if api == ["library"]:
                    return self.send_json(app.library())
                if len(api) == 3 and api[0] == "thumb":
                    return self.send_file(app.thumb(api[1], api[2], float(q.get("t", 0.5))), "image/jpeg")
                if api == ["luts"]:
                    return self.send_json(app.luts())
                if len(api) == 2 and api[0] == "lut-thumb":
                    return self.send_file(app.lut_thumb(api[1], q.get("lut", "none"), float(q.get("strength", 1.0)),
                                                        float(q.get("t", 1.0))), "image/jpeg")
                if api == ["styles"]:
                    return self.send_json(app.styles())
                if api == ["projects"]:
                    return self.send_json(app.projects())
                if len(api) == 2 and api[0] == "projects":
                    p = app.project_path(api[1])
                    if not os.path.exists(p):
                        return self.error("Proyecto no encontrado", 404)
                    with open(p, encoding="utf-8") as f:
                        return self.send_json(json.load(f))
                if len(api) == 2 and api[0] == "jobs":
                    job = app.jobs.get(api[1])
                    return self.send_json(job) if job else self.error("Trabajo no encontrado", 404)
                if len(api) == 2 and api[0] == "music":
                    return self.send_json(app.music_map(api[1]))
                if len(api) == 2 and api[0] == "beats":
                    dur = float(q["duration"]) if q.get("duration") else None
                    return self.send_json(app.beats(api[1], float(q.get("start", 0)), dur))
                return self.error("No encontrado", 404)
            except (ValueError, ff.FFmpegError, OSError) as e:
                return self.error(e)

        def do_PUT(self):
            u = urlparse(self.path)
            parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
            try:
                if len(parts) == 4 and parts[:2] == ["api", "upload"] and parts[2] in KINDS:
                    name = parts[3]
                    if parts[2] == "luts" and not name.lower().endswith(luts.LUT_EXT):
                        name += ".cube"
                    dest = app.path(parts[2], name)
                    n = int(self.headers.get("Content-Length") or 0)
                    tmp = dest + ".part"
                    with open(tmp, "wb") as f:
                        left = n
                        while left > 0:
                            chunk = self.rfile.read(min(1 << 20, left))
                            if not chunk:
                                break
                            f.write(chunk)
                            left -= len(chunk)
                    if left:
                        os.remove(tmp)
                        return self.error("Subida incompleta")
                    os.replace(tmp, dest)
                    if parts[2] == "luts":
                        app.save_lut(dest)
                    ff.probe.cache_clear()
                    return self.send_json({"ok": True, "name": os.path.basename(dest)})
                if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                    app.save_project(parts[2], self.read_json())
                    return self.send_json({"ok": True})
                return self.error("No encontrado", 404)
            except (ValueError, OSError) as e:  # LutError es un ValueError
                return self.error(e)

        def do_POST(self):
            u = urlparse(self.path)
            parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
            try:
                body = self.read_json()
                if parts == ["api", "learn"]:
                    if not body.get("name") or not body.get("references"):
                        return self.error("Indica un nombre y al menos un video de referencia")
                    return self.send_json({"job": app.jobs.start("learn", app.learn, body["name"], body["references"])})
                if parts == ["api", "create"]:
                    if not body.get("clips") or not body.get("music"):
                        return self.error("Elige clips y una canción")
                    return self.send_json({"job": app.jobs.start("create", app.create, body)})
                if parts == ["api", "render"]:
                    return self.send_json({"job": app.jobs.start("render", app.render, body["project"],
                                                                 bool(body.get("preview")))})
                return self.error("No encontrado", 404)
            except (ValueError, KeyError, OSError) as e:
                return self.error(e)

        def do_DELETE(self):
            u = urlparse(self.path)
            parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
            try:
                if len(parts) == 4 and parts[:2] == ["api", "library"] and parts[2] in MEDIA_KINDS:
                    os.remove(app.path(parts[2], parts[3]))
                    return self.send_json({"ok": True})
                if len(parts) == 3 and parts[:2] == ["api", "styles"]:
                    p = app.path("styles", parts[2] + ".json")
                    if not os.path.exists(p):
                        return self.error("Sólo se pueden borrar estilos propios", 404)
                    os.remove(p)
                    return self.send_json({"ok": True})
                if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                    os.remove(app.project_path(parts[2]))
                    return self.send_json({"ok": True})
                return self.error("No encontrado", 404)
            except (ValueError, OSError) as e:
                return self.error(e)

    return Handler


def serve(workspace: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    ff.require_ffmpeg()
    app = App(workspace)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"VideoEditor en http://{host}:{port}  (carpeta de trabajo: {app.ws})")
    print("Ctrl+C para salir")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

