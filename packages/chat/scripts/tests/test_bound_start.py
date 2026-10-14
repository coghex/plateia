"""The bridge's bound start under writer admission (#21's owner amendment 4, sections 3 and 4; its APPROVE's
clarification K3; docs/writer_admission.md 6.3 and design test 9): the authority's start is admitted only under a
`full` maintenance record whose binding is this launch's operation, bootstrap digest and release. That start is
one operation-owned section: L_adm is held shared from the admission through the bootstrap's classification, the
start, the socket's binding and a failed start's cleanup. It then serves read-only status, answers every mutation
`maintenance`, and makes no chat connection, import or flush. Anything else holds, writing nothing.

Invented records, accounts, operations, boots and releases only; no socket is bound and nothing is live."""
import fcntl
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from admission_fakes import HeldCase  # noqa: E402
from test_outbox_bridge import OPERATION, RELEASE_ID, BootstrapCase  # noqa: E402

OTHER_OPERATION = "activate-9f8e7d6c5b4a3921"
DATABASE = ("outbox.db", "outbox.db-wal", "outbox.db-shm", "outbox.sock", "outbox.authority.lock")


class Stop(BaseException):
    pass


class BoundStartCase(BootstrapCase, HeldCase):
    def setUp(self):
        super().setUp()
        self.bridge._HOLD_LOGGED[0] = None
        self.addCleanup(self.bridge._HOLD_LOGGED.__setitem__, 0, None)
        self.again()

    def again(self):
        """A clean state for the next case: the record removed first (closing an authority while maintenance is
        held is deferred, by design), then the authority closed and its files removed."""
        self.paths.maintenance.unlink(missing_ok=True)
        self.stop()
        self.wipe_database()
        for name in DATABASE:
            (self.state / name).unlink(missing_ok=True)
        self.journal.__init__()

    def hold_full(self, digest, operation=OPERATION, release=RELEASE_ID, state="full"):
        """The invented operation's maintenance, through Owner, up to `state`; in `full` bound to `digest`."""
        owner = self.owner(operation=operation)
        owner.acquire()
        self.assertTrue(owner.drain()["drained"])
        if state == "full":
            owner.advance("full", legacy="retired-and-inhibited", boot="boot-B",
                          binding={"release_id": release, "bootstrap_digest": f"sha256:{digest}",
                                   "permits": ["bridge-start"]})
        return owner

    def exclusive_now(self):
        """Whether an owner could take L_adm exclusive at this instant (one non-blocking try)."""
        fd = os.open(self.paths.admission_lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)

    def one_pass_of_main(self, digest):
        """main() with this launch digest, for one pass of its loop: it stops at its first sleep. Returns what
        it slept, whether it ran the bridge (a chat connection), and the authority it left."""
        slept, ran = [], []

        def sleep(seconds):
            slept.append(seconds)
            raise Stop()

        def run(*a, **k):
            ran.append(a)
            raise Stop()
        with mock.patch.object(self.bridge.time, "sleep", sleep), mock.patch.object(self.bridge, "run", run), \
                mock.patch.object(self.chatlib, "login", side_effect=AssertionError("connected")), \
                mock.patch.object(self.bridge.chatlib, "load_config", lambda: {}), self.assertRaises(Stop):
            self.bridge.main(["--activation-bootstrap", f"sha256:{digest}"])
        return slept, ran, self.bridge.OUTBOX_AUTHORITY

    def nothing_written(self):
        for name in DATABASE + ("outbox-status.json",):
            self.assertFalse((self.state / name).exists(), name)

    def committed(self, authority):
        return authority.store.meta("designation_bootstrap")

    def request(self, authority, op, **args):
        client = self.ds.Client(self.ds.LocalTransport(authority), clock=self.clock)
        writer = self.writer()
        return client.call(op, writer, **args)


class HeldStartTests(BoundStartCase):
    """In `partial`, and under a `full` record that is not this launch's, the bridge holds and writes nothing."""

    def test_in_partial_the_launched_bridge_holds_and_creates_no_database_wal_or_socket(self):
        d = self.publish(self.record(["alp-solver-2"]))
        self.hold_full(d, state="partial")
        with mock.patch.object(self.bridge, "ensure_authority", side_effect=AssertionError("started")):
            slept, ran, _ = self.one_pass_of_main(d)
        self.assertEqual((slept, ran), ([self.ds.HOLD_RECHECK], []))
        self.nothing_written()

    def test_a_wrong_digest_another_operation_or_another_release_holds_without_writing(self):
        for variant in ("a wrong digest", "another operation", "another release"):
            with self.subTest(variant=variant):
                self.again()
                d = self.publish(self.record(["alp-solver-2"]))
                self.hold_full("0" * 64 if variant == "a wrong digest" else d,
                               operation=OTHER_OPERATION if variant == "another operation" else OPERATION,
                               release="plateia-chat-9.9.9+ginvented" if variant == "another release" else RELEASE_ID)
                record = self.paths.maintenance.read_bytes()
                slept, ran, authority = self.one_pass_of_main(d)
                self.assertEqual((slept, ran), ([self.ds.HOLD_RECHECK], []))
                self.nothing_written()
                held = self.launch(d)  # asked directly, the start is refused too
                self.assertFalse(held.running)
                self.assertIn("the authority does not start", held.unavailable[0])
                self.assertIsNone(self.bridge.BOOTSTRAP_UNKNOWN, "held, not UNKNOWN")
                self.nothing_written()
                self.assertEqual(self.paths.maintenance.read_bytes(), record, "the bridge never writes the record")


class FullStartTests(BoundStartCase):
    """In `full` with this launch's binding: the bootstrap commits once, status answers with the record and the
    epoch, every mutation is answered `maintenance`, and nothing else runs; a restart replays."""

    def test_the_bound_start_commits_once_serves_status_and_refuses_every_mutation(self):
        d = self.publish(self.record(["alp-solver-2", "sam"]))
        self.hold_full(d)
        slept, ran, authority = self.one_pass_of_main(d)
        self.assertEqual((slept, ran), ([self.ds.HOLD_RECHECK], []), "no chat connection, import or flush")
        self.assert_running_under_the_lock(authority)
        committed = json.loads(self.committed(authority))
        self.assertEqual((committed["operation"], committed["digest"], committed["accounts"]),
                         (OPERATION, d, ["alp-solver-2", "sam"]))
        status = self.request(authority, "status")
        self.assertEqual((status["result"], status["authority_epoch"]), ("ok", authority.epoch))
        self.assertEqual(status["status"]["maintenance"]["state"], "full")
        self.assertEqual(status["status"]["maintenance"]["operation"], OPERATION)
        created = self.request(authority, "create", **self.create_args("e-invented-1"))
        self.assertEqual(created["result"], "maintenance")
        self.assertEqual(authority.store.con.execute("SELECT count(*) FROM entries").fetchone()[0], 0,
                         "nothing was committed for the mutation")
        self.assertFalse((self.state / "outbox-status.json").exists(), "the status snapshot is deferred")
        self.paths.maintenance.unlink()  # the test's own removal, so that closing is not deferred
        meta, accounts = BootstrapCase.rows(self)  # closes it
        self.hold_full(d)  # the same operation's full record again
        with mock.patch.object(self.ds.Store, "designate", side_effect=AssertionError("designated again")):
            again = self.launch(d)
        self.assert_running_under_the_lock(again)
        self.assertEqual(self.committed(again), meta["designation_bootstrap"], "a restart is the replay (S3)")

    def test_the_start_holds_l_adm_shared_through_classification_start_bind_and_cleanup(self):
        d = self.publish(self.record(["alp-solver-2"]))
        self.hold_full(d)
        seen = {}
        real_read, real_close = self.bridge.read_bootstrap, self.ds.Store.close

        def read_bootstrap(fd):
            seen["classification"] = self.exclusive_now()
            return real_read(fd)

        def bind(authority):
            seen["bind"] = self.exclusive_now()
            raise OSError("invented: the socket can't be bound")

        def close(store):
            seen.setdefault("cleanup", self.exclusive_now())
            return real_close(store)
        with mock.patch.object(self.bridge, "read_bootstrap", read_bootstrap), \
                mock.patch.object(self.ds.Authority, "bind", bind), mock.patch.object(self.ds.Store, "close", close):
            authority = self.launch(d)
        self.assertEqual(seen, {"classification": False, "bind": False, "cleanup": False},
                         "no owner could take L_adm exclusive during any of them")
        self.assertFalse(authority.running)
        self.assertTrue(self.exclusive_now(), "the section ended with the start")


class FailedStartTests(BoundStartCase):
    """A start that fails under `full` (its socket, or its thread) cleans up inside its own section, so its files
    are released at once, and the next pass starts again. A close outside any section stays deferred."""

    def test_socket_and_thread_start_failure_under_full_are_released_and_retried(self):
        for failing in ("bind", "run_in_thread"):
            with self.subTest(failing=failing):
                self.again()
                d = self.publish(self.record(["alp-solver-2"]))
                self.hold_full(d)

                def fail(authority):
                    raise OSError(f"invented: {failing} failed")
                with mock.patch.object(self.ds.Authority, failing, fail):
                    authority = self.launch(d)
                self.assertFalse(authority.running)
                self.assertTrue(authority.released, "released inside the start's section, not deferred")
                self.assertTrue(self.lock_free())
                self.assertFalse((self.state / "outbox-status.json").exists())
                retried = self.bridge.ensure_authority()  # the next pass
                self.assert_running_under_the_lock(retried)
                self.assertIsNotNone(self.committed(retried))

    def test_a_close_outside_any_section_is_deferred_under_full_until_the_release(self):
        d = self.publish(self.record(["alp-solver-2"]))
        owner = self.hold_full(d)
        authority = self.launch(d)
        self.assert_running_under_the_lock(authority)
        authority.close()
        self.assertFalse(authority.released, "the database and the lock are kept while maintenance is held")
        self.assertFalse(self.lock_free())
        self.assertIs(self.bridge.ensure_authority(), authority, "not dropped, not restarted")
        owner.release()
        authority.close()
        self.assertTrue(authority.released)
        self.assertTrue(self.lock_free())


class ReleaseTests(BoundStartCase):
    """After the release, the bridge's next pass leaves its hold: the running authority is kept and the bridge
    connects (ordinary sections resume)."""

    def test_after_the_release_the_bound_authority_is_kept_and_the_bridge_connects(self):
        d = self.publish(self.record(["alp-solver-2"]))
        owner = self.hold_full(d)
        _, _, authority = self.one_pass_of_main(d)
        owner.release()
        slept, ran, again = self.one_pass_of_main(d)
        self.assertIs(again, authority)
        self.assertEqual((slept, len(ran)), ([], 1), "it connects")
        self.assertEqual(self.request(authority, "create", **self.create_args("e-invented-2"))["result"], "ok",
                         "mutations are admitted again")


if __name__ == "__main__":
    unittest.main()
