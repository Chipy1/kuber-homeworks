# Домашнее задание к занятию «Обновление приложений»

Стенд: MicroK8s v1.35.6 на одном узле `b550-tomahawk`, внешний адрес `192.168.50.225`, containerd 2.1.6. Ingress-nginx стоит в namespace `ingress-nginx` и доступен снаружи через NodePort `30080` (HTTP). Все ресурсы этой работы — в namespace `hw34`.

## Задание 1. Выбор стратегии обновления

Условия из задания: несколько реплик, ресурсы фиксированы и увеличить их нельзя, в непиковое время свободно 20%, обновление мажорное, старая и новая версии друг с другом не работают, приложение должно оставаться доступным.

Перебрал четыре варианта.

Recreate останавливает все поды и только потом поднимает новые. Проблема с несовместимостью версий снимается полностью, но всё время обновления сервис лежит: сначала 0 подов, потом постепенный прогрев. Для условия «приложение должно быть доступно» не подходит.

RollingUpdate заменяет поды порциями. Ключевой момент — параметры. Если оставить дефолт (`maxSurge: 25%`, `maxUnavailable: 25%`), контроллер захочет поднять лишний под сверх пяти, а свободных ресурсов ровно 20%, то есть один под. Запас есть только в непиковое время, и тратить его на surge рискованно: при пиковой нагрузке шестой под просто некуда поставить, и обновление застрянет. Поэтому взял `maxSurge: 0` и `maxUnavailable: 1` (1 = 20% от 5). Тогда новых подов не появляется вообще, а старых одновременно снимается не больше одного: в любой момент живы минимум 4 из 5, и они продолжают принимать трафик через Service, пока новый под не пройдёт readinessProbe.

Blue-Green потребовал бы двух полных окружений: 5 подов старой версии плюс 5 новой, то есть 200% ресурсов. Запаса в 20% не хватает даже близко, пришлось бы выкупать вторую половину мощностей. Переключение трафика мгновенное и откат мгновенный, но цена не сходится с условием.

Canary можно сделать на одном дополнительном поде (5 + 1 = 120%, как раз в запас), и трафик на него заворачивается через ingress по весу или заголовку. Вариант рабочий, но у него та же беда, что и у rolling: какое-то время обе версии обслуживают живые пользователи. Плюс нужен ingress-контроллер и время на выкатку, а полный перевод на новую версию всё равно требует места под пять новых подов.

Выбрал RollingUpdate с `maxSurge: 0`, `maxUnavailable: 1`. Он укладывается в имеющиеся ресурсы без единого дополнительного пода и держит 80% ёмкости всё обновление. Компромисс осознанный: версии временно сосуществуют, поэтому мажорное обновление не может быть «просто заменой образа». Нужна совместимость на время окна — версионирование API, терпимость старого кода к новым полям, миграции БД по схеме expand/contract (сначала добавить новое, потом переключить код, потом убрать старое) — либо окно должно быть настолько коротким, чтобы им можно было пренебречь. В лабораторке ниже окно вышло 18 секунд, и приложение статическое, так что расхождение версий никого не задело. Если бы несовместимость была жёсткой (например, миграция, роняющая старую схему), тот же RollingUpdate пришлось бы дополнить обратной совместимостью или согласованным окном простоя, а Blue-Green стал бы единственным честным вариантом — но он не влезает в ресурсы.

## Задание 2. Обновление приложения

### 2.1. Исходный деплоймент на nginx 1.19

Манифесты: [manifests/namespace.yaml](manifests/namespace.yaml), [manifests/web-deployment.yaml](manifests/web-deployment.yaml), [manifests/web-service.yaml](manifests/web-service.yaml). В поде два контейнера: `nginx:1.19` с readinessProbe на `/` и `wbitt/network-multitool` с `HTTP_PORT=8080`. Реплик пять, стратегия — та, что выбрана в первом задании.

```console
$ kubectl -n hw34 apply -f manifests/web-deployment.yaml -f manifests/web-service.yaml
deployment.apps/web created
service/web-svc created
$ kubectl -n hw34 rollout status deployment/web
Waiting for deployment "web" rollout to finish: 0 of 5 updated replicas are available...
...
deployment "web" successfully rolled out
```

Первый запуск шёл около 85 секунд, причём поды стали `2/2 Running` заметно раньше, чем Deployment показал готовность: в какой-то момент `kubectl get deploy` отдавал `0/5`, хотя все пять подов уже были Ready. Кластер общий, в это время на нём крутились другие задания, и пересчёт статуса подтормаживал. Ещё через несколько секунд цифры сошлись:

```console
$ kubectl -n hw34 get deploy web
NAME   READY   UP-TO-DATE   AVAILABLE   AGE
web    5/5     5            5           94s

$ kubectl -n hw34 get pods -o wide
NAME                   READY   STATUS    RESTARTS   AGE   IP            NODE
web-7588f557b9-7qxjc   2/2     Running   0          94s   10.1.184.41   b550-tomahawk
web-7588f557b9-nrz4g   2/2     Running   0          94s   10.1.184.47   b550-tomahawk
web-7588f557b9-tkxpv   2/2     Running   0          94s   10.1.184.3    b550-tomahawk
web-7588f557b9-x66nw   2/2     Running   0          94s   10.1.184.24   b550-tomahawk
web-7588f557b9-ztzf6   2/2     Running   0          94s   10.1.184.62   b550-tomahawk
```

### 2.2. Обновление до 1.20

Чтобы не гадать, пропадает ли доступность, заранее поднял в namespace под `curl-watcher` ([manifests/curl-watcher.yaml](manifests/curl-watcher.yaml)), который в цикле дёргает `http://web-svc.hw34.svc.cluster.local/` и печатает код ответа. 400 запросов по 0.2 с — как раз с запасом на всё обновление.

```console
$ kubectl -n hw34 set image deployment/web nginx=nginx:1.20
deployment.apps/web image updated
$ kubectl -n hw34 rollout status deployment/web
Waiting for deployment "web" rollout to finish: 1 out of 5 new replicas have been updated...
Waiting for deployment "web" rollout to finish: 2 out of 5 new replicas have been updated...
Waiting for deployment "web" rollout to finish: 3 out of 5 new replicas have been updated...
Waiting for deployment "web" rollout to finish: 4 out of 5 new replicas have been updated...
Waiting for deployment "web" rollout to finish: 4 of 5 updated replicas are available...
deployment "web" successfully rolled out
```

Старт — 13:31:31, финиш — 13:31:49, восемнадцать секунд на пять подов. В событиях деплоймента видно, как контроллер попеременно снимал по одному поду старого ReplicaSet и добавлял по одному новому:

```text
Normal  ScalingReplicaSet  22s  Scaled down replica set web-7588f557b9 from 5 to 4
Normal  ScalingReplicaSet  22s  Scaled up replica set web-694b856997 from 0 to 1
...
Normal  ScalingReplicaSet  7s   Scaled down replica set web-7588f557b9 from 1 to 0
Normal  ScalingReplicaSet  7s   (combined from similar events): Scaled up replica set web-694b856997 from 4 to 5
```

Новые поды (в старом ReplicaSet осталось 0):

```console
$ kubectl -n hw34 get rs
NAME             DESIRED   CURRENT   READY   AGE
web-694b856997   5         5         5       21s
web-7588f557b9   0         0         0       2m12s

$ kubectl -n hw34 get pods -o wide
NAME                   READY   STATUS    RESTARTS   AGE   IP            NODE
web-694b856997-4cmdc   2/2     Running   0          10s   10.1.184.16   b550-tomahawk
web-694b856997-6hph9   2/2     Running   0          21s   10.1.184.58   b550-tomahawk
web-694b856997-gfbdc   2/2     Running   0          8s    10.1.184.51   b550-tomahawk
web-694b856997-gzx62   2/2     Running   0          12s   10.1.184.29   b550-tomahawk
web-694b856997-s86cj   2/2     Running   0          6s    10.1.184.28   b550-tomahawk
```

История ревизий после обновления:

```console
$ kubectl -n hw34 rollout history deployment/web
REVISION  CHANGE-CAUSE
1         <none>
2         <none>
```

Лог `curl-watcher` за окно обновления (в поде время UTC, на хосте UTC+5, отсюда 08:31 вместо 13:31):

```console
$ kubectl -n hw34 logs curl-watcher | awk '{print $2}' | sort | uniq -c
    130 200
```

130 запросов с 08:31:28 по 08:31:56, все 200. Ни одной дырки между старыми и новыми подами: Service исключает под из endpoints, как только тот перестаёт быть Ready, и запрос уходит на оставшиеся четыре. `maxUnavailable: 1` при этом не даёт контроллеру снять больше одного сразу, поэтому ниже четырёх живых подов дело не доходит.

### 2.3. Попытка обновления до 1.28 и её провал

Здесь задание разошлось с реальностью registry. Оно исходит из того, что тега 1.28 ещё нет, но `nginx:1.28.0` опубликован и прекрасно скачивается. Проверил через API Docker Hub:

```console
$ for t in 1.28.0 1.28.99 1.28.1; do printf '%s ' "$t"; curl -s -o /dev/null -w '%{http_code}\n' "https://hub.docker.com/v2/repositories/library/nginx/tags/$t"; done
1.28.0 200
1.28.99 404
1.28.1 200
```

Первый заход на `nginx:1.28.0` поэтому прошёл штатно: деплоймент докатился до 5/5, `nginx -v` внутри пода отдал `nginx/1.28.0`. Заодно это ещё одно окно с watcher'ом: 278 ответов 200 и один `000` (curl упёрся в `--max-time 2`) из 279. Похоже, единственный запрос попал в момент, когда под уже гасился, а правила ещё не успели обновиться; на общую картину это не влияет, но честно фиксирую.

Чтобы всё-таки воспроизвести именно неудачное обновление, взял заведомо несуществующий тег `nginx:1.28.99`:

```console
$ kubectl -n hw34 set image deployment/web nginx=nginx:1.28.99
deployment.apps/web image updated
$ kubectl -n hw34 rollout status deployment/web --timeout=40s
Waiting for deployment "web" rollout to finish: 0 out of 5 new replicas have been updated...
Waiting for deployment "web" rollout to finish: 1 out of 5 new replicas have been updated...
error: timed out waiting for the condition
```

Деплоймент встал на 4/5, новый под так и не смог вытянуть образ:

```console
$ kubectl -n hw34 get deploy web
NAME   READY   UP-TO-DATE   AVAILABLE   AGE
web    4/5     1            4           5m29s

$ kubectl -n hw34 get pods -o wide
NAME                   READY   STATUS         RESTARTS   AGE    IP            NODE
web-56dd68fc5f-cr4qn   2/2     Running        0          113s   10.1.184.60   b550-tomahawk
web-56dd68fc5f-jzx6k   2/2     Running        0          110s   10.1.184.54   b550-tomahawk
web-56dd68fc5f-rm5xk   2/2     Running        0          2m8s   10.1.184.4    b550-tomahawk
web-56dd68fc5f-thh2m   2/2     Running        0          116s   10.1.184.27   b550-tomahawk
web-758f4bfc87-nhl6f   1/2     ErrImagePull   0          42s    10.1.184.24   b550-tomahawk
```

`kubectl describe` по проблемному поду показывает причину без вариантов:

```text
Warning  Failed  14s (x3 over 65s)  kubelet  Failed to pull image "nginx:1.28.99": rpc error: code = NotFound desc = get image config descriptor: unexpected media type text/html for sha256:11cb862f...: not found
Warning  Failed  14s (x3 over 65s)  kubelet  Error: ErrImagePull
Normal   BackOff 31s (x3 over 64s)  kubelet  Back-off pulling image "nginx:1.28.99"
Warning  Failed  31s (x3 over 64s)  kubelet  Error: ImagePullBackOff
```

Спустя полминуты под висел в `ImagePullBackOff` (multitool в нём уже поднялся, поэтому `1/2`). Четыре пода прошлой версии остались живы — `maxUnavailable: 1` разрешает снять ровно один, а новый не стал Ready, поэтому дальше контроллер не пошёл. Проверил доступность тем же watcher'ом: 307 запросов за окно сбоя, все 200. То есть деплой сломался, а сервис нет — это ровно то поведение, ради которого выбиралась стратегия.

### 2.4. Откат

Откатывался не «на предыдущую», а явно на ревизию с 1.20: к этому моменту ревизий накопилось четыре, и обычный `rollout undo` вернул бы 1.28.0, а нужна рабочая 1.20.

```console
$ kubectl -n hw34 rollout undo deployment/web --to-revision=2
deployment.apps/web rolled back
$ kubectl -n hw34 rollout status deployment/web
deployment "web" successfully rolled out

$ kubectl -n hw34 get deploy web
NAME   READY   UP-TO-DATE   AVAILABLE   AGE
web    5/5     5            5           6m16s

$ kubectl -n hw34 exec deploy/web -c nginx -- nginx -v
nginx version: nginx/1.20.2
```

История после отката выглядит так:

```console
$ kubectl -n hw34 rollout history deployment/web
REVISION  CHANGE-CAUSE
1         <none>
3         <none>
4         <none>
5         <none>
```

Ревизия 2 пропала из списка, потому что откат не переключает указатель назад, а создаёт новую ревизию с тем же шаблоном (она получила номер 5), а прежнюю убирает. Поэтому в истории нет двойки, зато есть 1.19, 1.28.0, неудачный 1.28.99 и текущая 1.20.

## Задание 3*. Canary deployment

Собрал две версии приложения на разных ConfigMap. Страницы лежат в [manifests/canary/configmap-v1.yaml](manifests/canary/configmap-v1.yaml) и [manifests/canary/configmap-v2.yaml](manifests/canary/configmap-v2.yaml), в тексте явно написано `version: v1` и `version: v2`. ConfigMap монтируется поверх `/usr/share/nginx/html`, так что nginx отдаёт именно её.

Деплойменты и сервисы: [manifests/canary/web-v1.yaml](manifests/canary/web-v1.yaml), [manifests/canary/web-v2.yaml](manifests/canary/web-v2.yaml) — по две реплики, образ `nginx:1.20`, селекторы `app: web-v1` и `app: web-v2`. Ингрессы — [manifests/canary/ingresses.yaml](manifests/canary/ingresses.yaml): основной `web-canary` на host `canary.hw34.local` ведёт на `web-v1`, второй `web-canary-v2` с тем же host и path, но с аннотациями `nginx.ingress.kubernetes.io/canary: "true"` и `canary-weight: "20"` — на `web-v2`.

```console
$ kubectl -n hw34 apply -f manifests/canary/
configmap/web-v1-page created
configmap/web-v2-page created
ingress.networking.k8s.io/web-canary created
ingress.networking.k8s.io/web-canary-v2 created
deployment.apps/web-v1 created
service/web-v1 created
deployment.apps/web-v2 created
service/web-v2 created
deployment "web-v1" successfully rolled out
deployment "web-v2" successfully rolled out

$ kubectl -n hw34 get ingress
NAME                                      CLASS   HOSTS               ADDRESS   PORTS   AGE
ingress.networking.k8s.io/web-canary      nginx   canary.hw34.local             80      24s
ingress.networking.k8s.io/web-canary-v2   nginx   canary.hw34.local             80      24s

$ kubectl -n hw34 get deploy
NAME     READY   UP-TO-DATE   AVAILABLE   AGE
web      5/5     5            5           6m34s
web-v1   2/2     2            2           39s
web-v2   2/2     2            2           39s
```

Ingress-nginx при старте разбирает второй Ingress с `canary: "true"` и добавляет его backend в тот же server, распределяя запросы по весу. DNS-записи для `canary.hw34.local` нет, поэтому Host подставляется заголовком:

```console
$ curl -s -H 'Host: canary.hw34.local' http://192.168.50.225:30080/
<html>
  <head><title>hw34 canary</title></head>
  <body>
    <h1>version: v2</h1>
    <p>canary release, 20% of traffic</p>
  </body>
</html>
```

Посчитал распределение двумя прогонами по 60 запросов:

```console
$ for i in $(seq 1 60); do curl -s -H 'Host: canary.hw34.local' http://192.168.50.225:30080/ | grep -o 'version: v[12]'; done | sort | uniq -c
     43 version: v1
     17 version: v2

$ for i in $(seq 1 60); do curl -s -H 'Host: canary.hw34.local' http://192.168.50.225:30080/ | grep -o 'version: v[12]'; done | sort | uniq -c
     49 version: v1
     11 version: v2
```

Первый прогон дал 28% на v2, второй — 18%. Суммарно 28 из 120, то есть 23% при заданном весе 20%. Разброс ожидаемый: ingress-nginx выбирает backend для каждого запроса отдельно по весу, без липкости, так что на короткой выборке отклонение в несколько процентов — норма. Если нужна воспроизводимость, вместо веса используют `canary-by-header` или `canary-by-cookie` и закрепляют пользователя за версией.

## Итог

- Из четырёх стратегий под условия (нет свободных ресурсов, 20% запаса, мажорное обновление, сервис не должен падать) подошёл RollingUpdate с `maxSurge: 0` и `maxUnavailable: 1`: ноль дополнительных подов и минимум 4/5 живых.
- Обновление 1.19 → 1.20 заняло 18 секунд, watcher не увидел ни одного не-200 ответа из 130.
- Тег 1.28.0 в registry существует, поэтому для честного сбоя пришлось взять `1.28.99`; кластер выдал `ErrImagePull`/`ImagePullBackOff`, деплоймент остановился на 4/5, четыре старых пода продолжали отдавать 200 (307 запросов).
- Откат делал явно на ревизию 1.20 (`--to-revision=2`), деплоймент вернулся к 5/5 и `nginx/1.20.2`.
- Canary через аннотации ingress-nginx дал 23% трафика на v2 на 120 запросах при выставленном весе 20%.

Временный под `curl-watcher` после замеров удалён, в namespace `hw34` остались деплойменты `web`, `web-v1`, `web-v2`, сервисы `web-svc`, `web-v1`, `web-v2` и два Ingress'а.
