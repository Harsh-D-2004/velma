import gzip
import json
import secrets
import threading
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from app.helpers.lines import slice_text


@dataclass
class JobContext:
    job_id: str = ""
    messages: list = field(default_factory=list)
    workspace: dict = field(default_factory=dict)
    steps: int = 0
    tokens: int = 0
    finished: bool = False
    nudges: int = 0
    warned: bool = False
    model_calls: int = 0
    tool_calls: int = 0
    article: str = ""  # not snapshotted: reloaded from inputs/

    def pending_tool_calls(self) -> list:
        """Tool calls of the last assistant message that have no tool result yet."""
        done = set()
        for m in reversed(self.messages):
            if m["role"] == "tool":
                done.add(m["tool_call_id"])
            elif m["role"] == "assistant":
                return [c for c in m.get("tool_calls") or [] if c["id"] not in done]
        return []

    def snapshot(self) -> bytes:
        ws = {**self.workspace, "sections": {  # section text is rebuilt from inputs/ by start/end
            k: {a: b for a, b in s.items() if a != "text"} for k, s in self.workspace["sections"].items()}}
        state = {"job_id": self.job_id, "messages": self.messages, "workspace": ws, "steps": self.steps,
                 "tokens": self.tokens, "finished": self.finished,
                 "nudges": self.nudges, "warned": self.warned,
                 "model_calls": self.model_calls, "tool_calls": self.tool_calls}
        return gzip.compress(json.dumps(state).encode())


def state_key(job_id: str) -> str:
    return f"state/{job_id}.json.gz"


def init_workspace() -> dict:
    return {"title": None, "sections": {}, "section_order": [], "public_prefix": "p/" + secrets.token_urlsafe(16),
            "outline": None, "diagrams": {}, "glossary": {}, "search": {}}


def load(job_id: str, storage, article_key: str) -> JobContext:
    article = storage.get_private(article_key).decode()
    try:
        state = json.loads(gzip.decompress(storage.get_private(state_key(job_id))))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
            raise
        return JobContext(job_id=job_id, workspace=init_workspace(), article=article)
    lines, ws = article.splitlines(), state["workspace"]
    for s in ws["sections"].values():
        s["text"] = slice_text(lines, s["start"], s["end"], ws.get("title_line"))
    state.pop("cache", None)  # old snapshots
    return JobContext(**state, article=article)


class Snapshotter:
    """Uploads only the newest snapshot; older ones are dropped if the upload is slower than the agent."""

    def __init__(self, job_id: str, storage):
        self.key = state_key(job_id)
        self._storage = storage
        self._cv = threading.Condition()
        self._data: bytes | None = None
        self._busy = False
        self._error: Exception | None = None
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, data: bytes) -> None:
        with self._cv:
            self._data = data
            self._cv.notify_all()

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._data is None:
                    self._cv.wait()
                data, self._data, self._busy = self._data, None, True
            try:
                self._storage.put_private(self.key, data)
                err = None
            except Exception as e:  # boto already retried; keep the data so flush can report it
                err = e
            with self._cv:
                self._busy = False
                self._error = err
                self._cv.notify_all()

    def flush(self, timeout: float = 20) -> str:
        with self._cv:
            if not self._cv.wait_for(lambda: self._data is None and not self._busy, timeout):
                raise TimeoutError("snapshot upload did not finish")
            if self._error:
                raise self._error
        return self.key
