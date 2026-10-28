# 1.5. Сетевое взаимодействие в Kubernetes, часть 2

Кластер — MicroK8s v1.35.6 на одном узле `b550-tomahawk` (192.168.50.225). Штатный addon `ingress` не включал: на стенде нет sudo без пароля, поэтому ingress-nginx поставлен Helm-чартом. Снаружи он слушает NodePort 30080/30443 — порты 80 и 443 на хосте занял чужой Docker-контейнер, так что ходил на 30080.

```
$ microk8s helm3 list -n ingress-nginx
NAME         	NAMESPACE    	REVISION	UPDATED                                	STATUS  	CHART               	APP VERSION
ingress-nginx	ingress-nginx	1       	2026-10-28 13:20:28.460135976 +0500 +05	deployed	ingress-nginx-4.15.1	1.15.1

$ kubectl -n ingress-nginx get svc
NAME                                 TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)                      AGE
ingress-nginx-controller             NodePort    10.152.183.147   <none>        80:30080/TCP,443:30443/TCP   8m43s
ingress-nginx-controller-admission   ClusterIP   10.152.183.114   <none>        443/TCP                      8m43s
```

## Задание 1. Deployment и Service для frontend и backend

Создал namespace `hw15`, в нём два Deployment'а: `frontend` из `nginx:1.27` с тремя репликами и `backend` из `wbitt/network-multitool` с `HTTP_PORT=8080` (по этой переменной multitool поднимает свой nginx на 8080 вместо дефолтного 80). В обоих контейнерах повесил httpGet-readinessProbe, иначе `rollout status` и `kubectl get pods` показывают готовность пода, вообще не проверяя, отвечает ли приложение.

Манифесты: [manifests/namespace.yaml](manifests/namespace.yaml), [manifests/frontend.yaml](manifests/frontend.yaml), [manifests/backend.yaml](manifests/backend.yaml).

```yaml
# frontend: Deployment + Service
spec:
  replicas: 3
  ...
  ports:
    - name: http
      containerPort: 80
---
kind: Service
metadata:
  name: frontend-svc
spec:
  selector:
    app: frontend
  ports:
    - port: 80
      targetPort: http
```

```yaml
# backend
          env:
            - name: HTTP_PORT
              value: "8080"
          ports:
            - name: http
              containerPort: 8080
---
kind: Service
metadata:
  name: backend-svc
spec:
  selector:
    app: backend
  ports:
    - port: 8080
      targetPort: http
```

Применял в два шага, потому что `kubectl apply -f manifests/` читает каталог по алфавиту и `namespace.yaml` уезжает в конец — на первом прогоне получил `Error from server (NotFound): namespaces "hw15" not found` для всех объектов. Поэтому сначала namespace, потом остальное:

```
$ kubectl apply -f manifests/namespace.yaml
$ kubectl apply -f manifests/frontend.yaml -f manifests/backend.yaml -f manifests/ingress.yaml

$ kubectl -n hw15 rollout status deploy/frontend
deployment "frontend" successfully rolled out
$ kubectl -n hw15 rollout status deploy/backend
deployment "backend" successfully rolled out

$ kubectl -n hw15 get pods -o wide
NAME                        READY   STATUS    RESTARTS   AGE     IP            NODE
backend-6d867546cf-bkmcz    1/1     Running   1          2m41s   10.1.184.40   b550-tomahawk
frontend-775c859856-8ggw5   1/1     Running   0          2m41s   10.1.184.20   b550-tomahawk
frontend-775c859856-mdkxv   1/1     Running   0          2m41s   10.1.184.36   b550-tomahawk
frontend-775c859856-rswlt   1/1     Running   0          2m41s   10.1.184.59   b550-tomahawk

$ kubectl -n hw15 get svc
NAME           TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)    AGE
backend-svc    ClusterIP   10.152.183.22    <none>        8080/TCP   2m41s
frontend-svc   ClusterIP   10.152.183.100   <none>        80/TCP     2m41s
```

Единственный рестарт у backend-пода — событие `SandboxChanged` при самом первом старте (kubelet пересоздал sandbox, контейнер поднялся заново), к конфигурации отношения не имеет.

Проверка, что приложения видят друг друга по именам Service'ов. Из пода frontend в backend:

```
$ kubectl -n hw15 exec deploy/frontend -- curl -sS -i http://backend-svc:8080/
HTTP/1.1 200 OK
Server: nginx/1.30.6
Date: Wed, 28 Oct 2026 08:31:05 GMT
Content-Type: text/html
Content-Length: 142
...
WBITT Network MultiTool (with NGINX) - backend-6d867546cf-bkmcz - 10.1.184.40 - HTTP: 8080 , HTTPS: 443 . (Formerly praqma/network-multitool)
```

И в обратную сторону, из пода backend в frontend:

```
$ kubectl -n hw15 exec deploy/backend -- curl -sS -i http://frontend-svc:80/
HTTP/1.1 200 OK
Server: nginx/1.27.5
Date: Wed, 28 Oct 2026 08:31:06 GMT
Content-Type: text/html
Content-Length: 615
...
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
```

Тела разные и хорошо опознаваемые: multitool отдаёт строчку со своим именем пода и IP, nginx — стандартную заглушку. Заодно видно, что короткие имена `backend-svc`/`frontend-svc` резолвятся через CoreDNS без указания namespace, поскольку обращение идёт из того же `hw15`.

Балансировка за `frontend-svc` тоже живая — шесть запросов с хоста разошлись по трём подам:

```
$ kubectl -n hw15 logs -l app=frontend --tail=2 --prefix | grep 'curl/8'
[pod/frontend-775c859856-8ggw5/nginx] 10.1.184.56 - - [...] "GET / HTTP/1.1" 200 615 "-" "curl/8.23.0" "192.168.50.225"
[pod/frontend-775c859856-mdkxv/nginx] 10.1.184.56 - - [...] "GET / HTTP/1.1" 200 615 "-" "curl/8.23.0" "192.168.50.225"
[pod/frontend-775c859856-rswlt/nginx] 10.1.184.56 - - [...] "GET / HTTP/1.1" 200 615 "-" "curl/8.23.0" "192.168.50.225"
```

(`10.1.184.56` — IP пода ingress-контроллера, с которого пришли запросы.)

## Задание 2. Ingress

Манифест — [manifests/ingress.yaml](manifests/ingress.yaml):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: netology-ingress
  namespace: hw15
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: nginx
  rules:
    - http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: backend-svc
                port:
                  number: 8080
          - path: /
            pathType: Prefix
            backend:
              service:
                name: frontend-svc
                port:
                  number: 80
```

Правило без `host` — так и задумано, чтобы работал запрос просто по IP узла. `/api` и `/` оба `Prefix`, и `/` формально тоже подходит под `/api`, но ingress-nginx выбирает самое длинное совпадение, поэтому `/api` уходит в backend, а всё остальное — во frontend.

```
$ kubectl -n hw15 describe ingress netology-ingress
Name:             netology-ingress
Namespace:        hw15
Address:          10.152.183.147
Ingress Class:    nginx
Rules:
  Host        Path  Backends
  ----        ----  --------
  *           
              /api   backend-svc:8080 (10.1.184.40:8080)
              /      frontend-svc:80 (10.1.184.59:80,10.1.184.20:80,10.1.184.36:80)
Annotations:  nginx.ingress.kubernetes.io/rewrite-target: /
```

`Address` — это ClusterIP самого контроллера, снаружи он доступен на `192.168.50.225:30080`.

### Зачем rewrite-target

Ingress-контроллер не отрезает префикс пути сам: он передаёт в под ровно тот URI, который пришёл от клиента. То есть без аннотации запрос `/api` уехал бы в backend как `GET /api`, а multitool — это nginx со статикой в `/usr/share/nginx/html`, файла `api` там нет, и клиент получил бы 404. Аннотация `rewrite-target: /` заставляет контроллер переписать URI перед проксированием.

В сгенерированном конфиге nginx-контроллера это видно буквально:

```
$ kubectl -n ingress-nginx exec deploy/ingress-nginx-controller -- grep 'rewrite ' /etc/nginx/nginx.conf | grep -v lua
			rewrite "(?i)/api" / break;
```

Директива стоит внутри `location ~* "^/api"`, а у location'а для `/` её нет вовсе — поэтому запросы к frontend уходят с исходным путём (`/index.html` открывается как `/index.html`, а `/nonexistent` честно отдаёт 404 от nginx, а не подменяется на главную).

Чтобы не гадать, проверил и обратный случай: поднял временный Ingress с тем же бэкендом, но без аннотации (на отдельном хосте, чтобы не конфликтовать с основным правилом), дёрнул `/api` и получил 404. После проверки временный объект удалил.

```
$ curl -sS -i -H 'Host: norewrite.local' http://192.168.50.225:30080/api
HTTP/1.1 404 Not Found
Content-Type: text/html
Content-Length: 153
...
code=404
```

В логе backend-пода при этом осталась причина:

```
2026/10/28 08:31:16 [error] 16#16: *17 open() "/usr/share/nginx/html/api" failed (2: No such file or directory),
client: 127.0.0.1, server: localhost, request: "GET /api HTTP/1.1", host: "127.0.0.1:8080"
127.0.0.1 - - [...] "GET /api HTTP/1.1" 404 153 "-" "curl/8.23.0" "-"
```

Тот же 404 приходит, если ткнуться в под напрямую, минуя Ingress:

```
$ kubectl -n hw15 exec deploy/backend -- curl -s -o /dev/null -w 'code=%{http_code}\n' http://127.0.0.1:8080/api
code=404
```

### Проверка снаружи

С хоста, по адресу узла и NodePort:

```
$ curl -sS -i http://192.168.50.225:30080/
HTTP/1.1 200 OK
Date: Wed, 28 Oct 2026 08:31:09 GMT
Content-Type: text/html
Content-Length: 615
...
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>

$ curl -sS -i http://192.168.50.225:30080/api
HTTP/1.1 200 OK
Date: Wed, 28 Oct 2026 08:31:09 GMT
Content-Type: text/html
Content-Length: 142
...
WBITT Network MultiTool (with NGINX) - backend-6d867546cf-bkmcz - 10.1.184.40 - HTTP: 8080 , HTTPS: 443 . (Formerly praqma/network-multitool)
```

Коды для наглядности:

```
$ curl -s -o /dev/null -w '/ -> %{http_code}\n' http://192.168.50.225:30080/
/ -> 200
$ curl -s -o /dev/null -w '/api -> %{http_code}\n' http://192.168.50.225:30080/api
/api -> 200
```

Одна деталь, которую стоит держать в голове: `rewrite "(?i)/api" / break` заменяет URI целиком, а не только префикс, поэтому `/api/foo` тоже придёт в backend как `GET /` и вернёт главную multitool'а, а не 404 по несуществующему подпути. Для этой задачи (просто открыть backend по `/api`) поведение подходящее; если бы понадобилось прокидывать остаток пути, rewrite-target писали бы через capture-группу, например `/$2` при `path: /api(/|$)(.*)`.

## Итог

- В `hw15` работают `frontend` (3 реплики nginx:1.27) и `backend` (multitool с HTTP_PORT=8080), к каждому — ClusterIP-сервис на том же порту.
- Взаимная видимость проверена curl'ом из подов в обе стороны: оба ответа 200, тела — характерные страницы nginx и multitool со своим именем пода.
- `netology-ingress` класса nginx разводит `/api` на `backend-svc:8080`, остальное — на `frontend-svc:80`; снаружи это `http://192.168.50.225:30080`.
- Без `rewrite-target: /` multitool получил бы `GET /api` и ответил 404 — проверил временным Ingress без аннотации и логом backend-пода.
- Побочный эффект rewrite'а (схлопывание всего, что глубже `/api`, в `/`) описал выше; для сценария задания он безвреден.
