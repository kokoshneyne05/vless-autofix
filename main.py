#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автоматический сборщик + реальный тестер публичных VLESS.
Работает стабильно на GitHub Actions.
"""

import urllib.request
import re
import os
import json
import gzip
import shutil
import subprocess
import glob
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===================== НАСТРОЙКИ =====================
SOURCES = [
    "https://github.com/kort0881/vpn-vless-configs-russia/raw/refs/heads/main/output/vless.txt",
    # Можешь добавить ещё источники сюда (по одному на строку)
    # "https://raw.githubusercontent.com/other/repo/main/vless.txt",
]

MAX_NODES_TO_TEST = 300          # Не тестируем больше 300 (иначе GHA таймаутится)
CONCURRENCY = 8                  # Важно! Не ставь больше 10-12
TEST_TIMEOUT_SECONDS = 420       # Максимум 7 минут на весь тест
TOP_N = 20                       # Сколько лучших оставляем
# =====================================================


def get_nodes(url: str) -> list[str]:
    """Скачивает и очищает VLESS-ссылки из источника"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[!] Ошибка загрузки {url}: {e}")
        return []

    # Чистим типичный мусор
    content = content.replace("&amp;", "&")
    content = content.replace("%26", "&")

    links = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # Берём только саму ссылку (до | или пробела с мусором)
        match = re.search(r"(vless://[^\s|]+)", line)
        if match:
            link = match.group(1).rstrip(".,;")
            # Убираем возможные обрезки в конце
            if link.count("@") == 1 and "://" in link:
                links.append(link)

    return links


def download_file(url: str, filename: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(filename, "wb") as f:
            shutil.copyfileobj(r, f)
        return True
    except Exception as e:
        print(f"[!] Не удалось скачать {url}: {e}")
        return False


def main():
    print("=" * 60)
    print("VLESS Auto Collector + Real Tester")
    print("=" * 60)

    # ---------- 1. Сбор ----------
    all_links = set()
    for src in SOURCES:
        print(f"[*] Читаю источник: {src}")
        nodes = get_nodes(src)
        print(f"    → найдено {len(nodes)} ссылок")
        all_links.update(nodes)

    all_links = list(all_links)
    print(f"\n[+] Всего уникальных узлов: {len(all_links)}")

    if not all_links:
        print("[!] Нет ни одной ссылки. Создаю пустой sub.txt")
        open("sub.txt", "w").close()
        return

    # Ограничиваем количество (иначе тест будет слишком долгим)
    if len(all_links) > MAX_NODES_TO_TEST:
        print(f"[*] Слишком много узлов ({len(all_links)}). Берём первые {MAX_NODES_TO_TEST}")
        all_links = all_links[:MAX_NODES_TO_TEST]

    with open("raw_nodes.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(all_links))

    # ---------- 2. Скачиваем LiteSpeedTest ----------
    print("\n[*] Скачиваю LiteSpeedTest...")
    bin_url = "https://github.com/xxf098/LiteSpeedTest/releases/download/v0.15.0/lite-linux-amd64-v0.15.0.gz"
    if not download_file(bin_url, "lite.gz"):
        print("[!] Не удалось скачать бинарник. Выход.")
        open("sub.txt", "w").close()
        return

    with gzip.open("lite.gz", "rb") as f_in, open("lite", "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.chmod("lite", 0o755)

    # geoip/geosite (на всякий случай)
    download_file("https://github.com/v2fly/geoip/releases/latest/download/geoip.dat", "geoip.dat")
    download_file("https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat", "geosite.dat")

    # ---------- 3. Конфиг для реального теста ----------
    config = {
        "group": "auto",
        "speedtestMode": "pingonly",      # только пинг (быстро)
        "pingMethod": "googleping",       # реальный HTTP через прокси (не tcping!)
        "sortMethod": "ping",             # сортировка по пингу
        "concurrency": CONCURRENCY,
        "testMode": 2,
        "timeout": 8000,                  # 8 сек на один узел
        "language": "en",
        "fontSize": 24,
        "theme": "rainbow",
        "unique": True,
        "generatePicMode": 0,
        "outputMode": 4                   # 4 = txt с рабочими ссылками
    }

    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    print(f"[*] Запускаю тест (concurrency={CONCURRENCY}, timeout={TEST_TIMEOUT_SECONDS}s)...")
    print("    Это может занять 3–7 минут...\n")

    # ---------- 4. Запуск с таймаутом ----------
    try:
        result = subprocess.run(
            ["./lite", "--config", "config.json", "--test", "raw_nodes.txt"],
            timeout=TEST_TIMEOUT_SECONDS,
            capture_output=True,
            text=True
        )
        # Показываем важные куски лога
        if result.stdout:
            lines = result.stdout.strip().splitlines()
            print("--- STDOUT (последние 30 строк) ---")
            for line in lines[-30:]:
                print(line)
        if result.stderr:
            print("--- STDERR ---")
            print(result.stderr[-2000:])
    except subprocess.TimeoutExpired:
        print(f"[!] Тест превысил {TEST_TIMEOUT_SECONDS} секунд и был принудительно остановлен")
    except Exception as e:
        print(f"[!] Ошибка запуска lite: {e}")

    # ---------- 5. Ищем результат ----------
    # LiteSpeedTest создаёт файлы вида out-*.txt или просто *.txt
    candidates = []
    for f in glob.glob("*.txt"):
        if f in ("raw_nodes.txt", "sub.txt"):
            continue
        candidates.append(f)

    working_nodes = []

    if candidates:
        # Берём самый свежий
        latest = max(candidates, key=os.path.getmtime)
        print(f"\n[*] Найден отчёт: {latest}")
        with open(latest, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("vless://"):
                    working_nodes.append(line)
    else:
        print("\n[!] Файл отчёта не найден. Возможно все узлы отвалились или lite упал.")

    print(f"\n[+] Рабочих узлов после реального теста: {len(working_nodes)}")

    # ---------- 6. Сохраняем топ ----------
    top = working_nodes[:TOP_N]

    with open("sub.txt", "w", encoding="utf-8") as f:
        if top:
            f.write("\n".join(top) + "\n")
            print(f"[✓] sub.txt обновлён — сохранено {len(top)} лучших серверов")
        else:
            # Даже если 0 — создаём пустой файл, чтобы workflow не падал
            f.write("")
            print("[!] Рабочих серверов нет. sub.txt создан пустым.")

    # Для отладки показываем первые несколько
    if top:
        print("\n--- Топ серверов ---")
        for i, node in enumerate(top[:5], 1):
            print(f"{i}. {node[:90]}...")


if __name__ == "__main__":
    main()
