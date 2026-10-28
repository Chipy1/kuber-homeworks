#!/usr/bin/env python3
"""Проверка расчёта из solution.md: суммы, запас, подбор нод, раскладка подов."""

# requests приложения: (имя, копий, cpu/копия, ram/копия в ГБ)
WORKLOAD = [
    ("База данных", 3, 1.0, 4.0),
    ("Кеш", 3, 1.0, 4.0),
    ("Frontend", 5, 0.2, 0.05),
    ("Backend", 10, 1.0, 0.6),
]

RESERVE = 0.20          # запас на пики/раскатку
ADDONS_CPU, ADDONS_RAM = 0.52, 0.56   # CoreDNS, metrics-server, ingress
ADDONS_ROUNDED = (1.0, 1.0)           # сколько реально закладываю

NODE_CPU, NODE_RAM = 8, 16            # номинал рабочей ноды
RSV_CPU, RSV_RAM = 2, 4               # kubeReserved + systemReserved


def line(c="-"):
    print(c * 62)


print("1. Requests приложения")
line()
cpu = ram = 0.0
for name, n, c, r in WORKLOAD:
    cpu += n * c
    ram += n * r
    print(f"{name:<13} {n:>2} x {c:<4} = {n*c:>4.1f} ядра | {n*r:>6.2f} ГБ")
line()
print(f"{'Итого':<13} {sum(w[1] for w in WORKLOAD):>2} подов  = {cpu:>4.1f} ядра | {ram:>6.2f} ГБ")

print("\n2. Запас 20% и служебные аддоны")
line()
peak_cpu, peak_ram = cpu * (1 + RESERVE), ram * (1 + RESERVE)
print(f"workload + {RESERVE:.0%}: {peak_cpu:.2f} ядра, {peak_ram:.2f} ГБ")
print(f"аддоны (факт {ADDONS_CPU:.2f} / {ADDONS_RAM:.2f}) -> "
      f"{ADDONS_ROUNDED[0]:.1f} ядра, {ADDONS_ROUNDED[1]:.1f} ГБ")
need_cpu = peak_cpu + ADDONS_ROUNDED[0]
need_ram = peak_ram + ADDONS_ROUNDED[1]
print(f"нужно на рабочие ноды: {need_cpu:.2f} ядра, {need_ram:.2f} ГБ")

print("\n3. Подбор числа нод (N+1)")
line()
print(f"allocatable одной ноды: {NODE_CPU - RSV_CPU} ядер / {NODE_RAM - RSV_RAM} ГБ")
print(f"{'N':>2} | {'всего':>12} | {'после отказа':>14} | {'проходит':>9} | запас")
for n in range(3, 8):
    ac, ar = n * (NODE_CPU - RSV_CPU), n * (NODE_RAM - RSV_RAM)
    fc, fr = (n - 1) * (NODE_CPU - RSV_CPU), (n - 1) * (NODE_RAM - RSV_RAM)
    ok = fc >= need_cpu and fr >= need_ram
    print(f"{n:>2} | {ac:>5} / {ar:>4} | {fc:>6} / {fr:>5} | {str(ok):>9} | "
          f"{fc - need_cpu:+.1f} ядра, {fr - need_ram:+.1f} ГБ")

N = 5
alloc_c = N * (NODE_CPU - RSV_CPU)
alloc_r = N * (NODE_RAM - RSV_RAM)
print(f"\nвыбрано N={N}: allocatable {alloc_c} ядер / {alloc_r} ГБ, "
      f"сырьём {N*NODE_CPU} / {N*NODE_RAM}")

print("\n4. Загрузка")
line()
print(f"штатно:      {need_cpu/alloc_c:>5.1%} CPU / {need_ram/alloc_r:>5.1%} RAM, "
      f"свободно {alloc_c-need_cpu:.1f} ядра / {alloc_r-need_ram:.1f} ГБ")
fc, fr = (N - 1) * (NODE_CPU - RSV_CPU), (N - 1) * (NODE_RAM - RSV_RAM)
print(f"минус нода:  {need_cpu/fc:>5.1%} CPU / {need_ram/fr:>5.1%} RAM, "
      f"свободно {fc-need_cpu:.1f} ядра / {fr-need_ram:.1f} ГБ")
fc2, fr2 = (N - 2) * (NODE_CPU - RSV_CPU), (N - 2) * (NODE_RAM - RSV_RAM)
print(f"минус две:   дефицит {max(0, need_cpu-fc2):.1f} ядра / {max(0, need_ram-fr2):.1f} ГБ")

# раскладка: (имя, ram, cpu) по нодам
BEFORE = {
    "w1": [("db-0", 4, 1), ("cache-0", 4, 1), ("be-0..1", 1.2, 2), ("fe-0", 0.05, 0.2)],
    "w2": [("db-1", 4, 1), ("be-2..3", 1.2, 2), ("fe-1", 0.05, 0.2)],
    "w3": [("db-2", 4, 1), ("be-4..5", 1.2, 2), ("fe-2", 0.05, 0.2)],
    "w4": [("cache-1", 4, 1), ("be-6..7", 1.2, 2), ("fe-3", 0.05, 0.2)],
    "w5": [("cache-2", 4, 1), ("be-8..9", 1.2, 2), ("fe-4", 0.05, 0.2)],
}
AFTER = {  # w1 умер, поды переехали
    "w2": [("db-1", 4, 1), ("cache-0", 4, 1), ("be-2..3", 1.2, 2), ("fe-1", 0.05, 0.2)],
    "w3": [("db-2", 4, 1), ("be-4..5", 1.2, 2), ("be-0", 0.6, 1), ("fe-2", 0.05, 0.2)],
    "w4": [("cache-1", 4, 1), ("db-0", 4, 1), ("be-6..7", 1.2, 2), ("fe-3", 0.05, 0.2)],
    "w5": [("cache-2", 4, 1), ("be-8..9", 1.2, 2), ("be-1", 0.6, 1), ("fe-4..0", 0.1, 0.4)],
}


def placement(title, table):
    print(f"\n5. {title}")
    line()
    tot_c = tot_r = 0.0
    for node, pods in table.items():
        c = sum(p[1] for p in pods)
        r = sum(p[2] for p in pods)
        tot_c += c
        tot_r += r
        print(f"{node}: {c:>5.2f} ГБ ({c/(NODE_RAM-RSV_RAM):>4.0%}) "
              f"{r:>4.1f} ядра ({r/(NODE_CPU-RSV_CPU):>4.0%})  "
              + ", ".join(p[0] for p in pods))
    line()
    print(f"сумма: {tot_c:.2f} ГБ / {tot_r:.1f} ядер "
          f"(workload {ram:.2f} ГБ / {cpu:.1f} ядер)")


placement("Раскладка до отказа", BEFORE)
placement("После отказа w1", AFTER)

print("\n6. Диск")
line()
local_master, local_worker = 100, 200
pvc_db, pvc_cache = 100, 50
print(f"локально: {3} x {local_master} + {5} x {local_worker} = "
      f"{3*local_master + 5*local_worker} ГБ")
print(f"PVC: {3} x {pvc_db} + {3} x {pvc_cache} = {3*pvc_db + 3*pvc_cache} ГБ, "
      f"с запасом 20% = {int((3*pvc_db + 3*pvc_cache) * 1.2)} ГБ")
print(f"кластер сырьём: {3*4 + 5*NODE_CPU} vCPU, {3*8 + 5*NODE_RAM} ГБ RAM")
