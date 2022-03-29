#!/usr/bin/env bash

# Create Docker image

# Bash strict-mode
set -o errexit
set -o nounset
set -o pipefail

docker build -f docker/Dockerfile -t test/bugzilla2gitlab .