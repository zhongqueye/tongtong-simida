"""超分流水线：拆帧 → 分段 AI 超分 → 混合/调色/编码 → 拼接并带回音频。

按段处理（默认 48 帧一段），每段完成后立即删除该段的 2K 临时帧，
硬盘占用只有"原始 720p 帧 + 一段 2K 帧"；已完成的段会保留，中断后可续跑。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import grade
from .config import BITRATES, KNOWN_MODELS, NO_AI, TARGETS, JobSettings, ModelInfo, find_realesrgan
from .media import MediaError, VideoInfo, encoder_args, ffmpeg_bin, ffprobe_bin, probe, target_size

CHUNK_FRAMES = 48
# AI 输出用 JPEG（质量 100、4:4:4，与 PNG 的 PSNR 约 51dB，肉眼无差别）：
# PNG 压缩 2K 图很耗 CPU，换成 JPEG 能省下 AI 组件存图的时间
AI_FORMAT = "jpg"
PIPELINE_VERSION = 1


class Cancelled(Exception):
    pass


@dataclass
class Progress:
    stage: str            # extract / upscale / mux / done
    done: int = 0
    total: int = 0
    eta: float | None = None   # 秒
    message: str = ""


# ---------- 工具函数 ----------

def _run(cmd: list[str], cancel: threading.Event | None = None, what: str = "ffmpeg") -> None:
    """运行子进程，可取消；失败时抛出带日志尾部的错误。"""
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log)
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise Cancelled()
            time.sleep(0.2)
        if proc.returncode != 0:
            log.seek(0)
            tail = log.read().decode("utf-8", "replace").strip().splitlines()[-6:]
            raise MediaError(f"{what} 执行失败：\n" + "\n".join(tail))


def _matrix(info: VideoInfo) -> str:
    return "bt601" if info.color_space in ("bt470bg", "smpte170m") else "bt709"


def resolve_model(settings: JobSettings, binary: Path | None) -> ModelInfo:
    if settings.model == NO_AI.key or binary is None:
        return NO_AI
    return KNOWN_MODELS.get(settings.model) or ModelInfo(settings.model, settings.model, (4,))


def choose_scale(model: ModelInfo, src_short: int, target_short: int) -> int:
    for s in sorted(model.scales):
        if src_short * s >= target_short:
            return s
    return max(model.scales)


def output_name(src: Path, settings: JobSettings) -> str:
    tag = "2K" if settings.target == "2k" else "1080P"
    return f"{src.stem}_星枢{tag}.mp4"


def build_filter(settings: JobSettings, info: VideoInfo, size: tuple[int, int], use_ai: bool) -> str:
    """输入 0 = AI 超分帧，输入 1 = 原始帧（不用 AI 时只有输入 0 = 原始帧）。输出标签 [v]。"""
    w, h = size
    up = f"scale={w}:{h}:flags=lanczos,setsar=1"
    tail = grade.build_sharpen(settings.preset, settings.preset_strength)
    tail.append(f"scale=out_color_matrix={_matrix(info)}:out_range=tv,format=yuv420p")
    tail += grade.build_grain(settings.grain)  # 只加在亮度上，不产生彩色噪点
    graded = grade.build_grade_graph(settings.preset, settings.preset_strength, "pre", "graded")
    finish = f"{graded};[graded]{','.join(tail)}[v]"
    s = max(0.0, min(1.0, float(settings.strength)))
    if not use_ai or s >= 0.999:
        return f"[0:v]{up}[pre];{finish}"
    return (f"[0:v]{up},format=gbrp[ai];[1:v]{up},format=gbrp[base];"
            f"[ai][base]blend=all_mode=normal:all_opacity={s:.3f}[pre];{finish}")


class Upscaler:
    def __init__(self, binary: Path, model: ModelInfo, scale: int, tile: int = 0, fmt: str = AI_FORMAT):
        self.binary, self.model, self.scale, self.tile, self.fmt = binary, model, scale, tile, fmt

    def _cmd(self, src: Path, dst: Path, fmt: str | None = None) -> list[str]:
        return [str(self.binary), "-i", str(src), "-o", str(dst),
                "-n", self.model.key, "-s", str(self.scale),
                "-m", str(self.model.models_dir(self.binary)),
                "-t", str(self.tile), "-f", fmt or self.fmt]

    def run_dir(self, src: Path, dst: Path, on_tick: Callable[[int], None] | None = None,
                cancel: threading.Event | None = None) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(self._cmd(src, dst), stdout=subprocess.DEVNULL, stderr=log,
                                    cwd=str(self.binary.parent))
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    proc.terminate()
                    try:
                        proc.wait(5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise Cancelled()
                if on_tick:
                    on_tick(sum(1 for _ in dst.glob(f"*.{self.fmt}")))
                time.sleep(0.4)
            if proc.returncode != 0:
                log.seek(0)
                tail = log.read().decode("utf-8", "replace").strip().splitlines()[-6:]
                raise MediaError("AI 超分失败（可尝试把分块大小调小）：\n" + "\n".join(tail))

    def run_file(self, src: Path, dst: Path) -> None:
        _run(self._cmd(src, dst, dst.suffix.lstrip(".")), what="AI 超分")


# ---------- 主流程 ----------

class Pipeline:
    def __init__(self, src: str | Path, output: str | Path, settings: JobSettings, workdir: str | Path,
                 on_progress: Callable[[Progress], None] | None = None,
                 cancel: threading.Event | None = None, binary: Path | None = None,
                 chunk_frames: int = CHUNK_FRAMES):
        self.src = Path(src)
        self.output = Path(output)
        self.settings = settings
        self.work = Path(workdir)
        self.on_progress = on_progress or (lambda p: None)
        self.cancel = cancel or threading.Event()
        self.binary = binary if binary is not None else find_realesrgan()
        self.chunk_frames = chunk_frames

    # 设置或源文件变了，旧的中间结果就作废
    def _signature(self, info: VideoInfo) -> str:
        st = self.src.stat()
        payload = json.dumps({
            "v": PIPELINE_VERSION, "src": str(self.src), "size": st.st_size, "mtime": st.st_mtime,
            "settings": self.settings.to_dict(), "frames": info.frames,
            "chunk": self.chunk_frames,
        }, sort_keys=True)
        return hashlib.sha1(payload.encode()).hexdigest()

    def _prepare_workdir(self, info: VideoInfo) -> None:
        sig = self._signature(info)
        meta = self.work / "meta.json"
        if meta.exists():
            try:
                if json.loads(meta.read_text()).get("sig") == sig:
                    return
            except (ValueError, OSError):
                pass
            shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"sig": sig}))

    def _extract(self, info: VideoInfo) -> int:
        frames = self.work / "frames"
        done = frames / ".done"
        if not done.exists():
            shutil.rmtree(frames, ignore_errors=True)
            frames.mkdir(parents=True)
            self.on_progress(Progress("extract", 0, info.frames, message="正在拆帧"))
            _run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(self.src), "-map", "0:v:0",
                  "-fps_mode", "passthrough",
                  "-vf", f"scale=in_color_matrix={_matrix(info)}:in_range=tv,format=rgb24",
                  "-compression_level", "1",  # 临时帧，用最快的压缩
                  "-start_number", "0", str(frames / "%06d.png")], self.cancel, "拆帧")
            done.write_text(str(sum(1 for _ in frames.glob("*.png"))))
        return int(done.read_text())

    def _encode_chunk(self, info, size, use_ai, start, count, ai_dir, out: Path, bitrate) -> None:
        fps = info.fps_str
        frames = self.work / "frames"
        inputs: list[str] = []
        if use_ai:
            inputs += ["-framerate", fps, "-start_number", str(start), "-i", str(ai_dir / f"%06d.{AI_FORMAT}")]
        inputs += ["-framerate", fps, "-start_number", str(start), "-i", str(frames / "%06d.png")]
        gop = max(1, round(float(info.fps) * 2))
        venc, _ = encoder_args(bitrate, self.settings.codec, gop)
        tmp = out.with_suffix(".tmp.mp4")
        _run([ffmpeg_bin(), "-v", "error", "-y", *inputs,
              "-filter_complex", build_filter(self.settings, info, size, use_ai),
              "-map", "[v]", "-frames:v", str(count), *venc, *_color_args(info),
              "-an", str(tmp)], None, "编码")  # 暂停时让这一段编完（几秒），AI 结果不浪费
        os.replace(tmp, out)

    def _mux(self, info: VideoInfo, chunks: list[Path]) -> None:
        listfile = self.work / "concat.txt"
        listfile.write_text("".join(f"file '{c.as_posix()}'\n" for c in chunks))
        self.output.parent.mkdir(parents=True, exist_ok=True)
        part = self.output.with_name(self.output.stem + ".part.mp4")
        base = [ffmpeg_bin(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile)]
        if info.has_audio:
            base += ["-i", str(self.src), "-map", "0:v", "-map", "1:a:0"]
        else:
            base += ["-map", "0:v"]
        codec = video_codec(chunks[0])
        # 把色彩信息同时写进码流和 MP4 容器，避免平台转码时按默认值猜测而偏色
        prim, trc, mat = (1, 1, 1) if _matrix(info) == "bt709" else (6, 6, 6)
        bsf = {"hevc": "hevc_metadata", "h264": "h264_metadata"}.get(codec)
        tail = ["-c:v", "copy"]
        if bsf:
            tail += ["-bsf:v", f"{bsf}=colour_primaries={prim}:transfer_characteristics={trc}"
                               f":matrix_coefficients={mat}:video_full_range_flag=0"]
        if codec == "hevc":
            tail += ["-tag:v", "hvc1"]
        tail += [*_color_args(info), "-movflags", "+faststart+write_colr", str(part)]
        # 平台和剪辑软件最常用 44.1/48kHz；Seedance 原片常是 32kHz，统一转成 48kHz AAC
        reencode = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        audio = ["-c:a", "copy"] if (info.audio_codec == "aac" and info.sample_rate in (44100, 48000)) else reencode
        try:
            _run(base + (audio if info.has_audio else []) + tail, self.cancel, "合成")
        except MediaError:
            if not info.has_audio:
                raise
            _run(base + reencode + tail, self.cancel, "合成")
        os.replace(part, self.output)

    def run(self) -> Path:
        info = probe(self.src)
        model = resolve_model(self.settings, self.binary)
        use_ai = model is not NO_AI
        target_short = TARGETS[self.settings.target]
        size = target_size(info, target_short)
        bitrate = BITRATES.get(self.settings.quality, BITRATES["standard"])[target_short]
        scale = choose_scale(model, min(info.width, info.height), target_short) if use_ai else 1
        upscaler = Upscaler(self.binary, model, scale, self.settings.tile) if use_ai else None

        self._prepare_workdir(info)
        total = self._extract(info)
        if total == 0:
            raise MediaError("没有解出任何帧")
        frames = self.work / "frames"
        chunk_dir = self.work / "chunks"
        chunk_dir.mkdir(exist_ok=True)

        starts = list(range(0, total, self.chunk_frames))
        chunks = [chunk_dir / f"c{i:05d}.mp4" for i in range(len(starts))]
        done_frames = sum(min(self.chunk_frames, total - s) for s, c in zip(starts, chunks) if c.exists())
        t0, processed = time.monotonic(), 0
        last = {"cur": 0.0, "t": t0}  # 上一次帧数变化的时刻，用来算稳定的剩余时间

        def report(extra: float = 0) -> None:
            now = time.monotonic()
            cur = processed + extra
            if cur > last["cur"]:
                last["cur"], last["t"] = cur, now
            eta, label = None, (f"{model.label} ×{scale}" if use_ai else "传统放大")
            # 前 8 帧包含加载模型等开销，速度和剩余时间都不准，先显示"估算中"
            if last["cur"] >= min(8, total) and last["t"] - t0 > 3:
                per_frame = (last["t"] - t0) / last["cur"]
                remaining = total - done_frames - last["cur"]
                eta = max(0.0, remaining * per_frame - (now - last["t"]))
                label += f" · {per_frame:.2f} 秒/帧"
            self.on_progress(Progress("upscale", int(done_frames + cur), total, eta, label))

        report()
        for d in list(self.work.glob("cin*")) + list(self.work.glob("ai*")):
            shutil.rmtree(d, ignore_errors=True)  # 上次中断留下的半成品

        # GPU 超分第 N+1 段的同时，CPU 在后台编码第 N 段
        encoder = ThreadPoolExecutor(max_workers=1)
        pending = None  # (future, 要清理的目录)

        def finish_pending() -> None:
            nonlocal pending
            if pending is None:
                return
            fut, dirs = pending
            pending = None
            try:
                fut.result()
            finally:
                for d in dirs:
                    shutil.rmtree(d, ignore_errors=True)

        try:
            for i, (start, chunk) in enumerate(zip(starts, chunks)):
                if self.cancel.is_set():
                    raise Cancelled()
                if chunk.exists():
                    continue
                count = min(self.chunk_frames, total - start)
                dirs: list[Path] = []
                ai_dir = self.work / f"ai{i}"
                if use_ai:
                    cin = self.work / f"cin{i}"
                    cin.mkdir()
                    dirs = [cin, ai_dir]
                    for n in range(start, start + count):
                        name = f"{n:06d}.png"
                        try:  # 硬链接：不占额外空间；realesrgan 会跳过符号链接
                            os.link(frames / name, cin / name)
                        except OSError:
                            shutil.copy2(frames / name, cin / name)
                    upscaler.run_dir(cin, ai_dir, on_tick=lambda n: report(min(n, count) * 0.95),
                                     cancel=self.cancel)
                    got = sum(1 for _ in ai_dir.glob(f"*.{AI_FORMAT}"))
                    if got != count:
                        raise MediaError(f"AI 超分输出帧数不对（{got}/{count}）")
                finish_pending()  # 同一时间最多一段在编码
                fut = encoder.submit(self._encode_chunk, info, size, use_ai, start, count,
                                     ai_dir, chunk, bitrate)
                pending = (fut, dirs)
                processed += count
                report()
            finish_pending()
        except BaseException:
            # 出错或暂停：等后台那一段编完再退出，续跑时可以直接复用
            if pending is not None:
                try:
                    finish_pending()
                except BaseException:  # noqa: BLE001 —— 保留最初的异常
                    pass
            raise
        finally:
            encoder.shutdown(wait=True)

        self.on_progress(Progress("mux", total, total, 0, "正在合成音视频"))
        self._mux(info, chunks)
        shutil.rmtree(self.work, ignore_errors=True)
        self.on_progress(Progress("done", total, total, 0, "完成"))
        return self.output


def video_codec(path: Path) -> str:
    res = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    return res.stdout.strip()


def _color_args(info: VideoInfo) -> list[str]:
    bt709 = _matrix(info) == "bt709"
    return ["-colorspace", "bt709" if bt709 else "smpte170m",
            "-color_primaries", "bt709" if bt709 else "smpte170m",
            "-color_trc", "bt709" if bt709 else "smpte170m", "-color_range", "tv"]


# ---------- 单帧预览 ----------

def render_preview(src: str | Path, settings: JobSettings, at: float | None = None,
                   binary: Path | None = None) -> dict:
    """渲染一帧前后对比。before = 传统放大原画，after = 实际成片效果。返回 JPEG data URL。"""
    binary = binary if binary is not None else find_realesrgan()
    info = probe(src)
    model = resolve_model(settings, binary)
    use_ai = model is not NO_AI
    target_short = TARGETS[settings.target]
    size = target_size(info, target_short)
    t = info.duration / 2 if at is None else max(0.0, min(at, max(info.duration - 0.05, 0)))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="stellar_preview_") as tmp:
        tmp = Path(tmp)
        frame = tmp / "000000.png"
        _run([ffmpeg_bin(), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
              "-vf", f"scale=in_color_matrix={_matrix(info)}:in_range=tv,format=rgb24", str(frame)],
             what="取帧")
        inputs = []
        if use_ai:
            ai = tmp / "ai.png"
            scale = choose_scale(model, min(info.width, info.height), target_short)
            Upscaler(binary, model, scale, settings.tile).run_file(frame, ai)
            inputs += ["-i", str(ai)]
        inputs += ["-i", str(frame)]
        after, before = tmp / "after.jpg", tmp / "before.jpg"
        flt = build_filter(settings, info, size, use_ai)
        _run([ffmpeg_bin(), "-v", "error", "-y", *inputs, "-filter_complex", flt,
              "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(after)], what="预览")
        _run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(frame),
              "-vf", f"scale={size[0]}:{size[1]}:flags=lanczos", "-q:v", "2", str(before)], what="预览")
        enc = lambda p: "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()
        return {"before": enc(before), "after": enc(after), "width": size[0], "height": size[1],
                "at": round(t, 2), "seconds": round(time.monotonic() - started, 1),
                "model": model.label}
