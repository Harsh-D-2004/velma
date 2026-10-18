import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from app.core.settings import settings
from app.models.agent import Tool

STREAM_CAP = 4000
READ_CAP = 65536


def scratch_path(job_id: str) -> Path:
    return Path(settings.scratch_dir).resolve() / job_id


def prepare_scratch(ctx) -> Path:
    """Create <scratch_dir>/<job_id> holding the read-only article.txt (also on resume). Returns the path."""
    root = scratch_path(ctx.job_id)
    remove_scratch(root)
    root.mkdir(parents=True)
    f = root / "article.txt"
    f.write_text(ctx.article)
    f.chmod(0o444)
    return root


def remove_scratch(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def bash(args: dict, tctx) -> dict:
    cmd = str(args.get("command", ""))
    if not cmd.strip():
        return {"ok": False, "error": "command is required"}
    try:
        timeout = int(args.get("timeout") or 30)
    except (TypeError, ValueError):
        return {"ok": False, "error": "timeout must be a number of seconds"}
    timeout = max(1, min(timeout, 120))
    argv = ["prlimit", "--as=1073741824", "--fsize=104857600", f"--cpu={timeout}", "--",
            "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/work",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--dev", "/dev", "--tmpfs", "/tmp",
            "--bind", str(tctx.scratch.resolve()), "/work", "--chdir", "/work", "bash", "-c", cmd]
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    bufs, over = {"o": bytearray(), "e": bytearray()}, threading.Event()

    def pump(stream, key):  # read at most READ_CAP per stream, then stop the command
        while chunk := stream.read1(8192):
            if len(bufs[key]) < READ_CAP:
                bufs[key] += chunk[:READ_CAP - len(bufs[key])]
            if len(bufs[key]) >= READ_CAP:
                over.set()
                return

    ts = [threading.Thread(target=pump, args=(proc.stdout, "o"), daemon=True),
          threading.Thread(target=pump, args=(proc.stderr, "e"), daemon=True)]
    for t in ts:
        t.start()
    deadline = time.monotonic() + timeout
    while proc.poll() is None and not over.is_set() and time.monotonic() < deadline:
        time.sleep(0.02)
    timed_out = proc.poll() is None and not over.is_set()
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()
    for t in ts:
        t.join(1)
    if timed_out:
        return {"ok": False, "error": f"command timed out after {timeout}s"}
    cut = lambda b: bytes(b).decode(errors="replace")[:STREAM_CAP]
    out = {"ok": proc.returncode == 0, "exit_code": proc.returncode, "stdout": cut(bufs["o"]), "stderr": cut(bufs["e"])}
    if over.is_set():
        out["note"] = "output was cut and the command stopped: over 64 KB"
    return out


DESC = """Run one `bash -c` command in /work, your private scratch folder for this job.
/work/article.txt is the full article (read-only). Any standard Linux tool under /usr works: ls, cat, head, tail, wc, grep, sed, awk, sort, uniq, cut, tr, diff, find, python3.
Create or edit files with redirection, heredocs, sed -i or python3. Files last only for this job and never appear on the page.
No network, no environment variables or secrets, no /proc (ps and top do not work). You cannot see anything outside /work.
Limits: timeout default 30 s, max 120 s; 1 GB memory; 100 MB per file; output cut to 4,000 characters per stream.
Use it to count, search or check the article. You never need it to produce the explainer."""
TOOLS = [
    Tool("bash", DESC,
         {"type": "object", "properties": {"command": {"type": "string"},
                                           "timeout": {"type": "integer", "description": "seconds, default 30, max 120"}},
          "required": ["command"]}, bash),
]
