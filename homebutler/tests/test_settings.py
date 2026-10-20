import unittest

from homebutler.settings import Settings, SettingsError


class SettingsTests(unittest.TestCase):
    def test_defaults_run_locally(self):
        settings = Settings.from_env({})
        self.assertTrue(settings.database_url.startswith("sqlite"))
        self.assertEqual((settings.database_schema, settings.default_household, settings.config_check_seconds), ("hb", "A", 60))

    def test_invalid_values_name_the_variable_never_the_value(self):
        for env, word in [({"CONFIG_CHECK_SECONDS": "x"}, "CONFIG_CHECK_SECONDS"), ({"CONFIG_CHECK_SECONDS": "5"}, "CONFIG_CHECK_SECONDS"),
                          ({"DASHBOARD_ASSERTION_KEY": "short-secret"}, "DASHBOARD_ASSERTION_KEY"),
                          ({"SPREADSHEET_ID": "abc"}, "GOOGLE_CREDENTIALS"), ({"DATABASE_SCHEMA": "public"}, "DATABASE_SCHEMA"),
                          ({"DATABASE_SCHEMA": "a;b"}, "DATABASE_SCHEMA")]:
            with self.assertRaises(SettingsError) as raised:
                Settings.from_env(env)
            self.assertIn(word, str(raised.exception))
            self.assertNotIn("short-secret", str(raised.exception))

    def test_secrets_stay_out_of_the_text_form(self):
        settings = Settings.from_env({"DASHBOARD_ASSERTION_KEY": "k" * 40, "SPREADSHEET_ID": "s", "GOOGLE_CREDENTIALS": "{secret}"})
        self.assertNotIn("k" * 40, repr(settings)); self.assertNotIn("{secret}", repr(settings))


if __name__ == "__main__":
    unittest.main()
