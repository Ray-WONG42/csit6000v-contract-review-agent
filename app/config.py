from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    app_data: str = "data"
    app_demo: bool = True
    app_secure_cookie: bool = False
    app_public_url: str = "http://127.0.0.1:8765"
    model_mode: Literal["demo", "tacc", "ollama"] = "demo"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma4:12b"
    ollama_timeout: int = 180
    ollama_context_limit: int = 16384
    ollama_max_output_tokens: int = 1800
    contract_vector_search: bool | None = None
    ollama_embedding_model: str = "bge-m3:latest"
    ollama_embedding_timeout: int = 30
    admin_email: str = ""
    admin_password: str = ""
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_mailbox: str = ""
    graph_refresh_token: str = ""
    graph_poll: int = 20
    tacc_url: str = ""
    tacc_api_key: str = ""
    tacc_model: str = ""
    tacc_timeout: int = 90
    tacc_json_mode: bool = False
    tacc_context_limit: int = 16384
    tacc_max_output_tokens: int = 1800
    tacc_protocol: str = "chat"
    tacc_poll_url: str = ""
    tacc_disable_thinking: bool = False
    tacc_sdk_timeout: int = 720
    allowed_email_domains: str = ""
    brave_search_key: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    model_name: str = ""
    llm_temperature: float = 0
    agent_tools: str = "web_search,read_webpage,list_contracts,read_contract,review_contract,list_skills,save_skill,create_document"
    agent_short_term_memory: bool = True

    @property
    def data(self):
        path = Path(self.app_data)
        return path if path.is_absolute() else ROOT / path


settings = Settings()
