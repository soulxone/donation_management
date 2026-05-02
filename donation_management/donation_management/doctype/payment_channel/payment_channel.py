import frappe
from frappe.model.document import Document


class PaymentChannel(Document):
    def validate(self):
        if not self.display_name:
            self.display_name = self.channel_name
        if self.provider in ("Check", "Cash", "Zelle", "Manual") and self.supports_recurring:
            self.supports_recurring = 0
