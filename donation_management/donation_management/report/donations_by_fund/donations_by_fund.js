frappe.query_reports["Donations by Fund"] = {
  filters: [
    {fieldname: "from_date", label: "From Date", fieldtype: "Date", default: frappe.datetime.year_start()},
    {fieldname: "to_date", label: "To Date", fieldtype: "Date", default: frappe.datetime.year_end()},
    {fieldname: "donation_fund", label: "Fund", fieldtype: "Link", options: "Donation Fund"},
  ],
};
