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
    """Send receipt email with the formal tax receipt rendered inline as HTML.

    PDF attachment is opt-in: if frappe.attach_print succeeds (e.g. when
    a compatible PDF generator is configured on the site), we attach the PDF.
    Otherwise we still send the email — the inline HTML body contains the
    full IRS Pub 1771-compliant receipt content."""
    subject = settings.auto_email_subject or "Thank you for your gift"
    msg = _receipt_email_html(rcpt, donation, donor, settings)

    attachments = []
    try:
        attachments.append(
            frappe.attach_print(
                doctype="Donation Receipt",
                name=rcpt.name,
                print_format="Donation Receipt",
                file_name=f"{rcpt.name}.pdf",
            )
        )
    except Exception:
        # PDF generator not available or print format not yet loaded — fall through.
        frappe.log_error(title=f"Receipt PDF skipped for {rcpt.name}")

    frappe.sendmail(
        recipients=[donor.email],
        subject=subject,
        message=msg,
        reference_doctype="Donation Receipt",
        reference_name=rcpt.name,
        attachments=attachments or None,
    )
    rcpt.db_set("sent_at", now_datetime(), update_modified=False)


def _receipt_email_html(rcpt, donation, donor, settings):
    """Build a complete receipt as HTML email body — works as a stand-alone
    receipt even if the PDF attachment is missing."""
    addr_lines = []
    if donor.address_line_1:
        line = donor.address_line_1
        if donor.address_line_2:
            line += f", {donor.address_line_2}"
        addr_lines.append(line)
    if donor.city or donor.state or donor.postal_code:
        addr_lines.append(f"{donor.city or ''}{', ' + donor.state if donor.state else ''} {donor.postal_code or ''}".strip())
    address_html = "<br>".join(addr_lines) if addr_lines else ""

    return f"""
<div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;color:#333">
  <div style="background:linear-gradient(135deg,#3a9e8a,#4ABFAB,#6BB8D4);color:#fff;padding:2rem 1.5rem;text-align:center">
    <h1 style="margin:0;color:#fff;font-size:1.5rem">{settings.organization_name or 'Pleasant Springs Church'}</h1>
    <div style="margin-top:.5rem;opacity:.92">Pinson, Tennessee</div>
    {f'<div style="margin-top:.5rem;opacity:.92;font-size:.85rem">EIN: {settings.ein}</div>' if settings.ein else ''}
    <div style="margin-top:1rem;font-size:1.1rem;letter-spacing:.05em">OFFICIAL DONATION RECEIPT</div>
  </div>
  <div style="padding:1.5rem;line-height:1.6">
    <p>Dear {donor.donor_name},</p>
    <p>Thank you for your generous gift of <b>${donation.amount:,.2f}</b> to the <b>{donation.donation_fund}</b>.</p>
    <table style="width:100%;border-collapse:collapse;margin:1rem 0;background:#EBF6FA;border-radius:.4rem">
      <tr><td style="padding:.5rem 1rem"><b>Receipt #:</b></td><td style="padding:.5rem 1rem">{rcpt.name}</td></tr>
      <tr><td style="padding:.5rem 1rem"><b>Date of Gift:</b></td><td style="padding:.5rem 1rem">{frappe.format(donation.donation_date,{'fieldtype':'Date'})}</td></tr>
      <tr><td style="padding:.5rem 1rem"><b>Amount:</b></td><td style="padding:.5rem 1rem"><b>${donation.amount:,.2f}</b></td></tr>
      <tr><td style="padding:.5rem 1rem"><b>Fund:</b></td><td style="padding:.5rem 1rem">{donation.donation_fund}</td></tr>
      <tr><td style="padding:.5rem 1rem"><b>Method:</b></td><td style="padding:.5rem 1rem">{donation.payment_channel}{f' ({donation.payment_method})' if donation.payment_method else ''}</td></tr>
    </table>
    {f'<div style="margin:1rem 0;padding:.75rem;border-left:3px solid #4ABFAB;background:#f8fafa"><b>{donor.donor_name}</b><br>{address_html}</div>' if address_html else ''}
    <p style="font-size:.85rem;color:#666;border-top:1px solid #eee;margin-top:1.5rem;padding-top:1rem;font-style:italic">{settings.compliance_statement or 'No goods or services were provided in exchange for this contribution.'}</p>
    <p style="text-align:center;margin-top:2rem">
      <a href="https://ps-church.com/my-giving" style="background:#4ABFAB;color:#fff;padding:.6rem 1.2rem;border-radius:.4rem;text-decoration:none">View My Giving History</a>
    </p>
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
