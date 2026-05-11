#!/usr/bin/env python3
"""Render the HyperPersona production-architecture diagram via mmdc and open the PNG.

Captures *what is deployed where*, not request flow (see render_cloud_flow.py for that).
Verified against live AWS resources via aws-cli on 2026-05-07:
  - Frontend on Vercel (Vite SPA + /api/* rewrite to CloudFront)
  - AWS edge: CloudFront EJKSBPO8V4E0M -> ALB hyperpersona-alb
  - ECS Fargate cluster `hyperpersona`: server svc (1 task) + worker svc (4 tasks)
  - ECR: 3 image repositories
  - Messaging: SQS hyperpersona-jobs + ElastiCache Redis hyperpersona-redis-001
  - AI: Bedrock AgentCore (Firecracker microVM) + Bedrock foundation models
  - Persistence: DynamoDB (13 tables), OpenSearch Serverless, S3 traces bucket
  - Cross-cutting: Secrets Manager (4 secrets), IAM (task roles), CloudWatch Logs

Prereqs: Python 3, npx (ships with Node). First run caches mmdc + Chromium (~30s).
"""
import subprocess
import sys
from pathlib import Path

MERMAID = """flowchart TB
    subgraph CLIENT [Client]
        direction TB
        Browser[Browser]
    end

    subgraph FE [Vercel · frontend hosting]
        direction TB
        SPA[Vite React SPA<br/>hyperpersona-web.vercel.app]
        Rewrite[vercel.json<br/>rewrite /api/* to CloudFront]
    end

    subgraph EDGE [AWS edge · public ingress]
        direction TB
        CF[CloudFront EJKSBPO8V4E0M<br/>HTTPS terminator]
        ALB[ALB hyperpersona-alb<br/>HTTP target group]
    end

    subgraph CLUSTER [ECS Fargate cluster · hyperpersona]
        direction TB
        SrvSvc[server service<br/>desiredCount = 1<br/>FastAPI uvicorn]
        WkSvc[worker service<br/>desiredCount = 4<br/>SQS receive loop]
    end

    subgraph REG [ECR · container registry]
        direction TB
        ImgSrv[hyperpersona/server]
        ImgWk[hyperpersona/worker]
        ImgAC[bedrock-agentcore-<br/>hyperpersona_analyzer]
    end

    subgraph MSG [Messaging and cache]
        direction TB
        SQS[(SQS<br/>hyperpersona-jobs<br/>standard, 90s vis)]
        Redis[(ElastiCache Redis<br/>hyperpersona-redis-001<br/>offer cache + result channels)]
    end

    subgraph AI [AI / ML runtime]
        direction TB
        AC[Bedrock AgentCore<br/>hyperpersona_analyzer<br/>Firecracker microVM<br/>Strands extract_facts tool]
        Claude[Claude Sonnet 4.5<br/>generate]
        Titan[Titan v2 embed<br/>1024-dim]
    end

    subgraph DATA [Persistence]
        direction TB
        DDB[(DynamoDB · 13 tables<br/>events, consent, jobs, auth,<br/>profile, products, categories,<br/>reviews, votes, cart, wishlist,<br/>orders, product_catalog)]
        OS[(OpenSearch Serverless<br/>hyperpersona-vectors<br/>customer-facts<br/>behavior-embeddings<br/>session-summaries<br/>product-catalog)]
        S3T[(S3 · hyperpersona-traces<br/>per-job SQLite snapshots)]
    end

    subgraph SEC [Security]
        direction TB
        SM[(Secrets Manager<br/>jwt-secret, api-key,<br/>redis-auth, redis-url)]
        IAMSrv[(IAM · server task role<br/>+ execution role)]
        IAMWk[(IAM · worker task role<br/>+ execution role)]
    end

    subgraph OBS [Observability]
        direction TB
        CWLSrv[(CloudWatch<br/>/ecs/hyperpersona/server)]
        CWLWk[(CloudWatch<br/>/ecs/hyperpersona/worker)]
        CWLAC[(CloudWatch<br/>/aws/bedrock-agentcore/<br/>runtimes/hyperpersona_analyzer)]
    end

    %% User-visible request path (solid)
    Browser --> SPA
    SPA --> Rewrite
    Rewrite --> CF
    CF --> ALB
    ALB --> SrvSvc

    %% Image deploy lineage (dotted)
    SrvSvc -.pull image.-> ImgSrv
    WkSvc -.pull image.-> ImgWk
    AC -.pull image.-> ImgAC

    %% Messaging hand-offs (thick = workflow)
    SrvSvc ==>|enqueue| SQS
    SQS ==>|long-poll receive| WkSvc

    %% Cache + result channel (dotted = read/write)
    SrvSvc -.brpop result.-> Redis
    WkSvc -.push result.-> Redis
    SrvSvc -.cache offer.-> Redis

    %% Database access (dotted)
    SrvSvc -.read/write.-> DDB
    WkSvc -.read/write.-> DDB
    WkSvc -.KNN + upsert.-> OS
    WkSvc -.upload trace.-> S3T

    %% AI invocations (thick + dotted)
    WkSvc ==>|invoke runtime| AC
    WkSvc -.embed.-> Titan
    WkSvc -.generate.-> Claude
    AC -.generate.-> Claude

    %% Security (dotted)
    SrvSvc -.fetch secrets.-> SM
    WkSvc -.fetch secrets.-> SM
    SrvSvc -.assume.-> IAMSrv
    WkSvc -.assume.-> IAMWk

    %% Logs (dotted)
    SrvSvc -.stream logs.-> CWLSrv
    WkSvc -.stream logs.-> CWLWk
    AC -.stream logs.-> CWLAC

    classDef ui fill:#e8f5e9,stroke:#388e3c,color:#000
    classDef compute fill:#e3f2fd,stroke:#1976d2,color:#000
    classDef store fill:#f3e5f5,stroke:#7b1fa2,color:#000
    classDef workflow fill:#fff3e0,stroke:#f57c00,color:#000

    class Browser,SPA,Rewrite ui
    class CF,ALB,SrvSvc,WkSvc,AC,Claude,Titan,ImgSrv,ImgWk,ImgAC compute
    class SQS,Redis,DDB,OS,S3T,SM,IAMSrv,IAMWk,CWLSrv,CWLWk,CWLAC store

    style CLIENT fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style FE fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style EDGE fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style CLUSTER fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style REG fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style MSG fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style AI fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style DATA fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style SEC fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style OBS fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
"""

MMD = Path("/tmp/hyperpersona_cloud_architecture.mmd")
PNG = Path("/tmp/hyperpersona_cloud_architecture.png")


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
