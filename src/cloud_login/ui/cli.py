from __future__ import annotations

import getpass
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .. import providers
from ..models import AuthState, ProviderStatus, Urgency, format_expires

console = Console()
app = typer.Typer(
    help="Manage logins across AWS, GCP, Azure, Vultr, and Digital Ocean.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)

PROVIDER_HELP = "aws|gcp|azure|vultr|digitalocean"


# --- helpers ----------------------------------------------------------------


def _state_style(state: AuthState) -> str:
    return {
        AuthState.AUTHENTICATED: "bold green",
        AuthState.EXPIRED: "yellow",
        AuthState.UNAUTHENTICATED: "red",
        AuthState.NOT_INSTALLED: "dim",
        AuthState.ERROR: "bold red",
        AuthState.UNKNOWN: "white",
    }[state]


_URGENCY_STYLE = {Urgency.OK: "green", Urgency.SOON: "yellow", Urgency.EXPIRED: "red"}


def _render_status(statuses: list[ProviderStatus]) -> Table:
    table = Table(title="Cloud Login Status", show_lines=False)
    table.add_column("Provider", style="bold")
    table.add_column("State")
    table.add_column("Account / Project")
    table.add_column("User")
    table.add_column("Active profile")
    table.add_column("Expires")
    table.add_column("Notes", overflow="fold")
    for s in statuses:
        ident = s.identity
        expires_text = "-"
        if ident and ident.expires_at:
            label, urg = format_expires(ident.expires_at)
            expires_text = f"[{_URGENCY_STYLE[urg]}]{label}[/]"
        table.add_row(
            s.provider,
            f"[{_state_style(s.state)}]{s.state.value}[/]",
            ident.account if ident and ident.account else "-",
            ident.user if ident and ident.user else "-",
            s.active_profile or "-",
            expires_text,
            s.message or "",
        )
    return table


# --- root callback: default to GUI ------------------------------------------


@app.callback()
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        # No subcommand → launch GUI
        from .gui import run as run_gui

        sys.exit(run_gui())


# --- subcommands ------------------------------------------------------------


@app.command("gui")
def gui_cmd() -> None:
    """Launch the PySide6 GUI."""
    from .gui import run as run_gui

    raise typer.Exit(run_gui())


@app.command("tui")
def tui_cmd() -> None:
    """Launch the Textual TUI."""
    from .tui import run as run_tui

    raise typer.Exit(run_tui())


@app.command("menubar")
def menubar_cmd() -> None:
    """Launch the macOS menu-bar status app (resident process)."""
    from .menubar import run as run_menubar

    raise typer.Exit(run_menubar())


def _resolve_cloud_login_program() -> list[str]:
    """Return argv for invoking this `cloud-login` reliably from launchd.

    Preference order:
      1. The path *this* process was invoked from (sys.argv[0]) — most accurate
         because it's literally how the user just ran us.
      2. `shutil.which("cloud-login")` — works if the entry point is on PATH.
      3. `<python> -m cloud_login` — last-ditch fallback.
    """
    import os
    import shutil

    candidate = sys.argv[0] if sys.argv else ""
    if candidate:
        candidate = os.path.realpath(candidate)
        if os.path.isabs(candidate) and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return [candidate, "menubar"]
    on_path = shutil.which("cloud-login")
    if on_path:
        return [on_path, "menubar"]
    return [sys.executable, "-m", "cloud_login", "menubar"]


def _kill_running_menubar() -> None:
    """Best-effort kill of any running `cloud-login menubar` processes.

    Covers both the `cloud-login menubar` script invocation and the
    `python -m cloud_login menubar` fallback. Run before re-loading the
    LaunchAgent so we don't end up with stacked instances.
    """
    import subprocess

    for pattern in ("cloud-login menubar", "cloud_login.*menubar"):
        subprocess.run(["pkill", "-f", pattern], capture_output=True)


@app.command("autostart")
def autostart_cmd(
    action: str = typer.Argument("install", help="install | uninstall | status"),
) -> None:
    """Manage a launchd LaunchAgent so the menu-bar app starts at macOS login."""
    import os
    import subprocess
    from pathlib import Path

    if sys.platform != "darwin":
        console.print("[red]autostart is macOS-only[/]")
        raise typer.Exit(2)

    plist_path = Path.home() / "Library/LaunchAgents/com.cloud-login.menubar.plist"
    label = "com.cloud-login.menubar"

    if action == "status":
        exists = plist_path.exists()
        console.print(
            f"plist: {'[green]installed[/]' if exists else '[yellow]not installed[/]'}  ({plist_path})"
        )
        rc = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
        if rc.returncode == 0:
            console.print("[green]launchd: loaded[/]")
        else:
            console.print("[yellow]launchd: not loaded[/]")
        return

    if action == "uninstall":
        subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)
        _kill_running_menubar()
        if plist_path.exists():
            plist_path.unlink()
            console.print(f"[green]removed {plist_path}[/]")
        else:
            console.print("[yellow]already uninstalled[/]")
        return

    if action != "install":
        console.print(f"[red]unknown action: {action}[/]  (use install | uninstall | status)")
        raise typer.Exit(2)

    prog_args = _resolve_cloud_login_program()
    arg_xml = "\n        ".join(f"<string>{a}</string>" for a in prog_args)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        {arg_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>/tmp/cloud-login-menubar.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/cloud-login-menubar.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}</string>
    </dict>
</dict>
</plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist)
    console.print(f"[green]wrote {plist_path}[/]")
    console.print(f"[dim]program: {' '.join(prog_args)}[/]")

    # Idempotent re-install: kill any existing menubar (launchd-started OR
    # manually-started) and unload the previous LaunchAgent before reloading.
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    _kill_running_menubar()
    rc = subprocess.run(
        ["launchctl", "load", "-w", str(plist_path)],
        capture_output=True,
        text=True,
    )
    if rc.returncode == 0:
        console.print("[green]registered with launchd; will start at next login[/]")
        console.print("[dim]menubar is starting now[/]")
    else:
        console.print(f"[yellow]plist installed but launchctl load failed:[/] {rc.stderr.strip()}")
        console.print(f"[yellow]load manually with:[/] launchctl load -w {plist_path}")


@app.command("status")
def status_cmd(
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="aws|gcp|azure|vultr|digitalocean"),
    profile: Optional[str] = typer.Option(None, "--profile", help="profile/configuration to inspect"),
) -> None:
    """Show authentication status for all providers (or one)."""
    targets = [providers.get(provider)] if provider else providers.all_providers()
    results = [t.status(profile=profile) for t in targets]
    console.print(_render_status(results))


@app.command("profiles")
def profiles_cmd(provider: str = typer.Argument(..., help="aws|gcp|azure|vultr|digitalocean")) -> None:
    """List configured profiles/configurations for a provider."""
    p = providers.get(provider)
    names = p.profiles()
    if not names:
        console.print(f"[yellow]no profiles configured for {p.label}[/]")
        return
    for n in names:
        console.print(n)


@app.command("login")
def login_cmd(
    provider: str = typer.Argument(..., help="aws|gcp|azure|vultr|digitalocean"),
    method: Optional[str] = typer.Option(None, "--method", "-m", help="sso|access-key|user|adc|user+adc|api-key|token|device-code"),
    profile: Optional[str] = typer.Option(None, "--profile", help="profile (AWS) or configuration (GCP)"),
    project: Optional[str] = typer.Option(None, "--project", help="GCP project to activate after login"),
    subscription: Optional[str] = typer.Option(None, "--subscription", help="Azure subscription to activate after login"),
    tenant: Optional[str] = typer.Option(None, "--tenant", help="Azure tenant ID (for `az login --tenant`)"),
    region: Optional[str] = typer.Option(None, "--region", help="AWS region (with --method access-key)"),
    token: Optional[str] = typer.Option(None, "--token", help="API key/token (Vultr/DO). Omit to be prompted."),
) -> None:
    """Re-authenticate against a provider."""
    p = providers.get(provider)
    method = method or p.login_methods()[0]
    kwargs: dict = {}
    if provider == "aws":
        if method == "sso":
            if not profile:
                profile = typer.prompt("AWS profile")
            kwargs["profile"] = profile
        elif method == "access-key":
            kwargs["profile"] = profile or typer.prompt("AWS profile", default="default")
            kwargs["access_key_id"] = typer.prompt("AWS_ACCESS_KEY_ID")
            kwargs["secret_access_key"] = getpass.getpass("AWS_SECRET_ACCESS_KEY: ")
            if region:
                kwargs["region"] = region
    elif provider == "gcp":
        if profile:
            kwargs["configuration"] = profile
        if project:
            kwargs["project"] = project
    elif provider == "azure":
        if tenant:
            kwargs["tenant"] = tenant
        if subscription:
            kwargs["subscription"] = subscription
    elif provider in {"vultr", "digitalocean"}:
        # Only set kwargs["token"] if the user supplied one; otherwise let
        # provider.login() resolve from env → CLI config file → keyring.
        if token:
            kwargs["token"] = token
    result = p.login(method=method, on_output=lambda line: console.print(f"[dim]{line}[/]"), **kwargs)
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.message}[/]")
    if not result.ok:
        raise typer.Exit(1)


@app.command("projects")
def projects_cmd(
    provider: str = typer.Argument("gcp", help="gcp (only provider with a project list)"),
) -> None:
    """List projects visible to the current account (GCP only)."""
    p = providers.get(provider)
    if not hasattr(p, "list_projects"):
        console.print(f"[yellow]{p.label} has no project list[/]")
        raise typer.Exit(2)
    projects = p.list_projects()  # type: ignore[attr-defined]
    if not projects:
        console.print("[yellow]no projects visible (are you authenticated?)[/]")
        return
    table = Table(title=f"{p.label} projects")
    table.add_column("Project ID", style="bold")
    table.add_column("Name")
    for pid, name in projects:
        table.add_row(pid, name)
    console.print(table)


@app.command("set-project")
def set_project_cmd(
    project_id: str = typer.Argument(..., help="GCP project ID to activate"),
) -> None:
    """Set the active GCP project (`gcloud config set project`)."""
    p = providers.get("gcp")
    result = p.set_project(project_id)  # type: ignore[attr-defined]
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.message}[/]")
    if not result.ok:
        raise typer.Exit(1)


@app.command("subscriptions")
def subscriptions_cmd() -> None:
    """List Azure subscriptions visible to the current account."""
    p = providers.get("azure")
    subs = p.list_secondary()
    if not subs:
        console.print("[yellow]no subscriptions visible (are you authenticated?)[/]")
        return
    table = Table(title=f"{p.label} subscriptions")
    table.add_column("Subscription ID", style="bold")
    table.add_column("Name")
    for sid, name in subs:
        table.add_row(sid, name)
    console.print(table)


@app.command("set-subscription")
def set_subscription_cmd(
    subscription: str = typer.Argument(..., help="Azure subscription ID or name to activate"),
) -> None:
    """Set the active Azure subscription (`az account set --subscription`)."""
    p = providers.get("azure")
    result = p.set_secondary(subscription)
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.message}[/]")
    if not result.ok:
        raise typer.Exit(1)


@app.command("logout")
def logout_cmd(provider: str = typer.Argument(..., help="aws|gcp|azure|vultr|digitalocean")) -> None:
    """Sign out (revoke session) for a provider. For Vultr/DO this is a no-op; use `forget` to delete the stored key."""
    p = providers.get(provider)
    result = p.logout()
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.message}[/]")
    if not result.ok:
        raise typer.Exit(1)


@app.command("forget")
def forget_cmd(provider: str = typer.Argument(..., help="aws|gcp|azure|vultr|digitalocean")) -> None:
    """Delete stored credentials (e.g. remove Vultr/DO API key from keyring)."""
    p = providers.get(provider)
    result = p.forget()
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.message}[/]")
    if not result.ok:
        raise typer.Exit(1)


@app.command("set-key")
def set_key_cmd(
    provider: str = typer.Argument(..., help="vultr|digitalocean"),
    token: Optional[str] = typer.Option(None, "--token", help="omit to be prompted"),
) -> None:
    """Store/replace an API key for Vultr or Digital Ocean (alias for `login`)."""
    if provider not in {"vultr", "digitalocean"}:
        console.print("[red]set-key only applies to vultr and digitalocean[/]")
        raise typer.Exit(2)
    if not token:
        token = getpass.getpass(f"{provider} API token: ")
    p = providers.get(provider)
    result = p.login(token=token)
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.message}[/]")
    if not result.ok:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
