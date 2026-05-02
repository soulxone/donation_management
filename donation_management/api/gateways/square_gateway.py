"""Square gateway — Cash App Pay (Square-hosted) + Square Checkout for cards.

Uses Square's "Checkout API" (Quick Pay) for hosted payment links. Cash App Pay
appears alongside cards in the Square checkout when enabled on the merchant.
"""

import requests
import frappe
from frappe.utils import flt, get_url
from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret
from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import log_event


def _api_base():
    s = frappe.get_single("Donation Settings")
    return "https://connect.squareupsandbox.com" if s.square_application_id and "sandbox" in s.square_application_id.lower() else "https://connect.squareup.com"


def _headers():
    token = get_secret("square_access_token")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Square-Version": "2024-12-18",
    }


def create_payment_link(donation):
    s = frappe.get_single("Donation Settings")
    if not s.square_enabled:
        frappe.throw("Square is not enabled.")

    body = {
        "idempotency_key": donation.name,
        "quick_pay": {
            "name": f"Donation — {donation.donation_fund}",
            "price_money": {
                "amount": int(round(flt(donation.amount) * 100)),
                "currency": donation.currency or "USD",
            },
            "location_id": s.square_location_id,
        },
        "checkout_options": {
            "redirect_url": get_url(f"/donate/thanks?ref={donation.name}"),
            "ask_for_shipping_address": False,
            "accepted_payment_methods": {
                "apple_pay": True,
                "google_pay": True,
                "cash_app_pay": True,
                "afterpay_clearpay": False,
            },
        },
        "pre_populated_data": {
            "buyer_email": frappe.db.get_value("Donor", donation.donor, "email") or None,
        },
        "payment_note": donation.name,
    }
    r = requests.post(f"{_api_base()}/v2/online-checkout/payment-links", headers=_headers(), json=body, timeout=20)
    r.raise_for_status()
    data = r.json()
    link = data["payment_link"]
    donation.db_set("external_transaction_id", link["id"], update_modified=False)
    log_event("Square", "payment_link.created", link["id"], link["id"], donation=donation.name, verified=True, processing_status="Processed")
    return {"redirect_url": link["url"], "payment_link_id": link["id"]}
