# Playwright's own image already carries Chromium plus every system library it
# needs. Installing the browser onto a bare python-slim image instead means
# chasing a long list of shared-object dependencies, and the resulting image is
# no smaller once Chromium is in it.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["python", "webapp.py"]
