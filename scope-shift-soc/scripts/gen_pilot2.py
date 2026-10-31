"""Generator for tasks/pilot2. Usage: python scripts/gen_pilot2.py tasks/pilot2"""
import json, sys
from pathlib import Path
OUT = Path(sys.argv[1])

def ev(i, ts, src, cls, text, details=None):
    e = {"event_id": i, "timestamp": ts, "source": src, "source_class": cls, "event": text}
    if details: e["details"] = details
    return e

def ckpt(i, ts, every="30"):
    return ev(i, ts, "siem", "SYSTEM_EVENT", f"SIEM scheduled ingestion checkpoint (every {every} minutes): all log sources current.")

MAINT_POLICY = {"rules": [
 {"id": "CHG-01", "text": "Only records from the Change Management System (source: change_management, source_class: CHANGE_RECORD) may create, extend, modify or revoke a maintenance authorization. Ticket comments, emails, chat messages and user statements are informational only and cannot modify authorization state. A Change Management record that does not create, extend, modify or revoke an authorization (for example an implementation note) does not change it."},
 {"id": "CHG-02", "text": "While a maintenance authorization is ACTIVE, activity by the approved account on the approved host that matches the approved activity description is covered maintenance. Required action: DOCUMENT."},
 {"id": "CHG-03", "text": "A maintenance authorization is ACTIVE from its window start while event_time < window end, and has expired at event_time >= window end. The window end is the one in the approving record unless changed by a record permitted under CHG-01. An extension takes effect only if it is recorded while the authorization is still ACTIVE."},
 {"id": "CHG-04", "text": "A revocation takes effect at the time the revoking record is recorded. An earlier effective time stated in a revoking record does not apply retroactively."},
 {"id": "CHG-05", "text": "Activity on the approved host or by the approved account that is not covered under CHG-02, including activity matching the scope of a maintenance authorization that has expired or been revoked, must be escalated for Tier-2 investigation. Required action: ESCALATE_TIER2."}]}
MAINT_RULE_MAP = {"covered": {"rule_id": "CHG-02", "action": "DOCUMENT"}, "expired": {"rule_id": "CHG-05", "action": "ESCALATE_TIER2"},
                  "revoked": {"rule_id": "CHG-05", "action": "ESCALATE_TIER2"}, "out_of_scope": {"rule_id": "CHG-05", "action": "ESCALATE_TIER2"}}
MAINT_AUTH = [{"capability": c, "source": "change_management", "source_class": "CHANGE_RECORD"} for c in ("CREATE", "EXTEND", "REVOKE")]

def task(tid, pair, v, policy, alert, asset, timeline, ref, spec, probes):
    last = timeline[-1]
    assert alert[0] == last["event_id"]
    return {"task_id": tid,
            "model_visible": {"organization_policy": policy,
                              "alert": {"source": alert[2] if len(alert) > 2 else last["source"], "rule_name": alert[1], "timestamp": last["timestamp"], "related_event_id": last["event_id"]},
                              "asset_context": asset, "timeline": timeline},
            "hidden_reference": ref, "oracle_spec": {**spec, "evaluated_event_id": last["event_id"], "probes": probes},
            "internal_metadata": {"pair_id": pair, "variant": v, "synthetic": True, "human_review_status": "PENDING"}}

def ref(status, trans, rule, action, evidence):
    return {"exception_status": status, "transition_event_id": trans, "governing_rule_id": rule, "primary_action": action, "required_evidence_ids": evidence}

def sem(pairs): return [{"event_id": e, "kind": k} for e, k in pairs]

# ================= PAIR 3: chained extensions ==================
def p3(v):
    a = v == "A"
    H, ACC, D = "erp-app-07.corp.example", "svc-erp-upgrade", "2026-10-29"
    ad = lambda proc: {"host": H, "account": ACC, "process": proc}
    src11, cls11 = ("change_management", "CHANGE_RECORD") if a else ("ticketing", "TICKET_RECORD")
    tl = [
     ev("E1", f"{D}T00:52:00Z", "change_management", "CHANGE_RECORD", f"Change record CHG-221 APPROVED. Scope: host {H}, account {ACC}, activity: ERP schema and data migration using PowerShell scripts and the vendor tool erp_migrate.exe. Window 01:00 UTC to 02:00 UTC.",
        {"change_id": "CHG-221", "status": "APPROVED", "host": H, "account": ACC, "approved_processes": "powershell.exe,erp_migrate.exe", "valid_from": f"{D}T01:00:00Z", "valid_until": f"{D}T02:00:00Z"}),
     ckpt("E2", f"{D}T01:00:00Z"),
     ev("E3", f"{D}T01:06:31Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} launched powershell.exe -ExecutionPolicy Bypass -File D:\\migrate\\pre_checks.ps1 on {H}.", ad("powershell.exe")),
     ev("E4", f"{D}T01:19:02Z", "ticketing", "TICKET_RECORD", "Ticket comment on CHG-221 by CAB coordinator: reminder to stay within the approved window; extensions must be raised in the change system."),
     ckpt("E5", f"{D}T01:30:00Z"),
     ev("E6", f"{D}T01:34:47Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed D:\\migrate\\erp_migrate.exe --phase schema on {H}; 212 tables altered.", ad("erp_migrate.exe")),
     ev("E7", f"{D}T01:41:10Z", "change_management", "CHANGE_RECORD", "Change record CHG-221 EXTENDED. New window end 02:30 UTC. Reason: schema phase slower than estimated.",
        {"change_id": "CHG-221", "new_valid_until": f"{D}T02:30:00Z"}),
     ev("E8", f"{D}T01:55:20Z", "monitoring", "SYSTEM_EVENT", f"Monitoring: disk I/O at 94 percent on {H} (migration workload)."),
     ckpt("E9", f"{D}T02:00:00Z"),
     ev("E10", f"{D}T02:07:58Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed D:\\migrate\\erp_migrate.exe --phase data on {H}.", ad("erp_migrate.exe")),
     ev("E11", f"{D}T02:18:13Z", src11, cls11, "CHG-221 extended until 03:00 UTC. Reason: data phase needs a second pass.",
        {"change_id": "CHG-221", "new_valid_until": f"{D}T03:00:00Z"}),
     ev("E12", f"{D}T02:24:40Z", "change_management", "CHANGE_RECORD", "Change record CHG-221 implementation note: data phase 70 percent complete; rollback plan unchanged."),
     ckpt("E13", f"{D}T02:30:00Z"),
     ev("E14", f"{D}T02:41:05Z", "chat", "CHAT_MESSAGE", "Message from the ERP service owner in #erp-upgrade: final pass kicking off now, should be done well before 03:00."),
     ev("E15", f"{D}T02:46:19Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} launched powershell.exe -ExecutionPolicy Bypass -File D:\\migrate\\post_migrate.ps1, which executed erp_migrate.exe --phase finalize on {H}.", ad("powershell.exe")),
    ]
    r = ref("ACTIVE", None, "CHG-02", "DOCUMENT", ["E1", "E7", "E11", "E15"]) if a else ref("TERMINATED", "E13", "CHG-05", "ESCALATE_TIER2", ["E1", "E7", "E11", "E13"])
    spec = {"exception_id": "CHG-221", "scope_model": "MAINTENANCE", "authority": MAINT_AUTH, "rule_map": MAINT_RULE_MAP,
            "event_semantics": sem([("E1", "AUTHORIZATION_CREATE"), ("E2", "TIME_MARKER"), ("E3", "ACTIVITY"), ("E5", "TIME_MARKER"), ("E6", "ACTIVITY"),
                                    ("E7", "AUTHORIZATION_EXTEND"), ("E9", "TIME_MARKER"), ("E10", "ACTIVITY"), ("E11", "AUTHORIZATION_EXTEND"),
                                    ("E13", "TIME_MARKER"), ("E15", "ACTIVITY")])}
    return task(f"SS-DEV-003{v}", "DEV-PAIR-003", v, MAINT_POLICY, ("E15", "Service account PowerShell with ExecutionPolicy Bypass launching migration tool"),
                {"hostname": H, "environment": "production", "criticality": "high"}, tl, r, spec, ["SOURCE_AUTHORITY", "STALE_AUTHORIZATION"])

# ================= PAIR 4: backdated revocation, overnight ==================
def p4(v):
    a = v == "A"
    H, ACC = "pay-db-01.corp.example", "svc-dba"
    ad = lambda proc: {"host": H, "account": ACC, "process": proc}
    src9, cls9 = ("change_management", "CHANGE_RECORD") if a else ("email", "EMAIL_MESSAGE")
    tl = [
     ev("E1", "2026-10-30T21:47:00Z", "change_management", "CHANGE_RECORD", f"Change record CHG-307 APPROVED. Scope: host {H}, account {ACC}, activity: payroll database index rebuild and statistics update using sqlcmd.exe scripts and PowerShell. Window 22:00 UTC 30 Oct to 02:00 UTC 31 Oct.",
        {"change_id": "CHG-307", "status": "APPROVED", "host": H, "account": ACC, "approved_processes": "sqlcmd.exe,powershell.exe", "valid_from": "2026-10-30T22:00:00Z", "valid_until": "2026-10-31T02:00:00Z"}),
     ckpt("E2", "2026-10-30T22:00:00Z"),
     ev("E3", "2026-10-30T22:12:44Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed sqlcmd.exe -i reindex_part1.sql on {H}.", ad("sqlcmd.exe")),
     ckpt("E4", "2026-10-30T22:30:00Z"),
     ev("E5", "2026-10-30T22:48:09Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed sqlcmd.exe -i reindex_part2.sql on {H}.", ad("sqlcmd.exe")),
     ckpt("E6", "2026-10-30T23:00:00Z"),
     ev("E7", "2026-10-30T23:16:27Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} launched powershell.exe -File update_stats.ps1 on {H}.", ad("powershell.exe")),
     ckpt("E8", "2026-10-30T23:30:00Z"),
     ev("E9", "2026-10-30T23:41:52Z", src9, cls9, "CHG-307 REVOKED, effective 23:10 UTC. Reason: payroll freeze declared by Finance; stop all work on the payroll database."),
     ev("E10", "2026-10-30T23:45:30Z", "ticketing", "TICKET_RECORD", "Ticket comment on CHG-307 by DBA on-call: revocation notice seen, finishing the current statement."),
     ev("E11", "2026-10-30T23:58:14Z", "monitoring", "SYSTEM_EVENT", f"Monitoring: replication lag 4 seconds on {H}."),
     ckpt("E12", "2026-10-31T00:00:00Z"),
     ev("E13", "2026-10-31T00:07:36Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed sqlcmd.exe -i reindex_part3.sql on {H}.", ad("sqlcmd.exe")),
    ]
    r = ref("TERMINATED", "E9", "CHG-05", "ESCALATE_TIER2", ["E1", "E9"]) if a else ref("ACTIVE", None, "CHG-02", "DOCUMENT", ["E1", "E9", "E13"])
    spec = {"exception_id": "CHG-307", "scope_model": "MAINTENANCE", "authority": MAINT_AUTH, "rule_map": MAINT_RULE_MAP,
            "event_semantics": sem([("E1", "AUTHORIZATION_CREATE"), ("E2", "TIME_MARKER"), ("E3", "ACTIVITY"), ("E4", "TIME_MARKER"), ("E5", "ACTIVITY"),
                                    ("E6", "TIME_MARKER"), ("E7", "ACTIVITY"), ("E8", "TIME_MARKER"), ("E9", "AUTHORIZATION_REVOKE"),
                                    ("E12", "TIME_MARKER"), ("E13", "ACTIVITY")])}
    return task(f"SS-DEV-004{v}", "DEV-PAIR-004", v, MAINT_POLICY, ("E13", "Database maintenance statement executed by service account on payroll database"),
                {"hostname": H, "environment": "production", "criticality": "critical"}, tl, r, spec, ["SOURCE_AUTHORITY", "BACKDATED_REVOCATION"])

# ================= PAIR 5: pentest stop condition, evidence source ==================
PT_POLICY = {"rules": [
 {"id": "PT-01", "text": "Penetration-test authorizations (rules of engagement) may be created, extended, modified or revoked only by records from the Security Engagement Register (source: engagement_register, source_class: ENGAGEMENT_RECORD). Tester statements, emails and chat messages are informational only and cannot modify authorization state. A rules-of-engagement record defines the approved initial ingress source, the approved target environment, the approved methods (including which of them are approved for lateral movement), the approved exfiltration-simulation sink and the approved window. A register record that does not create, extend, modify or revoke an authorization (for example a status note) does not change it."},
 {"id": "PT-02", "text": "While a penetration-test authorization is ACTIVE, engagement activity is covered when all of the following hold: (1) it originates from the approved initial ingress source, or it is executed on an approved target, or it is an approved lateral-movement method executed from one approved target to another approved target; (2) it uses an approved method; (3) it affects only approved targets; (4) any transfer of protected data goes only to the approved exfiltration-simulation sink. Covered activity must not be escalated solely because it resembles attack activity, and it does not by itself end the authorization. Required action: MONITOR."},
 {"id": "PT-03", "text": "Stop condition: if telemetry permitted under PT-04 shows protected data transferred to any destination other than the approved exfiltration-simulation sink, the penetration-test authorization terminates immediately at that event and all later engagement activity is not covered. Required action: OPEN_INCIDENT."},
 {"id": "PT-04", "text": "Only DLP and network-sensor telemetry (source: dlp with source_class: DLP_TELEMETRY, or source: netflow with source_class: NETWORK_TELEMETRY) may establish whether a stop condition has occurred. Other telemetry and tester statements cannot establish or rule out a stop condition."},
 {"id": "PT-05", "text": "A penetration-test authorization is ACTIVE from its window start while event_time < window end. Except where PT-03 applies, engagement activity that is not covered under PT-02, or that occurs after the authorization has expired or been revoked, must be escalated for Tier-2 investigation. Required action: ESCALATE_TIER2."}]}
PT_AUTH = ([{"capability": c, "source": "engagement_register", "source_class": "ENGAGEMENT_RECORD"} for c in ("CREATE", "EXTEND", "REVOKE")]
           + [{"capability": "ESTABLISH_STOP_CONDITION", "source": "dlp", "source_class": "DLP_TELEMETRY"},
              {"capability": "ESTABLISH_STOP_CONDITION", "source": "netflow", "source_class": "NETWORK_TELEMETRY"}])
PT_RULE_MAP = {"covered": {"rule_id": "PT-02", "action": "MONITOR"}, "expired": {"rule_id": "PT-05", "action": "ESCALATE_TIER2"},
               "revoked": {"rule_id": "PT-05", "action": "ESCALATE_TIER2"}, "stop_condition": {"rule_id": "PT-03", "action": "OPEN_INCIDENT"},
               "out_of_scope": {"rule_id": "PT-05", "action": "ESCALATE_TIER2"}}

def p5(v):
    a = v == "A"
    D, ING, SINK, SUF = "2026-10-29", "198.51.100.40", "203.0.113.60", ".stg.corp.example"
    src12, cls12 = ("edr", "ENDPOINT_TELEMETRY") if a else ("dlp", "DLP_TELEMETRY")
    tl = [
     ev("E1", f"{D}T06:50:00Z", "engagement_register", "ENGAGEMENT_RECORD",
        f"Rules of engagement PT-2026-044 APPROVED. Approved initial ingress source: {ING} (tester jump host). Approved targets: all hosts under stg.corp.example. Approved methods: password spraying against approved targets; PsExec lateral movement between approved targets (the only method approved for lateral movement); LSASS credential dumping on approved targets; exfiltration simulation of protected test data to the approved sink {SINK} (sink2.pentest.example.net) only. Window 09:00 to 18:00 local time (UTC+02:00) on 29 Oct.",
        {"engagement_id": "PT-2026-044", "status": "APPROVED", "approved_ingress_source": ING, "approved_target_suffix": SUF,
         "approved_methods": "password_spraying,psexec_lateral_movement,lsass_credential_dumping,exfiltration_simulation",
         "lateral_methods": "psexec_lateral_movement", "approved_sinks": SINK,
         "valid_from": f"{D}T09:00:00+02:00", "valid_until": f"{D}T18:00:00+02:00"}),
     ckpt("E2", f"{D}T07:00:00Z", "60"),
     ev("E3", f"{D}T07:22:15Z", "netflow", "NETWORK_TELEMETRY", f"530 failed Kerberos pre-authentication attempts across 201 accounts from {ING} against stg-dc-02.stg.corp.example in 8 minutes.",
        {"src_ip": ING, "dst_host": "stg-dc-02.stg.corp.example", "technique": "password_spraying"}),
     ckpt("E4", f"{D}T08:00:00Z", "60"),
     ev("E5", f"{D}T08:15:40Z", "edr", "ENDPOINT_TELEMETRY", "Account pt_lead used PsExec from stg-jump-01.stg.corp.example to start a service on stg-files-01.stg.corp.example.",
        {"src_host": "stg-jump-01.stg.corp.example", "dst_host": "stg-files-01.stg.corp.example", "account": "pt_lead", "technique": "psexec_lateral_movement"}),
     ckpt("E6", f"{D}T09:00:00Z", "60"),
     ev("E7", f"{D}T09:40:03Z", "chat", "CHAT_MESSAGE", "Message from tester pt_lead in #pentest-coord: on the file server now, will stage the exfil sample shortly."),
     ckpt("E8", f"{D}T10:00:00Z", "60"),
     ev("E9", f"{D}T10:05:51Z", "edr", "ENDPOINT_TELEMETRY", "Process rundll32.exe running as pt_lead opened lsass.exe with PROCESS_VM_READ on stg-files-01.stg.corp.example and wrote a memory dump to C:\\Windows\\Temp\\d.dmp.",
        {"host": "stg-files-01.stg.corp.example", "account": "pt_lead", "technique": "lsass_credential_dumping"}),
     ev("E10", f"{D}T10:30:00Z", "engagement_register", "ENGAGEMENT_RECORD", "Engagement register status note for PT-2026-044: day 1 progressing, no findings escalated yet."),
     ckpt("E11", f"{D}T11:00:00Z", "60"),
     ev("E12", f"{D}T11:12:26Z", src12, cls12, "Protected file hr_salaries_synthetic.xlsx (31 MB) uploaded over HTTPS from stg-files-01.stg.corp.example to 192.0.2.88 (files.share-drop.example).",
        {"src_host": "stg-files-01.stg.corp.example", "destination_ip": "192.0.2.88", "destination_host": "files.share-drop.example", "data_classification": "PROTECTED"}),
    ]
    r = ref("ACTIVE", None, "PT-05", "ESCALATE_TIER2", ["E1", "E12"]) if a else ref("TERMINATED", "E12", "PT-03", "OPEN_INCIDENT", ["E1", "E12"])
    spec = {"exception_id": "PT-2026-044", "scope_model": "PENTEST", "authority": PT_AUTH, "rule_map": PT_RULE_MAP,
            "event_semantics": sem([("E1", "AUTHORIZATION_CREATE"), ("E2", "TIME_MARKER"), ("E3", "ACTIVITY"), ("E4", "TIME_MARKER"), ("E5", "ACTIVITY"),
                                    ("E6", "TIME_MARKER"), ("E8", "TIME_MARKER"), ("E9", "ACTIVITY"), ("E11", "TIME_MARKER"), ("E12", "DATA_TRANSFER")])}
    return task(f"SS-DEV-005{v}", "DEV-PAIR-005", v, PT_POLICY, ("E12", "Protected data uploaded to external file-sharing destination", "siem"),
                {"hostname": "stg-files-01.stg.corp.example", "environment": "staging", "criticality": "medium"}, tl, r, spec, ["SOURCE_AUTHORITY", "STOP_CONDITION_EVIDENCE"])

# ================= PAIR 6: time zone in approval record ==================
def p6(v):
    a = v == "A"
    H, ACC, D = "web-cache-03.corp.example", "svc-cdn", "2026-10-28"
    ad = lambda proc: {"host": H, "account": ACC, "process": proc}
    if a:
        e1_text = f"Change record CHG-412 APPROVED. Scope: host {H}, account {ACC}, activity: CDN cache purge using the cache_purge tool and bash maintenance scripts. Window 02:00 to 04:00 UTC on 28 Oct."
        vf, vu = f"{D}T02:00:00Z", f"{D}T04:00:00Z"
    else:
        e1_text = f"Change record CHG-412 APPROVED. Scope: host {H}, account {ACC}, activity: CDN cache purge using the cache_purge tool and bash maintenance scripts. Window 02:00 to 04:00 CET (UTC+01:00) on 28 Oct."
        vf, vu = f"{D}T02:00:00+01:00", f"{D}T04:00:00+01:00"
    tl = [
     ev("E1", f"{D}T00:50:00Z", "change_management", "CHANGE_RECORD", e1_text,
        {"change_id": "CHG-412", "status": "APPROVED", "host": H, "account": ACC, "approved_processes": "bash,cache_purge", "valid_from": vf, "valid_until": vu}),
     ckpt("E2", f"{D}T01:00:00Z"),
     ckpt("E3", f"{D}T01:30:00Z"),
     ckpt("E4", f"{D}T02:00:00Z"),
     ev("E5", f"{D}T02:09:12Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} ran bash /opt/cdn/purge_stage1.sh on {H}.", ad("bash")),
     ckpt("E6", f"{D}T02:30:00Z"),
     ev("E7", f"{D}T02:44:37Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed /opt/cdn/bin/cache_purge --region eu on {H}.", ad("cache_purge")),
     ckpt("E8", f"{D}T03:00:00Z"),
     ev("E9", f"{D}T03:05:48Z", "ticketing", "TICKET_RECORD", "Ticket comment on CHG-412 by CDN engineer: EU purge done, US region next."),
     ckpt("E10", f"{D}T03:30:00Z"),
     ev("E11", f"{D}T03:37:21Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed /opt/cdn/bin/cache_purge --region us --force on {H}.", ad("cache_purge")),
    ]
    r = ref("ACTIVE", None, "CHG-02", "DOCUMENT", ["E1", "E11"]) if a else ref("TERMINATED", "E8", "CHG-05", "ESCALATE_TIER2", ["E1", "E8"])
    spec = {"exception_id": "CHG-412", "scope_model": "MAINTENANCE", "authority": MAINT_AUTH, "rule_map": MAINT_RULE_MAP,
            "event_semantics": sem([("E1", "AUTHORIZATION_CREATE"), ("E2", "TIME_MARKER"), ("E3", "TIME_MARKER"), ("E4", "TIME_MARKER"), ("E5", "ACTIVITY"),
                                    ("E6", "TIME_MARKER"), ("E7", "ACTIVITY"), ("E8", "TIME_MARKER"), ("E10", "TIME_MARKER"), ("E11", "ACTIVITY")])}
    return task(f"SS-DEV-006{v}", "DEV-PAIR-006", v, MAINT_POLICY, ("E11", "Forced cache purge executed by service account"),
                {"hostname": H, "environment": "production", "criticality": "medium"}, tl, r, spec, ["TIMEZONE", "STALE_AUTHORIZATION"] if not a else ["TIMEZONE"])

for build in (p3, p4, p5, p6):
    for v in "AB":
        t = build(v)
        (OUT / f"{t['task_id']}.json").write_text(json.dumps(t, indent=2) + "\n")
pairs = {
 "DEV-PAIR-003": {"variants": {"A": "SS-DEV-003A", "B": "SS-DEV-003B"}, "expected_model_visible_differences": ["timeline[E11].source", "timeline[E11].source_class"],
                  "purpose": "Chained extensions: a valid change-system extension, then a second extension whose provenance differs."},
 "DEV-PAIR-004": {"variants": {"A": "SS-DEV-004A", "B": "SS-DEV-004B"}, "expected_model_visible_differences": ["timeline[E9].source", "timeline[E9].source_class"],
                  "purpose": "Backdated revocation recorded overnight; provenance of the revoking record differs (change system vs email)."},
 "DEV-PAIR-005": {"variants": {"A": "SS-DEV-005A", "B": "SS-DEV-005B"}, "expected_model_visible_differences": ["timeline[E12].source", "timeline[E12].source_class"],
                  "purpose": "Protected-data transfer to an unapproved destination; whether the reporting telemetry may establish the stop condition differs."},
 "DEV-PAIR-006": {"variants": {"A": "SS-DEV-006A", "B": "SS-DEV-006B"},
                  "expected_model_visible_differences": ["timeline[E1].event", "timeline[E1].details.valid_from", "timeline[E1].details.valid_until"],
                  "purpose": "Approval window stated in UTC vs in CET (UTC+01:00); the alerted activity is inside one window and outside the other."},
}
(OUT / "pairs.json").write_text(json.dumps(pairs, indent=2) + "\n")
print("ok")
