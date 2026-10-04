"""调色预设 → FFmpeg 滤镜链。

所有预设都以"强度" k (0–1.5) 线性缩放各项调整量，k=0 等于原色。
锐化 (cas) 在输出分辨率上执行，放在链尾。

整条调色链都在 RGB 里完成，不用 eq 滤镜：eq 调饱和度时会把每个像素的色度
整体往绿色方向偏约 1 级，在接近黑色的头发上会明显发绿（实测 G-R 从 -2 变成 +1）。

暗部护色：拉黑位时三通道减去同样的值，颜色差不变、亮度却变低，黑头发里很淡的
色调会被放大好几倍。所以压暗之后，按"被压暗了多少"把暗部颜色收回到灰，
让暗部的颜色比例和原片一致（只变暗，不变色）。
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


# Rec.709 亮度权重：调饱和度时保持亮度不变
_LUMA = (0.2126, 0.7152, 0.0722)


def _saturation(sat: float) -> str:
    """RGB 空间的饱和度矩阵：每个像素按比例远离/靠近自己的灰度，不改变色相。"""
    rows = []
    for c in range(3):
        rows += [(1 - sat) * _LUMA[j] + (sat if j == c else 0) for j in range(3)]
    names = ["rr", "rg", "rb", "gr", "gg", "gb", "br", "bg", "bb"]
    return "colorchannelmixer=" + ":".join(f"{n}={v:.5f}" for n, v in zip(names, rows))


def _contrast(c: float) -> list[tuple[float, float]]:
    """以中灰为中心的对比度曲线点（三通道相同，不改变色相）。"""
    return [(0, 0), (0.25, max(0.0, 0.5 - 0.25 * c)), (0.75, min(1.0, 0.5 + 0.25 * c)), (1, 1)]


def _black_point(preset: str, k: float) -> tuple[float, float]:
    """各预设的黑位/白位（与 build_grade 里的 _levels 参数一致）。"""
    return {"clear": (0.055 * k, 1 - 0.035 * k), "vivid": (0.05 * k, 1 - 0.04 * k),
            "cinema": (0.04 * k, 1 - 0.02 * k)}.get(preset, (0.0, 1.0))


def build_grade_graph(preset: str, k: float, src: str, dst: str) -> str:
    """调色 + 暗部护色，返回从 [src] 到 [dst] 的滤镜图片段。"""
    k = max(0.0, min(1.5, float(k)))
    chain = build_grade(preset, k)
    if not chain:
        return f"[{src}]null[{dst}]"
    black, white = _black_point(preset, k)
    graded = f"[{src}]{','.join(chain)},format=gbrp"
    if black <= 0:
        return f"{graded}[{dst}]"
    # 压暗后的亮度 L 对应原亮度 L*(w-b)+b，压暗比例 r = L / (L*(w-b)+b)。
    # 把颜色向灰拉 (1-r)，暗部的"颜色/亮度"比例就回到原片水平
    span = white - black
    mask = (f"lut=c0='255*clip(1-(val/255)/((val/255)*{span:.4f}+{black:.4f}),0,1)'")
    return (f"{graded},split=3[{dst}_c][{dst}_g][{dst}_m];"
            f"[{dst}_g]{_saturation(0)}[{dst}_gray];"
            f"[{dst}_m]format=gray,{mask},format=gbrp[{dst}_mask];"
            f"[{dst}_c][{dst}_gray][{dst}_mask]maskedmerge[{dst}]")


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
            _saturation(1 + 0.16 * k),
            f"vibrance=intensity={_f(0.12 * k)}",
        ]
    if preset == "vivid":
        return [
            _levels(0.05 * k, 1 - 0.04 * k),
            _curve(_contrast(1 + 0.05 * k)),
            _saturation(1 + 0.28 * k),
            f"vibrance=intensity={_f(0.25 * k)}",
        ]
    if preset == "cinema":
        return [
            _levels(0.04 * k, 1 - 0.02 * k),
            _curve([(0, 0), (0.2, 0.2 - 0.04 * k), (0.55, 0.55), (0.85, 0.85 + 0.02 * k), (1, 1)]),
            # 冷阴影只加蓝、少减红，不动绿：黑头发不会被带成青绿色
            ("colorbalance="
             f"rs={_f(-0.01 * k)}:bs={_f(0.02 * k)}"
             f":rh={_f(0.05 * k)}:bh={_f(-0.05 * k)}"),
            _saturation(1 - 0.04 * k),
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
