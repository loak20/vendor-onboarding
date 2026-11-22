# Prompt 2 — Generate the Synthetic Vendor Test Documents (Separate, One-Time Script)

Write a standalone, executable **`generate_test_data.py`** that creates exactly eight realistic **fictional** Indian B2B vendor onboarding submissions in `./sample_data/` using **ReportLab, Pillow and PyMuPDF**.

This script is **independent from the Streamlit application in Prompt 1**. The user runs it **once, separately**, then manually copies the generated form values into the web app and uploads the generated documents to test the real pipeline. **Do not implement or invoke the application, decision engine, Gemini API, or SQLite database here.** Do not add dataset generation to app startup or app runtime dependencies.

## 1. Exact folder and input contracts

Generate:

```text
sample_data/
├── test_scenarios.json
├── seed_db_records.json
├── vendor_01_kovai_precision/
│   ├── form_input.json
│   ├── gst_certificate.pdf
│   ├── pan_card.pdf
│   └── bank_proof.pdf
├── vendor_02_deccan_agro/          (same four filenames)
├── vendor_03_apex_cloud/           (same four filenames)
├── vendor_04_vanguard_logistics/   (same four filenames)
├── vendor_05_narmada_polymers/     (same four filenames)
├── vendor_06_zenith_packaging/
│   ├── form_input.json
│   ├── Electricity_Utility_Bill.pdf
│   ├── pan_card.pdf
│   └── bank_proof.pdf
├── vendor_07_meridian_tech/        (same four filenames as Vendor 1)
└── vendor_08_orion_global/         (same four filenames as Vendor 1)
```

Every `form_input.json` must contain **exactly these eight string keys**, matching Prompt 1:

```json
{
  "legal_name": "Kovai Precision Components Pvt Ltd",
  "trade_name": "Kovai Precision",
  "contact_email": "accounts@kovai-precision.example",
  "registered_address": "Coimbatore, Tamil Nadu, India",
  "pan": "ABCDE1234F",
  "gstin": "<complete computed 15-character GSTIN>",
  "bank_account_number": "12345678901234",
  "ifsc": "HDFC0001234"
}
```

The example values are illustrative; output concrete strings, no placeholders. Use fictional plausible addresses and reserved `.example` email domains (not real mailboxes). No `expected_outcome`, scenario ID, or expected-rule fields in the form JSON. All identifiers must stay **strings**.

For ordinary scenarios, generate PANs matching `^[A-Z]{5}[0-9]{4}[A-Z]$`, IFSC strings matching `^[A-Z]{4}0[A-Z0-9]{6}$`, and GSTINs with valid Indian state prefix, PAN embedded at zero-based `[2:12]`, entity character, `Z` at index 13, and the **real modulo-36 GSTIN checksum** at index 14. Match state codes to addresses (e.g., Tamil Nadu `33`, Telangana `36`, Maharashtra `27`, Karnataka `29`, Gujarat `24`, Delhi `07`). The values are syntactically realistic but **not government/bank verified**. Do not accidentally create inconsistencies other than the ones specified below.

## 2. Readable, well-designed synthetic PDFs

Use ReportLab for polished, aligned, legible documents with typography, borders, clear labels, and a visible diagonal **`SYNTHETIC TEST DOCUMENT — FOR DEMO ONLY`** watermark. Never imply actual official issuance; avoid real agency crests, bank logos, live signatures and counterfeit-style security features. Documents should nevertheless clearly resemble the *information structure* of the intended type so they test AI extraction.

For each applicable vendor create:

- **`gst_certificate.pdf`**: GST REG-06-inspired **synthetic registration certificate** with GSTIN, legal name, trade name, principal business address, state, liability/registration date and synthetic reference number.
- **`pan_card.pdf`**: card-styled **synthetic PAN information record** with PAN, legal name, entity type and incorporation date.
- **`bank_proof.pdf`**: formal **synthetic bank confirmation letter** with account-holder name, account number, IFSC, bank/branch name, branch address, letter date, reference number, and a stamp labeled `DEMO — NOT BANK VERIFIED`.
- **Vendor 6 only:** `Electricity_Utility_Bill.pdf` with electricity-bill heading, consumer name, service address, billing dates, consumer number and payable amount; **no GST certificate or registration information**. This file will be manually uploaded into the application's **`gst_certificate`** slot. Do not create `gst_certificate.pdf` for Vendor 6.

Ordinary PDFs must contain extractable text. Vendor 2 is the only scanned/image-only case.

## 3. Precisely eight scenarios

| ID | Vendor | Document/data setup | Expected status | Expected failing rule |
|---|---|---|---|---|
| 01 | Kovai Precision Components Pvt Ltd | Fully consistent form, digital GST/PAN/bank PDFs, corporate `.example` email | `APPROVED` | none |
| 02 | Deccan Agro Exports Ltd | Fully consistent form; **all three PDFs scanned, image-only** | `APPROVED` | none |
| 03 | Apex Cloud Solutions Pvt Ltd | Consistent PAN/GST/form, but bank proof holder is **`Rohan Sharma`** instead of the corporate entity; account/IFSC still match form | `REJECTED` | `BANK_BENEFICIARY_MISMATCH` |
| 04 | Vanguard Logistics India Pvt Ltd | Form legal name = `Vanguard Logistics India Pvt Ltd`, trade name = `Vanguard Logistics`; GST/PAN legal name and bank holder = `Vanguard Supply Chain Pvt Ltd`; GST trade name = `Vanguard Logistics` | `PENDING` | `LEGAL_NAME_MISMATCH` |
| 05 | Narmada Polymers LLP | Form/PAN-document PAN = **`AABFN4321K`**; GSTIN in form/GST certificate embeds **`AABFN9999K`** with **valid GSTIN checksum**; Gujarat state code `24` | `REJECTED` | `PAN_GSTIN_MISMATCH` |
| 06 | Zenith Packaging Industries | Form/PAN/bank consistent; utility bill instead of GST certificate, uploaded in GST slot | `PENDING` | `WRONG_DOCUMENT_TYPE` |
| 07 | Meridian Tech Services Pvt Ltd | Fully consistent own form/PDFs; PAN, GSTIN, account and IFSC equal **pre-seeded** approved vendor `Meridian Technology Services Pvt Ltd` | `REJECTED` | `DUPLICATE_VENDOR` |
| 08 | Orion Global Traders | Fully consistent own form/PDFs; exact PAN matches seeded internal blocklist | `REJECTED` | `BLOCKLIST_MATCH` |

Additional precise constraints:

- Vendor 2: first create normal PDFs, then rasterize **each page** with PyMuPDF; use Pillow to add mild grayscale noise and ~1–2° rotation without clipping text; embed resulting images in new PDFs **without any selectable/hidden text layer**. Ensure legibility sufficient for Gemini Vision.
- Vendor 3: do not change bank account number/IFSC, only beneficiary name; unrelated individual beneficiary is a deliberate simulated corporate-payee policy violation.
- Vendor 4: all identifiers, numbers, addresses, and trade name must otherwise reconcile. Bank holder matches **GST/PAN legal name**, not the form name. This is **Pending for DBA/name clarification**, not automatically a fraudulent-account finding.
- Vendor 5: form and GST certificate show **the same GSTIN**; PAN document and form show **the same PAN**. Only the cross-identity relationship is wrong. Compute the GSTIN check character correctly, so there is **no accidental `GSTIN_INVALID_CHECKSUM`**.
- Vendor 6: exactly three PDFs, one of which is the bill. Do not add a missing GST certificate elsewhere.
- Vendor 7: the approved seed record and submitted vendor must share the exact PAN/GSTIN/account/IFSC; submitted form and PDFs consistently use the submitted name. Duplicate detection comes from the database, not the generator.
- Vendor 8: the blocklist seed contains exact submitted PAN; reason must be **`Internal onboarding policy restriction — synthetic compliance test`**.
- No free email domains, accidental extra identifier mismatches, or identifier collisions between unrelated vendors. Keep dates deterministic and addresses/state codes coherent.

## 4. Test-only metadata (never uploaded to app)

Write `sample_data/test_scenarios.json` as an **array of eight** objects, each with exactly these fields:

```json
{
  "vendor_id": "06",
  "vendor_name": "Zenith Packaging Industries",
  "folder": "vendor_06_zenith_packaging",
  "scenario_description": "Electricity bill in GST certificate slot",
  "expected_outcome": "PENDING",
  "expected_validation_failures": ["WRONG_DOCUMENT_TYPE"],
  "documents": {
    "gst_certificate": "Electricity_Utility_Bill.pdf",
    "pan_document": "pan_card.pdf",
    "bank_proof": "bank_proof.pdf"
  }
}
```

Other scenarios map `gst_certificate` to `gst_certificate.pdf`, `pan_document` to `pan_card.pdf`, `bank_proof` to `bank_proof.pdf`. Vendor IDs are strings `01`–`08`. Expected failures use **exactly** Prompt 1's rule IDs; Vendor 1 and 2 have empty failure lists. This JSON is **for the person testing the app**; the runtime application must never read it.

## 5. One-time database fixture JSON

Write `sample_data/seed_db_records.json` using **exactly** this schema, with actual values populated:

```json
{
  "approved_vendors": [
    {
      "legal_name": "Meridian Technology Services Pvt Ltd",
      "pan": "<same PAN as Vendor 7>",
      "gstin": "<same GSTIN as Vendor 7>",
      "bank_account_number": "<same bank account as Vendor 7>",
      "ifsc": "<same IFSC as Vendor 7>"
    }
  ],
  "blocklist": [
    {
      "legal_name": "Orion Global Traders",
      "pan": "<same PAN as Vendor 8>",
      "reason": "Internal onboarding policy restriction — synthetic compliance test"
    }
  ]
}
```

Replace angle-bracket placeholders with literal identifiers. This file will be loaded **manually, once before testing**, using Prompt 1's external CLI command:

```bash
python database.py --seed ./sample_data/seed_db_records.json
```

The generator must **not** create, update or open SQLite; the Streamlit application must **not** automatically find or load the fixture file. This is the sole connection between generated fixtures and the app's pre-existing-record lookups.

## 6. Determinism and integrity checks

Use fixed random seed and fixed synthetic dates. Rerunning the script should reproducibly overwrite **only its own generated files**, without deleting unrelated files. After generation, **assert**:

1. Exactly the eight requested scenario folders exist and contain `form_input.json` plus the appropriate three PDFs.
2. All PDFs open, digital PDFs contain extractable text, and Vendor 2's image-only PDFs contain **no extractable text**.
3. Normal GSTINs have valid state, structure, embedded PAN and checksum; Vendor 5's GSTIN still has a correct checksum but deliberately embeds a different PAN.
4. Each scenario has only its specified data deviation, and metadata correctly identifies the upload slot/files.
5. Vendor 7 seeded identities match; Vendor 8 seed PAN matches.
6. No output `form_input.json` or `seed_db_records.json` includes `expected_outcome` or test rule labels.

Print a short generation table with vendor, digital/scanned document type, expected status, and relevant failure ID. Fail loudly with an actionable error when a check fails.

## 7. Generator deliverables and usage

Provide the **complete `generate_test_data.py`** (no placeholders) and describe running it with:

```bash
pip install reportlab Pillow pymupdf
python generate_test_data.py
```

The generator's only runtime effect should be creating `./sample_data/` and its files. Do not alter the app's `requirements.txt` or app code. Prompt 1's `README.md` must repeat these generation commands and explain **manual form entry + PDF uploads** and **one-time external fixture seeding**.

**Primary success criterion:** Eight independent, realistic upload scenarios that the web app processes through its genuine extraction/validation pipeline—never by checking dataset names or expected outcomes.
