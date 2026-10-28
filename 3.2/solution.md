# 3.2. Установка Kubernetes: два локальных кластера на kind

Ставить пять виртуалок под kubeadm на домашней машине смысла мало: ресурсов уйдёт много,
а результата не увижу. Поэтому взял kind — он поднимает ноды Kubernetes как обычные
Docker-контейнеры, внутри которых работает настоящий kubeadm, containerd и kubelet.
То есть установка не «игрушечная»: kubeadm init/join выполняется по-настоящему, etcd
запускается статическим подом, CRI — containerd. Разница с bare-metal только в том,
что ноды разделяют одно ядро хоста.

Стенд: Arch Linux, ядро 7.2.11-arch1-1, Docker 29.9.1, kind v0.33.0 (`~/.local/bin/kind`),
24 vCPU и 39 GiB RAM. На хосте уже работал MicroK8s с другими заданиями, его не трогал:
kind живёт в своей docker-сети `kind` и на порты MicroK8s не претендует.

## Задание 1. Кластер 1 master + 4 worker

### Конфиг

`kind-5nodes.yaml`:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: hw32
nodes:
  - role: control-plane
  - role: worker
  - role: worker
  - role: worker
  - role: worker
```

Ничего больше не задавал: CNI kind ставит свой (kindnet), StorageClass — local-path,
подсеть 10.244.0.0/16, service — 10.96.0.0/16.

### Первая попытка упала на моём же лишнем label

Сначала решил «на всякий случай» явно повесить на мастер метку control-plane:

```yaml
  - role: control-plane
    labels:
      node-role.kubernetes.io/control-plane: ""
```

`kind create cluster` дошёл до инициализации и через минуту отвалился:

```
error: error execution phase wait-control-plane: cannot obtain client without bootstrap:
could not bootstrap the admin user in file admin.conf: unable to create ClusterRoleBinding:
context deadline exceeded
```

Выглядит как проблема с API-сервером, но API-сервера ещё не было: `crictl ps -a` внутри
контейнера пустой. Причина нашлась в journalctl kubelet:

```
"command failed" err=<
  failed to validate kubelet flags: unknown 'kubernetes.io' or 'k8s.io' labels specified with
  --node-labels: [node-role.kubernetes.io/control-plane]
```

kind передаёт `labels` из конфига в kubelet через `--node-labels`, а kubelet с некоторых
версий запрещает метки в домене `kubernetes.io` из командной строки. kubelet падал в
рестарт-луп, статические поды (etcd, apiserver) не создавались, kubeadm честно ждал
четыре минуты и сдался. Роль control-plane kubeadm и так проставляет сам, так что метку
я убрал — итоговый конфиг выше. Кластер после неудачи пришлось удалить и создать заново
(второй раз я запускал с `--retain`, чтобы посмотреть, что внутри, и уже оттуда достал
лог kubelet).

### Создание

```
$ kind create cluster --name hw32 --config kind-5nodes.yaml --wait 180s
...
 ✓ Waiting ≤ 3m0s for control-plane = Ready
 ✓ Ready after 7s
Set kubectl context to "kind-hw32"
```

Образ `kindest/node:v1.37.0` — дефолт для kind v0.33.0 — скачался с первого раза,
подбирать другой тег не понадобилось. Kubernetes получился v1.37.0.

### Kubeconfig отдельно от MicroK8s

kind по умолчанию пишет контекст в `~/.kube/config` и делает его текущим. Это мне не
подходит: на хосте есть MicroK8s, и переключение контекста ломает другие задачи.
Поэтому сразу после создания вынес конфиг в отдельный файл и вернул MicroK8s на место:

```
$ kind export kubeconfig --name hw32 --kubeconfig ~/.kube/kind-hw32
Set kubectl context to "kind-hw32"
$ kubectl config use-context microk8s
$ kubectl config delete-context kind-hw32
$ kubectl config delete-cluster kind-hw32
$ kubectl config delete-user kind-hw32
```

Дальше работаю только через `KUBECONFIG=~/.kube/kind-hw32`. Второй кластер создавал уже
с `--kubeconfig ~/.kube/kind-hw32-ha`, так что основной конфиг даже не открывался.
Проверка, что ничего не уехало:

```
$ kubectl config get-contexts
CURRENT   NAME       CLUSTER            AUTHINFO   NAMESPACE
*         microk8s   microk8s-cluster   admin
```

### Ноды и версии

```
$ kubectl get nodes -o wide
NAME                 STATUS   ROLES           AGE   VERSION   INTERNAL-IP   ...  OS-IMAGE                KERNEL-VERSION      CONTAINER-RUNTIME
hw32-control-plane   Ready    control-plane   33s   v1.37.0   172.31.10.5   ...  Debian GNU/Linux 13 (trixie)   7.2.11-arch1-1 (amd64)   containerd://2.3.4
hw32-worker          Ready    <none>          19s   v1.37.0   172.31.10.4   ...  Debian GNU/Linux 13 (trixie)   7.2.11-arch1-1 (amd64)   containerd://2.3.4
hw32-worker2         Ready    <none>          19s   v1.37.0   172.31.10.6   ...  Debian GNU/Linux 13 (trixie)   7.2.11-arch1-1 (amd64)   containerd://2.3.4
hw32-worker3         Ready    <none>          19s   v1.37.0   172.31.10.3   ...  Debian GNU/Linux 13 (trixie)   7.2.11-arch1-1 (amd64)   containerd://2.3.4
hw32-worker4         Ready    <none>          19s   v1.37.0   172.31.10.2   ...  Debian GNU/Linux 13 (trixie)   7.2.11-arch1-1 (amd64)   containerd://2.3.4

$ kubectl version
Client Version: v1.37.1
Kustomize Version: v5.8.1
Server Version: v1.37.0
```

CRI видно прямо в колонке CONTAINER-RUNTIME, отдельно проверять не нужно, но для
порядка заглянул внутрь контейнера ноды:

```
$ docker exec hw32-control-plane containerd --version
containerd github.com/containerd/containerd/v2 v2.3.4 db8809540e1a7a9da5d518876894933ff55692ab

$ docker exec hw32-control-plane crictl version
Version:  0.1.0
RuntimeName:  containerd
RuntimeVersion:  v2.3.4
RuntimeApiVersion:  v1
```

Kernel-version у всех нод — ядро хоста, как и ожидается: контейнеры делят одно ядро.

### etcd на мастере

```
$ kubectl -n kube-system get pods -l component=etcd -o wide
NAME                      READY   STATUS    RESTARTS   AGE   IP            NODE                 ...
etcd-hw32-control-plane   1/1     Running   0          36s   172.31.10.5   hw32-control-plane   ...
```

Один под, стоит на control-plane. Это статический под: манифест лежит на ноде, kubelet
читает каталог `/etc/kubernetes/manifests` и создаёт поды сам, без участия API-сервера
(иначе получился бы замкнутый круг — apiserver без etcd не работает).

```
$ docker exec hw32-control-plane cat /etc/kubernetes/manifests/etcd.yaml | head -40
apiVersion: v1
kind: Pod
metadata:
  annotations:
    kubeadm.kubernetes.io/etcd.advertise-client-urls: https://172.31.10.5:2379
  labels:
    component: etcd
    tier: control-plane
  name: etcd
  namespace: kube-system
spec:
  containers:
  - command:
    - etcd
    - --advertise-client-urls=https://172.31.10.5:2379
    - --cert-file=/etc/kubernetes/pki/etcd/server.crt
    - --client-cert-auth=true
    - --data-dir=/var/lib/etcd
    - --feature-gates=InitialCorruptCheck=true
    - --initial-advertise-peer-urls=https://172.31.10.5:2380
    - --initial-cluster=hw32-control-plane=https://172.31.10.5:2380
    - --key-file=/etc/kubernetes/pki/etcd/server.key
    - --listen-client-urls=https://127.0.0.1:2379,https://172.31.10.5:2379
    - --listen-metrics-urls=http://127.0.0.1:2381
    - --listen-peer-urls=https://172.31.10.5:2380
    - --name=hw32-control-plane
    ...
    image: registry.k8s.io/etcd:3.7.0-0
```

Весь каталог манифестов на мастере — четыре файла: etcd, kube-apiserver,
kube-controller-manager, kube-scheduler. Всё остальное приезжает как обычные поды
через DaemonSet/Deployment (kindnet, kube-proxy, coredns).

Одна мелочь из лога kubeadm: `WARNING: could not find officially supported version of etcd
for Kubernetes v1.37.0, falling back to the nearest etcd version (3.7.0-0)`. То есть
для 1.37 в таблице поддерживаемых версий etcd ещё нет, kubeadm взял ближайшую.
На работу не влияет, но в логах висит.

### Проверка, что кластер действительно рабочий

Ready у нод ещё не значит, что CNI и планировщик живые, поэтому прогнал маленький деплоймент:

```
$ kubectl create deployment web --image=nginx --replicas=4
$ kubectl rollout status deployment/web --timeout=120s
deployment "web" successfully rolled out

$ kubectl get pods -o wide -l app=web
NAME                   READY   STATUS    RESTARTS   AGE   IP           NODE           ...
web-5fc9f4bf66-8t5ph   1/1     Running   0          11s   10.244.4.2   hw32-worker4   ...
web-5fc9f4bf66-9x6qs   1/1     Running   0          11s   10.244.1.2   hw32-worker2   ...
web-5fc9f4bf66-jhl6w   1/1     Running   0          11s   10.244.2.2   hw32-worker    ...
web-5fc9f4bf66-mx7d7   1/1     Running   0          11s   10.244.3.2   hw32-worker3   ...
```

Четыре пода разъехались по четырём разным воркерам, у каждого свой IP из подсети
10.244.0.0/16 — значит и kindnet, и планировщик работают. Деплоймент потом удалил,
чтобы не мешал.

## Задание 2*. HA-кластер 3 master + 3 worker

### Конфиг

`kind-ha.yaml`:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: hw32-ha
nodes:
  - role: control-plane
  - role: control-plane
  - role: control-plane
  - role: worker
  - role: worker
  - role: worker
```

Три мастера — нечётное число, кворум etcd 2 из 3.

```
$ kind create cluster --name hw32-ha --config kind-ha.yaml --kubeconfig ~/.kube/kind-hw32-ha --wait 180s
...
 ✓ Joining more control-plane nodes
 ✓ Joining worker nodes
 ✓ Ready after 15s
```

Здесь kind сам добавляет шаг «Joining more control-plane nodes» и поднимает
дополнительный контейнер под балансировщик.

### Ноды и etcd

```
$ kubectl get nodes -o wide
NAME                     STATUS   ROLES           AGE   VERSION   INTERNAL-IP    CONTAINER-RUNTIME
hw32-ha-control-plane    Ready    control-plane   84s   v1.37.0   172.31.10.12   containerd://2.3.4
hw32-ha-control-plane2   Ready    control-plane   35s   v1.37.0   172.31.10.8    containerd://2.3.4
hw32-ha-control-plane3   Ready    control-plane   20s   v1.37.0   172.31.10.11   containerd://2.3.4
hw32-ha-worker           Ready    <none>          19s   v1.37.0   172.31.10.9    containerd://2.3.4
hw32-ha-worker2          Ready    <none>          19s   v1.37.0   172.31.10.10   containerd://2.3.4
hw32-ha-worker3          Ready    <none>          19s   v1.37.0   172.31.10.7    containerd://2.3.4

$ kubectl -n kube-system get pods -l component=etcd -o wide
NAME                          READY   STATUS    RESTARTS   AGE   IP             NODE
etcd-hw32-ha-control-plane    1/1     Running   0          83s   172.31.10.12   hw32-ha-control-plane
etcd-hw32-ha-control-plane2   1/1     Running   0          18s   172.31.10.8    hw32-ha-control-plane2
etcd-hw32-ha-control-plane3   1/1     Running   0          15s   172.31.10.11   hw32-ha-control-plane3
```

Три члена etcd, по одному на мастере, у каждого свой data-dir на своей ноде.
Так же по три пода kube-apiserver, kube-controller-manager и kube-scheduler.

### Кластерный endpoint и балансировка API

«Для cluster ip использовать keepalived или другой способ» — в kind этот другой способ
сделан на уровне Docker. Внешний адрес кластера — не IP на нодах, а отдельный контейнер:

```
$ docker ps --filter name=hw32-ha --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'
hw32-ha-external-load-balancer   envoyproxy/envoy:v1.36.2   10000/tcp, 127.0.0.1:32999->6443/tcp
hw32-ha-worker                   kindest/node:v1.37.0
hw32-ha-worker2                  kindest/node:v1.37.0
hw32-ha-worker3                  kindest/node:v1.37.0
hw32-ha-control-plane            kindest/node:v1.37.0      127.0.0.1:44567->6443/tcp
hw32-ha-control-plane2           kindest/node:v1.37.0      127.0.0.1:46855->6443/tcp
hw32-ha-control-plane3           kindest/node:v1.37.0      127.0.0.1:39033->6443/tcp
```

kind v0.33.0 использует Envoy, а не haproxy, как в старых версиях.
`kubectl cluster-info` соответственно показывает адрес проброса этого контейнера:

```
$ kubectl cluster-info
Kubernetes control plane is running at https://127.0.0.1:32999
CoreDNS is running at https://127.0.0.1:32999/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy
```

Внутри docker-сети контейнер-балансировщик имеет адрес 172.31.10.13, и именно он
прописан в kubeadm как controlPlaneEndpoint — в `/kind/kubeadm.conf` на мастере:

```
controlPlaneEndpoint: hw32-ha-external-load-balancer:6443
```

Для сравнения, в одиночном кластере там `controlPlaneEndpoint: hw32-control-plane:6443`,
то есть просто имя единственной ноды.

Конфиг Envoy лежит в контейнере, `/home/envoy/cds.yaml` — апстримы:

```yaml
  name: kube_apiservers
  type: STRICT_DNS
  lb_policy: ROUND_ROBIN
  health_checks:
  - timeout: 3s
    interval: 2s
    unhealthy_threshold: 2
    healthy_threshold: 1
    http_health_check:
      path: /healthz
  load_assignment:
    ...
      - endpoint: {address: hw32-ha-control-plane,  port_value: 6443}
      - endpoint: {address: hw32-ha-control-plane2, port_value: 6443}
      - endpoint: {address: hw32-ha-control-plane3, port_value: 6443}
```

`lds.yaml` — слушатель: TCP-прокси на 6443, без терминирования TLS
(`envoy.filters.network.tcp_proxy`, cluster `kube_apiservers`). TLS-трафик до
apiserver проходит насквозь, поэтому kubectl проверяет сертификаты apiserver
как обычно. Health-check ходит на `/healthz` каждые 2 секунды, порог 2 —
значит мёртвый apiserver выкидывается из ротации примерно за 4 секунды.

Итого VIP здесь — адрес контейнера Envoy в docker-сети (172.31.10.13:6443),
а не адрес, поднятый на нодах. keepalived не нужен: нет отдельного виртуального IP,
который надо переезжать между машинами, потому что балансировщик — самостоятельный
контейнер, а не процесс на мастере.

Внутри кластера service `kubernetes` в default имеет три адреса:

```
$ kubectl get endpoints kubernetes
NAME         ENDPOINTS                                              AGE
kubernetes   172.31.10.11:6443,172.31.10.12:6443,172.31.10.8:6443   83s
```

Это то, чем пользуются поды внутри: kube-proxy раскидывает запросы к API по трём
apiserver'ам напрямую, без Envoy.

### Проверка отказоустойчивости

Остановил контейнер первого мастера. `docker stop` по умолчанию ждёт 10 секунд —
на первом прогоне он вернулся через 10.6 s, всё это время apiserver ещё отвечал.

Второй прогон сделал с замером: в фоне раз в секунду дергал `/healthz` через Envoy,
а на шестой секунде остановил мастер.

```
13:38:07 200 0.005273
...
13:38:12 200 0.004067
13:38:15 500 2.005056      <- здесь apiserver уже ушёл
13:38:16 200 0.004217
13:38:17 200 0.005064
...
13:38:49 200 0.004121
```

Один запрос из сорока получил 500 за 2 секунды — Envoy успел выбрать уже умерший
апстрим и ждал ответа до таймаута; следующий запрос ушёл на живые мастера.
Если бы клиент повторял запрос, он бы этого не заметил.

Нода при этом честно ушла в NotReady примерно через минуту — это стандартный
node-monitor-grace-period плюс время на обновление статуса:

```
$ kubectl get nodes
NAME                     STATUS     ROLES           AGE     VERSION
hw32-ha-control-plane    NotReady   control-plane   3m13s   v1.37.0
hw32-ha-control-plane2   Ready      control-plane   2m24s   v1.37.0
hw32-ha-control-plane3   Ready      control-plane   2m9s    v1.37.0
hw32-ha-worker           Ready      <none>          2m8s    v1.37.0
hw32-ha-worker2          Ready      <none>          2m8s    v1.37.0
hw32-ha-worker3          Ready      <none>          2m8s    v1.37.0
```

`kubectl get pods -A` продолжал работать всё это время: 27 подов, все в Running.
Статус пода etcd на остановленной ноде остался `1/1 Running` — это ожидаемо,
статус статических подов обновляет kubelet, а его там больше нет; последнее
состояние просто замерзло. Живость etcd проверял напрямую через etcdctl из
соседнего пода (в образе etcd нет `sh`, поэтому команда запускается без обёртки):

```
$ kubectl -n kube-system exec etcd-hw32-ha-control-plane2 -- etcdctl \
    --endpoints=https://172.31.10.8:2379,https://172.31.10.11:2379,https://172.31.10.12:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key endpoint status -w table
Failed to get the status of endpoint https://172.31.10.12:2379 (context deadline exceeded)
│ ENDPOINT                  │ ID               │ IS LEADER │ RAFT TERM │ RAFT INDEX │
│ https://172.31.10.8:2379  │ 975235fb0bd5b038 │ true      │ 3         │ 1389       │
│ https://172.31.10.11:2379 │ 377be8d4c3a4a048 │ false     │ 3         │ 1389       │
```

Два живых члена из трёх, индекс совпадает, лидер — control-plane2. Кворум 2/3 есть,
значит запись продолжает работать. Упади второй мастер — etcd встал бы на запись,
хотя на чтение из локального состояния отвечал бы.

После `docker start hw32-ha-control-plane` нода вернулась в Ready, etcd-под
перезапустился (RESTARTS 1), и все три члена снова в строю:

```
│ https://172.31.10.8:2379  │ 975235fb0bd5b038 │ true  │ 3 │ 2241 │
│ https://172.31.10.11:2379 │ 377be8d4c3a4a048 │ false │ 3 │ 2241 │
│ https://172.31.10.12:2379 │ bcbb75b790b92c39 │ false │ 3 │ 2241 │
```

Индекс у всех трёх одинаковый (2241), то есть вернувшийся член догнал raft-лог и
снова участвует в голосовании. Пока он был выключен, двое продолжали выбирать
лидера сами и запись шла без него — именно этого от кворума и ждёшь.

### Что именно даёт отказоустойчивость

- Три независимых kube-apiserver за балансировщиком: смерть одного мастера не рвёт
  доступ к API, kubectl ходит в Envoy, а тот знает про три апстрима.
- Три члена etcd: данные голосуются по raft, кворум 2 из 3. Один узел можно потерять
  без остановки записи.
- kube-controller-manager и kube-scheduler на всех мастерах, но активен только один —
  они выбирают лидера через lease-объекты:

```
$ kubectl -n kube-system get lease kube-scheduler kube-controller-manager -o jsonpath=...
kube-scheduler -> hw32-ha-control-plane2_61fc70a4-...
kube-controller-manager -> hw32-ha-control-plane3_a9889bd5-...
```

  Останавливал я control-plane, который не держал ни один из лидеров, так что
  перевыборов в тесте не было; при остановке лидера перевыборы займут секунды,
  пока lease не истечёт.

### Чего kind не даёт

- Все «ноды» — контейнеры на одной машине с одним ядром. Упал хост — упал весь кластер,
  никакой отказоустойчивости на уровне железа тут нет. Смысл HA-теста в kind —
  проверить, что control plane переживает потерю члена, а не что стенд переживёт
  отказ сервера.
- Балансировщик — тоже локальный контейнер. Это единственная точка отказа: пока Envoy
  жив, kubectl работает, но пропал хост — пропало всё. В продакшене на этом месте
  либо несколько LB, либо keepalived с VIP, либо облачный балансировщик.
- Сеть и диски не настоящие: нет отдельных сетевых интерфейсов, нет anti-affinity
  между физическими машинами, PV через local-path живут в контейнере ноды.
- Обновление кластера делается через создание нового, а не kubeadm upgrade по живым
  нодам, и нет никакого мониторинга/бэкапа etcd из коробки.

## Воспроизведение

```
kind create cluster --name hw32    --config 3.2/kind-5nodes.yaml --wait 180s
kind export kubeconfig --name hw32 --kubeconfig ~/.kube/kind-hw32

kind create cluster --name hw32-ha --config 3.2/kind-ha.yaml \
     --kubeconfig ~/.kube/kind-hw32-ha --wait 180s

export KUBECONFIG=~/.kube/kind-hw32
kubectl get nodes -o wide
```

Оба кластера оставил работать — памяти на хосте хватает. По `docker stats` вместе
они занимают около 3.4 GiB: мастер ~500–700 MiB, воркеры по 135–150 MiB, Envoy 25 MiB.

## Итог

- Кластер из 5 нод (1 master + 4 worker) поднят на kind, CRI — containerd 2.3.4,
  etcd static pod на мастере, kubeconfig вынесен в `~/.kube/kind-hw32`.
- HA-кластер 3 + 3 поднят там же, control plane с тремя членами etcd.
- «Другой способ» вместо keepalived — контейнер `hw32-ha-external-load-balancer`
  (Envoy) в docker-сети kind; VIP/endpoint живёт на нём, а не на нодах.
- Потеря одного мастера переживается: API отвечает, кворум etcd 2/3 сохраняется,
  после возврата контейнера кластер сам приходит в норму.
- MicroK8s не тронут, current-context снова `microk8s`.
