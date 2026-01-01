"""
Abstract base class for user interfaces.
Defines the contract that all interfaces (Telegram, Web) must implement.
"""

from abc import ABC, abstractmethod
from typing import Optional

from core.models import Session, Member
from core.mediagent import Mediagent


class InterfaceBase(ABC):
    """Abstract base class for user interfaces."""
    
    def __init__(self, mediagent: Mediagent):
        """Initialize the interface.
        
        Args:
            mediagent: The Mediagent instance to use
        """
        self.mediagent = mediagent
        
        # Register message callback with mediagent
        self.mediagent.register_message_callback(self._send_message_to_user)
    
    @abstractmethod
    async def _send_message_to_user(
        self,
        session_id: str,
        member_id: str,
        message: str
    ) -> None:
        """Send a message to a specific user.
        
        This is called by the Mediagent when it needs to communicate
        with participants.
        
        Args:
            session_id: The session the message belongs to
            member_id: The recipient member's ID
            message: The message content
        """
        pass
    
    @abstractmethod
    async def run(self) -> None:
        """Start the interface and begin accepting user interactions."""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the interface gracefully."""
        pass
    
    # Common helper methods that interfaces can use
    
    async def create_session(
        self,
        topic: str,
        admin_name: str,
        **kwargs
    ) -> Session:
        """Create a new decision session.
        
        Args:
            topic: The decision topic
            admin_name: Name of the admin
            **kwargs: Platform-specific identifiers
        
        Returns:
            The created session
        """
        return self.mediagent.session_mgr.create_session(
            topic=topic,
            admin_name=admin_name,
            **kwargs
        )
    
    async def join_session(
        self,
        invite_code: str,
        member_name: str,
        **kwargs
    ) -> tuple[Optional[Session], Optional[Member], str]:
        """Join an existing session.
        
        Args:
            invite_code: The session invite code
            member_name: Name of the joining member
            **kwargs: Platform-specific identifiers
        
        Returns:
            Tuple of (session, member, error_message)
        """
        return self.mediagent.session_mgr.join_session(
            invite_code=invite_code,
            member_name=member_name,
            **kwargs
        )
    
    async def start_session(self, session_id: str) -> tuple[bool, str]:
        """Start a decision session.
        
        Args:
            session_id: The session to start
        
        Returns:
            Tuple of (success, error_message)
        """
        return await self.mediagent.start_session(session_id)
    
    async def submit_response(
        self,
        session_id: str,
        member_id: str,
        answer: str
    ) -> tuple[bool, str]:
        """Submit a response from a participant.
        
        Args:
            session_id: The session ID
            member_id: The member's ID
            answer: The response content
        
        Returns:
            Tuple of (success, error_message)
        """
        return await self.mediagent.handle_response(
            session_id=session_id,
            member_id=member_id,
            answer=answer
        )
    
    async def submit_vote(
        self,
        session_id: str,
        member_id: str,
        option_index: int
    ) -> tuple[bool, str]:
        """Submit a vote from a participant.
        
        Args:
            session_id: The session ID
            member_id: The member's ID
            option_index: The chosen option (0-based index)
        
        Returns:
            Tuple of (success, error_message)
        """
        return await self.mediagent.handle_vote(
            session_id=session_id,
            member_id=member_id,
            option_index=option_index
        )

