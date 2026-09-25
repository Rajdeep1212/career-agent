from app.core.config import settings
from app.providers.registry import provider_status
from app.services.linkedin_service import validate_configuration

print("=== Career Agent configuration ===")
for provider in provider_status():
    if provider["installed"]:
        state = "configured" if provider["configured"] else f"skipped (needs {provider['requires']})"
        print(f"Job provider {provider['name']}:", state)
print("Google OAuth configured:", bool(settings.google_client_id and settings.google_client_secret))
print("Gmail token encryption configured:", bool(settings.token_encryption_key))
try:
    validate_configuration()
    linkedin_ready = True
except (ValueError, RuntimeError):
    linkedin_ready = False
print("LinkedIn OAuth ready:", linkedin_ready)

if not any(p["installed"] and p["configured"] for p in provider_status()):
    print("ACTION NEEDED: Add at least one job provider key (RAPIDAPI_KEY, ADZUNA_APP_ID/ADZUNA_APP_KEY or JOOBLE_API_KEY) to the root .env file.")
else:
    print("Job search configuration is ready.")
