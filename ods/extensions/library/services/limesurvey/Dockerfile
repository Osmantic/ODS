FROM martialblog/limesurvey:7-apache@sha256:9ec6e15c653460181e24a175dc2a8c4bdb4227a746233b2f7657f859ae1e0df8
USER root
RUN mkdir -p /var/lib/ods-limesurvey && chown www-data:www-data /var/lib/ods-limesurvey && chmod 0700 /var/lib/ods-limesurvey \
    && test ! -e /var/www/html/application/config/security.php \
    && ln -s /var/lib/ods-limesurvey/security.php /var/www/html/application/config/security.php \
    && a2disconf remoteip
COPY --chmod=0555 start.sh /usr/local/bin/ods-start.sh
USER www-data
ENTRYPOINT ["/usr/local/bin/ods-start.sh"]
CMD ["apache2-foreground"]
