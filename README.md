# cloud-login

One place to see "am I logged in?" across AWS, GCP, Azure, Oracle Cloud (OCI), Vultr, and Digital Ocean — and to re-auth without remembering each tool's commands.

The underlying CLIs (`aws`, `gcloud`, `az`, `doctl`/`vultr-cli`) remain the source of truth. This app wraps them: status checks, login flows (SSO browser, ADC, device code, API key), profile/configuration/subscription/project switching, and surfaces SSO token expiration.

## Surfaces

The same provider layer drives four interchangeable UIs:

| Command                | UI       | Use case                                                     |
| ---------------------- | -------- | ------------------------------------------------------------ |
| `cloud-login`          | GUI      | PySide6 desktop window with tabs per provider (default).     |
| `cloud-login tui`      | TUI      | Textual terminal UI with the same tabs.                      |
| `cloud-login menubar`  | menubar  | macOS resident tray icon with aggregate health + per-provider menu. |
| `cloud-login status`   | CLI      | Status table for all providers (or one with `-p aws`).       |

Every action in the GUI/TUI/menubar is also a CLI subcommand. Run `cloud-login --help` to see all.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Tested on Python 3.12 and 3.13 (on Python 3.15, PySide6 6.12 breaks Textual's worker threads when the GUI and TUI run in one process).

```bash
# Install globally so `cloud-login` is on PATH everywhere
uv tool install cloud-login
# or
pipx install cloud-login
```

To upgrade: `uv tool upgrade cloud-login` (or `pipx upgrade cloud-login`).

To uninstall: `uv tool uninstall cloud-login`.

## Providers

| Provider     | Backed by           | Profile concept   | Secondary picker | Credentials                                |
| ------------ | ------------------- | ----------------- | ---------------- | ------------------------------------------ |
| AWS          | `aws` CLI           | Profile           | —                | SSO browser, or AKID/secret to `~/.aws/credentials` |
| Google Cloud | `gcloud` CLI        | Configuration     | Project          | `gcloud auth login` (+ ADC), service accounts via gcloud |
| Azure        | `az` CLI            | —                 | Subscription     | `az login` (browser or `--use-device-code`) |
| OCI          | `oci` CLI           | Profile (in `~/.oci/config`) | Compartment | Session browser auth, API key (generated or existing), or an existing profile |
| Vultr        | api.vultr.com       | —                 | —                | API key — env, `~/.vultr-cli.yaml`, or macOS Keychain |
| DigitalOcean | api.digitalocean.com| —                 | —                | API token — env, `~/.config/doctl/config.yaml`, or macOS Keychain |

For Vultr/DO the API key **is** the credential. `Logout` is a no-op informational message; use **Forget Key** (or `cloud-login forget vultr`/`digitalocean`) to actually remove the stored key from Keychain.

## Common CLI subcommands

```bash
cloud-login status                       # all providers
cloud-login status -p aws --profile foo  # one provider, specific profile

cloud-login login aws -m sso --profile my-profile
cloud-login login aws -m access-key --profile foo --region us-east-1
cloud-login login gcp                    # default method is user+adc (two browsers)
cloud-login login gcp -m adc
cloud-login login azure                  # `az login`
cloud-login login azure -m device-code   # headless / SSH

cloud-login profiles aws
cloud-login projects                     # GCP
cloud-login subscriptions                # Azure
cloud-login compartments                 # OCI
cloud-login set-project <project-id>     # GCP
cloud-login set-subscription <sub-id>    # Azure
cloud-login set-compartment <ocid>       # OCI

cloud-login logout aws                   # `aws sso logout`
cloud-login forget vultr                 # delete keyring entry
```

## OCI

Three login methods:

```bash
# Browser session (token-based, expires; refresh by logging in again)
cloud-login login oci -m session --profile my-profile --region us-ashburn-1

# Use a profile that already exists in ~/.oci/config (it becomes the active profile)
cloud-login login oci -m profile --profile my-profile

# API key: generate a new key pair (or pass --key-file <path> for an existing one)
cloud-login login oci -m api-key --profile my-profile --user <user-ocid> \
  --tenancy <tenancy-ocid> --region us-ashburn-1 --generate-key
```

Missing api-key fields are prompted for; there is no secret to type. Use `--overwrite` to replace an existing profile.

**Session login on an API-key profile.** `oci session authenticate` converts the profile to a session profile (removing its key entries) and rewrites `~/.oci/config` without comments, so cloud-login refuses unless you confirm (GUI/TUI) or pass `--overwrite` (CLI). Use a new profile name to keep the API-key profile.

**Logout removes the profile.** `oci session terminate` deletes the session profile from `~/.oci/config`, so `cloud-login logout oci` asks first (`--yes` skips the prompt); log in with the session method and the same name to recreate it. API-key profiles cannot be logged out.

**Console upload required.** After API-key login the public key is printed; you must upload it in the OCI console (Identity -> Users -> your user -> API keys -> Add API key) before the profile can authenticate.

Compartments:

```bash
cloud-login compartments                 # id and path for the active profile
cloud-login set-compartment <ocid>       # add --profile <name> to either for another profile
```

Nothing is stored in `~/.oci/config` except standard profile entries; the compartment choice lives in `~/.config/cloud-login/settings.json`.

## Auto-start the menu bar at macOS login

```bash
cloud-login autostart install      # writes ~/Library/LaunchAgents/com.cloud-login.menubar.plist and launchctl loads it
cloud-login autostart status       # check whether it's installed and loaded
cloud-login autostart uninstall    # remove the LaunchAgent
```

The launchd plist runs `cloud-login menubar`. After install, it'll start automatically on next login; logs go to `~/Library/Logs/cloud-login/menubar.{log,err}` (a private, owner-only directory). Re-run `cloud-login autostart install` to move an existing install off the old `/tmp` log paths.

## Where things live

| Path                                       | What                                             |
| ------------------------------------------ | ------------------------------------------------ |
| `~/.aws/config`, `~/.aws/credentials`      | AWS profiles (managed by `aws` CLI)              |
| `~/.aws/sso/cache/*.json`                  | SSO tokens; expiry is read from here             |
| `~/.config/gcloud/`                        | gcloud configurations + ADC                      |
| `~/.azure/`                                | Azure CLI session                                |
| `~/.oci/config`, `~/.oci/sessions/`, `~/.oci/keys/` | OCI profiles, session tokens, generated API keys |
| `~/.vultr-cli.yaml`                        | Vultr CLI config (optional)                      |
| `~/.config/doctl/config.yaml`              | doctl config (optional)                          |
| macOS Keychain, service `cloud-login`      | Stored Vultr/DO API keys                         |
| `~/.config/cloud-login/settings.json`      | App preferences (window geometry, last-selected profile per provider, etc.) |

Nothing is read or written outside these. The app never bypasses the underlying CLIs for credential material.

## Project layout

```
src/cloud_login/
├── __init__.py            # main() entry point
├── models.py              # ProviderStatus, Identity, format_expires
├── settings.py            # JSON-backed persistent preferences
├── secrets.py             # macOS Keychain wrapper for Vultr/DO
├── assets/                # bundled provider logo SVGs
├── providers/
│   ├── base.py            # abstract Provider + subprocess helpers
│   ├── aws.py             # `aws` CLI + SSO cache parsing for expiry
│   ├── gcp.py             # `gcloud` CLI + project list
│   ├── azure.py           # `az` CLI + subscription list
│   ├── oci.py             # `oci` CLI + compartments (oci_config.py reads/writes ~/.oci/config)
│   ├── vultr.py           # HTTP to api.vultr.com + keyring
│   └── digitalocean.py    # HTTP to api.digitalocean.com + keyring
└── ui/
    ├── cli.py             # Typer commands (entry point routes here)
    ├── tui.py             # Textual TUI
    ├── gui.py             # PySide6 GUI (default)
    └── menubar.py         # macOS menu-bar status app
```

Adding a sixth provider means writing one file in `providers/` and adding it to `providers/__init__.py:all_providers()`. The UI shape is data-driven via `profile_label` and `secondary_label` attributes on the Provider — no UI changes needed for typical providers.

## Building a wheel

```bash
uv build                              # produces dist/cloud_login-<version>-py3-none-any.whl + .tar.gz
```
