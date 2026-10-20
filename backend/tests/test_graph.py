"""Этап 3: граф платежей и связей, отраслевой профиль контрагентов, похожие компании, холодный старт."""
import pandas as pd

from app.data.graph import compute_graph_features, pagerank
from app.data.models import CompanyProfile
from app.data.peers import assign_peer_groups, peer_benchmark, save_peer_groups
from app.data.sensitivity import compute_exposures


def _companies():
    return pd.DataFrame({"SYNTHETIC_U": ["A", "B", "C", "D"], "MAINOKVED_CODE": ["46.90", "41.20", "41.20", "62.01"],
                         "REGION": ["Москва", "Москва", "Татарстан", "Москва"]})


def test_industry_mix_hhi_and_affiliates():
    pay = pd.DataFrame({"BENEFICIARY": ["A", "A", "A", "B"], "PAYER": ["B", "C", "D", "A"],
                        "PAY_AMT": [600.0, 300.0, 100.0, 50.0], "PAY_CNT": [1, 1, 1, 1]})
    links = pd.DataFrame({"SYNTHETIC_F": ["F1", "F1", "F2"], "SYNTHETIC_U": ["A", "D", "B"]})
    f = compute_graph_features(pay, _companies(), links)
    mix = {(m["direction"], m["okved2"]): m["share"] for m in f["A"]["industry_mix"]}
    assert mix[("in", "41")] == 0.9 and mix[("in", "62")] == 0.1  # стройка платит 90% поступлений
    assert abs(f["A"]["hhi_in"] - (0.6**2 + 0.3**2 + 0.1**2)) < 1e-6 and f["A"]["n_buyers"] == 3
    assert f["A"]["affiliates"] == ["D"] and "affiliates" not in f["B"]
    assert f["A"]["network_pct"] >= f["D"].get("network_pct", 0)


def test_pagerank_sums_to_one():
    pay = pd.DataFrame({"PAYER": ["x", "y", "z"], "BENEFICIARY": ["y", "z", "x"], "PAY_AMT": [1.0, 1.0, 1.0]})
    pr = pagerank(pay)
    assert abs(sum(pr.values()) - 1) < 1e-6 and max(pr.values()) - min(pr.values()) < 1e-6


def test_second_order_effect_from_buyers_industry():
    p = CompanyProfile(uid="T1", okved="71.12", okved_name="Инжиниринг", products=["CL_PRODUCT_RKO_MARK"],
                       industry_mix=[{"direction": "in", "okved2": "41", "name": "Строительство зданий", "share": 0.8}])
    exp = compute_exposures(p)
    assert any(r["basis"] == "graph:in:41" for r in exp["construction"].reasons)
    assert any(r["basis"] == "graph:in:41" for r in exp["key_rate"].reasons)  # стройка чувствительна к ставке


def test_peers_benchmark_and_cold_start():
    profiles = [CompanyProfile(uid=f"P{i}", okved="56.10", okved_name="Рестораны", revenue=5e7,
                               products=["CL_PRODUCT_RKO_MARK", "CL_PRODUCT_ZPP_MARK"] if i % 3 else ["CL_PRODUCT_RKO_MARK"])
                for i in range(30)]
    stats = assign_peer_groups(profiles)
    save_peer_groups(stats)
    newbie = profiles[0]  # только РКО, нет платежей → холодный старт
    assert newbie.peer_key.startswith("o4:56.10|") and newbie.peers_n == 30
    b = peer_benchmark(newbie)
    zpp = next(r for r in b["products"] if r["product"] == "CL_PRODUCT_ZPP_MARK")
    assert zpp["peers_share"] > 0.6 and not zpp["has"]
    exp = compute_exposures(newbie)
    assert any(r["basis"] == "peers:CL_PRODUCT_ZPP_MARK" for r in exp["labor_market"].reasons)
