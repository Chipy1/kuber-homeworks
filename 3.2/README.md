# Домашнее задание 3.2

Подробное решение с командами и выводами: [solution.md](solution.md).

Два локальных кластера на kind (v0.33.0, Docker): одиночный 1 master + 4 worker и HA 3 master + 3 worker.

- [kind-5nodes.yaml](kind-5nodes.yaml) — конфиг кластера `hw32` (5 нод, etcd static pod на мастере).
- [kind-ha.yaml](kind-ha.yaml) — конфиг кластера `hw32-ha` (3 control-plane + 3 worker, Envoy LB).

Kubeconfig'и лежат вне репозитория: `~/.kube/kind-hw32` и `~/.kube/kind-hw32-ha`.
