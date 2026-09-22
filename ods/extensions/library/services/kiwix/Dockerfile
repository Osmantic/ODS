FROM ghcr.io/kiwix/kiwix-serve:3.8.2@sha256:e478554a9920412eeb5ce1d73f24e4b28445ebed233187cdd03c623c32be46a5
COPY start.sh /opt/ods/start.sh
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/bin/sh", "/opt/ods/start.sh"]
