# Домашнее задание к занятию «Запуск приложений в K8S»

Стенд прежний: MicroK8s v1.35.6 на одном узле `b550-tomahawk`, containerd 2.1.6, kubectl смотрит на `https://192.168.50.225:16443`. Все ресурсы этой работы — в namespace `hw13`, создан отдельно:

```bash
kubectl create namespace hw13
```

```text
namespace/hw13 created
```

Манифесты лежат в [manifests/](manifests/). Ниже — что применялось и что из этого вышло.

---

## Задание 1. Deployment из nginx и multitool

### 1.1. Первый запуск и ошибка

Собрал Deployment из двух контейнеров, у обоих в `containerPort` указан 80 — так, как это выглядит «по умолчанию»: nginx слушает 80, multitool тоже стартует свой nginx на 80. Манифест сохранил как [deployment-broken.yaml](manifests/deployment-broken.yaml):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: netology-deploy
  namespace: hw13
spec:
  replicas: 1
  selector:
    matchLabels:
      app: netology
  template:
    metadata:
      labels:
        app: netology
    spec:
      containers:
        - name: nginx
          image: nginx
          ports:
            - name: http
              containerPort: 80
        - name: multitool
          image: wbitt/network-multitool
          ports:
            - name: http
              containerPort: 80
```

Уже на применении kubectl предупредил, что порт 80 объявлен дважды:

```bash
kubectl apply -f manifests/deployment-broken.yaml
```

```text
Warning: spec.template.spec.containers[1].ports[0]: duplicate port definition with spec.template.spec.containers[0].ports[0]
Warning: spec.template.spec.containers[1].ports[0]: duplicate port name "http" with spec.template.spec.containers[0].ports[0], services and probes that select ports by name will use spec.template.spec.containers[0].ports[0]
deployment.apps/netology-deploy created
```

Это только предупреждение, к запуску оно отношения не имеет. Настоящая проблема вылезла позже:

```bash
kubectl -n hw13 get pods
```

```text
NAME                               READY   STATUS   RESTARTS     AGE
netology-deploy-6f758f897b-hh7pq   1/2     Error    1 (9s ago)   46s
```

Один контейнер из двух поднялся, второй циклически падал. `describe` показал причину:

```bash
kubectl -n hw13 describe pod netology-deploy-6f758f897b-hh7pq
```

```text
  multitool:
    Image:          wbitt/network-multitool
    State:          Terminated
      Reason:       Error
      Exit Code:    1
    Last State:     Terminated
      Reason:       Error
      Exit Code:    1
    Ready:          False
    Restart Count:  1
```

И лог самого контейнера:

```bash
kubectl -n hw13 logs netology-deploy-6f758f897b-hh7pq -c multitool
```

```text
WBITT Network MultiTool (with NGINX) - netology-deploy-6f758f897b-hh7pq - 10.1.184.34 - HTTP: 80 , HTTPS: 443 . (Formerly praqma/network-multitool)
2026/10/28 08:30:14 [emerg] 1#1: bind() to 0.0.0.0:80 failed (98: Address in use)
nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address in use)
```

Почему так: контейнеры пода делят один network namespace, то есть один и тот же IP и один и тот же набор портов. nginx занял `0.0.0.0:80` первым, multitool попытался занять тот же порт и получил `EADDRINUSE`. `containerPort` в манифесте — это только информационное поле, оно не разводит порты по разным «слотам», поэтому исправлять нужно именно конфигурацию самого multitool.

### 1.2. Исправление через HTTP_PORT

У образа `wbitt/network-multitool` порты задаются переменными окружения `HTTP_PORT` и `HTTPS_PORT` (по умолчанию 80 и 443). Переопределил их для контейнера multitool и заодно переименовал порт, чтобы не повторять имя `http` в двух контейнерах — [deployment.yaml](manifests/deployment.yaml):

```yaml
        - name: nginx
          image: nginx
          ports:
            - name: http
              containerPort: 80
        - name: multitool
          image: wbitt/network-multitool
          env:
            - name: HTTP_PORT
              value: "8080"
            - name: HTTPS_PORT
              value: "8443"
          ports:
            - name: http-mt
              containerPort: 8080
            - name: https-mt
              containerPort: 8443
```

```bash
kubectl apply -f manifests/deployment.yaml
kubectl -n hw13 rollout status deploy/netology-deploy --timeout=120s
```

```text
deployment "netology-deploy" successfully rolled out
```

```bash
kubectl -n hw13 get pods -o wide
```

```text
NAME                               READY   STATUS    RESTARTS   AGE   IP           NODE
netology-deploy-5747cd95b9-xvm2z   2/2     Running   0          9s    10.1.184.6   b550-tomahawk
```

Оба контейнера в поде поднялись, старый под с ошибкой ушёл в `Terminating`. В логе видно, что переменные действительно применились:

```bash
kubectl -n hw13 logs deploy/netology-deploy -c multitool
```

```text
WBITT Network MultiTool (with NGINX) - netology-deploy-5747cd95b9-xvm2z - 10.1.184.6 - HTTP: 8080 , HTTPS: 8443 . (Formerly praqma/network-multitool)
Replacing default HTTP port (80) with the value specified by the user - (HTTP_PORT: 8080).
Replacing default HTTPS port (443) with the value specified by the user - (HTTPS_PORT: 8443).
```

### 1.3. Масштабирование до двух реплик

До масштабирования в deployment была одна реплика:

```bash
kubectl -n hw13 get pods -o wide
kubectl -n hw13 get deploy
```

```text
NAME                               READY   STATUS    RESTARTS   AGE   IP           NODE
netology-deploy-5747cd95b9-xvm2z   2/2     Running   0          12s   10.1.184.6   b550-tomahawk

NAME              READY   UP-TO-DATE   AVAILABLE   AGE
netology-deploy   1/1     1            1           103s
```

Увеличил до двух:

```bash
kubectl -n hw13 scale deploy/netology-deploy --replicas=2
kubectl -n hw13 rollout status deploy/netology-deploy
```

```text
deployment.apps/netology-deploy scaled
Waiting for deployment "netology-deploy" rollout to finish: 1 of 2 updated replicas are available...
deployment "netology-deploy" successfully rolled out
```

```text
NAME                               READY   STATUS    RESTARTS   AGE   IP            NODE
netology-deploy-5747cd95b9-2rtfj   2/2     Running   0          3s    10.1.184.49   b550-tomahawk
netology-deploy-5747cd95b9-xvm2z   2/2     Running   0          12s   10.1.184.6    b550-tomahawk

NAME              READY   UP-TO-DATE   AVAILABLE   AGE
netology-deploy   2/2     2            2           103s
```

Значение в `manifests/deployment.yaml` после этого поправил на `replicas: 2`, чтобы файл не расходился с кластером.

### 1.4. Service netology-svc

Сервис должен вести на реплики обоих приложений, поэтому объявил в нём два порта: 80 — в nginx, 8080 — в multitool. Таргет-порты указал по именам контейнерных портов, тогда не приходится помнить номера из манифеста пода. [service.yaml](manifests/service.yaml):

```yaml
apiVersion: v1
kind: Service
metadata:
  name: netology-svc
  namespace: hw13
spec:
  selector:
    app: netology
  ports:
    - name: nginx
      port: 80
      targetPort: http
    - name: multitool
      port: 8080
      targetPort: http-mt
```

```bash
kubectl apply -f manifests/service.yaml
kubectl -n hw13 get svc netology-svc
kubectl -n hw13 get endpoints netology-svc
```

```text
NAME           TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)           AGE
netology-svc   ClusterIP   10.152.183.197   <none>        80/TCP,8080/TCP   0s

NAME           ENDPOINTS                                                     AGE
netology-svc   10.1.184.49:8080,10.1.184.6:8080,10.1.184.49:80 + 1 more...   0s
```

В endpoints попали оба пода по обоим портам, то есть селектор отработал.

### 1.5. Проверка из отдельного пода multitool

Создал отдельный под с multitool — [pod-multitool-client.yaml](manifests/pod-multitool-client.yaml). В нём переменные окружения не трогал, свой nginx у него слушает 80, но это уже другой под со своим IP.

```bash
kubectl apply -f manifests/pod-multitool-client.yaml
kubectl -n hw13 wait --for=condition=Ready pod/multitool-client --timeout=90s
```

```text
NAME               READY   STATUS    RESTARTS   AGE   IP            NODE
multitool-client   1/1     Running   0          2s    10.1.184.15   b550-tomahawk
```

Обращение к nginx через порт 80 сервиса:

```bash
kubectl -n hw13 exec multitool-client -- curl -s -w '\nHTTP CODE: %{http_code}\n' http://netology-svc/
```

```text
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
...
<h1>Welcome to nginx!</h1>
...
HTTP CODE: 200
```

Обращение к multitool через порт 8080 того же сервиса, несколько раз подряд — видно, что балансировка раскидывает запросы по обоим подам:

```bash
for i in 1 2 3 4; do
  kubectl -n hw13 exec multitool-client -- curl -s http://netology-svc:8080/ | grep WBITT
done
```

```text
WBITT Network MultiTool (with NGINX) - netology-deploy-5747cd95b9-xvm2z - 10.1.184.6 - HTTP: 8080 , HTTPS: 8443 . (Formerly praqma/network-multitool)
WBITT Network MultiTool (with NGINX) - netology-deploy-5747cd95b9-2rtfj - 10.1.184.49 - HTTP: 8080 , HTTPS: 8443 . (Formerly praqma/network-multitool)
WBITT Network MultiTool (with NGINX) - netology-deploy-5747cd95b9-2rtfj - 10.1.184.49 - HTTP: 8080 , HTTPS: 8443 . (Formerly praqma/network-multitool)
WBITT Network MultiTool (with NGINX) - netology-deploy-5747cd95b9-2rtfj - 10.1.184.49 - HTTP: 8080 , HTTPS: 8443 . (Formerly praqma/network-multitool)
```

Коды ответов на порт 8080:

```bash
kubectl -n hw13 exec multitool-client -- sh -c \
  'for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "%{http_code} " http://netology-svc:8080/; done; echo'
```

```text
200 200 200 200 200 200
```

Доступ из пода есть и к nginx, и к multitool.

---

## Задание 2. Старт nginx только после появления Service

Deployment [deployment-init.yaml](manifests/deployment-init.yaml): init-контейнер на busybox:1.36 крутит `nslookup` по полному DNS-имени сервиса, а основной контейнер nginx ждёт его завершения.

```yaml
      initContainers:
        - name: wait-for-svc
          image: busybox:1.36
          command:
            - sh
            - -c
            - 'until nslookup netology-init-svc.hw13.svc.cluster.local; do echo "waiting for netology-init-svc"; sleep 2; done'
      containers:
        - name: nginx
          image: nginx
          ports:
            - name: http
              containerPort: 80
```

Сервис `netology-init-svc` намеренно создал не сразу, а после демонстрации. Применил только deployment:

```bash
kubectl apply -f manifests/deployment-init.yaml
kubectl -n hw13 get pods -o wide
```

```text
NAME                               READY   STATUS     RESTARTS   AGE   IP            NODE
netology-init-79ff4c9fcd-v5mnq     0/1     Init:0/1   0          24s   10.1.184.34   b550-tomahawk
```

Под висит в `Init:0/1` — init-контейнер ещё не завершился, nginx даже не создавался. `describe` это подтверждает:

```bash
kubectl -n hw13 describe pod -l app=netology-init
```

```text
Init Containers:
  wait-for-svc:
    Image:         busybox:1.36
    State:          Running
      Started:      Wed, 28 Oct 2026 13:32:58 +0500
    Ready:          False
    Restart Count:  0
```

Лог init-контейнера — сплошные NXDOMAIN, потому что объекта с таким именем в DNS кластера пока нет:

```bash
kubectl -n hw13 logs -l app=netology-init -c wait-for-svc
```

```text
** server can't find netology-init-svc.hw13.svc.cluster.local: NXDOMAIN

waiting for netology-init-svc
```

Через 72 секунды состояние не изменилось — цикл честно ждёт:

```text
NAME                             READY   STATUS     RESTARTS   AGE
netology-init-79ff4c9fcd-v5mnq   0/1     Init:0/1   0          72s
```

Теперь создал Service — [service-init.yaml](manifests/service-init.yaml). Селектор тот же, что у пода, порт 80 в контейнер:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: netology-init-svc
  namespace: hw13
spec:
  selector:
    app: netology-init
  ports:
    - name: http
      port: 80
      targetPort: http
```

```bash
kubectl apply -f manifests/service-init.yaml
```

```text
service/netology-init-svc created
NAME                TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)   AGE
netology-init-svc   ClusterIP   10.152.183.132   <none>        80/TCP    16s
```

После появления сервиса CoreDNS начал отдавать запись, `nslookup` в init-контейнере вернул ответ, и только тогда стартовал nginx:

```bash
kubectl -n hw13 get pod -l app=netology-init -o wide
kubectl -n hw13 logs -l app=netology-init -c wait-for-svc
```

```text
NAME                             READY   STATUS    RESTARTS   AGE   IP            NODE
netology-init-79ff4c9fcd-v5mnq   1/1     Running   0          93s   10.1.184.34   b550-tomahawk
```

```text
Address:	10.152.183.10:53
Name:	netology-init-svc.hw13.svc.cluster.local
Address: 10.152.183.132
```

Пересоздавать под не пришлось: init-контейнер сам дождался DNS-записи в своём цикле, kubelet довёл под до `Running`.

---

## Итог

- Два контейнера в одном поде не могут слушать один и тот же порт: у них общий network namespace. `Address already in use` в логе multitool — прямое следствие этого, а не ошибки манифеста в `containerPort`.
- Развёл порты переменными `HTTP_PORT=8080` и `HTTPS_PORT=8443`; `containerPort` при этом стал чисто справочным, но понадобился для именованных `targetPort` в Service.
- `netology-svc` отдаёт nginx на 80 и multitool на 8080, endpoints содержат оба пода; из пода `multitool-client` оба ответа — 200.
- Init-контейнер с `nslookup` держит под в `Init:0/1` до появления Service; после создания `netology-init-svc` под сам перешёл в `Running` без перезапуска.
- В `hw13` остались: `deployment/netology-deploy` (2 реплики), `deployment/netology-init` (1 реплика), сервисы `netology-svc` и `netology-init-svc`, под `multitool-client`.
