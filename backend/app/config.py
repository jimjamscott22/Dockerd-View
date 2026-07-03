"""Environment-driven settings for the collector and API."""
import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    docker_sock: str = "/var/run/docker.sock"
    sample_interval_ms: int = 1500
    hostname_label: str = socket.gethostname()
    allowed_origins: str = "http://localhost:5173,http://localhost:8000"
    port: int = 8000
    docker_data_root: str = "/var/lib/docker"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def sample_interval_sec(self) -> float:
        return self.sample_interval_ms / 1000.0


def get_settings() -> Settings:
    return Settings()
