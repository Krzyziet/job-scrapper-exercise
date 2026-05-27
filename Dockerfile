FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1 \
    libgtk-3-0 libasound2 libxrandr2 libxss1 libpangocairo-1.0-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxext6 libx11-6 \
    fonts-liberation ca-certificates wget --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . .

CMD ["python", "main.py"]
