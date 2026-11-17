> **Historical document.** Written when this project was named `cloud-login`; it was later renamed **nimbulo** (module `nimbulo`, command `nimbulo`). Names below are as they were at the time.

# cloud-login PyPI Packaging + OCI Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the lost `cloud-login` source into a publishable PyPI package and add Oracle Cloud (OCI) as a sixth provider.

**Architecture:** Copy the installed package source verbatim into a `src/` layout repo, add packaging files, then add `OCIProvider` (wrapping the `oci` CLI) through the existing `Provider` base class and registry. OCI profile = profile picker, compartment = secondary picker; only the OCI API-key login needs new GUI/TUI form widgets.

**Tech Stack:** Python >=3.12, uv (`uv_build`), typer, rich, textual, PySide6, keyring, requests, pyyaml, qt-material; pytest, ruff, twine.

**Spec:** `docs/superpowers/specs/2026-11-14-cloud-login-pypi-oci-design.md`

## Global Constraints

- Python `>=3.12`; `uv` for env and build; `pytest` for tests; `ruff` for lint and format.
- Package/project name `cloud-login`; import package `cloud_login`; console script `cloud-login = cloud_login:main`.
- Recovered source: `/Users/djones/.local/share/uv/tools/cloud-login/lib/python3.12/site-packages/cloud_login/` (32-file install; copy verbatim, no edits except Task 6/7/8 OCI wiring).
- Dependencies stay exactly: `typer>=0.12, rich>=13.7, textual>=0.74, pyside6>=6.7, keyring>=25.0, requests>=2.32, pyyaml>=6.0, qt-material>=2.14`. No `oci` SDK; no other new runtime dependency.
- Author `Dayton Jones <jones.dayton@gmail.com>`; MIT license, copyright Dayton Jones; no project URLs.
- The string `<former-employer-name>` (any case) must appear nowhere in the shipped code, README, metadata, sdist or wheel (the `docs/superpowers/` design and plan files name it only to describe the scrub and are excluded from the sdist). README example profile becomes `my-profile`.
- Versions: baseline `0.1.0`; final local build `0.2.0`. Never upload to PyPI.
- No behaviour change to AWS/GCP/Azure/Vultr/DigitalOcean or existing UI behaviour.
- Tests mock subprocess output and use temp dirs for `~/.oci`, `~/.config/cloud-login`. No real cloud accounts or network.
- OCI settings persist in `~/.config/cloud-login/settings.json` (`oci.compartment.<profile>`), never in `~/.oci/config`.

## Review Focus

- `~/.oci/config` missing, empty, or `oci` not installed: status is `NOT_INSTALLED`/`UNAUTHENTICATED`, never a crash (Task 3).
- Writing an API-key profile must preserve all other profiles and comments' sections, and refuse to overwrite an existing profile name without explicit confirmation (Task 2).
- Expired session token shows `EXPIRED` with `expires_at`, not "authenticated" (Task 3).
- Offline / timed-out `oci iam region list` for API-key profile shows `ERROR`, not "authenticated" (Task 3).
- Compartment list is large or nested: names show the path (e.g. `root/dev/team-a`), and a stale saved compartment that no longer exists is dropped without error (Task 4).
- Private key / passphrase / session token never appears in `on_output` logs or `LoginResult.output` (Tasks 2, 5).
- Wheel installed in a clean venv finds SVG assets including `oci.svg` (Task 9).

---

### Task 1: Recover baseline and packaging

**Files:**
- Create: `src/cloud_login/**` (verbatim copy), `pyproject.toml`, `README.md`, `LICENSE`, `CHANGELOG.md`, `.gitignore`, `tests/test_smoke.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces: importable `cloud_login` package; `uv run pytest`, `uv run ruff check` working; `ASSETS_DIR` resolving to `src/cloud_login/assets`.

- [ ] **Step 1:** `git init` in `~/Code/cloud-login-pkg` (branch `main`); copy the recovered `cloud_login/` to `src/cloud_login/` excluding `__pycache__`; commit as `chore: recover baseline source verbatim` (source only).
- [ ] **Step 2:** Write `pyproject.toml` per Global Constraints (`uv_build` backend, `requires-python = ">=3.12"`, `[project.scripts]`, ruff config with line-length 120, pytest `testpaths = ["tests"]`, dev group with pytest, ruff, twine). README comes from the installed `METADATA` body (lines after the header block) with the `<former-employer-name>` example replaced by `my-profile` and the install section rewritten to `uv tool install cloud-login` / `pipx install cloud-login`. Add MIT `LICENSE`, `CHANGELOG.md` (0.1.0: recovered baseline), `.gitignore`.
- [ ] **Step 3:** Write `tests/test_smoke.py` with `test_imports` (imports `cloud_login`, `cloud_login.providers.all_providers`), `test_registry_has_five_providers_baseline` (keys == `["aws","gcp","azure","vultr","digitalocean"]`; Task 6 updates it), `test_assets_exist` (each provider key has `assets/<key>.svg`), `test_no_<former-employer-name>` (no file under repo except `docs/` contains the string, case-insensitive).
- [ ] **Step 4:** Run `uv sync && uv run pytest -v && uv run ruff check src tests`. Expected: pass. If ruff flags recovered code, add per-file ignores in `pyproject.toml` rather than editing recovered files.
- [ ] **Step 5:** Commit `build: add pyproject, README, license, changelog, smoke tests`. Create branch `oci-provider` for all following tasks.

### Task 2: OCI config helper

**Files:**
- Create: `src/cloud_login/providers/oci_config.py`
- Test: `tests/test_oci_config.py`

**Interfaces:**
- Produces:
  - `OCI_CONFIG_PATH: Path` (`~/.oci/config`, module-level so tests monkeypatch it)
  - `read_profiles() -> dict[str, dict[str, str]]` (empty dict if file missing; uses `configparser` with `DEFAULT` handled; profile name `DEFAULT` included)
  - `write_api_key_profile(name: str, *, user: str, tenancy: str, region: str, key_file: str, fingerprint: str, overwrite: bool = False) -> None` (raises `FileExistsError` if profile exists and not `overwrite`; preserves other profiles; sets file mode 0600)
  - `validate_ocid(value: str, kind: str) -> bool` (`kind` in `"user"`, `"tenancy"`; matches `ocid1.<kind>.<realm>..<unique>` shape)
  - `KNOWN_REGIONS: tuple[str, ...]` (seeded from `oci iam region list` output captured once into the module; include common commercial regions)
  - `generate_key_pair(directory: Path, name: str) -> tuple[Path, Path, str]` returns (private_key, public_key, fingerprint); private key mode 0600; no new runtime dependency (use `openssl` via subprocess, falling back to a clear error if absent).

- [ ] **Step 1:** Write failing tests: `test_read_profiles_missing_file_returns_empty`, `test_write_preserves_other_profiles` (pre-seed `[DEFAULT]` and `[PROD]`, add `[DEV]`, assert all three present and values unchanged), `test_write_refuses_overwrite_without_flag`, `test_validate_ocid_accepts_and_rejects` (valid `ocid1.user.oc1..aaaa`, invalid `ocid1.tenancy.oc1..x` for kind `user`, empty string), `test_config_file_mode_0600`, `test_generate_key_pair_creates_0600_private_key` (skip if `openssl` missing).
- [ ] **Step 2:** Run `uv run pytest tests/test_oci_config.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement the functions above in `oci_config.py`.
- [ ] **Step 4:** Run tests. Expected: PASS. Commit `feat(oci): config file helper`.

### Task 3: OCIProvider status, profiles, login (session and existing profile), logout

**Files:**
- Create: `src/cloud_login/providers/oci.py`
- Test: `tests/test_oci_provider.py`, `tests/conftest.py` (fixture `fake_run` that patches `Provider._run` with a command→(rc,out,err) table)

**Interfaces:**
- Consumes: `Provider`, `OutputCb` from `providers/base.py`; `AuthState`, `Identity`, `LoginResult`, `ProviderStatus` from `models.py`; `oci_config.read_profiles`.
- Produces: `class OCIProvider(Provider)` with `key = "oci"`, `label = "OCI"`, `cli_tool = "oci"`, `profile_label = "Profile"`, `secondary_label = "Compartment"`;
  - `status(self, profile: str | None = None) -> ProviderStatus`
  - `login_methods(self) -> list[str]` returns `["session", "profile", "api-key"]`
  - `login(self, method="session", on_output=None, **kwargs) -> LoginResult` (kwargs: `profile`, `region`; api-key kwargs defined in Task 5)
  - `logout(self, profile=None) -> LoginResult`

- [ ] **Step 1:** Write failing tests: `test_status_not_installed` (no `oci` on PATH), `test_status_no_config_file_unauthenticated`, `test_status_session_valid` (profile has `security_token_file`; fake `oci session validate --profile P --local` rc 0 and token JWT `exp` in the future → `AUTHENTICATED`, `identity.expires_at` set), `test_status_session_expired` (→ `EXPIRED`), `test_status_api_key_ok` (fake `oci iam region list --profile P` rc 0 → `AUTHENTICATED`, identity.account = tenancy OCID, user = user OCID, extra region), `test_status_api_key_offline_is_error` (rc 124 → `ERROR`), `test_login_session_runs_authenticate` (asserts command `["oci","session","authenticate","--profile-name",P,"--region",R]` streamed and output forwarded), `test_login_profile_method_only_validates` , `test_logout_session_runs_terminate` (`oci session terminate --profile P`), `test_logout_api_key_profile_not_supported_message`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement `OCIProvider`; token expiry from the `exp` claim of the JWT at `security_token_file` (decode payload manually, no signature check, no new dependency). Use the `--local` validate flag only if the installed `oci` supports it; otherwise rely on the JWT `exp` alone. **Step 4:** Run; expect PASS. Commit `feat(oci): provider status and login`.

### Task 4: Compartment selection

**Files:**
- Modify: `src/cloud_login/providers/oci.py`
- Test: `tests/test_oci_compartments.py`

**Interfaces:**
- Consumes: `settings.get/set/delete` from `cloud_login.settings`.
- Produces: on `OCIProvider`:
  - `list_secondary(self) -> list[tuple[str, str]]` returns `(compartment_ocid, "root/dev/team-a")` pairs, tenancy root first, active-only, sorted by path; cached 60 seconds in memory per profile.
  - `set_secondary(self, sid: str) -> LoginResult` stores `oci.compartment.<profile>` in settings.
  - `active_compartment(self, profile: str | None = None) -> str | None` returns stored value or `None`.

- [ ] **Step 1:** Write failing tests with `fake_run` returning realistic `oci iam compartment list --compartment-id <tenancy> --compartment-id-in-subtree true --all --lifecycle-state ACTIVE --output json` JSON: `test_list_builds_paths_from_parent_ids`, `test_list_includes_tenancy_root`, `test_list_cached_within_60s` (one subprocess call for two lists), `test_set_secondary_persists_per_profile` (two profiles keep different values), `test_stale_saved_compartment_dropped` (saved id missing from list → `active_compartment` returns `None` after a list call and setting is deleted), `test_list_failure_returns_empty_and_does_not_raise`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. Compartment paths are built from `compartment-id` parent links; tenancy OCID comes from the profile. **Step 4:** Run; expect PASS. Commit `feat(oci): compartment selection`.

### Task 5: API-key login method

**Files:**
- Modify: `src/cloud_login/providers/oci.py`
- Test: `tests/test_oci_api_key_login.py`

**Interfaces:**
- Consumes: `oci_config.write_api_key_profile`, `generate_key_pair`, `validate_ocid`, `KNOWN_REGIONS`.
- Produces: `login(method="api-key", ...)` kwargs: `profile: str`, `user: str`, `tenancy: str`, `region: str`, `key_file: str | None`, `generate_key: bool = False`, `overwrite: bool = False`. Result `LoginResult.message` includes next-step text. When a key pair is generated, `LoginResult.output` contains the **public** key PEM only, with the console upload instruction. Add `OCIProvider.api_key_defaults() -> dict[str, str]` (profile name `DEFAULT`/`my-profile`, region from the active profile or `us-ashburn-1`) for form pre-fill.

- [ ] **Step 1:** Write failing tests: `test_rejects_invalid_user_ocid`, `test_rejects_unknown_region_with_hint`, `test_generate_key_writes_profile_and_returns_public_key_only` (assert private key text absent from `output` and from every `on_output` call), `test_existing_key_file_missing_is_error`, `test_existing_profile_requires_overwrite`, `test_verifies_after_write` (a fake `oci iam region list` failure yields `ok=False` with a message pointing at the console upload step and fingerprint).
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement; verification runs `oci iam region list --profile <name>`; on failure, keep the profile but report that the public key must be uploaded. **Step 4:** Run; expect PASS. Commit `feat(oci): api-key login`.

### Task 6: Registry, icon, CLI wiring

**Files:**
- Modify: `src/cloud_login/providers/__init__.py` (add `OCIProvider` to `all_providers()` after Azure, and `__all__`), `src/cloud_login/ui/cli.py` (OCI login flags and compartment subcommands following how the existing providers do it), `README.md` (OCI row and a short OCI section), `CHANGELOG.md`, `tests/test_smoke.py` (registry now six keys, including `oci`)
- Create: `src/cloud_login/assets/oci.svg` (simple red-circle-ring mark, same viewBox style as the sibling icons)
- Test: `tests/test_cli_oci.py` (typer `CliRunner` with a fake provider run table)

**Interfaces:**
- Consumes: `OCIProvider` (Tasks 3–5). CLI flags for `login oci -m api-key`: `--profile`, `--user`, `--tenancy`, `--region`, `--key-file`, `--generate-key`, `--overwrite`.

- [ ] **Step 1:** Update the smoke test and write `test_cli_status_includes_oci`, `test_cli_login_oci_session`, `test_cli_login_oci_api_key_flags`, `test_cli_set_compartment`. **Step 2:** Run; expect FAIL. **Step 3:** Implement wiring; existing commands for other providers untouched. **Step 4:** Run the full suite; expect PASS. Commit `feat(oci): register provider, CLI, icon, docs`.

### Task 7: TUI form for OCI API-key login (parallel with Task 8)

**Files:**
- Modify: `src/cloud_login/ui/tui.py` (OCI-only branch alongside the existing `aws`/`vultr` conditionals near lines 93 and 299)
- Test: `tests/test_tui_oci.py` (Textual `run_test` pilot)

**Interfaces:**
- Consumes: `OCIProvider.api_key_defaults()`, `OCIProvider.login(method="api-key", ...)`.
- Produces: when provider key is `oci` and method is `api-key`, the pane shows inputs for profile, user OCID, tenancy OCID, region, key file, and a "generate key pair" checkbox; inline validation messages; submit calls `login` with the Task 5 kwargs; public key is shown in the log pane.

- [ ] **Step 1:** Write failing tests: `test_oci_form_hidden_for_other_methods`, `test_oci_form_visible_for_api_key`, `test_invalid_ocid_shows_message_without_calling_login`, `test_submit_calls_login_with_kwargs`, `test_other_providers_panes_unchanged` (aws/vultr token input still present, no OCI widgets).
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS. Commit `feat(oci): TUI api-key form`.

### Task 8: GUI form for OCI API-key login (parallel with Task 7)

**Files:**
- Modify: `src/cloud_login/ui/gui.py` (OCI-only branch near lines 343 and 614)
- Test: `tests/test_gui_oci.py` (`QT_QPA_PLATFORM=offscreen`; skip if PySide6 import fails)

**Interfaces:**
- Consumes: same as Task 7.
- Produces: same fields as Task 7 as `QLineEdit`s with a `QFileDialog` browse button for the key file, a generate checkbox, a copy-public-key button after generation, inline validation, form visible only for method `api-key`.

- [ ] **Step 1:** Write failing tests mirroring Task 7's names (`test_gui_oci_form_*`) plus `test_gui_other_providers_unchanged`. **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS. Commit `feat(oci): GUI api-key form`.

### Task 9: Menubar check, build, and release verification

**Files:**
- Modify: `src/cloud_login/ui/menubar.py` only if OCI does not appear or errors via the registry; `pyproject.toml` (version `0.2.0`), `CHANGELOG.md`
- Test: `tests/test_menubar_oci.py` (provider list includes `oci`, import-only on non-macOS)

- [ ] **Step 1:** Write `test_menubar_lists_oci_provider`; run; fix only if failing. **Step 2:** Bump version to `0.2.0`; changelog entry "Added OCI provider (session, API-key, existing-profile login; profile and compartment pickers)". **Step 3:** Run `uv run ruff check . && uv run ruff format --check . && uv run pytest -v`. Expected: all pass. **Step 4:** `uv build`; `uvx twine check dist/*` → PASSED for sdist and wheel; `unzip -l dist/*.whl | grep -c svg` → 6. **Step 5:** In a fresh temp venv outside the repo (`uv venv /tmp/…` via the scratchpad), `uv pip install dist/*.whl`, then `cloud-login --help` and `cloud-login status -p oci` run without traceback; assert `oci.svg` resolves from the installed package. **Step 6:** `grep -ril <former-employer-name> . dist/` returns nothing outside `docs/`; also check inside the sdist and wheel. **Step 7:** Commit `release: 0.2.0`. Do not upload.
