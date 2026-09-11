"""Bounded subprocess execution for both conversation and workflow commands."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable
from contextlib import nullcontext

from app.v1.policy import ExecutionLimits


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run_command(argv: list[str], cwd: Path, limits: ExecutionLimits,
                check: Callable[[], None] | None = None, guard=None) -> dict:
    if check:
        check()
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    job = None
    if os.name == "nt":
        from app.v1.windows_job import WindowsJob
        job = WindowsJob()
    try:
        with guard() if guard else nullcontext():
            spawn = job.spawn if job else subprocess.Popen
            if not job:
                options["stdin"] = subprocess.DEVNULL
            process = spawn(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=os.environ.copy(), shell=False, **options)
    except BaseException:
        if job:
            job.close()
        raise

    def stop_tree():
        if job:
            job.close()
        else:
            _kill_tree(process)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    def drain(pipe, buffer):
        try:
            while chunk := pipe.read(8192):
                room = max(0, limits.command_max_output_bytes - len(buffer))
                buffer.extend(chunk[:room])
                if len(chunk) > room:
                    overflow.set()
        finally:
            pipe.close()
    readers = [threading.Thread(target=drain, args=(pipe, buffer), daemon=True)
               for pipe, buffer in zip((process.stdout, process.stderr), buffers)]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + limits.command_timeout_seconds
    timed_out = False
    try:
        while process.poll() is None or any(reader.is_alive() for reader in readers):
            if check:
                check()
            timed_out = time.monotonic() >= deadline
            if timed_out or overflow.is_set():
                stop_tree()
                break
            time.sleep(0.05)
        process.wait(timeout=10)
        if check:
            check()
    except BaseException:
        stop_tree()
        process.wait(timeout=10)
        raise
    finally:
        # Reap descendants even if they detached their output handles and the
        # direct command already exited successfully.
        stop_tree()
        for reader in readers:
            reader.join(timeout=1)
    return {"command": argv, "cwd": str(cwd), "exit_code": process.returncode,
            "stdout": buffers[0].decode("utf-8", errors="replace"),
            "stderr": buffers[1].decode("utf-8", errors="replace"),
            "timed_out": timed_out, "output_truncated": overflow.is_set()}
