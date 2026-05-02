import frappe
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    cond = ["docstatus = 1"]
    args = {}
    if filters.get("from_date"):
        cond.append("donation_date >= %(from_date)s")
        args["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        cond.append("donation_date <= %(to_date)s")
        args["to_date"] = filters["to_date"]
    if filters.get("donation_fund"):
        cond.append("donation_fund = %(donation_fund)s")
        args["donation_fund"] = filters["donation_fund"]

    where = " AND ".join(cond)

    rows = frappe.db.sql(
        f"""select donation_fund as fund, count(*) as gifts,
                  sum(amount) as total, sum(net_amount) as net,
                  avg(amount) as avg_gift
            from `tabDonation`
            where {where}
            group by donation_fund
            order by total desc""",
        args,
        as_dict=True,
    )

    columns = [
        {"label": "Fund", "fieldname": "fund", "fieldtype": "Link", "options": "Donation Fund", "width": 200},
        {"label": "Gifts", "fieldname": "gifts", "fieldtype": "Int", "width": 100},
        {"label": "Total", "fieldname": "total", "fieldtype": "Currency", "width": 140},
        {"label": "Net", "fieldname": "net", "fieldtype": "Currency", "width": 140},
        {"label": "Average Gift", "fieldname": "avg_gift", "fieldtype": "Currency", "width": 140},
    ]

    return columns, rows
