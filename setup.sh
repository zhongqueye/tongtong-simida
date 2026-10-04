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
  # 先找手动下载好的压缩包（仓库目录 / vendor / 下载文件夹）
  ZIP=""
  for cand in "./${RE_ZIP}" "vendor/${RE_ZIP}" "$HOME/Downloads/${RE_ZIP}"; do
    if [ -f "$cand" ] && unzip -tq "$cand" >/dev/null 2>&1; then ZIP="$cand"; echo "使用已下载的 $cand"; break; fi
  done
  if [ -z "$ZIP" ]; then
    ZIP="vendor/realesrgan.zip"
    # 断点续传 + 超时重试；GitHub 在国内可能很慢
    if ! curl -L --fail --progress-bar --connect-timeout 20 --retry 3 --retry-delay 3 \
         -C - -o "${ZIP}.part" "${RE_BASE}/${RE_ZIP}"; then
      echo ""
      echo "✗ 下载失败（GitHub 连接不上或太慢）。可以手动下载："
      echo "  1. 用浏览器打开：${RE_BASE}/${RE_ZIP}"
      echo "  2. 下载完成后保持文件在「下载」文件夹（文件名 ${RE_ZIP}）"
      echo "  3. 重新双击「星枢超分.command」，会自动使用这个文件"
      exit 1
    fi
    mv "${ZIP}.part" "$ZIP"
  fi
  unzip -oq "$ZIP" -d vendor/realesrgan
  [ "$ZIP" = "vendor/realesrgan.zip" ] && rm -f "$ZIP"
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
PIP=".venv/bin/python -m pip install -q --disable-pip-version-check --timeout 20"
if ! $PIP -r requirements.txt; then
  echo "PyPI 连接失败，改用清华镜像重试…"
  $PIP -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
fi

echo ""
echo "✓ 安装完成。双击「星枢超分.command」启动，或运行：.venv/bin/python -m stellar_upscale"
