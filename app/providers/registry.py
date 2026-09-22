from app.core.config import settings
from app.providers.jsearch_provider import JSearchProvider

PROVIDERS = {'jsearch': JSearchProvider}


def register_provider(name, factory):
    """Contributors register a JobProvider factory here, not in the engine."""
    PROVIDERS[name] = factory


def get_providers():
    names = [name.strip() for name in settings.job_providers.split(',') if name.strip()]
    return [PROVIDERS[name]() for name in names if name in PROVIDERS]


def provider_status():
    return [{'id': 'jsearch', 'available': bool(settings.rapidapi_key), 'reason': 'Requires RapidAPI credentials'},
            *[{'id': name, 'available': False, 'reason': reason} for name,reason in (
                ('company_careers','Connector not installed'), ('greenhouse','Connector not installed'),
                ('lever','Connector not installed'), ('smartrecruiters','Connector not installed'),
                ('linkedin_jobs','Current LinkedIn scopes allow identity only, not job/member search'))]]
