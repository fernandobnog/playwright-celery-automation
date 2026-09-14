FROM python:3.11-slim-bookworm

# Prevent interactive prompts during apt installation
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99

# Install system dependencies: Xvfb, VNC, noVNC, window manager, and Chromium shared libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    ca-certificates \
    procps \
    net-tools \
    dumb-init \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fluxbox \
    # Chromium libraries
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Symlink noVNC default file so index.html loads vnc.html directly
RUN ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html || true

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser binaries
RUN playwright install chromium

# Copy entrypoint script and application code
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY . /app

# Default directories for runtime data and downloads
RUN mkdir -p /app/data/browser_profile /app/downloads

EXPOSE 8000 6080 5555

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/entrypoint.sh"]
