import frappe
from frappe.utils import getdate, today, add_months


def run_recurring_charges():
    """Monthly: trigger any active Recurring Donation whose next_charge_date is today.
    Stripe/PayPal handle most retries themselves; this is the fallback poller for
    channels we charge ourselves (e.g. ACH via Plaid)."""
    due = frappe.get_all(
        "Recurring Donation",
        filters={"status": "Active", "next_charge_date": ["<=", today()]},
        pluck="name",
    )
    for name in due:
        try:
            doc = frappe.get_doc("Recurring Donation", name)
            doc.charge_now()
        except Exception:
            frappe.log_error(title=f"Recurring charge failed: {name}")


def process_failed_recurring():
    """Daily: dunning — move recurring donations with too many failures to 'Paused'."""
    failed = frappe.get_all(
        "Recurring Donation",
        filters={"status": "Active", "consecutive_failures": [">=", 3]},
        pluck="name",
    )
    for name in failed:
        frappe.db.set_value("Recurring Donation", name, "status", "Paused")
        frappe.db.commit()
