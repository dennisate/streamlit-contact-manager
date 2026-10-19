import streamlit as st
import base64
import json
import os
import urllib.parse
from supabase import create_client, Client, ClientOptions, AuthApiError
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

load_dotenv()

# Auth state lives in the visitor's own cookies, never in a server-side file, so
# two people using the app never share a session.
COOKIE_PREFIX = "contacts_"
REFRESH_COOKIE = "refresh"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def pending_cookies() -> dict:
    if "pending_cookies" not in st.session_state:
        st.session_state.pending_cookies = {}
    return st.session_state.pending_cookies


def read_cookie(key: str) -> str | None:
    """Read a value this app stored in the visitor's browser."""
    pending = pending_cookies()
    if key in pending:
        return pending[key]
    raw = st.context.cookies.get(COOKIE_PREFIX + key)
    if not raw:
        return None
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def write_cookie(key: str, value: str | None) -> None:
    """Queue a cookie change. flush_cookies() applies it in the browser."""
    pending_cookies()[key] = value


def flush_cookies() -> None:
    """Apply queued cookie changes.

    Streamlit can read the cookies a request arrived with but cannot set them from
    Python, so a tiny same-origin iframe does the writing.
    """
    pending = pending_cookies()
    if not pending:
        return
    writes = []
    for key, value in pending.items():
        name = COOKIE_PREFIX + key
        if value is None:
            writes.append(f'document.cookie = "{name}=; Max-Age=0; path=/; SameSite=Lax";')
        else:
            encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
            writes.append(
                f'document.cookie = "{name}={encoded}; Max-Age={COOKIE_MAX_AGE}; path=/; SameSite=Lax"'
                ' + (location.protocol === "https:" ? "; Secure" : "");'
            )
    st.iframe("<script>" + "".join(writes) + "</script>", height=1)
    pending.clear()


class CookieStorage:
    """Supabase auth storage for the PKCE verifier, kept per browser.

    The session itself is not kept here: cookies are limited to roughly 4KB, so
    only the refresh token is stored and the session is rebuilt by refreshing.
    """

    def get_item(self, key: str) -> str | None:
        return read_cookie("verifier") if key.endswith("-code-verifier") else None

    def set_item(self, key: str, value: str) -> None:
        if key.endswith("-code-verifier"):
            write_cookie("verifier", value)

    def remove_item(self, key: str) -> None:
        if key.endswith("-code-verifier"):
            write_cookie("verifier", None)


def remember_session(session) -> None:
    """Keep the refresh token in the visitor's cookie so the next visit is signed in.

    Supabase rotates refresh tokens, so this runs after every sign-in and refresh.
    """
    if session and session.refresh_token:
        write_cookie(REFRESH_COOKIE, session.refresh_token)


def restore_session() -> None:
    """Rebuild this visitor's session from their stored refresh token."""
    token = read_cookie(REFRESH_COOKIE)
    if not token:
        return
    try:
        response = supabase.auth.refresh_session(token)
    except Exception:
        # Expired or revoked: forget it and fall back to the sign-in gate.
        write_cookie(REFRESH_COOKIE, None)
        return
    session = response.session
    if session and session.user:
        remember_session(session)
        st.session_state.session = session
        st.session_state.user = session.user
        st.session_state.user_role = session.user.user_metadata.get("role", "read-only")


def get_config(name: str) -> str | None:
    """Read a setting from Streamlit Cloud secrets, then local environment variables."""
    try:
        value = st.secrets.get(name)
    except StreamlitSecretNotFoundError:
        # No secrets.toml anywhere, which is the normal local setup.
        value = None
    return value or os.environ.get(name)


def get_app_url() -> str:
    """The URL the user is browsing, which is where Google must send them back.

    Taken from the current request so the same code works on localhost and on
    Streamlit Cloud. Set an APP_URL secret or environment variable to override it.
    """
    return (get_config("APP_URL") or st.context.url or "http://localhost:8501").rstrip("/")


# Initialize Supabase Client
url: str = get_config("SUPABASE_URL")
key: str = get_config("SUPABASE_KEY")
supabase: Client = create_client(url, key, options=ClientOptions(storage=CookieStorage()))

st.set_page_config(page_title="Contact Manager", layout="centered")

# Manage Auth State
if "user" not in st.session_state:
    st.session_state.user = None
if "user_role" not in st.session_state:
    st.session_state.user_role = None
if "otp_email" not in st.session_state:
    st.session_state.otp_email = None
if "session" not in st.session_state:
    st.session_state.session = None

# Supabase redirects back here with a short-lived auth code in the query string
# (PKCE flow), which Python can read directly.
query_params = st.query_params
if "error_description" in query_params:
    st.error(f"Google sign-in failed: {query_params['error_description']}")
elif "code" in query_params and st.session_state.user is None:
    try:
        # Exchange the one-time code for an active auth session
        response = supabase.auth.exchange_code_for_session({"auth_code": query_params["code"]})
        if response.session and response.session.user:
            remember_session(response.session)
            st.session_state.session = response.session
            st.session_state.user = response.session.user
            st.session_state.user_role = response.session.user.user_metadata.get("role", "read-only")
            
            # Clean up the URL query parameters so it looks neat again
            st.query_params.clear()
            st.rerun()
    except Exception as e:
        st.error(f"Token verification failed: {e}")

# Sign this visitor back in from their own stored refresh token, if they have one
if st.session_state.user is None:
    restore_session()

# --- SIGN-IN GATE ---
if st.session_state.user is None:
    st.title("🔐 Secure Access Portal")
    st.warning("This is a private network directory. Sign in with Google or an email code.")

    try:
        auth_data = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": get_app_url()
            }
        })
        google_url = auth_data.url
    except Exception as e:
        st.error(f"Failed to initialize Google Auth Provider: {e}")
        google_url = None

    if google_url:
        st.write("Click the button below to authorize your account securely via Google Workspace:")
        st.link_button("🔴 Sign In with Google", google_url, use_container_width=True)
        st.caption("💡 Once authorized, the page should automatically verify your account identity.")

    st.divider()
    st.write("#### ✉️ Sign in with an email code")

    pending_email = st.session_state.otp_email
    if not pending_email:
        email = st.text_input("Email address", placeholder="you@example.com")
        if st.button("Send sign-in code", use_container_width=True):
            email = email.strip()
            if not email:
                st.warning("Enter your email address first.")
            else:
                try:
                    # should_create_user=False keeps access invite-only: an unknown
                    # address is rejected instead of being signed up.
                    supabase.auth.sign_in_with_otp({"email": email, "options": {"should_create_user": False}})
                    st.session_state.otp_email = email
                    st.rerun()
                except AuthApiError as e:
                    if e.code == "otp_disabled":
                        st.error("That email address has no account yet. Ask an administrator to invite you.")
                    elif e.code == "signup_disabled":
                        st.error(
                            "That account exists but its email has not been confirmed yet. "
                            "Ask an administrator to confirm it."
                        )
                    else:
                        st.error(f"Could not send the sign-in code: {e}")
                except Exception as e:
                    st.error(f"Could not send the sign-in code: {e}")
    else:
        st.caption(f"A sign-in code was sent to {pending_email}.")
        code = st.text_input("Sign-in code", placeholder="Code from the email")
        if st.button("Verify code", use_container_width=True):
            try:
                response = supabase.auth.verify_otp(
                    {"email": pending_email, "token": code.strip(), "type": "email"}
                )
                if response.session and response.session.user:
                    remember_session(response.session)
                    st.session_state.session = response.session
                    st.session_state.user = response.session.user
                    st.session_state.user_role = response.session.user.user_metadata.get("role", "read-only")
                    st.session_state.otp_email = None
                    st.rerun()
                else:
                    st.error("That code was not accepted. Request a new one and try again.")
            except Exception as e:
                st.error(f"Sign-in failed: {e}")
        if st.button("Use a different email"):
            st.session_state.otp_email = None
            st.rerun()

    # The Google verifier and any other queued cookie change must reach the browser
    # before this run ends.
    flush_cookies()
    st.stop()

# --- AUTHENTICATED APP WORKFLOW ---
user_role = st.session_state.user_role
st.title("📇 Contact Directory")
st.success(f"Logged in successfully as: {st.session_state.user.email}")
st.info(f"Access Role Level: {user_role.upper()}")

if st.sidebar.button("Log Out"):
    # Revoke the session server-side, then forget it in this browser
    session = st.session_state.get("session")
    if session:
        try:
            supabase.auth.admin.sign_out(session.access_token, "global")
        except Exception:
            pass
    write_cookie(REFRESH_COOKIE, None)
    st.session_state.user = None
    st.session_state.user_role = None
    st.session_state.session = None
    st.query_params.clear()
    st.rerun()



# 1. READ: Fetch and view records (Available to ALL roles)
try:
    response = supabase.table("contacts").select("*").execute()
    contacts = response.data
except Exception as e:
    st.error(f"Error fetching data: {e}")
    contacts = []

# Display Data Table cleanly
if contacts:
    for item in contacts:
        with st.container():
            st.write(f"### {item['name']}")
            st.text(f"📞 {item['telephone_number']} | 📍 {item['address']}")
            
            # Contextual actions based on Roles
            col_edit, col_del = st.columns([1, 1])
            
            # EDIT Functionality (Read-Write & Admin)
            if user_role in ['read-write', 'admin']:
                with col_edit:
                    with st.popover("✏️ Edit"):
                        new_name = st.text_input("Name", value=item['name'], key=f"n_{item['id']}")
                        new_addr = st.text_input("Address", value=item['address'], key=f"a_{item['id']}")
                        new_tel = st.text_input("Telephone", value=item['telephone_number'], key=f"t_{item['id']}")
                        if st.button("Save Changes", key=f"s_{item['id']}"):
                            supabase.table("contacts").update({"name": new_name, "address": new_addr, "telephone_number": new_tel}).eq("id", item['id']).execute()
                            st.success("Updated!")
                            st.rerun()
            
            # DELETE Functionality (Admin Only)
            if user_role == 'admin':
                with col_del:
                    if st.button("🗑️ Delete", key=f"d_{item['id']}"):
                        supabase.table("contacts").delete().eq("id", item['id']).execute()
                        st.success("Deleted!")
                        st.rerun()
        st.divider()
else:
    st.info("No contacts found.")

# 2. CREATE Functionality (Read-Write & Admin Only)
if user_role in ['read-write', 'admin']:
    st.write("## ➕ Add New Contact")
    with st.form("add_contact_form", clear_on_submit=True):
        c_name = st.text_input("Full Name")
        c_addr = st.text_input("Address")
        c_tel = st.text_input("Telephone Number")
        submit = st.form_submit_button("Add Record")
        
        if submit and c_name:
            supabase.table("contacts").insert({"name": c_name, "address": c_addr, "telephone_number": c_tel}).execute()
            st.success(f"Added {c_name} to address book!")
            st.rerun()

# Any cookie change queued during this run (rotated refresh token, cleared logout)
# has to be written by the browser before the run ends.
flush_cookies()
