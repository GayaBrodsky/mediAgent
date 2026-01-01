"""
Centralized settings for the Mediagent Decision Platform.
All configurable parameters are defined here with environment variable support.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

def _load_env():
    """Load environment variables from .env file."""
    # Try multiple possible .env file locations
    possible_env_paths = [
        Path(__file__).parent.parent / ".env",           # GroupDec/.env (config/../.env)
        Path(__file__).parent.parent.parent / ".env",    # Workspace root/.env  
        Path.cwd() / ".env",                              # Current working directory
    ]
    
    for env_path in possible_env_paths:
        resolved = env_path.resolve()
        if resolved.exists():
            # Use override=True to ensure our values take precedence
            load_dotenv(resolved, override=True)
            print(f"Loaded environment from: {resolved}")
            return True
    
    # Try loading from default locations
    load_dotenv(override=True)
    print("Warning: No .env file found. Using environment variables or defaults.")
    return False

# Load environment at module import
_load_env()


class Settings:
    """Application settings with environment variable support."""
    
    @staticmethod
    def _clean_value(value: str) -> str:
        """Remove surrounding quotes from a value."""
        if value and len(value) >= 2:
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                return value[1:-1]
        return value
    
    @staticmethod
    def _parse_int(value: str, default: int) -> int:
        """Parse an integer value, handling expressions like 24*60*60."""
        if not value:
            return default
        try:
            # First try simple int parsing
            return int(value)
        except ValueError:
            try:
                # Try evaluating simple math expressions (safe for simple cases)
                # Only allow digits, *, +, -, /, spaces
                import re
                if re.match(r'^[\d\s\*\+\-\/\(\)]+$', value):
                    return int(eval(value))
            except:
                pass
            return default
    
    def __init__(self):
        # LLM Provider Configuration
        self.LLM_PROVIDER: str = self._clean_value(os.getenv("LLM_PROVIDER", "openai"))
        
        # API Keys - clean quotes from values
        raw_openai_key = os.getenv("OPENAI_API_KEY", "")
        self.OPENAI_API_KEY: str = self._clean_value(raw_openai_key)
        
        # Debug: show if API key was found (masked)
        if self.OPENAI_API_KEY:
            masked = self.OPENAI_API_KEY[:10] + "..." + self.OPENAI_API_KEY[-4:] if len(self.OPENAI_API_KEY) > 20 else "***"
            print(f"OpenAI API Key loaded: {masked}")
        else:
            print(f"OpenAI API Key NOT found (raw value length: {len(raw_openai_key)})")
            if raw_openai_key:
                print(f"  Raw value starts with: {raw_openai_key[:20]}...")
        
        self.GEMINI_API_KEY: str = self._clean_value(os.getenv("GEMINI_API_KEY", ""))
        self.QWEN_API_KEY: str = self._clean_value(os.getenv("QWEN_API_KEY", ""))
        self.DEEPSEEK_API_KEY: str = self._clean_value(os.getenv("DEEPSEEK_API_KEY", ""))
        
        # Telegram Configuration
        self.TELEGRAM_BOT_TOKEN: str = self._clean_value(os.getenv("TELEGRAM_BOT_TOKEN", ""))
        
        # Session Configuration - handle expressions
        self.RESPONSE_TIMEOUT_SECONDS: int = self._parse_int(os.getenv("RESPONSE_TIMEOUT_SECONDS", ""), 300)
        self.MIN_RESPONSE_PERCENTAGE: int = self._parse_int(os.getenv("MIN_RESPONSE_PERCENTAGE", ""), 60)
        self.MAX_ITERATIONS: int = self._parse_int(os.getenv("MAX_ITERATIONS", ""), 3)
        self.MAX_PARTICIPANTS: int = self._parse_int(os.getenv("MAX_PARTICIPANTS", ""), 20)
        
        # Web UI Configuration
        self.WEB_HOST: str = os.getenv("WEB_HOST", "127.0.0.1")
        self.WEB_PORT: int = int(os.getenv("WEB_PORT", "5000"))
        self.WEB_DEBUG: bool = os.getenv("WEB_DEBUG", "true").lower() == "true"
        
        # Logging Configuration
        self.ENABLE_LOGGING: bool = os.getenv("ENABLE_LOGGING", "true").lower() == "true"
        self.LOG_DIR: str = os.getenv("LOG_DIR", "logs")
        
        # LLM Model Configuration (can be overridden per provider)
        self.OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.QWEN_MODEL: str = os.getenv("QWEN_MODEL", "qwen-turbo")
        self.DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    
    def get_api_key(self, provider: str) -> str:
        """Get the API key for a specific LLM provider."""
        key_map = {
            "openai": self.OPENAI_API_KEY,
            "gemini": self.GEMINI_API_KEY,
            "qwen": self.QWEN_API_KEY,
            "deepseek": self.DEEPSEEK_API_KEY,
        }
        return key_map.get(provider, "")
    
    def get_model(self, provider: str) -> str:
        """Get the model name for a specific LLM provider."""
        model_map = {
            "openai": self.OPENAI_MODEL,
            "gemini": self.GEMINI_MODEL,
            "qwen": self.QWEN_MODEL,
            "deepseek": self.DEEPSEEK_MODEL,
        }
        return model_map.get(provider, "")


# Global settings instance
settings = Settings()

