#!/bin/bash
# 星枢 · 超分 —— 一键安装依赖（macOS / Linux）
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

RE_VER="v0.2.5.0"
RE_BASE="https://github.com/xinntao/Real-ESRGAN/releases/download/${RE_VER}"
case "$(uname -s)" in
  Darwin) RE_ZIP="realesrgan-ncnn-vulkan-20220424-macos.zip" ;;
  Linux)  RE_ZIP="realesrgan-ncnn-vulkan-20220424-ubuntu.zip" ;;
  *) echo "暂不支持此系统"; exit 1 ;;
esac

echo "==> 1/3 检查 ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install ffmpeg
  else
    echo "未找到 ffmpeg。请先安装 Homebrew（https://brew.sh），再运行：brew install ffmpeg"
    exit 1
  fi
fi
ffmpeg -version | head -1

echo "==> 2/3 下载 AI 超分组件 Real-ESRGAN (${RE_VER})"
if [ -d vendor/realesrgan ] && find vendor/realesrgan -name realesrgan-ncnn-vulkan -type f | grep -q .; then
  echo "已存在，跳过"
else
  mkdir -p vendor/realesrgan
  curl -L --fail --progress-bar -o vendor/realesrgan.zip "${RE_BASE}/${RE_ZIP}"
  unzip -oq vendor/realesrgan.zip -d vendor/realesrgan
  rm -f vendor/realesrgan.zip
  find vendor/realesrgan -name realesrgan-ncnn-vulkan -exec chmod +x {} \;
  # 去掉 macOS 的"来自互联网"隔离标记，否则会被系统拦截
  if [ "$(uname -s)" = "Darwin" ]; then xattr -dr com.apple.quarantine vendor/realesrgan 2>/dev/null || true; fi
fi

echo "==> 3/3 创建 Python 环境"
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3。macOS 请运行 xcode-select --install，或用 brew install python"
  exit 1
fi
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

echo ""
echo "✓ 安装完成。双击「星枢超分.command」启动，或运行：.venv/bin/python -m stellar_upscale"
