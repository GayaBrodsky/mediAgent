"""
In-memory session management for the Mediagent Decision Platform.
Handles session creation, retrieval, and invite code management.
"""

import secrets
import string
from typing import Optional
from datetime import datetime

from .models import Session, Member, MemberRole, SessionStatus, Response
from .logger import session_logger
from config.settings import settings


class SessionManager:
    """Manages decision sessions in memory."""
    
    def __init__(self):
        self._sessions: dict[str, Session] = {}  # session_id -> Session
        self._invite_codes: dict[str, str] = {}  # invite_code -> session_id
        self._telegram_user_sessions: dict[int, str] = {}  # telegram_id -> session_id (active)
        self._web_user_sessions: dict[str, str] = {}  # web_session_id -> session_id (active)
    
    def _generate_invite_code(self, length: int = 8) -> str:
        """Generate a unique, human-friendly invite code."""
        # Use uppercase letters and digits, avoiding confusing characters
        alphabet = string.ascii_uppercase.replace('O', '').replace('I', '') + string.digits.replace('0', '').replace('1', '')
        while True:
            code = ''.join(secrets.choice(alphabet) for _ in range(length))
            if code not in self._invite_codes:
                return code
    
    def create_session(
        self,
        topic: str,
        admin_name: str,
        admin_telegram_id: Optional[int] = None,
        admin_web_session_id: Optional[str] = None,
        max_iterations: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        min_response_percentage: Optional[int] = None,
    ) -> Session:
        """Create a new decision session.
        
        Args:
            topic: The decision topic
            admin_name: Name of the admin creating the session
            admin_telegram_id: Telegram ID of admin (if using Telegram)
            admin_web_session_id: Web session ID of admin (if using web UI)
            max_iterations: Number of question rounds (uses default if not set)
            timeout_seconds: Response timeout (uses default if not set)
            min_response_percentage: Minimum % to proceed (uses default if not set)
        
        Returns:
            The created Session object
        """
        # Create admin member
        admin = Member(
            name=admin_name,
            role=MemberRole.ADMIN,
            telegram_id=admin_telegram_id,
            web_session_id=admin_web_session_id,
        )
        
        # Generate unique invite code
        invite_code = self._generate_invite_code()
        
        # Create session
        session = Session(
            invite_code=invite_code,
            topic=topic,
            admin_id=admin.id,
            max_iterations=max_iterations or settings.MAX_ITERATIONS,
            timeout_seconds=timeout_seconds or settings.RESPONSE_TIMEOUT_SECONDS,
            min_response_percentage=min_response_percentage or settings.MIN_RESPONSE_PERCENTAGE,
        )
        
        # Add admin as first member
        session.add_member(admin)
        
        # Store session
        self._sessions[session.id] = session
        self._invite_codes[invite_code] = session.id
        
        # Track user's active session
        if admin_telegram_id:
            self._telegram_user_sessions[admin_telegram_id] = session.id
        if admin_web_session_id:
            self._web_user_sessions[admin_web_session_id] = session.id
        
        # Log session creation
        session_logger.log_session_created(
            session.id,
            topic,
            admin_name,
            invite_code,
            {
                "max_iterations": session.max_iterations,
                "timeout_seconds": session.timeout_seconds,
                "min_response_percentage": session.min_response_percentage
            }
        )
        
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by its ID."""
        return self._sessions.get(session_id)
    
    def get_session_by_invite_code(self, invite_code: str) -> Optional[Session]:
        """Get a session by its invite code."""
        session_id = self._invite_codes.get(invite_code.upper())
        if session_id:
            return self._sessions.get(session_id)
        return None
    
    def get_user_active_session_telegram(self, telegram_id: int) -> Optional[Session]:
        """Get a user's active session by Telegram ID."""
        session_id = self._telegram_user_sessions.get(telegram_id)
        if session_id:
            session = self._sessions.get(session_id)
            if session and session.status not in [SessionStatus.COMPLETED, SessionStatus.CANCELLED]:
                return session
        return None
    
    def get_user_active_session_web(self, web_session_id: str) -> Optional[Session]:
        """Get a user's active session by web session ID."""
        session_id = self._web_user_sessions.get(web_session_id)
        if session_id:
            session = self._sessions.get(session_id)
            if session and session.status not in [SessionStatus.COMPLETED, SessionStatus.CANCELLED]:
                return session
        return None
    
    def join_session(
        self,
        invite_code: str,
        member_name: str,
        telegram_id: Optional[int] = None,
        web_session_id: Optional[str] = None,
    ) -> tuple[Optional[Session], Optional[Member], str]:
        """Join an existing session using an invite code.
        
        Args:
            invite_code: The session invite code
            member_name: Name of the joining member
            telegram_id: Telegram ID (if using Telegram)
            web_session_id: Web session ID (if using web UI)
        
        Returns:
            Tuple of (session, member, error_message)
            If successful, session and member are populated, error_message is empty
            If failed, session and member are None, error_message explains why
        """
        session = self.get_session_by_invite_code(invite_code)
        
        if not session:
            return None, None, "Invalid invite code"
        
        if session.status == SessionStatus.COMPLETED:
            return None, None, "This session has already completed"
        
        if session.status == SessionStatus.CANCELLED:
            return None, None, "This session has been cancelled"
        
        if len(session.members) >= settings.MAX_PARTICIPANTS:
            return None, None, "This session has reached the maximum number of participants"
        
        # Check if user is already in the session
        if telegram_id:
            existing = session.get_member_by_telegram_id(telegram_id)
            if existing:
                return session, existing, ""  # Already joined, return existing member
        
        if web_session_id:
            existing = session.get_member_by_web_session(web_session_id)
            if existing:
                return session, existing, ""  # Already joined, return existing member
        
        # Create new member
        member = Member(
            name=member_name,
            role=MemberRole.PARTICIPANT,
            telegram_id=telegram_id,
            web_session_id=web_session_id,
        )
        
        session.add_member(member)
        
        # Track user's active session
        if telegram_id:
            self._telegram_user_sessions[telegram_id] = session.id
        if web_session_id:
            self._web_user_sessions[web_session_id] = session.id
        
        # Log member joining
        session_logger.log_member_joined(session.id, member.id, member_name)
        
        return session, member, ""
    
    def submit_response(
        self,
        session_id: str,
        member_id: str,
        answer: str,
    ) -> tuple[bool, str]:
        """Submit a response for the current round.
        
        Args:
            session_id: The session ID
            member_id: The responding member's ID
            answer: The member's response
        
        Returns:
            Tuple of (success, error_message)
        """
        session = self.get_session(session_id)
        
        if not session:
            return False, "Session not found"
        
        if session.status != SessionStatus.COLLECTING:
            return False, "Session is not currently collecting responses"
        
        if member_id not in session.members:
            return False, "You are not a member of this session"
        
        round_data = session.get_current_round_data()
        if not round_data:
            return False, "No active round"
        
        # Check if already responded
        if member_id in round_data.responses:
            return False, "You have already submitted a response for this round"
        
        # Get the question that was asked
        question = round_data.questions.get(member_id, "")
        
        # Create response
        response = Response(
            member_id=member_id,
            round_number=session.current_round,
            question=question,
            answer=answer,
        )
        
        round_data.responses[member_id] = response
        
        return True, ""
    
    def start_session(self, session_id: str) -> tuple[bool, str]:
        """Start the decision process for a session.
        
        Args:
            session_id: The session ID
        
        Returns:
            Tuple of (success, error_message)
        """
        session = self.get_session(session_id)
        
        if not session:
            return False, "Session not found"
        
        if session.status != SessionStatus.CREATED:
            return False, "Session has already started"
        
        if len(session.members) < 2:
            return False, "Need at least 2 members to start"
        
        session.status = SessionStatus.COLLECTING
        session.started_at = datetime.now()
        
        return True, ""
    
    def update_session_status(self, session_id: str, status: SessionStatus) -> bool:
        """Update a session's status."""
        session = self.get_session(session_id)
        if session:
            session.status = status
            if status == SessionStatus.COMPLETED:
                session.completed_at = datetime.now()
            return True
        return False
    
    def get_all_sessions(self) -> list[Session]:
        """Get all sessions (for admin/debugging purposes)."""
        return list(self._sessions.values())
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session and clean up references."""
        session = self.get_session(session_id)
        if not session:
            return False
        
        # Remove from invite codes
        if session.invite_code in self._invite_codes:
            del self._invite_codes[session.invite_code]
        
        # Remove user session mappings
        for member in session.members.values():
            if member.telegram_id and member.telegram_id in self._telegram_user_sessions:
                if self._telegram_user_sessions[member.telegram_id] == session_id:
                    del self._telegram_user_sessions[member.telegram_id]
            if member.web_session_id and member.web_session_id in self._web_user_sessions:
                if self._web_user_sessions[member.web_session_id] == session_id:
                    del self._web_user_sessions[member.web_session_id]
        
        # Remove session
        del self._sessions[session_id]
        
        return True


# Global session manager instance
session_manager = SessionManager()

