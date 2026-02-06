FROM maven:3.9.6-eclipse-temurin-21
WORKDIR /app
COPY target/super_bot-1.0-SNAPSHOT.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
ENV WEATHER_LAT="39.7392"
ENV WEATHER_LON="-104.9903"
