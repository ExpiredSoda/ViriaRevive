"""Subprocess helper — hides console windows on Windows + cancellation support."""

import subprocess
import sys
import threading

# On Windows, prevent ffmpeg/ffprobe from flashing a console window
_CREATION_FLAGS = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)

# ── Global cancel infrastructure ────────────────────────────────────────────
_cancel_flag = threading.Event()
_active_processes: list[subprocess.Popen] = []
_lock = threading.Lock()


def request_cancel():
    """Signal all running subprocesses to stop."""
    _cancel_flag.set()
    with _lock:
        for proc in _active_processes:
            try:
                proc.terminate()
            except OSError:
                pass


def reset_cancel():
    """Clear the cancel flag (call before starting a new pipeline)."""
    _cancel_flag.clear()
    with _lock:
        _active_processes.clear()


def is_cancelled() -> bool:
    """Check if cancellation has been requested."""
    return _cancel_flag.is_set()


class CancelledError(Exception):
    """Raised when a subprocess is interrupted by cancellation."""

    def __init__(self, message="Pipeline cancelled", output=None, stderr=None, stdout=None):
        super().__init__(message)
        if output is None and stdout is not None:
            output = stdout
        self.output = output
        self.stdout = output
        self.stderr = stderr


def run(*args, **kwargs):
    """subprocess.run() wrapper with cancellation support.

    Polls the process every 0.5s. If cancel is requested, terminates the
    process and raises CancelledError. Also hides console windows on Windows.
    """
    if _cancel_flag.is_set():
        raise CancelledError("Pipeline cancelled")

    kwargs.setdefault("creationflags", _CREATION_FLAGS)

    # Translate capture_output into Popen-compatible args
    capture_output = kwargs.pop("capture_output", False)
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)

    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    text_mode = bool(
        kwargs.get("text")
        or kwargs.get("universal_newlines")
        or kwargs.get("encoding")
        or kwargs.get("errors")
    )

    proc = subprocess.Popen(*args, **kwargs)
    with _lock:
        _active_processes.append(proc)

    try:
        # Drain stdout/stderr in background threads to prevent pipe deadlock.
        # FFmpeg writes heavily to stderr (progress, stats). If the pipe buffer
        # fills (~64KB) and nobody reads it, FFmpeg blocks → deadlock.
        stdout_chunks = []
        stderr_chunks = []

        def _drain(pipe, buf):
            try:
                while True:
                    chunk = pipe.read(8192)
                    if not chunk:
                        break
                    buf.append(chunk)
            except Exception:
                pass

        drain_threads = []
        if proc.stdout:
            t = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
            t.start()
            drain_threads.append(t)
        if proc.stderr:
            t = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
            t.start()
            drain_threads.append(t)

        def _combine_captured_output(join_timeout=5):
            for thread in drain_threads:
                thread.join(timeout=join_timeout)
            stdout_captured = proc.stdout is not None
            stderr_captured = proc.stderr is not None
            empty_output = "" if text_mode else b""
            if stdout_chunks:
                joiner = b'' if isinstance(stdout_chunks[0], bytes) else ''
                stdout = joiner.join(stdout_chunks)
            else:
                stdout = empty_output if stdout_captured else None
            if stderr_chunks:
                joiner = b'' if isinstance(stderr_chunks[0], bytes) else ''
                stderr = joiner.join(stderr_chunks)
            else:
                stderr = empty_output if stderr_captured else None
            return stdout, stderr

        # Poll the process, checking cancel flag every 0.5s
        elapsed = 0.0
        poll_interval = 0.5
        while proc.poll() is None:
            if _cancel_flag.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                stdout, stderr = _combine_captured_output(join_timeout=1)
                raise CancelledError("Pipeline cancelled", output=stdout, stderr=stderr)
            if timeout is not None and elapsed >= timeout:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                stdout, stderr = _combine_captured_output(join_timeout=1)
                raise subprocess.TimeoutExpired(proc.args, timeout, output=stdout, stderr=stderr)
            # Wait a bit before next poll
            try:
                proc.wait(timeout=poll_interval)
            except subprocess.TimeoutExpired:
                pass
            elapsed += poll_interval

        # Check cancel one more time after process exits (process may have been
        # killed externally by request_cancel via _active_processes)
        if _cancel_flag.is_set():
            stdout, stderr = _combine_captured_output(join_timeout=1)
            raise CancelledError("Pipeline cancelled", output=stdout, stderr=stderr)

        # Wait for drain threads to finish reading
        stdout, stderr = _combine_captured_output(join_timeout=5)

        result = subprocess.CompletedProcess(
            args=proc.args,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, result.args, result.stdout, result.stderr
            )
        return result
    finally:
        # Clean up pipes
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc.stderr:
            try:
                proc.stderr.close()
            except Exception:
                pass
        with _lock:
            try:
                _active_processes.remove(proc)
            except ValueError:
                pass
