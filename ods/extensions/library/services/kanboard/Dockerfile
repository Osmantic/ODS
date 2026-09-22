FROM kanboard/kanboard:v1.2.54@sha256:8df6c4339134b6c196da9a262daa42ab8ce395f528a48d4a3dd7038c296f34c1
COPY ods-patch.php ods-validate.php ods-start.sh /
RUN php /ods-patch.php && php -l /ods-validate.php
