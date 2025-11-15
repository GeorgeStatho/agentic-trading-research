from currentsapi import CurrentsAPI
import json
from Keys import NEWS_API_KEY


print(NEWS_API_KEY)
newsapiClient=CurrentsAPI(NEWS_API_KEY)
print(newsapiClient.api_key)

def jsonToPy():
    with open("symbols.json","r") as symbols:
        dic=json.load(symbols)
    return dic

def search(keyword:str,limit:int):
    response=newsapiClient.search(language="en",
                                keywords=keyword,
                                category="buisness",
                                limit=limit)
    return response

def writeArticle(response:dict,symbol:str):
    with open ( 'article.txt','a',encoding="utf-8") as articles:
        for article in response["news"]:
            print("TITLE:  "+article["title"])
            articles.write("SYMBOL: "+ symbol+"\n")
            articles.write("DATE: " + article["published"]+"\n\t")
            articles.write("TITLE: "+ article["title"]+"\n\t")
            articles.writelines("DESCRIPTION: "+ article["description"]+"\n")
    
def getLatestTop100(symbols:dict):
    for symbol in symbols:
        response=search(symbols[symbol],1)
        writeArticle(response,symbol)

symbols=jsonToPy()
getLatestTop100(symbols)

