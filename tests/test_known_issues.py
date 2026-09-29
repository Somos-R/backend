"""Known security gaps, written as the *desired* behavior.

Each test is `xfail(strict=True)`: it fails today because the hole exists. When
the fix lands the test starts passing, strict mode turns that into a failure,
and the fixer must delete the marker (or promote the test to a regular suite). Task IDs refer to the hardening plan.
"""
import pytest


def known_issue(task: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"[{task}] {why}")


@known_issue("2.6", "login has no rate limiting or lockout")
def test_login_is_rate_limited(client, citizen):
    codes = {
        client.post("/auth/login", json={"email": citizen.email, "password": "mala"}).status_code
        for _ in range(30)
    }
    assert 429 in codes
