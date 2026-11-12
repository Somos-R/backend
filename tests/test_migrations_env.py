"""The models and the migrations describe the same schema.

`alembic check` runs autogenerate against the test database (migrated to head by the `engine` fixture) and fails
if it would emit any operation: a table whose model migrations/env.py does not import shows up as one to drop, and
an index or column that exists on only one side shows up as one to add or remove. It runs in its own process
because this one has already imported every model through the app, which would hide a missing import.
"""
import os
import subprocess
import sys

from tests.conftest import ROOT


def test_autogenerate_finds_no_difference_between_models_and_migrations(engine):
    result = subprocess.run([sys.executable, "-m", "alembic", "check"], cwd=ROOT, env=os.environ.copy(),
                            capture_output=True, text=True, timeout=300)

    assert result.returncode == 0, result.stderr[-3000:]
