"""
OpenSearch 인덱스 관리 서비스 (생성/삭제/벌크/통계).

인덱스 스키마(분석기/필드 매핑/벡터 설정)는 current_domain.build_index_body 가 만든다.
"""
from opensearchpy import helpers
from app.core.config import settings
from app.domains import current_domain
from app.services.opensearch.client import opensearch_client


class IndexService:
    """인덱스 생성/삭제/관리 서비스"""

    def __init__(self, client=None):
        self.client = client or opensearch_client
        self.config = settings

    # ========================================================================
    # 인덱스 생성 (스키마는 활성 도메인이 설계)
    # ========================================================================
    def build_index(self, index_name: str = None) -> bool:
        idx = index_name or self.config.opensearch.index_name

        # 인덱스 스키마(분석기/필드/벡터)는 전적으로 도메인이 설계한다.
        index_body = current_domain.build_index_body(self.config.embedding.dimension)

        if not self.client.indices.exists(index=idx):
            self.client.indices.create(index=idx, body=index_body)
            print(f"[Index] '{idx}' 생성 완료")
            return True
        else:
            print(f"[Index] '{idx}' 이미 존재")
            return False

    # ========================================================================
    # 인덱스 존재 여부
    # ========================================================================
    def index_exists(self, index_name: str = None) -> bool:
        idx = index_name or self.config.opensearch.index_name
        return self.client.indices.exists(index=idx)

    # ========================================================================
    # 인덱스 삭제
    # ========================================================================
    def delete_index(self, index_name: str = None) -> bool:
        idx = index_name or self.config.opensearch.index_name
        if self.client.indices.exists(index=idx):
            self.client.indices.delete(index=idx)
            print(f"[Index] '{idx}' 삭제 완료")
            return True
        else:
            print(f"[Index] '{idx}' 존재하지 않음")
            return False

    # ========================================================================
    # 벌크 인덱싱
    # ========================================================================
    def bulk_index(self, actions, chunk_size: int = 100) -> dict:
        success, errors = helpers.bulk(
            self.client,
            actions,
            chunk_size=chunk_size,
            request_timeout=600,
            raise_on_error=False,
            stats_only=False
        )

        failed = len(errors)
        if failed > 0:
            print(f"[경고] {failed}건 인덱싱 실패!")
            for i, error in enumerate(errors[:5]):
                print(f"  - 에러 {i+1}: {error}")
            if len(errors) > 5:
                print(f"  - ... 외 {len(errors) - 5}건 추가 에러")

        return {"success": success, "failed": failed, "errors": errors}

    # ========================================================================
    # 인덱스 통계 조회
    # ========================================================================
    def get_index_stats(self, index_name: str = None) -> dict:
        idx = index_name or self.config.opensearch.index_name
        if not self.client.indices.exists(index=idx):
            return {"exists": False}

        stats = self.client.indices.stats(index=idx)
        return {
            "exists": True,
            "docs_count": stats["indices"][idx]["primaries"]["docs"]["count"],
            "size_bytes": stats["indices"][idx]["primaries"]["store"]["size_in_bytes"]
        }


# 싱글톤 인스턴스
index_service = IndexService()
