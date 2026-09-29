"""The impact-check hook that reminds to rebuild the dev image after dependency changes."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "impact_check.py"


def run_hook(payload: dict) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0
    return result.stdout


def edit(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path}}


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


class TestDependencyReminder:
    def test_editing_pyproject_reminds_to_rebuild(self):
        out = run_hook(edit("C:/repo/backend/pyproject.toml"))
        assert "docker compose up -d --build --no-deps app" in out
        assert "docker compose restart app" in out and "pip-audit" in out

    def test_editing_the_lock_file_does_too(self):
        assert "reconstruye la imagen" in run_hook(edit("C:\\repo\\backend\\poetry.lock")).lower()

    def test_writing_the_file_does_too(self):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "/repo/pyproject.toml"}}
        assert "--no-deps app" in run_hook(payload)

    def test_the_reminder_mentions_telling_the_team(self):
        assert "git pull" in run_hook(edit("/repo/pyproject.toml"))

    def test_running_poetry_add_triggers_it_via_bash(self):
        for command in ("poetry add sentry-sdk", "poetry remove x", "poetry update starlette",
                        "poetry lock", "cd backend && poetry add --group dev pytest-cov"):
            assert "--no-deps app" in run_hook(bash(command)), command

    def test_unrelated_bash_commands_are_silent(self):
        for command in ("poetry run pytest", "poetry install --no-root", "git status",
                        "docker compose up -d", "echo poetry", "poetry show"):
            assert run_hook(bash(command)) == "", command


class TestExistingRulesStillWork:
    def test_a_model_change_still_asks_about_migrations(self):
        assert "migración" in run_hook(edit("/repo/app/domains/users/models.py")).lower()

    def test_unrelated_files_are_silent(self):
        assert run_hook(edit("/repo/app/domains/weighings/service.py")) == ""

    def test_garbage_input_never_breaks_the_hook(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], input="not json",
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0 and result.stdout == ""
