"""Generator for tasks/pilot3 (long noisy timelines). Usage: python scripts/gen_pilot3.py tasks/pilot3"""
import json, sys, random, importlib.util, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
spec = importlib.util.spec_from_file_location("g2", str(Path(__file__).parent / "gen_pilot2.py"))
OUT = Path(sys.argv[1])
sys_argv = sys.argv; sys.argv = [sys.argv[0], tempfile.mkdtemp()]  # gen_pilot2 writes its own tasks on import; discard them
g2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(g2); sys.argv = sys_argv
MAINT_POLICY, MAINT_RULE_MAP, MAINT_AUTH = g2.MAINT_POLICY, g2.MAINT_RULE_MAP, g2.MAINT_AUTH
PT_POLICY, PT_RULE_MAP, PT_AUTH = g2.PT_POLICY, g2.PT_RULE_MAP, g2.PT_AUTH

def Z(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
def T(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))

class TL:
    """Collect events with tags; sort by time (stable) and number E1..En."""
    def __init__(self): self.items = []
    def add(self, ts, src, cls, text, details=None, kind=None, tag=None):
        self.items.append((T(ts) if isinstance(ts, str) else ts, len(self.items), src, cls, text, details, kind, tag))
    def build(self):
        out, sem, ids = [], [], {}
        for n, (ts, _, src, cls, text, details, kind, tag) in enumerate(sorted(self.items), 1):
            e = {"event_id": f"E{n}", "timestamp": Z(ts), "source": src, "source_class": cls, "event": text}
            if details: e["details"] = details
            out.append(e)
            if kind: sem.append({"event_id": e["event_id"], "kind": kind})
            if tag: ids[tag] = e["event_id"]
        return out, sem, ids

def checkpoints(tl, start, end, every=10):
    t = start
    while t <= end:
        tl.add(t, "siem", "SYSTEM_EVENT", f"SIEM scheduled ingestion checkpoint (every {every} minutes): all log sources current.", kind="TIME_MARKER", tag=f"ck{t:%H%M}")
        t += timedelta(minutes=every)

OTHER_HOSTS = ["mail-relay-02.corp.example", "hr-portal-01.corp.example", "build-agent-14.corp.example", "vpn-gw-01.corp.example",
               "crm-app-03.corp.example", "print-srv-02.corp.example", "wiki-01.corp.example", "jump-ops-01.corp.example"]
OTHER_ACCTS = ["svc-build", "jdoe", "svc-mail", "akowalski", "svc-monitor", "mchen", "svc-print", "rpatel"]
EDR_NOISE = ["{a} launched powershell.exe -File C:\\ops\\rotate_logs.ps1 on {h}.", "{a} executed certutil.exe -verifyctl on {h}.",
             "{a} ran sc.exe query wuauserv on {h}.", "{a} launched cmd.exe /c whoami /groups on {h}.",
             "{a} executed rundll32.exe printui.dll,PrintUIEntry on {h}.", "{a} ran schtasks.exe /query /fo LIST on {h}.",
             "{a} executed msiexec.exe /i agent_update.msi /qn on {h}.", "{a} launched bash /opt/scripts/backup_check.sh on {h}."]
MON_NOISE = ["Monitoring: CPU at {p} percent on {h}.", "Monitoring: memory pressure warning cleared on {h}.",
             "Monitoring: TLS certificate on {h} expires in {p} days.", "Monitoring: disk usage {p} percent on {h}."]
CHAT_NOISE = ["Message in #it-ops from {a}: anyone seeing slow VPN logins?", "Message in #it-ops from {a}: printer on floor 3 is back.",
              "Message in #soc from {a}: shift handover notes posted.", "Message in #it-ops from {a}: patch Tuesday prep starts tomorrow.",
              "Message in #soc from {a}: tuning the noisy certutil rule, ignore those for now."]
TICKET_NOISE = ["Ticket INC-{n} comment by {a}: user password reset completed.", "Ticket INC-{n} comment by {a}: awaiting vendor reply.",
                "Ticket REQ-{n} comment by {a}: access request approved by manager.", "Ticket INC-{n} comment by {a}: closed as duplicate."]

def noise(tl, rng, start, end, n, extra_hosts=()):
    hosts = OTHER_HOSTS + list(extra_hosts)
    span = int((end - start).total_seconds())
    for _ in range(n):
        ts = start + timedelta(seconds=rng.randrange(span))
        if ts.second == 0: ts += timedelta(seconds=7)
        r = rng.random(); h, a = rng.choice(hosts), rng.choice(OTHER_ACCTS)
        if r < 0.40: tl.add(ts, "edr", "ENDPOINT_TELEMETRY", rng.choice(EDR_NOISE).format(a=a, h=h))
        elif r < 0.62: tl.add(ts, "monitoring", "SYSTEM_EVENT", rng.choice(MON_NOISE).format(p=rng.randrange(20, 97), h=h))
        elif r < 0.80: tl.add(ts, "chat", "CHAT_MESSAGE", rng.choice(CHAT_NOISE).format(a=a))
        else: tl.add(ts, "ticketing", "TICKET_RECORD", rng.choice(TICKET_NOISE).format(n=rng.randrange(40000, 49999), a=a))

def other_change(tl, ts, cid, host, acct, frm, until, what):
    tl.add(ts, "change_management", "CHANGE_RECORD", f"Change record {cid} APPROVED. Scope: host {host}, account {acct}, activity: {what}. Window {frm} UTC to {until} UTC.")

def task(tid, pair, v, policy, alert_name, alert_src, asset, tl_sem_ids, alert_tag, ref, spec, probes):
    timeline, sem, ids = tl_sem_ids
    assert timeline[-1]["event_id"] == ids[alert_tag], (tid, timeline[-1])
    last = timeline[-1]
    return {"task_id": tid, "model_visible": {"organization_policy": policy,
            "alert": {"source": alert_src, "rule_name": alert_name, "timestamp": last["timestamp"], "related_event_id": last["event_id"]},
            "asset_context": asset, "timeline": timeline},
            "hidden_reference": ref, "oracle_spec": {**spec, "event_semantics": sem, "evaluated_event_id": last["event_id"], "probes": probes},
            "internal_metadata": {"pair_id": pair, "variant": v, "synthetic": True, "human_review_status": "PENDING"}}

def ref(status, trans, rule, action, ev): return {"exception_status": status, "transition_event_id": trans, "governing_rule_id": rule, "primary_action": action, "required_evidence_ids": ev}

# ===== PAIR 7: chained extensions in long log; second extension recorded after (B) or before (A) the effective end =====
def p7(v):
    a = v == "A"; rng = random.Random(7)
    H, ACC, D = "sap-db-02.corp.example", "svc-sap-maint", "2026-10-30"
    ad = lambda p: {"host": H, "account": ACC, "process": p}
    tl = TL()
    tl.add(f"{D}T00:41:12Z", "change_management", "CHANGE_RECORD", f"Change record CHG-610 APPROVED. Scope: host {H}, account {ACC}, activity: SAP database kernel patch using sapcar.exe, R3trans.exe and PowerShell helper scripts. Window 01:00 UTC to 03:00 UTC.",
           {"change_id": "CHG-610", "status": "APPROVED", "host": H, "account": ACC, "approved_processes": "sapcar.exe,R3trans.exe,powershell.exe", "valid_from": f"{D}T01:00:00Z", "valid_until": f"{D}T03:00:00Z"}, "AUTHORIZATION_CREATE", "create")
    other_change(tl, f"{D}T00:44:30Z", "CHG-611", "crm-app-03.corp.example", "svc-crm", "01:30", "05:00", "CRM application server restart")
    other_change(tl, f"{D}T00:47:05Z", "CHG-616", "wiki-01.corp.example", "svc-wiki", "02:00", "04:00", "wiki plugin upgrade")
    checkpoints(tl, T(f"{D}T01:00:00Z"), T(f"{D}T04:10:00Z"))
    for ts, p, txt in [("01:07:44", "sapcar.exe", "sapcar.exe -xvf SAPEXEDB_812.SAR -R D:\\stage"), ("01:26:19", "powershell.exe", "powershell.exe -File D:\\stage\\stop_sap.ps1"),
                       ("01:58:51", "R3trans.exe", "R3trans.exe -d"), ("02:21:36", "sapcar.exe", "sapcar.exe -xvf SAPEXE_812.SAR -R D:\\usr\\sap"),
                       ("03:12:08", "R3trans.exe", "R3trans.exe -x"), ("03:44:57", "powershell.exe", "powershell.exe -File D:\\stage\\verify_kernel.ps1")]:
        tl.add(f"{D}T{ts}Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed {txt} on {H}.", ad(p), "ACTIVITY")
    end1 = "03:50" if a else "03:40"
    tl.add(f"{D}T02:36:40Z", "change_management", "CHANGE_RECORD", f"Change record CHG-610 EXTENDED. New window end {end1} UTC. Reason: kernel extraction slower than planned.",
           {"change_id": "CHG-610", "new_valid_until": f"{D}T{end1}:00Z"}, "AUTHORIZATION_EXTEND", "ext1")
    tl.add(f"{D}T02:52:13Z", "ticketing", "TICKET_RECORD", "Ticket comment on CHG-610 by SAP basis lead: we will probably need until 04:30, will raise it in the change system if so.")
    tl.add(f"{D}T03:05:02Z", "change_management", "CHANGE_RECORD", "Change record CHG-611 EXTENDED. New window end 05:30 UTC. Reason: CRM cache warm-up.")
    tl.add(f"{D}T03:47:25Z", "change_management", "CHANGE_RECORD", "Change record CHG-610 EXTENDED. New window end 04:30 UTC. Reason: post-patch verification and restart.",
           {"change_id": "CHG-610", "new_valid_until": f"{D}T04:30:00Z"}, "AUTHORIZATION_EXTEND", "ext2")
    tl.add(f"{D}T03:58:40Z", "chat", "CHAT_MESSAGE", "Message from the SAP basis lead in #sap-maint: restart next, we are covered until 04:30.")
    tl.add(f"{D}T04:00:31Z", "change_management", "CHANGE_RECORD", "Change record CHG-610 implementation note: kernel files replaced; restart pending.")
    noise(tl, rng, T(f"{D}T00:35:00Z"), T(f"{D}T04:10:00Z"), 48)
    tl.add(f"{D}T04:14:22Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} launched powershell.exe -ExecutionPolicy Bypass -File D:\\stage\\post_patch_check.ps1 on {H}.", ad("powershell.exe"), "ACTIVITY", "alert")
    built = tl.build(); ids = built[2]
    r = (ref("ACTIVE", None, "CHG-02", "DOCUMENT", [ids["create"], ids["ext1"], ids["ext2"], ids["alert"]]) if a
         else ref("TERMINATED", ids["ck0340"], "CHG-05", "ESCALATE_TIER2", [ids["create"], ids["ext1"], ids["ext2"], ids["ck0340"]]))
    sp = {"exception_id": "CHG-610", "scope_model": "MAINTENANCE", "authority": MAINT_AUTH, "rule_map": MAINT_RULE_MAP}
    t = task(f"SS-DEV-007{v}", "DEV-PAIR-007", v, MAINT_POLICY, "Service account PowerShell with ExecutionPolicy Bypass on production database host", "edr",
             {"hostname": H, "environment": "production", "criticality": "critical"}, built, "alert", r, sp, ["LATE_EXTENSION", "STALE_AUTHORIZATION"])
    return t, [f"timeline[{ids['ext1']}].event", f"timeline[{ids['ext1']}].details.new_valid_until"], "Two chained change-system extensions in a long noisy log; whether the second is recorded before or after the effective end set by the first differs."

# ===== PAIR 8: extension for a look-alike change on a sister host =====
def p8(v):
    a = v == "A"; rng = random.Random(8)
    H, ACC, D = "fs-backup-01.corp.example", "svc-backup", "2026-10-31"
    H2, ACC2 = "fs-backup-10.corp.example", "svc-backup2"
    ad = lambda p: {"host": H, "account": ACC, "process": p}
    tl = TL()
    tl.add(f"{D}T21:32:40Z", "change_management", "CHANGE_RECORD", f"Change record CHG-7041 APPROVED. Scope: host {H}, account {ACC}, activity: backup catalog rebuild and volume shadow copy cleanup using vssadmin.exe and the vendor tool bkcatalog.exe. Window 22:00 UTC to 23:30 UTC.",
           {"change_id": "CHG-7041", "status": "APPROVED", "host": H, "account": ACC, "approved_processes": "vssadmin.exe,bkcatalog.exe", "valid_from": f"{D}T22:00:00Z", "valid_until": f"{D}T23:30:00Z"}, "AUTHORIZATION_CREATE", "create")
    tl.add(f"{D}T21:35:16Z", "change_management", "CHANGE_RECORD", f"Change record CHG-7014 APPROVED. Scope: host {H2}, account {ACC2}, activity: backup catalog rebuild and volume shadow copy cleanup using vssadmin.exe and the vendor tool bkcatalog.exe. Window 22:00 UTC to 23:30 UTC.")
    checkpoints(tl, T(f"{D}T22:00:00Z"), T(f"{D}T23:50:00Z"))
    for ts, p, txt in [("22:04:09", "bkcatalog.exe", "bkcatalog.exe --export D:\\cat\\pre.xml"), ("22:31:47", "vssadmin.exe", "vssadmin.exe list shadows"),
                       ("22:58:30", "bkcatalog.exe", "bkcatalog.exe --rebuild --full"), ("23:16:05", "bkcatalog.exe", "bkcatalog.exe --verify")]:
        tl.add(f"{D}T{ts}Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed {txt} on {H}.", ad(p), "ACTIVITY")
    for ts, txt in [("22:06:51", "bkcatalog.exe --export E:\\cat\\pre.xml"), ("22:44:12", "vssadmin.exe list shadows"), ("23:09:33", "bkcatalog.exe --rebuild --full"),
                    ("23:41:27", "vssadmin.exe delete shadows /for=E: /oldest /quiet")]:
        tl.add(f"{D}T{ts}Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC2} executed {txt} on {H2}.")
    if a:
        tl.add(f"{D}T23:21:58Z", "change_management", "CHANGE_RECORD", f"Change record CHG-7041 EXTENDED. New window end 00:30 UTC (1 Nov). Reason: catalog rebuild running long on {H}.",
               {"change_id": "CHG-7041", "new_valid_until": "2026-11-01T00:30:00Z"}, "AUTHORIZATION_EXTEND", "ext")
    else:
        tl.add(f"{D}T23:21:58Z", "change_management", "CHANGE_RECORD", f"Change record CHG-7014 EXTENDED. New window end 00:30 UTC (1 Nov). Reason: catalog rebuild running long on {H2}.",
               {"change_id": "CHG-7014", "new_valid_until": "2026-11-01T00:30:00Z"}, tag="ext")
    tl.add(f"{D}T23:26:44Z", "chat", "CHAT_MESSAGE", "Message from the backup team lead in #backup-ops: extension is in, both rebuilds keep going, shadow cleanup after.")
    tl.add(f"{D}T23:35:10Z", "ticketing", "TICKET_RECORD", "Ticket comment on CHG-7041 and CHG-7014 by backup engineer: shadow copy cleanup is the last step on both servers.")
    noise(tl, rng, T(f"{D}T21:25:00Z"), T(f"{D}T23:50:00Z"), 46)
    tl.add(f"{D}T23:52:39Z", "edr", "ENDPOINT_TELEMETRY", f"{ACC} executed vssadmin.exe delete shadows /for=D: /oldest /quiet on {H}.", ad("vssadmin.exe"), "ACTIVITY", "alert")
    built = tl.build(); ids = built[2]
    r = (ref("ACTIVE", None, "CHG-02", "DOCUMENT", [ids["create"], ids["ext"], ids["alert"]]) if a
         else ref("TERMINATED", ids["ck2330"], "CHG-05", "ESCALATE_TIER2", [ids["create"], ids["ck2330"]]))
    sp = {"exception_id": "CHG-7041", "scope_model": "MAINTENANCE", "authority": MAINT_AUTH, "rule_map": MAINT_RULE_MAP}
    t = task(f"SS-DEV-008{v}", "DEV-PAIR-008", v, MAINT_POLICY, "Shadow copy deletion by service account (possible ransomware precursor)", "edr",
             {"hostname": H, "environment": "production", "criticality": "high"}, built, "alert", r, sp, ["LOOKALIKE_RECORD", "STALE_AUTHORIZATION"])
    return t, [f"timeline[{ids['ext']}].event", f"timeline[{ids['ext']}].details.change_id"], "Extension recorded for the alerted change vs for a look-alike change (CHG-7014) on a sister host."

# ===== PAIR 9: exfil to the approved sink vs a look-alike IP, long engagement log =====
ING, SINK, SUF = "198.51.100.40", "203.0.113.60", ".stg.corp.example"
def pt_create(tl, ts, eid, frm, until, wtext):
    tl.add(ts, "engagement_register", "ENGAGEMENT_RECORD",
           f"Rules of engagement {eid} APPROVED. Approved initial ingress source: {ING} (tester jump host). Approved targets: all hosts under stg.corp.example. Approved methods: password spraying against approved targets; PsExec lateral movement between approved targets (the only method approved for lateral movement); LSASS credential dumping on approved targets; exfiltration simulation of protected test data to the approved sink {SINK} (sink2.pentest.example.net) only. Window {wtext}.",
           {"engagement_id": eid, "status": "APPROVED", "approved_ingress_source": ING, "approved_target_suffix": SUF,
            "approved_methods": "password_spraying,psexec_lateral_movement,lsass_credential_dumping,exfiltration_simulation",
            "lateral_methods": "psexec_lateral_movement", "approved_sinks": SINK, "valid_from": frm, "valid_until": until}, "AUTHORIZATION_CREATE", "create")

def pt_activity(tl, D, items):
    for ts, kind, d, text in items:
        tl.add(f"{D}T{ts}Z", "netflow" if "src_ip" in d or kind == "DATA_TRANSFER" else "edr", "NETWORK_TELEMETRY" if "src_ip" in d or kind == "DATA_TRANSFER" else "ENDPOINT_TELEMETRY", text, d, kind)

def p9(v):
    a = v == "A"; rng = random.Random(9); D = "2026-10-30"
    tl = TL()
    pt_create(tl, f"{D}T06:48:00Z", "PT-2026-051", f"{D}T07:00:00Z", f"{D}T15:00:00Z", "07:00 to 15:00 UTC on 30 Oct")
    checkpoints(tl, T(f"{D}T07:00:00Z"), T(f"{D}T12:30:00Z"), 30)
    F, J, DC = "stg-files-02.stg.corp.example", "stg-jump-01.stg.corp.example", "stg-dc-01.stg.corp.example"
    pt_activity(tl, D, [
        ("07:14:22", "ACTIVITY", {"src_ip": ING, "dst_host": DC, "technique": "password_spraying"}, f"412 failed Kerberos pre-authentication attempts across 160 accounts from {ING} against {DC} in 6 minutes."),
        ("07:52:10", "ACTIVITY", {"src_host": J, "dst_host": F, "account": "pt_red2", "technique": "psexec_lateral_movement"}, f"Account pt_red2 used PsExec from {J} to start a service on {F}."),
        ("08:33:41", "ACTIVITY", {"host": F, "account": "pt_red2", "technique": "lsass_credential_dumping"}, f"Process procdump64.exe running as pt_red2 read lsass.exe memory on {F}."),
        ("09:21:05", "DATA_TRANSFER", {"src_host": F, "destination_ip": SINK, "destination_host": "sink2.pentest.example.net", "data_classification": "PROTECTED", "technique": "exfiltration_simulation"},
         f"Protected file payroll_synthetic_q3.csv (12 MB) transferred over HTTPS from {F} to {SINK} (sink2.pentest.example.net)."),
        ("10:47:38", "DATA_TRANSFER", {"src_host": F, "destination_ip": SINK, "destination_host": "sink2.pentest.example.net", "data_classification": "PROTECTED", "technique": "exfiltration_simulation"},
         f"Protected file contracts_synthetic.zip (48 MB) transferred over HTTPS from {F} to {SINK} (sink2.pentest.example.net)."),
    ])
    tl.add(f"{D}T09:40:17Z", "chat", "CHAT_MESSAGE", "Message from tester pt_red2 in #pentest-coord: sink2 is getting slow, might move the next batch to the backup sink 203.0.113.66.")
    tl.add(f"{D}T09:55:00Z", "engagement_register", "ENGAGEMENT_RECORD", "Engagement register status note for PT-2026-051: exfiltration simulation phase in progress.")
    tl.add(f"{D}T11:02:44Z", "dlp", "DLP_TELEMETRY", "DLP: internal file share sync of marketing_assets.zip (unclassified) from crm-app-03.corp.example to the corporate file share.")
    noise(tl, rng, T(f"{D}T06:40:00Z"), T(f"{D}T12:50:00Z"), 55, extra_hosts=["dlp-gw-01.corp.example"])
    dst, dh = (SINK, "sink2.pentest.example.net") if a else ("203.0.113.66", "sink2-backup.pentest.example.net")
    tl.add(f"{D}T12:58:09Z", "netflow", "NETWORK_TELEMETRY", f"Protected file hr_reviews_synthetic.xlsx (27 MB) transferred over HTTPS from {F} to {dst} ({dh}).",
           {"src_host": F, "destination_ip": dst, "destination_host": dh, "data_classification": "PROTECTED", "technique": "exfiltration_simulation"}, "DATA_TRANSFER", "alert")
    built = tl.build(); ids = built[2]
    r = (ref("ACTIVE", None, "PT-02", "MONITOR", [ids["create"], ids["alert"]]) if a
         else ref("TERMINATED", ids["alert"], "PT-03", "OPEN_INCIDENT", [ids["create"], ids["alert"]]))
    sp = {"exception_id": "PT-2026-051", "scope_model": "PENTEST", "authority": PT_AUTH, "rule_map": PT_RULE_MAP}
    t = task(f"SS-DEV-009{v}", "DEV-PAIR-009", v, PT_POLICY, "Protected data transferred to external host during engagement", "siem",
             {"hostname": F, "environment": "staging", "criticality": "medium"}, built, "alert", r, sp, ["LOOKALIKE_DESTINATION", "STOP_CONDITION"])
    return t, [f"timeline[{ids['alert']}].event", f"timeline[{ids['alert']}].details.destination_ip", f"timeline[{ids['alert']}].details.destination_host"], "Exfiltration simulation to the approved sink vs to a look-alike 'backup sink' a tester mentioned only in chat."

# ===== PAIR 10: pentest window in local time + relative-minutes extension =====
def p10(v):
    a = v == "A"; rng = random.Random(10); D = "2026-10-29"
    tl = TL()
    pt_create(tl, f"{D}T06:20:00Z", "PT-2026-047", f"{D}T09:00:00+02:00", f"{D}T17:00:00+02:00", "09:00 to 17:00 local time (UTC+02:00) on 29 Oct")
    checkpoints(tl, T(f"{D}T07:00:00Z"), T(f"{D}T16:00:00Z"), 30)
    F, J, DC = "stg-app-04.stg.corp.example", "stg-jump-02.stg.corp.example", "stg-dc-02.stg.corp.example"
    pt_activity(tl, D, [
        ("07:21:33", "ACTIVITY", {"src_ip": ING, "dst_host": DC, "technique": "password_spraying"}, f"388 failed Kerberos pre-authentication attempts across 140 accounts from {ING} against {DC} in 7 minutes."),
        ("09:12:48", "ACTIVITY", {"src_host": J, "dst_host": F, "account": "pt_blue1", "technique": "psexec_lateral_movement"}, f"Account pt_blue1 used PsExec from {J} to start a service on {F}."),
        ("11:46:02", "ACTIVITY", {"host": F, "account": "pt_blue1", "technique": "lsass_credential_dumping"}, f"Process rundll32.exe comsvcs.dll MiniDump running as pt_blue1 read lsass.exe memory on {F}."),
    ])
    mins = 90 if a else 60
    tl.add(f"{D}T14:31:26Z", "engagement_register", "ENGAGEMENT_RECORD", f"Rules of engagement PT-2026-047 EXTENDED by {mins} minutes beyond the currently approved window end. Reason: domain controller testing delayed by change freeze.",
           {"engagement_id": "PT-2026-047", "new_valid_until": f"{D}T{'18:30' if a else '18:00'}:00+02:00"}, "AUTHORIZATION_EXTEND", "ext")
    tl.add(f"{D}T14:40:51Z", "chat", "CHAT_MESSAGE", "Message from tester pt_blue1 in #pentest-coord: thanks for the extension, we finish the DC part by 18:15.")
    tl.add(f"{D}T15:20:00Z", "engagement_register", "ENGAGEMENT_RECORD", "Engagement register status note for PT-2026-047: final phase started.")
    tl.add(f"{D}T15:45:37Z", "email", "EMAIL_MESSAGE", "Email from the engagement manager to the SOC: please note the testers are working late today, extension approved.")
    noise(tl, rng, T(f"{D}T06:15:00Z"), T(f"{D}T16:05:00Z"), 52)
    tl.add(f"{D}T16:12:40Z", "edr", "ENDPOINT_TELEMETRY", f"Process procdump64.exe -ma lsass.exe running as pt_blue1 dumped LSASS memory on {DC}.",
           {"host": DC, "account": "pt_blue1", "technique": "lsass_credential_dumping"}, "ACTIVITY", "alert")
    built = tl.build(); ids = built[2]
    r = (ref("ACTIVE", None, "PT-02", "MONITOR", [ids["create"], ids["ext"], ids["alert"]]) if a
         else ref("TERMINATED", ids["ck1600"], "PT-05", "ESCALATE_TIER2", [ids["create"], ids["ext"], ids["ck1600"]]))
    sp = {"exception_id": "PT-2026-047", "scope_model": "PENTEST", "authority": PT_AUTH, "rule_map": PT_RULE_MAP}
    t = task(f"SS-DEV-010{v}", "DEV-PAIR-010", v, PT_POLICY, "LSASS memory dump on domain controller", "edr",
             {"hostname": DC, "environment": "staging", "criticality": "high"}, built, "alert", r, sp, ["TIMEZONE", "RELATIVE_EXTENSION"])
    return t, [f"timeline[{ids['ext']}].event", f"timeline[{ids['ext']}].details.new_valid_until"], "Window in local time (UTC+02:00) with a relative extension (90 vs 60 minutes); the alert at 16:12 UTC is inside one window and outside the other."

pairs = {}
for b in (p7, p8, p9, p10):
    for v in "AB":
        t, diffs, purpose = b(v)
        (OUT / f"{t['task_id']}.json").write_text(json.dumps(t, indent=2) + "\n")
    pid = t["internal_metadata"]["pair_id"]
    pairs[pid] = {"variants": {"A": pid.replace("DEV-PAIR-", "SS-DEV-") + "A", "B": pid.replace("DEV-PAIR-", "SS-DEV-") + "B"}, "expected_model_visible_differences": diffs, "purpose": purpose}
(OUT / "pairs.json").write_text(json.dumps(pairs, indent=2) + "\n")
print("ok")
