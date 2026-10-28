# Домашнее задание 2.3. Конфигурация приложений

Кластер MicroK8s v1.35.6, один узел `b550-tomahawk` (192.168.50.225), containerd 2.1.6.
Все ресурсы задания живут в namespace `hw23`. Ingress-контроллер `ingress-nginx` снаружи
доступен на NodePort 30080/30443, поэтому 80 и 443 на хосте не задействованы (их занимает
чужой Docker-сервис).

Манифесты лежат в [manifests/](manifests/), применялись по порядку номеров в имени файла.

## Задание 1. Nginx + multitool и конфликт портов

### Что именно конфликтует

Сначала собрал Deployment из двух контейнеров без всякой конфигурации и посмотрел, что будет.
Временный деплоймент `web-conflict-test` (потом удалил, в манифестах его нет):

```
$ kubectl -n hw23 get pods -l app=web-conflict-test
NAME                                READY   STATUS   RESTARTS      AGE
web-conflict-test-f877b984c-tknzp   1/2     Error    1 (11s ago)   17s

$ kubectl -n hw23 logs -l app=web-conflict-test -c multitool --tail=6
WBITT Network MultiTool (with NGINX) - web-conflict-test-f877b984c-tknzp - 10.1.184.4 - HTTP: 80 , HTTPS: 443 .
2026/10/28 08:31:13 [emerg] 1#1: bind() to 0.0.0.0:80 failed (98: Address in use)
nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address in use)
2026/10/28 08:31:13 [emerg] 1#1: still could not bind()
nginx: [emerg] still could not bind()
```

Контейнеры в поде делят одну сетевую namespace, и оба nginx-процесса пытаются сесть на
`0.0.0.0:80`. Порт уже занят nginx из первого контейнера, второй падает с `Address in use`
и уходит в `CrashLoopBackOff` — в статусе пода видно `1/2`.

### Решение через ConfigMap

Multitool умеет читать `HTTP_PORT` и `HTTPS_PORT` из окружения и правит под них свой
конфиг nginx. Поэтому завёл ConfigMap и подключил его целиком через `envFrom`
([01-multitool-config.yaml](manifests/01-multitool-config.yaml)):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: multitool-config
  namespace: hw23
data:
  HTTP_PORT: "8080"
  HTTPS_PORT: "8443"
```

В [03-web-deployment.yaml](manifests/03-web-deployment.yaml) у контейнера multitool:

```yaml
          envFrom:
            - configMapRef:
                name: multitool-config
```

Порты в `containerPort` тоже поправил на 8080/8443, чтобы описание пода не врало.

### Проверка

```
$ kubectl -n hw23 get pods -o wide
NAME                   READY   STATUS    RESTARTS   AGE   IP            NODE
web-66849698c8-v7fwn   2/2     Running   0          72s   10.1.184.37   b550-tomahawk
```

Оба контейнера поднялись с нулевым числом рестартов. Лог multitool подтверждает, что
переменные доехали:

```
WBITT Network MultiTool (with NGINX) - web-66849698c8-v7fwn - 10.1.184.37 - HTTP: 8080 , HTTPS: 8443 .
Replacing default HTTP port (80) with the value specified by the user - (HTTP_PORT: 8080).
Replacing default HTTPS port (443) with the value specified by the user - (HTTPS_PORT: 8443).
```

`describe` показывает источник переменных и что состояние Running у обоих:

```
Containers:
  nginx:
    Image:          nginx
    State:          Running
      Started:      Wed, 28 Oct 2026 13:30:08 +0500
    Ready:          True
  multitool:
    Image:          wbitt/network-multitool
    State:          Running
      Started:      Wed, 28 Oct 2026 13:30:09 +0500
    Ready:          True
    Environment Variables from:
      multitool-config  ConfigMap  Optional: false
```

### Своя страница из ConfigMap

Страница лежит в [02-web-content.yaml](manifests/02-web-content.yaml) ключом `index.html`:
заголовок «Netology. Kubernetes homework 2.3», немного CSS (тёмный фон, карточка).

Монтирование сделал через `subPath`, иначе ConfigMap-том затёр бы весь каталог
`/usr/share/nginx/html` и вместе с ним `50x.html`:

```yaml
          volumeMounts:
            - name: web-content
              mountPath: /usr/share/nginx/html/index.html
              subPath: index.html
              readOnly: true
```

Проверил, что каталог на месте:

```
$ kubectl -n hw23 exec web-66849698c8-v7fwn -c nginx -- ls -l /usr/share/nginx/html
-rw-r--r-- 1 root root  497 Oct 21 14:24 50x.html
-rw-r--r-- 1 root root 1007 Oct 28 08:29 index.html
```

### Service и curl

[04-web-svc.yaml](manifests/04-web-svc.yaml) — обычный ClusterIP, порт 80 → 80. Пробросил
локально на 18080 (80 на хосте занят чужим сервисом):

```
$ kubectl -n hw23 port-forward svc/web-svc 18080:80
Forwarding from 127.0.0.1:18080 -> 80

$ curl -sS -D- http://127.0.0.1:18080/ | head -12
HTTP/1.1 200 OK
Server: nginx/1.31.8
Content-Type: text/html
Content-Length: 1007

<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Netology. Kubernetes homework 2.3</title>
```

Дополнительно дернул nginx из соседнего контейнера пода — ответ тот же, Service тут ни при чём:

```
$ kubectl -n hw23 exec web-66849698c8-v7fwn -c multitool -- curl -sS -o /dev/null -w '%{http_code}\n' http://localhost/
200
```

Сам multitool на 8080 тоже отвечает 200. port-forward после проверки убил.

## Задание 2. HTTPS с самоподписным сертификатом

### Сертификат и Secret

Ключ и сертификат генерирую в `/tmp`, в репозиторий они не попадают. Команда и создание
секрета собраны в [09-tls-secret.sh](manifests/09-tls-secret.sh):

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /tmp/hw23-tls.key -out /tmp/hw23-tls.crt \
  -subj "/CN=hw23.local/O=Netology" \
  -addext "subjectAltName=DNS:hw23.local"

kubectl -n hw23 create secret tls hw23-tls \
  --cert=/tmp/hw23-tls.crt --key=/tmp/hw23-tls.key \
  --dry-run=client -o yaml | kubectl apply -f -

rm -f /tmp/hw23-tls.key /tmp/hw23-tls.crt
```

`--dry-run=client | kubectl apply` вместо голого `create` — чтобы скрипт можно было
перезапускать. Сам секрет:

```
$ kubectl -n hw23 get secret hw23-tls
NAME       TYPE                DATA   AGE
hw23-tls   kubernetes.io/tls   2      0s
```

В [manifests/.gitignore](manifests/.gitignore) добавил `*.key`, `*.crt`, `*.pem`, `*.csr`,
так что случайно закоммитить ключ не получится.

### Приложение и Ingress

`secure-web` ([06-secure-deployment.yaml](manifests/06-secure-deployment.yaml)) — один nginx,
страница из ConfigMap `secure-content` ([05-secure-content.yaml](manifests/05-secure-content.yaml)),
снова через `subPath`. Service `secure-svc` ([07-secure-svc.yaml](manifests/07-secure-svc.yaml)) —
80 → 80.

Ingress ([08-secure-ingress.yaml](manifests/08-secure-ingress.yaml)):

```yaml
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - hw23.local
      secretName: hw23-tls
  rules:
    - host: hw23.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: secure-svc
                port:
                  number: 80
```

```
$ kubectl -n hw23 describe ingress secure-ingress
Ingress Class:    nginx
TLS:
  hw23-tls terminates hw23.local
Rules:
  Host        Path  Backends
  ----        ----  --------
  hw23.local
              /   secure-svc:80 (10.1.184.52:80)
```

### HTTPS снаружи

```
$ curl -k --resolve hw23.local:30443:192.168.50.225 https://hw23.local:30443/ -D- | head -8
HTTP/2 200
content-type: text/html
content-length: 1411
strict-transport-security: max-age=31536000; includeSubDomains

<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Netology. Kubernetes homework 2.3 — HTTPS</title>
```

Сертификат:

```
$ openssl s_client -connect 192.168.50.225:30443 -servername hw23.local </dev/null 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
subject=CN=hw23.local, O=Netology
issuer=CN=hw23.local, O=Netology
notBefore=Oct 28 08:31:25 2026 GMT
notAfter=Oct 28 08:31:25 2027 GMT
X509v3 Subject Alternative Name:
    DNS:hw23.local
```

Без `-k` curl ожидаемо ругается, сертификат самоподписной и в доверенные не добавлен:

```
curl: (60) SSL certificate OpenSSL verify result: self-signed certificate (18)
```

HTTP на 30080 с тем же Host отдаёт редирект — у ingress-nginx включён `force-ssl-redirect`
по умолчанию, когда для хоста прописан TLS:

```
$ curl --resolve hw23.local:30080:192.168.50.225 -o /dev/null -D- http://hw23.local:30080/ | head -5
HTTP/1.1 308 Permanent Redirect
Location: https://hw23.local
```

### Проверка приоритета хоста

В кластере уже был ingress из задания 1.5 — `hw15/netology-ingress`, без `host` (то есть `*`)
с путями `/` и `/api` на свои сервисы. Он появился как раз во время работы, при первой
проверке `kubectl get ingress -A` был пуст. Попытка добавить второй бесхостовый ingress
для теста упёрлась в admission-валидацию:

```
admission webhook "validate.nginx.ingress.kubernetes.io" denied the request:
host "_" and path "/" is already defined in ingress hw15/netology-ingress
```

Зато этот конфликт и есть проверка: правило с конкретным `host` должно перебивать catch-all.
Фактически:

```
$ kubectl get ingress -A
NAMESPACE   NAME               CLASS   HOSTS        ADDRESS          PORTS
hw15        netology-ingress   nginx   *            10.152.183.147   80
hw23        secure-ingress     nginx   hw23.local                    80, 443
```

- `https://hw23.local:30443/` → `<title>Netology. Kubernetes homework 2.3 — HTTPS</title>`, наша страница;
- `https://192.168.50.225:30443/` без совпадающего Host → `Welcome to nginx!`, отдаёт catch-all из hw15;
- `https://hw23.local:30443/api` → 404 от nginx из `secure-svc`. Путь `/api` остался у бесхостового
  ingress, потому что для `hw23.local` матчится наше правило `/`, и в hw15 запрос не уходит.

То есть порядок ingress-nginx соблюдает: точный хост важнее `*`, и внутри выбранного хоста
`/` уже забирает всё, включая `/api`.

### Состояние после проверок

```
$ kubectl -n hw23 get pods -o wide
NAME                          READY   STATUS    RESTARTS   AGE    IP
secure-web-6fd557bdcf-tqjx8   1/1     Running   0          44s    10.1.184.52
web-66849698c8-v7fwn          2/2     Running   0          2m37s  10.1.184.37

$ kubectl -n hw23 get svc
NAME         TYPE        CLUSTER-IP       PORT(S)
secure-svc   ClusterIP   10.152.183.181   80/TCP
web-svc      ClusterIP   10.152.183.52    80/TCP
```

## Итог

- Конфликт 80-го порта воспроизвёл на временном деплойменте: `bind() to 0.0.0.0:80 failed (98: Address in use)`.
- ConfigMap `multitool-config` с `HTTP_PORT=8080` и `HTTPS_PORT=8443` через `envFrom` развёл
  веб-серверы по портам, под `web` живёт в состоянии 2/2 Running.
- Страницы отдаются из ConfigMap через `subPath`: каталог nginx не затёрт, `50x.html` на месте.
- HTTPS на 30443 с самоподписным сертификатом `CN=hw23.local` из Secret `hw23-tls`; ключ
  в репозиторий не попал.
- Host-based правило `hw23.local` выигрывает у бесхостового ingress из 1.5 — проверил и по
  странице, и по пути `/api`.
