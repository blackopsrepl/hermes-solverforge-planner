"""Find, install and run the solverforge-planner engine binary.

The engine is built from ``engine/`` in this repository by its own GitHub
Actions release workflow, with a build attestation per binary. Installing
downloads the binary for this platform from that release and refuses it
unless its SHA-256 matches the checksum pinned below, so the reviewed commit
fixes exactly which binary runs. Nothing here updates itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ENGINE_VERSION = "0.1.0"
REPO = "blackopsrepl/hermes-solverforge-planner"
RELEASE_URL = f"https://github.com/{REPO}/releases/download/engine-v{ENGINE_VERSION}"
# Filled from the release's SHA256SUMS when the release is cut; an empty
# entry means that platform has no published binary for this version.
CHECKSUMS: dict[str, str] = {
    "x86_64-unknown-linux-musl": "",
    "aarch64-unknown-linux-musl": "",
    "x86_64-apple-darwin": "",
    "aarch64-apple-darwin": "",
    "x86_64-pc-windows-gnu": "",
}
MAX_BINARY_BYTES = 64 * 1024 * 1024


class EngineError(Exception):
    """A refusal or failure from the engine, carrying its machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def target_triple() -> str | None:
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(machine)
    if arch is None:
        return None
    if sys.platform.startswith("linux"):
        return f"{arch}-unknown-linux-musl"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    if sys.platform == "win32" and arch == "x86_64":
        return "x86_64-pc-windows-gnu"
    return None


def asset_name(triple: str) -> str:
    return f"solverforge-planner-{triple}" + (".exe" if "windows" in triple else "")


@dataclass(frozen=True)
class Engine:
    """Locates and runs the engine. ``data_dir`` is the plugin data directory."""

    data_dir: Path
    override: str = ""
    timeout_s: int = 120

    @property
    def installed_path(self) -> Path:
        suffix = ".exe" if sys.platform == "win32" else ""
        return self.data_dir / "bin" / f"solverforge-planner-{ENGINE_VERSION}{suffix}"

    def binary(self) -> Path | None:
        """An explicit setting wins, then the pinned install, then PATH (development)."""
        if self.override:
            path = Path(os.path.expanduser(self.override))
            return path if os.access(path, os.X_OK) else None
        if os.access(self.installed_path, os.X_OK):
            return self.installed_path
        found = shutil.which("solverforge-planner")
        return Path(found) if found else None

    def version(self) -> str | None:
        binary = self.binary()
        if binary is None:
            return None
        try:
            out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
            return json.loads(out.stdout)["data"]["version"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            return None

    def solve(self, problem: dict[str, Any]) -> dict[str, Any]:
        binary = self.binary()
        if binary is None:
            raise EngineError("not_installed", install_hint())
        budget = int(problem.get("settings", {}).get("solve_seconds", 5)) + 30
        try:
            proc = subprocess.run([str(binary), "solve"], input=json.dumps(problem), capture_output=True,
                                  text=True, timeout=min(self.timeout_s, budget))
        except subprocess.TimeoutExpired:
            raise EngineError("timeout", "the engine did not finish in time; it was stopped")
        except OSError as exc:
            raise EngineError("exec_failed", str(exc))
        raw = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
        try:
            envelope = json.loads(raw)
        except ValueError:
            raise EngineError("bad_output", (raw or f"exit {proc.returncode}").strip()[:500])
        if proc.returncode == 0 and envelope.get("status") == "ok":
            return envelope["data"]
        raise EngineError(str(envelope.get("code") or "engine_error"), str(envelope.get("message") or raw)[:500])

    def install(self, log: Callable[[str], None] = print) -> Path:
        """Download, verify and install the pinned engine for this platform."""
        triple = target_triple()
        if triple is None:
            raise EngineError("unsupported_platform", f"no engine build for {sys.platform}/{platform.machine()}")
        expected = CHECKSUMS.get(triple, "")
        if not expected:
            raise EngineError("unsupported_platform", f"engine {ENGINE_VERSION} has no published {triple} build")
        url = f"{RELEASE_URL}/{asset_name(triple)}"
        log(f"downloading {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "hermes-solverforge-planner"})
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read(MAX_BINARY_BYTES + 1)
        if len(body) > MAX_BINARY_BYTES:
            raise EngineError("bad_download", "engine download is larger than expected")
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise EngineError("checksum_mismatch", f"expected sha256 {expected}, got {actual}; not installed")
        target = self.installed_path
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
        log(f"installed {target} (sha256 {actual[:12]}…)")
        return target


def install_hint() -> str:
    return ("The SolverForge engine is not installed. Run `hermes solverforge-planner install`, "
            "type `/planner install`, or press Install on the Desktop Planner page.")
