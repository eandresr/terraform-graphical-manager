# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] — 0.3.0

> Branch: `feature/git-integration`

### Added

#### Git Integration
- New `app/git_manager.py` module: centralised git operations with PAT token resolution.
- **Branch / tag / release selector** in the workspace Overview tab: searchable dropdown
  lists all local and remote refs in the cloned repository.
  - Branches that exist only on the remote are shown with a **↓ remote** amber badge.
  - Branches that exist both locally and on the remote show a **↕** sync indicator.
  - Selecting any entry performs `git checkout` on disk (from the git root) so the
    workspace files and `.tf` variables reflect the chosen ref immediately.
  - For remote-only branches `git fetch origin` is run automatically before checkout so
    git's DWIM creates a local tracking branch.
- **Fetch button** in the git card header: runs `git fetch --all --prune` to refresh
  all remote refs without switching branches. The ref list updates in place after fetch.
- **Pull on next run** checkbox: when enabled, `git pull` is executed just before the
  Terraform runner starts, ensuring the workspace uses the latest remote code.
- **Run labels**: every execution records its git ref at submission time.
  - Green badge — code was pulled from remote: `git-branch:main`
  - Amber badge — local code was used (pull skipped): `git-branch:main (local)`
- **Apply Preview** idle state for git workspaces: a dedicated start screen lets the user
  configure the git pull option before kicking off the plan stage.
- **PAT token resolution** (priority order):
  1. `GITHUB_TOKEN` / `GIT_TOKEN` environment variable.
  2. Workspace-level `env`-type variable named `GITHUB_TOKEN` or `GIT_TOKEN`.
  3. Variable groups visible to the workspace.
- Token injected via `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0` — never written to
  `.git/config`.
- New REST API endpoints:
  - `GET  /api/workspace/<id>/git/refs` — list branches (with local/remote flags), tags and current HEAD.
  - `POST /api/workspace/<id>/git/checkout` — checkout a ref on disk; accepts `remote_only` flag.
  - `POST /api/workspace/<id>/git/fetch` — run `git fetch --all --prune`.

### Fixed
- **Git boundary detection**: `_has_git_repo` and `is_git_repo` now stop walking parent
  directories at `repos_root`, preventing workspaces inside the application source tree
  from inheriting the application's own git repository.
- **Git operations now run from the git root** (`git rev-parse --show-toplevel`), ensuring
  `git checkout` and `git pull` affect the full working tree rather than a subdirectory.
- **Dropdown closes when typing**: `@click.outside` was placed on the results list instead
  of the wrapper element, causing focus on the search input to be treated as an outside
  click. Moved to the outer `<div>`.
- **Search filter not reactive**: branch/tag groups used `x-if` (destroys DOM when false),
  which prevented Alpine from re-evaluating the inner `x-for` on each keystroke. Changed
  to `x-show` so reactivity is maintained throughout typing.

---

## [0.2.0] — 2026-03-31

> Branch: `feature/workspace-vars`

### Added

#### Variable Groups
- New `app/variable_groups.py` module: full CRUD for named groups of Terraform/environment variables.
- Variables inside a group can be marked as **sensitive** — their values are Fernet-encrypted at rest using the portal password as the key material.
- Groups support three scope modes:
  - Applied to **all** workspaces (`workspace_ids = ["*"]`)
  - Applied to **specific** workspaces (list of IDs)
  - Draft / unassigned (`workspace_ids = []`)
- **Variable Groups sidebar panel** in `base.html`: accessible from every page via a dedicated sidebar button. Supports create, edit, delete, and "Used in" modal (shows which workspaces a group is assigned to, with direct navigation links).
- **Variable Groups sub-tab** inside the workspace Variables tab: lets users assign/unassign groups to the current workspace without leaving the workspace view.
- New REST API endpoints:
  - `GET /api/variable-groups` — list groups for a specific workspace
  - `GET /api/variable-groups/all` — list all groups
  - `POST /api/variable-groups` — create a group
  - `GET /api/variable-groups/<id>` — get a single group
  - `PUT /api/variable-groups/<id>` — update a group
  - `DELETE /api/variable-groups/<id>` — delete a group

#### Workspace Variables
- New **Variables sub-tab** in the workspace view (alongside the existing Variable Groups sub-tab) for managing individual per-workspace variables.
- Variables can be of type `terraform` (injected as `TF_VAR_<key>`) or `env` (injected as `<key>=<value>`).
- Sensitive values are encrypted on save; leaving the value field blank when editing a sensitive variable preserves the existing encrypted blob.
- New REST API endpoints:
  - `GET /api/workspace/<id>/vars` — retrieve variables (sensitive values masked)
  - `PUT /api/workspace/<id>/vars` — save variables (encrypts sensitive values; blank = keep existing)

#### Portal Security (Lock)
- New `app/crypto.py` module: Fernet symmetric encryption with a key derived from the portal password via SHA-256.
- Setting a portal password now **enables encryption** for all sensitive variable values — the sensitive toggle is disabled in the UI and rejected by the API when no password is configured.
- **Password change** automatically re-encrypts all existing sensitive variable values with the new password (via `reencrypt_all_sensitive()`). No manual migration is needed.
- Improved **Remove Lock** flow:
  - Replaced the native `window.confirm()` dialog with a full Alpine.js modal.
  - The modal fetches and displays the **full list of affected sensitive variables** (in `folder → workspace → group → variable` format) before the user confirms.
  - Optional **"Decrypt and store as plain text"** checkbox: if checked, all sensitive variable values are decrypted and stored as plaintext before the lock is removed, so no data is lost.
  - All modal copy is in English.
- New REST API endpoints:
  - `GET /api/sensitive-vars-summary` — list all sensitive variables with their full path context
  - `POST /api/variable-groups/unsensitize-all` — decrypt all sensitive variables and store as plaintext (used by the remove-lock flow)

### Fixed
- **Run detail "Values" tab was empty**: all four storage backends (`local`, `aws`, `azure`, `gcp`) now correctly include `run_params` in `_build_metadata()` when persisting execution records.

---

## [0.1.0] — 2026-03-27

> Initial public release. Branch: `main`

### Added

#### Core
- Flask web application with multi-workspace Terraform management.
- Workspace auto-discovery via directory scanning (`workspace_scanner.py`).
- Dashboard with workspace cards grouped by folder.
- Run execution: `plan`, `apply`, `destroy` with real-time log streaming.
- Plan diff viewer: colour-coded resource changes parsed from `terraform plan -json` output.
- State viewer: parsed resource tree from `terraform show -json`.
- Execution history with status badges and detail pages.
- Variable injection for runs: supports `TF_VAR_*` and environment variables.

#### Sentinel
- Sentinel policy set management: create, edit, assign policy sets to workspaces.
- Automatic Sentinel checks injected between `plan` and `apply` stages.
- Sentinel results surfaced in execution detail pages and run logs.
- Policy enforcement modes: `advisory`, `soft-mandatory`, `hard-mandatory`.

#### Preview
- `terraform preview` command support (alias for extended plan output).
- Dedicated Preview button and result view in the workspace UI.

#### Terraform Version Management
- Per-workspace Terraform version display.
- Local installed versions listed via `version_manager.py`.

#### Authentication
- Optional portal password lock (session-based).
- Login page with configurable password via `tfg.conf`.

#### Storage Backends
- Local filesystem backend (default).
- AWS S3 backend.
- Azure Blob Storage backend.
- Google Cloud Storage backend.

#### Configuration
- `tfg.conf` file-based configuration (`config/tfg.conf.example` provided).
- CLI entry point (`tfg` command) via `pyproject.toml`.

---

[Unreleased]: https://github.com/eandresr/terraform-graphical-manager/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/eandresr/terraform-graphical-manager/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/eandresr/terraform-graphical-manager/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/eandresr/terraform-graphical-manager/releases/tag/v0.1.0
