"""端到端测试（"无 AI"模式，只需要 ffmpeg）：python3 -m unittest discover tests"""

import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("XDG_DATA_HOME", tempfile.mkdtemp(prefix="stellar_test_data_"))

from stellar_upscale import grade  # noqa: E402
from stellar_upscale.config import JobSettings  # noqa: E402
from stellar_upscale.media import probe, target_size  # noqa: E402
from stellar_upscale.pipeline import Cancelled, Pipeline, render_preview  # noqa: E402


def make_clip(path: Path, w=320, h=180, seconds=1, fps=24, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r={fps}:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}:sample_rate=32000", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)


def count_frames(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return int(out.stdout.strip())


NO_AI = dict(model="lanczos")
FAKE_AI = Path(__file__).with_name("fake_realesrgan.py")


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stellar_test_"))
        self.src = self.tmp / "clip.mp4"
        make_clip(self.src)

    def test_end_to_end_1080p(self):
        out = self.tmp / "out.mp4"
        Pipeline(self.src, out, JobSettings(target="1080p", **NO_AI), self.tmp / "work",
                 binary=None, chunk_frames=10).run()
        info = probe(out)
        self.assertEqual((info.width, info.height), (1920, 1080))
        self.assertEqual(count_frames(out), 24)
        self.assertTrue(info.has_audio)
        self.assertFalse((self.tmp / "work").exists(), "临时目录应被清理")

    def test_cancel_then_resume(self):
        out = self.tmp / "out.mp4"
        work = self.tmp / "work"
        cancel = threading.Event()

        def stop_after_first_chunk(p):
            if p.stage == "upscale" and p.done >= 8:
                cancel.set()

        with self.assertRaises(Cancelled):
            Pipeline(self.src, out, JobSettings(**NO_AI), work, on_progress=stop_after_first_chunk,
                     cancel=cancel, binary=None, chunk_frames=8).run()
        done_chunks = sorted((work / "chunks").glob("c*.mp4"))
        self.assertEqual(len(done_chunks), 1)
        mtime = done_chunks[0].stat().st_mtime

        Pipeline(self.src, out, JobSettings(**NO_AI), work, binary=None, chunk_frames=8).run()
        self.assertEqual(count_frames(out), 24)
        self.assertEqual(probe(out).height, 1440)
        self.assertLess(mtime, out.stat().st_mtime)

    def test_settings_change_invalidates_work(self):
        work = self.tmp / "work"
        cancel = threading.Event()
        with self.assertRaises(Cancelled):
            Pipeline(self.src, self.tmp / "a.mp4", JobSettings(**NO_AI), work,
                     on_progress=lambda p: p.done >= 8 and cancel.set(), cancel=cancel,
                     binary=None, chunk_frames=8).run()
        out = self.tmp / "b.mp4"
        Pipeline(self.src, out, JobSettings(preset="cinema", **NO_AI), work, binary=None, chunk_frames=8).run()
        self.assertEqual(count_frames(out), 24)

    def test_h264_and_platform_compat(self):
        out = self.tmp / "out.mp4"
        Pipeline(self.src, out, JobSettings(target="1080p", codec="h264", **NO_AI), self.tmp / "w",
                 binary=None, chunk_frames=10).run()
        res = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=codec_name,color_primaries,color_transfer,color_space,sample_rate",
                              "-of", "json", str(out)], capture_output=True, text=True, check=True)
        import json
        v, a = json.loads(res.stdout)["streams"]
        self.assertEqual(v["codec_name"], "h264")
        self.assertEqual((v["color_primaries"], v["color_transfer"], v["color_space"]),
                         ("bt709", "bt709", "bt709"))
        self.assertEqual(a["sample_rate"], "48000")
        self.assertEqual(count_frames(out), 24)

    def test_ai_path_overlapped_chunks_and_resume(self):
        """走 AI 分支（假组件）：多段并行编码、混合强度、取消后续跑。"""
        out, work = self.tmp / "ai.mp4", self.tmp / "work"
        cancel = threading.Event()
        settings = JobSettings(model="realesr-animevideov3", strength=0.4, target="1080p")
        with self.assertRaises(Cancelled):
            Pipeline(self.src, out, settings, work, binary=FAKE_AI, chunk_frames=5, cancel=cancel,
                     on_progress=lambda p: p.done >= 10 and cancel.set()).run()
        self.assertGreaterEqual(len(list((work / "chunks").glob("c*.mp4"))), 1)
        messages = []
        Pipeline(self.src, out, settings, work, binary=FAKE_AI, chunk_frames=5,
                 on_progress=lambda p: messages.append(p.message)).run()
        info = probe(out)
        self.assertEqual((info.width, info.height), (1920, 1080))
        self.assertEqual(count_frames(out), 24)
        self.assertFalse(work.exists())

    def test_no_audio(self):
        src = self.tmp / "silent.mp4"
        make_clip(src, audio=False)
        out = self.tmp / "out.mp4"
        Pipeline(src, out, JobSettings(target="1080p", **NO_AI), self.tmp / "w", binary=None).run()
        self.assertFalse(probe(out).has_audio)

    def test_preview(self):
        res = render_preview(self.src, JobSettings(target="1080p", **NO_AI), at=0.5, binary=None)
        self.assertTrue(res["before"].startswith("data:image/jpeg;base64,"))
        self.assertEqual((res["width"], res["height"]), (1920, 1080))


class GradeTest(unittest.TestCase):
    def test_all_presets_are_valid_ffmpeg(self):
        for preset in grade.PRESETS:
            for k in (0, 0.5, 1.5):
                chain = grade.build_grade(preset, k) + grade.build_sharpen(preset, k) + grade.build_grain(0.5)
                vf = ",".join(chain + ["format=yuv420p"])
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x36:d=0.1",
                                "-vf", vf, "-f", "null", "-"], check=True)

    def test_target_size(self):
        class Info:
            width, height = 720, 1280
        self.assertEqual(target_size(Info, 1440), (1440, 2560))
        Info.width, Info.height = 1280, 720
        self.assertEqual(target_size(Info, 1080), (1920, 1080))


if __name__ == "__main__":
    unittest.main()
