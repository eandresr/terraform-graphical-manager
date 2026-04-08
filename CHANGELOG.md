# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.2.0] — 2026-04-02

### Added

#### API Management Panel (`/api-docs`)
- Renamed and elevated the existing interactive API reference to the **API Management Panel**
  to better reflect its role as a full live client, not just documentation.
- Accessible via the **`</> API`** button in the top-right topbar on every page.
- All previously described features retained: Swagger-style grouped cards, colour-coded
  methods, live try-it panels, Bearer-token auto-retrieval, filter bar, collapse/expand all.

#### Variable Groups — dedicated sidebar page
- **Variable Groups** is now a persistent, first-class entry in the sidebar navigation,
  accessible from any page without opening a specific workspace.
- The global management page lists all groups across all workspaces and allows
  create / edit / delete / "Used in" directly from the sidebar shortcut.

#### Notification Channels — dedicated sidebar page
- **Notification Channels** is now a persistent entry in the sidebar navigation,
  accessible from any page.
- The global management page lists every channel (type badge, scope badge, trigger badges),
  allows creating new global channels, editing, deleting, and testing any channel inline.
- This complements the existing per-workspace Notifications tab (which continues to work
  for workspace-scoped channel management and assignment).

#### Manual "Migrate local data → cloud" panel in Settings
- A permanent **Migrate local data → cloud** section is now visible inside
  **Settings → Storage Backend** whenever a cloud backend (AWS / GCP / Azure) is configured.
- **Load diff** button — calls the new `GET /api/backend-config/diff` endpoint to compare
  object counts between the local backend and the configured cloud backend. The panel shows:
  - Counts for variable groups, notification channels, and executions in each backend.
  - Names of items that exist only in local storage (i.e. not yet migrated).
- **Migrate now** button — posts to `POST /api/backend-config/migrate` with
  `source_type: local` and copies all missing objects. Shows copied / skipped counts.
- **Delete local source data** button — appears after a successful migration, with
  confirmation, to clean up the local storage.
- Binary plan artefacts (`.binary` files) are intentionally excluded — only JSON records,
  logs, and configuration files are copied.
- New REST endpoint: `GET /api/backend-config/diff` — returns `only_in_source` /
  `only_in_dest` lists and counts for variable groups, notification channels, and
  executions across both backends.

#### Backend type selector product icons
- The 4-button backend type selector in Settings now displays cloud provider product
  images instead of generic SVG icons:
  - AWS S3 → `static/img/aws-s3.png`
  - GCP Cloud Storage → `static/img/google-bucket.png`
  - Azure Blob Storage → `static/img/azure-blob.png`
  - Local FS retains its original SVG icon.

### Fixed

#### Sensitive credential fields overwritten with encrypted mask on re-save
- **Root cause**: `GET /api/backend-config` returns sensitive fields masked as `••••••••`.
  The Settings form was pre-populating those fields with the mask on load, and
  `POST /api/backend-config` was encrypting the literal `••••••••` string and persisting
  it — silently replacing the real credential with an unusable encrypted placeholder.
- **Backend fix** (`app/routes/api_routes.py`): `save_backend_config_api` now treats both
  empty string and `••••••••` (8 bullet characters) as "keep existing encrypted value" —
  the real credential in `tfg.conf` is never touched unless a different plaintext value
  is explicitly typed.
- **Frontend fix** (`templates/settings.html`): `init()` no longer populates sensitive
  fields with the `••••••••` mask returned by the API — those fields load blank, preserving
  the correct "leave blank to keep current" UX.
- A new `savedSensitiveFields` array tracks which fields have an existing saved value so
  that an info icon (ⓘ) tooltip can inform the user without adding text that breaks grid
  layout.

#### Layout shift in backend config sensitive field labels
- The inline text `"✓ value saved — leave blank to keep"` added next to encrypted field
  labels was causing the AWS Secret Key input to drop out of alignment with the Access Key
  ID input in the two-column grid.
- **Fix**: replaced the inline text with a compact green ⓘ icon; hovering reveals a
  dark tooltip with the full explanation. No additional vertical space is consumed.
  Applied to all three sensitive fields: AWS Secret Key, GCP Service Account JSON, and
  Azure Client Secret.

---

## [1.1.0] — 2026-04-01

### Added

#### Offline-capable vendor assets
- All previously CDN-fetched JavaScript libraries are now bundled locally under
  `static/js/vendors/`, making TGM fully operational without internet access:
  - **Tailwind CSS** play-CDN build (`tailwind.cdn.js`)
  - **Alpine.js** (`alpine.min.js`)
  - **Socket.IO client** (`socket.io.min.js`)
  - **Chart.js** UMD bundle (`chart.umd.min.js`)
  - **D3.js** v7 minified bundle (`d3.v7.min.js`)
- Web fonts (Inter, JetBrains Mono) are served from `static/fonts/` with a
  companion `static/css/fonts.css` — no Google Fonts requests are made at runtime.
- All `<script src="…">` and font `@import` references in `base.html`, `login.html`,
  `workspace.html`, and `graph_view.html` updated to point at the local paths.

#### Storage Backend Configuration UI (`app/backend_config.py`)
- New `app/backend_config.py` module — centralised credential management for every
  supported storage backend without requiring environment variables.
- **Visual configuration panel** in **Settings → Storage Backend**:
  - 4-button type selector: Local / AWS S3 / GCP Cloud Storage / Azure.
  - Per-type form panels with all required connection fields (bucket/container,
    region/project, credentials, optional key prefix, etc.).
  - Sensitive fields (AWS secret key, GCP service-account JSON, Azure client secret)
    are displayed as password inputs and encrypted at rest.
  - Env-var override warning badge: displayed when `TERRAFORM_GRAPHICAL_BACKEND` is
    set in the process environment, indicating it takes precedence over saved config.
  - **Test connectivity** button — performs a write-read-delete probe against the
    configured backend and reports success or a descriptive error inline.
  - **Save backend config** button — persists settings to `[backend_credentials]`
    in `tfg.conf`.
- **Encryption**: sensitive credential fields are encrypted with Fernet
  (AES-128-CBC + HMAC-SHA256) using the portal password as key material — the same
  scheme used for variable groups. Requires a portal password to be set before
  configuring credentials for cloud backends.
- **AWS STS assume-role**: an optional `sts_role_arn` field instructs TGM to call
  `sts:AssumeRole` before every S3 operation. The temporary session credentials are
  injected automatically and never stored.
- **Data migration flow**:
  1. Switching to a different backend type and saving opens a confirmation modal.
  2. **Copy data** — streams every object from the source backend to the destination,
     reporting the count on completion.
  3. Optional **Delete source data** step with a second confirmation prompt, which
     removes all TGM-managed data from the former backend.
- **Backend resolution order** (updated):
  `TERRAFORM_GRAPHICAL_BACKEND` env var → `[backend_credentials].type` in `tfg.conf`
  → `local` (default).
- **New REST API endpoints**:
  - `GET  /api/backend-config` — returns current backend type and masked credentials.
  - `POST /api/backend-config` — saves backend type and credentials (sensitive fields
    encrypted; omitting a sensitive field preserves the existing encrypted value).
  - `POST /api/backend-config/test` — connectivity probe.
  - `POST /api/backend-config/migrate` — streams all data from source to destination backend.
  - `POST /api/backend-config/delete-source` — deletes all TGM data from a backend.

### Changed

- **`pyproject.toml`** — version `1.0.0 → 1.1.0`.
- **`app/storage/__init__.py`** — `get_backend()` resolves the backend type via
  `_resolve_type()` (env var → tfg.conf saved type → local) instead of reading the
  env var directly.
- **`app/storage/aws_backend.py`** — constructor falls back to saved backend credentials
  when AWS env vars are absent; `_execution_prefix` converted from `@staticmethod` to
  instance method to respect the configured prefix; `list_executions` uses the prefix.
- **`app/storage/gcp_backend.py`** — same pattern as the AWS backend: fallback to saved
  credentials, instance `_execution_prefix`, prefix-aware `list_executions`.
- **`app/storage/azure_backend.py`** — same pattern; supports both legacy connection-string
  and new service-principal credential sets side-by-side.
- **`app/routes/settings_routes.py`** — backend credentials are re-encrypted in the same
  password-change pass that already re-encrypts variable groups and notification channels.
- **`templates/settings.html`** — backend section completely rewritten using an Alpine.js
  component (`backendConfigSection()`); legacy static env-var instructions replaced by the
  new interactive configuration form.

#### Interactive API Reference UI (`/api-docs`)
- New page **`/api-docs`** — Swagger-style interactive REST API documentation.
- **`</> API`** button added to the top-right topbar on every page, next to the GitHub link.
- **Endpoint groups**: Workspaces, Executions, Workspace Execution Lock, Terraform Versions,
  Workspace Variables, Variable Groups, Git Integration, Sentinel Policy, Execution Statistics,
  Metrics Export, Notification Channels, Backend Configuration, Authentication.
- **Per-endpoint try-it panels**: inline inputs for path parameters, query parameters,
  and JSON request body (with a **Load example** button to pre-fill a working payload).
- **Live Send request**: fires the real HTTP request against the running TGM instance from
  the browser; formats the JSON response with syntax highlighting and a status code badge.
- **Copy response** button to copy the raw response body to the clipboard.
- **Auth banner**:
  - When portal lock is enabled: amber banner with a **Get Bearer token** button that calls
    `GET /api/settings/api-token` and auto-fills the token into all try-it panels.
  - When portal lock is disabled: green "No authentication required" banner.
- Token input field visible in every try-it panel when portal lock is active.
- **Filter bar**: filter endpoints by text (path, summary, description) and/or method badge.
- **Collapse / Expand all** controls.
- Implemented entirely with the vendored Alpine.js + Tailwind (no extra dependencies).
- New Flask route `GET /api-docs` registered in `workspace_routes.py`.
- New template `templates/api_docs.html`.

---

## [1.0.0] — 2026-03-31

First stable/production release. This version graduates TGM from beta (`0.x`) to a fully
featured local Terraform Cloud alternative with observability (execution charts, metrics
export) and alerting (notification channels) baked in.

### Added

#### Execution Statistics Charts
- Per-workspace **Run History** card in the Overview tab with two Chart.js line charts:
  - **Duration trend** — wall-clock execution time (seconds) per run, chronologically.
  - **Resource changes** — create / update / destroy counts per plan run.
- `GET /api/workspace/{id}/stats` — returns the series data consumed by the charts.
- **Refresh** button to reload the series without leaving the tab.

#### Metrics Export (`app/metrics_exporter.py`)
- Push execution metrics to an external time-series system after every run.
- Three supported backends:
  - **InfluxDB v2** — HTTP line protocol (`POST /api/v2/write`).
  - **Prometheus Pushgateway** — text exposition format (`POST /metrics/job/{job}`).
  - **Graphite** — plaintext TCP/UDP socket.
- Metrics pushed per run: `execution.duration_seconds`, `execution.resources.add`,
  `execution.resources.change`, `execution.resources.destroy`, `execution.status`.
- All metrics tagged/labelled with `workspace_id`, `workspace_name`, and `command`.
- Configurable: metric prefix, SSL verify toggle, per-backend credentials.
- Per-workspace opt-in toggle in the Overview tab card.
- New REST API endpoints:
  - `GET  /api/workspace/{id}/metrics-config` — read enabled flag.
  - `POST /api/workspace/{id}/metrics-config` — toggle enabled (`{metrics_enabled: bool}`).
- Full configuration panel in **Settings → Metrics Export**.

#### Notification Channels (`app/notification_manager.py`, `app/routes/notification_routes.py`)
- Alert external services when Terraform executions finish.
- Four integration types:
  - **Slack** — Incoming Webhook, optional channel / username / icon emoji override.
  - **Microsoft Teams** — Incoming Webhook, MessageCard JSON, dynamic colour (red = failed,
    green = completed), SSL verify toggle.
  - **Email / SMTP** — STARTTLS or SSL, optional authentication, multiple To addresses.
  - **PagerDuty** — Events API v2, routing key, configurable severity
    (critical / error / warning / info), custom_details payload, optional base URL for
    on-premises deployments.
- Channel scope: **global** (`workspace_ids = ["*"]`) or **workspace-specific**.
- Trigger conditions per channel: `on_success`, `on_failure`, `on_sentinel_fail`.
- Customisable **prefix template** and **body template** with variable substitution:
  `{workspace_name}`, `{workspace_id}`, `{command}`, `{status}`, `{duration}`,
  `{timestamp}`, `{terraform_version}`, `{sentinel_status}`, `{sentinel_summary}`.
- Default prefix: `[TGM] [{workspace_name}]`.
- **Notifications tab** in every workspace detail view:
  - Channel cards showing type icon, scope badge, trigger badges, prefix preview, and
    enable/disable state.
  - Create / Edit modal with per-type dynamic config fields.
  - **Test** button — sends a synthetic notification immediately; shows a timed
    success/error pill inline without reloading the page.
  - Delete workspace-scoped channels; unassign global channels.
  - **Assign a Global Channel** panel to link pre-existing global channels to the
    current workspace.
- Storage: one JSON file per channel under `notification_channels/` in the local
  backend; cloud backends fall back gracefully (channels not persisted, no crash).
- New REST API endpoints:
  - `GET  /api/notification-channels/all`
  - `GET  /api/notification-channels?workspace_id={id}`
  - `POST /api/notification-channels`
  - `GET|PUT|DELETE /api/notification-channels/{id}`
  - `POST /api/notification-channels/{id}/test`
  - `GET  /api/workspace/{id}/notification-channels`
  - `POST /api/workspace/{id}/notification-channels/assign`
  - `POST /api/workspace/{id}/notification-channels/unassign`

### Changed

- **`pyproject.toml`** — version `0.3.0 → 1.0.0`; classifier changed to
  `5 - Production/Stable`.
- **`app/execution_queue.py`** — metrics export and notification dispatch hooked in the
  `finally` block of `_run_execution`; both are wrapped in `try/except Exception: pass`
  so failures never affect execution lifecycle.
- **`app/storage/local_backend.py`** — added `list/get/save/delete_notification_channel()`
  methods following the same JSON-file-per-item pattern as variable groups.
- **`app/app.py`** — registered `notification_bp` blueprint at `/api` prefix.
- **`templates/workspace.html`** — added `Notifications` tab to the tab loop, full tab
  panel, create/edit modal, Alpine state variables and methods, `loadNotifChannels()` call
  in `switchTab()`.
- **`templates/settings.html`** — added Metrics Export configuration panel (backend
  selector radio buttons, per-backend sub-panels, prefix field, SSL toggles).
- **`app/routes/settings_routes.py`** — added POST handling for all metrics export fields.
- **`app/routes/api_routes.py`** — added `GET/POST /api/workspace/{id}/metrics-config`
  endpoints.

---

## [0.5.0] — 2026-04-08

### Added

#### Terraform Version Download & Install (`app/routes/settings_routes.py`, `templates/settings.html`)
- New **Download & Install** button in **Settings → Terraform Versions** next to the
  "Detected versions" heading, opening a full download modal.
- **Download modal** features:
  - **Amber security/privacy notice** — informs users that version metadata is fetched
    from an external HashiCorp URL and that they are responsible for verifying authenticity.
  - **Editable release source URL** — pre-filled with
    `https://releases.hashicorp.com/terraform/`; a **Reset** button restores the default.
    Allows pointing at an internal mirror without code changes.
  - **OS + architecture selectors** — auto-detected from the server platform on open;
    can be overridden manually (e.g. cross-download for a different target OS).
  - **Version list** — fetched live from the release index; already-installed versions
    are marked with a green "installed" badge; a text filter narrows the list.
  - **Multi-select** with "Select all" / "Clear" controls.
  - **Progress bar** — shows per-version download progress during bulk installs.
  - **Reload list** button to refresh available versions using the current source URL.
- **Uninstall** button on every installed-version card; blocked with an informative
  error (HTTP 409) when any workspace's last run used that version.
- New REST API endpoints:
  - `GET  /api/terraform-versions/available?base_url=…` — scrapes the release index,
    returns version list with `installed` flags, `default_os`, `default_arch`, and the
    resolved `base_url`.
  - `POST /api/terraform-versions/install` — body: `{versions, os, arch, base_url}`;
    downloads and extracts selected versions; returns per-version success/skip/error
    results. Uses HTTP 207 when at least one version failed.
  - `DELETE /api/terraform-versions/uninstall/<version>` — removes the version directory
    after validating no workspace's last run depends on it (HTTP 409 if blocked).
- **Security hardening**: `base_url` is validated to be HTTPS-only with a non-empty
  hostname; `version`, `os`, and `arch` parameters are constrained to
  `^[a-z0-9_.]+$` — path traversal is rejected with HTTP 400.

#### Execution Statistics & Metrics — API endpoints (`app/routes/api_routes.py`)
- `GET /api/workspace/{id}/stats` — time-series execution statistics (duration trend +
  resource change counts) for the Overview tab charts.
  - Filters to terminal statuses (`completed`, `failed`) only.
  - Returns up to the last 50 runs, sorted chronologically by timestamp.
  - Falls back to the local filesystem backend when the configured cloud backend returns
    an empty list.
- `GET  /api/metrics-config` — return the global metrics export configuration.
- `POST /api/metrics-config` — persist global metrics configuration to `tfg.conf`.
- `GET  /api/workspace/{id}/metrics-config` — read the per-workspace metrics opt-out flag.
- `POST /api/workspace/{id}/metrics-config` — toggle `metrics_enabled` per workspace.

### Fixed

#### `flake8 --max-line-length=99` now passes with zero errors across `app/`, `tests/`, and `run.py`
- `app/routes/settings_routes.py` L14 E501: Flask import split into a multi-line block.
- `app/routes/settings_routes.py` L383 E221: extra alignment spaces before `=` removed.
- `app/routes/settings_routes.py` L489 E501: long 409 error string split across two lines.
- `tests/`: removed unused imports (`pytest`, `configparser`, `argparse`, `MagicMock`,
  `_HASHICORP_DEFAULT_BASE`); wrapped long assertion lines; added missing blank line before
  nested `def`; moved `contextmanager`/`PropertyMock` imports to module top-level in
  `test_terraform_version_manager.py`.

### Tests

#### `tests/test_metrics_routes.py` — 18 new tests (5 suites)
- `TestWorkspaceStats` (7): unknown workspace → 404; empty series; non-terminal statuses
  filtered; chronological sort; truncation to last 50 runs; local backend fallback;
  series entry field schema.
- `TestGlobalMetricsConfigGet` (2): all expected keys present; correct default values.
- `TestGlobalMetricsConfigPost` (3): returns `ok: true`; keys passed to `config.save()`;
  HTTP 500 when `save()` raises.
- `TestWorkspaceMetricsConfigGet` (3): defaults to `true`; reads stored `false`; returns
  `true` when backend raises.
- `TestWorkspaceMetricsConfigPost` (3): disable, enable, HTTP 500 on backend error.

#### `tests/test_metrics_exporter.py` — 34 new tests (8 suites)
- `TestResourceCounts` (3): zero defaults; field mapping; `update + replace → change`.
- `TestTsNs` (3): valid ISO timestamp; invalid string fallback; missing key fallback.
- `TestSafeTag` (3): spaces, commas, equals characters are escaped.
- `TestSanitizeGraphitePath` (3): dots, spaces/slashes replaced; empty string → `unknown`.
- `TestExportExecutionMetrics` (7): skips when disabled / blank backend / per-workspace
  opt-out; routes to all three backends; exceptions are swallowed silently.
- `TestSendInfluxdb` (5): skips without URL or token; correct write URL built; line-protocol
  body content; `Authorization: Token …` header.
- `TestSendPrometheus` (5): skips without URL; push URL includes job/instance; Prometheus
  exposition format; Basic Auth header; no auth header when username blank.
- `TestSendGraphite` (5): skips without host; TCP socket used by default; metric line
  format; UDP socket used when protocol is `udp`; workspace ID sanitised in path.

### Changed

- **`pyproject.toml`** — version `1.2.0 → 0.5.0`.

---

## [0.4.0] — 2026-04-07

### Fixed

#### Cloud backend encryption key not propagated to storage backends
- **Root cause**: all three cloud backends (`GCSBackend`, `S3Backend`, `AzureBackend`) decrypt
  their stored credentials using an `enc_key` parameter. When `enc_key=""` they fall back to
  `flask.session["tgm_enc_key"]`, which is only available inside a request context.
  Several API endpoints and background-thread operations called `get_backend()` without passing
  the key, causing credential decryption to be silently skipped — `list_executions()` would
  catch the resulting `JSONDecodeError` and return `[]`, making charts and run lists appear empty.
- **Affected endpoints fixed** (`app/routes/api_routes.py`):
  - `GET /api/workspace/{id}/stats` — execution statistics charts were always empty for cloud backends.
  - `GET /api/workspace/{id}/executions` — Runs tab showed no history when using GCP/AWS/Azure.
  - `GET /api/executions/{id}` — execution detail failed to load from cloud storage.
  - `GET /api/executions/{id}/logs` — logs could not be fetched from cloud storage.
  - `GET /api/executions/{id}/plan` — plan JSON could not be fetched from cloud storage.
- **Affected background operations fixed** (`app/execution_queue.py`):
  - `ExecutionQueue.get(execution_id)` — new `enc_key=""` parameter passed to `get_backend()`.
  - `ExecutionQueue.list_for_workspace(workspace_id)` — new `enc_key=""` parameter passed to `get_backend()`.
  - `set_execution_lock` / `clear_execution_lock` in `_run_execution` — worker threads have no Flask
    request context; both calls now use `execution.enc_key` (carried from the submitting request)
    instead of relying on `flask.session`.

#### Execution statistics charts empty on Overview tab with cloud backend configured
- Direct consequence of the `enc_key` bug above. Charts for Duration and Total Managed Resources
  now populate correctly for GCP, AWS, and Azure backends after login.

#### Local filesystem fallback added to `workspace_stats`
- When the configured cloud backend returns an empty execution list (e.g. credentials not yet set
  up, network issue, or data pre-dates the cloud migration), `GET /api/workspace/{id}/stats` now
  falls back to the local filesystem backend before returning `{"series": []}`.
  Historical data is always visible regardless of cloud backend availability.

### Changed

#### `resource_counts` and `state_resource_count` now stored for `plan` runs
- `_build_metadata()` in `local_backend.py` (and equivalently in the cloud backends) already
  computed `resource_counts` from `plan.json` when available, but `state_resource_count` was
  never set for plan-type runs — only apply runs populated it via `state_pull()`.
- The `metadata.json` for every `plan` run now includes:
  - `resource_counts` — `{create, update, delete, replace, no-op}` breakdown parsed from `plan.json`.
  - `state_resource_count` — projected total of managed resources after the plan is applied
    (`no-op + create + update + replace`).
- This ensures the **Total Managed Resources** chart has data points for workspaces that have
  only ever been `plan`-ned (never applied), and makes resource counts consistent across both
  chart series.

---

## [0.3.0] — 2026-03-31

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

[1.2.0]: https://github.com/eandresr/terraform-graphical-manager/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/eandresr/terraform-graphical-manager/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/eandresr/terraform-graphical-manager/compare/v0.4.0...v1.0.0
[0.4.0]: https://github.com/eandresr/terraform-graphical-manager/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/eandresr/terraform-graphical-manager/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/eandresr/terraform-graphical-manager/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/eandresr/terraform-graphical-manager/releases/tag/v0.1.0
