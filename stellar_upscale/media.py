"""FFmpeg / FFprobe 相关工具。"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path


class MediaError(RuntimeError):
    pass


# 双击启动时 PATH 里可能没有 Homebrew 目录
_EXTRA_DIRS = ["/opt/homebrew/bin", "/usr/local/bin"]


def _which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_DIRS:
        cand = Path(d) / name
        if cand.is_file():
            return str(cand)
    return None


def ffmpeg_bin() -> str:
    path = _which("ffmpeg")
    if not path:
        raise MediaError("未找到 ffmpeg，请先运行 setup.sh（或 brew install ffmpeg）")
    return path


def ffprobe_bin() -> str:
    path = _which("ffprobe")
    if not path:
        raise MediaError("未找到 ffprobe，请先运行 setup.sh（或 brew install ffmpeg）")
    return path


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: Fraction
    frames: int
    duration: float
    has_audio: bool
    bitrate: int  # bps
    color_space: str = ""
    audio_codec: str = ""

    @property
    def fps_str(self) -> str:
        return f"{self.fps.numerator}/{self.fps.denominator}"

    def to_dict(self) -> dict:
        return {
            "width": self.width, "height": self.height,
            "fps": round(float(self.fps), 3), "frames": self.frames,
            "duration": round(self.duration, 2), "has_audio": self.has_audio,
            "bitrate": self.bitrate,
        }


def probe(path: str | Path) -> VideoInfo:
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise MediaError(f"无法读取视频：{res.stderr.strip() or path}")
    data = json.loads(res.stdout)
    vstreams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not vstreams:
        raise MediaError("文件中没有视频轨")
    v = vstreams[0]
    fps = Fraction(v.get("avg_frame_rate") or "0/1")
    if fps <= 0:
        fps = Fraction(v.get("r_frame_rate") or "24/1")
    duration = float(v.get("duration") or data.get("format", {}).get("duration") or 0)
    frames = int(v.get("nb_frames") or 0)
    if frames <= 0:
        frames = _count_frames(path)
    # 旋转元数据（手机竖拍）
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"])) % 180
    w, h = int(v["width"]), int(v["height"])
    if rotation == 90:
        w, h = h, w
    astreams = [s for s in data["streams"] if s.get("codec_type") == "audio"]
    return VideoInfo(
        path=str(path), width=w, height=h, fps=fps, frames=frames,
        duration=duration, has_audio=bool(astreams),
        bitrate=int(data.get("format", {}).get("bit_rate") or 0),
        color_space=v.get("color_space", ""),
        audio_codec=astreams[0].get("codec_name", "") if astreams else "",
    )


def _count_frames(path) -> int:
    cmd = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_packets",
           "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return int(res.stdout.strip().split(",")[0])
    except ValueError:
        raise MediaError("无法统计视频帧数")


@lru_cache(maxsize=1)
def encoders() -> frozenset:
    res = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"],
                         capture_output=True, text=True)
    names = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            names.add(parts[1])
    return frozenset(names)


def encoder_args(bitrate_mbps: int) -> tuple[list[str], str]:
    """返回 (编码参数, 编码器说明)。优先 Apple 硬件 HEVC。"""
    enc = encoders()
    b = f"{bitrate_mbps}M"
    if "hevc_videotoolbox" in enc:
        return (["-c:v", "hevc_videotoolbox", "-b:v", b, "-maxrate", f"{int(bitrate_mbps * 1.5)}M",
                 "-tag:v", "hvc1", "-profile:v", "main"], "HEVC（Apple 硬件）")
    if "libx265" in enc:
        return (["-c:v", "libx265", "-preset", "medium", "-b:v", b, "-tag:v", "hvc1",
                 "-x265-params", "log-level=error"], "HEVC（软件）")
    return (["-c:v", "libx264", "-preset", "medium", "-b:v", b], "H.264（软件）")


def target_size(info: VideoInfo, short_side: int) -> tuple[int, int]:
    """按短边计算输出尺寸，保持比例，宽高取偶数。"""
    if info.width >= info.height:
        h = short_side
        w = round(info.width * short_side / info.height / 2) * 2
    else:
        w = short_side
        h = round(info.height * short_side / info.width / 2) * 2
    return w, h
