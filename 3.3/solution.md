# Домашнее задание к занятию «Как работает сеть в K8s»

**Стенд:** MicroK8s `v1.35.6` на одном узле `b550-tomahawk` (Arch Linux, kernel `7.2.11-arch1-1`), runtime containerd `2.1.6`.
CNI — Calico `v3.29.3` (`calico/node:v3.29.3`, `calico/kube-controllers:v3.29.3`), IPPool `default-ipv4-ippool` `10.1.0.0/16` с `vxlanMode: Always`, поэтому трафик между подами идёт через интерфейс `vxlan.calico`, а поды получают адреса из блока `10.1.184.0/24`.

## Задание 1. Сетевая политика для frontend → backend → cache

### 1.1. Namespace, deployments, services

В задании namespace назван `App`, но имена объектов в Kubernetes должны быть в нижнем регистре, поэтому namespace создал как `app` — на смысл это не влияет.

Файлы:

* [manifests/namespace.yaml](manifests/namespace.yaml)
* [manifests/deployments.yaml](manifests/deployments.yaml)
* [manifests/services.yaml](manifests/services.yaml)
* [manifests/netpol-01-default-deny.yaml](manifests/netpol-01-default-deny.yaml)
* [manifests/netpol-02-allow-dns.yaml](manifests/netpol-02-allow-dns.yaml)
* [manifests/netpol-03-allow-frontend-backend.yaml](manifests/netpol-03-allow-frontend-backend.yaml)
* [manifests/netpol-04-allow-backend-cache.yaml](manifests/netpol-04-allow-backend-cache.yaml)

Все три deployment'а одинаковые, отличается только имя и метка. Например frontend:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
  namespace: app
  labels:
    app: frontend
spec:
  replicas: 1
  selector:
    matchLabels:
      app: frontend
  template:
    metadata:
      labels:
        app: frontend
    spec:
      containers:
        - name: frontend
          image: wbitt/network-multitool
          env:
            - name: HTTP_PORT
              value: "8080"
          ports:
            - name: http
              containerPort: 8080
```

`HTTP_PORT=8080` нужен, потому что entrypoint образа `network-multitool` поднимает nginx именно на этом порту (по умолчанию 80). Проверять доступ будем через сервисы, у которых порт 80 проксируется на `targetPort: 8080`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: app
spec:
  type: ClusterIP
  selector:
    app: backend
  ports:
    - name: http
      port: 80
      targetPort: 8080
      protocol: TCP
```

Сервисы `frontend` и `cache` отличаются только `metadata.name` и `spec.selector`.

Первый `kubectl apply -f manifests/` споткнулся на порядке файлов: kubectl читает каталог по алфавиту, `deployments.yaml` идёт раньше `namespace.yaml`.

```text
namespace/app created
networkpolicy.networking.k8s.io/default-deny-all created
...
service/frontend created
service/backend created
service/cache created
Error from server (NotFound): error when creating "manifests/deployments.yaml": namespaces "app" not found
```

Namespace и политики при этом создались, deployment'ы — нет. Повторил apply только для них:

```bash
kubectl apply -f manifests/deployments.yaml
kubectl -n app rollout status deploy/frontend
kubectl -n app rollout status deploy/backend
kubectl -n app rollout status deploy/cache
```

```text
deployment.apps/frontend created
deployment.apps/backend created
deployment.apps/cache created
deployment "frontend" successfully rolled out
deployment "backend" successfully rolled out
deployment "cache" successfully rolled out
```

Итоговое состояние:

```text
$ kubectl -n app get deploy,po -o wide
NAME                       READY   UP-TO-DATE   AVAILABLE   AGE     CONTAINERS   IMAGES                    SELECTOR
deployment.apps/backend    1/1     1            1           4m34s   backend      wbitt/network-multitool   app=backend
deployment.apps/cache      1/1     1            1           4m34s   cache        wbitt/network-multitool   app=cache
deployment.apps/frontend   1/1     1            1           4m34s   frontend     wbitt/network-multitool   app=frontend

NAME                            READY   STATUS    RESTARTS   AGE     IP            NODE            NOMINATED NODE   READINESS GATES
pod/backend-b6b89879-9q7dh      1/1     Running   0          3m36s   10.1.184.63   b550-tomahawk   <none>           <none>
pod/cache-6b6d46999f-cn9bw      1/1     Running   0          3m36s   10.1.184.14   b550-tomahawk   <none>           <none>
pod/frontend-7bcd556b57-l6wrz   1/1     Running   0          3m36s   10.1.184.7    b550-tomahawk   <none>           <none>

$ kubectl -n app get svc
NAME       TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)   AGE
backend    ClusterIP   10.152.183.139   <none>        80/TCP    4m38s
cache      ClusterIP   10.152.183.51    <none>        80/TCP    4m38s
frontend   ClusterIP   10.152.183.71   <none>        80/TCP    4m38s

$ kubectl -n app get endpoints
NAME       ENDPOINTS          AGE
backend    10.1.184.63:8080   3m37s
cache      10.1.184.14:8080   3m37s
frontend   10.1.184.7:8080    3m37s
```

Подождал ~1 минуту между созданием deployment'ов и появлением подов: rollout status всё это время показывал `0 of 1 updated replicas are available`, то есть поды ещё не успели подняться. `Running` и `1/1 READY` дождался, дальше проверял только на живых подах.

### 1.2. Политики

Логика такая: сначала один «зонтичный» запрет на всё, потом отдельные точечные разрешения. Политики в Kubernetes складываются по логике ИЛИ (что разрешила хотя бы одна — то пропущено), поэтому один default-deny и несколько allow работают вместе без конфликтов.

Базовый запрет ([manifests/netpol-01-default-deny.yaml](manifests/netpol-01-default-deny.yaml)):

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: app
spec:
  podSelector: {}          # пустой селектор = все поды namespace
  policyTypes:
    - Ingress
    - Egress
```

Ключевой момент: `policyTypes: [Ingress, Egress]` с пустыми правилами означает «изолировать поды по обоим направлениям, разрешений нет». Именно поэтому egress тоже режется — и вместе с ним DNS, который ходит на CoreDNS по UDP/53. Без отдельного разрешения curl падал бы не на политике к backend, а на резолве имени. Поэтому следующая политика разрешает порт 53 всем подам namespace ([netpol-02-allow-dns.yaml](manifests/netpol-02-allow-dns.yaml)):

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns-egress
  namespace: app
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
```

Оба селектора в одном элементе `to` объединяются через И: поды с меткой `k8s-app=kube-dns` именно в namespace `kube-system`. Метка `kubernetes.io/metadata.name` проставляется на namespace автоматически, начиная с 1.21, отдельно её вешать не пришлось. UDP/53 — обычные запросы, TCP/53 — ответы больше 512 байт и fallback.

Дальше разрешение цепочки frontend → backend → cache. В NetworkPolicy правило нужно с двух сторон: egress у источника и ingress у получателя. Одного ingress-правила мало, потому что исходящий трафик у frontend тоже зарезан default-deny. Файл [netpol-03-allow-frontend-backend.yaml](manifests/netpol-03-allow-frontend-backend.yaml):

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-to-backend
  namespace: app
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: frontend
      ports:
        - protocol: TCP
          port: 8080
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-egress-to-backend
  namespace: app
spec:
  podSelector:
    matchLabels:
      app: frontend
  policyTypes:
    - Egress
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: backend
      ports:
        - protocol: TCP
          port: 8080
```

Здесь `podSelector` без `namespaceSelector` — это поды только в своём namespace, то есть `app`. Под с такими же метками в другом namespace правила не получит.

Порт в политике — это порт контейнера на поде-получателе (8080), а не порт сервиса (80). Поэтому `curl http://backend` (порт 80 сервиса) проходит: kube-proxy делает DNAT на 8080 ещё до того, как пакет дойдёт до цепочки политик, и проверка порта видит уже 8080. А прямой запрос на podIP:80 не пройдёт — об этом ниже.

Пара backend → cache сделана так же ([netpol-04-allow-backend-cache.yaml](manifests/netpol-04-allow-backend-cache.yaml)), отличается только селекторами: ingress у `app=cache` от `app=backend`, egress у `app=backend` к `app=cache`, порт 8080.

### 1.3. Проверки

Проверял изнутри подов через `kubectl exec`: exec идёт не по сети подов, а через kubelet и API-сервер, поэтому сами политики на него не действуют, а curl внутри пода уже подчиняется правилам.

Сначала отделил DNS от политики — резолв работает у всех:

```text
$ kubectl -n app exec deploy/frontend -- nslookup backend
;; Got recursion not available from 10.152.183.10
Server:		10.152.183.10
Address:	10.152.183.10#53

Name:	backend.app.svc.cluster.local
Address: 10.152.183.139
;; Got recursion not available from 10.152.183.10
```

`Got recursion not available` — косметика busybox-nslookup при обращении к CoreDNS, имя при этом разрезолвилось в ClusterIP сервиса.

Разрешённые связи:

```text
$ kubectl -n app exec deploy/frontend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://backend
200

$ kubectl -n app exec deploy/backend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://cache
200
```

Запрещённые:

```text
$ kubectl -n app exec deploy/frontend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://cache
000
curl: (28) Connection timed out after 5000 milliseconds
command terminated with exit code 28

$ kubectl -n app exec deploy/cache -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://backend
000
curl: (28) Connection timed out after 5001 milliseconds
command terminated with exit code 28

$ kubectl -n app exec deploy/cache -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://frontend
000
curl: (28) Connection timed out after 5001 milliseconds
command terminated with exit code 28
```

Именно таймаут, а не `Connection refused`: пакеты молча дропаются в iptables, SYN уходит в никуда, curl ждёт до `-m 5`. Заодно проверил, что дело не только в метках, но и в порту:

```text
$ kubectl -n app exec deploy/frontend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://10.1.184.63:8080
200
$ kubectl -n app exec deploy/frontend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://10.1.184.63:80
000
curl: (28) Connection timed out after 5001 milliseconds
```

Прямой podIP на 8080 разрешён, тот же podIP на 80 — нет, потому что в политике указан только 8080.

Выход наружу: DNS разрешён, всё остальное нет.

```text
$ kubectl -n app exec deploy/frontend -- nslookup ya.ru
...
Non-authoritative answer:
Name:	ya.ru
Address: 77.88.44.242
Name:	ya.ru
Address: 77.88.55.242
Name:	ya.ru
Address: 5.255.255.242

$ kubectl -n app exec deploy/frontend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://ya.ru
000
curl: (28) Connection timed out after 5000 milliseconds

$ kubectl -n app exec deploy/frontend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' https://1.1.1.1
000
curl: (28) Connection timed out after 5001 milliseconds
```

Имя резолвится (запрос ушёл на CoreDNS, а тот сходил наверх), а TCP в интернет не идёт: egress разрешён только к backend на 8080 и к DNS. Запрос на `1.1.1.1` сделан специально без DNS, чтобы показать, что режется именно соединение.

Отдельно проверил, что DNS-правило действительно нужно, а не «на всякий случай». Удалил `allow-dns-egress`, подождал пару секунд и повторил проверки:

```text
$ kubectl -n app delete networkpolicy allow-dns-egress
networkpolicy.networking.k8s.io "allow-dns-egress" deleted from app namespace

$ kubectl -n app exec deploy/frontend -- nslookup -timeout=2 -retry=1 backend
;; communications error to 10.152.183.10#53: timed out
;; no servers could be reached
command terminated with exit code 1

$ kubectl -n app exec deploy/frontend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://backend
000
curl: (28) Resolving timed out after 5000 milliseconds
```

Обратите внимание на текст ошибки: `Resolving timed out`, а не `Connection timed out`. Без DNS-исключения curl вообще не доходит до соединения с backend, и по такой ошибке легко принять DNS-проблему за слишком строгую политику. Вернул файл обратно — резолв и ответ 200 восстановились:

```bash
kubectl apply -f manifests/netpol-02-allow-dns.yaml
```

```text
$ kubectl -n app exec deploy/frontend -- curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://backend
200
```

Сводка по проверкам:

| Откуда | Куда | Результат |
|---|---|---|
| frontend | backend:80 (сервис) | 200, разрешено |
| backend | cache:80 (сервис) | 200, разрешено |
| frontend | cache:80 (сервис) | `curl: (28) Connection timed out` |
| cache | backend:80 (сервис) | `curl: (28) Connection timed out` |
| cache | frontend:80 (сервис) | `curl: (28) Connection timed out` |
| frontend | backend podIP:8080 | 200 |
| frontend | backend podIP:80 | `curl: (28) Connection timed out` |
| frontend | DNS `ya.ru` | резолвится (77.88.44.242) |
| frontend | TCP наружу (`ya.ru`, `1.1.1.1`) | `curl: (28) Connection timed out` |

### 1.4. Что в кластере и как это применяет Calico

```text
$ kubectl -n app get networkpolicy
NAME                               POD-SELECTOR   AGE
allow-backend-egress-to-cache      app=backend    4m38s
allow-backend-to-cache             app=cache      4m38s
allow-dns-egress                   <none>         25s
allow-frontend-egress-to-backend   app=frontend   4m38s
allow-frontend-to-backend          app=backend    4m38s
default-deny-all                   <none>         4m38s
```

(`allow-dns-egress` моложе остальных, потому что его я удалял и создавал заново в эксперименте выше.)

YAML целиком — в манифестах, `kubectl -n app get networkpolicy -o yaml` выдаёт их же плюс служебную аннотацию `last-applied-configuration`. Для примера короткая политика:

```text
$ kubectl -n app get networkpolicy default-deny-all -o yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  annotations:
    kubectl.kubernetes.io/last-applied-configuration: |
      {"apiVersion":"networking.k8s.io/v1","kind":"NetworkPolicy",...,"spec":{"podSelector":{},"policyTypes":["Ingress","Egress"]}}
  creationTimestamp: "2026-10-28T08:30:03Z"
  generation: 1
  name: default-deny-all
  namespace: app
  resourceVersion: "245749"
  uid: 9fae82ba-215b-45bb-b01b-43c744ab1bc2
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
```

Как это устроено внутри Calico. Объекты `networking.k8s.io/v1 NetworkPolicy` читает `calico-kube-controllers`, переводит каждый в политику Calico с именем `knp.default.<имя>` в тире `default` и раскладывает через API. Felix на каждом узле превращает их в iptables. В комментариях к правилам это видно буквально:

```text
$ kubectl -n kube-system exec calico-node-b8wd4 -- iptables -S cali-pi-_B-YaV31fjjlPjTldnWH
-N cali-pi-_B-YaV31fjjlPjTldnWH
-A cali-pi-_B-YaV31fjjlPjTldnWH -p tcp -m comment --comment "cali:nrf8Gg-0hI0rfeAY" -m comment --comment "Policy app/knp.default.allow-frontend-to-backend ingress" -m set --match-set cali40s:9bQNtoySGjLODtxqg8Vb_j6 src -m multiport --dports 8080 -j MARK --set-xmark 0x10000/0x10000

$ kubectl -n kube-system exec calico-node-b8wd4 -- iptables -S cali-po-_24Cneq7yYURyHyRtSSJ
-N cali-po-_24Cneq7yYURyHyRtSSJ
-A cali-po-_24Cneq7yYURyHyRtSSJ -p tcp -m comment --comment "cali:-tOLcI-tm9PvUC18" -m comment --comment "Policy app/knp.default.allow-frontend-egress-to-backend egress" -m set --match-set cali40s:7Ft2r0bqcoRMlLjgRgbqV_0 dst -m multiport --dports 8080 -j MARK --set-xmark 0x10000/0x10000

$ kubectl -n kube-system exec calico-node-b8wd4 -- iptables -S cali-pi-_EgcAyoEmI5fXFnsEUAL
-N cali-pi-_EgcAyoEmI5fXFnsEUAL
-A cali-pi-_EgcAyoEmI5fXFnsEUAL -m comment --comment "cali:UvfZDoMtaFnkERpW" -m comment --comment "Policy app/knp.default.default-deny-all ingress"
```

Если отфильтровать вывод `iptables -S` по строке `knp.default`, получается ровно шесть правил — по одному на каждую политику из задания: ingress `allow-frontend-to-backend`, `allow-backend-to-cache`, `default-deny-all` и egress `allow-frontend-egress-to-backend`, `allow-backend-egress-to-cache`, `allow-dns-egress`. У правил `default-deny-all` целей нет вообще — только комментарий.

Разбор строки с `allow-frontend-to-backend` по частям:

* `cali-pi-…` — цепочка политик на ingress, `cali-po-…` — на egress (у egress frontend это `cali-po-_24Cneq7yYURyHyRtSSJ`, там `src` заменён на `dst`);
* `-m set --match-set cali40s:9bQN… src` — селектор подов превращён в ipset, в нём лежат IP подов с меткой `app=frontend`;
* `--dports 8080` — то самое ограничение по порту;
* `MARK --set-xmark 0x10000/0x10000` — «политика пропустила пакет», отметка в маркере.

Проверил, что в ipset реально адрес пода, а не что-то абстрактное. Сначала нашёл, какой cali-интерфейс на хосте соответствует поду:

```text
$ kubectl -n app exec frontend-7bcd556b57-l6wrz -- cat /sys/class/net/eth0/iflink
472
$ kubectl -n kube-system exec calico-node-b8wd4 -- ip -o link | grep '^472:'
472: cali15b700fbc98@if2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1450 ...
```

Дальше цепочки этого интерфейса и содержимое ipset:

```text
$ kubectl -n kube-system exec calico-node-b8wd4 -- iptables -S cali-fw-cali15b700fbc98
-N cali-fw-cali15b700fbc98
-A cali-fw-cali15b700fbc98 -m comment --comment "cali:HAwz_8TNIub3CTrs" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A cali-fw-cali15b700fbc98 -m comment --comment "cali:WOP4R9HuMBEs5cxL" -m conntrack --ctstate INVALID -j DROP
-A cali-fw-cali15b700fbc98 ... -j MARK --set-xmark 0x0/0x30000
-A cali-fw-cali15b700fbc98 -p udp ... "Drop VXLAN encapped packets originating in workloads" -m multiport --dports 4789 -j DROP
-A cali-fw-cali15b700fbc98 -p ipv4 ... "Drop IPinIP encapped packets originating in workloads" -j DROP
-A cali-fw-cali15b700fbc98 ... "Start of tier default" -j MARK --set-xmark 0x0/0x20000
-A cali-fw-cali15b700fbc98 ... -j cali-po-_x8dTgSTTkJOlJ7o7W0T
-A cali-fw-cali15b700fbc98 ... "Return if policy accepted" -j RETURN
-A cali-fw-cali15b700fbc98 ... -j cali-po-_24Cneq7yYURyHyRtSSJ
-A cali-fw-cali15b700fbc98 ... "Return if policy accepted" -j RETURN
-A cali-fw-cali15b700fbc98 ... -j cali-po-_EgcAyoEmI5fXFnsEUAL
-A cali-fw-cali15b700fbc98 ... "Return if policy accepted" -j RETURN
-A cali-fw-cali15b700fbc98 ... "Drop if no policies passed packet" -j DROP
-A cali-fw-cali15b700fbc98 ... -j cali-pro-kns.app
...
-A cali-fw-cali15b700fbc98 ... "Drop if no profiles matched" -j DROP
```

Здесь многоточием я заменил повторяющиеся `-m comment --comment "cali:<хеш>"`. Порядок проверки цепочек политик: `cali-po-_x8dTgSTTkJOlJ7o7W0T` (DNS), `cali-po-_24Cneq7yYURyHyRtSSJ` (frontend → backend) и `cali-po-_EgcAyoEmI5fXFnsEUAL` (default-deny, в ней нет ни одного правила). После них — `Drop if no policies passed packet`, затем профили namespace и service account.

Содержимое ipset'а из правила `allow-frontend-to-backend`:

```text
$ kubectl -n kube-system exec calico-node-b8wd4 -- ipset list cali40s:9bQNtoySGjLODtxqg8Vb_j6 | tail -3
Number of entries: 1
Members:
10.1.184.7
```

`10.1.184.7` — это IP пода frontend, ровно как в `kubectl get pods`. Для egress frontend и ingress cache используется один и тот же сет `cali40s:7Ft2r0bqcoRMlLjgRgbqV_0`, и в нём `10.1.184.63` — IP backend. То есть один podSelector `app=backend` даёт один ipset, который подставляется и в `src`, и в `dst` в зависимости от направления.

Логика iptables получается такая: пакет входит в per-endpoint цепочку (`cali-tw-<iface>` на вход поду, `cali-fw-<iface>` на выход), маркер обнуляется, затем по очереди проверяются цепочки политик; та, что совпала по селектору (ipset) и порту, ставит `0x10000`; после всех политик стоит `Drop if no policies passed packet` по условию «маркер так и остался нулевым». Отсюда и берётся default-deny: сама политика `default-deny-all` не содержит ни одного ACCEPT, её роль — включить изоляцию для всех подов namespace, а разрешает только то, что явно прописали остальные политики. Пустая цепочка `cali-pi-_EgcAyoEmI5fXFnsEUAL` выглядит именно так — один комментарий и никаких целей.

Ещё пара наблюдений по ходу:

* цепочки `cali-pro-kns.app` / `cali-pro-ksa.app.default` — это профили namespace и service account. Они идут после политик и пропускают трафик, если он уже прошёл проверку; если ни одна политика не сработала, до профилей дело доходит, но маркер остаётся нулевым, и всё равно DROP;
* в `cali-fw-*` есть отдельные DROP для VXLAN (UDP/4789) и IP-in-IP пакетов, «исходящих из подов» — чтобы под не мог сам заняться инкапсуляцией;
* CoreDNS за время работы перезапускался (`RESTARTS` 50 → 51) и поменял IP с `10.1.184.46` на `10.1.184.25`. Политика продолжает работать: в ipset `cali40s:TwSPtm330ezw8L_TsjzvdD8` лежит уже новый адрес. Ничего руками не правил — Felix обновляет сеты по селектору `k8s-app=kube-dns`, а не по IP.

Поды и сервисы оставил в namespace `app`, политики тоже. Временных подов для тестов не создавал — все проверки шли через `kubectl exec` в основные поды.

## Итог

* В `app` три deployment'а и три сервиса на `wbitt/network-multitool` с `HTTP_PORT=8080`, сервисы слушают 80 и проксируют на 8080.
* `default-deny-all` закрывает и ingress, и egress для всех подов namespace; дальше четыре точечные политики открывают только DNS, frontend→backend:8080 и backend→cache:8080.
* Разрешение пришлось писать с двух сторон (ingress + egress), иначе трафик режется на стороне источника.
* Без DNS-исключения ломается резолв: curl выдаёт `(28) Resolving timed out`, что легко спутать с отказом политики.
* Calico `v3.29.3` превращает NetworkPolicy в `knp.default.*` и рисует iptables-цепочки с ipset'ами по селекторам; default-deny реализован через финальный DROP по нулевому маркеру.
