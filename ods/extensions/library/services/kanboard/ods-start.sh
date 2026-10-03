#!/bin/sh
set -eu
php /ods-validate.php
cd /var/www/app
php cli db:migrate
exec /usr/local/bin/entrypoint.sh "$@"
