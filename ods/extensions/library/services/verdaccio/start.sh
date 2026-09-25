#!/bin/sh
set -eu
node /opt/ods-bootstrap.cjs
unset VERDACCIO_PASSWORD
exec verdaccio --config /verdaccio/conf/config.yaml --listen http://0.0.0.0:4873
