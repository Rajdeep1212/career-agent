from app.core.config import settings
from app.services.linkedin_service import validate_configuration

print("=== Career Agent configuration ===")
print("RapidAPI key configured:", bool(settings.rapidapi_key))
print("RapidAPI host:", settings.rapidapi_host)
print("Google OAuth configured:", bool(settings.google_client_id and settings.google_client_secret))
print("Gmail token encryption configured:", bool(settings.token_encryption_key))
try:
    validate_configuration()
    linkedin_ready = True
except (ValueError, RuntimeError):
    linkedin_ready = False
print("LinkedIn OAuth ready:", linkedin_ready)

if not settings.rapidapi_key:
    print("ACTION NEEDED: Add RAPIDAPI_KEY to the root .env file.")
else:
    print("Job search configuration is ready.")
