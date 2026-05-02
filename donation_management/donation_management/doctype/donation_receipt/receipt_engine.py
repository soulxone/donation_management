"""Auto-generate and email Donation Receipts.

Per-donation receipts are issued on Donation.on_submit if Settings says so.
Annual giving statements are generated via the bulk run in `api/year_end.py`.
"""

import frappe
from frappe.utils import flt, now_datetime, getdate


def issue_receipt_for(donation):
    """Create a Per-Donation Receipt and email it. Idempotent."""
    if donation.donation_receipt:
        return donation.donation_receipt
    s = frappe.get_single("Donation Settings")
    if not s.send_receipt_on_submit:
        return None

    donor = frappe.get_doc("Donor", donation.donor) if donation.donor else None
    if not donor or not donor.email:
        return None  # Anonymous / no email — admin can issue manually
    if donor.is_anonymous:
        return None
    if not donor.communication_optin:
        return None

    rcpt = frappe.new_doc("Donation Receipt")
    rcpt.receipt_type = "Per-Donation"
    rcpt.donor = donor.name
    rcpt.donation = donation.name
    rcpt.tax_year = getdate(donation.donation_date).year
    rcpt.total_amount = flt(donation.amount)
    rcpt.delivery_method = "Email"
    rcpt.email_to = donor.email
    rcpt.ein = s.ein
    rcpt.compliance_text = s.compliance_statement
    rcpt.flags.ignore_permissions = True
    rcpt.insert()
    rcpt.submit()

    donation.db_set("donation_receipt", rcpt.name, update_modified=False)

    _send_receipt_email(rcpt, donation, donor, s)
    donation.db_set("receipt_sent", 1, update_modified=False)
    donation.db_set("receipt_sent_at", now_datetime(), update_modified=False)
    return rcpt.name


def _send_receipt_email(rcpt, donation, donor, settings):
    subject = settings.auto_email_subject or "Thank you for your gift"
    msg = _receipt_email_html(rcpt, donation, donor, settings)
    frappe.sendmail(
        recipients=[donor.email],
        subject=subject,
        message=msg,
        reference_doctype="Donation Receipt",
        reference_name=rcpt.name,
        attachments=[
            frappe.attach_print(
                doctype="Donation Receipt",
                name=rcpt.name,
                print_format="Donation Receipt",
                file_name=f"{rcpt.name}.pdf",
            )
        ],
    )
    rcpt.db_set("sent_at", now_datetime(), update_modified=False)


def _receipt_email_html(rcpt, donation, donor, settings):
    return f"""
<div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto">
  <div style="background:linear-gradient(135deg,#3a9e8a,#4ABFAB,#6BB8D4);color:#fff;padding:1.5rem;text-align:center">
    <h2 style="margin:0">Thank you for your gift!</h2>
  </div>
  <div style="padding:1.5rem;color:#333;line-height:1.6">
    <p>Dear {donor.donor_name},</p>
    <p>Thank you for your gift of <b>${donation.amount:,.2f}</b> on <b>{frappe.format(donation.donation_date,{'fieldtype':'Date'})}</b> to the <b>{donation.donation_fund}</b> at {settings.organization_name}.</p>
    <p>A formal tax receipt is attached as a PDF for your records.</p>
    <p style="font-size:.9rem;color:#666;border-top:1px solid #eee;padding-top:1rem">{settings.compliance_statement or ''}</p>
    <p style="text-align:center;margin-top:2rem"><a href="https://ps-church.com/my-giving" style="background:#4ABFAB;color:#fff;padding:.6rem 1.2rem;border-radius:.4rem;text-decoration:none">View My Giving</a></p>
  </div>
</div>
"""


def generate_annual_statement(donor_name, tax_year):
    """Bulk: build an Annual Statement covering all submitted donations for a tax year."""
    rows = frappe.get_all(
        "Donation",
        filters={"donor": donor_name, "docstatus": 1, "donation_date": ["between", [f"{tax_year}-01-01", f"{tax_year}-12-31"]]},
        fields=["name", "donation_date", "donation_fund", "amount"],
        order_by="donation_date asc",
    )
    if not rows:
        return None
    total = sum(flt(r.amount) for r in rows)
    s = frappe.get_single("Donation Settings")
    rcpt = frappe.new_doc("Donation Receipt")
    rcpt.receipt_type = "Annual Statement"
    rcpt.donor = donor_name
    rcpt.tax_year = tax_year
    rcpt.total_amount = total
    rcpt.ein = s.ein
    rcpt.compliance_text = s.compliance_statement
    rcpt.delivery_method = "Email"
    rcpt.email_to = frappe.db.get_value("Donor", donor_name, "email")
    for r in rows:
        rcpt.append("donations_table", {"donation": r.name, "donation_date": r.donation_date, "donation_fund": r.donation_fund, "amount": r.amount})
    rcpt.flags.ignore_permissions = True
    rcpt.insert()
    rcpt.submit()
    return rcpt.name
