"""Deterministic normalization and validation rules.

Everything in this module is plain Python: no network calls and no LLM.
Given the same form values, extracted document fields and reference records,
the output is always identical, and every result carries the evidence that
produced it.

Each check returns a record:
    {"rule_id", "status": PASS|WARN|FAIL, "details", "evidence": {...},
     "disposition": NONE|REVIEW|REJECT}
FAIL does not always mean REJECT; the disposition carries the policy outcome.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "28": "Andhra Pradesh (old)", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

# 4th character of a PAN encodes the holder type.
PAN_HOLDER_TYPES = {
    "C": "Company", "F": "Firm / LLP", "P": "Individual",
    "H": "Hindu Undivided Family", "A": "Association of Persons",
    "B": "Body of Individuals", "G": "Government agency",
    "J": "Artificial juridical person", "L": "Local authority", "T": "Trust",
}

FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in",
    "ymail.com", "rocketmail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "rediffmail.com", "icloud.com", "me.com", "aol.com",
    "protonmail.com", "proton.me", "zohomail.com", "zohomail.in", "gmx.com",
    "mail.com", "yandex.com",
})

SLOTS = ("gst_certificate", "pan_document", "bank_proof")
SLOT_EXPECTED_TYPE = {
    "gst_certificate": "GST_CERTIFICATE",
    "pan_document": "PAN_DOCUMENT",
    "bank_proof": "BANK_PROOF",
}
DOC_LABELS = {
    "GST_CERTIFICATE": "GST registration certificate",
    "PAN_DOCUMENT": "PAN document",
    "BANK_PROOF": "bank account confirmation",
    "UTILITY_BILL": "utility bill",
    "UNKNOWN": "unrecognised document",
}

# Extraction error codes (set by extractor.py) mapped to rule IDs.
UNREADABLE_ERROR_CODES = frozenset({
    "UNSUPPORTED_FILE_TYPE", "FILE_TOO_LARGE", "TOO_MANY_PAGES",
    "CORRUPT_FILE", "EMPTY_FILE", "NOT_LEGIBLE",
})
EXTRACTION_ERROR_CODES = frozenset({"EXTRACTION_UNAVAILABLE"})

# Corporate suffix variants collapse to one canonical token sequence.
_TOKEN_CANON = {
    "pvt": "private", "pvtltd": "private limited", "ltd": "limited",
    "co": "company", "corp": "corporation", "intl": "international",
    "and": "and",
}
_HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "shri", "sri", "smt", "dr"})
_LEGAL_FORM_TOKENS = frozenset({"private", "limited", "llp", "company", "corporation", "inc", "plc"})
_ORG_TOKENS = _LEGAL_FORM_TOKENS | frozenset({
    "industries", "industry", "traders", "trading", "enterprises", "enterprise",
    "solutions", "services", "technologies", "technology", "tech", "exports",
    "exporters", "imports", "agro", "logistics", "polymers", "packaging",
    "components", "global", "international", "group", "holdings", "partners",
    "associates", "ventures", "systems", "labs", "foods", "pharma",
    "textiles", "motors", "infra", "engineering", "works", "mills",
    "agencies", "trust", "foundation", "society", "huf", "firm", "bank",
    "supply", "chain", "precision", "cloud", "india",
})


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------

def clean(value) -> str:
    return "" if value is None else str(value).strip()


def normalize_identifier(value) -> str:
    """PAN / GSTIN / IFSC: uppercase, drop spaces, hyphens and dots."""
    return re.sub(r"[^0-9A-Za-z]", "", clean(value)).upper()


def normalize_account(value) -> str:
    return re.sub(r"[\s\-./]", "", clean(value)).upper()


def name_tokens(value) -> list[str]:
    text = clean(value).lower().replace("&", " and ")
    text = re.sub(r"\bm\s*/\s*s\b\.?", " ", text)          # "M/s."
    text = re.sub(r"\bpvt\.?\s*ltd\b", " pvtltd ", text)    # "Pvt.Ltd"
    tokens = re.sub(r"[^0-9a-z]+", " ", text).split()
    out: list[str] = []
    for tok in tokens:
        out.extend(_TOKEN_CANON.get(tok, tok).split())
    if out and out[0] == "the":
        out = out[1:]
    return out


def normalize_name(value) -> str:
    """Casing, whitespace, punctuation and 'Pvt Ltd' == 'Private Limited'."""
    return " ".join(name_tokens(value))


def mask_account(value) -> str:
    acct = normalize_account(value)
    if not acct:
        return "—"
    return ("•" * max(len(acct) - 4, 0)) + acct[-4:]


def gstin_check_char(first14: str) -> str:
    """Standard GSTIN modulo-36 check character over the first 14 chars."""
    total = 0
    for i, ch in enumerate(first14):
        value = GSTIN_CHARSET.index(ch)
        product = value * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return GSTIN_CHARSET[(36 - total % 36) % 36]


def gstin_structure_problem(gstin: str) -> str | None:
    if len(gstin) != 15:
        return f"GSTIN must be 15 characters; got {len(gstin)}"
    if gstin[:2] not in GST_STATE_CODES:
        return f"'{gstin[:2]}' is not a valid GST state code"
    if not GSTIN_RE.match(gstin):
        return ("Structure must be 2-digit state + 10-character PAN + entity "
                "character + 'Z' + check character")
    if not PAN_RE.match(gstin[2:12]):
        return f"Embedded PAN '{gstin[2:12]}' is not a valid PAN pattern"
    return None


def holder_type(pan: str) -> str | None:
    return PAN_HOLDER_TYPES.get(pan[3]) if PAN_RE.match(pan) else None


def is_non_individual(pan: str) -> bool:
    return bool(PAN_RE.match(pan)) and pan[3] != "P"


def looks_like_individual(name: str) -> bool:
    """Conservative: 2-4 alphabetic tokens, no organisation words."""
    tokens = [t for t in name_tokens(name) if t not in _HONORIFICS]
    return (2 <= len(tokens) <= 4
            and all(t.isalpha() for t in tokens)
            and not any(t in _ORG_TOKENS for t in tokens))


def significant_tokens(name: str) -> set[str]:
    return {t for t in name_tokens(name) if t not in _LEGAL_FORM_TOKENS and len(t) > 2}


def _rule(rule_id, status, details, evidence, disposition="NONE") -> dict:
    return {"rule_id": rule_id, "status": status, "details": details,
            "evidence": evidence, "disposition": disposition}


# --------------------------------------------------------------------------
# Document evidence
# --------------------------------------------------------------------------

def _error_codes(doc: dict) -> set[str]:
    return {e.get("code") for e in doc.get("errors", []) if isinstance(e, dict)}


def _doc_usable(doc: dict | None) -> bool:
    return bool(doc) and doc.get("is_legible") is True and not (
        _error_codes(doc) & (UNREADABLE_ERROR_CODES | EXTRACTION_ERROR_CODES))


def select_evidence(documents: dict) -> dict:
    """Pick one usable document per type, judged by content not slot.

    A document in its expected slot is preferred; a misplaced document is still
    used as evidence for its detected type (WRONG_DOCUMENT_TYPE flags the slot).
    """
    evidence: dict[str, dict] = {}
    for slot in SLOTS:
        doc = documents.get(slot)
        if _doc_usable(doc) and doc["document_type_detected"] == SLOT_EXPECTED_TYPE[slot]:
            evidence[doc["document_type_detected"]] = doc
    for slot in SLOTS:
        doc = documents.get(slot)
        if _doc_usable(doc):
            evidence.setdefault(doc["document_type_detected"], doc)
    return evidence


def check_documents(documents: dict, file_names: dict) -> list[dict]:
    results = []
    for slot in SLOTS:
        expected = SLOT_EXPECTED_TYPE[slot]
        doc = documents.get(slot)
        name = file_names.get(slot)
        ev = {"upload_slot": slot, "file": name or "—"}
        if doc is None:
            results.append(_rule(
                "MISSING_REQUIRED_DOCUMENT", "FAIL",
                f"No file was uploaded for the required {DOC_LABELS[expected]}.",
                ev, "REVIEW"))
            continue
        codes = _error_codes(doc)
        messages = "; ".join(e.get("message", "") for e in doc.get("errors", []))
        ev = {**ev, "extraction_method": doc.get("extraction_method") or "—"}
        if codes & EXTRACTION_ERROR_CODES and not codes & UNREADABLE_ERROR_CODES:
            results.append(_rule(
                "EXTRACTION_UNAVAILABLE", "FAIL",
                f"Document extraction did not complete for the {DOC_LABELS[expected]}. {messages}".strip(),
                ev, "REVIEW"))
        elif codes & UNREADABLE_ERROR_CODES or doc.get("is_legible") is not True:
            results.append(_rule(
                "DOCUMENT_UNREADABLE", "FAIL",
                f"The {DOC_LABELS[expected]} could not be read reliably. {messages}".strip(),
                ev, "REVIEW"))
        else:
            detected = doc.get("document_type_detected") or "UNKNOWN"
            ev = {**ev, "expected_type": expected, "detected_type": detected}
            if detected == expected:
                results.append(_rule(
                    "WRONG_DOCUMENT_TYPE", "PASS",
                    f"Content is a {DOC_LABELS[expected]}, as expected for this slot.", ev))
            else:
                results.append(_rule(
                    "WRONG_DOCUMENT_TYPE", "FAIL",
                    f"Expected a {DOC_LABELS[expected]} but the content is a "
                    f"{DOC_LABELS.get(detected, detected)}.", ev, "REVIEW"))
    return results


# --------------------------------------------------------------------------
# Format checks on submitted identifiers
# --------------------------------------------------------------------------

def check_formats(form: dict) -> list[dict]:
    results = []
    pan = normalize_identifier(form.get("pan"))
    gstin = normalize_identifier(form.get("gstin"))
    ifsc = normalize_identifier(form.get("ifsc"))

    if PAN_RE.match(pan):
        results.append(_rule("PAN_INVALID_FORMAT", "PASS",
                             f"PAN matches AAAAA9999A pattern (holder type: {holder_type(pan)}).",
                             {"form_pan": pan}))
    else:
        results.append(_rule("PAN_INVALID_FORMAT", "FAIL",
                             "PAN must be 5 letters, 4 digits, 1 letter (e.g. AAAAA9999A).",
                             {"form_pan": pan or "—"}, "REVIEW"))

    problem = gstin_structure_problem(gstin)
    if problem:
        results.append(_rule("GSTIN_INVALID_FORMAT", "FAIL", problem,
                             {"form_gstin": gstin or "—"}, "REVIEW"))
    else:
        results.append(_rule("GSTIN_INVALID_FORMAT", "PASS",
                             f"Valid structure; state code {gstin[:2]} = {GST_STATE_CODES[gstin[:2]]}.",
                             {"form_gstin": gstin}))
        expected = gstin_check_char(gstin[:14])
        ev = {"form_gstin": gstin, "check_character": gstin[14], "computed": expected}
        if gstin[14] == expected:
            results.append(_rule("GSTIN_INVALID_CHECKSUM", "PASS",
                                 "Modulo-36 check character is correct.", ev))
        else:
            results.append(_rule("GSTIN_INVALID_CHECKSUM", "FAIL",
                                 f"Check character is '{gstin[14]}' but the computed value is "
                                 f"'{expected}' — likely a typo.", ev, "REVIEW"))

    if IFSC_RE.match(ifsc):
        results.append(_rule("IFSC_INVALID_FORMAT", "PASS",
                             "IFSC matches 4 letters + '0' + 6 alphanumerics.", {"form_ifsc": ifsc}))
    else:
        results.append(_rule("IFSC_INVALID_FORMAT", "FAIL",
                             "IFSC must be 4 letters, '0', then 6 letters/digits (e.g. ABCD0123456).",
                             {"form_ifsc": ifsc or "—"}, "REVIEW"))
    return results


# --------------------------------------------------------------------------
# Cross-reference checks: form vs documents
# --------------------------------------------------------------------------

def check_pan_gstin(form: dict, evidence: dict) -> list[dict]:
    """GSTIN embeds the holder's PAN at [2:12]; compare with every PAN source."""
    gstins = {}
    form_gstin = normalize_identifier(form.get("gstin"))
    if not gstin_structure_problem(form_gstin):
        gstins["form GSTIN"] = form_gstin
    gst_doc = evidence.get("GST_CERTIFICATE")
    if gst_doc:
        doc_gstin = normalize_identifier(gst_doc["fields"].get("gstin"))
        if doc_gstin and not gstin_structure_problem(doc_gstin):
            gstins["GST certificate GSTIN"] = doc_gstin

    pans = {}
    form_pan = normalize_identifier(form.get("pan"))
    if PAN_RE.match(form_pan):
        pans["form PAN"] = form_pan
    pan_doc = evidence.get("PAN_DOCUMENT")
    if pan_doc:
        doc_pan = normalize_identifier(pan_doc["fields"].get("pan"))
        if PAN_RE.match(doc_pan):
            pans["PAN document"] = doc_pan

    if not gstins or not pans:
        return []
    embedded: dict[str, list[str]] = {}
    for label, g in gstins.items():
        embedded.setdefault(g[2:12], []).append(label)
    pan_values: dict[str, list[str]] = {}
    for label, p in pans.items():
        pan_values.setdefault(p, []).append(label)
    ev = {"gstin_embedded_pan": ", ".join(embedded),
          "gstin_sources": "; ".join(f"{e}: {', '.join(ls)}" for e, ls in embedded.items()),
          "pan": ", ".join(pan_values),
          "pan_sources": "; ".join(f"{p}: {', '.join(ls)}" for p, ls in pan_values.items())}
    conflicts = [f"{' and '.join(gl)} embed{'s' if len(gl) == 1 else ''} {e}, but "
                 f"{' and '.join(pl)} show{'s' if len(pl) == 1 else ''} {p}"
                 for e, gl in embedded.items() for p, pl in pan_values.items() if e != p]
    if conflicts:
        return [_rule("PAN_GSTIN_MISMATCH", "FAIL",
                      "The GSTIN belongs to a different PAN holder: " + "; ".join(conflicts) + ".",
                      ev, "REJECT")]
    return [_rule("PAN_GSTIN_MISMATCH", "PASS",
                  "PAN embedded in the GSTIN (characters 3–12) matches the PAN on the form "
                  "and PAN document.", ev)]


def check_identifiers(form: dict, evidence: dict) -> list[dict]:
    specs = [
        ("PAN", "pan", "PAN_DOCUMENT", normalize_identifier, lambda v: v),
        ("GSTIN", "gstin", "GST_CERTIFICATE", normalize_identifier, lambda v: v),
        ("Bank account number", "bank_account_number", "BANK_PROOF", normalize_account, mask_account),
        ("IFSC", "ifsc", "BANK_PROOF", normalize_identifier, lambda v: v),
    ]
    results = []
    for label, field, doc_type, norm, show in specs:
        doc = evidence.get(doc_type)
        if not doc:
            continue
        form_val = norm(form.get(field))
        doc_val = norm(doc["fields"].get(field))
        ev = {"field": label, "form": show(form_val) if form_val else "—",
              DOC_LABELS[doc_type]: show(doc_val) if doc_val else "not found"}
        if not doc_val:
            results.append(_rule("IDENTIFIER_MISMATCH", "WARN",
                                 f"{label} could not be found on the {DOC_LABELS[doc_type]}, "
                                 "so the form value is uncorroborated.", ev, "REVIEW"))
        elif form_val == doc_val:
            results.append(_rule("IDENTIFIER_MISMATCH", "PASS",
                                 f"{label} on the form matches the {DOC_LABELS[doc_type]}.", ev))
        else:
            results.append(_rule("IDENTIFIER_MISMATCH", "FAIL",
                                 f"{label} on the form differs from the {DOC_LABELS[doc_type]}.",
                                 ev, "REVIEW"))
    return results


def _documented_legal_names(evidence: dict) -> dict:
    names = {}
    for doc_type in ("GST_CERTIFICATE", "PAN_DOCUMENT"):
        doc = evidence.get(doc_type)
        if doc:
            names[DOC_LABELS[doc_type]] = clean(doc["fields"].get("legal_name"))
    return names


def check_legal_name(form: dict, evidence: dict) -> list[dict]:
    documented = _documented_legal_names(evidence)
    if not documented:
        return []
    form_legal = clean(form.get("legal_name"))
    ev = {"form legal name": form_legal}
    ev.update({f"{k} legal name": v or "not found" for k, v in documented.items()})

    # Trade/DBA names are reconciled separately and never count as a legal-name match.
    gst = evidence.get("GST_CERTIFICATE")
    form_trade = clean(form.get("trade_name"))
    if gst:
        doc_trade = clean(gst["fields"].get("trade_name"))
        if not form_trade:
            ev["trade name"] = "not submitted"
        elif not doc_trade:
            ev["trade name"] = f"'{form_trade}' (GST certificate shows none)"
        elif normalize_name(form_trade) == normalize_name(doc_trade):
            ev["trade name"] = f"'{form_trade}' matches GST trade name"
        else:
            ev["trade name"] = f"'{form_trade}' differs from GST trade name '{doc_trade}'"

    missing = [k for k, v in documented.items() if not v]
    mismatched = [k for k, v in documented.items()
                  if v and normalize_name(v) != normalize_name(form_legal)]
    if mismatched:
        shown = "; ".join(f"{k}: '{documented[k]}'" for k in mismatched)
        return [_rule("LEGAL_NAME_MISMATCH", "FAIL",
                      f"Submitted legal name '{form_legal}' does not match the registered "
                      f"legal name ({shown}). Needs clarification of the legal entity.",
                      ev, "REVIEW")]
    if missing:
        return [_rule("LEGAL_NAME_MISMATCH", "WARN",
                      f"Legal name could not be read from the {', '.join(missing)}.",
                      ev, "REVIEW")]
    return [_rule("LEGAL_NAME_MISMATCH", "PASS",
                  "Submitted legal name matches the GST/PAN legal name "
                  "(after normalising case, punctuation and suffixes).", ev)]


def check_bank_beneficiary(form: dict, evidence: dict) -> list[dict]:
    bank = evidence.get("BANK_PROOF")
    if not bank:
        return []
    holder = clean(bank["fields"].get("account_holder_name"))
    form_legal = clean(form.get("legal_name"))
    form_pan = normalize_identifier(form.get("pan"))
    documented = {k: v for k, v in _documented_legal_names(evidence).items() if v}
    ev = {"account holder": holder or "not found", "form legal name": form_legal}
    ev.update({f"{k} legal name": v for k, v in documented.items()})
    ev["vendor type (from PAN)"] = holder_type(form_pan) or "unknown"

    if not holder:
        return [_rule("BANK_BENEFICIARY_MISMATCH", "WARN",
                      "Account holder name not found on the bank confirmation.", ev, "REVIEW")]
    if normalize_name(holder) == normalize_name(form_legal):
        return [_rule("BANK_BENEFICIARY_MISMATCH", "PASS",
                      "Account holder matches the submitted legal name.", ev)]
    if any(normalize_name(holder) == normalize_name(v) for v in documented.values()):
        return [_rule("BANK_BENEFICIARY_MISMATCH", "WARN",
                      "Account holder matches the legal entity on the GST/PAN documents, not the "
                      "submitted legal name — covered by the legal-name clarification.", ev)]

    vendor_names = [form_legal, clean(form.get("trade_name")), *documented.values()]
    vendor_tokens = set().union(*(significant_tokens(n) for n in vendor_names if n))
    unrelated_person = (looks_like_individual(holder)
                        and not (significant_tokens(holder) & vendor_tokens))
    if unrelated_person and is_non_individual(form_pan):
        return [_rule("BANK_BENEFICIARY_MISMATCH", "FAIL",
                      f"Account holder '{holder}' appears to be an individual unrelated to the "
                      f"{holder_type(form_pan).lower()} vendor. Policy: payments to business "
                      "vendors go only to accounts held in the entity's own name.", ev, "REJECT")]
    return [_rule("BANK_BENEFICIARY_MISMATCH", "FAIL",
                  f"Account holder '{holder}' does not match the vendor's legal name; the "
                  "relationship is unclear and needs review.", ev, "REVIEW")]


# --------------------------------------------------------------------------
# Reference-data checks (approved vendors, blocklist) and contact email
# --------------------------------------------------------------------------

def check_duplicates(form: dict, approved_candidates: list[dict]) -> list[dict]:
    pan = normalize_identifier(form.get("pan"))
    gstin = normalize_identifier(form.get("gstin"))
    acct = normalize_account(form.get("bank_account_number"))
    ifsc = normalize_identifier(form.get("ifsc"))
    legal = normalize_name(form.get("legal_name"))

    exact, bank_only = [], []
    for row in approved_candidates:
        matched = [label for label, a, b in (
            ("PAN", pan, row["pan"]), ("GSTIN", gstin, row["gstin"])) if a and a == b]
        if matched:
            exact.append((row, matched))
        elif acct and acct == normalize_account(row["bank_account_number"]):
            bank_only.append(row)

    if exact:
        row, matched = exact[0]
        ev = {"approved vendor": row["legal_name"], "matched on": ", ".join(matched),
              "approved via": row.get("source") or "—"}
        if acct == normalize_account(row["bank_account_number"]):
            ev["bank account"] = f"also matches ({mask_account(acct)})"
        return [_rule("DUPLICATE_VENDOR", "FAIL",
                      f"{' and '.join(matched)} already belong to approved vendor "
                      f"'{row['legal_name']}'.", ev, "REJECT")]
    for row in bank_only:
        corroborated = (ifsc == normalize_identifier(row["ifsc"])
                        and legal == normalize_name(row["legal_name"]))
        ev = {"approved vendor": row["legal_name"], "bank account": mask_account(acct),
              "IFSC matches": "yes" if ifsc == normalize_identifier(row["ifsc"]) else "no"}
        if corroborated:
            return [_rule("DUPLICATE_VENDOR", "FAIL",
                          "Bank account, IFSC and legal name match an approved vendor.",
                          ev, "REJECT")]
        return [_rule("DUPLICATE_VENDOR", "WARN",
                      f"Bank account is already registered to a different approved vendor "
                      f"('{row['legal_name']}'); not a duplicate by PAN/GSTIN, but needs review.",
                      ev, "REVIEW")]
    return [_rule("DUPLICATE_VENDOR", "PASS",
                  "No approved vendor shares this PAN, GSTIN or bank account.",
                  {"checked": "PAN, GSTIN, bank account against approved_vendors"})]


def candidate_pans(form: dict, evidence: dict) -> list[str]:
    pans = {normalize_identifier(form.get("pan"))}
    gstin = normalize_identifier(form.get("gstin"))
    if len(gstin) == 15:
        pans.add(gstin[2:12])
    pan_doc = evidence.get("PAN_DOCUMENT")
    if pan_doc:
        pans.add(normalize_identifier(pan_doc["fields"].get("pan")))
    return sorted(p for p in pans if PAN_RE.match(p))


def check_blocklist(pans: list[str], blocklist_hits: list[dict]) -> list[dict]:
    hits = [row for row in blocklist_hits if row["pan"] in pans]
    if hits:
        row = hits[0]
        return [_rule("BLOCKLIST_MATCH", "FAIL",
                      f"PAN {row['pan']} is on the internal blocklist.",
                      {"blocklisted entity": row["legal_name"], "pan": row["pan"],
                       "reason": row["reason"]}, "REJECT")]
    return [_rule("BLOCKLIST_MATCH", "PASS", "No PAN matches the internal blocklist.",
                  {"pans checked": ", ".join(pans) or "—"})]


def check_email_domain(form: dict) -> list[dict]:
    email = clean(form.get("contact_email")).lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    pan = normalize_identifier(form.get("pan"))
    ev = {"contact_email": email or "—", "domain": domain or "—"}
    if domain in FREE_EMAIL_DOMAINS:
        if is_non_individual(pan):
            return [_rule("FREE_EMAIL_DOMAIN", "FAIL",
                          f"Business vendor uses a personal email provider ({domain}).",
                          ev, "REVIEW")]
        return [_rule("FREE_EMAIL_DOMAIN", "WARN",
                      f"Personal email provider ({domain}); acceptable for an individual vendor.", ev)]
    return [_rule("FREE_EMAIL_DOMAIN", "PASS", "Contact email uses a non-personal domain.", ev)]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def run_all_checks(form: dict, documents: dict, file_names: dict,
                   approved_candidates: list[dict], blocklist_hits: list[dict]) -> list[dict]:
    """Run every applicable check. Checks that need a document which is
    missing/unusable are skipped (the document rule already forces review)."""
    evidence = select_evidence(documents)
    rules: list[dict] = []
    rules += check_documents(documents, file_names)
    rules += check_formats(form)
    rules += check_pan_gstin(form, evidence)
    rules += check_identifiers(form, evidence)
    rules += check_legal_name(form, evidence)
    rules += check_bank_beneficiary(form, evidence)
    rules += check_duplicates(form, approved_candidates)
    rules += check_blocklist(candidate_pans(form, evidence), blocklist_hits)
    rules += check_email_domain(form)
    return rules
