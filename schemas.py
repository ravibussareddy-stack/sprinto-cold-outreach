"""Structured outputs. The model fills NAMED FIELDS; code (not the model)
checks which fields are filled to decide whether we know enough."""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    field: str
    quote: str = Field(description="Short verbatim quote from the page text")
    source_url: str


class CompanyProfile(BaseModel):
    company_name: Optional[str] = None
    one_line_description: Optional[str] = None
    is_b2b_saas: Optional[bool] = Field(None, description="True if it sells software (subscription/cloud) to businesses. null if the text does not make it clear.")
    buyer_type: Optional[str] = Field(None, description="Who buys the product, e.g. 'HR teams at mid-size companies'. null if not stated.")
    target_industries: list[str] = []
    handles_sensitive_data: Optional[bool] = Field(None, description="Processes customer PII, financial, health, HR or security data")
    sensitive_data_types: list[str] = []
    funding_stage: Optional[Literal["Bootstrapped", "Pre-seed", "Seed", "Series A", "Series B", "Series C", "Series D+", "Public", "Acquired"]] = Field(None, description="Only if explicitly stated or clearly implied (e.g. 'backed by X, raised $20M Series B'). Otherwise null.")
    funding_evidence: Optional[str] = None
    employee_estimate: Optional[int] = Field(None, description="Only if stated or inferable from text (team page, 'team of 80'). Otherwise null.")
    open_roles_count: Optional[int] = Field(None, description="Count of open roles if a careers/jobs listing is present, else null")
    security_compliance_roles_hiring: list[str] = Field([], description="Open roles related to security, compliance, GRC, IT, trust")
    has_enterprise_tier: Optional[bool] = Field(None, description="Pricing shows an Enterprise/custom plan. null if no pricing info seen.")
    enterprise_features: list[str] = Field([], description="e.g. SSO, SAML, SCIM, audit logs, SLA, data residency, DPA")
    named_customers: list[str] = Field([], description="Customer logos/names mentioned, especially large or enterprise brands")
    certifications_claimed: list[str] = Field([], description="Certifications the company says IT holds (SOC 2, ISO 27001, HIPAA...). Exclude mentions about customers or partners.")
    certification_in_progress: Optional[bool] = None
    geographic_markets: list[str] = []
    recent_triggers: list[str] = Field([], description="Recent events: funding round, new enterprise launch, expansion to US/EU, big customer win, new product")
    evidence: list[Evidence] = Field([], description="One quote per important field you filled")


class PainPoint(BaseModel):
    title: str
    why_this_person_feels_it: str
    evidence_from_research: str = Field(description="The specific fact from the company profile this pain point is grounded in")
    sprinto_angle: str = Field(description="How Sprinto specifically relieves it")


class PainPoints(BaseModel):
    pain_points: list[PainPoint] = Field(description="Exactly two, most likely first")


class EmailDraft(BaseModel):
    subject: str = Field(description="Under 8 words, lowercase-ish, no clickbait, no emojis")
    body: str = Field(description="Plain text, greets the person by first name, 70-120 words, one question CTA, signed by the sender name provided")
    hook_used: str = Field(description="The specific researched fact the opening line is built on")
    reasoning: str = Field(description="One or two sentences: why this angle for this person")
