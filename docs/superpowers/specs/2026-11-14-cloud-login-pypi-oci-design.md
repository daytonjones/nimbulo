> **Historical document.** Written when this project was named `cloud-login`; it was later renamed **nimbulo** (module `nimbulo`, command `nimbulo`). Names below are as they were at the time.

# cloud-login: recover, package for PyPI, add OCI

Date: 2026-11-14 · Status: awaiting approval

## Intent and why

`cloud-login` is the author's personal tool: one place to see "am I logged in?"
across cloud providers and to re-auth without remembering each CLI's commands.
The original repo (`~/REPOS/cloud-login`) was lost. The only surviving copy is
the installed package in `~/.local/share/uv/tools/cloud-login/lib/python3.*/site-packages/`
(plain `.py` source, ~3,300 lines, plus README embedded in `METADATA`).

Goal: recover that source into a proper repo, make it publishable on PyPI
(name `cloud-login` is free as of 2026-11-14), and add Oracle Cloud (OCI) as a
sixth provider.

## Goals

1. Recover the installed source verbatim into a new repo: `cloud_login/` incl.
   `providers/`, `ui/` (cli, tui, gui, menubar), `models.py`, `settings.py`,
   `secrets.py`, `assets/*.svg`.
2. Rebuild packaging: `pyproject.toml` (from installed `METADATA`: name, version
   0.1.0, author Dayton Jones, deps, Python >=3.12, `cloud-login = cloud_login:main`
   script), `src/` layout, `uv_build` backend, README (from METADATA), MIT LICENSE,
   CHANGELOG, `.gitignore`.
3. Add an `OCIProvider` wrapping the `oci` CLI, following the existing provider pattern:
   - **Status**: authenticated / expired / unauthenticated / not-installed, with
     identity (user, tenancy, region) and session-token expiry where applicable.
   - **Login methods (all three)**: (a) `oci session authenticate` browser flow,
     (b) API-key setup writing a profile to `~/.oci/config`, (c) use an existing
     config profile as-is.
   - **Profile switching**: profile picker = sections of `~/.oci/config`.
   - **Compartment selection**: secondary picker (`secondary_label = "Compartment"`),
     like GCP projects / Azure subscriptions. Persisted the way existing providers
     persist their secondary selection.
4. OCI appears in all four surfaces (GUI, TUI, menubar, CLI) via the existing
   registry, with an `oci.svg` icon, and a README row.
5. Tests (pytest) with mocked subprocess output; ruff clean.
6. Build sdist+wheel, pass `twine check`, and verify a clean-venv install runs
   `cloud-login --help` and `cloud-login status`.

## Non-goals

- No behaviour change to AWS, GCP, Azure, Vultr, DigitalOcean, or existing UI
  behaviour. The only UI additions are OCI's own entries and its API-key form.
- No move of PySide6 or other dependencies into extras. Default install stays as today.
- No use of the OCI Python SDK.
- No upload to PyPI (publishing is a separate, explicit step by the author).

## Anti-goals (failure even if it "works")

- Any diff against recovered files other than additive OCI wiring
  (registry entry, README row, icon, tests) and packaging files.
- A package that installs but cannot find its SVG assets.
- Tests that need real cloud accounts or network.

## Constraints

- Python >=3.12; `uv` for env/packaging, `pytest`, `ruff`; Docker not needed.
- `oci` CLI present locally at `/opt/homebrew/bin/oci` (useful for checking real
  output shapes, e.g. `oci iam compartment list --output json`), but
  tests must not depend on it.
- Large work should happen in a git worktree / isolated repo and be delegated to
  sub-agents where tasks are independent (author's standing preference).

## Approaches considered

| Approach | Trade-off | Outcome |
|---|---|---|
| **A. Wrap `oci` CLI** | Consistent with other providers; no new deps; needs CLI installed; compartment listing spawns a process | **Chosen by author** |
| B. `oci` Python SDK | Faster/cleaner queries; heavy dep; login still needs CLI; breaks "CLI is source of truth" | Rejected |
| C. Hybrid CLI + SDK | Most capable; doubles surface to test | Rejected |
| GUI deps into `[gui]` extra | Small base install; changes default install | Rejected by author: "current functionality should not change" |

## Decisions made by the author

- CLI-wrapping approach for OCI.
- All three login methods, profile switching, compartment selection.
- GUI stays in the default install; nothing existing changes.

## Added after review (author decisions and source findings)

- **Public metadata:** author `Dayton Jones <jones.dayton@gmail.com>` (author wrote
  "gmail.comm"; treated as a typo for gmail.com, confirm at approval). No project URLs
  until a remote exists. MIT, copyright Dayton Jones.
- **Scrub all "<former-employer-name>"**: found in `METADATA` (author email) and README line
  `cloud-login login aws -m sso --profile <former-employer-name>` (replace with a neutral profile
  name such as `my-profile`). Final check: `grep -ri <former-employer-name>` over repo, sdist and
  wheel must return nothing.
- **OCI API-key login gets a small purpose-built form in the GUI and TUI**
  (user OCID, tenancy OCID, region, key path or "generate a key pair", fingerprint
  shown, public key displayed/copyable with a prompt to upload it in the console,
  then a verification call). This is an explicit, OCI-only exception to
  "no UI change": it must not alter behaviour for other providers. The CLI takes the
  same fields as flags. Good UX is a goal: field validation (OCID format, region
  list), clear next-step messages, no secrets in logs.
- **Source facts:** all 32 files in the install record are present; tests never
  shipped; base `Provider` already supports multiple login methods, profiles and a
  secondary picker, so no base-class change; assets are located via `__file__` (works
  from a wheel); secondary selections persist in `~/.config/cloud-login/settings.json`.
- **Compartment persistence:** `settings.json`, keyed per OCI profile
  (e.g. `oci.compartment.<profile>`); never write it into `~/.oci/config`.
- **OCI status:** session profiles use the token's own expiry; API-key profiles use a
  config check plus a cheap `oci iam region list` call with a short timeout;
  offline shows as an error, not "authenticated".
- **Version:** 0.2.0 in the local build; no upload.

## Calls made by Claude (correct if wrong)

- Repo at `~/Code/cloud-login-pkg`, `src/` layout, `uv_build`, MIT license.
- Version stays 0.1.0 for the recovered baseline; OCI release bumps to 0.2.0.
- Compartment list is fetched at the tenancy level (`--compartment-id-in-subtree true`,
  `--all`) and cached briefly to avoid repeated subprocess calls.
- The recovered baseline is committed first as its own commit, so OCI work is a
  reviewable diff on top of it.

## Left to the builder

OCI CLI parsing details, exact `~/.oci/config` read/write handling (use
`configparser`, preserve existing profiles), session-expiry detection, test
fixtures, README wording, and the OCI icon.
