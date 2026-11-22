"""Build reviewer_test_pack.zip: the eight test vendors plus a testing guide.

A separate helper (like generate_test_data.py); the app never imports or runs it.
It reads ./sample_data/ and writes ./reviewer_test_pack.zip, containing:

    vendor_onboarding_test_pack/
        TESTING_GUIDE.md          copy-ready form values + expected outcome per vendor
        vendor_01_.../            form_input.json + the three PDFs to upload
        ...

The zip is byte-for-byte reproducible (fixed timestamps), so rebuilding it after
an unchanged dataset produces no Git diff.

    python build_test_pack.py [--app-url https://your-app.streamlit.app]
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_data"
OUT = HERE / "reviewer_test_pack.zip"
ROOT = "vendor_onboarding_test_pack"

FIELD_LABELS = [("legal_name", "Legal name"), ("trade_name", "Trade name (optional)"),
                ("contact_email", "Contact email"), ("registered_address", "Registered address"),
                ("pan", "PAN"), ("gstin", "GSTIN"), ("bank_account_number", "Bank account number"),
                ("ifsc", "IFSC")]
SLOTS = [("gst_certificate", "GST registration certificate"), ("pan_document", "PAN document"),
         ("bank_proof", "Bank account confirmation")]


def guide(scenarios: list[dict], app_url: str | None) -> str:
    lines = [
        "# Vendor Onboarding demo: testing guide",
        "",
        f"**App:** {app_url}" if app_url else "**App:** use the link you were given.",
        "",
        "Every company, identifier and document in this pack is **synthetic**. Each PDF carries a",
        "\"SYNTHETIC TEST DOCUMENT\" watermark.",
        "",
        "## How to run one scenario",
        "",
        "1. Open the app, tab **New Submission & Live Run**.",
        "2. Copy the vendor's eight values below into the form.",
        "3. Upload the three files listed for that vendor into the matching slots.",
        "4. Click **Submit for review** and watch the seven stages run (each document is read by",
        "   Gemini, so a run can take up to a minute).",
        "5. Compare the decision with **Expected** below, then check the rule evidence, extracted",
        "   JSON and drafted email.",
        "6. Click **Start a new submission** before the next vendor.",
        "",
        "The **Operations Dashboard** tab shows the history, stage logs and the reference tables",
        "(one pre-approved vendor and one blocklisted entity, used by Vendors 07 and 08).",
        "",
        "**Re-running Vendor 01 or 02?** Approved vendors are remembered, so a second approval of",
        "the same company is correctly **REJECTED** as `DUPLICATE_VENDOR`. To approve it again,",
        "open Operations Dashboard → **Demo controls** → **Reset demo approvals**.",
        "",
        "## Scenarios at a glance",
        "",
        "| # | Vendor | What it tests | Expected | Failing rule |",
        "|---|---|---|---|---|",
    ]
    for s in scenarios:
        rules = ", ".join(f"`{r}`" for r in s["expected_validation_failures"]) or "—"
        lines.append(f"| {s['vendor_id']} | {s['vendor_name']} | {s['scenario_description']} | "
                     f"**{s['expected_outcome']}** | {rules} |")
    lines += ["", "## Form values and uploads", ""]
    for s in scenarios:
        form = json.loads((SAMPLE / s["folder"] / "form_input.json").read_text(encoding="utf-8"))
        lines += [f"### Vendor {s['vendor_id']}: {s['vendor_name']}", "",
                  f"Folder: `{s['folder']}/`", "", "| Field | Value |", "|---|---|"]
        lines += [f"| {label} | {form.get(key) or '(leave blank)'} |" for key, label in FIELD_LABELS]
        lines += ["", "| Upload slot | File |", "|---|---|"]
        for slot, label in SLOTS:
            name = s["documents"][slot]
            note = "  ⚠ not a GST certificate, on purpose" if slot == "gst_certificate" and \
                name != "gst_certificate.pdf" else ""
            lines.append(f"| {label} | `{name}`{note} |")
        rules = ", ".join(f"`{r}`" for r in s["expected_validation_failures"])
        lines += ["", f"**Expected:** **{s['expected_outcome']}**" + (f" · {rules}" if rules else ""),
                  ""]
    return "\n".join(lines)


def add(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build reviewer_test_pack.zip from sample_data/.")
    parser.add_argument("--app-url", help="Public app URL to print at the top of the guide.")
    args = parser.parse_args(argv)

    scenarios_file = SAMPLE / "test_scenarios.json"
    if not scenarios_file.is_file():
        print("sample_data/test_scenarios.json not found. Run: python generate_test_data.py",
              file=sys.stderr)
        return 1
    scenarios = json.loads(scenarios_file.read_text(encoding="utf-8"))

    with zipfile.ZipFile(OUT, "w") as zf:
        add(zf, f"{ROOT}/TESTING_GUIDE.md", guide(scenarios, args.app_url).encode("utf-8"))
        count = 1
        for s in scenarios:
            folder = SAMPLE / s["folder"]
            for name in ["form_input.json", *sorted(set(s["documents"].values()))]:
                add(zf, f"{ROOT}/{s['folder']}/{name}", (folder / name).read_bytes())
                count += 1
    print(f"Wrote {OUT.name}: {count} files, {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
