from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROJECT_J_", env_file=None, extra="forbid")
    project_root: Path = Path(__file__).resolve().parents[5]
    run_id: str = "local-simulation"
    # Relative to project_root; an absolute path replaces it. The store still requires a
    # directory named "simulation".
    simulation_root: Path = Path("project_j/private/simulation")
    study_root: Path = Path("project_j/private/study")
    realm: Literal["simulation", "study"] = "simulation"
    onboarding: bool = False  # Simulation only: start at onboarding, as study always does.
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = 8765
    # Debug-only switches for the simulated provider and store; both default to off.
    model_delay: float = Field(default=0, ge=0)
    fail_once: str | None = Field(
        default=None,
        pattern=r"^(?:(?:[a-z_]+:)?(?:before_publish|before_commit|after_commit)|ledger|model)$",
    )
