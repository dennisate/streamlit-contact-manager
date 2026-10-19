# Contact Directory

A small private address book that runs in the browser. It is a Streamlit app on top of
Supabase: Google handles who you are, Supabase stores the contacts and decides what you
are allowed to do with them.

It exists so that a small team can share one contact list without passing a spreadsheet
around. Sign-in is restricted to Google accounts, so there is no password to manage.
Anyone who authenticates with Google can get in, and new accounts default to `read-only`,
so access is granted by role rather than by invitation.

## What it does

- **Google-only sign-in.** The app shows nothing until you authenticate; unauthenticated
  visitors see the "Secure Access Portal" gate and a Sign In with Google button.
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
- **`.supabase_auth.json`** (created next to `app.py`, gitignored) holds the active
  session so it survives Streamlit's reruns and page reloads. Delete it to force a
  re-login.

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

In the Supabase project:

1. Enable the Google provider under Authentication → Providers, with a Google OAuth
   client ID and secret.
2. Add `http://localhost:8501` to Authentication → URL Configuration → Redirect URLs,
   otherwise Supabase sends users to the project's Site URL instead.
3. Create the `contacts` table and set row level security policies so that authenticated
   users can read it, and only the roles above can write to it. The role checks in the UI
   only hide buttons; RLS is what actually protects the data.

## Limitations

- The redirect URL is hardcoded to `http://localhost:8501` in `app.py`, so the app is
  built for local use. Hosting it elsewhere means changing that value and allowlisting
  the new URL in Supabase.
- The stored session file is shared by everything that talks to this app on the machine.
  That is fine for a single-user local install, but it is not a multi-user deployment
  pattern.
