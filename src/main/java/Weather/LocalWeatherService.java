package Weather;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class LocalWeatherService {
    public static String getLocalWeather() throws IOException, InterruptedException {
        // Open-Meteo API
        String lat = System.getenv("WEATHER_LAT");
        String lon = System.getenv("WEATHER_LON");

        // Providing sensible defaults for Denver if env vars are missing
        lat = (lat != null) ? lat : "39.7392";
        lon = (lon != null) ? lon : "-104.9903";

        String url = String.format(
                "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=temperature_2m",
                lat, lon
        );
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder().uri(URI.create(url)).build();

        // Standard synchronous approach to store output as a String
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());

        return  response.body();
    }
}
