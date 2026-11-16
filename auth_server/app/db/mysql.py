"""
MySQL 접속 + 스키마 생성 (schema.sql 을 기동 시 실행, IF NOT EXISTS 라 몇 번 실행해도 안전)
"""
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import settings

logger = logging.getLogger(__name__)
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_engine: Optional[Engine] = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        s = settings
        url = (f"mysql+pymysql://{quote_plus(s.mysql_user)}:{quote_plus(s.mysql_password)}"
               f"@{s.mysql_host}:{s.mysql_port}/{s.mysql_database}?charset=utf8mb4")
        _engine = create_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=1800)
    return _engine


def schema_statements(sql: str):
    """주석(--)을 지우고 ; 기준으로 문장 분리"""
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def init_schema(retries: int = 30, delay: float = 2.0) -> None:
    """MySQL 이 준비될 때까지 기다렸다가 스키마 적용 (컨테이너 동시 기동 대비)"""
    statements = schema_statements(SCHEMA_PATH.read_text(encoding="utf-8"))
    for attempt in range(1, retries + 1):
        try:
            with engine().begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
            tables = [re.search(r"EXISTS\s+(\w+)", s).group(1) for s in statements if "CREATE TABLE" in s]
            logger.info(f"MySQL 스키마 확인 완료 host={settings.mysql_host} db={settings.mysql_database} tables={tables}")
            return
        except Exception as e:  # noqa: BLE001
            if attempt == retries:
                raise
            logger.warning(f"MySQL 연결 대기 ({attempt}/{retries}) error={type(e).__name__}: {str(e)[:120]}")
            time.sleep(delay)
