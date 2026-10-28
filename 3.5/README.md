# Домашнее задание 3.5

Подробное решение: [solution.md](solution.md).

- `3.5.md` — текст задания.
- `files/task.yaml` — исходный манифест из задания, не изменялся.
- `manifests/01-web-consumer-image.yaml` — вынужденная замена образа `radial/busyboxplus:curl` (schema 1 manifest не поддерживается containerd 2.x), команда осталась исходной.
- `manifests/fix.yaml` — исправленный Deployment `web-consumer` с обращением по `auth-db.data.svc.cluster.local`.
