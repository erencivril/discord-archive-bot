# OpenRouter API client module
import os
import requests
import json
from dotenv import load_dotenv
from threading import Lock

load_dotenv()

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "microsoft/mai-ds-r1:free" # Default if not set in .env
CHAT_MODEL = os.getenv('OPENROUTER_CHAT_MODEL', DEFAULT_MODEL)
TEMPERATURE = float(os.getenv('OPENROUTER_TEMPERATURE', 0.4))

YOUR_SITE_URL = os.getenv('YOUR_SITE_URL', 'http://localhost:8000')
YOUR_APP_NAME = os.getenv('YOUR_APP_NAME', 'DiscordBot')

def get_ai_response(user_prompt, conversation_history=None, context_messages=None, model_override=None, system_prompt_override=None):
    """
    Sends a prompt to the configured OpenRouter model and returns the response.

    Args:
        user_prompt (str): The latest message from the user.
        conversation_history (list, optional): List of previous messages in the current chat
                                               (formatted as {'role': 'user'/'assistant', 'content': '...'})
        context_messages (str, optional): A string containing recent archived messages
                                          to provide context for tone and style.
        system_prompt_override (str, optional): If provided, use this as the system prompt instead of the default.

    Returns:
        str: The AI's response, or None if an error occurred.
    """
    if not OPENROUTER_API_KEY:
        print("Error: OPENROUTER_API_KEY not found in .env file.")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": YOUR_SITE_URL,
        "X-Title": YOUR_APP_NAME,
        "Content-Type": "application/json"
    }

    messages = []

    # System Prompt
    if system_prompt_override:
        system_prompt = system_prompt_override
    else:
        system_prompt = "### Sistem\nSen bir discord botusun. Aşağıdaki kurallara uy:\n Yardımcı, nazik ve saygılı ol.  \n Kullanıcının ihtiyaçlarını anlamaya çalış, açık ve anlaşılır yanıtlar ver.  \n Teknik açıklamalar gerektiğinde örnek kod ve madde işaretleri kullan.  \n Mümkün olduğunca kısa ve özlü cevaplar üret.  \n Teknik terimleri İngilizce bırakabilirsin."
        if context_messages:
            system_prompt += "\n\nÖrnek mesajlar (stilini kopyala):\n" + context_messages
    messages.append({"role": "system", "content": system_prompt})

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": user_prompt})

    # Determine which model to use
    model_to_use = model_override if model_override else CHAT_MODEL

    data = {
        "model": model_to_use,
        "messages": messages,
        "temperature": TEMPERATURE,
        # "max_tokens": 250,
    }

    try:
        response = requests.post(
            f"{OPENROUTER_API_BASE}/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        response.raise_for_status()

        result = response.json()
        print(f"[DEBUG] OpenRouter raw response: {result}")  # Added debug log for raw response
        ai_message = result['choices'][0]['message']['content'].strip()

        # Trim to max 3 sentences
        def trim_to_sentences(text, max_sentences=50):
            import re
            # Split by sentence-ending punctuation
            sentences = re.split(r'(?<=[.!?])\s+', text)
            trimmed = ' '.join(sentences[:max_sentences]).strip()
            return trimmed

        ai_message = trim_to_sentences(ai_message, max_sentences=50)

        return ai_message

    except requests.exceptions.RequestException as e:
        print(f"Error calling OpenRouter API: {e}")
        return None
    except (KeyError, IndexError) as e:
        print(f"Error parsing OpenRouter response: {e} - Response: {response.text}")
        return None
