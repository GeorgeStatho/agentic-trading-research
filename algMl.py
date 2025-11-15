"""
Pipeline for correlating news text with stock-price movements.

This script shows how to:
1. Load stock data stored as a JSON dictionary/list.
2. Parse article titles/descriptions saved in a text file.
3. Join both sources on symbol/date so each row contains price history + news.
4. Train a multi-output regression model that predicts (estimated price, confidence score).

Adapt the parsing logic to match your own JSON/text formats. The goal is to end up
with a pandas.DataFrame that contains numeric stock features (open/high/low/close/volume),
text features (news title+description) and target columns you want the model to learn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from sklearn.multioutput import MultiOutputRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# Paths can be tweaked or exposed via CLI arguments.
STOCK_JSON_PATH = Path("stock_history.json")
NEWS_TEXT_PATH = Path("article.txt")

# Numeric columns expected inside the JSON file.
PRICE_FEATURES = ["open", "high", "low", "close", "volume"]


def load_stock_json(path: Path) -> pd.DataFrame:
    """
    Load stock history from a JSON file.

    The JSON file can be either:
        1. A list of dictionaries, one per (symbol, date).
        2. A dictionary keyed by identifiers, where each value is a record dict.

    Required keys per record: symbol, date, open, high, low, close, volume.
    You can add any other fields, e.g. technical indicators or labels.
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing stock JSON file: {path}")

    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if isinstance(payload, dict):
        records = list(payload.values())
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError("Unsupported JSON format. Use dict or list of dicts.")

    stock_df = pd.DataFrame(records)
    if "date" not in stock_df.columns:
        raise ValueError("Stock JSON must include a 'date' field for alignment.")

    stock_df["date"] = pd.to_datetime(stock_df["date"], errors="coerce")
    if stock_df["date"].isna().any():
        raise ValueError("Some stock rows have invalid dates.")

    # Sort so later shift() operations respect chronological order per symbol.
    stock_df = stock_df.sort_values(["symbol", "date"]).reset_index(drop=True)

    # Generate supervised targets (next-day close + absolute change as confidence).
    stock_df["target_price"] = stock_df.groupby("symbol")["close"].shift(-1)
    stock_df["target_confidence"] = (
        stock_df["target_price"] - stock_df["close"]
    ).abs()

    return stock_df.dropna(subset=["target_price"])


def load_news_articles(path: Path) -> pd.DataFrame:
    """
    Parse titles/descriptions from article.txt.

    Format expected:
        SYMBOL: AAPL
        DATE: 2024-11-22
        TITLE: Example headline
        DESCRIPTION: Example description text

    Blocks are separated by blank lines. If SYMBOL/DATE are missing, the row is dropped,
    because we cannot join it against stock prices without them.
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing news text file: {path}")

    entries: List[dict] = []
    current = {"symbol": None, "date": None, "title": "", "description": ""}

    def flush_entry():
        if current["symbol"] and current["date"]:
            entries.append(current.copy())
        current.update({"symbol": None, "date": None, "title": "", "description": ""})

    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                flush_entry()
                continue

            if line.startswith("SYMBOL:"):
                current["symbol"] = line.split(":", 1)[1].strip().upper()
            elif line.startswith("DATE:"):
                current["date"] = line.split(":", 1)[1].strip()
            elif line.startswith("TITLE:"):
                current["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("DESCRIPTION:"):
                current["description"] = line.split(":", 1)[1].strip()
            else:
                # Treat any other line as part of the description.
                current["description"] = (
                    f"{current['description']} {line}".strip()
                )

    flush_entry()

    if not entries:
        raise ValueError(
            "No valid news blocks detected. Ensure each block has SYMBOL and DATE."
        )

    news_df = pd.DataFrame(entries)
    news_df["date"] = pd.to_datetime(news_df["date"], errors="coerce")
    news_df = news_df.dropna(subset=["date"])
    news_df["article_text"] = (
        news_df["title"].fillna("") + " " + news_df["description"].fillna("")
    )
    news_df = news_df[["symbol", "date", "article_text"]]
    return news_df


def prepare_dataset(stock_path: Path, news_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Merge stock + news sources so every row contains aligned inputs and targets."""
    stock_df = load_stock_json(stock_path)
    news_df = load_news_articles(news_path)

    full = pd.merge(
        stock_df,
        news_df,
        on=["symbol", "date"],
        how="inner",
    )

    if full.empty:
        raise ValueError(
            "Merge produced 0 rows. Confirm both sources share symbol/date identifiers."
        )

    feature_df = full[PRICE_FEATURES + ["article_text"]].copy()
    feature_df["article_text"] = feature_df["article_text"].fillna("")

    targets = full[["target_price", "target_confidence"]].copy()
    return feature_df, targets


def build_model() -> Pipeline:
    """Create a pipeline that vectorizes text + scales numeric inputs."""
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), PRICE_FEATURES),
            ("text", TfidfVectorizer(max_features=2000), "article_text"),
        ],
        remainder="drop",
    )

    regressor = MultiOutputRegressor(
        RandomForestRegressor(n_estimators=300, random_state=42, n_jobs=-1)
    )

    return Pipeline(
        steps=[
            ("preprocess", preprocessor),
            ("model", regressor),
        ]
    )


def train_and_evaluate(stock_path: Path, news_path: Path) -> None:
    """Train the model and report metrics."""
    features, targets = prepare_dataset(stock_path, news_path)

    X_train, X_test, y_train, y_test = train_test_split(
        features,
        targets,
        test_size=0.2,
        random_state=42,
        shuffle=True,
    )

    model = build_model()
    model.fit(X_train, y_train)

    predictions = model.predict(X_test)
    mae_price = mean_absolute_error(y_test["target_price"], predictions[:, 0])
    mae_conf = mean_absolute_error(y_test["target_confidence"], predictions[:, 1])

    print(f"Price MAE: {mae_price:.2f}")
    print(f"Confidence MAE: {mae_conf:.4f}")

    # Example: inspect one prediction.
    if len(X_test) > 0:
        sample_features = X_test.iloc[[0]]
        sample_pred = model.predict(sample_features)[0]
        print("Sample prediction:")
        print(
            f"  Estimated price: {sample_pred[0]:.2f} vs actual {y_test.iloc[0, 0]:.2f}"
        )
        print(
            f"  Confidence: {sample_pred[1]:.4f} vs actual {y_test.iloc[0, 1]:.4f}"
        )


if __name__ == "__main__":
    # Run `train_and_evaluate` once data files are available.
    # Adjust STOCK_JSON_PATH/NEWS_TEXT_PATH to point to your dataset exports.
    train_and_evaluate(STOCK_JSON_PATH, NEWS_TEXT_PATH)
