# Flask web application for managing the archive
import os
import subprocess
import shlex
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker
from dotenv import dotenv_values, set_key, find_dotenv
from database import Base, Message, Attachment, AppLog, DATABASE_URL, log_app_event 
from dotenv import load_dotenv
import json

load_dotenv()
import psutil


ADMIN_USERNAME = os.getenv('WEB_ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('WEB_ADMIN_PASSWORD', 'password') 

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'a_very_secret_key') 

# Database setup for Flask app
if not DATABASE_URL:
    print("Error: DATABASE_URL not found in .env file for Flask app.")
    exit()

engine = create_engine(DATABASE_URL)
SessionLocalWeb = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid."""
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def authenticate():
    """Sends a 401 response"""
    return Response(
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- Routes ---
@app.route('/')
@requires_auth
def index():
    db = SessionLocalWeb()
    try:
        # Get some basic stats
        message_count = db.query(Message).count()
        attachment_count = db.query(Attachment).count()
        # Get last 10 messages
        recent_messages = db.query(Message).order_by(desc(Message.timestamp)).limit(10).all()
    finally:
        db.close()

    # Gather system health metrics
    cpu_percent = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    mem_percent = mem.percent

    return render_template('index.html',
                           message_count=message_count,
                           attachment_count=attachment_count,
                           recent_messages=recent_messages,
                           cpu_percent=cpu_percent,
                           mem_percent=mem_percent)

@app.route('/messages')
@requires_auth
def view_messages():
    db = SessionLocalWeb()
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 50 # Messages per page
        search_query = request.args.get('q', '')
        offset = (page - 1) * per_page

        query = db.query(Message)
        if search_query:
            # Basic search in content and author name
            search_term = f"%{search_query}%"
            query = query.filter(Message.content.ilike(search_term) | Message.author_name.ilike(search_term))

        # Get total count for pagination
        total = query.count()
        # Apply ordering, offset, and limit
        messages = query.order_by(desc(Message.timestamp)).offset(offset).limit(per_page).all()

    finally:
        db.close()

    # Calculate total pages
    total_pages = (total + per_page - 1) // per_page

    return render_template('messages.html',
                           messages=messages,
                           page=page,
                           per_page=per_page,
                           total=total,
                           total_pages=total_pages,
                           search_query=search_query)

@app.route('/attachments')
@requires_auth
def view_attachments():
    db = SessionLocalWeb()
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 50 # Attachments per page
        search_query = request.args.get('q', '')
        offset = (page - 1) * per_page

        query = db.query(Attachment)
        if search_query:
            search_term = f"%{search_query}%"
            query = query.filter(Attachment.filename.ilike(search_term) | Attachment.url.ilike(search_term))

        # Get total count for pagination
        total = query.count()
        # Apply ordering, offset, and limit
        attachments = query.order_by(desc(Attachment.created_at)).offset(offset).limit(per_page).all()

    finally:
        db.close()

    # Calculate total pages
    total_pages = (total + per_page - 1) // per_page

    return render_template('attachments.html',
                           attachments=attachments,
                           page=page,
                           per_page=per_page,
                           total=total,
                           total_pages=total_pages,
                           search_query=search_query)

# Add template context processor to inject variables into all templates
@app.context_processor
def inject_template_globals():
    from datetime import datetime
    return {
        'current_year': datetime.now().year
    }

# --- Bot Service Control Routes ---

    def run_systemctl_command(action):
        """Helper function to run systemctl commands for the bot service."""
        command = f"sudo systemctl {action} discord-bot.service"
    try:
        # Use shlex.split for better security if action contained spaces, though unlikely here
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, check=True, timeout=15)
        return True, result.stdout.strip() or f"Command '{action}' executed successfully."
    except FileNotFoundError:
        return False, f"Error: 'sudo' or 'systemctl' command not found. Is sudo installed and in PATH?"
    except subprocess.CalledProcessError as e:
        error_message = f"Error executing '{action}': {e.stderr.strip()}"
        # Check for specific sudoers error
        if "a password is required" in e.stderr:
            error_message += "\n\nHint: Ensure NOPASSWD is configured correctly in sudoers for the 'eren' user and these specific systemctl commands."
        return False, error_message
    except subprocess.TimeoutExpired:
        return False, f"Error: Command '{action}' timed out."
    except Exception as e:
        return False, f"An unexpected error occurred: {e}"

@app.route('/bot/control/<action>', methods=['POST'])
@requires_auth
def bot_control(action):
    allowed_actions = ['start', 'stop', 'enable', 'disable', 'restart']
    if action not in allowed_actions:
        flash('Invalid action requested.', 'danger')
        return redirect(url_for('index')) # Or a dedicated control page

    success, message_output = run_systemctl_command(action)
    flash(message_output, 'success' if success else 'danger')

    # Log the action
    with SessionLocalWeb() as db:
        log_app_event(
            db,
            level="INFO" if success else "ERROR",
            event_type="bot_control",
            message=f"Bot control action '{action}' requested via web UI.",
            extra={"action": action, "success": success, "output": message_output}
        )

    # Redirect back to index or a dedicated control page
    return redirect(url_for('index'))

@app.route('/bot/status')
@requires_auth
def bot_status():
    """Returns the status of the bot service as JSON."""
    # Use a slightly different command to get brief status
    command = "systemctl is-active discord-bot.service"
    status_text = "unknown"
    is_enabled_text = "unknown"
    try:
        # Check active state
        result_active = subprocess.run(shlex.split(command), capture_output=True, text=True, timeout=5)
        # is-active returns exit code 0 if active, non-zero otherwise. Output is the state name.
        if result_active.returncode == 0:
             status_text = result_active.stdout.strip() # e.g., "active"
        else:
             # Could be inactive, failed, activating, etc. Get more detail.
             status_command = "systemctl status discord-bot.service --no-pager -l"
             result_status = subprocess.run(shlex.split(status_command), capture_output=True, text=True, timeout=10)
             # Try to parse the status output for a better description
             status_text = "inactive" # Default if parsing fails
             for line in result_status.stdout.splitlines():
                 if "Active:" in line:
                     status_text = line.split("Active:")[1].strip().split(" ")[0] # e.g., "inactive", "failed"
                     break

        command_enabled = "systemctl is-enabled discord-bot.service"
        result_enabled = subprocess.run(shlex.split(command_enabled), capture_output=True, text=True, timeout=5)
        if result_enabled.returncode == 0:
            is_enabled_text = result_enabled.stdout.strip() # "enabled"
        else:
            is_enabled_text = "disabled" 

    except Exception as e:
        print(f"Error getting bot status: {e}")
        status_text = "error"
        is_enabled_text = "error"

    return jsonify(status=status_text, enabled=is_enabled_text)


# --- Logs Route ---

@app.route('/bot/logs')
@requires_auth
def bot_logs():
    """Displays recent logs for the bot service."""
    log_lines = []
    error_message = None
    command = "journalctl -u discord-bot.service --no-pager -n 100 --output short-iso --quiet"
    try:
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, check=True, timeout=15)
        log_lines = result.stdout.strip().splitlines()
        log_lines.reverse() 
    except FileNotFoundError:
        error_message = "Error: 'sudo' or 'journalctl' command not found."
    except subprocess.CalledProcessError as e:
        error_message = f"Error fetching logs: {e.stderr.strip()}"
        if "Failed to read journal" in e.stderr or "Permission denied" in e.stderr:
             error_message += "\n\nHint: Ensure the 'eren' user is part of the 'systemd-journal' group (run 'sudo usermod -a -G systemd-journal eren' and log out/in) or has specific sudoers permission for journalctl."
    except subprocess.TimeoutExpired:
        error_message = "Error: Command to fetch logs timed out."
    except Exception as e:
        error_message = f"An unexpected error occurred fetching logs: {e}"

    if error_message:
        flash(error_message, 'danger')

    return render_template('logs.html', logs=log_lines)

@app.route('/bot/logs/json')
@requires_auth
def bot_logs_json():
    """Returns recent logs for the bot service as JSON with timestamps."""
    log_entries = []
    error_message = None
    command = "journalctl -u discord-bot.service --no-pager -n 100 --output short-iso --quiet"
    try:
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, check=True, timeout=15)
        lines = result.stdout.strip().splitlines()
        lines.reverse() 
        for line in lines:
            if " " in line:
                timestamp, message = line.split(" ", 1)
            else:
                timestamp, message = "", line
            log_entries.append({"timestamp": timestamp, "message": message})
    except Exception as e:
        error_message = str(e)
        return jsonify({"error": error_message, "logs": []}), 500

    return jsonify({"logs": log_entries})


# --- Settings Route ---

# Helper to find the .env file path
dotenv_path = find_dotenv()
if not dotenv_path:
    print("Warning: .env file not found. Settings page might not work correctly.")


@app.route('/settings', methods=['GET', 'POST'])
@requires_auth
def settings():
    if request.method == 'POST':
        try:
            prob_archive = float(request.form['prob_archive_reply'])
            prob_ai = float(request.form['prob_ai_reply'])
            ai_mention_cooldown = int(request.form['ai_mention_cooldown'])
            openrouter_chat_model = request.form['openrouter_chat_model'].strip()
            openrouter_mention_model = request.form['openrouter_mention_model'].strip() # New model
            mention_system_prompt = request.form.get('mention_system_prompt', '').strip()
            enable_voice_protection = 'enable_voice_protection' in request.form and request.form['enable_voice_protection'] == 'true'

            # Validation
            if not (0.0 <= prob_archive <= 1.0):
                flash('Archive Reply Probability must be between 0.0 and 1.0.', 'danger')
            elif not (0.0 <= prob_ai <= 1.0):
                flash('AI Reply Probability must be between 0.0 and 1.0.', 'danger')
            elif prob_archive + prob_ai > 1.0:
                 flash('The sum of Archive and AI probabilities cannot exceed 1.0.', 'danger')
            elif ai_mention_cooldown < 0:
                flash('AI Mention Cooldown must be 0 or greater.', 'danger')
            elif not openrouter_chat_model:
                flash('OpenRouter Chat Model cannot be empty.', 'danger')
            elif not openrouter_mention_model: 
                flash('OpenRouter Mention Model cannot be empty.', 'danger')
            else:
                # Save to .env file
                if not dotenv_path:
                     flash('Error: .env file path not found, cannot save settings.', 'danger')
                else:
                    set_key(dotenv_path, "PROB_ARCHIVE_REPLY", str(prob_archive))
                    set_key(dotenv_path, "PROB_AI_REPLY", str(prob_ai))
                    set_key(dotenv_path, "AI_MENTION_COOLDOWN", str(ai_mention_cooldown))
                    set_key(dotenv_path, "OPENROUTER_CHAT_MODEL", openrouter_chat_model)
                    set_key(dotenv_path, "OPENROUTER_MENTION_MODEL", openrouter_mention_model) # New save
                    set_key(dotenv_path, "MENTION_SYSTEM_PROMPT", mention_system_prompt) # New save
                    set_key(dotenv_path, "ENABLE_VOICE_PROTECTION", "true" if enable_voice_protection else "false")

                    # Log settings change
                    with SessionLocalWeb() as db:
                        log_app_event(
                            db,
                            level="INFO",
                            event_type="settings_changed",
                            message="Settings updated via web UI.",
                            extra={
                                "prob_archive": prob_archive,
                                "prob_ai": prob_ai,
                                "ai_mention_cooldown": ai_mention_cooldown,
                                "openrouter_chat_model": openrouter_chat_model,
                                "openrouter_mention_model": openrouter_mention_model, 
                                "mention_system_prompt": mention_system_prompt, 
                                "enable_voice_protection": enable_voice_protection
                            }
                        )
                    flash('Settings saved successfully. Restarting bot service to apply changes...', 'info')

                    # Attempt to restart the bot service
                    success, message_output = run_systemctl_command('restart')
                    if success:
                        flash('Bot service restarted successfully.', 'success')
                    else:
                        flash(f'Failed to restart bot service automatically: {message_output}', 'danger')
                    # Log restart attempt
                    with SessionLocalWeb() as db:
                         log_app_event(
                            db,
                            level="INFO" if success else "ERROR",
                            event_type="bot_control",
                            message=f"Bot restart attempted after settings change.",
                            extra={"action": "restart", "success": success, "output": message_output}
                        )

                return redirect(url_for('settings')) # Redirect to refresh page

        except ValueError:
            flash('Invalid input. Probabilities must be numbers.', 'danger')
        except Exception as e:
            flash(f'An error occurred saving settings: {e}', 'danger')
        current_values = dotenv_values(dotenv_path) if dotenv_path else {}
        prob_archive_current = current_values.get('PROB_ARCHIVE_REPLY', '0.4')
        prob_ai_current = current_values.get('PROB_AI_REPLY', '0.4')
        mention_system_prompt_current = current_values.get('MENTION_SYSTEM_PROMPT', 'You are a helpful assistant responding to a user mention.')
        return render_template('settings.html',
                               prob_archive=prob_archive_current,
                               prob_ai=prob_ai_current,
                               mention_system_prompt=mention_system_prompt_current)

    # GET request: Default values if no value exist
    current_values = dotenv_values(dotenv_path) if dotenv_path else {}
    prob_archive_current = current_values.get('PROB_ARCHIVE_REPLY', '0.4') 
    prob_ai_current = current_values.get('PROB_AI_REPLY', '0.4')
    ai_mention_cooldown_current = current_values.get('AI_MENTION_COOLDOWN', '60')
    openrouter_chat_model_current = current_values.get('OPENROUTER_CHAT_MODEL', 'microsoft/mai-ds-r1:free')
    openrouter_mention_model_current = current_values.get('OPENROUTER_MENTION_MODEL', 'google/gemini-flash-1.5') # Default is gemini
    mention_system_prompt_current = current_values.get('MENTION_SYSTEM_PROMPT', 'You are a helpful assistant responding to a user mention.')
    enable_voice_protection_current = current_values.get('ENABLE_VOICE_PROTECTION', 'false').lower() == 'true'
    return render_template('settings.html',
                           prob_archive=prob_archive_current,
                           prob_ai=prob_ai_current,
                           ai_mention_cooldown=ai_mention_cooldown_current,
                           openrouter_chat_model=openrouter_chat_model_current,
                           openrouter_mention_model=openrouter_mention_model_current,
                           mention_system_prompt=mention_system_prompt_current,
                           enable_voice_protection=enable_voice_protection_current)


@app.route('/delete_message/<int:message_db_id>', methods=['POST'])
@requires_auth
def delete_message_web(message_db_id):
    db = SessionLocalWeb()
    try:
        message_to_delete = db.query(Message).filter(Message.id == message_db_id).first()
        if message_to_delete:
            # We need the original message_id (discord's ID) to delete attachments correctly
            original_message_id = message_to_delete.message_id

            # Delete associated attachments
            db.query(Attachment).filter(Attachment.message_id == original_message_id).delete()
            # Delete the message itself
            db.delete(message_to_delete)
            log_app_event(db, "INFO", "message_deleted_web", f"Message deleted via web UI.", extra={"message_db_id": message_db_id, "original_message_id": original_message_id})
            db.commit()
            flash(f'Message (ID: {original_message_id}) and its attachments deleted successfully.', 'success')
        else:
            log_app_event(db, "WARNING", "message_delete_failed_web", f"Message delete failed via web UI (not found).", extra={"message_db_id": message_db_id})
            flash('Message not found.', 'error')
    except Exception as e:
        db.rollback()
        log_app_event(db, "ERROR", "message_delete_error_web", f"Error deleting message via web UI: {e}", extra={"message_db_id": message_db_id})
        flash(f'Error deleting message: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('view_messages'))

@app.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@requires_auth
def delete_attachment_web(attachment_id):
    db = SessionLocalWeb()
    try:
        attachment_to_delete = db.query(Attachment).filter(Attachment.id == attachment_id).first()
        if attachment_to_delete:
            att_details = {"attachment_id": attachment_to_delete.attachment_id, "filename": attachment_to_delete.filename, "url": attachment_to_delete.url}
            db.delete(attachment_to_delete)
            log_app_event(db, "INFO", "attachment_deleted_web", f"Attachment deleted via web UI.", extra={"attachment_db_id": attachment_id, **att_details})
            db.commit()
            flash(f'Attachment (ID: {attachment_id}) deleted successfully.', 'success')
        else:
            log_app_event(db, "WARNING", "attachment_delete_failed_web", f"Attachment delete failed via web UI (not found).", extra={"attachment_db_id": attachment_id})
            flash('Attachment not found.', 'error')
    except Exception as e:
        db.rollback()
        log_app_event(db, "ERROR", "attachment_delete_error_web", f"Error deleting attachment via web UI: {e}", extra={"attachment_db_id": attachment_id})
        flash(f'Error deleting attachment: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('view_attachments'))

# --- Application Logs Route ---

@app.route('/applogs')
@requires_auth
def view_app_logs():
    db = SessionLocalWeb()
    try:
        # Filtering
        level = request.args.get('level', '')
        event_type = request.args.get('event_type', '')
        search = request.args.get('search', '')
        page = request.args.get('page', 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page

        query = db.query(AppLog)
        if level:
            query = query.filter(AppLog.level == level)
        if event_type:
            query = query.filter(AppLog.event_type == event_type)
        if search:
            query = query.filter(AppLog.message.ilike(f"%{search}%"))

        total = query.count()
        logs = query.order_by(AppLog.timestamp.desc()).offset(offset).limit(per_page).all()
        total_pages = (total + per_page - 1) // per_page

        all_levels = [row[0] for row in db.query(AppLog.level).distinct()]
        all_event_types = [row[0] for row in db.query(AppLog.event_type).distinct()]

    finally:
        db.close()

    return render_template(
        'applogs.html',
        logs=logs,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        level=level,
        event_type=event_type,
        search=search,
        all_levels=all_levels,
        all_event_types=all_event_types,
        json=json
    )

# --- Run the App ---
if __name__ == '__main__':
    # Make sure tables exist before running the web app
    # You might run `python database.py` first
    print("Ensure database tables are created by running 'python database.py' or 'python archive.py' first.")
    app.run(debug=True, host='0.0.0.0', port=8080) 
