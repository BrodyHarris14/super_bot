# super_bot

A thin Python (Flask) router that fronts external requests and dispatches them
to backing microservices on the home server. Currently it proxies GPT-2 text
generation requests to [`ml-runner`](../discord_gptbot/ml-runner) and exposes
a local-weather lookup.

## Manages interactions with the OUT OF OFFICE discord server

``` mermaid
flowchart LR
subgraph Available_Commands
    gpt_command(["GPT Bot Command 
    /gpt {count} {set} {prefix}"])

    server_command(["Server Bot Command 
    /server {action}"])

    football_command(["Sport Bot - Football Command 
    /football"])
    basketball_command(["Sport Bot - Basketball Command 
    /basketball"])
    hockey_command(["Sport Bot - Hockey Command 
    /hockey"])

    splain_command(["Splain Dat Command 
    /splain"])
    end
    subgraph Sports_Channels
    football("#football")
    hockey("#hockey")
    basketball("#basketball")
end
subgraph Home Server
 subgraph super_bot
  handle["Handle command"]

  gpt_endpoint>"GPT Endpoint 
  /gpt?...&...&...&..."]

  server_endpoint>"Server Endpoint 
  /server?..."]

  sport_endpoint>"Sports Endpoint 
  /sport?sport=...
  (Routes based on sport)"]

  splain_endpoint>"Splain Endpoint 
  /splain?..."]
 end
 gpt_bot
 splain_dat
 football_microservice
 basketball_microservice
 hockey_microservice
end

server_command <--> server_endpoint <--> handle
gpt_command <--> gpt_endpoint <---> gpt_bot
football_command <--> sport_endpoint <---> football_microservice
basketball_command <--> sport_endpoint <---> basketball_microservice
hockey_command <--> sport_endpoint <---> hockey_microservice
splain_command <--> splain_endpoint  <---> splain_dat

hockey_microservice --game start--> hockey
basketball_microservice --game start--> basketball
football_microservice --game start--> football
hockey_microservice --game end--> hockey
basketball_microservice --game end--> basketball
football_microservice --game end--> football

classDef highlight stroke:#ffd900, fill:#6e5d00
class super_bot highlight

classDef discord stroke:#5865F2, fill:#5865F255
class Discord,Available_Commands,Sports_Channels discord

classDef default stroke:black, fill:black, color:white
```

### Implemented endpoints

| Endpoint | Method | Upstream | Notes |
| - | - | - | - |
| `/health` | GET | — | Liveness + pings `ML_RUNNER_URL` |
| `/gpt` | GET / POST | `ml-runner` `/generate` | Same params/forms as `/generate` |
| `/localWeather` | GET | Open-Meteo | Carried over from the Java skeleton |

### `/gpt` parameters

Identical to ml-runner's `/generate`. Any of these forms work:

- `GET /gpt?set=trump-tweet&prefix=hello&async=true`
- `POST /gpt` with `Content-Type: application/json`: `{"set": "...", "prefix": "...", "async": true}`
- `POST /gpt` with form-encoded body

`set` is required; `prefix` defaults to `""`; `async` enables async mode
(`"1"`, `"true"`, or `"yes"`). Sync requests return plain text; async
requests return `202` JSON with a `job_id`.

## Performs smart server reboots based on ongoing jobs
