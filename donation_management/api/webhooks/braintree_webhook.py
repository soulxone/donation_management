"""Braintree webhook receiver — verifies via gateway.webhook_notification.parse."""

import frappe
from frappe.utils import flt, now_datetime, today

from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import (
    log_event,
    already_processed,
)
from donation_management.api.gateways.braintree_gateway import _gateway


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle():
    payload = frappe.local.form_dict
    signature = payload.get("bt_signature")
    body = payload.get("bt_payload")
    if not (signature and body):
        frappe.local.response["http_status_code"] = 400
        return {"error": "missing fields"}
    gw = _gateway()
    try:
        notif = gw.webhook_notification.parse(signature, body)
    except Exception as e:
        frappe.local.response["http_status_code"] = 400
        return {"error": str(e)}

    kind = getattr(notif, "kind", "unknown")
    bt_id = getattr(notif.subject, "transaction", None) and notif.subject.transaction.id
    if not bt_id:
        bt_id = getattr(notif.subject, "subscription", None) and notif.subject.subscription.id

    event_id = f"{kind}:{bt_id}:{notif.timestamp.isoformat() if notif.timestamp else ''}"
    if already_processed("Braintree", event_id):
        return {"ok": True, "duplicate": True}

    log = log_event(
        "Braintree", kind, event_id, bt_id, raw_payload=str(body)[:65000], verified=True, processing_status="Received"
    )

    try:
        if kind in ("subscription_charged_successfully",):
            _on_subscription_charged(notif.subscription)
        elif kind in ("subscription_charged_unsuccessfully",):
            _on_subscription_failed(notif.subscription)
        elif kind in ("subscription_canceled", "subscription_expired"):
            _on_subscription_cancelled(notif.subscription)
        elif kind in ("disbursement",):
            pass  # disbursement event — informational only
        frappe.db.set_value("Donation Payment Log", log, "processing_status", "Processed")
        frappe.db.commit()
    except Exception as e:
        frappe.db.rollback()
        frappe.db.set_value("Donation Payment Log", log, {"processing_status": "Error", "error_message": str(e)[:500]})
        frappe.db.commit()
        frappe.local.response["http_status_code"] = 500
        return {"error": str(e)}

    return {"ok": True}


def _on_subscription_charged(sub):
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub.id}, "name")
    if not rec_name:
        return
    rec = frappe.get_doc("Recurring Donation", rec_name)
    txns = sub.transactions or []
    if not txns:
        return
    txn = txns[0]
    gross = flt(txn.amount)
    pct = flt(frappe.db.get_value("Payment Channel", rec.payment_channel, "fee_percent")) or 0
    fixed = flt(frappe.db.get_value("Payment Channel", rec.payment_channel, "fee_fixed")) or 0
    fee = round(gross * pct / 100.0 + fixed, 2)

    d = frappe.new_doc("Donation")
    d.donor = rec.donor
    d.donation_fund = rec.donation_fund
    d.payment_channel = rec.payment_channel
    d.amount = gross
    d.gross_amount = gross
    d.fee_amount = fee
    d.net_amount = gross - fee
    d.donation_date = today()
    d.received_date = now_datetime()
    d.status = "Succeeded"
    d.payment_method = "Venmo"
    d.external_transaction_id = txn.id
    d.recurring_donation = rec.name
    d.company = frappe.db.get_value("Donation Settings", "Donation Settings", "default_company")
    d.insert(ignore_permissions=True)
    d.submit()

    rec.consecutive_failures = 0
    rec.last_charge_date = today()
    rec.last_charge_status = "succeeded"
    rec.charges_count = (rec.charges_count or 0) + 1
    rec.total_charged = (rec.total_charged or 0) + gross
    rec.save(ignore_permissions=True)


def _on_subscription_failed(sub):
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub.id}, "name")
    if not rec_name:
        return
    rec = frappe.get_doc("Recurring Donation", rec_name)
    rec.consecutive_failures = (rec.consecutive_failures or 0) + 1
    rec.last_charge_status = "failed"
    if rec.consecutive_failures >= 3:
        rec.status = "Paused"
    rec.save(ignore_permissions=True)


def _on_subscription_cancelled(sub):
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub.id}, "name")
    if rec_name:
        frappe.db.set_value("Recurring Donation", rec_name, "status", "Cancelled")
