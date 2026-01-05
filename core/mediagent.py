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

    def _format_plain_responses(self, responses: dict[str, str], member_names: dict[str, str]) -> str: #NEW
        lines = []
        for member_id, txt in responses.items():
            name = member_names.get(member_id, member_id)
            lines.append(f"{name}: {txt}")
        return "\n".join(lines)
            
    
    def _fallback_parse_name_lines(self, text: str) -> dict[str, str]: #NEW
        """
        Fallback parser for LLM outputs like:
        Gaya: question...
        Adi: question...
        Rony: question...

        Also tolerates:
        - leading bullets/numbers
        - markdown bold around names
        """
        import re

        out: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue

            # remove bullets / numbering
            line = re.sub(r"^[-*]\s+", "", line)
            line = re.sub(r"^\d+[\).\s]+", "", line)

            # strip markdown bold on the name: **Gaya**: ...
            line = re.sub(r"^\*\*(.+?)\*\*\s*:", r"\1:", line)

            m = re.match(r"^([^:–—-]+)\s*[:–—-]\s*(.+)$", line) #NEW
            if not m:
                continue

            name = m.group(1).strip()
            q = m.group(2).strip()

            name = name.strip()
            q = q.strip()

            if name and q:
                out[name] = q

        return out

    async def _broadcast_message(self, session: Session, message: str) -> None:
        """Send a message to all active members in a session."""
        for member in session.get_active_members():
            await self._send_message(session.id, member.id, message)
    
    async def start_session(self, session_id: str) -> tuple[bool, str]:
            success, error = self.session_mgr.start_session(session_id)
            if not success:
                return False, error

            session = self.session_mgr.get_session(session_id)
            if not session:
                return False, "Session not found"

            # Scoping phase (Round 0)
            session.status = SessionStatus.COLLECTING
            session.current_round = 0

            if hasattr(self.session_mgr, 'save_session'):
                self.session_mgr.save_session(session)

            # 1) Ask only the Admin for constraints
            admin_id = session.admin_id if hasattr(session, 'admin_id') else list(session.members.keys())[0]

            from config.prompts import ADMIN_ELABORATION_PROMPT
            prompt = ADMIN_ELABORATION_PROMPT.format(topic=session.topic)
            scope_msg = await self.llm.generate(prompt, SYSTEM_PROMPT)

            await self._send_message(session.id, admin_id, scope_msg)

            # 2) Tell others to wait
            for mid in session.members:
                if mid != admin_id:
                    wait_msg = "The Admin is currently setting the session constraints. Please wait..."
                    await self._send_message(session.id, mid, wait_msg)

            if hasattr(self.session_mgr, 'save_session'):
                self.session_mgr.save_session(session)

            return True, ""

    
    async def _start_round(self, session: Session, prepared_questions: dict[str, str] = None) -> None:
        """Start a new round of questioning.
        
        Args:
            session: The session
            prepared_questions: Optional pre-generated questions from LLM (for rounds > 1)
        """
        # Check if the round data already exists (to prevent skipping rounds)
        round_data = session.get_current_round_data()
        
        # If it doesn't exist yet, then create it
        if not round_data:
            round_data = session.start_new_round()
            
        round_data.started_at = datetime.now()
        session.status = SessionStatus.COLLECTING
        
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

        if hasattr(self.session_mgr, 'save_session'):
            self.session_mgr.save_session(session)            
        
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
        session = self.session_mgr.get_session(session_id)
        if not session: return False, "Session not found"
        
        # Phase 1: Handling the Admin's Scoping (Round 0)
        if session.current_round == 0:
            # Update the topic with the Admin's constraints
            session.topic = f"{session.topic} (Constraints: {answer})"
            session.status = SessionStatus.COLLECTING

            if hasattr(session, "rounds") and session.rounds:
                session.rounds.clear()
            
            # IMPORTANT: We must explicitly set current_round to 1 BEFORE _start_round
            # so the UI knows we have moved forward
            session.current_round = 0
            session.start_new_round()

            
            # Trigger the UI ping
            await self._broadcast_message(session, f"✅ Topic finalized: {session.topic}")
            
            # SAVE IMMEDIATELY - This is the "Main Problem" fix
            # This ensures the DB reflects Round 1 before messages are sent.
            if hasattr(self.session_mgr, 'save_session'):
                self.session_mgr.save_session(session)
            
            
            # Now populate Round 1 questions
            await self._start_round(session)
            
            return True, ""

        # Phase 2: Normal response handling for Round 1+
        success, error = self.session_mgr.submit_response(session_id, member_id, answer)
        if not success: return False, error
        
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
        
        participants = "\n".join([f"- {name}" for name in member_names.values()])

        prompt_vars = {
            "topic": session.topic,
            "responses": self._format_plain_responses(current_responses, member_names), #NEW
            "participants": participants,
            "round_number": session.current_round,
        }

        
        # 2. FIX: Explicitly loop through all previous rounds to fill prompt variables
        # This ensures "round_1_responses", "round_2_responses", etc. are ALL populated
        for i in range(1, session.current_round):
            if i in all_responses:
                # This fills the {round_1_responses} or {round_2_responses} tags in your prompts.py
                prompt_vars[f"round_{i}_responses"] = self._format_plain_responses(all_responses[i], member_names) #NEW


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
            if not questions:
                questions = self._fallback_parse_name_lines(response)

            
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
            
            next_round = session.current_round + 1
            print(f"LLM generated {len(mapped_questions)} questions for round {next_round}")

            # ✅ Correct advance
            if next_round > session.max_iterations:
                await self._synthesize_decision(session)
                return

            # Let Session manage round advancement consistently
            session.start_new_round()  # this should move current_round forward internally

            
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
                if not questions:
                    questions = self._fallback_parse_name_lines(response)

                
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
                
                next_round = session.current_round + 1

                # ✅ Correct advance
                if next_round > session.max_iterations:
                    await self._synthesize_decision(session)
                    return

                # Let Session manage round advancement consistently
                session.start_new_round()  # this should move current_round forward internally


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
    def _extract_json_object(self, text: str) -> str | None:
        """
        Extract the first top-level JSON object from model output.
        Works even if there is extra text before/after, or code fences.
        """
        import re

        t = text.strip()

        # Remove ```json ... ``` fences if present
        fence = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.DOTALL | re.IGNORECASE)
        if fence:
            t = fence.group(1).strip()

        # Find first '{' and then brace-match to its closing '}'
        start = t.find("{")
        if start == -1:
            return None

        depth = 0
        in_str = False
        esc = False

        for i in range(start, len(t)):
            ch = t[i]

            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue

            # not in string
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return t[start:i + 1]

        return None




    async def _synthesize_decision(self, session: Session) -> None:
        """Generate final decision options for voting (REAL options only, no fake fallback)."""
        import json
        from datetime import datetime
        from config.prompts import FINAL_SYNTHESIS_PROMPT, SYSTEM_PROMPT, format_responses
        from .logger import session_logger
        from .models import SessionStatus, Decision

        all_responses = session.get_all_responses_formatted()
        member_names = session.get_member_names()

        # Build the same "Round N" formatted history you had before
        all_formatted = []
        for round_num in sorted(all_responses.keys()):
            all_formatted.append(
                f"**Round {round_num}:**\n{format_responses(all_responses[round_num], member_names)}"
            )

        prompt = FINAL_SYNTHESIS_PROMPT.format(
            topic=session.topic,
            transcript="\n\n".join(all_formatted),
        )


        def _try_parse_json(text: str) -> dict | None:
            """Extract a JSON object from the text and parse it."""
            json_text = self._extract_json_object(text)
            if not json_text:
                return None
            try:
                return json.loads(json_text)
            except Exception:
                return None

        def _validate_data(data: dict) -> str | None:
            """Return error string if invalid, else None."""
            if not isinstance(data, dict):
                return "not a JSON object"

            sols = data.get("proposed_solutions")
            if not isinstance(sols, list) or len(sols) != 3:
                return "proposed_solutions must be a list of exactly 3 items"

            for i, s in enumerate(sols, 1):
                if not isinstance(s, dict):
                    return f"option {i} is not an object"
                if not s.get("title") or not s.get("description"):
                    return f"option {i} missing title/description"

            return None

        try:
            raw = await self.llm.generate(prompt, SYSTEM_PROMPT)
            # If output looks truncated, retry with a stricter short-output instruction NEW
            if raw and not raw.strip().endswith("}"):
                short_prompt = prompt + "\n\nIMPORTANT: Your previous output was cut off. Regenerate the SAME JSON but much shorter, following all brevity rules."
                raw = await self.llm.generate(short_prompt, SYSTEM_PROMPT)
            data = _try_parse_json(raw)

            # Retry once with a strict "repair to JSON" prompt if parsing/validation fails
            err = _validate_data(data) if data else "parse failed"
            if data is None or err is not None:
                fix_prompt = (
                    "You are a strict JSON formatter.\n"
                    "Convert the text below into VALID JSON that matches EXACTLY this schema.\n"
                    "Output ONLY JSON (no markdown, no commentary).\n\n"
                    "Schema:\n"
                    "{\n"
                    '  "summary": string,\n'
                    '  "key_agreements": [string],\n'
                    '  "remaining_tensions": [string],\n'
                    '  "proposed_solutions": [\n'
                    '    {"title": string, "description": string, "pros": [string], "cons": [string]},\n'
                    '    {"title": string, "description": string, "pros": [string], "cons": [string]},\n'
                    '    {"title": string, "description": string, "pros": [string], "cons": [string]}\n'
                    "  ]\n"
                    "}\n\n"
                    "Rules:\n"
                    "- proposed_solutions MUST contain EXACTLY 3 items\n"
                    "- Use double quotes only\n"
                    "- No trailing commas\n\n"
                    "TEXT TO CONVERT:\n"
                    f"{raw}"
                )
                repaired = await self.llm.generate(fix_prompt, SYSTEM_PROMPT)
                data = _try_parse_json(repaired)

            # If still bad → DO NOT create fake options → end session with raw text
            err = _validate_data(data) if data else "parse failed"
            if data is None or err is not None:
                await self._broadcast_message(
                    session,
                    "❌ Final synthesis could not be parsed into 3 real options.\n\n"
                    "Here is the raw LLM output so you can inspect what went wrong:\n\n"
                    f"{raw}"
                )
                session.status = SessionStatus.COMPLETED
                session.completed_at = datetime.now()
                return

            # Build Decision object
            proposed = []
            for s in data["proposed_solutions"]:
                proposed.append({
                    "title": s.get("title", ""),
                    "description": s.get("description", ""),
                    "pros": s.get("pros", []) or [],
                    "cons": s.get("cons", []) or [],
                    "votes": []
                })

            decision = Decision(
                summary=data.get("summary", ""),
                key_agreements=data.get("key_agreements", []) or [],
                remaining_tensions=data.get("remaining_tensions", []) or [],
                proposed_solutions=proposed,
                winning_solution=None
            )

            session.decision = decision
            session.status = SessionStatus.VOTING

            # Send summary
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

            await self._broadcast_message(session, voting_msg)

            session_logger.log_voting_started(
                session.id,
                [{"title": s.title, "description": s.description} for s in decision.proposed_solutions]
            )

        except Exception as e:
            await self._broadcast_message(
                session,
                f"❌ An error occurred during synthesis: {str(e)[:180]}"
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
        """Finalize the decision. If tie, ask LLM to break it with an explanation."""
        from datetime import datetime
        import re
        from config.prompts import TIE_BREAKER_PROMPT
        from config.prompts import SYSTEM_PROMPT  # if you store it there

        if not session.decision or not session.decision.proposed_solutions:
            return

        solutions = session.decision.proposed_solutions
        vote_counts = [len(s.votes) for s in solutions]
        max_votes = max(vote_counts) if vote_counts else 0

        # All options with max votes (tie candidates)
        winners = [s for s in solutions if len(s.votes) == max_votes]

        # Prepare base results message
        results_msg = "🎉 **Voting Complete!**\n\n**Results:**\n"
        for s in solutions:
            results_msg += f"• {s.title}: {len(s.votes)} vote(s)\n"

        tie_break_text = None
        winner = None

        # ---- Tie case ----
        if len(winners) > 1:
            # Build transcript (same style you use in synthesis)
            member_names = session.get_member_names()
            all_responses = session.get_all_responses_formatted()

            parts = []
            for rnd in sorted(all_responses.keys()):
                round_block = all_responses[rnd]
                lines = []
                for mid, txt in round_block.items():
                    name = member_names.get(mid, mid)
                    lines.append(f"{name}: {txt}")
                parts.append(f"Round {rnd}:\n" + "\n".join(lines))
            transcript = "\n\n".join(parts) if parts else "No responses were collected."

            # Format tied options
            tied_lines = []
            for idx, s in enumerate(solutions, 1):
                if s in winners:
                    tied_lines.append(f"- Option {idx}: {s.title} — {s.description}")
            tied_options_text = "\n".join(tied_lines)

            prompt = TIE_BREAKER_PROMPT.format(
                topic=session.topic,
                transcript=transcript,
                tied_options=tied_options_text
            )

            try:
                tie_break_text = await self.llm.generate(prompt, SYSTEM_PROMPT)
            except Exception as e:
                tie_break_text = f"**The Tie-Breaker Decision:** Option 1\n**Rationale:** (Tie-break LLM failed: {str(e)[:120]})"

            # Parse "Option <1|2|3>"
            m = re.search(r"Decision:\*\*\s*Option\s*(1|2|3)", tie_break_text, re.IGNORECASE)
            if not m:
                # tolerate slight format differences
                m = re.search(r"Option\s*(1|2|3)", tie_break_text, re.IGNORECASE)

            chosen_idx = int(m.group(1)) if m else 1
            chosen_idx = max(1, min(3, chosen_idx))
            winner = solutions[chosen_idx - 1]

            results_msg += "\n⚖️ **Tie detected. Invoking mediator tie-breaker...**\n"
            results_msg += f"\n🏆 **Final Decision: {winner.title}**\n\n"
            results_msg += tie_break_text.strip()

        # ---- Normal (non-tie) case ----
        else:
            winner = winners[0] if winners else None
            results_msg += f"\n🏆 **Winner: {winner.title if winner else 'No winner'}**"

        # Save winner + close session
        session.decision.winning_solution = winner
        session.status = SessionStatus.COMPLETED
        session.completed_at = datetime.now()

        await self._broadcast_message(session, results_msg)

        # Log completion
        session_logger.log_session_completed(
            session.id,
            {
                "winner": winner.title if winner else "None",
                "votes": {s.title: len(s.votes) for s in solutions},
                "tie_breaker_used": len(winners) > 1,
                "tie_breaker_text": tie_break_text if tie_break_text else ""
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
