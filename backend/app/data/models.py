from __future__ import annotations

from pydantic import BaseModel, Field


class Counterparty(BaseModel):
    uid: str
    direction: str  # "in" — платит компании (покупатель), "out" — получает от компании (поставщик)
    amount: float
    count: int
    share: float  # доля в общем входящем/исходящем потоке компании
    okved: str | None = None
    okved_name: str | None = None


class CompanyProfile(BaseModel):
    uid: str
    okved: str
    okved_name: str
    okfs: str | None = None
    age: str | None = None
    lifecycle_stage: str | None = None  # ECO_MARK_LABEL
    key_owner_mark: bool = False  # NEGATIVEMARK — «наличие признака ключевого собственника»
    workers: str | None = None
    client_type: str | None = None
    products: list[str] = Field(default_factory=list)  # ключи из sensitivity.products
    fts: bool = False
    fts_countries: list[str] = Field(default_factory=list)
    revenue: float | None = None
    revenue_suspicious: bool = False
    group_id: str | None = None
    group_size: int = 1
    group_revenue_share: float | None = None
    okved_peers: int = 0
    okved_revenue_share: float | None = None  # «доля рынка» — оценка по выборке клиентов
    okved_revenue_rank: int | None = None
    city: str | None = None
    region: str | None = None
    counterparties: list[Counterparty] = Field(default_factory=list)
    top_buyer_share: float | None = None
    top_supplier_share: float | None = None
    data_gaps: list[str] = Field(default_factory=list)

    def display_name(self) -> str:
        """Данные обезличены — показываем отрасль + город + id."""
        return f"{self.okved_name[:60]} · {self.city or self.region or ''} · {self.uid}"

    def llm_view(self) -> dict:
        """Минимизированное представление для LLM: без идентификаторов контрагентов и группы."""
        return {
            "отрасль": f"{self.okved} {self.okved_name}",
            "масштаб_выручки_руб": self.revenue if not self.revenue_suspicious else None,
            "численность": self.workers,
            "возраст_лет": self.age,
            "стадия": self.lifecycle_stage,
            "регион": self.region,
            "продукты_банка": self.products,
            "вэд": {"есть": self.fts, "страны": self.fts_countries},
            "доля_в_отрасли_по_выборке": self.okved_revenue_share,
            "доля_в_группе": self.group_revenue_share,
            "ключевые_контрагенты": [
                {"роль": "покупатель" if c.direction == "in" else "поставщик", "отрасль": c.okved_name, "доля_потока": round(c.share, 3)}
                for c in self.counterparties[:6]
            ],
            "пробелы_в_данных": self.data_gaps,
        }
