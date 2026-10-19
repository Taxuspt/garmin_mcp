"""Exercise packaging safeguards without starting a server or contacting Garmin."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zipfile import ZipFile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("filename", ["garmin_email.txt", "garmin_password.txt"])
def test_documented_secret_files_are_gitignored(filename):
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", f"secrets/{filename}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_documented_secrets_directory_is_excluded_from_docker_context():
    patterns = (REPO_ROOT / ".dockerignore").read_text().splitlines()
    assert "secrets/" in patterns


def _executable(path, body):
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)


def test_dxt_build_uses_project_runtime_from_another_directory(tmp_path):
    project = tmp_path / "checkout"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (project / "dxt").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "build_dxt.sh", scripts)
    (scripts / "generate_dxt_tools.py").write_text("# generator supplied by the project\n")
    (project / "dxt" / "manifest.json").write_text('{"tools": []}\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "python3", "raise SystemExit('system python must not run')\n")
    _executable(
        bin_dir / "uv",
        "import json, pathlib, sys\n"
        f"project = pathlib.Path({str(project)!r})\n"
        "assert sys.argv[1:] == ['run', '--project', str(project), 'python', "
        "str(project / 'scripts' / 'generate_dxt_tools.py')]\n"
        "(project / 'dxt' / 'manifest.json').write_text(json.dumps({'tools': [{'name': 'generated'}]}))\n",
    )
    result = subprocess.run(
        ["bash", str(scripts / "build_dxt.sh")],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with ZipFile(project / "garmin-mcp.dxt") as bundle:
        assert json.loads(bundle.read("manifest.json")) == {"tools": [{"name": "generated"}]}


def _installation_step():
    workflow = (REPO_ROOT / ".github" / "workflows" / "pr-validation.yml").read_text()
    step = workflow.split("    - name: Test package installation\n", 1)[1]
    step = step.split("\n  pr-info:", 1)[0]
    lines = step.split("      run: |\n", 1)[1].splitlines()
    commands = []
    for line in lines:
        if line and not line.startswith("        "):
            break
        commands.append(line[8:])
    return step, "\n".join(commands)


@pytest.mark.parametrize("failure", ["", "help", "import"])
def test_installation_smoke_fails_on_errors_without_starting_server(tmp_path, failure):
    step, commands = _installation_step()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.jsonl"
    _executable(
        bin_dir / "uv",
        "import json, os, pathlib, subprocess, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "with calls.open('a') as output: output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "if args in (['python', 'install', '3.13'], ['sync']): raise SystemExit(0)\n"
        "if args == ['run', 'garmin-mcp-auth', '--help']:\n"
        "    raise SystemExit(7 if os.environ['SMOKE_FAILURE'] == 'help' else 0)\n"
        "if args[:3] == ['run', 'python', '-c']:\n"
        "    if os.environ['SMOKE_FAILURE'] == 'import': raise SystemExit(8)\n"
        "    raise SystemExit(subprocess.run([sys.executable, *args[2:]]).returncode)\n"
        "raise SystemExit('unexpected invocation, including server startup: ' + repr(args))\n",
    )
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", commands],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "SMOKE_FAILURE": failure,
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    assert ["run", "garmin-mcp", "--help"] not in invocations
    assert re.search(r"timeout-minutes: [1-9]\d*", step)
    assert (result.returncode != 0) if failure else (result.returncode == 0), result.stderr
