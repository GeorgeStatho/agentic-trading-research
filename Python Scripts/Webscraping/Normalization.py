from article_extraction import ArticleExtractionResult, DEFAULT_USER_AGENT, extract_article, extract_from_response
from article_scraper import ArticleSpider, crawl_articles


if __name__ == "__main__":
    demo_urls = [
        "https://www.economist.com/",
        "https://www.bloomberg.com/",
        "https://www.reuters.com/markets/",
        "https://www.marketwatch.com/",
        "https://finance.yahoo.com/",
        "https://www.wsj.com/news/business",
        "https://www.ft.com/markets",
        "https://www.cnbc.com/finance/",
        "https://www.investing.com/",
        "https://www.fool.com/",
        "https://www.barrons.com/",
        "https://www.morningstar.com/",
        "https://www.thestreet.com/",
        "https://www.zacks.com/",
        "https://www.businessinsider.com/markets",
    ]
    print("Starting crawl...", demo_urls)

    items = crawl_articles(demo_urls)
    print("Items found:", len(items))

    print(items)
    for item in items:
        print(item["url"])
        print(item["title"])
        print(item["text"][:500])
        print(item["links"])
