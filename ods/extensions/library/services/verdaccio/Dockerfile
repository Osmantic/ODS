FROM verdaccio/verdaccio:6@sha256:43c4067288b050422265407ea2fe747e511fcd0c407a85ec90ab9a326d9400cd
COPY config.yaml /verdaccio/conf/config.yaml
COPY bootstrap.cjs /opt/ods-bootstrap.cjs
COPY --chmod=0555 start.sh /opt/ods-start.sh
ENTRYPOINT ["/opt/ods-start.sh"]
