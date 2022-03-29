#!/usr/bin/env bash

# Run Docker container

# Bash strict-mode
set -o errexit
set -o nounset
set -o pipefail

./start_container.sh "bin/bugzilla2gitlab"