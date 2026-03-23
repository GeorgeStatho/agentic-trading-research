# newsCollecting.py (NewsMesh version)

import json
import time
import requests
from Keys import NEWS_API_KEY  # <-- put your NewsMesh API key in this constant
from newspaper import Article
BASE_URL = "https://api.newsmesh.co/v1"
API_KEY = NEWS_API_KEY  # rename in Keys.py if you want, but not required


def jsonToPy():
    with open("symbols.json", "r", encoding="utf-8") as symbols:
        return json.load(symbols)


def _get(endpoint: str, params: dict, timeout_s: int = 20) -> dict:
    """
    NewsMesh requires apiKey as a query parameter on all endpoints.
    """
    url = f"{BASE_URL}{endpoint}"
    params = dict(params or {})
    params["apiKey"] = API_KEY

    r = requests.get(url, params=params, timeout=timeout_s)
    # NewsMesh returns JSON errors with HTTP codes (401/429/etc), so raise for status
    r.raise_for_status()
    return r.json()


def search(keyword: str, limit: int = 1):
    """
    Currents: search(language="en", keywords=..., category="business")
    NewsMesh: /v1/search?apiKey=...&q=...
      - No language param in docs
      - No category filter param in /search in docs, so we filter locally by category=="business"
    """
    limit = max(1, min(int(limit), 25))  # NewsMesh max is 25
    payload = _get("/search", {"q": keyword, "limit": limit})
    articles = payload.get("data", []) or []
    # mimic your old behavior: business only
    return [a for a in articles if a.get("category") == "business"]

def fetch_full_text(url: str) -> str:
    article = Article(url)
    article.download()
    article.parse()
    return article.text


#Write To TextFile
def writeArticle(articles: list[dict], symbol: str):
    with open("savedArticlesUrls.json","r+",encoding="utf-8") as saved:
        urls:set=set(json.load(saved))
        with open("article.txt", "a", encoding="utf-8") as out:
            for article in articles:
                url = article.get("link", "")
                if not (url in urls):
                    urls.add(url)
                    full_text = ""

                    if url:
                        try:
                            full_text = fetch_full_text(url)
                        except Exception as e:
                            full_text = f"[Could not fetch full text: {e}]"

                    out.write(f"SYMBOL: {symbol}\n")
                    out.write(f"URL: ' {article.get('link','')}\n")
                    out.write(f"TITLE: {article.get('title','')}\n")
                    out.write(f"DATE: {article.get('published_date','')}\n")
                    out.write("FULL TEXT:\n")
                    out.write(full_text + "\n\n")
            saved.seek(0)
            json.dump(list(urls),saved,indent=4)
            saved.truncate()




def getLatestTop100(symbols: dict):
    for symbol in symbols:
        try:
            # your JSON appears to map ticker -> company name; keep that behavior
            keyword = symbols[symbol]
            articles = search(keyword, 25)  
            writeArticle(articles, symbol)
            time.sleep(2)  # be polite about rate limits
        except requests.HTTPError as e:
            print(f"[HTTP ERROR] {symbol}: {e}")
        except requests.RequestException as e:
            print(f"[NETWORK ERROR] {symbol}: {e}")
        except Exception as e:
            print(f"[ERROR] {symbol}: {e}")


if __name__ == "__main__":
    print(API_KEY)
    symbols = jsonToPy()
    getLatestTop100(symbols)
