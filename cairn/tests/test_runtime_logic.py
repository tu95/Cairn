from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

from cairn.dispatcher.config import ContainerConfig
from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease


@dataclass
class FakeProcess:
    cancelled: list[str] = field(default_factory=list)
    kill_count: int = 0

    def cancel(self, reason: str) -> None:
        self.cancelled.append(reason)

    def kill(self) -> None:
        self.kill_count += 1


def test_task_cancellation_keeps_first_reason_and_cancels_late_process() -> None:
    cancellation = TaskCancellation()

    assert cancellation.cancel("project stopped")
    assert not cancellation.cancel("second reason")
    assert cancellation.reason == "project stopped"

    process = FakeProcess()
    cancellation.attach_process(process)
    assert process.cancelled == ["project stopped"]


def test_heartbeat_conflict_failure_kills_attached_process() -> None:
    process = FakeProcess()
    lease = HeartbeatLease(lambda: ApiResult(409, text="lost"), "intent", "worker", interval=60)
    lease.attach_process(process)

    lease._fail(409, "lost")

    assert lease.failure is not None
    assert lease.failure.status_code == 409
    assert process.kill_count == 1


def test_runtime_manager_builds_local_process_with_timeout() -> None:
    manager = ContainerManager(ContainerConfig())

    process = manager.build_exec_process("runtime", {"A": "B"}, ["agent", "-p", "prompt"], timeout_seconds=300)

    assert process.command == ["agent", "-p", "prompt"]
    assert process.env == {"A": "B"}
    assert process.timeout_seconds == 300


def test_runtime_manager_names_project_runtime_without_external_state() -> None:
    manager = ContainerManager(ContainerConfig())

    assert manager.container_name("proj/001") == "cairn-local-proj-001"
    assert manager.ensure_running("proj/001") == "cairn-local-proj-001"
    assert manager.create_startup_container() == "cairn-startup-healthcheck-local"
    assert manager.managed_container_names() == []
    assert not manager.needs_completed_cleanup("proj/001")
    assert manager.cleanup_completed("proj/001")


def test_write_text_file_writes_directly_to_absolute_path(tmp_path: Path) -> None:
    manager = ContainerManager(ContainerConfig())
    target = tmp_path / "prompts" / "graph.yaml"

    manager.write_text_file("runtime", str(target), "facts: []\n")

    assert target.read_text(encoding="utf-8") == "facts: []\n"
