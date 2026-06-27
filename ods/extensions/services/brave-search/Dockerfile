FROM node:20-alpine

WORKDIR /app
COPY proxy.mjs ./

USER node
EXPOSE 8585

CMD ["node", "proxy.mjs"]
