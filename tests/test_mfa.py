"""The second-factor primitives: TOTP against the RFC 6238 vectors, encryption, recovery codes."""
import base64

import pytest
from pydantic import ValidationError

from app.core import mfa
from app.core.config import Settings

# RFC 6238 appendix B: SHA-1, the ASCII secret "12345678901234567890", 8-digit codes. We use 6
# digits, which are the last six of the RFC's eight.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
RFC_VECTORS = [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
               (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")]


class TestTotp:
    @pytest.mark.parametrize("moment,eight_digits", RFC_VECTORS)
    def test_matches_the_rfc_test_vectors(self, moment, eight_digits):
        assert mfa._code_at(RFC_SECRET, mfa.current_step(moment)) == eight_digits[-6:]

    def test_a_correct_code_is_accepted_and_returns_its_step(self):
        step = mfa.current_step(1234567890)
        assert mfa.verify_totp(RFC_SECRET, "005924", now=1234567890) == step

    def test_a_wrong_code_is_refused(self):
        assert mfa.verify_totp(RFC_SECRET, "000000", now=1234567890) is None

    @pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "00 5924x", None])
    def test_malformed_codes_are_refused(self, bad):
        assert mfa.verify_totp(RFC_SECRET, bad, now=1234567890) is None

    def test_spaces_in_the_code_are_tolerated(self):
        assert mfa.verify_totp(RFC_SECRET, "005 924", now=1234567890) is not None

    def test_the_neighbouring_steps_are_accepted_for_clock_drift(self):
        code = mfa._code_at(RFC_SECRET, mfa.current_step(1234567890))
        assert mfa.verify_totp(RFC_SECRET, code, now=1234567890 - 30) is not None
        assert mfa.verify_totp(RFC_SECRET, code, now=1234567890 + 30) is not None

    def test_two_steps_away_is_too_far(self):
        code = mfa._code_at(RFC_SECRET, mfa.current_step(1234567890))
        assert mfa.verify_totp(RFC_SECRET, code, now=1234567890 + 90) is None
        assert mfa.verify_totp(RFC_SECRET, code, now=1234567890 - 90) is None

    def test_a_code_cannot_be_replayed(self):
        step = mfa.verify_totp(RFC_SECRET, "005924", now=1234567890)
        assert mfa.verify_totp(RFC_SECRET, "005924", last_step=step, now=1234567890) is None

    def test_an_older_code_is_refused_once_a_newer_one_was_used(self):
        newer = mfa.current_step(1234567890)
        old_code = mfa._code_at(RFC_SECRET, newer - 1)
        assert mfa.verify_totp(RFC_SECRET, old_code, last_step=newer, now=1234567890) is None

    def test_generated_secrets_are_random_base32_of_160_bits(self):
        a, b = mfa.generate_secret(), mfa.generate_secret()
        assert a != b and len(base64.b32decode(a + "=" * (-len(a) % 8))) == 20

    def test_the_provisioning_uri_is_what_authenticator_apps_read(self):
        uri = mfa.provisioning_uri("ABCDEF", "ana@somosr.co")
        assert uri.startswith("otpauth://totp/Somos%20R%3Aana%40somosr.co?")
        assert "secret=ABCDEF" in uri and "issuer=Somos%20R" in uri and "digits=6" in uri


class TestSecretEncryption:
    def test_round_trip(self):
        secret = mfa.generate_secret()
        assert mfa.decrypt_secret(mfa.encrypt_secret(secret)) == secret

    def test_the_stored_value_does_not_contain_the_secret(self):
        secret = mfa.generate_secret()
        assert secret not in mfa.encrypt_secret(secret)

    def test_a_tampered_or_foreign_value_decrypts_to_none(self):
        assert mfa.decrypt_secret("not-a-token") is None
        token = mfa.encrypt_secret("ABC")
        assert mfa.decrypt_secret(token[:-4] + "AAAA") is None


class TestRecoveryCodes:
    def test_ten_distinct_readable_codes(self):
        codes = mfa.generate_recovery_codes()
        assert len(codes) == len(set(codes)) == 10
        assert all(len(c) == 11 and c[5] == "-" for c in codes)

    def test_hashing_ignores_case_dashes_and_spaces(self):
        assert mfa.hash_recovery_code("ABCDE-fghjk") == mfa.hash_recovery_code(" abcde fghjk ")

    def test_different_codes_hash_differently(self):
        a, b = mfa.generate_recovery_codes(2)
        assert mfa.hash_recovery_code(a) != mfa.hash_recovery_code(b)


class TestSettings:
    def _settings(self, **kw):
        return Settings(_env_file=None, database_url="postgresql://x", secret_key="k" * 48, **kw)

    def test_a_key_is_required_outside_dev(self):
        with pytest.raises(ValidationError, match="MFA_ENCRYPTION_KEY"):
            self._settings(app_env="prod", mfa_encryption_key=None)

    def test_dev_works_without_one(self):
        assert self._settings(app_env="dev", mfa_encryption_key=None).app_env == "dev"

    def test_a_malformed_key_is_refused(self):
        with pytest.raises(ValidationError, match="Fernet"):
            self._settings(app_env="dev", mfa_encryption_key="not-a-key")

    def test_a_malformed_network_is_refused_at_startup(self):
        with pytest.raises(ValidationError):
            self._settings(admin_allowed_cidrs="not-a-cidr")

    def test_networks_parse(self):
        nets = self._settings(admin_allowed_cidrs=" 203.0.113.0/24 , 198.51.100.7/32 ,").admin_networks
        assert [str(n) for n in nets] == ["203.0.113.0/24", "198.51.100.7/32"]
