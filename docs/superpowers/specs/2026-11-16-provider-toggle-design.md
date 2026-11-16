# Enable/disable cloud providers

Date: 2026-11-16 · Status: approved by author ("yes", proposal in chat)

## Intent
Let the author hide providers they do not use, via the config file or inside the app (saved).

## Design
- **Storage:** `providers.disabled` (list of provider keys) in `~/.config/cloud-login/settings.json`. Missing or empty = all enabled, so existing installs are unchanged. Unknown keys are ignored. Hand-editable.
- **API:** `providers.all_providers()` returns enabled providers only; `all_providers(include_disabled=True)` returns all; `providers.is_enabled(key)`, `providers.set_enabled(key, bool)` (refuses to disable the last enabled provider). `providers.get(key)` is unchanged: an explicitly named provider always works (author decision).
- **CLI:** `cloud-login providers` (table: key, label, enabled), `providers enable <key>`, `providers disable <key>`. `status` with no provider shows enabled ones only.
- **TUI / GUI / menubar:** a Providers control (TUI modal or key binding; GUI dialog with checkboxes; menubar checkable submenu) that saves immediately and applies live, no restart.
- **Guard:** at least one provider always enabled.
- Disabled = hidden only. Credentials, profiles and other settings are untouched.

## Non-goals
No change to any provider's behaviour; no per-UI separate settings; no env-var override.

## Anti-goals
A change in default behaviour for users who never touch the setting. Disabling deleting any stored data.

## Left to the builder
Dialog layout, live add/remove of tabs in each UI, tests (isolated HOME is already enforced globally).
