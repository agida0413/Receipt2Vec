# Receipt2Vec DevOps (Jenkins CI/CD)

서비스용 `docker-compose.yml` 과 별개인 **CI/CD 서버** 구성입니다.

```
GitHub (master) ──▶ Jenkins (13.125.111.48:8080)
                      ├ 이미지 빌드 (backend, ocr-backend, auth-server, frontend, opensearch)
                      ├ 단위 테스트 (rag·ocr·auth, 빌드한 이미지 안에서)
                      ├ Docker Hub 푸시  kimyong120413/reciept2vec:<서비스>-latest / <서비스>-<커밋>
                      └ SSH 배포 ──▶ RAG 서버 (15.164.106.172:80)
                                      docker compose pull (항상 최신) → up -d → 상태 확인
```

## 폴더
| 경로 | 내용 |
|---|---|
| `docker-compose.yml` | Jenkins 서버 (호스트 Docker 로 빌드, `jenkins-home` 볼륨) |
| `jenkins/` | Jenkins 이미지 (Docker CLI·AWS CLI·플러그인) |
| `casc/jenkins.yaml` | Jenkins 설정 코드 (관리자, Credentials, 파이프라인 작업) |
| `Jenkinsfile` | 빌드·테스트·푸시·배포 파이프라인 |
| `deploy/` | 운영 compose 오버라이드, 서버에서 실행되는 배포 스크립트 |
| `scripts/` | 시크릿 생성·업로드, 서버 초기 설정, 테스트·배포 보조 |
| `secrets/` | **시크릿 파일 (git 제외)** → Jenkins Credentials 로 자동 등록 |

## 시크릿 관리
- 키 값은 `secrets/` 파일에만 있고 git·Jenkinsfile·빌드 로그에 남지 않습니다.
- Jenkins 는 docker secret(`/run/secrets/*`)으로 받아 JCasC 가 Credentials 로 등록합니다.
  파이프라인은 `withCredentials` 로만 꺼내 쓰며 로그에서는 `****` 로 가려집니다.
- 운영 서버 환경 파일(`app_*_env`)은 배포할 때마다 권한 600 으로 서버에 복사됩니다.
- Docker Hub 로그인 정보는 빌드·배포 중에만 임시 폴더에 두고 바로 지웁니다.
- 목록과 Credential ID 는 `secrets/README.md`.

## 최초 설정
1. **RAG 서버** (한 번): `scripts/_install-docker.sh`, `scripts/bootstrap-rag-host.sh` 를 복사해 실행
   (Docker·Compose 2.24+·rsync 설치, OpenSearch 커널 설정, 스왑)
2. **개발 PC**: `secrets/rag_server_ssh_key` 에 RAG 서버 EC2 키페어(.pem) 내용 넣기
3. **Jenkins 서버**: `git clone https://github.com/agida0413/Receipt2Vec.git ~/Receipt2Vec`
4. **개발 PC**: `./scripts/upload-secrets.sh <Jenkins 서버 키.pem>`
5. **Jenkins 서버**: `cd ~/Receipt2Vec/dev-ops && bash scripts/bootstrap-jenkins-host.sh`
6. `http://13.125.111.48:8080` → `admin` / `secrets/jenkins_admin_password` → `receipt2vec-deploy` 실행

보안그룹: Jenkins 8080 은 관리자 IP 만, RAG 서버 22 는 Jenkins 서버만, RAG 서버 80 은 사용자에게.

## 사용
- `master` 에 푸시하면 5분 안에 자동 빌드·배포 (`.env` 의 `POLL_SCM`)
- 수동 실행 파라미터: `RUN_TESTS`, `PUSH_IMAGES`, `DEPLOY`
- **롤백**: `DEPLOY_ONLY=true`, `DEPLOY_TAG=<커밋 해시 12자리>`
- 시크릿 변경: `secrets/` 수정 → `docker compose up -d --force-recreate jenkins`
