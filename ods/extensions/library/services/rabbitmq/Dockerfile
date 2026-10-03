FROM rabbitmq:4.3.6-management@sha256:de62d9901fb73aaa8767b9c1e9ea32f2938f69d1b83e430f7c5ca854081d2128
COPY rabbitmq.conf /etc/rabbitmq/conf.d/30-ods.conf
COPY start.sh /opt/ods/start.sh
ENTRYPOINT ["/bin/sh", "/opt/ods/start.sh"]
CMD ["rabbitmq-server"]
