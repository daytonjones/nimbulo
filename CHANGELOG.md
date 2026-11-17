# Changelog

## Unreleased

### Added

- Linux support for `nimbulo autostart` (XDG autostart entry).

## [0.3.0] - 2026-11-16

### Added

- Enable or disable providers: `nimbulo providers`, `providers enable <key>`, `providers disable <key>`, stored as `providers.disabled` in `settings.json`. Disabled providers are hidden from the GUI, TUI, menu bar and status output; nothing is deleted and explicit commands (for example `nimbulo login oci`) still work.
- GUI **Providers…** dialog, TUI `p` modal, and a menu bar **Providers** submenu, all applying changes live. The menu bar re-reads the setting each time it opens, and its tray summary counts only enabled providers.

## [0.2.1] - 2026-11-15

### Fixed

- OCI: a profile whose private key is passphrase-protected but has no `pass_phrase` entry now shows a clear error instead of making the `oci` CLI prompt on the terminal (which could hang or garble the UI). All `oci` calls except the browser login now run detached from the terminal as a safety net.
- OCI: `OCI_CLI_CONFIG_FILE` is honored. Profiles are read from, and API-key profiles written to, that file (as the `oci` CLI does); previously nimbulo always used `~/.oci/config`, so it showed the wrong profiles when the variable was set.
- OCI: an inaccessible `~/.oci` directory now shows a clean error status (and a clean login failure) instead of raising `PermissionError`.

### Security

- `--token` exposed API keys in the process list. Prefer `--token-stdin` or the `NIMBULO_TOKEN` environment variable (or omit it to be prompted); `--token` still works but prints a warning.
- The launchd menubar agent logged to world-readable `/tmp`. Logs now go to `~/Library/Logs/nimbulo/` (mode 0700). Re-run `nimbulo autostart install` to update an existing install.
- AWS `access-key` login wrote `~/.aws/credentials` and `config` and only then restricted them to 0600. They are now created 0600 and replaced atomically.
- The GUI status panel now HTML-escapes provider-derived text for every provider (previously only OCI).

## [0.2.0] - 2026-11-14

### Added

- OCI provider: session, profile, and API-key login (with optional key generation), status, and compartment selection (`compartments`, `set-compartment`).
- OCI profile and compartment pickers in the TUI and GUI.

### Fixed

- GUI: OCI-derived text (OCIDs, CLI error output) is HTML-escaped before display in rich-text labels.
- OCI: profiles inherit `[DEFAULT]` values, and a malformed `~/.oci/config` shows an error status instead of breaking `status` for every provider; config writes keep symlinks and never duplicate a section.
- OCI: session login on an API-key profile and logout (which removes the profile) now ask for confirmation (`--overwrite` / `--yes` in the CLI).
- OCI: compartments load for session profiles; `compartments` and `set-compartment` accept `--profile`; `login -m profile` makes that profile active.
- OCI: service errors show their code and message; status shows the tenancy under the right label; CLI status escapes OCI values; regions are case-insensitive; the public key is shown for existing key files too.
- TUI: OCI session and profile logins keep the profile's own region.

### Packaging

- Recovered the package source and packaged it for PyPI (wheel and sdist, bundled provider SVG icons).

## [0.1.0]

- Recovered baseline: CLI, TUI, GUI, and menu bar for AWS, GCP, Azure, Vultr, and DigitalOcean.
- Packaged for PyPI.
