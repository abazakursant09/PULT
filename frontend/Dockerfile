FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293

# SECURITY-2D-3C3: disable Next telemetry (no runtime state writes, no telemetry network call).
ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

COPY package*.json ./
RUN npm ci

# Source is copied AS ROOT (no --chown): /app, node_modules and the built .next manifests/chunks stay
# root-owned and are only readable by the runtime `node` user — the app can never rewrite its own code.
COPY . .

# Backend API URL is inlined into the client bundle at BUILD time. Pass it as a
# build arg so the deployed frontend calls the real backend, never localhost:
#   docker build --build-arg NEXT_PUBLIC_API_URL=https://api.yourdomain.com .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# SECURITY-2D-3C3 — run as the image-native non-root `node` user (UID/GID 1000). The build runs as root so
# .next/server + .next/static + node_modules stay root-owned/read-only; only .next/cache is made writable
# for the runtime (next start's fetch/ISR cache). Only .next/cache is re-owned — never /app or the whole
# .next, and no world-writable modes. /home/node stays writable (image-native), recorded honestly by guards.
RUN npm run build \
    && mkdir -p .next/cache \
    && chown -R node:node .next/cache
USER node

EXPOSE 3000

CMD ["npm", "run", "start"]
