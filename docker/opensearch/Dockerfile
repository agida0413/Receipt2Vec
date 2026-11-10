# OpenSearch + 한국어 형태소 분석기(analysis-nori)
# k-NN / neural-search(hybrid 쿼리, normalization-processor) 플러그인은 기본 번들에 포함되어 있음
ARG OPENSEARCH_VERSION=2.19.2
FROM opensearchproject/opensearch:${OPENSEARCH_VERSION}

RUN /usr/share/opensearch/bin/opensearch-plugin install --batch analysis-nori
