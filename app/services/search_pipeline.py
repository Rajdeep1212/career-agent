from app.core.preferences import DEFAULT_JOB_PREFERENCES
from app.providers.jsearch_provider import JSearchProvider
from app.services.application_verifier import verify_application
from app.services.ranker import rank_jobs
from app.storage.history import is_seen, mark_seen
from app.storage.profile_store import load_profile


async def search_verify_rank(query: str, *, include_seen: bool = False):
    provider = JSearchProvider()
    profile = load_profile()
    jobs = await provider.search(query)

    verified = []
    for job in jobs:
        if not include_seen and is_seen(job):
            continue

        job = await verify_application(job)

        # Strict policy: only verified-active application pages proceed.
        if job.application_status != "active":
            continue

        verified.append(job)

    ranked = rank_jobs(profile, verified, DEFAULT_JOB_PREFERENCES)

    # Mark only jobs actually surfaced to the user.
    surfaced_keys = {(r.company, r.title) for r in ranked}
    for job in verified:
        if (job.company, job.title) in surfaced_keys:
            mark_seen(job)

    return {
        "query": query,
        "provider": "JSearch/RapidAPI",
        "profile": profile,
        "preferences": DEFAULT_JOB_PREFERENCES,
        "results": ranked,
        "verified_count": len(verified),
        "result_count": len(ranked),
    }
