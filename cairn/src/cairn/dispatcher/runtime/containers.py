from __future__ import annotations

import logging
from pathlib import Path

from cairn.dispatcher.config import ContainerConfig
from cairn.dispatcher.runtime.process import LocalManagedProcess

LOG = logging.getLogger(__name__)


class ContainerManager:
    _PREFIX = "cairn-local-"
    _STARTUP_PREFIX = "cairn-startup-healthcheck-local"

    def __init__(self, config: ContainerConfig):
        self._config = config

    def close(self) -> None:
        return None

    @property
    def mode(self) -> str:
        return "host"

    def container_name(self, project_id: str) -> str:
        sanitized = project_id.replace("/", "-")
        return f"{self._PREFIX}{sanitized}"

    def ensure_running(self, project_id: str) -> str:
        name = self.container_name(project_id)
        LOG.debug("using local runtime project=%s runtime=%s", project_id, name)
        return name

    def create_startup_container(self) -> str:
        return self._STARTUP_PREFIX

    def inspect_state(self, name: str) -> str | None:
        return None

    def cleanup_completed(self, project_id: str) -> bool:
        return True

    def cleanup_stopped(self, project_id: str) -> bool:
        return True

    def cleanup_orphan(self, name: str) -> bool:
        return True

    def managed_container_names(self) -> list[str]:
        return []

    def needs_completed_cleanup(self, project_id: str) -> bool:
        return False

    def needs_orphan_cleanup(self, name: str) -> bool:
        return False

    def needs_stopped_cleanup(self, project_id: str) -> bool:
        return False

    def remove_container(self, name: str, *, force: bool = True) -> None:
        return None

    def build_exec_process(
        self,
        container_name: str,
        env: dict[str, str],
        command: list[str],
        timeout_seconds: int | None = None,
        kill_after_seconds: int = 5,
    ) -> LocalManagedProcess:
        return LocalManagedProcess(command, env, timeout_seconds)

    def write_text_file(self, container_name: str, path: str, content: str) -> None:
        target = Path(path)
        if not target.is_absolute():
            raise ValueError(f"local file path must be absolute: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
