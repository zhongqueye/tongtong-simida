"""调色预设 → FFmpeg 滤镜链。

所有预设都以"强度" k (0–1.5) 线性缩放各项调整量，k=0 等于原色。
锐化 (cas) 在输出分辨率上执行，放在链尾。

整条调色链都在 RGB 里完成，不用 eq 滤镜：eq 调饱和度时会把每个像素的色度
整体往绿色方向偏约 1 级，在接近黑色的头发上会明显发绿（实测 G-R 从 -2 变成 +1）。

不拉黑位：旧版把黑位提到 0.055，黑头发从亮度 24 压到 8，约 27% 的发丝像素被压成纯黑，
发丝层次没了，同时暗部很淡的色调被放大成明显偏色。现在对比度只在中间调里加。

暗部护色：饱和度在暗部逐渐退出，黑头发保持原片的颜色比例。
"""

from __future__ import annotations

PRESETS = {
    "original": {"label": "原色", "desc": "只超分，不改颜色"},
    "clear": {"label": "通透", "desc": "中间调去灰雾、提饱和、轻锐化，暗部层次保留"},
    "vivid": {"label": "鲜艳", "desc": "色彩更饱满，适合明亮场景与二次元"},
    "cinema": {"label": "电影感", "desc": "暗部略沉、暖高光冷阴影，适合剧情向"},
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


# 暗部护色的亮度范围：低于 T0 完全不加饱和，高于 T1 正常加饱和，中间平滑过渡
_SHADOW_T0, _SHADOW_T1 = 0.06, 0.30


def _tone(preset: str, k: float) -> list[str]:
    """明暗/冷暖：作用于全画面。不拉黑位，暗部曲线斜率保持约 1，发丝等暗部层次不被压掉。"""
    if preset == "clear":
        return [
            _levels(0.0, 1 - 0.03 * k),  # 只提白位：高光更干净
            # 中间调 S 曲线：0.12 以下不动，中间调拉开，"去灰雾"主要靠这里
            _curve([(0, 0), (0.12, 0.12), (0.4, 0.4 - 0.015 * k), (0.7, 0.7 + 0.03 * k), (1, 1)]),
        ]
    if preset == "vivid":
        return [
            _levels(0.0, 1 - 0.04 * k),
            _curve([(0, 0), (0.12, 0.12), (0.4, 0.4 - 0.02 * k), (0.7, 0.7 + 0.035 * k), (1, 1)]),
        ]
    if preset == "cinema":
        return [
            # 暗部只轻微下沉（0.15 处约 -5 级），保留层次
            _curve([(0, 0), (0.15, 0.15 - 0.02 * k), (0.55, 0.55), (0.85, 0.85 + 0.02 * k), (1, 1)]),
            # 冷阴影只加蓝、少减红，不动绿：黑头发不会被带成青绿色
            ("colorbalance="
             f"rs={_f(-0.006 * k)}:bs={_f(0.012 * k)}"
             f":rh={_f(0.05 * k)}:bh={_f(-0.05 * k)}"),
        ]
    raise ValueError(f"未知预设：{preset}")


def _color(preset: str, k: float) -> list[str]:
    """饱和度：只作用于中间调和亮部（见 build_grade_graph 的暗部护色）。"""
    if preset == "clear":
        return [_saturation(1 + 0.16 * k), f"vibrance=intensity={_f(0.12 * k)}"]
    if preset == "vivid":
        return [_saturation(1 + 0.28 * k), f"vibrance=intensity={_f(0.25 * k)}"]
    if preset == "cinema":
        return [_saturation(1 - 0.04 * k)]
    raise ValueError(f"未知预设：{preset}")


def build_grade(preset: str, k: float = 1.0) -> list[str]:
    """不带暗部护色的线性调色链（仅供参考/测试；实际处理用 build_grade_graph）。"""
    k = max(0.0, min(1.5, float(k)))
    if preset == "original" or k == 0:
        return []
    return _tone(preset, k) + _color(preset, k)


def build_grade_graph(preset: str, k: float, src: str, dst: str) -> str:
    """调色 + 暗部护色，返回从 [src] 到 [dst] 的滤镜图片段。

    饱和度在暗部逐渐退出：黑头发保持原片的颜色比例（不偏绿、不偏紫），
    皮肤、天空、衣服照常变得更饱满。
    """
    k = max(0.0, min(1.5, float(k)))
    if preset == "original" or k == 0:
        return f"[{src}]null[{dst}]"
    if preset not in PRESETS:
        raise ValueError(f"未知预设：{preset}")
    # 16 位精度：colorchannelmixer 在 8 位下逐项取整，纯灰 (48,48,48) 会变成 (48,46,48)，
    # 暗部出现 ±2 级的色噪；16 位下误差可忽略，渐变也更平滑
    head = f"[{src}]{','.join(['format=gbrp16le'] + _tone(preset, k))}"
    t0, t1 = _SHADOW_T0, _SHADOW_T1
    mask = f"lut=c0='maxval*clip(({t1}-val/maxval)/{t1 - t0:.4f},0,1)'"
    return (f"{head},split=3[{dst}_t][{dst}_c][{dst}_m];"
            f"[{dst}_c]{','.join(_color(preset, k))}[{dst}_col];"
            # 用 Rec.709 亮度做遮罩（format=gray 会用 BT.601 权重）
            f"[{dst}_m]{_saturation(0)},extractplanes=g,{mask},format=gbrp16le[{dst}_mask];"
            f"[{dst}_col][{dst}_t][{dst}_mask]maskedmerge[{dst}]")


def build_sharpen(preset: str, k: float = 1.0) -> list[str]:
    k = max(0.0, min(1.5, float(k)))
    amount = {"original": 0.0, "clear": 0.45, "vivid": 0.35, "cinema": 0.25}.get(preset, 0.0) * k
    return [f"cas=strength={_f(min(amount, 1.0))}"] if amount > 0 else []


def build_grain(amount: float) -> list[str]:
    """细微的动态颗粒：掩盖放大后的"塑料感"，让皮肤和暗部更像实拍。"""
    a = max(0.0, min(1.0, float(amount)))
    strength = round(a * 6)
    return [f"noise=c0s={strength}:c0f=t"] if strength > 0 else []
