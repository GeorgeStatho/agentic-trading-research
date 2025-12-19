import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ARTICLES_PATH = Path("article.txt")
STOCK_DATA_PATH = Path("stockData.json")
VOCAB_SIZE = 2000
BATCH_SIZE = 4
EPOCHS = 5
LEARNING_RATE = 1e-3
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ---------------------------------------------------------------------------
# Data loading and preprocessing
# ---------------------------------------------------------------------------
@dataclass
class ArticleEntry:
    symbol: str
    title: str
    text: str
    url: str


@dataclass
class Prediction:
    symbol: str
    predicted_price: float
    confidence: float
    actual_price: float
    title: str


def _tokenize(text: str) -> List[str]:
    """
    Extremely small tokenizer that lowercases and keeps alphabetic tokens only.
    """
    return re.findall(r"[a-zA-Z']+", text.lower())


def _parse_articles(path: Path) -> List[ArticleEntry]:
    """
    Converts the semi-structured article.txt file into ArticleEntry objects.
    """
    entries: List[ArticleEntry] = []
    if not path.exists():
        return entries

    current: Dict[str, str] = {}
    capturing_body = False
    body_lines: List[str] = []

    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if line.startswith("SYMBOL:"):
                if current:
                    current["text"] = "\n".join(body_lines).strip()
                    entries.append(
                        ArticleEntry(
                            symbol=current.get("symbol", ""),
                            title=current.get("title", ""),
                            text=current.get("text", ""),
                            url=current.get("url", ""),
                        )
                    )
                current = {"symbol": line.split(":", 1)[1].strip()}
                capturing_body = False
                body_lines = []
            elif line.startswith("TITLE:"):
                current["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("URL:"):
                # urls in article.txt sometimes include quotes and spaces
                current["url"] = line.split(":", 1)[1].strip().strip("'\" ")
            elif line.startswith("DATE:"):
                current["date"] = line.split(":", 1)[1].strip()
            elif line.startswith("FULL TEXT:"):
                capturing_body = True
                body_lines = []
            else:
                if capturing_body and current:
                    body_lines.append(line)

        # append final article
        if current:
            current["text"] = "\n".join(body_lines).strip()
            entries.append(
                ArticleEntry(
                    symbol=current.get("symbol", ""),
                    title=current.get("title", ""),
                    text=current.get("text", "") or current.get("title", ""),
                    url=current.get("url", ""),
                )
            )
    return [entry for entry in entries if entry.symbol]


def _load_stock_prices(path: Path) -> Dict[str, float]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class ArticleDataset(Dataset):
    """
    Prepares bag-of-words features from article text and aligns
    them with stock prices from stockData.json.
    """

    def __init__(self, articles: List[ArticleEntry], stock_prices: Dict[str, float], vocab_size: int):
        # filter out entries without a matching stock price
        self.entries = [a for a in articles if a.symbol in stock_prices]
        if not self.entries:
            raise ValueError("No article entries have a matching stock price. Populate stockData.json first.")

        self.stock_prices = stock_prices
        self.vocab, self.idf = self._build_vocab(self.entries, vocab_size)
        self.feature_dim = len(self.vocab)
        self.max_price = max(stock_prices.values()) if stock_prices else 1.0

        self.features: List[torch.Tensor] = []
        self.targets: List[torch.Tensor] = []

        for entry in self.entries:
            vec = self._vectorize(entry.text)
            price = float(self.stock_prices[entry.symbol])
            confidence_target = price / self.max_price if self.max_price else 0.0
            target = torch.tensor([price, confidence_target], dtype=torch.float32)
            self.features.append(vec)
            self.targets.append(target)

    def _build_vocab(
        self, entries: List[ArticleEntry], vocab_size: int
    ) -> Tuple[Dict[str, int], Dict[str, float]]:
        token_counts: Counter[str] = Counter()
        doc_freq: Counter[str] = Counter()
        for entry in entries:
            tokens = _tokenize(entry.text + " " + entry.title)
            token_counts.update(tokens)
            doc_freq.update(set(tokens))

        most_common = token_counts.most_common(vocab_size)
        vocab = {token: idx for idx, (token, _) in enumerate(most_common)}
        total_docs = max(len(entries), 1)
        idf = {token: math.log((total_docs + 1) / (doc_freq.get(token, 1) + 1)) + 1.0 for token in vocab}
        return vocab, idf

    def _vectorize(self, text: str) -> torch.Tensor:
        tokens = _tokenize(text)
        vec = torch.zeros(self.feature_dim, dtype=torch.float32)
        if not tokens:
            return vec
        token_counts: Counter[str] = Counter(tokens)
        for token, count in token_counts.items():
            if token in self.vocab:
                tf = count / len(tokens)
                vec[self.vocab[token]] = tf * self.idf.get(token, 1.0)
        return vec

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, idx: int):
        return self.features[idx], self.targets[idx], self.entries[idx].symbol


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class ArticleValueModel(nn.Module):
    """
    Simple feed-forward network that predicts the future value of a stock
    and a confidence score (0..1) from article bag-of-words features.
    """

    def __init__(self, input_dim: int, hidden_dim: int = 512):
        super().__init__()
        half_hidden = max(hidden_dim // 2, 32)
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_dim, half_hidden),
            nn.ReLU(),
        )
        self.value_head = nn.Linear(half_hidden, 1)
        self.confidence_head = nn.Linear(half_hidden, 1)

    def forward(self, features: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        encoded = self.encoder(features)
        value = self.value_head(encoded).squeeze(1)
        confidence = torch.sigmoid(self.confidence_head(encoded)).squeeze(1)
        return value, confidence


# ---------------------------------------------------------------------------
# Training / evaluation helpers
# ---------------------------------------------------------------------------
def train_model(model: nn.Module, loader: DataLoader, epochs: int, device: torch.device) -> None:
    value_loss_fn = nn.MSELoss()
    confidence_loss_fn = nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    model.to(device)
    for epoch in range(1, epochs + 1):
        running_loss = 0.0
        for features, targets, _ in loader:
            features = features.to(device)
            targets = targets.to(device)

            optimizer.zero_grad()
            value_pred, conf_pred = model(features)
            value_loss = value_loss_fn(value_pred, targets[:, 0])
            confidence_loss = confidence_loss_fn(conf_pred, targets[:, 1])
            loss = value_loss + confidence_loss
            loss.backward()
            optimizer.step()
            running_loss += loss.item() * features.size(0)

        epoch_loss = running_loss / len(loader.dataset)
        print(f"Epoch {epoch:02d} | loss={epoch_loss:.4f}")


def generate_predictions(model: nn.Module, dataset: ArticleDataset) -> List[Prediction]:
    model.eval()
    predictions: List[Prediction] = []
    with torch.no_grad():
        features = torch.stack(dataset.features).to(DEVICE)
        value_pred, conf_pred = model(features)
        value_cpu = value_pred.cpu()
        conf_cpu = conf_pred.cpu()

        for entry, price, conf in zip(dataset.entries, value_cpu, conf_cpu):
            predictions.append(
                Prediction(
                    symbol=entry.symbol,
                    predicted_price=float(price.item()),
                    confidence=float(conf.item()),
                    actual_price=float(dataset.stock_prices[entry.symbol]),
                    title=entry.title,
                )
            )
    return predictions


def _print_predictions(predictions: List[Prediction], top_k: int = 5) -> None:
    for item in predictions:
        print(f"{item.symbol:>6} | predicted=${item.predicted_price:.2f} | confidence={item.confidence:.2%}")

    print("\nTop predictions by confidence:")
    sorted_items = sorted(predictions, key=lambda pred: pred.confidence, reverse=True)[:top_k]
    for pred in sorted_items:
        print(f"  {pred.symbol:>6} -> conf {pred.confidence:.2%}, article='{pred.title[:60]}'")


def run_inference(model: nn.Module, dataset: ArticleDataset, top_k: int = 5) -> List[Prediction]:
    """
    Runs the model on the dataset, prints a few sample predictions, and returns them.
    """
    predictions = generate_predictions(model, dataset)
    _print_predictions(predictions, top_k)
    return predictions


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def train_and_predict(epochs: int = EPOCHS, print_summary: bool = True) -> List[Prediction]:
    print(f"Using device: {DEVICE}")
    articles = _parse_articles(ARTICLES_PATH)
    if not articles:
        raise FileNotFoundError(f"No articles found in {ARTICLES_PATH}. Run newsCollecting.py first.")

    stock_prices = _load_stock_prices(STOCK_DATA_PATH)
    if not stock_prices:
        raise FileNotFoundError(f"No stock data found in {STOCK_DATA_PATH}.")

    dataset = ArticleDataset(articles, stock_prices, VOCAB_SIZE)
    loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True)

    model = ArticleValueModel(input_dim=dataset.feature_dim)
    train_model(model, loader, epochs, DEVICE)
    predictions = generate_predictions(model, dataset)
    if print_summary:
        _print_predictions(predictions)
    return predictions


def main():
    predictions = train_and_predict()
    return predictions


if __name__ == "__main__":
    main()
