"""Annual giving statement bulk runner.

Run once after January 1 to generate + email statements covering the previous tax year:

    bench --site ps-church.com execute donation_management.api.year_end.run --kwargs "{'tax_year': 2025}"
"""

import frappe
from frappe.utils import flt, now_datetime
from donation_management.donation_management.doctype.donation_receipt.receipt_engine import generate_annual_statement


@frappe.whitelist()
def run(tax_year, only_donor=None, dry_run=False):
    tax_year = int(tax_year)
    s = frappe.get_single("Donation Settings")

    # Find every donor with submitted donations in the tax year
    filters = {"docstatus": 1, "donation_date": ["between", [f"{tax_year}-01-01", f"{tax_year}-12-31"]]}
    if only_donor:
        filters["donor"] = only_donor
    donors = list({r.donor for r in frappe.get_all("Donation", filters=filters, fields=["donor"])})

    results = []
    for donor in donors:
        donor_doc = frappe.get_doc("Donor", donor)
        if not donor_doc.annual_statement_optin:
            results.append({"donor": donor, "status": "skipped — opted out"})
            continue
        if donor_doc.is_anonymous:
            results.append({"donor": donor, "status": "skipped — anonymous"})
            continue
        if not donor_doc.email:
            results.append({"donor": donor, "status": "skipped — no email"})
            continue

        if dry_run:
            results.append({"donor": donor, "status": "would-send"})
            continue

        rcpt_name = generate_annual_statement(donor, tax_year)
        if not rcpt_name:
            results.append({"donor": donor, "status": "no donations"})
            continue

        try:
            frappe.sendmail(
                recipients=[donor_doc.email],
                subject=f"{tax_year} Annual Giving Statement — {s.organization_name}",
                message=f"<p>Dear {donor_doc.donor_name},</p><p>Attached is your {tax_year} annual giving statement from {s.organization_name}. Thank you for your generosity throughout the year.</p>",
                reference_doctype="Donation Receipt",
                reference_name=rcpt_name,
                attachments=[
                    frappe.attach_print("Donation Receipt", rcpt_name, print_format="Donation Receipt", file_name=f"{rcpt_name}.pdf")
                ],
            )
            frappe.db.set_value("Donation Receipt", rcpt_name, "sent_at", now_datetime())
            results.append({"donor": donor, "receipt": rcpt_name, "status": "sent"})
        except Exception as e:
            results.append({"donor": donor, "receipt": rcpt_name, "status": f"error: {e}"})

    frappe.db.commit()
    return {"tax_year": tax_year, "count": len(results), "results": results}
