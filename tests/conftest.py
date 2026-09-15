import os

# Tests must never see the developer's real .env (credentials, phone number).
os.environ["JARVIS_DOTENV"] = "0"
