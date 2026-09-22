"""Bounded query scheduling, never a roles-by-cities Cartesian product."""
import re
from app.models.schemas import CandidateProfile
from app.models.career import SearchIntent, RoleSuggestion, SearchQuery


def _clean(value):
    return re.sub(r"[^\w +#./-]", " ", value, flags=re.UNICODE).strip()[:70]


def plan_search_queries(profile: CandidateProfile, intent: SearchIntent,
                        roles: list[RoleSuggestion], limit: int = 4) -> list[SearchQuery]:
    limit = max(4, min(10, limit))
    titles = list(dict.fromkeys(intent.roles_requested + [t for r in roles for t in r.titles]))
    if not titles:
        titles = profile.preferred_roles or [' '.join(profile.skills[:2]) + ' opportunities']
    locations = intent.locations or profile.preferred_locations or ['India']
    locations = [x for x in locations if x.casefold() not in {e.casefold() for e in intent.excluded_locations}] or ['India']
    remote_only = intent.remote_allowed and not intent.onsite_allowed and not intent.hybrid_allowed
    if remote_only:
        locations = ['remote ' + locations[0]]
    elif intent.remote_allowed and intent.locations:
        locations = locations + ['remote']
    level = 'fresher' if intent.fresher_preference or profile.experience_level in ('fresher', 'entry_level') else ''
    variants = [level, 'entry level' if level else 'jobs', 'graduate' if level else 'vacancies', 'opportunities']
    queries = []
    for i in range(limit * 4):
        role = _clean(titles[i % len(titles)])
        location = _clean(locations[i % len(locations)])
        modifier = variants[(i // max(len(titles), len(locations))) % len(variants)]
        query = ' '.join(x for x in (role, modifier, location) if x)
        if query not in {q.query for q in queries}:
            queries.append(SearchQuery(query=query, role=role, location=location,
                reason='Requested role/location' if intent.roles_requested else 'Candidate evidence and bounded role expansion'))
        if len(queries) >= limit:
            break
    return queries
