# Changelog

## [0.2.1] - 2026-11-15

### Fixed

- OCI: `OCI_CLI_CONFIG_FILE` is honored. Profiles are read from, and API-key profiles written to, that file (as the `oci` CLI does); previously cloud-login always used `~/.oci/config`, so it showed the wrong profiles when the variable was set.
- OCI: an inaccessible `~/.oci` directory now shows a clean error status (and a clean login failure) instead of raising `PermissionError`.

### Security

- `--token` exposed API keys in the process list. Prefer `--token-stdin` or the `CLOUD_LOGIN_TOKEN` environment variable (or omit it to be prompted); `--token` still works but prints a warning.
- The launchd menubar agent logged to world-readable `/tmp`. Logs now go to `~/Library/Logs/cloud-login/` (mode 0700). Re-run `cloud-login autostart install` to update an existing install.
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
