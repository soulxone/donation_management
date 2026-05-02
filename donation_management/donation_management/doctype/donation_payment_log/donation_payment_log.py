import frappe
from frappe.model.document import Document


class DonationPaymentLog(Document):
    pass


def already_processed(provider, external_event_id):
    """Idempotency check used by webhook handlers."""
    if not external_event_id:
        return False
    return bool(
        frappe.db.exists(
            "Donation Payment Log",
            {"provider": provider, "external_event_id": external_event_id, "processing_status": "Processed"},
        )
    )


def log_event(provider, event_type, external_event_id, external_object_id=None, raw_payload=None, verified=False, donation=None, processing_status="Received", error_message=None):
    doc = frappe.new_doc("Donation Payment Log")
    doc.provider = provider
    doc.event_type = event_type
    doc.external_event_id = external_event_id
    doc.external_object_id = external_object_id
    doc.raw_payload = raw_payload
    doc.verified = 1 if verified else 0
    doc.donation = donation
    doc.processing_status = processing_status
    doc.error_message = error_message
    doc.insert(ignore_permissions=True)
    return doc.name
