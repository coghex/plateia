"""Tests for rotate-logs, in a temporary directory with a fake service."""
import gzip
import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)

_loader = importlib.machinery.SourceFileLoader(
    "rotate_logs", str(Path(__file__).resolve().parent.parent / "rotate-logs"))
_spec = importlib.util.spec_from_loader("rotate_logs", _loader)
rotate_logs = importlib.util.module_from_spec(_spec)
_loader.exec_module(rotate_logs)


class RotateTests(unittest.TestCase):
    def test_rotates_shifts_and_bounds_the_copies(self):
        d = Path(tempfile.mkdtemp())
        log = d / "ergo.log"
        log.write_text("current\n")
        for n in range(1, 5):
            with gzip.open(d / f"ergo.log.{n}.gz", "wt") as f:
                f.write(f"copy {n}\n")
        signalled = []

        def service_reopens(label):
            signalled.append(label)
            log.write_text("")  # the service opens a fresh log on SIGHUP
        rotate_logs.rotate(log, "com.example.svc", keep=4, signal_service=service_reopens, settle=0)
        self.assertEqual(signalled, ["com.example.svc"])
        self.assertEqual(log.read_text(), "")
        self.assertEqual(gzip.open(d / "ergo.log.1.gz", "rt").read(), "current\n")
        self.assertEqual(gzip.open(d / "ergo.log.2.gz", "rt").read(), "copy 1\n")
        self.assertEqual(gzip.open(d / "ergo.log.4.gz", "rt").read(), "copy 3\n")
        self.assertFalse((d / "ergo.log.5.gz").exists())
        self.assertFalse((d / "ergo.log.1").exists())


if __name__ == "__main__":
    unittest.main()
