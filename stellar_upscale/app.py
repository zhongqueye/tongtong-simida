"""图形界面：pywebview 原生窗口 + web/ 下的 HTML 界面。"""

from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import subprocess
import sys
from pathlib import Path

from . import APP_NAME, __version__, grade
from .config import JobSettings, available_models, data_dir, find_realesrgan
from .jobs import JobManager
from .media import MediaError, encoder_args, probe
from .pipeline import render_preview

WEB_DIR = Path(__file__).resolve().parent / "web"
PREFS = data_dir() / "prefs.json"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def _load_prefs() -> dict:
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_prefs(prefs: dict) -> None:
    PREFS.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")


def _ok(**kw):
    return {"ok": True, **kw}


def _err(e) -> dict:
    return {"ok": False, "error": str(e)}


class Api:
    """暴露给前端 JS 的接口（window.pywebview.api.*）。每次调用在独立线程执行。"""

    def __init__(self, manager: JobManager):
        self._manager = manager
        self._window = None

    # ---------- 环境 ----------

    def env(self):
        binary = find_realesrgan()
        models = [
            {"key": m.key, "label": m.label, "note": m.note, "strength": m.default_strength,
             "ai": m.key != "lanczos"}
            for m in available_models(binary)
        ]
        try:
            _, enc_label = encoder_args(10)
            ffmpeg_ok = True
        except MediaError:
            enc_label, ffmpeg_ok = "未找到 ffmpeg", False
        prefs = _load_prefs()
        return {
            "app": APP_NAME, "version": __version__,
            "ffmpeg": ffmpeg_ok, "encoder": enc_label,
            "realesrgan": str(binary) if binary else "",
            "models": models,
            "presets": [{"key": k, **v} for k, v in grade.PRESETS.items()],
            "settings": JobSettings.from_dict(prefs.get("settings")).to_dict(),
            "theme": prefs.get("theme", "dark"),
            "background": self._background_url(prefs.get("background")),
        }

    def save_settings(self, settings: dict):
        prefs = _load_prefs()
        prefs["settings"] = JobSettings.from_dict(settings).to_dict()
        _save_prefs(prefs)
        return _ok()

    def set_theme(self, theme: str):
        prefs = _load_prefs()
        prefs["theme"] = theme
        _save_prefs(prefs)
        return _ok()

    # ---------- 文件选择 ----------

    def pick_videos(self):
        import webview
        res = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("视频文件 (*.mp4;*.mov;*.mkv;*.webm;*.m4v;*.avi)", "所有文件 (*.*)"))
        return self.describe(list(res or []))

    def pick_folder(self):
        import webview
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else ""

    def pick_background(self):
        import webview
        res = self._window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("图片 (*.jpg;*.jpeg;*.png;*.webp)",))
        if not res:
            return ""
        prefs = _load_prefs()
        prefs["background"] = res[0]
        _save_prefs(prefs)
        return self._background_url(res[0])

    def clear_background(self):
        prefs = _load_prefs()
        prefs.pop("background", None)
        _save_prefs(prefs)
        return _ok()

    @staticmethod
    def _background_url(path: str | None) -> str:
        if not path or not Path(path).is_file():
            return ""
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()

    def describe(self, paths: list[str]):
        """读取视频信息，过滤非视频文件。"""
        items = []
        for p in paths:
            path = Path(p)
            if path.is_dir():
                items += [self._describe_one(c) for c in sorted(path.iterdir())
                          if c.suffix.lower() in VIDEO_EXTS]
            elif path.suffix.lower() in VIDEO_EXTS:
                items.append(self._describe_one(path))
        return [i for i in items if i]

    @staticmethod
    def _describe_one(path: Path):
        try:
            info = probe(path).to_dict()
        except Exception as e:  # noqa: BLE001
            return {"path": str(path), "name": path.name, "error": str(e)}
        return {"path": str(path), "name": path.name, "size": path.stat().st_size, **info}

    # ---------- 任务 ----------

    def add_jobs(self, paths: list[str], settings: dict):
        try:
            self.save_settings(settings)
            return _ok(ids=self._manager.add(paths, settings))
        except Exception as e:  # noqa: BLE001
            return _err(e)

    def jobs(self):
        return self._manager.list()

    def pause(self, job_id):
        self._manager.pause(job_id)
        return _ok()

    def resume(self, job_id):
        self._manager.resume(job_id)
        return _ok()

    def resume_all(self):
        self._manager.resume_all()
        return _ok()

    def remove(self, job_id):
        self._manager.remove(job_id)
        return _ok()

    def clear_finished(self):
        self._manager.clear_finished()
        return _ok()

    # ---------- 预览 ----------

    def preview(self, path: str, settings: dict, at: float | None = None):
        try:
            return _ok(**render_preview(path, JobSettings.from_dict(settings), at))
        except Exception as e:  # noqa: BLE001
            return _err(e)

    # ---------- 系统 ----------

    def open_file(self, path: str):
        _open([path])
        return _ok()

    def reveal(self, path: str):
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            _open([str(Path(path).parent)])
        return _ok()


def _open(args: list[str]) -> None:
    opener = "open" if sys.platform == "darwin" else (shutil.which("xdg-open") or "xdg-open")
    subprocess.Popen([opener, *args])


def main() -> int:
    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先运行 setup.sh（或 pip install pywebview）")
        return 1

    manager = JobManager()
    api = Api(manager)
    window = webview.create_window(
        APP_NAME, str(WEB_DIR / "index.html"), js_api=api,
        width=1440, height=900, min_size=(1100, 720), background_color="#0b1220")
    api._window = window

    def bind_drop():
        # 拖入文件：pywebview 会在 drop 事件里附带本地完整路径
        try:
            from webview.dom import DOMEventHandler

            def on_drop(e):
                files = (e.get("dataTransfer") or {}).get("files") or []
                paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
                if paths:
                    window.evaluate_js(f"window.onNativeDrop({json.dumps(api.describe(paths))})")

            window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception as e:  # noqa: BLE001
            print("拖放功能不可用：", e)

    window.events.loaded += bind_drop
    webview.start(debug="--debug" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
