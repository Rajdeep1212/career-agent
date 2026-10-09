"""Company Radar configuration: the reviewed seed and the user's local copy.

The seed (app/core/radar/seed_vN.json) is versioned and never edited after
release. On first use it is copied to data/company_radar.json, which the user
owns: enable/disable entries, review them, add companies. The sync only uses
entries that are enabled, reviewed and have a source.

When a newer seed is released, the companies it adds are appended to the user's
file once (a backup is taken first); nothing the user has is changed or removed.
"""
import json
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings

SEED_VERSION = 2
SEED_DIR = Path(__file__).resolve().parents[1] / "core" / "radar"
CONFIG_PATH = Path(settings.data_dir) / "company_radar.json"

SourceType = Literal["greenhouse", "lever", "ashby", "smartrecruiters", "keka", "workday", "sitemap_jsonld", "undocumented_json", "none"]
Tag = Literal["big_tech", "gcc", "it_services", "ai_startup", "india_product"]
DEFAULT_INDIA_FILTER = r"India(?!na)|Bengaluru|Bangalore|Hyderabad|Pune|Chennai|Mumbai|Gurugram|Gurgaon|Noida|Delhi|Kolkata|Ahmedabad|Kochi|Jaipur|Coimbatore"


class RadarConfigError(Exception):
    """data/company_radar.json exists but cannot be read; it is never silently replaced."""


class SourceSpec(BaseModel):
    type: SourceType
    board: str | None = None            # greenhouse, ashby
    site: str | None = None             # lever
    region: Literal["global", "eu"] = "global"
    company: str | None = None          # smartrecruiters
    tenant: str | None = None           # keka: <tenant>.keka.com
    host: str | None = None             # workday: <tenant>.wdN.myworkdayjobs.com
    sites: list[str] = Field(default_factory=list)
    sitemap_capped: bool = False
    sitemaps: list[str] = Field(default_factory=list)
    job_url_pattern: str | None = None
    endpoint: str | None = None         # undocumented_json
    params: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _required_fields(self):
        required = {"greenhouse": ["board"], "ashby": ["board"], "lever": ["site"], "smartrecruiters": ["company"], "keka": ["tenant"],
                    "workday": ["host", "sites"], "sitemap_jsonld": ["sitemaps"], "undocumented_json": ["endpoint"]}
        missing = [name for name in required.get(self.type, []) if not getattr(self, name)]
        if missing:
            raise ValueError(f"{self.type} source needs {', '.join(missing)}")
        if self.type == "keka" and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.tenant or ""):
            raise ValueError("keka tenant must be the subdomain label of <tenant>.keka.com")
        if self.type == "workday" and not re.fullmatch(r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com", self.host or ""):
            raise ValueError("workday host must look like <tenant>.wdN.myworkdayjobs.com")
        return self


class Evidence(BaseModel):
    method: Literal["api_probe", "robots_sitemap", "manual", "sync"]
    checked_at: date
    total: int | None = None
    india: int | None = None
    note: str = ""


class CompanyEntry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    tags: list[Tag] = Field(min_length=1)
    careers_url: str | None = None
    source: SourceSpec
    india_filter: str | None = None
    evidence: Evidence
    enabled: bool = True
    reviewed: bool = False
    unofficial: bool = False

    @model_validator(mode="after")
    def _consistent(self):
        if self.source.type == "undocumented_json" and not self.unofficial:
            raise ValueError("undocumented sources must be marked unofficial")
        if self.source.type == "none" and self.enabled:
            raise ValueError("an entry without a source cannot be enabled")
        return self

    def india_pattern(self) -> re.Pattern:
        return re.compile(self.india_filter or DEFAULT_INDIA_FILTER, re.IGNORECASE)


class RadarConfig(BaseModel):
    version: Literal[1] = 1
    seed_version: int = SEED_VERSION
    companies: list[CompanyEntry]

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [company.id for company in self.companies]
        duplicates = sorted({company_id for company_id in ids if ids.count(company_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate company ids: {', '.join(duplicates)}")
        return self


def load_seed(version: int = SEED_VERSION) -> RadarConfig:
    data = json.loads((SEED_DIR / f"seed_v{version}.json").read_text(encoding="utf-8"))
    return RadarConfig(version=1, seed_version=version, companies=data["companies"])


def save_config(config: RadarConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(CONFIG_PATH)


def _stored() -> RadarConfig:
    try:
        return RadarConfig.model_validate_json(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise RadarConfigError(f"{CONFIG_PATH.name} could not be read; fix or delete it to re-create it from the seed.") from exc


def _with_new_seed_companies(config: RadarConfig) -> RadarConfig:
    """The config plus the companies a newer seed added. Existing entries, the user's edits and ids they already use stay as they are."""
    if config.seed_version >= SEED_VERSION:
        return config
    have = {company.id for company in config.companies}
    # Only what the newer seed added: a company the user removed from an older seed is not brought back.
    earlier = {company.id for company in load_seed(config.seed_version).companies}
    added = [company for company in load_seed().companies if company.id not in earlier and company.id not in have]
    return RadarConfig(version=1, seed_version=SEED_VERSION, companies=[*config.companies, *added])


def load_config() -> RadarConfig:
    """The user's radar config; created from the seed on first use, never overwritten.

    A file made from an older seed gains the newer seed's new companies once; the file is backed up
    first to <data dir>/backups/<UTC time>/company_radar.json."""
    if not CONFIG_PATH.exists():
        config = load_seed()
        save_config(config)
        return config
    config = _stored()
    if config.seed_version < SEED_VERSION:
        backup = CONFIG_PATH.parent / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") / CONFIG_PATH.name
        backup.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(CONFIG_PATH, backup)
        config = _with_new_seed_companies(config)
        save_config(config)
    return config


def read_config() -> RadarConfig:
    """The user's config if it exists (with a newer seed's new companies), else the seed; never writes a file."""
    return _with_new_seed_companies(_stored()) if CONFIG_PATH.exists() else load_seed()


def alias_map(config: RadarConfig) -> dict[str, str]:
    """company_key() of every name and alias -> company_key() of the canonical name."""
    from app.services.job_identity import company_key
    aliases: dict[str, str] = {}
    for company in config.companies:
        canonical = company_key(company.name)
        for name in [company.name, *company.aliases]:
            aliases.setdefault(company_key(name), canonical)
    return aliases


def syncable(config: RadarConfig) -> list[CompanyEntry]:
    return [company for company in config.companies
            if company.enabled and company.reviewed and company.source.type != "none"]
