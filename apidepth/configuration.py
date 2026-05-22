from __future__ import annotations

from typing import Callable, Dict, List, Optional


class Configuration:
    def __init__(self) -> None:
        self.api_key: Optional[str] = None
        self.collector_url: Optional[str] = None
        self.enabled: bool = True
        self.flush_interval: int = 20
        self.registry_refresh_interval: int = 6 * 60 * 60
        self.registry_cache_path: str = "/tmp/apidepth_registry.json"
        self.ignored_hosts: List[str] = []
        self.on_flush_error: Optional[Callable] = None
        self.environment: Optional[str] = None
        self.sample_rate: float = 1.0
        self.extra_vendors: Dict[str, str] = {}
