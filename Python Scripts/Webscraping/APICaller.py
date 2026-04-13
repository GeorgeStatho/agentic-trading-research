from foolAPI import get_stock_data

data = get_stock_data("AAPL", since="2026-01-01",limit=10)
print(data)