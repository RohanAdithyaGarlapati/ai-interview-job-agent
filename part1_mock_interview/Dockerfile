FROM python:3.12-slim

WORKDIR /app

# Model files for Silero VAD and the turn detector are baked into the image at
# build time. Downloading them on first boot instead would put ~30s in front of
# the first candidate to connect after a cold start.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python main.py download-files || true

ENV PORT=8080
EXPOSE 8080

# server.py serves the join page and supervises the agent worker as a child
# process. Render's free tier has no background workers, so both live here.
CMD ["python", "server.py"]
