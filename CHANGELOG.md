# Changelog

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
