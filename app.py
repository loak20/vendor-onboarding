"""Vendor onboarding demo — Streamlit UI.

Tab 1: enter a vendor submission, upload three documents, watch every stage run.
Tab 2: operations dashboard with history, per-run evidence, logs and reference data.

Run with:  streamlit run app.py

Settings come from `.env` locally, or from the app's Secrets on Streamlit Community
Cloud: GEMINI_API_KEY, GEMINI_MODEL, DATABASE_URL, SEED_FILE, APP_TIMEZONE,
PUBLIC_DEMO, TEST_PACK_URL (see .env.example and DEPLOYMENT.md).
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time

import streamlit as st


def _secrets_to_env() -> None:
    """Make Streamlit secrets visible to the other modules exactly like .env values.

    Streamlit already copies top-level *string* secrets into os.environ; this also
    covers booleans and numbers. Real environment variables are never overridden.
    Runs before the imports below, because they read their settings at import time.
    """
    try:
        if not st.secrets.load_if_toml_exists():
            return
        for key, value in st.secrets.items():
            if isinstance(value, (str, int, float, bool)) and key not in os.environ:
                os.environ[key] = str(value)
    except Exception as exc:  # malformed secrets.toml: report it, keep .env behaviour
        print(f"[config] could not read Streamlit secrets: {exc}", file=sys.stderr)


_secrets_to_env()

import database as db  # noqa: E402
import extractor  # noqa: E402
from decision_engine import APPROVED_LABEL, decide, draft_email  # noqa: E402
from validator import (DOC_LABELS, candidate_pans, mask_account, normalize_account,  # noqa: E402
                       normalize_identifier, run_all_checks, select_evidence)

st.set_page_config(page_title="Vendor Onboarding", page_icon="🧾", layout="wide")

PUBLIC_DEMO = (os.getenv("PUBLIC_DEMO") or "").strip().lower() in {"1", "true", "yes", "on"}
TEST_PACK_URL = (os.getenv("TEST_PACK_URL") or "").strip()


@st.cache_resource(show_spinner="Connecting to storage…")
def prepare_storage() -> list[str]:
    """Once per server process: create the empty tables and, only if SEED_FILE is set,
    load the reference records with the same idempotent routine as
    `python database.py --seed` (approved_vendors and blocklist only).
    Returns warnings to show in the UI."""
    db.init_db()
    seed_file = (os.getenv("SEED_FILE") or "").strip()
    if not seed_file:
        return []
    try:
        results = db.apply_seed(db.resolve_seed_path(seed_file))
    except db.SeedError as exc:
        print(f"[storage] SEED_FILE not loaded: {exc}", file=sys.stderr)
        return [f"`SEED_FILE` could not be loaded, so the reference tables may be empty. {exc}"]
    for r in results:
        print(f"[storage] seed {r['table']}: {'inserted' if r['inserted'] else 'already present'} "
              f"— {r['label']}", file=sys.stderr)
    return []


try:
    STORAGE_WARNINGS = prepare_storage()
except Exception as exc:  # e.g. wrong DATABASE_URL: explain instead of showing a traceback
    print(f"[storage] {db.describe()} unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
    st.error("**Storage unavailable.** The app could not open its database. If you run this "
             "deployment, check `DATABASE_URL` in the app's secrets; the full error is in the "
             "app logs.")
    st.stop()

FIELDS = [  # (key, label, placeholder, required)
    ("legal_name", "Legal name", "As registered for GST / PAN", True),
    ("trade_name", "Trade name (optional)", "Brand / DBA, if any", False),
    ("contact_email", "Contact email", "accounts@company.example", True),
    ("registered_address", "Registered address", "Street, city, state, PIN", True),
    ("pan", "PAN", "AAAAA9999A", True),
    ("gstin", "GSTIN", "15-character GSTIN", True),
    ("bank_account_number", "Bank account number", "Digits only", True),
    ("ifsc", "IFSC", "ABCD0123456", True),
]
UPLOADS = [("gst_certificate", "GST registration certificate"),
           ("pan_document", "PAN document"),
           ("bank_proof", "Bank account confirmation")]
STAGES = ["Receive", "Read documents", "Classify & extract", "Validate", "Decide",
          "Draft communication", "Persist"]
STATUS_ICON = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}

st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1280px;}
.badge {display:inline-block; padding:.45rem 1.1rem; border-radius:999px; font-weight:700;
        letter-spacing:.05em; color:#fff; font-size:1.05rem;}
.badge.APPROVED {background:#15803d;} .badge.PENDING {background:#b45309;}
.badge.REJECTED {background:#b91c1c;} .badge.PROCESSING {background:#475569;}
.decision {border-radius:12px; padding:1rem 1.2rem; margin:.4rem 0 1rem;
           border:1px solid rgba(128,128,128,.25);}
.decision.APPROVED {background:rgba(22,163,74,.08);} .decision.PENDING {background:rgba(217,119,6,.09);}
.decision.REJECTED {background:rgba(220,38,38,.08);}
.decision p {margin:.6rem 0 0; line-height:1.5;}
.rule {border:1px solid rgba(128,128,128,.22); border-left-width:4px; border-radius:8px;
       padding:.55rem .8rem; margin-bottom:.45rem;}
.rule.PASS {border-left-color:#16a34a;} .rule.WARN {border-left-color:#d97706;}
.rule.FAIL {border-left-color:#dc2626;}
.rule-head {display:flex; gap:.5rem; align-items:center; flex-wrap:wrap;}
.rule-head code {font-size:.8rem;}
.pill {font-size:.7rem; font-weight:700; padding:.1rem .5rem; border-radius:999px; color:#fff;}
.pill.PASS {background:#16a34a;} .pill.WARN {background:#d97706;} .pill.FAIL {background:#dc2626;}
.disp {font-size:.7rem; font-weight:600; padding:.1rem .5rem; border-radius:999px;
       border:1px solid rgba(128,128,128,.4);}
.disp.REJECT {color:#dc2626; border-color:#dc2626;} .disp.REVIEW {color:#d97706; border-color:#d97706;}
.rule-details {margin:.3rem 0 .15rem; font-size:.92rem;}
.rule-ev {font-size:.8rem; opacity:.75; word-break:break-word;}
.email {white-space:pre-wrap; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.8rem;
        line-height:1.45; border:1px solid rgba(128,128,128,.25); border-radius:8px; padding:.8rem 1rem;
        background:rgba(128,128,128,.06); margin-bottom:1rem;}
</style>
""", unsafe_allow_html=True)


# ==========================================================================
# Display helpers
# ==========================================================================

_ACCOUNT_RE = re.compile(r"\b\d{9,18}\b")


def mask_text(value) -> str:
    """Mask anything that looks like a bank account number for ordinary display."""
    return _ACCOUNT_RE.sub(lambda m: mask_account(m.group()), "" if value is None else str(value))


def masked_extraction(obj: dict | None) -> dict | None:
    if not obj:
        return obj
    shown = json.loads(json.dumps(obj))
    if shown["fields"].get("bank_account_number"):
        shown["fields"]["bank_account_number"] = mask_account(shown["fields"]["bank_account_number"])
    return shown


def esc(value) -> str:
    return html.escape(mask_text(value))


def rules_html(rules: list[dict]) -> str:
    blocks = []
    for r in rules:
        evidence = " · ".join(f"<b>{esc(k)}</b>: {esc(v)}" for k, v in r["evidence"].items())
        disp = (f'<span class="disp {r["disposition"]}">{r["disposition"]}</span>'
                if r["disposition"] != "NONE" else "")
        blocks.append(
            f'<div class="rule {r["status"]}"><div class="rule-head">'
            f'<span class="pill {r["status"]}">{r["status"]}</span>'
            f'<code>{esc(r["rule_id"])}</code>{disp}</div>'
            f'<div class="rule-details">{esc(r["details"])}</div>'
            f'<div class="rule-ev">{evidence}</div></div>')
    return "".join(blocks)


def decision_html(status: str, explanation: str) -> str:
    label = {"APPROVED": APPROVED_LABEL, "PENDING": "Pending — needs review / vendor follow-up",
             "REJECTED": "Rejected"}.get(status, status)
    return (f'<div class="decision {status}"><span class="badge {status}">{status}</span>'
            f'&nbsp;&nbsp;<b>{esc(label)}</b><p>{esc(explanation)}</p></div>')


def render_submission(sub: dict, logs: list[dict], show_form: bool = True) -> None:
    """Shared result view (used after a live run and in the dashboard)."""
    st.markdown(decision_html(sub["status"], sub.get("explanation") or ""), unsafe_allow_html=True)
    rules = sub.get("rules") or []
    counts = {s: sum(r["status"] == s for r in rules) for s in ("PASS", "WARN", "FAIL")}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Checks passed", counts["PASS"])
    c2.metric("Warnings", counts["WARN"])
    c3.metric("Failures", counts["FAIL"])
    c4.metric("Run time", f"{(sub.get('duration_ms') or 0) / 1000:.1f}s")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("##### Validation rules & evidence")
        if rules:
            st.markdown(rules_html(rules), unsafe_allow_html=True)
        else:
            st.caption("No rule results were recorded.")
    with right:
        st.markdown("##### Drafted vendor email")
        if sub.get("email_draft"):
            st.markdown(f'<div class="email">{esc(sub["email_draft"])}</div>', unsafe_allow_html=True)
        else:
            st.caption("No email needed — submission approved.")
        st.markdown("##### Extracted document data")
        extracted = sub.get("extracted") or {}
        files = sub.get("files") or {}
        for slot, label in UPLOADS:
            obj = extracted.get(slot)
            name = (files.get(slot) or {}).get("name", "no file")
            detected = obj["document_type_detected"] if obj else "—"
            with st.expander(f"{label} · {name} → {detected}"):
                if obj:
                    st.json(masked_extraction(obj))
                else:
                    st.caption("No document processed for this slot.")
        if show_form:
            with st.expander("Submitted form values"):
                st.json({k: (mask_account(sub[k]) if k == "bank_account_number" else sub[k])
                         for k, *_ in FIELDS})
    with st.expander(f"Execution log ({len(logs)} entries)"):
        st.dataframe([{"time": row["ts"][11:23], "stage": row["stage"], "level": row["level"],
                       "message": mask_text(row["message"])} for row in logs],
                     hide_index=True)
    if sub.get("errors"):
        with st.expander(f"Errors ({len(sub['errors'])})"):
            for err in sub["errors"]:
                st.error(f"[{err['stage']}] {mask_text(err['message'])}")


# ==========================================================================
# Pipeline (runs only on explicit Submit)
# ==========================================================================

class RunLog:
    def __init__(self, submission_id: int):
        self.sid = submission_id
        self.errors: list[dict] = []

    def __call__(self, stage: str, level: str, message: str, details=None) -> str:
        if level == "ERROR":
            self.errors.append({"stage": stage, "message": message})
        return db.log(self.sid, stage, level, message, details)


def run_pipeline(form: dict, uploads: dict) -> int:
    started = time.perf_counter()
    progress = st.progress(0.0, text="Starting…")

    def advance(i: int, text: str) -> None:
        progress.progress(i / len(STAGES), text=f"Stage {min(i + 1, len(STAGES))}/{len(STAGES)} · {text}")

    files_meta = {slot: ({"name": f.name, "size": f.size} if f else None) for slot, f in uploads.items()}
    file_names = {slot: (f.name if f else None) for slot, f in uploads.items()}

    # 1 — Receive and persist
    advance(0, "Receiving submission")
    with st.status("① Receive & persist submission", expanded=False) as box:
        sid = db.create_submission(form, files_meta)
        log = RunLog(sid)
        ts = log(STAGES[0], "INFO", f"Submission #{sid} created",
                 {"files": files_meta, "legal_name": form["legal_name"]})
        st.write(f"Submission **#{sid}** stored at `{ts}` with "
                 f"{sum(bool(f) for f in uploads.values())} of 3 documents.")
        box.update(label=f"① Submission #{sid} received and stored", state="complete")

    try:
        # 2 — Read documents
        advance(1, "Reading documents")
        reads = {}
        with st.status("② Reading documents (file checks, PDF text layer, page rendering)",
                       expanded=True) as box:
            for slot, label in UPLOADS:
                f = uploads[slot]
                if f is None:
                    st.write(f"⚪ **{label}** — no file uploaded")
                    log(STAGES[1], "WARN", f"{slot}: no file uploaded")
                    continue
                r = extractor.read_document(slot, f.name, f.getvalue())
                reads[slot] = r
                st.write(f"{'✅' if r.ok else '❌'} **{label}** · `{f.name}` — {r.summary()}")
                log(STAGES[1], "INFO" if r.ok else "ERROR", f"{slot}: {r.summary()}",
                    {"pages": r.page_count, "bytes": r.file_bytes, "method": r.extraction_method})
            bad = sum(not r.ok for r in reads.values()) + (3 - len(reads))
            box.update(label=f"② Documents read — {len(reads)} received, "
                             f"{sum(r.ok for r in reads.values())} readable",
                       state="error" if bad else "complete", expanded=bool(bad))

        # 3 — Classify by content and extract
        advance(2, f"Classifying & extracting with {extractor.GEMINI_MODEL}")
        extracted: dict = {slot: None for slot, _ in UPLOADS}
        with st.status(f"③ Classifying by content & extracting fields ({extractor.GEMINI_MODEL})",
                       expanded=True) as box:
            for slot, label in UPLOADS:
                if slot not in reads:
                    continue
                r = reads[slot]
                if r.ok:
                    st.write(f"⏳ **{label}** — sending {r.extraction_method} input to Gemini…")
                obj, reason = extractor.extract_document(
                    r, log=lambda lvl, msg, s=slot: log(STAGES[2], lvl, f"{s}: {msg}"))
                extracted[slot] = obj
                hard = [e for e in obj["errors"] if e["code"] != "FIELD_NOT_IN_TEXT"]
                expected = {"gst_certificate": "GST_CERTIFICATE", "pan_document": "PAN_DOCUMENT",
                            "bank_proof": "BANK_PROOF"}[slot]
                if hard:
                    st.write(f"❌ **{label}** — " + "; ".join(e["message"] for e in hard))
                    log(STAGES[2], "ERROR", f"{slot}: " + "; ".join(e["message"] for e in hard))
                else:
                    found = [k for k, v in obj["fields"].items() if v]
                    icon = "✅" if obj["document_type_detected"] == expected else "⚠️"
                    st.write(f"{icon} **{label}** → detected **{obj['document_type_detected']}** via "
                             f"{obj['extraction_method']}; fields found: {', '.join(found) or 'none'}"
                             + (f"  \n<small>{html.escape(reason)}</small>" if reason else ""),
                             unsafe_allow_html=True)
                    log(STAGES[2], "INFO", f"{slot}: detected {obj['document_type_detected']} "
                        f"({obj['extraction_method']}); fields: {', '.join(found) or 'none'}",
                        {"reason": reason})
                for e in obj["errors"]:
                    if e["code"] == "FIELD_NOT_IN_TEXT":
                        st.write(f"↳ ⚠️ {e['message']}")
                        log(STAGES[2], "WARN", f"{slot}: {e['message']}")
            db.save_extraction(sid, extracted)
            any_bad = any(o and o["errors"] for o in extracted.values())
            box.update(label="③ Documents classified and fields extracted"
                       + (" — with issues" if any_bad else ""),
                       state="error" if any_bad else "complete", expanded=any_bad)

        # 4 — Validate (deterministic Python)
        advance(3, "Running validation rules")
        with st.status("④ Validating with deterministic rules", expanded=False) as box:
            evidence = select_evidence(extracted)
            pans = candidate_pans(form, evidence)
            approved = db.find_approved_candidates(normalize_identifier(form["pan"]),
                                                   normalize_identifier(form["gstin"]),
                                                   normalize_account(form["bank_account_number"]))
            blocked = db.find_blocklisted(pans)
            log(STAGES[3], "INFO", f"Reference lookups: {len(approved)} approved-vendor candidate(s), "
                f"{len(blocked)} blocklist hit(s)")
            rules = run_all_checks(form, extracted, file_names, approved, blocked)
            for r in rules:
                st.write(f"{STATUS_ICON[r['status']]} `{r['rule_id']}` — {mask_text(r['details'])}")
                log(STAGES[3], "INFO" if r["status"] == "PASS" else "WARN",
                    f"{r['rule_id']} {r['status']} ({r['disposition']}): {r['details']}")
            fails = sum(r["status"] == "FAIL" for r in rules)
            box.update(label=f"④ {len(rules)} checks run — {fails} failed",
                       state="error" if fails else "complete")

        # 5 — Decide
        advance(4, "Applying decision policy")
        with st.status("⑤ Deciding (REJECT > REVIEW > APPROVE)", expanded=False) as box:
            status, explanation = decide(rules)
            st.write(explanation)
            log(STAGES[4], "INFO", f"Decision: {status}", {"explanation": explanation})
            box.update(label=f"⑤ Decision: {status}", state="complete")

        # 6 — Draft communication
        advance(5, "Drafting vendor communication")
        with st.status("⑥ Drafting vendor email", expanded=False) as box:
            email = draft_email(form, status, rules, sid)
            msg = "Drafted correction/rejection email (not sent)" if email else "Approved — no email needed"
            st.write(msg)
            log(STAGES[5], "INFO", msg)
            box.update(label=f"⑥ {msg}", state="complete")

        # 7 — Persist
        advance(6, "Saving results")
        with st.status("⑦ Persisting results", expanded=False) as box:
            if status == "APPROVED":
                inserted = db.insert_approved_vendor(
                    {"legal_name": form["legal_name"], "pan": normalize_identifier(form["pan"]),
                     "gstin": normalize_identifier(form["gstin"]),
                     "bank_account_number": normalize_account(form["bank_account_number"]),
                     "ifsc": normalize_identifier(form["ifsc"])}, source=f"submission #{sid}")
                log(STAGES[6], "INFO", "Added to approved_vendors" if inserted
                    else "Already present in approved_vendors (idempotent)")
            duration = int((time.perf_counter() - started) * 1000)
            log(STAGES[6], "INFO", f"Run complete in {duration} ms")
            db.complete_submission(sid, status=status, rules=rules, explanation=explanation,
                                   email_draft=email, errors=log.errors, duration_ms=duration)
            box.update(label=f"⑦ Results saved — submission #{sid}", state="complete")
    except Exception as exc:  # never leave a run half-finished or approved by accident
        duration = int((time.perf_counter() - started) * 1000)
        log("Pipeline", "ERROR", f"Unexpected error: {type(exc).__name__}: {exc}")
        db.complete_submission(sid, status="PENDING", rules=[],
                               explanation="Pending — an internal processing error occurred; "
                                           "manual review required.",
                               email_draft=None, errors=log.errors, duration_ms=duration)
        st.error(f"Unexpected error during processing — submission #{sid} routed to PENDING. {exc}")
    progress.progress(1.0, text="Done")
    return sid


# ==========================================================================
# Tab 1 — New submission & live run
# ==========================================================================

def reviewer_guide() -> None:
    """Short how-to for people trying the hosted demo (shown only when PUBLIC_DEMO is set)."""
    with st.expander("👋 Reviewing this demo? Start here"):
        pack = (f"[**Download the test pack**]({TEST_PACK_URL})" if TEST_PACK_URL
                else "Get the test pack from the project repository")
        st.markdown(f"""
1. {pack}: 8 fictional vendors, each with `form_input.json` and three PDFs, plus
   `TESTING_GUIDE.md` with copy-ready form values and the expected outcome of each scenario.
2. Type or paste one vendor's eight form values, upload its PDFs (`gst_certificate.pdf` → GST slot,
   `pan_card.pdf` → PAN slot, `bank_proof.pdf` → bank slot), and click **Submit for review**.
   **Vendor 06:** put `Electricity_Utility_Bill.pdf` in the **GST** slot.
3. Click **Start a new submission** before the next vendor.
4. Open **Operations Dashboard** for history, evidence, stage logs and the reference tables.

Vendors 07 and 08 test against reference records that are already loaded (one approved vendor,
one blocklisted entity). Approvals are remembered: if Vendor 01 or 02 comes back as
`DUPLICATE_VENDOR`, someone has already approved it, so use **Reset demo approvals** in the
Operations Dashboard and submit again.

All test data is synthetic. Please don't upload real documents: uploads are sent to Google Gemini
for extraction, and the results are stored in this demo's database.
""")


def new_submission_tab() -> None:
    if not os.getenv("GEMINI_API_KEY"):
        st.warning("`GEMINI_API_KEY` is not set. Documents cannot be extracted, so every "
                   "submission will be routed to **PENDING** (`EXTRACTION_UNAVAILABLE`). "
                   "Add the key to `.env` (local) or to the app's Secrets (hosted), then restart.")
    if PUBLIC_DEMO:
        reviewer_guide()
    nonce = st.session_state.setdefault("form_nonce", 0)
    with st.form(f"submission_{nonce}"):
        st.markdown("##### Vendor details")
        cols = st.columns(2)
        form = {}
        for i, (key, label, placeholder, _) in enumerate(FIELDS):
            form[key] = cols[i % 2].text_input(label, placeholder=placeholder, key=f"{key}_{nonce}", autocomplete="off")
        st.markdown("##### Documents  ·  PDF, PNG or JPG, up to 10 MB / 5 pages each")
        ucols = st.columns(3)
        uploads = {slot: ucols[i].file_uploader(label, type=["pdf", "png", "jpg", "jpeg"],
                                                key=f"{slot}_{nonce}")
                   for i, (slot, label) in enumerate(UPLOADS)}
        submitted = st.form_submit_button("Submit for review", type="primary")

    if st.button("Start a new submission", help="Clear the form and uploads"):
        st.session_state["form_nonce"] = nonce + 1
        st.session_state.pop("last_submission_id", None)
        st.rerun()

    if submitted:
        form = {k: (v or "").strip() for k, v in form.items()}
        missing = [label for key, label, _, required in FIELDS if required and not form[key]]
        if missing:
            st.error("Please fill in the required fields: " + ", ".join(missing))
            return
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", form["contact_email"]):
            st.error("Contact email does not look like a valid email address.")
            return
        st.markdown("#### Live run")
        sid = run_pipeline(form, uploads)
        st.session_state["last_submission_id"] = sid
        st.markdown("#### Result")
        render_submission(db.get_submission(sid), db.get_logs(sid), show_form=False)
    elif st.session_state.get("last_submission_id"):
        sid = st.session_state["last_submission_id"]
        sub = db.get_submission(sid)
        if sub:
            st.markdown(f"#### Last result · submission #{sid}")
            render_submission(sub, db.get_logs(sid), show_form=False)


# ==========================================================================
# Tab 2 — Operations dashboard
# ==========================================================================

def dashboard_tab() -> None:
    counts = db.status_counts()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total submissions", counts.get("TOTAL", 0))
    k2.metric("Approved", counts.get("APPROVED", 0))
    k3.metric("Pending", counts.get("PENDING", 0))
    k4.metric("Rejected", counts.get("REJECTED", 0))

    subs = db.list_submissions()
    f1, f2 = st.columns([2, 1])
    query = f1.text_input("Search", placeholder="Name, PAN, GSTIN, email or rule ID").strip().lower()
    statuses = f2.multiselect("Status", ["APPROVED", "PENDING", "REJECTED", "PROCESSING"])

    def findings(sub):
        return [r["rule_id"] for r in (sub.get("rules") or []) if r["disposition"] != "NONE"]

    def matches(sub):
        if statuses and sub["status"] not in statuses:
            return False
        haystack = " ".join([sub["legal_name"], sub["trade_name"] or "", sub["pan"], sub["gstin"],
                             sub["contact_email"], *findings(sub)]).lower()
        return query in haystack

    shown = [s for s in subs if matches(s)]
    st.dataframe([{"ID": s["id"], "Submitted": s["created_at"].replace("T", " ")[:19],
                   "Legal name": s["legal_name"], "PAN": s["pan"], "GSTIN": s["gstin"],
                   "Status": s["status"], "Findings": ", ".join(dict.fromkeys(findings(s))) or "—",
                   "Run time (s)": round((s["duration_ms"] or 0) / 1000, 1)} for s in shown],
                 hide_index=True)
    st.caption(f"{len(shown)} of {len(subs)} submissions shown")

    if shown:
        st.markdown("#### Submission detail")
        sid = st.selectbox("Inspect submission", [s["id"] for s in shown],
                           format_func=lambda i: next(f"#{s['id']} · {s['legal_name']} · {s['status']}"
                                                      for s in shown if s["id"] == i))
        render_submission(db.get_submission(sid), db.get_logs(sid))

    if PUBLIC_DEMO:
        with st.expander("Demo controls"):
            st.markdown("Approved submissions are added to `approved_vendors`, so submitting "
                        "Vendor 01 or 02 again is correctly **REJECTED** as `DUPLICATE_VENDOR`. "
                        "To re-run those scenarios, remove the approvals made by earlier demo runs. "
                        "Submission history and the seeded reference records are kept.")
            if st.button("Reset demo approvals"):
                removed = db.reset_demo_approvals()
                st.success(f"Removed {removed} demo approval(s) from approved_vendors.")

    st.markdown("#### Reference data (read-only)")
    r1, r2 = st.columns(2)
    with r1:
        st.markdown("**Approved vendors**")
        st.dataframe([{"Legal name": v["legal_name"], "PAN": v["pan"], "GSTIN": v["gstin"],
                       "Account": mask_account(v["bank_account_number"]), "IFSC": v["ifsc"],
                       "Source": v["source"]} for v in db.list_approved_vendors()], hide_index=True)
    with r2:
        st.markdown("**Blocklist**")
        st.dataframe([{"Legal name": b["legal_name"], "PAN": b["pan"], "Reason": b["reason"]}
                      for b in db.list_blocklist()], hide_index=True)
    st.caption("Storage: " + ("PostgreSQL (hosted, persistent)" if db.BACKEND == "postgres"
                              else "SQLite file on this machine"))


# ==========================================================================
# Layout
# ==========================================================================

st.title("Vendor Onboarding Review")
st.caption(f"Form + documents → content-based extraction (Gemini · `{extractor.GEMINI_MODEL}`) → "
           "deterministic Python rules → Approved / Pending / Rejected with evidence and a drafted "
           "vendor email. Simulated policy; no government or bank verification.")
for warning in STORAGE_WARNINGS:
    st.warning(warning)
tab_run, tab_ops = st.tabs(["New Submission & Live Run", "Operations Dashboard"])
with tab_run:
    new_submission_tab()
with tab_ops:
    dashboard_tab()
