from .demo import (
    extract_demo_article as extract_barrons_article,
    extract_demo_search_links as extract_barrons_search_links,
    is_demo_article_url as is_barrons_article_url,
    is_demo_url as is_barrons_url,
    response_looks_like_demo_search as response_looks_like_barrons_search,
)


def barrons_response_is_blocked(*args, **kwargs) -> bool:
    del args
    del kwargs
    return False
