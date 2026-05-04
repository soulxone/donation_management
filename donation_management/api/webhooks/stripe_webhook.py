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
    """Handle a completed Checkout Session.

    Two flows are supported:
      A) Internal /donate flow — Donation doc was pre-created and its name
         passed via metadata.donation or client_reference_id. We update it.
      B) Stripe Payment Link flow (e.g. /give → buy.stripe.com/...) — no
         Donation exists yet. We create one from session.customer_details
         using the default Fund/Company/Currency from Donation Settings.

    The session dict contains all needed fields, but does NOT include expanded
    payment_intent / balance_transaction. We pull fee/net via the raw Stripe
    REST API (no StripeObject), since it's straightforward JSON.
    """
    metadata = session.get("metadata") or {}
    donation_name = metadata.get("donation") or session.get("client_reference_id")

    if donation_name and frappe.db.exists("Donation", donation_name):
        _book_existing_donation(donation_name, session)
    elif session.get("payment_status") == "paid":
        _book_new_donation_from_session(session)


def _fetch_stripe_charge_details(payment_intent_id):
    """Pull fee/net/payment_method/charge_id for a payment intent.
    Returns dict with keys: gross, fee, net, pm_type, external_txn (charge id).
    Falls back to safe defaults if the call fails."""
    from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret
    import requests
    out = {"gross": None, "fee": 0.0, "net": None, "pm_type": None, "external_txn": payment_intent_id}
    if not payment_intent_id:
        return out
    try:
        sk = get_secret("stripe_secret_key")
        if not sk:
            return out
        r = requests.get(
            f"https://api.stripe.com/v1/payment_intents/{payment_intent_id}",
            auth=(sk, ""),
            params={"expand[]": "latest_charge.balance_transaction"},
            timeout=15,
        )
        if not r.ok:
            return out
        pi = r.json()
        ch = pi.get("latest_charge") or {}
        if not isinstance(ch, dict):
            return out
        bt = ch.get("balance_transaction") or {}
        if isinstance(bt, dict):
            out["fee"] = flt(bt.get("fee", 0)) / 100.0
            out["net"] = flt(bt.get("net", 0)) / 100.0
        if ch.get("amount") is not None:
            out["gross"] = flt(ch.get("amount")) / 100.0
        pm_details = ch.get("payment_method_details") or {}
        out["pm_type"] = pm_details.get("type")
        if ch.get("id"):
            out["external_txn"] = ch.get("id")
    except Exception:
        frappe.log_error(title=f"Stripe charge fetch failed for PI {payment_intent_id}")
    return out


def _book_existing_donation(donation_name, session):
    """Mark a pre-created Donation as Succeeded with fee details (Flow A)."""
    donation = frappe.get_doc("Donation", donation_name)
    if donation.docstatus == 1 and donation.status == "Succeeded":
        return  # already booked

    payment_status = session.get("payment_status")
    if payment_status == "paid":
        gross = flt(session.get("amount_total", 0)) / 100.0
        external_txn = session.get("payment_intent")
        if isinstance(external_txn, dict):
            external_txn = external_txn.get("id")
        details = _fetch_stripe_charge_details(external_txn)

        donation.gross_amount = details["gross"] if details["gross"] is not None else gross
        donation.fee_amount = details["fee"]
        donation.net_amount = details["net"] if details["net"] is not None else gross
        if details["pm_type"]:
            donation.payment_method = details["pm_type"]
        donation.external_transaction_id = details["external_txn"]
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


def _book_new_donation_from_session(session):
    """Create + submit a new Donation from a Stripe Checkout Session that has
    no pre-existing Donation reference (Flow B — Stripe Payment Links / /give).

    Donor is found-or-created by email from session.customer_details.
    Defaults pulled from Donation Settings (default_fund / default_company /
    default_currency)."""
    # Idempotency — skip if we've already booked this session
    session_id = session.get("id")
    if session_id and frappe.db.exists("Donation", {"external_session_id": session_id}):
        return

    customer = session.get("customer_details") or {}
    email = (customer.get("email") or "").strip().lower()
    name = (customer.get("name") or "").strip()
    if not name and email:
        name = email.split("@")[0]
    if not name:
        name = "Anonymous Donor"

    # Find-or-create Donor by email (or create unlinked if no email)
    donor_name = None
    if email:
        donor_name = frappe.db.get_value("Donor", {"email": email}, "name")
    if not donor_name:
        donor = frappe.new_doc("Donor")
        donor.donor_name = name
        donor.donor_type = "Individual"
        if email:
            donor.email = email
        if customer.get("phone"):
            donor.phone = customer.get("phone")
        addr = (customer.get("address") or {})
        if addr.get("line1"): donor.address_line_1 = addr.get("line1")
        if addr.get("line2"): donor.address_line_2 = addr.get("line2")
        if addr.get("city"):  donor.city = addr.get("city")
        if addr.get("state"): donor.state = addr.get("state")
        if addr.get("postal_code"): donor.postal_code = addr.get("postal_code")
        if session.get("customer"):
            donor.stripe_customer_id = session.get("customer")
        donor.insert(ignore_permissions=True)
        donor_name = donor.name

    # Pull fee/net from Stripe before creating the Donation
    external_txn = session.get("payment_intent")
    if isinstance(external_txn, dict):
        external_txn = external_txn.get("id")
    details = _fetch_stripe_charge_details(external_txn)
    gross_session = flt(session.get("amount_total", 0)) / 100.0
    gross = details["gross"] if details["gross"] is not None else gross_session
    net = details["net"] if details["net"] is not None else gross

    # Defaults from Donation Settings
    settings = frappe.get_single("Donation Settings")
    default_fund = settings.get("default_fund") or "General Fund"
    default_company = settings.get("default_company") or frappe.db.get_single_value("Global Defaults", "default_company")
    default_currency = (session.get("currency") or settings.get("default_currency") or "USD").upper()

    donation = frappe.new_doc("Donation")
    donation.donor = donor_name
    donation.donation_date = frappe.utils.today()
    donation.amount = gross
    donation.currency = default_currency
    donation.donation_fund = default_fund
    donation.payment_channel = "Stripe"
    donation.company = default_company
    donation.gross_amount = gross
    donation.fee_amount = details["fee"]
    donation.net_amount = net
    if details["pm_type"]:
        donation.payment_method = details["pm_type"]
    donation.external_transaction_id = details["external_txn"]
    # Best-effort: stash session id if the field exists on the doctype
    if hasattr(donation, "external_session_id"):
        donation.external_session_id = session_id
    donation.status = "Succeeded"
    donation.received_date = now_datetime()
    donation.source = "Online"
    donation.is_recurring_first = 0
    donation.insert(ignore_permissions=True)
    donation.submit()


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
