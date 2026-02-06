import Weather.LocalWeatherService;
import io.vertx.core.AbstractVerticle;
import io.vertx.core.Vertx;
import io.vertx.ext.web.Router;

public class Main extends AbstractVerticle {
  @Override
  public void start() {
    // Create a router
    Router router = Router.router(vertx);

    // Define a route
    router.get("/test").handler(ctx -> {
      System.out.println("GOT REQUEST");
      ctx.response()
          .putHeader("content-type", "text/plain")
          .end("Hello!");
    });

    // Define the Weather route
      router.get("/localWeather").handler(ctx -> {
          System.out.println("got local weather request");
          try {
              String weather = LocalWeatherService.getLocalWeather();
              ctx.response()
                      .putHeader("content-type", "text/plain")
                      .end(weather);
          } catch (Exception e){
              ctx.response()
                      .putHeader("content-type", "text/plain")
                      .setStatusCode(500)
                      .end();
          }
      });

    // Start an HTTP server
    vertx.createHttpServer()
        .requestHandler(router)
        .listen(8080, http -> {
          if (http.succeeded()) {
            System.out.println("HTTP server started on port 8080");
          } else {
            System.err.println("Failed to start HTTP server: " + http.cause());
          }
        });
  }

  public static void main(String[] args) {
    Vertx vertx = Vertx.vertx();
    vertx.deployVerticle(new Main());
  }
}