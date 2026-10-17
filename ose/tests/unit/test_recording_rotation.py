"""Rotation ownership, monotonic names and portable cyclic reuse."""
import errno
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ose.recording.rotation import OutputFiles, parse_size


class RotationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_size_units_and_exact_fractional_bytes(self):
        for text, expected in (("off", None), ("123", 123), ("2B", 2), ("1.5KiB", 1536),
                               ("100MiB", 100 * 1024**2), ("1GiB", 1024**3)):
            self.assertEqual(parse_size(text), expected)
        for text in ("0", "-1B", "1GB", "nan", "0.1B", "1.000000000000000000000000000001B"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_size(text)

    def test_cyclic_reuse_keeps_increasing_names_and_replaces_old_contents(self):
        files = OutputFiles(self.root / "capture.pcapng", numbered=True, cycle=3)
        self.addCleanup(files.close)
        files.preflight()
        for index in range(1, 6):
            stream = files.open_next()
            stream.write(str(index).encode())
            files.close()
            self.assertEqual(files.path.name, f"capture_{index:03d}.pcapng")
            self.assertLessEqual(len(list(self.root.iterdir())), 3)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ["capture_003.pcapng", "capture_004.pcapng", "capture_005.pcapng"])
        self.assertEqual([files.name(index).read_bytes() for index in range(3, 6)], [b"3", b"4", b"5"])
        self.assertEqual(files.status(), {"file_index": 5, "rotations": 4, "file_count": 3})
        self.assertEqual(files.name(1000).name, "capture_1000.pcapng")

    def test_new_or_existing_names_are_never_overwritten(self):
        files = OutputFiles(self.root / "capture.pcapng", numbered=True, cycle=1)
        self.addCleanup(files.close)
        files.preflight()
        files.open_next().write(b"owned")
        files.close()
        files.name(2).write_bytes(b"unrelated")
        with self.assertRaises(FileExistsError):
            files.open_next()
        self.assertEqual(files.name(1).read_bytes(), b"owned")
        self.assertEqual(files.name(2).read_bytes(), b"unrelated")
        with self.assertRaises(FileExistsError):
            OutputFiles(files.base, numbered=True).preflight()

    def test_externally_replaced_oldest_file_is_preserved(self):
        files = OutputFiles(self.root / "capture", numbered=True, cycle=1)
        self.addCleanup(files.close)
        files.open_next().write(b"owned")
        files.close()
        replacement = self.root / "replacement"
        replacement.write_bytes(b"unrelated")
        replacement.replace(files.path)
        with self.assertRaisesRegex(OSError, "replaced outside"):
            files.open_next()
        self.assertEqual(files.path.read_bytes(), b"unrelated")
        self.assertFalse(files.name(2).exists())

    def test_cyclic_reuse_on_filesystems_without_hard_links(self):
        files = OutputFiles(self.root / "capture.pcapng", numbered=True, cycle=1)
        self.addCleanup(files.close)
        files.open_next().write(b"old contents")
        files.close()
        with patch("ose.recording.rotation.os.link", create=True,
                   side_effect=OSError(errno.EOPNOTSUPP, "unsupported")):
            files.open_next().write(b"new")
        files.close()
        self.assertFalse(files.name(1).exists())
        self.assertEqual(files.name(2).read_bytes(), b"new")

    def test_dangling_symlink_is_an_existing_name(self):
        base = self.root / "capture.pcapng"
        base.symlink_to(self.root / "missing")
        with self.assertRaises(FileExistsError):
            OutputFiles(base).preflight()
