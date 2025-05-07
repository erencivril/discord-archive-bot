# Main Discord bot script
import discord
import os
import random
import asyncio
from dotenv import load_dotenv
from sqlalchemy.orm import sessionmaker
from database import SessionLocal, get_random_message, get_random_attachment, delete_message, get_recent_messages_for_context, init_db, log_app_event
from openrouter_client import get_ai_response
import time
import re

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
BOT_OWNER_ID = int(os.getenv('BOT_OWNER_ID', 0))
PROB_ARCHIVE_REPLY = float(os.getenv('PROB_ARCHIVE_REPLY', 0.4))
PROB_AI_REPLY = float(os.getenv('PROB_AI_REPLY', 0.4))
AI_CONTEXT_LIMIT = int(os.getenv('AI_CONTEXT_MESSAGE_LIMIT', 50))

# Basic validation
if not DISCORD_TOKEN:
    print("Error: DISCORD_TOKEN not found in .env file.")
    exit()
if not BOT_OWNER_ID:
    print("Warning: BOT_OWNER_ID not found or invalid in .env file. Delete command will not work.")
if PROB_ARCHIVE_REPLY + PROB_AI_REPLY > 1.0:
    print("Warning: Sum of PROB_ARCHIVE_REPLY and PROB_AI_REPLY exceeds 1.0.")

# Discord Client Setup
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True # Enable message content intent
intents.guilds = True # Needed for context

intents.voice_states = True  # Enable voice state events
intents.members = True       # Needed to fetch member info for role removal
intents.guilds = True

client = discord.Client(intents=intents)

@client.event
async def on_voice_state_update(member, before, after):
    print(f"[DEBUG] on_voice_state_update triggered for member {member} ({member.id})", flush=True)
    # Reload env for live config
    load_dotenv(override=True)
    BOT_OWNER_ID = int(os.getenv('BOT_OWNER_ID', 0))
    ENABLE_VOICE_PROTECTION = os.getenv('ENABLE_VOICE_PROTECTION', 'false').lower() == 'true'

    if not ENABLE_VOICE_PROTECTION:
        print("[DEBUG] Voice protection disabled.", flush=True)
        return

    # Only act if the affected member is the owner
    if member.id != BOT_OWNER_ID:
        # print(f"[DEBUG] Voice update for non-owner {member.id}, ignoring.", flush=True) # Can be noisy
        return
    print(f"[DEBUG] Voice update for owner {member.id}.", flush=True)

    # Detect mute, deafen, or disconnect
    was_muted = not before.mute and after.mute
    was_deafened = not before.deaf and after.deaf
    was_disconnected = before.channel is not None and after.channel is None

    if not (was_muted or was_deafened or was_disconnected):
        # print("[DEBUG] No relevant voice action detected.", flush=True) # Can be noisy
        return
    print(f"[DEBUG] Relevant voice action detected: muted={was_muted}, deafened={was_deafened}, disconnected={was_disconnected}", flush=True)

    guild = after.channel.guild if after.channel else before.channel.guild if before.channel else None
    if not guild:
        print("[DEBUG] Could not determine guild.", flush=True)
        return
    print(f"[DEBUG] Guild found: {guild.name} ({guild.id})", flush=True)

    # Fetch audit logs for the relevant action
    try:
        perpetrator = None
        action_type = discord.AuditLogAction.member_update if (was_muted or was_deafened) else discord.AuditLogAction.member_move if was_disconnected else None

        if action_type:
            async for entry in guild.audit_logs(limit=5, action=action_type):
                # Add checks for None before accessing attributes
                if entry and entry.target and entry.user and entry.target.id == BOT_OWNER_ID:
                    perpetrator = entry.user
                    break

        if not perpetrator:
            print("[DEBUG] Could not find perpetrator in audit logs.", flush=True)
            return  # Could not find who did it
        print(f"[DEBUG] Found perpetrator: {perpetrator} ({perpetrator.id})", flush=True)

        # Prevent action if owner performed the action on themselves
        if perpetrator.id == BOT_OWNER_ID:
            print("[DEBUG] Owner performed action on self, ignoring.", flush=True)
            return

        # Get the perpetrator's member object
        perp_member = guild.get_member(perpetrator.id)
        if not perp_member:
            print(f"[DEBUG] Could not get member object for perpetrator {perpetrator.id}.", flush=True)
            return
        print(f"[DEBUG] Got member object for perpetrator: {perp_member}", flush=True)

        # Identify all roles with voice moderation permissions
        roles_to_remove = []
        for role in perp_member.roles:
            perms = role.permissions
            if perms.mute_members or perms.deafen_members or perms.move_members:
                roles_to_remove.append(role)

        if not roles_to_remove:
            print(f"[DEBUG] Perpetrator {perp_member} has no relevant roles to remove.", flush=True)
            return
        print(f"[DEBUG] Roles to remove from {perp_member}: {[r.name for r in roles_to_remove]}", flush=True)

        # Remove all offending roles
        await perp_member.remove_roles(*roles_to_remove, reason="Voice protection: muted/deafened/disconnected the owner")
        print(f"[DEBUG] Roles removed successfully.", flush=True)

        # Log the action
        with SessionLocal() as db_session:
            log_app_event(
                db_session,
                level="INFO",
                event_type="voice_protection",
                message="Voice protection triggered: roles removed from perpetrator.",
                extra={
                    "perpetrator_id": perpetrator.id,
                    "perpetrator_name": str(perp_member),
                    "roles_removed": [role.name for role in roles_to_remove],
                    "action": "mute" if was_muted else "deafen" if was_deafened else "disconnect",
                    "guild_id": guild.id
                }
            )
            db_session.commit()
    except Exception as e:
        print(f"Error in voice protection: {e}")
        with SessionLocal() as db_session:
            log_app_event(
                db_session,
                level="ERROR",
                event_type="voice_protection_error",
                message=f"Error in voice protection: {e}",
                extra={"guild_id": guild.id if guild else None}
            )
            db_session.commit()

@client.event
async def on_ready():
    print(f'Logged in as {client.user}', flush=True)
    print(f'Owner ID: {BOT_OWNER_ID}', flush=True)
    print(f'Probabilities: Archive={PROB_ARCHIVE_REPLY*100}%, AI={PROB_AI_REPLY*100}%', flush=True)
    # Ensure DB tables exist when bot starts
    try:
        init_db()
    except Exception as e:
        print(f"Error initializing database on startup: {e}")
        # Depending on the error, you might want to exit or just log it
        # exit()


# Cooldown tracking for AI mention responses
ai_mention_cooldowns = {}

@client.event
async def on_message(message):
    print(f"[DEBUG] on_message called: message.id={message.id}, author={message.author}, content={message.content}, mentions={[str(m) for m in message.mentions]}", flush=True)
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    # --- AI Mention Handler ---
    if client.user in message.mentions:
        print(f"[DEBUG] Bot was mentioned by {message.author} ({message.author.id}) in message {message.id}", flush=True)
        # Reload env for live config
        load_dotenv(override=True)
        BOT_OWNER_ID = int(os.getenv('BOT_OWNER_ID', 0))
        AI_MENTION_COOLDOWN = int(os.getenv('AI_MENTION_COOLDOWN', 60))
        # Determine mention model (override must differ from chat model)
        mention_model_env = os.getenv('OPENROUTER_MENTION_MODEL')
        chat_model = os.getenv('OPENROUTER_CHAT_MODEL')
        if mention_model_env and mention_model_env != chat_model:
            mention_model = mention_model_env
        else:
            mention_model = 'google/gemini-flash-1.5'
        print(f"[DEBUG] Using mention model: {mention_model}", flush=True)

        is_owner = message.author.id == BOT_OWNER_ID
        now = time.time()
        user_id = message.author.id

        # Check cooldown (owner bypasses)
        if not is_owner:
            last_time = ai_mention_cooldowns.get(user_id, 0)
            if now - last_time < AI_MENTION_COOLDOWN:
                print(f"[DEBUG] User {user_id} is on cooldown.", flush=True)
                # Silent on cooldown
                return
            print(f"[DEBUG] User {user_id} is not on cooldown.", flush=True)
        else:
            print(f"[DEBUG] User {user_id} is owner, bypassing cooldown.", flush=True)

        # Remove bot mention from message content
        content = message.content
        for mention in message.mentions:
            if mention == client.user:
                content = re.sub(rf"<@!?{client.user.id}>", "", content)
        content = content.strip()

        # Generate AI response
        # Debug: Generating AI response for mention
        print(f"[DEBUG] Generating AI response for content: '{content}'", flush=True)
        with SessionLocal() as db_session:
            try:
                # Use the specific mention model from env
                mention_system_prompt = os.getenv('MENTION_SYSTEM_PROMPT', "You are a helpful assistant responding to a user mention.")
                response = get_ai_response(content, model_override=mention_model, system_prompt_override=mention_system_prompt)
                print(f"[DEBUG] AI response received (mention model): '{response[:100]}...'", flush=True)
                # Send the mention response
                if response:
                    try:
                        sent_msg = await message.channel.send(response)
                        print(f"[DEBUG] Mention response sent: {sent_msg.id}", flush=True)
                    except Exception as e:
                        print(f"[ERROR] Failed to send mention response: {e}", flush=True)
                # Log the event
                log_app_event(
                    db_session,
                    level="INFO",
                    event_type="ai_mention_response",
                    message="AI mention response sent.",
                    extra={
                        "user_id": user_id,
                        "is_owner": is_owner,
                        "content": content,
                        "response_snippet": response[:100] if response else "",
                        "trigger_message_id": message.id,
                        "model": mention_model
                    }
                )
                db_session.commit()
            except Exception as e:
                print(f"Error in AI mention handler: {e}", flush=True)
                with SessionLocal() as db_session2:
                    log_app_event(
                        db_session2,
                        level="ERROR",
                        event_type="ai_mention_error",
                        message=f"Error in AI mention handler: {e}",
                        extra={"user_id": user_id, "content": content, "trigger_message_id": message.id}
                    )
                    db_session2.commit()
        # Update cooldown
        if not is_owner:
            ai_mention_cooldowns[user_id] = now
        return

    # Use a scoped session for this event
    with SessionLocal() as db_session:
        try:
            # --- Delete Command Handling ---
            if message.content.startswith('!delete_msg') and message.author.id == BOT_OWNER_ID:
                parts = message.content.split()
                if len(parts) == 2 and parts[1].isdigit():
                    msg_id_to_delete = int(parts[1])
                    print(f"Attempting to delete message ID: {msg_id_to_delete} by owner request.", flush=True)
                    try:
                        deleted = delete_message(db_session, msg_id_to_delete)
                        if deleted:
                            log_app_event(db_session, "INFO", "message_deleted", f"Owner deleted message ID {msg_id_to_delete}", extra={"deleted_by": message.author.id})
                            await message.channel.send(f"Successfully deleted message ID `{msg_id_to_delete}` and its attachments from the archive.")
                        else:
                            log_app_event(db_session, "WARNING", "message_delete_failed", f"Owner failed to delete message ID {msg_id_to_delete} (not found?)", extra={"deleted_by": message.author.id})
                            await message.channel.send(f"Could not find message ID `{msg_id_to_delete}` in the archive or deletion failed.")
                    except Exception as e:
                        print(f"Error during delete command: {e}", flush=True)
                        log_app_event(db_session, "ERROR", "message_delete_error", f"Error deleting message ID {msg_id_to_delete}: {e}", extra={"deleted_by": message.author.id})
                        await message.channel.send(f"An error occurred while trying to delete message ID `{msg_id_to_delete}`.")
                else:
                    await message.channel.send("Usage: `!delete_msg <message_id>`")
                return # Stop processing after handling command

            # --- Probabilistic Response Logic ---
            action_roll = random.random() # Get a float between 0.0 and 1.0

            response_content = None
            action_taken = "None"

            if action_roll < PROB_ARCHIVE_REPLY:
                # Action: Post random archive content
                action_taken = "Archive Reply"
                # Decide whether to send text or attachment (if any attachments exist)
                # This could be refined (e.g., check attachment count first)
                if random.random() < 0.7: # 70% chance for text message
                    random_msg = get_random_message(db_session)
                    if not random_msg: # Fallback if no messages found
                        att = get_random_attachment(db_session)
                        if att:
                            # Log the attachment event
                            log_app_event(
                                db_session,
                                level="INFO",
                                event_type="attachment_sent",
                                message="Attachment sent as fallback (no messages found)",
                                extra={
                                    "attachment_id": att.attachment_id,
                                    "filename": att.filename,
                                    "url": att.url,
                                    "content_type": att.content_type,
                                    "trigger_message_id": message.id
                                }
                            )
                            response_content = att.url  # Only send the URL
                            action_taken += " (Fallback Attachment)"
                        else:
                            response_content = None
                    else:
                        response_content = random_msg.content # Get content for sending
                        # Log the random message event with its ID
                        log_app_event(
                            db_session,
                            level="INFO",
                            event_type="random_message_sent",
                            message="Random message retrieved from archive.",
                            extra={
                                "original_message_id": random_msg.message_id,
                                "db_message_id": random_msg.id,
                                "content_snippet": response_content[:100],
                                "trigger_message_id": message.id
                            }
                        )
                        action_taken += " (Text)"
                else: # 30% chance for attachment
                    att = get_random_attachment(db_session)
                    if not att: # Fallback if no attachments found
                        random_msg = get_random_message(db_session)
                        if random_msg: # Check if fallback message was found
                            response_content = random_msg.content # Get content for sending
                             # Log the fallback random message event
                            log_app_event(
                                db_session,
                                level="INFO",
                                event_type="random_message_sent",
                                message="Random message retrieved as fallback (no attachments found).",
                                extra={
                                    "original_message_id": random_msg.message_id,
                                    "db_message_id": random_msg.id,
                                    "content_snippet": response_content[:100],
                                    "trigger_message_id": message.id
                                }
                            )
                        action_taken += " (Fallback Text)"
                    else:
                        # Log the attachment event
                        log_app_event(
                            db_session,
                            level="INFO",
                            event_type="attachment_sent",
                            message="Attachment sent",
                            extra={
                                "attachment_id": att.attachment_id,
                                "filename": att.filename,
                                "url": att.url,
                                "content_type": att.content_type,
                                "trigger_message_id": message.id
                            }
                        )
                        response_content = att.url  # Only send the URL
                        action_taken += " (Attachment)"

            elif action_roll < PROB_ARCHIVE_REPLY + PROB_AI_REPLY:
                # Action: Generate AI response
                action_taken = "AI Reply"
                print(f"Generating AI response for: '{message.content}'", flush=True)
                try:
                    # Fetch context (recent messages from DB)
                    context = get_recent_messages_for_context(db_session, limit=AI_CONTEXT_LIMIT)
                    # TODO: Potentially add current conversation history if needed
                    # For simplicity, just using user prompt + DB context for now
                    response_content = get_ai_response(message.content, context_messages=context)
                    if response_content:
                         log_app_event(db_session, "INFO", "ai_response_success", f"AI generated response for message {message.id}", extra={"prompt": message.content, "response": response_content[:200], "trigger_message_id": message.id})
                    else:
                         log_app_event(db_session, "WARNING", "ai_response_empty", f"AI returned empty response for message {message.id}", extra={"prompt": message.content, "trigger_message_id": message.id})

                except Exception as e:
                    print(f"Error getting AI response: {e}", flush=True)
                    response_content = "Sorry, I encountered an error trying to think of a reply."
                    log_app_event(db_session, "ERROR", "ai_response_error", f"Error getting AI response for message {message.id}: {e}", extra={"prompt": message.content, "trigger_message_id": message.id})


            else:
                # Action: Do nothing
                action_taken = "No Action"
                pass

            # Send the response if one was generated
            if response_content:
                try:
                    sent_message = await message.channel.send(response_content)
                    print(f"Action Taken: {action_taken} | Triggered by: {message.id} | Sent response: {sent_message.id} | Content: {response_content[:100]}...")
                except discord.HTTPException as e:
                    print(f"Error sending message (triggered by {message.id}): {e}")
                    log_app_event(db_session, "ERROR", "send_message_error", f"Discord API error sending response for trigger {message.id}: {e}", extra={"response_content": response_content[:200], "trigger_message_id": message.id})
                    # Handle cases like message too long, etc.
                    if e.code == 50035: # Invalid Form Body (often means message too long)
                         await message.reply(response_content[:1990] + "...") # Truncate
                    else:
                         await message.channel.send("I tried to send a response, but something went wrong.")
                except Exception as e:
                    print(f"Unexpected error sending message: {e}")
                    log_app_event(db_session, "ERROR", "send_message_error", f"Unexpected error sending response for trigger {message.id}: {e}", extra={"response_content": response_content[:200], "trigger_message_id": message.id})
                    await message.channel.send("An unexpected error occurred while sending the response.")
            else:
                 print(f"Action Taken: {action_taken} | Triggered by: {message.id} | No response sent.")

            # Commit the session after all operations within the event are done
            db_session.commit()

        except Exception as e:
            print(f"Error processing message {message.id}: {e}", flush=True)
            log_app_event(db_session, "ERROR", "message_processing_error", f"Error processing message {message.id}: {e}", extra={"message_content": message.content[:200]})
            db_session.rollback()
            await message.channel.send("An internal error occurred while processing your message.")


async def main():
    async with client:
        try:
            await client.start(DISCORD_TOKEN)
        except discord.LoginFailure:
            print("Error: Invalid DISCORD_TOKEN. Please check your .env file.")
        except Exception as e:
            print(f"An unexpected error occurred during bot execution: {e}")
        finally:
            # Log shutdown
            try:
                with SessionLocal() as db_session:
                    log_app_event(db_session, "INFO", "bot_shutdown", "Bot shutting down.")
            except Exception as log_e:
                print(f"Failed to log shutdown event: {log_e}")
            print("Bot shutting down.", flush=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped manually.", flush=True)
