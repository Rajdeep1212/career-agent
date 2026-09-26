"""Structured career-agent contracts; legacy profile/job fields stay supported."""
from typing import Literal
from pydantic import BaseModel, Field


class SearchIntent(BaseModel):
    roles_requested: list[str] = Field(default_factory=list)
    role_families: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    excluded_locations: list[str] = Field(default_factory=list)
    # True when locations were filled from saved preferences rather than the request.
    locations_from_preferences: bool = False
    remote_allowed: bool = True
    hybrid_allowed: bool = True
    onsite_allowed: bool = True
    graduation_year: int | None = None
    experience_min: float | None = Field(default=None, ge=0)
    experience_max: float | None = Field(default=None, ge=0)
    fresher_preference: bool | None = None
    company_preferences: list[str] = Field(default_factory=list)
    industry_preferences: list[str] = Field(default_factory=list)
    minimum_match_score: float = Field(default=0, ge=0, le=100)
    freshness_preference: str | None = None
    internship_allowed: bool = True
    contract_allowed: bool = True
    non_coding: bool = False
    strict_mode: bool = False
    cv_discovery: bool = False
    filter_only: bool = False
    warnings: list[str] = Field(default_factory=list)


class RoleSuggestion(BaseModel):
    family: str
    titles: list[str]
    reason: str
    evidence: list[str] = Field(default_factory=list)
    suitability: Literal["strong", "possible", "exploratory"] = "possible"


class SearchQuery(BaseModel):
    query: str
    reason: str
    role: str
    location: str


EligibilityStatus = Literal["eligible", "uncertain", "excluded"]


class EligibilityEvidence(BaseModel):
    """One eligibility decision and the listing text it rests on."""
    outcome: EligibilityStatus
    reason: str
    quote: str | None = None

    def describe(self) -> str:
        """e.g. "excluded: quoted '5+ years experience required' (requires at least 5 years; ...)"."""
        return f"{self.outcome}: quoted '{self.quote}' ({self.reason})" if self.quote else f"{self.outcome}: {self.reason}"


class EligibilityResult(BaseModel):
    """Three-way eligibility; a deterministic heuristic (claim level L0), not a prediction.

    excluded  = an explicit disqualifier in the listing or an explicit user constraint.
    uncertain = ambiguous or missing evidence; shown to the user, ranked below eligible.
    eligible  = positive evidence and nothing unresolved.
    """
    status: EligibilityStatus = "eligible"
    eligible: bool = True  # compatibility: False only when status == "excluded"
    summary: str = ""
    evidence: list[EligibilityEvidence] = Field(default_factory=list)
    claim_level: Literal["L0"] = "L0"
    confidence: Literal["high", "medium", "low"] = "medium"
    hard_rejections: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    experience_match: str = "unknown"
    graduation_match: str = "unknown"


class MatchResult(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    skill_score: int = 0
    role_score: int = 0
    experience_score: int = 0
    location_score: int = 0
    matched_skills: list[str] = Field(default_factory=list)
    transferable_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    explanation: str
    components: dict[str, int] = Field(default_factory=dict)


ApplicationStatus = Literal["DISCOVERED", "SAVED", "APPLIED", "OUTREACH_PREPARED", "OUTREACH_SENT", "INTERVIEW", "REJECTED", "OFFER", "SKIPPED"]


class ContactCandidate(BaseModel):
    name: str | None = None
    role: str | None = None
    company: str
    contact_method: str
    public_source: str
    confidence: Literal["user_provided", "public_verified", "unverified"] = "user_provided"
