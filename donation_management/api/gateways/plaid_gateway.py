"""Plaid Link → Stripe ACH bridge for direct-from-bank donations.

Recommended flow (cheapest/most-reliable for churches):
  1. /donate page swaps in Plaid Link if the donor picks "Direct from Bank"
  2. Donor authenticates with their bank (Plaid handles MFA)
  3. We exchange public_token -> access_token, create a Stripe bank_account_token
     via Plaid's stripe processor token, attach to the Donor's Stripe Customer
  4. Charge with Stripe ACH (us_bank_account, automatic verification done by Plaid)

This module returns a Plaid Link token; the front-end opens Plaid Link and
posts the public_token back to `complete_link`. We then create the Stripe ACH
charge via stripe_gateway.
"""

import requests
import frappe
from frappe.utils import flt, now_datetime
from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret
from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import log_event


def _api_base():
    s = frappe.get_single("Donation Settings")
    env = (s.plaid_environment or "sandbox").lower()
    return f"https://{env}.plaid.com"


def _creds():
    s = frappe.get_single("Donation Settings")
    if not s.plaid_enabled:
        frappe.throw("Plaid is not enabled.")
    return {"client_id": s.plaid_client_id, "secret": get_secret("plaid_secret")}


def create_link_token(donation):
    body = {
        **_creds(),
        "user": {"client_user_id": donation.donor},
        "client_name": "Pleasant Springs Church",
        "products": ["auth"],
        "country_codes": ["US"],
        "language": "en",
        "account_filters": {"depository": {"account_subtypes": ["checking", "savings"]}},
    }
    r = requests.post(f"{_api_base()}/link/token/create", json=body, timeout=15)
    r.raise_for_status()
    return {"mode": "plaid_link", "link_token": r.json()["link_token"], "donation": donation.name}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def complete_link(donation, public_token, account_id):
    if not frappe.db.exists("Donation", donation):
        frappe.throw("Donation not found.")

    creds = _creds()
    # 1) public_token -> access_token
    r = requests.post(f"{_api_base()}/item/public_token/exchange", json={**creds, "public_token": public_token}, timeout=15)
    r.raise_for_status()
    access_token = r.json()["access_token"]

    # 2) access_token + account_id -> stripe_bank_account_token
    r = requests.post(
        f"{_api_base()}/processor/stripe/bank_account_token/create",
        json={**creds, "access_token": access_token, "account_id": account_id},
        timeout=15,
    )
    r.raise_for_status()
    stripe_token = r.json()["stripe_bank_account_token"]

    # 3) Charge via Stripe
    import stripe
    from donation_management.api.gateways.stripe_gateway import _ensure_customer
    stripe.api_key = get_secret("stripe_secret_key")
    if not stripe.api_key:
        frappe.throw("Stripe is required to settle Plaid ACH and is not configured.")

    d = frappe.get_doc("Donation", donation)
    customer = _ensure_customer(stripe, d.donor)
    bank = stripe.Customer.create_source(customer, source=stripe_token)

    is_recurring = bool(d.recurring_donation)
    if is_recurring:
        # ACH subscription via Stripe
        rec = frappe.get_doc("Recurring Donation", d.recurring_donation)
        from donation_management.api.gateways.stripe_gateway import create_checkout
        # Re-route: easier to use Stripe's hosted Checkout for ACH subscription verification
        return create_checkout(d)

    pi = stripe.PaymentIntent.create(
        amount=int(round(flt(d.amount) * 100)),
        currency=(d.currency or "USD").lower(),
        customer=customer,
        payment_method_types=["us_bank_account"],
        payment_method=bank.id,
        confirm=True,
        metadata={"donation": d.name, "fund": d.donation_fund, "via": "plaid"},
    )

    d.external_transaction_id = pi.id
    d.payment_method = "ACH"
    d.status = "Processing"  # ACH takes days to settle; webhook will flip to Succeeded
    d.received_date = now_datetime()
    d.save(ignore_permissions=True)

    log_event("Plaid", "ach.initiated", pi.id, pi.id, donation=d.name, verified=True, processing_status="Processed")
    return {"ok": True, "donation": d.name, "redirect_url": f"/donate/thanks?ref={d.name}"}
