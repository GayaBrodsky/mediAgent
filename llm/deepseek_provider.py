"""
DeepSeek LLM provider implementation.
DeepSeek API is compatible with OpenAI's API format.
"""

from typing import Optional
import httpx

from .base import LLMProvider


class DeepSeekProvider(LLMProvider):
    """DeepSeek API provider."""
    
    # DeepSeek API endpoint (OpenAI-compatible)
    API_URL = "https://api.deepseek.com/v1/chat/completions"
    
    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        """Initialize DeepSeek provider.
        
        Args:
            api_key: DeepSeek API key
            model: Model to use (default: deepseek-chat)
                   Options: deepseek-chat, deepseek-coder
        """
        super().__init__(api_key, model)
        self.client = httpx.AsyncClient(timeout=60.0)
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate a response using DeepSeek's API.
        
        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt
        
        Returns:
            The model's response text
        """
        messages = []
        
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        messages.append({
            "role": "user",
            "content": prompt
        })
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        response = await self.client.post(
            self.API_URL,
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        
        data = response.json()
        
        # OpenAI-compatible response format
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            return message.get("content", "")
        
        return ""
    
    async def health_check(self) -> bool:
        """Check if DeepSeek API is accessible."""
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5,
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            
            response = await self.client.post(
                self.API_URL,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return True
        except Exception:
            return False
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

