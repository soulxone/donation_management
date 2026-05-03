import frappe


def execute(filters=None):
    filters = filters or {}
    cond = ["d.docstatus = 1"]
    args = {}
    if filters.get("from_date"):
        cond.append("d.donation_date >= %(from_date)s")
        args["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        cond.append("d.donation_date <= %(to_date)s")
        args["to_date"] = filters["to_date"]
    if filters.get("donor"):
        cond.append("d.donor = %(donor)s")
        args["donor"] = filters["donor"]
    if filters.get("hide_anonymous"):
        cond.append("(don.is_anonymous = 0 OR don.is_anonymous IS NULL)")
    where = " AND ".join(cond)

    rows = frappe.db.sql(
        f"""select d.donor as donor, don.donor_name, don.email,
                   count(*) as gifts, sum(d.amount) as total,
                   min(d.donation_date) as first_gift, max(d.donation_date) as last_gift
            from `tabDonation` d
            join `tabDonor` don on don.name = d.donor
            where {where}
            group by d.donor
            order by total desc""",
        args,
        as_dict=True,
    )

    columns = [
        {"label": "Donor", "fieldname": "donor", "fieldtype": "Link", "options": "Donor", "width": 140},
        {"label": "Name", "fieldname": "donor_name", "fieldtype": "Data", "width": 200},
        {"label": "Email", "fieldname": "email", "fieldtype": "Data", "width": 200},
        {"label": "Gifts", "fieldname": "gifts", "fieldtype": "Int", "width": 80},
        {"label": "Total", "fieldname": "total", "fieldtype": "Currency", "width": 140},
        {"label": "First Gift", "fieldname": "first_gift", "fieldtype": "Date", "width": 110},
        {"label": "Last Gift", "fieldname": "last_gift", "fieldtype": "Date", "width": 110},
    ]
    return columns, rows
