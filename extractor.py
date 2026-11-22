"""Document reading, content-based classification and structured extraction.

Stage A, read_document(): pure local work. Validate type/size/pages, try the
PDF text layer with PyMuPDF, otherwise render pages to images for Vision.

Stage B, extract_document(): one Gemini call per document. Digital text goes
in as text; scanned PDFs and images go in as images. The response must be
schema-valid JSON (one retry allowed). For digital PDFs every returned value is
checked against the source text, and anything not literally present is dropped,
so the model cannot invent identifiers.

No form values are ever sent to the model: the document is the only evidence.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import pymupdf
from dotenv import load_dotenv

from validator import normalize_identifier, normalize_name

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PAGES = 5
MIN_USABLE_TEXT_CHARS = 80
RENDER_DPI = 150

DOC_TYPES = ["GST_CERTIFICATE", "PAN_DOCUMENT", "BANK_PROOF", "UTILITY_BILL", "UNKNOWN"]
FIELD_NAMES = ["legal_name", "trade_name", "pan", "gstin", "registered_address",
               "account_holder_name", "bank_account_number", "ifsc"]
IDENTIFIER_FIELDS = {"pan", "gstin", "ifsc", "bank_account_number"}
NAME_FIELDS = {"legal_name", "trade_name", "account_holder_name"}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "document_type_detected": {"type": "STRING", "enum": DOC_TYPES},
        "is_legible": {"type": "BOOLEAN"},
        "classification_reason": {"type": "STRING"},
        "fields": {
            "type": "OBJECT",
            "properties": {name: {"type": "STRING", "nullable": True} for name in FIELD_NAMES},
            "required": FIELD_NAMES,
        },
    },
    "required": ["document_type_detected", "is_legible", "classification_reason", "fields"],
}

PROMPT = """You are a document-intake clerk for Indian B2B vendor onboarding.
Classify ONE document by its CONTENT (ignore file names; ignore watermarks or
demo/sample disclaimers) and extract only values that are visibly printed on it.

document_type_detected — exactly one of:
- GST_CERTIFICATE: GST registration certificate (REG-06 style) showing a 15-character
  GSTIN / registration number, legal name, principal place of business.
- PAN_DOCUMENT: PAN card or PAN information/allotment record showing a 10-character
  Permanent Account Number and the holder's name.
- BANK_PROOF: bank letter, cancelled cheque or statement confirming an account
  holder name, account number and IFSC.
- UTILITY_BILL: electricity, water, gas, telephone or similar utility bill.
- UNKNOWN: anything else, or too little content to tell.

fields — use null for anything not printed on this document. Never infer,
compute or guess. In particular never derive a PAN from a GSTIN; only report a
PAN that is printed as a PAN. Copy identifiers exactly as printed without
spaces. Copy names exactly as printed (do not expand abbreviations).
- legal_name: registered legal name of the business/holder (for a utility bill,
  null — consumer names are not legal-name evidence).
- trade_name: trade name / DBA if printed.
- pan, gstin, ifsc, bank_account_number: as printed.
- registered_address: principal place of business / registered address.
- account_holder_name: bank account holder / beneficiary name (bank documents only).

is_legible — false only if the document is too blurred, cut off or damaged to
read its key information reliably.

classification_reason — one short sentence citing the visible evidence."""


class ExtractionError(Exception):
    """Raised when the model step cannot produce a valid result."""


@dataclass
class ReadResult:
    upload_slot: str
    file_name: str
    file_bytes: int
    mime_type: str | None = None
    page_count: int = 0
    text: str = ""
    images: list[tuple[bytes, str]] = field(default_factory=list)  # (bytes, mime)
    extraction_method: str | None = None  # PDF_TEXT | VISION
    errors: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.errors:
            return "; ".join(e["message"] for e in self.errors)
        kind = "PDF" if self.mime_type == "application/pdf" else (self.mime_type or "?")
        detail = (f"text layer {len(self.text)} chars" if self.extraction_method == "PDF_TEXT"
                  else f"{len(self.images)} image(s) for Vision")
        return (f"{kind}, {self.page_count} page(s), {self.file_bytes / 1024:.0f} KB — "
                f"{detail} → {self.extraction_method}")


def empty_fields() -> dict:
    return {name: None for name in FIELD_NAMES}


def failure_object(slot: str, method: str | None, errors: list[dict],
                   legible: bool | None = False) -> dict:
    """is_legible is False for unreadable files, None (unknown) when the model step failed."""
    return {"upload_slot": slot, "document_type_detected": "UNKNOWN", "is_legible": legible,
            "extraction_method": method, "fields": empty_fields(), "errors": errors}


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


# --------------------------------------------------------------------------
# Stage A — read
# --------------------------------------------------------------------------

def read_document(slot: str, file_name: str, data: bytes) -> ReadResult:
    res = ReadResult(upload_slot=slot, file_name=file_name, file_bytes=len(data))
    ext = os.path.splitext(file_name.lower())[1]
    if not data:
        res.errors.append({"code": "EMPTY_FILE", "message": "The file is empty."})
        return res
    if len(data) > MAX_FILE_BYTES:
        res.errors.append({"code": "FILE_TOO_LARGE",
                           "message": f"File is {len(data) / 1e6:.1f} MB; limit is 10 MB."})
        return res
    mime = _sniff(data)
    allowed = {".pdf": "application/pdf", ".png": "image/png",
               ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    if mime is None or allowed.get(ext) != mime:
        res.errors.append({"code": "UNSUPPORTED_FILE_TYPE",
                           "message": "File content is not a valid PDF, PNG or JPEG "
                                      "(or does not match its extension)."})
        return res
    res.mime_type = mime

    try:
        doc = pymupdf.open(stream=data, filetype="pdf" if mime == "application/pdf" else ext[1:])
    except Exception as exc:  # corrupt or truncated file
        res.errors.append({"code": "CORRUPT_FILE", "message": f"File could not be opened: {exc}"})
        return res

    with doc:
        if mime != "application/pdf":
            res.page_count = 1
            res.images = [(data, mime)]
            res.extraction_method = "VISION"
            return res
        if doc.needs_pass:
            res.errors.append({"code": "CORRUPT_FILE", "message": "PDF is password-protected."})
            return res
        res.page_count = doc.page_count
        if doc.page_count == 0:
            res.errors.append({"code": "CORRUPT_FILE", "message": "PDF has no pages."})
            return res
        if doc.page_count > MAX_PAGES:
            res.errors.append({"code": "TOO_MANY_PAGES",
                               "message": f"PDF has {doc.page_count} pages; limit is {MAX_PAGES}."})
            return res
        try:
            text = "\n".join(page.get_text("text") for page in doc).strip()
            if len(re.sub(r"\s", "", text)) >= MIN_USABLE_TEXT_CHARS:
                res.text = text
                res.extraction_method = "PDF_TEXT"
            else:
                res.images = [(page.get_pixmap(dpi=RENDER_DPI).tobytes("png"), "image/png")
                              for page in doc]
                res.extraction_method = "VISION"
        except Exception as exc:
            res.errors.append({"code": "CORRUPT_FILE", "message": f"PDF could not be parsed: {exc}"})
    return res


# --------------------------------------------------------------------------
# Stage B — classify and extract with Gemini
# --------------------------------------------------------------------------

def _validate_payload(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    if payload.get("document_type_detected") not in DOC_TYPES:
        raise ValueError(f"invalid document_type_detected: {payload.get('document_type_detected')!r}")
    if not isinstance(payload.get("is_legible"), bool):
        raise ValueError("is_legible must be boolean")
    fields = payload.get("fields")
    if not isinstance(fields, dict) or set(fields) != set(FIELD_NAMES):
        raise ValueError("fields must contain exactly the expected keys")
    for key, value in fields.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(f"field {key} must be string or null")
    return payload


def _call_gemini(read: ReadResult, log) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ExtractionError("GEMINI_API_KEY is not configured (set it in .env, or in the app's Secrets when hosted).")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ExtractionError("google-genai is not installed (pip install -r requirements.txt).") from exc

    if read.extraction_method == "PDF_TEXT":
        parts = [PROMPT, "Document text (extracted from the PDF text layer):\n<<<\n"
                 + read.text[:30_000] + "\n>>>"]
    else:
        parts = [PROMPT, "Document page image(s) follow."]
        parts += [types.Part.from_bytes(data=img, mime_type=mime) for img, mime in read.images]

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000))
    config = types.GenerateContentConfig(response_mime_type="application/json",
                                         response_schema=RESPONSE_SCHEMA)
    last_error = None
    for attempt in (1, 2):  # one retry for malformed output or a transient API error
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=parts,
                                                      config=config)
            return _validate_payload(json.loads(response.text or ""))
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = f"malformed model output: {exc}"
        except Exception as exc:  # network / quota / auth errors from the SDK
            last_error = f"{type(exc).__name__}: {exc}"
            if getattr(exc, "code", None) in (400, 401, 403, 404):
                break  # bad key or unknown model: retrying cannot help
        if attempt == 1:
            log("WARN", f"Gemini attempt 1 failed ({last_error[:200]}); retrying once.")
    raise ExtractionError(f"Gemini extraction failed — {last_error[:300]}")


def _flat(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def _ground(fields: dict, source_text: str) -> tuple[dict, list[dict]]:
    """Drop any value not literally present in the PDF text layer."""
    flat_text = _flat(source_text)
    name_text = f" {normalize_name(source_text)} "
    loose_text = " ".join(re.sub(r"[^0-9a-z]+", " ", source_text.lower()).split())
    grounded, errors = {}, []
    for key, value in fields.items():
        if value is None:
            grounded[key] = None
            continue
        if key in IDENTIFIER_FIELDS:
            ok = bool(_flat(value)) and _flat(value) in flat_text
        elif key in NAME_FIELDS:
            ok = f" {normalize_name(value)} " in name_text
        else:  # address: allow reflowed punctuation/whitespace
            ok = " ".join(re.sub(r"[^0-9a-z]+", " ", value.lower()).split()) in loose_text
        grounded[key] = value if ok else None
        if not ok:
            errors.append({"code": "FIELD_NOT_IN_TEXT",
                           "message": f"Dropped {key}='{value}': not found in the PDF text."})
    return grounded, errors


def _tidy(fields: dict) -> dict:
    out = {}
    for key in FIELD_NAMES:
        value = fields.get(key)
        value = " ".join(value.split()) if isinstance(value, str) else None
        if value and key in IDENTIFIER_FIELDS:
            value = normalize_identifier(value) if key != "bank_account_number" \
                else re.sub(r"[\s\-]", "", value)
        out[key] = value or None
    return out


def extract_document(read: ReadResult, log=lambda level, msg: None) -> tuple[dict, str | None]:
    """Return (extracted object, classification reason)."""
    if not read.ok:
        return failure_object(read.upload_slot, read.extraction_method, read.errors), None

    try:
        payload = _call_gemini(read, log)
    except ExtractionError as exc:
        return failure_object(read.upload_slot, read.extraction_method,
                              [{"code": "EXTRACTION_UNAVAILABLE", "message": str(exc)}],
                              legible=None), None

    fields = _tidy(payload["fields"])
    errors: list[dict] = []
    if read.extraction_method == "PDF_TEXT":
        fields, errors = _ground(fields, read.text)
    if not payload["is_legible"]:
        errors.append({"code": "NOT_LEGIBLE", "message": "Model reported the document as not legible."})
    return ({"upload_slot": read.upload_slot,
             "document_type_detected": payload["document_type_detected"],
             "is_legible": payload["is_legible"],
             "extraction_method": read.extraction_method,
             "fields": fields,
             "errors": errors},
            payload.get("classification_reason"))
