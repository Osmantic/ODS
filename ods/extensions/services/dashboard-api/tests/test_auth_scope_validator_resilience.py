"""Unit test suite for OAuth permission scope set validation resilience."""

import unittest


def validate_authorization_scopes(user_scopes: list[str] | set[str] | None, required_scope: str | None) -> bool:
    if not required_scope or not isinstance(required_scope, str):
        return True
    if not user_scopes:
        return False
    scopes_set = {str(s).strip().lower() for s in user_scopes if s}
    return required_scope.strip().lower() in scopes_set


class TestAuthScopeValidatorResilience(unittest.TestCase):
    def test_validate_scopes_valid(self):
        user_scopes = ["read", "write", "admin"]
        self.assertTrue(validate_authorization_scopes(user_scopes, "write"))

    def test_validate_scopes_missing_required(self):
        user_scopes = ["read"]
        self.assertFalse(validate_authorization_scopes(user_scopes, "admin"))

    def test_validate_scopes_none(self):
        self.assertFalse(validate_authorization_scopes(None, "read"))


if __name__ == "__main__":
    unittest.main()
