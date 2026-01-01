"""
Telegram bot interface for the Mediagent Decision Platform.
Uses python-telegram-bot for Telegram API integration.
"""

import asyncio
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    PollAnswerHandler,
    filters,
    ContextTypes,
)

from .base import InterfaceBase
from core.mediagent import Mediagent
from core.models import SessionStatus, MemberRole
from config.settings import settings


# Conversation states
(
    AWAITING_TOPIC,
    AWAITING_SETTINGS,
    AWAITING_RESPONSE,
    AWAITING_VOTE,
) = range(4)


class TelegramBot(InterfaceBase):
    """Telegram bot interface for the Mediagent."""
    
    def __init__(self, mediagent: Mediagent, token: Optional[str] = None):
        """Initialize the Telegram bot.
        
        Args:
            mediagent: The Mediagent instance
            token: Bot token (uses settings if not provided)
        """
        super().__init__(mediagent)
        
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        if not self.token:
            raise ValueError("Telegram bot token is required")
        
        self.app: Optional[Application] = None
        
        # Track polls for voting
        self._polls: dict[str, str] = {}  # poll_id -> session_id
    
    async def _send_message_to_user(
        self,
        session_id: str,
        member_id: str,
        message: str
    ) -> None:
        """Send a message to a user via Telegram."""
        if not self.app:
            return
        
        session = self.mediagent.session_mgr.get_session(session_id)
        if not session:
            return
        
        member = session.members.get(member_id)
        if not member or not member.telegram_id:
            return
        
        try:
            await self.app.bot.send_message(
                chat_id=member.telegram_id,
                text=message,
                parse_mode='Markdown'
            )
        except Exception as e:
            print(f"Error sending Telegram message: {e}")
    
    def _build_application(self) -> Application:
        """Build the Telegram application with handlers."""
        app = Application.builder().token(self.token).build()
        
        # Conversation handler for creating sessions
        create_conv = ConversationHandler(
            entry_points=[CommandHandler('start', self._cmd_start)],
            states={
                AWAITING_TOPIC: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_topic)
                ],
                AWAITING_SETTINGS: [
                    CallbackQueryHandler(self._handle_settings_callback)
                ],
            },
            fallbacks=[CommandHandler('cancel', self._cmd_cancel)],
            per_user=True,
            per_chat=True,
        )
        
        app.add_handler(create_conv)
        
        # Command handlers
        app.add_handler(CommandHandler('join', self._cmd_join))
        app.add_handler(CommandHandler('status', self._cmd_status))
        app.add_handler(CommandHandler('startdecision', self._cmd_start_decision))
        app.add_handler(CommandHandler('proceed', self._cmd_proceed))
        app.add_handler(CommandHandler('cancel', self._cmd_cancel))
        app.add_handler(CommandHandler('help', self._cmd_help))
        
        # Handle deep links (join via invite)
        app.add_handler(MessageHandler(
            filters.TEXT & filters.Regex(r'^/start\s+\w+$'),
            self._handle_deep_link
        ))
        
        # Handle regular messages (responses)
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_message
        ))
        
        # Handle poll answers
        app.add_handler(PollAnswerHandler(self._handle_poll_answer))
        
        # Handle callback queries (inline buttons)
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        
        return app
    
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle /start command - begin session creation or show help."""
        user = update.effective_user
        chat = update.effective_chat
        
        # Check if this is a deep link
        if context.args:
            return await self._handle_deep_link(update, context)
        
        # Check if user is already in a session
        existing = self.mediagent.session_mgr.get_user_active_session_telegram(user.id)
        if existing:
            await update.message.reply_text(
                f"You're already in a session about: *{existing.topic}*\n\n"
                f"Use /status to see the current state.\n"
                f"Use /cancel to leave the session.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
        
        await update.message.reply_text(
            "👋 Welcome to Mediagent - Group Decision Platform!\n\n"
            "I'll help your group make decisions through structured dialogue.\n\n"
            "To create a new decision session, please enter the topic or question "
            "your group needs to decide on:"
        )
        
        return AWAITING_TOPIC
    
    async def _handle_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle topic input for session creation."""
        user = update.effective_user
        topic = update.message.text.strip()
        
        if len(topic) < 10:
            await update.message.reply_text(
                "Please provide a more detailed topic (at least 10 characters)."
            )
            return AWAITING_TOPIC
        
        # Store topic temporarily
        context.user_data['topic'] = topic
        
        # Ask for settings
        keyboard = [
            [
                InlineKeyboardButton("3 rounds (quick)", callback_data="rounds_3"),
                InlineKeyboardButton("5 rounds (thorough)", callback_data="rounds_5"),
            ],
            [
                InlineKeyboardButton("5 min timeout", callback_data="timeout_300"),
                InlineKeyboardButton("15 min timeout", callback_data="timeout_900"),
            ],
            [InlineKeyboardButton("✅ Create with defaults", callback_data="create_default")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📝 Topic: *{topic}*\n\n"
            "Choose your session settings or use defaults:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        return AWAITING_SETTINGS
    
    async def _handle_settings_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle settings selection callbacks."""
        query = update.callback_query
        await query.answer()
        
        user = update.effective_user
        data = query.data
        
        # Initialize settings if needed
        if 'settings' not in context.user_data:
            context.user_data['settings'] = {
                'max_iterations': settings.MAX_ITERATIONS,
                'timeout_seconds': settings.RESPONSE_TIMEOUT_SECONDS,
            }
        
        if data.startswith('rounds_'):
            rounds = int(data.split('_')[1])
            context.user_data['settings']['max_iterations'] = rounds
            await query.edit_message_text(
                f"Set to {rounds} rounds. Choose timeout or create session:",
                reply_markup=query.message.reply_markup
            )
            return AWAITING_SETTINGS
        
        elif data.startswith('timeout_'):
            timeout = int(data.split('_')[1])
            context.user_data['settings']['timeout_seconds'] = timeout
            await query.edit_message_text(
                f"Set to {timeout // 60} minute timeout. Choose rounds or create session:",
                reply_markup=query.message.reply_markup
            )
            return AWAITING_SETTINGS
        
        elif data == 'create_default' or data == 'create':
            # Create the session
            topic = context.user_data.get('topic', 'Untitled Decision')
            user_settings = context.user_data.get('settings', {})
            
            session = self.mediagent.session_mgr.create_session(
                topic=topic,
                admin_name=user.first_name or user.username or 'Admin',
                admin_telegram_id=user.id,
                max_iterations=user_settings.get('max_iterations'),
                timeout_seconds=user_settings.get('timeout_seconds'),
            )
            
            # Clear conversation data
            context.user_data.clear()
            
            # Generate invite link
            bot_username = (await context.bot.get_me()).username
            invite_link = f"https://t.me/{bot_username}?start={session.invite_code}"
            
            await query.edit_message_text(
                f"✅ Session created!\n\n"
                f"📝 *Topic:* {topic}\n"
                f"🔑 *Invite Code:* `{session.invite_code}`\n\n"
                f"Share this link with participants:\n{invite_link}\n\n"
                f"Once everyone has joined, use /startdecision to begin.",
                parse_mode='Markdown'
            )
            
            return ConversationHandler.END
        
        return AWAITING_SETTINGS
    
    async def _handle_deep_link(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle deep links for joining sessions."""
        user = update.effective_user
        
        # Extract invite code from deep link
        if context.args:
            invite_code = context.args[0]
        else:
            # Parse from message text
            text = update.message.text
            parts = text.split()
            if len(parts) < 2:
                await update.message.reply_text(
                    "Invalid invite link. Please use /join <invite_code>"
                )
                return ConversationHandler.END
            invite_code = parts[1]
        
        # Try to join
        session, member, error = self.mediagent.session_mgr.join_session(
            invite_code=invite_code,
            member_name=user.first_name or user.username or 'Participant',
            telegram_id=user.id,
        )
        
        if error:
            await update.message.reply_text(f"❌ {error}")
            return ConversationHandler.END
        
        await update.message.reply_text(
            f"✅ Joined session!\n\n"
            f"📝 *Topic:* {session.topic}\n"
            f"👥 *Participants:* {len(session.members)}\n\n"
            f"Wait for the admin to start the decision process.",
            parse_mode='Markdown'
        )
        
        # Notify admin
        admin = next((m for m in session.members.values() if m.role == MemberRole.ADMIN), None)
        if admin and admin.telegram_id and admin.telegram_id != user.id:
            try:
                await context.bot.send_message(
                    chat_id=admin.telegram_id,
                    text=f"👤 *{member.name}* has joined the session!\n"
                         f"Total participants: {len(session.members)}",
                    parse_mode='Markdown'
                )
            except Exception:
                pass
        
        return ConversationHandler.END
    
    async def _cmd_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /join command."""
        if not context.args:
            await update.message.reply_text(
                "Please provide an invite code:\n/join <invite_code>"
            )
            return
        
        await self._handle_deep_link(update, context)
    
    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        user = update.effective_user
        session = self.mediagent.session_mgr.get_user_active_session_telegram(user.id)
        
        if not session:
            await update.message.reply_text(
                "You're not in any active session.\n"
                "Use /start to create one or /join to join an existing one."
            )
            return
        
        member = session.get_member_by_telegram_id(user.id)
        is_admin = member and member.role == MemberRole.ADMIN
        
        status_emoji = {
            SessionStatus.CREATED: "⏳",
            SessionStatus.COLLECTING: "📝",
            SessionStatus.PROCESSING: "⚙️",
            SessionStatus.VOTING: "🗳️",
            SessionStatus.COMPLETED: "✅",
            SessionStatus.CANCELLED: "❌",
        }
        
        response_info = ""
        if session.status == SessionStatus.COLLECTING:
            round_data = session.get_current_round_data()
            if round_data:
                responded = len(round_data.responses)
                total = len(session.get_active_members())
                response_info = f"\n📊 Responses: {responded}/{total}"
        
        await update.message.reply_text(
            f"{status_emoji.get(session.status, '❓')} *Session Status*\n\n"
            f"📝 Topic: {session.topic}\n"
            f"🔄 Status: {session.status.value}\n"
            f"📍 Round: {session.current_round}/{session.max_iterations}\n"
            f"👥 Participants: {len(session.members)}"
            f"{response_info}\n"
            f"{'🔑 Invite: `' + session.invite_code + '`' if is_admin else ''}",
            parse_mode='Markdown'
        )
    
    async def _cmd_start_decision(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /startdecision command - admin starts the decision process."""
        user = update.effective_user
        session = self.mediagent.session_mgr.get_user_active_session_telegram(user.id)
        
        if not session:
            await update.message.reply_text("You're not in any active session.")
            return
        
        member = session.get_member_by_telegram_id(user.id)
        if not member or member.role != MemberRole.ADMIN:
            await update.message.reply_text("Only the session admin can start the decision process.")
            return
        
        if len(session.members) < 2:
            await update.message.reply_text(
                "Need at least 2 participants to start. "
                f"Share the invite code: `{session.invite_code}`",
                parse_mode='Markdown'
            )
            return
        
        success, error = await self.mediagent.start_session(session.id)
        
        if not success:
            await update.message.reply_text(f"❌ {error}")
            return
        
        await update.message.reply_text(
            "🚀 Decision process started!\n"
            "All participants will receive the first question shortly."
        )
    
    async def _cmd_proceed(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /proceed command - admin forces progression."""
        user = update.effective_user
        session = self.mediagent.session_mgr.get_user_active_session_telegram(user.id)
        
        if not session:
            await update.message.reply_text("You're not in any active session.")
            return
        
        member = session.get_member_by_telegram_id(user.id)
        if not member or member.role != MemberRole.ADMIN:
            await update.message.reply_text("Only the session admin can force proceed.")
            return
        
        success, error = await self.mediagent.force_proceed(session.id)
        
        if not success:
            await update.message.reply_text(f"❌ {error}")
        else:
            await update.message.reply_text("⏩ Proceeding to next step...")
    
    async def _cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle /cancel command."""
        user = update.effective_user
        session = self.mediagent.session_mgr.get_user_active_session_telegram(user.id)
        
        # Clear any conversation state
        context.user_data.clear()
        
        if session:
            member = session.get_member_by_telegram_id(user.id)
            if member and member.role == MemberRole.ADMIN:
                await self.mediagent.cancel_session(session.id)
                await update.message.reply_text("❌ Session cancelled.")
            else:
                await update.message.reply_text(
                    "You've left the session. Use /start to create a new one."
                )
        else:
            await update.message.reply_text("Operation cancelled.")
        
        return ConversationHandler.END
    
    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        await update.message.reply_text(
            "🤖 *Mediagent Commands*\n\n"
            "/start - Create a new decision session\n"
            "/join <code> - Join a session with invite code\n"
            "/status - View current session status\n"
            "/startdecision - Start the decision process (admin)\n"
            "/proceed - Force proceed to next round (admin)\n"
            "/cancel - Cancel session (admin) or leave\n"
            "/help - Show this help message\n\n"
            "During a session, simply reply with your responses when prompted.",
            parse_mode='Markdown'
        )
    
    async def _handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle regular text messages (responses during session)."""
        user = update.effective_user
        session = self.mediagent.session_mgr.get_user_active_session_telegram(user.id)
        
        if not session:
            await update.message.reply_text(
                "You're not in any active session. Use /start or /join."
            )
            return
        
        if session.status != SessionStatus.COLLECTING:
            await update.message.reply_text(
                "The session is not currently collecting responses. "
                "Use /status to check the current state."
            )
            return
        
        member = session.get_member_by_telegram_id(user.id)
        if not member:
            return
        
        success, error = await self.mediagent.handle_response(
            session.id,
            member.id,
            update.message.text
        )
        
        if not success:
            await update.message.reply_text(f"❌ {error}")
    
    async def _handle_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle callback queries from inline keyboards."""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user = update.effective_user
        
        # Handle vote callbacks
        if data.startswith('vote_'):
            parts = data.split('_')
            if len(parts) >= 2:
                option_index = int(parts[1])
                
                session = self.mediagent.session_mgr.get_user_active_session_telegram(user.id)
                if session:
                    member = session.get_member_by_telegram_id(user.id)
                    if member:
                        success, error = await self.mediagent.handle_vote(
                            session.id,
                            member.id,
                            option_index
                        )
                        if not success:
                            await query.edit_message_text(f"❌ {error}")
    
    async def _handle_poll_answer(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle poll answer submissions."""
        poll_answer = update.poll_answer
        user_id = poll_answer.user.id
        poll_id = poll_answer.poll_id
        
        if poll_id not in self._polls:
            return
        
        session_id = self._polls[poll_id]
        session = self.mediagent.session_mgr.get_session(session_id)
        
        if not session:
            return
        
        member = session.get_member_by_telegram_id(user_id)
        if not member:
            return
        
        # Get selected option
        if poll_answer.option_ids:
            option_index = poll_answer.option_ids[0]
            await self.mediagent.handle_vote(session_id, member.id, option_index)
    
    async def _send_voting_poll(self, session_id: str) -> None:
        """Send a Telegram poll for voting."""
        session = self.mediagent.session_mgr.get_session(session_id)
        if not session or not session.decision:
            return
        
        options = [sol.title for sol in session.decision.proposed_solutions]
        
        # Send poll to all members
        for member in session.get_active_members():
            if not member.telegram_id:
                continue
            
            try:
                poll_msg = await self.app.bot.send_poll(
                    chat_id=member.telegram_id,
                    question=f"Vote on: {session.topic}",
                    options=options,
                    is_anonymous=False,
                    allows_multiple_answers=False,
                )
                
                # Track poll
                self._polls[poll_msg.poll.id] = session_id
                
            except Exception as e:
                print(f"Error sending poll: {e}")
    
    async def run(self) -> None:
        """Start the Telegram bot."""
        self.app = self._build_application()
        
        # Initialize the application
        await self.app.initialize()
        await self.app.start()
        
        # Start polling
        await self.app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
        print("Telegram bot is running...")
        
        # Keep running
        while True:
            await asyncio.sleep(1)
    
    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

