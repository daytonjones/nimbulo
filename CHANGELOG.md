# Changelog

## [0.2.0] - 2026-11-14

### Added

- OCI provider: session, profile, and API-key login (with optional key generation), status, and compartment selection (`compartments`, `set-compartment`).
- OCI profile and compartment pickers in the TUI and GUI.

### Fixed

- GUI: OCI-derived text (OCIDs, CLI error output) is HTML-escaped before display in rich-text labels.

### Packaging

- Recovered the package source and packaged it for PyPI (wheel and sdist, bundled provider SVG icons).

## [0.1.0]

- Recovered baseline: CLI, TUI, GUI, and menu bar for AWS, GCP, Azure, Vultr, and DigitalOcean.
- Packaged for PyPI.
