"""binding.py: the one session resolver. Every inventory is a fake."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import binding  # noqa: E402

_isolation.check_bound(binding)

P = "beta"
MGR_S, CHILD_S = "AAAAAAAA-0000-0000-0000-000000000001", "BBBBBBBB-0000-0000-0000-000000000002"
T0 = time.time() - 3600


def lstart(epoch):
    return time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(epoch))


class FakeInventory:
    """Live evidence as data: hook records per surface, process starts and
    parents, the registry and manager.json."""

    def __init__(self):
        self.rows, self.starts, self.parents, self.agents, self.managers = {}, {}, {}, {}, {}
        self.commands = {}
        self.down = False

    def sessions(self, surface):
        if self.down:
            raise binding.Unavailable("cmux down")
        return [dict(r) for r in self.rows.get(surface.upper(), [])]

    def start(self, pid):
        return self.starts.get(pid, "")

    def parent(self, pid):
        return self.parents.get(pid)

    def command(self, pid):
        return self.commands.get(pid, "")

    def registry(self):
        return self.agents

    def manager(self, project):
        return self.managers.get(project)

    # --- building the world -------------------------------------------------

    def process(self, pid, started=T0, parent=1):
        self.starts[pid] = lstart(started)
        self.parents[pid] = parent

    def hook(self, surface, pid, session, updated=None):
        self.rows.setdefault(surface.upper(), []).append(
            {"surface_id": surface, "pid": pid, "session_id": session, "active_for_surface": True,
             "updated_at_unix": updated if updated is not None else time.time()})

    def agent(self, name, surface, session, pid, role="manager", project=P, brand="claude", keys=None, **extra):
        self.commands[pid] = "codex" if brand == "codex" else "/opt/claude/versions/2.1.292"
        self.agents[name] = {"name": name, "project": project, "role": role, "status": "active",
                             "surface_id": surface, "keys": keys if keys is not None else [f"{brand}:{session}"],
                             "pid": pid, "pid_start": self.starts.get(pid), "brand": brand, **extra}

    def caller_inside(self, pid):
        """The caller (this test process) runs under pid, like a Bash tool
        command under its Claude Code process."""
        self.parents[os.getpid()] = pid


def manager_world():
    inv = FakeInventory()
    inv.process(100)
    inv.hook(MGR_S, 100, "sess-m1")
    inv.agent("bet-manager", MGR_S, "sess-m1", 100)
    inv.managers[P] = {"surface_id": MGR_S, "started_at": "2026-10-06T10:00:00Z"}
    inv.caller_inside(100)
    return inv


ENV = {"CMUX_SURFACE_ID": MGR_S}


class ResolveSelfTests(unittest.TestCase):
    def test_a_verified_manager(self):
        b, code = binding.resolve_self(P, "manager", env=ENV, inv=manager_world())
        self.assertEqual(code, "OK")
        self.assertEqual((b["name"], b["provider_session_id"], b["pid"], b["brand"], b["manager_generation"]),
                         ("bet-manager", "sess-m1", 100, "claude", "2026-10-06T10:00:00Z"))
        self.assertTrue(binding.valid(b))

    def code(self, inv, env=ENV, role="manager", project=P):
        return binding.resolve_self(project, role, env=env, inv=inv)[1]

    def test_every_refusal_has_its_own_code(self):
        cases = {}
        inv = manager_world()
        cases["NO_SURFACE"] = (inv, {})
        inv = manager_world()
        inv.down = True
        cases["INVENTORY_UNAVAILABLE"] = (inv, ENV)
        inv = manager_world()
        inv.rows = {}
        cases["NO_HOOK_RECORD"] = (inv, ENV)
        inv = manager_world()
        inv.parents[os.getpid()] = 999  # the caller is not inside the surface's process
        cases["NOT_ANCESTOR"] = (inv, ENV)
        inv = manager_world()
        inv.process(101, parent=100)
        inv.hook(MGR_S, 101, "sess-x")  # two processes in the caller's ancestry claim the surface
        inv.parents[os.getpid()] = 101
        cases["AMBIGUOUS_HOOK_RECORD"] = (inv, ENV)
        inv = manager_world()
        inv.rows[MGR_S][0]["updated_at_unix"] = T0 - 60  # written by an earlier process with this pid
        cases["STALE_HOOK_RECORD"] = (inv, ENV)
        inv = manager_world()
        inv.starts[100] = ""
        cases["PROCESS_GONE"] = (inv, ENV)
        inv = manager_world()
        inv.agents["bet-manager"]["pid_start"] = lstart(T0 - 86400)  # the registry knew an older process
        cases["PID_REUSED"] = (inv, ENV)
        inv = manager_world()
        inv.agents = {}
        cases["NO_REGISTRY_RECORD"] = (inv, ENV)
        inv = manager_world()
        inv.agents["bet-manager"]["keys"] = ["process:100:x"]
        cases["WEAK_RECORD"] = (inv, ENV)
        inv = manager_world()
        inv.agents["bet-manager"]["keys"] = ["claude:sess-old"]
        cases["SESSION_MISMATCH"] = (inv, ENV)
        inv = manager_world()
        inv.agent("bet-manager-2", MGR_S, "sess-m1", 100)
        cases["AMBIGUOUS_REGISTRY_RECORD"] = (inv, ENV)
        inv = manager_world()
        inv.agents["bet-manager"]["project"] = "gamma"
        cases["WRONG_PROJECT"] = (inv, ENV)
        inv = manager_world()
        inv.agents["bet-manager"]["role"] = "solver"
        cases["WRONG_ROLE"] = (inv, ENV)
        inv = manager_world()
        inv.managers = {}
        cases["NO_MANAGER_RECORD"] = (inv, ENV)
        inv = manager_world()
        inv.managers[P]["surface_id"] = CHILD_S
        cases["SURFACE_CHANGED"] = (inv, ENV)
        inv = manager_world()
        inv.rows[MGR_S][0]["active_for_surface"] = False  # cmux says another session is the tab's
        cases["INACTIVE_HOOK_RECORD"] = (inv, ENV)
        inv = manager_world()
        inv.commands[100] = "codex"  # the registry says claude; the process is codex
        cases["PROVIDER_MISMATCH"] = (inv, ENV)
        inv = manager_world()
        inv.rows[MGR_S][0]["workspace_id"] = "WS-A"
        inv.agents["bet-manager"]["workspace_id"] = "WS-B"
        cases["WORKSPACE_MISMATCH"] = (inv, ENV)
        inv = manager_world()

        def twice():
            raise binding.Ambiguous("key given twice")
        inv.registry = twice
        cases["AMBIGUOUS_EVIDENCE"] = (inv, ENV)
        for expected, (inv, env) in cases.items():
            with self.subTest(expected=expected):
                b, code = binding.resolve_self(P, "manager", env=env, inv=inv)
                self.assertEqual(code, expected)
                self.assertIsNone(b)
        self.assertEqual(set(cases) | {"OK", "SESSION_CHANGED", "GENERATION_CHANGED", "MALFORMED_BINDING",
                                       "HOOK_EVIDENCE_MISSING", "SURFACE_TAKEN", "MANAGER_MOVED"},
                         set(binding.CODES))

    def test_malformed_hook_rows_are_not_evidence(self):
        for field, bad in (("updated_at_unix", None), ("updated_at_unix", "now"), ("updated_at_unix", True),
                           ("updated_at_unix", float("nan")), ("updated_at_unix", float("inf")),
                           ("updated_at_unix", [1]), ("session_id", 7), ("session_id", None), ("pid", "100"),
                           ("pid", 0), ("pid", True)):
            with self.subTest(field=field, bad=bad):
                inv = manager_world()
                inv.rows[MGR_S][0][field] = bad
                self.assertEqual(self.code(inv), "NO_HOOK_RECORD")  # ignored, never sorted or trusted

    def test_a_registry_record_must_positively_match_pid_and_start(self):
        for change in ({"pid": None, "pid_start": None}, {"pid": None}, {"pid_start": None}, {"pid_start": " "},
                       {"pid": "100"}, {"keys": "claude:sess-m1"}, {"keys": [1, "claude:sess-m1"]}):
            with self.subTest(change=change):
                inv = manager_world()
                inv.agents["bet-manager"].update(change)
                self.assertEqual(self.code(inv), "WEAK_RECORD")
        inv = manager_world()
        inv.agents["bet-manager"]["keys"] = ["launch:t", "claude:sess-m1", "claude:sess-m2"]
        self.assertEqual(binding.original_session(inv.agents["bet-manager"]), "sess-m1")
        self.assertIsNone(binding.original_session({"keys": ["launch:t"]}))

    def test_a_cleared_session_is_the_newest_record_of_the_same_process(self):
        inv = manager_world()
        inv.rows[MGR_S][0]["updated_at_unix"] = time.time() - 30
        inv.hook(MGR_S, 100, "sess-m2")  # /clear: same pid, surface and manager.json; new session
        inv.agents["bet-manager"]["keys"].append("claude:sess-m2")
        b, code = binding.resolve_self(P, "manager", env=ENV, inv=inv)
        self.assertEqual((code, b["provider_session_id"]), ("OK", "sess-m2"))

    def test_two_sessions_tied_for_newest_are_ambiguous(self):
        inv = manager_world()
        t = inv.rows[MGR_S][0]["updated_at_unix"]
        inv.hook(MGR_S, 100, "sess-m2", updated=t)
        self.assertEqual(self.code(inv), "AMBIGUOUS_HOOK_RECORD")

    def test_a_child_of_any_role_and_observe_without_ancestry(self):
        inv = manager_world()
        inv.process(200, started=time.time() - 10)
        inv.hook(CHILD_S, 200, "sess-c1")
        inv.agent("bet-solver-3", CHILD_S, "sess-c1", 200, role="solver", brand="codex")
        b, code = binding.observe(CHILD_S, P, inv=inv)
        self.assertEqual((code, b["role"], b["brand"], b["manager_generation"]), ("OK", "solver", "codex", None))
        self.assertEqual(binding.resolve_self(P, None, env={"CMUX_SURFACE_ID": CHILD_S}, inv=inv)[1], "NOT_ANCESTOR")


class StrictEvidenceTests(unittest.TestCase):
    """The real Inventory reads with duplicate-key refusal. Its files are in a
    sandbox and its commands are fakes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=_isolation.HOME)
        self.root = Path(self.tmp.name)
        self.inv = binding.Inventory(chat_state=self.root / "chat", pm_state=self.root / "pm")
        (self.root / "chat").mkdir()
        (self.root / "pm" / P).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_keys_are_ambiguous_not_last_wins(self):
        (self.root / "chat" / "identities.json").write_text('{"agents": {"a": {"role": "solver", "role": "manager"}}}')
        with self.assertRaises(binding.Ambiguous):
            self.inv.registry()
        (self.root / "pm" / P / "manager.json").write_text('{"surface_id": "A", "surface_id": "B"}')
        with self.assertRaises(binding.Ambiguous):
            self.inv.manager(P)
        out = '{"sessions": [{"pid": 1, "pid": 2}]}'
        with mock.patch.object(binding.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, out, "")):
            with self.assertRaises(binding.Ambiguous):
                self.inv.sessions(MGR_S)

    def test_missing_unreadable_and_wrong_shapes(self):
        self.assertEqual(self.inv.registry(), {})
        (self.root / "chat" / "identities.json").write_text(json.dumps({"agents": []}))
        with self.assertRaises(binding.Unavailable):
            self.inv.registry()
        self.assertIsNone(self.inv.manager(P))
        for code, out in ((1, ""), (0, "nope"), (0, '{"x": 1}')):
            with mock.patch.object(binding.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], code, out, "")):
                with self.assertRaises(binding.Unavailable):
                    self.inv.sessions(MGR_S)

    def test_process_names_decide_the_provider(self):
        self.assertEqual([binding.brand_of(c) for c in ("codex", "/x/2.1.292", "claude", "node", "", None)],
                         ["codex", "claude", "claude", None, None, None])
        # full-width names as macOS ps -ww prints them; a truncated name decides nothing
        self.assertEqual(binding.brand_of("/Users/someone/.local/share/claude/versions/2.1.292\n"), "claude")
        self.assertEqual(binding.brand_of("/opt/homebrew/bin/codex"), "codex")
        self.assertIsNone(binding.brand_of("/Users/someone/."))

    def test_the_process_name_is_read_at_full_width(self):
        calls = []
        with mock.patch.object(binding.subprocess, "run", lambda cmd, **k: calls.append(cmd) or
                               subprocess.CompletedProcess(cmd, 0, "/Users/someone/.local/share/claude/versions/2.1.292\n", "")):
            self.assertEqual(binding.brand_of(self.inv.command(4242)), "claude")
        self.assertEqual(calls, [["ps", "-ww", "-o", "comm=", "-p", "4242"]])


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.inv = manager_world()
        self.b, code = binding.resolve_self(P, "manager", env=ENV, inv=self.inv)
        self.assertEqual(code, "OK")

    def test_still_the_same_session(self):
        self.assertEqual(binding.verify(self.b, inv=self.inv), "OK")
        self.assertTrue(binding.same_session(self.b, dict(self.b, verified_at="later")))

    def test_clear_restart_reuse_and_moves(self):
        inv = manager_world()
        inv.rows[MGR_S.upper()][0]["updated_at_unix"] = time.time() - 30  # a tie would be ambiguous
        inv.hook(MGR_S, 100, "sess-m2")  # /clear
        inv.agents["bet-manager"]["keys"].append("claude:sess-m2")
        self.assertEqual(binding.verify(self.b, inv=inv), "SESSION_CHANGED")
        inv = manager_world()
        inv.starts[100] = ""  # the process exited
        self.assertEqual(binding.verify(self.b, inv=inv), "PROCESS_GONE")
        inv = manager_world()
        inv.process(100, started=T0 + 600)  # a new process got the same pid
        self.assertEqual(binding.verify(self.b, inv=inv), "PID_REUSED")
        inv = manager_world()
        inv.process(101, started=time.time() - 10)
        inv.rows = {MGR_S.upper(): []}
        inv.hook(MGR_S, 101, "sess-x")  # another live, fresh process owns the tab now
        self.assertEqual(binding.verify(self.b, inv=inv), "SURFACE_TAKEN")
        inv = manager_world()
        inv.managers[P]["started_at"] = "2026-10-06T11:00:00Z"  # restarted in place, same surface
        self.assertEqual(binding.verify(self.b, inv=inv), "GENERATION_CHANGED")
        inv = manager_world()
        inv.managers[P]["surface_id"] = CHILD_S  # manager.json names another surface
        self.assertEqual(binding.verify(self.b, inv=inv), "MANAGER_MOVED")
        self.assertTrue({"SESSION_CHANGED", "PROCESS_GONE", "PID_REUSED", "SURFACE_TAKEN", "GENERATION_CHANGED",
                         "MANAGER_MOVED"} == set(binding.GONE))

    def test_missing_evidence_is_unknown_never_gone(self):
        """The coordinator's probe: the hook record is gone but the process is alive."""
        inv = manager_world()
        inv.rows = {MGR_S.upper(): []}
        self.assertEqual(binding.verify(self.b, inv=inv), "HOOK_EVIDENCE_MISSING")
        inv = manager_world()
        inv.rows = {MGR_S.upper(): []}
        inv.hook(CHILD_S, 100, "sess-m1")  # the same process reports another surface: still not exit
        self.assertEqual(binding.verify(self.b, inv=inv), "HOOK_EVIDENCE_MISSING")
        inv = manager_world()
        inv.process(101, started=time.time() - 10)
        inv.rows = {MGR_S.upper(): []}
        inv.hook(MGR_S, 101, "sess-x", updated=T0 - 9999)  # another process, but its record is stale
        self.assertEqual(binding.verify(self.b, inv=inv), "HOOK_EVIDENCE_MISSING")
        inv = manager_world()
        inv.managers = {}
        self.assertEqual(binding.verify(self.b, inv=inv), "NO_MANAGER_RECORD")
        inv = manager_world()
        inv.agents["bet-manager"].update(pid=None, pid_start=None)
        self.assertEqual(binding.verify(self.b, inv=inv), "WEAK_RECORD")
        for code in ("HOOK_EVIDENCE_MISSING", "NO_MANAGER_RECORD", "WEAK_RECORD", "INVENTORY_UNAVAILABLE",
                     "AMBIGUOUS_EVIDENCE", "MALFORMED_BINDING", "SESSION_MISMATCH", "NO_REGISTRY_RECORD"):
            self.assertNotIn(code, binding.GONE)
        inv = manager_world()
        inv.agents["bet-manager"]["status"] = "retired"  # registry drift on a live, unchanged session
        self.assertEqual(binding.verify(self.b, inv=inv), "NO_REGISTRY_RECORD")
        inv = manager_world()
        inv.agents["bet-manager"]["keys"] = ["process:100:x"]
        self.assertEqual(binding.verify(self.b, inv=inv), "WEAK_RECORD")
        inv = manager_world()
        inv.down = True
        self.assertEqual(binding.verify(self.b, inv=inv), "INVENTORY_UNAVAILABLE")

    def test_a_malformed_binding_never_verifies(self):
        for broken in (None, {}, dict(self.b, pid="100"), dict(self.b, provider_session_id=""),
                       {k: v for k, v in self.b.items() if k != "pid_start"}):
            with self.subTest(broken=broken):
                self.assertEqual(binding.verify(broken, inv=self.inv), "MALFORMED_BINDING")
                self.assertFalse(binding.same_session(broken, self.b))


if __name__ == "__main__":
    unittest.main()
