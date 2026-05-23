from __future__ import annotations

import scrapy


class DemoQuotesSpider(scrapy.Spider):
    name = "demo_quotes"
    allowed_domains = ["quotes.toscrape.com"]
    start_urls = ["https://quotes.toscrape.com/"]

    def parse(self, response: scrapy.http.Response):
        for quote in response.css(".quote"):
            text = quote.css(".text::text").get(default="").strip()
            author = quote.css(".author::text").get(default="").strip()
            tags = [tag.strip() for tag in quote.css(".tags .tag::text").getall() if tag.strip()]
            yield {
                "title": f"Quote from {author}" if author else "Quote",
                "text": text,
                "author": author,
                "tags": tags,
                "source_url": response.url,
            }
