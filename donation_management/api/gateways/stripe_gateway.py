"""Stripe gateway: Checkout sessions for one-time + Subscriptions for recurring.

Supports:
  - Cards (Visa, MC, Amex, Discover)
  - Apple Pay / Google Pay (auto-detected by Stripe Checkout)
  - Cash App Pay (US)
  - US Bank Account (ACH debit)
  - Link

Webhook handler lives in donation_management.api.webhooks.stripe_webhook.
"""

import json

import frappe
from frappe.utils import flt, get_url

from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret
from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import log_event


def _client():
    """Lazy import — stripe is only required when a Stripe channel is enabled."""
    import stripe
    settings = frappe.get_single("Donation Settings")
    if not settings.stripe_enabled:
        frappe.throw("Stripe is not enabled.")
    secret = get_secret("stripe_secret_key")
    if not secret:
        frappe.throw("Stripe secret key is not set in Donation Settings.")
    stripe.api_key = secret
    stripe.api_version = "2024-09-30.acacia"
    return stripe


def _site_url(path):
    return get_url(path)


def _ensure_customer(stripe, donor_name):
    """Find-or-create a Stripe Customer keyed to the Donor doctype."""
    donor = frappe.get_doc("Donor", donor_name)
    if donor.stripe_customer_id:
        return donor.stripe_customer_id
    cust = stripe.Customer.create(
        email=donor.email,
        name=donor.donor_name,
        phone=donor.phone,
        metadata={"frappe_donor": donor.name},
    )
    donor.db_set("stripe_customer_id", cust.id, update_modified=False)
    return cust.id


def _payment_methods(channel_name, frequency_recurring):
    """Map our Payment Channel -> Stripe payment_method_types."""
    name = (channel_name or "").lower()
    if "ach" in name:
        return ["us_bank_account"]
    if frequency_recurring:
        return ["card", "us_bank_account", "link"]
    return ["card", "us_bank_account", "cashapp", "link"]


def create_checkout(donation):
    """Build a Checkout Session for a Pending Donation.
    Returns {redirect_url} so the donor browser is sent to Stripe."""
    stripe = _client()
    settings = frappe.get_single("Donation Settings")

    customer_id = _ensure_customer(stripe, donation.donor)

    is_recurring = bool(donation.recurring_donation)
    pm_types = _payment_methods(donation.payment_channel, is_recurring)

    line_item = {
        "price_data": {
            "currency": (donation.currency or "USD").lower(),
            "unit_amount": int(round(flt(donation.amount) * 100)),
            "product_data": {
                "name": f"Donation — {donation.donation_fund}",
                "metadata": {"donation_fund": donation.donation_fund},
            },
        },
        "quantity": 1,
    }

    kwargs = dict(
        mode="subscription" if is_recurring else "payment",
        customer=customer_id,
        payment_method_types=pm_types,
        line_items=[line_item],
        success_url=_site_url(f"/donate/thanks?ref={donation.name}"),
        cancel_url=_site_url("/donate"),
        client_reference_id=donation.name,
        metadata={
            "donation": donation.name,
            "donor": donation.donor,
            "fund": donation.donation_fund,
            "recurring": "1" if is_recurring else "0",
        },
    )

    if is_recurring:
        # Convert price_data.unit_amount into a recurring price on the fly
        rec = frappe.get_doc("Recurring Donation", donation.recurring_donation)
        interval_map = {
            "Weekly": ("week", 1),
            "Bi-Weekly": ("week", 2),
            "Monthly": ("month", 1),
            "Quarterly": ("month", 3),
            "Annually": ("year", 1),
        }
        interval, count = interval_map.get(rec.frequency, ("month", 1))
        line_item["price_data"]["recurring"] = {"interval": interval, "interval_count": count}
    else:
        # One-off: capture donor address for receipts
        kwargs["billing_address_collection"] = "auto"

    session = stripe.checkout.Session.create(**kwargs)

    log_event(
        provider="Stripe",
        event_type="checkout.session.created",
        external_event_id=session.id,
        external_object_id=session.id,
        donation=donation.name,
        verified=True,
        raw_payload=json.dumps({"session_id": session.id, "url": session.url}),
        processing_status="Processed",
    )

    donation.db_set("external_transaction_id", session.id, update_modified=False)
    return {"redirect_url": session.url, "session_id": session.id}


def refresh_donation_from_session(session_id):
    """Pull a Checkout Session by ID and reconcile its state to our Donation.
    Used by the webhook handler and by an admin 'Refresh' action."""
    stripe = _client()
    session = stripe.checkout.Session.retrieve(
        session_id, expand=["payment_intent", "subscription", "subscription.latest_invoice.charge"]
    )
    donation_name = (session.metadata or {}).get("donation") or session.client_reference_id
    if not donation_name or not frappe.db.exists("Donation", donation_name):
        return None
    donation = frappe.get_doc("Donation", donation_name)

    if session.payment_status == "paid":
        # One-time
        pi = session.payment_intent
        charge = (pi and pi.latest_charge) or None
        if not isinstance(charge, str) and charge:
            charge_id = charge.id
        else:
            charge_id = charge
        if charge_id:
            ch = stripe.Charge.retrieve(charge_id, expand=["balance_transaction"])
            bt = ch.balance_transaction
            fee = flt(getattr(bt, "fee", 0)) / 100.0
            net = flt(getattr(bt, "net", 0)) / 100.0
            donation.fee_amount = fee
            donation.net_amount = net
            donation.gross_amount = flt(ch.amount) / 100.0
            donation.payment_method = (ch.payment_method_details and ch.payment_method_details.type) or None
            donation.external_transaction_id = ch.id
        donation.status = "Succeeded"
        donation.received_date = frappe.utils.now_datetime()
        donation.save(ignore_permissions=True)
        if donation.docstatus == 0:
            donation.submit()
    elif session.subscription:
        # Subscription mode — first charge handled by invoice webhook;
        # mark donation as Processing for now.
        donation.status = "Processing"
        donation.save(ignore_permissions=True)
        if donation.recurring_donation:
            rec = frappe.get_doc("Recurring Donation", donation.recurring_donation)
            rec.external_subscription_id = session.subscription if isinstance(session.subscription, str) else session.subscription.id
            rec.save(ignore_permissions=True)

    return donation.name
