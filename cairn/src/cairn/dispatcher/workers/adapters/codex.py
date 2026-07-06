from __future__ import annotations

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.workers.adapters._curl import build_verbose_curl_healthcheck, expand_env, render_curl_command
from cairn.dispatcher.workers.base import DriverResult, RegexSessionDriver


class CodexDriver(RegexSessionDriver):
    type_name = "codex"

    def build_healthcheck(self, worker: WorkerConfig) -> list[str]:
        if self._uses_local_auth(worker):
            return ["codex", "--version"]
        return [
            "curl",
            "-sS",
            "--fail",
            "-o",
            "/dev/null",
            self._healthcheck_url(worker),
            *self._healthcheck_headers(worker),
            "-d",
            self._healthcheck_payload(worker),
        ]

    def build_startup_healthcheck(self, worker: WorkerConfig) -> list[str]:
        if self._uses_local_auth(worker):
            return ["codex", "--version"]
        return build_verbose_curl_healthcheck(
            self._healthcheck_url(worker),
            headers=self._healthcheck_headers(worker),
            payload=self._healthcheck_payload(worker),
        )

    def describe_startup_healthcheck(self, worker: WorkerConfig) -> str:
        if self._uses_local_auth(worker):
            return "codex --version"
        return render_curl_command(
            self._healthcheck_url(worker),
            headers=[
                "-H",
                expand_env("Authorization: Bearer $OPENAI_API_KEY"),
                "-H",
                "content-type: application/json",
            ],
            payload=self._healthcheck_payload(worker),
        )

    def build_execute(self, worker: WorkerConfig, prompt: str, session: str | None) -> DriverResult:
        env = worker.env
        argv = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if env.get("CODEX_MODEL"):
            argv.extend(["--model", env["CODEX_MODEL"]])
        if not self._uses_local_auth(worker):
            argv.extend(
                [
                    "-c",
                    'model_provider="cairn"',
                    "-c",
                    'model_providers.cairn.name="cairn"',
                    "-c",
                    'model_providers.cairn.wire_api="responses"',
                    "-c",
                    f'model_providers.cairn.base_url="{env["CODEX_BASE_URL"]}"',
                    "-c",
                    'model_providers.cairn.env_key="OPENAI_API_KEY"',
                ]
            )
        argv.extend(
            [
                "-c",
                'model_reasoning_effort="high"',
                "--",
                prompt,
            ]
        )
        return DriverResult(argv=argv)

    def build_conclude(self, worker: WorkerConfig, prompt: str, session: str) -> list[str]:
        env = worker.env
        argv = [
            "codex",
            "exec",
            "resume",
            session,
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if env.get("CODEX_MODEL"):
            argv.extend(["--model", env["CODEX_MODEL"]])
        if not self._uses_local_auth(worker):
            argv.extend(
                [
                    "-c",
                    'model_provider="cairn"',
                    "-c",
                    'model_providers.cairn.name="cairn"',
                    "-c",
                    'model_providers.cairn.wire_api="responses"',
                    "-c",
                    f'model_providers.cairn.base_url="{env["CODEX_BASE_URL"]}"',
                    "-c",
                    'model_providers.cairn.env_key="OPENAI_API_KEY"',
                ]
            )
        argv.extend(
            [
                "-c",
                'model_reasoning_effort="high"',
                "--",
                prompt,
            ]
        )
        return argv

    @staticmethod
    def _uses_local_auth(worker: WorkerConfig) -> bool:
        return worker.env.get("CODEX_AUTH_MODE") == "local"

    @staticmethod
    def _healthcheck_url(worker: WorkerConfig) -> str:
        return f"{worker.env['CODEX_BASE_URL']}/responses"

    @staticmethod
    def _healthcheck_headers(worker: WorkerConfig) -> list[str]:
        return [
            "-H",
            f"Authorization: Bearer {worker.env['OPENAI_API_KEY']}",
            "-H",
            "content-type: application/json",
        ]

    @staticmethod
    def _healthcheck_payload(worker: WorkerConfig) -> str:
        return (
            '{"input":[{"content":"ping","role":"user"}],'
            '"model":"'
            + worker.env["CODEX_MODEL"]
            + '","stream":false}'
        )
