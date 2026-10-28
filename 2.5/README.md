# Домашнее задание 2.5 — Helm

Подробное решение с командами и выводом: [solution.md](solution.md).

- `webapp/` — чарт приложения: `Chart.yaml`, `values.yaml`, `templates/` (frontend и backend: Deployment + Service, ConfigMap с index.html, `_helpers.tpl`).
- `values-web-v1.yaml`, `values-web-v2.yaml`, `values-web-v3.yaml` — values для релизов web-v1 и web-v2 (namespace `app1`) и web-v3 (namespace `app2`).

Ставится как `microk8s helm3 install web-v1 ./webapp -n app1 -f values-web-v1.yaml`.
