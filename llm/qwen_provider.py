"""
Alibaba QWEN LLM provider implementation.
Uses the DashScope API.
"""

from typing import Optional
import httpx

from .base import LLMProvider


class QWENProvider(LLMProvider):
    """Alibaba QWEN API provider via DashScope."""
    
    # DashScope API endpoint
    API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    
    def __init__(self, api_key: str, model: str = "qwen-turbo"):
        """Initialize QWEN provider.
        
        Args:
            api_key: DashScope API key
            model: Model to use (default: qwen-turbo)
                   Options: qwen-turbo, qwen-plus, qwen-max
        """
        super().__init__(api_key, model)
        self.client = httpx.AsyncClient(timeout=60.0)
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate a response using QWEN's API.
        
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
            "input": {
                "messages": messages
            },
            "parameters": {
                "temperature": 0.7,
                "max_tokens": 4096,
                "result_format": "message"
            }
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
        
        # Extract content from DashScope response format
        output = data.get("output", {})
        choices = output.get("choices", [])
        
        if choices:
            message = choices[0].get("message", {})
            return message.get("content", "")
        
        # Fallback for older response format
        return output.get("text", "")
    
    async def health_check(self) -> bool:
        """Check if QWEN API is accessible."""
        try:
            messages = [{"role": "user", "content": "Hi"}]
            
            payload = {
                "model": self.model,
                "input": {"messages": messages},
                "parameters": {"max_tokens": 5, "result_format": "message"}
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

