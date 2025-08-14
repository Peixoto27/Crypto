from textblob import TextBlob
from news_fetcher import get_recent_news

def analyze_sentiment(symbol):
    """Analisa o sentimento das últimas notícias de um símbolo."""
    
    news_list = get_recent_news(symbol)
    
    if not news_list:
        print(f"⚠️ Sem notícias recentes para {symbol}.")
        return 0, []

    sentiment_scores = []
    analyzed_articles = []

    for article in news_list:
        text_to_analyze = f"{article['title']} {article['description'] or ''}"
        sentiment = TextBlob(text_to_analyze).sentiment.polarity
        sentiment_scores.append(sentiment)

        analyzed_articles.append({
            "title": article["title"],
            "url": article["url"],
            "sentiment": sentiment
        })

    # Média de sentimento
    avg_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0
    
    return avg_sentiment, analyzed_articles
