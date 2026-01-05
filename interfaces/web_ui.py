"""
Flask-based web UI for testing the Mediagent Decision Platform.
Provides a browser-based interface for simulating multi-user interactions.
"""

import asyncio
import json
import queue
import threading
import uuid
from datetime import datetime
from typing import Optional

from flask import Flask, render_template, request, jsonify, Response, session as flask_session

from .base import InterfaceBase
from core.mediagent import Mediagent
from core.models import SessionStatus
from config.settings import settings


class WebUI(InterfaceBase):
    """Flask-based web interface for testing."""
    
    def __init__(self, mediagent: Mediagent):
        """Initialize the web UI.
        
        Args:
            mediagent: The Mediagent instance
        """
        super().__init__(mediagent)
        
        self.app = Flask(
            __name__,
            template_folder='../templates',
            static_folder='../static'
        )
        self.app.secret_key = str(uuid.uuid4())
        
        # Message queues for SSE (one per web session)
        self._message_queues: dict[str, queue.Queue] = {}
        
        # Map web session IDs to member IDs and session IDs
        self._web_sessions: dict[str, dict] = {}  # web_session_id -> {member_id, session_id}
        
        self._setup_routes()
        
        # Event loop for async operations
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server_thread: Optional[threading.Thread] = None
    
    def _setup_routes(self):
        """Set up Flask routes."""
        
        @self.app.route('/')
        def index():
            """Main page."""
            return render_template('chat.html')
        
        @self.app.route('/api/create_session', methods=['POST'])
        def create_session():
            """Create a new decision session."""
            data = request.json
            topic = data.get('topic', '')
            admin_name = data.get('admin_name', 'Admin')
            
            if not topic:
                return jsonify({'error': 'Topic is required'}), 400
            
            # Get or create web session ID
            web_session_id = flask_session.get('web_session_id')
            if not web_session_id:
                web_session_id = str(uuid.uuid4())
                flask_session['web_session_id'] = web_session_id
            
            # Create session
            session = self.mediagent.session_mgr.create_session(
                topic=topic,
                admin_name=admin_name,
                admin_web_session_id=web_session_id,
            )
            
            # Track this web session
            admin = list(session.members.values())[0]
            self._web_sessions[web_session_id] = {
                'member_id': admin.id,
                'session_id': session.id
            }
            
            # Create message queue for this user
            if web_session_id not in self._message_queues:
                self._message_queues[web_session_id] = queue.Queue()
            
            return jsonify({
                'success': True,
                'session_id': session.id,
                'invite_code': session.invite_code,
                'member_id': admin.id
            })
        
        @self.app.route('/api/join_session', methods=['POST'])
        def join_session():
            """Join an existing session."""
            data = request.json
            invite_code = data.get('invite_code', '')
            member_name = data.get('member_name', 'Participant')
            
            if not invite_code:
                return jsonify({'error': 'Invite code is required'}), 400
            
            # Get or create web session ID
            web_session_id = flask_session.get('web_session_id')
            if not web_session_id:
                web_session_id = str(uuid.uuid4())
                flask_session['web_session_id'] = web_session_id
            
            session, member, error = self.mediagent.session_mgr.join_session(
                invite_code=invite_code,
                member_name=member_name,
                web_session_id=web_session_id,
            )
            
            if error:
                return jsonify({'error': error}), 400
            
            # Track this web session
            self._web_sessions[web_session_id] = {
                'member_id': member.id,
                'session_id': session.id
            }
            
            # Create message queue for this user
            if web_session_id not in self._message_queues:
                self._message_queues[web_session_id] = queue.Queue()
            
            return jsonify({
                'success': True,
                'session_id': session.id,
                'member_id': member.id,
                'topic': session.topic,
                'member_count': len(session.members)
            })
        
        @self.app.route('/api/start_session', methods=['POST'])
        def start_session():
            """Start the decision process."""
            data = request.json
            session_id = data.get('session_id', '')
            
            web_session_id = flask_session.get('web_session_id')
            if not web_session_id or web_session_id not in self._web_sessions:
                return jsonify({'error': 'Not in a session'}), 400
            
            # Run async operation
            future = asyncio.run_coroutine_threadsafe(
                self.mediagent.start_session(session_id),
                self._loop
            )
            from config.settings import settings
            success, error = future.result(timeout=settings.RESPONSE_TIMEOUT_SECONDS + 5) #NEW

            
            if not success:
                return jsonify({'error': error}), 400
            
            return jsonify({'success': True})
        
        @self.app.route('/api/submit_response', methods=['POST'])
        def submit_response():
            """Submit a response to the current question."""
            data = request.json
            answer = data.get('answer', '')
            
            if not answer:
                return jsonify({'error': 'Answer is required'}), 400
            
            web_session_id = flask_session.get('web_session_id')
            if not web_session_id or web_session_id not in self._web_sessions:
                return jsonify({'error': 'Not in a session'}), 400
            
            session_info = self._web_sessions[web_session_id]
            
            # Run async operation
            future = asyncio.run_coroutine_threadsafe(
                self.mediagent.handle_response(
                    session_info['session_id'],
                    session_info['member_id'],
                    answer
                ),
                self._loop
            )
            from config.settings import settings
            success, error = future.result(timeout=settings.RESPONSE_TIMEOUT_SECONDS + 5) #NEW

            
            if not success:
                return jsonify({'error': error}), 400
            
            return jsonify({'success': True})
        
        @self.app.route('/api/submit_vote', methods=['POST'])
        def submit_vote():
            """Submit a vote."""
            data = request.json
            option_index = data.get('option_index')
            
            if option_index is None:
                return jsonify({'error': 'Option index is required'}), 400
            
            web_session_id = flask_session.get('web_session_id')
            if not web_session_id or web_session_id not in self._web_sessions:
                return jsonify({'error': 'Not in a session'}), 400
            
            session_info = self._web_sessions[web_session_id]
            
            # Run async operation
            future = asyncio.run_coroutine_threadsafe(
                self.mediagent.handle_vote(
                    session_info['session_id'],
                    session_info['member_id'],
                    option_index
                ),
                self._loop
            )
            from config.settings import settings
            success, error = future.result(timeout=settings.RESPONSE_TIMEOUT_SECONDS + 5) #NEW

            
            if not success:
                return jsonify({'error': error}), 400
            
            return jsonify({'success': True})
        
        @self.app.route('/api/session_status')
        def session_status():
            """Get current session status."""
            web_session_id = flask_session.get('web_session_id')
            if not web_session_id:
                return jsonify({'in_session': False})
            
            # Check if we have this web session tracked
            if web_session_id not in self._web_sessions:
                # Try to recover from actual session data (server restart scenario)
                # Search all sessions for a member with this web_session_id
                all_sessions = self.mediagent.session_mgr.get_all_sessions()
                for session in all_sessions:
                    for member in session.members.values():
                        if member.web_session_id == web_session_id:
                            # Found! Restore the tracking
                            self._web_sessions[web_session_id] = {
                                'member_id': member.id,
                                'session_id': session.id
                            }
                            # Ensure message queue exists
                            if web_session_id not in self._message_queues:
                                self._message_queues[web_session_id] = queue.Queue()
                            break
                    else:
                        continue
                    break
                else:
                    # Not found in any session
                    return jsonify({'in_session': False})
            
            session_info = self._web_sessions[web_session_id]
            session = self.mediagent.session_mgr.get_session(session_info['session_id'])
            
            if not session:
                return jsonify({'in_session': False})
            
            member = session.members.get(session_info['member_id'])
            
            return jsonify({
                'in_session': True,
                'session_id': session.id,
                'invite_code': session.invite_code,
                'topic': session.topic,
                'status': session.status.value,
                'current_round': session.current_round,
                'max_iterations': session.max_iterations,
                'member_count': len(session.members),
                'member_id': session_info['member_id'],
                'member_name': member.name if member else 'Unknown',
                'is_admin': member.role.value == 'admin' if member else False,
                'voting_options': [
                    {'title': sol.title, 'description': sol.description}
                    for sol in (session.decision.proposed_solutions if session.decision else [])
                ] if session.status == SessionStatus.VOTING else []
            })
        
        @self.app.route('/api/init_session')
        def init_session():
            """Initialize a web session ID before connecting to SSE.
            This ensures the session cookie is set properly before streaming."""
            web_session_id = flask_session.get('web_session_id')
            if not web_session_id:
                web_session_id = str(uuid.uuid4())
                flask_session['web_session_id'] = web_session_id
            
            # Create message queue for this session
            if web_session_id not in self._message_queues:
                self._message_queues[web_session_id] = queue.Queue()
            
            return jsonify({'web_session_id': web_session_id})
        
        @self.app.route('/api/events')
        def events():
            """Server-Sent Events endpoint for real-time updates."""
            web_session_id = flask_session.get('web_session_id')
            
            if not web_session_id:
                # Session not initialized - client should call /api/init_session first
                web_session_id = str(uuid.uuid4())
                flask_session['web_session_id'] = web_session_id
            
            if web_session_id not in self._message_queues:
                self._message_queues[web_session_id] = queue.Queue()
            
            # Capture the queue reference for this connection
            message_queue = self._message_queues[web_session_id]
            
            def generate():
                while True:
                    try:
                        # Always get the LATEST queue for this session ID
                        current_queue = self._message_queues.get(web_session_id)
                        if not current_queue: break
                        msg = current_queue.get(timeout=30)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except queue.Empty:
                        # Send keepalive
                        yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
            
            return Response(
                generate(),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                }
            )
        
        @self.app.route('/api/force_proceed', methods=['POST'])
        def force_proceed():
            """Force proceed to next round (admin only)."""
            web_session_id = flask_session.get('web_session_id')
            if not web_session_id or web_session_id not in self._web_sessions:
                return jsonify({'error': 'Not in a session'}), 400
            
            session_info = self._web_sessions[web_session_id]
            
            # Run async operation
            future = asyncio.run_coroutine_threadsafe(
                self.mediagent.force_proceed(session_info['session_id']),
                self._loop
            )
            success, error = future.result(timeout=120)
            
            if not success:
                return jsonify({'error': error}), 400
            
            return jsonify({'success': True})
        
        @self.app.route('/api/leave_session', methods=['POST'])
        def leave_session():
            """Leave the current session."""
            web_session_id = flask_session.get('web_session_id')
            if web_session_id and web_session_id in self._web_sessions:
                del self._web_sessions[web_session_id]
            # Don't delete the message queue - it might still be in use by SSE
            return jsonify({'success': True})
    
    async def _send_message_to_user(
        self,
        session_id: str,
        member_id: str,
        message: str
    ) -> None:
        """Send a message to a user via SSE."""
        # Find the web session for this member
        session = self.mediagent.session_mgr.get_session(session_id)
        if not session:
            print(f"[WebUI] Session {session_id} not found")
            return
        
        member = session.members.get(member_id)
        if not member:
            print(f"[WebUI] Member {member_id} not found in session")
            return
        
        if not member.web_session_id:
            print(f"[WebUI] Member {member.name} has no web_session_id")
            return
        
        web_session_id = member.web_session_id
        
        if web_session_id in self._message_queues:
            self._message_queues[web_session_id].put({
                'type': 'message',
                'content': message,
                'refresh_state': True, #NEW
                'timestamp': datetime.now().isoformat()
            })
            print(f"[WebUI] Message sent to {member.name} (queue {web_session_id[:8]}...)")
        else:
            print(f"[WebUI] No queue found for {member.name} (web_session_id: {web_session_id[:8]}...)")
            print(f"[WebUI] Available queues: {[k[:8] + '...' for k in self._message_queues.keys()]}")
    
    async def run(self) -> None:
        """Start the web UI server."""
        # Create event loop for async operations
        self._loop = asyncio.get_event_loop()
        
        # Run Flask in a separate thread
        def run_flask():
            self.app.run(
                host=settings.WEB_HOST,
                port=settings.WEB_PORT,
                debug=settings.WEB_DEBUG,
                use_reloader=False,  # Disable reloader in thread
                threaded=True
            )
        
        self._server_thread = threading.Thread(target=run_flask, daemon=True)
        self._server_thread.start()
        
        print(f"Web UI running at http://{settings.WEB_HOST}:{settings.WEB_PORT}")
        
        # Keep the async loop running
        while True:
            await asyncio.sleep(1)
    
    async def stop(self) -> None:
        """Stop the web UI server."""
        # Flask doesn't have a built-in graceful shutdown in dev mode
        # In production, use a proper WSGI server
        pass
    
    def run_sync(self) -> None:
        """Run the web UI synchronously (for simple testing)."""
        print(f"Starting Web UI at http://{settings.WEB_HOST}:{settings.WEB_PORT}")
        
        # Create a new event loop for async operations
        self._loop = asyncio.new_event_loop()
        
        def run_loop():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()
        
        # Start async loop in background thread
        loop_thread = threading.Thread(target=run_loop, daemon=True)
        loop_thread.start()
        
        # Run Flask in main thread
        # Disable reloader when debug is on to avoid threading issues
        self.app.run(
            host=settings.WEB_HOST,
            port=settings.WEB_PORT,
            debug=settings.WEB_DEBUG,
            use_reloader=False,  # Disable reloader to prevent async loop issues
            threaded=True
        )

