"""
Google Gemini LLM provider implementation.
"""

from typing import Optional
import google.generativeai as genai

from .base import LLMProvider


class GeminiProvider(LLMProvider):
    """Google Gemini API provider."""
    
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        """Initialize Gemini provider.
        
        Args:
            api_key: Google API key
            model: Model to use (default: gemini-2.5-flash)
        """
        super().__init__(api_key, model)
        genai.configure(api_key=api_key)
        self.model_instance = genai.GenerativeModel(model)
    
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate a response using Gemini's API.
        
        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt
        
        Returns:
            The model's response text
        """
        # Combine system prompt with user prompt for Gemini
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"
        
        # Gemini's async API
        response = await self.model_instance.generate_content_async(
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7,
                max_output_tokens=4096,
            )
        )
        
        return response.text or ""
    
    async def health_check(self) -> bool:
        """Check if Gemini API is accessible."""
        try:
            response = await self.model_instance.generate_content_async(
                "Hi",
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=5,
                )
            )
            return bool(response.text)
        except Exception:
            return False

