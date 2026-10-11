# Chat outbox: requirement-to-test matrix (#19, PR #20)

This is the requirement-to-test matrix that #19's owner amendment 6 (F3,
item 20) requires. It maps every requirement, acceptance item, binding
correction and spec addition of #19, design revision 9's tests 1–48
([`chat_outbox_state.md`](chat_outbox_state.md), section 9), owner
amendment 6, and the code reviews' findings, to the named tests that assert
them, with a status for each. It describes PR #20 at `7439dd9`.

How to read it:
- Each row's **Requirement** or **Item** column (for design tests, the
  **Asserts** column, quoted from section 9) states what its named tests
  assert.
- **Tests** names test classes or methods. Appendix A lists every test method
  of each class named here, so each reference resolves to methods.
- **Status**: **COVERED** means a named test asserts it. **PARTIAL** means some
  of it is asserted, and the note says what is not. **MISSING** means no test
  asserts it. Process-only items (documentation, pull request text) are
  COVERED by that evidence, as the note says.
- **Failing first or control.** A note says "failing first" when the test
  failed on the commit before its fix, and "control" when it already passed
  there: a control is never failing-first evidence.
- Every example in the tests is invented: projects alpha and beta, people
  pat and sam, and agents such as alp-solver-2.

Module abbreviations, all under `packages/chat/scripts/tests/` unless stated:
- `resend`, `files`, `bridge`, `store`, `authority`, `crashes` and `regr` are
  `test_outbox_{resend,files,bridge,store,authority,crashes,regressions}`.
- `tb` is `test_bridge`, `sil` is `test_silence`, and `sg` is
  `test_skill_guidance`.
- `prov` is `packages/chat/tests/test_provenance`.
- `rb`, `rs` and `ri` are `packages/release/tests/test_build`, `test_stage`
  and `test_install`.

## 1. #19 body: requirements

| ID | Requirement | Tests | Status | Note |
|---|---|---|---|---|
| R1 | Per-part outcome: confirmed, uncertain or unsent; bounded per-part wait; slow-but-confirmed succeeds | resend.CommittedThenTimeoutTests (all 3); store.TransportOutcomeTests.test_unrelated_traffic_never_extends_a_parts_absolute_deadline; store.FinalityTests (all 4); resend.UnchangedPathTests.test_a_confirmed_post_exits_0_and_queues_nothing; resend.ReconciledAbsentTests.test_a_late_pong_without_an_error_is_confirmation | COVERED | |
| R2 | Never resend a confirmed part: pchat, any flush, restart, crash | resend.CommittedThenTimeoutTests.test_pchat_queues_only_the_unconfirmed_part_and_never_writes_it_again; resend.RestartTests (both); crashes.WriterSideTests.test_a2_before_and_after; resend.ClosedConnectionTests | COVERED | |
| R3 | Fixed parts; the delayed marker never changes a confirmed part; legacy rows and crash-left claim files delivered as today | resend.LegacyFlushTests (both); tb.OutboxTests.test_failures_are_requeued_and_crashed_claims_resumed; tb.OutboxTests.test_tag_and_reply_survive_the_outbox; crashes.BridgeSideTests.test_e4_before_and_after; resend.PartShapeTests.test_a_part_the_server_can_no_longer_carry_stays_unsent_and_visible | COVERED | The changed-capability part: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| R4 | Durable progress: in flight before writing, confirmed before the next; survives crash and restart; in flight after a crash is uncertain; nothing lost | crashes.WriterSideTests (all 11); crashes.BridgeSideTests (all 7); resend.RestartTests (both); tb.OutboxTests.test_post_queued_during_flush_survives | COVERED | |
| R5 | Reconciliation: Delivered, Absent, Unknown; 24-hour dead letter keeps texts and records | resend.ReconciledDeliveredTests; resend.ReconciledAbsentTests; resend.UnknownForADayTests; store.UniqueAttributionTests; store.FragmentTests; store.AccountingTests; resend.UntrackedSubstitutionTests; resend.SameFlushTests; resend.NegativeReconciliationTests (4 subtests) | COVERED | The four negative matches: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| R6 | Independent entries keep flowing, for another account **or the same one**; uncertain, waiting or failing | resend.IndependentDeliveryTests (both: another account only); authority.PausedClientTests; files.PausedLockHolderTests; bridge.BoundedSetupTests.test_another_entry_in_the_same_flush_still_gets_its_attempt; bridge.FlushContainmentTests; regr.IndependentFlowTests; resend.SameAccountFlowTests (both: uncertain on both transports, failing) | COVERED | Same account: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| R7 | Refusals not retried; dead letter lists confirmed parts | resend.ReconciledAbsentTests.test_a_fail_is_a_refusal_dead_lettered_and_never_retried; store.TransportOutcomeTests.test_a_fail_then_silence_is_a_refusal_kept_as_recorded; tb.SendMultilineTests.test_refusal_is_not_queued | COVERED | The bridge-side mid-post refusal is under 6087146049-S8. |
| R8 | `pchat status` separates waiting from awaiting reconciliation | resend.StatusTests; files.StatusReadTests (both) | COVERED | |
| R9 | No exactly-once claim; a pre-write failure queues the whole post as today | sg.GuidanceTests.test_the_queueing_guidance_describes_19s_behaviour; resend.UnchangedPathTests.test_a_server_that_cannot_be_reached_queues_the_whole_post_once; resend.FailureBeforeE1Tests (all 5) | COVERED | |
| R10 | Capture record: drift check, carried commits, provenance entries | prov (all 7) | COVERED | The drift-check run is PR-body evidence, not a test. |
| R11 | Guidance (SKILL.md), with test_skill_guidance passing | sg.GuidanceTests (all) | COVERED | |
| R12 | Contract docs through docs-push, linked | (process) | COVERED | Evidence: PR #20's Docs lines (D-72, D-73, chat_capture, release_contract, the outbox note at cab610c). |
| R13 | Privacy: invented data only | (process); rb.RefusalTests.test_private_data_in_the_payload_is_refused | COVERED | Evidence: every fixture is invented. |

## 2. #19 body: acceptance

| ID | Acceptance | Tests | Status | Note |
|---|---|---|---|---|
| Acc1 | Committed, then completion timeout: exit 3, only part 3 queued, no rewrites | resend.CommittedThenTimeoutTests.test_pchat_queues_only_the_unconfirmed_part_and_never_writes_it_again; regr.RepostTests (failing-first) | COVERED | |
| Acc2 | Reconciled as delivered (designated; the undesignated companion) | resend.ReconciledDeliveredTests (both) | COVERED | The negative matches are under 6087146049-S4. |
| Acc3 | Reconciled as absent (non-refusal error with its PONG), with companions | resend.ReconciledAbsentTests (all 4) | COVERED | |
| Acc4 | Still unknown: nothing written; visible in status | resend.CommittedThenTimeoutTests.test_an_uncertain_part_never_proves_absence_by_a_timeout; resend.StatusTests | COVERED | |
| Acc5 | Whole-post timeout in the bridge, reproducing the Background | resend.LegacyFlushTests.test_a_legacy_post_whose_final_confirmation_times_out_is_not_reposted; regr.RepostTests | COVERED | |
| Acc6 | Restart: new Record/Deliveries **and the bridge module reloaded** | resend.RestartTests.test_a_restarted_bridge_continues_from_the_recorded_progress; resend.RestartTests.test_a_reloaded_bridge_module_continues_from_the_recorded_progress | COVERED | The module reload: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| Acc7 | A crash between parts (as amended: the SQLite crash state) | resend.RestartTests.test_a_flush_killed_after_writing_a_part_recovers_from_the_database; crashes | COVERED | |
| Acc8 | Independent delivery during recovery (another account) | resend.IndependentDeliveryTests.test_another_accounts_entry_flows_while_one_awaits_its_check | COVERED | Same account is under R6. |
| Acc9 | Legacy and ack entries; pre-write failure; FAIL mid-post; slow-but-confirmed | resend.LegacyFlushTests.test_legacy_posts_and_acks_go_out_as_before; resend.UnchangedPathTests (all 3); resend.ReconciledAbsentTests.test_a_fail_is_a_refusal_dead_lettered_and_never_retried | COVERED | |
| Acc10 | Unknown for 24 hours (with A7's no-finality case) | resend.UnknownForADayTests; resend.PausedWriterTests.test_e7_makes_it_terminal_after_24_hours_but_leaves_it_writing | COVERED | |
| Gates | Both suites exit 0; the named modules run tests | CI build-test, and the local gates on 3.10 and 3.14 | COVERED | Evidence: CI. |

## 3. Review 6087146049 (original approval): corrections and spec additions

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| C1 | Failing-first only for defect regressions; compatibility checks stay passing | regr (failing-first); resend.UnchangedPathTests; resend.LegacyFlushTests | COVERED | |
| S1 | Outcome-aware queueing for every caller (notify, announce); exit codes, metadata, acks and silent-run suppression kept | resend.StorageFailureTests.test_an_agent_notice_does_the_same; resend.AnnounceStorageFailureTests; sil (all); tb.OutboxTests.test_tag_and_reply_survive_the_outbox | COVERED | |
| S2 | Absolute per-part deadline under unrelated traffic; partial write uncertain; FAIL then timeout; cleanup after confirmation | store.TransportOutcomeTests (all 4) | COVERED | These were controls: all passed when added (round 3). |
| S3 | Both transports, an uncertain middle part then unsent parts; a part that can no longer be carried unchanged is preserved visibly | resend.CommittedThenTimeoutTests.test_the_line_transport_behaves_the_same; store.TransportOutcomeTests.test_a_write_that_fails_part_way_leaves_that_part_uncertain (line: uncertain middle, then unsent); resend.PartShapeTests (both) | COVERED | Multiline uncertain middle and the changed-capability part: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| S4 | Absence covers the whole interval; reject wrong-account, unverified, wrong-channel and too-old matches; interrupted pagination is not a completed interval | bridge.CoverageTests (all 4); bridge.CatchUpIndexFailureTests; tb.CatchUpRecoveryTests; resend.NegativeReconciliationTests | COVERED | The four negative matches: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| S5 | One msgid never proves two parts; repeated identical texts | store.UniqueAttributionTests (all 3); resend.DirectPostDuringFlushTests | COVERED | |
| S6 | Crash safety of completion and transfer; no resurrection; stop on a failed progress record | crashes.WriterSideTests.test_e5_before_and_after; crashes.WriterSideTests.test_e3_before_and_after; regr.StorageFailureTests; resend.StorageFailureTests | COVERED | A6 superseded the claimed-file wording. |
| S7 | Independent delivery for the same account, and for a failing entry | resend.IndependentDeliveryTests.test_a_refused_and_a_failing_entry_never_stop_the_others (another account); resend.SameAccountFlowTests (both) | COVERED | Same account: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| S8 | Mid-post refusal through pchat **and the bridge**; status shows confirmed vs unconfirmed for transport dead letters | resend.ReconciledAbsentTests.test_a_fail_is_a_refusal_dead_lettered_and_never_retried; store.TransportOutcomeTests.test_a_fail_then_silence_is_a_refusal_kept_as_recorded; files.StatusReadTests.test_status_reads_the_highest_dead_letter_version_of_each_post; resend.BridgeRefusalTests (both transports) | COVERED | The bridge-side mid-post FAIL: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |

## 4. Owner amendment 1 (6090139511)

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| A1 / R14 | One SQLite authority; intent before bytes for every caller; files are boundaries; no joint TX | authority.OneOpenerTests; crashes.WriterSideTests.test_a1_before_and_after; authority.UnavailableTests | COVERED | |
| A2 | Delivered: designated senders only, every message accounted for | resend.ReconciledDeliveredTests; resend.UntrackedSubstitutionTests; store.UniqueAttributionTests | COVERED | |
| A3 | Absent needs attempt finality; complete record; every same-account message accounted for | resend.ReconciledAbsentTests; store.FinalityTests; store.AccountingTests; store.FragmentTests | COVERED | |
| A4 / B2 | Acceptance 3's fixture and companions | resend.ReconciledAbsentTests (all 4) | COVERED | |
| A5 | Acceptance 2: designated, with the undesignated companion | resend.ReconciledDeliveredTests (both) | COVERED | |
| A6 | Acceptance 7 recovers the SQLite crash state | resend.RestartTests.test_a_flush_killed_after_writing_a_part_recovers_from_the_database | COVERED | |
| A7 | Acceptance 10 without finality (bare timeout, broken connection) | resend.UnknownForADayTests; resend.ReconciledAbsentTests.test_a_crash_or_broken_connection_never_proves_absence | COVERED | |
| A8 | D1 not adopted | bridge.AnnounceTests.test_an_ordinary_post_killed_after_e1_is_still_abandoned; crashes.BridgeSideTests.test_e8_before_and_after | COVERED | |

## 5. Review 6090344060

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| C1–C3 | Finality and PONG wording; Delivered without finality; D-72/D-73 | resend.ReconciledAbsentTests; (docs) | COVERED | |
| S1 / B5 | One obligation per call through ambiguous commits | resend.FailureBeforeE1Tests (all 5) | COVERED | |
| S2 / B6 | Evidence kept for whole components; collection then delayed reconciliation | store.CollectionTests.test_collection_then_delayed_reconciliation_decides_the_same; resend.OutageTests | COVERED | |
| S3 / B7 | Bounded login, capability, SASL, welcome and channel waits; another entry still attempted | bridge.BoundedSetupTests (all 3) | COVERED | |

## 6. Owner amendment 2 (6090892929)

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| B1 / R15 / Acc11 | Release metadata; the built wheel; unchanged declarative content | rb.BuildTests.test_every_format_the_shipped_code_uses_is_declared; rb.test_the_versions_the_shipped_code_embeds_are_the_declared_ones; rb.test_release_json_is_unchanged_apart_from_19s_declarations; rb.test_any_other_declarative_change_is_caught; ri (all) | COVERED | |
| B3 | Missing finality blocks Absent, not Delivered | resend.ReconciledDeliveredTests; store.FinalityTests | COVERED | |
| B4 | Requirement 12 by D-72 and D-73 | (docs) | COVERED | |

## 7. Canonical approval 6091746432

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| C1 | Acceptance 11's "unchanged" reading | rb.test_release_json_is_unchanged_apart_from_19s_declarations | COVERED | |
| C2 | New locks and state files in the outbox-db path | rb.test_every_format_the_shipped_code_uses_is_declared | COVERED | |
| Gate | Implementation only after the owner's decision | (process) | COVERED | Owner decision, 13:39 UTC. |
| NoDesig | No designation interface | authority.OneOpenerTests.test_no_client_module_opens_the_database (static); authority.SandboxTests.test_no_designation_interface_exists | COVERED | Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| Sandbox | Every database a test opens is proved under `_isolation.CHAT_STATE` | regr (`_isolation.check_bound(pchat)`); authority.SandboxTests.test_every_database_opened_lies_under_the_sandbox (an audit hook on sqlite3.connect) | COVERED | Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| Gates | The release suite in the local gates | CI and the local gates | COVERED | |
| FF | Failing-first shows the defect itself | regr.RepostTests (4/8/12 and exit 3) | COVERED | |

## 8. Owner amendment 3 (6094379313) and acceptance 12 items 1–6

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| C1 | Bridge-only writer; bounded requests; commit before reply; idempotent ops; epochs; withdrawal | authority.OneOpenerTests; authority.RequestCrashTests (all 6); store.AttestationTests | COVERED | |
| C2 | No availability exemption | authority.PausedClientTests (all 8) | COVERED | |
| C3 / E4 | Queued while unavailable; nothing sent without a recorded attempt | authority.UnavailableTests (all 4) | COVERED | |
| C4 | outbox-authority format; paths | rb format tests | COVERED | |
| C5 | announce: no unbounded wait; fallback; the mark after durability; accounting on every part | bridge.AnnounceTests (all 5); bridge.DeliveredAnnouncementTests; store.AccountingTests; bridge.OwedAnnouncementExpiryTests | COVERED | The owed announcement through expiry: Failing first at 97db1bf. |
| Acc12.1–6 | A stopped client; lost replies and restarts; unavailable; announcement; single-line accounting; one opener | authority.PausedClientTests; authority.RequestCrashTests; authority.UnavailableTests; bridge.AnnounceTests; store.AccountingTests; authority.OneOpenerTests | COVERED | |

## 9. Review 6094453451, and owner amendment 4 (6097327208) with acceptance 12 items 7–12

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| (a)–(c) | Acceptance 11 generated fields; guidance; isolated fixture builds | rb.test_manifests_of_two_commits_differ_only_in_generated_fields; sg.test_the_queueing_guidance_describes_19s_behaviour; ri | COVERED | |
| (d) / D4 | A no-byte attestation survives recovery | store.AttestationTests.test_recovery_first_a8c_then_the_void_still_applies; store.AttestationTests.test_a_lost_a1_reply_then_a_closed_connection_then_the_handoff_delivers_once | COVERED | |
| (e) / D3 | An announcement's entry is an obligation | bridge.AnnounceTests.test_a_crash_after_e1_and_before_a1_is_adopted_after_the_restart_and_sent_once | COVERED | |
| D1 | Already durable obligations never wait for a lock; one fallback file per entry; import lock-free, bounded and fair; changed file held, never merged; removed only after the DB holds it | files.PausedLockHolderTests; files.PublicationTests; files.ImportBoundaryTests; files.ImportRotationTests; files.ListingFailureTests; files.ImportTests.test_a_changed_claim_prefix_is_held_and_never_merged; files.ChangedFallbackTests (3); files.LegacyClaimIntegrityTests | COVERED | The changed fallback file failed first at 97db1bf; the legacy claim's integrity at 10c6df5. |
| D2 | Queued means durable; legacy outbox read lock-free | files.PausedLockHolderTests; files.CleanupAfterPublicationTests | COVERED | |
| D5 | Release and guidance corrections | rb; sg | COVERED | |
| Acc12.7–12 | Already durable obligations; publications; import and crash boundaries; recovery before handoff; announcement never sent; release and guidance | files.PausedLockHolderTests; files.PublicationTests; files.ImportBoundaryTests; store.AttestationTests; bridge.AnnounceTests; rb; sg | COVERED | |

## 10. Review 6097396974, owner amendment 5 (6097617610), and approval 6097669785

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| E1 | Staging option A; #19's release refused at preflight | rs.ReleaseOf19Tests (all 3) | COVERED | |
| E2 | When a fallback file is published | resend.FailureBeforeE1Tests.test_a_failed_login_queues_by_q0_with_no_fallback_file; resend.FailureBeforeE1Tests.test_with_the_authority_unavailable_the_fallback_file_carries_it | COVERED | |
| E3 | outbox.lock still serializes dead letters | files.ExportDurabilityTests.test_a_delivery_dead_letter_waits_for_outbox_lock_and_stays_pending | COVERED | |
| E4 | "Send nothing" means no line without an attempt | authority.UnavailableTests | COVERED | |
| E5 | Module names; outbox embedded version; captured tests with provenance | rb.test_every_runtime_module_passes_the_unchanged_privacy_scan; rb.test_the_versions_the_shipped_code_embeds_are_the_declared_ones; sil; prov | COVERED | |
| E6 / Acc12.13–14 | Captured tests protected; "send nothing" per case | sil (all); authority.UnavailableTests | COVERED | |
| 6097669785 | Exit 3 after an acknowledged queue or handoff, with no file; both paths | resend.FailureBeforeE1Tests.test_a_failed_login_queues_by_q0_with_no_fallback_file; resend.StorageFailureTests.test_pchat_retries_then_hands_off_the_rest | COVERED | |

## 11. Design revision 9, section 9: tests 1–48, and both transports

| Test | Asserts (design section 9) | Tests | Status | Note |
|---|---|---|---|---|
| 1.1–1.4 | The round-5 regressions. Same flush. A has a confirmed part and an uncertain part of one text; B has a delivered part of the same text; the account is designated. One flush resolves them all, with no resend. Storage failure at confirmation. A2 fails after part 1 of 3, through `pchat`, `notify` and `announce`. … | resend.SameFlushTests; resend.StorageFailureTests; resend.DirectPostDuringFlushTests; resend.OutageTests | COVERED |  |
| 2 | Untracked substitution. Undesignated: an identical untracked message leaves U UNKNOWN until its 24-hour dead letter. Designated: an unexplained message suspends the designation and alerts once. | resend.UntrackedSubstitutionTests (both) | COVERED |  |
| 3 | Unique attribution. Covers identical texts within and across entries, the UNIQUE constraints, and a crash between components. | store.UniqueAttributionTests | COVERED |  |
| 4 | Finality. An error reply, and a late PONG during the drain, give finality. A bare timeout, a write error, a gone writer and a closed connection each give no finality, and R2 never applies. A FAIL during the drain is a refusal. | store.FinalityTests | COVERED |  |
| 5 | Fragments (F1). For a multi-line part with lines "north", "" and "south", each of these unexplained messages blocks R2 and R1: "north\n"; "south"; "\n"; "south\nnorth"; a joined non-adjacent concat piece; an unrelated text. A forced earlier part of the same post does not block. | store.FragmentTests | COVERED |  |
| 6 | Acknowledgements (F2). A failed ack, a restart, then A11 and a retry that succeeds. Also a crash before A11 and after it. | resend.AcknowledgementTests | COVERED |  |
| 7 | Durable claim (F3). A power loss after the rename without a directory `fsync`; after I2; after the unlink without a directory `fsync`. No occurrence is duplicated or lost. | files.DurableClaimTests | COVERED |  |
| 8 | Reopenable progress (F4). A delayed prefix, the import pass, then E8, G1 and H1: progress is kept, and H1 reopens the entry with its confirmed prefix. An unrelated abandoned entry is collected only after `import_epoch` advances. | files.ImportBoundaryTests.test_a_handoff_published_before_an_e8_mid_pass_is_applied_and_nothing_collected_early | COVERED |  |
| 9 | Export durability (F5). For X1, a power loss after the append and before the `fsync`. For X2, a power loss after the rename and before the directory `fsync`. A `Deliveries._dead` write concurrent with X1. A pending item that leaves after its ledger line. (Directory durability on recovery is test 22.) | files.ExportDurabilityTests | COVERED |  |
| 10 | Late outcomes (F6). A paused writer resumes after E7's export: version 2 is exported, and supersedes version 1, before G1 may collect anything. The entry stays terminal. | resend.LateOutcomeTests | COVERED |  |
| 11 | Failure before E1 (F7), and ambiguous creation commits. For `pchat post`, `pchat ack`, `notify` and `announce`: a failed login; a failed E1; a crash before and after Q0; Q0's fallback file, and power loss on either side of each step of its publication; an E1 commit that reports failure but lands. P finds *X* and hands it off. Restart, then import. … | resend.FailureBeforeE1Tests | COVERED |  |
| 12 | Crash at every boundary. One case per row of section 5.5. | crashes (every section 5.5 row, with CoverageMapTests) | COVERED |  |
| 13 | A paused writer, and the liveness probes. Never ended or retried. E7 at 24 hours. A missing pid, a changed start time and a changed boot id are each proof of a gone writer. Unreadable probes prove nothing. | resend.PausedWriterTests; resend.HostProbeTests | COVERED |  |
| 14 | Import. Duplicates, with or without the same explicit id, become distinct occurrences. A changed hash is held. Unsupported rows are held, with one alert row for their claim file (test 24). Fallback files, by section 6.4's single shape. … | files.ImportTests | COVERED |  |
| 15 | Collection. Messages near unresolved attempts are kept. Attributions go with their messages. Terminal rows stay until their current version is exported. Open-window dead attempts are kept. | store.CollectionTests | COVERED |  |
| 16 | Coverage. Each of these stops coverage, and R2 waits: KICK or PART, a disconnect, a stale reply, a failed V1, a checkpoint mismatch, a restart. | bridge.CoverageTests; bridge.CatchUpIndexFailureTests | COVERED |  |
| 17 | Independent flow. Another account, and the same account with another text, flow while one entry is undecided, refused or failing, while `L_outbox` is held by a paused process (tests 23 and 32), while a client is stopped at any point of a request (test 25), and while a writer is stopped in the middle of publishing … | resend.IndependentDeliveryTests; files.PausedLockHolderTests; authority.PausedClientTests; resend.SameAccountFlowTests | COVERED | Same account with another text: Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| 18 | Unchanged paths. Legacy and ack entries. A slow but confirmed post. Silent-run suppression. `pchat` exit codes and messages. The amended #19 acceptance 1–10. | resend.UnchangedPathTests; sil | COVERED |  |
| 19 | Collection, then delayed reconciliation. A confirmed attempt K has no msgid, and its message lies before the window of an unresolved attempt U, but K's window overlaps U's. Run G1 when K's message is older than `GC_MARGIN`, then reconcile U. K's message and attempt are kept, and U's verdict is the same as without G1. | store.CollectionTests.test_collection_then_delayed_reconciliation_decides_the_same | COVERED |  |
| 20 | Bounded setup. On an injected clock, the fake server trickles unrelated lines during each of these phases: capability negotiation; SASL; the welcome; NAMES or INVITE in channel creation. Each phase fails at its absolute deadline. Another entry in the same flush still gets its attempt. | bridge.BoundedSetupTests | COVERED |  |
| 21 | Release packaging (future, under R15). The release inventory test ties markers in the shipped code to the new format entries: `outbox.db` to the outbox store format, read and written; the fallback files to the outbox format version, read and written; versioned post dead letters to the delivery records version; … | rb | COVERED |  |
| 22 | Directory durability on recovery (design round 3, finding 1). The fake filesystem keeps a file's content and its directory entry apart, and a simulated power loss drops whatever was not synced. In each case, a process crash comes first, then recovery, then a power loss: Dead letters. … | files.ExportDurabilityTests | COVERED |  |
| 23 | A paused lock holder (design round 3, finding 2). With a fake lock table and an injected monotonic clock: A fake old-tool process holds `L_outbox` and never releases it. B's flush still runs IF, I1 and I2. It gives an unrelated open database entry for `#beta` its attempt, applies an E7 that falls due, and exports its alert row. … | files.PausedLockHolderTests | COVERED |  |
| 24 | Alerts, exactly one per event (design round 3, finding 3). For each kind (an E7 entry, a claim file with held rows, a changed claim file, and a suspended designation): a crash before the event's `TX`, then a restart: one row, one item; a crash after the `TX` and before X2: one item after restart; … | store.AlertTests | COVERED |  |
| 25 | A paused client (design round 4, finding 2). On an injected clock, with `sam`'s queued `#beta` entry due in the same flush, a fake client stops: before connecting; after half a frame; after a complete frame; while its reply is unread; after its A1 reply; while sending its part. … | authority.PausedClientTests | COVERED |  |
| 26 | Requests at every crash point. One case per row of section 5.5's request table: each op's reply lost after its commit, then a replay: `replay`, with the same state; an `error` reply treated as missing; a `conflict` that changes nothing; an authority restart between two requests of one call. … | authority.RequestCrashTests | COVERED |  |
| 27 | A12 and attested outcomes. A `void` applies only with the same writer, generation and `a1_nonce`, to an attempt still `writing` or ended by A8 or A8c, with no attribution, in an `open` entry (or one H1 reopens in the same `TX`). An attested outcome applies only to an attempt still `writing`. … | store.AttestationTests | COVERED |  |
| 28 | Authority unavailable: the material change. For `pchat post`, `pchat ack` and `notify`, test each of these: no socket; a socket path over the limit; a refused connect; a wrong peer uid; an unknown version; `busy`; `error`; no reply within `IPC_WAIT`. … | authority.UnavailableTests | COVERED |  |
| 29 | Announce fallback (design round 4, finding 1). A fake old tool holds `L_outbox` and never releases it. `announce`'s in-process E1 and Q0 fail before any byte. `announce` publishes a fallback file for *X* in `outbox.d/`, without touching `L_outbox`. `held_announced` is saved only afterwards. … | bridge.AnnounceTests | COVERED |  |
| 30 | Message accounting on every part (design round 4, finding 3). A single-line `Y` with finality, complete coverage and no match, plus one unexplained "beta status" message from the same account in `#alpha` inside `Y`'s window: UNKNOWN, never resent, dead-lettered at 24 hours. … | store.AccountingTests | COVERED |  |
| 31 | One opener. A second connection to the sandboxed database, while the authority runs, gets `SQLITE_BUSY` at once. A foreign holder at startup makes the start fail within `OPEN_WAIT`: no socket, status says why, the lock is untouched, and the start is retried next pass. … | authority.OneOpenerTests | COVERED |  |
| 32 | Already durable obligations behind a stopped lock holder. A fake old tool holds `L_outbox`, stopped half-way through a row, with no newline. Already durable are a published fallback file for `sam`'s `#beta` post and a complete legacy row for `alp-solver-2`. In the same flush: IF imports the file, and I1 and I2 import the complete row; … | files.PausedLockHolderTests | COVERED |  |
| 33 | Publications. With a fake filesystem that can drop unsynced changes, and an injected clock: A writer stopped before `link()`: its `tmp/` file is never imported; every other file and entry flows; nothing is reported. A crash after `link()` and before the directory `fsync`: the file may be imported; … | files.PublicationTests; files.ImportTests.test_an_unreadable_published_file_is_held_with_one_alert | COVERED |  |
| 34 | Import and crash boundaries. Crashes: before IF's `TX`, after it and before the unlink, and after the unlink and before the directory `fsync`. No obligation is lost or doubled. … | files.ImportBoundaryTests | COVERED |  |
| 35 | A no-byte attestation that recovery overtakes (correction (d)). A1 commits, and its reply is lost. The client closes its chat connection, and A8c ends the attempt (and, in a variant, the client exits and A8 does). Then the client's handoff arrives with the matching `void` attestation, once by `handoff` request and once by fallback file. … | store.AttestationTests | COVERED |  |
| 36 | An announcement after E1 and before A1 (correction (e)). The bridge is killed after an announcement's E1 commits, before A1, with `held_announced` unset and, in a variant, already saved. After the restart, E9 adopts *X*, and the announcement is delivered once. The re-raised announcement, with the same *X*, finds the obligation and adds no second entry. … | bridge.AnnounceTests | COVERED |  |
| 37 | Release packaging and guidance, as corrected (corrections (a)–(c)). These are future release-suite and guidance tests. Acceptance 11's comparison ignores the normally generated source, builder, release, artifact and hash fields, and passes across two commits. It fails when any declarative entry other than those section 10.1 lists changes. … | rb; sg; ri | COVERED |  |
| 38 | Staging, with #19's formats (option A). `test_stage`'s happy-path fixtures, built by the changed `test_stage.py` and `_staging.py`, use the live baseline formats, and still reach `staged`. Every existing protective staging test keeps its assertion. The refusal test. … | rs.ReleaseOf19Tests | COVERED |  |
| 39 | Release metadata. Every new runtime module's wheel path passes `STATE_NAMES` unchanged. A negative control named `outbox_*.py` is refused by the build. The `outbox` entry declares `embedded_version: true` with `field: "v"`. The format-marker test ties `"outbox/2"` in the shipped code to it. | rb.test_every_runtime_module_passes_the_unchanged_privacy_scan | COVERED |  |
| 40 | Captured tests, updated with provenance. `test_silence.py`, and every other captured test whose assertion encodes replaced behaviour, is updated in the pull request with a requirement 10 provenance entry. … | sil | COVERED |  |
| 41 | Intent before action. The intent is durable before the stop key, the typing or the submission key. With the intent's save failing, no key is sent, and the item is tried again at a later pass. | bridge.InterruptIntentTests.test_the_intent_is_durable_before_the_first_command; bridge.InterruptIntentTests.test_without_a_durable_intent_nothing_is_done | COVERED | Failing first at 97db1bf. |
| 42 | A crash at every interrupt boundary. A crash before the intent; after it and before the stop; after the stop; after the typing; after the submission and before its evidence is saved; after the evidence. After restart, every case from the intent on is held, never rung again. A crash before the intent is retried as today. | bridge.InterruptIntentTests.test_a_crash_at_any_boundary_after_the_intent_never_interrupts_again; bridge.InterruptIntentTests.test_a_crash_after_the_evidence_never_interrupts_again; bridge.InterruptIntentTests.test_a_crash_before_the_intent_is_retried_as_today | COVERED | Failing first at 97db1bf; the crashes before the intent and after the evidence are controls. |
| 43 | Save failures after delivery. One failed save, then a crash at once, before any later pass. Every save failing through the crash. A surviving ledger line for the same intent id, target, session and surface: reconciled as delivered, with no ring. … | bridge.InterruptOutcomeTests.test_save_failures_after_delivery_never_interrupt_again; bridge.InterruptOutcomeTests.test_with_no_surviving_proof_it_is_held_alerted_once_and_settled_by_the_acknowledgement; bridge.InterruptOutcomeTests.test_an_acknowledgement_while_work_is_owed_then_a_restart; bridge.AcknowledgementRecoveryTests | COVERED | Failing first at 97db1bf; the acknowledgement recovery cases at 10c6df5. |
| 44 | Three distinct outcomes. A proved no-action (a dialog, a draft or a browsing view seen before the intent; or no key sent) is retried as today. A partial step (a stop sent, then typing failed; typing without proved submission) is held. A proved submission waits for the acknowledgement. | bridge.InterruptOutcomeTests.test_a_proved_no_action_is_retried_and_a_partial_step_is_held; tb.InterruptKeystrokeTests (the dialog, draft and browsing holds before any key) | COVERED | Failing first at 97db1bf. |
| 45 | Proof that is not proof. A ledger line for another intent id, another target or surface, a reminder's line, an `ok` line without an intent id, and an unreadable ledger, each leave the item held. | bridge.InterruptOutcomeTests.test_proof_that_is_not_proof_leaves_it_held | COVERED | Failing first at 97db1bf. |
| 46 | An owed announcement at the acknowledgement wait. The announcement fails, and keeps failing, through the expiry: the item is dead-lettered truthfully, its announcement stays owed and is retried until durable, across a restart, with the same entry id, and the interrupt is never rung again. … | bridge.OwedAnnouncementExpiryTests | COVERED | Failing first at 97db1bf. |
| 47 | A changed fallback file. A file is imported, its removal fails, and its content is then changed outside this design, both as valid text and as bytes that do not decode. The next import holds the exact changed bytes with one alert, and removes the file only after that `TX` commits. … | files.ChangedFallbackTests (all 5) | COVERED | Failing first at 97db1bf, except the fairness and import_epoch case (a control). |
| 48 | Reconciliation's negative cases, and the same account. Through the caller and the flush: a message with the part's text from the wrong account, from an unverified sender, in the wrong channel, or from before the attempt's window never makes the part delivered or attributed, and never permits a blind resend; … | resend.NegativeReconciliationTests; resend.SameAccountFlowTests (both transports) | COVERED | Controls: added for amendment 6, passing at 97db1bf. |
| All | "Every scenario runs on both transports where they differ" | resend and regr (line and multiline); resend.PartShapeTests, resend.BridgeRefusalTests, resend.SameAccountFlowTests (both transports) | COVERED | Every scenario where the transports differ (part writing and its outcome) runs on both. The store, files and interrupt tests exercise no transport difference: justified, no further test. |

## 12. Owner amendment 6

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| F1.intent | Intent durable before the first physical step; none if it cannot be recorded (test 41) | bridge.InterruptIntentTests.test_the_intent_is_durable_before_the_first_command; bridge.InterruptIntentTests.test_without_a_durable_intent_nothing_is_done | COVERED | Failing first at 97db1bf. |
| F1.held | Unknown or partial outcome held, never repeated; recovery from `intent` is held (test 42) | bridge.InterruptIntentTests.test_a_crash_at_any_boundary_after_the_intent_never_interrupts_again; bridge.InterruptOutcomeTests.test_a_proved_no_action_is_retried_and_a_partial_step_is_held; bridge.ReplayedInterruptTests.test_a_replayed_interrupt_keeps_one_obligation_whatever_its_outcome | COVERED | Failing first at 97db1bf; the replay case at 5aec2b8. |
| F1.crash1 | One failed delivered-state save, then a crash at once (test 43) | bridge.InterruptOutcomeTests.test_save_failures_after_delivery_never_interrupt_again (persistent=False) | COVERED | Failing first at 97db1bf. |
| F1.crashN | Saves failing persistently through the crash (test 43) | bridge.InterruptOutcomeTests.test_save_failures_after_delivery_never_interrupt_again (persistent=True) | COVERED | Failing first at 97db1bf. |
| F1.ledger | A surviving exact-identity ledger record reconciles with no ring (test 43) | bridge.InterruptOutcomeTests.test_save_failures_after_delivery_never_interrupt_again; bridge.InterruptOutcomeTests.test_proof_that_is_not_proof_leaves_it_held (exact) | COVERED | Failing first at 97db1bf. |
| F1.noproof | No surviving proof: held, one alert, then acknowledgement or a truthful dead letter (test 43) | bridge.InterruptOutcomeTests.test_with_no_surviving_proof_it_is_held_alerted_once_and_settled_by_the_acknowledgement; bridge.InterruptOutcomeTests.test_a_proved_no_action_is_retried_and_a_partial_step_is_held (the truthful dead letter) | COVERED | Failing first at 97db1bf. |
| F1.bounds | A crash before and after the intent, and at each irreversible boundary (stop, typing, submission) (test 42) | bridge.InterruptIntentTests.test_a_crash_at_any_boundary_after_the_intent_never_interrupts_again (each of 5 commands, and after the submission); bridge.InterruptIntentTests.test_a_crash_before_the_intent_is_retried_as_today; bridge.InterruptIntentTests.test_a_crash_after_the_evidence_never_interrupts_again | COVERED | Failing first at 97db1bf. The crashes before the intent and after the evidence are controls. |
| F1.outcomes | Proved no-action vs partial vs delivered-not-acknowledged (test 44) | bridge.InterruptOutcomeTests.test_a_proved_no_action_is_retried_and_a_partial_step_is_held (no-action; typing mismatch; Enter acts but fails; failure after the Escape) | COVERED | Failing first at 97db1bf. |
| F1.mismatch | Ambiguous, mismatched or unreadable proof is unknown (test 45) | bridge.InterruptOutcomeTests.test_proof_that_is_not_proof_leaves_it_held (another intent, target, surface; no intent; not ok; unreadable) | COVERED | Failing first at 97db1bf (the case list includes a reminder's line). |
| F1.stays | Identity, the draft and dialog guards, acknowledgement, truthful dead letters | tb.InterruptKeystrokeTests; tb.InterruptRoutingTests (all 5); bridge.InterruptOutcomeTests.test_with_no_surviving_proof_it_is_held_alerted_once_and_settled_by_the_acknowledgement | COVERED | Baseline guards; truthful outcome-unknown dead letters are now asserted (F1.noproof). |
| F2(a) | An owed announcement through acknowledgement expiry and dead letter, then recovery, with no ring (test 46) | bridge.OwedAnnouncementExpiryTests.test_an_owed_announcement_survives_the_expiry_and_a_restart | COVERED | Failing first at 97db1bf. |
| F2(b) | A changed fallback file: exact bytes held before unlink; a failed or cancelled hold keeps the file; restart and power loss (test 47) | files.ChangedFallbackTests (exact bytes, valid and invalid; failed or cancelled hold; power loss between the hold and the removal); files.ChangedFallbackTests.test_a_changed_file_keeps_import_fair_and_its_pass_accounting; files.LegacyClaimIntegrityTests.test_a_fallback_file_rewritten_between_its_import_and_its_removal_is_held | COVERED | Failing first at 97db1bf, except the fairness and import_epoch case (a control). The removal after a rewrite between import and removal failed first at 10c6df5. |
| F2(c) | Negative matches rejected; a same-account entry flows while the first is uncertain or failing (test 48) | resend.NegativeReconciliationTests; resend.SameAccountFlowTests (both transports) | COVERED | Added for amendment 6; a control (passes at 97db1bf: the behaviour was right, the test was missing). |
| F3.20 | The matrix itself | this file | COVERED | |

## 13. Scope approval 6102156233: the binding correction and two additions

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| SA.corr | Recovery holds an interrupt still in intent without surviving success proof; durable accept or settled states kept; exact submission proof reconciles; no repeated interrupt | bridge.InterruptIntentTests.test_a_crash_at_any_boundary_after_the_intent_never_interrupts_again; bridge.InterruptOutcomeTests.test_proof_that_is_not_proof_leaves_it_held; bridge.InterruptOutcomeTests.test_save_failures_after_delivery_never_interrupt_again | COVERED | Failing first at 97db1bf. |
| SA.add1 | The intent gate covers every agent-affecting command, the preparatory Escape and draft cleanup included; a failed or unclear command never proves no action (a failure after the Escape; an Enter that acts but reports failure) | bridge.InterruptIntentTests.test_the_intent_is_durable_before_the_first_command; bridge.InterruptOutcomeTests.test_a_proved_no_action_is_retried_and_a_partial_step_is_held | COVERED | Failing first at 97db1bf. |
| SA.add2 | Ledger, announcement and pending-save failures apart; the acknowledgement while work is owed, then a restart at once; each obligation kept with its identity; the settled item kept until durable; no further interrupt | bridge.InterruptOutcomeTests.test_an_acknowledgement_while_work_is_owed_then_a_restart (the acknowledgement's save working and failing); bridge.InterruptOutcomeTests.test_save_failures_after_delivery_never_interrupt_again; bridge.InterruptOutcomeTests.test_with_no_surviving_proof_it_is_held_alerted_once_and_settled_by_the_acknowledgement; bridge.OwedAnnouncementExpiryTests; bridge.AcknowledgementRecoveryTests; bridge.LiveAcknowledgementRetryTests | COVERED | Failing first at 97db1bf; the acknowledgement recovery cases at 10c6df5, and the live retry at 5aec2b8. |

## 14. Code review 6102471519 (slot 9) and the final re-check

All failed first at `10c6df5` except where the note says control.

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| K1 | Acknowledgement recovery finds every recipient of one interrupt message by its own acknowledgement (message and recipient), so none gets a false unacknowledged dead letter | bridge.AcknowledgementRecoveryTests.test_every_recipient_of_one_interrupt_is_recovered_after_a_failed_settling_save | COVERED | |
| K2 | Acknowledgement recovery is complete only once it has read the file; a failed read is retried at a later pass, and a malformed line is skipped while the valid records are kept | bridge.AcknowledgementRecoveryTests.test_a_failed_read_is_retried_at_a_later_pass_and_a_malformed_line_is_skipped | COVERED | |
| K3 | A closed legacy claim is removed only while its length and sha256 are the committed ones; a rewritten one is held with its exact bytes, alerted once, never merged, and kept | files.LegacyClaimIntegrityTests.test_a_rewritten_closed_claim_is_held_with_its_bytes_and_kept; files.LegacyClaimIntegrityTests.test_an_unchanged_closed_claim_is_still_removed | COVERED | The unchanged claim's removal is a control. |
| K4 | At the final read (I2), every unterminated last line is held as unreadable, never imported, even when it is valid JSON | files.LegacyClaimIntegrityTests.test_an_unterminated_last_line_is_held_even_when_it_is_valid_json (valid JSON; not JSON) | COVERED | |
| K5 | This matrix, as public documentation linked from the pull request | (process) | COVERED | Evidence: this file, and PR #20's Docs line for it. |
| RC1 | Re-check, a lookup by message alone where one message has several recipients: an owner alert's dead-letter identity carries its recipient and the alert, so alerts for two recipients of one message, or two alerts for one, are never collapsed into one record | bridge.AlertIdentityTests.test_two_recipients_alerts_are_each_dead_lettered | COVERED | |
| RC2 | Re-check, a removal that does not re-validate the bytes: a legacy claim rewritten between its final import and its removal is kept, then held with its exact bytes | files.LegacyClaimIntegrityTests.test_a_claim_rewritten_between_its_final_import_and_its_removal_is_held | COVERED | |
| RC3 | Re-check, a removal that does not re-validate the bytes: a fallback file rewritten between its import and its removal is kept, then held with its exact bytes (as F2(b)) before removal | files.LegacyClaimIntegrityTests.test_a_fallback_file_rewritten_between_its_import_and_its_removal_is_held | COVERED | |

## 15. Code review 6102730761 (slot 10)

All failed first at `5aec2b8` except where the note says control.

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| L1 | A server message handled again on replay (its log failed, or a crash came between queueing and logging) keeps ONE interrupt obligation per message and recipient: the existing one keeps its identity, intent, phase and owed work, in each outcome (not yet rung, possibly acted, held, delivered, settled), and the recipient is never interrupted twice | bridge.ReplayedInterruptTests.test_a_replayed_interrupt_keeps_one_obligation_whatever_its_outcome | COVERED | |
| L2 | Another recipient of the same message stays independent | bridge.ReplayedInterruptTests.test_another_recipient_of_the_same_message_stays_independent | COVERED | |
| L3 | With no pending item and an unreadable ledger, a replayed interrupt is a possible repeat: held, never rung | bridge.ReplayedInterruptTests.test_a_replay_with_an_unreadable_ledger_is_never_rung | COVERED | |
| L4 | An ordinary wake-up keeps its documented baseline: a replay may duplicate it, never lose it | bridge.ReplayedInterruptTests.test_an_ordinary_wake_up_keeps_its_baseline | COVERED | A control. |
| L5 | A live acknowledgement whose settling fails in the ledger is settled by a later pass of the same bridge once the ledger works, with its owed work kept: no replay, no second interrupt or acknowledgement, no false dead letter | bridge.LiveAcknowledgementRetryTests.test_a_live_acknowledgement_is_settled_after_a_transient_ledger_failure (with and without an owed announcement) | COVERED | |

## 16. Code review 6103073520 (slot 11) and the bounded check

All failed first at `e4dc48a`.

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| M1 | An interrupt is queued only once its save succeeds: a failed save keeps nothing of it in memory, the message is not logged or checkpointed, and the same bridge's replay queues it again, so after a crash exactly one interrupt is queued and none is lost | bridge.QueuedOnlyWhenSavedTests.test_a_failed_queue_save_then_a_same_instance_replay_then_a_crash (one and two failed saves) | COVERED | |
| M2 | A failed save of a held replay leaves no alert behind | bridge.QueuedOnlyWhenSavedTests.test_a_failed_save_of_a_held_replay_leaves_no_alert_behind | COVERED | Bounded check. |
| M3 | An acknowledgement recorded before its instruction is replayed from history settles the interrupt once it is queued: never rung, no false dead letter, with a readable ledger and with an unreadable one (held) | bridge.AcknowledgementBeforeHistoryTests.test_an_acknowledgement_before_its_instruction_is_replayed | COVERED | |
| M4 | An acknowledgement counts as recorded only once its line is written, so a failed append is written when the message is replayed | bridge.AcknowledgementDurabilityTests.test_a_failed_acknowledgement_append_is_written_on_replay | COVERED | Bounded check. |

## 17. Code review 6103285015 (slot 12)

All failed first at `ab79c93` except where the note says otherwise.

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| N1 | A pending state that cannot be read, or cannot be parsed, is never an empty queue: with a possibly acted intent saved and the exact instruction replayed, no agent is touched, the saved bytes are never replaced, the message is left for replay, and once the state can be read the original intent is held | bridge.UnreadablePendingTests.test_a_possibly_acted_intent_survives_an_unreadable_pending_state (a read error; a parse error) | COVERED | |
| N2 | A missing pending file is an empty queue, and a new interrupt is delivered | bridge.UnreadablePendingTests.test_a_missing_pending_file_is_an_empty_queue | COVERED | A control. |
| N3 | An acknowledgement is durable (file, then directory) before it counts as recorded: after its settling save fails and the storage loses everything unsynced, the restarted bridge settles the interrupt by it, with its owed work kept, never rung and never falsely dead-lettered | bridge.AcknowledgementPowerLossTests.test_an_acknowledgement_survives_a_failed_settling_save_and_a_power_loss (with and without an owed announcement) | COVERED | |
| N4 | A failed acknowledgement append is retried by the replayed message (the durable append, on the live and replay path alike) | bridge.AcknowledgementDurabilityTests.test_a_failed_acknowledgement_append_is_written_on_replay | COVERED | From section 16 (M4); it now injects its failure at the durable append. Not failing-first evidence for this round. |

## 18. Code review 6103685016 (CODE13)

Failed first at `654b81e` except where the note says control.

| ID | Item | Tests | Status | Note |
|---|---|---|---|---|
| Q1 | While acks.jsonl cannot be read, a ready interrupt whose acknowledgement is already saved there is deferred: no Escape or Enter, its obligation kept, independent work going on; once the file can be read it is settled by that acknowledgement, never rung | bridge.UnreadableAcknowledgementsTests.test_a_ready_interrupt_is_deferred_until_its_saved_acknowledgement_can_be_read | COVERED | |
| Q2 | While acks.jsonl cannot be read, an expired delivered interrupt is neither dead-lettered as unacknowledged nor removed (an elapsed deadline is no substitute for the check), independent work going on; once the file can be read it is settled, its owed work kept | bridge.UnreadableAcknowledgementsTests.test_an_expired_interrupt_is_not_dead_lettered_while_its_acknowledgement_cannot_be_read (with and without an owed announcement) | COVERED | |
| Q3 | A readable file with no acknowledgement for an expired interrupt: dead-lettered as before | bridge.UnreadableAcknowledgementsTests.test_an_expired_interrupt_with_no_acknowledgement_is_dead_lettered_as_before | COVERED | A control. |
| Q4 | No acks.jsonl at all is no acknowledgement, never an unreadable one: a ready interrupt is delivered | bridge.UnreadableAcknowledgementsTests.test_with_no_acknowledgement_file_a_ready_interrupt_is_delivered | COVERED | A control. |

## 19. Code review 6103950289 (CODE14) and the bounded recovery-input map

The bridge's recovery readers of the pending state, `acks.jsonl` and the
delivery ledger, and first-start fallback publication: each failure
condition, the invariant it keeps, and the named test. Only a genuinely
missing file is absence; any other failure to inspect or read is unknown.
"Stat or inspection failure" is tested as Python 3.14 shows it, on both
supported Pythons: `Path.exists()` reports False and the read raises. This
map is bounded to these readers; it is not a certification of every input.
`pchat`'s own reads of `pending.json` are for display only and out of scope.

| ID | Reader (file: use) | Condition | Invariant | Tests | Status | Note |
|---|---|---|---|---|---|---|
| V1 | `pending.json`: `Deliveries._load` (the queue) | missing | an empty queue | bridge.UnreadablePendingTests.test_a_missing_pending_file_is_an_empty_queue | COVERED | Control. |
| V2 | `pending.json`: `Deliveries._load` | stat or inspection failure | unloaded: never replaced, nothing queued or delivered, read again each pass | bridge.RecoveryInputTests.test_pending_state_with_a_stat_failure_stays_unloaded | COVERED | Control (already read directly). |
| V3 | `pending.json`: `Deliveries._load` | open or read failure | unloaded, as V2 | bridge.UnreadablePendingTests.test_a_possibly_acted_intent_survives_an_unreadable_pending_state (read error) | COVERED | Failing first at `ab79c93`. |
| V4 | `pending.json`: `Deliveries._load` | parse failure (or not a list of items) | unloaded, as V2 | bridge.UnreadablePendingTests.test_a_possibly_acted_intent_survives_an_unreadable_pending_state (parse error) | COVERED | Failing first at `ab79c93`. |
| V5 | `acks.jsonl`: `Acks._load` (the recorded set) | missing | an empty set: the first acknowledgement is recorded | bridge.AcknowledgementDurabilityTests.test_a_failed_acknowledgement_append_is_written_on_replay | COVERED |  |
| V6 | `acks.jsonl`: `Acks._load` | stat or inspection failure | unknown: nothing recorded or settled as if empty; read again before each record | bridge.RecoveryInputTests.test_an_unreadable_acknowledgement_set_never_records_or_settles_as_if_empty (stat) | COVERED | Failing first at `5f87d47`. |
| V7 | `acks.jsonl`: `Acks._load` | open or read failure | unknown, as V6 | bridge.RecoveryInputTests.test_an_unreadable_acknowledgement_set_never_records_or_settles_as_if_empty (read) | COVERED | Failing first at `5f87d47` (the set could not even be built). |
| V8 | `acks.jsonl`: `Acks._load` | a torn or malformed line | skipped; the other lines kept | bridge.RecoveryInputTests.test_every_recovery_reader_skips_a_torn_line_and_keeps_the_rest | COVERED | The existing rule. |
| V9 | `acks.jsonl`: `recovered_acknowledgements` (settling) | missing | no acknowledgement: interrupts proceed | bridge.UnreadableAcknowledgementsTests.test_with_no_acknowledgement_file_a_ready_interrupt_is_delivered | COVERED | Control. |
| V10 | `acks.jsonl`: `recovered_acknowledgements` | stat or inspection failure | the interrupts it could settle are deferred (no key, no dead letter, no removal); settled once readable | bridge.RecoveryInputTests.test_acknowledgement_recovery_with_a_stat_failure_defers_a_ready_interrupt; bridge.RecoveryInputTests.test_acknowledgement_recovery_with_a_stat_failure_keeps_an_expired_interrupt | COVERED | Failing first at `5f87d47`. |
| V11 | `acks.jsonl`: `recovered_acknowledgements` | open or read failure | deferred, as V10 | bridge.UnreadableAcknowledgementsTests.test_a_ready_interrupt_is_deferred_until_its_saved_acknowledgement_can_be_read; bridge.UnreadableAcknowledgementsTests.test_an_expired_interrupt_is_not_dead_lettered_while_its_acknowledgement_cannot_be_read | COVERED | Failing first at `654b81e`. |
| V12 | `acks.jsonl`: `recovered_acknowledgements` | a torn or malformed line | skipped; the valid records used | bridge.AcknowledgementRecoveryTests.test_a_failed_read_is_retried_at_a_later_pass_and_a_malformed_line_is_skipped | COVERED | Failing first at `10c6df5`. |
| V13 | delivery ledger: `_proof` (submission proof) | missing, or no matching line | no proof: held | bridge.InterruptIntentTests.test_a_crash_at_any_boundary_after_the_intent_never_interrupts_again (no ledger file yet); bridge.InterruptOutcomeTests.test_with_no_surviving_proof_it_is_held_alerted_once_and_settled_by_the_acknowledgement (no matching line) | COVERED |  |
| V14 | delivery ledger: `_proof` | stat or inspection failure | no proof: held; reconciled once readable | bridge.RecoveryInputTests.test_proof_with_a_ledger_stat_failure_is_no_proof_until_it_can_be_read | COVERED | Control (already conservative). |
| V15 | delivery ledger: `_proof` | open or read failure | no proof: held | bridge.InterruptOutcomeTests.test_proof_that_is_not_proof_leaves_it_held (unreadable) | COVERED |  |
| V16 | delivery ledger: `_proof` | a torn or malformed line | skipped; a valid proof line still counts | bridge.RecoveryInputTests.test_every_recovery_reader_skips_a_torn_line_and_keeps_the_rest | COVERED | The existing rule. |
| V17 | delivery ledger: `_earlier_interrupt` (replay detection) | missing | no earlier obligation: the interrupt is queued | bridge.UnreadableAcknowledgementsTests.test_with_no_acknowledgement_file_a_ready_interrupt_is_delivered | COVERED | Control. |
| V18 | delivery ledger: `_earlier_interrupt` | stat or inspection failure | a possible repeat: held, never rung | bridge.RecoveryInputTests.test_a_replay_with_a_ledger_stat_failure_is_never_rung | COVERED | Failing first at `5f87d47`. |
| V19 | delivery ledger: `_earlier_interrupt` | open or read failure | held, as V18 | bridge.ReplayedInterruptTests.test_a_replay_with_an_unreadable_ledger_is_never_rung | COVERED | Failing first at `5aec2b8`. |
| V20 | delivery ledger: `_earlier_interrupt` | a torn or malformed line | skipped; a valid earlier line still counts | bridge.RecoveryInputTests.test_every_recovery_reader_skips_a_torn_line_and_keeps_the_rest | COVERED | The existing rule. |
| V21 | delivery ledger: `ledger_has` (alert export) | missing | not yet exported: queued once | store.AlertTests.test_an_expired_entry_raises_one_row_and_one_pending_item | COVERED |  |
| V22 | delivery ledger: `ledger_has` | stat or inspection failure | unknown: not exported now, no second push; exported once readable | bridge.RecoveryInputTests.test_alert_export_with_a_ledger_stat_failure_queues_no_second_push (stat) | COVERED | Failing first at `5f87d47`. |
| V23 | delivery ledger: `ledger_has` | open or read failure | unknown, as V22 | bridge.RecoveryInputTests.test_alert_export_with_a_ledger_stat_failure_queues_no_second_push (read) | COVERED | Control. |
| V24 | delivery ledger: `ledger_has` | a torn or malformed line (including JSON that is not an object) | skipped; a valid line still counts | bridge.RecoveryInputTests.test_every_recovery_reader_skips_a_torn_line_and_keeps_the_rest | COVERED | Failing first at `5f87d47`: a JSON line that was not an object raised. |
| V25 | fallback publication: `publish_fallback` (first start) | the state directory, or it and its parent, missing; no authority | created (default mode), each new entry synced in its parent; one durable file per call under its original id; imported once | resend.FirstStartTests.test_a_first_post_and_acknowledgement_before_any_start (one and two missing levels; a post and an acknowledgement) | COVERED | Failing first at `5f87d47`. |
| V26 | fallback publication: `publish_fallback` | the tree cannot be made (a parent is a file) | fails truthfully: nothing reported queued | resend.FirstStartTests.test_a_publication_that_cannot_be_made_reports_nothing_queued | COVERED | Control. |

## Counts

| Section | COVERED | PARTIAL | MISSING |
|---|---|---|---|
| 1. #19 body: requirements | 13 | 0 | 0 |
| 2. #19 body: acceptance | 11 | 0 | 0 |
| 3. Review 6087146049 (original approval): corrections and spec additions | 9 | 0 | 0 |
| 4. Owner amendment 1 (6090139511) | 8 | 0 | 0 |
| 5. Review 6090344060 | 4 | 0 | 0 |
| 6. Owner amendment 2 (6090892929) | 3 | 0 | 0 |
| 7. Canonical approval 6091746432 | 7 | 0 | 0 |
| 8. Owner amendment 3 (6094379313) and acceptance 12 items 1–6 | 6 | 0 | 0 |
| 9. Review 6094453451, and owner amendment 4 (6097327208) with acceptance 12 items 7–12 | 7 | 0 | 0 |
| 10. Review 6097396974, owner amendment 5 (6097617610), and approval 6097669785 | 7 | 0 | 0 |
| 11. Design revision 9, section 9: tests 1–48, and both transports | 49 | 0 | 0 |
| 12. Owner amendment 6 | 14 | 0 | 0 |
| 13. Scope approval 6102156233: the binding correction and two additions | 3 | 0 | 0 |
| 14. Code review 6102471519 (slot 9) and the final re-check | 8 | 0 | 0 |
| 15. Code review 6102730761 (slot 10) | 5 | 0 | 0 |
| 16. Code review 6103073520 (slot 11) and the bounded check | 4 | 0 | 0 |
| 17. Code review 6103285015 (slot 12) | 4 | 0 | 0 |
| 18. Code review 6103685016 (CODE13) | 4 | 0 | 0 |
| 19. Code review 6103950289 (CODE14) and the bounded recovery-input map | 26 | 0 | 0 |
| **Total (192 rows)** | **192** | **0** | **0** |

History: at `97db1bf`, before amendment 6's round, 104 of the then 130 rows
were COVERED, 15 PARTIAL and 11 MISSING. That round closed them and added
section 13. The final round added design tests 41–48 as their own rows in
section 11, and section 14. The recovery rounds added sections 15 and 16, the durability round section 17, the acknowledgement-read round section 18, and the systematic round section 19.

## Appendix A. The test methods of every class named above

Generated from the test tree at `7439dd9`. A module named on its own (such as `rb` or `sil`) stands
for all of its classes, listed here.

### `resend`: `packages/chat/scripts/tests/test_outbox_resend.py`

- **AcknowledgementTests**: `test_a_timed_out_ack_is_retired_and_retried_by_the_bridge`, `test_a_restart_between_the_failure_and_the_retry_changes_nothing`
- **AnnounceStorageFailureTests**: `test_the_bridges_announcement_does_the_same`, `test_pchat_retries_then_hands_off_the_rest`, `test_an_outcome_never_recorded_travels_as_an_attested_outcome`, `test_with_the_handoff_unanswered_too_the_fallback_file_carries_the_outcome`, `test_an_agent_notice_does_the_same`
- **BridgeRefusalTests**: `test_a_mid_post_fail_in_the_bridges_flush`
- **ClosedConnectionTests**: `test_a_403_recorded_only_on_a_retry_is_handed_off_and_delivered_once`
- **CommittedThenTimeoutTests**: `test_pchat_queues_only_the_unconfirmed_part_and_never_writes_it_again`, `test_the_line_transport_behaves_the_same`, `test_an_uncertain_part_never_proves_absence_by_a_timeout`
- **DirectPostDuringFlushTests**: `test_a_direct_posts_message_never_satisfies_an_older_part_too`
- **FailureBeforeE1Tests**: `test_a_failed_login_queues_by_q0_with_no_fallback_file`, `test_with_the_authority_unavailable_the_fallback_file_carries_it`, `test_an_e1_commit_whose_reply_is_lost_is_handed_off_not_duplicated`, `test_a_q0_commit_whose_reply_is_lost_plus_its_fallback_file_is_one_entry`, `test_two_calls_with_identical_text_are_two_entries`
- **FirstStartTests**: `test_a_first_post_and_acknowledgement_before_any_start`, `test_a_publication_that_cannot_be_made_reports_nothing_queued`
- **HostProbeTests**: `test_only_values_known_on_both_sides_are_compared`, `test_malformed_identities_prove_nothing_and_raise_nothing`, `test_a_socket_table_that_cannot_be_read_proves_nothing`, `test_a_start_time_is_read_from_one_source_per_platform`
- **IndependentDeliveryTests**: `test_another_accounts_entry_flows_while_one_awaits_its_check`, `test_a_refused_and_a_failing_entry_never_stop_the_others`
- **LateOutcomeTests**: `test_a_late_outcome_exports_a_superseding_version`
- **LegacyFlushTests**: `test_a_legacy_post_whose_final_confirmation_times_out_is_not_reposted`, `test_legacy_posts_and_acks_go_out_as_before`
- **NegativeReconciliationTests**: `test_wrong_account_unverified_wrong_channel_and_too_early_never_deliver`
- **OutageTests**: `test_evidence_survives_a_26_hour_outage`
- **PartShapeTests**: `test_an_uncertain_middle_part_then_unsent_parts_on_both_transports`, `test_a_part_the_server_can_no_longer_carry_stays_unsent_and_visible`
- **PausedWriterTests**: `test_a_paused_writer_is_never_ended_or_retried`, `test_each_proof_of_a_gone_writer_ends_it_without_finality`, `test_missing_recorded_identity_proves_nothing`, `test_unreadable_probes_prove_nothing`, `test_e7_makes_it_terminal_after_24_hours_but_leaves_it_writing`
- **ReconciledAbsentTests**: `test_an_error_reply_with_finality_and_a_complete_record_resends_once`, `test_a_late_pong_without_an_error_is_confirmation`, `test_a_fail_is_a_refusal_dead_lettered_and_never_retried`, `test_a_crash_or_broken_connection_never_proves_absence`
- **ReconciledDeliveredTests**: `test_a_designated_senders_logged_part_is_retired_without_writing`, `test_an_undesignated_account_stays_unknown_whatever_the_record_shows`
- **RestartTests**: `test_a_restarted_bridge_continues_from_the_recorded_progress`, `test_a_reloaded_bridge_module_continues_from_the_recorded_progress`, `test_a_flush_killed_after_writing_a_part_recovers_from_the_database`
- **SameAccountFlowTests**: `test_the_same_accounts_next_entry_flows_while_the_first_is_uncertain`, `test_the_same_accounts_next_entry_flows_while_the_first_is_failing`
- **SameFlushTests**: `test_one_flush_resolves_every_obligation_of_a_text`
- **StatusTests**: `test_status_counts_waiting_checking_and_files_awaiting_import`
- **StorageFailureTests**: `test_pchat_retries_then_hands_off_the_rest`, `test_an_outcome_never_recorded_travels_as_an_attested_outcome`, `test_with_the_handoff_unanswered_too_the_fallback_file_carries_the_outcome`, `test_an_agent_notice_does_the_same`
- **UnchangedPathTests**: `test_a_confirmed_post_exits_0_and_queues_nothing`, `test_a_server_that_cannot_be_reached_queues_the_whole_post_once`, `test_ack_exits_0_and_is_one_tagmsg`
- **UnknownForADayTests**: `test_after_24_hours_it_is_dead_lettered_with_its_progress_and_one_alert`
- **UntrackedSubstitutionTests**: `test_undesignated_it_leaves_the_part_unknown_until_its_dead_letter`, `test_designated_an_unexplained_message_suspends_once_with_one_alert`

### `files`: `packages/chat/scripts/tests/test_outbox_files.py`

- **BoundedDrainTests**: `test_staging_cleanup_is_bounded_per_flush_and_reaches_every_file`, `test_a_large_claim_is_imported_one_bounded_batch_per_flush`
- **ChangedClaimTests**: `test_a_changed_claim_is_held_once`
- **ChangedFallbackTests**: `test_the_changed_bytes_are_held_before_the_file_is_removed`, `test_a_power_loss_between_the_hold_and_the_removal`, `test_a_changed_file_keeps_import_fair_and_its_pass_accounting`, `test_a_failed_or_cancelled_hold_keeps_the_file`
- **CleanupAfterPublicationTests**: `test_a_cleanup_failure_after_publication_is_still_queued_and_delivered_once`, `test_a_failure_before_publication_is_still_a_failure`
- **DurableClaimTests**: `test_a_power_loss_after_the_rename_before_the_directory_sync`, `test_a_power_loss_after_the_import_and_after_an_unsynced_unlink`
- **ExportDurabilityTests**: `test_x1_a_power_loss_before_the_sync_loses_the_line_and_x1_writes_it_again`, `test_a_record_left_with_an_unsynced_directory_entry_is_synced_before_its_mark`, `test_negative_control_without_the_directory_sync_the_record_is_lost`, `test_a_fallback_file_in_a_directory_another_process_made_survives_after_exit_3`, `test_the_pending_queue_and_its_ledger_are_durable`, `test_a_delivery_dead_letter_waits_for_outbox_lock_and_stays_pending`
- **ImportBoundaryTests**: `test_crashes_around_each_step_neither_lose_nor_duplicate`, `test_bounded_and_fair_oldest_first_with_a_steady_inflow`, `test_import_epoch_grows_only_when_a_whole_listing_is_imported`, `test_a_handoff_published_before_an_e8_mid_pass_is_applied_and_nothing_collected_early`, `test_a_legacy_claim_read_in_several_requests_keeps_its_occurrence_ids`, `test_a_claim_is_never_unlinked_before_a_barrier_after_its_first_import`
- **ImportRotationTests**: `test_retained_failures_never_starve_a_later_file`
- **ImportTests**: `test_identical_rows_are_distinct_occurrences`, `test_unsupported_rows_are_held_with_one_alert_for_their_claim_file`, `test_a_changed_claim_prefix_is_held_and_never_merged`, `test_the_fallback_file_rules`, `test_an_unreadable_published_file_is_held_with_one_alert`
- **LegacyClaimIntegrityTests**: `test_a_rewritten_closed_claim_is_held_with_its_bytes_and_kept`, `test_an_unchanged_closed_claim_is_still_removed`, `test_a_claim_rewritten_between_its_final_import_and_its_removal_is_held`, `test_a_fallback_file_rewritten_between_its_import_and_its_removal_is_held`, `test_an_unterminated_last_line_is_held_even_when_it_is_valid_json`
- **ListingFailureTests**: `test_the_same_handoff_imports_once_the_directory_can_be_read_again`, `test_a_listing_that_fails_after_the_import_completes_no_pass`
- **MalformedFallbackTests**: `test_malformed_fields_anywhere_are_held_once_and_block_nothing`, `test_an_import_request_that_fails_keeps_its_file_with_an_alert_and_blocks_nothing`, `test_a_malformed_legacy_row_is_held_and_blocks_nothing`
- **PausedLockHolderTests**: `test_durable_obligations_behind_a_stopped_holder_flow_in_the_same_flush`, `test_a_holder_that_dies_leaves_its_half_row_held_at_retirement`, `test_no_client_of_this_release_waits_for_outbox_lock`, `test_the_lock_is_never_broken_and_an_e7_and_its_alert_still_go_out`
- **PublicationTests**: `test_a_writer_stopped_before_its_link_is_never_imported_and_delays_nothing`, `test_a_power_loss_after_the_link_before_the_directory_sync`, `test_two_files_for_one_entry_are_one_obligation`, `test_a_taken_name_is_never_replaced`, `test_without_hard_links_publication_fails_and_nothing_is_reported_queued`
- **StagingIdentityTests**: `test_an_incomplete_writer_identity_never_proves_its_writer_gone`
- **StatusReadTests**: `test_status_reads_the_highest_dead_letter_version_of_each_post`, `test_status_reports_unreadable_files_and_an_odd_snapshot`
- **UnreadableFileTests**: `test_an_unreadable_fallback_file_never_blocks_other_entries`, `test_an_unreadable_legacy_claim_never_blocks_other_entries`

### `bridge`: `packages/chat/scripts/tests/test_outbox_bridge.py`

- **AcknowledgementBeforeHistoryTests**: `test_an_acknowledgement_before_its_instruction_is_replayed`
- **AcknowledgementDurabilityTests**: `test_a_failed_acknowledgement_append_is_written_on_replay`
- **AcknowledgementPowerLossTests**: `test_an_acknowledgement_survives_a_failed_settling_save_and_a_power_loss`
- **AcknowledgementRecoveryTests**: `test_every_recipient_of_one_interrupt_is_recovered_after_a_failed_settling_save`, `test_a_failed_read_is_retried_at_a_later_pass_and_a_malformed_line_is_skipped`
- **AlertIdentityTests**: `test_two_recipients_alerts_are_each_dead_lettered`
- **AnnounceTests**: `test_with_its_requests_failing_it_publishes_a_fallback_file_without_outbox_lock`, `test_with_nothing_durable_the_flag_stays_unset_and_a_repeat_is_one_entry`, `test_deliveries_saves_held_announced_only_after_the_announcement_is_durable`, `test_a_crash_after_e1_and_before_a1_is_adopted_after_the_restart_and_sent_once`, `test_an_ordinary_post_killed_after_e1_is_still_abandoned`
- **AuthorityThreadTests**: `test_the_thread_serves_a_client_and_the_bridges_requests`
- **BoundedSetupTests**: `test_each_login_phase_times_out_at_its_deadline`, `test_channel_creation_times_out_at_its_deadline`, `test_another_entry_in_the_same_flush_still_gets_its_attempt`
- **CatchUpIndexFailureTests**: `test_a_failed_index_mid_catch_up_never_restores_completeness_or_moves_the_checkpoint`, `test_a_successful_replay_restores_completeness_from_the_preserved_checkpoint`
- **CoverageTests**: `test_begin_sync_and_each_way_of_stopping`, `test_a_catch_up_from_the_stored_mark_carries_the_span_over`, `test_a_failed_index_stops_coverage_and_holds_the_checkpoint`, `test_the_bridges_loop_indexes_verified_messages`
- **DeliveredAnnouncementTests**: `test_an_unrecorded_announcement_is_raised_again_until_durable_without_a_second_interrupt`, `test_an_announcement_that_raises_after_delivery_is_owed_and_the_interrupt_rung_once`, `test_no_failure_after_delivery_ever_rings_the_interrupt_again`, `test_a_failure_before_delivery_is_retried_as_before`, `test_an_acknowledgement_before_recovery_keeps_the_announcement_owed`
- **DeliveryContainmentTests**: `test_a_dead_letter_that_cannot_be_written_blocks_nothing`, `test_an_item_that_fails_in_any_way_blocks_no_other`, `test_an_item_leaves_only_once_its_ledger_line_is_durable`
- **FailedBindTests**: `test_a_failed_bind_is_retried_and_the_thread_started_at_the_next_pass`
- **FlushContainmentTests**: `test_each_failing_step_leaves_the_others_running`, `test_one_failing_reconciliation_key_leaves_the_others_decided`, `test_a_writer_with_an_impossible_pid_never_stops_a_flush`, `test_an_export_that_cannot_be_written_stays_due_and_blocks_nothing`
- **IndexTimeTests**: `test_server_fields_missing_or_malformed_are_never_replaced_by_local_ones`, `test_an_unreadable_time_is_a_failed_index`
- **InterruptIntentTests**: `test_the_intent_is_durable_before_the_first_command`, `test_without_a_durable_intent_nothing_is_done`, `test_a_crash_at_any_boundary_after_the_intent_never_interrupts_again`, `test_a_crash_after_the_evidence_never_interrupts_again`, `test_a_crash_before_the_intent_is_retried_as_today`
- **InterruptOutcomeTests**: `test_save_failures_after_delivery_never_interrupt_again`, `test_with_no_surviving_proof_it_is_held_alerted_once_and_settled_by_the_acknowledgement`, `test_an_acknowledgement_while_work_is_owed_then_a_restart`, `test_a_proved_no_action_is_retried_and_a_partial_step_is_held`, `test_proof_that_is_not_proof_leaves_it_held`
- **JoinWithoutServerTimeTests**: `test_a_failed_first_index_after_an_untimed_join_is_never_skipped_and_proves_nothing`, `test_a_timed_join_takes_its_boundary_from_the_server_alone`, `test_a_failed_index_mid_catch_up_never_restores_completeness_or_moves_the_checkpoint`, `test_a_successful_replay_restores_completeness_from_the_preserved_checkpoint`
- **LiveAcknowledgementRetryTests**: `test_a_live_acknowledgement_is_settled_after_a_transient_ledger_failure`
- **OwedAnnouncementExpiryTests**: `test_an_owed_announcement_survives_the_expiry_and_a_restart`
- **QueuedOnlyWhenSavedTests**: `test_a_failed_queue_save_then_a_same_instance_replay_then_a_crash`, `test_a_failed_save_of_a_held_replay_leaves_no_alert_behind`
- **RecoveryInputTests**: `test_acknowledgement_recovery_with_a_stat_failure_defers_a_ready_interrupt`, `test_acknowledgement_recovery_with_a_stat_failure_keeps_an_expired_interrupt`, `test_a_replay_with_a_ledger_stat_failure_is_never_rung`, `test_proof_with_a_ledger_stat_failure_is_no_proof_until_it_can_be_read`, `test_alert_export_with_a_ledger_stat_failure_queues_no_second_push`, `test_an_unreadable_acknowledgement_set_never_records_or_settles_as_if_empty`, `test_every_recovery_reader_skips_a_torn_line_and_keeps_the_rest`, `test_pending_state_with_a_stat_failure_stays_unloaded`
- **ReplayedInterruptTests**: `test_a_replayed_interrupt_keeps_one_obligation_whatever_its_outcome`, `test_another_recipient_of_the_same_message_stays_independent`, `test_an_ordinary_wake_up_keeps_its_baseline`, `test_a_replay_with_an_unreadable_ledger_is_never_rung`
- **ServerEvidenceTests**: `test_a_late_message_without_server_time_never_proves_absence`, `test_a_failed_first_index_without_a_checkpoint_is_replayed_and_never_skipped`, `test_a_failed_index_mid_catch_up_never_restores_completeness_or_moves_the_checkpoint`, `test_a_successful_replay_restores_completeness_from_the_preserved_checkpoint`
- **StartupCleanupTests**: `test_a_failed_sync_of_the_database_files_releases_everything`, `test_a_failed_bind_or_thread_start_releases_everything`, `test_a_dead_authority_thread_is_not_running_and_is_replaced`
- **UnreadableAcknowledgementsTests**: `test_a_ready_interrupt_is_deferred_until_its_saved_acknowledgement_can_be_read`, `test_an_expired_interrupt_is_not_dead_lettered_while_its_acknowledgement_cannot_be_read`, `test_an_expired_interrupt_with_no_acknowledgement_is_dead_lettered_as_before`, `test_with_no_acknowledgement_file_a_ready_interrupt_is_delivered`
- **UnreadablePendingTests**: `test_a_possibly_acted_intent_survives_an_unreadable_pending_state`, `test_a_missing_pending_file_is_an_empty_queue`

### `store`: `packages/chat/scripts/tests/test_outbox_store.py`

- **AccountingTests**: `test_a_single_line_part`, `test_a_multi_line_part_as_a_control`, `test_an_attributed_message_is_accounted_for_too`
- **AlertTests**: `test_an_expired_entry_raises_one_row_and_one_pending_item`, `test_a_crash_after_the_pending_item_and_before_the_mark_still_gives_one`, `test_suspensions_raise_one_row_each_with_the_next_sequence`, `test_a_replayed_index_suspends_nothing_new`
- **AttestationTests**: `test_a_void_from_writing_returns_the_part_to_unsent`, `test_wrong_nonce_writer_or_generation_change_nothing`, `test_recovery_first_a8c_then_the_void_still_applies`, `test_never_after_a5_or_a_decision_or_on_a_terminal_entry`, `test_an_attested_outcome_applies_only_to_an_attempt_still_writing`, `test_a_lost_a1_reply_then_a_closed_connection_then_the_handoff_delivers_once`
- **CollectionTests**: `test_messages_near_unresolved_attempts_are_kept_and_attributions_go_with_their_messages`, `test_terminal_rows_stay_until_their_current_version_is_exported`, `test_messages_kept_for_an_unresolved_attempt_never_stop_newer_ones_being_collected`, `test_open_window_dead_attempts_are_kept`, `test_collection_then_delayed_reconciliation_decides_the_same`
- **FinalityTests**: `test_an_error_reply_and_a_late_pong_give_finality`, `test_a_bare_timeout_and_a_write_error_give_none`, `test_a_fail_during_the_drain_is_a_refusal`, `test_a_gone_writer_or_a_closed_connection_ends_without_finality_and_r2_never_applies`
- **FragmentTests**: `test_each_unexplained_fragment_blocks_absence`, `test_with_nothing_unexplained_it_is_absent`, `test_a_forced_earlier_part_of_the_same_post_does_not_block`, `test_an_unconfirmed_multi_line_attempt_blocks_r1_inside_its_window`
- **TransportOutcomeTests**: `test_unrelated_traffic_never_extends_a_parts_absolute_deadline`, `test_a_write_that_fails_part_way_leaves_that_part_uncertain`, `test_a_fail_then_silence_is_a_refusal_kept_as_recorded`, `test_a_cleanup_failure_after_confirmation_changes_nothing`
- **UniqueAttributionTests**: `test_identical_texts_each_get_a_distinct_message`, `test_the_unique_constraints_refuse_a_second_use`, `test_a_crash_between_components_decides_the_rest_the_same_way_later`

### `authority`: `packages/chat/scripts/tests/test_outbox_authority.py`

- **AcceptFloodTests**: `test_a_connection_flood_never_stops_a_turn`
- **BindFailureTests**: `test_a_socket_with_the_wrong_mode_is_removed`
- **LockFailureTests**: `test_a_lock_error_leaves_no_file_open`
- **MalformedRequestTests**: `test_a_malformed_create_or_queue_is_refused_and_stores_nothing`, `test_a_frame_that_cannot_be_decoded_closes_only_its_own_connection`
- **OneOpenerTests**: `test_a_second_connection_gets_busy_at_once_while_the_authority_runs`, `test_a_second_bridge_cannot_take_the_authority_lock`, `test_a_foreign_holder_at_startup_fails_the_start_within_open_wait`, `test_a_database_made_by_other_code_is_refused`, `test_a_file_that_is_not_a_database_is_refused_and_the_lock_released`, `test_the_modes_read_back_exclusive_and_wal_and_no_autocommit_is_used`, `test_no_client_module_opens_the_database`, `test_the_socket_is_bound_0600_and_a_path_over_the_limit_binds_nothing`, `test_a_peer_of_another_uid_is_closed_unread`, `test_a_request_that_does_not_speak_for_its_sender_is_refused`
- **PausedClientTests**: `test_half_a_frame_commits_nothing_and_closes_at_req_wait`, `test_a_whole_frame_commits_and_its_unread_reply_is_recovered_by_replay`, `test_a_client_stopped_after_its_a1_reply_keeps_its_writing_attempt`, `test_another_accounts_entry_is_delivered_while_a_client_is_stopped_mid_request`, `test_floods_are_closed_or_busy_at_once_with_no_transaction`, `test_bridge_requests_still_run_at_every_other_turn`, `test_an_in_process_request_still_queued_at_auth_wait_is_cancelled_and_never_runs`, `test_negative_control_a_client_holding_a_database_transaction_stalls_other_writers`
- **RequestCrashTests**: `test_every_op_replays_to_the_same_state`, `test_handoff_void_and_retire_replay_too`, `test_an_error_reply_is_treated_as_missing_and_a_conflict_changes_nothing`, `test_a_restart_between_two_requests_of_one_call_makes_the_client_query`, `test_an_older_epochs_reply_is_ignored`, `test_no_reply_is_ever_written_before_its_commit`
- **SandboxTests**: `test_every_database_opened_lies_under_the_sandbox`, `test_no_designation_interface_exists`
- **TransactionFailureTests**: `test_a_failed_commit_is_rolled_back_and_the_next_request_runs`
- **UnavailableTests**: `test_each_way_of_being_unavailable_queues_the_whole_call_once`, `test_pchat_ack_and_an_agent_notice_queue_the_same_way`, `test_mid_post_the_remainder_is_handed_off_and_confirmed_parts_never_resent`, `test_a_socket_path_over_the_limit_queues_without_connecting`

### `crashes`: `packages/chat/scripts/tests/test_outbox_crashes.py`

- **AuthoritySideTests**: `test_the_authority_crashing_mid_transaction_rolls_it_back`, `test_the_start_transaction`
- **BridgeSideTests**: `test_e4_before_and_after`, `test_a8_before_and_after`, `test_r1_r2_before_and_after`, `test_e7_before_and_after`, `test_e8_before_and_after`, `test_x1_mark`, `test_g1_before_and_after`
- **CoverageMapTests**: `test_every_row_covered_elsewhere_names_a_test_that_exists`
- **WriterSideTests**: `test_login_or_setup`, `test_q0_tx_before_and_after`, `test_e1_before_and_after`, `test_a1_before_and_after`, `test_sending_waiting_and_closing_leave_it_writing_until_a8`, `test_a2_before_and_after`, `test_a4_before_and_after`, `test_a5_before_and_after`, `test_e3_before_and_after`, `test_the_handoffs_fallback_file_once_published_is_applied_by_h1`, `test_e5_before_and_after`

### `regr`: `packages/chat/scripts/tests/test_outbox_regressions.py`

- **IndependentFlowTests**: `test_a_stopped_outbox_lock_holder_does_not_block_another_accounts_queued_entry`
- **RepostTests**: `test_a_committed_multiline_post_is_never_reposted_whole`, `test_a_committed_line_split_post_is_never_reposted_whole`
- **StorageFailureTests**: `test_a_failed_confirmation_record_never_requeues_the_whole_post`

### `tb`: `packages/chat/scripts/tests/test_bridge.py`

- **CatchUpRecoveryTests**: `test_interrupted_catch_up_resumes_from_checkpoint`, `test_finished_catch_up_then_live_messages_advance_checkpoint`
- **InterruptKeystrokeTests**: `test_running_claude_in_insert_mode_needs_two_escapes`, `test_restored_prompt_is_cleared_before_typing`, `test_idle_claude_gets_no_escape`, `test_dialog_is_held_untouched`, `test_someones_draft_is_held_untouched`, `test_running_codex_needs_one_escape`, `test_codex_stale_running_after_an_interrupt_is_idle`, `test_codex_interrupted_with_background_terminals_gets_no_escape`, `test_running_codex_with_background_terminals_gets_one_escape`, `test_codex_transcript_browser_is_held_without_keys`, `test_plain_codex_delivery_holds_transcript_browser_without_keys`
- **InterruptRoutingTests**: `test_owner_double_bang_interrupts_directly`, `test_manager_may_interrupt_its_own_agent`, `test_manager_plain_mention_is_not_delivered`, `test_worker_cannot_interrupt`, `test_only_the_interrupted_agent_can_acknowledge`, `test_unacknowledged_interrupt_alerts_without_interrupting_again`, `test_unaddressed_worker_post_rings_manager`, `test_worker_asking_owner_pushes_and_rings_manager`, `test_worker_leading_address_form_rings_manager`, `test_worker_asking_assistant_rings_manager`, `test_worker_to_worker_rings_nobody`, `test_assistant_to_agent_bypasses_manager`, `test_owner_to_two_agents_and_manager`, `test_owner_to_agent_without_live_session_falls_back_to_manager`, `test_owner_unaddressed_rings_manager_for_acceptance`, `test_assistant_asking_owner_does_not_ring_manager`, `test_manager_reply_does_not_close_a_direct_delivery`, `test_direct_delivery_follows_a_moved_agent`

### `sil`: `packages/chat/scripts/tests/test_silence.py`

- **AgentWrapperTests**: `test_wrapper_notices_inside_a_silent_run_are_withheld_not_queued`
- **PchatTests**: `test_the_runs_environment_refuses_and_nothing_is_queued`, `test_an_account_name_or_an_old_key_alone_never_silences`, `test_silence_outlives_the_finish_with_no_timer_until_release`, `test_an_unreadable_run_named_by_the_environment_refuses_but_unrelated_ones_do_not`
- **SharedPathTests**: `test_a_direct_post_from_the_silent_invocation_is_refused_below_every_caller`, `test_the_silent_invocation_never_queues_a_post_but_keeps_acks`, `test_a_pre_run_outbox_entry_keeps_its_delivery`

### `sg`: `packages/chat/scripts/tests/test_skill_guidance.py`

- **GuidanceTests**: `test_the_rollout_paragraph_is_about_the_uncaptured_helper_only`, `test_the_guidance_names_commands_at_all`, `test_every_mentioned_subcommand_and_option_exists`, `test_options_named_on_their_own_belong_to_post`, `test_the_listed_post_types_are_pchats`, `test_the_queueing_guidance_describes_19s_behaviour`, `test_the_check_catches_a_missing_option`

### `prov`: `packages/chat/tests/test_provenance.py`

- **ProvenanceTests**: `test_the_baseline_and_effective_source_are_named`, `test_each_file_names_its_source_commit`, `test_the_carried_timing_link_is_kept_and_recorded_as_external`, `test_every_captured_file_has_its_recorded_hash_and_mode`, `test_byte_for_byte_means_identical_and_changed_means_listed`, `test_nothing_unrecorded_sits_in_the_captured_tree`, `test_the_monitored_tree_excludes_only_named_paths`

### `rb`: `packages/release/tests/test_build.py`

- **BuildTests**: `test_a_clean_landed_commit_builds_and_verifies`, `test_the_manifest_binds_versions_interpreter_platforms_and_formats`, `test_every_format_the_shipped_code_uses_is_declared`, `test_the_versions_the_shipped_code_embeds_are_the_declared_ones`, `test_every_runtime_module_passes_the_unchanged_privacy_scan`, `test_payload_bytes_come_from_the_recorded_commit`, `test_an_older_landed_commit_builds_from_its_own_bytes`, `test_the_same_commit_rebuilds_byte_for_byte_and_changes_nothing`, `test_the_inbox_core_is_not_released`, `test_the_command_line_reports_and_exits`
- **RefusalTests**: `test_a_dirty_tree_is_refused`, `test_a_commit_not_on_origin_master_is_refused`, `test_a_source_without_origin_master_is_refused`, `test_a_revision_that_is_not_a_commit_is_refused`, `test_an_output_directory_inside_the_checkout_is_refused`, `test_an_outside_looking_path_resolving_inside_the_checkout_is_refused`, `test_an_output_directory_under_the_live_tools_is_refused`, `test_a_symlinked_live_location_is_refused_either_way`, `test_resolved_default_runtime_locations_are_refused`, `test_nested_runtime_symlink_targets_are_refused`, `test_links_inside_a_linked_directory_and_link_cycles_are_handled`, `test_links_inside_a_linked_files_directory_are_handled`, `test_a_live_location_inside_the_output_directory_is_refused`, `test_adversarial_symlink_variants_are_refused`, `test_an_unreadable_runtime_tree_is_refused`, `test_too_many_entries_to_check_is_refused`, `test_configured_chat_state_and_config_locations_are_refused`, `test_private_data_in_the_payload_is_refused`, `test_an_existing_identity_is_never_rebound`, `test_a_refused_build_leaves_no_partial_release`
- **UnchangedTests**: `test_release_json_is_unchanged_apart_from_19s_declarations`, `test_any_other_declarative_change_is_caught`, `test_manifests_of_two_commits_differ_only_in_generated_fields`

### `rs`: `packages/release/tests/test_stage.py`

- **ReleaseOf19Tests**: `test_preflight_refuses_19s_release_against_the_live_baseline`, `test_the_fixture_release_is_not_19s_manifest`

### `ri`: `packages/release/tests/test_install.py`

- **InstallTests**: `test_the_check_catches_a_checkout_import`, `test_pchat_help_runs_from_the_venv`, `test_rotate_logs_help_runs_from_the_venv`, `test_the_bridge_loads_from_the_venv_without_starting`, `test_the_install_is_the_wheel_alone_and_adds_nothing_to_the_import_path`, `test_pip_ran_offline_and_wrote_nothing_to_the_home`, `test_the_exact_interpreter_is_recorded_for_this_environment`
