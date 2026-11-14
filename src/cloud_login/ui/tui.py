from __future__ import annotations

import difflib
import re
from typing import Optional

from rich.markup import escape
from rich.text import Text

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    Log,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from .. import providers
from ..models import AuthState, ProviderStatus, Urgency, format_expires
from ..providers import oci_config
from ..providers.base import Provider


def _badge(state: AuthState) -> str:
    color = {
        AuthState.AUTHENTICATED: "green",
        AuthState.EXPIRED: "yellow",
        AuthState.UNAUTHENTICATED: "red",
        AuthState.NOT_INSTALLED: "grey50",
        AuthState.ERROR: "red",
        AuthState.UNKNOWN: "white",
    }[state]
    return f"[{color}]●[/] {state.value}"


_OCI_PROFILE_RE = re.compile(r"[A-Za-z0-9_.@-]+")


def validate_oci_form(v: dict) -> Optional[str]:
    """Return the first problem with the OCI api-key form values, or None. Plain text, not markup."""
    name = v["profile"]
    if not _OCI_PROFILE_RE.fullmatch(name) or name in (".", ".."):
        return "profile name may only contain letters, digits and . _ @ - (and must not be '.' or '..')"
    if not oci_config.validate_ocid(v["user"], "user"):
        return "user OCID is not valid (expected ocid1.user.<realm>..<id>)"
    if not oci_config.validate_ocid(v["tenancy"], "tenancy"):
        return "tenancy OCID is not valid (expected ocid1.tenancy.<realm>..<id>)"
    region = v["region"]
    if region not in oci_config.KNOWN_REGIONS:
        near = difflib.get_close_matches(region, oci_config.KNOWN_REGIONS, n=3, cutoff=0.6)
        hint = f"; did you mean {', '.join(near)}?" if near else ""
        return f"unknown region {region!r}{hint}"
    if bool(v["key_file"]) == bool(v["generate_key"]):
        return "choose exactly one: supply an existing key file or generate a new key pair"
    return None


class ProviderPane(Vertical):
    """One tab's worth of UI for a single provider."""

    def __init__(self, provider: Provider) -> None:
        super().__init__()
        self.provider = provider
        if provider.key == "oci":
            self.add_class("-oci")
        self._status_widget: Static | None = None
        self._profile_select: Select | None = None
        self._method_select: Select | None = None
        self._secondary_select: Select | None = None
        self._token_input: Input | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id=f"status-{self.provider.key}", classes="status")

        methods = self.provider.login_methods()
        has_profile = self.provider.profile_label is not None
        has_method_choice = len(methods) > 1
        if has_profile or has_method_choice:
            with Horizontal(classes="row"):
                if has_profile:
                    yield Label(f"{self.provider.profile_label}:")
                    yield Select(
                        options=[("(detecting…)", "")],
                        id=f"profile-{self.provider.key}",
                        allow_blank=True,
                    )
                if has_method_choice:
                    yield Label("Method:")
                    yield Select(
                        options=[(m, m) for m in methods],
                        value=methods[0],
                        id=f"method-{self.provider.key}",
                        allow_blank=False,
                    )

        if self.provider.secondary_label is not None:
            with Horizontal(classes="row"):
                yield Label(f"{self.provider.secondary_label}:")
                yield Select(
                    options=[("(none)", "")],
                    id=f"secondary-{self.provider.key}",
                    allow_blank=True,
                )
                yield Button(
                    f"Set {self.provider.secondary_label}",
                    id=f"setsec-{self.provider.key}",
                    variant="default",
                )
                yield Button(
                    "Refresh",
                    id=f"refreshsec-{self.provider.key}",
                    variant="default",
                )

        if self.provider.key in {"aws", "vultr", "digitalocean"}:
            yield Input(
                placeholder={
                    "aws": "for `access-key`: AKID:SECRET   (otherwise leave blank)",
                    "vultr": "Vultr API key (stored in macOS Keychain)",
                    "digitalocean": "DigitalOcean token (stored in macOS Keychain)",
                }[self.provider.key],
                id=f"token-{self.provider.key}",
                password=True,
            )

        if self.provider.key == "oci":
            yield from self._compose_oci_form()

        with Horizontal(classes="row"):
            yield Button("Refresh", id=f"refresh-{self.provider.key}", variant="default")
            yield Button("Login / Re-auth", id=f"login-{self.provider.key}", variant="primary")
            if self.provider.key in {"vultr", "digitalocean"}:
                yield Button("Forget Key", id=f"forget-{self.provider.key}", variant="warning")
            else:
                yield Button("Logout", id=f"logout-{self.provider.key}", variant="warning")

    def _compose_oci_form(self) -> ComposeResult:
        try:
            defaults = self.provider.api_key_defaults()
        except Exception:
            defaults = {}
        with Vertical(id="oci-form"):
            with Horizontal(classes="oci-row"):
                yield Input(value=defaults.get("profile", ""), placeholder="profile name", id="oci-profile")
                yield Input(value=defaults.get("region", ""), placeholder="region (us-ashburn-1)", id="oci-region")
            with Horizontal(classes="oci-row"):
                yield Input(placeholder="user OCID (ocid1.user.oc1..…)", id="oci-user")
            with Horizontal(classes="oci-row"):
                yield Input(placeholder="tenancy OCID (ocid1.tenancy.oc1..…)", id="oci-tenancy")
            with Horizontal(classes="oci-row"):
                yield Input(placeholder="existing private key file (.pem)", id="oci-keyfile")
                yield Checkbox("Generate a new key pair", id="oci-generate")
            with Horizontal(classes="oci-row"):
                yield Checkbox("Overwrite existing profile", id="oci-overwrite")
                yield Static("", id="oci-msg")

    def on_mount(self) -> None:
        if self.provider.key == "oci":
            self._sync_oci_form()
        self._status_widget = self.query_one(f"#status-{self.provider.key}", Static)
        for attr, css_id in [
            ("_profile_select", f"#profile-{self.provider.key}"),
            ("_method_select", f"#method-{self.provider.key}"),
            ("_secondary_select", f"#secondary-{self.provider.key}"),
            ("_token_input", f"#token-{self.provider.key}"),
        ]:
            try:
                widget = self.query_one(css_id)
            except Exception:
                widget = None
            setattr(self, attr, widget)
        self.refresh_status()

    # --- rendering --------------------------------------------------------

    def render_status(self, s: ProviderStatus) -> None:
        ident = s.identity
        esc = escape if self.provider.key == "oci" else (lambda x: x)
        lines = [f"[b]{self.provider.label}[/]   {_badge(s.state)}"]
        if ident:
            if ident.user:
                lines.append(f"user: {esc(ident.user)}")
            if ident.account:
                label = (self.provider.secondary_label or "account").lower()
                lines.append(f"{label}: {esc(str(ident.account))}")
            if ident.expires_at:
                exp_label, urg = format_expires(ident.expires_at)
                color = {Urgency.OK: "green", Urgency.SOON: "yellow", Urgency.EXPIRED: "red"}[urg]
                lines.append(f"expires: [{color}]{exp_label}[/]")
        if s.active_profile:
            lines.append(f"active: {esc(s.active_profile)}")
        if s.message:
            lines.append(f"[dim]{esc(s.message)}[/]")
        if self._status_widget is not None:
            self._status_widget.update("\n".join(lines))
        if self._profile_select is not None:
            opts = [(p, p) for p in s.profiles] or [("(none)", "")]
            self._profile_select.set_options(opts)
            if s.active_profile and s.active_profile in s.profiles:
                self._profile_select.value = s.active_profile

    # --- OCI api-key form (oci only) ---------------------------------------

    def _sync_oci_form(self) -> None:
        self.query_one("#oci-form").display = self.selected_method() == "api-key"
        if hasattr(self.app, "_update_compact"):
            self.app._update_compact()

    def _set_oci_message(self, msg: str) -> None:
        # Text() is never parsed as markup, so brackets in user data are safe.
        self.query_one("#oci-msg", Static).update(Text(msg, style="red") if msg else "")

    def oci_form_values(self) -> dict:
        def val(wid: str) -> str:
            return self.query_one(f"#{wid}", Input).value.strip()

        generate = self.query_one("#oci-generate", Checkbox).value
        return {
            "profile": val("oci-profile"),
            "user": val("oci-user"),
            "tenancy": val("oci-tenancy"),
            "region": val("oci-region"),
            "key_file": None if generate else (val("oci-keyfile") or None),
            "generate_key": generate,
            "overwrite": self.query_one("#oci-overwrite", Checkbox).value,
        }

    @on(Select.Changed)
    def _oci_method_changed(self, event: Select.Changed) -> None:
        if self.provider.key == "oci" and event.select.id == "method-oci":
            self._sync_oci_form()

    @on(Checkbox.Changed, "#oci-generate")
    def _oci_generate_changed(self, event: Checkbox.Changed) -> None:
        self.query_one("#oci-keyfile", Input).disabled = event.value

    @on(Input.Submitted)
    def _oci_input_submitted(self, event: Input.Submitted) -> None:
        wid = event.input.id or ""
        if self.provider.key != "oci" or not wid.startswith("oci-"):
            return
        event.stop()
        order = ["oci-profile", "oci-user", "oci-tenancy", "oci-region", "oci-keyfile"]
        gen = self.query_one("#oci-generate", Checkbox).value
        last = "oci-region" if gen else "oci-keyfile"
        if wid == last:
            self.app._login_pressed(self)
        elif wid in order:
            nxt = order[order.index(wid) + 1]
            if gen and nxt == "oci-keyfile":
                nxt = "oci-region"
            self.query_one(f"#{nxt}").focus()

    def render_secondary(self, items: list[tuple[str, str]]) -> None:
        if self._secondary_select is None:
            return
        opts = [(f"{sid}  ({name})" if name and name != sid else sid, sid) for sid, name in items]
        self._secondary_select.set_options(opts or [("(none)", "")])

    # --- workers ----------------------------------------------------------

    @work(thread=True, exclusive=True)
    def refresh_status(self) -> None:
        s = self.provider.status()
        self.app.call_from_thread(self.render_status, s)
        if hasattr(self.app, "_update_tab_label"):
            self.app.call_from_thread(self.app._update_tab_label, self.provider.key, s.state)
        if self.provider.secondary_label is not None and s.ok:
            self.refresh_secondary()

    @work(thread=True, exclusive=False)
    def refresh_secondary(self) -> None:
        if self.provider.secondary_label is None:
            return
        items = self.provider.list_secondary()
        self.app.call_from_thread(self.render_secondary, items)
        self.app.call_from_thread(
            self._log_tui,
            f"[{self.provider.key}] {len(items)} {self.provider.secondary_label.lower()}(s) available",
        )

    def _log_tui(self, msg: str) -> None:
        app = self.app
        if hasattr(app, "_log"):
            app._log(msg)

    # --- helpers ----------------------------------------------------------

    def selected_profile(self) -> Optional[str]:
        if self._profile_select is None:
            return None
        v = self._profile_select.value
        return None if v in (Select.BLANK, "") else str(v)

    def selected_secondary(self) -> Optional[str]:
        if self._secondary_select is None:
            return None
        v = self._secondary_select.value
        return None if v in (Select.BLANK, "") else str(v)

    def selected_method(self) -> str:
        v = self._method_select.value if self._method_select else None
        if v in (None, Select.BLANK):
            return self.provider.login_methods()[0]
        return str(v)

    def token_value(self) -> Optional[str]:
        return self._token_input.value if self._token_input else None


class CloudLoginTUI(App):
    CSS = """
    Screen { background: $surface; }
    .status { padding: 1 1; height: auto; }
    .row { height: auto; padding: 0 1; }
    Select { width: 38; margin-right: 2; }
    Input { width: 60; }
    Button { margin-right: 1; }
    ProviderPane.-oci { overflow-y: auto; }
    #oci-form { height: auto; padding: 0 1; }
    .oci-row { height: 1; padding: 0; }
    #oci-form Input { height: 1; border: none; padding: 0 1; width: 1fr; margin-right: 1; }
    #oci-form Checkbox { height: 1; border: none; padding: 0; width: auto; }
    #oci-msg { height: auto; width: 1fr; padding: 0 1; }
    CloudLoginTUI.-oci-compact #log { height: 6; }
    #log { height: 16; border: solid $primary; }
    """
    BINDINGS = [
        ("r", "refresh_all", "Refresh all"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._panes: dict[str, ProviderPane] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        initial_key = providers.all_providers()[0].key
        with TabbedContent(initial=initial_key):
            for p in providers.all_providers():
                pane = ProviderPane(p)
                self._panes[p.key] = pane
                with TabPane(p.label, id=p.key):
                    yield pane
        yield Log(id="log", highlight=True)
        yield Footer()

    def _update_compact(self, active: str | None = None) -> None:
        """OCI tab with the api-key form open: shrink the log so the whole form fits at 80x24."""
        pane = self._panes.get("oci")
        on = False
        if pane is not None:
            try:
                on = (
                    (active or self.query_one(TabbedContent).active) == "oci"
                    and pane.query_one("#oci-form").display
                )
            except Exception:
                on = False
        self.set_class(on, "-oci-compact")

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._update_compact(event.pane.id)

    def _log(self, msg: str) -> None:
        self.query_one("#log", Log).write_line(msg)

    def on_mount(self) -> None:
        # Start everything dim; status callbacks light them up green when authenticated.
        for key in self._panes:
            self._update_tab_label(key, AuthState.UNKNOWN)

    def _update_tab_label(self, provider_key: str, state: AuthState) -> None:
        pane = self._panes.get(provider_key)
        if pane is None:
            return
        name = pane.provider.label
        markup = f"[green]{name}[/]" if state == AuthState.AUTHENTICATED else f"[dim]{name}[/]"
        try:
            tab = self.query_one(TabbedContent).get_tab(provider_key)
            tab.label = markup
        except Exception:
            pass

    def action_refresh_all(self) -> None:
        for pane in self._panes.values():
            pane.refresh_status()
        self._log("refreshing all providers…")

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if "-" not in bid:
            return
        action, key = bid.split("-", 1)
        pane = self._panes.get(key)
        if not pane:
            return
        if action == "refresh":
            pane.refresh_status()
            self._log(f"[{key}] refreshing…")
        elif action == "login":
            self._login_pressed(pane)
        elif action == "logout":
            self._do_logout(pane)
        elif action == "forget":
            self._do_forget(pane)
        elif action == "setsec":
            self._do_set_secondary(pane)
        elif action == "refreshsec":
            pane.refresh_secondary()

    def _login_pressed(self, pane: ProviderPane) -> None:
        if pane.provider.key == "oci" and pane.selected_method() == "api-key":
            problem = validate_oci_form(pane.oci_form_values())
            pane._set_oci_message(problem or "")
            if problem:
                return
        self._do_login(pane)

    def _on_output(self, key: str, line: str) -> None:
        if line.strip():
            self.call_from_thread(self._log, f"[{key}] {line}")

    @work(thread=True, exclusive=False)
    def _do_login(self, pane: ProviderPane) -> None:
        provider = pane.provider
        method = pane.selected_method()
        kwargs: dict = {}
        if provider.key == "aws":
            if method == "sso":
                profile = pane.selected_profile()
                if not profile:
                    self.call_from_thread(self._log, "[aws] pick a profile first")
                    return
                kwargs["profile"] = profile
            elif method == "access-key":
                token = pane.token_value() or ""
                if ":" not in token:
                    self.call_from_thread(
                        self._log,
                        "[aws] for access-key method, put `AKID:SECRET` in the token field",
                    )
                    return
                akid, secret = token.split(":", 1)
                kwargs.update(
                    profile=pane.selected_profile() or "default",
                    access_key_id=akid.strip(),
                    secret_access_key=secret.strip(),
                )
        elif provider.key == "gcp":
            if pane.selected_profile():
                kwargs["configuration"] = pane.selected_profile()
        elif provider.key == "azure":
            pass
        elif provider.key == "oci":
            if method == "api-key":
                kwargs.update(self.call_from_thread(pane.oci_form_values))
            else:
                if pane.selected_profile():
                    kwargs["profile"] = pane.selected_profile()
                region = self.call_from_thread(lambda: pane.query_one("#oci-region", Input).value.strip())
                if region:
                    kwargs["region"] = region
        elif provider.key in {"vultr", "digitalocean"}:
            token = (pane.token_value() or "").strip()
            if token:
                kwargs["token"] = token
            # else: provider.login() will resolve from env → CLI config file → keyring

        self.call_from_thread(self._log, f"[{provider.key}] login method={method} starting…")
        result = provider.login(
            method=method,
            on_output=lambda line, k=provider.key: self._on_output(k, line),
            **kwargs,
        )
        self.call_from_thread(
            self._log, f"[{provider.key}] {'OK' if result.ok else 'FAIL'}: {result.message}"
        )
        if provider.key == "oci" and result.output and method == "api-key":
            # public key + console upload steps; Log is plain text so brackets are literal
            for line in result.output.splitlines():
                self.call_from_thread(self._log, f"[{provider.key}] {line}")
        pane.refresh_status()

    @work(thread=True, exclusive=False)
    def _do_logout(self, pane: ProviderPane) -> None:
        result = pane.provider.logout()
        self.call_from_thread(
            self._log, f"[{pane.provider.key}] {'OK' if result.ok else 'FAIL'}: {result.message}"
        )
        pane.refresh_status()

    @work(thread=True, exclusive=False)
    def _do_forget(self, pane: ProviderPane) -> None:
        result = pane.provider.forget()
        self.call_from_thread(
            self._log, f"[{pane.provider.key}] {'OK' if result.ok else 'FAIL'}: {result.message}"
        )
        pane.refresh_status()

    @work(thread=True, exclusive=False)
    def _do_set_secondary(self, pane: ProviderPane) -> None:
        if pane.provider.secondary_label is None:
            return
        sid = pane.selected_secondary()
        if not sid:
            self.call_from_thread(
                self._log,
                f"[{pane.provider.key}] no {pane.provider.secondary_label.lower()} selected",
            )
            return
        result = pane.provider.set_secondary(sid)
        self.call_from_thread(
            self._log, f"[{pane.provider.key}] {'OK' if result.ok else 'FAIL'}: {result.message}"
        )
        pane.refresh_status()


def run() -> int:
    CloudLoginTUI().run()
    return 0
