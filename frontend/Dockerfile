# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS deps
WORKDIR /app
RUN corepack enable pnpm
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:24-bookworm-slim AS builder
WORKDIR /app
RUN corepack enable pnpm
# NEXT_PUBLIC_* values are inlined into the bundle at build time, so this has to be a
# build arg — setting it only at runtime would leave the default baked in.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
# Which stack the image is for. NODE_ENV cannot say: both stacks run this same production
# build. The UI uses it to name `make ps` or `make prod-ps` when the API is unreachable.
ARG NEXT_PUBLIC_ENVIRONMENT=local
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_ENVIRONMENT=$NEXT_PUBLIC_ENVIRONMENT
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm run build

FROM node:24-bookworm-slim AS runner
WORKDIR /app
# HOSTNAME: Next's standalone server binds to it, and Docker otherwise sets it to the
# container id, which leaves the server unreachable on 127.0.0.1 from its own health check.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN useradd --create-home --uid 10001 radar
COPY --from=builder --chown=radar:radar /app/.next/standalone ./
COPY --from=builder --chown=radar:radar /app/.next/static ./.next/static
COPY --from=builder --chown=radar:radar /app/public ./public
USER radar
EXPOSE 3000
CMD ["node", "server.js"]
