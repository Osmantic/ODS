FROM rssbridge/rss-bridge:latest@sha256:606896116558c57e3d381dec6874457f7a69216103d77c78b5bd38b92b251902
RUN mkdir -p /config /app/cache && chown -R www-data:www-data /config /app/cache
COPY --chown=www-data:www-data config.ini.php /config/config.ini.php
