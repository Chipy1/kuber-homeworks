# Домашнее задание 2.5 — Helm

Стенд: MicroK8s v1.35.6, один узел `b550-tomahawk` (192.168.50.225), containerd 2.1.6.
Helm поставился вместе с микрокластером, отдельного бинарника в системе нет, поэтому везде
`microk8s helm3` (v3.19.2). Все команды `kubectl` ниже выполнялись в контексте `microk8s`
(`kubectl config use-context microk8s`), на машине есть и другие кластеры.

```
$ microk8s helm3 version
version.BuildInfo{Version:"v3.19.2", GitCommit:"8766e718a0119851f10ddbe4577593a45fadf544", GitTreeState:"clean", GoVersion:"go1.24.13"}
$ kubectl get nodes
NAME            STATUS   ROLES    AGE   VERSION
b550-tomahawk   Ready    <none>   21d   v1.35.6
```

## Задание 1. Чарт webapp

Собрал чарт из двух компонентов: nginx как frontend и multitool как backend. Каждый
компонент получил свой Deployment и Service, никаких общих ресурсов между ними нет.

```
2.5/
├── webapp/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│       ├── _helpers.tpl
│       ├── configmap-frontend.yaml
│       ├── frontend-deployment.yaml
│       ├── frontend-service.yaml
│       ├── backend-deployment.yaml
│       └── backend-service.yaml
├── values-web-v1.yaml
├── values-web-v2.yaml
└── values-web-v3.yaml
```

`Chart.yaml` минимальный, `type: application`, версия чарта 0.1.0:

```yaml
apiVersion: v2
name: webapp
description: Двухкомпонентное приложение (nginx frontend + multitool backend) для домашки по Helm
type: application
version: 0.1.0
appVersion: "1.0"
```

`values.yaml` повторяет структуру приложения: два блока, у каждого образ, число реплик,
порты Service и ресурсы. Ничего из этого в шаблонах не зашито:

```yaml
frontend:
  replicaCount: 1
  image:
    repository: nginx
    tag: "1.19"
    pullPolicy: IfNotPresent
  service:
    type: ClusterIP
    port: 80
  resources: {}

backend:
  replicaCount: 1
  image:
    repository: wbitt/network-multitool
    tag: "latest"
    pullPolicy: IfNotPresent
  service:
    type: ClusterIP
    port: 8080
  resources: {}
```

Основная логика имён в `_helpers.tpl`. `webapp.fullname` склеивает имя релиза и имя
чарта, причём если имя чарта уже входит в имя релиза, второй раз его не добавляет:

```
webapp.fullname = printf "%s-%s" .Release.Name $name
```

Селекторы собираются отдельным шаблоном, которому через `dict` передаётся компонент:

```
{{- define "webapp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "webapp.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}
```

Вызывается как `include "webapp.selectorLabels" (dict "root" . "component" "frontend")`.
Так метки пода и `matchLabels` Deployment не расходятся: инстанс релиза отличает поды
web-v1 от web-v2, а component — frontend от backend.

Проверка чарта:

```
$ microk8s helm3 lint webapp/
==> Linting webapp/
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```

С `-f values-web-v2.yaml` результат такой же, замечание про иконку остаётся единственным.

Рендер (`helm template`) для web-v1, фрагмент:

```
$ microk8s helm3 template web-v1 webapp/ -n app1 -f values-web-v1.yaml
---
# Source: webapp/templates/frontend-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: web-v1-webapp-frontend
  labels:
    ...
    app.kubernetes.io/instance: web-v1
    app.kubernetes.io/component: frontend
spec:
  type: ClusterIP
  ports:
    - name: http
      port: 80
      targetPort: http
  selector:
    app.kubernetes.io/name: webapp
    app.kubernetes.io/instance: web-v1
    app.kubernetes.io/component: frontend
---
...
# Source: webapp/templates/frontend-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-v1-webapp-frontend
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: webapp
      app.kubernetes.io/instance: web-v1
      app.kubernetes.io/component: frontend
  template:
    metadata:
      annotations:
        checksum/index: 26950c1e8dbb59f847bbd510d39d590fcd6bbf52382f2bafc7c6eaedc5eda269
    spec:
      containers:
        - name: frontend
          image: "nginx:1.20"
          ...
```

`checksum/index` — хеш ConfigMap с `index.html`, подставленный в аннотацию пода. Меняешь
страницу — меняется аннотация — Deployment сам перекатывает поды. Без этого ConfigMap
обновился бы, а nginx продолжил отдавать старый файл из уже запущенного контейнера.

Сам `index.html` сделан шаблоном: в него подставляются имя релиза, namespace, тег образа и
адрес backend-сервиса. Это удобно для проверки — по ответу сразу видно, какой релиз ответил.

Отдельно про backend: у `wbitt/network-multitool` без переменной `HTTP_PORT` nginx
слушает только 80, порт 8080 внутри контейнера закрыт (проверил `curl` изнутри пода —
`8080=000`). Можно было выставить `HTTP_PORT=8080`, но я оставил дефолт и опубликовал
его через Service как 8080→80 — так в чарте видно, что порт сервиса настраивается
отдельно от порта контейнера. Поэтому `containerPort` у backend — 80, а Service
публикует 8080 и ведёт на именованный порт `http`. Значение `backend.service.port` при этом остаётся настраиваемым через values.

## Задание 2. Три релиза в двух namespace

Namespace создал руками, чтобы не смешивать это с установкой релиза:

```
$ kubectl create ns app1
namespace/app1 created
$ kubectl create ns app2
namespace/app2 created
```

Дальше три установки одного чарта, версия задаётся только values-файлом:

```
$ microk8s helm3 install web-v1 webapp/ -n app1 -f values-web-v1.yaml
$ microk8s helm3 install web-v2 webapp/ -n app1 -f values-web-v2.yaml
$ microk8s helm3 install web-v3 webapp/ -n app2 -f values-web-v3.yaml
```

```
$ microk8s helm3 list -A
NAME                	NAMESPACE           	REVISION	UPDATED                                	STATUS  	CHART                      	APP VERSION
ingress-nginx       	ingress-nginx       	1       	2026-10-28 13:20:28.460135976 +0500 +05	deployed	ingress-nginx-4.15.1       	1.15.1
kubernetes-dashboard	kubernetes-dashboard	1       	2026-10-27 22:11:34.499310369 +0500 +05	deployed	kubernetes-dashboard-7.14.0
web-v1              	app1                	2       	2026-10-28 13:31:50.653453827 +0500 +05	deployed	webapp-0.1.0               	1.0
web-v2              	app1                	1       	2026-10-28 13:30:35.229430428 +0500 +05	deployed	webapp-0.1.0               	1.0
web-v3              	app2                	1       	2026-10-28 13:30:35.481419164 +0500 +05	deployed	webapp-0.1.0               	1.0
```

У web-v1 ревизия 2 — об этом ниже, сначала поставил её на 1.19 и потом поднял до 1.20.

Ресурсы в app1:

```
$ kubectl get deploy,svc,pods -n app1 -o wide
NAME                                     READY   UP-TO-DATE   AVAILABLE   AGE   IMAGES
deployment.apps/web-v1-webapp-backend    1/1     1            1           2m55s wbitt/network-multitool:latest
deployment.apps/web-v1-webapp-frontend   2/2     2            2           2m55s nginx:1.20
deployment.apps/web-v2-webapp-backend    1/1     1            1           2m55s wbitt/network-multitool:latest
deployment.apps/web-v2-webapp-frontend   1/1     1            1           2m55s nginx:1.26

NAME                             TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)    AGE
service/web-v1-webapp-backend    ClusterIP   10.152.183.136   <none>        8080/TCP   2m55s
service/web-v1-webapp-frontend   ClusterIP   10.152.183.160   <none>        80/TCP     2m55s
service/web-v2-webapp-backend    ClusterIP   10.152.183.28    <none>        8080/TCP   2m55s
service/web-v2-webapp-frontend   ClusterIP   10.152.183.229   <none>        80/TCP     2m55s

NAME                                          READY   STATUS    RESTARTS   AGE    IP
pod/web-v1-webapp-backend-584d69cf74-b2tvg    1/1     Running   0          2m25s  10.1.184.48
pod/web-v1-webapp-frontend-779696dd94-sckn2   1/1     Running   0          100s   10.1.184.19
pod/web-v1-webapp-frontend-779696dd94-wqr95   1/1     Running   0          32s    10.1.184.43
pod/web-v2-webapp-backend-f669df59-txvnm      1/1     Running   0          2m25s  10.1.184.8
pod/web-v2-webapp-frontend-64fd7dfdf7-m6kjn   1/1     Running   0          2m25s  10.1.184.13
```

В app2 то же самое для третьего релиза:

```
$ kubectl get deploy,svc,pods -n app2 -o wide
NAME                                     READY   UP-TO-DATE   AVAILABLE   AGE   IMAGES
deployment.apps/web-v3-webapp-backend    1/1     1            1           2m55s wbitt/network-multitool:latest
deployment.apps/web-v3-webapp-frontend   1/1     1            1           2m55s nginx:1.27

NAME                             TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)    AGE
service/web-v3-webapp-backend    ClusterIP   10.152.183.170   <none>        8080/TCP   2m55s
service/web-v3-webapp-frontend   ClusterIP   10.152.183.82    <none>        80/TCP     2m55s

NAME                                          READY   STATUS    RESTARTS   AGE    IP
pod/web-v3-webapp-backend-785bb8f948-hljdt    1/1     Running   0          2m25s  10.1.184.9
pod/web-v3-webapp-frontend-64bbb94856-f82dl   1/1     Running   0          2m25s  10.1.184.10
```

Frontend у web-v1 в двух экземплярах — так в `values-web-v1.yaml` стоит `replicaCount: 2`,
чтобы было видно, что число реплик тоже приходит из values.

Что в подах действительно разные версии nginx — видно и по образу, и изнутри контейнера:

```
$ kubectl get pods -n app1 -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
web-v1-webapp-backend-584d69cf74-b2tvg	wbitt/network-multitool:latest
web-v1-webapp-frontend-779696dd94-sckn2	nginx:1.20
web-v1-webapp-frontend-779696dd94-wqr95	nginx:1.20
web-v2-webapp-backend-f669df59-txvnm	wbitt/network-multitool:latest
web-v2-webapp-frontend-64fd7dfdf7-m6kjn	nginx:1.26

$ kubectl exec -n app1 deploy/web-v1-webapp-frontend -- nginx -v
nginx version: nginx/1.20.2
$ kubectl exec -n app1 deploy/web-v2-webapp-frontend -- nginx -v
nginx version: nginx/1.26.3
$ kubectl exec -n app2 deploy/web-v3-webapp-frontend -- nginx -v
nginx version: nginx/1.27.5
```

Теги в values округлены до минорной ветки (`1.20`, `1.26`, `1.27`), а внутри образов
стоят конкретные патчи — отсюда расхождение между `1.20` и `1.20.2`.

### Обновление релиза

Отдельно прогнал upgrade, чтобы проверить, что смена тега в values действительно
перекатывает приложение. web-v1 сначала стояла на `1.19`, потом поднял её до `1.20`:

```
$ microk8s helm3 upgrade web-v1 webapp/ -n app1 -f values-web-v1.yaml
NAME: web-v1
LAST DEPLOYED: Wed Oct 28 13:31:50 2026
NAMESPACE: app1
STATUS: deployed
REVISION: 2

$ kubectl rollout status deploy/web-v1-webapp-frontend -n app1
Waiting for deployment "web-v1-webapp-frontend" rollout to finish: 1 out of 2 new replicas have been updated...
Waiting for deployment "web-v1-webapp-frontend" rollout to finish: 1 old replicas are pending termination...
deployment "web-v1-webapp-frontend" successfully rolled out

$ kubectl get pods -n app1 -l app.kubernetes.io/instance=web-v1
NAME                                      READY   STATUS        RESTARTS   AGE
web-v1-webapp-backend-584d69cf74-b2tvg    1/1     Running       0          2m
web-v1-webapp-frontend-779696dd94-sckn2   1/1     Running       0          75s
web-v1-webapp-frontend-779696dd94-wqr95   1/1     Running       0          7s
web-v1-webapp-frontend-7d459dd45c-wd8xc   1/1     Terminating   0          2m
```

Старый под `7d459dd45c` (nginx 1.19) в этот момент ещё добивался, новый ReplicaSet
`779696dd94` уже поднялся. Helm тут ничего не делает поверх Kubernetes — он просто
пересчитал манифесты с новым тегом и отправил их в API, остальное сделал Deployment.

История релиза и то, какие values реально применены:

```
$ microk8s helm3 history web-v1 -n app1
REVISION	UPDATED                 	STATUS    	CHART       	APP VERSION	DESCRIPTION
1       	Wed Oct 28 13:30:34 2026	superseded	webapp-0.1.0	1.0        	Install complete
2       	Wed Oct 28 13:31:50 2026	deployed  	webapp-0.1.0	1.0        	Upgrade complete

$ microk8s helm3 get values web-v1 -n app1
USER-SUPPLIED VALUES:
backend:
  replicaCount: 1
frontend:
  image:
    repository: nginx
    tag: "1.20"
  replicaCount: 2
```

### Проверка доступа

Порты 80/443 на хосте заняты чужим docker-контейнером `uh-nginx`, поэтому пробросил
сервисы на 18081–18083 и сходил туда curl'ом. Port-forward жил только на время проверки,
потом процессы убил.

```
$ kubectl port-forward -n app1 svc/web-v1-webapp-frontend 18081:80 &
$ kubectl port-forward -n app1 svc/web-v2-webapp-frontend 18082:80 &
$ kubectl port-forward -n app2 svc/web-v3-webapp-frontend 18083:80 &
$ curl -s -w "\nHTTP %{http_code}\n" http://127.0.0.1:18081/
<html>
  <head><title>web-v1</title></head>
  <body>
    <h1>release: web-v1</h1>
    <h2>namespace: app1</h2>
    <h2>frontend image: nginx:1.20</h2>
    <h2>backend service: web-v1-webapp-backend:8080</h2>
  </body>
</html>

HTTP 200
```

То же для двух остальных, ответы отличаются только релизом и тегом:

```
HTTP 200   # 18082: release: web-v2, namespace: app1, frontend image: nginx:1.26
HTTP 200   # 18083: release: web-v3, namespace: app2, frontend image: nginx:1.27
```

Кроме проброса с хоста, проверил сервисы изнутри кластера — из backend-пода, где есть
curl:

```
$ kubectl exec -n app1 deploy/web-v1-webapp-backend -- sh -c \
    'curl -s -o /dev/null -w "web-v1-webapp-frontend:80 -> %{http_code}\n" http://web-v1-webapp-frontend:80/; \
     curl -s -o /dev/null -w "web-v2-webapp-frontend:80 -> %{http_code}\n" http://web-v2-webapp-frontend:80/'
web-v1-webapp-frontend:80 -> 200
web-v2-webapp-frontend:80 -> 200

$ kubectl exec -n app2 deploy/web-v3-webapp-backend -- sh -c \
    'curl -s -o /dev/null -w "web-v3-webapp-backend:8080 -> %{http_code}\n" http://web-v3-webapp-backend:8080/'
web-v3-webapp-backend:8080 -> 200
```

Backend тоже отвечает 200 на своём 8080 — значит Service с портом 8080 и targetPort 80
собран правильно, а не просто «подRunning».

## Зачем values.yaml, чем релиз отличается от namespace

values.yaml — это входные параметры шаблона, то есть единственное место, где задаются
отличия между окружениями. Сами шаблоны описывают структуру (какие объекты и как
связаны), а values подставляют в них образ, тег, реплики, порты, ресурсы. Поэтому три
версии приложения — это один чарт и три файла значений, а не три копии манифестов.
Плюс values видны в самом релизе: `helm get values` показывает, что именно передали, а
`helm get manifest` — во что это отрендерилось. Когда параметр нужно поменять на ходу,
достаточно `--set frontend.image.tag=1.21` или правки файла и `helm upgrade`; шаблоны при
этом не трогаются, история ревизий сохраняется, откат делается `helm rollback`.

Релиз — это установленный экземпляр чарта: имя (`web-v1`), namespace, конкретный набор
values и история ревизий. Один и тот же чарт можно поставить сколько угодно раз, каждый
раз с новым именем релиза. Namespace — совсем другая сущность: область имён для
объектов, а также граница для RBAC, квот и сетевых политик. Релиз живёт внутри
namespace, но namespace ничего не знает про чарты и ревизии; удаление namespace уносит
все объекты всех релизов в нём, а `helm uninstall` удаляет только объекты одного релиза.

Два релиза в одном namespace (web-v1 и web-v2 в app1) допустимы, потому что имена всех
объектов содержат имя релиза:

```
service/web-v1-webapp-frontend   10.152.183.160   80/TCP
service/web-v2-webapp-frontend   10.152.183.229   80/TCP
```

Если бы в шаблонах стояло жёстко `name: webapp-frontend`, вторая установка упала бы на
`Service "webapp-frontend" already exists` — Kubernetes не разрешает два объекта с
одинаковыми именем и типом в одном namespace. Кроме имени, релизы разделены меткой
`app.kubernetes.io/instance`, и селекторы Service/Deployment собираются с ней, поэтому
трафик web-v1 не может утечь в поды web-v2 даже при одинаковых портах и образе. Именно
поэтому в чарте имя строится из `.Release.Name`, а не из константы.

## Итог

- Чарт `webapp` собирает два Deployment и два Service, все параметры вынесены в values, `helm lint` проходит без ошибок.
- В app1 работают web-v1 (nginx 1.20, 2 реплики frontend) и web-v2 (nginx 1.26), в app2 — web-v3 (nginx 1.27); имена Service не пересекаются за счёт префикса релиза.
- web-v1 пережила `helm upgrade` с 1.19 на 1.20, ревизия 2, старый ReplicaSet заменён новым.
- Все три фронтенда отвечают HTTP 200 через port-forward на 18081–18083, backend-сервисы отвечают 200 на 8080 изнутри кластера.
- Ресурсы в app1 и app2 оставлены работать, временные port-forward и проверочный под удалены.
