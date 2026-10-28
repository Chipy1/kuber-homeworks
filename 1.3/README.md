# Домашнее задание 1.3

Подробное решение с командами, выводом и разбором ошибки: [solution.md](solution.md).

Все ресурсы живут в namespace `hw13`. Манифесты: [deployment-broken.yaml](manifests/deployment-broken.yaml) — исходный вариант, где nginx и multitool делят 80-й порт; [deployment.yaml](manifests/deployment.yaml) — исправленный (multitool на 8080/8443), 2 реплики; [service.yaml](manifests/service.yaml) — `netology-svc` (80 → nginx, 8080 → multitool); [pod-multitool-client.yaml](manifests/pod-multitool-client.yaml) — отдельный под для проверки через curl; [deployment-init.yaml](manifests/deployment-init.yaml) и [service-init.yaml](manifests/service-init.yaml) — задание 2 с init-контейнером на busybox.
