"""Synthetic Antigravity quota fixtures; no account files or network access."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import main


def credential(email="person@example.com", token="synthetic-access"):
    return main.WindowsCredential("gemini:antigravity", json.dumps({"token": {"access_token": token}, "userInfo": {"email": email, "name": "Test"}}).encode())


class AntigravityQuotaTests(unittest.TestCase):
    def api(self):
        api = object.__new__(main.WebApi)
        api.agy_store = Mock()
        api.agy_store.current_credential.return_value = None
        api.agy_store.list_backups.return_value = []
        api.records = []
        api._agy_quota_cache = {}
        api._agy_quota_lock = __import__("threading").Lock()
        return api

    def usage(self):
        return {"plan": "AGY", "limitReached": False, "quotas": {"GEM 5H": {"used": 25, "remaining": 75, "total": 100, "resetAt": "2030-01-01T00:00:00Z"}}}

    def test_each_reported_window_is_preserved(self):
        result = main._agy_usage_from_groups([{"displayName": "Gemini", "buckets": [
            {"window": "five_hour", "remainingFraction": .8},
            {"window": "weekly", "remainingFraction": .25},
        ]}, {"displayName": "Third party", "buckets": [{"window": "weekly", "remainingFraction": 0}]}])
        self.assertEqual(set(result["quotas"]), {"GEM 5H", "GEM 7D", "3P 7D"})
        self.assertEqual(result["quotas"]["GEM 5H"]["remaining"], 80)
        self.assertTrue(result["limitReached"])

    def test_saved_account_quota_is_real_and_errors_do_not_leak(self):
        api = self.api()
        records = [main.BackupRecord(Path("one.agy"), "One", "one@example.com", "2030-01-01"),
                   main.BackupRecord(Path("two.agy"), "Two", "two@example.com", "2030-01-01")]
        api.agy_store.list_backups.return_value = records
        api.agy_store.read_session.side_effect = [(credential(token="one"), b"scope"), (credential(token="two"), b"scope")]
        with patch.object(main, "fetch_antigravity_quota_for_credential", side_effect=lambda cred: (self.usage(), "", None) if b'"one"' in cred.blob else ({"quotas": {}}, "secret-access-token", None)):
            state = api.build_agy_state()
        self.assertTrue(state["supportsQuota"])
        self.assertEqual(state["backups"][0]["quotaWindows"][0]["remaining"], 75)
        self.assertEqual(state["backups"][0]["session"], "—")
        self.assertNotIn("secret-access-token", json.dumps(state))
        self.assertEqual(state["backups"][1]["status"], "UNAVAILABLE")

    def test_cache_and_explicit_force_refresh(self):
        api = self.api()
        with patch.object(main, "fetch_antigravity_quota_for_credential", return_value=(self.usage(), "", None)) as fetch:
            api._read_agy_quota(credential())
            api._read_agy_quota(credential())
            self.assertEqual(fetch.call_count, 1)
            api._read_agy_quota(credential(), force=True)
            self.assertEqual(fetch.call_count, 2)
            api._read_agy_quota(credential(token="other"))
            self.assertEqual(fetch.call_count, 3)

    def test_saved_refresh_uses_atomic_store_updater(self):
        api = self.api()
        record = main.BackupRecord(Path("one.agy"), "One", "one@example.com", "2030-01-01")
        api.agy_store.list_backups.return_value = [record]
        original, updated = credential(token="old"), credential(token="new")
        api.agy_store.read_session.return_value = (original, b"scope")
        with patch.object(main, "fetch_antigravity_quota_for_credential", return_value=(self.usage(), "", updated)), patch.object(main, "credential_write") as write:
            api.build_agy_state()
        api.agy_store.update_record_credential.assert_called_once_with(record, updated, b"scope")
        write.assert_not_called()

    def test_current_account_change_during_refresh_never_overwrites_new_login(self):
        api = self.api()
        original, updated, other = credential(token="old"), credential(token="new"), credential(token="other")
        api.agy_store.current_credential.side_effect = [original, other]
        with patch.object(main, "agy_profile_from_credential", return_value={"name": "Person", "email": "person@example.com"}), patch.object(main, "fetch_antigravity_quota_for_credential", return_value=(self.usage(), "", updated)), patch.object(main, "credential_write") as write:
            state = api.build_agy_state()
        write.assert_not_called()
        self.assertEqual(state["currentQuota"]["quotaWindows"][0]["label"], "GEM 5H")

    def test_cli_report_current_email_works_without_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "email.json"
            store = Mock()
            store.current_profile.return_value = {"email": "synthetic@example.com"}
            with patch.object(main, "SessionStore", return_value=store), patch.object(main.sys, "stdout", None):
                self.assertEqual(main.cli(["--current-email", "--report", str(report)]), 0)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), {"email": "synthetic@example.com"})

    def test_cli_report_quota_works_without_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "quota.json"
            fixture = {"total": 0, "accounts": [], "source": "local-session"}
            with patch.object(main, "rainmeter_quota_payload", return_value=fixture) as fetch, patch.object(main.sys, "stdout", None):
                self.assertEqual(main.cli(["--rainmeter-quota-json", "--max-accounts", "8", "--report", str(report)]), 0)
            fetch.assert_called_once_with(8)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), fixture)

    def test_cli_report_switch_works_without_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "switch.json"
            fixture = {"ok": True, "switched": True}
            with patch.object(main, "switch_agy_to_email", return_value=fixture) as switch, patch.object(main.sys, "stdout", None):
                self.assertEqual(main.cli(["--switch-email", "synthetic@example.com", "--provider", "antigravity", "--report", str(report)]), 0)
            switch.assert_called_once_with("synthetic@example.com")
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), fixture)


if __name__ == "__main__":
    unittest.main()
