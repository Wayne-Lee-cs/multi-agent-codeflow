FROM python:3.12-slim

# Install git (required for worktree operations) and Node.js (for claude CLI)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Install cagent
COPY . /tmp/cagent
RUN pip install --no-cache-dir /tmp/cagent && rm -rf /tmp/cagent

WORKDIR /workspace

# Usage:
#   docker build -t cagent .
#   docker run --rm -it \
#     -e ANTHROPIC_API_KEY=sk-... \
#     -v $(pwd):/workspace \
#     cagent cagent run tasks.md
