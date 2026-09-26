"""Company Radar configuration: the reviewed seed and the user's local copy.

The seed (app/core/radar/seed_vN.json) is versioned and never edited after
release. On first use it is copied to data/company_radar.json, which the user
owns: enable/disable entries, review them, add companies. The sync only uses
entries that are enabled, reviewed and have a source.
"""
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings

SEED_VERSION = 1
SEED_DIR = Path(__file__).resolve().parents[1] / "core" / "radar"
CONFIG_PATH = Path(settings.data_dir) / "company_radar.json"

SourceType = Literal["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "sitemap_jsonld", "undocumented_json", "none"]
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
    host: str | None = None             # workday: <tenant>.wdN.myworkdayjobs.com
    sites: list[str] = Field(default_factory=list)
    sitemap_capped: bool = False
    sitemaps: list[str] = Field(default_factory=list)
    job_url_pattern: str | None = None
    endpoint: str | None = None         # undocumented_json
    params: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _required_fields(self):
        required = {"greenhouse": ["board"], "ashby": ["board"], "lever": ["site"], "smartrecruiters": ["company"],
                    "workday": ["host", "sites"], "sitemap_jsonld": ["sitemaps"], "undocumented_json": ["endpoint"]}
        missing = [name for name in required.get(self.type, []) if not getattr(self, name)]
        if missing:
            raise ValueError(f"{self.type} source needs {', '.join(missing)}")
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


def load_config() -> RadarConfig:
    """The user's radar config; created from the seed on first use, never overwritten."""
    if not CONFIG_PATH.exists():
        config = load_seed()
        save_config(config)
        return config
    try:
        return RadarConfig.model_validate_json(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise RadarConfigError(f"{CONFIG_PATH.name} could not be read; fix or delete it to re-create it from the seed.") from exc


def syncable(config: RadarConfig) -> list[CompanyEntry]:
    return [company for company in config.companies
            if company.enabled and company.reviewed and company.source.type != "none"]
