# Домашнее задание к занятию «Хранение в K8s. Часть 2»

**Стенд:** MicroK8s `v1.35.6` на одном узле `b550-tomahawk` (Arch Linux, внешний IP `192.168.50.225`), runtime `containerd://2.1.6`, клиент `kubectl v1.37.1`. Кластер общий, поэтому все ресурсы собраны в отдельном namespace `hw22`. Манифесты лежат в [manifests/](manifests/).

```bash
kubectl create namespace hw22
```

```text
namespace/hw22 created
```

---

## Задание 1. PV на hostPath, созданный руками

### Манифесты

PV описывает каталог на узле, который kubelet создаст при первом монтировании (`DirectoryOrCreate`). Политика reclaim — `Retain`, класс `manual`:

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: hw22-pv
spec:
  capacity:
    storage: 1Gi
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  storageClassName: manual
  hostPath:
    path: /var/snap/microk8s/common/hw22-data
    type: DirectoryOrCreate
```

PVC запрашивает ровно тот же класс и режим доступа:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: hw22-pvc
  namespace: hw22
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: manual
  resources:
    requests:
      storage: 1Gi
```

`storageClassName: manual` здесь — просто метка для ручного связывания: StorageClass с таким именем в кластере нет, поэтому динамический провижининг не включается, и контроллер ищет уже существующий PV с тем же классом.

Deployment из двух контейнеров: busybox раз в 5 секунд дописывает строку в `/shared/data.txt`, multitool читает этот файл через `tail -f`. Оба контейнера монтируют один PVC в `/shared`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hw22-local
  namespace: hw22
spec:
  replicas: 1
  selector:
    matchLabels:
      app: hw22-local
  template:
    metadata:
      labels:
        app: hw22-local
    spec:
      containers:
        - name: busybox
          image: busybox:1.36
          command:
            - /bin/sh
            - -c
            - |
              while true; do
                echo "$(date -Iseconds) запись от busybox" >> /shared/data.txt
                sleep 5
              done
          volumeMounts:
            - name: shared
              mountPath: /shared
        - name: multitool
          image: wbitt/network-multitool
          command:
            - /bin/sh
            - -c
            - tail -n +1 -f /shared/data.txt
          volumeMounts:
            - name: shared
              mountPath: /shared
      volumes:
        - name: shared
          persistentVolumeClaim:
            claimName: hw22-pvc
```

### Создание PV и PVC

```bash
kubectl apply -f manifests/pv-pvc/pv.yaml
kubectl apply -f manifests/pv-pvc/pvc.yaml
kubectl get pv,pvc -n hw22 -o wide
```

```text
persistentvolume/hw22-pv created
persistentvolumeclaim/hw22-pvc created
NAME                       CAPACITY   ACCESS MODES   RECLAIM POLICY   STATUS   CLAIM           STORAGECLASS   VOLUMEATTRIBUTESCLASS   REASON   AGE   VOLUMEMODE
persistentvolume/hw22-pv   1Gi        RWO            Retain           Bound    hw22/hw22-pvc   manual         <unset>                          0s    Filesystem

NAME                             STATUS   VOLUME    CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE   VOLUMEMODE
persistentvolumeclaim/hw22-pvc   Bound    hw22-pv   1Gi        RWO            manual         <unset>                 0s    Filesystem
```

Оба объекта созданы почти одновременно, но связались сразу же. StorageClass `manual` в кластере нет, так что динамически создавать том некому; PV-контроллер пробежал по существующим PV, нашёл совпадение по классу, режиму доступа и размеру и записал в `spec.claimRef` нового PVC.

### busybox пишет, multitool читает

```bash
kubectl apply -f manifests/pv-pvc/deployment.yaml
kubectl rollout status deploy/hw22-local -n hw22 --timeout=120s
kubectl get pods -n hw22 -o wide
```

```text
deployment.apps/hw22-local created
Waiting for deployment "hw22-local" rollout to finish: 0 of 1 updated replicas are available...
deployment "hw22-local" successfully rolled out
NAME                          READY   STATUS    RESTARTS   AGE   IP           NODE            NOMINATED NODE   READINESS GATES
hw22-local-575f65cb9d-rf2b7   2/2     Running   2          71s   10.1.184.5   b550-tomahawk   <none>           <none>
```

Пара рестартов при первом старте — не ошибка манифеста. В событиях видно, что под один раз пересоздавался (`SandboxChanged`), а образ `wbitt/network-multitool` не скачался с первой попытки:

```text
Warning   Failed    pod/hw22-local-575f65cb9d-rf2b7   Failed to pull image "wbitt/network-multitool": pull QPS exceeded
Warning   Failed    pod/hw22-local-575f65cb9d-rf2b7   Error: ErrImagePull
Normal    Pulled    pod/hw22-local-575f65cb9d-rf2b7   Successfully pulled image "wbitt/network-multitool" in 1.036s
```

kubelet ограничивает частоту pull'ов, вторая попытка прошла. Счётчик `RESTARTS 2` — по одному на контейнер.

Лог multitool (второй контейнер, читатель):

```bash
kubectl logs -n hw22 deploy/hw22-local -c multitool --tail=8
```

```text
2026-10-28T08:30:33+00:00 запись от busybox
2026-10-28T08:30:38+00:00 запись от busybox
2026-10-28T08:30:43+00:00 запись от busybox
2026-10-28T08:30:48+00:00 запись от busybox
2026-10-28T08:30:53+00:00 запись от busybox
2026-10-28T08:30:58+00:00 запись от busybox
2026-10-28T08:31:03+00:00 запись от busybox
2026-10-28T08:31:08+00:00 запись от busybox
```

У busybox лог пустой: он пишет не в stdout, а в файл на томе. Что файл действительно растёт, проверил замерами с интервалом:

```bash
POD=$(kubectl get pod -n hw22 -l app=hw22-local -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n hw22 $POD -c multitool -- ls -l /shared
kubectl exec -n hw22 $POD -c multitool -- wc -l /shared/data.txt
sleep 11
kubectl exec -n hw22 $POD -c multitool -- wc -l /shared/data.txt
```

```text
total 4
-rw-r--r--    1 root     root           832 Oct 28 08:31 data.txt
16 /shared/data.txt
18 /shared/data.txt
```

Файл в томе один и тот же для обоих контейнеров, поэтому busybox и multitool видят одни и те же байты.

### Удаляю Deployment и PVC

```bash
kubectl delete deploy hw22-local -n hw22
kubectl delete pvc hw22-pvc -n hw22
kubectl get pv,pvc -n hw22
```

```text
deployment.apps "hw22-local" deleted from hw22 namespace
persistentvolumeclaim "hw22-pvc" deleted from hw22 namespace
NAME                       CAPACITY   ACCESS MODES   RECLAIM POLICY   STATUS     CLAIM           STORAGECLASS   VOLUMEATTRIBUTESCLASS   REASON   AGE
persistentvolume/hw22-pv   1Gi        RWO            Retain           Released   hw22/hw22-pvc   manual         <unset>                          3m17s
```

PV перешёл в `Released`, а не в `Available`. Вот его состояние целиком:

```bash
kubectl describe pv hw22-pv
```

```text
Name:            hw22-pv
Labels:          <none>
Annotations:     pv.kubernetes.io/bound-by-controller: yes
Finalizers:      [kubernetes.io/pv-protection]
StorageClass:    manual
Status:          Released
Claim:           hw22/hw22-pvc
Reclaim Policy:  Retain
Access Modes:    RWO
VolumeMode:      Filesystem
Capacity:        1Gi
Node Affinity:   <none>
Message:
Source:
    Type:          HostPath (bare host directory volume)
    Path:          /var/snap/microk8s/common/hw22-data
    HostPathType:  DirectoryOrCreate
Events:          <none>
```

Причина в двух вещах. Политика `Retain` запрещает контроллеру что-либо делать с томом после освобождения — ни удалять, ни очищать. Второе: в `spec.claimRef` осталась ссылка на удалённый PVC вместе с его UID. Контроллер видит, что том был занят претендентом, которого больше нет, и помечает его `Released`. В `Available` том вернётся только если из `spec.claimRef` убрать ссылку вручную — например, так (в этой работе не выполнял, том мне нужен был для следующего шага):

```bash
kubectl patch pv hw22-pv --type=json -p='[{"op":"remove","path":"/spec/claimRef"}]'
```

Заодно проверил обратную ситуацию: если создать PVC с тем же именем заново, он Released-том не подхватит — UID в claimRef уже другой. PVC остался `Pending`, а контроллер безуспешно пытался найти динамический провижинер:

```text
NAME                             STATUS    VOLUME   CAPACITY   ACCESS MODES   STORAGECLASS   AGE
persistentvolumeclaim/hw22-pvc   Pending                                      manual         0s

Warning  ProvisioningFailed  2s (x6 over 67s)  persistentvolume-controller  storageclass.storage.k8s.io "manual" not found
```

Этот тестовый PVC и Deployment я потом удалил, чтобы не мешали.

### Данные остались на диске узла

Чтобы посмотреть каталог PV без лишних прав, поднял под, который монтирует тот же hostPath только на чтение ([manifests/pv-pvc/check-pod.yaml](manifests/pv-pvc/check-pod.yaml)):

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: hw22-check
  namespace: hw22
spec:
  restartPolicy: Never
  containers:
    - name: check
      image: busybox:1.36
      command: ["/bin/sh", "-c", "sleep 3600"]
      volumeMounts:
        - name: host-data
          mountPath: /host-data
          readOnly: true
  volumes:
    - name: host-data
      hostPath:
        path: /var/snap/microk8s/common/hw22-data
        type: Directory
```

```bash
kubectl apply -f manifests/pv-pvc/check-pod.yaml
kubectl exec -n hw22 hw22-check -- ls -l /host-data
kubectl exec -n hw22 hw22-check -- tail -3 /host-data/data.txt
```

```text
total 4
-rw-r--r--    1 root     root          1560 Oct 28 08:32 data.txt
2026-10-28T08:32:13+00:00 запись от busybox
2026-10-28T08:32:18+00:00 запись от busybox
2026-10-28T08:32:23+00:00 запись от busybox
```

Файл на месте, размер вырос до 1560 байт. Последняя строка записана в 08:32:23 — busybox успел дописать её уже во время остановки пода; после удаления Deployment и PVC содержимое никто не трогал.

Теперь удаляю сам PV:

```bash
kubectl delete pv hw22-pv
kubectl get pv
kubectl exec -n hw22 hw22-check -- ls -l /host-data
```

```text
persistentvolume "hw22-pv" deleted
No resources found
total 4
-rw-r--r--    1 root     root          1560 Oct 28 08:32 data.txt
```

И тот же файл, уже с самой ноды:

```bash
ls -l /var/snap/microk8s/common/hw22-data/
```

```text
total 4
-rw-r--r-- 1 root root 1560 Oct 28 13:32 data.txt
```

Размер и содержимое те же (на узле часовой пояс +05, отсюда 13:32 вместо 08:32 в UTC).

Почему так: hostPath PV — это только запись в API, описывающая каталог. Данные лежат в файловой системе узла, и kubelet при удалении PV ничего не чистит; `Retain` к тому же прямо запрещает контроллеру удалять том. Удаление объекта PV убирает описание, а не каталог. Почистить данные можно только руками на узле (`rm -rf /var/snap/microk8s/common/hw22-data`), чего я не делал — по условию файл оставлен.

Проверочный под убрал:

```bash
kubectl delete pod hw22-check -n hw22
```

```text
pod "hw22-check" deleted from hw22 namespace
```

---

## Задание 2. NFS-сервер и динамический PV

### NFS-сервер подом

Ставить пакеты на хост нельзя, поэтому NFS-сервер поднят в кластере. Каталог хранения — hostPath на узле, наружу отдан через `SHARED_DIRECTORY=/exports` (у этого образа переменная называется именно так). Контейнеру нужен `privileged: true`, иначе `rpc.nfsd` не сможет зарегистрироваться в ядре.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nfs-server
  namespace: hw22
spec:
  replicas: 1
  selector:
    matchLabels:
      app: nfs-server
  template:
    metadata:
      labels:
        app: nfs-server
    spec:
      containers:
        - name: nfs-server
          image: itsthenetwork/nfs-server-alpine:12
          env:
            - name: SHARED_DIRECTORY
              value: "/exports"
          ports:
            - name: nfs
              containerPort: 2049
          securityContext:
            privileged: true
          volumeMounts:
            - name: nfs-store
              mountPath: /exports
      volumes:
        - name: nfs-store
          hostPath:
            path: /var/snap/microk8s/common/hw22-nfs
            type: DirectoryOrCreate
---
apiVersion: v1
kind: Service
metadata:
  name: nfs-server
  namespace: hw22
spec:
  clusterIP: 10.152.183.56
  selector:
    app: nfs-server
  ports:
    - name: nfs
      port: 2049
      targetPort: 2049
```

Про фиксированный `clusterIP` — ниже, там отдельная история.

```bash
kubectl apply -f manifests/nfs/nfs-server.yaml
kubectl rollout status deploy/nfs-server -n hw22 --timeout=120s
kubectl logs -n hw22 deploy/nfs-server --tail=30
```

```text
deployment.apps/nfs-server created
service/nfs-server created
deployment "nfs-server" successfully rolled out
The SYNC environment variable is unset or null, defaulting to 'async' mode.
Displaying /etc/exports contents:
/exports *(rw,fsid=0,async,no_subtree_check,no_auth_nlm,insecure,no_root_squash)

Starting rpcbind...
...
Starting NFS in the background...
rpc.nfsd: Writing version string to kernel: -2 -3 +4 +4.1 +4.2
rpc.nfsd: Created AF_INET TCP socket.
Exporting File System...
exporting *:/exports
Startup successful.
```

`exporting *:/exports` и `Startup successful` — сервер поднялся и отдаёт каталог.

### Provisioner nfs-subdir-external-provisioner

RBAC взял из upstream-манифеста v4.0.2, только переименовал объекты с префиксом `hw22`, чтобы не пересечься с чужими cluster-scoped ресурсами: ServiceAccount, ClusterRole (просмотр nodes, полный цикл по PV, update PVC, events, storageclasses), ClusterRoleBinding, Role и RoleBinding на `endpoints` в своём namespace (нужны для leader election), а также StorageClass:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-client
provisioner: hw22.nfs.client
parameters:
  archiveOnDelete: "false"
reclaimPolicy: Delete
volumeBindingMode: Immediate
```

`provisioner` обязан совпадать с `PROVISIONER_NAME` в Deployment — иначе provisioner не увидит ни одного PVC.

Сам Deployment монтирует корень NFS в `/persistentvolumes` и получает адрес сервера через `NFS_SERVER`/`NFS_PATH`.

### Грабли: DNS-имя сервиса не годится для монтирования

Сначала, как и предполагалось, поставил `NFS_SERVER=nfs-server.hw22.svc.cluster.local` и такое же имя в `volumes.nfs.server`. Под provisioner'а завис в `ContainerCreating` на две минуты, в событиях — только `Scheduled`, ни `Pulled`, ни `FailedMount`:

```text
NAME                                   READY   STATUS              RESTARTS   AGE
hw22-nfs-provisioner-b85cb4449-ljmhn   0/1     ContainerCreating   0          2m

Events:
  Normal  Scheduled  2m  default-scheduler  Successfully assigned hw22/hw22-nfs-provisioner-b85cb4449-ljmhn to b550-tomahawk
```

Проверил по шагам. Имя не резолвится с хоста, где живёт kubelet:

```bash
getent hosts nfs-server.hw22.svc.cluster.local; echo "rc=$?"
```

```text
rc=2
```

В `/etc/resolv.conf` у хоста — стаб systemd-resolved (`nameserver 127.0.0.53`, `search internal`), про `cluster.local` там ничего нет. При этом сам ClusterIP с хоста доступен:

```bash
timeout 5 bash -c 'exec 3<>/dev/tcp/10.152.183.56/2049 && echo "tcp 2049 open"'
```

```text
tcp 2049 open
```

Дальше два тестовых пода с одинаковым томом NFS, отличие только в адресе сервера (второй под получал `server: nfs-server.hw22.svc.cluster.local`):

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: nfs-ip-test
  namespace: hw22
spec:
  restartPolicy: Never
  containers:
    - name: c
      image: busybox:1.36
      command: ["/bin/sh", "-c", "sleep 300"]
      volumeMounts:
        - name: d
          mountPath: /d
  volumes:
    - name: d
      nfs:
        server: 10.152.183.56
        path: /
```

С IP поднялся сразу:

```text
NAME          READY   STATUS    RESTARTS   AGE
nfs-ip-test   1/1     Running   0          15s

Normal  Pulled   15s  kubelet  spec.containers{c}: Container image "busybox:1.36" already present on machine
Normal  Started  15s  kubelet  spec.containers{c}: Container started
```

С DNS-именем — та же картина, что у provisioner'а:

```text
NAME           READY   STATUS              RESTARTS   AGE
nfs-dns-test   0/1     ContainerCreating   0          25s
```

Объяснение простое: `volumes.nfs` монтирует kubelet, а не под, и делает это в сетевом namespace хоста. CoreDNS обслуживает только поды, у хоста своего резолвера для `cluster.local` нет, поэтому `mount.nfs` не может преобразовать имя в адрес и молча висит в ожидании. Внутри пода то же имя резолвится без проблем:

```bash
kubectl exec -n hw22 deploy/multitool-nfs -- getent hosts nfs-server.hw22.svc.cluster.local
```

```text
10.152.183.56     nfs-server.hw22.svc.cluster.local  nfs-server.hw22.svc.cluster.local
```

Запрос уходит на CoreDNS (`10.152.183.10`), а kubelet к этой DNS отношения не имеет.

Поэтому в `NFS_SERVER` и в `volumes.nfs.server` стоит ClusterIP `10.152.183.56`, а в Service этот адрес зафиксирован полем `clusterIP`, чтобы он не поменялся при пересоздании сервиса. Альтернативы (править `/etc/hosts` или resolv.conf хоста) требуют root на узле, чего в этой среде нет.

После правки под стартовал, в логе видно получение лиза:

```bash
kubectl logs -n hw22 deploy/hw22-nfs-provisioner --tail=5
```

```text
I1028 08:38:41.260410       1 leaderelection.go:242] attempting to acquire leader lease  hw22/hw22.nfs.client...
I1028 08:38:41.265700       1 leaderelection.go:252] successfully acquired lease hw22/hw22.nfs.client
I1028 08:38:41.265813       1 controller.go:820] Starting provisioner controller hw22.nfs.client_hw22-nfs-provisioner-6b5c4ddb9-557sl_323a1c87-5dd7-4268-88d2-6a349b380b8c!
I1028 08:38:41.365894       1 controller.go:869] Started provisioner controller hw22.nfs.client_hw22-nfs-provisioner-6b5c4ddb9-557sl_323a1c87-5dd7-4268-88d2-6a349b380b8c!
```

```bash
kubectl get sc
```

```text
NAME         PROVISIONER       RECLAIMPOLICY   VOLUMEBINDINGMODE   ALLOWVOLUMEEXPANSION   AGE
nfs-client   hw22.nfs.client   Delete          Immediate           false                  3m42s
```

### PVC и приложение

PVC на 500 Mi класса `nfs-client` и Deployment с одним multitool, который монтирует том в `/data` ([manifests/nfs/app.yaml](manifests/nfs/app.yaml)):

```bash
kubectl apply -f manifests/nfs/app.yaml
kubectl rollout status deploy/multitool-nfs -n hw22 --timeout=120s
kubectl get pvc -n hw22
kubectl get pods -n hw22
```

```text
persistentvolumeclaim/nfs-pvc created
deployment.apps/multitool-nfs created
deployment "multitool-nfs" successfully rolled out
NAME      STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   AGE
nfs-pvc   Bound    pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a   500Mi      RWX            nfs-client     2s

NAME                                   READY   STATUS    RESTARTS   AGE
hw22-nfs-provisioner-6b5c4ddb9-557sl   1/1     Running   0          9s
multitool-nfs-f4889c7f6-n2bjx          1/1     Running   0          2s
nfs-server-8467d467b9-xnn7x            1/1     Running   0          5m49s
```

PV появился сам, за пару секунд, с именем из UID претендента. Запись и чтение изнутри пода:

```bash
POD=$(kubectl get pod -n hw22 -l app=multitool-nfs -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n hw22 $POD -- sh -c 'echo "привет с NFS, $(date -Iseconds)" > /data/hello.txt; cat /data/hello.txt; ls -l /data; df -h /data | tail -1'
```

```text
привет с NFS, 2026-10-28T08:38:50+00:00
total 4
-rw-r--r--    1 root     root            47 Oct 28 08:38 hello.txt
                        955.1G    804.8G    144.2G  85% /data
```

`df` показывает объём всего раздела с NFS-экспортом, а не 500 Mi из PVC: для NFS размер в PV — только учётная величина, ядро квоту не применяет.

Состояние PV и PVC:

```bash
kubectl -n hw22 get pv,pvc
kubectl describe pv pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a
```

```text
NAME                                                        CAPACITY   ACCESS MODES   RECLAIM POLICY   STATUS   CLAIM          STORAGECLASS   AGE
persistentvolume/pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a   500Mi      RWX            Delete           Bound    hw22/nfs-pvc   nfs-client     6s

NAME                            STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   AGE
persistentvolumeclaim/nfs-pvc   Bound    pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a   500Mi      RWX            nfs-client     6s

Name:            pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a
Labels:          <none>
Annotations:     pv.kubernetes.io/provisioned-by: hw22.nfs.client
Finalizers:      [kubernetes.io/pv-protection]
StorageClass:    nfs-client
Status:          Bound
Claim:           hw22/nfs-pvc
Reclaim Policy:  Delete
Access Modes:    RWX
VolumeMode:      Filesystem
Capacity:        500Mi
Node Affinity:   <none>
Message:
Source:
    Type:      NFS (an NFS mount that lasts the lifetime of a pod)
    Server:    10.152.183.56
    Path:      /hw22-nfs-pvc-pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a
    ReadOnly:  false
```

Каталог под том provisioner создал сам, имя — `<namespace>-<pvc>-<pv>`:

```bash
kubectl exec -n hw22 deploy/nfs-server -- ls -l /exports
ls -l /var/snap/microk8s/common/hw22-nfs/
cat /var/snap/microk8s/common/hw22-nfs/hw22-nfs-pvc-pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a/hello.txt
```

```text
drwxrwxrwx    1 root     root            18 Oct 28 08:38 hw22-nfs-pvc-pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a
drwxrwxrwx 1 root root 18 Oct 28 13:38 hw22-nfs-pvc-pvc-5439697b-3a7b-4fd6-8a06-6148dee7918a
привет с NFS, 2026-10-28T08:38:50+00:00
```

Перезапуск пода файл не теряет — том живёт на NFS-сервере, а не в контейнере:

```bash
kubectl rollout restart deploy/multitool-nfs -n hw22
kubectl rollout status deploy/multitool-nfs -n hw22 --timeout=120s
kubectl exec -n hw22 deploy/multitool-nfs -- cat /data/hello.txt
```

```text
deployment "multitool-nfs" successfully rolled out
привет с NFS, 2026-10-28T08:38:50+00:00
```

### Чем это отличается от задания 1

В первом задании PV — ручной объект: я сам задал имя, размер, класс и каталог на узле, сам следил, чтобы PVC и PV сошлись по классу, и сам решал, что делать с томом после освобождения. Здесь PV создаёт контроллер: PVC с классом `nfs-client` попал в очередь provisioner'а, тот создал каталог на NFS и сгенерировал объект PV с именем `pvc-<uid>` и аннотацией `pv.kubernetes.io/provisioned-by: hw22.nfs.client`. Класс задаёт и политику: `reclaimPolicy: Delete` — при удалении PVC provisioner удалит и каталог (при `archiveOnDelete: true` переименовал бы в `archived-...`), тогда как ручной `hw22-pv` с `Retain` остался нетронутым.

Данные лежат не на локальном диске пода и не в отдельном hostPath, а в каталоге NFS-экспорта, который сам смонтирован с `/var/snap/microk8s/common/hw22-nfs` на узле. Практическая разница: `ReadWriteMany` вместо `ReadWriteOnce` — один том можно смонтировать в несколько подов одновременно, а том переживает не только под, но и пересоздание Deployment. Плата за это — отдельный сервер NFS и его доступность: без `nfs-server` ни один под с этим томом не стартует.

---

## Итог

- Ручной hostPath PV связался с PVC по классу `manual`, оба контейнера видели один файл, прирост строк подтверждён замерами (16 → 18 за 11 секунд).
- После удаления PVC том остался `Released` с `claimRef` на старый PVC — `Retain` не даёт контроллеру освободить его до `Available`.
- Удаление PV не тронуло данные: файл `data.txt` на 1560 байт остался в `/var/snap/microk8s/common/hw22-data`.
- Динамический провижининг на NFS заработал после замены DNS-имени сервиса на ClusterIP: kubelet монтирует NFS с хоста, где `cluster.local` не резолвится.
- Provisioner создал PV `pvc-5439697b-...` и каталог `/hw22-nfs-pvc-pvc-5439697b-...`, файл читается после перезапуска пода.
