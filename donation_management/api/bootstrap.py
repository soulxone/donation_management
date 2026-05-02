"""Bootstrap helpers — invoke from `bench execute` or the System Console."""
import frappe
from donation_management.donation_management.doctype.donation import accounting


@frappe.whitelist()
def bootstrap_all(company=None):
    """One-shot setup: provision the CoA + back-fill income/clearing/fee accounts
    on every Donation Fund and Payment Channel.

        bench --site ps-church.com execute donation_management.api.bootstrap.bootstrap_all
    """
    return accounting.bootstrap_chart_of_accounts(company=company)


@frappe.whitelist()
def diagnostic():
    """Quick smoke check — useful after install."""
    return {
        "funds": frappe.get_all(
            "Donation Fund",
            fields=["name", "is_default", "is_active", "income_account", "company"],
        ),
        "channels": frappe.get_all(
            "Payment Channel",
            fields=["name", "provider", "is_enabled", "clearing_account", "fee_account"],
        ),
        "settings": frappe.get_doc("Donation Settings").as_dict(no_default_fields=True),
    }
