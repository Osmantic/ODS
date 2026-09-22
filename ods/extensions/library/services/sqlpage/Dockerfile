FROM lovasoa/sqlpage:v0.46.3@sha256:354c683a50f541be01d427b3b664f1d5b36c28739f45bfb809be14b99b6ff649
USER root
RUN rm -f /var/www/sqlpage/sqlpage.db && mkdir -p /var/www /etc/sqlpage && chown -R 1000:1000 /var/www /etc/sqlpage
COPY --chown=1000:1000 index.sql ods-health.sql /var/www/
USER 1000:1000
