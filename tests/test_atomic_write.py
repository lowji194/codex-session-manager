import ctypes
import errno
import os
import tempfile
import threading
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

import main


def windows_error(code):
    error = PermissionError(errno.EACCES, 'test file locked')
    error.winerror = code
    return error


class AtomicWriteTests(unittest.TestCase):
    def test_transient_windows_denial_retries_then_replaces(self):
        for code in (5, 32, 33):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'account.csm'
                path.write_bytes(b'old')
                replace = os.replace
                calls = 0

                def busy_then_replace(source, target):
                    nonlocal calls
                    calls += 1
                    if calls < 3:
                        raise windows_error(code)
                    replace(source, target)

                with patch.object(main.os, 'replace', side_effect=busy_then_replace), patch.object(main.time, 'sleep'):
                    main.atomic_write(path, b'new')
                self.assertEqual(calls, 3)
                self.assertEqual(path.read_bytes(), b'new')
                self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_permanent_denial_preserves_backup_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'account.csm'
            path.write_bytes(b'old')
            with patch.object(main.os, 'replace', side_effect=windows_error(5)) as replace, patch.object(main.time, 'sleep'):
                with self.assertRaisesRegex(PermissionError, 'Modify'):
                    main.atomic_write(path, b'new')
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(path.read_bytes(), b'old')
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_other_io_errors_fail_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'account.csm'
            path.write_bytes(b'old')
            with patch.object(main.os, 'replace', side_effect=OSError(errno.ENOSPC, 'disk full')) as replace, patch.object(main.time, 'sleep') as sleep:
                with self.assertRaises(OSError):
                    main.atomic_write(path, b'new')
            replace.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(path.read_bytes(), b'old')
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing modes')
    def test_real_windows_reader_releases_lock_during_retry(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'account.csm'
            path.write_bytes(b'old')
            handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
            self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
            timer = threading.Timer(0.2, kernel.CloseHandle, args=(handle,))
            timer.start()
            try:
                main.atomic_write(path, b'new')
            finally:
                timer.join()
            self.assertEqual(path.read_bytes(), b'new')
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])
