# line-cli setup

Matches line-cli v0.4.0 (verified 2026-10-12)

Setup covers installing the `line` binary, signing in, where the session is stored, how tokens
refresh, Linux keyring and headless storage, checking for updates, and product limits.

Upstream sources: `README.md`, `docs/CLI.md` (Get started, Account and session, Linux servers),
`TOKEN_SESSION.md`, `scripts/install-release.sh`, `scripts/install-release.ps1`,
`internal/session/keychain_*.go`, `internal/session/lock_*.go`, `internal/update/update.go`.

## Contents

- Install per OS
- Verify the install
- Sign in (login methods)
- Session storage per OS
- Token refresh and when to log in again
- Linux: keyring packages and headless storage
- Services and watchers
- Check for updates
- Limits

## Install per OS

### macOS: Homebrew (recommended)

```sh
brew install kongesque/tap/line-cli
```

Homebrew owns the executable. `line update` does not replace it; it prints `brew upgrade line-cli`
(`internal/update/update.go:68-70`).

### macOS and Linux: standalone installer

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/kongesque/line-cli/main/scripts/install-release.sh | sh
```

- Detects OS and architecture, downloads the release archive, and verifies it against the release
  SHA-256 checksums (`scripts/install-release.sh:50-51`).
- Installs `line` to `$LINE_CLI_INSTALL_DIR` if set, otherwise `~/.local/bin` (`install-release.sh:6`).
- Writes a `.line-cli-install` receipt next to the binary (`install-release.sh:81`). Self-update
  requires this receipt.
- Adds `~/.local/bin` to `PATH` in common shell profiles if missing (`install-release.sh:84-105`).
  Reopen the terminal if prompted.

### Windows: PowerShell installer

```powershell
irm https://raw.githubusercontent.com/kongesque/line-cli/main/scripts/install-release.ps1 | iex
```

- Installs `line.exe` to `%LOCALAPPDATA%\line-cli\bin` (`scripts/install-release.ps1:12`), or to
  `$env:LINE_CLI_INSTALL_DIR` if set (`install-release.ps1:9-10`).
- Verifies the archive SHA-256 (`install-release.ps1:44`) and writes the `.line-cli-install` receipt
  (`install-release.ps1:63`).
- Under Git Bash, PATH starts with Git's GNU `tar`, which reads `C:\...` as a remote host and makes
  the installer's `tar.exe` call fail. `setup.py --install` therefore runs the installer with
  `%SystemRoot%\System32` first on PATH (Windows' bsdtar). When installing by hand, use PowerShell.
- Adds the directory to the user `PATH`. Reopen PowerShell if `line` is not found.
- No extra keyring package is needed on Windows (`README.md:62`).
- `line update` on Windows does not replace the binary. It prints a PowerShell command to run after
  the command exits (`internal/update/update.go:74-78`). `can_self_update` is false on Windows
  (`internal/update/update.go:66`).

### Manual download

[GitHub Releases](https://github.com/kongesque/line-cli/releases/latest). Release binaries are
unsigned and not notarized (`docs/CLI.md:54-55`, `README.md:64-66`). Without the receipt, self-update
is disabled.

### Build from source

Requires Git and Go 1.26 or newer (`docs/CLI.md:748-751`).

macOS or Linux:

```sh
git clone https://github.com/kongesque/line-cli.git
cd line-cli
./install.sh
```

Windows PowerShell:

```powershell
git clone https://github.com/kongesque/line-cli.git
Set-Location line-cli
$env:CGO_ENABLED = "0"
go build -trimpath -o .\bin\line.exe ./cmd/line
.\bin\line.exe help
```

macOS source builds need Xcode Command Line Tools and CGO for Keychain support (`docs/CLI.md:750-751`).
Source builds never self-update (`installation` = `source`).

## Verify the install

```sh
line version
line help
```

`line version` prints `line <version>` (`internal/cli/cli.go:113-115`). Neither command contacts LINE.

## Sign in (login methods)

| Method | Command | Notes |
| --- | --- | --- |
| QR (default, experimental) | `line login` | Shows a terminal QR code. Scan with LINE on the phone, approve, enter the PIN if asked. Wait for `Session saved securely` (`docs/CLI.md:74-76`). Needs Letter Sealing enabled on the account; accounts with it disabled are not supported (`docs/CLI.md:120`). |
| Email and password | `line login --email you@example.com` | Password is read from the terminal with echo off and never saved. Phone verification may follow. |
| QR URL (sensitive) | `line login --qr-url` | Prints the one-time QR value for a trusted local QR tool. Treat it as a secret (`docs/CLI.md:107-111`). |
| Replace without asking | `line login --force` | Skips only the "replace saved session?" question (`docs/CLI.md:129-132`). |
| Headless (Linux) | `line login --headless` | Enrolls host-key storage on a Linux host without an unlocked keyring. See below. |

Rules that apply to every method:

- Login needs a real terminal. QR and headless enrollment refuse to run without one, before LINE is
  contacted (`internal/cli/login.go:57-59`). Email login needs a terminal to read the password
  (`cmd/line/password.go:36-38`). An agent cannot complete login; ask the user to run it.
- `--email` cannot be combined with `--qr` or `--qr-url` (`login.go:48-50`).
- If a usable session is saved, login asks before replacing it. Answer no to keep it
  (`login.go:101-139`).
- Signing in may sign out the LINE Chrome extension or another Chrome-style client, because LINE allows
  one such session at a time (`docs/CLI.md:10-12`).
- One device at a time: logging in with line-cli on a second computer invalidates the first.
  The first then fails with "LINE session was invalidated or replaced by another client; run line
  login" (classified as `session_replaced`). Ask the user before logging in again; doing so signs
  the other device out.
- The login window opened by `login.py` closes itself 3 seconds after a successful login. On
  failure or cancel it stays open with the error and waits for Enter.
- `setup.py --install` reports a `verification` object: the installer script's SHA-256 against the
  pinned hash, and the release archive checked by the upstream installer against `SHA256SUMS.txt`.
  Homebrew installs are not pinned or verified by this skill.
- Ctrl-C during login leaves the old local session in place if the new one was not saved
  (`docs/CLI.md:138-141`).
- For SSH: `ssh -t user@host` then `line login` (`docs/CLI.md:146-153`).

After login, check the account with `line whoami`, then `line chats`.

## Session storage per OS

One LINE account per operating-system user (`docs/CLI.md:456`).

| OS | Where the session lives | Key or protection |
| --- | --- | --- |
| macOS | macOS Keychain (`keychain_darwin.go`). | Keychain. Requires CGO in source builds. |
| Linux | Encrypted session file `session.enc` in `$XDG_CONFIG_HOME/line-cli` (normally `~/.config/line-cli`) (`internal/session/keychain_linux.go:9-21`). | The file's encryption key is stored in Secret Service, accessed through `secret-tool` (`internal/session/secret_tool_linux.go:33`). |
| Windows | DPAPI-encrypted file `session.dpapi` in `%LOCALAPPDATA%\line-cli` (`internal/session/keychain_windows.go:20-29` uses the user cache directory). | Current-user DPAPI. |
| Headless Linux | Host-key protected session, see below. | Host key, no TPM. Not protected from root or malware running as your account. |

Locks live in the same application directory on each platform (for example
`%LOCALAPPDATA%\line-cli\session.lock`, `internal/session/lock_windows.go:20-30`).

Linux directory and file permissions must be private: the application directory `0700` and files
`0600`. The CLI rejects unsafe ownership, file types, symlinks, and hard links (`docs/CLI.md:689-691`).

Your password is never saved (`docs/CLI.md:464`). Logout deletes the local session only
(`docs/CLI.md:483-489`).

## Token refresh and when to log in again

- The normal access token reaches its refresh boundary about seven days after issue
  (`docs/CLI.md:493`; `TOKEN_SESSION.md:74`). This is not a fixed session lifetime.
- If the saved refresh token still works, the CLI renews access automatically, including after a restart
  (`docs/CLI.md:493-496`).
- You need `line login` again only when refresh is rejected, the refresh token is missing, or LINE sends
  an explicit logout signal (`docs/CLI.md:503-505`).
- A generic 401 or 403, a network failure, or a token expiry alone does NOT invalidate the saved session
  (`docs/CLI.md:503-505`).
- Reads may refresh and retry once. Sends, reactions, unsend, and uploads are never replayed automatically
  (`docs/CLI.md:498-499`).
- If storage fails after a token rotation, the CLI stops further requests rather than risk losing the new
  token (`docs/CLI.md:506-508`).

For the audit trail, see `TOKEN_SESSION.md` in the upstream repo.

## Linux: keyring packages and headless storage

### Desktop Linux (Secret Service)

Debian and Ubuntu:

```sh
sudo apt install libsecret-tools gnome-keyring
```

- `libsecret-tools` provides `secret-tool`. Other distributions need the equivalent packages
  (unverified: package names for other distributions were not checked).
- The keyring must be running and unlocked in the same D-Bus session as `line`
  (`docs/CLI.md:40-43`). A locked keyring returns exit 69 (`native storage is unavailable or locked`,
  see errors.md).

### Linux servers and SSH (headless storage)

Use this when there is no unlocked keyring. It is experimental and supported only on specific hosts.

```sh
line login --headless --email you@example.com
line auth status
line auth status --check --json
```

- Enrollment is interactive. It asks you to accept **Host key; no TPM** protection before contacting LINE
  (`docs/CLI.md:530-535`). `--force` does not skip the consent.
- Headless protection does not stop root, malware running as your account, or someone with a full disk
  copy. It does not claim TPM protection. Reboot access is expected, not verified (`docs/CLI.md:530-534`).
- Needs the trusted helper `/usr/bin/systemd-creds` and a user credential broker. Use the same Unix account
  and config directory for interactive commands, SSH, cron, and services (`docs/CLI.md:541-547`).
- Accepted systemd versions: 256 through 259. Debian 13 (systemd 257) and Ubuntu 26.04 (systemd 259) were
  validated in disposable VMs. Other accepted versions still need a local check (`docs/CLI.md:541-547`).
- To move an existing native Linux session: `line auth migrate --storage=headless` (interactive, needs
  `yes`). Stop watchers and older CLI versions first (`docs/CLI.md:549-566`).

## Services and watchers

- Only one `line watch` may run at a time (`internal/session/lock_unix.go:22-28`). Stop it before
  `line update`.
- A user service needs the same `HOME` and `XDG_CONFIG_HOME` as the enrolled account. Do not add `User=`
  to a user service (`docs/CLI.md:576-579`). A reference unit file is in `docs/CLI.md:581-602`.
- Observed failures in one user-service environment: `PrivateTmp=true` and `ProtectSystem=strict` make the
  headless helper unavailable (exit 69); `ProtectHome=read-only` causes a nonzero exit (`docs/CLI.md:627-640`).
  `PrivateUsers=true` can hide the root-owned helper, which the CLI then rejects (`docs/CLI.md:642-646`).
  Check with `line auth status --check --json` run under the service's sandbox.
- Watch output is private message data. Restrict access to the journal or output files
  (`docs/CLI.md:622-623`).

## Check for updates

```sh
line update --check        # human-readable, no changes
line update --check --json # one JSON result, no changes
line update                # installs when supported; otherwise prints the next step
```

- Works without a LINE login. Contacts GitHub releases only.
- `--json` fields: `current_version`, `latest_version`, `status`, `installation`, `executable`,
  `can_self_update`, `release_url`, and optional `upgrade_command` and `instructions`
  (see json-schema.md).
- `status` values for a check: `update_available`, `up_to_date`, `ahead`, `unknown_version`.
- `installation` values: `standalone` (official installer), `homebrew`, `source`, `unknown`.
- Self-update works only on macOS and Linux official standalone installs with the receipt
  (`internal/update/update.go:66`). Windows always reports `can_self_update: false`.
- Before updating, stop running commands and watchers, including any service (`docs/CLI.md:362-365`).

## Limits

- Message history: the latest 100 messages only, per read (`docs/CLI.md:791`). Older messages are not
  reachable by the CLI, so react, unsend, and download can only target the latest 100.
- Attachments: up to 20 MiB for `send --file` and for `download` (`docs/CLI.md:249`, `docs/CLI.md:283-284`).
- Text: up to 10,000 UTF-16 units per message (`internal/messaging/client.go:18`).
- Stdin input: up to 40,000 bytes, not trimmed (`internal/cli/messages.go:118-124`).
- External media URLs are not downloadable (`docs/CLI.md:286`, `internal/messaging/files.go:157-158`).
- Sending: generic files only. Images, video, and audio go as ordinary files. Stickers and specialized media
  cannot be sent (`docs/CLI.md:249-250`, `docs/CLI.md:792`).
- Reading does not mark messages as read (`docs/CLI.md:234`, `docs/CLI.md:795`).
- Some older encrypted messages may be unavailable if LINE no longer provides the original device keys
  (`docs/CLI.md:234-236`).
- QR login cannot handle accounts with Letter Sealing disabled (`docs/CLI.md:120`, `docs/CLI.md:789`).
- Release binaries are not signed or notarized (`docs/CLI.md:796`).
- LINE protocol changes may break compatibility at any time (`docs/CLI.md:797`).
