# Plain slim base, with Chromium installed by Playwright itself at build time.
#
# The obvious alternative - Microsoft's mcr.microsoft.com/playwright/python
# image - couples two versions that then drift: the image tag pins the browser
# binaries, while an unpinned `playwright` in requirements.txt installs whatever
# is newest on top of them. Letting `playwright install` fetch the browsers means
# the library and the binaries always match, whatever version resolves.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["python", "webapp.py"]
