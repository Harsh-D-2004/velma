from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VELMA_", env_file=".env", extra="ignore")

    database_url: str = ""
    database_direct_url: str = ""

    max_article_chars: int = 2_000_000
    max_concurrent_jobs: int = 5
    max_job_seconds: int = 1800
    max_steps: int = 60
    max_html_bytes: int = 1_000_000
    retry_pause_seconds: int = 120

    scratch_dir: str = "./scratch"

    s3_endpoint_url: str | None = None
    archive_bucket: str = ""
    cdn_bucket: str = ""
    cdn_base_url: str = ""

    tavily_api_key: str = ""


settings = Settings()
