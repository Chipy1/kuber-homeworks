# Домашнее задание 1.2 — базовые объекты Kubernetes

Кластер MicroK8s v1.35.6 на одном узле `b550-tomahawk`, клиент `kubectl` v1.37.1. Все ресурсы задания живут в отдельном namespace `hw12`, чтобы не мешать остальным работам на этом же кластере.

```console
$ kubectl create namespace hw12
namespace/hw12 created
```

## Задание 1. Pod hello-world

Манифест — [manifests/hello-world-pod.yaml](manifests/hello-world-pod.yaml):

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: hello-world
  namespace: hw12
  labels:
    app: hello-world
spec:
  containers:
    - name: echoserver
      image: gcr.io/kubernetes-e2e-test-images/echoserver:2.2
      ports:
        - containerPort: 8080
```

Образ взял ровно тот, что просили в задании. gcr.io из кластера доступен, подменять на `registry.k8s.io` не пришлось — kubelet скачал образ за 12 секунд, размер 10198788 байт.

Манифесты применил сразу все одной командой, чтобы не гонять `apply` по три раза; поэтому в выводе ниже рядом с `hello-world` уже виден под из второго задания:

```console
$ kubectl apply -f manifests/
pod/hello-world created
service/netology-svc created
pod/netology-web created
```

`describe` по первому поду:

```console
$ kubectl describe pod hello-world -n hw12 | tail -8
Events:
  Type    Reason     Age   From               Message
  ----    ------     ----  ----               -------
  Normal  Scheduled  48s   default-scheduler  Successfully assigned hw12/hello-world to b550-tomahawk
  Normal  Pulling    48s   kubelet            Pulling image "gcr.io/kubernetes-e2e-test-images/echoserver:2.2"
  Normal  Pulled     36s   kubelet            Successfully pulled image "gcr.io/kubernetes-e2e-test-images/echoserver:2.2" in 12.189s (12.189s including waiting). Image size: 10198788 bytes.
  Normal  Created    36s   kubelet            Container created
  Normal  Started    36s   kubelet            Container started
  Normal  SandboxChanged 18s kubelet           Pod sandbox changed, it will be killed and re-created.
  Normal  Pulled     15s   kubelet            Container image "gcr.io/kubernetes-e2e-test-images/echoserver:2.2" already present on machine and can be accessed by the pod
```

Тут сразу видно неприятность: после первого старта kubelet написал `SandboxChanged` и пересоздал sandbox. Из-за этого контейнер перезапустился, а в `get pods` появился `RESTARTS 1` — это не падение приложения, а следствие пересоздания песочницы (в events нет ни `BackOff`, ни `Error`). После второго старта под нормально поднялся:

```console
$ kubectl get pods -n hw12 -o wide
NAME           READY   STATUS    RESTARTS   AGE     IP            NODE            NOMINATED NODE   READINESS GATES
hello-world    1/1     Running   1          2m46s   10.1.184.33   b550-tomahawk   <none>           <none>
netology-web   1/1     Running   0          44s     10.1.184.1    b550-tomahawk   <none>           <none>
```

### Доступ к поду через port-forward

Пробросил порт 8080 на локальный 8080 (порт был свободен):

```console
$ kubectl port-forward pod/hello-world -n hw12 8080:8080
Forwarding from 127.0.0.1:8080 -> 8080
Forwarding from [::1]:8080 -> 8080
```

В другом терминале:

```console
$ curl -s localhost:8080

Hostname: hello-world

Pod Information:
	-no pod information available-

Server values:
	server_version=nginx: 1.12.2 - lua: 10010

Request Information:
	client_address=127.0.0.1
	method=GET
	real path=/
	query=
	request_version=1.1
	request_scheme=http
	request_uri=http://localhost:8080/

Request Headers:
	accept=*/*
	host=localhost:8080
	user-agent=curl/8.23.0

Request Body:
	-no body in request-
```

`Hostname: hello-world` — это имя пода, echoserver подставляет его из переменной окружения. `client_address=127.0.0.1` тоже логичен: запрос пришёл не с моей машины напрямую, а изнутри network namespace пода, куда его завёл kubelet. После проверки port-forward я погасил (`Ctrl+C` / `pkill`), процессов `port-forward` в системе не осталось.

## Задание 2. Pod netology-web и Service netology-svc

Манифесты: [manifests/netology-web-pod.yaml](manifests/netology-web-pod.yaml) и [manifests/netology-svc.yaml](manifests/netology-svc.yaml).

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: netology-web
  namespace: hw12
  labels:
    app: netology-web
spec:
  containers:
    - name: echoserver
      image: gcr.io/kubernetes-e2e-test-images/echoserver:2.2
      ports:
        - containerPort: 8080
```

```yaml
apiVersion: v1
kind: Service
metadata:
  name: netology-svc
  namespace: hw12
spec:
  type: ClusterIP
  selector:
    app: netology-web
  ports:
    - name: http
      protocol: TCP
      port: 80
      targetPort: 8080
```

Service типа ClusterIP, порт 80 на входе, 8080 на поде. Селектор `app=netology-web` — тот же лейбл, что в манифесте пода.

```console
$ kubectl get pods -n hw12 -o wide
NAME           READY   STATUS    RESTARTS   AGE   IP           NODE            NOMINATED NODE   READINESS GATES
hello-world    1/1     Running   1          2m46s 10.1.184.33  b550-tomahawk   <none>           <none>
netology-web   1/1     Running   0          44s   10.1.184.1   b550-tomahawk   <none>           <none>

$ kubectl get svc -n hw12 -o wide
NAME           TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)   AGE     SELECTOR
netology-svc   ClusterIP   10.152.183.45   <none>        80/TCP    2m46s   app=netology-web

$ kubectl get endpoints -n hw12
Warning: v1 Endpoints is deprecated in v1.33+; use discovery.k8s.io/v1 EndpointSlice
NAME           ENDPOINTS         AGE
netology-svc   10.1.184.1:8080   2m46s
```

`kubectl` на 1.35 ругается, что объект Endpoints устарел, и предлагает EndpointSlice. Посмотрел и его — данные те же, просто в новом API:

```console
$ kubectl get endpointslices -n hw12
NAME                 ADDRESSTYPE   PORTS   ENDPOINTS    AGE
netology-svc-szdph   IPv4          8080    10.1.184.1   2m46s
```

Сам сервис целиком:

```console
$ kubectl describe svc netology-svc -n hw12
Name:                     netology-svc
Namespace:                hw12
Selector:                 app=netology-web
Type:                     ClusterIP
IP:                       10.152.183.45
Port:                     http  80/TCP
TargetPort:               8080/TCP
Endpoints:                10.1.184.1:8080
Session Affinity:         None
Internal Traffic Policy:  Cluster
```

### Отдельная история с «залипшими» endpoints

Первый раз endpoints оказались с чужим IP. Сразу после применения под `netology-web` получил адрес `10.1.184.27`, а в endpoints висел `10.1.184.4:8080` — при том, что `targetRef` указывал на тот же под и тот же UID:

```console
$ kubectl get endpoints netology-svc -n hw12 -o yaml | grep -A4 subsets
subsets:
- addresses:
  - ip: 10.1.184.4
    nodeName: b550-tomahawk
    targetRef:
      kind: Pod
      name: netology-web
      uid: 3d517256-375e-417c-ac9f-b6f1c179f6f3

$ kubectl get pod netology-web -n hw12 -o jsonpath='{.metadata.uid} {.status.podIP}{"\n"}'
3d517256-375e-417c-ac9f-b6f1c179f6f3 10.1.184.27
```

IP в endpoints не обновился и через полминуты, то есть это не «подождать ещё немного», а застрявшее состояние после того самого пересоздания sandbox. Пересоздал под — endpoints подхватили актуальный адрес:

```console
$ kubectl delete pod netology-web -n hw12
pod "netology-web" deleted from hw12 namespace
$ kubectl apply -f manifests/netology-web-pod.yaml
pod/netology-web created

$ kubectl get pods -n hw12 -o wide
NAME           READY   STATUS    RESTARTS   AGE   IP           NODE            NOMINATED NODE   READINESS GATES
hello-world    1/1     Running   1          2m17s 10.1.184.33  b550-tomahawk   <none>           <none>
netology-web   1/1     Running   0          15s   10.1.184.1   b550-tomahawk   <none>           <none>

$ kubectl get endpoints netology-svc -n hw12
netology-svc   10.1.184.1:8080   2m17s
```

Теперь IP пода и endpoint совпадают. Если бы я этого не заметил, сервис отправлял бы трафик в никуда.

### Доступ к сервису через port-forward

```console
$ kubectl port-forward svc/netology-svc -n hw12 8081:80
Forwarding from 127.0.0.1:8081 -> 8080
Forwarding from [::1]:8081 -> 8080
Handling connection for 8081
```

Обратите внимание: локальный порт 8081, порт сервиса 80, а в строке `Forwarding` справа уже 8080 — `kubectl` сам развернул `port: 80` в `targetPort: 8080`. Ответ:

```console
$ curl -s localhost:8081

Hostname: netology-web

Pod Information:
	-no pod information available-

Server values:
	server_version=nginx: 1.12.2 - lua: 10010

Request Information:
	client_address=127.0.0.1
	method=GET
	real path=/
	query=
	request_version=1.1
	request_scheme=http
	request_uri=http://localhost:8080/

Request Headers:
	accept=*/*
	host=localhost:8081
	user-agent=curl/8.23.0

Request Body:
	-no body in request-
```

`Hostname: netology-web` подтверждает, что запрос дошёл именно до пода за сервисом. В `host` уже виден `localhost:8081`, а в `request_uri` — `8080`: echoserver строит URI из собственного `server_port`, на котором слушает, поэтому там его порт, а не мой локальный.

Первый запуск port-forward на сервис сразу после пересоздания пода сорвался: `error: lost connection to pod`, а curl вернул код 7 (connection refused). Под к этому моменту был `Running 1/1`, так что на «под не готов» это не похоже; скорее всего проброс поднялся раньше, чем endpoint успел обновиться после пересоздания. Со второй попытки всё прошло без ошибок. Для чистоты прогнал ещё раз и снял вывод выше — стабильно.

## Чем доступ к поду отличается от доступа к сервису

В обоих случаях `kubectl port-forward` не использует ClusterIP и kube-proxy: клиент открывает поток к API-серверу, тот проксирует его на kubelet узла, а kubelet заходит в network namespace нужного пода и подключается к `127.0.0.1:<порт>`. Поэтому в ответе echoserver в обоих случаях `client_address=127.0.0.1` — для приложения запрос пришёл с локального адреса.

Разница в том, что происходит до этого:

- `port-forward pod/hello-world` сразу знает, в какой под идти. Работает, даже если под не Ready и у него нет endpoint'ов; имя пода — единственный адрес.
- `port-forward svc/netology-svc` сначала идёт в API за объектом Service, по его селектору находит подходящие поды (фактически через endpoints/EndpointSlice), выбирает один из них и только потом пробрасывает порт до этого пода, попутно пересчитывая `port 80` в `targetPort 8080`. Если селектор ни с чем не совпал или endpoint'ы пустые/устаревшие, проброс либо не поднимется, либо отвалится с `lost connection to pod`.

То есть сервис добавляет слой абстракции: клиент не знает имя пода и его IP, а знает только стабильное DNS-имя и порт. Внутри кластера это работает через `netology-svc.hw12.svc.cluster.local:80` и kube-proxy, а `port-forward` — это обходной путь для отладки с ноутбука, который эмулирует доступ «как будто я внутри», но по своей реализации всё равно приходит в конкретный под.

Ещё одно практическое следствие: при нескольких репликах `port-forward svc/...` каждый раз может попасть в разный под, а `port-forward pod/...` всегда бьёт в указанный. Для отладки конкретного экземпляра нужен второй вариант, для проверки, что сервис вообще видит поды, — первый.

Схему из папки `diagrams/` (исходники PlantUML `pods and svc.puml` и `port-forward-pod.puml`) я не перерисовывал, она ровно про этот случай: сервис ссылается на endpoints, endpoints — на поды.

## Итог

- В namespace `hw12` работают два пода: `hello-world` (10.1.184.33) и `netology-web` (10.1.184.1), оба на образе `gcr.io/kubernetes-e2e-test-images/echoserver:2.2`, образ тянется с gcr.io без замены.
- `netology-svc` — ClusterIP 10.152.183.45, порт 80 → 8080, endpoint 10.1.184.1:8080, селектор `app=netology-web`.
- Оба варианта port-forward проверены curl'ом, ответы echoserver приложены, процессы проброса после проверки убиты.
- Попутно поймал две инфраструктурные мелочи: пересоздание sandbox у обоих подов при первом старте и застрявший IP в endpoints после этого. Второе лечится пересозданием пода, что и сделал.
