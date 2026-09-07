"""Process configuration for one memory_service scope."""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for one tenant/organization scope.

    ``db_connect_timeout``（秒）是**每一次建连的期限**，不是查询超时。它必须有值：
    libpq 默认没有期限，而「连不上」在生产里最常见的样子不是被拒绝，是**丢包后一直等**
    （地址写错、防火墙静默丢弃、库过载）。那时每一次请求占住一个线程池线程不放，
    健康检查探针尤其致命 —— 它按固定频率来，几十次之后整个 adapter 就没有线程可用了，
    而每一条请求都还「正在处理中」，日志里一行错都没有。见 ``deps.get_conn``。
    """

    model_config = SettingsConfigDict(extra="forbid")

    memory_tenant: str
    memory_org: str
    database_url: str = ""
    adapter_auth: str = ""
    viewer_user_id: str = ""
    memory_embedding_api_key: str = ""
    memory_embedding_base_url: str = ""
    memory_embedding_model: str = ""
    memory_embedding_dim: int = 2048
    db_connect_timeout: int = 5

    @model_validator(mode="after")
    def _normalise_and_validate(self) -> "Settings":
        self.memory_tenant = self.memory_tenant.strip()
        self.memory_org = self.memory_org.strip()
        self.adapter_auth = self.adapter_auth.strip()
        self.viewer_user_id = self.viewer_user_id.strip()
        self.database_url = self.database_url.strip()
        self.memory_embedding_api_key = self.memory_embedding_api_key.strip()
        self.memory_embedding_base_url = self.memory_embedding_base_url.strip()
        self.memory_embedding_model = self.memory_embedding_model.strip()

        if not self.memory_tenant or not self.memory_org:
            raise ValueError("MEMORY_TENANT 和 MEMORY_ORG 不能为空：不能使用空 scope")
        if not self.database_url:
            raise ValueError("DATABASE_URL 未设置：adapter 无法连接 memory_service 数据库")
        if self.memory_embedding_dim <= 0:
            raise ValueError("MEMORY_EMBEDDING_DIM 必须大于 0")
        if self.db_connect_timeout <= 0:
            raise ValueError("DB_CONNECT_TIMEOUT 必须大于 0：没有期限的连接会把请求挂住")
        return self

    @property
    def embedding_configured(self) -> bool:
        """Whether the complete adapter-owned embedding configuration is present."""
        return bool(
            self.memory_embedding_api_key
            and self.memory_embedding_base_url
            and self.memory_embedding_model
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load process settings once; dependency overrides can replace this in tests."""
    return Settings()
