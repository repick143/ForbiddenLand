"""Start the backend and frontend with one shared local API configuration."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 9092


def _port_value(value: str | int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid API port: {value!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError("API port must be between 1 and 65535")
    return port


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the ForbiddenLand backend and frontend with a shared API target."
    )
    parser.add_argument(
        "--api-port",
        type=_port_value,
        default=None,
        help="Backend port (default: FORBIDDENLAND_API_PORT or 9092).",
    )
    parser.add_argument(
        "--api-host",
        default=None,
        help=f"Backend host (default: FORBIDDENLAND_API_HOST or {DEFAULT_API_HOST}).",
    )
    return parser.parse_args(argv)


def resolve_api_port(value: int | None = None) -> int:
    """Resolve and validate the one port shared by both development processes."""

    if value is not None:
        return _port_value(value)
    return _port_value(os.environ.get("FORBIDDENLAND_API_PORT", DEFAULT_API_PORT))


def resolve_api_host(value: str | None = None) -> str:
    """Resolve the backend bind host without losing an explicit environment override."""

    host = (
        value if value is not None else os.environ.get("FORBIDDENLAND_API_HOST", DEFAULT_API_HOST)
    )
    normalized = host.strip()
    if not normalized:
        raise ValueError("API host must not be empty")
    return normalized


def proxy_host(host: str, *, bracket_ipv6: bool = True) -> str:
    """Use a loopback address for the browser proxy when the server binds all interfaces."""

    normalized = resolve_api_host(host)
    if normalized in {"0.0.0.0", "::"}:
        return DEFAULT_API_HOST
    if bracket_ipv6 and ":" in normalized and not normalized.startswith("["):
        return f"[{normalized}]"
    return normalized


def shared_environment(host: str, port: int) -> dict[str, str]:
    """Return child-process variables that keep the backend and Vite proxy aligned."""

    normalized_host = resolve_api_host(host)
    normalized_port = _port_value(port)
    target_host = proxy_host(normalized_host)
    environment = os.environ.copy()
    environment["FORBIDDENLAND_API_HOST"] = normalized_host
    environment["FORBIDDENLAND_API_PORT"] = str(normalized_port)
    environment["FORBIDDENLAND_API_PROXY_TARGET"] = f"http://{target_host}:{normalized_port}"
    return environment


def _api_is_healthy(host: str, port: int) -> bool:
    url = f"http://{proxy_host(host)}:{port}/api/v1/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=0.5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("service") == "forbiddenland-api"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _npm_executable() -> str:
    command = "npm.cmd" if os.name == "nt" else "npm"
    executable = shutil.which(command)
    if executable is None:
        raise RuntimeError("npm was not found on PATH; install Node 22.14.x first")
    return executable


def _spawn_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop_process(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_dev(*, host: str, port: int) -> int:
    environment = shared_environment(host, port)
    processes: list[tuple[str, subprocess.Popen[object]]] = []
    spawn_options = _spawn_options()
    try:
        if _api_is_healthy(host, port):
            print(f"Reusing healthy backend at {environment['FORBIDDENLAND_API_PROXY_TARGET']}")
        else:
            backend = subprocess.Popen(
                [sys.executable, "-m", "forbiddenland.api.app"],
                cwd=PROJECT_ROOT,
                env=environment,
                **spawn_options,
            )
            processes.append(("backend", backend))

        frontend = subprocess.Popen(
            [_npm_executable(), "run", "dev"],
            cwd=FRONTEND_ROOT,
            env=environment,
            **spawn_options,
        )
        processes.append(("frontend", frontend))
        print(f"Shared API target: {environment['FORBIDDENLAND_API_PROXY_TARGET']}")
        while True:
            for name, process in processes:
                return_code = process.poll()
                if return_code is not None:
                    print(f"{name} process exited with code {return_code}")
                    return return_code if return_code != 0 else 0
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 130
    finally:
        for _, process in reversed(processes):
            _stop_process(process)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        port = resolve_api_port(args.api_port)
        host = resolve_api_host(args.api_host)
        return run_dev(host=host, port=port)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Development startup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
