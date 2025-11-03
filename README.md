# super_bot

The Java brains of the discord microservices endeavors. It:

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

### Command overview

| Command | Uses | Handled By|
| - | - | - |
| | | |

## Performs smart server reboots based on ongoing jobs
