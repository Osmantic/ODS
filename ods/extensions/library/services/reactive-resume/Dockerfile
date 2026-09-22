# syntax=docker/dockerfile:1
FROM amruthpillai/reactive-resume:v5.3.1@sha256:2f5205f30121c2ce8247baf80dbc105516033f5cd42dba872fc6f459622169d9
COPY validate.cjs /opt/ods-validate.cjs
USER node
CMD ["node", "--require", "/opt/ods-validate.cjs", "apps/server/dist/index.mjs"]
