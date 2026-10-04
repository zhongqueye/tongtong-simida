"""任务队列：顺序处理、可取消、可续跑，任务记录保存在本地 JSON。"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import JobSettings, data_dir
from .media import probe
from .pipeline import Cancelled, Pipeline, Progress, output_name

STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE = "queued", "running", "done"
STATUS_FAILED, STATUS_PAUSED = "failed", "paused"


@dataclass
class Job:
    id: str
    src: str
    output: str
    settings: dict
    status: str = STATUS_QUEUED
    stage: str = ""
    done: int = 0
    total: int = 0
    eta: float | None = None
    message: str = ""
    error: str = ""
    info: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    timing: dict = field(default_factory=dict)  # 用时分解：extract / ai / encode / wait / mux（秒）

    @property
    def name(self) -> str:
        return Path(self.src).name

    def to_public(self) -> dict:
        d = asdict(self)
        d["name"] = self.name
        d["output_name"] = Path(self.output).name
        d["output_exists"] = Path(self.output).exists()
        d["elapsed"] = ((self.finished or time.time()) - self.started) if self.started else None
        return d


class JobManager:
    def __init__(self, store: Path | None = None):
        self.store = store or data_dir() / "jobs.json"
        self.work_root = data_dir() / "work"
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.current: str | None = None
        self.worker: threading.Thread | None = None
        self._load()

    # ---------- 持久化 ----------

    def _load(self) -> None:
        if not self.store.exists():
            return
        try:
            for d in json.loads(self.store.read_text(encoding="utf-8")):
                job = Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
                if job.status in (STATUS_RUNNING, STATUS_QUEUED):
                    job.status = STATUS_PAUSED  # 上次退出时未完成，可点"继续"续跑
                    job.eta = None
                self.jobs[job.id] = job
        except (ValueError, TypeError, OSError):
            pass

    def _save(self) -> None:
        with self.lock:
            data = [asdict(j) for j in self.jobs.values()]
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.store)

    # ---------- 操作 ----------

    def add(self, paths: list[str], settings: dict) -> list[str]:
        s = JobSettings.from_dict(settings)
        added, errors = [], []
        for p in paths:
            src = Path(p).expanduser()
            try:
                info = probe(src).to_dict()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src.name}：{e}")
                continue
            out_dir = Path(s.output_dir).expanduser() if s.output_dir else src.parent
            with self.lock:
                taken = {j.output for j in self.jobs.values() if j.status != STATUS_DONE}
            out = _unique(out_dir / output_name(src, s), taken)
            job = Job(id=uuid.uuid4().hex[:10], src=str(src), output=str(out),
                      settings=s.to_dict(), info=info, total=info["frames"])
            with self.lock:
                self.jobs[job.id] = job
            added.append(job.id)
        self._save()
        self.start()
        if errors:
            raise ValueError("\n".join(errors))
        return added

    def list(self) -> list[dict]:
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)
            return [j.to_public() for j in jobs]

    def resume(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job and job.status in (STATUS_PAUSED, STATUS_FAILED):
                job.status, job.error = STATUS_QUEUED, ""
        self._save()
        self.start()

    def resume_all(self) -> None:
        with self.lock:
            for job in self.jobs.values():
                if job.status == STATUS_PAUSED:
                    job.status = STATUS_QUEUED
        self._save()
        self.start()

    def pause(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            if job.id == self.current:
                self.cancel_event.set()
            elif job.status == STATUS_QUEUED:
                job.status = STATUS_PAUSED
        self._save()

    def remove(self, job_id: str) -> None:
        self.pause(job_id)
        for _ in range(50):
            if self.current != job_id:
                break
            time.sleep(0.1)
        with self.lock:
            self.jobs.pop(job_id, None)
        shutil.rmtree(self.work_root / job_id, ignore_errors=True)
        self._save()

    def clear_finished(self) -> None:
        with self.lock:
            for jid in [j.id for j in self.jobs.values() if j.status == STATUS_DONE]:
                self.jobs.pop(jid)
        self._save()

    # ---------- 后台处理 ----------

    def start(self) -> None:
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.worker = threading.Thread(target=self._loop, daemon=True)
            self.worker.start()

    def _next(self) -> Job | None:
        with self.lock:
            queued = [j for j in self.jobs.values() if j.status == STATUS_QUEUED]
            return min(queued, key=lambda j: j.created) if queued else None

    def _loop(self) -> None:
        while True:
            job = self._next()
            if job is None:
                return
            self.cancel_event.clear()
            with self.lock:
                self.current = job.id
                job.status, job.error, job.started, job.finished = STATUS_RUNNING, "", time.time(), None

            def on_progress(p: Progress, job=job):
                with self.lock:
                    job.stage, job.done, job.total, job.eta, job.message = (
                        p.stage, p.done, p.total or job.total, p.eta, p.message)
                    if p.timing:
                        job.timing = p.timing

            try:
                Pipeline(job.src, job.output, JobSettings.from_dict(job.settings),
                         self.work_root / job.id, on_progress=on_progress,
                         cancel=self.cancel_event).run()
                with self.lock:
                    job.status, job.eta = STATUS_DONE, 0
            except Cancelled:
                with self.lock:
                    job.status, job.eta, job.message = STATUS_PAUSED, None, "已暂停"
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    job.status, job.error, job.eta = STATUS_FAILED, str(e), None
            finally:
                with self.lock:
                    job.finished = time.time()
                    self.current = None
                self._save()


def _unique(path: Path, taken: set[str]) -> Path:
    free = lambda p: not p.exists() and str(p) not in taken  # noqa: E731
    if free(path):
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}({i}){path.suffix}")
        if free(cand):
            return cand
    return path
