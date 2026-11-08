"""Tests de extremo a extremo con clips sintéticos (requieren ffmpeg)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error

from videoeditor import ff
from videoeditor.analyze import analyze, to_template
from videoeditor.auto import AutoOptions, build_project
from videoeditor.beats import detect
from videoeditor.project import ProjectError, parse_project
from videoeditor.render import render
from videoeditor import segment
from videoeditor.style import apply_style, learn, list_styles

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _make_clip(path, src, dur=3.0, size="320x568"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=size={size}:rate=30" + ("" if src == "cellauto" else f":duration={dur}"), "-t", str(dur),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)


def _make_music(path, bpm=120, dur=12.0):
    # Clic de bombo en cada beat sobre un tono de fondo.
    period = 60.0 / bpm
    expr = f"0.9*sin(2*PI*60*t)*exp(-30*mod(t\\,{period}))+0.05*sin(2*PI*440*t)"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='{expr}':s=44100:d={dur}", "-c:a", "aac", path], check=True)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="ve_test_")
        cls.clips = []
        for i, src in enumerate(["testsrc2", "smptebars", "cellauto", "rgbtestsrc", "testsrc"]):
            p = os.path.join(cls.dir, f"c{i}.mp4")
            _make_clip(p, src)
            cls.clips.append(p)
        cls.music = os.path.join(cls.dir, "music.m4a")
        _make_music(cls.music)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def small(self, data):
        data["output"] = {"width": 180, "height": 320, "fps": 15, "crf": 30, "preset": "ultrafast"}
        return data

    def test_beats(self):
        info = detect(self.music)
        self.assertAlmostEqual(info.bpm, 120, delta=3)
        self.assertGreater(len(info.beats), 15)

    def test_render_all_segment_types_and_effects(self):
        c = self.clips
        data = self.small({
            "music": {"src": self.music},
            "grade": "night_blue",
            "segments": [
                {"type": "black", "duration": 0.5},
                {"type": "stack", "duration": 2.0, "gap": 4, "panels": [
                    {"src": c[0], "appear": 0}, {"src": c[1], "appear": 0.5}, {"src": c[2], "appear": 1.0}]},
                {"type": "clip", "src": c[3], "duration": 1.5, "transition": {"type": "slidedown", "duration": 0.3},
                 "effects": ["punch", {"type": "shake", "duration": 0.5, "at": 0}, {"type": "flash", "times": [0.5]}],
                 "texts": [{"text": "DÍA 1: PUSH", "y": 0.2, "size": 24}]},
                {"type": "clip", "src": c[4], "duration": 1.5, "speed": 0.5, "zoom": 1.2, "mirror": True,
                 "transition": "flash", "effects": [{"type": "glitch", "duration": 0.5}, "fade_out"]},
                {"type": "color", "color": "white", "duration": 0.6, "transition": "fade"},
            ],
        })
        project = parse_project(data, self.dir)
        out = os.path.join(self.dir, "full.mp4")
        render(project, out, log=lambda *_: None)
        self.assertAlmostEqual(ff.duration(out), project.total_duration, delta=0.15)
        self.assertTrue(ff.has_audio(out))
        self.assertEqual(ff.video_size(out), (180, 320))

    def test_auto_templates(self):
        for template in ("gym_stack", "beat_cuts"):
            data, info = build_project(self.clips, self.music, template,
                                       AutoOptions(max_duration=10), self.dir)
            project = parse_project(self.small(data), self.dir)
            self.assertLessEqual(project.total_duration, 10.5)
            out = os.path.join(self.dir, f"auto_{template}.mp4")
            render(project, out, log=lambda *_: None)
            self.assertAlmostEqual(ff.duration(out), project.total_duration, delta=0.15)

    def test_cuts_land_on_beats(self):
        data, info = build_project(self.clips, self.music, "gym_stack", AutoOptions(), self.dir)
        t, starts = 0.0, []
        for s in data["segments"]:
            starts.append(t)
            t += s["duration"]
        for s in starts[1:]:
            self.assertLess(min(abs(s - b) for b in info.beats), 0.02)

    def test_analyze_roundtrip(self):
        """Renderiza un edit con estructura conocida y comprueba que el análisis la recupera."""
        c = self.clips
        data = self.small({"segments": [
            {"type": "black", "duration": 0.5},
            {"type": "stack", "duration": 2.0, "panels": [
                {"src": c[0], "appear": 0}, {"src": c[1], "appear": 0.6}, {"src": c[2], "appear": 1.2}]},
            {"type": "clip", "src": c[3], "duration": 1.5},
            {"type": "clip", "src": c[4], "duration": 1.5, "effects": [{"type": "glitch", "duration": 0.5}]},
        ]})
        out = os.path.join(self.dir, "ref.mp4")
        render(parse_project(data, self.dir), out, log=lambda *_: None)
        a = analyze(out)
        kinds = [s.kind for s in a.shots]
        self.assertEqual(kinds[:2], ["black", "stack"])
        self.assertEqual(len(a.shots[1].panels), 3)
        self.assertIsNotNone(a.shots[-1].glitch_at)
        self.assertTrue(to_template(a)["segments"])

    def test_validation(self):
        with self.assertRaises(ProjectError):
            parse_project({"segments": [{"type": "nope", "duration": 1}]})
        with self.assertRaises(ProjectError):
            parse_project({"segments": [{"type": "black", "duration": 1, "effects": ["magic"]}]})
        with self.assertRaises(ProjectError):
            parse_project({"segments": [{"type": "stack", "duration": 1, "panels": [{"src": "a"}]}]})
        with self.assertRaises(ProjectError):
            parse_project({"segments": [{"type": "black", "duration": 1},
                                        {"type": "black", "duration": 0.2, "transition": {"type": "fade", "duration": 0.5}}]})

    def test_example_project_is_valid(self):
        here = os.path.dirname(__file__)
        path = os.path.join(here, "..", "examples", "gym_reference.json")
        with open(path, encoding="utf-8") as f:
            parse_project(json.load(f), os.path.dirname(path))

    def test_detects_transitions_and_effects(self):
        """Fundido, flash, slide, fundido a negro y flash dentro de un plano."""
        c = self.clips
        data = {"output": {"width": 180, "height": 320, "fps": 30, "crf": 20, "preset": "ultrafast"}, "segments": [
            {"type": "clip", "src": c[0], "duration": 1.5},
            {"type": "clip", "src": c[1], "duration": 1.5, "transition": {"type": "fade", "duration": 0.5}},
            {"type": "clip", "src": c[3], "duration": 1.5, "transition": {"type": "flash", "duration": 0.3}},
            {"type": "clip", "src": c[4], "duration": 1.5, "transition": {"type": "slideleft", "duration": 0.4}},
            {"type": "clip", "src": c[0], "duration": 1.5, "transition": {"type": "fadeblack", "duration": 0.6}},
            {"type": "clip", "src": c[1], "duration": 1.5, "effects": [{"type": "flash", "times": [0.7]}]},
        ]}
        out = os.path.join(self.dir, "transitions.mp4")
        render(parse_project(data, self.dir), out, log=lambda *_: None)
        a = analyze(out)
        types = [s.transition["type"] for s in a.shots[1:]]
        self.assertEqual(types, ["fade", "flash", "slideleft", "fadeblack", "cut"])
        self.assertEqual(len(a.shots[-1].flashes), 1)
        for got, want in zip([s.start for s in a.shots[1:]], [1.5, 3.0, 4.5, 6.0, 7.5]):
            self.assertAlmostEqual(got, want, delta=0.2)

    def test_learn_and_apply_style(self):
        c = self.clips
        ref = {"output": {"width": 180, "height": 320, "fps": 30, "crf": 20, "preset": "ultrafast"},
               "music": {"src": self.music}, "segments": [
                   {"type": "black", "duration": 0.5},
                   {"type": "stack", "duration": 3.0, "panels": [
                       {"src": c[0], "appear": 0}, {"src": c[1], "appear": 1.0}, {"src": c[2], "appear": 2.0}]},
                   {"type": "clip", "src": c[3], "duration": 2.0},
                   {"type": "clip", "src": c[4], "duration": 2.0, "transition": {"type": "fade", "duration": 0.5}},
                   {"type": "clip", "src": c[0], "duration": 2.0, "effects": [{"type": "glitch", "duration": 0.6}]}]}
        ref_path = os.path.join(self.dir, "ref_style.mp4")
        render(parse_project(ref, self.dir), ref_path, log=lambda *_: None)
        sdir = os.path.join(self.dir, "styles")
        style = learn([ref_path], "Prueba", sdir, log=lambda *_: None)
        secs = style["variants"][0]["sections"]
        self.assertEqual([s["type"] for s in secs], ["black", "stack", "clip", "clip", "clip"])
        self.assertEqual(secs[1]["panels"], [0.0, 2.0, 4.0])  # 1 s = 2 beats a 120 BPM
        self.assertEqual(secs[3]["transition"]["type"], "fade")
        self.assertEqual(secs[4]["effects"][0]["type"], "glitch")
        self.assertIn("r", style["color"])
        self.assertTrue(any(s["id"] == "prueba" for s in list_styles(sdir)))

        # Aplicar con más clips de los que tiene la referencia: se repite el cuerpo
        data, info = apply_style(style, self.clips + self.clips[:2], self.music, AutoOptions(), self.dir)
        types = [s["type"] for s in data["segments"]]
        self.assertEqual(types[:2], ["black", "stack"])
        self.assertGreater(types.count("clip"), 3)
        self.assertIn("color_match", data)
        project = parse_project(self.small(data), self.dir)
        out = os.path.join(self.dir, "styled.mp4")
        render(project, out, log=lambda *_: None)
        self.assertAlmostEqual(ff.duration(out), project.total_duration, delta=0.15)
        t, starts = 0.0, []
        for s in data["segments"]:
            starts.append(t)
            t += s["duration"]
        for s in starts[1:]:
            self.assertLess(min(abs(s - b) for b in info.beats), 0.02)

    @unittest.skipUnless(segment.available(), "MediaPipe no instalado")
    def test_subject_drop(self):
        c = self.clips
        data = self.small({"segments": [
            {"type": "clip", "src": c[0], "duration": 1.0},
            {"type": "clip", "src": c[1], "duration": 1.0, "transition": {"type": "subject_drop", "duration": 0.4}}]})
        out = os.path.join(self.dir, "drop.mp4")
        render(parse_project(data, self.dir), out, log=lambda *_: None)
        self.assertAlmostEqual(ff.duration(out), 2.0, delta=0.15)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
class ServerTests(unittest.TestCase):
    def test_api_flow(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer

        from videoeditor.server import App, make_handler

        ws = tempfile.mkdtemp(prefix="ve_ws_")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(ws)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}"

        def call(method, path, body=None, raw=None):
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            req = urllib.request.Request(url + path, data=data, method=method)
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        def wait(job):
            import time
            for _ in range(600):
                j = call("GET", f"/api/jobs/{job}")
                if j["status"] != "running":
                    self.assertEqual(j["status"], "done", j.get("error"))
                    return j["result"]
                time.sleep(0.2)
            self.fail("timeout")

        try:
            clip = os.path.join(ws, "src.mp4")
            _make_clip(clip, "testsrc2", dur=4, size="180x320")
            music = os.path.join(ws, "song.m4a")
            _make_music(music, dur=8)
            for i in range(4):
                with open(clip, "rb") as f:
                    call("PUT", f"/api/upload/clips/c{i}.mp4", raw=f.read())
            with open(music, "rb") as f:
                call("PUT", "/api/upload/music/song.m4a", raw=f.read())
            lib = call("GET", "/api/library")
            self.assertEqual(len(lib["clips"]), 4)
            self.assertIn("transitions", call("GET", "/api/meta"))
            res = wait(call("POST", "/api/create", {"clips": [f"c{i}.mp4" for i in range(4)], "music": "song.m4a",
                                                    "template": "beat_cuts", "duration": 4, "name": "t"})["job"])
            proj = call("GET", f"/api/projects/{res['project']}")
            proj["output"] = {"width": 180, "height": 320, "fps": 15, "crf": 30, "preset": "ultrafast"}
            call("PUT", "/api/projects/t", proj)
            out = wait(call("POST", "/api/render", {"project": "t"})["job"])
            with urllib.request.urlopen(url + f"/media/renders/{out['output']}") as r:
                self.assertGreater(len(r.read()), 1000)
            with urllib.request.urlopen(urllib.request.Request(url + "/media/renders/t.mp4", headers={"Range": "bytes=0-99"})) as r:
                self.assertEqual(r.status, 206)
                self.assertEqual(len(r.read()), 100)
            with self.assertRaises(urllib.error.HTTPError):
                call("PUT", "/api/projects/t", {"segments": [{"type": "nope", "duration": 1}]})
        finally:
            httpd.shutdown()
            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
