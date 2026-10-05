# Newsletters

A newsletter is a mailing list anyone can join from a company's website, with or without an account. People sign up with their email address, get a confirmation email, and are on the list once they click it (double opt-in). The company then writes issues as broadcasts and sends them to the list. Company owners and staff manage lists in the admin under **Email > Newsletters**.

## How it works

1. **Create a list.** email-service gives it a public key (`nl_...`) and its own confirmation email (a company template with `kind: newsletter_confirmation`, kept out of the event template list).
2. **Add the form** to the website. It posts to the list's `subscribe_url`.
3. **The visitor confirms.** The email links to `/n/confirm/<token>` on email-service. Opening the page shows a button. Clicking it confirms (mail scanners open links, and that alone must not subscribe anyone). The page then redirects to `confirmed_redirect_url`, or shows its own "You are subscribed" message.
4. **Send an issue.** Create a broadcast with the audience `{"mode": "newsletter", "list_id": 12}`. Only confirmed subscribers get it, each in their language.

Unconfirmed sign-ups are deleted after `EMAIL_NEWSLETTER_PENDING_DAYS` (7). The confirmation link works for `EMAIL_NEWSLETTER_CONFIRM_HOURS` (48).

## The website form

```html
<form id="newsletter">
  <input type="email" name="email" required autocomplete="email" />
  <!-- Honeypot: hidden from people, filled by bots. -->
  <input type="text" name="website" tabindex="-1" autocomplete="off" hidden />
  <button type="submit">Subscribe</button>
  <p role="status"></p>
</form>
<script>
  const form = document.getElementById("newsletter");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    const response = await fetch("https://email.shellui.com/api/v1/public/newsletters/nl_xxx/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...data, language: document.documentElement.lang }),
    });
    form.querySelector("[role=status]").textContent =
      response.status === 202 ? "Check your inbox to confirm." : "Something went wrong. Try again later.";
  });
</script>
```

Add the email-service host to the page's CSP `connect-src`.

### `POST /api/v1/public/newsletters/{public_key}/subscribe`

No auth. Body (JSON or form):

| Field | Notes |
| --- | --- |
| `email` | Required |
| `language` | Optional. Falls back to `Accept-Language`, then the list's `default_language`. |
| `first_name` | Optional. Available in the confirmation email and in issues. |
| `website` | Honeypot. Must be empty. When filled, the answer is `202` and nothing happens. |
| `turnstile_token` | Required when the list has a Turnstile secret |

The answer is `202 {"status": "pending"}` whether the address is new, already pending, or already confirmed, so the form cannot be used to find out who is subscribed. An already confirmed address gets no new email, and a pending one gets at most one email every `EMAIL_NEWSLETTER_RESEND_MINUTES` (10).

| Error | Status | When |
| --- | --- | --- |
| `validation_failed` | 400 | Missing or invalid `email` |
| `turnstile_failed` | 400 | The Turnstile token is missing or rejected |
| `origin_not_allowed` | 403 | The browser `Origin` is not in the list's `allowed_origins` |
| `newsletter_not_found` | 404 | Unknown or rotated key |
| `rate_limited` | 429 | Too many sign-ups from this IP or on this list |
| `newsletter_unavailable` | 503 | The company cannot send the confirmation email (no provider) |
| `turnstile_unavailable` | 503 | Cloudflare did not answer |

### Abuse protection

- **Double opt-in**: nobody is on the list until they click the link in their own inbox.
- **Rate limits**: `EMAIL_NEWSLETTER_IP_PER_HOUR` (10) per IP, `EMAIL_NEWSLETTER_ADDRESS_PER_DAY` (3) confirmation emails per address and list, and `EMAIL_NEWSLETTER_LIST_PER_HOUR` (500) per list. Behind a proxy, set `EMAIL_CLIENT_IP_HEADER` (for example `CF-Connecting-IP`) so the limit applies to the visitor and not the proxy.
- **Honeypot**: the `website` field.
- **Cloudflare Turnstile** (optional): set the site key and secret on the list. The secret is stored encrypted and never returned.
- **Allowed origins**: browsers may only post from the list's `allowed_origins` (empty means any website). The endpoint answers CORS for those origins only.

## Unsubscribing

On the bulk lane every issue carries a one-click unsubscribe link (`/u/<token>`, also sent as `List-Unsubscribe`) that removes the address from that list only. Other lists and company broadcasts are unaffected.

A company-wide unsubscribe (the link in a regular broadcast, or Resend's own unsubscribe page) also unsubscribes the address from every list of the company at that moment. Someone who later signs up again and confirms gets the newsletter again.

With Resend, issues go out through Resend Broadcasts and carry Resend's unsubscribe link. Resend unsubscribes are per Resend account, so using that link removes the address from every list of the company (and from every Shellui company on the platform account). Resend keeps that contact unsubscribed on its side: if the person signs up again later, Resend still skips them until the contact is re-enabled in Resend.

Unsubscribed rows keep their status and address hash so an import cannot add them back. Their address and first name are cleared after `EMAIL_NEWSLETTER_PENDING_DAYS`. `POST /api/v1/privacy/erase` deletes the rows.

## Shellui Actions events

`email.newsletter.confirmed` and `email.newsletter.unsubscribed`, with `list_id`, `list_name`, `email`, `language`, and `source`. Use them to copy subscribers into a CRM. `email` is masked once the stored address has been cleared.

## Admin endpoints

Auth: staff or company owner.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/newsletters` | `{newsletters: [...]}` |
| `POST` | `/api/v1/newsletters` | `{name, description?, default_language?, allowed_origins?, confirmed_redirect_url?}`. Returns `201`. |
| `GET` | `/api/v1/newsletters/{id}` | Adds `sender: {from_email, error_code}` for the confirmation email |
| `PATCH` | `/api/v1/newsletters/{id}` | Same fields, plus `turnstile_site_key` and `turnstile_secret` (write only, empty clears it) |
| `DELETE` | `/api/v1/newsletters/{id}` | Deletes the list, its subscribers, and its confirmation email |
| `POST` | `/api/v1/newsletters/{id}/rotate-key` | New public key. Forms with the old key get `404`. |
| `GET` | `/api/v1/newsletters/{id}/subscribers` | `?status=&email=&page=`. 50 per page. |
| `POST` | `/api/v1/newsletters/{id}/subscribers` | `{email, first_name?, language?, mode?}`. Returns `{outcome, subscriber}`. |
| `DELETE` | `/api/v1/newsletters/{id}/subscribers/{sid}` | Removes the row. The person can sign up again. |
| `GET` | `/api/v1/newsletters/{id}/subscribers.csv` | `?status=`. Columns `email, first_name, language, status, source, created_at, confirmed_at, unsubscribed_at`. |
| `POST` | `/api/v1/newsletters/{id}/subscribers/import` | `{csv, mode?}`. Returns `{added, existing, invalid, skipped_unsubscribed, skipped_suppressed}`. |

`mode` is `confirm` (default: each new address gets the confirmation email) or `consented` (added as confirmed, for people who already agreed elsewhere). Unsubscribed addresses are never re-added by `consented`: adding one returns `409 subscriber_unsubscribed`, and imports count them in `skipped_unsubscribed`. Imports take a CSV with an `email` column, optionally `first_name` and `language`, up to 2 MB and `EMAIL_BROADCAST_MAX_RECIPIENTS` rows.

The list payload: `id`, `name`, `description`, `public_key`, `default_language`, `allowed_origins`, `confirmed_redirect_url`, `turnstile_site_key`, `turnstile_configured`, `confirmation_template_id`, `subscribe_url`, `counts {pending, confirmed, unsubscribed}`, `created_at`, `updated_at`.

## The confirmation email

Edited like an event copy through `/api/v1/templates/{confirmation_template_id}/versions`. Variables: `company_name`, `list_name`, `confirm_url` (required), `first_name`, `recipient_email`. It starts in English and French and goes out on the transactional lane from the company's regular From address.
