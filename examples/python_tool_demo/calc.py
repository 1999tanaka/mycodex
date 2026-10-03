"""見積もり計算ツール (デモ用)。"""

TAX_RATE = 0.08  # 消費税率


def price_with_tax(price: int) -> int:
    """税込み価格 (円未満切り捨て)。"""
    return int(price * (1 + TAX_RATE))


def total_with_tax(prices: list[int]) -> int:
    """税込み合計。"""
    return sum(price_with_tax(p) for p in prices)


if __name__ == "__main__":
    print(total_with_tax([1000, 500]))
