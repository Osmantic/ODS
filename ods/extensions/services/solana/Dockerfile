FROM node:20-alpine

WORKDIR /app

# Install deps first for layer caching (reproducible via the lockfile).
# ws pulls native accelerators (utf-8-validate/bufferutil) that need a C
# toolchain to compile. --ignore-scripts skips that build and ws falls back to
# its pure-JS path, keeping the image toolchain-free; --omit=optional trims the
# optional ones from the tree.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --omit=optional --ignore-scripts --no-audit --no-fund

COPY index.mjs solana.mjs mcp.mjs cluster.mjs ./

# Keystore volume must be writable by the unprivileged runtime user. Named
# volumes inherit this ownership on first creation.
RUN mkdir -p /keystore && chown -R node:node /keystore

USER node
EXPOSE 8590

CMD ["node", "index.mjs"]
