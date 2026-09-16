import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
from pixel_provider.config import ConfigError, normalize_config, default_config, public_config


_VALID_LOCAL = {
    "schemaVersion": 1, "revision": 0, "enabled": False,
    "providers": [{
        "id": "tower1-main", "label": "Tower1 Main", "kind": "local",
        "baseUrl": "http://127.0.0.1:8080/v1", "model": "qwen3-27b-q4",
        "contextTokens": 8192, "maxOutputTokens": 4096,
        "supportsTools": True, "supportsVision": False, "reasoning": False,
        "credentialRef": None, "enabled": True,
    }],
    "roles": {"leader": "tower1-main", "backups": [], "advisor": None, "handoff": None},
    "policy": {"allowCloud": False, "maxAttempts": 3, "deadlineSeconds": 120},
}

_VALID_CLOUD = {
    "id": "cloud-gpu", "label": "Cloud GPU", "kind": "cloud",
    "baseUrl": "https://api.example.com:443/v1", "model": "gpt-4",
    "contextTokens": 128000, "maxOutputTokens": 4096,
    "supportsTools": True, "supportsVision": True, "reasoning": True,
    "credentialRef": "cloud-key-a", "enabled": True,
}


class TestNormalize(unittest.TestCase):
    def test_default_valid(self):
        c = normalize_config(default_config())
        self.assertEqual(c["enabled"], False)
        self.assertEqual(c["providers"], [])

    def test_local_valid(self):
        c = normalize_config(_VALID_LOCAL)
        self.assertEqual(c["providers"][0]["id"], "tower1-main")

    def test_cloud_inactive_allowed(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"].append(copy.deepcopy(_VALID_CLOUD))
        c = normalize_config(cfg)
        self.assertEqual(len(c["providers"]), 2)

    def test_cloud_active_denied(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"].append(copy.deepcopy(_VALID_CLOUD))
        cfg["enabled"] = True
        cfg["roles"]["leader"] = "cloud-gpu"
        with self.assertRaises(ConfigError) as e:
            normalize_config(cfg)
        self.assertEqual(e.exception.code, "cloud_not_authorized")

    def test_unknown_root_key(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["extra"] = 1
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_bool_as_int_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["supportsTools"] = 1
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_input_not_mutated(self):
        original = copy.deepcopy(_VALID_LOCAL)
        normalize_config(_VALID_LOCAL)
        self.assertEqual(_VALID_LOCAL, original)

    def test_default_independent(self):
        a, b = default_config(), default_config()
        a["revision"] = 99
        self.assertEqual(b["revision"], 0)

    def test_public_omits_credential(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["credentialRef"] = "secret-ref"
        pub = public_config(cfg)
        self.assertNotIn("credentialRef", pub["providers"][0])
        self.assertTrue(pub["providers"][0]["hasCredential"])

    def test_duplicate_provider_id(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"].append(copy.deepcopy(cfg["providers"][0]))
        with self.assertRaises(ConfigError) as e:
            normalize_config(cfg)
        self.assertEqual(e.exception.code, "duplicate_id")

    def test_backup_duplicate(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["roles"]["backups"] = ["tower1-main", "tower1-main"]
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_leader_in_backups(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        p2 = copy.deepcopy(cfg["providers"][0])
        p2["id"] = "tower1-backup"
        cfg["providers"].append(p2)
        cfg["roles"]["backups"] = ["tower1-main"]
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_two_profiles_same_roles(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        p2 = copy.deepcopy(cfg["providers"][0])
        p2["id"] = "tower2-main"
        cfg["providers"].append(p2)
        cfg["enabled"] = True
        cfg["roles"]["leader"] = "tower2-main"
        cfg["roles"]["advisor"] = "tower1-main"
        cfg["roles"]["handoff"] = "tower1-main"
        c = normalize_config(cfg)
        self.assertEqual(c["roles"]["advisor"], c["roles"]["handoff"])

    def test_backups_order_preserved(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        p2 = copy.deepcopy(cfg["providers"][0])
        p2["id"] = "b"
        p3 = copy.deepcopy(cfg["providers"][0])
        p3["id"] = "a"
        cfg["providers"].extend([p2, p3])
        cfg["enabled"] = True
        cfg["roles"]["backups"] = ["b", "a"]
        c = normalize_config(cfg)
        self.assertEqual(c["roles"]["backups"], ["b", "a"])

    def test_disabled_leader_required(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["enabled"] = True
        cfg["roles"]["leader"] = None
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_bad_port(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1:0/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_credentials_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://user:pass@127.0.0.1:8080/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_query_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1:8080/v1?q=1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_empty_query_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1:8080/v1?"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_fragment_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1:8080/v1#x"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_empty_fragment_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1:8080/v1#"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_empty_userinfo_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://@127.0.0.1:8080/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_http_non_loopback_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://example.com:80/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_http_127_evil_dot_com_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.evil.com:8080/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_http_localhost_allowed(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://localhost:8080/v1"
        c = normalize_config(cfg)
        self.assertEqual(c["providers"][0]["baseUrl"], "http://localhost:8080/v1")

    def test_http_loopback_ipv6_allowed(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://[::1]:8080/v1"
        c = normalize_config(cfg)
        self.assertIn("[::1]", c["providers"][0]["baseUrl"])

    def test_https_metadata_ip(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0] = copy.deepcopy(_VALID_CLOUD)
        cfg["roles"]["leader"] = "cloud-gpu"
        cfg["providers"][0]["baseUrl"] = "https://169.254.169.254:80/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_https_unspecified_ip_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0] = copy.deepcopy(_VALID_CLOUD)
        cfg["roles"]["leader"] = "cloud-gpu"
        cfg["providers"][0]["baseUrl"] = "https://0.0.0.0:443/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_https_multicast_ip_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0] = copy.deepcopy(_VALID_CLOUD)
        cfg["roles"]["leader"] = "cloud-gpu"
        cfg["providers"][0]["baseUrl"] = "https://224.0.0.1:443/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_credential_path_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["credentialRef"] = "/etc/secret.key"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_max_output_exceeds_context(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["maxOutputTokens"] = cfg["providers"][0]["contextTokens"] + 1
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_unknown_nested_policy(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["policy"]["extra"] = True
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_unknown_nested_provider(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["extra"] = True
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_unknown_nested_roles(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["roles"]["extra"] = True
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_control_char_in_url(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1\x00:8080/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_backslash_in_url(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1:8080/v\\1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_no_hostname(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://:8080/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_unknown_role_ref(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["roles"]["leader"] = "nonexistent"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_disabled_provider_reference(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["enabled"] = True
        cfg["providers"][0]["enabled"] = False
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    # ---- Defect-specific tests ----

    def test_id_fullmatch_no_trim(self):
        """ID with leading/trailing spaces must be rejected, not trimmed."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["id"] = " tower1-main "
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_id_uppercase_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["id"] = "TOWER1-MAIN"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_id_leading_digit_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["id"] = "1abc"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_no_port_https_default(self):
        """https://api.example.com/v1 must be valid (port 443 implied)."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0] = copy.deepcopy(_VALID_CLOUD)
        cfg["roles"]["leader"] = "cloud-gpu"
        cfg["providers"][0]["baseUrl"] = "https://api.example.com/v1"
        c = normalize_config(cfg)
        self.assertEqual(c["providers"][0]["baseUrl"], "https://api.example.com/v1")

    def test_url_no_port_http_default(self):
        """http://localhost/v1 must be valid (port 80 implied)."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://localhost/v1"
        c = normalize_config(cfg)
        self.assertEqual(c["providers"][0]["baseUrl"], "http://localhost/v1")

    def test_url_path_prefix_allowed(self):
        """A provider path prefix must be preserved, not silently discarded."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1:8080/provider/v1/"
        c = normalize_config(cfg)
        self.assertEqual(c["providers"][0]["baseUrl"], "http://127.0.0.1:8080/provider/v1")

    def test_url_ipv6_brackets_preserved(self):
        """Canonical IPv6 brackets must survive normalization."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://[::1]:9000/v1"
        c = normalize_config(cfg)
        self.assertIn("[::1]", c["providers"][0]["baseUrl"])

    def test_https_ipv6_unspecified_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0] = copy.deepcopy(_VALID_CLOUD)
        cfg["roles"]["leader"] = "cloud-gpu"
        cfg["providers"][0]["baseUrl"] = "https://[::]:443/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_length_limit(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://" + "a" * 2050 + ":8080/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_schemaversion_bool_rejected(self):
        """schemaVersion must reject True (bool is subclass of int)."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["schemaVersion"] = True
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_schemaversion_float_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["schemaVersion"] = 1.0
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_schemaversion_zero_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["schemaVersion"] = 0
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_provider_non_dict_rejected(self):
        """Malformed JSON: provider as list should not raise AttributeError."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0] = [1, 2, 3]
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_roles_non_dict_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["roles"] = "bad"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_policy_non_dict_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["policy"] = None
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_cloud_enabled_missing_credential_ref(self):
        """Cloud provider enabled without credentialRef must fail."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        p = copy.deepcopy(_VALID_CLOUD)
        p["credentialRef"] = None
        cfg["providers"].append(p)
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_cloud_disabled_no_credential_ok(self):
        """Cloud provider disabled without credentialRef is fine."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        p = copy.deepcopy(_VALID_CLOUD)
        p["credentialRef"] = None
        p["enabled"] = False
        cfg["providers"].append(p)
        c = normalize_config(cfg)
        self.assertEqual(len(c["providers"]), 2)

    def test_independence_roles_mutation(self):
        """Mutating returned roles must not affect input."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        result = normalize_config(cfg)
        result["roles"]["leader"] = "MUTATED"
        result["roles"]["backups"].append("MUTATED")
        self.assertEqual(cfg["roles"]["leader"], "tower1-main")
        self.assertEqual(cfg["roles"]["backups"], [])

    def test_independence_policy_mutation(self):
        """Mutating returned policy must not affect input."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        result = normalize_config(cfg)
        result["policy"]["allowCloud"] = True
        result["policy"]["maxAttempts"] = 99
        self.assertEqual(cfg["policy"]["allowCloud"], False)
        self.assertEqual(cfg["policy"]["maxAttempts"], 3)

    def test_independence_providers_mutation(self):
        """Mutating returned providers must not affect input."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        result = normalize_config(cfg)
        result["providers"][0]["label"] = "MUTATED"
        result["providers"].append({"id": "x"})
        self.assertEqual(cfg["providers"][0]["label"], "Tower1 Main")
        self.assertEqual(len(cfg["providers"]), 1)

    def test_control_char_in_string_field(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["label"] = "Tab\there"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_control_char_before_trim(self):
        """Control char detection must fire before any whitespace trim."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["label"] = "\x00\x20"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_error_message_no_user_input(self):
        """Error messages must not contain raw user input."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["label"] = "inject<script>alert(1)</script>\n"
        with self.assertRaises(ConfigError) as ctx:
            normalize_config(cfg)
        self.assertNotIn("<script>", str(ctx.exception))

    def test_config_error_is_value_error(self):
        """ConfigError must be catchable as ValueError."""
        try:
            raise ConfigError("test", "test")
        except ValueError:
            pass
        else:
            self.fail("ConfigError not caught as ValueError")

    def test_invalid_json_dict_raises_config_error(self):
        """Non-dict input must raise ConfigError, not TypeError."""
        with self.assertRaises(ConfigError):
            normalize_config("not a dict")
        with self.assertRaises(ConfigError):
            normalize_config(None)
        with self.assertRaises(ConfigError):
            normalize_config([1, 2])

    def test_http_non_loopback_ip_rejected(self):
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://10.0.0.1:8080/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_https_loopback_ip_allowed(self):
        """HTTPS to a loopback IP should be allowed (only HTTP is restricted)."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0] = copy.deepcopy(_VALID_CLOUD)
        cfg["roles"]["leader"] = "cloud-gpu"
        cfg["providers"][0]["baseUrl"] = "https://127.0.0.1:443/v1"
        cfg["providers"][0]["credentialRef"] = "ref-a"
        c = normalize_config(cfg)
        self.assertEqual(c["providers"][0]["baseUrl"], "https://127.0.0.1:443/v1")

    def test_credential_ref_not_trimmed(self):
        """credentialRef must not be trimmed; spaces make it invalid."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["credentialRef"] = " ref-a "
        with self.assertRaises(ConfigError):
            normalize_config(cfg)

    def test_url_malformed_port_value_error_caught(self):
        """Non-numeric port must produce ConfigError, not ValueError."""
        cfg = copy.deepcopy(_VALID_LOCAL)
        cfg["providers"][0]["baseUrl"] = "http://127.0.0.1:abc/v1"
        with self.assertRaises(ConfigError):
            normalize_config(cfg)


if __name__ == "__main__":
    unittest.main()
