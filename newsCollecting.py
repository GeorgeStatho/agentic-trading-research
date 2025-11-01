from newsapi import NewsApiClient
from Keys import NEWS_API_KEY
import requests

newsapiClient=NewsApiClient(NEWS_API_KEY)

newsapiClient.get_top_headlines(sources='forbes')
print(newsapiClient.)