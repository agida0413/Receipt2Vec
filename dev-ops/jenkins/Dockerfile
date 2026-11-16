# Receipt2Vec CI/CD 용 Jenkins
#   - Docker CLI(+compose, buildx): 호스트의 Docker 데몬(/var/run/docker.sock)으로 이미지 빌드
#   - AWS CLI v2: ECR 로그인·저장소 생성
#   - ssh/rsync: RAG 서버 배포
#   - 플러그인·설정은 이미지와 JCasC(casc/jenkins.yaml)로 코드화 → 설치 마법사 없음
FROM jenkins/jenkins:2.580.2-lts-jdk21

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg unzip rsync openssh-client git jq \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin docker-compose-plugin \
 && ARCH=$(uname -m) \
 && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${ARCH}.zip" -o /tmp/awscli.zip \
 && unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/awscli.zip \
 && rm -rf /var/lib/apt/lists/*

USER jenkins
COPY --chown=jenkins:jenkins plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

# 설치 마법사 생략 (관리자 계정·설정은 JCasC 가 만듦)
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Duser.timezone=Asia/Seoul" \
    CASC_JENKINS_CONFIG=/var/jenkins_conf/jenkins.yaml
