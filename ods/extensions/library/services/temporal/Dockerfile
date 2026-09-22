FROM temporalio/temporal:1.9.1@sha256:ad4c82c97bd12b417d1ea942610dbcd511afb250c4d5ed26c694009533df447e
RUN mkdir -p /home/temporal/data
CMD ["server", "start-dev", "--ip", "0.0.0.0", "--port", "7233", "--ui-ip", "0.0.0.0", "--ui-port", "8233", "--ui-disable-news-fetch", "--db-filename", "/home/temporal/data/temporal.db"]
