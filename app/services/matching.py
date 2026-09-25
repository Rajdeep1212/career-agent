"""Transparent heuristic scores, not probabilities of getting hired."""
import re
from datetime import datetime, timezone
from app.models.career import MatchResult

STOP={'engineer','junior','senior','jobs','job','role','roles','and','the','of','in'}
# Whole-token synonyms; substring replacement turned "Qatar" into "testingtar".
SYNONYMS={'qa':'testing','tester':'testing','testers':'testing','analyst':'analysis','analysts':'analysis'}


def words(text):
    tokens=re.findall(r'[a-z0-9+#]+',text.lower())
    return {SYNONYMS.get(token,token) for token in tokens}-STOP


def match_job(profile, job, intent, eligibility):
    candidate={s.casefold():s for s in profile.skills}
    required={s.casefold():s for s in job.skills}
    matched=[candidate[s] for s in candidate if s in required]
    missing=[required[s] for s in required if s not in candidate]
    skill=round(35*len(matched)/len(required)) if required else 0
    title_words=words(job.title)
    targets=intent.roles_requested or profile.preferred_roles
    role=max((round(25*len(words(t)&title_words)/max(1,len(words(t)))) for t in targets),default=0)
    if not targets:
        role=min(15,5*len(matched))
    transfer=[]
    if title_words&{'testing','test','quality'}:
        transfer=[s for s in profile.skills if s.casefold() in ('python','postman','rest api','sql','api testing') and s not in matched]
    elif title_words&{'analysis','analytics','business','operations'}:
        transfer=[s for s in profile.skills if s.casefold() in ('python','sql','excel','power bi','tableau','communication') and s not in matched]
    transfer_score=min(5,len(transfer)*2)
    experience=10 if eligibility.experience_match=='match' else 5 if eligibility.experience_match=='possible' else 0
    location=10 if (job.work_mode=='remote' and intent.remote_allowed) or any(x.lower() in job.location.lower() for x in intent.locations or profile.preferred_locations) else 3
    relevant=[p for p in profile.projects+profile.research if len(words(p)&words(job.title+' '+job.description))>=2]
    project=min(5,len(relevant)*3)
    education=5 if eligibility.graduation_match=='match' else 0
    verification=5 if job.verification_state=='ACTIVE_VERIFIED' or job.application_status=='active' else 2 if job.verification_state=='LIKELY_ACTIVE' else 0
    freshness=0
    if job.posted_date:
        try:
            posted=datetime.fromisoformat(job.posted_date.replace('Z','+00:00'))
            if posted.tzinfo is None:posted=posted.replace(tzinfo=timezone.utc)
            age=(datetime.now(timezone.utc)-posted).days
            freshness=5 if 0<=age<=7 else 2 if 0<=age<=30 else 0
        except ValueError:pass
    parts=dict(skills=skill,role=role,transferable=transfer_score,experience=experience,location=location,
               projects_research=project,education=education,verification=verification,freshness=freshness)
    strengths=([f'Matches: {", ".join(matched)}'] if matched else [])+([f'Transferable: {", ".join(transfer)}'] if transfer else [])+eligibility.positive_signals
    if relevant:strengths.append('Relevant candidate evidence: '+relevant[0][:180])
    gaps=([f'Not shown in profile: {", ".join(missing)}'] if missing else [])+eligibility.warnings
    if not required:gaps.append('Listing has no recognized skill requirements; skill fit is unknown.')
    total=min(100,sum(parts.values())) if eligibility.eligible else 0
    return MatchResult(overall_score=total,skill_score=skill,role_score=role,experience_score=experience,
        location_score=location,matched_skills=matched,transferable_skills=transfer,missing_skills=missing,
        strengths=strengths,gaps=gaps,components=parts,
        explanation=f'Heuristic fit {total}/100 based on available evidence. '+(' '.join(strengths[:2]) or 'Limited matching evidence; review the listing.'))
