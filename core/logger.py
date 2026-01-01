"""
Session logging for the Mediagent Decision Platform.
Saves all conversations and activity to log files.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from config.settings import settings


class SessionLogger:
    """Logs all session activity to files."""
    
    def __init__(self):
        self.enabled = settings.ENABLE_LOGGING
        self.log_dir = Path(settings.LOG_DIR)
        
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            print(f"Logging enabled. Logs will be saved to: {self.log_dir.absolute()}")
    
    def _get_session_log_path(self, session_id: str) -> Path:
        """Get the log file path for a session."""
        return self.log_dir / f"session_{session_id}.json"
    
    def _get_global_log_path(self) -> Path:
        """Get the global activity log path."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"activity_{date_str}.log"
    
    def _write_global_log(self, entry: str) -> None:
        """Append to global activity log."""
        if not self.enabled:
            return
        
        timestamp = datetime.now().isoformat()
        with open(self._get_global_log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {entry}\n")
    
    def _load_session_log(self, session_id: str) -> dict:
        """Load existing session log or create new one."""
        log_path = self._get_session_log_path(session_id)
        
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                return json.load(f)
        
        return {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "events": [],
            "rounds": {},
            "llm_interactions": [],
            "final_decision": None
        }
    
    def _save_session_log(self, session_id: str, data: dict) -> None:
        """Save session log to file."""
        if not self.enabled:
            return
        
        log_path = self._get_session_log_path(session_id)
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    def log_session_created(
        self,
        session_id: str,
        topic: str,
        admin_name: str,
        invite_code: str,
        settings_info: dict
    ) -> None:
        """Log session creation."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        data["topic"] = topic
        data["admin"] = admin_name
        data["invite_code"] = invite_code
        data["settings"] = settings_info
        data["events"].append({
            "type": "session_created",
            "timestamp": datetime.now().isoformat(),
            "admin": admin_name,
            "topic": topic
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"SESSION_CREATED: {session_id} - Topic: {topic[:50]}...")
    
    def log_member_joined(
        self,
        session_id: str,
        member_id: str,
        member_name: str
    ) -> None:
        """Log member joining session."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        data["events"].append({
            "type": "member_joined",
            "timestamp": datetime.now().isoformat(),
            "member_id": member_id,
            "member_name": member_name
        })
        
        if "members" not in data:
            data["members"] = {}
        data["members"][member_id] = member_name
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"MEMBER_JOINED: {session_id} - {member_name}")
    
    def log_session_started(self, session_id: str, member_count: int) -> None:
        """Log session start."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        data["started_at"] = datetime.now().isoformat()
        data["member_count"] = member_count
        data["events"].append({
            "type": "session_started",
            "timestamp": datetime.now().isoformat(),
            "member_count": member_count
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"SESSION_STARTED: {session_id} - {member_count} members")
    
    def log_round_started(
        self,
        session_id: str,
        round_number: int,
        questions: dict[str, str]
    ) -> None:
        """Log round start with questions."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        
        if str(round_number) not in data["rounds"]:
            data["rounds"][str(round_number)] = {
                "started_at": datetime.now().isoformat(),
                "questions": questions,
                "responses": {}
            }
        
        data["events"].append({
            "type": "round_started",
            "timestamp": datetime.now().isoformat(),
            "round": round_number,
            "question_count": len(questions)
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"ROUND_STARTED: {session_id} - Round {round_number}")
    
    def log_response_received(
        self,
        session_id: str,
        round_number: int,
        member_id: str,
        member_name: str,
        question: str,
        response: str
    ) -> None:
        """Log a member's response."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        
        round_key = str(round_number)
        if round_key not in data["rounds"]:
            data["rounds"][round_key] = {"responses": {}}
        
        data["rounds"][round_key]["responses"][member_id] = {
            "member_name": member_name,
            "question": question,
            "response": response,
            "timestamp": datetime.now().isoformat()
        }
        
        data["events"].append({
            "type": "response_received",
            "timestamp": datetime.now().isoformat(),
            "round": round_number,
            "member_name": member_name,
            "response_preview": response[:100] + "..." if len(response) > 100 else response
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"RESPONSE: {session_id} - Round {round_number} - {member_name}")
    
    def log_round_completed(
        self,
        session_id: str,
        round_number: int,
        response_count: int
    ) -> None:
        """Log round completion."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        
        round_key = str(round_number)
        if round_key in data["rounds"]:
            data["rounds"][round_key]["completed_at"] = datetime.now().isoformat()
            data["rounds"][round_key]["response_count"] = response_count
        
        data["events"].append({
            "type": "round_completed",
            "timestamp": datetime.now().isoformat(),
            "round": round_number,
            "response_count": response_count
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"ROUND_COMPLETED: {session_id} - Round {round_number} - {response_count} responses")
    
    def log_llm_interaction(
        self,
        session_id: str,
        round_number: int,
        prompt: str,
        response: str,
        parsed_questions: Optional[dict] = None
    ) -> None:
        """Log LLM prompt and response."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        
        data["llm_interactions"].append({
            "timestamp": datetime.now().isoformat(),
            "round": round_number,
            "prompt": prompt,
            "response": response,
            "parsed_questions": parsed_questions
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"LLM_CALL: {session_id} - Round {round_number}")
    
    def log_voting_started(
        self,
        session_id: str,
        options: list[dict]
    ) -> None:
        """Log voting phase start."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        data["voting"] = {
            "started_at": datetime.now().isoformat(),
            "options": options,
            "votes": {}
        }
        
        data["events"].append({
            "type": "voting_started",
            "timestamp": datetime.now().isoformat(),
            "option_count": len(options)
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"VOTING_STARTED: {session_id} - {len(options)} options")
    
    def log_vote_cast(
        self,
        session_id: str,
        member_id: str,
        member_name: str,
        option_index: int,
        option_title: str
    ) -> None:
        """Log a vote."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        
        if "voting" not in data:
            data["voting"] = {"votes": {}}
        
        data["voting"]["votes"][member_id] = {
            "member_name": member_name,
            "option_index": option_index,
            "option_title": option_title,
            "timestamp": datetime.now().isoformat()
        }
        
        data["events"].append({
            "type": "vote_cast",
            "timestamp": datetime.now().isoformat(),
            "member_name": member_name,
            "option": option_title
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"VOTE: {session_id} - {member_name} -> {option_title}")
    
    def log_session_completed(
        self,
        session_id: str,
        final_decision: Optional[dict] = None
    ) -> None:
        """Log session completion."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        data["completed_at"] = datetime.now().isoformat()
        data["final_decision"] = final_decision
        
        data["events"].append({
            "type": "session_completed",
            "timestamp": datetime.now().isoformat()
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"SESSION_COMPLETED: {session_id}")
    
    def log_error(
        self,
        session_id: str,
        error_type: str,
        error_message: str,
        context: Optional[dict] = None
    ) -> None:
        """Log an error."""
        if not self.enabled:
            return
        
        data = self._load_session_log(session_id)
        data["events"].append({
            "type": "error",
            "timestamp": datetime.now().isoformat(),
            "error_type": error_type,
            "error_message": error_message,
            "context": context
        })
        
        self._save_session_log(session_id, data)
        self._write_global_log(f"ERROR: {session_id} - {error_type}: {error_message[:100]}")


# Global logger instance
session_logger = SessionLogger()

