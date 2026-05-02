"""ERPNext accounting integration for Donation.

Booking model (gross/net):

    Donor pays $100 by Stripe. Fee = $3.20. Net deposited = $96.80.

        DR Stripe Clearing                $96.80
        DR Stripe Processing Fees         $ 3.20
            CR Donations Income / General        $100.00

For zero-fee channels (Cash, Check, Zelle, ACH-direct):

        DR Operating Bank Account         $100.00
            CR Donations Income / General        $100.00

Reversal on cancel: the Journal Entry is cancelled, which auto-reverses GL.
"""

import frappe
from frappe.utils import flt, getdate


def book_donation_journal_entry(donation):
    """Create + submit a Journal Entry for this Donation. Idempotent."""
    if donation.docstatus != 1:
        return
    if donation.journal_entry:
        return
    if donation.status not in ("Succeeded", "Pending"):
        # Don't book failed/refunded/disputed
        return

    company = donation.company or _default_company()
    if not company:
        frappe.throw("Set a company on the Donation or in Donation Settings before submitting.")

    income_account = donation.income_account or _resolve_income_account(donation.donation_fund, company)
    deposit_account = donation.deposit_account or _resolve_deposit_account(donation.payment_channel, company)

    if not income_account:
        frappe.throw(f"No income account configured for fund {donation.donation_fund}.")
    if not deposit_account:
        frappe.throw(f"No clearing/deposit account configured for channel {donation.payment_channel}.")

    gross = flt(donation.gross_amount or donation.amount)
    fee = flt(donation.fee_amount or 0)
    net = flt(donation.net_amount or (gross - fee))

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.posting_date = donation.donation_date
    je.company = company
    je.user_remark = f"Donation {donation.name} — {donation.donor_name or donation.donor} → {donation.donation_fund}"
    je.cheque_no = donation.external_transaction_id or donation.name
    je.cheque_date = donation.donation_date

    # CR income (gross)
    je.append("accounts", {
        "account": income_account,
        "credit_in_account_currency": gross,
        "debit_in_account_currency": 0,
        "user_remark": f"Donation to {donation.donation_fund}",
    })

    # DR deposit/clearing (net)
    je.append("accounts", {
        "account": deposit_account,
        "debit_in_account_currency": net,
        "credit_in_account_currency": 0,
        "user_remark": f"Net via {donation.payment_channel}",
    })

    # DR fee expense (if any)
    if fee > 0:
        fee_account = _resolve_fee_account(donation.payment_channel, company)
        if not fee_account:
            frappe.throw(f"Fee account not set on channel {donation.payment_channel}, but fee_amount={fee}.")
        je.append("accounts", {
            "account": fee_account,
            "debit_in_account_currency": fee,
            "credit_in_account_currency": 0,
            "user_remark": f"Processing fee — {donation.payment_channel}",
        })

    je.flags.ignore_permissions = True
    je.insert()
    je.submit()

    donation.db_set("journal_entry", je.name, update_modified=False)
    return je.name


def reverse_donation_journal_entry(donation):
    if not donation.journal_entry:
        return
    try:
        je = frappe.get_doc("Journal Entry", donation.journal_entry)
        if je.docstatus == 1:
            je.cancel()
    except frappe.DoesNotExistError:
        pass


# -------- Account resolution helpers --------

def _default_company():
    s = frappe.get_single("Donation Settings")
    return s.default_company or frappe.defaults.get_user_default("Company")


def _resolve_income_account(fund_name, company):
    if not fund_name:
        return None
    acct = frappe.db.get_value("Donation Fund", fund_name, "income_account")
    if acct:
        return acct
    # Auto-provision if missing
    return ensure_fund_income_account(fund_name, company)


def _resolve_deposit_account(channel_name, company):
    if not channel_name:
        return None
    acct = frappe.db.get_value("Payment Channel", channel_name, "clearing_account")
    if acct:
        return acct
    return ensure_channel_clearing_account(channel_name, company)


def _resolve_fee_account(channel_name, company):
    acct = frappe.db.get_value("Payment Channel", channel_name, "fee_account")
    if acct:
        return acct
    return ensure_channel_fee_account(channel_name, company)


# -------- COA provisioning --------

DONATIONS_INCOME_GROUP = "Donations"
PAYMENT_CLEARING_GROUP = "Payment Clearing"
PROCESSING_FEES_GROUP = "Payment Processing Fees"


def ensure_donations_coa(company):
    """Create the parent groups under the company's CoA if missing.
    Returns (income_parent_name, clearing_parent_name, fees_parent_name)."""
    # Find Income parent (root)
    income_root = frappe.db.get_value(
        "Account",
        {"company": company, "root_type": "Income", "is_group": 1, "parent_account": ["in", ("", None)]},
        "name",
    )
    if not income_root:
        # Fallback: pick the first income group
        income_root = frappe.db.get_value("Account", {"company": company, "root_type": "Income", "is_group": 1}, "name")

    bank_root = frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Bank", "is_group": 1},
        "name",
    ) or frappe.db.get_value(
        "Account",
        {"company": company, "root_type": "Asset", "is_group": 1, "parent_account": ["in", ("", None)]},
        "name",
    )

    expenses_root = frappe.db.get_value(
        "Account",
        {"company": company, "root_type": "Expense", "is_group": 1, "parent_account": ["in", ("", None)]},
        "name",
    ) or frappe.db.get_value(
        "Account", {"company": company, "root_type": "Expense", "is_group": 1}, "name"
    )

    income_parent = _create_group_if_missing(DONATIONS_INCOME_GROUP, income_root, company, "Income")
    clearing_parent = _create_group_if_missing(PAYMENT_CLEARING_GROUP, bank_root, company, "Asset", account_type="Bank")
    fees_parent = _create_group_if_missing(PROCESSING_FEES_GROUP, expenses_root, company, "Expense")

    return income_parent, clearing_parent, fees_parent


def _create_group_if_missing(name, parent, company, root_type, account_type=None):
    abbr = frappe.db.get_value("Company", company, "abbr")
    full_name = f"{name} - {abbr}"
    if frappe.db.exists("Account", full_name):
        return full_name
    if not parent:
        return None
    acct = frappe.new_doc("Account")
    acct.account_name = name
    acct.parent_account = parent
    acct.is_group = 1
    acct.company = company
    acct.root_type = root_type
    if account_type:
        acct.account_type = account_type
    acct.flags.ignore_permissions = True
    acct.insert()
    return acct.name


def _create_leaf_if_missing(account_name, parent, company, root_type, account_type=None):
    abbr = frappe.db.get_value("Company", company, "abbr")
    full_name = f"{account_name} - {abbr}"
    if frappe.db.exists("Account", full_name):
        return full_name
    if not parent:
        return None
    acct = frappe.new_doc("Account")
    acct.account_name = account_name
    acct.parent_account = parent
    acct.is_group = 0
    acct.company = company
    acct.root_type = root_type
    if account_type:
        acct.account_type = account_type
    acct.flags.ignore_permissions = True
    acct.insert()
    return acct.name


def ensure_fund_income_account(fund_name, company):
    income_parent, _, _ = ensure_donations_coa(company)
    acct = _create_leaf_if_missing(fund_name, income_parent, company, "Income")
    if acct:
        frappe.db.set_value("Donation Fund", fund_name, {"income_account": acct, "company": company})
    return acct


def ensure_channel_clearing_account(channel_name, company):
    _, clearing_parent, _ = ensure_donations_coa(company)
    label = f"{channel_name} Clearing"
    acct = _create_leaf_if_missing(label, clearing_parent, company, "Asset", account_type="Bank")
    if acct:
        frappe.db.set_value("Payment Channel", channel_name, "clearing_account", acct)
    return acct


def ensure_channel_fee_account(channel_name, company):
    _, _, fees_parent = ensure_donations_coa(company)
    label = f"{channel_name} Fees"
    acct = _create_leaf_if_missing(label, fees_parent, company, "Expense")
    if acct:
        frappe.db.set_value("Payment Channel", channel_name, "fee_account", acct)
    return acct


@frappe.whitelist()
def bootstrap_chart_of_accounts(company=None):
    """Whitelisted helper — run from console or a Bench job:
       bench --site ps-church.com execute donation_management.donation_management.doctype.donation.accounting.bootstrap_chart_of_accounts
    Provisions:
      - Donations income parent + child for each Donation Fund
      - Payment Clearing parent + clearing account for each enabled Payment Channel
      - Payment Processing Fees parent + fee account for each fee-bearing Payment Channel
      - Updates Donation Fund / Payment Channel records to reference the accounts
    """
    company = company or _default_company()
    if not company:
        frappe.throw("No company configured.")

    ensure_donations_coa(company)

    funds = frappe.get_all("Donation Fund", filters={"is_active": 1}, pluck="name")
    for f in funds:
        ensure_fund_income_account(f, company)

    channels = frappe.get_all(
        "Payment Channel",
        filters={"is_enabled": 1},
        fields=["name", "provider", "fee_percent", "fee_fixed"],
    )
    for ch in channels:
        ensure_channel_clearing_account(ch.name, company)
        if (flt(ch.fee_percent) > 0) or (flt(ch.fee_fixed) > 0):
            ensure_channel_fee_account(ch.name, company)

    frappe.db.commit()
    return {"ok": True, "company": company, "funds": funds, "channels": [c.name for c in channels]}
