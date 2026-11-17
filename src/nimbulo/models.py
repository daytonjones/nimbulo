from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


class AuthState(str, Enum):
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    UNAUTHENTICATED = "unauthenticated"
    UNKNOWN = "unknown"
    NOT_INSTALLED = "not_installed"
    ERROR = "error"


@dataclass
class Identity:
    """Who you are on a given provider, right now."""

    account: str | None = None        # AWS account id, GCP project, DO/Vultr account email
    user: str | None = None           # IAM user/role ARN, gcloud account, DO/Vultr email
    expires_at: datetime | None = None  # token/session expiry if known (tz-aware)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderStatus:
    provider: str
    state: AuthState
    identity: Identity | None = None
    active_profile: str | None = None
    profiles: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.state == AuthState.AUTHENTICATED


@dataclass
class LoginResult:
    ok: bool
    message: str = ""
    output: str = ""


# --- expiration formatting --------------------------------------------------


class Urgency(str, Enum):
    OK = "ok"        # >30m left
    SOON = "soon"    # <=30m left
    EXPIRED = "expired"


def format_expires(dt: datetime | None, now: datetime | None = None) -> tuple[str, Urgency]:
    """Render an expiry timestamp as 'in 4h 12m' / 'expired 3m ago' plus urgency."""
    if dt is None:
        return "", Urgency.OK
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    delta = dt - now
    total = int(delta.total_seconds())
    if total < 0:
        return f"expired {_pretty_delta(-total)} ago", Urgency.EXPIRED
    label = f"in {_pretty_delta(total)}"
    urgency = Urgency.SOON if total <= 30 * 60 else Urgency.OK
    return label, urgency


def _pretty_delta(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"
