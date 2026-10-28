# 3.5. Troubleshooting: web-consumer не видит auth-db

Исходный манифест лежит рядом, в [files/task.yaml](files/task.yaml), я его не правил. Внутри три объекта: Deployment `web-consumer` в namespace `web` (две реплики, образ `radial/busyboxplus:curl`, в цикле `curl auth-db`), Deployment `auth-db` в namespace `data` (`nginx:1.19.1`) и Service `auth-db` в том же `data`.

## Первый запуск: namespace не создаются

Применил ровно так, как написано в задании:

```shell
kubectl apply -f https://raw.githubusercontent.com/netology-code/kuber-homeworks/main/3.5/files/task.yaml
```

```
Error from server (NotFound): error when creating "https://raw.githubusercontent.com/netology-code/kuber-homeworks/main/3.5/files/task.yaml": namespaces "web" not found
Error from server (NotFound): error when creating "https://raw.githubusercontent.com/netology-code/kuber-homeworks/main/3.5/files/task.yaml": namespaces "data" not found
Error from server (NotFound): error when creating "https://raw.githubusercontent.com/netology-code/kuber-homeworks/main/3.5/files/task.yaml": namespaces "data" not found
```

Три ошибки на три объекта: в манифесте указаны `namespace: web` и `namespace: data`, но самих namespace там нет, а apply не создаёт их автоматически. Ничего не применилось.

Отдельно сверил скачанный файл с локальным — `diff` пустой, содержимое одинаковое, так что дальше работал с копией из репозитория.

```shell
kubectl create namespace web
kubectl create namespace data
kubectl apply -f files/task.yaml
```

```
namespace/web created
namespace/data created
deployment.apps/web-consumer created
deployment.apps/auth-db created
service/auth-db created
```

## Вторая заминка: образ web-consumer не тянется

После создания namespace поды `auth-db` пошли нормально, а `web-consumer` встал:

```
NAME                            READY   STATUS             RESTARTS   AGE
web-consumer-6c5f7cc594-78bzg   0/1     ErrImagePull       0          66s
web-consumer-6c5f7cc594-l2d9z   0/1     ImagePullBackOff   0          66s
auth-db-7bdf574b5f-kw6vv        1/1     Running            1          66s
```

В events конкретика:

```
Failed to pull image "radial/busyboxplus:curl": rpc error: code = InvalidArgument
desc = failed to pull and unpack image "docker.io/radial/busyboxplus:curl":
schema 1 image manifests are no longer supported: invalid argument
```

Образ старый, собран со schema 1 manifest, а containerd 2.1.6 в этом кластере такие манифесты уже не принимает. С DNS это никак не связано, но проверить гипотезу без работающего пода нельзя, поэтому заменил только `image` на `wbitt/network-multitool` (там есть `curl` и `sh`), команду оставил дословно из исходника: `while true; do curl auth-db; sleep 5; done`. Правка вынесена в [manifests/01-web-consumer-image.yaml](manifests/01-web-consumer-image.yaml), чтобы было видно, что исходный манифест я не трогал.

```shell
kubectl apply -f manifests/01-web-consumer-image.yaml
kubectl -n web rollout status deploy/web-consumer --timeout=180s
```

```
deployment.apps/web-consumer configured
deployment "web-consumer" successfully rolled out
```

## Симптом

```
NAME                            READY   STATUS    RESTARTS   AGE   IP
web-consumer-7fbd577985-4gbs8   1/1     Running   0          6s    10.1.184.45
web-consumer-7fbd577985-866lk   1/1     Running   0          3s    10.1.184.50
auth-db-7bdf574b5f-kw6vv        1/1     Running   1          102s  10.1.184.38
```

```shell
kubectl -n web logs deploy/web-consumer --tail=8
```

```
Found 2 pods, using pod/web-consumer-7fbd577985-4gbs8
curl: (6) Could not resolve host: auth-db (Domain name not found)
curl: (6) Could not resolve host: auth-db (Domain name not found)
...
```

Сам сервис при этом живой:

```
NAME      TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)   AGE
auth-db   ClusterIP   10.152.183.180   <none>        80/TCP    3m13s

NAME      ENDPOINTS        AGE
auth-db   10.1.184.38:80   3m13s
```

Endpoints указывают на под `auth-db-7bdf574b5f-kw6vv` (10.1.184.38), то есть selector совпадает и трафик было бы куда отдать. Проблема только в имени.

## Почему короткое имя не резолвится

Посмотрел, что под получает в `/etc/resolv.conf`:

```shell
kubectl -n web exec deploy/web-consumer -- cat /etc/resolv.conf
```

```
search web.svc.cluster.local svc.cluster.local cluster.local internal
nameserver 10.152.183.10
options ndots:5
```

`10.152.183.10` — это сервис `kube-dns` в `kube-system` (за ним CoreDNS). В `search` первым идёт зона своего namespace, `web.svc.cluster.local`, и только потом общие `svc.cluster.local` и `cluster.local`. `internal` microk8s добавляет сам.

`auth-db` содержит ноль точек, а `ndots:5` означает «если в имени меньше пяти точек, сначала пробуй дописать search-домены». Поэтому резолвер честно перебрал:

```
auth-db.web.svc.cluster.local   -> NXDOMAIN
auth-db.svc.cluster.local       -> NXDOMAIN
auth-db.cluster.local           -> NXDOMAIN
auth-db.internal                -> NXDOMAIN
auth-db.                        -> NXDOMAIN
```

Записи `auth-db` в зоне `web.svc.cluster.local` нет, потому что Service создан в `data`. Он зарегистрирован как `auth-db.data.svc.cluster.local`, а сокращённых имён между namespace не бывает — это не поиск «где-нибудь в кластере», а строгое дерево зон. Отсюда и `Could not resolve host`.

Проверил это же руками из пода:

```shell
kubectl -n web exec deploy/web-consumer -- nslookup auth-db
```

```
Server:		10.152.183.10
Address:	10.152.183.10#53

** server can't find auth-db: NXDOMAIN
```

С namespace:

```shell
kubectl -n web exec deploy/web-consumer -- nslookup auth-db.data
```

```
Name:	auth-db.data.svc.cluster.local
Address: 10.152.183.180
```

Полное имя тоже отвечает:

```shell
kubectl -n web exec deploy/web-consumer -- nslookup auth-db.data.svc.cluster.local
```

```
Server:		10.152.183.10
Name:	auth-db.data.svc.cluster.local
Address: 10.152.183.180
```



## Исправление

Поменял в команде контейнера короткое имя на FQDN `auth-db.data.svc.cluster.local`, манифест — [manifests/fix.yaml](manifests/fix.yaml). Заодно там зафиксирован рабочий образ, иначе Deployment после apply откатился бы на нерабочий `radial/busyboxplus:curl` из исходника.

```yaml
      containers:
      - name: busybox
        image: wbitt/network-multitool
        command:
        - sh
        - -c
        - while true; do curl -sS http://auth-db.data.svc.cluster.local; sleep 5; done
```

```shell
kubectl apply -f manifests/fix.yaml
kubectl -n web rollout status deploy/web-consumer --timeout=120s
```

```
deployment.apps/web-consumer configured
Waiting for deployment "web-consumer" rollout to finish: 1 out of 2 new replicas have been updated...
Waiting for deployment "web-consumer" rollout to finish: 1 old replicas are pending termination...
deployment "web-consumer" successfully rolled out
```

## Проверка

Поды после раскатки:

```
NAME                            READY   STATUS    RESTARTS   AGE   IP
web-consumer-5cc5bd4889-8mlnl   1/1     Running   0          60s   10.1.184.12
web-consumer-5cc5bd4889-ztqq9   1/1     Running   0          61s   10.1.184.31
```

Логи (фрагмент, `kubectl -n web logs -f --tail=10 deploy/web-consumer`, снял через `timeout 12`):

```
Found 2 pods, using pod/web-consumer-5cc5bd4889-ztqq9
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
...
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>
...
```

Код ответа:

```shell
kubectl -n web exec deploy/web-consumer -- curl -s -o /dev/null -w '%{http_code}\n' http://auth-db.data.svc.cluster.local
```

```
200
```

Короткое имя, ради контроля, так и продолжает падать — это ожидаемо, потому что DNS-имя осталось прежним, поменялся только способ обращения:

```shell
kubectl -n web exec deploy/web-consumer -- curl -sS -o /dev/null -w '%{http_code}\n' http://auth-db
```

```
curl: (6) Could not resolve host: auth-db (Domain name not found)
000
```

Итоговое состояние:

```
NAME                           READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/web-consumer   2/2     2            2           3m12s

NAME                      READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/auth-db   1/1     1            1           3m13s
```

## Альтернатива, которую не применял

Вместо FQDN можно завести в `web` Service типа `ExternalName`, который ссылается на сервис в `data`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: auth-db
  namespace: web
spec:
  type: ExternalName
  externalName: auth-db.data.svc.cluster.local
```

Тогда исходная команда `curl auth-db` заработала бы без правки Deployment. Вариант не стал применять: `ExternalName` отдаёт CNAME на уровне DNS и не имеет ClusterIP, а имя сервиса-цели всё равно приходится писать целиком — выгоды мало, зато появляется второй объект, который надо помнить при переезде. FQDN в манифесте виднее при чтении.

## Мелочи, которые заметил по ходу

- Под `auth-db` один раз перезапустился (`RESTARTS 1`), в events видно `SandboxChanged ... Pod sandbox changed, it will be killed and re-created`. То же самое kubelet сделал и с подами `web-consumer` примерно на 46-й секунде жизни. На DNS это не влияло, но в момент пересоздания sandbox `Endpoints` сервиса на несколько секунд показывал чужой адрес `10.1.184.1` вместо `10.1.184.38`, и я сначала заподозрил рассинхрон endpoints. После стабилизации адрес вернулся к поду.
- Старые поды `web-consumer-7fbd577985-*` висели в `Terminating` примерно 30 секунд, ровно до истечения grace period. Причина в том, что PID 1 в контейнере — это `sh -c` с бесконечным циклом, SIGTERM он не обрабатывает, и kubelet добивает его SIGKILL через 30 секунд. Для проверки это не мешало, но при частых раскатках лучше вешать `trap` или запускать `curl` не через обёртку.

## Итог

- Манифест из задания не создаёт `web` и `data`, apply падает с `namespaces "web"/"data" not found` — namespace пришлось создать руками.
- Образ `radial/busyboxplus:curl` в кластере не тянется из-за schema 1 manifest, заменил на `wbitt/network-multitool`.
- Причина недоступности `auth-db`: Service живёт в `data`, а `web-consumer` обращается по короткому имени; search domains резолвера (`web.svc.cluster.local`, `svc.cluster.local`, `cluster.local`, `internal`) не ведут в чужой namespace, отсюда `curl: (6) Could not resolve host`.
- Исправление — обращение по `auth-db.data.svc.cluster.local` в [manifests/fix.yaml](manifests/fix.yaml); после раскатки `curl` отдаёт код 200 и HTML nginx в логах.
- Ресурсы остались работать: `web-consumer` 2/2 в `web`, `auth-db` 1/1 и Service `auth-db` (10.152.183.180) в `data`.
