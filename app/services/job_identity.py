import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from app.models.schemas import JobPosting, JobSourceRef


def canonical_url(value):
    if not value:
        return ''
    parts = urlsplit(str(value))
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
        return ''
    query = [(k,v) for k,v in parse_qsl(parts.query) if not k.lower().startswith('utm_') and k.lower() not in ('ref','source','trackingid','fbclid','gclid')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), urlencode(sorted(query)), ''))


def identity_keys(job: JobPosting):
    keys=[]
    url=canonical_url(job.application_url)
    if url:
        keys.append('url:'+url)
    if job.source_job_id and job.source:
        keys.append('source:'+job.source.casefold()+':'+job.source_job_id)
    if not keys:
        keys.append('fields:'+'|'.join(re.sub(r'\s+', ' ', x).strip().casefold() for x in (job.company,job.title,job.location)))
    return keys


def job_identity(job: JobPosting):
    return hashlib.sha256(identity_keys(job)[0].encode()).hexdigest()


def deduplicate_jobs(jobs):
    seen=set(); unique=[]
    for job in jobs:
        keys=set(identity_keys(job))
        if not keys & seen:
            unique.append(job)
        seen.update(keys)
    return unique


# Cross-source identity: the same job listed on the official board and on an aggregator.
_LEGAL_SUFFIXES = re.compile(r'\b(?:private|pvt|limited|ltd|inc|incorporated|llc|llp|plc|corp|corporation|co|company|'
                             r'gmbh|ag|sa|bv|group|holdings|india|technologies|technology|solutions)\b')
_CITY_ALIASES = {'bangalore': 'bengaluru', 'gurgaon': 'gurugram', 'bombay': 'mumbai', 'madras': 'chennai',
                 'calcutta': 'kolkata', 'new delhi': 'delhi', 'navi mumbai': 'mumbai'}
_CITIES = ('bengaluru', 'hyderabad', 'pune', 'chennai', 'mumbai', 'gurugram', 'noida', 'delhi', 'kolkata',
           'ahmedabad', 'kochi', 'jaipur', 'coimbatore', 'thiruvananthapuram', 'indore', 'chandigarh', 'mysuru')
TITLE_SIMILARITY = 0.88


def company_key(name) -> str:
    """'Databricks India Pvt. Ltd.' -> 'databricks'."""
    text = re.sub(r'[^a-z0-9& ]+', ' ', str(name or '').casefold()).replace('&', ' and ')
    stripped = re.sub(r'\s+', ' ', _LEGAL_SUFFIXES.sub(' ', text)).strip()
    return stripped or re.sub(r'\s+', ' ', text).strip()


def title_key(title) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9+#]+', ' ', str(title or '').casefold())).strip()


def city_key(location) -> str | None:
    """The first known Indian city in a location string, with common aliases folded."""
    text = ' ' + re.sub(r'[^a-z ]+', ' ', str(location or '').casefold()) + ' '
    for alias, city in _CITY_ALIASES.items():
        text = text.replace(f' {alias} ', f' {city} ')
    found = [(text.find(f' {city} '), city) for city in _CITIES if f' {city} ' in text]
    return min(found)[1] if found else None


def same_job(a: JobPosting, b: JobPosting) -> bool:
    """Same company (keys already resolved by the caller), similar title and a compatible city."""
    from difflib import SequenceMatcher
    title_a, title_b = title_key(a.title), title_key(b.title)
    city_a, city_b = city_key(a.location), city_key(b.location)
    if city_a and city_b:
        return city_a == city_b and (title_a == title_b or SequenceMatcher(None, title_a, title_b).ratio() >= TITLE_SIMILARITY)
    return title_a == title_b   # a city is unknown: only an exact title is trusted


def resolve_with_index(jobs: list[JobPosting], index: list[tuple[str, JobPosting]],
                       aliases: dict[str, str]) -> tuple[list[JobPosting], list[tuple[str, JobPosting]]]:
    """Fold listings that match exactly one official index job into it.

    `index` holds (index key, official job); `aliases` maps company_key() of a
    name or alias to the company_key() of its canonical name. A matched
    listing is replaced by the official job (its URL and source-based status)
    with the listing added to sources[]; an ambiguous or unmatched listing is
    kept as it is. Returns (jobs, [(index key, merged listing)]).
    """
    by_company: dict[str, list[tuple[str, JobPosting]]] = {}
    by_url: dict[str, tuple[str, JobPosting]] = {}
    for key, official in index:
        by_company.setdefault(company_key(official.company), []).append((key, official))
        url = canonical_url(official.application_url)
        if url:
            by_url[url] = (key, official)
    merged: dict[str, JobPosting] = {}
    order: list[str | JobPosting] = []
    matches: list[tuple[str, JobPosting]] = []
    for job in jobs:
        hit = by_url.get(canonical_url(job.application_url))
        if hit is None:
            name = company_key(job.company)
            candidates = [(key, official) for key, official in by_company.get(aliases.get(name, name), []) if same_job(job, official)]
            hit = candidates[0] if len(candidates) == 1 else None
        if hit is None:
            order.append(job)
            continue
        key, official = hit
        if key not in merged:
            merged[key] = official.model_copy(deep=True)
            order.append(key)
        target = merged[key]
        if job is not official and job.source != official.source:
            ref = JobSourceRef(source=job.source or 'Unknown', url=str(job.application_url) if job.application_url else None,
                               source_job_id=job.source_job_id)
            if ref not in target.sources:
                target.sources.append(ref)
                matches.append((key, job))
    return [merged[item] if isinstance(item, str) else item for item in order], matches
