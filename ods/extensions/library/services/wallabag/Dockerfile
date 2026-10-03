FROM wallabag/wallabag:2.6.14@sha256:4a527e027e0d59e87c14225ef11e005af3d4890374202ad319ce5e63dfc66709
COPY ods-patch.php ods-validate.php ods-start.sh /
RUN php /ods-patch.php && php -l /ods-validate.php
