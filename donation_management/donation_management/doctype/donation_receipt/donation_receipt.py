import frappe
from frappe.model.document import Document
from frappe.utils import flt


class DonationReceipt(Document):
    def validate(self):
        if self.receipt_type == "Per-Donation":
            if not self.donation:
                frappe.throw("Per-donation receipts require a donation reference.")
            self.total_amount = flt(frappe.db.get_value("Donation", self.donation, "amount"))
            if not self.tax_year:
                d = frappe.db.get_value("Donation", self.donation, "donation_date")
                if d:
                    self.tax_year = d.year
        else:
            self.total_amount = sum(flt(r.amount) for r in (self.donations_table or []))
