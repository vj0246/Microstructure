"""Runtime settings, overridable with IMPACT_<FIELD> environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IMPACT_")

    # Outside the repo: the raw store holds gigabytes of small files that must not sync to OneDrive.
    data_dir: Path = Path.home() / "data" / "microstructure"
    request_interval_s: float = Field(default=1.0, ge=0.5)
    timeout_s: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=5, ge=0)
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"
