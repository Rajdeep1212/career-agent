from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from typing import Literal

WorkMode = Literal["remote", "hybrid", "onsite"]
VerificationState = Literal["ACTIVE_VERIFIED", "LIKELY_ACTIVE", "UNVERIFIED", "CLOSED"]


def _all_work_modes() -> list[WorkMode]:
    return ["remote", "hybrid", "onsite"]


class CandidateProfile(BaseModel):
    schema_version: int = 2
    name: str | None = None
    graduation_year: int | None = None
    degree: str | None = None
    skills: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    research: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    preferred_roles: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    internships: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    skill_categories: dict[str, list[str]] = Field(default_factory=dict)
    domain_knowledge: list[str] = Field(default_factory=list)
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    experience_years: float | None = Field(default=None, ge=0, le=70)
    experience_level: str = "unknown"
    work_mode_preferences: list[WorkMode] = Field(default_factory=_all_work_modes)
    relocation_preference: bool | None = None
    parsing_warnings: list[str] = Field(default_factory=list)


class JobSearchPreferences(BaseModel):
    allowed_work_modes: list[WorkMode] = Field(default_factory=_all_work_modes)
    preferred_locations: list[str] = Field(default_factory=list)
    max_required_experience_years: float = 1.0
    allow_zero_to_two_when_fresher_friendly: bool = True
    required_graduation_year: int | None = None
    exclude_batch_years: list[int] = Field(default_factory=list)
    exclude_2026_only: bool = False
    require_fresher_or_recent_grad_language: bool = True
    require_active_application: bool = False
    prefer_official_application_url: bool = True
    deduplicate: bool = True
    preferred_role_families: list[str] = Field(default_factory=list)
    minimum_match_score: float = Field(default=0, ge=0, le=100)
    search_query_limit: int = Field(default=4, ge=4, le=10)
    verification_limit: int = Field(default=20, ge=1, le=50)


class CandidateProfileUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, max_length=150)
    graduation_year: int | None = Field(default=None, ge=1950, le=2100)
    degree: str | None = Field(default=None, max_length=200)
    skills: list[str] | None = None
    projects: list[str] | None = None
    research: list[str] | None = None
    preferred_locations: list[str] | None = None
    preferred_roles: list[str] | None = None
    education: list[str] | None = None
    experience: list[str] | None = None
    internships: list[str] | None = None
    certifications: list[str] | None = None
    skill_categories: dict[str, list[str]] | None = None
    domain_knowledge: list[str] | None = None
    evidence: dict[str, list[str]] | None = None
    experience_years: float | None = Field(default=None, ge=0, le=70)
    experience_level: Literal['unknown', 'fresher', 'entry_level', 'experienced'] | None = None
    work_mode_preferences: list[Literal['remote', 'hybrid', 'onsite']] | None = Field(default=None, min_length=1)
    relocation_preference: bool | None = None
    parsing_warnings: list[str] | None = None


class JobSearchPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allowed_work_modes: list[Literal['remote', 'hybrid', 'onsite']] | None = Field(default=None, min_length=1)
    preferred_locations: list[str] | None = None
    max_required_experience_years: float | None = Field(default=None, ge=0, le=70)
    allow_zero_to_two_when_fresher_friendly: bool | None = None
    required_graduation_year: int | None = Field(default=None, ge=1950, le=2100)
    exclude_batch_years: list[int] | None = None
    exclude_2026_only: bool | None = None
    require_fresher_or_recent_grad_language: bool | None = None
    require_active_application: bool | None = None
    prefer_official_application_url: bool | None = None
    deduplicate: bool | None = None
    preferred_role_families: list[str] | None = None
    minimum_match_score: float | None = Field(default=None, ge=0, le=100)
    search_query_limit: int | None = Field(default=None, ge=4, le=10)
    verification_limit: int | None = Field(default=None, ge=1, le=50)


class JobSourceRef(BaseModel):
    """Another listing of the same job, e.g. an aggregator copy of an official posting."""
    source: str
    url: str | None = None
    source_job_id: str | None = None


class JobPosting(BaseModel):
    company: str
    title: str
    location: str
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    experience_min: float = 0
    experience_max: float | None = None
    fresher_allowed: bool = False
    recent_graduate_allowed: bool = False
    graduation_years: list[int] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    description: str = ""
    application_url: HttpUrl | None = None
    application_status: Literal["active", "closed", "unverified"] = "unverified"
    source: str | None = None
    official_application: bool = False
    posted_date: str | None = None
    source_job_id: str | None = None
    employment_type: str | None = None
    salary: str | None = None
    education_requirements: list[str] = Field(default_factory=list)
    industry: str | None = None
    verification_state: VerificationState = "UNVERIFIED"
    verification_reason: str = "Not checked yet."
    verification_checked_at: str | None = None
    sources: list[JobSourceRef] = Field(default_factory=list)  # other listings merged into this one



class JobScore(BaseModel):
    company: str
    title: str
    location: str
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"]
    application_url: str | None = None
    source: str | None = None
    posted_date: str | None = None
    official_application: bool = False
    total_score: float
    skill_score: float
    eligibility_score: float
    role_relevance_score: float
    location_score: float
    project_relevance_score: float
    application_quality_score: float
    matched_skills: list[str]
    missing_skills: list[str]
    eligible: bool
    eligibility_status: Literal["eligible", "uncertain", "excluded"] = "eligible"
    eligibility_summary: str = ""
    reasons: list[str]


class RankJobsRequest(BaseModel):
    profile: CandidateProfile
    jobs: list[JobPosting]
    preferences: JobSearchPreferences | None = None


class EmailDraftRequest(BaseModel):
    recipient: str
    subject: str
    body: str
    attachment_path: str | None = None


class EmailSendRequest(EmailDraftRequest):
    user_approved: bool = False


class CreateEmailDraftRequest(BaseModel):
    recipient: str
    subject: str
    body: str
    attachment_id: str | None = None


class EmailDraftRecord(BaseModel):
    id: int
    recipient: str
    subject: str
    body: str
    attachment_id: str | None = None
    status: Literal["draft", "approved", "sent", "cancelled"]
    created_at: str
    approved_at: str | None = None
    sent_at: str | None = None
    gmail_message_id: str | None = None


class AgentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    include_seen: bool = False
    session_id: str | None = None
    strict_mode: bool | None = None


class SearchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)
    career_session_id: str | None = Field(default=None, max_length=128)
    strict_mode: bool | None = None


class PrepareJobEmailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient: str
    company: str
    title: str
    application_url: str | None = None
    attachment_id: str | None = None
    custom_note: str | None = None
    job_id: str | None = Field(default=None, min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    application_id: str | None = Field(
        default=None,
        pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
    )
    contact_id: str | None = Field(
        default=None,
        pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
    )
