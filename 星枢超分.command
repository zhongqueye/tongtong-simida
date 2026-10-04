#!/bin/bash
# 双击启动 星枢 · 超分（首次运行会自动安装依赖）
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -x .venv/bin/python ] || [ ! -d vendor/realesrgan ]; then
  ./setup.sh || { echo ""; read -r -p "安装失败，按回车键退出"; exit 1; }
fi
exec .venv/bin/python -m stellar_upscale "$@"
