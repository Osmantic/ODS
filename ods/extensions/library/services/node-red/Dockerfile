# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
FROM nodered/node-red:5.0.7@sha256:a649dd711d55490151a2c39a8e48ad0c44325488fbc0e66315f2d2e19e5e1ace
COPY settings.js /opt/ods/settings.js
USER node-red
ENTRYPOINT ["node", "/usr/src/node-red/node_modules/node-red/red.js"]
CMD ["--userDir", "/data", "--settings", "/opt/ods/settings.js"]
