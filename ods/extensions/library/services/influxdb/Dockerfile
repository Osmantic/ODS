FROM influxdb:2@sha256:05d6fb735bc22449c401094d42846123a24e3325d3a6e092242a45bfd68fff21
RUN chown -R 1000:1000 /var/lib/influxdb2 /etc/influxdb2 && chmod 0700 /var/lib/influxdb2 /etc/influxdb2
COPY --chmod=0555 start.sh /ods-start.sh
USER 1000:1000
ENTRYPOINT ["/ods-start.sh"]
CMD ["influxd"]
