FROM maven:3.9-eclipse-temurin-21

COPY seed /tmp/shi-agent-seed
RUN mvn -B -f /tmp/shi-agent-seed/pom.xml -Dmaven.repo.local=/opt/maven-repository test \
    && chmod -R a+rX /opt/maven-repository

ENV MAVEN_OPTS="-Dmaven.repo.local=/opt/maven-repository -Duser.home=/tmp"
WORKDIR /workspace
ENTRYPOINT []
