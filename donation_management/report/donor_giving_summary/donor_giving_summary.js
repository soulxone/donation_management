frappe.query_reports["Donor Giving Summary"] = {
  filters: [
    {fieldname: "from_date", label: "From Date", fieldtype: "Date", default: frappe.datetime.year_start()},
    {fieldname: "to_date", label: "To Date", fieldtype: "Date", default: frappe.datetime.year_end()},
    {fieldname: "donor", label: "Donor", fieldtype: "Link", options: "Donor"},
    {fieldname: "hide_anonymous", label: "Hide Anonymous", fieldtype: "Check", default: 1},
  ],
};
