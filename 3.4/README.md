# Домашнее задание 3.4

Подробное решение с командами и пояснениями: [solution.md](solution.md).

## Манифесты

- `manifests/namespace.yaml` — namespace `hw34`.
- `manifests/web-deployment.yaml`, `manifests/web-service.yaml` — Deployment `web` (5 реплик, nginx + multitool, `maxSurge: 0` / `maxUnavailable: 1`) и Service `web-svc`.
- `manifests/curl-watcher.yaml` — временный под, которым проверялась доступность во время обновления (после замеров удалён).
- `manifests/canary/` — ConfigMap'ы со страницами v1/v2, деплойменты `web-v1`/`web-v2`, их сервисы и два Ingress'а с canary-аннотациями.
