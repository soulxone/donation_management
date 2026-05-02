import frappe
from frappe.model.document import Document


class DonationFund(Document):
    def validate(self):
        if self.is_default:
            others = frappe.get_all(
                "Donation Fund", filters={"is_default": 1, "name": ["!=", self.name]}, pluck="name"
            )
            for o in others:
                frappe.db.set_value("Donation Fund", o, "is_default", 0)
        if not self.fund_code and self.fund_name:
            self.fund_code = "".join(w[0] for w in self.fund_name.split()[:3]).upper()

    def after_insert(self):
        if self.company and not self.income_account:
            try:
                from donation_management.donation_management.doctype.donation import accounting
                accounting.ensure_fund_income_account(self.name, self.company)
            except Exception:
                frappe.log_error(title=f"Fund income account creation failed: {self.name}")
