import frappe


ROLES = [
    {
        "role_name": "Donations Manager",
        "desk_access": 1,
        "description": "Full access to donations, donors, recurring giving, receipts, reports. Can submit/cancel.",
    },
    {
        "role_name": "Donations User",
        "desk_access": 1,
        "description": "Day-to-day data entry: create donors, log donations, view reports.",
    },
]


DEFAULT_FUNDS = [
    {"fund_name": "General Fund", "fund_code": "GEN", "is_default": 1, "is_active": 1, "is_tax_deductible": 1, "show_on_donation_page": 1, "sort_order": 10, "description": "Supports the day-to-day ministry of Pleasant Springs Church.", "icon": "🏛️"},
    {"fund_name": "Building Fund", "fund_code": "BLD", "is_active": 1, "is_tax_deductible": 1, "show_on_donation_page": 1, "sort_order": 20, "description": "Maintenance, repairs, and improvements to our church facilities.", "icon": "🏗️"},
    {"fund_name": "Missions", "fund_code": "MIS", "is_active": 1, "is_tax_deductible": 1, "show_on_donation_page": 1, "sort_order": 30, "description": "Local outreach, foreign missions, and missionary support.", "icon": "🌍"},
    {"fund_name": "Benevolence", "fund_code": "BEN", "is_active": 1, "is_tax_deductible": 1, "show_on_donation_page": 1, "sort_order": 40, "description": "Direct aid to those in need within our community.", "icon": "❤️"},
    {"fund_name": "Youth & Children", "fund_code": "YTH", "is_active": 1, "is_tax_deductible": 1, "show_on_donation_page": 1, "sort_order": 50, "description": "Children's curriculum, youth events, and PS Kids ministry.", "icon": "👶"},
    {"fund_name": "Cemetery Care", "fund_code": "CEM", "is_active": 1, "is_tax_deductible": 1, "show_on_donation_page": 1, "sort_order": 60, "description": "Upkeep of Pleasant Springs Cemetery grounds and records.", "icon": "🌳"},
]


DEFAULT_CHANNELS = [
    {"channel_name": "Stripe", "provider": "Stripe", "display_name": "Card / Apple Pay / Google Pay", "supports_recurring": 1, "is_enabled": 0, "sort_order": 10, "icon": "💳", "fee_percent": 2.9, "fee_fixed": 0.30},
    {"channel_name": "Stripe ACH", "provider": "Stripe", "display_name": "Bank Transfer (ACH via Stripe)", "supports_recurring": 1, "is_enabled": 0, "sort_order": 15, "icon": "🏦", "fee_percent": 0.8, "fee_fixed": 0},
    {"channel_name": "PayPal", "provider": "PayPal", "display_name": "PayPal", "supports_recurring": 1, "is_enabled": 0, "sort_order": 20, "icon": "🅿️", "fee_percent": 2.89, "fee_fixed": 0.49},
    {"channel_name": "Venmo", "provider": "Braintree", "display_name": "Venmo", "supports_recurring": 1, "is_enabled": 0, "sort_order": 25, "icon": "💸", "fee_percent": 3.49, "fee_fixed": 0.49},
    {"channel_name": "Cash App Pay", "provider": "Square", "display_name": "Cash App Pay", "supports_recurring": 0, "is_enabled": 0, "sort_order": 30, "icon": "💵", "fee_percent": 2.75, "fee_fixed": 0},
    {"channel_name": "Square", "provider": "Square", "display_name": "Square (in-person)", "supports_recurring": 0, "is_enabled": 0, "sort_order": 35, "icon": "🟦", "fee_percent": 2.6, "fee_fixed": 0.10},
    {"channel_name": "Plaid ACH", "provider": "Plaid ACH", "display_name": "Direct from Bank", "supports_recurring": 1, "is_enabled": 0, "sort_order": 40, "icon": "🏦", "fee_percent": 0, "fee_fixed": 0.50},
    {"channel_name": "Text-to-Donate", "provider": "Twilio SMS", "display_name": "Text-to-Donate", "supports_recurring": 0, "is_enabled": 0, "sort_order": 50, "icon": "📱", "fee_percent": 2.9, "fee_fixed": 0.30},
    {"channel_name": "Zelle", "provider": "Zelle", "display_name": "Zelle (manual)", "supports_recurring": 0, "is_enabled": 1, "sort_order": 60, "icon": "🅉", "fee_percent": 0, "fee_fixed": 0},
    {"channel_name": "Check", "provider": "Check", "display_name": "Check", "supports_recurring": 0, "is_enabled": 1, "sort_order": 70, "icon": "✉️"},
    {"channel_name": "Cash", "provider": "Cash", "display_name": "Cash", "supports_recurring": 0, "is_enabled": 1, "sort_order": 80, "icon": "💵"},
]


def after_install():
    create_roles()
    seed_funds()
    seed_channels()
    seed_settings()
    bootstrap_coa()
    frappe.db.commit()


def bootstrap_coa():
    """Best-effort CoA provisioning. Safe to skip on fresh sites without a Company yet —
    the admin can re-run via `bench execute donation_management.api.bootstrap.bootstrap_all`."""
    s = frappe.get_single("Donation Settings")
    company = s.default_company or frappe.defaults.get_user_default("Company")
    if not company:
        company = frappe.db.get_value("Company", {}, "name")
    if not company:
        return
    if not s.default_company:
        s.default_company = company
        s.save(ignore_permissions=True)
    try:
        from donation_management.donation_management.doctype.donation import accounting
        accounting.bootstrap_chart_of_accounts(company=company)
    except Exception:
        frappe.log_error(title="Donation CoA bootstrap deferred")


def create_roles():
    for r in ROLES:
        if not frappe.db.exists("Role", r["role_name"]):
            doc = frappe.new_doc("Role")
            doc.role_name = r["role_name"]
            doc.desk_access = r["desk_access"]
            doc.insert(ignore_permissions=True)


def seed_funds():
    for f in DEFAULT_FUNDS:
        if not frappe.db.exists("Donation Fund", f["fund_name"]):
            doc = frappe.new_doc("Donation Fund")
            doc.update(f)
            doc.insert(ignore_permissions=True)


def seed_channels():
    for c in DEFAULT_CHANNELS:
        if not frappe.db.exists("Payment Channel", c["channel_name"]):
            doc = frappe.new_doc("Payment Channel")
            doc.update(c)
            doc.insert(ignore_permissions=True)


def seed_settings():
    s = frappe.get_single("Donation Settings")
    if not s.organization_name:
        s.organization_name = "Pleasant Springs Church"
    if not s.default_currency:
        s.default_currency = "USD"
    if not s.default_fund and frappe.db.exists("Donation Fund", "General Fund"):
        s.default_fund = "General Fund"
    s.save(ignore_permissions=True)
