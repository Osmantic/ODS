FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90 AS password
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 sudo jq passwd && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1001 owner && useradd -r pixel-ops-broker && echo 'owner:fixture-only-password' | chpasswd && usermod -aG sudo owner
RUN install -d -m 0755 /qa /home/owner/ods/bin /opt/pixel-ops-broker /etc/pixel-ops-broker && install -d -o pixel-ops-broker -g pixel-ops-broker -m 0700 /var/lib/pixel-ops-broker /var/lib/pixel-ops-broker/plans
COPY approval_terminal.py /qa/approval_terminal.py
COPY ods-pixel-approve /home/owner/ods/bin/ods-pixel-approve
COPY real_approval.py /qa/real_approval.py
RUN chmod 0755 /home/owner/ods/bin/ods-pixel-approve && chown -R owner:owner /home/owner/ods && python3 /qa/real_approval.py --setup
USER owner
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python3", "/qa/real_approval.py"]

FROM password AS passwordless
USER root
RUN gpasswd -d owner sudo && echo "owner ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/qa-passwordless && chmod 0440 /etc/sudoers.d/qa-passwordless
USER owner
CMD ["python3", "/qa/real_approval.py", "--passwordless"]
