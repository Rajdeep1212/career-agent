"""Bounded service orchestration with structured state and auditable outcomes."""
import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections import Counter

from app.core.config import settings
from app.models.career import SearchIntent
from app.models.schemas import JobPosting
from app.providers.base import ProviderError
from app.providers.jsearch_provider import format_reset
from app.providers.registry import get_providers
from app.services.candidate_intelligence import analyze_candidate
from app.services.search_intent import interpret_search_request
from app.services.role_discovery import expand_roles, family_for_title
from app.services.search_planner import plan_search_queries
from app.services.job_identity import deduplicate_jobs, job_identity
from app.services.application_verifier import verify_application
from app.services.eligibility import evaluate_eligibility
from app.services.matching import match_job
from app.storage import career_store, history
from app.storage.profile_store import load_profile
from app.storage.preference_store import preferences_for

logger=logging.getLogger(__name__)


def _public(response):
    return {key:value for key,value in response.items() if not key.startswith('_')}


def _summary(diagnostics):
    d=diagnostics
    return (f"{d['provider_results']} provider results, {d['unique_jobs']} unique jobs; "
            f"{d['already_seen']} already seen, {d['closed']} closed, "
            f"{d['eligibility_rejected']} rejected by eligibility, "
            f"{d.get('intent_rejected',0)} outside requested roles, "
            f"{d.get('below_match_threshold',0)} below the match threshold. "
            f"{d['final_recommendations']} recommendations. "
            + (f"Provider errors: {'; '.join(d['errors'][:3])}." if d['errors'] else
               "Broaden the role/location filters or include seen jobs if needed." if not d['final_recommendations'] else
               "Review evidence and application status before applying."))


def _provider_error_message(name, exc):
    """e.g. "JSearch/RapidAPI: HTTP 429 quota or rate limit exceeded, resets 01 Oct 2026 00:00 UTC"."""
    if exc.status_code is None:
        return f'{name}: {exc.reason or str(exc).rstrip(".")}'
    message=f'{name}: HTTP {exc.status_code} {exc.reason or "request failed"}'
    return message+(f', resets {format_reset(exc.reset_at)}' if exc.reset_at else '')


_PROFILE_LOCATION_REFERENCE = re.compile(
    r'\b(?:saved|preferred|profile)\s+locations?\b|'
    r'\bmy\s+(?:saved\s+|preferred\s+)?locations?\b|'
    r'\blocation\s+preferences?\b|'
    r'\blocations?\s+(?:saved|listed)\s+in\s+(?:my\s+)?profile\b',
    re.IGNORECASE,
)


def _mentions_profile_locations(value: str) -> bool:
    return bool(_PROFILE_LOCATION_REFERENCE.search(value))


def _distinct_locations(*groups: list[str]) -> list[str]:
    result = []
    seen = set()
    for group in groups:
        for value in group:
            cleaned = value.strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
    return result


class CareerAgent:
    def __init__(self, providers=None):
        self.providers=providers

    async def search(self, query, *, include_seen=False, session_id=None, strict_mode=None):
        started=time.monotonic()
        profile=analyze_candidate(load_profile())
        preferences=preferences_for(profile)
        previous=career_store.get_session(session_id) if session_id else None
        if session_id and not previous:
            raise ValueError('Search session was not found. Start a new search.')
        prior_intent=SearchIntent.model_validate(previous['intent']) if previous else None
        intent=interpret_search_request(query,prior_intent)
        if strict_mode is not None:intent.strict_mode=strict_mode
        if not previous and not intent.minimum_match_score:
            intent.minimum_match_score=preferences.minimum_match_score
        # Explicit profile preferences are defaults, not inferred qualifications.
        if _mentions_profile_locations(query):
            explicit = [location for location in intent.locations
                        if not _mentions_profile_locations(location)]
            prioritize_saved = bool(re.search(r'\bprioriti[sz]e\b', query, re.IGNORECASE))
            groups = (preferences.preferred_locations, explicit) if prioritize_saved else (
                explicit, preferences.preferred_locations)
            intent.locations = _distinct_locations(*groups)
        elif not previous and not intent.locations:
            intent.locations=preferences.preferred_locations[:]
        profile_key=hashlib.sha256(profile.model_dump_json().encode()).hexdigest()
        session_id=session_id or str(uuid.uuid4())
        if intent.filter_only and previous and previous['response'].get('_profile_key')==profile_key:
            response=previous['response']
            ranked=response.get('_ranked_candidates',response.get('results',[]))
            response['results']=[r for r in ranked if r['total_score']>=intent.minimum_match_score][:50]
            response['result_count']=len(response['results'])
            response['intent']=intent.model_dump()
            d=response['diagnostics']
            d.update(reused_results=True,generated_queries=0,provider_requests=0,
                     below_match_threshold=sum(r['total_score']<intent.minimum_match_score for r in ranked),
                     final_recommendations=response['result_count'],latency_ms=round((time.monotonic()-started)*1000))
            response['summary']='Filtered stored recommendations without contacting providers. '+_summary(d)
            career_store.save_session(session_id,intent.model_dump(),response)
            return _public(response)
        roles=expand_roles(intent,profile)[:6]
        queries=plan_search_queries(profile,intent,roles,preferences.search_query_limit)
        providers=self.providers if self.providers is not None else get_providers()
        diagnostics=dict(generated_queries=len(queries),provider_requests=0,provider_results=0,
            unique_jobs=0,already_seen=0,active_verified=0,likely_active=0,unverified=0,closed=0,
            eligibility_rejected=0,intent_rejected=0,ranked_results=0,final_recommendations=0,
            below_match_threshold=0,provider_counts={},errors=[],provider_errors=[],skipped_requests=0,
            filtered_examples=[],reused_results=False)
        blocked=set()
        jobs=[]
        if not providers:diagnostics['errors'].append('No configured provider is available.')
        # One request per planned query, distributed across enabled providers.
        # No implicit retries or role × city × provider fan-out.
        for index,planned in enumerate(queries if providers else []):
            provider=providers[index%len(providers)]
            name=provider.name
            if name in blocked:
                diagnostics['skipped_requests']+=1
                continue
            diagnostics['provider_requests']+=1
            try:
                returned=await provider.search(planned.query)
                if not isinstance(returned,list):raise ValueError('Invalid provider result')
                diagnostics['provider_results']+=len(returned)
                diagnostics['provider_counts'][name]=diagnostics['provider_counts'].get(name,0)+len(returned)
                for raw in returned[:50]:
                    try:
                        job=raw.model_copy(deep=True) if isinstance(raw,JobPosting) else JobPosting.model_validate(raw)
                        if not job.source:job.source=name
                        jobs.append(job)
                    except (ValueError,TypeError):
                        diagnostics['errors'].append(f'{name}: a malformed listing was skipped.')
            except ProviderError as exc:
                message=_provider_error_message(name,exc)
                if message not in diagnostics['errors']:
                    diagnostics['errors'].append(message)
                    diagnostics['provider_errors'].append(dict(provider=name,status_code=exc.status_code,
                        reason=exc.reason,reset_at=exc.reset_at,remaining=exc.remaining,limit=exc.limit))
                # Credential and quota failures repeat for every query; stop spending requests.
                if exc.status_code in (401,403,429) or exc.status_code is None and exc.reason is None:
                    blocked.add(name)
            except Exception:
                message=f'{name}: request unavailable; check provider configuration/access/quota.'
                if message not in diagnostics['errors']:diagnostics['errors'].append(message)
        unique=deduplicate_jobs(jobs)
        diagnostics['unique_jobs']=len(unique)
        unseen=[]
        for job in unique:
            if not include_seen and history.is_seen(job):diagnostics['already_seen']+=1
            else:unseen.append(job)
        semaphore=asyncio.Semaphore(max(1,min(8,settings.verification_concurrency)))
        async def verify(job):
            async with semaphore:
                return await verify_application(job)
        budget=preferences.verification_limit
        checked=await asyncio.gather(*(verify(job) for job in unseen[:budget]))
        for job in unseen[budget:]:
            job.verification_state='UNVERIFIED';job.application_status='unverified'
            job.verification_reason='Not checked: this search reached the configured verification budget.'
        checked+=unseen[budget:]
        diagnostics['verification_attempted']=min(len(unseen),budget)
        counts=Counter(job.verification_state for job in checked)
        for state in ('ACTIVE_VERIFIED','LIKELY_ACTIVE','UNVERIFIED','CLOSED'):
            diagnostics[state.lower()]=counts[state]
        ranked=[]
        for job in checked:
            eligibility=evaluate_eligibility(profile,job,preferences,intent)
            match=match_job(profile,job,intent,eligibility)
            job_family=family_for_title(job.title)
            off_role=bool(intent.roles_requested and match.role_score==0
                          and not (job_family and job_family['family'] in intent.role_families))
            result={**job.model_dump(mode='json'), 'id':job_identity(job),
                'eligibility':eligibility.model_dump(),'match':match.model_dump(),
                'total_score':match.overall_score,'skill_score':match.skill_score,
                'matched_skills':match.matched_skills,'transferable_skills':match.transferable_skills,
                'missing_skills':match.missing_skills,'eligible':eligibility.eligible,
                'reasons':match.strengths+match.gaps,
                'next_action':'Review the active listing and apply manually.' if job.verification_state=='ACTIVE_VERIFIED' else
                              'Check application availability and requirements before applying.'}
            career_store.upsert_job(result)
            if not eligibility.eligible:
                if job.verification_state!='CLOSED':diagnostics['eligibility_rejected']+=1
                if len(diagnostics['filtered_examples'])<10:
                    diagnostics['filtered_examples'].append({'title':job.title,'reasons':eligibility.hard_rejections})
                continue
            if off_role:
                diagnostics['intent_rejected']+=1
                continue
            ranked.append(result)
        ranked.sort(key=lambda r:r['total_score'],reverse=True)
        results=[r for r in ranked if r['total_score']>=intent.minimum_match_score][:50]
        diagnostics.update(ranked_results=len(ranked),final_recommendations=len(results),
            below_match_threshold=sum(r['total_score']<intent.minimum_match_score for r in ranked),
            latency_ms=round((time.monotonic()-started)*1000))
        for result in results:history.mark_seen(JobPosting.model_validate(result))
        response=dict(query=query,provider=', '.join(p.name for p in providers) or 'None',
            profile=profile.model_dump(),preferences=preferences.model_dump(),session_id=session_id,
            intent=intent.model_dump(),role_suggestions=[r.model_dump() for r in roles],
            queries=[q.model_dump() for q in queries],diagnostics=diagnostics,
            results=results,result_count=len(results),verified_count=counts['ACTIVE_VERIFIED'],summary=_summary(diagnostics),
            _ranked_candidates=ranked,_profile_key=profile_key)
        career_store.save_session(session_id,intent.model_dump(),response)
        logger.info('career_search %s',json.dumps({'session_id':session_id,**{k:diagnostics[k] for k in
            ('generated_queries','provider_requests','provider_results','unique_jobs','eligibility_rejected','ranked_results','final_recommendations','latency_ms')}}))
        return _public(response)
