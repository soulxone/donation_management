"""Stripe webhook receiver — public endpoint, signature-verified.

URL: https://ps-church.com/api/method/donation_management.api.webhooks.stripe_webhook.handle

Configure in Stripe Dashboard → Developers → Webhooks. Events to subscribe:
  - checkout.session.completed
  - checkout.session.async_payment_succeeded
  - checkout.session.async_payment_failed
  - payment_intent.succeeded
  - payment_intent.payment_failed
  - charge.refunded
  - charge.dispute.created
  - invoice.payment_succeeded            (recurring)
  - invoice.payment_failed               (recurring)
  - customer.subscription.deleted        (recurring)
"""

import json

import frappe
from frappe.utils import flt, now_datetime

from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret
from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import (
    log_event,
    already_processed,
)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle():
    """Top-level Stripe webhook entrypoint."""
    import stripe

    payload = frappe.request.get_data(as_text=True)
    sig_header = frappe.request.headers.get("Stripe-Signature")
    secret = get_secret("stripe_webhook_secret")

    if not secret:
        frappe.log_error(title="Stripe webhook: secret not configured", message=payload[:1000])
        frappe.local.response["http_status_code"] = 500
        return {"error": "webhook secret not configured"}

    try:
        stripe.Webhook.construct_event(payload, sig_header, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        frappe.log_error(title="Stripe webhook: bad signature", message=str(e))
        log_event("Stripe", "signature.failed", external_event_id=None, raw_payload=payload[:5000], verified=False, processing_status="Error", error_message=str(e))
        frappe.local.response["http_status_code"] = 400
        return {"error": "bad signature"}

    # construct_event returns a StripeObject that doesn't expose .get() on
    # newer stripe-python — and json.dumps(stripe_obj, default=str) crashes
    # on it too. Since the payload IS already JSON and has been signature-
    # verified above, parse the raw bytes directly into a plain dict.
    event = json.loads(payload)

    if already_processed("Stripe", event["id"]):
        return {"ok": True, "duplicate": True}

    log_name = log_event(
        provider="Stripe",
        event_type=event["type"],
        external_event_id=event["id"],
        external_object_id=((event.get("data") or {}).get("object") or {}).get("id"),
        raw_payload=json.dumps(event, default=str)[:65000],
        verified=True,
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
        frappe.log_error(title=f"Stripe webhook handler error: {event['type']}", message=str(e))
        frappe.local.response["http_status_code"] = 500
        return {"error": str(e)}

    return {"ok": True}


# ---- Routing ----

def _route(event):
    t = event["type"]
    obj = event["data"]["object"]

    if t == "checkout.session.completed" or t.startswith("checkout.session.async_payment_"):
        _on_checkout_completed(obj)
    elif t == "payment_intent.succeeded":
        _on_payment_intent_succeeded(obj)
    elif t == "payment_intent.payment_failed":
        _on_payment_failed(obj)
    elif t == "charge.refunded":
        _on_charge_refunded(obj)
    elif t == "charge.dispute.created":
        _on_dispute(obj)
    elif t == "invoice.payment_succeeded":
        _on_invoice_paid(obj)
    elif t == "invoice.payment_failed":
        _on_invoice_failed(obj)
    elif t == "customer.subscription.deleted":
        _on_subscription_cancelled(obj)


def _on_checkout_completed(session):
    """Webhook payload already contains the session — no re-fetch needed.
    The session dict has all fields, but does NOT include expanded
    payment_intent / balance_transaction. We pull fee/net via the raw
    Stripe REST API (no StripeObject), since it's straightforward JSON."""
    from frappe.utils import flt
    metadata = session.get("metadata") or {}
    donation_name = metadata.get("donation") or session.get("client_reference_id")
    if not donation_name or not frappe.db.exists("Donation", donation_name):
        return
    donation = frappe.get_doc("Donation", donation_name)
    if donation.docstatus == 1 and donation.status == "Succeeded":
        return  # already booked

    payment_status = session.get("payment_status")
    if payment_status == "paid":
        # Pull charge/fee details via raw HTTP to avoid StripeObject pitfalls.
        gross = flt(session.get("amount_total", 0)) / 100.0
        fee = 0.0
        net = gross
        pm_type = None
        external_txn = session.get("payment_intent")  # default to PI id
        if isinstance(external_txn, dict):
            external_txn = external_txn.get("id")
        try:
            from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret
            import requests
            sk = get_secret("stripe_secret_key")
            if sk and external_txn:
                r = requests.get(
                    f"https://api.stripe.com/v1/payment_intents/{external_txn}",
                    auth=(sk, ""),
                    params={"expand[]": "latest_charge.balance_transaction"},
                    timeout=15,
                )
                if r.ok:
                    pi = r.json()
                    ch = pi.get("latest_charge") or {}
                    if isinstance(ch, dict):
                        bt = ch.get("balance_transaction") or {}
                        if isinstance(bt, dict):
                            fee = flt(bt.get("fee", 0)) / 100.0
                            net = flt(bt.get("net", 0)) / 100.0
                        gross = flt(ch.get("amount", gross * 100)) / 100.0
                        pm_details = ch.get("payment_method_details") or {}
                        pm_type = pm_details.get("type")
                        external_txn = ch.get("id") or external_txn
        except Exception:
            frappe.log_error(title=f"Stripe charge fetch failed for {donation_name}")

        donation.gross_amount = gross
        donation.fee_amount = fee
        donation.net_amount = net
        if pm_type:
            donation.payment_method = pm_type
        donation.external_transaction_id = external_txn
        donation.status = "Succeeded"
        donation.received_date = now_datetime()
        donation.save(ignore_permissions=True)
        if donation.docstatus == 0:
            donation.submit()
    elif session.get("subscription"):
        donation.status = "Processing"
        donation.save(ignore_permissions=True)
        if donation.recurring_donation:
            sub = session.get("subscription")
            sub_id = sub if isinstance(sub, str) else (sub or {}).get("id")
            if sub_id:
                rec = frappe.get_doc("Recurring Donation", donation.recurring_donation)
                rec.external_subscription_id = sub_id
                rec.save(ignore_permissions=True)


def _on_payment_intent_succeeded(pi):
    # If the PI didn't come from our Checkout Session (e.g. PaymentElement direct),
    # we still want to capture it. For Checkout-driven flows, the
    # checkout.session.completed event already handled it.
    donation = _find_donation_by_external(pi["id"]) or _find_donation_by_external(pi.get("client_reference_id"))
    if not donation:
        return
    if donation.status == "Succeeded":
        return
    donation.status = "Succeeded"
    donation.external_transaction_id = pi["id"]
    donation.received_date = now_datetime()
    donation.save(ignore_permissions=True)
    if donation.docstatus == 0:
        donation.submit()


def _on_payment_failed(pi):
    donation = _find_donation_by_external(pi["id"])
    if donation:
        donation.status = "Failed"
        donation.save(ignore_permissions=True)


def _on_charge_refunded(charge):
    donation = _find_donation_by_external(charge["id"]) or _find_donation_by_external(charge.get("payment_intent"))
    if not donation:
        return
    if charge.get("amount_refunded") and charge["amount_refunded"] >= charge["amount"]:
        donation.status = "Refunded"
        donation.save(ignore_permissions=True)
        if donation.docstatus == 1:
            donation.cancel()


def _on_dispute(dispute):
    donation = _find_donation_by_external(dispute.get("charge")) or _find_donation_by_external(dispute.get("payment_intent"))
    if donation:
        donation.status = "Disputed"
        donation.save(ignore_permissions=True)


def _on_invoice_paid(invoice):
    """Recurring charge succeeded — create a child Donation under the plan."""
    sub_id = invoice.get("subscription")
    if not sub_id:
        return
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub_id}, "name")
    if not rec_name:
        return
    rec = frappe.get_doc("Recurring Donation", rec_name)

    # Charge details
    charge = invoice.get("charge")
    pm_type = None
    fee_amount = 0
    net_amount = flt(invoice.get("amount_paid", 0)) / 100.0
    gross_amount = flt(invoice.get("amount_paid", 0)) / 100.0
    if charge:
        try:
            import stripe
            stripe.api_key = get_secret("stripe_secret_key")
            ch = stripe.Charge.retrieve(charge, expand=["balance_transaction"])
            pm_type = (ch.payment_method_details and ch.payment_method_details.type) or None
            bt = ch.balance_transaction
            if bt:
                fee_amount = flt(getattr(bt, "fee", 0)) / 100.0
                net_amount = flt(getattr(bt, "net", 0)) / 100.0
                gross_amount = flt(ch.amount) / 100.0
        except Exception:
            pass

    donation = frappe.new_doc("Donation")
    donation.donor = rec.donor
    donation.donation_fund = rec.donation_fund
    donation.payment_channel = rec.payment_channel
    donation.amount = gross_amount or rec.amount
    donation.gross_amount = gross_amount or rec.amount
    donation.fee_amount = fee_amount
    donation.net_amount = net_amount
    donation.currency = rec.currency or "USD"
    donation.donation_date = frappe.utils.today()
    donation.received_date = now_datetime()
    donation.status = "Succeeded"
    donation.source = "Online"
    donation.payment_method = pm_type
    donation.external_transaction_id = invoice.get("id")
    donation.recurring_donation = rec.name
    donation.is_recurring_first = 0
    donation.company = frappe.db.get_value("Donation Settings", "Donation Settings", "default_company")
    donation.insert(ignore_permissions=True)
    donation.submit()

    rec.consecutive_failures = 0
    rec.last_charge_date = frappe.utils.today()
    rec.last_charge_status = "succeeded"
    rec.charges_count = (rec.charges_count or 0) + 1
    rec.total_charged = (rec.total_charged or 0) + (gross_amount or rec.amount)
    rec.save(ignore_permissions=True)


def _on_invoice_failed(invoice):
    sub_id = invoice.get("subscription")
    if not sub_id:
        return
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub_id}, "name")
    if not rec_name:
        return
    rec = frappe.get_doc("Recurring Donation", rec_name)
    rec.consecutive_failures = (rec.consecutive_failures or 0) + 1
    rec.last_charge_status = "failed"
    if rec.consecutive_failures >= 3:
        rec.status = "Paused"
    rec.save(ignore_permissions=True)


def _on_subscription_cancelled(sub):
    rec_name = frappe.db.get_value("Recurring Donation", {"external_subscription_id": sub["id"]}, "name")
    if rec_name:
        frappe.db.set_value("Recurring Donation", rec_name, "status", "Cancelled")


def _find_donation_by_external(value):
    if not value:
        return None
    name = frappe.db.get_value("Donation", {"external_transaction_id": value}, "name")
    return frappe.get_doc("Donation", name) if name else None
