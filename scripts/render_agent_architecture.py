#!/usr/bin/env python3
"""HyperPersona full agent architecture slide (Authrex style, 1920x1080 PPT).

Layout — single horizontal chain so the whole pipeline fits one row, with
3 storage chips branching downward off Embedder + Status Updater:

  Frontend Submit -> SQS -> Worker -> Privacy Guard -> Strands Analyzer
                                   -> Fact Distiller -> Embedder -> Status
                                                            |          |
                                                  behaviour /  customer  S3
                                                  embeddings    facts    audit

Uses the ELK layout engine (built into mermaid-cli) for clean orthogonal
routing. Renders at high resolution, then computes the correct scale-to-fit
factor (so taller-than-16:9 sources don't get cropped) and pads to exactly
1920 x 1080 with white background.
"""
import subprocess
import sys
from pathlib import Path

MERMAID = """%%{init: {"flowchart": {"defaultRenderer": "elk", "nodeSpacing": 40, "rankSpacing": 60}}}%%
flowchart LR
    I1[<b>Frontend Submit</b><br/>JWT + consent<br/>sliding-window<br/>rate limit]
    I2[<b>SQS Queue</b><br/>hyperpersona-jobs<br/>standard · 90s vis<br/>20s long-poll]
    I3[<b>Worker</b><br/>30s budget<br/>deterministic job_id<br/>idempotent]
    P1[<b>Privacy Guard</b><br/>regex pre-LLM<br/>emails · phones<br/>names redacted]
    P2[<b>Strands Analyzer</b><br/>sonnet 4.5<br/>AgentCore<br/>Firecracker microVM]
    P3[<b>Fact Distiller</b><br/>sonnet 4.5<br/>polarity in -1 · 0 · +1]
    P4[<b>Embedding Indexer</b><br/>titan v2<br/>1024-dim<br/>unit-norm vector]
    P5[<b>Status Updater</b><br/>DDB processed<br/>jobs=completed<br/>SQS ack]
    ST1[<b>behaviour-embeddings</b><br/>raw event vector<br/>OpenSearch]
    ST2[<b>customer-facts</b><br/>fact + polarity<br/>OpenSearch]
    ST3[<b>S3 audit trail</b><br/>SQLite snapshot<br/>daemon thread]

    I1 ==> I2
    I2 ==> I3
    I3 ==> P1
    P1 --> P2
    P2 --> P3
    P3 --> P4
    P4 --> P5
    P4 -.upsert raw.-> ST1
    P4 -.upsert facts.-> ST2
    P5 -.snapshot.-> ST3

    classDef gateway fill:#ffffff,stroke:#37474f,stroke-width:2px,color:#000
    classDef agent fill:#ffffff,stroke:#0d47a1,stroke-width:2px,color:#000
    classDef behStore fill:#2e7d32,stroke:#1b5e20,color:#fff,stroke-width:2px
    classDef factStore fill:#ef6c00,stroke:#bf360c,color:#fff,stroke-width:2px
    classDef auditStore fill:#6a1b9a,stroke:#4a148c,color:#fff,stroke-width:2px

    class I1,I2,I3 gateway
    class P1,P2,P3,P4,P5 agent
    class ST1 behStore
    class ST2 factStore
    class ST3 auditStore

    style LEFT fill:#f5f7fa,stroke:#bbbbbb,stroke-dasharray: 4 4
    style MIDDLE fill:#eef5fb,stroke:#bbbbbb,stroke-dasharray: 4 4
    style RIGHT fill:#f5f7fa,stroke:#bbbbbb,stroke-dasharray: 4 4
"""

MMD = Path("/tmp/hyperpersona_agent_architecture.mmd")
RAW = Path("/tmp/hyperpersona_agent_architecture_raw.png")
PNG = Path("/tmp/hyperpersona_agent_architecture.png")

# PowerPoint 16:9 widescreen — 1920 x 1080.
SLIDE_W = 1920
SLIDE_H = 1080


def _png_dims(path: Path) -> tuple[int, int]:
    """Return (width, height) of a PNG via sips."""
    out = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    w = h = 0
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("pixelWidth:"):
            w = int(line.split(":", 1)[1])
        elif line.startswith("pixelHeight:"):
            h = int(line.split(":", 1)[1])
    return w, h


def main() -> int:
    MMD.write_text(MERMAID)
    # Render at native size and high scale — every pixel is real diagram
    # detail. PowerPoint can resize the image when you drop it onto a slide.
    cmd = [
        "npx", "--yes", "-p", "@mermaid-js/mermaid-cli", "mmdc",
        "-i", str(MMD), "-o", str(PNG),
        "-w", "6000", "-H", "2000", "-s", "3", "-b", "white",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"mmdc failed: {result.stderr}", file=sys.stderr)
        return 1

    final_w, final_h = _png_dims(PNG)
    print(f"Saved {PNG} ({PNG.stat().st_size:,} bytes)  native {final_w}x{final_h}")
    print("PowerPoint: drop the image, then drag the corner to resize. The")
    print("diagram is naturally horizontal so it fills the slide width well.")
    subprocess.run(["open", str(PNG)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
