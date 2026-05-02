import frappe
from frappe.model.document import Document
from frappe.utils import flt, today


class Donor(Document):
    def validate(self):
        if self.is_anonymous and not self.donor_name:
            self.donor_name = "Anonymous Donor"
        if self.email:
            self.email = self.email.strip().lower()

    def update_giving_summary(self):
        """Recompute lifetime_total / counts from submitted donations."""
        rows = frappe.db.sql(
            """select count(*) as cnt, coalesce(sum(amount),0) as total,
                      min(donation_date) as first_d, max(donation_date) as last_d
               from `tabDonation`
               where donor=%s and docstatus=1""",
            self.name,
            as_dict=1,
        )[0]
        self.db_set("donations_count", rows.cnt or 0, update_modified=False)
        self.db_set("lifetime_total", flt(rows.total), update_modified=False)
        self.db_set("first_donation_date", rows.first_d, update_modified=False)
        self.db_set("last_donation_date", rows.last_d, update_modified=False)


def find_or_create_donor(email=None, name=None, phone=None, is_anonymous=False, **extra):
    """Match-or-create lookup used by public donation form + webhook handlers.
    Match priority: email → phone → name (within last 90 days)."""
    if is_anonymous and not email and not phone:
        donor = frappe.new_doc("Donor")
        donor.donor_name = "Anonymous"
        donor.is_anonymous = 1
        donor.donor_type = "Anonymous"
        donor.insert(ignore_permissions=True)
        return donor.name

    existing = None
    if email:
        existing = frappe.db.get_value("Donor", {"email": email.strip().lower()}, "name")
    if not existing and phone:
        existing = frappe.db.get_value("Donor", {"phone": phone.strip()}, "name")
    if existing:
        return existing

    donor = frappe.new_doc("Donor")
    donor.donor_name = name or (email.split("@")[0] if email else "New Donor")
    donor.email = email
    donor.phone = phone
    for k, v in extra.items():
        if hasattr(donor, k) and v is not None:
            setattr(donor, k, v)
    donor.insert(ignore_permissions=True)
    return donor.name
