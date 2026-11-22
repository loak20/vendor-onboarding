"""Rule-to-decision policy and deterministic vendor email drafting.

The decision is computed only from rule dispositions — never from an LLM:
    any REJECT -> REJECTED; else any REVIEW -> PENDING; else APPROVED.
"""
from __future__ import annotations

from validator import SLOTS, DOC_LABELS, SLOT_EXPECTED_TYPE

APPROVED_LABEL = "Approved under simulated onboarding policy"


def decide(rules: list[dict]) -> tuple[str, str]:
    """Return (status, internal explanation)."""
    rejects = [r for r in rules if r["disposition"] == "REJECT"]
    reviews = [r for r in rules if r["disposition"] == "REVIEW"]

    # Safety net: approval needs every slot to be a verified document of the right type.
    verified_slots = {r["evidence"].get("upload_slot") for r in rules
                      if r["rule_id"] == "WRONG_DOCUMENT_TYPE" and r["status"] == "PASS"}
    evidence_complete = verified_slots == set(SLOTS)

    if rejects:
        status = "REJECTED"
        text = "Rejected — " + " ".join(f"[{r['rule_id']}] {r['details']}" for r in rejects)
        if reviews:
            text += f" Also {len(reviews)} item(s) would need review."
    elif reviews or not evidence_complete:
        status = "PENDING"
        items = [f"[{r['rule_id']}] {r['details']}" for r in reviews]
        if not evidence_complete and not reviews:
            items.append("Not every required document was verified, so approval is withheld.")
        text = f"Pending — {len(items)} item(s) need review: " + " ".join(items)
    else:
        status = "APPROVED"
        text = (f"{APPROVED_LABEL}. All {len(rules)} checks passed or were informational: "
                "documents classified as expected, identifiers consistent across form and "
                "documents, no duplicate or blocklist match.")
    return status, text


# --------------------------------------------------------------------------
# Vendor-facing email (drafted, never sent)
# --------------------------------------------------------------------------

def _doc_label_for_slot(slot: str) -> str:
    return DOC_LABELS[SLOT_EXPECTED_TYPE[slot]]


def _action_item(rule: dict, form: dict) -> str | None:
    ev, rid = rule["evidence"], rule["rule_id"]
    slot = ev.get("upload_slot")
    file_name = ev.get("file")
    if rid == "MISSING_REQUIRED_DOCUMENT":
        return f"Please upload your {_doc_label_for_slot(slot)}; it was not included."
    if rid == "WRONG_DOCUMENT_TYPE":
        detected = DOC_LABELS.get(ev.get("detected_type"), "different document")
        return (f"The file provided as your {_doc_label_for_slot(slot)} ('{file_name}') appears "
                f"to be a {detected}. Please upload your {_doc_label_for_slot(slot)} instead.")
    if rid == "DOCUMENT_UNREADABLE":
        return (f"We could not read your {_doc_label_for_slot(slot)} ('{file_name}'). Please "
                "upload a clear, complete copy (PDF, PNG or JPG, up to 10 MB and 5 pages).")
    if rid == "EXTRACTION_UNAVAILABLE":
        return None  # Our system issue — handled internally, nothing for the vendor to do.
    if rid == "PAN_INVALID_FORMAT":
        return (f"The PAN entered ('{ev.get('form_pan')}') is not in the standard 10-character "
                "format (e.g. AAAAA9999A). Please confirm your PAN.")
    if rid in ("GSTIN_INVALID_FORMAT", "GSTIN_INVALID_CHECKSUM"):
        return (f"The GSTIN entered ('{ev.get('form_gstin')}') failed our format and check-digit "
                "validation. Please re-enter it exactly as shown on your GST registration certificate.")
    if rid == "IFSC_INVALID_FORMAT":
        return (f"The IFSC entered ('{ev.get('form_ifsc')}') is not in the standard 11-character "
                "format. Please confirm it from your cheque leaf or bank letter.")
    if rid == "IDENTIFIER_MISMATCH":
        field = ev.get("field")
        doc_values = {k: v for k, v in ev.items() if k not in ("field", "form")}
        doc_label, doc_value = next(iter(doc_values.items()))
        if rule["status"] == "WARN":
            return (f"We could not find your {field} on the {doc_label}. Please upload a copy "
                    f"that clearly shows it.")
        return (f"The {field} entered ({ev.get('form')}) does not match your {doc_label} "
                f"({doc_value}). Please correct the form or upload the correct document.")
    if rid == "LEGAL_NAME_MISMATCH":
        documented = [v for k, v in ev.items() if k.endswith("legal name") and k != "form legal name"]
        doc_name = next((v for v in documented if v and v != "not found"), None)
        if rule["status"] == "WARN" or not doc_name:
            return "We could not read the legal name on your GST/PAN documents. Please upload clearer copies."
        trade = form.get("trade_name") or ""
        trade_note = f" If '{trade}' is your trading name, " if trade else " "
        return (f"The legal name entered ('{form.get('legal_name')}') differs from the legal name "
                f"on your GST/PAN documents ('{doc_name}').{trade_note}please confirm the registered "
                "legal entity we should onboard and resubmit using that exact legal name.")
    if rid == "BANK_BENEFICIARY_MISMATCH":
        holder = ev.get("account holder")
        if rule["status"] == "WARN" and holder in (None, "not found"):
            return "Please upload a bank confirmation that clearly shows the account holder name."
        if rule["disposition"] == "REJECT":
            return (f"The bank confirmation names an individual account holder ('{holder}'). Under "
                    "our payment policy, business vendors must be paid into an account held in the "
                    f"business's own name, so we cannot onboard this account. You may submit a new "
                    f"application with a bank confirmation for an account held by "
                    f"'{form.get('legal_name')}'.")
        return (f"The account holder on your bank confirmation ('{holder}') does not match your "
                f"legal name. Please provide a bank confirmation for an account held in the name "
                f"of '{form.get('legal_name')}', or explain the relationship.")
    if rid == "PAN_GSTIN_MISMATCH":
        return (f"The PAN embedded in your GSTIN (characters 3–12: {ev.get('gstin_embedded_pan')}) "
                f"does not match your PAN ({ev.get('pan')}). A GSTIN is issued against the PAN of "
                "the same legal entity, so we cannot proceed "
                "with this submission. If this is an error, please submit a new application with "
                "matching GST and PAN details.")
    if rid == "DUPLICATE_VENDOR":
        if rule["disposition"] == "REVIEW":
            return ("The bank account provided is already associated with another vendor in our "
                    "records. Please confirm the account details or provide an explanation.")
        return ("A vendor with the same PAN/GSTIN is already registered with us. If you are an "
                "existing vendor, please contact your procurement point of contact to update your "
                "details rather than registering again.")
    if rid == "FREE_EMAIL_DOMAIN":
        return ("Please provide a contact email on your company's own domain; personal email "
                "providers are not accepted for business vendors.")
    return None


def draft_email(form: dict, status: str, rules: list[dict], submission_id: int) -> str | None:
    """Deterministic correction / rejection email. Returns None when approved."""
    if status == "APPROVED":
        return None
    ref = f"VO-{submission_id:05d}"
    name = form.get("legal_name") or "Vendor"
    to = form.get("contact_email") or "(no email provided)"
    blocked = any(r["rule_id"] == "BLOCKLIST_MATCH" and r["status"] == "FAIL" for r in rules)

    if blocked:
        # Internal restriction lists are never disclosed to the vendor.
        subject = f"Vendor registration {ref} — unable to approve"
        body = ["We have reviewed your vendor registration and are unable to approve it under "
                "our internal onboarding policy. We are not able to share further detail."]
    else:
        primary = [r for r in rules if r["disposition"] == ("REJECT" if status == "REJECTED" else "REVIEW")]
        secondary = [r for r in rules if r["disposition"] == "REVIEW"] if status == "REJECTED" else []
        primary_items = [i for i in (_action_item(r, form) for r in primary) if i]
        secondary_items = [i for i in (_action_item(r, form) for r in secondary) if i]
        internal_only = any(r["rule_id"] == "EXTRACTION_UNAVAILABLE" for r in rules)

        if status == "REJECTED":
            subject = f"Vendor registration {ref} — unable to approve"
            body = ["Thank you for your vendor registration. We are unable to approve it for the "
                    "following reason(s):", ""]
            body += [f"  {n}. {item}" for n, item in enumerate(primary_items, 1)]
            if secondary_items:
                body += ["", "If you submit a new application, please also address:", ""]
                body += [f"  {n}. {item}" for n, item in enumerate(secondary_items, 1)]
        elif primary_items:
            subject = f"Action required: vendor registration {ref} — information needed"
            body = ["Thank you for your vendor registration. Before we can complete our review, "
                    "we need the following from you:", ""]
            body += [f"  {n}. {item}" for n, item in enumerate(primary_items, 1)]
            if internal_only:
                body += ["", "Some of your documents are also being reviewed manually by our "
                             "team; no action is needed from you for those."]
            body += ["", f"Please reply to this email quoting reference {ref} with the "
                         "requested information or documents."]
        else:
            subject = f"Vendor registration {ref} — under review"
            body = ["Thank you for your vendor registration. Your documents are being reviewed "
                    "manually by our team and no action is needed from you at this time. We will "
                    "contact you if anything further is required."]

    lines = [f"To: {to}", f"Subject: {subject}", "", f"Dear {name} team,", "", *body, "",
             "Regards,", "Vendor Onboarding Team", "",
             "— Draft generated by the onboarding demo. Not sent. —"]
    return "\n".join(lines)
