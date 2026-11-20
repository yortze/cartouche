"""File d'attente des tâches longues (installations, téléchargements), une à la fois."""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections import deque

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class JobError(Exception):
    """Erreur attendue, avec un message clair pour l'utilisateur."""


class Job:
    def __init__(self, kind: str, title: str, subtitle: str = "", ref: str = ""):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.subtitle = subtitle
        self.ref = ref  # chemin du PKG ou autre identifiant de l'objet traité
        self.status = "queued"  # queued | running | done | error | cancelled
        self.progress: float | None = None
        self.detail = "En attente"
        self.error = ""
        self.result: dict = {}
        self.created = time.time()
        self.started: float | None = None
        self.finished: float | None = None
        self.cancel_event = threading.Event()
        self.process = None
        self._log: deque[str] = deque(maxlen=500)
        self._lock = threading.Lock()

    def add_log(self, line: str) -> None:
        line = line.rstrip()
        if line:
            with self._lock:
                self._log.append(line[:500])

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled()

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    def to_dict(self) -> dict:
        with self._lock:
            lines = list(self._log)
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "subtitle": self.subtitle,
            "ref": self.ref, "status": self.status, "progress": self.progress, "detail": self.detail,
            "error": self.error, "result": self.result, "created": self.created,
            "started": self.started, "finished": self.finished, "log": lines[-200:],
        }


class JobManager:
    def __init__(self, on_finish=None, keep: int = 30):
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._keep = keep
        self._on_finish = on_finish
        self._worker = threading.Thread(target=self._loop, name="jobs", daemon=True)
        self._worker.start()

    def submit(self, job: Job, fn) -> Job:
        with self._lock:
            self._jobs[job.id] = job
            self._trim()
        self._queue.put((job, fn))
        return job

    def _trim(self) -> None:
        finished = [j for j in self._jobs.values() if not j.active]
        for job in sorted(finished, key=lambda j: j.created)[: max(0, len(self._jobs) - self._keep)]:
            self._jobs.pop(job.id, None)

    def _loop(self) -> None:
        while True:
            job, fn = self._queue.get()
            if job.cancel_event.is_set():
                job.status, job.detail, job.finished = "cancelled", "Annulé", time.time()
                continue
            job.status, job.started = "running", time.time()
            try:
                fn(job)
                if job.cancel_event.is_set():
                    raise JobCancelled()
                job.status = "done"
            except JobCancelled:
                job.status, job.detail = "cancelled", "Annulé"
            except JobError as exc:
                job.status, job.error = "error", str(exc)
                job.detail = str(exc)
            except Exception as exc:  # erreur imprévue : on la garde lisible
                log.exception("Tâche %s en échec", job.title)
                job.status, job.error = "error", f"Erreur inattendue : {exc}"
                job.detail = job.error
            finally:
                job.process = None
                job.finished = time.time()
            if self._on_finish:
                try:
                    self._on_finish(job)
                except Exception:
                    log.exception("Rappel de fin de tâche en échec")

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or not job.active:
            return False
        job.cancel_event.set()
        process = job.process
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
        return True

    def list(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)
        return [job.to_dict() for job in jobs]

    def busy(self) -> bool:
        with self._lock:
            return any(job.active for job in self._jobs.values())

    def active_refs(self) -> set[str]:
        with self._lock:
            return {job.ref for job in self._jobs.values() if job.active and job.ref}
