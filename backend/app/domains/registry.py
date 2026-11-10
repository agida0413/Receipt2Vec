"""
도메인 레지스트리 + 리졸버.

도메인 이름(.env 의 ACTIVE_DOMAIN) → DomainSpec 구현체 매핑.
새 도메인은 구현 클래스를 만든 뒤 DOMAIN_REGISTRY 에 등록한다.
"""
from typing import Dict, Type

from app.domains.base import DomainSpec
from app.domains.person import PersonDomain
from app.domains.animal import AnimalDomain


# 도메인 이름 → 구현 클래스
DOMAIN_REGISTRY: Dict[str, Type[DomainSpec]] = {
    "person": PersonDomain,
    "animal": AnimalDomain,
    # "mydomain": MyDomain,   # 새 도메인은 여기 한 줄만 추가
}


def load_domain(name: str) -> DomainSpec:
    """도메인 이름으로 활성 도메인 인스턴스를 생성한다."""
    try:
        domain_cls = DOMAIN_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"알 수 없는 도메인 '{name}'. 사용가능: {list(DOMAIN_REGISTRY)}"
        )
    return domain_cls()
