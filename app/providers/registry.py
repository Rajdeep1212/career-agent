from app.core.config import settings
from app.demo import DemoJobProvider
from app.providers.adzuna_provider import AdzunaProvider
from app.providers.jooble_provider import JoobleProvider
from app.providers.jsearch_provider import JSearchProvider
from app.providers.radar_provider import RadarProvider

PROVIDERS = {'radar': RadarProvider, 'jsearch': JSearchProvider, 'adzuna': AdzunaProvider, 'jooble': JoobleProvider, 'demo': DemoJobProvider}
REQUIREMENTS = {'demo': 'nothing (synthetic demo data)', 'radar': 'a synced index (python -m app.sources.sync)', 'jsearch': 'RAPIDAPI_KEY', 'adzuna': 'ADZUNA_APP_ID and ADZUNA_APP_KEY', 'jooble': 'JOOBLE_API_KEY'}


def register_provider(name, factory):
    """Contributors register a JobProvider factory here, not in the engine."""
    PROVIDERS[name] = factory


def _names():
    return list(dict.fromkeys(name.strip().lower() for name in settings.job_providers.split(',') if name.strip()))


def _configured(factory) -> bool:
    check = getattr(factory, 'configured', None)
    return bool(check()) if callable(check) else True


def get_providers():
    """Listed providers whose credentials are set; a missing key skips a provider."""
    return [PROVIDERS[name]() for name in _names() if name in PROVIDERS and _configured(PROVIDERS[name])]


NOT_INSTALLED = (
    ('linkedin_jobs', 'Current LinkedIn scopes allow identity only, not job/member search'),
)


def provider_status():
    """Configuration only (never secret values), for the Connections view."""
    return [*[{'id': name, 'name': getattr(PROVIDERS[name], 'name', name), 'installed': True,
               'configured': _configured(PROVIDERS[name]), 'requires': REQUIREMENTS.get(name, 'provider credentials')}
              for name in _names() if name in PROVIDERS],
            *[{'id': name, 'name': name, 'installed': False, 'configured': False, 'reason': reason}
              for name, reason in NOT_INSTALLED]]
