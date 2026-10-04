#!/usr/bin/env python3
"""测试用的假 realesrgan-ncnn-vulkan：参数相同，用 ffmpeg 普通放大代替 AI。"""
import subprocess
import sys
import time
from pathlib import Path

a = sys.argv
src, dst = Path(a[a.index("-i") + 1]), Path(a[a.index("-o") + 1])
scale, fmt = int(a[a.index("-s") + 1]), a[a.index("-f") + 1]


def up(i: Path, o: Path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(i), "-vf", f"scale=iw*{scale}:ih*{scale}",
                    "-q:v", "1", str(o)], check=True)
    time.sleep(0.05)


if src.is_dir():
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.iterdir()):
        if p.is_symlink():  # 和真实组件一样跳过符号链接
            continue
        up(p, dst / f"{p.stem}.{fmt}")
else:
    up(src, dst)
