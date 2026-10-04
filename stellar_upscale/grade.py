"""调色预设 → FFmpeg 滤镜链。

所有预设都以"强度" k (0–1.5) 线性缩放各项调整量，k=0 等于原色。
锐化 (cas) 在输出分辨率上执行，放在链尾。
"""

from __future__ import annotations

PRESETS = {
    "original": {"label": "原色", "desc": "只超分，不改颜色"},
    "clear": {"label": "通透", "desc": "去灰雾、提对比与饱和、轻锐化，适合大多数漫剧"},
    "vivid": {"label": "鲜艳", "desc": "色彩更饱满，适合明亮场景与二次元"},
    "cinema": {"label": "电影感", "desc": "暗部更沉、暖高光冷阴影，适合剧情向"},
}


def _f(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


def _levels(black: float, white: float) -> str:
    """把 [black, white] 拉伸到 [0, 1]，去掉 AI 视频常见的"灰蒙蒙"。"""
    b, w = _f(black), _f(white)
    return (f"colorlevels=rimin={b}:gimin={b}:bimin={b}"
            f":rimax={w}:gimax={w}:bimax={w}")


def _curve(points: list[tuple[float, float]]) -> str:
    return "curves=all='" + " ".join(f"{_f(x)}/{_f(y)}" for x, y in points) + "'"


def build_grade(preset: str, k: float = 1.0) -> list[str]:
    """返回调色滤镜列表（不含锐化）。"""
    k = max(0.0, min(1.5, float(k)))
    if preset == "original" or k == 0:
        return []
    if preset == "clear":
        return [
            _levels(0.055 * k, 1 - 0.035 * k),
            # 轻 S 曲线：暗部柔和下压，避免头发死黑
            _curve([(0, 0), (0.25, 0.25 - 0.02 * k), (0.75, 0.75 + 0.015 * k), (1, 1)]),
            f"eq=saturation={_f(1 + 0.16 * k)}",
            f"vibrance=intensity={_f(0.12 * k)}",
        ]
    if preset == "vivid":
        return [
            _levels(0.05 * k, 1 - 0.04 * k),
            f"eq=contrast={_f(1 + 0.05 * k)}:saturation={_f(1 + 0.28 * k)}",
            f"vibrance=intensity={_f(0.25 * k)}",
        ]
    if preset == "cinema":
        return [
            _levels(0.04 * k, 1 - 0.02 * k),
            _curve([(0, 0), (0.2, 0.2 - 0.04 * k), (0.55, 0.55), (0.85, 0.85 + 0.02 * k), (1, 1)]),
            ("colorbalance="
             f"rs={_f(-0.03 * k)}:bs={_f(0.04 * k)}"
             f":rh={_f(0.05 * k)}:bh={_f(-0.05 * k)}"),
            f"eq=saturation={_f(1 - 0.04 * k)}",
        ]
    raise ValueError(f"未知预设：{preset}")


def build_sharpen(preset: str, k: float = 1.0) -> list[str]:
    k = max(0.0, min(1.5, float(k)))
    amount = {"original": 0.0, "clear": 0.45, "vivid": 0.35, "cinema": 0.25}.get(preset, 0.0) * k
    return [f"cas=strength={_f(min(amount, 1.0))}"] if amount > 0 else []


def build_grain(amount: float) -> list[str]:
    """细微的动态颗粒：掩盖放大后的"塑料感"，让皮肤和暗部更像实拍。"""
    a = max(0.0, min(1.0, float(amount)))
    strength = round(a * 6)
    return [f"noise=c0s={strength}:c0f=t"] if strength > 0 else []
