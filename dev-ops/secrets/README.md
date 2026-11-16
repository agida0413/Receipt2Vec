# dev-ops/secrets (git 제외)

Jenkins 컨테이너의 `/run/secrets/<파일명>` 으로 마운트되고, JCasC 가 Jenkins Credentials 로 등록한다.
`../scripts/init-secrets.sh` 가 이 파일들을 만든다. 서버로는 `../scripts/upload-secrets.sh` 로 복사(scp)한다.

| 파일 | Jenkins Credential ID | 내용 |
|---|---|---|
| jenkins_admin_password | (관리자 로그인) | Jenkins admin 비밀번호 (자동 생성) |
| dockerhub_password | dockerhub | Docker Hub 비밀번호 또는 Access Token |
| rag_server_ssh_key | rag-server-ssh | RAG 서버 EC2 키페어 (.pem 내용) — 직접 넣어야 함 |
| aws_access_key_id / aws_secret_access_key | aws-credentials | AWS IAM 키 |
| github_token | github | 비공개 저장소일 때 GitHub PAT (공개면 비워 둠) |
| slack_webhook_url | slack-webhook | 배포 알림 |
| app_root_env | app-root-env | 운영 루트 `.env` (MySQL/Redis 비밀번호, 공개 주소) |
| app_rag_backend_env | app-rag-backend-env | 운영 `rag_backend/.env` (Gemini 키) |
| app_ocr_backend_env | app-ocr-backend-env | 운영 `ocr_backend/.env` |
| app_auth_server_env | app-auth-server-env | 운영 `auth_server/.env` (OAuth 키) |

시크릿을 바꾼 뒤에는 `docker compose up -d --force-recreate jenkins` (JCasC 가 다시 읽음).
