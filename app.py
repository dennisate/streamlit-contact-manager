import streamlit as st
import os
from supabase import create_client, Client, ClientOptions, AuthApiError
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError
from streamlit_cookies_controller import CookieController

load_dotenv()

# Auth state lives in the visitor's own cookies, never in a server-side file, so
# two people using the app never share a session.
COOKIE_PREFIX = "contacts_"
REFRESH_COOKIE = "refresh"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def read_cookie(key: str) -> str | None:
    """Read a value from the visitor's browser through the cookie component."""
    return cookie_controller.get(COOKIE_PREFIX + key)


def write_cookie(key: str, value: str | None) -> None:
    """Set or remove a browser cookie through the cookie component."""
    name = COOKIE_PREFIX + key
    if value is None:
        cookie_controller.remove(name)
    else:
        cookie_controller.set(name, value, max_age=COOKIE_MAX_AGE, same_site="lax")


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


def activate_session() -> None:
    """Attach this Streamlit session's access token to this run's Supabase client."""
    session = st.session_state.session
    if not session:
        return
    try:
        response = supabase.auth.set_session(session.access_token, session.refresh_token)
    except Exception:
        # The refresh cookie can rebuild a revoked or expired session on the next pass.
        st.session_state.session = None
        st.session_state.user = None
        st.session_state.user_role = None
        return

    if response.session and response.session.user:
        remember_session(response.session)
        st.session_state.session = response.session
        st.session_state.user = response.session.user
        st.session_state.user_role = response.session.user.user_metadata.get("role", "read-only")


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


st.set_page_config(page_title="Contact Manager", layout="wide")

# Community Cloud filters browser cookies from st.context.cookies. This component
# reads them in the browser and returns them through Streamlit's component channel.
# Wait for its first response before attempting a PKCE code exchange.
cookie_state_key = "contacts_auth_cookies"
cookies_ready = cookie_state_key in st.session_state
cookie_controller = CookieController(key=cookie_state_key)

# Initialize Supabase Client
url: str = get_config("SUPABASE_URL")
key: str = get_config("SUPABASE_KEY")
supabase: Client = create_client(url, key, options=ClientOptions(storage=CookieStorage()))

if not cookies_ready:
    st.spinner("Preparing secure sign-in…")
    st.stop()

# Manage Auth State
if "user" not in st.session_state:
    st.session_state.user = None
if "user_role" not in st.session_state:
    st.session_state.user_role = None
if "otp_email" not in st.session_state:
    st.session_state.otp_email = None
if "session" not in st.session_state:
    st.session_state.session = None

# A Streamlit rerun constructs a new Supabase client. Reapply this browser's
# session so table queries use its JWT rather than the anonymous project key.
activate_session()

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
    st.markdown(
        """
        <style>
        .stApp { background: #ffffff; }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stMainBlockContainer"] { max-width: 1080px; padding-top: 4rem; }
        div[data-testid="stVerticalBlockBorderWrapper"] { border: 0; }
        .signin-wordmark {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            margin-bottom: 1.25rem;
            color: #111827;
            font-size: 1.65rem;
            font-weight: 700;
        }
        .signin-wordmark__icon {
            display: inline-flex;
            width: 21px;
            height: 21px;
            align-items: center;
            justify-content: center;
            color: #ffffff;
            font-size: 0.75rem;
            background: #111827;
        }
        .signin-rule { height: 3px; width: 100%; margin: 0 0 1.4rem; background: #f8c928; }
        .signin-heading { margin: 0 0 0.45rem; color: #252a34; font-size: 1.45rem; font-weight: 600; }
        .signin-subheading { margin: 0 0 1.4rem; color: #4b5563; font-size: 0.95rem; }
        .signin-divider { display: flex; align-items: center; gap: 0.8rem; margin: 1.6rem 0; color: #424954; font-size: 0.8rem; }
        .signin-divider::before, .signin-divider::after { content: ""; flex: 1; height: 1px; background: #d1d5db; }
        .signin-note { margin-top: 1.4rem; color: #5d6674; font-size: 0.82rem; line-height: 1.5; }
        div[data-testid="stTextInput"] input { border: 1px solid #4b5563; border-radius: 7px; }
        div[data-testid="stTextInput"] input:focus { border-color: #111827; box-shadow: 0 0 0 1px #111827; }
        div[data-testid="stLinkButton"] > a {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.6rem;
            border-color: #d1d5db;
            color: #303946;
            font-weight: 500;
        }
        div[data-testid="stLinkButton"] > a::before {
            content: "";
            width: 18px;
            height: 18px;
            background: center / contain no-repeat url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 18 18'%3E%3Cpath fill='%234285F4' d='M17.64 9.205c0-.638-.057-1.252-.164-1.841H9v3.482h4.844a4.14 4.14 0 0 1-1.797 2.716v2.258h2.909c1.703-1.568 2.684-3.877 2.684-6.615Z'/%3E%3Cpath fill='%2334A853' d='M9 18c2.43 0 4.466-.806 5.956-2.18l-2.909-2.258c-.806.54-1.837.86-3.047.86-2.34 0-4.32-1.58-5.027-3.705H.966v2.332A9 9 0 0 0 9 18Z'/%3E%3Cpath fill='%23FBBC05' d='M3.973 10.717A5.41 5.41 0 0 1 3.69 9c0-.597.102-1.178.283-1.717V4.951H.966A9 9 0 0 0 0 9c0 1.452.348 2.827.966 4.049l3.007-2.332Z'/%3E%3Cpath fill='%23EA4335' d='M9 3.578c1.32 0 2.507.454 3.441 1.345l2.581-2.581C13.462.891 11.426 0 9 0A9 9 0 0 0 .966 4.951l3.007 2.332C4.68 5.158 6.66 3.578 9 3.578Z'/%3E%3C/svg%3E");
        }
        div[data-testid="stButton"] > button { background: #f8c928; border-color: #f8c928; color: #252a34; font-weight: 600; }
        div[data-testid="stButton"] > button:hover { background: #eab308; border-color: #eab308; color: #252a34; }
        @media (max-width: 700px) {
            [data-testid="stMainBlockContainer"] { padding-top: 1.5rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    _, sign_in_column, _ = st.columns([1, 1.25, 1])
    with sign_in_column:
        with st.container(border=True):
            st.markdown(
                '<div class="signin-wordmark"><span class="signin-wordmark__icon">⌁</span>Contact Directory</div>'
                '<div class="signin-rule"></div>'
                '<h2 class="signin-heading">Sign in to continue</h2>'
                '<p class="signin-subheading">Use your email address to receive a one-time sign-in code.</p>',
                unsafe_allow_html=True,
            )

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

            pending_email = st.session_state.otp_email
            if not pending_email:
                email = st.text_input("Email address", placeholder="you@company.com")
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

            st.markdown('<div class="signin-divider">OR</div>', unsafe_allow_html=True)
            if google_url:
                st.link_button("Continue with Google", google_url, use_container_width=True)
            st.markdown('<p class="signin-note">This is a private, invite-only directory. Need access? Contact your administrator.</p>', unsafe_allow_html=True)

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
