#!/bin/sh
set -eu
php /ods-validate.php
exec /entrypoint.sh "$@"
