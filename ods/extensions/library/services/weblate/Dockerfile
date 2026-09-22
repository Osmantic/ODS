FROM weblate/weblate:2026.9.1.1@sha256:dc2d291144a2b750e6455077f1fb1870556c9b03242e69689d95bb4225a953dc
COPY --chmod=0555 start.py /ods-start.py
COPY --chmod=0555 health.sh /ods-health.sh
USER 1000:1000
ENTRYPOINT ["python", "/ods-start.py"]
CMD ["runserver"]
