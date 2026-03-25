# Security Policy

## Overview

Terraform Graphical Manager is a **locally-hosted** web application designed to run on a developer's own machine or within a trusted private network. It does not expose any public-facing services by default and does not transmit data to external servers.

This document describes the security model of the application, known limitations, responsible disclosure process, and hardening recommendations.

---

## Supported Versions

| Version | Supported |
|---|---|
| Latest (`main`) | ✅ Yes |
| Older releases | ❌ No — please update to the latest version |

---

## Security Model

### Threat model

The application is designed to be run by a single trusted user (or a small team on a private network). It is **not** designed to be exposed to the public internet.

| Threat | Mitigation |
|---|---|
| Command injection via workspace paths | Workspace paths are validated to be within `repos_root`; all subprocess calls use argument lists (`shell=False`) |
| Credential leakage between concurrent runs | Each execution receives an isolated environment dict — `os.environ` is never passed directly to subprocesses |
| Sensitive value exposure in state viewer | Sensitive state attributes and Terraform outputs are masked as `***sensitive***` in the UI |
| Credentials written to disk | Credentials entered in the modal are never persisted; they exist only in memory for the duration of the execution |
| Path traversal | `WorkspaceScanner` resolves all paths and ensures they remain within `repos_root` |
| Cross-site scripting (XSS) | Jinja2 auto-escaping is enabled by default for all HTML templates |

### What this application does NOT provide

- Authentication or authorization — anyone with network access to the port can use the application
- TLS / HTTPS — the server runs plain HTTP by default
- Rate limiting or brute-force protection
- Multi-tenancy isolation

**If you expose this application to a network beyond your own machine, you are responsible for adding appropriate access controls** (e.g. a reverse proxy with authentication, firewall rules, VPN).

---

## Subprocess Security

All Terraform and Git commands are executed via Python's `subprocess` module with the following safety measures:

- **`shell=False` always** — arguments are passed as lists, never as shell strings. This prevents shell injection.
- **Explicit environment dict** — each execution constructs a clean environment containing only the credentials the user provides. The host's `os.environ` is never passed to Terraform processes.
- **Working directory pinned** — every subprocess sets `cwd` to the validated workspace path.
- **Timeout enforcement** — long-running operations (e.g. `git pull`) have a timeout to prevent indefinite blocking.

---

## Credential Handling

Credentials (AWS, GCP, Azure) are:

1. Accepted from the user via the execution modal (browser form submission over localhost)
2. Stored **only in memory** as a Python dict for the duration of the execution
3. Passed to the Terraform subprocess via the isolated environment dict
4. **Never written to disk**, never logged, never included in execution metadata

> Execution logs (`plan.log`, `apply.log`) are written by Terraform itself. If Terraform happens to print a credential value in its output (unusual but possible in certain error conditions), that value would appear in the stored log. Review log retention policies accordingly.

---

## Storage Security

### Local backend

- Execution metadata, logs, and plan artefacts are written to `./TERRAFORM_GRAPHICAL_BACKEND/` (or the path set in `TERRAFORM_GRAPHICAL_BACKEND_LOCAL_PATH`).
- This directory may contain **Terraform plan binaries** (`tfplan.binary`) which can encode sensitive resource attribute values. Protect this directory with appropriate filesystem permissions.
- Binary plan files can be inspected with `terraform show tfplan.binary` by anyone with read access. Treat them as sensitive.

### Cloud backends (S3, GCS, Azure)

- Credentials for cloud backends are read from environment variables and never stored by the application itself.
- Ensure the cloud bucket/container has appropriate access controls and is not publicly readable.
- Enable server-side encryption on the bucket/container for data at rest.

---

## Recommended Hardening

If you run this application on a shared machine or accessible network:

1. **Bind to localhost only** — the default `0.0.0.0` binding should be changed to `127.0.0.1` in `run.py` if multi-user access is not desired:

   ```python
   socketio.run(app, host="127.0.0.1", port=5005, ...)
   ```

2. **Use a reverse proxy** (nginx, Caddy) with HTTP Basic Auth or OAuth2 if exposing to a team.

3. **Enable TLS** via the reverse proxy — never run plain HTTP over an untrusted network.

4. **Restrict filesystem permissions** on the `TERRAFORM_GRAPHICAL_BACKEND/` directory:

   ```bash
   chmod 700 TERRAFORM_GRAPHICAL_BACKEND/
   ```

5. **Rotate credentials regularly** — since credentials are entered per-run, there is no long-lived secret stored in the app itself.

6. **Keep dependencies updated** — run `pip install --upgrade -r requirements.txt` periodically and review the changelog for Flask, Flask-SocketIO, and boto3/google-cloud-storage/azure-storage-blob.

---

## Reporting a Vulnerability

**Please do not report security vulnerabilities via public GitHub issues.**

To report a security vulnerability responsibly:

1. Email the maintainer directly via [eduardoandres.net](https://eduardoandres.net) (use the contact form).
2. Include in your report:
   - A description of the vulnerability and its potential impact
   - Steps to reproduce
   - Any proof-of-concept code or screenshots
   - Your suggested fix (optional but appreciated)
3. You will receive an acknowledgement within **72 hours**.
4. A fix will be prioritized and a new release published. You will be credited in the release notes unless you request anonymity.

Please allow reasonable time for a fix before any public disclosure.

---

## Dependency Vulnerabilities

You can audit the project's Python dependencies for known CVEs using:

```bash
pip install pip-audit
pip-audit
```

Or via GitHub's Dependabot if you have forked the repository.

---

## Acknowledgements

Security improvements are always welcome. If you have hardening suggestions that do not constitute a vulnerability, feel free to open a GitHub issue or Pull Request.
