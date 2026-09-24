"""健康检查与适配层的 503 不回显数据库错误原文：地址、端口、库名、用户名都只进服务端日志。"""
from __future__ import annotations

import logging

from fastapi import HTTPException
import pytest

from adapter.deps import get_conn
from adapter.settings import Settings as AdapterSettings
from memory_service_app.health import build_health_report
from memory_service_app.settings import Settings

UNREACHABLE = "postgresql://leaky_user@127.0.0.1:1/leaky_database"
DETAILS = ("127.0.0.1", "leaky_user", "leaky_database", "port 1", "connection")


def test_the_health_report_names_the_failure_without_connection_details(caplog):
    caplog.set_level(logging.WARNING)
    report = build_health_report(Settings(memory_tenant="t", memory_org="o", database_url=UNREACHABLE,
                                          db_connect_timeout=1))
    assert report.db is False and report.ok is False
    text = " ".join(report.warnings)
    assert "数据库不可达" in text and not any(detail in text for detail in DETAILS)
    assert any(record.name == "tkos.memory.health" and "127.0.0.1" in record.getMessage()
               for record in caplog.records)


def test_the_adapter_503_does_not_echo_the_database_error():
    dependency = get_conn(AdapterSettings(memory_tenant="t", memory_org="o", database_url=UNREACHABLE,
                                          db_connect_timeout=1))
    with pytest.raises(HTTPException) as caught:
        next(dependency)
    assert caught.value.status_code == 503
    assert not any(detail in str(caught.value.detail) for detail in DETAILS)
