FROM python:3.12-slim

# Node.js нужен для Claude Code CLI, который использует claude-agent-sdk;
# ffmpeg — для декодирования аудио при транскрибации.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates ffmpeg git \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

VOLUME /data
CMD ["python", "-m", "bot.main"]
