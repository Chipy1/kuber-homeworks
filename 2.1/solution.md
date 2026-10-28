# Домашнее задание 2.1. Хранение в Kubernetes

Работал на живом MicroK8s v1.35.6 (один узел `b550-tomahawk`, containerd 2.1.6, Arch Linux). Все ресурсы задания собраны в namespace `hw21`, манифесты лежат в [manifests/](manifests/).

## Задание 1. Deployment из busybox и multitool с общей директорией

Манифест: [manifests/deployment-shared-volume.yaml](manifests/deployment-shared-volume.yaml).

Оба контейнера находятся в одном поде, поэтому вместо PVC хватило `emptyDir`: том создаётся при старте пода и удаляется вместе с ним, а монтируется он в `/shared` сразу в оба контейнера. busybox раз в 5 секунд дописывает в `/shared/data.txt` строку с датой, multitool держит на этом файле `tail -F` и печатает всё, что туда попадает.

```yaml
      volumes:
        - name: shared
          emptyDir: {}
```

В контейнере multitool перед `tail -F` стоит `touch /shared/data.txt` — иначе получилась бы гонка: если multitool стартует первым, `tail` может не успеть открыть файл. С `touch` файл существует всегда, а дописывает в него уже busybox.

Создал namespace и применил манифесты:

```
$ kubectl apply -f manifests/
namespace/hw21 created
deployment.apps/shared-volume created
daemonset.apps/node-logs created

$ kubectl -n hw21 rollout status deploy/shared-volume
deployment "shared-volume" successfully rolled out
```

### Первый замер логов

```
$ kubectl -n hw21 logs shared-volume-67d7447b6b-c2425 -c busybox --tail=4
wrote '2026-10-28 08:31:06 busybox tick' to /shared/data.txt
wrote '2026-10-28 08:31:11 busybox tick' to /shared/data.txt
wrote '2026-10-28 08:31:16 busybox tick' to /shared/data.txt
wrote '2026-10-28 08:31:21 busybox tick' to /shared/data.txt

$ kubectl -n hw21 logs shared-volume-67d7447b6b-c2425 -c multitool --tail=4
2026-10-28 08:31:06 busybox tick
2026-10-28 08:31:11 busybox tick
2026-10-28 08:31:16 busybox tick
2026-10-28 08:31:21 busybox tick

$ kubectl -n hw21 exec shared-volume-67d7447b6b-c2425 -c multitool -- sh -c 'wc -l /shared/data.txt'
5 /shared/data.txt
```

Строки в логах multitool появились не сами по себе — он их прочитал из файла, который написал busybox. Время в контейнерах UTC, на узле часовой пояс +05:00, поэтому метки не совпадают с системными логами из задания 2.

### Второй замер через 30 секунд

```
$ kubectl -n hw21 logs shared-volume-67d7447b6b-c2425 -c busybox --tail=6
wrote '2026-10-28 08:31:31 busybox tick' to /shared/data.txt
wrote '2026-10-28 08:31:36 busybox tick' to /shared/data.txt
wrote '2026-10-28 08:31:41 busybox tick' to /shared/data.txt
wrote '2026-10-28 08:31:46 busybox tick' to /shared/data.txt
wrote '2026-10-28 08:31:51 busybox tick' to /shared/data.txt
wrote '2026-10-28 08:31:56 busybox tick' to /shared/data.txt

$ kubectl -n hw21 logs shared-volume-67d7447b6b-c2425 -c multitool --tail=6
2026-10-28 08:31:31 busybox tick
2026-10-28 08:31:36 busybox tick
2026-10-28 08:31:41 busybox tick
2026-10-28 08:31:46 busybox tick
2026-10-28 08:31:51 busybox tick
2026-10-28 08:31:56 busybox tick

$ kubectl -n hw21 exec shared-volume-67d7447b6b-c2425 -c multitool -- sh -c 'wc -l /shared/data.txt; tail -n 3 /shared/data.txt'
11 /shared/data.txt
2026-10-28 08:31:46 busybox tick
2026-10-28 08:31:51 busybox tick
2026-10-28 08:31:56 busybox tick
```

За 30 секунд файл вырос с 5 до 11 строк, то есть ровно на 6 записей с шагом 5 секунд. В логах multitool те же самые строки, что и в логах busybox, — обмен через `emptyDir` работает.

Отдельно про правку манифеста: сначала busybox писал только через `>> /shared/data.txt`, и `kubectl logs -c busybox` был пустым, потому что в stdout ничего не уходило. Добавил второй `echo` в stdout, чтобы логи обоих контейнеров можно было сравнивать. Логика записи в файл от этого не изменилась.

## Задание 2. DaemonSet, читающий логи узла

Манифест: [manifests/daemonset-hostlogs.yaml](manifests/daemonset-hostlogs.yaml).

Каталог `/var/log` узла примонтирован через `hostPath` в `/host-log`, причём только на чтение — и в томе, и в `volumeMounts`:

```yaml
      volumes:
        - name: host-log
          hostPath:
            path: /var/log
            type: Directory
```

Tolerations заданы максимально широко, чтобы под попал на узел с любыми taints:

```
$ kubectl -n hw21 get ds node-logs -o jsonpath='{.spec.template.spec.tolerations}'
[{"operator":"Exists"}]
```

Сейчас на узле taints нет вообще (`kubectl get nodes -o jsonpath='{.items[0].spec.taints}'` возвращает пустоту), но `operator: Exists` без key оставлен намеренно: DaemonSet тогда переживёт появление служебных taints вроде `node.kubernetes.io/disk-pressure` или `unschedulable` при обслуживании узла.

### Про syslog на Arch Linux

Задание требует прочитать `/var/log/syslog`, но на стенде Arch Linux, где логирует systemd-journald, а не rsyslog. Текстового `/var/log/syslog` тут нет и не появится. Проверил это и на хосте, и изнутри пода:

```
$ ls -l /var/log/syslog
ls: cannot access '/var/log/syslog': No such file or directory

$ kubectl -n hw21 exec ds/node-logs -- ls -la /host-log/syslog /host-log/messages
ls: /host-log/syslog: No such file or directory
ls: /host-log/messages: No such file or directory
```

journald хранит логи в бинарных файлах `/var/log/journal`, которые видны в поде, но обычным `tail`/`cat` их не прочитать:

```
$ kubectl -n hw21 exec ds/node-logs -- ls /host-log/journal
a8a11d46af6040628d5ca22444927fc1
remote
```

Поэтому для демонстрации взял реальный текстовый лог узла — `/var/log/pacman.log`. Это тот же самый hostPath `/var/log`, тот же DaemonSet и то же чтение файла узла из пода, только файл, который на этой системе действительно есть и обновляется.

Сначала посмотрел, что вообще доступно в `/host-log`, и убедился, что монтирование read-only:

```
$ kubectl -n hw21 exec ds/node-logs -- sh -c 'mount | grep host-log'
/dev/nvme0n1p2 on /host-log type btrfs (ro,noatime,compress=zstd:3,ssd,discard=async,space_cache=v2,subvolid=259,subvol=/@log)

$ kubectl -n hw21 exec ds/node-logs -- touch /host-log/probe
touch: /host-log/probe: Read-only file system
```

`ro` в выводе `mount` и ошибка при `touch` подтверждают, что контейнер видит логи узла, но изменить их не может.

Список каталога (сократил середину):

```
$ kubectl -n hw21 exec ds/node-logs -- ls /host-log
AmneziaVPN
Calamares.log
README
Xorg.0.log
Xorg.0.log.old
...
journal
...
pacman.log
pince.log
pods
...
```

И чтение самого лога:

```
$ kubectl -n hw21 exec ds/node-logs -- wc -l /host-log/pacman.log /host-log/Xorg.0.log
    40571 /host-log/pacman.log
     1594 /host-log/Xorg.0.log

$ kubectl -n hw21 exec ds/node-logs -- tail -n 20 /host-log/pacman.log
[2026-10-22T14:41:46+0500] [ALPM-SCRIPTLET] foreign	python-readchar
[2026-10-22T14:41:46+0500] [ALPM-SCRIPTLET] foreign	python-uuid7
[2026-10-22T14:41:46+0500] [ALPM] running 'update-desktop-database.hook'...
[2026-10-22T14:41:59+0500] [PACMAN] Running 'pacman --sync -y --'
[2026-10-22T14:41:59+0500] [PACMAN] synchronizing package lists
[2026-10-22T15:02:24+0500] [PACMAN] Running 'pacman --sync -- hashcat'
[2026-10-22T15:02:27+0500] [ALPM] transaction started
[2026-10-22T15:02:27+0500] [ALPM] installed hashcat (1:7.1.2-2)
[2026-10-22T15:02:27+0500] [ALPM] transaction completed
[2026-10-22T15:02:27+0500] [ALPM] running '35-systemd-update.hook'...
[2026-10-24T11:44:26+0500] [PACMAN] Running 'pacman --sync -- hcxtools'
[2026-10-24T11:44:28+0500] [ALPM] transaction started
[2026-10-24T11:44:28+0500] [ALPM] installed hcxtools (7.1.2-1)
[2026-10-24T11:44:29+0500] [ALPM] transaction completed
[2026-10-24T11:44:29+0500] [ALPM] running '35-systemd-update.hook'...
[2026-10-26T22:43:52+0500] [PACMAN] Running 'pacman --sync -- kubectl'
[2026-10-26T22:43:54+0500] [ALPM] transaction started
[2026-10-26T22:43:54+0500] [ALPM] installed kubectl (1.37.1-1)
[2026-10-26T22:43:55+0500] [ALPM] transaction completed
[2026-10-26T22:43:55+0500] [ALPM] running '35-systemd-update.hook'...
```

Это настоящий лог хоста: в нём видно, как на узел 26 октября ставился `kubectl 1.37.1-1`. Внутри пода тот же файл открывается на чтение, хотя снаружи он принадлежит root.

### Состояние ресурсов

```
$ kubectl get ds,pods -n hw21 -o wide
NAME                       DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR   AGE     CONTAINERS   IMAGES                    SELECTOR
daemonset.apps/node-logs   1         1         1       1            1           <none>          2m45s   multitool    wbitt/network-multitool   app=node-logs

NAME                                 READY   STATUS    RESTARTS   AGE     IP            NODE            NOMINATED NODE   READINESS GATES
pod/node-logs-ms8w7                  1/1     Running   1          2m45s   10.1.184.61   b550-tomahawk   <none>           <none>
pod/shared-volume-67d7447b6b-c2425   2/2     Running   0          73s     10.1.184.17   b550-tomahawk   <none>           <none>
```

Один рестарт у пода `node-logs` — следствие сбоя в начале работы: первый `kubectl apply` вернул

```
error: error validating "manifests/deployment-shared-volume.yaml": error validating data: failed to download openapi: Get "https://192.168.50.225:16443/openapi/v2?timeout=32s": dial tcp 192.168.50.225:16443: connect: connection refused
```

apiserver на несколько секунд пропал, контейнер пода успел перезапуститься. Повторил `kubectl apply` — прошёл без ошибок, дальше под стабильно `Running`, `READY 1/1`. Оба пода находятся на единственном узле `b550-tomahawk`, что для DaemonSet с одной нодой и ожидалось.

## Итог

- Общий `emptyDir` в одном поде даёт два контейнера с доступом к одному каталогу: запись busybox сразу видна multitool через `tail -F`.
- За 30 секунд файл вырос с 5 до 11 строк с шагом ровно 5 секунд.
- DaemonSet с `hostPath: /var/log` и `readOnly` смонтировал лог-каталог узла в `/host-log`; запись из пода запрещена (`Read-only file system`).
- На Arch Linux `/var/log/syslog` отсутствует из-за journald, поэтому читал реальный текстовый `/var/log/pacman.log`; механизм hostPath от этого не меняется.
- `tolerations: [{operator: Exists}]` — под DaemonSet стартует на узле с любыми taints.
