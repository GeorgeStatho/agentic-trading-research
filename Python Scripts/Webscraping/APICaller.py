from foolAPI import get_stock_data


if __name__ == "__main__":
    data = get_stock_data("DEMO", since="2026-01-01", limit=3)
    print(data)
