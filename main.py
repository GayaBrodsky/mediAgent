"""
Mediagent - Group Decision Platform
Main entry point for running the application.

Usage:
    python main.py --mode web      # Start web UI for testing
    python main.py --mode telegram # Start Telegram bot
    python main.py --mode both     # Start both interfaces
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Load .env before importing settings
from dotenv import load_dotenv
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path, override=True)
    print(f"Loaded .env from: {env_path}")
else:
    load_dotenv()  # Try default locations

from config.settings import settings
from core.mediagent import Mediagent
from core.session import session_manager
from llm.base import LLMProvider


def create_llm_provider() -> LLMProvider:
    """Create the LLM provider based on settings."""
    provider_name = settings.LLM_PROVIDER
    api_key = settings.get_api_key(provider_name)
    
    if not api_key:
        print(f"Warning: No API key found for {provider_name}")
        print("Set the appropriate API key in your .env file")
        print("Using OpenAI as fallback (will fail if no key is set)")
        provider_name = "openai"
        api_key = settings.OPENAI_API_KEY
    
    return LLMProvider.create_provider(provider_name, api_key)


def run_web_mode():
    """Run the web UI interface."""
    print("=" * 50)
    print("Starting Mediagent - Web UI Mode")
    print("=" * 50)
    
    llm = create_llm_provider()
    mediagent = Mediagent(llm_provider=llm, session_mgr=session_manager)
    
    from interfaces.web_ui import WebUI
    web_ui = WebUI(mediagent)
    
    print(f"\nLLM Provider: {settings.LLM_PROVIDER}")
    print(f"Max Iterations: {settings.MAX_ITERATIONS}")
    print(f"Response Timeout: {settings.RESPONSE_TIMEOUT_SECONDS}s")
    print(f"\nOpen http://{settings.WEB_HOST}:{settings.WEB_PORT} in your browser")
    print("Press Ctrl+C to stop\n")
    
    web_ui.run_sync()


async def run_telegram_mode():
    """Run the Telegram bot interface."""
    print("=" * 50)
    print("Starting Mediagent - Telegram Bot Mode")
    print("=" * 50)
    
    if not settings.TELEGRAM_BOT_TOKEN:
        print("\nError: TELEGRAM_BOT_TOKEN not set in environment")
        print("Please add your bot token to the .env file")
        sys.exit(1)
    
    llm = create_llm_provider()
    mediagent = Mediagent(llm_provider=llm, session_mgr=session_manager)
    
    from interfaces.telegram_bot import TelegramBot
    telegram_bot = TelegramBot(mediagent)
    
    print(f"\nLLM Provider: {settings.LLM_PROVIDER}")
    print(f"Max Iterations: {settings.MAX_ITERATIONS}")
    print(f"Response Timeout: {settings.RESPONSE_TIMEOUT_SECONDS}s")
    print("\nBot is running. Press Ctrl+C to stop\n")
    
    try:
        await telegram_bot.run()
    except KeyboardInterrupt:
        await telegram_bot.stop()


async def run_both_mode():
    """Run both web UI and Telegram bot."""
    print("=" * 50)
    print("Starting Mediagent - Both Interfaces")
    print("=" * 50)
    
    llm = create_llm_provider()
    mediagent = Mediagent(llm_provider=llm, session_mgr=session_manager)
    
    # Import interfaces
    from interfaces.web_ui import WebUI
    from interfaces.telegram_bot import TelegramBot
    
    web_ui = WebUI(mediagent)
    
    print(f"\nLLM Provider: {settings.LLM_PROVIDER}")
    print(f"Max Iterations: {settings.MAX_ITERATIONS}")
    print(f"Response Timeout: {settings.RESPONSE_TIMEOUT_SECONDS}s")
    
    # Start web UI in background
    print(f"\nStarting Web UI at http://{settings.WEB_HOST}:{settings.WEB_PORT}")
    
    # Run Flask in a separate thread
    import threading
    
    def run_flask():
        web_ui.app.run(
            host=settings.WEB_HOST,
            port=settings.WEB_PORT,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start Telegram if token is available
    if settings.TELEGRAM_BOT_TOKEN:
        print("Starting Telegram bot...")
        telegram_bot = TelegramBot(mediagent)
        
        try:
            await telegram_bot.run()
        except KeyboardInterrupt:
            await telegram_bot.stop()
    else:
        print("\nTelegram bot not started (no token set)")
        print("Web UI only mode. Press Ctrl+C to stop")
        
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Mediagent - Group Decision Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py --mode web       # Start web UI for local testing
    python main.py --mode telegram  # Start Telegram bot for production
    python main.py --mode both      # Start both interfaces

Environment Variables:
    LLM_PROVIDER            - LLM to use (openai, gemini, qwen, deepseek)
    OPENAI_API_KEY          - OpenAI API key
    GEMINI_API_KEY          - Google Gemini API key
    QWEN_API_KEY            - Alibaba QWEN API key
    DEEPSEEK_API_KEY        - DeepSeek API key
    TELEGRAM_BOT_TOKEN      - Telegram bot token
    RESPONSE_TIMEOUT_SECONDS - Response timeout (default: 300)
    MAX_ITERATIONS          - Number of rounds (default: 3)

Copy env.example to .env and configure your settings.
        """
    )
    
    parser.add_argument(
        "--mode",
        type=str,
        choices=["web", "telegram", "both"],
        default="web",
        help="Interface mode to run (default: web)"
    )
    
    parser.add_argument(
        "--provider",
        type=str,
        choices=["openai", "gemini", "qwen", "deepseek"],
        help="Override LLM provider (uses env setting by default)"
    )
    
    parser.add_argument(
        "--host",
        type=str,
        help="Web UI host (default: 127.0.0.1)"
    )
    
    parser.add_argument(
        "--port",
        type=int,
        help="Web UI port (default: 5000)"
    )
    
    args = parser.parse_args()
    
    # Override settings if provided
    if args.provider:
        settings.LLM_PROVIDER = args.provider
    if args.host:
        settings.WEB_HOST = args.host
    if args.port:
        settings.WEB_PORT = args.port
    
    # Run appropriate mode
    if args.mode == "web":
        run_web_mode()
    elif args.mode == "telegram":
        asyncio.run(run_telegram_mode())
    elif args.mode == "both":
        asyncio.run(run_both_mode())


if __name__ == "__main__":
    main()

