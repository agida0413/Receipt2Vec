"""
OpenSearch 클라이언트
"""
from opensearchpy import OpenSearch
from app.core.config import settings

    # ========================================================================
    # Opensearch와의 연결을 수립
    # ========================================================================

def create_opensearch_client() -> OpenSearch:
    """OpenSearch 클라이언트 생성"""
    config = settings.opensearch
    return OpenSearch(
        hosts=[{'host': config.host, 'port': config.port}],
        timeout=config.timeout
    )


# 싱글톤 클라이언트
opensearch_client = create_opensearch_client()
