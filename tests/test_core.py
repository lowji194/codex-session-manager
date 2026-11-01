import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


def jwt(payload):
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.x"


class CoreTests(unittest.TestCase):
    def test_default_backups_are_stored_in_tool_folder(self):
        self.assertEqual(main.DATA_ROOT, main.PROJECT_ROOT / 'tool')
        self.assertEqual(main.BACKUP_DIR, main.PROJECT_ROOT / 'tool' / 'backups')

    def test_legacy_backup_migration_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / 'legacy'
            destination = root / 'tool' / 'backups'
            legacy.mkdir()
            original = legacy / 'account.csm'
            original.write_bytes(b'legacy-session')

            self.assertEqual(main.migrate_legacy_backups(legacy, destination), 1)
            self.assertEqual((destination / original.name).read_bytes(), b'legacy-session')
            self.assertEqual(original.read_bytes(), b'legacy-session')

            (destination / original.name).write_bytes(b'new-session')
            self.assertEqual(main.migrate_legacy_backups(legacy, destination), 0)
            self.assertEqual((destination / original.name).read_bytes(), b'new-session')

    def test_legacy_migration_runs_once_and_does_not_resurrect_deleted_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / 'legacy'
            destination = root / 'tool' / 'backups'
            marker = root / 'tool' / '.legacy-backups-migrated'
            legacy.mkdir()
            original = legacy / 'deleted-account.csm'
            original.write_bytes(b'legacy-session')

            self.assertEqual(
                main.migrate_legacy_backups_once(legacy, destination, marker), 1,
            )
            (destination / original.name).unlink()
            self.assertEqual(
                main.migrate_legacy_backups_once(legacy, destination, marker), 0,
            )
            self.assertFalse((destination / original.name).exists())
            self.assertTrue(original.exists())

    def test_web_assets_exist(self):
        for name in ('index.html', 'styles.css', 'app.js'):
            self.assertTrue((main.WEB_DIR / name).is_file(), name)

    def test_provider_switch_is_wired_for_antigravity(self):
        html = (main.WEB_DIR / 'index.html').read_text(encoding='utf-8')
        script = (main.WEB_DIR / 'app.js').read_text(encoding='utf-8')
        self.assertIn('data-provider="codex"', html)
        self.assertIn('data-provider="antigravity"', html)
        self.assertIn("callApi('select_provider'", script)
        self.assertIn("state.provider === 'antigravity'", script)

    def test_direct_quota_sync_button_is_wired(self):
        html = (main.WEB_DIR / 'index.html').read_text(encoding='utf-8')
        script = (main.WEB_DIR / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="reload"', html)
        self.assertIn("$('reload').addEventListener", script)
        self.assertIn('Quota trực tiếp từ phiên Codex', html)
        self.assertNotIn('Rainmeter', html + script)

    def test_quota_from_usage_uses_real_window_durations(self):
        quota = main.quota_from_usage({
            'plan_type': 'plus',
            'rate_limit': {
                'limit_reached': False,
                'primary_window': {'used_percent': 38, 'limit_window_seconds': 18000, 'reset_at': 1890000000},
                'secondary_window': {'used_percent': 52, 'limit_window_seconds': 604800, 'reset_at': 1890600000},
            },
            'rate_limit_reset_credits': {'available_count': 2},
            'credits': {'balance': '4.50'},
        })
        self.assertEqual(quota['plan'], 'PLUS')
        self.assertEqual(quota['session'], 'SESSION 38% · còn 62')
        self.assertEqual(quota['weekly'], '7D 52% · còn 48')
        self.assertEqual(quota['reset_credits'], 2)
        self.assertTrue(quota['found'])

    def test_app_server_payload_uses_codex_bucket(self):
        quota = main.quota_from_app_server({
            'rateLimits': {},
            'rateLimitsByLimitId': {
                'codex': {
                    'planType': 'pro',
                    'primary': {'usedPercent': 100, 'windowDurationMins': 300, 'resetsAt': 1890000000},
                    'secondary': {'usedPercent': 20, 'windowDurationMins': 10080, 'resetsAt': 1890600000},
                    'rateLimitReachedType': 'rate_limit_reached',
                }
            },
            'rateLimitResetCredits': {'availableCount': 1},
        })
        self.assertEqual(quota['plan'], 'PRO')
        self.assertEqual(quota['session'], 'SESSION 100% · còn 0')
        self.assertEqual(quota['status'], 'ĐÃ HẾT QUOTA')

    def test_inactive_expired_backup_skips_slow_app_server_refresh(self):
        service = main.QuotaService(ttl_seconds=300)
        with patch.object(main, 'fetch_quota_http', side_effect=main.QuotaAuthError('expired')), \
             patch.object(main, 'refresh_quota_with_app_server') as refresh:
            quota, updated = service.fetch(
                'inactive', b'{}', force=True, allow_token_refresh=False,
            )
        refresh.assert_not_called()
        self.assertEqual(quota['status'], 'UNAVAILABLE')
        self.assertFalse(quota['found'])
        self.assertIsNone(updated)

    def test_rainmeter_export_uses_session_backups(self):
        state = {
            'current': {'email': 'active@example.com'},
            'quotaError': '',
            'backups': [{
                'id': 'active.csm',
                'email': 'active@example.com',
                'plan': 'PLUS',
                'status': 'HOẠT ĐỘNG',
                'quotaSource': 'codex-session',
                'quotaWindows': [{
                    'label': 'SESSION', 'used': 25, 'total': 100,
                    'remaining': 75, 'reset_at': 1890000000, 'unlimited': False,
                }],
                'resetCredits': 1,
                'credits': '2.00',
            }],
        }
        agy_record = main.BackupRecord(
            Path('agy@example.com.agy'), 'AGY User', 'agy@example.com',
            '2026-10-29T20:00:00+07:00', provider='antigravity',
        )
        agy_credential = main.WindowsCredential('gemini:antigravity', b'{}')
        with patch.object(main.WebApi, 'build_state', return_value=state), \
             patch.object(main.AntigravitySessionStore, 'current_profile', return_value={'email': 'agy@example.com'}), \
             patch.object(main.AntigravitySessionStore, 'list_backups', return_value=[agy_record]), \
             patch.object(main.AntigravitySessionStore, 'read_session', return_value=(agy_credential, b'scope')), \
             patch.object(main, 'fetch_antigravity_quota_for_credential', return_value=({
                 'plan': 'AGY', 'limitReached': False,
                 'quotas': {'GEM 5H': {'used': 20, 'total': 100, 'remaining': 80, 'resetAt': None, 'unlimited': False}},
                 'resetCredits': None,
             }, '', None)):
            payload = main.rainmeter_quota_payload(8)
        self.assertEqual(payload['source'], 'local-session')
        self.assertEqual(payload['currentEmail'], 'active@example.com')
        self.assertEqual(payload['currentEmails']['antigravity'], 'agy@example.com')
        self.assertEqual(payload['accounts'][0]['usage']['quotas']['SESSION']['remaining'], 75)
        self.assertEqual(payload['accounts'][0]['usage']['resetCredits'], 1)
        self.assertEqual(payload['accounts'][1]['connection']['provider'], 'antigravity')
        self.assertEqual(payload['accounts'][1]['usage']['quotas']['GEM 5H']['remaining'], 80)
        self.assertNotIn('access_token', json.dumps(payload))

    def test_account_order_is_plan_then_email_and_ignores_live_state(self):
        accounts = [
            {'id': '4', 'email': 'z-free@example.com', 'plan': 'FREE', 'status': 'ACTIVE', 'backupAt': 'new'},
            {'id': '2', 'email': 'z-plus@example.com', 'plan': 'PLUS', 'status': 'ACTIVE', 'backupAt': 'old'},
            {'id': '1', 'email': 'a-pro@example.com', 'plan': 'PRO', 'status': 'LIMITED', 'backupAt': 'new'},
            {'id': '3', 'email': 'a-plus@example.com', 'plan': 'PLUS', 'status': 'LIMITED', 'backupAt': 'new'},
        ]
        first = sorted(accounts, key=main.stable_account_sort_key)
        for account in accounts:
            account['status'] = 'SWITCHED'
            account['backupAt'] = 'changed'
            account['session'] = 'SESSION 100%'
        second = sorted(accounts, key=main.stable_account_sort_key)

        expected = [
            'a-pro@example.com',
            'a-plus@example.com',
            'z-plus@example.com',
            'z-free@example.com',
        ]
        self.assertEqual([item['email'] for item in first], expected)
        self.assertEqual([item['email'] for item in second], expected)

    def test_quota_progress_uses_remaining_tokens_and_zero_is_red(self):
        script = (main.WEB_DIR / 'app.js').read_text(encoding='utf-8')
        self.assertIn('style="width:${remaining}%"', script)
        self.assertIn("if (remaining <= 0) return 'badge-bad'", script)
        self.assertIn("quotaBar(item.weekly, 'weekly')", script)

    def test_state_auto_refreshes_without_overlapping_requests(self):
        script = (main.WEB_DIR / 'app.js').read_text(encoding='utf-8')
        self.assertIn('const AUTO_REFRESH_MS = 300_000', script)
        self.assertIn('window.setInterval(autoRefresh, AUTO_REFRESH_MS)', script)
        self.assertIn('activeRequests > 0 || document.hidden', script)
        self.assertIn("document.addEventListener('visibilitychange'", script)

    def test_pywebview_bridge_replaces_qwebchannel(self):
        html = (main.WEB_DIR / 'index.html').read_text(encoding='utf-8')
        script = (main.WEB_DIR / 'app.js').read_text(encoding='utf-8')
        self.assertNotIn('qwebchannel.js', html.lower())
        self.assertIn("window.addEventListener('pywebviewready'", script)
        self.assertIn('window.pywebview.api', script)

    def test_web_api_returns_serializable_state(self):
        # Never refresh real account tokens or write production backups in tests.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = main.SessionStore(root / 'auth.json', root / 'backups')
            with patch.object(main, 'SessionStore', return_value=store):
                state = main.WebApi().get_state()
        json.dumps(state, ensure_ascii=False)
        self.assertIn('backups', state)

    def test_profile_from_auth(self):
        raw = json.dumps({"tokens": {"id_token": jwt({"name": "Test User", "email": "test@example.com"})}}).encode()
        self.assertEqual(main.profile_from_auth(raw), {"name": "Test User", "email": "test@example.com"})

    def test_safe_part(self):
        self.assertEqual(main.safe_part('A/B: C'), 'A_B_ C')

    @unittest.skipUnless(__import__('os').name == 'nt', 'DPAPI requires Windows')
    def test_dpapi_roundtrip(self):
        secret = b'secret-session-data'
        self.assertEqual(main.dpapi_unprotect(main.dpapi_protect(secret)), secret)

    @unittest.skipUnless(__import__('os').name == 'nt', 'DPAPI requires Windows')
    def test_store_backup_and_restore(self):
        raw = json.dumps({"tokens": {"id_token": jwt({"name": "Test User", "email": "test@example.com"})}}).encode()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            auth = root / '.codex' / 'auth.json'
            auth.parent.mkdir()
            auth.write_bytes(raw)
            store = main.SessionStore(auth, root / 'backups')
            record = store.create_backup()
            self.assertTrue(record.path.exists())
            auth.unlink()
            store.restore(record)
            self.assertEqual(auth.read_bytes(), raw)

    @unittest.skipUnless(__import__('os').name == 'nt', 'DPAPI requires Windows')
    def test_antigravity_login_bundle_roundtrip_and_no_plain_token(self):
        def agy_blob(email, access, refresh):
            return json.dumps({
                'token': {
                    'access_token': access,
                    'token_type': 'Bearer',
                    'refresh_token': refresh,
                    'expiry': '2026-12-01T00:00:00Z',
                    'expires_in': 3600,
                },
                'auth_method': 'oauth',
                'id_token_email': email,
            }).encode()

        target = main.WindowsCredential(
            target='gemini:antigravity',
            blob=agy_blob('target@example.com', 'target-access-secret', 'target-refresh-secret'),
            username='target-user',
        )
        current = main.WindowsCredential(
            target='gemini:antigravity',
            blob=agy_blob('current@example.com', 'current-access-secret', 'current-refresh-secret'),
            username='current-user',
        )
        vault = {'value': target}

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            scope = root / 'Antigravity' / 'credential-user-scope'
            scope.parent.mkdir()
            scope.write_bytes(b'target-scope-1234')
            store = main.AntigravitySessionStore(root / 'backups', 'gemini:antigravity', scope)

            with patch.object(main, 'credential_read', side_effect=lambda _: vault['value']), \
                 patch.object(main, 'credential_write', side_effect=lambda value: vault.__setitem__('value', value)):
                record = store.create_backup()
                backup_text = record.path.read_text(encoding='utf-8')
                self.assertNotIn('target-access-secret', backup_text)
                self.assertNotIn('target-refresh-secret', backup_text)
                self.assertEqual(record.provider, 'antigravity')

                vault['value'] = current
                scope.write_bytes(b'current-scope-12')
                store.restore(record)

            self.assertEqual(main.agy_profile_from_credential(vault['value'].blob)['email'], 'target@example.com')
            self.assertEqual(scope.read_bytes(), b'target-scope-1234')
            self.assertIsNotNone(store.find_by_email('current@example.com'))

    def test_antigravity_profile_rejects_missing_token(self):
        raw = json.dumps({'id_token_email': 'test@example.com', 'token': {}}).encode()
        with self.assertRaises(ValueError):
            main.agy_profile_from_credential(raw)

    @unittest.skipUnless(__import__('os').name == 'nt', 'DPAPI requires Windows')
    def test_backup_overwrites_same_email(self):
        first = json.dumps({"tokens": {"id_token": jwt({"name": "Old Name", "email": "same@example.com"})}}).encode()
        second = json.dumps({"tokens": {"id_token": jwt({"name": "New Name", "email": "same@example.com"})}}).encode()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            auth = root / '.codex' / 'auth.json'
            auth.parent.mkdir()
            auth.write_bytes(first)
            store = main.SessionStore(auth, root / 'backups')
            old_record = store.create_backup()

            auth.write_bytes(second)
            new_record = store.create_backup()
            records = store.list_backups()

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].email, 'same@example.com')
            self.assertEqual(records[0].name, 'New Name')
            self.assertFalse(old_record.path.exists())
            self.assertTrue(new_record.path.exists())

    @unittest.skipUnless(__import__('os').name == 'nt', 'DPAPI requires Windows')
    def test_switching_to_current_email_is_safe_noop(self):
        raw = json.dumps({"tokens": {"id_token": jwt({"name": "Current", "email": "current@example.com"})}}).encode()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            auth = root / '.codex' / 'auth.json'
            auth.parent.mkdir()
            auth.write_bytes(raw)
            store = main.SessionStore(auth, root / 'backups')
            result = main.switch_to_email('CURRENT@example.com', store)
            self.assertTrue(result['ok'])
            self.assertFalse(result['changed'])
            self.assertEqual(auth.read_bytes(), raw)

    def test_switch_requires_existing_backup_before_stopping_codex(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = main.SessionStore(root / 'missing-auth.json', root / 'backups')
            with self.assertRaises(FileNotFoundError):
                main.switch_to_email('missing@example.com', store)

    @unittest.skipUnless(__import__('os').name == 'nt', 'DPAPI requires Windows')
    def test_switch_backs_up_current_session_before_restore(self):
        target_raw = json.dumps({"tokens": {"id_token": jwt({"name": "Target", "email": "target@example.com"})}}).encode()
        current_raw = json.dumps({"tokens": {"id_token": jwt({"name": "Current", "email": "current@example.com"})}}).encode()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            auth = root / '.codex' / 'auth.json'
            auth.parent.mkdir()
            store = main.SessionStore(auth, root / 'backups')
            auth.write_bytes(target_raw)
            store.create_backup()
            auth.write_bytes(current_raw)

            with patch.object(main, 'stop_codex'), patch.object(main, 'start_codex'):
                result = main.switch_to_email('target@example.com', store)

            self.assertTrue(result['changed'])
            self.assertEqual(auth.read_bytes(), target_raw)
            self.assertIsNotNone(store.find_by_email('current@example.com'))


if __name__ == '__main__':
    unittest.main()
