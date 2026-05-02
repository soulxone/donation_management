"""Text-to-donate via Twilio SMS.

Donor texts e.g. "GIVE 50" or "GIVE 25 BUILDING" to the church number.
Twilio posts to /api/method/donation_management.api.gateways.twilio_gateway.inbound_sms.
We:
  1. Match (or create) the Donor by phone number
  2. Create a Pending Donation
  3. Generate a Stripe Checkout one-time URL
  4. SMS the link back to the donor
  5. They tap, pay, return to /thanks; webhook books the JE.

This requires Stripe to be enabled (Twilio is the trigger, not the rails).
"""

import re
import frappe
from frappe.utils import flt
from donation_management.donation_management.doctype.donor.donor import find_or_create_donor
from donation_management.donation_management.doctype.donation_settings.donation_settings import get_secret


def _twilio_client():
    from twilio.rest import Client
    s = frappe.get_single("Donation Settings")
    if not s.twilio_enabled:
        frappe.throw("Twilio is not enabled.")
    return Client(s.twilio_account_sid, get_secret("twilio_auth_token")), s


@frappe.whitelist(allow_guest=True, methods=["POST"])
def inbound_sms():
    """Twilio webhook for incoming SMS. Returns TwiML response."""
    body = (frappe.local.form_dict.get("Body") or "").strip()
    from_num = frappe.local.form_dict.get("From") or ""
    s = frappe.get_single("Donation Settings")
    keyword = (s.twilio_keyword or "GIVE").upper()

    # Parse: "GIVE 50" or "GIVE 25 BUILDING"
    pattern = re.compile(rf"^\s*{re.escape(keyword)}\s+(\d+(?:\.\d{{1,2}})?)\s*([A-Za-z ]*)$", re.I)
    m = pattern.match(body)
    if not m:
        return _twiml(f"Reply with: {keyword} <amount> <fund>. Example: {keyword} 50  or  {keyword} 25 BUILDING")

    amount = flt(m.group(1))
    fund_input = (m.group(2) or "").strip()

    # Resolve fund
    fund = None
    if fund_input:
        fund = frappe.db.get_value("Donation Fund", {"fund_code": fund_input.upper(), "is_active": 1}, "name") \
               or frappe.db.get_value("Donation Fund", {"fund_name": ["like", f"%{fund_input}%"], "is_active": 1}, "name")
    if not fund:
        fund = s.default_fund or frappe.db.get_value("Donation Fund", {"is_default": 1}, "name")
    if not fund:
        return _twiml("Sorry — no default fund is configured.")

    # Stripe must be on
    if not s.stripe_enabled:
        return _twiml("Online giving is temporarily unavailable. Please give in person or by check.")

    # Donor + pending donation
    donor_id = find_or_create_donor(phone=from_num, name=f"SMS donor {from_num[-4:]}")
    donation = frappe.new_doc("Donation")
    donation.donor = donor_id
    donation.donation_fund = fund
    donation.payment_channel = "Text-to-Donate"
    donation.amount = amount
    donation.gross_amount = amount
    donation.currency = "USD"
    donation.donation_date = frappe.utils.today()
    donation.status = "Pending"
    donation.source = "Text"
    donation.company = s.default_company or frappe.defaults.get_user_default("Company")
    donation.insert(ignore_permissions=True)

    # Stripe Checkout URL
    from donation_management.api.gateways.stripe_gateway import create_checkout
    handoff = create_checkout(donation)
    url = handoff.get("redirect_url")

    return _twiml(f"Tap to give ${amount:.2f} to {fund}: {url}")


def _twiml(message):
    frappe.local.response["type"] = "page"
    frappe.local.response.update({
        "data": f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{frappe.utils.escape_html(message)}</Message></Response>',
        "content_type": "text/xml",
    })
    return frappe.local.response["data"]


def send_thank_you_sms(donation_name):
    """Optional: invoked from receipt engine after a successful SMS-initiated donation."""
    client, s = _twilio_client()
    d = frappe.get_doc("Donation", donation_name)
    phone = frappe.db.get_value("Donor", d.donor, "phone")
    if not phone:
        return
    client.messages.create(
        body=f"Thank you for your gift of ${d.amount:.2f} to Pleasant Springs Church! A receipt has been emailed to you.",
        from_=s.twilio_from_number,
        to=phone,
    )
