"""Regresiones de la revisión de código: estilos, validación de proyectos, LUTs, servidor, CLI."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer

from tests.synth import make_song
from tests.test_editor import HAS_FFMPEG, _make_clip
from videoeditor import ff, luts, qa
from videoeditor.auto import AutoOptions
from videoeditor.project import ProjectError, parse_project
from videoeditor.style import apply_style, find_style


def _seg(**kw):
    return {"type": "black", "duration": 1.0, **kw}


class ProjectValidationTests(unittest.TestCase):
    def parse(self, **kw):
        data = {"segments": [{"type": "black", "duration": 1.0}]}
        data.update(kw)
        return parse_project(data, ".")

    def test_music_without_src_is_no_music(self):
        # el editor deja {start, fade_out…} sin 'src' al elegir "(sin música)"
        self.assertIsNone(self.parse(music={"start": 1.0, "fade_out": 0.4}).music)
        self.assertIsNone(self.parse(music={}).music)
        self.assertEqual(self.parse(music="a.mp3").music.src, os.path.normpath("a.mp3"))

    def test_malformed_structures_raise_project_error(self):
        bad = [
            {"segments": [1]},
            {"segments": "x"},
            {"segments": [_seg(effects=[5])]},
            {"segments": [_seg(effects="glitch")]},
            {"segments": [_seg(texts=[5])]},
            {"segments": [_seg(texts=7)]},
            {"segments": [_seg(transition=5)]},
            {"segments": [{"type": "stack", "duration": 1, "panels": [1, 2]}]},
            {"segments": [{"type": "clip", "duration": 1, "src": 5}]},
            {"segments": [_seg()], "music": 5},
            {"segments": [_seg()], "sfx": [1]},
            {"segments": [_seg()], "output": 3},
        ]
        for data in bad:
            with self.assertRaises(ProjectError, msg=data):
                parse_project(data, ".")

    def test_effect_params_validated(self):
        ok = [{"type": "glitch", "at": "end", "duration": 0.5}, {"type": "flash", "times": [0.1, 0.5]},
              {"type": "strobe", "color": "white", "rate": 8, "amount": 0.5}, {"type": "shake", "at": 0.3}]
        self.parse(segments=[_seg(effects=ok)])
        for fx in ({"type": "glitch", "at": "middle"}, {"type": "flash", "times": "0.5"},
                   {"type": "flash", "times": ["a"]}, {"type": "punch", "amount": "mucho"},
                   {"type": "letterbox", "size": 2}, {"type": "strobe", "color": "no es color!"},
                   {"type": "fade_out", "duration": -1}, {"type": "blur", "amount": True}):
            with self.assertRaises(ProjectError, msg=fx) as cm:
                self.parse(segments=[_seg(effects=[fx])])
            self.assertIn("effects[0]", str(cm.exception))

    def test_output_validated(self):
        self.assertEqual(self.parse(output={"width": 360, "height": 640, "fps": 30, "crf": 23,
                                            "preset": "fast"}).output.preset, "fast")
        for o in ({"fps": 0}, {"fps": -5}, {"width": 0}, {"height": -2}, {"width": 361}, {"preset": "rapidisimo"},
                  {"crf": 52}, {"crf": -1}, {"fps": "x"}):
            with self.assertRaises(ProjectError, msg=o):
                self.parse(output=o)

    def test_colors(self):
        for c in ("red", "#FF8800", "0xFF8800", "white@0.5", "#ff880080"):
            self.assertEqual(self.parse(segments=[{"type": "color", "duration": 1, "color": c}]).segments[0].color, c)
        for c in ("rojo!", "#12", "red;drawtext", 5):
            with self.assertRaises(ProjectError, msg=c):
                self.parse(segments=[{"type": "color", "duration": 1, "color": c}])
        with self.assertRaises(ProjectError):
            self.parse(segments=[_seg(texts=[{"text": "x", "color": "a b"}])])

    def test_transition_aliases_are_normalised(self):
        from videoeditor.sfx import MOVING

        p = self.parse(segments=[_seg(), _seg(transition={"type": "zoom", "duration": 0.3}),
                                 _seg(transition="fadewhite")])
        self.assertEqual(p.segments[1].transition.type, "zoomin")
        self.assertIn(p.segments[1].transition.type, MOVING)
        self.assertEqual(p.segments[2].transition.type, "flash")
        self.assertEqual(p.segments[2].transition.xfade, "fadewhite")

    def test_qa_warnings(self):
        p = self.parse(segments=[_seg(), _seg(duration=0.5, transition={"type": "whip", "duration": 0.4}),
                                 _seg(duration=1.0, transition={"type": "zoomthrough", "duration": 0.4})])
        # whip entra (0.2 s) y zoomthrough sale (0.2 s) dentro de 0.5 s: caben
        self.assertFalse([w for w in qa.check_project(p) if "solapan" in w])
        p = self.parse(segments=[_seg(), _seg(duration=0.3, transition={"type": "whip", "duration": 0.29}),
                                 _seg(duration=1.2, transition={"type": "zoomthrough", "duration": 0.59})])
        self.assertTrue([w for w in qa.check_project(p) if "solapan" in w])

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
    def test_short_music_warning(self):
        d = tempfile.mkdtemp(prefix="ve_fix_")
        try:
            song = os.path.join(d, "s.wav")
            make_song(song)
            dur = ff.duration(song)
            p = self.parse(segments=[_seg(duration=dur + 5)], music={"src": song})
            self.assertTrue([w for w in qa.check_project(p) if "más corta" in w])
            p = self.parse(segments=[_seg(duration=2)], music={"src": song, "start": dur + 3})
            self.assertTrue([w for w in qa.check_project(p) if "más allá" in w])
            p = self.parse(segments=[_seg(duration=2)], music={"src": song})
            self.assertEqual(qa.check_project(p), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
class StyleTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="ve_fix_")
        cls.clip = os.path.join(cls.dir, "c.mp4")
        _make_clip(cls.clip, "testsrc2", dur=6)
        cls.song = os.path.join(cls.dir, "song.wav")
        make_song(cls.song, offset=0.3)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    @staticmethod
    def style(sections, start_beat=0.0):
        return {"name": "Sin negro", "id": "sin_negro",
                "variants": [{"source": "x", "start_beat": start_beat, "sections": sections}]}

    def assert_cuts_on_beats(self, data):
        from videoeditor.music import cached_music_map

        full = cached_music_map(self.song)
        start = data["music"]["start"]
        t, cuts = 0.0, []
        for sg in data["segments"][:-1]:
            t += sg["duration"]
            cuts.append(t)
        self.assertTrue(cuts)
        for c in cuts:
            self.assertLess(min(abs(start + c - b) for b in full.beats), 0.03, (c, start))

    def test_cuts_on_beats_without_black_lead(self):
        st = self.style([{"type": "clip", "beats": 2.0} for _ in range(3)])
        opt = dict(smart_inpoints=False)
        # sin seguir la música, y con inicio de canción explícito
        data, _ = apply_style(st, [self.clip], self.song, AutoOptions(**opt), self.dir, follow_music=False)
        self.assert_cuts_on_beats(data)
        data, _ = apply_style(st, [self.clip], self.song, AutoOptions(music_start=2.0, **opt), self.dir,
                              follow_music=False)
        self.assert_cuts_on_beats(data)
        data, _ = apply_style(st, [self.clip], self.song, AutoOptions(music_start=2.0, **opt), self.dir)
        self.assert_cuts_on_beats(data)

    def test_cuts_on_beats_with_black_lead(self):
        st = self.style([{"type": "black", "until": "first_beat"}] + [{"type": "clip", "beats": 2.0}] * 3)
        data, _ = apply_style(st, [self.clip], self.song, AutoOptions(smart_inpoints=False), self.dir,
                              follow_music=False)
        self.assertEqual(data["segments"][0]["type"], "black")
        self.assert_cuts_on_beats(data)

    def test_short_max_duration_is_respected(self):
        style = find_style("gym_noche", None)
        for md in (1.0, 2.0):
            data, _ = apply_style(style, [self.clip, self.clip], self.song,
                                  AutoOptions(max_duration=md, smart_inpoints=False), self.dir)
            total = sum(sg["duration"] for sg in data["segments"])
            self.assertLessEqual(total, md + 0.05, (md, total))

    def test_style_lut_from_arbitrary_path_is_a_cube(self):
        style = dict(find_style("gym_noche", None), id="custom_fuera_de_carpeta", name="Fuera")
        d = tempfile.mkdtemp(prefix="ve_fix_proj_")
        try:
            data, _ = apply_style(style, [self.clip], self.song, AutoOptions(smart_inpoints=False), d, lut="style")
            self.assertEqual(data["lut"], os.path.join("luts", "custom_fuera_de_carpeta.cube"))
            self.assertTrue(os.path.isfile(os.path.join(d, data["lut"])))
            parse_project(data, d)  # el render puede resolverlo
            self.assertTrue(os.path.isfile(luts.resolve(data["lut"], 1.0, d)))
            # un estilo de las carpetas estándar sigue usando style:<id>
            data, _ = apply_style(find_style("gym_noche", None), [self.clip], self.song,
                                  AutoOptions(smart_inpoints=False), d, lut="style")
            self.assertEqual(data["lut"], "style:gym_noche")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class LutTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ve_fix_lut_")
        self.old_cache = os.environ.get("VIDEOEDITOR_LUT_CACHE")
        os.environ["VIDEOEDITOR_LUT_CACHE"] = os.path.join(self.dir, "cache")
        os.makedirs(os.path.join(self.dir, "cache"))

    def tearDown(self):
        luts.set_workspace(None)
        if self.old_cache is None:
            os.environ.pop("VIDEOEDITOR_LUT_CACHE", None)
        else:
            os.environ["VIDEOEDITOR_LUT_CACHE"] = self.old_cache
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_style_lut_cache_follows_relearned_profile(self):
        os.makedirs(os.path.join(self.dir, "styles"))
        luts.set_workspace(self.dir)
        st = find_style("gym_noche", None)
        path = os.path.join(self.dir, "styles", "mio.json")

        def write(color):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dict(st, id="mio", name="Mio", color=color), f)

        write(st["color"])
        first = luts.resolve("style:mio", 1.0, self.dir)
        write({c: [min(1.0, v + 0.2) for v in st["color"][c]] for c in "rgb"})
        second = luts.resolve("style:mio", 1.0, self.dir)
        self.assertNotEqual(first, second)

    def test_other_lut_formats(self):
        f = os.path.join(self.dir, "look.3dl")
        with open(f, "w") as fh:
            fh.write("0 1023\n")
        self.assertEqual(luts.resolve(f, 1.0), f)  # a intensidad 1 ffmpeg lo lee directamente
        with self.assertRaises(luts.LutError) as cm:
            luts.resolve(f, 0.5)
        self.assertIn(".cube", str(cm.exception))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
class ServerRobustnessTests(unittest.TestCase):
    def setUp(self):
        from videoeditor.server import App, make_handler

        self.ws = tempfile.mkdtemp(prefix="ve_fix_ws_")
        self.app = App(self.ws)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.ws, ignore_errors=True)

    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + path, data=data, method=method)) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_malformed_requests_get_json_errors(self):
        for body in ({"segments": [1]}, {"segments": [{"type": "clip", "duration": 1}]},
                     {"segments": [{"type": "black", "duration": 1, "effects": [3]}]},
                     {"segments": [{"type": "black", "duration": 1}], "output": []}):
            code, res = self.call("PUT", "/api/projects/malo", body)
            self.assertEqual(code, 400, body)
            self.assertIn("error", res)
        # sin 'src' en la música: se acepta como "sin música"
        code, _ = self.call("PUT", "/api/projects/ok", {"segments": [{"type": "black", "duration": 1}],
                                                        "music": {"start": 0, "fade_out": 0.4}})
        self.assertEqual(code, 200)
        code, res = self.call("PUT", "/api/projects/malo", raw=b"{no es json")
        self.assertEqual(code, 400)
        code, res = self.call("POST", "/api/render", raw=b"[]")
        self.assertIn(code, (400, 500))
        self.assertIn("error", res)
        code, res = self.call("POST", "/api/render", {"nada": 1})
        self.assertEqual(code, 400)
        # un proyecto roto en disco tampoco corta la conexión
        with open(os.path.join(self.ws, "projects", "roto.json"), "w") as f:
            f.write('{"segments": [1]}')
        code, res = self.call("POST", "/api/render", {"project": "roto"})
        self.assertEqual(code, 200)
        for _ in range(50):
            j = self.call("GET", f"/api/jobs/{res['job']}")[1]
            if j["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(j["status"], "error")

    def test_same_key_job_not_started_twice(self):
        gate = threading.Event()
        jobs = self.app.jobs
        a = jobs.start("render", lambda log: gate.wait(5), key="render:p")
        b = jobs.start("render", lambda log: None, key="render:p")
        c = jobs.start("render", lambda log: None, key="render:otro")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        gate.set()
        for _ in range(50):
            if jobs.get(a)["status"] != "running":
                break
            time.sleep(0.05)
        self.assertNotEqual(jobs.start("render", lambda log: None, key="render:p"), a)

    def test_thumb_cache_follows_file_changes(self):
        clip = os.path.join(self.ws, "clips", "c.mp4")
        _make_clip(clip, "testsrc2", dur=2, size="160x284")
        t1 = self.app.thumb("clips", "c.mp4", 0.5)
        self.assertTrue(os.path.isfile(t1))
        _make_clip(clip, "smptebars", dur=3, size="160x284")
        t2 = self.app.thumb("clips", "c.mp4", 0.5)
        self.assertNotEqual(t1, t2)
        self.assertFalse([f for f in os.listdir(os.path.join(self.ws, "cache")) if ".part" in f])

    def test_lut_upload_by_extension(self):
        code, _ = self.call("PUT", "/api/upload/luts/viejo.3dl", raw=b"0 1023\n")
        self.assertEqual(code, 200)
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "luts", "viejo.3dl")))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
class RenderFixTests(unittest.TestCase):
    def test_cutout_failure_falls_back(self):
        from videoeditor import render as render_mod
        from videoeditor import segment

        d = tempfile.mkdtemp(prefix="ve_fix_r_")
        try:
            clip = os.path.join(d, "c.mp4")
            _make_clip(clip, "testsrc2", dur=2, size="160x284")
            p = parse_project({"output": {"width": 90, "height": 160, "fps": 15, "preset": "ultrafast"},
                               "segments": [{"type": "clip", "src": clip, "duration": 1},
                                            {"type": "clip", "src": clip, "duration": 1,
                                             "transition": {"type": "subject_drop", "duration": 0.3}}]}, d)
            logs = []
            old = segment.cutout_video

            def boom(*a, **k):
                raise ff.FFmpegError("fallo de ffmpeg")

            segment.cutout_video = boom
            try:
                self.assertEqual(render_mod._make_cutouts(p, d, logs.append), {})
            finally:
                segment.cutout_video = old
            self.assertTrue(any("slidedown" in m for m in logs))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_failed_render_leaves_no_partial_file(self):
        from videoeditor.render import render

        d = tempfile.mkdtemp(prefix="ve_fix_r_")
        try:
            clip = os.path.join(d, "c.mp4")
            _make_clip(clip, "testsrc2", dur=2, size="160x284")
            p = parse_project({"output": {"width": 90, "height": 160, "fps": 15, "preset": "ultrafast"},
                               "segments": [{"type": "clip", "src": clip, "duration": 1}]}, d)
            out = os.path.join(d, "out", "r.mp4")
            old = ff.run

            def fail_on_assemble(args, **k):
                if args and args[-1].endswith(".part.mp4"):
                    with open(args[-1], "wb") as fh:
                        fh.write(b"basura")
                    raise ff.FFmpegError("se cayó")
                return old(args, **k)

            ff.run = fail_on_assemble
            try:
                with self.assertRaises(ff.FFmpegError):
                    render(p, out, log=lambda *_: None, qa=False)
            finally:
                ff.run = old
            self.assertFalse(os.path.exists(out))
            self.assertEqual(os.listdir(os.path.join(d, "out")), [])
            render(p, out, log=lambda *_: None, qa=False)
            self.assertTrue(os.path.exists(out))
            self.assertEqual(sorted(os.listdir(os.path.join(d, "out"))), ["r.mp4"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_preview_scales_pixel_values(self):
        from videoeditor.render import preview_project

        p = parse_project({"output": {"width": 1080, "height": 1920},
                           "segments": [{"type": "stack", "duration": 1, "gap": 20,
                                         "panels": [{"src": "a.mp4"}, {"src": "b.mp4"}],
                                         "texts": [{"text": "HOLA", "size": 100}],
                                         "effects": [{"type": "rgb_split", "amount": 10}, {"type": "glitch"}]}]}, ".")
        q = preview_project(p, 0.5).segments[0]
        self.assertEqual((q.gap, q.texts[0].size), (10, 50))
        self.assertEqual(q.effects[0].params["amount"], 5)
        self.assertEqual(p.segments[0].gap, 20)  # el original no cambia


class CliTests(unittest.TestCase):
    def test_workspace_after_subcommand(self):
        from videoeditor.__main__ import main

        d = tempfile.mkdtemp(prefix="ve_fix_cli_")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["luts", "-w", d]), 0)
                self.assertEqual(main(["-w", d, "luts"]), 0)
                self.assertEqual(main(["luts", "--workspace", d]), 0)
            self.assertIn("noche_azul", buf.getvalue())
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
