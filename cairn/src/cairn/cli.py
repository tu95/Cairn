from pathlib import Path
import os
import threading
import time

import click
import requests
import uvicorn

from cairn.dispatcher.logging import configure_logging
from cairn.dispatcher.scheduler.loop import DispatcherLoop
from cairn.server import db


@click.group()
def main():
    """Cairn - Fact-graph based collaborative exploration protocol."""


@main.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host")
@click.option("--port", default=8000, show_default=True, help="Bind port")
@click.option(
    "--db-path",
    type=click.Path(),
    default=str(db.DEFAULT_DB),
    show_default=True,
    help="SQLite database path",
)
@click.option("--log-level", default="info", show_default=True, help="Uvicorn log level")
@click.option("--access-log/--no-access-log", default=True, show_default=True, help="Enable Uvicorn access log")
def serve(host: str, port: int, db_path: str, log_level: str, access_log: bool):
    """Start the Cairn API server."""
    db.configure(Path(db_path))
    from cairn.server.app import app

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        access_log=access_log,
    )


@main.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Dispatcher config path",
)
@click.option("--once", is_flag=True, help="Run one scheduling iteration and exit")
@click.option(
    "--startup-healthcheck-only",
    is_flag=True,
    help="Run startup worker healthchecks and exit",
)
@click.option("--log-level", default="INFO", show_default=True, help="Log level")
def dispatch(config_path: Path, once: bool, startup_healthcheck_only: bool, log_level: str):
    """Run the Cairn dispatcher."""
    configure_logging(log_level, bare=startup_healthcheck_only)
    loop = DispatcherLoop(config_path)
    try:
        if startup_healthcheck_only:
            loop.run_startup_healthchecks_only()
            return
        loop.run(once=once)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("dispatch.yaml"),
    show_default=True,
    help="Dispatcher config path",
)
@click.option("--host", default="0.0.0.0", show_default=True, help="Server bind host")
@click.option("--port", default=8000, show_default=True, help="Server bind port")
@click.option(
    "--db-path",
    type=click.Path(),
    default=str(db.DEFAULT_DB),
    show_default=True,
    help="SQLite database path",
)
@click.option("--log-level", default="INFO", show_default=True, help="Dispatcher log level")
@click.option("--uvicorn-log-level", default="warning", show_default=True, help="Uvicorn log level")
@click.option("--access-log/--no-access-log", default=False, show_default=True, help="Enable Uvicorn access log")
@click.option("--startup-timeout", default=20, show_default=True, help="Seconds to wait for the server to become healthy")
def launch(
    config_path: Path,
    host: str,
    port: int,
    db_path: str,
    log_level: str,
    uvicorn_log_level: str,
    access_log: bool,
    startup_timeout: int,
):
    """Start the local Cairn server and dispatcher together."""
    configure_logging(log_level)
    db.configure(Path(db_path))
    os.environ["CAIRN_DISPATCH_CONFIG"] = str(config_path)
    from cairn.server.app import app
    from cairn.server.routers import worker_config

    server_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=uvicorn_log_level.lower(),
        access_log=access_log,
    )
    server = uvicorn.Server(server_config)
    server_thread = threading.Thread(target=server.run, name="cairn-uvicorn", daemon=True)
    server_thread.start()

    health_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    health_url = f"http://{health_host}:{port}/projects"
    try:
        _wait_for_server(health_url, startup_timeout)
        if host == "0.0.0.0":
            click.echo(f"Cairn server: http://0.0.0.0:{port} (all interfaces)")
        else:
            click.echo(f"Cairn server: http://{host}:{port}")
        click.echo(f"Cairn dispatcher config: {config_path}")
        loop = DispatcherLoop(config_path)
        worker_config.set_dispatcher_reload_callback(loop.request_reload)
        while True:
            should_restart = loop.run()
            if not should_restart:
                break
            click.echo("Reloading dispatcher with latest config...")
            loop = DispatcherLoop(config_path)
            worker_config.set_dispatcher_reload_callback(loop.request_reload)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        worker_config.set_dispatcher_reload_callback(None)
        server.should_exit = True
        server_thread.join(timeout=5)


def _wait_for_server(url: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=1)
            if response.status_code < 500:
                return
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(0.2)
    detail = f": {last_error}" if last_error is not None else ""
    raise RuntimeError(f"server did not become healthy at {url} within {timeout_seconds}s{detail}")
