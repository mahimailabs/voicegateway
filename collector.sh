#!/usr/bin/env bash
# Retired VoiceGateway collector installer.
# Public URL:  https://voicegateway.dev/collector.sh
#
# The Python fleet collector this script used to install is retired. VoiceGateway is now one Go
# service with its own stack, installed by install.sh. This script stays at its public URL so old
# links explain the change instead of returning a 404. It installs nothing.
set -euo pipefail

cat >&2 <<'MSG'
!! The VoiceGateway collector is retired, so this script no longer installs anything.

VoiceGateway now runs as one stack that reads your LiveKit project directly. Install it with:

  curl -fsSL https://voicegateway.dev/install.sh | bash

Guide: https://github.com/mahimailabs/voicegateway/blob/main/docs/self-hosting.md

An existing collector keeps running as it is; this script does not touch it.
MSG
exit 1
