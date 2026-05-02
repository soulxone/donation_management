"""Braintree gateway — primarily used for Venmo Pay (US only).

Flow:
  start_donation -> generate_client_token (returned to browser)
  -> donor uses Braintree Drop-in (Venmo button enabled) to get a payment_method_nonce
  -> nonce posted to /api/method/donation_management.api.gateways.braintree_gateway.charge_nonce
  -> create_transaction settles + finalizes Donation

We don't redirect — Braintree's Drop-in is rendered in-page. So `create_transaction`
returns `{mode: "client_token", token: "..."}` for the front-end to render Drop-in.
"""

import frappe
from frappe.utils import flt, now_datetime
from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret
from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import log_event


def _gateway():
    import braintree
    s = frappe.get_single("Donation Settings")
    if not s.braintree_enabled:
        frappe.throw("Braintree is not enabled.")
    env = braintree.Environment.Production if (s.paypal_mode == "live") else braintree.Environment.Sandbox
    return braintree.BraintreeGateway(
        access_token=None,
        environment=env,
        merchant_id=s.braintree_merchant_id,
        public_key=s.braintree_public_key,
        private_key=get_secret("braintree_private_key"),
    )


def create_transaction(donation):
    """Front-end will render Drop-in with the returned token; on submit, the
    front-end posts the nonce to charge_nonce."""
    gw = _gateway()
    customer_id = _ensure_customer(gw, donation.donor)
    token = gw.client_token.generate({"customer_id": customer_id})
    return {
        "mode": "braintree_dropin",
        "client_token": token,
        "donation": donation.name,
        "amount": flt(donation.amount),
    }


def _ensure_customer(gw, donor_name):
    donor = frappe.get_doc("Donor", donor_name)
    if donor.paypal_payer_id:
        return donor.paypal_payer_id
    result = gw.customer.create({
        "first_name": (donor.donor_name or "Donor").split()[0],
        "last_name": " ".join((donor.donor_name or "Donor").split()[1:]) or "—",
        "email": donor.email,
        "phone": donor.phone,
    })
    if not result.is_success:
        frappe.throw(f"Braintree customer create failed: {result.message}")
    donor.db_set("paypal_payer_id", result.customer.id, update_modified=False)
    return result.customer.id


@frappe.whitelist(allow_guest=True, methods=["POST"])
def charge_nonce(donation, nonce):
    """Called by the donate page after Drop-in returns a payment_method_nonce."""
    if not frappe.db.exists("Donation", donation):
        frappe.throw("Donation not found.")
    d = frappe.get_doc("Donation", donation)
    if d.status == "Succeeded":
        return {"already_done": True, "donation": d.name}

    gw = _gateway()
    is_recurring = bool(d.recurring_donation)

    if is_recurring:
        # Vault the payment method and create a subscription
        result = gw.payment_method.create({
            "customer_id": frappe.db.get_value("Donor", d.donor, "paypal_payer_id"),
            "payment_method_nonce": nonce,
            "options": {"make_default": True},
        })
        if not result.is_success:
            frappe.throw(f"Vault failed: {result.message}")
        plan_id = _ensure_plan(d)
        sub_result = gw.subscription.create({
            "payment_method_token": result.payment_method.token,
            "plan_id": plan_id,
            "price": f"{flt(d.amount):.2f}",
        })
        if not sub_result.is_success:
            frappe.throw(f"Subscription failed: {sub_result.message}")
        rec = frappe.get_doc("Recurring Donation", d.recurring_donation)
        rec.external_subscription_id = sub_result.subscription.id
        rec.external_payment_method_id = result.payment_method.token
        rec.save(ignore_permissions=True)
        d.status = "Processing"
        d.save(ignore_permissions=True)
        return {"ok": True, "subscription_id": sub_result.subscription.id, "donation": d.name}

    # One-time
    result = gw.transaction.sale({
        "amount": f"{flt(d.amount):.2f}",
        "payment_method_nonce": nonce,
        "customer_id": frappe.db.get_value("Donor", d.donor, "paypal_payer_id"),
        "options": {"submit_for_settlement": True},
        "custom_fields": {"donation": d.name, "fund": d.donation_fund},
    })
    if not result.is_success:
        d.status = "Failed"
        d.save(ignore_permissions=True)
        frappe.throw(f"Transaction failed: {result.message}")

    txn = result.transaction
    fee = flt(getattr(txn, "service_fee_amount", 0)) or _estimate_fee(d, flt(txn.amount))
    d.gross_amount = flt(txn.amount)
    d.fee_amount = fee
    d.net_amount = flt(txn.amount) - fee
    d.payment_method = txn.payment_instrument_type or "Venmo"
    d.external_transaction_id = txn.id
    d.status = "Succeeded"
    d.received_date = now_datetime()
    d.save(ignore_permissions=True)
    if d.docstatus == 0:
        d.submit()

    log_event("Braintree", "transaction.sale.completed", txn.id, txn.id, donation=d.name, verified=True, processing_status="Processed")
    return {"ok": True, "donation": d.name, "redirect_url": f"/donate/thanks?ref={d.name}"}


def _estimate_fee(d, amount):
    pct = flt(frappe.db.get_value("Payment Channel", d.payment_channel, "fee_percent")) or 0
    fixed = flt(frappe.db.get_value("Payment Channel", d.payment_channel, "fee_fixed")) or 0
    return round(amount * pct / 100.0 + fixed, 2)


def _ensure_plan(donation):
    """Braintree plans have to exist in the Braintree dashboard pre-created.
    We pick a plan_id from a naming convention: 'PSC-<freq>-<amount>'."""
    rec = frappe.get_doc("Recurring Donation", donation.recurring_donation)
    return f"PSC-{rec.frequency.upper()}-{int(round(flt(rec.amount)*100))}"
