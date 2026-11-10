"""
도메인 패키지 컴포지션 루트.

.env 의 ACTIVE_DOMAIN 값으로 레지스트리에서 도메인 구현체를 선택해
current_domain 으로 노출한다. 서비스 계층은 current_domain 을 import 해서 사용한다.

  from app.domains import current_domain
  doc = current_domain.to_document(row)
"""
from app.core.config import settings
from app.domains.base import DomainSpec
from app.domains.registry import DOMAIN_REGISTRY, load_domain

# 활성 도메인 인스턴스
current_domain: DomainSpec = load_domain(settings.current_domain)

__all__ = [
    "DomainSpec",
    "current_domain",
    "load_domain",
    "DOMAIN_REGISTRY",
]
