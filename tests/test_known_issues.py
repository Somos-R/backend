"""Known security gaps, written as the *desired* behavior.

Mark a test `@known_issue("<task>", "<why>")` while the hole exists. It is
`xfail(strict=True)`: when the fix lands the test passes, strict mode turns that
into a failure, and the fixer must delete the marker (or promote the test to a
regular suite). Task IDs refer to the hardening plan.

There are no open known issues right now.
"""
import pytest


def known_issue(task: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"[{task}] {why}")
