# Домашнее задание 3.3

Подробное решение, выводы команд и разбор работы Calico: [solution.md](solution.md).

## Манифесты

[namespace.yaml](manifests/namespace.yaml), [deployments.yaml](manifests/deployments.yaml) (frontend/backend/cache, `HTTP_PORT=8080`), [services.yaml](manifests/services.yaml) (80 → 8080).

Политики: [default-deny](manifests/netpol-01-default-deny.yaml), [DNS](manifests/netpol-02-allow-dns.yaml), [frontend → backend](manifests/netpol-03-allow-frontend-backend.yaml), [backend → cache](manifests/netpol-04-allow-backend-cache.yaml).
