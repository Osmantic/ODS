FROM quay.io/keycloak/keycloak:latest@sha256:82a77884f3af238beab1e7afd63b5f530e1b5c0590bd7aa60b40a40463e29b2c
ENV KC_DB=postgres KC_HEALTH_ENABLED=true KC_METRICS_ENABLED=true
RUN /opt/keycloak/bin/kc.sh build
COPY --chmod=0555 start.sh /ods-start.sh
COPY --chmod=0555 health.sh /ods-health.sh
USER 1000:0
ENTRYPOINT ["/ods-start.sh"]
CMD ["start", "--optimized"]
