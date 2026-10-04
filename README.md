# 星枢 · 超分

给 Seedance 等 AI 生成的 720P 漫剧视频做**超分辨率 + 画质增强**，本地运行，针对 Apple 芯片 Mac（M1/M2，8GB 也能跑）优化。

- **AI 超分**：Real-ESRGAN（ncnn / Vulkan → Metal），720P → 1080P / 2K
- **AI 细节强度**：AI 结果与原画按比例混合，避免全强度带来的"AI 画"假感
- **一键调色**：原色 / 通透 / 鲜艳 / 电影感，强度可调；可加细微胶片颗粒
- **单帧预览**：正式处理前，拖动中线对比原片与成片
- **批量队列**：一次拖入多个视频或整个文件夹，按顺序处理
- **断点续跑**：分段处理，中途暂停、退出或断电后，从断点继续
- **省硬盘**：2K 临时帧处理完一段就删一段，1 分钟视频只需约 2–3 GB 临时空间
- **原音频保留**，Apple 硬件 HEVC 编码

## 安装与启动（macOS）

1. 安装 [Homebrew](https://brew.sh)（已安装可跳过）
2. 下载本仓库，双击 **`星枢超分.command`**
   - 首次运行会自动安装 ffmpeg、下载 Real-ESRGAN、创建 Python 环境，需要几分钟
   - 如果提示"无法打开，因为来自身份不明的开发者"：右键 → 打开，或在终端运行 `chmod +x 星枢超分.command setup.sh`

也可以手动安装：

```bash
./setup.sh
.venv/bin/python -m stellar_upscale          # 图形界面
```

## 推荐设置

| 素材 | 模式 | AI 细节强度 | 调色 | 颗粒 |
|---|---|---|---|---|
| 写实 / 3D 真人风漫剧（默认） | 快速 | 20–40% | 通透 | 30% |
| 想要更多细节，能接受慢 | 精细 | 40–60% | 通透 | 20–30% |
| 纯二次元 | 快速 | 70–100% | 鲜艳 | 0 |
| 只想调色、最快 | 无 AI | — | 通透 | 30% |

- **快速**模型（animevideov3）是为动画训练的，全强度会把皮肤磨平、给发丝描边，写实素材请把强度控制在 40% 以内
- **精细**模型（x4plus）更偏写实，但速度大约只有快速模式的 1/8
- 输出 1080P 时，内部也是先超分到 2K 再精细缩小（超采样），耗时与 2K 基本相同
- 要发到会二次压缩的平台，建议直接输出 2K

## 速度参考（M2 8GB，推算值）

1 分钟 24fps 视频共 1440 帧：

| 模式 | 预计耗时 |
|---|---|
| 无 AI | 1–2 分钟 |
| 快速 | 6–10 分钟 |
| 精细 | 45–75 分钟 |

界面会显示实时进度和剩余时间。处理时请尽量关闭其它大型程序，内存不足（开始使用交换内存）时速度会明显下降；精细模式若报错，可在命令行加 `--tile 256`。

## 命令行

```bash
.venv/bin/python -m stellar_upscale 视频1.mp4 视频2.mp4 \
  -t 2k -m realesr-animevideov3 -s 0.3 -p clear --preset-strength 1 -g 0.3 -o ~/Movies/超分
```

| 参数 | 说明 |
|---|---|
| `-t` | `1080p` / `2k` |
| `-m` | `realesr-animevideov3`（快速）/ `realesrgan-x4plus`（精细）/ `lanczos`（无 AI） |
| `-s` | AI 细节强度 0–1 |
| `-p` | `original` / `clear` / `vivid` / `cinema` |
| `--preset-strength` | 调色强度 0–1.5 |
| `-g` | 胶片颗粒 0–1 |
| `-q` | `standard`（1080P 12Mbps / 2K 20Mbps）/ `high`（18 / 30Mbps） |
| `--tile` | AI 分块大小，0 = 自动 |

按 Ctrl+C 中断后，重新运行同样的命令会从断点继续。

## 处理流程

```
原视频 ─拆帧(PNG)─┬─ 每 48 帧一段 ─ Real-ESRGAN ─┐
                 └──────── 原始帧 ───────────────┴─ 混合(AI 强度) → 调色 → 锐化 → 颗粒 → HEVC 编码
                                                    ↓
                               各段拼接 + 原音频 → 输出 xxx_星枢2K.mp4
```

## 文件位置

- 输出：默认与原视频同目录，文件名后缀 `_星枢2K` / `_星枢1080P`
- 任务记录与临时文件：`~/Library/Application Support/StellarUpscale/`

## 开发

```bash
python3 -m unittest discover tests   # 需要 ffmpeg
```

界面在 `stellar_upscale/web/`，用 [pywebview](https://pywebview.flowrl.com) 显示为原生窗口。
