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
from videoeditor import qa, segment
from videoeditor.music import analyze_music
from videoeditor.render import source_length
from tests.synth import make_song
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
        t, hits = 0.0, []
        for s in data["segments"]:
            tr = s.get("transition") or {}
            moving = tr.get("type", "cut") not in ("cut", "fade", "fadeblack", "flash")
            hits.append(t + (tr.get("duration", 0) if moving else 0))
            t += s["duration"]
        for s in hits[1:]:  # el corte (o el final de la transición) cae en un beat
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

    # ---------------------------------------------------------------- música
    def song(self):
        if not hasattr(self, "_song"):
            path = os.path.join(self.dir, "song.wav")
            truth = make_song(path, offset=0.3)
            type(self)._song = (path, truth)
        return self._song

    def test_music_analysis(self):
        path, truth = self.song()
        m = analyze_music(path)
        self.assertAlmostEqual(m.bpm, 128, delta=0.5)
        drops = [s.start for s in m.sections if s.label == "drop"]
        for want in truth["starts"]["drop"]:
            self.assertLess(min(abs(want - d) for d in drops), 0.3, (drops, want))
        self.assertIsNotNone(m.drop)
        self.assertLess(min(abs(m.drop - d) for d in truth["starts"]["drop"]), 0.3)
        # los beats siguen la canción sin desfasarse (error < 40 ms en toda la canción)
        p = truth["beat"]
        err = max(abs((b - 0.3) / p - round((b - 0.3) / p)) * p for b in m.beats)
        self.assertLess(err, 0.04)

    def test_music_guided_style(self):
        from videoeditor.style import find_style

        path, truth = self.song()
        style = find_style("gym_noche", None)
        data, mm = apply_style(style, self.clips, path, AutoOptions(), self.dir)
        info = data["info"]
        self.assertIsNotNone(info["drop"])
        # el momento fuerte del estilo (la persona aterriza tras la intro) cae en el drop
        t, landing = 0.0, None
        for sg in data["segments"]:
            tr = sg.get("transition") or {}
            if sg["type"] == "clip" and landing is None:
                landing = t + (tr.get("duration", 0) if tr.get("type", "cut") != "cut" else 0)
            t += sg["duration"]
        self.assertAlmostEqual(landing, info["drop"], delta=0.08)
        self.assertTrue(data.get("sound_design"))
        self.assertTrue(any(e["type"] == "flash" for sg in data["segments"] for e in sg.get("effects", [])))
        project = parse_project(self.small(data), self.dir)
        out = os.path.join(self.dir, "guided.mp4")
        rep = {}
        render(project, out, log=lambda *_: None, report=rep)
        self.assertAlmostEqual(ff.duration(out), project.total_duration, delta=0.15)
        self.assertNotIn("satura", " ".join(rep["check"]["issues"]))
        self.assertEqual(rep["check"]["stats"].get("cuts_off_beat", 0), 0, rep["check"]["issues"])

    # ---------------------------------------------------------------- velocidad y efectos aprendidos
    def test_speed_ramp(self):
        data = {"output": {"width": 180, "height": 320, "fps": 30, "preset": "ultrafast"}, "segments": [
            {"type": "clip", "src": self.clips[0], "duration": 2.0, "ramp": [[0.5, 0.25], [1.5, 1.0]], "ramp_ease": 0}]}
        project = parse_project(data, self.dir)
        self.assertAlmostEqual(source_length(project.segments[0].source, 2.0), 0.5 + 0.25 + 0.5, delta=0.01)
        out = os.path.join(self.dir, "ramp.mp4")
        render(project, out, log=lambda *_: None)
        self.assertAlmostEqual(ff.duration(out), 2.0, delta=0.1)

    def test_detects_punch_shake_speed_text(self):
        c = self.clips
        data = {"output": {"width": 360, "height": 640, "fps": 30, "crf": 20, "preset": "ultrafast"}, "segments": [
            {"type": "clip", "src": c[0], "duration": 3, "effects": [{"type": "punch", "times": [1.0], "amount": 0.12}]},
            {"type": "clip", "src": c[3], "duration": 3, "effects": [{"type": "shake", "at": 1.0, "duration": 1.0, "amount": 0.7}]},
            {"type": "clip", "src": c[0], "duration": 3, "ramp": [[1.0, 0.3], [2.0, 1.0]]},
            {"type": "clip", "src": c[4], "duration": 3, "texts": [{"text": "PUSH DAY", "y": 0.5, "size": 64, "start": 0.5, "end": 2.5}]},
        ]}
        out = os.path.join(self.dir, "fx9.mp4")
        render(parse_project(data, self.dir), out, log=lambda *_: None, qa=False)
        a = analyze(out)
        self.assertEqual(len(a.shots), 4, [(s.start, s.end) for s in a.shots])
        self.assertTrue(a.shots[0].punches and abs(a.shots[0].punches[0][0] - 1.0) < 0.1)
        self.assertTrue(a.shots[2].speed and a.shots[2].speed[0][2] < 0.5)
        if shutil.which("tesseract"):
            self.assertTrue(any("PUSH" in t["text"] for t in a.shots[3].texts), a.shots[3].texts)

    # ---------------------------------------------------------------- calidad
    def test_quality_checks(self):
        data = {"output": {"width": 180, "height": 320, "fps": 30, "preset": "ultrafast"}, "music": {"src": self.music},
                "segments": [{"type": "clip", "src": self.clips[0], "duration": 1.0},
                             {"type": "clip", "src": self.clips[0], "in": 2.0, "duration": 2.5}]}
        project = parse_project(data, self.dir)
        fixed, warns = qa.preflight(project)
        self.assertTrue(warns)
        s2 = fixed.segments[1].source
        self.assertLessEqual(s2.inpoint + source_length(s2, 2.5), ff.duration(self.clips[0]) + 0.05)
        out = os.path.join(self.dir, "frozen.mp4")
        render(project, out, log=lambda *_: None, qa=False)
        self.assertTrue(any("congelada" in i for i in qa.check_output(out, project)["issues"]))
        rep = {}
        render(project, out, log=lambda *_: None, report=rep)
        self.assertFalse(any("congelada" in i for i in rep["check"]["issues"]))

    def test_hdr_is_tonemapped(self):
        hdr = os.path.join(self.dir, "hdr.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=30:duration=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_primaries", "bt2020",
                        "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", hdr], check=True)
        self.assertTrue(qa.is_hdr(hdr))
        out = os.path.join(self.dir, "hdr_out.mp4")
        render(parse_project(self.small({"segments": [{"type": "clip", "src": hdr, "duration": 0.8}]}), self.dir),
               out, log=lambda *_: None)
        self.assertAlmostEqual(ff.duration(out), 0.8, delta=0.15)

    # ---------------------------------------------------------------- cine y sonido
    def test_cinematic_and_sound(self):
        c = self.clips
        data = self.small({"music": {"src": self.music}, "sound_design": True, "grade": "film",
                           "sfx": [{"type": "hit", "at": 0.2}],
                           "info": {"drop": 1.5, "beats": [i * 0.5 for i in range(20)]},
                           "segments": [
                               {"type": "clip", "src": c[0], "duration": 1.5, "effects": [{"type": "stabilize"}]},
                               {"type": "clip", "src": c[1], "duration": 1.5, "transition": {"type": "slideup", "duration": 0.4},
                                "effects": [{"type": "halation"}, {"type": "film_grain"}, {"type": "glitch", "duration": 0.5}]}]})
        project = parse_project(data, self.dir)
        from videoeditor.render import _sfx_cues
        kinds = {c_["type"] for c_ in _sfx_cues(project)}
        self.assertTrue({"whoosh", "impact", "glitch", "hit"} <= kinds, kinds)
        out = os.path.join(self.dir, "cine.mp4")
        rep = {}
        render(project, out, log=lambda *_: None, report=rep)
        self.assertAlmostEqual(ff.duration(out), 3.0, delta=0.15)
        self.assertLess(rep["check"]["stats"]["peak_db"], -0.1)


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
