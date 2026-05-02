app_name = "donation_management"
app_title = "Donation Management"
app_publisher = "PS Church"
app_description = "Donation acceptance, receipting, recurring giving, and tax statements for Pleasant Springs Church"
app_email = "soulxone@gmail.com"
app_license = "AGPLv3"
required_apps = ["frappe", "erpnext"]

app_icon = "/assets/donation_management/images/donate.svg"
app_color = "#4ABFAB"
app_icon_color = "#FFFFFF"

after_install = "donation_management.install.after_install"

app_include_css = "/assets/donation_management/css/donation.css"
app_include_js = "/assets/donation_management/js/donation.js"
web_include_css = "/assets/donation_management/css/donation_web.css"

# Document hooks
doc_events = {
    "Donation": {
        "validate": "donation_management.donation_management.doctype.donation.donation.validate_donation",
        "on_submit": "donation_management.donation_management.doctype.donation.donation.on_submit",
        "on_cancel": "donation_management.donation_management.doctype.donation.donation.on_cancel",
    },
    "Recurring Donation": {
        "validate": "donation_management.donation_management.doctype.recurring_donation.recurring_donation.validate",
    },
}

# Fixtures
fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "Donation Management"]]},
    {"dt": "Property Setter", "filters": [["module", "=", "Donation Management"]]},
    {"dt": "Print Format", "filters": [["module", "=", "Donation Management"]]},
    {"dt": "Email Template", "filters": [["name", "like", "Donation%"]]},
]

# Scheduled Tasks — runs daily at 02:00 site time
scheduler_events = {
    "daily": [
        "donation_management.tasks.process_failed_recurring",
    ],
    "monthly": [
        "donation_management.tasks.run_recurring_charges",
    ],
}

# Public website routes
website_route_rules = [
    {"from_route": "/donate", "to_route": "donate"},
    {"from_route": "/donate/thanks", "to_route": "donate_thanks"},
    {"from_route": "/my-giving", "to_route": "my_giving"},
]

# Whitelisted API methods exposed at /api/method/...
override_whitelisted_methods = {}

# Webhook endpoints (configure each in the provider's dashboard):
#   Stripe    → /api/method/donation_management.api.webhooks.stripe_webhook.handle
#   PayPal    → /api/method/donation_management.api.webhooks.paypal_webhook.handle
#   Braintree → /api/method/donation_management.api.webhooks.braintree_webhook.handle
#   Square    → /api/method/donation_management.api.webhooks.square_webhook.handle
#   Twilio    → /api/method/donation_management.api.gateways.twilio_gateway.inbound_sms (TwiML)
#
# Donor return URLs:
#   PayPal    → /api/method/donation_management.api.gateways.paypal_gateway.return_url
