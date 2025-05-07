#Script for bulk archiving messages from an old Discord server
import discord
import os
import asyncio
import datetime
from dotenv import load_dotenv
from database import SessionLocal, add_message, init_db

load_dotenv()

ARCHIVE_BOT_TOKEN = os.getenv('ARCHIVE_BOT_TOKEN', os.getenv('DISCORD_TOKEN')) 
OLD_GUILD_ID = int(os.getenv('OLD_GUILD_ID', 0)) 
# If channles are not specified to archive, archive all readable text channels in guild
CHANNEL_IDS_TO_ARCHIVE = [int(cid.strip()) for cid in os.getenv('CHANNEL_IDS_TO_ARCHIVE', '').split(',') if cid.strip()]

if not ARCHIVE_BOT_TOKEN or not OLD_GUILD_ID:
    print("Error: ARCHIVE_BOT_TOKEN or OLD_GUILD_ID not found in .env file.")
    exit()

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True 

client = discord.Client(intents=intents)
db_session = SessionLocal()

@client.event
async def on_ready():
    print(f'Archive bot logged in as {client.user}')
    print(f'Target Guild ID: {OLD_GUILD_ID}')
    guild = client.get_guild(OLD_GUILD_ID)

    if not guild:
        print(f"Error: Could not find guild with ID {OLD_GUILD_ID}. Make sure the bot is in the server.")
        await client.close()
        db_session.close()
        return

    print(f"Found guild: {guild.name}")

    channels_to_process = []
    if CHANNEL_IDS_TO_ARCHIVE:
        for channel_id in CHANNEL_IDS_TO_ARCHIVE:
            channel = guild.get_channel(channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                channels_to_process.append(channel)
            else:
                print(f"Warning: Could not find text channel with ID {channel_id} or it's not a text channel.")
    else:
        # If no specific channels listed, get all readable text channels
        channels_to_process = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history]

    if not channels_to_process:
        print("Error: No readable text channels found to archive.")
        await client.close()
        db_session.close()
        return

    print(f"Found {len(channels_to_process)} channels to archive:")
    for channel in channels_to_process:
        print(f"- {channel.name} ({channel.id})")

    total_archived = 0
    total_skipped = 0
    start_time = datetime.datetime.now()

    for channel in channels_to_process:
        print(f"\nArchiving channel: #{channel.name} ({channel.id})...")
        channel_archived = 0
        channel_skipped = 0
        try:
            # Using async for loop to iterate through history
            async for message in channel.history(limit=None, oldest_first=True): # Fetch oldest first
                if message.author.bot: # Skipping bot messages
                    continue

                attachments_data = []
                for attachment in message.attachments:
                    attachments_data.append({
                        'attachment_id': attachment.id,
                        'url': attachment.url,
                        'filename': attachment.filename,
                        'content_type': attachment.content_type
                    })

                msg_data = {
                    'message_id': message.id,
                    'guild_id': message.guild.id,
                    'channel_id': message.channel.id,
                    'author_id': message.author.id,
                    'author_name': str(message.author), 
                    'content': message.content,
                    'timestamp': message.created_at.replace(tzinfo=None), 
                    'attachments': attachments_data
                }

                added = add_message(db_session, msg_data)
                if added:
                    channel_archived += 1
                    total_archived += 1
                else:
                    channel_skipped += 1
                    total_skipped += 1

                if (channel_archived + channel_skipped) % 1000 == 0:
                    print(f"  ... processed {channel_archived + channel_skipped} messages in #{channel.name} ({channel_archived} added, {channel_skipped} skipped)")

        except discord.Forbidden:
            print(f"Error: Bot lacks permissions to read history in channel #{channel.name}. Skipping.")
        except Exception as e:
            print(f"Error archiving channel #{channel.name}: {e}")

        print(f"Finished archiving #{channel.name}. Added: {channel_archived}, Skipped: {channel_skipped}")

    end_time = datetime.datetime.now()
    duration = end_time - start_time
    print("\n--- Archiving Complete ---")
    print(f"Total messages added: {total_archived}")
    print(f"Total messages skipped (duplicates): {total_skipped}")
    print(f"Duration: {duration}")

    await client.close()
    db_session.close()


if __name__ == "__main__":
    print("Initializing database for archive script...")
    init_db() # 

    print("Starting archive process...")
    if ARCHIVE_BOT_TOKEN:
        try:
             asyncio.run(client.start(ARCHIVE_BOT_TOKEN))
        except discord.LoginFailure:
            print("Error: Invalid ARCHIVE_BOT_TOKEN. Please check your token on .env file.")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
        finally:
            if not db_session.is_active: 
                 try:
                     db_session.close()
                 except Exception as e:
                     print(f"Error closing DB session: {e}")

    else:
        print("ARCHIVE_BOT_TOKEN not set.")
