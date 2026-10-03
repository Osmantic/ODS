FROM drakkan/sftpgo:v2.7.6@sha256:1edd28d80de2a008863fbfc58c8ff7db47b1b410bae48d4e6076192337809b11
COPY --chmod=0555 start.sh /usr/local/bin/ods-sftpgo-start
ENTRYPOINT ["/usr/local/bin/ods-sftpgo-start"]
CMD ["sftpgo", "serve"]
