"""
데이터 로더 서비스.

CSV 읽기 → 도메인으로 문서 변환 → 임베딩 텍스트 구성 → 인코딩 → OpenSearch 적재.
필드 매핑/정규화/임베딩 구성은 current_domain 이 담당한다.
"""
import csv
from typing import Generator

from app.core.config import settings
from app.domains import current_domain
from app.services.opensearch.embedding_service import embedding_service
from app.services.opensearch.index_service import index_service


class DataLoader:
    """CSV 데이터 로더 및 OpenSearch 적재"""

    def __init__(self):
        self.config = settings

    # ========================================================================
    # CSV 읽기
    # ========================================================================
    def read_csv(self, file_path: str = None) -> Generator:
        path = file_path or self.config.data_csv
        with open(path, mode="r", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            for row in reader:
                yield row

    # ========================================================================
    # 내부 문서 변환 + 임베딩 → bulk 액션 생성
    # ========================================================================
    def generate_actions(
        self,
        rows_generator: Generator,
        index_name: str = None,
        max_rows: int = None
    ) -> Generator:
        idx = index_name or self.config.opensearch.index_name

        processed = 0
        skipped_exclude = 0
        skipped_duplicate = 0
        seen_ids = set()

        for row in rows_generator:
            # 1. 원천 row → 내부 문서 (활성 도메인)
            doc = current_domain.to_document(row)
            if doc is None:
                skipped_exclude += 1
                continue

            # 2. 식별자 기준 중복 제거
            doc_id = doc["id"]
            if doc_id in seen_ids:
                skipped_duplicate += 1
                continue
            seen_ids.add(doc_id)

            # 3. 임베딩 텍스트 구성 (활성 도메인) → 인코딩
            embed_text = current_domain.compose_embedding_text(doc)
            vector = embedding_service.encode_passage(embed_text)

            processed += 1
            if processed % 1000 == 0:
                print(f"[진행] {processed:,}건 처리, 제외:{skipped_exclude:,}, 중복:{skipped_duplicate:,}")

            source = dict(doc)
            source[current_domain.vector_field] = vector

            yield {
                "_index": idx,
                "_id": doc_id,
                "_source": source,
            }

            if max_rows and processed >= max_rows:
                break

        print(f"[완료] 총 {processed:,}건 처리, 제외:{skipped_exclude:,}, 중복:{skipped_duplicate:,}")

    # ========================================================================
    # CSV → OpenSearch 적재
    # ========================================================================
    def load_to_opensearch(
        self,
        index_name: str = None,
        max_rows: int = None
    ) -> dict:
        print(f"[시작] 데이터 로딩... (max_rows={max_rows})")
        print(f"[파일] {self.config.data_csv}")

        rows = self.read_csv()
        actions = self.generate_actions(rows, index_name, max_rows)
        result = index_service.bulk_index(actions)

        print(f"[결과] success: {result['success']}, failed: {result['failed']}")
        return result


# 싱글톤 인스턴스
data_loader = DataLoader()
