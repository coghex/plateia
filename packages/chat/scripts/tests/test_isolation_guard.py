"""The test-isolation guard fails closed: once imported, a test that reaches for
the real home's state or config, the network, or a command that could touch a
live agent is refused before the access happens. Every probe here only reads,
so a broken guard could not change anything. Plateia's own test."""
import os
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)

REAL = Path(_isolation.REAL_HOME)


class IsolationGuardTests(unittest.TestCase):
    def test_the_sandbox_replaces_home_and_the_chat_paths(self):
        home = str(_isolation.HOME)
        self.assertNotEqual(os.path.realpath(home), str(REAL))
        self.assertEqual((os.environ["HOME"], os.environ["CHAT_TEST_SANDBOX"]), (home, home))
        for var in ("CHAT_STATE", "CHAT_CONFIG"):
            self.assertTrue(os.environ[var].startswith(home + os.sep), var)
        for var in ("CMUX_SURFACE_ID", "CMUX_SOCKET_PATH", "CHAT_AGENT_ID", "CHAT_CHANNEL", "CHAT_REQUEST_ID"):
            self.assertNotIn(var, os.environ)

    def test_reading_real_state_or_config_is_refused(self):
        # The guard matches resolved paths, so the probes avoid names that may be
        # symlinks on a real machine (an installed ~/.local/bin command can
        # resolve outside the protected roots).
        for rel in (".local/state/chat/identities.json", ".config/chat/config.json",
                    ".local/bin/plateia-isolation-probe", "Library/Application Support/kanban/state.json"):
            path = REAL / rel
            with self.subTest(path=rel):
                with self.assertRaisesRegex(_isolation.LiveStateAccess, "live path"):
                    with open(path, "rb"):
                        pass
                with self.assertRaises(_isolation.LiveStateAccess):
                    path.read_text()
        with self.assertRaises(_isolation.LiveStateAccess):
            os.listdir(REAL / ".config")

    def test_the_network_is_refused(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with self.assertRaisesRegex(_isolation.LiveStateAccess, "network connection"):
                sock.connect(("127.0.0.1", 9))

    def test_commands_that_reach_live_agents_are_refused(self):
        for command in ("cmux", "pchat", "chat-bridge", "claude", "codex", "gh", "launchctl", "ps", "lsof"):
            with self.subTest(command=command):
                with self.assertRaisesRegex(_isolation.LiveStateAccess, f"refused to run {command}"):
                    subprocess.run([command, "--version"], capture_output=True)
                with self.assertRaises(_isolation.LiveStateAccess):
                    subprocess.run(["/usr/bin/" + command, "--version"], capture_output=True)

    def test_a_module_bound_to_a_live_path_fails_closed(self):
        live = types.ModuleType("live_module")
        live.STATE = REAL / ".local/state/chat"
        with self.assertRaisesRegex(_isolation.LiveStateAccess, r"live_module\.STATE is live"):
            _isolation.check_bound(live)
        sandboxed = types.ModuleType("sandboxed_module")
        sandboxed.STATE = _isolation.CHAT_STATE
        _isolation.check_bound(sandboxed)

    def test_the_sandbox_itself_stays_usable(self):
        with tempfile.TemporaryDirectory(dir=_isolation.HOME) as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("invented")
            self.assertEqual(path.read_text(), "invented")
        result = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True)
        self.assertEqual(result.stdout, "ok\n")


if __name__ == "__main__":
    unittest.main()
