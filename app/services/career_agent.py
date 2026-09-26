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
from app.sources.adapters import RADAR_SOURCE
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
            f"{d['eligibility_rejected']} excluded by explicit disqualifiers, "
            f"{d.get('intent_rejected',0)} outside requested roles, "
            f"{d.get('below_match_threshold',0)} below the match threshold. "
            f"{d['final_recommendations']} recommendations "
            f"({d.get('eligible_results',0)} eligible, {d.get('uncertain_results',0)} uncertain, shown after eligible). "
            + (f"Provider errors: {'; '.join(d['errors'][:3])}." if d['errors'] else
               "Broaden the role/location filters or include seen jobs if needed." if not d['final_recommendations'] else
               "Review evidence and application status before applying."))


def _provider_error_message(name, exc):
    """e.g. "JSearch/RapidAPI: HTTP 429 quota or rate limit exceeded, resets 01 Oct 2026 00:00 UTC"."""
    if exc.status_code is None:
        return f'{name}: {exc.reason or str(exc).rstrip(".")}'
    message=f'{name}: HTTP {exc.status_code} {exc.reason or "request failed"}'
    return message+(f', resets {format_reset(exc.reset_at)}' if exc.reset_at else '')


def search_cost_warning(plan):
    """Why the dashboard should ask before running this search, or None."""
    requests=plan['provider_requests']
    shares=plan.get('requests_by_provider',{})
    parts=[]
    if requests>=settings.search_warn_requests:
        split=', '.join(f'{name} {count}' for name,count in shares.items())
        parts.append(f"This search will send {requests} requests ({split}).")
    for name,share in shares.items():
        quota=plan.get('quotas',{}).get(name) or {}
        remaining,limit=quota.get('remaining'),quota.get('limit')
        if not share or remaining is None:
            continue
        # A per-key lifetime allowance (Jooble's free plan) is worth flagging before it runs out.
        lifetime=quota.get('reset_at') is None and quota.get('counted_locally')
        if remaining>share and not (lifetime and limit and remaining<=limit*0.1):
            continue
        text=f"Only {remaining}"+(f" of {limit}" if limit else '')+f" {name} requests remain"
        text+=f" {quota['window']}" if quota.get('window') else ''
        text+=f", resetting {format_reset(quota['reset_at'])}" if quota.get('reset_at') else ''
        text+=' (counted on this machine)' if quota.get('counted_locally') else ''
        parts.append(text+'.')
    return ' '.join(parts) or None


_TIERS={'eligible':0,'uncertain':1}


def _rank_key(result):
    """Eligible before uncertain; higher heuristic score first within each tier."""
    return (_TIERS.get(result.get('eligibility_status','eligible'),1), -result['total_score'])


def _tier_counts(results):
    statuses=[r.get('eligibility_status','eligible') for r in results]
    return dict(eligible_results=statuses.count('eligible'),uncertain_results=statuses.count('uncertain'))


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


def _split(providers):
    """(local index providers, remote providers): local ones send no requests and skip the round-robin."""
    local=[p for p in providers if getattr(p,'local',False)]
    return local,[p for p in providers if not getattr(p,'local',False)]


def _preverified(job):
    """Radar jobs already carry today's source-based status; the page need not be fetched again."""
    return job.source==RADAR_SOURCE and job.verification_state in ('ACTIVE_VERIFIED','CLOSED')


class CareerAgent:
    def __init__(self, providers=None):
        self.providers=providers

    def _interpret(self, query, session_id, strict_mode):
        """Profile, preferences, stored session and structured intent; no side effects."""
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
            intent.locations_from_preferences = False
        elif not previous and not intent.locations:
            intent.locations=preferences.preferred_locations[:]
            intent.locations_from_preferences=bool(intent.locations)
        profile_key=hashlib.sha256(profile.model_dump_json().encode()).hexdigest()
        reuse=bool(intent.filter_only and previous and previous['response'].get('_profile_key')==profile_key)
        return profile,preferences,previous,intent,profile_key,reuse

    def plan(self, query, *, session_id=None, strict_mode=None):
        """The provider requests a search would send, without sending or storing anything."""
        profile,preferences,_previous,intent,_key,reuse=self._interpret(query,session_id,strict_mode)
        providers=self.providers if self.providers is not None else get_providers()
        if reuse or not providers:
            return dict(provider_requests=0,queries=[],providers=[p.name for p in providers],reuses_results=reuse)
        roles=expand_roles(intent,profile)[:6]
        queries=plan_search_queries(profile,intent,roles,preferences.search_query_limit)
        _local,remote=_split(providers)
        # Mirrors search(): one request per planned query, round-robin across remote providers.
        shares={}
        for index in range(len(queries) if remote else 0):
            name=remote[index%len(remote)].name
            shares[name]=shares.get(name,0)+1
        quotas={p.name:p.quota() for p in remote if callable(getattr(p,'quota',None))}
        return dict(provider_requests=len(queries) if remote else 0,queries=[q.query for q in queries],
                    providers=list(dict.fromkeys(p.name for p in providers)),requests_by_provider=shares,
                    quotas={name:quota for name,quota in quotas.items() if quota},reuses_results=False)

    async def search(self, query, *, include_seen=False, session_id=None, strict_mode=None):
        started=time.monotonic()
        profile,preferences,previous,intent,profile_key,reuse=self._interpret(query,session_id,strict_mode)
        session_id=session_id or str(uuid.uuid4())
        if reuse:
            response=previous['response']
            ranked=response.get('_ranked_candidates',response.get('results',[]))
            response['results']=[r for r in ranked if r['total_score']>=intent.minimum_match_score][:50]
            response['result_count']=len(response['results'])
            response['diagnostics'].update(_tier_counts(response['results']))
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
        local,remote=_split(providers)
        # Local indexes are read once for all planned queries; no requests are sent.
        for provider in local:
            try:
                found=provider.search_local(queries)
            except Exception:
                logger.exception('local provider %s failed',provider.name)
                diagnostics['errors'].append(f'{provider.name}: the local index could not be read.')
                continue
            diagnostics['provider_results']+=len(found)
            diagnostics['provider_counts'][provider.name]=diagnostics['provider_counts'].get(provider.name,0)+len(found)
            jobs.extend(job.model_copy(deep=True) for job in found)
        # One request per planned query, distributed across enabled remote providers.
        # No implicit retries or role × city × provider fan-out.
        for index,planned in enumerate(queries if remote else []):
            provider=remote[index%len(remote)]
            name=provider.name
            if name in blocked:
                diagnostics['skipped_requests']+=1
                continue
            diagnostics['provider_requests']+=1
            try:
                search_planned=getattr(provider,'search_planned',None)
                returned=await (search_planned(planned) if search_planned else provider.search(planned.query))
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
        preverified=[job for job in unseen if _preverified(job)]
        to_check=[job for job in unseen if not _preverified(job)]
        checked=list(await asyncio.gather(*(verify(job) for job in to_check[:budget])))
        for job in to_check[budget:]:
            job.verification_state='UNVERIFIED';job.application_status='unverified'
            job.verification_reason='Not checked: this search reached the configured verification budget.'
        checked+=to_check[budget:]+preverified
        diagnostics['verification_attempted']=min(len(to_check),budget)
        diagnostics['source_verified']=len(preverified)
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
                'eligibility_status':eligibility.status,'eligibility_summary':eligibility.summary,
                'reasons':match.strengths+match.gaps,
                'next_action':f'Check before applying: {eligibility.summary}' if eligibility.status=='uncertain' else
                              'Review the active listing and apply manually.' if job.verification_state=='ACTIVE_VERIFIED' else
                              'Check application availability and requirements before applying.'}
            career_store.upsert_job(result)
            if not eligibility.eligible:
                if job.verification_state!='CLOSED':diagnostics['eligibility_rejected']+=1
                if len(diagnostics['filtered_examples'])<10:
                    diagnostics['filtered_examples'].append({'title':job.title,'reasons':eligibility.hard_rejections,
                                                             'summary':eligibility.summary})
                continue
            if off_role:
                diagnostics['intent_rejected']+=1
                continue
            ranked.append(result)
        ranked.sort(key=_rank_key)
        results=[r for r in ranked if r['total_score']>=intent.minimum_match_score][:50]
        diagnostics.update(_tier_counts(results))
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
