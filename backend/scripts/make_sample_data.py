"""Генерирует синтетический набор данных в схеме кейса (data/sample/*.csv).

Нужен, чтобы прототип и тесты запускались без исходных файлов организаторов.
Запуск: uv run python scripts/make_sample_data.py
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "sample"
random.seed(42)

OKVEDS = [
    ("41.20", "Строительство жилых и нежилых зданий"),
    ("46.90", "Торговля оптовая неспециализированная"),
    ("46.51", "Торговля оптовая компьютерами, периферийными устройствами к компьютерам и программным обеспечением"),
    ("62.01", "Разработка компьютерного программного обеспечения"),
    ("56.10", "Деятельность ресторанов и услуги по доставке продуктов питания"),
    ("68.20", "Аренда и управление собственным или арендованным недвижимым имуществом"),
    ("49.41", "Деятельность автомобильного грузового транспорта"),
    ("47.11", "Торговля розничная преимущественно пищевыми продуктами"),
    ("45.11", "Торговля легковыми автомобилями и грузовыми автомобилями малой грузоподъемности"),
    ("25.11", "Производство строительных металлических конструкций"),
    ("10.71", "Производство хлеба и мучных кондитерских изделий"),
    ("71.12", "Деятельность в области инженерных изысканий, инженерно-технического проектирования"),
]
CITIES = [("г. Москва", "Москва"), ("г. Санкт-Петербург", "Санкт-Петербург"), ("г. Новосибирск", "Новосибирская область"),
          ("г. Екатеринбург", "Свердловская область"), ("г. Казань", "Республика Татарстан")]
COUNTRIES = ["КИТАЙ", "ТУРЦИЯ", "КИТАЙ, ТУРЦИЯ", "ОАЭ, ИНДИЯ, КИТАЙ", "УЗБЕКИСТАН", "КИТАЙ, ГОНКОНГ"]
STAGES = ["Оптимизация", "Бизнес в новой реальности", "Рождение", "Стремительный рост", ""]
N = 400


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["SYNTHETIC_U", "ORG_TYPE", "OKFS_NAME", "COMPANY_AGE_YEARS_GRADATION", "ECO_MARK_LABEL", "MAINOKVED_CODE",
            "MAINOKVED_NAME", "NEGATIVEMARK", "AMOUNT_WORKERS_FNS_GRADATION", "AMOUNT_WORKERS_SPARK_GRADATION", "CL_TYPE",
            "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK", "CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK",
            "CL_PRODUCT_ZPP_MARK", "FTS_FLAG", "FTS_COUNTRY_NAME", "FACTORING_MARK", "REVENUE_COMPANY", "GROUP_ID", "CITY", "REGION"]
    uids = [f"U_S{i:04d}" for i in range(N)]
    rows = []
    for i, uid in enumerate(uids):
        code, name = OKVEDS[i % len(OKVEDS)]
        city, region = random.choice(CITIES)
        trade = code.startswith(("46", "45")) and random.random() < 0.7
        p = lambda prob: "1.0" if random.random() < prob else "0.0"  # noqa: E731
        rows.append({
            "SYNTHETIC_U": uid, "ORG_TYPE": "КОММ_ОРГ", "OKFS_NAME": "ЧАСТНАЯ СОБСТВЕННОСТЬ",
            "COMPANY_AGE_YEARS_GRADATION": random.choice(["0-3", "4-7", "8-15", "16-25"]),
            "ECO_MARK_LABEL": random.choice(STAGES), "MAINOKVED_CODE": code, "MAINOKVED_NAME": name,
            "NEGATIVEMARK": p(0.1), "AMOUNT_WORKERS_FNS_GRADATION": random.choice(["Менее 15", "16-100", "101-250", "Более 251"]),
            "AMOUNT_WORKERS_SPARK_GRADATION": "", "CL_TYPE": random.choice(["CLIENT", "CLIENT", "PROSPECT", "CLOSE_CLIENT"]),
            "CL_PRODUCT_RKO_MARK": p(0.6), "CL_PRODUCT_GAR_MARK": p(0.15 if code.startswith(("41", "71")) else 0.03),
            "CL_PRODUCT_LOA_MARK": p(0.5 if code.startswith("41") else 0.15), "CL_PRODUCT_LEA_MARK": p(0.6 if code.startswith("49") else 0.15),
            "CL_PRODUCT_DEP_MARK": p(0.3 if code.startswith("68") else 0.08), "CL_PRODUCT_ZPP_MARK": p(0.6 if code.startswith("62") else 0.3),
            "FTS_FLAG": "1.0" if trade else "0.0", "FTS_COUNTRY_NAME": random.choice(COUNTRIES) if trade else "",
            "FACTORING_MARK": p(0.05), "REVENUE_COMPANY": str(round(10 ** random.uniform(6, 10.5))),
            "GROUP_ID": f"group_S{i // 3}", "CITY": city, "REGION": region,
        })
    with open(OUT / "df_Uid_data_hack.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
        w.writeheader()
        w.writerows(rows)

    with open(OUT / "df_Uid_payment_hack.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"])
        for uid in uids:
            for _ in range(random.randint(1, 6)):
                other = random.choice(uids)
                if other != uid:
                    w.writerow([uid, other, float(round(10 ** random.uniform(4, 8))), random.randint(1, 200)])

    with open(OUT / "df_Fid_Uid_link_hack.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"])
        for i, uid in enumerate(uids):
            fid = f"F_S{i // 2:04d}"
            w.writerow([fid, rows[i]["CITY"].replace("г. ", ""), "ДЕМО", uid])
    print(f"Сгенерировано: {N} компаний -> {OUT}")


if __name__ == "__main__":
    main()
