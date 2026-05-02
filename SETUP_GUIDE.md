# Donation Management — Setup Guide

This guide walks Pleasant Springs Church admins through configuring the donation system end‑to‑end, and shows donors how to give.

> **Repo:** https://github.com/soulxone/donation_management
> **Site:** https://ps-church.com
> **Admin desk:** https://ps-church.com/app

---

## Part 1 — Admin Setup (one-time)

### 1.1 Verify the app is installed

After the bench deploy finishes:

1. Sign in at https://ps-church.com/app as Administrator.
2. In the desk search bar (top center) type **"Donations"** and select the **Donations** workspace. You should see card breaks for *Records*, *Donors*, and *Configuration*.
3. Search **"Donor"**, **"Donation Fund"**, **"Payment Channel"**, **"Donation Settings"** — each should open without error.

If something is missing, open a terminal on Frappe Cloud bench and run:

```bash
bench --site ps-church.com install-app donation_management
bench --site ps-church.com migrate
bench --site ps-church.com clear-cache
```

### 1.2 Configure Donation Settings

Navigate to **Donations workspace → Donation Settings**. Fill in:

| Section | Field | Value |
|---|---|---|
| Organization | Organization Name | `Pleasant Springs Church` |
|  | EIN | *(your federal EIN)* |
|  | Default Company | *(your ERPNext Company)* |
|  | Default Currency | `USD` |
|  | Default Fund | `General Fund` |
| Receipts | Auto-send receipt on submit | ☑ |
|  | Receipt Email Subject | `Thank you for your gift to Pleasant Springs Church` |
|  | Compliance Statement | (default IRS Pub 1771 text — leave as-is unless your CPA advises) |
| Donation Page Defaults | Minimum Donation | `1` |
|  | Suggested Amounts | `10,25,50,100,250,500` |
|  | Thank You Message | (HTML shown on `/donate/thanks`) |

Leave provider sections (Stripe, PayPal, etc.) for now — Section 1.4.

### 1.3 Review seeded Funds

The installer creates six default funds. Open **Donation Fund** list and adjust as needed:

- General Fund (default)
- Building Fund
- Missions
- Benevolence
- Youth & Children
- Cemetery Care

For each: confirm the **Income Account** is wired (auto-created on install). If blank, click **Save** — `after_insert` will create the GL account `<Fund Name> - <Company Abbr>`.

To add a new fund: **+ New** → fill name + description + icon emoji → Save. The income account is auto-provisioned.

### 1.4 Enable Payment Channels

Each provider has its own setup. Start with the channels you want to offer immediately and enable the rest later.

#### A. Stripe (cards · Apple Pay · Google Pay · Cash App Pay · ACH · Link)
1. Stripe Dashboard → **Developers → API keys** → copy *Publishable key* + *Secret key*.
2. In **Donation Settings → Stripe section**: paste both, check **Stripe Enabled**, click **Save**.
3. Stripe Dashboard → **Developers → Webhooks → + Add endpoint**:
   - URL: `https://ps-church.com/api/method/donation_management.api.webhooks.stripe_webhook.handle`
   - Events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `payment_intent.succeeded`, `payment_intent.payment_failed`, `charge.refunded`, `charge.dispute.created`, `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.deleted`
4. Copy the **Signing secret** → paste into **Donation Settings → Webhook Signing Secret** → Save.
5. Open **Payment Channel** list. For both `Stripe` and `Stripe ACH`, set **Is Enabled = ☑** and Save.

#### B. PayPal (PayPal accounts)
1. https://developer.paypal.com → **My Apps & Credentials** → create REST app.
2. Copy **Client ID** + **Secret** into Donation Settings → PayPal section. Set Mode = `live`. Save.
3. Dashboard → **Webhooks → + Add Webhook**:
   - URL: `https://ps-church.com/api/method/donation_management.api.webhooks.paypal_webhook.handle`
   - Events: `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.REFUNDED`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.SALE.COMPLETED`, `PAYMENT.SALE.REFUNDED`, `BILLING.SUBSCRIPTION.ACTIVATED`, `BILLING.SUBSCRIPTION.CANCELLED`, `BILLING.SUBSCRIPTION.SUSPENDED`
4. Enable the **PayPal** Payment Channel.

#### C. Braintree (Venmo)
Venmo only flows through PayPal/Braintree.
1. https://www.braintreepayments.com → sign up under your existing PayPal Business account.
2. **Settings → API → Generate new API Key**. Copy Merchant ID, Public Key, Private Key into Donation Settings → Braintree section. Save.
3. **Settings → Processing → Payment methods → Venmo → Enable**.
4. Add webhook destination: `https://ps-church.com/api/method/donation_management.api.webhooks.braintree_webhook.handle` — subscribe to `subscription_charged_successfully`, `subscription_charged_unsuccessfully`, `subscription_canceled`, `subscription_expired`.
5. Enable the **Venmo** Payment Channel.

#### D. Square (in‑person + Cash App Pay)
1. https://developer.squareup.com → create a new application.
2. **Credentials**: copy **Application ID** + **Access Token** + **Location ID** into Donation Settings → Square section.
3. **Webhook subscriptions → + Subscribe**:
   - URL: `https://ps-church.com/api/method/donation_management.api.webhooks.square_webhook.handle`
   - Events: `payment.created`, `payment.updated`, `refund.created`
4. Enable the **Square** and **Cash App Pay** Payment Channels.

#### E. Plaid (direct bank ACH — cheapest for large gifts)
1. https://dashboard.plaid.com → create app → grab Client ID + Sandbox/Development/Production secret.
2. Plug into Donation Settings → Plaid section. Set Environment.
3. Stripe must be enabled — Plaid's `processor/stripe/bank_account_token` lets us settle the ACH through Stripe.
4. Enable the **Plaid ACH** Payment Channel.

#### F. Twilio (text‑to‑donate)
1. https://console.twilio.com → buy a phone number capable of SMS.
2. Set Account SID, Auth Token, From Number, Keyword (default `GIVE`) in Donation Settings → Twilio section.
3. In Twilio console, on the phone number → **Messaging → A message comes in → Webhook**:
   `https://ps-church.com/api/method/donation_management.api.gateways.twilio_gateway.inbound_sms` (HTTP POST, returns TwiML)
4. Enable the **Text-to-Donate** Payment Channel.
5. Test by texting `GIVE 5` to the number — you should receive a Stripe Checkout link.

#### G. Manual channels (always on)
**Zelle**, **Check**, and **Cash** are pre-enabled with no setup. Donors selecting these get on‑screen instructions plus a reference number. After you receive the gift, find the matching `Donation` in the desk, set status to `Succeeded`, and submit — the JE books to your books automatically.

### 1.5 Sanity check the donation page

1. Open https://ps-church.com/donate in a private/incognito window.
2. Confirm fund cards and channel cards render. Disabled channels won't show.
3. Make a $1 test gift via Cash channel — pick fund, amount, fill name + email, Continue. You should land on instructions screen with `GIFT-2026-00001`.
4. Back in desk → **Donation list** → confirm the draft. Submit it. Verify the JE was auto-created (open the Donation, **Journal Entry** field should be populated).

### 1.6 Wire up access for staff

- Bookkeeper: assign role **Donations User** (read/write donations + reports).
- Treasurer/lead: assign role **Donations Manager** (full access incl. submit/cancel + Settings).
- Read-only viewers: **Accounts Manager** has read access to donations + reports.

System Manager retains everything.

### 1.7 Annual giving statements

Run once each January for the prior calendar year:

```bash
bench --site ps-church.com execute donation_management.api.year_end.run --kwargs "{'tax_year': 2025}"
```

Dry-run first:

```bash
bench --site ps-church.com execute donation_management.api.year_end.run --kwargs "{'tax_year': 2025, 'dry_run': True}"
```

The runner skips anonymous, opted-out, and email-less donors.

### 1.8 Reports

In the desk search bar:
- **Donations by Fund** — totals + average gift per fund, date filters
- **Donor Giving Summary** — leaderboard by lifetime + first/last gift, hides anonymous

---

## Part 2 — Donor Guide

### Giving online
1. Visit **https://ps-church.com/donate**.
2. Pick a fund (General, Building, Missions, etc.).
3. Choose **One time**, **Monthly**, or **Weekly**.
4. Enter an amount or pick a preset chip.
5. Toggle **Make this gift anonymous** if desired (skips name/email).
6. Otherwise enter name, email, optional address (needed on tax receipts), optional memo.
7. Pick a method:
   - **Card / Apple Pay / Google Pay** — credit/debit, fastest.
   - **Bank Transfer (ACH via Stripe)** — lower fees, best for large gifts.
   - **PayPal / Venmo** — sign in with your existing account.
   - **Cash App Pay** — pay with your Cash App balance.
   - **Direct from Bank** — link your bank with Plaid (lowest fees).
   - **Zelle / Check / Cash** — manual; receive instructions on screen.
8. Click **Continue**. You'll be taken to the secure provider page (or shown next-step instructions).
9. After payment, you'll land on **/donate/thanks** and receive a tax receipt by email automatically.

### Text-to-donate
Text **`GIVE <amount>`** (e.g. `GIVE 50`) to the church number. You'll receive a one-tap link to complete the gift on Stripe Checkout. Optionally add a fund: `GIVE 25 BUILDING`.

### Viewing your giving history
If you have a member account on ps-church.com, sign in and visit **https://ps-church.com/my-giving** to:
- See your lifetime giving total
- Download PDF receipts for each gift
- Verify your contact info

### Updating recurring gifts
Currently donors must contact the church office to pause, change amount, or cancel a recurring plan. (A self-serve portal action is on the roadmap.)

### Tax receipts
- **Per-gift receipt** is emailed automatically right after each successful gift.
- **Annual giving statement** covering the full prior year is emailed each January.
- All receipts include the church's EIN and IRS Pub 1771 compliance language.

---

## Part 3 — Troubleshooting

**Donation submitted but no Journal Entry created**
Check the Donation: `Income Account` and `Deposit/Clearing Account` must be set. Re-save the linked Donation Fund / Payment Channel — the auto-provisioner will fix missing accounts. Then resubmit the JE manually via accounting helpers.

**Webhook not firing**
- Check the **Donation Payment Log** doctype — every event we receive lands here, signed or not.
- Verify the URL in the provider's dashboard matches exactly.
- For Stripe specifically: signature verification fails if the wrong webhook secret is in Donation Settings.

**Donor charged but Donation status stuck on `Pending`**
- The webhook never arrived. Check `Donation Payment Log` filtered by provider and external_event_id.
- Manually sync: open the Donation, in the Console run `donation_management.api.gateways.stripe_gateway.refresh_donation_from_session("<session_id>")`.

**Anonymous donor still showing in reports**
By default the **Donor Giving Summary** report has `Hide Anonymous = ☑`. The data is still in the database for the bookkeeper.

**Recurring plan paused unexpectedly**
After 3 consecutive failed charges, plans are auto-paused (see `tasks.process_failed_recurring`). Check `Donation Payment Log` for the failures, fix the donor's payment method, then set the **Recurring Donation** status back to `Active` and reset `consecutive_failures` to 0.

---

## Part 4 — Architecture Reference

| Concept | Doctype | Notes |
|---|---|---|
| Person/org giving | **Donor** | Auto-rolls up lifetime totals. Optional link to Church Member + portal User. |
| Designation | **Donation Fund** | Each fund has a GL income account. |
| Payment rail | **Payment Channel** | One per provider; each has a clearing account + fee account. |
| The gift | **Donation** | Submittable. Books a JE on submit. |
| Recurring plan | **Recurring Donation** | Mirrors Stripe Subscription / PayPal Billing Agreement. |
| Receipt | **Donation Receipt** | Per-donation or annual statement; PDF print format. |
| All keys | **Donation Settings** | Single-doc; provider creds, defaults, suggested amounts. |
| Audit trail | **Donation Payment Log** | Every webhook event, signed or not, idempotent. |

**Public URLs:**
- `/donate` — give now
- `/donate/thanks` — confirmation
- `/my-giving` — donor self-service

**Webhook URLs (configure in each provider dashboard):**
| Provider | URL |
|---|---|
| Stripe | `/api/method/donation_management.api.webhooks.stripe_webhook.handle` |
| PayPal | `/api/method/donation_management.api.webhooks.paypal_webhook.handle` |
| Braintree | `/api/method/donation_management.api.webhooks.braintree_webhook.handle` |
| Square | `/api/method/donation_management.api.webhooks.square_webhook.handle` |
| Twilio SMS | `/api/method/donation_management.api.gateways.twilio_gateway.inbound_sms` |

**Bench helpers:**
```bash
# (Re-)provision GL accounts for all funds + channels
bench --site ps-church.com execute donation_management.api.bootstrap.bootstrap_all

# Quick state diagnostic
bench --site ps-church.com execute donation_management.api.bootstrap.diagnostic

# Annual giving statements
bench --site ps-church.com execute donation_management.api.year_end.run --kwargs "{'tax_year': 2025}"
```
