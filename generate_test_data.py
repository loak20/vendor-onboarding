#!/usr/bin/env python3
"""Generate eight synthetic Indian B2B vendor-onboarding test submissions.

Standalone, one-time script. It is NOT part of the Streamlit app: it does not
import app modules, call Gemini or touch SQLite. It writes ./sample_data/ and
nothing else:

    pip install reportlab Pillow pymupdf
    python generate_test_data.py

You then type each vendor's form_input.json values into the app by hand and
upload its three PDFs. Seed the reference tables once with:

    python database.py --seed ./sample_data/seed_db_records.json

All names, identifiers, banks and addresses are fictional. Identifiers are
syntactically realistic (valid GSTIN checksums etc.) but not government or
bank verified.
"""
from __future__ import annotations

import io
import json
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pymupdf
    from PIL import Image, ImageChops, ImageFilter
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Missing dependency ({exc.name}). Run: pip install reportlab Pillow pymupdf")

OUT_DIR = Path("sample_data")
SEED = 20261118
WATERMARK = "SYNTHETIC TEST DOCUMENT — FOR DEMO ONLY"
DISCLAIMER = ("Synthetic test document generated for software testing. Not issued by any "
              "government authority, bank or utility. All names and numbers are fictional.")
BLOCKLIST_REASON = "Internal onboarding policy restriction — synthetic compliance test"
FORM_KEYS = ["legal_name", "trade_name", "contact_email", "registered_address",
             "pan", "gstin", "bank_account_number", "ifsc"]
RULE_IDS = ["MISSING_REQUIRED_DOCUMENT", "WRONG_DOCUMENT_TYPE", "DOCUMENT_UNREADABLE",
            "EXTRACTION_UNAVAILABLE", "PAN_INVALID_FORMAT", "GSTIN_INVALID_FORMAT",
            "GSTIN_INVALID_CHECKSUM", "PAN_GSTIN_MISMATCH", "IFSC_INVALID_FORMAT",
            "IDENTIFIER_MISMATCH", "LEGAL_NAME_MISMATCH", "BANK_BENEFICIARY_MISMATCH",
            "DUPLICATE_VENDOR", "BLOCKLIST_MATCH", "FREE_EMAIL_DOMAIN"]
FREE_DOMAINS = {"gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com",
                "live.com", "rediffmail.com", "icloud.com", "aol.com", "protonmail.com"}

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STATES = {"07": "Delhi", "24": "Gujarat", "27": "Maharashtra", "29": "Karnataka",
          "32": "Kerala", "33": "Tamil Nadu", "36": "Telangana"}
BANKS = {"CDBL": "Coastal Demo Bank Ltd", "IVDB": "Indus Valley Demo Bank",
         "SFDB": "Saffron Demo Bank"}


class GenerationError(Exception):
    pass


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = CHARSET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return CHARSET[(36 - total % 36) % 36]


def make_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    first14 = f"{state_code}{pan}{entity}Z"
    return first14 + gstin_check_char(first14)


# ==========================================================================
# Scenario data
# ==========================================================================

@dataclass
class Vendor:
    vendor_id: str
    folder: str
    form: dict
    state_code: str
    constitution: str
    pan_entity_type: str
    incorporated: str
    liability_date: str
    bank_branch: str
    bank_branch_address: str
    description: str
    expected_outcome: str
    expected_failures: list
    deviations: set = field(default_factory=set)
    scanned: bool = False
    gst: dict | None = None
    pan_doc: dict = field(default_factory=dict)
    bank: dict = field(default_factory=dict)
    utility: dict | None = None

    @property
    def documents(self) -> dict:
        return {"gst_certificate": "Electricity_Utility_Bill.pdf" if self.utility else "gst_certificate.pdf",
                "pan_document": "pan_card.pdf", "bank_proof": "bank_proof.pdf"}


def _vendor(vid, folder, legal, trade, email, address, state, pan, acct, ifsc, constitution,
            entity_type, incorporated, liability, branch, branch_address, description,
            outcome, failures, gstin_pan=None, scanned=False) -> Vendor:
    gstin = make_gstin(state, gstin_pan or pan)
    form = {"legal_name": legal, "trade_name": trade, "contact_email": email,
            "registered_address": address, "pan": pan, "gstin": gstin,
            "bank_account_number": acct, "ifsc": ifsc}
    v = Vendor(vid, folder, form, state, constitution, entity_type, incorporated, liability,
               branch, branch_address, description, outcome, failures, scanned=scanned)
    n = int(vid)
    v.gst = {"gstin": gstin, "legal_name": legal, "trade_name": trade, "address": address,
             "state": f"{STATES[state]} (State Code {state})", "constitution": constitution,
             "liability_date": liability, "issue_date": liability,
             "reference": f"SYN-REG06-{state}-{2017 + n % 3}-{40100 + n * 37:06d}"}
    v.pan_doc = {"pan": pan, "legal_name": legal, "entity_type": entity_type,
                 "incorporated": incorporated, "reference": f"SYN-PAN-{n:02d}-{77300 + n * 53}"}
    v.bank = {"holder": legal, "account": acct, "account_type": "Current Account", "ifsc": ifsc,
              "bank_name": BANKS[ifsc[:4]], "branch": branch, "branch_address": branch_address,
              "letter_date": f"{3 + n:02d}/11/2026",
              "reference": f"{ifsc[:4]}/{branch[:3].upper()}/2026/{400 + n * 11:04d}"}
    return v


def build_vendors() -> list[Vendor]:
    v1 = _vendor("01", "vendor_01_kovai_precision", "Kovai Precision Components Pvt Ltd",
                 "Kovai Precision", "accounts@kovai-precision.example",
                 "Plot 14, SIDCO Industrial Estate, Kurichi, Coimbatore, Tamil Nadu 641021, India",
                 "33", "AAKCK4821M", "50200048213377", "CDBL0001042", "Private Limited Company",
                 "Company", "14/06/2016", "01/07/2017", "Coimbatore Main",
                 "12 Avinashi Road, Coimbatore, Tamil Nadu 641018",
                 "Happy path: consistent form and digital GST, PAN and bank PDFs",
                 "APPROVED", [])
    v2 = _vendor("02", "vendor_02_deccan_agro", "Deccan Agro Exports Ltd", "Deccan Agro",
                 "finance@deccan-agro.example",
                 "Survey No. 112, Shamshabad Road, Rajendranagar, Hyderabad, Telangana 500030, India",
                 "36", "AADCD5532Q", "918020061734552", "IVDB0003117", "Public Limited Company",
                 "Company", "02/09/2011", "01/07/2017", "Hyderabad Begumpet",
                 "8-2-614 Begumpet Main Road, Hyderabad, Telangana 500016",
                 "Consistent submission; all three documents are scanned image-only PDFs",
                 "APPROVED", [], scanned=True)
    v3 = _vendor("03", "vendor_03_apex_cloud", "Apex Cloud Solutions Pvt Ltd", "Apex Cloud",
                 "ap@apex-cloud.example",
                 "4th Floor, 27 Outer Ring Road, Bellandur, Bengaluru, Karnataka 560103, India",
                 "29", "AAFCA7314R", "39871204556", "SFDB0000729", "Private Limited Company",
                 "Company", "21/01/2019", "15/03/2019", "Bengaluru Koramangala",
                 "80 Feet Road, Koramangala 4th Block, Bengaluru, Karnataka 560034",
                 "Bank proof account holder is an unrelated individual (Rohan Sharma)",
                 "REJECTED", ["BANK_BENEFICIARY_MISMATCH"])
    v3.bank.update(holder="Rohan Sharma", account_type="Savings Account")
    v3.deviations = {"bank.holder"}

    v4 = _vendor("04", "vendor_04_vanguard_logistics", "Vanguard Logistics India Pvt Ltd",
                 "Vanguard Logistics", "accounts@vanguard-logistics.example",
                 "Unit 7, Bhiwandi Logistics Park, Thane, Maharashtra 421302, India",
                 "27", "AAHCV6620L", "60331987204418", "CDBL0004410", "Private Limited Company",
                 "Company", "09/08/2014", "01/07/2017", "Thane West",
                 "Gokhale Road, Naupada, Thane West, Maharashtra 400602",
                 "Form legal name differs from GST/PAN legal entity; bank holder and GST trade "
                 "name match the documented entity",
                 "PENDING", ["LEGAL_NAME_MISMATCH"])
    documented = "Vanguard Supply Chain Pvt Ltd"
    v4.gst["legal_name"] = documented
    v4.pan_doc["legal_name"] = documented
    v4.bank["holder"] = documented
    v4.deviations = {"gst.legal_name", "pan.legal_name", "bank.holder"}

    v5 = _vendor("05", "vendor_05_narmada_polymers", "Narmada Polymers LLP", "Narmada Polymers",
                 "accounts@narmada-polymers.example",
                 "Plot 88, GIDC Estate, Makarpura, Vadodara, Gujarat 390010, India",
                 "24", "AABFN4321K", "77120045893301", "IVDB0002408",
                 "Limited Liability Partnership", "Limited Liability Partnership", "11/04/2018",
                 "01/05/2018", "Vadodara Alkapuri", "RC Dutt Road, Alkapuri, Vadodara, Gujarat 390007",
                 "GSTIN (valid checksum) embeds PAN AABFN9999K; form and PAN document show AABFN4321K",
                 "REJECTED", ["PAN_GSTIN_MISMATCH"], gstin_pan="AABFN9999K")
    v5.deviations = {"gstin.embedded_pan"}

    v6 = _vendor("06", "vendor_06_zenith_packaging", "Zenith Packaging Industries",
                 "Zenith Packaging", "accounts@zenith-packaging.example",
                 "B-42, Okhla Industrial Area Phase II, New Delhi, Delhi 110020, India",
                 "07", "AAGFZ2045H", "41290033871652", "SFDB0001175", "Partnership",
                 "Firm", "03/02/2013", "01/07/2017", "New Delhi Nehru Place",
                 "Hemkunt Chambers, Nehru Place, New Delhi, Delhi 110019",
                 "Electricity bill in GST certificate slot", "PENDING", ["WRONG_DOCUMENT_TYPE"])
    v6.gst = None
    v6.utility = {"provider": "Yamuna Demo Power Distribution Ltd",
                  "consumer_name": "Zenith Packaging Industries", "consumer_number": "6027 4419 0832",
                  "service_address": v6.form["registered_address"], "meter_number": "YDP-M-882140",
                  "category": "Non-Domestic (Industrial)", "sanctioned_load": "45 kW",
                  "bill_number": "YDP/OKH/2026/10/55821", "bill_date": "05/11/2026",
                  "period": "01/10/2026 to 31/10/2026", "due_date": "20/11/2026",
                  "units": 3412, "rate": 8.00, "fixed": 5400.00, "duty_pct": 5}
    v6.deviations = {"gst_certificate.replaced_by_utility_bill"}

    v7 = _vendor("07", "vendor_07_meridian_tech", "Meridian Tech Services Pvt Ltd", "Meridian Tech",
                 "finance@meridian-tech.example",
                 "Office 502, Baner Business Bay, Baner, Pune, Maharashtra 411045, India",
                 "27", "AAMCM3398E", "50100392847716", "CDBL0002216", "Private Limited Company",
                 "Company", "27/10/2015", "01/07/2017", "Pune Baner",
                 "Baner Road, Near Balewadi Phata, Pune, Maharashtra 411045",
                 "PAN, GSTIN, account and IFSC equal a pre-seeded approved vendor",
                 "REJECTED", ["DUPLICATE_VENDOR"])
    v8 = _vendor("08", "vendor_08_orion_global", "Orion Global Traders", "Orion Global",
                 "accounts@orion-global.example",
                 "Door 33/1180, Market Road, Ernakulam, Kochi, Kerala 682011, India",
                 "32", "AAKFO8812D", "20984411730059", "IVDB0005531", "Partnership",
                 "Firm", "18/07/2012", "01/07/2017", "Kochi Ernakulam",
                 "MG Road, Ernakulam, Kochi, Kerala 682035",
                 "PAN exactly matches a seeded internal blocklist entry",
                 "REJECTED", ["BLOCKLIST_MATCH"])
    return [v1, v2, v3, v4, v5, v6, v7, v8]


def seed_records(vendors: list[Vendor]) -> dict:
    v7 = next(v for v in vendors if v.vendor_id == "07").form
    v8 = next(v for v in vendors if v.vendor_id == "08").form
    return {
        "approved_vendors": [{"legal_name": "Meridian Technology Services Pvt Ltd",
                              "pan": v7["pan"], "gstin": v7["gstin"],
                              "bank_account_number": v7["bank_account_number"], "ifsc": v7["ifsc"]}],
        "blocklist": [{"legal_name": "Orion Global Traders", "pan": v8["pan"],
                       "reason": BLOCKLIST_REASON}],
    }


# ==========================================================================
# PDF drawing
# ==========================================================================

W, H = A4
INK = HexColor("#1F2937")
MUTED = HexColor("#6B7280")
LINE = HexColor("#CBD5E1")
SHADE = HexColor("#F1F5F9")
MARGIN = 48


def new_canvas(buf, title: str):
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(title)
    c.setAuthor("generate_test_data.py (synthetic)")
    c.setSubject(WATERMARK)
    return c


def finish_page(c):
    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUTED)
    for i, line in enumerate(simpleSplit(DISCLAIMER, "Helvetica", 7.5, W - 2 * MARGIN)):
        c.drawCentredString(W / 2, 30 - i * 10, line)
    c.saveState()  # watermark last, translucent, so it never hides content
    c.setFillColor(HexColor("#B91C1C"))
    c.setFillAlpha(0.11)
    c.setFont("Helvetica-Bold", 27)
    c.translate(W / 2, H / 2)
    c.rotate(38)
    c.drawCentredString(0, 0, WATERMARK)
    c.restoreState()
    c.showPage()


def header_band(c, title: str, subtitle: str, color: str, height: float = 74):
    c.setFillColor(HexColor(color))
    c.rect(0, H - height, W, height, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(MARGIN, H - 36, title)
    c.setFont("Helvetica", 9)
    c.drawString(MARGIN, H - 54, subtitle)


def kv_table(c, x: float, y: float, rows, label_w: float, total_w: float, size: float = 10) -> float:
    value_w = total_w - label_w
    for label, value in rows:
        label_lines = simpleSplit(label, "Helvetica-Bold", 8.5, label_w - 14)
        value_lines = simpleSplit(str(value), "Helvetica", size, value_w - 14)
        row_h = max(24, 12 * max(len(label_lines), len(value_lines)) + 12)
        c.setFillColor(SHADE)
        c.rect(x, y - row_h, label_w, row_h, stroke=0, fill=1)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.rect(x, y - row_h, total_w, row_h, stroke=1, fill=0)
        c.line(x + label_w, y, x + label_w, y - row_h)
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 8.5)
        for i, line in enumerate(label_lines):
            c.drawString(x + 7, y - 15 - i * 12, line)
        c.setFillColor(INK)
        c.setFont("Helvetica", size)
        for i, line in enumerate(value_lines):
            c.drawString(x + label_w + 7, y - 15 - i * 12, line)
        y -= row_h
    return y


def paragraph(c, x: float, y: float, text: str, width: float, size: float = 10,
              font: str = "Helvetica", leading: float = 14) -> float:
    c.setFont(font, size)
    c.setFillColor(INK)
    for line in simpleSplit(text, font, size, width):
        c.drawString(x, y, line)
        y -= leading
    return y


def gst_certificate_pdf(v: Vendor) -> bytes:
    g, buf = v.gst, io.BytesIO()
    c = new_canvas(buf, "Synthetic GST registration certificate")
    header_band(c, "GST REGISTRATION CERTIFICATE",
                "Synthetic record  ·  layout inspired by Form GST REG-06  ·  not government issued",
                "#1E3A8A")
    y = H - 104
    c.setFillColor(HexColor("#EFF6FF"))
    c.setStrokeColor(HexColor("#93C5FD"))
    c.roundRect(MARGIN, y - 40, W - 2 * MARGIN, 40, 6, stroke=1, fill=1)
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(MARGIN + 12, y - 16, "REGISTRATION NUMBER (GSTIN)")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(MARGIN + 12, y - 33, g["gstin"])
    y -= 60
    rows = [("1. Legal Name", g["legal_name"]),
            ("2. Trade Name, if any", g["trade_name"] or "—"),
            ("3. Constitution of Business", g["constitution"]),
            ("4. Address of Principal Place of Business", g["address"]),
            ("5. State", g["state"]),
            ("6. Date of Liability", g["liability_date"]),
            ("7. Period of Validity", f"From {g['liability_date']} to Not Applicable"),
            ("8. Type of Registration", "Regular"),
            ("9. Synthetic Reference Number", g["reference"]),
            ("10. Date of Issue of Certificate", g["issue_date"])]
    y = kv_table(c, MARGIN, y, rows, 190, W - 2 * MARGIN)
    y -= 26
    paragraph(c, MARGIN, y, "This synthetic certificate reproduces the information structure of a "
              "GST registration certificate so that document-extraction software can be tested. "
              "It carries no legal validity and was not issued by any tax authority.",
              W - 2 * MARGIN, size=9, leading=13)
    finish_page(c)
    c.save()
    return buf.getvalue()


def pan_card_pdf(v: Vendor) -> bytes:
    p, buf = v.pan_doc, io.BytesIO()
    c = new_canvas(buf, "Synthetic PAN information record")
    cw, ch = 420, 250
    x, top = (W - cw) / 2, H - 80
    c.setFillColor(HexColor("#F8FAFC"))
    c.setStrokeColor(HexColor("#0F766E"))
    c.setLineWidth(1.4)
    c.roundRect(x, top - ch, cw, ch, 14, stroke=1, fill=1)
    c.setFillColor(HexColor("#0F766E"))
    c.roundRect(x, top - 52, cw, 52, 14, stroke=0, fill=1)
    c.rect(x, top - 52, cw, 20, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x + 18, top - 24, "PAN INFORMATION RECORD")
    c.setFont("Helvetica", 8)
    c.drawString(x + 18, top - 40, "Synthetic card-style record  ·  not issued by the Income Tax Department")
    fields = [("Permanent Account Number", p["pan"], 17, "Helvetica-Bold"),
              ("Name", p["legal_name"], 12, "Helvetica-Bold"),
              ("Entity Type", p["entity_type"], 10.5, "Helvetica"),
              ("Date of Incorporation / Formation", p["incorporated"], 10.5, "Helvetica")]
    y = top - 78
    for label, value, size, font in fields:
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 18, y, label.upper())
        c.setFillColor(INK)
        c.setFont(font, size)
        c.drawString(x + 18, y - size - 4, value)
        y -= size + 30
    y = top - ch - 36
    y = kv_table(c, MARGIN, y, [("Record Reference", p["reference"]),
                                ("Record Purpose", "Synthetic test input for vendor-onboarding software")],
                 160, W - 2 * MARGIN, size=9.5)
    paragraph(c, MARGIN, y - 24, "This record mimics the information layout of a PAN card / "
              "allotment letter. It has no photograph, signature or security features and is not "
              "valid identification.", W - 2 * MARGIN, size=9, leading=13)
    finish_page(c)
    c.save()
    return buf.getvalue()


def bank_proof_pdf(v: Vendor) -> bytes:
    b, buf = v.bank, io.BytesIO()
    c = new_canvas(buf, "Synthetic bank account confirmation letter")
    c.setFillColor(HexColor("#7C2D12"))
    c.setFont("Helvetica-Bold", 19)
    c.drawString(MARGIN, H - 64, b["bank_name"])
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    c.drawString(MARGIN, H - 80, f"{b['branch']} Branch  ·  {b['branch_address']}")
    c.setStrokeColor(HexColor("#7C2D12"))
    c.setLineWidth(1.2)
    c.line(MARGIN, H - 90, W - MARGIN, H - 90)
    c.setFillColor(INK)
    c.setFont("Helvetica", 9.5)
    c.drawRightString(W - MARGIN, H - 112, f"Date: {b['letter_date']}")
    c.drawRightString(W - MARGIN, H - 126, f"Ref: {b['reference']}")
    c.setFont("Helvetica-Bold", 11.5)
    c.drawCentredString(W / 2, H - 160, "TO WHOM IT MAY CONCERN")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN, H - 190, "Sub: Confirmation of bank account details")
    y = paragraph(c, MARGIN, H - 212,
                  f"This is to confirm that the following {b['account_type'].lower()} is "
                  f"maintained with our {b['branch']} branch and is active as on the date of "
                  "this letter:", W - 2 * MARGIN)
    rows = [("Account Holder Name", b["holder"]), ("Account Number", b["account"]),
            ("Account Type", b["account_type"]), ("IFSC", b["ifsc"]),
            ("Bank Name", b["bank_name"]), ("Branch Name", b["branch"]),
            ("Branch Address", b["branch_address"])]
    y = kv_table(c, MARGIN, y - 8, rows, 170, W - 2 * MARGIN)
    y = paragraph(c, MARGIN, y - 26, "This letter is issued at the request of the account holder "
                  "without any risk or responsibility on the part of the bank or its officials.",
                  W - 2 * MARGIN)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN, y - 30, f"For {b['bank_name']}")
    c.setFont("Helvetica", 9.5)
    c.drawString(MARGIN, y - 78, "Authorised Signatory")
    c.setFillColor(MUTED)
    c.drawString(MARGIN, y - 92, "(synthetic letter — no signature)")
    c.saveState()  # rubber-stamp style box, clearly labelled as not verified
    c.translate(W - MARGIN - 120, y - 60)
    c.rotate(-8)
    c.setStrokeColor(HexColor("#6D28D9"))
    c.setFillColor(HexColor("#6D28D9"))
    c.setLineWidth(2)
    c.roundRect(-112, -24, 224, 48, 8, stroke=1, fill=0)
    c.setLineWidth(0.8)
    c.roundRect(-106, -18, 212, 36, 6, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(0, -4, "DEMO — NOT BANK VERIFIED")
    c.restoreState()
    finish_page(c)
    c.save()
    return buf.getvalue()


def utility_bill_pdf(v: Vendor) -> bytes:
    u, buf = v.utility, io.BytesIO()
    energy = u["units"] * u["rate"]
    duty = round((energy + u["fixed"]) * u["duty_pct"] / 100, 2)
    total = energy + u["fixed"] + duty
    money = lambda amt: f"Rs. {amt:,.2f}"
    c = new_canvas(buf, "Synthetic electricity bill")
    header_band(c, "ELECTRICITY BILL", f"{u['provider']}  ·  synthetic bill for software testing",
                "#065F46")
    half = (W - 2 * MARGIN - 14) / 2
    top = H - 104
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN, top, "Consumer Details")
    c.drawString(MARGIN + half + 14, top, "Bill Details")
    left = kv_table(c, MARGIN, top - 8, [
        ("Consumer Name", u["consumer_name"]), ("Consumer Number", u["consumer_number"]),
        ("Service Address", u["service_address"]), ("Category", u["category"]),
        ("Sanctioned Load", u["sanctioned_load"]), ("Meter Number", u["meter_number"])],
        92, half, size=9)
    right = kv_table(c, MARGIN + half + 14, top - 8, [
        ("Bill Number", u["bill_number"]), ("Bill Date", u["bill_date"]),
        ("Billing Period", u["period"]), ("Due Date", u["due_date"])], 92, half, size=9)
    y = min(left, right) - 30
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN, y, "Charges")
    y = kv_table(c, MARGIN, y - 8, [
        ("Units Consumed", f"{u['units']:,} kWh"),
        ("Energy Charges", f"{money(energy)}  ({u['units']:,} kWh x Rs. {u['rate']:.2f})"),
        ("Fixed Charges", money(u["fixed"])),
        (f"Electricity Duty ({u['duty_pct']}%)", money(duty))], 200, W - 2 * MARGIN)
    c.setFillColor(HexColor("#ECFDF5"))
    c.setStrokeColor(HexColor("#065F46"))
    c.rect(MARGIN, y - 38, W - 2 * MARGIN, 38, stroke=1, fill=1)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(MARGIN + 10, y - 24, "Total Amount Payable")
    c.drawRightString(W - MARGIN - 10, y - 24, money(total))
    paragraph(c, MARGIN, y - 66, f"Please pay by {u['due_date']} to avoid a late payment "
              "surcharge. Quote your consumer number on all correspondence.", W - 2 * MARGIN, size=9)
    finish_page(c)
    c.save()
    return buf.getvalue()


def scan_pdf(pdf_bytes: bytes, rng: random.Random, angle: float) -> bytes:
    """Rasterize every page, add mild grey noise + rotation, rebuild with images only."""
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    out = pymupdf.open()
    for page in src:
        img = Image.open(io.BytesIO(page.get_pixmap(dpi=170).tobytes("png"))).convert("L")
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
        noise = Image.frombytes("L", img.size, rng.randbytes(img.size[0] * img.size[1]))
        noise = noise.point(lambda v: v * 22 // 255)
        img = ImageChops.subtract(img, noise).filter(ImageFilter.GaussianBlur(0.5))
        jpg = io.BytesIO()
        img.save(jpg, format="JPEG", quality=82)
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=jpg.getvalue(), keep_proportion=True)
    data = out.tobytes(garbage=3, deflate=True, no_new_id=True)  # deterministic bytes
    src.close()
    out.close()
    return data


# ==========================================================================
# Writing
# ==========================================================================

def scenario_metadata(v: Vendor) -> dict:
    return {"vendor_id": v.vendor_id, "vendor_name": v.form["legal_name"], "folder": v.folder,
            "scenario_description": v.description, "expected_outcome": v.expected_outcome,
            "expected_validation_failures": list(v.expected_failures), "documents": v.documents}


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def generate(vendors: list[Vendor]) -> dict[str, dict[str, bytes]]:
    """Write every file; return the pre-scan source PDFs for verification."""
    OUT_DIR.mkdir(exist_ok=True)
    rng = random.Random(SEED)
    angles = [1.4, -1.7, 1.1]
    sources: dict[str, dict[str, bytes]] = {}
    for v in vendors:
        folder = OUT_DIR / v.folder
        folder.mkdir(exist_ok=True)
        write_json(folder / "form_input.json", {k: v.form[k] for k in FORM_KEYS})
        pdfs = {"pan_card.pdf": pan_card_pdf(v), "bank_proof.pdf": bank_proof_pdf(v)}
        if v.utility:
            pdfs["Electricity_Utility_Bill.pdf"] = utility_bill_pdf(v)
        else:
            pdfs["gst_certificate.pdf"] = gst_certificate_pdf(v)
        sources[v.vendor_id] = pdfs
        for i, (name, data) in enumerate(sorted(pdfs.items())):
            (folder / name).write_bytes(scan_pdf(data, rng, angles[i % 3]) if v.scanned else data)
    write_json(OUT_DIR / "test_scenarios.json", [scenario_metadata(v) for v in vendors])
    write_json(OUT_DIR / "seed_db_records.json", seed_records(vendors))
    return sources


# ==========================================================================
# Integrity checks — fail loudly with actionable messages
# ==========================================================================

def check(condition: bool, message: str) -> None:
    if not condition:
        raise GenerationError(message)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _pdf_text(data: bytes) -> str:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def verify_gstin(gstin: str, label: str) -> None:
    check(len(gstin) == 15 and GSTIN_RE.match(gstin) is not None,
          f"{label}: GSTIN {gstin} has invalid structure")
    check(gstin[:2] in STATES, f"{label}: GSTIN state code {gstin[:2]} not in the state table")
    check(gstin[13] == "Z", f"{label}: GSTIN index 13 must be 'Z'")
    check(gstin[14] == gstin_check_char(gstin[:14]), f"{label}: GSTIN {gstin} checksum is wrong")


def actual_deviations(v: Vendor) -> set:
    f, found = v.form, set()
    if v.gst is None:
        found.add("gst_certificate.replaced_by_utility_bill")
    else:
        for key, form_key in (("legal_name", "legal_name"), ("trade_name", "trade_name"),
                              ("gstin", "gstin"), ("address", "registered_address")):
            if v.gst[key] != f[form_key]:
                found.add(f"gst.{key}")
    if f["gstin"][2:12] != f["pan"]:
        found.add("gstin.embedded_pan")
    if v.pan_doc["pan"] != f["pan"]:
        found.add("pan.pan")
    if v.pan_doc["legal_name"] != f["legal_name"]:
        found.add("pan.legal_name")
    for key, form_key in (("holder", "legal_name"), ("account", "bank_account_number"),
                          ("ifsc", "ifsc")):
        if v.bank[key] != f[form_key]:
            found.add(f"bank.{key}")
    return found


def verify(vendors: list[Vendor], sources: dict) -> None:
    # 1. Folders and files
    expected_folders = {v.folder for v in vendors}
    found_folders = {p.name for p in OUT_DIR.glob("vendor_*") if p.is_dir()}
    check(len(vendors) == 8, f"Expected 8 scenarios, built {len(vendors)}")
    check(found_folders == expected_folders,
          f"Unexpected scenario folders in {OUT_DIR}: {sorted(found_folders - expected_folders)}. "
          "Remove stale folders and re-run.")
    for v in vendors:
        folder = OUT_DIR / v.folder
        files = {p.name for p in folder.iterdir() if not p.name.startswith(".")}
        want = {"form_input.json", *v.documents.values()}
        check(files == want, f"{v.folder}: expected files {sorted(want)}, found {sorted(files)}. "
              "Delete unexpected files and re-run.")

    # 2. PDFs open; digital ones have text, scanned ones have none
    for v in vendors:
        for name in v.documents.values():
            data = (OUT_DIR / v.folder / name).read_bytes()
            try:
                text = _pdf_text(data)
            except Exception as exc:
                raise GenerationError(f"{v.folder}/{name} does not open: {exc}") from exc
            if v.scanned:
                check(text.strip() == "", f"{v.folder}/{name} must be image-only but has a text layer")
                with pymupdf.open(stream=data, filetype="pdf") as doc:
                    check(all(page.get_images() for page in doc), f"{v.folder}/{name} has no page image")
            else:
                check(len(text.strip()) > 200, f"{v.folder}/{name} has no extractable text")
                check(WATERMARK in _flat(text), f"{v.folder}/{name} is missing the watermark")

    # 3. Identifier syntax
    for v in vendors:
        f, label = v.form, f"Vendor {v.vendor_id}"
        check(PAN_RE.match(f["pan"]) is not None, f"{label}: PAN {f['pan']} malformed")
        check(IFSC_RE.match(f["ifsc"]) is not None, f"{label}: IFSC {f['ifsc']} malformed")
        check(f["ifsc"][:4] in BANKS, f"{label}: IFSC bank code not in the fictional bank table")
        verify_gstin(f["gstin"], label)
        if v.vendor_id == "05":
            check(f["gstin"][2:12] == "AABFN9999K" and f["pan"] == "AABFN4321K",
                  "Vendor 05: GSTIN must embed AABFN9999K while form/PAN document show AABFN4321K")
            check(f["gstin"][:2] == "24", "Vendor 05: state code must be 24 (Gujarat)")
        else:
            check(f["gstin"][2:12] == f["pan"], f"{label}: GSTIN must embed the form PAN")
        check(f["contact_email"].endswith(".example"), f"{label}: email must use a .example domain")
        check(f["contact_email"].split("@")[1] not in FREE_DOMAINS, f"{label}: free email domain")
        check(all(isinstance(f[k], str) for k in FORM_KEYS), f"{label}: all form values must be strings")

    # 4. Only the specified deviation, and the documents really carry the data
    for v in vendors:
        check(actual_deviations(v) == v.deviations,
              f"Vendor {v.vendor_id}: deviations {sorted(actual_deviations(v))} differ from the "
              f"specified {sorted(v.deviations)}")
        src = {name: _flat(_pdf_text(data)) for name, data in sources[v.vendor_id].items()}
        must_contain = {
            "pan_card.pdf": [v.pan_doc["pan"], v.pan_doc["legal_name"]],
            "bank_proof.pdf": [v.bank["holder"], v.bank["account"], v.bank["ifsc"], "DEMO — NOT BANK VERIFIED"],
        }
        if v.gst:
            must_contain["gst_certificate.pdf"] = [v.gst["gstin"], v.gst["legal_name"],
                                                   v.gst["trade_name"], v.gst["address"]]
        else:
            must_contain["Electricity_Utility_Bill.pdf"] = [v.utility["consumer_name"], "ELECTRICITY BILL"]
            bill_text = src["Electricity_Utility_Bill.pdf"]
            check("GST" not in bill_text and v.form["gstin"] not in bill_text,
                  "Vendor 06: utility bill must not contain GST registration information")
        for name, needles in must_contain.items():
            for needle in needles:
                check(needle in src[name], f"Vendor {v.vendor_id}: '{needle}' missing from {name}")
        meta = scenario_metadata(v)
        check(meta["documents"]["gst_certificate"] in v.documents.values(),
              f"Vendor {v.vendor_id}: metadata points to a file that was not generated")
        check(all(r in RULE_IDS for r in meta["expected_validation_failures"]),
              f"Vendor {v.vendor_id}: unknown rule ID in expected failures")

    # No identifier collisions between unrelated vendors
    for key in ("pan", "gstin", "bank_account_number"):
        values = [v.form[key] for v in vendors]
        check(len(set(values)) == len(values), f"Duplicate {key} across vendors: {values}")

    # 5. Seed relationships
    seed = json.loads((OUT_DIR / "seed_db_records.json").read_text(encoding="utf-8"))
    v7 = next(v for v in vendors if v.vendor_id == "07").form
    v8 = next(v for v in vendors if v.vendor_id == "08").form
    approved = seed["approved_vendors"][0]
    check(all(approved[k] == v7[k] for k in ("pan", "gstin", "bank_account_number", "ifsc")),
          "Seed approved vendor identifiers must equal Vendor 07's")
    check(approved["legal_name"] == "Meridian Technology Services Pvt Ltd", "Seed approved vendor name")
    check(seed["blocklist"][0]["pan"] == v8["pan"], "Seed blocklist PAN must equal Vendor 08's PAN")
    check(seed["blocklist"][0]["reason"] == BLOCKLIST_REASON, "Seed blocklist reason text")

    # 6. No test labels leak into app-facing inputs
    for path in [OUT_DIR / v.folder / "form_input.json" for v in vendors] + [OUT_DIR / "seed_db_records.json"]:
        raw = path.read_text(encoding="utf-8")
        check("expected" not in raw.lower() and not any(r in raw for r in RULE_IDS),
              f"{path} contains test-only labels")
        if path.name == "form_input.json":
            check(list(json.loads(raw)) == FORM_KEYS, f"{path} must contain exactly {FORM_KEYS}")
    scenarios = json.loads((OUT_DIR / "test_scenarios.json").read_text(encoding="utf-8"))
    check(len(scenarios) == 8 and [s["vendor_id"] for s in scenarios] == [f"{i:02d}" for i in range(1, 9)],
          "test_scenarios.json must list vendors 01-08 in order")


def print_table(vendors: list[Vendor]) -> None:
    print(f"\nGenerated {len(vendors)} scenarios in {OUT_DIR.resolve()}\n")
    print(f"{'ID':<3} {'Vendor':<36} {'Documents':<9} {'Expected':<9} Failure rule")
    print("-" * 86)
    for v in vendors:
        print(f"{v.vendor_id:<3} {v.form['legal_name']:<36} {'scanned' if v.scanned else 'digital':<9} "
              f"{v.expected_outcome:<9} {', '.join(v.expected_failures) or '—'}")
    print("\nAll integrity checks passed.")
    print("Next: python database.py --seed ./sample_data/seed_db_records.json")


def main() -> int:
    vendors = build_vendors()
    try:
        sources = generate(vendors)
        verify(vendors, sources)
    except GenerationError as exc:
        print(f"\nGENERATION CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    print_table(vendors)
    return 0


if __name__ == "__main__":
    sys.exit(main())
