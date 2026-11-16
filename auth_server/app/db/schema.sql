-- Receipt2Vec 인증 서버 스키마 (MySQL 8, utf8mb4)
-- MySQL 최초 기동 시 /docker-entrypoint-initdb.d 로 실행되고, 인증 서버 기동 시에도 다시 실행된다 (IF NOT EXISTS)
-- 토큰(리프레시·블랙리스트·OAuth state)은 만료가 있는 데이터라 Redis 에 둔다.

-- 서비스 사용자 (JWT sub = user_uid). 소셜 계정 여러 개가 한 사용자에 연결될 수 있다.
CREATE TABLE IF NOT EXISTS users (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    user_uid          CHAR(36)     NOT NULL COMMENT '외부 노출용 ID (JWT sub, 다른 서비스의 userId)',
    email             VARCHAR(255) NULL,
    name              VARCHAR(100) NULL,
    profile_image_url VARCHAR(500) NULL,
    role              VARCHAR(20)  NOT NULL DEFAULT 'USER' COMMENT 'USER / ADMIN',
    status            VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE / BLOCKED / WITHDRAWN',
    created_at        DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at        DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    last_login_at     DATETIME(3)  NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_uid (user_uid),
    KEY ix_users_email (email)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- 소셜 로그인 계정 (google / naver / kakao). (provider, provider_user_id) 가 계정의 고유 키
CREATE TABLE IF NOT EXISTS user_social_accounts (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    user_id           BIGINT       NOT NULL,
    provider          VARCHAR(20)  NOT NULL,
    provider_user_id  VARCHAR(191) NOT NULL COMMENT '제공자의 사용자 ID (google sub, naver id, kakao id)',
    email             VARCHAR(255) NULL,
    email_verified    TINYINT(1)   NOT NULL DEFAULT 0,
    name              VARCHAR(100) NULL,
    profile_image_url VARCHAR(500) NULL,
    raw_profile       JSON         NULL COMMENT '제공자가 준 프로필 원본 (토큰 제외)',
    connected_at      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_login_at     DATETIME(3)  NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_social_provider_user (provider, provider_user_id),
    KEY ix_social_user (user_id),
    CONSTRAINT fk_social_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- OAuth 제공자 설정 현황 (비밀값은 저장하지 않음 — 환경변수에만). 인증 서버 기동 시 갱신
CREATE TABLE IF NOT EXISTS oauth_providers (
    provider       VARCHAR(20)  NOT NULL,
    client_id      VARCHAR(255) NULL,
    redirect_uri   VARCHAR(500) NOT NULL,
    scopes         VARCHAR(255) NULL,
    enabled        TINYINT(1)   NOT NULL DEFAULT 0,
    updated_at     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (provider)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- 로그인 이력 (성공·실패 감사 로그)
CREATE TABLE IF NOT EXISTS login_history (
    id               BIGINT       NOT NULL AUTO_INCREMENT,
    user_id          BIGINT       NULL,
    provider         VARCHAR(20)  NOT NULL,
    provider_user_id VARCHAR(191) NULL,
    success          TINYINT(1)   NOT NULL,
    failure_reason   VARCHAR(255) NULL,
    ip               VARCHAR(64)  NULL,
    user_agent       VARCHAR(500) NULL,
    request_id       VARCHAR(64)  NULL,
    created_at       DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY ix_login_user_time (user_id, created_at),
    KEY ix_login_time (created_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;
