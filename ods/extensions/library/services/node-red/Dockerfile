# syntax=docker/dockerfile:1
FROM nodered/node-red:5.0.7@sha256:a649dd711d55490151a2c39a8e48ad0c44325488fbc0e66315f2d2e19e5e1ace
COPY settings.js /opt/ods/settings.js
USER node-red
ENTRYPOINT ["node", "/usr/src/node-red/node_modules/node-red/red.js"]
CMD ["--userDir", "/data", "--settings", "/opt/ods/settings.js"]
