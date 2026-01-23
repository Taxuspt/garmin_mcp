# GitHub Actions Workflows

This repository uses GitHub Actions for continuous integration and security checks. Below is an overview of each workflow.

## Workflows

### 1. CI (`ci.yml`)

**Triggers:**
- Pull requests to `main` or `master`
- Pushes to `main` or `master`

**What it does:**
- Tests the codebase across Python versions 3.10, 3.11, 3.12, and 3.13
- Runs all integration and unit tests
- Uses uv for fast dependency management
- Provides a test summary

**Matrix:** Tests run in parallel across 4 Python versions for comprehensive compatibility checks.

### 2. PR Validation (`pr-validation.yml`)

**Triggers:**
- Pull request opened, synchronized, or reopened

**What it does:**
- Comprehensive validation on Python 3.13
- Runs all tests with strict markers and fail-fast behavior (max 5 failures)
- Validates package installation
- Checks `pyproject.toml` syntax
- Provides PR metadata information

**Jobs:**
1. `validate` - Runs complete test suite
2. `test-installation` - Verifies the package installs correctly
3. `pr-info` - Displays PR metadata for debugging

### 3. Security Checks (`security.yml`)

**Triggers:**
- Pull requests to `main` or `master`
- Weekly schedule (Mondays at 9am UTC)
- Manual trigger via workflow_dispatch

**What it does:**
- Checks dependency vulnerabilities
- Verifies lock file (`uv.lock`) is in sync with `pyproject.toml`
- Validates Python syntax across all source files
- Checks package import structure

**Jobs:**
1. `dependency-check` - Security and dependency validation
2. `code-quality` - Code syntax and import checks

## Running Tests Locally

To run the same tests that CI runs:

```bash
# Install dependencies
uv sync

# Run integration and unit tests
uv run pytest tests/integration tests/unit -v --tb=short

# Check lock file status
uv lock --check
```

## Skipped Tests

The workflows skip end-to-end (e2e) tests because they require valid Garmin credentials. E2E tests are located in `tests/e2e/` and should be run manually with proper authentication.

## Badge Status

Add these badges to your README.md:

```markdown
![CI](https://github.com/YOUR_USERNAME/garmin_mcp/workflows/CI/badge.svg)
![PR Validation](https://github.com/YOUR_USERNAME/garmin_mcp/workflows/PR%20Validation/badge.svg)
![Security Checks](https://github.com/YOUR_USERNAME/garmin_mcp/workflows/Security%20Checks/badge.svg)
```

## Troubleshooting

### Lock file out of sync

If the security check fails with "Lock file is out of sync":

```bash
uv lock
git add uv.lock
git commit -m "Update uv.lock"
```

### Test failures

Check the Actions tab for detailed test output. Tests run with `-v --tb=short` for verbose output with short tracebacks.
