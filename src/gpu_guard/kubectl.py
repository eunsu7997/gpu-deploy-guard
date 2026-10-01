"""Bounded, read-only kubectl adapter; no arbitrary command execution API."""

from dataclasses import dataclass
import shutil
import subprocess
from typing import Protocol


QUERIES = {
    "context": ("config", "current-context"),
    "nodes": ("get", "nodes", "-o", "json"),
    "pods": ("get", "pods", "-n", "kube-system", "-o", "json"),
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class CommandRunner(Protocol):
    def available(self) -> bool: ...

    def run(self, query: str, *, context: str | None = None) -> CommandResult: ...


class KubectlRunner:
    """Execute only predefined reads with shell disabled and a timeout."""

    def __init__(self, timeout: int = 10):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.executable = shutil.which("kubectl")

    def available(self) -> bool:
        return self.executable is not None

    def run(self, query: str, *, context: str | None = None) -> CommandResult:
        if query not in QUERIES:
            raise ValueError(f"Unsupported read-only query: {query}")
        if query != "context" and (not isinstance(context, str) or not context.strip()):
            raise ValueError("API queries require an explicit current context")
        if not self.executable:
            return CommandResult(1, stderr="kubectl executable not found")
        command = [self.executable, *QUERIES[query]]
        if query != "context":
            command.extend([f"--context={context}", f"--request-timeout={self.timeout}s"])
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=self.timeout, check=False, shell=False,
            )
            return CommandResult(result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired:
            return CommandResult(1, stderr=f"kubectl {query} timed out after {self.timeout}s", timed_out=True)
        except OSError as exc:
            return CommandResult(1, stderr=str(exc))
