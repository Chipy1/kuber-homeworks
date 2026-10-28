# Домашнее задание 2.2

Полное решение с командами и пояснениями: [solution.md](solution.md).

## Файлы

- `manifests/pv-pvc/` — PV на hostPath, PVC, Deployment busybox+multitool, под для проверки файла на узле.
- `manifests/nfs/` — NFS-сервер с Service, RBAC и Deployment provisioner'а, StorageClass `nfs-client`, PVC и multitool.
