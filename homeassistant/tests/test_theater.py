"""The HA-side relay is an allowlist, not a general HTTP proxy."""
import unittest


class TheaterRelayValidationTests(unittest.TestCase):

    def module(self):
        from custom_components.home_butler import theater
        return theater

    def test_only_local_origins_are_accepted(self):
        theater = self.module()
        self.assertEqual(theater.normalize_url(" http://192.168.1.10:8080/ "), "http://192.168.1.10:8080")
        for bad in ["ftp://host", "http://user:pw@host", "http://host/summary",
                    "http://host?a=1", "not a url", ""]:
            with self.assertRaises(ValueError):
                theater.normalize_url(bad)

    def test_only_known_boolean_flags_are_relayed(self):
        theater = self.module()
        self.assertEqual(theater.validate_flags({"kef_link": True}), {"kef_link": True})
        for bad in [{}, {"kef_link": "yes"}, {"unknown": True}, {"kef_link": 1}, "flags", None]:
            with self.assertRaises(ValueError):
                theater.validate_flags(bad)

    def test_actions_are_a_fixed_method_and_path_map(self):
        theater = self.module()
        self.assertEqual(theater.ACTIONS, {"summary": ("GET", "/summary"),
                                           "set_flags": ("POST", "/flags")})
