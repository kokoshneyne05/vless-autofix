#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VLESS Auto Collector + Hybrid Tester (2026)
1. Быстрый TCP-фильтр
2. Реальный тест (googleping) только выживших
3. Если реальных 0 — берём лучшие по TCP (чтобы подписка не была пустой)
"""

import urllib.request
import re
import os
import json
import gzip
import shutil
import subprocess
import glob
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===================== НАСТРОЙКИ =====================
SOURCES = [
    "https://github.com/kort0881/vpn-vless-configs-russia/raw/refs/heads/main/output/vless.txt",
    # Добавляй ещё источники сюда
]

MAX_NODES_COLLECT = 800          # сколько максимум собираем
MAX_NODES_REAL_TEST = 120        # сколько максимум пускаем в реальный тест
TCP_TIMEOUT = 2.5                # секунд на TCP-проверку
TCP_WORKERS = 80                 # потоков для TCP
CONCURRENCY_REAL = 6             # concurrency для LiteSpeedTest (не ставь > 8)
REAL_TEST_TIMEOUT = 380          # секунд на весь реальный тест
TOP_N = 20
# =====================================================


def get_nodes(url: str) -> list:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=18) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[!] Ошибка загрузки {url}: {e}")
        return []

    content = content.replace("&amp;", "&").replace("%26", "&")
    links = []
    for line in content.splitlines():
        match = re.search(r"(vless://[^\s|\"']+)", line)
        if match:
            link = match.group(1).rstrip(".,; \t")
            if link.count("@") == 1:
                links.append(link)
    return links


def parse_host_port(vless_link: str):
    """Извлекает host:port из vless://uuid@host:port?..."""
    try:
        after = vless_link.split("@", 1)[1]
        hostport = after.split("?", 1)[0].split("#", 1)[0]
        if hostport.startswith("["):  # IPv6
            host, port = hostport[1:].split("]:")
            return host, int(port)
        else:
            host, port = hostport.rsplit(":", 1)
            return host, int(port)
    except Exception:
        return None


def tcp_check(link: str):
    """Быстрая TCP-проверка. Возвращает (link, latency_ms) или None"""
    parsed = parse_host_port(link)
    if not parsed:
        return None
    host, port = parsed
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT):
            latency = (time.perf_counter() - start) * 1000
            return (link, latency)
    except Exception:
        return None


def download_file(url: str, filename: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40) as r, open(filename, "wb") as f:
            shutil.copyfileobj(r, f)
        return True
    except Exception as e:
        print(f"[!] Не удалось скачать {filename}: {e}")
        return False


def main():
    print("=" * 60)
    print("VLESS Auto — Hybrid Tester (TCP + Real)")
    print("=" * 60)

    # ---------- 1. Сбор ----------
    all_links = set()
    for src in SOURCES:
        print(f"[*] Источник: {src}")
        nodes = get_nodes(src)
        print(f"    → {len(nodes)} ссылок")
        all_links.update(nodes)

    all_links = list(all_links)
    print(f"\n[+] Уникальных узлов: {len(all_links)}")

    if not all_links:
        open("sub.txt", "w").close()
        print("[!] Нет ссылок")
        return

    if len(all_links) > MAX_NODES_COLLECT:
        all_links = all_links[:MAX_NODES_COLLECT]
        print(f"[*] Ограничил до {MAX_NODES_COLLECT}")

    # ---------- 2. Быстрый TCP-фильтр ----------
    print(f"\n[*] TCP-проверка {len(all_links)} узлов ({TCP_WORKERS} потоков)...")
    tcp_alive = []

    with ThreadPoolExecutor(max_workers=TCP_WORKERS) as executor:
        futures = {executor.submit(tcp_check, link): link for link in all_links}
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                tcp_alive.append(res)

    # Сортируем по задержке
    tcp_alive.sort(key=lambda x: x[1])
    print(f"[+] Живых по TCP: {len(tcp_alive)}")

    if not tcp_alive:
        print("[!] Даже по TCP ничего не ответило. sub.txt пустой.")
        open("sub.txt", "w").close()
        return

    # Берём лучших по TCP для реального теста
    candidates = [link for link, _ in tcp_alive[:MAX_NODES_REAL_TEST]]
    print(f"[*] На реальный тест идёт: {len(candidates)} лучших по TCP")

    with open("raw_nodes.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(candidates))

    # ---------- 3. Реальный тест (LiteSpeedTest) ----------
    print("\n[*] Скачиваю LiteSpeedTest...")
    if not download_file(
        "https://github.com/xxf098/LiteSpeedTest/releases/download/v0.15.0/lite-linux-amd64-v0.15.0.gz",
        "lite.gz"
    ):
        # Если не скачался — отдаём топ по TCP
        top = [link for link, _ in tcp_alive[:TOP_N]]
        with open("sub.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(top) + "\n")
        print(f"[!] LiteSpeedTest не скачался. Отдал топ-{len(top)} по TCP")
        return

    with gzip.open("lite.gz", "rb") as f_in, open("lite", "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.chmod("lite", 0o755)

    download_file("https://github.com/v2fly/geoip/releases/latest/download/geoip.dat", "geoip.dat")
    download_file("https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat", "geosite.dat")

    config = {
        "group": "auto",
        "speedtestMode": "pingonly",
        "pingMethod": "googleping",      # реальный тест
        "sortMethod": "ping",
        "concurrency": CONCURRENCY_REAL,
        "testMode": 2,
        "timeout": 9000,
        "language": "en",
        "unique": True,
        "outputMode": 4
    }
    with open("config.json", "w") as f:
        json.dump(config, f)

    print(f"[*] Реальный тест (googleping), concurrency={CONCURRENCY_REAL}...")
    print("    Жди 3–6 минут...\n")

    try:
        proc = subprocess.run(
            ["./lite", "--config", "config.json", "--test", "raw_nodes.txt"],
            timeout=REAL_TEST_TIMEOUT,
            capture_output=True,
            text=True
        )
        # Показываем хвост лога
        if proc.stdout:
            print("--- последние строки stdout ---")
            print("\n".join(proc.stdout.strip().splitlines()[-15:]))
        if proc.stderr:
            print("--- stderr (хвост) ---")
            print(proc.stderr[-1500:])
    except subprocess.TimeoutExpired:
        print(f"[!] Реальный тест превысил {REAL_TEST_TIMEOUT}s, принудительно остановлен")
    except Exception as e:
        print(f"[!] Ошибка lite: {e}")

    # ---------- 4. Парсим результат реального теста ----------
    real_working = []
    for f in glob.glob("*.txt"):
        if f in ("raw_nodes.txt", "sub.txt"):
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("vless://"):
                        real_working.append(line)
        except Exception:
            pass

    # Убираем дубли
    real_working = list(dict.fromkeys(real_working))
    print(f"\n[+] Прошли РЕАЛЬНЫЙ тест (googleping): {len(real_working)}")

    # ---------- 5. Формируем итоговый sub.txt ----------
    if real_working:
        final = real_working[:TOP_N]
        source = "real googleping"
    else:
        # Фоллбэк — лучшие по TCP
        final = [link for link, _ in tcp_alive[:TOP_N]]
        source = "TCP fallback (реальных не нашлось)"

    with open("sub.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(final) + "\n")

    print(f"[✓] sub.txt сохранён ({len(final)} серверов) — источник: {source}")

    if final:
        print("\nПримеры:")
        for i, node in enumerate(final[:3], 1):
            print(f"  {i}. {node[:100]}...")


if __name__ == "__main__":
    main()
