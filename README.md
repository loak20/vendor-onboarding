# Vendor Onboarding Review — PS-2 demo

A working local demo of Indian B2B vendor onboarding. A vendor submits eight form
fields and three documents. The app reads the documents and classifies them by
**content**, extracts the identifiers, reconciles everything with deterministic Python
rules, and returns **APPROVED / PENDING / REJECTED** with the evidence behind it. It also
drafts the vendor email and keeps a full audit trail.

Stack: Python · Streamlit · SQLite locally or PostgreSQL when hosted · PyMuPDF · Gemini
(`google-genai`).

**Hosting it on a public URL?** See [DEPLOYMENT.md](DEPLOYMENT.md): Streamlit Community Cloud plus a
free Neon PostgreSQL database, step by step.

---

## 1. Project structure

```text
vendor_onboarding/
├── app.py               Streamlit UI: submission form + live run, operations dashboard
├── extractor.py         File checks, PDF text layer, Vision fallback, Gemini classification/extraction
├── validator.py         Normalisation + every validation rule (pure Python, no LLM)
├── decision_engine.py   Rule → decision policy, deterministic vendor email drafting
├── database.py          Storage (SQLite file, or PostgreSQL when DATABASE_URL is set), queries, `--seed` CLI
├── requirements.txt     App runtime dependencies only
├── .env.example         Settings template (GEMINI_API_KEY, GEMINI_MODEL, optional hosting settings)
├── .streamlit/
│   ├── config.toml          Upload cap, hidden tracebacks for visitors
│   └── secrets.toml.example Template for Streamlit Community Cloud → Secrets
├── generate_test_data.py   SEPARATE one-time test-data generator (not imported by the app)
├── build_test_pack.py   SEPARATE helper: zips sample_data + a testing guide for reviewers
├── reviewer_test_pack.zip  Output of build_test_pack.py (linked from the hosted app)
├── DEPLOYMENT.md        Step-by-step public deployment (Streamlit Community Cloud + Neon)
├── HANDOFF.md           Setup + demo guide for someone running this on a new machine
└── WHAT_WAS_BUILT.md    Summary of the work, decisions and testing
```

### Pipeline

```text
 Vendor form (8 fields) + 3 uploads ── user clicks "Submit for review"
   │
 ① Receive ............ submission row + execution log created in SQLite
 ② Read documents ..... type / size (10 MB) / page (5) checks
   │                     PDF with a usable text layer ──► PDF_TEXT
   │                     scanned PDF or PNG/JPG ────────► render pages ──► VISION
 ③ Classify & extract . Gemini returns schema-checked JSON (1 retry on bad output)
   │                     type decided by content: GST_CERTIFICATE / PAN_DOCUMENT /
   │                     BANK_PROOF / UTILITY_BILL / UNKNOWN
   │                     digital PDFs: any value not literally in the text is dropped
 ④ Validate ........... deterministic rules + lookups in approved_vendors / blocklist
 ⑤ Decide ............. any REJECT → REJECTED · else any REVIEW → PENDING · else APPROVED
 ⑥ Draft email ........ deterministic template for PENDING / REJECTED (never sent)
 ⑦ Persist & display .. Tab 1 shows the run live; Tab 2 shows history and audit detail
```

Gemini reads documents; it never decides. The form values are never sent to the model,
so the documents are the only evidence it sees.

---

## 2. App setup

Requires Python 3.10+ (tested with 3.12).

```bash
cd vendor_onboarding
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then put your key in .env
#   GEMINI_API_KEY=...               (https://aistudio.google.com/apikey)
#   GEMINI_MODEL=gemini-3.8-flash    (optional; any current Gemini model with image + JSON output)
streamlit run app.py
```

On first start the app creates **empty** tables in `vendor_onboarding.db`. It never
loads sample data on its own (the one opt-in exception, `SEED_FILE`, is described in
section 4). Without `GEMINI_API_KEY` the app still runs, but every
document fails extraction, so every submission goes to **PENDING**
(`EXTRACTION_UNAVAILABLE`). A banner in the UI says so.

---

## 3. Separate, one-time test-data creation

`generate_test_data.py` is a standalone script. It is **not** part of the app, and the
app never imports it, runs it or reads its output folder. Run it once, beside the app
or in any working directory:

```bash
pip install reportlab Pillow pymupdf
python generate_test_data.py
```

It writes `./sample_data/` with eight fictional vendors, each with a
`form_input.json` and three PDFs. Vendor 2's PDFs are scanned, image-only copies. It
also writes `test_scenarios.json` (expected results, for the human tester only) and
`seed_db_records.json` (two reference records). It checks its own output and fails
loudly if anything is inconsistent. Re-running it overwrites only its own files.

## 4. One-time fixture seeding

Vendors 7 and 8 test lookups against **pre-existing reference data**: one existing
approved vendor and one blocklisted entity. Load them once, after generating the data
and before testing those vendors:

```bash
python database.py --seed ./sample_data/seed_db_records.json
```

This inserts directly into the independent `approved_vendors` and `blocklist` tables.
It is idempotent: re-running prints `already present` and adds no duplicates. It is
not upload processing.

**No shell, e.g. on a hosted app?** Set `SEED_FILE=sample_data/seed_db_records.json` (in
`.env` or the host's secrets). The app then runs the same idempotent routine once at
startup. This is the only file it opens in `sample_data/`, and only when you configure
it. Without `SEED_FILE` the running app never opens it.

**Optional PostgreSQL.** If `DATABASE_URL` is set, every table lives in that PostgreSQL
database instead of the SQLite file. The seed command and the app both use it, with
identical behaviour.

## 5. Manual end-to-end test

1. `streamlit run app.py` and open **New Submission & Live Run**.
2. Open `sample_data/vendor_XX_*/form_input.json` and type its eight values into the form.
3. Upload that vendor's PDFs: `gst_certificate.pdf` → GST slot, `pan_card.pdf` →
   PAN slot, `bank_proof.pdf` → bank slot.
   **Vendor 6:** upload `Electricity_Utility_Bill.pdf` into the **GST** slot.
4. Click **Submit for review**. Watch stages ①–⑦ run, then check the decision, each
   rule and its evidence, the extracted JSON and the drafted email.
5. Click **Start a new submission** to clear the form for the next vendor.
6. Use **Operations Dashboard** for KPIs, searchable history, per-run evidence, stage
   logs, errors and the read-only reference tables.

## 6. Expected results (manual comparison only)

The app never reads these values; they come from `test_scenarios.json` for the tester.

| ID | Vendor | Setup | Expected | Failing rule |
|---|---|---|---|---|
| 01 | Kovai Precision Components Pvt Ltd | Clean digital PDFs | **APPROVED** | — |
| 02 | Deccan Agro Exports Ltd | Clean, but all three PDFs are scanned images (Vision path) | **APPROVED** | — |
| 03 | Apex Cloud Solutions Pvt Ltd | Bank holder is an individual, "Rohan Sharma" | **REJECTED** | `BANK_BENEFICIARY_MISMATCH` |
| 04 | Vanguard Logistics India Pvt Ltd | GST/PAN/bank legal entity is "Vanguard Supply Chain Pvt Ltd"; trade name matches | **PENDING** | `LEGAL_NAME_MISMATCH` |
| 05 | Narmada Polymers LLP | GSTIN (valid checksum) embeds AABFN9999K; PAN is AABFN4321K | **REJECTED** | `PAN_GSTIN_MISMATCH` |
| 06 | Zenith Packaging Industries | Electricity bill in the GST slot | **PENDING** | `WRONG_DOCUMENT_TYPE` |
| 07 | Meridian Tech Services Pvt Ltd | PAN/GSTIN/account equal seeded approved vendor | **REJECTED** | `DUPLICATE_VENDOR` |
| 08 | Orion Global Traders | PAN on the seeded blocklist | **REJECTED** | `BLOCKLIST_MATCH` |

Outcomes assume extraction succeeds, i.e. a valid Gemini key and model.

**Approvals add the vendor to `approved_vendors`.** So submitting Vendor 1 or 2 a second
time is correctly **REJECTED** as `DUPLICATE_VENDOR`. Reset between demo runs (below).

## 7. Resetting the local database between demo runs

```bash
# stop the app first (Ctrl+C)
rm vendor_onboarding.db                                       # ⚠ deletes ALL submission history
python database.py --seed ./sample_data/seed_db_records.json  # re-load the two reference records
streamlit run app.py                                          # tables are re-created empty
```

You don't need to regenerate the dataset. Deleting the DB permanently discards every
stored submission, extraction, decision, email and log.

On the hosted demo (PostgreSQL), use **Operations Dashboard → Demo controls → Reset demo
approvals** to re-run Vendors 01/02 while keeping history. For a full wipe, see
DEPLOYMENT.md, Step 7.

## 8. Rules, policy assumptions and limitations

Each check returns `rule_id`, `status` (PASS/WARN/FAIL), `details`, `evidence` and
`disposition` (NONE/REVIEW/REJECT). FAIL does not always mean REJECT. Most findings ask
for review; only these reject: `PAN_GSTIN_MISMATCH`, `DUPLICATE_VENDOR`,
`BLOCKLIST_MATCH`, and `BANK_BENEFICIARY_MISMATCH` for a clearly unrelated individual.

Policy assumptions (simulated, documented on purpose):

- **Corporate-payee policy (Vendor 3).** A business vendor is identified by the 4th PAN
  character not being `P`. Its bank account must be held in the entity's own name. A
  holder that looks like an individual and shares no name tokens with the vendor is
  rejected. Any other holder discrepancy is ambiguous and goes to review.
- **Legal vs trade name (Vendor 4).** Names are compared after normalising case,
  punctuation and suffixes (`Pvt Ltd` = `Private Limited`). Fuzzy similarity is never
  treated as identity. Legal and trade names are reconciled separately. A bank holder
  that matches the **documented** GST/PAN entity but not the submitted name is a
  warning, and the submission is PENDING for clarification, not a fraud finding.
- **PAN ↔ GSTIN (Vendor 5).** A GSTIN embeds its holder's PAN at characters 3–12. A
  mismatch with the form or PAN document is a hard reject. Only well-formed PANs are
  compared, so a malformed PAN typo goes to review instead.
- **Duplicates (Vendor 7)** are checked only against `approved_vendors`, never against
  past pending or rejected submissions. Exact PAN/GSTIN → reject. Bank account plus
  IFSC plus legal name → reject. A bank account alone shared with a different approved
  vendor → review, not reject.
- **Blocklist (Vendor 8)** is an exact PAN match, including the PAN embedded in the
  GSTIN. The vendor email never discloses the internal list.
- **Missing evidence never approves.** Missing, unreadable or unextractable documents
  and identifiers absent from a document all route to review. On digital PDFs, any
  value the model returns that is not literally present in the text layer is dropped.
  This guards against hallucinated identifiers.
- Bank account numbers are masked in normal UI display and in emails. Full values are
  kept in SQLite as the system of record.

Limitations:

- Compliance checks and approvals are **simulated**. There is no government (GST/PAN) or
  bank verification. PAN, GSTIN and IFSC checks confirm syntax and checksum only.
- **No email is sent.** Drafts are stored and displayed only.
- No authentication and no live GST or bank APIs. Storage is a local SQLite file, or
  PostgreSQL for the hosted demo. Anyone with a hosted demo's link can submit, so the
  operator's Gemini quota is shared.
- Vision extraction quality depends on the Gemini model and scan quality. Scanned
  documents cannot get the text-layer grounding check.
