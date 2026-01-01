"""
Data models for the Mediagent Decision Platform.
Uses Pydantic for validation and serialization.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class SessionStatus(str, Enum):
    """Status of a decision session."""
    CREATED = "created"           # Session created, waiting for members
    COLLECTING = "collecting"     # Collecting responses for current round
    PROCESSING = "processing"     # Processing responses with LLM
    VOTING = "voting"             # Final voting phase
    COMPLETED = "completed"       # Decision process complete
    CANCELLED = "cancelled"       # Session cancelled


class MemberRole(str, Enum):
    """Role of a member in the session."""
    ADMIN = "admin"       # Created the session
    PARTICIPANT = "participant"  # Regular participant


class Member(BaseModel):
    """Represents a participant in the decision session."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    role: MemberRole = MemberRole.PARTICIPANT
    
    # Platform-specific identifiers
    telegram_id: Optional[int] = None
    telegram_username: Optional[str] = None
    web_session_id: Optional[str] = None
    
    # Timestamps
    joined_at: datetime = Field(default_factory=datetime.now)
    
    # Status
    is_active: bool = True


class Response(BaseModel):
    """A single response from a member for a specific round."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    member_id: str
    round_number: int
    question: str  # The question that was asked
    answer: str    # The member's response
    timestamp: datetime = Field(default_factory=datetime.now)


class RoundData(BaseModel):
    """Data for a single round of the decision process."""
    round_number: int
    questions: dict[str, str] = Field(default_factory=dict)  # member_id -> question
    responses: dict[str, Response] = Field(default_factory=dict)  # member_id -> response
    llm_analysis: Optional[str] = None  # Raw LLM response for this round
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ProposedSolution(BaseModel):
    """A solution proposed by the LLM for voting."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    votes: list[str] = Field(default_factory=list)  # List of member_ids who voted for this


class Decision(BaseModel):
    """The final decision result."""
    summary: str
    key_agreements: list[str] = Field(default_factory=list)
    remaining_tensions: list[str] = Field(default_factory=list)
    proposed_solutions: list[ProposedSolution] = Field(default_factory=list)
    recommendation: Optional[str] = None
    winning_solution: Optional[ProposedSolution] = None
    created_at: datetime = Field(default_factory=datetime.now)


class Session(BaseModel):
    """Represents a complete decision-making session."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    invite_code: str = Field(default_factory=lambda: str(uuid.uuid4())[:8].upper())
    
    # Session configuration
    topic: str
    max_iterations: int = 3
    timeout_seconds: int = 300
    min_response_percentage: int = 60
    
    # Participants
    admin_id: str
    members: dict[str, Member] = Field(default_factory=dict)  # member_id -> Member
    
    # Session state
    status: SessionStatus = SessionStatus.CREATED
    current_round: int = 0
    
    # Round data
    rounds: dict[int, RoundData] = Field(default_factory=dict)  # round_number -> RoundData
    
    # Final decision
    decision: Optional[Decision] = None
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    def add_member(self, member: Member) -> None:
        """Add a member to the session."""
        self.members[member.id] = member
    
    def get_member_by_telegram_id(self, telegram_id: int) -> Optional[Member]:
        """Find a member by their Telegram ID."""
        for member in self.members.values():
            if member.telegram_id == telegram_id:
                return member
        return None
    
    def get_member_by_web_session(self, web_session_id: str) -> Optional[Member]:
        """Find a member by their web session ID."""
        for member in self.members.values():
            if member.web_session_id == web_session_id:
                return member
        return None
    
    def get_active_members(self) -> list[Member]:
        """Get all active members."""
        return [m for m in self.members.values() if m.is_active]
    
    def get_current_round_data(self) -> Optional[RoundData]:
        """Get the data for the current round."""
        return self.rounds.get(self.current_round)
    
    def start_new_round(self) -> RoundData:
        """Start a new round and return its data."""
        self.current_round += 1
        round_data = RoundData(
            round_number=self.current_round,
            started_at=datetime.now()
        )
        self.rounds[self.current_round] = round_data
        return round_data
    
    def get_response_percentage(self) -> float:
        """Calculate the percentage of members who have responded in current round."""
        round_data = self.get_current_round_data()
        if not round_data:
            return 0.0
        
        active_members = self.get_active_members()
        if not active_members:
            return 0.0
        
        responded = len(round_data.responses)
        return (responded / len(active_members)) * 100
    
    def all_responses_received(self) -> bool:
        """Check if all active members have responded."""
        return self.get_response_percentage() >= 100
    
    def min_responses_received(self) -> bool:
        """Check if minimum required responses have been received."""
        return self.get_response_percentage() >= self.min_response_percentage
    
    def get_all_responses_formatted(self) -> dict[int, dict[str, str]]:
        """Get all responses from all rounds, formatted as round -> member_id -> answer."""
        result = {}
        for round_num, round_data in self.rounds.items():
            result[round_num] = {
                member_id: resp.answer 
                for member_id, resp in round_data.responses.items()
            }
        return result
    
    def get_member_names(self) -> dict[str, str]:
        """Get a mapping of member IDs to names."""
        return {m.id: m.name for m in self.members.values()}

