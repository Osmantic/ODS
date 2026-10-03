FROM ghcr.io/umami-software/umami:3.4.0@sha256:85909afc45bdcda1917394594a087421fdbb05610fded0fa9f6fb861abb2f367
COPY --chown=1001:1001 ods-config.mjs ods-start.mjs ods-initialize.mjs ods-start.sh /app/
RUN node --input-type=module -e "await import('bcryptjs'); await import('@prisma/adapter-pg'); await import('./generated/prisma/client.js')"
CMD ["node", "/app/ods-start.mjs"]
