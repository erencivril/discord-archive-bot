# Archive DiscordBot

This project is the actual bot i was using and i own the code


Archive DiscordBot is a Python-based Discord bot project that is made by Eren Çivril and web interface that archives messages and attachments from a designated server, offers randomized archive replies, and generates AI-driven responses in Turkish. The project includes a Flask-powered admin UI for monitoring, searching, and controlling the bot.

## Features

- Archive text messages and attachments from specified channels.
- Random archive replies (text or attachments) with configurable probability.
- Turkish AI assistant responses via OpenRouter (configurable system prompt).
- Random AI replies with configurable probability.
- Random AI generated responses by copying the style of the archived messages.
- Support for the different AI model for the responses on mention.
- Basic voice protection: automatically remove offending roles if owner is muted/deafened/disconnected.
- Admin web dashboard (Flask) with:
  - Dashboard stats (total messages/attachments, CPU/memory usage, bot status).
  - Paginated views for messages, attachments, and application logs.
  - Bot control panel (start/stop/restart, enable/disable on boot).
  - Secure basic authentication.
- Configurable via environment variables (`.env`).

## Prerequisites

- Python 3.8 or higher
- PostgreSQL database
- A Discord bot application and token
- An OpenRouter API key
- `git` (optional, for version control)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/discordbot-archive.git
   cd discordbot-archive
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Copy and configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your tokens, database URL, and other settings
   ```

4. Initialize the database:
   ```bash
   python database.py
   ```

## Running

- **Bot**  
  ```bash
  python bot.py
  ```

- **Web Dashboard**  
  ```bash
  python web_app.py
  ```
  Access the UI at `http://localhost:8080` and log in with the credentials from your `.env`.

- **Systemd Service (that's what i am using on my ubuntu vds)**  
  ```bash
  sudo cp discord-bot.service /etc/systemd/system/
  sudo systemctl enable discord-bot.service
  sudo systemctl start discord-bot.service
  ```

## Configuration

Edit `.env` adjust:

- `PROB_ARCHIVE_REPLY` / `PROB_AI_REPLY`: probabilities for archive vs. AI responses.
- `AI_MENTION_COOLDOWN`: cooldown for non-owner mentions.
- `OPENROUTER_CHAT_MODEL`, `OPENROUTER_MENTION_MODEL`: models for AI responses.
- `MENTION_SYSTEM_PROMPT`: Turkish system prompt for AI.
- `ENABLE_VOICE_PROTECTION`: toggle voice protection feature.
- Plus your Discord tokens, guild/channel IDs, database URL, and web-UI credentials.
