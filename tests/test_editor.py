"""Tests de extremo a extremo con clips sintéticos (requieren ffmpeg)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse

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

    # ---------------------------------------------------------------- técnicas nuevas
    def color_clip(self):
        """Segundo clip en color y con movimiento, distinto de testsrc2 (tono girado y volteado)."""
        if not hasattr(type(self), "_tsb"):
            p = os.path.join(self.dir, "tsb.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=3",
                            "-vf", "hue=h=120,hflip,vflip", "-c:v", "libx264", "-pix_fmt", "yuv420p", p], check=True)
            type(self)._tsb = p
        return self._tsb

    def techniques_video(self):
        """Edit con todas las técnicas nuevas, en tiempos conocidos (se renderiza una vez)."""
        if not hasattr(type(self), "_tech"):
            a, b = self.clips[0], self.color_clip()
            data = {"output": {"width": 180, "height": 320, "fps": 30, "crf": 20, "preset": "ultrafast"},
                    "music": {"src": self.music}, "segments": [
                        {"type": "clip", "src": a, "duration": 2.0, "effects": [{"type": "freeze", "at": 0.8, "duration": 0.5}]},
                        {"type": "clip", "src": b, "duration": 2.0, "grade": "bw"},
                        {"type": "clip", "src": a, "in": 1.0, "duration": 2.0,
                         "effects": [{"type": "strobe", "at": 0.5, "duration": 1.0, "rate": 6}]},
                        {"type": "clip", "src": b, "in": 1.0, "duration": 2.0,
                         "effects": [{"type": "bw_flash", "times": [1.0], "duration": 0.15}]},
                        {"type": "clip", "src": a, "in": 0.5, "duration": 1.5, "transition": {"type": "zoomthrough", "duration": 0.4}},
                        {"type": "clip", "src": b, "duration": 1.5, "transition": {"type": "whip", "duration": 0.4}},
                        {"type": "stack", "layout": "columns", "duration": 2.4, "panels": [
                            {"src": a, "appear": 0}, {"src": b, "appear": 0.8}, {"src": a, "in": 1.0, "appear": 1.6}]},
                        {"type": "stack", "layout": "grid", "duration": 2.4, "panels": [
                            {"src": a, "appear": 0}, {"src": b, "appear": 0.6}, {"src": a, "in": 1.0, "appear": 1.2},
                            {"src": b, "in": 1.0, "appear": 1.8}]},
                        {"type": "clip", "src": a, "duration": 1.0}]}
            project = parse_project(data, self.dir)
            out = os.path.join(self.dir, "tecnicas.mp4")
            rep = {}
            render(project, out, log=lambda *_: None, report=rep)
            type(self)._tech = (out, project, rep)
        return self._tech

    def test_new_techniques_roundtrip(self):
        """Congelación, blanco y negro, estroboscopio, destello B/N, zoom a través del corte,
        barrido y paneles en columnas / cuadrícula: se renderizan y el análisis los encuentra."""
        out, project, rep = self.techniques_video()
        # el QA no confunde la congelación ni el estroboscopio con fallos
        self.assertFalse([i for i in rep["check"]["issues"] if "congelada" in i or "Negro" in i], rep["check"]["issues"])
        a = analyze(out)
        sh = a.shots
        starts = [s.start for s in sh]
        self.assertEqual(len(sh), 9, [(s.start, s.kind, s.transition["type"]) for s in sh])
        for got, want in zip(starts, [0, 2, 4, 6, 8, 9.5, 11, 13.4, 15.8]):
            self.assertAlmostEqual(got, want, delta=0.15)
        # 1. imagen congelada
        self.assertEqual(len(sh[0].freezes), 1)
        self.assertAlmostEqual(sh[0].freezes[0][0], 0.8, delta=0.15)
        self.assertAlmostEqual(sh[0].freezes[0][1], 0.5, delta=0.15)
        # 2. plano en blanco y negro y destello en B/N
        self.assertEqual([s.grade for s in sh], [None, "bw"] + [None] * 7)
        self.assertEqual(len(sh[3].bw_flashes), 1)
        self.assertAlmostEqual(sh[3].bw_flashes[0][0], 1.0, delta=0.15)
        # 3. estroboscopio: momento, duración y ritmo
        self.assertEqual(len(sh[2].strobes), 1)
        st = sh[2].strobes[0]
        self.assertAlmostEqual(st["at"], 0.5, delta=0.15)
        self.assertAlmostEqual(st["duration"], 1.0, delta=0.15)
        self.assertAlmostEqual(st["rate"], 6.0, delta=0.5)
        # 4-5. zoom a través del corte y barrido (centrados en el corte)
        self.assertEqual(sh[4].transition["type"], "zoomthrough")
        self.assertAlmostEqual(sh[4].transition["duration"], 0.4, delta=0.15)
        self.assertEqual(sh[5].transition["type"], "whip")
        self.assertEqual(sh[5].transition["direction"], "left")
        self.assertAlmostEqual(sh[5].transition["duration"], 0.4, delta=0.15)
        # 6. paneles en columnas y en cuadrícula
        self.assertEqual((sh[6].kind, sh[6].layout, len(sh[6].panels)), ("stack", "columns", 3))
        self.assertEqual((sh[7].kind, sh[7].layout, len(sh[7].panels)), ("stack", "grid", 4))
        for got, want in zip(sh[6].panels + sh[7].panels, [0, 0.8, 1.6, 0, 0.6, 1.2, 1.8]):
            self.assertAlmostEqual(got, want, delta=0.15)
        # sin nada más inventado
        for i, s in enumerate(sh):
            self.assertEqual(len(s.freezes), int(i == 0))
            self.assertEqual(len(s.strobes), int(i == 2))
            self.assertEqual(len(s.bw_flashes), int(i == 3))
            self.assertFalse(s.speed or s.shakes, (i, s.speed, s.shakes))
        tpl = to_template(a)
        segs = tpl["segments"]
        self.assertEqual(segs[1]["grade"], "bw")
        self.assertEqual(segs[6]["layout"], "columns")
        self.assertIn("freeze", [e["type"] for e in segs[0]["effects"]])
        parse_project(tpl, self.dir)
        report_txt = __import__("videoeditor.analyze", fromlist=["report"]).report(a)
        for word in ("imagen congelada", "blanco y negro", "estroboscopio", "zoom a través del corte",
                     "barrido", "columnas", "cuadrícula", "destello en B/N"):
            self.assertIn(word, report_txt)

    def test_new_techniques_no_false_positives(self):
        """Un montaje normal (cortes secos, un slide y un zoom de golpe) no tiene ninguna técnica nueva."""
        c, b = self.clips, self.color_clip()
        data = {"output": {"width": 180, "height": 320, "fps": 30, "crf": 20, "preset": "ultrafast"}, "segments": [
            {"type": "clip", "src": c[0], "duration": 1.5},
            {"type": "clip", "src": b, "duration": 1.5, "effects": [{"type": "punch", "times": [0.7]}]},
            {"type": "clip", "src": c[1], "duration": 1.5},
            {"type": "clip", "src": c[0], "in": 1.0, "duration": 1.5, "transition": {"type": "slideleft", "duration": 0.4}},
            {"type": "clip", "src": b, "in": 1.0, "duration": 1.5},
            {"type": "stack", "duration": 2.0, "panels": [{"src": c[0], "appear": 0}, {"src": b, "appear": 0.7},
                                                          {"src": c[0], "in": 1, "appear": 1.4}]}]}
        out = os.path.join(self.dir, "control.mp4")
        render(parse_project(data, self.dir), out, log=lambda *_: None, qa=False)
        a = analyze(out)
        types = {s.transition["type"] for s in a.shots}
        self.assertFalse(types & {"zoomthrough", "whip"}, types)
        self.assertEqual((a.shots[-1].kind, a.shots[-1].layout, len(a.shots[-1].panels)), ("stack", "rows", 3))
        for s in a.shots:
            self.assertFalse(s.freezes or s.strobes or s.bw_flashes or s.grade, s)
            self.assertTrue(s.kind != "stack" or s.layout == "rows")
        # los cortes siguen donde estaban
        for want in (1.5, 3.0, 6.0, 7.5):
            self.assertLess(min(abs(s.start - want) for s in a.shots), 0.1)

    def test_new_techniques_learn_and_apply(self):
        out, _, _ = self.techniques_video()
        sdir = os.path.join(self.dir, "styles_tec")
        style = learn([out], "Técnicas", sdir, log=lambda *_: None)
        secs = style["variants"][0]["sections"]
        p = 60 / style["variants"][0]["bpm"]
        self.assertEqual(secs[1].get("grade"), "bw")
        fx = {e["type"]: e for sec in secs for e in sec.get("effects", [])}
        self.assertTrue({"freeze", "strobe", "bw_flash"} <= set(fx), fx)
        self.assertAlmostEqual(fx["freeze"]["beats"] * p, 0.5, delta=0.15)
        self.assertAlmostEqual(fx["strobe"]["period_beats"] * p, 1 / 6, delta=0.03)  # 6 Hz en beats
        self.assertEqual(secs[4]["transition"]["type"], "zoomthrough")
        self.assertEqual(secs[5]["transition"]["type"], "whip")
        self.assertEqual([secs[6].get("layout"), secs[7].get("layout")], ["columns", "grid"])

        data, info = apply_style(style, self.clips[:2] + [self.color_clip()], self.music,
                                 AutoOptions(), self.dir, follow_music=False, variant=0)
        segs = data["segments"]
        self.assertEqual([s.get("layout") for s in segs if s["type"] == "stack"], ["columns", "grid"])
        self.assertIn("bw", [s.get("grade") for s in segs])
        self.assertEqual({"zoomthrough", "whip"}, {(s.get("transition") or {}).get("type") for s in segs} & {"zoomthrough", "whip"})
        types = {e["type"] for s in segs for e in s.get("effects", [])}
        self.assertTrue({"freeze", "strobe", "bw_flash"} <= types, types)
        # el estroboscopio va en subdivisiones del beat de la canción nueva
        strobe = next(e for s in segs for e in s.get("effects", []) if e["type"] == "strobe")
        self.assertAlmostEqual(1 / strobe["rate"], fx["strobe"]["period_beats"] * info.period, delta=0.01)
        # los cortes de las transiciones centradas caen en el beat
        t = 0.0
        for s in segs:
            if (s.get("transition") or {}).get("type") in ("zoomthrough", "whip"):
                self.assertLess(min(abs(t - b) for b in info.beats), 0.02)
            t += s["duration"]
        project = parse_project(self.small(data), self.dir)
        from videoeditor.render import _sfx_cues
        self.assertTrue(any(c_["type"] == "whoosh" for c_ in _sfx_cues(project)))
        out2 = os.path.join(self.dir, "styled_tec.mp4")
        render(project, out2, log=lambda *_: None)
        self.assertAlmostEqual(ff.duration(out2), project.total_duration, delta=0.15)

    def test_new_techniques_validation(self):
        with self.assertRaises(ProjectError):
            parse_project({"segments": [{"type": "stack", "duration": 1, "layout": "diagonal",
                                         "panels": [{"src": "a"}, {"src": "b"}]}]})
        with self.assertRaises(ProjectError):  # la mitad de salida no cabe en el segmento anterior
            parse_project({"segments": [{"type": "black", "duration": 0.1},
                                        {"type": "black", "duration": 1, "transition": {"type": "whip", "duration": 0.4}}]})
        p = parse_project({"segments": [{"type": "black", "duration": 1},
                                        {"type": "black", "duration": 1, "transition": {"type": "whip", "duration": 0.4}}]})
        self.assertEqual(p.segments[1].transition.direction, "left")
        self.assertEqual(p.segments[1].transition.overlap, 0.0)
        from videoeditor.render import expand_transitions
        ex = expand_transitions(p)
        self.assertEqual([e.type for e in ex.segments[0].effects], ["tr_whip"])
        self.assertEqual(ex.segments[1].effects[0].params["phase"], "in")

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


def _frame_rgb(path, t):
    """Color medio (R, G, B, 0-255) del fotograma en el segundo t."""
    import numpy as np

    raw = ff.capture(["-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", "scale=32:-2,format=rgb24",
                      "-f", "rawvideo", "-"])
    return np.frombuffer(raw, np.uint8).reshape(-1, 3).mean(axis=0)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
class LutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="ve_lut_")
        cls.clip = os.path.join(cls.dir, "c.mp4")
        _make_clip(cls.clip, "testsrc2", dur=3.0)
        cls.music = os.path.join(cls.dir, "music.m4a")
        _make_music(cls.music)
        os.environ["VIDEOEDITOR_LUT_CACHE"] = os.path.join(cls.dir, "lutcache")

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("VIDEOEDITOR_LUT_CACHE", None)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def project(self, segments, **extra):
        data = {"output": {"width": 180, "height": 320, "fps": 15, "crf": 30, "preset": "ultrafast"},
                "segments": segments, **extra}
        return parse_project(data, self.dir)

    def rend(self, name, segments, **extra):
        out = os.path.join(self.dir, name + ".mp4")
        render(self.project(segments, **extra), out, log=lambda *_: None, qa=False)
        return out

    def test_cube_files_are_valid(self):
        import numpy as np
        from videoeditor import luts

        self.assertGreaterEqual(len(luts.PRESETS), 8)
        ident = luts.identity_lattice()
        self.assertEqual(ident.shape, (33 ** 3, 3))
        self.assertAlmostEqual(ident[1, 0], 1 / 32)  # el rojo varía más rápido
        self.assertEqual(ident[1, 1], 0.0)
        diag_idx = [i * (33 * 33 + 33 + 1) for i in range(33)]
        for look in luts.PRESETS.values():
            self.assertTrue(look.name and look.description, look.id)
            text = luts.preset_cube(look)
            lines = [ln for ln in text.splitlines() if ln and not ln.startswith(("#", "TITLE", "LUT_", "DOMAIN"))]
            self.assertEqual(len(lines), 33 ** 3, look.id)
            self.assertIn("LUT_3D_SIZE 33", text)
            size, arr = luts.parse_cube(text)
            self.assertEqual(size, 33)
            self.assertTrue(((arr >= 0) & (arr <= 1)).all(), look.id)
            # la diagonal de grises sube (con algo de tolerancia): no hay inversiones fuertes
            diag = arr[diag_idx] @ luts.LUMA
            self.assertGreater(diag[-1], diag[0] + 0.3, look.id)
            self.assertGreater(np.diff(diag).min(), -0.02, look.id)
            # el .cube publicado en videoeditor/luts coincide con el generado
            _, shipped = luts.read_cube(os.path.join(luts.SHIPPED_DIR, f"{look.id}.cube"))
            self.assertLess(np.abs(shipped - arr).max(), 1e-5, f"regenera con python -m videoeditor.luts ({look.id})")
            # con intensidad 0 es la identidad exacta; a 0.5, el punto medio
            self.assertLess(np.abs(look.lattice(0.0) - ident).max(), 1e-12)
            self.assertLess(np.abs(look.lattice(0.5) - (ident + arr) / 2).max(), 1e-3)
        with self.assertRaises(luts.LutError):
            luts.parse_cube("LUT_3D_SIZE 2\n0 0 0\n")

    def test_luminance_not_crushed(self):
        """Sobre material oscuro los looks no deben aplastar la imagen a negro."""
        import numpy as np
        from videoeditor import luts

        rng = np.random.default_rng(1)
        px = np.clip(rng.gamma(2.0, 0.045, (4000, 3)), 0, 1)  # escena nocturna: media ~0.09
        y0 = float((px @ luts.LUMA).mean())
        for look in luts.PRESETS.values():
            y = float((look.apply(px) @ luts.LUMA).mean())
            self.assertGreater(y, 0.45 * y0, look.id)  # phonk_rojo aplasta a propósito, pero no todo
            self.assertLess(y, 4 * y0 + 0.1, look.id)

    def test_learned_lut(self):
        import numpy as np
        from videoeditor import luts
        from videoeditor.style import find_style

        color = find_style("gym_noche", None)["color"]
        lat = luts.learned_lattice(color)
        self.assertTrue(((lat >= 0) & (lat <= 1)).all())
        diag = lat[[i * (33 * 33 + 33 + 1) for i in range(33)]] @ luts.LUMA
        self.assertTrue((np.diff(diag) >= -1e-9).all())
        hi = lat[28 * (33 * 33 + 33 + 1)]  # gris claro: el estilo tiene luces cálidas (R > B)
        self.assertGreater(hi[0], hi[2])
        size, _ = luts.parse_cube(luts.learned_lut(color))
        self.assertEqual(size, 33)
        with self.assertRaises(luts.LutError):
            luts.learned_lattice({"r": [0.1, 0.2]})

    def test_all_presets_render_and_black_is_untouched(self):
        from videoeditor import luts

        segs = [{"type": "black", "duration": 0.4}]
        for i, pid in enumerate(luts.PRESETS):
            segs.append({"type": "clip", "src": self.clip, "in": 0.1 * i, "duration": 0.4, "lut": pid})
        out = self.rend("all_presets", segs, lut="mate_film")  # mate_film levanta los negros
        self.assertAlmostEqual(ff.duration(out), 0.4 * len(segs), delta=0.2)
        self.assertLess(_frame_rgb(out, 0.2).max(), 6)  # el segmento negro no se tiñe
        out = self.rend("stack_lut", [{"type": "stack", "duration": 1.0, "gap": 20, "color": "black", "panels": [
            {"src": self.clip}, {"src": self.clip}]}], lut="mate_film")
        self.assertGreater(ff.duration(out), 0.9)

    def test_strength_and_override(self):
        import numpy as np

        def seg(**kw):
            return [{"type": "clip", "src": self.clip, "in": 0.5, "duration": 1.0, **kw}]

        base = self.rend("no_lut", seg())
        zero = self.rend("zero", seg(), lut="teal_orange", lut_strength=0.0)
        half = self.rend("half", seg(), lut="teal_orange", lut_strength=0.5)
        full = self.rend("full", seg(), lut="teal_orange")
        b, z, h_, f = (_frame_rgb(p, 0.5) for p in (base, zero, half, full))
        self.assertLess(np.abs(b - z).max(), 1.5)  # intensidad 0 = sin cambios
        d_half, d_full = np.abs(h_ - b).sum(), np.abs(f - b).sum()
        self.assertGreater(d_full, 10)
        self.assertGreater(d_half, 0.15 * d_full)
        self.assertLess(d_half, d_full)
        # override por segmento: "none" y null desactivan; otro preset lo cambia
        segs = [{"type": "clip", "src": self.clip, "in": 0.5, "duration": 1.0, "lut": "none"},
                {"type": "clip", "src": self.clip, "in": 0.5, "duration": 1.0, "lut": None},
                {"type": "clip", "src": self.clip, "in": 0.5, "duration": 1.0},
                {"type": "clip", "src": self.clip, "in": 0.5, "duration": 1.0, "lut": "oro_gym", "lut_strength": 0.7}]
        p = self.project(segs, lut="teal_orange")
        self.assertEqual([s.lut for s in p.segments], ["none", "none", None, "oro_gym"])
        out = self.rend("override", segs, lut="teal_orange")
        c = [_frame_rgb(out, t) for t in (0.5, 1.5, 2.5, 3.5)]
        self.assertLess(np.abs(c[0] - b).max(), 1.5)
        self.assertLess(np.abs(c[1] - b).max(), 1.5)
        self.assertLess(np.abs(c[2] - f).max(), 3)
        self.assertGreater(np.abs(c[3] - f).sum(), 8)

    def test_validation_and_compat(self):
        from videoeditor import luts
        from videoeditor.project import check_sources

        seg = [{"type": "black", "duration": 1}]
        for bad in ({"lut": "no_existe"}, {"lut": "style:no_existe_xyz"}, {"lut": 5}, {"lut": "algo.txt"},
                    {"lut": "noche_azul", "lut_strength": 1.5}, {"lut": "noche_azul", "lut_strength": "mucho"}):
            with self.assertRaises(ProjectError, msg=str(bad)):
                self.project(seg, **bad)
        with self.assertRaises(ProjectError) as cm:
            self.project([{"type": "black", "duration": 1, "lut": "x_no"}])
        self.assertIn("segments[0]", str(cm.exception))
        self.assertIn("noche_azul", str(cm.exception))  # sugiere los presets
        with self.assertRaises(ProjectError):
            self.project([{"type": "black", "duration": 1, "lut_strength": -1}])
        p = self.project(seg, lut="noche_azul", lut_strength=0.4)
        self.assertEqual((p.lut, p.lut_strength), ("noche_azul", 0.4))
        self.assertIsNone(self.project(seg).lut)
        self.assertEqual(self.project(seg).lut_strength, 1.0)
        self.assertIsNone(self.project(seg, lut="none").lut)
        # compatibilidad: "lut": "ruta.cube" sigue siendo una ruta relativa al JSON
        shutil.copy(os.path.join(luts.SHIPPED_DIR, "acero_frio.cube"), os.path.join(self.dir, "mi_look.cube"))
        p = self.project(seg, lut="mi_look.cube")
        self.assertEqual(p.lut, os.path.join(self.dir, "mi_look.cube"))
        self.assertEqual(check_sources(p), [])
        p = self.project(seg, lut="falta.cube")
        self.assertEqual(check_sources(p), [os.path.join(self.dir, "falta.cube")])

    def test_path_escaping_and_user_lut_strength(self):
        import numpy as np
        from videoeditor import luts

        weird = os.path.join(self.dir, "mi look: [rojo], 'v2'.cube")
        shutil.copy(os.path.join(luts.SHIPPED_DIR, "phonk_rojo.cube"), weird)
        seg = [{"type": "clip", "src": self.clip, "in": 0.5, "duration": 1.0}]
        base = self.rend("esc_base", seg)
        full = self.rend("esc_full", seg, lut=weird)
        half = self.rend("esc_half", seg, lut=weird, lut_strength=0.5)
        b, f, h_ = (_frame_rgb(p, 0.5) for p in (base, full, half))
        self.assertGreater(np.abs(f - b).sum(), 10)
        self.assertLess(np.abs(h_ - b).sum(), np.abs(f - b).sum())
        self.assertEqual(luts.resolve(weird, 1.0), weird)
        self.assertTrue(luts.resolve(weird, 0.5).startswith(luts.cache_dir()))

    def test_apply_style_with_lut(self):
        from videoeditor.style import find_style

        style = find_style("gym_noche", None)
        data, _ = apply_style(style, [self.clip, self.clip], self.music, AutoOptions(), self.dir,
                              lut="teal_orange", lut_strength=0.7)
        self.assertEqual((data["lut"], data["lut_strength"]), ("teal_orange", 0.7))
        self.assertNotIn("color_match", data)  # con un LUT elegido, el LUT manda
        data, _ = apply_style(style, [self.clip, self.clip], self.music, AutoOptions(), self.dir)
        self.assertIn("color_match", data)  # sin LUT, se copia el color del estilo
        data, _ = apply_style(style, [self.clip, self.clip], self.music, AutoOptions(), self.dir, lut="style")
        self.assertEqual(data["lut"], "style:gym_noche")
        self.assertNotIn("color_match", data)  # el LUT aprendido ya lleva el color del estilo
        data["output"] = {"width": 180, "height": 320, "fps": 15, "crf": 30, "preset": "ultrafast"}
        out = os.path.join(self.dir, "style_lut.mp4")
        render(parse_project(data, self.dir), out, log=lambda *_: None, qa=False)
        self.assertGreater(ff.duration(out), 1.0)

    def test_cli(self):
        import contextlib
        import io

        from videoeditor.__main__ import main

        sheet = os.path.join(self.dir, "sheet.jpg")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["-w", self.dir, "luts"]), 0)
            self.assertEqual(main(["luts", "--export", os.path.join(self.dir, "exp")]), 0)
            self.assertEqual(main(["lut-preview", self.clip, "-o", sheet, "--at", "1", "--width", "90"]), 0)
        self.assertIn("noche_azul", buf.getvalue())
        self.assertEqual(len([f for f in os.listdir(os.path.join(self.dir, "exp")) if f.endswith(".cube")]), 9)
        self.assertGreater(os.path.getsize(sheet), 2000)


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

            # LUTs: listado, subida de .cube propios, miniaturas y creación con LUT
            luts_list = call("GET", "/api/luts")
            ids = [item["id"] for item in luts_list]
            self.assertIn("noche_azul", ids)
            self.assertIn("style:gym_noche", ids)
            self.assertGreaterEqual(len([i for i in luts_list if i["kind"] == "preset"]), 8)
            from videoeditor import luts as luts_mod
            with open(os.path.join(luts_mod.SHIPPED_DIR, "oro_gym.cube"), "rb") as f:
                call("PUT", "/api/upload/luts/mio.cube", raw=f.read())
            with self.assertRaises(urllib.error.HTTPError):
                call("PUT", "/api/upload/luts/roto.cube", raw=b"esto no es un LUT")
            self.assertFalse(os.path.exists(os.path.join(ws, "luts", "roto.cube")))
            user = [i for i in call("GET", "/api/luts") if i["kind"] == "user"]
            self.assertEqual([u["id"] for u in user], ["mio.cube"])
            self.assertEqual(len(call("GET", "/api/library")["luts"]), 1)
            for spec in ("none", "noche_azul", "style:gym_noche", user[0]["value"]):
                with urllib.request.urlopen(url + f"/api/lut-thumb/c0.mp4?lut={urllib.parse.quote(spec)}&strength=0.8&t=1") as r:
                    self.assertEqual(r.headers["Content-Type"], "image/jpeg")
                    self.assertGreater(len(r.read()), 500)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(url + "/api/lut-thumb/c0.mp4?lut=no_existe")
            res = wait(call("POST", "/api/create", {"clips": [f"c{i}.mp4" for i in range(4)], "music": "song.m4a",
                                                    "template": "beat_cuts", "duration": 4, "name": "t2",
                                                    "lut": user[0]["value"], "lut_strength": 0.6})["job"])
            proj = call("GET", f"/api/projects/{res['project']}")
            self.assertEqual((proj["lut"], proj["lut_strength"]), ("../luts/mio.cube", 0.6))
        finally:
            httpd.shutdown()
            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
