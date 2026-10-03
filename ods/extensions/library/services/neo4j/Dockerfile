FROM neo4j:community@sha256:d8f4c156caa3af76499134947deb11d13042e471d9733060449f2a01eb7a248e
USER root
RUN chmod 0700 /data /logs && chown 7474:7474 /data /logs
COPY --chmod=0555 start.sh /ods-start.sh
USER 7474:7474
ENTRYPOINT ["tini", "-g", "--", "/ods-start.sh"]
CMD ["neo4j"]
