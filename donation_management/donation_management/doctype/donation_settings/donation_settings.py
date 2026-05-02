import frappe
from frappe.model.document import Document


class DonationSettings(Document):
    pass


def get_settings():
    return frappe.get_single("Donation Settings")


def get_secret(field):
    """Resolve a Password field on Donation Settings."""
    return frappe.utils.password.get_decrypted_password(
        "Donation Settings", "Donation Settings", field, raise_exception=False
    )
