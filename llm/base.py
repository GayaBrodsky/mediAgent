"""
Abstract base class for LLM providers.
Defines a unified interface for all LLM integrations.
"""

import json
import re
from abc import ABC, abstractmethod
from typing import Optional
from core.models import Decision, ProposedSolution


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    def __init__(self, api_key: str, model: str):
        """Initialize the LLM provider.
        
        Args:
            api_key: API key for the provider
            model: Model name to use
        """
        self.api_key = api_key
        self.model = model
    
    @abstractmethod
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate a response from the LLM.
        
        Args:
            prompt: The user prompt to send
            system_prompt: Optional system prompt for context
        
        Returns:
            The LLM's response text
        """
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is properly configured and accessible.
        
        Returns:
            True if healthy, False otherwise
        """
        pass
    
    def parse_json_from_response(self, response: str) -> Optional[dict]:
        """Extract JSON from an LLM response that may contain markdown.
        
        Args:
            response: The raw LLM response
        
        Returns:
            Parsed JSON dict or None if parsing fails
        """
        # Try to find JSON in code blocks first
        json_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', response)
        
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # Try to find raw JSON (starts with { and ends with })
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                json_str = json_match.group(0)
            else:
                return None
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Try to fix common JSON issues
            try:
                # Remove trailing commas
                fixed = re.sub(r',\s*([}\]])', r'\1', json_str)
                return json.loads(fixed)
            except json.JSONDecodeError:
                return None
    
    def parse_member_questions(self, response: str) -> tuple[dict[str, str], Optional[str]]:
        """Parse member-specific questions from an LLM response.
        
        Args:
            response: The raw LLM response
        
        Returns:
            Tuple of (questions_dict, analysis_text)
            questions_dict maps member_id to their personalized question
        """
        parsed = self.parse_json_from_response(response)
        
        if parsed and "questions" in parsed:
            questions = parsed.get("questions", {})
            analysis = parsed.get("analysis", parsed.get("synthesis", ""))
            return questions, analysis
        
        # Fallback: return empty dict if parsing fails
        return {}, None
    
    def parse_final_decision(self, response: str) -> Optional[Decision]:
        """Parse the final synthesis response into a Decision object.
        
        Args:
            response: The raw LLM response
        
        Returns:
            Decision object or None if parsing fails
        """
        parsed = self.parse_json_from_response(response)
        
        if not parsed:
            return None
        
        try:
            # Parse proposed solutions
            solutions = []
            for sol_data in parsed.get("proposed_solutions", []):
                solution = ProposedSolution(
                    title=sol_data.get("title", "Untitled Solution"),
                    description=sol_data.get("description", ""),
                    pros=sol_data.get("pros", []),
                    cons=sol_data.get("cons", []),
                )
                solutions.append(solution)
            
            decision = Decision(
                summary=parsed.get("summary", ""),
                key_agreements=parsed.get("key_agreements", []),
                remaining_tensions=parsed.get("remaining_tensions", []),
                proposed_solutions=solutions,
                recommendation=parsed.get("recommendation"),
            )
            
            return decision
            
        except Exception:
            return None
    
    @staticmethod
    def get_provider(provider_name: str) -> type["LLMProvider"]:
        """Get the provider class for a given provider name.
        
        Args:
            provider_name: Name of the provider (openai, gemini, qwen, deepseek)
        
        Returns:
            The provider class
        
        Raises:
            ValueError: If provider name is not recognized
        """
        from .openai_provider import OpenAIProvider
        from .gemini_provider import GeminiProvider
        from .qwen_provider import QWENProvider
        from .deepseek_provider import DeepSeekProvider
        
        providers = {
            "openai": OpenAIProvider,
            "gemini": GeminiProvider,
            "qwen": QWENProvider,
            "deepseek": DeepSeekProvider,
        }
        
        if provider_name not in providers:
            raise ValueError(f"Unknown provider: {provider_name}. Available: {list(providers.keys())}")
        
        return providers[provider_name]
    
    @staticmethod
    def create_provider(provider_name: str, api_key: str, model: Optional[str] = None) -> "LLMProvider":
        """Factory method to create an LLM provider instance.
        
        Args:
            provider_name: Name of the provider
            api_key: API key for the provider
            model: Optional model name (uses default if not specified)
        
        Returns:
            Configured LLMProvider instance
        """
        from config.settings import settings
        
        provider_class = LLMProvider.get_provider(provider_name)
        
        if model is None:
            model = settings.get_model(provider_name)
        
        return provider_class(api_key=api_key, model=model)

