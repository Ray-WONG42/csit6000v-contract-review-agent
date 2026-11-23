from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from engine import LEGACY_RULES, RULES


class EmailInput(BaseModel):
    language: Literal["zh", "en"] = "en"
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        import re
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮箱格式不正确")
        return value


class RegisterInput(EmailInput):
    password: str = Field(min_length=10, max_length=128)
    code: str = Field(pattern=r"^\d{6}$")


class LoginInput(EmailInput):
    password: str = Field(min_length=1, max_length=128)


class LanguageInput(BaseModel):
    language: Literal["zh", "en"]


class CustomItem(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("title", "question")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("自定义审查项需要标题和要求")
        return value


CategoryId = Literal["termination", "liability_cap", "governing_law", "automatic_renewal", "audit_rights", "assignment", "insurance", "agreement_date", "liquidated_damages"]


class SkillInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    categories: list[CategoryId] = Field(max_length=8)
    focus: str = Field(default="", max_length=1500)
    checks: dict[CategoryId, list[str]] = Field(default_factory=dict)
    custom_items: list[CustomItem] = Field(default_factory=list, max_length=6)
    policy: Literal["supplier-demo@1", "evidence-only@1"] = "supplier-demo@1"

    @field_validator("categories")
    @classmethod
    def category_values(cls, value):
        if len(set(value)) != len(value) or any(x not in RULES and x not in LEGACY_RULES for x in value):
            raise ValueError("审查类别无效")
        return value

    @field_validator("checks")
    @classmethod
    def checks_valid(cls, value):
        if any((key not in RULES and key not in LEGACY_RULES) or len(items) > 10
               or any(len(item) > 150 for item in items) for key, items in value.items()):
            raise ValueError("检查项无效")
        return value

    @model_validator(mode="after")
    def nonempty_skill(self):
        if not self.categories and not self.custom_items and not self.focus.strip():
            raise ValueError("请选择内置类别或填写自定义审查要求")
        return self


class JobInput(BaseModel):
    language: Literal["zh", "en"] = "en"
    instruction: str = Field(default="", max_length=4000)
    skill_id: str = Field(default="", max_length=64)
    parent_id: str = Field(default="", max_length=64)
    request_key: str = Field(min_length=8, max_length=100)
    model_mode: Literal["", "ollama", "tacc", "api"] = ""
    ollama_model: str = Field(default="", max_length=255)


class CompanyInput(BaseModel):
    company: str = Field(min_length=1, max_length=100)


class ReportInput(BaseModel):
    content: str = Field(min_length=1, max_length=100000)


class EventView(BaseModel):
    id: int
    stage: str
    title: str
    detail: str
    created: float


class EvidenceView(BaseModel):
    paragraph_id: str
    quote: str
    page: int | None = None
    retrieval_sources: list[Literal["keyword", "vector", "survey", "fallback", "adjacent"]] = Field(default_factory=list)


class RowView(BaseModel):
    category: str
    label: str
    status: Literal["found", "not_found", "review"]
    evidence: list[EvidenceView]
    missing: list[str]
    question: str
    assessment: str = "待核验"
    explanation: str = ""
    finding: str = ""
    reason: str = ""
    checklist: list[dict] = Field(default_factory=list)


class ResultView(BaseModel):
    state: str
    rows: list[RowView] = Field(default_factory=list)
    calls: list[dict] = Field(default_factory=list)
    message: str = ""
    answer: str = ""
    model_mode: str = ""
    model_name: str = ""
    tacc_client_job: str = ""
    seconds: float = 0


class JobView(BaseModel):
    id: str
    contract_id: str
    source: str
    state: str
    instruction: str
    created: float
    error: str
    report_file: str
    result: ResultView | None = None
    report: str = ""
    delivery: str = ""
    model_mode: str = ""
    model_name: str = ""


class ContractView(BaseModel):
    id: str
    name: str
    company: str
    file_id: str
    created: float
    owner_email: str = ""


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=5000)


class AssistantInput(BaseModel):
    language: Literal["zh", "en"] = "en"
    request_key: str = Field(min_length=8, max_length=100)
    message: str = Field(min_length=1, max_length=6000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    contract_id: str = Field(default="", max_length=64)
    allow_web: bool = True
    model_mode: Literal["", "ollama", "tacc", "api"] = ""
    ollama_model: str = Field(default="", max_length=255)

    @model_validator(mode="after")
    def context_budget(self):
        if sum(len(message.content) for message in self.history) > 16000:
            raise ValueError("对话上下文过长，请新建对话")
        return self
