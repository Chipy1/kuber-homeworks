# Домашнее задание 1.5

Подробное решение с командами и пояснениями: [solution.md](solution.md).

## Манифесты

- [manifests/namespace.yaml](manifests/namespace.yaml) — namespace hw15
- [manifests/frontend.yaml](manifests/frontend.yaml) — Deployment frontend (nginx, 3 реплики) и Service frontend-svc
- [manifests/backend.yaml](manifests/backend.yaml) — Deployment backend (multitool, HTTP_PORT=8080) и Service backend-svc
- [manifests/ingress.yaml](manifests/ingress.yaml) — Ingress netology-ingress с rewrite-target
