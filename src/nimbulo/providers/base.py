from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Callable

from ..models import LoginResult, ProviderStatus

OutputCb = Callable[[str], None] | None


class Provider(ABC):
    key: str = ""
    label: str = ""
    cli_tool: str | None = None  # e.g. "aws", "gcloud"; None for HTTP-only providers

    # UI hints — drive whether the GUI/TUI shows profile/secondary pickers.
    # Set to None for providers where the concept doesn't apply (Vultr, DO).
    profile_label: str | None = None  # e.g. "Profile" (AWS), "Configuration" (GCP)
    secondary_label: str | None = None  # e.g. "Project" (GCP), "Subscription" (Azure)

    @abstractmethod
    def status(self, profile: str | None = None) -> ProviderStatus: ...

    def profiles(self) -> list[str]:
        return self.status().profiles

    def login_methods(self) -> list[str]:
        """Names of login methods this provider supports, in preferred order."""
        return ["default"]

    @abstractmethod
    def login(self, method: str = "default", on_output: OutputCb = None, **kwargs: Any) -> LoginResult: ...

    def logout(self, profile: str | None = None) -> LoginResult:
        return LoginResult(ok=False, message="logout not supported for this provider")

    def forget(self) -> LoginResult:
        """Explicit destructive removal of stored credentials.

        Default: same as logout() for session-based providers (AWS/GCP/Azure).
        Vultr/DO override this to delete their keyring entry.
        """
        return self.logout()

    def cli_available(self) -> bool:
        return self.cli_tool is None or shutil.which(self.cli_tool) is not None

    # --- secondary (project / subscription) — overridden by GCP / Azure -----

    def list_secondary(self) -> list[tuple[str, str]]:
        """Return (id, display_name) pairs for the secondary picker."""
        return []

    def set_secondary(self, sid: str) -> LoginResult:
        return LoginResult(ok=False, message=f"{self.label} has no secondary selector")

    @staticmethod
    def _run(
        cmd: list[str], timeout: int = 60, input_text: str | None = None, no_tty: bool = False
    ) -> tuple[int, str, str]:
        """Run a command and capture output.

        ``no_tty=True`` detaches the child from the controlling terminal (new session,
        stdin closed) so a CLI that wants to prompt (e.g. for a key passphrase, via
        /dev/tty) fails fast instead of hanging or garbling the UI.
        """
        extra: dict = {}
        if no_tty:
            extra["start_new_session"] = True
            if input_text is None:
                extra["stdin"] = subprocess.DEVNULL
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input_text,
                **extra,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError as exc:
            return 127, "", str(exc)
        except subprocess.TimeoutExpired as exc:
            return 124, "", f"timeout after {timeout}s: {exc}"

    @staticmethod
    def _popen_stream(
        cmd: list[str],
        on_output: OutputCb = None,
        timeout: int = 600,
    ) -> tuple[int, str]:
        """Run a command, streaming combined stdout+stderr line-by-line to on_output.

        Returns (returncode, full_combined_output). Used for `aws sso login` and
        `gcloud auth login` so device codes / fallback URLs appear in the UI log
        while the subprocess is still running.
        """
        sink: Callable[[str], None] = on_output if on_output is not None else (lambda _line: None)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            sink(f"error: {exc}")
            return 127, str(exc)
        assert proc.stdout is not None
        collected: list[str] = []
        try:
            for raw in proc.stdout:
                line = raw.rstrip()
                collected.append(line)
                sink(line)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            sink(f"timeout after {timeout}s")
            return 124, "\n".join(collected)
        return proc.returncode, "\n".join(collected)
