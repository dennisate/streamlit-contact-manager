import streamlit as st
import json
import os
import urllib.parse
from pathlib import Path
from supabase import create_client, Client, ClientOptions
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

load_dotenv()


class AuthStorage:
    """Local store for the Supabase auth session and PKCE code verifier.

    Streamlit re-runs this file from top to bottom on every interaction, and the
    redirect back from Google starts a fresh session, so the Supabase client
    cannot keep either value in memory. A JSON file next to the app is durable
    across both reruns and browser reloads.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get_item(self, key: str) -> str | None:
        return self._read().get(key)

    def set_item(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = value
        self._path.write_text(json.dumps(data), encoding="utf-8")

    def remove_item(self, key: str) -> None:
        data = self._read()
        if data.pop(key, None) is not None:
            self._path.write_text(json.dumps(data), encoding="utf-8")


def get_config(name: str) -> str | None:
    """Read a setting from Streamlit Cloud secrets, then local environment variables."""
    try:
        value = st.secrets.get(name)
    except StreamlitSecretNotFoundError:
        # No secrets.toml anywhere, which is the normal local setup.
        value = None
    return value or os.environ.get(name)


# Initialize Supabase Client
url: str = get_config("SUPABASE_URL")
key: str = get_config("SUPABASE_KEY")
supabase: Client = create_client(
    url,
    key,
    options=ClientOptions(storage=AuthStorage(Path(__file__).with_name(".supabase_auth.json"))),
)

st.set_page_config(page_title="Contact Manager", layout="centered")

# Manage Auth State
if "user" not in st.session_state:
    st.session_state.user = None
if "user_role" not in st.session_state:
    st.session_state.user_role = None

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
            st.session_state.user = response.session.user
            st.session_state.user_role = response.session.user.user_metadata.get("role", "read-only")
            
            # Clean up the URL query parameters so it looks neat again
            st.query_params.clear()
            st.rerun()
    except Exception as e:
        st.error(f"Token verification failed: {e}")

# Try to automatically pull an active user session token if it exists
if st.session_state.user is None:
    try:
        session = supabase.auth.get_session()
        if session and session.user:
            st.session_state.user = session.user
            st.session_state.user_role = session.user.user_metadata.get("role", "read-only")
    except Exception:
        pass

# --- GOOGLE ONLY GATEKEEPER AUTH UI ---
if st.session_state.user is None:
    st.title("🔐 Secure Access Portal")
    st.warning("This is a private network directory. Google Authentication is required.")
    
    try:
        auth_data = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": "http://localhost:8501" 
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
            
    st.stop()

# --- AUTHENTICATED APP WORKFLOW ---
user_role = st.session_state.user_role
st.title("📇 Contact Directory")
st.success(f"Logged in successfully as: {st.session_state.user.email}")
st.info(f"Access Role Level: {user_role.upper()}")

if st.sidebar.button("Log Out"):
    supabase.auth.sign_out()
    st.session_state.user = None
    st.session_state.user_role = None
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
