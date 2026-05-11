#!/usr/bin/env python3
"""Render the HyperPersona event-ingest pipeline (S-1..S-7) at PPT 16:9 size.

Layout: two horizontal rows folded inside an outer TB chart so the 7 stages
fill a 1920x1080 PowerPoint slide canvas instead of becoming a thin strip.
Row 1: S-1 -> S-2 -> S-3 -> S-4
Row 2: S-5 -> S-6 -> S-7
The cross-row hand-off (S-4 Redact -> S-5 Claude) becomes the visible bend.

Output: /tmp/hyperpersona_ingest_pipeline.png (exactly 1920 x 1080 px,
PowerPoint 16:9 widescreen, ready to drop into a slide).
"""
import subprocess
import sys
from pathlib import Path

MERMAID = """flowchart TB
    subgraph ROW1 [ ]
        direction LR

        subgraph S1 [S-1 · Frontend ingest]
            direction TB
            POST[POST /events/batch<br/>JWT + consent gate]
            Slide[sliding-window<br/>per-customer rate limit]
            DDBwrite[batch write events<br/>DynamoDB · 25 max per call]
            SQSpush[push jobs to SQS<br/>10 msg per batch]
        end

        subgraph S2 [S-2 · Job queue]
            direction TB
            SQS[(SQS hyperpersona-jobs<br/>standard · 90s vis<br/>20s long-poll)]
        end

        subgraph S3 [S-3 · Worker pickup · 30s budget]
            direction TB
            WkPop[worker.pop<br/>SQS receive_message]
            Disp[dispatch process_event<br/>kick off pipeline]
            Idem{deterministic<br/>job_id seen?}
            Pipeline[Strands agentic pipeline<br/>privacy / analyzer / storage]
        end

        subgraph S4 [S-4 · Privacy gate]
            direction TB
            PII{sensitive personal<br/>data detected?}
            Redact[regex redaction<br/>emails · phones · names]
        end
    end

    subgraph ROW2 [ ]
        direction LR

        subgraph S5 [S-5 · Fact extraction]
            direction TB
            Claude[Bedrock InvokeModel API<br/>Claude Sonnet 4.5]
            Facts[atomic facts<br/>polarity in -1 · 0 · +1]
        end

        subgraph S6 [S-6 · Embeddings]
            direction TB
            Titan[Bedrock Titan v2 embed<br/>1024-dim unit-norm vector]
            Beh[(behaviour-embeddings<br/>raw event)]
            CFacts[(customer-facts<br/>each extracted fact)]
        end

        subgraph S7 [S-7 · Status update + cleanup]
            direction TB
            DDBst[DynamoDB<br/>event=processed<br/>jobs=completed]
            SQSack[SQS delete_message<br/>ack receipt handle]
            S3sync[daemon thread<br/>S3 audit · SQLite snapshot]
        end
    end

    POST --> Slide
    Slide --> DDBwrite
    DDBwrite --> SQSpush
    SQSpush ==> SQS

    SQS ==> WkPop
    WkPop --> Disp
    Disp --> Idem
    Idem -->|new job| Pipeline
    Idem -.duplicate skip.-> SQSack

    Pipeline --> PII
    PII -->|yes| Redact
    PII -->|no| Claude
    Redact --> Claude

    Claude --> Facts
    Facts --> Titan
    Titan -.upsert raw event.-> Beh
    Titan -.upsert each fact.-> CFacts

    Titan --> DDBst
    DDBst --> SQSack
    DDBst -.snapshot trace.-> S3sync

    classDef ui fill:#e8f5e9,stroke:#388e3c,color:#000
    classDef compute fill:#e3f2fd,stroke:#1976d2,color:#000
    classDef workflow fill:#fff3e0,stroke:#f57c00,color:#000
    classDef decision fill:#fff9c4,stroke:#f9a825,color:#000
    classDef store fill:#f3e5f5,stroke:#7b1fa2,color:#000

    class POST ui
    class WkPop,Disp,Pipeline,Redact,Claude,Facts,Titan compute
    class Slide,DDBwrite,SQSpush,DDBst,SQSack,S3sync workflow
    class Idem,PII decision
    class SQS,Beh,CFacts store

    style ROW1 fill:transparent,stroke:transparent
    style ROW2 fill:transparent,stroke:transparent

    style S1 fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style S2 fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style S3 fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style S4 fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style S5 fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style S6 fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
    style S7 fill:#ffffff,stroke:#aaaaaa,stroke-dasharray: 4 4
"""

MMD = Path("/tmp/hyperpersona_ingest_pipeline.mmd")
RAW = Path("/tmp/hyperpersona_ingest_pipeline_raw.png")
PNG = Path("/tmp/hyperpersona_ingest_pipeline.png")

# PowerPoint 16:9 widescreen: 13.333" x 7.5" = 1920 x 1080 px at high DPI.
SLIDE_W = 1920
SLIDE_H = 1080


def main() -> int:
    MMD.write_text(MERMAID)
    cmd = [
        "npx", "--yes", "-p", "@mermaid-js/mermaid-cli", "mmdc",
        "-i", str(MMD), "-o", str(RAW),
        "-w", "5000", "-H", "3000", "-s", "3", "-b", "white",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"mmdc failed: {result.stderr}", file=sys.stderr)
        return 1

    # Force exact 1920x1080 PPT canvas: scale to fit, then pad with white.
    subprocess.run([
        "sips", "-Z", str(max(SLIDE_W, SLIDE_H)),
        str(RAW), "--out", str(PNG),
    ], check=True, capture_output=True)
    subprocess.run([
        "sips", "--padToHeightWidth", str(SLIDE_H), str(SLIDE_W),
        "--padColor", "FFFFFF",
        str(PNG), "--out", str(PNG),
    ], check=True, capture_output=True)

    dims = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(PNG)],
        capture_output=True, text=True,
    ).stdout
    print(f"Saved {PNG} ({PNG.stat().st_size:,} bytes)")
    print(dims.strip())
    subprocess.run(["open", str(PNG)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
