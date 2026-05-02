"""Unauthenticated public endpoints for the /donate page.

All methods are guest-callable but rate-limited and CSRF-friendly. They never
trust client-side amount/fund/channel without re-validating against the DB.
"""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from donation_management.donation_management.doctype.donor.donor import find_or_create_donor
from donation_management.donation_management.doctype.donation_payment_log.donation_payment_log import log_event


@frappe.whitelist(allow_guest=True)
def get_donation_page_data():
    """Returns funds + channels + suggested amounts for the public /donate page."""
    funds = frappe.get_all(
        "Donation Fund",
        filters={"is_active": 1, "show_on_donation_page": 1},
        fields=["name", "fund_name", "fund_code", "description", "icon", "is_default", "campaign_target", "campaign_end_date"],
        order_by="sort_order asc, fund_name asc",
    )
    channels = frappe.get_all(
        "Payment Channel",
        filters={"is_enabled": 1},
        fields=["name", "channel_name", "provider", "display_name", "icon", "supports_recurring", "min_amount", "max_amount", "test_mode"],
        order_by="sort_order asc",
    )
    s = frappe.get_single("Donation Settings")
    suggested = [a.strip() for a in (s.suggested_amounts or "").split(",") if a.strip()]
    return {
        "organization_name": s.organization_name,
        "minimum_donation": flt(s.minimum_donation or 1),
        "suggested_amounts": [flt(a) for a in suggested],
        "thank_you_message": s.thank_you_message,
        "funds": funds,
        "channels": channels,
        "default_fund": s.default_fund,
    }


@frappe.whitelist(allow_guest=True)
def start_donation(
    fund,
    channel,
    amount,
    frequency=None,
    donor_name=None,
    email=None,
    phone=None,
    address_line_1=None,
    address_line_2=None,
    city=None,
    state=None,
    postal_code=None,
    country=None,
    is_anonymous=0,
    memo=None,
    in_honor_of=None,
    in_memory_of=None,
    campaign=None,
):
    """Validates input, finds-or-creates the Donor, creates a Pending Donation,
    and returns next-step instructions for the chosen channel.

    For provider-managed channels (Stripe/PayPal/Braintree/Square), this returns
    `redirect_url` that the front-end should send the browser to (Phase 4 lights
    those up). For manual channels (Cash/Check/Zelle), it returns instructions
    text that the page renders inline."""
    # Validate
    amount = flt(amount)
    s = frappe.get_single("Donation Settings")
    minimum = flt(s.minimum_donation or 1)
    if amount < minimum:
        frappe.throw(_("Minimum donation is {0}.").format(minimum))

    fund_doc = frappe.db.get_value(
        "Donation Fund",
        {"name": fund, "is_active": 1, "show_on_donation_page": 1},
        ["name", "fund_name"],
        as_dict=1,
    )
    if not fund_doc:
        frappe.throw(_("Fund is not available for online giving."))

    channel_doc = frappe.db.get_value(
        "Payment Channel",
        {"name": channel, "is_enabled": 1},
        ["name", "provider", "supports_recurring", "min_amount", "max_amount"],
        as_dict=1,
    )
    if not channel_doc:
        frappe.throw(_("Selected payment channel is not available."))

    if amount < flt(channel_doc.min_amount or 0):
        frappe.throw(_("This channel has a minimum of {0}.").format(channel_doc.min_amount))
    if channel_doc.max_amount and amount > flt(channel_doc.max_amount):
        frappe.throw(_("This channel has a maximum of {0}.").format(channel_doc.max_amount))

    if frequency and frequency not in ("Once", "Monthly", "Weekly", "Bi-Weekly", "Quarterly", "Annually"):
        frappe.throw(_("Invalid frequency."))
    if frequency and frequency != "Once" and not channel_doc.supports_recurring:
        frappe.throw(_("Selected channel does not support recurring donations."))

    is_anonymous = bool(int(is_anonymous or 0))

    # Donor
    donor_id = find_or_create_donor(
        email=email,
        name=donor_name,
        phone=phone,
        is_anonymous=is_anonymous,
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        state=state,
        postal_code=postal_code,
        country=country,
    )

    # Recurring container if needed
    recurring_id = None
    if frequency and frequency != "Once":
        rec = frappe.new_doc("Recurring Donation")
        rec.donor = donor_id
        rec.amount = amount
        rec.frequency = frequency
        rec.donation_fund = fund_doc.name
        rec.payment_channel = channel_doc.name
        rec.donor_note = memo
        rec.status = "Active"
        rec.insert(ignore_permissions=True)
        recurring_id = rec.name

    # Donation (Draft / Pending)
    donation = frappe.new_doc("Donation")
    donation.donor = donor_id
    donation.donation_fund = fund_doc.name
    donation.payment_channel = channel_doc.name
    donation.amount = amount
    donation.gross_amount = amount
    donation.currency = "USD"
    donation.donation_date = frappe.utils.today()
    donation.received_date = now_datetime()
    donation.status = "Pending"
    donation.source = "Online"
    donation.is_anonymous = is_anonymous
    donation.memo = memo
    donation.in_honor_of = in_honor_of
    donation.in_memory_of = in_memory_of
    donation.campaign = campaign
    donation.recurring_donation = recurring_id
    donation.is_recurring_first = 1 if recurring_id else 0
    donation.ip_address = frappe.local.request_ip
    donation.user_agent = (frappe.local.request.headers.get("User-Agent") if frappe.local.request else None) or ""
    donation.company = s.default_company or frappe.defaults.get_user_default("Company")
    donation.insert(ignore_permissions=True)

    log_event(
        provider=channel_doc.provider,
        event_type="public.start_donation",
        external_event_id=donation.name,
        external_object_id=donation.name,
        donation=donation.name,
        verified=True,
        processing_status="Received",
    )

    # Hand off to provider
    next_step = _handoff(donation, channel_doc)
    return {"donation": donation.name, **next_step}


def _handoff(donation, channel_doc):
    """Per-provider next-step. Phase 4 supplies the live integrations."""
    provider = channel_doc.provider

    if provider == "Stripe":
        try:
            from donation_management.api.gateways import stripe_gateway
            return stripe_gateway.create_checkout(donation)
        except ImportError:
            return _manual_pending(donation, "Stripe integration is not yet enabled.")

    if provider == "PayPal":
        try:
            from donation_management.api.gateways import paypal_gateway
            return paypal_gateway.create_order(donation)
        except ImportError:
            return _manual_pending(donation, "PayPal integration is not yet enabled.")

    if provider == "Braintree":
        try:
            from donation_management.api.gateways import braintree_gateway
            return braintree_gateway.create_transaction(donation)
        except ImportError:
            return _manual_pending(donation, "Braintree integration is not yet enabled.")

    if provider == "Square":
        try:
            from donation_management.api.gateways import square_gateway
            return square_gateway.create_payment_link(donation)
        except ImportError:
            return _manual_pending(donation, "Square integration is not yet enabled.")

    if provider == "Plaid ACH":
        try:
            from donation_management.api.gateways import plaid_gateway
            return plaid_gateway.create_link_token(donation)
        except ImportError:
            return _manual_pending(donation, "Plaid integration is not yet enabled.")

    # Manual channels
    if provider == "Zelle":
        s = frappe.get_single("Donation Settings")
        return {
            "mode": "instructions",
            "title": "Send via Zelle",
            "instructions": (
                f"Open your bank's Zelle and send <b>${donation.amount:,.2f}</b> to "
                f"<b>{s.organization_name}</b>. Include the reference <b>{donation.name}</b> in the memo so we can match your gift."
            ),
        }
    if provider == "Check":
        return {
            "mode": "instructions",
            "title": "Mail a check",
            "instructions": (
                f"Make a check for <b>${donation.amount:,.2f}</b> payable to <b>Pleasant Springs Church</b> "
                f"and mail it to: P.O. Box / address on file. Write reference <b>{donation.name}</b> on the memo line."
            ),
        }
    if provider == "Cash":
        return {
            "mode": "instructions",
            "title": "Bring cash to service",
            "instructions": f"Drop your gift of <b>${donation.amount:,.2f}</b> in the offering plate this Sunday. Reference <b>{donation.name}</b>.",
        }

    return _manual_pending(donation, "We've recorded your intent. Watch for follow-up from the church office.")


def _manual_pending(donation, message):
    return {
        "mode": "pending_manual",
        "title": "Almost done",
        "instructions": message,
    }
