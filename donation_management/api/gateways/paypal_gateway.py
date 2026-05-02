"""PayPal Orders v2 + Subscriptions — REST API.

Flow (one-time):
  start_donation -> create_order returns approval URL -> donor approves on paypal.com
  -> donor returns to /donate/paypal-return?token=ORDER_ID -> capture_order finalizes
  -> webhook PAYMENT.CAPTURE.COMPLETED books the donation

Flow (recurring): a Plan + Subscription is created; webhooks
BILLING.SUBSCRIPTION.ACTIVATED + PAYMENT.SALE.COMPLETED drive Donation creation.
"""

import json

import requests
import frappe
from frappe.utils import flt, get_url

from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret


def _base_url():
    s = frappe.get_single("Donation Settings")
    return "https://api-m.sandbox.paypal.com" if (s.paypal_mode or "sandbox") == "sandbox" else "https://api-m.paypal.com"


def _access_token():
    s = frappe.get_single("Donation Settings")
    if not s.paypal_enabled:
        frappe.throw("PayPal is not enabled.")
    client_id = s.paypal_client_id
    client_secret = get_secret("paypal_client_secret")
    if not (client_id and client_secret):
        frappe.throw("PayPal credentials are not set.")
    r = requests.post(
        f"{_base_url()}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _headers(token=None):
    return {
        "Authorization": f"Bearer {token or _access_token()}",
        "Content-Type": "application/json",
    }


def create_order(donation):
    """One-time PayPal Order (recurring uses create_subscription)."""
    if donation.recurring_donation:
        return create_subscription(donation)

    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": (donation.currency or "USD"),
                "value": f"{flt(donation.amount):.2f}",
            },
            "description": f"Donation — {donation.donation_fund}",
            "custom_id": donation.name,
            "invoice_id": donation.name,
        }],
        "application_context": {
            "brand_name": "Pleasant Springs Church",
            "user_action": "PAY_NOW",
            "return_url": get_url(f"/api/method/donation_management.api.gateways.paypal_gateway.return_url?donation={donation.name}"),
            "cancel_url": get_url("/donate"),
        },
    }
    r = requests.post(f"{_base_url()}/v2/checkout/orders", headers=_headers(), data=json.dumps(body), timeout=20)
    r.raise_for_status()
    data = r.json()
    approve = next((l["href"] for l in data.get("links", []) if l["rel"] == "approve"), None)
    donation.db_set("external_transaction_id", data["id"], update_modified=False)
    return {"redirect_url": approve, "order_id": data["id"]}


@frappe.whitelist(allow_guest=True)
def return_url(donation, token=None, PayerID=None):
    """PayPal redirects here after approval. We capture and redirect to /thanks."""
    name = donation
    if not name or not frappe.db.exists("Donation", name):
        frappe.local.response["http_status_code"] = 404
        return "Not found"
    capture_order(name)
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"/donate/thanks?ref={name}"


def capture_order(donation_name):
    donation = frappe.get_doc("Donation", donation_name)
    order_id = donation.external_transaction_id
    if not order_id:
        return
    r = requests.post(f"{_base_url()}/v2/checkout/orders/{order_id}/capture", headers=_headers(), timeout=20)
    r.raise_for_status()
    data = r.json()
    cap = data["purchase_units"][0]["payments"]["captures"][0]
    gross = flt(cap["amount"]["value"])
    fee = flt(cap.get("seller_receivable_breakdown", {}).get("paypal_fee", {}).get("value") or 0)
    net = flt(cap.get("seller_receivable_breakdown", {}).get("net_amount", {}).get("value") or (gross - fee))

    donation.gross_amount = gross
    donation.fee_amount = fee
    donation.net_amount = net
    donation.external_transaction_id = cap["id"]
    donation.payment_method = "PayPal"
    donation.status = "Succeeded"
    donation.received_date = frappe.utils.now_datetime()
    donation.save(ignore_permissions=True)
    if donation.docstatus == 0:
        donation.submit()
    return cap["id"]


def create_subscription(donation):
    """Recurring via PayPal Subscriptions. Creates a Plan on demand."""
    rec = frappe.get_doc("Recurring Donation", donation.recurring_donation)
    plan_id = _ensure_plan(donation, rec)

    body = {
        "plan_id": plan_id,
        "custom_id": donation.name,
        "subscriber": {"name": {"given_name": rec.donor_name or "Donor"}, "email_address": frappe.db.get_value("Donor", rec.donor, "email")},
        "application_context": {
            "brand_name": "Pleasant Springs Church",
            "user_action": "SUBSCRIBE_NOW",
            "return_url": get_url(f"/api/method/donation_management.api.gateways.paypal_gateway.return_subscription?donation={donation.name}"),
            "cancel_url": get_url("/donate"),
        },
    }
    r = requests.post(f"{_base_url()}/v1/billing/subscriptions", headers=_headers(), data=json.dumps(body), timeout=20)
    r.raise_for_status()
    data = r.json()
    rec.external_subscription_id = data["id"]
    rec.save(ignore_permissions=True)
    approve = next((l["href"] for l in data.get("links", []) if l["rel"] == "approve"), None)
    return {"redirect_url": approve, "subscription_id": data["id"]}


def _ensure_plan(donation, rec):
    """Find-or-create a billing plan keyed to (fund, frequency, amount)."""
    cache_key = f"paypal_plan::{donation.donation_fund}::{rec.frequency}::{flt(rec.amount):.2f}"
    cached = frappe.cache().get_value(cache_key)
    if cached:
        return cached

    interval_map = {
        "Weekly": ("WEEK", 1),
        "Bi-Weekly": ("WEEK", 2),
        "Monthly": ("MONTH", 1),
        "Quarterly": ("MONTH", 3),
        "Annually": ("YEAR", 1),
    }
    unit, count = interval_map.get(rec.frequency, ("MONTH", 1))
    product_id = _ensure_product(donation.donation_fund)

    plan_body = {
        "product_id": product_id,
        "name": f"{donation.donation_fund} — {rec.frequency} ${flt(rec.amount):.2f}",
        "billing_cycles": [{
            "frequency": {"interval_unit": unit, "interval_count": count},
            "tenure_type": "REGULAR",
            "sequence": 1,
            "total_cycles": 0,  # 0 = forever
            "pricing_scheme": {"fixed_price": {"value": f"{flt(rec.amount):.2f}", "currency_code": "USD"}},
        }],
        "payment_preferences": {"auto_bill_outstanding": True, "setup_fee": {"value": "0", "currency_code": "USD"}},
    }
    r = requests.post(f"{_base_url()}/v1/billing/plans", headers=_headers(), data=json.dumps(plan_body), timeout=20)
    r.raise_for_status()
    plan_id = r.json()["id"]
    frappe.cache().set_value(cache_key, plan_id, expires_in_sec=86400)
    return plan_id


def _ensure_product(fund_name):
    cache_key = f"paypal_product::{fund_name}"
    cached = frappe.cache().get_value(cache_key)
    if cached:
        return cached
    body = {
        "name": f"Donation — {fund_name}",
        "type": "SERVICE",
        "category": "NONPROFIT",
    }
    r = requests.post(f"{_base_url()}/v1/catalogs/products", headers=_headers(), data=json.dumps(body), timeout=20)
    r.raise_for_status()
    pid = r.json()["id"]
    frappe.cache().set_value(cache_key, pid, expires_in_sec=86400)
    return pid


@frappe.whitelist(allow_guest=True)
def return_subscription(donation, subscription_id=None):
    name = donation
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"/donate/thanks?ref={name}"
