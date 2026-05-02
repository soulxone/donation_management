import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, today


class Donation(Document):
    def validate(self):
        validate_donation(self, None)


def validate_donation(doc, method=None):
    if not doc.amount or flt(doc.amount) <= 0:
        frappe.throw("Donation amount must be greater than zero.")

    if not doc.gross_amount:
        doc.gross_amount = doc.amount
    if doc.gross_amount and doc.fee_amount is not None:
        doc.net_amount = flt(doc.gross_amount) - flt(doc.fee_amount)
    else:
        doc.net_amount = doc.amount

    if doc.donation_fund and not doc.income_account:
        doc.income_account = frappe.db.get_value("Donation Fund", doc.donation_fund, "income_account")
    if doc.payment_channel and not doc.deposit_account:
        doc.deposit_account = frappe.db.get_value("Payment Channel", doc.payment_channel, "clearing_account")

    if doc.donor and not doc.receipt_email:
        doc.receipt_email = frappe.db.get_value("Donor", doc.donor, "email")


def on_submit(doc, method=None):
    """Submit hook: fan out to accounting + receipting + donor stats."""
    # Booking the JE is implemented in Phase 2; the call is wired here so future submits
    # will start booking automatically once that module lands.
    try:
        from donation_management.donation_management.doctype.donation import accounting
        accounting.book_donation_journal_entry(doc)
    except ImportError:
        # Phase 2 not yet deployed
        pass
    except Exception:
        frappe.log_error(title=f"Donation JE failed: {doc.name}")

    # Update donor lifetime totals
    if doc.donor:
        try:
            frappe.get_doc("Donor", doc.donor).update_giving_summary()
        except Exception:
            frappe.log_error(title=f"Donor summary update failed: {doc.donor}")

    # Issue receipt (Phase 5 lights up the actual generator)
    try:
        from donation_management.donation_management.doctype.donation_receipt import receipt_engine
        receipt_engine.issue_receipt_for(doc)
    except ImportError:
        pass
    except Exception:
        frappe.log_error(title=f"Receipt issue failed: {doc.name}")


def on_cancel(doc, method=None):
    """Cancel the linked Journal Entry, then refresh donor totals."""
    try:
        from donation_management.donation_management.doctype.donation import accounting
        accounting.reverse_donation_journal_entry(doc)
    except Exception:
        frappe.log_error(title=f"JE cancel failed: {doc.journal_entry}")
    if doc.donor:
        try:
            frappe.get_doc("Donor", doc.donor).update_giving_summary()
        except Exception:
            pass


@frappe.whitelist()
def quick_create(donor, fund, amount, channel, **kwargs):
    """Helper for desk quick-add and webhook handlers."""
    doc = frappe.new_doc("Donation")
    doc.donor = donor
    doc.donation_fund = fund
    doc.amount = amount
    doc.payment_channel = channel
    doc.donation_date = kwargs.get("donation_date") or today()
    doc.received_date = now_datetime()
    doc.source = kwargs.get("source", "Online")
    doc.external_transaction_id = kwargs.get("external_transaction_id")
    doc.payment_method = kwargs.get("payment_method")
    doc.fee_amount = kwargs.get("fee_amount") or 0
    doc.gross_amount = kwargs.get("gross_amount") or amount
    doc.status = kwargs.get("status", "Succeeded")
    doc.company = kwargs.get("company") or frappe.defaults.get_user_default("Company")
    doc.insert(ignore_permissions=True)
    if kwargs.get("submit"):
        doc.submit()
    return doc.name
