FROM quay.io/hedgedoc/hedgedoc:1.12.0@sha256:89bd85d7817fb3e3a4aafdd85aefb0fbb20b848f6dbf1d9ee0d72fdcb76710cc
USER root
RUN mkdir -p /hedgedoc/public/uploads && chown -R 10000:10000 /hedgedoc/public/uploads && chmod 700 /hedgedoc/public/uploads
USER 10000:10000
