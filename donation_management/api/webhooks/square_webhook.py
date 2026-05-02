"""Square webhook receiver — HMAC-SHA1 signature in X-Square-HmacSha256-Signature.

Subscribe to:
  payment.created
  payment.updated
  refund.created
"""
import base64
import hashlib
import hmac
import json

import frappe
from frappe.utils import flt, now_datetime, get_url

from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret
from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import (
    log_event,
    already_processed,
)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle():
    raw = frappe.request.get_data(as_text=True)
    sig = frappe.request.headers.get("X-Square-HmacSha256-Signature")
    secret = get_secret("square_webhook_signature_key") or ""
    notification_url = get_url(frappe.request.path)
    expected = base64.b64encode(
        hmac.new(secret.encode(), (notification_url + raw).encode(), hashlib.sha256).digest()
    ).decode()
    verified = hmac.compare_digest(sig or "", expected)
    if not verified:
        frappe.local.response["http_status_code"] = 400
        return {"error": "bad signature"}

    try:
        event = json.loads(raw)
    except ValueError:
        frappe.local.response["http_status_code"] = 400
        return {"error": "bad json"}

    event_id = event.get("event_id") or event.get("id")
    if event_id and already_processed("Square", event_id):
        return {"ok": True, "duplicate": True}
    log = log_event("Square", event.get("type"), event_id, ((event.get("data") or {}).get("id")), raw_payload=raw[:65000], verified=True, processing_status="Received")

    try:
        _route(event)
        frappe.db.set_value("Donation Payment Log", log, "processing_status", "Processed")
        frappe.db.commit()
    except Exception as e:
        frappe.db.rollback()
        frappe.db.set_value("Donation Payment Log", log, {"processing_status": "Error", "error_message": str(e)[:500]})
        frappe.db.commit()
        frappe.local.response["http_status_code"] = 500
        return {"error": str(e)}
    return {"ok": True}


def _route(event):
    t = event.get("type")
    obj = ((event.get("data") or {}).get("object") or {}).get("payment") or {}
    if t in ("payment.created", "payment.updated"):
        if obj.get("status") == "COMPLETED":
            _on_payment_completed(obj)
        elif obj.get("status") == "FAILED":
            _on_payment_failed(obj)
    elif t == "refund.created":
        _on_refund(obj)


def _on_payment_completed(payment):
    note = (payment.get("note") or "").strip()
    name = note if note and frappe.db.exists("Donation", note) else None
    if not name:
        return
    d = frappe.get_doc("Donation", name)
    if d.status == "Succeeded":
        return
    gross = flt(payment.get("amount_money", {}).get("amount", 0)) / 100.0
    fee_total = sum(flt(f.get("amount_money", {}).get("amount", 0)) for f in (payment.get("processing_fee") or [])) / 100.0
    d.gross_amount = gross
    d.fee_amount = fee_total
    d.net_amount = gross - fee_total
    d.payment_method = (payment.get("source_type") or "Square").title()
    d.external_transaction_id = payment.get("id")
    d.status = "Succeeded"
    d.received_date = now_datetime()
    d.save(ignore_permissions=True)
    if d.docstatus == 0:
        d.submit()


def _on_payment_failed(payment):
    note = (payment.get("note") or "").strip()
    if note and frappe.db.exists("Donation", note):
        frappe.db.set_value("Donation", note, "status", "Failed")


def _on_refund(refund):
    pid = refund.get("payment_id")
    name = frappe.db.get_value("Donation", {"external_transaction_id": pid}, "name")
    if not name:
        return
    d = frappe.get_doc("Donation", name)
    d.status = "Refunded"
    d.save(ignore_permissions=True)
    if d.docstatus == 1:
        d.cancel()
