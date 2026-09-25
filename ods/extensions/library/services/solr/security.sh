#!/bin/bash
# Sourced by the native Solr init sequence after /var/solr initialization.
umask 077
python3 /opt/ods-security.py
unset SOLR_ADMIN_PASSWORD
