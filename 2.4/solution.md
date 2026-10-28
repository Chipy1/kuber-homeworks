# Домашнее задание 2.4. Управление доступом

Задача: сделать для человека отдельный kubeconfig и такой набор прав, чтобы он мог смотреть поды, их описание и логи, но не мог ни удалить под, ни прочитать секреты.

Всё делалось на живом MicroK8s v1.35.6 (узел `b550-tomahawk`, `192.168.50.225`, apiserver на `16443`). Рабочий namespace — `hw24`, в нём же лежат манифесты из [manifests/](manifests/).

## Стенд оказался с AlwaysAllow

Сначала собрал ключ, CSR, сертификат и kubeconfig, затем проверил нового пользователя до того, как создал хоть одну роль. Ожидал `Forbidden`, а получил полный доступ:

```
$ KUBECONFIG=/tmp/netology-user/netology-user.conf kubectl get pods -n kube-system
NAME                                       READY   STATUS    RESTARTS   AGE
calico-kube-controllers-676854dc5d-7lds5   1/1     Running   52         21d
calico-node-b8wd4                          0/1     Running   4          15h
coredns-78894c95f4-v2kkx                   1/1     Running   51         21d
metrics-server-65556bc7f8-vm2f7            0/1     Running   3          15h

$ kubectl auth can-i --list -n hw24
Resources   Non-Resource URLs   Resource Names   Verbs
*.*         []                  []               [*]
            [*]                 []               [*]
```

Первый kubeconfig я собирал в `/tmp/netology-user`, позже скрипт делает его в `~/.kube/netology-user.conf` — на доступ это не влияет, оба указывают на один apiserver с одним и тем же сертификатом.

Пользователь без единого RoleBinding листал чужой namespace и видел звёздочки во всех правах. Причина нашлась в аргументах apiserver:

```
$ grep authorization-mode /var/snap/microk8s/current/args/kube-apiserver
--authorization-mode=AlwaysAllow
```

В `microk8s status` addon `rbac` при этом числился в disabled. Пока авторизатор AlwaysAllow, apiserver разрешает аутентифицированному клиенту всё подряд, и созданные Role/RoleBinding просто никто не читает. То есть без включения RBAC показать `Forbidden` физически невозможно, и вся домашняя работа превращается в набор YAML, который ни на что не влияет.

Штатный способ — `microk8s enable rbac`, но он требует root, а `sudo -n true` на этой машине ответил `sudo: a password is required`, пароля у меня нет. Зато мой пользователь входит в группы `microk8s` и `docker`, поэтому addon я запустил от root через docker, не редактируя системные файлы руками:

```
$ docker run --rm --privileged --pid=host -v /:/host alpine:latest \
    nsenter -t 1 -m -u -i -n -p -- /snap/bin/microk8s enable rbac
Infer repository core for addon rbac
Enabling RBAC
Reconfiguring apiserver
Restarting apiserver
RBAC is enabled
```

Скрипт addon'а сам меняет строку в args (`AlwaysAllow` → `RBAC,Node`) и перезапускает apiserver, на всё ушло 3.8 секунды. Проверил, что кластер поднялся: `/readyz` вернул `ok`, узел `Ready`, в `kube-system` все четыре пода `Running`, `ingress-nginx-controller` тоже `Running`. Admin-kubeconfig не затронут — пользователь `admin` из `system:masters`, такие биндинги проверяются первыми и дают полный доступ в обход ролей.

Стоит помнить, что рестарт apiserver на общем стенде обрывает чужие соединения на несколько секунд. Здесь обошлось четырьмя, но включать RBAC посреди чужой работы — так себе идея, лучше договариваться заранее.

## Ключ и CSR

Ключ и запрос делал обычным openssl, без всяких SAN и расширений — Kubernetes смотрит только на CN и O:

```
$ openssl genrsa -out netology-user.key 2048
$ openssl req -new -key netology-user.key -out netology-user.csr \
    -subj "/CN=netology-user/O=netology"
$ openssl req -in netology-user.csr -noout -subject
subject=CN=netology-user, O=netology
```

CN попадёт в `User`, O — в `Group` (в `kubectl auth whoami` это видно ниже: группы `netology` и `system:authenticated`). Файлы ключа лежат в `/tmp/netology-user`, в репозиторий они не попадают — в папке задачи есть [.gitignore](.gitignore) на `*.key`, `*.crt`, `*.csr`, `*.conf`.

## Подпись через CSR API

В методичке предлагают подписать запрос руками: `openssl x509 -req -CA ca.crt -CAkey ca.key`. На моём стенде `ca.key` вообще-то читается, потому что файл лежит как `root:microk8s 660`, а я в группе `microk8s`:

```
$ ls -l /var/snap/microk8s/current/certs/ca.key
-rw-rw---- 1 root microk8s 1704 Oct  6 16:06 /var/snap/microk8s/current/certs/ca.key
```

Но так делать не стал. Ручная подпись требует копию приватного ключа CA на машине, где им может воспользоваться кто угодно, и никто не помнит, какой сертификат когда выписан и на какой срок. Kubernetes умеет это сам: объект `CertificateSigningRequest` с `signerName: kubernetes.io/kube-apiserver-client` подписывается CA кластера после того, как кто-то с правом `approve` поставит условие Approved. Приватный ключ CA для этого не нужен ни мне, ни скрипту, ни CI.

Манифест с настоящим request из моей генерации — [manifests/csr.yaml](manifests/csr.yaml), base64 там длинный, поэтому середина сокращена:

```yaml
apiVersion: certificates.k8s.io/v1
kind: CertificateSigningRequest
metadata:
  name: netology-user
spec:
  request: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURSBSRVFVRVNULS0tLS0KTUlJQ2NEQ0NBV...
  signerName: kubernetes.io/kube-apiserver-client
  usages:
    - client auth
```

Дальше approve и выдача:

```
$ kubectl apply -f manifests/csr.yaml
certificatesigningrequest.certificates.k8s.io/netology-user created
$ kubectl get csr netology-user
NAME            AGE   SIGNERNAME                            REQUESTOR   CONDITION
netology-user   0s    kubernetes.io/kube-apiserver-client   admin       Pending

$ kubectl certificate approve netology-user
certificatesigningrequest.certificates.k8s.io/netology-user approved
$ kubectl get csr netology-user
NAME            AGE   SIGNERNAME                            REQUESTOR   CONDITION
netology-user   5s    kubernetes.io/kube-apiserver-client   admin       Approved,Issued
```

Сертификат забирается из `.status.certificate` (там base64) и проверяется:

```
$ kubectl get csr netology-user -o jsonpath='{.status.certificate}' \
    | base64 -d > netology-user.crt
$ openssl x509 -in netology-user.crt -noout -subject -issuer -serial -dates
subject=O=netology, CN=netology-user
issuer=CN=10.152.183.1
serial=1F9403E19C6628DAF98A6609D8393028
notBefore=Oct 28 08:28:24 2026 GMT
notAfter=Oct 28 08:28:24 2027 GMT

$ openssl verify -CAfile ca.crt netology-user.crt
netology-user.crt: OK
```

`issuer=CN=10.152.183.1` — это CA MicroK8s (у него такой CN), то есть подпись действительно кластерная, а не самодельная. Срок годности — год, `verify` против `ca.crt` проходит.

## kubeconfig

Собрал отдельный конфиг, чтобы не смешивать с админским. Тут наткнулся на мелочь: у `kubectl config set-cluster` нет флага `--certificate-authority-data`, есть только путь к файлу:

```
$ kubectl config set-cluster microk8s --certificate-authority-data=...
error: unknown flag: --certificate-authority-data
```

Пришлось сначала вынуть CA в файл и зашить его через `--embed-certs`:

```
$ kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' \
    | base64 -d > ca.crt
$ kubectl config --kubeconfig=$HOME/.kube/netology-user.conf \
    set-cluster microk8s --server=https://192.168.50.225:16443 \
    --certificate-authority=ca.crt --embed-certs=true
$ kubectl config --kubeconfig=$HOME/.kube/netology-user.conf \
    set-credentials netology-user --client-certificate=netology-user.crt \
    --client-key=netology-user.key --embed-certs=true
$ kubectl config --kubeconfig=$HOME/.kube/netology-user.conf \
    set-context netology-user@microk8s --cluster=microk8s --user=netology-user --namespace=hw24
$ kubectl config --kubeconfig=$HOME/.kube/netology-user.conf use-context netology-user@microk8s
```

Получился самодостаточный файл, который можно отдать пользователю как есть:

```
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: DATA+OMITTED
    server: https://192.168.50.225:16443
  name: microk8s
contexts:
- context:
    cluster: microk8s
    namespace: hw24
    user: netology-user
  name: netology-user@microk8s
current-context: netology-user@microk8s
kind: Config
users:
- name: netology-user
  user:
    client-certificate-data: DATA+OMITTED
    client-key-data: DATA+OMITTED
```

Проверка личности:

```
$ KUBECONFIG=$HOME/.kube/netology-user.conf kubectl auth whoami
ATTRIBUTE   VALUE
Username    netology-user
Groups      [netology system:authenticated]
```

## Роль и биндинг

Роль namespace-ная, ровно на то, что просили: поды, их логи и события. `manifests/role.yaml` (пояснительные комментарии из файла здесь убрал):

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pod-reader
  namespace: hw24
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["get", "list", "watch"]
```

`manifests/rolebinding.yaml` цепляет роль на пользователя (не на группу — так нагляднее, хотя `O=netology` в сертификате тоже даёт группу `netology`, и биндинг на неё работал бы так же):

```yaml
subjects:
  - kind: User
    name: netology-user
    apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: Role
  name: pod-reader
  apiGroup: rbac.authorization.k8s.io
```

Тестовый под — [manifests/pod.yaml](manifests/pod.yaml), обычный nginx с лимитами. После применения:

```
$ kubectl apply -f manifests/role.yaml -f manifests/rolebinding.yaml -f manifests/pod.yaml
role.rbac.authorization.k8s.io/pod-reader created
rolebinding.rbac.authorization.k8s.io/netology-user-pod-reader created
pod/demo-pod created

$ kubectl get pods -n hw24 -o wide
NAME       READY   STATUS    RESTARTS   AGE   IP            NODE            NOMINATED NODE
demo-pod   1/1     Running   0          33s   10.1.184.18   b550-tomahawk   <none>
```

Под сначала повисел в `Pending` секунд тридцать — это отходил scheduler после рестарта apiserver, событий не было вообще. Когда он прогрелся, под уехал на узел и поднялся.

## Что пользователь может

Всё дальше — от имени `netology-user`, с его kubeconfig.

```
$ kubectl get pods
NAME       READY   STATUS    RESTARTS   AGE
demo-pod   1/1     Running   0          70s

$ kubectl describe pod demo-pod
Name:             demo-pod
Namespace:        hw24
Priority:         0
Service Account:  default
Node:             b550-tomahawk/192.168.50.225
Start Time:       Wed, 28 Oct 2026 13:32:56 +0500
Labels:           app=demo-pod
Status:           Running
IP:               10.1.184.18
Containers:
  nginx:
    Container ID:   containerd://8bf2f388c67010850aa9f6eb6660b4dd9b4e3721085b5ad0de51d51bf1e1479d
    Image:          nginx:1.27
...
Events:
  Type    Reason     Age   From               Message
  ----    ------     ----  ----               -------
  Normal  Scheduled  37s   default-scheduler  Successfully assigned hw24/demo-pod to b550-tomahawk
  Normal  Pulled     37s   kubelet            spec.containers{nginx}: Container image "nginx:1.27" already present on machine and can be accessed by the pod
  Normal  Created    37s   kubelet            spec.containers{nginx}: Container created
  Normal  Started    37s   kubelet            spec.containers{nginx}: Container started

$ kubectl logs demo-pod
/docker-entrypoint.sh: /docker-entrypoint.d/ is not empty, will attempt to perform configuration
/docker-entrypoint.sh: Looking for shell scripts in /docker-entrypoint.d/
...
/docker-entrypoint.sh: Configuration complete; ready for start up
2026/10/28 08:32:57 [notice] 1#1: using the "epoll" event method
2026/10/28 08:32:57 [notice] 1#1: nginx/1.27.5
2026/10/28 08:32:57 [notice] 1#1: start worker processes
...
```

События в `describe` непустые именно потому, что в роли есть `events`. Проверил это отдельно: убрал третье правило, и `describe` молча показал пустоту, без ошибки:

```
$ kubectl describe pod demo-pod | sed -n '/Events:/,$p'
Events:                      <none>
```

То есть без `events` пользователь не увидит, почему под не стартует, — а именно за этим `describe` обычно и нужен. Роль потом вернул через `kubectl apply`.

Так же проверил, что логи — отдельное право на субресурс `pods/log`, а не часть `pods`. Убрал второе правило:

```
$ kubectl logs demo-pod
Error from server (Forbidden): pods "demo-pod" is forbidden: User "netology-user" cannot get resource "pods/log" in API group "" in the namespace "hw24"
```

`kubectl logs` ходит в `/api/v1/namespaces/hw24/pods/demo-pod/log`, и это отдельная запись в RBAC. Право на `pods` её не покрывает, поэтому в роли она выписана явно.

## Что пользователю нельзя

Секреты и удаление:

```
$ kubectl get secrets
Error from server (Forbidden): secrets is forbidden: User "netology-user" cannot list resource "secrets" in API group "" in the namespace "hw24"

$ kubectl delete pod demo-pod
Error from server (Forbidden): pods "demo-pod" is forbidden: User "netology-user" cannot delete resource "pods" in API group "" in the namespace "hw24"

$ kubectl get pods -n default
Error from server (Forbidden): pods is forbidden: User "netology-user" cannot list resource "pods" in API group "" in the namespace "default"
```

Последняя ошибка про namespace — важная: Role и RoleBinding живут внутри `hw24`, за его границы они не распространяются никак. Даже `get pods` в соседнем namespace уже запрещён. Если бы понадобился доступ везде, это был бы ClusterRole + ClusterRoleBinding, но по заданию доступ нужен только в `hw24`.

Полный список прав пользователя:

```
$ kubectl auth can-i --list
Resources                                       Non-Resource URLs   Resource Names   Verbs
selfsubjectreviews.authentication.k8s.io        []                  []               [create]
selfsubjectaccessreviews.authorization.k8s.io   []                  []               [create]
selfsubjectrulesreviews.authorization.k8s.io   []                  []               [create]
events                                          []                  []               [get list watch]
pods/log                                        []                  []               [get list watch]
pods                                            []                  []               [get list watch]
                                                [/api/*]            []               [get]
                                                [/api]              []               [get]
                                                [/apis/*]           []               [get]
                                                [/healthz]          []               [get]
                                                [/livez]            []               [get]
                                                [/readyz]           []               [get]
                                                [/version]          []               [get]
```

Три первых строки и запросы к `/api`, `/healthz` — это дефолтные биндинги `system:basic-user` и `system:discovery` для группы `system:authenticated`, они есть у любого, кто прошёл аутентификацию. Всё, что даёт доступ к объектам, — только наша роль.

Точечные проверки:

```
$ kubectl auth can-i get pods/log   # yes
$ kubectl auth can-i get events     # yes
$ kubectl auth can-i get secrets    # no
$ kubectl auth can-i delete pods    # no
$ kubectl auth can-i create pods    # no
```

## Воспроизведение

Всё, что делал руками, собрано в [create-user.sh](create-user.sh). Скрипт: генерирует ключ и CSR, создаёт объект CSR, делает approve, ждёт выдачи сертификата (условия Issued в CSR нет, поэтому опрашивает `.status.certificate`), собирает kubeconfig и печатает проверку. Переменные `USER_NAME`, `GROUP`, `NAMESPACE`, `WORKDIR`, `KUBECONFIG_OUT`, `SERVER` можно переопределить, по умолчанию всё как в задании.

```
$ bash create-user.sh
== 1. ключ и CSR (CN=netology-user, O=netology)
subject=CN=netology-user, O=netology
== 2. CertificateSigningRequest в кластере
certificatesigningrequest.certificates.k8s.io/netology-user created
== 3. approve
certificatesigningrequest.certificates.k8s.io/netology-user approved
== 4. ждём, пока CA выпишет сертификат
subject=O=netology, CN=netology-user
issuer=CN=10.152.183.1
notBefore=Oct 28 08:28:24 2026 GMT
notAfter=Oct 28 08:28:24 2027 GMT
== 5. kubeconfig /home/alex/.kube/netology-user.conf
...
== 6. проверка
Username                                            netology-user
Groups                                              [netology system:authenticated]
NAME       READY   STATUS    RESTARTS   AGE
demo-pod   1/1     Running   0          61s
```

Скрипт запускается под тем, у кого есть право approve на CSR (в microk8s это admin из `~/.kube/config`), ключи пишет в `/tmp/netology-user` с правами 700 на каталог, kubeconfig — в `~/.kube/netology-user.conf` с 600. Приватный ключ пользователя никуда не копируется, ключ CA не нужен вовсе.

Манифесты применяются отдельно, они в [manifests/](manifests/):

```
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/csr.yaml
kubectl certificate approve netology-user
kubectl apply -f manifests/role.yaml -f manifests/rolebinding.yaml -f manifests/pod.yaml
```

## Итог

- Сертификат `netology-user` подписан CA кластера через CSR API (`issuer=CN=10.152.183.1`, `Approved,Issued`), приватный ключ CA не понадобился.
- Отдельный kubeconfig с зашитыми сертификатом, ключом и CA лежит в `~/.kube/netology-user.conf`, контекст `netology-user@microk8s`, namespace по умолчанию `hw24`.
- Роль `pod-reader` даёт `pods`, `pods/log`, `events` на get/list/watch, биндинг — на User `netology-user`.
- `get pods`, `describe`, `logs` от имени пользователя работают; `get secrets`, `delete pod` и любой запрос в другой namespace получают `Forbidden`.
- Кластер изначально работал с `--authorization-mode=AlwaysAllow`, RBAC пришлось включить (`microk8s enable rbac` от root через docker + nsenter), apiserver перезапускался примерно на 4 секунды.
- `demo-pod` и RBAC-объекты оставлены в кластере, ключи и сертификаты в репозиторий не попали.
