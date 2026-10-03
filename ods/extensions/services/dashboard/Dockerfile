# ODS Dashboard
# Multi-stage build: React SPA → nginx static serving

# Stage 1: Build React app
# Vite 7 requires Node >=20.19. Use a compatible tag so dashboard rebuilds do
# not reuse an older cached Node 20 base image. The digest locks this build.
FROM node:20.19-alpine@sha256:658d0f63e501824d6c23e06d4bb95c71e7d704537c9d9272f488ac03a370d448 AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source files
COPY . .

# Build the React app
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine@sha256:df221db836e1754089190208cee7eeda94f233197056426eda74a43ab1abeac2

# Copy built app from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Preserve an immutable nginx template. The entrypoint renders a fresh active
# config from it on every container start so runtime substitutions are
# restart-safe and can follow a changed preview port or API key.
COPY nginx.conf /etc/nginx/conf.d/default.conf.template

# Copy entrypoint script for runtime auth configuration
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod 755 /entrypoint.sh

# Create non-root user and set permissions for nginx
RUN addgroup -g 1000 odser && \
    adduser -D -u 1000 -G odser odser && \
    chown -R odser:odser /usr/share/nginx/html && \
    chown -R odser:odser /var/cache/nginx && \
    chown -R odser:odser /var/log/nginx && \
    chown -R odser:odser /etc/nginx/conf.d && \
    sed -i '/^user/d' /etc/nginx/nginx.conf && \
    sed -i 's|^pid[[:space:]].*;|pid /tmp/nginx.pid;|' /etc/nginx/nginx.conf

USER odser

# Expose port 3001
EXPOSE 3001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:3001/ || exit 1

# Run entrypoint (configures nginx, then starts it)
ENTRYPOINT ["/entrypoint.sh"]
