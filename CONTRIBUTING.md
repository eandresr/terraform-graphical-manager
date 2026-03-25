# Contributing to Terraform Graphical Manager

Thank you for considering contributing to **Terraform Graphical Manager**!  
All contributions are welcome — bug reports, feature requests, documentation improvements, and code.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How to Report a Bug](#how-to-report-a-bug)
- [How to Request a Feature](#how-to-request-a-feature)
- [Development Workflow](#development-workflow)
- [Coding Style](#coding-style)
- [Commit Messages](#commit-messages)
- [Pull Request Guidelines](#pull-request-guidelines)
- [Running the App Locally](#running-the-app-locally)

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).  
By participating you agree to abide by its terms.

---

## How to Report a Bug

1. **Search existing issues** first — the bug may already be reported.
2. If not, [open a new issue](../../issues/new?template=bug_report.md) and include:
   - A clear title and description
   - Steps to reproduce
   - Expected vs actual behaviour
   - Your OS, Python version, and Terraform version
   - Relevant log output or screenshots

---

## How to Request a Feature

1. [Open a new issue](../../issues/new?template=feature_request.md) with the label `enhancement`.
2. Describe **what** you want and **why** it would benefit the project.
3. If possible, sketch a rough design or API shape.

A maintainer will discuss feasibility and priority before any implementation begins.

---

## Development Workflow

### 1. Fork the repository

Click **Fork** on the GitHub page to create your own copy.

```bash
git clone https://github.com/<your-username>/terraform-graphical-manager.git
cd terraform-graphical-manager
```

### 2. Set up the upstream remote

```bash
git remote add upstream https://github.com/eandresr/terraform-graphical-manager.git
```

### 3. Create a feature branch

Branch from `main`. Use a short, descriptive name prefixed with the type:

```
feat/   — new feature
fix/    — bug fix
docs/   — documentation only
refactor/ — code refactoring without behaviour change
chore/  — tooling, dependencies, CI
```

Examples:

```bash
git checkout main
git pull upstream main
git checkout -b feat/per-workspace-env-vars
```

> Always branch from an up-to-date `main`, never work directly on it.

### 4. Make your changes

- Write clean, focused commits (one logical change per commit).
- Follow the [coding style](#coding-style) below.
- Add or update tests if your change affects logic.
- Update documentation (README, docstrings) if needed.

### 5. Keep your branch up to date

```bash
git fetch upstream
git rebase upstream/main
```

### 6. Push and open a Pull Request

```bash
git push origin feat/per-workspace-env-vars
```

Then open a Pull Request on GitHub against the `main` branch of the upstream repository.  
Use the [Pull Request template](.github/pull_request_template.md) and **always reference the Issue** your PR resolves:

```
Closes #42
```

---

## Coding Style

All Python code must conform to **[PEP 8](https://peps.python.org/pep-0008/)**.

### Key rules

| Rule | Detail |
|---|---|
| Indentation | 4 spaces — never tabs |
| Line length | Maximum **99** characters |
| Imports | Stdlib → third-party → local, separated by blank lines |
| String quotes | Double quotes `"` for strings, single `'` only when avoiding escaping |
| Functions / variables | `snake_case` |
| Classes | `PascalCase` |
| Constants | `UPPER_SNAKE_CASE` |
| Type hints | Required for all public function signatures |
| Docstrings | Required for all public modules, classes, and functions (Google style) |

### Enforcement

Dev tools are declared as optional dependencies in `pyproject.toml`. Install them once:

```bash
pip install ".[dev]"
```

Then lint with:

```bash
# pycodestyle
pycodestyle --max-line-length=99 app/

# or flake8 (reads config from pyproject.toml automatically)
flake8 app/
```

Configuration for both tools is already set in `pyproject.toml` (`max-line-length = 99`).

### Templates & frontend

- HTML templates follow the existing Tailwind + Alpine.js patterns already in the project.
- JavaScript uses `camelCase` for functions and variables.
- Keep Alpine.js components self-contained in the template where they are used.

---

## Commit Messages

Follow the **[Conventional Commits](https://www.conventionalcommits.org/)** specification:

```
<type>(<scope>): <short summary>

[optional body — explain WHY, not what]

[optional footer — issue refs, breaking changes]
```

### Types

| Type | When to use |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code restructuring without behaviour change |
| `style` | Formatting (whitespace, semicolons) — no logic change |
| `test` | Adding or fixing tests |
| `chore` | Build, CI, dependencies |

### Examples

```
feat(version-manager): add per-run terraform binary override

Allows the execution modal to override the workspace-pinned terraform
version for a single run without persisting the change.

Closes #37
```

```
fix(storage): handle missing metadata.json gracefully in list_all_executions

Raises a warning instead of crashing when a run directory exists but
has no metadata file (e.g. partially written by a failed execution).

Closes #51
```

---

## Pull Request Guidelines

- **One PR per issue / feature** — keep PRs focused and small.
- **Reference the Issue** using `Closes #N` or `Fixes #N` in the PR description.
- **Fill in the PR template** completely.
- Ensure the app starts without errors (`tgm start` or `python run.py`) before submitting.
- Ensure `flake8 --max-line-length=99 app/` passes with no new violations.
- Ensure `pytest tests/` passes with no failures.
- The CI workflow (`.github/workflows/ci.yml`) runs automatically on every PR:
  it lints with flake8, runs the full pytest suite, and does a smoke-test of the
  running Flask app. **All checks must pass before a PR can be merged.**
- Be responsive to review feedback — PRs inactive for 30 days may be closed.

---

## Running the App Locally

```bash
# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Install the package + dev tools
pip install ".[dev]" pytest pytest-flask

# Configure
cp config/tfg.conf.example config/tfg.conf
# Edit config/tfg.conf — set repos_root to a directory containing .tf files

# Run
tgm start
# or: python run.py
# Open http://localhost:5005
```

---

## Questions?

Feel free to open a [Discussion](../../discussions) or reach out via the issue tracker.  
The project is mainly maintained by [Eduardo Andres Rabano](https://eduardoandres.net).
