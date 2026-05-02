// Desk-side enhancements for Donation Management
frappe.provide("donation_management");

donation_management.format_currency = function (val) {
  try { return format_currency(val, "USD"); } catch (e) { return val; }
};
