"""命令行入口：python -m stellar_upscale 视频... [选项]

不带参数运行时打开图形界面。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import threading
from pathlib import Path

from . import APP_NAME, grade
from .config import STYLES, TARGETS, JobSettings, data_dir
from .pipeline import Cancelled, Pipeline, Progress, output_name


def _fmt_eta(sec):
    if sec is None:
        return "--:--"
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from .app import main as gui_main
        return gui_main()

    ap = argparse.ArgumentParser(prog="stellar_upscale", description=f"{APP_NAME} 命令行")
    ap.add_argument("inputs", nargs="+", help="视频文件")
    ap.add_argument("-o", "--output-dir", default="", help="输出目录（默认与原视频同目录）")
    ap.add_argument("--style", choices=list(STYLES), default="real",
                    help="画风：real（仿真人）/ cg3d（国漫 3D）/ anime2d（2D 动漫），决定下面几项的默认值")
    ap.add_argument("-m", "--model", help="realesr-animevideov3（快速）/ realesr-general-x4v3（写实）"
                                          " / realesrgan-x4plus（精细）/ lanczos（无 AI）")
    ap.add_argument("-s", "--strength", type=float, help="AI 细节强度 0–1")
    ap.add_argument("-t", "--target", choices=list(TARGETS), default="2k")
    ap.add_argument("-p", "--preset", choices=list(grade.PRESETS))
    ap.add_argument("--preset-strength", type=float, help="调色强度 0–1.5")
    ap.add_argument("-g", "--grain", type=float, help="胶片颗粒 0–1")
    ap.add_argument("-q", "--quality", choices=["standard", "high"], default="standard")
    ap.add_argument("-c", "--codec", choices=["hevc", "h264"], default="hevc",
                    help="hevc（体积小）/ h264（兼容性最好）")
    ap.add_argument("--tile", type=int, default=0, help="AI 分块大小，0=自动；内存不足时设 256")
    args = ap.parse_args(argv)

    style = dict(STYLES[args.style]["settings"])
    overrides = {k: getattr(args, k) for k in ("model", "strength", "preset", "preset_strength", "grain")
                 if getattr(args, k) is not None}
    settings = JobSettings(style="custom" if overrides else args.style, **{**style, **overrides},
                           target=args.target, quality=args.quality, codec=args.codec,
                           output_dir=args.output_dir, tile=args.tile)
    cancel = threading.Event()
    rc = 0
    for src in args.inputs:
        src = Path(src).expanduser().resolve()
        out_dir = Path(settings.output_dir).expanduser() if settings.output_dir else src.parent
        out = out_dir / output_name(src, settings)
        work = data_dir() / "work" / ("cli_" + hashlib.sha1(str(src).encode()).hexdigest()[:12])

        def show(p: Progress, name=src.name):
            pct = p.done / p.total * 100 if p.total else 0
            sys.stdout.write(f"\r{name}  {p.message:<14} {p.done}/{p.total} {pct:5.1f}%  剩余 {_fmt_eta(p.eta)}  ")
            sys.stdout.flush()

        try:
            Pipeline(src, out, settings, work, on_progress=show, cancel=cancel).run()
            print(f"\n✓ {out}")
        except KeyboardInterrupt:
            cancel.set()
            print("\n已中断，下次运行同样的命令会从断点继续")
            return 130
        except Cancelled:
            return 130
        except Exception as e:  # noqa: BLE001
            print(f"\n✗ {src.name}：{e}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
