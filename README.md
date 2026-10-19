# Contact Directory

A small private address book that runs in the browser. It is a Streamlit app on top of
Supabase: Google handles who you are, Supabase stores the contacts and decides what you
are allowed to do with them.

It exists so that a small team can share one contact list without passing a spreadsheet
around. Sign-in is by Google account or by a one-time code sent to an email address, so
there is no password to manage. Access is invite-only: the app never creates accounts, and
each person gets a role that decides what they can do.

## What it does

- **Sign in with Google or an email code.** The app shows nothing until you authenticate;
  unauthenticated visitors see the "Secure Access Portal" gate with a Sign In with Google
  button and an email-code form.
- **View the directory.** Every signed-in user sees the full list of contacts with name,
  telephone number and address.
- **Add and edit contacts.** Available to `read-write` and `admin` users. Editing happens
  in a popover on each contact.
- **Delete contacts.** `admin` only.
- **Stay signed in.** The session is kept on the server side, so a browser refresh does
  not log you out. The Log Out button in the sidebar ends the session.

## Roles

Permissions come from the `role` field in each user's Supabase `user_metadata`. Anyone
without a role is treated as `read-only`.

| Role         | View contacts | Add / Edit | Delete |
| ------------ | ------------- | ---------- | ------ |
| `read-only`  | yes           | no         | no     |
| `read-write` | yes           | yes        | no     |
| `admin`      | yes           | yes        | yes    |

Set a role in the Supabase dashboard under Authentication → Users → user metadata, for
example `{"role": "admin"}`.

## How it fits together

- **Streamlit** renders the UI and runs all the logic server-side.
- **Supabase Auth** performs the Google OAuth flow (PKCE). After the redirect back, the
  one-time code in the URL is exchanged for a session.
- **Supabase Postgres** holds the contacts, in a table named `contacts` with the columns
  the app reads and writes: `id`, `name`, `address`, `telephone_number`.
- **Cookies** hold the signed-in state, in the visitor's own browser. Only the refresh
  token is stored (a session is too big for a cookie); the app rebuilds the session from
  it on each visit. Nothing is shared between visitors, so one person's login is never
  handed to another.

## Running it

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens at <http://localhost:8501>.

### Configuration

Credentials are read from Streamlit Cloud secrets first, and fall back to a `.env` file
next to `app.py`:

```
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_KEY=<your-anon-key>
```

Locally, put those two values in `.env`. On Streamlit Cloud, put the same two keys in the
app's secrets (Settings → Secrets).

The app works out its own address from the incoming request, so the sign-in redirect is
correct both on localhost and when deployed. Set `APP_URL` (secret or environment
variable) to override that, for example `https://your-app.streamlit.app`.

In the Supabase project:

1. Enable the Google provider under Authentication → Providers, with a Google OAuth
   client ID and secret.
2. Turn off public sign-up: Authentication → Sign In / Providers, disable **Allow new
   users to sign up**. This is what makes access invite-only — without it, anyone with a
   Google account can create one for themselves. Existing users are unaffected.
3. For email sign-in, add `{{ .Token }}` to both the **Magic Link** and **Confirm signup**
   email templates (Authentication → Email Templates), so the message shows the one-time
   code. The emailed link itself is not usable here: Supabase returns the session in the
   URL fragment, which a server-side Streamlit app cannot read. The typed code is what
   signs you in. Note that the built-in email sender is rate limited (a couple of messages
   per hour), so testing with a custom SMTP provider is easier.
4. Add `http://localhost:8501` to Authentication → URL Configuration → Redirect URLs,
   otherwise Supabase sends users to the project's Site URL instead.
5. Create the `contacts` table and set row level security policies so that authenticated
   users can read it, and only the roles above can write to it. The role checks in the UI
   only hide buttons; RLS is what actually protects the data.

### Inviting users

The app refuses to create accounts, so people must exist in Supabase before they can sign
in:

1. Authentication → Users → Add user, with the address they will sign in with, and auto
   confirm enabled. The password does not matter — this app never uses passwords, so the
   person signs in with the emailed code instead, or with Google if it is the same address.
2. Give them a role in that user's metadata, for example `{"role": "read-write"}`. Without
   one they can sign in but only read.

Supabase's **Invite user** option works too and emails the person a link, but it asks them
to set a password they will never use here.

Auto confirm matters: Supabase treats an unconfirmed address as a signup that hasn't
finished, and with public sign-up turned off it refuses it — so an unconfirmed account
cannot sign in at all, even though it appears in the user list. If someone reports "that
account exists but its email has not been confirmed yet", confirm them from
Authentication → Users (or delete and re-add with auto confirm ticked).

## Limitations

- Signed-in state lives in the browser's cookies, so clearing them — or switching browser
  or device — means signing in again with a new code.
- Sessions are per browser, so each person signs in for themselves; there is no shared or
  "service" login.
