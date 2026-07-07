from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from cairn.dispatcher.config import ContainerConfig
from cairn.dispatcher.runtime.process import LocalManagedProcess
from cairn.workspace import workspace_path

LOG = logging.getLogger(__name__)

# docker shim：拦截 agent 内部对 docker 的调用，给它创建的容器打上特征 label，
# 从而可以确定性地追踪与清理，不依赖 LLM 是否记得收尾。
# 它 exec 到宿主机真实 docker（通过绝对路径，避免再命中自己造成死循环）。
_DOCKER_SHIM = r'''#!/usr/bin/env python3
import json, os, sys, time

PID = os.environ.get("CAIRN_PROJECT_ID", "")
DOCKER = os.environ.get("CAIRN_DOCKER_BIN") or "docker"
LEDGER = os.environ.get("CAIRN_DOCKER_LEDGER", "")
GLOBAL_VALUE_FLAGS = {"-H", "--host", "-l", "--log-level", "--context", "--config",
                      "--tlscacert", "--tlscert", "--tlskey"}
args = sys.argv[1:]


def sub_index(a):
    i = 0
    while i < len(a):
        t = a[i]
        if t in GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return i
    return -1


def record(kind):
    if not LEDGER:
        return
    try:
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": int(time.time()), "project": PID, "cmd": kind, "argv": sys.argv[1:]}) + "\n")
    except OSError:
        pass


idx = sub_index(args)
if idx >= 0 and PID:
    sub = args[idx]
    if sub in ("run", "create"):
        labels = ["--label", "cairn.managed=1", "--label", "cairn.project=%s" % PID]
        args = args[:idx + 1] + labels + args[idx + 1:]
        record(sub)
    elif sub == "compose":
        rest = args[idx + 1:]
        if "-p" not in rest and "--project-name" not in rest:
            args = args[:idx + 1] + ["-p", "cairn_%s" % PID] + rest
        record("compose")

try:
    if os.path.isabs(DOCKER):
        os.execv(DOCKER, [DOCKER] + args)
    else:
        os.execvp(DOCKER, [DOCKER] + args)
except OSError as exc:
    sys.stderr.write("cairn docker shim: cannot exec %s: %s\n" % (DOCKER, exc))
    sys.exit(127)
'''


class ContainerManager:
    _PREFIX = "cairn-local-"
    _STARTUP_PREFIX = "cairn-startup-healthcheck-local"

    def __init__(self, config: ContainerConfig):
        self._config = config
        self.manage_docker = bool(getattr(config, "manage_docker", False))
        self._confine_workdir = bool(getattr(config, "confine_workdir", True))
        self._reap_orphans = bool(getattr(config, "reap_orphans", True))
        binary = getattr(config, "docker_binary", "docker") or "docker"
        self._docker = shutil.which(binary) or binary
        self._shim_dir: Path | None = None
        if self.manage_docker:
            self._shim_dir = self._install_shim()
            LOG.info(
                "docker isolation enabled docker=%s shim=%s confine_workdir=%s reap_orphans=%s",
                self._docker,
                self._shim_dir,
                self._confine_workdir,
                self._reap_orphans,
            )

    def close(self) -> None:
        return None

    @property
    def mode(self) -> str:
        return "host"

    # ── 命名与运行时（宿主机直跑，agent 仍在本机以保留 IDA 等工具） ──────────────
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

    def managed_container_names(self) -> list[str]:
        return []

    def cleanup_orphan(self, name: str) -> bool:
        return True

    def needs_orphan_cleanup(self, name: str) -> bool:
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
        run_env = dict(env)
        cwd: str | None = None
        project_id = self._project_id_from_name(container_name)
        if self.manage_docker and project_id is not None:
            if self._shim_dir is not None:
                existing = run_env.get("PATH") or os.environ.get("PATH", "")
                run_env["PATH"] = (
                    f"{self._shim_dir}{os.pathsep}{existing}" if existing else str(self._shim_dir)
                )
                run_env["CAIRN_PROJECT_ID"] = project_id
                run_env["CAIRN_DOCKER_BIN"] = self._docker
                run_env["CAIRN_DOCKER_LEDGER"] = str(self._project_dir(project_id) / "docker-ledger.jsonl")
            if self._confine_workdir:
                cwd = str(self._project_dir(project_id))
        return LocalManagedProcess(command, run_env, timeout_seconds, cwd=cwd)

    def write_text_file(self, container_name: str, path: str, content: str) -> None:
        target = Path(path)
        if not target.is_absolute():
            raise ValueError(f"local file path must be absolute: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    # ── 项目容器清理（按 label 确定性删除，与 dispatcher 的清理生命周期对接） ──────
    def needs_completed_cleanup(self, project_id: str) -> bool:
        return self._has_project_containers(project_id)

    def needs_stopped_cleanup(self, project_id: str) -> bool:
        return self._has_project_containers(project_id)

    def cleanup_completed(self, project_id: str) -> bool:
        return self._cleanup_project(project_id)

    def cleanup_stopped(self, project_id: str) -> bool:
        return self._cleanup_project(project_id)

    def reap_orphans(self, live_project_ids: set[str]) -> None:
        """删除已不存在项目（被删除/崩溃残留）留下的托管容器。"""
        if not self._docker_ready() or not self._reap_orphans:
            return
        live = {self._label_value(pid) for pid in live_project_ids}
        orphans: list[str] = []
        for cid, project in self._managed_with_label("cairn.project"):
            if project and project not in live:
                orphans.append(cid)
        for cid, project in self._managed_with_label(
            "com.docker.compose.project", filter_label="com.docker.compose.project"
        ):
            if project.startswith("cairn_") and project[len("cairn_"):] not in live:
                orphans.append(cid)
        if orphans:
            LOG.info("reaping orphan containers count=%s", len(set(orphans)))
            self._rm(sorted(set(orphans)))

    # ── 内部工具 ──────────────────────────────────────────────────────────────
    def _install_shim(self) -> Path | None:
        try:
            shim_dir = workspace_path(".cairn-shim")
            shim_dir.mkdir(parents=True, exist_ok=True)
            shim = shim_dir / "docker"
            shim.write_text(_DOCKER_SHIM, encoding="utf-8")
            shim.chmod(0o755)
            return shim_dir
        except OSError:
            LOG.exception("failed to install docker shim; disabling docker isolation")
            self.manage_docker = False
            return None

    def _project_dir(self, project_id: str) -> Path:
        return workspace_path(self._label_value(project_id))

    def _project_id_from_name(self, name: str) -> str | None:
        if name and name.startswith(self._PREFIX):
            return name[len(self._PREFIX):]
        return None

    @staticmethod
    def _label_value(project_id: str) -> str:
        return project_id.replace("/", "-")

    def _docker_ready(self) -> bool:
        return self.manage_docker and bool(self._docker)

    def _project_container_ids(self, project_id: str) -> list[str]:
        val = self._label_value(project_id)
        ids = self._ps(["--filter", f"label=cairn.project={val}"])
        ids += self._ps(["--filter", f"label=com.docker.compose.project=cairn_{val}"])
        return sorted(set(ids))

    def _has_project_containers(self, project_id: str) -> bool:
        if not self._docker_ready():
            return False
        return bool(self._project_container_ids(project_id))

    def _cleanup_project(self, project_id: str) -> bool:
        if self._docker_ready():
            self._rm(self._project_container_ids(project_id))
        return True

    def _ps(self, extra: list[str]) -> list[str]:
        try:
            result = subprocess.run(
                [self._docker, "ps", "-aq", *extra],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.split() if line]

    def _managed_with_label(self, value_label: str, filter_label: str = "cairn.managed=1") -> list[tuple[str, str]]:
        fmt = "{{.ID}}\t{{.Label " + '"' + value_label + '"' + "}}"
        try:
            result = subprocess.run(
                [self._docker, "ps", "-a", "--filter", f"label={filter_label}", "--format", fmt],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        pairs: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            cid, _, label = line.partition("\t")
            pairs.append((cid.strip(), label.strip()))
        return pairs

    def _rm(self, ids: list[str]) -> None:
        if not ids:
            return
        try:
            subprocess.run(
                [self._docker, "rm", "-f", *ids],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            LOG.warning("failed to remove containers ids=%s", ids)
