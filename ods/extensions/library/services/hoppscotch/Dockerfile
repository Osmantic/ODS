# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
FROM hoppscotch/hoppscotch:2026.8.1@sha256:e55f3f901e7b18c1a90e6f5b1d283ab45e89049774552553e0451a0ae308ddf2
COPY --chmod=0555 start.sh /opt/ods-start.sh
USER 1000:0
ENTRYPOINT ["tini", "-g", "--"]
CMD ["/bin/sh", "/opt/ods-start.sh"]
