from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from .. import providers
from ..models import AuthState, ProviderStatus, Urgency, format_expires

console = Console()
app = typer.Typer(
    help="Manage logins across AWS, GCP, Azure, OCI, Vultr, and Digital Ocean.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)

PROVIDER_HELP = "aws|gcp|azure|oci|vultr|digitalocean"


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
    table = Table(title="Nimbulo Status", show_lines=False)
    table.add_column("Provider", style="bold")
    table.add_column("State")
    table.add_column("Account / Project")
    table.add_column("User")
    table.add_column("Active profile")
    table.add_column("Expires")
    table.add_column("Notes", overflow="fold")
    for s in statuses:
        ident = s.identity
        # OCI values come from ~/.oci/config and oci CLI output: never parse them as markup.
        esc = escape if s.provider == "oci" else (lambda x: x)
        expires_text = "-"
        if ident and ident.expires_at:
            label, urg = format_expires(ident.expires_at)
            expires_text = f"[{_URGENCY_STYLE[urg]}]{label}[/]"
        table.add_row(
            s.provider,
            f"[{_state_style(s.state)}]{s.state.value}[/]",
            esc(ident.account) if ident and ident.account else "-",
            esc(ident.user) if ident and ident.user else "-",
            esc(s.active_profile) if s.active_profile else "-",
            expires_text,
            esc(s.message) if s.message else "",
        )
    return table


# --- root callback: default to GUI ------------------------------------------


@app.callback()
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        # No subcommand → launch GUI
        from .gui import run as run_gui

        sys.exit(run_gui())


def _oci_login_kwargs(p, method, profile, region, user, tenancy, key_file, generate_key, overwrite) -> dict:
    """Build OCI login kwargs; prompt for missing non-secret api-key fields (no secrets are prompted)."""
    kwargs: dict = {}
    if profile:
        kwargs["profile"] = profile
    if region:
        kwargs["region"] = region
    if method == "session" and overwrite:
        kwargs["overwrite"] = True  # allow converting an existing API-key profile to a session profile
    if method != "api-key":
        return kwargs
    defaults = p.api_key_defaults()
    kwargs["profile"] = profile or typer.prompt("OCI profile name", default=defaults.get("profile", "DEFAULT"))
    kwargs["user"] = user or typer.prompt("User OCID")
    kwargs["tenancy"] = tenancy or typer.prompt("Tenancy OCID")
    kwargs["region"] = region or typer.prompt("Region", default=defaults.get("region", "us-ashburn-1"))
    if not key_file and not generate_key:
        if typer.confirm("Generate a new API key pair?", default=True):
            generate_key = True
        else:
            key_file = typer.prompt("Path to existing private key file")
    if key_file:
        kwargs["key_file"] = key_file
    kwargs["generate_key"] = generate_key
    kwargs["overwrite"] = overwrite
    return kwargs


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


def _resolve_nimbulo_program() -> list[str]:
    """Return argv for invoking this `nimbulo` reliably from launchd.

    Preference order:
      1. The path *this* process was invoked from (sys.argv[0]) — most accurate
         because it's literally how the user just ran us.
      2. `shutil.which("nimbulo")` — works if the entry point is on PATH.
      3. `<python> -m nimbulo` — last-ditch fallback.
    """
    import os
    import shutil

    candidate = sys.argv[0] if sys.argv else ""
    if candidate:
        candidate = os.path.realpath(candidate)
        if os.path.isabs(candidate) and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return [candidate, "menubar"]
    on_path = shutil.which("nimbulo")
    if on_path:
        return [on_path, "menubar"]
    return [sys.executable, "-m", "nimbulo", "menubar"]


def _kill_running_menubar() -> None:
    """Best-effort kill of any running `nimbulo menubar` processes.

    Covers both the `nimbulo menubar` script invocation and the
    `python -m nimbulo menubar` fallback. Run before re-loading the
    LaunchAgent / relaunching so we don't end up with stacked instances.
    Works on macOS and Linux (pkill -f); a missing pkill is tolerated.
    """
    import subprocess

    for pattern in ("nimbulo menubar", "nimbulo.*menubar"):
        try:
            subprocess.run(["pkill", "-f", pattern], capture_output=True)
        except OSError:
            pass


@app.command("autostart")
def autostart_cmd(
    action: str = typer.Argument("install", help="install | uninstall | status"),
) -> None:
    """Start the menu-bar app at login (macOS: launchd LaunchAgent; Linux: XDG autostart entry)."""
    if sys.platform == "darwin":
        _autostart_macos(action)
    elif sys.platform.startswith("linux"):
        _autostart_linux(action)
    else:
        console.print("[red]autostart supports macOS and Linux only[/]")
        raise typer.Exit(2)


def _autostart_macos(action: str) -> None:
    import subprocess

    plist_path = Path.home() / "Library/LaunchAgents/com.nimbulo.menubar.plist"
    label = "com.nimbulo.menubar"
    # Before the rename the agent was "com.cloud-login.menubar"; remove it on install/uninstall so two
    # menubar apps never run side by side.
    legacy_plist = Path.home() / "Library/LaunchAgents/com.cloud-login.menubar.plist"

    def _remove_legacy_agent() -> None:
        if legacy_plist.exists():
            subprocess.run(["launchctl", "unload", "-w", str(legacy_plist)], capture_output=True)
            legacy_plist.unlink()
            console.print(f"[green]removed old agent {legacy_plist}[/]")

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
        _remove_legacy_agent()
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

    _remove_legacy_agent()
    prog_args = _resolve_nimbulo_program()
    log_dir = Path.home() / "Library/Logs/nimbulo"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
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
    <string>{log_dir}/menubar.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/menubar.err</string>
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


_DESKTOP_EXEC_SPECIAL = set(" \t\n\"'\\><~|&;$*?#()`")


def _desktop_exec_quote(arg: str) -> str:
    """Quote one argv element for a .desktop Exec= line (Desktop Entry spec).

    Args containing reserved characters go in double quotes with `"`, `` ` ``, `$` and `\\` escaped by
    a backslash; because the value is itself a desktop string, a literal backslash is doubled again.
    A literal `%` must be written `%%` (field-code introducer).
    """
    arg = arg.replace("%", "%%")
    if not any(c in _DESKTOP_EXEC_SPECIAL for c in arg):
        return arg
    out = []
    for c in arg:
        if c == "\\":
            out.append("\\\\\\\\")
        elif c in '"`$':
            out.append("\\" + c)
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def _linux_autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "nimbulo-menubar.desktop"


def _menubar_running() -> bool:
    """True if a `nimbulo menubar` process exists (pgrep -f); False if none or pgrep is unavailable."""
    import subprocess

    try:
        rc = subprocess.run(["pgrep", "-f", "nimbulo.*menubar"], capture_output=True)
    except OSError:
        return False
    return rc.returncode == 0


def _autostart_linux(action: str) -> None:
    """XDG autostart entry (GNOME/KDE/XFCE/...). Output goes to the desktop session journal, not a file."""
    import subprocess

    path = _linux_autostart_path()

    if action == "status":
        exists = path.exists()
        console.print(f"entry: {'[green]installed[/]' if exists else '[yellow]not installed[/]'}  ({path})")
        if _menubar_running():
            console.print("[green]menubar: running[/]")
        else:
            console.print("[yellow]menubar: not running[/]")
        return

    if action == "uninstall":
        _kill_running_menubar()
        if path.exists():
            path.unlink()
            console.print(f"[green]removed {path}[/]")
        else:
            console.print("[yellow]already uninstalled[/]")
        return

    if action != "install":
        console.print(f"[red]unknown action: {action}[/]  (use install | uninstall | status)")
        raise typer.Exit(2)

    prog_args = _resolve_nimbulo_program()
    exec_line = " ".join(_desktop_exec_quote(a) for a in prog_args)
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=nimbulo menu bar\n"
        "Comment=Cloud login status in the system tray\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path.with_name(f".{path.name}.{os.getpid()}.tmp"))
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(entry)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    console.print(f"[green]wrote {path}[/]")
    console.print(f"[dim]program: {' '.join(prog_args)}[/]")
    console.print("[dim]output goes to the desktop session journal (journalctl --user), not a log file[/]")
    console.print("[dim]GNOME needs the AppIndicator/StatusNotifier extension for the tray icon to show[/]")

    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        _kill_running_menubar()  # reinstall must not stack instances
        try:
            subprocess.Popen(
                prog_args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            console.print("[green]menubar started; will also start at next login[/]")
        except OSError as e:
            console.print(f"[yellow]could not start menubar now ({e}); it will start at next login[/]")
    else:
        console.print("[dim]no graphical session detected; will start at next login[/]")


@app.command("status")
def status_cmd(
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="aws|gcp|azure|oci|vultr|digitalocean"),
    profile: Optional[str] = typer.Option(None, "--profile", help="profile/configuration to inspect"),
) -> None:
    """Show authentication status for all providers (or one)."""
    targets = [providers.get(provider)] if provider else providers.all_providers()
    results = [t.status(profile=profile) for t in targets]
    console.print(_render_status(results))


providers_app = typer.Typer(help="Show or toggle which providers are enabled.", invoke_without_command=True)
app.add_typer(providers_app, name="providers")


@providers_app.callback()
def _providers_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    table = Table(title="Providers")
    table.add_column("Key", style="bold")
    table.add_column("Provider")
    table.add_column("Enabled")
    for p in providers.all_providers(include_disabled=True):
        on = providers.is_enabled(p.key)
        table.add_row(p.key, p.label or p.key, "[green]yes[/]" if on else "[dim]no[/]")
    console.print(table)
    if providers.disables_everything():
        console.print("[yellow]settings.json disables every provider; showing all[/]")


def _toggle(key: str, enabled: bool) -> None:
    ok, msg = providers.set_enabled(key, enabled)
    if ok:
        console.print(escape(msg))
        return
    console.print(f"[red]{escape(msg)}[/]")
    unknown = key.lower() not in [p.key for p in providers.all_providers(include_disabled=True)]
    raise typer.Exit(2 if unknown else 1)


@providers_app.command("enable")
def providers_enable_cmd(key: str = typer.Argument(..., help=PROVIDER_HELP)) -> None:
    """Enable a provider."""
    _toggle(key, True)


@providers_app.command("disable")
def providers_disable_cmd(key: str = typer.Argument(..., help=PROVIDER_HELP)) -> None:
    """Disable a provider (hidden from status and the UIs)."""
    _toggle(key, False)


@app.command("profiles")
def profiles_cmd(provider: str = typer.Argument(..., help="aws|gcp|azure|oci|vultr|digitalocean")) -> None:
    """List configured profiles/configurations for a provider."""
    p = providers.get(provider)
    names = p.profiles()
    if not names:
        console.print(f"[yellow]no profiles configured for {p.label}[/]")
        return
    for n in names:
        console.print(n)


def _resolve_token_arg(token: Optional[str], token_stdin: bool) -> Optional[str]:
    """Resolve an API token without requiring it on the command line.

    Order: --token-stdin, NIMBULO_TOKEN env var, deprecated --token.
    A token passed as a command-line argument is visible to other local users
    via `ps`, so --token is kept only for backward compatibility and warns.
    """
    if token_stdin:
        value = sys.stdin.readline().strip()
        return value or None
    env = os.environ.get("NIMBULO_TOKEN", "").strip()
    if env:
        return env
    if token:
        console.print(
            "[yellow]warning:[/] --token exposes the secret in the process list. "
            "Use --token-stdin, the NIMBULO_TOKEN env var, or omit it to be prompted."
        )
        return token
    return None


@app.command("login")
def login_cmd(
    provider: str = typer.Argument(..., help="aws|gcp|azure|oci|vultr|digitalocean"),
    method: Optional[str] = typer.Option(None, "--method", "-m", help="sso|access-key|user|adc|user+adc|api-key|token|device-code|session|profile"),
    profile: Optional[str] = typer.Option(None, "--profile", help="profile (AWS/OCI) or configuration (GCP)"),
    project: Optional[str] = typer.Option(None, "--project", help="GCP project to activate after login"),
    subscription: Optional[str] = typer.Option(None, "--subscription", help="Azure subscription to activate after login"),
    tenant: Optional[str] = typer.Option(None, "--tenant", help="Azure tenant ID (for `az login --tenant`)"),
    region: Optional[str] = typer.Option(None, "--region", help="AWS region (with --method access-key) or OCI region"),
    token: Optional[str] = typer.Option(None, "--token", help="DEPRECATED (visible in `ps`): use --token-stdin or NIMBULO_TOKEN.", hidden=True),
    token_stdin: bool = typer.Option(False, "--token-stdin", help="read the API token (Vultr/DO) from stdin"),
    user: Optional[str] = typer.Option(None, "--user", help="OCI user OCID (oci -m api-key)"),
    tenancy: Optional[str] = typer.Option(None, "--tenancy", help="OCI tenancy OCID (oci -m api-key)"),
    key_file: Optional[str] = typer.Option(None, "--key-file", help="existing OCI API private key (oci -m api-key)"),
    generate_key: bool = typer.Option(False, "--generate-key", help="generate a new OCI API key pair (oci -m api-key)"),
    overwrite: bool = typer.Option(False, "--overwrite", help="replace an existing OCI profile (oci -m api-key), or convert an API-key profile (oci -m session)"),
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
    elif provider == "oci":
        kwargs = _oci_login_kwargs(p, method, profile, region, user, tenancy, key_file, generate_key, overwrite)
    elif provider in {"vultr", "digitalocean"}:
        # Only set kwargs["token"] if the user supplied one; otherwise let
        # provider.login() resolve from env → CLI config file → keyring.
        resolved = _resolve_token_arg(token, token_stdin)
        if resolved:
            kwargs["token"] = resolved
    safe = escape if p.key == "oci" else (lambda s: s)  # OCI messages carry user-provided text
    result = p.login(method=method, on_output=lambda line: console.print(f"[dim]{safe(line)}[/]"), **kwargs)
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{safe(result.message)}[/]")
    if p.key == "oci" and method == "api-key" and result.output:
        console.print(result.output, markup=False, highlight=False)  # public key + console upload steps
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


@app.command("compartments")
def compartments_cmd(
    profile: Optional[str] = typer.Option(None, "--profile", help="OCI profile (default: the active one)"),
) -> None:
    """List OCI compartments (id and path) for the active profile."""
    p = providers.get("oci")
    items = p.list_secondary(profile)  # type: ignore[call-arg]
    if not items:
        console.print("[yellow]no compartments visible (are you authenticated?)[/]")
        return
    table = Table(title=f"{p.label} compartments")
    table.add_column("Compartment OCID", style="bold", overflow="fold")
    table.add_column("Path")
    for cid, path in items:
        table.add_row(Text(cid), Text(path))
    console.print(table)


@app.command("set-compartment")
def set_compartment_cmd(
    compartment: str = typer.Argument(..., help="OCI compartment OCID to use for the active profile"),
    profile: Optional[str] = typer.Option(None, "--profile", help="OCI profile (default: the active one)"),
) -> None:
    """Set the active OCI compartment (stored in nimbulo settings, not ~/.oci/config)."""
    p = providers.get("oci")
    result = p.set_secondary(compartment, profile=profile)  # type: ignore[call-arg]
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{escape(result.message)}[/]")
    if not result.ok:
        raise typer.Exit(1)


@app.command("logout")
def logout_cmd(
    provider: str = typer.Argument(..., help="aws|gcp|azure|oci|vultr|digitalocean"),
    yes: bool = typer.Option(False, "--yes", "-y", help="OCI: skip the confirmation (logout removes the profile)"),
) -> None:
    """Sign out (revoke session) for a provider. For Vultr/DO this is a no-op; use `forget` to delete the stored key."""
    p = providers.get(provider)
    if p.key == "oci":
        _oci_logout(p, yes)
        return
    result = p.logout()
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.message}[/]")
    if not result.ok:
        raise typer.Exit(1)


def _oci_logout(p, yes: bool) -> None:
    from ..providers.oci import ConfirmationRequired

    result = p.logout()
    if isinstance(result, ConfirmationRequired):
        console.print(f"[yellow]{escape(result.message)}[/]")
        if not yes and not typer.confirm("Continue?", default=False):
            console.print("[red]logout cancelled[/]")
            raise typer.Exit(1)
        result = p.logout(confirm=True)
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{escape(result.message)}[/]")
    if not result.ok:
        raise typer.Exit(1)


@app.command("forget")
def forget_cmd(provider: str = typer.Argument(..., help="aws|gcp|azure|oci|vultr|digitalocean")) -> None:
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
    token: Optional[str] = typer.Option(None, "--token", help="DEPRECATED (visible in `ps`): use --token-stdin or NIMBULO_TOKEN.", hidden=True),
    token_stdin: bool = typer.Option(False, "--token-stdin", help="read the API token from stdin"),
) -> None:
    """Store/replace an API key for Vultr or Digital Ocean (alias for `login`)."""
    if provider not in {"vultr", "digitalocean"}:
        console.print("[red]set-key only applies to vultr and digitalocean[/]")
        raise typer.Exit(2)
    token = _resolve_token_arg(token, token_stdin)
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
