"""
Mediagent - The core orchestration engine for group decision making.
Manages the iterative questioning process and LLM coordination.
"""

import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable

from .models import Session, SessionStatus, RoundData, Decision
from .session import SessionManager, session_manager
from .logger import session_logger
from llm.base import LLMProvider
from config.settings import settings
from config.prompts import (
    SYSTEM_PROMPT,
    INITIAL_QUESTION,
    get_iteration_prompt,
    FINAL_SYNTHESIS_PROMPT,
    format_responses,
)

from core import session


# Type for message sending callback
MessageCallback = Callable[[str, str, str], Awaitable[None]]  # (session_id, member_id, message) -> None


class Mediagent:
    """
    The Mediagent orchestrates the group decision-making process.
    
    It manages:
    - Session flow (rounds, timeouts)
    - LLM communication
    - Message routing to participants
    """
    
    def __init__(
        self,
        llm_provider: LLMProvider,
        session_mgr: Optional[SessionManager] = None,
    ):
        """Initialize the Mediagent.
        
        Args:
            llm_provider: The LLM provider to use
            session_mgr: Session manager (uses global instance if not provided)
        """
        self.llm = llm_provider
        self.session_mgr = session_mgr or session_manager
        
        # Callbacks for sending messages to interfaces
        self._message_callbacks: list[MessageCallback] = []
        
        # Active timeout tasks
        self._timeout_tasks: dict[str, asyncio.Task] = {}
    
    def register_message_callback(self, callback: MessageCallback) -> None:
        """Register a callback for sending messages to participants.
        
        Args:
            callback: Async function(session_id, member_id, message)
        """
        self._message_callbacks.append(callback)
    
    async def _send_message(self, session_id: str, member_id: str, message: str) -> None:
        """Send a message to a member via all registered callbacks."""
        for callback in self._message_callbacks:
            try:
                await callback(session_id, member_id, message)
            except Exception as e:
                print(f"Error sending message: {e}")
    
    async def _broadcast_message(self, session: Session, message: str) -> None:
        """Send a message to all active members in a session."""
        for member in session.get_active_members():
            await self._send_message(session.id, member.id, message)
    
    async def start_session(self, session_id: str) -> tuple[bool, str]:
        """Start a decision session and begin the first round.
        
        Args:
            session_id: The session to start
        
        Returns:
            Tuple of (success, error_message)
        """
        success, error = self.session_mgr.start_session(session_id)
        if not success:
            return False, error
        
        session = self.session_mgr.get_session(session_id)
        if not session:
            return False, "Session not found"
        
        # Log session start
        session_logger.log_session_started(session_id, len(session.members))
        
        # Start the first round
        await self._start_round(session)
        
        return True, ""
    
    async def _start_round(self, session: Session, prepared_questions: dict[str, str] = None) -> None:
        """Start a new round of questioning.
        
        Args:
            session: The session
            prepared_questions: Optional pre-generated questions from LLM (for rounds > 1)
        """
        # Create round data
        round_data = session.start_new_round()
        round_data.started_at = datetime.now()
        
        if session.current_round == 1:
            # First round: send the same initial question to everyone
            initial_q = INITIAL_QUESTION.format(topic=session.topic)
            
            for member in session.get_active_members():
                round_data.questions[member.id] = initial_q
                await self._send_message(session.id, member.id, initial_q)
        else:
            # Subsequent rounds: use prepared questions from LLM
            if prepared_questions:
                round_data.questions = prepared_questions
            
            # Send questions to each member
            for member in session.get_active_members():
                question = round_data.questions.get(member.id)
                if question:
                    await self._send_message(session.id, member.id, f"**Round {session.current_round} Question:**\n\n{question}")
                else:
                    # Fallback question if none prepared for this member
                    fallback_q = f"Based on the discussion so far, what are your thoughts on {session.topic}?"
                    round_data.questions[member.id] = fallback_q
                    await self._send_message(session.id, member.id, f"**Round {session.current_round} Question:**\n\n{fallback_q}")
        
        print(f"Started round {session.current_round} with {len(round_data.questions)} questions")
        
        # Log round start
        session_logger.log_round_started(
            session.id,
            session.current_round,
            round_data.questions
        )
        
        # Start timeout task
        self._start_timeout(session)
    
    def _start_timeout(self, session: Session) -> None:
        """Start the timeout task for the current round."""
        # Cancel any existing timeout
        if session.id in self._timeout_tasks:
            self._timeout_tasks[session.id].cancel()
        
        async def timeout_handler():
            await asyncio.sleep(session.timeout_seconds)
            await self._handle_timeout(session.id)
        
        task = asyncio.create_task(timeout_handler())
        self._timeout_tasks[session.id] = task
    
    def _cancel_timeout(self, session_id: str) -> None:
        """Cancel the timeout task for a session."""
        if session_id in self._timeout_tasks:
            self._timeout_tasks[session_id].cancel()
            del self._timeout_tasks[session_id]
    
    async def _handle_timeout(self, session_id: str) -> None:
        """Handle timeout for a round."""
        session = self.session_mgr.get_session(session_id)
        if not session or session.status != SessionStatus.COLLECTING:
            return
        
        if session.min_responses_received():
            # Proceed with available responses
            await self._process_round(session)
        else:
            # Not enough responses, send reminder
            round_data = session.get_current_round_data()
            if round_data:
                for member in session.get_active_members():
                    if member.id not in round_data.responses:
                        await self._send_message(
                            session.id,
                            member.id,
                            "⏰ Reminder: The round is ending soon. Please submit your response."
                        )
            
            # Give a short grace period, then proceed anyway
            await asyncio.sleep(60)  # 1 minute grace period
            
            session = self.session_mgr.get_session(session_id)
            if session and session.status == SessionStatus.COLLECTING:
                await self._process_round(session)
    
    async def handle_response(self, session_id: str, member_id: str, answer: str) -> tuple[bool, str]:
        """Handle a response from a participant.
        
        Args:
            session_id: The session ID
            member_id: The responding member's ID
            answer: The member's response
        
        Returns:
            Tuple of (success, error_message)
        """
        success, error = self.session_mgr.submit_response(session_id, member_id, answer)
        
        if not success:
            return False, error
        
        session = self.session_mgr.get_session(session_id)
        if not session:
            return False, "Session not found"
        
        # Log the response
        member = session.members.get(member_id)
        round_data = session.get_current_round_data()
        question = round_data.questions.get(member_id, "") if round_data else ""
        
        session_logger.log_response_received(
            session_id,
            session.current_round,
            member_id,
            member.name if member else "Unknown",
            question,
            answer
        )
        
        # Check if all responses are in
        if session.all_responses_received():
            self._cancel_timeout(session_id)
            await self._process_round(session)
        
        return True, ""
    
    async def _process_round(self, session: Session) -> None:
        """Process completed round and either start next round or synthesize."""
        session.status = SessionStatus.PROCESSING
        
        round_data = session.get_current_round_data()
        if round_data:
            round_data.completed_at = datetime.now()
            
            # Log round completion
            session_logger.log_round_completed(
                session.id,
                session.current_round,
                len(round_data.responses)
            )
        
        # Notify participants
        await self._broadcast_message(
            session,
            f"📊 Round {session.current_round} complete. Processing responses..."
        )
        
        if session.current_round >= session.max_iterations:
            # Final synthesis
            await self._synthesize_decision(session)
        else:
            # Generate next round questions
            await self._generate_next_questions(session)
    
    async def _generate_next_questions(self, session: Session) -> None:
        """Use LLM to generate personalized questions for next round."""
        # Build the prompt
        prompt_template = get_iteration_prompt(session.current_round)
        
        # Get formatted responses from ALL rounds so far
        # This ensures the AI has the full "memory" of the conversation
        all_responses = session.get_all_responses_formatted()
        member_names = session.get_member_names()
        
        # 1. Format current round responses (the ones we just finished)
        current_responses = all_responses.get(session.current_round, {})
        
        prompt_vars = {
            "topic": session.topic,
            "responses": format_responses(current_responses, member_names),
        }
        
        # 2. FIX: Explicitly loop through all previous rounds to fill prompt variables
        # This ensures "round_1_responses", "round_2_responses", etc. are ALL populated
        for i in range(1, session.current_round):
            if i in all_responses:
                # This fills the {round_1_responses} or {round_2_responses} tags in your prompts.py
                prompt_vars[f"round_{i}_responses"] = format_responses(
                    all_responses[i], member_names
                )

        # 3. For iteration_n (if rounds go beyond 3)
        if session.current_round > 3:
            prompt_vars["round_number"] = session.current_round
            all_prev = []
            for i in range(1, session.current_round):
                if i in all_responses:
                    all_prev.append(f"**Round {i}:**\n{format_responses(all_responses[i], member_names)}")
            prompt_vars["all_previous_responses"] = "\n\n".join(all_prev)
        
        # 4. Format the final prompt
        prompt = prompt_template.format(**prompt_vars)
        
        # Call LLM
        try:
            response = await self.llm.generate(prompt, SYSTEM_PROMPT)
            questions, analysis = self.llm.parse_member_questions(response)
            
            # Log LLM interaction
            session_logger.log_llm_interaction(
                session.id,
                session.current_round,
                prompt[:500] + "..." if len(prompt) > 500 else prompt,
                response[:1000] + "..." if len(response) > 1000 else response,
                questions
            )
            
            # Map questions back to member IDs
            # The LLM might use names, so we need to handle both
            mapped_questions = {}
            for key, question in questions.items():
                # Try to find member by ID or name
                if key in session.members:
                    mapped_questions[key] = question
                else:
                    # Try to find by name
                    for member in session.members.values():
                        if member.name.lower() == key.lower():
                            mapped_questions[member.id] = question
                            break
            
            # If no questions were parsed, use a generic follow-up
            if not mapped_questions:
                for member in session.get_active_members():
                    mapped_questions[member.id] = (
                        f"Based on the discussion so far, could you elaborate on your position "
                        f"regarding {session.topic}? What aspects are most important to you?"
                    )
            
            print(f"LLM generated {len(mapped_questions)} questions for round {session.current_round + 1}")
            
            # Start the next round with prepared questions
            session.status = SessionStatus.COLLECTING
            await self._start_round(session, prepared_questions=mapped_questions)
            
        except Exception as e:
            print(f"Error generating questions: {e}")
            await self._broadcast_message(
                session,
                f"⚠️ An error occurred while processing: {str(e)[:100]}"
            )
            
            # Retry once
            try:
                await asyncio.sleep(2)
                await self._broadcast_message(session, "🔄 Retrying...")
                response = await self.llm.generate(prompt, SYSTEM_PROMPT)
                questions, analysis = self.llm.parse_member_questions(response)
                
                # Map questions back to member IDs (same logic as above)
                mapped_questions = {}
                for key, question in questions.items():
                    if key in session.members:
                        mapped_questions[key] = question
                    else:
                        for member in session.members.values():
                            if member.name.lower() == key.lower():
                                mapped_questions[member.id] = question
                                break
                
                if not mapped_questions:
                    for member in session.get_active_members():
                        mapped_questions[member.id] = (
                            f"Based on the discussion so far, could you elaborate on your position "
                            f"regarding {session.topic}? What aspects are most important to you?"
                        )
                
                # Start next round with prepared questions
                session.status = SessionStatus.COLLECTING
                await self._start_round(session, prepared_questions=mapped_questions)
                
            except Exception as retry_error:
                print(f"Retry also failed: {retry_error}")
                await self._broadcast_message(
                    session,
                    "❌ Unable to process responses. The session will continue to the final synthesis."
                )
                # Skip to final synthesis or complete the session
                if session.current_round >= session.max_iterations - 1:
                    await self._synthesize_decision(session)
                else:
                    # Allow admin to force proceed or try again
                    session.status = SessionStatus.COLLECTING
                    await self._broadcast_message(
                        session,
                        "ℹ️ Admin can click 'Force Proceed' to continue with available responses."
                    )
    
    async def _synthesize_decision(self, session: Session) -> None:
        """Generate final decision options for voting."""
        # Build synthesis prompt
        all_responses = session.get_all_responses_formatted()
        member_names = session.get_member_names()
        
        all_formatted = []
        for round_num in sorted(all_responses.keys()):
            all_formatted.append(
                f"**Round {round_num}:**\n{format_responses(all_responses[round_num], member_names)}"
            )
        
        prompt = FINAL_SYNTHESIS_PROMPT.format(
            topic=session.topic,
            all_responses="\n\n".join(all_formatted),
        )
        
        try:
            response = await self.llm.generate(prompt, SYSTEM_PROMPT)
            decision = self.llm.parse_final_decision(response)
            
            if decision and decision.proposed_solutions:
                session.decision = decision
                session.status = SessionStatus.VOTING
                
                # Send summary and voting options to all members
                summary_msg = f"📋 **Decision Summary**\n\n{decision.summary}\n\n"
                
                if decision.key_agreements:
                    summary_msg += "**Key Agreements:**\n"
                    for agreement in decision.key_agreements:
                        summary_msg += f"• {agreement}\n"
                    summary_msg += "\n"
                
                if decision.remaining_tensions:
                    summary_msg += "**Points of Discussion:**\n"
                    for tension in decision.remaining_tensions:
                        summary_msg += f"• {tension}\n"
                    summary_msg += "\n"
                
                await self._broadcast_message(session, summary_msg)
                
                # Send voting options
                voting_msg = "🗳️ **Please vote on the following options:**\n\n"
                for i, solution in enumerate(decision.proposed_solutions, 1):
                    voting_msg += f"**Option {i}: {solution.title}**\n"
                    voting_msg += f"{solution.description}\n"
                    if solution.pros:
                        voting_msg += f"Pros: {', '.join(solution.pros)}\n"
                    if solution.cons:
                        voting_msg += f"Cons: {', '.join(solution.cons)}\n"
                    voting_msg += "\n"
                
                if decision.recommendation:
                    voting_msg += f"💡 **Recommendation:** {decision.recommendation}\n"
                
                await self._broadcast_message(session, voting_msg)
                
                # Log voting started
                session_logger.log_voting_started(
                    session.id,
                    [{"title": s.title, "description": s.description} for s in decision.proposed_solutions]
                )
                
            else:
                # Fallback if parsing fails
                await self._broadcast_message(
                    session,
                    f"📋 **Discussion Complete**\n\nThe LLM's synthesis:\n\n{response}"
                )
                session.status = SessionStatus.COMPLETED
                session.completed_at = datetime.now()
                
        except Exception as e:
            print(f"Error synthesizing decision: {e}")
            await self._broadcast_message(
                session,
                "❌ An error occurred during synthesis. Please review the discussion manually."
            )
            session.status = SessionStatus.COMPLETED
            session.completed_at = datetime.now()
    
    async def handle_vote(self, session_id: str, member_id: str, option_index: int) -> tuple[bool, str]:
        """Handle a vote from a participant.
        
        Args:
            session_id: The session ID
            member_id: The voting member's ID
            option_index: Index of the chosen option (0-based)
        
        Returns:
            Tuple of (success, error_message)
        """
        session = self.session_mgr.get_session(session_id)
        
        if not session:
            return False, "Session not found"
        
        if session.status != SessionStatus.VOTING:
            return False, "Session is not in voting phase"
        
        if not session.decision or not session.decision.proposed_solutions:
            return False, "No voting options available"
        
        if option_index < 0 or option_index >= len(session.decision.proposed_solutions):
            return False, "Invalid option"
        
        # Remove previous vote if any
        for solution in session.decision.proposed_solutions:
            if member_id in solution.votes:
                solution.votes.remove(member_id)
        
        # Add vote
        session.decision.proposed_solutions[option_index].votes.append(member_id)
        
        # Log vote
        member = session.members.get(member_id)
        session_logger.log_vote_cast(
            session_id,
            member_id,
            member.name if member else "Unknown",
            option_index,
            session.decision.proposed_solutions[option_index].title
        )
        
        await self._send_message(
            session_id,
            member_id,
            f"✅ Vote recorded for: {session.decision.proposed_solutions[option_index].title}"
        )
        
        # Check if all members have voted
        all_voted = all(
            any(m.id in sol.votes for sol in session.decision.proposed_solutions)
            for m in session.get_active_members()
        )
        
        if all_voted:
            await self._finalize_decision(session)
        
        return True, ""
    
    async def _finalize_decision(self, session: Session) -> None:
        """Finalize the decision and announce the winner."""
        if not session.decision:
            return
        
        # Find winning option
        max_votes = 0
        winner = None
        
        for solution in session.decision.proposed_solutions:
            if len(solution.votes) > max_votes:
                max_votes = len(solution.votes)
                winner = solution
        
        session.decision.winning_solution = winner
        session.status = SessionStatus.COMPLETED
        session.completed_at = datetime.now()
        
        # Announce results
        results_msg = "🎉 **Voting Complete!**\n\n**Results:**\n"
        
        for solution in session.decision.proposed_solutions:
            vote_count = len(solution.votes)
            results_msg += f"• {solution.title}: {vote_count} vote(s)\n"
        
        results_msg += f"\n🏆 **Winner: {winner.title if winner else 'Tie'}**"
        
        await self._broadcast_message(session, results_msg)
        
        # Log session completion
        session_logger.log_session_completed(
            session.id,
            {
                "winner": winner.title if winner else "Tie",
                "votes": {s.title: len(s.votes) for s in session.decision.proposed_solutions}
            }
        )
    
    async def force_proceed(self, session_id: str) -> tuple[bool, str]:
        """Force a session to proceed even without all responses (admin action).
        
        Args:
            session_id: The session ID
        
        Returns:
            Tuple of (success, error_message)
        """
        session = self.session_mgr.get_session(session_id)
        
        if not session:
            return False, "Session not found"
        
        if session.status != SessionStatus.COLLECTING:
            return False, "Session is not currently collecting responses"
        
        self._cancel_timeout(session_id)
        await self._process_round(session)
        
        return True, ""
    
    async def cancel_session(self, session_id: str) -> tuple[bool, str]:
        """Cancel a session.
        
        Args:
            session_id: The session ID
        
        Returns:
            Tuple of (success, error_message)
        """
        session = self.session_mgr.get_session(session_id)
        
        if not session:
            return False, "Session not found"
        
        self._cancel_timeout(session_id)
        session.status = SessionStatus.CANCELLED
        
        await self._broadcast_message(session, "❌ This decision session has been cancelled.")
        
        return True, ""

