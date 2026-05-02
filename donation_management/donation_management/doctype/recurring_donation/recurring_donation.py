import frappe
from frappe.model.document import Document
from frappe.utils import add_days, add_months, getdate, today


FREQUENCY_DELTA = {
    "Weekly": ("days", 7),
    "Bi-Weekly": ("days", 14),
    "Monthly": ("months", 1),
    "Quarterly": ("months", 3),
    "Annually": ("months", 12),
}


class RecurringDonation(Document):
    def validate(self):
        validate(self, None)

    def advance_next_charge(self):
        unit, n = FREQUENCY_DELTA.get(self.frequency, ("months", 1))
        base = getdate(self.next_charge_date or self.start_date or today())
        nxt = add_days(base, n) if unit == "days" else add_months(base, n)
        self.db_set("next_charge_date", nxt)

    def charge_now(self):
        """Stub — Phase 6 wires this to Stripe/PayPal subscription advance.
        For provider-managed subscriptions (Stripe), the provider charges and posts
        a webhook; we simply record the resulting Donation. For self-managed channels
        (Plaid ACH), this method initiates the charge."""
        pass


def validate(doc, method=None):
    if not doc.next_charge_date:
        doc.next_charge_date = doc.start_date
    if doc.end_date and doc.start_date and getdate(doc.end_date) < getdate(doc.start_date):
        frappe.throw("End date cannot be before start date.")
