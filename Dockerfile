FROM maven:3.9.6-eclipse-temurin-21
WORKDIR /app
COPY target/super_bot-1.0-SNAPSHOT.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]