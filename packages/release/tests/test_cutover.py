"""A8's end-to-end cutover test (PLT-14, #21; D-74): #19's real release,
activated in an invented home, then driven through its installed commands
and its bridge authority.

The driver runs in the staged release's own interpreter, with -I: it imports
only the installed plateia_chat package (never the source checkout) and loads
the outbox tests' invented chat server and process table by path. No socket,
chat server, service, real home or real state is used: the chat server is
#19's FakeServer, clients reach the authority in-process (LocalTransport), and
the bridge's authority starts through chat-bridge's own start_authority with
the launch digest the activation bound."""
import json
import os
import plistlib
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import _staging
import _support

sys.path.insert(0, str(_support.RELEASE))
import activate_release  # noqa: E402
import stage_release  # noqa: E402
from build_release import Refused  # noqa: E402
from test_activate import ActivationCase, BRIDGE_PLIST  # noqa: E402

FAKES = _support.CHECKOUT / "packages/chat/scripts/tests/outbox_fakes.py"

DRIVER = textwrap.dedent('''
    import contextlib, importlib.machinery, importlib.util, io, json, os, sys, time
    from unittest import mock

    fakes_path, out_path, digest = sys.argv[1:4]
    import plateia_chat
    sys.path.insert(0, str(plateia_chat.SCRIPTS))  # as each installed command does itself
    import chatlib, delivery_store, agentcli
    bridge = plateia_chat.load("chat-bridge")
    pchat = plateia_chat.load("pchat", "pchat_cli")
    loader = importlib.machinery.SourceFileLoader("outbox_fakes_cutover", fakes_path)
    fakes = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(fakes)

    out = {"files": {m: sys.modules[m].__file__ for m in ("chatlib", "delivery_store", "agentcli", "identities",
                                                          "runstore")},
           "bridge_file": bridge.__file__, "running_release": bridge.running_release(), "sys_path": sys.path,
           "steps": {}}
    clock = fakes.Clock(time.time())
    server = fakes.FakeServer(clock)
    probes = delivery_store.FakeProbes(pid=5151)
    cfg = dict(fakes.CFG)
    live = {"authority": None}

    def client():
        a = live["authority"]
        return delivery_store.Client(delivery_store.LocalTransport(a) if a is not None and a.running else None,
                                     clock=clock)

    patches = [mock.patch.object(chatlib, "login", lambda *a, **k: server.login(*a, **k)),
               mock.patch.object(chatlib, "_wallclock", clock), mock.patch.object(chatlib, "_mono", clock.mono),
               mock.patch.object(chatlib, "_sleep", clock.advance),
               mock.patch.object(chatlib, "PART_WAIT", 0.05, create=True),
               mock.patch.object(chatlib, "silence_refusal", lambda account: None),
               mock.patch.object(chatlib, "load_config", lambda: cfg),
               mock.patch.object(chatlib, "client_factory", client),
               mock.patch.object(delivery_store, "PROBES", probes.view(4242)),
               mock.patch.object(delivery_store.Authority, "bind", lambda a: False),
               mock.patch.object(delivery_store.Authority, "run_in_thread", lambda a: None),
               mock.patch.object(bridge, "_now", clock)]
    for p in patches:
        p.start()

    def run(*argv, account="alp-solver-2"):
        text = io.StringIO()
        with mock.patch.object(pchat, "who", lambda args, cfg, required=True: account), \\
                mock.patch.object(pchat.identities, "remember", lambda *a, **k: None), \\
                mock.patch.object(pchat.runstore, "silent_refusal", lambda *a: None), \\
                contextlib.redirect_stdout(text), contextlib.redirect_stderr(text):
            code = pchat.main(list(argv))
        return code, text.getvalue()

    def start():
        a = delivery_store.Authority(chatlib.STATE_DIR, clock=clock, mono=clock.mono, probes=probes)
        bridge.start_authority(a, digest)
        live["authority"] = bridge.OUTBOX_AUTHORITY = a
        return a

    def flush(times=1):
        for _ in range(times):
            clock.advance(60)
            bridge.flush_outbox(cfg, authority=live["authority"], deliveries=bridge.Deliveries())

    def texts():
        return [m["text"] for m in server.published if m["text"]]

    def database(a):
        return a.run_tx(lambda s: {"meta": dict(s.con.execute("SELECT name, value FROM meta").fetchall()),
                                   "accounts": sorted(r[0] for r in s.con.execute(
                                       "SELECT account FROM accounts WHERE designated_at IS NOT NULL"))})

    steps = out["steps"]
    # 1. The bridge is down: a post and a notice are queued durably, and status says so.
    steps["post_down"] = run("post", "#alpha", "[status] queued while the bridge is down")
    with contextlib.redirect_stderr(io.StringIO()):
        agentcli.notify({"name": "alp-solver-2", "channel": "#alpha"}, "a notice while the bridge is down")
    steps["fallback_files"] = sorted(n for n in os.listdir(chatlib.STATE_DIR / "outbox.d")
                                     if delivery_store.is_published_name(n))
    steps["status_down"] = run("status")
    steps["published_down"] = texts()
    # 2. The new bridge starts under the activation: the bootstrap, then import and delivery.
    a = start()
    steps["first_start"] = database(a)
    flush(2)
    steps["published_after_import"] = texts()
    # 3. With the bridge up: post, ack and notify, through the authority.
    steps["post_up"] = run("post", "#alpha", "[status] posted with the bridge up")
    steps["ack_up"] = run("ack", "#alpha", "srv1")
    agentcli.notify({"name": "alp-solver-2", "channel": "#alpha"}, "a notice with the bridge up")
    steps["tagmsgs"] = [w for _, w in server.writes if "TAGMSG" in w]
    # 4. No whole-post replay: a long post whose last confirmation times out is never posted again.
    server.stall_after = len(server.published) + 3
    steps["long"] = run("post", "#alpha", fakes.LONG)
    committed = len(server.published)
    flush(3)
    steps["long_committed"], steps["long_after_flushes"] = committed, len(server.published)
    server.stall_after = None
    flush(1)
    steps["status_up"] = run("status")
    # 5. A restart: the replay changes nothing, and nothing is posted again.
    before = len(server.published)
    live["authority"].close()
    a = start()
    steps["restart"] = database(a)
    flush(2)
    steps["published_after_restart"] = len(server.published) - before
    steps["live_status"] = delivery_store.Client(delivery_store.LocalTransport(a), clock=clock).call("status", None)
    steps["published"] = texts()
    live["authority"].close()
    with open(out_path, "w") as f:
        json.dump(out, f)
''')


class CutoverTests(ActivationCase):
    """A8: an invented end-to-end activation, through the installed commands
    and the bridge's authority."""

    def drive(self, plan):
        python = Path(plan["staged_release"]) / "env/bin/python"
        driver = self.where / "driver.py"
        driver.write_text(DRIVER)
        out = self.where / "driver-out.json"
        d = activate_release.sha256_bytes(activate_release.bootstrap_bytes(plan))
        env = {"HOME": str(self.home), "PATH": f"{self.fake_bin}{os.pathsep}/usr/bin:/bin", "LANG": "C.UTF-8"}
        done = subprocess.run([str(python), "-I", "-B", "-W", "error::ResourceWarning", str(driver), str(FAKES),
                               str(out), d], capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(out.read_text()), d

    def test_an_invented_cutover_end_to_end(self):
        legacy = {"channel": "#alpha", "as": "alp-solver-2", "text": "[status] a legacy row from before the cutover",
                  "at": "2026-10-09T00:00:00"}
        with open(self.state / "outbox.jsonl", "a") as f:
            f.write(json.dumps(legacy) + "\n")
        self.before = _staging.snapshot_tree(self.home)
        plan, path = self.activated(designations=["alp-solver-2"])
        rel = Path(plan["staged_release"])

        # The bound commands run the pinned release: the pchat link and the handoff layer.
        env = {"HOME": str(self.home), "PATH": f"{self.fake_bin}{os.pathsep}/usr/bin:/bin"}
        for argv in ([str(self.home / ".local/bin/pchat"), "--help"],
                     [sys.executable, str(self.skills / "chat/scripts/pchat"), "--help"]):
            done = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("usage: pchat", done.stdout)
        bound = plistlib.loads((self.home / BRIDGE_PLIST).read_bytes())["ProgramArguments"]
        self.assertTrue(Path(bound[0]).is_relative_to(rel))

        out, d = self.drive(plan)
        self.assertEqual(bound[2], f"sha256:{d}")
        for name, f in out["files"].items():
            self.assertTrue(Path(f).is_relative_to(rel), f"{name} loads from the release: {f}")
        self.assertTrue(Path(out["bridge_file"]).is_relative_to(rel))
        self.assertEqual(out["running_release"], plan["release"])
        self.assertFalse(any(p.startswith(str(_support.CHECKOUT)) for p in out["sys_path"]),
                         "no source checkout on the import path")
        steps = out["steps"]

        # The bridge down: a durable queue, and status says what waits.
        self.assertEqual(steps["post_down"][0], 3, steps["post_down"][1])
        self.assertEqual(len(steps["fallback_files"]), 2, "the post and the notice, each one outbox/2 file")
        self.assertEqual(steps["published_down"], [])
        self.assertIn("2 fallback files", steps["status_down"][1])

        # The first start: the plan-bound bootstrap, committed before anything was imported.
        committed = json.loads(steps["first_start"]["meta"]["designation_bootstrap"])
        self.assertEqual((committed["operation"], committed["digest"]), (plan["operation"], d))
        self.assertEqual(steps["first_start"]["accounts"], ["alp-solver-2"], "an unnamed account stays undesignated")

        # Import and delivery: the queued post, the notice and the legacy row, each once.
        published = steps["published"]
        for text in ("[status] queued while the bridge is down", "a notice while the bridge is down",
                     "[status] a legacy row from before the cutover"):
            self.assertEqual(sum(1 for t in published if text in t), 1, text)
        self.assertNotIn("an invented queued message", " ".join(published), "an unsupported row is held, not posted")
        self.assertIn("1 held rows", steps["status_up"][1])

        # The bridge up: post, ack and notify.
        self.assertEqual(steps["post_up"][0], 0, steps["post_up"][1])
        self.assertEqual(steps["ack_up"][0], 0, steps["ack_up"][1])
        self.assertTrue(any("+draft/react=ack" in w for w in steps["tagmsgs"]))
        self.assertEqual(sum(1 for t in published if "a notice with the bridge up" in t), 1)

        # No whole-post replay.
        self.assertEqual(steps["long"][0], 3, steps["long"][1])
        self.assertEqual(steps["long_after_flushes"], steps["long_committed"],
                         "no part the server committed is posted again")

        # A restart is the replay: the original bootstrap stands, and nothing is posted again.
        self.assertEqual(steps["restart"]["meta"]["designation_bootstrap"],
                         steps["first_start"]["meta"]["designation_bootstrap"])
        self.assertEqual(steps["published_after_restart"], 0)

        # After the first new-format writes, rollback is refused; the post-check passes on the new bridge.
        with self.assertRaisesRegex(Refused, "repair or roll-forward"):
            self.rollback(path)
        self.ops.bridge = {"running": True, "release": plan["release"]}
        self.assertFalse(self.postcheck(path)["ok"], "no live answer: nothing proves the authority serves")
        self.ops.live = steps["live_status"]  # the real authority's answer to the read-only status request
        report = self.postcheck(path)
        self.assertTrue(report["ok"], report)
        self.assertEqual({k for k, v in report["first_writes"].items() if v.startswith("written")},
                         {"outbox-db/1", "outbox/2"})
        calls = self.fake_log.read_text().splitlines() if self.fake_log.exists() else []
        self.assertTrue(all(c.startswith("launchctl print ") for c in calls),
                        f"only pchat status's read-only service query, answered by the fake: {calls}")


if __name__ == "__main__":
    unittest.main()
