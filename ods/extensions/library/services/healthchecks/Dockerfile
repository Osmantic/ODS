FROM healthchecks/healthchecks:v4.4@sha256:aa08a61b0dcfd83f1332eab195d5cec1b595ae076eb8388cadf8a3d597114f9b
COPY --chmod=0555 start.py /opt/healthchecks/ods-start.py
ENTRYPOINT ["python", "/opt/healthchecks/ods-start.py"]
CMD ["uwsgi", "/opt/healthchecks/docker/uwsgi.ini"]
