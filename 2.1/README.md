# Домашнее задание 2.1

Подробное решение с командами и пояснениями: [solution.md](solution.md).

Манифесты:

- [manifests/00-namespace.yaml](manifests/00-namespace.yaml) — namespace `hw21`
- [manifests/deployment-shared-volume.yaml](manifests/deployment-shared-volume.yaml) — Deployment из busybox и multitool с общим `emptyDir`
- [manifests/daemonset-hostlogs.yaml](manifests/daemonset-hostlogs.yaml) — DaemonSet с hostPath `/var/log` в режиме read-only
