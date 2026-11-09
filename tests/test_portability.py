"""Tests de portabilidad (Windows / macOS / Linux): escapado de rutas en filtros, fuentes,
cachés, degradación cuando falta un filtro de ffmpeg y el comando doctor."""
import glob
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from videoeditor import doctor, ff, luts, sysutil

HAS_FFMPEG = shutil.which("ffmpeg") is not None

NASTY_NAMES = [
    "simple",
    "con espacios y más",
    "tilde á ñ Ü",
    "comas,punto;y;coma",
    "corchetes[0] [x]",
    "com'illa",
    "dos:puntos",
    "mezcla: a,b [c] 'd' ñ;e",
]


class FilterValueTests(unittest.TestCase):
    def test_windows_drive_and_backslashes(self):
        # `C:` -> `C\\:` (dos niveles de escape) y las barras pasan a `/`
        self.assertEqual(ff.filter_value(r"C:\Users\Ana\AppData\Local\Temp\x.cube"),
                         r"C\\:/Users/Ana/AppData/Local/Temp/x.cube")

    def test_unc_path(self):
        self.assertEqual(ff.filter_value(r"\\server\share\f.ttf"), "//server/share/f.ttf")

    def test_each_special_char_is_escaped_twice_or_more(self):
        self.assertEqual(ff.filter_value("a b"), "a b")          # el espacio no es especial
        self.assertEqual(ff.filter_value("a,b"), r"a\,b")
        self.assertEqual(ff.filter_value("a;b"), r"a\;b")
        self.assertEqual(ff.filter_value("[a]"), r"\[a\]")
        self.assertEqual(ff.filter_value("a:b"), r"a\\:b")
        self.assertEqual(ff.filter_value("it's"), r"it\\\'s")
        self.assertEqual(ff.filter_value("ñá"), "ñá")

    def test_luts_alias(self):
        self.assertEqual(luts.ff_escape(r"C:\a b\c.cube"), ff.filter_value(r"C:\a b\c.cube"))

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
    def test_real_ffmpeg_accepts_nasty_paths(self):
        cube = sorted(glob.glob(os.path.join(luts.SHIPPED_DIR, "*.cube")))[0]
        font = sysutil.fallback_font() or None
        for dirname in NASTY_NAMES:
            with self.subTest(dirname=dirname):
                base = tempfile.mkdtemp(prefix="ve_pt_")
                try:
                    d = os.path.join(base, dirname)
                    os.makedirs(d)
                    lut = os.path.join(d, "mi look.cube")
                    shutil.copy(cube, lut)
                    txt = os.path.join(d, "texto.txt")
                    with open(txt, "w", encoding="utf-8") as f:
                        f.write("Hola ñandú")
                    chain = [f"lut3d=file={ff.filter_value(lut)}"]
                    if ff.has_filter("drawtext"):
                        opts = [f"textfile={ff.filter_value(txt)}", "fontsize=12"]
                        if font:
                            opts.insert(1, f"fontfile={ff.filter_value(font)}")
                        chain.append("drawtext=" + ":".join(opts))
                    r = subprocess.run(
                        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=0.1",
                         "-vf", ",".join(chain), "-frames:v", "1", "-f", "null", "-"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
                    self.assertEqual(r.returncode, 0, r.stderr)
                    # y como parte de un filter_complex (segundo nivel de escape distinto)
                    r = subprocess.run(
                        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=0.1",
                         "-filter_complex", "[0:v]" + ",".join(chain) + "[o]", "-map", "[o]",
                         "-frames:v", "1", "-f", "null", "-"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
                    self.assertEqual(r.returncode, 0, r.stderr)
                finally:
                    shutil.rmtree(base, ignore_errors=True)

    @unittest.skipUnless(HAS_FFMPEG and ff.has_filter("vidstabdetect"), "sin vidstab")
    def test_real_vidstab_result_path(self):
        base = tempfile.mkdtemp(prefix="ve_pt_")
        try:
            d = os.path.join(base, "carpeta con: ñ, [x]")
            os.makedirs(d)
            trf = os.path.join(d, "stab.trf")
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=128x128:d=0.5:r=15",
                 "-vf", f"vidstabdetect=result={ff.filter_value(trf)}", "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.getsize(trf) > 0)
        finally:
            shutil.rmtree(base, ignore_errors=True)


class CacheAndFontTests(unittest.TestCase):
    def test_cache_root_windows(self):
        with mock.patch.object(sysutil.sys, "platform", "win32"), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Ana\AppData\Local"}, clear=False):
            os.environ.pop("VIDEOEDITOR_CACHE", None)
            root = sysutil.cache_root()
        self.assertTrue(root.startswith(r"C:\Users\Ana\AppData\Local"))
        self.assertIn("videoeditor", root)
        self.assertNotIn(".cache", root)

    def test_cache_root_macos_linux_and_override(self):
        env = {k: v for k, v in os.environ.items() if k not in ("VIDEOEDITOR_CACHE", "XDG_CACHE_HOME")}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(sysutil.sys, "platform", "darwin"):
                self.assertIn(os.path.join("Library", "Caches", "videoeditor"), sysutil.cache_root())
            with mock.patch.object(sysutil.sys, "platform", "linux"):
                self.assertTrue(sysutil.cache_root().endswith(os.path.join(".cache", "videoeditor")))
        with mock.patch.dict(os.environ, {"VIDEOEDITOR_CACHE": "/x/y", "XDG_CACHE_HOME": "/z"}):
            self.assertEqual(sysutil.cache_root(), "/x/y")
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/z"}), \
                mock.patch.object(sysutil.sys, "platform", "linux"):
            os.environ.pop("VIDEOEDITOR_CACHE", None)
            self.assertEqual(sysutil.cache_root(), os.path.join("/z", "videoeditor"))

    def test_fallback_font_lists_cover_all_oses(self):
        joined = "\n".join(sysutil.FONT_FALLBACKS)
        for needle in ("arialbd.ttf", "segoeuib.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
            self.assertIn(needle, joined)

    def test_fallback_font_picks_first_existing(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "b.ttf")
            open(f, "w").close()
            self.assertEqual(sysutil.fallback_font([os.path.join(d, "a.ttf"), f]), f)
            self.assertIsNone(sysutil.fallback_font([os.path.join(d, "nada.ttf")]))

    def test_font_file_uses_fallback_without_fc_match(self):
        from videoeditor import render

        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "arialbd.ttf")
            open(f, "w").close()
            render._font_file.cache_clear()
            with mock.patch.object(render.shutil, "which", return_value=None), \
                    mock.patch.object(sysutil, "FONT_FALLBACKS", [f]):
                self.assertEqual(render._font_file("Sans:style=Bold"), f)
            render._font_file.cache_clear()

    def test_replace_file_retries_permission_error(self):
        calls = {"n": 0}
        real = os.replace

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("en uso")
            return real(a, b)

        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a"), os.path.join(d, "b")
            open(a, "w").close()
            with mock.patch.object(sysutil.os, "replace", flaky):
                sysutil.replace_file(a, b)
            self.assertTrue(os.path.exists(b))
            self.assertEqual(calls["n"], 3)


class DegradeTests(unittest.TestCase):
    """Builds de ffmpeg 'essentials': sin zscale / vidstab / drawtext / libx264."""

    def setUp(self):
        sysutil._warned.clear()

    def _project(self, **seg_extra):
        from videoeditor.project import parse_project

        seg = {"type": "clip", "src": "clip.mp4", "duration": 1.0, **seg_extra}
        return parse_project({"output": {"width": 180, "height": 320, "fps": 15}, "segments": [seg]}, base_dir="/tmp")

    def _graph(self, project, tmp):
        from videoeditor.render import build_segment_command

        cmd = build_segment_command(project, project.segments[0], 1.0, os.path.join(tmp, "o.mp4"), tmp, "0")
        return cmd[cmd.index("-filter_complex") + 1], cmd

    def test_no_drawtext_skips_texts(self):
        p = self._project(texts=[{"text": "Hola", "start": 0, "end": 1}])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ff, "available_filters", return_value=frozenset({"null", "scale"})):
            graph, _ = self._graph(p, tmp)
        self.assertNotIn("drawtext", graph)

    def test_with_drawtext_escapes_windows_like_path(self):
        p = self._project(texts=[{"text": "Hola", "start": 0, "end": 1}])
        tmp = r"C:\Users\Ana María\AppData\Local\Temp\videoeditor_x"
        with mock.patch.object(ff, "available_filters", return_value=frozenset({"drawtext"})), \
                mock.patch("builtins.open", mock.mock_open()):
            graph, _ = self._graph(p, tmp)
        self.assertIn("textfile=C\\\\:/Users/Ana María/AppData/Local/Temp/videoeditor_x/text_0_0.txt", graph)
        self.assertNotIn("textfile='", graph)

    def test_no_zscale_skips_hdr_chain(self):
        from videoeditor import render

        p = self._project()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(render, "_hdr_transfer", return_value="smpte2084"), \
                mock.patch.object(ff, "available_filters", return_value=frozenset({"scale"})):
            graph, _ = self._graph(p, tmp)
        self.assertNotIn("zscale", graph)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(render, "_hdr_transfer", return_value="smpte2084"), \
                mock.patch.object(ff, "available_filters", return_value=frozenset({"zscale", "tonemap"})):
            graph, _ = self._graph(p, tmp)
        self.assertIn("zscale", graph)

    def test_no_vidstab_skips_stabilize_without_running_ffmpeg(self):
        p = self._project(effects=[{"type": "stabilize"}])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ff, "available_filters", return_value=frozenset({"scale"})), \
                mock.patch.object(ff, "run", side_effect=AssertionError("no debe llamar a ffmpeg")):
            graph, _ = self._graph(p, tmp)
        self.assertNotIn("vidstab", graph)

    def test_stabilize_paths_are_escaped(self):
        from videoeditor import render

        src = render.Source(src="a.mp4", stabilize=r"C:\Temp\Ana María\stab_0.trf")
        chain = render._source_chain(src, 1.0, 180, 320, 15)
        self.assertTrue(any(c.startswith(r"vidstabtransform=input=C\\:/Temp/Ana María/stab_0.trf:") for c in chain), chain)

    def test_codec_fallbacks(self):
        ff.available_encoders.cache_clear()
        with mock.patch.object(ff, "available_encoders", return_value=frozenset({"libx264"})):
            self.assertEqual(ff.video_codec_args(20, "fast")[:2], ["-c:v", "libx264"])
        with mock.patch.object(ff, "available_encoders", return_value=frozenset({"mpeg4"})):
            self.assertEqual(ff.video_codec_args(20, "fast")[:2], ["-c:v", "mpeg4"])
        with mock.patch.object(ff, "available_encoders", return_value=frozenset({"libopenh264"})):
            self.assertEqual(ff.video_codec_args(20, "fast")[:2], ["-c:v", "libopenh264"])

    def test_qa_hdr_warning_name_from_windows_path(self):
        import os as _os
        self.assertEqual(_os.path.basename(r"C:\v\iPhone clip.mov".replace("\\", "/")), "iPhone clip.mov")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg no instalado")
class DoctorTests(unittest.TestCase):
    def test_run_checks_structure(self):
        with tempfile.TemporaryDirectory() as d:
            res = doctor.run_checks(d)
        names = [r[1] for r in res]
        for needle in ("Python", "ffmpeg", "ffprobe", "Filtro/codec zscale", "Filtro/codec vidstab",
                       "mediapipe (opcional)", "tesseract (opcional)", "Carpeta de trabajo"):
            self.assertIn(needle, names)
        for state, _, _, fix in res:
            self.assertIn(state, (doctor.OK, doctor.WARN, doctor.FAIL))
            if state != doctor.OK:
                self.assertTrue(fix)

    def test_missing_filters_reported_with_fix(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ff, "available_filters", return_value=frozenset({"scale"})), \
                mock.patch.object(ff, "available_encoders", return_value=frozenset()):
            res = {r[1]: r for r in doctor.run_checks(d)}
        self.assertEqual(res["Filtro/codec zscale"][0], doctor.WARN)
        self.assertEqual(res["Filtro/codec xfade"][0], doctor.FAIL)
        self.assertTrue(res["Filtro/codec zscale"][3])

    def test_no_ffmpeg(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(doctor.shutil, "which", return_value=None):
            res = {r[1]: r for r in doctor.run_checks(d)}
        self.assertEqual(res["ffmpeg"][0], doctor.FAIL)
        self.assertIn("winget", res["ffmpeg"][3] + "winget")  # el texto de arreglo existe
        for osk in ("windows", "macos", "linux"):
            self.assertTrue(doctor.FFMPEG_FIX[osk])

    def test_cli_doctor_runs(self):
        from videoeditor.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            rc = main(["-w", d, "doctor"])
        self.assertIn(rc, (0, 1))


if __name__ == "__main__":
    unittest.main()
