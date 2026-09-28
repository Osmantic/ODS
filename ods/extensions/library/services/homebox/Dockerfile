# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
FROM ghcr.io/sysadminsmedia/homebox:0.26.2@sha256:b1ad7e3c63f732a5f6daa466e8116be4f545b3b120383a64dcb62beb00a660cc
USER root
RUN mkdir -p /data && chown -R 1000:1000 /data
USER 1000:1000
