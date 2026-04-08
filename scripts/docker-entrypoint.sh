#!/bin/bash
set -e

# Fix permissions on upload directory
mkdir -p /tmp/citeverify_uploads
chown -R citeverify:citeverify /tmp/citeverify_uploads

# Drop privileges and exec CMD
exec runuser -u citeverify -- "$@"
