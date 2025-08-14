import os
import requests
from datetime import datetime, timedelta

# URL base da TheNewsAPI
THENEWS_API_URL = "https://api.thenewsapi.com/v1/news/all"

def get_recent_news(symbol):
    """Busca notícias recentes relacionadas a um símbolo usando TheNewsAPI."""
    
    api_key = os.getenv("THENEWS_API_KEY")
    
    if not api_key:
        print("⚠️ Chave da TheNewsAPI não encontrada no .env. Pulando análise de notícias.")
        return []

    # Formata o termo de busca (ex: BTCUSDT -> Bitcoin OR BTC)
    currency_name = symbol.replace("USDT", "").lower()
    currency_code = symbol.replace("USDT", "")
    query = f"{currency_name} OR {currency_code}"

    # Filtra para as últimas 24h
    date_from = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        response = requests.get(
            THENEWS_API_URL,
            params={
                "api_token": api_key,
                "search": query,
                "language": "en",
                "published_on": date_from,
                "limit": 10
            }
        )
        response.raise_for_status()
        data = response.json()

        # Ajusta para formato padrão esperado pelo sentiment_analyzer
        articles = []
        for article in data.get("data", []):
            articles.append({
                "title": article.get("title"),
                "description": article.get("description"),
                "url": article.get("url"),
                "published_at": article.get("published_at")
            })

        return articles

    except requests.RequestException as e:
        print(f"❌ Erro ao buscar notícias na TheNewsAPI: {e}")
        return []
