package Weather;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class LocalWeatherService {
    public static String getLocalWeather() throws IOException, InterruptedException {
        // Open-Meteo API endpoint for Denver, CO - totally static - yes I know
        String url = "https://api.open-meteo.com/v1/forecast?latitude=39.7392&longitude=-104.9903&current=temperature_2m";

        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder().uri(URI.create(url)).build();

        // Standard synchronous approach to store output as a String
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());

        return  response.body();
    }
}
