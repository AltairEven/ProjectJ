from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROJECT_J_", env_file=None, extra="forbid")
    project_root: Path = Path(__file__).resolve().parents[5]
    run_id: str = "local-simulation"
    # Relative to project_root; an absolute path replaces it. The store still requires a
    # directory named "simulation".
    simulation_root: Path = Path("project_j/private/simulation")
    realm: Literal["simulation"] = "simulation"
    provider: Literal["simulation"] = "simulation"
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = 8765
    classroom_model: Literal["gpt-6.1-sol"] = "gpt-6.1-sol"
    classroom_effort: Literal["low"] = "low"
    preparation_effort: Literal["medium"] = "medium"
    evaluation_effort: Literal["medium"] = "medium"
