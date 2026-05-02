"""PayPal webhook receiver — public endpoint, signature verified via PayPal's
verify-webhook-signature endpoint.

URL: https://ps-church.com/api/method/donation_management.api.webhooks.paypal_webhook.handle

Configure in PayPal Dashboard → Webhooks. Subscribe to:
  PAYMENT.CAPTURE.COMPLETED
  PAYMENT.CAPTURE.REFUNDED
  PAYMENT.CAPTURE.DENIED
  BILLING.SUBSCRIPTION.ACTIVATED
  BILLING.SUBSCRIPTION.CANCELLED
  BILLING.SUBSCRIPTION.SUSPENDED
  PAYMENT.SALE.COMPLETED          (recurring charges)
  PAYMENT.SALE.REFUNDED
"""

import json
import requests
import frappe
from frappe.utils import flt, now_datetime, today

from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import (
    log_event,
    already_processed,
)
from donation_management.api.gateways.paypal_gateway import _base_url, _access_token, _headers


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle():
    payload = frappe.request.get_data(as_text=True)
    headers = dict(frappe.request.headers)

    s = frappe.get_single("Donation Settings")
    webhook_id = getattr(s, "paypal_webhook_id", None)

    verified = False
    if webhook_id:
        verified = _verify_signature(headers, payload, webhook_id)

    try:
        event = json.loads(payload)
    except ValueError:
        frappe.local.response["http_status_code"] = 400
        return {"error": "bad json"}

    event_id = event.get("id")
    if event_id and already_processed("PayPal", event_id):
        return {"ok": True, "duplicate": True}

    log_name = log_event(
        provider="PayPal",
        event_type=event.get("event_type"),
        external_event_id=event_id,
        external_object_id=(event.get("resource") or {}).get("id"),
        raw_payload=payload[:65000],
        verified=verified,
        processing_status="Received",
    )

    try:
        _route(event)
        frappe.db.set_value("Donation Payment Log", log_name, "processing_status", "Processed")
        frappe.db.commit()
    except Exception as e:
        frappe.db.rollback()
        frappe.db.set_value("Donation Payment Log", log_name, {"processing_status": "Error", "error_message": str(e)[:500]})
        frappe.db.commit()
        frappe.log_error(title=f"PayPal webhook handler error: {event.get('event_type')}", message=str(e))
        frappe.local.response["http_status_code"] = 500
        return {"error": str(e)}

    return {"ok": True}


def _verify_signature(headers, payload, webhook_id):
    body = {
        "auth_algo": headers.get("Paypal-Auth-Algo"),
        "cert_url": headers.get("Paypal-Cert-Url"),
        "transmission_id": headers.get("Paypal-Transmission-Id"),
        "transmission_sig": headers.get("Paypal-Transmission-Sig"),
        "transmission_time": headers.get("Paypal-Transmission-Time"),
        "webhook_id": webhook_id,
        "webhook_event": json.loads(payload),
    }
    try:
        r = requests.post(
            f"{_base_url()}/v1/notifications/verify-webhook-signature",
            headers=_headers(_access_token()),
            data=json.dumps(body),
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("verification_status") == "SUCCESS"
    except Exception:
        return False


def _route(event):
    t = event.get("event_type")
    res = event.get("resource") or {}

    if t == "PAYMENT.CAPTURE.COMPLETED":
        _on_capture_completed(res)
    elif t in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.SALE.REFUNDED"):
        _on_refunded(res)
    elif t == "PAYMENT.CAPTURE.DENIED":
        _on_capture_denied(res)
    elif t == "PAYMENT.SALE.COMPLETED":
        _on_recurring_sale(res)
    elif t == "BILLING.SUBSCRIPTION.ACTIVATED":
        _on_sub_activated(res)
    elif t in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.SUSPENDED"):
        _on_sub_cancelled(res)


def _on_capture_completed(res):
    name = res.get("custom_id") or res.get("invoice_id")
    if not name or not frappe.db.exists("Donation", name):
        return
    donation = frappe.get_doc("Donation", name)
    if donation.status == "Succeeded":
        return
    gross = flt(res.get("amount", {}).get("value"))
    fee = flt(res.get("seller_receivable_breakdown", {}).get("paypal_fee", {}).get("value") or 0)
    net = flt(res.get("seller_receivable_breakdown", {}).get("net_amount", {}).get("value") or (gross - fee))
    donation.gross_amount = gross
    donation.fee_amount = fee
    donation.net_amount = net
    donation.external_transaction_id = res.get("id")
    donation.status = "Succeeded"
    donation.received_date = now_datetime()
    donation.save(ignore_permissions=True)
    if donation.docstatus == 0:
        donation.submit()


def _on_refunded(res):
    pid = res.get("id")
    name = frappe.db.get_value("Donation", {"external_transaction_id": pid}, "name")
    if not name:
        # Refund references the original capture/sale via links
        for link in (res.get("links") or []):
            if link.get("rel") == "up":
                ref_id = link["href"].rstrip("/").split("/")[-1]
                name = frappe.db.get_value("Donation", {"external_transaction_id": ref_id}, "name")
                if name:
                    break
    if not name:
        return
    donation = frappe.get_doc("Donation", name)
    donation.status = "Refunded"
    donation.save(ignore_permissions=True)
    if donation.docstatus == 1:
        donation.cancel()


def _on_capture_denied(res):
    name = res.get("custom_id") or res.get("invoice_id")
    if name and frappe.db.exists("Donation", name):
        frappe.db.set_value("Donation", name, "status", "Failed")


def _on_sub_activated(res):
    sub_id = res.get("id")
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub_id}, "name")
    if rec_name:
        frappe.db.set_value("Recurring Donation", rec_name, "status", "Active")


def _on_sub_cancelled(res):
    sub_id = res.get("id")
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub_id}, "name")
    if rec_name:
        frappe.db.set_value("Recurring Donation", rec_name, "status", "Cancelled")


def _on_recurring_sale(res):
    """A scheduled recurring charge succeeded — create a new Donation under the plan."""
    sub_id = res.get("billing_agreement_id")
    if not sub_id:
        return
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub_id}, "name")
    if not rec_name:
        return
    rec = frappe.get_doc("Recurring Donation", rec_name)
    gross = flt((res.get("amount") or {}).get("total"))
    fee = flt(((res.get("transaction_fee") or {}).get("value") or 0))
    net = gross - fee

    donation = frappe.new_doc("Donation")
    donation.donor = rec.donor
    donation.donation_fund = rec.donation_fund
    donation.payment_channel = rec.payment_channel
    donation.amount = gross or rec.amount
    donation.gross_amount = gross or rec.amount
    donation.fee_amount = fee
    donation.net_amount = net or (gross or rec.amount)
    donation.donation_date = today()
    donation.received_date = now_datetime()
    donation.status = "Succeeded"
    donation.payment_method = "PayPal"
    donation.external_transaction_id = res.get("id")
    donation.recurring_donation = rec.name
    donation.company = frappe.db.get_value("Donation Settings", "Donation Settings", "default_company")
    donation.insert(ignore_permissions=True)
    donation.submit()

    rec.consecutive_failures = 0
    rec.last_charge_date = today()
    rec.last_charge_status = "succeeded"
    rec.charges_count = (rec.charges_count or 0) + 1
    rec.total_charged = (rec.total_charged or 0) + (gross or rec.amount)
    rec.save(ignore_permissions=True)
