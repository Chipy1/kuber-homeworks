#!/usr/bin/env bash
# Самоподписной сертификат и Secret для Ingress.
# Ключ и сертификат лежат только в /tmp и в репозиторий не попадают.
set -euo pipefail

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /tmp/hw23-tls.key -out /tmp/hw23-tls.crt \
  -subj "/CN=hw23.local/O=Netology" \
  -addext "subjectAltName=DNS:hw23.local"

kubectl -n hw23 create secret tls hw23-tls \
  --cert=/tmp/hw23-tls.crt --key=/tmp/hw23-tls.key \
  --dry-run=client -o yaml | kubectl apply -f -

rm -f /tmp/hw23-tls.key /tmp/hw23-tls.crt
