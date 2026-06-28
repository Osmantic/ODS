# ODS Dashboard
# Multi-stage build: React SPA → nginx static serving

# Stage 1: Build React app
# Vite 7 requires Node >=20.19. Use a compatible tag so dashboard rebuilds do
# not reuse an older cached node:20-alpine base image.
FROM node:20.19-alpine AS builder

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
FROM nginx:alpine

# Copy built app from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
# B1 fix: Copy directly to conf.d (not templates) since we override ENTRYPOINT
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy entrypoint script for runtime auth configuration
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Create non-root user and set permissions for nginx
RUN addgroup -g 1000 odser && \
    adduser -D -u 1000 -G odser odser && \
    chown -R odser:odser /usr/share/nginx/html && \
    chown -R odser:odser /var/cache/nginx && \
    chown -R odser:odser /var/log/nginx && \
    chown -R odser:odser /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && \
    chown -R odser:odser /var/run/nginx.pid && \
    sed -i '/^user/d' /etc/nginx/nginx.conf

USER odser

# Expose port 3001
EXPOSE 3001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:3001/ || exit 1

# Run entrypoint (configures nginx, then starts it)
ENTRYPOINT ["/entrypoint.sh"]
