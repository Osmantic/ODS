FROM invoiceshelf/invoiceshelf:2.4.2@sha256:76f13fbf45f4bad494bb360ec062c7268c71ad6d91f96ae1319de414a9959080
COPY --chmod=0555 start.sh /usr/local/bin/ods-invoiceshelf-start
ENTRYPOINT ["/usr/local/bin/ods-invoiceshelf-start"]
CMD ["/init"]
