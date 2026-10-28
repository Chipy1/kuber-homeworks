# Домашнее задание 1.4 — сетевое взаимодействие в Kubernetes

Работал на живом кластере MicroK8s v1.35.6 (один узел `b550-tomahawk`, внешний адрес 192.168.50.225). Все ресурсы собрал в новом namespace `hw14`, чтобы не задеть чужие объекты. Манифесты лежат в [manifests/](manifests/), применял их через `kubectl apply -f`.

## Задание 1. Deployment из двух контейнеров и Service с двумя портами

Сначала namespace:

```console
$ kubectl apply -f manifests/namespace.yaml
namespace/hw14 created
```

Deployment — [manifests/deployment.yaml](manifests/deployment.yaml). В одном поде два контейнера, `nginx` и `wbitt/network-multitool`, по три реплики. Главный момент здесь — порты. Контейнеры пода делят один network namespace, поэтому занять один и тот же порт дважды нельзя. Multitool по умолчанию поднимает HTTP на 80 и HTTPS на 443, то есть ровно там же, где слушает nginx. При первом запуске без переменных окружения поды бы не встали: один из контейнеров падал бы с `bind: address already in use`. Поэтому multitool перевёл на 8080/8443:

```yaml
env:
  - name: HTTP_PORT
    value: "8080"
  - name: HTTPS_PORT
    value: "8443"
```

Сам Deployment:

```yaml
containers:
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
      - name: mt-http
        containerPort: 8080
      - name: mt-https
        containerPort: 8443
```

После применения дождался раскатки и проверил поды:

```console
$ kubectl -n hw14 rollout status deploy/netology-app
deployment "netology-app" successfully rolled out

$ kubectl -n hw14 get pods -o wide
NAME                            READY   STATUS    RESTARTS   AGE   IP            NODE
netology-app-54d5466769-7lhvz   2/2     Running   0          95s   10.1.184.21   b550-tomahawk
netology-app-54d5466769-8btpt   2/2     Running   0          95s   10.1.184.42   b550-tomahawk
netology-app-54d5466769-g59hx   2/2     Running   0          95s   10.1.184.39   b550-tomahawk
```

`READY 2/2` подтверждает, что оба контейнера живы и порты не конфликтуют.

Service — [manifests/service.yaml](manifests/service.yaml). Тип ClusterIP, один selector `app: netology-app`, два описания портов:

```yaml
ports:
  - name: nginx
    port: 9001
    targetPort: 80
  - name: multitool
    port: 9002
    targetPort: 8080
```

```console
$ kubectl -n hw14 get svc
NAME                      TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)             AGE
netology-nginx-nodeport   NodePort    10.152.183.155   <none>        80:30090/TCP        96s
netology-svc              ClusterIP   10.152.183.162   <none>        9001/TCP,9002/TCP   96s

$ kubectl -n hw14 get endpoints
NAME                      ENDPOINTS                                                      AGE
netology-nginx-nodeport   10.1.184.21:80,10.1.184.39:80,10.1.184.42:80                 96s
netology-svc              10.1.184.21:8080,10.1.184.39:8080,10.1.184.42:8080 + 3 more...   96s
```

Вывод `get endpoints` обрезает второй набор, поэтому посмотрел YAML:

```console
$ kubectl -n hw14 get endpoints netology-svc -o yaml
subsets:
- addresses:
  - ip: 10.1.184.21
  ...
  - ip: 10.1.184.39
  - ip: 10.1.184.42
  ports:
  - name: multitool
    port: 8080
    protocol: TCP
  - name: nginx
    port: 80
    protocol: TCP
```

Один и тот же набор адресов подов получает два порта: 80 и 8080. Для nginx-порта адреса фактически означают `podIP:80`, для multitool — `podIP:8080`.

### Проверка из отдельного пода

Создал временный под с multitool ([manifests/test-pod.yaml](manifests/test-pod.yaml)) и ходил из него curl'ом:

```console
$ kubectl -n hw14 exec multitool-test -- curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://netology-svc:9001/
HTTP 200
$ kubectl -n hw14 exec multitool-test -- curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://netology-svc:9002/
HTTP 200
$ kubectl -n hw14 exec multitool-test -- curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://netology-svc.hw14.svc.cluster.local:9001/
HTTP 200
$ kubectl -n hw14 exec multitool-test -- curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://netology-svc.hw14.svc.cluster.local:9002/
HTTP 200
```

Тела ответов разные, значит трафик действительно попадает в разные контейнеры:

```console
$ kubectl -n hw14 exec multitool-test -- curl -s http://netology-svc:9001/ | head -6
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
html { color-scheme: light dark; }

$ kubectl -n hw14 exec multitool-test -- curl -s http://netology-svc:9002/ | head -12
WBITT Network MultiTool (with NGINX) - netology-app-54d5466769-g59hx - 10.1.184.39 - HTTP: 8080 , HTTPS: 8443 . (Formerly praqma/network-multitool)
```

Короткое имя и FQDN резолвятся в один ClusterIP:

```console
$ kubectl -n hw14 exec multitool-test -- nslookup netology-svc.hw14.svc.cluster.local
Name:	netology-svc.hw14.svc.cluster.local
Address: 10.152.183.162
```

После проверки тестовый под удалил, чтобы не висел без дела: `kubectl -n hw14 delete pod multitool-test`.

### Почему один Service с двумя портами работает

Service — это не процесс и не proxy внутри пода, а набор правил в kube-proxy и запись в CoreDNS. У объекта есть общий selector, по которому контроллер endpoints выбирает поды. Дальше для каждого элемента `ports` строится отдельное правило DNAT: пакет на ClusterIP:9001 переписывается в `podIP:80`, пакет на ClusterIP:9002 — в `podIP:8080`. Порты независимы, у каждого своё имя (обязательно, когда портов больше одного), поэтому конфликта между ними нет. Оба порта ведут на один и тот же список подов, потому что selector один.

## Задание 2. Доступ снаружи через NodePort

Порты 80 и 443 на хосте заняты чужим Docker-сервисом `uh-nginx`, поэтому пробрасывать через hostPort или ставить свой ingress на эти порты было нельзя. Взял NodePort. Сначала проверил, что 30090 свободен: в кластере уже был только ingress-nginx с 30080/30443.

```console
$ kubectl get svc -A | grep NodePort
ingress-nginx   ingress-nginx-controller   NodePort   10.152.183.147   <none>   80:30080/TCP,443:30443/TCP   8m47s
```

Манифест — [manifests/service-nodeport.yaml](manifests/service-nodeport.yaml):

```yaml
spec:
  type: NodePort
  selector:
    app: netology-app
  ports:
    - name: http
      port: 80
      targetPort: 80
      nodePort: 30090
```

С хоста:

```console
$ curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://192.168.50.225:30090/
HTTP 200

$ curl -s http://192.168.50.225:30090/ | head -6
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
html { color-scheme: light dark; }
```

В браузере не проверял — curl уже вернул ту же стандартную страницу nginx, так что по этому адресу отдаётся она же.

### Почему NodePort ведёт только в nginx

Selector `app: netology-app` выбирает под целиком, а не отдельный контейнер: у Service нет понятия «контейнер», он оперирует IP пода. Внутри пода оба контейнера смотрят в один и тот же `podIP`. Когда kube-proxy обрабатывает `targetPort: 80`, он отправляет пакет на `podIP:80`, а на этом порту слушает только nginx. Multitool переехал на 8080, поэтому этот Service его не задевает. Если бы multitool остался на 80, то либо под не запустился бы из-за конфликта портов, либо (при другом раскладе) NodePort мог бы попасть в multitool вместо nginx — как раз поэтому порт и увели на 8080.

Сам NodePort открывается на всех узлах кластера, здесь узел один. Запрос на 192.168.50.225:30090 попадает в kube-proxy, тот по цепочке правил выбирает один из трёх подов и переписывает адрес на `podIP:80`.

## Итог

- Deployment `netology-app` из nginx и multitool, 3 реплики, оба контейнера в состоянии Running 2/2.
- `netology-svc` типа ClusterIP с портами 9001→80 и 9002→8080, endpoints содержат по три адреса на каждый порт.
- Из отдельного пода curl возвращает 200 по короткому имени и по FQDN на оба порта, тела ответов относятся к nginx и multitool соответственно.
- `netology-nginx-nodeport` с nodePort 30090 отдаёт страницу nginx с хоста по адресу 192.168.50.225.
- Тестовый под удалён, рабочие ресурсы остались в namespace `hw14`.
