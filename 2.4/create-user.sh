#!/usr/bin/env bash
# Создание ограниченного пользователя netology-user: ключ, CSR, подпись через
# Kubernetes CSR API, сборка отдельного kubeconfig.
#
# Приватный ключ CA не нужен и не запрашивается: сертификат подписывает
# kube-apiserver своим CA, когда CSR получает Approved.
#
# Запускать под пользователем, у которого есть право approve на CSR
# (в microk8s это admin из ~/.kube/config).
#
# Переменные можно переопределить: USER_NAME, GROUP, NAMESPACE, WORKDIR,
# KUBECONFIG_OUT, SERVER.
set -euo pipefail

USER_NAME="${USER_NAME:-netology-user}"
GROUP="${GROUP:-netology}"
NAMESPACE="${NAMESPACE:-hw24}"
WORKDIR="${WORKDIR:-/tmp/${USER_NAME}}"
KUBECONFIG_OUT="${KUBECONFIG_OUT:-$HOME/.kube/${USER_NAME}.conf}"
SERVER="${SERVER:-https://192.168.50.225:16443}"
CONTEXT="${USER_NAME}@microk8s"
CSR_NAME="${USER_NAME}"

mkdir -p "$WORKDIR"
chmod 700 "$WORKDIR"
cd "$WORKDIR"

echo "== 1. ключ и CSR (CN=$USER_NAME, O=$GROUP)"
openssl genrsa -out "${USER_NAME}.key" 2048 2>/dev/null
openssl req -new -key "${USER_NAME}.key" -out "${USER_NAME}.csr" \
  -subj "/CN=${USER_NAME}/O=${GROUP}"
openssl req -in "${USER_NAME}.csr" -noout -subject

echo "== 2. CertificateSigningRequest в кластере"
kubectl delete csr "$CSR_NAME" --ignore-not-found >/dev/null
REQUEST_B64="$(base64 -w0 "${USER_NAME}.csr")"
cat > csr.yaml <<EOF
apiVersion: certificates.k8s.io/v1
kind: CertificateSigningRequest
metadata:
  name: ${CSR_NAME}
spec:
  request: ${REQUEST_B64}
  signerName: kubernetes.io/kube-apiserver-client
  usages:
    - client auth
EOF
kubectl apply -f csr.yaml

echo "== 3. approve"
kubectl certificate approve "$CSR_NAME"

echo "== 4. ждём, пока CA выпишет сертификат"
for _ in $(seq 1 30); do
  kubectl get csr "$CSR_NAME" -o jsonpath='{.status.certificate}' | base64 -d > "${USER_NAME}.crt" 2>/dev/null || true
  if [ -s "${USER_NAME}.crt" ] && openssl x509 -in "${USER_NAME}.crt" -noout 2>/dev/null; then
    break
  fi
  sleep 1
done
test -s "${USER_NAME}.crt" || { echo "сертификат не выдан" >&2; exit 1; }
openssl x509 -in "${USER_NAME}.crt" -noout -subject -issuer -dates

echo "== 5. kubeconfig $KUBECONFIG_OUT"
kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' \
  | base64 -d > ca.crt
# kubectl config set-cluster не умеет --certificate-authority-data, только файл,
# поэтому CA сначала сохраняем на диск, а потом зашиваем в конфиг через --embed-certs
kubectl config --kubeconfig="$KUBECONFIG_OUT" set-cluster microk8s \
  --server="$SERVER" \
  --certificate-authority=ca.crt \
  --embed-certs=true
kubectl config --kubeconfig="$KUBECONFIG_OUT" set-credentials "$USER_NAME" \
  --client-certificate="${USER_NAME}.crt" \
  --client-key="${USER_NAME}.key" \
  --embed-certs=true
kubectl config --kubeconfig="$KUBECONFIG_OUT" set-context "$CONTEXT" \
  --cluster=microk8s \
  --user="$USER_NAME" \
  --namespace="$NAMESPACE"
kubectl config --kubeconfig="$KUBECONFIG_OUT" use-context "$CONTEXT"
chmod 600 "$KUBECONFIG_OUT"

echo "== 6. проверка"
KUBECONFIG="$KUBECONFIG_OUT" kubectl auth whoami
KUBECONFIG="$KUBECONFIG_OUT" kubectl get pods -n "$NAMESPACE"

cat <<EOF

Готово. Конфиг: $KUBECONFIG_OUT
Использование: KUBECONFIG=$KUBECONFIG_OUT kubectl get pods -n $NAMESPACE
Файлы ключа/CSR/сертификата остались в $WORKDIR и в git не попадают.
EOF
