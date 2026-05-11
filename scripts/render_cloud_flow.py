#!/usr/bin/env python3
"""Render the HyperPersona production cloud-flow diagram via mmdc and open the PNG.

Captures the *current deployed* architecture as observed via aws-cli + code reads:
  - Frontend on Vercel (HTTPS), API path rewritten to CloudFront
  - CloudFront (HTTPS terminator) -> ALB (HTTP) -> ECS Fargate server
  - Server enqueues to SQS, BRPOPs result channel on Redis (async)
  - 4 worker replicas long-poll SQS, dispatch by job_type
    * process_event high-signal -> AgentCore Firecracker microVM (analyzer step)
    * generate_recommendation     -> KNN + recommender + verifier (in-process)
    * generate_complement_recommendation -> cart hydrate + Bedrock JSON picks
  - All results land on Redis result:{job_id}; worker SQS-acks after handler

Prereqs: Python 3, npx (ships with Node). First run caches mmdc + Chromium (~30s).
"""
import subprocess
import sys
from pathlib import Path

MERMAID = """flowchart TB
    subgraph UI [Browser and frontend]
        direction TB
        Browser[Browser]
        Vercel[Vercel SPA<br/>hyperpersona-web.vercel.app]
    end

    subgraph EDGE [HTTPS edge layer]
        direction TB
        Rewrite[Vercel rewrite<br/>/api/* to CloudFront]
        CF[CloudFront<br/>EJKSBPO8V4E0M<br/>d1g772s2njto2q.cloudfront.net]
        ALB[ALB hyperpersona-alb<br/>HTTP :80 only]
    end

    subgraph SRV [ECS Fargate server x 1]
        direction TB
        SrvTask[server task<br/>FastAPI uvicorn]
        Cache{cache hit?}
        Enq[push job to SQS]
        Wait[await BRPOP<br/>on result channel]
    end

    subgraph QUEUE [Queue and result channels]
        direction TB
        SQS[(SQS<br/>hyperpersona-jobs<br/>standard, 90s visibility,<br/>20s long-poll)]
        ResultCh[(Redis<br/>result:job_id<br/>60s TTL)]
    end

    subgraph WK [ECS Fargate worker x 4]
        direction TB
        WkTask[worker task<br/>SQS receive loop]
        Disp{job_type?}
        PE[process_event<br/>privacy + AgentCore + writes]
        GR[generate_recommendation<br/>KNN + recommender + verifier]
        GC[generate_complement_recommendation<br/>cart hydrate + ranked picks]
        Ack[SQS delete_message<br/>ack after handler]
    end

    subgraph ACR [Bedrock AgentCore Runtime]
        direction TB
        AC[hyperpersona_analyzer<br/>Firecracker microVM]
        Tool[Strands Agent<br/>extract_facts tool]
    end

    subgraph BMR [Bedrock foundation models]
        direction TB
        Titan[Titan v2 embed<br/>1024-dim]
        Claude[Claude Sonnet 4.5<br/>generate]
    end

    subgraph STORE [Persistence]
        direction TB
        DDB[(DynamoDB<br/>13 tables<br/>events, consent, jobs,<br/>products, cart, orders, ...)]
        OS[(OpenSearch Serverless<br/>customer-facts<br/>behavior-embeddings<br/>session-summaries<br/>product-catalog)]
        Redis[(ElastiCache Redis<br/>offer cache, counters)]
        S3[(S3 hyperpersona-traces)]
    end

    %% UI to edge
    Browser --> Vercel
    Vercel --> Rewrite
    Rewrite --> CF
    CF --> ALB
    ALB --> SrvTask

    %% Server-side flow
    SrvTask --> Cache
    Cache -->|miss| Enq
    Cache -.hit.-> Redis
    Enq ==> SQS
    SrvTask --> Wait
    Wait -.brpop.-> ResultCh
    SrvTask -.put job and event.-> DDB

    %% Worker pulls jobs
    SQS ==> WkTask
    WkTask --> Disp
    Disp -->|process_event| PE
    Disp -->|generate_recommendation| GR
    Disp -->|generate_complement_recommendation| GC

    %% process_event branch
    PE ==> AC
    AC --> Tool
    Tool -.generate facts.-> Claude
    PE -.embed event.-> Titan
    PE -.embed facts.-> Titan
    PE -.upsert vectors.-> OS
    PE -.update status.-> DDB

    %% generate_recommendation branch
    GR -.embed query.-> Titan
    GR -.KNN search.-> OS
    GR -.generate offer.-> Claude
    GR -.verify offer.-> Claude
    GR ==> ResultCh

    %% generate_complement branch
    GC -.batch get cart.-> DDB
    GC -.scan candidates.-> DDB
    GC -.embed cart.-> Titan
    GC -.generate picks.-> Claude
    GC ==> ResultCh

    %% Worker cleanup
    WkTask ==> Ack
    Ack -.delete_message.-> SQS
    WkTask -.snapshot trace.-> S3

    classDef ui fill:#e8f5e9,stroke:#388e3c,color:#000
    classDef compute fill:#e3f2fd,stroke:#1976d2,color:#000
    classDef workflow fill:#fff3e0,stroke:#f57c00,color:#000
    classDef decision fill:#fff9c4,stroke:#f9a825,color:#000
    classDef store fill:#f3e5f5,stroke:#7b1fa2,color:#000

    class Browser,Vercel ui
    class Rewrite,CF,ALB,SrvTask,WkTask,AC,Tool,Titan,Claude,PE,GR,GC compute
    class Enq,Wait,Ack workflow
    class Cache,Disp decision
    class SQS,ResultCh,DDB,OS,Redis,S3 store

    style UI fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style EDGE fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style SRV fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style QUEUE fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style WK fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style ACR fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style BMR fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style STORE fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
"""

MMD = Path("/tmp/hyperpersona_cloud_flow.mmd")
PNG = Path("/tmp/hyperpersona_cloud_flow.png")


def main() -> int:
    MMD.write_text(MERMAID)
    cmd = [
        "npx", "--yes", "-p", "@mermaid-js/mermaid-cli", "mmdc",
        "-i", str(MMD), "-o", str(PNG),
        "-w", "4000", "-H", "5000", "-s", "2", "-b", "white",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"mmdc failed: {result.stderr}", file=sys.stderr)
        return 1
    print(f"Saved {PNG} ({PNG.stat().st_size:,} bytes)")
    subprocess.run(["open", str(PNG)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
