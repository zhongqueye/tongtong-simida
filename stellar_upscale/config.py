"""路径、模型、预设等全局配置。"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = ROOT / "vendor"


def data_dir() -> Path:
    """任务记录等用户数据的存放目录。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / "StellarUpscale"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- 超分模型 ----------

@dataclass(frozen=True)
class ModelInfo:
    key: str            # 传给 realesrgan-ncnn-vulkan 的 -n
    label: str          # 界面显示
    scales: tuple       # 支持的放大倍数
    note: str = ""
    default_strength: float = 0.7  # 默认 AI 细节强度


KNOWN_MODELS = {
    "realesr-animevideov3": ModelInfo(
        "realesr-animevideov3", "快速", (2, 3, 4),
        "速度快；全强度会有\"AI 画\"感，写实画面建议 0.2–0.4", 0.3),
    "realesrgan-x4plus": ModelInfo(
        "realesrgan-x4plus", "精细", (4,),
        "更偏写实，但比快速模式慢约 30 倍（10 秒视频约 2 小时）", 0.5),
    "realesrgan-x4plus-anime": ModelInfo(
        "realesrgan-x4plus-anime", "二次元", (4,),
        "纯二次元画风", 1.0),
}

NO_AI = ModelInfo("lanczos", "无 AI", (1,), "只做传统放大与调色，几乎实时", 0.0)


def find_realesrgan() -> Path | None:
    """依次查找：环境变量 → vendor 目录 → PATH。"""
    env = os.environ.get("STELLAR_REALESRGAN")
    if env and Path(env).is_file():
        return Path(env)
    if VENDOR_DIR.exists():
        for p in sorted(VENDOR_DIR.rglob("realesrgan-ncnn-vulkan")):
            if p.is_file():
                return p
    found = shutil.which("realesrgan-ncnn-vulkan")
    return Path(found) if found else None


def available_models(binary: Path | None) -> list[ModelInfo]:
    """扫描模型目录，返回可用模型（已知模型在前，"无 AI" 永远可用）。"""
    models: list[ModelInfo] = []
    if binary:
        mdir = binary.parent / "models"
        names = {p.stem for p in mdir.glob("*.param")} if mdir.exists() else set()
        for key, info in KNOWN_MODELS.items():
            if any(n == key or n.startswith(key + "-x") for n in names):
                models.append(info)
        # 用户自行放入的其它 ncnn 模型（按 x4 处理）
        known_files = {n for n in names
                       for k in KNOWN_MODELS if n == k or n.startswith(k + "-x")}
        for n in sorted(names - known_files):
            models.append(ModelInfo(n, n, (4,), "自定义模型", 1.0))
    models.append(NO_AI)
    return models


# ---------- 输出 ----------

TARGETS = {
    "1080p": 1080,   # 短边像素
    "2k": 1440,
}

# 目标短边 → 视频码率 (Mbps)，按画质档位
BITRATES = {
    "standard": {1080: 12, 1440: 20},
    "high": {1080: 18, 1440: 30},
}


@dataclass
class JobSettings:
    model: str = "realesr-animevideov3"
    strength: float = 0.3          # AI 细节强度：AI 结果与传统放大的混合比例
    target: str = "2k"
    preset: str = "clear"
    preset_strength: float = 1.0   # 调色强度
    grain: float = 0.3             # 胶片颗粒 0–1，让画面不过分光滑
    quality: str = "standard"
    codec: str = "hevc"            # hevc（体积小）/ h264（兼容性最好）
    output_dir: str = ""           # 为空则输出到原视频所在目录
    tile: int = 0                  # realesrgan 分块大小，0 = 自动

    @classmethod
    def from_dict(cls, d: dict | None) -> "JobSettings":
        d = d or {}
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)
