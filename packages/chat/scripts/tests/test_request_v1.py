"""request/v1 (COLLAB-1, #27): the canonical line, its digest and request-scoped
authority validation. Plateia's own test; offline, with invented data only
(projects alpha and beta, the owner pat, the assistant sam, the manager
alpha-manager, the worker alp-solver-2) and no chat, state or clock."""
import dataclasses
import datetime
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import request_v1 as rq  # noqa: E402

RID = "alpha-20261013-1"
POLICY = "owner-grant"
POLICY_DIGEST = hashlib.sha256(b"invented owner grant, version one").hexdigest()
OTHER_DIGEST = hashlib.sha256(b"invented owner grant, version two").hexdigest()
EXP = "20261101T000000Z"
EXP_EPOCH = int(datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc).timestamp())
NOW = EXP_EPOCH - 86400
RELAY_ID = "m4k2x9"  # the message ID of sam's relay post

BASE = rq.Request(request_id=RID, op="op-7f3a", rev=1, action="run-check", target="alpha-repo",
                  scope=("check-alpha", "report-alpha"), policy_name=POLICY, policy_digest=POLICY_DIGEST,
                  gates=("owner-merge-gate",), expiry=EXP)
GRANT = rq.Grant(name=POLICY, digest=POLICY_DIGEST,
                 scope=frozenset({"check-alpha", "report-alpha", "check-beta"}),
                 actions=frozenset({"run-check", "summarize"}), targets=frozenset({"alpha-repo", "beta-repo"}),
                 gates=frozenset({"owner-merge-gate"}))
MINIMAL = (f"[request {RID}] request/v1 op=op-7f3a rev=1 act=run-check tgt=alpha-repo scope=check-alpha "
           f"policy={POLICY}:{POLICY_DIGEST}")
FULL = (f"[request {RID}] request/v1 op=op-7f3a rev=1 act=run-check tgt=alpha-repo "
        f"scope=check-alpha,report-alpha gates=owner-merge-gate policy={POLICY}:{POLICY_DIGEST} exp={EXP} "
        f"relay=sam cite={RELAY_ID} trace=pat-thread-7")


def req(**changes):
    return dataclasses.replace(BASE, **changes)


def text(**changes):
    return rq.encode(req(**changes))


def hop(**changes):
    """A later hop: it cites sam's relay post and says sam relayed it."""
    return text(**{"relay": "sam", "cite": RELAY_ID, **changes})


RECORDS = {
    RELAY_ID: rq.Record("sam", True, text()),
    "m-manager": rq.Record("alpha-manager", True, text()),
    "m-unverified": rq.Record("sam", False, text()),
    "m-later-hop": rq.Record("sam", True, hop()),
    "m-chat": rq.Record("sam", True, "go ahead, approved"),
    "m-relayed": rq.Record("sam", True, text(relay="sam")),
    "m-bad-claim": rq.Record("sam", True, text(relay="alpha-manager")),  # sam's post naming someone else
}
GOOD = rq.Evidence(post=rq.Post("sam", "m5r8w2", True), assistants=frozenset({"sam"}), lookup=RECORDS.get,
                   grant=GRANT, now=NOW, expiry=rq.VALID, revocation=rq.VALID)
MANAGER = rq.Post("alpha-manager", "m6z1q7", True)


def good(**changes):
    return dataclasses.replace(GOOD, **changes)


def padded(trace_length):
    """A line whose last field is a trace token of the given length."""
    scope = ",".join(f"{i}" + "x" * 29 for i in range(8))
    return (f"[request {RID}] request/v1 op=op-pad rev=1 act=run-check tgt=alpha-repo scope={scope} "
            f"policy={POLICY}:{POLICY_DIGEST} exp={EXP} relay=sam cite={RELAY_ID} trace={'t' * trace_length}")


class RequestV1Tests(unittest.TestCase):
    def refused_code(self, wire):
        with self.assertRaises(rq.RequestError) as caught:
            rq.parse(wire)
        return caught.exception.code

    def assertVerdict(self, wire, evidence, status, reason=None):
        verdict = rq.validate(wire, evidence)
        self.assertEqual(verdict.status, status, verdict)
        if reason is not None:
            self.assertIn(reason, verdict.reasons)
        if status != rq.VERIFIED:
            self.assertIsNone(verdict.authority)
        return verdict

    # 1
    def test_canonical_round_trip(self):
        for label, wire in (("minimal", MINIMAL), ("every optional field", FULL)):
            with self.subTest(line=label):
                parsed = rq.parse(wire)
                self.assertEqual(rq.encode(parsed), wire)
                self.assertEqual(rq.parse(rq.encode(parsed)), parsed)
        full, minimal = rq.parse(FULL), rq.parse(MINIMAL)
        self.assertEqual(full.execution_key, (RID, 1))
        self.assertEqual((full.scope, full.gates, full.expiry), (("check-alpha", "report-alpha"),
                                                                 ("owner-merge-gate",), EXP))
        self.assertEqual((full.relay, full.cite, full.trace), ("sam", RELAY_ID, "pat-thread-7"))
        self.assertEqual((minimal.gates, minimal.expiry, minimal.relay, minimal.cite, minimal.trace),
                         ((), None, None, None, None))
        self.assertEqual(text(relay="sam", cite=RELAY_ID, trace="pat-thread-7"), FULL)
        self.assertEqual(text(scope=("check-alpha",), gates=(), expiry=None), MINIMAL)
        # the byte limit counts the prefix: exactly the limit reads back, one more is refused
        room = rq.MAX_LINE_BYTES - len(padded(0))
        self.assertTrue(1 <= room < rq.MAX_TOKEN, room)
        at_limit = padded(room)
        self.assertEqual(len(at_limit.encode("ascii")), rq.MAX_LINE_BYTES)
        self.assertEqual(rq.encode(rq.parse(at_limit)), at_limit)
        self.assertEqual(self.refused_code(padded(room + 1)), "over-length")
        # a Request with no canonical spelling is not encoded
        for label, bad in (("unsorted scope", req(scope=("report-alpha", "check-alpha"))),
                           ("zero revision", req(rev=0)), ("revision as text", req(rev="1")),
                           ("uppercase token", req(op="Op-7f3a")), ("scope as a list", req(scope=["check-alpha"])),
                           ("not a request", None)):
            with self.subTest(unencodable=label):
                with self.assertRaises(rq.RequestError):
                    rq.encode(bad)

    # 2
    def test_non_canonical_forms_are_refused_with_a_named_reason(self):
        def swap(old, new):
            self.assertIn(old, MINIMAL)
            return MINIMAL.replace(old, new)

        digest = POLICY_DIGEST
        cases = [
            ("not text: none", None, "not-text"), ("not text: bytes", MINIMAL.encode(), "not-text"),
            ("empty", "", "bad-spacing"),
            ("trailing newline", MINIMAL + "\n", "multiple-lines"),
            ("two lines", MINIMAL + "\n" + MINIMAL, "multiple-lines"),
            ("tab", swap(" op=", "\top="), "control-character"),
            ("carriage return", MINIMAL + "\r", "control-character"),
            ("nul", swap("alpha-repo", "alpha\x00repo"), "control-character"),
            ("non-ascii", swap("op-7f3a", "op-é"), "non-ascii"),
            ("doubled space", swap(" rev=", "  rev="), "bad-spacing"),
            ("leading space", " " + MINIMAL, "bad-spacing"), ("trailing space", MINIMAL + " ", "bad-spacing"),
            ("no space after the prefix", swap("] request", "]request"), "bad-prefix"),
            ("another bracket word", swap("[request", "[requests"), "bad-prefix"),
            ("uppercase request id", swap("alpha-20261013-1]", "ALPHA-20261013-1]"), "bad-request-id"),
            ("impossible date", swap("alpha-20261013-1]", "alpha-20261313-1]"), "bad-request-id"),
            ("counter with a leading zero", swap("alpha-20261013-1]", "alpha-20261013-01]"), "bad-request-id"),
            ("no counter", swap("alpha-20261013-1]", "alpha-20261013]"), "bad-request-id"),
            ("another format", swap("request/v1", "card-move/v1"), "unsupported-format"),
            ("no format", f"[request {RID}]", "unsupported-format"),
            ("free text", f"[request {RID}] please move it", "unsupported-format"),
            ("extra text", MINIMAL + " please hurry", "extra-text"),
            ("unknown field", MINIMAL + " priority=high", "unknown-field"),
            ("repeated field", swap(" rev=1", " rev=1 rev=1"), "repeated-field"),
            ("fields out of order", swap("act=run-check tgt=alpha-repo", "tgt=alpha-repo act=run-check"),
             "field-order"),
            ("optional fields out of order", MINIMAL + " trace=t1 relay=sam", "field-order"),
            ("missing op", swap(" op=op-7f3a", ""), "missing-field"),
            ("missing scope", swap(" scope=check-alpha", ""), "missing-field"),
            ("missing policy", swap(f" policy={POLICY}:{digest}", ""), "missing-field"),
            ("empty value", swap("op=op-7f3a", "op="), "bad-token"),
            ("over-length", padded(rq.MAX_LINE_BYTES - len(padded(0)) + 1), "over-length"),
        ]
        cases += [(f"token {t!r}", swap("op=op-7f3a", f"op={t}"), "bad-token")
                  for t in ("Op-7f3a", "op_7f3a", "-op", "op-", "op--7f3a", "a" * (rq.MAX_TOKEN + 1))]
        cases += [(f"revision {n!r}", swap(" rev=1 ", f" rev={n} "), "bad-integer")
                  for n in ("0", "01", "+1", "-1", "1.0", "1e3", "1" + "0" * rq.MAX_INT_DIGITS)]
        cases += [(f"scope {c!r}", swap(" scope=check-alpha ", f" scope={c} "), "bad-collection")
                  for c in ("b,a", "a,a", "a,,b", "a,", ",a", ",".join(f"a{i}" for i in range(1, 10)))]
        cases += [("scope item not a token", swap(" scope=check-alpha ", " scope=Alpha "), "bad-token")]
        cases += [(f"policy {why}", swap(f"policy={POLICY}:{digest}", p), "bad-policy")
                  for why, p in (("without a digest", f"policy={POLICY}"),
                                 ("uppercase digest", f"policy={POLICY}:{digest.upper()}"),
                                 ("short digest", f"policy={POLICY}:{digest[:63]}"))]
        cases += [(f"expiry {e!r}", MINIMAL + f" exp={e}", "bad-expiry")
                  for e in ("20261301T000000Z", "2026-11-01T00:00:00Z", "20261101T000000z", "20261101",
                            "20261101T240000Z")]
        tried = set()
        for label, wire, code in cases:
            with self.subTest(case=label):
                if isinstance(wire, str):
                    self.assertNotEqual(wire, MINIMAL)
                self.assertEqual(self.refused_code(wire), code)
                tried.add(code)
        self.assertEqual(tried, set(rq.CODES) - {"non-canonical-request"}, "a named reason has no case")
        # validation turns the same refusal into a verdict, not an exception
        self.assertVerdict("go ahead, approved", GOOD, rq.REFUSED, "malformed:bad-prefix")
        self.assertVerdict(MINIMAL + " please hurry", GOOD, rq.REFUSED, "malformed:extra-text")

    # 3
    def test_the_digest_follows_logical_content_and_nothing_else(self):
        expected = (b"plateia request/v1 logical content\n" b"id=alpha-20261013-1\n" b"rev=1\n" b"op=op-7f3a\n"
                    b"act=run-check\n" b"tgt=alpha-repo\n" b"scope=check-alpha,report-alpha\n"
                    b"gates=owner-merge-gate\n" + f"policy={POLICY}:{POLICY_DIGEST}\n".encode("ascii")
                    + b"exp=20261101T000000Z\n")
        self.assertEqual(rq.logical_bytes(req()), expected)
        self.assertEqual(rq.digest(req()), hashlib.sha256(expected).hexdigest())
        bare = rq.logical_bytes(rq.parse(MINIMAL))
        self.assertTrue(b"\ngates=\npolicy=" in bare and bare.endswith(b"exp=\n"), bare)
        # delivery and hop provenance (relay, cite, trace; a new transport message ID) are not hashed
        for label, hop_fields in (("relay claim", {"relay": "sam"}), ("citation", {"cite": "m9q4t1"}),
                                  ("relayed hop", {"relay": "sam", "cite": RELAY_ID}),
                                  ("trace", {"trace": "pat-thread-7"}), ("all three", {"relay": "alpha-manager",
                                                                                   "cite": "m2", "trace": "t2"})):
            with self.subTest(provenance=label):
                self.assertEqual(rq.digest(req(**hop_fields)), rq.digest(req()))
                self.assertEqual(rq.digest(rq.parse(text(**hop_fields))), rq.digest(req()))
        # but every logical field is
        changes = {"request id": {"request_id": "alpha-20261013-2"}, "revision": {"rev": 2},
                   "operation": {"op": "op-9b1c"}, "action": {"action": "summarize"},
                   "target": {"target": "beta-repo"}, "scope": {"scope": ("check-alpha",)},
                   "gates": {"gates": ()}, "policy name": {"policy_name": "owner-grant-b"},
                   "policy digest": {"policy_digest": OTHER_DIGEST}, "expiry": {"expiry": "20261102T000000Z"},
                   "no expiry": {"expiry": None}}
        seen = {rq.digest(req())}
        for label, change in changes.items():
            with self.subTest(changed=label):
                changed = rq.digest(req(**change))
                self.assertRegex(changed, r"^[0-9a-f]{64}$")
                self.assertNotIn(changed, seen)
                seen.add(changed)

    # 4
    def test_conflicting_reuse_is_detected_against_supplied_content(self):
        prior = req()
        self.assertEqual(rq.compare_reuse(prior, prior), rq.REPEAT)
        for label, again in (("a relay", req(relay="sam", cite=RELAY_ID)), ("a repost with a trace", req(trace="t1")),
                             ("the same line", text())):
            with self.subTest(repeat=label):
                self.assertEqual(rq.compare_reuse(prior, again), rq.REPEAT)
        self.assertEqual(rq.compare_reuse(text(), text(trace="t1")), rq.REPEAT)  # lines are read the same way
        for label, change in (("scope", {"scope": ("check-alpha",)}), ("action", {"action": "summarize"}),
                              ("target", {"target": "beta-repo"}), ("operation", {"op": "op-other"}),
                              ("gates", {"gates": ()}), ("expiry", {"expiry": "20261102T000000Z"}),
                              ("policy digest", {"policy_digest": OTHER_DIGEST})):
            with self.subTest(conflict=label):
                self.assertEqual(rq.compare_reuse(prior, req(**change)), rq.CONFLICT)
                self.assertEqual(rq.compare_reuse(req(**change), prior), rq.CONFLICT)
        # another revision, or another request, is another key, whatever its content
        self.assertEqual(rq.compare_reuse(prior, req(rev=2, scope=("check-alpha",), action="summarize")),
                         rq.DISTINCT)
        self.assertEqual(rq.compare_reuse(prior, req(rev=2)), rq.DISTINCT)
        self.assertEqual(rq.compare_reuse(prior, req(request_id="alpha-20261013-2")), rq.DISTINCT)
        self.assertEqual(rq.compare_reuse(prior, req(request_id="beta-20261013-1")), rq.DISTINCT)

    # 5
    def test_a_relayed_request_within_scope_is_verified(self):
        first = self.assertVerdict(text(), GOOD, rq.VERIFIED)
        self.assertEqual(first, rq.Verdict(rq.VERIFIED, (), "m5r8w2"))
        # reposted under a new message ID: the same content, verified on the new post
        again = self.assertVerdict(text(), good(post=rq.Post("sam", "m9q4t1", True)), rq.VERIFIED)
        self.assertEqual(again.authority, "m9q4t1")
        self.assertEqual(rq.compare_reuse(text(), text()), rq.REPEAT)
        # the owner's client does not matter: only the audit trace differs, never the outcome
        for trace in ("pat-phone-thread", "pat-desktop-thread"):
            with self.subTest(trace=trace):
                self.assertEqual(rq.validate(text(trace=trace), GOOD), first)
                self.assertEqual(rq.validate(req(trace=trace), GOOD), first)
        # a later hop is authorized by the cited relay, whoever posts it
        for sender in ("alpha-manager", "alp-solver-2"):
            with self.subTest(hop_posted_by=sender):
                self.assertEqual(rq.validate(hop(), good(post=rq.Post(sender, "m6z1q7", True))),
                                 rq.Verdict(rq.VERIFIED, (), RELAY_ID))
        self.assertEqual(rq.validate(hop(trace="pat-thread-7"), good(post=MANAGER)).status, rq.VERIFIED)
        self.assertEqual(rq.validate(hop(), good(post=None)).status, rq.VERIFIED)  # the hop's own post is not proof
        self.assertEqual(rq.validate(hop(cite="m-relayed"), good(post=MANAGER)).status, rq.VERIFIED)
        # beta's request within the same grant; a request with no expiry needs no clock
        beta = text(request_id="beta-20261013-4", target="beta-repo", scope=("check-beta",))
        self.assertEqual(rq.validate(beta, GOOD).status, rq.VERIFIED)
        self.assertEqual(rq.validate(text(expiry=None), good(now=None)).status, rq.VERIFIED)

    # 6
    def test_a_trace_reference_or_sender_label_is_refused(self):
        sender = lambda account, verified=True: good(post=rq.Post(account, "m7", verified))  # noqa: E731
        cases = [
            ("a trace reference from a manager", text(trace="pat-thread-7"), sender("alpha-manager"),
             "trace-reference-only"),
            ("a trace reference from a worker", text(trace="pat-thread-7"), sender("alp-solver-2"),
             "trace-reference-only"),
            ("a sender label alone", text(), sender("alpha-manager"), "sender-label-only"),
            ("a manager naming sam", text(relay="sam"), sender("alpha-manager"), "relay-claim-mismatch"),
            ("a manager naming sam", text(relay="sam"), sender("alpha-manager"), "sender-label-only"),
            ("sam's name on an unverified post", text(), sender("sam", False), "relay-unverified"),
            ("a relay claim that is not the sender", text(relay="alp-solver-2"), sender("sam"),
             "relay-claim-mismatch"),
            ("a citation of a manager's post", text(cite="m-manager"), sender("alpha-manager"),
             "cited-author-not-assistant"),
            ("a citation of an unverified post", text(cite="m-unverified"), sender("alpha-manager"),
             "cited-author-unverified"),
            ("a citation of another hop", text(cite="m-later-hop"), sender("alpha-manager"),
             "cited-record-not-relay"),
            ("a citation of ordinary chat", text(cite="m-chat"), sender("alpha-manager"),
             "cited-content-malformed:bad-prefix"),
            ("a claim that sam relayed what a manager cited", hop(relay="alpha-manager"), sender("alpha-manager"),
             "relay-claim-mismatch"),
            ("sam's post naming another relay, posted directly", text(relay="alpha-manager"), GOOD,
             "relay-claim-mismatch"),
            ("a citation of that post, naming sam", hop(cite="m-bad-claim"), sender("alpha-manager"),
             "cited-relay-claim-mismatch"),
            ("a citation of that post, naming no one", text(cite="m-bad-claim"), sender("alpha-manager"),
             "cited-relay-claim-mismatch"),
        ]
        for label, wire, evidence, reason in cases:
            with self.subTest(case=label, reason=reason):
                self.assertVerdict(wire, evidence, rq.REFUSED, reason)
        # a trace on a verified assistant's own post is only audit context, and cannot refuse or verify alone
        self.assertEqual(rq.validate(text(trace="pat-thread-7"), GOOD).status, rq.VERIFIED)

    # 7
    def test_a_request_wider_than_the_grant_or_than_its_citation_is_refused(self):
        sam_posts = [
            ("scope wider than the grant", text(scope=("check-alpha", "check-gamma")), "scope-wider-than-grant"),
            ("action not granted", text(action="deploy"), "action-not-granted"),
            ("target not granted", text(target="gamma-repo"), "target-not-granted"),
            ("a granted gate dropped", text(gates=()), "gate-dropped"),
            ("another policy name", text(policy_name="other-grant"), "policy-mismatch"),
            ("another policy digest", text(policy_digest=OTHER_DIGEST), "policy-mismatch"),
        ]
        for label, wire, reason in sam_posts:
            with self.subTest(first_hop=label):
                self.assertVerdict(wire, GOOD, rq.REFUSED, reason)
        # listing more assistants never widens anything
        wide = good(assistants=frozenset({"sam", "alpha-manager", "alp-solver-2"}))
        self.assertVerdict(text(scope=("check-alpha", "check-gamma")), wide, rq.REFUSED, "scope-wider-than-grant")
        # a later hop keeps exactly the cited scope, gates, expiry and correlation
        later = [
            ("narrower scope", hop(scope=("check-alpha",)), "cited-scope-mismatch"),
            ("wider scope", hop(scope=("check-alpha", "check-beta", "report-alpha")), "cited-scope-mismatch"),
            ("gates dropped", hop(gates=()), "cited-gates-mismatch"),
            ("gates added", hop(gates=("owner-merge-gate", "review-gate")), "cited-gates-mismatch"),
            ("later expiry", hop(expiry="20261102T000000Z"), "cited-expiry-mismatch"),
            ("no expiry", hop(expiry=None), "cited-expiry-mismatch"),
            ("another request id", hop(request_id="alpha-20261013-2"), "cited-request-id-mismatch"),
            ("another revision", hop(rev=2), "cited-revision-mismatch"),
            ("another policy digest", hop(policy_digest=OTHER_DIGEST), "cited-policy-mismatch"),
            ("another operation", hop(op="op-9b1c"), "cited-digest-mismatch"),
            ("another granted action", hop(action="summarize"), "cited-digest-mismatch"),
        ]
        for label, wire, reason in later:
            with self.subTest(later_hop=label):
                self.assertVerdict(wire, good(post=MANAGER), rq.REFUSED, reason)

    # 8
    def test_evidence_that_cannot_be_established_is_unresolved(self):
        def failing(msgid):
            raise OSError("record unavailable")

        cases = [
            ("expiry unknown", text(), good(expiry=rq.UNKNOWN), "expiry-unknown"),
            ("revocation unknown", text(), good(revocation=rq.UNKNOWN), "revocation-unknown"),
            ("a status that is none of the three", text(), good(expiry="maybe"), "expiry-unknown"),
            ("lifecycle inputs omitted", text(), rq.Evidence(post=GOOD.post, assistants=GOOD.assistants,
                                                            lookup=GOOD.lookup, grant=GRANT, now=NOW),
             "revocation-unknown"),
            ("no time", text(), good(now=None), "time-unavailable"),
            ("a boolean for the time", text(), good(now=True), "time-unavailable"),
            ("no grant", text(), good(grant=None), "grant-evidence-unavailable"),
            ("no assistant list", text(), good(assistants=None), "assistants-unavailable"),
            ("no transport evidence", text(), good(post=None), "transport-evidence-unavailable"),
            ("a post with no message ID", text(), good(post=rq.Post("sam", "", True)),
             "transport-evidence-unavailable"),
            ("a post whose verification is unknown", text(), good(post=rq.Post("sam", "m7", None)),
             "transport-evidence-unavailable"),
            ("a cited record that cannot be found", hop(), good(post=MANAGER, lookup=lambda msgid: None),
             "cited-record-unavailable"),
            ("no lookup at all", hop(), good(post=MANAGER, lookup=None), "cited-record-unavailable"),
            ("a lookup that fails", hop(), good(post=MANAGER, lookup=failing), "cited-record-unavailable"),
            ("a trace beside an unavailable citation", hop(trace="pat-thread-7"),
             good(post=MANAGER, lookup=lambda msgid: None), "cited-record-unavailable"),
            ("no evidence at all", text(), None, "grant-evidence-unavailable"),
        ]
        for label, wire, evidence, reason in cases:
            with self.subTest(case=label, reason=reason):
                self.assertVerdict(wire, evidence, rq.UNRESOLVED, reason)
        # known lapses are refused, not unresolved
        self.assertVerdict(text(), good(expiry=rq.INVALID), rq.REFUSED, "grant-expired")
        self.assertVerdict(text(), good(revocation=rq.INVALID), rq.REFUSED, "revoked")
        # reaching the expiry is a rejection; one second earlier is not
        self.assertVerdict(text(), good(now=EXP_EPOCH), rq.REFUSED, "expired")
        self.assertVerdict(text(), good(now=EXP_EPOCH + 1), rq.REFUSED, "expired")
        self.assertVerdict(text(), good(now=EXP_EPOCH - 1), rq.VERIFIED)
        # a definite refusal stays refused beside unresolved evidence
        for label, wire, evidence, reason in (
                ("a wider scope, expiry and revocation unknown", text(scope=("check-gamma",)),
                 good(expiry=rq.UNKNOWN, revocation=rq.UNKNOWN), "scope-wider-than-grant"),
                ("known revocation, citation unavailable", hop(),
                 good(post=MANAGER, lookup=None, revocation=rq.INVALID), "revoked"),
                ("a trace from a manager, grant missing", text(trace="pat-thread-7"),
                 good(post=MANAGER, grant=None), "trace-reference-only"),
                ("an unverified author with no message ID", text(), good(post=rq.Post("sam", "", False)),
                 "relay-unverified"),
                ("a non-assistant sender with no message ID", text(), good(post=rq.Post("alpha-manager", "", True)),
                 "sender-label-only"),
                ("a wrong relay claim with no message ID", text(relay="alpha-manager"),
                 good(post=rq.Post("sam", "", True)), "relay-claim-mismatch")):
            with self.subTest(refused_over_unresolved=label):
                verdict = self.assertVerdict(wire, evidence, rq.REFUSED, reason)
                self.assertFalse([r for r in verdict.reasons if r.endswith(("-unknown", "-unavailable"))], verdict)


if __name__ == "__main__":
    unittest.main()
