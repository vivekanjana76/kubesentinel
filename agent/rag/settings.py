from pydantic_settings import BaseSettings, SettingsConfigDict


class RagSettings(BaseSettings):
    supabase_url: str
    supabase_service_role_key: str
    # Direct Postgres connection — used by migrate.py for DDL execution.
    # Find in: Supabase Dashboard > Project Settings > Database > Connection string (URI).
    database_url: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = RagSettings()  # type: ignore[call-arg]
