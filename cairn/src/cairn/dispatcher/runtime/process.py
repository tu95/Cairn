from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import signal
import subprocess

LOG = logging.getLogger(__name__)
EXEC_KILL_JOIN_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False
    cancel_reason: str | None = None


class LocalManagedProcess:
    def __init__(self, command: list[str], env: dict[str, str], timeout_seconds: int | None = None):
        self.command = command
        self.env = env
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._cancel_reason: str | None = None

    def start(self) -> None:
        merged_env = os.environ.copy()
        merged_env.update(self.env)
        self._process = subprocess.Popen(
            self.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=merged_env,
            start_new_session=True,
        )

    def communicate(self, timeout: float | None) -> ProcessResult:
        assert self._process is not None
        effective_timeout = self.timeout_seconds if self.timeout_seconds is not None else timeout
        try:
            stdout, stderr = self._process.communicate(timeout=effective_timeout)
            return ProcessResult(
                returncode=self._process.returncode if self._process.returncode is not None else 1,
                stdout=stdout,
                stderr=stderr,
                cancelled=self._cancel_reason is not None,
                cancel_reason=self._cancel_reason,
            )
        except subprocess.TimeoutExpired:
            self.kill()
            stdout, stderr = self._process.communicate(timeout=EXEC_KILL_JOIN_TIMEOUT_SECONDS)
            return ProcessResult(
                returncode=self._process.returncode if self._process.returncode is not None else 137,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                cancelled=self._cancel_reason is not None,
                cancel_reason=self._cancel_reason,
            )

    def kill(self) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return

    def cancel(self, reason: str) -> None:
        if self._cancel_reason is None:
            self._cancel_reason = reason
        self.kill()


ManagedProcess = LocalManagedProcess
