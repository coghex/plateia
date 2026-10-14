"""The request store (COLLAB-2, #32; docs/collaboration_foundations_design.md sections 1, 3 and 4, D-8 and D-15):
request/v1 requests and their claims in requests.db, under the outbox authority. Each of #32's acceptance signals
is a test here, with its owner amendments' additions: registration, repeats and conflicting reuse, supersession,
verified acceptance, fenced claims, starts and reconciliation, recovery across a restart, refusals recorded, the
foreign-file and pinned-root rules, writer admission on both transports and the bound start.

Invented requests, accounts, rooms, posts and operations only; no socket is bound and nothing is live."""
import hashlib
import os
import shutil
import sqlite3
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from admission_fakes import HeldCase  # noqa: E402
from outbox_fakes import BRIDGE_PID, CLIENT_PID, OTHER_PID  # noqa: E402
from test_bound_start import BoundStartCase  # noqa: E402
from test_outbox_bridge import BootstrapCase  # noqa: E402

POLICY = "manager-policy:" + "a" * 64
VERIFIED = {"status": "verified", "reasons": [], "authority": "srv-relay-1"}
UNRESOLVED = {"status": "unresolved", "reasons": ["assistants-unavailable"], "authority": None}
REFUSED = {"status": "refused", "reasons": ["sender-label-only"], "authority": None}
SOLVER, OTHER_SOLVER, MANAGER = "alp-solver-2", "bet-solver-1", "alp-manager"
STORE_FILES = ("requests.db", "requests.db-wal", "requests.db-shm", "requests.db-journal")


def line(n=1, rev=1, scope="alpha", relay=None):
    """A canonical request/v1 line, invented; `relay` is not part of the digest, so a relay keeps it."""
    pieces = [f"[request alpha-20261014-{n}]", "request/v1", f"op=move-{n}", f"rev={rev}", "act=review",
              "tgt=pr-7", f"scope={scope}", f"policy={POLICY}"]
    return " ".join(pieces + ([f"relay={relay}"] if relay else []))


def rid(n=1):
    return f"alpha-20261014-{n}"


class RequestCase(HeldCase):
    """An outbox case whose authority also holds the request store, reached on both transports."""

    def op(self, op, bridge=False, **args):
        client = self.ds.Client(self.ds.LocalTransport(self.authority, bridge=bridge), clock=self.clock)
        return client.request(op, self.writer(BRIDGE_PID if bridge else CLIENT_PID), **args)

    def register(self, n=1, rev=1, agent="sam", bridge=False, **over):
        args = dict(agent=agent, line=line(n, rev), verdict=VERIFIED, room="#alpha", decision_maker=MANAGER,
                    claimants=[SOLVER, OTHER_SOLVER], successors=["pat"])
        args.update(over)
        return self.op("register", bridge=bridge, **args)

    def lookup(self, n=1, rev=None, bridge=False):
        return self.op("lookup", bridge=bridge, req_id=rid(n), **({} if rev is None else {"rev": rev}))

    def key(self, n=1, rev=1):
        keys = self.lookup(n, rev)["keys"]
        return keys[0] if keys else None

    def claim(self, agent=SOLVER, n=1, rev=1, bridge=False):
        return self.op("claim", bridge=bridge, req_id=rid(n), rev=rev, agent=agent)

    def start(self, claimed, agent=SOLVER, n=1, rev=1, bridge=False, **over):
        args = dict(req_id=rid(n), rev=rev, agent=agent, generation=claimed["key"]["generation"],
                    fence=claimed["fence"])
        args.update(over)
        return self.op("start", bridge=bridge, **args)

    def reconcile(self, outcome, generation, agent=SOLVER, n=1, rev=1, bridge=False):
        return self.op("reconcile", bridge=bridge, req_id=rid(n), rev=rev, agent=agent, generation=generation,
                       outcome=outcome, evidence="invented: the target shows no review")

    def accept(self, msgid, n=1, rev=1, bridge=False):
        return self.op("accept", bridge=bridge, req_id=rid(n), rev=rev, msgid=msgid)

    def post(self, msgid, text, account=MANAGER, at=None):
        """A verified post the bridge indexed (V1), in outbox.db's messages table."""
        self.tx(lambda s: s.index(msgid, "#alpha", account, text, self.clock() if at is None else at))

    def refusals(self):
        return [r[0] for r in self.authority.requests.con.execute("SELECT reason FROM refusals ORDER BY id")]

    def store_files(self):
        """Every requests.db file and classification copy in the state directory, by name and sha256."""
        state = self.chatlib.STATE_DIR
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
                for p in state.iterdir() if p.name.startswith(("requests.db", ".request-store"))}

    def assertRefused(self, reply, reason, result="refused"):
        self.assertEqual(reply["result"], result, reply)
        self.assertEqual(reply["refusal"]["reason"], reason, reply)
        self.assertTrue(reply["refusal"]["recorded"])


class RegistrationTests(RequestCase):
    """Atomic registration with the room bound; repeats answered from the record; conflicting reuse refused."""

    def test_registration_binds_the_room_actors_and_verdict_in_one_record(self):
        reply = self.register()
        self.assertEqual(reply["result"], "ok")
        key = reply["key"]
        self.assertEqual((key["request_id"], key["rev"], key["state"], key["generation"]), (rid(), 1, "open", 0))
        self.assertEqual(key["digest"], self.ds.request_store.request_v1.digest(
            self.ds.request_store.request_v1.parse(line())))
        self.assertEqual((key["room"], key["decision_maker"], key["claimants"], key["successors"], key["registrant"]),
                         ("#alpha", MANAGER, [SOLVER, OTHER_SOLVER], ["pat"], "sam"))
        self.assertEqual(key["verdict"], VERIFIED)

    def test_acceptance_1_a_repost_or_relay_produces_no_second_claim(self):
        self.register()
        claimed = self.claim()
        self.assertEqual(claimed["result"], "ok")
        for again in (self.register(), self.register(line=line(relay="sam"), agent="sam"),
                      self.register(bridge=True, room="#beta")):
            with self.subTest(again=again["key"]["room"]):
                self.assertEqual(again["result"], "replay", "answered from the record, never registered again")
                self.assertEqual(again["key"]["room"], "#alpha", "the record's room, not the copy's")
        second = self.claim(OTHER_SOLVER)
        self.assertRefused(second, "already-claimed", "conflict")
        self.assertEqual(second["key"]["claim"]["owner"], SOLVER, "the existing claim is returned")
        self.assertNotIn("fence", second, "another claimant never gets the fence token")
        self.assertEqual(self.key()["generation"], 1)
        self.assertEqual(self.authority.requests.con.execute("SELECT count(*) FROM claims").fetchone()[0], 1)

    def test_acceptance_2_concurrent_registrations_converge_on_one_record(self):
        self.authority.run_in_thread()
        rooms = [f"#room-{i}" for i in range(8)]
        barrier, replies = threading.Barrier(len(rooms)), {}

        def register(room):
            barrier.wait()
            replies[room] = self.register(room=room, bridge=True)  # each queued to the one authority thread
        threads = [threading.Thread(target=register, args=(room,)) for room in rooms]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        results = sorted(r["result"] for r in replies.values())
        self.assertEqual(results, ["ok"] + ["replay"] * 7)
        winner = next(room for room, r in replies.items() if r["result"] == "ok")
        self.assertEqual({r["key"]["room"] for r in replies.values()}, {winner}, "every one gets the winner's record")
        self.assertEqual(self.authority.requests.con.execute("SELECT count(*) FROM requests").fetchone()[0], 1)

    def test_acceptance_5_a_conflicting_reuse_is_refused_with_the_record_unchanged(self):
        before = self.register()["key"]
        conflict = self.register(line=line(scope="alpha,beta"))
        self.assertRefused(conflict, "conflicting-reuse", "conflict")
        self.assertEqual(conflict["key"], before)
        self.assertEqual(self.key(), before)
        self.assertEqual(self.refusals(), ["conflicting-reuse"])

    def test_a_malformed_line_is_refused_and_nothing_is_recorded(self):
        reply = self.register(line="[request alpha-20261014-1] request/v1 op=x")
        self.assertEqual(reply["result"], "refused")
        self.assertNotIn("refusal", reply)
        self.assertEqual(self.lookup()["keys"], [])
        self.assertEqual(self.refusals(), [])

    def test_a_refusal_survives_client_request_where_call_would_discard_it(self):
        self.register()
        args = dict(req_id=rid(), rev=1, agent="intruder-1")
        client = self.ds.Client(self.ds.LocalTransport(self.authority), clock=self.clock)
        self.assertIsNone(client.call("claim", self.writer(), **args))
        reply = client.request("claim", self.writer(), **args)
        self.assertRefused(reply, "wrong-actor")
        self.assertEqual(self.refusals(), ["wrong-actor", "wrong-actor"], "both were recorded before the reply")


class SupersessionTests(RequestCase):
    """Acceptance 6: section 1's three cases, with a later start on a superseded key refused and its claim kept."""

    def test_an_unstarted_earlier_key_is_closed_superseded_and_its_claim_kept(self):
        self.register()
        claimed = self.claim()
        self.assertEqual(self.register(rev=2)["key"]["state"], "open")
        old = self.key(rev=1)
        self.assertEqual((old["state"], old["superseded_by"], old["claim"]["owner"]), ("superseded", 2, SOLVER))
        self.assertRefused(self.start(claimed), "superseded")
        self.assertRefused(self.claim(OTHER_SOLVER), "superseded")
        self.assertEqual(self.key(rev=1)["claim"]["owner"], SOLVER, "the claim is never erased")

    def test_a_started_earlier_key_makes_the_new_revision_wait_until_effect(self):
        self.register()
        claimed = self.claim()
        self.assertEqual(self.start(claimed)["result"], "ok")
        waiting = self.register(rev=2, agent="pat")  # a named successor
        self.assertEqual(waiting["key"]["state"], "waiting")
        self.assertRefused(self.claim(rev=2), "waiting")
        self.assertEqual(self.register(rev=2, agent="pat")["result"], "replay", "a waiting revision repeats too")
        self.assertEqual(self.reconcile("effect", 1, agent=MANAGER)["key"]["state"], "reconciled")
        self.assertEqual(self.key(rev=2)["state"], "open", "it stops waiting")
        self.assertEqual(self.claim(rev=2)["result"], "ok")
        self.assertEqual(self.key(rev=1)["claim"]["owner"], SOLVER)

    def test_no_effect_on_an_earlier_key_with_a_waiting_revision_supersedes_it(self):
        self.register()
        claimed = self.claim()
        self.start(claimed)
        self.register(rev=2)
        reconciled = self.reconcile("no_effect", 1)
        self.assertEqual((reconciled["key"]["state"], reconciled["key"]["superseded_by"]), ("superseded", 2))
        self.assertRefused(self.claim(OTHER_SOLVER), "superseded")  # the superseded content is never claimed again
        self.assertEqual(self.key(rev=2)["state"], "open")
        self.assertEqual(self.key(rev=1)["claim"]["owner"], SOLVER)

    def test_a_new_revision_is_refused_while_a_newer_one_exists_and_an_older_key_still_repeats(self):
        self.register()
        self.register(rev=3)
        self.assertRefused(self.register(rev=2), "newer-revision")
        self.assertEqual(self.lookup()["keys"][-1]["rev"], 3)
        self.assertEqual([k["rev"] for k in self.lookup()["keys"]], [1, 3])
        again = self.register(rev=1)
        self.assertEqual((again["result"], again["key"]["state"]), ("replay", "superseded"))

    def test_only_the_original_registrant_or_a_named_successor_supersedes(self):
        self.register()
        self.assertRefused(self.register(rev=2, agent="alp-manager"), "not-original-authority")
        self.assertEqual(self.key(rev=1)["state"], "open")
        self.assertEqual(self.register(rev=2, agent="pat")["result"], "ok")

    def test_supersession_racing_a_start_either_way(self):
        """Serialized on the authority: whichever commits first decides; the start never runs on a superseded key."""
        self.register()
        claimed = self.claim()
        self.register(rev=2)
        self.assertRefused(self.start(claimed), "superseded")
        self.fresh()
        self.register()
        claimed = self.claim()
        self.assertEqual(self.start(claimed)["result"], "ok")
        self.assertEqual(self.register(rev=2)["key"]["state"], "waiting")
        self.assertEqual(self.key(rev=1)["state"], "started")
        for attempt in range(6):
            with self.subTest(attempt=attempt):
                self.fresh()
                self.authority.run_in_thread()
                self.register()
                claimed = self.claim()
                barrier, out = threading.Barrier(2), {}

                def run(name, fn):
                    barrier.wait()
                    out[name] = fn()
                threads = [threading.Thread(target=run, args=("start", lambda: self.start(claimed, bridge=True))),
                           threading.Thread(target=run, args=("supersede", lambda: self.register(rev=2, bridge=True)))]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(10)
                first, second = self.key(rev=1), self.key(rev=2)
                if out["start"]["result"] == "ok":
                    self.assertEqual((first["state"], second["state"]), ("started", "waiting"))
                else:
                    self.assertRefused(out["start"], "superseded")
                    self.assertEqual((first["state"], second["state"]), ("superseded", "open"))


class AcceptanceTests(RequestCase):
    """Acceptance by the decision-maker, citing a verified post that names the request ID; distinct from claim."""

    def test_a_verified_post_by_the_decision_maker_naming_the_request_accepts_it(self):
        self.register()
        self.post("srv-accept-1", f"[status {rid()}] accepted")
        accepted = self.accept("srv-accept-1")
        self.assertEqual(accepted["result"], "ok")
        self.assertEqual(accepted["key"]["acceptance"]["msgid"], "srv-accept-1")
        self.assertEqual(accepted["key"]["state"], "open", "acceptance is not a claim")
        self.assertEqual(self.accept("srv-accept-1")["result"], "replay")

    def test_acceptance_7_a_wrong_actor_or_mismatched_post_is_refused_and_recorded(self):
        self.register()
        self.register(n=12)
        self.post("srv-other", f"[status {rid()}] accepted", account=SOLVER)
        self.post("srv-twelve", f"[status {rid(12)}] accepted")
        self.assertRefused(self.accept("srv-other"), "wrong-actor")
        self.assertRefused(self.accept("srv-twelve"), "post-mismatch")
        self.assertRefused(self.accept("srv-missing"), "post-unverified")
        self.assertEqual(self.accept("srv-twelve", n=12)["result"], "ok")
        self.assertIsNone(self.key()["acceptance"])
        self.assertEqual(self.refusals(), ["wrong-actor", "post-mismatch", "post-unverified"])

    def test_a_committed_acceptance_replays_after_its_post_is_collected_from_outbox_db(self):
        self.register()
        self.post("srv-old", f"{rid()} ok", at=self.clock() - 3 * self.ds.GC_MARGIN)
        self.assertEqual(self.accept("srv-old")["result"], "ok")
        self.tx(lambda s: s.collect())
        self.assertEqual(self.sql("SELECT * FROM messages WHERE msgid = 'srv-old'"), [], "outbox.db collected it")
        replay = self.accept("srv-old")
        self.assertEqual(replay["result"], "replay")
        self.assertEqual(replay["key"]["acceptance"]["account"], MANAGER)
        self.register(n=2)
        self.assertRefused(self.accept("srv-old", n=2), "post-unverified")  # the post itself is gone

    def test_an_unverified_or_refused_verdict_is_never_accepted_claimed_or_started(self):
        for n, verdict in ((1, UNRESOLVED), (2, REFUSED)):
            with self.subTest(verdict=verdict["status"]):
                self.register(n=n, verdict=verdict)
                self.post(f"srv-{n}", f"{rid(n)} accepted")
                self.assertRefused(self.accept(f"srv-{n}", n=n), "verdict-not-verified")
                self.assertRefused(self.claim(n=n), "verdict-not-verified")
                self.assertRefused(self.op("start", req_id=rid(n), rev=1, agent=SOLVER, generation=1, fence="f"),
                                   "verdict-not-verified")
                self.assertEqual(self.key(n)["state"], "open")
        self.assertEqual(self.refusals(), ["verdict-not-verified"] * 6)


class ClaimTests(RequestCase):
    """Fenced claims and starts: actors, generations, fence tokens and lost replies."""

    def test_acceptance_7_a_wrong_actor_claim_start_or_reconcile_is_refused_and_recorded(self):
        self.register()
        self.assertRefused(self.claim("intruder-1"), "wrong-actor")
        claimed = self.claim()
        self.assertRefused(self.start(claimed, agent=OTHER_SOLVER), "wrong-actor")
        self.start(claimed)
        self.assertRefused(self.reconcile("effect", 1, agent=OTHER_SOLVER), "wrong-actor")
        self.assertEqual(self.key()["state"], "started")
        self.assertEqual(self.refusals(), ["wrong-actor"] * 3)

    def test_lost_replies_after_a_committed_claim_or_start_are_answered_from_the_record(self):
        self.register()
        claimed = self.claim()
        again = self.claim()
        self.assertEqual((again["result"], again["fence"], again["key"]["generation"]),
                         ("replay", claimed["fence"], 1))
        self.assertEqual(self.start(claimed)["result"], "ok")
        self.assertEqual(self.start(claimed)["result"], "replay")
        self.assertEqual(self.claim()["result"], "replay")
        self.assertEqual(self.refusals(), [])

    def test_a_start_needs_the_claims_fence_token(self):
        self.register()
        claimed = self.claim()
        self.assertRefused(self.start(claimed, fence="0" * 32), "wrong-fence")
        self.assertEqual(self.key()["state"], "claimed")

    def test_acceptance_3_a_stale_claimant_is_fenced_and_the_refusal_recorded(self):
        self.register()
        first = self.claim()
        self.start(first)
        self.assertEqual(self.reconcile("no_effect", 1)["key"]["state"], "open")
        second = self.claim(OTHER_SOLVER)
        self.assertEqual(second["key"]["generation"], 2)
        self.assertNotEqual(second["fence"], first["fence"])
        self.assertRefused(self.start(first), "stale-generation")
        self.assertRefused(self.start(first, agent=OTHER_SOLVER, fence=second["fence"]), "stale-generation")
        self.assertRefused(self.reconcile("effect", 1, agent=MANAGER), "already-reconciled", "conflict")
        self.assertEqual((self.key()["state"], self.key()["claim"]["owner"]), ("claimed", OTHER_SOLVER))
        self.assertEqual(self.refusals(), ["stale-generation", "stale-generation", "already-reconciled"])


class ReconcileTests(RequestCase):
    """Reconciliation is fenced to the generation it reconciles; its retry recovers its recorded outcome."""

    def started(self):
        self.register()
        claimed = self.claim()
        self.start(claimed)
        return claimed

    def test_effect_closes_the_key_reconciled_and_nothing_reopens_it(self):
        self.started()
        self.assertEqual(self.reconcile("effect", 1, agent=MANAGER)["key"]["state"], "reconciled")
        self.assertRefused(self.claim(OTHER_SOLVER), "reconciled")
        self.assertEqual(self.reconcile("effect", 1)["result"], "replay")
        self.assertRefused(self.reconcile("no_effect", 1), "already-reconciled", "conflict")
        self.assertEqual(self.key()["state"], "reconciled")

    def test_a_lost_reconciliation_reply_recovers_its_outcome_without_a_second_increment(self):
        self.started()
        self.assertEqual(self.reconcile("no_effect", 1)["result"], "ok")
        retry = self.reconcile("no_effect", 1)
        self.assertEqual((retry["result"], retry["outcome"], retry["key"]["generation"]), ("replay", "no_effect", 1))
        self.assertEqual(self.claim(OTHER_SOLVER)["key"]["generation"], 2)

    def test_a_delayed_reconciliation_after_a_later_claim_releases_nothing(self):
        self.started()
        self.reconcile("no_effect", 1)
        later = self.claim(OTHER_SOLVER)
        self.start(later, agent=OTHER_SOLVER)
        delayed = self.reconcile("no_effect", 1)
        self.assertEqual(delayed["result"], "replay")
        key = self.key()
        self.assertEqual((key["state"], key["generation"], key["claim"]["owner"]), ("started", 2, OTHER_SOLVER))

    def test_an_unstarted_claim_or_an_unclaimed_key_has_nothing_to_reconcile(self):
        self.register()
        self.assertRefused(self.reconcile("effect", 1, agent=MANAGER), "wrong-generation")
        self.claim()
        self.assertRefused(self.reconcile("no_effect", 1), "not-started")
        self.assertEqual(self.key()["state"], "claimed")


class RecoveryTests(RequestCase):
    """Acceptance 4 and section 4: claims and starts survive a restart; an unknown outcome is never taken over."""

    def test_acceptance_4_an_unknown_outcome_stays_unknown_across_a_restart_with_no_takeover(self):
        self.register()
        claimed = self.claim()
        self.start(claimed)
        epoch = self.authority.epoch
        self.restart_bridge()
        key = self.key()
        self.assertEqual((key["state"], key["claim"]["owner"], key["claim"]["authority_epoch"]),
                         ("unknown", SOLVER, epoch))
        self.assertGreater(self.authority.epoch, epoch, "the epoch is recorded for audit, not as the fence")
        self.assertRefused(self.claim(OTHER_SOLVER), "already-claimed", "conflict")
        self.assertEqual(self.start(claimed)["result"], "replay", "a lost start reply, answered from the record")
        self.clock.advance(30 * 86400)
        self.restart_bridge()
        self.assertEqual(self.key()["state"], "unknown", "no lease or restart permits a takeover")
        self.assertEqual(self.reconcile("effect", 1)["key"]["state"], "reconciled")

    def test_an_unstarted_claim_resumes_after_a_restart_only_with_its_fence_token(self):
        self.register()
        claimed = self.claim()
        self.restart_bridge()
        self.assertEqual(self.key()["state"], "claimed")
        self.assertRefused(self.start(claimed, fence="1" * 32), "wrong-fence")
        self.assertEqual(self.start(claimed)["result"], "ok")
        self.assertEqual(self.key()["generation"], 1)


class AuthorityBoundaryTests(RequestCase):
    """The request store's place in the authority: its own transactions, the one read of outbox.db, the existing
    operations unchanged, and its lifecycle inside the authority's start and close."""

    def every_request_op(self, bridge):
        self.register(n=5, bridge=bridge)
        self.accept("srv-5", n=5, bridge=bridge)
        claimed = self.claim(n=5, bridge=bridge)
        self.start(claimed, n=5, bridge=bridge)
        self.reconcile("effect", 1, n=5, bridge=bridge)
        self.claim("intruder-1", n=5, bridge=bridge)
        return self.lookup(5, bridge=bridge)

    def test_request_operations_never_open_an_outbox_db_transaction(self):
        for bridge in (False, True):
            with self.subTest(bridge=bridge):
                self.fresh()
                self.post("srv-5", f"{rid(5)} accepted")
                changes = self.authority.store.con.total_changes
                fail = AssertionError("an outbox.db transaction began")
                with mock.patch.object(self.authority.store, "tx", side_effect=fail), \
                        mock.patch.object(self.authority.store, "read_tx", side_effect=fail):
                    looked = self.every_request_op(bridge)
                self.assertEqual(looked["keys"][0]["state"], "reconciled")
                self.assertEqual(self.authority.store.con.total_changes, changes, "outbox.db was never written")

    def test_the_socket_path_serves_request_operations_for_their_sender_only(self):
        from test_outbox_authority import Conn
        conn = Conn(self)
        self.addCleanup(conn.close)
        args = dict(agent="sam", line=line(), verdict=VERIFIED, room="#alpha", decision_maker=MANAGER,
                    claimants=[SOLVER])
        conn.send({"v": self.ds.PROTOCOL, "op": "register", "request_id": "s1", "writer": self.writer(), **args})
        self.assertEqual(conn.reply()["result"], "ok")
        other = Conn(self)
        self.addCleanup(other.close)
        other.send({"v": self.ds.PROTOCOL, "op": "claim", "request_id": "s2", "writer": self.writer(OTHER_PID),
                    "req_id": rid(), "rev": 1, "agent": SOLVER})
        refused = other.reply()
        self.assertEqual((refused["result"], refused["detail"]),
                         ("refused", "the request does not speak for its sender"))
        self.assertEqual(self.key()["state"], "open")

    def test_existing_operations_are_unchanged_and_never_touch_requests_db(self):
        """The same outbox requests get byte-identical replies whether the request store is open or refused, and
        leave requests.db's bytes as they were."""
        writer = self.writer()
        frames = []
        for foreign in (False, True):
            self.fresh()
            self.authority.close()
            if foreign:
                (self.chatlib.STATE_DIR / "requests.db").write_bytes(b"invented: not a database\n")
            self.authority = self.new_authority()  # the same epoch in both runs
            if foreign:
                self.assertIsNone(self.authority.requests.con)
            else:
                self.register()
            before = self.store_files()
            transport = self.ds.LocalTransport(self.authority)
            replies = []
            for n, (op, args) in enumerate((("create", self.create_args("a" * 32)), ("query", {"id": "a" * 32}),
                                            ("attempt", dict(id="a" * 32, n=0, generation=0, a1_nonce="n")),
                                            ("status", {}), ("frobnicate", {}))):
                request = {"v": self.ds.PROTOCOL, "op": op, "request_id": f"r{n}",
                           "writer": None if op == "status" else writer, **args}
                replies.append(self.ds.encode_frame(transport.exchange(request), self.ds.REPLY_MAX))
            frames.append(replies)
            self.assertEqual(self.store_files(), before, "no requests.db byte changed")
        self.assertEqual(frames[0], frames[1])
        self.assertEqual(self.ds.PROTOCOL, "outbox-authority/1")
        self.assertEqual(self.ds.CLIENT_OPS, ("create", "queue", "attempt", "outcome", "retire", "void", "handoff",
                                              "done", "query", "status"))

    def test_a_failed_request_store_open_releases_its_connection_and_the_authority_runs(self):
        self.authority.close()
        made = []
        real = self.ds.request_store.sqlite3.connect

        def connect(*a, **k):
            made.append(real(*a, **k))
            return made[-1]

        def prepare(store, con):
            raise sqlite3.OperationalError("invented: disk I/O error")
        with mock.patch.object(self.ds.request_store.sqlite3, "connect", connect), \
                mock.patch.object(self.ds.request_store.RequestStore, "_prepare", prepare):
            self.authority = self.new_authority()
            self.assertTrue(self.authority.running)
            self.assertIsNone(self.authority.requests.con)
            with self.assertRaises(sqlite3.ProgrammingError):
                made[-1].execute("SELECT 1")  # closed
            refused = self.register()  # each admitted mutation tries the open again, and is refused the same way
            self.assertEqual(refused["refusal"]["reason"], "request-store-unavailable")
            self.assertIn("invented: disk I/O error", self.lookup()["refusal"]["detail"])
            self.assertEqual(self.client().call("create", self.writer(), **self.create_args("a" * 32))["result"],
                             "ok")
        self.assertEqual(self.register()["result"], "ok", "the next admitted mutation opens it again")

    def test_a_failed_start_releases_both_connections_and_the_lock(self):
        self.authority.close()
        a = self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, probes=self.probes)
        with mock.patch.object(self.ds.Authority, "bind", side_effect=OSError("invented: address in use")):
            with self.assertRaises(OSError):
                a.launch()
        self.assertTrue(a.released)
        self.assertIsNone(a.requests.con)
        self.assertIsNone(a.store.con)
        self.authority = self.new_authority()

    def test_closing_is_deferred_with_requests_db_open_while_maintenance_is_held(self):
        self.register()
        owner = self.hold("partial")
        self.authority.close()
        self.assertFalse(self.authority.released, "released stays false while requests.db is open")
        self.assertIsNotNone(self.authority.requests.con)
        owner.release()
        self.authority.close()
        self.assertTrue(self.authority.released)
        self.assertIsNone(self.authority.requests.con)


class ForeignFileTests(RequestCase):
    """A foreign, corrupt or ambiguous requests.db refuses only the request operations: the authority runs, and
    every byte is kept, with no new sidecar."""

    def foreign_wal(self, path):
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE notes (x)")
        con.execute("INSERT INTO notes VALUES ('invented')")
        con.close()

    def foreign_rollback(self, path):
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO meta VALUES ('schema', 'request-store/1')")
        con.commit()
        con.close()

    def our_tables_other_format(self, path):
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        for statement in self.ds.request_store.SCHEMA.split(";"):
            if statement.strip():
                con.execute(statement)
        con.execute("INSERT INTO meta VALUES ('schema', 'request-store/9'), ('writer_model', 'bridge-exclusive/1')")
        con.close()

    def test_each_foreign_file_is_kept_byte_for_byte_and_refuses_only_request_operations(self):
        cases = {
            "not SQLite": lambda p: Path(p).write_bytes(b"invented: not a database at all\n" * 64),
            "a rollback-journal SQLite database": self.foreign_rollback,
            "a WAL database with another schema": self.foreign_wal,
            "this store's tables in another format": self.our_tables_other_format,
            "a SQLite header with garbage after it": lambda p: Path(p).write_bytes(
                b"SQLite format 3\x00" + b"\x10\x00\x02\x02" + b"\xff" * 4076),
        }
        for label, make in cases.items():
            with self.subTest(case=label):
                self.fresh()
                self.authority.close()
                for name in STORE_FILES:
                    (self.chatlib.STATE_DIR / name).unlink(missing_ok=True)
                make(str(self.chatlib.STATE_DIR / "requests.db"))
                before = self.store_files()
                self.authority = self.new_authority()
                self.assertTrue(self.authority.running)
                self.assertIsNone(self.authority.requests.con)
                for reply in (self.register(), self.lookup(), self.claim(), self.register(bridge=True)):
                    self.assertEqual(reply["result"], "refused")
                    self.assertEqual(reply["refusal"]["reason"], "request-store-unavailable")
                    self.assertFalse(reply["refusal"]["recorded"])
                self.assertEqual(self.client().call("create", self.writer(), **self.create_args("a" * 32))["result"],
                                 "ok", "posts, acks and notices are unaffected")
                self.authority.close()
                self.assertEqual(self.store_files(), before, "every byte kept, no sidecar, no copy left")

    def test_a_sidecar_without_a_non_empty_requests_db_is_ambiguous_and_kept(self):
        for sidecar in ("requests.db-wal", "requests.db-shm", "requests.db-journal"):
            for with_empty in (False, True):
                with self.subTest(sidecar=sidecar, empty_database=with_empty):
                    self.fresh()
                    self.authority.close()
                    for name in STORE_FILES:
                        (self.chatlib.STATE_DIR / name).unlink(missing_ok=True)
                    (self.chatlib.STATE_DIR / sidecar).write_bytes(b"invented: stale sidecar bytes\n")
                    if with_empty:
                        (self.chatlib.STATE_DIR / "requests.db").write_bytes(b"")
                    before = self.store_files()
                    self.authority = self.new_authority()
                    self.assertIsNone(self.authority.requests.con)
                    self.assertIn("ambiguous evidence", self.register()["refusal"]["detail"])
                    self.authority.close()
                    self.assertEqual(self.store_files(), before)

    def test_a_missing_or_empty_requests_db_is_created_and_a_crashed_one_is_read_with_its_wal(self):
        self.assertTrue((self.chatlib.STATE_DIR / "requests.db").exists(), "made at the start")
        self.register()
        self.authority.close()
        (self.chatlib.STATE_DIR / "requests.db").unlink()
        (self.chatlib.STATE_DIR / "requests.db").write_bytes(b"")
        self.authority = self.new_authority()
        self.assertIsNotNone(self.authority.requests.con, "an empty requests.db with no sidecar is new")
        self.assertEqual(self.register()["result"], "ok")
        crashed = self.chatlib.STATE_DIR / "crashed"
        crashed.mkdir()
        self.addCleanup(shutil.rmtree, crashed)
        # a crash leaves the -wal beside the file: copy both while the authority holds them open
        for name in ("requests.db", "requests.db-wal"):
            shutil.copyfile(self.chatlib.STATE_DIR / name, crashed / name)
        self.authority.close()
        for name in ("requests.db", "requests.db-wal"):
            shutil.copyfile(crashed / name, self.chatlib.STATE_DIR / name)
        self.authority = self.new_authority()
        self.assertIsNotNone(self.authority.requests.con, "classified on a copy that read its WAL")
        self.assertEqual(self.register()["result"], "replay")
        self.assertFalse([p for p in self.chatlib.STATE_DIR.iterdir() if p.name.startswith(".request-store")])


class AdmissionTests(RequestCase):
    """Writer admission 4.4 to 4.5 and 6.4 for the request operations, on both paths: the client's (handle) and
    the bridge's in-process one (run_request). `lookup` is answered in every state with no section and no write;
    every mutation runs only inside an admitted section."""

    def prepared(self):
        self.register()
        self.post("srv-accept", f"{rid()} accepted")
        claimed = self.claim()
        self.start(claimed)
        self.register(n=2)
        return claimed

    def mutations(self, claimed):
        return (("register a new key", lambda b: self.register(n=3, bridge=b)),
                ("register a known key", lambda b: self.register(bridge=b)),
                ("accept", lambda b: self.accept("srv-accept", bridge=b)),
                ("claim", lambda b: self.claim(n=2, bridge=b)),
                ("start", lambda b: self.start(claimed, bridge=b)),
                ("reconcile", lambda b: self.reconcile("effect", 1, bridge=b)))

    def test_lookup_is_answered_in_every_state_with_no_section_and_no_write(self):
        self.prepared()
        owner = self.owner()
        owner.acquire()
        for state in ("draining", "partial", "full", "unknown"):
            if state == "partial":
                owner.drain()
            if state == "full":
                owner.advance("full", boot="boot-B", legacy="retired-and-inhibited")
            if state == "unknown":
                self.paths.maintenance.write_bytes(b"{")
            for bridge in (False, True):
                with self.subTest(state=state, bridge=bridge):
                    before = self.store_files()
                    with mock.patch.object(self.ds, "section", side_effect=AssertionError("a section")), \
                            mock.patch.object(self.authority.requests, "tx", side_effect=AssertionError("a write")):
                        looked = self.lookup(bridge=bridge)
                    self.assertEqual((looked["result"], looked["keys"][0]["state"]), ("ok", "started"))
                    self.assertEqual(self.store_files(), before)

    def test_partial_full_and_unknown_refuse_every_mutation_on_both_paths(self):
        claimed = self.prepared()
        owner = self.hold("partial")
        for state in ("partial", "full", "unknown"):
            if state == "full":
                owner.advance("full", boot="boot-B", legacy="retired-and-inhibited")
            if state == "unknown":
                self.paths.maintenance.write_bytes(b"{")
            for label, mutate in self.mutations(claimed):
                for bridge in (False, True):
                    with self.subTest(state=state, op=label, bridge=bridge):
                        with mock.patch.object(self.authority.requests, "tx",
                                               side_effect=AssertionError("a transaction began")):
                            reply = mutate(bridge)
                        if bridge:
                            self.assertIsNone(reply, "not run: CANCELLED, nothing begun")
                        else:
                            self.assertEqual((reply["result"], reply["maintenance"]["state"]), ("maintenance", state))
        self.paths.maintenance.unlink()
        self.assertEqual(self.refusals(), [])
        self.assertEqual([k["rev"] for k in self.lookup(3)["keys"]], [])

    def test_draining_refuses_new_work_and_lets_continuations_run_on_both_paths(self):
        for bridge in (False, True):
            with self.subTest(bridge=bridge):
                self.fresh()
                claimed = self.prepared()
                if bridge:
                    with self.ds.section(self.paths) as open_section:  # a bridge section open before the record
                        self.assertTrue(open_section.admitted)
                        owner = self.hold("draining")
                        replies = {label: mutate(True) for label, mutate in self.mutations(claimed)}
                    self.assertIsNone(self.register(n=4, bridge=True), "outside a section: not run")
                    self.assertIsNone(self.accept("srv-accept", bridge=True))
                else:
                    owner = self.hold("draining")
                    replies = {label: mutate(False) for label, mutate in self.mutations(claimed)}
                for label in ("register a new key", "claim", "start"):
                    reply = replies[label]
                    if bridge:
                        self.assertIsNone(reply, label)
                    else:
                        self.assertEqual(reply["result"], "maintenance", label)
                self.assertEqual(replies["register a known key"]["result"], "replay")
                self.assertEqual(replies["accept"]["result"], "ok")
                self.assertEqual(replies["reconcile"]["result"], "ok")
                owner.release()
                self.assertEqual(self.lookup(3)["keys"], [], "the new key was never registered")
                self.assertEqual(self.key(2)["state"], "open", "the claim was never made")


class BoundStartTests(BoundStartCase):
    """Under a `full` record a bound start neither creates nor writes requests.db; `lookup` creates nothing; after
    the release, on the same Authority, the deferred opening and its recovery write happen only once admitted."""

    def setUp(self):
        super().setUp()
        for name in STORE_FILES:  # the case's own start made one: these tests begin with none
            (self.state / name).unlink(missing_ok=True)

    def op(self, authority, op, bridge=False, **args):
        client = self.ds.Client(self.ds.LocalTransport(authority, bridge=bridge), clock=self.clock)
        return client.request(op, self.writer(), **args)

    def files(self):
        return {n: (self.state / n).read_bytes() for n in STORE_FILES if (self.state / n).exists()}

    def register(self, authority, n=1, bridge=False):
        return self.op(authority, "register", bridge=bridge, agent="sam", line=line(n), verdict=VERIFIED,
                       room="#alpha", decision_maker=MANAGER, claimants=[SOLVER])

    def test_a_bound_start_creates_nothing_and_admits_requests_db_only_after_the_release(self):
        d = self.publish(self.record(["alp-solver-2"]))
        owner = self.hold_full(d)
        authority = self.launch(d)
        self.assert_running_under_the_lock(authority)
        self.assertEqual(self.files(), {}, "no requests.db, -wal, -shm or -journal")
        looked = self.op(authority, "lookup", req_id=rid())
        self.assertEqual(looked["refusal"]["reason"], "request-store-unavailable")
        self.assertEqual(self.register(authority)["result"], "maintenance")
        self.assertIsNone(self.register(authority, bridge=True))
        self.assertEqual(self.files(), {}, "lookup and the refused mutations created nothing")
        owner.release()
        self.assertEqual(self.op(authority, "lookup", req_id=rid())["refusal"]["reason"],
                         "request-store-unavailable", "lookup stays read-only after the release too")
        self.assertEqual(self.files(), {})
        self.assertEqual(self.register(authority)["result"], "ok", "the first admitted mutation opens it")
        self.assertIn("requests.db", self.files())
        self.assertEqual(self.op(authority, "lookup", req_id=rid())["keys"][0]["state"], "open")

    def test_the_recovery_write_is_deferred_until_admission(self):
        plain = self.ds.Authority(self.state, clock=self.clock, probes=self.probes)
        plain.start()
        self.register(plain)
        claimed = self.op(plain, "claim", req_id=rid(), rev=1, agent=SOLVER)
        self.op(plain, "start", req_id=rid(), rev=1, agent=SOLVER, generation=1, fence=claimed["fence"])
        plain.close()
        self.wipe_database()  # the bound start below bootstraps a new outbox.db; requests.db is kept
        before = self.files()
        d = self.publish(self.record(["alp-solver-2"]))
        owner = self.hold_full(d)
        authority = self.launch(d)
        self.assertEqual(self.files(), before, "the bound start wrote nothing to requests.db")
        owner.release()
        self.assertEqual(self.files(), before)
        self.assertEqual(self.register(authority, n=2)["result"], "ok")
        self.assertEqual(self.op(authority, "lookup", req_id=rid())["keys"][0]["state"], "unknown",
                         "the start recorded before the restart is unknown, once admission allowed the write")


class PinnedRootTests(BootstrapCase):
    """#21 amendment 7 and #32 amendment 2: during the bridge's activated start, a changed state root found around
    requests.db's open fails the whole start closed; a bad requests.db under an unchanged root refuses only the
    request operations."""

    def setUp(self):
        super().setUp()
        for name in STORE_FILES:  # the case's own start made one: these tests begin with none
            (self.state / name).unlink(missing_ok=True)

    def replace_root(self):
        moved = self.state.with_name(f"{self.state.name}-pinned-{os.urandom(4).hex()}")
        self.state.rename(moved)
        self.state.mkdir()
        self.addCleanup(self.restore_root, moved)
        return moved

    def restore_root(self, moved):
        if moved.exists():
            shutil.rmtree(self.state)
            moved.rename(self.state)

    def test_a_root_swapped_just_before_the_open_fails_the_start_and_leaves_no_requests_db(self):
        d = self.publish(self.record(["alp-solver-2"]))
        moved, real = [], self.ds.request_store.RequestStore.preflight

        def preflight(store, root_fd=None):
            result = real(store, root_fd)
            moved.append(self.replace_root())
            return result
        with mock.patch.object(self.ds.request_store.RequestStore, "preflight", preflight):
            authority = self.launch(d)
        self.assertTrue(moved)
        self.assertIn("moved or replaced around requests.db's open", authority.requests.why, "the store's own check")
        self.assertIsNotNone(self.bridge.BOOTSTRAP_UNKNOWN, authority.unavailable)
        self.assertFalse(authority.running)
        self.assertTrue(authority.released)
        self.assertIsNone(authority.requests.con)
        self.assertIsNone(authority.store.con)
        self.stop()
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), [], "nothing in the replacement")
        self.assertFalse((moved[0] / "requests.db").exists(), "nor in the pinned root: it was never opened")

    def test_a_root_swapped_just_after_the_open_fails_the_start(self):
        d = self.publish(self.record(["alp-solver-2"]))
        moved, real = [], sqlite3.connect

        def connect(*a, **k):
            con = real(*a, **k)
            if str(a[0] if a else k.get("database")).endswith("/requests.db") and not moved:
                moved.append(self.replace_root())
            return con
        with mock.patch.object(self.ds.request_store.sqlite3, "connect", connect):
            authority = self.launch(d)
        self.assertTrue(moved)
        self.assertIn("moved or replaced around requests.db's open", authority.requests.why, "the check after it")
        self.assertIsNotNone(self.bridge.BOOTSTRAP_UNKNOWN)
        self.assertTrue(authority.released)
        self.assertIsNone(authority.requests.con)

    def test_requests_db_and_its_classification_stay_in_the_pinned_root(self):
        d = self.publish(self.record(["alp-solver-2"]))
        made = []
        real_mkdir = os.mkdir

        def mkdir(path, *a, **k):
            made.append((os.fsdecode(path), k.get("dir_fd") is not None))
            return real_mkdir(path, *a, **k)
        authority = self.launch(d)
        self.assert_running_under_the_lock(authority)
        self.assertTrue((self.state / "requests.db").exists())
        self.stop()
        with mock.patch.object(self.ds.request_store.os, "mkdir", mkdir):
            again = self.launch(d)  # an existing requests.db is classified on a copy
        self.assert_running_under_the_lock(again)
        self.assertIsNotNone(again.requests.con)
        copies = [m for m in made if m[0].startswith(".request-store-classify-")]
        self.assertEqual(len(copies), 1)
        self.assertTrue(copies[0][1], "the copy's directory is made through the pinned root's descriptor")
        self.assertFalse([p for p in self.state.iterdir() if p.name.startswith(".request-store")], "and removed")

    def test_a_foreign_requests_db_under_an_unchanged_root_keeps_the_bridge_running(self):
        d = self.publish(self.record(["alp-solver-2"]))
        (self.state / "requests.db").write_bytes(b"invented: a foreign file\n")
        authority = self.launch(d)
        self.assert_running_under_the_lock(authority)
        self.assertIsNone(self.bridge.BOOTSTRAP_UNKNOWN)
        self.assertIsNone(authority.requests.con)
        self.assertIn("not a SQLite database", authority.requests.why)
        self.stop()
        self.assertEqual((self.state / "requests.db").read_bytes(), b"invented: a foreign file\n")


if __name__ == "__main__":
    unittest.main()
