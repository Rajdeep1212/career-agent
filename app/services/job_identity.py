import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from app.models.schemas import JobPosting


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
